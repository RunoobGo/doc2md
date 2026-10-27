"""Two-column PDF reading order (CH-487 complex corpus)."""
from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.pipeline.s1_parse import read_pdf

pytestmark = [pytest.mark.gate, pytest.mark.milestone("DoD")]
SRC = Path(__file__).parent / "corpus" / "sources" / "pdf_10_two_column_6p.pdf"


def test_two_column_page_reads_left_column_before_right():
    lines = read_pdf(SRC)[0].lines
    texts = [ln.text for ln in lines]
    assert any("左栏用于验证阅读顺序与栏间分隔" in t for t in texts)
    assert not any("双中栏文排" in t for t in texts), "左右栏被逐行交错"
    left = next(i for i, ln in enumerate(lines)
                if "左栏用于验证阅读顺序" in ln.text and ln.x0 < 200)
    right = next(i for i, ln in enumerate(lines)
                 if "右栏用于验证阅读顺序" in ln.text and ln.x0 > 200)
    assert left < right, "右栏内容出现在左栏之前"


def test_single_column_long_paragraph_stays_whole():
    pages = read_pdf(Path(__file__).parent / "corpus" / "sources"
                     / "pdf_09_complex_long_8p.pdf")
    first = "\n".join(ln.text for ln in pages[0].lines)
    assert "这一段用于验证长段落折行" in first
    assert "双栏" not in first


def test_blank_index_matches_naive_band_filter():
    """OPT4-17：带查询索引与旧「逐次重扫」口径逐元素等价（含顺序与边界）。

    索引是纯性能重构（双栏路径 32.9×–323× 的重复扫描），口径一旦漂移，
    受影响的是行缘空白证据 → 两栏乘除号形态的空格保真。这里钉旧口径的
    三条语义：空白判定 `not text.strip()`、下界 `top-1.0` 含、上界
    `max(bottom, top+1.0)` 不含。
    """
    from doc2md.pipeline.s1_parse import _BlankIndex

    def naive(chars, top, bottom):
        hi = max(bottom, top + 1.0)
        return [c for c in chars
                if not (c.get("text") or "").strip()
                and top - 1.0 <= float(c.get("top", 0.0) or 0.0) < hi]

    chars = [
        {"text": "A", "top": 10.0},          # 非空白：任何带都不收
        {"text": " ", "top": 9.0},           # 恰在下界（含）
        {"text": "\t", "top": 10.0},
        {"text": " ", "top": 10.999},
        {"text": " ", "top": 11.0},          # 恰在上界（不含）
        {"text": " ", "top": 40.0},
    ]
    idx = _BlankIndex(chars)
    for top, bottom in ((10.0, 8.0), (10.0, 10.5), (40.0, 40.0), (0.0, 5.0)):
        want = naive(chars, top, bottom)
        assert idx.in_band(top, bottom) == want
        assert idx.in_band(top, bottom) == want, "索引查询不得改变自身状态"
