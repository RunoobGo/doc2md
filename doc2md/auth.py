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
from .storage.db import Store, now_iso, token_hash

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
    """签发 token；`allowed` 之外的角色的签发即 ValueError（api.token_roles）。

    CH-471 SVC-9：明文 token 只在此返回一次（给调用方展示），库里落 `token_hash(token)`。
    """
    if role not in ROLES or role not in tuple(allowed):
        raise ValueError(f"bad role {role}（可签发：{list(allowed)}）")
    token = "d2m_" + secrets.token_urlsafe(24)
    with store.tx() as tx:
        tx.execute("INSERT INTO tokens(token, role, label, created_at) VALUES (?,?,?,?)",
                   (token_hash(token), role, label, now_iso()))
    return token


def revoke(store: Store, token: str) -> bool:
    """吊销 token。返回是否命中（CH-469，V-13）。

    原实现返回 None 且不看影响行数：调用方对「吊销了个不存在的 token」与
    「吊销成功」给出完全相同的响应，运维面上无法确认操作真的生效。
    CH-471 SVC-9：调用方传明文（CLI --revoke 手输），入库按键哈希匹配。
    """
    with store.tx() as tx:
        tx.execute("UPDATE tokens SET revoked_at=? WHERE token=?",
                   (now_iso(), token_hash(token)))
        return tx.rowcount > 0


def check(store: Store, token: str | None, action: str,
          revoke_window: float = 5.0) -> str:
    """返回角色名；未授权 401，角色不足 403。吊销**即时生效**（CH-473 / R5-SVC-9 澄清：
    `revoke_window` 形参自引入起即无使用方——吊销写 `revoked_at` 后下次 check 即拒，
    没有传播窗口。保留形参只为不破坏调用方签名；文档中「≤5s 生效」的旧口径偏保守，
 以本实现为准：即时）。"""
    if not token:
        raise E4011_Unauthorized("missing token")
    row = store.q1("SELECT role, revoked_at, created_at FROM tokens WHERE token=?",
                   (token_hash(token),))
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
