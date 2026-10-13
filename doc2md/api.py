"""API —— v2.4 增补与修订（§3.6，锚点 v2.1 §7.2）。

新增：DELETE /docs/{id}?confirm=true、DELETE /sources/{key}?confirm=true
修订：PATCH 必填 base_revision + 425/E4251 + current_revision；
     POST /jobs 目录扫描统一走 FR-129（超限 413/E4133）；
     token 三角色 + --role；GET /settings reader 可见、PUT 需 admin。
事件：仅全局总线 /events（FR-125 硬规则，CH-401）；per-job 端点仅 CLI/脚本 UA。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import auth, config
from .cron import sweep_tmp
from .edits import patch_block, rollback
from .errors import (ApiError, E4001_BadRequest, E4031_Forbidden, E4041_NotFound,
                     E4133_DirTooWide, Doc2mdError)
from .ops.logging import RotatingJsonlWriter
from .pipeline.convert import convert
from .scheduler import Scheduler
from .security.dirguard import WalkLimits, guard_walk
from .storage.db import Store, now_iso
from .storage.repo import delete_doc, delete_source, get_doc, list_blocks, save_ir, search

SCRIPT_UA_HINTS = ("python-requests", "curl/", "doc2md-cli", "httpx", "go-http-client")


def create_app(cfg: dict | None = None, root: Path | None = None):
    from flask import Flask, jsonify, request, Response

    cfg = cfg or config.load()
    root = Path(root) if root else config.path_setting(cfg, "paths.root", ".")
    root.mkdir(parents=True, exist_ok=True)
    override = root / "settings_override.json"
    if override.is_file():
        _deep(cfg, json.loads(override.read_text(encoding="utf-8")))
    store = Store(root / "state.sqlite")
    sched = Scheduler(store)
    # NFR-19：轮转由服务内置执行（禁止依赖 newsyslog/cron）——服务自己持有写入器；
    # §4.6 同时要求「服务启动时清理 work/tmp 残留」，与 cron --daily 共用 sweep_tmp。
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
    app.config["doc2md"] = {"cfg": cfg, "store": store, "root": root, "sched": sched,
                            "writer": writer}

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

    # --- 作业 ---------------------------------------------------------------
    @app.post("/jobs")
    def post_job():
        need("submit_job")
        b = body()
        path = b.get("path", "")
        idem = request.headers.get("Idempotency-Key") or b.get("idempotency_key")
        if not path:
            raise E4001_BadRequest("path required")
        p = Path(path)
        files: list[Path] = []
        if p.is_dir():
            lim = WalkLimits(
                follow_symlinks=bool(config.get(cur(), "watch.follow_symlinks", False)),
                max_depth=int(config.get(cur(), "watch.max_depth", 20)),
                skip_hidden=bool(config.get(cur(), "watch.skip_hidden", True)),
                max_files_per_dir=int(config.get(cur(), "watch.max_files_per_dir", 5000)))
            files = [Path(f) for f in guard_walk(p, lim).files]
        elif p.is_file():
            files = [p]
        else:
            raise E4041_NotFound(f"path not found: {path}")

        out = []
        for f in files:
            try:
                res = convert(f, cfg=cur(), out_dir=root / "out")
            except Doc2mdError as e:
                if e.code == "E4221":
                    j = sched.submit("reject:" + f.name, idem_key=idem)
                    sched.mark(j.job_id, "rejected", e.code, e.detail)
                    out.append({"file": f.name, "status": "rejected", "code": e.code})
                else:
                    raise
                continue
            save_ir(store, res.ir)
            j = sched.submit(res.doc_id, res.source_key, idem_key=idem)
            sched.mark(j.job_id, "done")
            _emit(store, "job_done", res.doc_id, j.job_id, {"blocks": len(res.ir.blocks)}, logw())
            out.append({"file": f.name, "job_id": j.job_id, "doc_id": res.doc_id,
                        "attached": j.attached, "idempotency_hit": j.idem_hit,
                        "sharded": res.ir.sharded, "shard_count": res.ir.shard_count})
        return jsonify({"jobs": out}), 202

    # --- 文档 ---------------------------------------------------------------
    @app.get("/docs/<doc_id>")
    def get_document(doc_id):
        need("read")
        d = get_doc(store, doc_id)
        d["blocks"] = [b.to_dict() for b in list_blocks(store, doc_id)]
        return jsonify(d)

    @app.get("/docs/<doc_id>/markdown")
    def get_markdown(doc_id):
        need("export")
        get_doc(store, doc_id)
        zp = root / "out" / f"{doc_id}.zip"
        if not zp.exists():
            raise E4041_NotFound("artifact not found")
        import zipfile
        with zipfile.ZipFile(zp) as z:
            name = [n for n in z.namelist() if n.endswith("content.md")][0]
            return Response(z.read(name).decode("utf-8"), mimetype="text/markdown")

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
        return jsonify(rollback(store, doc_id, block_id, int(b["to_revision"]),
                                b.get("as", "anon")[:12]))

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
        """编辑审计流水（含 before/after 正文）→ 需 editor 及以上，不进 reader。"""
        need("edit")
        get_doc(store, doc_id)
        block_id = request.args.get("block_id")
        sql = ("SELECT block_id, before, after, from_revision, to_revision, actor, ts, reason"
               " FROM edit_audit WHERE doc_id=?")
        args: tuple = (doc_id,)
        if block_id:
            sql += " AND block_id=?"
            args += (block_id,)
        rows = store.q(sql + " ORDER BY rowid", args)
        return jsonify({"doc_id": doc_id, "audit": [dict(r) for r in rows]})

    @app.get("/audit/deletions")
    def deletion_audit():
        """删除留痕（FR-128）：只有 who/when/doc_id + 计数，绝不含正文（NFR-20）。"""
        need("delete")
        rows = store.q("SELECT doc_id, source_key, actor, ts, kind, cascade_json"
                       " FROM deletions ORDER BY rowid")
        out = []
        for r in rows:
            d = dict(r)
            d["cascade"] = json.loads(d.pop("cascade_json") or "{}")
            out.append(d)
        return jsonify({"deletions": out})

    # --- 搜索 / 事件 --------------------------------------------------------
    @app.get("/search")
    def _search():
        need("read")
        return jsonify(search(store, request.args.get("q", ""),
                              int(request.args.get("limit", 20))))

    @app.get("/events")
    def events():
        """全局事件总线（前端唯一事件通道）。"""
        need("read")
        since = int(request.args.get("since", 0))
        rows = store.q("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 200", (since,))
        return jsonify({"events": [dict(r) for r in rows]})

    @app.get("/jobs/<job_id>/events")
    def job_events(job_id):
        """per-job 端点：仅 CLI / 脚本 UA（FR-125 硬规则，CH-401）。"""
        ua = request.headers.get("User-Agent", "")
        if not any(h in ua for h in SCRIPT_UA_HINTS):
            raise E4031_Forbidden(
                "per-job events endpoint is CLI/script only; use /events bus",
                detail={"ua": ua[:60], "rule": "FR-125"})
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
        merged = dict(app.config["doc2md"]["cfg"])
        _deep(merged, b)
        app.config["doc2md"]["cfg"] = merged
        override = root / "settings_override.json"
        override.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
        override.chmod(0o600)
        _emit(store, "settings_updated", None, None, {"keys": sorted(b.keys())}, logw())
        return jsonify({"updated": sorted(b.keys())})

    @app.post("/system/reload")
    def reload_():
        need("system_reload")
        app.config["doc2md"]["cfg"] = config.load()
        _emit(store, "system_reload", None, None, {}, logw())
        return jsonify({"reloaded": True, "ts": now_iso()})

    @app.post("/tokens")
    def create_token():
        need("token_admin")
        role = body().get("role", "reader")
        return jsonify({"token": auth.issue(store, role, body().get("label", "")),
                        "role": role})

    @app.delete("/tokens/<token>")
    def revoke_token(token):
        need("token_admin")
        auth.revoke(store, token)
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


def _emit(store: Store, code: str, doc_id: str | None, job_id: str | None,
          payload: dict, writer: RotatingJsonlWriter | None = None) -> None:
    """事件入库 + 同记录落内置日志（NFR-19：轮转由服务自身执行，不靠 newsyslog/cron）。

    payload 不得含正文片段（NFR-20）：入库走 `_scrub`，写日志走写入器自身的 scrub。
    """
    with store.tx() as tx:
        tx.execute("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
                   " VALUES (?,?,?,?,?)",
                   (now_iso(), code, doc_id, job_id,
                    json.dumps(_scrub(payload), ensure_ascii=False)))
    if writer is not None:
        writer.write({"code": code, "doc_id": doc_id, "job_id": job_id, **payload})
        writer.flush()


def _scrub(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 64:
            import hashlib
            out[k] = f"<sha256:{hashlib.sha256(v.encode('utf-8', 'replace')).hexdigest()[:12]}>"
        else:
            out[k] = v
    return out
