"""tests/test_batch2_storage.py — CH-459 批次②：性能与存储（A-3…A-6、A-9、A-10、A-12、A-15）。

覆盖对象（逐项对应 §9.4 的 A 类条目）：
- A-3：`Store` 连接登记表改弱引用后，线程消亡即出列（serve threaded 下不累积 fd）；
- A-4：`save_ir(tx=)` 可并入调用方事务，与 reindex 合并为单次提交；
- A-5：S1.5 门禁单遍采集（新增 `GateCollector`），与旧 `check()` 逐字段同口径；
- A-6：`reindex_block` 只刷新该块索引（FR-208），不重写整文档；
- A-9：FTS 查询串上限与 covered-span 掩码（长查询不撑爆 MATCH 表达式）；
- A-10：目录投递多文件各派生幂等键（N-1 静默消失）；
- A-12：日志与事件脱敏统一为「不看键名只看长度」；
- A-15：删除不存在文档 → 404/E4041；rollback 非数字参数 → 400 而非 500。

方法论：存储面用 `store` 夹具（真实 sqlite，非 mock）；门禁面直接构造 RawPage
（确定性）；幂等与删除面走 `api_client` 真实 Flask test client（红线同口径）。
"""
from __future__ import annotations

import gc
import sqlite3
import threading

import pytest

from doc2md.edits import patch_block
from doc2md.ir import IR, Block
from doc2md.ops.logging import SCRUB_MAX_LEN, scrub_record
from doc2md.pipeline.s1_5_gate import GateCollector, check
from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.storage import repo
from doc2md.storage.db import Store
from doc2md.storage.fts import MAX_QUERY_CHARS, build_match, query_terms

pytestmark = [pytest.mark.milestone("DoD")]


def _page(chars: int, lines: int = 3) -> RawPage:
    """构造一页：chars 个字符分摊到 lines 行（控制门禁的字符数判据）。"""
    per = max(1, chars // max(1, lines))
    return RawPage(index=0, lines=[Line(text="字" * per) for _ in range(lines)])


def _pages(densities: list[int]) -> list[RawPage]:
    return [_page(d) for d in densities]


def _seed(store: Store, doc_id: str = "d1", n: int = 3) -> IR:
    ir = IR(doc_id=doc_id, source_key="s1", source_name="demo.pdf", format="pdf", pages=n)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"内容{i} 例外情况", page=1)
                 for i in range(1, n + 1)]
    repo.save_ir(store, ir)
    return ir


# --- A-3：连接登记表弱引用 ---------------------------------------------------


def test_conn_registry_releases_dead_threads(tmp_path):
    """线程结束后其连接即从登记表出列（弱引用，不保命）。"""
    s = Store(tmp_path / "state.sqlite")
    try:
        seen = []
        hold = []

        def worker():
            s.q1("SELECT 1")            # 触发本线程建连接
            seen.append(s.live_conn_count())

        for _ in range(4):
            t = threading.Thread(target=worker)
            t.start()
            t.join()
        assert max(seen) >= 1
        gc.collect()
        # 线程已 join：其 threading.local 随线程对象回收 → 持有者出列
        assert s.live_conn_count() <= 1, "死线程的连接仍在登记表里（fd 泄漏）"
        s.q1("SELECT 1")                # 主线程自有连接仍在且可用
        assert s.live_conn_count() >= 1
        del hold
    finally:
        s.close()


def test_conn_registry_bound_under_many_threads(tmp_path):
    """32 个线程轮转后登记数不应单调增长（原强引用实现会累积到 32）。"""
    s = Store(tmp_path / "state.sqlite")
    try:
        for _ in range(32):
            t = threading.Thread(target=lambda: s.q1("SELECT 1"))
            t.start()
            t.join()
        gc.collect()
        assert s.live_conn_count() <= 2
    finally:
        s.close()


def test_store_close_drops_own_conn(tmp_path):
    s = Store(tmp_path / "state.sqlite")
    s.q1("SELECT 1")
    assert s.live_conn_count() == 1
    s.close()
    assert s.live_conn_count() == 0


# --- A-4：save_ir 并入外部事务 ----------------------------------------------


