"""CH-506 / OPT4-12：`w:num/w:lvlOverride` 必须覆盖抽象编号定义。

ECMA-376 给了编号定义两级形态：抽象定义（`w:abstractNum/w:lvl`）与**实例覆盖**
（`w:num/w:lvlOverride`，含 `w:lvl` 与 `w:startOverride`）。Word 在「重新开始编号」
与「本节单独换格式」时写的正是后者。`_DocxNumbering` 原实现只读抽象层，于是：

- 覆盖里 `numFmt="none"` / 空 `lvlText`（= 该级不显示编号）被无视，凭空产出 `1. `
  ——与 CH-504 修掉的 D-1 **同一形态**，只是换成 `lvlOverride` 这个构造；
- `startOverride="5"` 被无视，标记从 1 起而不是 5 起（编号序列错，且无声）。

口径同 CH-504：编号标记只能来自源文档的**有效**声明——`lvlOverride` 存在时，
它才是有效声明。本文件钉住三态（none / 格式替换 / 起始值覆盖）与一条反向守卫。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from doc2md.pipeline.s1_parse import read_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

pytestmark = [pytest.mark.gate]

_ABSTRACT = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}">
 <w:abstractNum w:abstractNumId="1">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
 </w:abstractNum>
 <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''


def _with_override(override: str) -> str:
    """在 numId=1 上挂一个 ilvl=0 的 lvlOverride。"""
    return _ABSTRACT.replace(
        '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        '<w:num w:numId="1"><w:abstractNumId w:val="1"/>'
        f'<w:lvlOverride w:ilvl="0">{override}</w:lvlOverride></w:num>')


_NONE_OVERRIDE = _with_override(
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="none"/>'
    '<w:lvlText w:val=""/></w:lvl>')
_FORMAT_OVERRIDE = _with_override(
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
    '<w:lvlText w:val="%1)"/></w:lvl>')
_START_OVERRIDE = _with_override('<w:startOverride w:val="5"/>')


def _build(path: Path, numbering_xml: str, texts: list[str]) -> Path:
    """造一个 DOCX：每个段落挂**段落级** numPr → numId=1 / ilvl=0。"""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    for text in texts:
        p = d.add_paragraph(text)
        pPr = p._p.get_or_add_pPr()
        numPr = OxmlElement("w:numPr")
        ilvl = OxmlElement("w:ilvl")
        ilvl.set(qn("w:val"), "0")
        numId = OxmlElement("w:numId")
        numId.set(qn("w:val"), "1")
        numPr.append(ilvl)
        numPr.append(numId)
        pPr.append(numPr)
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

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in items.items():
            z.writestr(n, v)
    tmp.replace(path)
    return path


def _markers(path: Path) -> list[str]:
    return [ln.list_marker for pg in read_docx(path) for ln in pg.lines]


def test_override_none_does_not_fabricate_marker(tmp_path):
    """覆盖层 numFmt=none：不得凭抽象层的 %1. 造出标记（D-1 同型）。"""
    p = _build(tmp_path / "none.docx", _NONE_OVERRIDE, ["总则", "职责"])
    assert _markers(p) == ["", ""]


def test_override_level_replaces_abstract_format(tmp_path):
    """覆盖层 lvlText=%1)：有效标记按覆盖层渲染，而非抽象层的 %1.。"""
    p = _build(tmp_path / "fmt.docx", _FORMAT_OVERRIDE, ["总则", "职责"])
    assert _markers(p) == ["1)", "2)"]


def test_start_override_changes_first_number(tmp_path):
    """startOverride=5：首个标记是 5.，下一个是 6.。"""
    p = _build(tmp_path / "start.docx", _START_OVERRIDE, ["总则", "职责"])
    assert _markers(p) == ["5.", "6."]


def test_abstract_numbering_without_override_is_unchanged(tmp_path):
    """反向守卫：无 lvlOverride 的普通有序列表，序列不得被本次修复改动。"""
    p = _build(tmp_path / "plain.docx", _ABSTRACT, ["总则", "职责"])
    assert _markers(p) == ["1.", "2."]
