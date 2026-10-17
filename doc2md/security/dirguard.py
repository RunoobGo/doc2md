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


def walk_limits(cfg: dict, *, follow_symlinks: bool = False) -> WalkLimits:
    """CH-471（SVC-10）：按 [watch] 配置拼 WalkLimits 的唯一构造器。

    api.post_job / cli._dir_files / cli.cmd_watch 三处原本各自重复同一段 cfg 读取，
    字段与默认值（尤其 deadline）极易漂移。`follow_symlinks` 传入时与配置项取「或」，
    供 CLI 的 `--follow` 覆盖，其余调用方传 False 即纯跟随配置。
    """
    from .. import config
    return WalkLimits(
        follow_symlinks=follow_symlinks or bool(
            config.get(cfg, "watch.follow_symlinks", False)),
        max_depth=int(config.get(cfg, "watch.max_depth", 20)),
        skip_hidden=bool(config.get(cfg, "watch.skip_hidden", True)),
        max_files_per_dir=int(config.get(cfg, "watch.max_files_per_dir", 5000)))



@dataclass
class WalkResult:
    files: list[str] = field(default_factory=list)
    skipped_hidden: int = 0
    skipped_symlinks: int = 0
    # CH-483（R8-SEC-1）：follow_symlinks=True 时的实际跟随 / 越界拒绝计数。
    followed_symlinks: int = 0
    skipped_escaping_symlinks: int = 0
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

        # CH-471（Q-2）：惰性迭代 os.scandir，边扫边判定——文件数一旦超过
        # max_files_per_dir 立即抛 E4133，不必像原 `sorted(os.scandir(cur))` 那样先把
        # 整份目录条目物化进内存再检查（恶意/超大单目录的内存尖峰面）。
        # 顺序确定性：本层收集到的文件与子目录各自排序后再入结果 / 入栈，遍历与产物
        # 顺序仍稳定；成功路径每层文件数 ≤ max_files_per_dir，排序开销有界。
        dir_files: list[str] = []
        sub_dirs: list[str] = []
        try:
            with os.scandir(cur) as it:
                for e in it:
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
                        if is_link:
                            # CH-483（R8-SEC-1）：跟随必须限定在 root 内，且用
                            # realpath 判 containment（链接自身路径永远在 root 下）。
                            real = os.path.realpath(e.path)
                            base_real = os.path.realpath(base)
                            if not (real == base_real
                                    or real.startswith(base_real + os.sep)):
                                res.skipped_escaping_symlinks += 1
                                continue
                            res.followed_symlinks += 1
                        is_dir = e.is_dir() if is_link else e.is_dir(
                            follow_symlinks=False)
                        is_file = e.is_file() if is_link else e.is_file(
                            follow_symlinks=False)
                        if is_dir:
                            if depth + 1 > lim.max_depth:
                                continue
                            rp = os.path.realpath(e.path)
                            if rp in seen_real:
                                continue
                            seen_real.add(rp)
                            sub_dirs.append(e.path)
                        elif is_file:
                            if len(dir_files) + 1 > lim.max_files_per_dir:
                                raise E4133_DirTooWide(
                                    f"directory {cur} exceeds max_files_per_dir="
                                    f"{lim.max_files_per_dir}",
                                    detail={"dir": str(cur),
                                            "limit": lim.max_files_per_dir})
                            dir_files.append(e.path)
                    except OSError:
                        continue
        except OSError:
            # readdir 本身失败（权限/竞态）：整层跳过，且不把已收集的半截条目并入结果
            # （原 sorted() 是全有或全无，这里靠「延后 extend」保持同样语义）。
            continue

        if res.truncated:
            break

        dir_files.sort()
        res.files.extend(dir_files)
        sub_dirs.sort()
        for sp in reversed(sub_dirs):        # 逆序入栈 → 弹出顺序为名升序，遍历确定
            stack.append((Path(sp), depth + 1))

    res.elapsed = time.monotonic() - t0
    return res


def scan_or_raise(root: str | os.PathLike, limits: WalkLimits | None = None) -> list[str]:
    """便捷入口：超限直接抛 E4133（API 目录上传路径用）。

    CH-469（O-10）：`truncated` 同样是「超限」，但原实现只看文件数上限、把截断
    结果当完整清单返回——投递目录没扫完却报告「这就是全部文件」，调用方据此
    入队，未扫到的文件静默丢失（用户视角是「目录里明明有，队列里没有」）。
    """
    res = guard_walk(root, limits)
    if res.truncated:
        raise E4133_DirTooWide(
            f"directory scan truncated after {res.elapsed:.1f}s at {root}",
            detail={"reason": "walk_truncated", "dirs_visited": res.dirs_visited,
                    "collected": len(res.files),
                    "deadline_seconds": limits.deadline_seconds if limits else WalkLimits().deadline_seconds})
    return res.files