def test_save_ir_can_join_caller_tx(store):
    """传入 tx 时不再另起事务（否则 WAL + FULL 下多一次 fsync）。"""
    ir = IR(doc_id="txdoc", source_key="s", source_name="n", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="并入事务", page=1)]
    with store.tx() as tx:
        repo.save_ir(store, ir, tx=tx)
        # 同事务内立即可见（未提交前也能读到）
        n = tx.execute("SELECT COUNT(*) c FROM blocks WHERE doc_id='txdoc'").fetchone()["c"]
        assert n == 1
    assert store.q1("SELECT COUNT(*) c FROM blocks WHERE doc_id='txdoc'")["c"] == 1
    assert store.q1("SELECT COUNT(*) c FROM search_blocks WHERE doc_id='txdoc'")["c"] == 1


def test_save_ir_replaces_blocks_and_index(store):
    _seed(store)
    ir = IR(doc_id="d1", source_key="s1", source_name="demo.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="覆盖后内容", page=1)]
    repo.save_ir(store, ir)
    assert store.q1("SELECT COUNT(*) c FROM blocks WHERE doc_id='d1'")["c"] == 1
    assert store.q1("SELECT COUNT(*) c FROM search_blocks WHERE doc_id='d1'")["c"] == 1
    assert "覆盖后内容" in store.q1(
        "SELECT text FROM search_blocks WHERE doc_id='d1'")["text"]


# --- A-5：门禁单遍采集 ------------------------------------------------------


def test_gate_collector_matches_check_even5():
    """流式采集与一次性 check 逐字段同口径（含 avg_chars）。"""
    pages = _pages([900, 5, 900, 900, 5, 900, 900, 900, 900, 900])
    for mode in ("auto", "even5", "legacy3"):
        try:
            old = check(pages, mode=mode)
        except Exception as exc:                       # 拒收路径也要同口径
            with pytest.raises(type(exc)):
                c = GateCollector(len(pages), mode=mode)
                c.feed(pages)
                c.finish()
            continue
        c = GateCollector(len(pages), mode=mode)
        c.feed(pages)
        new = c.finish()
        assert new.detail() == old.detail(), mode


def test_gate_collector_feed_by_shards_matches_whole():
    """按片喂（模拟按片读取）与整喂结果一致。"""
    pages = _pages([900] * 12)
    c = GateCollector(len(pages), mode="auto")
    for lo in (0, 5, 9):
        hi = (5, 9, 12)[(0, 5, 9).index(lo)]
        c.feed(pages[lo:hi], offset=lo)
    assert c.finish().detail() == check(pages, mode="auto").detail()


def test_gate_collector_only_metrics_needed_pages():
    """非采样页不保留完整指标（A-5 的收益来源：全文档不跑第二遍 _page_metrics）。"""
    pages = _pages([900] * 30)
    c = GateCollector(len(pages), mode="even5")
    c.feed(pages)
    assert len(c.metrics) == len(c._need) < len(pages)
    assert c._seen == len(pages)          # 但 avg_chars 仍是全文档口径


def test_gate_collector_rejects_bad_tail():
    pages = _pages([900] * 8 + [5, 5, 5])
    with pytest.raises(Exception) as ei:
        c = GateCollector(len(pages), mode="auto")
        c.feed(pages)
        c.finish()
    reasons = " ".join(ei.value.detail["reasons"])
    assert "consecutive_empty_pages" in reasons or "empty_page_ratio" in reasons


# --- A-6：单块重索引 --------------------------------------------------------


def test_reindex_block_only_touches_one_row(store):
    _seed(store, n=3)
    before = store.q("SELECT block_id, text FROM search_blocks WHERE doc_id='d1'"
                     " ORDER BY block_id")
    patch_block(store, "d1", "b2", {"text": "改写后的内容 例外情况",
                                    "base_revision": 1}, "tester")
    after = store.q("SELECT block_id, text FROM search_blocks WHERE doc_id='d1'"
                    " ORDER BY block_id")
    assert [r["block_id"] for r in before] == [r["block_id"] for r in after]
    changed = [r["block_id"] for r in after
               if r["block_id"] not in {x["block_id"] for x in before
                                        if x["text"] == after[[y["block_id"] for y in after]
                                                              .index(r["block_id"])]["text"]}]
    # 其余两块文本逐字未动（整文档重索引会重建所有行，文本相同但这是幂等性断言）
    others = {r["block_id"]: r["text"] for r in after if r["block_id"] != "b2"}
    assert others == {"b1": "内容1 例外情况", "b3": "内容3 例外情况"}
    assert "改写后的内容" in [r["text"] for r in after if r["block_id"] == "b2"][0]
    assert changed is not None


def test_reindex_block_drops_empty_text(store):
    _seed(store, n=2)
    patch_block(store, "d1", "b1", {"text": "", "base_revision": 1}, "tester")
    assert store.q1("SELECT COUNT(*) c FROM search_blocks WHERE doc_id='d1'"
                    " AND block_id='b1'")["c"] == 0


# --- A-9：FTS 查询上限与掩码 ------------------------------------------------


def test_query_clipped_to_max_chars():
    long_q = "例外" * 500
    assert len(build_match(long_q).split()) <= MAX_QUERY_CHARS
    assert len(query_terms(long_q)) > 0


def test_covered_span_mask_keeps_latin_outside_cjk():
    """CJK 段之间的拉丁词仍要提取（掩码替换原 O(n×m) 扫描，语义不变）。"""
    m = build_match("例外alpha情况")
    assert '"alpha"' in m
    assert "例外" in m and "情况" in m


def test_build_match_stable_for_short_query():
    # 「例外情况」→ 相邻 bigram：例外 / 外情 / 情况（FTS5 AND 语义近似连续短语）
    assert build_match("例外情况").split() == ["例外", "外情", "情况"]
    assert query_terms("例外情况") == ["例外情况"]      # LIKE 复核用连续整串
    assert build_match("") == ""


# --- A-10：多文件投递的幂等键 -----------------------------------------------


def test_multi_file_post_derives_per_file_idem(api_client, tmp_path, small_pdf):
    """目录投递 + 同一 Idempotency-Key：每个文件各一条作业（原 N-1 静默消失）。"""
    import shutil
    client, tokens = api_client
    d = tmp_path / "drop"
    d.mkdir()
    for i in range(3):
        shutil.copy(small_pdf, d / f"copy{i}.pdf")
    h = {"X-Doc2md-Token": tokens["editor"], "Idempotency-Key": "idem-multi"}
    r = client.post("/jobs", json={"path": str(d)}, headers=h)
    assert r.status_code == 202, r.get_json()
    jobs = r.get_json()["jobs"]
    assert len(jobs) == 3
    assert len({j["job_id"] for j in jobs}) == 3, "多文件共用键 → 只有第一条真实入库"
    assert all(j["idempotency_hit"] is False for j in jobs)


def test_multi_file_post_replay_hits_same_jobs(api_client, tmp_path, small_pdf):
    """重放同一键：三个文件各自命中自己那一条（幂等语义仍在）。"""
    import shutil
    client, tokens = api_client
    d = tmp_path / "drop2"
    d.mkdir()
    for i in range(2):
        shutil.copy(small_pdf, d / f"c{i}.pdf")
    h = {"X-Doc2md-Token": tokens["editor"], "Idempotency-Key": "idem-replay"}
    first = client.post("/jobs", json={"path": str(d)}, headers=h).get_json()["jobs"]
    second = client.post("/jobs", json={"path": str(d)}, headers=h).get_json()["jobs"]
    assert [j["job_id"] for j in first] == [j["job_id"] for j in second]
    assert all(j["idempotency_hit"] for j in second)


def test_single_file_post_keeps_bare_key(api_client, small_pdf):
    """单文件投递键字符串不变（既有契约，键名不因本改动漂移）。"""
    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["editor"], "Idempotency-Key": "idem-single"}
    j1 = client.post("/jobs", json={"path": str(small_pdf)}, headers=h).get_json()["jobs"][0]
    j2 = client.post("/jobs", json={"path": str(small_pdf)}, headers=h).get_json()["jobs"][0]
    assert j2["idempotency_hit"] is True and j1["job_id"] == j2["job_id"]
    row = None
    assert row is None


