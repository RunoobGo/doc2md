#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attribute.py —— 偏差阶段归因器：把「差多少分」拆成「哪一阶段丢的」。

参考资料的反面样本：综合分 0.744、其中 order 只有 0.478——**只有一个综合分时，
你不知道该修 S1 还是 S4**。第十七轮的教训更进一步（G9 / O-g）：真值若与产物同源，
连「丢了什么」都看不见。本模块做两件事：

  1. 把质量综合分的缺口按画像权重**精确**拆到各指标（恒等式，可复算）；
  2. 把每个指标的缺口按可观测证据分到管线阶段（S1 解析 / S1.5 门禁 / S2 分片缝合 /
     S3 结构 / S4 渲染 / LLM），拆不动的进 `unattributed` 并**计数**。

    Σ_k (归一化权重_k × (1 − 分数_k)) == 1 − 质量综合分

这与 CH-500 的「50 = 18 + 6 + 10 + 2 + 5 + 8 + 1」是同一条纪律：组合口径必须可复算，
不许出现「总数对不上但没人知道差在哪」。

**口径声明（双口径纪律）**：本模块的「质量综合分」是**测试侧重算值**，不是
`mdscore.score()["overall"]`——evaluate.py 调 score 时 `ref=None`，那个 overall 只覆盖
一致性侧（权重被重新归一化）。保真侧四项（heading MHS / table TEDS / structure BSF /
order NID）来自 evaluate.py 对 `ideal/` + `srcstruct` 源端直抽的比对。两侧在这里合成
一个向量、用同一套画像权重，**不与 `expected/` 基线口径混用**。

用法：
    .venv/bin/python tests/qa/attribute.py                     # 全语料
    .venv/bin/python tests/qa/attribute.py --only hard_ --json work/qa/attribution.json

退出码：运行结果恒 0（逐样本失败记为 `status="error"`，不中断批次）；
参数/清单错误退 2。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CORPUS = HERE.parent / "corpus"
sys.path.insert(0, str(REPO))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402
from tests.qa import evaluate, mdscore  # noqa: E402
from tests.qa import srcstruct as src_extract  # noqa: E402

STAGES = ("S1_parse", "S1_5_gate", "S2_shard", "S3_structure", "S4_render", "LLM")

#: 拆不动的偏差落在这个伪阶段。它**不在 STAGES 里**，单独计数——
#: 恒等式是 `Σ by_stage[st].loss + unattributed.loss == total_gap`，
#: 两边不重叠，才不会像 CH-500 那样出现「相加对不上总数」。
UNATTRIBUTED = "unattributed"

#: 偏差类型 → 阶段。**单一口径**：要改归因就改这张表，不要在代码里加分支。
KIND_STAGE: dict[str, str] = {
    "heading_missed": "S1_parse",       # 源端有标题、产物没有：抽取层丢失
    "heading_false": "S3_structure",    # 产物有、源端没有：结构判定误升
    "heading_level": "S3_structure",    # 文本对上、层级错：映射口径
    "table_missed": "S1_parse",
    "table_split": "S2_shard",          # 相邻同表头 = 跨片/跨页裂表（B-43）
    "table_shape": "S3_structure",      # 列数不一致（row_shape / TCOLC）
    "table_content": "S3_structure",    # 找到了但单元格内容错
    "structure_blocks": "S3_structure",
    "structure_order": "S2_shard",      # 分片场景下的顺序错乱优先归缝合
    "lint_contract": "S4_render",
    "asset_broken": "S4_render",
    "render_redline": "S4_render",
    "silent_degrade": "S1_parse",       # W1045 在 s1_parse._docx_table_rows 检出
    "idempotent_break": "S2_shard",
    # 以下不占指标预算（没有对应的 scores 键），只计严重度与阶段计数
    "cid_mojibake": "S1_5_gate",
    "furniture_residue": "S1_parse",
    "shard_warning": "S2_shard",
    "llm_ops_rejected": "LLM",
    "gate_verdict": "S1_5_gate",
}

