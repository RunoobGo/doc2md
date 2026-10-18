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
import hashlib
import json
import os
import threading
from pathlib import Path

from . import PIPELINE_VERSION, SCHEMA_VERSION, auth, config
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
from .ops.backup import BackupPlan
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
    prepare_ir_rows,
    reconcile_search,
    save_ir,
    search,
)

SCRIPT_UA_HINTS = ("python-requests", "curl/", "doc2md-cli", "httpx", "go-http-client")

# CH-473（R5-SVC-7）：同步路径的进程池全局只允许一个在途。Flask threaded 下两个
# 并发大批量 POST /jobs 会各建一个 workers 进程池（8GB 目标机上 pdfplumber 子进程
# 内存随池数翻倍）；单池已用满 perf.workers 的 CPU 并行度，双池不让单请求更快，
# 只是放大内存峰值——用信号量把并行收敛在单请求内，请求间排队（admin/submit 低频）。
_SYNC_POOL_GATE = threading.BoundedSemaphore(1)

# CH-476（R7-PERF-j）：列表端点的单次投递上限。`limit` 由客户端自报，SQLite 的
# `LIMIT -1`/大数语义是「无界」——reader 一个请求就能把全库行数（每行还带
# snippet / detail_json）物化成 JSON 塞进内存。拒负只挡住了一半（`?limit=999999999`
# 照样无界），此处再加硬顶：超过即截到 500，口径与 `/events` 的固定 200 同族。
_MAX_ROWS = 500

# CH-476（R7-PERF-k）：/docs/<id>/markdown 的分块流式读大小。
_MD_CHUNK = 64 * 1024

# CH-476（R7-SVC-2）：`mode` 档位白名单，与 CLI `--mode` 的 choices 同集合
# （`cli.py:107`）。用 tuple 不用 set：`in` 走 == 比较，对不可哈希的值（dict/list）
# 也只会返回 False，不会在校验器里抛 TypeError。
_MODES = ("auto", "single", "two_phase")

# CH-476（R7-SVC-3）：同步池的排队上界与超时提示。闸门原为无超时 `acquire()`——
# 小时级大批量占池期间，后续 `POST /jobs` 的请求线程无限期挂起（连接与线程一直被
# 占着，客户端只能等死）。现给等待设上界、超时即明确 503 + `Retry-After`：符合本项目
# 「明确失败优先于看似成功」，也不用「降级为串行」去掩盖排队（那会让同一请求在不同
# 负载下走不同吞吐路径，池路径的产物等价性红线不再覆盖它）。两个数是服务端自持的
# 排队上界、不是对外契约，故用常量而非配置键（新键要连带 附录 A / example.toml /
# 键集差校验三处，收益不抵 churn）。
_SYNC_GATE_WAIT_S = 60
_SYNC_RETRY_AFTER_S = 60


def _predelete_snapshot(cfg: dict, root: Path) -> Path | None:
    """CH-483（R8-OPS-1）：删除前整库快照；失败即 E5031，删除不得继续。

    与 cron 的本地快照同目录、同保留期；删除是低频操作，O(DB) 拷贝换取
    「误删可恢复」是明确的数据安全取舍。
    """
    state = Path(root) / "state.sqlite"
    if not state.is_file():
        return None
    plan = BackupPlan(
        local_dir=config.path_setting(cfg, "ops.backup_local_dir", root,
                                      str(Path(root) / "reports/backups")),
        external_dir=None,
        rotation_days=int(config.get(cfg, "ops.backup_rotation_days", 14)),
        work_root=Path(root),
        work_dir_mode=str(config.get(cfg, "ops.work_dir_mode", "700")),
        cfg=cfg)
    try:
        return plan.predelete_snapshot(state)
    except Doc2mdError:
        raise                                  # E4131（磁盘门禁）等契约错误原样上抛
    except Exception as exc:                    # noqa: BLE001 — 任何失败都不得放行删除
        raise E5031_StorageError(
            "pre-delete snapshot failed; delete aborted",
            detail={"reason": type(exc).__name__}) from exc


