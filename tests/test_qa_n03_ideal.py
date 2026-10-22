"""tests/test_qa_n03_ideal.py — N-3（QA-OPT-2）：ideal 标注真值链路。

首轮 QA 遗留（N-3，P1）：`heading_pr` 仅 DOCX 有真值。本文件落地首批
DOCX 标题真值标注（`tests/corpus/ideal/*.ideal.json`，由
`make_ideal_docx.py` 从**源文件 XML 样式**程序化生成，不经管线），并钉住：

① 标注与源样式一致（--check 同款校验，防标注漂移）——这是「标注是真值」
   的前提：标注文件被人手改坏或源样本重生成后样式变化，这里先红；
② S3 分析对显式标题零丢失零改级：pred（S3 heading 块）对 ref（源样式
   真值）F1 == 1.0。这是**真断言**——pred 走 S3 启发式分析，ref 来自
   源 XML，两层独立；F1 掉下来即 S3 丢了标题或改了层级；
③ 覆盖完整性：sources/ 里每份 docx 样本都有 ideal 标注（防漏标）；
④ `pred_headings` 助手边界（空 IR / 无标题 IR）。

PDF 样本真值仍缺位（人工标注推进中）——qa_eval 的「ref 空返回 None、
不得当 1.0 聚合」纪律由 test_qa_o06_metrics.py 钉住，此处不重复。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from doc2md import qa_eval
from doc2md.ir.model import IR
from doc2md.pipeline.s1_parse import read_docx
from doc2md.pipeline.s3_structure import analyze, modal_size

pytestmark = [pytest.mark.milestone("DoD")]

HERE = Path(__file__).resolve().parent
IDEAL = HERE / "corpus" / "ideal"
SOURCES = HERE / "corpus" / "sources"


# ------------------------------------------------------------- ① 标注 = 源样式
@pytest.mark.parametrize("src", sorted(SOURCES.glob("docx_*.docx")),
                         ids=lambda p: p.stem)
def test_ideal_annotation_matches_source_style(src):
    import sys
    sys.path.insert(0, str(HERE / "corpus"))
    import make_ideal_docx as gen
    target = IDEAL / f"{src.stem}.ideal.json"
    assert target.is_file(), f"缺标注：{target.name}"
    ann = json.loads(target.read_text(encoding="utf-8"))
    assert ann["source"] == src.name
    assert ann["headings"] == gen.heading_truth(src), \
        "标注与源 XML 样式不一致——先跑 make_ideal_docx.py 重生成，勿手改数字"


# ------------------------------------------------- ② S3 对显式标题零丢失零改级
@pytest.mark.parametrize("src", sorted(SOURCES.glob("docx_*.docx")),
                         ids=lambda p: p.stem)
def test_s3_preserves_explicit_docx_headings(src):
    ann = json.loads((IDEAL / f"{src.stem}.ideal.json").read_text(encoding="utf-8"))
    pages = read_docx(src)
    ir = analyze(pages, base_size=modal_size(pages))
    res = qa_eval.heading_pr(qa_eval.pred_headings(ir.blocks), ann["headings"])
    assert res["f1"] == 1.0, \
        f"{src.name}: S3 标题保真破线 {res}（ref-only=丢标题 / pred-only=误判标题）"


# --------------------------------------------------------------- ③ 覆盖完整性
def test_every_docx_sample_has_ideal_annotation():
    missing = [p.name for p in SOURCES.glob("docx_*.docx")
               if not (IDEAL / f"{p.stem}.ideal.json").is_file()]
    assert not missing, f"缺标注：{missing}"


# --------------------------------------------------------------- ④ 助手边界
def test_pred_headings_empty_and_nonheading_ir():
    assert qa_eval.pred_headings([]) == []
    ir = IR(doc_id="d", source_key="s")
    ir.blocks = []
    assert qa_eval.pred_headings(ir.blocks) == []
    from doc2md.ir.model import Block
    ir.blocks = [Block(id="p1", type="paragraph", text="正文"),
                 Block(id="h1", type="heading", text="标题", level=2)]
    assert qa_eval.pred_headings(ir.blocks) == [{"text": "标题", "level": 2}]


def test_ideal_json_shape_contract():
    """标注文件契约：kind/headings 键集稳定（评测脚本与人工审阅依赖）。"""
    files = sorted(IDEAL.glob("docx_*.ideal.json"))
    assert files, "ideal 目录不得为空"
    for f in files:
        ann = json.loads(f.read_text(encoding="utf-8"))
        assert ann["kind"] == "headings"
        assert set(ann) >= {"source", "kind", "provenance", "headings"}
        for h in ann["headings"]:
            assert set(h) == {"seq", "text", "level"}
            assert isinstance(h["level"], int) and 1 <= h["level"] <= 6
