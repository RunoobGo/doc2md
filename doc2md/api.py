"""API —— v2.4 增补与修订（方案 附录 C；上游锚点 v2.1 §7.2 / v2.4 §3.6）。

新增：DELETE /docs/{id}?confirm=true、DELETE /sources/{key}?confirm=true
修订：PATCH 必填 base_revision + 425/E4251 + current_revision；
     POST /jobs 目录扫描统一走 FR-129（超限 413/E4133）；
     token 三角色 + --role；GET /settings reader 可见、PUT 需 admin。
事件：全局总线 /events 供前端；per-job 端点仅 CLI/脚本 UA（CH-401 / FR-125，
咨询性约定而非安全边界——UA 可伪造，鉴权由 need("read") 承担；SVC-9）。
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from . import auth, config
from .cron import sweep_tmp
from .edits import patch_block, rollback
from .errors import (
    ApiError,
    Doc2mdError,
    E4001_BadRequest,
    E4031_Forbidden,
    E4041_NotFound,
    E4091_DocOverwrite,
    E5031_StorageError,
)
from .ops.logging import RotatingJsonlWriter, scrub_record
from .pipeline.convert import convert
from .scheduler import Scheduler
from .security.dirguard import scan_or_raise, walk_limits
from .storage.db import Store, now_iso
from .storage.repo import (
    count_blocks,
    delete_doc,
    delete_source,
    get_doc,
    list_blocks,
    save_ir,
    search,
)

SCRIPT_UA_HINTS = ("python-requests", "curl/", "doc2md-cli", "httpx", "go-http-client")


def create_app(cfg: dict | None = None, root: Path | None = None):
    from flask import Flask, Response, jsonify, request

    cfg = cfg or config.load()
    root = Path(root) if root else config.path_setting(cfg, "paths.root", ".")
    root.mkdir(parents=True, exist_ok=True)
    override = root / "settings_override.json"
    # CH-469：baseline 必须在叠加 override **之前**取快照。否则「文件里已有的键」
    # 会同时出现在 baseline 与 merged 里，写回时的稀疏差量把它们当成未改动丢掉，
    # 多次 PUT 的净效果仍会回退（本项要修的就是这类丢失）。
    cfg_baseline = copy.deepcopy(cfg)
    if override.is_file():
        _deep(cfg, json.loads(override.read_text(encoding="utf-8")))
    store = Store(root / "state.sqlite")
    sched = Scheduler(store)
    # NFR-19：轮转由服务内置执行（禁止依赖 newsyslog/cron）——服务自己持有写入器；
    # 方案 6.5 同时要求「服务启动时清理 work/tmp 残留」，与 cron --daily 共用 sweep_tmp。
    logs = config.path_setting(cfg, "paths.logs", root, str(root / "logs"))
    writer = RotatingJsonlWriter(
        logs, stem="app",
        rotate_mb=float(config.get(cfg, "logs.rotate_mb", 50)),
        rotate_keep=int(config.get(cfg, "logs.rotate_keep", 5)))
    tmp_cleaned = sweep_tmp(cfg, root)
    writer.write({"code": "SERVICE_START", "root": str(root), "tmp_cleaned": tmp_cleaned,
                  "rotate_mb": writer.rotate_mb, "keep": writer.rotate_keep})
    writer.flush()
    app = Flask("doc2md")
    # CH-469：配置热更改为「copy-on-write + 锁」。原实现 `merged = dict(cfg)` 是浅拷贝，
    # `_deep` 就地改写共享的嵌套 dict，而并发请求线程正在读 cur() → 读到半改状态。
    # baseline 保存未叠加 override 的配置，用于把 override 文件算成「稀疏差量」
    # （只记真正被改过的键，不让全量默认值冻结进去、挡住日后升级）。
    app.config["doc2md"] = {"cfg": cfg, "store": store, "root": root, "sched": sched,
                            "writer": writer, "cfg_lock": threading.Lock(),
                            "cfg_baseline": cfg_baseline}

    def cur() -> dict:
        return app.config["doc2md"]["cfg"]

    def logw() -> RotatingJsonlWriter:
        return app.config["doc2md"]["writer"]

    def tok() -> str | None:
        return request.headers.get("X-Doc2md-Token") or (
            request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or None)

    def need(action: str) -> str:
        return auth.check(store, tok(), action)

    def body() -> dict:
        return request.get_json(silent=True) or {}

    def err(exc: Doc2mdError):
        return jsonify(exc.to_body()), exc.http

    def _window() -> tuple[int, int | None]:
        """分页窗口解析（CH-469，V-5）。

        口径：**不给 `limit`（或 `limit=0`）= 取全部**，保持既有「一次拉全量」契约
        不漂移；给了 `limit` 才走 SQL 窗口。非数字 → 400/E4001（与 `_int_arg` 同口径，
        不让参数错误逃逸成 500）。
        """
        offset = _int_arg("offset", 0)
        limit = _int_arg("limit", 0)
        if offset < 0 or limit < 0:
            raise E4001_BadRequest("offset/limit must be >= 0")
        return offset, (limit or None)

    def _int_arg(name: str, default: int) -> int:
        """查询参数整数解析：非数字走 400/E4001（CH-464，原裸 int() 逃逸 500 HTML）。"""
        raw = request.args.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise E4001_BadRequest(f"{name} must be an integer") from None

    @app.errorhandler(ApiError)
    def _api_err(e):
        return err(e)

    @app.errorhandler(Doc2mdError)
    def _internal_err(e):
        """5xx 内部错误同样以 {code,message,detail} 契约返回（CH-440 依赖）。"""
        return err(e)

    @app.errorhandler(404)
    def _404(e):
        return err(E4041_NotFound("no such route"))

    @app.errorhandler(Exception)
    def _unhandled(e):
        """SVC-2（CH-471）：未归类异常一律走 {code,message,detail} JSON 契约。

        原实现只注册 ApiError/Doc2mdError/404 三个处理器；任何其它异常
        （BadZipFile / IndexError / 存储抖动）逃逸成 Werkzeug 默认 **HTML 500**，
        违反附录 C「响应错误一律走该契约，5xx 同样」。路由级 HTTPException
        （405/415…）原样透传保持 Flask 默认；其余转 E5000。
        以 int `.code`（HTTP 状态）判别 HTTPException，避免为一次 isinstance
        引入 werkzeug 依赖（本包 code 恒为字符串 "E4001" 式，不冲突）。
        NFR-20：日志只记异常类名，不落消息正文/请求体。
        """
        code = getattr(e, "code", None)
        if isinstance(code, int) and 100 <= code < 600 and hasattr(e, "get_response"):
            return e
        logw().write({"code": "E_UNHANDLED", "exc": type(e).__name__})
        return err(Doc2mdError("internal error"))

    # --- 作业 ---------------------------------------------------------------
    @app.post("/jobs")
    def post_job():
        need("submit_job")
        b = body()
        path = b.get("path", "")
        idem = request.headers.get("Idempotency-Key") or b.get("idempotency_key")
        # CH-471（STOR-5）：重复转换默认护编辑，force=true 才允许覆盖已有编辑
        force = bool(b.get("force"))
        if not path:
            raise E4001_BadRequest("path required")
        p = Path(path)
        files: list[Path] = []
        if p.is_dir():
            lim = walk_limits(cur())
            # CH-469（O-10）：走 scan_or_raise——目录没扫完（超时）必须 413/E4133，
            # 不能把半截清单当完整结果入队（用户视角是「目录里明明有，队列里没有」）。
            files = [Path(f) for f in scan_or_raise(p, lim)]
        elif p.is_file():
            files = [p]
        else:
            raise E4041_NotFound(f"path not found: {path}")

        # CH-459（A-10）：目录投递时按文件派生幂等键——原实现 N 个文件共用请求里的
        # 同一个键，第 2..N 个全部命中第 1 个作业而静默消失（同步/异步两条路径同病）。
        # 单文件投递维持原样（键字符串不变），多文件才派生。
        # CH-464：派生基准从 basename 改为**相对投递根的路径**——basename 在
        # 不同子目录同名时碰撞（a/report.pdf 与 b/report.pdf 得同键，第二个文件
        # 异步路径命中附着、静默丢失；同步路径覆盖第一个 job 的 detail）。
        multi = len(files) > 1
        base = p if p.is_dir() else p.parent

        def _idem_for(f: Path):
            if not (idem and multi):
                return idem
            try:
                rel = f.relative_to(base).as_posix()
            except ValueError:
                rel = f.name
            return f"{idem}#{rel}"

        # CH-449：[perf].queue_async = true → 只入队、立即 202 返回，由 `doc2md work` 消费。
        # 低配机上「一次丢一个目录」不应把 HTTP 线程占住（也不应同时起步 N 个解析进程）。
        # 默认 false = 同步转换，既有红线契约（方案 5.4：jobs[].doc_id 立即可用）不变。
        if bool(config.get(cur(), "perf.queue_async", False)):
            from .pipeline.s1_parse import sniff
            from .worker import enqueue
            # 入队前整体预检 magic：异步路径必须**要么全入队要么全不入**。
            # 先嗅探再入队（只读 8 字节），否则目录里混一个坏文件就会留下
            # 「前半截已入队、请求整体 4xx」的半成品队列（同步路径由 convert()
            # 逐文件失败，不存在这个中间态）。
            for f in files:
                if f.is_file():
                    sniff(f)
            mode = b.get("mode")
            out = [enqueue(sched, f, mode=mode, idem_key=_idem_for(f))
                   for f in files if f.is_file()]
            for it in out:
                if not it["attached"]:
                    _emit(store, "job_queued", it["doc_id"], it["job_id"],
                          {"source": "api"}, logw())
            return jsonify({"jobs": out, "mode": "queued",
                            "consume_with": "doc2md work"}), 202

        out = []

        def _finish_one(f, ir, doc_id, source_key):
            j = sched.submit(doc_id, source_key, idem_key=_idem_for(f))
            # CH-459（A-4）：save_ir / mark(done) / 事件三写合并为单事务
            # （原 5 次独立提交，WAL + synchronous=FULL 每次 fsync）。
            # CH-471（STOR-5）：库内已有编辑时 save_ir 抛 E4091，整笔事务回滚
            # （job 不标 done、编辑原样保留），本文件记为 conflict 后继续处理其余。
            try:
                with store.tx() as tx:
                    save_ir(store, ir, tx=tx, force=force)
                    sched.mark(j.job_id, "done", tx=tx)
                    _emit(store, "job_done", doc_id, j.job_id,
                          {"blocks": len(ir.blocks)}, logw(), tx=tx)
            except E4091_DocOverwrite as e:
                sched.mark(j.job_id, "rejected", e.code,
                           {"reason": "edited_blocks_present",
                            "edited_blocks": e.detail.get("edited_blocks")})
                _emit(store, "job_rejected", doc_id, j.job_id,
                      {"code": e.code, "source": "api"}, logw())
                out.append({"file": f.name, "job_id": j.job_id, "doc_id": doc_id,
                            "status": "conflict", "code": e.code,
                            "edited_blocks": e.detail.get("edited_blocks")})
                return
            out.append({"file": f.name, "job_id": j.job_id, "doc_id": doc_id,
                        "attached": j.attached, "idempotency_hit": j.idem_hit,
                        "sharded": ir.sharded, "shard_count": ir.shard_count})

        if not files:
            return jsonify({"jobs": out}), 202

        # SVC-3（CH-471）：多文件目录原为「一个请求线程内串行 convert 全部」，
        # 吞吐随文件数线性恶化（正是 queue_async 要避免、但它默认关）。大批量改走
        # run_batch 进程池（与 CLI/worker 同一吞吐层，产物口径逐字节一致）。但进程池
        # 按请求新建：小批次建池 + 子进程预热 pdfplumber 的成本高于收益，且长驻父进程
        # 里偶发 worker 失败（本机实测）→ 小/中批仍走进程内串行：确定、无池抖动，
        # 并保留「错误按 HTTP 码传播」既有契约（413/E4131、415/E4151…）与即时 doc_id。
        # 并行阈值可调（perf.sync_min_parallel，见决策/登记 B）。
        parallel_min = max(2, int(config.get(cur(), "perf.sync_min_parallel", 8) or 0))
        if len(files) >= parallel_min:
            from .pipeline.batch import default_workers, run_batch
            from .worker import _read_ir
            by_file = {r["file"]: r for r in run_batch(
                files, cfg=cur(), out_dir=root / "out",
                workers=default_workers(cfg))}
            for f in files:
                r = by_file.get(str(f)) or {"status": "error", "code": "E5000"}
                if r["status"] == "rejected":
                    j = sched.submit_rejected(f.name, r.get("code", "E4221"),
                                              {"reason": r.get("message", "")},
                                              idem_key=_idem_for(f))
                    out.append({"file": f.name, "job_id": j.job_id,
                                "status": "rejected", "code": r.get("code", "E4221")})
                elif r["status"] != "ok":
                    out.append({"file": f.name, "status": "error",
                                "code": r.get("code", "E5000")})
                else:
                    ir = _read_ir(r.get("zip", ""))
                    if ir is None:
                        out.append({"file": f.name, "doc_id": r.get("doc_id", ""),
                                    "status": "error", "code": "E5031"})
                        continue
                    _finish_one(f, ir, r.get("doc_id", ""), r.get("source_key", ""))
            return jsonify({"jobs": out}), 202

        for f in files:
            try:
                res = convert(f, cfg=cur(), out_dir=root / "out")
            except Doc2mdError as e:
                if e.code == "E4221":
                    j = sched.submit_rejected(f.name, e.code, e.detail,
                                              idem_key=_idem_for(f))
                    out.append({"file": f.name, "job_id": j.job_id,
                                "status": "rejected", "code": e.code})
                    continue
                # CH-471（SVC-8）：多文件同步批次中途硬错误（413/E4131、415/E4151…）
                # 原本 `raise` → 请求整体 4xx，但前面 `_finish_one` 已提交落库的文件
                # 留在库里 → 客户端只拿到错误、不知哪些文件已成功（半成品批次）。
                # 多文件改记本文件 error 后继续（不建 job 行），与 run_batch 并行路径
                # 同口径（每文件状态进 out、整体 202）；单文件仍按 HTTP 码传播，
                # 保留 413/415 契约（见 test_perf_lowend 的 disk-short / magic 用例）。
                # 选「逐文件续」而非「回滚整批」：已提交的转换不重做（性能优先），
                # 且批内每个文件都有明确终态（功能全面）。
                if len(files) > 1:
                    out.append({"file": f.name, "status": "error", "code": e.code})
                    continue
                raise
            _finish_one(f, res.ir, res.doc_id, res.source_key)
        return jsonify({"jobs": out}), 202

    @app.get("/jobs")
    def list_jobs():
        """队列可观测（CH-449）：按状态计数 + 队头明细，不含正文（NFR-20）。"""
        need("read")
        status = request.args.get("status")
        # CH-471（SVC-8）：与 `_window` 同口径——非数字走 400/E4001，负值拒绝。
        # 原裸 `int()` 无负值保护 → `?limit=-1` 拼进 SQL 得 `LIMIT -1`（SQLite 语义
        # 是「全部」），既绕过队列分页可观测上限，又与 `_window` 的拒负不一致。
        limit = _int_arg("limit", 50)
        if limit < 0:
            raise E4001_BadRequest("limit must be >= 0")
        sql = ("SELECT job_id, doc_id, source_key, status, created_at, started_at,"
               " finished_at, error_code, detail_json, source_path, mode FROM tasks")
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = [dict(r) for r in store.q(sql, args + (limit,))]
        for r in rows:
            try:
                r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except (TypeError, ValueError):
                r["detail"] = {}
        counts = {r["status"]: r["c"] for r in store.q(
            "SELECT status, COUNT(*) c FROM tasks GROUP BY status")}
        return jsonify({"tasks": rows, "counts": counts})

    # --- 文档 ---------------------------------------------------------------
    @app.get("/docs/<doc_id>")
    def get_document(doc_id):
        need("read")
        d = get_doc(store, doc_id)
        # CH-469（V-5）：分片文档可达数千块，原实现无条件全量物化 + 序列化，
        # 首个响应就能到 MB 级。`?offset=&limit=` 走 SQL 窗口；不带参数仍取全部。
        offset, limit = _window()
        blocks = list_blocks(store, doc_id, offset=offset, limit=limit)
        d["blocks"] = [b.to_dict() for b in blocks]
        d["blocks_total"] = count_blocks(store, doc_id)
        d["blocks_offset"] = offset
        return jsonify(d)

    @app.get("/docs/<doc_id>/markdown")
    def get_markdown(doc_id):
        need("export")
        get_doc(store, doc_id)
        zp = root / "out" / f"{doc_id}.zip"
        if not zp.exists():
            raise E4041_NotFound("artifact not found")
        import zipfile
        try:
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.endswith("content.md")]
                if not names:
                    raise E4041_NotFound("artifact content.md missing")
                return Response(z.read(names[0]).decode("utf-8"),
                                mimetype="text/markdown")
        except zipfile.BadZipFile as exc:
            raise E5031_StorageError("artifact unreadable",
                                     detail={"reason": type(exc).__name__}) from exc

    @app.patch("/docs/<doc_id>/blocks/<block_id>")
    def patch(doc_id, block_id):
        need("edit")
        b = body()
        res = patch_block(store, doc_id, block_id, b, b.get("as", tok() or "anon")[:12],
                          require_base_revision=bool(
                              config.get(cur(), "api.base_revision_required", True)))
        _emit(store, "text_edited", doc_id, None, {"block_id": block_id,
                                                   "revision": res["revision"]}, logw())
        return jsonify(res)

    @app.post("/docs/<doc_id>/blocks/<block_id>/rollback")
    def roll(doc_id, block_id):
        need("rollback")
        b = body()
        # CH-459（A-15）：to_revision 非数字（或缺字段 / None）时原 int() 直转
        # 逃逸成 500——参数校验收敛 400/E4001，与其它输入判定同口径。
        try:
            to_rev = int(b["to_revision"])
        except (KeyError, TypeError, ValueError):
            raise E4001_BadRequest("to_revision must be an integer",
                                   detail={"field": "to_revision"})
        actor = b.get("as") or tok() or "anon"
        return jsonify(rollback(store, doc_id, block_id, to_rev, str(actor)[:12]))

    @app.delete("/docs/<doc_id>")
    def del_doc(doc_id):
        need("delete")
        if request.args.get("confirm") != "true":
            raise E4001_BadRequest("confirm=true required for destructive delete",
                                 detail={"rule": "FR-128", "requires": "confirm=true"})
        return jsonify(delete_doc(store, doc_id, tok() or "admin", root))

    @app.delete("/sources/<source_key>")
    def del_src(source_key):
        need("delete")
        if request.args.get("confirm") != "true":
            raise E4001_BadRequest("confirm=true required for destructive delete",
                                 detail={"rule": "FR-128", "requires": "confirm=true"})
        return jsonify(delete_source(store, source_key, tok() or "admin", root))

    @app.get("/docs/<doc_id>/audit")
    def doc_audit(doc_id):
        """编辑审计流水（含 before/after 正文）→ 需 editor 及以上，不进 reader。

        CH-469（V-5）：审计每块每次编辑一行，长期跑的库能积累到数万行且**每行带
        前后两份正文**——全量返回是这套 API 里最重的一次序列化。`?offset=&limit=`
        走 SQL 窗口并回 `total`（不带 limit 仍取全部，契约不漂移）。
        """
        need("edit")
        get_doc(store, doc_id)
        offset, limit = _window()
        cond = "doc_id=?"
        args: tuple = (doc_id,)
        block_id = request.args.get("block_id")
        if block_id:
            cond += " AND block_id=?"
            args += (block_id,)
        total = store.q1(f"SELECT COUNT(*) c FROM edit_audit WHERE {cond}", args)["c"]
        sql = ("SELECT block_id, before, after, from_revision, to_revision, actor, ts, reason"
               f" FROM edit_audit WHERE {cond} ORDER BY rowid")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += (limit, offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            args += (offset,)
        rows = store.q(sql, args)
        return jsonify({"doc_id": doc_id, "audit": [dict(r) for r in rows],
                        "total": total, "offset": offset})

    @app.get("/audit/deletions")
    def deletion_audit():
        """删除留痕（FR-128）：只有 who/when/doc_id + 计数，绝不含正文（NFR-20）。"""
        need("delete")
        offset, limit = _window()
        total = store.q1("SELECT COUNT(*) c FROM deletions")["c"]
        sql = ("SELECT doc_id, source_key, actor, ts, kind, cascade_json"
               " FROM deletions ORDER BY rowid")
        args: list = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args = [limit, offset]
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            args = [offset]
        out = []
        for r in store.q(sql, tuple(args)):
            d = dict(r)
            d["cascade"] = json.loads(d.pop("cascade_json") or "{}")
            out.append(d)
        return jsonify({"deletions": out, "total": total, "offset": offset})

    # --- 搜索 / 事件 --------------------------------------------------------
    @app.get("/search")
    def _search():
        need("read")
        return jsonify(search(store, request.args.get("q", ""),
                              _int_arg("limit", 20)))

    @app.get("/events")
    def events():
        """全局事件总线（前端唯一事件通道）。"""
        need("read")
        since = _int_arg("since", 0)
        rows = store.q("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 200", (since,))
        return jsonify({"events": [dict(r) for r in rows]})

    @app.get("/jobs/<job_id>/events")
    def job_events(job_id):
        """per-job 端点：面向 CLI / 脚本 UA（CH-401 / FR-125）。

        CH-471 SVC-9：这是**咨询性约定**，不是安全边界——User-Agent 由客户端自报、可任意
        伪造（`curl -A doc2md-cli`）。真正的访问控制是下面的 `need("read")`（token 角色门）。
        UA 检查只是把误用（前端应走 /events 总线）尽早挡回并给出引导，不构成鉴权。
        """
        ua = request.headers.get("User-Agent", "")
        if not any(h in ua for h in SCRIPT_UA_HINTS):
            raise E4031_Forbidden(
                "per-job events endpoint is CLI/script only; use /events bus",
                detail={"ua": ua[:60], "rule": "FR-125", "advisory": True})
        need("read")
        rows = store.q("SELECT * FROM events WHERE job_id=? ORDER BY seq", (job_id,))
        return jsonify({"events": [dict(r) for r in rows]})

    # --- 设置 / token -------------------------------------------------------
    @app.get("/settings")
    def get_settings():
        need("read")
        return jsonify(cur())

    @app.put("/settings")
    def put_settings():
        need("settings_put")
        b = body()
        if not isinstance(b, dict):
            raise E4001_BadRequest("settings body must be an object")
        unknown = config.unknown_keys(b)
        if unknown:
            raise E4001_BadRequest(
                f"unknown configuration key(s): {', '.join(unknown)}（见方案 附录 A）")
        # CH-469：copy-on-write（见 create_app）+ override 落盘写稀疏差量而非本次 body。
        # 原实现整体覆盖文件 → 第二次 PUT 后 reload，第一次 PUT 的键静默回退。
        # CH-469（O-15）：校验发生在**应用之前**——坏值一旦进内存 cfg，
        # 后续每个请求都会按它行为（workers="8" 让批量直接崩），而 400 只是当场报错。
        # 未知键在这里是硬错误（装载期只警告）：PUT 是显式写入，拼错的键进了
        # override 文件就会在每次重启时静默失效。
        candidate = copy.deepcopy(app.config["doc2md"]["cfg"])
        baseline = app.config["doc2md"]["cfg"]
        _deep(candidate, b)
        try:
            config.validate(candidate)
            # CH-471 SVC-6（D4）：红线键（run.offline_assert）禁止经运行期 PUT 修改——
            # 一次 PUT 关掉断网守护 = 进程静默可出站。convert(cfg) 仍可显式带该键（合法 profile），
            # 拦的是「在线途中改」，故只在此处比对基线，不改 config.validate 的通用口径。
            config.check_runtime_immutable(candidate, baseline)
        except config.ConfigError as exc:
            raise E4001_BadRequest(f"invalid configuration: {exc}") from None
        with app.config["doc2md"]["cfg_lock"]:
            app.config["doc2md"]["cfg"] = candidate
            write_override(override, candidate, app.config["doc2md"]["cfg_baseline"])
        _emit(store, "settings_updated", None, None, {"keys": sorted(b.keys())}, logw())
        return jsonify({"updated": sorted(b.keys())})

    @app.post("/system/reload")
    def reload_():
        need("system_reload")
        # CH-462：与 create_app 同口径——重载必须叠加 settings_override.json，
        # 否则 PUT /settings 落盘的修改在 reload 后静默回退（启动合并、重载丢失）。
        # CH-469（O-15）：reload 的输入可以是人手改过的 doc2md.toml —— 校验失败保持
        # 旧配置继续服务并回 400，而不是让半坏配置接管（或逃逸成 500）。
        try:
            merged = config.load()
            if override.is_file():
                _deep(merged, json.loads(override.read_text(encoding="utf-8")))
            config.validate(merged)
        except config.ConfigError as exc:
            raise E4001_BadRequest(f"invalid configuration: {exc}") from None
        except (OSError, ValueError) as exc:      # 配置文件缺失 / 非法 TOML / 非法 JSON
            raise E4001_BadRequest(
                f"unreadable configuration source: {type(exc).__name__}: {str(exc)[:200]}") from None
        with app.config["doc2md"]["cfg_lock"]:
            app.config["doc2md"]["cfg"] = merged
        _emit(store, "system_reload", None, None, {}, logw())
        return jsonify({"reloaded": True, "ts": now_iso()})

    @app.post("/tokens")
    def create_token():
        need("token_admin")
        b = body()
        role = b.get("role", "reader")
        # CH-469（V-7）：角色白名单在 API 面判掉。原实现把任意字符串直送
        # `auth.issue`，而它抛的是 ValueError（不是 Doc2mdError）→ 逃逸成 500 + HTML，
        # 调用方拿不到 {code,message} 契约也看不出正确取值。
        allowed = config.get(cur(), "api.token_roles", auth.ROLES)
        if role not in auth.ROLES or role not in tuple(allowed):
            raise E4001_BadRequest(f"bad role {role!r}",
                                   detail={"allowed": [r for r in auth.ROLES
                                                       if r in tuple(allowed)]})
        return jsonify({"token": auth.issue(store, role, b.get("label", ""),
                                            allowed=allowed),
                        "role": role})

    @app.delete("/tokens/<token>")
    def revoke_token(token):
        need("token_admin")
        # CH-469（V-13）：不存在 / 已删除的 token 原实现回 200 {"revoked": true}
        # ——「没吊销任何东西」被报成「吊销成功」，与 FR-128 的删除同口径收敛为 404。
        if not auth.revoke(store, token):
            raise E4041_NotFound("unknown token", detail={"token": token[:6] + "…"})
        return jsonify({"revoked": True})

    @app.get("/")
    def index():
        """M9a 最小审阅编辑器（静态页，同源调用本 API；只读 token 也能用）。"""
        return app.send_static_file("editor.html")

    @app.get("/healthz")
    def health():
        n = store.q1("SELECT COUNT(*) c FROM docs")["c"]
        return jsonify({"ok": True, "docs": n, "pipeline": "2.4.0", "ts": now_iso()})

    return app


def _deep(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep(dst[k], v)
        else:
            dst[k] = v
    return dst


def _sparse(base: dict, merged: dict) -> dict:
    """merged 相对 base 的差量（只含被真正改过的叶子键，CH-469）。"""
    out: dict = {}
    for k, v in merged.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            sub = _sparse(base[k], v)
            if sub:
                out[k] = sub
        elif k not in base or base[k] != v:
            out[k] = v
    return out


def write_override(path: Path, merged: dict, baseline: dict) -> dict:
    """settings_override.json 的原子写入口（差量口径，CH-469）。

    全量落盘会把当时的默认值冻结进 override，升级后新默认值被旧值压住；
    只落差量则 reload / 重启仍按「默认 → 文件 → override」叠加。
    """
    diff = _sparse(baseline, merged)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass                      # 非 POSIX 平台 chmod 近似 no-op（见 ops/backup 同口径）
    tmp.replace(path)
    return diff


def _emit(store: Store, code: str, doc_id: str | None, job_id: str | None,
          payload: dict, writer: RotatingJsonlWriter | None = None,
          tx=None) -> None:
    """事件入库 + 同记录落内置日志（NFR-19：轮转由服务自身执行，不靠 newsyslog/cron）。

    CH-459（A-4）：`tx` 形参允许把事件插入并入调用方事务（worker._finish_ok 与
    同步 /jobs 路径的 detail/mark/event/入库四写合并为单次提交）。传入 tx 时
    日志行仍由本函数落盘、先于调用方提交——jsonl 是运维面 at-least-once 语义，
    DB 才是状态真源；不传 tx 时行为与从前完全一致（自有事务）。

    payload 不得含正文片段（NFR-20）：入库走 `scrub_record`，写日志走写入器自身的
    scrub——CH-459（A-12）两处已统一为同一实现（不看键名、只看长度）。
    """
    sql = ("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
           " VALUES (?,?,?,?,?)")
    args = (now_iso(), code, doc_id, job_id,
            json.dumps(scrub_record(payload), ensure_ascii=False))
    if tx is not None:
        tx.execute(sql, args)
    else:
        with store.tx() as t:
            t.execute(sql, args)
    if writer is not None:
        writer.write({"code": code, "doc_id": doc_id, "job_id": job_id, **payload})
        writer.flush()
