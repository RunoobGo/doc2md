"""doc2md cron —— 定时任务承载（ADR-30 / CH-414 / P-62）。

执行语义（方案 6.5 与 附录 E 步骤表为准，逐条失败语义见后者）：
  --daily  = daily_report → local_snapshot（磁盘门禁是其前置检查）→〔快照成功且 chmod 有告警时 work_dir_mode〕
             → rotation → tmp_clean → log_rotation → events_purge → tasks_purge → search_reconcile
  log_rotation 一步治理两条通道：自持 jsonl 走阈值轮转（writer.check），launchd 输出捕获
  走就地截断保尾（bound_capture_files，CH-481）——后者的句柄属 launchd，改名式轮转会失效。
  --weekly = 同一序列，只在 `local_snapshot` 成功之后、`rotation` **之前**插入 external_backup（NFR-18），
             故新副本当轮即受轮出保护——不是「跑完日流程再追加备份」
不装 cron、不在 serve 内起常驻线程；由 launchd StartCalendarInterval 触发。
不装 cron、不在 serve 内起常驻线程；由 launchd StartCalendarInterval 触发。
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import PIPELINE_VERSION, config
from .ops.backup import BackupBusy, BackupPlan, backup_lock
from .ops.drill import DRILL_DIR_NAME
from .ops.logging import RotatingJsonlWriter, bound_capture_files, capture_budget
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
    """清理 work/tmp 残留（方案 6.5：服务启动与 cron --daily 共用同一路径口径）。

    CH-473（R5-STOR-4）：加**在途保护窗**。原实现无条件清空 tmp，与并发
    `doc2md work`（同一 root、tmp 落在 paths.tmp）无互斥——服务启动清扫 /
    cron --daily 会把在途转换的中间产物删掉，该文件转换失败（可重试，但无人值守
    时段周期性误伤，且失败原因与「磁盘已满」等不可区分）。现在 mtime 在
    `perf.stale_job_minutes`（默认 30）内的条目跳过（在途件随写入持续更新 mtime；
    目录取自身 mtime，内容增删即刷新）；该键 <= 0 = 关闭保护、维持旧行为全清。
    返回值仍是「已清条数」——被跳过的在途件不计数，下轮（或 cron 每日）再清。
    CH-476（R7-OPS-1）：`drill-*` 前缀（`backup drill --keep` 的取证沙箱）**永久豁免**，
    不受 mtime 窗口约束——取证目录必须在下一次每日作业之后仍然存在。
    """
    tmp = config.path_setting(cfg, "paths.tmp", root, str(root / "tmp"))
    cleaned = 0
    if not tmp.exists():
        return cleaned
    try:
        window_s = float(config.get(cfg, "perf.stale_job_minutes", 30) or 0) * 60.0
    except (TypeError, ValueError):
        window_s = 30 * 60.0
    cutoff = time.time() - window_s if window_s > 0 else 0.0
    for p in tmp.iterdir():
        # CH-476（R7-OPS-1）：`backup drill --keep` 的取证沙箱（`drill-<ts>/`）就在
        # 同一个 tmp 下，「保留演练沙箱目录（取证用）」不得被下一次 cron/serve 清扫
        # 兑现成删除——`drill-*` 前缀永久豁免。不带 --keep 时 drill 收尾自删，
        # 故这里被保护的只有用户明确要求留存的目录（不会无限堆积）。
        if p.name.startswith(f"{DRILL_DIR_NAME}-"):
            continue
        if cutoff:
            try:
                if p.stat().st_mtime >= cutoff:
                    continue          # 保护窗内的在途件：跳过
            except OSError:
                continue              # 取不到 mtime（竞态删除中）——按在途处理
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            cleaned += 1
        except OSError:
            pass
    return cleaned


def _run_backup(plan: BackupPlan, cfg: dict, root: Path, mode: str,
                passphrase_env: str, rep: "CronReport") -> None:
    """cron 的备份/外置/轮出步骤（在 backup_lock 内执行）。"""
    # NFR-6（CH-450）：备份同样是写盘。快照大小 = 库大小（外置副本 ≈ ×1.05，
    # AES-GCM + nonce），按同一系数口径预估；空间不足记为失败步骤而不是半截文件。
    from .errors import Doc2mdError
    from .ops.diskguard import assert_free_space
    state = root / "state.sqlite"
    snap = None
    # CH-483（R8-OPS-3）：只有本轮外置加密备份成功，才允许轮出外置旧副本。
    # 失败/跳过的轮次若仍 wheel，连续失败超过保留期会删掉最后一份外置副本。
    external_rotatable = False
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
        if plan.mode_warnings:
            # O-12：chmod 在无 POSIX 权限的卷上不生效 ≠ 失败，但「work/ 700
            # 所以快照可明文」这个前提已经不成立了，必须进日报。
            rep.add("work_dir_mode", warnings=list(plan.mode_warnings))

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
                    # CH-471（OPS-4）：外置卷剩余空间守卫已下沉到 plan.to_external
                    # （plan 带 cfg → 内部自检），这里不再重复预检，to_external 成为
                    # 唯一闸口，直接调用方也受同一道保护。
                    out = plan.to_external(snap, phrase)
                    external_rotatable = True
                    rep.add("external_backup", path=str(out), bytes=out.stat().st_size,
                            encrypted=True)
                except Doc2mdError as exc:
                    rep.add("external_backup", code=exc.code, message=exc.message,
                            ok=False)
                    rep.ok = False

        removed = plan.rotate(include_external=(mode == "weekly" and external_rotatable),
                              keep_newest=True)
        # CH-485（R9-OPS-1）：删前/恢复前快照与 state-* 同轮清理；沿用
        # 既有 rotation 步骤名，避免改变附录 E 的 10 步契约。
        pre_removed = plan.rotate_predelete()
        rep.add("rotation", removed=[str(p) for p in removed],
                predelete_removed=[str(p) for p in pre_removed],
                external_rotation=("done" if mode == "weekly" and external_rotatable
                                   else "skipped"))


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
                      work_root=root,
                      work_dir_mode=str(config.get(cfg, "ops.work_dir_mode", "700")),
                      cfg=cfg)

    if with_backup:
        try:
            with backup_lock(plan.local_dir):
                _run_backup(plan, cfg, root, mode, passphrase_env, rep)
        except BackupBusy as exc:
            rep.add("local_snapshot", skipped=str(exc))
            rep.add("rotation", external_rotation="skipped",
                    predelete_removed=[])
    # 4) tmp 清理（服务启动同样执行一次，见 api.create_app）
    cleaned = sweep_tmp(cfg, root)
    rep.add("tmp_clean", removed=cleaned)

    # 5) 日志轮转检查（含任务计划捕获文件的就地截断，CH-481 / NFR-19 补全）
    rotated = writer.check()
    cap_bytes, tail_bytes = capture_budget(
        float(config.get(cfg, "logs.rotate_mb", 50)),
        int(config.get(cfg, "logs.rotate_keep", 5)))
    clipped = bound_capture_files(logs, cap_bytes, tail_bytes)
    clip_errors = sum(1 for c in clipped if "error" in c)
    # 步骤名不变：捕获界定属本步语义（「日志空间治理」）的自然扩展，不是新增契约。
    # 截断失败只意味着继续占盘，不是数据丢失，故记数字进 payload 而不翻转 rep.ok——
    # 与 work_dir_mode 的 chmod 告警同口径（O-12）。
    rep.add("log_rotation", rotated=rotated, captures=clipped,
            capture_cap_bytes=cap_bytes, capture_tail_bytes=tail_bytes,
            capture_errors=clip_errors)

    # 6) 事件保留期清理（[ops].events_retention_days）
    days = int(config.get(cfg, "ops.events_retention_days", 30))
    with store.tx() as tx:
        # datetime(ts) 必要（同 CH-455 的 worker.reclaim_stale）：ts 由 now_iso()
        # 写成 ISO 带 '+00:00'，与 datetime('now') 的空格分隔式直比起作用，
        # 边界日当天的过期行会因 'T' > ' ' 被判为「未过期」，保留期实际最多溢出 24h。
        n = tx.execute("DELETE FROM events WHERE datetime(ts) < datetime('now', ?)",
                       (f"-{days} days",)).rowcount
    rep.add("events_purge", removed=n, retention_days=days)

    # 7) 终态作业保留期清理（CH-471 / SVC-4，[ops].tasks_retention_days）：
    #    tasks 除逐 doc 级联（repo.delete_doc）外此前永不清理，长运行实例无界增长，
    #    且 GET /jobs 每 5s 重扫全表 GROUP BY status → 延迟持续恶化。只删**终态**
    #    （done/failed/rejected）且已过保留期的行，queued/running 属在途不动。
    #    finished_at 口径同 events：用 datetime() 归一，避免 ISO '+00:00' 直比溢出。
    tdays = int(config.get(cfg, "ops.tasks_retention_days", 30))
    with store.tx() as tx:
        tn = tx.execute(
            "DELETE FROM tasks WHERE status IN ('done','failed','rejected') "
            "AND finished_at IS NOT NULL AND finished_at <> '' "
            "AND datetime(finished_at) < datetime('now', ?)",
            (f"-{tdays} days",)).rowcount
    rep.add("tasks_purge", removed=tn, retention_days=tdays)

    # 8) 搜索索引对账（CH-471 / STOR-3）：blocks 与 search_blocks 计数背离即整篇
    #    重建索引（复活 reindex 死代码），修掉「迁移崩溃 / 历史缺陷 → 检索静默失效」。
    from .storage.repo import reconcile_search
    rec = reconcile_search(store)
    rep.add("search_reconcile", **rec)
    if rec["docs_repaired"] or rec["orphans_removed"]:
        writer.write({"code": "CRON_SEARCH_RECONCILE", **rec})

    writer.close()
    # CH-473（R5-SVC-8）：cron 的 Store 与原实现一样从不 close——`PRAGMA optimize`
    # （CH-471 STOR-1 关闭前维护索引）因此不执行。进程即退、影响极小，但既然
    # close 是 no-op 安全操作，正常路径补上；异常路径由解释器退出时收尾。
    store.close()
    rep.finished_at = now_iso()
    reports = config.path_setting(cfg, "paths.reports", root, str(root / "reports"))
    reports.mkdir(parents=True, exist_ok=True)
    # CH-476（R7-OPS-2）：文件名日期与日报体的 `date` 统一到 UTC 口径——原用本地
    # `time.strftime`，跨零点的作业会写出 `cron-daily-<昨天>.json` 而内容记今天。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (reports / f"cron-{mode}-{stamp}.json").write_text(
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
        "pipeline": PIPELINE_VERSION,
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
