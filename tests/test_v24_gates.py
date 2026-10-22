"""tests/test_v24_gates.py — 红线层 6 条 merge-gate（方案 8.1 / 附录 F）

运行：pytest -m gate -q
红线：6 条全绿方可合并；任一回红按方案 8.1 分层处置执行。

对上游蓝本 B-14 的口径调整（登记见方案 第十一、十二章）：
- char_similarity / half_sentence_heads 由 doc2md.pipeline.metrics 提供
  （CH-428 / CH-429：difflib autojunk 对 CJK 失真 + 59 万字逐字符不可用）；
- convert() 的分片开关是 mode="two_phase"（方案 4.3 配置面），非蓝本伪码里的 shard=True；
- 产物 zip 经 Result.zip_path 暴露，非 convert(...).zip；
- B-14 test_edit_optimistic_lock 的审计断言写的是 `audit[-1]["before"] == "改A"`，
  与自身 fixture 顺序矛盾（第一次 PATCH 后只有一条审计，before 应为原文）；
  本实现按 ADR-28 真实语义断言「每条审计的 before 等于 from_revision 的内容」。

CH-456（T8 门禁时长裁剪）：`test_shard_fallback_is_honest` 与
`test_shard_stitch_merges_continuation` 原各跑一次 320 页大样本全量转换
（合计约 44s）。两处的断言集全部与文档大小无关（降级诚实性 / 缝合续接机制），
改用 4 页专用样本 `_mid_sentence_pdf`（每页正文以逗号收尾，同字体跨页续写），
把片界 deterministically 落在句中断续处；`test_shard_two_phase` 的大样本
相似度红线不动。裁剪后红线层单轮约 55s（沙箱实测，见方案 第十二章）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from doc2md import config
from doc2md.errors import WARN_TABLE_ACROSS_SHARD
from doc2md.llm import DEFAULT_OP_WHITELIST
from doc2md.ops.logging import RotatingJsonlWriter
from doc2md.pipeline.convert import convert
from doc2md.pipeline.metrics import char_similarity, half_sentence_heads
from doc2md.security.names import WIN_RESERVED, sanitize_name

pytestmark = [pytest.mark.gate]
GOLDEN = Path(__file__).parent / "golden"

MALICIOUS_NAMES = [
    "../../etc/passwd", "..\\..\\windows\\system32\\cmd.pdf",
    "con.pdf", "CON", "prn.txt", "AUX.doc", "com1", "LPT9.md",
    "bad\x00name.pdf", "ctrl\x01\x02chars.pdf",
    "a" * 200 + ".pdf", "\ud800lone.pdf",
]


def _mid_sentence_pdf(tmp_path, pages: int = 4) -> Path:
    """跨片续接专用样本（CH-456 / T8）：每页前 9 行是完整句子（句号收尾），
    第 10 行是 2 字段续接（逗号收尾、同字体同字号）→ 片界必然落在句中断续处，
    缝合判定确定触发；且每缝被吸收的文本仅 2 字，similarity 红线不受样本规模影响。
    不设标题行：片首块须与上一片片尾块同字号，16pt 标题会挡掉续接判定。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    p = tmp_path / "mid_sentence.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    _w, height = A4
    for page in range(pages):
        c.setFont("STSong-Light", 10.5)
        y = height - 4 * cm
        for i in range(9):
            # 每句唯一：重复文本会让 difflib 行对齐退化，similarity 红线失真
            c.drawString(2 * cm, y,
                         f"第{page + 1}页第{i + 1}个完整句子，句末以句号收尾，内容足够长。")
            y -= 0.6 * cm
        c.drawString(2 * cm, y, "续，")
        c.showPage()
    c.save()
    return p


# --- 1. shard_two_phase（FR-126 再修订 / M4M6）--------------------------------
def test_shard_two_phase(large_single, large_sharded):
    sim = char_similarity(large_single.markdown, large_sharded.markdown)
    st = large_sharded.meta["shard_stats"]
    assert sim >= 0.99, f"分片 vs 单片相似度 {sim} < 0.99"
    assert not half_sentence_heads(large_sharded)
    assert large_sharded.meta["sharded"] is True
    assert large_sharded.meta["shard_count"] >= 2
    # 分片确实发生（非降级伪通过，方案 4.3）
    assert not st.get("fallback"), f"降级单片，非真分片：{st}"
    assert st["shards"] >= 2 and st["blocks_raw"] > st["blocks_after_dedupe"]
    # CH-469：重叠区不得在产物里留下多余块——分片路径去重后的块数必须与单片路径
    # 相等。char_similarity ≥ 0.99 允许约 210 字的误差，一份重复段落（实测 101 字）
    # 能在红线下静默通过，块数相等才是可判定的硬口径。
    assert st["blocks_after_dedupe"] == len(large_single.ir.blocks), \
        f"重叠区残留多余块：分片 {st['blocks_after_dedupe']} vs 单片 {len(large_single.ir.blocks)}"
    # shard_edge 可筛选：报告有边界行 + data 属性
    # CH-464：拆掉 or 逃逸——缝合发生时 shard_edge 必须可筛选
    if st["seams"] > 0:
        assert any(b.flags.get("shard_edge") for b in large_sharded.ir.blocks)


