"""CH-492 缺陷修复验收：F-05 带口令 PDF 诊断面（选项 c：尾窗 /Encrypt 明文探测）。

背景：QA 阶段一实测（M-3）实证——口令加密与结构损坏的 PDF 在 pdfplumber 侧
同为 `PdfminerException`，`_open_source` 收敛后 detail 逐字节相同
（`{file, exc:"PdfminerException"}`），用户完全无法自助定位。修复：open 失败
异常路径用尾窗 `/Encrypt` 明文探测打 `password_required` 诊断 flag（stdlib
实现，零新依赖、错误码不动、正常路径零开销；有界风险见 helper docstring）。
"""
import pytest

from doc2md.errors import E4151_UnsupportedType
from doc2md.pipeline.s1_parse import _pdf_tail_has_encrypt, read_pdf


def _make_encrypted_pdf(path, password: str = "secret123") -> None:
    """pypdf 造带用户口令的加密 PDF（dev 依赖，仅测试用）。"""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    src = path.with_suffix(".src.pdf")
    c = canvas.Canvas(str(src))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Encrypted Document")
    c.drawString(72, 690, "This content requires a password to open.")
    c.save()
    writer = PdfWriter()
    writer.append(PdfReader(str(src)))
    writer.encrypt(password)
    with open(path, "wb") as f:
        writer.write(f)
    src.unlink()


def _make_truncated_pdf(path) -> None:
    """截断的合法 PDF（前半）——结构损坏但 magic 合法。"""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Truncated Document")
    c.drawString(72, 690, "This file is cut in half.")
    c.save()
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def test_encrypted_pdf_detail_has_password_required(tmp_path):
    """口令加密 PDF → E4151 + detail.password_required=True（F-05 核心断言）。"""
    enc = tmp_path / "enc.pdf"
    _make_encrypted_pdf(enc)
    with pytest.raises(E4151_UnsupportedType) as ei:
        read_pdf(enc)
    assert ei.value.detail.get("password_required") is True


def test_truncated_pdf_detail_has_no_password_flag(tmp_path):
    """截断损坏 PDF → E4151，detail 无 password_required（与口令形态可区分）。"""
    trunc = tmp_path / "trunc.pdf"
    _make_truncated_pdf(trunc)
    with pytest.raises(E4151_UnsupportedType) as ei:
        read_pdf(trunc)
    assert "password_required" not in ei.value.detail


def test_tail_probe_direct(tmp_path):
    """尾窗探测直测：加密 → True；截断 → False（helper 单测）。"""
    enc = tmp_path / "enc2.pdf"
    _make_encrypted_pdf(enc)
    assert _pdf_tail_has_encrypt(enc) is True
    trunc = tmp_path / "trunc2.pdf"
    _make_truncated_pdf(trunc)
    assert _pdf_tail_has_encrypt(trunc) is False


def test_normal_pdf_not_probed(tmp_path):
    """正常 PDF 不触发探测（helper 对正常文件返回 False，语义安全）。"""
    from reportlab.pdfgen import canvas

    ok = tmp_path / "ok.pdf"
    c = canvas.Canvas(str(ok))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Normal Document")
    c.drawString(72, 690, "This file opens fine.")
    c.save()
    assert _pdf_tail_has_encrypt(ok) is False


def test_batch_bytes_sums_stat(tmp_path):
    """_batch_bytes 汇总批内总字节；stat 失败（文件已删）按 0 计（保守串行）。"""
    from doc2md.api import _batch_bytes

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"x" * 100)
    b.write_bytes(b"y" * 250)
    assert _batch_bytes([a, b]) == 350
    assert _batch_bytes([tmp_path / "missing.pdf"]) == 0
    assert _batch_bytes([]) == 0
