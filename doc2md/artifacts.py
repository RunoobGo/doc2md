"""artifacts —— 产物重建 / 孤儿清理 / 产物 IR 回读（CH-492/A-1 抽出）。

原 `api._rebuild_artifact(+_body)` 与 `worker._drop_orphan_artifact` /
`worker._read_ir` 的归并模块：api（入口层）与 worker（编排层）同向依赖本
模块，消除两者间的双向循环导入。cli 的 backup restore 路径亦改引本模块。

锁语义原样保留：进程内 64 分片 `_doc_lock` + 跨进程 `_artifact_lock`
（O_EXCL 锁文件 + 心跳续期 + token 所有权，CH-490）。本模块不持有事务；
事务边界由调用方（api._finish_one / worker._finish_ok）掌握。
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from . import SCHEMA_VERSION, config
from .errors import Doc2mdError, E5031_StorageError
from .storage.repo import get_doc, list_blocks, purge_files

if TYPE_CHECKING:
    from .storage.db import Store

# 以下五段自 api.py 原样搬移（CH-492/A-1），勿改行为：
_ARTIFACT_META_KEYS = {
    "doc_id", "source_key", "source_name", "source_name_raw", "format",
    "pages", "sharded", "shard_count", "schema_version", "warnings",
}
_ARTIFACT_META_EXTRA = {"shard_stats", "llm_prompt", "notes"}


# 固定数量的分片锁：按 doc 串行、不同 doc 可并发，且不随文档数无限增长。
_REBUILD_LOCKS = tuple(threading.Lock() for _ in range(64))


def _doc_lock(doc_id: str) -> threading.Lock:
    return _REBUILD_LOCKS[hash(doc_id) % len(_REBUILD_LOCKS)]


def _reclaim_stale_lock(path: Path, seen: os.stat_result,
                        stale_seconds: float) -> bool:
    """回收陈旧锁前复核身份与 mtime，返回是否已清掉（可重试建锁）。

    `seen` 是调用方判定「陈旧」时的 stat。判定与 unlink 之间有两个合法并发：
    - 原持有者心跳刚续期 → mtime 变化，放弃回收（否则误删活锁）；
    - 锁已被别的进程回收并重建 → inode 变化，放弃本轮（否则删掉新持有者的锁）。
    复核后仍陈旧才 unlink；unlink 自身的 TOCTOU 窗口缩到两次 syscall 之间。
    """
    try:
        cur = path.stat()
    except OSError:
        return True                       # 已消失：视为回收成功，重试建锁
    if (cur.st_dev, cur.st_ino) != (seen.st_dev, seen.st_ino):
        return True                       # 已换主：新持有者的锁不受影响
    if cur.st_mtime_ns != seen.st_mtime_ns:
        return False                      # 刚续期：不再陈旧
    if time.time() - cur.st_mtime <= stale_seconds:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


@contextlib.contextmanager
def _artifact_lock(out_dir: Path, doc_id: str, *, timeout: float = 30.0,
                   stale_seconds: float = 60.0,
                   heartbeat_seconds: float = 10.0):
    """跨进程按 doc 串行重建（B-18；CH-490 心跳与所有权）。

    进程内已有分片锁；API 与 worker 是两个进程，仍可能同时 package 同一
    doc。锁文件放 out/.locks，O_EXCL 建锁。CH-490 起：
    - 持锁期间后台线程周期性刷新 mtime；重建超过 60s 不再被误判为陈旧；
    - 锁文件带唯一 token，释放时只清自己的锁——即使发生陈旧回收，旧持有者
      也不会删掉新持有者的锁；
    - 心跳/文件消失时心跳线程自行退出，不影响主流程。
    """
    lock_dir = Path(out_dir) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{doc_id}.lock"
    deadline = time.monotonic() + timeout
    token = uuid.uuid4().hex
    payload = f"{os.getpid()} {time.time():.0f} {token}".encode("ascii")
    stale_seconds = max(0.05, float(stale_seconds))
    heartbeat_seconds = max(0.01, min(float(heartbeat_seconds), stale_seconds / 2))
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                seen = path.stat()
            except OSError:
                seen = None
            if (seen is not None
                    and time.time() - seen.st_mtime > stale_seconds
                    and _reclaim_stale_lock(path, seen, stale_seconds)):
                continue
            if time.monotonic() >= deadline:
                raise E5031_StorageError(
                    "artifact rebuild busy", detail={"doc_id": doc_id})
            time.sleep(0.05)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(heartbeat_seconds):
            try:
                if token not in path.read_text(encoding="ascii"):
                    return
                os.utime(path, None)
            except OSError:
                return

    beat = threading.Thread(target=heartbeat, name="doc2md-artifact-lock",
                            daemon=True)
    beat.start()
    try:
        yield
    finally:
        stop.set()
        beat.join(timeout=max(1.0, heartbeat_seconds * 3))
        try:
            if token in path.read_text(encoding="ascii"):
                path.unlink(missing_ok=True)
        except OSError:
            pass


def rebuild_artifact(store: Store, doc_id: str, cfg: dict, root: Path) -> Path | None:
    """同进程/跨进程按 doc 串行重建；避免旧 revision 后写覆盖新 zip。"""
    out_dir = config.path_setting(cfg, "paths.out", root, str(root / "out"))
    with _doc_lock(doc_id), _artifact_lock(Path(out_dir), doc_id):
        return _rebuild_artifact_body(store, doc_id, cfg, root, out_dir=Path(out_dir))


def _rebuild_artifact_body(store: Store, doc_id: str, cfg: dict,
                           root: Path, *, out_dir: Path | None = None) -> Path | None:
    """CH-485（R9-SVC-2）：从 DB 重建 zip。

    DB 已是完整事实源：blocks 保存 font/links/provenance，docs 保存 package 实际
    使用的 meta 与 S1.5 gate。PATCH/rollback/E4091/恢复后调用，保证导出与库面
    一致，而不是依赖初始转换留下的旧 zip。CH-489：`gate_json` 为空只表示旧库
    没有页级门禁明细，不表示 blocks 不可重建；报告降级为空 gate 而不是拒绝重建。
    """
    from .ir import IR
    from .pipeline import s4_render
    from .pipeline.convert import package

    d = get_doc(store, doc_id)
    gate_raw = d.get("gate_json") or "{}"
    meta = d.get("meta") or {}
    try:
        gate = json.loads(gate_raw) if isinstance(gate_raw, str) else gate_raw
    except ValueError:
        gate = {}
    blocks = list_blocks(store, doc_id)
    extra = {k: v for k, v in meta.items()
             if k not in _ARTIFACT_META_KEYS and k not in _ARTIFACT_META_EXTRA}
    ir = IR(
        doc_id=d["doc_id"], source_key=d["source_key"],
        source_name=d.get("source_name", "unnamed"),
        source_name_raw=d.get("source_name_raw", ""),
        format=d.get("format", "pdf"), pages=int(d.get("pages", 0) or 0),
        blocks=blocks, sharded=bool(d.get("sharded")),
        shard_count=int(d.get("shard_count", 1) or 1),
        warnings=list(meta.get("warnings", []) or []),
        schema_version=int(meta.get("schema_version", SCHEMA_VERSION)),
        extra_meta=extra,
    )
    md = s4_render.render(
        ir,
        preserve_strike=bool(config.get(cfg, "s4.preserve_marks_strike", False)),
        link_internal=str(config.get(cfg, "s4.link_internal", "plain")),
    )
    out_dir = Path(out_dir) if out_dir is not None else config.path_setting(
        cfg, "paths.out", root, str(root / "out"))
    out = package(ir, md, meta, out_dir, cfg, gate=gate)
    # package 期间文档可能被删除：提交后的删除路径会再 purge 一次，这里也做
    # 发布后复核，避免返回一个已无库行可寻址的 zip（FR-128）。
    try:
        alive = get_doc(store, doc_id) is not None
    except Doc2mdError:
        alive = False
    if not alive:
        try:
            out.unlink()
        except OSError:
            pass
        return None
    return out


def drop_orphan_artifact(store: Store, root, cfg: dict, doc_id: str) -> None:
    """CH-484：E4041 回滚后清掉本轮刚落盘、已不可寻址的产物孤儿。

    只在 `doc_id` 确已不在 `docs` 时清——mark rowcount=0 也可能来自「任务行单独
    消失、文档仍在库」，那份 `out/{doc_id}.zip` 是**活文档**的正文载体，清它等于
    删数据（数据安全护栏）。best-effort：删除失败不翻转已判定的 rejected 终态，
    孤儿至多留在盘上、绝不影响库面正确性。
    """
    if not doc_id or store.q1("SELECT 1 FROM docs WHERE doc_id=? LIMIT 1",
                              (doc_id,)) is not None:
        return
    purge_files(doc_id, root,
                out_dir=config.path_setting(cfg, "paths.out", root, str(Path(root) / "out")),
                tmp_dir=config.path_setting(cfg, "paths.tmp", root, str(Path(root) / "tmp")))


def artifact_entry(names: list[str], base: str, doc_id: str = "") -> str | None:
    """按约定名精确取产物条目；不再用 `endswith` 兜底。

    当前布局是 `<doc_id>/content.md`，旧版/测试夹具是扁平 `content.md`——两种
    合法形态都精确匹配。`evil_content.md`、`x/y/content.md` 之类后缀相同的
    条目不会被误当正文/IR（FR-127 双闸的读取侧口径）。
    """
    if doc_id:
        expected = f"{doc_id}/{base}"
        if expected in names:
            return expected
    return base if base in names else None


def read_ir(zip_path: str):
    """从产物 zip 回读 IR（子进程不回传对象；父进程以产物为准重建入库内容）。

    读不到即返回 None——由调用方决定终态（CH-469：不得据此标 done）。
    `BadZipFile` 不是 `OSError` 子类、`IR.from_dict` 的字段类型问题抛 `TypeError`，
    两者都要收进来，否则一个坏产物会让整个消费批次崩掉。
    """
    import zipfile

    from .ir import IR
    try:
        with zipfile.ZipFile(zip_path) as z:
            name = artifact_entry(z.namelist(), "ir.json", Path(zip_path).stem)
            if not name:
                return None
            return IR.from_dict(json.loads(z.read(name).decode("utf-8")))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None
