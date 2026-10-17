"""CH-483 / R8-SVC-1：同步转换窗口的删除守护。"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from doc2md.storage import repo


def _ir(doc_id: str, text: str, source_key: str = "s1"):
    from doc2md.ir import IR, Block
    return IR(doc_id=doc_id, source_key=source_key, source_name="race.pdf",
              format="pdf", pages=1,
              blocks=[Block(id="b1", type="paragraph", text=text, page=0)])


@pytest.mark.gate
def test_sync_delete_during_conversion_does_not_resurrect(api_client, headers,
                                                          monkeypatch, tmp_path):
    """转换期间删除已存在的文档：落库事务必须看到墓碑并拒绝，而不是重建 docs。"""
    client, tokens = api_client
    holder = client.application.config["doc2md"]
    store = holder["store"]
    root = Path(holder["root"])
    repo.save_ir(store, _ir("d1", "既有正文"))

    src = tmp_path / "race.pdf"
    src.write_bytes(b"%PDF-1.7\n")

    api_mod = importlib.import_module("doc2md.api")

    def fake_convert(path, **kw):
        # 转换窗口内用户删除：真实 delete_doc 会清行并写墓碑。
        repo.delete_doc(store, "d1", "admin", root)
        return SimpleNamespace(ir=_ir("d1", "复活正文"), doc_id="d1",
                               source_key="s1")

    monkeypatch.setattr(api_mod, "convert", fake_convert)

    r = client.post("/jobs", json={"path": str(src)}, headers=headers(tokens, "editor"))
    assert r.status_code == 202, r.get_data(as_text=True)[:500]
    job = r.get_json()["jobs"][0]
    assert job["status"] == "rejected", job
    assert job["code"] == "E4041", job
    assert job.get("reason") == "doc_deleted_during_conversion", job
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='d1'")["c"] == 0
    assert store.q1("SELECT COUNT(*) c FROM blocks WHERE doc_id='d1'")["c"] == 0
    assert store.q1("SELECT COUNT(*) c FROM tasks WHERE doc_id='d1' AND status='done'")["c"] == 0
    text = store.q("SELECT payload_json FROM events WHERE doc_id='d1'")
    assert "复活正文" not in str(text)


@pytest.mark.gate
def test_sync_delete_before_request_still_allows_reimport(api_client, headers,
                                                          monkeypatch, tmp_path):
    """删除发生在请求开始之前（用户重新上传）不在守护范围：必须允许落库。"""
    client, tokens = api_client
    holder = client.application.config["doc2md"]
    store = holder["store"]
    root = Path(holder["root"])
    repo.save_ir(store, _ir("d1", "旧正文"))
    repo.delete_doc(store, "d1", "admin", root)

    src = tmp_path / "reimport.pdf"
    src.write_bytes(b"%PDF-1.7\n")
    api_mod = importlib.import_module("doc2md.api")
    monkeypatch.setattr(api_mod, "convert", lambda p, **kw: SimpleNamespace(
        ir=_ir("d1", "新正文"), doc_id="d1", source_key="s1"))

    r = client.post("/jobs", json={"path": str(src)}, headers=headers(tokens, "editor"))
    assert r.status_code == 202, r.get_data(as_text=True)[:500]
    job = r.get_json()["jobs"][0]
    assert job.get("status") != "rejected", job
    assert store.q1("SELECT text FROM blocks WHERE doc_id='d1'")["text"] == "新正文"
