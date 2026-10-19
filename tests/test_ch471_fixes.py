"""tests/test_ch471_fixes.py —— CH-471 第四轮审查回归红线。

覆盖本批次实施的正确性 / 数据安全 / 健壮性修复：
- PIPE-1：分片转换按全文档字号众数定 base_size（修复「字体跨片异质 → 分片≠单片」）；
- STOR-5：同源文件重复转换不得静默清空用户编辑；
- STOR-3：FTS 迁移重建不得留下空索引且需有对账路径；
- TEST-1：golden 资产缺失时门禁必须硬失败而非静默跳过。

方法论：块级行为直接构造 RawPage/Line 调 analyze（确定性、无第三方依赖）；
存储/门禁走内存 SQLite 与真实管线，均不触网。
"""
from __future__ import annotations

import copy
import zipfile

import pytest

from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.pipeline.s3_structure import analyze, modal_size

pytestmark = [pytest.mark.milestone("DoD")]


def _page(*lines: Line, index: int = 0) -> RawPage:
    return RawPage(index=index, lines=list(lines))


def _body(size: float, n: int, tag: str) -> list[Line]:
    # 每行 ~40 个汉字，供 modal_size 按字符数加权
    return [Line(text=f"{tag}正文内容行{i}，用于把众数字号稳定在这一档。" * 1, size=size)
            for i in range(n)]


def _find(ir, prefix: str):
    for b in ir.blocks:
        if (b.text or "").startswith(prefix):
            return b
    return None


# ------------------------------------------------------------------ PIPE-1

# 跨片异质样本：shard A（页0/1）正文 10pt 主导，shard B（页2/3）正文 20pt 主导，
# B 内插一条「3.1 开头、非粗、长 40~80、以句号收尾、22pt」的行。
# 全文档众数 = 10 → 该行 22≥10+3.5 判为标题；片内众数 = 20 → 22<20+3.5 判为段落。
_FLIP = "3.1 这一行编号齐全但字号只比片内正文大两号，跨片口径不同会翻判。"
_PAGES = [
    _page(*_body(10.0, 15, "A0"), index=0),
    _page(*_body(10.0, 15, "A1"), index=1),
    _page(Line(text=_FLIP, size=22.0), *_body(20.0, 8, "B2"), index=2),
    _page(*_body(20.0, 8, "B3"), index=3),
]
_TAIL = _PAGES[2:]


def test_modal_size_whole_vs_shard_diverge():
    """前置事实：全文档众数(10) ≠ 尾片局部众数(20)——异质字体确实存在。"""
    assert modal_size(_PAGES) == 10.0
    assert modal_size(_TAIL) == 20.0


def test_modal_size_hist_path_matches_line_scan(small_pdf):
    """CH-492（P-1）：直方图归并路径与逐行扫描回退路径必须同值。

    base_size 是「分片≡单片」判据根（convert.py CH-471/PIPE-1）；归并若在
    计数或平票插入序上偏离逐行扫描，golden 与分片等价性会静默劣化。
    """
    from doc2md.pipeline.s1_parse import read_pdf
    pages = read_pdf(small_pdf)
    with_hist = modal_size(pages)
    for p in pages:
        p.size_hist = {}                     # 清直方图 → 强制逐行扫描回退
    assert modal_size(pages) == with_hist


def test_pipe1_old_per_shard_modal_flips_heading():
    """复现缺陷：尾片按**自身**众数分析（旧 convert 行为）会把该行判成段落。"""
    ir = analyze(_TAIL)                       # base_size=None → 片内自算 20
    b = _find(ir, "3.1")
    assert b is not None and b.type == "paragraph", \
        "旧口径应把该行降为段落（这正是 PIPE-1 的丢标题 bug）"


def test_pipe1_whole_doc_base_restores_equivalence():
    """修复断言：尾片按**全文档**众数分析 = 单片整篇分析（G1 分片≡单片）。"""
    whole = modal_size(_PAGES)
    ir_shard = analyze(_TAIL, base_size=whole)   # 新 convert 行为
    ir_single = analyze(_PAGES)                  # 单片整篇
    assert _find(ir_shard, "3.1").type == "heading"
    assert _find(ir_single, "3.1").type == "heading"


def test_convert_passes_single_whole_doc_base_to_all_shards(monkeypatch, cfg,
                                                            tmp_path, small_pdf):
    """接线断言：convert 对每片 analyze 传同一个非空 base_size（全文档众数），
    而非各片 None 现算——这是 PIPE-1 的修复落点。"""
    import importlib
    cv = importlib.import_module("doc2md.pipeline.convert")   # 模块，非 __init__ 再导出的同名函数

    calls: list = []
    orig = cv.analyze

    def spy(pages, base_size=None):
        calls.append(base_size)
        return orig(pages, base_size=base_size)

    monkeypatch.setattr(cv, "analyze", spy)
    c = copy.deepcopy(cfg)
    c.setdefault("s2", {})["max_pages_per_shard"] = 2      # 3 页 → 切成多片
    cv._convert(small_pdf, cfg=c, mode="two_phase",
                out_dir=str(tmp_path / "o"), raw_name=None)

    assert len(calls) >= 2, f"未触发分片，calls={calls}"
    assert all(b is not None for b in calls), f"存在 base_size=None 的分片：{calls}"
    assert len(set(calls)) == 1, f"各片 base_size 不一致（应共享全文档众数）：{calls}"


# ------------------------------------------------------------------ STOR-5

import json  # noqa: E402

from doc2md.edits import patch_block  # noqa: E402
from doc2md.errors import E4091_DocOverwrite  # noqa: E402
from doc2md.ir import IR, Block  # noqa: E402
from doc2md.storage.repo import save_ir  # noqa: E402


