"""PDF 结构修复回归：页眉页脚 / 页码 / 中文序号标题 / 几何分段 / 列表续行。

真实语料暴露的问题（2026-10-18）：
- 页眉页脚与页码进入正文（“第 1 页 共 4 页”“—2—”），且打断跨行句子；
- “一、问题描述”这类中文序号标题未识别，与下一行正文粘成一段；
- PDF 折行在“：/；”后分段，列表项续行被拆成独立段落；
- 无标点的附件清单跨页合并成数千字巨段。

修复口径：S1 保留行几何（top/bottom/page height），S3 在段落分组前剔除
页边距噪声，并用几何缩进 + 结构起始信号决定分段；本地模型只处理残余歧义。
"""
from __future__ import annotations

import json

import pytest

from doc2md.ir import IR, Block
from doc2md.llm import DEFAULT_OP_WHITELIST, LlmClient
from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.pipeline.s3_structure import analyze

pytestmark = [pytest.mark.gate, pytest.mark.milestone("DoD")]


def _page(*lines: Line, index: int = 0, height: float = 800.0) -> RawPage:
    return RawPage(index=index, lines=list(lines), height=height)


def _line(text: str, **kw) -> Line:
    return Line(text=text, **kw)


def _texts(ir: IR) -> list[str]:
    return [b.text for b in ir.blocks]


# ---------------------------------------------------------------- 页眉页脚 / 页码

