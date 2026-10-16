"""S1 解析层 —— 文本型 PDF / DOCX 抽取为页级行结构。

范围纪律（上游 v2.4 Delta §10-1，方案 1.2 非目标）：不做图片 / OCR / 扫描件 / 公式；本层只处理可抽取文本层。

CH-459（FR-104 / FR-107 收口）：本层补三组结构化信号，供 S3 产出完整七类块——
- `Line.mono`：等宽字体行（代码块信号；取自 fontname，阈值见 `_is_mono`）；
- `Line.row`：表格行的单元格文本（PDF 按行内字符横向间隙聚类，DOCX 取 `w:tbl` 真实单元格）；
- `Line.link` / `link_internal` / `link_dest`：链接（PDF 取 Link 标注的 URI / GoTo，
  DOCX 取 `w:hyperlink` 的关系目标或书签锚点）。
另补 `Line.level_hint`：DOCX 段落样式名显式声明的标题层级（"Heading N"），
S3 据此判定标题——原实现只看字号/粗体/编号，DOCX 的 Heading 样式全部落成段落。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAGIC_PDF = b"%PDF-"
MAGIC_ZIP = b"PK\x03\x04"
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
    row: list[str] | None = None            # CH-459：表格行单元格（非 None 即行候选）
    row_explicit: bool = False              # CH-459：行来自真实表格对象（DOCX w:tbl），
                                            # 而非 PDF 几何推断——S3 的防误判守卫只对后者生效
    level_hint: int | None = None           # CH-459：DOCX Heading 样式显式层级
    x0: float = 0.0


@dataclass
class RawPage:
    index: int                      # 0-based
    lines: list[Line] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(ln.text.strip()) for ln in self.lines)


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
        try:
            import zipfile
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
            if DOCX_REQUIRED_PART.decode() in names:
                return "docx"
        except Exception:
            pass
    raise E4151_UnsupportedType(f"unsupported magic for {p.name}",
                                detail={"file": p.name, "magic": head.hex()})


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
        raise E4151_UnsupportedType(
            f"unreadable {kind}: {Path(path).name}",
            detail={"file": Path(path).name, "exc": type(exc).__name__},
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
    # 分块模式仍需预先知道页数来算片边界，保持原口径。
    lazy_total = page_span is None and not (chunk_pages and chunk_pages > 0)
    total = 0 if lazy_total else _pdf_page_count(path)
    if page_span:
        start, end = max(0, page_span[0]), min(total, page_span[1])
    else:
        start, end = 0, total
    if not lazy_total and end <= start:
        return out
    step = chunk_pages if chunk_pages and chunk_pages > 0 else (end - start)
    lo_range = range(start, end, step) if not lazy_total else range(0, 1)

    for lo in lo_range:
        hi = min(lo + step, end)
        # 每个分块独立 open/close：pdfminer 的 LRUCachingObjectCache / 页对象
        # 随 PDF 实例一起释放，驻留峰值从「整本文档」降为「一个分块」。
        with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
            if lazy_total:              # 单片路径：页数在同一个 handle 上读出
                total = len(pdf.pages)
                lo, hi = 0, total
            for i in range(lo, hi):
                page = pdf.pages[i]
                chars = page.chars or []
                cs_index = _char_index(chars)
                lines: list[Line] = []
                geoms: list[tuple[float, float]] = []
                for ln in (page.extract_text_lines() or []):
                    top = float(ln.get("top", 0.0) or 0.0)
                    bottom = float(ln.get("bottom", 0.0) or 0.0)
                    lchars = _slice_chars(chars, cs_index, top, bottom)
                    st = _line_style(lchars)
                    lines.append(Line(text=ln.get("text", ""), size=st[0],
                                      bold=st[1], italic=st[2], strike=st[3],
                                      mono=st[4],
                                      row=_row_cells(lchars),
                                      x0=float(ln.get("x0", 0.0) or 0.0)))
                    geoms.append((top, bottom))
                _attach_links(lines, geoms, page)
                out.append(RawPage(index=i, lines=lines))
                page.flush_cache()
    return out


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


def _pdf_page_count(path: str | Path) -> int:
    import pdfplumber
    with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
        return len(pdf.pages)


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


def _line_style(chars: list[dict]) -> tuple[float, bool, bool, bool, bool]:
    """取**已按几何区间切好**的行内 char 的众数字号与样式多数派。

    CH-459：第五返回值 `mono` = 行内多数字体为等宽家族（代码块信号）。
    CH-471（PIPE-2）：入参 `chars` 已由 `_slice_chars` 用 `[top-1.0, max(bottom,
    top+1.0))` 同一边界切好，原此处再过滤一遍**恒等于原列表**（逐行全字符比对，
    与 `_char_index` 的 bisect 优化自相矛盾），删除之——G6 逐字节等价。
    """
    from collections import Counter
    cs = chars
    if not cs:
        return (0.0, False, False, False, False)
    size = Counter(round(float(c.get("size", 0.0)), 1) for c in cs).most_common(1)[0][0]
    n = len(cs)
    bold = sum(1 for c in cs if _is_bold(c)) / n >= 0.6
    italic = sum(1 for c in cs if _is_italic(c)) / n >= 0.6
    strike = sum(1 for c in cs if c.get("strike")) / n >= 0.6
    mono = sum(1 for c in cs if _is_mono(c)) / n >= 0.6
    return (size, bold, italic, bool(strike), mono)


def _is_mono(c: dict) -> bool:
    return any(h in str(c.get("fontname", "")).lower() for h in _MONO_HINTS)


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


def _is_bold(c: dict) -> bool:
    if c.get("bold"):
        return True
    fontname = str(c.get("fontname", "")).lower()
    return "bold" in fontname or "black" in fontname or c.get("weight", 0) >= 600


def _is_italic(c: dict) -> bool:
    if c.get("italic"):
        return True
    return "italic" in str(c.get("fontname", "")).lower() or "oblique" in str(
        c.get("fontname", "")).lower()


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _docx_para_line(p, d) -> Line | None:
    """段落 → Line（含 CH-459 的等宽 / 超链接 / 标题层级提示 / 分页符信号）。"""
    text = (p.text or "").strip()
    if not text:
        return None
    size = 0.0
    bold = italic = strike = mono = False
    for r in p.runs:
        if r.font.size is not None:
            size = float(r.font.size.pt)
        bold = bold or bool(r.bold)
        italic = italic or bool(r.italic)
        strike = strike or bool(r.font.strike)
        mono = mono or _name_is_mono(r.font.name)
    style = (p.style.name or "") if p.style is not None else ""
    level = None
    if style.lower().startswith("heading"):
        try:
            level = max(1, min(6, int(style.split()[-1])))
        except Exception:
            level = 1
    link, link_internal, link_dest = _docx_para_link(p, d, text)
    return Line(text=text, size=size or _style_size(style),
                bold=bold or style.lower().startswith("heading"),
                italic=italic, strike=strike, mono=mono,
                link=link, link_internal=link_internal, link_dest=link_dest,
                level_hint=level)


def _name_is_mono(name: str | None) -> bool:
    return bool(name) and any(h in name.lower() for h in _MONO_HINTS)


def _docx_para_link(p, d, para_text: str) -> tuple[str | None, bool, str]:
    """CH-459（FR-107/108）：段落内 `w:hyperlink` → (外部 URL, 是否内部, 锚点)。

    保守口径：只有超链接文字**完整覆盖**段落文本时才附着——行内局部链接
    （链接文字只是段落的一部分）本轮不抽：块级 links 模型无法表达「哪一段文字」
    是链接，硬附会把整块文本包成 `[整块](url)`，属于制造错误数据。
    """
    links = p._element.findall(f".//{_W_NS}hyperlink")
    if not links:
        return None, False, ""
    for h in links:
        texts = "".join(t.text or "" for t in h.iter(f"{_W_NS}t")).strip()
        if not texts or texts != para_text:
            continue
        rid = h.get(f"{_R_NS}id")
        if rid:
            try:
                rel = d.part.rels[rid]
                if rel.is_external:
                    return rel.target_ref, False, ""
            except KeyError:
                pass
        anchor = h.get(f"{_W_NS}anchor")
        if anchor:
            return None, True, anchor
    return None, False, ""


def _docx_table_rows(tbl) -> list[list[str]]:
    """`w:tbl` → 行列表（单元格文本已 strip）；空单元格保留空串以对齐列数。"""
    rows: list[list[str]] = []
    for tr in tbl.findall(f"{_W_NS}tr"):
        cells = []
        for tc in tr.findall(f"{_W_NS}tc"):
            cells.append("".join(t.text or "" for t in tc.iter(f"{_W_NS}t")).strip())
        if cells:
            rows.append(cells)
    return rows


def read_docx(path: str | Path, fmt: str | None = None) -> list[RawPage]:
    """DOCX → 页级行结构（DOCX 无物理页，按节合成单页序列，每 40 行一页）。

    CH-459：按 body 顺序遍历（段落与表格交错，原实现只读 `d.paragraphs`，
    表格内容整体丢失——FR-104 的 table 块对 DOCX 从未有输入）；
    段落内 `<w:br w:type="page"/>` 合成为 `\\x0c` 行（S3 的 page_break 信号）。
    fmt：调用方已嗅探的格式（形参保留与 read_pdf 对齐；DOCX 分支不使用）。
    """
    import docx
    import docx.text.paragraph as _dp

    d = _open_source(lambda: docx.Document(str(path)), path, "docx")
    lines: list[Line] = []
    for child in d.element.body:
        tag = child.tag
        if tag == f"{_W_NS}p":
            ln = _docx_para_line(_dp.Paragraph(child, d), d)
            if ln is not None:
                lines.append(ln)
            if child.findall(f".//{_W_NS}br[@{_W_NS}type='page']"):
                lines.append(Line(text="\x0c", size=0.0))
        elif tag == f"{_W_NS}tbl":
            for cells in _docx_table_rows(child):
                lines.append(Line(text=" | ".join(cells), row=cells,
                                  row_explicit=True))
    pages: list[RawPage] = []
    chunk = 40
    for i in range(0, max(1, (len(lines) + chunk - 1) // chunk)):
        pages.append(RawPage(index=i, lines=lines[i * chunk:(i + 1) * chunk]))
    return pages


def _style_size(style_name: str) -> float:
    s = style_name.lower()
    if s.startswith("title"):
        return 20.0
    if s.startswith("heading"):
        try:
            lvl = int(s.split()[-1])
        except Exception:
            lvl = 1
        return max(11.0, 18.0 - lvl * 1.5)
    return 10.5


def read_any(path: str | Path, page_span: tuple[int, int] | None = None,
             chunk_pages: int | None = None, fmt: str | None = None) -> list[RawPage]:
    fmt = fmt or sniff(path)
    if fmt == "pdf":
        return read_pdf(path, page_span, chunk_pages=chunk_pages, fmt=fmt)
    if page_span:
        return read_docx(path)[page_span[0]:page_span[1]]
    return read_docx(path)
