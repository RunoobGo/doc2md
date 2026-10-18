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
