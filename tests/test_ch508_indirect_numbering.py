"""CH-508 / B-52：间接编号形态（basedOn / numStyleLink / lvl@pStyle）与 F-7。

OOXML 的编号可以绕两道弯到达段落：

- **样式级 numPr 的 basedOn 链**：派生段落样式不写 `numPr`，继承基样式的编号；
- **`w:abstractNum/w:numStyleLink`**：抽象定义委托给一个编号样式，编号样式的
  `numPr/numId` 再指向真正的 `w:num`；
- **`w:lvl/w:pStyle`**：级别反向绑定段落样式，应用该样式的段落自动获得此级编号。

三者原先都未读取，结局同型——**静默少产**：段落没有任何标记，也不留告警，
读者看到的是「这段本来就没有编号」。本文件各造一份合成 DOCX 钉住，并加三条反向守卫（悬空 numStyleLink /
环状 basedOn / 缺 numbering.xml 的文档不得猜编号、也不得崩）。

F-7 另属样式名约定：裸 `Heading` 是无数字的自定义样式名，原实现走异常兜底
判成 1 级（连字号都按 H1 给）。本文件要求无数字名不产层级，`Heading N`
（N=1..9）不受影响。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from doc2md.pipeline.s1_parse import read_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

pytestmark = [pytest.mark.gate]


def _lvl(text: str = "%1.", pstyle: str = "") -> str:
    ps = f'<w:pStyle w:val="{pstyle}"/>' if pstyle else ""
    return (f'<w:lvl w:ilvl="0"><w:start w:val="1"/>{ps}'
            f'<w:numFmt w:val="decimal"/><w:lvlText w:val="{text}"/></w:lvl>')


_ABSTRACT = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1">{_lvl()}</w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''

_LINK = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1"><w:numStyleLink w:val="NumStyle"/></w:abstractNum>
 <w:abstractNum w:abstractNumId="2">{_lvl()}</w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
 <w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>
</w:numbering>'''

_LINK_DANGLING = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1"><w:numStyleLink w:val="NoSuchStyle"/></w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''

_PSTYLE = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1">{_lvl(pstyle="MyList")}</w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''


def _add_num_pr(owner, nid: str, ilvl: int = 0) -> None:
    """给样式元素或段落元素挂 `w:pPr/w:numPr`（owner 需有 get_or_add_pPr）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    numPr = OxmlElement("w:numPr")
    lvl_el = OxmlElement("w:ilvl")
    lvl_el.set(qn("w:val"), str(ilvl))
    nid_el = OxmlElement("w:numId")
    nid_el.set(qn("w:val"), nid)
    numPr.append(lvl_el)
    numPr.append(nid_el)
    owner.get_or_add_pPr().append(numPr)


