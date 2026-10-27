#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prioritize.py —— 优先级引擎：把三层证据排成一张可复算的施工单。

两条口径必须同时成立，它们互相拉扯，所以写死在这里：

  * **分档只看影响面与重复率**（`W = impact 0.6 + frequency 0.4`），外加一条无条件
    覆盖：blocking 项恒 P0。第十七轮的纪律是「分档不看修复成本」——B-43 之所以是
    「失败」而不是「缺口」，与它有多难修无关。
  * **可修复性只影响同档内的排期序与处置路径**（本机实施 / 重烤黄金 / 人工裁定 /
    外部环境），永不把一个高影响项推到 P2。

第三个恒等式（前两个见 dev-plan §12.10 的 CH-500 与 attribute.py）：

    条目数 == 分档合计 == 来源合计 == 唯一 (来源, 键) 数

需人工或外部环境的项会自动拟一个 `B-<n>` **草稿号**（从 dev-plan §12.7 现有最大号 +1
起顺次分配）。草稿号只是提案，落盘由人工在 Task 10 确认——号段本身也从文档派生，
不写死。

**B 号锚点只有一处：dev-plan §12.7。** `b_rows()` 先定位 `### 12.7 …`、到下一个
`###` / `##` 标题为止，只在该段内解析 `| B-nn | … |` 行；§9.2 另有一条独立的
`B-1…B-3` 待办序列，全文扫描会把它静默并进同一张表（号段与阻断集合都会被污染）。
标题一旦丢失，`b_rows()` 返回空表，`test_priority_calibers_are_complete_and_derived`
随即转红——**锚点失效是硬失败，不回退到全文扫描**。

`--top N`（默认 0 = 全部）只截断 **Markdown 与控制台**里每个分档的打印行数；
JSON 恒含全部条目——否则第三个恒等式就成了谎。

用法（四个输入都是可选的，缺哪个就在报告里写「未提供」，不猜）：
    .venv/bin/python tests/qa/prioritize.py \
        --attribution work/qa/attribution.json --defects work/qa/defects.json \
        --trace work/qa/trace.json --eval work/qa/eval.json \
        --json work/qa/priorities.json --md work/qa/priorities.md

退出码：缺席的输入层记「未提供」并继续，运行结果恒 0；**在场但不可解析 / 结构非法**
的输入文件与 §12.7 锚点失效属基础设施错误，退 2（与 defect_scan.py 的清单错误同边界）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEV_PLAN = REPO / "docs" / "dev-plan.md"
sys.path.insert(0, str(REPO))

from tests.qa import attribute, defect_scan  # noqa: E402

W = {"impact": 0.6, "frequency": 0.4}
P0_FLOOR = 0.60
P1_FLOOR = 0.35

SEVERITY_IMPACT = {"blocking": 1.0, "major": 0.6, "minor": 0.25}
_SEV_ORDER = ("minor", "major", "blocking")
_SEV_RANK = {s: i for i, s in enumerate(_SEV_ORDER)}

FIX_LOCAL = 1.0      # 本机可自证：改判定 / 补测试 / 改报告口径
FIX_REBAKE = 0.7     # 需重烤 22 篇语料黄金 + 逐篇人工复核（动产物字节）
FIX_HUMAN = 0.4      # 需人工裁定口径，或真值只能人工逐页对照
FIX_EXTERNAL = 0.1   # 需其它主机 / 外部服务 / 外部语料

DISPOSITION = {FIX_LOCAL: "fix_local", FIX_REBAKE: "rebake",
               FIX_HUMAN: "human", FIX_EXTERNAL: "external"}

#: 第五种处置（R19-QA-3）：命中来自 `defect_scan.CORPUS_KNOWN_BENIGN` 登记的语料预期
#: 形态——扫描器正确命中，但该形态不是缺陷。它**不进** DISPOSITION：那张表的键是可修复性
#: 分值，而「已知非缺陷」与可修复性无关（该项的 fixability 仍取规则登记值）。
DISP_KNOWN_BENIGN = "known_benign"

