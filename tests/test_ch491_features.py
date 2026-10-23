"""CH-491（B-4 / B-5 / B-6）：CLI 检索入口、Markdown 直出、进度打点。

三条新能力的验收：
- `doc2md search` 与 API `GET /search` 同一实现、同一默认口径，结果集逐条相同；
- `--format md` 产出的 Markdown 与 zip 内 `content.md` **逐字节相同**（含 front-matter）；
- `--json` 时进度打点静音（机器可读面不得混入人读进度行）。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from doc2md import config
from doc2md.cli import main as cli_main
from doc2md.storage.db import Store
from doc2md.storage.repo import save_ir

REPO_ROOT = Path(config.__file__).resolve().parents[2]

# S1.5 门禁默认要求每页 ≥120 字符；测试 PDF 必须写足内容才不会被 E4221 拒收。
_FILLER = ("This paragraph exists to satisfy the S1.5 gate minimum characters per page "
           "threshold so that the conversion proceeds to the packaging stage. ")


def _make_pdf(path: Path, title: str, body: str) -> None:
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, title)
    y = 690
    text = body + _FILLER * 3
    for i in range(0, len(text), 70):
        c.drawString(72, y, text[i:i + 70])
        y -= 18
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 12)
            y = 720
    c.save()


@pytest.fixture()
def lib(tmp_path):
    """建一个含一篇文档的库，返回 (root, doc_id)。"""
    from doc2md.ir.model import IR, Block

    root = tmp_path / "root"
    root.mkdir()
    ir = IR(doc_id="doc-search-1", source_key="k1", source_name="合同",
            source_name_raw="合同.pdf", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph", text="Payment is due within 30 days.")]
    store = Store(root / "state.sqlite")
    try:
        save_ir(store, ir)
    finally:
        store.close()
    return root


def test_search_cli_matches_repo(lib, capsys):
    """CLI search 与 repo.search 结果集一致（同一实现、同一口径）。"""
    from doc2md.storage.repo import search

    rc = cli_main(["search", "Payment", "--root", str(lib)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] in ("fts", "like"), out
    assert out["hits"], "应命中 Payment"

    store = Store(lib / "state.sqlite")
    try:
        direct = search(store, "Payment", 20)
    finally:
        store.close()
    assert out["hits"] == direct["hits"], "CLI 与 repo.search 结果集必须逐条相同"


def test_search_cli_missing_library(tmp_path, capsys):
    """库不存在时明确报 E4041（退出码 4），不静默空结果。"""
    rc = cli_main(["search", "x", "--root", str(tmp_path / "nope")])
    assert rc == 4
    err = json.loads(capsys.readouterr().err)
    assert err["code"] == "E4041"


def test_search_cli_limit_is_honoured(lib, capsys):
    """--limit 透传到检索，超限截断而非报错。"""
    rc = cli_main(["search", "Payment", "--root", str(lib), "--limit", "500"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out["hits"], list)


def test_format_md_is_byte_identical_to_zip_content(tmp_path, capsys):
    """--format md 的产物与 zip 内 content.md 逐字节相同。"""
    src = tmp_path / "a.pdf"
    _make_pdf(src, "Quarterly Report", "Revenue increased by 12 percent.")

    out = tmp_path / "out"
    root = tmp_path / "root"
    rc = cli_main(["convert", str(src), "--root", str(root), "--out", str(out),
                   "--format", "both", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    md_path = Path(payload["md"])
    assert md_path.exists(), "md 直出文件应存在"

    with zipfile.ZipFile(payload["zip"]) as z:
        name = [n for n in z.namelist() if n.endswith("content.md")][0]
        inner = z.read(name)
    assert md_path.read_bytes() == inner, "md 必须与 zip 内 content.md 逐字节相同"


def test_emit_md_failure_leaves_no_partial_target(tmp_path, monkeypatch):
    """直出写盘失败时不得留下截断的 .md（tmp + fsync + replace，失败即清理）。"""
    from doc2md import cli

    zip_path = tmp_path / "d1.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("d1/content.md", b"# hello\n")
    out = tmp_path / "out"
    out.mkdir()

    def boom(_fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(cli.os, "fsync", boom)
    with pytest.raises(OSError):
        cli._emit_md(zip_path, out, "a.md")
    assert not (out / "a.md").exists()
    assert list(out.iterdir()) == [], "失败路径必须清掉临时文件"


def test_format_md_removes_zip(tmp_path, capsys):
    """--format md 只留 Markdown（zip 被移除），语义与 both 区分开。"""
    src = tmp_path / "b.pdf"
    _make_pdf(src, "Memo", "Internal use only.")

    out = tmp_path / "out"
    rc = cli_main(["convert", str(src), "--root", str(tmp_path / "root"),
                   "--out", str(out), "--format", "md", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["md"]).exists()
    # D12（QA-R2）：zip 已删，结果行不再报路径，改以 zip_removed 表达
    assert payload.get("zip_removed") is True
    assert "zip" not in payload
    assert list(out.glob("*.zip")) == [], "输出目录不应有 zip"


def test_format_zip_is_default_and_unchanged(tmp_path, capsys):
    """默认 --format zip 行为不变（无 md 字段、zip 存在）。"""
    src = tmp_path / "c.pdf"
    _make_pdf(src, "Note", "Default behaviour.")

    out = tmp_path / "out"
    rc = cli_main(["convert", str(src), "--root", str(tmp_path / "root"),
                   "--out", str(out), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "md" not in payload, "默认 zip 模式不应有 md 字段"
    assert Path(payload["zip"]).exists()


def test_progress_ticks_on_stderr_but_silent_under_json(tmp_path, capsys):
    """进度打点落 stderr；--json 时静音。"""
    src = tmp_path / "d.pdf"
    _make_pdf(src, "Progress", "Stage ticks.")

    # 非 --json：stderr 应有五阶段打点
    rc = cli_main(["convert", str(src), "--root", str(tmp_path / "r1"),
                   "--out", str(tmp_path / "o1")])
    assert rc == 0
    err = capsys.readouterr().err
    for stage in ("s1", "s1_5", "s3", "s4", "package"):
        assert f"[{stage}]" in err, f"缺少 {stage} 阶段打点"

    # --json：stderr 不得有进度行
    rc = cli_main(["convert", str(src), "--root", str(tmp_path / "r2"),
                   "--out", str(tmp_path / "o2"), "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[s1]" not in captured.err, "--json 时进度必须静音"
    json.loads(captured.out)          # stdout 仍是合法 JSON