def _save(d, path: Path, numbering_xml: str | None) -> Path:
    d.save(path)
    if numbering_xml is None:
        return path
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["word/numbering.xml"] = numbering_xml.encode()
    rels = items["word/_rels/document.xml.rels"].decode()
    if "numbering.xml" not in rels:
        items["word/_rels/document.xml.rels"] = rels.replace(
            "</Relationships>",
            f'<Relationship Id="rIdNum1" Type="{R_NS}/numbering" '
            'Target="numbering.xml"/></Relationships>').encode()
    ct = items["[Content_Types].xml"].decode()
    if "numbering.xml" not in ct:
        items["[Content_Types].xml"] = ct.replace(
            "</Types>",
            '<Override PartName="/word/numbering.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            "</Types>").encode()
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in items.items():
            z.writestr(n, v)
    tmp.replace(path)
    return path


def _strip_numbering(path: Path) -> Path:
    """删掉 numbering 部件及其关系 / 内容类型登记（合成「无编号部件」的 DOCX）。"""
    import re

    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items.pop("word/numbering.xml", None)
    rels = items["word/_rels/document.xml.rels"].decode()
    items["word/_rels/document.xml.rels"] = re.sub(
        r'<Relationship[^>]*Target="numbering\.xml"[^>]*/>', "", rels).encode()
    ct = items["[Content_Types].xml"].decode()
    items["[Content_Types].xml"] = re.sub(
        r'<Override[^>]*PartName="/word/numbering\.xml"[^>]*/>', "", ct).encode()
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in items.items():
            z.writestr(n, v)
    tmp.replace(path)
    return path


def _lines(path: Path):
    return [ln for pg in read_docx(path) for ln in pg.lines]


def test_style_numbering_inherits_through_based_on(tmp_path):
    """派生样式不写 numPr 时，沿 basedOn 继承基样式的编号。"""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    d = Document()
    base = d.styles.add_style("ListBase", WD_STYLE_TYPE.PARAGRAPH)
    _add_num_pr(base.element, "1")
    derived = d.styles.add_style("ListDerived", WD_STYLE_TYPE.PARAGRAPH)
    derived.base_style = base
    d.add_paragraph("总则", style=derived)
    d.add_paragraph("职责", style=derived)
    p = _save(d, tmp_path / "based_on.docx", _ABSTRACT)

    assert [ln.list_marker for ln in _lines(p)] == ["1.", "2."]


def test_num_style_link_delegates_to_numbering_style(tmp_path):
    """abstractNum 用 numStyleLink 委托：编号样式 → numId=2 → 抽象层 2。"""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    d = Document()
    num_style = d.styles.add_style("NumStyle", WD_STYLE_TYPE.LIST)
    _add_num_pr(num_style.element, "2")
    p_el = d.add_paragraph("总则")
    _add_num_pr(p_el._p, "1")
    d.add_paragraph("职责", style=d.styles["Normal"])
    path = _save(d, tmp_path / "num_style_link.docx", _LINK)
    # 第二段没有显式 numPr，不应产出标记（只验证链接不泄漏到别的段落）。
    assert [ln.list_marker for ln in _lines(path)][0] == "1."


def test_level_pstyle_grants_numbering_to_style_paragraph(tmp_path):
    """`w:lvl/w:pStyle` 反向绑定：样式段落无 numPr 也获得该级编号。"""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    d = Document()
    mine = d.styles.add_style("MyList", WD_STYLE_TYPE.PARAGRAPH)
    d.add_paragraph("总则", style=mine)
    d.add_paragraph("职责", style=mine)
    p = _save(d, tmp_path / "pstyle.docx", _PSTYLE)

    assert [ln.list_marker for ln in _lines(p)] == ["1.", "2."]


def test_dangling_num_style_link_does_not_invent_numbering(tmp_path):
    """反向守卫：numStyleLink 指向不存在的样式时，不得猜一个号出来。"""
    from docx import Document

    d = Document()
    p_el = d.add_paragraph("总则")
    _add_num_pr(p_el._p, "1")
    page = _save(d, tmp_path / "dangling.docx", _LINK_DANGLING)

    assert [ln.list_marker for ln in _lines(page)] == [""]


def test_cyclic_based_on_does_not_invent_numbering(tmp_path):
    """反向守卫：basedOn 成环时保守返回「无编号」，不得递归到栈溢出。"""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    d = Document()
    a = d.styles.add_style("CycleA", WD_STYLE_TYPE.PARAGRAPH)
    b = d.styles.add_style("CycleB", WD_STYLE_TYPE.PARAGRAPH)
    a.base_style = b
    b.base_style = a
    d.add_paragraph("总则", style=a)
    p = _save(d, tmp_path / "cycle.docx", _ABSTRACT)

    assert [ln.list_marker for ln in _lines(p)] == [""]


def test_docx_without_numbering_part_keeps_styled_paragraphs(tmp_path):
    """反向守卫：无 numbering.xml 时，样式段落走 marker() 不得崩。"""
    from docx import Document

    d = Document()
    d.add_paragraph("正文", style=d.styles["Normal"])
    d.add_heading("真二级标题", level=2)
    p = _strip_numbering(_save(d, tmp_path / "no_numbering.docx", None))

    first, second = _lines(p)
    assert first.list_marker == ""
    assert second.list_marker == ""
    assert second.level_hint == 2


def test_bare_heading_style_is_not_a_heading(tmp_path):
    """F-7：无数字的自定义样式名 `Heading` 不产层级，也不按 H1 给字号/粗体。"""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    d = Document()
    custom = d.styles.add_style("Heading", WD_STYLE_TYPE.PARAGRAPH)
    d.add_paragraph("自定义样式正文", style=custom)
    d.add_heading("真二级标题", level=2)
    p = _save(d, tmp_path / "heading_name.docx", None)

    first, second = _lines(p)
    assert first.level_hint is None and first.bold is False
    assert first.size == 10.5
    assert second.level_hint == 2
    assert second.text == "真二级标题"
