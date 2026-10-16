"""scheduler —— 作业投递与状态机（附录 B-8 约束 9，CH-412 / P-60）。

两条去重路径构成同一机制：
1. Idempotency-Key 命中 → 返回既有 job_id；
2. 同 doc_id 存在进行中作业（唯一索引 uq_tasks_doc_active）→ **附着**返回既有 job_id。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from .storage.db import Store, now_iso

ACTIVE = ("queued", "running")
TERMINAL = ("done", "failed", "rejected")


@dataclass
class Job:
    job_id: str
    doc_id: str
    status: str
    attached: bool = False
    idem_hit: bool = False


class Scheduler:
    def __init__(self, store: Store):
        self.store = store

    def submit(self, doc_id: str, source_key: str = "",
               idem_key: str | None = None, *, source_path: str | None = None,
               mode: str | None = None) -> Job:
        """投递作业；命中去重路径时不新建。

        `source_path` / `mode`（CH-448）：异步队列的消费参数，独立列存放
        （`detail_json` 是结果/失败明细的契约面，不混入管道数据）。
        """
        with self.store.tx() as tx:
            if idem_key:
                row = tx.execute("SELECT job_id, doc_id FROM tasks WHERE idem_key=? LIMIT 1",
                                 (idem_key,)).fetchone()
                if row:
                    return Job(row["job_id"], row["doc_id"], "queued",
                               attached=True, idem_hit=True)
            row = tx.execute(
                f"SELECT job_id FROM tasks WHERE doc_id=? AND status IN {ACTIVE} LIMIT 1",
                (doc_id,)).fetchone()
            if row:
                return Job(row["job_id"], doc_id, "queued", attached=True)
            job_id = "job-" + uuid.uuid4().hex[:12]
            try:
                tx.execute(
                    "INSERT INTO tasks(job_id, doc_id, source_key, status, idem_key,"
                    " created_at, source_path, mode) VALUES (?,?,?,?,?,?,?,?)",
                    (job_id, doc_id, source_key, "queued", idem_key, now_iso(),
                     source_path, mode))
            except sqlite3.IntegrityError:
                # 并发窗口：唯一索引挡住第二份进行中作业 → 附着
                row = tx.execute(
                    f"SELECT job_id FROM tasks WHERE doc_id=? AND status IN {ACTIVE} LIMIT 1",
                    (doc_id,)).fetchone()
                return Job(row["job_id"], doc_id, "queued", attached=True)
        return Job(job_id, doc_id, "queued")

    def submit_rejected(self, name: str, code: str, detail: dict | None = None,
                        *, idem_key: str | None = None) -> Job:
        """记一条「投递即拒收」的终态作业（CH-471 SVC-10 去重）。

        同步 /jobs、并行批、CLI watch 三处原本各自重复 `submit("reject:"+name)` +
        `mark(..., "rejected", code, detail)`；前缀命名与终态口径散在三份代码里易漂移，
        收敛到此。返回 Job 供调用方继续 emit 事件 / 组装响应。
        """
        j = self.submit("reject:" + name, idem_key=idem_key)
        self.mark(j.job_id, "rejected", code, detail)
        return j


    def mark(self, job_id: str, status: str, error_code: str = "", detail: dict | None = None,
             tx=None):
        """状态推进。CH-459（A-4）：`tx` 形参允许并入调用方事务——
        worker._finish_ok 与同步 /jobs 路径把 mark 与入库/事件合为单次提交
        （WAL + synchronous=FULL 下每次提交都 fsync，是每任务固定开销主因）。
        """
        if status not in ACTIVE + TERMINAL:
            raise ValueError(f"bad status {status}")
        col = {"running": "started_at", "done": "finished_at",
               "failed": "finished_at", "rejected": "finished_at"}.get(status)

        def _do(t):
            if col:
                t.execute(f"UPDATE tasks SET status=?, error_code=?, detail_json=?,"
                          f" {col}=? WHERE job_id=?",
                          (status, error_code, json.dumps(detail or {}, ensure_ascii=False),
                           now_iso(), job_id))
            else:
                t.execute("UPDATE tasks SET status=? WHERE job_id=?", (status, job_id))

        if tx is not None:
            _do(tx)
        else:
            with self.store.tx() as t:
                _do(t)

    def get(self, job_id: str) -> dict | None:
        r = self.store.q1("SELECT * FROM tasks WHERE job_id=?", (job_id,))
        return dict(r) if r else None
