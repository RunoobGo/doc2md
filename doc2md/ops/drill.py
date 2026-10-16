"""备份恢复演练（DoD 第 11 条脚本化，CH-456 / T7）。

一条命令跑完闭环：**快照 → 外置加密 → 模拟数据丢失 → 轮出确认 → 解密恢复 →
integrity_check → 内容比对**。全程在 `work/tmp/drill-<ts>/` 隔离沙箱内进行，
不触碰线上 `state.sqlite`（只读拷贝），演练结束即清理（`--keep` 可留存取证）。

输出纪律（NFR-20）：报告只含表名、行数与指纹前缀，不含任何正文片段。
口令只从 `DOC2MD_BACKUP_PASSPHRASE` 环境变量读取，不入库、不进日志。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import E4001_BadRequest
from .backup import MAGIC_V2, BackupPlan

DRILL_DIR_NAME = "drill"


@dataclass
class DrillStep:
    name: str
    ok: bool
    detail: dict = field(default_factory=dict)


@dataclass
class DrillReport:
    ok: bool
    steps: list[DrillStep] = field(default_factory=list)
    tables: dict = field(default_factory=dict)
    match: bool = False
    drill_dir: Path | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok,
                "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in self.steps],
                "tables": self.tables, "match": self.match,
                "drill_dir": str(self.drill_dir) if self.drill_dir else None}


class DrillFailure(RuntimeError):
    """演练任一步失败即整体失败（不落「看起来成功」的报告）。"""


def _require_passphrase() -> str:
    p = os.environ.get("DOC2MD_BACKUP_PASSPHRASE")
    if not p:
        raise E4001_BadRequest(
            "DOC2MD_BACKUP_PASSPHRASE not set",
            detail={"hint": "外置副本强制加密（NFR-18），口令只走环境变量"})
    return p


def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%'"
        " ORDER BY name")]


def fingerprint(db_path: Path) -> dict:
    """逻辑内容指纹：逐表行数 + 行内容的顺序无关摘要（NFR-20：只指纹不落正文）。

    CH-469（O-7）：原实现 `fetchall()` 把整表读进内存，再 `repr(sorted(rows))`
    造一个同样量级的字符串——`blocks.text` 是全文，几百 MB 的库做一次演练就是一次
    峰值翻倍（低配机上直接 OOM，而演练正是要在低配机上跑）。改成服务端游标
    `fetchmany` 逐行喂哈希：每行 sha256 视作大整数、按模 2^256 累加。
    加法可交换 ⇒ 与原来的「排序后整体哈希」一样与行序无关；重复行按重数计入
    （两行相同即加两次）。
    用途边界（CH-471 OPS-2）：这只是一把**非对抗性**的差异探针——比对「恢复出来的库
    vs 丢失前的库」是否内容一致，能可靠抓出意外损坏/漏恢复。它**不是**防篡改指纹：
    模 2^256 的加性合并与顺序无关，攻击者可用子集和构造同值指纹，**无需**任何 sha256
    原像。若将来需要防篡改，应改用带键 MAC（或在对行哈希排序后取异或等抗加性的组合）。
    """

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: dict[str, dict] = {}
        for name in _tables(con):
            rows, acc = 0, 0
            cur = con.execute(f'SELECT * FROM "{name}"')
            while True:
                batch = cur.fetchmany(512)
                if not batch:
                    break
                for row in batch:
                    rows += 1
                    payload = repr(tuple(row)).encode("utf-8", "surrogatepass")
                    acc = (acc + int.from_bytes(hashlib.sha256(payload).digest(), "big"))
                    acc &= (1 << 256) - 1
            out[name] = {"rows": rows,
                         "sha256": acc.to_bytes(32, "big").hex()[:16]}
        return out
    finally:
        con.close()


def integrity_check(db_path: Path) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        # 重度损坏时 pragma 本身抛错（而非回一行）——同样视为「非 ok」
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        con.close()


def run_drill(cfg: dict, root: Path, *, keep: bool = False) -> DrillReport:
    """执行恢复演练；任一步失败抛 DrillFailure（整体 ok=False 由调用方转退出码）。"""
    passphrase = _require_passphrase()
    root = Path(root)
    live_db = root / "state.sqlite"

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    drill = root / "tmp" / f"{DRILL_DIR_NAME}-{ts}"
    work = drill / "work"
    local_dir = drill / "backups_local"
    external_dir = drill / "backups_external"
    for d in (work, local_dir, external_dir):
        d.mkdir(parents=True, exist_ok=True)

    rep = DrillReport(ok=False, drill_dir=drill)
    try:
        # 1) 线上库只读拷贝进沙箱（不存在则建空 schema，演练对空库同样成立）
        live_copy = work / "state.sqlite"
        shutil.copy2(live_db, live_copy) if live_db.is_file() else _create_empty(live_copy)
        rep.steps.append(DrillStep("copy_live", True, {"bytes": live_copy.stat().st_size}))
        pre_loss = fingerprint(live_copy)

        # 2) 本地快照
        plan = BackupPlan(
            local_dir=local_dir, external_dir=external_dir,
            rotation_days=int(_cfg_get(cfg, "ops.backup_rotation_days", 14)),
            work_root=work,
            work_dir_mode=str(_cfg_get(cfg, "ops.work_dir_mode", "700")),
            cfg=cfg)

        snap = plan.snapshot(live_copy)
        rep.steps.append(DrillStep("snapshot", True, {"file": snap.name}))
        # O-12：权限位没真正落下（SMB/NFS/exFAT 等无 POSIX 权限的卷）不是失败，
        # 但必须出现在报告里——「work/ 700」是快照可以明文存放的唯一前提。
        if plan.mode_warnings:
            rep.steps.append(DrillStep("work_dir_mode", True,
                                       {"warnings": list(plan.mode_warnings)}))

        # 3) 外置加密副本（NFR-18：强制 AES-256-GCM 容器 + 600）
        enc = plan.to_external(snap, passphrase)
        head = enc.read_bytes()[:len(MAGIC_V2)]
        if head != MAGIC_V2:
            raise DrillFailure(f"external container magic mismatch: {head!r}")
        rep.steps.append(DrillStep("external_encrypt", True,
                                   {"file": enc.name, "mode": oct(enc.stat().st_mode & 0o777)}))

        # 4) 模拟数据丢失：沙箱工作库整体删除（最强损失面）
        live_copy.unlink()
        rep.steps.append(DrillStep("simulate_loss", True, {"removed": "work/state.sqlite"}))

        # 5) 轮出确认：人造一个超期副本（20 天前）与当期副本，rotate 后超期必须消失
        old = local_dir / "state-20000101T000000Z.sqlite"
        old.write_bytes(b"stale")
        old_mtime = datetime.now(timezone.utc) - timedelta(days=20)
        os.utime(old, (old_mtime.timestamp(), old_mtime.timestamp()))
        removed = plan.rotate(now=datetime.now(timezone.utc))
        removed_names = {p.name for p in removed}
        if old.name not in removed_names:
            raise DrillFailure("rotate did not remove the stale snapshot")
        if not snap.is_file():
            raise DrillFailure("rotate removed the current snapshot")
        rep.steps.append(DrillStep("rotate", True,
                                   {"removed": sorted(removed_names), "kept": snap.name}))

        # 6) 解密恢复
        restored = work / "restored.sqlite"
        plan.restore(enc, restored, passphrase)
        rep.steps.append(DrillStep("restore", True,
                                   {"file": restored.name,
                                    "mode": oct(restored.stat().st_mode & 0o777)}))

        # 7) integrity_check
        ic = integrity_check(restored)
        if ic != "ok":
            raise DrillFailure(f"integrity_check = {ic!r}")
        rep.steps.append(DrillStep("integrity_check", True, {"result": ic}))

        # 8) 内容比对：恢复库 vs 丢失前快照（逐表行数 + 指纹）
        after = fingerprint(restored)
        rep.tables = after
        rep.match = after == pre_loss
        if not rep.match:
            diff = {t for t in set(after) | set(pre_loss)
                    if after.get(t) != pre_loss.get(t)}
            raise DrillFailure(f"restored content mismatch on tables: {sorted(diff)}")
        rep.steps.append(DrillStep("compare", True, {"tables": len(after), "match": True}))
        rep.ok = True
        return rep
    except DrillFailure:
        raise
    except Exception as exc:
        # 任一步的非预期失败（GCM InvalidTag / OSError / sqlite3 错误）统一收敛为
        # DrillFailure：CLI 只认这一种失败形态，绝不泄出栈回溯当契约输出。
        raise DrillFailure(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if not keep:
            shutil.rmtree(drill, ignore_errors=True)
            rep.drill_dir = None


def _create_empty(path: Path) -> None:
    from ..storage.db import Store
    Store(path).close()


def _cfg_get(cfg, dotted: str, default):
    """不依赖 config.get（drill 可能拿到任意 dict）；口径与 config.get 一致。"""
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
