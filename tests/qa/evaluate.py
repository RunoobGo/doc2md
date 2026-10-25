#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evaluate.py —— Doc2md 实机转换效果评测执行器。

一次运行完成「目标 ↔ 实际」的自动对账与量化打分：

  1. 跑真机转换（`pipeline.convert.convert`），取实际产物 `content.md`；
  2. 取**目标侧**真值：
       - 标题：优先 `tests/corpus/ideal/*.ideal.json`（人工/程序化标注，权威）；
       - 表格 / 列表 / 代码 / 链接：`srcstruct.extract()` 从源文件直抽，不经管线；
  3. 解析产物 Markdown 的块结构（`mdscore.parse_blocks`）；
  4. 逐指标比对 —— 标题 MHS、表格 TEDS、结构 BSF、文本 NED、阅读顺序 NID、
     规范 LINT、资产 ASSET、幂等 IDEM、渲染红线 RENDER、降级诚实度 HONEST；
  5. 输出机器可读 JSON + 人类可读 Markdown 报告，并按文档画像跑门禁。

**双口径纪律（不可违反）**：本脚本对 `expected/` 只做**逐字节回归**（证明稳定），
对 `ideal/` + 源端直抽才做**质量打分**（证明对）。两套数分列，不聚合。

用法：
    .venv/bin/python tests/qa/evaluate.py                    # 全语料
    .venv/bin/python tests/qa/evaluate.py --only pdf_ --repeats 3
    .venv/bin/python tests/qa/evaluate.py --profile financial
    .venv/bin/python tests/qa/evaluate.py --json work/qa/eval.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "corpus"
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402
from tests.qa import mdscore  # noqa: E402
from tests.qa import srcstruct as src_extract  # noqa: E402

# ============================================================================
# 目标侧装配
# ============================================================================

def load_ideal(source_name: str) -> dict | None:
    """读 ideal/<stem>.ideal.json（权威标题真值）。不存在返回 None。"""
    p = CORPUS / "ideal" / f"{Path(source_name).stem}.ideal.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def build_ref_blocks(source: Path) -> list[mdscore.Block]:
    """由源端真值组装「理想骨架块序列」，供 BSF / NID 比对。

    **顺序必须来自源端**：先出全部标题再出全部表格的「分类骨架」会把
    「参照系构造错误」误报成「管线顺序错乱」（docx_11 的 NID 曾因此只有 0.27，
    而真实顺序是逐节交错的）。这里改用 `srcstruct.ordered_skeleton()`——
    DOCX 走 OOXML body 元素序（真值级），PDF 走 (page, top) 几何序（启发级）。

    这是**结构清单比对**（参考资料 §四）：不比文字，比骨架。
    标题仍优先取 `ideal/` 标注（人工/程序化权威真值），表格取源端直抽。
    """
    sk = src_extract.ordered_skeleton(source) if hasattr(src_extract, "ordered_skeleton") else None
    items = (sk or {}).get("items")
    if items is None:
        # 回退：源端顺序不可用时的分类骨架（顺序可信度降级）
        gt = src_extract.extract(source)
        items = [{"type": "heading", "level": h["level"], "text": h["text"]}
                 for h in gt.get("headings", [])]
        items += [{"type": "table", "header": t["header"], "body": t["body"],
                   "ncol": t["ncol"]} for t in gt.get("tables", [])]

    ideal = load_ideal(source.name)
    ideal_map = {}
    ideal_authoritative = bool(ideal and ideal.get("kind") == "headings")
    if ideal_authoritative:
        for h in ideal.get("headings", []):
            ideal_map[re.sub(r"\s+", "", h.get("text", ""))] = int(h.get("level", 1))

    blocks: list[mdscore.Block] = []
    for it in items:
        if it["type"] == "heading":
            key = re.sub(r"\s+", "", it.get("text", ""))
            if ideal_authoritative and key not in ideal_map:
                # ideal/ 存在即权威——它已把 pdf_10 的「第 N 页第 M 栏」栏头
                # 定性为页眉家具（真值空集），源端字号启发式不该再把它捞回来。
                continue
            lvl = ideal_map.get(key, int(it.get("level", 1)))
            blocks.append(mdscore.Block("heading", level=lvl, text=it.get("text", "")))
        else:
            rows = [it.get("header", [])] + list(it.get("body", []))
            blocks.append(mdscore.Block(
                "table", text="\n".join("| " + " | ".join(r) + " |" for r in rows),
                meta={"header": it.get("header", []), "aligns": [],
                      "body": list(it.get("body", [])), "ncol": it.get("ncol", 0)}))
    return blocks