#: 偏差类型（attribute.KIND_STAGE 的键）∪ 缺陷规则（defect_scan.RULES 的键）→ 可修复性。
#: **单一口径**：改排期就改这张表；表缺口径会 KeyError（`test_priority_calibers_...`
#: 先红）。末尾两条是 attribute.UNATTRIBUTED 的伪类型（`heading_residual` /
#: `idempotent_unattributed` 由 attribute.py 直接构造 Dev，不在 KIND_STAGE 里）——
#: 它们拆不动、只能人工定位，故与 human 同档；不登记会在真实语料上直接 KeyError。
FIXABILITY: dict[str, float] = {
    "heading_missed": FIX_REBAKE, "heading_false": FIX_REBAKE,
    "heading_level": FIX_HUMAN,        # B-45：CJK 序号层级口径需人工裁定
    "table_missed": FIX_REBAKE,
    "table_split": FIX_HUMAN,          # B-43：正确列归属要人工逐页对照真实 PDF
    "table_shape": FIX_REBAKE, "table_content": FIX_REBAKE,
    "structure_blocks": FIX_REBAKE, "structure_order": FIX_REBAKE,
    "lint_contract": FIX_LOCAL, "asset_broken": FIX_LOCAL,
    "render_redline": FIX_LOCAL,
    "silent_degrade": FIX_REBAKE,      # 归因侧与规则侧同题，处置一致
    "idempotent_break": FIX_REBAKE,
    "cid_mojibake": FIX_HUMAN,         # B-47：拒收/降级契约待裁定
    "furniture_residue": FIX_REBAKE,
    "shard_warning": FIX_LOCAL,
    "llm_ops_rejected": FIX_EXTERNAL,  # 需起 LLM 服务（B-42 同面）
    "gate_verdict": FIX_HUMAN,
    "split_table": FIX_HUMAN, "leader_dots_in_cell": FIX_HUMAN,
    "cjk_space_collapse": FIX_REBAKE, "mojibake_residue": FIX_HUMAN,
    "fence_no_lang": FIX_LOCAL, "col_count_drift": FIX_REBAKE,
    "duplicate_anchor": FIX_LOCAL,
    "heading_residual": FIX_HUMAN, "idempotent_unattributed": FIX_HUMAN,
}

#: 同题已登记的 B 号。**关联不等于归因**：只表示「这个问题在 §12.7 已有登记行」，
#: 用于取「发布阻断」标记与避免重复拟号；分档不读这张表（阻断标记读文档派生的集合）。
LINK_B: dict[str, str] = {
    "table_split": "B-43", "split_table": "B-43",
    "leader_dots_in_cell": "B-44", "cjk_space_collapse": "B-44",
    "heading_level": "B-45",
    "cid_mojibake": "B-47", "mojibake_residue": "B-47",
    "llm_ops_rejected": "B-42",
}

#: 规则 → 同题偏差类型（阶段口径必须一致，测试会钉）
RULE_KIND = {"split_table": "table_split", "col_count_drift": "table_shape",
             "mojibake_residue": "cid_mojibake",
             "furniture_residue": "furniture_residue",
             "silent_degrade": "silent_degrade"}
#: 规则 → 阶段。同题的从 KIND_STAGE **派生**，其余四条按证据来源直接定。
RULE_STAGE = {
    **{r: attribute.KIND_STAGE[k] for r, k in RULE_KIND.items()},
    "leader_dots_in_cell": "S3_structure",   # 目录形态误判为表 = 结构判定
    "cjk_space_collapse": "S1_parse",        # run 间不补空格 = 抽取层
    "fence_no_lang": "S4_render",
    "duplicate_anchor": "S4_render",
}

TRACE_IMPACT = {"FR_P0": 1.0, "FR_other": 0.6, "NFR": 0.5, "E": 0.6, "W": 0.3}
TRACE_TITLE = {"FR_P0": "P0 功能需求无测试引用", "FR_other": "功能需求无测试引用",
               "NFR": "非功能需求无测试引用", "E": "错误码无测试引用",
               "W": "告警码无测试引用"}

#: 单个 §12.7 行：`| B-43 | … |`。
B_ROW = re.compile(r"^\|\s*B-(\d+)\s*\|(.*)$", re.M)
#: §12.7 的标题锚点（`### 12.7 未验证项（诚实标注）`）。`(?![\d.])` 防 `12.70` 误配。
_SEC_127 = re.compile(r"^###[ \t]*12\.7(?![\d.])[^\n]*$", re.M)
#: 段终止：下一个二级 / 三级标题（`### 12.8 …` 或更外层的 `## 13 …`）。
_NEXT_HEADING = re.compile(r"^#{2,3}[ \t]", re.M)