def _doc_ir(doc_id: str = "s5") -> IR:
    ir = IR(doc_id=doc_id, source_key="sk-" + doc_id, source_name="demo.pdf",
            format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"原始段落{i}", page=1)
                 for i in range(1, 4)]
    return ir


def _created_at(store, doc_id: str) -> str:
    return store.q1("SELECT created_at FROM docs WHERE doc_id=?", (doc_id,))["created_at"]


def test_save_ir_refuses_overwrite_of_edited_doc(store):
    """STOR-5 核心：编辑过（revision>1）的文档，重复转换入库默认拒绝——编辑不得丢。"""
    save_ir(store, _doc_ir())
    patch_block(store, "s5", "b1", {"text": "用户改过的内容", "base_revision": 1}, "tester")
    created = _created_at(store, "s5")

    with pytest.raises(E4091_DocOverwrite) as ei:
        save_ir(store, _doc_ir())        # 同一 doc_id，正文是「原始段落」→ 覆盖即丢编辑
    assert ei.value.detail["edited_blocks"] == 1
    assert ei.value.http == 409
    row = store.q1("SELECT text, revision FROM blocks WHERE doc_id='s5' AND block_id='b1'")
    assert row["text"] == "用户改过的内容" and row["revision"] == 2   # 拒绝不得改动库
    assert _created_at(store, "s5") == created


def test_save_ir_force_overwrites_and_preserves_created_at(store):
    """force=True 才放行覆盖：正文替换、revision 归 1、created_at 保留、记 doc_overwritten。"""
    save_ir(store, _doc_ir())
    patch_block(store, "s5", "b1", {"text": "将被覆盖", "base_revision": 1}, "tester")
    created = _created_at(store, "s5")

    save_ir(store, _doc_ir(), force=True)
    row = store.q1("SELECT text, revision FROM blocks WHERE doc_id='s5' AND block_id='b1'")
    assert row["text"] == "原始段落1" and row["revision"] == 1
    assert _created_at(store, "s5") == created
    ev = store.q1("SELECT payload_json FROM events"
                  " WHERE doc_id='s5' AND code='doc_overwritten'")
    assert ev is not None
    assert json.loads(ev["payload_json"])["overwritten_edits"] == 1


def test_save_ir_idempotent_reconvert_preserves_created_at(store):
    """无编辑的重复转换（幂等重写）应放行，但保留 created_at、记 doc_overwritten 事件。"""
    save_ir(store, _doc_ir())
    sentinel = "2000-01-01T00:00:00.000+00:00"
    with store.tx() as t:
        t.execute("UPDATE docs SET created_at=? WHERE doc_id='s5'", (sentinel,))

    save_ir(store, _doc_ir())            # 全 revision=1 → 放行，不得重置 created_at
    assert _created_at(store, "s5") == sentinel
    assert store.q1("SELECT 1 c FROM events WHERE doc_id='s5' AND code='doc_overwritten'")


def _h(tokens, role):
    return {"X-Doc2md-Token": tokens[role]}


def test_api_reconvert_conflict_keeps_edits(api_client, saved_doc, small_pdf):
    """HTTP 面接线：转换→编辑→再转换 → 该文件 status=conflict 且编辑仍在；force 才覆盖。"""
    client, tokens, doc_id = saved_doc
    blocks = client.get(f"/docs/{doc_id}", headers=_h(tokens, "admin")).get_json()["blocks"]
    bid = blocks[0]["id"]
    r = client.patch(f"/docs/{doc_id}/blocks/{bid}",
                     json={"text": "哨兵编辑例外条款ZZ", "base_revision": 1},
                     headers=_h(tokens, "editor"))
    assert r.status_code == 200, r.get_json()

    # 默认再转换同一文件：拒绝，编辑不得丢
    again = client.post("/jobs", json={"path": str(small_pdf)}, headers=_h(tokens, "editor"))
    assert again.status_code == 202, again.get_json()
    job = again.get_json()["jobs"][0]
    assert job["status"] == "conflict" and job["code"] == "E4091", job
    still = client.get(f"/docs/{doc_id}", headers=_h(tokens, "admin")).get_json()["blocks"]
    assert any(b["id"] == bid and "哨兵编辑例外条款ZZ" in b["text"] for b in still), \
        "拒绝覆盖不得动已有编辑"

    # force=true：明确放行覆盖（操作者知情），正文按新转换重建
    forced = client.post("/jobs", json={"path": str(small_pdf), "force": True},
                         headers=_h(tokens, "editor"))
    fj = forced.get_json()["jobs"][0]
    assert fj.get("status") != "conflict" and fj.get("doc_id") == doc_id, fj
    after = client.get(f"/docs/{doc_id}", headers=_h(tokens, "admin")).get_json()["blocks"]
    assert not any("哨兵编辑例外条款ZZ" in b["text"] for b in after), "force 覆盖后编辑清除"


# ------------------------------------------------------------------ STOR-3

from doc2md.ir import IR as _IR  # noqa: E402
from doc2md.storage.db import Store  # noqa: E402
from doc2md.storage.repo import reconcile_search, search  # noqa: E402


def _mg_ir(doc_id: str, text: str) -> _IR:
    ir = _IR(doc_id=doc_id, source_key="k-" + doc_id, source_name="d.pdf",
             format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text=text, page=1)]
    return ir


def test_reconcile_search_revives_missing_index(store):
    """blocks 有正文、search_blocks 被清空（迁移/崩溃后）→ 对账整篇重建（复活 reindex）。"""
    ir = _IR(doc_id="rc", source_key="k-rc", source_name="d.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="段落一 例外情况", page=1),
                 Block(id="b2", type="paragraph", text="段落二 例外情况", page=1)]
    save_ir(store, ir)
    with store.tx() as t:
        t.execute("DELETE FROM search_blocks WHERE doc_id='rc'")   # 模拟空索引
    assert search(store, "例外情况")["total"] == 0                 # 检索静默失效

    rec = reconcile_search(store)
    assert rec["docs_repaired"] == 1, rec
    assert search(store, "例外情况")["total"] == 2, rec           # 两块检索均恢复