# ============================================================================
# 结构清单比对（元素级 P/R/F1）
# ============================================================================

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def heading_f1(pred: list[mdscore.Block], ref: list[dict]) -> dict:
    """标题 F1：对齐文本（归一化空白）+ 层级。

    边界：两侧皆空 = 完美（1.0），与 `qa_eval.char_f1` 同口径——pdf_10 这类
    「真值为空集」的样本，S3 零误判即满分，不能因为「无真值」判 0。
    """
    ph = [(_norm(b.text), b.level) for b in pred if b.type == "heading"]
    rh = [(_norm(h.get("text", "")), int(h.get("level", 0))) for h in ref]

    from collections import Counter
    pc, rc = Counter(ph), Counter(rh)
    matched = sum((pc & rc).values())
    # 只比文本（不含层级）的匹配数，用于诊断「文本对但层级错」
    pt = Counter(t for t, _ in ph)
    rt = Counter(t for t, _ in rh)
    matched_text = sum((pt & rt).values())

    tp, tr = len(ph), len(rh)
    if tp == 0 and tr == 0:
        return {"p": 1.0, "r": 1.0, "f1": 1.0, "pred": 0, "ref": 0,
                "matched": 0, "matched_text_only": 0, "level_accuracy": 1.0,
                "fp": 0, "fn": 0}
    p = matched / tp if tp else 0.0
    r = matched / tr if tr else 0.0
    f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
    lvl_acc = (matched / matched_text) if matched_text else 0.0
    return {"p": round(p, 4), "r": round(r, 4), "f1": round(f1, 4),
            "pred": tp, "ref": tr, "matched": matched,
            "matched_text_only": matched_text,
            "level_accuracy": round(lvl_acc, 4),
            "fp": tp - matched_text, "fn": tr - matched_text}


def skeleton(blocks: list[mdscore.Block]) -> list[mdscore.Block]:
    """抽出「骨架」——只保留标题与表格块。

    BSF / NID 必须在骨架上算：源端真值给不出正文段落清单（DOCX 能给但
    PDF 给不出），拿「完整产物块序列」去比「只有标题+表格的参照」会把
    「段落多」误算成「结构差」。骨架比对回答的才是真问题——
    **标题与表格的数量、顺序、层级对不对**。
    """
    return [b for b in blocks if b.type in ("heading", "table")]


def table_f1(pred: list[mdscore.Block], ref: list[dict]) -> dict:
    """表格：漏检按 0 分计入，同时报 found-only TEDS（区分「没找到」与「找到但错」）。"""
    pt = [b for b in pred if b.type == "table"]
    if not pt and not ref:
        return {"teds": 1.0, "found_only": None, "n_pred": 0, "n_ref": 0,
                "missed": 0, "extra": 0, "shape_ok": None}
    scores = []
    for i, rt in enumerate(ref):
        if i < len(pt):
            scores.append(mdscore.teds(pt[i].meta, {
                "header": rt.get("header", []),
                "body": list(rt.get("body", [])),
                "ncol": rt.get("ncol", 0)}))
        else:
            scores.append(0.0)
    teds_all = sum(scores) / len(ref) if ref else 0.0
    found_only = round(sum(scores[:len(pt)]) / len(scores[:len(pt)]), 4) if pt else None

    # 形状对账：列数是否一致（PDF 侧 row_explicit=False 的常见失分点）
    shape_ok = None
    if pt and ref:
        ok = sum(1 for i, rt in enumerate(ref)
                 if i < len(pt) and pt[i].meta.get("ncol") == rt.get("ncol"))
        shape_ok = round(ok / len(ref), 4)
    return {"teds": round(teds_all, 4), "found_only": found_only,
            "n_pred": len(pt), "n_ref": len(ref),
            "missed": max(0, len(ref) - len(pt)),
            "extra": max(0, len(pt) - len(ref)),
            "shape_ok": shape_ok}


