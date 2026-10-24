# CH-499 本机可实施项（B-27 / B-3 / B-14）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把第十五轮之后**仍可在本机闭环**的三项开放登记真正落地：B-27（running 行的所有权/租约，终结「合法长作业被 `reclaim_stale` 误抢 → 重复转换 + 假 `rejected`」）、B-3（注入归因去循环：证据面从算子 JSON 扩到源文档正文）、B-14（跨文件系统语义取证：本机 APFS 之外的第二/第三种文件系统实测），最后按项目登记流收口 CH-499（`DB_VERSION` 9→10、`PLAN_VERSION` 3.32→3.33）。

**Architecture:**
- B-27 落在存储 + 队列两层：`doc2md/storage/db.py`（v10 两列 + 幂等补列）、`doc2md/worker.py`（`claim()` 打租约、`reclaim_stale()` 三分支判定、转换在途期间的续租线程）。判定语义 = **租约过期**（进程死/被杀）或**硬上限超时**（进程活着但卡死）才回收；无租约的历史行沿用 `started_at` 窗口（CH-448/CH-469/CH-496 语义不回潮）。
- B-3 落在 `doc2md/llm.py`：`OpValidator._attribute` 从「只看 op 自身 JSON」改为「op 自身 ∪ 本次校验的源块正文」，文档侧证据按实例缓存（惰性、每次 repair 至多扫一遍）。
- B-14 是取证轮（不改产品，除非实测抓出缺陷）：`hdiutil` 在 `/tmp` 造 FAT32 / exFAT（或大小写敏感 APFS）映像并挂载，对 `security/dirguard.py` 的 realpath 收敛、`Store` 的 0600 私有性、`names.py` 消毒与 `Store` 建库各跑一轮，结论回填 §12.7 B-14。

**Tech Stack:** Python 3.15.0 / SQLite 3.54.1（WAL、`ALTER TABLE … DROP COLUMN` 可用）/ pytest（`-m gate` 红线层）/ ruff / reportlab 长作业探针 `/tmp/reclaim_probe.py` / macOS `hdiutil` + `diskutil`。

**Spec:** `docs/dev-plan.md` §12.7（B-27/B-3/B-14 行的出口条件）+ `docs/review-round7-CH-476.md:127-128`（B-27 的原始张力描述）。

## Global Constraints

- `PIPELINE_VERSION` 保持 **2.17.0**（产物字节不变；黄金逐字节复现必须实测确认）；`SCHEMA_VERSION` 保持 **14**（IR 结构不动）。
- **`DB_VERSION` 9 → 10**：`tasks.owner` + `tasks.lease_expires_at`（`TEXT NOT NULL DEFAULT ''`）。DDL 无条件幂等重放 + `_add_column` 补列；`user_version` 高于本构建仍 fail-closed。同步改 dev-plan §1.4 版本表该行数字与升版说明（`test_ch477_version_sync` 红线）。
- `PLAN_VERSION` 3.32 → **3.33**；常量 / dev-plan header / §1.4「本版 v3.33」三处一致；README「当前编号至 `CH-499`／下一条 `CH-500`」与 §10.1、十一章标题上界同步。
- 新配置键 `[perf].job_lease_cap_minutes`（默认 `240`，`0` = 不设上限）必须**三处同源**：`config/default_toml.py`、`config/loader.SPEC`、`config/doc2md.example.toml`（`test_ch469_ops.py::…keys_three_way…` 钉住）+ dev-plan 配置表加一行。
- 计数一律以 `--junitxml` 的 `tests=` / `failures=` / `errors=` / `skipped=` / `time=` 为证据；先写正文再 `wc -l`，禁止自指漂移。
- 宿主相关判断改产品代码，**不得用 `t.Skip`/`xfail` 掩盖**；新增红线必须在 Windows CI 腿也能跑（不依赖 POSIX 专有语义；若必须依赖，则按平台分支断言，与 `is_private` 同口径）。
- **不用进程存活性判定**（`os.kill(pid, 0)` 在 Windows 是广播 Ctrl+C，bpo-14484 / X-18 已付过代价）——租约只能靠心跳续期。
- 绝不吞掉真正的迁移失败：`Store` 建库路径只消化 `database is locked` 这类瞬时并发。
- 每次收口跑：`pytest -m gate` → 全量 `pytest tests/` → `ruff check doc2md tests config ops` → `doc2md doctor`；gate 层**恒 0 skipped**。
- 用户已授权提交并推送（`git push origin main`，**不要**设 `GIT_SSH_COMMAND`：仓库 `core.sshCommand` 已带部署密钥）。
- 外部不可得项继续只登记：B-6/B-8（Win11 真机吞吐）、B-9 Windows 半侧（`.ps1` / `Register-ScheduledTask`）、B-11 Chrome/Safari 人工走查、B-12 目标机档与真实千页语料、B-1/B-2/B-13/B-15 真实语料误报面、CH-470 分支保护决策（改仓库可见工作流，需用户明确同意）。
- **A3 / A6（行内 marks span 模型）本轮明确不动**：`docs/adr-o11-inline-marks.md` 的触发条件 T2 是「任一必然 bump `SCHEMA_VERSION` 的变更立项」；本轮 bump 的是 `DB_VERSION`（状态库），IR schema 未动 → 触发条件不成立，按 ADR §5「触发前任何中间态都是纯负债」保持推迟。