def test_reconcile_search_removes_orphan_rows(store):
    """索引里有、blocks 里已无的孤儿行必须清掉（否则搜索命中不存在的块）。"""
    save_ir(store, _mg_ir("live", "现存正文 例外情况"))
    with store.tx() as t:
        t.execute("INSERT INTO search_blocks(doc_id, block_id, text, aux)"
                  " VALUES ('ghost','g','幽灵正文','')")
    rec = reconcile_search(store)
    assert rec["orphans_removed"] >= 1, rec
    assert store.q1("SELECT 1 c FROM search_blocks WHERE doc_id='ghost'") is None


def test_fts_migration_rebuilds_aux_atomically(tmp_path):
    """旧库（FTS 无 aux 列）重开 → 影子表流式回填 + 单事务替换：aux 补齐、旧正文仍可 CJK 检索。"""
    import sqlite3

    from doc2md.storage.repo import save_ir as _save
    db = tmp_path / "state.sqlite"
    store = Store(db)
    _save(store, _mg_ir("mg", "中文例外情况检索哨兵"))
    store.close()

    raw = sqlite3.connect(db)                       # 退化成 aux-less 旧形态并回填正文
    raw.execute("DROP TABLE search_blocks")
    raw.execute("CREATE VIRTUAL TABLE search_blocks USING fts5("
                "doc_id UNINDEXED, block_id UNINDEXED, text, tokenize='unicode61')")
    raw.execute("INSERT INTO search_blocks VALUES ('mg','b1','中文例外情况检索哨兵')")
    raw.commit()
    raw.close()

    store2 = Store(db)                              # _migrate_fts 发现无 aux → 重建
    cols = {r["name"] for r in store2._conn.execute("PRAGMA table_info(search_blocks)")}
    assert "aux" in cols, "迁移未补上 aux 列"
    assert not store2.q1("SELECT 1 c FROM sqlite_master WHERE type='table'"
                        " AND name='search_blocks_new'"), "影子表未清理（替换未提交）"
    assert store2.q1("SELECT COUNT(*) c FROM search_blocks")["c"] == 1
    assert search(store2, "例外情况")["total"] == 1   # 迁移后旧正文可检索
    store2.close()


# ------------------------------------------------------------------ TEST-1

def test_missing_golden_hard_fails(tmp_path, monkeypatch):
    """golden 缺失时 `_need` 必须硬失败（Failed）而非 skip——防红线门禁伪绿。

    仅显式 DOC2MD_ALLOW_MISSING_GOLDEN=1 才降级为 skip（有意子集运行）。
    """
    import conftest

    monkeypatch.setattr(conftest, "GOLDEN", tmp_path)     # 空目录，无样本
    monkeypatch.delenv("DOC2MD_ALLOW_MISSING_GOLDEN", raising=False)
    with pytest.raises(pytest.fail.Exception):
        conftest._need("small_3p.pdf")
    monkeypatch.setenv("DOC2MD_ALLOW_MISSING_GOLDEN", "1")
    with pytest.raises(pytest.skip.Exception):
        conftest._need("small_3p.pdf")


# ------------------------------------------------------------------ B2 性能

def test_db_pragmas_tuned(tmp_path):
    """STOR-1 / 决策 D1：连接建立即套 WAL+NORMAL+mmap+cache（每提交 fsync 是吞吐主因）。"""
    from doc2md.storage import db as dbmod
    s = Store(tmp_path / "state.sqlite")
    c = s._conn
    assert str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    assert int(c.execute("PRAGMA synchronous").fetchone()[0]) == 1        # NORMAL
    assert int(c.execute("PRAGMA mmap_size").fetchone()[0]) == dbmod.SQLITE_MMAP_BYTES
    assert int(c.execute("PRAGMA cache_size").fetchone()[0]) == -dbmod.SQLITE_CACHE_KB
    s.close()


def test_search_orders_by_bm25_relevance(store):
    """STOR-2：命中按 bm25 相关性排序——高词频块应靠前（原按 rowid 插入序）。"""
    ir = _IR(doc_id="sr", source_key="k", source_name="d.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="standard term", page=1),
                 Block(id="b2", type="paragraph",
                       text="standard standard standard standard term", page=1)]
    save_ir(store, ir)
    r = search(store, "standard")
    assert r["mode"] == "fts", r
    order = [h["block_id"] for h in r["hits"]]
    assert order.index("b2") < order.index("b1"), order   # b2 tf 更高 → 更相关


def test_search_has_more_probe_replaces_exact_count(store):
    """STOR-2：主查询多取一行探测 has_more，取消第二遍全表 COUNT。

    截断时 total=本页可见数（≤limit），未截断时 total 仍是精确命中数。
    """
    ir = _IR(doc_id="hm", source_key="k", source_name="d.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"哨兵{i} 例外情况", page=1)
                 for i in range(25)]
    save_ir(store, ir)

    page = search(store, "例外情况", limit=10)
    assert len(page["hits"]) == 10 and page["has_more"] is True
    assert page["total"] == 10
    full = search(store, "例外情况", limit=100)
    assert full["total"] == 25 and full["has_more"] is False


def test_rotating_writer_encodes_once_valid_jsonl(tmp_path):
    """PERF-2：二进制追加 + 单次编码后，日志仍是逐行可解的 UTF-8 JSON（含中文）。"""
    from doc2md.ops.logging import RotatingJsonlWriter

    w = RotatingJsonlWriter(tmp_path, stem="t", rotate_mb=1)
    w.write({"code": "X", "note": "中文正文片段应被截断为指纹", "ts": "t"})
    w.flush()
    w.close()
    raw = (tmp_path / "t.jsonl").read_bytes()
    rec = json.loads(raw.splitlines()[-1].decode("utf-8"))
    assert rec["code"] == "X"


