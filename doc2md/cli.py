"""doc2md CLI —— 方案 附录 B / ADR-30 字面入口（命名纪律：小写 doc2md）。

    doc2md serve [--host --port --root --config]        # 起 API 服务（不内置线程，ADR-30）
    doc2md serve token [--role admin|editor|reader] [--label] [--revoke TOKEN]
    doc2md cron --daily | --weekly                      # launchd StartCalendarInterval 触发
    doc2md convert <file|dir> [--mode auto|single|two_phase] [--out DIR] [--json] [--workers N]
    doc2md watch <dir> [--once]                         # FR-111 稳定性判定 + 入队
    doc2md work [--once|--max-jobs N|--enqueue PATH|--status]   # CH-449 队列消费
    doc2md tokenizer export <gguf> [--manifest PATH]    # M0：vocab 导出 + manifest
    doc2md tokenizer verify --manifest PATH --gguf GGUF # M0 验收 4：口径可复现
    doc2md doctor                                       # 环境 / 路径 / 权限自检

退出码：0 正常；2 参数错；3 门禁拒收（E4221）；4 其他业务错；5 运行/系统错。
控制台纪律（CH-451）：Windows cp936/GBK 下输出流按 errors="replace" 降级，
机器可读行（--json / 队列 / watch）一律 ASCII 转义 JSON，两种代码页同字节可解析。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import PIPELINE_VERSION, config
from .errors import Doc2mdError, E4221_GateReject

EXIT_OK, EXIT_USAGE, EXIT_GATE, EXIT_BIZ, EXIT_SYS = 0, 2, 3, 4, 5


def _console_safe() -> None:
    """CH-451：Windows 控制台默认 cp936/GBK，文件名里的非 GBK 字符（emoji、
    生僻字、日文）会让 `print()` 直接抛 UnicodeEncodeError —— 实测
    `doc2md convert "🔥报告.pdf"` 在 cp936 代码页下整条命令崩栈（不是转换失败，
    是**打印**失败）。口径：输出流按 errors="replace" 降级，转换结果本身不受影响。
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


def _jline(obj: dict) -> str:
    """机器可读行（--json / 脚本 / CI 消费）：一律 ASCII 转义（CH-451）。

    `ensure_ascii=False` 在 GBK 控制台上要么崩、要么被 errors="replace" 打成 `?`
    —— 那就不再是有效 JSON。ASCII 转义在两种控制台下同字节可解析。
    """
    return json.dumps(obj, ensure_ascii=True)

