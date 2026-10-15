"""FR-111 大文件拷贝场景（CH-443）。

方案对 M3 的验收写的是「向 watch 目录拷贝 300MB 文件的过程中，服务不在拷贝中途入队」，
黄金集里对应资产是 tests/golden/bigfile.bin（120MB）。实测该文件**没有任何测试消费**，
即这条验收实际处于无人判定状态；而资产本身既占 120MB 又与方案标称 300MB 不符。

处置：把判定搬进测试（真写盘 + 注入时钟，确定性、可重跑），
bigfile.bin 降级为「可选资产」——由生成器按需现补，分发包不再内置。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.security.dirguard import WalkLimits
from doc2md.security.watcher import StabilityTracker

pytestmark = [pytest.mark.milestone("M3")]

CHUNK = 8 * 1024 * 1024          # 8MB / 步
STEPS = 6                        # 48MB 总写入量（大文件场景，仍秒级完成）


def test_large_copy_never_enqueues_mid_write(tmp_path):
    """拷贝期间（大小持续变化）绝不入队；写完后连续 stable_seconds 静默才入队一次。"""
    f = tmp_path / "incoming.bin"
    tr = StabilityTracker(stable_seconds=5.0)
    now = 1000.0
    enqueued_during_copy = []

    with f.open("wb") as fh:
        for i in range(STEPS):
            fh.write(b"x" * CHUNK)
            fh.flush()
            now += 1.0                                    # 每秒一步，间隔 < stable_seconds
            if tr.observe(f, now):
                enqueued_during_copy.append((i, tr.pending[str(f)]))
    assert enqueued_during_copy == [], "拷贝中途入队（稳定性判定失效）"
    assert f.stat().st_size == CHUNK * STEPS

    # 写完 → 第一个观察点只登记稳定窗口起点，尚不入队
    now += 1.0
    assert tr.observe(f, now) is False
    # 静默满 5s → 入队，且只入队一次
    now += 5.0
    assert tr.observe(f, now) is True
    now += 10.0
    assert tr.observe(f, now) is False
    assert list(tr.enqueued) == [str(f)]   # CH-464：enqueued 由 list 改 dict（提交时戳）


def test_large_copy_via_poll_all(tmp_path):
    """走 watch 实际入口（poll_all → guard_walk），同样不得在增长期入队。"""
    watch = tmp_path / "watch"
    watch.mkdir()
    tr = StabilityTracker(stable_seconds=5.0)
    now = 0.0
    seen = []
    with (watch / "big.bin").open("wb") as fh:
        for _ in range(3):
            fh.write(b"y" * CHUNK)
            fh.flush()
            now += 2.0
            seen += tr.poll_all(watch, WalkLimits(), now)
    assert seen == []
    now += 6.0
    assert tr.poll_all(watch, WalkLimits(), now) == [str(watch / "big.bin")]


def test_golden_bigfile_is_optional_and_regenerable(golden_dir):
    """资产纪律：bigfile.bin 不随分发包走时，测试面不得依赖它（缺失即自补）。"""
    p = golden_dir / "bigfile.bin"
    if not p.exists():
        pytest.skip("bigfile.bin 未随包分发（按纪律省略，可用 make_golden.py 现补）")
    assert p.stat().st_size >= 100 * 1024 * 1024, "大文件样本体积异常"
    assert "bigfile.bin" in {f.name for f in golden_dir.iterdir()}


def test_bigfile_is_not_a_mandatory_fixture():
    """CH-443：大文件不得成为任何用例的**强制前置**。

    conftest 里的 `_need()` 缺失即 fail，属于强制；只允许显式 skip 的「可选容忍」用法，
    因此点名 bigfile 的文件必须在白名单内（只有本文件的可选断言与清单复核用例）。
    """
    tests = Path(__file__).resolve().parent
    conftest = (tests / "conftest.py").read_text(encoding="utf-8")
    assert "bigfile" not in conftest, "conftest 把 bigfile 变成了用例前置"
    allow = {Path(__file__).name, "test_golden_repro.py"}
    named = {f.name for f in tests.glob("test_*.py")
             if "bigfile" in f.read_text(encoding="utf-8")}
    assert named <= allow, f"新用例把大文件当隐性前置：{sorted(named - allow)}"
