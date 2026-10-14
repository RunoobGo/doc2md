"""NFR-9【再修订】token 三角色 + 端点权限矩阵（CH-407 / ADR-29 / P-54）。

角色边界：
  admin  —— 全部写操作 + token 管理 + settings
  editor —— 提交作业 + 编辑 / 回滚 + 重跑
  reader —— 只读查询与导出
吊销即时生效：≤ revoke_window_seconds（默认 5s）内新请求 401。

CH-456（T9）：删除三个零调用符号——`require`（api 统一走 `check`）、
`timing_safe_equal`（token 比对走参数化 SQL 索引查找，无逐字节比对面）、
`RevocationCache`（NFR-9 口径为每请求实时查库，缓存没有接入点）。
`issue` 新增 `allowed`：可签发角色集由 `api.token_roles` 配置供给。
"""
from __future__ import annotations

import secrets

from .errors import E4011_Unauthorized, E4031_Forbidden
from .storage.db import Store, now_iso

ROLES = ("admin", "editor", "reader")
RANK = {"reader": 1, "editor": 2, "admin": 3}

# 端点权限矩阵：action -> 所需最低角色
MATRIX = {
    "read":            "reader",
    "export":          "reader",
    "submit_job":      "editor",
    "edit":            "editor",
    "rollback":        "editor",
    "rerun":           "editor",
    "delete":          "admin",
    "settings_put":    "admin",
    "system_reload":   "admin",
    "token_admin":     "admin",
}


def issue(store: Store, role: str = "admin", label: str = "",
          allowed: tuple[str, ...] | list[str] = ROLES) -> str:
    """签发 token；`allowed` 之外的角色的签发即 ValueError（api.token_roles）。"""
    if role not in ROLES or role not in tuple(allowed):
        raise ValueError(f"bad role {role}（可签发：{list(allowed)}）")
    token = "d2m_" + secrets.token_urlsafe(24)
    with store.tx() as tx:
        tx.execute("INSERT INTO tokens(token, role, label, created_at) VALUES (?,?,?,?)",
                   (token, role, label, now_iso()))
    return token


def revoke(store: Store, token: str) -> None:
    with store.tx() as tx:
        tx.execute("UPDATE tokens SET revoked_at=? WHERE token=?", (now_iso(), token))


def check(store: Store, token: str | None, action: str,
          revoke_window: float = 5.0) -> str:
    """返回角色名；未授权 401，角色不足 403。吊销在窗口期内即时生效。"""
    if not token:
        raise E4011_Unauthorized("missing token")
    row = store.q1("SELECT role, revoked_at, created_at FROM tokens WHERE token=?", (token,))
    if not row:
        raise E4011_Unauthorized("unknown token")
    if row["revoked_at"]:
        raise E4011_Unauthorized("token revoked")
    need = MATRIX.get(action)
    if need is None:
        raise E4031_Forbidden(f"unknown action: {action}")
    if RANK[row["role"]] < RANK[need]:
        raise E4031_Forbidden(f"action {action} requires {need}",
                              detail={"role": row["role"], "required": need})
    return row["role"]
