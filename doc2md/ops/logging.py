"""NFR-19 日志轮转（上游蓝本 B-12，含实测发现的缓冲口径修正）。

关键纪律：轮转大小口径必须用内存字节计数器，不能用 `stat().st_size` ——
文件句柄默认缓冲使磁盘大小滞后（源文档实测 2KB 阈值在 ~8.2KB 才触发，放大 4 倍）。
计数器在打开时以既有文件大小初始化，崩溃重启后口径连续。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RotatingJsonlWriter:
    """单文件 ≥ rotate_mb 或跨 UTC 日即轮转，保留 rotate_keep 份（覆盖最旧）。

    CH-469（O-13）：写入加锁。`serve` 是 threaded 模式，事件、审计、作业日志共用
    同一个实例；原实现「判阈值 → 改名 → 重开句柄 → 写」整段无同步，两个线程同时
    触发轮转时，一方握着已被 rename 走的旧句柄继续写（那行直接落进 `.jsonl.1`
    甚至随覆盖消失），另一方把 `_size` 记到别人重开的文件上——日志丢行且无任何痕迹。
    """

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
        # CH-471（PERF-2）：二进制追加写，日志行**一次编码为 bytes** 再落盘并计入
        # _size（原实现文本模式 write(str) 后又 `len(line.encode())`，每行编两次码）。
        self._fh = open(self._path, "ab")
        self._lock = threading.Lock()
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
        self._fh = open(self._path, "ab")
        self._size = 0
        marker = (json.dumps({"code": "E_LOG_ROTATE", "ts": _now_iso(),
                              "keep": self.rotate_keep}, ensure_ascii=False) + "\n"
                  ).encode("utf-8", errors="replace")
        self._fh.write(marker)
        self._size += len(marker)
        self.rotations += 1

    def write(self, record: dict) -> None:
        # NFR-20：日志不得含正文片段 —— 长文本字段一律截断为指纹
        data = (json.dumps(_scrub(record), ensure_ascii=False, default=str) + "\n"
                ).encode("utf-8", errors="replace")
        with self._lock:
            if self._should_rotate():
                self._rotate()
            self._fh.write(data)
            self._size += len(data)

    def flush(self) -> None:
        with self._lock:
            self._fh.flush()

    def close(self) -> None:
        try:
            with self._lock:
                self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # NFR-19 由服务内置执行：提供显式检查口供 cron --daily 调用
    def check(self) -> bool:
        with self._lock:
            if self._should_rotate():
                self._rotate()
                return True
            return False


SCRUB_MAX_LEN = 64


def _fingerprint(s: str) -> str:
    import hashlib
    return f"<sha256:{hashlib.sha256(s.encode('utf-8', 'replace')).hexdigest()[:12]}>"


def scrub_record(rec):
    """NFR-20 脱敏：**任意**字符串超过 `SCRUB_MAX_LEN` 即换指纹（dict / list 递归）。

    CH-459（A-12）：原实现按**键名提示**（text/before/after/body/snippet/content）
    决定是否脱敏——提示名单外的键即使带上大段正文也原样入日志，而事件 payload
    的键名由调用方自由给出，这个名单必然漏。与 `api` 侧口径统一为「不看键名、
    只看长度」后，两条写入路径（库内 events.payload_json 与 JSONL 日志）一致。

    指纹保留可关联性（同内容同指纹）但不保留内容（NFR-20）。
    """
    if isinstance(rec, dict):
        return {k: scrub_record(v) for k, v in rec.items()}
    if isinstance(rec, (list, tuple)):
        return [scrub_record(v) for v in rec]
    if isinstance(rec, (bytes, bytearray)):
        # R6-OPS-2（CH-475）：bytes 不经此分支会被 json.dumps(default=str) 在
        # _scrub **之后**才逐字字符串化，绕过「>SCRUB_MAX_LEN 换指纹」——正文/
        # 二进制片段可经 bytes 通道原样泄露进日志（NFR-20）。先解码再走 str 长度规则。
        rec = bytes(rec).decode("utf-8", "replace")
    if isinstance(rec, str) and len(rec) > SCRUB_MAX_LEN:
        return _fingerprint(rec)
    return rec


_scrub = scrub_record          # 旧私有名（模块内部沿用）


class EventBus:
    """全局事件总线（FR-125 硬规则：前端只用总线；per-job 端点仅 CLI / 脚本 UA）。

    CH-401：本类是唯一对前端暴露的事件通道实现，事件写入 RotatingJsonlWriter，
    并按 [ops].events_retention_days 由 cron 清库。
    """

    def __init__(self, writer: RotatingJsonlWriter):
        self.writer = writer
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, code: str, **payload) -> dict:
        # O-13：`_seq += 1` 与写入必须同临界区。分开做会得到「seq 递增、文件里却
        # 倒序」的日志——按 seq 做增量读取的消费方会永久漏掉那几行。
        with self._lock:
            self._seq += 1
            evt = {"seq": self._seq, "ts": _now_iso(), "code": code, **payload}
            self.writer.write(evt)
        return evt
