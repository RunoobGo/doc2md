"""CH-482：代码块行首缩进按几何重建（S1 x0/字宽 → S3 4 空格一级）。

背景（CH-478 批次② 的待决策项）：pdfplumber 的 `extract_text_lines()` 把行首空白
从 `Line.text` 剥掉，`Line.x0` 仍带几何；DOCX 腿则在 `p.text.strip()` 处丢。后果是
Python/YAML 这类「缩进即语法」的代码在产物里全部顶格，等价门也看不到缩进漂移。

本文件钉住三件事：
1. **PDF 重建**：`codeindent_1p.pdf`（0/24/48pt 干净几何）与 `codeindent_multi_1p.pdf`
   （三级 + 抖动 + 偏差 + 远右）的真实产物，fence / mono 两种模式都按块内最小 x0
   量化出 4 空格一级；
2. **保守边界**：残差超过 ±0.25 级、级数超过 8 的行保持原样——抖动行（0.3 级）与
   远右栏（16.7 级）宁可不缩；+29pt 的偏差行（1.208 级）仍判 1 级，证明容差不是
   「非整数倍一律丢」；
3. **DOCX 与表格**：DOCX 等宽段的前导 tab / 空格由 S1 原样保留；表格行先判表格，
   列偏移不得被当成代码缩进。

模式边界：`indent` 模式（文本自带 4 空格 / tab 起块）在累积时已删去一级 Markdown
块标记，不再走几何重建，否则会把块容器缩进当内容缩进加回去（双重缩进）。
"""

from __future__ import annotations

import pytest

from doc2md.pipeline.convert import convert
from doc2md.pipeline.s1_parse import Line, RawPage, read_any
from doc2md.pipeline.s3_structure import _rebuild_code_indent, analyze

pytestmark = [pytest.mark.milestone("DoD")]

MULTI_FENCE = (
    "def outer():\n"
    "    if ready:\n"
    "        return 1\n"
    "    return 0\n"
    "jitter_probe = True\n"
    "    snap_probe = True\n"
    "right_column = 2"
)
MULTI_MONO = "for i in range(3):\n    print(i)\n        log(i)"


def _codes(path) -> list[str]:
    return [b.text for b in convert(path).ir.blocks if b.type == "code"]


def _lines(buf: list[str], xs: list[float], *, char_w: float = 6.0,
           size: float = 10.0) -> list[Line]:
    return [Line(text=t, size=size, mono=True, x0=x, char_w=char_w)
            for t, x in zip(buf, xs)]


# ---------------------------------------------------------------- PDF 端到端

@pytest.mark.gate
def test_pdf_fence_block_rebuilds_three_levels(golden_dir):
    """围栏块：0/24/48pt → 0/4/8 空格，二级缩进是 Python 语法结构。"""
    assert _codes(golden_dir / "codeindent_multi_1p.pdf")[0] == MULTI_FENCE
    assert _codes(golden_dir / "codeindent_1p.pdf")[0] == (
        "def indent_probe(x):\n    if x:\n        return 1\n    return 0")


@pytest.mark.gate
def test_pdf_mono_block_rebuilds_relative_indent(golden_dir):
    """无围栏等宽块走同一口径（mono 模式不是「不重建」的例外）。"""
    assert _codes(golden_dir / "codeindent_multi_1p.pdf")[1] == MULTI_MONO
    assert _codes(golden_dir / "codeindent_1p.pdf")[1] == (
        "for i in range(3):\n    print(i)")


@pytest.mark.gate
def test_unreliable_geometry_is_not_guessed(golden_dir):
    """抖动（0.3 级）与远右栏（16.7 级）不缩进；偏差 0.208 级仍判 1 级。

    恢复「把不可信 x0 一律吸成缩进」的实现会让 jitter / right_column 带前导空格；
    反过来把容差收到 0 会让 snap_probe 丢掉缩进——两侧都在这条断言里。
    """
    fence = _codes(golden_dir / "codeindent_multi_1p.pdf")[0].splitlines()
    assert fence[4] == "jitter_probe = True"          # 0.3 级，残差超容差
    assert fence[5] == "    snap_probe = True"        # 1.208 级，残差 0.208
    assert fence[6] == "right_column = 2"             # 16.7 级，超上限


@pytest.mark.gate
def test_s1_pdf_carries_measured_char_width(golden_dir):
    """字宽必须逐字符实测：x0 含行首空白，(x1-x0)/len(text) 会把缩进摊进字宽。"""
    lines = [ln for pg in read_any(golden_dir / "codeindent_multi_1p.pdf")
             for ln in pg.lines if ln.text.strip()]
    mono = [ln for ln in lines if ln.mono]
    assert mono and all(5.5 <= ln.char_w <= 6.5 for ln in mono), \
        f"Courier 10pt 字宽应约 6.0，实测 {sorted({round(ln.char_w, 2) for ln in mono})}"
    assert all(ln.char_w == 0.0 for ln in lines if not ln.mono), \
        "非等宽行不应带字宽（避免比例字体宽度污染量化）"


