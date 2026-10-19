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


def test_gate_collector_frozen_expectations():
    """CH-464：`check()` 内部已走 GateCollector（同一实现），「collector 对比
    check」是同义反复、回归保护为零——改为对冻结期望值断言（判定语义变更时
    必须显式改这里，而不是静默跟随实现）。"""
    pages = _pages([900, 5, 900, 900, 5, 900, 900, 900, 900, 900])
    # auto：3 个连续尾部稀疏页不触发（single sparse 不杀），平均字符数冻结
    r = check(pages, mode="auto")
    assert r.passed is True and r.stats["total_pages"] == 10
    assert r.stats["decision_pages"] == 10
    # even5：采样页含两个稀疏页（5 字符）→ 全部硬拒（任一不达标即拒）
    from doc2md.errors import E4221_GateReject
    with pytest.raises(E4221_GateReject):
        check(pages, mode="even5")
    c = GateCollector(len(pages), mode="even5")
    c.feed(pages)
    res = c.finish(reject_on_any=False)        # 采集面单独看：不拒收、只出判定
    # even5 采样点 {0,2,4,7,9} 恰含稀疏页 index 4 → 恰 1 页硬拒（冻结口径）
    assert [p.ok for p in res.pages].count(False) == 1
    # 分片喂入与整喂等价（真正的流式语义回归，见下一条测试）


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
    from doc2md.errors import E4221_GateReject
    with pytest.raises(E4221_GateReject) as ei:
        c = GateCollector(len(pages), mode="auto")
        c.feed(pages)
        c.finish()
    reasons = " ".join(ei.value.detail["reasons"])
    assert "consecutive_empty_pages" in reasons or "empty_page_ratio" in reasons


def test_gate_collector_reports_unfed_pages():
    """CH-469：声明 total 却少喂页 → 可判定的缺页错误，不是 `metrics[i]` 裸 KeyError。

    少喂是调用方契约违反（按片喂页时漏喂一段），原实现在判定面或报告面
    任一处 `self.metrics[i]` 直接 KeyError——栈里既没有页号也没有口径。
    """
    from doc2md.errors import Doc2mdError
    pages = _pages([900] * 8)
    c = GateCollector(8, mode="even5")
    c.feed(pages[:3])                       # 只喂前 3 页，采样点 4/6/8 从未到达
    with pytest.raises(Doc2mdError) as ei:
        c.finish()
    assert ei.value.detail["missing_pages"] == [5, 6, 8]
    assert "never fed" in ei.value.message

    c2 = GateCollector(5, mode="auto")      # auto 判定面 = 全页，缺页必逐项点名
    c2.feed(_pages([900] * 2))
    with pytest.raises(Doc2mdError) as ei2:
        c2.finish()
    assert ei2.value.detail["missing_pages"] == [3, 4, 5]


# --- A-6：单块重索引 --------------------------------------------------------


def test_reindex_block_only_touches_one_row(store):
    """FR-208「该块索引」：编辑 b2 只删插 b2 的 FTS 行，其余行 rowid 不变
    （rowid 变化 = 该行被整行重写，即整文档重索引的行为证据）。"""
    _seed(store, n=3)
    before = {r["block_id"]: r["rowid"] for r in store.q(
        "SELECT block_id, rowid FROM search_blocks WHERE doc_id='d1'")}
    patch_block(store, "d1", "b2", {"text": "改写后的内容 例外情况",
                                    "base_revision": 1}, "tester")
    after = {r["block_id"]: r["rowid"] for r in store.q(
        "SELECT block_id, rowid FROM search_blocks WHERE doc_id='d1'")}
    assert after["b1"] == before["b1"] and after["b3"] == before["b3"], \
        "未编辑块的 FTS 行被重写（整文档重索引行为）"
    assert after["b2"] != before["b2"]              # 被编辑块确实刷新了
    assert "改写后的内容" in store.q1(
        "SELECT text FROM search_blocks WHERE doc_id='d1' AND block_id='b2'")["text"]


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
    assert j1["doc_id"] == j2["doc_id"]             # 同键同文件 → 同一文档


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
    from doc2md.events import emit
    long = "正文正文正文" * 12
    emit(store, "job_done", "d1", "j1", {"note": long})
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
    """数字字符串是合法输入（不是「必须 int」的过度收紧）：
    先 PATCH 一次产生审计（rev1→2），回滚 "1" 是确定性 200 且正文复原；
    越界 revision "99" 是确定性 404（无该审计行）。"""
    client, tokens, doc_id = saved_doc
    h = {"X-Doc2md-Token": tokens["editor"]}
    b0 = client.get(f"/docs/{doc_id}", headers=h).get_json()["blocks"][0]
    bid, original = b0["id"], b0["text"]
    r = client.patch(f"/docs/{doc_id}/blocks/{bid}",
                     json={"text": "改写后的内容 例外情况", "base_revision": 1},
                     headers=h)
    assert r.status_code == 200, r.get_json()
    r1 = client.post(f"/docs/{doc_id}/blocks/{bid}/rollback",
                     json={"to_revision": "1", "as": "tester"}, headers=h)
    assert r1.status_code == 200, r1.get_json()
    assert client.get(f"/docs/{doc_id}", headers=h).get_json()["blocks"][0]["text"] == original
    r2 = client.post(f"/docs/{doc_id}/blocks/{bid}/rollback",
                     json={"to_revision": "99", "as": "tester"}, headers=h)
    assert r2.status_code == 404, r2.get_json()


