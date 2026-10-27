"""CH-505 / OPT4-8：语料重烤前的逐行 diff 报告（`run_corpus.py --diff-report`）。

产物字节冻结既是防线也是改进税：每项改产物的修复都要连带人工复核 20+ 篇产物。
本报告把复核面从「整篇产物」压到「变化行 + 类型」——四类里前三类可批量放行，
结构变化必须逐条看。判据必须**保守**：行块增删、低相似度的 1:1 替换一律归
「结构变化」，宁可多报、不可少报（把文本增删误判成仅空白会放行真缺陷）。

自证（OPT4-8 验收）：
- 不改产品 → 报告 `0 篇有变化`；
- `_row_cells` 的 `"".join` 改 `" ".join` → 22 篇语料实测 11 行、全部「仅空白」，
  0 篇含结构变化（口径见 `docs/qa/round4/optimization-plan.md` OPT4-8）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CORPUS = REPO / "tests" / "corpus"


def _runner():
    """按路径加载 `tests/corpus/run_corpus.py`（该目录不是包，不能 import）。"""
    path = CORPUS / "run_corpus.py"
    spec = importlib.util.spec_from_file_location("d2m_run_corpus", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.gate
def test_line_classification_covers_four_kinds_conservatively():
    """四类各一正例 + 三条保守反例：不确定的一律落「结构变化」。"""
    rc = _runner()
    assert rc.classify_line("| 第一章总则 | .... | 1 |",
                            "| 第一章 总则 | .... | 1 |") == "仅空白"
    assert rc.classify_line("", "   ") == "仅空白"
    assert rc.classify_line(r"a \& b \| c", "a & b | c") == "仅标点转义"
    assert rc.classify_line("| 甲 | 5.00 |", "| 甲 | 6.00 |") == "文本增删"
    # 保守反例：整行换义 / 空行变正文 / 低相似度替换都必须进结构变化
    assert rc.classify_line("完全不同的整行内容", "另一个世界的文字") == "结构变化"
    assert rc.classify_line("", "# 新标题") == "结构变化"


@pytest.mark.gate
def test_block_insert_delete_is_structural():
    """行数不等的块（新增/删除整行）不做逐行归类，一律结构变化。"""
    rc = _runner()
    changes = rc.diff_lines("甲\n乙\n丙\n", "甲\n丙\n")
    assert [c["kind"] for c in changes] == ["结构变化"], changes
    assert changes[0]["old"] == "乙" and changes[0]["new"] is None
    added = rc.diff_lines("甲\n", "甲\n乙\n")
    assert [c["kind"] for c in added] == ["结构变化"] and added[0]["new"] == "乙"


@pytest.mark.gate
def test_report_summary_separates_batch_safe_from_structural(tmp_path):
    """汇总行三档分列，新增样本不与「有变化」混算。"""
    rc = _runner()
    for name, text in (("a.md", "甲乙\n"), ("b.md", "甲 乙\n"), ("c.md", "丙\n")):
        (tmp_path / name).write_text(text, encoding="utf-8")
    entries = [
        {"source": "a.pdf", "expected_markdown": str(tmp_path / "a.md")},
        {"source": "b.pdf", "expected_markdown": str(tmp_path / "b.md")},
        {"source": "c.pdf", "expected_markdown": str(tmp_path / "c.md")},
        {"source": "d.pdf", "expected_markdown": str(tmp_path / "d.md")},
    ]
    results = {
        "a.pdf": {"actual": "甲 乙\n"},          # 与基线 甲乙 仅差空白
        "b.pdf": {"actual": "甲\n乙\n"},          # 基线一行 → 结构变化
        "c.pdf": {"actual": "丙\n"},              # 基线一致
        "d.pdf": {"actual": "丁\n"},              # 无基线 → 新增
    }
    report = rc.build_report(entries, results)
    s = report["summary"]
    assert s["total"] == 3 and s["changed"] == 2 and s["same"] == 1 and s["new"] == 1, s
    assert s["whitespace_escape_only"] == 1 and s["structural"] == 1, s
    line = rc.render_report(report)
    assert ("汇总：3 篇中 2 篇有变化，其中 1 篇仅空白/转义类、0 篇仅文本增删类、"
            "1 篇含结构变化；另 1 篇无基线（新增样本）。") in line


@pytest.mark.corpus
def test_live_corpus_report_is_empty_without_product_changes(tmp_path, capsys):
    """自证：不改产品时，全量语料的 diff 报告必须 0 篇有变化、0 篇新增。"""
    rc = _runner()
    entries = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    result = rc.run(entries, diff_dir=tmp_path)
    capsys.readouterr()
    s = result["report"]["summary"]
    assert result["failed"] == 0, result["summary_line"]
    assert s["changed"] == 0 and s["new"] == 0 and s["structural"] == 0, result["summary_line"]
    assert s["total"] == len(entries) - len(s["rejected"]), s