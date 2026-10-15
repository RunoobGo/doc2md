"""NFR-6 磁盘门禁 —— 方案 §「日志轮转退化为不轮转 + 告警，NFR-6 磁盘门禁兜底」，
v2.4 全文只引用、从未落地（CH-450 登记）。低配机（Win11 系统盘常年 <5GB 可用）
在批量转换时最容易踩的不是内存而是「写一半 ENOSPC」——zip 半截、sqlite 事务挂起。

口径：**写之前**按待写量 × 安全系数估算，不足即拒收（413 / E4131），不做「先写再失败」。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import E4131_TooLarge


def free_mb(path: str | Path) -> int:
    """目标路径所在卷的可用容量（MB）。卷不存在时按 0 处理，交由调用方判定。

    CH-469（O-6）：口径是 **available**（非特权进程真能写进去的量）而不是 free。
    ext4/APFS 都给 root 预留了一块（默认 5%），`shutil.disk_usage().free` 把预留
    算进来 → 普通进程实际可用的量被高估；低配机「系统盘常年 <5GB」正是这条门禁的
    设计场景，高估即放行、写完即 ENOSPC，门禁形同虚设。
    `os.statvfs` 只在 POSIX 上有；Windows 回退 free（该平台上二者同值）。
    """
    p = Path(path)
    probe = p
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(available_bytes(probe) / (1024 * 1024))
    except OSError:
        return 0


def available_bytes(path: str | Path) -> int:
    """卷上「本进程可写」的字节数：两个来源取更小者（宁可早拒收，不可写完失败）。"""
    target = str(path)
    free = int(shutil.disk_usage(target).free)
    statvfs = getattr(os, "statvfs", None)
    if statvfs is not None:
        try:
            vfs = statvfs(target)
            free = min(free, int(vfs.f_bavail) * int(vfs.f_frsize))
        except OSError:
            pass
    return free


def need_bytes(src_bytes: int, factor: float) -> int:
    """待写量估算：源文件按 factor 放大（zip 压缩 + report.html + 同份 IR + 解压余量）。"""
    return max(int(src_bytes * float(factor)), 1)


def assert_free_space(target_dir: str | Path, src_size: int, *, cfg: dict | None = None,
                      what: str = "artifact") -> int:
    """写产物前检查剩余空间；不足 → E4131_TooLarge（413）。返回当前可用 MB。

    需求 = max([perf].min_free_mb, 源大小 × [perf].disk_safety_factor)：
    前者是「不管转什么文件都要留的余量」（日志、sqlite WAL），后者防「小文件
    把盘写到只剩 1MB」。两项同时置 0 = 显式关闭门禁（配额自管的挂载点）。
    """
    factor = 3.0
    floor_mb = 512
    if cfg is not None:
        from .. import config
        factor = float(config.get(cfg, "perf.disk_safety_factor", factor) or 0)
        floor_mb = int(config.get(cfg, "perf.min_free_mb", floor_mb))
    avail_mb = free_mb(target_dir)
    need_mb = max(max(floor_mb, 0), int(need_bytes(src_size, max(factor, 0.0))
                                        / (1024 * 1024)) if factor > 0 else 0)
    if need_mb and avail_mb < need_mb:
        raise E4131_TooLarge(
            f"insufficient free space for {what}: need {need_mb}MB, free {avail_mb}MB",
            detail={"target": str(target_dir), "free_mb": avail_mb, "need_mb": need_mb,
                    "rule": "NFR-6"})
    return avail_mb
