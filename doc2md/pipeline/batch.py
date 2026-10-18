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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

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
    status: str                      # ok | rejected | conflict | error
    doc_id: str = ""
    source_key: str = ""
    blocks: int = 0
    chars: int = 0
    zip: str = ""
    code: str = ""
    message: str = ""
    gate: dict | None = None
    edited_blocks: int = 0
    meta: dict = field(default_factory=dict)

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
        if self.edited_blocks:
            d["edited_blocks"] = self.edited_blocks
        if self.meta:
            d["meta"] = self.meta
        return d


def _convert_one(path: str, cfg: dict | None, mode: str | None,
                 out: str | None, force: bool = False,
                 precheck_edits: bool = False) -> dict:
    """worker 入口：必须在模块顶层、参数可 pickle（spawn 平台要求）。"""
    from .convert import convert
    try:
        res = convert(path, cfg=cfg, mode=mode, out_dir=out, force=force,
                      precheck_edits=precheck_edits)
    except Doc2mdError as exc:              # 含 E4221 门禁拒收，跨进程按 code 分类
        detail = exc.detail or {}
        # CH-490：预检命中的编辑冲突是「明确的业务终态」，不是内部错误；
        # 与 E4221 一样回传可判定 status，API 侧保持 conflict/202 语义。
        status = ("rejected" if exc.code == "E4221"
                  else "conflict" if exc.code == "E4091" else "error")
        return Summary(file=str(path), status=status,
                       doc_id=str(detail.get("doc_id", "")),
                       source_key=str(detail.get("source_key", "")),
                       code=exc.code, message=str(exc),
                       gate=_gate(detail) if exc.code == "E4221" else None,
                       edited_blocks=int(detail.get("edited_blocks", 0) or 0)
                       if exc.code == "E4091" else 0).to_dict()
    except Exception as exc:                # noqa: BLE001 — 解析层未预期异常不得拖垮整批
        # CH-485（R9-PIPE-9）：第三方解析异常的消息可能夹带页正文；任务明细会
        # 经 GET /jobs 暴露给 reader。只留类名，与 worker died 同口径。
        return Summary(file=str(path), status="error", code="E5000",
                       message=f"{type(exc).__name__}").to_dict()
    return Summary(file=str(path), status="ok", doc_id=res.doc_id, source_key=res.source_key,
                   blocks=len(res.ir.blocks), chars=res.metrics.get("chars", 0),
                   zip=str(res.zip_path) if res.zip_path else "",
                   gate=res.gate, meta=res.meta).to_dict()


def _gate(detail: dict) -> dict:
    return {"mode": detail.get("mode"), "sampled_pages": detail.get("sampled_pages"),
            "reasons": detail.get("reasons")}


