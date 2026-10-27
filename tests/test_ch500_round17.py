"""tests/test_ch500_round17.py — CH-500（第 17 轮系统性审查）本机可自证修复的红线。

对应审查登记项（口径见 docs/review-round17-CH-500.md）：

服务 / API 面
- R17-API-1：`body()` 的 Content-Type 判据用错对象——Werkzeug 把表单型正文消费进
  `request.form`，`request.data` 恒空，于是 urlencoded 带真实载荷仍折叠成 `{}`：
  `PUT /settings` 空转重写回 200、`POST /tokens` 把 `role=admin` 静默降级成 reader。
- R17-API-2：整数查询参数没有值域——`?since=<1e20>` 原样进 SQLite 绑定抛
  `OverflowError` → 500/E5000（HTML）。
- R17-API-3：路由级 `HTTPException`（405/415）透传成 Werkzeug 的 HTML 页，违反
  附录 C「响应错误一律走 {code,message,detail} 契约」。
- R17-API-4：`E_UNHANDLED` 写完不 flush——500 是低频事件，日志行要等下一个无关请求
  才落盘（实测三次 500 后 `logs/app.jsonl` 字节数不变）。
- R17-API-5：`/jobs` 缺 `total`/`has_more`（截断被调用方当全集）、`/docs/<id>/audit`
  有 `total` 却缺 `has_more`。
- R17-API-6：`repo.list_docs` 有分页信封与测试覆盖，却没有 HTTP 出口（附录 C 一直
  把 `GET /docs` 写成契约面），GUI 只能用 `GET /jobs?limit=50` 猜文档清单。
- R17-API-7：`/search` 的 `has_more=True` 是死胡同——`search()` 只收 `limit`、没有
  `offset`，第 21 条命中永久拿不到；`limit=0` 未归 D9 口径（空前缀反报 `has_more`）；
  三条查询两条无 `ORDER BY`、FTS 侧只按 `bm25`，翻页必然重叠或漏行。
- R17-API-8：`/jobs` 同族缺口——R17-API-5 补了 `total`/`has_more` 却没补游标，第 51
  条作业不可见；`created_at DESC` 无决胜位；信封不回显 `offset`/`limit`，GUI 队列
  面板把窗口截断当全集显示。

服务面
- R17-SVC-1：`events.emit` 的日志行用 `{..., **payload}`，payload 自带的业务错误码
  `code` **覆盖**了事件名——同一条事件在库里是 `job_rejected`、在 jsonl 里是 `E4091`。
- R17-SVC-2：`run_work` 的 `try` 起点在主循环之后，启动段抛异常时 `store.close()`
  不执行、也没有 `WORK_STOP`。

SQL
- R17-SQL-1：`status IN {ACTIVE}` 把 Python 元组的 repr 拼进 SQL；状态 GROUP BY
  在 api/worker/cron 复制三份。

管线面
- R17-PIPE-1：外链分支早于 `_escape_text`，链接文字里的 `<script>`、`*`、`~` 裸进产物。
- R17-PIPE-2：渲染红线 `assert_render_contract` 自 CH-417 起没有任何生产调用方。
- R17-PIPE-3：`sniff` 用裸 `except Exception: pass` 吞掉 `BadZipFile`——损坏的 .docx
  与「xlsx/epub 改名成 .docx」拿到一模一样的「unsupported magic」。三个分支里
  `zip_unreadable`（打开 ZIP 时 `OSError`）本机黑盒不可复现：`chmod 000` 先被开头
  8 字节的读取拦下，走的是另一条消息，故该分支只用白盒测试钉住。
- R17-PIPE-4：片级内部锚 `extra_meta["shard"]` 被 `IR.meta()` 平铺进交付面，
  多片文档交付了一个假的页范围。
- R17-PIPE-5：`_strip_page_furniture` 重建 RawPage 时丢 `rotation` / `rtl_ratio`。
- R17-PIPE-6：DOCX 行字号取「最后一个显式字号的 run」，run 排列顺序决定标题级别。

打包
- R17-PKG-1：`python -m doc2md` 报「No module named doc2md.__main__」。
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile

import pytest

from doc2md import auth
from doc2md.errors import E4151_UnsupportedType, E5031_StorageError
from doc2md.events import emit
from doc2md.ir import IR, Block
from doc2md.pipeline.s1_parse import Line, RawPage, sniff
from doc2md.pipeline.s3_structure import _strip_page_furniture
from doc2md.pipeline.s4_render import _contract_violation, assert_render_contract, render
from doc2md.scheduler import ACTIVE, ACTIVE_SQL, Scheduler
from doc2md.storage.db import Store

pytestmark = pytest.mark.gate


def _hdr(tokens: dict, role: str = "admin") -> dict:
    return {"X-Doc2md-Token": tokens[role]}


# --- R17-API-1：表单正文不得折叠成 {} ---------------------------------------
def test_form_encoded_body_is_rejected_not_empty(api_client, tmp_path):
    """实测的「看似成功」形态：urlencoded 真载荷 → 400，且不落 override 文件。"""
    client, tokens = api_client
    r = client.put("/settings", data="api.bind=127.0.0.1",
                   content_type="application/x-www-form-urlencoded",
                   headers=_hdr(tokens))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert r.get_json()["code"] == "E4001"
    override = tmp_path / "work" / "settings_override.json"
    assert not override.exists(), "空转重写必须不发生（CH-495 R14-API-2 口径）"


def test_form_encoded_role_admin_does_not_downgrade_to_reader(api_client):
    client, tokens = api_client
    r = client.post("/tokens", data="role=admin",
                    content_type="application/x-www-form-urlencoded",
                    headers=_hdr(tokens))
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"
    # 原缺陷：折叠成 {} → role 缺省 reader，回 200 并签出一张 reader 票
    # B-53 同主语正断言（CH-512）：同一判据走同一条 `POST /tokens` 的正常签发面，
    # 键名 `token` 必须命中恰好 1 处——否则「400 里没有票」可能只是响应体压根没键。
    leak_hits = list(r.get_json() or {}).count("token")
    ok = client.post("/tokens", json={"role": "admin", "label": "ch512"}, headers=_hdr(tokens))
    assert ok.status_code == 200, ok.get_data(as_text=True)[:200]
    good_hits = list(ok.get_json()).count("token")
    assert (leak_hits, good_hits) == (0, 1), (
        f"token 泄漏判据无感：400 响应命中 {leak_hits} 处、正常签发面命中 {good_hits} 处（应为 0 / 1）")


def test_true_empty_body_still_defaults_to_empty_object(api_client):
    """收紧只针对「有正文却不是 JSON」；真·空体维持 200 缺省口径。"""
    client, tokens = api_client
    r = client.put("/settings", headers=_hdr(tokens))
    assert r.status_code == 200, r.get_data(as_text=True)[:200]


# --- R17-API-2：整数参数钉在 SQLite INTEGER 域 ------------------------------
@pytest.mark.parametrize("path,qs,role", [
    ("/events", {"since": "9" * 25}, "reader"),
    ("/audit/deletions", {"offset": "-" + "9" * 25}, "admin"),
    ("/jobs", {"limit": "9" * 25}, "reader"),
])
def test_out_of_range_int_query_args_return_400(api_client, path, qs, role):
    """`?since=1e20` 原形态是 OverflowError → 500/E5000（HTML 页）。"""
    client, tokens = api_client
    r = client.get(path, query_string=qs, headers=_hdr(tokens, role))
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:200])
    body = r.get_json()
    assert body["code"] == "E4001" and "range" in body["message"], body


# --- R17-API-3：路由级 HTTPException 走 JSON 契约 ---------------------------
def test_method_not_allowed_returns_json_contract(api_client):
    client, _ = api_client
    r = client.put("/healthz")
    assert r.status_code == 405, r.get_data(as_text=True)[:200]
    assert r.mimetype == "application/json"
    body = r.get_json()
    assert body["code"] == "E4001", body          # 不新增错误码（CODE_INDEX 冻结）
    d = body["detail"]
    assert d["reason"] == "method_not_allowed" and d["status"] == 405
    assert d["method"] == "PUT" and d["path"] == "/healthz"
    assert "allowed" in d and "GET" in d["allowed"]


def test_404_and_413_keep_their_specific_handlers(api_client):
    """`_http_exc` 是兜底，不得抢掉 404/413 的既有精确映射。"""
    client, tokens = api_client
    assert client.get("/docs/nope", headers=_hdr(tokens, "reader")
                      ).get_json()["code"] == "E4041"
    client.application.config["MAX_CONTENT_LENGTH"] = 10
    r = client.post("/jobs", data="x" * 64, content_type="application/json",
                    headers=_hdr(tokens, "editor"))
    assert r.status_code == 413 and r.get_json()["code"] == "E4131"


# --- R17-API-4：未归类异常当日志写完即落盘 ----------------------------------
def test_unhandled_error_line_is_flushed_immediately(api_client, tmp_path,
                                                     monkeypatch):
    client, tokens = api_client
    monkeypatch.setattr(auth, "check",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    log = tmp_path / "work" / "logs" / "app.jsonl"
    before = log.stat().st_size if log.exists() else 0
    r = client.get("/docs", headers=_hdr(tokens, "reader"))
    assert r.status_code == 500 and r.get_json()["code"] == "E5000"
    # 关键：不追发第二个请求，E_UNHANDLED 必须已经在文件里（原实现要等下次 flush）
    assert log.stat().st_size > before, "500 日志未即时落盘"
    lines = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert any(l.get("code") == "E_UNHANDLED" for l in lines), lines


# --- R17-API-5/6：分页信封与 GET /docs --------------------------------------
def test_jobs_list_exposes_total_and_has_more(api_client, saved_doc):
    client, tokens, _ = saved_doc
    r = client.get("/jobs?limit=0", headers=_hdr(tokens, "reader")).get_json()
    assert {"tasks", "counts", "total", "has_more"} <= set(r), sorted(r)
    assert r["tasks"] == [] and r["total"] == 1 and r["has_more"] is False, r
    assert sum(r["counts"].values()) == r["total"], "counts 与 total 同口径"
    # 再造一行在途作业：截断必须可见（原实现只回 tasks，GUI 把截断当全集显示）
    store = client.application.config["doc2md"]["store"]
    Scheduler(store).submit("doc-extra", "key-extra", source_path="/tmp/extra.pdf")
    trunc = client.get("/jobs?limit=1", headers=_hdr(tokens, "reader")).get_json()
    assert trunc["total"] == 2 and trunc["has_more"] is True, trunc
    assert len(trunc["tasks"]) == 1
    full = client.get("/jobs", headers=_hdr(tokens, "reader")).get_json()
    assert full["total"] == 2 and full["has_more"] is False
    queued = client.get("/jobs?status=queued", headers=_hdr(tokens, "reader")).get_json()
    assert queued["total"] == queued["counts"]["queued"] == 1, queued
    done = client.get("/jobs?status=done", headers=_hdr(tokens, "reader")).get_json()
    assert done["total"] == 1 and done["has_more"] is False, done


def test_jobs_window_pages_with_offset(api_client):
    """R17-API-8：`/jobs` 有 `has_more` 却没有游标 = 队列第 51 条永远看不到。"""
    client, tokens = api_client
    store = client.application.config["doc2md"]["store"]
    sched = Scheduler(store)
    for i in range(5):
        sched.submit(f"doc-{i}", f"key-{i}")
    h = _hdr(tokens, "reader")
    pages = [client.get(f"/jobs?limit=2&offset={o}", headers=h).get_json() for o in (0, 2, 4)]
    ids = [t["job_id"] for p in pages for t in p["tasks"]]
    assert len(ids) == 5 and len(set(ids)) == 5, ids        # 同刻入队也不重不漏
    assert [p["offset"] for p in pages] == [0, 2, 4], pages
    assert [p["limit"] for p in pages] == [2, 2, 2], pages
    assert [p["has_more"] for p in pages] == [True, True, False], pages
    for qs in ("offset=-1", "offset=abc", "limit=abc", "offset=99999999999999999999999"):
        r = client.get(f"/jobs?{qs}", headers=h)
        assert r.status_code == 400, (qs, r.status_code)
        assert r.get_json()["code"] == "E4001", (qs, r.get_json())
    assert client.get("/jobs?limit=0", headers=h).get_json()["has_more"] is False


def test_queue_panel_shows_window_truncation():
    """GUI 不得把「最新 50 条」显示成全部（与 R17-API-5 同一契约的客户端一侧）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "doc2md" / "static" / "editor.html") \
        .read_text(encoding="utf-8")
    assert "QSTATE.truncNoted" in src, "截断提示缺失或未去重"
    assert "j.has_more" in src and "j.total" in src, "loadQueue 未读窗口字段"