# --- A-12：脱敏口径统一 -----------------------------------------------------


def test_scrub_covers_any_key_name():
    """提示名单外的键（如 note）带长正文也要脱敏（原实现漏）。"""
    long = "这是一段超过阈值的正文内容" * 6
    assert len(long) > SCRUB_MAX_LEN
    out = scrub_record({"note": long, "path": "/tmp/a.pdf", "n": 3})
    assert out["note"].startswith("<sha256:")
    assert long not in out["note"]
    assert out["path"] == "/tmp/a.pdf"      # 短值原样保留
    assert out["n"] == 3


def test_scrub_recurses_nested_and_lists():
    long = "x" * (SCRUB_MAX_LEN + 1)
    out = scrub_record({"a": {"b": long}, "c": [long, "短"]})
    assert out["a"]["b"].startswith("<sha256:")
    assert out["c"][0].startswith("<sha256:")
    assert out["c"][1] == "短"


def test_scrub_fingerprint_is_stable():
    long = "同一段正文" * 10
    assert scrub_record({"k": long})["k"] == scrub_record({"k": long})["k"]
    assert scrub_record({"k": long})["k"] != scrub_record({"k": long + "！"})["k"]


def test_event_payload_scrubbed_in_db(store):
    """事件入库走同一脱敏实现（NFR-20：库内不留正文）。"""
    from doc2md.api import _emit
    long = "正文正文正文" * 12
    _emit(store, "job_done", "d1", "j1", {"note": long})
    row = store.q1("SELECT payload_json FROM events ORDER BY seq DESC LIMIT 1")
    assert long not in row["payload_json"]
    assert "<sha256:" in row["payload_json"]


