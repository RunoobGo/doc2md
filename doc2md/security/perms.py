"""跨平台私有权限：POSIX mode bits 与 Windows DACL 的统一入口。

NFR-20 的隐私前提是「工作目录/备份副本只对当前用户可读」。POSIX 上用
0700/0600 表达；Windows 的 `os.chmod` 只切换只读属性，mode bits 既不落盘
也不等价于 ACL —— 同一台机器上的其他本地用户仍可读。这里在 Windows 上
移除继承 ACE 并只授予当前用户，在 POSIX 上保持原 chmod 语义并校验结果。

调用方只把返回的 warnings 记入报告，不因权限不可表达而吞掉隐私告警。
"""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

_IDENTITY: str | None = None
_HARDENED: set[str] = set()


def _windows_identity() -> str:
    """当前账户的 `DOMAIN\\user` 形式；whoami 失败才退回环境变量。"""
    global _IDENTITY
    if _IDENTITY:
        return _IDENTITY
    try:
        proc = subprocess.run(
            ["whoami"], capture_output=True, text=True, timeout=5, check=False,
            errors="replace")
        if proc.returncode == 0 and proc.stdout.strip():
            _IDENTITY = proc.stdout.strip()
            return _IDENTITY
    except (OSError, subprocess.SubprocessError):
        pass
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    _IDENTITY = f"{domain}\\{user}" if domain and "\\" not in user else user
    return _IDENTITY


def _cache_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def _harden_windows(path: Path) -> list[str]:
    key = _cache_key(path)
    if key in _HARDENED:
        return []
    identity = _windows_identity()
    grant = f"{identity}:(OI)(CI)F" if path.is_dir() else f"{identity}:F"
    try:
        proc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True, text=True, timeout=10, check=False,
            errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"icacls failed on {path}: {type(exc).__name__}"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        suffix = f": {detail[-1][:120]}" if detail else ""
        return [f"icacls failed on {path}: exit {proc.returncode}{suffix}"]
    _HARDENED.add(key)
    return []


def check_applied(path: str | Path, mode: int) -> list[str]:
    """回读校验：请求的 mode 位没真的落地，说明该卷不表达 POSIX 权限。

    CH-499（B-14）取证：FAT32 / exFAT 映像上 `os.chmod` 是**彻底 no-op**——0600 /
    0644 / 0777 三种请求回读恒为挂载默认的 0700（`noowners` + `fskit`）。原先的判据
    「`mode` 要求 other 全零 **且** 回读 other 非零」在这种卷上永不触发（回读恰好是
    0700），于是收紧失败静默返回空告警、`is_private()` 反手判 True：隐私结论靠的是
    挂载默认值而非本产品的收紧，M8/NFR-20 的「work/ 700、库 600」前提其实从未被执行。
    判据收紧为「请求值 == 实际回读值」，告警文案不变（O-12 的既有断言字面量继续有效）。
    """
    p = Path(path)
    try:
        got = p.stat().st_mode & 0o777
    except OSError:
        return []
    if got != (mode & 0o777):
        return [f"{p} stays {oct(got)} after chmod {oct(mode)}"
                " (volume ignores POSIX permissions)"]
    return []


def ensure_private(path: str | Path, mode: int = 0o700) -> list[str]:
    """把路径收紧到仅当前用户可访问，返回不可表达时的告警。

    Windows 上忽略 POSIX 的 group/other 位：DACL 只授予当前用户，因此
    `work_dir_mode=750` 在 Windows 上会落成更严格的「仅属主」。
    """
    p = Path(path)
    if os.name == "nt":
        return _harden_windows(p)
    try:
        p.chmod(mode)
    except OSError as exc:
        return [f"chmod {oct(mode)} failed on {p}: {type(exc).__name__}"]
    return check_applied(p, mode)


def is_private(path: str | Path) -> bool:
    """当前路径是否已只对当前用户可访问（测试/doctor 用）。"""
    p = Path(path)
    if os.name != "nt":
        try:
            return (p.stat().st_mode & 0o077) == 0
        except OSError:
            return False
    try:
        proc = subprocess.run(
            ["icacls", str(p)], capture_output=True, text=True, timeout=10,
            check=False, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    out = proc.stdout.lower()
    return "(i)" not in out and _windows_identity().lower() in out
