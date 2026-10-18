"""CH-485 / 产物事实源：块字段回读、PATCH/E4091 重建、原子打包、恢复补产物。"""
from __future__ import annotations

import json
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