# ------------------------------------------------------------------ B3 服务层

def test_validator_blocks_merge_into_immutable_prev():
    """SVC-1：merge_block 声明的是非 immutable 目标，但其**写入目的地**（紧邻前驱）
    是 immutable → validator 必须在有效写集上拒绝（原只验声明目标可绕过红线）。"""
    from doc2md.llm import OpValidator

    blocks = [Block(id="p1", type="paragraph", text="甲", immutable=True),
              Block(id="p2", type="paragraph", text="乙")]
    acc, rej = OpValidator(blocks).apply([{"op": "merge_block", "target": "p2"}])
    assert acc == [] and len(rej) == 1
    assert any(r.startswith("immutable:p1") for r in rej[0]["reasons"]), rej


def test_apply_ops_refuses_rewriting_immutable_prev():
    """SVC-1 二次设防：绕过 validator 直接 apply_ops，也不得改写 immutable 前驱。"""
    from doc2md.llm import apply_ops

    ir = _IR(doc_id="imm", source_key="k", source_name="d.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="p1", type="paragraph", text="不可变", immutable=True),
                 Block(id="p2", type="paragraph", text="可变")]
    n = apply_ops(ir, [{"op": "merge_block", "target": "p2"}])
    assert n == 0
    assert ir.blocks[0].text == "不可变" and not ir.blocks[0].flags.get("absorbed_ids")
    assert any(b.id == "p2" for b in ir.blocks)      # 未被吸收


def test_merge_into_mutable_prev_still_works():
    """SVC-1 正向：前驱可变时 merge 正常落地（护栏未削弱合法路径）。"""
    from doc2md.llm import apply_ops

    ir = _IR(doc_id="ok", source_key="k", source_name="d.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="p1", type="paragraph", text="甲"),
                 Block(id="p2", type="paragraph", text="乙")]
    n = apply_ops(ir, [{"op": "merge_block", "target": "p2"}])
    assert n == 1 and [b.id for b in ir.blocks] == ["p1"]


def test_api_unhandled_exception_is_json_500(api_client, monkeypatch):
    """SVC-2：非 Doc2mdError 逃逸异常必须走 {code,message,detail} JSON（500/E5000），
    不得回落 Werkzeug HTML 500；响应不回显异常正文（NFR-20）。"""
    client, tokens = api_client

    def boom(*a, **k):
        raise RuntimeError("内部炸了 敏感正文")

    monkeypatch.setattr("doc2md.api.get_doc", boom)
    r = client.get("/docs/whatever", headers=_h(tokens, "reader"))
    assert r.status_code == 500, r.get_data(as_text=True)[:200]
    body = r.get_json()
    assert body["code"] == "E5000", body
    assert "敏感正文" not in json.dumps(body, ensure_ascii=False)


def test_api_markdown_corrupt_zip_is_e5031(api_client, saved_doc, tmp_path):
    """SVC-2：产物 zip 损坏 → 500/E5031（不得 BadZipFile 逃逸成 HTML 500）。"""
    from pathlib import Path

    client, tokens, doc_id = saved_doc
    zp = Path(tmp_path) / "work" / "out" / f"{doc_id}.zip"
    assert zp.exists(), "样本应先产出 zip"
    zp.write_bytes(b"this is definitely not a zip archive")
    r = client.get(f"/docs/{doc_id}/markdown", headers=_h(tokens, "admin"))
    assert r.status_code == 500, r.get_data(as_text=True)[:200]
    assert r.get_json()["code"] == "E5031", r.get_json()


def test_api_markdown_missing_content_entry_is_e4041(api_client, saved_doc, tmp_path):
    """SVC-2：合法 zip 但无 content.md → 404/E4041（不得 IndexError 逃逸 500）。"""
    from pathlib import Path

    client, tokens, doc_id = saved_doc
    zp = Path(tmp_path) / "work" / "out" / f"{doc_id}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "no markdown here")
    r = client.get(f"/docs/{doc_id}/markdown", headers=_h(tokens, "admin"))
    assert r.status_code == 404, r.get_data(as_text=True)[:200]
    assert r.get_json()["code"] == "E4041", r.get_json()


def test_cron_purges_old_terminal_tasks(tmp_path, tmp_cfg):
    """SVC-4：终态（done/rejected）且过保留期的作业清理；queued / 近期完成不动。"""
    import doc2md.cron as cron
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    root.mkdir(parents=True, exist_ok=True)
    s = Store(root / "state.sqlite")
    with s.tx() as t:
        for jid, doc, status, age in [
            ("old-done", "d1", "done", "-100 days"),
            ("old-rejected", "d3", "rejected", "-100 days"),
        ]:
            t.execute("INSERT INTO tasks(job_id,doc_id,status,created_at,finished_at)"
                      " VALUES(?,?,?,datetime('now'),datetime('now',?))",
                      (jid, doc, status, age))
        t.execute("INSERT INTO tasks(job_id,doc_id,status,created_at,finished_at)"
                  " VALUES('recent-done','d2','done',datetime('now'),datetime('now'))")
        t.execute("INSERT INTO tasks(job_id,doc_id,status,created_at)"
                  " VALUES('old-queued','d4','queued',datetime('now','-100 days'))")
    s.close()

    rep = cron.run(tmp_cfg, root=root, mode="daily", with_backup=False)
    step = next(x for x in rep.steps if x["step"] == "tasks_purge")
    assert step["removed"] == 2, step

    s2 = Store(root / "state.sqlite")
    ids = {r["job_id"] for r in s2._conn.execute("SELECT job_id FROM tasks").fetchall()}
    s2.close()
    assert ids == {"recent-done", "old-queued"}, ids


