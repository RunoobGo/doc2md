"""CH-485 / 运维与入口：Windows offline guard、CLI 参数、权限、watch 下限。"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import doc2md.security.netguard as ng
from doc2md.cli import _ensure_private_root, _watch_stable_seconds, build_parser
from doc2md.ops.backup import (
    LEGACY_SALT,
    MAGIC,
    BackupPlan,
    decrypt_bytes,
    derive_key,
)


class _FakeSocket:
    """模拟 Windows 的 socket.socket：有 connect/connect_ex/sendto，无 sendmsg。"""

    def connect(self, *a):  # pragma: no cover - 仅作为可替换原语
        pass

    def connect_ex(self, *a):  # pragma: no cover
        pass

    def sendto(self, *a):  # pragma: no cover
        pass


@pytest.mark.gate
def test_netguard_install_tolerates_missing_sendmsg(monkeypatch):
    fake = type("FakeSocket", (_FakeSocket,), {})
    monkeypatch.setattr(socket, "socket", fake)
    ng._ORIG.clear()
    try:
        ng._install({"127.0.0.1"})
        assert fake.connect is ng._guarded_connect
        assert "sendmsg" not in ng._ORIG
        assert ng.sendmsg_supported() is False
        ng._uninstall()
        assert fake.connect is not ng._guarded_connect
        assert ng._ORIG == {}
    finally:
        ng._ORIG.clear()


@pytest.mark.gate
def test_cli_common_options_survive_nested_subparsers():
    cases = [
        (["serve", "--root", "/x", "token", "--role", "admin"], "/x"),
        (["serve", "token", "--root", "/x", "--role", "admin"], "/x"),
        (["backup", "--root", "/x", "restore", "src", "--force"], "/x"),
        (["backup", "restore", "src", "--root", "/x", "--force"], "/x"),
        (["tokenizer", "--root", "/x", "verify", "--manifest", "m", "--gguf", "g"], "/x"),
        (["tokenizer", "verify", "--manifest", "m", "--gguf", "g", "--root", "/x"], "/x"),
    ]
    for argv, expected in cases:
        args = build_parser().parse_args(argv)
        assert getattr(args, "root", None) == expected, (argv, vars(args))


def test_cli_private_root_mode(tmp_path):
    from doc2md.security.perms import is_private
    root = tmp_path / "work"
    _ensure_private_root(root)
    assert root.is_dir()
    if os.name == "nt":
        assert is_private(root), "Windows 上必须落成仅当前用户 DACL"
    else:
        assert (root.stat().st_mode & 0o777) == 0o700


def _mklink_junction(link: Path, target: Path) -> bool:
    import subprocess
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    return proc.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_junction_not_followed_by_dirguard(tmp_path):
    from doc2md.security.dirguard import WalkLimits, guard_walk
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "inside.pdf").write_bytes(b"x")
    (outside / "secret.pdf").write_bytes(b"x")
    link = root / "jump"
    if not _mklink_junction(link, outside):
        pytest.skip("cannot create junction in this environment")
    res = guard_walk(root, WalkLimits(follow_symlinks=False))
    assert res.skipped_symlinks >= 1
    assert not any("jump" in p or "secret" in p for p in res.files), res.files


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_sweep_tmp_removes_junction_not_target(tmp_path):
    from doc2md.ops.tmpclean import sweep_tmp
    root = tmp_path / "work"
    tmp = root / "tmp"
    outside = tmp_path / "outside"
    tmp.mkdir(parents=True)
    outside.mkdir()
    keep = outside / "keep.pdf"
    keep.write_bytes(b"x")
    link = tmp / "jump"
    if not _mklink_junction(link, outside):
        pytest.skip("cannot create junction in this environment")
    cleaned = sweep_tmp({"paths": {"root": str(root), "tmp": str(tmp)},
                              "perf": {"stale_job_minutes": 0}}, root)
    assert cleaned >= 1
    assert not link.exists()
    assert keep.exists(), "junction 目标内容不得被递归删除"


def test_watch_stable_seconds_nonzero_floor():
    cfg = {"watch": {"stable_seconds": 0}}
    assert _watch_stable_seconds(cfg) == 0.1
    cfg["watch"]["stable_seconds"] = -3
    assert _watch_stable_seconds(cfg) == 0.1
    cfg["watch"]["stable_seconds"] = 2.5
    assert _watch_stable_seconds(cfg) == 2.5


PASS = "ch485-pass"


def _db(path: Path, value: str) -> Path:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t(x TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()
    return path


@pytest.mark.gate
def test_restore_safe_reads_legacy_v1_container(tmp_path):
    plain = tmp_path / "state.sqlite"
    _db(plain, "v1-data")
    nonce = os.urandom(12)
    ct = AESGCM(derive_key(PASS, LEGACY_SALT)).encrypt(
        nonce, plain.read_bytes(), MAGIC)
    enc = tmp_path / "legacy.enc"
    enc.write_bytes(MAGIC + nonce + ct)
    dst = tmp_path / "restored.sqlite"
    BackupPlan(local_dir=tmp_path / "bk", external_dir=None).restore_safe(
        enc, dst, PASS, force=True)
    con = sqlite3.connect(str(dst))
    try:
        assert con.execute("SELECT x FROM t").fetchone()[0] == "v1-data"
    finally:
        con.close()


@pytest.mark.gate
def test_restore_failure_rolls_back_old_db(tmp_path):
    old = _db(tmp_path / "state.sqlite", "old")
    src = _db(tmp_path / "src.sqlite", "new")
    plan = BackupPlan(local_dir=tmp_path / "bk", external_dir=None)
    real_replace = Path.replace

    def boom(self, target):
        if str(self).endswith(".part"):
            raise OSError("simulated replace failure")
        return real_replace(self, target)

    with patch.object(Path, "replace", boom):
        with pytest.raises(OSError):
            plan.restore_safe(src, old, "", force=True)
    con = sqlite3.connect(str(old))
    try:
        assert con.execute("SELECT x FROM t").fetchone()[0] == "old"
    finally:
        con.close()
    assert list((tmp_path / "bk").glob("pre-restore-*.part")) == []


@pytest.mark.gate
def test_snapshot_is_atomic_and_quick_checkable(tmp_path):
    db = tmp_path / "state.sqlite"
    _db(db, "snapshot")
    plan = BackupPlan(local_dir=tmp_path / "bk", external_dir=None,
                      work_root=tmp_path)
    snap = plan.snapshot(db)
    assert snap.name.startswith("state-") and snap.name.endswith(".sqlite")
    assert not list((tmp_path / "bk").glob("*.part"))
    con = sqlite3.connect(str(snap))
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        con.close()


@pytest.mark.gate
def test_concurrent_external_backup_produces_valid_container(tmp_path):
    snap = tmp_path / "state-20261018T000000Z-abcdef12.sqlite"
    snap.write_bytes(os.urandom(1024 * 1024))
    ext = tmp_path / "ext"
    plan = BackupPlan(local_dir=tmp_path / "local", external_dir=ext)
    errors: list[str] = []

    def worker():
        try:
            plan.to_external(snap, PASS)
        except Exception as exc:  # noqa: BLE001 — 探针收集
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    outs = list(ext.glob("*.enc"))
    assert len(outs) == 1, outs
    assert decrypt_bytes(outs[0].read_bytes(), PASS) == snap.read_bytes()


def test_backup_lock_is_exclusive(tmp_path):
    from doc2md.ops.backup import BackupBusy, backup_lock
    with backup_lock(tmp_path):
        with pytest.raises(BackupBusy):
            with backup_lock(tmp_path):
                pass
    with backup_lock(tmp_path):
        pass