# CH-441：doctor 自检的依赖清单 = pyproject 运行时依赖的导入名（单一口径，防漂移）。
# 实装只用 pdfplumber / python-docx / cryptography / Flask；
# pypdf、Jinja2、PyYAML、watchdog 从未被 doc2md 包导入（S1 走 pdfplumber，
# 审阅页与日报是内置 f-string 渲染，watch 走内置轮询 StabilityTracker）。
DEPENDENCY_MODULES = ("pdfplumber", "docx", "flask", "cryptography")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="doc2md.toml 路径（默认 ./ 或 $DOC2MD_HOME）")
    p.add_argument("--root", help="工作根目录（覆盖 [paths].root）")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="doc2md",
                                 description="文本型 PDF / DOCX → Markdown 离线管线")
    ap.add_argument("--version", action="version", version=f"doc2md {PIPELINE_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="启动 API 服务；带 token 子命令时仅做 token 管理")
    _add_common(serve)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    tok = serve.add_subparsers(dest="sub")
    t = tok.add_parser("token", help="token 管理（API 等价物，方案 附录 C）")
    _add_common(t)
    t.add_argument("--role", choices=("admin", "editor", "reader"), default="admin")
    t.add_argument("--label", default="")
    t.add_argument("--revoke", metavar="TOKEN", help="吊销指定 token（≤5s 生效）")
    t.add_argument("--list", action="store_true", dest="list_tokens")

    cr = sub.add_parser("cron", help="定时任务（launchd 触发，ADR-30）")
    _add_common(cr)
    g = cr.add_mutually_exclusive_group(required=True)
    g.add_argument("--daily", action="store_true")
    g.add_argument("--weekly", action="store_true")
    cr.add_argument("--no-backup", action="store_true", help="跳过备份步骤（排障用）")

    bk = sub.add_parser("backup", help="备份恢复演练（DoD 第 11 条，CH-456 / T7）")
    _add_common(bk)
    bksub = bk.add_subparsers(dest="bk_cmd", required=True)
    dl = bksub.add_parser("drill", help="隔离沙箱内跑完整恢复闭环（快照→加密→丢失→轮出→恢复→校验→比对）")
    _add_common(dl)
    dl.add_argument("--keep", action="store_true", help="保留演练沙箱目录（取证用）")

    cv = sub.add_parser("convert", help="转换文件 / 目录")
    _add_common(cv)
    cv.add_argument("path")
    cv.add_argument("--mode", choices=("auto", "single", "two_phase"))
    cv.add_argument("--out", help="产物 zip 输出目录（默认 [paths].out）")
    cv.add_argument("--json", action="store_true", help="只打印 JSON 摘要（脚本/CI 用）")
    cv.add_argument("--follow-symlinks", action="store_true")
    cv.add_argument("--workers", type=int,
                    help="批量进程数（CH-448）；缺省 = [perf].workers（0 自动 min(4, CPU)），1 = 串行")
    cv.add_argument("--llm-mode", choices=("offline", "server"),
                    help="LLM 补助实现（CH-456 / T2）：offline = 确定性占位（默认，零网络）；"
                         "server = 本地 llama-server（llm.bind:llm.port）")
    cv.add_argument("--llm-no-fallback", action="store_true",
                    help="server 模式服务不可用时明确失败（E5041 / 退出码 4），不回退 offline")

    wk = sub.add_parser("work", help="消费 tasks 队列（CH-449，低配机异步转换）")
    _add_common(wk)
    wk.add_argument("--once", action="store_true", help="排空当前队列即退出")
    wk.add_argument("--max-jobs", type=int, default=0, help="处理 N 个作业后退出")
    wk.add_argument("--workers", type=int, help="并发进程数（覆盖 [perf].workers）")
    wk.add_argument("--idle", type=float, default=5.0, help="队列空时轮询间隔秒")
    wk.add_argument("--enqueue", metavar="PATH",
                    help="先把文件/目录入队（不直接转换）；加 --once 则入队后立即排空")
    wk.add_argument("--status", action="store_true", help="打印队列计数后退出")

    wa = sub.add_parser("watch", help="watch folder：稳定性判定后提交作业")
    _add_common(wa)
    wa.add_argument("dir")
    wa.add_argument("--once", action="store_true", help="单轮扫描后退出（cron 模式）")
    wa.add_argument("--interval", type=float, help="轮询间隔秒（默认 [watch].stable_seconds）")

    tz = sub.add_parser("tokenizer", help="M0：GGUF 内嵌 vocab 工具链")
    _add_common(tz)
    tzsub = tz.add_subparsers(dest="tz_cmd", required=True)
    ex = tzsub.add_parser("export")
    _add_common(ex)
    ex.add_argument("gguf")
    ex.add_argument("--vocab", help="vocab JSONL 输出路径")
    ex.add_argument("--manifest", help="manifest 输出路径（默认 [chunks].manifest）")
    vf = tzsub.add_parser("verify")
    _add_common(vf)
    vf.add_argument("--manifest", required=True)
    vf.add_argument("--gguf", required=True)

    dr = sub.add_parser("doctor", help="环境自检")
    _add_common(dr)
    return ap


def _cfg(args: argparse.Namespace) -> dict:
    cfg = config.load(args.config)
    if getattr(args, "root", None):
        cfg.setdefault("paths", {})["root"] = args.root
    return cfg


def _root(cfg: dict) -> Path:
    return config.path_setting(cfg, "paths.root", ".")


def _out(cfg: dict) -> Path:
    return config.path_setting(cfg, "paths.out", _root(cfg), str(_root(cfg) / "out"))


def _free_mb(path: Path) -> int:
    from .ops.diskguard import free_mb
    try:
        return free_mb(path)
    except OSError:
        return -1


# --- serve ------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if getattr(args, "sub", None) == "token":
        return _cmd_token(args, cfg)
    root = _root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)                       # M8 任务 6：work/ 权限 700
    from .api import create_app
    app = create_app(cfg, root)
    host = args.host or str(config.get(cfg, "api.bind", "127.0.0.1"))
    port = args.port or int(config.get(cfg, "api.port", 8090))
    print(f"doc2md serve http://{host}:{port} root={root} pipeline={PIPELINE_VERSION}",
          file=sys.stderr)
    try:
        app.run(host=host, port=port, threaded=True)   # 不启常驻线程跑定时任务（ADR-30）
    except OSError as exc:
        print(f"serve failed: {exc}", file=sys.stderr)
        return EXIT_SYS
    return EXIT_OK


