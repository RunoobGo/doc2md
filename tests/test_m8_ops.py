"""tests/test_m8_ops.py —— M8 备份加密 / 轮出 / 拒绝未加密（CH-406 / NFR-18）+ 回滚契约。

DoD-11 演练（附录 E）在本文件以 pytest 形式落地，可反复重跑：
  删除级联 → 备份 → 加密外置 → 恢复 → 轮出 → 未加密拒绝。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from doc2md.ir import IR, Block
from doc2md.ops.backup import CHUNK_SIZE, MAGIC_V2, BackupPlan, decrypt_bytes, encrypt_bytes, hdiutil_recipe
from doc2md.storage import repo
from doc2md.storage.db import Store

pytestmark = [pytest.mark.milestone("M8")]
PASS = "correct horse battery staple"


def _mk_db(p: Path) -> Path:
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE t(x TEXT)")
    c.execute("INSERT INTO t VALUES ('本条款涉及例外情况')")
    c.commit()
    c.close()
    return p


# --- 加解密原语 --------------------------------------------------------------
def test_encrypt_decrypt_roundtrip():
    data = "正文：例外情况 12345".encode() + os.urandom(64)
    blob = encrypt_bytes(data, PASS)
    assert blob.startswith(MAGIC_V2)      # CH-460 / A-11：新容器一律 v2（随机盐）
    assert "例外情况" not in blob.decode("latin-1")     # 密文不含明文
    assert decrypt_bytes(blob, PASS) == data


def test_wrong_passphrase_rejected():
    blob = encrypt_bytes(b"payload", PASS)
    with pytest.raises(InvalidTag):
        decrypt_bytes(blob, "wrong-pass")


def test_decrypt_rejects_non_container():
    with pytest.raises(ValueError):
        decrypt_bytes(b"PLAINTEXT-DB", PASS)


# --- 外置备份纪律 ------------------------------------------------------------
def test_external_requires_encryption(tmp_path):
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups",
                      external_dir=tmp_path / "ext", encrypted_external=False)
    snap = plan.snapshot(_mk_db(tmp_path / "state.sqlite"))
    with pytest.raises(PermissionError):               # NFR-18：未加密一律拒绝
        plan.to_external(snap, PASS)


def test_external_backup_encrypted_and_restorable(tmp_path):
    db = _mk_db(tmp_path / "state.sqlite")
    ext = tmp_path / "ext"
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups",
                      external_dir=ext, encrypted_external=True)
    snap = plan.snapshot(db)
    out = plan.to_external(snap, PASS)
    assert out.suffix == ".enc"
    from doc2md.security.perms import is_private
    if os.name == "nt":
        assert is_private(out)
    else:
        assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert MAGIC_V2 == out.read_bytes()[:7]   # 外置副本为新 v2 容器
    # 恢复 → 可用
    dst = plan.restore(out, tmp_path / "restored.sqlite", PASS)
    got = sqlite3.connect(str(dst)).execute("SELECT x FROM t").fetchone()[0]
    assert got == "本条款涉及例外情况"


def test_local_snapshot_dir_mode_700(tmp_path):
    db = _mk_db(tmp_path / "state.sqlite")
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups", external_dir=None)
    plan.snapshot(db)
    work = tmp_path / "work"
    from doc2md.security.perms import is_private
    if os.name == "nt":
        assert is_private(work), "Windows 上 work/ 必须落成仅当前用户 DACL"
    else:
        assert oct(work.stat().st_mode & 0o777) == "0o700", "work/ 权限必须 700（M8 验收）"


def test_rotation_drops_files_older_than_14_days(tmp_path):
    db = _mk_db(tmp_path / "state.sqlite")
    ext = tmp_path / "ext"
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups",
                      external_dir=ext, rotation_days=14)
    now = datetime.now(timezone.utc)
    # 快照名按 UTC 秒生成，测试显式给时间戳避免同秒重名
    old_snap = plan.snapshot(db, ts=now - timedelta(days=15))
    old_ext = plan.to_external(old_snap, PASS)
    new_snap = plan.snapshot(db, ts=now)
    plan.to_external(new_snap, PASS)
    # 轮出按 mtime 判定，把旧副本的 mtime 拨回 15 天前
    old = (now - timedelta(days=15)).timestamp()
    for p in (old_snap, old_ext):
        os.utime(p, (old, old))
    removed = plan.rotate()
    assert {p.name for p in removed} == {old_snap.name, old_ext.name}
    assert new_snap.exists()
    assert not old_snap.exists() and not old_ext.exists()
    assert len(list(plan.local_dir.glob("state-*.sqlite"))) == 1


def test_hdiutil_recipe_documented():
    """README 二选一的另一支（macOS sparsebundle）必须可查。"""
    r = hdiutil_recipe()
    assert "-encryption AES-256" in r and "SPARSEBUNDLE" in r


# --- 回滚契约（编辑审计 → 指定 revision） ------------------------------------
def test_rollback_restores_previous_text(store):
    from doc2md.edits import patch_block, rollback
    ir = IR(doc_id="rb", source_key="rbk", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="原文 v1", page=0)])
    repo.save_ir(store, ir)
    patch_block(store, "rb", "b1", {"text": "改A v2", "base_revision": 1}, "tester")
    patch_block(store, "rb", "b1", {"text": "改B v3", "base_revision": 2}, "tester")
    res = rollback(store, "rb", "b1", to_revision=1, actor="tester")
    assert res["revision"] == 4
    cur = repo.list_blocks(store, "rb")[0]
    assert cur.text == "原文 v1"
    # 回滚同样可检索（索引随 PATCH 刷新）
    assert repo.search(store, "原文 v1")["total"] >= 1


def test_rollback_unknown_revision_404(store):
    from doc2md.edits import patch_block, rollback
    from doc2md.errors import E4041_NotFound
    ir = IR(doc_id="rb2", source_key="rb2k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="原文", page=0)])
    repo.save_ir(store, ir)
    patch_block(store, "rb2", "b1", {"text": "改A", "base_revision": 1}, "tester")
    with pytest.raises(E4041_NotFound):
        rollback(store, "rb2", "b1", to_revision=9, actor="tester")


def test_rollback_via_api(saved_doc):
    client, tokens, doc = saved_doc
    blocks = client.get(f"/docs/{doc}",
                        headers={"X-Doc2md-Token": tokens["admin"]}).get_json()["blocks"]
    bid, orig = blocks[0]["id"], blocks[0]["text"]
    r = client.patch(f"/docs/{doc}/blocks/{bid}",
                     json={"text": "API 改A", "base_revision": 1},
                     headers={"X-Doc2md-Token": tokens["editor"]})
    assert r.status_code == 200
    rb = client.post(f"/docs/{doc}/blocks/{bid}/rollback",
                     json={"to_revision": 1},
                     headers={"X-Doc2md-Token": tokens["editor"]})
    assert rb.status_code == 200, rb.get_json()
    after = client.get(f"/docs/{doc}",
                       headers={"X-Doc2md-Token": tokens["admin"]}).get_json()["blocks"]
    assert next(b for b in after if b["id"] == bid)["text"] == orig


# --- 整源删除（source_key 级联） --------------------------------------------
def test_delete_source_removes_all_docs(api_client, small_pdf):
    client, tokens = api_client
    r = client.post("/jobs", json={"path": str(small_pdf)},
                    headers={"X-Doc2md-Token": tokens["editor"]})
    job = r.get_json()["jobs"][0]
    doc, source = job["doc_id"], None
    row = client.get(f"/docs/{doc}", headers={"X-Doc2md-Token": tokens["admin"]}).get_json()
    source = row["source_key"]
    assert client.delete(f"/sources/{source}",
                         headers={"X-Doc2md-Token": tokens["admin"]}).status_code == 400
    d = client.delete(f"/sources/{source}?confirm=true",
                      headers={"X-Doc2md-Token": tokens["admin"]})
    assert d.status_code == 200, d.get_json()
    assert client.get(f"/docs/{doc}",
                      headers={"X-Doc2md-Token": tokens["admin"]}).status_code == 404
    tomb = client.get("/audit/deletions",
                      headers={"X-Doc2md-Token": tokens["admin"]}).get_json()["deletions"]
    assert any(t["kind"] == "source" for t in tomb)


def test_delete_doc_fail_closed_when_purge_fails(store, tmp_path, monkeypatch):
    """CH-440：产物删不净 → 抛 E5031 并保留库行（可安全重试），不留不可寻址副本。"""
    from doc2md.errors import E5031_StorageError

    ir = IR(doc_id="cz440", source_key="sk440", source_name="a.pdf", format="pdf",
            pages=1, blocks=[Block(id="b1", type="paragraph", text="本条款涉及例外情况")])
    repo.save_ir(store, ir)
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "cz440.zip").write_bytes(b"PK")
    (out / "cz440.md").write_text("正文", encoding="utf-8")

    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **k):
        if self.name == "cz440.zip":
            raise OSError("simulated disk fault")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    with pytest.raises(E5031_StorageError) as ei:
        repo.delete_doc(store, "cz440", "tester", root=tmp_path)
    assert ei.value.detail["failed"] == 1
    # 库行保留 → 文档仍可寻址；未删净的文件也还在
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='cz440'")["c"] == 1
    assert (out / "cz440.zip").exists()
    assert store.q1("SELECT COUNT(*) c FROM deletions")["c"] == 0

    monkeypatch.undo()
    res = repo.delete_doc(store, "cz440", "tester", root=tmp_path)
    assert res["deleted"]["blocks"] == 1 and res["files"]
    assert not (out / "cz440.zip").exists() and not (out / "cz440.md").exists()
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='cz440'")["c"] == 0
    tomb = store.q("SELECT * FROM deletions ORDER BY rowid")[-1]
    cascade = json.loads(tomb["cascade_json"])
    # 首次尝试已删掉 cz440.md，重试只需清除剩下的 zip（级联文件计数不重复计）
    assert cascade["files"] == 1 and cascade["deleted"]["blocks"] == 1


def test_delete_fail_closed_is_contract_shaped_over_http(saved_doc, monkeypatch):
    """CH-440 配套：E5031 走 API 也必须返回 {code,message,detail}，而不是 Flask 默认 500 HTML。"""
    client, tokens, doc = saved_doc
    h = {"X-Doc2md-Token": tokens["admin"]}
    real_unlink = Path.unlink

    def flaky(self, *a, **k):
        if self.suffix == ".zip":
            raise OSError("simulated disk fault")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky)
    r = client.delete(f"/docs/{doc}?confirm=true", headers=h)
    assert r.status_code == 500, r.get_data(as_text=True)[:200]
    j = r.get_json()
    assert j["code"] == "E5031" and j["detail"]["failed"] >= 1
    monkeypatch.undo()
    # 失败未提交：文档仍在，可重试
    assert client.get(f"/docs/{doc}", headers=h).status_code == 200
    ok = client.delete(f"/docs/{doc}?confirm=true", headers=h)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["deleted"]["blocks"] >= 1
    assert client.get(f"/docs/{doc}", headers=h).status_code == 404


# --- 依赖口径（CH-441：pyproject ↔ 实装导入 ↔ doctor 自检 三方一致） ----------
_IMPORT_TO_DIST = {"docx": "python-docx", "flask": "flask"}


def _third_party_imports(pkg_dir: Path) -> set[str]:
    """CH-462：regex 逐行扫描改 **ast 遍历**——原正则漏掉 importlib.import_module /
    __import__ 的字符串字面量调用（动态导入不进依赖闭包即装机崩溃），且对
    多行 from-import（括号换行）的首名之外全部失明。ast 语义化解析无这两类盲区。"""
    import ast
    import sys
    names: set[str] = set()
    for p in pkg_dir.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or node.module is None:
                    continue                 # 相对导入不参与口径比对
                names.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                fn = node.func
                is_importlib = (isinstance(fn, ast.Attribute)
                                and isinstance(fn.value, ast.Name)
                                and fn.value.id == "importlib"
                                and fn.attr == "import_module")
                is_dunder = isinstance(fn, ast.Name) and fn.id == "__import__"
                if (is_importlib or is_dunder) and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        names.add(arg.value.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n != "doc2md"}


def test_declared_deps_match_actual_imports():
    """CH-441：不得声明未使用的依赖（安装面虚胖），也不得导入未声明的依赖（装机即崩）。"""
    tomllib = pytest.importorskip("tomllib")
    root = Path(__file__).resolve().parent.parent
    py = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {d.split("[")[0].split(">")[0].split("=")[0].strip().lower()
                for d in py["project"]["dependencies"]}
    actual = {_IMPORT_TO_DIST.get(n, n) for n in _third_party_imports(root / "doc2md")}
    assert actual <= declared, f"未声明却被导入：{sorted(actual - declared)}"
    assert declared <= actual, f"声明却未使用：{sorted(declared - actual)}"


def test_doctor_dep_list_matches_pyproject():
    """CH-441：`doc2md doctor` 的 dep_* 清单必须与运行时依赖同一口径。"""
    from doc2md.cli import DEPENDENCY_MODULES
    tomllib = pytest.importorskip("tomllib")
    root = Path(__file__).resolve().parent.parent
    py = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {d.split("[")[0].split(">")[0].split("=")[0].strip().lower()
                for d in py["project"]["dependencies"]}
    assert {_IMPORT_TO_DIST.get(m, m) for m in DEPENDENCY_MODULES} == declared


def test_package_data_globs_exist():
    """CH-441：package-data 只声明真实存在的资源（曾挂 prompts/*.md 空 glob）。"""
    tomllib = pytest.importorskip("tomllib")
    root = Path(__file__).resolve().parent.parent
    py = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    globs = py["tool"]["setuptools"]["package-data"]["doc2md"]
    for g in globs:
        assert list((root / "doc2md").glob(g)), f"package-data glob 落空：{g}"


def _cron_cfg(cfg: dict, tmp_path: Path, external: bool = False) -> dict:
    import copy
    c = copy.deepcopy(cfg)
    c.setdefault("paths", {})["root"] = str(tmp_path / "work")
    for k in ("out", "reports", "logs", "tmp"):
        c["paths"][k] = str(tmp_path / "work" / k)
    c.setdefault("ops", {})["backup_local_dir"] = str(tmp_path / "work/reports/backups")
    if external:
        c["ops"]["backup_external_dir"] = str(tmp_path / "ext")
    else:
        c["ops"].pop("backup_external_dir", None)
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    return c


# --- cron 日报 / 周报（CH-433 语义） ----------------------------------------
def test_events_purge_honours_retention_boundary(tmp_path, cfg):
    """保留期边界必须按「时刻」而非「日期」判定（CH-455 同源缺陷）。

    events.ts 由 api._emit 用 now_iso() 写成 ISO 带 `+00:00`，SQL 原来直比
    `ts < datetime('now', '-30 days')`；SQLite 在第 11 位比字符（'T'=0x54 >
    ' '=0x20），于是**边界日当天**已过期的行会被判为未过期——保留期实际最长
    溢出 24 小时。断言取「30 天 + 1 小时」这条刚好跨过边界的行，它在缺陷版
    里会被留下。
    """
    from datetime import datetime, timedelta, timezone

    from doc2md import cron

    c = _cron_cfg(cfg, tmp_path)
    root = tmp_path / "work"
    store = Store(root / "state.sqlite")          # _cron_cfg 已建目录，首次连接建库
    now = datetime.now(timezone.utc)

    def seed(tag: str, ts: datetime) -> str:
        jid = f"job-{tag}"
        with store.tx() as tx:
            tx.execute("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
                       " VALUES (?,?,?,?,?)",
                       (ts.isoformat(timespec="milliseconds"), "TEST", f"doc-{tag}", jid, "{}"))
        return jid

    keep = seed("keep", now - timedelta(days=29))
    seed("over", now - timedelta(days=30, hours=1))            # 已过 30 天保留期
    seed("way", now - timedelta(days=45))
    r = cron.run(c, root=root, mode="daily", with_backup=False)
    step = next(s for s in r.steps if s["step"] == "events_purge")
    assert step["removed"] == 2, (step, r.steps)
    left = {row["job_id"] for row in store.q("SELECT job_id FROM events")}
    assert left == {keep}, left
    store.close()


def test_cron_daily_ok_and_weekly_skips_without_external(tmp_path, cfg):
    from doc2md import cron
    c = _cron_cfg(cfg, tmp_path)
    store = Store(tmp_path / "work" / "state.sqlite")
    daily = cron.run(c, root=tmp_path / "work", mode="daily")
    assert daily.ok, daily.steps
    assert any(s["step"] == "daily_report" for s in daily.steps)
    # 周报：未配置外置卷 → skipped，不算失败（CH-433）
    weekly = cron.run(c, root=tmp_path / "work", mode="weekly")
    assert weekly.ok, weekly.steps
    ext_step = [s for s in weekly.steps if s["step"] == "external_backup"]
    assert ext_step and ext_step[0].get("skipped") and ext_step[0].get("ok") is not False
    store.close()


def test_cron_weekly_refuses_plaintext_external(tmp_path, cfg):
    from doc2md import cron
    c = _cron_cfg(cfg, tmp_path, external=True)
    store = Store(tmp_path / "work" / "state.sqlite")
    res = cron.run(c, root=tmp_path / "work", mode="weekly")   # 无口令 env
    assert not res.ok, "有外置卷但无口令必须失败，而不是静默明文外拷"
    ext = tmp_path / "ext"
    if ext.exists():
        assert not list(ext.glob("*.sqlite")), "明文快照不得落到外置卷"
    store.close()


def test_cron_weekly_with_passphrase_writes_enc(tmp_path, cfg, monkeypatch):
    from doc2md import cron
    c = _cron_cfg(cfg, tmp_path, external=True)
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
    store = Store(tmp_path / "work" / "state.sqlite")
    res = cron.run(c, root=tmp_path / "work", mode="weekly")
    assert res.ok, res.steps
    encs = list((tmp_path / "ext").glob("*.enc"))
    assert encs and encs[0].read_bytes().startswith(MAGIC_V2)
    store.close()


# --- DoD-11 演练：一条龙 ----------------------------------------------------
def test_dod11_drill_end_to_end(tmp_path, cfg, small_pdf, monkeypatch):
    """方案附录 E 演练序列（备份加密/恢复/轮出 + 删除级联）在一次测试内闭环。

    逐项对应 DoD-11 检查单：
    ① 取含编辑流水的文档 → ② admin DELETE?confirm=true → ③ GET 404 + 搜索 0 残留
    ④ 产物/IR/事件/revision/审计均不可见 → ⑤ 留痕只含 who/when/doc_id
    ⑥ 轮出 ≤14 天 → ⑦ 容器可解密恢复且与历史快照逐字节一致
    ⑧ 密钥不在库/介质/日志/日报/事件流任一位置 → ⑨ 整源删除另测（本文件另一用例）
    ⑩ 非 admin 403 / 缺 confirm 拒收（本用例断言）。
    """
    import hashlib

    from doc2md import auth, cron
    from doc2md.api import create_app
    c = _cron_cfg(cfg, tmp_path, external=True)
    root = tmp_path / "work"
    app = create_app(c, root)
    st: Store = app.config["doc2md"]["store"]
    tok = auth.issue(st, "admin", "dod11")
    editor = auth.issue(st, "editor", "dod11-e")
    with app.test_client() as cl:
        h = {"X-Doc2md-Token": tok}
        he = {"X-Doc2md-Token": editor}
        r = cl.post("/jobs", json={"path": str(small_pdf)}, headers=he)
        doc = r.get_json()["jobs"][0]["doc_id"]
        assert cl.get(f"/docs/{doc}", headers=h).status_code == 200

        # ① 制造编辑流水（演练对象必须带 before/after 审计）
        blocks = cl.get(f"/docs/{doc}", headers=h).get_json()["blocks"]
        bid = blocks[0]["id"]
        cl.patch(f"/docs/{doc}/blocks/{bid}",
                 json={"text": "演练改写 例外条款", "base_revision": 1}, headers=he)
        assert cl.get(f"/docs/{doc}/audit", headers=he).get_json()["audit"]

        # ②③⑩ 删除双闸
        r0 = cl.delete(f"/docs/{doc}", headers=h)   # 附录 E：缺 confirm=true → 400
        assert r0.status_code == 400 and r0.get_json()["code"] == "E4001"
        assert cl.delete(f"/docs/{doc}", headers=he).status_code == 403       # 非 admin
        # ⑦ 先做一次加密外置备份，留作「历史快照」比对
        monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
        wk = cron.run(c, root=root, mode="weekly")
        assert wk.ok, wk.steps
        encs = list((tmp_path / "ext").glob("*.enc"))
        snaps = list(Path(c["ops"]["backup_local_dir"]).glob("state-*.sqlite"))
        assert encs and snaps and encs[0].read_bytes().startswith(MAGIC_V2)
        # ⑧ 密钥纪律：口令字面量不得出现在任何持久化介质
        for p in [Path(c["paths"]["root"]) / "state.sqlite", *snaps, *encs,
                  *(root / "logs").glob("*"), *(Path(c["paths"]["reports"])).rglob("*")]:
            if p.is_file():
                assert PASS.encode() not in p.read_bytes(), f"口令泄漏于 {p}"

        d = cl.delete(f"/docs/{doc}?confirm=true", headers=h)
        assert d.status_code == 200, d.get_json()
        assert cl.get(f"/docs/{doc}", headers=h).status_code == 404
        assert cl.get(f"/docs/{doc}/markdown", headers=h).status_code == 404
        assert cl.get("/search?q=" + urllib_parse_quote("演练改写"),
                      headers=h).get_json()["total"] == 0
        # ④ 库内不可见 + 产物文件已清除
        assert st.q1("SELECT COUNT(*) c FROM blocks WHERE doc_id=?", (doc,))["c"] == 0
        assert st.q1("SELECT COUNT(*) c FROM search_blocks WHERE doc_id=?", (doc,))["c"] == 0
        assert st.q1("SELECT COUNT(*) c FROM edit_audit WHERE doc_id=?", (doc,))["c"] == 0
        assert st.q1("SELECT COUNT(*) c FROM events WHERE doc_id=?", (doc,))["c"] == 0
        assert not list((root / "out").glob(f"*{doc}*"))
        # ⑤ 留痕不含正文
        tomb = cl.get("/audit/deletions", headers=h).get_json()["deletions"][-1]
        assert set(tomb) == {"doc_id", "source_key", "actor", "ts", "kind", "cascade"}
        assert "演练改写" not in json.dumps(tomb, ensure_ascii=False)

    # ⑦ 解密恢复的历史快照必须与备份源逐字节一致（「可挂载 / 可解密恢复」硬判据）
    restored = tmp_path / "restored.sqlite"
    plan = BackupPlan(local_dir=Path(c["ops"]["backup_local_dir"]),
                      external_dir=tmp_path / "ext")
    plan.restore(encs[0], restored, PASS)
    src_hash = hashlib.sha256(Path(snaps[-1]).read_bytes()).hexdigest()
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == src_hash, "恢复副本与快照不一致"
    assert sqlite3.connect(str(restored)).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # ⑥ 轮出：把快照 mtime 拨到 15 天前 → 一律删除
    old = (datetime.now(timezone.utc) - timedelta(days=15)).timestamp()
    for p in snaps + encs:
        os.utime(p, (old, old))
    assert len(plan.rotate()) == len(snaps) + len(encs)
    app.config["doc2md"]["writer"].close()
    st.close()


def urllib_parse_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# --- CH-460 / A-11：容器 v2（随机盐 + 分块流式） ------------------------------
def test_v2_salt_is_random_per_call():
    """同口令同数据两次加密：盐与密文都不同（彩虹表预制失效）。"""
    data = b"payload-" + os.urandom(32)
    b1 = encrypt_bytes(data, PASS)
    b2 = encrypt_bytes(data, PASS)
    assert b1 != b2
    assert b1[7:7 + 16] != b2[7:7 + 16]        # 头部盐位不同
    assert decrypt_bytes(b1, PASS) == data and decrypt_bytes(b2, PASS) == data


def test_v1_container_still_decryptable():
    """历史 v1 容器（静态盐）保持可解密——存量外置副本的恢复路径不断。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from doc2md.ops.backup import LEGACY_SALT, MAGIC, derive_key
    nonce = os.urandom(12)
    key = derive_key(PASS, LEGACY_SALT)
    blob = MAGIC + nonce + AESGCM(key).encrypt(nonce, b"legacy-db-bytes", MAGIC)
    assert decrypt_bytes(blob, PASS) == b"legacy-db-bytes"


def test_stream_roundtrip_multi_chunk_and_tail(tmp_path):
    """多块流式 round-trip：整除 / 不整除末块 / 空输入三种形态。"""
    from doc2md.ops.backup import decrypt_stream, encrypt_stream
    for size in (0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 7, CHUNK_SIZE * 2 + 123):
        data = os.urandom(size)
        src = tmp_path / f"in-{size}"
        dst_enc = tmp_path / f"out-{size}.enc"
        dec = tmp_path / f"out-{size}.sqlite"
        src.write_bytes(data)
        with src.open("rb") as s, dst_enc.open("wb") as d:
            n = encrypt_stream(s, d, PASS)
        assert n == (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        with dst_enc.open("rb") as s, dec.open("wb") as d:
            decrypt_stream(s, d, PASS)
        assert dec.read_bytes() == data


def test_v2_swap_chunks_rejected(tmp_path):
    """交换两块密文 → 序号 AAD 认证失败（重排不可行）。"""
    from doc2md.ops.backup import decrypt_bytes, encrypt_bytes
    data = os.urandom(CHUNK_SIZE * 2 + 100)
    blob = bytearray(encrypt_bytes(data, PASS))
    hdr = 7 + 16 + 4 + 8
    blk = CHUNK_SIZE + 16 + 12
    a, b = blob[hdr:hdr + blk], blob[hdr + blk:hdr + blk * 2]
    blob[hdr:hdr + blk] = b
    blob[hdr + blk:hdr + blk * 2] = a
    with pytest.raises(InvalidTag):
        decrypt_bytes(bytes(blob), PASS)


def test_v2_truncated_container_restore_leaves_nothing(tmp_path):
    """截断容器 → restore 失败且 dst / .part 都不存在（不留半截库）。"""
    plan = BackupPlan(local_dir=tmp_path / "bk", external_dir=tmp_path / "ext")
    plan.local_dir.mkdir(parents=True)
    snap = plan.local_dir / "state-x.sqlite"
    snap.write_bytes(os.urandom(8192))
    out = plan.to_external(snap, PASS)
    cut = tmp_path / "cut.enc"
    cut.write_bytes(out.read_bytes()[: -32])   # 掐掉末块尾部 → 结构截断
    dst = tmp_path / "restored.sqlite"
    with pytest.raises((InvalidTag, ValueError)):
        plan.restore(cut, dst, PASS)
    assert not dst.exists()
    assert not dst.with_name(dst.name + ".part").exists()


def test_v2_header_total_tamper_rejected():
    """改头部总块数 → 每块 AAD 都绑定 total，首块即认证失败。"""
    blob = bytearray(encrypt_bytes(b"some-db" * 100, PASS))
    blob[7 + 16 + 4] ^= 0x01                   # total_chunks 最低位翻位
    # total 被改小 → 尾随字节被拒；被改大 → 结构截断被拒。两条路都不给过。
    with pytest.raises((InvalidTag, ValueError)):
        decrypt_bytes(bytes(blob), PASS)


# --- CH-467：pre-push 红线钩子（直推 main 的唯一防线；必需检查不拦直推，见 CH-470） --
def test_pre_push_hook_valid_and_gate_aligned():
    """钩子存在、可执行、语法合法，且红线口径与 CI merge-gate 一致（-m gate）。"""
    import subprocess
    hook = Path(__file__).resolve().parent.parent / "ops" / "hooks" / "pre-push"
    assert hook.is_file()
    assert os.access(hook, os.X_OK), "钩子必须可执行"
    src = hook.read_text(encoding="utf-8")
    assert "pytest" in src and "-m gate" in src, "红线口径必须与 CI merge-gate 同一断言面"
    assert "no:cacheprovider" in src
    assert subprocess.run(["sh", "-n", str(hook)], capture_output=True).returncode == 0


def test_hooks_path_enabled_hint():
    """仓库内钩子目录已在 README 说明启用方式（core.hooksPath 是本机配置，不可断言）。"""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "core.hooksPath ops/hooks" in readme
