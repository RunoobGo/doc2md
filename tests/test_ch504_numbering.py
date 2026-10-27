"""CH-504 / 管线：`numFmt="none"` 与空 `lvlText` 不得凭空造出编号标记。

LibreOffice 写出的 DOCX（含 `.doc` → `.docx` 中间件）给 `Heading1..9` 样式挂上
样式级 `w:numPr`，指向一个每级都是 `numFmt="none"` + `lvlText=""` 的
`abstractNum`。按 ECMA-376 §17.9.19，`none` 的语义就是「不显示编号」，Word
渲染这些标题时不带任何号。原实现在 `_DocxNumbering.marker` 里对空 `lvlText`
兜底成 `"%1."`，于是每个标题都被凭空加上 `1. ` 前缀——文档里不存在的字符进入
交付正文，且 `warnings` 为空（静默失效）。

口径：编号标记只能来自源文档的显式声明，缺失时留空，不得合成。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from doc2md.pipeline.s1_parse import read_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

pytestmark = [pytest.mark.gate]

# LibreOffice 形态：每级 numFmt=none / lvlText 为空串
_NONE_NUMBERING = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="none"/><w:lvlText w:val=""/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="none"/><w:lvlText w:val=""/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''

# 对照：真实有序编号，修复不得把它一起削掉
_DECIMAL_NUMBERING = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2."/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''


def _build(path: Path, numbering_xml: str, heads: list[tuple[str, str]],
           style_numpr: bool) -> Path:
    """造一个 DOCX：heads = [(styleId, text)]；style_numpr=True 时给 Heading1/2
    样式挂样式级 numPr（LibreOffice 形态）。"""
    from docx import Document

    d = Document()
    for sid, text in heads:
        p = d.add_paragraph(text)
        p.style = d.styles["Heading 1" if sid == "Heading1" else "Heading 2"]
    d.save(path)

    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}

    items["word/numbering.xml"] = numbering_xml.encode()
    rels = items["word/_rels/document.xml.rels"].decode()
    if "numbering.xml" not in rels:
        items["word/_rels/document.xml.rels"] = rels.replace(
            "</Relationships>",
            f'<Relationship Id="rIdNum1" Type="{R_NS}/numbering" Target="numbering.xml"/>'
            "</Relationships>").encode()
    ct = items["[Content_Types].xml"].decode()
    if "numbering.xml" not in ct:
        items["[Content_Types].xml"] = ct.replace(
            "</Types>",
            '<Override PartName="/word/numbering.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            "</Types>").encode()

    if style_numpr:
        styles = items["word/styles.xml"].decode()
        for sid, ilvl in (("Heading1", 0), ("Heading2", 1)):
            npr = (f'<w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/>'
                   '<w:numId w:val="1"/></w:numPr></w:pPr>')
            # 已有 pPr 则插入其中，否则补一个
            styles = styles.replace(f'<w:style w:type="paragraph" w:styleId="{sid}">',
                                    f'<w:style w:type="paragraph" w:styleId="{sid}">{npr}', 1)
        items["word/styles.xml"] = styles.encode()

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in items.items():
            z.writestr(n, v)
    tmp.replace(path)
    return path


def _markers(path: Path) -> list[tuple[str, str, int]]:
    return [(ln.text, ln.list_marker, ln.list_level)
            for pg in read_docx(path) for ln in pg.lines]


def test_numfmt_none_does_not_fabricate_marker(tmp_path):
    """样式级 numPr 指向 numFmt=none 的标题：不得产出任何编号标记。"""
    p = _build(tmp_path / "lo.docx", _NONE_NUMBERING,
               [("Heading1", "总则"), ("Heading2", "摘要"), ("Heading2", "职责")],
               style_numpr=True)
    rows = _markers(p)
    assert [t for t, _, _ in rows] == ["总则", "摘要", "职责"]
    assert [m for _, m, _ in rows] == ["", "", ""], rows
    assert [lv for _, _, lv in rows] == [-1, -1, -1], rows


def test_numfmt_none_marker_absent_from_rendered_text(tmp_path):
    """凭空造出的 `1. ` 不得出现在任何一行的可见文本里。"""
    p = _build(tmp_path / "lo.docx", _NONE_NUMBERING,
               [("Heading1", "总则"), ("Heading2", "摘要")], style_numpr=True)
    for text, marker, _ in _markers(p):
        assert not text.startswith("1. "), (text, marker)


def test_empty_lvltext_does_not_fabricate_marker(tmp_path):
    """lvlText 元素存在但为空 = 该级不显示编号，与 numFmt=none 同口径。"""
    xml = _NONE_NUMBERING.replace('<w:numFmt w:val="none"/>', '<w:numFmt w:val="decimal"/>')
    p = _build(tmp_path / "empty.docx", xml,
               [("Heading1", "总则"), ("Heading2", "摘要")], style_numpr=True)
    assert [m for _, m, _ in _markers(p)] == ["", ""], _markers(p)


def test_real_decimal_numbering_is_preserved(tmp_path):
    """反向守卫：真实 %1. / %1.%2. 编号不得被本次修复削掉。"""
    p = _build(tmp_path / "num.docx", _DECIMAL_NUMBERING,
               [("Heading1", "总则"), ("Heading2", "摘要"), ("Heading2", "职责")],
               style_numpr=True)
    rows = _markers(p)
    assert [m for _, m, _ in rows] == ["1.", "1.1.", "1.2."], rows
    assert [lv for _, _, lv in rows] == [0, 1, 1], rows
