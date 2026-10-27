"""源文本转义单一来源（OPT4-16 / B-37）。

同一语义过去分散在三处实现：
- S1 `_docx_cell_text`：`&` → `&amp;` → `<` → `&lt;` → `>` → `&gt;`；
- S3 `_gfm_table.cell_md`：Markdown 元字符 + 列分隔 `|` 反斜杠转义，`<` 折 `&lt;`；
- S4 `_escape_inline`：行内 8 个元字符的反斜杠转义（translate 表）。

字符集不同是**语境差异**（单元格多列分隔 `|`，且 `<br>` 是合法结构换行），
但实现分叉让同一字符按来源产生不同结果：源文本 `A & B` 在 DOCX 表格里得
`A &amp; B`，在 PDF 表格里得 `A & B`——任一侧改动都动产物字节且无人对账。

本模块把口径收成一份。应用点（同一段源文本**恰好转义一次**）：
- DOCX 表格单元格：S1 调 `escape_entities` 后插入结构 `<br>`；S3 传
  `entities=False`（已折过，`<br>` 是结构分隔符）；
- PDF 表格单元格：S3 传 `entities=True`，先折实体再按 `<br>` 分段——源文本里的
  字面 `<br>` 因此不会被当结构换行；
- 正文 / 链接文字：S4 调 `escape_inline`。
"""
from __future__ import annotations

# S4 行内字面量：8 个 Markdown 元字符，一律反斜杠转义。
INLINE_ESCAPE = set("\\`*_[]<~")
INLINE_ESCAPE_TABLE = str.maketrans({c: "\\" + c for c in INLINE_ESCAPE})
# S3 单元格：行内集合 + 列分隔 |，`<` 折实体（translate 单趟，反斜杠不会二次转义）。
CELL_ESCAPE_TABLE = str.maketrans({
    "\\": "\\\\", "|": "\\|", "*": "\\*", "_": "\\_",
    "[": "\\[", "]": "\\]", "`": "\\`", "~": "\\~", "<": "&lt;",
})


def escape_entities(text: str) -> str:
    """`& < >` 折 HTML 实体；`&` 先行，否则 `<` 折出的 `&` 会被二次转义。"""
    return (str(text).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def escape_inline(text: str) -> str:
    """行内字面量：源文本不得激活强调 / 代码 / 链接 / HTML 开标签。"""
    return str(text).translate(INLINE_ESCAPE_TABLE)


def escape_cell_markdown(text: str) -> str:
    """单元格的 Markdown 转义（实体部分由 `escape_entities` 负责）。

    `|` 必须转义（否则提前结束单元格）；反斜杠与元字符在同一 translate 表里
    单趟完成，等价于旧实现的「反斜杠先行」串行替换（D4）。
    """
    return str(text).translate(CELL_ESCAPE_TABLE)