def _sqlite3_weakref_support_is_absent() -> bool:
    """CH-464：原「回归护栏」断言的是 CPython stdlib 事实（sqlite3.Connection
    不支持弱引用），与仓库代码零耦合——Python 未来若给 sqlite3 加弱引用会误红。
    降级为文档化常量；真正的行为断言在 test_conn_registry_* 三条。"""
    import weakref
    conn = sqlite3.connect(":memory:")
    try:
        weakref.ref(conn)
        return False
    except TypeError:
        return True
    finally:
        conn.close()


def test_conn_registry_design_matches_stdlib_constraint():
    assert _sqlite3_weakref_support_is_absent(), \
        "sqlite3 已支持弱引用：_ConnRef 持有者壳可简化（登记 CH-464 的前提消失）"


# --- CH-464：第二轮修复的回归钉 ------------------------------------------------


def test_multi_file_post_same_basename_diff_dirs(api_client, tmp_path, small_pdf):
    """幂等键派生改相对投递根路径：不同子目录同名文件各自成 job（CH-464）。"""
    import shutil
    client, tokens = api_client
    d = tmp_path / "drop3"
    (d / "a").mkdir(parents=True)
    (d / "b").mkdir(parents=True)
    shutil.copy(small_pdf, d / "a" / "report.pdf")
    shutil.copy(small_pdf, d / "b" / "report.pdf")
    h = {"X-Doc2md-Token": tokens["editor"], "Idempotency-Key": "idem-collision"}
    r = client.post("/jobs", json={"path": str(d)}, headers=h)
    jobs = r.get_json()["jobs"]
    assert len({j["job_id"] for j in jobs}) == 2, "basename 派生 → 同名碰撞，第二个静默丢失"
    assert all(j["idempotency_hit"] is False for j in jobs)


def test_s3_quote_block_stays_in_source_order():
    """引用行后接正文行：quote 块必须在两段正文之间（CH-464 保序修复）。"""
    from doc2md.pipeline.s1_parse import Line, RawPage
    from doc2md.pipeline.s3_structure import analyze
    page = RawPage(index=0, lines=[
        Line(text="> 引用行内容"),
        Line(text="正文段落一。"),
        Line(text=""),
        Line(text="正文段落二。"),
    ])
    ir = analyze([page])
    types = [b.type for b in ir.blocks]
    assert types == ["quote", "paragraph", "paragraph"], types


def test_row_cells_sorts_by_x0_before_clustering():
    """内容流乱序（右列先画）的行也能正确聚类（CH-464 修复）。"""
    from doc2md.pipeline.s1_parse import _row_cells
    chars = [  # B 列字符先于 A 列
        {"text": "B", "x0": 200.0, "x1": 210.0},
        {"text": "1", "x0": 205.0, "x1": 212.0},
        {"text": "A", "x0": 20.0, "x1": 30.0},
        {"text": "2", "x0": 26.0, "x1": 33.0},
    ]
    assert _row_cells(chars) == ["A2", "B1"]
