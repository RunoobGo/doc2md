"""events —— 事件入库 + 内置日志落盘（NFR-19 / NFR-20 同一口径）。

CH-492（A-1）：本模块由 `api._emit` 原样抽出。抽出前 `worker.py` 两处函数内
延迟导入反向引用 `api._emit`，与 `api.py` 三处反向延迟导入构成双向环；抽为
中立模块后 api 与 worker 同向依赖本模块，环被消除。

行为纪律（与原 `api._emit` 逐字节相同，勿改）：
- `tx` 非空时 INSERT 并入调用方事务（CH-459/A-4 的写路径合并），日志行仍由
  本函数先于调用方提交落盘——jsonl 是运维面 at-least-once 语义，DB 才是
  状态真源；
- payload 入库走 `scrub_record`、写日志走写入器自身的 scrub（CH-459/A-12
  统一为「不看键名、只看长度」）。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .ops.logging import scrub_record
from .storage.db import now_iso

if TYPE_CHECKING:                     # 仅静态期解析，运行期零导入成本
    from .ops.logging import RotatingJsonlWriter
    from .storage.db import Store


def emit(store: Store, code: str, doc_id: str | None, job_id: str | None,
         payload: dict, writer: RotatingJsonlWriter | None = None,
         tx=None) -> None:
    """事件入库 + 同记录落内置日志（NFR-19：轮转由服务自身执行，不靠 newsyslog/cron）。

    CH-459（A-4）：`tx` 形参允许把事件插入并入调用方事务（worker._finish_ok 与
    同步 /jobs 路径的 detail/mark/event/入库四写合并为单次提交）。传入 tx 时
    日志行仍由本函数落盘、先于调用方提交——jsonl 是运维面 at-least-once 语义，
    DB 才是状态真源；不传 tx 时行为与从前完全一致（自有事务）。

    payload 不得含正文片段（NFR-20）：入库走 `scrub_record`，写日志走写入器自身的
    scrub——CH-459（A-12）两处已统一为同一实现（不看键名、只看长度）。
    """
    sql = ("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
           " VALUES (?,?,?,?,?)")
    args = (now_iso(), code, doc_id, job_id,
            json.dumps(scrub_record(payload), ensure_ascii=False))
    if tx is not None:
        tx.execute(sql, args)
    else:
        with store.tx() as t:
            t.execute(sql, args)
    if writer is not None:
        writer.write({"code": code, "doc_id": doc_id, "job_id": job_id, **payload})
        writer.flush()
