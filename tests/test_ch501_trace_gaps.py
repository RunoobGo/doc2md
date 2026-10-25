"""CH-501 / T5 可追溯缺口补测：被 trace.py 点名的「真未覆盖」项逐条落成行为断言。

这里的测试不是为了让 `trace.uncovered` 好看：每条都先读实现/样本确认真实形态，
再断言**行为事实**（页数、正文 token、错误码与页级明细、zip 条目、HTTP 401 码、
输出降级动作、日报结构、样本字节复现），docstring 里的编号只是索引键。
NFR-14 / 24 本轮已补本地行为断言；只有 NFR-21 / 22 需要长跑或外部环境实测，
保持未覆盖交给 Task 6 分派，不在本文件用裸编号凑覆盖。
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest
from conftest import hdr

from doc2md.errors import Doc2mdError
from doc2md.pipeline.convert import convert

CORPUS = Path(__file__).resolve().parent / "corpus" / "sources"


def test_fr101_text_pdf_parses_page_structure(tmp_path, tmp_cfg):
    """FR-101：文本型 PDF 必须解析出真实页级正文与行结构，不是空壳。"""
    res = convert(CORPUS / "pdf_01_simple_1p.pdf", cfg=tmp_cfg,
                  out_dir=tmp_path / "out")
    assert res.ir.format == "pdf"
    assert res.ir.pages == 1, f"页数 {res.ir.pages} 与样本不符（样本只有 1 页）"
    assert len(res.ir.blocks) >= 4, f"块数 {len(res.ir.blocks)} 过少，行结构未落地"
    assert "这是一份用于验证 PDF 文本转换的最小样本。" in res.markdown
    assert "第二段包含中文、English 与数字 2026。" in res.markdown


def test_fr103_scanned_pdf_gate_rejects_with_page_detail(tmp_path, tmp_cfg):
    """FR-103：扫描件无文本层 → S1.5 整篇拒收 E4221，并给页级明细。"""
    with pytest.raises(Doc2mdError) as ei:
        convert(CORPUS / "pdf_06_scanned_1p.pdf", cfg=tmp_cfg,
                out_dir=tmp_path / "out")
    assert ei.value.code == "E4221"
    pages = ei.value.detail["page_detail"]
    assert len(pages) == 1 and pages[0]["page"] == 1
    assert pages[0]["ok"] is False
    assert pages[0]["chars"] == 0 and pages[0]["lines"] == 0
    assert "no_text_lines" in pages[0]["reasons"], pages[0]["reasons"]


def test_fr105_render_emits_front_matter_and_atx_headings(tmp_path, tmp_cfg):
    """FR-105：渲染产物 = YAML front-matter + ATX 标题正文（真实样本形态）。"""
    res = convert(CORPUS / "pdf_01_simple_1p.pdf", cfg=tmp_cfg,
                  out_dir=tmp_path / "out")
    assert res.zip_path is not None, "给了 out_dir 却没有打包产物"
    with zipfile.ZipFile(res.zip_path) as z:
        content = z.read(f"{res.doc_id}/content.md").decode("utf-8")
    head, body = content.split("\n---\n\n", 1)
    fm = json.loads(head[len("---\n"):])
    assert fm["doc_id"] == res.doc_id and fm["source_name"] == "pdf_01_simple_1p.pdf"
    assert body.splitlines()[:3] == [
        "# 1. Doc2md 简单转换测试", "", "## 1.1 摘要"]


def test_fr106_zip_contains_exactly_three_entries(tmp_path, tmp_cfg):
    """FR-106：产物 zip 恰为 content.md / ir.json / report.html 三条目。"""
    res = convert(CORPUS / "pdf_01_simple_1p.pdf", cfg=tmp_cfg,
                  out_dir=tmp_path / "out")
    assert res.zip_path is not None and res.zip_path.name == f"{res.doc_id}.zip"
    with zipfile.ZipFile(res.zip_path) as z:
        names = sorted(z.namelist())
        assert names == [f"{res.doc_id}/{name}"
                         for name in ("content.md", "ir.json", "report.html")]
        assert json.loads(z.read(f"{res.doc_id}/ir.json"))["meta"]["doc_id"] == res.doc_id
        assert z.read(f"{res.doc_id}/report.html").lower().startswith(b"<!doctype html>")


def test_e4011_unauthorized_body_carries_code(api_client):
    """E4011：受保护端点无 token / 坏 token 都回 401，响应体带 E4011。"""
    client, _tokens = api_client
    for headers in ({}, {"X-Doc2md-Token": "not-a-real-token"}):
        r = client.get("/docs", headers=headers)
        assert r.status_code == 401, (headers, r.status_code)
        assert r.get_json()["code"] == "E4011", r.get_json()


def test_nfr9_role_matrix_401_403_200(api_client):
    """NFR-9：权限分级——无 token 401、reader 越权 403/E4031、admin 放行。"""
    client, tokens = api_client
    assert client.get("/audit/deletions").status_code == 401
    r = client.get("/audit/deletions", headers=hdr(tokens, "reader"))
    assert r.status_code == 403 and r.get_json()["code"] == "E4031"
    assert client.get("/audit/deletions", headers=hdr(tokens, "admin")).status_code == 200


def test_nfr23_console_safe_downgrades_non_utf8(monkeypatch):
    """NFR-23：非 UTF-8 控制台输出降级为 replace，不得因编码崩溃。"""
    from unittest.mock import Mock

    from doc2md import cli

    out, err = Mock(encoding="cp936"), Mock(encoding="cp936")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._console_safe()
    out.reconfigure.assert_called_once_with(errors="replace")
    err.reconfigure.assert_called_once_with(errors="replace")


def test_nfr14_daily_report_lands_structured_counts(tmp_path, cfg):
    """NFR-14：日报每日落盘，含文档数/作业成败/失败码分布的结构键。"""
    from doc2md import cron
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    store = Store(root / "state.sqlite")
    try:
        daily = cron._daily_report(store, root, cfg)
    finally:
        store.close()
    assert daily.ok, "日报步骤必须返回 ok"
    assert daily.path is not None and daily.path.exists(), f"报告未落盘：{daily.path}"
    body = json.loads(daily.path.read_text(encoding="utf-8"))
    for key in ("date", "pipeline", "docs_total", "docs_sharded",
                "tasks", "error_codes", "last_deletion_ts"):
        assert key in body, f"日报缺结构键 {key!r}：现有 {sorted(body)}"
    # 空库上的计数必须真从状态库读，而不是写死常量
    assert body["docs_total"] == 0 and body["docs_sharded"] == 0, body


def test_nfr24_golden_sample_regenerates_byte_identical(tmp_path, monkeypatch):
    """NFR-24：黄金样本两次独立生成逐字节一致（可复现性）。"""
    import make_golden as mg

    monkeypatch.setattr(mg, "FORCE_REGEN", False)
    outputs = []
    for name in ("pass1", "pass2"):
        target = tmp_path / name
        target.mkdir()
        monkeypatch.setattr(mg, "GOLDEN_DIR", target)
        mg.make_small_3p()
        outputs.append((target / "small_3p.pdf").read_bytes())
    assert outputs[0], "small_3p.pdf 未生成"
    assert outputs[0] == outputs[1], "small_3p.pdf 两次生成字节不一致（NFR-24）"
