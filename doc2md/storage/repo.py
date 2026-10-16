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



def save_ir(store: Store, ir: IR, profile: str = "standard", tx=None,
            force: bool = False) -> None:
    """整本文档入库（docs + blocks + FTS）。

    CH-459（A-4/A-14）：原实现 docs/blocks 与 reindex 各起一次事务、逐块
    `execute`——N 块 = 2 次写事务 + 2N 条语句，而 WAL + synchronous=FULL 下
    每次提交都 fsync，这是每任务固定开销的主因（12.3 实测佐证）。现合并为
    **单事务 + executemany**（两次语句批量）。

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
    from .fts import cjk_aux
    ts = now_iso()

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
            " meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ir.doc_id, ir.source_key, ir.source_name, ir.source_name_raw, ir.format,
             ir.pages, int(ir.sharded), ir.shard_count, "done", profile, created_at, ts,
             json.dumps(ir.meta(), ensure_ascii=False)))
        t.execute("DELETE FROM blocks WHERE doc_id=?", (ir.doc_id,))
        t.executemany(
            "INSERT INTO blocks(doc_id, block_id, type, text, level, page,"
            " flags_json, marks_json, immutable, revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(ir.doc_id, b.id, b.type, b.text, b.level, b.page,
              json.dumps(b.flags, ensure_ascii=False), json.dumps(b.marks, ensure_ascii=False),
              int(b.immutable), b.revision) for b in ir.blocks])
        t.execute("DELETE FROM search_blocks WHERE doc_id=?", (ir.doc_id,))
        t.executemany(
            "INSERT INTO search_blocks(doc_id, block_id, text, aux) VALUES (?,?,?,?)",
            [(ir.doc_id, b.id, b.text[:SEARCH_INDEX_WINDOW],
              cjk_aux(b.text[:SEARCH_INDEX_WINDOW]))
             for b in ir.blocks if b.text])
        if existed:
            # 覆盖留痕只记计数与来源，不记正文（NFR-20）
            t.execute("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
                      " VALUES (?,?,?,?,?)",
                      (ts, "doc_overwritten", ir.doc_id, None,
                       json.dumps({"overwritten_blocks": prev_blocks,
                                   "overwritten_edits": edited,
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
        tx.execute("DELETE FROM search_blocks WHERE doc_id=?", (doc_id,))
        tx.executemany(
            "INSERT INTO search_blocks(doc_id, block_id, text, aux) VALUES (?,?,?,?)",
            [(doc_id, b.id, b.text[:SEARCH_INDEX_WINDOW],
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
        " LEFT JOIN (SELECT doc_id, COUNT(*) ns FROM search_blocks GROUP BY doc_id) s"
        " ON s.doc_id=b.doc_id"
        " WHERE b.nb <> COALESCE(s.ns, 0)")
    docs_repaired = blocks_reindexed = 0
    for row in divergent:
        doc_id = row["doc_id"]
        blocks_reindexed += reindex(store, doc_id, list_blocks(store, doc_id))
        docs_repaired += 1
    # 孤儿索引行：blocks 侧已无该 doc（删除级联之外的历史残留），清掉避免命中不存在的块
    with store.tx() as tx:
        orphans = tx.execute(
            "DELETE FROM search_blocks WHERE doc_id NOT IN (SELECT doc_id FROM blocks)"
        ).rowcount or 0
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
        t.execute("DELETE FROM search_blocks WHERE doc_id=? AND block_id=?",
                  (doc_id, block.id))
        if block.text:
            t.execute(
                "INSERT INTO search_blocks(doc_id, block_id, text, aux) VALUES (?,?,?,?)",
                (doc_id, block.id, block.text[:SEARCH_INDEX_WINDOW],
                 cjk_aux(block.text[:SEARCH_INDEX_WINDOW])))

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
                         immutable=bool(r["immutable"]), revision=r["revision"]))
    return out


def count_blocks(store: Store, doc_id: str) -> int:
    """块总数（CH-469：分页窗口需要 total 才能判断是否还有下一页）。"""
    row = store.q("SELECT COUNT(*) AS n FROM blocks WHERE doc_id=?", (doc_id,))
    return int(row[0]["n"]) if row else 0


def search(store: Store, query: str, limit: int = 20) -> dict:
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
    # 兜底：纯 LIKE 子串（FTS 表达式非法 / n-gram 未命中，如旧库尚未回填 aux）。
    # LIKE 路径无 MATCH 上下文，bm25 不适用，按插入序返回（同样 LIMIT+1 探测，免全表 COUNT）。
    rows = store.q(
        f"SELECT doc_id, block_id, substr(text, 1, 48) AS snip FROM search_blocks"
        f" WHERE {like_sql} LIMIT ?", (*like_args, limit + 1))
    has_more = len(rows) > limit
    hits = [dict(r) for r in rows[:limit]]
    out = {"total": len(hits), "has_more": has_more, "hits": hits,
           "match": expr or like_args[0], "mode": "like"}
    if fts_error:
        out["fts_error"] = fts_error
    return out


def delete_doc(store: Store, doc_id: str, actor: str, root: Path | None = None, *,
               files_removed: tuple[list[str], list[str]] | None = None) -> dict:
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
    removed, failed = files_removed if files_removed is not None else purge_files(doc_id, root)
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
        for sql in ("DELETE FROM search_blocks WHERE doc_id=?",
                    "DELETE FROM blocks WHERE doc_id=?",
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
    return {"doc_id": doc_id, "deleted": counts, "files": removed}



def delete_source(store: Store, source_key: str, actor: str, root: Path | None = None) -> dict:
    """整源删除（FR-128：同一来源所有版本）。

    CH-459（A-15）：无匹配文档时 404/E4041——原实现写一条空 source 留痕并
    返回 200，与 delete_doc 的「不存在也成功」同病。

    CH-469（V-6）：产物清理改为**整源一次遍历**。原实现逐文档调 `purge_files`，
    每个文档都把 out/ir/tmp 三棵树各 rglob 一遍——N 个版本 = 3N 次全树扫描，
    目录里文档越多越慢（同一批文件被反复匹配）。
    """
    with store.tx() as tx:
        ids = [r["doc_id"] for r in
               tx.execute("SELECT doc_id FROM docs WHERE source_key=?", (source_key,))]
    if not ids:
        raise E4041_NotFound(f"source {source_key} not found")
    removed, failed = purge_many(ids, root)
    if failed:
        # 与 delete_doc 同口径 fail-closed：产物没删净就保留全部库行，整源删除可重试
        raise E5031_StorageError(
            "artifact removal incomplete; rows kept for retry",
            detail={"source_key": source_key, "failed": len(failed),
                    "removed": len(removed)})
    by_doc = {d: [p for p in removed if d in Path(p).name] for d in ids}
    for d in ids:
        delete_doc(store, d, actor, None, files_removed=(by_doc.get(d, []), []))
    with store.tx() as tx:
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES (?,?,?,?,?,?)",
                   (f"source:{source_key}", source_key, actor, now_iso(), "source",
                    json.dumps({"docs": len(ids), "files": len(removed)},
                               ensure_ascii=False)))
    return {"source_key": source_key, "docs": ids, "files": removed}


def purge_files(doc_id: str, root: Path | None) -> tuple[list[str], list[str]]:
    """删除 out/ 与 work/ 下该文档的产物与中间产物，返回 (已删, 失败)。"""
    return _purge([doc_id], root)


def purge_many(doc_ids, root: Path | None) -> tuple[list[str], list[str]]:
    """多文档共一次目录遍历（CH-469，V-6）。"""
    return _purge([d for d in doc_ids if d], root)


def _purge(ids: list[str], root: Path | None) -> tuple[list[str], list[str]]:
    """out/ir/tmp 三棵树各一次遍历，命中任一 doc_id 即删（一次遍历服务整批）。"""
    removed: list[str] = []
    failed: list[str] = []
    if not root or not ids:
        return removed, failed
    for base in (Path(root) / "out", Path(root) / "ir", Path(root) / "tmp"):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not any(d in p.name for d in ids):
                continue
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed.append(str(p))
            except OSError:
                failed.append(str(p))
    return removed, failed
