"""CH-498 / B-17：多方并发首开全新 state.sqlite 的建库竞态红线。

根因（本机实测）：`_new_conn` 里那句裸 `PRAGMA journal_mode=WAL` 在另一进程正创建/迁移
同一库文件时抛 `sqlite3.OperationalError: database is locked`。原实现把它当未知异常上抛，
CLI 侧落 E5000、常驻 worker 直接退出。

机制校正（本轮量出来的，和直觉相反）：这条 PRAGMA **是**按连接 busy_timeout 等待的——
`timeout=30.0` 下单次撞锁要 31.0s 才抛「locked」（裸连接默认 5s 则 5s 抛）。所以修复不能
只是「加个重试」：沿用 30s 单次等待 × 200 次上限 = 最长挂起约 100 分钟，把「30s 后退出的
故障」换成「永远不返回的故障」。`Store._set_wal` 的做法是切换阶段先把单次等待压到
`WAL_SWITCH_BUSY_MS`=250ms，再以 `WAL_RETRY_DEADLINE_S`=60s 为总预算重试，超时 fail-closed
抛 RuntimeError；切完由 `_new_conn` 恢复 busy_timeout=30000。

红线形态：两项靠竞态窗口命中（同进程多线程 + 4 个真实子进程跑 `doc2md work --root <fresh>
--status`），压不中窗口就会假绿（修复前本机 12 轮里红 8 轮，非轮轮红），故再补两项确定性的：
「对方持锁超过预算 → 限时 RuntimeError」和「对方只持 0.4s → 等到手并切到 WAL」，都用独立连接
`BEGIN EXCLUSIVE` 把锁做成必然。四项都拒绝静默退回 delete/legacy（实测 journal_mode）。
"""
from __future__ import annotations

import concurrent.futures as cf
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _journal_mode(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.gate
def test_concurrent_first_open_of_fresh_store_is_safe(tmp_path):
    from doc2md.storage.db import Store

    db = tmp_path / "state.sqlite"
    barrier = cf.ThreadPoolExecutor(max_workers=8)

    def open_it(_i):
        store = Store(db)
        return store.q1("SELECT COUNT(*) n FROM tasks")["n"]

    try:
        results = list(barrier.map(open_it, range(8)))
    finally:
        barrier.shutdown()
    assert results == [0] * 8, results
    assert _journal_mode(db) == "wal"


@pytest.mark.gate
def test_concurrent_cli_first_open_fresh_root(tmp_path):
    """4 个真实 `doc2md work --status` 进程同时首开同一全新根，必须全部退 0。"""
    for d in ("src", "out", "logs", "tmp"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    binp = REPO_ROOT / ".venv/bin/doc2md"
    if not binp.exists():                     # CI/venv 路径差异：用当前解释器兜底
        cmd = [sys.executable, "-m", "doc2md.cli"]   # 包无 __main__.py，`-m doc2md` 退 1
    else:
        cmd = [str(binp)]
    procs = [subprocess.Popen(cmd + ["work", "--root", str(tmp_path), "--status"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(4)]
    outs = [(p.wait(timeout=120), p.communicate(timeout=30)[1]) for p in procs]
    bad = [(rc, err) for rc, err in outs if rc != 0]
    assert not bad, bad
    assert _journal_mode(tmp_path / "state.sqlite") == "wal"


@pytest.mark.gate
def test_wal_switch_fails_closed_within_the_budget(tmp_path, monkeypatch):
    """确定性腿：对方持锁超过总预算时，必须**限时**失败并且失败得可识别。

    上面两项靠并发窗口命中，压不中就是假绿（修复前本机 12 轮里红 8 轮，不是轮轮红）；
    本项把「对方一直持独占」做成必然，因此它既是修复的红线，也钉死修复自身没有把
    「30s 后 E5000 退出」换成「挂起 100 分钟」：

    - 实测机制：`PRAGMA journal_mode=WAL` 会按连接 busy_timeout 等待，`timeout=30.0`
      下单次撞锁要 31.0s 才抛 `database is locked`。修复前 `_new_conn` 直接用这一句
      → 上抛的是 `sqlite3.OperationalError`（CLI 侧落 E5000），本项判红。
    - 修复后：切换阶段先把单次等待压到 250ms，再以总预算重试；预算被 monkeypatch 到
      0.5s → 限时 `RuntimeError`（fail-closed 文案含「locked」），且必须远快于 30s。
    - 失败面还得可恢复：对手松手后同一文件必须能正常首开，不许留下「毒库」。
    """
    from doc2md.storage import db as dbmod
    from doc2md.storage.db import Store

    monkeypatch.setattr(dbmod, "WAL_RETRY_DEADLINE_S", 0.5)
    db = tmp_path / "state.sqlite"
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")          # 模拟对方长期持独占建库
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="locked"):
            Store(db)
        elapsed = time.monotonic() - t0
        # 8s 是「机制」判据而非「结果」判据：单次 busy 等待若被退回 30s（即「切换阶段先
        # 压小 busy_timeout」这一步丢失），一次撞锁就吃满 31s，本行直接红——只断言
        # 「抛的是 RuntimeError」抓不到这种回归。留 8s 是给低配 CI 机的负载余量。
        assert elapsed < 8.0, f"fail-closed 没有守住总预算：耗时 {elapsed:.1f}s"
        holder.execute("COMMIT")               # 对手松手
        store = Store(db)
        assert store.q1("SELECT COUNT(*) n FROM tasks")["n"] == 0
        assert _journal_mode(db) == "wal"
        store.close()
    finally:
        holder.close()


@pytest.mark.gate
def test_transient_exclusive_holder_is_waited_out(tmp_path):
    """反向契约：毫秒级的对手（只持锁 0.4s）必须**等到手**，不是报错也不是退出。

    B-17 的真实生产形态就是「cron / serve / work 同时首开一个新根」，赢家几十毫秒就
    松手。上一项证明超时不挂起，本项证明短窗口不致命——终态必须是 wal 且队列可读。
    """
    from doc2md.storage.db import Store

    db = tmp_path / "state.sqlite"
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    pool = cf.ThreadPoolExecutor(max_workers=1)

    def open_it():
        store = Store(db)
        return store.q1("SELECT COUNT(*) n FROM tasks")["n"]

    fut = pool.submit(open_it)
    try:
        time.sleep(0.4)
        holder.execute("COMMIT")
        holder.close()
        assert fut.result(timeout=30) == 0
        assert _journal_mode(db) == "wal"
    finally:
        pool.shutdown()
