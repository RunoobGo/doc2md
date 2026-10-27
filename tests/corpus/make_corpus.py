#!/usr/bin/env python3
"""生成 Doc2md 综合语料（PDF/DOCX）——从简单到复杂、从小文件到大文件。

产物：
  sources/   可转换的 PDF/DOCX 源文件
  expected/  人工审阅后的目标 Markdown（正文，不含 YAML frontmatter）
  manifest.json  源文件哈希、页数、期望行为（ok / reject）

生成器只依赖 reportlab / python-docx / pypdf；不依赖 doc2md 转换结果，
目标 Markdown 由 `run_corpus.py --write-baseline` 生成后人工审阅。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

import pypdf
import reportlab.rl_config
from docx import Document
from docx.shared import Cm, Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"
sys.path.insert(0, str(HERE.parent))

from make_golden import freeze_docx  # noqa: E402  固定 DOCX zip 时间戳

reportlab.rl_config.invariant = 1
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
CJK = "STSong-Light"
MONO = "Courier"
W, H = A4


def _pdf(name: str):
    SOURCES.mkdir(parents=True, exist_ok=True)
    path = SOURCES / name
    if path.exists():
        path.unlink()
    return path, canvas.Canvas(str(path), pagesize=A4)


def _finish(c, path: Path, desc: str) -> None:
    c.save()
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  {desc}")


def _para(c, x, y, text, size=10.5, font=CJK):
    c.setFont(font, size)
    c.drawString(x, y, text)


def _add_uri_link(path: Path, page_index: int, rect: tuple[float, float, float, float],
                  uri: str) -> None:
    """给 PDF 某页加 URI Link 标注（与 make_golden 的 links_pdf 同口径）。"""
    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    page = writer.pages[page_index]
    annot = pypdf.generic.DictionaryObject()
    annot[pypdf.generic.NameObject("/Type")] = pypdf.generic.NameObject("/Annot")
    annot[pypdf.generic.NameObject("/Subtype")] = pypdf.generic.NameObject("/Link")
    annot[pypdf.generic.NameObject("/Rect")] = pypdf.generic.ArrayObject(
        [pypdf.generic.FloatObject(v) for v in rect])
    action = pypdf.generic.DictionaryObject()
    action[pypdf.generic.NameObject("/S")] = pypdf.generic.NameObject("/URI")
    action[pypdf.generic.NameObject("/URI")] = pypdf.generic.TextStringObject(uri)
    annot[pypdf.generic.NameObject("/A")] = action
    if "/Annots" not in page:
        page[pypdf.generic.NameObject("/Annots")] = pypdf.generic.ArrayObject()
    page["/Annots"].append(annot)
    with path.open("wb") as fh:
        writer.write(fh)


# --- PDF --------------------------------------------------------------------
def pdf_simple() -> None:
    path, c = _pdf("pdf_01_simple_1p.pdf")
    _para(c, 2 * cm, H - 2 * cm, "1. Doc2md 简单转换测试", 18)
    _para(c, 2 * cm, H - 3.2 * cm, "1.1 摘要", 14)
    _para(c, 2 * cm, H - 4.5 * cm, "这是一份用于验证 PDF 文本转换的最小样本。")
    _para(c, 2 * cm, H - 5.2 * cm, "第二段包含中文、English 与数字 2026。")
    _finish(c, path, "1 页：标题 + 段落 + 中英混排")


def pdf_lists() -> None:
    path, c = _pdf("pdf_02_lists_2p.pdf")
    _para(c, 2 * cm, H - 2 * cm, "1. 列表与分页测试", 18)
    _para(c, 2 * cm, H - 3.4 * cm, "1.1 有序列表", 14)
    for i, text in enumerate(("第一项内容", "第二项内容", "第三项内容"), 1):
        _para(c, 2 * cm, H - (4.4 + i * 0.7) * cm, f"{i}. {text}")
    c.showPage()
    _para(c, 2 * cm, H - 2 * cm, "1.2 无序列表", 14)
    for i, text in enumerate(("项目甲", "项目乙", "项目丙"), 1):
        _para(c, 2 * cm, H - (3.2 + i * 0.7) * cm, f"- {text}")
    _para(c, 2 * cm, H - 7 * cm, "列表结束后的普通段落。")
    _finish(c, path, "2 页：有序/无序列表 + 分页")


def pdf_table() -> None:
    path, c = _pdf("pdf_03_table_1p.pdf")
    _para(c, 2 * cm, H - 2 * cm, "1. 表格测试", 18)
    data = [["项目", "数量", "单价"], ["苹果", "3", "5.00"], ["香蕉", "2", "3.50"]]
    t = Table(data, colWidths=[5 * cm, 4 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("FONTNAME", (0, 0), (-1, -1), CJK),
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    tw, th = t.wrapOn(c, W - 4 * cm, H)
    t.drawOn(c, 2 * cm, H - 4.5 * cm - th)
    _para(c, 2 * cm, H - 4.5 * cm - th - 1.0 * cm, "表格下方的说明段落。")
    _finish(c, path, "1 页：3×3 网格表 + 表后说明")


def pdf_links_code() -> None:
    path, c = _pdf("pdf_04_links_code_2p.pdf")
    _para(c, 2 * cm, H - 2 * cm, "1. 链接与代码测试", 18)
    _para(c, 2 * cm, H - 3.5 * cm, "1.1 访问示例网站", 14)
    url = "https://example.com"
    _para(c, 2 * cm, H - 4.4 * cm, url, 11)
    c.setFont(MONO, 10)
    y = H - 6 * cm
    for line in ("def convert(path):", "    result = pipeline(path)", "    return result"):
        c.drawString(2 * cm, y, line)
        y -= 0.45 * cm
    c.showPage()
    _para(c, 2 * cm, H - 2 * cm, "代码说明", 14)
    _para(c, 2 * cm, H - 3.4 * cm, "上一页包含一个等宽代码块和外部链接。")
    _finish(c, path, "2 页：外部链接 + 等宽代码")
    # 链接标注覆盖 URL 那一行（PDF 坐标原点在左下）
    _add_uri_link(path, 0, (2 * cm, H - 4.55 * cm, 12 * cm, H - 4.15 * cm), url)


def pdf_cjk_en() -> None:
    path, c = _pdf("pdf_05_cjk_en_3p.pdf")
    for page in range(3):
        _para(c, 2 * cm, H - 2 * cm, f"第 {page + 1} 章 中英文混排", 18)
        _para(c, 2 * cm, H - 3.6 * cm, "本节验证中文、English、数字 123 与标点的抽取。", 11)
        for i in range(8):
            _para(c, 2 * cm, H - (4.8 + i * 0.7) * cm,
                  f"这是第 {page + 1} 页第 {i + 1} 段：内容同时包含中文和 English words。")
        c.showPage()
    _finish(c, path, "3 页：中文/英文/数字混排")


def pdf_scanned() -> None:
    path, c = _pdf("pdf_06_scanned_1p.pdf")
    c.setStrokeColorRGB(0.4, 0.4, 0.4)
    c.rect(2 * cm, 2 * cm, W - 4 * cm, H - 4 * cm)
    c.circle(W / 2, H / 2, 4 * cm)
    c.line(2 * cm, H / 2, W - 2 * cm, H / 2)
    _finish(c, path, "1 页：无文本层（预期门禁拒收）")


def pdf_numbered_105() -> None:
    path, c = _pdf("pdf_07_numbered_105pt_2p.pdf")
    _para(c, 2 * cm, H - 2 * cm, "1. 10.5pt 编号与正文测试", 18)
    lines = ["1. 第一项", "2. 第二项", "3. 第三项",
             "这是 10.5pt 正文段落，应保持为段落而不是标题。"]
    y = H - 3.6 * cm
    for text in lines:
        _para(c, 2 * cm, y, text, 10.5)
        y -= 0.65 * cm
    c.showPage()
    _para(c, 2 * cm, H - 2 * cm, "结语", 14)
    _para(c, 2 * cm, H - 3.4 * cm, "编号短行应被识别为列表；正文不应被抬成标题。")
    _finish(c, path, "2 页：10.5pt 编号行与正文")


def pdf_large() -> None:
    path, c = _pdf("pdf_08_large_40p.pdf")
    for page in range(40):
        _para(c, 2 * cm, H - 2 * cm, f"第 {page + 1} 章 大数据量样本", 18)
        _para(c, 2 * cm, H - 3.4 * cm, "1.1 章节说明", 14)
        y = H - 4.8 * cm
        for i in range(12):
            _para(c, 2 * cm, y,
                  f"第 {page + 1} 页第 {i + 1} 段：这是用于大文件转换测试的稳定文本。", 10.5)
            y -= 0.55 * cm
        c.showPage()
    _finish(c, path, "40 页：大文件/长文档")


def _long_para(seed: str, repeat: int = 6, width: int = 42) -> list[str]:
    """按固定宽度切出长段落行，模拟中文排版折行。"""
    text = seed * repeat
    return [text[i:i + width] for i in range(0, len(text), width)]


def _pdf_page_furniture(c, page: int, title: str = "综合长文档测试") -> None:
    c.setFont(CJK, 9)
    c.drawString(1.4 * cm, H - 1.2 * cm, title)
    c.drawRightString(W - 1.4 * cm, H - 1.2 * cm, "内部测试样本")
    c.drawCentredString(W / 2, 1.0 * cm, f"第 {page} 页 共 8 页")


def _draw_wrapped(c, lines: list[str], y: float, *, first_indent: float = 2.4 * cm,
                  cont_indent: float = 1.4 * cm, size=11, leading=0.55 * cm) -> float:
    for i, line in enumerate(lines):
        c.setFont(CJK, size)
        c.drawString(first_indent if i == 0 else cont_indent, y, line)
        y -= leading
    return y


def pdf_complex_long() -> None:
    """8 页复杂 PDF：长段落、跨页续段、中文序号、列表、表格、题注、页眉页脚。"""
    path, c = _pdf("pdf_09_complex_long_8p.pdf")
    long_a = ("这一段用于验证长段落折行、首行缩进与跨页续段，正文包含中文、English、"
              "数字 2026 和标点；排版器在固定宽度处折行，转换后应保持为同一段落。")
    long_b = ("综合验收关注结构而不是逐字复刻：标题层级、段落边界、列表编号、表格行列与"
              "图表题注都必须稳定，页眉页脚和页码不得进入 Markdown 正文。")
    for page in range(1, 9):
        _pdf_page_furniture(c, page)
        y = H - 2.2 * cm
        if page == 1:
            c.setFont(CJK, 20)
            c.drawString(1.4 * cm, y, "一、复杂长文档综合测试")
            y -= 1.2 * cm
            c.setFont(CJK, 12)
            c.drawString(1.4 * cm, y, "副标题：长段落、列表、表格与页码")
            y -= 1.0 * cm
            y = _draw_wrapped(c, _long_para(long_a, 5), y)
            c.setFont(CJK, 12)
            c.drawString(1.4 * cm, y - 0.4 * cm, "跨页续段开始：这一行没有句末标点")
        elif page == 2:
            c.setFont(CJK, 11)
            c.drawString(1.4 * cm, y, "这一段继续上一页的句子，直到这里才结束。")
            y -= 0.55 * cm
            y = _draw_wrapped(c, _long_para(long_b, 4), y)
            c.setFont(CJK, 16)
            c.drawString(1.4 * cm, y - 0.2 * cm, "二、列表与结构")
            y -= 1.0 * cm
            for i, item in enumerate(("第一项内容需要保持完整", "第二项内容包含 English",
                                      "第三项内容包含数字 2026"), 1):
                c.setFont(CJK, 11)
                c.drawString(1.4 * cm, y, f"{i}. {item}")
                y -= 0.55 * cm
        elif page == 3:
            c.setFont(CJK, 14)
            c.drawString(1.4 * cm, y, "（一）子标题与长段落")
            y -= 0.8 * cm
            y = _draw_wrapped(c, _long_para(long_b, 4), y)
            c.setFont(CJK, 11)
            for text in ("- 要点甲：结构稳定", "- 要点乙：段落完整", "- 要点丙：页码剔除"):
                c.drawString(1.4 * cm, y, text)
                y -= 0.55 * cm
        elif page == 4:
            c.setFont(CJK, 14)
            c.drawString(1.4 * cm, y, "三、表格与题注")
            y -= 1.0 * cm
            data = [["项目", "数量", "单价"],
                    ["甲", "3", "5.00"], ["乙", "2", "3.50"], ["丙", "10", "1.20"]]
            t = Table(data, colWidths=[5 * cm, 4 * cm, 4 * cm])
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), CJK),
                ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]))
            tw, th = t.wrapOn(c, W - 4 * cm, H)
            t.drawOn(c, 1.4 * cm, y - th)
            y -= th + 0.6 * cm
            c.setFont(CJK, 10.5)
            c.drawCentredString(W / 2, y, "表 1 综合数据表")
            y -= 0.8 * cm
            y = _draw_wrapped(c, _long_para(long_a, 4), y)
        elif page == 5:
            y = _draw_wrapped(c, _long_para(long_a, 4), y)
            c.setFont(CJK, 10.5)
            c.drawCentredString(W / 2, y - 0.2 * cm, "图 1 综合流程图")
            y -= 1.0 * cm
            c.setFont(CJK, 16)
            c.drawString(1.4 * cm, y, "四、跨栏与混排")
            y -= 0.9 * cm
            y = _draw_wrapped(c, _long_para(long_b, 4), y)
        elif page == 6:
            y = _draw_wrapped(c, _long_para(long_b, 4), y)
            c.setFont(CJK, 11)
            c.drawString(1.4 * cm, y, "1. 列表项第一行较长，需要在折行后继续")
            y -= 0.55 * cm
            c.drawString(1.4 * cm, y, "保持为同一个列表项而不是新段落。")
            y -= 0.8 * cm
            y = _draw_wrapped(c, _long_para(long_a, 4), y)
        elif page == 7:
            c.setFont(CJK, 14)
            c.drawString(1.4 * cm, y, "五、复杂表格与长单元格")
            y -= 1.0 * cm
            data = [["字段", "说明", "备注"],
                    ["甲字段", "这是一段较长的单元格说明文字用于验证单元格折行", "正常"],
                    ["乙字段", "另一段较长的单元格说明文字用于验证表格结构", "关注"],
                    ["丙字段", "短文本", "无"]]
            t = Table(data, colWidths=[4 * cm, 10 * cm, 3 * cm])
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), CJK),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            tw, th = t.wrapOn(c, W - 4 * cm, H)
            t.drawOn(c, 1.4 * cm, y - th)
            y -= th + 0.8 * cm
            y = _draw_wrapped(c, _long_para(long_b, 3), y)
        else:
            y = _draw_wrapped(c, _long_para(long_a, 4), y)
            c.setFont(CJK, 16)
            c.drawString(1.4 * cm, y, "六、结论")
            y -= 0.9 * cm
            y = _draw_wrapped(c, _long_para(long_b, 4), y)
        c.showPage()
    _finish(c, path, "8 页：长段落/跨页续段/列表/表格/题注/页眉页脚")


def pdf_two_column() -> None:
    """6 页双栏 PDF：左右栏各自长段落，页眉页脚与页码。"""
    path, c = _pdf("pdf_10_two_column_6p.pdf")
    left_seed = ("左栏用于验证阅读顺序与栏间分隔；左栏包含长段落、中文和数字 2026。"
                 "转换后左栏正文应完整出现在右栏之前。")
    right_seed = ("右栏用于验证阅读顺序与栏间分隔；右栏包含长段落、中文和数字 2026。"
                  "转换后右栏正文不得与左栏逐行交错。")
    for page in range(1, 7):
        c.setFont(CJK, 9)
        c.drawString(1.2 * cm, H - 1.1 * cm, "双栏长文档测试")
        c.drawRightString(W - 1.2 * cm, H - 1.1 * cm, "内部测试样本")
        c.drawCentredString(W / 2, 0.9 * cm, f"— {page} —")
        c.setFont(CJK, 8)
        c.drawCentredString(W / 2, H / 2, "│")
        for col, (x, seed) in enumerate(((1.2 * cm, left_seed), (10.6 * cm, right_seed))):
            y = H - 2.0 * cm
            c.setFont(CJK, 14 if page == 1 and col == 0 else 12)
            c.drawString(x, y, f"第 {page} 页第 {col + 1} 栏")
            y -= 0.9 * cm
            for line in _long_para(seed, 4, width=22):
                c.setFont(CJK, 10)
                c.drawString(x, y, line)
                y -= 0.5 * cm
        c.showPage()
    _finish(c, path, "6 页：双栏长段落 + 页眉页脚/页码")


def pdf_wrap_space() -> None:
    """1 页：折行点精确落在空格上的定向样本（O-01 / QA-D2M-2026-10-21 / C-04）。

    `extract_text_lines` 把行缘空白从行字典（text/chars/bbox）整体剥掉，折行恰好
    切在空格上时，S3 的跨行拼接必须凭 page.chars 的几何证据把空格补回。本样本
    以显式画行的方式覆盖六种形态（原文空格的有无 × 边界类型），全部缝合成同一
    段落（无硬句末、非结构起始）：
      A 行尾证据：行以 "……数字 " 收尾，空格画在本行行尾（trailing_space）；
      B 行首证据：行以 " 2026……" 起头，空格画在下一行行首（leading_space）；
      C 行首证据：中文与 English 单词边界；
      D 行首证据：English 到中文的反向边界；
      E 行首证据：2026 到中文的数字汉字边界；
      F 反例：原文无空格（"……相接" + "的片段……"），不得插入空格。
    """
    path, c = _pdf("pdf_11_wrap_space_1p.pdf")
    c.setFont(CJK, 16)
    c.drawString(1.4 * cm, H - 2.2 * cm, "七、折行空白保真")
    lines = [
        # 首行（首行缩进 2.4cm）；段内不得出现硬句末（。！？），否则 S3 切段
        "折行空白保真测试：原文在折行处留有的空格必须原样保留，",
        # A：行尾空格证据（行尾一个空格，中文与数字边界）
        "覆盖中文与数字 ",
        "2026 和标点的边界；",
        # B：行首空格证据（行首一个空格，中文与数字边界）
        "还包括中文与数字",
        " 2026 同年发布的文本；",
        # C：行首空格证据（中文与英文单词边界）
        "中英混排时中文与",
        " English 单词之间的空格；",
        # D：行首空格证据（英文到中文反向边界）
        "以及 English 到中文的",
        " 反向边界同样保留；",
        # E：行首空格证据（数字到中文边界）
        "还包括 2026",
        " 年之后的数字汉字边界；",
        # F：反例——原文无空格，缝回时不得插入
        "反例里中西文直接相接",
        "的片段原文没有空格，也不得插入空格，缝合成同一段落。",
    ]
    y = H - 3.4 * cm
    for i, line in enumerate(lines):
        c.setFont(CJK, 11)
        c.drawString(2.4 * cm if i == 0 else 1.4 * cm, y, line)
        y -= 0.55 * cm
    c.showPage()
    _finish(c, path, "1 页：折行落在空格上的六形态定向样本（O-01）")


# --- DOCX -------------------------------------------------------------------
def _docx(name: str) -> tuple[Path, Document]:
    SOURCES.mkdir(parents=True, exist_ok=True)
    path = SOURCES / name
    if path.exists():
        path.unlink()
    return path, Document()


def _mono(paragraph, text: str, size=10) -> None:
    run = paragraph.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(size)


def _hyperlink(paragraph, url: str, text: str) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    part = paragraph.part
    rid = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _revision_run(text: str, deleted: bool = False):
    from docx.oxml import OxmlElement
    r = OxmlElement("w:r")
    t = OxmlElement("w:delText" if deleted else "w:t")
    t.text = text
    r.append(t)
    return r


def _revision(tag: str, rev_id: int, run_el):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    el = OxmlElement(tag)
    el.set(qn("w:id"), str(rev_id))
    el.set(qn("w:author"), "corpus")
    el.set(qn("w:date"), "2024-10-13T00:00:00Z")
    el.append(run_el)
    return el


def docx_simple() -> None:
    path, d = _docx("docx_01_simple.docx")
    d.add_heading("Doc2md DOCX 简单测试", level=1)
    d.add_heading("摘要", level=2)
    d.add_paragraph("这是普通段落，包含中文与 English。")
    d.add_heading("加粗", level=2)
    p = d.add_paragraph()
    r = p.add_run("整段加粗")
    r.bold = True
    d.add_heading("斜体", level=2)
    p = d.add_paragraph()
    r = p.add_run("整段斜体")
    r.italic = True
    d.add_heading("删除线", level=2)
    p = d.add_paragraph()
    r = p.add_run("整段删除线（默认输出不保留删除线语法）")
    r.font.strike = True
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 标题/段落/格式")


def docx_lists() -> None:
    path, d = _docx("docx_02_lists.docx")
    d.add_heading("DOCX 列表测试", level=1)
    for text in ("项目甲", "项目乙", "项目丙"):
        d.add_paragraph(text, style="List Bullet")
    for text in ("第一项", "第二项", "第三项"):
        d.add_paragraph(text, style="List Number")
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 项目符号/编号列表")


def docx_tables() -> None:
    path, d = _docx("docx_03_tables.docx")
    d.add_heading("DOCX 表格测试", level=1)
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate((("项目", "数量", "单价"), ("苹果", "3", "5.00"), ("香蕉", "2", "3.50"))):
        for c, val in enumerate(row):
            t.cell(r, c).text = val
    t2 = d.add_table(rows=2, cols=3)
    t2.cell(0, 0).text = "合并列"
    t2.cell(0, 1).text = "值"
    t2.cell(0, 2).text = "备注"
    t2.cell(1, 0).text = "A"
    t2.cell(1, 1).text = "B"
    t2.cell(1, 2).text = "C"
    t2.cell(0, 1).merge(t2.cell(0, 2))
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 普通表 + 合并单元格")


def docx_links_notes() -> None:
    from docx.opc.constants import CONTENT_TYPE as CT
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import XmlPart
    from docx.oxml import parse_xml

    path, d = _docx("docx_04_links_notes.docx")
    d.add_heading("DOCX 链接与注释测试", level=1)
    p = d.add_paragraph()
    _hyperlink(p, "https://example.com", "示例网站")
    d.add_paragraph("上面的链接文字完整覆盖段落时应输出 Markdown 链接。")
    _NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    footnotes = (f"<w:footnotes {_NS}>"
                 '<w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                 '<w:footnote w:type="continuationSeparator"><w:p><w:r><w:continuationSeparator/>'
                 '</w:r></w:p></w:footnote>'
                 '<w:footnote w:id="2"><w:p><w:r><w:t>脚注：这是第一条注文。</w:t></w:r></w:p>'
                 '</w:footnote></w:footnotes>')
    endnotes = (f"<w:endnotes {_NS}>"
                '<w:endnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
                '<w:endnote w:id="2"><w:p><w:r><w:t>尾注：这是尾注内容。</w:t></w:r></w:p>'
                '</w:endnote></w:endnotes>')
    pkg = d.part.package
    d.part.relate_to(XmlPart(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES,
                             parse_xml(footnotes), pkg), RT.FOOTNOTES)
    d.part.relate_to(XmlPart(PackURI("/word/endnotes.xml"), CT.WML_ENDNOTES,
                             parse_xml(endnotes), pkg), RT.ENDNOTES)
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 超链接/脚注/尾注")


def docx_revisions_fields() -> None:
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import qn

    path, d = _docx("docx_05_revisions_fields.docx")
    d.add_heading("DOCX 修订与域代码测试", level=1)
    d.add_paragraph("普通段落。")
    p = d.add_paragraph("插入前，")
    p._p.append(_revision("w:ins", 1, _revision_run("插入的修订文本。")))
    p._p.append(_revision("w:del", 2, _revision_run("删除的旧文本。", True)))
    d.add_heading("域代码", level=2)
    p = d.add_paragraph("生效日期：")
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "DATE")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "2026-10-18"
    r.append(t)
    fld.append(r)
    p._p.append(fld)
    d.add_heading("智能标记", level=2)
    p = d.add_paragraph()
    smart = OxmlElement("w:smartTag")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "智能标记正文"
    r.append(t)
    smart.append(r)
    p._p.append(smart)
    d.add_heading("内容控件", level=2)
    # 块级 sdt：内容控件中的段落
    body = d.element.body
    sdt = parse_xml(
        '<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:sdtContent><w:p><w:r><w:t>内容控件中的段落</w:t></w:r></w:p>'
        '</w:sdtContent></w:sdt>')
    body.insert(len(body) - 1, sdt)
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 修订/域/smartTag/sdt")


def docx_textbox_image() -> None:
    from docx.oxml import parse_xml

    path, d = _docx("docx_06_textbox_image.docx")
    d.add_heading("DOCX 文本框与图片测试", level=1)
    p = d.add_paragraph()
    p.add_run("前")
    ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"')
    p._p.append(parse_xml(
        f'<w:r {ns}><w:drawing><wp:inline><a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:txbx><w:txbxContent><w:p><w:r><w:t>框内文字</w:t></w:r></w:p>'
        '</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r>'))
    p.add_run("后")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
    d.add_picture(io.BytesIO(png), width=Cm(2))
    d.add_page_break()
    d.add_paragraph("分页后的正文。")
    section = d.sections[0]
    section.header.paragraphs[0].text = "页眉：不应进入 Markdown"
    section.footer.paragraphs[0].text = "页脚：不应进入 Markdown"
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 文本框/图片/分页/页眉页脚")


def docx_code_quote() -> None:
    path, d = _docx("docx_07_code_quote.docx")
    d.add_heading("DOCX 代码与引用测试", level=1)
    for text in ("def convert(path):", "    result = pipeline(path)", "    return result"):
        p = d.add_paragraph()
        _mono(p, text)
    d.add_paragraph("代码段结束后的普通段落。")
    d.add_paragraph("> 这是一段引用文本，应输出为 blockquote。")
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 等宽代码/引用")


def docx_large() -> None:
    path, d = _docx("docx_08_large_30sections.docx")
    for sec in range(1, 31):
        d.add_heading(f"第 {sec} 章 大数据量 DOCX", level=1)
        d.add_heading("1.1 章节说明", level=2)
        for i in range(4):
            d.add_paragraph(f"第 {sec} 章第 {i + 1} 段：用于大文件转换测试的稳定文本。")
        d.add_paragraph(f"第 {sec} 章要点甲", style="List Bullet")
        d.add_paragraph(f"第 {sec} 章要点乙", style="List Bullet")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text = "字段"
        t.cell(0, 1).text = "值"
        t.cell(1, 0).text = f"章节 {sec}"
        t.cell(1, 1).text = str(sec * 10)
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 30 节大文档")


def docx_mixed() -> None:
    path, d = _docx("docx_09_mixed_cn_en.docx")
    d.add_heading("中英文混排与编号", level=1)
    for i in range(1, 6):
        d.add_heading(f"{i}. 章节 {i}", level=2)
        d.add_paragraph(f"第 {i} 节包含中文、English、数字 {i * 10} 与标点。")
        d.add_paragraph(f"要点 {i}", style="List Bullet")
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 中英文混排/编号")


def _docx_page_field(section) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p = section.footer.paragraphs[0]
    p.text = "第 "
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    p._p.append(fld)
    run = p.add_run(" 页")
    run.font.size = Pt(9)


def _docx_footnote(d, text: str, note_id: int = 2) -> None:
    from docx.opc.constants import CONTENT_TYPE as CT
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import XmlPart
    from docx.oxml import parse_xml
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    xml = (f"<w:footnotes {ns}>"
           '<w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
           '<w:footnote w:type="continuationSeparator"><w:p><w:r>'
           '<w:continuationSeparator/></w:r></w:p></w:footnote>'
           f'<w:footnote w:id="{note_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
           '</w:footnote></w:footnotes>')
    pkg = d.part.package
    d.part.relate_to(XmlPart(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES,
                             parse_xml(xml), pkg), RT.FOOTNOTES)


def docx_complex_long() -> None:
    path, d = _docx("docx_10_complex_long.docx")
    d.add_heading("复杂长文 DOCX 综合测试", level=1)
    seed = ("这一段用于验证超长段落在 Word 中的折行、分页与结构保持，包含中文、English、"
            "数字 2026、标点以及较长的业务描述，转换后应保持为完整段落。")
    for sec in range(1, 9):
        d.add_heading(f"第 {sec} 章 长段落与结构", level=1)
        d.add_heading(f"{sec}.1 背景", level=2)
        for _ in range(3):
            d.add_paragraph(seed * 3)
        d.add_heading(f"{sec}.2 要点", level=3)
        for i in range(1, 5):
            d.add_paragraph(f"第 {sec} 章要点 {i}", style="List Bullet")
        d.add_paragraph(f"第 {sec} 章编号项 {i}", style="List Number")
        if sec == 3:
            d.add_page_break()
            t = d.add_table(rows=4, cols=3)
            t.cell(0, 0).text = "字段"
            t.cell(0, 1).text = "说明"
            t.cell(0, 2).text = "备注"
            t.cell(0, 1).merge(t.cell(0, 2))
            for r in range(1, 4):
                t.cell(r, 0).text = f"字段 {r}"
                t.cell(r, 1).text = ("较长单元格文本用于验证合并单元格之后的读取顺序，"
                                     "以及表格在长文档中的位置稳定性。")
                t.cell(r, 2).text = "正常"
        if sec == 5:
            _docx_footnote(d, "脚注：复杂长文样本的注文。", note_id=2)
    section = d.sections[0]
    section.header.paragraphs[0].text = "复杂长文 DOCX 页眉"
    section.footer.paragraphs[0].text = "复杂长文 DOCX 页脚"
    _docx_page_field(section)
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 长段落/多级标题/合并表/页眉页脚/脚注")


def docx_complex_tables() -> None:
    path, d = _docx("docx_11_complex_tables.docx")
    d.add_heading("复杂表格 DOCX 测试", level=1)
    d.add_paragraph("本样本包含合并单元格、长单元格、单元格内列表和多张相邻表格。")
    for idx in range(1, 6):
        d.add_heading(f"{idx}. 表格组", level=2)
        d.add_paragraph(f"表 {idx} 说明段落。")
        t = d.add_table(rows=4, cols=4)
        t.cell(0, 0).text = "项目"
        t.cell(0, 1).text = "说明"
        t.cell(0, 2).text = "数量"
        t.cell(0, 3).text = "备注"
        t.cell(0, 1).merge(t.cell(0, 2))
        for r in range(1, 4):
            t.cell(r, 0).text = f"项目 {idx}-{r}"
            cell = t.cell(r, 1)
            cell.text = ("长单元格文本：包含中文、English 与数字，用于验证单元格内折行和"
                         "表格读取顺序。")
            cell.add_paragraph("单元格内第二段。")
            cell.add_paragraph("单元格内列表项", style="List Bullet")
            t.cell(r, 2).text = str(r * 10)
            t.cell(r, 3).text = "正常"
        d.add_page_break()
    d.add_paragraph("表格组结束后的总结段落。")
    d.save(path)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  DOCX 复杂表格/合并单元格/长单元格")


# --- OPT4-4：LibreOffice 形态的 DOCX 编号语料 ---------------------------------
# 三份样本对准 `_DocxNumbering.marker` 修复（CH-504 / R19-PIPE-3）守卫的两半：
#   ① 样式级 numPr → numFmt="none" + 空 lvlText（LibreOffice 写出的 Heading1..9）；
#   ② bullet + 非空 lvlText（反向守卫：真实项目符号不得被削掉）；
#   ③ 多级 %1.%2.%3. + 中间层「元素存在但 lvlText 为空」（守卫第二半的深层形态）。
# 语料腿的验收判据：去掉守卫 → `run_corpus.py` 必须转红（原先只有 gate 4 项会红）。
_WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NUMBERING_NONE = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{_WML_NS}">
 <w:abstractNum w:abstractNumId="10">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="none"/><w:lvlText w:val=""/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="none"/><w:lvlText w:val=""/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
</w:numbering>'''
_NUMBERING_BULLET = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{_WML_NS}">
 <w:abstractNum w:abstractNumId="10">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="o"/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="§"/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
</w:numbering>'''
_NUMBERING_MULTILEVEL = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{_WML_NS}">
 <w:abstractNum w:abstractNumId="10">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val=""/></w:lvl>
  <w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3."/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
</w:numbering>'''


def _docx_numpr(paragraph, num_id: int, ilvl: int) -> None:
    """段落级 `w:numPr`（python-docx 未暴露该属性）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    pPr = paragraph._p.get_or_add_pPr()
    numPr = OxmlElement("w:numPr")
    lvl = OxmlElement("w:ilvl")
    lvl.set(qn("w:val"), str(ilvl))
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    numPr.append(lvl)
    numPr.append(num)
    pPr.append(numPr)