#: 四个可选输入层的展示名（报告里「未提供」用的就是这套标签）。
LAYER_LABEL = {
    "attribution": "归因层（--attribution）",
    "defects": "缺陷层（--defects）",
    "trace": "可追溯层（--trace）",
    "eval": "全量对账层（--eval）",
}


def b_rows(dev_plan: str) -> dict[str, str]:
    """dev-plan §12.7 的 `| B-nn | … |` → {B 号: 行内正文}。**只扫 §12.7**。

    标题缺失时返回 `{}`（而不是回退到全文扫描）：§9.2 另有一条独立的 `B-1…B-3`
    待办序列，全文扫描会把两段静默并进同一张表，号段与阻断集合随之失真。空表会让
    门禁直接报「锚点失效」——红比错好。
    """
    text = dev_plan or ""
    head = _SEC_127.search(text)
    if head is None:
        return {}
    tail = _NEXT_HEADING.search(text, head.end())
    section = text[head.end():tail.start() if tail else len(text)]
    return {f"B-{int(n)}": rest.strip() for n, rest in B_ROW.findall(section)}


def release_blocking(dev_plan: str) -> set[str]:
    """「发布阻断」集合**从文档派生**。

    人在 §12.7 给某行标了「发布阻断」，它就是阻断项；标记被摘掉，本函数随之缩小，
    `test_priority_calibers_are_complete_and_derived` 会红。把决策钉在文档上，
    而不是钉在代码里——否则代码会先于文档过期。
    """
    return {b for b, row in b_rows(dev_plan).items() if "发布阻断" in row}


def next_free_b(dev_plan: str) -> int:
    """§12.7 现有最大 B 号 + 1（同样只认该段）；段缺失时**硬失败**，不回退。

    §9.2 另有一条独立的 `B-1…B-3` 待办序列：锚点缺失时若静默返回 1，拟出的草稿号
    会直接与 §9.2 的 B-1 撞号。CLI 在调用前已按空表退 2，这里再兜一层，防止被
    其它调用方吞掉。
    """
    nums = [int(b.split("-", 1)[1]) for b in b_rows(dev_plan)]
    if not nums:
        raise ValueError(
            "§12.7 锚点缺失：解析不到任何 | B-nn | 行，无法派生新号段；"
            "回退到 B-1 会与 §9.2 自己的 B-1…B-3 待办序列撞号")
    return max(nums) + 1


def _worse(a: str, b: str) -> str:
    """两严重度取重者；未知值按 minor 计（上游 schema 违约不静默抬高影响面）。"""
    return a if _SEV_RANK.get(a, 0) >= _SEV_RANK.get(b, 0) else b


def _join_capped(ids: list[str], cap: int = 12) -> str:
    """证据列表超过 cap 条时显式标出总数：截断不标注会让「12 条」被读成全集。"""
    head = ", ".join(ids[:cap])
    return head + (f" …（共 {len(ids)} 条，此处列前 {cap}）" if len(ids) > cap else "")


@dataclass
class Item:
    origin: str          # attribution | defect | trace | eval
    key: str             # 偏差类型 / 规则名 / trace 分组 / eval 分组
    title: str
    stage: str           # 管线阶段，非管线项为 "-"
    impact: float
    frequency: float
    fixability: float
    score: float
    priority: str        # P0 | P1 | P2
    blocking: bool
    basis: str           # loss | severity | structural（impact 的依据，报告里显式打出）
    loss: float
    n_sources: int
    evidence: str
    b_linked: str = ""
    b_draft: str = ""
    disposition: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def score_item(impact: float, frequency: float) -> float:
    return round(W["impact"] * impact + W["frequency"] * frequency, 6)


def bucket(score: float, blocking: bool) -> str:
    """分档。blocking 无条件 P0；否则按分数落档。**fixability 不参与。**"""
    if blocking:
        return "P0"
    if score >= P0_FLOOR:
        return "P0"
    return "P1" if score >= P1_FLOOR else "P2"


