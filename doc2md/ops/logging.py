"""NFR-19 日志轮转（附录 B-12 蓝本，含实测发现的缓冲口径修正）。

关键纪律：轮转大小口径必须用内存字节计数器，不能用 `stat().st_size` ——
文件句柄默认缓冲使磁盘大小滞后（源文档实测 2KB 阈值在 ~8.2KB 才触发，放大 4 倍）。
计数器在打开时以既有文件大小初始化，崩溃重启后口径连续。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RotatingJsonlWriter:
    """单文件 ≥ rotate_mb 或跨 UTC 日即轮转，保留 rotate_keep 份（覆盖最旧）。"""

    def __init__(self, directory, stem: str = "app", rotate_mb: float = 50,
                 rotate_keep: int = 5):
        self.dir = Path(directory)
        self.stem = stem
        self.rotate_mb = float(rotate_mb)
        self.rotate_bytes = int(self.rotate_mb * 1024 * 1024)
        self.rotate_keep = max(1, int(rotate_keep))
        self.dir.mkdir(parents=True, exist_ok=True)
        self._day = self._utc_day()
        self._path = self.dir / f"{stem}.jsonl"
        self._size = self._path.stat().st_size if self._path.exists() else 0
        self._fh = open(self._path, "a", encoding="utf-8", errors="replace")
        self.rotations = 0

    @staticmethod
    def _utc_day() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _should_rotate(self) -> bool:
        return self._utc_day() != self._day or self._size >= self.rotate_bytes

    def _rotate(self) -> None:
        self._fh.close()
        oldest = self.dir / f"{self.stem}.jsonl.{self.rotate_keep}"
        if oldest.exists():
            oldest.unlink()
        for i in range(self.rotate_keep - 1, 0, -1):
            src = self.dir / f"{self.stem}.jsonl.{i}"
            if src.exists():
                src.replace(self.dir / f"{self.stem}.jsonl.{i + 1}")
        if self._path.exists():
            self._path.replace(self.dir / f"{self.stem}.jsonl.1")
        self._day = self._utc_day()
        self._fh = open(self._path, "a", encoding="utf-8", errors="replace")
        self._size = 0
        marker = json.dumps({"code": "E_LOG_ROTATE", "ts": _now_iso(),
                             "keep": self.rotate_keep}, ensure_ascii=False) + "\n"
        self._fh.write(marker)
        self._size += len(marker.encode("utf-8"))
        self.rotations += 1

    def write(self, record: dict) -> None:
        # NFR-20：日志不得含正文片段 —— 长文本字段一律截断为指纹
        line = json.dumps(_scrub(record), ensure_ascii=False, default=str) + "\n"
        if self._should_rotate():
            self._rotate()
        self._fh.write(line)
        self._size += len(line.encode("utf-8"))

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # NFR-19 由服务内置执行：提供显式检查口供 cron --daily 调用
    def check(self) -> bool:
        if self._should_rotate():
            self._rotate()
            return True
        return False


_LONG_KEYS_HINT = ("text", "before", "after", "body", "snippet", "content")


def _scrub(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and any(h in k for h in _LONG_KEYS_HINT) and len(v) > 64:
            import hashlib
            out[k] = f"<sha256:{hashlib.sha256(v.encode('utf-8', 'replace')).hexdigest()[:12]}>"
        elif isinstance(v, dict):
            out[k] = _scrub(v)
        else:
            out[k] = v
    return out


class EventBus:
    """全局事件总线（FR-125 硬规则：前端只用总线；per-job 端点仅 CLI / 脚本 UA）。

    CH-401：本类是唯一对前端暴露的事件通道实现，事件写入 RotatingJsonlWriter，
    并按 [ops].events_retention_days 由 cron 清库。
    """

    def __init__(self, writer: RotatingJsonlWriter):
        self.writer = writer
        self._seq = 0

    def emit(self, code: str, **payload) -> dict:
        self._seq += 1
        evt = {"seq": self._seq, "ts": _now_iso(), "code": code, **payload}
        self.writer.write(evt)
        return evt