---

### Task 1: B-27 — running 行的所有权/租约（`DB_VERSION` 9→10）

**Files:**
- Modify: `doc2md/storage/db.py:23`（`DB_VERSION`）、`:120-132`（`tasks` DDL）、`:378-393`（`_migrate` 补列）
- Modify: `doc2md/worker.py:61-82`（`claim`）、`:89-118`（`reclaim_stale`）、`:213-254`（`_claim_batch` 续租）
- Modify: `doc2md/config/default_toml.py`（`[perf]`）、`doc2md/config/loader.py:133-135`（SPEC）、`config/doc2md.example.toml`
- Test: `tests/test_ch499_lease.py`（新文件，≥7 项 gate）
- Evidence: `/tmp/reclaim_probe.py` 复跑（B-27 出口判据）

**Interfaces:**
- Produces: `claim(store, limit, *, lease_seconds: int = 0, owner: str = "") -> list[dict]`（旧调用 `claim(store, 2)` 行为逐字节不变：无租约行）；`reclaim_stale(store, cfg) -> int`（签名不变，判定改为「租约过期 ∪ 无租约且 `started_at` 超窗 ∪ 超硬上限」）；私有 `_lease_keeper(store, owner, job_ids, lease_s)`（上下文管理器，进出各一次，退出即 join，无线程渗漏）。
- Consumes: `Store.tx()`、`now_iso()`、`config.get(cfg, "perf.stale_job_minutes"/"perf.job_lease_cap_minutes")`。

- [ ] **Step 1: 写失败测试**（`tests/test_ch499_lease.py`）

七条 gate 红线，形态必须能失败（每条附反向验证）：

1. `test_claim_with_lease_stamps_owner_and_expiry` — `claim(..., lease_seconds=60, owner="w1")` 后 `owner`/`lease_expires_at` 非空且 expiry ≈ now+60s。
2. `test_claim_without_lease_keeps_legacy_shape` — `claim(store, 2)` 两列恒 `''`（老调用方零行为变化）。
3. `test_reclaim_skips_row_with_live_lease` — **B-27 本体**：`started_at` 远早于窗口（如 2020 年）但 `lease_expires_at` 在未来 → `reclaim_stale()` 返回 0、行仍 `running`。
4. `test_reclaim_takes_expired_lease_and_clears_ownership` — expiry 在过去 → 返回 1、状态回 `queued`、`owner`/`lease_expires_at` 清空（真死进程仍被回收）。
5. `test_reclaim_legacy_unleased_row_still_uses_started_at_window` — 无租约 + `started_at` 超窗 → 1，第二次 0（与 `test_ch496_local.py::test_arch_reclaim_stale_counts_actual_rows` 同语义，不得回潮）。
6. `test_reclaim_hard_cap_rescues_wedged_worker` — 租约一直在续（未来 expiry）但 `started_at` 早于 `job_lease_cap_minutes` → 返回 1；`cap=0` → 返回 0（卡死进程有上界，运维可关）。
7. `test_lease_keeper_renews_and_leaves_no_thread` — 假 `run_batch` 阻塞 1.2×间隔，`_lease_keeper` 期间 expiry 前移；退出后 `threading.enumerate()` 回到基线（X-17 渗漏面）。

外加 `test_db_v10_migration_backfills_lease_columns`（非 gate 或 gate 二选一，按项数预算定）：新库两列存在；用 `ALTER TABLE tasks DROP COLUMN` 造出 v9 形态 + `PRAGMA user_version=9` → 重开 `Store` 补齐两列且 `user_version == DB_VERSION == 10`；再开一次仍幂等。

