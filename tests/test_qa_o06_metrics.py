"""O-06（QA-D2M-2026-10-21 / C-31 / R-4）：结构质量指标测试。

覆盖：char_f1 数学性质、heading_pr 匹配语义（含 multiset 与无真值）、
table_col_consistency 端到端、_metrics 对空 IR / 单块 IR 不崩（gate 边界）、
metrics 键不进 content.md front-matter（字节零影响）、DOCX 标题真值接入。
"""

from __future__ import annotations

import pytest

from doc2md import qa_eval
from doc2md.ir.model import IR, Block
from doc2md.pipeline.convert import _metrics

pytestmark = [pytest.mark.milestone("DoD")]


def _ir(*blocks: Block, fmt: str = "pdf") -> IR:
    ir = IR(doc_id="d", source_key="k")
    ir.blocks = list(blocks)
    ir.format = fmt
    return ir


def test_char_f1_identity_and_empty():
    assert qa_eval.char_f1("abc", "abc") == {"p": 1.0, "r": 1.0, "f1": 1.0}
    assert qa_eval.char_f1("", "")["f1"] == 1.0
    assert qa_eval.char_f1("abc", "")["f1"] == 0.0
    assert qa_eval.char_f1("", "abc")["f1"] == 0.0


def test_char_f1_partial_and_whitespace_ignored():
    r = qa_eval.char_f1("数字2026", "数字 2026")
    assert r["f1"] == 1.0, "剥空白后完全一致——O-01 修复对 F1 的增益可度量"
    r2 = qa_eval.char_f1("数字2026", "数字2027")
    assert 0.5 < r2["f1"] < 1.0


def test_heading_pr_matches_text_and_level():
    ref = [{"text": "第一章 概述", "level": 1},
           {"text": "背景", "level": 2}]
    pred_ok = [{"text": "第一章 概述", "level": 1},
               {"text": "背景", "level": 3}]        # 层级错 → 不算命中
    r = qa_eval.heading_pr(pred_ok, ref)
    assert r["matched"] == 1 and r["r"] == 0.5
    assert r["p"] == 0.5, "预测 2 条中 1 条命中"
    # 空白归一化命中
    r2 = qa_eval.heading_pr([{"text": "第一章  概述", "level": 1}], ref)
    assert r2["matched"] == 1


def test_heading_pr_no_reference_returns_none_not_perfect():
    r = qa_eval.heading_pr([{"text": "x", "level": 1}], [])
    assert r["p"] is None and r["f1"] is None
    assert r["ref"] == 0


def test_table_col_consistency_counts_modal_rate():
    t_ok = Block(id="t1", type="table", row_shape=[2, 2, 2])
    t_bad = Block(id="t2", type="table", row_shape=[3, 3, 2])
    r = qa_eval.table_col_consistency([t_ok, t_bad])
    assert r["tables"] == 2 and r["rows"] == 4
    assert r["rate"] == 0.75, "t_bad 体行 [3,2] 只有一行命中众数 3"


def test_table_col_consistency_without_row_shape_is_none():
    """无 row_shape（反序列化块/手造块）→ 无真值不出数（宁漏勿误）。"""
    t = Block(id="t", type="table", text="| a | b |\n| --- | --- |\n| 1 | 2 |")
    r = qa_eval.table_col_consistency([t])
    assert r["tables"] == 1 and r["rows"] == 0 and r["rate"] is None


def test_table_col_consistency_empty_ir_returns_none_rate():
    r = qa_eval.table_col_consistency([])
    assert r == {"tables": 0, "rows": 0, "rate": None}


def test_metrics_empty_ir_and_single_block_do_not_crash():
    m0 = _metrics("", _ir())
    assert m0["table_col"]["rate"] is None
    assert "heading_pr" not in m0
    m1 = _metrics("# 一\n\n正文", _ir(Block(id="b1", type="heading", text="一", level=1)))
    assert m1["blocks"] == 1


def test_metrics_docx_heading_pr_uses_level_hint_ground_truth():
    class L:
        def __init__(self, text, lv):
            self.text, self.level_hint = text, lv

    class P:
        def __init__(self, lines):
            self.lines = lines

    pages = [P([L("第一章 概述", 1), L("正文一行", None)]),
             P([L("背景", 2), L("背景", None)])]
    blocks = [Block(id="h1", type="heading", text="第一章 概述", level=1),
              Block(id="p1", type="paragraph", text="正文一行"),
              Block(id="h2", type="heading", text="背景", level=2),
              Block(id="p2", type="paragraph", text="背景")]
    m = _metrics("# 第一章 概述\n\n正文一行\n\n## 背景\n\n背景",
                 _ir(*blocks, fmt="docx"), pages=pages)
    assert m["heading_pr"]["f1"] == 1.0
    assert m["heading_pr"]["ref"] == 2