def make_item(origin: str, key: str, title: str, stage: str, impact: float,
              frequency: float, fixability: float, basis: str, blocking: bool,
              loss: float, n_sources: int, evidence: str, b_linked: str,
              known_benign: bool = False, **extra) -> Item:
    # 先夹取、再算分、再落库：Item 存的是夹取后的 impact / frequency，分数必须用
    # 同一组值，否则「分数 = 0.6·影响 + 0.4·频率」无法从打印字段复算
    # （make_item(..., impact=1.0, frequency=2.0) 会存 frequency=1.0 却给出 1.4）。
    imp = round(min(1.0, max(0.0, impact)), 4)
    freq = round(min(1.0, max(0.0, frequency)), 4)
    score = score_item(imp, freq)
    ev = evidence or ""
    if len(ev) > 200:
        ev = ev[:200] + "…"   # 截断必须留痕：原串更长时末尾带省略号
    return Item(origin=origin, key=key, title=title, stage=stage,
                impact=imp, frequency=freq, fixability=fixability, score=score,
                priority=bucket(score, blocking), blocking=blocking, basis=basis,
                loss=round(loss, 6), n_sources=n_sources,
                evidence=ev, b_linked=b_linked,
                # R19-QA-3：`known_benign` 只改**处置**，不动 impact / frequency / score /
                # priority——与「fixability 不进分档」是同一条纪律（O-f：判定不看修复代价）。
                # 已登记的语料预期形态照原分数落档，只是处置从「fix_local（去改产品）」变成
                # 「known_benign（已登记，别去改产品）」，并按下方拟号规则不占 B 号。
                disposition=(DISP_KNOWN_BENIGN if known_benign
                             else DISPOSITION[fixability]),
                extra=extra)


def collect(*, attribution: dict | None = None, defects: dict | None = None,
            trace: dict | None = None, eval_summary: dict | None = None,
            dev_plan: str | None = None) -> list[Item]:
    """四层证据 → 候选条目。缺层就跳过；**同键跨样本先聚合再出一条**，不逐样本刷屏。"""
    dp = DEV_PLAN.read_text(encoding="utf-8") if dev_plan is None else dev_plan
    block_b = release_blocking(dp)
    items: list[Item] = []

    # ① 归因层：预算内偏差的 impact = 该类型 loss 占全语料 total_gap 之比；
    #    契约类偏差 loss=0，impact 改按严重度映射（basis 显式打出用的是哪一种）
    if attribution:
        recs = [r for r in attribution.get("records", []) if r.get("status") == "ok"]
        n = max(1, len(recs))
        gap = sum(float(r.get("total_gap", 0.0)) for r in recs)
        agg: dict[str, dict] = {}
        for r in recs:
            for d in r.get("deviations", []):
                a = agg.setdefault(d["kind"], {"loss": 0.0, "n": 0, "sev": "minor",
                                              "stage": d.get("stage", "-"), "ev": ""})
                a["loss"] += float(d.get("loss", 0.0))
                a["n"] += 1
                a["sev"] = _worse(a["sev"], d.get("severity", "minor"))
                a["ev"] = a["ev"] or d.get("evidence", "")
        for kind, a in sorted(agg.items()):
            if a["loss"] > 0 and gap > 0:
                impact, basis = min(1.0, a["loss"] / gap), "loss"
            else:
                impact, basis = SEVERITY_IMPACT[a["sev"]], "severity"
            b = LINK_B.get(kind, "")
            items.append(make_item(
                "attribution", kind, f"偏差类型 {kind}（{a['stage']}）", a["stage"],
                impact, a["n"] / n, FIXABILITY[kind], basis,
                a["sev"] == "blocking" or b in block_b, a["loss"], a["n"],
                a["ev"], b))

    # ② 缺陷层：impact 按严重度，frequency = 命中样本数 / ok 样本数
    if defects:
        recs = [r for r in defects.get("records", []) if r.get("status") == "ok"]
        n = max(1, len(recs))
        agg = {}
        for r in recs:
            for f in r.get("findings", []):
                # severity 缺失时用 defect_scan.SEVERITY 的**登记值**兜底：规则名就是
                # 口径；未登记的按 minor 计（不静默抬档）。
                sev = f.get("severity") or defect_scan.SEVERITY.get(f["rule"], "minor")
                a = agg.setdefault(f["rule"], {"sev": sev, "ev": "", "src": set(),
                                               "benign": set()})
                a["src"].add(r.get("source", "?"))
                # R19-QA-3：登记是 (样本, 规则) 级的。同一规则若在**任一**未登记样本上
                # 命中，本条就仍是可施工项——按规则整体豁免会把真实退化一起吞掉。
                if f.get("known_benign"):
                    a["benign"].add(r.get("source", "?"))
                a["sev"] = _worse(a["sev"], sev)
                a["ev"] = a["ev"] or f.get("evidence", "")
        for rule, a in sorted(agg.items()):
            b = LINK_B.get(rule, "")
            items.append(make_item(
                "defect", rule, f"规则命中 {rule}（{RULE_STAGE[rule]}）",
                RULE_STAGE[rule], SEVERITY_IMPACT[a["sev"]], len(a["src"]) / n,
                FIXABILITY[rule], "severity",
                a["sev"] == "blocking" or b in block_b, 0.0, len(a["src"]), a["ev"], b,
                known_benign=bool(a["src"]) and a["benign"] == a["src"]))

    # ③ 可追溯层：结构性缺口，每轮恒定存在 ⇒ frequency = 1.0（basis=structural）
    if trace:
        unc = trace.get("uncovered", {})
        for grp in ("FR_P0", "FR_other", "NFR", "E", "W"):
            ids = sorted(unc.get(grp, []))
            if ids:
                items.append(make_item("trace", grp, TRACE_TITLE[grp], "-",
                                       TRACE_IMPACT[grp], 1.0, FIX_LOCAL,
                                       "structural", grp == "FR_P0", 0.0, len(ids),
                                       _join_capped(ids), ""))
        orph = sorted(trace.get("orphans", []))
        if orph:
            items.append(make_item("trace", "orphans", "测试引用了不存在的编号", "-",
                                   0.2, 1.0, FIX_LOCAL, "structural", False, 0.0,
                                   len(orph), _join_capped(orph), ""))

    # ④ 全量对账层：产品基线腿（expected/）红 = 已交付基线漂移，恒 blocking
    if eval_summary:
        n = max(1, int(eval_summary.get("n_ok", 0)))
        gf = eval_summary.get("gate_failures", [])
        if gf:
            items.append(make_item("eval", "gate_failures",
                                   "产品基线红线失败（expected/ 漂移）", "-", 1.0,
                                   len(gf) / n, FIX_LOCAL, "severity", True, 0.0,
                                   len(gf), _join_capped([g["source"] for g in gf]), ""))
        if eval_summary.get("n_error"):
            items.append(make_item("eval", "errors", "转换异常（status=error）", "-",
                                   1.0, int(eval_summary["n_error"]) / n, FIX_LOCAL,
                                   "severity", True, 0.0, int(eval_summary["n_error"]),
                                   "详见 eval.json 的 records[].error", ""))
    return items