def _cmd_token(args: argparse.Namespace, cfg: dict) -> int:
    from . import auth
    from .storage.db import Store
    store = Store(_root(cfg) / "state.sqlite")
    if args.list_tokens:
        rows = store.q("SELECT token, role, label, created_at, revoked_at FROM tokens")
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.revoke:
        auth.revoke(store, args.revoke)
        print(json.dumps({"revoked": True, "effective_within_s": int(config.get(
            cfg, "api.revoke_effective_seconds", 5))}, ensure_ascii=False))
        return EXIT_OK
    print(auth.issue(store, args.role, args.label,
                     allowed=config.get(cfg, "api.token_roles", auth.ROLES)))
    return EXIT_OK


# --- cron -------------------------------------------------------------------
def cmd_cron(args: argparse.Namespace) -> int:
    from . import cron
    cfg = _cfg(args)
    mode = "weekly" if args.weekly else "daily"
    rep = cron.run(cfg, _root(cfg), mode=mode, with_backup=not args.no_backup)
    print(json.dumps({"mode": rep.mode, "ok": rep.ok, "steps": rep.steps},
                     ensure_ascii=False, indent=2))
    return EXIT_OK if rep.ok else EXIT_SYS


# --- backup drill ---------------------------------------------------------------
def cmd_backup_drill(args: argparse.Namespace) -> int:
    """备份恢复演练（DoD 第 11 条）：一条命令跑完恢复闭环，沙箱隔离、不碰线上库。"""
    from .ops.drill import DrillFailure, run_drill
    cfg = _cfg(args)
    root = _root(cfg)
    try:
        rep = run_drill(cfg, root, keep=args.keep)
    except DrillFailure as exc:
        print(_jline({"ok": False, "error": str(exc)}), file=sys.stderr)
        return EXIT_BIZ
    print(_jline(rep.to_dict()))
    return EXIT_OK if rep.ok else EXIT_BIZ


# --- convert ------------------------------------------------------------------
def _gate_summary(detail: dict) -> dict:
    """CLI 只回摘要（页级明细留在报告 / --json）；避免 stderr 被 20×N 字段刷满。"""
    return {"mode": detail.get("mode"), "sampled_pages": detail.get("sampled_pages"),
            "reasons": detail.get("reasons"), "stats": detail.get("stats")}


def cmd_convert(args: argparse.Namespace) -> int:
    from .pipeline.batch import default_workers, run_batch
    cfg = _cfg(args)
    # CH-456 / T2：--llm-mode / --llm-no-fallback 覆盖声明性配置（口径与配置键一致）
    if getattr(args, "llm_mode", None):
        cfg.setdefault("llm", {})["mode"] = args.llm_mode
    if getattr(args, "llm_no_fallback", False):
        cfg.setdefault("llm", {})["fallback"] = False
    src = Path(args.path).expanduser()
    if src.is_dir():
        files = _dir_files(src, cfg, bool(args.follow_symlinks))
    elif src.is_file():
        files = [src]
    else:
        print(f"not found: {src}", file=sys.stderr)
        return EXIT_USAGE
    out = Path(args.out).expanduser() if args.out else _out(cfg)
    rc = EXIT_OK
    if not files:
        print(_jline({"status": "empty", "path": str(src)}), file=sys.stderr)
        return rc
    # CH-448：目录投递走有界进程池（GIL 下线程池对解析无增益；实测口径见方案 第十二
    # 章吞吐表）；产物与串行逐字节一致（等价性由门禁把住）。`--workers 1` 退回串行。
    workers = args.workers if args.workers is not None else default_workers(cfg)
    if len(files) > 1 and workers > 1:
        results = run_batch(files, cfg=cfg, mode=args.mode, out_dir=out, workers=workers)
        for res in results:
            rc = max(rc, _report(res, as_json=args.json))
        return rc

    from .errors import Doc2mdError, E4221_GateReject
    from .pipeline.convert import convert
    for f in files:
        try:
            res = convert(f, cfg=cfg, mode=args.mode, out_dir=out)
        except E4221_GateReject as exc:
            print(_jline({"file": f.name, "status": "rejected", "code": exc.code,
                          "gate": _gate_summary(exc.detail or {})}), file=sys.stderr)
            rc = max(rc, EXIT_GATE)
            continue
        except Doc2mdError as exc:
            print(_jline({"file": f.name, "status": "error", "code": exc.code,
                          "message": str(exc)}), file=sys.stderr)
            rc = max(rc, EXIT_BIZ)
            continue
        rc = max(rc, _report({"file": f.name, "status": "ok", "doc_id": res.doc_id,
                              "blocks": len(res.ir.blocks), "chars": res.metrics["chars"],
                              "zip": str(res.zip_path) if res.zip_path else "",
                              "sharded": res.meta.get("sharded"),
                              "shard_stats": res.meta.get("shard_stats", {})},
                             as_json=args.json))
    return rc


