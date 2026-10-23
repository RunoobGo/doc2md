"""仓储层：docs / blocks / audit / search / 删除级联。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from ..errors import E4041_NotFound, E4091_DocOverwrite, E5031_StorageError
from ..ir import IR
from .db import Store, now_iso

# CH-471（STOR-8）：搜索索引只写入正文前 SEARCH_INDEX_WINDOW 个字符。FTS5 的索引体积
# 与分词成本随正文线性增长，超长块取前窗即覆盖绝大多数检索命中。关键：这**不是**数据截断
# ——`blocks.text` 始终存全文，窗口外的尾部照常读得到，只是「落在窗口外的词」不进 FTS
# 命中（LIKE 兜底检索见 search()）。原 `[:8000]` 魔法数散落三处、口径易漂移，收敛为常量。
SEARCH_INDEX_WINDOW = 8000


def _fts_replace_doc(t, doc_id: str, rows) -> None:
    """整篇替换 FTS 行，删除走 `search_map` rowid 映射（CH-476 / R7-STOR-1，决策 D1）。

    FTS5 的 doc_id/block_id 均 UNINDEXED：原 `DELETE FROM search_blocks WHERE
    doc_id=?` 是对全内容表的线性扫描，且发生在 BEGIN IMMEDIATE 写锁内——
    10 万块级库上 worker 批量落库 100 文档 = 100 次全库扫（检索面库龄税）。
    映射表把删除收敛为 O(本文档块数)，插入显式赋 rowid 并同步映射。
    rows = [(block_id, text_window, aux), ...]。
    """
    t.execute("DELETE FROM search_blocks WHERE rowid IN"
              " (SELECT fts_rowid FROM search_map WHERE doc_id=?)", (doc_id,))
    t.execute("DELETE FROM search_map WHERE doc_id=?", (doc_id,))
    if not rows:
        return
    t.executemany("INSERT OR IGNORE INTO search_map(doc_id, block_id) VALUES (?,?)",
                  [(doc_id, bid) for bid, _x, _a in rows])
    ids = {r[0]: r[1] for r in t.execute(
        "SELECT block_id, fts_rowid FROM search_map WHERE doc_id=?",
        (doc_id,)).fetchall()}
    # 同篇重号块（坏 IR 理论形态）只索引首现——映射唯一，重复行对检索无增量意义
    t.executemany("INSERT INTO search_blocks(rowid, doc_id, block_id, text, aux)"
                  " VALUES (?,?,?,?,?)",
                  [(ids[bid], doc_id, bid, x, a) for bid, x, a in rows if bid in ids])


def _fts_replace_block(t, doc_id: str, block_id: str, text_aux) -> None:
    """单块 FTS 替换（CH-476 / R7-STOR-1）：经唯一索引 (doc_id, block_id) O(1) 定位。

    原每次 PATCH 编辑都 `DELETE ... WHERE doc_id=? AND block_id=?` 全库扫——
    单块保存的写锁时长随库龄增长。`text_aux=None` 时只删不插。
    """
    t.execute("DELETE FROM search_blocks WHERE rowid IN"
              " (SELECT fts_rowid FROM search_map WHERE doc_id=? AND block_id=?)",
              (doc_id, block_id))
    t.execute("DELETE FROM search_map WHERE doc_id=? AND block_id=?",
              (doc_id, block_id))
    if text_aux is None:
        return
    cur = t.execute("INSERT INTO search_map(doc_id, block_id) VALUES (?,?)",
                    (doc_id, block_id))
    t.execute("INSERT INTO search_blocks(rowid, doc_id, block_id, text, aux)"
              " VALUES (?,?,?,?,?)",
              (cur.lastrowid, doc_id, block_id, text_aux[0], text_aux[1]))



def prepare_ir_rows(ir: IR):
    """事务外预计算 blocks 插入行与 FTS 行（JSON/cjk_aux 是写锁时长主因）。"""
    from .fts import cjk_aux
    block_rows = [
        (ir.doc_id, b.id, b.type, b.text, b.level, b.page,
         json.dumps(b.flags, ensure_ascii=False),
         json.dumps(b.marks, ensure_ascii=False),
         json.dumps(b.links or [], ensure_ascii=False),
         json.dumps(b.font or {}, ensure_ascii=False),
         json.dumps(b.provenance or {}, ensure_ascii=False),
         int(b.immutable), b.revision)
        for b in ir.blocks]
    fts_rows = [(b.id, b.text[:SEARCH_INDEX_WINDOW],
                 cjk_aux(b.text[:SEARCH_INDEX_WINDOW]))
                for b in ir.blocks if b.text]
    return block_rows, fts_rows


def save_ir(store: Store, ir: IR, profile: str = "standard", tx=None,
            force: bool = False, meta: dict | None = None,
            gate: dict | None = None, prepared=None) -> None:
    """整本文档入库（docs + blocks + FTS）。

    CH-459（A-4/A-14）：原实现 docs/blocks 与 reindex 各起一次事务、逐块
    `execute`——N 块 = 2 次写事务 + 2N 条语句（当时 `synchronous=FULL`，每提交
    一次 fsync；该 PRAGMA 自 CH-471 / STOR-1 起为 NORMAL，合批仍保留——少事务
    往返并保持崩溃原子性）。现合并为 **单事务 + executemany**（两次语句批量）。

    `tx` 形参（CH-459）：调用方可传入已有事务把入库并入其中——
    `worker._finish_ok` 用它把 detail/mark/event/入库四写合成一次提交。

    CH-471（STOR-5，HIGH / 数据安全）：`doc_id = hash_of_path(p)` 是内容寻址，
    同源文件重复转换永远落到同一文档行——原实现 `INSERT OR REPLACE` +
    `DELETE FROM blocks` 会**静默清空用户编辑**（块 revision 归 1、孤儿化
    edit_audit、rollback 能把旧文本当新版本复活）。现改为护编辑：
    - 库内该文档已有 `revision>1` 的块且未 `force` → 抛 `E4091_DocOverwrite`
      （落在调用方事务内，整笔回滚，编辑原样保留）；
    - 覆盖（force 或无编辑的幂等重写）时**保留原 `created_at`**，只更新 `updated_at`；
    - 每次真实覆盖既有文档都记 `doc_overwritten` 事件（只带计数、无正文，NFR-20）。
    """
    ts = now_iso()
    # CH-485（R9-SVC-2）：DB 成为产物事实源——保存 package 实际使用的完整
    # meta/gate 与块级 links/font/provenance；旧调用方可省略，缺省仍为 ir.meta()。
    doc_meta = meta if meta is not None else ir.meta()
    gate_json = json.dumps(gate or {}, ensure_ascii=False)
    # CH-485（R9-SVC-6）：调用方可在 BEGIN IMMEDIATE 之前传入预计算结果；
    # 非 tx 路径也在此之前完成，写锁只留 SQL。
    block_rows, fts_rows = prepared if prepared is not None else prepare_ir_rows(ir)

    def _do(t):
        cur = t.execute("SELECT created_at FROM docs WHERE doc_id=?",
                        (ir.doc_id,)).fetchone()
        existed = cur is not None
        created_at = cur["created_at"] if existed else ts
        edited = prev_blocks = 0
        if existed:
            prev_blocks = t.execute(
                "SELECT COUNT(*) c FROM blocks WHERE doc_id=?",
                (ir.doc_id,)).fetchone()["c"]
            edited = t.execute(
                "SELECT COUNT(*) c FROM blocks WHERE doc_id=? AND revision>1",
                (ir.doc_id,)).fetchone()["c"]
            if edited and not force:
                raise E4091_DocOverwrite(
                    "document has user edits; re-conversion would overwrite them",
                    detail={"doc_id": ir.doc_id, "edited_blocks": edited,
                            "hint": "pass force=true to overwrite"})
        t.execute(
            "INSERT OR REPLACE INTO docs(doc_id, source_key, source_name, source_name_raw,"
            " format, pages, sharded, shard_count, status, profile, created_at, updated_at,"
            " meta_json, gate_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ir.doc_id, ir.source_key, ir.source_name, ir.source_name_raw, ir.format,
             ir.pages, int(ir.sharded), ir.shard_count, "done", profile, created_at, ts,
             json.dumps(doc_meta, ensure_ascii=False), gate_json))
        t.execute("DELETE FROM blocks WHERE doc_id=?", (ir.doc_id,))
        # CH-473（R5-STOR-1 / STOR-5 的 force 半修）：覆盖时同事务清掉该 doc 的编辑
        # 审计行——edit_audit 的 before/after 与 revision 序列随块删除已失效，残留行
        # 会让 `rollback(to_revision=N)` 把**覆盖前的旧文本**当新版本复活（数据安全面）。
        # 无编辑时该表本就为空（有审计 ⟺ 存在 revision>1 的块），删 0 行无害。
        purged_audit = t.execute(
            "DELETE FROM edit_audit WHERE doc_id=?", (ir.doc_id,)).rowcount or 0
        t.executemany(
            "INSERT INTO blocks(doc_id, block_id, type, text, level, page,"
            " flags_json, marks_json, links_json, font_json, provenance_json,"
            " immutable, revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            block_rows)
        _fts_replace_doc(t, ir.doc_id, fts_rows)
        if existed:
            # 覆盖留痕只记计数与来源，不记正文（NFR-20）
            t.execute("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
                      " VALUES (?,?,?,?,?)",
                      (ts, "doc_overwritten", ir.doc_id, None,
                       json.dumps({"overwritten_blocks": prev_blocks,
                                   "overwritten_edits": edited,
                                   "purged_audit": purged_audit,
                                   "forced": bool(force)}, ensure_ascii=False)))

    if tx is not None:
        _do(tx)
    else:
        with store.tx() as t:
            _do(t)


def reindex(store: Store, doc_id: str, blocks) -> int:
    """搜索索引刷新（CH-418：编辑提交后 ≤5s 可检索到新内容）。

    CH-435：同时写入 aux 列（CJK unigram+bigram），否则中文子串查询命中 0。
    CH-459（A-14）：executemany 批量插入（原逐块 execute）。
    """
    from .fts import cjk_aux
    with store.tx() as tx:
        _fts_replace_doc(tx, doc_id, [
            (b.id, b.text[:SEARCH_INDEX_WINDOW],
             cjk_aux(b.text[:SEARCH_INDEX_WINDOW]))
            for b in blocks if b.text])
    return len(blocks)


def reconcile_search(store: Store) -> dict:
    """搜索索引对账（CH-471 / STOR-3，复活死代码 `reindex`）。

    FTS 与 blocks 的一致性只在写入路径维护；`_migrate_fts` 中途崩溃（旧实现先
    DROP 再回填）或历史缺陷，都会留下「blocks 有正文、search_blocks 无对应行」的
    空/缺索引——检索静默失效且无告警。本函数按文档比对「有正文块数 vs 索引行数」，
    不一致即整篇 `reindex`（幂等：先 DELETE 该 doc 全部索引行再插）。启动或 cron 调用。
    只回传统计，不读正文（NFR-20）。
    """
    divergent = store.q(
        "SELECT b.doc_id AS doc_id, b.nb AS nb FROM"
        " (SELECT doc_id, COUNT(*) nb FROM blocks WHERE text<>'' GROUP BY doc_id) b"
        # CH-476（R7-STOR-1）：索引侧计数经 search_map **联查真实 FTS 行**——
        # 直接数映射行会有盲区：FTS 行被单独抹掉（历史缺陷/手工库操作）而映射
        # 行还在时，`_fts_replace_doc` 的 `INSERT OR IGNORE` 会跳过该 block_id，
        # 缺失的索引行永不补回。联 FTS 才是「检索真的少了这行」的口径。
        # FTS5 的 rowid 是主键，联接不走 UNINDEXED 列，无全库内容扫描之扰。
        " LEFT JOIN (SELECT m.doc_id AS doc_id, COUNT(*) ns FROM search_map m"
        "  JOIN search_blocks sb ON sb.rowid=m.fts_rowid GROUP BY m.doc_id) s"
        " ON s.doc_id=b.doc_id"
        " WHERE b.nb <> COALESCE(s.ns, 0)")
    docs_repaired = blocks_reindexed = 0
    for row in divergent:
        doc_id = row["doc_id"]
        blocks_reindexed += reindex(store, doc_id, list_blocks(store, doc_id))
        docs_repaired += 1
    # 孤儿索引行：blocks 侧已无该 doc（删除级联之外的历史残留），清掉避免命中不存在的块。
    # CH-483（R8-PERF-1）：`search_blocks.doc_id` 是 UNINDEXED，旧 SQL 的
    # `doc_id NOT IN (...)` 会全表扫 FTS 内容列。改为经 search_map（doc_id 有索引）
    # 取 rowid；只有“FTS 行数 > 映射行数”的异常存量才做一次 rowid 兜底扫描。
    with store.tx() as tx:
        fts_n = tx.execute("SELECT COUNT(*) c FROM search_blocks").fetchone()["c"]
        map_n = tx.execute("SELECT COUNT(*) c FROM search_map").fetchone()["c"]
        orphans = 0
        if fts_n > map_n:
            orphans += tx.execute(
                "DELETE FROM search_blocks WHERE rowid NOT IN"
                " (SELECT fts_rowid FROM search_map)").rowcount or 0
        # 映射侧存在“blocks 已无该 doc”的行：这已经是损坏态，允许一次
        # `doc_id IN (...)` 扫描把对应 FTS 行清干净（map 的 fts_rowid 可能
        # 与真实行错位，不能按映射 rowid 删）。
        orphan_ids = [r["doc_id"] for r in tx.execute(
            "SELECT DISTINCT doc_id FROM search_map WHERE doc_id NOT IN"
            " (SELECT doc_id FROM blocks)")]
        if orphan_ids:
            marks = ",".join("?" * len(orphan_ids))
            orphans += tx.execute(
                f"DELETE FROM search_blocks WHERE doc_id IN ({marks})",
                tuple(orphan_ids)).rowcount or 0
        # CH-476（R7-STOR-1）：映射同清，否则孤儿 doc 的 fts_rowid 永占位
        tx.execute("DELETE FROM search_map WHERE doc_id NOT IN (SELECT doc_id FROM blocks)")
    return {"docs_scanned": len(divergent), "docs_repaired": docs_repaired,
            "blocks_reindexed": blocks_reindexed, "orphans_removed": orphans}


def reindex_block(store: Store, doc_id: str, block, tx=None) -> None:
    """单块索引刷新（CH-459 / A-6，FR-208「该块索引」）。

    原实现任何单块编辑都 `reindex` 整文档——N 块文档改一个块要重写全部
    FTS 行。本函数只删插该块一行；块被删空文本时只删不插。
    CH-464：`tx=` 形参允许并入调用方事务——`patch_block` 的 blocks UPDATE
    与 FTS 刷新原为两笔提交，之间崩溃会让 blocks 与 search_blocks 永久
    不一致（搜索命中旧内容），且无启动对账恢复。
    """
    from .fts import cjk_aux

    def _do(t):
        # CH-476（R7-STOR-1）：单块删除经 search_map 唯一索引 O(1)（原
        # `WHERE doc_id=? AND block_id=?` 在 UNINDEXED 列上全库扫）。
        win = block.text[:SEARCH_INDEX_WINDOW] if block.text else ""
        _fts_replace_block(t, doc_id, block.id,
                           (win, cjk_aux(win)) if win else None)

    if tx is not None:
        _do(tx)
    else:
        with store.tx() as t:
            _do(t)


def get_doc(store: Store, doc_id: str) -> dict:
    r = store.q1("SELECT * FROM docs WHERE doc_id=?", (doc_id,))
    if not r:
        raise E4041_NotFound(f"doc {doc_id} not found")
    d = dict(r)
    d["meta"] = json.loads(d.pop("meta_json") or "{}")
    return d


def list_docs(store: Store, *, offset: int = 0, limit: int | None = None,
              status: str | None = None) -> dict:
    """文档清单（O-07 / C-26 / F-01）：CLI `docs list` 的只读面。

    返回分页信封 {"docs": [...], "total", "offset", "limit", "has_more"}——
    与 API `/jobs` 同族（CH-471 STOR-7）：`total` 为过滤后全集条数、不受窗口
    影响；`has_more` 由 `offset+len(rows) < total` 直接判定，不第二遍 COUNT。
    每行附 `blocks` 计数（相关子查询只在窗口行数上执行，编辑面判断「这篇
    有没有被改过」不必另发查询）。

    排序 `created_at DESC, doc_id`：doc_id 是唯一 tiebreaker——翻页稳定性
    不能依赖 rowid 物理序（覆盖写 / VACUUM 都会改 rowid，CH-485 起 DB 是
    产物事实源、覆盖写是常态）。

    `limit=None` 语义为「无窗口」（`--all` 导出路径用）；CLI 面自行硬顶
    条数（见 cli.DOCS_MAX_ROWS），本函数不再复制一套上限。
    """
    where, args = "", ()
    if status:
        where, args = " WHERE d.status=?", (status,)
    total_row = store.q1(f"SELECT COUNT(*) AS c FROM docs d{where}", args)
    total = int(total_row["c"]) if total_row else 0
    sql = ("SELECT d.doc_id, d.source_key, d.source_name, d.format, d.pages,"
           " d.sharded, d.shard_count, d.status, d.profile, d.created_at,"
           " d.updated_at,"
           " (SELECT COUNT(*) FROM blocks b WHERE b.doc_id=d.doc_id) AS blocks"
           f" FROM docs d{where} ORDER BY d.created_at DESC, d.doc_id"
           " LIMIT ? OFFSET ?")
    rows = [dict(r) for r in store.q(
        sql, args + (-1 if limit is None else int(limit), int(offset)))]
    for r in rows:
        r["sharded"] = bool(r["sharded"])      # 库列是 0/1 整数，出口恒布尔
    # D9：显式 limit=0 = 取空集，不存在“下一页”，has_more 不能反着报 True。
    has_more = False if limit == 0 else offset + len(rows) < total
    return {"docs": rows, "total": total, "offset": int(offset),
            "limit": limit, "has_more": has_more}


def list_blocks(store: Store, doc_id: str, *, offset: int = 0,
                limit: int | None = None):
    """块读取（CH-469：加窗口参数）。分片文档可达数千块，
    原实现无界返回 → `GET /docs/<id>` 与编辑器一次拉全量。
    `limit=None` 保持既有「取全部」契约。"""
    from ..ir import Block
    sql = "SELECT * FROM blocks WHERE doc_id=? ORDER BY rowid"
    args: tuple = (doc_id,)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += (int(limit), int(offset))
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        args += (int(offset),)
    rows = store.q(sql, args)
    out = []
    for r in rows:
        out.append(Block(id=r["block_id"], type=r["type"], text=r["text"], level=r["level"],
                         page=r["page"], flags=json.loads(r["flags_json"] or "{}"),
                         marks=json.loads(r["marks_json"] or "{}"),
                         links=json.loads(r["links_json"] or "[]"),
                         font=json.loads(r["font_json"] or "{}"),
                         provenance=json.loads(r["provenance_json"] or "{}"),
                         immutable=bool(r["immutable"]), revision=r["revision"]))
    return out


def count_blocks(store: Store, doc_id: str) -> int:
    """块总数（CH-469：分页窗口需要 total 才能判断是否还有下一页）。"""
    row = store.q("SELECT COUNT(*) AS n FROM blocks WHERE doc_id=?", (doc_id,))
    return int(row[0]["n"]) if row else 0


def search(store: Store, query: str, limit: int = 20, *,
           full: bool = False) -> dict:
    """全文检索（CH-435：CJK 走 aux n-gram 列命中，text 列 LIKE 复核连续性）。

    两段式：FTS5 负责「快速缩小候选集」，`text LIKE` 负责「字组确实连续出现」，
    避免 bigram AND 把「例外…情况」这类跨段假阳性当成命中。
    snippet 取 text 列（第 3 列），展示的是原文而不是分词碎片。

    CH-471（STOR-2）：
    - FTS 命中按 `bm25(search_blocks)` 相关性排序（原按 rowid 插入序，最相关的不靠前）；
    - 取消每次检索的第二遍全表 `COUNT(*)`：主查询多取一行（`LIMIT limit+1`）探测
      `has_more`。`total` 保留向后兼容——未截断时即精确命中数，截断时为「本页可见数」
      并以 `has_more=True` 表明还有更多。
    """
    from .fts import build_match, like_escape, query_terms

    terms = query_terms(query)
    if not terms:
        return {"total": 0, "hits": [], "has_more": False, "mode": "empty"}
    like_sql = " AND ".join(["text LIKE ? ESCAPE '\\'"] * len(terms))
    like_args = tuple(f"%{like_escape(t)}%" for t in terms)
    expr = build_match(query)
    fts_error = ""

    # CH-485（R9-SVC-4）：显式 full 模式直接扫 blocks.text，覆盖 FTS 8000 字
    # 窗口外的尾部；正常查询不触发全表 LIKE（原实现每次零命中都要扫一遍 FTS 正文）。
    if full:
        rows = store.q(
            "SELECT doc_id, block_id, substr(text, 1, 48) AS snip FROM blocks"
            f" WHERE {like_sql} LIMIT ?", (*like_args, limit + 1))
        has_more = len(rows) > limit
        hits = [dict(r) for r in rows[:limit]]
        return {"total": len(hits), "has_more": has_more, "hits": hits,
                "match": like_args[0], "mode": "full"}
    if expr:
        try:
            rows = store.q(
                "SELECT doc_id, block_id, snippet(search_blocks, 2, '[', ']', '…', 24) AS snip"
                f" FROM search_blocks WHERE search_blocks MATCH ? AND {like_sql}"
                " ORDER BY bm25(search_blocks) LIMIT ?", (expr, *like_args, limit + 1))
            if rows:
                has_more = len(rows) > limit
                hits = [dict(r) for r in rows[:limit]]
                return {"total": len(hits), "has_more": has_more, "hits": hits,
                        "match": expr, "mode": "fts"}
            # 健康 FTS 零命中就是零命中；不再为“没搜到”付出全表 LIKE。
            return {"total": 0, "has_more": False, "hits": [],
                    "match": expr, "mode": "fts"}
        except sqlite3.OperationalError as exc:
            # CH-469（V-9）：MATCH 表达式不被接受 → 可以降级 LIKE，但**原因必须留痕**
            # （响应带 fts_error），否则「索引面坏了」与「n-gram 没命中」不可区分。
            fts_error = type(exc).__name__
        except sqlite3.Error as exc:
            # 库面故障（磁盘满 / 索引损坏 / busy 超时）：原实现 `except Exception: pass`
            # 把它一并吞成 LIKE 兜底，最坏处置——调用方看到的只是「没搜到几条」，
            # 而真因是搜索面已经不可用。明确报错（detail 只带异常类名，NFR-20）。
            raise E5031_StorageError("search index query failed",
                                     detail={"reason": type(exc).__name__}) from exc
    # 兜底：FTS 表达式非法时用 blocks.text 的 LIKE（不再只查 8000 字索引窗口）。
    # 这是错误路径，不是每次零命中的常规路径。
    rows = store.q(
        f"SELECT doc_id, block_id, substr(text, 1, 48) AS snip FROM blocks"
        f" WHERE {like_sql} LIMIT ?", (*like_args, limit + 1))
    has_more = len(rows) > limit
    hits = [dict(r) for r in rows[:limit]]
    out = {"total": len(hits), "has_more": has_more, "hits": hits,
           "match": expr or like_args[0], "mode": "like"}
    if fts_error:
        out["fts_error"] = fts_error
    return out


def delete_doc(store: Store, doc_id: str, actor: str, root: Path | None = None, *,
               files_removed: tuple[list[str], list[str]] | None = None,
               out_dir: Path | None = None, tmp_dir: Path | None = None) -> dict:
    """FR-128 级联删除：out 产物、IR/中间产物、事件、revision、编辑审计；
    删除留痕本身保留（who/when/doc_id，不含正文，NFR-20）。

    CH-440：先删产物文件再删库行。反序时若文件删除失败，库行已提交消失，
    该文档的 zip 副本就成了不可寻址的残留正文（GET 已 404，API 层无法再触发重试）。
    产物未删净即 fail-closed：抛 E5031 并保留库行，删除可安全重试。

    CH-459（A-15）：删前存在性检查——原实现对不存在的 doc_id 也留痕并返回
    200，「删了个不存在的东西」与「删成功了」在响应上不可区分。

    `files_removed`（CH-469，V-6）：整源删除已在上层一次遍历清完产物时传入，
    本层跳过逐文档 rglob（见 `delete_source`）。
    """
    if store.q1("SELECT 1 FROM docs WHERE doc_id=?", (doc_id,)) is None:
        raise E4041_NotFound(f"doc {doc_id} not found")
    if files_removed is not None:
        removed, failed = files_removed
    elif out_dir is None and tmp_dir is None:
        removed, failed = purge_files(doc_id, root)
    else:
        removed, failed = purge_files(doc_id, root, out_dir=out_dir, tmp_dir=tmp_dir)
    if failed:
        raise E5031_StorageError(
            "artifact removal incomplete; rows kept for retry",
            detail={"doc_id": doc_id, "failed": len(failed), "removed": len(removed)})
    with store.tx() as tx:
        src = tx.execute("SELECT source_key FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
        if src is None:
            # CH-471（STOR-10）：写锁下权威复核——上面那次快检在写事务之外，它与此刻之间
            # 该 doc 若被并发删除（cron worker 与 API 同删一份），本次会「删 0 行却仍写一条
            # deletions 留痕」，把「删了个不存在的东西」伪装成成功（正是 A-15 要根除的语义，
            # 只是被 TOCTOU 绕过）。BEGIN IMMEDIATE 拿写锁后再查，check-and-delete 原子：
            # 不存在即 404 回滚，绝不落伪造留痕。
            raise E4041_NotFound(f"doc {doc_id} not found")
        counts = {
            "blocks": tx.execute("SELECT COUNT(*) c FROM blocks WHERE doc_id=?",
                                 (doc_id,)).fetchone()["c"],
            "audit": tx.execute("SELECT COUNT(*) c FROM edit_audit WHERE doc_id=?",
                                (doc_id,)).fetchone()["c"],
            "events": tx.execute("SELECT COUNT(*) c FROM events WHERE doc_id=?",
                                 (doc_id,)).fetchone()["c"],
        }
        # CH-476（R7-STOR-1）：FTS 删除经 search_map rowid 映射（O(本文档块数)），
        # 原 `WHERE doc_id=?` 是全库线性扫描且同在写锁事务内。
        tx.execute("DELETE FROM search_blocks WHERE rowid IN"
                   " (SELECT fts_rowid FROM search_map WHERE doc_id=?)", (doc_id,))
        tx.execute("DELETE FROM search_map WHERE doc_id=?", (doc_id,))
        for sql in ("DELETE FROM blocks WHERE doc_id=?",
                    "DELETE FROM edit_audit WHERE doc_id=?",
                    "DELETE FROM events WHERE doc_id=?",
                    "DELETE FROM tasks WHERE doc_id=?",
                    "DELETE FROM docs WHERE doc_id=?"):
            tx.execute(sql, (doc_id,))
        # 留痕只记数量与文件名指纹，不记正文与完整路径（NFR-20）
        cascade = {"deleted": counts, "files": len(removed)}
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES (?,?,?,?,?,?)",
                   (doc_id, src["source_key"], actor, now_iso(), "doc",
                    json.dumps(cascade, ensure_ascii=False)))
    # 提交后再清一次：转换/重建在第一次 purge 与本次提交之间可能刚发布
    # out/{doc_id}.zip。此时库行已不存在，孤儿产物不会再被任何路径引用；
    # 第二次定向删除把窗口收窄到「提交后仍并发写」，失败也不翻转删除结果。
    if files_removed is None:
        purge_files(doc_id, root, out_dir=out_dir, tmp_dir=tmp_dir)
    return {"doc_id": doc_id, "deleted": counts, "files": removed}



def delete_source(store: Store, source_key: str, actor: str, root: Path | None = None, *,
                  out_dir: Path | None = None, tmp_dir: Path | None = None) -> dict:
    """整源删除（FR-128：同一来源所有版本）。

    CH-459（A-15）：无匹配文档时 404/E4041——原实现写一条空 source 留痕并
    返回 200，与 delete_doc 的「不存在也成功」同病。

    CH-469（V-6）：产物清理改为**整源一次遍历**。原实现逐文档调 `purge_files`，
    每个文档都把 out/ir/tmp 三棵树各 rglob 一遍——N 个版本 = 3N 次全树扫描，
    目录里文档越多越慢（同一批文件被反复匹配）。
    """
    # CH-476（R7-PERF-i）：只读查询走 `store.q`——原实现用 `store.tx()` 起了一个
    # BEGIN IMMEDIATE + COMMIT（拿写锁、写 journal header），只为跑一条 SELECT。
    ids = [r["doc_id"] for r in
           store.q("SELECT doc_id FROM docs WHERE source_key=?", (source_key,))]
    if not ids:
        raise E4041_NotFound(f"source {source_key} not found")
    if out_dir is None and tmp_dir is None:
        removed, failed = purge_many(ids, root)
    else:
        removed, failed = purge_many(ids, root, out_dir=out_dir, tmp_dir=tmp_dir)
    if failed:
        # 与 delete_doc 同口径 fail-closed：产物没删净就保留全部库行，整源删除可重试
        raise E5031_StorageError(
            "artifact removal incomplete; rows kept for retry",
            detail={"source_key": source_key, "failed": len(failed),
                    "removed": len(removed)})
    # CH-493（P-4）：产物按 doc 归因。原实现内层 `Path(p).name` 对每个 doc_id
    # 都重建一遍 → O(D×F) 次路径构造（D=100 / F=300 时 3 万次，约 30ms 纯开销）。
    # 改为先构造一次 name，再按 removed 原序一次扫描填充，逐 doc 的列表顺序不变。
    by_doc: dict[str, list] = {d: [] for d in ids}
    for p in removed:
        nm = Path(p).name
        for d in ids:
            if d in nm:
                by_doc[d].append(p)
    for d in ids:
        delete_doc(store, d, actor, None,
                   files_removed=(by_doc.get(d, []), []),
                   out_dir=out_dir, tmp_dir=tmp_dir)
    with store.tx() as tx:
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES (?,?,?,?,?,?)",
                   (f"source:{source_key}", source_key, actor, now_iso(), "source",
                    json.dumps({"docs": len(ids), "files": len(removed)},
                               ensure_ascii=False)))
    # 同 delete_doc：提交后补一次定向清扫，收窄「purge → 提交」窗口内新发布的产物。
    purge_many(ids, root, out_dir=out_dir, tmp_dir=tmp_dir)
    return {"source_key": source_key, "docs": ids, "files": removed}


def purge_files(doc_id: str, root: Path | None, *,
                out_dir: Path | None = None,
                tmp_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """删除该文档的产物与中间产物，返回 (已删, 失败)。"""
    return _purge([doc_id], root, out_dir=out_dir, tmp_dir=tmp_dir)


def purge_many(doc_ids, root: Path | None, *,
               out_dir: Path | None = None,
               tmp_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """多文档共一次删除（CH-469，V-6）。"""
    return _purge([d for d in doc_ids if d], root,
                  out_dir=out_dir, tmp_dir=tmp_dir)


def _purge(ids: list[str], root: Path | None, *,
           out_dir: Path | None = None,
           tmp_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """按“已知产物路径”删除，不再对 out/ir 全树 rglob。

    CH-483（R8-PERF-2）：
    - `paths.out` / `paths.tmp` 可配置，旧实现硬编码 root/out|tmp → 自定义路径
      的产物永远删不掉；
    - 单文档删除原要对三棵树各做一次 rglob，成本随目录规模线性增长；
      现在 out/ir 直接拼 `{doc_id}.zip` / `{doc_id}.json`（含 `.part`），
      tmp 只扫顶层（中间产物不在深层目录）。
    - 文件名匹配改为精确前缀（`doc_id` + "."），杜绝子串误删。
    """
    removed: list[str] = []
    failed: list[str] = []
    if not root or not ids:
        return removed, failed
    out_base = Path(out_dir) if out_dir else Path(root) / "out"
    tmp_base = Path(tmp_dir) if tmp_dir else Path(root) / "tmp"
    ir_base = Path(root) / "ir"
    seen: set[Path] = set()

    def _kill(p: Path) -> None:
        if p in seen:
            return
        seen.add(p)
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(str(p))
        except OSError:
            failed.append(str(p))

    id_set = set(ids)
    for d in ids:
        for base, exts in ((out_base, (".zip", ".zip.part")),
                           (ir_base, (".json", ".json.part"))):
            for ext in exts:
                p = base / f"{d}{ext}"
                if p.exists():
                    _kill(p)
    # 旧版 / 测试可能留下 `{doc_id}.*` 的变体；out/ir 只扫一层，
    # 且整批只扫一次（旧实现 rglob 全树 × 每个文档）。
    for base in (out_base, ir_base):
        if not base.exists():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            entries = []
        for p in entries:
            if p.name.split(".", 1)[0] in id_set:
                _kill(p)
    if tmp_base.exists():
        try:
            entries = list(tmp_base.iterdir())
        except OSError:
            entries = []
        for p in entries:
            if any(p.name.startswith(f"{d}.") or p.name == d for d in ids):
                _kill(p)
    return removed, failed
