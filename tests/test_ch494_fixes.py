"""CH-494（X-18）：备份锁 PID 活性探测的 Windows 安全性与回归红线。

背景：CH-493 的 `_lock_holder_dead` 用 `os.kill(pid, 0)` 做持有者存活探测。
POSIX 上 signal 0 是标准的存在性探测（不发信号）；但 CPython 的 Windows
实现里 `signal.CTRL_C_EVENT` 的整数值就是 0，与「signal 0」在 C 层混同
（bpo-14484，2012 年至今未修）——`posixmodule.c` 的 `os_kill_impl` 命中
`sig == CTRL_C_EVENT` 分支，执行 `GenerateConsoleCtrlEvent(0, pid)`：
**向 pid 所在进程组广播 Ctrl+C**。CH-493 落地后，CI 的 Windows full-suite
腿连续两次在 ~56% 处被 KeyboardInterrupt 打断（Linux 五腿——3× merge-gate
+ full-suite + lint——全绿），退出码
1、中断帧 `threading.py:355`（主线程 join 线程期间收到信号）——即
X-18。CH-494 把探测收进 `_pid_alive` / `_pid_alive_windows`
（OpenProcess + GetExitCodeProcess，Windows 原生、不发任何信号）。

本文件两条线：
- **静态红线**：`os.kill(x, 0)` 全库只允许出现在 `backup.py::_pid_alive`
  的 POSIX 分支内（该函数有 `os.name == "nt"` 分流）。新增调用点必须走
  封装或先扩白名单——误用的代价是向无关进程组广播 Ctrl+C，而不是报错。
- **行为矩阵**：`_lock_holder_dead` 的「确证才回收、无法确证一律保守」
  口径，逐场景钉死（死 PID / 活 PID / 权限不足 / 非数字 / 空文件 /
  非正 PID），以及 `backup_lock` 在持有者确证已死时可抢锁、无法确证时
  保持 `BackupBusy`。

平台教训（CH-494 第一轮远端实证）：行为矩阵一律在 `_pid_alive` 分派
边界打桩（平台无关）。早期版本曾直接 monkeypatch `os.kill`——在
Windows 上被 `_pid_alive` 的 `nt` 分支整体绕过，对 PID 1 的真实
OpenProcess 探测返回「确证已不在」而断言翻车（远端 run `39404094953`
的 windows-suite 腿 1 failed / 418 passed，非 X-18 复发）。
`os.kill` 级语义由固定 `os.name` 的 `test_pid_alive_posix_semantics`
覆盖，不在此重复。
"""
from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from doc2md.ops import backup

pytestmark = pytest.mark.gate

