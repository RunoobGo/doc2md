"""tests/test_ch499_lease.py — CH-499 / B-27：running 行的所有权与租约。

B-27 的病灶（CH-498 双进程实测取证）：`reclaim_stale` 只看 `started_at` 的年龄，
于是「合法但超过窗口的在途转换」会被第二个 worker 判成遗骸并重新认领——同一件
重跑一遍（低配机白烧 CPU），先落库的一方让后落库的一方走 `mark rowcount=0 →
E4041 → 回滚`，用户面留下一条与终态（`done`）矛盾的假 `rejected`。

修法（`DB_VERSION` 9→10）：`tasks` 增 `owner` + `lease_expires_at`。认领时打租约，
转换在途期间由续租线程按间隔前移expiry；`reclaim_stale` 改为三条判据——
① 租约已过期（进程死了/被杀，心跳停）② 无租约的历史行沿用 `started_at` 窗口
③ 超过 `[perf].job_lease_cap_minutes` 硬上限（进程活着但卡死的兜底）。

红线形态纪律：每条都必须能失败（CH-498 §9.1 口径），且不用 `t.Skip` 掩盖平台差异。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from doc2md.scheduler import Scheduler
from doc2md.storage.db import DB_VERSION, Store
from doc2md.worker import _lease_keeper, claim, reclaim_stale


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _queued(store: Store, n: int = 1) -> list[str]:
    sched = Scheduler(store)
    return [sched.submit(f"doc{i}", f"src{i}", source_path=f"/tmp/a{i}.pdf").job_id
            for i in range(n)]


def _row(store: Store, job_id: str) -> dict:
    r = store.q1("SELECT status, owner, lease_expires_at, started_at FROM tasks"
                 " WHERE job_id=?", (job_id,))
    return dict(r)


@pytest.mark.gate
def test_claim_with_lease_stamps_owner_and_expiry(tmp_path):
    """打租约的认领：owner 与 expiry 落列，expiry ≈ now + lease_seconds。"""
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 1)
    claim(store, 1, lease_seconds=60, owner="w-test")
    row = _row(store, jid)
    assert row["status"] == "running"
    assert row["owner"] == "w-test"
    assert row["lease_expires_at"]
    skew = (_parse(row["lease_expires_at"]) - datetime.now(timezone.utc)
            - timedelta(seconds=60)).total_seconds()
    assert abs(skew) < 5.0, f"expiry 偏差过大：{skew}s"
    store.close()


@pytest.mark.gate
def test_claim_without_lease_keeps_legacy_shape(tmp_path):
    """旧调用方（`claim(store, limit)`）零行为变化：两列保持空串。

    这条是「不改历史语义」的钉子——`tests/test_perf_lowend.py`、`test_ch448`
    时代的调用不带租约，回收必须继续走 `started_at` 窗口分支。
    """
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 2)[:1]
    claim(store, 1)
    row = _row(store, jid)
    assert row["status"] == "running"
    assert row["owner"] == "" and row["lease_expires_at"] == ""
    store.close()


@pytest.mark.gate
def test_reclaim_skips_row_with_live_lease(tmp_path):
    """B-27 本体：`started_at` 远超窗口但租约仍在续 → 不得回收、不得重复转换。"""
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 1)
    claim(store, 1, lease_seconds=300, owner="w-alive")
    # 把 started_at 推到 2020 年：旧口径（只看年龄）必然误判成遗骸
    with store.tx() as tx:
        tx.execute("UPDATE tasks SET started_at=? WHERE job_id=?",
                   ("2020-01-01T00:00:00.000+00:00", jid))
    cfg = {"perf": {"stale_job_minutes": 1, "job_lease_cap_minutes": 0}}
    assert reclaim_stale(store, cfg) == 0
    assert _row(store, jid)["status"] == "running"
    store.close()


@pytest.mark.gate
def test_reclaim_takes_expired_lease_and_clears_ownership(tmp_path):
    """真死进程（心跳停 → 租约过期）仍被回收，且回收后所有权清空可再认领。"""
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 1)
    claim(store, 1, lease_seconds=60, owner="w-dead")
    with store.tx() as tx:
        tx.execute("UPDATE tasks SET lease_expires_at=? WHERE job_id=?",
                   (_iso(datetime.now(timezone.utc) - timedelta(seconds=5)), jid))
    cfg = {"perf": {"stale_job_minutes": 30}}
    assert reclaim_stale(store, cfg) == 1
    row = _row(store, jid)
    assert row["status"] == "queued"
    assert row["owner"] == "" and row["lease_expires_at"] == ""
    assert row["started_at"] is None
    # 回队列后可被新 owner 正常认领（闭环，不是「回收即卡死」）
    (got,) = claim(store, 1, lease_seconds=60, owner="w-new")
    assert got["job_id"] == jid and _row(store, jid)["owner"] == "w-new"
    store.close()


@pytest.mark.gate
def test_reclaim_legacy_unleased_row_still_uses_started_at_window(tmp_path):
    """无租约的历史行（旧库升级 / 外部写入）沿用 `started_at` 窗口，计数不虚报。"""
    store = Store(tmp_path / "state.sqlite")
    ts = _iso(datetime.now(timezone.utc) - timedelta(minutes=99))
    with store.tx() as tx:
        tx.execute("INSERT INTO tasks(job_id, doc_id, source_key, status, created_at,"
                   " started_at, source_path, mode) VALUES (?,?,?,?,?,?,?,?)",
                   ("job-legacy", "d1", "s1", "running", ts, ts, "/tmp/a.pdf", "auto"))
    cfg = {"perf": {"stale_job_minutes": 30}}
    assert reclaim_stale(store, cfg) == 1
    assert reclaim_stale(store, cfg) == 0
    store.close()


@pytest.mark.gate
def test_reclaim_hard_cap_rescues_wedged_worker(tmp_path):
    """进程活着但卡死（租约一直有效）→ 硬上限到点仍回收；`cap=0` 即关闭该判据。"""
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 1)
    claim(store, 1, lease_seconds=86_400, owner="w-wedged")
    with store.tx() as tx:
        tx.execute("UPDATE tasks SET started_at=? WHERE job_id=?",
                   (_iso(datetime.now(timezone.utc) - timedelta(minutes=90)), jid))
    assert reclaim_stale(store, {"perf": {"stale_job_minutes": 30,
                                          "job_lease_cap_minutes": 1}}) == 1
    assert _row(store, jid)["status"] == "queued"

    # 第二家：同一形态但 cap=0（不设上限）→ 不回收（租约仍新、窗口未到）
    store2 = Store(tmp_path / "state2.sqlite")
    (j2,) = _queued(store2, 1)
    claim(store2, 1, lease_seconds=86_400, owner="w-wedged")
    with store2.tx() as tx:
        tx.execute("UPDATE tasks SET started_at=? WHERE job_id=?",
                   (_iso(datetime.now(timezone.utc) - timedelta(minutes=90)), j2))
    assert reclaim_stale(store2, {"perf": {"stale_job_minutes": 180,
                                           "job_lease_cap_minutes": 0}}) == 0
    store.close()
    store2.close()


@pytest.mark.gate
def test_reclaim_ignores_rows_without_source_path(tmp_path):
    """同步 serve 路径写的 running 行不带消费路径语义，任何判据都不得动它（CH-448 口径）。"""
    store = Store(tmp_path / "state.sqlite")
    ts = _iso(datetime.now(timezone.utc) - timedelta(minutes=999))
    with store.tx() as tx:
        tx.execute("INSERT INTO tasks(job_id, doc_id, status, created_at, started_at,"
                   " source_path, lease_expires_at) VALUES (?,?,?,?,?,?,?)",
                   ("job-sync", "d1", "running", ts, ts, None, ""))
    assert reclaim_stale(store, {"perf": {"stale_job_minutes": 1,
                                          "job_lease_cap_minutes": 1}}) == 0
    assert _row(store, "job-sync")["status"] == "running"
    store.close()


@pytest.mark.gate
def test_lease_keeper_renews_and_leaves_no_thread(tmp_path):
    """续租线程：在途期间前移 expiry；退出即回收线程，且不碰已终态/他人作业行。"""
    store = Store(tmp_path / "state.sqlite")
    jids = _queued(store, 2)
    for jid in jids:
        claim(store, 1, lease_seconds=60, owner="w-keeper")
    before = _row(store, jids[0])["lease_expires_at"]
    baseline = {t.name for t in threading.enumerate()}
    with _lease_keeper(store, "w-keeper", jids, 60, interval=0.05):
        time.sleep(0.25)
        after = _row(store, jids[0])["lease_expires_at"]
    assert _parse(after) > _parse(before), "在途期间租约必须被前移"
    assert {t.name for t in threading.enumerate()} == baseline, "续租线程必须随退出回收"

    # 终态行不得被续租复活（租约 UPDATE 带 status='running' 守卫）
    sched = Scheduler(store)
    sched.mark(jids[0], "done")
    with _lease_keeper(store, "w-keeper", jids, 60, interval=0.05):
        time.sleep(0.15)
    assert _row(store, jids[0])["status"] == "done"
    store.close()


@pytest.mark.gate
def test_lease_keeper_does_not_steal_other_owner_rows(tmp_path):
    """续租按 owner 收敛：另一进程认领的同 job_id 行不被本进程心跳改写。"""
    store = Store(tmp_path / "state.sqlite")
    (jid,) = _queued(store, 1)
    claim(store, 1, lease_seconds=60, owner="w-a")
    with store.tx() as tx:
        tx.execute("UPDATE tasks SET owner='w-b' WHERE job_id=?", (jid,))
    stale_before = _row(store, jid)["lease_expires_at"]
    with _lease_keeper(store, "w-a", [jid], 60, interval=0.05):
        time.sleep(0.2)
    assert _row(store, jid)["lease_expires_at"] == stale_before, "非本主行不得续租"
    store.close()


@pytest.mark.gate
def test_db_v10_adds_lease_columns_and_migrates_old_library(tmp_path):
    """新库自带两列；v9 形态存量库开库幂等补齐并升到 DB_VERSION。"""
    import sqlite3

    assert DB_VERSION == 10
    fresh = tmp_path / "state.sqlite"
    store = Store(fresh)
    names = {r["name"] for r in store.q("PRAGMA table_info(tasks)")}
    assert {"owner", "lease_expires_at"} <= names
    assert store.q1("PRAGMA user_version")[0] == DB_VERSION
    store.close()

    # 造 v9 形态：删掉两列 + 把 user_version 回拨
    con = sqlite3.connect(fresh)
    con.execute("ALTER TABLE tasks DROP COLUMN owner")
    con.execute("ALTER TABLE tasks DROP COLUMN lease_expires_at")
    con.execute("PRAGMA user_version=9")
    con.commit()
    con.close()

    store2 = Store(fresh)
    names2 = {r["name"] for r in store2.q("PRAGMA table_info(tasks)")}
    assert {"owner", "lease_expires_at"} <= names2
    assert store2.q1("PRAGMA user_version")[0] == DB_VERSION
    # 默认值口径：补列不得把既有行写成 NULL（判定按空串比较）
    with store2.tx() as tx:
        tx.execute("INSERT INTO tasks(job_id, doc_id, status, created_at)"
                   " VALUES ('j-m','d-m','queued','2026-01-01T00:00:00.000+00:00')")
    row = store2.q1("SELECT owner, lease_expires_at FROM tasks WHERE job_id='j-m'")
    assert row["owner"] == "" and row["lease_expires_at"] == ""
    store2.close()
    # 再开一次仍幂等（DDL 无条件重放 + 补列探测不报错）
    Store(fresh).close()


@pytest.mark.gate
def test_lease_config_key_is_three_way_contract():
    """`[perf].job_lease_cap_minutes` 三处同源（DEFAULT_TOML / SPEC / 分发样例）。"""
    import tomllib
    from pathlib import Path

    from doc2md import config
    from doc2md.config import loader
    from doc2md.config.default_toml import DEFAULT_TOML

    key = "perf.job_lease_cap_minutes"
    assert key in loader.SPEC
    assert key in {k for k in loader._dotted_keys(tomllib.loads(DEFAULT_TOML)) if "." in k}
    ex = Path(__file__).resolve().parent.parent / "config" / "doc2md.example.toml"
    assert key in {k for k in loader._dotted_keys(
        tomllib.loads(ex.read_text(encoding="utf-8"))) if "." in k}
    kind, bounds = loader.SPEC[key]
    assert kind == "int" and bounds[0] == 0        # 0 = 不设上限，必须是合法值
    assert int(config.get(tomllib.loads(DEFAULT_TOML), key)) >= 0
    with pytest.raises(config.ConfigError):
        config.validate({"perf": {"job_lease_cap_minutes": "x"}})
