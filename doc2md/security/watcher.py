"""FR-111【修订】watch folder —— 写入稳定性判定 + 递归防护复用（CH-411 / P-58）。

纪律：
① 大小 + mtime 连续 stable_seconds 秒无变化才入队，期间只登记不计算；
② 递归防护（符号链接不跟随 / 深度 / 隐藏文件 / 单目录上限）复用 FR-129 guard_walk。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .dirguard import WalkLimits, guard_walk


@dataclass
class Stamp:
    size: int
    mtime: float


@dataclass
class StabilityTracker:
    """登记 → 稳定 → 入队 的三态登记簿（可脱离 watchdog 单测）。"""
    stable_seconds: float = 5.0
    pending: dict[str, Stamp] = field(default_factory=dict)
    enqueued: list[str] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    first_seen: dict[str, float] = field(default_factory=dict)

    def observe(self, path: str | os.PathLike, now: float | None = None) -> bool:
        """返回 True 表示本次观察后达到稳定、可入队。"""
        t = time.monotonic() if now is None else now
        p = Path(path)
        try:
            st = p.stat()
        except OSError:
            self.pending.pop(str(p), None)
            self.first_seen.pop(str(p), None)
            return False
        key, cur = str(p), Stamp(st.st_size, st.st_mtime)
        prev = self.pending.get(key)
        if prev is None:
            if key not in self.registered:
                self.registered.append(key)
            self.pending[key] = cur
            self.first_seen[key] = t
            return False
        if prev.size != cur.size or prev.mtime != cur.mtime:
            self.pending[key] = cur
            self.first_seen[key] = t              # 变化 → 稳定窗口重置
            return False
        if t - self.first_seen.get(key, t) >= self.stable_seconds:
            self.pending.pop(key, None)
            self.first_seen.pop(key, None)
            if key not in self.enqueued:
                self.enqueued.append(key)
            return True
        return False

    def poll_all(self, root: str | os.PathLike, limits: WalkLimits | None = None,
                 now: float | None = None) -> list[str]:
        """扫描目录并返回本轮稳定可入队的文件。"""
        lim = limits or WalkLimits()
        files = guard_walk(root, lim).files
        ready: list[str] = []
        for f in files:
            if self.observe(f, now):
                ready.append(f)
        return ready