def count_delta(pred: int | None, ref: int | None) -> dict | None:
    """标量计数比对（列表项 / 代码块 / 链接）。任一侧不可判定即返回 None。"""
    if pred is None or ref is None:
        return None
    if ref == 0 and pred == 0:
        return {"pred": 0, "ref": 0, "score": 1.0}
    if ref == 0:
        return {"pred": pred, "ref": 0, "score": 0.0}
    return {"pred": pred, "ref": ref,
            "score": round(min(pred, ref) / max(pred, ref), 4)}


def product_counts(blocks: list[mdscore.Block]) -> dict:
    """从产物块序列统计列表项 / 代码块 / 链接数。"""
    list_items = 0
    for b in blocks:
        if b.type == "list":
            list_items += len(re.findall(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+",
                                         b.text, re.M))
    code_blocks = sum(1 for b in blocks if b.type == "code")
    return {"list_items": list_items, "code_blocks": code_blocks}


# ============================================================================
# 单样本评测
# ============================================================================

def evaluate_one(source: Path, *, out_dir: Path, profile: str = "generic",
                 repeats: int = 1, expected_md: Path | None = None) -> dict:
    """评测单个样本，返回结构化结果。"""
    rec: dict = {"source": source.name, "kind": source.suffix.lstrip(".").lower()}
    t0 = time.time()

    # --- 真机转换（repeats 次用于幂等率）---
    mds: list[str] = []
    warnings: list[str] = []
    tcolc = None
    try:
        for _ in range(max(1, repeats)):
            res = convert(source, out_dir=out_dir, force=True)
            mds.append(res.markdown)
            warnings = list((res.meta or {}).get("warnings", []) or [])
            # 产品侧键名是 table_col（convert._metrics），不是函数名 table_col_consistency；
            # 值是 {"tables","rows","rate"}，打分只取 rate
            tcolc = ((res.metrics or {}).get("table_col") or {}).get("rate")
        rec["elapsed_s"] = round(time.time() - t0, 3)
    except Doc2mdError as exc:
        rec["status"] = "rejected"
        rec["error_code"] = exc.code
        rec["elapsed_s"] = round(time.time() - t0, 3)
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["elapsed_s"] = round(time.time() - t0, 3)
        return rec

    md = mds[0]
    rec["status"] = "ok"
    blocks = mdscore.parse_blocks(md)

    # --- 目标侧 ---
    gt = src_extract.extract(source)
    ideal = load_ideal(source.name)
    ref_headings = (ideal or {}).get("headings", gt.get("headings", [])) \
        if (ideal or gt) else []
    ref_blocks = build_ref_blocks(source)

    # --- 保真度（对源端/ideal 真值）---
    rec["heading"] = heading_f1(blocks, ref_headings)
    rec["table"] = table_f1(blocks, gt.get("tables", []))
    # 骨架比对（只含标题+表格，见 skeleton() 的理由）
    bsf, d = mdscore.structure_f1(skeleton(blocks), ref_blocks)
    rec["structure"] = {"bsf": round(bsf, 4), **d}
    rec["order"] = {"nid": round(
        mdscore.reading_order_nid(skeleton(blocks), ref_blocks), 4)}
    if expected_md and expected_md.is_file():
        exp = expected_md.read_text(encoding="utf-8")
        rec["text_vs_expected"] = {
            "ned": round(mdscore.ned(mdscore.normalize_text(md),
                                     mdscore.normalize_text(exp)), 4)}
        rec["byte_identical"] = mdscore.strip_front_matter(md)[1].strip() == exp.strip()

    # --- 结构清单计数比对 ---
    pc = product_counts(blocks)
    rec["counts"] = {
        "list_items": count_delta(pc["list_items"], gt.get("list_items")),
        "code_blocks": count_delta(pc["code_blocks"], gt.get("code_blocks")),
    }

    # --- 一致性（无真值）---
    sc = mdscore.score(md, ref=None, ref_kind="none", profile=profile,
                       repeats=mds if len(mds) > 1 else None,
                       warnings=warnings, tcolc=tcolc)
    rec["consistency"] = sc["scores"]
    rec["consistency_detail"] = sc["detail"]
    rec["gate"] = sc["gate"]
    rec["n_runs"] = len(mds)
    return rec


