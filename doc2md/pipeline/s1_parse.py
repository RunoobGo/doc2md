"""S1 解析层 —— 文本型 PDF / DOCX 抽取为页级行结构。

范围纪律（上游 v2.4 Delta §10-1，方案 1.2 非目标）：不做图片 / OCR / 扫描件 / 公式；本层只处理可抽取文本层。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAGIC_PDF = b"%PDF-"
MAGIC_ZIP = b"PK\x03\x04"
DOCX_REQUIRED_PART = b"word/document.xml"


@dataclass
class Line:
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    strike: bool = False
    link: str | None = None
    link_internal: bool = False
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
        head = p.open("rb").read(8)
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
             chunk_pages: int | None = None) -> list[RawPage]:
    """页级行结构 + 字号 / 粗斜体（pdfplumber chars）。

    page_span=(start, end) 半开区间，供 S2 分片按页范围粗切使用。
    chunk_pages：每重新打开一次文档最多解析多少页（CH-447，低配机内存纪律）。
    """
    import pdfplumber
    out: list[RawPage] = []
    total = _pdf_page_count(path)
    start, end = page_span if page_span else (0, total)
    start, end = max(0, start), min(total, end)
    if end <= start:
        return out

    step = chunk_pages if chunk_pages and chunk_pages > 0 else (end - start)
    for lo in range(start, end, step):
        hi = min(lo + step, end)
        # 每个分块独立 open/close：pdfminer 的 LRUCachingObjectCache / 页对象
        # 随 PDF 实例一起释放，驻留峰值从「整本文档」降为「一个分块」。
        with _open_source(lambda: pdfplumber.open(str(path)), path, "pdf") as pdf:
            for i in range(lo, hi):
                page = pdf.pages[i]
                chars = page.chars or []
                cs_index = _char_index(chars)
                lines: list[Line] = []
                for ln in (page.extract_text_lines() or []):
                    top = float(ln.get("top", 0.0) or 0.0)
                    bottom = float(ln.get("bottom", 0.0) or 0.0)
                    st = _line_style(_slice_chars(chars, cs_index, top, bottom),
                                     top, bottom)
                    lines.append(Line(text=ln.get("text", ""), size=st[0],
                                      bold=st[1], italic=st[2], strike=st[3],
                                      x0=float(ln.get("x0", 0.0) or 0.0)))
                out.append(RawPage(index=i, lines=lines))
                page.flush_cache()
    return out


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


def _line_style(chars: list[dict], top: float, bottom: float) -> tuple[float, bool, bool, bool]:
    """按几何区间 [top, bottom) 取行内 char 的众数字号与样式多数派。"""
    from collections import Counter
    cs = [c for c in chars if float(c.get("top", 0.0)) >= top - 1.0
          and float(c.get("top", 0.0)) < max(bottom, top + 1.0)]
    if not cs:
        return (0.0, False, False, False)
    size = Counter(round(float(c.get("size", 0.0)), 1) for c in cs).most_common(1)[0][0]
    n = len(cs)
    bold = sum(1 for c in cs if _is_bold(c)) / n >= 0.6
    italic = sum(1 for c in cs if _is_italic(c)) / n >= 0.6
    strike = sum(1 for c in cs if c.get("strike")) / n >= 0.6
    return (size, bold, italic, bool(strike))


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


def read_docx(path: str | Path) -> list[RawPage]:
    """DOCX → 页级行结构（DOCX 无物理页，按节合成单页序列，每 40 行一页）。"""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: F401  (保留供后续扩展)

    d = _open_source(lambda: docx.Document(str(path)), path, "docx")
    lines: list[Line] = []
    for p in d.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        size = 0.0
        bold = italic = strike = False
        for r in p.runs:
            if r.font.size is not None:
                size = float(r.font.size.pt)
            bold = bold or bool(r.bold)
            italic = italic or bool(r.italic)
            strike = strike or bool(r.font.strike)
        style = (p.style.name or "") if p.style is not None else ""
        lines.append(Line(text=text, size=size or _style_size(style),
                          bold=bold or style.lower().startswith("heading"),
                          italic=italic, strike=strike))
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
             chunk_pages: int | None = None) -> list[RawPage]:
    fmt = sniff(path)
    if fmt == "pdf":
        return read_pdf(path, page_span, chunk_pages=chunk_pages)
    if page_span:
        return read_docx(path)[page_span[0]:page_span[1]]
    return read_docx(path)
