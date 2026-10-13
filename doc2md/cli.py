"""doc2md CLI —— §4.4 / ADR-30 字面入口（命名纪律：小写 doc2md）。

    doc2md serve [--host --port --root --config]        # 起 API 服务（不内置线程，ADR-30）
    doc2md serve token [--role admin|editor|reader] [--label] [--revoke TOKEN]
    doc2md cron --daily | --weekly                      # launchd StartCalendarInterval 触发
    doc2md convert <file|dir> [--mode auto|single|two_phase] [--out DIR] [--json]
    doc2md watch <dir> [--once]                         # FR-111 稳定性判定 + 入队
    doc2md tokenizer export <gguf> [--manifest PATH]    # M0：vocab 导出 + manifest
    doc2md tokenizer verify --manifest PATH --gguf GGUF # M0 验收 4：口径可复现
    doc2md doctor                                       # 环境 / 路径 / 权限自检

退出码：0 正常；2 参数错；3 门禁拒收（E4221）；4 其他业务错；5 运行/系统错。
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
    t = tok.add_parser("token", help="token 管理（API 等价物，§4.4）")
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

    cv = sub.add_parser("convert", help="转换文件 / 目录")
    _add_common(cv)
    cv.add_argument("path")
    cv.add_argument("--mode", choices=("auto", "single", "two_phase"))
    cv.add_argument("--out", help="产物 zip 输出目录（默认 [paths].out）")
    cv.add_argument("--json", action="store_true", help="只打印 JSON 摘要（脚本/CI 用）")
    cv.add_argument("--follow-symlinks", action="store_true")

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
    print(auth.issue(store, args.role, args.label))
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


# --- convert ------------------------------------------------------------------
def _gate_summary(detail: dict) -> dict:
    """CLI 只回摘要（页级明细留在报告 / --json）；避免 stderr 被 20×N 字段刷满。"""
    return {"mode": detail.get("mode"), "sampled_pages": detail.get("sampled_pages"),
            "reasons": detail.get("reasons"), "stats": detail.get("stats")}


def cmd_convert(args: argparse.Namespace) -> int:
    from .pipeline.convert import convert
    from .security.dirguard import WalkLimits, guard_walk
    cfg = _cfg(args)
    src = Path(args.path).expanduser()
    if src.is_dir():
        lim = WalkLimits(follow_symlinks=args.follow_symlinks or bool(
            config.get(cfg, "watch.follow_symlinks", False)),
            max_depth=int(config.get(cfg, "watch.max_depth", 20)),
            skip_hidden=bool(config.get(cfg, "watch.skip_hidden", True)),
            max_files_per_dir=int(config.get(cfg, "watch.max_files_per_dir", 5000)))
        files = [Path(f) for f in guard_walk(src, lim).files
                 if Path(f).suffix.lower() in (".pdf", ".docx")]
    elif src.is_file():
        files = [src]
    else:
        print(f"not found: {src}", file=sys.stderr)
        return EXIT_USAGE
    out = Path(args.out).expanduser() if args.out else _out(cfg)
    rc = EXIT_OK
    for f in files:
        try:
            res = convert(f, cfg=cfg, mode=args.mode, out_dir=out)
        except E4221_GateReject as exc:
            print(json.dumps({"file": f.name, "status": "rejected", "code": exc.code,
                              "gate": _gate_summary(exc.detail or {})},
                             ensure_ascii=False), file=sys.stderr)
            rc = EXIT_GATE
            continue
        except Doc2mdError as exc:
            print(json.dumps({"file": f.name, "status": "error", "code": exc.code,
                              "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            rc = EXIT_BIZ
            continue
        if args.json:
            print(json.dumps({"file": f.name, **res.to_dict(), "metrics": res.metrics,
                              "zip": str(res.zip_path)}, ensure_ascii=False))
        else:
            st = res.meta.get("shard_stats", {})
            print(f"{f.name}: doc_id={res.doc_id} blocks={len(res.ir.blocks)} "
                  f"chars={res.metrics['chars']} sharded={res.meta.get('sharded')} "
                  f"shards={st.get('shards', 1)} stitched={st.get('stitched', 0)} "
                  f"dropped_dupes={st.get('dropped_dupes', 0)} zip={res.zip_path}")
    return rc


# --- watch --------------------------------------------------------------------
def cmd_watch(args: argparse.Namespace) -> int:
    from .scheduler import Scheduler
    from .security.dirguard import WalkLimits
    from .security.watcher import StabilityTracker
    from .storage.db import Store
    from .storage.repo import save_ir
    from .pipeline.convert import convert
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
    import time
    rc = EXIT_OK
    while True:
        try:
            ready = tracker.poll_all(args.dir, lim)
        except Doc2mdError as exc:
            print(json.dumps({"status": "error", "code": exc.code,
                              "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            ready = []
        for f in ready:
            p = Path(f)
            if p.suffix.lower() not in (".pdf", ".docx"):
                continue
            try:
                res = convert(p, cfg=cfg, out_dir=root / "out")
                save_ir(store, res.ir)
                j = sched.submit(res.doc_id, res.source_key)
                sched.mark(j.job_id, "done")
                print(json.dumps({"watched": p.name, "doc_id": res.doc_id,
                                  "job_id": j.job_id}, ensure_ascii=False))
            except E4221_GateReject as exc:
                j = sched.submit("reject:" + p.name)
                sched.mark(j.job_id, "rejected", exc.code, exc.detail)
                print(json.dumps({"watched": p.name, "status": "rejected",
                                  "code": exc.code}, ensure_ascii=False), file=sys.stderr)
                rc = EXIT_GATE
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
    checks = {
        "python": sys.version.split()[0],
        "pipeline": PIPELINE_VERSION,
        "root": str(root),
        "root_exists": root.exists(),
        "root_mode": oct(root.stat().st_mode & 0o777) if root.exists() else None,
        "tokenizer": config.get(cfg, "chunks.tokenizer"),
        "s1_5_sample_pages": config.get(cfg, "s1_5.sample_pages"),
        "shard_mode": config.get(cfg, "s2.mode", "auto"),
        "max_pages_per_shard": config.get(cfg, "s2.max_pages_per_shard"),
        "op_whitelist": config.get(cfg, "llm.op_whitelist"),
        "base_revision_required": config.get(cfg, "api.base_revision_required"),
        "logs_rotate_mb": config.get(cfg, "logs.rotate_mb"),
        "backup_encrypted_external": config.get(cfg, "ops.backup_external_encrypted"),
    }
    # CH-441：清单一律与 pyproject 声明的运行时依赖一致（pypdf 实未使用，列出必误报 MISSING）
    for mod in DEPENDENCY_MODULES:
        try:
            __import__(mod)
            checks[f"dep_{mod}"] = "ok"
        except Exception as exc:             # noqa: BLE001 — 自检用途
            checks[f"dep_{mod}"] = f"MISSING ({type(exc).__name__})"
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return EXIT_OK if not any(str(v).startswith("MISSING") for v in checks.values()) else EXIT_SYS


COMMANDS = {"serve": cmd_serve, "cron": cmd_cron, "convert": cmd_convert,
            "watch": cmd_watch, "tokenizer": cmd_tokenizer, "doctor": cmd_doctor}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn = COMMANDS.get(args.cmd)
    if fn is None:
        return EXIT_USAGE
    try:
        return fn(args)
    except Doc2mdError as exc:
        print(json.dumps(exc.to_body(), ensure_ascii=False), file=sys.stderr)
        return EXIT_GATE if exc.code == "E4221" else EXIT_BIZ
    except KeyboardInterrupt:
        return EXIT_SYS


if __name__ == "__main__":
    raise SystemExit(main())
