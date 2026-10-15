"""仓储层：docs / blocks / audit / search / 删除级联。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..errors import E4041_NotFound, E5031_StorageError
from ..ir import IR
from .db import Store, now_iso


def save_ir(store: Store, ir: IR, profile: str = "standard", tx=None) -> None:
    """整本文档入库（docs + blocks + FTS）。

    CH-459（A-4/A-14）：原实现 docs/blocks 与 reindex 各起一次事务、逐块
    `execute`——N 块 = 2 次写事务 + 2N 条语句，而 WAL + synchronous=FULL 下
    每次提交都 fsync，这是每任务固定开销的主因（12.3 实测佐证）。现合并为
    **单事务 + executemany**（两次语句批量）。

    `tx` 形参（CH-459）：调用方可传入已有事务把入库并入其中——
    `worker._finish_ok` 用它把 detail/mark/event/入库四写合成一次提交。
    """
    from .fts import cjk_aux
    ts = now_iso()

    def _do(t):
        t.execute(
            "INSERT OR REPLACE INTO docs(doc_id, source_key, source_name, source_name_raw,"
            " format, pages, sharded, shard_count, status, profile, created_at, updated_at,"
            " meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ir.doc_id, ir.source_key, ir.source_name, ir.source_name_raw, ir.format,
             ir.pages, int(ir.sharded), ir.shard_count, "done", profile, ts, ts,
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
            [(ir.doc_id, b.id, b.text[:8000], cjk_aux(b.text[:8000]))
             for b in ir.blocks if b.text])

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
            [(doc_id, b.id, b.text[:8000], cjk_aux(b.text[:8000]))
             for b in blocks if b.text])
    return len(blocks)


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
                (doc_id, block.id, block.text[:8000], cjk_aux(block.text[:8000])))

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


def list_blocks(store: Store, doc_id: str):
    from ..ir import Block
    rows = store.q("SELECT * FROM blocks WHERE doc_id=? ORDER BY rowid", (doc_id,))
    out = []
    for r in rows:
        out.append(Block(id=r["block_id"], type=r["type"], text=r["text"], level=r["level"],
                         page=r["page"], flags=json.loads(r["flags_json"] or "{}"),
                         marks=json.loads(r["marks_json"] or "{}"),
                         immutable=bool(r["immutable"]), revision=r["revision"]))
    return out


def search(store: Store, query: str, limit: int = 20) -> dict:
    """全文检索（CH-435：CJK 走 aux n-gram 列命中，text 列 LIKE 复核连续性）。

    两段式：FTS5 负责「快速缩小候选集」，`text LIKE` 负责「字组确实连续出现」，
    避免 bigram AND 把「例外…情况」这类跨段假阳性当成命中。
    snippet 取 text 列（第 3 列），展示的是原文而不是分词碎片。
    """
    from .fts import build_match, like_escape, query_terms

    terms = query_terms(query)
    if not terms:
        return {"total": 0, "hits": [], "mode": "empty"}
    like_sql = " AND ".join(["text LIKE ? ESCAPE '\\'"] * len(terms))
    like_args = tuple(f"%{like_escape(t)}%" for t in terms)
    expr = build_match(query)

    if expr:
        try:
            rows = store.q(
                "SELECT doc_id, block_id, snippet(search_blocks, 2, '[', ']', '…', 24) AS snip"
                f" FROM search_blocks WHERE search_blocks MATCH ? AND {like_sql}"
                " LIMIT ?", (expr, *like_args, limit))
            total = store.q1(
                f"SELECT COUNT(*) c FROM search_blocks WHERE search_blocks MATCH ? AND {like_sql}",
                (expr, *like_args))["c"]
            if total:
                return {"total": total, "hits": [dict(r) for r in rows],
                        "match": expr, "mode": "fts"}
        except Exception:                            # noqa: BLE001 — FTS 语法兜底
            pass
    # 兜底：纯 LIKE 子串（FTS 表达式非法 / n-gram 未命中，如旧库尚未回填 aux）
    rows = store.q(
        f"SELECT doc_id, block_id, substr(text, 1, 48) AS snip FROM search_blocks"
        f" WHERE {like_sql} LIMIT ?", (*like_args, limit))
    total = store.q1(
        f"SELECT COUNT(*) c FROM search_blocks WHERE {like_sql}", like_args)["c"]
    return {"total": total, "hits": [dict(r) for r in rows],
            "match": expr or like_args[0], "mode": "like"}


def delete_doc(store: Store, doc_id: str, actor: str, root: Path | None = None) -> dict:
    """FR-128 级联删除：out 产物、IR/中间产物、事件、revision、编辑审计；
    删除留痕本身保留（who/when/doc_id，不含正文，NFR-20）。

    CH-440：先删产物文件再删库行。反序时若文件删除失败，库行已提交消失，
    该文档的 zip 副本就成了不可寻址的残留正文（GET 已 404，API 层无法再触发重试）。
    产物未删净即 fail-closed：抛 E5031 并保留库行，删除可安全重试。

    CH-459（A-15）：删前存在性检查——原实现对不存在的 doc_id 也留痕并返回
    200，「删了个不存在的东西」与「删成功了」在响应上不可区分。
    """
    if store.q1("SELECT 1 FROM docs WHERE doc_id=?", (doc_id,)) is None:
        raise E4041_NotFound(f"doc {doc_id} not found")
    removed, failed = purge_files(doc_id, root)
    if failed:
        raise E5031_StorageError(
            "artifact removal incomplete; rows kept for retry",
            detail={"doc_id": doc_id, "failed": len(failed), "removed": len(removed)})
    with store.tx() as tx:
        src = tx.execute("SELECT source_key FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
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
                   (doc_id, src["source_key"] if src else "", actor, now_iso(), "doc",
                    json.dumps(cascade, ensure_ascii=False)))
    return {"doc_id": doc_id, "deleted": counts, "files": removed}


def delete_source(store: Store, source_key: str, actor: str, root: Path | None = None) -> dict:
    """整源删除（FR-128：同一来源所有版本）。

    CH-459（A-15）：无匹配文档时 404/E4041——原实现写一条空 source 留痕并
    返回 200，与 delete_doc 的「不存在也成功」同病。
    """
    with store.tx() as tx:
        ids = [r["doc_id"] for r in
               tx.execute("SELECT doc_id FROM docs WHERE source_key=?", (source_key,))]
    if not ids:
        raise E4041_NotFound(f"source {source_key} not found")
    for d in ids:
        delete_doc(store, d, actor, root)
    with store.tx() as tx:
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES (?,?,?,?,?,?)",
                   (f"source:{source_key}", source_key, actor, now_iso(), "source",
                    json.dumps({"docs": len(ids)}, ensure_ascii=False)))
    return {"source_key": source_key, "docs": ids}


def purge_files(doc_id: str, root: Path | None) -> tuple[list[str], list[str]]:
    """删除 out/ 与 work/ 下的产物与中间产物，返回 (已删, 失败)。"""
    removed: list[str] = []
    failed: list[str] = []
    if not root:
        return removed, failed
    for base in (Path(root) / "out", Path(root) / "ir", Path(root) / "tmp"):
        if not base.exists():
            continue
        for p in base.rglob(f"*{doc_id}*"):
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed.append(str(p))
            except OSError:
                failed.append(str(p))
    return removed, failed