def _report(res: dict, *, as_json: bool) -> int:
    """批量/串行共用的结果打印；返回该条对应的退出码贡献（调用方取 max = 最严重）。"""
    status = res.get("status")
    name = Path(str(res.get("file", ""))).name
    payload = {"file": name, "status": status, "doc_id": res.get("doc_id", ""),
               "blocks": res.get("blocks", 0), "chars": res.get("chars", 0),
               "zip": res.get("zip", "")}
    if res.get("code"):
        payload["code"] = res["code"]
    if res.get("message"):
        payload["message"] = res["message"]
    if res.get("gate"):
        payload["gate"] = res["gate"]
    if as_json:
        print(_jline(payload))
    elif status == "ok":
        st = res.get("shard_stats") or {}
        print(f"{name}: doc_id={payload['doc_id']} blocks={payload['blocks']} "
              f"chars={payload['chars']} sharded={res.get('sharded')} "
              f"shards={st.get('shards', 1)} stitched={st.get('stitched', 0)} "
              f"dropped_dupes={st.get('dropped_dupes', 0)} zip={payload['zip']}")
    else:
        print(_jline(payload), file=sys.stderr)
    if status == "ok":
        return EXIT_OK
    return EXIT_GATE if status == "rejected" else EXIT_BIZ


# --- work ---------------------------------------------------------------------
def cmd_work(args: argparse.Namespace) -> int:
    """队列消费（CH-449）。`serve` 不起线程（ADR-30），异步转换由本进程承担。"""
    from . import worker
    from .scheduler import Scheduler
    from .storage.db import Store
    cfg = _cfg(args)
    root = _root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "state.sqlite")
    rc = EXIT_OK
    try:
        if args.status:
            print(_jline(worker.queue_stats(store)))
            return rc
        if args.enqueue:
            sched = Scheduler(store)
            src = Path(args.enqueue).expanduser()
            files = [src] if src.is_file() else _dir_files(src, cfg, False)
            queued, skipped = 0, 0
            for f in files:
                try:
                    it = worker.enqueue(sched, f)
                except Doc2mdError as exc:
                    print(_jline({"file": f.name, "status": "error", "code": exc.code,
                                  "message": exc.message}), file=sys.stderr)
                    skipped += 1
                    rc = max(rc, EXIT_BIZ)
                    continue
                queued += 1
                print(_jline(it))
            print(_jline({"enqueued": queued, "rejected": skipped,
                          "counts": worker.queue_stats(store)}))
            if not args.once:
                return rc
        rep = worker.run_work(cfg, root, once=args.once, max_jobs=args.max_jobs,
                              workers=args.workers, idle_seconds=args.idle)
        print(_jline(rep.to_dict()))
        return EXIT_SYS if rep.failed else rc
    finally:
        store.close()


# --- watch --------------------------------------------------------------------
def _dir_files(src: Path, cfg: dict, follow: bool) -> list[Path]:
    from .security.dirguard import WalkLimits, guard_walk
    lim = WalkLimits(follow_symlinks=follow or bool(
        config.get(cfg, "watch.follow_symlinks", False)),
        max_depth=int(config.get(cfg, "watch.max_depth", 20)),
        skip_hidden=bool(config.get(cfg, "watch.skip_hidden", True)),
        max_files_per_dir=int(config.get(cfg, "watch.max_files_per_dir", 5000)))
    return [Path(f) for f in guard_walk(src, lim).files
            if Path(f).suffix.lower() in (".pdf", ".docx")]


