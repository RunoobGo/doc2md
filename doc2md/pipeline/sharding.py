"""FR-126【再修订】大文档两阶段分片（ADR-27，上游蓝本 B-10；规格见方案 v2.0 4.3）。

① S2 后粗切：按页范围切分（默认 300 页/片），片间 2 页重叠上下文；
② 各片独立走 S3 / S4；
③ S4 后缝合：相邻片 shard_edge 边界块按「编号连续性（R-04）+ 字体相似」判定续接；
④ 跨片表格不合并记 W1043；
⑤ 产物 meta 记 sharded / shard_count，报告说明「本文为分片转换，跨片表格未合并」。

本轮对上游蓝本 B-10 的三处补强（登记 CH-423，见方案 v2.0 第十一章）：
- 重叠区去重：overlap 让同页块在相邻片各出现一次，源骨架缝合后仍保留两份
  → 新增 overlap_dedupe() 按「片界 ±1 块窗口 + 文本指纹」删除重复（CH-423）；
- W1043 检测落地：缝合处任一侧为 table 即记 W1043（源骨架只声明不落码）；
- absorbed_ids 累积：一个块连续吸收多块时不覆盖既有留痕。
"""
from __future__ import annotations

import hashlib
import re

from ..ir import Block, IR
from ..errors import WARN_TABLE_ACROSS_SHARD

_NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)[\.、\s]")
_NUM_TAIL = re.compile(r"(\d+(?:\.\d+)*)\s*[。.!！?？]?\s*$")
_SENT_END = ("。", "！", "？", ".", "!", "?", "；", ";")


def shard_plan(pages: int, max_pages: int = 300, overlap: int = 2) -> list[tuple[int, int]]:
    """S2 后粗切：页范围 + 重叠上下文（此时无标题信息，不得引用标题）。"""
    if pages <= 0:
        return []
    if pages <= max_pages:
        return [(0, pages)]
    spans: list[tuple[int, int]] = []
    start = 0
    while start < pages:
        end = min(start + max_pages, pages)
        spans.append((max(0, start - overlap), end))
        start = end
    return spans


def _num_key(s: str) -> tuple[int, ...]:
    return tuple(int(p) for p in s.split("."))


def _owner(page: int, spans: list[tuple[int, int]]) -> int:
    """页归属片号：重叠页归前一片（与 overlap_dedupe「保留第一份」一致）。"""
    for i, (lo, hi) in enumerate(spans):
        if lo <= page < hi:
            return i
    return -1


def _continuation(a: Block, b: Block) -> bool:
    """判定相邻分片边界块 a→b 是否可续接：R-04 编号续接 或 字体/字号一致且 a 非句末。"""
    ta = (a.text or "").rstrip()
    tb = (b.text or "").lstrip()
    if a.type == "table" or b.type == "table":
        return False
    ma, mb = _NUM_HEAD.match(tb), _NUM_TAIL.search(ta)
    if ma and mb:
        ka, kb = _num_key(ma.group(1)), _num_key(mb.group(1))
        if ka[:-1] == kb[:-1] and kb[-1] == ka[-1] + 1:
            return True
    if not ta:
        return False
    ends = ta.endswith(_SENT_END)
    fa, fb = a.font or {}, b.font or {}
    return (str(fa.get("family", "")) == str(fb.get("family", ""))
            and abs(float(fa.get("size", 0) or 0) - float(fb.get("size", 0) or 0)) < 0.5
            and not ends)


def fingerprint(b: Block) -> str:
    return hashlib.sha256(
        f"{b.type}|{(b.text or '').strip()}|{b.page}|{round(float((b.font or {}).get('size', 0) or 0), 1)}"
        .encode("utf-8", "replace")).hexdigest()[:16]


def overlap_dedupe(blocks: list[Block], spans: list[tuple[int, int]],
                   max_pages: int = 300) -> list[Block]:
    """CH-423：删除片间重叠区造成的重复块。

    只在片界前后窗口内比对指纹；重复时保留前一份（文档顺序第一出现的即正确位置）。
    """
    if len(spans) < 2:
        return blocks
    boundaries: set[int] = set()
    for i in range(1, len(spans)):
        overlap_pages = {p for p in range(spans[i][0], min(spans[i - 1][1], spans[i][1]))}
        boundaries |= overlap_pages
    if not boundaries:
        return blocks
    seen: set[str] = set()
    out: list[Block] = []
    for b in blocks:
        fp = fingerprint(b)
        if b.page in boundaries and fp in seen:
            b.flags.setdefault("dropped_dup_of", fp)
            continue
        seen.add(fp)
        out.append(b)
    return out