#: 预算内偏差（loss 参与恒等式）；其余为「契约违规」，loss=0。
#: `add()` 的 `budgeted=None` 默认值按本集合解析；两个 unattributed 类型
#: 虽不在 KIND_STAGE 里（直接构造 Dev），也占预算，必须登记在此。
BUDGETED = frozenset({"heading_missed", "heading_false", "heading_level",
                      "table_missed", "table_split", "table_shape", "table_content",
                      "structure_blocks", "structure_order", "lint_contract",
                      "asset_broken", "render_redline", "silent_degrade",
                      "idempotent_break", "heading_residual",
                      "idempotent_unattributed"})

#: 页眉/页码家具。页码页脚必须是**整行**（`第 N 页` / `第 N 页 共 M 页`），
#: 否则 pdf_08 的正文 `第 1 页第 1 段：…` 会被整段误判；
#: `奇数页/偶数页` 保持非锚定——D08 的页眉是 `第 N 章 …　奇数页`。
#: 每行单独 search，`^...$` 已按整行约束，无需 re.M。
_FURNITURE = re.compile(
    r"^[ \t]*[-–—]?[ \t]*\d{1,4}[ \t]*[-–—]?[ \t]*$"
    r"|^[ \t]*第[ \t]*\d+[ \t]*页(?:[ \t]*共[ \t]*\d+[ \t]*页)?[ \t]*$"
    r"|奇数页|偶数页")
_MOJIBAKE = re.compile(r"\(cid:\d+\)|\ufffd")


@dataclass
class Dev:
    stage: str
    kind: str
    metric: str
    loss: float
    severity: str          # blocking | major | minor
    evidence: str
    budgeted: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["loss"] = round(self.loss, 6)
        return d


def _by_stage(devs: list[Dev]) -> dict[str, dict]:
    """按 STAGES 汇总偏差（ok 与非 ok 记录共用同一口径，schema 才同形）。"""
    out: dict[str, dict] = {}
    for st in STAGES:
        ds = [d for d in devs if d.stage == st]
        out[st] = {"count": len(ds),
                   "budgeted": sum(1 for d in ds if d.budgeted),
                   "loss": round(sum(d.loss for d in ds if d.budgeted), 6),
                   "kinds": sorted({d.kind for d in ds}),
                   "severities": sorted({d.severity for d in ds})}
    return out


def split_tables(blocks: list) -> list[tuple[int, int]]:
    """相邻且表头相同的表格块下标对（裂表信号；defect_scan R1 复用本函数）。"""
    idx = [i for i, b in enumerate(blocks) if b.type == "table"]
    out = []
    for a, b in zip(idx, idx[1:]):
        if b != a + 1:
            continue
        ha = [re.sub(r"\s+", "", c) for c in (blocks[a].meta.get("header") or [])]
        hb = [re.sub(r"\s+", "", c) for c in (blocks[b].meta.get("header") or [])]
        if ha and ha == hb:
            out.append((a, b))
    return out


def combined_scores(rec: dict, sc: dict) -> dict[str, float]:
    """保真侧（evaluate 的 rec）+ 一致性侧（mdscore 的 sc）合成一个分数向量。"""
    s: dict[str, float] = {k: float(v) for k, v in (sc.get("scores") or {}).items()
                           if k in mdscore.CONSISTENCY_KEYS}
    h = rec.get("heading") or {}
    if h:
        # MHS 口径：标题 F1 × 层级准确率（mdscore.heading_score 的同一算法）
        s["heading"] = float(h.get("f1", 0.0)) * float(h.get("level_accuracy", 0.0))
    t = rec.get("table") or {}
    if t and t.get("teds") is not None:
        s["table"] = float(t["teds"])
    if (rec.get("structure") or {}).get("bsf") is not None:
        s["structure"] = float(rec["structure"]["bsf"])
    if (rec.get("order") or {}).get("nid") is not None:
        s["order"] = float(rec["order"]["nid"])
    return s


