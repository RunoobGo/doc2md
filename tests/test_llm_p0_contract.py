"""P0 LLM 契约加固（2026-10-21）。

覆盖结论里的四条修复：
- 必填参数缺失 → 整条拒绝（missing_param:*），不再 accepted-but-noop；
- split_block 必须有安全 at，禁止中点兜底/词内/URL 内切分；
- mark_table_boundary 只允许 table 本身或紧邻 table 的块；
- apply_ops 返回逐目标报告，accepted / effective / noop 分账。
"""
from __future__ import annotations

import json

import pytest

from doc2md.ir import IR, Block
from doc2md.llm import (
    DEFAULT_OP_WHITELIST,
    LlmClient,
    OpEffect,
    OpValidator,
    _allowed_ops_for,
    apply_ops,
    split_reason,
)
from doc2md.pipeline.convert import _llm_stats

pytestmark = [pytest.mark.gate, pytest.mark.milestone("DoD")]


def _ir(*blocks: Block) -> IR:
    return IR(doc_id="p0", source_key="p0", source_name="p0.docx", blocks=list(blocks))


@pytest.mark.parametrize("op,block", [
    ({"op": "fix_heading_level", "target": "b1"}, Block(
        id="b1", type="heading", text="1.1 标题", level=4)),
    ({"op": "fix_list_marker", "target": "b1"}, Block(
        id="b1", type="list_item", text="* 项")),
    ({"op": "split_block", "target": "b1"}, Block(
        id="b1", type="paragraph", text="前半段后半段")),
])
def test_missing_required_param_is_rejected(op, block):
    accepted, rejected = OpValidator([block]).apply([op])
    assert accepted == []
    assert len(rejected) == 1
    assert ("missing_param:" + {"fix_heading_level": "level",
                                "fix_list_marker": "marker",
                                "split_block": "at"}[op["op"]]
            in rejected[0]["reasons"])


def test_valid_required_params_still_accept():
    blocks = [Block(id="h1", type="heading", text="1.1 标题", level=4),
              Block(id="li1", type="list_item", text="* 项"),
              Block(id="p1", type="paragraph", text="前半部分内容 后半部分内容")]
    ops = [{"op": "fix_heading_level", "target": "h1", "level": 3},
           {"op": "fix_list_marker", "target": "li1", "marker": "-"},
           {"op": "split_block", "target": "p1", "at": 6}]
    accepted, rejected = OpValidator(blocks).apply(ops)
    assert rejected == []
    assert len(accepted) == 3


def test_split_rejects_inside_english_word_and_number():
    assert split_reason("hello world", 3) == "split_inside_token"
    assert split_reason("abc123def", 4) == "split_inside_token"


def test_split_rejects_inside_url():
    text = "见 https://example.com/a/b 后文"
    at = text.index("example") + 2
    assert split_reason(text, at) == "split_inside_url"


def test_split_allows_whitespace_and_cjk_boundary():
    assert split_reason("前半段 后半段", 3) is None       # 空格前
    assert split_reason("前半段后半段", 3) is None       # CJK 字符间
    assert split_reason("短块", 9) == "bad_at"


def test_split_requires_natural_boundary_for_model_ops():
    assert split_reason("第 1 页第 1 栏", 1, require_boundary=True) == "split_too_short"
    assert split_reason("第一句话。第二句话。", 5, require_boundary=True) is None
    assert split_reason("hello world", 5, require_boundary=True) is None


def test_split_without_at_does_not_midpoint_split():
    before = "前半段后半段"
    ir = _ir(Block(id="b1", type="paragraph", text=before))
    assert apply_ops(ir, [{"op": "split_block", "target": "b1"}]) == 0
    assert [b.text for b in ir.blocks] == [before]


def test_table_boundary_requires_table_or_adjacent_block():
    blocks = [Block(id="p0", type="paragraph", text="远段"),
              Block(id="cap", type="paragraph", text="表 1 说明"),
              Block(id="t1", type="table", text="| a | b |"),
              Block(id="after", type="paragraph", text="表后正文")]
    v = OpValidator(blocks)
    accepted, rejected = v.apply([
        {"op": "mark_table_boundary", "target": "t1"},
        {"op": "mark_table_boundary", "target": "cap"},
        {"op": "mark_table_boundary", "target": "after"},
        {"op": "mark_table_boundary", "target": "p0"},
    ])
    assert [o["target"] for o in accepted] == ["t1", "cap", "after"]
    assert rejected[0]["op"]["target"] == "p0"
    assert rejected[0]["reasons"] == ["not_table_boundary"]


