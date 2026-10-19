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
               mode: str | None = None, del_seq: int | None = None,
               force: bool = False) -> Job:
        """投递作业；命中去重路径时不新建。

        `source_path` / `mode`（CH-448）：异步队列的消费参数，独立列存放
        （`detail_json` 是结果/失败明细的契约面，不混入管道数据）。
        """
        with self.store.tx() as tx:
            return self._submit_tx(tx, doc_id, source_key, idem_key,
                                   source_path=source_path, mode=mode,
                                   del_seq=del_seq, force=force)

    def submit_many(self, items, *, force: bool = False) -> list[Job]:
        """批量投递（CH-476 / R7-PERF-h）：整批只开一个 `BEGIN IMMEDIATE`。

        `items` 为 `(doc_id, source_key, idem_key, source_path, mode)` 五元组序列，
        逐条判定与产出顺序和 `submit` 完全一致（含批内重复 doc_id / idem_key 的
        附着语义：前一条插入的行在本事务内对后一条可见）。收益是把 N 次提交
        压成 1 次——WAL 下每次 COMMIT 都要写一条 journal header 并 fsync，
        万件级目录的入队成本几乎全在这 N 次上。
        原子性由此**强于**逐条 `submit`：任一条失败即整批回滚，不留半批队列行。
        """
        jobs: list[Job] = []
        with self.store.tx() as tx:
            # CH-493（P-5）：整批共用一个删除基准。原实现每个作业各查一次
            # `MAX(deletions.id)`，而同一 `BEGIN IMMEDIATE` 事务内该值不可能变
            # （无其它写入者可提交）→ N 次冗余往返改为 1 次，取值逐条相同。
            del_seq = int(tx.execute(
                "SELECT COALESCE(MAX(id),0) AS m FROM deletions").fetchone()["m"])
            for doc_id, source_key, idem_key, source_path, mode in items:
                jobs.append(self._submit_tx(tx, doc_id, source_key, idem_key,
                                            source_path=source_path, mode=mode,
                                            del_seq=del_seq, force=force))
        return jobs

    def _submit_tx(self, tx, doc_id: str, source_key: str = "",
                   idem_key: str | None = None, *, source_path: str | None = None,
                   mode: str | None = None, del_seq: int | None = None,
                   force: bool = False) -> Job:
        """`submit` 的语句序列（运行在调用方事务里）。"""
        if idem_key:
            # CH-485（R9-SVC-3）：幂等键按 (key, doc_id) 绑定。原实现只看 key，
            # 同键不同文档会静默返回旧作业；submit_rejected 更会把在途有效作业
            # 标成 rejected。现在不同 doc_id 各建一行（索引非唯一），同键同文档
            # 才命中，且回报真实终态。
            row = tx.execute(
                "SELECT job_id, doc_id, status FROM tasks"
                " WHERE idem_key=? AND doc_id=? ORDER BY created_at DESC LIMIT 1",
                (idem_key, doc_id)).fetchone()
            if row:
                return Job(row["job_id"], row["doc_id"], row["status"],
                           attached=True, idem_hit=True)
        row = tx.execute(
            f"SELECT job_id FROM tasks WHERE doc_id=? AND status IN {ACTIVE} LIMIT 1",
            (doc_id,)).fetchone()
        if row:
            return Job(row["job_id"], doc_id, "queued", attached=True)
        job_id = "job-" + uuid.uuid4().hex[:12]
        # CH-485（R9-SVC-1）：入队时冻结删除基准。异步消费在转换期间遇到的
        # 更新墓碑（id > del_seq）必须在落库事务内拦截；同步路径由请求入口
        # 的 since_seq 显式传入（不能在这里现取，见 _finish_one 注释）。
        if del_seq is None:
            del_seq = int(tx.execute(
                "SELECT COALESCE(MAX(id),0) AS m FROM deletions").fetchone()["m"])
        try:
            tx.execute(
                "INSERT INTO tasks(job_id, doc_id, source_key, status, idem_key,"
                " created_at, source_path, mode, del_seq, force)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, doc_id, source_key, "queued", idem_key, now_iso(),
                 source_path, mode, int(del_seq), int(bool(force))))
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
             tx=None) -> bool:
        """状态推进，返回本次推进是否真正落到该行（False = 行不存在或已是终态）。

        CH-459（A-4）：`tx` 形参允许并入调用方事务——
        worker._finish_ok 与同步 /jobs 路径把 mark 与入库/事件合为单次提交
        （原口径是每提交一次 fsync；`synchronous` 自 CH-471 / STOR-1 起为 NORMAL，
        合批仍保留——少一次写锁往返并保持崩溃原子性）。

        CH-476（R7-SVC-1，决策 D6）：返回值供调用方做「行还在不在」判定——
        delete_doc 级联 `DELETE FROM tasks WHERE doc_id=?` 会收走在途 running 行，
        落库前先 mark 并查 rowcount==0 即可识别「文档已被删除」，回滚不写 docs，
        杜绝半复活态（正文回来了、任务行没了）。
        """
        if status not in ACTIVE + TERMINAL:
            raise ValueError(f"bad status {status}")
        col = {"running": "started_at", "done": "finished_at",
               "failed": "finished_at", "rejected": "finished_at"}.get(status)

        def _do(t):
            # R6-SVC-3（CH-475，决策 D1）：终态幂等——mark 不得改写已成功（done）或
            # 已拒收（rejected）的作业，杜绝「幂等重执行 / submit_rejected 命中既有
            # done 作业」把成功终态翻成 rejected 的数据安全缺陷。仍允许从 queued/
            # running/failed 迁移：failed→done 是同步 serve 路径与常驻 worker 竞争
            # 同一行时的自纠正（worker 误判 E4001、同步路径实际已落库），不可堵死；
            # 历史无路径孤儿行的 queued→failed 清理（CH-448） likewise 保留。
            # 需重开一个 done 作业走显式 requeue，而非 mark。
            if col:
                t.execute(f"UPDATE tasks SET status=?, error_code=?, detail_json=?,"
                          f" {col}=? WHERE job_id=? AND status NOT IN ('done','rejected')",
                          (status, error_code, json.dumps(detail or {}, ensure_ascii=False),
                           now_iso(), job_id))
            else:
                t.execute("UPDATE tasks SET status=? WHERE job_id=?"
                          " AND status NOT IN ('done','rejected')", (status, job_id))
            # CH-476（R7-SVC-1）：紧随 UPDATE 取游标 rowcount（_Tx.rowcount 优先
            # 最近语句游标）——0 = 行已被 delete_doc 级联收走或已是终态。
            return t.rowcount > 0

        if tx is not None:
            return _do(tx)
        with self.store.tx() as t:
            return _do(t)

    def get(self, job_id: str) -> dict | None:
        r = self.store.q1("SELECT * FROM tasks WHERE job_id=?", (job_id,))
        return dict(r) if r else None
