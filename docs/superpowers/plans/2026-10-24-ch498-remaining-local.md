# CH-498 剩余本机可闭环项 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把第十四轮审查之后**仍可在本机闭环**的开放项全部落地并取证：B-17 产品侧并发首开竞态修复、drain→真实进程池端到端补测、B-16 远端 CI 干净退出取证、B-11 真实浏览器走查、B-12 30 分钟本机长跑、§9.1 门禁分层复核定档，最后按登记流收口 CH-498。

**Architecture:** B-17 改 `doc2md/storage/db.py` 的连接建立路径——WAL 切换与迁移期 DDL 在「多进程同时首开全新库」时按有限重试消化 `database is locked`，并在建立后**实测校验** `journal_mode` 确为 `wal`（不静默降级）。其余任务分别落在：worker 端到端红线（`tests/`）、取证与文档（`docs/dev-plan.md` §12.1/§12.3/§12.7/§12.10、README、附录 F/G.6）、CI 与浏览器的外部证据采集（不改产品代码）。

**Tech Stack:** Python 3.15 / SQLite WAL / pytest（`-m gate` 红线层）/ ruff / Flask `doc2md serve` + `doc2md/static/editor.html` / GitHub Actions（`gh`）/ Playwright MCP（Chromium）。

**Spec:** `docs/review-round14-CH-495.md`（46 项发现的原始登记）+ `docs/dev-plan.md` §12.7（B 类出口条件）。本计划实施的是该轮之后残余的「本机可执行」子集。

## Global Constraints

- 不改变产物字节：`PIPELINE_VERSION` 保持 **2.17.0**；`SCHEMA_VERSION` 14 / `DB_VERSION` 9 不动（本轮不建 schema 窗口）。
- `PLAN_VERSION` 3.31 → **3.32**，且常量 / dev-plan header / §1.4 三处一致（`test_ch477_version_sync` 红线）。
- README「当前编号至 / 下一条自」与 dev-plan §10.1 一致；十一章标题上界同步。
- 计数一律以 `--junitxml` 的 `tests=` / `failures=` / `errors=` / `skipped=` / `time=` 为证据；先改正文再 `wc -l` 重算，禁止自指漂移。
- 宿主相关判断改产品代码，**不得用 `t.Skip` 掩盖**；新增红线必须跨平台可跑（Windows CI 腿也要绿）。
- 绝不吞掉真正的迁移失败：只允许对 `database is locked` 这类**瞬时并发**做有限重试，其余 `OperationalError` 原样上抛。
- 每轮收口必须跑：`pytest -m gate` → 全量 `pytest tests/` → `ruff check doc2md tests config ops` → `doc2md doctor`，且 gate 全绿无 skip。
- 提交/推送需按用户当轮指示（本轮用户已授权提交并推送）。
- 外部不可得项（Win11 真机 B-6/B-8、跨文件系统 B-14、真实语料 B-1/B-2/B-3/B-13/B-15、A3/A6 需 schema 窗口）继续只登记不实施。

---

### Task 1: B-17 产品侧修复——并发首开全新库的 WAL/DDL 竞态

**Files:**
- Modify: `doc2md/storage/db.py:236-247`（`_new_conn` 的 `PRAGMA journal_mode=WAL`）与迁移入口
- Test: `tests/test_ch498_db_init_race.py`（新文件，2 项 gate）

**Interfaces:**
- Consumes: `Store(path)`（既有公开构造，签名不变）。
- Produces: 私有辅助 `_set_wal(conn)`（同文件内）；行为保证——N 个进程/线程同时首开同一路径，全部成功且 `PRAGMA journal_mode == 'wal'`。

- [ ] **Step 1: 写失败测试（线程扇出，同一全新路径）**

新建 `tests/test_ch498_db_init_race.py`：