_ARTIFACT_META_KEYS = {
    "doc_id", "source_key", "source_name", "source_name_raw", "format",
    "pages", "sharded", "shard_count", "schema_version", "warnings",
}
_ARTIFACT_META_EXTRA = {"shard_stats", "llm_prompt", "notes"}


# 固定数量的分片锁：按 doc 串行、不同 doc 可并发，且不随文档数无限增长。
_REBUILD_LOCKS = tuple(threading.Lock() for _ in range(64))


def _doc_lock(doc_id: str) -> threading.Lock:
    return _REBUILD_LOCKS[hash(doc_id) % len(_REBUILD_LOCKS)]


def _rebuild_artifact(store: Store, doc_id: str, cfg: dict, root: Path) -> Path | None:
    """同进程内按 doc 串行重建；避免两个 PATCH 并发时旧 revision 后写覆盖新 zip。"""
    with _doc_lock(doc_id):
        return _rebuild_artifact_body(store, doc_id, cfg, root)


def _rebuild_artifact_body(store: Store, doc_id: str, cfg: dict,
                           root: Path) -> Path | None:
    """CH-485（R9-SVC-2）：从 DB 重建 zip；旧文档（gate_json 为空）返回 None。

    DB 已是完整事实源：blocks 保存 font/links/provenance，docs 保存 package 实际
    使用的 meta 与 S1.5 gate。PATCH/rollback/E4091/恢复后调用，保证导出与库面
    一致，而不是依赖初始转换留下的旧 zip。
    """
    from .ir import IR
    from .pipeline import s4_render
    from .pipeline.convert import package

    d = get_doc(store, doc_id)
    gate_raw = d.get("gate_json") or "{}"
    if gate_raw in ("", "{}"):
        return None
    meta = d.get("meta") or {}
    try:
        gate = json.loads(gate_raw) if isinstance(gate_raw, str) else gate_raw
    except ValueError:
        gate = {}
    blocks = list_blocks(store, doc_id)
    extra = {k: v for k, v in meta.items()
             if k not in _ARTIFACT_META_KEYS and k not in _ARTIFACT_META_EXTRA}
    ir = IR(
        doc_id=d["doc_id"], source_key=d["source_key"],
        source_name=d.get("source_name", "unnamed"),
        source_name_raw=d.get("source_name_raw", ""),
        format=d.get("format", "pdf"), pages=int(d.get("pages", 0) or 0),
        blocks=blocks, sharded=bool(d.get("sharded")),
        shard_count=int(d.get("shard_count", 1) or 1),
        warnings=list(meta.get("warnings", []) or []),
        schema_version=int(meta.get("schema_version", SCHEMA_VERSION)),
        extra_meta=extra,
    )
    md = s4_render.render(
        ir,
        preserve_strike=bool(config.get(cfg, "s4.preserve_marks_strike", False)),
        link_internal=str(config.get(cfg, "s4.link_internal", "plain")),
    )
    out_dir = config.path_setting(cfg, "paths.out", root, str(root / "out"))
    out = package(ir, md, meta, Path(out_dir), cfg, gate=gate)
    # package 期间文档可能被删除：提交后的删除路径会再 purge 一次，这里也做
    # 发布后复核，避免返回一个已无库行可寻址的 zip（FR-128）。
    try:
        alive = get_doc(store, doc_id) is not None
    except Doc2mdError:
        alive = False
    if not alive:
        try:
            out.unlink()
        except OSError:
            pass
        return None
    return out


