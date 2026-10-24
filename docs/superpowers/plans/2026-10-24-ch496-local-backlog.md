# CH-496 本机可执行待办实施计划

> **For agentic workers:** 本计划在单会话内顺序执行，每个任务自带测试与门禁复核。用户未要求提交，**不 commit / 不 push**。

**Goal:** 消化 CH-495 登记后剩余「本机可验证」的代码/界面项，把需要外部环境的项继续留在登记册。

**Architecture:** 先做零行为变更的收敛（F11/F9/F7），再做局部缺陷修复（B5/G8/架构 4 项），最后做需要产物字节或浏览器验证的项（A5/G4'/B-11），收口时统一登记 CH-496、升版本、同源重算文档计数。

**Tech Stack:** Python 3.15（`.venv`）、Flask、SQLite WAL、原生 JS（editor.html）、pytest（gate/full）、ruff。

**Spec:** `docs/review-round14-CH-495.md` + `docs/dev-plan.md` §12.7 待办表。

## Global Constraints

- 不提交、不推送；HEAD 保持 `8536783`。
- 每次改动后本机验证：目标测试 → `pytest -m gate` → 全量 → `ruff check doc2md tests` → `doc2md doctor`。
- 证据计数只认 `--junitxml` 的 `tests=` 属性。
- 对外接口与既有测试兼容；测试里的错误码字面量保留为线上口径（oracle）。
- 产物字节若变化必须升 `PIPELINE_VERSION`；版本三处（`doc2md/__init__.py` / `pyproject.toml` / README·dev-plan）同步。

---

### Task 1: F11 — mode 白名单单一来源

**Files:** `doc2md/config/loader.py`、`doc2md/config/__init__.py`、`doc2md/api.py:73-77`、`doc2md/cli.py:159`、`tests/test_ch496_local.py`（新建）

- [ ] 在 `config/loader.py` 定义 `MODES = ("auto", "single", "two_phase")`，`s2.mode` 用 `_enum(*MODES)`。
- [ ] `config/__init__.py` 导出 `MODES`。
- [ ] `api._MODES` 删除，校验改 `config.MODES`；注释去掉 `cli.py:107` 死引用。
- [ ] `cli --mode choices=config.MODES`。
- [ ] 门禁测试：AST 检查 api 不再定义 `_MODES`；argparse choices 与 `config.MODES` 相等；loader 的 `s2.mode` 白名单集合相等。

### Task 2: F9 — sweep_tmp 迁到 ops 层

**Files:** 新建 `doc2md/ops/tmpclean.py`；`doc2md/cron.py`、`doc2md/api.py:21`、`doc2md/ops/drill.py:6`、`doc2md/config/loader.py:302,315`、`tests/test_ch473_fixes.py:554`、`tests/test_ch476_fixes.py:984`、`tests/test_ch485_ops.py:118`、`docs/dev-plan.md`。

- [ ] 原样搬移 `sweep_tmp`（含 docstring 与 `DRILL_DIR_NAME` 依赖）。
- [ ] `cron.py` / `api.py` 改从 `ops.tmpclean` 导入；测试同步改导入；注释里的「cron.sweep_tmp」改为新模块。
- [ ] 跑 `test_ch473_fixes.py::test_stor4_*`、`test_ch476_fixes.py::test_ops1_sweep_tmp*`、`test_ch485_ops.py::test_sweep_tmp*`。

### Task 3: F7 — 错误码字面量常量化 + AST 守卫

**Files:** `doc2md/cli.py`、`doc2md/api.py`、`doc2md/worker.py`、`doc2md/pipeline/batch.py`、`tests/test_ch496_local.py`

- [ ] 生产代码中的 `"E\d{4}"` 字面量改为 `errors` 中对应类/`Doc2mdError.code`。
- [ ] AST 守卫：扫描 `doc2md/**.py`（排除 `errors.py`），断言不存在长度 5、匹配 `^E\d{4}$` 的字符串常量。
- [ ] 测试中的字面量不动（wire oracle）。

