"""S1 解析层 —— 文本型 PDF / DOCX 抽取为页级行结构。

范围纪律（Delta §10-1）：不做图片 / OCR / 扫描件 / 公式；本层只处理可抽取文本层。
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
        return sum(len(l.text.strip()) for l in self.lines)


def sniff(path: str | Path) -> str:
    p = Path(path)
    head = p.open("rb").read(8)
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
    raise ValueError(f"unsupported magic for {p.name}")


def read_pdf(path: str | Path, page_span: tuple[int, int] | None = None) -> list[RawPage]:
    """页级行结构 + 字号 / 粗斜体（pdfplumber chars）。

    page_span=(start, end) 半开区间，供 S2 分片按页范围粗切使用。
    """
    import pdfplumber
    out: list[RawPage] = []
    with pdfplumber.open(str(path)) as pdf:
        total = len(pdf.pages)
        start, end = page_span if page_span else (0, total)
        start, end = max(0, start), min(total, end)
        for i in range(start, end):
            page = pdf.pages[i]
            lines: list[Line] = []
            chars = page.chars or []
            raw_lines = page.extract_text_lines() or []
            for ln in raw_lines:
                st = _line_style(chars, float(ln.get("top", 0.0) or 0.0),
                                 float(ln.get("bottom", 0.0) or 0.0))
                lines.append(Line(text=ln.get("text", ""), size=st[0],
                                  bold=st[1], italic=st[2], strike=st[3],
                                  x0=float(ln.get("x0", 0.0) or 0.0)))
            out.append(RawPage(index=i, lines=lines))
    return out


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

    d = docx.Document(str(path))
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


def read_any(path: str | Path, page_span: tuple[int, int] | None = None) -> list[RawPage]:
    fmt = sniff(path)
    if fmt == "pdf":
        return read_pdf(path, page_span)
    if page_span:
        return read_docx(path)[page_span[0]:page_span[1]]
    return read_docx(path)
