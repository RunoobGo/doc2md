"""tests/test_qa_n03_ideal.py — N-3（QA-OPT-2/3）：ideal 标注真值链路。

首轮 QA 遗留（N-3，P1）：`heading_pr` 仅 DOCX 有真值。本文件落地标题
真值标注（`tests/corpus/ideal/*.ideal.json`）并钉住：

DOCX 批次（make_ideal_docx.py，源 XML 样式 `Heading N`/`Title`）：
① 标注与源样式一致（--check 同款校验，防标注漂移）；
② S3 对显式标题零丢失零改级：F1 == 1.0（pred 走 S3 启发式，ref 来自
   源 XML，两层独立）；
③ 覆盖完整性；④ `pred_headings` 助手边界；⑤ JSON 形状契约。

PDF 批次（N-3 二批，make_ideal_pdf.py，源 PDF 字号分布 + 生成器字号
约定）：同构四层校验，但 S3 的 F1 **冻结在实测基线**而非 1.0——
pdf_04「代码说明」/ pdf_07「结语」（第 2 页顶部无编号 14pt 短行）被
S3 跨页续段合并吞进正文段，暴露 `unnumbered` 分支 `base+6.0` 阈值
对 +4.0pt 形态漏判；修复需与 golden small_3p 页标题（+5.5pt，刻意
排除）区分，属专项变更 → 登记 N-8，基线冻结防进一步漂移。
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
    files = sorted(IDEAL.glob("*_*.ideal.json"))
    assert files, "ideal 目录不得为空"
    for f in files:
        ann = json.loads(f.read_text(encoding="utf-8"))
        assert ann["kind"] == "headings"
        assert set(ann) >= {"source", "kind", "provenance", "headings"}
        for h in ann["headings"]:
            assert set(h) == {"seq", "text", "level"}
            assert isinstance(h["level"], int) and 1 <= h["level"] <= 6


# ======================================================= PDF 批次（N-3 二批）
PDF_BASELINE_F1 = {
    # N-8（QA-OPT-3）修复后全 1.0：原 pdf_04 / pdf_07 的第 2 页顶部无编号
    # 14pt 短标题被同页续段合并吞进正文段（unnumbered 分支 base+6.0 阈值对
    # +3.5~+4.0pt 漏判）——修复为阈值降到 base+3.5 + 页标题豁免集（数字折叠
    # 后跨页重复的无编号行不抬），冻结基线解除。
    "pdf_01": 1.0,
    "pdf_02": 1.0,
    "pdf_03": 1.0,
    "pdf_04": 1.0,
    "pdf_05": 1.0,
    "pdf_07": 1.0,
    "pdf_08": 1.0,
    "pdf_09": 1.0,
    "pdf_10": None,          # 真值=空集（栏头是家具）：断 pred 也是空集
    "pdf_11": 1.0,
}


@pytest.mark.parametrize("stem", sorted(PDF_BASELINE_F1), ids=lambda s: s)
def test_ideal_pdf_annotation_matches_source(stem):
    import sys
    sys.path.insert(0, str(HERE / "corpus"))
    import make_ideal_pdf as gen
    src = next(SOURCES.glob(f"{stem}*.pdf"))
    target = IDEAL / f"{src.stem}.ideal.json"
    assert target.is_file(), f"缺标注：{target.name}"
    ann = json.loads(target.read_text(encoding="utf-8"))
    assert ann == gen.build_annotation(src), \
        "标注与源字号分布不一致——先跑 make_ideal_pdf.py 重生成，勿手改数字"


@pytest.mark.parametrize("stem", sorted(PDF_BASELINE_F1), ids=lambda s: s)
def test_s3_pdf_heading_frozen_baseline(stem):
    from doc2md.pipeline.s1_parse import read_pdf
    src = next((HERE / "corpus" / "sources").glob(f"{stem}*.pdf"))
    ann = json.loads((IDEAL / f"{src.stem}.ideal.json").read_text(
        encoding="utf-8"))
    pages = read_pdf(src)
    ir = analyze(pages, base_size=modal_size(pages))
    pred = qa_eval.pred_headings(ir.blocks)
    if ann["headings"]:
        res = qa_eval.heading_pr(pred, ann["headings"])
        assert res["f1"] == pytest.approx(PDF_BASELINE_F1[stem], abs=5e-4), \
            f"{src.name}: S3 标题 F1 偏离冻结基线 {res}（升高=修复应同步更新基线；" \
            "降低=回归，先查）"
    else:
        assert pred == [], \
            f"{src.name}: 真值为空集（全家具）但 S3 判出标题 {pred}"


def test_every_convertible_pdf_sample_has_ideal_annotation():
    skipped = {"pdf_06_scanned_1p.pdf"}     # 无文本层，门禁拒收，无真值
    missing = [p.name for p in (HERE / "corpus" / "sources").glob("pdf_*.pdf")
               if p.name not in skipped
               and not (IDEAL / f"{p.stem}.ideal.json").is_file()]
    assert not missing, f"缺标注：{missing}"
