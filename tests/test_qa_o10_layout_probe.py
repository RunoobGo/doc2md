"""O-10（QA-D2M-2026-10-21 / C-28 / F-12）：旋转页 / RTL 探测。

只探测不修复：旋转页（/Rotate % 180 != 0）或 RTL 占比 > 50% → W1046 +
`extra_meta.layout_probe` 明细；转换不失败；常规语料零新增告警（G6）。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from doc2md.errors import WARN_LAYOUT_ROTATED_RTL
from doc2md.pipeline.s1_parse import _rtl_ratio

pytestmark = [pytest.mark.milestone("DoD")]
CORPUS = Path(__file__).parent / "corpus"


def _rotated_pdf(src: Path, dst: Path, degrees: int) -> None:
    """pypdf 给整本加 /Rotate（reportlab 画布不直接暴露该属性）。"""
    import io

    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(degrees)
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    dst.write_bytes(buf.getvalue())


def test_rtl_ratio_pure_arabic_is_one():
    chars = [{"text": ch} for ch in "مرحبا بالعالم"]
    assert _rtl_ratio(chars) == 1.0


def test_rtl_ratio_mixed_and_non_alpha_denominator():
    # 阿拉伯 5 + 拉丁 5 → 0.5；数字/标点不参与分母
    chars = [{"text": ch} for ch in "مرحباabcde123., "]
    assert _rtl_ratio(chars) == 0.5
    # 纯数字/空白 → 无字母 → 0.0（非 RTL 文档恒 0，不误报）
    assert _rtl_ratio([{"text": "1"}, {"text": " "}]) == 0.0
    assert _rtl_ratio([]) == 0.0


def test_rtl_ratio_hebrew_counts_as_rtl():
    chars = [{"text": ch} for ch in "שלוםabc"]
    assert _rtl_ratio(chars) == round(4 / 7, 12) or _rtl_ratio(chars) > 0.5


def test_rotated_pdf_warns_but_converts(tmp_path, small_pdf):
    rot = tmp_path / "rot90.pdf"
    _rotated_pdf(small_pdf, rot, 90)
    from doc2md.pipeline.convert import convert
    res = convert(rot, out_dir=tmp_path / "out")
    assert WARN_LAYOUT_ROTATED_RTL in res.ir.warnings
    assert res.ir.extra_meta["layout_probe"]["rotated_pages"], "须给出页号明细"
    assert res.markdown.strip(), "转换不失败、仍有产物"
    with zipfile.ZipFile(res.zip_path) as z:
        body = z.read(f"{res.doc_id}/content.md").decode("utf-8")
    assert WARN_LAYOUT_ROTATED_RTL in body, "告警随 meta 进 front-matter（诚实暴露）"


def test_unrotated_pdf_no_layout_warning(tmp_path, small_pdf):
    from doc2md.pipeline.convert import convert
    res = convert(small_pdf, out_dir=tmp_path / "out")
    assert WARN_LAYOUT_ROTATED_RTL not in res.ir.warnings
    assert "layout_probe" not in res.ir.extra_meta, "零探测零落键（G6：字节不变）"


def test_gate_corpus_zero_layout_warnings():
    """gate：全语料（常规输入）零 W1046——探测不得误报常规文档。"""
    for exp in sorted(CORPUS.glob("expected/*.md")):
        meta = exp.read_text(encoding="utf-8")
        if meta.startswith("---\n"):
            fm = meta.split("---")[1]
            assert WARN_LAYOUT_ROTATED_RTL not in fm, exp.name


def test_layout_probe_dict_shape_in_ir_json(tmp_path, small_pdf):
    rot = tmp_path / "rot270.pdf"
    _rotated_pdf(small_pdf, rot, 270)
    from doc2md.pipeline.convert import convert
    res = convert(rot, out_dir=tmp_path / "out")
    with zipfile.ZipFile(res.zip_path) as z:
        ir = json.loads(z.read(f"{res.doc_id}/ir.json").decode("utf-8"))
    # extra_meta 并入 ir.json 的 meta（IR.meta() 的 **extra_meta 展开）
    probe = (ir.get("meta") or {}).get("layout_probe") or {}
    assert set(probe) == {"rotated_pages", "rtl_pages"}
    assert probe["rtl_pages"] == []
