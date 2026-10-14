"""黄金样本集自身的可复现性与可移植性（CH-442）。

这批资产是红线门禁的输入（方案 8.4），本身若不逐字节可复现，MANIFEST 的 sha256 绑定
和「产物字节一致」类断言就只是摆设；若含绝对路径，换机后防护用例被 skip（假通过）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.milestone("DoD")]


@pytest.fixture()
def gen(tmp_path, monkeypatch):
    """把生成器的输出目录指向 tmp_path（GOLDEN_DIR 是模块级，用 monkeypatch 复位）。"""
    import make_golden as mg
    monkeypatch.setattr(mg, "GOLDEN_DIR", tmp_path)
    monkeypatch.setattr(mg, "FORCE_REGEN", False)
    return mg


def test_pdf_sample_is_byte_reproducible(tmp_path, gen):
    """同一脚本跑两次 → 同一份字节（reportlab invariant 模式）。"""
    first = tmp_path / "pass1"
    second = tmp_path / "pass2"
    for d in (first, second):
        d.mkdir()
        gen.GOLDEN_DIR = d
        gen.make_small_3p()
        gen.GOLDEN_DIR = tmp_path
    a = (first / "small_3p.pdf").read_bytes()
    b = (second / "small_3p.pdf").read_bytes()
    assert a == b, "PDF 样本不可复现（/CreationDate 或 /ID 未被固定）"


def test_large_pdf_sample_is_byte_reproducible(tmp_path, gen):
    gen.GOLDEN_DIR = tmp_path
    gen.make_large_320p()
    a = (tmp_path / "large_320p.pdf").read_bytes()
    (tmp_path / "large_320p.pdf").unlink()
    gen.make_large_320p()
    assert a == (tmp_path / "large_320p.pdf").read_bytes(), "320 页样本字节漂移"


def test_docx_sample_is_byte_reproducible(tmp_path, gen):
    """docx 走 freeze_docx：zip 条目时间戳 + core.xml 日期字段都要固定。"""
    dirs = []
    for name in ("pass1", "pass2"):
        d = tmp_path / name
        d.mkdir()
        gen.GOLDEN_DIR = d
        gen.make_docx_samples()
        dirs.append(d / "docx")
    for f in ("sample_simple.docx", "sample_links.docx", "sample_revisions.docx"):
        assert (dirs[0] / f).read_bytes() == (dirs[1] / f).read_bytes(), f"{f} 不可复现"


def test_freeze_docx_keeps_document_valid(tmp_path, gen):
    """固定元数据时不得写坏 OOXML（属性引号、命名空间都得留着）。"""
    from docx import Document
    gen.GOLDEN_DIR = tmp_path
    gen.make_docx_samples()
    doc = Document(str(tmp_path / "docx" / "sample_simple.docx"))
    texts = "\n".join(p.text for p in doc.paragraphs)
    assert "一级标题" in texts and "删除线" in texts
    core = (tmp_path / "docx" / "sample_simple.docx")
    import zipfile
    xml = zipfile.ZipFile(core).read("docProps/core.xml").decode()
    assert 'xsi:type="dcterms:W3CDTF"' in xml          # 属性引号未被转义吃掉
    assert "2024-10-13T00:00:00Z" in xml


def test_shipped_symlink_loop_is_relative(golden_dir):
    """循环链接必须用相对目标：绝对路径写死本机安装位置，分发后成断链 → 防护用例被 skip。"""
    loop = golden_dir / "dirs" / "symlink_loop"
    if not loop.is_dir():
        pytest.skip("dirs/symlink_loop missing")
    names = {p.name for p in loop.iterdir()}
    assert {"A", "B"} <= names, "循环链接样本缺失"
    for n in ("A", "B"):
        p = loop / n
        assert p.is_symlink(), f"{n} 不再是符号链接"
        target = os.readlink(p)
        assert not os.path.isabs(target), f"链接目标写成绝对路径：{target}"
        assert not p.exists(), "A→B→A 应为悬空循环（exists 跟随链接）"


def test_generator_has_no_absolute_output_path():
    src = Path(__file__).resolve().parent / "make_golden.py"
    text = src.read_text(encoding="utf-8")
    assert 'GOLDEN_DIR = Path("/' not in text, "生成器输出目录又被写死成绝对路径"


def test_shipped_manifest_hashes_match_files(golden_dir):
    """MANIFEST 的 sha256 绑定必须是真的：逐个可复核的条目全查
    （只容忍 CH-443 规定省略的 bigfile.bin——分发包不内置 120MB 资产）。"""
    import hashlib
    import json

    entries = json.loads((golden_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    assert entries, "MANIFEST 为空"
    checked = 0
    for e in entries:
        p = golden_dir / e["path"]
        if not p.is_file():
            assert e["path"] == "bigfile.bin", f"清单声明却缺失：{e['path']}"
            continue
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        assert digest.hexdigest() == e["sha256"], f"哈希漂移：{e['path']}"
        assert p.stat().st_size == e["bytes"], f"字节数漂移：{e['path']}"
        checked += 1
    assert checked >= 5000, f"复核面过小（{checked}），清单可信度不足"
