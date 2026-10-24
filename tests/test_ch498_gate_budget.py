"""CH-498 / §9.1 门禁分层复核：把「慢项清单不外扩」变成可执行红线。

定档结论（证据见 dev-plan 9.1「门禁耗时」行）：本机 macOS arm64 静置树 518 项实测
39.98s（`--junitxml` 用例耗时求和 39.53s），但同一批在 windows-latest 的 gate 腿
116.42s 级——**「< 60s」不能恢复为硬判据**，否则就是一条按机器快慢变色的断言，
违反本项目「主机相关判定必须写在产品/测试口径里、不许 skip」的纪律。硬口径仍是项数，
耗时作为「量级」记录。

因此耗时面唯一能静态守住的不是「多少秒」，而是**是什么在花时间**：门禁的墙钟几乎全
压在真实转换上——7 项请求 `large_*`（320 页金样本）夹具的 gate 项吃掉 21.99s
（求和的 55.6%），其中只有 2 项真正付费（session 级转换由首个请求者承担），其余 5 项
复用缓存 ≈0s。本文件冻结这份清单：新的慢项进 gate 必须显式改这张表，即「扩容决策
显式化」；两条付费项也不许被静默撤掉——它们是低配档端到端产物唯一的红线证明。

形态是纯源码 AST 扫描（毫秒级、无子进程、不依赖机器快慢）；盲区：模块内自定义夹具
间接拉起重转换、或 `request.getfixturevalue("large_sharded")` 这类动态取名，静态扫描
看不到——所以收口口径仍要求每轮跑一次 `--durations` 归因，发现构成漂移即回填此表。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# 320 页金样本的转换入口（tests/conftest.py）：session 级，一次转换多处复用
HEAVY_FIXTURES = frozenset(
    {"large_pdf", "large_single", "large_sharded", "large_zip_names"}
)

# 冻结的慢项清单（file :: 测试函数名，不含参数化 id）。新增/删除都要连带改 9.1 的定档行。
HEAVY_GATE_ITEMS = frozenset({
    ("test_ch483_pipeline.py", "test_golden_320p_has_all_chapter_headings"),
    ("test_perf_lowend.py", "test_product_zip_complete"),
    ("test_v24_gates.py", "test_product_zip_entries"),
    ("test_v24_gates.py", "test_shard_edge_filterable_in_report"),
    ("test_v24_gates.py", "test_shard_overlap_dedupe_no_duplicate_ids"),
    ("test_v24_gates.py", "test_shard_pages_not_double_counted"),
    ("test_v24_gates.py", "test_shard_two_phase"),
})

# 付费承担 session 转换的两项：撤掉任何一条等于低配档真实产物失去红线证明
PAID_PROOFS = frozenset({
    ("test_ch483_pipeline.py", "test_golden_320p_has_all_chapter_headings"),
    ("test_perf_lowend.py", "test_product_zip_complete"),
})


def _is_gate(node: ast.FunctionDef | ast.AsyncFunctionDef, module_gate: bool) -> bool:
    if module_gate:
        return True
    for dec in node.decorator_list:
        attr = getattr(dec, "attr", None) or getattr(getattr(dec, "func", None), "attr", None)
        if attr == "gate":
            return True
    return False


def _heavy_gate_items() -> set[tuple[str, str]]:
    """扫 tests/test_*.py，交出「gate 且签名请求了 large_* 夹具」的项。"""
    out: set[tuple[str, str]] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_gate = False
        for stmt in tree.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(getattr(t, "id", "") == "pytestmark" for t in stmt.targets):
                continue
            module_gate = "gate" in ast.dump(stmt.value)
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test_") or not _is_gate(fn, module_gate):
                continue
            args = {a.arg for a in fn.args.args}
            if args & HEAVY_FIXTURES:
                out.add((path.name, fn.name))
    return out


@pytest.mark.gate
def test_gate_heavy_items_are_frozen_and_documented():
    """慢项清单不外扩（9.1 定档的静态形态），且两条付费证明仍在 gate 里。"""
    live = _heavy_gate_items()
    assert live, "未扫到任何慢项——conftest 的 large_* 夹具改名或本文件扫描形态漂移"
    extra = live - HEAVY_GATE_ITEMS
    assert not extra, (
        f"新的重型 gate 项 {sorted(extra)} 会推高门禁墙钟：320 页真实转换是门禁耗时的"
        "主要来源。要么它确实该在 merge-gate 里（连同 dev-plan 9.1 定档行一起加进本表，"
        "并复跑 --durations 记录构成），要么移出 gate（改标 milestone/非 gate）。"
    )
    missing = PAID_PROOFS - live
    assert not missing, f"两条付费红线证明不得离开 gate：{sorted(missing)}"