def test_api_sync_parallel_dispatch_by_threshold(api_client, tmp_path, monkeypatch, small_pdf):
    """SVC-3：同步 POST /jobs 仅在文件数达阈值时才新建进程池；小批走进程内串行
    （确定性、保留错误按 HTTP 码传播），并行分支按输入顺序回灌结果并回读 IR 入库。

    并行分支桩掉 run_batch/_read_ir（不真起子进程）以稳定验证派活与映射逻辑。
    """
    import shutil

    import doc2md.api as api
    import doc2md.pipeline.batch as batch

    client, tokens = api_client
    d = tmp_path / "drop"
    d.mkdir()
    for i in range(3):
        shutil.copy(small_pdf, d / f"s{i}.pdf")
    h = _h(tokens, "editor")

    calls: list = []
    monkeypatch.setattr(batch, "run_batch",
                        lambda paths, **kw: calls.append([str(p) for p in paths]) or [])

    # 默认阈值 8 > 3 → 串行，绝不建池；三个真实文档入库
    r = client.post("/jobs", json={"path": str(d)}, headers=h)
    assert calls == [], "小批应走进程内串行，不新建进程池"
    jobs = r.get_json()["jobs"]
    assert len(jobs) == 3 and all(j.get("doc_id") for j in jobs), jobs

    # 降阈值（→ max(2,1)=2）+ 关闭字节条件（sync_min_bytes=0），3 文件走并行：
    # 桩 run_batch 记录输入顺序、_read_ir 造 IR
    client.put("/settings", json={"perf": {"sync_min_parallel": 1,
                                           "sync_min_bytes": 0}},
               headers=_h(tokens, "admin"))

    def fake_batch(paths, **kw):
        calls.append([str(p) for p in paths])
        return [{"file": str(p), "status": "ok", "doc_id": f"pb{i}",
                 "source_key": "sk", "blocks": 3, "zip": f"doc:pb{i}"}
                for i, p in enumerate(paths)]

    monkeypatch.setattr(batch, "run_batch", fake_batch)
    monkeypatch.setattr(api, "read_ir",
                        lambda zp: _doc_ir(doc_id=str(zp).split("doc:", 1)[1]))
    calls.clear()
    r2 = client.post("/jobs", json={"path": str(d)}, headers=h)
    assert calls and len(calls[0]) == 3, calls
    j2 = r2.get_json()["jobs"]
    assert len(j2) == 3 and {j["doc_id"] for j in j2} == {"pb0", "pb1", "pb2"}, j2
    assert all(j.get("status") not in ("error", "conflict") for j in j2), j2

    # CH-492（QA M-1 双条件）：件数够但批内总字节不足 → 仍走串行（小文件大批量
    # 走池是负优化：实测 8 个最小 PDF 慢 7.8 倍）。sync_min_bytes=10GB 远超
    # 3 个小 PDF 的总字节 → 字节条件拦截，不建池。
    client.put("/settings", json={"perf": {"sync_min_parallel": 1,
                                           "sync_min_bytes": 10_000_000_000}},
               headers=_h(tokens, "admin"))
    calls.clear()
    r3 = client.post("/jobs", json={"path": str(d)}, headers=h)
    assert calls == [], "件数够但总字节不足应走串行（双条件的字节侧拦截）"
    j3 = r3.get_json()["jobs"]
    assert len(j3) == 3 and all(j.get("doc_id") for j in j3), j3


# --- SVC-5 / SVC-6（D4）：绑定强制 loopback + 冻结 offline_assert ----------------
def _cfg_with(**paths):
    """从 loader._base() 取一份完整默认配置，按 dotted 路径写入值后返回。"""
    from doc2md.config import loader
    cfg = loader._base()
    for dotted, val in paths.items():
        cur = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return cfg


@pytest.mark.parametrize("key,val", [
    ("api.bind", "0.0.0.0"),        # 全部 IPv4 接口
    ("api.bind", "::"),             # 全部 IPv6 接口
    ("api.bind", "192.168.1.20"),   # 局域网 IP
    ("llm.bind", "0.0.0.0"),        # llm 服务同样受限
    ("api.bind", "example.com"),    # 无法确定是回环 → 保守拒绝
])
def test_svc5_validate_rejects_nonloopback_bind(key, val):
    from doc2md import config
    cfg = _cfg_with(**{key: val})
    with pytest.raises(config.ConfigError, match="loopback"):
        config.validate(cfg)


@pytest.mark.parametrize("key,val", [
    ("api.bind", "127.0.0.1"),
    ("api.bind", "localhost"),
    ("api.bind", "LocalHost"),      # 大小写不敏感
    ("api.bind", "::1"),
    ("api.bind", "[::1]"),          # 容忍 IPv6 方括号写法
])
def test_svc5_validate_allows_loopback_bind(key, val):
    from doc2md import config
    config.validate(_cfg_with(**{key: val}))   # 不抛即通过


def test_svc5_remote_bind_requires_explicit_opt_in():
    from doc2md import config
    cfg = _cfg_with(**{"api.bind": "0.0.0.0", "api.allow_remote_bind": True})
    config.validate(cfg)                        # 显式 opt-in 后放行


def test_svc6_offline_assert_frozen_at_validate():
    """SVC-6：offline_assert 不是全局冻结键——convert(cfg) 仍可带 false（合法 profile），
    validate() 不拦它；红线只拦运行期 PUT（见 test_svc5_svc6_put_settings_rejects）。"""
    from doc2md import config
    config.validate(_cfg_with(**{"run.offline_assert": False}))   # 装载/代码路径：放行
    config.validate(_cfg_with(**{"run.offline_assert": True}))