def test_allowed_ops_hides_table_boundary_for_far_paragraph():
    blocks = [Block(id="p0", type="paragraph", text="远段"),
              Block(id="p1", type="paragraph", text="表前说明"),
              Block(id="t1", type="table", text="| a | b |")]
    wanted = set(DEFAULT_OP_WHITELIST)
    far_ops = _allowed_ops_for(blocks, 0, wanted)
    near_ops = _allowed_ops_for(blocks, 1, wanted)
    far_hits, near_hits = (far_ops.count("mark_table_boundary"),
                           near_ops.count("mark_table_boundary"))
    assert (far_hits, near_hits) == (0, 1), (
        f"表边界 op 收窄判据无感：远段命中 {far_hits} 处、紧邻表的段落命中 {near_hits} 处"
        f"（应为 0 / 1；紧邻表为 0 说明 `_allowed_ops_for` 压根不发这个 op，前一条否定恒真）")


def test_shallow_headings_are_not_lifted_further():
    blocks = [Block(id="h2", type="heading", text="域代码", level=2),
              Block(id="h4", type="heading", text="1.2.3.4 长标题", level=4)]
    accepted, rejected = OpValidator(blocks).apply([
        {"op": "fix_heading_level", "target": "h2", "level": 1},
        {"op": "fix_heading_level", "target": "h4", "level": 3},
    ])
    assert [o["target"] for o in accepted] == ["h4"]
    assert rejected[0]["reasons"] == ["heading_too_shallow"]

    ir = _ir(Block(id="h2", type="heading", text="域代码", level=2))
    effects = apply_ops(ir, [{"op": "fix_heading_level", "target": "h2",
                              "level": 1}], return_report=True)
    assert effects[0].changed is False
    assert effects[0].reason == "already_shallow"
    assert ir.blocks[0].level == 2


def test_apply_ops_report_marks_noops_and_repeats():
    ir = _ir(Block(id="h1", type="heading", text="标题", level=4),
             Block(id="t1", type="table", text="| a | b |"))
    effects = apply_ops(ir, [
        {"op": "fix_heading_level", "target": "h1", "level": 4},   # same_level
        {"op": "mark_table_boundary", "target": "t1"},             # changed
        {"op": "mark_table_boundary", "target": "t1"},             # already_set
    ], return_report=True)
    assert [(e.op, e.changed, e.reason) for e in effects] == [
        ("fix_heading_level", False, "same_level"),
        ("mark_table_boundary", True, ""),
        ("mark_table_boundary", False, "already_set"),
    ]


def test_llm_stats_separates_accepted_effective_noop():
    stats = _llm_stats(
        {"source": "server", "response_ops": 3, "accepted_ops": ["a", "b", "c"],
         "rejected_model_count": 1, "latency_ms": 12.5},
        [OpEffect("a", "b1", True), OpEffect("b", "b2", False, "same_level")],
    )
    assert stats["response_ops"] == 3
    assert stats["accepted_ops"] == 3
    assert stats["effective_ops"] == 1
    assert stats["noop_ops"] == 1
    assert stats["rejected_ops"] == 1
    assert stats["latency_ms"] == 12.5