def _inject_docx_numbering(path: Path, numbering_xml: str,
                           style_levels: dict[str, tuple[int, int]] | None = None) -> None:
    """替换 `word/numbering.xml`，可给段落样式挂样式级 numPr。

    `style_levels`：{styleId: (numId, ilvl)}——LibreOffice 把标题编号写成
    Heading1..9 的样式级 numPr，而不是段落级；这是本组语料的主要形态。
    改完 zip 后由调用方再跑 `freeze_docx`（本函数不负责时间戳冻结）。
    """
    import zipfile

    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import qn
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        items = [(n, z.read(n)) for n in z.namelist()]
    out = []
    for name, data in items:
        if name == "word/numbering.xml":
            data = numbering_xml.encode("utf-8")
        elif name == "word/styles.xml" and style_levels:
            root = parse_xml(data)
            for sid, (nid, ilvl) in style_levels.items():
                st = next((s for s in root.findall(f"{{{_WML_NS}}}style")
                           if s.get(f"{{{_WML_NS}}}styleId") == sid), None)
                if st is None:
                    raise SystemExit(f"style {sid} missing from default template")
                pPr = st.find(f"{{{_WML_NS}}}pPr")
                if pPr is None:
                    pPr = OxmlElement("w:pPr")
                    st.insert(0, pPr)
                numPr = OxmlElement("w:numPr")
                lvl = OxmlElement("w:ilvl")
                lvl.set(qn("w:val"), str(ilvl))
                num = OxmlElement("w:numId")
                num.set(qn("w:val"), str(nid))
                numPr.append(lvl)
                numPr.append(num)
                # pPr 子元素有固定顺序（ECMA-376 §17.3.1.26）：numPr 必须在
                # spacing / ind / outlineLvl 之前，否则 Word 视为损坏包。
                anchor = None
                for tag in ("suppressLineNumbers", "pBdr", "shd", "tabs",
                            "suppressAutoHyphens", "kinsoku", "wordWrap",
                            "overflowPunct", "topLinePunct", "autoSpaceDE",
                            "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
                            "spacing", "ind", "contextualSpacing", "mirrorIndents",
                            "suppressOverlap", "jc", "textDirection",
                            "textAlignment", "textboxTightWrap", "outlineLvl",
                            "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"):
                    anchor = pPr.find(f"{{{_WML_NS}}}{tag}")
                    if anchor is not None:
                        anchor.addprevious(numPr)
                        break
                if anchor is None:
                    pPr.append(numPr)
            data = etree.tostring(root.getroottree(), xml_declaration=True,
                                  encoding="UTF-8", standalone=True)
        out.append((name, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in out:
            z.writestr(name, data)


def docx_lo_heading_numbering() -> None:
    """LibreOffice 形态：Heading1/2 挂样式级 numPr → numFmt=none + 空 lvlText。"""
    path, d = _docx("docx_12_lo_heading_numbering.docx")
    d.add_heading("总则", level=1)
    d.add_paragraph("本样本模拟 LibreOffice 写出的标题自动编号形态。")
    d.add_heading("编制目的", level=2)
    d.add_paragraph("标题样式指向的抽象编号每一级都是 numFmt=none。")
    d.add_heading("适用范围", level=2)
    d.add_paragraph("按 ECMA-376 §17.9.19，none 的语义是「不显示编号」。")
    d.add_heading("附则", level=1)
    d.add_paragraph("转换后标题不得带任何数字前缀。")
    d.save(path)
    _inject_docx_numbering(path, _NUMBERING_NONE,
                           {"Heading1": (1, 0), "Heading2": (1, 1)})
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  "
          "DOCX 样式级 numPr + numFmt=none（LibreOffice 形态）")


def docx_bullet_glyph() -> None:
    """bullet 编号：lvlText 是字形 o / §（非数字），反向守卫真实项目符号。"""
    path, d = _docx("docx_13_bullet_glyph.docx")
    d.add_heading("项目符号字形测试", level=1)
    d.add_paragraph("下面三个项目符号的 lvlText 是字形 o，不是数字。")
    for text in ("第一条要点", "第二条要点", "第三条要点"):
        _docx_numpr(d.add_paragraph(text), 1, 0)
    d.add_paragraph("下面一行是二级项目符号，lvlText 为 §。")
    _docx_numpr(d.add_paragraph("子要点"), 1, 1)
    d.save(path)
    _inject_docx_numbering(path, _NUMBERING_BULLET)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  "
          "DOCX bullet + 非空 lvlText（o / §）")


