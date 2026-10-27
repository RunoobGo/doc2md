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
from .escapes import escape_cell_markdown, escape_entities
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
# CH-495（R14-PIPE-12）：S4 直接导入本式（dev-plan §列表渲染的既定设计），
# 跨模块共享契约转正为公共名，不再是私有符号。
ORDERED_ITEM = re.compile(r"^\s*(\d{1,3})[、.．)）]\s*(?!\d)(?=\S)")
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
# D2（QA-R2）：当文档同时出现多种 Markdown 字面量形态时，显式段落里的
# `#` / `>` / `1.` 是**被展示的语法**而非结构信号。至少命中两类才判定，
# 避免把「一句以 > 开头的真实引用 + 一个普通编号列表」误降为纯文本。
_LITERAL_MARKERS = (
    re.compile(r"^\s{0,3}#{1,6}\s+\S"),
    re.compile(r"^\s{0,3}>\s?\S"),
    re.compile(r"^\s*\d{1,3}[.)]\s+\S"),
    re.compile(r"^\s*[-+*]\s+\S"),
    re.compile(r"^\s*```"),
    re.compile(r"\[[^\]]+\]\([^)]+\)"),
    re.compile(r"\*\*[^*]+\*\*"),
    re.compile(r"<[A-Za-z/][^>]*>"),
)


def _markdown_literal_doc(pages: list[RawPage]) -> bool:
    """文档是否在演示 Markdown 字面量（D2 的保守判定）。

    只统计显式 DOCX 段落（无表格/等宽/样式提示），且要求 ≥2 类不同形态；
    现有语料中 docx_07 只有一类 `>`、docx_09/11 只有一类有序编号，不受影响。
    """
    kinds: set[int] = set()
    for pg in pages:
        for ln in pg.lines:
            t = (ln.text or "").strip()
            if not t or ln.row or not ln.explicit_para or ln.mono \
                    or ln.level_hint or ln.list_marker:
                continue
            for i, pat in enumerate(_LITERAL_MARKERS):
                if pat.search(t):
                    kinds.add(i)
                    break
    return len(kinds) >= 2


def _plain_docx_line(ln) -> bool:
    return bool(getattr(ln, "explicit_para", False)) and not ln.level_hint \
        and not ln.list_marker and not ln.mono and ln.row is None

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
# N-4：罗马数字页码（I~XXXIX）同口径覆盖——“— IV —”“Page iv of x”
# “第 iv 页”。裸罗马数字（“IV”单独一行）无上下文不认：装饰式/量词/比值
# 形态才触发（宁漏勿误，与裸阿拉伯数字不入 _PAGE_NUM 同一对称口径）。
_ROMAN = r"(?=[ivxIVX])(?:X{0,3})(?:IX|IV|V?I{0,3})"
_PAGENUM_ATOM = rf"(?:\d{{1,4}}|{_ROMAN})"
_PAGE_NUM = re.compile(
    rf"^(?:[—–-]\s*{_PAGENUM_ATOM}\s*[—–-]"
    rf"|第\s*{_PAGENUM_ATOM}\s*页(?:\s*共\s*{_PAGENUM_ATOM}\s*页)?"
    rf"|共\s*{_PAGENUM_ATOM}\s*页\s*第\s*{_PAGENUM_ATOM}\s*页"
    rf"|page\s*{_PAGENUM_ATOM}(?:\s*of\s*{_PAGENUM_ATOM})?"
    rf"|{_PAGENUM_ATOM}\s*/\s*{_PAGENUM_ATOM})$", re.I)
_PAGE_NUM_INLINE = re.compile(
    rf"(第\s*{_PAGENUM_ATOM}\s*页\s*共\s*{_PAGENUM_ATOM}\s*页|[—–-]\s*{_PAGENUM_ATOM}\s*[—–-])", re.I)

# O-09（C-02）：`第 N 页` / `第 M 栏` 逐页变体归一化（见 _furniture_key；
# 只认「页/栏」量词，「章/节/图/表」等真标题量词不动）。
# N-4：量词数字放宽到罗马数字（“第 IV 页第 II 栏”）。
_FURNITURE_PAGE_COL = re.compile(rf"第\s*{_PAGENUM_ATOM}\s*[页栏]", re.I)

