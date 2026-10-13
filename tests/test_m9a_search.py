"""tests/test_m9a_search.py —— M9a 编辑后可检索 + CH-435 CJK 检索修复回归。

覆盖：
- CH-435：FTS5 unicode61 把连续 CJK 当一个整词 → 中文子串查询命中 0；
  现在走 aux（unigram+bigram）列，并按 text 列 LIKE 复核字组连续性；
- CH-418 / FR-208 补充：编辑提交后 ≤5s 可检索到新内容（索引刷新在 PATCH 内同步完成）；
- 旧库迁移：无 aux 列的 v2 FTS 虚表 → 重建 + 回填（CH-435 迁移路径）；
- M8 补充：token 吊销即时生效（≤5s 内新请求 401，§4.8 验收）。
"""
from __future__ import annotations

import re
import sqlite3
import time

import pytest

from doc2md.storage import repo
from doc2md.storage.db import DB_VERSION, Store
from doc2md.storage.fts import build_match, cjk_aux, like_escape, query_terms
from doc2md.ir import Block, IR

pytestmark = [pytest.mark.milestone("M9a")]


def _seed(store: Store, doc_id: str = "d1") -> None:
    ir = IR(doc_id=doc_id, source_key="s1", source_name="small_3p.pdf",
            format="pdf", pages=3)
    ir.blocks = [
        Block(id="b1", type="heading", text="第 2 章 例外情况", level=2, page=1),
        Block(id="b2", type="paragraph",
              text="本条款涉及例外情况，standard process 不适用于紧急场景。", page=1),
        Block(id="b3", type="paragraph",
              text="The standard process requires approval before deployment. "
                   "例外是允许的，情况另议。", page=2),
        Block(id="b4", type="paragraph", text="100% 完成，_private 字段另说", page=3),
    ]
    repo.save_ir(store, ir)


# --- 单元：fts 辅助 ----------------------------------------------------------
def test_cjk_aux_ngrams():
    assert cjk_aux("例外情况") == "例 外 情 况 例外 外情 情况"
    assert cjk_aux("abc例外") == "abc 例 外 例外"      # 非 CJK 段原样保留（覆盖条款ZZ 混合 token）
    assert cjk_aux("") == ""


def test_query_terms_and_match():
    assert query_terms("例外情况 standard") == ["例外情况", "standard"]
    assert build_match("例外情况") == "例外 外情 情况"
    assert build_match('"quoted"') == '"quoted"'
    assert like_escape("100%_x") == "100\\%\\_x"


# --- CH-435：中文子串检索 ----------------------------------------------------
def test_cjk_substring_search_hits(store):
    _seed(store)
    for q, expect_ids in [("例外", {"b1", "b2", "b3"}),
                          ("例外情况", {"b1", "b2"}),      # b3 的「例外…情况」不连续 → 排除
                          ("本条款涉及", {"b2"}),
                          ("紧急场景", {"b2"})]:
        r = repo.search(store, q)
        assert r["mode"] == "fts", (q, r)
        assert r["total"] == len(expect_ids), (q, r)
        assert {h["block_id"] for h in r["hits"]} == expect_ids


def test_latin_and_mixed_search(store):
    _seed(store)
    assert {h["block_id"] for h in repo.search(store, "standard process")["hits"]} == {"b2", "b3"}
    assert {h["block_id"] for h in repo.search(store, "approval")["hits"]} == {"b3"}
    # 通配符字面量不再当 LIKE 通配
    hits = repo.search(store, "100%")["hits"]
    assert [h["block_id"] for h in hits] == ["b4"]


def test_zero_hit_is_honest(store):
    _seed(store)
    r = repo.search(store, "不存在的词组")
    assert r["total"] == 0
    r2 = repo.search(store, "")
    assert r2["mode"] == "empty" and r2["total"] == 0


def test_fts_syntax_fallthrough(store):
    """用户输入 FTS 保留语法（NEAR/3）时表达式不成立 → 退 LIKE，结果仍按子串精确。"""
    _seed(store)
    r = repo.search(store, "例外 NEAR/3 完成")   # 无块同时含「例外」与「完成」
    assert r["total"] == 0, r
    # 两个独立词是 AND 语义（不要求相邻），与「例外情况」这类短语查询不同
    r2 = repo.search(store, "例外 情况")
    assert {h["block_id"] for h in r2["hits"]} == {"b1", "b2", "b3"}
    r3 = repo.search(store, "例外情况")
    assert {h["block_id"] for h in r3["hits"]} == {"b1", "b2"}


