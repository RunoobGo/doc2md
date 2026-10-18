#!/usr/bin/env python3
"""ops/bench/b4_delete_race_stress.py —— B-4 并发压测台（常驻 worker × 并发删除）。

这是第八轮审查 B-4 项「本机多进程实跑」用到的手动压测夹具，固化进仓库以便复跑取证。
**非 gate、非 pytest**：放在 `ops/bench/` 且文件名不含 `test_`，pytest 不会自动收集
（`pytest.ini` testpaths=tests、python_files=test_*.py）。真实 WAL 库 + 独立进程跑，
不 import 被测转换代码当桩——转换一律走真实 CLI（`doc2md work`），删除走 `delete_doc`。

覆盖两个场景（`--scenario`）：

  resurrect —— 删除 × 在途转换复活窗口（R8-SVC-1 / R7-SVC-1 事务守护）：
    先播种使 doc 在库，再入队重转；另一进程在 worker 处于 `running`（约 10s 窗口）
    时 `delete_doc` 提交（删 docs/tasks/blocks/search + 写墓碑）。判据：删除成功后
    在途转换是否把 `docs` 行复活。守护成立应恒 `final_docs/blocks/fts=0`、
    `_finish_ok` 走 `mark rowcount=0 → E4041 → 回滚`。CH-484 后 `zip_file_orphans`
    应为 0（E4041 回滚时 `_drop_orphan_artifact` 顺带清掉本轮 `out/{doc}.zip`；
    修复前此处稳定复现 5/5 文件孤儿）。

  claim —— 多 worker 并发 claim 原子性 + R6-SVC-4（source_path=NULL 误认领后果）
    + reclaim 口径：K 个 `doc2md work` 进程消费同一队列，注入若干 NULL-path 遗留
    queued 行与一条「陈旧 running + 无路径」行。判据：每 job 恰被一 worker 领到终态
    （`dup_docs=0`、无双重消费）；NULL-path queued 行被 claim 抢走后 `drain_once`
    一律干净 `failed/E4001`（零误转、不崩、不挂起）；`reclaim_stale`（只认带路径）
    按设计略过 NULL-path 陈旧 running 行。支持 CH-475 延期判定。

用法：
    python3 ops/bench/b4_delete_race_stress.py                       # 两场景全跑
    python3 ops/bench/b4_delete_race_stress.py --scenario resurrect --iters 5
    python3 ops/bench/b4_delete_race_stress.py --scenario claim --workers 4
    python3 ops/bench/b4_delete_race_stress.py --root /tmp/my_b4 --keep

退出码：0=全部判据通过；2=出现复活泄漏 / 双重消费 / NULL-path 误转 / reclaim 误动。
语料取自 `tests/golden/`。两场景各自的临时根在 `--root`（默认 tempfile）下独立建，
跑毕清扫，除非 `--keep`。置于 `if __name__ == '__main__'` 之下（spawn 安全）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC2MD = str(REPO / ".venv/bin/doc2md")
PY = str(REPO / ".venv/bin/python")
GOLDEN = REPO / "tests" / "golden"
# claim 场景用一组「快速小文件」保证多进程在有限时间内排空同一队列。
SMALL = ["small_3p.pdf", "codeindent_1p.pdf", "codeindent_multi_1p.pdf",
         "codeindent_table_1p.pdf", "numlist_1p.pdf", "links_pdf.pdf",
         "badtail_late.pdf"]


def _sh(*a, cwd=str(REPO)):
    return subprocess.run(a, capture_output=True, text=True, cwd=cwd)


def _conn(root: Path):
    c = sqlite3.connect(str(root / "state.sqlite"), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _mkroot(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    (root / "src").mkdir(parents=True)
    (root / "out").mkdir()
    (root / "logs").mkdir()


def _enqueue(root: Path, path: Path):
    r = _sh(DOC2MD, "work", "--enqueue", str(path), "--root", str(root))
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("doc_id"):
            return d["doc_id"], d["job_id"]
    raise RuntimeError(f"enqueue fail: {r.stdout} {r.stderr}")


def _db_state(root: Path, doc_id: str) -> dict:
    c = _conn(root)
    try:
        return {
            "docs": c.execute("SELECT COUNT(*) n FROM docs WHERE doc_id=?", (doc_id,)).fetchone()["n"],
            "blocks": c.execute("SELECT COUNT(*) n FROM blocks WHERE doc_id=?", (doc_id,)).fetchone()["n"],
            "fts": c.execute("SELECT COUNT(*) n FROM search_blocks WHERE doc_id=?", (doc_id,)).fetchone()["n"],
            "tasks": [(x["job_id"][-4:], x["status"]) for x in
                      c.execute("SELECT job_id,status FROM tasks WHERE doc_id=?", (doc_id,))],
            "tomb": c.execute("SELECT COUNT(*) n FROM deletions WHERE doc_id=?", (doc_id,)).fetchone()["n"],
        }
    finally:
        c.close()


# ------------------------------------------------------------------ resurrect

def scenario_resurrect(root: Path, iters: int) -> int:
    """删除成功后在途转换是否复活 docs 行；顺带统计 out/{doc}.zip 文件孤儿。"""
    _mkroot(root)
    src = root / "src/a.pdf"
    shutil.copy(GOLDEN / "large_320p.pdf", src)

    doc_id0, _ = _enqueue(root, src)
    t0 = time.time()
    _sh(DOC2MD, "work", "--root", str(root), "--once")     # 播种：使 doc 落库
    print(f"[setup] seed convert {time.time()-t0:.1f}s doc_id={doc_id0[:8]}")

    def race_once(i: int) -> dict:
        doc_id, job = _enqueue(root, src)                  # 队列多一条待处理；docs 仍在库
        seeded_before = _db_state(root, doc_id)["docs"]
        w = subprocess.Popen([DOC2MD, "work", "--root", str(root), "--max-jobs", "1"],
                             cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        c = _conn(root)
        ran = False
        deadline = time.time() + 90
        while time.time() < deadline:
            st = dict(c.execute("SELECT job_id,status FROM tasks WHERE doc_id=?",
                                (doc_id,)).fetchall()).get(job)
            if st == "running":
                ran = True
                break
            if st in ("done", "failed", "rejected"):
                break
            time.sleep(0.05)
        time.sleep(0.6)                                    # 落在 running 窗口中段
        dr = _sh(PY, "-c",
                 "import json;from pathlib import Path;"
                 "from doc2md.storage.db import Store;from doc2md.storage.repo import delete_doc;"
                 f"r=Path({str(root)!r});s=Store(r/'state.sqlite');"
                 f"print(json.dumps(delete_doc(s,{doc_id!r},'stress',r,out_dir=r/'out',tmp_dir=r/'tmp')));s.close()")
        w.communicate()
        c.close()
        st = _db_state(root, doc_id)
        return {
            "iter": i, "job": job[-6:], "seeded_before": seeded_before, "reached_running": ran,
            "delete_rc": dr.returncode, "delete_err": (dr.stderr or "")[:120],
            "final_docs": st["docs"], "final_blocks": st["blocks"], "final_fts": st["fts"],
            "final_tasks": st["tasks"], "tombstones": st["tomb"],
            "zip_orphan": (root / "out" / f"{doc_id}.zip").exists(),
        }

    def ensure_seeded(doc_id: str) -> bool:
        if _db_state(root, doc_id)["docs"] >= 1:
            return True
        _enqueue(root, src)
        _sh(DOC2MD, "work", "--root", str(root), "--once")
        return _db_state(root, doc_id)["docs"] >= 1

    true_leaks = orphan = ok_guard = 0
    for i in range(iters):
        ensure_seeded(doc_id0)                             # 保证删除命中「已存在 doc」
        r = race_once(i)
        deleted_ok = r["delete_rc"] == 0
        if deleted_ok and (r["final_docs"] or r["final_blocks"] or r["final_fts"]):
            true_leaks += 1                                # 删除成功却复活 = 守护失效
        elif deleted_ok and r["reached_running"]:
            ok_guard += 1                                  # 落在在途窗口且库干净 = 守护生效样本
        if r["zip_orphan"]:
            orphan += 1
        print(f"[iter {i}] run={r['reached_running']} del_rc={r['delete_rc']} "
              f"docs={r['final_docs']} blocks={r['final_blocks']} fts={r['final_fts']} "
              f"tomb={r['tombstones']} zip={r['zip_orphan']} tasks={r['final_tasks']} "
              f"delerr={r['delete_err']!r}")

    print("SUMMARY " + json.dumps({
        "scenario": "resurrect", "iter": iters,
        "guard_passed_deletes_landed_inflight": ok_guard,
        "true_resurrection_leaks": true_leaks,
        "zip_file_orphans": orphan,
        "note": "CH-484 后 zip_file_orphans 应为 0（E4041 回滚清产物）",
    }))
    return 2 if true_leaks else 0


# ----------------------------------------------------------------------- claim

def scenario_claim(root: Path, k: int) -> int:
    """K worker 并发领同一队列：claim 原子性 + NULL-path 认领后果 + reclaim 口径。"""
    _mkroot(root)
    srcs = []
    for n in SMALL:
        d = root / "src" / n
        shutil.copy(GOLDEN / n, d)
        srcs.append(d)
    for s in srcs:
        _enqueue(root, s)                                  # 入队全部（doc_id 各异）

    c = _conn(root)
    alljobs = [r["job_id"] for r in c.execute(
        "SELECT job_id FROM tasks WHERE source_path IS NOT NULL ORDER BY created_at")]
    null_ids = alljobs[-3:]                                # 注入 3 条无消费路径的遗留 queued 行
    for j in null_ids:
        c.execute("UPDATE tasks SET source_path=NULL WHERE job_id=?", (j,))
    stale_job = alljobs[0]                                 # 注入 1 条陈旧 running + 无路径行
    c.execute("UPDATE tasks SET status='running', started_at='2000-01-01T00:00:00+00:00',"
              " source_path=NULL WHERE job_id=?", (stale_job,))
    c.commit()
    n_queued = c.execute("SELECT COUNT(*) n FROM tasks WHERE status='queued'").fetchone()["n"]
    n_running = c.execute("SELECT COUNT(*) n FROM tasks WHERE status='running'").fetchone()["n"]
    c.close()
    print(f"[setup] total={len(alljobs)} queued={n_queued} "
          f"running(stale,nullpath)={n_running} nullpath_queued={len(null_ids)}")

    procs = [subprocess.Popen([DOC2MD, "work", "--root", str(root), "--once"],
                             cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(k)]
    rcs = [p.communicate() for p in procs]
    _sh(DOC2MD, "work", "--root", str(root), "--once")     # 补一轮确保 queued 排空到终态

    c = _conn(root)
    by = {r["job_id"]: (r["status"], r["error_code"])
          for r in c.execute("SELECT job_id,status,error_code FROM tasks")}
    nullpath_states = {j: by.get(j) for j in null_ids}
    stale_state = by.get(stale_job)
    status_counts: dict[str, int] = {}
    for st, _ in by.values():
        status_counts[st] = status_counts.get(st, 0) + 1
    docs_rows = c.execute("SELECT COUNT(*) n FROM docs").fetchone()["n"]
    dup_docs = c.execute("SELECT doc_id,COUNT(*) n FROM docs GROUP BY doc_id HAVING n>1").fetchall()
    c.close()

    nullpath_all_failed = all(v and v[0] == "failed" and v[1] == "E4001"
                              for v in nullpath_states.values())
    nullpath_converted = sum(1 for v in nullpath_states.values() if v and v[0] == "done")
    stale_untouched = bool(stale_state and stale_state[0] == "running")

    print(f"[result] worker_rcs={[p.returncode for p in procs]} status={status_counts} "
          f"docs={docs_rows} dup_docs={len(dup_docs)}")
    print(f"[result] nullpath_states={nullpath_states} stale_running_nullpath={stale_state}")
    print("SUMMARY " + json.dumps({
        "scenario": "claim", "total_jobs": len(alljobs), "concurrent_workers": k,
        "status_counts": status_counts,
        "left_queued_after_drain": status_counts.get("queued", 0),
        "duplicate_consume_or_docs": len(dup_docs),
        "nullpath_all_cleanly_failed_E4001": nullpath_all_failed,
        "nullpath_misconverted_as_done": nullpath_converted,
        "stale_nullpath_running_untouched_by_reclaim": stale_untouched,
        "worker_stderr_tail": [e[:120] for _, e in rcs if e],
    }))
    return 0 if (len(dup_docs) == 0 and nullpath_all_failed and nullpath_converted == 0
                 and stale_untouched) else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-4 并发压测台（手动夹具，非 CI gate）")
    ap.add_argument("--scenario", choices=["resurrect", "claim", "all"], default="all")
    ap.add_argument("--iters", type=int, default=5, help="resurrect：复活窗口重打轮数")
    ap.add_argument("--workers", type=int, default=4, help="claim：并发 worker 进程数")
    ap.add_argument("--root", default="", help="临时根（默认 tempfile，跑毕清扫）")
    ap.add_argument("--keep", action="store_true", help="留存临时根供取证")
    args = ap.parse_args(argv)

    scratch = Path(args.root) if args.root else Path(tempfile.mkdtemp(prefix="b4_"))
    rc = 0
    try:
        if args.scenario in ("resurrect", "all"):
            rc |= scenario_resurrect(scratch / "resurrect", args.iters)
        if args.scenario in ("claim", "all"):
            rc |= scenario_claim(scratch / "claim", args.workers)
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            print(f"[keep] 临时根留存于 {scratch}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
