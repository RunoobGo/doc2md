"""CH-490 / 本机可实施的遗留项：产物锁心跳、编辑预检、运行期路径冻结。"""
from __future__ import annotations

import copy
import os
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.milestone("DoD")]


@pytest.mark.gate
def test_artifact_lock_heartbeat_prevents_stale_steal(tmp_path):
    """持锁超过 stale 窗口但心跳存活时，第二个线程不得抢锁。"""
    from doc2md.api import _artifact_lock

    a_acquired = threading.Event()
    b_acquired = threading.Event()
    release_a = threading.Event()
    peak = 0
    active = 0
    guard = threading.Lock()

    def enter(flag):
        nonlocal peak, active
        with guard:
            active += 1
            peak = max(peak, active)
        flag.set()

    def leave():
        nonlocal active
        with guard:
            active -= 1

    def holder_a():
        with _artifact_lock(tmp_path, "doc-lock", timeout=2,
                            stale_seconds=0.15, heartbeat_seconds=0.05):
            enter(a_acquired)
            release_a.wait(2)
        leave()

    def holder_b():
        a_acquired.wait(2)
        with _artifact_lock(tmp_path, "doc-lock", timeout=2,
                            stale_seconds=0.15, heartbeat_seconds=0.05):
            enter(b_acquired)
        leave()

    ta = threading.Thread(target=holder_a)
    tb = threading.Thread(target=holder_b)
    ta.start()
    tb.start()
    assert a_acquired.wait(1)
    time.sleep(0.35)                       # 超过 stale=0.15，心跳应续命
    assert not b_acquired.is_set(), "心跳未阻止陈旧抢锁"
    release_a.set()
    ta.join(3)
    tb.join(3)
    assert b_acquired.is_set() and peak == 1, (peak, b_acquired.is_set())


@pytest.mark.gate
def test_api_precheck_skips_parsing_on_edited_doc(api_client, headers, saved_doc,
                                                  small_pdf, monkeypatch):
    """编辑冲突在转换前判定：不解析源文件、不覆盖旧 zip，响应仍是 conflict/202。"""
    import importlib

    client, tokens, doc_id = saved_doc
    h = headers(tokens, "editor")
    doc = client.get(f"/docs/{doc_id}", headers=h).get_json()
    block = next(b for b in doc["blocks"] if b["text"].strip())
    marker = "CH490-PRECHECK-MARKER"
    r = client.patch(f"/docs/{doc_id}/blocks/{block['id']}",
                     json={"base_revision": block["revision"], "text": marker},
                     headers=h)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]

    convert_mod = importlib.import_module("doc2md.pipeline.convert")

    def forbidden(*_a, **_kw):
        raise AssertionError("precheck 命中后不得进入 read_any")

    monkeypatch.setattr(convert_mod, "read_any", forbidden)
    r = client.post("/jobs", json={"path": str(small_pdf)}, headers=h)
    assert r.status_code == 202, r.get_data(as_text=True)[:300]
    job = r.get_json()["jobs"][0]
    assert job["status"] == "conflict" and job["code"] == "E4091", job
    zp = Path(client.application.config["doc2md"]["cfg"]["paths"]["out"]) / f"{doc_id}.zip"
    import zipfile
    with zipfile.ZipFile(zp) as z:
        md = z.read(f"{doc_id}/content.md").decode("utf-8")
    assert marker in md, "预检不得覆盖编辑后的旧 zip"


@pytest.mark.gate
def test_batch_convert_classifies_precheck_conflict(monkeypatch, tmp_path):
    import importlib

    from doc2md.errors import E4091_DocOverwrite
    from doc2md.pipeline import batch

    convert_mod = importlib.import_module("doc2md.pipeline.convert")

    def conflict(*_a, **_kw):
        raise E4091_DocOverwrite("edited", detail={
            "doc_id": "d" * 16, "source_key": "s" * 24, "edited_blocks": 2})

    monkeypatch.setattr(convert_mod, "convert", conflict)
    out = batch._convert_one(str(tmp_path / "x.pdf"), None, None, None)
    assert out["status"] == "conflict" and out["code"] == "E4091", out
    assert out["doc_id"] == "d" * 16 and out["source_key"] == "s" * 24
    assert out["edited_blocks"] == 2, out


@pytest.mark.gate
def test_sqlite_cache_is_bounded_for_threaded_serve():
    """B-24：私有页缓存有界，且不启用每连接整库 mmap（并发扫描实测见报告）。"""
    from doc2md.storage import db as dbmod

    assert dbmod.SQLITE_CACHE_KB <= 8192
    assert dbmod.SQLITE_MMAP_BYTES == 0


@pytest.mark.gate
def test_editor_updates_revision_after_refetch():
    """B-26 浏览器实机发现的回归：保存/重取后块元信息也要显示新 revision。"""
    html = (Path(__file__).resolve().parents[1] / "doc2md" / "static" / "editor.html").read_text(
        encoding="utf-8")
    assert "data-rev" in html
    assert "revEl.textContent" in html


@pytest.mark.gate
def test_paths_logs_is_runtime_immutable(tmp_cfg):
    from doc2md import config

    candidate = copy.deepcopy(tmp_cfg)
    candidate["paths"]["logs"] = str(Path(tmp_cfg["paths"]["root"]) / "other-logs")
    with pytest.raises(config.ConfigError):
        config.check_runtime_immutable(candidate, tmp_cfg)


@pytest.mark.gate
def test_artifact_lock_stale_without_heartbeat_is_reclaimed(tmp_path):
    """没有心跳的旧锁文件（模拟崩溃遗留）仍按 stale 窗口回收。"""
    from doc2md.api import _artifact_lock

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    dead = lock_dir / "doc-lock.lock"
    dead.write_text("999999 0 dead-token", encoding="ascii")
    old = time.time() - 10
    os.utime(dead, (old, old))
    with _artifact_lock(tmp_path, "doc-lock", timeout=1,
                        stale_seconds=1, heartbeat_seconds=0.05):
        assert dead.exists()
    assert not dead.exists()
