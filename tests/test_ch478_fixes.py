"""CH-478 批次① 的回归：R6-PIPE-1 的端到端收口。

登记判据（12.7 的 R6-PIPE-1 行）写的是「修复后归 list_item」，CH-475 只做到「不再
误判 H1」——编号行落进了缝合段落，逐行边界丢失。本批次补上 PDF 有序列表分支，并把
黄金样本 `numlist_1p.pdf` 作为端到端反例钉住两件事：同字号编号行不得是标题，且同页
三类真标题信号（大字号 / 多级编号 / CJK 章節目条）照旧成立。
"""

import pytest

from doc2md.pipeline.convert import convert
from doc2md.pipeline.s3_structure import _is_ordered_item
from doc2md.pipeline.s4_render import render

pytestmark = [pytest.mark.milestone("DoD")]


# ---------------------------------------------------------------- 端到端反例

@pytest.fixture(scope="module")
def numlist_ir(golden_dir):
    return convert(golden_dir / "numlist_1p.pdf").ir


@pytest.mark.gate
def test_same_size_numbered_items_are_list_items_not_headings(numlist_ir):
    """「1./2./3.」三行：字号==正文众数、无加粗 → 既不是标题也不被缝进段落。"""
    heads = {b.text for b in numlist_ir.blocks if b.type == "heading"}
    for item in ("1. 安装依赖", "2. 配置环境变量", "3. 启动服务"):
        assert item not in heads, f"{item} 复现了 R6-PIPE-1 的假 H1"
    kinds = {b.text: b.type for b in numlist_ir.blocks}
    assert all(kinds.get(i) == "list_item" for i in ("1. 安装依赖",
                                                     "2. 配置环境变量",
                                                     "3. 启动服务")), \
        f"编号行未落 list_item：{kinds}"
    # 缝合丢失的形态：三行粘成一个块（旧口径）——三件必须各自成块
    assert sum(1 for b in numlist_ir.blocks if b.type == "list_item") == 3


@pytest.mark.gate
def test_real_heading_signals_still_fire(numlist_ir):
    """同页三类真标题对照组照旧判标题——门不是「同字号一律不判标题」。"""
    got = {(b.text, b.level) for b in numlist_ir.blocks if b.type == "heading"}
    assert ("第一章 绪论", 1) in got      # CJK 章節目条，同字号仍判 H1
    assert ("1.1 环境要求", 2) in got     # 多级编号，同字号仍判 H2
    assert ("2. 运行说明", 1) in got      # 字号 14 ≥ 众数+3.5，走第一分支


@pytest.mark.gate
def test_markdown_keeps_ordinal_for_numbered_items(numlist_ir):
    """有序列表落 Markdown 保留序号（GFM 有序表），不退化成符号 bullet。"""
    md = render(numlist_ir)
    for line in ("1. 安装依赖", "2. 配置环境变量", "3. 启动服务"):
        assert f"\n{line}\n" in f"\n{md}", md
    assert "- 1. 安装依赖" not in md


@pytest.mark.gate
def test_prose_lines_do_not_enter_the_ordered_branch(numlist_ir):
    """同页正文行（行末句号 / 长行）不得被误收进列表——新分支只吃列表形态。"""
    prose = [b.text for b in numlist_ir.blocks if b.type == "paragraph"]
    assert any(t.startswith("本节说明本样本的用途") for t in prose)
    assert any(t.startswith("运行环境需要") for t in prose)
    assert len([t for t in prose if t.startswith("正文第")]) == 12
    assert not any("安装依赖" in t for t in prose)


# ---------------------------------------------------------------- 判据本体

@pytest.mark.parametrize("text", [
    "1. 安装依赖",
    "12、配置环境变量",
    "3）启动服务",
    "4．启动服务",          # 全角句点
    "1.安装依赖",           # 分隔符后无空格仍是列表项
])
def test_ordered_item_positive_forms(text):
    assert _is_ordered_item(text)


@pytest.mark.parametrize("text", [
    "3.5 节的说明在此展开",      # 分隔符后紧跟数字 = 多级编号，不是列表
    "2019. 年度报告要点",        # 四位年份不是序号
    "1. 这一行长度已经明显超过三十个字符所以它按段落处理而不是列表项",
    "1. 安装依赖并验证。",        # 句末标点 = 句子
    "本节说明本样本的用途：编号列表项与正文字号相同时不得误判为标题。",
    "• 符号 bullet 走 _BULLET 分支",
    "第一章 绪论",
    "",
    "   ",
])
def test_ordered_item_negative_forms(text):
    assert not _is_ordered_item(text)
