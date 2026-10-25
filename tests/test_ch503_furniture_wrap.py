r"""CH-503（第十八轮）R18-PIPE-2：页脚信号不得吃掉跨页折行的半句正文。

缺陷形态（320 页金样本实测）：`_strip_page_furniture` 的信号 3「同一边距行在 ≥2 页
重复 + 字号不高于正文或键长 ≤24」只看**这一行自己**，不看它是不是上一句正文的后半截。
长文档里同一句式反复出现（「准流程。」「说明。」「标准流程。」），跨页断句折到页顶/页边
带的后半截就满足全部条件，被当页脚**整行删掉**——丢的是正文的一半，前后两句还被接成
一段，产物里不留任何痕迹。金样本上 7 个片区间歇性翻掉判定，`5770 → 5789` 块、
正文 `1775236 → 1775484` 字节（`ops/bench/opt6_shard_equivalence.py` D4 面复现）。

本文件守三侧，缺一侧都会让修复退化：
① 折行尾巴必须**保留**（否则继续静默丢字）；
② 真家具必须**仍被剔除**（否则「宁漏勿误」被改成「不再剔除」，页眉页脚回潮）；
③ 豁免面不得只看前一行——`pdf_09` 的运行页眉正好接在无标点正文之后，只凭「前一行
   未硬句末」放行会让页眉回潮并把跨页续段劈成两段（本轮第一版修复即被语料逐字节
   比对当场挡下，故判据收紧为「前一行未硬句末 **且** 本行以硬句末收尾」）。
全部是纯单元断言：手工构造 `RawPage`，不拉语料夹具（`test_ch498_gate_budget` 口径）。
"""

from __future__ import annotations

import pytest

from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.pipeline.s3_structure import (
    _is_wrap_tail,
    _strip_page_furniture,
    analyze,
)

pytestmark = [pytest.mark.gate]

HEIGHT = 1000.0          # 边距带：bottom ≤ 100 或 top ≥ 880（上下 10% / 12%）


def _body(text: str, size: float = 12.0, *, top: float = 300.0) -> Line:
    """页中带的正文行（不进边距带，永不参与家具判定）。"""
    return Line(text, size=size, x0=60.0, top=top, bottom=top + size)


def _banded(text: str, size: float = 10.0, *, top: float = 20.0) -> Line:
    """页顶带内的行——家具信号的候选面。"""
    return Line(text, size=size, x0=60.0, top=top, bottom=top + size)


def _pages(*page_lines: list[Line]) -> list[RawPage]:
    return [RawPage(index=i, lines=lines, height=HEIGHT)
            for i, lines in enumerate(page_lines)]


def _texts(pages: list[RawPage]) -> list[str]:
    return [(ln.text or "") for p in pages for ln in p.lines]


def test_wrap_tail_after_unfinished_sentence_is_kept():
    """① 核心回归：上一句未硬句末时，页顶带里的重复短行必须保留。"""
    pages = _pages(
        [_body("本项作业要遵循的标")],
        [_banded("准流程。"), _body("其余内容保持完整。")],
        [_body("另一段落的正文一半")],
        [_banded("准流程。"), _body("收尾说明。")],
    )
    stripped = _strip_page_furniture(pages, 12.0)
    assert _texts(stripped) == _texts(pages), "跨页折行的后半截被当页脚删掉了"


def test_real_furniture_after_hard_end_is_still_stripped():
    """② 牙齿的另一侧：前一行硬句末时，同一条重复短行仍按页脚剔除。

    行尾点号是故意留的——它证明「挡住它的条件是前一行硬句末」而不是「本行无标点」，
    两个条件各自独立成立（`test_is_wrap_tail_caliber` 逐条钉住）。
    """
    pages = _pages(
        [_body("上一章到此结束。"), _banded("机密文件请勿外传。")],
        [_body("本章说明适用范围。"), _banded("机密文件请勿外传。")],
    )
    stripped = _strip_page_furniture(pages, 12.0)
    kept = _texts(stripped)
    assert "机密文件请勿外传。" not in kept, "真页脚未被剔除（红线判据被改成不存在）"
    assert kept.count("本章说明适用范围。") == 1