def quality_gap(scores: dict[str, float], profile: str) -> tuple[float, dict[str, float]]:
    """按画像权重把 1 − 综合分**精确**拆到各指标。返回 (total_gap, 逐指标预算)。"""
    weights = mdscore.PROFILES.get(profile, mdscore.PROFILES["generic"])["weights"]
    w = {k: v for k, v in weights.items() if k in scores}
    tot = sum(w.values())
    if not tot:
        return 0.0, {}
    overall = sum(scores[k] * v for k, v in w.items()) / tot
    return 1.0 - overall, {k: (v / tot) * (1.0 - scores[k]) for k, v in w.items()}


def attribute(rec: dict, sc: dict, *, md: str, blocks: list,
              ir=None, res=None) -> dict:
    """纯函数：不做转换、不读盘，因此可毫秒级单测（判定层必须有牙齿）。"""
    profile = sc.get("profile", "generic")
    scores = combined_scores(rec, sc)
    total_gap, budget = quality_gap(scores, profile)
    devs: list[Dev] = []

    def add(kind: str, metric: str, loss: float, evidence: str,
            severity: str = "major", budgeted: bool | None = None) -> None:
        # 预算资格只认 BUDGETED 这一张表；显式 budgeted=False 的契约违规不受影响
        if budgeted is None:
            budgeted = kind in BUDGETED
        devs.append(Dev(stage=KIND_STAGE[kind], kind=kind, metric=metric,
                        loss=max(0.0, loss), severity=severity,
                        evidence=evidence, budgeted=budgeted))

    sharded = bool(getattr(ir, "sharded", False))
    warnings = list(((res.meta or {}).get("warnings", []) if res else []) or [])
    splits = split_tables(blocks)

    # --- heading：按计数拆三段，F1 是调和均值，拆不干净的残差进 unattributed ---
    hb = budget.get("heading", 0.0)
    h = rec.get("heading") or {}
    if hb > 0 and h:
        nref, npred = max(1, int(h.get("ref", 0))), max(1, int(h.get("pred", 0)))
        miss = int(h.get("fn", 0)) / nref
        false = int(h.get("fp", 0)) / npred
        level = (int(h.get("matched_text_only", 0)) - int(h.get("matched", 0))) / nref
        parts = {"heading_missed": miss, "heading_false": false, "heading_level": level}
        ssum = sum(parts.values())
        scale = (hb / ssum) if ssum > 0 else 0.0
        for kind, ratio in parts.items():
            if ratio > 0:
                add(kind, "heading", ratio * scale,
                    f"{h.get('fn', 0)} 漏 / {h.get('fp', 0)} 误 / "
                    f"层级错 {int(h.get('matched_text_only', 0)) - int(h.get('matched', 0))}")
        if ssum <= 0:
            # 计数全为 0 但 F1 < 1（例如两侧皆空却给了缺口）：拆不动，进伪阶段
            devs.append(Dev(UNATTRIBUTED, "heading_residual", "heading", hb,
                            "minor", "标题缺口无法按计数拆分（调和均值残差）",
                            budgeted=True))

    # --- table：漏检 / 裂表 / 形状 / 内容 ---
    tb = budget.get("table", 0.0)
    t = rec.get("table") or {}
    if tb > 0 and t:
        nref = max(1, int(t.get("n_ref", 0)))
        missed = int(t.get("missed", 0)) / nref
        shape = t.get("shape_ok")
        shape_gap = (1.0 - float(shape)) / nref if shape is not None else 0.0
        kind = "table_split" if splits else "table_missed"
        if missed > 0:
            ev = f"漏检 {t.get('missed')} 张"
            if splits:
                ev += f"；检出裂表 {len(splits)} 处（下标 {splits}，相邻同表头）"
            add(kind, "table", missed * tb, ev, "blocking" if splits else "major")
        if shape_gap > 0:
            add("table_shape", "table", shape_gap * tb,
                f"列数一致率 {shape}（源端 {t.get('n_ref')} 张）")
        rest = tb * (1.0 - missed - shape_gap)
        if rest > 1e-9:
            add("table_content", "table", rest,
                f"found-only TEDS {t.get('found_only')}：表找到了但单元格内容/结构有损")

    # --- structure / order：分片场景优先归缝合，否则归结构 ---
    for metric, key in (("structure", "bsf"), ("order", "nid")):
        b = budget.get(metric, 0.0)
        if b <= 0:
            continue
        val = (rec.get(metric) or {}).get(key)
        kind = "structure_order" if (sharded or splits) else "structure_blocks"
        add(kind, metric, b, f"{metric}.{key}={val}"
                             + ("（分片/裂表场景）" if kind == "structure_order" else ""))

    # --- 一致性侧：逐项直归 S4，honest 归 S1（W1045 的检出点）---
    lb = budget.get("lint_contract", 0.0)
    if lb > 0:
        by_rule = ((sc.get("detail") or {}).get("lint") or {}).get("by_class", {})
        add("lint_contract", "lint_contract", lb,
            f"契约类违规 {json.dumps(by_rule.get('contract', {}), ensure_ascii=False)}")
    ab = budget.get("asset", 0.0)
    if ab > 0:
        det = (sc.get("detail") or {}).get("asset") or {}
        add("asset_broken", "asset", ab,
            f"断链 {det.get('total', 0) - det.get('resolved', 0)} / {det.get('total', 0)}")
    rb = budget.get("render", 0.0)
    if rb > 0:
        add("render_redline", "render", rb,
            f"红线命中 {json.dumps((sc.get('detail') or {}).get('render', {}).get('hits', {}), ensure_ascii=False)}",
            "blocking")
    hob = budget.get("honest", 0.0)
    if hob > 0:
        add("silent_degrade", "honest", hob,
            f"产物有降级形态但无 W104x 告警：{(sc.get('detail') or {}).get('honest', {})}",
            "blocking")
    ib = budget.get("idempotent", 0.0)
    if ib > 0:
        if sharded:
            add("idempotent_break", "idempotent", ib,
                f"分片场景 N 次输出不一致（{(sc.get('detail') or {}).get('idempotent', {})}）",
                "blocking")
        else:
            devs.append(Dev(UNATTRIBUTED, "idempotent_unattributed", "idempotent",
                            ib, "blocking", "非分片场景的不一致：阶段不可判定，"
                            "需 --repeats 3 复现后人工定位", budgeted=True))

    # --- 契约违规（不占预算，loss=0）---
    body = mdscore.strip_front_matter(md or "")[1]
    if _MOJIBAKE.search(body):
        add("cid_mojibake", "-", 0.0, f"乱码残留 {_MOJIBAKE.findall(body)[:3]}",
            "blocking", budgeted=False)
    furn = [ln for ln in body.split("\n") if _FURNITURE.search(ln)]
    if furn:
        add("furniture_residue", "-", 0.0, f"页眉/页码残留 {len(furn)} 行：{furn[:3]}",
            "major", budgeted=False)
    for w in warnings:
        code = w.split(":")[0] if isinstance(w, str) else str(w)
        if code in ("W1043", "W1044"):
            add("shard_warning", "-", 0.0, f"告警 {w}", "major", budgeted=False)
    if res is not None:
        rej = list(getattr(res, "rejected_ops", []) or [])
        if rej:
            add("llm_ops_rejected", "-", 0.0, f"LLM 算子被拒 {len(rej)} 条",
                "minor", budgeted=False)

    attributed = sum(d.loss for d in devs if d.budgeted and d.stage in STAGES)
    unattr = [d for d in devs if d.stage == UNATTRIBUTED and d.budgeted]
    unattributed_loss = sum(d.loss for d in unattr)
    unattributed_n = len(unattr)
    by_stage = _by_stage(devs)
    residual = total_gap - attributed - unattributed_loss
    return {
        "profile": profile,
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "budget": {k: round(v, 6) for k, v in budget.items()},
        "total_gap": round(total_gap, 6),
        "deviations": [d.to_dict() for d in devs],
        "by_stage": by_stage,
        "unattributed": {"count": unattributed_n,
                         "loss": round(unattributed_loss, 6)},
        "identity": {"total_gap": round(total_gap, 6),
                     "attributed": round(attributed, 6),
                     "unattributed_loss": round(unattributed_loss, 6),
                     "residual": round(residual, 9),
                     "ok": abs(residual) < 1e-9},
        "signals": {"sharded": sharded, "split_tables": splits,
                    "warnings": warnings},
    }


