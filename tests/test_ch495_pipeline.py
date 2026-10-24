"""CH-495（第 14 轮审查）管线回归红线：嵌套文本框去重 + 编号还原口径。

1. R14-PIPE-1：嵌套文本框的段落在旧实现下最多出现 4 次——`txbxContent`
   扁平 `iter(w:p)` 把嵌套 AlternateContent 的 Choice/Fallback 两支都产出
   （×2），宿主段 `include_textbox=True` 拼 run 时再收两支（×2）。修复后
   每层段落恰好产出一次、宿主段只含自己这一层的文本。
2. R14-PIPE-2：`_fmt_num` 罗马数值表只到 10（40→"xxxx"）、字母序 %26 回绕
   （27→"a" 与首项撞号），修复后与 Word 口径一致（xl / aa）。
"""
from __future__ import annotations

import pytest
from docx.oxml import parse_xml

from doc2md.pipeline.s1_parse import Line, read_docx

_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
       'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')


def _lines(path) -> list[Line]:
    return [ln for pg in read_docx(path) for ln in pg.lines]


def _textbox(inner_xml: str) -> str:
    return ('<w:drawing><wp:inline><a:graphic><a:graphicData '
            'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            f'<wps:wsp><wps:txbx><w:txbxContent>{inner_xml}'
            '</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing>')


def _nested_ac() -> str:
    """嵌套文本框：mc:AlternateContent 双分支（Choice=NESTED / Fallback=NESTED-FB）。"""
    branch = ('<w:drawing><wp:inline><a:graphic><a:graphicData '
              'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
              '<wps:wsp><wps:txbx><w:txbxContent>'
              '<w:p><w:r><w:t>NESTED</w:t></w:r></w:p>'
              '</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic>'
              '</wp:inline></w:drawing>')
    fallback = branch.replace("NESTED", "NESTED-FB")
    return (f'<mc:AlternateContent><mc:Choice Requires="wps">{branch}</mc:Choice>'
            f'<mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>')


@pytest.mark.gate
def test_nested_textbox_paragraph_appears_once(tmp_path):
    import docx
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("前")
    # 外层文本框段落包在 sdt 里：强制文档级 has_sdt → 宿主段走 _docx_all_runs
    # 接受后视图（旧实现 include_textbox=True 会把嵌套两支的 run 再拼一遍）。
    outer_p = ('<w:sdt><w:sdtContent><w:p>'
               '<w:r><w:t>OUTER-TB</w:t></w:r>'
               f'<w:r>{_nested_ac()}</w:r>'
               '</w:p></w:sdtContent></w:sdt>')
    p._p.append(parse_xml(f'<w:r {_NS}>{_textbox(outer_p)}</w:r>'))
    p.add_run("后")
    path = tmp_path / "nested_tb.docx"
    d.save(str(path))

    texts = [ln.text for ln in _lines(path)]
    assert texts.count("NESTED") == 1, texts
    assert "NESTED-FB" not in texts, texts
    assert "OUTER-TB" in texts, texts
    assert "前后" in texts, texts
    assert not any("NESTED" in t and t != "NESTED" for t in texts), texts


@pytest.mark.gate
def test_single_textbox_unchanged(tmp_path):
    """单层文本框（无嵌套）产物与修复前逐字节一致——G6 口径的守门样本。"""
    import docx
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("宿主段落")
    inner = '<w:p><w:r><w:t>框内文字</w:t></w:r></w:p>'
    p._p.append(parse_xml(f'<w:r {_NS}>{_textbox(inner)}</w:r>'))
    path = tmp_path / "tb1.docx"
    d.save(str(path))
    texts = [ln.text for ln in _lines(path)]
    assert texts.count("框内文字") == 1 and "宿主段落" in texts, texts


@pytest.mark.gate
@pytest.mark.parametrize(("n", "fmt", "want"), [
    (40, "lowerRoman", "xl"), (50, "upperRoman", "L"), (9, "lowerRoman", "ix"),
    (1994, "lowerRoman", "mcmxciv"), (4, "upperRoman", "IV"),
    (26, "lowerLetter", "z"), (27, "lowerLetter", "aa"),
    (28, "upperLetter", "AB"), (52, "lowerLetter", "az"),
    (53, "lowerLetter", "ba"), (1, "lowerLetter", "a"),
])
def test_fmt_num_word_semantics(n, fmt, want):
    from doc2md.pipeline.s1_parse import _DocxNumbering
    assert _DocxNumbering._fmt_num(n, fmt) == want