- [ ] **Step 2: 跑红**：`.venv/bin/python -m pytest tests/test_ch499_lease.py -q` → 期望 7 fail（列不存在 / 参数不存在）。
- [ ] **Step 3: 实现存储层**：`DB_VERSION = 10`；`tasks` DDL 加两列 + v10 注释行；`_migrate` 用既有 `_add_column` 幂等补列（无条件探测，与 `del_seq`/`force` 同段）。
- [ ] **Step 4: 实现队列层**：`claim()` 打租约；`reclaim_stale()` 构造单一谓词（同一 SQL 片段用于 SELECT 与 UPDATE，UPDATE 再带 `AND status='running'`）；`_lease_keeper` 续租线程（间隔 = `max(1.0, min(60.0, lease_s/3))`；每次 `UPDATE tasks SET lease_expires_at=? WHERE owner=? AND status='running' AND job_id IN (…)`)；`_claim_batch` 在 `run_batch` 外层套 keeper，仅在 `stale_job_minutes>0` 且有已认领带路径作业时启用。
- [ ] **Step 5: 配置键三处同源** + dev-plan 配置表一行。
- [ ] **Step 6: 跑绿 + 回归**：`.venv/bin/python -m pytest tests/test_ch499_lease.py tests/test_ch496_local.py tests/test_ch490_fixes.py tests/test_perf_lowend.py tests/test_ch469_service.py -q` 全绿（心跳/回收计数不得回潮）。
- [ ] **Step 7: 变异反证**：临时把 `reclaim_stale` 谓词退回「只看 `started_at`」→ 第 3 条必须红；临时删掉续租 → 第 7 条必须红。记录实测。
- [ ] **Step 8: B-27 出口取证**：复跑 `/tmp/reclaim_probe.py`（`DOC2MD_HOME` 沙箱、`stale_job_minutes=1`、3000 页 PDF、A/B 双进程 `work --once`），判据 = B 侧 `reclaimed_stale=0` 且 `rejected=0`，且 A 正常 `done`、库面/产物自洽。
- [ ] **Step 9: gate + 全量 + ruff + doctor + 黄金逐字节**（`PIPELINE_VERSION` 不动的前提是产物字节不变；黄金腿必须实跑确认）。

---

### Task 2: B-3 — 注入归因去循环（证据面扩到源文档正文）

**Files:**
- Modify: `doc2md/llm.py:399-403`（`_attribute`）、`OpValidator.__init__` / `apply`（缓存位）
- Test: `tests/test_ch499_attribution.py`（新文件，≥3 项 gate）+ `tests/test_t2_llm_server.py`（既有口径复核）

**Interfaces:**
- Produces: `_attribute(self, op) -> str`（staticmethod → 实例方法）；`OpValidator._doc_injection_hint`（惰性缓存，`None` = 未扫）。归因取值集合不变（`injection_candidate` / `model_hallucination`），响应契约与错误码不变。
- Consumes: `scan_injection()`（含 CH-476 预检优化）、`OpValidator` 已有的 `blocks`。

- [ ] **Step 1: 写失败测试**
  1. `test_attribution_flags_injection_from_document_text` — 源块正文含注入话术（如「ignore all previous instructions」），op 只带 target id（**不**复述话术）→ `injection_candidate`（今天的漏报形态，本轮靶点）。
  2. `test_attribution_keeps_op_blob_signal` — op 自身含话术、文档干净 → `injection_candidate`（CH-431 既有口径不回潮）。
  3. `test_attribution_model_hallucination_when_both_clean` — 两处都干净 → `model_hallucination`。
  4. `test_doc_hint_scan_is_lazy_and_cached` — 无 rejection 时 `v._doc_injection_hint is None`（happy path 零额外扫描）；多 rejection 只扫一遍（用计数器 monkeypatch `scan_injection` 或缓存位前后对照）。
- [ ] **Step 2: 跑红 → 实现 → 跑绿**（缓存位 + `or` 双证据；文档侧 blob 用块 `text` 逐块过 `scan_injection`，不 `json.dumps` 整份 IR——避免大文档的一次性字符串放大，成本口径与 CH-476/R7-PERF-a 的预检一致）。
- [ ] **Step 3: 产物字节面确认**：`offline_llm` 只产白名单内合法 op，rejection 仅来自探针；黄金样本 `full` 档无 rejection → `attribution` 串不进产物。跑黄金逐字节腿确认；若产物变了即停下并重新评估 `PIPELINE_VERSION`（不在本轮默认路径）。
- [ ] **Step 4: 登记口径**：本机自建语料（中/英各若干注入话术）验证词表命中面；**真实注入语料的误报/漏报率仍留 B-3**（外部语料，不实施），§12.7 行改写为「去循环已落地 + 误报面待真实语料」。