def _neutral_record(source: Path, profile: str) -> dict:
    """非 ok 记录的统一骨架：键集与 ok 记录一致，数值全部中性化。"""
    return {
        "source": source.name,
        "kind": source.suffix.lstrip(".").lower(),
        "profile": profile,
        "scores": {},
        "budget": {},
        "total_gap": 0.0,
        "tcolc": None,
        "deviations": [],
        "by_stage": _by_stage([]),
        "unattributed": {"count": 0, "loss": 0.0},
        "identity": {"total_gap": 0.0, "attributed": 0.0,
                     "unattributed_loss": 0.0, "residual": 0.0, "ok": True},
        "signals": {},
        "record": {},
    }


def _error_record(source: Path, profile: str, exc: Exception) -> dict:
    out = _neutral_record(source, profile)
    out["status"] = "error"
    out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def attribute_one(source: Path, *, out_dir: Path, profile: str = "generic",
                  repeats: int = 1, sink: dict | None = None) -> dict:
    """单样本：一次真机转换 → 目标侧装配 → 归因。

    `sink` 与 `run_hard.check_spec` 同形：给一个 dict，就把产物 Markdown 写进
    `sink["md"]`（Task 9 的真实样本腿要拿它算人工真值召回，不该去猜产物落盘路径）。

    整个逐样本流程都在错误边界内：只有转换抛 `Doc2mdError` 才是 `rejected`
    （S1.5 门禁裁决），其余任何阶段失败都记 `status="error"` 并返回中性骨架，
    绝不向上抛——否则一份坏样本会中断整批并让 CLI 退非零。
    """
    try:
        try:
            res = convert(source, out_dir=out_dir, force=True)
        except Doc2mdError as exc:
            out = _neutral_record(source, profile)
            out["status"] = "rejected"
            out["error_code"] = exc.code
            dev = Dev(KIND_STAGE["gate_verdict"], "gate_verdict", "-", 0.0,
                      "major", f"reject:{exc.code}", budgeted=False)
            out["deviations"] = [dev.to_dict()]
            out["by_stage"] = _by_stage([dev])
            return out

        md = res.markdown
        if sink is not None:
            sink["md"] = md
        blocks = mdscore.parse_blocks(md)
        gt = src_extract.extract(source)
        ideal = evaluate.load_ideal(source.name)
        # 与 evaluate.evaluate_one 同口径：ideal 存在即权威，headings=[] 是真值空集，
        # 不得回退到源端启发式（pdf_10 的栏头会被误当成漏检标题）。
        ref_headings = (ideal or {}).get("headings", gt.get("headings", [])) \
            if (ideal or gt) else []
        ref_blocks = evaluate.build_ref_blocks(source)
        skel = evaluate.skeleton(blocks)
        rec = {
            "status": "ok",
            "heading": evaluate.heading_f1(blocks, ref_headings),
            "table": evaluate.table_f1(blocks, gt.get("tables", [])),
            "structure": {"bsf": mdscore.structure_f1(skel, ref_blocks)[0]},
            "order": {"nid": mdscore.reading_order_nid(skel, ref_blocks)},
            "counts": {"list_items": evaluate.count_delta(
                evaluate.product_counts(blocks)["list_items"], gt.get("list_items"))},
        }
        mds = [md]
        for _ in range(max(0, repeats - 1)):
            mds.append(convert(source, out_dir=out_dir, force=True).markdown)
        warnings = list((res.meta or {}).get("warnings", []) or [])
        # 产品 metrics["table_col"] 是 {"tables","rows","rate"}，打分只取 rate；
        # 整份 dict 传给 mdscore.score 会在 round() 时 TypeError
        tcolc = ((res.metrics or {}).get("table_col") or {}).get("rate")
        sc = mdscore.score(md, ref=None, ref_kind="none", profile=profile,
                           repeats=mds if len(mds) > 1 else None, warnings=warnings,
                           tcolc=tcolc)
        out = _neutral_record(source, profile)
        out["status"] = "ok"
        out["tcolc"] = tcolc
        out.update(attribute(rec, sc, md=md, blocks=blocks, ir=res.ir, res=res))
        out["record"] = rec
        return out
    except Exception as exc:  # noqa: BLE001 —— 错误边界：逐样本失败不得中断批次
        return _error_record(source, profile, exc)


