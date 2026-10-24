"""tests/test_ch498_drain_e2e.py — CH-498 Task 2：drain_once → 真实进程池的端到端缝线。

CH-497 已把缝线两侧各自钉死（`_claim_batch` 侧用假池断 `run_batch(cfg=…, out_dir=…,
workers=…, plans=…)` 的调用形态；`run_batch` 侧用真实池断混合档位各产出一行），
中间这段「真实入队 → 认领 → 单池 → 产物落库」此前只靠人工夹具。
本项是对既有实现的**固化**（不改产品代码）：一次 drain 建了多个池、池宽没传到缝线、
档位没逐作业传到池、块行没真正落库、产物与库面状态不一致或 zip 名与 docs 行内容
寻址 id 对不上，都会把它打红。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from doc2md import worker
from doc2md.artifacts import artifact_entry
from doc2md.scheduler import Scheduler


@pytest.mark.gate
def test_drain_once_builds_one_real_pool_and_persists_artifacts(
        store, tmp_cfg, tmp_path, golden_dir, monkeypatch):
    """整次 drain 只建一个真实进程池；逐作业档位与产物贯通「入队→认领→落库」全缝。

    5a–5d 段分别钉死：池宽 workers=2 经缝线传到、两计划两档位、块行按 doc 分组
    真实落库、zip 名与 docs 行的内容寻址 id 互证且 content.md 非空白。
    """
    # --- 1. 两份真语料拷进 tmp_path：默认档 + 显式 "single" 档 -------------------
    small = tmp_path / "small_3p.pdf"
    numlist = tmp_path / "numlist_1p.pdf"
    shutil.copy(golden_dir / "small_3p.pdf", small)
    shutil.copy(golden_dir / "numlist_1p.pdf", numlist)

    # --- 2. 真实入队：doc_id 自定义即可（同路径单作业时 matched=None 走
    #     「全组处理」语义，见 doc2md/worker.py _finish_ok 之前的注释）；
    #     store 用 conftest 夹具，teardown 负责 close -----------------------------
    sched = Scheduler(store)
    sched.submit("doc-small", source_path=str(small), mode=None)
    sched.submit("doc-numlist", source_path=str(numlist), mode="single")

    # --- 3. 计数转发器包一层真实 run_batch：必须 patch `worker` 的模块属性，
    #     `_claim_batch` 才走得到替身（返回真实实现的结果，产物真落盘） ----------
    calls: list[dict] = []
    real = worker.run_batch

    def counting(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(worker, "run_batch", counting)

    # --- 4. 真实 drain：整池执行的是产品代码，不经任何假池 ----------------------
    rep = worker.drain_once(store, tmp_cfg, tmp_path, workers=2)

    # --- 5a. 整次 drain 只建一个池；池宽经缝线传到；两条计划、两个档位 ------------
    assert len(calls) == 1, f"一次 drain 必须且只能调一次 run_batch：{calls}"
    assert calls[0]["workers"] == 2, calls[0]
    plans = calls[0]["plans"]
    assert len(plans) == 2, plans
    assert {p[1] for p in plans} == {None, "single"}, plans
    assert {Path(str(p[0])).name for p in plans} == {"small_3p.pdf", "numlist_1p.pdf"}

    # --- 5b. 库面：2 done / 0 failed（notes 进断言消息），队列无在途残留，docs=2 --
    assert rep.done == 2 and rep.failed == 0, (rep.to_dict(), rep.notes)
    assert store.q1("SELECT COUNT(*) c FROM tasks"
                    " WHERE status IN ('queued','running')")["c"] == 0
    assert store.q1("SELECT COUNT(*) c FROM docs")["c"] == 2

    # --- 5c. 块行确实落库且关联到 docs：GROUP BY 必须交出两组，
    #     单个 doc 吞掉全部块也逃不过这里 ------------------------------------------
    blk = store.q("SELECT b.doc_id, COUNT(*) c FROM blocks b"
                  " JOIN docs d ON d.doc_id = b.doc_id GROUP BY b.doc_id")
    assert len(blk) == 2 and all(r["c"] >= 2 for r in blk), [tuple(r) for r in blk]

    # --- 5d. 产物：两份 zip 落在 _out_dir(cfg, root) 下；zip 名 ↔ docs 行
    #     同 id 互证（产物名即 {ir.doc_id}.zip，入队自定义 id 已被内容寻址 id 替换），
    #     包内 content.md 非空白 ---------------------------------------------------
    out_dir = worker._out_dir(tmp_cfg, tmp_path)
    zips = sorted(out_dir.glob("*.zip"))
    assert len(zips) == 2, (str(out_dir), [str(z) for z in zips])
    assert {r["doc_id"] for r in store.q("SELECT doc_id FROM docs")} == {z.stem for z in zips}
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            entry = artifact_entry(z.namelist(), "content.md", zp.stem)
            assert entry, f"产物缺 content.md 条目：{zp} {z.namelist()}"
            assert z.read(entry).strip(), f"产物 content.md 为空：{zp}"
