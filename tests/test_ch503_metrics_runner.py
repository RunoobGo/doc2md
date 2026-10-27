r"""CH-503（第十八轮）B-34 指标汇总器的红线：`tests/corpus/run_metrics.py`。

补的是**上报面**（CH-500 报告 AR-11 / B-34 更正口径）：`doc2md/qa_eval.py` 的三个指标
此前只有单篇消费者（`convert._metrics` 随报告走、`test_qa_n03_ideal.py` 46 项断言），
没有任何地方把 24 份 ideal 标注**逐篇汇总**——「指标存在」与「指标看得见」之间断了链。
本轮落地 runner 后，本文件钉住它最容易写坏的四处：

① **分母**：24 份标注（DOCX 14 / PDF 10）逐篇都要有一行；漏一篇就是「看不见的回退」。
② **无真值 ≠ 满分**：`heading_pr` 在 `ref==0` 时各项 `None`（`pdf_10_two_column_6p` 实测如此）。
   聚合若按 1.0 计入，质量数会随「标注变空」而**上升**——这是度量面上的空跑形态。
③ **双口径不得交叉**：对 `expected/*.md`（管线自己重烤的基线）的 `char_f1` 只作漂移监控，
   混进 ideal 聚合就等于把自啮合数当质量真值（C-31 的老病灶）。
④ **冻结基线**：逐篇命中数/一致率/漂移 F1 与 `tests/corpus/metrics/ideal-baseline.json`
   相等；产物结构回退在这里转红，而不是等人去看报告。

④ 需要真实转换（24 篇本机秒级），因此挂 `corpus` 标记跑在全量层；
①②③⑤ 是纯函数与文件系统检查，毫秒级，进 gate。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BASELINE_PATH = REPO / "tests" / "corpus" / "metrics" / "ideal-baseline.json"

# 现存标注集合的分母口径（CH-505 实测，不是估算）：DOCX 14 份 / PDF 10 份 = 24
EXPECTED_BATCH_COUNTS = {"docx": 14, "pdf": 10}
# 无标题真值的样本：双栏样本没有标题可标，必须显形而不是被算成满分
EXPECTED_NO_TRUTH = {"pdf_10_two_column_6p"}


def _runner():
    """按路径加载 `tests/corpus/run_metrics.py`（该目录不是包，不能 import）。"""
    path = REPO / "tests" / "corpus" / "run_metrics.py"
    spec = importlib.util.spec_from_file_location("d2m_run_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(stem: str, batch: str, *, ref: int, matched: int, f1, rate, drift_f1) -> dict:
    """合成一行度量结果——只喂 `summarize`，不做转换。"""
    return {
        "stem": stem, "batch": batch,
        "headings": {"pred": matched, "ref": ref, "matched": matched,
                     "p": f1, "r": f1, "f1": f1, "no_truth": ref == 0},
        "tables": {"tables": 1 if rate is not None else 0,
                   "rows": 0 if rate is None else 3, "rate": rate},
        "drift": None if drift_f1 is None else {"p": drift_f1, "r": drift_f1,
                                                "f1": drift_f1, "bytes": 10},
    }


# --- ① 分母：每份标注都要有一行 ---------------------------------------------
@pytest.mark.gate
def test_annotation_denominator_and_source_files_are_complete():
    """24 份标注、两批分母、每份都能解析到真实源文件——漏一篇就红，不用等转换。"""
    from tests.qa.assertions import assert_no_vacuous, require_hits

    rm = _runner()
    items = require_hits(rm.annotations(), 24, "ideal 标注清单")
    counts = {"docx": 0, "pdf": 0}
    for it in items:
        counts["docx" if it["stem"].startswith("docx_") else "pdf"] += 1
    assert counts == EXPECTED_BATCH_COUNTS, (
        f"标注分母漂移：实测 {counts}，冻结口径 {EXPECTED_BATCH_COUNTS}。"
        "新增/删除标注要连带重烤 ideal-baseline.json（run_metrics.py --write-baseline）")
    # 每份标注都得指向一个真实存在的源文件，且 kind 只有 headings 这一形态
    assert_no_vacuous(items, lambda it: (rm.SOURCES / it["annotation"]["source"]).is_file(),
                      subject="标注→源文件解析")
    assert_no_vacuous(items, lambda it: it["kind"] == "headings",
                      subject="标注 kind 口径")


@pytest.mark.gate
def test_baseline_rows_cover_exactly_the_annotation_set():
    """冻结基线的样本集合与现存标注**双向差为空**：孤儿基线行与缺基线行都要红。"""
    rm = _runner()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    from tests.qa.assertions import require_hits

    frozen = {r["stem"] for r in require_hits(baseline["rows"], 24, "基线行")}
    live = {it["stem"] for it in rm.annotations()}
    assert frozen == live, (
        f"基线与标注集合错位：只有基线有 {sorted(frozen - live)}，"
        f"只有标注有 {sorted(live - frozen)}")


# --- ② 无真值 ≠ 满分 --------------------------------------------------------
@pytest.mark.gate
def test_empty_reference_is_never_aggregated_as_perfect():
    """负夹具（OPT-2 口径在度量面的形态）：ref=0 的行不得进聚合，全空则聚合为 None。"""
    rm = _runner()
    only_no_truth = [_row("x_1", "docx", ref=0, matched=0, f1=None, rate=None, drift_f1=1.0)]
    s = rm.summarize(only_no_truth, None)["docx"]
    assert s["heading_micro_recall"] is None and s["heading_macro_f1"] is None, (
        f"无真值被聚合成了数：{s}——空标注会让质量分凭空出现")
    assert s["no_truth"] == ["x_1"], f"无真值行未被单列：{s['no_truth']}"

    mixed = only_no_truth + [_row("x_2", "docx", ref=4, matched=2, f1=0.5,
                                  rate=None, drift_f1=1.0)]
    s2 = rm.summarize(mixed, None)["docx"]
    # 只算有真值的那一篇：2/4 = 0.5，而不是 0/4 = 0.0 也不是 (0.5+1.0)/2
    assert s2["heading_micro_recall"] == 0.5 and s2["heading_macro_f1"] == 0.5, s2
    assert s2["n"] == 2 and s2["no_truth"] == ["x_1"]


@pytest.mark.gate
def test_empty_row_set_aggregates_to_none_not_one():
    """空集合不得给出 1.0：`sum([])/len([])` 若被兜底成满分，整条腿就恒真。"""
    rm = _runner()
    s = rm.summarize([], None)
    for batch in ("docx", "pdf"):
        assert s[batch]["heading_micro_recall"] is None
        assert s[batch]["heading_macro_f1"] is None
        assert s[batch]["table_macro_rate"] is None
        assert s[batch]["drift_macro_f1"] is None


# --- ③ 双口径不交叉 ----------------------------------------------------------
@pytest.mark.gate
def test_drift_caliber_does_not_leak_into_ideal_aggregate():
    """漂移 F1 与 ideal 数各走各的：ideal 无真值时不得拿漂移数顶上，反之亦然。"""
    rm = _runner()
    rows = [_row("d_1", "docx", ref=0, matched=0, f1=None, rate=None, drift_f1=0.25)]
    s = rm.summarize(rows, None)["docx"]
    assert s["heading_micro_recall"] is None and s["heading_macro_f1"] is None, s
    assert s["drift_macro_f1"] == 0.25, s          # 漂移列照样出数，两列互不替补
    # 漂移列单独聚合分母：只数真有 expected 基线产物的行
    assert rm.summarize([_row("d_2", "docx", ref=1, matched=1, f1=1.0,
                              rate=None, drift_f1=None)], None)["docx"]["drift_macro_f1"] is None


# --- ④ 冻结基线（需真实转换，跑在全量层） -----------------------------------
@pytest.mark.corpus
def test_measured_metrics_match_frozen_baseline():
    """逐篇标题命中数 / 表格列一致率 / 漂移 F1 与基线相等；三列各自独立比。"""
    rm = _runner()
    from tests.qa.assertions import assert_no_vacuous, require_hits

    rows = require_hits(rm.measure()["rows"], 24, "语料逐篇指标行")
    assert_no_vacuous(rows, lambda r: "headings" in r or "skipped" in r,
                      subject="指标行完整性（每篇要么出数要么写明跳过原因）")
    skipped = [(r["stem"], r["skipped"]) for r in rows if "skipped" in r]
    assert not skipped, (
        f"有样本没能出数：{skipped}"
        "——分母里每一篇都得有指标行或明确的失败归因")
    frozen = {r["stem"]: r for r in json.loads(
        BASELINE_PATH.read_text(encoding="utf-8"))["rows"]}
    drift = {}
    for r in rows:
        b = frozen[r["stem"]]
        for col in ("headings", "tables", "drift"):
            if r.get(col) != b.get(col):
                drift.setdefault(r["stem"], {})[col] = {
                    "measured": r.get(col), "frozen": b.get(col)}
    assert not drift, (
        f"指标与冻结基线不齐：{json.dumps(drift, ensure_ascii=False)}\n"
        "先判定是产物回退还是有意改进；确认无误后跑 "
        "`.venv/bin/python tests/corpus/run_metrics.py --write-baseline` 重烤并在收口报告写明")


@pytest.mark.corpus
def test_frozen_summary_counts_are_stated_and_reproducible():
    """汇总行的两个总数（DOCX 117 / PDF 102 条标题真值）必须能复算——文档引用的是它们。"""
    rm = _runner()
    s = rm.measure()["summary"]
    assert s["docx"]["headings_ref"] == 117 and s["docx"]["headings_matched"] == 117, s["docx"]
    assert s["pdf"]["headings_ref"] == 102 and s["pdf"]["headings_matched"] == 102, s["pdf"]
    assert set(s["docx"]["no_truth"] + s["pdf"]["no_truth"]) == EXPECTED_NO_TRUTH, (
        f"无真值样本集合漂移：实测 {s['docx']['no_truth'] + s['pdf']['no_truth']}")
    assert s["filter"] is None, "全集口径：聚合不得带 --only 过滤"