def test_docs_route_exposes_list_docs_envelope(api_client, saved_doc):
    client, tokens, doc_id = saved_doc
    r = client.get("/docs", headers=_hdr(tokens, "reader"))
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    body = r.get_json()
    assert {"docs", "total", "offset", "limit", "has_more"} <= set(body), sorted(body)
    assert body["total"] == 1
    row = next(d for d in body["docs"] if d["doc_id"] == doc_id)
    assert row["blocks"] > 0 and row["status"], row       # 每行带 blocks 计数
    assert client.get("/docs?limit=0", headers=_hdr(tokens, "reader")
                      ).get_json()["docs"] == [], "limit=0 取空集（D9 口径）"
    assert client.get("/docs?limit=0", headers=_hdr(tokens, "reader")
                      ).get_json()["has_more"] is False, "D9：空前缀没有下一页"
    assert client.get("/docs?status=nope", headers=_hdr(tokens, "reader")
                      ).get_json()["total"] == 0


def test_audit_envelope_has_more(api_client, saved_doc):
    client, tokens, doc_id = saved_doc
    blocks = client.get(f"/docs/{doc_id}?limit=1",
                        headers=_hdr(tokens, "reader")).get_json()["blocks"]
    assert blocks, "小样本至少一块正文"
    bid = blocks[0]["id"]
    rev = blocks[0].get("revision", 0)
    for i in range(2):
        r = client.patch(f"/docs/{doc_id}/blocks/{bid}",
                         json={"text": f"改一下 {i}", "base_revision": rev},
                         headers=_hdr(tokens, "editor"))
        assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
        rev = r.get_json()["revision"]
    j = client.get(f"/docs/{doc_id}/audit?limit=1",
                   headers=_hdr(tokens, "editor")).get_json()
    assert j["total"] == 2 and len(j["audit"]) == 1, j
    assert j["has_more"] is True
    assert client.get(f"/docs/{doc_id}/audit?limit=0",
                      headers=_hdr(tokens, "editor")).get_json()["has_more"] is False
    full = client.get(f"/docs/{doc_id}/audit",
                      headers=_hdr(tokens, "editor")).get_json()
    assert full["has_more"] is False and len(full["audit"]) == 2, full
    # 排序口径不动：GUI 历史面板依赖 rowid 升序（最旧在前）
    assert [a["after"] for a in full["audit"]] == ["改一下 0", "改一下 1"], full
    # D9 口径必须四个列表端点同一真源：删除留痕也不得缺 has_more
    dele = client.get("/audit/deletions", headers=_hdr(tokens, "admin")).get_json()
    assert {"total", "offset", "limit", "has_more"} <= set(dele), dele
    assert dele["has_more"] is False, dele
    assert client.get("/audit/deletions?limit=0",
                      headers=_hdr(tokens, "admin")).get_json()["has_more"] is False


