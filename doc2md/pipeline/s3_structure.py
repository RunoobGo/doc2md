"""S3 结构分析 + S4 规则库（20 条，v2.3 口径）——本期落 R-03 / R-04 相关项。

R-03：标题判定（字号 + 粗体 + 编号形态）
R-04：编号连续性（用于分片缝合与层级校正）

CH-459（FR-104 收口）：`BLOCK_TYPES` 声明七类，本模块原只产三类。
本轮补齐 table / code / quote / page_break 四类的**保守检测**——
- table：S1 已按行内横向间隙聚出单元格（`Line.row`），本层按「连续行 + 列数对齐 +
  行首对齐 + 行尾非句末标点」成组，组装为 GFM（S4 原样透传）；
- code：围栏行（``` / ~~~）配对、等宽字体行连续段（`Line.mono`）、
  4 空格 / 制表符缩进行（Markdown 语义，仅空段起算）；
- quote：`>` 前缀行或整行 CJK 引号包裹，连续行并块；
- page_break：行内 form-feed（DOCX 分页符由 S1 合成该信号）。
全部检测遵循「宁漏勿误」：漏判只退化为段落（原行为），误判会污染正文。
另落 FR-107/108 的块级链接附着（`Line.link` → `Block.links`，每块至多一条）。
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

# CH-459：四类新块的检测式
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_QUOTE_PREFIX = re.compile(r"^\s{0,3}>\s?")
_QUOTE_OPEN = "「『“‘"
_QUOTE_CLOSE = "」』”’"
_SENT_END = ("。", "！", "？", "；", ".", "!", "?", ";", "：", ":")
# 表格行组约束：行首 x0 对齐容差（pt）与最少行数
_ROW_X0_TOL = 3.0
_ROW_MIN = 2


def _continuation_led(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_CONT_HEAD.match(t)) and t[0] not in "（(【《「『"


def _join_back(blocks: list[Block], text: str, font: dict) -> Block | None:
    """把续句行并回最近的内容块；成功返回被并入的块，失败返回 None。

    CH-459：遇到 quote / code / table / page_break 即止步——这些是结构块，
    续句穿过它们并回更前的段落会制造错位（原实现它们不存在，该分支不可达）。
    标题块照旧可穿过（CH-430：页眉/编号标题插在折行中间是常态）。
    """
    for b in reversed(blocks):
        if b.type in ("paragraph", "list_item"):
            b.text = _append_line(b.text or "", text.strip())
            b.flags.setdefault("joined_across_break", True)
            if font and not b.font:
                b.font = dict(font)
            return b
        if b.type == "heading":
            continue
        return None
    return None


def _is_quote_line(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _QUOTE_PREFIX.match(t):
        return True
    return len(t) >= 2 and t[0] in _QUOTE_OPEN and t[-1] in _QUOTE_CLOSE


def _quote_text(text: str) -> str:
    t = text.strip()
    if t.startswith(">"):
        return re.sub(r"^\s{0,3}>\s?", "", t)
    return t


def _gfm_table(rows: list[list[str]]) -> str:
    """行单元格 → GFM 表格文本（列数不齐补齐到最多列；单元格内 | 转义）。"""
    ncols = max(len(r) for r in rows)
    padded = [(r + [""] * ncols)[:ncols] for r in rows]
    esc = [[c.replace("|", "\\|").replace("\n", " ").strip() for c in row]
           for row in padded]
    head, body = esc[0], esc[1:]
    sep = "| " + " | ".join(["---"] * ncols) + " |"
    lines = ["| " + " | ".join(head) + " |", sep]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _attach_link(b: Block, ln) -> None:
    """CH-459：行级链接 → 块级 links（每块至多一条；S4 的 [text](url) 消费它）。

    内部链接（PDF GoTo / DOCX 书签）同样记录：`link_internal` 口径下 S4 默认
    不产生锚点语法（plain），`dest` 留给 anchor 模式与审阅面。
    """
    if ln.link:
        b.links = [{"url": ln.link, "text": (b.text or "").strip(),
                    "dest": ln.link_dest or ""}]
    elif ln.link_internal:
        b.links = [{"url": "", "text": (b.text or "").strip(), "dest": ln.link_dest or ""}]


def analyze(pages: list[RawPage], base_size: float | None = None) -> IR:
    """页级行 → 块序列（段落合并 / 标题判定 / 列表判定 / 表格代码引用分页）。

    CH-473（PIPE-9）：块 id 一律在文末按落块顺序统一重排（`b1..bN`），循环内
    不再预写 `id=f"b{seq+1}"`——原实现在 8 处循环分支里维护 `seq` 计数器，
    而 `flush_rows` 的「组不成表逐行退回段落」分支一次落多块却只递增一次 seq，
    同批退回行拿到同一个 id（重复 id）；且这些预写值随后又被文末重排整体
    覆盖，属纯死写。预写值没有任何读取方（`_join_back`/`_attach_link` 均不
    看 id），故循环内一律占位 `""`，唯一赋值点是收尾重排。
    """
    blocks: list[Block] = []
    if base_size is None:
        base_size = modal_size(pages)
    # CH-459：跨页存续的聚集状态
    code_buf: list[str] | None = None          # None = 不在代码块内
    code_mode = ""                             # fence（显式围栏）| indent | mono
    quote_buf: list[str] = []
    row_buf: list[list[str]] = []
    row_x0: list[float] = []
    row_lines: list = []                       # 行缓冲（组不成表时退回段落用）
    quote_page = 0
    row_page = 0
    code_page = 0                              # CH-464：代码块首行所在页（收尾落块用）
    # CH-469：段落缓冲提升为跨页存续状态（原在页循环内逐页重置，
    # 英文跨页句的第二段以小写起头被 G1 判为半句，段落也被切成两块）
    para_buf: list[str] = []
    para_lines: list = []                      # 与 para_buf 同步的 Line 行缓冲
    para_font: dict = {}
    para_page = 0

    def emit_code(page: int) -> None:
        nonlocal code_buf
        buf = list(code_buf or [])
        while buf and not buf[-1]:
            buf.pop()
        while buf and not buf[0]:
            buf.pop(0)
        if buf:
            blocks.append(Block(id="", type="code",
                                text="\n".join(buf), page=page))
        code_buf = None

    def flush_para() -> None:
        nonlocal para_buf, para_font, para_page, para_lines
        before = len(blocks)
        _flush(para_buf, blocks, para_font, para_page)
        if len(blocks) > before:              # 段缓冲非空且缝合后非空才落块
            for ln in para_lines:             # CH-459：取段内首条带链接行
                if ln.link or ln.link_internal:
                    _attach_link(blocks[-1], ln)
                    break
        para_buf = []
        para_lines = []

    def flush_quote() -> None:
        nonlocal quote_buf
        if quote_buf:
            blocks.append(Block(id="", type="quote",
                                text="\n".join(quote_buf), page=quote_page))
        quote_buf = []

    def flush_rows(as_table: bool) -> None:
        nonlocal row_buf, row_x0, row_lines
        if not row_lines:
            return
        if as_table and len(row_buf) >= _ROW_MIN:
            blocks.append(Block(id="", type="table",
                                text=_gfm_table(row_buf), page=row_page))
        else:                                   # 组不成表：逐行退回段落（漏判不误判）
            for i, ln in enumerate(row_lines):
                blocks.append(Block(id="", type="paragraph",
                                    text=ln.text.strip(), font=_font(ln),
                                    page=row_page))
                if i == 0 and (ln.link or ln.link_internal):
                    _attach_link(blocks[-1], ln)
        row_buf, row_x0, row_lines = [], [], []

    for page in pages:
        for ln in page.lines:
            # --- page_break（CH-459：form-feed 行，DOCX 分页符的信号形态）---
            # 必须先于 rstrip 判定：\x0c 是 Python 空白字符，rstrip 会把它剥掉，
            # 放到空行分支之后该分支永假（CH-459 收尾自查实测确认）。
            if "\x0c" in ln.text:
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                if code_buf is not None:
                    emit_code(page.index)
                blocks.append(Block(id="", type="page_break",
                                    text=ln.text.replace("\x0c", "").strip(),
                                    page=page.index))
                continue
            text = ln.text.rstrip()
            # --- 空行：代码块内保留（代码含空行），否则冲刷段/引用缓冲 ---
            # 行组缓冲不在此冲刷：跨页表格靠它在页界存续（页界不产生空行），
            # 且空行不会丢内容（单元格结构不受影响）；引用缓冲必须冲刷——
            # 否则两段独立引用被并成一块、段间空行丢失（GFM 下两个引用块
            # 与一个含空行的引用块渲染等价，按块界拆更利审阅与编辑）。
            if not text.strip():
                if code_buf is not None:
                    code_buf.append("")
                    continue
                flush_para()
                flush_quote()
                continue
            # --- 代码围栏（CH-459）---
            if _FENCE.match(text):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                if code_buf is None:
                    code_buf, code_mode = [], "fence"
                    code_page = page.index
                elif code_mode == "fence":
                    emit_code(page.index)
                else:                           # 缩进/等宽块内的围栏行是普通内容
                    code_buf.append(text)
                continue
            # --- 代码块累积中（CH-459：三种模式各自的关闭条件）---
            if code_buf is not None:
                if code_mode == "mono" and not ln.mono:
                    emit_code(page.index)       # 等宽段结束：落块后本行走常规分流
                elif code_mode == "indent" and not (
                        text.startswith("    ") or text.startswith("\t")):
                    emit_code(page.index)       # 缩进段结束
                else:
                    code_buf.append(re.sub(r"^(    |\t)", "", text)
                                    if code_mode == "indent" else text)
                    continue
            # --- 缩进代码（Markdown 语义：4 空格 / 制表符，仅空段起算）---
            if (text.startswith("    ") or text.startswith("\t")) and not para_buf:
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                code_buf, code_mode = [re.sub(r"^(    |\t)", "", text)], "indent"
                code_page = page.index
                continue
            # --- 标题（CH-459：DOCX Heading 样式提示优先于启发式）---
            lvl = _heading_level(text, ln.size, base_size, ln.bold,
                                 hint=ln.level_hint)
            if lvl:
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="heading", text=text.strip(),
                                    level=lvl, font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                continue
            # --- 列表（既有口径不变）---
            if _BULLET.match(text):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                continue
            # --- 表格行候选（CH-459：S1 已聚出单元格）---
            row_ok = bool(ln.row) and len(ln.row) >= 2 and (
                ln.row_explicit or not ln.row[-1].rstrip().endswith(_SENT_END))
            if row_ok:
                if not row_buf:
                    flush_para()
                    flush_quote()
                    row_page = page.index
                elif abs(ln.x0 - (sum(row_x0) / len(row_x0))) > _ROW_X0_TOL:
                    flush_rows(True)            # 行首跳变：切组，各自判定
                    row_page = page.index
                row_buf.append(list(ln.row))
                row_x0.append(ln.x0)
                row_lines.append(ln)
                continue
            if row_buf:
                flush_rows(True)
            # --- 引用块（CH-459：段间边界起算，段中引用行按保守口径留在段内）---
            if _is_quote_line(text) and not para_buf:
                if not quote_buf:
                    quote_page = page.index
                quote_buf.append(_quote_text(text))
                continue
            flush_quote()   # CH-464：非引用行到达 → 引用缓冲立即落块（原实现推迟到
                            # 下一个空行/结构行，quote 块会排进其后的段落之间，乱序）
            # --- 等宽字体连续段 → 代码块（CH-459）---
            if ln.mono and not para_buf:
                code_buf, code_mode = [text], "mono"
                code_page = page.index
                continue
            # CH-430：续句标点起头 → 无条件并回上一个内容块（可跨标题/空行打断）
            if _continuation_led(text) and not para_buf:
                back = _join_back(blocks, text, _font(ln))
                if back is not None:
                    if (ln.link or ln.link_internal) and not back.links:
                        _attach_link(back, ln)
                    continue
            # 普通正文行：句子未结束则续接同段
            if para_buf and _can_continue(para_buf[-1]):
                para_buf.append(text.strip())
                para_lines.append(ln)
            elif para_buf:
                flush_para()
                para_buf = [text.strip()]
                para_lines = [ln]
                para_font = _font(ln)
                para_page = page.index
            else:
                para_buf = [text.strip()]
                para_lines = [ln]
                para_font = _font(ln)
                para_page = page.index
    # 收尾：跨页状态落块
    flush_para()                               # CH-469：段缓冲跨页存续，统一在文末落块
    flush_quote()
    flush_rows(True)
    if code_buf is not None:
        emit_code(code_page)                   # CH-464：原硬编码第 1 页，审阅页页码错位
    ir = IR(doc_id="", source_key="", blocks=blocks, pages=len(pages))
    # CH-473（PIPE-9）：块 id 的唯一赋值点——按落块顺序整体重排。循环内的
    # 预写 id 是死写（无读取方）且「一次落多块」的分支会产生重复 id，已移除。
    for i, b in enumerate(ir.blocks, start=1):
        b.id = f"b{i}"
    _fix_levels(ir)
    return ir


def _font(ln) -> dict:
    return {"family": "serif" if not ln.italic else "italic-serif",
            "size": round(ln.size, 1), "bold": bool(ln.bold)}


def modal_size(pages: list[RawPage]) -> float:
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


def _heading_level(text: str, size: float, base: float, bold: bool,
                   hint: int | None = None) -> int | None:
    """R-03：字号显著大于正文，或编号 + 短行 + 粗体。

    CH-459：`hint` 为 DOCX 段落样式显式声明的层级（"Heading N"）——
    显式声明优先于任何启发式（原实现对无编号的 DOCX 标题全部漏判成段落）。
    """
    if hint:
        return hint
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


def _flush(buf: list[str], blocks: list[Block], font: dict, page: int) -> None:
    """行缓冲 → 段落块：逐行按中西文规则缝合。

    CH-473（PIPE-9）：不再收 `seq` 预写 id——唯一赋值点是 `analyze` 收尾重排。
    """
    if not buf:
        return
    text = buf[0]
    for nxt in buf[1:]:
        text = _append_line(text, nxt)
    text = text.strip()
    if text:
        blocks.append(Block(id="", type="paragraph", text=text,
                            font=font or {}, page=page))


def _append_line(a: str, b: str) -> str:
    """按跨行口径把 b 接到 a 后面（唯一的拼接入口，CH-469）。

    连字符断词（"under-" + "score" → "underscore"）需要剥掉 a 尾部连字符，
    这层语义集中在本函数处理；调用方不再自行 `a + _join_line(a, b)`，
    避免追加/替换两种口径混用产出重复文本。
    """
    frag = _join_line(a, b)
    if a.endswith("-") and b[:1].islower():
        return a[:-1] + frag
    return a + frag


def _join_line(a: str, b: str) -> str:
    """跨行拼接的**待追加片段**（只经 _append_line 消费，CH-469）。

    连字符断词分支只返回 b：整串 `a[:-1] + b` 是替换语义的写法，
    而两个调用方都是追加语义，直接返回整串会产出 "under-underscore"
    这类重复文本（与方案 §merged[-1]=prev[:-1]+ln 的意图不符，且原实现无测试覆盖）。
    """
    if a.endswith("-") and b[:1].islower():
        return b
    last = a[-1] if a else ""
    first = b[0] if b else ""
    if _CJK_HEAD.match(b) or ord(last) > 0x2E80 or ord(first) > 0x2E80:
        return b
    return (" " + b) if (last.isalnum() and first.isalnum()) else b


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