def docx_multilevel_hidden() -> None:
    """多级编号：中间层 lvlText 元素存在但为空 → 本级不显示编号，计数器仍推进。"""
    path, d = _docx("docx_14_multilevel_hidden.docx")
    d.add_heading("多级编号隐藏中间层", level=1)
    rows = [
        (0, "总则与适用范围"),
        (1, "隐藏层级条目：本级 lvlText 为空，不显示编号，但计数器仍推进"),
        (2, "第一条细则"),
        (2, "第二条细则"),
        (0, "附则"),
    ]
    for ilvl, text in rows:
        _docx_numpr(d.add_paragraph(text), 1, ilvl)
    d.save(path)
    _inject_docx_numbering(path, _NUMBERING_MULTILEVEL)
    freeze_docx(path)
    print(f"  ✓ {path.name:36s} {path.stat().st_size:>9,} bytes  "
          "DOCX 多级 %1.%2.%3. + 中间层空 lvlText")


CASES = [
    ("pdf_01_simple_1p.pdf", "pdf", "ok"),
    ("pdf_02_lists_2p.pdf", "pdf", "ok"),
    ("pdf_03_table_1p.pdf", "pdf", "ok"),
    ("pdf_04_links_code_2p.pdf", "pdf", "ok"),
    ("pdf_05_cjk_en_3p.pdf", "pdf", "ok"),
    ("pdf_06_scanned_1p.pdf", "pdf", "reject:E4221"),
    ("pdf_07_numbered_105pt_2p.pdf", "pdf", "ok"),
    ("pdf_08_large_40p.pdf", "pdf", "ok"),
    ("pdf_09_complex_long_8p.pdf", "pdf", "ok"),
    ("pdf_10_two_column_6p.pdf", "pdf", "ok"),
    ("pdf_11_wrap_space_1p.pdf", "pdf", "ok"),
    ("docx_01_simple.docx", "docx", "ok"),
    ("docx_02_lists.docx", "docx", "ok"),
    ("docx_03_tables.docx", "docx", "ok"),
    ("docx_04_links_notes.docx", "docx", "ok"),
    ("docx_05_revisions_fields.docx", "docx", "ok"),
    ("docx_06_textbox_image.docx", "docx", "ok"),
    ("docx_07_code_quote.docx", "docx", "ok"),
    ("docx_08_large_30sections.docx", "docx", "ok"),
    ("docx_09_mixed_cn_en.docx", "docx", "ok"),
    ("docx_10_complex_long.docx", "docx", "ok"),
    ("docx_11_complex_tables.docx", "docx", "ok"),
    ("docx_12_lo_heading_numbering.docx", "docx", "ok"),
    ("docx_13_bullet_glyph.docx", "docx", "ok"),
    ("docx_14_multilevel_hidden.docx", "docx", "ok"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重新生成全部源文件")
    _ = ap.parse_args()
    SOURCES.mkdir(parents=True, exist_ok=True)
    EXPECTED.mkdir(parents=True, exist_ok=True)
    print("生成 PDF 语料 ...")
    pdf_simple()
    pdf_lists()
    pdf_table()
    pdf_links_code()
    pdf_cjk_en()
    pdf_scanned()
    pdf_numbered_105()
    pdf_large()
    pdf_complex_long()
    pdf_two_column()
    pdf_wrap_space()
    print("生成 DOCX 语料 ...")
    docx_simple()
    docx_lists()
    docx_tables()
    docx_links_notes()
    docx_revisions_fields()
    docx_textbox_image()
    docx_code_quote()
    docx_large()
    docx_mixed()
    docx_complex_long()
    docx_complex_tables()
    docx_lo_heading_numbering()
    docx_bullet_glyph()
    docx_multilevel_hidden()
    manifest = []
    for name, kind, behavior in CASES:
        p = SOURCES / name
        if not p.is_file():
            raise SystemExit(f"missing generated source: {p}")
        manifest.append({
            "source": name,
            "kind": kind,
            "expected_behavior": behavior,
            "expected_markdown": None if behavior.startswith("reject") else f"expected/{Path(name).stem}.md",
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {HERE / 'manifest.json'}（{len(manifest)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
