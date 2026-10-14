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
from .storage.db import Store, now_iso
from .storage.repo import list_blocks, reindex


def patch_block(store: Store, doc_id: str, block_id: str, body: dict, actor: str,
                *, require_base_revision: bool = True) -> dict:
    base = body.get("base_revision")
    if base is None and require_base_revision:
        raise E4001_BadRequest("base_revision is required")

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

    # 索引刷新必须在事务外：reindex 自带 BEGIN IMMEDIATE，嵌套会失败
    reindex(store, doc_id, list_blocks(store, doc_id))
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
