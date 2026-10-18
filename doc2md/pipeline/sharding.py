"""FR-126【再修订】大文档两阶段分片（ADR-27，上游蓝本 B-10；规格见方案 4.3）。

① S2 后粗切：按页范围切分（默认 300 页/片），片间 2 页重叠上下文；
② 各片独立走 S3 / S4；
③ S4 后缝合：相邻片 shard_edge 边界块按「编号连续性（R-04）+ 字体相似」判定续接；
④ 跨片表格不合并记 W1043；
⑤ 产物 meta 记 sharded / shard_count，报告说明「本文为分片转换，跨片表格未合并」。

本轮对上游蓝本 B-10 的三处补强（登记 CH-423，见方案 第十一章）：
- 重叠区去重：overlap 让同页块在相邻片各出现一次，源骨架缝合后仍保留两份
  → 新增 overlap_dedupe() 按「片界 ±1 块窗口 + 文本指纹」删除重复（CH-423）；
- W1043 检测落地：缝合处任一侧为 table 即记 W1043（源骨架只声明不落码）；
- absorbed_ids 累积：一个块连续吸收多块时不覆盖既有留痕。
"""
from __future__ import annotations

import hashlib
import re

from ..errors import WARN_TABLE_ACROSS_SHARD
from ..ir import IR, Block

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


def _cjk(ch: str) -> bool:
    return bool(ch) and ord(ch[0]) > 0x2E80


def _merge_text(a: Block, b: Block) -> str:
    """CH-469：缝合时把被吸收块 a 的正文并入 b（保留换行，供标题续接用）。"""
    ta, tb = (a.text or "").rstrip(), (b.text or "").lstrip()
    if not ta:
        return tb
    if not tb:
        return ta
    if a.type == "heading" or b.type == "heading":
        return f"{ta}\n{tb}"
    joiner = "" if (_cjk(ta[-1]) or _cjk(tb[0])) else " "
    return ta + joiner + tb


# R7-PIPE-1（D3）：结构块——离散结构节点，缝合续接一律禁止吸收
# CH-485（R9-PIPE-8）：list_item 也是结构节点；跨片两个列表项若被“续接”，
# 会拼成 "1. 第一项2. 第二项" 并丢掉列表边界。
_STRUCTURAL = frozenset({"heading", "table", "code", "quote", "page_break",
                         "list_item"})


