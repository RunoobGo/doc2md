"""CH-483 / 数据安全链：删前快照、轮出保护、恢复 CLI、WAL 安全复制。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from doc2md.ops.backup import BackupPlan, encrypt_bytes

pytestmark = [pytest.mark.milestone("M8")]
PASS = "ch483-passphrase"


def _mk_db(p: Path, value: str = "正文") -> Path:
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE t(x TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()
    return p


# --- DB7：墓碑索引 -------------------------------------------------------------
@pytest.mark.gate
def test_db7_has_deletions_doc_ts_index(store):
    from doc2md.storage.db import DB_VERSION
    assert DB_VERSION >= 7
    names = {r["name"] for r in store.q("PRAGMA index_list('deletions')")}
    assert "ix_deletions_doc_seq" in names


# --- 删前快照 -----------------------------------------------------------------
@pytest.mark.gate
def test_delete_takes_predelete_snapshot(api_client, headers, saved_doc, tmp_cfg):
    client, tokens, doc_id = saved_doc
    r = client.delete(f"/docs/{doc_id}?confirm=true", headers=headers(tokens, "admin"))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    backups = Path(tmp_cfg["ops"]["backup_local_dir"])
    snaps = list(backups.glob("predelete-*.sqlite"))
    assert len(snaps) == 1, snaps
    con = sqlite3.connect(str(snaps[0]))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # 快照在删除前生成：必须仍包含待恢复的文档。
        assert con.execute("SELECT COUNT(*) FROM docs WHERE doc_id=?", (doc_id,)).fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.gate
def test_delete_fail_closed_when_snapshot_fails(api_client, headers, saved_doc,
                                                monkeypatch):
    client, tokens, doc_id = saved_doc
    from doc2md.ops.backup import BackupPlan

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(BackupPlan, "predelete_snapshot", boom)
    r = client.delete(f"/docs/{doc_id}?confirm=true", headers=headers(tokens, "admin"))
    assert r.status_code == 500, r.get_data(as_text=True)[:300]
    assert client.get(f"/docs/{doc_id}", headers=headers(tokens, "reader")).status_code == 200


# --- 轮出：外置失败不轮外置 + 保留最新 ----------------------------------------
def _touch_old(p: Path, days: int) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(p, (ts, ts))


def test_rotation_can_skip_external_and_keep_newest(tmp_path):
    local = tmp_path / "local"
    ext = tmp_path / "ext"
    local.mkdir()
    ext.mkdir()
    now = datetime.now(timezone.utc)
    newest = local / f"state-{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite"
    older = local / "state-20200101T000000Z.sqlite"
    ext_old = ext / "state-20200101T000000Z.sqlite.enc"
    for p in (newest, older, ext_old):
        p.write_bytes(b"x")
        _touch_old(p, 30)
    plan = BackupPlan(local_dir=local, external_dir=ext, rotation_days=14)
    plan.rotate(include_external=False, keep_newest=True)
    assert newest.exists(), "最新一份必须保留"
    assert not older.exists()
    assert ext_old.exists(), "外置轮出被跳过时不得碰外置副本"


def test_rotation_keeps_newest_external_when_all_expired(tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    only = ext / "state-20200101T000000Z.sqlite.enc"
    only.write_bytes(b"x")
    _touch_old(only, 30)
    plan = BackupPlan(local_dir=tmp_path / "local", external_dir=ext, rotation_days=14)
    plan.rotate(include_external=True, keep_newest=True)
    assert only.exists(), "外置目录的最后一份副本不得被轮出删除"


@pytest.mark.gate
def test_cron_external_failure_does_not_rotate_external(tmp_path, cfg, monkeypatch):
    """外置备份失败时，cron 不得轮出外置旧副本（否则连续失败会删掉最后一份）。"""
    import copy

    from doc2md import cron
    from doc2md.errors import Doc2mdError
    from doc2md.ops.backup import BackupPlan

    c = copy.deepcopy(cfg)
    root = tmp_path / "work"
    root.mkdir()
    ext = tmp_path / "ext"
    ext.mkdir()
    old = ext / "state-20200101T000000Z.sqlite.enc"
    old.write_bytes(b"last-copy")
    _touch_old(old, 30)
    c.setdefault("paths", {})["root"] = str(root)
    c["paths"]["out"] = str(root / "out")
    c["paths"]["reports"] = str(root / "reports")
    c["paths"]["logs"] = str(root / "logs")
    c["paths"]["tmp"] = str(root / "tmp")
    c.setdefault("ops", {})["backup_local_dir"] = str(root / "reports/backups")
    c["ops"]["backup_external_dir"] = str(ext)
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)

    def boom(self, *a, **kw):
        raise Doc2mdError("external unavailable")

    monkeypatch.setattr(BackupPlan, "to_external", boom)
    rep = cron.run(c, root=root, mode="weekly")
    assert not rep.ok
    assert old.exists(), "外置备份失败时最后一份外置副本被删"
    rot = next(s for s in rep.steps if s["step"] == "rotation")
    assert rot.get("external_rotation") == "skipped"


# --- 恢复 CLI -----------------------------------------------------------------
def _run_cli(argv, monkeypatch):
    from doc2md.cli import main
    monkeypatch.setenv("CH483_PASS", PASS)
    return main(argv)


@pytest.mark.gate
def test_restore_cli_encrypted_and_clears_wal(tmp_path, monkeypatch):
    src = _mk_db(tmp_path / "source.sqlite", "恢复正文")
    enc = tmp_path / "state.sqlite.enc"
    enc.write_bytes(encrypt_bytes(src.read_bytes(), PASS))
    dst = tmp_path / "restore" / "state.sqlite"
    dst.parent.mkdir()
    _mk_db(dst, "old")
    (dst.parent / "state.sqlite-wal").write_bytes(b"stale-wal")
    (dst.parent / "state.sqlite-shm").write_bytes(b"stale-shm")

    rc = _run_cli(["backup", "restore", str(enc), "--to", str(dst),
                   "--force", "--passphrase-env", "CH483_PASS"], monkeypatch)
    assert rc == 0
    con = sqlite3.connect(str(dst))
    try:
        assert con.execute("SELECT x FROM t").fetchone()[0] == "恢复正文"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
    assert not (dst.parent / "state.sqlite-wal").exists()
    assert not (dst.parent / "state.sqlite-shm").exists()
    assert list(dst.parent.glob("pre-restore-*.sqlite")), "强制恢复前必须备份旧库"


def test_restore_cli_refuses_without_force(tmp_path, monkeypatch):
    src = _mk_db(tmp_path / "source.sqlite", "new")
    enc = tmp_path / "s.enc"
    enc.write_bytes(encrypt_bytes(src.read_bytes(), PASS))
    dst = _mk_db(tmp_path / "state.sqlite", "old")
    before = dst.read_bytes()
    rc = _run_cli(["backup", "restore", str(enc), "--to", str(dst),
                   "--passphrase-env", "CH483_PASS"], monkeypatch)
    assert rc == 2
    assert dst.read_bytes() == before


def test_restore_cli_bad_container_keeps_target(tmp_path, monkeypatch):
    dst = _mk_db(tmp_path / "state.sqlite", "old")
    bad = tmp_path / "bad.enc"
    bad.write_bytes(b"D2MBAE2" + b"broken")
    before = dst.read_bytes()
    rc = _run_cli(["backup", "restore", str(bad), "--to", str(dst),
                   "--force", "--passphrase-env", "CH483_PASS"], monkeypatch)
    assert rc == 4
    assert dst.read_bytes() == before


# --- WAL 安全复制 -------------------------------------------------------------
@pytest.mark.gate
def test_sqlite_backup_sees_uncheckpointed_wal(tmp_path):
    from doc2md.ops.backup import sqlite_backup
    src = tmp_path / "wal.sqlite"
    con = sqlite3.connect(str(src))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(x TEXT)")
    con.execute("INSERT INTO t VALUES ('wal-only')")
    con.commit()
    # 保持连接打开、不做 checkpoint：主库文件本身尚未包含该行。
    dst = tmp_path / "copy.sqlite"
    try:
        sqlite_backup(src, dst)
    finally:
        con.close()
    got = sqlite3.connect(str(dst)).execute("SELECT x FROM t").fetchone()[0]
    assert got == "wal-only"
