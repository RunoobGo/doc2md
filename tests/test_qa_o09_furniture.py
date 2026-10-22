"""O-09（QA-D2M-2026-10-21 / C-02）：页眉变体剔除——`第 N 页第 M 栏` 逐页变体。

`_furniture_key` 只抹「页/栏」量词的数字：真标题量词（章/节）不动；
剔除处的三重约束（边距带 + 字号/短键 + ≥2 页重复）不受本改动影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2md.pipeline.s3_structure import _furniture_key

pytestmark = [pytest.mark.milestone("DoD")]
CORPUS = Path(__file__).parent / "corpus"


def test_furniture_key_masks_page_and_col_variants():
    assert _furniture_key("第 1 页第 1 栏 第 1 页第 2 栏") == "## ##"
    assert _furniture_key("第12栏") == "#"
    assert _furniture_key("第 3 页 / 第 4 栏") == "# / #"


def test_furniture_key_does_not_mask_heading_quantifiers():
    """宁漏勿误：`第 1 章` / `第 2 节` / `第 3 图` 是真标题量词，不得折进 #。"""
    assert _furniture_key("第 1 章 概述") == "第 1 章 概述"
    assert _furniture_key("第 2 节 背景") == "第 2 节 背景"
    assert _furniture_key("第 3 图 结果") == "第 3 图 结果"


def test_furniture_key_keeps_existing_page_num_semantics():
    assert _furniture_key("第 1 页 共 8 页") == "#"
    assert _furniture_key("— 3 —") == "#"


def test_pdf10_corpus_has_no_column_header_furniture():
    """gate（C-02）：pdf_10 产物零栏标题页眉；全语料同理。"""
    expected = CORPUS / "expected"
    hits = []
    for f in sorted(expected.glob("*.md")):
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and "第" in s and "栏" in s and _furniture_key(s) in ("#", "##", "## ##"):
                hits.append(f"{f.name}: {s[:40]}")
    assert not hits, f"栏标题页眉泄漏进产物：{hits}"