def test_shard_edge_filterable_in_report(large_sharded):
    from doc2md.pipeline.convert import render_report
    html = render_report(large_sharded.ir, large_sharded.meta)
    assert 'data-shard-edge="1"' in html or "edge-1" in html
    assert "只看分片边界块" in html
    assert "本文为分片转换" in json.dumps(large_sharded.meta, ensure_ascii=False)


def test_shard_stitch_merges_continuation(tmp_path, cfg):
    """缝合真发生：挑一个片界落在半句处的片大小，断言 stitched ≥ 1 + absorbed_ids 留痕。

    CH-456（T8）：改测 4 页专用样本（`_mid_sentence_pdf`，片界必然落在句中断续处），
    不再跑 320 页大样本全量转换；相似度基线用同一样本的 single 模式（断言强度不变）。
    """
    import copy
    sample = _mid_sentence_pdf(tmp_path)
    c = copy.deepcopy(cfg)
    c["s2"]["max_pages_per_shard"] = 1          # 每页一片，片界 = 页界
    c["s2"]["shard_overlap_pages"] = 0
    single = convert(sample, cfg=c, mode="single")
    r = convert(sample, cfg=c, mode="two_phase")
    st = r.meta["shard_stats"]
    assert st["shards"] >= 2
    assert st["stitched"] >= 1, f"未发生缝合续接：{st}"
    absorbed = [b for b in r.ir.blocks if b.flags.get("absorbed_ids")]
    assert absorbed, "缝合未记 absorbed_ids"
    assert any("s4:shard_stitch" in (b.provenance or {}).get("modified_by", [])
               for b in absorbed)
    assert char_similarity(single.markdown, r.markdown) >= 0.99


def test_shard_overlap_dedupe_no_duplicate_ids(large_sharded):
    """CH-426：各片块 id 独立从 b1 起，合并必须统一编号，否则 PATCH/FTS 命中错块。"""
    ids = [b.id for b in large_sharded.ir.blocks]
    assert len(ids) == len(set(ids)), "分片合并后存在重复 block_id"


def test_shard_pages_not_double_counted(large_single, large_sharded):
    """CH-438：重叠页不得计入总页数（各片 pages 求和会让 320 页样本变 322）。"""
    assert large_sharded.ir.pages == large_single.ir.pages == 320
    st = large_sharded.meta["shard_stats"]
    assert st["shards"] >= 2 and st["dropped_dupes"] > 0


def test_shard_fallback_is_honest(tmp_path, cfg, monkeypatch):
    """方案 4.3：缝合异常降级单片，且不得把降级产物标成 sharded=True。

    CH-456（T8）：改测 4 页专用样本（每页一片强制 ≥2 片后触发缝合），
    断言集与文档大小无关；不再跑 320 页大样本全量转换。
    """
    import copy
    import importlib
    cv = importlib.import_module("doc2md.pipeline.convert")   # 包属性 convert 已被函数遮蔽

    def boom(*a, **k):
        raise RuntimeError("synthetic stitch crash")

    # convert.py 以 `from .sharding import stitch_shards` 绑定名字 → 必须打 convert 模块的引用
    monkeypatch.setattr(cv, "stitch_shards", boom)
    sample = _mid_sentence_pdf(tmp_path)
    c = copy.deepcopy(cfg)
    c["s2"]["max_pages_per_shard"] = 1
    c["s2"]["shard_overlap_pages"] = 0
    r = cv.convert(sample, cfg=c, mode="two_phase")
    assert r.meta["shard_stats"].get("fallback") is True
    assert r.meta["sharded"] is False and r.meta["shard_count"] == 1
    assert WARN_TABLE_ACROSS_SHARD in r.meta["warnings"]


# --- 2. malicious_filenames（FR-127 / FR-129 / M3）---------------------------
@pytest.mark.parametrize("raw", MALICIOUS_NAMES)
def test_malicious_filenames(raw):
    out = sanitize_name(raw)
    assert ".." not in out and "/" not in out and "\\" not in out
    assert len(out.encode("utf-8")) <= 128
    assert out.rsplit(".", 1)[0].lower() not in WIN_RESERVED
    assert out.strip() and out.strip(" .") == out
    assert "\x00" not in out and not any(ord(ch) < 0x20 for ch in out)


def test_malicious_names_manifest(names_manifest):
    """黄金清单逐条比对（tests/golden/names/malicious_names.json）。"""
    for item in names_manifest:
        got = sanitize_name(item["input"])
        assert got == item["expected"], (
            f'{item["description"]}: {item["input"]!r} → {got!r} ≠ {item["expected"]!r}')


def test_product_zip_entries(large_zip_names):
    for name in large_zip_names:
        assert ".." not in name and not name.startswith(("/", "\\"))
        assert not Path(name).is_absolute()


def test_zip_rejects_unsafe_source_name(tmp_path, small_pdf, cfg):
    """打包双闸：越权名不得进 zip 条目。"""
    from doc2md.errors import E4003_FileNameUnsafe
    from doc2md.ir import IR
    from doc2md.pipeline.convert import package
    ir = IR(doc_id="d1", source_key="s1", source_name="../evil.md", format="pdf", pages=1)
    with pytest.raises(E4003_FileNameUnsafe):
        package(ir, "x", {}, tmp_path, cfg)


