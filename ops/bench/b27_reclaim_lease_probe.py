#!/usr/bin/env python3
"""ops/bench/b27_reclaim_lease_probe.py —— CH-499 / B-27 租约语义的双进程实测台。

这是第十五轮 B-27 项「合法长作业被 `reclaim_stale` 误抢」用到并复跑取证的夹具，
固化进仓库以便随时复跑。**非 gate、非 pytest**：放在 `ops/bench/` 且文件名不含
`test_`，pytest 不会自动收集（`pytest.ini` testpaths=tests、python_files=test_*.py）。
转换一律走真实 CLI（`doc2md work`），不 import 被测代码当桩；库面判定用只读 sqlite3 连接。

背景（CH-498 实测取证）：`reclaim_stale` 原先只看 `started_at` 的年龄。把窗口压到
`stale_job_minutes=1` 后，A 进程正在转换的 3000 页作业在 age=68s 时被 B 进程判成遗骸——
B 重新认领并**完整重跑一遍**（≈70s 纯浪费），A 先落库使 B 的 `mark` 走
`rowcount=0 → E4041 → 回滚`，用户面留下一条与终态 `done` 矛盾的假 `rejected`。
CH-499 的修法是给 running 行加所有权 + 租约（`DB_VERSION` 10）：A 在途期间由心跳续租，
B 只回收「租约过期」或「超 `[perf].job_lease_cap_minutes` 硬上限」的行。

两个场景（`--scenario`，默认两个都跑）：

  live —— 合法长作业越过 stale 窗口、心跳仍在续 → **不得**被回收。
    判据：B 侧 `reclaimed_stale=0` 且 `rejected=0`；A 期间 `lease_expires_at` 严格前移
    （观察到 ≥2 个不同取值）；终态 `tasks=done`、`docs=1`、产物 zip 自洽。

  dead —— A 被 SIGKILL（低配机 OOM 的代理形态）后心跳停止 → **必须**仍被回收（CH-448
    语义不得回潮）。判据：B 侧 `reclaimed_stale>=1`，B 自己把这件跑完（终态 `done`、
    `docs=1`），且回收前观察到租约已过期（`lease_expires_at < now`）。

用法：
    python3 ops/bench/b27_reclaim_lease_probe.py
    python3 ops/bench/b27_reclaim_lease_probe.py --scenario live --pages 3000
    python3 ops/bench/b27_reclaim_lease_probe.py --root /tmp/my_b27 --keep

退出码：0=判据全过；2=判据不满足（含「无心跳仍被回收」失效）；
3=时间窗不足（单件耗时没越过窗口，加大 --pages 重跑——不得把「没撞上」写成「撞不上」）。

踩坑登记（CH-498，本夹具已内建规避）：`--root` **不**参与配置发现
（`doc2md/config/loader.py` 顺序 = DEFAULT → `$DOC2MD_HOME/doc2md.toml` →
`./doc2md.toml` → `--config`），所以压缩窗口必须把 `DOC2MD_HOME` 指到沙箱根，
并在起跑前回读 `perf.stale_job_minutes` 确认生效——第一轮实测就栽在这里。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC2MD = str(REPO / ".venv/bin/doc2md")


def _console_safe() -> None:
    """与 `doc2md/cli.py` / `soak_resident.py` 同口径：中文提示在非 UTF-8 控制台上
    降级为 replace，机器可读行另走 ASCII 转义（CH-498 / B-16 的 cp1252 崩栈教训）。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _sh(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([DOC2MD, *args, "--root", str(Root.path)],
                          capture_output=True, text=True, errors="replace",
                          timeout=3600, cwd=cwd)


def _bg(*args: str) -> subprocess.Popen:
    return subprocess.Popen([DOC2MD, *args, "--root", str(Root.path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")


class _Root:
    """沙箱根（模块级单例，供 `_sh` / `_bg` 拼 CLI 参数）。"""

    path = Path("/tmp/b27_probe")
    keep = False


Root = _Root()


def _env() -> dict:
    env = dict(os.environ)
    env["DOC2MD_HOME"] = str(Root.path)     # 配置发现走这里，`--root` 不参与
    return env


def q(sql: str, params=()) -> list:
    con = sqlite3.connect(str(Root.path / "state.sqlite"), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        return [tuple(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def build_pdf(pages: int) -> Path:
    """多页中文正文 PDF——把单件转换推到分钟级，才测得出 stale 窗口这一维。"""
    path = Root.path / "src" / f"big_{pages}p.pdf"
    if path.exists():
        print(f"[setup] reuse {path.name} ({path.stat().st_size} B)", flush=True)
        return path
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    for i in range(1, pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, h - 60, f"第 {i // 20 + 1} 章 小节 {i}")
        c.setFont("Helvetica", 10.5)
        y = h - 100
        for k in range(12):
            c.drawString(50, y, f"第 {i} 页正文第 {k + 1} 段：离线转换压力样本，编号列表项 "
                                f"{k + 1}）后接跨页延续语句，用于把单作业耗时推到分钟级。")
            y -= 22
        c.showPage()
    c.save()
    print(f"[setup] built {pages}p: {path.stat().st_size} B", flush=True)
    return path


def setup_root(stale_minutes: int, lease_cap: int) -> None:
    shutil.rmtree(Root.path, ignore_errors=True)
    for d in ("src", "out", "logs", "tmp"):
        (Root.path / d).mkdir(parents=True, exist_ok=True)
    (Root.path / "doc2md.toml").write_text(
        "[perf]\n"
        "workers = 1\n"
        f"stale_job_minutes = {stale_minutes}    # 同时是租约时长（CH-499）\n"
        "stale_check_seconds = 5\n"
        "queue_batch_factor = 1       # 每轮只领 1 件，避免把排队误读成被抢\n"
        "batch_file_timeout_s = 0     # 不设单件墙钟，隔离出 reclaim 这一个变量\n"
        f"job_lease_cap_minutes = {lease_cap}\n"
        "[paths]\n"
        f'root = "{Root.path}"\n',
        encoding="utf-8")
    os.environ["DOC2MD_HOME"] = str(Root.path)


def readback_cfg(stale_minutes: int, lease_cap: int) -> bool:
    """回读生效值：窗口没真的压下来就别把「没撞上」写成结论（CH-498 踩坑）。"""
    out = _sh("doctor")
    from doc2md import config
    cfg = config.load()
    got_min = int(config.get(cfg, "perf.stale_job_minutes", 30) or 0)
    got_cap = int(config.get(cfg, "perf.job_lease_cap_minutes", 240) or 0)
    print(f"[cfg ] stale_job_minutes={got_min} job_lease_cap_minutes={got_cap} "
          f"doctor_rc={out.returncode}", flush=True)
    return got_min == stale_minutes and got_cap == lease_cap


def _age_s(started: str | None) -> float:
    if not started:
        return 0.0
    ts = datetime.fromisoformat(started).replace(tzinfo=None)
    return (datetime.now(timezone.utc).replace(tzinfo=None) - ts).total_seconds()


def _lease_past(lease: str | None) -> bool:
    if not lease:
        return False
    ts = datetime.fromisoformat(lease).replace(tzinfo=None)
    return ts < datetime.now(timezone.utc).replace(tzinfo=None)


def _row() -> tuple:
    rows = q("SELECT job_id, doc_id, status, started_at, lease_expires_at, owner"
             " FROM tasks")
    return rows[0] if rows else ("", "", "gone", None, "", "")


def _notes(proc_out: str) -> list[str]:
    return [ln for ln in (proc_out or "").splitlines() if "reclaim" in ln.lower()]


def _rep(proc_out: str) -> dict:
    """取 `work` 的汇总机读行（含 drained/notes）；解析不到返回空 dict。

    判据必须读**结构化计数**，不能对 stdout 做 `"rejected" in out` 似真判断——
    `WorkReport.to_dict()` 恒带 `"rejected": 0` 字段，字符串匹配会把「零拒绝」
    误读成「有拒绝」。
    """
    for ln in reversed((proc_out or "").strip().splitlines()):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and "drained" in obj:
            return obj
    return {}


def _reclaimed_stale(rep: dict) -> int:
    n = 0
    for note in rep.get("notes") or []:
        if isinstance(note, str) and note.startswith("reclaimed_stale="):
            n += int(note.split("=", 1)[1])
    return n


def _rejected_events() -> int:
    return q("SELECT COUNT(*) FROM events WHERE code='job_rejected'")[0][0]


def scenario_live(pages: int, stale_minutes: int) -> dict:
    """合法长作业 × 心跳在续：B 进场不得回收，A 正常收尾。"""
    verdict = {"scenario": "live", "ok": False, "reclaimed": None, "rejected": None,
               "lease_distinct": 0, "age_at_b_s": 0.0, "final_status": "",
               "inconclusive": False}
    src = build_pdf(pages)
    print(f"[live] enqueue {src.name}", flush=True)
    r = _sh("work", "--enqueue", str(src))
    if r.returncode:
        print(f"[live] enqueue rc={r.returncode} err={r.stderr.strip()[:200]}", flush=True)
        verdict["error"] = "enqueue_failed"
        return verdict
    a = _bg("work", "--once", "--workers", "1")
    window_s = stale_minutes * 60
    need_age = window_s + 8               # 严格越过窗口：旧口径在这里必然误抢
    deadline = time.monotonic() + max(900.0, window_s * 6)
    seen: set[str] = set()
    entered = False
    while time.monotonic() < deadline:
        time.sleep(2.0)
        job_id, _doc, status, started, lease, owner = _row()
        if status != "running":
            if not entered:
                print(f"[live] single-item too fast: status={status} "
                      f"age={_age_s(started):.0f}s < {need_age}s → inconclusive，"
                      f"加大 --pages 重跑", flush=True)
                verdict["inconclusive"] = True
                a.communicate(timeout=3600)
                return verdict
            break
        if lease:
            seen.add(lease)
        age = _age_s(started)
        if age >= need_age and not entered:
            entered = True
            verdict["age_at_b_s"] = round(age, 1)
            print(f"[live] A running age={age:.1f}s (>窗口 {window_s}s)，owner={owner} "
                  f"lease={lease} → 放 B 进场", flush=True)
            b = _sh("work", "--once", "--workers", "1")
            b_out = (b.stdout or "") + (b.stderr or "")
            for ln in _notes(b_out)[:6]:
                print(f"[live][B] {ln[:220]}", flush=True)
            rep = _rep(b_out)
            verdict["reclaimed"] = _reclaimed_stale(rep)
            verdict["b_drained"] = rep.get("drained")
            verdict["rejected"] = int(rep.get("rejected", -1))
            verdict["rejected_events"] = _rejected_events()
            verdict["b_rc"] = b.returncode
    verdict["lease_distinct"] = len(seen)
    out_a = a.communicate(timeout=3600)[0] or ""
    print(f"[live][A rc={a.returncode}] 用时合计，A 侧尾行："
          f"{out_a.strip().splitlines()[-1][:220] if out_a.strip() else ''}", flush=True)
    verdict["final_status"] = _row()[2]
    verdict["docs"] = q("SELECT COUNT(*) FROM docs")[0][0]
    verdict["blocks"] = q("SELECT COUNT(*) FROM blocks")[0][0]
    zips = sorted((Root.path / "out").glob("*.zip"))
    verdict["zips"] = [p.name for p in zips]
    if zips:
        with zipfile.ZipFile(zips[0]) as z:
            verdict["zip_crc_bad"] = z.testzip()
            verdict["zip_entries"] = len(z.namelist())
    verdict["ok"] = (verdict["reclaimed"] == 0 and verdict["rejected"] == 0
                     and verdict["rejected_events"] == 0
                     and verdict["final_status"] == "done"
                     and verdict["docs"] == 1 and verdict["lease_distinct"] >= 2)
    print(f"[live] verdict reclaimed={verdict['reclaimed']} "
          f"rejected={verdict['rejected']}/{verdict['rejected_events']}(events) "
          f"lease_distinct={verdict['lease_distinct']} "
          f"final={verdict['final_status']} docs={verdict['docs']} ok={verdict['ok']}",
          flush=True)
    return verdict


def scenario_dead(pages: int, stale_minutes: int) -> dict:
    """A 被 SIGKILL（OOM 代理）→ 心跳停 → 租约过期 → B 必须回收并跑完（CH-448 不回潮）。"""
    verdict = {"scenario": "dead", "ok": False, "reclaimed": None,
               "lease_expired_seen": False, "final_status": "", "inconclusive": False}
    src = build_pdf(pages)
    print(f"[dead] enqueue {src.name}", flush=True)
    r = _sh("work", "--enqueue", str(src))
    if r.returncode:
        print(f"[dead] enqueue rc={r.returncode} err={(r.stderr or '').strip()[:200]}",
              flush=True)
        verdict["error"] = "enqueue_failed"
        return verdict
    a = _bg("work", "--once", "--workers", "1")
    window_s = stale_minutes * 60
    need_age = window_s + 8               # 严格越过窗口：kill 必须落在在途期内
    deadline = time.monotonic() + max(900.0, window_s * 8)
    killed = False
    while time.monotonic() < deadline:
        time.sleep(2.0)
        _job, _doc, status, started, lease, _owner = _row()
        if not killed:
            if status == "running" and _age_s(started) >= need_age:
                print(f"[dead] A running age={_age_s(started):.0f}s → kill -9 pid={a.pid}",
                      flush=True)
                a.kill()
                killed = True
                verdict["age_at_kill_s"] = round(_age_s(started), 1)
                # 池子进程继承了 A 的 stdout 管道：读端不关就等不到 EOF，
                # 而本场景不消费 A 的输出，判据全在库面与 B 侧。
                if a.stdout is not None:
                    a.stdout.close()
                continue
            if status != "running":
                # A 在到达 kill 窗口前就转换完了 → 本页数测不到崩溃语义
                print(f"[dead] 单件耗时不足：status={status} age={_age_s(started):.0f}s "
                      f"< {need_age}s → inconclusive，加大 --pages 重跑", flush=True)
                verdict["inconclusive"] = True
                return verdict
            continue
        if _lease_past(lease):
            verdict["lease_expired_seen"] = True
            break
    if not killed:
        print("[dead] A 未进入 running（轮询超时）→ inconclusive", flush=True)
        verdict["inconclusive"] = True
        return verdict
    try:
        a.wait(timeout=120)
    except subprocess.TimeoutExpired:
        pass                              # 遗骸子进程仍持有管道：不影响本场景判据
    b = _sh("work", "--once", "--workers", "1")
    b_out = (b.stdout or "") + (b.stderr or "")
    for ln in _notes(b_out)[:6]:
        print(f"[dead][B] {ln[:220]}", flush=True)
    rep = _rep(b_out)
    verdict["reclaimed"] = _reclaimed_stale(rep)
    verdict["b_drained"] = rep.get("drained")
    verdict["b_rejected"] = rep.get("rejected")
    verdict["b_rc"] = b.returncode
    verdict["final_status"] = _row()[2]
    verdict["docs"] = q("SELECT COUNT(*) FROM docs")[0][0]
    zips = sorted((Root.path / "out").glob("*.zip"))
    verdict["zips"] = [p.name for p in zips]
    if zips:
        with zipfile.ZipFile(zips[0]) as z:
            verdict["zip_crc_bad"] = z.testzip()
            verdict["zip_entries"] = len(z.namelist())
    verdict["ok"] = (verdict["reclaimed"] >= 1 and verdict["lease_expired_seen"]
                     and verdict["final_status"] == "done" and verdict["docs"] == 1)
    print(f"[dead] verdict reclaimed={verdict['reclaimed']} "
          f"lease_expired={verdict['lease_expired_seen']} "
          f"final={verdict['final_status']} docs={verdict['docs']} ok={verdict['ok']}",
          flush=True)
    return verdict


def main(argv=None) -> int:
    _console_safe()
    ap = argparse.ArgumentParser(
        description="CH-499 / B-27 租约双进程实测台（live=不得误抢，dead=仍须回收）")
    ap.add_argument("--scenario", choices=("live", "dead", "both"), default="both")
    ap.add_argument("--pages", type=int, default=6000,
                    help="合成多页 PDF 的页数（决定单件耗时是否越过窗口；默认 6000，"
                         "3000 页在 M 系机上约 70s，距 68s 采样点只剩 2s 余量）")
    ap.add_argument("--stale-minutes", type=int, default=1,
                    help="压缩后的 stale 窗口 = 租约时长（分钟，默认 1）")
    ap.add_argument("--lease-cap", type=int, default=0,
                    help="本夹具的 job_lease_cap_minutes（0=不设上限，隔离出租约这一个变量）")
    ap.add_argument("--root", default="/tmp/b27_probe")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    if args.pages < 50 or args.stale_minutes < 1:
        print(json.dumps({"error": "bad_args"}, ensure_ascii=True))
        return 2
    Root.path = Path(args.root)
    Root.keep = bool(args.keep)
    if DOC2MD == str(REPO / ".venv/bin/doc2md") and not Path(DOC2MD).exists():
        print(json.dumps({"error": "venv_doc2md_missing", "path": DOC2MD},
                         ensure_ascii=True))
        return 2
    os.environ["DOC2MD_HOME"] = str(Root.path)
    results: list[dict] = []
    want = ("live", "dead") if args.scenario == "both" else (args.scenario,)
    for name in want:
        setup_root(args.stale_minutes, args.lease_cap)
        if not readback_cfg(args.stale_minutes, args.lease_cap):
            print(json.dumps({"error": "cfg_not_applied",
                              "hint": "DOC2MD_HOME 未生效？见本文件顶部踩坑登记"},
                             ensure_ascii=True))
            return 2
        fn = scenario_live if name == "live" else scenario_dead
        results.append(fn(args.pages, args.stale_minutes))
    if not Root.keep:
        shutil.rmtree(Root.path, ignore_errors=True)
    inconclusive = any(r.get("inconclusive") for r in results)
    ok = all(r.get("ok") for r in results)
    print(json.dumps({"minutes_window": args.stale_minutes * 60,
                      "pages": args.pages, "results": results,
                      "verdict": "ok" if ok else ("inconclusive" if inconclusive
                                                  else "failed")},
                     ensure_ascii=True, sort_keys=True))
    if not ok:
        return 3 if inconclusive else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