def cmd_watch(args: argparse.Namespace) -> int:
    from . import worker
    from .pipeline.convert import convert
    from .scheduler import Scheduler
    from .security.dirguard import WalkLimits
    from .security.watcher import StabilityTracker
    from .storage.db import Store
    from .storage.repo import save_ir
    cfg = _cfg(args)
    root = _root(cfg)
    store = Store(root / "state.sqlite")
    sched = Scheduler(store)
    lim = WalkLimits(
        follow_symlinks=bool(config.get(cfg, "watch.follow_symlinks", False)),
        max_depth=int(config.get(cfg, "watch.max_depth", 20)),
        skip_hidden=bool(config.get(cfg, "watch.skip_hidden", True)),
        max_files_per_dir=int(config.get(cfg, "watch.max_files_per_dir", 5000)))
    tracker = StabilityTracker(stable_seconds=float(config.get(cfg, "watch.stable_seconds", 5)))
    interval = args.interval or tracker.stable_seconds
    async_queue = bool(config.get(cfg, "perf.queue_async", False))
    import time
    rc = EXIT_OK
    while True:
        try:
            ready = tracker.poll_all(args.dir, lim)
        except Doc2mdError as exc:
            print(_jline({"status": "error", "code": exc.code,
                          "message": str(exc)}), file=sys.stderr)
            ready = []
        for f in ready:
            p = Path(f)
            if p.suffix.lower() not in (".pdf", ".docx"):
                continue
            if async_queue:
                # CH-449：低配机上 watch 目录批量落文件时，稳定即入队、由 `doc2md work`
                # 并发消费；不在轮询线程里做 CPU 重活（否则稳定窗口被计算拖长）。
                try:
                    it = worker.enqueue(sched, p)
                    print(_jline(it))
                except Doc2mdError as exc:
                    print(_jline({"watched": p.name, "status": "error", "code": exc.code,
                                  "message": exc.message}), file=sys.stderr)
                    rc = max(rc, EXIT_BIZ)
                continue
            try:
                res = convert(p, cfg=cfg, out_dir=root / "out")
                save_ir(store, res.ir)
                j = sched.submit(res.doc_id, res.source_key)
                sched.mark(j.job_id, "done")
                print(_jline({"watched": p.name, "doc_id": res.doc_id, "job_id": j.job_id}))
            except E4221_GateReject as exc:
                j = sched.submit("reject:" + p.name)
                sched.mark(j.job_id, "rejected", exc.code, exc.detail)
                print(_jline({"watched": p.name, "status": "rejected",
                              "code": exc.code}), file=sys.stderr)
                rc = EXIT_GATE
            except Doc2mdError as exc:
                print(_jline({"watched": p.name, "status": "error", "code": exc.code,
                              "message": exc.message}), file=sys.stderr)
                rc = max(rc, EXIT_BIZ)
        if args.once:
            return rc
        time.sleep(interval)


# --- tokenizer ------------------------------------------------------------------
def cmd_tokenizer(args: argparse.Namespace) -> int:
    from .ops import gguf
    cfg = _cfg(args)
    if args.tz_cmd == "export":
        manifest_path = Path(args.manifest or config.get(
            cfg, "chunks.manifest", str(_root(cfg) / "tokenizer.manifest.json")))
        vocab = args.vocab or str(manifest_path.with_name("vocab.jsonl"))
        man = gguf.export_vocab(args.gguf, vocab)
        gguf.write_manifest(man, manifest_path)
        print(json.dumps({"manifest": str(manifest_path), "vocab": man["vocab_file"],
                          "n_vocab": man["n_vocab"],
                          "gguf_sha256_prefix": man["gguf_sha256_prefix"]},
                         ensure_ascii=False, indent=2))
        return EXIT_OK
    res = gguf.verify_manifest(args.manifest, args.gguf)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return EXIT_OK if all(res[k] for k in ("gguf_ok", "vocab_ok", "name_ok", "n_vocab_ok")) else EXIT_SYS