def _cell(v) -> str:
    """Markdown 表格单元格：转义竖线、折叠换行。"""
    return str(v).replace("|", "\\|").replace("\n", " ")


def report_md(payload: dict) -> str:
    """人类可读报告：每阶段 loss 合计、unattributed、恒等式逐份核对。"""
    s = payload["summary"]
    ok = [r for r in payload["records"] if r.get("status") == "ok"]
    bad = [r for r in ok if not (r.get("identity") or {}).get("ok", False)]
    tcolc_n = sum(1 for r in ok if r.get("tcolc") is not None)
    lines = [
        "# Doc2md 偏差阶段归因报告（自动生成）", "",
        f"- 文档画像：`{payload['profile']}`",
        f"- 生成时间：{payload['generated_at']}",
        f"- 样本：{s['n']} 总计 / {s['n_ok']} ok / "
        f"{s['n_rejected']} rejected / {s['n_error']} error",
        f"- 平均 total_gap：{s['total_gap_mean']:.6f}",
        f"- 恒等式成立：{s['identity_ok']}/{s['n_ok']}",
        f"- TCOLC 有值样本：{tcolc_n}/{s['n_ok']}",
        "",
        "## 阶段归因（跨样本 loss 合计）", "",
        "| 阶段 | loss 合计 |", "|---|---|",
    ]
    for st in STAGES:
        lines.append(f"| {st} | {s['by_stage'][st]:.6f} |")
    lines.append(f"| {UNATTRIBUTED} | {s['unattributed_loss']:.6f} |")
    lines.append("")
    if bad:
        names = "、".join(r["source"] for r in bad)
        lines.append(f"❌ 恒等式不成立：{len(bad)} 份（{names}）")
    else:
        lines.append("恒等式全部成立：Σ by_stage + unattributed == total_gap。")
    lines += ["", "## 偏差类型", "",
              "| 类型 | 阶段 | 次数 | loss 合计 | 严重度 |", "|---|---|---|---|---|"]
    for kind, a in sorted(s["kinds"].items(), key=lambda kv: (-kv[1]["loss"], kv[0])):
        stage = KIND_STAGE.get(kind, UNATTRIBUTED)
        lines.append(f"| {_cell(kind)} | {stage} | {a['n']} | "
                     f"{a['loss']:.6f} | {_cell(a['severity'])} |")
    lines += ["", "## 逐样本", "",
              "| 样本 | 状态 | total_gap | S1 | S1.5 | S2 | S3 | S4 | LLM | "
              "unattr | 恒等式 | tcolc |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in payload["records"]:
        if r.get("status") != "ok":
            detail = r.get("error_code") or r.get("error") or ""
            lines.append(f"| {_cell(r['source'])} | {r.get('status')} "
                         f"({_cell(detail)}) | - | - | - | - | - | - | - | - | - | - |")
            continue
        bs = r["by_stage"]
        ident = r.get("identity") or {}
        id_cell = "ok" if ident.get("ok") else f"❌ 恒等式不成立（残差 {ident.get('residual')}）"
        tc = r.get("tcolc")
        lines.append(
            f"| {_cell(r['source'])} | ok | {r['total_gap']:.6f} | "
            f"{bs['S1_parse']['loss']:.6f} | {bs['S1_5_gate']['loss']:.6f} | "
            f"{bs['S2_shard']['loss']:.6f} | {bs['S3_structure']['loss']:.6f} | "
            f"{bs['S4_render']['loss']:.6f} | {bs['LLM']['loss']:.6f} | "
            f"{r['unattributed']['loss']:.6f} | {id_cell} | "
            f"{'-' if tc is None else f'{tc:.4f}'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Doc2md 偏差阶段归因（信息层）",
        epilog="运行结果恒 0（逐样本失败记为 status=error）；参数/清单错误退 2。")
    ap.add_argument("--only", default="", help="只跑名称含该子串的样本")
    ap.add_argument("--profile", default="generic", choices=sorted(mdscore.PROFILES))
    ap.add_argument("--json", default=None, help="写 JSON 结果路径")
    ap.add_argument("--md", default=None, help="写 Markdown 报告路径")
    args = ap.parse_args()

    # 参数/清单错误是基础设施故障：致命（退 2），不混入逐样本 error 记录
    try:
        manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
        entries = [e for e in manifest if args.only in e["source"]] \
            if args.only else manifest
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("source"), str):
                raise ValueError(f"清单条目缺 source：{e!r}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"清单读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="doc2md-attr-") as td:
        out_dir = Path(td) / "out"
        out_dir.mkdir()
        for e in entries:
            src = CORPUS / "sources" / e["source"]
            try:
                rec = attribute_one(src, out_dir=out_dir, profile=args.profile)
            except Exception as exc:  # noqa: BLE001 —— attribute_one 已兜底，再守一层
                rec = _error_record(src, args.profile, exc)
            records.append(rec)
            gap = rec.get("total_gap") if rec.get("status") == "ok" else None
            print(f"  {rec['source']:34s} {rec['status']:9s} "
                  f"gap={'-' if gap is None else f'{gap:.4f}'}")

    ok = [r for r in records if r.get("status") == "ok"]
    n_ok = len(ok)
    n_rej = sum(1 for r in records if r.get("status") == "rejected")
    n_err = sum(1 for r in records if r.get("status") == "error")
    kinds_agg: dict[str, dict] = {}
    for r in ok:
        for d in r["deviations"]:
            a = kinds_agg.setdefault(d["kind"], {"loss": 0.0, "n": 0,
                                                 "severity": d["severity"]})
            a["loss"] = round(a["loss"] + d["loss"], 6)
            a["n"] += 1
    summary = {
        "n": len(records), "n_ok": n_ok, "n_rejected": n_rej, "n_error": n_err,
        "total_gap_mean": round(sum(r["total_gap"] for r in ok) / max(1, n_ok), 6),
        # 阶段 → 跨样本 loss 合计（报告与 Task 6 共用这一份）
        "by_stage": {st: round(sum(r["by_stage"][st]["loss"] for r in ok), 6)
                     for st in STAGES},
        "unattributed_loss": round(sum(r["unattributed"]["loss"] for r in ok), 6),
        "identity_ok": sum(1 for r in ok if r["identity"]["ok"]),
        # 偏差类型 → {"loss": 合计, "n": 命中次数}（Task 6 的 impact 依据）
        "kinds": kinds_agg,
    }
    payload = {
        "profile": args.profile,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "records": records,
    }

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON  -> {p}")

    print("\n" + "=" * 60)
    print("阶段归因（跨样本 loss 合计）")
    for st in STAGES:
        print(f"  {st:14s} {summary['by_stage'][st]:.6f}")
    print(f"  {UNATTRIBUTED:14s} {summary['unattributed_loss']:.6f}")
    print("-" * 60)
    print(f"  样本 {n_ok} ok / {n_rej} rejected / {n_err} error（共 {len(records)}）")
    print(f"  平均 total_gap {summary['total_gap_mean']:.6f}｜"
          f"恒等式 {summary['identity_ok']}/{n_ok} 成立")
    bad = [r["source"] for r in ok if not r["identity"]["ok"]]
    if bad:
        print(f"  ❌ 恒等式不成立：{len(bad)} 份（{', '.join(bad)}）")

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report_md(payload), encoding="utf-8")
        print(f"Markdown -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
