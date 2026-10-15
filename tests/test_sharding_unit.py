"""tests/test_sharding_unit.py — CH-462 批次④：sharding 纯函数直接单测（A-16 第 4 项）。

覆盖对象（此前仅经 convert 集成路径间接覆盖）：
- `shard_plan`：空 / 单片 / 整除 / 不整除 / 重叠语义；
- `fingerprint`：稳定性与区分度；
- `overlap_dedupe`：重叠页重复块去重（保留第一份 + `dropped_dup_of` 留痕）；
- `stitch_shards`：块序保持、跨片续接吸收（absorbed_ids）、id 重排（CH-426）。

方法论：纯函数直接构造 `IR` / `Block`，确定性、无 IO。
"""
from __future__ import annotations

import pytest

from doc2md.ir import IR, Block
from doc2md.pipeline.sharding import fingerprint, overlap_dedupe, shard_plan, stitch_shards

pytestmark = [pytest.mark.milestone("DoD")]


def _blk(bid, text, page, btype="paragraph", size=10.0, family="F"):
    return Block(id=bid, type=btype, text=text, page=page,
                 font={"family": family, "size": size})


def _ir(blocks, start, end):
    ir = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=end - start)
    ir.blocks = blocks
    ir.extra_meta["shard"] = {"index": 0, "start": start, "end": end}
    return ir


# --- shard_plan ---------------------------------------------------------------


def test_shard_plan_empty_and_single():
    assert shard_plan(0) == []
    assert shard_plan(1) == [(0, 1)]
    assert shard_plan(5, max_pages=10) == [(0, 5)]


def test_shard_plan_splits_with_overlap():
    spans = shard_plan(600, max_pages=300, overlap=2)
    assert spans == [(0, 300), (298, 600)]          # 第二片带 2 页重叠上下文
    assert spans[0][1] == 300 and spans[1][0] == 298


def test_shard_plan_remainder_page():
    spans = shard_plan(301, max_pages=300, overlap=0)
    assert spans == [(0, 300), (300, 301)]


def test_shard_plan_covers_all_pages():
    spans = shard_plan(730, max_pages=300, overlap=2)
    assert spans[0][0] == 0 and spans[-1][1] == 730
    for a, b in zip(spans, spans[1:]):
        assert b[0] < a[1]                          # 相邻片有重叠上下文


# --- fingerprint ----------------------------------------------------------------


def test_fingerprint_stable_and_discriminating():
    b1 = _blk("b1", "内容", 3)
    b2 = _blk("b2", "内容", 3)
    assert fingerprint(b1) == fingerprint(b2)       # 与 id 无关（跨片比对）
    assert fingerprint(_blk("b1", "内容 ", 3)) == fingerprint(b1)   # 尾空白不敏感
    assert fingerprint(_blk("b1", "内容", 4)) != fingerprint(b1)
    assert fingerprint(_blk("b1", "内容", 3, btype="heading")) != fingerprint(b1)


# --- overlap_dedupe ---------------------------------------------------------------


def test_overlap_dedupe_keeps_first_and_marks():
    spans = [(0, 300), (298, 600)]
    first = _blk("bX", "重复块", 298)                # 页 298 ∈ 两片重叠区
    second = _blk("bW", "重复块", 298)               # 同内容不同对象（模拟重叠区重复）
    unique = _blk("bY", "独有", 298)
    out = overlap_dedupe([first, unique, second], spans)
    assert len(out) == 2 and out[0] is first        # 保留文档顺序第一份
    assert first.flags.get("dropped_dup_of") is None
    assert second.flags.get("dropped_dup_of") == fingerprint(first)   # 第二份留痕

    # 同一对象重复出现（调用方误用）也按指纹去重
    out2 = overlap_dedupe([first, first], spans)
    assert len(out2) == 1


def test_overlap_dedupe_noop_single_span():
    blocks = [_blk("b1", "a", 0), _blk("b2", "a", 0)]
    assert overlap_dedupe(blocks, [(0, 5)]) is blocks


# --- stitch_shards ---------------------------------------------------------------


def test_stitch_keeps_order_and_renumbers():
    left = _ir([_blk("b1", "第一段，", 0), _blk("b2", "第二段。", 1)], 0, 2)
    right = _ir([_blk("b1", "第三段。", 2), _blk("b2", "第四段。", 3)], 2, 4)
    merged, stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert stats["shards"] == 2
    texts = [b.text for b in merged.blocks]
    assert texts == ["第一段，", "第二段。", "第三段。", "第四段。"]
    ids = [b.id for b in merged.blocks]
    assert ids == [f"b{i}" for i in range(1, 5)]    # CH-426：合并后 id 统一重排


def test_stitch_absorbs_continuation_with_trace():
    """跨片续接：后片首块并入前片末块，吸收留痕（R-04 编号续接）。"""
    left = _ir([_blk("b1", "步骤如下：", 0), _blk("b2", "3.1 前置条件", 1)], 0, 2)
    right = _ir([_blk("b1", "2 准备工作。", 2)], 2, 3)
    merged, stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert stats["stitched"] == 1
    # 缝合方向：后块吸收前块（前块不进产物，id 记入后块 absorbed_ids 留痕）
    tail = merged.blocks[-1]
    assert tail.text == "2 准备工作。"
    assert tail.flags.get("absorbed_ids"), "被吸收块的 id 应留痕"


def test_stitch_never_merges_across_tables():
    left = _ir([_blk("b1", "| a | b |", 0, btype="table")], 0, 1)
    right = _ir([_blk("b1", "| c | d |", 1, btype="table")], 1, 2)
    merged, _stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert [b.type for b in merged.blocks] == ["table", "table"]
    assert merged.blocks[0].text == "| a | b |"     # 表格不续接，原文保持