def _continuation(a: Block, b: Block) -> bool:
    """判定相邻分片边界块 a→b 是否可续接：R-04 编号续接 或 字体/字号一致且 a 非句末。

    CH-469：正文续接分支不得命中标题块——标题是离散结构节点，"3.1 前置条件"
    无句末标点 + 同字号即被误判成"a 未说完"，导致前一片标题被吸收。

    R7-PIPE-1（D3）两条收紧，CH-469 口径推广到**全部**结构块：
    - 编号续接比较原为**方向反置**（`kb[-1] == ka[-1] + 1`；`ka` 是后块头编号、
      `kb` 是前块尾编号，续接必须要求 b = a + 1）：自然递增 5→6 永不命中
      （该分支对真实语料是死代码），递减 6→5 反而命中并错误缝合。修正为
      `ka[-1] == kb[-1] + 1`；
    - 结构块守卫（heading/table/code/quote/page_break）**前移到编号分支之前**：
      原编号分支在 heading 守卫之前 return，"4.2 部署要点"标题可被吸收进前片
      段落；code/quote/page_break 在 `font={}` 时 family/size 恒匹配，经字体分支
      被吸收（page_break 吸收 code 后渲染空串 = 整块代码静默消失）。
      两分支现仅对 paragraph / list_item 生效。
    """
    ta = (a.text or "").rstrip()
    tb = (b.text or "").lstrip()
    if a.type in _STRUCTURAL or b.type in _STRUCTURAL:
        return False
    ma, mb = _NUM_HEAD.match(tb), _NUM_TAIL.search(ta)
    if ma and mb:
        ka, kb = _num_key(ma.group(1)), _num_key(mb.group(1))
        if ka[:-1] == kb[:-1] and ka[-1] == kb[-1] + 1:
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
                   max_pages: int = 300,
                   shard_bounds: list[int] | None = None) -> list[Block]:
    """CH-423：删除片间重叠区造成的重复块。

    只在片界前后窗口内比对指纹；重复时保留前一份（文档顺序第一出现的即正确位置）。
    CH-469 补「缝合尾缀」判定：S3 的段落缓冲跨页存续后，一个段落归属**起始页**，
    而该片起始页早于重叠窗口——后一片从窗口页开始解析时，只能产出该段落的
    **后半截**，文本与整块不相等，指纹因而漏掉它，重叠内容在产物里出现两份。
    故在指纹相等之外再加两条：窗口内某块若与更早一片某块同类型同字号、且正文
    是后者的**真后缀**（后一片只拿到跨片段落的尾缀），判为伪影丢弃；若某已保留的
    窗口块正文是它的**真前缀**（前一片在片界处被截断，后一片给出完整版），
    用完整版替换截断版。两条都只在「最近 64 个已保留块」这一常量比对面内查找。

    CH-473（R5-PIPE-5 / R5-PIPE-6，G1「分片≡单片」）两条收紧：
    - `_seam_suffix` 加**页差约束**（CH-476 / R7-PIPE-2 把原「恰为相邻页」放宽为
      `0 < b.page - a.page <= 最宽重叠窗口 + 1`）：跨页续写段落的尾缀块来自后一片
      的窗口首页，其完整版起始页可早若干页；无约束时文档里重复出现的独立短句（法律/
      公文的重复条款、「特此说明。」恰是前一片某长段的结尾）页不相邻也会被判成尾缀而
      **整块误删**——它不是任何段落的尾缀，是真实内容（静默丢字 + 分片≠单片）；
      约束过窄则跨 ≥3 页长块的尾缀漏判、重叠内容在产物里留两份。
    - `_seam_truncated` 加**片级锚定**：被替换的截断版必须是「某一片的最后一块」
      且完整版来自后一片（`shard_bounds` 由 `stitch_shards` 按片序传入）。块层
      文本无法区分「片界截断版 + 后片完整版」与「同页两个真实块构成前缀关系」
      （列表项「注意」与「注意事项如下：……」）——只有片界位置能：真实短块不是
      片末块（同页同片的更长块跟在它之后），锚定不成立即不替换。锚定漏判时退化为
      「截断内容留两份」，由 `shard_two_phase` 门禁的块数相等断言可判定暴露，
      不会变成静默丢内容。
    """
    if len(spans) < 2:
        return blocks
    boundaries: set[int] = set()
    widest = 0
    for i in range(1, len(spans)):
        overlap_pages = {p for p in range(spans[i][0], min(spans[i - 1][1], spans[i][1]))}
        boundaries |= overlap_pages
        widest = max(widest, len(overlap_pages))
    if not boundaries:
        return blocks
    # 缝合尾缀允许的最大页差（CH-476 / R7-PIPE-2）：尾缀块落在后一片的窗口首页，
    # 其完整版的起始页最早可早到前一片窗口首页之前一页，故界 = 最宽窗口 + 1
    seam_gap = widest + 1
    # 片级锚定表：每片最后一块的全局索引（CH-473 / R5-PIPE-6；未提供 = 退化为纯文本判定）
    last_idx = {e - 1 for e in shard_bounds} if shard_bounds else set()
    seen: set[str] = set()
    out: list[Block] = []
    tail: list[tuple[int, Block]] = []           # (全局索引, 块)——片级锚定需要索引
    for idx, b in enumerate(blocks):
        # CH-469：指纹只在片界窗口内计算——seen 仅被窗口内的块填充，
        # 窗口外块不可能命中，原先对全文档每块 sha256 是纯 CPU 浪费
        # （320 页样本 ≈ 每片一次全量重扫）。指纹含页号，相等必同页，
        # 同页即同进同出窗口，故收敛比对面不改变判定结果。
        in_window = b.page in boundaries
        if in_window:
            fp = fingerprint(b)
            if fp in seen:
                b.flags.setdefault("dropped_dup_of", fp)
                continue
            src = _seam_suffix(tail, b, seam_gap)
            if src is not None:
                b.flags.setdefault("dropped_dup_of", fingerprint(src))
                continue
            cut = _seam_truncated(tail, b, boundaries, idx, last_idx)
            if cut is not None and _replace_recent(out, cut):
                seen.discard(fingerprint(cut))
                cut.flags.setdefault("dropped_dup_of", fp)
                # 被替换的旧截断块仍留在 tail 里：后续窗口块若恰好是它的后缀，
                # `_seam_suffix` 会把新内容当伪影丢掉。替换成功即同步移出 tail。
                for j, (_ti, tb) in enumerate(tail):
                    if tb is cut:
                        del tail[j]
                        break
            seen.add(fp)
        out.append(b)
        tail.append((idx, b))
        if len(tail) > 64:
            del tail[:-64]
    return out


