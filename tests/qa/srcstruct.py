#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""srcstruct.py —— 源端结构清单提取器（目标侧的「真值」，独立于管线）。

为什么需要它
------------
`tests/corpus/expected/` 是**管线产物基线**，对它比对只能证明「稳定」，
不能证明「对」（C-31 的自啮合问题）。`tests/corpus/ideal/` 只有标题标注，
覆盖不了表格 / 列表 / 代码 / 链接。

本模块走第三条路：**直接从源文件抽取结构清单**，不经管线任何环节，
因此与产物彻底独立。它回答的是参考资料里「没有标注数据怎么办」的第一条
——**结构清单比对**：不比文字，比骨架（标题序列 / 表格形状 / 列表项 / 代码块 / 链接）。

  ┌─────────────┐   源端抽取（本模块）   ┌──────────────┐
  │ 源 PDF/DOCX │ ─────────────────────▶ │ 结构清单 GT  │
  └─────────────┘                        └──────┬───────┘
                                                │ 序列对齐 / TEDS
  ┌─────────────┐   管线转换             ┌──────▼───────┐
  │ 源 PDF/DOCX │ ─────────────────────▶ │ 产物 MD 清单 │
  └─────────────┘                        └─────────────┘

抽取口径（诚实标注能力边界）
---------------------------
- **DOCX**：真值级。python-docx 直读 OOXML——`w:pStyle` 给标题层级（不是按字号猜，
  正是参考资料强调的「尊重文档声明的结构」）、`w:tbl` 给表格、`w:numPr` 给列表、
  等宽字体给代码块、relationship 给超链接。
- **PDF**：启发级，弱于 DOCX。PDF 没有结构层，只能用**生成器的字号约定**
  （与 `make_ideal_pdf.py` 同口径：18/20/16=章级、14=节级）与 pdfplumber 的
  表格线检测。**PDF 侧的表格清单只在有线框时可信**，无线框宽列距表不可判定
  ——这是项目 §1.2 非目标 6 的同一边界，不得越界声称。

用法：
    from tests.qa.srcstruct import extract
    gt = extract(Path("sources/pdf_03_table_1p.pdf"))
    gt["headings"]   # [{"text","level","seq"}]
    gt["tables"]     # [{"header","body","ncol","nrow"}]
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["extract", "extract_docx", "extract_pdf", "ordered_skeleton", "StructGT"]

# PDF 标题字号约定（与 tests/corpus/make_corpus.py 生成器一致，
# 与 tests/corpus/make_ideal_pdf.py 同口径）
PDF_HEAD_SIZES = {18: 1, 20: 1, 16: 1, 14: 2}

# DOCX 样式名 → 层级
DOCX_STYLE_LEVELS = {"title": 1, "subtitle": 2}

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _W(tag: str) -> str:
    """OOXML 主命名空间限定名。"""
    return f"{{{_W_NS}}}{tag}"


def _norm(s: str) -> str:
    """归一化：去全部空白。全半角不折算（与 mdscore/qa_eval 同口径）。"""
    return re.sub(r"\s+", "", s or "")


