"""tests/test_ch484_artifact_orphan.py — CH-484：删除×在途转换留下的产物孤儿。

CH-476/483 把「删除×转换竞争」的**库面**复活堵死了（mark rowcount=0 / 墓碑命中
→ E4041 整笔回滚，正文不写回）。B-4 本机多进程并发压测实测到库面确实干净，但
`out/{doc_id}.zip` 文件仍是孤儿：进程池按转换前抓到的内容把 zip 落盘，删除在这
之后清空了 docs 行，`_finish_ok` 抛 E4041 回滚、任务记 rejected，可这份刚写的
zip 没人清——GET 404、内容不可寻址，字节却永久占盘（FR-128「删净产物」承诺在
无人值守竞争路径下失效）。

本组用例把竞争用确定性单线程复现（与 test_ch476 同方法论）：run_batch 替身
「转换完成后、返回结果前」调真实 delete_doc，并把产物写到内容寻址名
`out/{doc_id}.zip`（delete 之后再落盘，正是真实时序）。断言修复后该文件被清、
且不误删**仍存活文档**的同名产物（数据安全护栏）。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from doc2md import worker
from doc2md.ir import IR, Block
from doc2md.scheduler import Scheduler
from doc2md.storage import repo
from doc2md.storage.db import Store

pytestmark = [pytest.mark.milestone("DoD")]


def _ir(doc_id: str, bid: str, text: str) -> IR:
    ir = IR(doc_id=doc_id, source_key="s1", source_name="x.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id=bid, type="paragraph", text=text, page=1)]
    return ir


def _write_zip(root: Path, doc_id: str, ir: IR) -> Path:
    zp = root / "out" / f"{doc_id}.zip"
    zp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("ir.json", json.dumps(ir.to_dict()))
    return zp


# --- work 路径：转换产物成为孤儿 --------------------------------------------------


@pytest.mark.gate
def test_worker_orphan_zip_purged_on_e4041(tmp_cfg, tmp_path, monkeypatch):
    """删除落地后、pool 把 zip 写进 out/{doc_id}.zip → E4041 回滚必须顺带清掉这份孤儿。

    原实现只回滚库面（docs/blocks 不复活），产物文件留下：GET 404 但字节占盘，
    违反 FR-128「级联删净产物」。
    """
    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    repo.save_ir(store, _ir("d1", "b1", "既有正文"))

    src = root / "x.pdf"
    src.write_bytes(b"%PDF-1.7\n")

    sched = Scheduler(store)
    sched.submit("d1", "s1", source_path=str(src), mode=None)

    def racing_batch(files, **kw):
        # 真实 delete_doc 级联（收走 docs + tasks），随后 pool 把产物落盘——
        # 复刻「删除在转换完成之前落地、pool 写完才返回」的真实时序。
        repo.delete_doc(store, "d1", "tester", root)
        zp = _write_zip(root, "d1", _ir("d1", "b9", "复活正文"))
        return [{"file": str(src), "status": "ok", "zip": str(zp),
                 "blocks": 1, "doc_id": "d1", "code": "", "message": ""}]

    monkeypatch.setattr(worker, "run_batch", racing_batch)
    rep = worker.drain_once(store, tmp_cfg, root, workers=1)

    assert rep.drained == 1 and rep.rejected == 1 and rep.done == 0, rep.to_dict()
    assert store.q1("SELECT COUNT(*) AS c FROM docs WHERE doc_id='d1'")["c"] == 0
    # 孤儿产物必须被清（本条是修复前会失败的红线）
    assert not (root / "out" / "d1.zip").exists(), "删除竞争后 out/d1.zip 成为孤儿残留"
    store.close()


@pytest.mark.gate
def test_worker_keeps_live_doc_artifact_on_e4041(tmp_cfg, tmp_path, monkeypatch):
    """数据安全护栏：E4041 只因任务行消失（docs 行仍存活）时，不得误删存活文档的产物。

    mark rowcount=0 也可能来自「task 行单独没了、doc 仍在库」。此时 out/{doc_id}.zip
    是**活文档**的正文载体，清理它等于删数据——修复只能针对「doc 确已不在库」的孤儿。
    """
    root = tmp_path / "work"
    (root / "logs").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    store = Store(root / "state.sqlite")
    repo.save_ir(store, _ir("alive", "b1", "活文档正文"))

    src = root / "y.pdf"
    src.write_bytes(b"%PDF-1.7\n")

    sched = Scheduler(store)
    sched.submit("alive", "s1", source_path=str(src), mode=None)

    def racing_batch(files, **kw):
        # 只收走 task 行（docs 仍在库）→ mark 返回 False → E4041，但文档存活。
        with store.tx() as t:
            t.execute("DELETE FROM tasks WHERE doc_id='alive'")
        zp = _write_zip(root, "alive", _ir("alive", "b1", "活文档正文"))
        return [{"file": str(src), "status": "ok", "zip": str(zp),
                 "blocks": 1, "doc_id": "alive", "code": "", "message": ""}]

    monkeypatch.setattr(worker, "run_batch", racing_batch)
    rep = worker.drain_once(store, tmp_cfg, root, workers=1)

    assert rep.rejected == 1, rep.to_dict()
    assert store.q1("SELECT COUNT(*) AS c FROM docs WHERE doc_id='alive'")["c"] == 1
    # 活文档产物绝不能被清
    assert (root / "out" / "alive.zip").exists(), "误删了仍存活文档的产物"
    store.close()


# --- api 同步 /jobs 路径：同序孤儿清理 --------------------------------------------


@pytest.mark.gate
def test_api_orphan_zip_purged_on_e4041(api_client, headers, monkeypatch):
    """同步转换落盘 out/{doc_id}.zip 后删除命中墓碑 → E4041 → 孤儿产物须被清。

    与 work 路径同缺口：`_finish_one` 回滚只保住了库面，进程内 convert 刚写的
    zip 留在盘上。api 面 E4041 只经墓碑命中（`doc_id` 必已出 `docs`），故无需
    另设「活文档」护栏用例（那条路径不可达）。
    """
    import importlib
    from types import SimpleNamespace

    client, tokens = api_client
    holder = client.application.config["doc2md"]
    store, root = holder["store"], Path(holder["root"])
    (root / "out").mkdir(parents=True, exist_ok=True)
    repo.save_ir(store, _ir("d1", "b1", "既有正文"))

    src = root / "race.pdf"
    src.write_bytes(b"%PDF-1.7\n")

    api_mod = importlib.import_module("doc2md.api")

    def racing_convert(p, **kw):
        repo.delete_doc(store, "d1", "tester", root)
        _write_zip(root, "d1", _ir("d1", "b9", "复活正文"))
        return SimpleNamespace(ir=_ir("d1", "b9", "复活正文"),
                               doc_id="d1", source_key="s1")

    monkeypatch.setattr(api_mod, "convert", racing_convert)
    r = client.post("/jobs", json={"path": str(src)}, headers=headers(tokens, "editor"))
    assert r.status_code == 202, r.get_data(as_text=True)[:300]
    job = r.get_json()["jobs"][0]
    assert job["status"] == "rejected" and job["code"] == "E4041", job
    assert store.q1("SELECT COUNT(*) AS c FROM docs WHERE doc_id='d1'")["c"] == 0
    assert not (root / "out" / "d1.zip").exists(), "删除竞争后 out/d1.zip 成为孤儿残留"