# N-4：「章节名 · 页码」混合页眉（页眉每页 = 同一章节名 + 递增页码）。
# 尾随页码片段**只在 key 层**抹平（供跨页重复聚类），不进
# `_PAGE_NUM_INLINE`——那会整行丢弃、连章节名一起误杀。旧键逐页不同
# （“第一章 引言 · 12” ≠ “第一章 引言 · 13”）聚不出跨页重复。
# 分隔符不含连字符：日期/编号（2026-10-22、表 3-1）不得被折成 #。
_FURNITURE_TAIL_NUM = re.compile(rf"[·•・|｜—–]\s*(?:{_PAGENUM_ATOM})\s*$", re.I)

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
    if not ORDERED_ITEM.match(t) or len(t) >= 30:
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
    """页眉页脚归一化键：页码数字抹成 `#`，空白折叠，供跨页重复检测。

    O-09（C-02）：`第 N 页` / `第 M 栏` 式**逐页变体**也要抹成 `#`——pdf_10
    类页眉每页数字都变（"第 1 页第 1 栏" → "第 2 页第 3 栏"），旧键逐页不同
    无法聚出跨页重复。只抹「页/栏」量词的数字：`第 N 章` / `第 1 节` 等真
    标题量词不动（宁漏勿误——真标题折进 `#` 会被跨页重复误杀）。防线仍是
    剔除处的三重约束：边距带内 + 字号不高于正文或短键 + ≥2 页重复。

    N-4：两类新增——① 量词/页码片段放宽到罗马数字（“第 IV 页”“— IV —”）；
    ② 「章节名 · 页码」混合页眉的**尾随**页码片段抹平（“第一章 引言 · 12”
    与 “… · 13” 聚成同键）。分隔符不含连字符，日期/编号不受影响。
    """
    t = _PAGE_NUM_INLINE.sub("#", text or "")
    t = _FURNITURE_PAGE_COL.sub("#", t)
    t = _FURNITURE_TAIL_NUM.sub("#", t)
    return re.sub(r"\s+", " ", t).strip()


def _is_wrap_tail(prev_kept: str, candidate: str) -> bool:
    """本行是否**上一句未写完正文的折行后半截**——是则不得判家具。

    R18-PIPE-2（320 页金样本实测）：跨页断句把句子折成两半后，落在页顶/页边带里的
    后半截（「准流程。」「说明。」「标准流程。」「当遵循例外情况。」……）在长文档里
    会因同一句式反复出现而满足「≥2 页重复」，再叠上 `len(key) <= 24` 这条最松的
    触发臂，就被当页脚**整行删掉**——删掉的不只是噪声，而是正文的一半，前后两句还
    会被接成一段（静默丢字 + 错接，且产物里不留任何痕迹）。

    两个条件同时成立才豁免，缺一会走偏（两侧都有实测样本）：
    ① 上一条保留行未硬句末——句子确实被折断；页序即阅读序，锚点跨页也连续。
    ② 本行自己以硬句末收尾——它把那句关上了。
    ② 不是装饰：语料 `pdf_09` 的运行页眉「综合长文档测试 内部测试样本」正好接在一行
    无标点正文之后，只凭 ① 会被留下——产物里多出三行页眉，跨页续段的两半还被劈成
    两段（`tests/corpus/run_corpus.py` 逐字节比对当场转红）。金样本上被吃掉的尾巴
    全部以句号收尾，② 既保住它们，又把不带标点的页眉挡在豁免面之外。
    宁漏勿误的方向不变，只是换了判据：不带硬句末的真尾巴仍留在剔除面（与修复前
    一致），不为此引入新的页眉回潮。
    """
    return (bool(prev_kept) and not _ends_sentence(prev_kept)
            and _ends_sentence(candidate))


