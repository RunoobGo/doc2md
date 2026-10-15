"""tests/test_m5_m6c.py —— M5 渲染规则分级（CH-416/417）+ M6c 并发投递防护（CH-412）。

M5 验收（方案 4.5）：默认配置下输出不含 `~~`，且不含 `](#` 或 `#page-` 类自造锚点。
M6c 验收（方案 5.4）：doc_id 唯一索引 + submit 附着语义单测通过；
       与 Idempotency-Key 去重构成同一机制的两条路径。
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from doc2md.ir import IR, Block
from doc2md.pipeline.s4_render import assert_render_contract, is_external, render
from doc2md.scheduler import Scheduler

pytestmark = [pytest.mark.milestone("M5")]
m6c = pytest.mark.milestone("M6c")


def _ir(blocks):
    return IR(doc_id="r5", source_key="r5k", source_name="a.pdf", format="pdf",
              pages=1, blocks=blocks)


# --- M5：链接分级 ------------------------------------------------------------
def test_external_link_rendered():
    ir = _ir([Block(id="b1", type="paragraph", text="官方文档",
                    links=[{"url": "https://example.com/a", "text": "官方文档"}])])
    md = render(ir)
    assert "[官方文档](https://example.com/a)" in md
    assert_render_contract(md)


def test_internal_link_downgrades_to_plain_by_default():
    ir = _ir([Block(id="b1", type="paragraph", text="见第 3 节",
                    links=[{"url": "", "dest": "sec3", "text": "见第 3 节"}])])
    md = render(ir)
    assert md.strip() == "见第 3 节"
    assert "](#" not in md and "#page-" not in md
    assert_render_contract(md)


def test_internal_link_anchor_optin():
    """CH-464 收敛：anchor 档正文与 plain 同形（GFM 无自动锚点，`](#dest)` 必为
    死链且违反渲染红线）；内部链接信息保留在 IR，两档正文均为纯文本。"""
    ir = _ir([Block(id="b1", type="paragraph", text="见第 3 节",
                    links=[{"url": "", "dest": "sec3", "text": "见第 3 节"}])])
    assert render(ir, link_internal="anchor").strip() == "见第 3 节"
    assert render(ir, link_internal="anchor") == render(ir, link_internal="plain")
    assert "](#" not in render(ir, link_internal="anchor")   # 红线口径


@pytest.mark.parametrize("url,ext", [
    ("http://x", True), ("HTTPS://x", True), ("mailto:a@b.c", True),
    ("ftp://x", True), ("", False), ("#sec1", False), ("page-3", False),
    ("javascript:alert(1)", False),
])
def test_is_external_matrix(url, ext):
    assert is_external(url) is ext


# --- M5：marks 分级 ----------------------------------------------------------
def test_bold_italic_on_strike_off_by_default():
    ir = _ir([Block(id="b1", type="paragraph", text="重点",
                    marks={"bold": True, "italic": True, "strikethrough": True})])
    md = render(ir)
    assert "***重点***" in md
    assert "~~" not in md
    assert_render_contract(md)


def test_strike_optin_emits_tilde():
    ir = _ir([Block(id="b1", type="paragraph", text="删除", marks={"strikethrough": True})])
    assert "~~删除~~" in render(ir, preserve_strike=True)


def test_render_contract_rejects_violations():
    with pytest.raises(AssertionError):
        assert_render_contract("a ~~b~~ c\n")
    with pytest.raises(AssertionError):
        assert_render_contract("[x](#page-3)\n")


# --- M5：块类型渲染 + 默认配置全链路 ----------------------------------------
def test_block_type_rendering():
    ir = _ir([
        Block(id="b1", type="heading", text="标题", level=2),
        Block(id="b2", type="list_item", text="· 条目一"),
        Block(id="b3", type="code", text="pip install doc2md"),
        Block(id="b4", type="quote", text="引用行1\n引用行2"),
        Block(id="b5", type="table", text="| a | b |\n|---|---|\n| 1 | 2 |"),
        Block(id="b6", type="heading", text="超层级", level=9),
    ])
    md = render(ir)
    assert "## 标题" in md and "- 条目一" in md
    assert "```\npip install doc2md\n```" in md
    assert "> 引用行1" in md and "> 引用行2" in md
    assert "| a | b |" in md                       # 表格 GFM 原样透传
    assert "###### 超层级" in md                    # level 夹到 6
    assert_render_contract(md)


def test_default_pipeline_output_honors_contract(golden_dir, cfg):
    """真实 DOCX + PDF 走完整管线，默认配置下不得出现 ~~ 与自造锚点。"""
    from doc2md.pipeline.convert import convert
    for name in ("docx/sample_links.docx", "small_3p.pdf", "links_pdf.pdf"):
        p = golden_dir / name
        if not p.is_file():
            pytest.skip(f"missing {name}")
        res = convert(p, cfg=cfg, mode="single")
        assert "~~" not in res.markdown, name
        assert "](#" not in res.markdown and "#page-" not in res.markdown, name


# --- M6c：附着语义 + 唯一索引 ------------------------------------------------
@m6c
def test_submit_attaches_to_active_job(store):
    s = Scheduler(store)
    j1 = s.submit("docA", "srcA")
    j2 = s.submit("docA", "srcA")
    assert j2.attached and j2.job_id == j1.job_id
    assert store.q1("SELECT COUNT(*) c FROM tasks")["c"] == 1
    s.mark(j1.job_id, "done")
    j3 = s.submit("docA", "srcA")
    assert not j3.attached and j3.job_id != j1.job_id, "终态后应可重新投递"


@m6c
def test_idempotency_key_is_second_path_of_same_mechanism(store):
    s = Scheduler(store)
    j1 = s.submit("docB", "srcB", idem_key="k-1")
    j2 = s.submit("docC", "srcC", idem_key="k-1")      # 同键不同文档 → 仍命中既有作业
    assert j2.idem_hit and j2.attached and j2.job_id == j1.job_id
    assert j2.doc_id == "docB"


@m6c
def test_partial_unique_index_enforces_single_active(store):
    """唯一索引是并发窗口的最后一道闸：绕过 submit 预查也必须被拒。"""
    s = Scheduler(store)
    s.submit("docD", "srcD")
    with pytest.raises(sqlite3.IntegrityError):
        with store.tx() as tx:
            tx.execute("INSERT INTO tasks(job_id, doc_id, source_key, status, created_at)"
                       " VALUES ('job-x','docD','srcD','queued', datetime('now'))")
    # 终态不受索引约束
    with store.tx() as tx:
        tx.execute("INSERT INTO tasks(job_id, doc_id, source_key, status, created_at)"
                   " VALUES ('job-y','docD','srcD','done', datetime('now'))")


@m6c
def test_concurrent_submit_yields_one_job(store):
    """8 线程同时投递同一 doc_id，最终只有一份进行中作业。"""
    s = Scheduler(store)
    ids: list[str] = []
    lock = threading.Lock()

    def worker():
        j = s.submit("docE", "srcE")
        with lock:
            ids.append(j.job_id)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(ids)) == 1, ids
    assert store.q1("SELECT COUNT(*) c FROM tasks WHERE status IN ('queued','running')")["c"] == 1


@m6c
def test_api_idempotency_header_attaches(golden_dir, api_client):
    """同一 Idempotency-Key 两次 POST /jobs → 第二次 jobs[].idempotency_hit=True 且同 job_id。"""
    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["editor"], "Idempotency-Key": "idem-1"}
    r1 = client.post("/jobs", json={"path": str(golden_dir / "small_3p.pdf")}, headers=h)
    r2 = client.post("/jobs", json={"path": str(golden_dir / "small_3p.pdf")}, headers=h)
    assert r1.status_code == 202 and r2.status_code == 202
    j1, j2 = r1.get_json()["jobs"][0], r2.get_json()["jobs"][0]
    assert j2["idempotency_hit"] is True and j2["job_id"] == j1["job_id"]