def run_batch(paths: Sequence[str | Path], *, cfg: dict | None = None,
              mode: str | None = None, out_dir: str | Path | None = None,
              workers: int | None = None, start_method: str | None = None,
              on_result: Callable[[dict], None] | None = None,
              force: bool = False, precheck_edits: bool = False) -> list[dict]:
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
    # CH-469：单文件墙钟上限（完整口径见并行分支注释）。先算 window——串行分支共用。
    per_file_s = 0
    if cfg is not None:
        try:
            per_file_s = int(config.get(cfg, "perf.batch_file_timeout_s", 0) or 0)
        except (TypeError, ValueError):
            per_file_s = 0
    window = per_file_s if per_file_s > 0 else None
    # CH-473（R5-PIPE-7）：串行快路径仅在**未配空窗上限**时走进程内 for 循环。
    # 原实现无条件进程内直跑——损坏 PDF 让 pdfplumber 无限挂起时，CLI `--workers 1`、
    # 单文件投递、API 同步小批（默认阈值内）全部永等，与并行路径「空窗记账 + terminate」
    # 语义不一致（dev-plan 亦未声明此例外）。配了上限（>0）时落到下方单 worker
    # 进程池：同一 wait 空窗判定、同一 E5000 记账、同一 terminate 收尾；默认 0
    # （不设限）保持进程内零开销、零行为变化（G6 不受影响）。
    if (n == 1 or len(files) == 1) and window is None:
        for f in files:
            r = _convert_one(f, cfg, mode, out, force=force,
                             precheck_edits=precheck_edits)
            results.append(r)
            if on_result:
                on_result(r)
        return results

    import multiprocessing
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    ctx = multiprocessing.get_context(start_method) if start_method else None
    kw = {"max_workers": n}
    if ctx is not None:
        kw["mp_context"] = ctx
    # CH-469：单文件墙钟上限。损坏 PDF 能让 pdfplumber 无限挂起，`_convert_one`
    # 的 except 接不住 hang，整批便永等 → 默认 0 = 不设限（保持既有契约：
    # 千页级文档合法耗时可能很长）。判定口径是**空窗**：连续 per_file_s 秒内
    # 没有任何文件完成，就把仍在排队的件全部记超时。
    # （不能用 `fut.result(timeout=…)`：件是先完成才轮得到读结果，
    #   那条 timeout 在 as_completed 循环里永不触发，挂死时依旧卡在等下一件完成。）
    pool = ProcessPoolExecutor(**kw)
    # CH-469：先建好完整映射再进循环——`wait()` 每轮把 pending 收敛成「未完成集」，
    # 若只留收敛后的集合，异常分支就再也取不到该件对应的文件名（记账会丢件）。
    futs: dict = {}
    pending: set = set()
    try:
        # submit 也要在 try 内：BrokenProcessPool / 资源耗尽时否则会跳过
        # finally 的 shutdown+terminate，把 worker 留到解释器退出（X-17 回归面）。
        futs = {pool.submit(_convert_one, f, cfg, mode, out, force,
                            precheck_edits): f
                for f in files}
        pending = set(futs)
        while pending:
            done, pending_now = wait(pending, timeout=window, return_when=FIRST_COMPLETED)
            if not done and window is not None:
                # 空窗：仍在跑的件无法安全中止（强杀 worker 会连带砍掉别的在途件），
                # 但批次必须给出结论——逐个记超时并取消排队件。
                for fut in pending:
                    fut.cancel()
                    r = Summary(file=str(futs[fut]), status="error", code="E5000",
                                message=f"batch file timeout after {per_file_s}s").to_dict()
                    results.append(r)
                    if on_result:
                        on_result(r)
                pending = set()
                break
            pending = pending_now
            for fut in done:
                try:
                    r = fut.result()
                except Exception as exc:        # noqa: BLE001 — 进程崩溃（OOM killer 等）
                    # CH-469：只带类名，不带 str(exc)（pdfminer 异常消息常夹带页正文，NFR-20）
                    r = Summary(file=str(futs[fut]), status="error", code="E5000",
                                message=f"worker died: {type(exc).__name__}").to_dict()
                results.append(r)
                if on_result:
                    on_result(r)
    finally:
        # 卡住的件已按超时记账；未完成的排队件直接取消，
        # 活动 worker 交由 shutdown 收尾（wait=False 不让取消再变成阻塞点）。
        for fut in pending:
            fut.cancel()
        # 先抓本池 worker 句柄——shutdown 会把 `_processes` 置 None，之后再取就没了。
        workers = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        # CH-474（X-16 根治 / 登记 X-17）：**无条件**终止本池残留 worker 并回收，
        # 不再限于空窗超时（abandoned）分支。X-16 的原修复只在该分支 terminate，
        # 本次 full-suite 暴露出**正常完成路径同样挂死**：`_ExecutorManagerThread.
        # join_processes`（CPython 3.12）对不响应的 worker 只 join 不 terminate，
        # 而多线程父进程 fork 出的 worker 可能持有继承锁、永不响应退出哨兵
        # （CI 日志的 `This process (pid=…) is multi-threaded, use of fork() may
        # lead to deadlocks in the child` 即此形态）——manager 线程永不结束，
        # 解释器退出时 `_python_exit` join manager 死锁：run 38901173280 在
        # 「605 passed」全部打印完毕后卡退出 27 分钟直至作业超时被取消
        # （同代码的 4151515 干净退出——竞态，runner 负载越高窗口越大）。
        # 本批 future 此时已全部有结论（正常路径=已完成、异常路径=已记账），
        # terminate 空闲 worker 不影响 results，产物逐字节等价（G6）；异常路径
        # 下在途件被终止亦优于留到解释器退出死锁。仍按 pid 只动本 executor
        # 自己的 worker（非全局 active_children，绝不触碰别处的池）。
        for proc in workers:
            try:
                proc.terminate()
            except (OSError, ValueError, RuntimeError):
                pass
        # terminate 后必须 join 回收僵尸，否则 manager 的 join_processes 仍会
        # 等到它们——给单个 worker 5s 上限，防「连 SIGTERM 都不响应」的极端
        # 情形把这里本身变成新的阻塞点。
        for proc in workers:
            try:
                proc.join(timeout=5)
            except (OSError, ValueError, RuntimeError):
                pass
        # SIGKILL 兜底：**无条件**发出（对已死进程 `os.kill` 抛 ProcessLookupError、
        # 已在 `_send_signal` 内部吞掉）。不要用 `if proc.is_alive()` 做前置——
        # CPython 的 `Popen.poll()` 把 waitpid 的 ECHILD（已被回收）也返回 None
        # （process.py #1731717 怪癖），`is_alive()` 会误报 True/False；SIGKILL
        # 在内核层面不可阻挡，发出即保证 manager 的 join_processes 必然返回、
        # 解释器退出不死锁。本批 future 已全部有结论，强杀无数据面影响。
        for proc in workers:
            try:
                proc.kill()
            except (OSError, ValueError, RuntimeError):
                pass
        for proc in workers:
            try:
                proc.join(timeout=1)
            except (OSError, ValueError, RuntimeError):
                pass
    return results