def _strip_page_furniture(pages: list[RawPage], base_size: float) -> list[RawPage]:
    """剔除页码与跨页重复的页眉页脚（仅在页边距带内，宁漏勿误）。

    三类信号：
    1. 行本身是页码（装饰式 / “第 N 页 共 M 页” / Page N of M）；
    2. 行内含页码片段（页脚常把标题与页码排在同一行）；
    3. 同一边距行在 ≥2 页重复，且字号不高于正文（页眉/保密声明）。
    第一页居中的大标题只在单页出现，且字号通常更大 → 保留。
    信号 3 另受 `_is_wrap_tail` 约束（R18-PIPE-2）：接在未硬句末正文之后、且自己
    以硬句末收尾的残句尾巴不判家具。
    """
    if not pages or not any(p.height > 0 for p in pages):
        return pages
    counts: dict[str, int] = {}
    body_sizes: dict[float, int] = {}
    for page in pages:
        seen: set[str] = set()
        height = page.height
        for ln in page.lines:
            t = (ln.text or "").strip()
            if not t:
                continue
            # CH-493（P-2）：`_in_margin` 每行只算一次——原实现对同一行算了两次
            # （实测 pdf_09 的 125 行触发 250 次调用）。
            if not _in_margin(ln, height):
                if ln.size > 0:
                    key_size = round(ln.size, 1)
                    body_sizes[key_size] = body_sizes.get(key_size, 0) + max(1, len(t))
                continue
            key = _furniture_key(t)
            if key and key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    body_hint = (max(body_sizes.items(), key=lambda kv: kv[1])[0]
                 if body_sizes else base_size)
    out: list[RawPage] = []
    # R18-PIPE-2：折行续写锚点——跨页也连续（页序即阅读序）。见 `_is_wrap_tail`。
    prev_kept = ""
    for page in pages:
        kept: list = []
        # CH-493（P-2）：仅在输入页自带直方图时，按**保留后**的行重算一份
        # （与 `s1_parse._size_hist` 同口径）。原实现重建 RawPage 时丢掉
        # `size_hist`，任何在本函数之后调 `modal_size` 的调用方都会静默退回全文档
        # 逐行扫描，抵消 CH-492（P-1）的优化——目前两条生产路径都在本函数**之前**
        # 调 `modal_size`，故属潜伏项而非现行缺陷。重算而非照搬原直方图：照搬会把
        # 已剔除的页眉页脚字号算进正文口径。手工构造的页本来就没有直方图，不为它们
        # 付这遍开销（实测 2400 行 ≈0.25ms，得不偿失）。
        track_hist = bool(page.size_hist)
        hist: dict[float, int] = {}
        height = page.height
        for ln in page.lines:
            t = (ln.text or "").strip()
            if t and _in_margin(ln, height):
                if _PAGE_NUM.fullmatch(t) or _PAGE_NUM_INLINE.search(t):
                    continue
                key = _furniture_key(t)   # CH-493（P-2）：每行最多一次（原最多 3 次）
                if counts.get(key, 0) >= 2 and (
                        ln.size <= 0
                        or ln.size < body_hint - 0.5
                        or len(key) <= 24) and not _is_wrap_tail(prev_kept, t):
                    continue
            kept.append(ln)
            if t:
                prev_kept = t
            if track_hist and ln.size:
                k = round(ln.size, 1)
                hist[k] = hist.get(k, 0) + max(1, len(t))
        out.append(RawPage(index=page.index, lines=kept, height=page.height,
                           size_hist=hist,
                           # CH-500（R17-PIPE-5）：版式探测字段随页携带。原重建
                           # 只搬 index/lines/height/size_hist，`rotation` /
                           # `rtl_ratio` 静默归零——当前唯一的读取方
                           # （`convert.py` 的 W1046）用的是**剔除前**的 `pages_all`，
                           # 所以线上没露馅（实测旋转页/RTL 页的 W1046 仍正常交付）；
                           # 但任何在本函数之后读这两项的调用方都会拿到 0，
                           # 与 CH-493（P-2）修 `size_hist` 前的是同一个失效形态。
                           rotation=page.rotation, rtl_ratio=page.rtl_ratio))
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
                b.text = _append_line(prev, text.strip(),
                                      space=_wrap_space(b, ln))
                b.line_trail = bool(getattr(ln, "trailing_space", False))
                b.flags.setdefault("joined_across_break", True)
                return True
            return False
        if b.type == "heading":
            continue
        if b.type == "page_break":
            # CH-496 / A5：列表项折行恰好落在页界时，分页行先冲刷段缓冲，
            # 续行到这里若被 page_break 挡回就退化成半句段落。分页符不是
            # 语义边界（上面的结构起始/缩进守卫才是），可穿过。
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
                b.text = _append_line(b.text or "", text.strip(),
                                      space=_wrap_space(b, ln))
                b.line_trail = bool(getattr(ln, "trailing_space", False))
                return True
            return False
        if b.type in ("paragraph", "list_item"):
            return False
        return False
    return False


