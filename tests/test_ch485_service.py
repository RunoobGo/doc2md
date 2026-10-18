"""CH-485 / 数据安全：DB8、异步 drift 删除守护、删前快照生命周期。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from doc2md.ir import IR, Block
from doc2md.scheduler import Scheduler
from doc2md.storage.db import DB_VERSION, Store
from doc2md.storage.repo import delete_doc, save_ir
from doc2md.worker import _finish_ok

pytestmark = [pytest.mark.milestone("M8")]


@pytest.mark.gate
def test_db8_has_del_seq_and_artifact_columns(store):
    assert DB_VERSION == 8
    tcols = {r["name"] for r in store.q("PRAGMA table_info(tasks)")}
    assert {"del_seq", "force"} <= tcols
    bcols = {r["name"] for r in store.q("PRAGMA table_info(blocks)")}
    assert {"links_json", "font_json", "provenance_json"} <= bcols
    dcols = {r["name"] for r in store.q("PRAGMA table_info(docs)")}
    assert "gate_json" in dcols


def _ir(doc_id: str, source_key: str, text: str = "content") -> IR:
    return IR(doc_id=doc_id, source_key=source_key, source_name="x.pdf",
              blocks=[Block(id="b1", text=text)])


@pytest.mark.gate
def test_async_drift_delete_does_not_resurrect(tmp_path):
    """入队 D1、转换期内容变成 D2、删除 D2：异步落库必须被墓碑拦截。"""
    st = Store(tmp_path / "state.sqlite")
    D1, D2 = "1" * 16, "2" * 16
    src1, src2 = "1" * 24, "2" * 24
    save_ir(st, _ir(D2, src2, "old D2"))
    sched = Scheduler(st)
    j = sched.submit(D1, src1, source_path="/tmp/x.pdf")
    task = sched.get(j.job_id)
    assert task["del_seq"] == 0
    delete_doc(st, D2, "tester", tmp_path)
    ir2 = _ir(D2, src2, "resurrected D2")
    job = {"job_id": j.job_id, "doc_id": D1, "source_key": src1,
           "path": "/tmp/x.pdf", "del_seq": task["del_seq"]}
    res = {"doc_id": D2, "source_key": src2, "status": "ok",
           "zip": "/tmp/x.zip", "blocks": 1, "code": ""}
    with pytest.raises(Exception) as ei:
        _finish_ok(st, sched, job, res, ir2)
    from doc2md.errors import E4041_NotFound
    assert isinstance(ei.value, E4041_NotFound)
    assert st.q1("SELECT 1 FROM docs WHERE doc_id=?", (D2,)) is None
    assert sched.get(j.job_id)["status"] == "queued"
    st.close()


@pytest.mark.gate
def test_async_drift_delete_before_enqueue_still_allows(tmp_path):
    """删除发生在入队之前：del_seq 已包含墓碑，后续正常转换不得被误拒。"""
    st = Store(tmp_path / "state.sqlite")
    with st.tx() as tx:
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind)"
                   " VALUES (?,?,?,?,?)", ("dead", "dead", "t", "2026-01-01", "doc"))
    D1, D2 = "1" * 16, "2" * 16
    sched = Scheduler(st)
    j = sched.submit(D1, "1" * 24, source_path="/tmp/x.pdf")
    task = sched.get(j.job_id)
    assert task["del_seq"] == 1
    job = {"job_id": j.job_id, "doc_id": D1, "source_key": "1" * 24,
           "path": "/tmp/x.pdf", "del_seq": task["del_seq"]}
    res = {"doc_id": D2, "source_key": "2" * 24, "status": "ok",
           "zip": "/tmp/x.zip", "blocks": 1, "code": ""}
    _finish_ok(st, sched, job, res, _ir(D2, "2" * 24, "new"))
    assert st.q1("SELECT 1 FROM docs WHERE doc_id=?", (D2,)) is not None
    st.close()


def _old(p: Path, days: int = 30) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(p, (ts, ts))


@pytest.mark.gate
def test_cron_rotates_old_predelete_and_pre_restore(tmp_path, tmp_cfg):
    from doc2md import cron
    root = Path(tmp_cfg["paths"]["root"])
    backups = Path(tmp_cfg["ops"]["backup_local_dir"])
    backups.mkdir(parents=True, exist_ok=True)
    pre = backups / "predelete-20200101T000000Z-deadbeef.sqlite"
    rst = backups / "pre-restore-20200101T000000Z-deadbeef.sqlite"
    pre.write_bytes(b"deleted-content")
    rst.write_bytes(b"old-db")
    _old(pre)
    _old(rst)
    rep = cron.run(tmp_cfg, root, mode="daily", with_backup=True)
    assert rep.ok, rep.steps
    assert not pre.exists(), "过期 predelete 必须被 cron 轮出"
    assert not rst.exists(), "过期 pre-restore 必须被 cron 轮出"
    rot = next(s for s in rep.steps if s.get("step") == "rotation")
    assert any("predelete-20200101" in x for x in rot["predelete_removed"])


@pytest.mark.gate
def test_predelete_snapshot_is_0600(tmp_path, tmp_cfg):
    from doc2md.ops.backup import BackupPlan
    db = tmp_path / "state.sqlite"
    Store(db).close()
    plan = BackupPlan(local_dir=tmp_path / "backups", external_dir=None,
                      work_root=tmp_path, cfg=tmp_cfg)
    snap = plan.predelete_snapshot(db)
    assert (snap.stat().st_mode & 0o777) == 0o600


@pytest.mark.gate
def test_delete_missing_doc_does_not_snapshot(api_client, headers, tmp_cfg):
    client, tokens = api_client
    backups = Path(tmp_cfg["ops"]["backup_local_dir"])
    r = client.delete("/docs/does-not-exist?confirm=true",
                      headers=headers(tokens, "admin"))
    assert r.status_code == 404
    assert not list(backups.glob("predelete-*.sqlite"))


@pytest.mark.gate
def test_search_full_mode_reaches_beyond_index_window(tmp_path):
    from doc2md.storage.repo import search
    st = Store(tmp_path / "state.sqlite")
    text = "A" * 8500 + "TAILMARK" + "B" * 20
    save_ir(st, IR(doc_id="c" * 16, source_key="c" * 24,
                   blocks=[Block(id="b1", text=text)]))
    assert search(st, "TAILMARK")["total"] == 0
    assert search(st, "TAILMARK", full=True)["total"] == 1
    st.close()


@pytest.mark.gate
def test_idempotency_reject_does_not_cancel_active_job(tmp_path):
    st = Store(tmp_path / "state.sqlite")
    sched = Scheduler(st)
    valid = sched.submit("d" * 16, "d" * 24, idem_key="shared")
    rej = sched.submit_rejected("bad.pdf", "E4221", idem_key="shared")
    assert rej.job_id != valid.job_id
    assert sched.get(valid.job_id)["status"] == "queued"
    assert sched.get(rej.job_id)["status"] == "rejected"
    st.close()


@pytest.mark.gate
def test_async_force_allows_overwriting_edits(tmp_path):
    st = Store(tmp_path / "state.sqlite")
    ir = IR(doc_id="e" * 16, source_key="e" * 24,
            blocks=[Block(id="b1", text="旧")])
    save_ir(st, ir)
    with st.tx() as tx:
        tx.execute("UPDATE blocks SET text='编辑', revision=2 WHERE doc_id=?",
                   (ir.doc_id,))
    sched = Scheduler(st)
    j = sched.submit(ir.doc_id, ir.source_key, source_path="/tmp/x.pdf", force=True)
    task = sched.get(j.job_id)
    assert task["force"] == 1
    job = {"job_id": j.job_id, "doc_id": ir.doc_id, "source_key": ir.source_key,
           "path": "/tmp/x.pdf", "del_seq": task["del_seq"], "force": True}
    res = {"doc_id": ir.doc_id, "source_key": ir.source_key, "status": "ok",
           "zip": "/tmp/x.zip", "blocks": 1, "code": ""}
    _finish_ok(st, sched, job, res, IR(doc_id=ir.doc_id,
                                       source_key=ir.source_key,
                                       blocks=[Block(id="b1", text="新")]))
    assert st.q1("SELECT text FROM blocks WHERE doc_id=?", (ir.doc_id,))["text"] == "新"
    st.close()