def test_repeated_header_and_footer_removed():
    pages = [
        _page(
            _line("公司 版本：01", size=9.0, x0=200.0, top=30.0, bottom=40.0),
            _line("第一页正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
            _line("报告 第 1 页 共 2 页", size=9.0, x0=70.0, top=770.0, bottom=780.0),
            index=0,
        ),
        _page(
            _line("公司 版本：01", size=9.0, x0=200.0, top=30.0, bottom=40.0),
            _line("第二页正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
            _line("报告 第 2 页 共 2 页", size=9.0, x0=70.0, top=770.0, bottom=780.0),
            index=1,
        ),
    ]
    ir = analyze(pages)
    assert _texts(ir) == ["第一页正文。", "第二页正文。"]


def test_decorated_page_number_removed_on_single_page():
    ir = analyze([_page(
        _line("正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
        _line("— 1 —", size=9.0, x0=380.0, top=770.0, bottom=780.0),
    )])
    assert _texts(ir) == ["正文。"]


def test_margin_title_without_page_marker_is_kept():
    ir = analyze([_page(
        _line("第一章 年度报告", size=12.0, x0=70.0, top=30.0, bottom=44.0),
        _line("正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
    )], base_size=12.0)
    assert _texts(ir) == ["第一章 年度报告", "正文。"]
    assert [b.type for b in ir.blocks] == ["heading", "paragraph"]


# ---------------------------------------------------------------- 中文序号标题

def test_cjk_ordinal_heading_is_detected():
    ir = analyze([
        _page(
            _line("一、问题描述", size=16.0, x0=70.0),
            _line("2016 年共发生 18 起问题。", size=16.0, x0=94.9),
        )
    ], base_size=16.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert len(heads) == 1
    assert heads[0].text == "一、问题描述" and heads[0].level == 1
    assert _texts(ir) == ["一、问题描述", "2016 年共发生 18 起问题。"]


def test_cjk_ordinal_sentence_is_not_heading():
    text = "一、这是正文段落而不是标题，因为它超过三十个字符并且以句号结束。"
    ir = analyze([_page(_line(text, size=16.0, x0=70.0))], base_size=16.0)
    assert not [b for b in ir.blocks if b.type == "heading"]
    assert _texts(ir) == [text]


# ---------------------------------------------------------------- 几何分段

def test_first_line_indent_starts_new_paragraph():
    ir = analyze([_page(
        _line("上一段结尾没有句号", size=12.0, x0=70.9, top=100.0, bottom=114.0),
        _line("新段落首行", size=12.0, x0=94.9, top=130.0, bottom=144.0),
        _line("新段落续行。", size=12.0, x0=70.9, top=160.0, bottom=174.0),
    )], base_size=12.0)
    assert _texts(ir) == ["上一段结尾没有句号", "新段落首行新段落续行。"]


def test_unindented_wrapped_line_continues_after_terminal_punctuation():
    """有首行缩进证据时，折行末的“。”不自动切段（中文排版常见）。"""
    ir = analyze([_page(
        _line("第一句结束。", size=12.0, x0=70.9, top=100.0, bottom=114.0),
        _line("第二句仍在同一段。", size=12.0, x0=70.9, top=130.0, bottom=144.0),
        _line("另一段首行", size=12.0, x0=94.9, top=200.0, bottom=214.0),
        _line("另一段续行。", size=12.0, x0=70.9, top=230.0, bottom=244.0),
    )], base_size=12.0)
    assert _texts(ir) == ["第一句结束。第二句仍在同一段。", "另一段首行另一段续行。"]


def test_number_split_across_lines_is_rejoined_without_space():
    from doc2md.pipeline.s3_structure import _append_line
    assert _append_line("正文包含数字 2", "026 和标点。") == "正文包含数字 2026 和标点。"


def test_list_item_continuation_is_joined_back():
    ir = analyze([_page(
        _line("1. 项目说明用于验证列表项折行续文必须并回同一列表项",
              size=12.0, x0=70.9, top=100.0, bottom=114.0),
        _line("续行内容。", size=12.0, x0=70.9, top=130.0, bottom=144.0),
    )], base_size=12.0)
    assert [b.type for b in ir.blocks] == ["list_item"]
    assert ir.blocks[0].text.endswith("续行内容。")


def test_attachment_marker_starts_new_paragraph():
    ir = analyze([_page(
        _line("上一行没有句号", size=12.0, x0=70.9, top=100.0, bottom=114.0),
        _line("附件 1", size=12.0, x0=70.9, top=130.0, bottom=144.0),
        _line("项目内容。", size=12.0, x0=70.9, top=160.0, bottom=174.0),
    )], base_size=12.0)
    assert _texts(ir)[0] == "上一行没有句号"
    assert any("附件 1" in t for t in _texts(ir)[1:])


def test_wrapped_heading_continuation_joins_heading():
    ir = analyze([_page(
        _line("一、这是一个较长的章节标题用于验证标题折行", size=16.0, x0=70.9,
              top=100.0, bottom=114.0),
        _line("继续部分", size=16.0, x0=70.9, top=130.0, bottom=144.0),
        _line("正文首行。", size=16.0, x0=94.9, top=160.0, bottom=174.0),
    )], base_size=16.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert len(heads) == 1
    assert heads[0].text == "一、这是一个较长的章节标题用于验证标题折行继续部分"
    assert "继续部分" not in [b.text for b in ir.blocks if b.type == "paragraph"]


def test_heading_subtitle_with_colon_is_not_joined():
    ir = analyze([_page(
        _line("一、章节标题", size=16.0, x0=70.9, top=100.0, bottom=114.0),
        _line("副标题：说明文字", size=12.0, x0=70.9, top=130.0, bottom=144.0),
        _line("正文首行。", size=16.0, x0=94.9, top=160.0, bottom=174.0),
        _line("正文续行。", size=16.0, x0=70.9, top=190.0, bottom=204.0),
    )], base_size=16.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert [h.text for h in heads] == ["一、章节标题"]
    assert "副标题：说明文字" in _texts(ir)


def test_repeated_body_line_near_top_is_kept():
    repeated = "这是一行重复出现的正文内容，不应因为靠近页顶而被当成页眉删除。"
    pages = [
        _page(
            _line("小页眉", size=9.0, x0=200.0, top=30.0, bottom=40.0),
            _line(repeated, size=12.0, x0=70.0, top=54.0, bottom=68.0),
            _line("第一页正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
            index=0,
        ),
        _page(
            _line("小页眉", size=9.0, x0=200.0, top=30.0, bottom=40.0),
            _line(repeated, size=12.0, x0=70.0, top=54.0, bottom=68.0),
            _line("第二页正文。", size=12.0, x0=70.0, top=100.0, bottom=114.0),
            index=1,
        ),
    ]
    texts = _texts(analyze(pages))
    assert not any("小页眉" in t for t in texts)
    assert sum(1 for t in texts if repeated in t) == 2


def test_wrapped_title_headings_are_merged():
    ir = analyze([_page(
        _line("一、关于印发企业安全生产费用管理办法的通", size=18.0, x0=70.9),
        _line("知财资〔2022〕136 号", size=18.0, x0=70.9),
        _line("正文开始。", size=12.0, x0=70.9),
    )], base_size=12.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert len(heads) == 1
    assert heads[0].text == "一、关于印发企业安全生产费用管理办法的通知财资〔2022〕136 号"


def test_centered_caption_breaks_paragraph():
    ir = analyze([_page(
        _line("正文没有句号", size=12.0, x0=70.9, top=100.0, bottom=114.0),
        _line("图 2.1-1 示意图", size=10.0, x0=280.0, top=130.0, bottom=144.0),
        _line("下一段首行", size=12.0, x0=94.9, top=160.0, bottom=174.0),
        _line("下一段续行。", size=12.0, x0=70.9, top=190.0, bottom=204.0),
    )], base_size=12.0)
    assert "正文没有句号" in _texts(ir)
    assert "图 2.1-1 示意图" in _texts(ir)


def test_paren_subheading_is_heading():
    ir = analyze([_page(
        _line("（1）子标题示例", size=12.0, x0=94.9),
        _line("2016 年 12 月 24 日进行检查。", size=12.0, x0=94.9),
    )], base_size=12.0)
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert heads and heads[0].text == "（1）子标题示例"
    assert heads[0].level == 3


def test_alpha_list_item_is_structured():
    ir = analyze([_page(
        _line("a. 保留原方案", size=12.0, x0=94.9),
        _line("b. 取消原方案", size=12.0, x0=94.9),
    )], base_size=12.0)
    assert [b.type for b in ir.blocks] == ["list_item", "list_item"]


def test_chinese_article_marker_becomes_ordered_number():
    ir = analyze([_page(
        _line("根据相关规定制定本办法。", size=12.0, x0=94.9, list_marker="第3条"),
        _line("这里是第二条内容。", size=12.0, x0=94.9, list_marker="第 12 条"),
    )], base_size=12.0)
    texts = [b.text for b in ir.blocks]
    assert texts[0].startswith("3. ")
    assert texts[1].startswith("12. ")


def test_date_line_is_not_promoted_to_heading():
    ir = analyze([_page(
        _line("2025年  月  日", size=16.0, x0=70.9),
        _line("这是正文段落，用于建立正文基准。", size=10.5, x0=70.9),
    )], base_size=10.5)
    assert not [b for b in ir.blocks if b.type == "heading"]
    assert any("2025年" in b.text for b in ir.blocks)


def test_docx_explicit_paragraphs_do_not_merge():
    ir = analyze([_page(
        _line("日期行没有句末标点", size=12.0, explicit_para=True),
        _line("下一段是独立段落。", size=12.0, explicit_para=True),
    )], base_size=12.0)
    assert _texts(ir) == ["日期行没有句末标点", "下一段是独立段落。"]


# ---------------------------------------------------------------- 本地模型输入

def test_llm_payload_carries_candidate_text():
    blocks = [Block(id="b1", type="paragraph", text="前一段没有句号", page=0),
              Block(id="b2", type="paragraph", text="后一段也没有句号", page=0)]
    client = LlmClient(bind="127.0.0.1", port=1, retries=0)
    payloads = client.payload_batches(blocks, DEFAULT_OP_WHITELIST)
    assert payloads, "候选窗口为空，模型看不到任何正文"
    user = payloads[0]["messages"][1]["content"]
    assert "前一段没有句号" in user
    assert '"text"' in user
    assert '"allowed_ops"' in user


def test_llm_payload_batches_are_bounded():
    blocks = [Block(id=f"b{i}", type="paragraph",
                    text=("未结束的候选段落" * 30), page=i // 10)
              for i in range(200)]
    client = LlmClient(bind="127.0.0.1", port=1, retries=0)
    payloads = client.payload_batches(blocks, DEFAULT_OP_WHITELIST)
    assert len(payloads) > 1
    for p in payloads:
        assert len(json.dumps(p, ensure_ascii=False)) < client.max_payload_chars


@pytest.mark.parametrize("op,target_type", [
    ("fix_heading_level", "paragraph"),
    ("fix_list_marker", "paragraph"),
    ("merge_block", "heading"),
])
def test_ops_reject_wrong_block_type(op, target_type):
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type=target_type, text="目标", page=0),
              Block(id="b2", type="paragraph", text="正文", page=0)]
    op_body = {"op": op, "target": "b1"}
    if op == "fix_heading_level":
        op_body["level"] = 2
    accepted, rejected = OpValidator(blocks).apply([op_body])
    assert accepted == []
    assert rejected and any(r.startswith("bad_type") for r in rejected[0]["reasons"])


def test_llm_rejects_caption_merge():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph", text="正文。", page=0),
              Block(id="b2", type="paragraph", text="图 2.1-1 示意图", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "caption_target" in rejected[0]["reasons"]


def test_llm_rejects_cross_type_merge():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph", text="正文段落", page=0),
              Block(id="b2", type="list_item", text="a. 列表项", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert any(r.startswith("merge_type_mismatch") for r in rejected[0]["reasons"])


def test_llm_rejects_merge_into_attachment_heading():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph", text="附件 5（1 个）", page=0),
              Block(id="b2", type="paragraph", text="某单位名称", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "structural_merge" in rejected[0]["reasons"]


def test_llm_rejects_short_standalone_merge():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph", text="甲单位", page=0),
              Block(id="b2", type="paragraph", text="乙单位", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "merge_short_prev" in rejected[0]["reasons"]


def test_llm_rejects_merge_after_complete_sentence():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph",
                    text="上一段已经完整收句，且长度足以跳过短独立行判断。", page=0),
              Block(id="b2", type="paragraph",
                    text="新的条目名称", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "merge_after_sentence" in rejected[0]["reasons"]


def test_llm_rejects_merge_of_numbered_paragraph():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph",
                    text="4.上一年度营业收入超过十亿元的部分按照比例提取费用。", page=0),
              Block(id="b2", type="paragraph",
                    text="5.上一年度营业收入超过五十亿元的部分按照比例提取费用。", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "list_target" in rejected[0]["reasons"]


def test_llm_rejects_merge_into_caption():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph",
                    text="表 1 综合数据表", page=0),
              Block(id="b2", type="paragraph",
                    text="列一 列二", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "caption_merge" in rejected[0]["reasons"]


def test_llm_rejects_merge_across_explicit_docx_paragraphs():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="paragraph", text="标题行", page=0,
                    provenance={"explicit_para": True}),
              Block(id="b2", type="paragraph", text="接收单位名单", page=0,
                    provenance={"explicit_para": True})]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b2"}])
    assert accepted == []
    assert "explicit_para_merge" in rejected[0]["reasons"]


def test_llm_accepts_merge_when_earlier_neighbor_is_structural():
    """merge 的写集含直接前驱；不能拿“前驱的前驱”再次判跨类型。"""
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", type="list_item", text="- 列表项", page=0),
              Block(id="b2", type="paragraph",
                    text="前一行为未收句，需要与下一行合并成完整段落", page=0),
              Block(id="b3", type="paragraph", text="续行内容。", page=0)]
    accepted, rejected = OpValidator(blocks).apply(
        [{"op": "merge_block", "target": "b3"}])
    assert not rejected, rejected
    assert accepted and accepted[0]["target"] == "b3"
