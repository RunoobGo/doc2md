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
from .s3_structure import _ORDERED_ITEM

_EXT_SCHEMES = ("http://", "https://", "mailto:", "ftp://")
_ANCHOR_MARK = ("](#", "#page-")
_FENCE_RUN = re.compile(r"`+|~+")


def _code_fence(text: str) -> str:
    """CH-476（R7-PIPE-3）：CommonMark 围栏长度 = 内容里最长反引号/波浪线 run + 1。

    固定三反引号遇到代码正文里的 ``` 字面行（S3 把围栏行当内容累积，见
    `s3_structure` 的 mono/indent 分支）会提前闭栏，产出嵌套破损的 Markdown。
    无围栏字面的普通代码块 run=0 → 仍为三个反引号，现有语料字节不变。
    """
    longest = max((len(m.group(0)) for m in _FENCE_RUN.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


def is_external(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith(_EXT_SCHEMES)


def render(ir: IR, *, preserve_strike: bool = False, link_internal: str = "plain") -> str:
    parts: list[str] = []
    for b in ir.blocks:
        md = _render_block(b, preserve_strike=preserve_strike, link_internal=link_internal)
        if md:
            # CH-473（PIPE-11）：空行压缩只作用于**非代码块**。围栏结构由块类型
            # 唯一决定（只有 code 块包围栏，长度见 `_code_fence`），不再对整篇做正则切围栏——正文里
            # 出现的 ``` 字面行（文档内示例、引用内容）会让非贪婪配对错位：
            # 轻则把正文误判成代码免于压缩，重则把真实代码块暴露给 `\n{3,}`
            # 压缩（S3 emit_code 特意保留的代码空行被压扁，代码内容失真）。
            if b.type != "code":
                md = re.sub(r"\n{3,}", "\n\n", md)
            parts.append(md)
    return "\n\n".join(parts).strip() + "\n"


def _render_block(b: Block, **opt) -> str:
    text = b.text or ""
    if b.type == "heading":
        lvl = min(6, max(1, b.level or 1))
        return "#" * lvl + " " + _inline(text, b, **opt)
    if b.type == "list_item":
        indent = text[:len(text) - len(text.lstrip())]
        m = _ORDERED_ITEM.match(text)
        if m:
            # CH-478（R6-PIPE-1 收口）：有序项保留序号，分隔符统一为 GFM 的 `N. `
            # CH-483（R8-PIPE-4）：保留 DOCX 自动编号的层级缩进。
            return indent + m.group(1) + ". " + _inline(
                text[m.end():].strip(), b, **opt)
        # CH-485（R9-PIPE-3）：多级编号（2.1.）不是 GFM 有序列表语法；保留
        # 原编号并缩进，绝不落成 `- 2.1.` 的双标记畸形。
        m2 = re.match(r"^\s*(\d+(?:\.\d+)+)[.]?\s+(.*)$", text)
        if m2:
            return indent + m2.group(1) + ". " + _inline(
                m2.group(2).strip(), b, **opt)
        t = re.sub(r"^\s*[·•‣∙○●■□\-\*]\s+", "", text)
        return indent + "- " + _inline(t, b, **opt)
    if b.type == "page_break":
        # CH-459：GFM 无分页符语法——结构信息留在 IR / 审阅页，Markdown 不落字节
        return ""
    if b.type == "code":
        fence = _code_fence(text)
        return fence + "\n" + text + "\n" + fence
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