def _same_face(a: Block, b: Block) -> bool:
    """同类型同字号（缝合比对的前提，与 `_continuation` 的字体口径一致）。"""
    if a.type != b.type:
        return False
    fa, fb = a.font or {}, b.font or {}
    return abs(float(fa.get("size", 0) or 0) - float(fb.get("size", 0) or 0)) < 0.5


def _seam_suffix(tail: list[tuple[int, Block]], b: Block,
                 max_gap: int = 1) -> Block | None:
    """在 tail 里找「b 是其真后缀」的前一片块，找不到返回 None。

    CH-473（R5-PIPE-5）：加**页差约束**——跨页续写段落从起始页的下一页才开始
    产出尾缀块。缺此约束时，文档中重复出现的独立短句（「特此说明。」恰是前一片
    某长段的结尾、且落在后一片的重叠窗口页）页不相邻也会被删——那不是任何段落的
    尾缀，是真实内容。

    CH-476（R7-PIPE-2）：约束由「恰为相邻页」放宽为 `0 < b.page - a.page <= max_gap`
    （max_gap = 最宽重叠窗口 + 1，由 `overlap_dedupe` 按 spans 算出）。原 `== +1`
    只对「完整块起始页 = 窗口首页 − 1」成立：跨 ≥3 页的长块（长段/代码清单/表格）
    起始页更早，页差 ≥2 时尾缀漏判、重叠内容在产物里留两份。上限仍由窗口宽度界定，
    远处的重复短句照旧不误删。
    """
    tb = (b.text or "").strip()
    if not tb:
        return None
    for _idx, a in reversed(tail):
        gap = b.page - a.page if a.page is not None and b.page is not None else -1
        if not 0 < gap <= max_gap or not _same_face(a, b):
            continue
        ta = (a.text or "").strip()
        if len(ta) > len(tb) and ta.endswith(tb):
            return a
    return None


def _seam_truncated(tail: list[tuple[int, Block]], b: Block, boundaries: set[int],
                    b_idx: int = -1, last_idx: set[int] | None = None) -> Block | None:
    """在 tail 里找「b 的正文以它为真前缀」的同页窗口块（前一片的截断版），找不到返回 None。

    CH-473（R5-PIPE-6）：加**片级锚定**——截断版必须是某一片的最后一块
    （`a_idx in last_idx`）且完整版来自后一片（`b_idx > a_idx`）。同页两个真实块
    天然构成前缀关系时（列表项「注意」与「注意事项如下：……」），短块不是片末块
    （同页同片的更长块跟在它之后），锚定不成立即不替换。块层文本无法区分这两种
    结构，只有片界位置能。未提供 `last_idx`（测试直调）时退化为纯文本判定。
    """
    tb = (b.text or "").strip()
    if not tb or b.page not in boundaries:
        return None
    for a_idx, a in reversed(tail):
        if a.page != b.page or not _same_face(a, b):
            continue
        if last_idx and (a_idx not in last_idx or b_idx <= a_idx):
            continue
        ta = (a.text or "").strip()
        if len(tb) > len(ta) and tb.startswith(ta):
            return a
    return None


def _replace_recent(out: list[Block], old: Block) -> bool:
    """从 out 末尾 64 项里按身份摘掉 old（比对面对应的常量范围）。"""
    for i in range(len(out) - 1, max(-1, len(out) - 65), -1):
        if out[i] is old:
            del out[i]
            return True
    return False


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

    blocks: list[Block] = []
    bounds: list[int] = []                       # 每片结束的累计索引（CH-473 / R5-PIPE-6）
    for ir in shard_irs:
        blocks.extend(ir.blocks)
        bounds.append(len(blocks))
    dropped_dupes = 0
    n_before = len(blocks)
    if dedupe:
        # 片级锚定与 spans 同源（都按 extra_meta["shard"] 过滤）：长度不一致
        # （异常 IR 缺 shard meta）时放弃锚定，退化为纯文本判定。
        anchored = bounds if len(bounds) == len(spans) else None
        blocks = overlap_dedupe(blocks, spans, max_pages, shard_bounds=anchored)
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
                # CH-469：吸收必须搬运正文（原实现只留痕 absorbed_ids，
                # 被吸收块文本直接消失 = 缝合即丢内容）
                nxt.text = _merge_text(b, nxt)
                # CH-485（R9-PIPE-5）：缝合后的块级 marks 取双方交集，避免把
                # 一片的局部粗体扩散到吸收后的整块。
                nxt.marks = {k: True for k in (b.marks or {})
                             if (b.marks or {}).get(k) and (nxt.marks or {}).get(k)}
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
