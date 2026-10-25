#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""难度语料的结构面断言腿（B-46 / OPT-17）。

分工（三份资产，不得混淆）：
  - `tests/qa/test_hard_corpus.py`：`gen_cases.py --pytest` 的自动骨架，只核
    `contains` / `not_contains`，无缺口时 skip ⇒ **永远不可能真 PASS**（O-g）；
  - `tests/corpus/run_hard.py`：信息层对账器，核结构面，**不被 pytest 收集**，退 1 只给人看；
  - 本文件：把结构面搬进 pytest，成为可判定断言。已知产品缺口用
    `xfail(strict=True, reason="B-4x")` 钉住——缺口修好后 XPASS 会转红，逼人在
    同一批里删掉 xfail，而不是让「已知缺陷」永久免检。

标记为 `corpus` 而非 `gate`：OPT-17 的排序规则是「先把断言落盘但标记 corpus，
B-43 / B-44 闭合的同一批切 gate」。切换只改本文件的 `GATE_READY` 一处。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HARD = REPO / "tests" / "corpus" / "hard"
sys.path.insert(0, str(REPO))

from tests.qa import mdscore  # noqa: E402

#: 切 gate 的唯一开关：B-43 / B-44 闭合同批置 True（`pytestmark` 由本值派生，无需另改）
GATE_READY = False

#: 已知产品缺口 → 登记号。删掉一行 = 声明该缺口已闭合（届时 XPASS 会先转红提醒）
KNOWN_GAPS = {
    "D02": "B-43",   # 跨页表丢一列并裂成两张表（发布阻断）
    "D05": "B-44",   # 目录引导符成单元格 + `第一章 总则` → `第一章总则`（发布阻断）
    "D08": "B-45",   # 层级冲突：生成器声明 H1，产品按 CJK 序号映射给 H2（待裁定）
    "D09": "B-48",   # TOC 域缓存结果被当正文标题重复输出（本轮新增登记）
    "D10": "B-47",   # 乱码文本层契约未裁定（拒收 or 新增 W 码）
}

pytestmark = pytest.mark.gate if GATE_READY else pytest.mark.corpus


def _run_hard():
    """按路径加载 `tests/corpus/run_hard.py`（该目录不是包，不能 import）。"""
    path = REPO / "tests" / "corpus" / "run_hard.py"
    spec = importlib.util.spec_from_file_location("d2m_run_hard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _specs() -> list:
    if not (HARD / "expectations.json").is_file():
        return []
    return json.loads((HARD / "expectations.json").read_text(encoding="utf-8"))


def _params():
    specs = _specs()
    assert specs, f"{HARD / 'expectations.json'} 缺失或为空：拒绝静默收集 0 项难度样本"
    out = []
    for s in specs:
        marks = ()
        gap = KNOWN_GAPS.get(s["difficulty"])
        if gap:
            marks = (pytest.mark.xfail(strict=True, reason=f"{gap} 未闭合"),)
        out.append(pytest.param(s, marks=marks, id=s["difficulty"]))
    return out


def extra_checks(spec: dict, md: str, res) -> list[tuple[str, bool, str]]:
    """run_hard 不核的结构面键（新增键只在此实现，避免两处维护）。"""
    exp = spec.get("expect", {})
    rows: list[tuple[str, bool, str]] = []

    if exp.get("no_duplicate_headings"):
        heads = [b for b in mdscore.parse_blocks(md) if b.type == "heading"]
        if not heads:
            rows.append(("no_duplicate_headings", False, "产物无标题"))
        else:
            # 只剥行尾「制表位 + 页码」（TOC 引导符，如 `第一章 总则\t1`）；标题非行尾的
            # 制表位按空白保留，由 slugger 归一为 `-`，不得把它当截断点。
            slugs = [mdscore._slugify_heading(re.sub(r"\t\s*\d+\s*$", "", b.text))
                     for b in heads]
            dup = sorted({s for s in slugs if slugs.count(s) > 1})
            texts = [b.text for b, s in zip(heads, slugs) if s in dup]
            rows.append(("no_duplicate_headings", not dup,
                         f"重复锚点 {texts}" if dup else f"{len(heads)} 个标题锚点唯一"))

    want_w = exp.get("warnings_include_any")
    if want_w:
        got = list((res.meta or {}).get("warnings", []) or []) if res else []
        hit = [w for w in want_w if any(w in g for g in got)]
        rows.append(("warnings_include_any", bool(hit),
                     f"命中 {hit} / 实际 {got}" if hit else f"未命中，实际告警 {got}"))

    if exp.get("warnings_empty") and res is not None:
        got = list((res.meta or {}).get("warnings", []) or [])
        rows.append(("warnings_empty", not got, f"应无告警，实际 {got}" if got else "无告警"))
    return rows


def test_extra_checks_tab_cut():
    """合成 markdown：只剥行尾「制表位 + 页码」，其余制表位按空白参与锚点。"""
    spec = {"expect": {"no_duplicate_headings": True}}

    def ok(md: str) -> bool:
        rows = extra_checks(spec, md, None)
        assert len(rows) == 1 and rows[0][0] == "no_duplicate_headings", rows
        return rows[0][1]

    assert not ok("# A\n# A\t1"), "行尾制表位 + 页码应剥掉：`# A\\t1` 与 `# A` 重复（D09 形态）"
    assert ok("# A\tB"), "制表位不是行尾页码，不得截断标题"
    assert ok("# A\n# A\tB"), "`# A\\tB` 不得与 `# A` 误判重复"
    assert not ok("# A B\n# A\tB"), "制表位是空白：slugger 归一后应与空格等价"


@pytest.mark.parametrize("spec", _params())
def test_hard_expectations_structure(spec, tmp_path):
    """逐难度样本：真机转换 → 结构面对账（表格 / 标题 / 行为 / 附加键）。"""
    src = HARD / spec["source"]
    assert src.is_file(), (
        f"{spec['source']} 未生成：先跑 .venv/bin/python tests/qa/gen_cases.py --sources")
    sink: dict = {}
    rows = _run_hard().check_spec(spec, tmp_path, sink=sink)
    rows += extra_checks(spec, sink.get("md", ""), sink.get("res"))
    # sink 三条写路径的契约：拒收（D07）/ 期望拒收却转换成功（D10）/ 正常转换（其余）
    want_behavior = spec["expect"].get("behavior")
    if want_behavior and "error_code" in sink:
        assert want_behavior == f"reject:{sink['error_code']}", (
            f"{spec['difficulty']} sink 写了 error_code={sink['error_code']}，"
            f"与期望 {want_behavior} 不符")
    elif want_behavior:
        assert "res" in sink, (
            f"{spec['difficulty']} 期望 {want_behavior} 却转换成功，sink 未写 res")
    else:
        assert sink.get("md"), f"{spec['difficulty']} 正常路径 sink['md'] 为空"
        assert sink.get("res") is not None, f"{spec['difficulty']} 正常路径 sink 未写 res"
    bad = [f"{name}: {msg}" for name, ok, msg in rows if not ok]
    assert not bad, f"{spec['difficulty']} {'; '.join(bad)}"