# ---------------------------------------------------------------- DOCX / 表格 / 模式边界

@pytest.mark.gate
def test_docx_mono_keeps_leading_tabs_and_spaces(golden_dir):
    """DOCX 无几何可重建：前导 tab / 空格必须在 S1 就以文本形态保住。"""
    src = [ln.text for pg in read_any(golden_dir / "docx" / "sample_code.docx")
           for ln in pg.lines if ln.mono]
    assert src == ["def g(x):", "\tif x:", "\t\treturn 1", "    return 0"]
    codes = _codes(golden_dir / "docx" / "sample_code.docx")
    assert codes == ["def g(x):\n\tif x:\n\t\treturn 1\n    return 0"]


@pytest.mark.gate
def test_table_cell_code_is_not_rebuilt_as_code_block(golden_dir):
    """表格优先：右列代码的列偏移（+200pt）不是缩进，不得进代码块分支。"""
    ir = convert(golden_dir / "codeindent_table_1p.pdf").ir
    tables = [b.text for b in ir.blocks if b.type == "table"]
    assert tables == [
        "| 名称 | 代码 |\n| --- | --- |\n| 循环 | print(i) |\n| 缩进 | log(i) |"
    ], f"表格结构漂移：{tables}"
    assert not [b for b in ir.blocks if b.type == "code"], \
        "表格行被等宽分支吞成代码块（列偏移会被量化成几十级缩进）"


@pytest.mark.gate
def test_mono_block_starting_with_indent_is_not_dedented():
    """等宽段首行自带 tab 时也必须走 mono（内容原样），不得被 indent 分支删一级。"""
    ir = analyze([RawPage(index=0, lines=[
        Line(text="前置段落。", size=10.5),
        Line(text=""),
        Line(text="\tdef h():", size=10, mono=True),
        Line(text="\t\treturn 1", size=10, mono=True),
    ])])
    assert [b.text for b in ir.blocks if b.type == "code"] == [
        "\tdef h():\n\t\treturn 1"]


@pytest.mark.gate
def test_indent_mode_text_is_not_rebuilt_twice():
    """indent 模式已删一级块标记；再按几何加回去就是双重缩进。"""
    ir = analyze([RawPage(index=0, lines=[
        Line(text="前置正文段落。", size=10.5),
        Line(text=""),
        Line(text="    if x:", size=10, x0=56.0, char_w=6.0),
        Line(text="        return 1", size=10, x0=80.0, char_w=6.0),
        Line(text="结束段落。", size=10.5),
    ])])
    codes = [b.text for b in ir.blocks if b.type == "code"]
    assert codes == ["if x:\n    return 1"]


# ---------------------------------------------------------------- 量化函数本体

@pytest.mark.parametrize("buf, xs, expected", [
    # 精确整数倍：0/24/48 → 0/4/8
    (["a", "b", "c"], [50.0, 74.0, 98.0], ["a", "    b", "        c"]),
    # 偏差 0.208 级（+29pt）仍吸附到 1 级；偏差 0.292 级（+31pt）不猜
    (["base", "snap", "skip"], [50.0, 79.0, 81.0], ["base", "    snap", "skip"]),
    # 远右栏超上限不缩进
    (["base", "right"], [50.0, 450.0], ["base", "right"]),
    # 空行与无几何行原样保留
    (["base", "", "tail"], [50.0, 0.0, 50.0], ["base", "", "tail"]),
])
def test_rebuild_quantization_rules(buf, xs, expected):
    assert _rebuild_code_indent(buf, _lines(buf, xs)) == expected


@pytest.mark.gate
def test_rebuild_falls_back_to_font_size_when_char_width_missing():
    """无实测字宽（老数据 / 非等宽源）退回 0.6×字号——Courier 的比例常数。"""
    lines = _lines(["a", "b"], [50.0, 74.0], char_w=0.0, size=10.0)
    assert _rebuild_code_indent(["a", "b"], lines) == ["a", "    b"]


@pytest.mark.gate
def test_rebuild_requires_aligned_buffer_and_two_geometric_lines():
    """缓冲错位 / 不足两行带几何 → 整块不动（不做跨行猜测）。"""
    buf = ["a", "b"]
    assert _rebuild_code_indent(buf, _lines(buf, [50.0])) == buf          # 长度不一致
    assert _rebuild_code_indent(buf, _lines(buf, [50.0, 0.0])) == buf     # 仅一行几何
