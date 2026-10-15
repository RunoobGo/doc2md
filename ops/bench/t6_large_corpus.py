#!/usr/bin/env python3
"""ops/bench/t6_large_corpus.py —— 大语料吞吐 / 驻留 / 失败率实测（T6，方案 9.2 / 12.3）。

出口条件：>1000 文件混合语料上给出「队列积压、内存、失败率」三张表。
口径对齐方案 12.9：固定语料（种子确定）、并发与串行各跑一轮、产物留待复核、
测量进程树 RSS（规避 RUSAGE_CHILDREN 只报单子进程峰值的陷阱）。

跨平台口径（T3 的测量工具，方案 9.2 / 12.7）：本脚本即「跨机吞吐基线」的
同口径测量入口——同一份语料配比与同一张表结构，在 Windows / macOS 上直接
运行即可产出可入第十二章的数字。平台差异只落在驻留采样的后端上：
  - Linux：读 /proc（进程树 + VmRSS），零依赖；
  - macOS：`ps -axo pid=,ppid=,rss=`（一次调用拿全系统三列，Python 侧建树），零依赖；
  - Windows：无轻量标准库方案，装了 psutil 用 psutil（Process.children 递归
    + memory_info），没装则驻留列输出 n/a——吞吐与失败账目不依赖采样能力。
后端可用环境变量 T6_SAMPLER=proc|ps|psutil|none 强制覆盖（排障与测试用）。
语料的「种子确定」指内容确定；字节级可复现在同平台同依赖版本下成立，
跨机比对的是表内计数与耗时，不要求语料字节跨平台一致。

用法：
    python3 ops/bench/t6_large_corpus.py [--files 1080] [--workers 1,4] [--keep] [--out F]

结构：主进程只做编排与采样；转换一律走真实 CLI（doc2md work --enqueue DIR --once），
不 import 管线代码，避免测量进程自身成为被测对象。语料与 work/ 全在临时目录，
--keep 可留存取证。基准脚本置于 if __name__ == '__main__' 之下（spawn 安全，12.9 第 3 条）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

WORDS_ZH = ("文档 转换 管线 离线 分片 缝合 门禁 备份 加密 队列 渲染 指纹 消毒 "
            "驻留 吞吐 失败率 积压 探针 白名单 注入 归因 快照 轮出 演练").split()
WORDS_EN = ("doc2md pipeline offline shard stitch gate backup encrypt queue render "
            "fingerprint sanitize resident throughput backlog probe whitelist").split()


def _save_docx_det(doc, path: Path) -> None:
    """python-docx 保存后重写 zip 条目时间戳（docx 内容不变，只改 zip 元数据）。

    python-docx 的 save 走 zipfile.writestr，条目 date_time 取当前时间——
    同种子两次生成的 docx 字节不同，「固定语料」口径（12.9）不成立。
    固定为 1980-01-01（zip  epoch）。条目顺序与压缩级别沿用原包。
    """
    buf = io.BytesIO()
    doc.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, src.read(info.filename))


# --- 语料生成（种子确定 → 可复现） ---------------------------------------------
def generate_corpus(root: Path, *, n_pdf: int = 1000, n_docx: int = 60,
                    n_bad: int = 20, seed: int = 20261014) -> dict:
    """混合语料：n_pdf 个单页 PDF + n_docx 个 5 节 DOCX + n_bad 个坏文件。

    坏文件三分：截断 PDF（魔术码合法但结构损坏，打开失败 → E4151，CH-457）、
    错魔术码（类型拒 E4151）、空文件（E4151）。后两种在入队面即拒；
    截断 PDF 通过 sniff 后才在 S1 打开时失败（消费侧 failed）。
    每个坏文件字节唯一：同字节文件会按 doc_id 内容寻址被 uq_tasks_doc_active
    附着去重（约定 14），那样失败率表的「文件数 ↔ 终态」就不是 1:1 了。

    字节级可复现（12.9「固定语料」）：reportlab 的 Canvas 默认嵌 CreationDate
    时间戳、python-docx 的 zip 条目带当前时间——两者都会使同种子两次生成
    产出不同字节。PDF 走 `invariant=1` 固定时间，DOCX 保存后重写 zip 条目
    时间戳为固定值（docx 本体不变，只改 zip 元数据）。
    """
    rng = random.Random(seed)
    pdf_dir, docx_dir, bad_dir = root / "pdf", root / "docx", root / "bad"
    for d in (pdf_dir, docx_dir, bad_dir):
        d.mkdir(parents=True, exist_ok=True)

    from reportlab.pdfgen import canvas as rl_canvas
    for i in range(n_pdf):
        p = pdf_dir / f"small-{i:05d}.pdf"
        c = rl_canvas.Canvas(str(p), pagesize=(595, 842), invariant=1)
        y = 800
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, f"Bench Document {i:05d}")
        c.setFont("Helvetica", 10.5)
        y -= 24
        body = " ".join(rng.choice(WORDS_ZH) for _ in range(28)) + "。"
        body += " ".join(rng.choice(WORDS_EN) for _ in range(18)) + "."
        # 手动换行（CJK 宽度按字符估）
        line, width = "", 0
        for ch in body:
            width += 10.5 if ord(ch) > 127 else 5.2
            if width > 450:
                c.drawString(72, y, line)
                y -= 16
                line, width = "", 0
            line += ch
        if line:
            c.drawString(72, y, line)
        c.showPage()
        c.save()

    from docx import Document
    for i in range(n_docx):
        p = docx_dir / f"report-{i:04d}.docx"
        doc = Document()
        doc.add_heading(f"Bench Report {i:04d}", 0)
        for s in range(5):
            doc.add_heading(f"{s + 1}. 章节标题", level=1)
            para = " ".join(rng.choice(WORDS_ZH) for _ in range(40)) + "。"
            doc.add_paragraph(para)
            doc.add_paragraph(" ".join(rng.choice(WORDS_EN) for _ in range(20)) + ".")
        _save_docx_det(doc, p)

    good_pdf = (pdf_dir / "small-00000.pdf").read_bytes()
    for i in range(n_bad):
        kind = i % 3
        if kind == 0:                                   # 截断 PDF → S1 打开失败
            # 变长切片：同字节会被 uq_tasks_doc_active 附着去重，测不出独立失败
            (bad_dir / f"truncated-{i:03d}.pdf").write_bytes(good_pdf[:180 + i])
        elif kind == 1:                                 # 错魔术码 → 类型拒
            (bad_dir / f"notpdf-{i:03d}.pdf").write_text(
                f"这不是 PDF，只是文本。{i}\n" * 8, encoding="utf-8")
        else:                                           # 空文件（sniff 即拒，无去重问题）
            (bad_dir / f"empty-{i:03d}.pdf").write_bytes(b"")
    return {"pdf": n_pdf, "docx": n_docx, "bad": n_bad,
            "total": n_pdf + n_docx + n_bad, "seed": seed}


# --- 采样器 --------------------------------------------------------------------
def _probe_backend() -> str:
    """按宿主探测后端（不含环境变量覆盖）——拆出来便于测试伪造，
    避免断言依赖真实 /proc 布局（CH-469）。"""
    if os.path.isdir("/proc"):
        return "proc"
    if os.name == "posix":
        return "ps"
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "none"
    return "psutil"


def _tree_backend() -> str:
    """进程树 / RSS 采样后端：proc（Linux /proc）/ ps（POSIX ps）/ psutil / none。

    环境变量 T6_SAMPLER 可强制覆盖（排障与测试）。判定顺序即优先级：
    /proc 目录存在 → proc；POSIX 非 /proc（macOS）→ ps；Windows 且有 psutil
    → psutil；其余 → none（驻留列记 n/a，吞吐与失败账目不受影响）。
    """
    override = os.environ.get("T6_SAMPLER", "").strip().lower()
    if override in ("proc", "ps", "psutil", "none"):
        return override
    return _probe_backend()


def _tree_proc(root_pid: int, proc_root: str = "/proc") -> tuple[list[int], dict[int, int]]:
    """proc 树上 root_pid 的完整后代进程与各进程 VmRSS（KB）。

    `proc_root` 可注入：/proc 的布局是 Linux 专有的，写死路径等于把该分支变成
    「只能在 Linux 上测」——CH-469 用假 proc 根做跨平台断言（后代遍历、VmRSS
    解析、读不到的进程跳过），真实 /proc 由 `sample_tree` 走默认值。
    """
    ppid_of = {}
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            with open(f"{proc_root}/{entry}/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("PPid:"):
                        ppid_of[int(entry)] = int(line.split()[1])
                        break
        except OSError:
            continue
    children: dict[int, list[int]] = {}
    for pid, ppid in ppid_of.items():
        children.setdefault(ppid, []).append(pid)
    out, stack = [], [root_pid]
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(children.get(p, []))
    rss = {}
    for pid in out:
        try:
            with open(f"{proc_root}/{pid}/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss[pid] = int(line.split()[1])
                        break
        except OSError:
            continue
    return out, rss


def _tree_ps(root_pid: int) -> tuple[list[int], dict[int, int]]:
    """macOS 路径：`ps -axo pid=,ppid=,rss=` 一次拿全系统三列（rss 单位 KB）。

    Linux 的 GNU procps 接受同样的输出规格，因此该分支可在 Linux 上实测验证。
    """
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="],
                         capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise OSError(f"ps failed: {out.stderr.strip()[:80]}")
    ppid_of: dict[int, int] = {}
    rss: dict[int, int] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, ppid, rss_kb_v = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        ppid_of[pid] = ppid
        rss[pid] = rss_kb_v
    children: dict[int, list[int]] = {}
    for pid, ppid in ppid_of.items():
        children.setdefault(ppid, []).append(pid)
    pids, stack = [], [root_pid]
    while stack:
        p = stack.pop()
        pids.append(p)
        stack.extend(children.get(p, []))
    return pids, {p: rss[p] for p in pids if p in rss}


def _tree_psutil(root_pid: int) -> tuple[list[int], dict[int, int]]:
    """Windows 路径：psutil 递归子进程 + memory_info().rss（换算 KB）。"""
    import psutil
    root = psutil.Process(root_pid)
    procs = [root] + root.children(recursive=True)
    return ([p.pid for p in procs],
            {p.pid: p.memory_info().rss // 1024 for p in procs})


def sample_tree(root_pid: int) -> tuple[list[int], dict[int, int], str]:
    """按当前后端采样进程树与 RSS；返回 (pids, rss_kb_by_pid, backend)。"""
    backend = _tree_backend()
    fn = {"proc": _tree_proc, "ps": _tree_ps, "psutil": _tree_psutil}.get(backend)
    if fn is None:
        return [root_pid], {}, "none"
    try:
        pids, rss = fn(root_pid)
        return pids, rss, backend
    except Exception:  # noqa: BLE001 — 采样失败不得打断被测 CLI，退化为单进程
        return [root_pid], {}, "none"


class Sampler(threading.Thread):
    """双采样：进程树 RSS（0.1s）+ 队列状态计数（0.5s）。

    rss_available 记录驻留采样是否真实拿到过数据：后端 none 或全程异常时
    为 False，表格相应列输出 n/a 而不是 0（0 会被读成「不驻留」的假数据）。
    """

    def __init__(self, root_pid: int, db_path: Path):
        super().__init__(daemon=True)
        self.root_pid, self.db_path = root_pid, db_path
        self.stop = threading.Event()
        self.backend = _tree_backend()
        self.peak_tree_mb = 0.0
        self.peak_single_mb = 0.0
        self.rss_samples = 0
        self.rss_series: list[tuple[float, float]] = []     # (t_s, tree_mb)
        self.queue_series: list[dict] = []                  # (t_s, counts)

    def run(self):
        t0 = time.monotonic()
        while not self.stop.is_set():
            now = time.monotonic() - t0
            pids, rss, _ = sample_tree(self.root_pid)
            if rss:
                self.rss_samples += 1
                tree_mb = sum(rss.values()) / 1024.0
                single_mb = max(rss.values()) / 1024.0
                self.peak_tree_mb = max(self.peak_tree_mb, tree_mb)
                self.peak_single_mb = max(self.peak_single_mb, single_mb)
                self.rss_series.append((round(now, 1), round(tree_mb, 1)))
            counts = self._queue_counts()
            if counts is not None:
                self.queue_series.append({"t": round(now, 1), **counts})
            self.stop.wait(0.1)

    def _queue_counts(self) -> dict | None:
        if not self.db_path.exists():
            return None
        try:
            con = sqlite3.connect(_ro_uri(self.db_path), timeout=1.0, uri=True)
            try:
                rows = con.execute("SELECT status, COUNT(*) FROM tasks"
                                   " GROUP BY status").fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        return {st: n for st, n in rows}


def _ro_uri(db: Path) -> str:
    """跨平台 SQLite 只读 URI：as_uri() 产出 file:///C:/... 形态；
    盘符路径带反斜杠直接拼进 URI 在 Windows 上不合法，故统一走 as_uri()。"""
    return db.resolve().as_uri() + "?mode=ro"


# --- 单轮排空测量 ----------------------------------------------------------------
def run_drain(corpus: Path, root: Path, workers: int) -> dict:
    """真 CLI：doc2md work --enqueue <corpus> --once --workers N。父进程只采样。"""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "state.sqlite"
    cmd = [sys.executable, "-m", "doc2md.cli", "work", "--enqueue", str(corpus),
           "--once", "--workers", str(workers), "--root", str(root)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    sampler = Sampler(proc.pid, db)
    sampler.start()
    out, err = proc.communicate(timeout=3600)
    sampler.stop.set()
    sampler.join(timeout=2.0)
    wall = time.monotonic() - t0

    # 入队即拒的坏文件（CLI stderr 的机器行）
    enqueue_rejected: dict[str, int] = {}
    for line in (err or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("status") == "error":
            enqueue_rejected[obj.get("code", "?")] = enqueue_rejected.get(
                obj.get("code", "?"), 0) + 1

    # 终态分布（DB 为准）
    con = sqlite3.connect(_ro_uri(db), uri=True)
    try:
        final = {st: n for st, n in con.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()}
        codes = {f"{c or '-'}": n for c, n in con.execute(
            "SELECT error_code, COUNT(*) FROM tasks WHERE status IN ('rejected','failed')"
            " GROUP BY error_code").fetchall()}
    finally:
        con.close()

    series = sampler.queue_series
    backlog_peak = max((s.get("queued", 0) + s.get("running", 0) for s in series),
                       default=0)
    half = next((s["t"] for s in series
                 if s.get("queued", 0) + s.get("running", 0) <= 0.5 * backlog_peak
                 and s.get("done", 0) > 0), None)
    return {"workers": workers, "exit": proc.returncode, "wall_s": round(wall, 1),
            "final": final, "rejected_codes": codes,
            "enqueue_rejected": enqueue_rejected,
            "sampler": sampler.backend,
            "rss_available": sampler.rss_samples > 0,
            "peak_tree_mb": round(sampler.peak_tree_mb, 1) if sampler.rss_samples else None,
            "peak_single_mb": round(sampler.peak_single_mb, 1) if sampler.rss_samples else None,
            "backlog_peak": backlog_peak, "half_drain_s": half,
            "queue_series": series, "rss_series": sampler.rss_series[::10]}


# --- 三张表 ---------------------------------------------------------------------
def _fmt_mb(v) -> str:
    """驻留数字格式化：None（平台无采样能力）→ n/a，不得输出 0（会被读成不驻留）。"""
    return "n/a（平台无进程树采样能力，见脚本 docstring）" if v is None else str(v)


def tables(results: list[dict], corpus_info: dict) -> str:
    lines: list[str] = []
    total = corpus_info["total"]

    lines.append("### T6-1 队列积压（doc2md work --enqueue DIR --once）\n")
    lines.append("| 指标 | " + " | ".join(f"workers={r['workers']}" for r in results) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in results) + " |")
    rows = [
        ("语料总量（件）", [str(total)] * len(results)),
        ("排空耗时（s）", [str(r["wall_s"]) for r in results]),
        ("吞吐（件/分钟）", [f"{total / max(r['wall_s'], 0.1) * 60:.0f}" for r in results]),
        ("峰值积压 queued+running", [str(r["backlog_peak"]) for r in results]),
        ("半排空时点（s）", [str(r["half_drain_s"]) for r in results]),
        ("入队即拒（坏文件）", [str(sum(r["enqueue_rejected"].values())) for r in results]),
    ]
    for name, vals in rows:
        lines.append(f"| {name} | " + " | ".join(vals) + " |")

    lines.append("\n### T6-2 驻留（进程树 RSS 峰值，12.9 第 3 条口径）\n")
    lines.append("| 指标 | " + " | ".join(f"workers={r['workers']}" for r in results) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in results) + " |")
    lines.append("| 采样后端 | " + " | ".join(r["sampler"] for r in results) + " |")
    lines.append("| 进程树峰值 RSS（MB） | " + " | ".join(
        _fmt_mb(r["peak_tree_mb"]) for r in results) + " |")
    lines.append("| 最大单进程 RSS（MB） | " + " | ".join(
        _fmt_mb(r["peak_single_mb"]) for r in results) + " |")

    lines.append("\n### T6-3 失败率（终态分布）\n")
    lines.append("| 终态 | " + " | ".join(f"workers={r['workers']}" for r in results) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in results) + " |")
    keys = ["done", "rejected", "failed", "queued", "running"]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(r["final"].get(k, 0)) for r in results) + " |")
    lines.append("| 入队即拒 | " + " | ".join(
        str(sum(r["enqueue_rejected"].values())) for r in results) + " |")
    all_codes = sorted({c for r in results for c in r["rejected_codes"]} |
                       {c for r in results for c in r["enqueue_rejected"]})
    for c in all_codes:
        vals = []
        for r in results:
            n = r["rejected_codes"].get(c, 0) + r["enqueue_rejected"].get(c, 0)
            vals.append(str(n))
        lines.append(f"| ├─ {c} | " + " | ".join(vals) + " |")
    done = [r["final"].get("done", 0) for r in results]
    rate = [f"{100.0 * (total - d) / max(total, 1):.2f}%" for d in done]
    lines.append("| 非 done 占比 | " + " | ".join(rate) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="T6 大语料实测（>1000 文件混合类型）")
    ap.add_argument("--files", type=int, default=1080, help="语料总量（近似，按配比生成）")
    ap.add_argument("--workers", default="1,4", help="逗号分隔的并发度列表")
    ap.add_argument("--keep", action="store_true", help="保留语料与 work/ 目录")
    ap.add_argument("--out", help="结果 JSON 输出路径（缺省只打印）")
    args = ap.parse_args()

    # 配比：PDF : DOCX : 坏文件 ≈ 50 : 3 : 1（向下取整，坏文件至少 3 个）
    n_bad = max(3, args.files // 54)
    n_docx = max(1, args.files // 18)
    n_pdf = args.files - n_bad - n_docx

    tmp = Path(tempfile.mkdtemp(prefix="t6-bench-"))
    corpus = tmp / "corpus"
    print(f"[t6] 平台={sys.platform} 采样后端={_tree_backend()}"
          f"（T6_SAMPLER=proc|ps|psutil|none 可覆盖）")
    print(f"[t6] 语料目录：{corpus}")
    t0 = time.monotonic()
    info = generate_corpus(corpus, n_pdf=n_pdf, n_docx=n_docx, n_bad=n_bad)
    print(f"[t6] 语料生成：{info}（{time.monotonic() - t0:.1f}s）")

    results = []
    for w in (int(x) for x in args.workers.split(",")):
        root = tmp / f"work-w{w}"
        print(f"[t6] 排空测量 workers={w} …")
        results.append(run_drain(corpus, root, w))
        r = results[-1]
        print(f"[t6]   exit={r['exit']} wall={r['wall_s']}s "
              f"peak_tree={_fmt_mb(r['peak_tree_mb'])}MB final={r['final']}")

    print()
    print(tables(results, info))
    print(f"\n[t6] 语料总量 {info['total']}（pdf={info['pdf']} docx={info['docx']} "
          f"bad={info['bad']} seed={info['seed']}）")
    print(f"[t6] 临时目录：{tmp}{'（--keep 保留）' if args.keep else ''}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"platform": sys.platform, "sampler": _tree_backend(),
             "corpus": info, "results": [{k: v for k, v in r.items()
                                          if not k.endswith('_series')} for r in results],
             "queue_series": {str(r["workers"]): r["queue_series"] for r in results}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[t6] JSON 已写：{args.out}")
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
