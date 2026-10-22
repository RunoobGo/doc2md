"""tests/test_qa_o07_cli_docs.py — O-07（C-26 / F-01）：CLI `docs` 只读三件套。

缺口：`search`（CH-491）补齐了检索面，但「已转了什么 / 这篇长什么样 /
把正文取出来」仍要起 serve 或翻 zip——离线单机用户的读取面不完整。

本批新增三个只读子命令（零 DB 写入）：
- `docs list`：分页信封 + 块计数（`repo.list_docs` 新只读面）；
- `docs show`：`GET /docs/<id>` 的 CLI 等价物（含 O-05 的 warnings 派生字段）；
- `docs export`：zip 内 content.md 原样字节落盘 + manifest.json 对账，
  同名冲突按 F-14 既定口径加 `-<doc_id 前 8>` 后缀。

回归口径（优化方案 O-07 行）：① list 条数 == `SELECT COUNT(*) FROM docs`；
② show 与 API 同参逐字段相同；③ export 与 zip content.md 逐字节相同；
④ 恶意 doc_id / 文件名走同一消毒闸；⑤ gate 增 3 条（见 test_v24_gates.py）。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from doc2md.artifacts import artifact_entry
from doc2md.cli import main

pytestmark = [pytest.mark.milestone("DoD")]


def _root(tmp_path) -> Path:
    """与 api_client / tmp_cfg 夹具同一工作根（tmp_path/work）。"""
    return tmp_path / "work"


def _sql_count(root: Path, sql: str, args: tuple = ()) -> int:
    from doc2md.storage.db import Store
    s = Store(root / "state.sqlite")
    try:
        row = s.q1(sql, args)
        return int(row["c"]) if row else 0
    finally:
        s.close()


def _zip_content(root: Path, doc_id: str) -> bytes:
    with zipfile.ZipFile(root / "out" / f"{doc_id}.zip") as z:
        entry = artifact_entry(z.namelist(), "content.md", doc_id)
        assert entry, "产物 zip 必须含 content.md"
        return z.read(entry)


def _post_job(client, tokens, path: Path) -> str:
    r = client.post("/jobs", json={"path": str(path)},
                    headers={"X-Doc2md-Token": tokens["editor"]})
    assert r.status_code == 202, (r.status_code, r.get_data(as_text=True)[:300])
    jobs = r.get_json()["jobs"]
    assert jobs and jobs[0].get("doc_id"), jobs
    return jobs[0]["doc_id"]


def _make_docx(path: Path, text: str) -> None:
    """同源同名、内容不同的两篇 docx（>120 字符：S1.5 门禁对每页字符数有下限，
    单句样本会被 E4221 拒收——那属门禁语义，不是本测试要覆盖的面）。"""
    import docx
    d = docx.Document()
    d.add_paragraph(text * 12)
    d.save(str(path))


# ---------------------------------------------------------------- list
def test_docs_list_count_matches_db(saved_doc, tmp_path, capsys):
    """① 条数 == `SELECT COUNT(*) FROM docs`；分页信封字段齐全；
    每行 blocks 计数与逐篇 COUNT 一致（相关子查询不许漂移）。"""
    _client, _tokens, doc_id = saved_doc
    root = _root(tmp_path)
    assert main(["docs", "list", "--root", str(root)]) == 0
    body = json.loads(capsys.readouterr().out)
    n = _sql_count(root, "SELECT COUNT(*) AS c FROM docs")
    assert body["total"] == n >= 1
    assert len(body["docs"]) == n
    assert body["offset"] == 0 and body["has_more"] is False
    row = body["docs"][0]
    assert row["doc_id"] == doc_id
    assert row["blocks"] == _sql_count(
        root, "SELECT COUNT(*) AS c FROM blocks WHERE doc_id=?", (doc_id,))
    assert isinstance(row["sharded"], bool)
    for k in ("source_key", "source_name", "format", "pages", "status",
              "profile", "created_at", "updated_at"):
        assert k in row, f"清单行缺字段 {k}"


def test_docs_list_window_and_status_filter(saved_doc, tmp_path, capsys):
    """分页窗口与状态过滤：offset/limit 生效、has_more 语义正确；
    status 过滤后 total 只数满足条件的行。"""
    _client, _tokens, doc_id = saved_doc
    root = _root(tmp_path)
    assert main(["docs", "list", "--root", str(root), "--limit", "1"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert len(body["docs"]) == 1 and body["total"] >= 1
    assert body["has_more"] == (1 < body["total"])
    assert main(["docs", "list", "--root", str(root),
                 "--status", "done"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert all(r["status"] == "done" for r in body["docs"])
    assert body["total"] == _sql_count(root, "SELECT COUNT(*) AS c FROM docs"
                                       " WHERE status=?", ("done",))
    # 不存在的状态：空清单但不报错（过滤语义，不是错误）
    assert main(["docs", "list", "--root", str(root), "--status", "nope"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["docs"] == [] and body["total"] == 0


def test_docs_list_negative_limit_is_usage_error(saved_doc, tmp_path, capsys):
    """负 limit 必须退 2（argparse 对 --limit abc 同样退 2）：
    SQLite `LIMIT -1` 是无界的反直觉语义，不许透到 SQL。"""
    root = _root(tmp_path)
    assert main(["docs", "list", "--root", str(root), "--limit", "-1"]) == 2
    assert "E4001" in capsys.readouterr().err


def test_docs_list_no_library_is_e4041(tmp_path, capsys):
    """库不存在：E4041 / 退出码 4（与 search 同一 _open_store 单点口径）。"""
    assert main(["docs", "list", "--root", str(tmp_path / "nowhere")]) == 4
    assert "E4041" in capsys.readouterr().err


# ---------------------------------------------------------------- show
def test_docs_show_matches_api(saved_doc, tmp_path, capsys):
    """② show 与 `GET /docs/<id>` 同参逐字段相同（CLI/API 对齐红线的扩展）。"""
    client, tokens, doc_id = saved_doc
    root = _root(tmp_path)
    assert main(["docs", "show", doc_id, "--root", str(root),
                 "--limit", "100", "--offset", "0"]) == 0
    cli_body = json.loads(capsys.readouterr().out)
    r = client.get(f"/docs/{doc_id}?limit=100&offset=0",
                   headers={"X-Doc2md-Token": tokens["reader"]})
    assert r.status_code == 200
    api_body = r.get_json()
    for k in ("doc_id", "source_key", "source_name", "format", "pages",
              "sharded", "shard_count", "status", "profile", "created_at",
              "updated_at", "blocks", "blocks_total", "blocks_offset",
              "blocks_limit", "warnings"):
        assert cli_body[k] == api_body[k], f"show 与 API 的 {k} 不一致"
    assert cli_body["blocks_total"] == _sql_count(
        root, "SELECT COUNT(*) AS c FROM blocks WHERE doc_id=?", (doc_id,))


def test_docs_show_unknown_id_is_e4041(saved_doc, tmp_path, capsys):
    """未知 doc_id：E4041 / 退出码 4（不崩栈、不空响应）。"""
    root = _root(tmp_path)
    assert main(["docs", "show", "0" * 16, "--root", str(root)]) == 4
    assert "E4041" in capsys.readouterr().err


# ---------------------------------------------------------------- export
def test_docs_export_bytes_equal_zip(saved_doc, tmp_path, capsys):
    """③ 导出 md 与 zip 内 content.md 逐字节相同；manifest 对账三字段
    （doc_id ↔ file ↔ bytes/sha256）与实文件一致。"""
    _client, _tokens, doc_id = saved_doc
    root = _root(tmp_path)
    exp = tmp_path / "export"
    assert main(["docs", "export", doc_id, "--root", str(root),
                 "--out", str(exp)]) == 0
    capsys.readouterr()
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    assert man["count"] == 1
    entry = man["docs"][0]
    assert entry["doc_id"] == doc_id
    body = _zip_content(root, doc_id)
    md = (exp / entry["file"]).read_bytes()
    assert md == body, "导出字节必须与 zip 内 content.md 逐字节相同"
    assert entry["bytes"] == len(body)
    assert entry["sha256"] == hashlib.sha256(body).hexdigest()


def test_docs_export_name_collision_suffix(saved_doc, tmp_path, capsys):
    """同名不同内容的两篇：第二篇按 F-14 既定口径加 `-<doc_id 前 8>` 后缀，
    manifest 两条目各自指向真实文件（对账不串）。"""
    client, tokens, doc_id = saved_doc          # small_3p.pdf → report 名不同
    root = _root(tmp_path)
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir()
    d2.mkdir()
    _make_docx(d1 / "report.docx", "第一篇正文")
    _make_docx(d2 / "report.docx", "第二篇另外的正文")
    id1 = _post_job(client, tokens, d1 / "report.docx")
    id2 = _post_job(client, tokens, d2 / "report.docx")
    assert id1 != id2
    exp = tmp_path / "export"
    assert main(["docs", "export", id1, id2, "--root", str(root),
                 "--out", str(exp)]) == 0
    capsys.readouterr()
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    assert man["count"] == 2
    names = {e["doc_id"]: e["file"] for e in man["docs"]}
    assert names[id1] == "report.docx.md"
    assert names[id2] == f"report.docx-{id2[:8]}.md"
    for e in man["docs"]:
        assert (exp / e["file"]).exists()
        assert (exp / e["file"]).read_bytes() == _zip_content(root, e["doc_id"])
    # 后缀不得串到另一篇：第二篇内容不属于第一篇
    assert "第二篇" in (exp / names[id2]).read_text(encoding="utf-8")
    assert "第一篇" in (exp / names[id1]).read_text(encoding="utf-8")


def test_docs_export_all_covers_library(saved_doc, tmp_path, capsys):
    """`--all`：manifest.count == 库内文档数，每个条目落盘且字节等价。"""
    root = _root(tmp_path)
    exp = tmp_path / "export-all"
    assert main(["docs", "export", "--all", "--root", str(root),
                 "--out", str(exp)]) == 0
    capsys.readouterr()
    total = _sql_count(root, "SELECT COUNT(*) AS c FROM docs")
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    assert man["count"] == total >= 1
    listed = {e["doc_id"] for e in man["docs"]}
    assert listed == {r["doc_id"] for r in json.loads(
        _run_list(root, capsys))["docs"]}
    for e in man["docs"]:
        assert (exp / e["file"]).read_bytes() == _zip_content(root, e["doc_id"])


def _run_list(root: Path, capsys) -> str:
    assert main(["docs", "list", "--root", str(root)]) == 0
    return capsys.readouterr().out


def test_docs_export_rejects_traversal_doc_id(saved_doc, tmp_path, capsys):
    """④ 恶意 doc_id 走同一消毒闸：路径穿越形态在拼 zip 路径前即被拒
    （E4003 / 退出码 4），且导出目录外不落任何文件。"""
    root = _root(tmp_path)
    exp = tmp_path / "export"
    rc = main(["docs", "export", "../../etc/passwd", "--root", str(root),
               "--out", str(exp)])
    assert rc == 4
    err = capsys.readouterr().err
    assert "E4003" in err
    assert not (tmp_path / "etc").exists(), "路径穿越不得在库外落文件"
    assert not list(exp.glob("*.md")), "失败篇不得留下半截 md"


def test_docs_export_unknown_id_reports_and_continues(saved_doc, tmp_path,
                                                      capsys):
    """批内单篇失败不中断其余导出（与 convert 多文件口径一致）；
    失败篇留受控 E4041 payload，manifest 只含成功篇。"""
    _client, _tokens, doc_id = saved_doc
    root = _root(tmp_path)
    exp = tmp_path / "export"
    rc = main(["docs", "export", "0" * 16, doc_id, "--root", str(root),
               "--out", str(exp)])
    assert rc == 4
    err = capsys.readouterr().err
    assert "E4041" in err
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    assert [e["doc_id"] for e in man["docs"]] == [doc_id]
    assert (exp / man["docs"][0]["file"]).read_bytes() == _zip_content(root,
                                                                       doc_id)


def test_docs_export_requires_ids_or_all(saved_doc, tmp_path, capsys):
    """无 doc_id 且无 --all：用法错（2）；两者同给：互斥用法错（2）。"""
    root = _root(tmp_path)
    assert main(["docs", "export", "--root", str(root)]) == 2
    assert "E4001" in capsys.readouterr().err
    _client, _tokens, doc_id = saved_doc
    assert main(["docs", "export", doc_id, "--all", "--root", str(root)]) == 2


# ---------------------------------------------------------------- 单元：命名与消毒
def test_export_md_name_collision_uses_doc_id_suffix():
    """F-14 口径单测：同名不同 doc_id → `-<前 8>`；同 doc_id 重导 → 覆盖
    （不加后缀）；无源名 → doc_id 兜底。"""
    from doc2md.cli import _export_md_name
    id1, id2 = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
    taken: dict = {}
    a = _export_md_name("报告.pdf", id1, taken)
    b = _export_md_name("报告.pdf", id2, taken)
    again = _export_md_name("报告.pdf", id1, taken)
    assert a == "报告.pdf.md"
    assert b == f"报告.pdf-{id2[:8]}.md"
    assert again == a, "同 doc_id 重复导出走覆盖语义，不得再加后缀"
    assert _export_md_name("", "cccccccccccccccc", {}) == "cccccccccccccccc.md"
    assert _export_md_name("notes.md", "dddddddddddddddd", {}) == "notes.md"


def test_export_md_name_rejects_unsafe_source_name():
    """库内 source_name 被污染时（旧库/恢复），导出不得绕过消毒闸落盘。"""
    from doc2md.cli import _export_md_name
    from doc2md.errors import E4003_FileNameUnsafe
    with pytest.raises(E4003_FileNameUnsafe):
        _export_md_name("../../etc/passwd", "eeeeeeeeeeeeeeee", {})


def test_export_rebuilds_missing_zip(saved_doc, tmp_path, capsys):
    """zip 被误删：按 CH-489 口径（DB 是产物事实源）先重建再导出，
    导出字节与重建前一致（重建内容确定性由 artifacts 自身保证）。"""
    root = _root(tmp_path)
    doc_id = saved_doc[2]
    before = _zip_content(root, doc_id)
    (root / "out" / f"{doc_id}.zip").unlink()
    exp = tmp_path / "export"
    assert main(["docs", "export", doc_id, "--root", str(root),
                 "--out", str(exp)]) == 0
    capsys.readouterr()
    man = json.loads((exp / "manifest.json").read_text(encoding="utf-8"))
    assert (exp / man["docs"][0]["file"]).read_bytes() == before
    assert (root / "out" / f"{doc_id}.zip").exists(), "重建的 zip 应落回 out"


def test_docs_show_negative_limit_is_usage_error(saved_doc, tmp_path, capsys):
    """show 的负 limit 同样退 2（_nonneg 单点口径）。"""
    root = _root(tmp_path)
    assert main(["docs", "show", saved_doc[2], "--root", str(root),
                 "--limit", "-5"]) == 2
    assert "E4001" in capsys.readouterr().err


def test_cli_docs_help_lists_subcommands():
    """子命令在 --help 面可见（文档漂移红线：docstring/README 同步的前提）。"""
    from doc2md.cli import build_parser
    ap = build_parser()
    a = ap.parse_args(["docs", "list", "--limit", "10"])
    assert a.cmd == "docs" and a.dc_cmd == "list" and a.limit == 10
    a = ap.parse_args(["docs", "show", "abc123"])
    assert a.dc_cmd == "show" and a.doc_id == "abc123"
    a = ap.parse_args(["docs", "export", "id1", "id2", "--all", "--out", "d"])
    assert a.dc_cmd == "export" and a.doc_ids == ["id1", "id2"] and a.all