def test_svc6_runtime_immutable_blocks_offline_assert_change():
    from doc2md import config
    from doc2md.config import loader
    base = loader._base()                                          # offline_assert = True
    cand = _cfg_with(**{"run.offline_assert": False})              # 试图关掉守护
    with pytest.raises(config.ConfigError, match="运行期不可变"):
        config.check_runtime_immutable(cand, base)
    # 改回同值不算修改 → 放行
    config.check_runtime_immutable(_cfg_with(**{"run.offline_assert": True}), base)


def test_svc5_svc6_put_settings_rejects_red_line_change(api_client):
    """SVC-5/6：PUT /settings 走同一 validate —— 非回环 bind 与关闭 offline_assert
    均在应用前被拒（400），内存配置与落盘 override 都不动。"""
    client, tokens = api_client
    h = _h(tokens, "admin")
    before = client.get("/settings", headers=h).get_json()
    for body in ({"api": {"bind": "0.0.0.0"}}, {"run": {"offline_assert": False}}):
        r = client.put("/settings", json=body, headers=h)
        assert r.status_code == 400, (body, r.get_json())
    assert client.get("/settings", headers=h).get_json() == before


# --- SEC-4（names.py）：内部连点不再误伤，路径穿越仍拒 --------------------------
def test_sec4_internal_dots_survive_sanitize():
    from doc2md.security.names import sanitize_name
    assert sanitize_name("a..b.pdf") == "a..b.pdf"        # 连点为合法名，不再 E4003
    assert sanitize_name("report..final.md") == "report..final.md"


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"),                       # 剥路径后无穿越
    ("dir/..", "unnamed"),                                # 末段 .. 被 strip 掉 → 无名
])
def test_sec4_traversal_still_neutralized(raw, expected):
    from doc2md.security.names import sanitize_name
    out = sanitize_name(raw)
    assert ".." not in out and "/" not in out and "\\" not in out
    assert out == expected


@pytest.mark.parametrize("bad", ["..", "a/../../b", "..\\..\\x", "x\\..\\y"])
def test_sec4_assert_rejects_dotdot_segments(bad):
    from doc2md.errors import E4003_FileNameUnsafe
    from doc2md.security.names import assert_name_safe
    with pytest.raises(E4003_FileNameUnsafe):
        assert_name_safe(bad)


# --- SEC-1（netguard）：sendmsg 也是出站原语，须与 sendto/connect 同受守护 ------
def test_sec1_sendmsg_blocked_under_guard():
    import socket as _sock

    from doc2md.security.netguard import OfflineViolation, offline_guard
    if not hasattr(_sock.socket, "sendmsg"):
        pytest.skip("POSIX-only socket primitive; Windows has no sendmsg")
    with pytest.raises(OfflineViolation):
        with offline_guard(True):
            _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM).sendmsg(
                b"probe", 0, 0, ("93.184.216.34", 53))   # message, ancdata, flags, address


def test_sec1_sendmsg_connected_placeholder_uses_sendto_peer(monkeypatch):
    # 连接态套接字的占位 (0,0) 不应被当作外发地址判定；关键字外部地址则必须拦截。
    # 桩掉 _ORIG["sendmsg"] 直接验证守护的判定分支，不触碰真实 syscall。
    from doc2md.security import netguard

    sent: list = []
    monkeypatch.setitem(netguard._ORIG, "sendmsg",
                        lambda self, m, *a, **k: sent.append((m, a, k)))
    monkeypatch.setitem(netguard._STATE, "allowed", {"127.0.0.1", "::1"})
    netguard._guarded_sendmsg(object(), b"ping", (), 0, (0, 0))   # 占位 → 放行
    assert sent and sent[0][0] == b"ping", sent
    with pytest.raises(netguard.OfflineViolation):
        netguard._guarded_sendmsg(object(), b"x", address=("93.184.216.34", 53))


# --- SVC-9（token 哈希存储 + 存量明文迁移）--------------------------------------
def test_sec9_issue_stores_hash_not_plaintext(tmp_path):
    import hashlib

    from doc2md import auth
    st = Store(tmp_path / "state.sqlite")
    try:
        tok = auth.issue(st, "admin", "sec9")
        row = st.q1("SELECT token FROM tokens WHERE label='sec9'")
        assert row["token"] == hashlib.sha256(tok.encode()).hexdigest()
        assert row["token"] != tok and not row["token"].startswith("d2m_")
        assert auth.check(st, tok, "read") == "admin"          # 明文仍可用（按哈希查）
        assert auth.revoke(st, tok) is True                    # 按哈希吊销命中
        with pytest.raises(Exception):
            auth.check(st, tok, "read")                        # 吊销后失效
    finally:
        st.close()


def test_sec9_migrates_existing_plaintokens_on_reopen(tmp_path):
    from doc2md import auth
    db = tmp_path / "state.sqlite"
    st = Store(db)
    with st.tx() as tx:
        tx.execute("INSERT INTO tokens(token, role, label, created_at) VALUES (?,?,?,?)",
                   ("d2m_legacy_plain", "editor", "legacy", "2020-01-01T00:00:00+00:00"))
    st.close()

    st2 = Store(db)                                            # 重开触发 _migrate_token_hash
    try:
        row = st2.q1("SELECT token, role FROM tokens WHERE label='legacy'")
        assert row["token"] != "d2m_legacy_plain"
        assert len(row["token"]) == 64 and all(c in "0123456789abcdef" for c in row["token"])
        # 旧明文凭证升级后仍通过哈希校验，不打断在用 token
        assert auth.check(st2, "d2m_legacy_plain", "submit_job") == "editor"
    finally:
        st2.close()


# --- OPS-3（gguf 尺寸帽）：恶意声明长度先帽后读，截断报错而非裸 struct.error ------
def _gguf_header(n_kv: int) -> bytes:
    import struct
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", n_kv)