```python
"""CH-498 / B-17：多方并发首开全新 state.sqlite 的建库竞态红线。

根因（本机实测）：`_new_conn` 的 `PRAGMA journal_mode=WAL` 在另一进程正创建/迁移
同一库文件时返回 `sqlite3.OperationalError: database is locked`——busy_timeout 对
「切 WAL 需要独占」这条语句不生效，SQLite 直接判忙。原实现把它当未知异常上抛，
CLI 侧落 E5000、常驻 worker 直接退出。

红线形态：先在**同进程多线程**里复现（CI 跨平台稳定），再用 **4 个真实子进程**
跑 `doc2md work --root <fresh> --status` 复现生产形态。两者都只允许成功，
且必须实测 `journal_mode == 'wal'`——不接受静默退回 delete/legacy。
"""
from __future__ import annotations

import concurrent.futures as cf
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _journal_mode(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.gate
def test_concurrent_first_open_of_fresh_store_is_safe(tmp_path):
    from doc2md.storage.db import Store

    db = tmp_path / "state.sqlite"
    barrier = cf.ThreadPoolExecutor(max_workers=8)

    def open_it(_i):
        store = Store(db)
        return store.execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"]

    try:
        results = list(barrier.map(open_it, range(8)))
    finally:
        barrier.shutdown()
    assert results == [0] * 8, results
    assert _journal_mode(db) == "wal"
```

- [ ] **Step 2: 加子进程形态红线（生产等价）**

同文件追加：

```python
@pytest.mark.gate
def test_concurrent_cli_first_open_fresh_root(tmp_path):
    """4 个真实 `doc2md work --status` 进程同时首开同一全新根，必须全部退 0。"""
    for d in ("src", "out", "logs", "tmp"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    binp = REPO_ROOT / ".venv/bin/doc2md"
    if not binp.exists():                     # CI/venv 路径差异：用当前解释器兜底
        cmd = [sys.executable, "-m", "doc2md"]
    else:
        cmd = [str(binp)]
    procs = [subprocess.Popen(cmd + ["work", "--root", str(tmp_path), "--status"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(4)]
    outs = [(p.wait(timeout=120), p.communicate(timeout=30)[1]) for p in procs]
    bad = [(rc, err) for rc, err in outs if rc != 0]
    assert not bad, bad
    assert _journal_mode(tmp_path / "state.sqlite") == "wal"
```

Run: `.venv/bin/python -m pytest tests/test_ch498_db_init_race.py -q`
Expected: 至少一条 FAIL（`database is locked` → E5000 / 未捕获 OperationalError）。若首跑偶然绿，把子进程数提到 6 或多跑几轮直到暴露；**不得**为了让它红而断言脆弱形态——红线本身断言的是「必须全绿」，偶然绿只是没抽中。

- [ ] **Step 3: 实现有限重试 + 建库后实测校验**

在 `doc2md/storage/db.py` 的 `Store` 内加私有方法（放在 `_new_conn` 之前）：

```python
    def _set_wal(self, conn: sqlite3.Connection) -> None:
        """切 WAL：并发首开同一全新库时，另一进程正持独占建库 → SQLite 直接判忙。

        `busy_timeout` 对 `PRAGMA journal_mode=WAL` 无效（该语句要求独占，SQLite
        选择立即 SQLITE_BUSY 而不是等待），故这里自行有限重试。只消化「database is
        locked」，其余 OperationalError 原样上抛——绝不把真正的迁移失败当成并发噪声。
        """
        delay = 0.02
        for attempt in range(200):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                time.sleep(min(delay * (1.0 + attempt * 0.05), 0.5))
                continue
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                raise RuntimeError(
                    f"state.sqlite journal_mode is not WAL after {attempt + 1} "
                    "attempts; refusing to run without WAL")
            return
        raise RuntimeError(
            "state.sqlite stayed locked while switching to WAL (>200 attempts); "
            "another process may be holding the database (refusing to run)")
```

`_new_conn` 中原 `conn.execute("PRAGMA journal_mode=WAL")` 改为 `self._set_wal(conn)`；补 `import time`（若未导入）。

- [ ] **Step 4: 迁移期 DDL 同样只消化「locked」**

`_migrate` 的 `self._conn.executescript(DDL)` 与各 `PRAGMA table_info` 探测本身不重试；若实测仍能在并发首开下抛 locked（Step 5 的复跑统计决定），则加同口径私有包装 `_run_locked_ok(fn)`：**仅**捕获 `"database is locked"`、按同样退避重试、其余上抛。不要给 `_add_column` 加吞——它已按 `duplicate column name` 精确处置。

- [ ] **Step 5: 复验红线与竞态消失**

