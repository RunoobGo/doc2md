"""低配机吞吐与有界内存（CH-447 … CH-451）—— 新增红线断言。

动因：外部《Python PDF 技术栈对比》方案的结论落到本项目可执行的部分——
① L1 规则层不引入模型（本项目 v2.4 范围本就排除 OCR），因此不需要 GPU；
② Python 的 GIL 决定「多文件」必须进程级并行；
③ 大文档的驻留内存要在 8GB 级 Win11 上可控（不是「多占几个 GB」了事）。

口径纪律：全部断言以「产物逐条目内容等价」为准——性能改动不得改变任何一个字节，
等价性另有离线三态复核（HEAD 版 / 分块关 / 分块开）留档。

运行：pytest -m gate -q（并入红线层，方案 8.1 / 附录 F）
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from doc2md import config
from doc2md.errors import E4131_TooLarge, E4151_UnsupportedType

pytestmark = [pytest.mark.gate, pytest.mark.milestone("PERF")]
GOLDEN = Path(__file__).parent / "golden"
SAMPLES = ["small_3p.pdf", "links_pdf.pdf", "docx/sample_simple.docx"]


def _entry_hashes(zp: str | Path) -> dict:
    """zip 逐条目内容 sha256；content.md 去掉 YAML front matter（含 elapsed_ms 非确定字段）。"""
    out: dict[str, str] = {}
    with zipfile.ZipFile(zp) as z:
        for n in sorted(z.namelist()):
            raw = z.read(n)
            if n.endswith("content.md"):
                t = raw.decode("utf-8")
                raw = t[t.index("\n---\n", 4) + 5:].encode("utf-8")
            out[n.split("/")[-1]] = hashlib.sha256(raw).hexdigest()
    return out


def _paths() -> list[Path]:
    missing = [s for s in SAMPLES if not (GOLDEN / s).is_file()]
    if missing:
        pytest.skip(f"golden samples missing: {missing}")
    return [GOLDEN / s for s in SAMPLES]


# --- CH-447 有界内存 ----------------------------------------------------------
def test_default_chunk_pages_is_bounded(cfg):
    """默认必须是分块档：整本一次读完在 320 页样本上实测驻留 1290MB（低配机不可接受）。"""
    assert int(config.get(cfg, "s1.chunk_pages", 0)) == 32


def test_chunking_is_output_equivalent(tmp_path, cfg):
    """chunk=1（每页重开文档）与 chunk=0（整本一趟）产物逐条目一致 ——
    分块只是内存纪律，不得改变任何一个字节。"""
    from doc2md.pipeline.convert import convert
    src = GOLDEN / "small_3p.pdf"
    ref = convert(src, cfg=cfg, out_dir=tmp_path / "off")
    for chunk in (1, 2, 3, 32):
        c = copy.deepcopy(cfg)
        c["s1"]["chunk_pages"] = chunk
        got = convert(src, cfg=c, out_dir=tmp_path / f"on{chunk}")
        assert got.doc_id == ref.doc_id
        assert _entry_hashes(got.zip_path) == _entry_hashes(ref.zip_path), f"chunk={chunk}"


def test_hash_of_path_matches_content_hash(cfg, tmp_path):
    """流式哈希与整本读入哈希同值（约定 14：doc_id 只由内容决定）；跨块边界不得漏字节。"""
    from doc2md.ir import doc_id_of, hash_of_path, source_key_of
    f = tmp_path / "blob.bin"
    f.write_bytes(b"x" * (1 << 20) + b"tail")        # 跨 1MB 块边界
    doc, key = hash_of_path(f)
    raw = f.read_bytes()
    assert doc == doc_id_of(raw)[:16] and key == source_key_of(raw)[:24]
    assert hash_of_path(f, chunk_bytes=7)[0] == doc  # 块大小不影响结果


# --- CH-448 批量进程池 --------------------------------------------------------
def test_batch_workers_default_is_bounded(cfg):
    import os

    from doc2md.pipeline.batch import default_workers
    c = copy.deepcopy(cfg)
    c["perf"]["workers"] = 0
    assert default_workers(c) == min(4, os.cpu_count() or 1)
    c["perf"]["workers"] = 2
    assert default_workers(c) == 2
    c["perf"]["workers"] = -1                        # 负数不得变成「0 个进程」挂死
    assert default_workers(c) == 1


@pytest.mark.parametrize("start_method", [None, "spawn"])
def test_batch_matches_serial(tmp_path, cfg, start_method):
    """批量 = 串行（逐条目内容）；spawn 分支复现 Windows 无 fork 的启动方式。"""
    from doc2md.pipeline.batch import run_batch
    from doc2md.pipeline.convert import convert
    files = _paths()
    serial = {}
    for f in files:
        r = convert(f, cfg=cfg, out_dir=tmp_path / "serial")
        serial[f.name] = _entry_hashes(r.zip_path)
    res = run_batch(files, cfg=cfg, out_dir=tmp_path / "batch", workers=3,
                    start_method=start_method)
    assert len(res) == len(files)
    assert all(r["status"] == "ok" for r in res), res
    for r in res:
        assert _entry_hashes(r["zip"]) == serial[Path(r["file"]).name]


def test_batch_reports_gate_reject_per_file(tmp_path, cfg):
    """批量面的拒收是逐文件语义（不得因一条坏文件拖垮整批，也不得静默成功）。"""
    from doc2md.pipeline.batch import run_batch
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot a real pdf\n")
    files = _paths()[:1] + [bad]
    res = run_batch(files, cfg=cfg, out_dir=tmp_path / "out", workers=2)
    by = {Path(r["file"]).name: r for r in res}
    assert by["small_3p.pdf"]["status"] == "ok"
    assert by["bad.pdf"]["status"] in ("rejected", "error")
    assert by["bad.pdf"]["code"] in ("E4221", "E4131", "E4151", "E5000", "E5031")


def test_cli_convert_dir_uses_pool(tmp_path, cfg, monkeypatch):
    """CLI 目录投递走批量路径，产物与配置根一致（--workers 1 可退回串行）。"""
    from doc2md import cli
    src = tmp_path / "in"
    src.mkdir()
    for f in _paths():
        (src / f.name).write_bytes(f.read_bytes())
    out = tmp_path / "work" / "out"
    rc = cli.main(["convert", str(src), "--root", str(tmp_path / "work"),
                   "--out", str(out), "--json", "--workers", "2"])
    assert rc == 0, rc
    zips = sorted(out.glob("*.zip"))
    assert len(zips) == 3, zips
    for z in zips:
        assert _entry_hashes(z)


# --- CH-449 入队 / 消费 -------------------------------------------------------
def test_queue_async_enqueue_then_work(tmp_cfg, tmp_path, api_ready_cfg):
    """POST /jobs 只入队 → GET /jobs 可见 → `doc2md work --once` 消费入库、可检索。"""
    from doc2md import worker
    cfg = api_ready_cfg
    app, client, tokens = _app(tmp_cfg, tmp_path, cfg, async_queue=True)
    src = GOLDEN / "small_3p.pdf"
    r = client.post("/jobs", json={"path": str(src)}, headers=_h(tokens, "editor"))
    assert r.status_code == 202, r.get_json()
    body = r.get_json()
    assert body["mode"] == "queued"
    job = body["jobs"][0]
    assert job["status"] == "queued" and job["job_id"] and job["doc_id"]
    assert client.get(f"/docs/{job['doc_id']}", headers=_h(tokens)).status_code == 404
    lst = client.get("/jobs", headers=_h(tokens)).get_json()
    assert lst["counts"].get("queued") == 1

    root = tmp_path / "work"
    rep = worker.run_work(tmp_cfg, root, once=True, workers=2)
    assert (rep.done, rep.failed, rep.rejected) == (1, 0, 0), rep.to_dict()
    assert client.get(f"/docs/{job['doc_id']}", headers=_h(tokens)).status_code == 200
    assert client.get("/jobs", headers=_h(tokens)).get_json()["counts"].get("done") == 1
    app.config["doc2md"]["writer"].close()


def test_queue_async_idempotency_and_attach(tmp_cfg, tmp_path, api_ready_cfg):
    """入队路径同样必须有 Idempotency-Key 与「同 doc 进行中 → 附着」两条去重。"""
    app, client, tokens = _app(tmp_cfg, tmp_path, api_ready_cfg, async_queue=True)
    src = str(GOLDEN / "small_3p.pdf")
    h = dict(_h(tokens, "editor"), **{"Idempotency-Key": "idem-perf-1"})
    j1 = client.post("/jobs", json={"path": src}, headers=h).get_json()["jobs"][0]
    j2 = client.post("/jobs", json={"path": src}, headers=h).get_json()["jobs"][0]
    assert j2["idempotency_hit"] is True and j2["job_id"] == j1["job_id"]
    j3 = client.post("/jobs", json={"path": src}, headers=_h(tokens, "editor")) \
              .get_json()["jobs"][0]
    assert j3["attached"] is True and j3["job_id"] == j1["job_id"]
    assert client.get("/jobs", headers=_h(tokens)).get_json()["counts"].get("queued") == 1
    app.config["doc2md"]["writer"].close()


def test_claim_is_atomic_per_job(store):
    """两个消费进程同时认领不得拿到同一作业（认领与置 running 同事务）。"""
    from doc2md.scheduler import Scheduler
    from doc2md.worker import claim
    sched = Scheduler(store)
    ids = [sched.submit(f"doc{i}", f"src{i}").job_id for i in range(3)]
    for jid in ids:
        with store.tx() as tx:
            tx.execute("UPDATE tasks SET detail_json=? WHERE job_id=?",
                       (json.dumps({"path": "/tmp/x.pdf"}), jid))
    a = claim(store, 2)
    b = claim(store, 2)
    assert len(a) == 2 and len(b) == 1
    assert not ({x["job_id"] for x in a} & {x["job_id"] for x in b})
    assert store.q1("SELECT status FROM tasks WHERE job_id=?", (a[0]["job_id"],))["status"] \
        == "running"


def test_work_fails_jobs_without_path(tmp_path, cfg):
    """历史 queued 行没有消费路径 → 明确 failed/E4001，不得静默反复认领。"""
    from doc2md import worker
    from doc2md.scheduler import Scheduler
    from doc2md.storage.db import Store
    root = tmp_path / "work"
    root.mkdir()
    store = Store(root / "state.sqlite")
    j = Scheduler(store).submit("legacy-doc", "legacy-key")
    rep = worker.run_work(cfg, root, once=True, workers=1)
    assert rep.drained == 1 and rep.failed == 1
    row = store.q1("SELECT status, error_code FROM tasks WHERE job_id=?", (j.job_id,))
    assert row["status"] == "failed" and row["error_code"] == "E4001"
    store.close()


def _stale_running_row(store, minutes: int, *, with_path: bool = True) -> str:
    """造一条「被崩溃的 work 进程遗留在 running」的作业，started_at 往回推 minutes 分钟。

    时间串必须用 now_iso() 的真实格式（ISO + '+00:00'），否则测不出回收谓词的
    比较缺陷——历史 bug 正是「测试里写 sqlite 风格的时间串能过、真实写入格式不过」。
    """
    from datetime import datetime, timedelta, timezone

    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="milliseconds")
    with store.tx() as tx:
        tx.execute(
            "INSERT INTO tasks(job_id, doc_id, source_key, status, created_at,"
            " started_at, source_path, mode) VALUES (?,?,?,?,?,?,?,?)",
            (f"job-stale-{minutes}", f"doc-stale-{minutes}", f"src-stale-{minutes}",
             "running", ts, ts, ("/tmp/x.pdf" if with_path else None), "auto"))
    return f"job-stale-{minutes}"


def test_reclaim_stale_matches_real_timestamp_format(tmp_path, cfg):
    """CH-448 的遗骸回收必须真的能命中 work.claim() 写下的时间格式（缺陷修复）。

    started_at 由 now_iso() 写成 `...T..:..:..+00:00`，而 SQL 侧原来是
    `started_at < datetime('now', ?)` —— SQLite 的字符串比较在 'T'(0x54) 对
    ' '(0x20) 处就分胜负，任何同日的过期作业都判不出来：OOM 后的遗骸永远
    停在 running，队列看起来「有活干」却永不动。断言按真实格式给，不给宽松值。
    """
    from doc2md import worker
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    root.mkdir()
    store = Store(root / "state.sqlite")
    jid = _stale_running_row(store, 99)                    # 远超 30 分钟阈值
    fresh = _stale_running_row(store, 5, with_path=False)  # 未过期 + 无消费路径
    assert worker.reclaim_stale(store, cfg) == 1
    assert store.q1("SELECT status FROM tasks WHERE job_id=?", (jid,))["status"] == "queued"
    # 未过期、以及不带消费路径的行都不得被动（同步 serve 路径语义保持不被回收）
    row = store.q1("SELECT status, started_at FROM tasks WHERE job_id=?", (fresh,))
    assert row["status"] == "running" and row["started_at"]
    store.close()


def test_run_work_reports_reclaimed_without_crashing(tmp_path, cfg):
    """run_work 启动时若真有遗骸，必须把 reclaimed_stale 记进报告而不是崩掉。

    历史缺陷：`total.notes.append(...)` 写在 `total = WorkReport()` 之前，
    于是**唯一「回收真的发生了」的分支**必抛 NameError。低配机上 OOM 后重启
    work 正是最常见路径，崩溃等于放弃 CH-448 的整个恢复设计。
    """
    from doc2md import worker
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    _stale_running_row(store, 99)
    store.close()

    rep = worker.run_work(cfg, root, once=True, workers=1)
    assert any(n.startswith("reclaimed_stale=1") for n in rep.notes), rep.notes
    dumped = (root / "logs" / "work.jsonl").read_text(encoding="utf-8")
    assert '"reclaimed_stale": 1' in dumped


def test_work_writes_its_own_log(tmp_path, cfg):
    """CH-444 的第三条：work 进程写 logs/work.jsonl，不与 app/cron 同名互踩。"""
    from doc2md import worker
    from doc2md.storage.db import Store
    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "app.jsonl").write_text('{"code":"KEEP"}\n', encoding="utf-8")
    store = Store(root / "state.sqlite")
    store.close()
    rep = worker.run_work(cfg, root, once=True, workers=1)
    assert rep.drained == 0
    dumped = (root / "logs" / "work.jsonl").read_text(encoding="utf-8")
    assert "WORK_START" in dumped and "WORK_STOP" in dumped
    assert (root / "logs" / "app.jsonl").read_text(encoding="utf-8") == '{"code":"KEEP"}\n'


# --- CH-450 NFR-6 磁盘门禁 ----------------------------------------------------
def test_disk_gate_blocks_write(tmp_path, cfg, monkeypatch):
    import doc2md.ops.diskguard as dg
    monkeypatch.setattr(dg, "free_mb", lambda p: 1)
    from doc2md.pipeline.convert import convert
    with pytest.raises(E4131_TooLarge) as ei:
        convert(GOLDEN / "small_3p.pdf", cfg=cfg, out_dir=tmp_path / "out")
    assert ei.value.detail["rule"] == "NFR-6" and ei.value.http == 413
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").glob("*.zip")) == []


def test_disk_gate_estimate_uses_safety_factor(tmp_path, cfg, monkeypatch):
    import doc2md.ops.diskguard as dg
    c = copy.deepcopy(cfg)
    c["perf"].update({"min_free_mb": 0, "disk_safety_factor": 3})
    src = (tmp_path / "src.bin")
    src.write_bytes(b"z" * (10 * 1024 * 1024))       # 10MB
    monkeypatch.setattr(dg, "free_mb", lambda p: 25)  # 25MB < 10MB×3
    with pytest.raises(E4131_TooLarge) as ei:
        dg.assert_free_space(tmp_path / "out", src.stat().st_size, cfg=c)
    assert ei.value.detail["need_mb"] >= 30
    monkeypatch.setattr(dg, "free_mb", lambda p: 40)
    assert dg.assert_free_space(tmp_path / "out", src.stat().st_size, cfg=c) >= 40


def test_disk_gate_disabled_needs_both_zero(tmp_path, cfg, monkeypatch):
    """关闭门禁 = 固定下限与放大系数同时置 0（配额自管的挂载点），此时不阻塞写入。"""
    import doc2md.ops.diskguard as dg
    c = copy.deepcopy(cfg)
    c["perf"].update({"min_free_mb": 0, "disk_safety_factor": 0})
    monkeypatch.setattr(dg, "free_mb", lambda p: 0)
    assert dg.assert_free_space(tmp_path, 10 ** 9, cfg=c) == 0
    # 只关下限：放大系数仍在，大文件依旧受管
    c["perf"]["disk_safety_factor"] = 3
    with pytest.raises(E4131_TooLarge):
        dg.assert_free_space(tmp_path, 10 ** 9, cfg=c)


def test_api_jobs_returns_413_when_disk_short(tmp_cfg, tmp_path, api_ready_cfg, monkeypatch):
    """API 面同步转换撞门禁 → 413/E4131（而不是写半截 zip 后 500）。"""
    import doc2md.ops.diskguard as dg
    monkeypatch.setattr(dg, "free_mb", lambda p: 1)
    app, client, tokens = _app(tmp_cfg, tmp_path, api_ready_cfg)
    r = client.post("/jobs", json={"path": str(GOLDEN / "small_3p.pdf")},
                    headers=_h(tokens, "editor"))
    assert r.status_code == 413, r.get_json()
    assert r.get_json()["code"] == "E4131"
    app.config["doc2md"]["writer"].close()


def test_cron_records_disk_gate_failure(tmp_cfg, tmp_path, monkeypatch, cfg):
    """NFR-19 的兜底口径（方案 §「轮转异常退化为告警，NFR-6 磁盘门禁兜底」）：
    空间不足时快照步骤失败并可观测，不产生半截 sqlite。"""
    import doc2md.cron as cron
    import doc2md.ops.diskguard as dg
    from doc2md.storage.db import Store
    monkeypatch.setattr(dg, "free_mb", lambda p: 1)
    root = tmp_path / "work"
    root.mkdir(parents=True)
    Store(root / "state.sqlite").close()
    rep = cron.run(tmp_cfg, root=root, mode="daily")
    assert rep.ok is False
    snap = next(s for s in rep.steps if s["step"] == "local_snapshot")
    assert snap.get("code") == "E4131" and snap.get("ok") is False
    assert list(Path(tmp_cfg["ops"]["backup_local_dir"]).glob("*.sqlite")) == [] \
        if Path(tmp_cfg["ops"]["backup_local_dir"]).exists() else True


# --- CH-451 Windows 控制台 / 编码 ---------------------------------------------
def test_sniff_maps_to_e4151(tmp_path):
    """magic 不合法必须是契约内的 415/E4151（此前裸 ValueError → CLI 栈回溯 / API 500）。"""
    from doc2md.pipeline.s1_parse import sniff
    junk = tmp_path / "not-a-doc.pdf"
    junk.write_bytes(b"PK\x03\x04nothing")
    with pytest.raises(E4151_UnsupportedType) as ei:
        sniff(junk)
    assert ei.value.http == 415
    missing = tmp_path / "gone.pdf"
    with pytest.raises(E4151_UnsupportedType):
        sniff(missing)


def test_api_magic_mismatch_is_415(tmp_cfg, tmp_path, api_ready_cfg):
    """API 面：zip 容器但不是 docx → 415/E4151（CH-451 前是 500 裸 ValueError）。"""
    app, client, tokens = _app(tmp_cfg, tmp_path, api_ready_cfg)
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"PK\x03\x04this is not a docx\n")
    r = client.post("/jobs", json={"path": str(junk)}, headers=_h(tokens, "editor"))
    assert r.status_code == 415, r.get_json()
    assert r.get_json()["code"] == "E4151"
    missing = client.post("/jobs", json={"path": str(tmp_path / "nope.pdf")},
                          headers=_h(tokens, "editor"))
    assert missing.status_code == 404, missing.get_json()
    app.config["doc2md"]["writer"].close()


def test_cli_machine_lines_are_ascii(tmp_path, monkeypatch, capsys):
    """机器可读行在 cp936 与 UTF-8 控制台上同字节可解析：一律 ASCII 转义。"""
    from doc2md import cli
    line = cli._jline({"file": "🔥报告.pdf", "status": "ok"})
    assert line.isascii(), line
    assert json.loads(line)["file"] == "🔥报告.pdf"


def test_console_safe_replaces_unencodable(tmp_path, monkeypatch, capsys):
    """CH-451 复现：GBK 控制台打印 emoji 文件名原会 UnicodeEncodeError 崩栈。"""
    import io

    from doc2md import cli
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp936", errors="strict")
    monkeypatch.setattr(sys, "stdout", buf)
    with pytest.raises(UnicodeEncodeError):        # 修复前现场（strict 下必崩）
        print("🔥报告.pdf")
        buf.flush()
    cli._console_safe()                             # 已降级为 errors="replace"
    buf2 = io.TextIOWrapper(io.BytesIO(), encoding="cp936", errors="strict")
    monkeypatch.setattr(sys, "stdout", buf2)
    cli._console_safe()
    print("🔥报告.pdf 转换完成")
    buf2.flush()
    out = buf2.buffer.getvalue().decode("cp936", errors="replace")
    assert "报告.pdf" in out and "Traceback" not in out


def test_cli_convert_unsupported_exits_biz(tmp_path, cfg, capsys):
    """CLI 面对垃圾文件退出码必须是 4（业务错），不是 Python 栈回溯的 1。"""
    from doc2md import cli
    junk = tmp_path / "in"
    junk.mkdir()
    (junk / "x.pdf").write_bytes(b"PK\x03\x04nope")
    rc = cli.main(["convert", str(junk), "--root", str(tmp_path / "work"), "--json",
                   "--workers", "1"])
    assert rc == cli.EXIT_BIZ == 4, rc
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["code"] == "E4151"


def test_cli_convert_dir_reports_gate_reject_exit(tmp_path, cfg, capsys):
    """目录批量中的拒收 → 退出码 3（EXIT_GATE），其余文件仍完成。"""
    from doc2md import cli
    src = tmp_path / "in"
    src.mkdir()
    (src / "ok.pdf").write_bytes((GOLDEN / "small_3p.pdf").read_bytes())
    bad = (GOLDEN / "badtail_scan.pdf")
    if bad.is_file():
        (src / "bad.pdf").write_bytes(bad.read_bytes())
    out = tmp_path / "work" / "out"
    rc = cli.main(["convert", str(src), "--root", str(tmp_path / "work"),
                   "--out", str(out), "--json", "--workers", "2"])
    if bad.is_file():
        assert rc == cli.EXIT_GATE == 3, rc
        assert list(out.glob("*.zip")), "拒收不得连带阻塞其余文件"
    else:
        assert rc == 0


# --- doctor 可观测性 ----------------------------------------------------------
def test_doctor_exposes_perf_knobs(tmp_path, cfg, capsys):
    from doc2md import cli
    rc = cli.main(["doctor", "--root", str(tmp_path / "work")])
    assert rc == 0, rc
    out = json.loads(capsys.readouterr().out)
    assert out["s1_chunk_pages"] == 32 and out["min_free_mb"] == 512
    assert out["cpu_count"] >= 1 and isinstance(out["free_mb"], int)


# --- helpers ------------------------------------------------------------------
@pytest.fixture
def api_ready_cfg(cfg):
    """带 token 的服务侧配置（每个用例独立实例）。"""
    return copy.deepcopy(cfg)


def _app(tmp_cfg, tmp_path, cfg, async_queue=False):
    from doc2md import auth
    from doc2md.api import create_app
    c = copy.deepcopy(tmp_cfg)
    c.setdefault("perf", {})["queue_async"] = async_queue
    root = tmp_path / "work"
    app = create_app(c, root)
    st = app.config["doc2md"]["store"]
    tokens = {r: auth.issue(st, r, f"perf-{r}") for r in ("admin", "editor", "reader")}
    return app, app.test_client(), tokens


def _h(tokens: dict, role: str = "admin") -> dict:
    return {"X-Doc2md-Token": tokens[role]}


# --- CH-462 / A-16：zip 完整性（防静默截断） ----------------------------------
def test_product_zip_complete(large_sharded):
    """产物 zip 三条目齐全、CRC 全过、ir.json 与内存 IR 块数一致——
    打包环节若静默丢条目/截断，现有按名字安全性的断言抓不住。"""
    import json
    import zipfile
    assert large_sharded.zip_path, "分片产物未打包"
    with zipfile.ZipFile(large_sharded.zip_path) as z:
        assert z.testzip() is None                       # 全条目 CRC 校验
        names = z.namelist()
        base = large_sharded.doc_id
        for entry in ("content.md", "ir.json", "report.html"):
            assert f"{base}/{entry}" in names, f"缺条目 {entry}"
        ir = json.loads(z.read(f"{base}/ir.json"))
        assert len(ir["blocks"]) == len(large_sharded.ir.blocks)
        assert z.read(f"{base}/content.md").strip()      # 正文非空