# --- R17-API-7：检索翻页（offset）+ limit=0 归入 D9 口径 ---------------------
def _seed_hits(store, n: int = 25, doc_id: str = "hm"):
    from doc2md.storage.repo import save_ir
    ir = IR(doc_id=doc_id, source_key="k-" + doc_id, source_name="d.pdf",
            format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"哨兵{i} 例外情况 正文", page=1)
                 for i in range(n)]
    save_ir(store, ir)


def test_search_offset_pages_without_overlap_or_gaps(store):
    """`has_more=True` 原先是死胡同：明说还有更多，却没有参数能取回来。"""
    from doc2md.storage.repo import search
    _seed_hits(store)
    pages = [search(store, "例外情况", limit=10, offset=o) for o in (0, 10, 20)]
    ids = [h["block_id"] for p in pages for h in p["hits"]]
    assert len(ids) == 25 and len(set(ids)) == 25, ids      # 不重不漏
    assert [p["has_more"] for p in pages] == [True, True, False], pages
    assert [p["offset"] for p in pages] == [0, 10, 20], pages
    assert [p["limit"] for p in pages] == [10, 10, 10], pages


def test_search_limit_zero_follows_d9_window_caliber(store):
    """D9：`limit=0` 是「取空集」，不得因探测行给出 has_more=True。"""
    from doc2md.storage.repo import search
    _seed_hits(store)
    for kwargs in ({}, {"full": True}):
        r = search(store, "例外情况", 0, **kwargs)
        assert r["hits"] == [] and r["total"] == 0, (kwargs, r)
        assert r["has_more"] is False and r["offset"] == 0 and r["limit"] == 0, (kwargs, r)