def test_dirguard_symlink_loop_bounded(golden_dir):
    """符号链接循环目录：10s 内扫完且不跟随（验收 M3）。"""
    import time

    from doc2md.security.dirguard import WalkLimits, guard_walk
    root = golden_dir / "dirs" / "symlink_loop"
    if not root.is_dir():
        # CH-473（TEST-1，MED）：gate 层缺失必须硬失败，不得静默 skip——
        # 否则未生成语料的机器上 gate 假绿。口径同 conftest._missing。
        msg = ("golden sample missing: dirs/symlink_loop — run "
               "`.venv/bin/python tests/make_golden.py` (或设 "
               "DOC2MD_ALLOW_MISSING_GOLDEN=1 降级为 skip)")
        if os.environ.get("DOC2MD_ALLOW_MISSING_GOLDEN") == "1":
            pytest.skip(msg)
        pytest.fail(msg)
    if os.name == "nt" and not any((root / n).is_symlink() for n in ("A", "B")):
        pytest.skip("Windows checkout without symlink support (core.symlinks=false)")
    t0 = time.monotonic()
    res = guard_walk(root, WalkLimits(follow_symlinks=False))
    assert time.monotonic() - t0 < 10
    assert res.skipped_symlinks >= 1 or not res.files


def test_dirguard_too_many_files_raises(golden_dir):
    from doc2md.errors import E4133_DirTooWide
    from doc2md.security.dirguard import WalkLimits, guard_walk
    root = golden_dir / "dirs" / "many"
    if not root.is_dir():
        # CH-473（TEST-1，MED）：同 symlink_loop——gate 层硬失败
        msg = ("golden sample missing: dirs/many — run "
               "`.venv/bin/python tests/make_golden.py` (或设 "
               "DOC2MD_ALLOW_MISSING_GOLDEN=1 降级为 skip)")
        if os.environ.get("DOC2MD_ALLOW_MISSING_GOLDEN") == "1":
            pytest.skip(msg)
        pytest.fail(msg)
    with pytest.raises(E4133_DirTooWide):
        guard_walk(root, WalkLimits(max_files_per_dir=10, deadline_seconds=30))


def test_watch_stability_fake_clock(tmp_path):
    """FR-111：size+mtime 静默 5s 才入队（可注入时钟单测）。"""
    from doc2md.security.watcher import StabilityTracker
    f = tmp_path / "a.pdf"
    f.write_bytes(b"partial")
    tr = StabilityTracker(stable_seconds=5)
    assert tr.observe(f, now=100.0) is False          # 首次只登记
    f.write_bytes(b"partial-data-longer")
    assert tr.observe(f, now=101.0) is False          # 变化 → 窗口重置
    assert tr.observe(f, now=103.0) is False          # 距重置仅 2s
    assert tr.observe(f, now=106.5) is True           # 静默 ≥5s
    assert tr.observe(f, now=107.0) is False          # 已入队，不重复


# --- 3. injection_resistance（ADR-29 / M4M6）--------------------------------
_INJ_CACHE: dict[str, object] = {}


def _cached_convert(path, cfg):
    """CH-462（A-16）：同一注入语料在三个测试函数间共享转换结果——
    原实现每个测试各 convert 一遍，同一批语料被重复转换约 33 次（纯测试耗时）。"""
    key = str(path)
    if key not in _INJ_CACHE:
        _INJ_CACHE[key] = convert(path, cfg=cfg)
    return _INJ_CACHE[key]


def test_injection_resistance(cfg, golden_dir):
    cln = sorted((golden_dir / "clean").glob("*.pdf"))
    inj = sorted((golden_dir / "injected").glob("*.pdf"))
    assert len(inj) >= 10 and len(cln) >= 10
    wl = set(config.get(cfg, "llm.op_whitelist", DEFAULT_OP_WHITELIST))
    for c_path, i_path in zip(cln, inj):
        c = _cached_convert(c_path, cfg)
        i = _cached_convert(i_path, cfg)
        assert abs(c.metrics["f1"] - i.metrics["f1"]) <= 0.005, (
            f"{c_path.name} vs {i_path.name} 指标差超限")
        assert all(op in wl for op in i.applied_ops), "白名单外 op 被应用"


def test_injection_rejection_attributed(cfg, golden_dir):
    """方案 8.1（injection_resistance）：越权 op 被拒时可区分「模型胡话」与「注入成功」。"""
    inj = sorted((golden_dir / "injected").glob("*.pdf"))
    hits = 0
    for p in inj:
        r = _cached_convert(p, cfg)
        for rej in r.rejected_ops:
            assert rej["attribution"] in ("injection_candidate", "model_hallucination")
        if any(rej["attribution"] == "injection_candidate" for rej in r.rejected_ops):
            hits += 1
    assert hits >= 1, "全部注入样本都没产生 injection_candidate 归因，防线可观测性失效"


