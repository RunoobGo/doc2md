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


CASES = [
    ("pdf_01_simple_1p.pdf", "pdf", "ok"),
    ("pdf_02_lists_2p.pdf", "pdf", "ok"),
    ("pdf_03_table_1p.pdf", "pdf", "ok"),
    ("pdf_04_links_code_2p.pdf", "pdf", "ok"),
    ("pdf_05_cjk_en_3p.pdf", "pdf", "ok"),
    ("pdf_06_scanned_1p.pdf", "pdf", "reject:E4221"),
    ("pdf_07_numbered_105pt_2p.pdf", "pdf", "ok"),
    ("pdf_08_large_40p.pdf", "pdf", "ok"),
    ("docx_01_simple.docx", "docx", "ok"),
    ("docx_02_lists.docx", "docx", "ok"),
    ("docx_03_tables.docx", "docx", "ok"),
    ("docx_04_links_notes.docx", "docx", "ok"),
    ("docx_05_revisions_fields.docx", "docx", "ok"),
    ("docx_06_textbox_image.docx", "docx", "ok"),
    ("docx_07_code_quote.docx", "docx", "ok"),
    ("docx_08_large_30sections.docx", "docx", "ok"),
    ("docx_09_mixed_cn_en.docx", "docx", "ok"),
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