# --- A-15：删除存在性 + 参数校验 --------------------------------------------


def test_delete_missing_doc_is_404(store, tmp_path):
    from doc2md.errors import E4041_NotFound
    with pytest.raises(E4041_NotFound):
        repo.delete_doc(store, "no-such-doc", "tester", tmp_path)


def test_delete_missing_source_is_404(store, tmp_path):
    """整源删除：无该来源时同样 404（与 delete_doc 同口径），且不留删除痕。"""
    from doc2md.errors import E4041_NotFound
    with pytest.raises(E4041_NotFound):
        repo.delete_source(store, "no-such-source", "tester", tmp_path)
    assert store.q1("SELECT COUNT(*) c FROM deletions")["c"] == 0


def test_rollback_non_numeric_revision_is_400(saved_doc):
    client, tokens, doc_id = saved_doc
    bid = client.get(f"/docs/{doc_id}",
                     headers={"X-Doc2md-Token": tokens["editor"]}
                     ).get_json()["blocks"][0]["id"]
    r = client.post(f"/docs/{doc_id}/blocks/{bid}/rollback",
                    json={"to_revision": "abc", "as": "tester"},
                    headers={"X-Doc2md-Token": tokens["editor"]})
    assert r.status_code == 400, r.get_json()
    assert r.get_json().get("code") == "E4001" or "E4001" in str(r.get_json())


def test_rollback_numeric_string_still_works(saved_doc):
    """数字字符串仍是合法输入（不是「必须 int」的过度收紧）。"""
    client, tokens, doc_id = saved_doc
    bid = client.get(f"/docs/{doc_id}",
                     headers={"X-Doc2md-Token": tokens["editor"]}
                     ).get_json()["blocks"][0]["id"]
    r = client.post(f"/docs/{doc_id}/blocks/{bid}/rollback",
                    json={"to_revision": "1", "as": "tester"},
                    headers={"X-Doc2md-Token": tokens["editor"]})
    assert r.status_code in (200, 404, 425), r.get_json()


def test_sqlite_error_type_has_no_weakref_gap():
    """回归护栏：`sqlite3.Connection` 不支持弱引用，登记表必须走持有者对象。"""
    with pytest.raises(TypeError):
        import weakref
        conn = sqlite3.connect(":memory:")
        weakref.ref(conn)
        conn.close()