---

### Task 3: B-14 — 跨文件系统语义取证（本机第二/第三种文件系统）

**Files:**
- Evidence only（默认不改产品）；若实测抓出缺陷 → 就地修并补 gate 红线，登记为 CH-499 的 A 类小修。
- Test（若有缺陷才写）：`tests/test_ch499_crossfs.py`

- [ ] **Step 1: 造卷**（`/tmp` 下映像，跑毕 `hdiutil detach` + 删映像，不碰用户 `/Volumes/fx` 内容）：尝试 `MS-DOS FAT32`、`ExFAT`；若 exFAT 不可造，用「大小写敏感 APFS」+「JHFS+/FAT」补足两种，并把「exFAT 未能造卷」如实登记。
- [ ] **Step 2: 每项文件系统实测四件事**：① `os.chmod(0o600)` 后 `stat` 模式位是否真实生效 + `Store` 私有性判定（`is_private()` / `test_arch_db_permissions_are_private` 的口径在该卷上的结果）；② `os.symlink` 是否支持（FAT 系应 `EOPNOTSUPP`）与 `security/dirguard.py` 的 realpath 收敛在无软链卷上的行为；③ `names.py` 消毒 + 非 ASCII/emoji 文件名写入；④ `Store` 建库 + WAL + 锁文件 + 一次真实 `convert`。
- [ ] **Step 3: 结论表回填 §12.7 B-14**（逐文件系统、逐判定项，含「装饰性权限位」这类必须暴露的语义），并明确剩余面（SMB/网络卷、Windows junction 真机 → 仍开放，归 B-6/B-8/B-9 的真机腿）。
- [ ] **Step 4: 清理**：确认 `/Volumes` 无残留挂载、`/tmp` 无残留映像；`git status` 干净。

---

### Task 4: 登记与收口（CH-499）

- [ ] `docs/dev-plan.md`：§1.4 版本表 `DB_VERSION` 9→10 + 升版说明；配置表新增 `[perf].job_lease_cap_minutes` 行；§6/§631 遗骸回收段改写为租约口径；§12.7 B-27/B-3/B-14 三行按实况改写（含出口判据取证数字）；§12.10 新增 v3.33 行；十一章标题上界与 §10.1/README 编号对账。
- [ ] `README.md`：验收段补 CH-499 本机数字（先写正文再 `wc -l`）。
- [ ] 附录 G.6 活锚点与历史口径对照串同步。
- [ ] 版本升位四处对账 grep 复核（`3.32` 命中仅剩历史叙述）。
- [ ] gate（junit 计数）→ 全量 → ruff → doctor → 黄金逐字节；新增红线计入 gate 项数（文档口径同步）。
- [ ] `git commit` + `git push origin main`（不设 `GIT_SSH_COMMAND`）→ `gh run watch` 取六作业证据（含 `windows-suite` 的 gate 恒 0 skipped）→ 回填 §12.1/README。
- [ ] 记忆文件与账本（`.superpowers/sdd/2026-10-24-ch499-host-implementable/progress.md`）更新。

---

## 验收判据（Definition of Done）

- B-27：`reclaim_stale` 不再抢走「租约在续」的在途作业；`/tmp/reclaim_probe.py` 复跑 `reclaimed_stale=0` 且 `rejected=0`；同时「无心跳/超硬上限」两类真死场景仍被回收（第 4/6 条红线）。`DB_VERSION=10` 迁移对存量库幂等补齐，旧库升级后仍可用（新增迁移红线 + 既有 `test_ch473_fixes` 迁移链全绿）。
- B-3：源文档驱动的注入不再被误归为 `model_hallucination`（第 1 条红线）；既有 op-blob 归因与 `model_hallucination` 分流不回潮；happy path 不多扫一遍正文。
- B-14：本机至少两种**非 APFS 区分大小写默认卷**的实测结论表（权限位、软链、消毒、建库四件），缺陷（若有）落地并钉红线。
- 收口：gate 全绿且 0 skipped、全量 0 failed 0 error、ruff 全绿、doctor 退 0、黄金逐字节全过、远端六作业全绿并取到 Windows 腿数字。