# --- 旧库迁移：无 aux 列 → 重建回填 ------------------------------------------
def test_legacy_fts_migration(tmp_path):
    p = tmp_path / "state.sqlite"
    old = sqlite3.connect(str(p))
    old.executescript(
        "CREATE TABLE docs (doc_id TEXT PRIMARY KEY, source_key TEXT NOT NULL,"
        " source_name TEXT NOT NULL, source_name_raw TEXT DEFAULT '', format TEXT NOT NULL,"
        " pages INTEGER DEFAULT 0, sharded INTEGER DEFAULT 0, shard_count INTEGER DEFAULT 1,"
        " status TEXT NOT NULL DEFAULT 'done', profile TEXT DEFAULT 'standard',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, meta_json TEXT DEFAULT '{}');"
        " CREATE TABLE blocks (doc_id TEXT NOT NULL, block_id TEXT NOT NULL, type TEXT NOT NULL,"
        " text TEXT NOT NULL, level INTEGER, page INTEGER, flags_json TEXT DEFAULT '{}',"
        " marks_json TEXT DEFAULT '{}', immutable INTEGER DEFAULT 0,"
        " revision INTEGER NOT NULL DEFAULT 1, updated_by TEXT DEFAULT '',"
        " updated_at TEXT DEFAULT '', PRIMARY KEY (doc_id, block_id));"
        " CREATE VIRTUAL TABLE search_blocks USING fts5(doc_id UNINDEXED, block_id UNINDEXED,"
        " text, tokenize='unicode61'); PRAGMA user_version=2;")
    old.execute("INSERT INTO search_blocks VALUES (?,?,?)", ("d9", "b1", "旧库中的例外情况条款"))
    old.commit()
    old.close()

    store = Store(p)
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(search_blocks)")}
    assert "aux" in cols
    assert int(store.q1("PRAGMA user_version")[0]) == DB_VERSION
    r = repo.search(store, "例外情况")
    assert r["total"] == 1 and r["hits"][0]["doc_id"] == "d9", r
    store.close()


# --- M9a：PATCH 后 ≤5s 可检索到新内容 ----------------------------------------
def test_edit_reflected_in_search_within_5s(saved_doc):
    client, tokens, doc = saved_doc
    blocks = client.get(f"/docs/{doc}",
                        headers={"X-Doc2md-Token": tokens["admin"]}).get_json()["blocks"]
    bid = blocks[0]["id"]
    marker = "检索哨兵例外条款ZZ"
    t0 = time.monotonic()
    r = client.patch(f"/docs/{doc}/blocks/{bid}",
                     json={"text": f"{blocks[0]['text']} {marker}", "base_revision": 1},
                     headers={"X-Doc2md-Token": tokens["editor"]})
    assert r.status_code == 200, r.get_json()
    hit = None
    while time.monotonic() - t0 < 5.0:
        hit = client.get(f"/search?q={marker}",
                         headers={"X-Doc2md-Token": tokens["reader"]}).get_json()
        if hit["total"]:
            break
        time.sleep(0.05)
    assert hit and hit["total"] >= 1, f"5s 内搜不到新内容：{hit}"
    assert hit["mode"] == "fts"
    assert time.monotonic() - t0 < 5.0
    # 旧内容仍可检索（同块原文子串）
    old_sub = re.sub(r"\s+", "", blocks[0]["text"])[:6]
    if old_sub:
        assert client.get(f"/search?q={old_sub}",
                          headers={"X-Doc2md-Token": tokens["reader"]}).get_json()["total"] >= 1


def test_api_search_endpoint_role_matrix(saved_doc):
    client, tokens, doc = saved_doc
    ok = client.get("/search?q=例外", headers={"X-Doc2md-Token": tokens["reader"]})
    assert ok.status_code == 200
    body = ok.get_json()
    assert set(body) >= {"total", "hits", "mode"}
    assert client.get("/search").status_code == 401


# --- CH-436：Store 必须可从工作线程使用（serve threaded=True） ----------------
def test_store_usable_from_worker_thread(store):
    import threading
    _seed(store)
    out: dict = {}
    err: list[str] = []

    def worker():
        try:
            out["hits"] = repo.search(store, "例外情况")["total"]
            with store.tx() as tx:
                tx.execute("INSERT INTO events(ts, code, doc_id)"
                           " VALUES (datetime('now'),'THREAD','d1')")
        except Exception as e:                       # noqa: BLE001
            err.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=worker)
    t.start()
    t.join(10)
    assert not err, err
    assert out.get("hits") == 2
    assert store.q1("SELECT COUNT(*) c FROM events WHERE code='THREAD'")["c"] == 1


# --- M9a：最小审阅前端在 API 面上可用 ----------------------------------------
def test_editor_page_served(api_client):
    client, _ = api_client
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Doc2md 审阅编辑器" in body
    assert "base_revision" in body, "前端必须按 FR-203 携带锁定 revision"
    assert "/docs/" in body and "/search?q=" in body


# --- M8 补充：吊销 ≤5s 生效 ---------------------------------------------------
def test_token_revocation_effective_within_5s(api_client):
    from doc2md import auth
    client, tokens = api_client
    st = client.application.config["doc2md"]["store"]
    t = auth.issue(st, "editor", "m8-revoke-me")
    assert client.get("/search?q=x", headers={"X-Doc2md-Token": t}).status_code == 200
    auth.revoke(st, t)
    t0 = time.monotonic()
    code = None
    while time.monotonic() - t0 < 5.0:
        code = client.get("/search?q=x", headers={"X-Doc2md-Token": t}).status_code
        if code == 401:
            break
        time.sleep(0.05)
    assert code == 401, "吊销未在 5s 内生效"
    assert time.monotonic() - t0 < 5.0
    # admin 才能吊销；editor 调 token 管理端点 403
    assert client.delete(f"/tokens/{t}",
                         headers={"X-Doc2md-Token": tokens["editor"]}).status_code == 403