def _wrap_space(b: Block, ln) -> bool:
    """O-01（C-04）：折行点处的空白证据——块尾来源行或续行自身任一带
    `trailing_space` / `leading_space` 即认为原文此处有空格。"""
    return bool(getattr(ln, "leading_space", False)
                or getattr(b, "line_trail", False))


def _join_back(blocks: list[Block], text: str, font: dict, ln=None) -> Block | None:
    """把续句行并回最近的内容块；成功返回被并入的块，失败返回 None。

    CH-459：遇到 quote / code / table 即止步——这些是结构块，续句穿过它们
    并回更前的段落会制造错位（原实现它们不存在，该分支不可达）。标题块照旧
    可穿过（CH-430：页眉/编号标题插在折行中间是常态）；CH-496 / A5 起
    page_break 也可穿过——分页符只是版面事件，续句标点起头已经证明是同一句。

    `ln`（O-01 / C-04）：续行本身。行缘空白证据（`leading_space`）与块尾
    `line_trail` 共同决定折行点是否补空格；缺省 None 时按无证据处理
    （直接构造块序列的调用方没有行对象）。
    """
    for b in reversed(blocks):
        if b.type in ("paragraph", "list_item"):
            b.text = _append_line(b.text or "", text.strip(),
                                  space=_wrap_space(b, ln))
            b.line_trail = bool(getattr(ln, "trailing_space", False))
            b.flags.setdefault("joined_across_break", True)
            if font and not b.font:
                b.font = dict(font)
            return b
        if b.type == "heading":
            continue
        if b.type == "page_break":
            continue                  # CH-496 / A5：续句跨页（见 docstring）
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


