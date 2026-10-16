"""state.sqlite —— 单一状态库（WAL；cron 与 serve 分进程共用）。

schema 覆盖本期全部落点：
- docs / blocks（+ revision，CH-409 乐观锁）
- edit_audit（before/after 全文，NFR-18 因此要求备份加密）
- deletions（删除留痕，只含 who/when/doc_id，不含正文，FR-128 / NFR-20）
- tokens（+ role，CH-407 三角色；吊销即时生效）
- tasks（+ 部分唯一索引，CH-412 并发投递防护）
- events（全局总线持久化，保留期由 cron 清理）
- search_blocks（FTS5，编辑后 ≤5s 刷新，CH-418；文档级检索走它按 doc_id 聚合，无独立 search_docs 表）
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import weakref
from datetime import datetime, timezone
from pathlib import Path

DB_VERSION = 5   # state.sqlite 结构版本（user_version）；与 IR 的 SCHEMA_VERSION=14 是两套口径

DDL = f"""
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS docs (
  doc_id        TEXT PRIMARY KEY,
  source_key    TEXT NOT NULL,
  source_name   TEXT NOT NULL,
  source_name_raw TEXT DEFAULT '',
  format        TEXT NOT NULL,
  pages         INTEGER DEFAULT 0,
  sharded       INTEGER DEFAULT 0,
  shard_count   INTEGER DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'done',
  profile       TEXT DEFAULT 'standard',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  meta_json     TEXT DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS ix_docs_source ON docs(source_key);

CREATE TABLE IF NOT EXISTS blocks (
  doc_id   TEXT NOT NULL,
  block_id TEXT NOT NULL,
  type     TEXT NOT NULL,
  text     TEXT NOT NULL,
  level    INTEGER,
  page     INTEGER,
  flags_json  TEXT DEFAULT '{{}}',
  marks_json  TEXT DEFAULT '{{}}',
  immutable INTEGER DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  PRIMARY KEY (doc_id, block_id)
);

CREATE TABLE IF NOT EXISTS edit_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL, block_id TEXT NOT NULL,
  before TEXT, after TEXT,
  from_revision INTEGER, to_revision INTEGER,
  actor TEXT, ts TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_doc ON edit_audit(doc_id, block_id);

CREATE TABLE IF NOT EXISTS deletions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL, source_key TEXT, actor TEXT, ts TEXT,
  kind TEXT NOT NULL DEFAULT 'doc',      -- doc | source
  cascade_json TEXT DEFAULT '{{}}'       -- 只记数量与路径指纹，不记正文
);

CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,                        -- CH-471 SVC-9：存 sha256(token) 十六进制，
                                                 -- 明文只在签发时展示一次；按哈希查比对。
                                                 -- 列名沿用 token（不破坏既有 PRAGMA 迁移），
                                                 -- 存量明文由 _migrate 逐行哈希就地升级。
  role  TEXT NOT NULL DEFAULT 'admin',
  label TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  revoked_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
  job_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  source_key TEXT,
  status TEXT NOT NULL,                  -- queued|running|done|failed|rejected
  idem_key TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT,
  error_code TEXT, detail_json TEXT DEFAULT '{{}}',
  source_path TEXT, mode TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_idem ON tasks(idem_key);
