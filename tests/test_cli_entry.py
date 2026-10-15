"""tests/test_cli_entry.py — CH-462 批次④：CLI 四个子命令入口层补测（A-16 第 2 项）。

覆盖对象（此前仅底层函数有测，argparse 分发 / cmd_* 入口零覆盖）：
- `serve token`：签发 / --list / --revoke（API 等价物，附录 C）；
- `backup drill`：CLI 分发 + 退出码聚合（run_drill 闭环已有 test_t7_drill 覆盖）；
- `watch --once`：稳定性判定 + 转换 + 入库一条线（stable 窗口用旧 mtime 驱动，不 sleep）；
- `tokenizer export / verify`：GGUF 词表导出与 manifest 校验。

方法论：走 `cli.main(argv)` 真分发（真实 argparse → cmd_* → 退出码），不 mock 内部；
大 IO 用 --root 指向 tmp_path，避免渗入仓库 work/。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from gguf_fixture import build_gguf

from doc2md.cli import build_parser, main

pytestmark = [pytest.mark.milestone("DoD")]


# --- argparse 分发层 ---------------------------------------------------------


def test_parser_accepts_the_four_subcommands():
    ap = build_parser()
    a = ap.parse_args(["watch", "/tmp/x", "--once", "--interval", "1.0"])
    assert a.cmd == "watch" and a.once and a.interval == 1.0
    a = ap.parse_args(["tokenizer", "export", "m.gguf", "--vocab", "v.jsonl"])
    assert a.tz_cmd == "export" and a.gguf == "m.gguf"
    a = ap.parse_args(["serve", "token", "--role", "editor", "--label", "ci"])
    assert a.sub == "token" and a.role == "editor"
    a = ap.parse_args(["backup", "drill", "--keep"])
    assert a.bk_cmd == "drill" and a.keep is True


# --- serve token -------------------------------------------------------------


def test_serve_token_issue_and_revoke(tmp_path, capsys):
    root = str(tmp_path / "work")
    assert main(["serve", "token", "--root", root, "--role", "editor", "--label", "ci"]) == 0
    tok = capsys.readouterr().out.strip()
    assert tok.startswith("d2m_") and len(tok) >= 16

    assert main(["serve", "token", "--root", root, "--list"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [x["role"] for x in rows] == ["editor"]

    assert main(["serve", "token", "--root", root, "--revoke", tok]) == 0
    assert json.loads(capsys.readouterr().out)["revoked"] is True

    assert main(["serve", "token", "--root", root, "--list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["revoked_at"]


# --- backup drill（CLI 分发层；闭环本体见 test_t7_drill.py） --------------------


def test_backup_drill_cli_runs_green(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", "cli-drill-pass")
    assert main(["backup", "drill", "--root", str(tmp_path / "work")]) == 0


def test_backup_drill_cli_failure_exit_code(tmp_path, monkeypatch):
    """缺口令 → E4001 穿透为 DrillFailure → 退出码 4（EXIT_BIZ）。"""
    monkeypatch.delenv("DOC2MD_BACKUP_PASSPHRASE", raising=False)
    assert main(["backup", "drill", "--root", str(tmp_path / "work")]) == 4   # EXIT_BIZ


# --- watch --once --------------------------------------------------------------


def test_watch_once_converts_stable_file(tmp_path, small_pdf):
    drop = tmp_path / "drop"
    drop.mkdir()
    dst = drop / "incoming.pdf"
    shutil.copy(small_pdf, dst)
    old = dst.stat().st_mtime - 60          # 拨旧 mtime：绕开 5s 稳定窗口（不 sleep）
    os.utime(dst, (old, old))
    root = tmp_path / "work"
    assert main(["watch", str(drop), "--once", "--root", str(root)]) == 0
    assert list((root / "out").glob("*.zip")), "watch --once 应完成转换并落产物"


def test_watch_once_ignores_unstable_file(tmp_path, small_pdf):
    """mtime 在稳定窗口内的文件不提交（写入静默窗口语义）。
    拨成未来 mtime：不依赖 copy 与执行间隔的墙钟运气（CH-464）。"""
    drop = tmp_path / "drop2"
    drop.mkdir()
    dst = drop / "fresh.pdf"
    shutil.copy(small_pdf, dst)
    future = dst.stat().st_mtime + 60
    os.utime(dst, (future, future))              # mtime 未来 → 窗口必然未满
    root = tmp_path / "work"
    assert main(["watch", str(drop), "--once", "--root", str(root)]) == 0
    assert not list((root / "out").glob("*.zip"))


# --- tokenizer -------------------------------------------------------------------


def test_tokenizer_export_and_verify(tmp_path):
    gg = tmp_path / "m.gguf"
    build_gguf(gg)
    vocab = tmp_path / "vocab.jsonl"
    manifest = tmp_path / "tok.manifest.json"
    root = str(tmp_path / "work")
    assert main(["tokenizer", "export", str(gg), "--vocab", str(vocab),
                 "--manifest", str(manifest), "--root", root]) == 0
    man = json.loads(manifest.read_text(encoding="utf-8"))
    assert man["n_vocab"] > 0 and Path(man["vocab_file"]).exists()
    assert vocab.read_text(encoding="utf-8").strip()      # 词表非空

    assert main(["tokenizer", "verify", "--manifest", str(manifest),
                 "--gguf", str(gg), "--root", root]) == 0


# --- CH-464：watcher 观察语义（同内容只报一次稳定） -----------------------------
def test_tracker_reports_stable_once_and_retracks_rewrite(tmp_path):
    from doc2md.security.watcher import StabilityTracker
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    old = f.stat().st_mtime - 60
    os.utime(f, (old, old))
    tr = StabilityTracker(stable_seconds=5.0)
    assert tr.observe(f, now=100.0) is True            # 首见快速通道（老文件）
    for _ in range(3):                                 # 常驻模式多轮：不再重复提交
        assert tr.observe(f, now=100.0) is False
    # 重写（mtime 变化）→ 弹回 pending 重新走窗口
    f.write_bytes(b"xx")
    assert tr.observe(f, now=101.0) is False           # 变化 → 重新登记
    assert tr.observe(f, now=107.0) is True            # 静默满窗口 → 再提交一次
    assert tr.observe(f, now=107.5) is False           # 同内容不再重复


def test_tracker_future_mtime_not_stuck(tmp_path):
    """mtime 在未来（时钟跳变/跨机拷贝）：不判稳定、走常规窗口，不永久卡死。"""
    from doc2md.security.watcher import StabilityTracker
    f = tmp_path / "future.pdf"
    f.write_bytes(b"x")
    future = f.stat().st_mtime + 3600
    os.utime(f, (future, future))
    tr = StabilityTracker(stable_seconds=5.0)
    assert tr.observe(f, now=100.0) is False           # 快速通道不误触发
    assert tr.observe(f, now=106.0) is True            # 常规窗口到期正常提交
