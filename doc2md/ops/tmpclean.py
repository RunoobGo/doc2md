"""work/tmp 残留清理（CH-496 / F9：自 cron 迁出）。

`serve` 启动与 `cron --daily` 共用同一路径口径，两者都不该依赖编排层
（`cron`）来获得这个能力——原实现让入口层 `api` 反向 import `cron`，把
「定时编排」与「临时目录治理」绑在一起。此处独立成 ops 模块，两个调用方
同向依赖。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import config
from ..security.dirguard import is_link_like
from .drill import DRILL_DIR_NAME


def sweep_tmp(cfg: dict, root: Path) -> int:
    """清理 work/tmp 残留（方案 6.5：服务启动与 cron --daily 共用同一路径口径）。

    CH-473（R5-STOR-4）：加**在途保护窗**。原实现无条件清空 tmp，与并发
    `doc2md work`（同一 root、tmp 落在 paths.tmp）无互斥——服务启动清扫 /
    cron --daily 会把在途转换的中间产物删掉，该文件转换失败（可重试，但无人值守
    时段周期性误伤，且失败原因与「磁盘已满」等不可区分）。现在 mtime 在
    `perf.stale_job_minutes`（默认 30）内的条目跳过（在途件随写入持续更新 mtime；
    目录取自身 mtime，内容增删即刷新）；该键 <= 0 = 关闭保护、维持旧行为全清。
    返回值仍是「已清条数」——被跳过的在途件不计数，下轮（或 cron 每日）再清。
    CH-476（R7-OPS-1）：`drill-*` 前缀（`backup drill --keep` 的取证沙箱）**永久豁免**，
    不受 mtime 窗口约束——取证目录必须在下一次每日作业之后仍然存在。
    """
    tmp = config.path_setting(cfg, "paths.tmp", root, str(root / "tmp"))
    cleaned = 0
    if not tmp.exists():
        return cleaned
    try:
        window_s = float(config.get(cfg, "perf.stale_job_minutes", 30) or 0) * 60.0
    except (TypeError, ValueError):
        window_s = 30 * 60.0
    cutoff = time.time() - window_s if window_s > 0 else 0.0
    for p in tmp.iterdir():
        # CH-476（R7-OPS-1）：`backup drill --keep` 的取证沙箱（`drill-<ts>/`）就在
        # 同一个 tmp 下，「保留演练沙箱目录（取证用）」不得被下一次 cron/serve 清扫
        # 兑现成删除——`drill-*` 前缀永久豁免。不带 --keep 时 drill 收尾自删，
        # 故这里被保护的只有用户明确要求留存的目录（不会无限堆积）。
        if p.name.startswith(f"{DRILL_DIR_NAME}-"):
            continue
        if cutoff:
            try:
                if p.stat().st_mtime >= cutoff:
                    continue          # 保护窗内的在途件：跳过
            except OSError:
                continue              # 取不到 mtime（竞态删除中）——按在途处理
        try:
            if is_link_like(p):
                # Windows junction 的 is_dir() 为 True；rmtree 会进入目标递归删除。
                # 只移除链接本身（rmdir 对目录链接/junction 生效）。
                p.rmdir() if p.is_dir() else p.unlink()
            else:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            cleaned += 1
        except OSError:
            pass
    return cleaned
