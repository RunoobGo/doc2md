"""tests/qa 工具链自检——指标引擎与源端真值的边界行为。

命名：`test_<行为>_<条件>_<预期结果>`。
覆盖：正常路径 + 每函数至少 1 个异常/边界路径。用例之间无执行顺序依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.qa import mdscore  # noqa: E402

pytestmark = pytest.mark.corpus


# ---------------------------------------------------------------------------
# ned / nid / levenshtein
# ---------------------------------------------------------------------------

def test_ned_identical_text_returns_one():
    assert mdscore.ned("abc", "abc") == 1.0


def test_ned_both_empty_returns_one():
    # 边界：两侧皆空 = 完美，不是 0
    assert mdscore.ned("", "") == 1.0


def test_ned_one_side_empty_returns_zero():
    assert mdscore.ned("abc", "") == 0.0
    assert mdscore.ned("", "abc") == 0.0


def test_nid_sequence_reversed_is_low():
    assert mdscore.nid(["a", "b", "c"], ["c", "b", "a"]) < 1.0


def test_nid_both_empty_returns_one():
    assert mdscore.nid([], []) == 1.0


# ---------------------------------------------------------------------------
# strip_front_matter
# ---------------------------------------------------------------------------

FM_MD = "---\npipeline_version: 2.18.0\n---\n\n# 标题\n正文\n"


def test_strip_front_matter_with_yaml_splits_correctly():
    fm, body = mdscore.strip_front_matter(FM_MD)
    assert fm.startswith("---")
    assert body.lstrip("\n").startswith("# 标题")


def test_strip_front_matter_without_yaml_returns_whole():
    fm, body = mdscore.strip_front_matter("# 标题\n")
    assert fm == ""
    assert body == "# 标题\n"


def test_parse_blocks_skips_front_matter_heading_like_keys():
    # front-matter 的 `---` 不得被当成分隔线，YAML 键值不得被当正文段落
    blocks = mdscore.parse_blocks(FM_MD)
    types = [b.type for b in blocks]
    assert "hr" not in types
    assert types.count("heading") == 1


# ---------------------------------------------------------------------------
# parse_blocks
# ---------------------------------------------------------------------------

def test_parse_blocks_detects_gfm_table():
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    blocks = mdscore.parse_blocks(md)
    assert [b.type for b in blocks] == ["table"]
    assert blocks[0].meta["header"] == ["a", "b"]
    assert blocks[0].meta["body"] == [["1", "2"]]


def test_parse_blocks_detects_tight_list_as_one_block():
    md = "- 甲\n- 乙\n- 丙\n"
    blocks = mdscore.parse_blocks(md)
    assert len(blocks) == 1 and blocks[0].type == "list"


def test_parse_blocks_separates_setext_heading():
    blocks = mdscore.parse_blocks("标题\n====\n正文\n")
    assert blocks[0].type == "heading" and blocks[0].level == 1


def test_parse_blocks_empty_input_returns_empty():
    assert mdscore.parse_blocks("") == []


# ---------------------------------------------------------------------------
# teds
# ---------------------------------------------------------------------------

def t(name, header, body):
    return {"header": header, "aligns": [], "body": body, "ncol": len(header)}


def test_teds_identical_table_returns_one():
    tb = t("x", ["a"], [["1"]])
    assert mdscore.teds(tb, tb) == 1.0


def test_teds_missing_row_scores_below_one():
    gt = t("x", ["h1", "h2"], [["1", "2"], ["3", "4"]])
    pred = t("x", ["h1", "h2"], [["1", "2"]])
    assert 0.0 <= mdscore.teds(pred, gt) < 1.0


def test_teds_column_loss_penalty_exceeds_row_loss():
    """实测本实现的不对称性：丢列惩罚**重于**丢行。

    参考资料 §二称「`<tr>` 比 `<td>` 高一层，丢行惩罚重于丢列」（同表缺行
    0.8125 / 缺列 0.84375）。**本实现实测结论相反**——丢列时表头与每个数据行
    都要各删一个单元格，代价沿行数放大；丢行只删一个 `<tr>` 子树（序列层
    删除代价恒为 1，不按子树规模计）。

    两者不矛盾：参考资料的构造是「删整列但保留列数」的特定形态，本实现按
    标准 TEDS 树距离算。**把这个反向钉住**，避免后续有人照抄参考资料的数字
    来「校准」本实现，把正确的实现改错。优化方向判读时须按本实现的实际口径。
    """
    base = t("x", ["h1", "h2"], [["1", "2"], ["3", "4"]])
    lost_row = t("x", ["h1", "h2"], [["1", "2"]])            # 少一个 <tr>
    lost_col = t("x", ["h1"], [["1"], ["3"]])                # 每行少一个 <td>
    assert mdscore.teds(lost_col, base) < mdscore.teds(lost_row, base)


def test_teds_missing_table_scores_zero_not_skipped():
    """漏检的表按 0 计入——区分「没找到」与「找到了但错了」。"""
    gt = t("x", ["h1"], [["1"]])
    empty = t("x", [], [])
    assert mdscore.teds(empty, gt) == 0.0


# ---------------------------------------------------------------------------
# lint：契约类 vs 方言类
# ---------------------------------------------------------------------------

def test_lint_flags_md040_for_bare_fence():
    assert mdscore.lint_report("```\ncode\n```\n").get("MD040") == 1


def test_lint_classifies_md025_as_dialect_not_contract():
    # 源文档有多个章级标题时产物就该有多个 H1——不得计入契约类
    assert mdscore.LINT_CLASS["MD025"] == "dialect"
    assert "MD025" not in mdscore.CONTRACT_RULES


def test_lint_contract_ignores_dialect_violations():
    md = "# A\n正文\n\n# B\n正文\n"      # 两个 H1 -> MD025（方言类）
    v = mdscore.lint_report(md)
    assert v.get("MD025")
    assert mdscore._lint_score(v, md.count("\n") + 1, contract_only=True) == 1.0


def test_lint_contract_flags_md001_heading_jump():
    md = "# A\n\n### C\n"                 # H1 直接跳 H3
    assert mdscore.lint_report(md).get("MD001") == 1


# ---------------------------------------------------------------------------
# render 红线 / asset / idempotent
# ---------------------------------------------------------------------------

def test_render_redlines_catches_strike_by_default():
    assert mdscore.render_redlines("~~删除线~~")["score"] == 0.0


def test_render_redlines_allows_strike_when_enabled():
    assert mdscore.render_redlines("~~删除线~~", strike_enabled=True)["score"] == 1.0


def test_render_redlines_catches_self_anchor():
    assert mdscore.render_redlines("[x](#a)")["hits"].get("self_anchor") == 1


def test_render_redlines_detects_unclosed_fence():
    assert mdscore.render_redlines("```\ncode\n")["hits"].get("unclosed_fence") == 1


def test_asset_report_no_links_is_perfect():
    assert mdscore.asset_report("# 标题\n正文\n")["rate"] == 1.0


def test_asset_report_flags_broken_anchor():
    assert mdscore.asset_report("[x](#nope)")["rate"] == 0.0


def test_idempotent_rate_identical_outputs_is_one():
    assert mdscore.idempotent_rate(["a", "a", "a"]) == 1.0


def test_idempotent_rate_divergent_outputs_is_fraction():
    assert mdscore.idempotent_rate(["a", "a", "b"]) == pytest.approx(2 / 3)


def test_idempotent_rate_single_run_is_one():
    assert mdscore.idempotent_rate(["a"]) == 1.0


# ---------------------------------------------------------------------------
# score() 汇总
# ---------------------------------------------------------------------------

def test_score_without_ref_emits_only_consistency_keys():
    out = mdscore.score("# A\n正文\n", ref=None, ref_kind="none")
    assert "text" not in out["scores"]
    assert "lint_contract" in out["scores"]
    assert 0.0 <= out["overall"] <= 1.0


def test_score_with_ref_emits_fidelity_keys():
    out = mdscore.score("# A\n正文\n", ref="# A\n正文\n")
    assert out["scores"]["text"] == 1.0
    assert out["scores"]["structure"] == 1.0


def test_score_gate_uses_contract_lint_not_dialect():
    md = "# A\n正文\n\n# B\n正文\n"
    out = mdscore.score(md, ref=None, profile="generic")
    assert out["gate"]["passed"] is True


def test_score_unknown_profile_falls_back_to_generic():
    out = mdscore.score("# A\n", ref=None, profile="__nope__")
    assert out["profile"] == "__nope__"
    assert out["gate"]["thresholds"] == mdscore.PROFILES["generic"]["gates"]


def test_score_batch_profile_weights_sum_to_one():
    out = mdscore.score("# A\n", ref=None, profile="batch")
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-6