REPO_ROOT = Path(backup.__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "doc2md"

# 裸 os.kill(x, 0) 唯一合法落点：(文件, 所在函数)
_ALLOWED_SIGNAL_ZERO = {("doc2md/ops/backup.py", "_pid_alive")}


class _SignalZeroVisitor(ast.NodeVisitor):
    """收集裸 `os.kill(<x>, 0)` 调用，记录 (文件, 直接外层函数名)。"""

    def __init__(self, rel: str):
        self.rel = rel
        self.stack: list[str] = []
        self.hits: list[tuple[str, str | None]] = []

    def _visit_fn(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_fn
    visit_AsyncFunctionDef = _visit_fn

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "kill"
                and isinstance(fn.value, ast.Name) and fn.value.id == "os"
                and len(node.args) >= 2):
            second = node.args[1]
            if isinstance(second, ast.Constant) and second.value == 0:
                self.hits.append((self.rel, self.stack[-1] if self.stack else None))
        self.generic_visit(node)


def _signal_zero_sites() -> list[tuple[str, str | None]]:
    sites: list[tuple[str, str | None]] = []
    for f in sorted(PKG_DIR.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        v = _SignalZeroVisitor(rel)
        v.visit(tree)
        sites.extend(v.hits)
    return sites


def test_no_raw_signal_zero_probe_outside_pid_alive():
    """X-18 红线：`os.kill(pid, 0)` 只允许 `backup.py::_pid_alive`（POSIX 分支）。

    Windows 上该调用 = 向目标进程组广播 Ctrl+C（bpo-14484 /
    `posixmodule.c: os_kill_impl` 的 `sig == CTRL_C_EVENT` 分支，
    `CTRL_C_EVENT == 0`）。解析不到生产包或出现意外落点都必须红。
    """
    sites = _signal_zero_sites()
    assert sites, "未能在 doc2md/ 内定位到任何 os.kill(x, 0)——锚点消失，请复核"
    assert set(sites) <= _ALLOWED_SIGNAL_ZERO, (
        f"裸 os.kill(x, 0) 出现在非法落点：{sorted(set(sites) - _ALLOWED_SIGNAL_ZERO)}；"
        "Windows 上它会向目标进程组广播 Ctrl+C，请改走 "
        "doc2md.ops.backup._pid_alive()")


def _write_lock(p: Path, pid: int) -> None:
    p.write_text(f"{pid} {time.time():.0f}", encoding="ascii")


def test_lock_holder_dead_confirms_dead_pid(tmp_path, monkeypatch):
    """确证已不在（探测 False）→ 可回收。"""
    lock = tmp_path / "backup.lock"
    _write_lock(lock, 999999)

    monkeypatch.setattr(backup, "_pid_alive", lambda pid: False)
    assert backup._lock_holder_dead(lock) is True


def test_lock_holder_dead_keeps_lock_when_holder_alive(tmp_path, monkeypatch):
    """持有者存活（探测 True）→ 锁有效，不得回收。"""
    lock = tmp_path / "backup.lock"
    _write_lock(lock, os.getpid())
    monkeypatch.setattr(backup, "_pid_alive", lambda pid: True)
    assert backup._lock_holder_dead(lock) is False


def test_lock_holder_dead_is_conservative_on_permission_error(tmp_path, monkeypatch):
    """无法确证（探测 None：POSIX EPERM / Windows ACCESS_DENIED 或异常）→ 保守。"""
    lock = tmp_path / "backup.lock"
    _write_lock(lock, 1)

    monkeypatch.setattr(backup, "_pid_alive", lambda pid: None)
    assert backup._lock_holder_dead(lock) is False


@pytest.mark.parametrize("raw", ["", "not-a-pid 1700000000", "0 1700000000",
                                 "-1 1700000000", "  \n"])
def test_lock_holder_dead_rejects_unparseable_lock(tmp_path, raw):
    """空文件 / 非数字 / 非正 PID：无从确证，一律保守（退回 mtime 口径）。"""
    lock = tmp_path / "backup.lock"
    lock.write_text(raw, encoding="ascii")
    assert backup._lock_holder_dead(lock) is False


def test_pid_alive_dispatches_to_windows_branch(monkeypatch):
    """`os.name == "nt"` 时必须走 Windows 原生探测，绝不走 os.kill。"""
    seen = {}

    def fake_win(pid):
        seen["pid"] = pid
        return True

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(backup, "_pid_alive_windows", fake_win)
    assert backup._pid_alive(4242) is True
    assert seen == {"pid": 4242}


def test_pid_alive_posix_semantics(monkeypatch):
    """POSIX 三分支：存活 True / 确证死 False / 权限不足 None。"""
    monkeypatch.setattr(os, "name", "posix")

    def dead(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr(os, "kill", dead)
    assert backup._pid_alive(1) is False

    def denied(pid, sig):
        raise PermissionError(pid)

    monkeypatch.setattr(os, "kill", denied)
    assert backup._pid_alive(1) is None

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    assert backup._pid_alive(1) is True


def test_pid_alive_windows_failure_is_conservative():
    """探测原语不可用（非 Windows 上 WinDLL 失败）→ None，绝不当成已死。

    在 Windows CI 腿上本用例真实执行 OpenProcess 路径：对不存在的 PID
    应返回 False，对当前进程应返回 True——两端都在 CI 有覆盖。
    """
    result = backup._pid_alive_windows(999999)
    if os.name == "nt":
        assert result is False
        assert backup._pid_alive_windows(os.getpid()) is True
    else:
        assert result is None


def test_backup_lock_reclaims_when_holder_confirmed_dead(tmp_path, monkeypatch):
    """持有者确证已死 → 遗留锁立即回收（CH-493 A-1 的本意，X-18 后不回归）。"""
    lock = tmp_path / "backup.lock"
    _write_lock(lock, os.getpid())            # 刚建的锁：age=0，mtime 口径不会放行
    monkeypatch.setattr(backup, "_pid_alive", lambda pid: False)
    with backup.backup_lock(tmp_path):        # 不抛 BackupBusy 即通过
        pass


def test_backup_lock_stays_busy_when_probe_inconclusive(tmp_path, monkeypatch):
    """无法确证（None）→ 保持 BackupBusy：宁可多等，不可误抢活锁。"""
    from doc2md.ops.backup import BackupBusy
    lock = tmp_path / "backup.lock"
    _write_lock(lock, os.getpid())
    monkeypatch.setattr(backup, "_pid_alive", lambda pid: None)
    with pytest.raises(BackupBusy):
        with backup.backup_lock(tmp_path):
            pass


def test_backup_lock_stays_busy_when_holder_alive(tmp_path, monkeypatch):
    """持有者存活 → BackupBusy（互斥语义本身不回归）。"""
    from doc2md.ops.backup import BackupBusy
    lock = tmp_path / "backup.lock"
    _write_lock(lock, os.getpid())
    monkeypatch.setattr(backup, "_pid_alive", lambda pid: True)
    with pytest.raises(BackupBusy):
        with backup.backup_lock(tmp_path):
            pass