def _input_keys(*, attribution: dict | None = None, defects: dict | None = None,
                trace: dict | None = None, eval_summary: dict | None = None) -> dict[str, set[str]]:
    """每层「候选键」的独立清点（不复用 collect 的聚合结果）。

    与 collect() 的产出做**异源对账**：同键聚合后每个候选键必须恰好对应一条
    Item——少一条说明候选被吞，多一条说明凭空造了优先级项。
    """
    want: dict[str, set[str]] = {"attribution": set(), "defect": set(),
                                 "trace": set(), "eval": set()}
    if attribution:
        for r in attribution.get("records", []):
            if r.get("status") != "ok":
                continue
            for d in r.get("deviations", []):
                want["attribution"].add(d["kind"])
    if defects:
        for r in defects.get("records", []):
            if r.get("status") != "ok":
                continue
            for f in r.get("findings", []):
                want["defect"].add(f["rule"])
    if trace:
        unc = trace.get("uncovered", {})
        for grp in ("FR_P0", "FR_other", "NFR", "E", "W"):
            if unc.get(grp):
                want["trace"].add(grp)
        if trace.get("orphans"):
            want["trace"].add("orphans")
    if eval_summary:
        if eval_summary.get("gate_failures"):
            want["eval"].add("gate_failures")
        if eval_summary.get("n_error"):
            want["eval"].add("errors")
    return want