def test_metrics_pdf_has_no_heading_pr_no_fake_truth():
    m = _metrics("# x", _ir(Block(id="h", type="heading", text="x", level=1),
                            fmt="pdf"), pages=[[type("L", (), {"level_hint": None,
                                                               "text": "x"})()]])
    assert "heading_pr" not in m, "PDF 无真值不得产出标题 P/R（宁漏勿误）"


def test_metrics_keys_do_not_reach_front_matter_bytes(tmp_path, small_pdf):
    from doc2md.pipeline.convert import convert
    res = convert(small_pdf, out_dir=tmp_path / "out")
    assert "table_col" in res.metrics
    assert "heading_pr" not in res.metrics, "PDF 无真值"
    import zipfile
    with zipfile.ZipFile(res.zip_path) as z:
        body = z.read(f"{res.doc_id}/content.md").decode("utf-8")
    fm = body.split("---")[1]
    assert "table_col" not in fm and "heading_pr" not in fm, \
        "metrics 是 Result 独立字段，不得进 front-matter（字节零影响）"


def test_row_shape_transient_not_serialized():
    import json as _json
    t = Block(id="t", type="table", row_shape=[2, 2])
    assert "row_shape" not in _json.dumps(t.to_dict(), ensure_ascii=False), \
        "瞬态字段不进 ir.json（SCHEMA_VERSION 不变，G6）"


# ---------------------------------------------------- N-6（QA-OPT-2）quality 摘要
def test_report_quality_section_iff_metrics(tmp_path, small_pdf):
    """N-6：report.html 尾节 quality 摘要——传 metrics 出现、不传缺省；
    content.md 字节不受 metrics 有无影响（报告层纪律）。"""
    import json
    import zipfile

    from doc2md.pipeline.convert import convert
    res = convert(small_pdf, out_dir=tmp_path / "out")
    with zipfile.ZipFile(res.zip_path) as z:
        report = z.read(f"{res.doc_id}/report.html").decode("utf-8")
        body = z.read(f"{res.doc_id}/content.md").decode("utf-8")
    assert "<h2>Quality</h2>" in report
    assert "table_col.rate" in report and "heading_pr.f1" in report
    assert res.metrics["table_col"] is not None
    # front-matter 不携带 metrics 键
    fm = json.loads(body[4:body.index("\n---", 4)])
    assert not ({"table_col", "heading_pr"} & set(fm)), fm.keys()

    # DB 重建路径（不传 metrics）→ 整节缺省
    from doc2md.pipeline.convert import package, render_report
    r2 = render_report(res.ir, res.meta)
    assert "<h2>Quality</h2>" not in r2, "无 metrics 不得补零冒充"
    # package 直调（rebuild 口径）同样缺省
    zp = package(res.ir, res.markdown, res.meta, tmp_path / "rebuild", {})
    with zipfile.ZipFile(zp) as z:
        assert "<h2>Quality</h2>" not in z.read(
            f"{res.doc_id}/report.html").decode("utf-8")


def test_report_quality_escapes_and_shows_dash_for_missing():
    """quality 表格值 HTML 转义；缺真值（heading_pr 无 / rate None）显示 —。"""
    from doc2md.pipeline.convert import render_report
    ir = _ir(Block(id="h1", type="heading", text="标题", level=1))
    ir.format = "pdf"
    meta = {"source_name": "x.pdf", "warnings": []}
    report = render_report(ir, meta, metrics={
        "blocks": 1, "chars": 2, "headings": 1,
        "table_col": {"tables": 0, "rows": 0, "rate": None}})
    assert "<td>—</td>" in report, "rate=None 必须显示 —，不得当 0/满分"
    assert "elapsed_ms" not in report, \
        "耗时字段不得进 report.html（逐条目字节等价纪律：天生不可复现）"
    # 转义：值里带 HTML 字符不破结构
    report2 = render_report(ir, meta, metrics={
        "blocks": 1, "chars": "<b>2</b>", "headings": 1,
        "table_col": {"tables": 0, "rows": 0, "rate": None}})
    assert "&lt;b&gt;2&lt;/b&gt;" in report2 and "<b>2</b>" not in \
        report2.split("Quality")[1]


