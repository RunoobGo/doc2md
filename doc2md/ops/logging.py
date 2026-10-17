"""NFR-19 日志轮转（上游蓝本 B-12，含实测发现的缓冲口径修正）。

关键纪律：轮转大小口径必须用内存字节计数器，不能用 `stat().st_size` ——
文件句柄默认缓冲使磁盘大小滞后（源文档实测 2KB 阈值在 ~8.2KB 才触发，放大 4 倍）。
计数器在打开时以既有文件大小初始化，崩溃重启后口径连续。

NFR-19 的覆盖面对 CH-481 做了补全：改名式轮转只适用于**写入进程自己持有的文件**
（app / cron / work 三份 jsonl）。launchd 捕获通道（`launchd-<档>.{out,err}`）的句柄
握在 launchd 手里、改名会让它继续往旧 inode 追加，故另有一条**就地截断保尾**口径
（`bound_capture_files`），预算沿用 `logs.rotate_mb` / `rotate_keep`，不新增配置键。
"""
from __future__ import annotations

import json
import os
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


# ---------------------------------------------------------------------------
# NFR-19 补全（CH-481）：任务计划的输出捕获文件纳入同一预算口径
# ---------------------------------------------------------------------------

CAPTURE_GLOBS = ("launchd-*.out", "launchd-*.err")


def capture_budget(rotate_mb: float, rotate_keep: int) -> tuple[int, int]:
    """捕获文件的预算：上限沿用 `logs.rotate_mb`，保尾 = 上限 / rotate_keep。

    不新增配置键——捕获通道与 jsonl 属同一条 NFR-19 预算，两个旋钮已经够表达意图。
    `rotate_keep` 的合法下限是 1，此时 `cap // keep == cap`（截断后尺寸不变，等于不做事），
    故保尾的分母取 `max(2, keep)`：任何配置下保尾都 ≤ 上限的一半，判定必然收缩。
    """
    cap = max(1, int(float(rotate_mb) * 1024 * 1024))
    return cap, cap // max(2, int(rotate_keep))


def bound_capture_files(directory, cap_bytes: int, tail_bytes: int) -> list[dict]:
    """把超过 `cap_bytes` 的捕获文件**就地**截到 `tail_bytes`，返回逐档证据。

    为什么只能就地截断、不能轮转：这两份文件的句柄握在 launchd 手里（O_APPEND），
    `RotatingJsonlWriter._rotate()` 那套「改名 + 重开」会让 launchd 继续往已改名的
    旧文件里追加——新文件永远是空的，增长只是换了个地方。截断（保留 inode）不碰
    属主，O_APPEND 的下一笔写入自然落在截断后的新末尾。
    因此本函数**不做 rename、不建 `.1` 兄弟文件**，这是 `tests/test_ch481_capture_clip.py`
    钉住的第一条红线。

    逐档证据只含档名与字节数（NFR-20：不采信捕获内容）。
    """
    out: list[dict] = []
    d = Path(directory)
    if not d.is_dir():
        return out
    for pat in CAPTURE_GLOBS:
        for p in sorted(d.glob(pat)):
            try:
                before = p.stat().st_size
                if before < cap_bytes:
                    continue
                after = _clip_to_tail(p, tail_bytes)
                out.append({"name": p.name, "before": before, "after": after,
                            "dropped": before - after})
            except OSError as exc:
                # 截断失败不构成日流程失败：后果只是「继续占盘」，不是数据丢失。
                # 但必须让日报看得见（记 error 字段 + capture_errors 计数）。
                out.append({"name": p.name, "error": str(exc)[:200]})
    return out


def _clip_to_tail(path: Path, tail_bytes: int) -> int:
    """保留尾部 `tail_bytes` 并**从整行起头**，返回截断后字节数。

    并发追加的取舍：读尾与写回之间若有新行落盘，那几行会随 `truncate` 一起消失。
    捕获面是 stderr 噪声（访问行 / 回溯），最坏丢几行最新输出——不承诺逐行保全，
    只承诺「有界」与「尾部可读」，这两条都在下面的断言里。
    """
    marker = (json.dumps({"code": "E_LOG_CLIP", "ts": _now_iso(),
                          "kept": tail_bytes}, ensure_ascii=False) + "\n"
              ).encode("utf-8", errors="replace")
    with open(path, "rb+") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size <= tail_bytes:
            return size
        fh.seek(size - tail_bytes)
        tail = fh.read(tail_bytes)
        nl = tail.find(b"\n")        # 首行残半截不可读；找不到换行则原样保留（超长单行）
        if nl != -1:
            tail = tail[nl + 1:]
        fh.seek(0)
        fh.write(marker + tail)
        fh.truncate(len(marker) + len(tail))
        fh.flush()
        return len(marker) + len(tail)


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
