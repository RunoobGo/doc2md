"""state.sqlite —— 单一状态库（WAL；cron 与 serve 分进程共用）。

schema 覆盖本期全部落点：
- docs / blocks（+ revision，CH-409 乐观锁）
- edit_audit（before/after 全文，NFR-18 因此要求备份加密）
- deletions（删除留痕，只含 who/when/doc_id，不含正文，FR-128 / NFR-20）
- tokens（+ role，CH-407 三角色；吊销即时生效）
- tasks（+ 部分唯一索引，CH-412 并发投递防护）
- events（全局总线持久化，保留期由 cron 清理）
- search_docs / search_blocks（FTS5，编辑后 ≤5s 刷新，CH-418）
"""
from __future__ import annotations

import sqlite3
import threading
import weakref
from datetime import datetime, timezone
from pathlib import Path

DB_VERSION = 4   # state.sqlite 结构版本（user_version）；与 IR 的 SCHEMA_VERSION=14 是两套口径

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
  token TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, code TEXT NOT NULL,
  doc_id TEXT, job_id TEXT, payload_json TEXT DEFAULT '{{}}'
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_blocks USING fts5(
  doc_id UNINDEXED, block_id UNINDEXED, text, aux, tokenize='unicode61'
);

PRAGMA user_version={DB_VERSION};
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
        conn.execute("PRAGMA foreign_keys=ON")
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
        self._migrate_fts()

    def _migrate_fts(self) -> None:
        """CH-435：FTS5 虚表不支持 ADD COLUMN，旧库（无 aux 列）需重建并回填。"""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(search_blocks)")}
        if not cols or "aux" in cols:
            return
        rows = self._conn.execute(
            "SELECT doc_id, block_id, text FROM search_blocks").fetchall()
        self._conn.execute("DROP TABLE search_blocks")
        self._conn.executescript(DDL)
        from .fts import cjk_aux
        with self.tx() as tx:
            for r in rows:
                tx.execute("INSERT INTO search_blocks(doc_id, block_id, text, aux) VALUES (?,?,?,?)",
                           (r["doc_id"], r["block_id"], r["text"], cjk_aux(r["text"])))

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

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        """CH-459（A-14）：批量语句入口——save_ir / reindex 的 N 次 execute 合一次调用。"""
        return self.conn.executemany(sql, seq_of_params)

    @property
    def rowcount(self):
        return self.conn.execute("SELECT changes()").fetchone()[0]
