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
CH-476（R7-GAP-1）：FR-102 的修订痕迹块级留痕——S1 已按「接受后」口径把 `w:ins`
插入并入正文，本层把行级 `Line.has_revision` 透传到落块的 `flags.has_revision`，
只打旗标不改正文（PDF 行恒 False，产物不变）。
CH-476（R7-GAP-2）：脚注 / 尾注行（`Line.note_kind`）不参与续句/续段启发式，一律
自成段落块并带 `flags.note_kind`——注边界在 OOXML 里是显式 `w:p`，不能猜。
"""
from __future__ import annotations

import re

from ..ir import IR, Block
from .s1_parse import RawPage

_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[、\.::]?\s*")
_CJK_HEAD = re.compile(
    r"^\s*(第\s*[一二三四五六七八九十百千\d]+\s*[章节条款项编部篇])"
    # CH-485（R9-PIPE-2）：`第 1 章的内容…` / `第 1 章所述…` 是正文引用，
    # 不是标题；只对词头后**紧邻**的常见虚词做否定，不影响 `第 1 章 总则`。
    r"(?!的|所|中|里|内|是|为|规|要|内容)")
# 中文公文最常见的章节序号形态（“一、问题描述”）。原实现只认阿拉伯数字与
# “第X章/节”，导致标题与下一行正文粘成一段，或整段被当成正文。
_CJK_ORD = re.compile(r"^\s*[一二三四五六七八九十百千]+\s*、")
# 括号序号：“（一）”“（1）”。短行无句末标点时按子标题处理。
_PAREN_HEAD = re.compile(r"^\s*[（(]\s*(\d{1,3}|[一二三四五六七八九十百千]+)\s*[）)]")
# 字母列表项：“a.”“b）”。有序列表的拉丁变体，常见于技术文档。
_ALPHA_ITEM = re.compile(r"^\s*([a-zA-Z])\s*[.．、）)]\s*(?=\S)")
_DATE_LINE = re.compile(
    r"^\s*\d{4}\s*年(?:\s*(?:\d{1,2})?\s*月)?(?:\s*(?:\d{1,2})?\s*日)?\s*$")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
# 结构起始信号：即使上一行没有句末标点，也必须另起一段/一块。
_STRUCT_START = re.compile(
    r"^\s*(附件\s*\d+|附录\s*[A-Za-z一二三四五六七八九十\d]+|"
    r"[一二三四五六七八九十百千]+\s*、|（\s*[一二三四五六七八九十百千\d]+\s*）)")
# 图表题注：居中/远右排版，必须自成一块，不得粘进上一段正文。
_CAPTION = re.compile(r"^\s*(图|表|figure|table)\s*[\d一二三四五六七八九十]+"
                      r"(?:[\s.．\-－—]+\d+)*\s*\S", re.I)
_BULLET = re.compile(r"^\s*([·•‣∙○●■□\-\*])\s+")
# CH-478（R6-PIPE-1 收口，决策「落地有序列表分支」）：PDF 有序列表项的编号形态。
# `(?!\d)` 挡住小数（"3.5 节"），`\d{1,3}` 上限挡住年份起头的段落（"2019. 报告"），
# 分隔符集含全角 `．）`，空格可选（"1.安装依赖" 同样成立）。
# 正文首字用先行断言而非 `\S`：S4 复用本式取 `m.end()` 切序号，多吃一个字符会把
# 列表项内容削掉首字（"1. 安装依赖" → "1. 装依赖"）。
_ORDERED_ITEM = re.compile(r"^\s*(\d{1,3})[、.．)）]\s*(?!\d)(?=\S)")
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
# 冒号/分号在中文排版里常出现在折行末尾（“如下：”），句义并未结束；
# 只有硬句末才默认切段，软句末交给几何/结构信号判断。
_SOFT_END = ("；", ";", "：", ":")
_HARD_END = tuple(x for x in _SENT_END if x not in _SOFT_END)
# 表格行组约束：行首 x0 对齐容差（pt）与最少行数
_ROW_X0_TOL = 3.0
_ROW_MIN = 2

# PDF 页眉页脚带：页面顶部/底部 10%（footer 顶部按 88% 起算，覆盖页码基线略高的情况）。
_MARGIN_TOP = 0.10
_MARGIN_BOTTOM = 0.88
# 页码形态：装饰式 “— 3 —”、中文 “第 3 页 共 5 页”、英文 “Page 3 of 5”、
# “3 / 5”。只在页边距带内生效，避免误删正文里的数字。
_PAGE_NUM = re.compile(
    r"^(?:[—–-]\s*\d{1,4}\s*[—–-]"
    r"|第\s*\d{1,4}\s*页(?:\s*共\s*\d{1,4}\s*页)?"
    r"|共\s*\d{1,4}\s*页\s*第\s*\d{1,4}\s*页"
    r"|page\s*\d{1,4}(?:\s*of\s*\d{1,4})?"
    r"|\d{1,4}\s*/\s*\d{1,4})$", re.I)
_PAGE_NUM_INLINE = re.compile(
    r"(第\s*\d{1,4}\s*页\s*共\s*\d{1,4}\s*页|[—–-]\s*\d{1,4}\s*[—–-])")

# CH-482：代码块行首缩进重建（S1 几何 → Markdown 的 4 空格一级）
_CODE_INDENT_UNIT = 4       # Markdown 代码块一级缩进
_CODE_INDENT_MAX = 8        # 32 空格；超出视为多栏/远右噪声，不猜
_CODE_INDENT_SNAP = 0.25    # 量化残差容差（级）：与 4 空格整数倍不吻合的行保持原样


def _rebuild_code_indent(buf: list[str], lines: list) -> list[str]:
    """CH-482：按行几何重建代码块的行首缩进（仅 fence / mono 模式调用）。

    抽取层（pdfplumber `extract_text_lines`）把行首空白从 `Line.text` 剥掉，但
    `Line.x0` 仍带几何——代码缩进是内容不是装饰，不重建即丢失 Python/YAML 的
    语法结构。做法：块内最小 x0 为基准，按实测等宽字宽 `char_w` 把 x0 差量化成
    4 空格一级（`level = round(delta / (4*char_w))`）。

    保守边界（宁漏勿误）：
    - 残差超过 ±0.25 级 → 该行不缩进（2 空格缩进、扫描噪点、比例字体都落此）；
    - 级数 > `_CODE_INDENT_MAX` → 不缩进（多栏并排的右栏、远右注释）；
    - 少于两行带几何 / 无字宽可估 → 整块保持原样；
    - DOCX 行 x0=0（无几何），文本已在 S1 保留空白，不经本函数。
    `buf` 与 `lines` 由 S3 同步维护（含空行），长度不一致时按原样返回，不做猜测。
    """
    if len(buf) != len(lines):
        return buf
    pairs = [(i, t, ln) for i, (t, ln) in enumerate(zip(buf, lines))
             if t.strip() and getattr(ln, "x0", 0.0) > 0]
    if len(pairs) < 2:
        return buf
    widths = sorted(ln.char_w for _, _, ln in pairs
                    if getattr(ln, "char_w", 0.0) > 0)
    if widths:
        char_w = widths[len(widths) // 2]
    else:
        sizes = sorted(ln.size for _, _, ln in pairs if ln.size > 0)
        if not sizes:
            return buf
        char_w = 0.6 * sizes[len(sizes) // 2]
    if char_w <= 0:
        return buf
    base = min(ln.x0 for _, _, ln in pairs)
    unit = _CODE_INDENT_UNIT * char_w
    rebuilt: dict[int, str] = {}
    for i, t, ln in pairs:
        raw = (ln.x0 - base) / unit
        level = round(raw)
        if level <= 0 or level > _CODE_INDENT_MAX or abs(raw - level) > _CODE_INDENT_SNAP:
            continue
        rebuilt[i] = " " * (_CODE_INDENT_UNIT * level) + t
    if not rebuilt:
        return buf
    return [rebuilt.get(i, t) for i, t in enumerate(buf)]


def _continuation_led(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_CONT_HEAD.match(t)) and t[0] not in "（(【《「『"


def _is_ordered_item(text: str) -> bool:
    """CH-478（R6-PIPE-1 收口）：有序列表项判定，与标题第三回退共用反向信号。

    同字号（size == 正文众数）、非粗体、单级编号、短行、无句末标点——这五条既是
    `_heading_level` 让位的条件，也是列表项成立的充分形态；判为 `list_item` 后
    编号在 S4 保留（`1. `），不再退化成段落或假 H1。
    """
    t = (text or "").strip()
    if not _ORDERED_ITEM.match(t) or len(t) >= 30:
        return False
    return not t.endswith(("。", "！", "？", ".", "!", "?"))


def _is_alpha_item(text: str) -> bool:
    """字母编号列表项（“a. 保留…”）：短行、无硬句末。"""
    t = (text or "").strip()
    if not _ALPHA_ITEM.match(t) or len(t) >= 40:
        return False
    return not t.endswith(("。", "！", "？", ".", "!", "?"))


def _cn_number(text: str) -> int:
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(text, 1)


def _normalize_marker(marker: str) -> str:
    """DOCX 自动编号 → GFM 有序列表标记。

    “第3条/第十二条”这类中文条款编号不能退化成所有行都写 “1.”；转成
    阿拉伯序号即可保留 GFM 有序列表语义。
    """
    m = re.match(r"^\s*第\s*(\d+)\s*[条项款节章]\s*$", marker or "")
    if m:
        return f"{int(m.group(1))}."
    m = re.match(r"^\s*第\s*([一二三四五六七八九十]+)\s*[条项款节章]\s*$",
                 marker or "")
    if m:
        return f"{_cn_number(m.group(1))}."
    if marker and (marker[:1].isdigit() or _BULLET.match(marker + " ")):
        return marker
    return "1."


# --- PDF 页眉页脚 / 页码 ------------------------------------------------------

def _in_margin(ln, height: float) -> bool:
    """行是否落在页面上下 10% 噪声带；无几何（DOCX/合成页）恒 False。"""
    if height <= 0 or (ln.top <= 0 and ln.bottom <= 0):
        return False
    top = ln.top if ln.top > 0 else ln.bottom
    bottom = ln.bottom if ln.bottom > 0 else ln.top
    return bottom <= height * _MARGIN_TOP or top >= height * _MARGIN_BOTTOM


def _furniture_key(text: str) -> str:
    """页眉页脚归一化键：页码数字抹成 `#`，空白折叠，供跨页重复检测。"""
    return re.sub(r"\s+", " ", _PAGE_NUM_INLINE.sub("#", text or "")).strip()


def _strip_page_furniture(pages: list[RawPage], base_size: float) -> list[RawPage]:
    """剔除页码与跨页重复的页眉页脚（仅在页边距带内，宁漏勿误）。

    三类信号：
    1. 行本身是页码（装饰式 / “第 N 页 共 M 页” / Page N of M）；
    2. 行内含页码片段（页脚常把标题与页码排在同一行）；
    3. 同一边距行在 ≥2 页重复，且字号不高于正文（页眉/保密声明）。
    第一页居中的大标题只在单页出现，且字号通常更大 → 保留。
    """
    if not pages or not any(p.height > 0 for p in pages):
        return pages
    counts: dict[str, int] = {}
    body_sizes: dict[float, int] = {}
    for page in pages:
        seen: set[str] = set()
        for ln in page.lines:
            t = (ln.text or "").strip()
            if not t:
                continue
            if not _in_margin(ln, page.height) and ln.size > 0:
                key_size = round(ln.size, 1)
                body_sizes[key_size] = body_sizes.get(key_size, 0) + max(1, len(t))
            if not _in_margin(ln, page.height):
                continue
            key = _furniture_key(t)
            if key and key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    body_hint = (max(body_sizes.items(), key=lambda kv: kv[1])[0]
                 if body_sizes else base_size)
    out: list[RawPage] = []
    for page in pages:
        kept = []
        for ln in page.lines:
            t = (ln.text or "").strip()
            if t and _in_margin(ln, page.height):
                if _PAGE_NUM.fullmatch(t) or _PAGE_NUM_INLINE.search(t):
                    continue
                if counts.get(_furniture_key(t), 0) >= 2 and (
                        ln.size <= 0
                        or ln.size < body_hint - 0.5
                        or len(_furniture_key(t)) <= 24):
                    continue
            kept.append(ln)
        out.append(RawPage(index=page.index, lines=kept, height=page.height))
    return out


def _page_layout(page: RawPage) -> dict | None:
    """页级排版信号：正文左边距 + 是否存在首行缩进证据。

    中文 PDF 段落首行缩进 2 字符（实测 24~32pt），续行回到左边距。没有该证据的
    页面（DOCX、无缩进英文稿、纯列表）返回 has_indent=False，分段退回标点口径。
    """
    if page.height <= 0:
        return None
    xs = [ln.x0 for ln in page.lines
          if (ln.text or "").strip() and ln.x0 > 0 and not ln.row and not ln.mono]
    if not xs:
        return None
    base = min(xs)
    indents = [x for x in xs if 6.0 <= x - base <= 80.0]
    return {"base": base,
            "has_indent": len(indents) >= 2 or bool(indents and len(xs) >= 3)}


def _is_indented(ln, layout: dict | None) -> bool:
    if not layout:
        return False
    return 6.0 <= ln.x0 - layout["base"] <= 80.0


def _ends_sentence(text: str) -> bool:
    p = (text or "").rstrip()
    return bool(p) and p.endswith(_HARD_END)


def _continues_paragraph(prev: str, ln, layout: dict | None) -> bool:
    """几何感知的续段判断（替代只看句末标点的单信号）。

    优先级：续句标点 > 首行缩进（新段）> 未硬结束（续段）> 有缩进证据时的
    无缩进折行（续段）。无几何页面保持旧口径（硬句末即切段）。
    """
    p = (prev or "").rstrip()
    if not p:
        return False
    if getattr(ln, "explicit_para", False):
        return False
    t = (ln.text or "").strip()
    if _continuation_led(t):
        return True
    # 居中/远右的图表题注不是段落续行；即使上一行没有句号，也另起一块。
    # 只对题注形态生效——未识别成表格的远右数据行不能被进一步拆碎。
    if _CAPTION.match(t) and (not layout or ln.x0 - layout["base"] > 40.0):
        return False
    if layout and layout["has_indent"] and _is_indented(ln, layout):
        return False
    if not _ends_sentence(p):
        return True
    return bool(layout and layout["has_indent"])


def _join_list_continuation(blocks: list[Block], text: str, ln,
                            layout: dict | None) -> bool:
    """列表项折行续文并回列表项本身（原实现落入新段落，产生半句块）。"""
    if getattr(ln, "explicit_para", False):
        return False
    if _STRUCT_START.match(text) or _BULLET.match(text) or _is_ordered_item(text) or _is_alpha_item(text):
        return False
    if layout and layout["has_indent"] and _is_indented(ln, layout):
        return False
    for b in reversed(blocks):
        if b.type == "list_item":
            prev = b.text or ""
            # 短列表项（“3. 启动服务”）语义已完整，下一行是独立段落；
            # 只有折行迹象（长行/软标点收尾）才并回。
            if (len(prev.rstrip()) < 20
                    and not prev.rstrip().endswith(("：", ":", "；", ";", "，", ","))):
                return False
            if _can_continue(prev):
                b.text = _append_line(prev, text.strip())
                b.flags.setdefault("joined_across_break", True)
                return True
            return False
        if b.type == "heading":
            continue
        return False
    return False


def _join_heading_continuation(blocks: list[Block], text: str, ln,
                               layout: dict | None) -> bool:
    """标题折行续文并回标题（“一、合肥…相关” + “工程”）。

    只在有首行缩进证据的页面启用，且续行必须回到左边距、短、非结构起始；
    正文首行通常带缩进，不会被吸进标题。
    """
    if not (layout and layout["has_indent"]):
        return False
    if getattr(ln, "explicit_para", False):
        return False
    if _is_indented(ln, layout):
        return False
    if _STRUCT_START.match(text) or _BULLET.match(text) or _is_ordered_item(text) or _is_alpha_item(text):
        return False
    if "：" in text or ":" in text:
        # “副标题：…”/“摘要：…”是新的块，不是标题折行。
        return False
    if len(text.strip()) > 20:
        return False
    for b in reversed(blocks):
        if b.type == "heading":
            if _can_continue(b.text or ""):
                b.text = _append_line(b.text or "", text.strip())
                return True
            return False
        if b.type in ("paragraph", "list_item"):
            return False
        return False
    return False


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
    # CH-485（R9-PIPE-7）：块级 links 无字符偏移；只有链接行完整覆盖当前块
    # 时才附着，否则多行段落会被整段包进 [text](url)，制造错误链接范围。
    if (b.text or "").strip() != (ln.text or "").strip():
        return
    if ln.link:
        b.links = [{"url": ln.link, "text": (b.text or "").strip(),
                    "dest": ln.link_dest or ""}]
    elif ln.link_internal:
        b.links = [{"url": "", "text": (b.text or "").strip(), "dest": ln.link_dest or ""}]


def _mark_provenance(b: Block, lines, *, merge: bool = False) -> None:
    """CH-476（R7-GAP-1 / R7-GAP-2，决策 D4/D7）：来源行上的 DOCX 特征 → 块级留痕。

    - `has_revision`：正文已由 S1 按「接受后」口径提取（`w:ins` 并入、`w:del` 不入），
      这里的 flag 只服务审阅定位——让人看出「哪些块来自修订痕迹」，不再改动正文；
    - `note_kind`：该块来自脚注 / 尾注（S1 把注文追加到文档末尾，flag 让审阅页区分
      「尾部这些段落是注，不是正文」）。
    PDF 行两个信号恒为空，故 PDF 语料产物不变。
    """
    rows = list(lines)
    if any(getattr(ln, "explicit_para", False) for ln in rows):
        # DOCX `w:p` 是显式段落边界；模型不得跨段 merge。
        b.provenance.setdefault("explicit_para", True)
    if any(ln.has_revision for ln in rows):
        b.flags.setdefault("has_revision", True)
    kinds = [ln.note_kind for ln in rows if ln.note_kind]
    if kinds:
        b.flags.setdefault("note_kind", kinds[0])
    # CH-483（R8-PIPE-7）：块级 marks 只在“所有非空来源行都统一带格式”时写入。
    # 混合粗体的段落保持无 marks，避免把局部强调错误扩散成整段 `**...**`。
    if b.type in ("paragraph", "list_item"):
        nonempty = [ln for ln in rows if (ln.text or "").strip()]
        if nonempty:
            # CH-485（R9-PIPE-5）：AND 语义。新建块时全量判定；续行/并回时
            # 只清不置——新行统一不代表已有行也统一，绝不能把局部格式扩散。
            for key, attr in (("bold", "uniform_bold"),
                              ("italic", "uniform_italic"),
                              ("strikethrough", "uniform_strike")):
                ok = all(getattr(ln, attr) for ln in nonempty)
                if not ok:
                    b.marks.pop(key, None)
                elif not merge:
                    b.marks.setdefault(key, True)


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
    # 页眉页脚/页码先于分段剔除：否则页脚会把跨页段落切断，页码也会被
    # 当作普通短行进入正文。字号基准取原始页（正文仍占字符多数）。
    pages = _strip_page_furniture(pages, base_size)
    # CH-459：跨页存续的聚集状态
    code_buf: list[str] | None = None          # None = 不在代码块内
    code_mode = ""                             # fence（显式围栏）| indent | mono
    code_lines: list = []                      # CH-476（R7-GAP-1）：与 code_buf 同步的 Line
    quote_buf: list[str] = []
    quote_lines: list = []                     # CH-476：与 quote_buf 同步的 Line
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
        nonlocal code_buf, code_lines
        buf = list(code_buf or [])
        if code_mode in ("fence", "mono"):
            # CH-482：indent 模式在累积时已删去一级（Markdown 块标记），再按几何
            # 重建会把块容器的缩进当成内容缩进又加回来（双重缩进）。
            buf = _rebuild_code_indent(buf, code_lines)
        while buf and not buf[-1]:
            buf.pop()
        while buf and not buf[0]:
            buf.pop(0)
        if buf:
            blocks.append(Block(id="", type="code",
                                text="\n".join(buf), page=page))
            _mark_provenance(blocks[-1], code_lines)
        code_buf = None
        code_lines = []

    def flush_para() -> None:
        nonlocal para_buf, para_font, para_page, para_lines
        before = len(blocks)
        _flush(para_buf, blocks, para_font, para_page)
        if len(blocks) > before:              # 段缓冲非空且缝合后非空才落块
            for ln in para_lines:             # CH-459：取段内首条带链接行
                if ln.link or ln.link_internal:
                    _attach_link(blocks[-1], ln)
                    break
            _mark_provenance(blocks[-1], para_lines)   # CH-476（R7-GAP-1）
        para_buf = []
        para_lines = []

    def flush_quote() -> None:
        nonlocal quote_buf, quote_lines
        if quote_buf:
            blocks.append(Block(id="", type="quote",
                                text="\n".join(quote_buf), page=quote_page))
            _mark_provenance(blocks[-1], quote_lines)
        quote_buf = []
        quote_lines = []

    def flush_rows(as_table: bool) -> None:
        nonlocal row_buf, row_x0, row_lines
        if not row_lines:
            return
        # CH-489：DOCX 的 `w:tbl` 是显式表格对象，单行表也必须是表格；
        # PDF 的单行候选仍退回段落（无显式对象时宁可漏判，不制造假表）。
        one_explicit_row = bool(row_lines[0].row_explicit) and len(row_buf) == 1
        if as_table and (len(row_buf) >= _ROW_MIN or one_explicit_row):
            blocks.append(Block(id="", type="table",
                                text=_gfm_table(row_buf), page=row_page))
            _mark_provenance(blocks[-1], row_lines)     # CH-476（R7-GAP-1）
        else:                                   # 组不成表：逐行退回段落（漏判不误判）
            for i, ln in enumerate(row_lines):
                blocks.append(Block(id="", type="paragraph",
                                    text=ln.text.strip(), font=_font(ln),
                                    page=row_page))
                if i == 0 and (ln.link or ln.link_internal):
                    _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
        row_buf, row_x0, row_lines = [], [], []

    for page in pages:
        layout = _page_layout(page)
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
                    code_lines.append(ln)
                    continue
                flush_para()
                flush_quote()
                # 空行是表格边界：DOCX 连续 w:tbl 与 PDF 同页两表都靠它/或
                # table_start 切开；跨页表格在页界没有空行，不受影响。
                # 但表格自己的空行（ln.row 非 None，单元格全空）不得触发切分。
                if ln.row is None:
                    flush_rows(True)
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
                    code_lines.append(ln)
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
                    code_lines.append(ln)
                    continue
            # --- 缩进代码（Markdown 语义：4 空格 / 制表符，仅空段起算）---
            # CH-482：等宽行不走本分支——DOCX 等宽段的前导空白是内容（S1 已保留），
            # 交给 mono 分支原样累积；indent 分支会删掉一级，等于改写代码内容。
            if (text.startswith("    ") or text.startswith("\t")) \
                    and not ln.mono and not para_buf:
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                code_buf, code_mode = [re.sub(r"^(    |\t)", "", text)], "indent"
                code_lines = [ln]
                code_page = page.index
                continue
            # --- 结构起始（附件/附录/中文序号）：上一行无句末标点也另起一段 ---
            # 放在代码块处理之后：代码内容里的“附件 1”不得被当成结构行。
            if para_buf and _STRUCT_START.match(text):
                flush_para()
            # --- 标题（CH-459：DOCX Heading 样式提示优先于启发式）---
            # 自动编号是显式列表信号：不让「第一项」这类文本再被 CJK 章标记
            # 启发式抢成标题（Heading 样式在 S1 已跳过编号解析，hint 仍可优先）。
            # CH-485（R9-PIPE-4）：表格行先走表格分组，绝不能被 heading 分支
            # 当成标题（首列“1.1 范围”会触发多级编号信号）。
            if ln.row:
                lvl = None
            elif ln.level_hint is not None:
                lvl = _heading_level(text, ln.size, base_size, ln.bold,
                                     hint=ln.level_hint)
            else:
                lvl = None if ln.list_marker else _heading_level(
                    text, ln.size, base_size, ln.bold)
            if lvl:
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                # CH-485（R9-PIPE-3）：Heading 的自动编号在 S1 已解析出来，
                # 这里并入标题文本，避免“1. 方法”退化成“方法”。
                head_text = (f"{ln.list_marker} {text.strip()}"
                             if ln.list_marker else text.strip())
                prev = blocks[-1] if blocks else None
                if (prev is not None and prev.type == "heading"
                        and prev.page == page.index
                        and abs(float(prev.font.get("size", 0.0)) - float(ln.size or 0.0)) < 0.6
                        and len(prev.text or "") >= 12
                        and not (prev.text or "").rstrip().endswith(_HARD_END)
                        and not _STRUCT_START.match(text)
                        and not _NUM.match(text) and not _CJK_HEAD.match(text)
                        and not _CJK_ORD.match(text) and not _PAREN_HEAD.match(text)):
                    # 大字号标题在 PDF 折行后被判成两个 heading，拼回一个。
                    prev.text = _append_line(prev.text or "", head_text)
                    _mark_provenance(prev, (ln,), merge=True)
                    continue
                blocks.append(Block(id="", type="heading", text=head_text,
                                    level=lvl, font=_font(ln), page=page.index))
                if ln.level_hint:
                    # CH-476（R7-PIPE-4）：显式样式层级留痕，`_fix_levels` 依此不改写
                    blocks[-1].flags["level_from_hint"] = True
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- DOCX 自动编号（CH-483 / R8-PIPE-4）---
            if ln.list_marker:
                flush_para()
                flush_quote()
                flush_rows(True)
                marker = _normalize_marker(ln.list_marker)
                indent = "  " * max(0, ln.list_level)
                blocks.append(Block(id="", type="list_item",
                                    text=f"{indent}{marker} {text.strip()}",
                                    font=_font(ln), page=page.index))
                blocks[-1].flags["docx_numbering"] = {
                    "marker": ln.list_marker, "level": ln.list_level}
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 列表（既有口径不变）---
            if _BULLET.match(text):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 有序列表（CH-478 / R6-PIPE-1 收口：同字号单级编号行的归宿）---
            # 表格行优先于本分支：S1 已按横向间隙聚出 ≥2 单元格时，行首的 "1、"
            # 是单元格内容而非列表标记（宁漏勿误）。
            if _is_ordered_item(text) and not (ln.row and len(ln.row) >= 2):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 字母编号列表（“a. …”）：与数字列表同口径 ---
            if _is_alpha_item(text) and not (ln.row and len(ln.row) >= 2):
                flush_para()
                flush_quote()
                flush_rows(True)
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 表格行候选（CH-459：S1 已聚出单元格）---
            # CH-489：DOCX 单列表格（w:tbl 只有 gridCol=1）也是显式表格；
            # PDF 仍要求 ≥2 个几何聚类单元格，避免把单列正文误判成表。
            min_cells = 1 if ln.row_explicit else 2
            row_ok = bool(ln.row) and len(ln.row) >= min_cells and (
                ln.row_explicit or not ln.row[-1].rstrip().endswith(_SENT_END))
            if row_ok:
                if not row_buf:
                    flush_para()
                    flush_quote()
                    row_page = page.index
                elif ln.table_start:
                    flush_rows(True)            # 新 DOCX 表格：即使首行 x0 相同也要切开
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
                quote_lines.append(ln)
                continue
            flush_quote()   # CH-464：非引用行到达 → 引用缓冲立即落块（原实现推迟到
                            # 下一个空行/结构行，quote 块会排进其后的段落之间，乱序）
            # --- 等宽字体连续段 → 代码块（CH-459）---
            if ln.mono and not para_buf:
                code_buf, code_mode = [text], "mono"
                code_lines = [ln]
                code_page = page.index
                continue
            # --- 脚注 / 尾注行（CH-476 / R7-GAP-2，决策 D7）：自成一块 ---
            # DOCX 的注边界是显式 `w:p`，不能被 CH-430 的续句启发式并进前一个正文块
            # （末句无标点时真会发生：注文被当作正文的续行 = 内容与来源双错）。
            if ln.note_kind:
                flush_para()
                blocks.append(Block(id="", type="paragraph", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # CH-485（R9-PIPE-7）：块级链接无法表达行内范围；完整覆盖的链接行
            # 独立成块，既保留可点击 URL，又不把整段包进 [text](url)。
            if ln.link or ln.link_internal:
                flush_para()
                flush_quote()
                flush_rows(True)
                blocks.append(Block(id="", type="paragraph", text=text.strip(),
                                    font=_font(ln), page=page.index))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # CH-430：续句标点起头 → 无条件并回上一个内容块（可跨标题/空行打断）
            if _continuation_led(text) and not para_buf and not ln.explicit_para:
                back = _join_back(blocks, text, _font(ln))
                if back is not None:
                    if (ln.link or ln.link_internal) and not back.links:
                        _attach_link(back, ln)
                    _mark_provenance(back, (ln,), merge=True)
                    continue
            # 普通正文行：几何缩进优先，其次软/硬句末与续句标点
            if para_buf and _continues_paragraph(para_buf[-1], ln, layout):
                para_buf.append(text.strip())
                para_lines.append(ln)
            elif para_buf:
                flush_para()
                para_buf = [text.strip()]
                para_lines = [ln]
                para_font = _font(ln)
                para_page = page.index
            elif _join_heading_continuation(blocks, text, ln, layout):
                continue
            elif _join_list_continuation(blocks, text, ln, layout):
                continue
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

    CH-492（P-1）：优先归并 S1 单趟累计的 `RawPage.size_hist`（O(页数×键数)
    归并，省掉全文档第二次逐行遍历）；无直方图的页（手工构造 / 直接调用方）
    回退原逐行扫描。两条路径的键、计数与**插入序**完全一致——
    `Counter.most_common(1)` 的平票判定取插入序最前者，归并序 = 各字号在
    文档行序中的首次出现序 = 单遍序——故 `base_size` 逐字节不变
    （base_size 是「分片≡单片」判据根，见 convert.py CH-471/PIPE-1）。
    """
    from collections import Counter
    c: Counter = Counter()
    for p in pages:
        if p.size_hist:
            # CH-492（P-1）：必须是**累加**归并——`Counter.update` 按键相加，
            # 且新键按首次出现序插入（平票判定的前提）。切勿改成
            # `dict.update`：那是覆盖语义，后页计数会冲掉前页（本规格
            # 实测：3000 例中 1122 例因此与单遍结果不符）。
            c.update(p.size_hist)
        else:
            for ln in p.lines:
                if ln.size:
                    # CH-485（R9-PIPE-2）：10.5pt 被 round(...,0) 抬成 10.0 后，
                    # 第三回退的“字号>正文”会把所有单级编号短行误判成标题。
                    c[round(ln.size, 1)] += max(1, len(ln.text.strip()))
    return c.most_common(1)[0][0] if c else 10.0


