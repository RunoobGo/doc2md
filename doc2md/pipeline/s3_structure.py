"""S3 结构分析 + S4 规则库（20 条，v2.3 口径）——本期落 R-03 / R-04 相关项。

R-03：标题判定（字号 + 粗体 + 编号形态）
R-04：编号连续性（用于分片缝合与层级校正）
"""
from __future__ import annotations

import re

from ..ir import IR, Block
from .s1_parse import RawPage

_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[、\.::]?\s*")
_CJK_HEAD = re.compile(r"^\s*(第[一二三四五六七八九十百千\d]+[章节条款项编部篇])")
_BULLET = re.compile(r"^\s*([·•‣∙○●■□\-\*])\s+")
# CH-430：只能「续句」不能「起句」的行首标点 —— 这类行必是上一段的延续，
# 即便中间被标题行或空行打断（PDF 折行 + 页眉/编号标题插入是常态），
# 也必须并回前一个段落块，否则产出「半句开头」块（G1 明令禁止）。
# 只认标点，不认小写字母/数字：拉丁正文的跨行由 _can_continue 处理，避免误并。
_CONT_HEAD = re.compile(r"^\s*([　，、。；：！？〉》」』】〕）”…—])")


def _continuation_led(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_CONT_HEAD.match(t)) and t[0] not in "（(【《「『"


def _join_back(blocks: list[Block], text: str, font: dict) -> bool:
    """把续句行并回最近的内容块；成功返回 True。"""
    for b in reversed(blocks):
        if b.type in ("paragraph", "list_item"):
            b.text = (b.text or "") + _join_line(b.text or "", text.strip())
            b.flags.setdefault("joined_across_break", True)
            if font and not b.font:
                b.font = dict(font)
            return True
    return False


def analyze(pages: list[RawPage], base_size: float | None = None) -> IR:
    """页级行 → 块序列（段落合并 / 标题判定 / 列表判定）。"""
    blocks: list[Block] = []
    if base_size is None:
        base_size = _modal_size(pages)
    seq = 0
    for page in pages:
        para_buf: list[str] = []
        para_font: dict = {}
        para_page = page.index
        for ln in page.lines:
            text = ln.text.rstrip()
            if not text.strip():
                _flush(para_buf, blocks, seq, para_font, para_page)
                seq += 1
                para_buf = []
                continue
            lvl = _heading_level(text, ln.size, base_size, ln.bold)
            if lvl:
                _flush(para_buf, blocks, seq, para_font, para_page)
                seq += 1
                para_buf = []
                seq = _append(Block(id=f"b{seq + 1}", type="heading", text=text.strip(),
                                    level=lvl, font=_font(ln), page=page.index), blocks, seq)
                continue
            if _BULLET.match(text):
                _flush(para_buf, blocks, seq, para_font, para_page)
                seq += 1
                para_buf = []
                seq = _append(Block(id=f"b{seq + 1}", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index), blocks, seq)
                continue
            # CH-430：续句标点起头 → 无条件并回上一个内容块（可跨标题/空行打断）
            if _continuation_led(text) and not para_buf and _join_back(blocks, text, _font(ln)):
                continue
            # 普通正文行：句子未结束则续接同段
            if para_buf and _can_continue(para_buf[-1]):
                para_buf.append(text.strip())
            elif para_buf:
                _flush(para_buf, blocks, seq, para_font, para_page)
                seq += 1
                para_buf = [text.strip()]
                para_font = _font(ln)
                para_page = page.index
            else:
                para_buf = [text.strip()]
                para_font = _font(ln)
                para_page = page.index
            seq = seq  # noop（保持编号语义清晰）
        _flush(para_buf, blocks, seq, para_font, para_page)
    ir = IR(doc_id="", source_key="", blocks=blocks, pages=len(pages))
    for i, b in enumerate(ir.blocks, start=1):
        b.id = f"b{i}"
    _fix_levels(ir)
    return ir


