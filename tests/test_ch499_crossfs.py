"""tests/test_ch499_crossfs.py — CH-499 / B-14：合成权限卷上的 chmod 不得静默成功。

本机取证（`ops/bench/b14_crossfs_probe.py`，macOS hdiutil 造 FAT32 / exFAT 映像）量到的
反直觉一条：这两类卷上 `os.chmod` **完全是 no-op**——0600 / 0644 / 0777 三种请求回读
恒为挂载默认的 0700。于是旧判据

    if not mode & 0o077 and got & 0o077:        # 「other 位还在」才算没生效
        warn("volume ignores POSIX permissions")

在这种卷上**永不触发**：`got` 恰好是 0700，group/other 全零 → `ensure_private` 静默
返回空告警、`is_private()` 返回 True。隐私结果这次碰巧成立（靠的是 `noowners` 挂载
默认值，不是本产品的收紧），但「work/ 600、state.sqlite 600」这条 M8/NFR-20 前提
在该卷上其实**从未被执行**——换一种挂载参数（Finder 以 0755 挂 FAT 卷、SMB/NFS
noacl、被映射的 Windows 卷）就是真泄漏，而告警面一直是哑的。

修法：把判据从「other 位是否残留」收紧为「请求值与实际回读是否一致」（同一句告警
文案不变，`tests/test_ch469_ops.py` 的 O-12 断言字面量继续有效）。校验抽成
`check_applied(path, mode)`，让这条红线在**没有真合成卷的主机上也可判定**——
按项目纪律不用 `t.Skip`/`xfail` 掩盖平台差异。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from doc2md.security import perms

DECORATIVE = "ignores POSIX"


@pytest.mark.gate
def test_check_applied_agrees_with_observed_mode(tmp_path):
    """回读一致 → 无告警；请求与回读不符 → 必须告警（两端平台同判据）。"""
    f = tmp_path / "state.sqlite"
    f.write_text("x", encoding="utf-8")
    f.chmod(0o600)
    got = f.stat().st_mode & 0o777
    assert perms.check_applied(f, got) == []
    out = perms.check_applied(f, 0o000)
    assert out and DECORATIVE in out[0], out


@pytest.mark.gate
def test_ensure_private_flags_decorative_chmod(tmp_path, monkeypatch):
    """合成权限卷的实测形态（请求 600、回读恒为挂载默认 700）：POSIX 断告警，Windows 断走 DACL 分支。

    这里必须用「回读 700 / 请求 600」而不是「回读 755 / 请求 700」：后者旧判据
    （other 位残留）本来就能抓到，写成红线会恒真；前者才是本轮取证到的静默假信号，
    旧判据在它上面恒返回空告警。
    """
    if os.name == "nt":
        d = tmp_path / "work"
        d.mkdir(mode=0o755)
        # Windows 不看 chmod 回读：收紧走 icacls。捕获命令面，确认没有把「chmod 成功」
        # 当成隐私成立（那是本轮修掉的 POSIX 假信号在 Windows 上的同族误读）。
        seen: list[str] = []

        def fake_run(argv, *a, **kw):
            seen.append(" ".join(str(x) for x in argv))
            done = subprocess.CompletedProcess(argv, 0)
            done.stdout, done.stderr = "", ""
            return done

        monkeypatch.setattr(perms.subprocess, "run", fake_run)
        perms._HARDENED.clear()
        perms.ensure_private(d, 0o700)
        assert any("icacls" in c for c in seen), seen
        return
    f = tmp_path / "state.sqlite"
    f.write_text("x", encoding="utf-8")
    f.chmod(0o700)                      # 卷的挂载默认值，chmod 在它身上不生效
    monkeypatch.setattr(Path, "chmod", lambda self, mode, *a, **kw: None)
    assert (f.stat().st_mode & 0o777) == 0o700
    out = perms.ensure_private(f, 0o600)
    assert out and DECORATIVE in out[0], out


@pytest.mark.gate
def test_ensure_private_quiet_when_mode_really_lands(tmp_path):
    """真 POSIX 卷（本机 APFS / CI 的 ext4）上收紧成功 → 零告警（不得反向变噪）。"""
    d = tmp_path / "work"
    d.mkdir(mode=0o755)
    assert perms.ensure_private(d, 0o700) == []
    assert perms.is_private(d) is True
    f = d / "state.sqlite"
    f.write_text("x", encoding="utf-8")
    assert perms.ensure_private(f, 0o600) == []
    assert perms.is_private(f) is True