def _gfm_table(rows: list[list[str]], *, entities: bool = False) -> str:
    """行单元格 → GFM 表格文本（列数不齐补齐到最多列；单元格内 | 转义）。

    O-05（C-06）：单元格内容规范统一收口在本函数——调用方只交单元格文本、
    不预拼：`|` 必须转义（否则提前结束单元格），换行统一为 `<br>`（S1 的
    段落边界标记与 run 内 `w:br` 映射的 `\\n` 同一口径，不再一个可辨一个
    掉空格）。`<br>` 本身不再转义——GFM 单元格内的合法换行。
    D4（QA-R2）：**先转义反斜杠再转义管道**。原实现只转义管道，源文本
    `\\|` 先留下一个未转义反斜杠，渲染器把 `\\` 还原成字面反斜杠后管道
    成为分隔符，整行多出一格。按 `<br>` 分段处理：结构换行保留，段内
    Markdown 元字符转义（含源文本的字面 `<`）。

    `entities`（OPT4-16 / B-37）：调用方是否**尚未**折过 HTML 实体。
    - False（DOCX 路径，默认）：S1 `_docx_cell_text` 已折过 `& < >` 再插入
      结构 `<br>`，这里的 `<br>` 是分隔符，字面 `<` 已不存在；
    - True（PDF 路径）：先折实体再分段——源文本里的字面 `<br>` 因此不会被
      误当结构换行，与 DOCX 语义对齐。
    实体折转与 Markdown 转义的实现都在 `escapes` 模块（单一来源）。
    """
    def cell_md(c: str) -> str:
        s = str(c).strip()
        if entities:
            s = escape_entities(s)
        parts = s.replace("\n", "<br>").split("<br>")
        return "<br>".join(escape_cell_markdown(p) for p in parts)

    ncols = max(len(r) for r in rows)
    padded = [(r + [""] * ncols)[:ncols] for r in rows]
    esc = [[cell_md(c) for c in row] for row in padded]
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
    literal_doc = _markdown_literal_doc(pages)
    # N-8（QA-OPT-3）：页标题形态豁免集。无编号行的**数字折叠键**（“正常页面 1”
    # →“正常页面 #”）在 ≥2 页的边距带内重复 → 逐页变体的页标题/页眉残留
    # （furniture 剔除只认「页/栏」量词等显式页码片段，“正常页面 1” 这类
    # 裸计数不可折，会漏到这里）。宁漏勿误：这类行即使字号显著大于正文也
    # 不抬成标题；全文档唯一（“代码说明”“结语”）的无编号大字行才判标题。
    _PAGE_TITLE_REPEAT = 2
    page_titles: set[str] = set()
    _pt_counts: dict[str, int] = {}
    for _pg in pages:
        _seen_pt: set[str] = set()
        for _ln in _pg.lines:
            _t = (_ln.text or "").strip()
            if not _t or len(_t) > 60 or not _in_margin(_ln, _pg.height):
                continue
            if _NUM.match(_t) or _CJK_HEAD.match(_t) or _CJK_ORD.match(_t) \
                    or _PAREN_HEAD.match(_t):
                continue                          # 编号行不收集：页眉变体已由 N-4 折平剔除
            _k = re.sub(r"\d+", "#", _furniture_key(_t))
            if _k not in _seen_pt:
                _seen_pt.add(_k)
                _pt_counts[_k] = _pt_counts.get(_k, 0) + 1
    page_titles = {k for k, n in _pt_counts.items() if n >= _PAGE_TITLE_REPEAT}
    # CH-459：跨页存续的聚集状态
    code_buf: list[str] | None = None          # None = 不在代码块内
    code_mode = ""                             # fence（显式围栏）| indent | mono
    code_lines: list = []                      # CH-476（R7-GAP-1）：与 code_buf 同步的 Line
    quote_buf: list[str] = []
    quote_lines: list = []                     # CH-476：与 quote_buf 同步的 Line
    row_buf: list[list[str]] = []
    row_x0: list[float] = []
    # CH-493（P-3）：行首 x0 的**累加和**。原实现每追加一行都 `sum(row_x0)` 从零
    # 重算整组均值 → O(行数²)（实测 8000 行表单的平方项 ≈30ms）。改为增量维护
    # 后是 O(1)，且与 `sum()` 同为「从 0 起、按行序左折」，浮点结果逐位相同。
    row_x0_sum = 0
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
        _flush(para_buf, blocks, para_font, para_page, para_lines)
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
        nonlocal row_buf, row_x0, row_x0_sum, row_lines
        if not row_lines:
            return
        # CH-489：DOCX 的 `w:tbl` 是显式表格对象，单行表也必须是表格；
        # PDF 的单行候选仍退回段落（无显式对象时宁可漏判，不制造假表）。
        one_explicit_row = bool(row_lines[0].row_explicit) and len(row_buf) == 1
        if as_table and (len(row_buf) >= _ROW_MIN or one_explicit_row):
            # O-05（C-06）：拍平空位计数汇总为表级 flags.flattened_cells。
            # 只在非零时写 flag——零值不落字节（G6：无合并的表格 ir.json 不变），
            # 非零即「本表有合并信息被降维」，convert 据此留 W1045 告警。
            flat = sum(int(getattr(ln, "row_flat", 0) or 0) for ln in row_lines)
            blocks.append(Block(id="", type="table",
                                text=_gfm_table(
                                    row_buf,
                                    # OPT4-16：DOCX 行由 S1 折过实体（<br> 是结构），
                                    # PDF 行在此折——同一实现、恰好一次。
                                    entities=not row_lines[0].row_explicit),
                                page=row_page,
                                flags={"flattened_cells": flat} if flat else {},
                                row_shape=[len(r) for r in row_buf]))
            _mark_provenance(blocks[-1], row_lines)     # CH-476（R7-GAP-1）
        else:                                   # 组不成表：逐行退回段落（漏判不误判）
            for i, ln in enumerate(row_lines):
                blocks.append(Block(id="", type="paragraph",
                                    text=ln.text.strip(), font=_font(ln),
                                    page=row_page,
                                    line_trail=bool(ln.trailing_space)))
                if i == 0 and (ln.link or ln.link_internal):
                    _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
        row_buf, row_x0, row_lines = [], [], []
        row_x0_sum = 0

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
            if _FENCE.match(text) and not (literal_doc and _plain_docx_line(ln)):
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
                if code_mode == "mono" and (not ln.mono or ln.link
                                            or ln.link_internal):
                    # R19-PIPE-2：等宽段里遇到带链接标注的行也收块，让本行走常规分流去拿
                    # `_attach_link`——与下面「带链接的行不起等宽块」是同一条契约（等宽是
                    # 排版证据、Link 标注是语义证据）。**只**对 mono 段生效：fence / indent
                    # 段的边界来自源文档的显式记号，在那里收块会改写代码内容，其链接丢失
                    # 另行登记（需新告警码 + 附录 D 预留，见 dev-plan §12.7）。
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
                                     hint=ln.level_hint, top=ln.top,
                                     page_height=getattr(page, "height", None))
            elif page_titles and \
                    re.sub(r"\d+", "#", _furniture_key(text)) in page_titles:
                # R19-PERF-1：`page_titles` 空集时短路。31 篇样本里 29 篇该集合为空，
                # 而本臂对每个非表格行都要付一次 `_furniture_key`（内含 3 次预编译 sub
                # + 一次模块级 `re.sub`，后者每次调用还要走 `re._compile` 缓存查找）。
                # 320 页金样本实测：`_furniture_key` 18,312 次调用中 15,613 次（85%）来自
                # 本行，占 `analyze()` 118.4 ms 里的 27.2 ms（S3 的 23.0%）。空集上的
                # `in` 恒为 False，加前件不改语义、不改产物字节。
                lvl = None        # N-8：逐页变体页标题，字号再大也不抬（宁漏勿误）
            else:
                lvl = None if ln.list_marker else _heading_level(
                    text, ln.size, base_size, ln.bold, top=ln.top,
                    page_height=getattr(page, "height", None))
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
                    prev.text = _append_line(prev.text or "", head_text,
                                             space=_wrap_space(prev, ln))
                    prev.line_trail = bool(getattr(ln, "trailing_space", False))
                    _mark_provenance(prev, (ln,), merge=True)
                    continue
                blocks.append(Block(id="", type="heading", text=head_text,
                                    level=lvl, font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
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
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
                blocks[-1].flags["docx_numbering"] = {
                    "marker": ln.list_marker, "level": ln.list_level}
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 列表（既有口径不变）---
            if _BULLET.match(text) and not (literal_doc and _plain_docx_line(ln)):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 有序列表（CH-478 / R6-PIPE-1 收口：同字号单级编号行的归宿）---
            # 表格行优先于本分支：S1 已按横向间隙聚出 ≥2 单元格时，行首的 "1、"
            # 是单元格内容而非列表标记（宁漏勿误）。
            if _is_ordered_item(text) and not (ln.row and len(ln.row) >= 2) \
                    and not (literal_doc and _plain_docx_line(ln)):
                flush_para()
                flush_quote()
                flush_rows(True)             # 行组被结构行终止：按事实判定
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # --- 字母编号列表（“a. …”）：与数字列表同口径 ---
            if _is_alpha_item(text) and not (ln.row and len(ln.row) >= 2) \
                    and not (literal_doc and _plain_docx_line(ln)):
                flush_para()
                flush_quote()
                flush_rows(True)
                blocks.append(Block(id="", type="list_item", text=text.strip(),
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
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
                elif abs(ln.x0 - (row_x0_sum / len(row_x0))) > _ROW_X0_TOL:
                    flush_rows(True)            # 行首跳变：切组，各自判定
                    row_page = page.index
                row_buf.append(list(ln.row))
                row_x0.append(ln.x0)
                row_x0_sum += ln.x0
                row_lines.append(ln)
                continue
            if row_buf:
                flush_rows(True)
            # --- 引用块（CH-459：段间边界起算，段中引用行按保守口径留在段内）---
            # D3（QA-R2）：引用前缀是**源文本字面量**时不得激活；文档同时演示多种
            # Markdown 语法（literal_doc）时，显式段落一律按正文处理。
            # 真实引用行（docx_07）仍需先冲刷已缓冲段落，否则会被续段逻辑吞成段落；
            # PDF 段中折行以 `>` 开头仍按保守口径留在段内（宁漏勿误）。
            if _is_quote_line(text) and not (literal_doc and _plain_docx_line(ln)):
                if para_buf and not ln.explicit_para:
                    pass
                else:
                    if para_buf:
                        flush_para()
                    if not quote_buf:
                        quote_page = page.index
                    quote_buf.append(_quote_text(text))
                    quote_lines.append(ln)
                    continue
            flush_quote()   # CH-464：非引用行到达 → 引用缓冲立即落块（原实现推迟到
                            # 下一个空行/结构行，quote 块会排进其后的段落之间，乱序）
            # --- 等宽字体连续段 → 代码块（CH-459）---
            # R19-PIPE-2：带链接标注的行**不起**代码块。等宽字体是排版证据，Link 标注是
            # 语义证据，冲突时语义优先：原顺序让本行落进 code 块，`_attach_link` 永不执行，
            # URL / GoTo 目标从 IR 与产物里静默消失且 `ir.warnings` 为空——违反损失台账
            # 契约（C-29 / O-05「诚实暴露而非静默降级」）。内部跳转（`表 3 参数一览` →
            # `#page=7`）尤其致命：正文文字完全看不出这里曾有交叉引用。
            # 只加前件、不调分支顺序：`note_kind`（:下 8 行）与链接块的既有先后关系不动，
            # 否则含链接的注文会从 note 块退化成普通链接段（`docx_04_links_notes` 会转红）。
            if ln.mono and not para_buf and not (ln.link or ln.link_internal):
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
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
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
                                    font=_font(ln), page=page.index,
                                    line_trail=bool(ln.trailing_space)))
                _attach_link(blocks[-1], ln)
                _mark_provenance(blocks[-1], (ln,))
                continue
            # CH-430：续句标点起头 → 无条件并回上一个内容块（可跨标题/空行打断）
            if _continuation_led(text) and not para_buf and not ln.explicit_para:
                back = _join_back(blocks, text, _font(ln), ln)
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
                   hint: int | None = None, *, top: float | None = None,
                   page_height: float | None = None) -> int | None:
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
    # 只认「短行 + 字号显著大于正文」：+10pt 以上视为一级，其余二级。
    # N-8（QA-OPT-3）：+5.5pt 阈值原为豁免 golden small_3p 的页标题而定——
    # O-09 之后页标题在 furniture 层已剔除（小文档 # 三页聚合），历史顾虑
    # 消失；N-4 同批暴露的 pdf_04「代码说明」（+4.0）/ pdf_07「结语」（+3.5）
    # 被续段合并吞掉，阈值降到 base+3.5（与 numbered 分支的字号信号对齐）。
    # 逐页变体页标题的残余风险由调用方的 page_titles 豁免集兜底。
    # D7（QA-R2）：字号只是必要条件，不足以单独晋升——14pt 提示框在正文中部
    # 会被误判成 H2。再要求至少一个版面信号：页顶（≤10% 页高）、短行（≤16 字）
    # 或粗体；pdf_04「代码说明」/ pdf_07「结语」均满足页顶 + 短行，语料不变。
    if (not numbered and size and base and size >= base + 3.5
            and len(t) <= 60
            and not t.endswith(("。", "！", "？", ".", "!", "?"))):
        at_top = bool(top is not None and page_height
                      and top <= float(page_height) * 0.10)
        if bold or len(t) <= 16 or at_top:
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


def _flush(buf: list[str], blocks: list[Block], font: dict, page: int,
           lines: list | None = None) -> None:
    """行缓冲 → 段落块：逐行按中西文规则缝合。

    CH-473（PIPE-9）：不再收 `seq` 预写 id——唯一赋值点是 `analyze` 收尾重排。
    `lines`（O-01）：与 `buf` 逐行对齐的来源行（可含 None 占位）。跨行拼接时
    把行缘空白几何证据（`trailing_space` / `leading_space`）传给 `_join_line`，
    折行恰好切在空格上时补回该空格（"数字 2026" → "数字" + "2026"）。
    """
    if not buf:
        return
    # CH-495（R14-PERF-7）：原实现逐行 `text = _append_line(text, nxt)` 整串复制，
    # 超长段落（PDF 无断行分段时 buf 可达数千行）O(n²)。`_append_line`/`_join_line`
    # 的判定只依赖累积串的**末字符**（连字符剥除 / 数字断行 / 中西文空白），
    # 改为片段列表 + 末字符跟踪，收尾一次 join → O(n)，拼接口径逐字节等价。
    pieces = [buf[0]]
    tail = buf[0][-1:]
    for i, nxt in enumerate(buf[1:], start=1):
        prev_ln = lines[i - 1] if lines and i - 1 < len(lines) else None
        next_ln = lines[i] if lines and i < len(lines) else None
        space = bool(getattr(prev_ln, "trailing_space", False)
                     or getattr(next_ln, "leading_space", False))
        strip_hyphen = tail.endswith("-") and nxt[:1].islower()
        frag = _join_line(tail, nxt, space=space)
        if strip_hyphen:
            pieces[-1] = pieces[-1][:-1]
        pieces.append(frag)
        tail = frag[-1:] or ("".join(pieces))[-1:]
    text = "".join(pieces).strip()
    if text:
        last = lines[-1] if lines else None
        blocks.append(Block(id="", type="paragraph", text=text,
                            font=font or {}, page=page,
                            line_trail=bool(getattr(last, "trailing_space", False))))


def _append_line(a: str, b: str, *, space: bool = False) -> str:
    """按跨行口径把 b 接到 a 后面（唯一的拼接入口，CH-469）。

    连字符断词（"under-" + "score" → "underscore"）需要剥掉 a 尾部连字符，
    这层语义集中在本函数处理；调用方不再自行 `a + _join_line(a, b)`，
    避免追加/替换两种口径混用产出重复文本。

    `space`（O-01 / C-04）：行缘空白几何证据——折行点处原本有空格（上一行
    `trailing_space` 或下一行 `leading_space`）。中西文边界默认不插空格，
    但有此证据时必须补回，否则 "数字 2026" 折行后被拼成 "数字2026"。
    """
    frag = _join_line(a, b, space=space)
    if a.endswith("-") and b[:1].islower():
        return a[:-1] + frag
    return a + frag


def _join_line(a: str, b: str, *, space: bool = False) -> str:
    """跨行拼接的**待追加片段**（经 _append_line 与 _flush 消费，CH-469 / CH-495）。

    连字符断词分支只返回 b：整串 `a[:-1] + b` 是替换语义的写法，
    而两个调用方都是追加语义，直接返回整串会产出 "under-underscore"
    这类重复文本（与方案 §merged[-1]=prev[:-1]+ln 的意图不符，且原实现无测试覆盖）。

    `space`（O-01 / C-04）：原文折行处有空格时的补空格证据。只作用于中西文
    边界（本分支默认无空格）；数字被折行切开（"2" + "026"）与连字符断词两条
    早退分支不受影响——那两种形态下原文不会有空格。
    """
    if a.endswith("-") and b[:1].islower():
        return b
    last = a[-1] if a else ""
    first = b[0] if b else ""
    if last.isdigit() and first.isdigit():
        # 折行把同一个数字切开（"2" + "026"）时不得插空格。
        return b
    if _CJK_HEAD.match(b) or ord(last) > 0x2E80 or ord(first) > 0x2E80:
        # 中西文边界：默认不加空格（中文排版惯例），但行缘空白证据表明
        # 原文该处确有空格时补回（宁漏勿误的反面是「有据必恢」——证据来自
        # page.chars 里与行缘相邻的空白字符，见 s1_parse._edge_spaces）。
        return (" " + b) if space else b
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
