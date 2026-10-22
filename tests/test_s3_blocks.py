"""tests/test_s3_blocks.py — CH-459 四类块检测 + 链接抽取 + LLM op 语义（批次①）。

覆盖对象：
- S3 七类块可达性（FR-104 红线缺口：BLOCK_TYPES 声明七类、原实现只产三类）；
- table / code / quote / page_break 的保守检测口径（宁漏勿误：漏判退化为段落）；
- S1 链接信号（PDF Link 标注几何附着 / DOCX w:hyperlink 整段覆盖才附着）；
- llm.apply_ops 五个 op 的语义（split_block / merge_block 原过校验不落地）。

方法论：块级行为直接构造 RawPage/Line 调 analyze（确定性、无第三方依赖）；
链接与 DOCX 结构走自建样本（docx 用 python-docx + 手工 XML 注入 w:hyperlink /
w:tbl / w:br，避免改动已分发的黄金样本资产）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.ir import BLOCK_TYPES, IR, Block
from doc2md.llm import apply_ops
from doc2md.pipeline.s1_parse import Line, RawPage, _attach_links, read_pdf
from doc2md.pipeline.s3_structure import analyze
from doc2md.pipeline.s4_render import render

pytestmark = [pytest.mark.milestone("DoD")]
GOLDEN = Path(__file__).parent / "golden"


# ---------------------------------------------------------------- 工具

def _page(*lines: Line, index: int = 0) -> RawPage:
    return RawPage(index=index, lines=list(lines))


def _line(text: str, **kw) -> Line:
    return Line(text=text, **kw)


def _types(ir: IR) -> list[str]:
    return [b.type for b in ir.blocks]


# ---------------------------------------------------------------- FR-104：七类块可达性

def test_all_seven_block_types_reachable():
    """FR-104 红线缺口回归：一次 analyze 同时产出全部七类块。

    原实现只产 heading/paragraph/list_item 三类——下游 W1043 跨片表格检测永假、
    S4 三个渲染分支死代码、mark_table_boundary op 无对象。
    """
    lines = [
        _line("第一章 绪论", size=16.0, bold=True),
        _line("- 列表项", size=10.5),
        _line("正文段落一。", size=10.5),
        _line("| 列A | 列B |", size=10.5, x0=10.0,
              row=["列A", "列B"]),
        _line("| 列C | 列D |", size=10.5, x0=10.0,
              row=["列C", "列D"]),
        _line("```", size=10.5),
        _line("code_line_1", size=10.5),
        _line("```", size=10.5),
        _line("> 引用一行", size=10.5),
        _line("\x0c", size=0.0),
        _line("分页后的正文。", size=10.5),
    ]
    ir = analyze([_page(*lines)])
    got = set(_types(ir))
    assert got == set(BLOCK_TYPES), f"七类块未全部产出，缺：{set(BLOCK_TYPES) - got}"


def test_table_rows_assemble_gfm():
    rows = [_line("名称 数值", x0=20.0, row=["名称", "数值"]),
            _line("甲 100", x0=20.0, row=["甲", "100"]),
            _line("乙 200", x0=20.0, row=["乙", "200"])]
    ir = analyze([_page(_line("上文。", x0=20.0), *rows)])
    tbl = next(b for b in ir.blocks if b.type == "table")
    assert tbl.text.splitlines()[0] == "| 名称 | 数值 |"
    assert tbl.text.splitlines()[1] == "| --- | --- |"
    assert tbl.text.splitlines()[2] == "| 甲 | 100 |"


def test_single_multi_column_row_is_not_a_table():
    """单行多列（列数齐也无连续性证据）→ 退回段落：漏判不误判。"""
    ir = analyze([_page(_line("名称 数值", x0=20.0, row=["名称", "数值"]))])
    assert "table" not in _types(ir)
    assert _types(ir) == ["paragraph"]


def test_pdf_row_tail_sentence_end_guard():
    """PDF 几何推断行以句末标点收尾 → 不是表格行（段落折行的空档易伪装多列）。

    DOCX 真实表格（row_explicit）不受该守卫约束——单元格以「。」结尾是常态。
    """
    pdf_rows = [_line("结论 备注。", x0=20.0, row=["结论", "备注。"]),
                _line("补充 说明。", x0=20.0, row=["补充", "说明。"])]
    ir = analyze([_page(*pdf_rows)])
    assert "table" not in _types(ir)

    docx_rows = [_line("结论 备注。", x0=0.0, row=["结论", "备注。"], row_explicit=True),
                 _line("补充 说明。", x0=0.0, row=["补充", "说明。"], row_explicit=True)]
    ir2 = analyze([_page(*docx_rows)])
    assert _types(ir2) == ["table"]


def test_row_x0_jump_splits_groups():
    """行首跳变（超过 3pt 容差）切组：两组各自判定，不并成一张错位表。"""
    lines = [_line("甲 100", x0=20.0, row=["甲", "100"]),
             _line("乙 200", x0=20.0, row=["乙", "200"]),
             _line("丙 300", x0=60.0, row=["丙", "300"])]
    ir = analyze([_page(*lines)])
    tbls = [b for b in ir.blocks if b.type == "table"]
    assert len(tbls) == 1                      # 前两组行
    assert "| 甲 | 100 |" in tbls[0].text
    assert "丙" not in tbls[0].text


def test_gfm_table_escapes_pipes_and_pads_columns():
    rows = [_line("a|b c", x0=0.0, row=["a|b", "c"]),
            _line("d e f", x0=0.0, row=["d", "e", "f"])]
    ir = analyze([_page(*rows)])
    tbl = next(b for b in ir.blocks if b.type == "table")
    head, sep = tbl.text.splitlines()[0], tbl.text.splitlines()[1]
    assert "a\\|b" in head                       # 单元格内 | 转义
    assert head.endswith("|  |")                 # 列数补齐到最多列（3）
    assert sep == "| --- | --- | --- |"


def test_fence_code_block():
    lines = [_line("```python"), _line("x = 1"), _line("```")]
    ir = analyze([_page(*lines)])
    code = next(b for b in ir.blocks if b.type == "code")
    assert code.text == "x = 1"


def test_indent_code_block_requires_empty_para():
    """4 空格缩进仅空段起算：段中缩进行是续文，不是代码（Markdown 语义）。"""
    lines = [_line("    indented"), _line("    more_code")]
    ir = analyze([_page(*lines)])
    code = next(b for b in ir.blocks if b.type == "code")
    assert code.text.splitlines() == ["indented", "more_code"]

    para_ctx = [_line("正文未结束，"), _line("    续行带缩进。")]
    ir2 = analyze([_page(*para_ctx)])
    assert "code" not in _types(ir2)


def test_mono_code_block_closes_on_non_mono():
    lines = [_line("def f():", mono=True), _line("    return 1", mono=True),
             _line("普通正文。", mono=False)]
    ir = analyze([_page(*lines)])
    code = next(b for b in ir.blocks if b.type == "code")
    assert code.text == "def f():\n    return 1"
    assert _types(ir)[-1] == "paragraph"


def test_quote_block_two_forms():
    """两种引用形态（> 前缀 / 整行 CJK 引号）连续行并为一个引用块。"""
    lines = [_line("> 前缀引用"), _line("「整行引号包裹」")]
    ir = analyze([_page(*lines)])
    quotes = [b for b in ir.blocks if b.type == "quote"]
    assert len(quotes) == 1
    assert quotes[0].text.splitlines() == ["前缀引用", "「整行引号包裹」"]


def test_quote_blocks_split_on_empty_line():
    lines = [_line("> 第一段引用"), _line(""), _line("> 第二段引用")]
    ir = analyze([_page(*lines)])
    quotes = [b for b in ir.blocks if b.type == "quote"]
    assert len(quotes) == 2


def test_quote_only_starts_at_para_boundary():
    """段中引用行按保守口径留在段内（起句才判引用块）。"""
    lines = [_line("正文第一行，"), _line("> 这不是引用块。")]
    ir = analyze([_page(*lines)])
    assert "quote" not in _types(ir)


def test_page_break_block_and_render_empty():
    """page_break 块进 IR（结构信息留痕）；GFM 无分页符语法 → S4 产空串。"""
    lines = [_line("第一页正文。"), _line("\x0c"), _line("第二页正文。")]
    ir = analyze([_page(*lines)])
    assert "page_break" in _types(ir)
    pb = next(b for b in ir.blocks if b.type == "page_break")
    assert pb.text == ""
    # GFM 无分页符语法：page_break 块渲染为空串，但不崩溃、不影响其他块
    md = render(ir)
    assert "第一页正文。" in md and "第二页正文。" in md
    assert "\x0c" not in md


# ---------------------------------------------------------------- 链接抽取

def test_pdf_link_annotation_attaches_by_geometry():
    """PDF Link 标注按纵向相交附着到行（FR-107）。"""
    pages = read_pdf(GOLDEN / "links_pdf.pdf")
    urls = [ln.link for p in pages for ln in p.lines if ln.link]
    assert any(u.startswith("https://") for u in urls), f"未抽到 URI 链接：{urls}"
    assert any(u.startswith("mailto:") for u in urls)


def test_pdf_link_only_first_line_per_annotation():
    pages = read_pdf(GOLDEN / "links_pdf.pdf")
    lines = [ln for p in pages for ln in p.lines]
    linked = [i for i, ln in enumerate(lines) if ln.link or ln.link_internal]
    assert linked, "links_pdf.pdf 应有链接行"
    # 保守口径：附着只发生在标注带相交的行——同一 URL 至多落在一根行上
    # （多根行同 URL = 标注带跨行误附着），且相邻行不连续被同一标注命中。
    urls = [lines[i].link for i in linked if lines[i].link]
    assert len(urls) == len(set(urls)), f"同一 URL 附着到多行：{urls}"


def test_attach_links_ignores_non_uri_goto_actions():
    """Launch / JavaScript 类动作不抽（执行类链接不是消费对象）。"""
    ln = _line("点击执行")
    geoms = [(100.0, 112.0)]
    page = type("P", (), {"annots": [{"top": 101.0, "bottom": 111.0,
                                      "data": {"A": {"S": "/Launch"}}}]})()
    _attach_links([ln], geoms, page)
    assert ln.link is None and not ln.link_internal


def test_attach_links_goto_internal():
    ln = _line("跳到第二章")
    page = type("P", (), {"annots": [{"top": 101.0, "bottom": 111.0,
                                      "data": {"A": {"S": "/GoTo",
                                                     "D": b"chap2"}}}]})()
    _attach_links([ln], [(100.0, 112.0)], page)
    assert ln.link is None
    assert ln.link_internal and ln.link_dest == "chap2"


def test_paragraph_gets_first_linked_line():
    """CH-485：完整覆盖的链接行独立成块，避免整段被包进一个链接。"""
    lines = [_line("请看参考链接，"), _line("详见附录。", link="https://a.example/x")]
    ir = analyze([_page(*lines)])
    linked = next(b for b in ir.blocks if b.links)
    assert linked.links[0]["url"] == "https://a.example/x"


def test_one_link_per_block():
    lines = [_line("第一行。", link="https://a.example/1"),
             _line("第二行。", link="https://a.example/2")]
    ir = analyze([_page(*lines)])
    para = next(b for b in ir.blocks if b.type == "paragraph")
    assert len(para.links) == 1
    assert para.links[0]["url"] == "https://a.example/1"


def test_internal_link_recorded_without_url():
    lines = [_line("见第二章。", link_internal=True, link_dest="chap2")]
    ir = analyze([_page(*lines)])
    para = next(b for b in ir.blocks if b.type == "paragraph")
    assert para.links[0]["url"] == "" and para.links[0]["dest"] == "chap2"


def test_docx_hyperlink_full_paragraph_only(tmp_path):
    """DOCX w:hyperlink：整段覆盖才附着；局部链接不抽（块级模型边界）。"""
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = docx.Document()
    p_full = d.add_paragraph("整段链接")
    p_part = d.add_paragraph()
    part = d.part
    rid = part.relate_to(
        "https://full.example/doc",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    # 整段超链接：把段落唯一 run 裹进 w:hyperlink
    h1 = OxmlElement("w:hyperlink")
    h1.set(qn("r:id"), rid)
    for r in p_full._p.findall(qn("w:r")):
        h1.append(r)
    p_full._p.append(h1)
    # 局部超链接：前缀普通文字 + 链接文字（链接不覆盖整段）
    rid2 = part.relate_to(
        "https://partial.example/doc",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    r_pre = OxmlElement("w:r")
    t_pre = OxmlElement("w:t")
    t_pre.text = "前缀"
    r_pre.append(t_pre)
    p_part._p.append(r_pre)
    h2 = OxmlElement("w:hyperlink")
    h2.set(qn("r:id"), rid2)
    r_link = OxmlElement("w:r")
    t_link = OxmlElement("w:t")
    t_link.text = "链接文字"
    r_link.append(t_link)
    h2.append(r_link)
    p_part._p.append(h2)
    p = tmp_path / "hl.docx"
    d.save(str(p))

    from doc2md.pipeline.s1_parse import read_docx
    pages = read_docx(p)
    lines = [ln for pg in pages for ln in pg.lines]
    full = next(ln for ln in lines if "整段链接" in ln.text)
    partial = next(ln for ln in lines if "前缀" in ln.text)
    assert full.link == "https://full.example/doc"
    assert partial.link is None, "行内局部链接本轮不抽（块级模型无法表达文字区间）"


def test_docx_table_rows_and_page_break_signal(tmp_path):
    """DOCX w:tbl → row_explicit 行；w:br type=page → \\x0c 信号行。"""
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = docx.Document()
    d.add_paragraph("表前段落")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "A1"
    t.cell(0, 1).text = "B1"
    t.cell(1, 0).text = "A2"
    t.cell(1, 1).text = "B2"
    p = d.add_paragraph("表后段落")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    p._element.append(br)
    path = tmp_path / "tbl.docx"
    d.save(str(path))

    from doc2md.pipeline.s1_parse import read_docx
    pages = read_docx(path)
    lines = [ln for pg in pages for ln in pg.lines]
    rows = [ln for ln in lines if ln.row]
    assert [r.row for r in rows] == [["A1", "B1"], ["A2", "B2"]]
    assert all(r.row_explicit for r in rows)
    assert any("\x0c" in ln.text for ln in lines)
    ir = analyze(pages)
    assert "table" in _types(ir)
    assert "page_break" in _types(ir)


def test_docx_heading_style_hint_wins_over_heuristics():
    """DOCX Heading 样式显式层级优先（无编号短行也不该漏判）。"""
    ln = _line("无编号标题行", size=10.5, level_hint=3)
    ir = analyze([_page(ln)])
    h = next(b for b in ir.blocks if b.type == "heading")
    assert h.level == 3


# ---------------------------------------------------------------- e2e（黄金样本）

def test_pdf_unnumbered_large_short_line_is_heading():
    """真实 Word 导出的报告标题常无编号；大字号短行应识别为标题。"""
    pages = [_page(_line("年度报告", size=20.0),
                   _line("这是正文第一段，用于建立正文基准。", size=10.0),
                   _line("这是正文第二段，用于建立正文基准。", size=10.0))]
    ir = analyze(pages, base_size=10.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert heads and heads[0].text == "年度报告" and heads[0].level == 1


def test_pdf_unnumbered_short_large_line_becomes_heading():
    """N-8：无编号短大字行升级为标题（阈值 base+3.5，与 numbered 分支对齐）。

    旧口径 +6pt 阈值下本用例的 15.5pt「页标题」保持段落；O-09 之后页标题
    形态在 furniture 层已剔除，pdf_04「代码说明」（+4.0）/ pdf_07「结语」
    （+3.5）被续段合并吞掉的缺陷暴露，阈值下调。误判防线改为：长行、
    句末标点（本文件其余用例）+ 页标题豁免集（test_pdf_page_title_repeat
    _not_heading）。
    """
    pages = [_page(_line("代码说明", size=14.0),
                   _line("这是正文第一段，用于建立正文基准。", size=10.0),
                   _line("这是正文第二段，用于建立正文基准。", size=10.0))]
    ir = analyze(pages, base_size=10.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert heads and heads[0].text == "代码说明" and heads[0].level == 2


def test_pdf_unnumbered_long_or_sentence_line_stays_paragraph():
    """N-8 误判防线：长行（>60 字）与句末标点行不因字号升级。"""
    long_text = "这是一段没有编号但很长的大字号文字，" * 6
    pages = [_page(_line(long_text, size=14.0),
                   _line("摘要完毕。", size=14.0),
                   _line("这是正文第一段，用于建立正文基准。", size=10.0))]
    ir = analyze(pages, base_size=10.0)
    assert not [b for b in ir.blocks if b.type == "heading"]


def test_pdf_page_title_repeat_not_heading():
    """N-8 豁免集：数字折叠后跨页重复的无编号行是页标题，字号再大不抬。

    badtail 形态——“正常页面 1/2/…”逐页变体，furniture 键不可折漏到 S3；
    几何页（height>0）边距带内聚出跨页重复后豁免。单页大字行不受影响
    （见 test_pdf_unnumbered_short_large_line_becomes_heading）。
    """
    pages = [RawPage(index=i, height=842.0, lines=[
        _line(f"正常页面 {i}", size=14.0, top=40.0, bottom=58.0),
        _line(f"这是第 {i} 页的正文内容，字号回落后保持段落形态。", size=10.0,
              top=90.0, bottom=104.0),
    ]) for i in range(1, 4)]
    ir = analyze(pages, base_size=10.0)
    assert not [b for b in ir.blocks if b.type == "heading"]
    assert sum("正常页面" in b.text for b in ir.blocks if b.type == "paragraph") == 3


def test_links_pdf_markdown_carries_both_links(golden_dir):
    from doc2md.pipeline.convert import convert
    r = convert(golden_dir / "links_pdf.pdf")
    # 块级链接模型：整段作锚文本，URL 正确可点（行内局部链接本轮不抽）
    assert "](https://example.com)" in r.markdown
    assert "](mailto:test@example.com)" in r.markdown


def test_docx_sample_headings_detected(golden_dir):
    from doc2md.pipeline.convert import convert
    r = convert(golden_dir / "docx" / "sample_simple.docx")
    assert "# 一级标题" in r.markdown
    assert "## 二级标题" in r.markdown
    assert "### 三级标题" in r.markdown


# ---------------------------------------------------------------- LLM op 语义（A-7）

def _ir(*blocks: Block) -> IR:
    return IR(doc_id="d", source_key="s", blocks=list(blocks))


def test_fix_heading_level_lands():
    ir = _ir(Block(id="b1", type="heading", text="标题", level=2))
    n = apply_ops(ir, [{"op": "fix_heading_level", "target": "b1", "level": 4}])
    assert n == 1 and ir.blocks[0].level == 4
    assert "llm:fix_heading_level" in ir.blocks[0].provenance["modified_by"]


def test_fix_heading_level_same_level_is_noop():
    """目标层级与现状相同：不计数、不写 provenance（applied_ops 只记真实变更）。"""
    ir = _ir(Block(id="b1", type="heading", text="标题", level=2))
    n = apply_ops(ir, [{"op": "fix_heading_level", "target": "b1", "level": 2}])
    assert n == 0 and ir.blocks[0].level == 2
    assert "modified_by" not in ir.blocks[0].provenance


def test_fix_list_marker_normalizes():
    ir = _ir(Block(id="b1", type="list_item", text="• 事项"))
    n = apply_ops(ir, [{"op": "fix_list_marker", "target": "b1", "marker": "-"}])
    assert n == 1 and ir.blocks[0].text == "- 事项"


def test_mark_table_boundary_flags_only():
    ir = _ir(Block(id="b1", type="paragraph", text="正文"))
    n = apply_ops(ir, [{"op": "mark_table_boundary", "target": "b1"}])
    assert n == 1 and ir.blocks[0].flags["table_boundary"] is True
    assert ir.blocks[0].text == "正文"          # 只打旗标，不改正文


def test_split_block_inserts_suffix_id_without_renumbering():
    ir = _ir(Block(id="b1", type="paragraph", text="前半段后半段"),
             Block(id="b2", type="paragraph", text="另一块"))
    n = apply_ops(ir, [{"op": "split_block", "target": "b1", "at": 3}])
    assert n == 1
    assert [b.id for b in ir.blocks] == ["b1", "b1s1", "b2"]   # 不重排后续 id
    assert ir.blocks[0].text == "前半段"
    assert ir.blocks[1].text == "后半段"


def test_split_block_rejects_empty_side():
    ir = _ir(Block(id="b1", type="paragraph", text="短块"))
    n = apply_ops(ir, [{"op": "split_block", "target": "b1", "at": 0}])
    assert n == 0 and len(ir.blocks) == 1


def test_merge_block_joins_previous_with_cjk_rule():
    ir = _ir(Block(id="b1", type="paragraph", text="前文"),
             Block(id="b2", type="paragraph", text="后句"))
    n = apply_ops(ir, [{"op": "merge_block", "target": "b2"}])
    assert n == 1
    assert [b.id for b in ir.blocks] == ["b1"]
    assert ir.blocks[0].text == "前文后句"        # CJK 直连
    assert ir.blocks[0].flags["absorbed_ids"] == ["b2"]


def test_merge_block_latin_gets_space():
    ir = _ir(Block(id="b1", type="paragraph", text="hello"),
             Block(id="b2", type="paragraph", text="world"))
    apply_ops(ir, [{"op": "merge_block", "target": "b2"}])
    assert ir.blocks[0].text == "hello world"


def test_merge_block_rejects_structural_previous():
    ir = _ir(Block(id="b1", type="heading", text="标题", level=1),
             Block(id="b2", type="paragraph", text="正文"))
    n = apply_ops(ir, [{"op": "merge_block", "target": "b2"}])
    assert n == 0 and len(ir.blocks) == 2


def test_unknown_target_is_noop():
    ir = _ir(Block(id="b1", type="paragraph", text="正文"))
    n = apply_ops(ir, [{"op": "fix_heading_level", "target": "bX", "level": 2}])
    assert n == 0


# ------------------------------------------------- CH-469：跨行拼接 / 跨页段落 / 渲染保真

def test_hyphen_join_does_not_duplicate_text():
    """CH-469 回归：`_join_line` 返回「待追加片段」，连字符断词由 _append_line
    剥尾连字符。原实现返回整串 `a[:-1]+b` 而两个调用方都是追加语义 →
    "under-" + "score" 产出 "under-underscore"。"""
    from doc2md.pipeline.s3_structure import _append_line
    assert _append_line("under-", "score") == "underscore"
    assert _append_line("hello", "world") == "hello world"
    assert _append_line("本合同", "适用于") == "本合同适用于"


def test_s3_hyphenated_word_across_lines():
    ir = analyze([_page(_line("under-", size=10.5), _line("score covers all", size=10.5))])
    assert [b.text for b in ir.blocks] == ["underscore covers all"]


def test_s3_paragraph_survives_page_break():
    """CH-469：段缓冲原在页循环内逐页重置——英文跨页句第二段以小写起头，
    既把一段切成两块，又必然命中 G1 的 half_sentence_heads 红线。"""
    from doc2md.pipeline.metrics import half_sentence_heads
    ir = analyze([
        _page(_line("This report covers", size=10.5, ), index=0),
        _page(_line("all contracts signed in 2024.", size=10.5), index=1),
    ])
    assert [b.type for b in ir.blocks] == ["paragraph"]
    assert ir.blocks[0].text == "This report covers all contracts signed in 2024."
    assert half_sentence_heads(ir) == []


def test_render_keeps_blank_lines_inside_code_block():
    """CH-469：全局 `re.sub(r"\\n{3,}", "\\n\\n")` 会把 S3 emit_code 特意保留的
    代码块内空行压扁（代码内容失真）。压缩只允许作用于围栏之外。"""
    ir = _ir(Block(id="b1", type="paragraph", text="前文"),
             Block(id="b2", type="code", text="a\n\n\n\nb"),
             Block(id="b3", type="paragraph", text="后文\n\n\n多余空行"))
    md = render(ir)
    assert "a\n\n\n\nb" in md, "代码块内空行被压缩"
    assert "后文\n\n多余空行" in md, "围栏外的空行压缩仍需生效"


def test_render_tight_list_between_items():
    """O-04（C-05）：连续 list_item 之间单换行（tight list）；块界
    （段落→列表、列表→段落、列表→标题）仍保留空行——Markdown 需要空行
    打断段落。块内多行 list_item 不受影响。"""
    ir = _ir(Block(id="b1", type="heading", text="列表", level=2),
             Block(id="b2", type="paragraph", text="引导段"),
             Block(id="b3", type="list_item", text="- 甲"),
             Block(id="b4", type="list_item", text="- 乙"),
             Block(id="b5", type="list_item", text="1. 丙"),
             Block(id="b6", type="paragraph", text="收尾段"))
    md = render(ir)
    assert "引导段\n\n- 甲\n- 乙\n1. 丙\n\n收尾段" in md


def test_render_page_break_between_list_items_stays_tight():
    """O-04：page_break 渲染为空串、不参与间距判定——被它隔开的两个列表项
    仍按连续列表项 tight 相接（GFM 无分页语法，结构信息留在 IR/审阅面）。"""
    ir = _ir(Block(id="b1", type="list_item", text="- 甲"),
             Block(id="b2", type="page_break", text=""),
             Block(id="b3", type="list_item", text="- 乙"))
    assert render(ir) == "- 甲\n- 乙\n"


def test_apply_ops_preserves_links_on_split_and_merge():
    """CH-469：split_block / merge_block 原不搬运 links → 尾块与被吸收块的
    链接静默丢失。"""
    ir = _ir(Block(id="b1", type="paragraph", text="前文与前文",
                   links=[{"url": "https://a.example", "text": "前文"},
                          {"url": "https://b.example", "text": "后文"}]))
    assert apply_ops(ir, [{"op": "split_block", "target": "b1", "at": 4}]) == 1
    assert [b.links for b in ir.blocks] == [
        [{"url": "https://a.example", "text": "前文"}],
        [{"url": "https://b.example", "text": "后文"}]]

    ir2 = _ir(Block(id="b1", type="paragraph", text="前文"),
              Block(id="b2", type="paragraph", text="后文",
                    links=[{"url": "https://c.example", "text": "后文"}]))
    assert apply_ops(ir2, [{"op": "merge_block", "target": "b2"}]) == 1
    assert ir2.blocks[0].links == [{"url": "https://c.example", "text": "后文"}]