def _renumber(blocks: list[Block]) -> None:
    """CH-426：各片 S3 的块 id 都从 b1 起，直接合并会产生重复 id
    → PATCH / rollback / FTS 按 id 寻址会命中错块。去重后、缝合前统一重排 b1..bN，
    使后续 absorbed_ids 记录的即最终 id。"""
    for i, b in enumerate(blocks, start=1):
        b.id = f"b{i}"


def stitch_shards(shard_irs: list[IR], max_pages: int = 300,
                  dedupe: bool = True) -> tuple[IR, dict]:
    """S4 后缝合：保持文档顺序；b 吸收 a 时 a 不进产物并记 absorbed_ids。

    返回 (merged_ir, stats)。stats 含 stitch/edges/warnings/dropped_dupes。
    """
    if not shard_irs:
        raise ValueError("stitch_shards: empty shard_irs")
    # convert.py 按片存 extra_meta["shard"] = {"index","start","end"}（CH-423 勘误：
    # 原蓝本示意 s["shard"]["start"] 双层索引，实装为单层，误读会 KeyError→静默降级）
    spans = [(m["start"], m["end"]) for m in
             (ir.extra_meta.get("shard") for ir in shard_irs) if m]
    spans = [s for s in spans if s] or [(0, 0)]

    blocks: list[Block] = [b for ir in shard_irs for b in ir.blocks]
    dropped_dupes = 0
    n_before = len(blocks)
    if dedupe:
        blocks = overlap_dedupe(blocks, spans, max_pages)
        dropped_dupes = n_before - len(blocks)
    _renumber(blocks)                          # CH-426：缝合前先统一编号

    merged: list[Block] = []
    i = 0
    stitched = seams = w_table = 0
    # CH-427：shard_edge 语义收敛为「真正的跨片缝合候选」，先清各片预打的边界页标记，
    # 再由缝合阶段按片界重打，避免报告把片内块当分片问题筛出来。
    for b in blocks:
        b.flags.pop("shard_edge", None)
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        own = _owner(b.page or 0, spans)
        cross = nxt is not None and own >= 0 and own != _owner(nxt.page or 0, spans)
        if cross:
            seams += 1
            b.flags["shard_edge"] = True
            nxt.flags["shard_edge"] = True
            if _continuation(b, nxt):
                ids = nxt.flags.setdefault("absorbed_ids", [])
                if b.id not in ids:
                    ids.append(b.id)
                nxt.provenance.setdefault("modified_by", []).append("s4:shard_stitch")
                stitched += 1
                i += 1
                continue
            # 不续接：跨片表格记 W1043，两侧保留 shard_edge 供审阅筛选
            if b.type == "table" or nxt.type == "table":
                w_table += 1
        merged.append(b)
        i += 1

    head = shard_irs[0]
    # CH-438：pages 不得按各片求和 —— 重叠页（默认 2 页）会在相邻两片各计一次，
    # 320 页样本实算 322。片范围（spans）才是真实页数口径。
    true_pages = max((hi for _, hi in spans), default=0) or sum(ir.pages for ir in shard_irs)
    out = IR(
        doc_id=head.doc_id, source_key=head.source_key,
        source_name=head.source_name, source_name_raw=head.source_name_raw,
        format=head.format, pages=true_pages,
        blocks=merged, sharded=len(shard_irs) > 1, shard_count=len(shard_irs),
        warnings=list(dict.fromkeys(
            [w for ir in shard_irs for w in ir.warnings]
            + ([WARN_TABLE_ACROSS_SHARD] if w_table else []))),
    )
    out.extra_meta.update(head.extra_meta)
    stats = {"shards": len(shard_irs), "stitched": stitched, "seams": seams,
             "edges": sum(1 for b in merged if b.flags.get("shard_edge")),
             "tables_across_shard": w_table, "dropped_dupes": dropped_dupes,
             "blocks_raw": n_before, "blocks_after_dedupe": len(blocks),
             "blocks_after": len(merged)}
    return out, stats


def mark_shard_edges(ir: IR, spans: list[tuple[int, int]]) -> None:
    """S4 收尾：落在片边界页（首块/末块语义）的块打 shard_edge。"""
    if len(spans) < 2:
        return
    boundary_pages: set[int] = set()
    for j in range(1, len(spans)):
        lo, hi = spans[j]
        boundary_pages |= {lo, lo + 1, hi - 1}
    for b in ir.blocks:
        if b.page in boundary_pages:
            b.flags["shard_edge"] = True
