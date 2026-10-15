"""FR-203 / PATCH【修订】编辑乐观并发契约（ADR-28 / CH-409，上游蓝本 B-13）。

要点：
① base_revision 必填，缺失 → 400 E4001；
② 不一致 → 425 / E4251 并附 current_revision 供前端自愈；
③ UPDATE ... WHERE revision=? 为数据库层第二道保险；
④ 审计 before 取自库里当前值，与 from_revision 严格对应；
⑤ 提交后触发搜索索引刷新（CH-418，≤5s）。

方案 8.5 灰度：require_base_revision=False 时为 optional 阶段（缺省沿用旧行为并告警）。
"""
from __future__ import annotations

from .errors import E4001_BadRequest, E4041_NotFound, E4251_RevisionConflict
from .ir import Block as _Block
from .storage.db import Store, now_iso
from .storage.repo import reindex_block


def patch_block(store: Store, doc_id: str, block_id: str, body: dict, actor: str,
                *, require_base_revision: bool = True) -> dict:
    base = body.get("base_revision")
    if base is None and require_base_revision:
        raise E4001_BadRequest("base_revision is required")
    if base is not None:
        # CH-469（V-8）：`base_revision` 非数字（JSON 字符串 "abc" / 对象 / 数组）
        # 原实现在下面的 int() 直转 → ValueError 不在 Doc2mdError 谱系里，
        # 逃逸成 500 + HTML，客户端既拿不到 {code,message} 也学不到正确写法。
        # 数字字符串仍是合法输入（不收紧契约）。
        try:
            base = int(base)
        except (TypeError, ValueError):
            raise E4001_BadRequest("base_revision must be an integer",
                                   detail={"field": "base_revision"}) from None

    with store.tx() as tx:
        row = tx.execute(
            "SELECT revision, text, immutable FROM blocks WHERE doc_id=? AND block_id=?",
            (doc_id, block_id)).fetchone()
        if row is None:
            raise E4041_NotFound("block not found")
        current_rev, old_text, immutable = row["revision"], row["text"], row["immutable"]
        if immutable:
            raise E4001_BadRequest("block is immutable", detail={"block_id": block_id})
        if base is None:                       # 灰度 optional 阶段：沿用旧行为 + 告警
            base = current_rev
            warning = "base_revision_missing_optimistic_lock_bypassed"
        else:
            warning = ""
            if int(base) != int(current_rev):
                raise E4251_RevisionConflict(
                    "revision conflict", detail={"current_revision": int(current_rev)})
        tx.execute(
            "UPDATE blocks SET text=?, revision=revision+1, updated_by=?, updated_at=?"
            " WHERE doc_id=? AND block_id=? AND revision=?",
            (body.get("text", ""), actor, now_iso(), doc_id, block_id, int(base)))
        if tx.rowcount != 1:
            raise E4251_RevisionConflict("revision conflict")
        tx.execute(
            "INSERT INTO edit_audit(doc_id, block_id, before, after, from_revision,"
            " to_revision, actor, ts, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, block_id, old_text, body.get("text", ""), int(base), int(base) + 1,
             actor, now_iso(), body.get("reason", "")))
        # CH-459（A-6 / FR-208「该块索引」）：只删插该块一行。
        # CH-464：并入同一事务（原为两笔提交，之间崩溃 → blocks 与
        # search_blocks 永久不一致且无恢复路径）。
        reindex_block(store, doc_id, _Block(id=block_id, text=body.get("text", "")),
                      tx=tx)
    out = {"revision": int(base) + 1, "block_id": block_id}
    if warning:
        out["warning"] = warning
    return out


def rollback(store: Store, doc_id: str, block_id: str, to_revision: int,
             actor: str) -> dict:
    """回滚承诺的前提：审计 before/after 与 revision 一一对应，回滚同样走乐观锁。"""
    # 「回到 revision N 的内容」优先取 from_revision==N 那次的 before；
    # 初始态（首个 audit 的 from_revision）与库内 revision=1 无 audit 时退到 to_revision==N。
    row = store.q1(
        "SELECT before AS text FROM edit_audit WHERE doc_id=? AND block_id=?"
        " AND from_revision=? ORDER BY id ASC LIMIT 1", (doc_id, block_id, to_revision))
    if not row:
        row = store.q1(
            "SELECT after AS text FROM edit_audit WHERE doc_id=? AND block_id=?"
            " AND to_revision=? ORDER BY id DESC LIMIT 1", (doc_id, block_id, to_revision))
    if not row:
        raise E4041_NotFound("no audit for that revision")
    cur = store.q1("SELECT revision FROM blocks WHERE doc_id=? AND block_id=?",
                   (doc_id, block_id))
    if cur is None:
        raise E4041_NotFound("block not found")
    return patch_block(store, doc_id, block_id,
                       {"text": row["text"], "base_revision": int(cur["revision"]),
                        "reason": f"rollback_to_{to_revision}"}, actor)