def test_payload_carries_v25_schema_and_contract():
    blocks = [Block(id="p1", type="paragraph", text="前一段没有句号"),
              Block(id="p2", type="paragraph", text="后一段也没有句号"),
              Block(id="h1", type="heading", level=5,
                    text="5.1 这是一个超过六十个字符的长编号标题，用来验证标题候选也会进入同一批次并带上层级参数契约")]
    client = LlmClient(bind="127.0.0.1", port=1, retries=0)
    payloads = client.payload_batches(blocks, DEFAULT_OP_WHITELIST)
    assert payloads
    system = payloads[0]["messages"][0]["content"]
    user = payloads[0]["messages"][1]["content"]
    assert "参数契约" in system and "fix_heading_level" in system
    assert '"op_schema"' in user
    assert '"required"' in user
    assert '"level"' in user and '"at"' in user
    # 本批没有 list_item 候选，不再下发 marker 的参数契约。
    clean_hits = user.count('"marker"')
    # 同主语对照：`"marker"` 只长在 `fix_list_marker` 的参数契约里，所以「这批没有 list_item
    # 候选 → user 里没有 "marker"」在契约压根不出门时同样成立。补一条含 list_item 候选的批次，
    # 同一个 `count('"marker"')` 判据必须命中 1，否则前一条否定只是空话。
    li_blocks = [Block(id="p1", type="paragraph", text="前一段没有句号"),
                 Block(id="li1", type="list_item", text="一、列表项甲。二、列表项乙")]
    li_user = LlmClient(bind="127.0.0.1", port=1, retries=0).payload_batches(
        li_blocks, DEFAULT_OP_WHITELIST)[0]["messages"][1]["content"]
    # 命中基准 2：`fix_list_marker` 的参数契约里 `"marker"` 出现两处——
    # `"required": ["target", "marker"]` 的表元素 + 独立的 `"marker"` 说明键。
    marker_hits = li_user.count('"marker"')
    assert (clean_hits, marker_hits) == (0, 2), (
        f"marker 契约下发判据无感：无 list_item 候选批命中 {clean_hits} 处、"
        f"有候选批命中 {marker_hits} 处（应为 0 / 2；有候选批为 0 说明参数契约压根不出门，"
        f"前一条否定恒真）")


def test_payload_op_schema_is_narrowed_to_batch_candidates():
    """2B 实测：全量 schema 会让只该修标题的批次输出空 op。"""
    blocks = [Block(id="p1", type="paragraph", text="前一段没有句号"),
              Block(id="p2", type="paragraph", text="后一段也没有句号")]
    client = LlmClient(bind="127.0.0.1", port=1, retries=0)
    user = client.payload_batches(blocks, DEFAULT_OP_WHITELIST)[0]["messages"][1]["content"]
    obj, _ = json.JSONDecoder().raw_decode(user, user.index("{"))
    assert set(obj["op_schema"]) == {"merge_block", "split_block"}
    assert "白名单 op：[\"merge_block\", \"split_block\"]" in user
    only_para = list(obj["op_schema"])
    narrow_hits = [only_para.count(op) for op in
                   ("fix_heading_level", "fix_list_marker", "mark_table_boundary")]
    # 同主语对照：三条否定只在「schema 永远只发 merge/split」这种实现下也成立，
    # 而那只意味着收窄判据是死的。补一条标题候选 + 列表项候选 + 紧邻表的段落同批的样本，
    # 三个 op 必须各进 `op_schema` 一次——收窄是**按批次**发生的，不是把门焊死。
    wide_blocks = [
        Block(id="h1", type="heading", level=5,
              text="5.1 这是一个超过六十个字符的长编号标题，用来验证标题候选也会进入同一批次"
                   "并带上层级参数契约，确保收窄是按候选类型发生的而不是把门焊死"),
        Block(id="li1", type="list_item", text="一、列表项甲。二、列表项乙"),
        Block(id="p2", type="paragraph", text="表前正文 第 3 页 共 10 页 残留"),
        Block(id="t1", type="table", text="| a | b |")]
    wide_user = LlmClient(bind="127.0.0.1", port=1, retries=0).payload_batches(
        wide_blocks, DEFAULT_OP_WHITELIST)[0]["messages"][1]["content"]
    wide, _ = json.JSONDecoder().raw_decode(wide_user, wide_user.index("{"))
    wide_hits = [list(wide["op_schema"]).count(op) for op in
                 ("fix_heading_level", "fix_list_marker", "mark_table_boundary")]
    assert (narrow_hits, wide_hits) == ([0, 0, 0], [1, 1, 1]), (
        f"op_schema 按批收窄判据无感：纯段落批命中 {narrow_hits}、三种候选同批命中 {wide_hits}"
        f"（应为 [0, 0, 0] / [1, 1, 1]；同批一侧为 0 说明 schema 压根不随候选变，前三条否定恒真）")