def _font(ln) -> dict:
    return {"family": "serif" if not ln.italic else "italic-serif",
            "size": round(ln.size, 1), "bold": bool(ln.bold)}


def _modal_size(pages: list[RawPage]) -> float:
    """正文字号 = 按**字符数**加权的众数。

    按行计数会被大号标题主导（每页 1~2 行标题字号 14~16，正文行多但单行字数少
    时仍可能反超）；字符加权后标题无法绑架正文口径。
    """
    from collections import Counter
    c: Counter = Counter()
    for p in pages:
        for ln in p.lines:
            if ln.size:
                c[round(ln.size, 0)] += max(1, len(ln.text.strip()))
    return c.most_common(1)[0][0] if c else 10.0


def _heading_level(text: str, size: float, base: float, bold: bool) -> int | None:
    """R-03：字号显著大于正文，或编号 + 短行 + 粗体。"""
    t = text.strip()
    if not t or len(t) > 80:
        return None
    numbered = bool(_NUM.match(t) or _CJK_HEAD.match(t))
    # 纯 CJK 无编号的短粗体大字行不判标题（PDF 合成样本每行字号偏大时易误判）
    if size and base and size >= base + 3.5 and numbered:
        return _level_from_number(t, size, base)
    if numbered and bold and size and size >= base + 0.8 and len(t) < 40:
        return _level_from_number(t, size, base)
    if numbered and len(t) < 30 and not t.endswith(("。", "！", "？", ".", "!", "?")):
        return _level_from_number(t, size or base, base)
    return None


def _level_from_number(t: str, size: float, base: float) -> int:
    m = _NUM.match(t)
    if m:
        return min(6, max(1, m.group(1).count(".") + 1))
    m2 = _CJK_HEAD.match(t)
    if m2:
        word = m2.group(1)
        order = {"章": 1, "编": 1, "部": 1, "篇": 1, "节": 2, "条": 3, "项": 4}
        for k, v in order.items():
            if word.endswith(k):
                return v
    return max(1, min(6, int(round((base + 3.5 - (size or base)) )) + 1))


def _can_continue(prev: str) -> bool:
    p = prev.rstrip()
    if not p:
        return False
    return not p.endswith(("。", "！", "？", "；", ".", "!", "?", ";", "：", ":"))


def _flush(buf: list[str], blocks: list[Block], seq: int, font: dict, page: int) -> None:
    """行缓冲 → 段落块：逐行按中西文规则缝合。"""
    if not buf:
        return
    text = buf[0]
    for nxt in buf[1:]:
        text = text + _join_line(text, nxt)
    text = text.strip()
    if text:
        blocks.append(Block(id=f"b{seq + 1}", type="paragraph", text=text,
                            font=font or {}, page=page))


def _join_line(a: str, b: str) -> str:
    """跨行拼接：中文直连，英文补空格，连字符断词合并。"""
    if a.endswith("-") and b[:1].islower():
        return a[:-1] + b
    last = a[-1] if a else ""
    first = b[0] if b else ""
    if _CJK_HEAD.match(b) or ord(last) > 0x2E80 or ord(first) > 0x2E80:
        return b
    return (" " + b) if (last.isalnum() and first.isalnum()) else b


def _append(b: Block, blocks: list[Block], seq: int) -> int:
    blocks.append(b)
    return seq


def _fix_levels(ir: IR) -> None:
    """R-04 编号连续性 → 层级校正：同前缀的编号必须同级。"""
    seen: dict[str, int] = {}
    for b in ir.blocks:
        if b.type != "heading":
            continue
        m = _NUM.match(b.text)
        if not m:
            continue
        key = m.group(1)
        prefix = key.rsplit(".", 1)[0] if "." in key else ""
        lvl = min(6, key.count(".") + 1)
        sibling = seen.get(prefix, lvl)
        b.level = sibling
        b.font.setdefault("rule", "R-04")
        if lvl != sibling:
            b.flags.setdefault("rule_notes", []).append("R-04:level_from_sibling")
        seen[prefix] = sibling
