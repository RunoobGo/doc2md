"""CH-483 / 健壮性与性能：LLM 类型、陈旧作业、请求体、删除路径、symlink、配置层叠。"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.ir import IR, Block


# --- R8-LLM-1：非字符串 op ------------------------------------------------------
@pytest.mark.gate
def test_non_string_op_is_rejected_not_crash():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", text="x")]
    accepted, rejected = OpValidator(blocks).apply([{"op": ["split_block"]}])
    assert accepted == []
    assert rejected and "not_whitelisted" in rejected[0]["reasons"]


def test_empty_whitelist_rejects_everything():
    from doc2md.llm import OpValidator
    blocks = [Block(id="b1", text="x")]
    accepted, rejected = OpValidator(blocks, whitelist=[]).apply(
        [{"op": "merge_block", "target": "b1"}])
    assert accepted == [] and rejected


def test_parse_ops_rejects_non_string_name():
    from doc2md.llm import LlmServiceError, _parse_ops
    resp = {"choices": [{"message": {"content": '[{"op": ["x"]}]'}}]}
    with pytest.raises(LlmServiceError):
        _parse_ops(resp)


# --- R8-API-1：非对象 JSON ------------------------------------------------------
@pytest.mark.gate
def test_non_object_json_returns_400(api_client, headers):
    client, tokens = api_client
    r = client.post("/jobs", json=[], headers=headers(tokens, "editor"))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    r2 = client.patch("/docs/x/blocks/y", json=["x"], headers=headers(tokens, "editor"))
    assert r2.status_code == 400, r2.get_data(as_text=True)[:200]


# --- R8-SVC-2：同路径陈旧作业 ---------------------------------------------------
@pytest.mark.gate
def test_stale_same_path_job_is_rejected(tmp_cfg, tmp_path, monkeypatch):
    from doc2md import worker
    from doc2md.scheduler import Scheduler
    from doc2md.storage.db import Store

    store = Store(tmp_path / "state.sqlite")
    src = tmp_path / "same.pdf"
    src.write_bytes(b"%PDF-1.7\n")
    sched = Scheduler(store)
    old = sched.submit("doc-old", "sk", "k-old", source_path=str(src))
    new = sched.submit("doc-new", "sk", "k-new", source_path=str(src))

    ir = IR(doc_id="doc-new", source_key="sk", source_name="same.pdf", format="pdf",
            pages=1, blocks=[Block(id="b1", type="paragraph", text="新正文。", page=0)])

    def fake_batch(paths, **kw):
        return [{"file": str(src), "status": "ok", "zip": "fake.zip",
                 "doc_id": "doc-new", "blocks": 1}]

    monkeypatch.setattr(worker, "run_batch", fake_batch)
    monkeypatch.setattr(worker, "read_ir", lambda z: ir)
    rep = worker.drain_once(store, tmp_cfg, tmp_path, workers=1)
    assert rep.rejected == 1 and rep.done == 1, (rep.done, rep.rejected, rep.notes)
    old_row = sched.get(old.job_id)
    new_row = sched.get(new.job_id)
    assert old_row["status"] == "rejected" and old_row["error_code"] == "E4092", old_row
    detail = __import__("json").loads(old_row["detail_json"])
    assert detail["reason"] == "source_superseded"
    assert detail["consumed_doc_id"] == "doc-new"
    assert new_row["status"] == "done", new_row
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='doc-old'")["c"] == 0
    store.close()


# --- R8-PERF-2：删除产物用配置路径 + 直接命中 --------------------------------
@pytest.mark.gate
def test_purge_uses_configured_out_dir(tmp_path):
    from doc2md.storage import repo as repo_mod
    root = tmp_path / "work"
    custom = root / "custom_out"
    custom.mkdir(parents=True)
    zip_path = custom / "d1.zip"
    zip_path.write_bytes(b"zip")
    legacy = root / "out" / "d1.zip"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    removed, failed = repo_mod.purge_files("d1", root, out_dir=custom)
    assert not failed
    assert not zip_path.exists()
    assert legacy.exists(), "未配置的旧目录不在本次删除作用域"
    assert str(zip_path) in removed


# --- R8-SEC-1：symlink 跟随与越界 ------------------------------------------------
@pytest.mark.gate
def test_follow_symlinks_counts_escape_and_does_not_read_outside(tmp_path):
    from doc2md.security.dirguard import guard_walk, walk_limits
    root = tmp_path / "root"
    root.mkdir()
    (root / "real").mkdir()
    (root / "real" / "a.pdf").write_bytes(b"x")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"secret")
    (root / "inside-link").symlink_to(root / "real", target_is_directory=True)
    (root / "escape.pdf").symlink_to(outside)
    res = guard_walk(root, walk_limits({}, follow_symlinks=True))
    files = {str(Path(p).resolve()) for p in res.files}
    assert str(outside) not in files, "越界链接不得被读取"
    assert res.skipped_escaping_symlinks == 1, res
    assert res.followed_symlinks >= 1, res


# --- R8-CFG-1：配置层叠 ----------------------------------------------------------
@pytest.mark.gate
def test_config_cascades_home_cwd_and_explicit(tmp_path, monkeypatch):
    from doc2md import config
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "workdir"
    work.mkdir()
    (home / "doc2md.toml").write_text("[api]\nport = 1111\n", encoding="utf-8")
    (work / "doc2md.toml").write_text("[s2]\nmode = \"single\"\n", encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[api]\nport = 3333\n", encoding="utf-8")
    monkeypatch.setenv("DOC2MD_HOME", str(home))
    monkeypatch.chdir(work)
    cfg = config.load(explicit)
    assert config.get(cfg, "api.port") == 3333
    assert config.get(cfg, "s2.mode") == "single", "cwd 层未合并"