Run:
```bash
.venv/bin/python -m pytest tests/test_ch498_db_init_race.py -q
for i in 1 2 3 4 5 6 7 8; do .venv/bin/python /tmp/b17_repro2.py 2>&1 | tail -1; done
```
Expected: 两条红线全绿；`/tmp/b17_repro2.py`（15 轮 × 4 进程，修复前 3/15 轮失败）**连续 8 次 `failure_trials=0`**。若仍有失败轮次，提高重试预算或改为首开文件锁，并如实登记。

- [ ] **Step 6: 全链取证**

Run: `pytest -m gate --junitxml` → 全量 → `ruff check doc2md tests config ops` → `doc2md doctor`。
Expected: gate 全绿 0 skip（项数 +2）、全量 0 failed、ruff 无输出、doctor 退 0 且 pipeline 2.17.0。

---

### Task 2: drain → 真实进程池的端到端缝线红线（补 parked 覆盖缺口）

**Files:**
- Test: `tests/test_ch498_drain_e2e.py`（新文件，1 项 gate）
- Reference: `doc2md/worker.py`（`_claim_batch` / `drain_once`）、`tests/test_ch497_local.py`

**Interfaces:**
- Consumes: `worker.drain_once(cfg, root=..., n=...)`（既有签名）、`doc2md work --enqueue`。
- Produces: 无新接口；证明「claim 出的 plans 真被同一个进程池按逐作业参数消费」这条缝有红线。

- [ ] **Step 1: 写红线**

```python
"""CH-498：drain_once 的完整缝线——真实入队 → 认领 → 单池 → 产物落库。

CH-497 把两侧各自钉住了（payload 用 mock 断、`run_batch(plans=…)` 用真实池断），
中间「drain_once 把 claim 到的多档位作业交给一个池并逐参数消费」只靠人工夹具。
本项用两件真语料（默认档 + `single` 档）走完整路径，要求两份产物都落库、
`tasks` 全 `done`、且 `--enqueue` 的 mode 与落库文档的转换档位一致。
"""
```

要点（实现时按 `tests/` 既有 fixture 风格落地）：
1. 用 `tmp_path` 造根 + `Store` 初始化，`--enqueue` 两个不同文件（`tests/golden/small_3p.pdf` 默认档、`tests/golden/links_pdf.pdf` 传 `mode=single`——若 CLI 入队不带 mode，则直接写 `tasks` 行的 `mode` 字段，保持与 worker 认领口径一致）。
2. 调 `worker.drain_once(cfg, root=tmp_path, n=2)`。
3. 断言：返回结果两条均 `status=="ok"`；`Store` 内 `tasks` 无 `queued`/`running`；`docs` 行数 2；两份 zip 产物存在且 `content.md` 非空。
4. 断言 `run_batch` 在整次 drain 中**只被调用一次**（用一次性 patch 计数，保留真实实现转发）。

Run: `.venv/bin/python -m pytest tests/test_ch498_drain_e2e.py -q` → PASS（本项是对既有实现的固化，不应改产品代码；若失败即暴露真 bug，按 B-17 同法修复并登记）。

- [ ] **Step 2: 计时复核**

Run: `.venv/bin/python -m pytest -m gate --junitxml=/tmp/ch498-gate.xml -q`
Expected: 新增项自身耗时 ≤ ~8s，整体 gate 仍 < 60s（本机带 35–48s）。若把 gate 推过 60s，登记并在 Task 6 的分层复核里处置，不得静默放宽判据。

---

### Task 3: B-16 远端 CI 干净退出取证（本机已可采集）

**Files:**
- Modify: `docs/dev-plan.md` §12.7 B-16 行、CH-498 登记行、§12.10

- [ ] Step 1: `gh run list --limit 3` 取本轮 push（`43470b1`）触发的 `ci` run；`gh run watch <id>` 或轮询到 `completed`。
- [ ] Step 2: `gh run view <id> --json name,conclusion,jobs` 逐 job 判读：`merge-gate` / `lint` / `windows-suite`（含 full suite + doctor）/ `full-suite` 全 `success`；任一失败 → 取 `gh run view --log-failed` 尾部归因，按项目纪律改产品代码修复后重推。
- [ ] Step 3: 若 `windows-suite` 绿，即取得「Windows 真机带 gate 干净退出」证据；把 run id、耗时、项数写进 §12.7 B-16 与 CH-498 登记行。`full-matrix`（周度三版本腿）仅在 ci 绿后用 `gh workflow run full-matrix.yml` 触发一次，取 3.12 full-suite 干净退出。
- [ ] Step 4: 明确 B-16 的**另一半**（`--minutes 30` 长跑）由 Task 5 处置；两半齐了才可标「已关闭」。