def create_app(cfg: dict | None = None, root: Path | None = None,
               config_path: str | None = None, reload_from_files: bool = False):
    from flask import Flask, Response, jsonify, request

    cfg = cfg or config.load(config_path)
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
    # CH-473（R5-STOR-2 / STOR-3 的启动对账半）：FTS 与 blocks 的一致性只在写入路径
    # 维护，_migrate_fts 中途崩溃或历史缺陷会留下空/缺索引且无告警（原方案写了
    # 「启动/cron 对账」，cron 侧已落地、启动侧缺失——serve 实例最长 24h 用空索引
    # 检索）。启动时先对账一次：只回传统计、不读正文（NFR-20）；对账失败不阻断
    # 启动（旧库面问题留给 cron 下轮），异常类名进 SERVICE_START 日志。
    try:
        search_reconcile = reconcile_search(store)
    except Exception as exc:                    # noqa: BLE001 — 启动不得被对账炸掉
        search_reconcile = {"error": type(exc).__name__}
    writer.write({"code": "SERVICE_START", "root": str(root), "tmp_cleaned": tmp_cleaned,
                  "search_reconcile": search_reconcile,
                  "rotate_mb": writer.rotate_mb, "keep": writer.rotate_keep})
    writer.flush()
    app = Flask("doc2md")
    # CH-469：配置热更改为「copy-on-write + 锁」。原实现 `merged = dict(cfg)` 是浅拷贝，
    # `_deep` 就地改写共享的嵌套 dict，而并发请求线程正在读 cur() → 读到半改状态。
    # baseline 保存未叠加 override 的配置，用于把 override 文件算成「稀疏差量」
    # （只记真正被改过的键，不让全量默认值冻结进去、挡住日后升级）。
    app.config["doc2md"] = {"cfg": cfg, "store": store, "root": root, "sched": sched,
                            "writer": writer, "cfg_lock": threading.Lock(),
                            "cfg_baseline": cfg_baseline,
                            "config_path": config_path,
                            "reload_from_files": bool(reload_from_files)}

    def cur() -> dict:
        return app.config["doc2md"]["cfg"]

    def logw() -> RotatingJsonlWriter:
        return app.config["doc2md"]["writer"]

    def tok() -> str | None:
        return request.headers.get("X-Doc2md-Token") or (
            request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or None)

    def actor_id(b: dict | None = None, default: str = "anon") -> str:
        """审计 actor 标识（R6-SVC-2 / CH-475）。

        优先用客户端显式给出的 "as"（用户自填署名，非凭据）；缺省时用 bearer
        token 的 sha256 前 12 位——绝不把明文 token 前缀写进 edit_audit / tombstone。
        token 在库内只存哈希，审计面同样不落明文凭据（NFR-20 精神）；哈希前缀仍
        保留「同一 token 同一 actor」的可关联性，不保留凭据本身。
        """
        a = (b or {}).get("as")
        if a:
            return str(a)[:12]
        t = tok()
        if t:
            return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]
        return default

    def need(action: str) -> str:
        return auth.check(store, tok(), action)

    def body() -> dict:
        # CH-483（R8-API-1）：数组/字符串/数字 JSON 会让调用方 `.get()` 抛
        # AttributeError → 500；请求体契约只接受 JSON 对象。
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise E4001_BadRequest("JSON object required")
        return data

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
        # CH-483（R8-SVC-1）：转换窗口的删除守护基准。记录请求开始时 deletions
        # 的最大自增 id；落库事务内只要出现更大的墓碑，就拒绝落库。
        since_seq = store.q1("SELECT COALESCE(MAX(id),0) AS m FROM deletions")["m"]
        b = body()
        path = b.get("path", "")
        if not isinstance(path, str):
            raise E4001_BadRequest("path must be a string")
        idem = request.headers.get("Idempotency-Key") or b.get("idempotency_key")
        if idem is not None and not isinstance(idem, str):
            raise E4001_BadRequest("idempotency_key must be a string")
        # CH-471（STOR-5）：重复转换默认护编辑，force=true 才允许覆盖已有编辑
        # CH-473（R5-SVC-6）：`bool(b.get("force"))` 把 JSON 字符串 "false"/"no"/"0"
        # 都判真——字符串形态的假值会意外放行「覆盖用户编辑」。只认布尔 true 与
        # 显式真值字符串（"true"/"1"/"yes"），其余（含缺省）一律不 force。
        force = b.get("force")
        force = force is True or (
            isinstance(force, str) and force.strip().lower() in ("true", "1", "yes"))
        # CH-476（R7-SVC-2）：`mode` 白名单。原实现把任意 JSON 值直送 `tasks.mode`，
        # 消费侧（`convert`）只区分 `"single"`，其余一律按 auto 执行——于是 `mode:"singel"`
        # （拼写错）与 `mode:{}`（对象）都能入库，用户以为切了档位、实际跑的是默认档，
        # 而队列行里留着一条永远不被识别的值。CLI 侧 `--mode` 早有 choices（`cli.py:107`），
        # API 面补齐同一口径：非白名单 → 400/E4001，在入队/转换之前拒。
        mode = b.get("mode")
        if mode is not None and mode not in _MODES:
            raise E4001_BadRequest("mode must be auto/single/two_phase",
                                   detail={"mode": type(mode).__name__})
        if not path:
            raise E4001_BadRequest("path required")
        p = Path(path)
        files: list[Path] = []
        if p.is_dir():
            lim = walk_limits(cur())
            # CH-469（O-10）：走 scan_or_raise——目录没扫完（超时）必须 413/E4133，
            # 不能把半截清单当完整结果入队（用户视角是「目录里明明有，队列里没有」）。
            # 与 CLI 同口径：目录里的 README/图片不是输入，直接忽略；否则整批
            # 第一个非 PDF/DOCX 就会 E4151，导致 0 入队。
            files = [Path(f) for f in scan_or_raise(p, lim)
                     if Path(f).suffix.lower() in (".pdf", ".docx")]
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
            from .worker import enqueue_many
            # CH-485（R9-SVC-7）：不在这里静默剔除扫描后消失的文件；
            # enqueue_many 的预检会以 E4041 整体失败，避免“响应少一项”。
            targets = files
            # CH-476（R7-PERF-3）：预检 + 内容哈希进线程池并行（hashlib 对 1MB 块
            # 释放 GIL），写库仍按原顺序单线程提交——`jobs[]` 顺序、附着/幂等口径不变。
            # 入队前整体预检 magic 的「要么全入队、要么零副作用」纪律由 enqueue_many
            # 的预检阶段承担（原实现另开一轮 sniff，每个文件多读一次头部）。
            # 竞态口径变化（有意）：清单生成后文件消失，旧实现把它从响应里静默剔除，
            # 现按 E4041 整体失败——「目录里明明有、队列里没有」比一次 404 更难排查。
            out = enqueue_many(sched, targets, mode=mode,
                               idem_keys=[_idem_for(f) for f in targets],
                               cfg=cur(), force=force)
            for it in out:
                if not it["attached"]:
                    _emit(store, "job_queued", it["doc_id"], it["job_id"],
                          {"source": "api"}, logw())
            return jsonify({"jobs": out, "mode": "queued",
                            "consume_with": "doc2md work"}), 202

        out = []

        def _finish_one(f, ir, doc_id, source_key, since, *, meta=None, gate=None):
            j = sched.submit(doc_id, source_key, idem_key=_idem_for(f),
                             source_path=str(f), mode=mode, del_seq=since,
                             force=force)
            # R6-SVC-3（CH-475，决策 D1）：幂等命中 / 附着既有作业时，该 doc_id 已由
            # 在途或既往作业负责落库——不再重跑 save_ir。既省一次全量写（性能优先），
            # 又杜绝「对已 done 作业再 mark 致终态翻转 / 对已编辑文档误触 E4091 报
            # conflict」的数据安全缺陷。直接复用既有作业状态。
            if j.idem_hit or j.attached:
                row = sched.get(j.job_id) or {}
                out.append({"file": f.name, "job_id": j.job_id, "doc_id": doc_id,
                            "attached": j.attached, "idempotency_hit": j.idem_hit,
                            "status": row.get("status", j.status)})
                return
            # CH-459（A-4）：save_ir / mark(done) / 事件三写合并为单事务
            # （原 5 次独立提交；每提交一次 fsync 是当时的口径，`synchronous`
            # 自 CH-471 / STOR-1 起已是 NORMAL，合批的收益剩「一次写锁 + 崩溃原子性」）。
            # CH-471（STOR-5）：库内已有编辑时 save_ir 抛 E4091，整笔事务回滚
            # （job 不标 done、编辑原样保留），本文件记为 conflict 后继续处理其余。
            # CH-476（R7-SVC-1，决策 D6）：mark 提前到 save_ir 之前（与 worker
            # _finish_ok 同序）——submit 与本事务之间 delete_doc 若级联收走了
            # 刚建的 queued 行，mark rowcount=0 → 抛 E4041 整笔回滚，正文不写回，
            # 杜绝「docs 有、tasks 无」的半复活态（FR-128 轮出语义）。
            prepared = prepare_ir_rows(ir)
            try:
                with store.tx() as tx:
                    # CH-483（R8-SVC-1）：同一个写事务内先看墓碑。删除与落库
                    # 由 BEGIN IMMEDIATE 串行化——要么本事务先提交、随后删除
                    # 级联收走；要么删除先提交、本事务看到墓碑并回滚。
                    dead = tx.execute(
                        "SELECT 1 FROM deletions WHERE (doc_id=? OR doc_id=?)"
                        " AND id>? LIMIT 1",
                        (doc_id, f"source:{source_key}", since)).fetchone()
                    if dead is not None:
                        raise E4041_NotFound("doc deleted during conversion")
                    if not sched.mark(j.job_id, "done", tx=tx):
                        raise E4041_NotFound(f"task row gone: {j.job_id}")
                    save_ir(store, ir, tx=tx, force=force, meta=meta, gate=gate,
                            prepared=prepared)
                    _emit(store, "job_done", doc_id, j.job_id,
                          {"blocks": len(ir.blocks)}, logw(), tx=tx)
            except E4041_NotFound:
                # 行可能已被删除级联收走，也可能是 submit 新建后被墓碑拦截。
                # 后者补 rejected 终态（mark 返回 False 时不再写事件，避免孤儿行）。
                if sched.mark(j.job_id, "rejected", "E4041",
                              {"reason": "doc_deleted_during_conversion"}):
                    _emit(store, "job_rejected", doc_id, j.job_id,
                          {"code": "E4041", "source": "api"}, logw())
                # CH-484：删除×在途转换——convert 刚写进 out/{doc_id}.zip 的产物
                # 随本轮回滚成为孤儿（库面已不复活，字节却仍占盘）。仅在 doc 确已
                # 出 docs 时 best-effort 清掉，绝不误删存活文档的载体。
                from .worker import _drop_orphan_artifact
                _drop_orphan_artifact(store, root, cur(), doc_id)
                out.append({"file": f.name, "job_id": j.job_id, "doc_id": doc_id,
                            "status": "rejected", "code": "E4041",
                            "reason": "doc_deleted_during_conversion"})
                return
            except E4091_DocOverwrite as e:
                sched.mark(j.job_id, "rejected", e.code,
                           {"reason": "edited_blocks_present",
                            "edited_blocks": e.detail.get("edited_blocks")})
                _emit(store, "job_rejected", doc_id, j.job_id,
                      {"code": e.code, "source": "api"}, logw())
                # CH-485（R9-SVC-2）：本次 package 已覆盖旧 zip；从 DB 的编辑后
                # 内容重建，恢复“导出 == 库面”。失败不改变已判定的 conflict。
                try:
                    _rebuild_artifact(store, doc_id, cur(), root)
                except Exception as exc:  # noqa: BLE001 — 重建是补偿动作
                    logw().write({"code": "E_ARTIFACT_REBUILD",
                                  "doc_id": doc_id,
                                  "exc": type(exc).__name__})
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
            # CH-473（R5-SVC-7）：进程池全局信号量——见 _SYNC_POOL_GATE 注释。
            # 池的整个生命周期（建池 → 转换 → 读产物）都在临界区内，退出即释放。
            # CH-476（R7-SVC-3）：等待改为有界——超时明确回 503 + Retry-After，
            # 不让请求线程为上一个大批量的池无限期挂起。
            if not _SYNC_POOL_GATE.acquire(timeout=_SYNC_GATE_WAIT_S):
                return jsonify({"code": "E5041", "message": "sync conversion pool busy",
                                "detail": {"reason": "sync_pool_busy",
                                           "waited_s": _SYNC_GATE_WAIT_S,
                                           "files": len(files)}}), 503, {
                    "Retry-After": str(_SYNC_RETRY_AFTER_S)}
            try:
                by_file = {r["file"]: r for r in run_batch(
                    files, cfg=cur(), mode=mode,
                    out_dir=config.path_setting(cur(), "paths.out", root,
                                                str(root / "out")),
                    workers=default_workers(cfg))}
                for f in files:
                    r = by_file.get(str(f)) or {"status": "error", "code": "E5000"}
                    if r["status"] == "rejected":
                        j = sched.submit_rejected(f.name, r.get("code", "E4221"),
                                                  {"reason": r.get("message", "")},
                                                  idem_key=_idem_for(f))
                        _emit(store, "job_rejected", None, j.job_id,
                              {"code": r.get("code", "E4221"), "source": "api_batch"},
                              logw())
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
                        _finish_one(f, ir, r.get("doc_id", ""),
                                    r.get("source_key", ""), since_seq,
                                    meta=r.get("meta"), gate=r.get("gate"))
            finally:
                _SYNC_POOL_GATE.release()
            return jsonify({"jobs": out}), 202

        for f in files:
            try:
                # CH-476（R7-SVC-2）：同步分支同样吃 `mode`。原实现只在异步分支读这个
                # 字段，`queue_async=false`（默认）下 `{"mode":"single"}` 被静默忽略——
                # 客户端以为切了档，实际跑的是 `s2.mode` 默认档。
                res = convert(f, cfg=cur(), mode=mode,
                              out_dir=config.path_setting(cur(), "paths.out", root,
                                                          str(root / "out")),
                              force=force)
            except Doc2mdError as e:
                if e.code == "E4221":
                    j = sched.submit_rejected(f.name, e.code, e.detail,
                                              idem_key=_idem_for(f))
                    _emit(store, "job_rejected", None, j.job_id,
                          {"code": e.code, "source": "api_sync"}, logw())
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
            _finish_one(f, res.ir, res.doc_id, res.source_key, since_seq,
                        meta=getattr(res, "meta", None),
                        gate=getattr(res, "gate", None))
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
        limit = min(limit, _MAX_ROWS)      # CH-476（R7-PERF-j）：无界上限硬顶
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
        out_dir = config.path_setting(cur(), "paths.out", root, str(root / "out"))
        zp = out_dir / f"{doc_id}.zip"
        if not zp.exists():
            raise E4041_NotFound("artifact not found")
        import zipfile
        # CH-476（R7-PERF-k）：中央目录先校验 + 正文分块流式出。原实现 `z.read()` 把
        # 整篇 content.md 一次性读进内存、再 decode 成 str、Flask 又 encode 回 bytes，
        # 单请求峰值 ≈ 3× 正文大小（数千页文档 = 数十 MB × 并发数）。
        # 状态码语义不变：坏 zip / 缺 content.md 仍在**响应头之前**判定并抛出
        # （E5031 / E4041）；流式阶段才失败（读取中被替换）只能截断，属可接受窗口。
        try:
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.endswith("content.md")]
        except zipfile.BadZipFile as exc:
            raise E5031_StorageError("artifact unreadable",
                                     detail={"reason": type(exc).__name__}) from exc
        if not names:
            raise E4041_NotFound("artifact content.md missing")

        def chunks():
            with zipfile.ZipFile(zp) as z, z.open(names[0]) as f:
                while True:
                    buf = f.read(_MD_CHUNK)
                    if not buf:
                        break
                    yield buf

        # `content_type` 而非 `mimetype`：body 改为字节流后 Flask 不再自动补 charset，
        # 显式写出以保持与原「返回 str」实现逐字节相同的响应头。
        return Response(chunks(), content_type="text/markdown; charset=utf-8")

    @app.patch("/docs/<doc_id>/blocks/<block_id>")
    def patch(doc_id, block_id):
        need("edit")
        b = body()
        res = patch_block(store, doc_id, block_id, b, actor_id(b),
                          require_base_revision=bool(
                              config.get(cur(), "api.base_revision_required", True)))
        _emit(store, "text_edited", doc_id, None, {"block_id": block_id,
                                                   "revision": res["revision"]}, logw())
        try:
            _rebuild_artifact(store, doc_id, cur(), root)
        except Exception as exc:            # noqa: BLE001 — 编辑已提交，回报补偿失败
            res["artifact_rebuild_error"] = type(exc).__name__
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
        actor = actor_id(b)
        res = rollback(store, doc_id, block_id, to_rev, actor)
        try:
            _rebuild_artifact(store, doc_id, cur(), root)
        except Exception as exc:            # noqa: BLE001 — 回滚已提交，回报补偿失败
            res["artifact_rebuild_error"] = type(exc).__name__
        return jsonify(res)

    @app.delete("/docs/<doc_id>")
    def del_doc(doc_id):
        need("delete")
        if request.args.get("confirm") != "true":
            raise E4001_BadRequest("confirm=true required for destructive delete",
                                 detail={"rule": "FR-128", "requires": "confirm=true"})
        cfg = cur()
        # CH-485（R9-OPS-1）：先确认对象存在再拍全库快照；删除不存在的 doc
        # 不应产生任何备份写入。
        if store.q1("SELECT 1 FROM docs WHERE doc_id=?", (doc_id,)) is None:
            raise E4041_NotFound(f"doc {doc_id} not found")
        _predelete_snapshot(cfg, root)
        return jsonify(delete_doc(
            store, doc_id, actor_id(None, default="admin"), root,
            out_dir=config.path_setting(cfg, "paths.out", root, str(root / "out")),
            tmp_dir=config.path_setting(cfg, "paths.tmp", root, str(root / "tmp"))))

    @app.delete("/sources/<source_key>")
    def del_src(source_key):
        need("delete")
        if request.args.get("confirm") != "true":
            raise E4001_BadRequest("confirm=true required for destructive delete",
                                 detail={"rule": "FR-128", "requires": "confirm=true"})
        cfg = cur()
        if store.q1("SELECT 1 FROM docs WHERE source_key=?", (source_key,)) is None:
            raise E4041_NotFound(f"source {source_key} not found")
        _predelete_snapshot(cfg, root)
        return jsonify(delete_source(
            store, source_key, actor_id(None, default="admin"), root,
            out_dir=config.path_setting(cfg, "paths.out", root, str(root / "out")),
            tmp_dir=config.path_setting(cfg, "paths.tmp", root, str(root / "tmp"))))

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
        # CH-473（R5-SVC-3）：负值 limit 拼进 SQL 得 `LIMIT -1`（SQLite 语义 = 无界），
        # reader 单请求即可拉全库检索结果（每条带 snippet）——SVC-8 只修了 /jobs，
        # 这里补同口径拒负。
        limit = _int_arg("limit", 20)
        if limit < 0:
            raise E4001_BadRequest("limit must be >= 0")
        return jsonify(search(
            store, request.args.get("q", ""), min(limit, _MAX_ROWS),
            full=(request.args.get("mode") == "full"
                  or request.args.get("full") in ("1", "true"))))

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
        # CH-473（R5-SVC-4）：candidate 构造与校验整体挪进 cfg_lock——原实现
        # deepcopy 在锁外，两个并发 PUT 各持旧副本、后应用者把先应用者的键从内存态
        # 抹掉（override 文件不丢、重启/reload 可自愈，但窗口内两请求所见不一致）。
        # admin-only 低频端点，锁内校验的串行化成本可忽略。
        with app.config["doc2md"]["cfg_lock"]:
            candidate = copy.deepcopy(app.config["doc2md"]["cfg"])
            baseline = app.config["doc2md"]["cfg"]
            _deep(candidate, b)
            try:
                config.validate(candidate)
                # CH-471 SVC-6（D4）：红线键（run.offline_assert、CH-473 起含
                # api.allow_remote_bind）禁止经运行期 PUT 修改——一次 PUT 关掉断网
                # 守护 / 两步 PUT 掏空 loopback 默认 = 进程静默可出站。convert(cfg)
                # 仍可显式带该键（合法 profile），拦的是「在线途中改」，故只在此处
                # 比对基线，不改 config.validate 的通用口径。
                config.check_runtime_immutable(candidate, baseline)
            except config.ConfigError as exc:
                raise E4001_BadRequest(f"invalid configuration: {exc}") from None
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
            if app.config["doc2md"]["reload_from_files"]:
                merged = config.load(app.config["doc2md"].get("config_path"))
                # `serve --root` 是进程级绑定：配置文件里的相对 paths.root
                # 不得在 reload 时把 store/writer 的根换掉（换根必须重启）。
                merged.setdefault("paths", {})["root"] = str(root)
            else:
                # 程序化 create_app(cfg) / 测试没有显式文件来源：以启动基线为
                # 基准重放 override。退回 config.load() 会按当前 cwd 重新找
                # doc2md.toml，把调用方传入的 cfg 静默换掉（测试与嵌入方必现）。
                merged = copy.deepcopy(app.config["doc2md"]["cfg_baseline"])
            if override.is_file():
                _deep(merged, json.loads(override.read_text(encoding="utf-8")))
            config.validate(merged)
            # store/writer/root 闭包在启动时绑定；运行期改 paths.root 会让请求读写
            # 新根、后台仍用旧根。数据边界变更必须重启，不能靠 reload 静默切换。
            new_root = Path(config.path_setting(merged, "paths.root", root)).resolve()
            if new_root != Path(root).resolve():
                raise E4001_BadRequest(
                    "paths.root changes require a restart",
                    detail={"current": str(root), "requested": str(new_root)})
            # R6-SVC-1（CH-475）：reload 与 PUT /settings 同口径守运行期不可变红线键。
            # 原实现只在 PUT 调 check_runtime_immutable——直接改 doc2md.toml 再 reload
            # 即可绕过守卫关掉 run.offline_assert / api.allow_remote_bind，等于离线断言
            # 存在运行期旁路。比对当前内存基线，红线键变化则拒绝 reload（保持旧配置服务）。
            # 确需调整红线键仍须**重启**（reload 属运行期变更，与 PUT 同等对待）。
            config.check_runtime_immutable(merged, app.config["doc2md"]["cfg"])
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
        # CH-476（R7-OPS-2）：版本走 `PIPELINE_VERSION` 常量——原硬编码 "2.4.0"
        # 会在每次契约级升版时静默说谎（doctor / cron 日报同为该常量的消费方）。
        return jsonify({"ok": True, "docs": n, "pipeline": PIPELINE_VERSION,
                        "ts": now_iso()})

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