-- CH-448（DB_VERSION=4）：队列入队即带消费路径与转换档位，独立列而非塞 detail_json：
-- detail_json 是「结果/失败明细」契约面，队列管道数据混进去会让两者互相污染，
-- 且不依赖 sqlite 的 JSON1 编译开关。
CREATE INDEX IF NOT EXISTS ix_tasks_queued ON tasks(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_doc_active
  ON tasks(doc_id) WHERE status IN ('queued','running');
-- CH-469：按 doc_id 的**非活动态**查找没有可用索引——uq_tasks_doc_active 是部分索引，
-- 只覆盖 status IN ('queued','running')，而 `DELETE FROM tasks WHERE doc_id=?`
-- （FR-128 级联删除）与历史作业回溯要扫全表。
CREATE INDEX IF NOT EXISTS ix_tasks_doc ON tasks(doc_id);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, code TEXT NOT NULL,
  doc_id TEXT, job_id TEXT, payload_json TEXT DEFAULT '{{}}'
);
-- CH-469：events 只有 seq 主键可用。`GET /jobs/<id>/events`（FR-125 per-job 通道）
-- 与删除级联的 `COUNT/DELETE ... WHERE doc_id=?` 都是全表扫描；
-- 事件按 cron 保留期清理前会持续增长，这两个查询在常驻实例上是热点。
CREATE INDEX IF NOT EXISTS ix_events_job ON events(job_id);
CREATE INDEX IF NOT EXISTS ix_events_doc ON events(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS search_blocks USING fts5(
  doc_id UNINDEXED, block_id UNINDEXED, text, aux, tokenize='unicode61'
);

PRAGMA user_version={DB_VERSION};
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def token_hash(token: str) -> str:
    """token 存储键（CH-471 SVC-9）：sha256 十六进制。

    单函数口径，签发 / 校验 / 吊销 / 存量迁移共用，避免「一处哈希一处明文」的错配。
    哈希本身非秘密（离线库、无彩虹表价值），目的是让 DB/CLI 泄露不再直接给出可用 token。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _ConnRef:
    """连接持有者（`sqlite3.Connection` 不支持弱引用，需要一层可弱引用的壳）。

    CH-459（A-3）：唯一持有者是 `Store._local.holder`（线程独占）。线程消亡即
    壳失引用 → 弱引用登记表自动出列 → Connection 析构关 fd。登记因此只留痕不保命。
    """

    __slots__ = ("conn", "__weakref__")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


class Store:
    """状态库句柄。

    CH-436：连接按**线程**隔离（threading.local），不是单连接。
    原因：`doc2md serve` 用 Flask `threaded=True`，cron 与 API 分进程共用同一库文件；
    sqlite3 的连接不允许跨线程使用，单连接实现在并发请求下必然 `ProgrammingError` → 500。
    WAL + busy_timeout 保证多连接并发读写安全（写锁串行化）。

    CH-459（A-3）：连接登记表改**弱引用**。原实现用强引用 list 跟踪连接——
    `serve` 每来一个新线程就建一个连接进表，线程结束后连接对象仍被 list 持有，
    永不 GC、fd 永不释放，请求累积到 fd 上限即服务面 500。弱引用下死线程的
    连接可被正常回收（sqlite3.Connection 析构即关 fd），登记表只留痕不保命。

    注：`sqlite3.Connection` 是 C 扩展类型、不支持弱引用，因此登记表存的是
    可弱引用的持有者 `_ConnRef`（由线程 local 独占持有，随线程消亡而回收）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: weakref.WeakSet[_ConnRef] = weakref.WeakSet()
        self._lock = threading.Lock()
        self._migrate()

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        # CH-471（STOR-7 / 决策 D6）：DDL（见本文件建表段）**不声明任何 FOREIGN KEY**，
        # 所以这条 pragma 是防御性的空转——保留它只为「若将来加 FK 则即刻生效」，当前
        # 不产生引用约束。父子完整性（docs→blocks/search、tasks→docs）**唯一机制**是
        # `repo.delete_doc`/`delete_source` 的手写级联；不得误以为 sqlite 会自动级联。
        # 防孤儿回归由 gate 层的 `test_delete_cascade`（FR-128）断言：删文档后子块与
        # 索引行必须清零、墓碑不含正文。不新增 FK 是为避开表重建与 DB_VERSION 连锁。
        conn.execute("PRAGMA foreign_keys=ON")
        # CH-471（STOR-1 / 决策 D1）：WAL + synchronous=NORMAL 是 SQLite 推荐的
        # 耐久/吞吐平衡档——**进程崩溃**（`work`/`serve` 被 OOM 干掉是低配机常态，
        # CH-448）已提交数据全部保住；仅在**断电/OS 崩溃**时可能丢最近尚未 checkpoint
        # 的一次提交。原默认 FULL 每提交一次 fsync，是文档自称「每任务固定开销主因」。
        # mmap_size/cache_size 供读密集检索（大/分片库）走内存映射，免反复系统调用。
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=268435456")   # 256MB
        conn.execute("PRAGMA cache_size=-65536")     # 64MB（负值=KB）
        holder = _ConnRef(conn)
        self._local.holder = holder          # 线程独占持有：线程死 → holder 死 → 出表
        with self._lock:
            self._conns.add(holder)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        return conn

    def _migrate(self) -> None:
        cur_ver = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if cur_ver < DB_VERSION:
            self._conn.executescript(DDL)
        # 幂等补列（schema 迁移，v2.2 遗留能力）
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(blocks)")}
        if "revision" not in cols:
            self._conn.execute("ALTER TABLE blocks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        tcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)")}
        if tcols and "source_path" not in tcols:
            # CH-448：存量库补列（异步队列的消费路径）；NULL = 非队列行，消费方直接判失败
            self._conn.execute("ALTER TABLE tasks ADD COLUMN source_path TEXT")
            self._conn.execute("ALTER TABLE tasks ADD COLUMN mode TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_tasks_queued"
                               " ON tasks(status, created_at)")
        tcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(tokens)")}
        if tcols and "role" not in tcols:
            # 方案 8.5 兼容期：存量 token 默认 admin
            self._conn.execute("ALTER TABLE tokens ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        self._migrate_token_hash()
        self._migrate_fts()

    def _migrate_token_hash(self) -> None:
        """CH-471 SVC-9：存量明文 token 就地升级为 sha256(token)。

        幂等：明文 token 一律以 `d2m_` 前缀签发，哈希是 64 位十六进制且不以 `d2m_` 开头，
        故 `token LIKE 'd2m_%'` 只会命中尚未升级的行；升级一次后再跑即空集。
        保留原 role/label/时间戳，只换主键值。旧库中已存在的活 token（客户端仍持明文）
        校验时走同一 `token_hash`，命中升级后的行 → 升级不打断在用凭证。
        """
        rows = self._conn.execute(
            "SELECT token, role, label, created_at, revoked_at FROM tokens"
            " WHERE token LIKE 'd2m_%'").fetchall()
        for r in rows:
            self._conn.execute(
                "UPDATE tokens SET token=? WHERE token=?",
                (token_hash(r["token"]), r["token"]))


    def _migrate_fts(self) -> None:
        """CH-435：FTS5 虚表不支持 ADD COLUMN，旧库（无 aux 列）需重建并回填。

        CH-471（STOR-3 / STOR-4，HIGH）：原实现先 `DROP TABLE search_blocks` 再在
        **另一个事务**里回填——drop 与回填之间崩溃即留下永久空索引（aux 列已存在，
        本迁移不再触发，检索静默失效且无告警）。改为：
        - 影子表 `search_blocks_new` 建好并**流式**回填（`fetchmany` 批插，替一次性
          `fetchall`，大库迁移不再把全部正文读进内存）；
        - 最后**单事务** `DROP 旧 + RENAME 新` 原子替换。崩溃只留下未启用的影子表，
          下次启动重跑（幂等 DROP IF EXISTS），旧索引始终可用直到替换提交。
        """
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(search_blocks)")}
        if not cols or "aux" in cols:
            return
        from .fts import cjk_aux
        self._conn.execute("DROP TABLE IF EXISTS search_blocks_new")
        self._conn.execute(
            "CREATE VIRTUAL TABLE search_blocks_new USING fts5("
            "doc_id UNINDEXED, block_id UNINDEXED, text, aux, tokenize='unicode61')")
        cur = self._conn.execute("SELECT doc_id, block_id, text FROM search_blocks")
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            with self.tx() as tx:
                tx.executemany(
                    "INSERT INTO search_blocks_new(doc_id, block_id, text, aux)"
                    " VALUES (?,?,?,?)",
                    [(r["doc_id"], r["block_id"], r["text"], cjk_aux(r["text"]))
                     for r in batch])
        with self.tx() as tx:
            tx.execute("DROP TABLE search_blocks")
            tx.execute("ALTER TABLE search_blocks_new RENAME TO search_blocks")

    # --- 事务 ---------------------------------------------------------------
    def tx(self):
        """BEGIN IMMEDIATE：写锁前置，乐观锁的第二道保险依赖它。"""
        return _Tx(self._conn)

    def q(self, sql, params=()):
        return self._conn.execute(sql, params).fetchall()

    def q1(self, sql, params=()):
        return self._conn.execute(sql, params).fetchone()

    def close(self):
        """关闭本线程连接；其他线程的连接随线程结束由 GC 释放（sqlite3 连接析构即关闭）。

        CH-459：登记表同场清理——死线程的弱引用与显式关闭的连接一并出列。
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("PRAGMA optimize")     # CH-471 STOR-1/D1：关闭前让 SQLite 按访问模式维护索引
            except sqlite3.Error:
                pass
            try:
                conn.close()
            except sqlite3.Error:
                pass
            holder = getattr(self._local, "holder", None)
            if holder is not None:
                with self._lock:
                    self._conns.discard(holder)
            self._local = threading.local()

    def live_conn_count(self) -> int:
        """存活连接数（弱引用口径：已被 GC 收走的死线程连接不计入）。

        CH-459（A-3）：`serve` threaded 模式下请求线程来了又走，该值应保持有界；
        强引用实现下它只会单调增长直到 fd 上限。测试与运维探针共用此口径。
        """
        with self._lock:
            return len(self._conns)


class _Tx:
    def __init__(self, conn):
        self.conn = conn
        self._last = None

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            # CH-473（R5-STOR-3）：COMMIT 自身失败（ENOSPC——本项目 headline 场景）
            # 时 sqlite3 的行为是**连接仍处于事务内**（失败提交不自动回滚）：
            # 残留写锁阻塞他进程，下次 BEGIN IMMEDIATE 直接报 "cannot start a
            # transaction within a transaction"。长驻进程（doc2md work 的主连接）
            # 被污染后循环必崩。失败即显式 ROLLBACK 再抛；回滚路径自身出错时
            # （连接已断）让原始异常优先，不掩盖真实病因。
            if exc_type is None:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise               # COMMIT 失败：sqlite 错误就是最优先的病因
            # exc_type 非 None = with 体已抛：回滚再失败的 sqlite 错误不得
            # 顶替原始异常（裸 raise 会把它变成传播中的异常）。吞掉回滚错误、
            # 落回 return False，让 body 的异常原样上抛——连接已断的善后
            # 交给 GC 与进程退出，此处不制造第二次病因。
        return False

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self._last = cur
        return cur

    def executemany(self, sql, seq_of_params):
        """CH-459（A-14）：批量语句入口——save_ir / reindex 的 N 次 execute 合一次调用。"""
        cur = self.conn.executemany(sql, seq_of_params)
        self._last = cur
        return cur

    @property
    def rowcount(self):
        """CH-471（STOR-10）：优先用最近一条语句游标的 rowcount，退回 connection-global
        `SELECT changes()`。原实现只用 `changes()`——它反映「本连接最后一次写」，一旦读写
        语句交错（UPDATE 之后又跑 SELECT 再取 rowcount）就会读到过期计数。游标 rowcount
        精确到该条语句，对 DML 与 `changes()` 等价、对 SELECT 返回 -1，故 -1 时回退 `changes()`
        仍给出上一条写的行数（SELECT 不重置 changes()）。纯 UPDATE 调用点行为不变。"""
        if self._last is not None and self._last.rowcount != -1:
            return self._last.rowcount
        return self.conn.execute("SELECT changes()").fetchone()[0]