### Task 4: B5 — apply_ops 合并索引

**Files:** `doc2md/llm.py:897-1100`、`tests/test_ch496_local.py`

- [ ] `merge_block` 分支复用已算 `idx` 传给 `_merge_block(ir, b, idx)`；成功后 `index.pop(b.id, None)`。
- [ ] 回归：合并后对 absorbed id 再发 op → `target_missing`；`return_report` 的 changed 计数与 `len(ir.blocks)` 变化一致。

### Task 5: G8 — max-jobs 负数 + drop_orphan 竞态

**Files:** `doc2md/cli.py`（work 参数解析）、`doc2md/worker.py:604`、`doc2md/artifacts.py:204`、调用点、测试。

- [ ] `--max-jobs < 0` → `parser.error`（退出码 2），并给 `run_work` 加正数防御。
- [ ] `drop_orphan_artifact` 的「查 doc 是否存在 → purge」纳入 `_artifact_lock`；确认调用点未持锁（避免自死锁）。
- [ ] 测试：`--max-jobs -1` 用法错误；孤儿清除并发下不误删新产物（构造最小 race 或串行断言加锁边界）。

### Task 6: 架构 4 项

**Files:** `doc2md/worker.py:81-105,199-233`、`doc2md/storage/db.py:218-256`、`doc2md/cron.py`（events_purge）、`tests/`

- [ ] `reclaim_stale` 返回实际 `changes()` 行数（并发下不虚报）。
- [ ] `_claim_batch` 同路径模式冲突：保持「最早已入队作业的 mode」并写入 `rep.notes`；测试钉死。
- [ ] `Store.__init__` 先 `ensure_private(parent/path)` 再 `_migrate`，连接建立后补 chmod `-wal`/`-shm`；测试权限位。
- [ ] 核实 `job_events` 由 cron `events_purge` 覆盖，serve-only 场景在文档注明保留口径。

### Task 7: A5 — 跨页列表续行

**Files:** `doc2md/pipeline/s3_structure.py:409-503,738-753,863-915`、`tests/`、版本号。

- [ ] 先构造 IR/文档复现：列表项 → page_break → 缩进续行，确认当前被截断。
- [ ] 让 `_join_list_continuation` / `_join_back` 允许穿过 `page_break`（仅当下一页首块是续行），页码/旗标保持。
- [ ] 若产物字节变化：`PIPELINE_VERSION` 2.16.0 → 2.17.0，版本三处同步；不满足则回退并登记。

### Task 8: G4' — 回滚 UI

**Files:** `doc2md/static/editor.html`、`tests/test_ch496_local.py`

- [ ] 块编辑区增加「历史」按钮：拉取该块审计/修订，列出可回滚版本；选择后 `POST /docs/<id>/blocks/<bid>/rollback`，成功后 refetch。
- [ ] XSS 安全：全部 `textContent`/DOM 构造，不拼 innerHTML。
- [ ] 静态契约测试（按钮存在、POST 路径、无 innerHTML 拼接）+ `node --check` 等价校验（提取脚本）。

### Task 9: B-11 — 浏览器走查

- [ ] 临时 root 启 `serve --port 0`，取 token；用 Playwright MCP 走：加载 → 编辑保存 → 历史/回滚 → 审计 → 队列面板。
- [ ] 记录结论；无法自动化的项（如系统对话框）明确标注。

### Task 10: CH-496 收口

- [ ] `docs/dev-plan.md` 登记 CH-496（§10.1、§12.7、v3.30 行），`PLAN_VERSION` 3.29 → 3.30。
- [ ] README 文档地图/gate 数、资产行数同源重算（先改内容，再 `wc -l`）。
- [ ] gate / full / ruff / doctor 全绿；更新项目记忆。
- [ ] 末尾列出仍需外部环境/人工的 B 项。
