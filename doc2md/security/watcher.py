"""FR-111【修订】watch folder —— 写入稳定性判定 + 递归防护复用（CH-411 / P-58）。

纪律：
① 大小 + mtime 连续 stable_seconds 秒无变化才入队，期间只登记不计算；
② 递归防护（符号链接不跟随 / 深度 / 隐藏文件 / 单目录上限）复用 FR-129 guard_walk。

CH-464：观察语义收敛为「同内容只报一次稳定」——首见快速通道（CH-462 引入，
修 `watch --once` 死路径）与常规 pending 路径此前都只对 `enqueued` 做**追加时**
去重，`return True` 本身不去重：常驻模式下旧文件每轮 poll 都再次返回 True、
已稳定入队的文件弹窗后重新登记再等一个窗口又返回 True → 每轮把目录内全部
文件重转换一遍（CPU/磁盘打满、库反复重建）。现在 `enqueued` 记录**提交时戳**：
未变 → False；重写（size/mtime 变化）→ 弹回 pending 重新走窗口（保留热更新
能力）。登记簿改 set/dict 消除原 list 的每 key O(n) 扫描。

CH-471（PERF-1 / 决策 D3）：本轮**保持纯轮询为默认**（不引入 watchdog，守住 4 运行
时依赖闭环这一核心价值）。size+mtime 短路（上段 `enqueued` 判据）跳过的是「未变更
文件的重复转换/重复内容处理」——已提交且未变的文件每轮只做一次 stat + O(1) 字典
比对即返回 False。**口径澄清（CH-473 / R5-PERF-1）**：目录扫描本身（poll_all →
guard_walk 全树 scandir）仍是每轮全量 O(files)，短路口省的是转换/哈希而非扫描——
原 docstring「未变文件每轮只做 O(1) 字典比对」忽略了扫描成本，已修正。扫描侧短路
（如目录级 mtime 哨兵）与事件驱动快路径（watchdog + 轮询对账）登记为未来可选
增强，其大目录 CPU 收益需真机测量（登记 X-11）。
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
    enqueued: dict[str, Stamp] = field(default_factory=dict)   # 路径 → 提交时戳（CH-464）
    first_seen: dict[str, float] = field(default_factory=dict)
    # CH-469（O-8）：连续 N 轮没在清单里见到 → 判定已消失，出登记簿
    stale_rounds: int = 3
    misses: dict[str, int] = field(default_factory=dict)
    last_truncated: bool = False

    def prune(self, seen: set[str]) -> list[str]:
        """回收已删除文件的观察状态。

        `observe()` 在 stat 失败时会清账，但**删除的文件根本不会再进本轮清单**
        （poll_all 只遍历扫描结果），所以那条清理路径对它们永远不会触发：常驻
        watch 跑几天，登记簿里全是消失文件的路径 + Stamp（内存与 fd 名册都在涨），
        而且同名文件日后重新落盘会立刻命中 `enqueued` 里的旧 Stamp——尺寸和 mtime
        恰好相近时被当成「已提交过」而漏转换。
        """
        dropped: list[str] = []
        tracked = set(self.pending) | set(self.enqueued) | set(self.first_seen)
        for key in seen:
            self.misses.pop(key, None)
        for key in tracked - seen:
            n = self.misses.get(key, 0) + 1
            if n < max(1, self.stale_rounds):
                self.misses[key] = n
                continue
            self.pending.pop(key, None)
            self.enqueued.pop(key, None)
            self.first_seen.pop(key, None)
            self.misses.pop(key, None)
            dropped.append(key)
        return dropped

    def observe(self, path: str | os.PathLike, now: float | None = None) -> bool:
        """返回 True 表示本次观察后达到稳定、可入队。

        同内容只返回一次 True；重写（size/mtime 变化）后重新走稳定窗口。
        """
        t = time.monotonic() if now is None else now
        p = Path(path)
        key = str(p)
        try:
            st = p.stat()
        except OSError:
            self.pending.pop(key, None)
            self.first_seen.pop(key, None)
            return False
        cur = Stamp(st.st_size, st.st_mtime)
        if self.stable_seconds <= 0:
            # 显式零窗口 = 首次观察即稳定；不能依赖 wall-clock - mtime 的
            # 符号（Windows 上 mtime 可能略超前，导致 0 秒窗口永远不触发）。
            self.enqueued[key] = cur
            return True
        done = self.enqueued.get(key)
        if done is not None:
            if done.size == cur.size and done.mtime == cur.mtime:
                return False                          # CH-464：已提交且未变，不重复
            # 重写 → 弹回 pending 重新走稳定窗口（热更新语义）
            self.enqueued.pop(key, None)
            self.pending[key] = cur
            self.first_seen[key] = t
            return False
        prev = self.pending.get(key)
        if prev is None:
            # CH-462：首见文件若 mtime 已老于稳定窗口（写入发生在观察开始之前），
            # 直接判稳定——否则 `watch --once`（cron 模式，每次全新进程、没有
            # 「上一轮」可对比）单轮观察永远返回 False，文件永远不被提交。
            # 常驻模式不受影响：进程启动前就在目录里的老文件同样应当首轮提交。
            # 判定用墙钟（mtime 是 epoch 基准，与 monotonic 的 `t` 不同基）。
            # mtime 在未来（时钟跳变 / 跨机拷贝）时 age 为负：不判稳定，进
            # pending 走常规窗口——墙钟追上后按常规语义处理，避免未来文件卡死。
            if (time.time() - st.st_mtime) >= self.stable_seconds:
                self.enqueued[key] = cur
                return True
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
            self.enqueued[key] = cur
            return True
        return False

    def poll_all(self, root: str | os.PathLike, limits: WalkLimits | None = None,
                 now: float | None = None) -> list[str]:
        """扫描目录并返回本轮稳定可入队的文件。"""
        lim = limits or WalkLimits()
        res = guard_walk(root, lim)
        ready: list[str] = []
        seen: set[str] = set()
        for f in res.files:
            key = str(Path(f))
            seen.add(key)
            if self.observe(key, now):
                ready.append(f)
        # 截断对常驻轮询是可容忍的（下一轮补齐），但必须留痕：调用方据此知道
        # 「这一轮没扫完」，而不是把半结果当完整清单（O-10 的同一条纪律）。
        self.last_truncated = res.truncated
        if not res.truncated:
            self.prune(seen)                    # 截断轮的清单不完整，不做删除判定
        return ready
