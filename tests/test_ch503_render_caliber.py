r"""CH-503（第十八轮）R18-PIPE-1：渲染红线判据的转义/位置口径红线。

缺陷形态（本机实测）：`_contract_violation` 用裸子串 `"](#" in md` / `"#page-" in md`
判「自造锚点」，而 `_escape_text` 只把 `]` 转义成 `\]`、`#` 与 `-` 原样保留——于是源文本
里惰性的 `\](#anchor)`（讲 Markdown 语法的文档）与 `#page-3`（CSS 选择器 / 说明文字）
都被判成违规，整篇文档以 E5031 拒收。一篇「教读者不要写 ](#x) 死链」的文档无法转换，
是该红线最讽刺的失败面。

本文件守两件事，缺一件都会让修复退化：
① 惰性形态必须放行（否则继续误拒真文档）；
② 活动形态必须仍红（否则「判据太严」被改成「判据不存在」——红线失去牙齿）。
"""
from __future__ import annotations

import pytest

from doc2md.ir import IR, Block
from doc2md.pipeline.s4_render import _contract_violation, assert_render_contract, render

#: 活动形态：渲染器漏出生成语法时产物里**一定**长这样（前面没有反斜杠）。
ACTIVE_FORMS = ("[x](#page-3)", "<script>alert(1)</script>", "<img src=x>",
                "<a href=\"#sec-1\">锚</a>", "[y](javascript:alert(1))", "~~删除~~")

#: 惰性形态：同一段文字经 `_escape_text` 之后的实际产物（`#`、`-`、`(`、`)` 不在转义表里）。
INERT_FORMS = (r"\](#page-3)",
               r"\<script>alert(1)\</script>",
               r"\<img src=x\>",
               r'\<a href="#sec-1">\锚\</a\>',
               r"\](javascript:alert(1))",
               r"\~\~删除\~\~")


@pytest.mark.gate
def test_active_forms_still_rejected():
    """活动形态逐个必须仍被判违规——红线收窄判据后不许出现漏网形态。"""
    for form in ACTIVE_FORMS:
        bad = _contract_violation(form)
        assert bad, f"活动形态被判清白（红线失去牙齿）：{form!r}"
        assert bad in form, f"返回的标记不是原文里的形态：{bad!r} ∉ {form!r}"
    with pytest.raises(AssertionError):
        assert_render_contract("[x](#page-3)\n")
    with pytest.raises(AssertionError):
        assert_render_contract("a ~~b~~ c\n")


@pytest.mark.gate
def test_inert_forms_are_not_violations():
    """转义后的惰性形态一律放行：这是「讲 Markdown 的文档被拒收」的回归钉。"""
    for form in INERT_FORMS:
        assert _contract_violation(form) == "", f"惰性形态被误判：{form!r}"
        assert_render_contract(form + "\n")


@pytest.mark.gate
def test_anchor_only_counts_in_link_position():
    """`#page-` 只在链接语法位置算自造锚点，孤立出现（CSS/说明文字）不算。"""
    assert _contract_violation('选择器 #page-3 与 href="#page-3" 不会生成锚点') == ""
    assert _contract_violation("[x](#page-3)") == "](#"        # 链接位置仍红
    assert _contract_violation("<a href=\"#page-3\">") == "<a "   # 裸 HTML 锚标签仍红


@pytest.mark.gate
def test_paragraph_with_anchor_literal_converts():
    """端到端可达性：段落里含 `](#…)` 的 IR 能出产物，不再抛 E5031。"""
    ir = IR(doc_id="p1", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph",
                          text="写 Markdown 时，形如 ](#anchor) 的自造锚点是死链"),
                    Block(id="b2", type="paragraph", text="CSS 里 #page-3 同理")])
    md = render(ir)
    assert "\\](#anchor)" in md and "#page-3" in md, md
    assert _contract_violation(md) == "", md


@pytest.mark.gate
def test_real_docx_about_markdown_syntax_is_accepted(tmp_path):
    """真机腿：一篇讲 Markdown 语法的 DOCX 必须转换成功（修复前实测 E5031 拒收）。"""
    docx = pytest.importorskip("docx")
    from doc2md.pipeline.convert import convert

    d = docx.Document()
    d.add_heading("锚点示例文档", level=1)
    d.add_paragraph("写 Markdown 时，形如 ](#anchor) 的自造锚点在 GFM 里是死链。")
    d.add_paragraph("CSS 里 #page-3 选择器也一样不会生成锚点。")
    src = tmp_path / "anchor_literal.docx"
    d.save(src)
    res = convert(src, out_dir=tmp_path / "out", force=True)
    assert "\\](#anchor)" in res.markdown, res.markdown
    assert "#page-3" in res.markdown, res.markdown


@pytest.mark.gate
def test_code_block_literals_still_exempted_per_block():
    """逐块豁免口径不变：代码块里的 `~~`/`](#…`/`<script>` 原样保留，不牵连正文。"""
    src = "~~not-strike~~\n<script>x</script>\n[t](#anchor)"
    ir = IR(doc_id="p3", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="code", text=src, flags={"language": "text"}),
                    Block(id="b2", type="paragraph", text="正常正文")])
    md = render(ir)
    assert src in md, md
    # 整篇口径仍会命中代码字面量（它本就不用于生产），逐块口径才是生产的形状。
    assert _contract_violation(md), "整篇口径必命中代码字面量——故生产用逐块口径"
