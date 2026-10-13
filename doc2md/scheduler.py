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
               idem_key: str | None = None) -> Job:
        """投递作业；命中去重路径时不新建。"""
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
                    " created_at) VALUES (?,?,?,?,?,?)",
                    (job_id, doc_id, source_key, "queued", idem_key, now_iso()))
            except sqlite3.IntegrityError:
                # 并发窗口：唯一索引挡住第二份进行中作业 → 附着
                row = tx.execute(
                    f"SELECT job_id FROM tasks WHERE doc_id=? AND status IN {ACTIVE} LIMIT 1",
                    (doc_id,)).fetchone()
                return Job(row["job_id"], doc_id, "queued", attached=True)
        return Job(job_id, doc_id, "queued")

    def mark(self, job_id: str, status: str, error_code: str = "", detail: dict | None = None):
        if status not in ACTIVE + TERMINAL:
            raise ValueError(f"bad status {status}")
        col = {"running": "started_at", "done": "finished_at",
               "failed": "finished_at", "rejected": "finished_at"}.get(status)
        with self.store.tx() as tx:
            if col:
                tx.execute(f"UPDATE tasks SET status=?, error_code=?, detail_json=?,"
                           f" {col}=? WHERE job_id=?",
                           (status, error_code, json.dumps(detail or {}, ensure_ascii=False),
                            now_iso(), job_id))
            else:
                tx.execute("UPDATE tasks SET status=? WHERE job_id=?", (status, job_id))

    def get(self, job_id: str) -> dict | None:
        r = self.store.q1("SELECT * FROM tasks WHERE job_id=?", (job_id,))
        return dict(r) if r else None

    def active_for(self, doc_id: str) -> dict | None:
        r = self.store.q1(
            f"SELECT * FROM tasks WHERE doc_id=? AND status IN {ACTIVE} LIMIT 1", (doc_id,))
        return dict(r) if r else None
