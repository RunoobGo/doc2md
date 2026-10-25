"""难度语料回归（自动生成骨架）。

由 `tests/qa/gen_cases.py --pytest` 生成。断言值需人工按 expectations.json
的 `judge` 口径确认后填入——**不得**直接把当前产物当期望值写死
（那是 C-31 的自啮合陷阱：只能证明稳定，不能证明对）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "corpus"
HARD = CORPUS / "hard"
sys.path.insert(0, str(HERE.parents[1]))

SPECS = json.loads((HARD / "expectations.json").read_text(encoding="utf-8")) if (HARD / "expectations.json").is_file() else []

@pytest.mark.corpus
@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s["source"])
def test_hard_corpus(spec, tmp_path):
    """逐难度样本：跑转换并对账目标声明。"""
    from doc2md.errors import Doc2mdError
    from doc2md.pipeline.convert import convert
    src = HARD / spec["source"]
    if not src.is_file():
        pytest.skip(f"{spec['source']} 未生成")
    try:
        res = convert(src, out_dir=tmp_path, force=True)
    except Doc2mdError as exc:
        want = spec["expect"].get("behavior")
        assert want == f"reject:{exc.code}", \
            f"{spec['source']}: 期望 {want}，实际 {exc.code}"
        return
    md = res.markdown
    exp = spec["expect"]
    gaps: list[str] = []
    for token in exp.get("contains", []):
        if token not in md:
            gaps.append(f"缺关键内容 {token!r}")
    for token in exp.get("not_contains", []):
        if token in md:
            gaps.append(f"残留噪声 {token!r}")
    if gaps:
        # 已知缺陷走 xfail（不转红、但 CI 输出可见），口径见
        # docs/qa/eval-report-v2.md；缺陷修复后应立即转为红断言。
        pytest.xfail(f"{spec['difficulty']} 已知缺陷: " + "; ".join(gaps))
    pytest.skip(f"TODO 未填断言: {spec['difficulty']} —— {spec['judge']}")
