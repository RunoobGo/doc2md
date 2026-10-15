"""S4 渲染 —— IR → Markdown（FR-107 / FR-108 分级在 v2.4 落地）。

渲染分级（CH-416 / CH-417）：
- 外部链接（http/https/mailto）→ [text](url)
- 内部链接（PDF goto / DOCX 书签）→ plain（不产生锚点语法）
- bold/italic 默认开；strikethrough 默认关（preserve_marks_strike=false）

验收红线：默认配置下输出不含 `~~`，且不含 `](#` 或 `#page-` 类自造锚点。
"""
from __future__ import annotations

import re

from ..ir import IR, Block

_EXT_SCHEMES = ("http://", "https://", "mailto:", "ftp://")
_ANCHOR_MARK = ("](#", "#page-")


def is_external(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith(_EXT_SCHEMES)


def render(ir: IR, *, preserve_strike: bool = False, link_internal: str = "plain") -> str:
    parts: list[str] = []
    for b in ir.blocks:
        md = _render_block(b, preserve_strike=preserve_strike, link_internal=link_internal)
        if md:
            parts.append(md)
    text = "\n\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


def _render_block(b: Block, **opt) -> str:
    text = b.text or ""
    if b.type == "heading":
        lvl = min(6, max(1, b.level or 1))
        return "#" * lvl + " " + _inline(text, b, **opt)
    if b.type == "list_item":
        t = re.sub(r"^\s*[·•‣∙○●■□\-\*]\s+", "", text)
        return "- " + _inline(t, b, **opt)
    if b.type == "page_break":
        # CH-459：GFM 无分页符语法——结构信息留在 IR / 审阅页，Markdown 不落字节
        return ""
    if b.type == "code":
        return "```\n" + text + "\n```"
    if b.type == "quote":
        return "\n".join("> " + ln for ln in text.splitlines())
    if b.type == "table":
        return text            # 表格文本已在 S3 组装为 GFM
    return _inline(text, b, **opt)


def _inline(text: str, b: Block, *, preserve_strike: bool, link_internal: str) -> str:
    marks = b.marks or {}
    if marks.get("bold"):
        text = f"**{text}**"
    if marks.get("italic"):
        text = f"*{text}*"
    if marks.get("strikethrough") and preserve_strike:
        text = f"~~{text}~~"
    # CH-459：块级 links 至多渲染一条（行内跨度级链接不在 IR 模型内——
    # 多条并列时取首条可渲染的，其余留存在 IR/审阅面，不制造错标数据）
    for lk in b.links or []:
        url, label = lk.get("url", ""), lk.get("text") or text
        if is_external(url):
            return f"[{label}]({url})"
        if link_internal == "anchor":
            # CH-464：anchor 档**不再**产出 `](#dest)`——`assert_render_contract`
            # 明令禁止自造锚点（GFM 无自动标题锚点，产出的必然是死链），该分支
            # 原实现与自家红线互斥、形同虚设。正文与 plain 同形（纯文本），内部
            # 链接信息完整保留在 IR（`links[].dest`）与审阅页，由下游按需消费。
            return text
        # plain：内部链接保持纯文本，不产生锚点语法
    return text


def assert_render_contract(md: str) -> None:
    """默认配置输出的渲染红线（进 shard_two_phase / 渲染分级断言）。"""
    low = md
    for mark in _ANCHOR_MARK:
        if mark in low:
            raise AssertionError(f"render contract violated: found {mark!r}")
    if "~~" in low:
        raise AssertionError("render contract violated: strikethrough in default output")