def test_injection_no_nonwhitelist_op_applied(cfg, golden_dir, tmp_path):
    """探针口径：把「越权 op」强灌进 LLM 输出，断言应用数为 0。"""
    inj = sorted((golden_dir / "injected").glob("*.pdf"))
    for p in inj[:3]:
        r = _cached_convert(p, cfg)
        wl = set(config.get(cfg, "llm.op_whitelist", DEFAULT_OP_WHITELIST))
        assert not (set(r.applied_ops) - wl)


# --- 4. edit_optimistic_lock（FR-203 修订 / M8）------------------------------
def test_edit_optimistic_lock(saved_doc):
    client, tokens, doc = saved_doc
    blocks = client.get(f"/docs/{doc}", headers=_tokens_h(tokens)).get_json()["blocks"]
    bid = blocks[0]["id"]
    original = blocks[0]["text"]

    r1 = client.patch(f"/docs/{doc}/blocks/{bid}",
                      json={"text": "改A", "base_revision": 1}, headers=_tokens_h(tokens, "editor"))
    r2 = client.patch(f"/docs/{doc}/blocks/{bid}",
                      json={"text": "改B", "base_revision": 1}, headers=_tokens_h(tokens, "editor"))
    assert r1.status_code == 200, r1.get_json()
    assert r2.status_code == 425, r2.get_json()
    body = r2.get_json()
    assert body["code"] == "E4251"
    assert body["detail"]["current_revision"] == 2
    # 数据未被覆盖
    after = client.get(f"/docs/{doc}", headers=_tokens_h(tokens)).get_json()["blocks"]
    assert next(b for b in after if b["id"] == bid)["text"] == "改A"
    # 审计：每条 before 与 from_revision 严格对应
    audit = client.get(f"/docs/{doc}/audit", headers=_tokens_h(tokens, "editor")).get_json()["audit"]
    assert audit, "无审计流水"
    assert audit[-1]["before"] == original
    assert audit[-1]["from_revision"] == 1 and audit[-1]["to_revision"] == 2
    # 缺 base_revision → 400
    r3 = client.patch(f"/docs/{doc}/blocks/{bid}", json={"text": "改C"},
                      headers=_tokens_h(tokens, "editor"))
    assert r3.status_code == 400 and r3.get_json()["code"] == "E4001"


def test_edit_gray_release_optional(cfg, store):
    """方案 8.5 灰度：require_base_revision=False 时缺省沿用旧行为 + 告警。"""
    from doc2md.edits import patch_block
    from doc2md.ir import IR, Block
    ir = IR(doc_id="gx", source_key="gxk", source_name="a.pdf", format="pdf", pages=1,
            blocks=[Block(id="b1", type="paragraph", text="原文", page=0)])
    from doc2md.storage.repo import save_ir
    save_ir(store, ir)
    res = patch_block(store, "gx", "b1", {"text": "新值"}, "tester",
                      require_base_revision=False)
    assert res["revision"] == 2 and "warning" in res


def test_reader_cannot_edit(api_client, small_pdf):
    client, tokens = api_client
    r = client.post("/jobs", json={"path": str(small_pdf)}, headers=_tokens_h(tokens, "editor"))
    doc = r.get_json()["jobs"][0]["doc_id"]
    assert client.patch(f"/docs/{doc}/blocks/b1",
                        json={"text": "x", "base_revision": 1},
                        headers=_tokens_h(tokens, "reader")).status_code == 403
    assert client.post("/jobs", json={"path": str(small_pdf)},
                       headers=_tokens_h(tokens, "reader")).status_code == 403
    assert client.delete(f"/docs/{doc}?confirm=true",
                         headers=_tokens_h(tokens, "editor")).status_code == 403
    assert client.put("/settings", json={"s2": {"mode": "single"}},
                      headers=_tokens_h(tokens, "editor")).status_code == 403
    assert client.get("/settings", headers=_tokens_h(tokens, "reader")).status_code == 200


# --- 5. delete_cascade（FR-128 / M8 + DoD）-----------------------------------
def test_delete_cascade(saved_doc):
    client, tokens, doc = saved_doc
    # 先确保可搜
    blocks = client.get(f"/docs/{doc}", headers=_tokens_h(tokens)).get_json()["blocks"]
    q = (blocks[0]["text"] or "")[:6]
    r0 = client.delete(f"/docs/{doc}", headers=_tokens_h(tokens, "admin"))
    assert r0.status_code == 400 and r0.get_json()["code"] == "E4001"   # 附录 E：缺 confirm → 400
    r = client.delete(f"/docs/{doc}?confirm=true", headers=_tokens_h(tokens, "admin"))
    assert r.status_code == 200, r.get_json()
    assert client.get(f"/docs/{doc}", headers=_tokens_h(tokens)).status_code == 404
    assert client.get(f"/search?q=%22{q}%22",
                      headers=_tokens_h(tokens)).get_json()["total"] == 0
    tomb = client.get("/audit/deletions", headers=_tokens_h(tokens, "admin")).get_json()["deletions"]
    assert tomb and tomb[-1]["doc_id"] == doc
    assert "before" not in tomb[-1] and "after" not in tomb[-1]
    assert "text" not in json.dumps(tomb[-1], ensure_ascii=False)


