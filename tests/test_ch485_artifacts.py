"""CH-485 / 产物事实源：块字段回读、PATCH/E4091 重建、原子打包、恢复补产物。"""
from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from doc2md.ir import IR, Block
from doc2md.storage.repo import list_blocks, save_ir

pytestmark = [pytest.mark.milestone("M8")]


def _ir() -> IR:
    return IR(doc_id="a" * 16, source_key="a" * 24, source_name="x.pdf",
              blocks=[Block(id="b1", text="正文", font={"family": "serif", "size": 10.0},
                            links=[{"url": "https://e/x", "text": "正文", "dest": ""}],
                            provenance={"modified_by": ["test"]},
                            flags={"note_kind": "footnote"}, marks={"bold": True})])


@pytest.mark.gate
def test_save_ir_round_trips_artifact_fields(store):
    ir = _ir()
    meta = {**ir.meta(), "shard_stats": {"shards": 1}, "llm_prompt": "v2.4"}
    gate = {"mode": "auto", "page_detail": [{"page": 1, "ok": True}]}
    save_ir(store, ir, meta=meta, gate=gate)
    got = list_blocks(store, ir.doc_id)[0]
    assert got.font == {"family": "serif", "size": 10.0}
    assert got.links[0]["url"] == "https://e/x"
    assert got.provenance["modified_by"] == ["test"]
    assert got.flags["note_kind"] == "footnote"
    row = store.q1("SELECT meta_json, gate_json FROM docs WHERE doc_id=?", (ir.doc_id,))
    assert json.loads(row["meta_json"])["shard_stats"] == {"shards": 1}
    assert json.loads(row["gate_json"])["mode"] == "auto"


@pytest.mark.gate
def test_patch_rebuilds_zip_content(api_client, headers, saved_doc, tmp_cfg):
    client, tokens, doc_id = saved_doc
    doc = client.get(f"/docs/{doc_id}", headers=headers(tokens, "reader")).get_json()
    block = next(b for b in doc["blocks"] if b["text"].strip())
    marker = "CH485-PATCH-MARKER"
    r = client.patch(f"/docs/{doc_id}/blocks/{block['id']}",
                     json={"base_revision": block["revision"], "text": marker},
                     headers=headers(tokens, "editor"))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    zp = Path(tmp_cfg["paths"]["out"]) / f"{doc_id}.zip"
    with zipfile.ZipFile(zp) as z:
        md = z.read(f"{doc_id}/content.md").decode("utf-8")
    assert marker in md


@pytest.mark.gate
def test_e4091_rebuild_restores_edited_zip(api_client, headers, saved_doc,
                                            small_pdf, tmp_cfg):
    client, tokens, doc_id = saved_doc
    doc = client.get(f"/docs/{doc_id}", headers=headers(tokens, "reader")).get_json()
    block = next(b for b in doc["blocks"] if b["text"].strip())
    marker = "CH485-E4091-EDIT"
    client.patch(f"/docs/{doc_id}/blocks/{block['id']}",
                 json={"base_revision": block["revision"], "text": marker},
                 headers=headers(tokens, "editor"))
    r = client.post("/jobs", json={"path": str(small_pdf)},
                    headers=headers(tokens, "editor"))
    assert r.status_code == 202, r.get_data(as_text=True)[:300]
    jobs = r.get_json()["jobs"]
    assert jobs[0]["status"] == "conflict", jobs
    zp = Path(tmp_cfg["paths"]["out"]) / f"{doc_id}.zip"
    with zipfile.ZipFile(zp) as z:
        md = z.read(f"{doc_id}/content.md").decode("utf-8")
    assert marker in md, "E4091 后必须从 DB 编辑内容重建 zip"


@pytest.mark.gate
def test_artifact_lock_serializes_threads(tmp_path):
    """B-18：同一 out 目录的 doc 重建必须串行，不能两个线程同时 package。"""
    from doc2md.api import _artifact_lock
    active = 0
    peak = 0
    order: list[int] = []
    guard = threading.Lock()

    def worker(i: int) -> None:
        nonlocal active, peak
        with _artifact_lock(tmp_path, "doc-lock", timeout=5):
            with guard:
                active += 1
                peak = max(peak, active)
                order.append(i)
            time.sleep(0.05)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1, f"artifact lock overlapped: order={order} peak={peak}"
    assert not (tmp_path / ".locks" / "doc-lock.lock").exists()


@pytest.mark.gate
def test_convert_precheck_refuses_to_overwrite_edited_artifact(
        tmp_path, golden_dir, tmp_cfg, small_pdf):
    """CLI/watch 没有 save_ir 的事后补偿：解析前必须拒绝覆盖已编辑文档的 zip。"""
    from doc2md.errors import E4091_DocOverwrite
    from doc2md.pipeline.convert import convert
    from doc2md.storage.db import Store

    root = Path(tmp_cfg["paths"]["root"])
    out = tmp_path / "out"
    res = convert(small_pdf, cfg=tmp_cfg, out_dir=out)
    store = Store(root / "state.sqlite")
    try:
        save_ir(store, res.ir, meta=res.meta, gate=res.gate)
        with store.tx() as tx:
            tx.execute("UPDATE blocks SET revision=2 WHERE doc_id=?", (res.doc_id,))
    finally:
        store.close()

    with pytest.raises(E4091_DocOverwrite):
        convert(small_pdf, cfg=tmp_cfg, out_dir=out, precheck_edits=True)
    # 显式 force 仍是逃生门。
    forced = convert(small_pdf, cfg=tmp_cfg, out_dir=out, force=True,
                     precheck_edits=True)
    assert forced.zip_path and forced.zip_path.exists()


def test_package_is_atomic(tmp_path, golden_dir, cfg):
    from doc2md.pipeline.convert import convert
    res = convert(golden_dir / "small_3p.pdf", cfg=cfg, out_dir=tmp_path)
    assert res.zip_path and res.zip_path.exists()
    assert not list(tmp_path.glob("*.part"))
    with zipfile.ZipFile(res.zip_path) as z:
        assert f"{res.doc_id}/content.md" in z.namelist()


@pytest.mark.gate
def test_rebuild_artifact_from_db_after_loss(store, tmp_path):
    from doc2md.api import _rebuild_artifact
    ir = _ir()
    meta = ir.meta()
    save_ir(store, ir, meta=meta, gate={"mode": "auto", "page_detail": []})
    out = tmp_path / "out"
    zp = out / f"{ir.doc_id}.zip"
    _rebuild_artifact(store, ir.doc_id, {"paths": {"root": str(tmp_path),
                                                   "out": str(out)}}, tmp_path)
    assert zp.exists()
    zp.unlink()
    _rebuild_artifact(store, ir.doc_id, {"paths": {"root": str(tmp_path),
                                                   "out": str(out)}}, tmp_path)
    assert zp.exists()
    with zipfile.ZipFile(zp) as z:
        assert json.loads(z.read(f"{ir.doc_id}/ir.json"))["meta"]["doc_id"] == ir.doc_id
