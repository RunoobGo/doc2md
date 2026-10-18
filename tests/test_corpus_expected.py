"""综合语料回归：PDF/DOCX 源文件 ↔ tests/corpus/expected 目标 Markdown。

独立于 tests/golden 的逐字节门禁；本测试验证“结构/语义效果”，失败时实际输出
写到 work/corpus_diff/。语料生成与口径见 tests/corpus/README.md。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.corpus]


def test_corpus_expected_markdown():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "tests" / "corpus" / "run_corpus.py")],
        cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]