# --- doctor ---------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    root = _root(cfg)
    # CH-456（T9）：冻结键校验进自检面——配置错误在 doctor 就暴露，不进运行期静默失效
    try:
        config.validate(cfg)
        frozen_ok = True
    except ValueError as exc:
        frozen_ok = False
        print(f"config invalid: {exc}", file=sys.stderr)
    checks = {
        "python": sys.version.split()[0],
        "pipeline": PIPELINE_VERSION,
        "config_frozen_keys": "ok" if frozen_ok else "invalid",
        "run_profile": config.get(cfg, "run.profile"),
        "root": str(root),
        "root_exists": root.exists(),
        "root_mode": oct(root.stat().st_mode & 0o777) if root.exists() else None,
        "tokenizer": config.get(cfg, "chunks.tokenizer"),
        "s1_5_sample_pages": config.get(cfg, "s1_5.sample_pages"),
        "shard_mode": config.get(cfg, "s2.mode", "auto"),
        "max_pages_per_shard": config.get(cfg, "s2.max_pages_per_shard"),
        "op_whitelist": config.get(cfg, "llm.op_whitelist"),
        "llm_mode": config.get(cfg, "llm.mode", "offline"),
        "llm_fallback": config.get(cfg, "llm.fallback", True),
        "llm_endpoint": f"{config.get(cfg, 'llm.bind')}:{config.get(cfg, 'llm.port')}",
        "llm_vendor_models": _vendor_models(cfg),
        "base_revision_required": config.get(cfg, "api.base_revision_required"),
        "logs_rotate_mb": config.get(cfg, "logs.rotate_mb"),
        "backup_encrypted_external": config.get(cfg, "ops.backup_external_encrypted"),
        # CH-447/448/450：低配机三项（内存分块 / 并发 / 磁盘门禁）在自检面可见
        "s1_chunk_pages": config.get(cfg, "s1.chunk_pages"),
        "perf_workers": config.get(cfg, "perf.workers"),
        "cpu_count": os.cpu_count(),
        "perf_queue_async": config.get(cfg, "perf.queue_async"),
        "min_free_mb": config.get(cfg, "perf.min_free_mb"),
        "free_mb": _free_mb(root),
        "console_encoding": getattr(sys.stdout, "encoding", None),
    }
    # CH-441：清单一律与 pyproject 声明的运行时依赖一致（pypdf 实未使用，列出必误报 MISSING）
    for mod in DEPENDENCY_MODULES:
        try:
            __import__(mod)
            checks[f"dep_{mod}"] = "ok"
        except Exception as exc:             # noqa: BLE001 — 自检用途
            checks[f"dep_{mod}"] = f"MISSING ({type(exc).__name__})"
    # CH-456 / T2：server 模式下探测 llama-server /health（offline 模式不探测，零网络）
    if str(config.get(cfg, "llm.mode", "offline")) == "server":
        from .llm import LlmClient
        ok, why = LlmClient.from_cfg(cfg).probe()
        checks["llm_server"] = "reachable" if ok else f"unreachable ({why})"
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return EXIT_OK if not any(str(v).startswith("MISSING") for v in checks.values()) else EXIT_SYS


def _vendor_models(cfg) -> list[str]:
    """paths.vendor_models 下的 gguf 资产清单（T2 消费点：定位本地模型文件）。"""
    import glob
    base = str(config.get(cfg, "paths.vendor_models", "") or "")
    if not base:
        return []
    return sorted(Path(p).name for p in glob.glob(str(Path(base) / "*.gguf")))


COMMANDS = {"serve": cmd_serve, "cron": cmd_cron, "convert": cmd_convert,
            "watch": cmd_watch, "work": cmd_work,
            "tokenizer": cmd_tokenizer, "doctor": cmd_doctor,
            "backup": cmd_backup_drill}


def main(argv: list[str] | None = None) -> int:
    _console_safe()                    # CH-451：cp936 控制台上打印非 GBK 文件名不得崩
    args = build_parser().parse_args(argv)
    fn = COMMANDS.get(args.cmd)
    if fn is None:
        return EXIT_USAGE
    try:
        return fn(args)
    except Doc2mdError as exc:
        print(_jline(exc.to_body()), file=sys.stderr)
        return EXIT_GATE if exc.code == "E4221" else EXIT_BIZ
    except KeyboardInterrupt:
        return EXIT_SYS
    except OSError as exc:             # ENOSPC / EACCES / ELOOP：低配机常见，栈回溯不是契约输出
        print(_jline({"code": "E5031", "message": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        return EXIT_SYS


if __name__ == "__main__":
    raise SystemExit(main())