def prioritize(*, attribution: dict | None = None, defects: dict | None = None,
               trace: dict | None = None, eval_summary: dict | None = None,
               dev_plan: str | None = None) -> dict:
    """排序（分档 → 分数 → 可修复性 → 来源 → 键）→ 拟 B 草稿号 → 算恒等式。"""
    dp = DEV_PLAN.read_text(encoding="utf-8") if dev_plan is None else dev_plan
    items = collect(attribution=attribution, defects=defects, trace=trace,
                    eval_summary=eval_summary, dev_plan=dp)
    items.sort(key=lambda it: (it.priority, -it.score, -it.fixability,
                               it.origin, it.key))
    # 异源对账：候选键从**原始输入**独立清点，再与 collect() 的产出逐来源比对。
    # 只比 items 内部的三条合计是自证——它们全由 items 派生，除重复键外恒真。
    want = _input_keys(attribution=attribution, defects=defects, trace=trace,
                       eval_summary=eval_summary)
    got = {origin: {it.key for it in items if it.origin == origin} for origin in want}
    by_input = sum(len(v) for v in want.values())
    # 拟新号：只给「需人工裁定 / 需外部环境」且尚未登记的项。
    # rebake **不占号**——重烤是本机可执行的实施动作（人工复核是其验收步骤），
    # 进实施队列；真值本身只能人工对照的（如 B-43）本来就落在 human。
    nxt = next_free_b(dp)
    for it in items:
        if it.b_linked or it.disposition not in ("human", "external"):
            continue
        it.b_draft = f"B-{nxt}"
        nxt += 1
    counts = {p: sum(1 for it in items if it.priority == p) for p in ("P0", "P1", "P2")}
    origins: dict[str, int] = {}
    for it in items:
        origins[it.origin] = origins.get(it.origin, 0) + 1
    uniq = {(it.origin, it.key) for it in items}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "weights": W, "floors": {"P0": P0_FLOOR, "P1": P1_FLOOR},
        "items": [it.to_dict() for it in items],
        "counts": counts, "origins": origins, "b_next": nxt,
        "blocking": sorted({(it.b_linked or it.b_draft) for it in items
                            if it.blocking and (it.b_linked or it.b_draft)}),
        "inputs": {"attribution": attribution is not None,
                   "defects": defects is not None,
                   "trace": trace is not None,
                   "eval": eval_summary is not None},
        "identity": {"n_items": len(items),
                     "by_priority": sum(counts.values()),
                     "by_origin": sum(origins.values()),
                     "unique_keys": len(uniq),
                     "by_input": by_input,
                     # 四条内部合计（廉价的展示不变量）+ 与原始输入的异源对账。
                     "ok": len(items) == sum(counts.values())
                           == sum(origins.values()) == len(uniq)
                           == by_input
                           and all(want[o] == got[o] for o in want)},
    }


def _cell(v) -> str:
    """Markdown 表格单元格：转义竖线、折叠换行。"""
    return str(v).replace("|", "\\|").replace("\n", " ")


def _b_cell(it: dict) -> str:
    """B 列：已登记关联优先于草稿号（两者不会同时非空，见 prioritize 的拟号条件）。"""
    if it.get("b_linked"):
        return it["b_linked"]
    if it.get("b_draft"):
        return f"{it['b_draft']}（草稿）"
    return "-"