def _heading_level(text: str, size: float, base: float, bold: bool,
                   hint: int | None = None) -> int | None:
    """R-03：字号显著大于正文，或编号 + 短行 + 粗体。

    CH-459：`hint` 为 DOCX 段落样式显式声明的层级（"Heading N"）——
    显式声明优先于任何启发式（原实现对无编号的 DOCX 标题全部漏判成段落）。
    CH-476（R7-PIPE-4）：hint 命中时调用方打 `flags.level_from_hint`，
    `_fix_levels` 据该标记跳过——「优先」口径从此贯穿到收尾的层级校正。
    """
    if hint:
        return hint
    t = text.strip()
    if not t or len(t) > 80:
        return None
    if _DATE_LINE.match(t):
        return None
    # 中文序号只认“短标题”形态：长句/带句末标点的是正文，不是标题。
    if _CJK_ORD.match(t) and (len(t) > 30 or t.endswith(_SENT_END)):
        return None
    if _PAREN_HEAD.match(t) and (len(t) > 40 or t.endswith(_SENT_END)):
        return None
    numbered = bool(_NUM.match(t) or _CJK_HEAD.match(t) or _CJK_ORD.match(t)
                    or _PAREN_HEAD.match(t))
    # 纯 CJK 无编号的短粗体大字行不判标题（PDF 合成样本每行字号偏大时易误判）
    if size and base and size >= base + 3.5 and numbered:
        return _level_from_number(t, size, base)
    if numbered and bold and size and size >= base + 0.8 and len(t) < 40:
        return _level_from_number(t, size, base)
    if numbered and len(t) < 30 and not t.endswith(("。", "！", "？", ".", "!", "?")):
        # R6-PIPE-1（CH-475，决策 D4）：第三回退原无字号/加粗门——「单级编号 + 短行 +
        # 无句末标点」正是有序列表项（"1. 安装依赖"）的典型形态，会被误判为 H1，污染
        # 正文结构与 TOC。纯单级编号不构成标题充分条件：仅当存在标题信号时才走编号→层级——
        # 字号>正文 / 粗体 / 多级编号（"1.1"，列表极少用）/ CJK 章節目条（永非列表项）。
        # 否则返回 None，留给后续段落处理（消除假阳性 H1，胜过罕见漏判一个同字号标题）。
        m_num = _NUM.match(t)
        multi_level = bool(m_num and "." in m_num.group(1))
        cjk_head = bool(_CJK_HEAD.match(t) or _CJK_ORD.match(t))
        paren_head = bool(_PAREN_HEAD.match(t))
        # CH-485（R9-PIPE-2）：0.5pt 容差吸收字体测量的细微差异。
        if (size or base) > base + 0.5 or bold or multi_level or cjk_head or paren_head:
            return _level_from_number(t, size or base, base)
    # 真实 Word 报告常有无编号的大字号章节标题（PDF 无 Heading 样式可依赖）。
    # 只认「短行 + 字号显著大于正文」：+6pt 阈值高于现有 golden 的 +5.5pt，
    # 避免把 small_3p 这类页标题误升为标题；+10pt 以上视为一级。
    if (not numbered and size and base and size >= base + 6.0
            and len(t) <= 60
            and not t.endswith(("。", "！", "？", ".", "!", "?"))):
        return 1 if size >= base + 10.0 else 2
    return None