def test_search_full_mode_pages_stably(store):
    from doc2md.storage.repo import search
    _seed_hits(store)
    pages = [search(store, "例外情况", limit=10, full=True, offset=o) for o in (0, 10, 20)]
    ids = [h["block_id"] for p in pages for h in p["hits"]]
    assert len(ids) == 25 and len(set(ids)) == 25, ids
    assert all(p["mode"] == "full" for p in pages), pages


def test_search_api_window_contract(api_client):
    """HTTP 面与其余列表端点同一口径：非数字 / 负值 → 400，limit=0 → 空 + False。"""
    client, tokens = api_client
    store = client.application.config["doc2md"]["store"]
    _seed_hits(store)
    for qs in ("offset=-1", "offset=abc", "limit=-1", "offset=99999999999999999999999"):
        r = client.get(f"/search?q=例外情况&{qs}", headers=_hdr(tokens))
        assert r.status_code == 400, (qs, r.status_code, r.get_data(as_text=True)[:160])
        assert r.get_json()["code"] == "E4001", (qs, r.get_json())
    r = client.get("/search?q=例外情况&limit=0", headers=_hdr(tokens)).get_json()
    assert r["hits"] == [] and r["has_more"] is False and r["limit"] == 0, r
    p0 = client.get("/search?q=例外情况&limit=10", headers=_hdr(tokens)).get_json()
    p1 = client.get("/search?q=例外情况&limit=10&offset=10", headers=_hdr(tokens)).get_json()
    assert p0["offset"] == 0 and p1["offset"] == 10, (p0, p1)
    assert ({h["block_id"] for h in p0["hits"]} & {h["block_id"] for h in p1["hits"]}) == set()


