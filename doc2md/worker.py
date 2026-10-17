"""work —— 队列消费进程（CH-449；低配机「较多文件」的第二条吞吐路径）。

为什么需要它：`serve` 是同步转换的 HTTP 面，一次 `POST /jobs` 丢 200 个文件会把
请求线程占满（并把 workers 个解析进程的内存同时抬起来）。ADR-30 明令不在 serve 内
起常驻线程，所以异步化走「入队 + 独立 `doc2md work` 进程」——与 cron 同构：
分进程、共用 state.sqlite（WAL + busy_timeout，CH-436 口径）。

分工纪律：
- 父进程 = 唯一 DB 写入者（claim / mark / save_ir / 事件），sqlite 不开跨进程共享连接；
- 子进程 = 纯计算（`pipeline.batch._convert_one`），只回传紧凑摘要，不回传 IR 对象；
- 认领用 `BEGIN IMMEDIATE` + `UPDATE ... WHERE status='queued'` 双条件，
  两个 work 进程同时跑也不会重复消费（与 CH-412 的 uq_tasks_doc_active 附着语义一致）。

日志纪律（CH-444 的第三条）：work 进程写自己的 `logs/work.jsonl`，
不与 serve 的 app.jsonl、cron 的 cron.jsonl 同名——重命名式轮转只能有一个所有者。

平台：Windows 无 fork，worker 函数与参数均可 pickle（batch 模块顶层），
`ProcessPoolExecutor` 默认上下文即可，实测 fork / spawn 两种启动方式产物一致。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .errors import E4041_NotFound, E4091_DocOverwrite
from .pipeline.batch import default_workers, run_batch
from .scheduler import Scheduler
from .storage.db import Store, now_iso
from .storage.repo import save_ir


@dataclass
class WorkReport:
    drained: int = 0
    done: int = 0
    failed: int = 0
    rejected: int = 0
    drift: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"drained": self.drained, "done": self.done, "failed": self.failed,
                "rejected": self.rejected, "drift": self.drift, "notes": self.notes}


def claim(store: Store, limit: int) -> list[dict]:
    """原子认领最多 limit 个 queued 作业 → 置 running（同事务，防双认领）。

    消费参数取 `source_path` / `mode` 列（CH-448），进程崩溃后靠它们重新排队。
    """
    limit = max(1, int(limit))
    out: list[dict] = []
    with store.tx() as tx:
        rows = tx.execute(
            "SELECT job_id, doc_id, source_key, source_path, mode FROM tasks"
            " WHERE status='queued' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        for r in rows:
            tx.execute("UPDATE tasks SET status='running', started_at=?, error_code=''"
                       " WHERE job_id=? AND status='queued'", (now_iso(), r["job_id"]))
            if tx.execute("SELECT changes()").fetchone()[0] != 1:
                continue
            out.append({"job_id": r["job_id"], "doc_id": r["doc_id"],
                        "source_key": r["source_key"] or "",
                        "path": r["source_path"] or "", "mode": r["mode"]})
    return out


def _out_dir(cfg: dict, root):
    return config.path_setting(cfg, "paths.out", root, str(root / "out"))


def reclaim_stale(store: Store, cfg: dict) -> int:
    """遗骸回收（CH-448）：`doc2md work` 被 OOM killer 干掉是低配机常态，
    其认领的 running 作业若超过 [perf].stale_job_minutes 未更新，重新置 queued。
    只动带消费路径的 running 行（同步 serve 路径写的 running 不带 path 语义，不回收）。

    CH-469：常驻 `run_work` 按 [perf].stale_check_seconds 周期性重跑本函数，
    不再只在进程启动时跑一次。
    """
    minutes = int(config.get(cfg, "perf.stale_job_minutes", 30) or 0)
    if minutes <= 0:
        return 0
    with store.tx() as tx:
        # `datetime(started_at)` 不是冗余：started_at 由 now_iso() 写成
        # `YYYY-MM-DDTHH:MM:SS.mmm+00:00`，而 datetime('now') 给 `YYYY-MM-DD HH:MM:SS`。
        # SQLite 的字符串比较在第 11 位就分出胜负（'T'=0x54 > ' '=0x20），
        # 直比起作用的同日内过期作业永远判不出来（缺陷 CH-455）。
        rows = tx.execute(
            "SELECT job_id FROM tasks WHERE status='running'"
            " AND started_at IS NOT NULL AND datetime(started_at) < datetime('now', ?)"
            " AND source_path IS NOT NULL",
            (f"-{minutes} minutes",)).fetchall()
        for r in rows:
            tx.execute("UPDATE tasks SET status='queued', started_at=NULL, error_code=''"
                       " WHERE job_id=? AND status='running'", (r["job_id"],))
    return len(rows)


def _startup_reconcile(store: Store) -> dict:
    """启动时 FTS 对账（CH-473 / R5-STOR-2）：失败不阻断启动，只回异常类名。

    与 api.create_app 的同名逻辑同口径——work 与 serve 都可能在「迁移中途崩溃 /
    历史缺陷留下空索引」的库上启动，每日 cron 对账之前最长有 24h 检索静默失效窗口。
    """
    from .storage.repo import reconcile_search
    try:
        return reconcile_search(store)
    except Exception as exc:                    # noqa: BLE001 — 启动不得被对账炸掉
        return {"error": type(exc).__name__}


def queue_claim_size(cfg: dict | None, workers: int) -> int:
    """每轮领取量（CH-476 / R7-PERF-1，决策 D2）：workers × [perf].queue_batch_factor。

    按批取件而非按池取件——原 `claim(store, workers)` 让每轮固定开销（新建进程池 +
    每个新 worker 重导 pdfplumber ≈0.17s）只摊到 workers 件单页转换上，并发增益被
    池重建吃掉（12.3 T6「4 进程仅快 4.5%」）。系数=1 即旧口径。
    同时在跑的转换件数仍受 workers 约束（run_batch 池大小不变），X-17 收尾语义不动。
    """
    factor = 4
    if cfg is not None:
        try:
            factor = int(config.get(cfg, "perf.queue_batch_factor", 4) or 4)
        except (TypeError, ValueError):
            factor = 4
    return max(1, int(workers)) * max(1, factor)


def drain_once(store: Store, cfg: dict, root, *, workers: int | None = None,
               writer=None) -> WorkReport:
    """认领一批 → 进程池转换 → 逐条落库。队列空时返回 drained=0。"""
    rep = WorkReport()
    n = workers if workers is not None else default_workers(cfg)
    n = max(1, int(n))
    jobs = claim(store, queue_claim_size(cfg, n))
    if not jobs:
        return rep
    rep.drained = len(jobs)
    by_path: dict[str, list[dict]] = {}
    for j in jobs:
        if not j.get("path"):
            # 入队时未带路径（历史行 / 只给了 doc_id）→ 明确失败，不静默挂起
            Scheduler(store).mark(j["job_id"], "failed", "E4001",
                                  {"reason": "queued task has no source path"})
            rep.failed += 1
            rep.notes.append(f"{j['job_id']}: no path")
            continue
        by_path.setdefault(j["path"], []).append(j)

    results: list[dict] = []
    # 同批内可能有不同转换档位（mode 为 per-job），按 mode 分组跑池，
    # 每组仍是同一批进程，不多开进程数上限。
    by_mode: dict[str | None, list[str]] = {}
    for path in by_path:
        mode = next((j.get("mode") for j in by_path[path] if j.get("mode")), None)
        by_mode.setdefault(mode, []).append(path)
    for mode, group in by_mode.items():
        results.extend(run_batch(group, cfg=cfg, out_dir=_out_dir(cfg, root),
                                 workers=n, mode=mode))
    sched = Scheduler(store)
    for res in results:
        group = by_path.get(res["file"], [])
        # CH-483（R8-SVC-2）：同一路径可能排着多个不同 doc_id 的作业（入队后
        # 文件被替换）。本轮只转换一次，产物只对应 consumed doc_id；其余旧作业
        # 必须 rejected/E4092，不能全部标 done（否则旧 doc_id 查询 404）。
        # 只有“能明确对上 consumed doc_id”时才淘汰组内其它作业；对不上
        # （历史测试替身 / 转换端未回报 doc_id）保持原来的全组处理语义。
        matched = None
        if res["status"] == "ok":
            consumed = res.get("doc_id") or ""
            for cand in reversed(group):
                if consumed and cand.get("doc_id") == consumed:
                    matched = cand
                    break
        for job in group:
            if res["status"] == "ok" and matched is not None and job is not matched:
                detail = {"reason": "source_superseded",
                          "enqueued_doc_id": job.get("doc_id") or "",
                          "consumed_doc_id": res.get("doc_id") or ""}
                sched.mark(job["job_id"], "rejected", "E4092", detail)
                rep.rejected += 1
                rep.notes.append(f"{job['job_id']}: source superseded")
                _event(store, job, res, "job_rejected", writer)
                continue
            if res["status"] == "ok":
                ir = _read_ir(res["zip"])
                if ir is None:
                    # CH-469：产物 status=ok 但 zip 里读不到 ir.json（损坏 / 缺项 /
                    # IR 版本不符）时，原实现跳过入库却仍标 done + 发 job_done——
                    # 队列说「完成」、`GET /docs/<id>` 却 404，正文只剩一个不可寻址的
                    # zip，既骗过监控又断掉重试线索。改为明确失败（可安全重投）。
                    sched.mark(job["job_id"], "failed", "E5031",
                               {"reason": "artifact_ir_unreadable",
                                "zip": str(res.get("zip", ""))[-64:]})
                    rep.failed += 1
                    rep.notes.append(f"{job['job_id']}: ir unreadable")
                    _event(store, job, res, "job_failed", writer)
                    continue
                try:
                    drift = _finish_ok(store, sched, job, res, ir, writer=writer)
                except E4041_NotFound:
                    # CH-476（R7-SVC-1）：删除×在途转换竞争——任务行已被 delete_doc
                    # 级联收走，_finish_ok 已回滚（docs/blocks/search 未写回）。
                    # 记 rejected 并补一条留痕事件（events 无 FK，行本身可达；
                    # 正文不复活是主目标，可观测性是次目标）。
                    sched.mark(job["job_id"], "rejected", "E4041",
                               {"reason": "doc_deleted_during_conversion"})
                    _event(store, job, res, "job_rejected", writer)
                    rep.rejected += 1
                    rep.notes.append(f"{job['job_id']}: doc deleted during conversion")
                    continue
                except E4091_DocOverwrite as e:
                    # CH-471（STOR-5）：库内已有编辑 → 拒绝覆盖（编辑原样保留）。
                    # 无人值守的 work/watch 路径默认不 force：宁可显式 rejected
                    # 也不静默丢编辑；需要覆盖由调用方带 force 重投。
                    sched.mark(job["job_id"], "rejected", e.code,
                               {"reason": "edited_blocks_present",
                                "edited_blocks": e.detail.get("edited_blocks")})
                    rep.rejected += 1
                    rep.notes.append(f"{job['job_id']}: overwrite conflict (edits kept)")
                    _event(store, job, res, "job_rejected", writer)
                    continue
                rep.done += 1
                rep.drift += 1 if drift else 0
            elif res["status"] == "rejected":
                sched.mark(job["job_id"], "rejected", res.get("code", "E4221"),
                           {"reason": res.get("message", "")})
                rep.rejected += 1
                _event(store, job, res, "job_rejected", writer)
            else:
                sched.mark(job["job_id"], "failed", res.get("code", "E5000"),
                           {"reason": res.get("message", "")[:200]})
                rep.failed += 1
                _event(store, job, res, "job_failed", writer)
    return rep


def _finish_ok(store: Store, sched: Scheduler, job: dict, res: dict, ir,
               writer=None) -> bool:
    """入库 + 状态推进。返回是否发生内容漂移（入队后源文件被改写）。

    CH-459（A-4）：detail 更新 / save_ir / mark(done) / 事件原为四次独立
    提交（每次提交一次 fsync 是当时口径；`synchronous` 自 CH-471 / STOR-1 起为
    NORMAL，合批仍保留——每任务固定开销的剩余主因是池重建与 worker 重导入，
    见 12.3 归因与 CH-476 / R7-PERF-1），
    现合并为单事务。同笔修掉一个静默缺陷：原实现先写 detail_json 再
    `sched.mark(done)`，而 mark 用 `{}` 覆盖 detail_json——明细从来没活过
    （本次把 detail 与终态写进同一笔 UPDATE）。

    `ir` 由调用方（drain_once）回读后传入：CH-469 起「读不到 IR」必须在
    **决定终态之前**判定，否则会把拿不到正文的作业标成 done。

    CH-476（R7-SVC-1，决策 D6）：mark 提前到 save_ir 之前（先定行、后落库）——
    delete_doc 级联会收走在途 running 行，mark rowcount=0 即识别竞争，抛
    E4041 回滚整笔事务，正文不再写回（原序先写 docs 再无条件 UPDATE，
    删除×转换竞争会留下「内容有、任务无」的半复活态）。

    CH-476（R7-PERF-2）：漂移判定用子进程回报的 `doc_id`，父进程不再对源文件做
    第三次全量流式读（入队 / 子进程 / 此处各一遍，20MB+ 文档每任务白读两遍）。
    """
    from .api import _emit
    detail = {"path": job["path"], "zip": res["zip"], "blocks": res["blocks"]}
    # `res["doc_id"]` 是产物所依据那份内容的哈希（子进程转换前取）。比「转换结束后
    # 再哈希一次」更贴合物理事实：源文件在转换**期间**被改写时，旧口径会把
    # 「磁盘新内容 ≠ 产物」误报成漂移，而留痕里的 consumed 又写的是转换前的哈希，
    # 自相矛盾；新口径只声明「入队后、转换前被改写」这一真实可归因窗口。
    consumed = res.get("doc_id") or ""
    enqueued = job.get("doc_id") or ""
    drift = bool(consumed and enqueued and consumed != enqueued)
    if drift:
        # 产物按消费时内容入库；任务留痕，不偷偷改 task.doc_id 主键语义
        detail["content_drift"] = {"enqueued": enqueued, "consumed": consumed}
    with store.tx() as tx:
        if not sched.mark(job["job_id"], "done", detail=detail, tx=tx):
            raise E4041_NotFound(
                f"task row gone (doc deleted during conversion): {job['job_id']}")
        save_ir(store, ir, tx=tx)
        _emit(store, "job_done", res.get("doc_id") or job.get("doc_id"), job["job_id"],
              {"blocks": int(res.get("blocks", 0) or 0), "source": "work",
               "file": str(job.get("path", ""))[-64:]}, writer, tx=tx)
    return drift


def _read_ir(zip_path: str):
    """从产物 zip 回读 IR（子进程不回传对象；父进程以产物为准重建入库内容）。

    读不到即返回 None——由调用方决定终态（CH-469：不得据此标 done）。
    `BadZipFile` 不是 `OSError` 子类、`IR.from_dict` 的字段类型问题抛 `TypeError`，
    两者都要收进来，否则一个坏产物会让整个消费批次崩掉。
    """
    import zipfile

    from .ir import IR
    try:
        with zipfile.ZipFile(zip_path) as z:
            name = next((n for n in z.namelist() if n.endswith("ir.json")), None)
            if not name:
                return None
            return IR.from_dict(json.loads(z.read(name).decode("utf-8")))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None


def _event(store: Store, job: dict, res: dict, code: str, writer,
           doc_id: str | None = None) -> None:
    """事件入库 + 落 work.jsonl（复用 api._emit，NFR-19/20 同一口径，CH-444）。"""
    from .api import _emit
    payload = {"blocks": int(res.get("blocks", 0) or 0), "source": "work",
               "file": str(job.get("path", ""))[-64:]}
    if code != "job_done" and res.get("code"):
        payload["code"] = res["code"]
    _emit(store, code, doc_id or job.get("doc_id"), job["job_id"], payload, writer)


def queue_stats(store: Store) -> dict:
    return {r["status"]: r["c"] for r in store.q(
        "SELECT status, COUNT(*) c FROM tasks GROUP BY status")}


def _enqueue_hash_threads(cfg: dict | None) -> int:
    """[perf].enqueue_hash_threads：入队预检/哈希的线程上限（CH-476 / R7-PERF-3）。

    1 = 旧口径（单线程逐个哈希）。默认 8：GB 级目录按 ~300-500MB/s 单线程要数秒，
    `hashlib` 对 1MB 块释放 GIL，线程池近线性；8 个并发读的驻留 ≈ 8×1MB，可忽略。
    """
    n = 8
    if cfg is not None:
        try:
            n = int(config.get(cfg, "perf.enqueue_hash_threads", 8) or 8)
        except (TypeError, ValueError):
            n = 8
    return max(1, n)


def _sniff_and_hash(p: Path) -> tuple[str, str]:
    """单个源的入队预检（只读、无库副作用）：存在性 → magic → 内容哈希。"""
    from .ir import hash_of_path
    from .pipeline.s1_parse import sniff
    if not p.is_file():
        raise E4041_NotFound(f"path not found: {p}")
    sniff(p)                                   # → E4151（magic 不合法）
    return hash_of_path(p)


def enqueue_many(sched: Scheduler, paths, *, mode: str | None = None,
                 idem_keys=None, cfg: dict | None = None,
                 threads: int | None = None) -> list[dict]:
    """批量入队（CH-476 / R7-PERF-3）：**哈希并行、提交单事务**。

    并行只放在预检/哈希（纯文件读，`hashlib` 大缓冲释放 GIL）；写库按入参顺序
    一次 `sched.submit_many`（R7-PERF-h），逐条判定与队列行序、
    `uq_tasks_doc_active` 附着口径同旧串行实现完全一致，只是 N 次提交并成 1 次。
    原子性沿用 CH-449 的「要么全入队、要么零副作用」：任一文件不可读或 magic
    不合法，在写库之前就抛出（E4041 / E4151）——旧实现靠「先整体 sniff 再逐个
    enqueue」达成，现同一判定收在本函数的预检阶段，顺带省掉每个文件多开一次。
    """
    items = [Path(p) for p in paths]
    if idem_keys is not None and len(idem_keys) != len(items):
        raise ValueError("idem_keys must align with paths")
    if not items:
        return []
    k = int(threads) if threads is not None else _enqueue_hash_threads(cfg)
    k = max(1, min(k, len(items)))
    if k == 1:
        hashed = [_sniff_and_hash(p) for p in items]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=k) as ex:
            # `ex.map` 按入参顺序产出，异常在迭代到该位时抛出 → 首个失败即整体失败，
            # 且 list() 抽干后仍未触碰 state.sqlite。
            hashed = list(ex.map(_sniff_and_hash, items))
    out = []
    jobs = sched.submit_many([(hashed[i][0], hashed[i][1],
                               idem_keys[i] if idem_keys is not None else None,
                               str(p), mode)
                              for i, p in enumerate(items)])
    for i, p in enumerate(items):
        doc_id = hashed[i][0]
        job = jobs[i]
        out.append({"file": p.name, "job_id": job.job_id, "doc_id": doc_id,
                    "status": "queued", "attached": job.attached,
                    "idempotency_hit": job.idem_hit})
    return out


def enqueue(sched: Scheduler, path: str | Path, *, mode: str | None = None,
            idem_key: str | None = None, cfg: dict | None = None) -> dict:
    """只入队不算（CH-449 异步路径）。doc_id 仍按内容寻址（约定 14），
    因此 `uq_tasks_doc_active` 的附着去重在入队阶段就已生效。

    入队面把「类型 / 存在性」判定做掉，口径与同步路径一致（E4041 / E4151），
    否则垃圾文件要等到消费进程才失败，HTTP 面等于放弃了输入安全判定。
    消费参数（source_path / mode）走 tasks 独立列，附着到既有作业时不改对端行。
    单文件即 `enqueue_many` 的一元素特例（CH-476 / R7-PERF-3 收敛为一条代码路径）。
    """
    return enqueue_many(sched, [path], mode=mode, idem_keys=[idem_key], cfg=cfg)[0]


def run_work(cfg: dict, root, *, once: bool = False, max_jobs: int = 0,
             workers: int | None = None, idle_seconds: float = 5.0,
             reclaim_seconds: float | None = None, on_report=None) -> WorkReport:
    """消费循环：once=True 排空当前队列即退出；max_jobs>0 达量退出；否则轮询常驻。"""
    from .ops.logging import RotatingJsonlWriter
    store = Store(root / "state.sqlite")
    total = WorkReport()
    reclaimed = reclaim_stale(store, cfg)
    if reclaimed:
        total.notes.append(f"reclaimed_stale={reclaimed}")
    if reclaim_seconds is None:
        try:
            reclaim_seconds = float(config.get(cfg, "perf.stale_check_seconds", 60) or 0)
        except (TypeError, ValueError):
            reclaim_seconds = 60.0
    logs = config.path_setting(cfg, "paths.logs", root, str(root / "logs"))
    writer = RotatingJsonlWriter(
        logs, stem="work",
        rotate_mb=float(config.get(cfg, "logs.rotate_mb", 50)),
        rotate_keep=int(config.get(cfg, "logs.rotate_keep", 5)))
    writer.write({"code": "WORK_START", "root": str(root),
                  "once": bool(once), "max_jobs": int(max_jobs or 0),
                  "reclaimed_stale": reclaimed,
                  # CH-473（R5-STOR-2 / STOR-3 的 work 对账半）：与 create_app 同口径，
                  # 启动时先修一次 FTS 一致性（迁移中途崩溃 / 历史缺陷的空索引无告警），
                  # 只回传统计、不读正文（NFR-20）；失败不阻断启动，类名进日志。
                  "search_reconcile": _startup_reconcile(store),
                  "workers": workers if workers is not None else default_workers(cfg)})
    writer.flush()
    last_reclaim = time.monotonic()
    try:
        while True:
            # CH-469：遗骸回收原只在进程启动时跑一次。常驻 work 进程跑几天后，
            # 期间被 OOM 干掉的**同行**进程留下的 running 行永不回收——那正是
            # CH-448 要防的场景（低配机 OOM 是常态），队列看似有活却永不动。
            # 节流重跑：`reclaim_seconds<=0` 回到「只启动时一次」的旧行为。
            if not once and reclaim_seconds > 0 and \
                    time.monotonic() - last_reclaim >= reclaim_seconds:
                last_reclaim = time.monotonic()
                n = reclaim_stale(store, cfg)
                if n:
                    total.notes.append(f"reclaimed_stale={n}")
                    writer.write({"code": "WORK_RECLAIM", "count": n})
                    writer.flush()
            rep = drain_once(store, cfg, root, workers=workers, writer=writer)
            for k in ("drained", "done", "failed", "rejected", "drift"):
                setattr(total, k, getattr(total, k) + getattr(rep, k))
            total.notes.extend(rep.notes)
            if on_report:
                on_report(rep)
            finished = total.done + total.failed + total.rejected
            if max_jobs and finished >= max_jobs:
                return total
            if rep.drained == 0:
                if once:
                    return total              # 队列排空即退出（cron 式批量）
                time.sleep(idle_seconds)
    finally:
        writer.write({"code": "WORK_STOP", **total.to_dict()})
        writer.close()
        store.close()