不能自动化的部分：无。`api.github.com` 本机可达（已实测 200）。

---

### Task 4: B-11 真实浏览器走查（Playwright MCP，Chromium）

**Files:**
- Modify: `docs/dev-plan.md` §12.7 B-11 行 + CH-498 登记行
- Artifacts: 走查截图（会话内，不落库）

- [ ] Step 1: 造一个隔离工作根（`mktemp -d`，`src/out/logs/tmp` + `state.sqlite`），转换 2–3 份真语料入库存，`doc2md serve --root <r> --port 0` 起服务并从 CLI 取实际端口（CH-496 的端口真值改动正好被这条走查使用），签发 admin/editor/reader 三种令牌。
- [ ] Step 2: Playwright MCP 打开 `doc2md/static/editor.html`，逐项走 §12.7 既有契约清单：① 队列五态可视化 + 失败原因；② 编辑块 → PATCH 成功 → 审计面板展示前后正文；③ 历史面板「回滚到此版本」→ 版本号与正文回退；④ 检索命中与 `has_more`；⑤ 用 reader 令牌做 editor 专属操作 → 401/403 是否有可见错误条（CH-495 / R14-GUI-4 的收口点）。每步记录 console 是否报错。
- [ ] Step 3: 发现的缺陷按 A/B 分流：本机可修且可自验 → 当场改 `editor.html`/产品码并补红线；需人工判断的交互取舍 → 登记新 B 项。
- [ ] Step 4: 把「Chromium 真浏览器走查已完成 + run 记录」写进 §12.7 B-11；Firefox/WebKit 与 Safari 仍属人工/其他引擎，保留登记。

约束：浏览器只访问 `127.0.0.1`，服务用后即停；不触碰用户真实工作根 `/Volumes/fx/Object/Doc2md` 之外的库（临时根用 `--keep` 便于取证，事后自行删除）。

---

### Task 5: B-12 30 分钟本机长跑（夹具已在 CH-497 落地）

**Files:**
- Modify: `docs/dev-plan.md` §12.7 B-12 行 + CH-498 登记行

- [ ] Step 1: 在 Task 1 修复落地、gate 全绿之后启动（避免与其他重型测试并发失真）：
```bash
.venv/bin/python ops/bench/soak_resident.py --minutes 30 --root /tmp/soak30 --keep --workers 2 --interval 20
```
后台运行，结束判据 exit 0；`SUMMARY` 落 `/tmp/soak30.out`。
- [ ] Step 2: 从 `/tmp/soak30/logs/work*.jsonl` 抓 `WORK_RECLAIM` 行数与 `tasks` 终态分布（`sqlite3` 只读查询，不写库）。
- [ ] Step 3: 回填 §12.7 B-12：`cycles` / RSS 首末 / FD 首末 / `failed` / `drained` / reclaim 计数；据此把 B-12 从「夹具就绪」升为「本机 30 分钟档已验」，Windows 侧另留登记。
- [ ] Step 4: 若 exit≠0：保留 `/tmp/soak30`，按 `worker_stderr_tail` 与 `drain_error` 归因，登记新 B 项或修复后重跑；**不得**为凑绿放宽判据。
- [ ] Step 5: 跑完删除自建的 `/tmp/soak30`（用户未要求留存时）。

---

### Task 6: §9.1 门禁分层复核——把「< 60s」定档

**Files:**
- Modify: `docs/dev-plan.md` §9.1（DoD 门禁耗时行）、§12.1、README 相应口径行

- [ ] Step 1: 出数：`pytest -m gate --durations=15 --junitxml=/tmp/ch498-gate.xml -q`，按文件归因耗时（`--junitxml` 的 per-case `time` 求和），列出 top 5 慢项与占比。
- [ ] Step 2: 判定并写进文档（本项目「功能完整优先，不为省时间撤红线」）：
  - 若本机 gate 与 Windows CI 腿都在 60s 内 → **恢复 `<60s` 为硬判据**，并把「慢在两条 320 页真实转换、属机器 bound」写为附注，同时在 §9.1 记录新的采样区间与项数（项数仍是主口径）。
  - 若某条腿超 60s → 保持「量级达标 + 项数为硬口径」，并新增一条 gate 红线锁住「慢项清单不外扩」（top 慢项文件名 + 项数不变即绿），把扩容决策显式化。