def _fsync_dir(path: Path) -> None:
    """目录项落盘（CH-473 / R5-SVC-5，与 ops/backup._fsync_dir 同口径）。

    rename/replace 只保证看得到目录项，不保证它已离开页缓存——掉电时替换可能丢。
    override 是「设置静默回退默认值」的数据安全面，值得与备份容器同等级的落盘纪律。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_override(path: Path, merged: dict, baseline: dict) -> dict:
    """settings_override.json 的原子写入口（差量口径，CH-469）。

    全量落盘会把当时的默认值冻结进 override，升级后新默认值被旧值压住；
    只落差量则 reload / 重启仍按「默认 → 文件 → override」叠加。

    CH-473（R5-SVC-5）：补落盘纪律——原实现 tmp.write_text + replace 无 fsync，
    replace 后崩溃可丢 override（用户的 PUT 静默回退默认值）。现为「内容 fsync →
    rename → 目录 fsync」三段式（ops/backup 容器同口径）。
    """
    diff = _sparse(baseline, merged)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(diff, ensure_ascii=False, indent=2))
        fh.flush()
        os.fsync(fh.fileno())                   # R5-SVC-5：内容先落盘再 rename
    from .security.perms import ensure_private
    ensure_private(tmp, 0o600)    # Windows 走 ACL，不把 chmod 的 no-op 当成功
    tmp.replace(path)
    _fsync_dir(path.parent)                     # R5-SVC-5：目录项抗掉电
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
