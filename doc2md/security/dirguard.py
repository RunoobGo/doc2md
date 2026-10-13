"""FR-129 目录投递防护 —— API 目录上传与 watch folder 共用同一实现（P-59 / CH-411）。

纪律：符号链接不跟随、深度 ≤ max_depth、跳过隐藏文件、单目录文件数上限。
超限返回 E4133（明确错误码）而非挂起（验收：符号链接循环目录 10s 内扫完）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import E4133_DirTooWide


@dataclass
class WalkLimits:
    follow_symlinks: bool = False
    max_depth: int = 20
    skip_hidden: bool = True
    max_files_per_dir: int = 5000
    deadline_seconds: float = 10.0


@dataclass
class WalkResult:
    files: list[str] = field(default_factory=list)
    skipped_hidden: int = 0
    skipped_symlinks: int = 0
    dirs_visited: int = 0
    truncated: bool = False
    elapsed: float = 0.0
    hit_depth: int = 0


def guard_walk(root: str | os.PathLike, limits: WalkLimits | None = None) -> WalkResult:
    """显式栈遍历 —— 不跟随符号链接，天然免疫链接循环；带硬截止时间。"""
    lim = limits or WalkLimits()
    t0 = time.monotonic()
    res = WalkResult()
    base = Path(root)
    if not base.is_dir():
        raise E4133_DirTooWide(f"not a directory: {root}")
    stack: list[tuple[Path, int]] = [(base, 0)]
    seen_real: set[str] = {os.path.realpath(base)}

    while stack:
        if time.monotonic() - t0 > lim.deadline_seconds:
            res.truncated = True
            break
        cur, depth = stack.pop()
        res.dirs_visited += 1
        res.hit_depth = max(res.hit_depth, depth)
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError:
            continue

        per_dir_files = 0
        for e in entries:
            if time.monotonic() - t0 > lim.deadline_seconds:
                res.truncated = True
                break
            if lim.skip_hidden and e.name.startswith("."):
                res.skipped_hidden += 1
                continue
            is_link = e.is_symlink()
            if is_link and not lim.follow_symlinks:
                res.skipped_symlinks += 1
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth + 1 > lim.max_depth:
                        continue
                    rp = os.path.realpath(e.path)
                    if rp in seen_real:
                        continue
                    seen_real.add(rp)
                    stack.append((Path(e.path), depth + 1))
                elif e.is_file(follow_symlinks=False):
                    per_dir_files += 1
                    if per_dir_files > lim.max_files_per_dir:
                        raise E4133_DirTooWide(
                            f"directory {cur} exceeds max_files_per_dir="
                            f"{lim.max_files_per_dir}",
                            detail={"dir": str(cur), "limit": lim.max_files_per_dir})
                    res.files.append(e.path)
            except OSError:
                continue
        if res.truncated:
            break

    res.elapsed = time.monotonic() - t0
    return res


def scan_or_raise(root: str | os.PathLike, limits: WalkLimits | None = None) -> list[str]:
    """便捷入口：超限直接抛 E4133（API 目录上传路径用）。"""
    return guard_walk(root, limits).files
