"""tests/test_t7_drill.py —— 备份恢复演练自动化（CH-456 / T7，DoD 第 11 条）。

口径对齐 docs/dev-plan.md T7 出口条件：一条命令在隔离沙箱内完成
「快照 → 外置加密 → 模拟数据丢失 → 轮出确认 → 解密恢复 → integrity_check →
内容比对」全闭环；任一步失败即整体失败，不落「看起来成功」的报告。

NFR-20：报告与断言只涉及表名、行数、指纹前缀；正文唯一串（MARKER）用于
反向验证「任何环节都不泄露正文」。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from doc2md.errors import E4001_BadRequest
from doc2md.ir import IR, Block
from doc2md.ir.model import source_key_of
from doc2md.ops.backup import MAGIC_V2, BackupPlan
from doc2md.ops.drill import DrillFailure, run_drill
from doc2md.storage.db import Store, now_iso
from doc2md.storage.repo import save_ir

pytestmark = [pytest.mark.milestone("DoD")]

PASS = "drill-pass-phrase-2026"
MARKER = "演练夹具唯一正文串-T7-9f3a"      # NFR-20 反向探针：不得出现在任何输出里
STEP_ORDER = ["copy_live", "snapshot", "external_encrypt", "simulate_loss",
              "rotate", "restore", "integrity_check", "compare"]


@pytest.fixture
def drill_env(tmp_path, monkeypatch):
    """造一个带真实数据的 state.sqlite + 口令环境变量；返回 (root, cfg)。"""
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
    root = tmp_path / "work"
    root.mkdir(parents=True, exist_ok=True)
    db = root / "state.sqlite"
    store = Store(db)
    ir = IR(
        doc_id="doc-t7-0001", source_key=source_key_of(b"t7-source-bytes"),
        source_name="t7-sample.pdf", format="pdf", pages=3,
        blocks=[
            Block(id="b1", type="heading", text="演练标题", level=1, page=1),
            Block(id="b2", type="paragraph", text=MARKER, page=1),
            Block(id="b3", type="paragraph", text="第三段正文。", page=2),
        ])
    save_ir(store, ir, profile="standard")
    with store.tx() as tx:                       # 事件总线（列名以 DDL 为准）
        for i, code in enumerate(["convert.done", "index.refresh", "backup.weekly"]):
            tx.execute(
                "INSERT INTO events(ts, code, doc_id, payload_json) VALUES (?,?,?,?)",
                (now_iso(), code, ir.doc_id, json.dumps({"i": i}, ensure_ascii=False)))
        tx.execute(
            "INSERT INTO tokens(token, role, label, created_at) VALUES (?,?,?,?)",
            ("tok-t7-admin", "admin", "t7", now_iso()))
    store.close()
    from doc2md import config
    cfg = config.load()
    cfg.setdefault("paths", {})["root"] = str(root)
    return root, cfg


# --- 闭环 --------------------------------------------------------------------
def test_drill_full_loop_succeeds(drill_env):
    root, cfg = drill_env
    rep = run_drill(cfg, root)
    assert rep.ok is True
    assert rep.match is True
    assert [s.name for s in rep.steps] == STEP_ORDER
    assert all(s.ok for s in rep.steps)
    # 12 张表口径：FTS5 虚拟表本身被 _tables 过滤，但其影子表在列
    for t in ("docs", "blocks", "events", "tokens", "search_blocks_data"):
        assert t in rep.tables
    assert len(rep.tables) == 12
    assert rep.tables["docs"]["rows"] == 1
    assert rep.tables["blocks"]["rows"] == 3
    assert rep.tables["events"]["rows"] == 3
    assert rep.drill_dir is None                    # 默认即清沙箱


def test_drill_rotate_step_removes_stale_keeps_current(drill_env):
    root, cfg = drill_env
    rep = run_drill(cfg, root)
    rot = next(s for s in rep.steps if s.name == "rotate")
    assert "state-20000101T000000Z.sqlite" in rot.detail["removed"]
    assert rot.detail["kept"].startswith("state-")


def test_drill_sandbox_is_isolated(drill_env):
    """演练只读线上库：跑完后 state.sqlite 逐字节不变，且沙箱目录已清。"""
    root, cfg = drill_env
    before = (root / "state.sqlite").read_bytes()
    rep = run_drill(cfg, root)
    assert (root / "state.sqlite").read_bytes() == before
    assert rep.drill_dir is None
    assert not (root / "tmp").exists() or not any((root / "tmp").iterdir())


def test_drill_keep_preserves_forensic_dir(drill_env, tmp_path):
    root, cfg = drill_env
    rep = run_drill(cfg, root, keep=True)
    assert rep.drill_dir is not None and rep.drill_dir.is_dir()
    assert (rep.drill_dir / "backups_external").is_dir()


def test_drill_external_container_discipline(drill_env):
    """外置副本：AES-256-GCM 容器魔数 + 文件 600（NFR-18）。"""
    root, cfg = drill_env
    rep = run_drill(cfg, root, keep=True)
    encs = list((rep.drill_dir / "backups_external").glob("*.enc"))
    assert len(encs) == 1
    enc = encs[0]
    assert enc.read_bytes()[:len(MAGIC_V2)] == MAGIC_V2
    assert oct(enc.stat().st_mode & 0o777) == "0o600"


def test_drill_report_leaks_no_plaintext(drill_env):
    """NFR-20：报告 JSON 不得含任何正文字符串（只允许表名/行数/指纹前缀）。"""
    root, cfg = drill_env
    rep = run_drill(cfg, root)
    blob = json.dumps(rep.to_dict(), ensure_ascii=False)
    assert MARKER not in blob
    assert "第三段正文" not in blob
    assert PASS not in blob                       # 口令同样不得进报告


def test_drill_missing_passphrase_is_hard_failure(tmp_path, monkeypatch):
    """外置副本强制加密：缺口令直接 E4001，绝不落明文副本。"""
    monkeypatch.delenv("DOC2MD_BACKUP_PASSPHRASE", raising=False)
    from doc2md import config
    with pytest.raises(E4001_BadRequest):
        run_drill(config.load(), tmp_path / "work")


def test_drill_survives_missing_live_db(tmp_path, monkeypatch):
    """线上库不存在时按空 schema 演练（新建部署首次演练同样成立）。"""
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
    from doc2md import config
    rep = run_drill(config.load(), tmp_path / "work")
    assert rep.ok is True and rep.match is True


# --- 负向：恢复链必须真的在校验 ----------------------------------------------
def test_restore_rejects_tampered_container(drill_env):
    """篡改外置容器任一八比特组 → 恢复必须失败（GCM 认证不是摆设）。"""
    root, cfg = drill_env
    rep = run_drill(cfg, root, keep=True)
    enc = next((rep.drill_dir / "backups_external").glob("*.enc"))
    blob = bytearray(enc.read_bytes())
    blob[len(MAGIC_V2) + 16 + 4 + 8 + 12 + 5] ^= 0x01   # 头(7+16+4+8)+nonce 之后的密文区翻一比特
    enc.write_bytes(bytes(blob))
    plan = BackupPlan(local_dir=rep.drill_dir / "backups_local",
                      external_dir=rep.drill_dir / "backups_external")
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        plan.restore(enc, root / "should-not-exist.sqlite", PASS)
    assert not (root / "should-not-exist.sqlite").exists()


def test_drill_fails_honestly_on_tampered_external(drill_env, monkeypatch):
    """加密出口被污染时，演练必须 DrillFailure，而不是 compare 蒙混过关。"""
    root, cfg = drill_env
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
    orig = BackupPlan.to_external

    def corrupt(self, snapshot, passphrase):
        out = orig(self, snapshot, passphrase)
        data = bytearray(out.read_bytes())
        data[-1] ^= 0xFF                           # 翻 GCM tag 末字节
        out.write_bytes(bytes(data))
        return out

    monkeypatch.setattr(BackupPlan, "to_external", corrupt)
    with pytest.raises(DrillFailure):
        run_drill(cfg, root)


def test_drill_failure_leaves_no_ok_report(drill_env, monkeypatch):
    """失败路径不写「ok: true」：异常必须穿透，finally 照常清沙箱。"""
    root, cfg = drill_env
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)

    def boom(self, *a, **k):
        raise OSError("simulated disk full")

    monkeypatch.setattr(BackupPlan, "snapshot", boom)
    with pytest.raises(DrillFailure):
        run_drill(cfg, root)
    leftovers = list((root / "tmp").glob("drill-*")) if (root / "tmp").exists() else []
    assert leftovers == []


def test_fingerprint_is_deterministic_and_content_sensitive(drill_env):
    """指纹口径：同库稳定；改一行内容必须变（否则比对形同虚设）。"""
    root, cfg = drill_env
    from doc2md.ops.drill import fingerprint
    db = root / "state.sqlite"
    fp1 = fingerprint(db)
    assert fingerprint(db) == fp1
    con = sqlite3.connect(str(db))
    con.execute("UPDATE blocks SET text=? WHERE block_id='b3'", (MARKER + "-改",))
    con.commit()
    con.close()
    fp2 = fingerprint(db)
    assert fp2["blocks"]["sha256"] != fp1["blocks"]["sha256"]
    assert fp2["blocks"]["rows"] == fp1["blocks"]["rows"]


def test_integrity_check_flags_corruption(tmp_path):
    from doc2md.ops.drill import integrity_check
    db = tmp_path / "corrupt.sqlite"
    store = Store(db)
    store.close()
    assert integrity_check(db) == "ok"
    raw = bytearray(db.read_bytes())
    raw[100] = 0x0C          # 页 1 的 b-tree 页类型字节 13(叶子表)→12(非法)：确定性触发
    db.write_bytes(bytes(raw))
    assert integrity_check(db) != "ok"