def test_first_line_of_document_has_no_wrap_anchor():
    """锚点只在「已有保留行」之后成立：文档首行落在边距带时按家具剔除。"""
    pages = _pages(
        [_banded("准流程。"), _body("正文开始。")],
        [_banded("准流程。"), _body("正文继续。")],
    )
    stripped = _strip_page_furniture(pages, 12.0)
    assert "准流程。" not in _texts(stripped), "无锚点时应当照常剔除（本轮不放宽这一侧）"


def test_wrap_anchor_is_page_order_continuous():
    """页序即阅读序：锚点跨页也连续——尾巴接在**上一页**的半句之后，同样不得判家具。

    这条与 `test_wrap_tail_after_unfinished_sentence_is_kept` 的差别只在锚点位置：
    半句留在上一页、尾巴落在下一页页顶带（真实跨页断句的形态）。短行在两个页面各出现
    一次，重复计数照样达到 2——只有「锚点跨页连续」能救回它。
    """
    pages = _pages(
        [_body("第一段完整结束。"), _body("第二段写到一半")],
        [_banded("准流程。"), _body("第三段又写到一半")],
        [_banded("准流程。"), _body("第四段完整结束。")],
    )
    stripped = _strip_page_furniture(pages, 12.0)
    assert _texts(stripped) == _texts(pages)


@pytest.mark.parametrize(("prev", "cand", "expected"), [
    ("", "准流程。", False),                    # 无锚点（文档首行）
    ("正文写到一半", "准流程。", True),          # 句子被折断 + 本行关上它
    ("正文结束了。", "准流程。", False),         # 前一行硬句末 → 可判家具
    ("正文写到一半", "机密文件请勿外传", False),  # 本行不带硬句末 → 是页眉不是尾巴
    ("软句末如下：", "准流程。", True),          # 冒号是软句末，句子没写完
    ("列表项 b）", "准流程。", True),            # 括号收尾同样不算硬句末
])
def test_is_wrap_tail_caliber(prev: str, cand: str, expected: bool):
    assert _is_wrap_tail(prev, cand) is expected


def test_running_header_without_terminal_punctuation_is_stripped():
    """② 的另一侧（语料 `pdf_09` 当场抓出的过度豁免）：页眉不带句末标点，
    即便接在无标点的正文半句之后也必须照常剔除——否则产物里凭空多出运行页眉，
    跨页续段的两半还会被它劈成两段。"""
    pages = _pages(
        [_body("跨页续段开始：这一行没有句末标点")],
        [_banded("综合长文档测试 内部测试样本"), _body("这一段继续上一页的句子，直到这里才结束。")],
        [_body("又一行没有句末标点的正文")],
        [_banded("综合长文档测试 内部测试样本"), _body("这一行写完了。")],
    )
    stripped = _strip_page_furniture(pages, 12.0)
    kept = _texts(stripped)
    assert "综合长文档测试 内部测试样本" not in kept
    assert kept.count("这一段继续上一页的句子，直到这里才结束。") == 1


def test_analyze_does_not_silently_lose_the_wrap_tail():
    """端到端：修复后 `analyze` 产出把半句并回原段，既不丢字也不错接到别的段。"""
    pages = _pages(
        [_body("本项作业要遵循的标")],
        [_banded("准流程。"), _body("其余内容保持完整。")],
        [_body("另一段落的正文一半")],
        [_banded("准流程。"), _body("收尾说明。")],
    )
    ir = analyze(pages, base_size=12.0)
    joined = "".join(b.text for b in ir.blocks)
    assert "本项作业要遵循的标准流程。" in joined, (
        f"半句未并回原段：{[b.text for b in ir.blocks]}")
    assert "另一段落的正文一半准流程。" in joined