def test_search_gui_exposes_the_next_page():
    """GUI 不得继续把截断只当一句提示——服务端已给游标，页面要能用它取下一页。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "doc2md" / "static" / "editor.html") \
        .read_text(encoding="utf-8")
    assert "id=\"goMore\"" in src, "缺少「更多命中」入口"
    assert 'doSearch("more")' in src, "翻页入口未接回 doSearch"
    assert "&offset=" in src, "检索请求未带 offset 游标"
    assert "const SRCH =" in src, "翻页游标状态未声明"


# --- R17-SVC-1：事件日志的保留键不得被 payload 覆盖 -------------------------
def test_emit_reserved_keys_win_over_payload(tmp_path):
    from doc2md.ops.logging import RotatingJsonlWriter
    store = Store(tmp_path / "state.sqlite")
    w = RotatingJsonlWriter(tmp_path / "logs", stem="app")
    try:
        # api.py 拒绝路径的真实形态：payload 自带业务错误码
        emit(store, "job_rejected", "d1", "j1",
             {"code": "E4091", "reason": "active_job_exists"}, writer=w)
        w.flush()
        line = json.loads((tmp_path / "logs" / "app.jsonl")
                          .read_text(encoding="utf-8").splitlines()[-1])
        assert line["code"] == "job_rejected", line       # 事件名不被业务码顶掉
        assert line["biz_code"] == "E4091", line          # 业务码换名保留，两侧 grep 都成立
        assert line["reason"] == "active_job_exists"
        row = store.q1("SELECT code, payload_json FROM events WHERE job_id='j1'")
        assert row["code"] == "job_rejected"
        assert json.loads(row["payload_json"])["code"] == "E4091", "入库面保留业务码"
    finally:
        w.close()
        store.close()


# --- R17-SVC-2：worker 启动段失败也要收尾 ----------------------------------
def test_run_work_closes_store_when_startup_fails(tmp_path, cfg, monkeypatch):
    from doc2md import worker

    closed = []
    real_store = worker.Store

    class SpyStore(real_store):
        def close(self):                      # noqa: D102 - 只加记录
            closed.append(True)
            return super().close()

    monkeypatch.setattr(worker, "Store", SpyStore)
    monkeypatch.setattr(worker, "reclaim_stale",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lock")))
    with pytest.raises(RuntimeError):
        worker.run_work(cfg, tmp_path, once=True)
    assert closed, "启动段异常必须仍走 finally 关连接（原实现在 try 之外）"
    assert not (tmp_path / "logs" / "work.jsonl").exists()


def test_run_work_writes_start_and_stop_on_normal_exit(tmp_path, cfg):
    from doc2md import worker
    rep = worker.run_work(cfg, tmp_path, once=True)       # 空队列：排空即退
    assert rep.drained == 0
    lines = [json.loads(ln) for ln in (tmp_path / "logs" / "work.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    codes = [l["code"] for l in lines]
    assert codes == ["WORK_START", "WORK_STOP"], codes
    assert lines[-1]["done"] == rep.done


# --- R17-SQL-1：活动状态谓词参数化 -----------------------------------------
def test_active_sql_predicate_is_placeholder_list():
    assert ACTIVE_SQL == "(" + ", ".join("?" for _ in ACTIVE) + ")"
    assert "'" not in ACTIVE_SQL and "queued" not in ACTIVE_SQL


def test_submit_still_detects_active_job_via_bound_params(tmp_path):
    """`IN (?, ?)` 改写后，在途附着口径不得变化（不重复建作业行）。"""
    store = Store(tmp_path / "state.sqlite")
    try:
        sched = Scheduler(store)
        first = sched.submit("doc-a", "key-a", source_path="/tmp/a.pdf")
        again = sched.submit("doc-a", "key-a2", source_path="/tmp/a2.pdf")
        assert again.attached and again.job_id == first.job_id, again
        other = sched.submit("doc-b", "key-b", source_path="/tmp/b.pdf")
        assert not other.attached and other.job_id != first.job_id
        assert store.q1("SELECT COUNT(*) c FROM tasks")["c"] == 2, "附着不建行"
        idem = sched.submit("doc-a", "key-a", idem_key="k1")
        assert idem.idem_hit or idem.attached, idem
    finally:
        store.close()


# --- R17-PIPE-1：外链链接文字必须转义 --------------------------------------
def test_external_link_label_is_escaped():
    ir = IR(doc_id="p1", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="x",
                          links=[{"url": "https://e.com/a",
                                  "text": "<script>alert(1)</script> **粗** ~~删~~"}])])
    md = render(ir)
    # 转义后仍是 `<script>` 五个字符、只是前面带反斜杠 → 判定口径与红线一致：无裸 HTML
    assert _contract_violation(md) == "", md
    assert md.startswith("[\\<script>"), md
    assert "\\*\\*粗\\*\\*" in md and "\\~\\~删\\~\\~" in md, md
    assert_render_contract(md)


def test_external_link_label_newline_flattened():
    """链接文字里的换行会切断段落结构（原实现原样透传）。"""
    ir = IR(doc_id="p1", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="x",
                          links=[{"url": "https://e.com/a", "text": "第一行\n第二行"}])])
    md = render(ir)
    # B-53 同主语正断言（CH-512）：同一计数判据必须**能**看见换行——正文段落里的
    # 软换行是合法输出，它若也计 0，说明 `md` 是被掏空的字符串而不是渲染结果。
    probe = render(IR(doc_id="p1", source_key="k", source_name="a.pdf", format="pdf",
                      pages=1, blocks=[Block(id="b1", type="paragraph",
                                             text="正文一行\n正文二行")]))
    flat_hits = md.strip().count("\n")
    raw_hits = probe.strip().count("\n")
    assert (flat_hits, raw_hits) == (0, 1), (
        f"换行压平判据无感：链接文字命中 {flat_hits} 处、正文软换行命中 {raw_hits} 处（应为 0 / 1）")


# --- R17-PIPE-2：渲染红线接进生产路径 --------------------------------------
def test_render_guard_fires_when_a_block_violates_contract(monkeypatch):
    """守卫必须真的有牙齿：注入一处违规 → 生产 render() 抛错而不是静默出门。"""
    import doc2md.pipeline.s4_render as s4

    orig = s4._contract_violation

    def seeded(md, *, allow_strike=False):
        found = orig(md, allow_strike=allow_strike)
        return found or ("<script" if "危险" in md else "")

    monkeypatch.setattr(s4, "_contract_violation", seeded)
    ir = IR(doc_id="p2", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="危险"),
                    Block(id="b2", type="code", text="危险")])
    with pytest.raises(E5031_StorageError) as exc:
        render(ir)
    assert exc.value.detail["reason"] == "render_contract"
    assert exc.value.detail["block_id"] == "b1", "定位到违规块"
    assert render(ir, contract=False)                      # 显式放行口径仍在


def test_render_guard_exempts_code_block_literals():
    """代码块正文是源字面量：`~~`、`<script>`、`](#…` 合法，不得误杀整篇转换。"""
    src = "~~not-strike~~\n<script>x</script>\n[t](#anchor)"
    ir = IR(doc_id="p3", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="code", text=src,
                          flags={"language": "text"}),
                    Block(id="b2", type="paragraph", text="正常正文")])
    md = render(ir)                       # 不抛
    assert src in md, md
    assert _contract_violation(md), "整篇口径必命中代码字面量——故生产用逐块口径"


def test_render_strike_optin_emits_tilde_without_error():
    """`preserve_strike=True` 时 `~~` 是预期语法，不是漏网裸标记。"""
    ir = IR(doc_id="p4", source_key="k", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="作废",
                          marks={"strikethrough": True})])
    # B-53 同主语正断言（CH-512）：同一份 IR、同一个 `~~` 计数判据，opt-in 档必须
    # 恰好命中 2 处（成对删除线）。计数为 0 就说明「默认档没有 ~~」是恒真的空判据。
    on = render(ir, preserve_strike=True)
    off = render(ir)                                       # 默认档仍降级为纯文本
    assert "~~作废~~" in on
    on_hits = on.count("~~")
    off_hits = off.count("~~")
    assert (off_hits, on_hits) == (0, 2), (
        f"删除线判据无感：默认档 ~~ 命中 {off_hits} 处、opt-in 档命中 {on_hits} 处（应为 0 / 2）")


# --- R17-PIPE-3：ZIP 容器三种结局分别可判 ----------------------------------
def test_sniff_damaged_zip_reports_corrupt_zip(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 200)          # PK 头 + 截断
    with pytest.raises(E4151_UnsupportedType) as exc:
        sniff(p)
    assert exc.value.detail["reason"] == "corrupt_zip", exc.value.detail


def test_sniff_zip_unreadable_branch_is_pinned_white_box(monkeypatch, tmp_path):
    """`reason="zip_unreadable"` 在本机无法黑盒复现：chmod 000 先被头部读取分支
    （`cannot read …`）吃掉，走不到容器层。按白盒钉住第三结局——头 8 字节可读、
    打开容器时 OS 级失败（故障盘 / NFS ESTALE 的真实形态）。"""
    import zipfile

    p = tmp_path / "x.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    def boom(*a, **k):
        raise OSError("remote I/O failed")

    monkeypatch.setattr(zipfile, "ZipFile", boom)
    with pytest.raises(E4151_UnsupportedType) as exc:
        sniff(p)
    assert exc.value.detail["reason"] == "zip_unreadable", exc.value.detail
    assert "magic" in exc.value.detail


def test_sniff_other_zip_reports_not_a_docx(tmp_path):
    p = tmp_path / "sheet.docx"
    import zipfile
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", "<x/>")
    with pytest.raises(E4151_UnsupportedType) as exc:
        sniff(p)
    assert exc.value.detail["reason"] == "not_a_docx", exc.value.detail
    assert "word/document.xml" in str(exc.value)


def test_sniff_real_docx_still_returns_docx(tmp_path):
    docx = pytest.importorskip("docx").Document()
    docx.add_paragraph("正文")
    p = tmp_path / "ok.docx"
    docx.save(p)
    assert sniff(p) == "docx"


# --- R17-PIPE-4：片级内部锚不得进交付面 ------------------------------------
def test_shard_internal_anchor_absent_from_delivered_meta(tmp_path, small_pdf):
    from doc2md.pipeline.convert import convert
    res = convert(small_pdf, out_dir=tmp_path / "out")
    # B-53 同主语正断言（CH-512）：`shard` 是 convert 逐片写入的内部锚
    # （`convert.py` 的 `ir.extra_meta["shard"]`），单片样本永远不触发它，所以三条
    # 否定断言在干净样本上无法自证有牙齿。这里把缺陷形状注回**真实对象**：
    # extra_meta 加一个 `shard` 后，`meta()` 的平铺必须把它带到交付面——两个计数
    # 各 1，证明「平铺后没有」不是因为 `meta()` 压根不看 extra_meta。
    clean_extra = res.ir.extra_meta
    leak_extra = list(clean_extra).count("shard")
    leak_flat = list(res.ir.meta()).count("shard")
    res.ir.extra_meta = {**clean_extra, "shard": {"index": 0, "start": 0, "end": 0}}
    inject_extra = list(res.ir.extra_meta).count("shard")
    inject_flat = list(res.ir.meta()).count("shard")
    assert (leak_extra, leak_flat) == (0, 0), (
        f"内部锚判据无感：单片样本 extra_meta/meta() 命中 {leak_extra}/{leak_flat} 处（应为 0 / 0）")
    assert (inject_extra, inject_flat) == (1, 1), (
        f"`meta()` 平铺未生效：注入 extra_meta 后命中 {inject_extra}/{inject_flat} 处（应为 1 / 1）")
    res.ir.extra_meta = clean_extra
    with zipfile.ZipFile(res.zip_path) as z:
        meta = json.loads(z.read(f"{res.doc_id}/ir.json").decode("utf-8"))["meta"]
    kept_hits = list(meta).count("shard_count")
    leak_hits = list(meta).count("shard")
    assert (leak_hits, kept_hits) == (0, 1), (
        f"交付 meta 判据无感：shard 命中 {leak_hits} 处、文档级口径 shard_count 命中 "
        f"{kept_hits} 处（应为 0 / 1；shard_count 为 0 说明读到的是空 dict，前一条否定恒真）")


# --- R17-PIPE-5：边距剔除后仍带版式探测字段 --------------------------------
def test_strip_furniture_keeps_layout_probe_fields():
    pages = [RawPage(index=0, height=800, rotation=90, rtl_ratio=0.7,
                     size_hist={12.0: 10},
                     lines=[Line(text="正文一行", size=12.0, x0=60, top=400, bottom=412),
                            Line(text="第 1 页", size=9.0, x0=360, top=760, bottom=770)])]
    out = _strip_page_furniture(pages, 12.0)
    assert [ln.text for ln in out[0].lines] == ["正文一行"], out[0].lines
    assert out[0].rotation == 90 and out[0].rtl_ratio == 0.7
    assert out[0].size_hist, "CH-493（P-2）的直方图口径不得回退"


# --- R17-PIPE-6：DOCX 行字号与 run 顺序无关 --------------------------------
def test_docx_line_size_is_order_independent(tmp_path):
    docx = pytest.importorskip("docx")
    from doc2md.pipeline.s1_parse import _docx_para_line

    def _para(big_first: bool):
        d = docx.Document()
        p = d.add_paragraph()
        for text, pt in ((("标题很长的一段正文", 22.0), ("尾缀", 10.0))
                         if big_first else
                         (("尾缀", 10.0), ("标题很长的一段正文", 22.0))):
            r = p.add_run(text)
            r.font.size = docx.shared.Pt(pt)
        return d, p

    a = _docx_para_line(_para(True)[1], _para(True)[0])
    b = _docx_para_line(_para(False)[1], _para(False)[0])
    assert a.size == b.size == 22.0, (a.size, b.size)     # 可见字符加权众数


def test_docx_line_ignores_empty_run_size(tmp_path):
    docx = pytest.importorskip("docx")
    from doc2md.pipeline.s1_parse import _docx_para_line
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("正文正文").font.size = docx.shared.Pt(10.5)
    p.add_run("").font.size = docx.shared.Pt(22.0)        # 零可见字符 → 权重 1
    assert _docx_para_line(p, d).size == 10.5


# --- R17-PKG-1：包入口可执行 ------------------------------------------------
def test_python_m_doc2md_runs():
    from doc2md import PIPELINE_VERSION
    out = subprocess.run([sys.executable, "-m", "doc2md", "--version"],
                         capture_output=True, text=True, cwd=".")
    assert out.returncode == 0, out.stderr[:300]
    assert PIPELINE_VERSION in out.stdout, out.stdout