def _style_level(style_name: str) -> int | None:
    """python-docx 样式名 → 标题层级；非标题返回 None。"""
    s = (style_name or "").strip()
    low = s.lower()
    if low in DOCX_STYLE_LEVELS:
        return DOCX_STYLE_LEVELS[low]
    if low.startswith("heading ") or low.startswith("标题 "):
        try:
            return int(s.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            return None
    return None


# ============================================================================
# DOCX：真值级抽取
# ============================================================================

def _docx_iter_block_items(doc):
    """按文档顺序遍历 body 的段落与表格（python-docx 无内置，需自实现）。

    `Document.paragraphs` 只给 body 顶层段落、跳过表格内段落，且丢失与表格的
    相对顺序——结构清单一比对顺序，就必须自己走 body 的子元素序列。
    """
    from docx.oxml.ns import qn
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            from docx.text.paragraph import Paragraph
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            from docx.table import Table
            yield Table(child, doc)


def _docx_headings(doc) -> list[dict]:
    """标题：只认显式样式（w:pStyle），不按字号猜。

    参考资料的明确结论：「一段话长得像标题，不等于它是标题」，
    转换器要么尊重文档声明的结构，要么猜测作者意图——这里取前者。
    """
    from docx.text.paragraph import Paragraph
    out, seq = [], 0
    for item in _docx_iter_block_items(doc):
        if not isinstance(item, Paragraph):
            continue
        lvl = _style_level(getattr(item.style, "name", "") or "")
        if lvl is None:
            continue
        text = item.text.strip()
        if not text:
            continue
        seq += 1
        out.append({"seq": seq, "text": text, "level": lvl})
    return out


def _docx_tables(doc) -> list[dict]:
    """表格：含 gridSpan / vMerge 证据（合并单元格是真值的一部分）。"""
    from docx.table import Table
    out = []
    for item in _docx_iter_block_items(doc):
        if not isinstance(item, Table):
            continue
        rows = []
        spans = 0
        for row in item.rows:
            cells = []
            for tc in row.cells:
                tcPr = tc._tc.tcPr
                if tcPr is not None:
                    gs = tcPr.find(_W("gridSpan"))
                    vm = tcPr.find(_W("vMerge"))
                    if gs is not None or vm is not None:
                        spans += 1
                cells.append(re.sub(r"\s+", " ", tc.text).strip())
            rows.append(cells)
        if not rows:
            continue
        ncol = max(len(r) for r in rows)
        out.append({
            "header": rows[0] if rows else [],
            "body": rows[1:],
            "ncol": ncol,
            "nrow": len(rows),
            "merged_cells": spans,
        })
    return out


#: 字面项目符号形态：语料生成器与大量真实 Word 文档用「手打符号」而非
#: `w:numPr` 造列表（参考资料：真实世界的 Word 是人类用字体按钮堆出来的）。
#: 只看 numPr 会把这类列表全部漏掉，必须两条路都认。
_LITERAL_BULLET_RE = re.compile(
    r"^\s*(?:[-•·◦▪‧※]|\d{1,3}[、.．)）、]|[a-zA-Z][.)]|[(（]\d{1,3}[)）])\s+\S")


def _docx_lists(doc) -> int:
    """列表项计数：`w:numPr` 声明式 + 字面符号启发式，两者取并集去重。

    参考资料：编号在 numbering.xml，正文里只有 numId/ilvl；而更多文档干脆
    不用编号、直接手打「- 」或「1. 」。只认前者会系统性漏计。
    """
    from docx.text.paragraph import Paragraph
    n = 0
    for item in _docx_iter_block_items(doc):
        if not isinstance(item, Paragraph):
            continue
        text = item.text.strip()
        if not text:
            continue
        pPr = item._p.pPr
        has_numpr = (pPr is not None and pPr.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr") is not None)
        if has_numpr or _LITERAL_BULLET_RE.match(text):
            n += 1
    return n


def _docx_code_blocks(doc) -> int:
    """代码块：等宽字体段落的连续段计为一块（粗略但够结构清单比对）。"""
    from docx.text.paragraph import Paragraph
    n, in_code = 0, False
    for item in _docx_iter_block_items(doc):
        if not isinstance(item, Paragraph):
            in_code = False
            continue
        mono = any((r.font.name or "").lower() in ("courier", "consolas", "monaco", "monospace")
                   for r in item.runs if r.font is not None)
        if mono and item.text.strip():
            if not in_code:
                n += 1
            in_code = True
        else:
            in_code = False
    return n


def _docx_links(doc) -> list[str]:
    """外部链接 URL（relationship 口径）。"""
    urls = []
    try:
        for rel in doc.part.rels.values():
            if "hyperlink" in rel.reltype:
                urls.append(rel.target_ref)
    except Exception:  # noqa: BLE001 — 关系项缺失不应让抽取整体失败
        pass
    return urls


def extract_docx(path: Path) -> dict:
    import docx
    d = docx.Document(str(path))
    return {
        "kind": "docx",
        "source": path.name,
        "confidence": "ground_truth",     # 声明式结构，非启发式
        "headings": _docx_headings(d),
        "tables": _docx_tables(d),
        "list_items": _docx_lists(d),
        "code_blocks": _docx_code_blocks(d),
        "links": _docx_links(d),
    }


# ============================================================================
# PDF：启发级抽取（能力边界如实标注）
# ============================================================================

def _pdf_lines(path: Path) -> list[dict]:
    import pdfplumber
    out = []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for ch in (page.chars or []):
                out.append({
                    "page": pno, "text": ch.get("text", ""),
                    "size": round(float(ch.get("size", 0) or 0), 1),
                    "x0": float(ch.get("x0", 0) or 0),
                    "top": round(float(ch.get("top", 0) or 0), 1),
                    "fontname": ch.get("fontname", ""),
                })
    return out


def _pdf_group_lines(chars: list[dict]) -> list[dict]:
    """按 top 聚成行，行内按 x0 排序后拼接。"""
    buckets: dict[tuple[int, float], list[dict]] = {}
    for c in chars:
        buckets.setdefault((c["page"], c["top"]), []).append(c)
    lines = []
    for (page, top), cs in sorted(buckets.items()):
        cs.sort(key=lambda x: x["x0"])
        text = "".join(c["text"] for c in cs).strip()
        if not text:
            continue
        sizes = [c["size"] for c in cs if c["size"]]
        size = max(sizes, key=sizes.count) if sizes else 0.0
        lines.append({"page": page, "top": top, "text": text, "size": size})
    return lines


def _pdf_headings(lines: list[dict]) -> list[dict]:
    """标题：字号约定 + 行长约束。与 make_ideal_pdf.py 同口径，启发级。"""
    out, seq = [], 0
    for ln in lines:
        lvl = PDF_HEAD_SIZES.get(int(round(ln["size"])))
        if lvl is None:
            continue
        text = ln["text"].strip()
        if not text or len(text) > 60:
            continue
        seq += 1
        out.append({"seq": seq, "text": text, "level": lvl})
    return out


def _pdf_tables(path: Path) -> list[dict]:
    """表格：pdfplumber 的线框检测。**只在有线框时可信**——无线框表不可判定，
    宁可返回空集也不猜（与 S3「宁漏勿误」同纪律）。"""
    import pdfplumber
    out = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    rows = [[re.sub(r"\s+", " ", (c or "")).strip() for c in row]
                            for row in tbl]
                    rows = [r for r in rows if any(c for c in r)]
                    if len(rows) < 2:
                        continue
                    ncol = max(len(r) for r in rows)
                    out.append({"header": rows[0], "body": rows[1:],
                                "ncol": ncol, "nrow": len(rows),
                                "merged_cells": 0})
    except Exception:  # noqa: BLE001
        return []
    return out


def extract_pdf(path: Path) -> dict:
    lines = _pdf_group_lines(_pdf_lines(path))
    return {
        "kind": "pdf",
        "source": path.name,
        "confidence": "heuristic",        # 启发级，弱于 DOCX，结论须打折
        "headings": _pdf_headings(lines),
        "tables": _pdf_tables(path),
        "list_items": None,               # PDF 无列表结构层，不出数
        "code_blocks": None,
        "links": None,
    }


# ============================================================================
# 有序骨架：NID / BSF 必须比「顺序」，而顺序只能来自源端
# ============================================================================

def _docx_ordered_skeleton(doc) -> list[dict]:
    """DOCX 按 body 元素真实顺序产出骨架条目（标题 / 表格交错）。

    为什么必须有序：先出全部标题再出全部表格的「分类骨架」会让阅读顺序
    指标（NID）把「参照系构造错误」误报成「管线顺序错乱」——
    docx_11 的 5 张表夹在章节之间时，分类骨架的 NID 只有 0.27，
    而真实顺序是逐节交错的。
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    out = []
    for item in _docx_iter_block_items(doc):
        if isinstance(item, Paragraph):
            lvl = _style_level(getattr(item.style, "name", "") or "")
            if lvl is None:
                continue
            text = item.text.strip()
            if text:
                out.append({"type": "heading", "level": lvl, "text": text})
        elif isinstance(item, Table):
            rows = []
            spans = 0
            for row in item.rows:
                cells = []
                for tc in row.cells:
                    tcPr = tc._tc.tcPr
                    if tcPr is not None and (tcPr.find(_W("gridSpan")) is not None
                                             or tcPr.find(_W("vMerge")) is not None):
                        spans += 1
                    cells.append(re.sub(r"\s+", " ", tc.text).strip())
                rows.append(cells)
            if rows:
                out.append({"type": "table", "header": rows[0], "body": rows[1:],
                            "ncol": max(len(r) for r in rows), "nrow": len(rows),
                            "merged_cells": spans})
    return out


def _pdf_ordered_skeleton(path: Path) -> list[dict]:
    """PDF 按 (page, top) 几何顺序产出骨架条目。

    能力边界：pdfplumber 的 `find_tables` 只在**有线框**时给得出表格 bbox，
    无线框表拿不到位置——此时表格按页尾追加，顺序可信度降级，
    结果里如实标注 `order_confidence`。
    """
    import pdfplumber
    lines = _pdf_group_lines(_pdf_lines(path))
    out: list[dict] = []
    for ln in lines:
        lvl = PDF_HEAD_SIZES.get(int(round(ln["size"])))
        if lvl is None:
            continue
        text = ln["text"].strip()
        if not text or len(text) > 60:
            continue
        out.append({"type": "heading", "level": lvl, "text": text,
                    "page": ln["page"], "top": ln["top"]})

    confident = True
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            try:
                tables = page.find_tables() or []
            except Exception:  # noqa: BLE001
                tables = []
                confident = False
            for tbl in tables:
                bbox = getattr(tbl, "bbox", None)
                top = float(bbox[1]) if bbox else 1e6 - pno
                rows = [[re.sub(r"\s+", " ", (c or "")).strip() for c in r]
                        for r in (tbl.extract() or [])]
                rows = [r for r in rows if any(c for c in r)]
                if len(rows) < 2:
                    continue
                out.append({"type": "table", "header": rows[0], "body": rows[1:],
                            "ncol": max(len(r) for r in rows), "nrow": len(rows),
                            "merged_cells": 0, "page": pno, "top": top})
    out.sort(key=lambda e: (e.get("page", 0), e.get("top", 0.0)))
    return {"items": out, "order_confidence": "geometry" if confident else "degraded"}


def ordered_skeleton(path: Path) -> dict:
    """产出**文档真实顺序**的骨架条目，供 NID / BSF 比对。

    返回 {"items": [...], "order_confidence": "declaration"|"geometry"|"degraded"|"unavailable"}
    - declaration：DOCX，顺序来自 OOXML body 元素序，真值级；
    - geometry：PDF 按 (page, top) 排序，启发级；
    - degraded：PDF 有线框表拿不到 bbox，顺序可信度降级；
    - unavailable：旧版 .doc 无内置解析器。
    """
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".docx":
        import docx
        d = docx.Document(str(path))
        return {"items": _docx_ordered_skeleton(d), "order_confidence": "declaration"}
    if suf == ".pdf":
        return _pdf_ordered_skeleton(path)
    return {"items": [], "order_confidence": "unavailable"}


# ============================================================================
# 统一入口
# ============================================================================

#: 结构清单真值（StructGT）——与产物清单对齐比对的参照系
StructGT = dict


def extract(path: Path) -> StructGT:
    """按后缀分派，返回结构清单真值。"""
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".docx":
        return extract_docx(path)
    if suf == ".pdf":
        return extract_pdf(path)
    if suf == ".doc":
        # 旧版二进制无内置解析器（O-12 / C-35），不出数而非猜
        return {"kind": "doc", "source": path.name, "confidence": "unavailable",
                "headings": [], "tables": [], "list_items": None,
                "code_blocks": None, "links": None}
    raise ValueError(f"unsupported source type: {suf}")