def _gguf_key(name: str) -> bytes:
    import struct
    b = name.encode()
    return struct.pack("<Q", len(b)) + b


def test_ops3_string_length_capped(tmp_path):
    import struct

    from doc2md.ops.gguf import GgufError, read_metadata
    # 一个 KV：key="x"，value 声明为 STRING 且长度 2**40（远超帽），后面不接任何数据
    blob = (_gguf_header(1) + _gguf_key("x") + struct.pack("<I", 8)
            + struct.pack("<Q", 1 << 40))
    p = tmp_path / "huge_str.gguf"
    p.write_bytes(blob)
    with pytest.raises(GgufError, match="exceeds cap"):
        read_metadata(p)


def test_ops3_array_count_capped(tmp_path):
    import struct

    from doc2md.ops.gguf import GgufError, read_metadata
    # value 声明为 ARRAY<STRING>，count = 2**40 → 元素数帽拦截，绝不进入 range 展开
    blob = (_gguf_header(1) + _gguf_key("x") + struct.pack("<I", 9)
            + struct.pack("<I", 8) + struct.pack("<Q", 1 << 40))
    p = tmp_path / "huge_arr.gguf"
    p.write_bytes(blob)
    with pytest.raises(GgufError, match="exceeds cap"):
        read_metadata(p)


def test_ops3_truncated_string_is_gguf_error(tmp_path):
    import struct

    from doc2md.ops.gguf import GgufError, read_metadata
    # 声明字符串长度 100，但文件在 key 之后即截断 → 干净 GgufError（非裸 struct.error）
    blob = _gguf_header(1) + _gguf_key("x") + struct.pack("<I", 8) + struct.pack("<Q", 100)
    p = tmp_path / "trunc.gguf"
    p.write_bytes(blob)
    with pytest.raises(GgufError, match="truncated"):
        read_metadata(p)


def test_ops3_valid_small_gguf_still_reads(tmp_path):
    from gguf_fixture import build_gguf

    from doc2md.ops.gguf import read_metadata
    p = build_gguf(tmp_path / "ok.gguf")
    assert read_metadata(p).tokens          # 正常词表照常读出（帽不误伤合法文件）


# --- SVC-8（质量）：GET /jobs limit 非负校验 + 同步多文件不半途 4xx ------------------
def test_svc8_jobs_limit_negative_and_nonint_rejected(api_client):
    """SVC-8：GET /jobs 的 limit 与 `_window` 同口径——负值 400/E4001、非数字 400/E4001。
    原裸 `int()` 无负值保护 → `?limit=-1` 拼进 SQL 得 `LIMIT -1`（SQLite 语义 = 全部），
    既绕开队列分页上限又与 `_window` 的拒负不一致。"""
    client, tokens = api_client
    h = _h(tokens, "reader")
    assert client.get("/jobs", headers=h).status_code == 200
    assert client.get("/jobs?limit=5", headers=h).status_code == 200
    neg = client.get("/jobs?limit=-1", headers=h)
    assert neg.status_code == 400, neg.get_data(as_text=True)[:200]
    assert neg.get_json()["code"] == "E4001"
    bad = client.get("/jobs?limit=abc", headers=h)
    assert bad.status_code == 400 and bad.get_json()["code"] == "E4001"


def test_svc8_serial_multifile_error_reports_per_file(api_client, tmp_path, small_pdf):
    """SVC-8：同步多文件批次中途撞硬错误（415/E4151）→ 不再整体 4xx 留下「前半截已落库、
    客户端不知情」的半成品；改记本文件 error 后继续，返回 202 且每个文件都有明确终态
    （好文件带 doc_id、坏文件带 code）。单文件仍按 HTTP 码传播（test_perf_lowend 已锁）。"""
    import shutil

    client, tokens = api_client
    d = tmp_path / "drop"
    d.mkdir()
    shutil.copy(small_pdf, d / "good1.pdf")
    (d / "bad.pdf").write_bytes(b"PK\x03\x04this is not a docx\n")
    shutil.copy(small_pdf, d / "good2.pdf")
    r = client.post("/jobs", json={"path": str(d)}, headers=_h(tokens, "editor"))
    assert r.status_code == 202, r.get_data(as_text=True)[:300]
    by = {j["file"]: j for j in r.get_json()["jobs"]}
    assert by["bad.pdf"]["status"] == "error" and by["bad.pdf"]["code"] == "E4151"
    assert by["good1.pdf"].get("doc_id") and by["good2.pdf"].get("doc_id")


# --- STOR-6（数据安全）：PATCH 缺 text / 非字符串不得静默清空 ----------------------
def test_stor6_patch_requires_text_string(store):
    """原实现 `body.get("text", "")` 把「缺字段 / text=null」当空串静默清空整块并写进
    审计（乐观锁校验通过后吞掉内容 = 最隐蔽的数据丢失面）。缺省/null/非串 → 400/E4001。"""
    from doc2md.edits import patch_block
    from doc2md.errors import E4001_BadRequest
    save_ir(store, _doc_ir())
    for bad_body in ({"base_revision": 1},                     # 缺 text
                     {"text": None, "base_revision": 1},       # text=null
                     {"text": 123, "base_revision": 1},        # 非字符串
                     {"text": {"a": 1}, "base_revision": 1}):
        with pytest.raises(E4001_BadRequest) as ei:
            patch_block(store, "s5", "b1", bad_body, "tester")
        assert ei.value.detail.get("field") == "text", bad_body
    # 库内容不得被这些非法请求改动
    row = store.q1("SELECT text, revision FROM blocks WHERE doc_id='s5' AND block_id='b1'")
    assert row["text"] == "原始段落1" and row["revision"] == 1