def test_delete_removes_artifacts(saved_doc):
    client, tokens, doc = saved_doc
    r = client.delete(f"/docs/{doc}?confirm=true", headers=_tokens_h(tokens, "admin"))
    assert r.get_json()["files"], "产物文件未被级联清除"


# --- 6. log_rotation（NFR-19 / M8）-------------------------------------------
def _svc(tmp_path, tmp_cfg):
    """真起一个服务实例（create_app），返回 (app, client, root)。"""
    from doc2md.api import create_app
    root = tmp_path / "work"
    app = create_app(tmp_cfg, root)
    return app, app.test_client(), root


def test_log_rotation(tmp_path):
    w = RotatingJsonlWriter(tmp_path, rotate_mb=50, rotate_keep=5)
    w.rotate_bytes = 2048                          # 测试用小阈值
    for i in range(400):
        w.write({"i": i, "msg": "x" * 60})
    w.close()
    rotated = sorted(tmp_path.glob("app.jsonl.*"))
    assert [p.name for p in rotated] == [f"app.jsonl.{k}" for k in range(1, 6)]
    first = (tmp_path / "app.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(first)["code"] == "E_LOG_ROTATE"


def test_service_rotates_its_own_logs_without_restart(tmp_path, tmp_cfg, small_pdf):
    """红线字面口径：轮转发生在**服务进程内**且服务不重启（方案 6.5）。

    旧用例只单测写入器，服务侧从未持有写入器（CH-444 修的就是这一点），
    所以「注入 >50MB 日志自动轮转」在真实服务面上是伪覆盖。
    """
    app, client, root = _svc(tmp_path, tmp_cfg)
    w = app.config["doc2md"]["writer"]
    # 服务启动即自带一条 SERVICE_START
    first = w._path.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(first)["code"] == "SERVICE_START"
    w.rotate_bytes = 1024                          # 小阈值模拟 >50MB
    for i in range(80):
        w.write({"i": i, "msg": "z" * 40})         # 全程不 close、不重建服务
    assert w.rotations >= 1
    rotated = sorted(root.glob("logs/app.jsonl.*"))
    assert rotated, "服务内未产生轮转文件"
    # 轮转后服务仍在同一实例上可用（未重启）
    from doc2md import auth
    st = app.config["doc2md"]["store"]
    tok = auth.issue(st, "editor", "gate")
    r = client.post("/jobs", json={"path": str(small_pdf)},
                    headers={"X-Doc2md-Token": tok})
    assert r.status_code == 202, r.get_json()
    assert client.get("/healthz").status_code == 200
    w.close()


def test_service_business_events_land_in_logs(tmp_path, tmp_cfg, small_pdf):
    """NFR-19 内置执行的另一半：事件除入库外必须进服务日志，且不含正文（NFR-20）。"""
    from doc2md import auth
    app, client, root = _svc(tmp_path, tmp_cfg)
    st = app.config["doc2md"]["store"]
    editor = auth.issue(st, "editor", "log")
    h = {"X-Doc2md-Token": editor}
    doc = client.post("/jobs", json={"path": str(small_pdf)}, headers=h) \
                 .get_json()["jobs"][0]["doc_id"]
    bid = client.get(f"/docs/{doc}", headers=h).get_json()["blocks"][0]["id"]
    secret = "本条款涉及例外情况" * 12
    client.patch(f"/docs/{doc}/blocks/{bid}",
                 json={"text": secret, "base_revision": 1}, headers=h)
    app.config["doc2md"]["writer"].close()
    dumped = (root / "logs" / "app.jsonl").read_text(encoding="utf-8")
    codes = [json.loads(ln)["code"] for ln in dumped.splitlines()]
    assert "job_done" in codes and "text_edited" in codes
    assert "本条款" not in dumped, "NFR-20：正文片段进了服务日志"


def test_service_start_sweeps_work_tmp(tmp_path, tmp_cfg):
    """方案 6.5：服务启动时清理 work/tmp 残留（此前只有 cron 做，服务面从未执行）。

    CH-473（STOR-4）：sweep_tmp 加在途保护窗——mtime 在 perf.stale_job_minutes
    （默认 30）内的条目按在途处理、跳过。残留物模拟「上个进程崩掉留下的」：
    把 mtime 推到窗口外（进程已死、不会再写入），启动清扫照清。
    """
    import os
    import time
    tmp = Path(tmp_cfg["paths"]["tmp"])
    (tmp / "stale").mkdir(parents=True)
    (tmp / "stale" / "x.bin").write_bytes(b"old")
    (tmp / "leftover.tmp").write_bytes(b"old")
    old = time.time() - 3600
    os.utime(tmp / "stale", (old, old))
    os.utime(tmp / "leftover.tmp", (old, old))
    app, _, _ = _svc(tmp_path, tmp_cfg)
    assert not tmp.exists() or list(tmp.iterdir()) == [], "work/tmp 残留未被启动清理"
    app.config["doc2md"]["writer"].close()


def test_cron_and_service_do_not_share_one_log_file(tmp_path, tmp_cfg, cfg):
    """CH-444：serve 与 cron 分离进程，各自重命名轮转同名 app.jsonl 会互相踩。"""
    from doc2md import cron
    app, _, root = _svc(tmp_path, tmp_cfg)
    app.config["doc2md"]["writer"].close()
    app_file = root / "logs" / "app.jsonl"
    before = app_file.read_bytes()
    assert before, "服务日志应已存在"
    rep = cron.run(tmp_cfg, root=root, mode="daily")
    assert rep.ok, rep.steps
    assert app_file.read_bytes() == before, "cron 动了服务的活动日志"
    assert (root / "logs" / "cron.jsonl").is_file(), "cron 应写自己的日志文件"


def test_service_honours_rotate_config(tmp_path, cfg):
    """写入器阈值来自 [logs]（服务侧同样生效，不只是 cron 侧）。"""
    import copy
    c = copy.deepcopy(cfg)
    c["paths"] = dict(cfg.get("paths", {}))
    c["paths"].update({"root": str(tmp_path / "work"), "out": str(tmp_path / "work/out"),
                       "reports": str(tmp_path / "work/reports"),
                       "logs": str(tmp_path / "work/logs"),
                       "tmp": str(tmp_path / "work/tmp")})
    c["logs"] = {"rotate_mb": 0.001, "rotate_keep": 2}
    app, _, _ = _svc(tmp_path, c)
    w = app.config["doc2md"]["writer"]
    assert w.rotate_keep == 2 and w.rotate_bytes < 4096
    w.close()


def test_log_rotation_uses_inmemory_counter(tmp_path):
    """内存计数字节口径：缓冲未落盘也必须按阈值轮转（源文档实测 stat() 放大 4 倍）。"""
    w = RotatingJsonlWriter(tmp_path, rotate_mb=50, rotate_keep=5)
    w.rotate_bytes = 4096
    for i in range(120):
        w.write({"i": i, "msg": "y" * 80})         # ~12KB，全程未 flush
    assert w.rotations >= 1, "未 flush 时 stat() 口径会漏轮转"
    w.close()


def test_logs_contain_no_document_text(tmp_path):
    """NFR-20：正文片段不得进日志，长文本字段落 sha256 指纹。"""
    w = RotatingJsonlWriter(tmp_path, rotate_mb=50, rotate_keep=5)
    secret = "本条款涉及例外情况" * 20
    w.write({"code": "TEST", "text": secret, "before": secret})
    w.close()
    dumped = (tmp_path / "app.jsonl").read_text(encoding="utf-8")
    assert "本条款" not in dumped
    assert "<sha256:" in dumped


def test_gate_test_env_neutralization():
    """O-02 门禁：测试环境中立化三件套在位（代理清理 / basetemp 注入 / 沙箱探测）。

    把「环境中立化」本身变成红线：夹具被删或改名时本断言立即红，而不是等
    某天 T2 又红了才人工归因。探测函数的返回值因宿主而异（干净机全 False），
    红线钉的是**机制在位**，不是「当前宿主受限」——故不断言具体布尔值。
    """
    import conftest as qa_conftest
    assert callable(qa_conftest._brokered_mkdir_denied)
    assert callable(qa_conftest._rss_sampling_denied)
    assert hasattr(qa_conftest, "_neutral_proxy_env")    # autouse 代理清理夹具
    assert hasattr(qa_conftest, "pytest_configure")      # basetemp 注入钩子
    # 夹具效应：本用例自身就在 autouse 夹具作用下，进程内不应残留代理变量
    # （夹具缺失 + 宿主带代理时本条即红；宿主干净时由上面的在位断言兜底）
    for k in qa_conftest._PROXY_VARS:
        assert k not in os.environ, f"{k} leaked into test process"


def test_gate_wrap_space_preserved():
    """O-01 红线（C-04）：折行落在空格上时，空格必须凭 S1 几何证据补回。

    两段断言：
    ① 定向样本 `pdf_11_wrap_space_1p` 的六种形态逐一核对（行尾证据 /
       行首证据 × 中西文、英文单词、数字汉字边界 + 无空格反例）；
    ② `pdf_09_complex_long_8p`（C-04 原始缺陷样本）22/22 全保留，
       且全语料 `expected/*.md` 的粘滞形态（"数字2026"）零出现。
    语料逐字节比对由 test_corpus_expected.py 负责，这里钉的是**语义**红线。
    """
    corpus = Path(__file__).parent / "corpus" / "expected"
    md = (corpus / "pdf_11_wrap_space_1p.md").read_text(encoding="utf-8")
    assert "覆盖中文与数字 2026 和标点的边界" in md        # A：行尾证据
    assert "还包括中文与数字 2026 同年发布的文本" in md    # B：行首证据
    assert "中英混排时中文与 English 单词之间的空格" in md  # C：中文→英文
    assert "以及 English 到中文的 反向边界同样保留" in md   # D：英文→中文
    assert "还包括 2026 年之后的数字汉字边界" in md        # E：数字→中文
    assert "反例里中西文直接相接的片段原文没有空格" in md  # F：反例不插空格
    t = (corpus / "pdf_09_complex_long_8p.md").read_text(encoding="utf-8")
    assert t.count("数字2026") == 0
    assert t.count("数字 2026") == 22, "pdf_09 的 22 处折行空格必须全部保留"
    glued = sum(f.read_text(encoding="utf-8").count("数字2026")
                for f in corpus.glob("*.md"))
    assert glued == 0, f"折行空格被吃掉：粘滞形态 {glued} 处"


def test_gate_batch_serial_row_isomorphic(tmp_path, cfg):
    """O-03 门禁（C-21）：同一输入，批量工人与串行路径的结果行
    共有字段键集与取值类型一致（值可比对）。

    两条出口此前各自组装行字典（串行缺 source_key、批量缺顶层
    sharded/shard_stats），下游脚本要写两套解析。收敛到
    `batch.result_row` 单点后，本红线钉住同构性不被再次打破。
    批量独有字段（退出码聚合等）不在共有集内——同构指共有字段。
    """
    from doc2md.pipeline.batch import _convert_one, result_row, run_batch
    from doc2md.pipeline.convert import convert

    src = GOLDEN / "small_3p.pdf"
    out = tmp_path / "out"
    res = convert(src, cfg=cfg, out_dir=out)
    serial_row = result_row(src, res)                       # 串行单点
    batch_row = _convert_one(str(src), cfg, None, str(out))  # 批量 worker 入口
    common = {"file", "status", "doc_id", "source_key", "blocks", "chars",
              "zip", "sharded", "shard_stats", "meta"}
    assert set(serial_row) == common, f"串行行键集漂移：{set(serial_row) ^ common}"
    assert set(batch_row) == common, f"批量行键集漂移：{set(batch_row) ^ common}"
    for k in sorted(common):
        assert type(serial_row[k]) is type(batch_row[k]), f"{k} 类型不一致"
        if k != "file":                     # file 记调用方传入路径，不逐字比对
            assert serial_row[k] == batch_row[k], f"{k} 取值不一致"
    assert isinstance(serial_row["sharded"], bool), "sharded 必须恒为布尔"
    assert isinstance(serial_row["shard_stats"], dict)
    # 进程池出口（真起子进程）与串行单点同构
    pool_rows = run_batch([src], cfg=cfg, out_dir=tmp_path / "pool", workers=2)
    assert len(pool_rows) == 1 and set(pool_rows[0]) == common
    assert pool_rows[0]["sharded"] is False


def test_gate_tight_list_no_blank_gap():
    """O-04 红线（C-05）：连续列表项之间不得有空行（tight list）。

    手写 Markdown 的列表项是单换行相接；渲染层若用空行连接，Obsidian/VS Code
    等会渲染成松散列表（项与项之间带段间距）。段落→列表、列表→段落等块界
    仍保留空行（Markdown 需要空行打断段落），不在本红线内。
    """
    import re
    corpus = Path(__file__).parent / "corpus" / "expected"
    loose = re.compile(r"(?m)^\s*(?:- |\d+\. ).*\n\n\s*(?:- |\d+\. )")
    hits = [f.name for f in sorted(corpus.glob("*.md"))
            if loose.search(f.read_text(encoding="utf-8"))]
    assert not hits, f"列表项之间出现空行：{hits}"


def test_gate_docs_list_show_match_db(saved_doc, tmp_path, capsys):
    """O-07 红线（C-26）：CLI 只读面与库事实一致。

    `docs list` 条数 == `SELECT COUNT(*) FROM docs`；`docs show` 的
    blocks_total 与逐篇块数一致。读面谎报比没有读面更坏——脚本按条数对账
    时会静默少算，且错在「看起来成功」的那一侧。
    """
    from doc2md.cli import main
    from doc2md.storage.db import Store
    _client, _tokens, doc_id = saved_doc
    root = tmp_path / "work"
    assert main(["docs", "list", "--root", str(root)]) == 0
    body = json.loads(capsys.readouterr().out)
    s = Store(root / "state.sqlite")
    try:
        n = s.q1("SELECT COUNT(*) AS c FROM docs")["c"]
        nb = s.q1("SELECT COUNT(*) AS c FROM blocks WHERE doc_id=?",
                  (doc_id,))["c"]
    finally:
        s.close()
    assert body["total"] == n == len(body["docs"]) >= 1
    assert main(["docs", "show", doc_id, "--root", str(root),
                 "--limit", "500"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["blocks_total"] == nb == len(d["blocks"])


def test_gate_docs_export_bytes_equal_zip(saved_doc, tmp_path, capsys):
    """O-07 红线（F-01）：`docs export` 与 zip 内 content.md 逐字节相同，
    manifest 的 sha256 与实文件一致（对账面不许自己编一个哈希）。"""
    import hashlib
    import zipfile

    from doc2md.artifacts import artifact_entry
    from doc2md.cli import main
    _client, _tokens, doc_id = saved_doc
    root = tmp_path / "work"
    exp = tmp_path / "gate-export"
    assert main(["docs", "export", doc_id, "--root", str(root),
                 "--out", str(exp)]) == 0
    capsys.readouterr()
    with zipfile.ZipFile(root / "out" / f"{doc_id}.zip") as z:
        want = z.read(artifact_entry(z.namelist(), "content.md", doc_id))
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    entry = man["docs"][0]
    got = (exp / entry["file"]).read_bytes()
    assert got == want, "导出字节必须与 zip 内 content.md 逐字节相同"
    assert entry["sha256"] == hashlib.sha256(got).hexdigest()


def test_gate_docs_export_rejects_traversal_doc_id(saved_doc, tmp_path, capsys):
    """O-07 红线（FR-127）：恶意 doc_id 走同一消毒闸。

    doc_id 进 `out_dir/<doc_id>.zip` 路径拼接之前先过 `assert_name_safe`：
    doc_id 正常形态是内容哈希（十六进制），穿越形态必然是恶意输入——拒收
    且库外零落盘（`tmp_path/etc` 不得出现）。
    """
    from doc2md.cli import main
    root = tmp_path / "work"
    exp = tmp_path / "gate-export"
    rc = main(["docs", "export", "../../etc/passwd", "--root", str(root),
               "--out", str(exp)])
    assert rc == 4
    assert "E4003" in capsys.readouterr().err
    assert not (tmp_path / "etc").exists(), "路径穿越不得在库外落文件"
    assert not list(exp.glob("*.md")), "失败篇不得留下半截 md"


def _o08_ingest(root, src):
    """O-08 测试共用：转换 + 入库（CLI convert 不写库，入库口径同 watch）。"""
    from doc2md.pipeline.convert import convert
    from doc2md.storage.db import Store
    from doc2md.storage.repo import save_ir
    res = convert(src, out_dir=root / "out")
    s = Store(root / "state.sqlite")
    try:
        save_ir(s, res.ir, meta=res.meta, gate=res.gate)
    finally:
        s.close()
    return res


def test_gate_skip_existing_artifacts_untouched(tmp_path, small_pdf, capsys):
    """O-08 红线（C-27）：`--skip-existing` 全跳过、退出码 0、既有产物
    （sha256）逐字节不变——「跳过」不得以任何方式变成「重写」。"""
    import hashlib

    from doc2md.cli import main
    root = tmp_path / "work"
    src = tmp_path / "gate.pdf"
    src.write_bytes(small_pdf.read_bytes())
    _o08_ingest(root, src)
    zips = list((root / "out").glob("*.zip"))
    assert len(zips) == 1
    before = hashlib.sha256(zips[0].read_bytes()).hexdigest()
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "skipped" and row["blocks"] == 0
    assert hashlib.sha256(zips[0].read_bytes()).hexdigest() == before


def test_gate_skip_existing_changed_source_converts(tmp_path, small_pdf,
                                                    capsys):
    """O-08 红线（C-27 边界）：源文件改写 → 内容哈希变 → 不得误跳过。
    内容寻址是跳过判据的全部依据，「未变才跳」必须被红线钉住。"""
    from doc2md.cli import main
    root = tmp_path / "work"
    src = tmp_path / "mutable.pdf"
    src.write_bytes(small_pdf.read_bytes())
    _o08_ingest(root, src)
    src.write_bytes(src.read_bytes() + b"\n% appended page note\n")
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "ok" and row["blocks"] > 0, "内容已变不得跳过"


def test_gate_watch_restart_zero_reconversion(tmp_path, capsys):
    """O-08 红线（F-07）：watch 进程重启后对未变更目录零转换。

    进程内判据（CH-464 StabilityTracker）管「同进程不重复」，本红线管
    「重启不重转」：第二遍 `watch --once`（全新 Tracker）必须全 skipped，
    产物 sha 与 docs 行数都不变。修复前每个新进程首轮把老文件全部重转。
    """
    import hashlib
    import os

    from doc2md.cli import main
    from doc2md.storage.db import Store
    root = tmp_path / "work"
    drop = tmp_path / "gdrop"
    drop.mkdir()
    src = drop / "watched.pdf"
    src.write_bytes((Path(__file__).parent / "golden" / "small_3p.pdf").read_bytes())
    old = src.stat().st_mtime - 60
    os.utime(src, (old, old))
    assert main(["watch", str(drop), "--once", "--root", str(root)]) == 0
    capsys.readouterr()
    zips = list((root / "out").glob("*.zip"))
    assert len(zips) == 1
    before = hashlib.sha256(zips[0].read_bytes()).hexdigest()
    s = Store(root / "state.sqlite")
    try:
        n_docs = s.q1("SELECT COUNT(*) AS c FROM docs")["c"]
    finally:
        s.close()
    assert n_docs == 1
    # 第二遍（重启语义）：零转换
    assert main(["watch", str(drop), "--once", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert '"status": "skipped"' in out
    assert hashlib.sha256(zips[0].read_bytes()).hexdigest() == before
    s = Store(root / "state.sqlite")
    try:
        assert s.q1("SELECT COUNT(*) AS c FROM docs")["c"] == n_docs
    finally:
        s.close()


# --- helpers ------------------------------------------------------------------
def _tokens_h(tokens: dict, role: str = "admin") -> dict:
    return {"X-Doc2md-Token": tokens[role]}
