"""tests/test_ch497_local.py — CH-497 单池复用与长跑夹具红线。

- B1：`run_batch` 接受逐作业 (path, mode, force) 计划，一串作业只建一个池；
  `_claim_batch` 每次 drain 只调一次，不再按 (mode, force) 分组重建池。
- B-12：`ops/bench/soak_resident.py` 可独立执行、不被 pytest 收集、带 JSON 出口。
"""
from __future__ import annotations

import concurrent.futures as cf
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakePool:
    built = 0
    submissions: list[tuple[str, str | None, bool]] = []

    def __init__(self, **kw):
        type(self).built += 1
        self._processes = {}

    def submit(self, fn, path, cfg, mode, out, force, precheck, skip):
        type(self).submissions.append((path, mode, force))
        fut = cf.Future()
        fut.set_result({"file": path, "status": "ok"})
        return fut

    def shutdown(self, **kw):
        pass


@pytest.fixture
def fake_pool(monkeypatch):
    _FakePool.built = 0
    _FakePool.submissions = []
    # CH-497：`run_batch` 在调用时局部导入 ProcessPoolExecutor（保持
    # tests/test_ch473_fixes.py 的 cf patch 口径），故此处 patch 模块属性。
    monkeypatch.setattr(cf, "ProcessPoolExecutor", _FakePool)
    return _FakePool


@pytest.mark.gate
def test_b1_plans_share_one_pool(fake_pool):
    from doc2md.pipeline import batch as batch_mod

    rows = batch_mod.run_batch(
        cfg={"perf": {}}, workers=3,
        plans=[("/a.pdf", "single", True),
               ("/b.pdf", "two_phase", False),
               ("/c.pdf", None, False)])
    assert fake_pool.built == 1, "同一批作业必须共用一个进程池（B1）"
    assert fake_pool.submissions == [
        ("/a.pdf", "single", True),
        ("/b.pdf", "two_phase", False),
        ("/c.pdf", None, False)]
    assert sorted(r["file"] for r in rows) == ["/a.pdf", "/b.pdf", "/c.pdf"]


@pytest.mark.gate
def test_b1_paths_keep_global_mode(fake_pool):
    from doc2md.pipeline import batch as batch_mod

    batch_mod.run_batch(["/a.pdf", "/b.pdf"], cfg={"perf": {}}, workers=2,
                        mode="two_phase", force=True)
    assert fake_pool.built == 1
    assert fake_pool.submissions == [("/a.pdf", "two_phase", True),
                                     ("/b.pdf", "two_phase", True)]


@pytest.mark.gate
def test_b1_paths_and_plans_are_exclusive():
    from doc2md.pipeline import batch as batch_mod

    with pytest.raises(ValueError):
        batch_mod.run_batch(["/a.pdf"], plans=[("/a.pdf", None, False)])


@pytest.mark.gate
def test_b1_mixed_modes_real_pool(tmp_path, cfg, golden_dir):
    """真实进程池：两种档位混合入参可在同一真实进程池各产出一行。

    逐作业参数是否真正透传到 worker，由 `test_b1_plans_share_one_pool` 的假池
    提交断言（及 tests/test_ch496_local.py）钉住；成功行不带 `mode` 键，故本
    用例不单独证明档位透传。
    """
    from doc2md.pipeline.batch import run_batch

    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    shutil.copy(golden_dir / "small_3p.pdf", a)
    shutil.copy(golden_dir / "numlist_1p.pdf", b)
    rows = run_batch(cfg=cfg, out_dir=tmp_path / "out", workers=2,
                     plans=[(a, "single", False), (b, None, False)])
    assert {r["status"] for r in rows} == {"ok"}, rows
    assert {Path(r["file"]).name for r in rows} == {"a.pdf", "b.pdf"}
    assert all(r.get("doc_id") for r in rows), rows


@pytest.mark.gate
def test_b12_soak_harness_contract():
    """B-12 夹具必须可独立执行、不被 pytest 收集、参数与 JSON 出口稳定。"""
    path = REPO_ROOT / "ops" / "bench" / "soak_resident.py"
    src = path.read_text(encoding="utf-8")
    assert "__main__" in src and "import pytest" not in src
    assert '"SUMMARY "' in src
    proc = subprocess.run([sys.executable, str(path), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    for flag in ("--minutes", "--interval", "--root", "--keep"):
        assert flag in proc.stdout, (flag, proc.stdout[:400])