def test_stor6_patch_empty_string_is_valid_explicit_clear(store):
    """空串是合法的「显式清空」——与「缺字段」区分开：显式写空放行，revision 递增。"""
    from doc2md.edits import patch_block
    save_ir(store, _doc_ir())
    out = patch_block(store, "s5", "b1", {"text": "", "base_revision": 1}, "tester")
    assert out["revision"] == 2
    row = store.q1("SELECT text, revision FROM blocks WHERE doc_id='s5' AND block_id='b1'")
    assert row["text"] == "" and row["revision"] == 2


# --- OPS-4（质量）：to_external 内部磁盘守卫（cfg=None 裸 plan 跳过） ----------------
def _tiny_db(tmp_path):
    import sqlite3
    db = tmp_path / "state.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES ('y')")
    con.commit()
    con.close()
    return db


def test_ops4_to_external_internal_disk_guard_blocks(tmp_path, monkeypatch):
    """CH-471 OPS-4：带 cfg 的 plan 让 to_external 自守磁盘——原实现只在 cron 调用点预检，
    直接 to_external（手抄片段 / 未来调用方）会跳过守卫、把外置卷写满留半截容器。
    守卫须在打开写之前触发，故外置目录不留任何 .enc。"""
    from doc2md.errors import E4131_TooLarge
    from doc2md.ops import diskguard
    from doc2md.ops.backup import BackupPlan

    ext = tmp_path / "ext"
    plan = BackupPlan(local_dir=tmp_path / "backups", external_dir=ext,
                      cfg={"perf": {"min_free_mb": 512, "disk_safety_factor": 3}})
    snap = plan.snapshot(_tiny_db(tmp_path))
    monkeypatch.setattr(diskguard, "free_mb", lambda p: 1)     # 外置卷只剩 1MB
    with pytest.raises(E4131_TooLarge):
        plan.to_external(snap, "pw")
    assert list(ext.glob("*.enc")) == []                       # 打开写之前就拒，不留半截


def test_ops4_bare_plan_skips_guard(tmp_path):
    """OPS-4 向后兼容：cfg=None 的裸 plan（纯测试构造）保持原样直写，不触发自守卫。"""
    from doc2md.ops.backup import BackupPlan
    plan = BackupPlan(local_dir=tmp_path / "backups", external_dir=tmp_path / "ext")
    snap = plan.snapshot(_tiny_db(tmp_path))
    out = plan.to_external(snap, "pw")                         # 裸 plan：无 cfg → 不守
    assert out.suffix == ".enc" and out.exists()


def test_ops4_restore_fsyncs_parent_dir(tmp_path, monkeypatch):
    """OPS-4：restore 的「原子 rename」后必须 fsync 父目录——否则掉电可能内容在、目录项
    没落，或 rename 回退。锁定 `_fsync_dir(dst.parent)` 被调用。"""
    from doc2md.ops import backup as bk
    plan = bk.BackupPlan(local_dir=tmp_path / "backups", external_dir=tmp_path / "ext")
    db = _tiny_db(tmp_path)
    enc = plan.to_external(plan.snapshot(db), "pw")
    called: list = []
    monkeypatch.setattr(bk, "_fsync_dir", lambda p: called.append(str(p)))
    dst = tmp_path / "restored.sqlite"
    plan.restore(enc, dst, "pw")
    assert dst.exists()
    assert str(dst.parent) in called, called                    # rename 后 fsync 了目标父目录


# --- STOR-10（质量）：delete_doc 写锁下权威复核，不留伪造 deletions 留痕 -------------
def test_stor10_delete_doc_rechecks_under_write_lock(store):
    """删前存在性快检在写事务之外：若快检「还在」→ BEGIN IMMEDIATE 前被并发删除，原实现
    会「删 0 行却仍写一条 deletions」把「删了不存在的东西」伪装成成功。写锁下的复核必须
    404 回滚且不落留痕。"""
    from doc2md.errors import E4041_NotFound
    from doc2md.storage.repo import delete_doc
    save_ir(store, _doc_ir("gone"))
    orig_q1 = store.q1

    def fake_q1(sql, params=()):
        if sql.strip().startswith("SELECT 1 FROM docs"):
            return {"1": 1}                    # 骗过写锁外的快检，模拟「检到时还在」
        return orig_q1(sql, params)

    with store.tx() as t:                       # 制造竞态终态：进事务时该 docs 行已无
        t.execute("DELETE FROM docs WHERE doc_id='gone'")
    store.q1 = fake_q1
    try:
        with pytest.raises(E4041_NotFound):
            delete_doc(store, "gone", "tester", None)
    finally:
        store.q1 = orig_q1
    assert orig_q1("SELECT 1 c FROM deletions WHERE doc_id='gone'") is None


def test_stor10_tx_rowcount_prefers_cursor_over_changes(store):
    """STOR-10：_Tx.rowcount 原用 connection-global `SELECT changes()`——读写交错会读到
    过期计数。现优先用最近语句游标 rowcount。UPDATE 命中 1 行后即使再跑一条 SELECT，
    rowcount 仍应是该 UPDATE 的 1；未命中的 UPDATE 应为 0 而非上一次的 1。"""
    save_ir(store, _doc_ir("rc"))
    try:
        with store.tx() as tx:
            tx.execute("UPDATE blocks SET revision=revision+1"
                       " WHERE doc_id='rc' AND block_id='b1'")
            tx.execute("SELECT 1 FROM docs WHERE doc_id='rc'")  # 读语句不得污染 rowcount
            assert tx.rowcount == 1
            tx.execute("UPDATE blocks SET revision=revision+1"
                       " WHERE doc_id='rc' AND block_id='nope'")
            assert tx.rowcount == 0                             # 未命中 → 0，不是过期的 1
            raise RuntimeError("abandon this tx so the fixture store stays clean")
    except RuntimeError:
        pass







