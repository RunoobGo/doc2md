"""tests/test_ch469_service.py — CH-469 批次②：服务层与存储契约（V-1…V-13）。

覆盖对象（逐项对应本轮登记）：
- V-1：PUT /settings 落盘为**稀疏差量**，多次 PUT 的键在 reload 后不互相回退；
- V-2：遗骸回收在常驻 work 循环里按周期重跑（原只在进程启动时一次）；
- V-3：产物回读不到 IR 的作业不得标 done（原实现队列说「完成」、GET 却 404）；
- V-4：events(job_id/doc_id) 与 tasks(doc_id) 补索引（含 v4 存量库迁移）；
- V-5：`GET /docs/<id>` 与审计/删除留痕支持 offset/limit 窗口，不带参数仍取全部；
- V-6：整源删除一次目录遍历（原 N 个文档 = 3N 次全树 rglob）；
- V-7：未知角色签发 token → 400/E4001（原 ValueError 逃逸 500）；
- V-8：`base_revision` 非数字 → 400/E4001（同上）；
- V-9：搜索面 FTS 语法类错误降级 LIKE 且留 `fts_error`，库面故障不再被吞；
- V-10：配置热更 copy-on-write，不就地改写并发请求正在读的共享嵌套 dict；
- V-11：审阅页对**文档派生字段**一律转义（构造文档即可执行脚本的 XSS 面）；
- V-12：CLI 未归类异常收口为 EXIT_SYS、watch 退出码不被后续拒收降级、连接收尾；
- V-13：吊销不存在的 token 明确报 404 / 退出码 4（原无条件报成功）。

方法论：API 面走真实 Flask test client（与红线层同口径），存储面用真实 sqlite；
run_batch / purge_many 等外圈用 monkeypatch 记录调用次数（断言的是「几趟」而不是耗时）。
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from doc2md import config
from doc2md.ir import IR, Block
from doc2md.storage import repo
from doc2md.storage.db import DB_VERSION, Store

pytestmark = [pytest.mark.milestone("DoD")]


def _seed(store: Store, doc_id: str = "d1", source_key: str = "s1", n: int = 5) -> IR:
    ir = IR(doc_id=doc_id, source_key=source_key, source_name="demo.pdf",
            format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"第 {i} 块 例外情况", page=1)
                 for i in range(1, n + 1)]
    repo.save_ir(store, ir)
    return ir


def _app_store(client) -> Store:
    return client.application.config["doc2md"]["store"]


# --- V-1 / V-10：设置面 --------------------------------------------------------


@pytest.mark.gate
def test_settings_put_accumulates_across_reloads(api_client, headers, tmp_path):
    """两次 PUT 各改不同子表的键，reload 后必须**都在**。

    原实现 PUT 直接把本次 body 整体写进 settings_override.json，第二次 PUT 覆盖
    文件 → 第一次的修改在重启/reload 后静默回退（用户改过又"消失"的配置）。
    """
    client, tokens = api_client
    r = client.put("/settings", json={"perf": {"workers": 2}}, headers=headers(tokens))
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.put("/settings", json={"api": {"port": 9001}}, headers=headers(tokens))
    assert r.status_code == 200, r.get_data(as_text=True)

    diff = json.loads((tmp_path / "work" / "settings_override.json").read_text("utf-8"))
    assert diff == {"perf": {"workers": 2}, "api": {"port": 9001}}, diff

    assert client.post("/system/reload", headers=headers(tokens)).status_code == 200
    s = client.get("/settings", headers=headers(tokens, "reader")).get_json()
    assert int(s["perf"]["workers"]) == 2 and int(s["api"]["port"]) == 9001


def test_settings_override_stays_sparse(api_client, headers):
    """差量口径：未被改过的默认键不得冻结进 override（否则升级后新默认值被旧值压住）。"""
    client, tokens = api_client
    base = client.get("/settings", headers=headers(tokens, "reader")).get_json()
    client.put("/settings", json={"logs": {"rotate_mb": 12}}, headers=headers(tokens))
    diff = json.loads((Path(client.application.config["doc2md"]["root"])
                       / "settings_override.json").read_text("utf-8"))
    assert set(diff) == {"logs"}
    assert diff["logs"] == {"rotate_mb": 12}
    assert "s2" not in diff and "paths" not in diff
    # 默认值本身仍在（只是没被写进差量）
    assert "s2" in base


@pytest.mark.gate
def test_settings_put_does_not_mutate_shared_nested_dicts(api_client, headers):
    """copy-on-write：热更替换整个配置对象，不就地改写并发读者手上的嵌套 dict。

    原实现 `merged = dict(cfg)` 是浅拷贝，`_deep` 递归改的是**共享**的内层 dict——
    另一个请求线程此刻读 `cfg["perf"]["workers"]` 会读到半改状态。
    """
    client, tokens = api_client
    holder = client.application.config["doc2md"]
    inner_before = holder["cfg_baseline"]["perf"]
    snapshot = dict(inner_before)
    client.put("/settings", json={"perf": {"workers": 3, "queue_async": True}},
               headers=headers(tokens))
    assert holder["cfg_baseline"]["perf"] == snapshot      # 基线一字未动
    assert holder["cfg_baseline"]["perf"] is inner_before  # 同一对象，未被就地替换
    assert int(holder["cfg"]["perf"]["workers"]) == 3


# --- V-2：常驻循环的遗骸回收 ----------------------------------------------------


@pytest.mark.gate
def test_reclaim_reruns_inside_resident_loop(tmp_cfg, tmp_path, monkeypatch):
    """work 常驻进程必须周期性重跑遗骸回收。

    原实现只在 `run_work` 入口调一次 `reclaim_stale`：进程跑几天后，期间被 OOM
    干掉的**同行**进程留下的 running 行永不回收——正是 CH-448 要防的场景。
    """
    from doc2md import worker

    calls = {"reclaim": 0, "drain": 0}

    def spy_reclaim(store, cfg):
        calls["reclaim"] += 1
        return 0

    class _Stop(Exception):
        pass

    def fake_drain(store, cfg, root, **kw):
        calls["drain"] += 1
        import time as _t
        _t.sleep(0.03)
        if calls["drain"] >= 3:
            raise _Stop()
        return worker.WorkReport()

    (tmp_path / "work" / "logs").mkdir(parents=True)
    monkeypatch.setattr(worker, "reclaim_stale", spy_reclaim)
    monkeypatch.setattr(worker, "drain_once", fake_drain)
    with pytest.raises(_Stop):
        worker.run_work(tmp_cfg, tmp_path / "work", workers=1,
                        idle_seconds=0.0, reclaim_seconds=0.01)
    assert calls["reclaim"] >= 2, calls          # 启动 1 次 + 循环内至少 1 次


def test_reclaim_seconds_zero_keeps_startup_only(tmp_cfg, tmp_path, monkeypatch):
    """`<=0` 回到旧行为（只启动时一次），给排障留一个开关。"""
    from doc2md import worker

    calls = {"reclaim": 0, "drain": 0}

    def spy_reclaim(store, cfg):
        calls["reclaim"] += 1
        return 0

    class _Stop(Exception):
        pass

    def fake_drain(store, cfg, root, **kw):
        calls["drain"] += 1
        raise _Stop()

    (tmp_path / "work" / "logs").mkdir(parents=True)
    monkeypatch.setattr(worker, "reclaim_stale", spy_reclaim)
    monkeypatch.setattr(worker, "drain_once", fake_drain)
    with pytest.raises(_Stop):
        worker.run_work(tmp_cfg, tmp_path / "work", workers=1, reclaim_seconds=0)
    assert calls["reclaim"] == 1


# --- V-3：产物读不到 IR 不得标 done --------------------------------------------


@pytest.mark.gate
def test_drain_fails_when_artifact_ir_unreadable(tmp_cfg, tmp_path, monkeypatch):
    """zip 里缺 ir.json / 损坏 → 任务明确 failed，而不是「done 但文档 404」。"""
    from doc2md import worker
    from doc2md.scheduler import Scheduler

    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    src = root / "x.pdf"
    src.write_bytes(b"%PDF-1.7\n")
    sched = Scheduler(store)
    job = sched.submit("d1", "s1", source_path=str(src), mode=None)

    monkeypatch.setattr(worker, "run_batch", lambda files, **kw: [
        {"file": str(src), "status": "ok", "zip": str(root / "missing.zip"),
         "blocks": 3, "doc_id": "d1", "code": "", "message": ""}])
    rep = worker.drain_once(store, tmp_cfg, root, workers=1)
    assert rep.drained == 1 and rep.failed == 1 and rep.done == 0, rep.to_dict()
    row = store.q1("SELECT status, error_code FROM tasks WHERE job_id=?", (job.job_id,))
    assert row["status"] == "failed" and row["error_code"] == "E5031"
    assert store.q1("SELECT COUNT(*) c FROM docs")["c"] == 0
    codes = [r["code"] for r in store.q("SELECT code FROM events")]
    assert "job_failed" in codes and "job_done" not in codes
    store.close()


def test_drain_finishes_ok_when_ir_readable(tmp_cfg, tmp_path, monkeypatch):
    """正向对照：产物正常时 done 路径不受影响（入库 + 事件 + 明细同笔事务）。"""
    import zipfile

    from doc2md import worker
    from doc2md.scheduler import Scheduler

    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    src = root / "x.pdf"
    src.write_bytes(b"%PDF-1.7\n")
    sched = Scheduler(store)
    job = sched.submit("d1", "s1", source_path=str(src), mode=None)

    ir = IR(doc_id="d1", source_key="s1", source_name="x.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="正文一块", page=1)]
    zp = root / "ok.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("ir.json", json.dumps(ir.to_dict()))
    monkeypatch.setattr(worker, "run_batch", lambda files, **kw: [
        {"file": str(src), "status": "ok", "zip": str(zp),
         "blocks": 1, "doc_id": "d1", "code": "", "message": ""}])
    rep = worker.drain_once(store, tmp_cfg, root, workers=1)
    assert rep.done == 1 and rep.failed == 0, rep.to_dict()
    assert store.q1("SELECT status FROM tasks WHERE job_id=?", (job.job_id,))["status"] == "done"
    assert store.q1("SELECT COUNT(*) c FROM blocks WHERE doc_id='d1'")["c"] == 1
    detail = json.loads(store.q1("SELECT detail_json FROM tasks WHERE job_id=?",
                                 (job.job_id,))["detail_json"])
    assert detail["zip"] == str(zp)              # CH-459 的 detail 覆盖修复仍成立
    store.close()


# --- V-4：索引与存量库迁移 ------------------------------------------------------


def test_new_db_has_hot_path_indexes(store):
    names = {r["name"] for r in store.q(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_events_job", "ix_events_doc", "ix_tasks_doc"} <= names, sorted(names)


def test_v4_db_migration_adds_indexes(tmp_path):
    """存量库（user_version=4、索引缺失）必须由迁移补上新索引，且数据不动。

    部分唯一索引 `uq_tasks_doc_active` 只覆盖 queued/running，`DELETE FROM tasks
    WHERE doc_id=?`（FR-128 级联）与历史回溯没有可用索引 → 全表扫描。
    """
    p = tmp_path / "old.sqlite"
    s = Store(p)
    _seed(s, "d1", n=2)
    s.close()

    # 复刻 v4 存量库：把本轮新增的三条索引抹掉并降回 user_version=4
    conn = sqlite3.connect(str(p))
    for ix in ("ix_tasks_doc", "ix_events_job", "ix_events_doc"):
        conn.execute(f"DROP INDEX IF EXISTS {ix}")
    conn.execute(f"PRAGMA user_version={DB_VERSION - 1}")
    conn.commit()
    conn.close()

    s2 = Store(p)
    names = {r["name"] for r in s2.q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_events_job", "ix_events_doc", "ix_tasks_doc"} <= names, sorted(names)
    assert s2.q1("SELECT COUNT(*) AS c FROM blocks WHERE doc_id='d1'")["c"] == 2
    assert int(s2._conn.execute("PRAGMA user_version").fetchone()[0]) == DB_VERSION
    s2.close()


# --- V-5：窗口参数 --------------------------------------------------------------


@pytest.mark.gate
def test_docs_blocks_window_and_total(api_client, headers):
    """带 limit 走 SQL 窗口并回 `blocks_total`；不带参数仍取全部（契约不漂移）。"""
    client, tokens = api_client
    _seed(_app_store(client), "dwin", n=5)
    h = headers(tokens, "reader")
    full = client.get("/docs/dwin", headers=h).get_json()
    assert len(full["blocks"]) == 5 and full["blocks_total"] == 5

    win = client.get("/docs/dwin?offset=2&limit=2", headers=h).get_json()
    assert [b["id"] for b in win["blocks"]] == ["b3", "b4"]
    assert win["blocks_total"] == 5 and win["blocks_offset"] == 2

    tail = client.get("/docs/dwin?offset=4&limit=10", headers=h).get_json()
    assert [b["id"] for b in tail["blocks"]] == ["b5"]


def test_docs_window_bad_params_are_400(api_client, headers):
    client, tokens = api_client
    _seed(_app_store(client), "dbad", n=2)
    h = headers(tokens, "reader")
    for qs in ("limit=abc", "offset=-1", "limit=-2"):
        r = client.get(f"/docs/dbad?{qs}", headers=h)
        assert r.status_code == 400, (qs, r.get_data(as_text=True)[:200])
        assert r.get_json()["code"] == "E4001"


def test_audit_and_deletions_are_windowed(api_client, headers):
    """审计每行带 before/after 两份正文，是最重的一次序列化 → 必须可开窗。"""
    client, tokens = api_client
    store = _app_store(client)
    _seed(store, "daud", n=1)
    for i in range(4):
        with store.tx() as tx:
            tx.execute(
                "INSERT INTO edit_audit(doc_id, block_id, before, after, from_revision,"
                " to_revision, actor, ts, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                ("daud", "b1", f"前{i}", f"后{i}", i + 1, i + 2, "t", "2026-01-01T00:00:00+00:00", ""))
    j = client.get("/docs/daud/audit?block_id=b1&limit=2",
                   headers=headers(tokens, "editor")).get_json()
    assert len(j["audit"]) == 2 and j["total"] == 4, j
    j2 = client.get("/docs/daud/audit?block_id=b1", headers=headers(tokens, "editor")).get_json()
    assert len(j2["audit"]) == 4 and j2["total"] == 4

    d = client.get("/audit/deletions?limit=1", headers=headers(tokens)).get_json()
    assert "total" in d and len(d["deletions"]) <= 1


# --- V-6：整源删除一次遍历 ------------------------------------------------------


def test_delete_source_purges_with_a_single_walk(tmp_cfg, tmp_path):
    """两个版本共用一趟目录遍历（原实现每文档 rglob 三棵树）。"""
    root = tmp_path / "work"
    (root / "out").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    for d in ("docA", "docB"):
        _seed(store, d, source_key="same", n=1)
        (root / "out" / f"{d}.zip").write_bytes(b"PK\x03\x04")

    walks: list[int] = []
    real = repo.purge_many

    def spy(ids, r, **kw):
        walks.append(len(list(ids)))
        return real(ids, r, **kw)

    repo.purge_many = spy
    try:
        out = repo.delete_source(store, "same", "tester", root)
    finally:
        repo.purge_many = real

    # 提交前后各一次定向清扫（收窄产物复活窗口）；每次仍是一次遍历覆盖批内全部 doc。
    assert walks and all(n == 2 for n in walks), walks
    assert not list((root / "out").glob("*"))     # 产物删净
    assert store.q1("SELECT COUNT(*) c FROM docs")["c"] == 0
    assert len(out["files"]) == 2
    row = store.q1("SELECT cascade_json FROM deletions WHERE kind='source'")
    assert json.loads(row["cascade_json"])["files"] == 2
    store.close()


def test_delete_source_fail_closed_keeps_rows(tmp_path, monkeypatch):
    """产物没删净 → 整源删除 fail-closed，库行保留可重试（与 delete_doc 同口径）。"""
    root = tmp_path / "work"
    (root / "out").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    _seed(store, "docC", source_key="srcC", n=1)
    (root / "out" / "docC.zip").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(repo, "purge_many",
                        lambda ids, r: ([], [str(root / "docC.zip")]))
    from doc2md.errors import E5031_StorageError
    with pytest.raises(E5031_StorageError):
        repo.delete_source(store, "srcC", "tester", root)
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='docC'")["c"] == 1
    store.close()


# --- V-7 / V-13：token 面 -------------------------------------------------------


@pytest.mark.gate
def test_create_token_unknown_role_is_400(api_client, headers):
    """原实现把任意 role 直送 auth.issue → ValueError 逃逸 500 + HTML。"""
    client, tokens = api_client
    r = client.post("/tokens", json={"role": "root"}, headers=headers(tokens))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    body = r.get_json()
    assert body["code"] == "E4001" and "admin" in body["detail"]["allowed"]


def test_create_token_respects_narrowed_roles(api_client, headers):
    """`api.token_roles` 收窄后，被排除的角色同样 400（不是 500）。"""
    client, tokens = api_client
    holder = client.application.config["doc2md"]
    holder["cfg"]["api"]["token_roles"] = ["reader"]
    r = client.post("/tokens", json={"role": "editor"}, headers=headers(tokens))
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"
    ok = client.post("/tokens", json={"role": "reader"}, headers=headers(tokens))
    assert ok.status_code == 200 and ok.get_json()["role"] == "reader"


@pytest.mark.gate
def test_revoke_unknown_token_is_404(api_client, headers):
    """原实现无条件 `{"revoked": true}`——「没吊销任何东西」被报成「吊销成功」。"""
    client, tokens = api_client
    r = client.delete("/tokens/d2m_not_a_real_token", headers=headers(tokens))
    assert r.status_code == 404, r.get_data(as_text=True)[:200]
    assert r.get_json()["code"] == "E4041"


def test_revoke_real_token_then_401(api_client, headers):
    client, tokens = api_client
    _seed(_app_store(client), "drev", n=1)
    tok = client.post("/tokens", json={"role": "reader"},
                      headers=headers(tokens)).get_json()["token"]
    h = {"X-Doc2md-Token": tok}
    assert client.get("/docs/drev", headers=h).status_code == 200
    r = client.delete(f"/tokens/{tok}", headers=headers(tokens))
    assert r.status_code == 200 and r.get_json()["revoked"] is True
    assert client.get("/search?q=例外", headers=h).status_code == 401
    # 二次吊销同一 token：revoked_at 已置但行仍在 → rowcount=1，仍报成功（幂等）；
    # 只有「库里根本没有」才 404（见 test_revoke_unknown_token_is_404）。
    # CH-473（TEST-2，LOW）：原实现在此重复了两遍完全相同的 status 断言——
    # 改为断言响应体契约（revoked 恒 true），使每次重复调用都有独立信息量。
    again = client.delete(f"/tokens/{tok}", headers=headers(tokens))
    assert again.status_code == 200 and again.get_json()["revoked"] is True


# --- V-8：乐观锁入参 ------------------------------------------------------------


@pytest.mark.gate
def test_patch_base_revision_non_numeric_is_400(api_client, headers):
    """`base_revision: "abc"` 原在 edits 里 int() 直转 → 500。"""
    client, tokens = api_client
    store = _app_store(client)
    _seed(store, "drev", n=1)
    h = headers(tokens, "editor")
    for bad in ("abc", {"x": 1}, [1]):
        r = client.patch("/docs/drev/blocks/b1", headers=h,
                         json={"text": "改", "base_revision": bad})
        assert r.status_code == 400, (bad, r.get_data(as_text=True)[:200])
        assert r.get_json()["code"] == "E4001"
    # 数字字符串仍是合法输入（不收紧既有契约）
    ok = client.patch("/docs/drev/blocks/b1", headers=h,
                      json={"text": "改", "base_revision": "1"})
    assert ok.status_code == 200 and ok.get_json()["revision"] == 2


def test_patch_missing_base_revision_still_400(api_client, headers):
    client, tokens = api_client
    _seed(_app_store(client), "drev2", n=1)
    r = client.patch("/docs/drev2/blocks/b1", headers=headers(tokens, "editor"),
                     json={"text": "改"})
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"


# --- V-9：搜索面错误可见 --------------------------------------------------------


def test_search_fts_syntax_error_degrades_with_marker(store):
    """FTS 表达式类 OperationalError：仍可 LIKE 兜底，但响应必须带 fts_error。"""
    _seed(store, "dft", n=2)

    def fake_q(sql, params=()):
        if "MATCH" in sql:
            raise sqlite3.OperationalError("fts5: syntax error near \"\"")
        return Store.q(store, sql, params)

    store.q = fake_q                            # type: ignore[method-assign]
    out = repo.search(store, "例外")
    assert out["mode"] == "like" and out["fts_error"] == "OperationalError", out
    assert out["total"] >= 1                    # 兜底确实找到了内容


def test_search_db_fault_is_not_swallowed(store):
    """库面故障（损坏 / 磁盘）原被 `except Exception: pass` 吞成「没搜到」。"""
    from doc2md.errors import E5031_StorageError
    _seed(store, "dft2", n=1)

    def fake_q(sql, params=()):
        if "MATCH" in sql:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return Store.q(store, sql, params)

    store.q = fake_q                            # type: ignore[method-assign]
    with pytest.raises(E5031_StorageError) as ei:
        repo.search(store, "例外")
    assert ei.value.detail["reason"] == "DatabaseError"
    assert "例外" not in json.dumps(ei.value.to_body())   # NFR-20：detail 不带查询内容


# --- V-11：审阅页转义 -----------------------------------------------------------


def _editor_html() -> str:
    import doc2md
    p = Path(doc2md.__file__).resolve().parent / "static" / "editor.html"
    return p.read_text(encoding="utf-8")


# 文档派生字段（源文档可控）的裸插形态：${b.id} / ${t.source_path} …
_BARE_FIELD = re.compile(
    r"\$\{\s*(?:b|blk|t|det|d|h)\s*\.\s*"
    r"(?:id|bid|text|type|revision|level|status|doc_id|source_path|job_id|"
    r"reason|error_code|mode|absorbed_ids)")


def _inner_html_templates(html: str) -> list[str]:
    """取出所有赋给 `.innerHTML` 的模板串正文——只有这个汇点需要转义。
    （`log()` 一类走 textContent，裸插无害，不该被误判。）"""
    out: list[str] = []
    for m in re.finditer(r"\.innerHTML\s*=\s*`", html):
        start = m.end()
        end = html.find("`", start)
        while end != -1 and html[end - 1] == "\\":
            end = html.find("`", end + 1)
        if end != -1:
            out.append(html[start:end])
    return out


def test_editor_escapes_document_derived_fields():
    """块 id / 正文 / 状态来自源文档，不得裸插 innerHTML（构造文档即可执行脚本）。"""
    html = _editor_html()
    templates = _inner_html_templates(html)
    assert templates, "editor.html 没有 innerHTML 模板？扫描口径需复核"
    offenders = []
    for tpl in templates:
        for m in _BARE_FIELD.finditer(tpl):
            expr = m.group(0)[2:].strip()
            if "esc(" not in expr:
                offenders.append("${" + expr + "}")
    assert not offenders, offenders
    # 正例：这几处本轮亲自补过转义，回归时最容易被回退
    assert "esc(b.id)" in html and "esc(b.type)" in html
    assert "${esc(b.text || \"\")}" in html
    assert "absorbed_ids.map(esc).join(" in html
    # 块 id 不得再拼进 CSS 属性选择器（含引号的 id 能改写选择器）
    assert '[data-bid="${' not in html
    # 队列面板的状态类同样来自数据面
    assert 'c-${esc(t.status)}' in html
    # dataset 查询用 bid 变量而不是拼接的选择器
    assert "blkEl = (bid) =>" in html


@pytest.mark.gate
def test_editor_log_sink_is_text_content():
    """日志侧的裸插（`${t.job_id.slice(0, 8)}` 等）只在 textContent 汇点里出现；
    一旦日志改成 innerHTML 拼串，上面的转义扫描必须同时抓到——两条断言互为保险。"""
    html = _editor_html()
    assert re.search(r'function log\([\s\S]*?\bel\.textContent\s*=', html)
    assert "document.write" not in html and "insertAdjacentHTML" not in html
    # 探针正文不会因「测试数据」落到源码里（真实浏览器验证属登记项）
    assert "<img src=x onerror=" not in html


# --- V-12：CLI 兜底与退出码 -----------------------------------------------------


@pytest.mark.gate
def test_cli_unexpected_exception_maps_to_exit_sys(tmp_path, capsys, monkeypatch):
    """未归类异常（第三方解析栈）必须收口为 EXIT_SYS + JSON，而不是栈回溯 + 退出码 1。"""
    from doc2md import cli

    def boom(args):
        raise RuntimeError("boom 含正文片段")

    monkeypatch.setitem(cli.COMMANDS, "doctor", boom)
    assert cli.main(["doctor", "--root", str(tmp_path)]) == cli.EXIT_SYS
    line = capsys.readouterr().err.strip().splitlines()[-1]
    body = json.loads(line)
    assert body["code"] == "E5000"
    assert "boom 含正文片段" not in line and "RuntimeError" in line   # NFR-20


def test_cli_watch_gate_reject_does_not_downgrade_exit_code(tmp_path, monkeypatch, capsys):
    """watch 里后到的拒收不得把已判定的 EXIT_BIZ 降回 EXIT_GATE（原为赋值不是 max）。"""
    from doc2md import cli
    from doc2md.errors import E4151_UnsupportedType, E4221_GateReject

    work_root = tmp_path / "work"
    work_root.mkdir(parents=True)
    bad, gated = tmp_path / "bad.pdf", tmp_path / "gate.pdf"
    for f in (bad, gated):
        f.write_bytes(b"%PDF-1.7\n")

    class FakeTracker:
        def __init__(self, stable_seconds: float = 0.0):
            self.stable_seconds = 0.0

        def poll_all(self, d, lim):
            # 同一趟先业务失败、后门禁拒收：赋值式 `rc = EXIT_GATE` 会把 4 降成 3
            return [str(bad), str(gated)]

    def fake_convert(p, **kw):
        if Path(p).name == "gate.pdf":
            raise E4221_GateReject("密度不足", detail={"mode": "auto"})
        raise E4151_UnsupportedType("非文本 PDF")

    monkeypatch.setattr("doc2md.security.watcher.StabilityTracker", FakeTracker)
    # `doc2md.pipeline.convert` 属性指的是 `__init__` 里再导出的 convert 函数本身，
    # 按字符串路径补丁会打到函数上；须经 import 机制取到模块对象。
    convert_mod = importlib.import_module("doc2md.pipeline.convert")
    monkeypatch.setattr(convert_mod, "convert", fake_convert)

    class Args:
        # 类体内 `root` 是本级名字，不能引用外层同名变量 → 先换成 work_dir
        cmd, dir, once, interval, root = "watch", str(tmp_path), True, 0.0, str(work_root)
        config = None

    assert cli.cmd_watch(Args()) == cli.EXIT_BIZ
    err = capsys.readouterr().err
    assert "E4151" in err and "E4221" in err


def test_cli_watch_once_scan_error_counts_exit_biz(tmp_path, monkeypatch, capsys):
    """QA 缺陷 1（CH-492）：--once 模式下扫描级 Doc2mdError 必须计入 rc。

    原实现 except 分支只打印不抬 rc——cron/launchd 按 `watch --once` 退出码
    判成功时，目录打错 / 超限 / 被删会静默假成功（exit=0；对照组 convert
    同场景 exit=4）。stderr 的 E4133 行保持只打印语义不变。
    """
    from doc2md import cli
    from doc2md.errors import E4133_DirTooWide

    class FakeTracker:
        def __init__(self, stable_seconds: float = 0.0):
            self.stable_seconds = 0.0

        def poll_all(self, d, lim):
            raise E4133_DirTooWide("not a directory")

    monkeypatch.setattr("doc2md.security.watcher.StabilityTracker", FakeTracker)

    class Args:
        cmd, dir, once, interval, root = "watch", str(tmp_path / "no-such"), True, 0.0, str(tmp_path / "w")
        config = None

    assert cli.cmd_watch(Args()) == cli.EXIT_BIZ
    assert "E4133" in capsys.readouterr().err


def test_cli_watch_resident_scan_error_keeps_polling(tmp_path, monkeypatch, capsys):
    """QA 缺陷 1（CH-492）：常驻模式（无 --once）扫描失败只打印、继续轮询。

    常驻模式的下一轮本就带补齐语义（security/watcher.poll_all），一次扫描
    失败不得中断循环；rc 亦不在常驻路径返回。这里以「失败后仍再次 poll、
    KeyboardInterrupt 模拟 Ctrl-C」证明循环未被一次扫描失败打断。
    """
    from doc2md import cli
    from doc2md.errors import E4133_DirTooWide

    calls = {"n": 0}

    class FakeTracker:
        def __init__(self, stable_seconds: float = 0.0):
            self.stable_seconds = 0.0

        def poll_all(self, d, lim):
            calls["n"] += 1
            if calls["n"] == 1:
                raise E4133_DirTooWide("not a directory")
            raise KeyboardInterrupt                      # 模拟常驻模式下 Ctrl-C

    monkeypatch.setattr("doc2md.security.watcher.StabilityTracker", FakeTracker)

    class Args:
        cmd, dir, once, interval, root = "watch", str(tmp_path), False, 0.0, str(tmp_path / "w")
        config = None

    with pytest.raises(KeyboardInterrupt):
        cli.cmd_watch(Args())
    assert calls["n"] == 2                               # 失败后循环继续轮询
    assert "E4133" in capsys.readouterr().err


def test_cli_watch_closes_store(tmp_path, monkeypatch):
    """异常退出路径也要收尾 thread-local 连接（常驻循环独占一条写连接）。"""
    from doc2md import cli
    from doc2md.storage import db as dbmod

    closed = {"n": 0}
    real_close = dbmod.Store.close

    def spy_close(self):
        closed["n"] += 1
        real_close(self)

    monkeypatch.setattr(dbmod.Store, "close", spy_close)
    monkeypatch.setattr("doc2md.security.watcher.StabilityTracker",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("stop")))

    class Args:
        cmd, dir, once, interval, root = "watch", str(tmp_path), True, 0.0, str(tmp_path / "w")
        config = None

    with pytest.raises(RuntimeError):
        cli.cmd_watch(Args())
    assert closed["n"] >= 1


def test_cli_revoke_unknown_token_exit_biz(tmp_path, capsys):
    from doc2md import cli
    root = str(tmp_path / "work")
    assert cli.main(["serve", "token", "--root", root]) == cli.EXIT_OK      # 建库 + 签发
    assert cli.main(["serve", "token", "--root", root, "--revoke", "d2m_nope"]) == cli.EXIT_BIZ
    out = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert out["revoked"] is False and out["code"] == "E4041"


def test_cli_token_role_outside_configured_set_is_usage_error(tmp_path, capsys, monkeypatch):
    """argparse 的 choices 挡不住「配置里收窄了可签发角色」这条路径。"""
    from doc2md import cli

    real_get = config.get

    def fake_get(cfg, key, default=None):
        if key == "api.token_roles":
            return ["reader"]
        return real_get(cfg, key, default)

    monkeypatch.setattr(cli.config, "get", fake_get)
    rc = cli.main(["serve", "token", "--root", str(tmp_path / "work"), "--role", "editor"])
    assert rc == cli.EXIT_USAGE
    assert "token_roles" in capsys.readouterr().err


# --- 配置面新键 ----------------------------------------------------------------


def test_new_perf_keys_have_defaults(cfg):
    """本轮新增的两个配置键必须进默认表（否则 `doc2md work` 读不到节流值）。"""
    assert int(config.get(cfg, "perf.stale_check_seconds")) == 60
    assert int(config.get(cfg, "perf.batch_file_timeout_s")) == 0
