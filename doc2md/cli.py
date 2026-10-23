"""doc2md CLI —— 方案 附录 B / ADR-30 字面入口（命名纪律：小写 doc2md）。

    doc2md serve [--host --port --root --config]        # 起 API 服务（不内置线程，ADR-30）
    doc2md serve token [--role admin|editor|reader] [--label] [--revoke TOKEN]
    doc2md cron --daily | --weekly                      # launchd StartCalendarInterval 触发
    doc2md convert <file|dir> [--mode auto|single|two_phase] [--out DIR] [--json] [--workers N] \
                [--skip-existing] [--force]
    doc2md watch <dir> [--once]                         # FR-111 稳定性判定 + 入队
    doc2md work [--once|--max-jobs N|--enqueue PATH|--status]   # CH-449 队列消费
    doc2md search QUERY [--full] [--limit N]    # 检索已入库文档（API /search 的 CLI 等价物，CH-491）
    doc2md docs list [--limit N --offset N --status S]   # 库内文档清单（O-07）
    doc2md docs show <doc_id> [--limit N --offset N]     # 单篇元数据 + 块窗口（API /docs/<id> 等价物）
    doc2md docs export <doc_id...> | --all [--out DIR]   # 导出 content.md + manifest.json 对账
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
from .errors import Doc2mdError, E4041_NotFound, E4221_GateReject, E5031_StorageError

EXIT_OK, EXIT_USAGE, EXIT_GATE, EXIT_BIZ, EXIT_SYS = 0, 2, 3, 4, 5

# O-07：CLI 只读列表面的分页硬顶——与 API `_MAX_ROWS` 同口径（CH-476 R7-PERF-j：
# 无界上限会把全库行物化进终端/管道）。超限截断不报错（search 既有口径）。
DOCS_MAX_ROWS = 500


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


def _jline(obj, indent: int | None = None) -> str:
    """机器可读 JSON（--json / 脚本 / CI 消费）：一律 ASCII 转义（CH-451）。

    `ensure_ascii=False` 在 GBK 控制台上要么崩、要么被 errors="replace" 打成 `?`
    —— 那就不再是有效 JSON。ASCII 转义在两种控制台下同字节可解析。
    CH-475（R6-OPS-3）：所有 CLI 机器可读 JSON 出口统一走此函数——含 doctor/cron/
    list-tokens 这类「人读 + 机读」的 indent=2 面，消除绕过 `_jline` 的 ensure_ascii=False 旁路。
    """
    return json.dumps(obj, ensure_ascii=True, indent=indent)


def _err_payload(exc, message: str, **extra) -> dict:
    """错误行 payload（CH-492 / F-05）：detail 非空时带上。

    CLI 面此前不展示 detail——口令加密 PDF 的 `password_required` 诊断 flag
    对 CLI 用户不可见，而 CLI 恰是「用户自助定位」的主场景（QA M-3 实证缺口）。
    detail 是受控字段（file / exc / password_required），不含文档内容（NFR-20）。
    """
    payload = {"status": "error", "code": exc.code, "message": message}
    payload.update(extra)
    if getattr(exc, "detail", None):
        payload["detail"] = exc.detail
    return payload


# CH-441：doctor 自检的依赖清单 = pyproject 运行时依赖的导入名（单一口径，防漂移）。
# 实装只用 pdfplumber / python-docx / cryptography / Flask；
# pypdf、Jinja2、PyYAML、watchdog 从未被 doc2md 包导入（S1 走 pdfplumber，
# 审阅页与日报是内置 f-string 渲染，watch 走内置轮询 StabilityTracker）。
DEPENDENCY_MODULES = ("pdfplumber", "docx", "flask", "cryptography")


def _add_common(p: argparse.ArgumentParser) -> None:
    # CH-485（R9-OPS-4）：两层子命令都注册同名选项时，子 parser 的默认 None
    # 会覆盖父 parser 已解析的值——`backup --root /x restore ...` 实际 root=None。
    # SUPPRESS 让未显式给出的子选项不写入 namespace，父值因此得以保留。
    p.add_argument("--config", default=argparse.SUPPRESS,
                   help="doc2md.toml 路径（默认 ./ 或 $DOC2MD_HOME）")
    p.add_argument("--root", default=argparse.SUPPRESS,
                   help="工作根目录（覆盖 [paths].root）")


def _ensure_private_root(root: Path) -> None:
    """CH-485（R9-OPS-8）：纯 CLI 入口也落实 work/ 700（NFR-20）。

    原实现只有 serve 与备份快照路径 chmod；只用 convert/work/watch 的部署
    work/ 可能是 755，正文与 state.sqlite 对其他用户可读。非 POSIX 卷
    chmod 失败只告警，不阻断转换。
    """
    root.mkdir(parents=True, exist_ok=True)
    from .security.perms import ensure_private
    for warning in ensure_private(root, 0o700):
        print(f"warning: {warning}", file=sys.stderr)


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
    dl.add_argument("--keep", action="store_true",
                    help="保留演练沙箱目录（取证用；CH-476 起不受 tmp 周期清扫与 cron --daily 清理）")
    rs = bksub.add_parser("restore", help="从快照/加密容器恢复 state.sqlite（CH-483）")
    _add_common(rs)
    rs.add_argument("src", help="快照或 .enc 容器路径")
    rs.add_argument("--to", help="恢复目标（默认 <root>/state.sqlite）")
    rs.add_argument("--force", action="store_true",
                    help="目标已存在时先备份旧库再覆盖；不绕过完整性校验")
    rs.add_argument("--passphrase-env", default="DOC2MD_BACKUP_PASSPHRASE",
                    help="读取口令的环境变量名（默认 DOC2MD_BACKUP_PASSPHRASE）")

    cv = sub.add_parser("convert", help="转换文件 / 目录")
    _add_common(cv)
    cv.add_argument("path")
    cv.add_argument("--mode", choices=("auto", "single", "two_phase"))
    cv.add_argument("--out", help="产物 zip 输出目录（默认 [paths].out）")
    cv.add_argument("--json", action="store_true", help="只打印 JSON 摘要（脚本/CI 用）")
    cv.add_argument("--follow-symlinks", action="store_true")
    cv.add_argument("--workers", type=int,
                    help="批量进程数（CH-448）；缺省 = [perf].workers（0 自动 min(4, CPU)），1 = 串行")
    cv.add_argument("--force", action="store_true",
                    help="允许覆盖库内已有用户编辑（默认 E4091 拒收，产物保持可导出）")
    cv.add_argument("--skip-existing", action="store_true",
                    help="O-08（C-27）：内容哈希已入库即跳过（结果行 status=skipped，退出码不计失败）；"
                         "源文件改写后哈希变化，不会误跳过新内容")
    cv.add_argument("--llm-mode", choices=("offline", "server"),
                    help="LLM 补助实现（CH-456 / T2）：offline = 确定性占位（默认，零网络）；"
                         "server = 本地 llama-server（llm.bind:llm.port）")
    cv.add_argument("--llm-no-fallback", action="store_true",
                    help="server 模式服务不可用时明确失败（E5041 / 退出码 4），不回退 offline")
    cv.add_argument("--format", choices=("zip", "md", "both"), default="zip",
                    help="产物形态（CH-491）：zip = 现状（content.md + ir.json + report.html）；"
                         "md = 只写 Markdown 直出文件；both = 两者都写。"
                         "md 与 zip 内 content.md 逐字节相同")

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

    se = sub.add_parser("search", help="检索已入库文档（API /search 的 CLI 等价物，CH-491）")
    _add_common(se)
    se.add_argument("query", help="检索词（CJK 单字 + bigram / 非 CJK 原样段）")
    se.add_argument("--full", action="store_true",
                    help="走 blocks.text 全表 LIKE，覆盖 FTS 索引窗口外的块尾部")
    se.add_argument("--limit", type=int,
                    help="返回条数上限（默认 20，上限 500；超限截到 500 而非报错）")

    # O-07（C-26 / F-01）：docs 只读三件套——不起 serve 即可查/导已转内容，
    # 与 search 合成完整 CLI 读取面。三个子命令全部只读（零 DB 写入）。
    dc = sub.add_parser("docs", help="已入库文档的只读查询 / 导出（O-07）")
    _add_common(dc)
    dcsub = dc.add_subparsers(dest="dc_cmd", required=True)
    dcl = dcsub.add_parser("list", help="文档清单（分页信封 + 块计数）")
    _add_common(dcl)
    dcl.add_argument("--limit", type=int,
                     help=f"条数上限（默认 50，硬顶 {DOCS_MAX_ROWS}；超限截断不报错）")
    dcl.add_argument("--offset", type=int, default=0, help="偏移（默认 0）")
    dcl.add_argument("--status", help="按 docs.status 过滤（默认全部）")
    dcs = dcsub.add_parser("show", help="单篇元数据 + 块窗口（GET /docs/<id> 等价物）")
    _add_common(dcs)
    dcs.add_argument("doc_id", help="文档 id（内容哈希前 16 位十六进制）")
    dcs.add_argument("--limit", type=int,
                     help=f"块窗口大小（默认 20，硬顶 {DOCS_MAX_ROWS}）")
    dcs.add_argument("--offset", type=int, default=0, help="块窗口偏移（默认 0）")
    dce = dcsub.add_parser("export", help="导出 content.md（与 zip 内逐字节相同）+ manifest.json")
    _add_common(dce)
    dce.add_argument("doc_ids", nargs="*", metavar="doc_id",
                     help="要导出的 doc_id（可多个）；或 --all 全量")
    dce.add_argument("--all", action="store_true", help="导出库内全部文档")
    dce.add_argument("--out", help="导出目录（默认 [paths].out）")
    return ap


def _cfg(args: argparse.Namespace) -> dict:
    cfg = config.load(getattr(args, "config", None))
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


def _sendmsg_supported() -> bool:
    from .security.netguard import sendmsg_supported
    return sendmsg_supported()


def _watch_stable_seconds(cfg: dict) -> float:
    """watch 稳定窗口下限（CH-485 / R9-OPS-10）：0 或非法值不得让轮询自旋。"""
    try:
        v = float(config.get(cfg, "watch.stable_seconds", 5) or 0)
    except (TypeError, ValueError):
        v = 5.0
    return v if v > 0 else 0.1


# --- serve ------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if getattr(args, "sub", None) == "token":
        return _cmd_token(args, cfg)
    root = _root(cfg)
    _ensure_private_root(root)                  # M8 任务 6 / CH-485：work/ 权限 700
    from .api import create_app
    app = create_app(cfg, root, config_path=getattr(args, "config", None),
                     reload_from_files=True)
    host = args.host or str(config.get(cfg, "api.bind", "127.0.0.1"))
    # CH-473（R5-SEC-2 / D4 闭环）：`--host` 与配置同口径。原实现只校验 TOML 内的
    # api.bind（loopback 检查在 config.validate 里、对 load 的文件生效），一行
    # `serve --host 0.0.0.0` 即绕过「默认只监听回环」的离线单机姿态——CLI 面与
    # 配置面口径分裂。非 loopback 须显式 opt-in（api.allow_remote_bind）。
    if not config.is_loopback_bind(host) and not config.get(
            cfg, "api.allow_remote_bind", False):
        print(f"serve refused: host {host!r} 非本机回环（NFR-1 离线单机姿态）；"
              f"确需对外暴露请设 api.allow_remote_bind = true 后重试", file=sys.stderr)
        return EXIT_USAGE
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
    from .security.perms import ensure_private
    from .storage.db import Store
    root = _root(cfg)
    _ensure_private_root(root)
    store = Store(root / "state.sqlite")
    for warning in ensure_private(root / "state.sqlite", 0o600):
        print(f"warning: {warning}", file=sys.stderr)
    try:
        return _token_body(args, cfg, store, auth)
    finally:
        store.close()                    # CH-469（V-12）：命令面也要收尾连接


def _token_body(args, cfg, store, auth) -> int:
    if args.list_tokens:
        # CH-471 SVC-9：库里只存 token 哈希（明文仅签发时展示一次）。--list 输出哈希前缀，
        # 既够运维按 label/时间定位并吊销，也不再从 DB/终端泄露可用凭证。
        rows = store.q("SELECT token, role, label, created_at, revoked_at FROM tokens")
        print(_jline([{"token_sha256": r["token"][:16], "role": r["role"],
                       "label": r["label"], "created_at": r["created_at"],
                       "revoked_at": r["revoked_at"]} for r in rows], indent=2))
        return EXIT_OK
    if args.revoke:
        # CH-469（V-13）：`auth.revoke` 现在回报是否命中。原实现在这里同样
        # 无条件打印 revoked=true——吊销一个不存在的 token 也被报成成功。
        if not auth.revoke(store, args.revoke):
            print(_jline({"revoked": False, "code": "E4041",
                          "message": "unknown token"}), file=sys.stderr)
            return EXIT_BIZ
        print(_jline({"revoked": True, "effective_within_s": int(config.get(
            cfg, "api.revoke_effective_seconds", 5))}))
        return EXIT_OK
    allowed = config.get(cfg, "api.token_roles", auth.ROLES)
    if args.role not in tuple(allowed):
        # CH-469（V-7）：argparse 的 choices 挡不住「配置里收窄了可签发角色」这一路，
        # 直送 auth.issue 会抛 ValueError 崩栈。同 API 面收敛为参数错（退出码 2）。
        print(_jline({"code": "E4001",
                      "message": f"role {args.role} not in api.token_roles"}),
              file=sys.stderr)
        return EXIT_USAGE
    print(auth.issue(store, args.role, args.label, allowed=allowed))
    return EXIT_OK


# --- cron -------------------------------------------------------------------
def cmd_cron(args: argparse.Namespace) -> int:
    from . import cron
    cfg = _cfg(args)
    _ensure_private_root(_root(cfg))
    mode = "weekly" if args.weekly else "daily"
    rep = cron.run(cfg, _root(cfg), mode=mode, with_backup=not args.no_backup)
    print(_jline({"mode": rep.mode, "ok": rep.ok, "steps": rep.steps}, indent=2))
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


def cmd_backup_restore(args: argparse.Namespace) -> int:
    """离线恢复（CH-483 / R8-OPS-2）：默认拒绝覆盖，强制时先备份旧库。

    恢复前必须停止 serve / work；本命令不尝试探测在线进程（跨平台锁的成本
    高于收益，且真实停服流程属 B-10 真机演练）。
    """
    from .ops.backup import BackupBusy, BackupPlan, backup_lock
    cfg = _cfg(args)
    root = _root(cfg)
    dst = Path(args.to).expanduser() if args.to else root / "state.sqlite"
    plan = BackupPlan(
        local_dir=config.path_setting(cfg, "ops.backup_local_dir", root,
                                      str(root / "reports/backups")),
        external_dir=None,
        rotation_days=int(config.get(cfg, "ops.backup_rotation_days", 14)),
        work_root=root,
        work_dir_mode=str(config.get(cfg, "ops.work_dir_mode", "700")),
        cfg=cfg)
    phrase = os.environ.get(args.passphrase_env, "")
    try:
        with backup_lock(plan.local_dir):
            plan.restore_safe(Path(args.src).expanduser(), dst, phrase,
                              force=bool(args.force))
    except FileExistsError as exc:
        print(_jline({"code": "E4001", "message": str(exc)}), file=sys.stderr)
        return EXIT_USAGE
    except BackupBusy:
        print(_jline({"code": "E4001",
                      "message": "backup/restore already running"}), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:                    # noqa: BLE001 — CLI 统一退出码 4
        print(_jline({"code": "E5031",
                      "message": f"restore failed: {type(exc).__name__}"}),
              file=sys.stderr)
        return EXIT_BIZ
    # CH-485（R9-SVC-2）：恢复 DB 后补齐缺失产物。只有升级后写入过
    # gate_json 的文档可重建；旧文档没有完整块字段，保留“需重转”提示。
    rebuilt = 0
    rebuild_errors: list[str] = []
    try:
        from .artifacts import rebuild_artifact
        from .storage.db import Store
        st = Store(dst)
        try:
            out_dir = _out(cfg)
            for row in st.q("SELECT doc_id, gate_json FROM docs"):
                if (row["gate_json"] or "{}") in ("", "{}"):
                    continue
                if (out_dir / f"{row['doc_id']}.zip").exists():
                    continue
                try:
                    rebuild_artifact(st, row["doc_id"], cfg, root)
                    rebuilt += 1
                except Exception as exc:  # noqa: BLE001 — 恢复已成功，逐篇报告
                    rebuild_errors.append(f"{row['doc_id']}:{type(exc).__name__}")
        finally:
            st.close()
    except Exception as exc:                # noqa: BLE001 — 重建失败不翻转恢复结果
        rebuild_errors.append(f"init:{type(exc).__name__}")
    print(_jline({"restored": str(dst), "bytes": dst.stat().st_size,
                  "artifacts_rebuilt": rebuilt,
                  "artifact_errors": rebuild_errors[:5]}))
    return EXIT_OK


def cmd_backup(args: argparse.Namespace) -> int:
    if getattr(args, "bk_cmd", "") == "restore":
        return cmd_backup_restore(args)
    return cmd_backup_drill(args)


# --- convert ------------------------------------------------------------------
def _gate_summary(detail: dict) -> dict:
    """CLI 只回摘要（页级明细留在报告 / --json）；避免 stderr 被 20×N 字段刷满。"""
    return {"mode": detail.get("mode"), "sampled_pages": detail.get("sampled_pages"),
            "reasons": detail.get("reasons"), "stats": detail.get("stats")}


def _emit_md(zip_path: Path, out_dir: Path, md_name: str) -> Path:
    """CH-491（B-5）：从产物 zip 提取 content.md 直出。

    从 zip 原样读出再落盘，保证与 zip 内 content.md **逐字节相同**（含 front-matter），
    不重新序列化——重新拼装会因 json 缩进/键序漂移破坏等价性。
    写盘走「唯一临时文件 + fsync + os.replace」：中途失败只留 `.part`（随即清理），
    不会把目标 Markdown 覆盖成截断内容。
    """
    import os
    import uuid
    import zipfile

    from .artifacts import artifact_entry
    from .errors import E4041_NotFound
    with zipfile.ZipFile(zip_path) as z:
        entry = artifact_entry(z.namelist(), "content.md", zip_path.stem)
        if not entry:
            raise E4041_NotFound("artifact content.md missing")
        body = z.read(entry)
    md_path = out_dir / md_name
    tmp = out_dir / f".{md_name}.{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "xb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, md_path)
    finally:
        tmp.unlink(missing_ok=True)
    return md_path


def _md_name(res: dict) -> str:
    """md 直出文件名：优先消毒后的源文件名（用户可读），回退 doc_id。"""
    meta = res.get("meta") or {}
    name = str(meta.get("source_name") or "").strip()
    if name:
        from .security.names import assert_name_safe
        assert_name_safe(name)          # 与 package() 同一道闸，失败即拒收
        return name if name.lower().endswith(".md") else f"{name}.md"
    return f"{res.get('doc_id', 'doc')}.md"


def _apply_format(res: dict, out: Path, fmt: str) -> str:
    """按 --format 落 md 直出；返回 md 路径（未产出时为空串）。

    三态语义：`zip` = 只留 zip（现状）；`md` = 提取 md 后移除 zip（md 已含
    front-matter 与正文，zip 的 ir.json/report.html 属审阅面，md 模式不保留）；
    `both` = 两者都留。md 与 zip 内 content.md 逐字节相同。
    """
    if res.get("status") != "ok":
        return ""
    zp = res.get("zip") or ""
    if not zp:
        return ""
    if fmt == "zip":
        return ""
    try:
        md_path = _emit_md(Path(zp), out, _md_name(res))
    except Exception as exc:            # noqa: BLE001 — md 直出失败收敛为结果行错误
        # D1（QA-R2）：md 是用户显式要求的产物，落盘失败不能仍报 status=ok/rc=0。
        # 结果行翻成 error + E5031，zip 保留供调用方降级取件；退出码归业务失败。
        res["status"] = "error"
        res["code"] = "E5031"
        res["message"] = f"markdown emit failed: {type(exc).__name__}"
        return ""
    if fmt == "md":
        # md 已落盘且字节等价，移除 zip 避免「md 模式却留 zip」的语义混淆。
        # D12（QA-R2）：删掉 zip 后结果行不得再报 zip 路径，用 zip_removed 表达结果。
        try:
            Path(zp).unlink()
        except OSError:
            pass
        else:
            res.pop("zip", None)
            res["zip_removed"] = True
    return str(md_path)


def cmd_convert(args: argparse.Namespace) -> int:
    from .pipeline.batch import default_workers, result_row, run_batch, skip_existing_hit, skipped_row
    cfg = _cfg(args)
    _ensure_private_root(_root(cfg))
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
    # N-2 附带发现（QA-OPT-3）：--out 覆盖必须回灌 cfg——skip_existing_hit
    # 的产物回退判据（N-1）按 cfg paths.out 解析 zip 位置，不回灌则
    # 自定义 --out 的二转永远打不到历史 zip（串行与批量 worker 共用本
    # cfg，单点修复两路同时生效）。
    cfg.setdefault("paths", {})["out"] = str(out)
    rc = EXIT_OK
    if not files:
        print(_jline({"status": "empty", "path": str(src)}), file=sys.stderr)
        return rc
    # CH-448：目录投递走有界进程池（GIL 下线程池对解析无增益；实测口径见方案 第十二
    # 章吞吐表）；产物与串行逐字节一致（等价性由门禁把住）。`--workers 1` 退回串行。
    workers = args.workers if args.workers is not None else default_workers(cfg)
    if len(files) > 1 and workers > 1:
        results = run_batch(files, cfg=cfg, mode=args.mode, out_dir=out,
                            workers=workers, force=bool(args.force),
                            precheck_edits=True,
                            skip_existing=bool(args.skip_existing))
        for res in results:
            res["md"] = _apply_format(res, out, args.format)
            rc = max(rc, _report(res, as_json=args.json))
        return rc

    from .errors import Doc2mdError, E4221_GateReject
    from .pipeline.convert import convert

    # CH-491（B-6）：五阶段进度打点。仅串行路径接入（批量 worker 跨进程，回调
    # 不可 pickle）；`--json` 时静音——机器可读面不得混入人读进度行。
    quiet = bool(args.json)

    def _progress(stage: str, info: dict) -> None:
        if quiet:
            return
        bits = " ".join(f"{k}={v}" for k, v in info.items())
        print(f"[{stage}] {bits}", file=sys.stderr)

    for f in files:
        # O-08（C-27）：串行路径的跳过判定与批量工人共用 skip_existing_hit
        # 单点、跳过行共用 skipped_row 单点——两条出口同构（O-03 纪律）。
        if args.skip_existing:
            hit = skip_existing_hit(cfg, f)
            if hit is not None:
                doc_id, source_key, zp = hit
                rc = max(rc, _report(skipped_row(f, doc_id, source_key, zp),
                                     as_json=args.json))
                continue
        try:
            res = convert(f, cfg=cfg, mode=args.mode, out_dir=out,
                          force=bool(args.force), precheck_edits=True,
                          progress=_progress)
        except E4221_GateReject as exc:
            print(_jline({"file": f.name, "status": "rejected", "code": exc.code,
                          "gate": _gate_summary(exc.detail or {})}), file=sys.stderr)
            rc = max(rc, EXIT_GATE)
            continue
        except Doc2mdError as exc:
            print(_jline(_err_payload(exc, str(exc), file=f.name)), file=sys.stderr)
            rc = max(rc, EXIT_BIZ)
            continue
        # O-03（C-21）：与批量工人共用 result_row 单点——两条出口的共有字段
        # （file/status/doc_id/source_key/blocks/chars/zip/sharded/shard_stats/
        # meta）键集与取值类型逐字节同构，下游脚本一套解析即可。
        row = result_row(f, res)
        row["md"] = _apply_format(row, out, args.format)
        rc = max(rc, _report(row, as_json=args.json))
    return rc


def _report(res: dict, *, as_json: bool) -> int:
    """批量/串行共用的结果打印；返回该条对应的退出码贡献（调用方取 max = 最严重）。"""
    status = res.get("status")
    name = Path(str(res.get("file", ""))).name
    payload = {"file": name, "status": status, "doc_id": res.get("doc_id", ""),
               "blocks": res.get("blocks", 0), "chars": res.get("chars", 0)}
    if "zip" in res:
        payload["zip"] = res["zip"]
    if res.get("zip_removed"):
        payload["zip_removed"] = True
    if res.get("md"):
        payload["md"] = res["md"]
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
        line = (f"{name}: doc_id={payload['doc_id']} blocks={payload['blocks']} "
                f"chars={payload['chars']} sharded={res.get('sharded')} "
                f"shards={st.get('shards', 1)} stitched={st.get('stitched', 0)} "
                f"dropped_dupes={st.get('dropped_dupes', 0)} "
                f"zip={payload.get('zip', '')}")
        if res.get("zip_removed"):
            line += " zip_removed=true"
        if res.get("md"):
            line += f" md={res['md']}"
        print(line)
    elif status == "skipped":
        # O-08（C-27）：跳过是「本次未做转换」的正常终态——人读行明示 doc_id
        # 与既有产物路径，退出码贡献 0（不同步失败聚合）。
        print(f"{name}: skipped doc_id={payload['doc_id']} zip={payload.get('zip', '')}")
    else:
        print(_jline(payload), file=sys.stderr)
    if status in ("ok", "skipped"):
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
    _ensure_private_root(root)
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
                    print(_jline(_err_payload(exc, exc.message, file=f.name)),
                          file=sys.stderr)
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
def _watch_done_hit(store, path) -> tuple[str, str] | None:
    """O-08（F-07）：watch 重启判据——同路径 + 同内容哈希已有 done 任务行。

    返回 `(doc_id, source_key)`（命中）或 None（未命中 / 读不了）。

    判据落在库面而非新状态文件：`tasks.source_path + source_key` 两列即
    「这个路径的这份内容已提交且已完成」，不新增表、不引入第二份事实源。
    源文件被改写 → 内容哈希变 → 不命中，热更新语义完整保留（CH-464 的
    进程内判据管「同一进程不重复转」，本判据管「重启后不重转」，两层互补）。
    文件读不了 → None：让调用方走它自己的错误口径，不在这里吞异常。
    """
    from .ir.model import hash_of_path
    try:
        doc_id, source_key = hash_of_path(path)
    except OSError:
        return None
    row = store.q1(
        "SELECT job_id FROM tasks WHERE source_path=? AND source_key=?"
        " AND status='done' ORDER BY created_at DESC LIMIT 1",
        (str(path), source_key))
    return (doc_id, source_key) if row else None


def _dir_files(src: Path, cfg: dict, follow: bool) -> list[Path]:
    """一次性目录枚举：截断即 E4133（CH-469 / O-10）。

    半截清单在这里比在服务面更危险——`batch` 会把「没扫到」理解成「没有更多文件」，
    退出码 0、报告齐全，剩下的文件永远没人转。常驻 watch 不受此约束：它的下一轮
    本来就是补齐语义（见 security/watcher.poll_all）。
    """
    from .security.dirguard import scan_or_raise, walk_limits
    return [Path(f) for f in scan_or_raise(src, walk_limits(cfg, follow_symlinks=follow))
            if Path(f).suffix.lower() in (".pdf", ".docx", ".doc")]


def cmd_watch(args: argparse.Namespace) -> int:
    from . import worker
    from .pipeline.convert import convert
    from .scheduler import Scheduler
    from .security.dirguard import walk_limits
    from .security.watcher import StabilityTracker
    from .storage.db import Store
    from .storage.repo import save_ir
    cfg = _cfg(args)
    root = _root(cfg)
    _ensure_private_root(root)
    store = Store(root / "state.sqlite")

    def loop() -> int:
        sched = Scheduler(store)
        lim = walk_limits(cfg)
        stable_seconds = _watch_stable_seconds(cfg)
        tracker = StabilityTracker(stable_seconds=stable_seconds)
        interval = args.interval or tracker.stable_seconds
        async_queue = bool(config.get(cfg, "perf.queue_async", False))
        import time
        rc = EXIT_OK
        while True:
            try:
                ready = tracker.poll_all(args.dir, lim)
            except Doc2mdError as exc:
                print(_jline(_err_payload(exc, str(exc))), file=sys.stderr)
                if args.once:
                    # CH-492（QA 缺陷 1）：--once（cron 模式）下扫描级失败必须计入
                    # rc——原实现只打印不抬 rc，目录打错 / 超限 / 被删时退出码 0，
                    # cron/launchd 按退出码判成功即静默假成功（convert 同场景为
                    # EXIT_BIZ，对齐之）。常驻模式不抬 rc：下一轮轮询可能恢复，
                    # 且 rc 不在常驻路径返回（仅 --once 分支 return rc）。
                    rc = max(rc, EXIT_BIZ)
                ready = []
            for f in ready:
                p = Path(f)
                if p.suffix.lower() not in (".pdf", ".docx", ".doc"):
                    continue
                # O-08（F-07）：重启判据——同路径同内容已 done 即跳过（零转换）。
                # 反向断言基线：修复前每个新进程的首轮都会把目录内老文件全部
                # 重转换一遍（ StabilityTracker 是进程内登记簿，重启即失忆）。
                hit = _watch_done_hit(store, p)
                if hit is not None:
                    print(_jline({"watched": p.name, "status": "skipped",
                                  "doc_id": hit[0], "source_key": hit[1]}))
                    continue
                if async_queue:
                    # CH-449：低配机上 watch 目录批量落文件时，稳定即入队、由
                    # `doc2md work` 并发消费；不在轮询线程里做 CPU 重活
                    # （否则稳定窗口被计算拖长）。
                    try:
                        it = worker.enqueue(sched, p)
                        print(_jline(it))
                    except Doc2mdError as exc:
                        print(_jline(_err_payload(exc, exc.message,
                                                  watched=p.name)),
                              file=sys.stderr)
                        rc = max(rc, EXIT_BIZ)
                    continue
                try:
                    res = convert(p, cfg=cfg, out_dir=_out(cfg), precheck_edits=True)
                    save_ir(store, res.ir, meta=res.meta, gate=res.gate)
                    j = sched.submit(res.doc_id, res.source_key,
                                     source_path=str(p))
                    # CH-489：`submit` 可能附着到已有的 queued/running 作业
                    # （同 doc_id 的去重路径）。此时该作业的终态由它的消费者
                    # 决定，watch 不能替对端提前写 done（会把仍在跑的作业伪装
                    # 成已完成，并让后续消费者面对一个已终态的行）。
                    if not (j.attached or j.idem_hit):
                        sched.mark(j.job_id, "done")
                    print(_jline({"watched": p.name, "doc_id": res.doc_id,
                                  "job_id": j.job_id}))
                except E4221_GateReject as exc:
                    j = sched.submit_rejected(p.name, exc.code, exc.detail)
                    print(_jline({"watched": p.name, "status": "rejected",
                                  "code": exc.code}), file=sys.stderr)
                    # CH-469（V-12）：原为 `rc = EXIT_GATE`（赋值）。EXIT_GATE=3 小于
                    # EXIT_BIZ=4，于是一个后续拒收会把已判定的「业务失败」降级成 3，
                    # 脚本侧按退出码分流时会走错分支——与其它路径同口径取 max。
                    rc = max(rc, EXIT_GATE)
                except Doc2mdError as exc:
                    print(_jline(_err_payload(exc, exc.message,
                                              watched=p.name)), file=sys.stderr)
                    rc = max(rc, EXIT_BIZ)
            if args.once:
                return rc
            time.sleep(interval)

    # CH-469（V-12）：watch 常驻循环独占一条 thread-local 写连接，异常退出（Ctrl-C、
    # poll_all 抛出非 Doc2mdError）必须显式 close——原实现只在正常返回时靠进程结束兜底。
    try:
        return loop()
    finally:
        store.close()


# --- tokenizer ------------------------------------------------------------------
def cmd_tokenizer(args: argparse.Namespace) -> int:
    from .ops import gguf
    cfg = _cfg(args)
    if args.tz_cmd == "export":
        manifest_path = (Path(args.manifest).expanduser() if args.manifest else
                         config.path_setting(cfg, "chunks.manifest", _root(cfg),
                                             str(_root(cfg) / "tokenizer.manifest.json")))
        vocab = args.vocab or str(manifest_path.with_name("vocab.jsonl"))
        man = gguf.export_vocab(args.gguf, vocab)
        gguf.write_manifest(man, manifest_path)
        print(_jline({"manifest": str(manifest_path), "vocab": man["vocab_file"],
                      "n_vocab": man["n_vocab"],
                      "gguf_sha256_prefix": man["gguf_sha256_prefix"]}, indent=2))
        return EXIT_OK
    res = gguf.verify_manifest(args.manifest, args.gguf)
    print(_jline(res, indent=2))
    return EXIT_OK if all(res[k] for k in ("gguf_ok", "vocab_ok", "name_ok", "n_vocab_ok")) else EXIT_SYS


# --- search ---------------------------------------------------------------------
def cmd_search(args: argparse.Namespace) -> int:
    """CH-491（B-4）：CLI 检索入口——补齐 CLI/API 能力不对齐。

    `GET /search`（`api.py:949`）此前是 API 独占，离线单机用户不启 serve 就
    无法检索已入库文档。本命令复用 `storage/repo.py::search`，与 API 同一
    实现、同一默认口径（limit 默认 20、上限 500 截断不报错），结果集逐条相同。
    """
    from .storage.repo import search
    cfg = _cfg(args)
    # O-07：库连接与「no library」口径收敛到 _open_store 单点（此前与 docs 三
    # 命令各自内联，格式漂移过）；limit 走 _nonneg——help 早写明「上限 500
    # 截断不报错」，而负值经 SQLite `LIMIT -1`（无界）会绕过该口径。
    limit = _nonneg(args, "limit", 20)
    if limit is None:
        return EXIT_USAGE
    store = _open_store(cfg)
    try:
        result = search(store, args.query, limit, full=bool(args.full))
    finally:
        store.close()
    print(_jline(result, indent=2))
    return EXIT_OK


# --- docs（O-07：只读查询 / 导出）-----------------------------------------------
def _open_store(cfg: dict):
    """只读命令面共用的库连接；库不存在即 E4041（与 search 同口径，退出码 4）。

    单点收敛的原因：三个 docs 子命令 + search 四处的「no library」消息与
    退出码此前各自内联，漂移过格式（CH-491 起 search 先收敛过一次）。
    """
    from .storage.db import Store
    db = _root(cfg) / "state.sqlite"
    if not db.exists():
        raise E4041_NotFound(f"no library at {db}")
    return Store(db)


def _nonneg(args: argparse.Namespace, name: str, default: int) -> int | None:
    """--limit / --offset 收口（O-07）：负值打印 E4001 并返回 None（调用方退
    EXIT_USAGE——argparse 对 `--limit abc` 同样退 2，负值没理由按业务错 4；
    API `_window` 亦拒负）；超 DOCS_MAX_ROWS 截断不报错（search help 既定的
    「上限 500」口径）。必须挡在 SQL 之前：SQLite `LIMIT -1` 是无界的反直觉语义。
    """
    raw = getattr(args, name, None)
    if raw is None:
        return default
    if raw < 0:
        print(_jline({"code": "E4001", "message": f"{name} must be >= 0"}),
              file=sys.stderr)
        return None
    return min(raw, DOCS_MAX_ROWS)


def cmd_docs(args: argparse.Namespace) -> int:
    if args.dc_cmd == "list":
        return _cmd_docs_list(args)
    if args.dc_cmd == "show":
        return _cmd_docs_show(args)
    return _cmd_docs_export(args)


def _cmd_docs_list(args: argparse.Namespace) -> int:
    from .storage.repo import list_docs
    cfg = _cfg(args)
    limit = _nonneg(args, "limit", 50)
    offset = _nonneg(args, "offset", 0)
    if limit is None or offset is None:
        return EXIT_USAGE
    store = _open_store(cfg)
    try:
        result = list_docs(store, offset=offset, limit=limit, status=args.status)
    finally:
        store.close()
    print(_jline(result, indent=2))
    return EXIT_OK


def _cmd_docs_show(args: argparse.Namespace) -> int:
    from .storage.repo import count_blocks, get_doc, list_blocks
    cfg = _cfg(args)
    limit = _nonneg(args, "limit", 20)
    offset = _nonneg(args, "offset", 0)
    if limit is None or offset is None:
        return EXIT_USAGE
    store = _open_store(cfg)
    try:
        d = get_doc(store, args.doc_id)              # 不存在 → E4041（退出码 4）
        # 与 GET /docs/<id> 同一派生字段（O-05）：降维告警不再埋在内嵌 meta 里
        d["warnings"] = list((d.get("meta") or {}).get("warnings") or [])
        d["blocks"] = [b.to_dict() for b in
                       list_blocks(store, args.doc_id, offset=offset, limit=limit)]
        d["blocks_total"] = count_blocks(store, args.doc_id)
        d["blocks_offset"] = offset
        d["blocks_limit"] = limit
    finally:
        store.close()
    print(_jline(d, indent=2))
    return EXIT_OK


def _zip_content_md(zp: Path, doc_id: str) -> bytes:
    """从产物 zip 原样读出 content.md 字节（不重新序列化——重新拼装会因
    json 缩进/键序漂移破坏「与 zip 内逐字节相同」的契约，同 _emit_md 口径）。"""
    import zipfile

    from .artifacts import artifact_entry
    try:
        with zipfile.ZipFile(zp) as z:
            entry = artifact_entry(z.namelist(), "content.md", doc_id)
            if not entry:
                raise E4041_NotFound("artifact content.md missing")
            return z.read(entry)
    except zipfile.BadZipFile as exc:
        raise E5031_StorageError("artifact unreadable",
                                 detail={"reason": type(exc).__name__}) from exc


def _export_md_name(source_name: str, doc_id: str, taken: dict) -> str:
    """导出文件名：消毒后的源文件名（用户可读）；同批冲突按 F-14 既定口径
    追加 `-<doc_id 前 8>`，再冲突加长到 16，最后回退全 doc_id（PK 唯一）。

    `taken` 为「本批已占名 → doc_id」映射：只在不同 doc_id 争同名时加后缀，
    同一 doc_id 重复导出走覆盖语义（内容寻址，重导结果逐字节相同）。
    """
    from .security.names import assert_name_safe
    base = (source_name or "").strip()
    if base:
        assert_name_safe(base)                      # 与 package() 同一道闸
        name = base if base.lower().endswith(".md") else f"{base}.md"
    else:
        name = f"{doc_id}.md"
    if taken.get(name) in (None, doc_id):
        taken[name] = doc_id
        return name
    stem = name[:-3] if name.lower().endswith(".md") else name
    for n in (8, 16, len(doc_id)):
        cand = f"{stem}-{doc_id[:n]}.md"
        if taken.get(cand) in (None, doc_id):
            taken[cand] = doc_id
            return cand
    name = f"{doc_id}.md"       # 理论不可达兜底：doc_id 全量唯一（PK）
    taken[name] = doc_id
    return name


def _atomic_write(path: Path, data: bytes) -> None:
    """唯一临时文件 + fsync + os.replace：中途失败只留 `.part`（随即清理），
    不会把目标写成截断内容（CH-491 md 直出同口径）。"""
    import os
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _cmd_docs_export(args: argparse.Namespace) -> int:
    """导出已入库文档的 Markdown（O-07 / F-01）。

    字节口径：从产物 zip 原样提取 content.md，与 zip 内逐字节相同；zip 缺失
    时按 CH-489 口径（DB 是产物事实源）先重建再读。另写 manifest.json 对账
    （doc_id ↔ 文件 ↔ sha256），供导出后校验与脚本消费。默认导出目录为
    `[paths].out`（与 convert 产物同目录）；zip 始终从库里定位，`--out` 只
    决定 md/manifest 落点。
    """
    import hashlib

    from .artifacts import rebuild_artifact
    from .security.names import assert_name_safe
    from .storage.repo import get_doc, list_docs
    cfg = _cfg(args)
    root = _root(cfg)
    out_dir = Path(args.out).expanduser() if args.out else _out(cfg)
    if args.all and args.doc_ids:
        print(_jline({"code": "E4001",
                      "message": "doc_ids and --all are mutually exclusive"}),
              file=sys.stderr)
        return EXIT_USAGE
    if not args.all and not args.doc_ids:
        print(_jline({"code": "E4001",
                      "message": "give doc_ids or --all"}), file=sys.stderr)
        return EXIT_USAGE
    rc = EXIT_OK
    store = _open_store(cfg)
    try:
        if args.all:
            doc_ids = [r["doc_id"] for r in
                       list_docs(store, limit=None)["docs"]]
        else:
            doc_ids = list(args.doc_ids)
        manifest: list[dict] = []
        taken: dict = {}
        for doc_id in doc_ids:
            try:
                # 消毒闸先行：doc_id 进路径拼 `out_dir/<doc_id>.zip` 之前先过
                # 同一道名字断言（FR-127），路径穿越形态在这里就拒。
                assert_name_safe(doc_id)
                row = get_doc(store, doc_id)        # 不存在 → E4041
                zp = _out(cfg) / f"{doc_id}.zip"
                if not zp.exists():
                    try:
                        rebuild_artifact(store, doc_id, cfg, root)
                    except Doc2mdError:
                        raise
                    except Exception as exc:       # noqa: BLE001 — 统一 E5031 契约
                        raise E5031_StorageError(
                            "artifact rebuild failed",
                            detail={"reason": type(exc).__name__}) from exc
                    if not zp.exists():
                        raise E4041_NotFound("artifact not found")
                body = _zip_content_md(zp, doc_id)
                name = _export_md_name(row.get("source_name") or "", doc_id, taken)
                _atomic_write(out_dir / name, body)
                manifest.append({"doc_id": doc_id, "file": name,
                                 "bytes": len(body),
                                 "sha256": hashlib.sha256(body).hexdigest()})
            except Doc2mdError as exc:
                # 逐篇失败不中断整批（与 convert 多文件口径一致）：其余文档
                # 照常导出，失败篇只在 stderr 留受控 payload（NFR-20）。
                print(_jline(_err_payload(exc, str(exc), doc_id=doc_id)),
                      file=sys.stderr)
                rc = max(rc, EXIT_BIZ)
        # manifest 无条件写（含 count=0）：一次导出 run 的对账面，「实际导出
        # 几篇」由 count 如实反映——全部失败时也留一个诚实的空对账，而不是
        # 让上一批的 manifest 冒充本批结果。
        _atomic_write(out_dir / "manifest.json",
                      (_jline({"count": len(manifest), "docs": manifest},
                              indent=2) + "\n").encode("utf-8"))
    finally:
        store.close()
    return rc


# --- doctor ---------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    from .security.perms import is_private
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
        # Windows 的 mode 位不代表 ACL；单列真实私有性，避免「0o777 看着吓人、
        # 实际 ACL 已收紧」或反过来的误读。
        "root_private": is_private(root) if root.exists() else None,
        "tokenizer": config.get(cfg, "chunks.tokenizer"),
        "s1_5_sample_pages": config.get(cfg, "s1_5.sample_pages"),
        "shard_mode": config.get(cfg, "s2.mode", "auto"),
        "max_pages_per_shard": config.get(cfg, "s2.max_pages_per_shard"),
        "op_whitelist": config.get(cfg, "llm.op_whitelist"),
        "llm_mode": config.get(cfg, "llm.mode", "offline"),
        "llm_fallback": config.get(cfg, "llm.fallback", True),
        "llm_endpoint": f"{config.get(cfg, 'llm.bind')}:{config.get(cfg, 'llm.port')}",
        "llm_vendor_models": _vendor_models(cfg),
        "config_unknown_keys": config.last_unknown_keys(),
        "backup_external_dir": config.get(cfg, "ops.backup_external_dir", ""),
        "backup_passphrase_env_set": bool(os.environ.get("DOC2MD_BACKUP_PASSPHRASE")),
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
        # CH-464：FR-107/108 渲染档位与 watch 稳定窗口进自检面——配置拼错
        # （如 anchor 静默回退语义）在 doctor 即可见，不进运行期静默失效
        "s4_link_internal": config.get(cfg, "s4.link_internal", "plain"),
        "s4_preserve_strike": config.get(cfg, "s4.preserve_marks_strike", False),
        "watch_stable_seconds": config.get(cfg, "watch.stable_seconds", 5),
        # CH-485（R9-OPS-3）：Windows 无 sendmsg，离线守护需显式展示该平台差异。
        "offline_sendmsg": "yes" if _sendmsg_supported() else "no",
    }
    # CH-441：清单一律与 pyproject 声明的运行时依赖一致（pypdf 实未使用，列出必误报 MISSING）
    for mod in DEPENDENCY_MODULES:
        try:
            __import__(mod)
            checks[f"dep_{mod}"] = "ok"
        except Exception as exc:             # noqa: BLE001 — 自检用途
            checks[f"dep_{mod}"] = f"MISSING ({type(exc).__name__})"
    # CH-456 / T2：server 模式下探测 llama-server /health（offline 模式不探测，零网络）
    llm_server_mode = str(config.get(cfg, "llm.mode", "offline")) == "server"
    if llm_server_mode:
        from .llm import LlmClient
        ok, why = LlmClient.from_cfg(cfg).probe()
        checks["llm_server"] = "reachable" if ok else f"unreachable ({why})"
    # CH-487：.doc 转换后端可见性（auto 不可用不判失败，显式指定才判）
    from .pipeline.legacy_doc import available_backend
    doc_backend_req = str(config.get(cfg, "s1.legacy_doc_backend", "auto"))
    doc_backend = available_backend(doc_backend_req)
    checks["legacy_doc_backend"] = doc_backend or (
        "disabled" if doc_backend_req == "none" else "unavailable")
    print(_jline(checks, indent=2))
    # CH-473（CFG-2，LOW）：配置语义非法（冻结键校验不过）时自检必须非零退出。
    # 原实现只把 MISSING 依赖算失败——`config_frozen_keys: invalid` 照样退 0，
    # 「doctor 全绿」与「配置实际不可用」并存，自检面失去门禁意义。配置错按
    # O-15 口径映射为用法退出码（EXIT_USAGE），与加载期配置错同族。
    if not frozen_ok:
        return EXIT_USAGE
    # CH-475（R6-OPS-3）：自检失败口径补齐。原判定只看 `MISSING` 依赖——
    # ① server 模式 `llm_server: unreachable`、② 磁盘不足（`free_mb < min_free_mb`，
    # 含探测失败的 `-1`）都 exit 0，用户误判「环境健康」，转换时才暴雷，与「低配机友好、
    # 早失败」口径相悖。offline 模式不探测 llm_server（本就离线），不算失败。
    free_mb = checks["free_mb"]
    min_free_mb = int(checks["min_free_mb"] or 0)
    unhealthy = (
        any(str(v).startswith("MISSING") for v in checks.values())
        or (llm_server_mode and str(checks.get("llm_server", "")).startswith("unreachable"))
        or (doc_backend_req in ("libreoffice", "word") and doc_backend is None)
        or free_mb < 0 or free_mb < min_free_mb
    )
    return EXIT_SYS if unhealthy else EXIT_OK


def _vendor_models(cfg) -> list[str]:
    """paths.vendor_models 下的 gguf 资产清单（T2 消费点：定位本地模型文件）。"""
    import glob
    base = config.path_setting(cfg, "paths.vendor_models", _root(cfg),
                               str(_root(cfg) / "vendor/models/llm"))
    return sorted(Path(p).name for p in glob.glob(str(base / "*.gguf")))


COMMANDS = {"serve": cmd_serve, "cron": cmd_cron, "convert": cmd_convert,
            "watch": cmd_watch, "work": cmd_work,
            "tokenizer": cmd_tokenizer, "doctor": cmd_doctor,
            "backup": cmd_backup, "search": cmd_search, "docs": cmd_docs}


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
    except config.ConfigError as exc:   # CH-469（O-15）：配置错 = 用法错误，不是系统故障
        # 原实现落进兜底 `except Exception` → E5000 / EXIT_SYS，脚本按码分流时
        # 「doc2md.toml 写错了」和「解析栈炸了」混在同一个退出码里。
        print(_jline({"code": "E4001", "message": f"config error: {exc}"}), file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_SYS
    except OSError as exc:             # ENOSPC / EACCES / ELOOP：低配机常见，栈回溯不是契约输出
        print(_jline({"code": "E5031", "message": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        return EXIT_SYS
    except Exception as exc:           # noqa: BLE001 — CH-469（V-12）：兜底收口
        # 未归类的第三方异常（pdfplumber/pdfminer 解析栈、docx 结构错误）原样冒泡时
        # Python 回显栈回溯并退出码 1——1 不在本 CLI 的退出码契约里，脚本按码分流会
        # 落进「未知」分支。这里统一收口为 EXIT_SYS；消息只带异常类名，
        # 不带 str(exc)（解析类异常的消息常夹带页正文，NFR-20）。
        print(_jline({"code": "E5000", "message": f"unexpected: {type(exc).__name__}"}),
              file=sys.stderr)
        return EXIT_SYS


if __name__ == "__main__":
    raise SystemExit(main())
