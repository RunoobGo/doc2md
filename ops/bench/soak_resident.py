#!/usr/bin/env python3
"""ops/bench/soak_resident.py —— 常驻 worker 长跑 / 渗漏夹具（B-12）。

**非 gate、非 pytest**：放在 `ops/bench/` 且文件名不以 `test_` 开头，pytest
（testpaths=tests、python_files=test_*.py）不会收集。真实 WAL 库 + 真实 CLI
（`doc2md work` 常驻）跑，不 import 被测转换代码当桩。

用途：代替「>30 分钟手工盯屏」——按固定间隔把 `tests/golden/` 小样本重新入队，
采样常驻进程 RSS / FD 与日志轮转，收尾断言队列排空、无失败、无渗漏迹象。

用法：
    python3 ops/bench/soak_resident.py --minutes 0     # 单轮冒烟（约 2s）
    python3 ops/bench/soak_resident.py --minutes 30    # B-12 验收口径
    python3 ops/bench/soak_resident.py --minutes 2 --root /tmp/soak --keep

**平台**：夹具为 POSIX-only（macOS/Linux）——依赖 `.venv/bin/doc2md`、
`ps -o rss=`、`/proc/<pid>/fd`、`lsof`；Windows 需适配副本
（`.venv/Scripts/doc2md.exe`，且无 `ps`/`/proc`/`lsof`）。

退出码：0 = 全部判据通过；2 = 队列未排空 / 有失败作业 / RSS·FD 超界 /
常驻进程在收尾前已退出 / 排空采样异常 / 建库预初始化失败（此时 SUMMARY 为
`{"error": "init_failed", ...}`）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC2MD = str(REPO / ".venv/bin/doc2md")
GOLDEN = REPO / "tests/golden"
CYCLE = ["small_3p.pdf", "codeindent_1p.pdf", "numlist_1p.pdf", "links_pdf.pdf"]


def _sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def _conn(root: Path):
    c = sqlite3.connect(str(root / "state.sqlite"), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _counts(root: Path) -> dict:
    c = _conn(root)
    try:
        st = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        docs = c.execute("SELECT COUNT(*) n FROM docs").fetchone()["n"]
        return {"status": st, "docs": docs}
    finally:
        c.close()


def _rss_kb(pid: int) -> int | None:
    r = _sh("ps", "-o", "rss=", "-p", str(pid))
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _fd_count(pid: int) -> int | None:
    proc = Path(f"/proc/{pid}/fd")
    if proc.is_dir():
        try:
            return len(os.listdir(proc))
        except OSError:
            return None
    r = _sh("lsof", "-p", str(pid), "-Fn")
    if r.returncode != 0:
        return None
    return sum(1 for ln in r.stdout.splitlines() if ln.startswith("f"))


def _enqueue(root: Path, src: Path, attempts: int = 100) -> int:
    # 常驻 worker 刚拉起的瞬间可能仍在建库 / 建表（WAL 初始化），此时入队会
    # 撞上 OperationalError（E5000）。短重试吸收这段启动竞态；真失败仍非零。
    rc = 1
    for _ in range(attempts):
        rc = _sh(DOC2MD, "work", "--root", str(root),
                 "--enqueue", str(src)).returncode
        if rc == 0:
            return 0
        time.sleep(0.1)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="常驻 worker 长跑夹具（手动，非 CI gate）")
    ap.add_argument("--minutes", type=float, default=2.0,
                    help="运行时长；0 = 仅一轮冒烟")
    ap.add_argument("--interval", type=float, default=20.0, help="入队间隔秒")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--root", default="",
                    help="工作根（默认 tempfile；用户传入则不代删）")
    ap.add_argument("--keep", action="store_true", help="留存工作根供取证")
    args = ap.parse_args(argv)

    # 只代删自己用 mkdtemp 造的根；--root 是用户目录，绝不整目录删除（Ruling I）。
    created = not args.root
    root = Path(tempfile.mkdtemp(prefix="soak_")) if created else Path(args.root)
    for d in ("src", "out", "logs", "tmp"):
        (root / d).mkdir(parents=True, exist_ok=True)
    srcs = []
    for i, name in enumerate(CYCLE):
        dst = root / "src" / f"{i}_{name}"
        shutil.copy(GOLDEN / name, dst)
        srcs.append(dst)

    # 先串行建库：避开解发 E5000 的并发初始化竞态（B-17），常驻进程随后只打开已存在的库。
    init = _sh(DOC2MD, "work", "--root", str(root), "--status")
    if init.returncode != 0:
        print("SUMMARY " + json.dumps(
            {"error": "init_failed", "rc": init.returncode,
             "stderr_tail": (init.stderr or "")[-200:]}, ensure_ascii=False))
        if created and not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        elif args.keep:
            print(f"[keep] 工作根留存于 {root}", file=sys.stderr)
        else:
            print(f"[root] 用户指定的工作根未删除，留存于 {root}", file=sys.stderr)
        return 2

    proc = subprocess.Popen(
        [DOC2MD, "work", "--root", str(root), "--workers", str(args.workers),
         "--idle", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + max(0.0, args.minutes) * 60
    samples: list[dict] = []
    cycles = 0
    err = ""
    final = {"status": {}, "docs": 0}
    logs: list[Path] = []
    drain_error = ""
    try:
        while True:
            rc = _enqueue(root, srcs[cycles % len(srcs)])
            cycles += 1
            time.sleep(min(max(args.interval, 0.1), 2.0))   # 给排队作业起步
            c = _counts(root)
            samples.append({
                "t": round(time.monotonic(), 1), "cycle": cycles,
                "rss_kb": _rss_kb(proc.pid), "fds": _fd_count(proc.pid),
                "docs": c["docs"], "status": c["status"], "enqueue_rc": rc,
            })
            if args.minutes <= 0:
                break
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(max(args.interval, 0.1), left))
    finally:
        # 等队列排空（成功作业 → 终态），再停常驻进程。采样失败不得阻断停机
        # 阶梯（否则守护进程泄漏且 traceback 以 1 退出），只记入 drain_error。
        try:
            drain_deadline = time.monotonic() + 120
            while time.monotonic() < drain_deadline:
                final = _counts(root)
                if final["status"].get("queued", 0) == 0 and \
                        final["status"].get("running", 0) == 0:
                    break
                time.sleep(0.5)
        except Exception as exc:
            drain_error = f"{type(exc).__name__}: {exc}"
        worker_alive = proc.poll() is None
        proc.terminate()
        try:
            _out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
        logs = sorted((root / "logs").glob("work*.jsonl"))

    first, last = samples[0], samples[-1]
    rss_ok = (first["rss_kb"] is None or last["rss_kb"] is None
              or last["rss_kb"] <= first["rss_kb"] * 1.5 + 65536)
    fd_ok = (first["fds"] is None or last["fds"] is None
             or last["fds"] <= first["fds"] + 8)
    drained = (final["status"].get("queued", 0) == 0
               and final["status"].get("running", 0) == 0)
    failed = final["status"].get("failed", 0)
    done = final["status"].get("done", 0)
    summary = {
        "minutes": args.minutes, "cycles": cycles, "workers": args.workers,
        "drained": drained, "done": done, "failed": failed,
        "docs": final["docs"], "final_status": final["status"],
        "rss_kb_first": first["rss_kb"], "rss_kb_last": last["rss_kb"],
        "rss_within_bound": rss_ok,
        "fds_first": first["fds"], "fds_last": last["fds"],
        "fds_within_bound": fd_ok,
        "work_logs": [p.name for p in logs],
        "worker_alive": worker_alive,
        "drain_error": drain_error,
        "enqueue_rc_nonzero": sum(1 for s in samples if s["enqueue_rc"] != 0),
        "worker_stderr_tail": (err or "")[-200:],
    }
    # 契约：SUMMARY 必须是 stdout 最后一行——提示信息一律走 stderr。
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    ok = (drained and failed == 0 and done >= cycles and rss_ok and fd_ok
          and worker_alive and not drain_error)
    if created and not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    elif args.keep:
        print(f"[keep] 工作根留存于 {root}", file=sys.stderr)
    else:
        # 用户传入 --root 且未 --keep：仍保留（不代删），仅告知位置。
        print(f"[root] 用户指定的工作根未删除，留存于 {root}", file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
