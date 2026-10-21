"""P0 LLM 契约加固（2026-10-21）。

覆盖结论里的四条修复：
- 必填参数缺失 → 整条拒绝（missing_param:*），不再 accepted-but-noop；
- split_block 必须有安全 at，禁止中点兜底/词内/URL 内切分；
- mark_table_boundary 只允许 table 本身或紧邻 table 的块；
- apply_ops 返回逐目标报告，accepted / effective / noop 分账。
"""
from __future__ import annotations

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
    assert "mark_table_boundary" not in _allowed_ops_for(blocks, 0, wanted)
    assert "mark_table_boundary" in _allowed_ops_for(blocks, 1, wanted)


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
              Block(id="p2", type="paragraph", text="后一段也没有句号")]
    client = LlmClient(bind="127.0.0.1", port=1, retries=0)
    payloads = client.payload_batches(blocks, DEFAULT_OP_WHITELIST)
    assert payloads
    system = payloads[0]["messages"][0]["content"]
    user = payloads[0]["messages"][1]["content"]
    assert "参数契约" in system and "fix_heading_level" in system
    assert '"op_schema"' in user
    assert '"required"' in user
    assert '"level"' in user and '"marker"' in user and '"at"' in user
