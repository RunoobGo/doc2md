"""NFR-9【再修订】token 三角色 + 端点权限矩阵（CH-407 / ADR-29 / P-54）。

角色边界：
  admin  —— 全部写操作 + token 管理 + settings
  editor —— 提交作业 + 编辑 / 回滚 + 重跑
  reader —— 只读查询与导出
吊销即时生效：≤ revoke_window_seconds（默认 5s）内新请求 401。
"""
from __future__ import annotations

import hmac
import secrets
import time

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


def issue(store: Store, role: str = "admin", label: str = "") -> str:
    if role not in ROLES:
        raise ValueError(f"bad role {role}")
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


def require(store: Store, token: str | None, action: str) -> str:
    return check(store, token, action)


def timing_safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


class RevocationCache:
    """短 TTL 校验缓存：吊销即时生效窗口内不得放行旧 token（NFR-9 验收）。"""

    def __init__(self, ttl: float = 1.0):
        self.ttl = ttl
        self._ok: dict[str, float] = {}

    def allowed(self, token: str, valid: bool) -> bool:
        now = time.monotonic()
        if not valid:
            self._ok.pop(token, None)
            return False
        exp = self._ok.get(token, 0)
        if exp < now:
            self._ok[token] = now + self.ttl
        return True

    def invalidate(self, token: str) -> None:
        self._ok.pop(token, None)