- [ ] Step 3: 撤除/保留 marker 不在本轮实施（撤红线属功能覆盖决策）；只在文档登记结论。

---

### Task 7: CH-498 登记与文档同源收口

**Files:**
- Modify: `doc2md/__init__.py`（`PLAN_VERSION = "3.32"`）
- Modify: `docs/dev-plan.md`（header、§1.4、§10.1、十一章标题、CH-498 登记行、§12.1、§12.3、§12.7、§12.10、附录 F/G.6）
- Modify: `README.md`（编号、gate 数与耗时、非红线链尾、文档地图实测）
- Test: `tests/test_ch477_version_sync.py`、`tests/test_ch491_doc_sync.py`

- [ ] Step 1: 先写正文（CH-498 登记行 + §12.7 B-16/B-17/B-12/B-11 + §9.1 定档 + A3/A6 继续延期裁决），后重算数字。
- [ ] Step 2: `PLAN_VERSION` 3.31→3.32（常量 + header + §1.4 三处）。
- [ ] Step 3: 实测重算：`find tests -maxdepth 1 -name '*.py' | wc -l`、`wc -l tests/*.py | tail -1`、`--collect-only -q` 尾行、`wc -l docs/*.md`、`wc -l docs/dev-plan.md`、`wc -l README.md`；附录 G.6 增两行（`test_ch498_db_init_race.py`、`test_ch498_drain_e2e.py`）。
- [ ] Step 4: README 非红线链尾用实测 `full − gate`，不预估。
- [ ] Step 5: 全链取证（gate/full/ruff/doctor + 两条文档红线），把 `--junitxml` 数字回填登记行。
- [ ] Step 6: 更新项目记忆（B 项状态、新出口条件、durable lessons）。
- [ ] Step 7: `git status --short` 复核无杂物 → 提交 → `git push origin main`（**不要**设 `GIT_SSH_COMMAND`，仓库 `core.sshCommand` 已带 deploy key；覆盖它会 publickey 失败）。推送后再取一次远端 CI 结果，把 run id 记进 §12.7。

---

## 明确排除（本轮不实施）

| 项 | 原因 | 后续动作 |
| --- | --- | --- |
| B-6 / B-8 Win11 真机 spawn 池与跨机基准、B-14 跨文件系统语义 | 本机无第二台目标机/异构文件系统 | 需真机；B-7 的 Windows CI 腿已能部分替代，结论按 run id 记 |
| B-1/B-2/B-3/B-13/B-15 真实语料类 | 需真实世界语料（损坏 ToUnicode、双栏、注入稿、Word 模板、中文标题） | 由用户提供语料后另立 CH |
| A3 标记语义 IR 位 / A6 链接块粗斜体 | 改变产物字节，需 schema/管线窗口与全语料重烤；`docs/adr-o11-inline-marks.md` 已定档「等 T2 窗口」 | 本轮**裁决：继续延期**，无窗口即不动 |
| Safari 走查 | 本机 Playwright 无 WebKit 引擎可用面（Task 4 先做 Chromium） | 人工走查或另配环境 |
| 撤 gate marker | 属功能覆盖决策，非性能决策 | Task 6 只定档判据，不动覆盖 |

## Self-Review

- **Spec coverage**：B-17 → Task 1（含根因与复现脚本证据）；parked drain 缝线 → Task 2；B-16 → Task 3；B-11 → Task 4；B-12 → Task 5；§9.1 可选项 → Task 6；收口与版本 → Task 7。不可得项在「明确排除」逐条落位。
- **Placeholder scan**：Task 1/2 给出完整可执行代码；Task 3–6 是取证/判定任务，其「内容」即命令与判据，无 TBD；Task 7 的数字槽位由 Step 5 实测填充。
- **Type consistency**：`Store(path)` 签名不变；`_set_wal(conn)` 仅同文件私有；`drain_once(cfg, root=…, n=…)` 沿用既有；两条新红线均 `@pytest.mark.gate`。
