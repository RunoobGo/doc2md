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


# --- overlap_dedupe：缝合尾缀（CH-469）-------------------------------------------


def test_overlap_dedupe_drops_cross_page_seam_fragment():
    """后一片的跨片段落**尾缀**必须判为重叠伪影。

    S3 段落缓冲跨页存续后，段落归属起始页；片 0 在页 297 产出一个续写到页 298
    的整段，片 1 从页 298 起解析，只能得到该段的后半截——文本与整段不相等，
    纯指纹比对会漏掉它，产物里同一段文字出现两份。
    """
    spans = [(0, 300), (298, 600)]
    head = _blk("b1", "第一段前半。第二段起始内容，第二段一直续写越过片界。", 297)
    frag = _blk("b2", "第二段起始内容，第二段一直续写越过片界。", 298)
    tail_blk = _blk("b3", "独立段落。", 298)
    out = overlap_dedupe([head, frag, tail_blk], spans)
    assert out == [head, tail_blk]
    assert frag.flags["dropped_dup_of"] == fingerprint(head)


def test_overlap_dedupe_keeps_non_suffix_window_blocks():
    """尾缀判定不得扩大到「内容不同就顺手丢」：三种近似形态全部保留。"""
    spans = [(0, 300), (298, 600)]
    head = _blk("b1", "前半。后半内容。", 297)
    other = _blk("b2", "完全不相关的内容。", 298)          # 非同源
    prefix = _blk("b3", "前半。", 298)                      # 是前块的**前缀**而非后缀
    diff_type = _blk("b4", "前半。后半内容。", 298, btype="heading")   # 类型不同
    out = overlap_dedupe([head, other, prefix, diff_type], spans)
    assert out == [head, other, prefix, diff_type]
    assert not any(b.flags.get("dropped_dup_of") for b in out)


def test_overlap_dedupe_suffix_only_scans_recent_blocks():
    """比对面有常量上限：远处的同尾缀块不误伤（也保证不是全表扫描）。"""
    spans = [(0, 300), (298, 600)]
    far = _blk("b1", "很久以前的一段。共同结尾内容。", 10)
    middle = [_blk(f"m{i}", f"中间块{i}。", 20 + i) for i in range(70)]
    frag = _blk("b2", "共同结尾内容。", 298)
    out = overlap_dedupe([far, *middle, frag], spans)
    assert frag in out


def test_overlap_dedupe_replaces_seam_truncated_block():
    """前一片在片界处截断的段落，须由后一片的完整版**替换**（不是并存）。

    镜像形态：段落起于窗口页并续写越过前一片的覆盖末界，前一片只剩前半截，
    后一片给出整段。丢弃任何一份都会丢字，保留两份则前半出现两次。
    """
    spans = [(0, 300), (298, 600)]
    cut = _blk("b1", "跨片段落的前半。", 298)
    full = _blk("b2", "跨片段落的前半。后半内容。", 298)
    nxt = _blk("b3", "另一段。", 299)
    out = overlap_dedupe([cut, full, nxt], spans)
    assert out == [full, nxt]
    assert cut.flags["dropped_dup_of"] == fingerprint(full)
    assert full.flags.get("dropped_dup_of") is None


def test_overlap_dedupe_replacement_only_within_window():
    """替换只在重叠窗口内发生：窗口外的同页前缀关系不得被改写（不动正常正文）。"""
    spans = [(0, 300), (298, 600)]
    early = _blk("b1", "正文起手的句子。", 5)
    later = _blk("b2", "正文起手的句子。后面是全新的内容。", 5)
    out = overlap_dedupe([early, later], spans)
    assert out == [early, later]



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
    """跨片续接：后片首块并入前片末块，吸收留痕 **且正文必须搬运**（CH-469）。

    原实现只记 absorbed_ids、不搬文本，被吸收块的内容直接从产物消失
    （缝合 = 丢内容），本断言当时把丢失结果固化为「预期」。
    """
    left = _ir([_blk("b1", "步骤如下：", 0), _blk("b2", "本文件适用于", 1)], 0, 2)
    right = _ir([_blk("b1", "所有合同。", 2)], 2, 3)
    merged, stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert stats["stitched"] == 1
    tail = merged.blocks[-1]
    assert tail.flags.get("absorbed_ids"), "被吸收块的 id 应留痕"
    assert tail.text == "本文件适用于所有合同。", f"续接正文未搬运：{tail.text!r}"


def test_stitch_latin_continuation_gets_space():
    left = _ir([_blk("b1", "the contr", 0)], 0, 1)
    right = _ir([_blk("b1", "act covers", 1)], 1, 2)
    merged, stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert stats["stitched"] == 1
    assert merged.blocks[-1].text == "the contr act covers"


def test_stitch_never_absorbs_heading_by_font_heuristic():
    """CH-469：标题是离散结构节点。"3.1 前置条件" 无句末标点 + 同字号，
    原正文续接分支会把它判成「上一句没说完」并吸收掉整条标题。
    """
    left = _ir([_blk("b1", "3.1 前置条件", 1, btype="heading", size=12.0)], 0, 2)
    right = _ir([_blk("b1", "2 准备工作", 2, btype="heading", size=12.0)], 2, 3)
    merged, stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert stats["stitched"] == 0
    assert [b.text for b in merged.blocks] == ["3.1 前置条件", "2 准备工作"]
    assert [b.type for b in merged.blocks] == ["heading", "heading"]


def test_stitch_never_merges_across_tables():
    left = _ir([_blk("b1", "| a | b |", 0, btype="table")], 0, 1)
    right = _ir([_blk("b1", "| c | d |", 1, btype="table")], 1, 2)
    merged, _stats = stitch_shards([left, right], max_pages=300, dedupe=False)
    assert [b.type for b in merged.blocks] == ["table", "table"]
    assert merged.blocks[0].text == "| a | b |"     # 表格不续接，原文保持
