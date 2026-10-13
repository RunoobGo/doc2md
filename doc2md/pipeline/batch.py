"""batch —— 多文件吞吐层（CH-448；对标外部方案「Python 层受 GIL 限制」的结论）。

范围纪律：本层只解决「较多文件同时转化」的 CPU 利用，不引入任何模型/OCR；
产物口径与串行完全一致（等价性由 tests/test_v24_gates.py 的批量=串行断言把住）。

平台纪律（低配 Win11 / Mac mini m4 16GB）：
- 用 `ProcessPoolExecutor`（不是线程）：pdfplumber 解析是纯 Python CPU 工作，
  线程受 GIL 串行化，实测 4 进程 30 个小文件 3.8s → 2.1s，线程池无增益。
- 不 `mp.set_start_method`、不依赖 fork 语义：worker 函数在本模块顶层，
  参数与返回值都是可 pickle 的基础类型 → Windows（spawn）与 macOS（3.8+ 默认 spawn）
  同样可用，实测 fork/spawn 两种启动方式结果一致。
- 并发数与 `[s1].chunk_pages` 互相决定驻留峰值：workers × chunk × 单页驻留。
  默认 `workers=0` → min(4, cpu_count())；8GB 内存机器不要把它调到 8。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .. import config
from ..errors import Doc2mdError


def default_workers(cfg: dict | None = None) -> int:
    """[perf].workers：0/缺省 = 自动 min(4, cpu_count())；负数按 1（串行）处理。"""
    n = 0
    if cfg is not None:
        try:
            n = int(config.get(cfg, "perf.workers", 0) or 0)
        except (TypeError, ValueError):
            n = 0
    if n < 0:
        return 1
    if n == 0:
        n = min(4, os.cpu_count() or 1)
    return max(1, n)


@dataclass
class Summary:
    """跨进程回传的紧凑结果（不回传 IR 对象：pickle 大对象是内存与延迟的额外成本）。"""

    file: str
    status: str                      # ok | rejected | error
    doc_id: str = ""
    source_key: str = ""
    blocks: int = 0
    chars: int = 0
    zip: str = ""
    code: str = ""
    message: str = ""
    gate: dict | None = None

    def to_dict(self) -> dict:
        d = {"file": self.file, "status": self.status, "doc_id": self.doc_id,
             "source_key": self.source_key, "blocks": self.blocks, "chars": self.chars,
             "zip": self.zip}
        if self.code:
            d["code"] = self.code
        if self.message:
            d["message"] = self.message
        if self.gate:
            d["gate"] = self.gate
        return d


def _convert_one(path: str, cfg: dict | None, mode: str | None,
                 out: str | None) -> dict:
    """worker 入口：必须在模块顶层、参数可 pickle（spawn 平台要求）。"""
    from .convert import convert
    try:
        res = convert(path, cfg=cfg, mode=mode, out_dir=out)
    except Doc2mdError as exc:              # 含 E4221 门禁拒收，跨进程按 code 分类
        detail = exc.detail or {}
        return Summary(file=str(path), status="rejected" if exc.code == "E4221" else "error",
                       code=exc.code, message=str(exc),
                       gate=_gate(detail) if exc.code == "E4221" else None).to_dict()
    except Exception as exc:                # noqa: BLE001 — 解析层未预期异常不得拖垮整批
        return Summary(file=str(path), status="error", code="E5000",
                       message=f"{type(exc).__name__}: {exc}").to_dict()
    return Summary(file=str(path), status="ok", doc_id=res.doc_id, source_key=res.source_key,
                   blocks=len(res.ir.blocks), chars=res.metrics.get("chars", 0),
                   zip=str(res.zip_path) if res.zip_path else "").to_dict()


def _gate(detail: dict) -> dict:
    return {"mode": detail.get("mode"), "sampled_pages": detail.get("sampled_pages"),
            "reasons": detail.get("reasons")}


def run_batch(paths: Sequence[str | Path], *, cfg: dict | None = None,
              mode: str | None = None, out_dir: str | Path | None = None,
              workers: int | None = None, start_method: str | None = None,
              on_result: Callable[[dict], None] | None = None) -> list[dict]:
    """有界进程池批量转换；顺序不保证，完成即回调。单文件或 workers=1 走串行同一路径。

    start_method="spawn" 用于在 Linux 上复现 Windows 的进程启动方式（验收用）；
    默认 None = 平台默认上下文。
    """
    files = [str(p) for p in paths]
    if not files:
        return []
    out = str(out_dir) if out_dir else None
    n = workers if workers is not None else default_workers(cfg)
    n = max(1, min(int(n), len(files)))
    results: list[dict] = []
    if n == 1 or len(files) == 1:
        for f in files:
            r = _convert_one(f, cfg, mode, out)
            results.append(r)
            if on_result:
                on_result(r)
        return results

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    ctx = multiprocessing.get_context(start_method) if start_method else None
    kw = {"max_workers": n}
    if ctx is not None:
        kw["mp_context"] = ctx
    with ProcessPoolExecutor(**kw) as pool:
        futs = {pool.submit(_convert_one, f, cfg, mode, out): f for f in files}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as exc:        # noqa: BLE001 — 进程崩溃（OOM killer 等）
                r = Summary(file=str(futs[fut]), status="error", code="E5000",
                            message=f"worker died: {type(exc).__name__}: {exc}").to_dict()
            results.append(r)
            if on_result:
                on_result(r)
    return results
