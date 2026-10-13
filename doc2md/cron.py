"""doc2md cron —— 定时任务承载（ADR-30 / CH-414 / P-62）。

执行语义（§3.4）：
  --daily  = 日报（NFR-14）→ 本地快照备份（FR-607）→ tmp 清理 → 日志轮转检查
  --weekly = --daily 的超集：先完整执行 --daily（幂等），再增量打包至外置加密卷（NFR-18）
不装 cron、不在 serve 内起常驻线程；由 launchd StartCalendarInterval 触发。
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .ops.backup import BackupPlan
from .ops.logging import RotatingJsonlWriter
from .storage.db import Store, now_iso


@dataclass
class CronReport:
    mode: str
    started_at: str
    steps: list[dict] = field(default_factory=list)
    finished_at: str = ""
    ok: bool = True

    def add(self, step: str, **kv) -> None:
        self.steps.append({"step": step, **kv})

    def to_json(self) -> str:
        return json.dumps({"mode": self.mode, "started_at": self.started_at,
                           "finished_at": self.finished_at, "ok": self.ok,
                           "steps": self.steps}, ensure_ascii=False, indent=2)


def _db_bytes(state: Path) -> int:
    """待备份量估算：主库 + WAL（快照消费的是 checkpoint 后的全量）。"""
    total = 0
    for p in (state, state.with_name(state.name + "-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def sweep_tmp(cfg: dict, root: Path) -> int:
    """清理 work/tmp 残留（§4.6：服务启动与 cron --daily 共用同一路径口径）。"""
    tmp = config.path_setting(cfg, "paths.tmp", root, str(root / "tmp"))
    cleaned = 0
    if tmp.exists():
        for p in tmp.iterdir():
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                cleaned += 1
            except OSError:
                pass
    return cleaned


def run(cfg: dict | None = None, root: Path | None = None, mode: str = "daily",
        passphrase_env: str = "DOC2MD_BACKUP_PASSPHRASE",
        with_backup: bool = True) -> CronReport:
    cfg = cfg or config.load()
    root = Path(root) if root else config.path_setting(cfg, "paths.root", ".")
    rep = CronReport(mode=mode, started_at=now_iso())

    store = Store(root / "state.sqlite")
    logs = config.path_setting(cfg, "paths.logs", root, str(root / "logs"))
    # CH-444：cron 与 serve 是两个进程，各自轮转同名 app.jsonl 会互相踩（重命名式
    # 轮转会让对端继续写进已轮出的文件）。serve 拥有 app.jsonl，cron 只写 cron.jsonl。
    writer = RotatingJsonlWriter(logs, stem="cron",
                                 rotate_mb=float(config.get(cfg, "logs.rotate_mb", 50)),
                                 rotate_keep=int(config.get(cfg, "logs.rotate_keep", 5)))

    # 1) 日报 —— NFR-20：只含统计，不含正文片段
    daily = _daily_report(store, root, cfg)
    rep.add("daily_report", path=str(daily.path), docs=daily.stat_doc if daily.ok else -1)
    writer.write({"code": "CRON_DAILY", "mode": mode, "docs": daily.stat_doc,
                  "jobs_ok": daily.ok})

    # 2) 本地快照备份 —— FR-607
    plan = BackupPlan(local_dir=config.path_setting(
                          cfg, "ops.backup_local_dir", root, str(root / "reports/backups")),
                      external_dir=config.path_setting(
                          cfg, "ops.backup_external_dir", root)
                      if config.get(cfg, "ops.backup_external_dir") else None,
                      rotation_days=int(config.get(cfg, "ops.backup_rotation_days", 14)),
                      encrypted_external=bool(
                          config.get(cfg, "ops.backup_external_encrypted", True)),
                      work_root=root)
    if with_backup:
        # NFR-6（CH-450）：备份同样是写盘。快照大小 = 库大小（外置副本 ≈ ×1.05，
        # AES-GCM + nonce），按同一系数口径预估；空间不足记为失败步骤而不是半截文件。
        from .errors import Doc2mdError
        from .ops.diskguard import assert_free_space
        state = root / "state.sqlite"
        snap = None
        try:
            assert_free_space(plan.local_dir, _db_bytes(state), cfg=cfg,
                              what="local_snapshot")
            snap = plan.snapshot(state)
        except Doc2mdError as exc:
            rep.add("local_snapshot", code=exc.code, message=exc.message, ok=False)
            rep.ok = False
        except OSError as exc:
            rep.add("local_snapshot", error=str(exc)[:200], ok=False)
            rep.ok = False
        if snap is not None:
            rep.add("local_snapshot", path=str(snap), bytes=snap.stat().st_size)

            # 3) 外置加密增量（仅 weekly）
            if mode == "weekly":
                import os
                phrase = os.environ.get(passphrase_env, "")
                if not plan.external_dir:
                    # CH-433：未配置外置卷属「按配置跳过」，不是失败；
                    # 原逻辑先判 passphrase，会让没接外置卷的部署每周报一次 ok=False。
                    rep.add("external_backup", skipped="ops.backup_external_dir not configured")
                elif not phrase:
                    rep.add("external_backup", skipped="no passphrase in env", ok=False)
                    rep.ok = False
                else:
                    try:
                        assert_free_space(plan.external_dir, snap.stat().st_size, cfg=cfg,
                                          what="external_backup")
                        out = plan.to_external(snap, phrase)
                        rep.add("external_backup", path=str(out), bytes=out.stat().st_size,
                                encrypted=True)
                    except Doc2mdError as exc:
                        rep.add("external_backup", code=exc.code, message=exc.message,
                                ok=False)
                        rep.ok = False
            removed = plan.rotate()
            rep.add("rotation", removed=[str(p) for p in removed])

    # 4) tmp 清理（服务启动同样执行一次，见 api.create_app）
    cleaned = sweep_tmp(cfg, root)
    rep.add("tmp_clean", removed=cleaned)

    # 5) 日志轮转检查
    rotated = writer.check()
    rep.add("log_rotation", rotated=rotated)
    writer.close()

    # 6) 事件保留期清理（[ops].events_retention_days）
    days = int(config.get(cfg, "ops.events_retention_days", 30))
    with store.tx() as tx:
        n = tx.execute("DELETE FROM events WHERE ts < datetime('now', ?)",
                       (f"-{days} days",)).rowcount
    rep.add("events_purge", removed=n, retention_days=days)

    rep.finished_at = now_iso()
    reports = config.path_setting(cfg, "paths.reports", root, str(root / "reports"))
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"cron-{mode}-{time.strftime('%Y%m%d')}.json").write_text(
        rep.to_json(), encoding="utf-8")
    return rep


@dataclass
class Daily:
    ok: bool
    stat_doc: int = 0
    path: Path | None = None


def _daily_report(store: Store, root: Path, cfg: dict) -> Daily:
    """日报（NFR-14）：计数与失败码分布，绝不含正文（NFR-20）。"""
    docs = int(store.q1("SELECT COUNT(*) c FROM docs")["c"])
    tasks = {r["status"]: r["c"] for r in store.q(
        "SELECT status, COUNT(*) c FROM tasks GROUP BY status")}
    errs = [dict(r) for r in store.q(
        "SELECT error_code, COUNT(*) c FROM tasks WHERE error_code IS NOT NULL"
        " AND error_code != '' GROUP BY error_code")]
    shards = int(store.q1("SELECT COUNT(*) c FROM docs WHERE sharded=1")["c"])
    del_events = store.q1("SELECT MAX(ts) m FROM deletions")["m"]
    body = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "pipeline": "2.4.0",
        "docs_total": docs,
        "docs_sharded": shards,
        "tasks": tasks,
        "error_codes": errs,
        "last_deletion_ts": del_events,
        "log_dir": str(config.path_setting(cfg, "paths.logs", root, str(root / "logs"))),
    }
    out = config.path_setting(cfg, "paths.reports", root,
                              str(root / "reports")) / f"daily-{body['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return Daily(ok=True, stat_doc=docs, path=out)