def _level_from_number(t: str, size: float, base: float) -> int:
    m = _NUM.match(t)
    if m:
        return min(6, max(1, m.group(1).count(".") + 1))
    if _CJK_ORD.match(t):
        return 1
    m3 = _PAREN_HEAD.match(t)
    if m3:
        return 3 if m3.group(1).isdigit() else 2
    m2 = _CJK_HEAD.match(t)
    if m2:
        word = m2.group(1)
        order = {"章": 1, "编": 1, "部": 1, "篇": 1, "节": 2, "条": 3, "项": 4}
        for k, v in order.items():
            if word.endswith(k):
                return v
    return max(1, min(6, int(round((base + 3.5 - (size or base)) )) + 1))


def _can_continue(prev: str) -> bool:
    p = (prev or "").rstrip()
    if not p:
        return False
    return not _ends_sentence(p)


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
    if last.isdigit() and first.isdigit():
        # 折行把同一个数字切开（"2" + "026"）时不得插空格。
        return b
    if _CJK_HEAD.match(b) or ord(last) > 0x2E80 or ord(first) > 0x2E80:
        return b
    return (" " + b) if (last.isalnum() and first.isalnum()) else b


def _fix_levels(ir: IR) -> None:
    """R-04 编号连续性 → 层级校正：编号标题的层级按「点数 + 1」归一。

    CH-476（R7-PIPE-4）：DOCX `Heading N` 样式显式声明的层级（`flags.level_from_hint`）
    不参与改写——显式声明是文档作者的意图，样式层与编号深度本就独立（"Heading 2"
    写「3.2.1 实施细则」是合法排版），原实现无条件覆盖把 `_heading_level` 的
    「hint 优先」口径架空。同时删除 `seen` 兄弟机制：同一前缀的 key 点数恒相同，
    sibling 在数学上必等于 lvl（`rule_notes` 永不触发），那段是死逻辑。
    """
    for b in ir.blocks:
        if b.type != "heading" or b.flags.get("level_from_hint"):
            continue
        m = _NUM.match(b.text)
        if not m:
            continue
        b.level = min(6, m.group(1).count(".") + 1)
        b.font.setdefault("rule", "R-04")
