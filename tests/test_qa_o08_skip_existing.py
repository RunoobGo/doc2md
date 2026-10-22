"""tests/test_qa_o08_skip_existing.py — O-08 增量跳过（QA-D2M-2026-10-21 / C-27 / F-07）。

缺口：同目录二次转换把已入库内容原样重转一遍（解析→渲染→打包→落库全走一遍，
耗时与首次同量级）；watch 进程重启后登记簿失忆，首轮把目录内老文件全部重转。

修复：
- `convert --skip-existing`：内容寻址 doc_id 命中库内 docs 行即跳过，
  结果行 `status="skipped"`（与 ok 行同键集，O-03 同构纪律的第三种终态）；
- watch 重启判据落库面：`tasks.source_path + source_key` 已有 done 行即跳过
  （不新增表、不引入第二份事实源；进程内 CH-464 判据管「同进程不重复」，
  本判据管「重启不重转」，两层互补）；
- 文档化边界：不加开关时重复转换仍走 E4091 兜底（编辑保护是安全网，
  跳过只是优化——见 README / dev-plan 附录 B）。

边界（本文件锁定）：
① 跳过行与成功行同键集（脚本一套解析三种终态）；
② 源文件改写 → 内容哈希变 → 不误跳过（内容寻址的必然结果，仍要红线钉住）；
③ 库不存在 / 查询失败 → fail-open 照常转换（宁可重转不静默漏转）；
④ 跳过不碰既有产物（zip mtime/sha 不变）；
⑤ watch 重启零转换、改写后仍转换（热更新语义保留）。

口径边界（重要）：CLI `convert` 本身**不写库**（库由 serve / watch / work
populate；让 convert 改入库会与 E4091 编辑保护语义相冲突，属另一个量级
的改动）。因此 `--skip-existing` 的判据有两层（O-08 + N-1）：
① 库判据：doc_id 命中库内 docs 行（watch / serve / work 转过的内容）；
② 产物回退判据（N-1）：库未命中/不存在时，`out/<doc_id>.zip` 存在且
   front-matter `pipeline_version` 与当前管线一致才跳（CLI 纯产物流二转
   也能跳；陈旧产物 fail-closed 照常重转）。
本文件的库 populate 一律走 `_ingest`（convert + save_ir，与 watch
同步路径同一入口）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from doc2md.cli import main
from doc2md.pipeline.batch import result_row, skipped_row

pytestmark = [pytest.mark.milestone("DoD")]


def _root(tmp_path) -> Path:
    return tmp_path / "work"


def _ingest(root: Path, src: Path) -> str:
    """转换并入库（CLI convert 只产产物不落库；入库口径同 watch：convert + save_ir）。"""
    from doc2md.pipeline.convert import convert
    from doc2md.storage.db import Store
    from doc2md.storage.repo import save_ir
    res = convert(src, out_dir=root / "out")
    s = Store(root / "state.sqlite")
    try:
        save_ir(s, res.ir, meta=res.meta, gate=res.gate)
    finally:
        s.close()
    return res.doc_id


def _make_docx(path: Path, text: str) -> None:
    import docx
    d = docx.Document()
    d.add_paragraph(text * 12)          # >120 字符：S1.5 门禁每页字符下限
    d.save(str(path))


def _digest(p: Path) -> tuple[int, str]:
    st = p.stat()
    return st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()


def _watch_once(root: Path, drop: Path) -> int:
    return main(["watch", str(drop), "--once", "--root", str(root)])


# ---------------------------------------------------------------- convert 跳过
def test_convert_skip_existing_second_run_all_skipped(tmp_path, small_pdf,
                                                      capsys):
    """① 同目录连转两次：第二次全部 skipped、退出码 0、既有产物
    （mtime + sha256）逐字节不变——跳过不得以任何方式碰产物。

    库 populate 走 `_ingest`（= watch 同步路径的 convert + save_ir）：
    CLI convert 只产产物不写库（见模块 docstring 的口径边界）。"""
    root = _root(tmp_path)
    drop = tmp_path / "drop"
    drop.mkdir()
    shutil.copy(small_pdf, drop / "a.pdf")
    shutil.copy(small_pdf, drop / "b.pdf")
    _ingest(root, drop / "a.pdf")
    _ingest(root, drop / "b.pdf")
    capsys.readouterr()
    zips = sorted((root / "out").glob("*.zip"))
    assert len(zips) == 1, "同名同内容 → 内容寻址落同一 doc_id（一份产物）"
    before = _digest(zips[0])

    assert main(["convert", str(drop), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(rows) == 2 and all(r["status"] == "skipped" for r in rows), rows
    assert _digest(zips[0]) == before, "跳过不得触碰既有产物"


def test_convert_skip_existing_after_watch_end_to_end(tmp_path, small_pdf,
                                                      capsys):
    """① 端到端（真实用户路径）：watch 转过 → 库有行 → 同目录
    `convert --skip-existing` 全跳过、零重复解析。"""
    root = _root(tmp_path)
    drop = tmp_path / "wdrop"
    drop.mkdir()
    shutil.copy(small_pdf, drop / "incoming.pdf")
    old = (drop / "incoming.pdf").stat().st_mtime - 60
    os.utime(drop / "incoming.pdf", (old, old))
    assert main(["watch", str(drop), "--once", "--root", str(root)]) == 0
    capsys.readouterr()
    zips = sorted((root / "out").glob("*.zip"))
    assert len(zips) == 1
    before = _digest(zips[0])
    assert main(["convert", str(drop), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["status"] == "skipped", rows
    assert _digest(zips[0]) == before


def test_skip_existing_row_isomorphic_with_ok_row(tmp_path, small_pdf, capsys):
    """① 跳过行与成功行同键集（O-03 同构纪律延伸到第三种终态）；
    CLI `--json` 出口两种终态的 payload 键集也一致（脚本一套解析）。"""
    root = _root(tmp_path)
    ingested = tmp_path / "c.pdf"
    shutil.copy(small_pdf, ingested)
    _ingest(root, ingested)
    assert main(["convert", str(ingested), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    skipped_payload = json.loads(capsys.readouterr().out.strip())

    fresh = tmp_path / "fresh.pdf"
    shutil.copy(small_pdf, fresh)
    fresh.write_bytes(fresh.read_bytes() + b"\n% tweak\n")   # 内容变 → 新 doc_id
    assert main(["convert", str(fresh), "--root", str(root),
                 "--json"]) == 0
    ok_payload = json.loads(capsys.readouterr().out.strip())
    assert ok_payload["status"] == "ok"
    assert set(skipped_payload) == set(ok_payload), (
        f"CLI --json 出口键集不一致：{set(skipped_payload) ^ set(ok_payload)}")
    # 原始行（run_batch / _convert_one 出口）同样同构
    from doc2md.pipeline.convert import convert
    res = convert(fresh, out_dir=root / "out2")
    assert set(skipped_row("x", "d", "s")) == set(result_row("x", res))


def test_skip_existing_changed_source_is_not_skipped(tmp_path, capsys):
    """② 源文件改写 → 内容哈希变 → 不误跳过（照常转换、状态 ok）。"""
    root = _root(tmp_path)
    p = tmp_path / "mutable.docx"
    _make_docx(p, "第一版内容")
    _ingest(root, p)
    _make_docx(p, "第二版完全不同的内容")
    assert main(["convert", str(p), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "ok", "内容已变，不得跳过"
    assert row["blocks"] > 0


def test_skip_existing_batch_path_skips(tmp_path, small_pdf, capsys):
    """批量（进程池）出口同样跳过，且 CLI --json 键集与串行一致。"""
    root = _root(tmp_path)
    drop = tmp_path / "drop2"
    drop.mkdir()
    shutil.copy(small_pdf, drop / "a.pdf")
    _make_docx(drop / "b.docx", "批量样本正文")
    _ingest(root, drop / "a.pdf")
    _ingest(root, drop / "b.docx")
    assert main(["convert", str(drop), "--root", str(root),
                 "--skip-existing", "--json", "--workers", "2"]) == 0
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(rows) == 2 and all(r["status"] == "skipped" for r in rows), rows
    # CLI --json 出口（_report payload）键集与 ok 行一致：脚本一套解析
    for r in rows:
        assert set(r) == {"file", "status", "doc_id", "blocks", "chars", "zip"}
        assert r["blocks"] == 0 and r["chars"] == 0
        assert r["zip"].endswith(f"{r['doc_id']}.zip")


def test_skip_existing_without_library_is_noop(tmp_path, small_pdf, capsys):
    """③ 库不存在 → fail-open：开关形同虚设，照常转换出产物。"""
    root = _root(tmp_path)
    src = tmp_path / "fresh.pdf"
    shutil.copy(small_pdf, src)
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing"]) == 0
    out = capsys.readouterr().out
    assert "status=skipped" not in out
    assert list((root / "out").glob("*.zip")), "无库时不得跳过转换"


def test_skip_existing_db_row_without_artifact_reports_empty_zip(tmp_path,
                                                                 small_pdf,
                                                                 capsys):
    """库有行但产物丢失：仍跳过（DB 是事实源），但 zip 字段为空串——
    不冒充产物存在（导出面另有按 DB 重建的口径）。"""
    root = _root(tmp_path)
    src = tmp_path / "d.pdf"
    shutil.copy(small_pdf, src)
    doc_id = _ingest(root, src)
    (root / "out" / f"{doc_id}.zip").unlink()
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "skipped" and row["zip"] == ""


# ---------------------------------------------------------------- watch 重启
def test_watch_restart_skips_done_and_zero_reconversion(tmp_path, capsys):
    """⑤ watch 重启零转换：首轮正常转换并落 done 任务行；第二次 watch
    （全新进程语义：StabilityTracker 进程内登记簿重置）全部 skipped，
    且不产生第二次转换（产物 mtime/sha 不变、docs 行数不变）。

    反向基线（修复前）：Tracker 首见快速通道对老文件直接判稳定 → 每个新
    进程首轮把目录内全部文件重转换一遍（F-07 原缺陷）。
    """
    from doc2md.storage.db import Store
    root = _root(tmp_path)
    drop = tmp_path / "wdrop"
    drop.mkdir()
    _make_docx(drop / "report.docx", "监视目录样本正文")
    old = (drop / "report.docx").stat().st_mtime - 60
    os.utime(drop / "report.docx", (old, old))

    assert _watch_once(root, drop) == 0
    first = capsys.readouterr()
    assert '"status": "skipped"' not in first.out
    zp = next((root / "out").glob("*.zip"))
    before = _digest(zp)
    s = Store(root / "state.sqlite")
    try:
        n_docs = s.q1("SELECT COUNT(*) AS c FROM docs")["c"]
        n_done = s.q1("SELECT COUNT(*) AS c FROM tasks WHERE status='done'"
                      " AND source_path IS NOT NULL")["c"]
    finally:
        s.close()
    assert n_docs == 1 and n_done == 1

    # 第二次 watch（重启语义）：零转换
    assert _watch_once(root, drop) == 0
    second = capsys.readouterr()
    assert '"status": "skipped"' in second.out
    assert '"watched"' in second.out and "doc_id" in second.out
    assert _digest(zp) == before, "重启后的 watch 不得重转（产物不变）"
    s = Store(root / "state.sqlite")
    try:
        assert s.q1("SELECT COUNT(*) AS c FROM docs")["c"] == n_docs
    finally:
        s.close()


def test_watch_rewrite_still_converts_after_skip(tmp_path, capsys):
    """⑤ 热更新语义保留：跳过后改写文件 → 内容哈希变 → 再次 watch 照常转换。"""
    root = _root(tmp_path)
    drop = tmp_path / "wdrop2"
    drop.mkdir()
    f = drop / "live.docx"
    _make_docx(f, "初始版本正文")
    old = f.stat().st_mtime - 60
    os.utime(f, (old, old))
    assert _watch_once(root, drop) == 0
    capsys.readouterr()
    _make_docx(f, "改写之后的另一批正文")
    assert _watch_once(root, drop) == 0
    out = capsys.readouterr().out
    assert '"status": "skipped"' not in out, "改写后不得再跳过"


def test_watch_async_queue_restart_skips(tmp_path, capsys, monkeypatch):
    """异步队列路径（perf.queue_async）同样落重启判据：done 行命中即跳过，
    不入队、不转换。"""
    root = _root(tmp_path)
    drop = tmp_path / "wdrop3"
    drop.mkdir()
    f = drop / "async.docx"
    _make_docx(f, "异步队列样本正文")
    old = f.stat().st_mtime - 60
    os.utime(f, (old, old))

    # 第一轮：同步路径转换并落 done 行（source_path 已记录）
    assert _watch_once(root, drop) == 0
    capsys.readouterr()
    # 第二轮改走异步队列配置：命中 done 行 → skipped，队列零增长
    cfg_file = root / "doc2md.toml"
    cfg_file.write_text("[perf]\nqueue_async = true\n", encoding="utf-8")
    rc = main(["watch", str(drop), "--once", "--root", str(root),
               "--config", str(cfg_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "skipped"' in out
    from doc2md.storage.db import Store
    s = Store(root / "state.sqlite")
    try:
        assert s.q1("SELECT COUNT(*) AS c FROM tasks"
                    " WHERE status='queued'")["c"] == 0, "跳过不得入队"
    finally:
        s.close()


# ---------------------------------------------------------------- 单点口径
def test_skip_existing_hit_unit(tmp_path, small_pdf):
    """判据单测：命中返回三元组；库不存在返回 None；源改写后不命中。"""
    from doc2md.pipeline.batch import skip_existing_hit
    cfg = {"paths": {"root": str(_root(tmp_path))}}
    src = tmp_path / "u.pdf"
    shutil.copy(small_pdf, src)
    assert skip_existing_hit(cfg, src) is None          # 库不存在
    _ingest(_root(tmp_path), src)
    hit = skip_existing_hit(cfg, src)
    assert hit is not None and hit[0] and hit[1]
    assert hit[2].endswith(f"{hit[0]}.zip")             # 产物在 → 给路径
    data = bytearray(src.read_bytes())
    data.extend(b"\n% appended\n")                       # 内容变 → 哈希变
    src.write_bytes(bytes(data))
    assert skip_existing_hit(cfg, src) is None, "内容已变不得命中"


def test_skipped_row_zip_optional(tmp_path):
    """skipped_row 的 zip 形参可缺省（无产物时给空串，键集不变）。"""
    a = skipped_row("f", "d", "s")
    b = skipped_row("f", "d", "s", "")
    assert a == b and a["zip"] == ""
    assert skipped_row("f", "d", "s", "/x/y.zip")["zip"] == "/x/y.zip"


# ---------------------------------------------------- N-1（QA-OPT-2）产物版本契约
def test_front_matter_carries_pipeline_version(tmp_path, small_pdf):
    """N-1：新转换的 front-matter 携带 pipeline_version，且与常量同源；
    ir.json meta 同步携带（rebuild 链路用同一 dict）。"""
    import zipfile

    from doc2md import PIPELINE_VERSION
    from doc2md.pipeline.convert import convert
    res = convert(small_pdf, out_dir=tmp_path / "out")
    with zipfile.ZipFile(res.zip_path) as z:
        body = z.read(f"{res.doc_id}/content.md").decode("utf-8")
        ir = json.loads(z.read(f"{res.doc_id}/ir.json").decode("utf-8"))
    assert body.startswith("---\n")
    fm = json.loads(body[4:body.index("\n---", 4)])
    assert fm["pipeline_version"] == PIPELINE_VERSION
    assert ir["meta"]["pipeline_version"] == PIPELINE_VERSION


def test_zip_fallback_skips_fresh_artifact_without_db(tmp_path, small_pdf,
                                                      capsys):
    """N-1 收益面：CLI 纯产物流（不落库）二转——zip 存在 + 版本匹配 →
    库未命中也跳过（此前 skipped=0 的缺口闭合）。"""
    root = _root(tmp_path)
    src = tmp_path / "n1.pdf"
    shutil.copy(small_pdf, src)
    assert main(["convert", str(src), "--root", str(root)]) == 0
    capsys.readouterr()
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "skipped", row
    assert row["zip"].endswith(f"{row['doc_id']}.zip")


def _repack_with_version(zp: Path, version: str | None) -> None:
    """把 zip 内 content.md front-matter 的 pipeline_version 改写为指定值
    （None = 删除该键，模拟旧管线产物），原地替换。"""
    import zipfile
    tmp = zp.with_suffix(".tmp.zip")
    with zipfile.ZipFile(zp) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith("/content.md"):
                body = data.decode("utf-8")
                end = body.index("\n---", 4)
                fm = json.loads(body[4:end])
                if version is None:
                    fm.pop("pipeline_version", None)
                else:
                    fm["pipeline_version"] = version
                body = "---\n" + json.dumps(fm, ensure_ascii=False,
                                            indent=2) + "\n---\n" + body[end + 4:]
                data = body.encode("utf-8")
            zout.writestr(info, data)
    tmp.replace(zp)


@pytest.mark.parametrize("version", ["0.0.1", None],
                         ids=["stale-version", "legacy-no-field"])
def test_zip_fallback_never_skips_stale_artifact(tmp_path, small_pdf, capsys,
                                                 version):
    """N-1 fail-closed 红线：版本不符 / 字段缺失（旧管线产物）→ 不跳、
    照常重转（重转后 zip 内 front-matter 恢复为当前版本）。"""
    root = _root(tmp_path)
    src = tmp_path / "stale.pdf"
    shutil.copy(small_pdf, src)
    assert main(["convert", str(src), "--root", str(root)]) == 0
    zp = next((root / "out").glob("*.zip"))
    _repack_with_version(zp, version)
    capsys.readouterr()
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "ok", "陈旧产物必须重转，不得跳过"
    import zipfile
    with zipfile.ZipFile(root / "out" / f"{row['doc_id']}.zip") as z:
        body = z.read(f"{row['doc_id']}/content.md").decode("utf-8")
    fm = json.loads(body[4:body.index("\n---", 4)])
    from doc2md import PIPELINE_VERSION
    assert fm["pipeline_version"] == PIPELINE_VERSION, "重转后版本恢复"


def test_zip_fallback_corrupt_zip_fail_closed(tmp_path, small_pdf, capsys):
    """N-1 fail-closed：zip 损坏 → 不跳（宁可重转走 E4091 兜底）。"""
    root = _root(tmp_path)
    src = tmp_path / "bad.pdf"
    shutil.copy(small_pdf, src)
    assert main(["convert", str(src), "--root", str(root)]) == 0
    zp = next((root / "out").glob("*.zip"))
    zp.write_bytes(b"PK\x03\x04 truncated")       # 非法 zip
    capsys.readouterr()
    assert main(["convert", str(src), "--root", str(root),
                 "--skip-existing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["status"] == "ok", "损坏产物必须重转"


# ------------------------------------------------ N-2 附带发现（QA-OPT-3）：--out 回灌
def test_zip_fallback_honors_cli_out_override_serial(tmp_path, small_pdf,
                                                     capsys):
    """`--out` 自定义产物目录：二转 `--skip-existing` 必须在同一目录命中。

    修复前：`--out` 只用于产物写入，未回灌 cfg——skip_existing_hit 按默认
    paths.out 解析 zip 位置，自定义目录的 CLI 纯产物流二转永远 skipped=0。"""
    root = _root(tmp_path)
    drop = tmp_path / "odrop"
    drop.mkdir()
    custom = tmp_path / "custom_out"
    shutil.copy(small_pdf, drop / "a.pdf")
    shutil.copy(small_pdf, drop / "b.pdf")
    assert main(["convert", str(drop), "--root", str(root),
                 "--out", str(custom), "--json"]) == 0
    assert len(list(custom.glob("*.zip"))) == 1, "同内容落同一 doc_id"
    capsys.readouterr()
    assert main(["convert", str(drop), "--root", str(root),
                 "--out", str(custom), "--skip-existing", "--json"]) == 0
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines()
            if l.strip()]
    assert len(rows) == 2 and all(r["status"] == "skipped" for r in rows), rows
    assert all(r["zip"].endswith(f"{r['doc_id']}.zip") for r in rows)


def test_zip_fallback_honors_cli_out_override_batch(tmp_path, small_pdf,
                                                    capsys):
    """同上走批量进程池出口：worker 收到的 cfg 必须带回灌后的 paths.out。"""
    root = _root(tmp_path)
    drop = tmp_path / "odrop2"
    drop.mkdir()
    custom = tmp_path / "custom_out2"
    shutil.copy(small_pdf, drop / "a.pdf")
    _make_docx(drop / "b.docx", "批量出口自定义目录样本正文")
    assert main(["convert", str(drop), "--root", str(root),
                 "--out", str(custom), "--json", "--workers", "2"]) == 0
    capsys.readouterr()
    assert main(["convert", str(drop), "--root", str(root),
                 "--out", str(custom), "--skip-existing", "--json",
                 "--workers", "2"]) == 0
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines()
            if l.strip()]
    assert len(rows) == 2 and all(r["status"] == "skipped" for r in rows), rows