# ============================================================================
# 汇总
# ============================================================================

def _agg(records: list[dict], path: str) -> float | None:
    vals = []
    for r in records:
        cur = r
        ok = True
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and isinstance(cur, (int, float)):
            vals.append(float(cur))
    return round(sum(vals) / len(vals), 4) if vals else None


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("status") == "ok"]
    return {
        "n_total": len(records),
        "n_ok": len(ok),
        "n_rejected": sum(1 for r in records if r.get("status") == "rejected"),
        "n_error": sum(1 for r in records if r.get("status") == "error"),
        "macro": {
            "heading_f1": _agg(ok, "heading.f1"),
            "heading_level_acc": _agg(ok, "heading.level_accuracy"),
            "table_teds": _agg(ok, "table.teds"),
            "table_found_only_teds": _agg(ok, "table.found_only"),
            "table_shape_ok": _agg(ok, "table.shape_ok"),
            "structure_bsf": _agg(ok, "structure.bsf"),
            "order_nid": _agg(ok, "order.nid"),
            "lint_contract": _agg(ok, "consistency.lint_contract"),
            "lint_dialect": _agg(ok, "consistency.lint"),
            "asset": _agg(ok, "consistency.asset"),
            "idempotent": _agg(ok, "consistency.idempotent"),
            "render": _agg(ok, "consistency.render"),
            "honest": _agg(ok, "consistency.honest"),
        },
        "totals": {
            "heading_fp": sum(r.get("heading", {}).get("fp", 0) for r in ok),
            "heading_fn": sum(r.get("heading", {}).get("fn", 0) for r in ok),
            "table_missed": sum(r.get("table", {}).get("missed", 0) for r in ok),
            "table_extra": sum(r.get("table", {}).get("extra", 0) for r in ok),
            "elapsed_s": round(sum(r.get("elapsed_s", 0) for r in records), 2),
        },
        "gate_failures": [{"source": r["source"], "failed": r["gate"]["failed"]}
                          for r in ok if not r.get("gate", {}).get("passed", True)],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Doc2md 转换效果实机评测")
    ap.add_argument("--only", default="", help="只跑名称含该子串的样本")
    ap.add_argument("--repeats", type=int, default=1, help="每样本转换次数（>1 算幂等率）")
    ap.add_argument("--profile", default="generic", choices=sorted(mdscore.PROFILES))
    ap.add_argument("--json", default=None, help="写 JSON 结果路径")
    ap.add_argument("--md", default=None, help="写 Markdown 报告路径")
    args = ap.parse_args()

    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    entries = [e for e in manifest if args.only in e["source"]] if args.only else manifest

    records = []
    with tempfile.TemporaryDirectory(prefix="doc2md-qa-") as td:
        out = Path(td) / "out"
        out.mkdir()
        for e in entries:
            src = CORPUS / "sources" / e["source"]
            exp = CORPUS / e["expected_markdown"] if e.get("expected_markdown") else None
            rec = evaluate_one(src, out_dir=out, profile=args.profile,
                               repeats=args.repeats,
                               expected_md=(CORPUS / exp) if exp else None)
            # 期望行为对账（拒收类样本）
            want = e.get("expected_behavior", "ok")
            if want.startswith("reject:"):
                rec["behavior_ok"] = (rec.get("status") == "rejected"
                                      and rec.get("error_code") == want.split(":", 1)[1])
            else:
                rec["behavior_ok"] = (rec.get("status") == "ok")
            records.append(rec)
            tag = rec.get("status")
            h = rec.get("heading", {}).get("f1")
            t = rec.get("table", {}).get("teds")
            print(f"  {rec['source']:34s} {tag:9s} "
                  f"H={'-' if h is None else f'{h:.3f}'} "
                  f"T={'-' if t is None else f'{t:.3f}'}")

    summary = summarize(records)
    payload = {"summary": summary, "records": records,
               "meta": {"profile": args.profile, "repeats": args.repeats,
                        "pipeline_version": __import__("doc2md").PIPELINE_VERSION}}

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON  -> {p}")

    print("\n" + "=" * 60)
    print("宏观指标（macro avg）")
    for k, v in summary["macro"].items():
        print(f"  {k:24s} {'-' if v is None else f'{v:.4f}'}")
    print("-" * 60)
    print(f"  样本 {summary['n_ok']} ok / {summary['n_rejected']} rejected / "
          f"{summary['n_error']} error，耗时 {summary['totals']['elapsed_s']}s")
    print(f"  标题 FP={summary['totals']['heading_fp']} FN={summary['totals']['heading_fn']}｜"
          f"表格 missed={summary['totals']['table_missed']} extra={summary['totals']['table_extra']}")
    if summary["gate_failures"]:
        print("  门禁未过：")
        for g in summary["gate_failures"]:
            print(f"    - {g['source']}: {', '.join(g['failed'])}")

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_report_md(payload), encoding="utf-8")
        print(f"Markdown -> {p}")
    return 0


