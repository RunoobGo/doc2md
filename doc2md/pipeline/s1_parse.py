"""S1 解析层 —— 文本型 PDF / DOCX 抽取为页级行结构。

范围纪律（上游 v2.4 Delta §10-1，方案 1.2 非目标）：不做图片 / OCR / 扫描件 / 公式；本层只处理可抽取文本层。

CH-459（FR-104 / FR-107 收口）：本层补三组结构化信号，供 S3 产出完整七类块——
- `Line.mono`：等宽字体行（代码块信号；取自 fontname，阈值见 `_is_mono`）；
- `Line.row`：表格行的单元格文本（PDF 按行内字符横向间隙聚类，DOCX 取 `w:tbl` 真实单元格）；
- `Line.link` / `link_internal` / `link_dest`：链接（PDF 取 Link 标注的 URI / GoTo，
  DOCX 取 `w:hyperlink` 的关系目标或书签锚点）。
另补 `Line.level_hint`：DOCX 段落样式名显式声明的标题层级（"Heading N"），
S3 据此判定标题——原实现只看字号/粗体/编号，DOCX 的 Heading 样式全部落成段落。

CH-476（R7-GAP-1，决策 D4「提取而非拒收」）：`Line.has_revision` + 修订痕迹的
「接受后」口径——DOCX 段落 / 表格行含 `w:ins`（插入）或 `w:del`（删除）时，正文按
「全部接受修订」的样子提取（插入并入、删除不入），并给该行打旗标。原实现只走
`p.text` / `p.runs`，而 python-docx 不读 `w:ins` 内的 run，修订插入的正文整段静默丢失。

CH-476（R7-GAP-2/3，决策 D7）：`read_docx` 补两处内容保真——
- 脚注 / 尾注（`word/footnotes.xml`、`word/endnotes.xml`）正文抽为尾部段落行，
  `Line.note_kind` 标来源；原实现只遍历 document.xml 的 body，注文整块丢失；
- 表格单元格内多段落以 `<br>` 相连（O-05 起；CH-476 时为单空格，原
  `"".join(tc.iter(w:t))` 直接粘连 = 吃掉段落边界）。

CH-476（R7-PERF-d/g）：DOCX 解析两处热点（口径不变，产物逐字节等价）——
- 段落样式按 `w:pStyle/@w:val` 记忆化（`_DocxCtx.styles`）：python-docx 的
  `paragraph.style` 每次访问都重扫整张 styles part，实测 185µs/段、10k 段文档
  仅样式解析即 3.7s（占 `read_docx` 九成）；样式只有几十种，记忆化后同样式段落直取；
- `w:hyperlink` 改为文档级预检（`_DocxCtx.has_links`）：无链接的文档不再逐段跑
  `.//w:hyperlink`（实测 1.6µs/段，10 万段 ≈ 160ms）。
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from pathlib import Path

from .escapes import escape_entities

MAGIC_PDF = b"%PDF-"
MAGIC_ZIP = b"PK\x03\x04"
MAGIC_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOCX_REQUIRED_PART = b"word/document.xml"

# CH-459：等宽字体的识别口径（PDF fontname / DOCX run.font.name，均转小写后子串匹配）。
# 只认明确的等宽家族，不把「无衬线」当代码——误判面比漏判大得多。
_MONO_HINTS = ("mono", "courier", "consolas", "menlo", "monaco")


@dataclass
class Line:
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    strike: bool = False
    link: str | None = None
    link_internal: bool = False
    link_dest: str = ""
    mono: bool = False
    # CH-483（R8-PIPE-7）：整行所有非空文本是否统一带该格式。`bold/italic/strike`
    # 仍是「任一 run 命中」的行级信号（旧口径），这里用于块级 marks 的保守生产。
    uniform_bold: bool = False
    uniform_italic: bool = False
    uniform_strike: bool = False
    # CH-483（R8-PIPE-4）：DOCX 自动编号（w:numPr）解析出的行首标记与层级。
    list_marker: str = ""
    list_level: int = -1
    row: list[str] | None = None            # CH-459：表格行单元格（非 None 即行候选）
    row_explicit: bool = False              # CH-459：行来自真实表格对象（DOCX w:tbl），
                                            # 而非 PDF 几何推断——S3 的防误判守卫只对后者生效
    row_flat: int = 0                       # O-05（C-06）：本行被拍平的网格空位数
                                            # （gridSpan 展开空位 + vMerge 连续格置空）。
                                            # GFM 无 colspan 语义，这些空位是「有信息
                                            # 被降维」的计数证据；S3 汇总为表级
                                            # flags.flattened_cells，convert 据此留
                                            # W1045 告警（诚实暴露，不自动合并）
    table_start: bool = False               # 新表格的首行：S3 据此切开相邻 w:tbl
    level_hint: int | None = None           # CH-459：DOCX Heading 样式显式层级
    has_revision: bool = False              # CH-476（R7-GAP-1）：DOCX 段落/表格行含
                                            # `w:ins` / `w:del` 修订痕迹（正文已按
                                            # 「接受后」口径提取，此信号供块级留痕）
    note_kind: str = ""                     # CH-476（R7-GAP-2）：该行来自 DOCX 注
                                            # （"footnote" / "endnote"），空串=正文
    x0: float = 0.0
    char_w: float = 0.0                     # CH-482：等宽字符实测宽度（pt，非等宽行 0）
                                            # ——x0 含行首空白，字宽不能由 (x1-x0)/len(text)
                                            # 反推；S3 的缩进量化用它把 x0 差换算成空格级数
    top: float = 0.0                        # PDF 行几何（pt，原点在页面左上）；DOCX 为 0
    bottom: float = 0.0                     # 供页眉页脚带判定，S3 段落分段亦用
    explicit_para: bool = False             # DOCX `w:p` 段落边界：S3 不得跨段续接
    # O-01（QA-D2M-2026-10-21 / C-04）：行首 / 行尾空白几何证据。
    # `extract_text_lines` 把行首尾空白从 text/chars/bbox 一并剥掉，但
    # page.chars 里仍在——折线恰好落在空格上时，S3 的跨行拼接需要知道
    # 「此处原有空格」，否则中西文边界会把空格吃掉（"数字 2026" → "数字2026"）。
    # 判据见 `_edge_spaces`：只认与行缘严格相邻的空白字符，行内空格与
    # 远距（栏间）空白均不算。
    trailing_space: bool = False
    leading_space: bool = False


@dataclass
class RawPage:
    index: int                      # 0-based
    lines: list[Line] = field(default_factory=list)
    height: float = 0.0             # PDF 页高（pt）；DOCX / 合成页为 0（无几何）
    # CH-492（P-1）：S1 单趟累计的字号直方图 round(size,1) → 字符数，供
    # s3_structure.modal_size 归并，免去全文档第二次逐行遍历。手工构造的页
    # （测试 / 直接调用方）缺省为空 dict → modal_size 回退逐行扫描，行为不变。
    size_hist: dict[float, int] = field(default_factory=dict)
    # O-10（C-28/F-12）：版式探测——旋转角（/Rotate 原值）与 RTL 字符占比
    # （0~1）。RawPage 本就不序列化（瞬态），convert 据此留 W1046 告警；
    # 手工构造页缺省 0/0.0 = 未探测到，不影响既有调用方。
    rotation: int = 0
    rtl_ratio: float = 0.0

    @property
    def chars(self) -> int:
        return sum(len(ln.text.strip()) for ln in self.lines)


def _size_hist(lines: list[Line]) -> dict[float, int]:
    """CH-492（P-1）：单趟累计字号直方图。

    口径与 `s3_structure.modal_size` 的逐行累加**逐字节一致**——key 取
    `round(ln.size, 1)`（CH-485/R9-PIPE-2 的 0.1 精度纪律：10.5 不得被
    抬成 10.0），value 为 Σ max(1, len(ln.text.strip()))；`ln.size` 为 0
    的行不计入（DOCX 分页符行与 w:tbl 表格行 size 恒 0，与原 modal_size
    的 `if ln.size:` 同口径）。改此口径前先读 s3_structure.modal_size。
    """
    h: dict[float, int] = {}
    for ln in lines:
        if ln.size:
            k = round(ln.size, 1)
            h[k] = h.get(k, 0) + max(1, len(ln.text.strip()))
    return h


def sniff(path: str | Path) -> str:
    """magic 判定：非文本型 PDF / DOCX 必须是契约内的 415 / E4151。

    CH-451：原实现抛裸 `ValueError`，CLI 面逃到 `main()` 之外（栈回溯 + 退出码非契约值），
    API 面由 Flask 兜成 500 —— 与 CH-434（消毒失败裸 ValueError → 500）同一类错误：
    「输入安全」判定在两个入口都不可判定。
    """
    from ..errors import E4151_UnsupportedType
    p = Path(path)
    try:
        # CH-469：`with` 关闭句柄（原 `p.open("rb").read(8)` 依赖 GC 回收）
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        raise E4151_UnsupportedType(f"cannot read {p.name}: {exc}", detail={"file": p.name})
    if head.startswith(MAGIC_PDF):
        return "pdf"
    if head.startswith(MAGIC_ZIP):
        # CH-500（R17-PIPE-3）：ZIP 容器分三种结局，过去两种都折进同一句
        # 「unsupported magic」——截断/损坏的 .docx（下载不完整最常见）与
        # 「其实是别的 ZIP 改名成 .docx」（xlsx/epub/odt）对用户是完全不同的
        # 处置，却拿到一模一样的报错（实测逐字节同文，detail 还回显 PK magic，
        # 于是「类型不支持」把「文件坏了」彻底盖住）。原 `except Exception: pass`
        # 把 BadZipFile 直接吞掉，是这条误诊的根因。
        import zipfile
        try:
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise E4151_UnsupportedType(
                f"{p.name} is a damaged ZIP container, not a readable .docx: "
                f"{type(exc).__name__}",
                detail={"file": p.name, "magic": head.hex(),
                        "reason": "corrupt_zip"}) from None
        except OSError as exc:
            raise E4151_UnsupportedType(
                f"cannot read {p.name}: {type(exc).__name__}",
                detail={"file": p.name, "magic": head.hex(),
                        "reason": "zip_unreadable"}) from None
        if DOCX_REQUIRED_PART.decode() in names:
            return "docx"
        raise E4151_UnsupportedType(
            f"{p.name} is a ZIP container but has no word/document.xml "
            "(not a .docx — xlsx/epub/odt are not supported)",
            detail={"file": p.name, "magic": head.hex(), "reason": "not_a_docx"})
    if head.startswith(MAGIC_OLE) and p.suffix.lower() == ".doc":
        # Legacy Word binary. Parsing needs an external converter (S1
        # `read_legacy_doc`); magic is checked here so unsupported OLE files
        # (xls/ppt) still fail fast with E4151.
        return "doc"
    raise E4151_UnsupportedType(f"unsupported magic for {p.name}",
                                detail={"file": p.name, "magic": head.hex()})


def _pdf_tail_has_encrypt(path: str | Path) -> bool:
    """尽力而为的口令加密探测（CH-492 / F-05）：PDF 的 `/Encrypt` 条目
    （trailer 字典或 XRef 流字典）以明文存储（加密只覆盖字符串与流），
    读文件尾 128KB 匹配 `/Encrypt <n> <n> R` 或 `/Encrypt <<`。

    仅用于 open 失败后的诊断 flag（`detail.password_required`），不参与
    任何行为判定。有界风险：误报 = 损坏文件尾窗恰含明文 `/Encrypt`（要求
    literal token 出现在 trailer/xref 区，实践中近乎为零）；漏报 =
    `/Encrypt` 仅在 128KB 尾窗外的前代 xref 流（回退旧行为，与不探测相同）。
    """
    import re
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 128 * 1024))
            tail = f.read()
    except OSError:
        return False
    return re.search(rb"/Encrypt\s+(\d+\s+\d+\s+R|<<)", tail) is not None


def _open_source(open_fn, path: str | Path, kind: str):
    """源文档打开边界：打不开 → 415/E4151（方案 附录 D：magic 判定不支持的类型
    （含不可读）；打开源文件 `OSError` 同归此码）。

    CH-457（T6 大语料实测暴露）：魔术码合法但结构损坏的 PDF（截断 / 无 xref）在
    pdfplumber open 阶段抛 `PdfminerException`，原实现未映射 → 冒泡成 E5000 内部
    错误，「输入损坏」被误报成「工具故障」（CLI 退出码 5 而非 4，API 500 而非 415，
    任务终态 failed 且错误码不可判定）。只映射打开失败：页级抽取异常不在此冒充
    「不可读」，仍走既有失败路径（那是另一类失败，精度优先）。
    detail 只含文件名与异常类名（NFR-20：异常消息可能夹带文档内容，不带入）。
    """
    from ..errors import Doc2mdError, E4151_UnsupportedType
    try:
        return open_fn()
    except Doc2mdError:
        raise
    except Exception as exc:  # noqa: BLE001 — 打开边界统一收敛为「输入不可读」
        detail = {"file": Path(path).name, "exc": type(exc).__name__}
        if kind == "pdf" and _pdf_tail_has_encrypt(path):
            # CH-492（F-05）：口令加密与结构损坏在 pdfplumber 侧同为
            # PdfminerException（detail 无法区分）；尾窗 /Encrypt 明文探测
            # 给用户一个「可能需要口令」的诊断 flag（尽力而为，见 helper）。
            detail["password_required"] = True
        raise E4151_UnsupportedType(
            f"unreadable {kind}: {Path(path).name}",
            detail=detail,
        ) from exc


def read_pdf(path: str | Path, page_span: tuple[int, int] | None = None,
             chunk_pages: int | None = None, fmt: str | None = None) -> list[RawPage]:
    """页级行结构 + 字号 / 粗斜体（pdfplumber chars）。

    page_span=(start, end) 半开区间，供 S2 分片按页范围粗切使用。
    chunk_pages：每重新打开一次文档最多解析多少页（CH-447，低配机内存纪律）。
    fmt：调用方已嗅探的格式（CH-459 消除 convert 内二次 sniff 开文件；缺省仍自查）。
    """
    import pdfplumber
    out: list[RawPage] = []
    # CH-469：整读且不分块时不再为「数页数」单独 open 一次——那次 open 与首个
    # 分块的 open 重复解析 xref/trailer（320 页 / chunk=50 时共 8 次打开）。
    # CH-493（P-9）：分块模式同样懒取页数——首个分块的 handle 上就能读到
    # `len(pdf.pages)`，没必要先单独 open 一次只为数页（320 页 / chunk=32：
    # 11 次开 → 10 次开，重复解析的 xref/trailer 随页数线性变重）。
    # 唯一必须**前置**页数的是退化 page_span（上界 ≤ 下界），那里保持原口径：
    # 不开文件直接返回空。
    start, span_end = 0, None
    if page_span:
        start, span_end = max(0, page_span[0]), page_span[1]
        if span_end <= start:
            return out
    total, end = -1, 0
    step = chunk_pages if chunk_pages and chunk_pages > 0 else 0
    lo = start
    while total < 0 or lo < end:
        # 每个分块独立 open/close：pdfminer 的 LRUCachingObjectCache / 页对象
        # 随 PDF 实例一起释放，驻留峰值从「整本文档」降为「一个分块」。
        with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
            if total < 0:               # 页数在同一个 handle 上读出
                total = len(pdf.pages)
                end = total if span_end is None else min(total, span_end)
                if end <= start:
                    break
                if not step:
                    step = end - start
            hi = min(lo + step, end)
            for i in range(lo, hi):
                page = pdf.pages[i]
                chars = page.chars or []
                cs_index = _char_index(chars)
                raw_lines = page.extract_text_lines() or []
                geometry = _detect_two_column(page, chars, raw_lines)
                if geometry:
                    lines = _extract_two_column(page, raw_lines, *geometry)
                else:
                    lines = _lines_single_column(raw_lines, chars, cs_index)
                _attach_links(lines, [(ln.top, ln.bottom) for ln in lines], page)
                rp = RawPage(index=i, lines=lines,
                             height=float(getattr(page, "height", 0.0) or 0.0))
                rp.size_hist = _size_hist(lines)   # CH-492（P-1）：单趟直方图
                # O-10（C-28/F-12）：版式探测（只记录，判断与告警在 convert）
                rp.rotation = int(getattr(page, "rotation", 0) or 0)
                rp.rtl_ratio = _rtl_ratio(chars)
                out.append(rp)
                page.flush_cache()
        lo = hi                            # CH-493（P-9）：推进到下一个分块
    return out


def _rtl_ratio(chars: list[dict]) -> float:
    """O-10（C-28/F-12）：页内 RTL 字符占比（字母类字符为分母）。

    RTL 判定范围：希伯来 + 阿拉伯（含补充与呈现形式区）。分母只取
    「字母或 RTL」字符——标点/数字/空白不参与，避免数字型页脚把占比稀释到
    阈值之下。无字母字符 → 0.0（非 RTL 文档恒 0，不误报）。
    """
    if not chars:
        return 0.0
    rtl = total = 0
    for c in chars:
        t = str(c.get("text", "") or "")
        for ch in t:
            o = ord(ch)
            is_rtl = (0x0590 <= o <= 0x05FF or 0x0600 <= o <= 0x06FF
                      or 0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF
                      or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF)
            is_alpha = ch.isalpha()
            if is_rtl:
                rtl += 1
                total += 1
            elif is_alpha:
                total += 1
    return rtl / total if total else 0.0


def _edge_spaces(vis: list[dict], blanks: list[dict]) -> tuple[bool, bool]:
    """O-01（C-04）：行首 / 行尾空白几何证据（页 chars 里的空白字符）。

    `extract_text_lines` 的行字典（text/chars/x0/x1）把行缘空白整体剥掉，
    但 `page.chars` 里仍在。判据（宁漏勿误）——空白字符须**整体落在**
    首个可见字符之前 / 末个可见字符之后，且与行缘严格相邻：
    - 行内空白（两个可见字符之间的空格）不算：其 x1 越过首字符 x0、
      或 x0 早于末字符 x1，被相邻性排除（语料 2537 个空白的实测形态：
      绝大多数是行内空格，零个压在可见字形上）；
    - 距离行缘超过 `gap` 的不算：双栏页同 band 内另一栏的空白字符会被
      误计（两栏路径的 full 行用全页 chars 补证据，靠此边界收口）。
    `gap` 取行内最大字符宽度的 0.75 倍（下限 4pt）——同字号空格宽度量级，
    真空格恒在界内，栏间空档（≥ 12pt）恒在界外。
    """
    if not vis or not blanks:
        return False, False
    vis.sort(key=lambda c: float(c.get("x0", 0) or 0))
    first_x0 = float(vis[0].get("x0", 0) or 0)
    last_x1 = max(float(c.get("x1", 0) or 0) for c in vis)
    max_w = max((float(c.get("x1", 0) or 0) - float(c.get("x0", 0) or 0))
                for c in vis)
    gap = max(4.0, 0.75 * max_w)
    tol = 0.5                        # 横向测量容差：真实空格与行缘严格相邻
    leading = any(first_x0 - gap <= float(c.get("x0", 0) or 0)
                  and float(c.get("x1", 0) or 0) <= first_x0 + tol
                  for c in blanks)
    trailing = any(last_x1 - tol <= float(c.get("x0", 0) or 0)
                   and float(c.get("x1", 0) or 0) <= last_x1 + gap
                   for c in blanks)
    return leading, trailing


class _BlankIndex:
    """页内空白字形的带查询索引（与 `_slice_chars` 同 band 口径）。

    OPT4-17：原 `_blanks_in_band` 每次调用重扫整页字符，双栏路径逐行取三次，
    实测量级 32.9×–323× 的重复扫描。这里按 `top` 排序一次、二分定位带边界。
    返回结果保持 `chars` 的原始顺序——`_edge_spaces` 按 any() 消费，顺序无语义，
    但保持可让重构的等价性可被逐元素比对。
    """

    __slots__ = ("_tops", "_items")

    def __init__(self, chars: list[dict] | None):
        items = [(float(c.get("top", 0.0) or 0.0), i, c)
                 for i, c in enumerate(chars or [])
                 if not (c.get("text") or "").strip()]
        items.sort(key=lambda t: (t[0], t[1]))
        self._tops = [it[0] for it in items]
        self._items = items

    def in_band(self, top: float, bottom: float) -> list[dict]:
        lo = bisect_left(self._tops, top - 1.0)
        hi = bisect_left(self._tops, max(bottom, top + 1.0))
        band = self._items[lo:hi]
        if len(band) > 1:
            band = sorted(band, key=lambda t: t[1])
        return [c for _, _, c in band]


def _line_from_raw(raw: dict, lchars: list[dict],
                   edge_blanks: list[dict] | None = None) -> Line:
    """行字典 → Line。

    `edge_blanks`（O-01）：行缘空白证据的空白字符清单。**与 `lchars` 分离传入**——
    `_line_style` / `_row_cells` / `_char_width` 的口径不得因新增证据而漂移
    （G6：既有产物逐字节不变）。单栏路径的 `lchars` 本就是 band 全量 chars
    （含空白），缺省 None 时自行分离；两栏路径的行字典 chars 已被
    `extract_text_lines` 剥掉空白，须由调用方显式补（见 `_extract_two_column`）。
    """
    st = _line_style(lchars)
    vis = [c for c in lchars if (c.get("text") or "").strip()]
    if edge_blanks is None:
        blanks = [c for c in lchars if not (c.get("text") or "").strip()]
    else:
        blanks = edge_blanks
    lead, trail = _edge_spaces(vis, blanks)
    return Line(text=raw.get("text", ""), size=st[0],
                bold=st[1], italic=st[2], strike=st[3], mono=st[4],
                uniform_bold=st[5], uniform_italic=st[6], uniform_strike=st[7],
                row=_row_cells(lchars),
                x0=float(raw.get("x0", 0.0) or 0.0),
                char_w=_char_width(lchars) if st[4] else 0.0,
                top=float(raw.get("top", 0.0) or 0.0),
                bottom=float(raw.get("bottom", 0.0) or 0.0),
                trailing_space=trail, leading_space=lead)


def _lines_single_column(raw_lines: list[dict], chars: list[dict],
                         cs_index) -> list[Line]:
    lines: list[Line] = []
    for ln in raw_lines:
        top = float(ln.get("top", 0.0) or 0.0)
        bottom = float(ln.get("bottom", 0.0) or 0.0)
        lines.append(_line_from_raw(ln, _slice_chars(chars, cs_index, top, bottom)))
    return lines


def _detect_two_column(page, chars: list[dict], raw_lines: list[dict]) -> tuple[float, float] | None:
    """Detect a two-column body from a low-density central gutter.

    Full-width title/header bands are excluded from the gutter density so a
    spanning title does not hide the columns underneath it.
    """
    height = float(getattr(page, "height", 0.0) or 0.0)
    width = float(getattr(page, "width", 0.0) or 0.0)
    if height <= 0 or width <= 0:
        return None
    body = [c for c in chars if (c.get("text") or "").strip()
            and height * 0.08 <= float(c.get("top", 0.0)) <= height * 0.92]
    if len(body) < 40:
        return None
    mid, gutter = width / 2, 12.0
    left = [c for c in body if float(c.get("x0", 0.0)) < mid - gutter / 2]
    right = [c for c in body if float(c.get("x0", 0.0)) > mid + gutter / 2]
    if len(left) < 0.2 * len(body) or len(right) < 0.2 * len(body):
        return None
    def y_span(cs: list[dict]) -> float:
        return (max(float(c.get("bottom", 0.0)) for c in cs)
                - min(float(c.get("top", 0.0)) for c in cs))
    if y_span(left) < max(40.0, 0.10 * height) or y_span(right) < max(40.0, 0.10 * height):
        return None
    # A real gutter is a consistent horizontal gap between left and right text
    # runs. A single-column line may leave the exact centre band empty because
    # CJK glyph boxes do not cover it, so density alone is not enough.
    bands: dict[int, list[dict]] = {}
    for c in body:
        bands.setdefault(round(float(c.get("top", 0.0)) / 2), []).append(c)
    both = large = contiguous = 0
    for cs in bands.values():
        if any(float(c.get("x0", 0.0)) < mid < float(c.get("x1", 0.0)) for c in cs):
            contiguous += 1
            both += 1
            continue
        left_cs = [c for c in cs if float(c.get("x1", 0.0)) <= mid]
        right_cs = [c for c in cs if float(c.get("x0", 0.0)) >= mid]
        if not left_cs or not right_cs:
            continue
        both += 1
        gap = (min(float(c.get("x0", 0.0)) for c in right_cs)
               - max(float(c.get("x1", 0.0)) for c in left_cs))
        if gap >= gutter / 2:
            large += 1
        elif gap < 3.0:
            contiguous += 1
    if both < 6 or large < 0.6 * both or contiguous > 0.2 * both:
        return None
    return mid, gutter


def _extract_two_column(page, raw_lines: list[dict], mid: float,
                        gutter: float) -> list[Line]:
    """Extract left/right crops and concatenate in column reading order."""
    bbox = page.bbox
    left_page = page.crop((bbox[0], bbox[1], mid - gutter / 2, bbox[3]))
    right_page = page.crop((mid + gutter / 2, bbox[1], bbox[2], bbox[3]))
    left_raw = left_page.extract_text_lines() or []
    right_raw = right_page.extract_text_lines() or []
    left_chars = left_page.chars or []
    right_chars = right_page.chars or []
    page_chars = page.chars or []
    height = float(page.height)
    body_raw = [ln for ln in left_raw + right_raw
                if float(ln.get("top", 0.0)) >= height * 0.08]
    col_top = min((float(ln.get("top", 0.0)) for ln in body_raw), default=0.0)
    col_bottom = max((float(ln.get("bottom", 0.0)) for ln in body_raw), default=height)
    full_raw = [ln for ln in raw_lines
                if float(ln.get("x0", 0.0)) < mid - gutter / 2
                and float(ln.get("x1", 0.0)) > mid + gutter / 2
                and (float(ln.get("top", 0.0)) < col_top - 2.0
                     or float(ln.get("bottom", 0.0)) > col_bottom + 2.0)]
    full_tops = sorted(float(ln.get("top", 0.0)) for ln in full_raw)

    def keep(ln) -> bool:
        # OPT4-17：原实现对每个候选行重扫 full_tops（O(行数×跨栏行数)）。
        # 排序后二分定位最近候选——只有紧邻 top±2.0 的可能命中，左邻取一位
        # 是为浮点边界（t 落在 top-2.0 下侧一个 ULP 内）兜底。
        top = float(ln.get("top", 0.0))
        i = bisect_left(full_tops, top - 2.0)
        return all(abs(top - t) > 2.0 for t in full_tops[max(0, i - 1):i + 2])

    left_blanks = _BlankIndex(left_chars)
    right_blanks = _BlankIndex(right_chars)
    page_blanks = _BlankIndex(page_chars)

    def edge_blanks_for(ln, idx: _BlankIndex) -> list[dict]:
        # O-01：两栏路径的行字典 chars 已被 extract_text_lines 剥掉空白，
        # 行缘空白证据改从 crop 后的 chars 里补（band 口径同 _slice_chars）。
        # full 行用全页 chars：行缘 gap 边界（见 _edge_spaces）天然排除
        # 栏间 / 他栏空白，只留本行自己的行首尾空格。
        return idx.in_band(float(ln.get("top", 0.0) or 0.0),
                           float(ln.get("bottom", 0.0) or 0.0))

    left = [_line_from_raw(ln, ln.get("chars") or [],
                           edge_blanks_for(ln, left_blanks))
            for ln in left_raw if keep(ln)]
    right = [_line_from_raw(ln, ln.get("chars") or [],
                            edge_blanks_for(ln, right_blanks))
             for ln in right_raw if keep(ln)]
    full = [_line_from_raw(ln, ln.get("chars") or [],
                           edge_blanks_for(ln, page_blanks))
            for ln in full_raw]
    col = left + right
    if not col:
        return full
    min_top = min(ln.top for ln in col)
    max_bottom = max(ln.bottom for ln in col)
    prefix = [ln for ln in full if ln.top < min_top]
    suffix = [ln for ln in full if ln.top > max_bottom]
    middle = [ln for ln in full if min_top <= ln.top <= max_bottom]
    return prefix + left + middle + right + suffix


def _attach_links(lines: list[Line], geoms: list[tuple[float, float]], page) -> None:
    """CH-459（FR-107/108）：Link 标注按几何覆盖附着到行。

    只认两类动作：URI（外部，`link`）与 GoTo（内部，`link_internal` + `link_dest`）；
    其余动作（Launch / JavaScript 等）不抽——执行类链接不是本管线的消费对象。
    一个标注只附着到第一根相交的行（审阅面每块只消费一个 URL，多链接块按保守口径）。
    """
    for a in (getattr(page, "annots", None) or []):
        if not isinstance(a, dict):
            continue
        atop, abot = a.get("top"), a.get("bottom")
        if atop is None or abot is None:
            continue
        uri = a.get("uri")
        dest = ""
        if uri:
            uri = str(uri)
        else:
            action = (a.get("data") or {}).get("A") or {}
            if not str(action.get("S", "")).endswith("GoTo"):
                continue                      # 只认 URI / GoTo，其余动作不抽
            d = action.get("D")
            if isinstance(d, bytes):
                dest = d.decode("utf-8", "replace")
            elif isinstance(d, (list, tuple)) and d:
                # 显式目标数组 [page, /XYZ, x, y, z]（与命名目标同样常见）：
                # 取页号作 dest；原 str(list) 会产出 "['/XYZ', ...]" 垃圾串
                try:
                    dest = str(int(d[0]))
                except (TypeError, ValueError):
                    dest = ""
            else:
                dest = str(d or "")
        for ln, (top, bottom) in zip(lines, geoms):
            if ln.link or ln.link_internal:
                continue
            # 行带与标注带在纵向相交（标注通常只覆盖链接文字那一行的条带）
            if float(atop) < bottom and float(abot) > top:
                if uri:
                    ln.link = uri
                else:
                    ln.link_internal = True
                    ln.link_dest = dest
                break


# CH-493（P-9）：删去 `_pdf_page_count`。页数改由首个分块的 handle 读取后，
# 该函数再无调用点（`tests/test_ch491_doc_sync.py` 的死代码红线随即判出）。


def _char_index(chars: list[dict]) -> tuple[list[int], list[float]]:
    """预排序索引：(原始下标序, 对应的 top 升序序列)。

    _line_style 原本对每行做一次 O(全页 chars) 线性过滤，实测是 S1 的 CPU 热点
    （15.6k 行 × 每行重扫 → 单文件 320 页 profile 里占 27/100s）。
    这里用 bisect 取区间，但**取回的下标会再排序还原成原始 char 顺序**，
    使众数平票时的 `Counter.most_common` 平局判定与逐行全扫完全一致
    （实测 120 页 / 5613 行 0 处不符）。
    """
    tops = [float(c.get("top", 0.0)) for c in chars]
    order = sorted(range(len(chars)), key=lambda j: (tops[j],
                                                     float(chars[j].get("x0", 0.0))))
    return order, [tops[j] for j in order]


def _slice_chars(chars: list[dict], index: tuple[list[int], list[float]],
                 top: float, bottom: float) -> list[dict]:
    from bisect import bisect_left
    order, sorted_tops = index
    lo = bisect_left(sorted_tops, top - 1.0)
    hi = bisect_left(sorted_tops, max(bottom, top + 1.0))
    if lo >= hi:
        return []
    return [chars[j] for j in sorted(order[lo:hi])]


def _line_style(chars: list[dict]) -> tuple[
        float, bool, bool, bool, bool, bool, bool, bool]:
    """行内众数字号 + 多数派样式 + 逐字符统一格式（uniform_*）。

    CH-485（R9-PIPE-5 / R9-PERF-1）：一趟同时算 size 与四个计数，并按 fontname
    缓存字体派生位；`uniform_*` 要求**所有非空白字符**都带该格式，而不是行级
    60% 多数派——后者会把“局部粗体”扩散成整段 `**`。
    """
    cs = chars
    if not cs:
        return (0.0, False, False, False, False, False, False, False)
    sizes: dict[float, int] = {}
    n = len(cs)
    b = i = s = m = 0
    ub = ui = us = 0
    nvis = 0
    cache: dict[str, tuple[bool, bool, bool]] = {}
    for c in cs:
        size = round(float(c.get("size", 0.0)), 1)
        sizes[size] = sizes.get(size, 0) + 1
        fn = str(c.get("fontname", "")).lower()
        flags = cache.get(fn)
        if flags is None:
            flags = ("bold" in fn or "black" in fn,
                     "italic" in fn or "oblique" in fn,
                     any(h in fn for h in _MONO_HINTS))
            cache[fn] = flags
        is_b = bool(c.get("bold")) or flags[0] or c.get("weight", 0) >= 600
        is_i = bool(c.get("italic")) or flags[1]
        is_s = bool(c.get("strike"))
        is_m = flags[2]
        b += is_b
        i += is_i
        s += is_s
        m += is_m
        if (c.get("text") or "").strip():
            nvis += 1
            ub += is_b
            ui += is_i
            us += is_s
    size = max(sizes.items(), key=lambda kv: kv[1])[0]
    return (size, b / n >= 0.6, i / n >= 0.6, s / n >= 0.6, m / n >= 0.6,
            nvis > 0 and ub == nvis, nvis > 0 and ui == nvis,
            nvis > 0 and us == nvis)


def _is_mono(c: dict) -> bool:
    return any(h in str(c.get("fontname", "")).lower() for h in _MONO_HINTS)


def _char_width(chars: list[dict]) -> float:
    """CH-482：行内字符宽度中位数（pt）。

    `Line.x0` 是从**行首空白**起算的，`(x1-x0)/len(text)` 会把缩进空格摊进字宽
    （空白已被 `extract_text_lines` 从 text 剥掉，但仍在 chars/几何里）。逐字符取宽
    再取中位数不受行首空白影响；等宽字体下空格与可见字符同宽，故不区分空白。
    """
    widths = []
    for c in chars:
        if not (c.get("text") or ""):
            continue
        w = float(c.get("x1", 0.0) or 0.0) - float(c.get("x0", 0.0) or 0.0)
        if w > 0:
            widths.append(w)
    if not widths:
        return 0.0
    widths.sort()
    return widths[len(widths) // 2]


def _row_cells(chars: list[dict]) -> list[str] | None:
    """CH-459（FR-104 表格）：行内字符按横向间隙聚类成单元格。

    间隙阈值 = max(8pt, 1.6 × 行内字符宽度中位数)——单空格（约 1/3 字宽）不会误切，
    真正的列间空档（≥ 1.6 字宽）才分列。返回 None 表示「不是多列行」（单列/无间隙），
    由 S3 结合连续性判定是否成表（单行多列可能是排版性空距，不是表格）。
    """
    cs = [c for c in chars if (c.get("text") or "").strip()]
    if len(cs) < 2:
        return None
    # CH-464：聚类假设 x 升序，但内容流不保证（双栏回写、生成器先画右列）——
    # 乱序时整行被切碎或判成单列。按 x0 排序后再聚类。
    cs.sort(key=lambda c: float(c.get("x0", 0) or 0))
    widths = sorted(float(c.get("x1", 0) or 0) - float(c.get("x0", 0) or 0) for c in cs)
    median_w = widths[len(widths) // 2] or 1.0
    gap_th = max(8.0, 1.6 * median_w)
    cells: list[list[str]] = [[cs[0].get("text", "")]]
    prev_x1 = float(cs[0].get("x1", 0) or 0)
    for c in cs[1:]:
        x0 = float(c.get("x0", 0) or 0)
        if x0 - prev_x1 > gap_th:
            cells.append([])
        cells[-1].append(c.get("text", ""))
        prev_x1 = max(prev_x1, float(c.get("x1", 0) or 0))
    if len(cells) < 2:
        return None
    return ["".join(seg).strip() for seg in cells]


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


@dataclass
class _DocxCtx:
    """一次 `read_docx` 的文档级预检 / 记忆化状态（CH-476 / R7-PERF-d、g）。

    `styles`：`w:pStyle/@w:val` → 样式名。python-docx 的 `paragraph.style` 每次访问
    都调 `Styles.get_by_id` → 未命中显式样式时 `default_for()` 重扫整张 styles part
    （对每个 `w:style` 做 enum 解析），实测 185µs/段。样式种类只有几十，按 style_id
    记忆化即把 O(段 × 样式) 降为 O(样式)。缓存的是**样式名字符串**（本层只用
    `style.name`），不缓存 python-docx 样式对象，避免跨段共享可变代理。
    `has_links`：该容器子树内是否存在 `w:hyperlink`。无则整篇跳过逐段 XPath——
    预检覆盖 body 全集，跳过条件与「该段 findall 返回空」严格等价。
    """
    styles: dict = field(default_factory=dict)
    has_links: bool = True
    has_sdt: bool = False
    has_custom_xml: bool = False
    has_fields: bool = False       # w:fldSimple（DATE/REF 等域结果）
    has_smart_tag: bool = False    # w:smartTag 包裹的 run
    has_textbox: bool = False      # 有文本框时才做全树遍历
    numbering: "_DocxNumbering | None" = None


def _docx_ctx(root, numbering=None) -> _DocxCtx:
    """按遍历容器建预检上下文（body 与各 notes part 各一份，样式缓存由调用方共享）。"""
    return _DocxCtx(
        has_links=root.find(f".//{_W_NS}hyperlink") is not None,
        has_sdt=root.find(f".//{_W_NS}sdt") is not None,
        has_custom_xml=root.find(f".//{_W_NS}customXml") is not None,
        has_fields=root.find(f".//{_W_NS}fldSimple") is not None,
        has_smart_tag=root.find(f".//{_W_NS}smartTag") is not None,
        has_textbox=root.find(f".//{_W_NS}txbxContent") is not None,
        numbering=numbering)


class _DocxNumbering:
    """CH-483（R8-PIPE-4）：`numbering.xml` + 样式级 `w:numPr` 的编号解析。

    只负责“第几项 / 什么标记 / 几级”；排版与缩进由 S3/S4 决定。无法解析的
    格式回退为 decimal，绝不因为编号缺失丢掉正文。
    """

    def __init__(self, d):
        self.levels: dict[str, dict[int, dict]] = {}
        self.style_num: dict[str, tuple[str, int]] = {}
        self.pstyle_num: dict[str, tuple[str, int]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        try:
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
            from docx.oxml import parse_xml
            part = d.part.part_related_by(RT.NUMBERING)
        except (KeyError, ImportError):
            return
        try:
            root = parse_xml(part.blob)
        except Exception:
            return
        def read_lvl(lvl) -> tuple[int, dict] | None:
            try:
                ilvl = int(lvl.get(f"{_W_NS}ilvl", "0"))
            except ValueError:
                return None
            fmt = lvl.find(f"{_W_NS}numFmt")
            txt = lvl.find(f"{_W_NS}lvlText")
            start = lvl.find(f"{_W_NS}start")
            pstyle = lvl.find(f"{_W_NS}pStyle")
            return ilvl, {
                "fmt": (fmt.get(f"{_W_NS}val", "decimal") if fmt is not None
                        else "decimal"),
                "text": (txt.get(f"{_W_NS}val", "") if txt is not None else ""),
                # CH-504：区分「lvlText 元素存在但为空」（= 该级不显示编号）与
                # 「元素缺失」（畸形文档，保留 decimal 兜底）。两者都读成 ""
                # 会把前者误判成后者。
                "has_text": txt is not None,
                "start": int(start.get(f"{_W_NS}val", "1")) if start is not None else 1,
                # CH-508（B-52）：该级绑定的段落样式；应用此样式的段落自动获得
                # 本级编号（ECMA-376 §17.9.29）。
                "pstyle": (pstyle.get(f"{_W_NS}val", "") if pstyle is not None else ""),
            }

        # --- 样式侧：直接 numPr + basedOn 链 + 编号样式（供 numStyleLink 解析）---
        style_own: dict[str, tuple[str, int]] = {}
        style_base: dict[str, str] = {}
        try:
            styles = d.styles.element
        except Exception:
            styles = None
        if styles is not None:
            for st in styles.findall(f"{_W_NS}style"):
                sid = st.get(f"{_W_NS}styleId", "")
                if not sid:
                    continue
                base_el = st.find(f"{_W_NS}basedOn")
                if base_el is not None:
                    style_base[sid] = base_el.get(f"{_W_NS}val", "")
                numPr = st.find(f"{_W_NS}pPr/{_W_NS}numPr")
                if numPr is None:
                    continue
                nid_el = numPr.find(f"{_W_NS}numId")
                lvl_el = numPr.find(f"{_W_NS}ilvl")
                if nid_el is None:
                    continue
                style_own[sid] = (
                    nid_el.get(f"{_W_NS}val", ""),
                    int(lvl_el.get(f"{_W_NS}val", "0")) if lvl_el is not None else 0)

        def style_num_of(sid: str, seen: set[str] | None = None):
            """样式自身的 numPr，否则沿 basedOn 链继承（CH-508 / B-52）。

            只追 basedOn，不追「编号样式」——后者是 numStyleLink 的方向。
            环状 basedOn 返回 None（不猜、不造编号）。
            """
            if sid in style_own:
                return style_own[sid]
            seen = set() if seen is None else seen
            if sid in seen:
                return None
            seen.add(sid)
            base = style_base.get(sid, "")
            return style_num_of(base, seen) if base else None

        for sid in set(style_own) | set(style_base):
            got = style_num_of(sid)
            if got is not None:
                self.style_num[sid] = got

        # --- 编号定义侧：抽象层 + numStyleLink 委托 ---
        abstract: dict[str, dict[int, dict]] = {}
        abstract_link: dict[str, str] = {}
        for an in root.findall(f"{_W_NS}abstractNum"):
            aid = an.get(f"{_W_NS}abstractNumId", "")
            lv: dict[int, dict] = {}
            for lvl in an.findall(f"{_W_NS}lvl"):
                parsed = read_lvl(lvl)
                if parsed is not None:
                    lv[parsed[0]] = parsed[1]
            abstract[aid] = lv
            link_el = an.find(f"{_W_NS}numStyleLink")
            if link_el is not None:
                abstract_link[aid] = link_el.get(f"{_W_NS}val", "")

        num_aid: dict[str, str] = {}
        num_override: dict[str, list[tuple[int, dict | None, int | None]]] = {}
        for num in root.findall(f"{_W_NS}num"):
            nid = num.get(f"{_W_NS}numId", "")
            aid_el = num.find(f"{_W_NS}abstractNumId")
            num_aid[nid] = aid_el.get(f"{_W_NS}val", "") if aid_el is not None else ""
            ovs: list[tuple[int, dict | None, int | None]] = []
            for ov in num.findall(f"{_W_NS}lvlOverride"):
                try:
                    oilvl = int(ov.get(f"{_W_NS}ilvl", "0"))
                except ValueError:
                    continue
                lvl_el = ov.find(f"{_W_NS}lvl")
                parsed = read_lvl(lvl_el) if lvl_el is not None else None
                start_el = ov.find(f"{_W_NS}startOverride")
                start = None
                if start_el is not None:
                    try:
                        start = int(start_el.get(f"{_W_NS}val", "1"))
                    except (KeyError, ValueError):
                        pass
                ovs.append((oilvl, parsed[1] if parsed is not None else None, start))
            num_override[nid] = ovs

        def levels_for(nid: str, seen: set[str] | None = None) -> dict[int, dict]:
            """该 numId 的有效级别定义：抽象层 / numStyleLink 委托 + 实例覆盖。"""
            seen = set() if seen is None else seen
            if nid in seen:
                return {}
            seen.add(nid)
            aid = num_aid.get(nid, "")
            link = abstract_link.get(aid, "")
            if link:
                linked = style_num_of(link)
                target = linked[0] if linked is not None else ""
                base = levels_for(target, seen) if target else {}
            else:
                base = {i: dict(c) for i, c in abstract.get(aid, {}).items()}
            for oilvl, cfg, start in num_override.get(nid, []):
                if cfg is not None:
                    base[oilvl] = dict(cfg)
                if start is not None and oilvl in base:
                    base[oilvl]["start"] = start
            return base

        for nid in num_aid:
            self.levels[nid] = levels_for(nid)

        # 级别 → 段落样式的反向映射：未挂 numPr 的该样式段落也获得编号。
        for nid, levels in self.levels.items():
            for ilvl, cfg in levels.items():
                ps = cfg.get("pstyle") or ""
                if ps and ps not in self.pstyle_num:
                    self.pstyle_num[ps] = (nid, ilvl)

    @staticmethod
    def _fmt_num(n: int, fmt: str) -> str:
        # CH-495（R14-PIPE-2）：罗马数值表原只到 10——n=40 渲染成 "xxxx"
        # （应为 "xl"）、50 → "xxxxx"；补全到 1000 与 Word 口径一致。
        # 字母序原按 %26 回绕——第 27 项变回 "a"（与首项撞号）；改为 Word 的
        # 双字母续接口（z 之后是 aa、ab…，bijective base-26）。
        if fmt in ("lowerLetter", "upperLetter"):
            k, out = max(1, n), ""
            while k > 0:
                k, r = divmod(k - 1, 26)
                out = chr(ord("a") + r) + out
            return out.upper() if fmt == "upperLetter" else out
        if fmt in ("lowerRoman", "upperRoman"):
            vals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
                    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
            out, k = "", max(1, n)
            for v, s in vals:
                while k >= v:
                    out += s
                    k -= v
            return out.upper() if fmt == "upperRoman" else out
        return str(n)

    def marker(self, el, style_id: str | None) -> tuple[str, int]:
        numPr = el.find(f".//{_W_NS}numPr")
        nid = lvl = None
        if numPr is not None:
            nid_el = numPr.find(f"{_W_NS}numId")
            lvl_el = numPr.find(f"{_W_NS}ilvl")
            nid = nid_el.get(f"{_W_NS}val", "") if nid_el is not None else None
            lvl = int(lvl_el.get(f"{_W_NS}val", "0")) if lvl_el is not None else 0
        if nid is None and style_id:
            # CH-508（B-52）：样式自身 / basedOn 继承的 numPr 优先；其次是该样式
            # 在某个级别 `w:lvl/w:pStyle` 上的绑定。
            got = self.style_num.get(style_id) or self.pstyle_num.get(style_id)
            if got is not None:
                nid, lvl = got
        if not nid or nid == "0":
            return "", -1
        levels = self.levels.get(nid) or {}
        ilvl = int(lvl or 0)
        cfg = levels.get(ilvl) or levels.get(0)
        if not cfg:
            return "", -1
        counters = self.counters.setdefault(nid, {})
        for i in range(ilvl + 1):
            if i not in counters:
                counters[i] = int((levels.get(i) or {}).get("start", 1)) - 1
        counters[ilvl] += 1
        for i in list(counters):
            if i > ilvl:
                counters[i] = int((levels.get(i) or {}).get("start", 1)) - 1
        # CH-504：`numFmt="none"` 或空 `lvlText` = 该级不显示编号（ECMA-376
        # §17.9.19）。计数器已在上面推进，深层 `%2` 之类引用仍算得对，只是本级
        # 不产出标记。原实现对空 lvlText 兜底成 `"%1."`，于是 LibreOffice 写出的
        # 标题样式（Heading1..9 挂样式级 numPr → numFmt=none）被凭空加上 `1. `
        # 前缀——文档里不存在的字符进了交付正文，且不留告警。
        if cfg["fmt"] == "none" or (cfg["has_text"] and not cfg["text"]):
            return "", -1
        if cfg["fmt"] == "bullet":
            # CH-485（R9-PIPE-3）：lvlText 可能是 "o"/"§" 等任意字形；bullet
            # 的语义是项目符号，不能让它落入 S3 的“非数字→1.”归一。
            return "•", ilvl

        def repl(m):
            idx = int(m.group(1)) - 1
            lc = levels.get(idx) or {}
            val = counters.get(idx, int(lc.get("start", 1)))
            return self._fmt_num(val, str(lc.get("fmt", "decimal")))

        import re as _re
        marker = _re.sub(r"%(\d)", repl, cfg["text"] or "%1.")
        return marker, ilvl


def _docx_style_name(el, d, ctx: _DocxCtx) -> str:
    """段落样式名（记忆化）。与 `p.style.name` 同口径：无显式样式 / 样式不存在时
    python-docx 回落到默认段落样式，本函数走同一 `Styles.get_by_id`，只是每个
    style_id 只解析一次；样式缺失（返回 None）时同样得到空串。
    """
    sid = el.style                                  # CT_P.style：读 w:pStyle/@w:val，无全表扫
    if sid not in ctx.styles:
        from docx.enum.style import WD_STYLE_TYPE
        st = d.styles.get_by_id(sid, WD_STYLE_TYPE.PARAGRAPH)
        ctx.styles[sid] = (st.name or "") if st is not None else ""
    return ctx.styles[sid]



def _heading_level_from_style(style_name: str) -> int | None:
    """`Heading N`（N=1..9）→ 层级；无数字的自定义样式名不算标题。

    CH-508（B-52 / F-7）：原实现只要前缀是 `heading` 就把末段整数当层级，裸
    `Heading` 走异常兜底成 1 级——一个自定义样式仅因命名为 `Heading` 就被判成
    H1（连字号也按 H1 给）。规范只约定 `Heading N`，无数字名不构成层级声明。
    """
    s = (style_name or "").strip().lower()
    if not s.startswith("heading"):
        return None
    tail = s[len("heading"):].strip()
    if not tail.isdigit():
        return None
    n = int(tail)
    return min(6, n) if 1 <= n <= 9 else None


def _docx_para_line(p, d, rels=None, ctx: _DocxCtx | None = None) -> Line | None:
    """段落 → Line（含 CH-459 的等宽 / 超链接 / 标题层级提示 / 分页符信号）。

    CH-476（R7-GAP-1，决策 D4「提取而非拒收」）：含修订痕迹的段落改走「接受后」口径。
    python-docx 的 `p.text` / `p.runs` 只认 `w:p` 直接子级里的 `w:r`，被 `w:ins`
    包裹的插入 run 不在其中（实测 `p.text` 里 "inserted" 消失）——修订插入的正文
    会整段静默丢失。无修订的段落走原路径，产物字节不变（G6）。

    `rels`（CH-476 / R7-GAP-2）：超链接关系集合，默认取 document part 的 rels；
    脚注 / 尾注 part 调用时必须传自己的 rels——rId 只在所属 part 的关系表内有意义，
    拿错关系表会把链接指到别的 target（制造错误数据）。
    `ctx`（CH-476 / R7-PERF-d、g）：文档级预检与样式记忆化；缺省新建（等价于
    「不跳过任何段、不共享缓存」，即原行为），供独立调用与测试使用。
    """
    el = p._element
    if ctx is None:
        ctx = _DocxCtx()
    rev = _docx_has_revision(el)
    # CH-483（R8-PIPE-2）：只要容器里可能藏着 `w:sdt` / `w:customXml` / 超链接，
    # 或段落本身有修订，就必须走「全部 run」的接受后视图——python-docx 的
    # `p.text`/`p.runs` 只认 `w:p` 直接子级，sdt 内的文本会整块消失。
    use_all = (rev or ctx.has_sdt or ctx.has_custom_xml or ctx.has_links
               or ctx.has_fields or ctx.has_smart_tag)
    if use_all:
        runs = _docx_all_runs(p)
        # `w:del` 里的文本是 `w:delText`，`Run.text` 本就不取 → 拼出来即「接受后」正文
        raw_text = "".join(r.text or "" for r in runs)
    else:
        runs = p.runs
        raw_text = p.text or ""
    if not raw_text.strip():
        return None
    size = 0.0
    bold = italic = strike = mono = False
    # CH-500（R17-PIPE-6）：字号取**可见字符加权的众数**，不再是「最后一个带显式
    # 字号的 run」。原循环逐 run 覆写 `size`，于是段内 run 的**排列顺序**决定
    # 结果（实测同一对 run 换序：22pt 在前记 10.0、10pt 在前记 22.0，短 pair 里
    # 后者直接被判成 `heading level 1`）。字号往下游喂给 `_heading_level` /
    # `modal_size`，「这行正文有多大」应当只由正文内容量决定。众数口径与 PDF 侧
    # `_line_flags` 的 `max(sizes.items(), key=kv[1])` 同构（并列取先出现的 run，
    # 与 dict 插入序一致），无显式字号的 run 不投票（保持 0 = 继承，行为不变）。
    sizes: dict[float, int] = {}
    for r in runs:
        if r.font.size is not None:
            pt = float(r.font.size.pt)
            size = pt
            n = len((r.text or "").strip())
            sizes[pt] = sizes.get(pt, 0) + max(1, n)
        bold = bold or bool(r.bold)
        italic = italic or bool(r.italic)
        strike = strike or bool(r.font.strike)
        mono = mono or _name_is_mono(r.font.name)
    if sizes:
        size = max(sizes.items(), key=lambda kv: kv[1])[0]
    nonempty = [r for r in runs if (r.text or "").strip()]
    uniform_bold = bool(nonempty) and all(bool(r.bold) for r in nonempty)
    uniform_italic = bool(nonempty) and all(bool(r.italic) for r in nonempty)
    uniform_strike = bool(nonempty) and all(bool(r.font.strike) for r in nonempty)
    # CH-482：等宽段的行首空白（空格 / `w:tab`）是代码内容，DOCX 没有 x0 几何可重建，
    # 丢了就无从恢复——只对等宽行保留前导空白，散文行仍走 strip（原口径不变）。
    text = raw_text.rstrip() if mono else raw_text.strip()
    # O-01（QA-D2M-2026-10-21 / C-04）：行缘空白证据。散文行的 text 已 strip，
    # 原文本的行首 / 行尾空白只在此留证——DOCX 行是显式段落（explicit_para），
    # 常规续段路径不会跨段拼接，唯一例外是标题折行合并（heading merge 不查
    # explicit_para），其补空格判断需要这两个旗标。等宽行的前导空白已保留在
    # text 内，不再设 leading（否则拼接时重复补空格）。
    trailing_space = raw_text != raw_text.rstrip()
    leading_space = (not mono) and raw_text != raw_text.lstrip()
    style = _docx_style_name(el, d, ctx)
    level = _heading_level_from_style(style)
    is_heading = level is not None
    marker, list_level = "", -1
    if ctx.numbering is not None:
        # CH-485（R9-PIPE-3）：Heading 的自动编号也要保留（原实现跳过，
        # 标题文本只剩“方法”）；S3 会用 level_hint 优先，不会把它当列表。
        marker, list_level = ctx.numbering.marker(el, el.style)
    link, link_internal, link_dest = _docx_para_link(p, d, text, rels, ctx.has_links)
    return Line(text=text, size=size or _style_size(style),
                bold=bold or is_heading,
                italic=italic, strike=strike, mono=mono,
                uniform_bold=uniform_bold or is_heading,
                uniform_italic=uniform_italic, uniform_strike=uniform_strike,
                list_marker=marker, list_level=list_level,
                link=link, link_internal=link_internal, link_dest=link_dest,
                level_hint=level, has_revision=rev, explicit_para=True,
                trailing_space=trailing_space, leading_space=leading_space)


def _docx_has_revision(el) -> bool:
    """子树内是否有 `w:ins` / `w:del`（单次 `iter` 短路，不做两遍 xpath）。"""
    marks = (_W_NS + "ins", _W_NS + "del")
    return any(e.tag in marks for e in el.iter())


def _inside_textbox(el, stop=None) -> bool:
    """el 的祖先链上是否有文本框/绘图边界（CH-495：可指定止步元素）。

    `stop` 非空时只检查 el 到 stop（不含）之间的祖先——用于判定「run 是否属于
    段落 p 自己这一层」：p 在 txbxContent 里时，p 的直接 run 不算「在文本框内」
    （相对 p 无边界），而 p 内**嵌套**文本框的 run 算（相对 p 有 drawing 边界）。
    """
    tags = (_W_NS + "drawing", _W_NS + "txbxContent", _MC_NS + "AlternateContent")
    for a in el.iterancestors():
        if stop is not None and a is stop:
            return False
        if a.tag in tags:
            return True
    return False


def _docx_all_runs(p) -> list:
    """段落内**全部** run（含 `w:ins` / `w:hyperlink` 包裹者），包装成 python-docx Run。

    只在 `_docx_has_revision` 命中时调用；`Run.text` 沿用 python-docx 口径
    （`w:tab`→\\t、`w:br`/`w:cr`→\\n、`w:delText` 不取），故与 `p.text` 同形。
    """
    from docx.text.run import Run
    # CH-485（R9-PIPE-1）：文本框/绘图里的 run 由 _docx_textbox_paragraphs 单独
    # 产出；若在此一并拼入宿主段落，会重复（AlternateContent 双分支还会翻倍）。
    # CH-495（R14-PIPE-1）：边界改按「相对段落 p」判定——txbxContent 内的段落
    # 收自己的全部 run（原 include_textbox=True 的口径），但其内嵌套文本框的
    # run 归嵌套层段落单独产出，不再拼进宿主（原口径下嵌套文本一段文本 ×2，
    # 叠加 Choice/Fallback 双分支扁平遍历可达 ×4）。
    pe = p._element
    return [Run(r, p) for r in pe.iter(f"{_W_NS}r")
            if not _inside_textbox(r, stop=pe)]


def _name_is_mono(name: str | None) -> bool:
    return bool(name) and any(h in name.lower() for h in _MONO_HINTS)


def _docx_para_link(p, d, para_text: str, rels=None,
                    has_links: bool = True) -> tuple[str | None, bool, str]:
    """CH-459（FR-107/108）：段落内 `w:hyperlink` → (外部 URL, 是否内部, 锚点)。

    保守口径：只有超链接文字**完整覆盖**段落文本时才附着——行内局部链接
    （链接文字只是段落的一部分）本轮不抽：块级 links 模型无法表达「哪一段文字」
    是链接，硬附会把整块文本包成 `[整块](url)`，属于制造错误数据。

    `rels`：关系表；缺省用 `d.part.rels`（正文段落）。脚注 / 尾注传各自 part 的
    rels（CH-476 / R7-GAP-2），否则 rId 会撞进正文关系表指向错的 target。
    `has_links`（CH-476 / R7-PERF-d）：调用方已确认整个容器子树无 `w:hyperlink`
    时直传 False——该段 findall 必为空，跳过与否逐字节等价，省掉每段一次 XPath。
    """
    if not has_links:
        return None, False, ""
    links = p._element.findall(f".//{_W_NS}hyperlink")
    if not links:
        return None, False, ""
    if rels is None:
        rels = d.part.rels
    for h in links:
        texts = "".join(t.text or "" for t in h.iter(f"{_W_NS}t")).strip()
        if not texts or texts != para_text:
            continue
        rid = h.get(f"{_R_NS}id")
        if rid:
            try:
                rel = rels[rid]
                if rel.is_external:
                    return rel.target_ref, False, ""
            except KeyError:
                pass
        anchor = h.get(f"{_W_NS}anchor")
        if anchor:
            return None, True, anchor
    return None, False, ""


_NOTE_TAGS = {"footnote": f"{_W_NS}footnote", "endnote": f"{_W_NS}endnote"}
# Word 在 footnotes.xml / endnotes.xml 里放的分隔符占位注（页面底部的横线本体），
# 带 w:type="separator" / "continuationSeparator"——不是注文，抽进来即制造噪声行
_NOTE_SEPARATORS = ("separator", "continuationSeparator")


def _docx_notes_lines(d, ctx: _DocxCtx) -> list[Line]:
    """CH-476（R7-GAP-2，决策 D7）：脚注 / 尾注正文补抽为文档末尾的段落行。

    原实现只遍历 document.xml 的 body，`word/footnotes.xml` / `word/endnotes.xml`
    完全不读 → 学术、法律文档的注文整块静默丢失，而非目标清单从未排除它们。
    口径：
    - 一条注 = 一行；注内多段落以单空格相连（与表格单元格段落口径一致，R7-GAP-3）；
    - 分隔符占位注跳过；
    - 不伪造 `[^1]` 行内脚注标记：GFM 无脚注语法，正文处没有可指的锚点，硬造即死链
      （与 FR-107 内部链接一律纯文本的口径同理）。注文来源由 `Line.note_kind`
      带到块级 `flags.note_kind`，供审阅区分「尾部段落是注不是正文」。
    - 无注的文档（现有全部语料）返回空列表 → 产物字节不变（G6）。
    `ctx`：与正文共享样式记忆化；`has_links` 按注 part 自己的子树单独预检。
    """
    import docx.text.paragraph as _dp
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import parse_xml

    lines: list[Line] = []
    for reltype, kind in ((RT.FOOTNOTES, "footnote"), (RT.ENDNOTES, "endnote")):
        try:
            part = d.part.part_related_by(reltype)
        except KeyError:
            continue
        root = parse_xml(part.blob)
        local = _docx_ctx(root, ctx.numbering)
        local.styles = ctx.styles
        for note in root.findall(_NOTE_TAGS[kind]):
            if note.get(f"{_W_NS}type") in _NOTE_SEPARATORS:
                continue
            got = [_docx_para_line(_dp.Paragraph(el, d), d, part.rels, local)
                   for el in note.findall(f"{_W_NS}p")]
            got = [ln for ln in got if ln is not None]
            if not got:
                continue
            head, *rest = got
            text = head.text if not rest else " ".join(
                [head.text] + [ln.text for ln in rest])
            lines.append(replace(head, text=text, note_kind=kind))
    return lines


def _docx_cell_text(tc) -> str:
    """单元格正文：段落边界以 `<br>` 相连（O-05 / C-06，GFM 单元格内合法换行）。

    CH-476（R7-GAP-3）原口径是单空格相连——比最初的 `"".join` 进，但段落
    边界仍不可辨（审阅面看成一句）。`<br>` 在 GFM 表格单元格内是合法换行，
    主流渲染器（GitHub / VS Code / Obsidian）均支持；单段落单元格（现有全部
    语料）无边界可连，与本函数改动前逐字节相同 → 产物不变（G6）。
    嵌套 `w:tbl` 的段落仍被拍平进本格：无版面模型，本轮不表达表格嵌套
    （拍平事实由 W1045 告警诚实暴露，见 `_docx_table_rows`）。
    """
    from docx.text.run import Run
    parts = []
    for p in tc.iter(f"{_W_NS}p"):
        if _inside_textbox(p):
            continue
        # CH-483（R8-PIPE-6）：`w:br`/`w:tab` 不是 `w:t`，旧实现只拼 `w:t`
        # 会把 "A\nB\tC" 粘成 "ABC"。Run.text 已按 python-docx 口径映射。
        txt = "".join(Run(r, p).text or "" for r in p.iter(f"{_W_NS}r")).strip()
        if txt:
            # D3（QA-R2）：单元格里的 `<`/`>` 是源文本字面量，不是允许直通的
            # HTML；`<br>` 只在下面作为**结构**分隔符由本函数自己拼入。
            # OPT4-16：实体折转收口到 `escapes.escape_entities`（B-37 单一来源）。
            parts.append(escape_entities(txt))
    return "<br>".join(parts)


def _docx_table_elems(parent, tag: str):
    """按序产出 `w:tr` / `w:tc`，展开 sdtContent / customXml 包裹层。

    OOXML 允许 `w:sdt` 包裹整行或单元格（重复节控件/内容控件）；只取直接子级
    会让这些行/格整块消失。递归只沿已知容器向下，不进入单元格正文。
    """
    for child in parent:
        if child.tag == tag:
            yield child
        elif child.tag in (f"{_W_NS}sdt", f"{_W_NS}customXml"):
            content = child.find(f"{_W_NS}sdtContent")
            yield from _docx_table_elems(content if content is not None else child, tag)


def _docx_table_rows(tbl) -> list[tuple[list[str], bool, int]]:
    """`w:tbl` → (单元格文本行, 该行是否含修订痕迹, 本行拍平空位数)。

    CH-476（R7-GAP-1）：单元格取「接受后」视图（`w:ins` 内的 `w:t` 覆盖、`w:delText`
    不含），表格路径天然是接受口径，只需把修订信号带出来。
    CH-476（R7-GAP-3）：单元格内段落边界以 `<br>` 相连，见 `_docx_cell_text`。
    CH-483（R8-PIPE-5）：`gridSpan` 展开为网格空位，`vMerge` 连续格置空，
    保证每行列数与表格网格一致（GFM 无合并语义，宁可用空位对齐，不左移列）。
    O-05（C-06 / F-11）：第三返回值统计本行的拍平空位数（gridSpan 展开 +
    vMerge 连续格）——空位本身是「合并信息被降维」的计数证据，供 S3 汇总、
    convert 留 W1045 告警。行末补齐网格宽度的空位**不计**：那是对齐而非
    合并语义丢失。
    """
    grid_el = tbl.find(f"{_W_NS}tblGrid")
    grid_n = len(grid_el.findall(f"{_W_NS}gridCol")) if grid_el is not None else 0
    rows: list[tuple[list[str], bool, int]] = []
    for tr in _docx_table_elems(tbl, f"{_W_NS}tr"):
        cells: list[str] = []
        flat = 0
        for tc in _docx_table_elems(tr, f"{_W_NS}tc"):
            tcPr = tc.find(f"{_W_NS}tcPr")
            span = 1
            cont = False
            if tcPr is not None:
                gs = tcPr.find(f"{_W_NS}gridSpan")
                if gs is not None:
                    try:
                        span = max(1, int(gs.get(f"{_W_NS}val", "1")))
                    except ValueError:
                        span = 1
                vm = tcPr.find(f"{_W_NS}vMerge")
                if vm is not None:
                    cont = vm.get(f"{_W_NS}val", "continue") != "restart"
            cells.append("" if cont else _docx_cell_text(tc))
            flat += (span - 1) + (1 if cont else 0)
            if not cont:
                # D5（QA-R2）：嵌套 `w:tbl` 被拍平成 `<br>` 文本，必须计入
                # 降维计数，否则 W1045 永远不触发（静默丢结构）。
                flat += sum(1 for t in tc.iter(f"{_W_NS}tbl")
                            if not _inside_textbox(t))
            cells.extend([""] * (span - 1))
        if cells:
            rows.append((cells, _docx_has_revision(tr), flat))
    width = max([grid_n] + [len(r[0]) for r in rows])
    if width and any(len(r[0]) < width for r in rows):
        rows = [(cells + [""] * (width - len(cells)), rev, flat)
                for cells, rev, flat in rows]
    return rows


def _docx_textbox_paragraphs(container, seen: set[int] | None = None):
    """按文档顺序产出文本框内的 w:p（AlternateContent 只取 Choice 一支）。

    Office 模板可能把文本框嵌套在另一个文本框/绘图中。CH-495（R14-PIPE-1）：
    原实现对选中的 txbxContent 扁平 `iter(w:p)`——嵌套 AlternateContent 的
    Choice 与 Fallback 两支 w:p 全部产出（文本翻倍），且宿主段落拼 run 时再
    收一遍（叠加至 ×4）。现改为层级递归：本层段落由 `_txbx_paragraphs` 产出
    后即向下钻进段内嵌套文本框，嵌套层段落由嵌套层自己产出一次；元素 id 去重
    兜底（防模板病态自引用）。
    """
    seen = seen if seen is not None else set()
    for child in container:
        if child.tag == f"{_MC_NS}AlternateContent":
            branch = child.find(f"{_MC_NS}Choice")
            if branch is None:
                branch = child.find(f"{_MC_NS}Fallback")
            if branch is not None:
                yield from _docx_textbox_paragraphs(branch, seen)
        elif child.tag == f"{_W_NS}txbxContent":
            yield from _txbx_paragraphs(child, seen)
        else:
            yield from _docx_textbox_paragraphs(child, seen)


def _txbx_paragraphs(txbx, seen: set[int]):
    """单个 txbxContent 层内的 w:p：产出本层段落，再递归段内嵌套文本框。

    sdt / customXml / 嵌套表格等包装层透明下钻（R8-PIPE-2 口径，本层被包起来
    的 w:p 不丢）；嵌套 AlternateContent 仍只取 Choice 一支；嵌套文本框经
    `_docx_textbox_paragraphs` 的 txbxContent 分支进入，每层段落恰好产出一次。
    """
    for child in txbx:
        if child.tag == f"{_W_NS}p":
            if id(child) not in seen:
                seen.add(id(child))
                yield child
            yield from _docx_textbox_paragraphs(child, seen)
        elif child.tag == f"{_MC_NS}AlternateContent":
            branch = child.find(f"{_MC_NS}Choice")
            if branch is None:
                branch = child.find(f"{_MC_NS}Fallback")
            if branch is not None:
                yield from _docx_textbox_paragraphs(branch, seen)
        else:
            yield from _txbx_paragraphs(child, seen)


def _iter_docx_blocks(container):
    """按文档顺序产出 ('p'|'tbl', element)，递归进入 sdtContent / customXml。

    CH-483（R8-PIPE-2）：块级内容控件与自定义 XML 不是 `w:body` 的直接 `w:p`/
    `w:tbl` 子级；旧实现只遍历直接子级，整块正文静默消失。
    """
    for child in container:
        tag = child.tag
        if tag == f"{_W_NS}p":
            yield "p", child
        elif tag == f"{_W_NS}tbl":
            yield "tbl", child
        elif tag == f"{_W_NS}sdt":
            content = child.find(f"{_W_NS}sdtContent")
            if content is not None:
                yield from _iter_docx_blocks(content)
        elif tag == f"{_W_NS}customXml":
            yield from _iter_docx_blocks(child)


def _docx_page_break_offsets(p, *, use_all: bool) -> list[int]:
    """段落内 `<w:br w:type="page"/>` 在 `Run.text` 串中的字符偏移。

    与 python-docx 的 `Run.text` 口径对齐：`w:t` 计文本长度，`w:tab` / 普通
    `w:br` / `w:cr` 各计 1 个字符；分页符本身贡献空串，只记录当前位置。
    """
    runs = _docx_all_runs(p) if use_all else p.runs
    offs: list[int] = []
    pos = 0
    for r in runs:
        for child in r._r.iterchildren():
            tag = child.tag
            if tag == f"{_W_NS}t":
                pos += len(child.text or "")
            elif tag == f"{_W_NS}tab":
                pos += 1
            elif tag == f"{_W_NS}br":
                if child.get(f"{_W_NS}type") == "page":
                    offs.append(pos)
                else:
                    pos += 1
            elif tag == f"{_W_NS}cr":
                pos += 1
    return offs


def _split_docx_page_break(ln: Line, offsets: list[int], raw: str) -> list[Line]:
    """D6（QA-R2）：按分页符**在段内的位置**切分，前后文各成一行。"""
    lead = 0 if ln.mono else len(raw) - len(raw.lstrip())
    text = ln.text or ""
    out: list[Line] = []
    prev = 0
    for cut in sorted({max(0, min(len(text), int(o) - lead)) for o in offsets}):
        seg = text[prev:cut]
        if seg.strip():
            out.append(replace(ln, text=seg if ln.mono else seg.strip(),
                               leading_space=False))
        out.append(Line(text="\x0c", size=0.0))
        prev = cut
    tail = text[prev:]
    if tail.strip():
        out.append(replace(ln, text=tail if ln.mono else tail.strip(),
                           leading_space=False))
    return out


def read_docx(path: str | Path, fmt: str | None = None) -> list[RawPage]:
    """DOCX → 页级行结构（DOCX 无物理页，按节合成单页序列，每 40 行一页）。

    CH-459：按 body 顺序遍历（段落与表格交错，原实现只读 `d.paragraphs`，
    表格内容整体丢失——FR-104 的 table 块对 DOCX 从未有输入）；
    段落内 `<w:br w:type="page"/>` 合成为 `\\x0c` 行（S3 的 page_break 信号）。
    CH-476（R7-GAP-1）：修订痕迹按「接受后」口径提取——`w:ins` 插入并入正文、
    `w:del` 删除不入正文，命中的行打 `Line.has_revision` 供 S3 块级留痕。
    CH-476（R7-GAP-2，决策 D7）：正文行之后追加脚注 / 尾注行（`_docx_notes_lines`），
    注文不再整块丢失；无注的文档该行序为空，产物字节不变。
    fmt：调用方已嗅探的格式（形参保留与 read_pdf 对齐；DOCX 分支不使用）。
    """
    import docx
    import docx.text.paragraph as _dp

    d = _open_source(lambda: docx.Document(str(path)), path, "docx")
    numbering = _DocxNumbering(d)
    ctx = _docx_ctx(d.element.body, numbering)
    lines: list[Line] = []
    for kind, child in _iter_docx_blocks(d.element.body):
        if kind == "p":
            para = _dp.Paragraph(child, d)
            ln = _docx_para_line(para, d, ctx=ctx)
            if child.findall(f".//{_W_NS}br[@{_W_NS}type='page']"):
                # D6（QA-R2）：分页符属于段落内部——按它的字符位置切分，
                # 原实现把整段写完再补 `\x0c`，分页后的文字被错误地留在前一页。
                use_all = (_docx_has_revision(child) or ctx.has_sdt
                           or ctx.has_custom_xml or ctx.has_links
                           or ctx.has_fields or ctx.has_smart_tag)
                offs = _docx_page_break_offsets(para, use_all=use_all)
                if ln is not None:
                    runs = (_docx_all_runs(para) if use_all else para.runs)
                    raw = "".join(r.text or "" for r in runs)
                    if not offs:
                        # `<w:br>` 直接挂在 w:p 下（不在任何 run 内）时取不到字符
                        # 偏移；按段末处理，至少保住页分隔信号本身。
                        offs = [len(raw)]
                    lines.extend(_split_docx_page_break(ln, offs, raw))
                elif offs:
                    lines.extend(Line(text="\x0c", size=0.0) for _ in offs)
                else:
                    lines.append(Line(text="\x0c", size=0.0))
            elif ln is not None:
                lines.append(ln)
        else:
            rows = _docx_table_rows(child)
            for i, (cells, rev, flat) in enumerate(rows):
                lines.append(Line(text=" | ".join(cells), row=cells,
                                  row_explicit=True, has_revision=rev,
                                  row_flat=flat, table_start=(i == 0)))
    # CH-485（R9-PIPE-1）：文本框正文不是 body 的直接 w:p；单独产出为段落，
    # 避免宿主段落只得到“前后”或 AlternateContent 双分支重复。
    # 无文本框的文档不做全树遍历（常见路径省一次 O(全部 XML 元素) 扫描）。
    if ctx.has_textbox:
        for p_el in _docx_textbox_paragraphs(d.element.body):
            ln = _docx_para_line(_dp.Paragraph(p_el, d), d, ctx=ctx)
            if ln is not None:
                lines.append(ln)
    lines += _docx_notes_lines(d, ctx)
    pages: list[RawPage] = []
    chunk = 40
    for i in range(0, max(1, (len(lines) + chunk - 1) // chunk)):
        seg = lines[i * chunk:(i + 1) * chunk]
        rp = RawPage(index=i, lines=seg)
        rp.size_hist = _size_hist(seg)    # CH-492（P-1）：单趟直方图
        pages.append(rp)
    return pages


def _style_size(style_name: str) -> float:
    s = style_name.lower()
    if s.startswith("title"):
        return 20.0
    lvl = _heading_level_from_style(style_name)
    if lvl is not None:
        return max(11.0, 18.0 - lvl * 1.5)
    return 10.5


def read_any(path: str | Path, page_span: tuple[int, int] | None = None,
             chunk_pages: int | None = None, fmt: str | None = None,
             *, legacy_doc_backend: str = "auto",
             legacy_doc_timeout_s: int = 120,
             tmp_dir: str | Path | None = None) -> list[RawPage]:
    fmt = fmt or sniff(path)
    if fmt == "pdf":
        return read_pdf(path, page_span, chunk_pages=chunk_pages, fmt=fmt)
    if fmt == "doc":
        from ..errors import E4151_UnsupportedType
        from .legacy_doc import LegacyDocError, read_legacy_doc
        try:
            return read_legacy_doc(
                path, page_span=page_span, backend=legacy_doc_backend,
                timeout_s=legacy_doc_timeout_s, tmp_dir=tmp_dir)
        except LegacyDocError as exc:
            raise E4151_UnsupportedType(
                "cannot convert legacy .doc",
                detail={"file": Path(path).name, "stage": exc.stage,
                        "hint": "install LibreOffice or Microsoft Word, or set "
                                "s1.legacy_doc_backend"}) from exc
    if page_span:
        return read_docx(path)[page_span[0]:page_span[1]]
    return read_docx(path)
