"""N-4（QA-OPT-3）：页眉判据扩展——罗马数字页码 + 「章节名+页码」混合页眉。

登记项 N-4（optimization-report §6）：`_furniture_key` 原来只覆盖阿拉伯
页码；罗马数字页码（Page I/II、— IV —、第 iv 页）与「章节名 · 页码」
同行混合页眉（页码逐页递增导致旧键聚不出跨页重复）未覆盖。

宁漏勿误护栏（负例钉住）：
- 裸罗马数字（"IV" 单独出现）无上下文不认——与裸阿拉伯数字不入
  `_PAGE_NUM` 同一对称口径；
- `第 IV 章` 是真标题量词，不折 `#`；
- 混合页眉的尾随页码只在 **key 层**抹平，不进 `_PAGE_NUM_INLINE`
  （那会整行丢弃、连章节名一起误杀）；
- 尾随分隔符不含连字符：日期（2026-10-22）、编号（表 3-1）不受影响。
"""

from __future__ import annotations

import pytest

from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.pipeline.s3_structure import (
    _PAGE_NUM,
    _furniture_key,
    analyze,
)

pytestmark = [pytest.mark.gate, pytest.mark.milestone("DoD")]


def _page(*lines: Line, index: int = 0, height: float = 800.0) -> RawPage:
    return RawPage(index=index, lines=list(lines), height=height)


def _line(text: str, **kw) -> Line:
    return Line(text=text, **kw)


def _texts(ir) -> list[str]:
    return [b.text for b in ir.blocks]


# ---------------------------------------------------------------- 罗马页码：key 归一化

def test_furniture_key_masks_roman_page_col_variants():
    assert _furniture_key("第 IV 页第 II 栏") == "##"
    assert _furniture_key("第 xii 页") == "#"
    assert _furniture_key("第 I 页 / 第 iii 栏") == "# / #"


def test_page_num_fullmatch_roman_forms():
    assert _PAGE_NUM.fullmatch("— IV —")
    assert _PAGE_NUM.fullmatch("Page iv of x")
    assert _PAGE_NUM.fullmatch("第 IV 页")
    assert _PAGE_NUM.fullmatch("第 xii 页 共 xx 页")
    assert _PAGE_NUM.fullmatch("共 IV 页 第 II 页")
    assert _PAGE_NUM.fullmatch("IV / XII")
    assert _PAGE_NUM.fullmatch("— xix —")


def test_page_num_rejects_bare_or_invalid_roman():
    """宁漏勿误：裸 IV / 非法形态不认（DIV/MIX 这类英文词也不得命中）。"""
    assert not _PAGE_NUM.fullmatch("IV")
    assert not _PAGE_NUM.fullmatch("iv")
    assert not _PAGE_NUM.fullmatch("第 IV 章")
    assert not _PAGE_NUM.fullmatch("DIV")
    assert not _PAGE_NUM.fullmatch("MIX")
    assert not _PAGE_NUM.fullmatch("XID")


# ---------------------------------------------------------------- 混合页眉：key 层尾随页码抹平

def test_furniture_key_masks_trailing_page_fragment():
    # 语义与 _FURNITURE_PAGE_COL.sub("#") 一致：匹配段（含分隔符）替换为 #，
    # 分隔符前的空白保留。
    assert _furniture_key("第一章 引言 · 12") == "第一章 引言 #"
    # 页码逐页递增，键必须聚合相同（跨页重复检测的前提）
    assert _furniture_key("第一章 引言 · 12") == _furniture_key("第一章 引言 · 13")
    assert _furniture_key("摘要 | 3") == "摘要 #"
    assert _furniture_key("报告 — 12") == "报告 #"
    assert _furniture_key("附录 A・iv") == "附录 A#"


def test_furniture_key_tail_requires_separator():
    """宁漏勿误：无分隔符的尾随数字（真标题可能形态）不抹。"""
    assert _furniture_key("第 1 章 概述") == "第 1 章 概述"
    assert _furniture_key("版本 12") == "版本 12"


def test_furniture_key_tail_excludes_hyphen_separator():
    """日期/编号形态（连字符）不得被折成 #（连字符不入尾随分隔符类）。"""
    assert _furniture_key("版本 2026-10") == "版本 2026-10"
    assert _furniture_key("表 3-1 结果") == "表 3-1 结果"
    assert _furniture_key("ISO 8601-1") == "ISO 8601-1"


def test_roman_decorated_number_not_in_inline_mixed_case():
    """装饰式罗马页码走 inline（页脚单行），混合页眉的 · N 不走 inline。"""
    from doc2md.pipeline.s3_structure import _PAGE_NUM_INLINE
    assert _PAGE_NUM_INLINE.search("— IV —")
    assert not _PAGE_NUM_INLINE.search("第一章 引言 · 12")


# ---------------------------------------------------------------- 端到端（analyze）

def test_mixed_running_header_stripped_across_pages():
    """「章节名 · 页码」页眉逐页递增 → 三页全剔；正文零误伤。"""
    pages = []
    for i, (num, body) in enumerate(
            [("1", "首段正文。"), ("2", "次段正文。"), ("3", "末段正文。")]):
        pages.append(_page(
            _line(f"第一章 引言 · {num}", size=9.0, x0=200.0,
                  top=30.0, bottom=40.0),
            _line(body, size=12.0, x0=70.0, top=100.0, bottom=114.0),
            index=i,
        ))
    ir = analyze(pages)
    assert _texts(ir) == ["首段正文。", "次段正文。", "末段正文。"]


def test_roman_decorated_footer_stripped_on_single_page():
    ir = analyze([_page(
        _line("正文第一行。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
        _line("— IV —", size=9.0, x0=380.0, top=770.0, bottom=780.0),
    )])
    assert _texts(ir) == ["正文第一行。"]


def test_roman_running_header_stripped_across_pages():
    pages = []
    for i, num in enumerate(["I", "II", "III"]):
        pages.append(_page(
            _line(f"第 {num} 页", size=9.0, x0=200.0, top=30.0, bottom=40.0),
            _line(f"第{i + 1}页正文。", size=12.0, x0=70.0,
                  top=100.0, bottom=114.0),
            index=i,
        ))
    ir = analyze(pages)
    assert _texts(ir) == ["第1页正文。", "第2页正文。", "第3页正文。"]


def test_date_body_line_survives():
    """负例（宁漏勿误）：正文区含日期行不受尾随抹平影响。"""
    ir = analyze([_page(
        _line("发布日期：2026-10-22。", size=12.0, x0=70.0,
              top=100.0, bottom=114.0),
        _line("正文另起一行。", size=12.0, x0=70.0, top=130.0, bottom=144.0),
    )])
    assert _texts(ir) == ["发布日期：2026-10-22。", "正文另起一行。"]
