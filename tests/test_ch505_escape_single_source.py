"""CH-505 / OPT4-16：表格单元格转义单一来源（B-37）。

B-37 的形态：S1 与 S3 各有一套「源文本 → 安全文本」实现，同一字符按来源产出
不同结果——源文本 `A & B` 在 DOCX 表格里得 `A &amp; B`（S1 折实体），在 PDF
表格里得 `A & B`（S3 不折 `&`）。任一侧改动都动产物字节，且没有对账面。

修复把实现收口到 `doc2md/pipeline/escapes.py`，应用点保持「恰好一次」：
DOCX 单元格 S1 折实体后插结构 `<br>`（S3 传 `entities=False`），PDF 单元格
S3 折实体（`entities=True`）。本文件钉四件事：

① **等价性**：新单元格转义对旧串行替换**逐字节等价**（默认路径，含反斜杠
   先行、`|`、`<br>` 分段、补列）；行内转义与旧 translate 语义等价；
② **统一性**：`A & B < C > D` 在 DOCX 预折路径与 PDF 折叠路径产出相同；
③ **字面 `<br>`**：PDF 侧不再被误当结构换行（与 DOCX 已折实体的结果对齐）；
④ **单一来源**：实体折转与 translate 表只允许存在于 `escapes.py`；三个
   消费层不得自建（变异证明：把链式 replace 加回任一层，本项转红）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.pipeline import s4_render
from doc2md.pipeline.escapes import (
    CELL_ESCAPE_TABLE,
    INLINE_ESCAPE,
    escape_cell_markdown,
    escape_entities,
    escape_inline,
)
from doc2md.pipeline.s3_structure import _gfm_table

pytestmark = [pytest.mark.gate]

PIPELINE = Path(__file__).resolve().parents[1] / "doc2md" / "pipeline"

# 旧实现（OPT4-16 替换前）的逐字副本——等价性证明的参照物，不得按新实现改写。
_LEGACY_CELL_REPLACEMENTS = (
    ("\\", "\\\\"), ("|", "\\|"), ("*", "\\*"), ("_", "\\_"),
    ("[", "\\["), ("]", "\\]"), ("`", "\\`"), ("~", "\\~"), ("<", "&lt;"),
)


def _legacy_cell_md(c: str) -> str:
    parts = str(c).strip().replace("\n", "<br>").split("<br>")
    esc = []
    for part in parts:
        for a, b in _LEGACY_CELL_REPLACEMENTS:
            part = part.replace(a, b)
        esc.append(part)
    return "<br>".join(esc)


def _legacy_gfm_table(rows: list[list[str]]) -> str:
    ncols = max(len(r) for r in rows)
    padded = [(r + [""] * ncols)[:ncols] for r in rows]
    esc = [[_legacy_cell_md(c) for c in row] for row in padded]
    head, body = esc[0], esc[1:]
    sep = "| " + " | ".join(["---"] * ncols) + " |"
    lines = ["| " + " | ".join(head) + " |", sep]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


_LEGACY_S1_CHAIN = ("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")


def _legacy_s1_entities(text: str) -> str:
    for a, b in _LEGACY_S1_CHAIN:
        text = text.replace(a, b)
    return text


# ------------------------------------------------ ① 等价性（默认路径逐字节）
_LEGACY_CASES = [
    [["列一", "列|二"], ["a\nb", "  padded  "]],
    [["反斜杠\\|组合", "普通", "末列"], ["x", "y", "z"]],
    [["甲", "乙"], ["一段<br>二段", "丙"]],
    [["<script>", "a & b"], ["", "尾格"]],
    [["`code`", "*星*"], ["_下_", "~浪~"]],
]


@pytest.mark.parametrize("rows", _LEGACY_CASES,
                         ids=lambda r: f"{len(r)}x{len(r[0])}")
def test_default_cell_path_matches_legacy_byte_for_byte(rows):
    """默认（DOCX）路径与旧串行替换逐字节相同：本次是重构，不是行为变更。"""
    assert _gfm_table(rows) == _legacy_gfm_table(rows)


def test_escape_inline_matches_reference_and_is_reexported():
    """行内转义：translate 表与逐字符参考实现等价；S4 的名字指向同一函数。"""

    def ref(text: str) -> str:
        return "".join("\\" + ch if ch in INLINE_ESCAPE else ch
                       for ch in text)

    for s in ("", "\\`*_[]<~", "中文 and <b>x</b>", "a\tb\nc"):
        assert escape_inline(s) == ref(s)
    assert s4_render._escape_inline is escape_inline, "S4 自建了第二份实现"
    assert s4_render._INLINE_ESCAPE is INLINE_ESCAPE


def test_escape_entities_matches_legacy_s1_chain():
    """实体折转与 S1 旧链逐字节等价（`&` 先行，不产生二次转义）。"""
    for s in ("", "A & B", "<b>", "x > y", "&amp;", "a&b<c>d", "无特殊字符"):
        assert escape_entities(s) == _legacy_s1_entities(s)


# ------------------------------------------------ ② 统一性 / ③ 字面 <br>
def test_docx_prescape_and_pdf_fold_produce_same_cell():
    """同一源文本在两条路径上产出同一个单元格——B-37 的「分叉面」消失。"""
    src = "A & B < C > D"
    docx = _gfm_table([[escape_entities(src)]])          # S1 折实体后交给 S3
    pdf = _gfm_table([[src]], entities=True)             # S3 折实体（PDF 路径）
    assert docx == pdf
    assert "A &amp; B &lt; C &gt; D" in pdf


def test_pdf_literal_br_stays_literal():
    """PDF 侧字面 `<br>` 不再被当结构换行；DOCX 侧早由 S1 折实体，结果一致。"""
    literal = "一段<br>二段"
    assert "一段&lt;br&gt;二段" in _gfm_table([[literal]], entities=True)
    # DOCX 路径：S1 折实体后交给 S3，字面 <br> 同样保留为字面量
    assert "一段&lt;br&gt;二段" in _gfm_table([[escape_entities(literal)]])
    # 结构换行仍须保留（反向守卫：修复不得把真结构换行也折掉）
    assert "甲<br>乙" in _gfm_table([["甲\n乙"]], entities=True)


# ------------------------------------------------ ④ 单一来源（静态红线）
def test_escape_implementations_have_a_single_source():
    """实体折转 / translate 表只许在 escapes.py；消费层不得自建副本。"""
    owners = sorted(p.name for p in PIPELINE.glob("*.py")
                    if "&amp;" in p.read_text(encoding="utf-8"))
    assert owners == ["escapes.py"], f"实体折转出现第二处实现：{owners}"
    for name in ("s1_parse.py", "s3_structure.py", "s4_render.py"):
        text = (PIPELINE / name).read_text(encoding="utf-8")
        assert "str.maketrans" not in text, f"{name} 自建 translate 表"
        assert "&lt;" not in text, f"{name} 自建实体替换链"
    assert CELL_ESCAPE_TABLE is not None  # 表由 escapes 统一构造（导入面钉住）


def test_cell_markdown_escape_preserves_backslash_before_pipe():
    """D4 的「反斜杠先行」在 translate 单趟下保持：源文本 `\\|` → `\\\\\\|`。"""
    assert escape_cell_markdown("\\|") == "\\\\\\|"
    assert escape_cell_markdown("|") == "\\|"
    assert escape_cell_markdown("\\") == "\\\\"
    # 全表唯一 `<` 映射例外：它折实体而非反斜杠
    assert escape_cell_markdown("<") == "&lt;"
