"""CH-505 / OPT4-11：`doc2md/security/perms.py` 覆盖补齐（40% → ≥85%）。

NFR-20 的隐私前提（work/ 700、库 600）此前只有「正常 chmod 成功」一条路径被测，
其余判定面全部裸露：Windows DACL 分支（本机不可执行真实 icacls）、FAT32 类
「chmod 是 no-op」的回读判定、whoami 失败回退、icacls 非零退出与子进程缺失。

矩阵口径：**平台 × 入口 × 失败面**
- 平台：posix / nt（nt 用 `perms.os` 代理 + `subprocess.run` 注入验证判定逻辑，
  真实 DACL 走查仍属 B-14 受限面，不在本文件冒充已验）；
- 入口：`ensure_private` / `is_private` / `check_applied` / `_harden_windows` /
  `_windows_identity` / `_cache_key`；
- 失败面：路径消失、chmod 不落地、icacls 非零退出、子进程缺失、身份不可得。

缓存（`_IDENTITY` / `_HARDENED`）是模块级状态，逐用例重置——否则用例顺序
会决定 whoami 是否被再次调用（假绿）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from doc2md.security import perms

pytestmark = [pytest.mark.gate]


class _NtOs(SimpleNamespace):
    """`os` 代理：只给 perms 用到的 `name` / `environ` 两个面。"""

    name = "nt"
    environ = os.environ


@pytest.fixture(autouse=True)
def _reset_perm_caches():
    perms._IDENTITY = None
    perms._HARDENED.clear()
    yield
    perms._IDENTITY = None
    perms._HARDENED.clear()


@pytest.fixture
def nt(monkeypatch):
    monkeypatch.setattr(perms, "os", _NtOs())
    monkeypatch.setattr(perms, "_IDENTITY", "DOM\\alice")
    return _NtOs()


def _done(cmd, rc=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


# ------------------------------------------------------------ _windows_identity
def test_windows_identity_prefers_whoami_and_caches(monkeypatch):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return _done(cmd, 0, out="DOM\\alice\n")

    monkeypatch.setattr(perms.subprocess, "run", fake)
    assert perms._windows_identity() == "DOM\\alice"
    assert perms._windows_identity() == "DOM\\alice"
    assert calls == [["whoami"]], "身份必须缓存：每次 icacls 都跑 whoami 是白付子进程"


def test_windows_identity_falls_back_to_env_when_whoami_fails(monkeypatch):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 5, err="denied"))
    monkeypatch.setenv("USERNAME", "bob")
    monkeypatch.setenv("USERDOMAIN", "CORP")
    assert perms._windows_identity() == "CORP\\bob"


def test_windows_identity_fallback_without_domain_and_userslash(monkeypatch):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 1))
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.setenv("USERNAME", "CORP\\carol")
    assert perms._windows_identity() == "CORP\\carol", "已带域名的用户名不得重复拼域"


def test_windows_identity_survives_missing_whoami(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("whoami")

    monkeypatch.setattr(perms.subprocess, "run", boom)
    monkeypatch.setenv("USERNAME", "dave")
    monkeypatch.delenv("USERDOMAIN", raising=False)
    assert perms._windows_identity() == "dave"


# ---------------------------------------------------------------- _cache_key
def test_cache_key_normalises_case_and_resolution(tmp_path):
    key = perms._cache_key(tmp_path)
    assert key == str(tmp_path.resolve()).lower()


def test_cache_key_survives_resolve_failure(monkeypatch):
    def boom(self, *a, **k):
        raise OSError("gone")

    monkeypatch.setattr(Path, "resolve", boom)
    assert perms._cache_key(Path("/TMP/Mixed")) == "/tmp/mixed"


# ------------------------------------------------------------ _harden_windows
def test_harden_windows_skips_cached_path_without_subprocess(nt, monkeypatch):
    p = Path("/data/private")
    perms._HARDENED.add(perms._cache_key(p))

    def boom(*a, **k):
        raise AssertionError("已收紧路径不得再调 icacls")

    monkeypatch.setattr(perms.subprocess, "run", boom)
    assert perms._harden_windows(p) == []


def test_harden_windows_grant_shape_for_file_and_dir(nt, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or _done(cmd, 0))
    f = tmp_path / "db.sqlite"
    f.write_text("x", encoding="utf-8")
    assert perms._harden_windows(f) == []
    assert perms._harden_windows(tmp_path) == []
    assert "DOM\\alice:F" in seen[0], seen
    assert "DOM\\alice:(OI)(CI)F" in seen[1], seen
    assert seen[0][:3] == ["icacls", str(f), "/inheritance:r"]
    # 成功路径进缓存：第二次不再拉子进程
    assert perms._cache_key(f) in perms._HARDENED


def test_harden_windows_reports_missing_icacls(nt, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("icacls")

    monkeypatch.setattr(perms.subprocess, "run", boom)
    out = perms._harden_windows(tmp_path)
    assert out == [f"icacls failed on {tmp_path}: FileNotFoundError"]


def test_harden_windows_reports_nonzero_exit_with_last_stderr_line(nt, monkeypatch,
                                                                  tmp_path):
    def fake(cmd, **kw):
        return _done(cmd, 5, err="Access is denied.\n")

    monkeypatch.setattr(perms.subprocess, "run", fake)
    out = perms._harden_windows(tmp_path)
    assert out == [f"icacls failed on {tmp_path}: exit 5: Access is denied."]
    assert perms._cache_key(tmp_path) not in perms._HARDENED, "失败不得进缓存"


def test_harden_windows_reports_nonzero_exit_without_detail(nt, monkeypatch, tmp_path):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 3))
    assert perms._harden_windows(tmp_path) == [
        f"icacls failed on {tmp_path}: exit 3"]


# ---------------------------------------------------------------- check_applied
def test_check_applied_missing_path_is_silent(tmp_path):
    assert perms.check_applied(tmp_path / "nope", 0o600) == []


def test_check_applied_accepts_matching_mode(tmp_path):
    p = tmp_path / "f"
    p.write_text("x", encoding="utf-8")
    p.chmod(0o600)
    assert perms.check_applied(p, 0o600) == []


def test_check_applied_flags_volume_that_ignores_chmod(tmp_path):
    """B-14 取证形态：请求 0600，回读仍是挂载默认——必须留告警，不得静默。"""
    p = tmp_path / "f"
    p.write_text("x", encoding="utf-8")
    p.chmod(0o644)
    out = perms.check_applied(p, 0o600)
    assert out == [f"{p} stays 0o644 after chmod 0o600"
                   " (volume ignores POSIX permissions)"]


# --------------------------------------------------------------- ensure_private
def test_ensure_private_posix_applies_and_verifies(tmp_path):
    p = tmp_path / "db"
    p.mkdir()
    assert perms.ensure_private(p, 0o700) == []
    assert p.stat().st_mode & 0o777 == 0o700
    assert perms.is_private(p)


def test_ensure_private_posix_reports_chmod_failure(tmp_path, monkeypatch):
    def boom(self, mode):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "chmod", boom)
    out = perms.ensure_private(tmp_path / "x", 0o600)
    assert out == [f"chmod 0o600 failed on {tmp_path / 'x'}: OSError"]


def test_ensure_private_windows_delegates_to_icacls(nt, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or _done(cmd, 0))
    assert perms.ensure_private(tmp_path) == []
    assert seen and seen[0][0] == "icacls"
    assert "DOM\\alice:(OI)(CI)F" in seen[0]


def test_ensure_private_windows_returns_warning_on_failure(nt, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise subprocess.SubprocessError("timeout")

    monkeypatch.setattr(perms.subprocess, "run", boom)
    assert perms.ensure_private(tmp_path) == [
        f"icacls failed on {tmp_path}: SubprocessError"]


# ------------------------------------------------------------------- is_private
def test_is_private_posix_modes(tmp_path):
    p = tmp_path / "d"
    p.mkdir()
    p.chmod(0o755)
    assert perms.is_private(p) is False
    p.chmod(0o700)
    assert perms.is_private(p) is True


def test_is_private_posix_missing_path_is_false(tmp_path):
    assert perms.is_private(tmp_path / "gone") is False


def test_is_private_windows_reads_icacls(nt, monkeypatch, tmp_path):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 0, out="DOM\\alice:(F)\n"))
    assert perms.is_private(tmp_path) is True


def test_is_private_windows_inherited_ace_is_not_private(nt, monkeypatch, tmp_path):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 0, out="DOM\\alice:(I)(F)\n"))
    assert perms.is_private(tmp_path) is False


def test_is_private_windows_icacls_failure_and_missing_binary(nt, monkeypatch,
                                                             tmp_path):
    monkeypatch.setattr(perms.subprocess, "run",
                        lambda cmd, **kw: _done(cmd, 1, err="denied"))
    assert perms.is_private(tmp_path) is False

    def boom(*a, **k):
        raise OSError("no icacls")

    monkeypatch.setattr(perms.subprocess, "run", boom)
    assert perms.is_private(tmp_path) is False