def report_md(payload: dict, *, top: int = 0) -> str:
    """人类可读施工单：分层汇总 + 三个分档表 + B 号段 + 恒等式。

    `top > 0` 时每个分档只打印前 `top` 行（JSON 恒含全部条目——截断只影响阅读）。
    """
    ident = payload["identity"]
    counts = payload.get("counts", {})
    items = payload.get("items", [])
    inputs = payload.get("inputs", {})
    wts = payload.get("weights") or W
    lines = [
        "# Doc2md 优先级施工单（自动生成）", "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 分档权重：impact {wts.get('impact', W['impact'])} / "
        f"frequency {wts.get('frequency', W['frequency'])}"
        f"（和 {sum(wts.values()):.1f}；**fixability 不进分档**）",
        f"- 落档下限：P0 ≥ {payload.get('floors', {}).get('P0', P0_FLOOR)}、"
        f"P1 ≥ {payload.get('floors', {}).get('P1', P1_FLOOR)}；blocking 无条件 P0",
        "- 同档排序：分档 → 分数降序 → 可修复性降序 → 来源 → 键",
        "- `--top`：本报告只截断各档的打印行数；JSON 恒含全部条目，"
        "恒等式只对全量成立",
        "",
        "## 数据层", "",
        "| 层 | 状态 |", "|---|---|",
    ]
    for key in ("attribution", "defects", "trace", "eval"):
        ok = bool(inputs.get(key))
        lines.append(f"| {LAYER_LABEL[key]} | {'已提供' if ok else '未提供'} |")
    missing = [LAYER_LABEL[k] for k in ("attribution", "defects", "trace", "eval")
               if not inputs.get(k)]
    lines.append("")
    if missing:
        lines.append("- 未提供：" + "、".join(missing)
                     + "——该层不参与本次排序（不按 0 补、不猜）")
    else:
        lines.append("- 未提供：无（四层齐备）")
    lines += [
        "",
        f"IDENTITY n_items={ident['n_items']} by_priority={ident['by_priority']} "
        f"by_origin={ident['by_origin']} unique={ident['unique_keys']} "
        f"by_input={ident.get('by_input', '?')} "
        f"ok={'true' if ident['ok'] else 'false'}",
        "",
    ]
    if not ident["ok"]:
        lines += ["❌ 组合口径不可复算：条目数与分档 / 来源 / 唯一键 / 输入候选合计"
                  "对不上，本表不可用于排期。", ""]

    header = ("| # | 档 | 来源 | 键 | 阶段 | 影响 | 依据 | 频率 | 分数 | 可修 | "
              "处置 | 阻断 | B 关联 / 草稿 | 证据 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    for pri in ("P0", "P1", "P2"):
        rows = [it for it in items if it["priority"] == pri]
        shown = rows[:top] if top > 0 else rows
        title = f"## {pri}（{len(rows)} 条"
        title += f"，显示前 {len(shown)} 条）" if len(shown) < len(rows) else "）"
        lines += [title, "", header, sep]
        for i, it in enumerate(shown, start=1):
            lines.append(
                f"| {i} | {pri} | {_cell(it['origin'])} | {_cell(it['key'])} | "
                f"{_cell(it['stage'])} | {it['impact']:.4f} | {_cell(it['basis'])} | "
                f"{it['frequency']:.4f} | {it['score']:.4f} | {it['fixability']:.1f} | "
                f"{_cell(it['disposition'])} | {'是' if it['blocking'] else '否'} | "
                f"{_cell(_b_cell(it))} | {_cell(it['evidence'])} |")
        if not shown:
            lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - |")
        lines.append("")

    lines += ["## 分档合计", "", "| 档 | 条数 |", "|---|---|"]
    for pri in ("P0", "P1", "P2"):
        lines.append(f"| {pri} | {counts.get(pri, 0)} |")
    lines.append(f"| 合计 | {sum(counts.values())} |")
    lines.append("")

    linked = [it for it in items if it.get("b_linked")]
    drafted = [it for it in items if it.get("b_draft")]
    lines += ["## B 号（关联与草稿）", ""]
    if linked or drafted:
        lines += ["| 条目 | B 号 | 性质 |", "|---|---|---|"]
        for it in linked:
            lines.append(f"| {_cell(it['origin'])}/{_cell(it['key'])} | "
                         f"{_cell(it['b_linked'])} | 已登记关联（dev-plan §12.7） |")
        for it in drafted:
            lines.append(f"| {_cell(it['origin'])}/{_cell(it['key'])} | "
                         f"{_cell(it['b_draft'])} | 草稿（待人工确认） |")
    else:
        lines.append("- 无（既无已登记关联，也无需拟号的项）")
    lines += [
        "",
        "草稿号只是提案：**落盘与号段确认由人工在 Task 10 完成**，本层不改 "
        "dev-plan §12.7；号段从该段现有最大号 +1 派生，不写死。",
        "",
        f"PRIORITIES P0={counts.get('P0', 0)} P1={counts.get('P1', 0)} "
        f"P2={counts.get('P2', 0)} N={ident['n_items']} "
        f"IDENTITY={'ok' if ident['ok'] else 'bad'}",
    ]
    return "\n".join(lines) + "\n"


def _dump_json(path: str, payload: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_layer(layer: str, raw: str | None) -> tuple[dict | None, bool]:
    """读一层输入。返回 (数据, 是否可用)；不可解析属基础设施错误，由调用方退 2。

    `--eval` 取 `payload["summary"]`（eval.json 顶层是 `{meta, records, summary}`），
    其余三层取顶层本体（trace.json 顶层就是 `trace()` 的返回值 + generated_at）。
    """
    if not raw:
        print(f"未提供：--{layer}（未指定）")
        return None, True
    p = Path(raw)
    if not p.is_file():
        print(f"未提供：{p}（文件不存在）")
        return None, True
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if layer == "eval":
            if not isinstance(data, dict) or not isinstance(data.get("summary"), dict):
                raise ValueError("eval.json 缺 summary 对象（顶层应为 {meta, records, summary}）")
            data = data["summary"]
        elif not isinstance(data, dict):
            raise ValueError(f"顶层应为 JSON 对象，实际 {type(data).__name__}")
    except (OSError, ValueError, TypeError) as exc:
        print(f"输入不可解析：{p}：{type(exc).__name__}: {exc}", file=sys.stderr)
        return None, False
    return data, True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Doc2md 优先级引擎（信息层）：影响 × 频率 → 分档，成本只排期。",
        epilog="缺席的输入层记「未提供」并继续（退 0）；输入不可解析 / §12.7 锚点"
               "失效属基础设施错误，退 2。")
    ap.add_argument("--attribution", default=None,
                    help="归因层 JSON（work/qa/attribution.json，取顶层本体）")
    ap.add_argument("--defects", default=None,
                    help="缺陷层 JSON（work/qa/defects.json，取顶层本体）")
    ap.add_argument("--trace", default=None,
                    help="可追溯层 JSON（work/qa/trace.json，取顶层本体）")
    ap.add_argument("--eval", default=None,
                    help="全量对账 JSON（work/qa/eval.json，只取 payload['summary']）")
    ap.add_argument("--json", default=None, help="写完整结果（全部条目）的 JSON 路径")
    ap.add_argument("--md", default=None, help="写 Markdown 报告路径")
    ap.add_argument("--top", type=int, default=0,
                    help="每个分档在 Markdown / 控制台最多打印 N 行（0 = 全部）；"
                         "JSON 恒含全部条目，否则恒等式不成立")
    args = ap.parse_args()

    if args.top < 0:
        print(f"参数错误：--top 必须 ≥ 0，实际 {args.top}", file=sys.stderr)
        return 2

    layers: dict[str, dict | None] = {}
    for layer, raw in (("attribution", args.attribution), ("defects", args.defects),
                       ("trace", args.trace), ("eval", args.eval)):
        data, ok = _read_layer(layer, raw)
        if not ok:
            return 2
        layers[layer] = data

    dp = DEV_PLAN.read_text(encoding="utf-8") if DEV_PLAN.is_file() else ""
    if not b_rows(dp):
        print(f"§12.7 锚点失效：{DEV_PLAN} 里解析不到任何 B 行（阻断集合与新号段"
              f"无法派生，拒绝出一份不可信的施工单）", file=sys.stderr)
        return 2

    out = prioritize(attribution=layers["attribution"], defects=layers["defects"],
                     trace=layers["trace"], eval_summary=layers["eval"],
                     dev_plan=dp)

    if args.json:
        _dump_json(args.json, out)
        print(f"\nJSON  -> {args.json}")

    print("\n" + "=" * 72)
    for pri in ("P0", "P1", "P2"):
        rows = [it for it in out["items"] if it["priority"] == pri]
        shown = rows[:args.top] if args.top > 0 else rows
        print(f"{pri}（{len(rows)} 条"
              + (f"，显示前 {len(shown)} 条）" if len(shown) < len(rows) else "）"))
        for it in shown:
            print(f"  {it['score']:.4f} {it['origin']:11s} {it['key']:24s} "
                  f"fix={it['fixability']:.1f} {it['disposition']:9s} "
                  f"{'阻断' if it['blocking'] else '    '} "
                  f"{(it['b_linked'] or it['b_draft'] or '-'):7s} {it['title']}")
        if not shown:
            print("  （空）")
    ident = out["identity"]
    print("-" * 72)
    print("  分档 " + " / ".join(f"{p} {out['counts'][p]}" for p in ("P0", "P1", "P2")))
    draft = [it["b_draft"] for it in out["items"] if it["b_draft"]]
    print("  B 草稿号：" + (f"{draft[0]}…{draft[-1]}（提案，待人工在 Task 10 确认）"
                            if draft else "无"))
    print(f"IDENTITY n_items={ident['n_items']} by_priority={ident['by_priority']} "
          f"by_origin={ident['by_origin']} unique={ident['unique_keys']} "
          f"by_input={ident['by_input']} ok={'true' if ident['ok'] else 'false'}")
    print(f"PRIORITIES P0={out['counts']['P0']} P1={out['counts']['P1']} "
          f"P2={out['counts']['P2']} N={ident['n_items']} "
          f"IDENTITY={'ok' if ident['ok'] else 'bad'}")

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report_md(out, top=args.top), encoding="utf-8")
        print(f"Markdown -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