def _report_md(payload: dict) -> str:
    s = payload["summary"]
    m = s["macro"]
    lines = ["# Doc2md 转换效果评测报告（自动生成）", "",
             f"- 管线版本：`{payload['meta']['pipeline_version']}`",
             f"- 文档画像：`{payload['meta']['profile']}`",
             f"- 样本：{s['n_ok']} ok / {s['n_rejected']} rejected / {s['n_error']} error",
             "", "## 宏观指标", "",
             "| 指标 | 值 |", "|---|---|"]
    labels = {
        "heading_f1": "标题 F1（MHS）", "heading_level_acc": "标题层级准确率",
        "table_teds": "表格 TEDS（含漏检）", "table_found_only_teds": "表格 TEDS（仅命中）",
        "table_shape_ok": "表格列数一致率", "structure_bsf": "结构 BSF",
        "order_nid": "阅读顺序 NID", "lint_contract": "规范合规 LINT（契约类）",
        "lint_dialect": "规范合规 LINT（含方言类）",
        "asset": "资产完整 ASSET", "idempotent": "幂等 IDEM",
        "render": "渲染红线 RENDER", "honest": "降级诚实 HONEST",
    }
    for k, v in m.items():
        lines.append(f"| {labels.get(k, k)} | {'-' if v is None else f'{v:.4f}'} |")
    lines += ["", "## 逐样本", "",
              "| 样本 | 状态 | 标题F1 | 层级 | 表格TEDS | 形状 | BSF | NID | LINT | IDEM |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in payload["records"]:
        if r.get("status") != "ok":
            lines.append(f"| {r['source']} | {r.get('status')} "
                         f"({r.get('error_code') or r.get('error','')}) | - | - | - | - | - | - | - | - |")
            continue
        h, t = r.get("heading", {}), r.get("table", {})
        c = r.get("consistency", {})
        lines.append(
            f"| {r['source']} | ok | {h.get('f1','-'):.3f} | {h.get('level_accuracy','-'):.3f} | "
            f"{t.get('teds','-'):.3f} | {t.get('shape_ok','-')} | "
            f"{r.get('structure',{}).get('bsf','-'):.3f} | {r.get('order',{}).get('nid','-'):.3f} | "
            f"{c.get('lint_contract','-'):.3f} | {c.get('idempotent','-'):.3f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
