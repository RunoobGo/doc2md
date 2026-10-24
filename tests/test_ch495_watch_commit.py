"""CH-495（R14-ARCH-1）回归红线：watch 同步提交路径与 api/worker 同契约。

修复前 `cmd_watch` 的同步分支是 `save_ir → submit → mark` 三段独立事务：
无墓碑检查、无 mark-before-save、无 E4091 补偿重建、不发事件——删除与 watch
扫描竞态时文档「半复活」（docs 有行、tasks 无行/态漂移），且 /events 审计面
对 watch 落库完全失明。本文件钉住修复后的三条口径：

1. 转换期间出现墓碑（id > since）→ 整笔回滚，docs 不落行，任务 rejected/E4041，
   发 job_rejected 事件（与 api._finish_one R8-SVC-1 同序）；
2. save_ir 撞 E4091（库内已有编辑）→ conflict 终态 + rebuild_artifact 补偿
   （R9-SVC-2 同口径），退出码 EXIT_BIZ；
3. 成功路径单事务发 job_done 事件（事件表对 watch 不再失明）。

历史缺陷口径见 docs/review-round14-CH-495.md F1。
"""
from __future__ import annotations

import importlib
import json
from typing import ClassVar

import pytest

from doc2md.cli import main as cli_main
from doc2md.ir import IR, Block
from doc2md.storage.db import Store, now_iso


class _FakeTracker:
    def __init__(self, stable_seconds: float = 0.0):
        self.stable_seconds = stable_seconds
        self.files: list[str] = []

    def poll_all(self, _d, _lim):
        return list(self.files)


class _FakeResult:
    ir = IR(doc_id="d" * 16, source_key="s" * 24, source_name="a.pdf",
            blocks=[Block(id="b1", text="正文")])
    meta: ClassVar[dict] = {}
    gate: ClassVar[dict] = {}
    doc_id = "d" * 16
    source_key = "s" * 24


@pytest.fixture()
def watch_env(tmp_path, monkeypatch):
    """真实 Store/Scheduler + 打桩 tracker/convert 的 watch --once 环境。"""
    from doc2md import cli  # noqa: F401
    from doc2md import worker as worker_mod  # noqa: F401 — 先于 save_ir 打桩导入

    work_root = tmp_path / "work"
    work_root.mkdir()
    src = tmp_path / "drop" / "a.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF-1.7\n")

    tracker = _FakeTracker()
    tracker.files = [str(src)]
    monkeypatch.setattr("doc2md.security.watcher.StabilityTracker",
                        lambda stable_seconds=0.0: tracker)
    convert_mod = importlib.import_module("doc2md.pipeline.convert")
    monkeypatch.setattr(convert_mod, "convert",
                        lambda *_a, **_kw: _FakeResult())

    def run() -> int:
        return cli_main(["watch", str(src.parent), "--once",
                         "--root", str(work_root)])

    return run, work_root, tracker


def _rows(work_root, sql, args=()):
    store = Store(work_root / "state.sqlite")
    try:
        return [dict(r) for r in store.q(sql, args)]
    finally:
        store.close()


@pytest.mark.gate
def test_watch_tombstone_blocks_commit(watch_env, monkeypatch):
    """转换期间删除落地 → 墓碑拦截整笔事务，正文不写回，rejected 留痕。"""
    run, work_root, _ = watch_env
    db = work_root / "state.sqlite"

    def convert_with_race(*_a, **_kw):
        store = Store(db)
        try:
            with store.tx() as tx:
                tx.execute(
                    "INSERT INTO deletions(doc_id, source_key, actor, ts, kind,"
                    " cascade_json) VALUES (?,?,?,?,?,?)",
                    ("d" * 16, "s" * 24, "tester", now_iso(), "doc", "{}"))
        finally:
            store.close()
        return _FakeResult()

    convert_mod = importlib.import_module("doc2md.pipeline.convert")
    monkeypatch.setattr(convert_mod, "convert", convert_with_race)

    assert run() == 0
    assert _rows(work_root, "SELECT * FROM docs") == []
    tasks = _rows(work_root, "SELECT status, error_code FROM tasks"
                             " WHERE doc_id=?", ("d" * 16,))
    assert tasks and tasks[0]["status"] == "rejected"
    assert tasks[0]["error_code"] == "E4041"
    events = _rows(work_root, "SELECT code FROM events ORDER BY rowid")
    assert {"code": "job_rejected"} in events
    assert {"code": "job_done"} not in events


@pytest.mark.gate
def test_watch_e4091_conflict_rebuilds(watch_env, monkeypatch):
    """save_ir 撞库内编辑 → conflict 终态 + rebuild_artifact 补偿 + EXIT_BIZ。"""
    from doc2md import artifacts as artifacts_mod
    from doc2md.errors import E4091_DocOverwrite
    from doc2md.storage import repo as repo_mod

    run, work_root, _ = watch_env

    def _raise(*_a, **_kw):
        raise E4091_DocOverwrite("edited blocks present",
                                 {"edited_blocks": ["b1"]})

    monkeypatch.setattr(repo_mod, "save_ir", _raise)
    rebuilt: list[str] = []
    monkeypatch.setattr(artifacts_mod, "rebuild_artifact",
                        lambda _s, doc_id, _c, _r: rebuilt.append(doc_id))

    rc = run()
    from doc2md.cli import EXIT_BIZ
    assert rc == EXIT_BIZ
    assert rebuilt == ["d" * 16]
    tasks = _rows(work_root, "SELECT status, error_code FROM tasks"
                             " WHERE doc_id=?", ("d" * 16,))
    assert tasks and tasks[0]["status"] == "rejected"
    assert tasks[0]["error_code"] == "E4091"


@pytest.mark.gate
def test_watch_success_emits_job_done(watch_env, monkeypatch):
    """成功路径：单事务 mark(done)+save_ir+job_done 事件，stdout 契约键不变。"""
    from doc2md.storage import repo as repo_mod

    run, work_root, _ = watch_env
    saved: list[dict] = []
    monkeypatch.setattr(repo_mod, "save_ir",
                        lambda *_a, **kw: saved.append(kw))

    assert run() == 0
    assert saved and saved[0].get("tx") is not None, "save_ir 必须并入单事务"
    tasks = _rows(work_root, "SELECT status FROM tasks WHERE doc_id=?",
                  ("d" * 16,))
    assert tasks and tasks[0]["status"] == "done"
    events = _rows(work_root, "SELECT code, payload_json FROM events"
                              " ORDER BY rowid")
    done = [e for e in events if e["code"] == "job_done"]
    assert len(done) == 1
    payload = json.loads(done[0]["payload_json"])
    assert payload["source"] == "watch" and payload["blocks"] == 1
