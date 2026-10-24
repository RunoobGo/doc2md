# 第十四轮全面审查报告（CH-495）

- **审查基线**：`main` @ `8536783`（v2.15.0；QA 二轮 D1–D13 收口后）
- **审查范围**：架构设计与模块边界 / GUI 与 API 层 / 管线与存储实现与性能 / 文档一致性，四层并行只读审查
- **决策原则**：功能完整、性能优先；本机可自动验证者即修即验并补红线，需外部环境/人工者仅登记 B 类
- **出口版本**：`PIPELINE_VERSION` 2.15.0→**2.16.0**（A1/A2 改产物字节）、`PLAN_VERSION` 3.28→**3.29**、`SCHEMA_VERSION` 14、`DB_VERSION` 9 不动
- **出口取证**：本机（macOS arm64 / Python 3.15，`.venv` editable）gate **497 全绿**、全量 **1263 = 1261 passed / 2 skipped**（2 条为 Windows junction 平台门控）、`ruff check` 全绿、`doctor` 退 0

> **发现计数**：架构层 12（F1–F12）+ 4 待验证；API/GUI 层 8（G1–G8）+ 差异表 5（D1–D5）；管线/性能层 7+7（A1–A7 / B1–B7）；文档层 11（#1–#11）。合计 **46 项发现 + 4 待验证**。处置分三类：**已实施**（本机可验证、附红线）、**B 类登记**（需外部环境/人工/大面积机械改动）、**复核后撤销**（前提不成立）。

---

## 一、已实施的修复（A 类）

每项均补 gate 红线于 `tests/test_ch495_{watch_commit,pipeline,service,round2}.py`（四文件共 37 项，496 行）。

### 1.1 正确性 / 健壮性

| 编号 | 严重度 | 问题 | 修复 | 落点 |
| --- | --- | --- | --- | --- |
| **F1** | HIGH | watch 同步提交路径缺 CH-476/483 那套库面护栏（墓碑校验、pre-save mark、E4091 重建、事件发射），与 worker 提交路径口径分裂——竞争下可复活已删文档 | 抽出 `_watch_commit`，与 worker 共用同一提交序（mark→save_ir→emit 单事务） | `doc2md/cli.py` |
| **G3** | MED | 已吊销 token 二次 `DELETE` 仍返 200，文档称应 404（幂等掩盖「本就不存在/已吊销」） | revoke SQL 加 `AND (revoked_at IS NULL OR revoked_at='')`，二次吊销 rowcount=0 → 404/E4041 | `doc2md/auth.py`；断言补 `tests/test_ch469_service.py` |
| **G2** | MED | `PUT /settings` 畸形 JSON 静默 200；`POST /tokens` 缺 role 静默发 reader | `body()` 解析失败抛 400/E4001；role 走 `need()` 白名单校验 | `doc2md/api.py` |
| **G1** | MED | 无 `MAX_CONTENT_LENGTH`，超大 body 可耗尽内存 | 设 `_MAX_BODY_BYTES = 32 MiB` + 413 handler（E4131） | `doc2md/api.py` |
| **G5** | LOW | 事件端点 UA 检查先于鉴权，未授权请求也能探测 UA 策略 | `need("read")` 提到 UA 检查之前 | `doc2md/api.py` |
| **F3** | MED | cron 步骤 6/7/8 无逐步守护，报告最后写——中途异常则整份报告丢失 | 每步 try 守护 + 报告在 `finally` 写（异常也留证） | `doc2md/ops/cron.py` |
| **F8** | LOW | `user_version > DB_VERSION` 的库不被拒——DDL 尾部 `PRAGMA user_version` 会静默把新库版本戳降级回写 | `_migrate` 开头 fail-closed 抛 RuntimeError（拒绝降级/腐坏） | `doc2md/storage/db.py` |
| **A4** | LOW-MED | zip 产物 `os.replace` 后缺目录 fsync，崩溃可致 rename 丢失 | 落盘后对父目录 fsync | `doc2md/pipeline/convert.py` |
| **F2** | MED | `llm.py` 的 `_cfg_vendor_models` 路径解析与 `config.path_setting` 口径分歧 | 统一走 `config.path_setting` | `doc2md/llm.py` |
| **F10** | LOW | `if limit is not None … elif offset:` 死分支（`_window` 恒返回 int） | 删死分支，直接 `LIMIT ? OFFSET ?`；docstring 校正「取全部」→缺省 `_MAX_ROWS` 硬顶 | `doc2md/api.py` |
| **G7** | LOW | `--port 0` 被 `or` 吞成默认；watch dir 未 `expanduser`；cli docstring 缺 `backup drill`/`restore` | `args.port is not None` 判定；`Path(args.dir).expanduser()`；docstring 补两行 | `doc2md/cli.py` |

### 1.2 管线结构 / 产物正确性

| 编号 | 严重度 | 问题 | 修复 | 落点 |
| --- | --- | --- | --- | --- |
| **A1** | MED | 嵌套文本框（textbox 内套 textbox）正文被重复抽取 4 次 | `_txbxParagraphs` 改递归去重，每段只归最内层容器一次 | `doc2md/pipeline/s1_parse.py` |
| **A2** | LOW | `_fmt_num` 罗马数字只到 10、字母编号 26 后回绕错误 | 改 bijective base-26 + 扩展罗马数字（任意正整数可逆） | `doc2md/pipeline/s1_parse.py` |

### 1.3 性能（等价改写，产物字节不变）

| 编号 | 严重度 | 问题 | 修复 | 实测收益 | 落点 |
| --- | --- | --- | --- | --- | --- |
| **B2** | MED-LOW | `_escape_inline` 逐字符 `join` | `str.maketrans`/`translate` 表驱动 | 32.8→10.6 ns/char（~3×），200 随机串对照参考实现字节一致 | `doc2md/pipeline/s4_render.py` |
| **B7** | LOW | `_flush` 逐行 `text = _append_line(text, nxt)` 整串复制，O(n²) | 片段列表 + 末字符跟踪（`_join_line` 判定只依赖累积串末字符，故等价） | 8 组参数化 + 120 随机 buf 对照顺序累积口径一致 | `doc2md/pipeline/s3_structure.py` |
| **B4** | MED-LOW | `sqlite_backup` 一次性 `backup()` 长持读锁 | `while src.backup(dst, pages=1000): sleep(0.01)` 分步拷贝释放锁 | 4000 行跨多页步长 + integrity_check 通过 | `doc2md/ops/backup.py` |
| **B3** | MED-LOW | `--skip-existing` 每文件构造/关闭 Store + 双哈希 | 进程内 `_SKIP_STORES` 缓存延迟构造的 Store | 缓存实例复用（`s1 is s2`）断言 | `doc2md/pipeline/batch.py` |

### 1.4 GUI（editor.html）

| 编号 | 严重度 | 问题 | 修复 | 落点 |
| --- | --- | --- | --- | --- |
| **G4** | MED | 空队列提示引导用户用不存在的 `--async` 旗标；`showAudit`/`doSearch` 的 `api()` 无 try/catch，失败静默 | 提示改 `doc2md work --enqueue <路径>`；两处包 try/catch → `log(..., true)` 显式报错 | `doc2md/static/editor.html` |

### 1.5 错误码语义（F5，不新增码）

`E5041_LlmUnavailable` 被复用于「同步池忙」，与「LLM 服务不可用」语义碰撞。**不能新增 E5051**——该码已被 CH-456 删除，且 `tests/test_t9_convergence.py:261` 断言 `"E5051" not in CODE_INDEX`（守卫禁复活）。处置：保持 E5041，`detail.reason="sync_pool_busy"` 区分，docstring 登记该口径。**B 类**：错误码字面量→常量的批量替换（F7）留待专门机械轮。

### 1.6 跨模块契约转正（F12）

`s4_render` 从 `s3_structure` 引私有 `_ORDERED_ITEM`——私有名跨模块共享是隐式契约。处置：转正为公共名 `ORDERED_ITEM`（去下划线），断言 `s4.ORDERED_ITEM is s3.ORDERED_ITEM` 且无残留 `_ORDERED_ITEM`；`docs/dev-plan.md` 与 `docs/qa/test-plan.md` 全文同步改名。

---

## 二、B 类登记（需外部环境 / 人工 / 大面积机械改动，本轮不实施）

| 编号 | 事项 | 受限原因 | 验证方式 | 建议顺序 |
| --- | --- | --- | --- | --- |
| **B1** | drain 内进程池跨 mode 组常驻复用（当前每组重建池） | X-16/X-17 有 `terminate` 退出死锁史，池生命周期改动风险高 | 需并发长跑压测（M-5 夹具）+ 远端 full-suite 干净退出序列复核 | 高（性能收益明确，但须先压测） |
| **A3** | marker 语义（如 `page_break`）丢失，应升为 IR flag | 改 IR 携带字段 = 产物格式变更决策，牵动 schema 与黄金资产 | 需 IR 契约评审 + 全量黄金字节重烤 | 中（已按 `docs/adr-o11-inline-marks.md` 推迟：T1 未出现；待 T2 窗口与 A6 合并实施） |
| **A6** | 链接块丢 bold/italic run 格式 | 需 IR 携带 run 级格式，同属产物格式变更 | 同 A3 | 中（ADR-o11；禁止「只加字段不接渲染」的半成品中间态） |
| **A5** | tight list 跨 `page_break` 的连续性（待验证） | 需真实跨页列表语料复现 | 真实语料人工判读 | 低 |
| **F7** | 错误码字面量→常量批量替换 | 机械大面积改动，混入本轮会淹没真实修复 diff | 专门一轮 + AST 守卫扩面 | 低 |
| **F9** | `api` 从 `cron` 引 `sweep_tmp`，模块归位 | 跨模块依赖整理，非缺陷 | 重构轮 | 低 |
| **F11** | `_MODES` 两处重复定义 | 去重需确认两处语义完全一致 | 重构轮 | 低 |
| **B5** | `apply_ops` 的 O(n) 列表操作 | 收益不确定，需 profile 定位真实热点 | 基准测量 | 低 |
| **G4'** | FR-211 回滚 UI 缺失 | 需真实浏览器走查（人工） | 浏览器手动验证回滚流 | 中 |
| **G6** | token 经 URL 路径传递 | **已定档为设计拒绝**（CH-495）：`api.tok()` 只读 `X-Doc2md-Token` / `Authorization`；URL 里的凭据会落访问日志、代理缓存与 `Referer`，维持不支持——口径写入 `docs/dev-plan.md` §5.5「凭据通道纪律」 | 无需验证（进 URL 稳定 401/E4011） | 已结案 |
| **R7-OPS-3** | CLI 权限口径分裂（沿前轮） | **已按设计立场定档**（CH-495）：单机自用，本机 CLI 操作者 = 本地 admin；远程/多用户不在支持面内——口径写入 §6.2「权限边界的产品立场」 | 无需验证（设计立场） | 已结案 |
| **G8** | 待验证：`POST /jobs` 路径包含校验、`drop_orphan` 竞态、`--max-jobs -1` | 需构造特定竞争/边界场景 | 定向用例 + 压测 | 中 |
| **架构待验证 4 项** | `reclaim_stale` 计数、`_claim_batch` mode 覆盖、WAL 文件权限窗口、`job_events` 无界增长 | 需长跑/并发/多用户场景观测 | 并发压测 + 长跑观测 | 中 |
| **既有 B 类** | B-5/B-6/B-7（Windows 真机）、B-11（编辑器走查）、B-13/B-15（真实语料人工判读） | 沿袭前轮，均需外部环境/人工 | 见各轮报告 | 按既有台账 |

---

## 三、复核后撤销 / 无需修复

| 编号 | 原判 | 撤销理由 |
| --- | --- | --- |
| **F4** | `api.revoke_effective_seconds` 死键 | 非死键——`dev-plan:917` 登记为「仅展示」设计，有消费面 |
| **B6** | `search` LIKE 复核无上限 | 复核 `repo.search` 所有 LIKE 路径均已有 `LIMIT limit+1` 约束，无缺口 |

---

## 四、文档层发现与处置（#1–#11）

| 编号 | 严重度 | 问题 | 处置 |
| --- | --- | --- | --- |
| **#1** | HIGH | QA 轮 2.13.0–2.15.0（O-01~O-12 / N-1~N-8 / D1–D13）此前**未登记 CH 条目**，gate 从 419 涨到 460 的 41 项无归属 | 本轮 CH-495 登记行**补记该滞后**，并把 41 项按文件归因（`test_v24_gates` +10、`test_llm_p0_contract` +16（新文件）、`test_qa_n04_furniture_ext` +11（新文件）、`test_ch490_fixes` +2、`test_ch473_fixes` +1、`test_ch485_artifacts` +1 = 41） |
| **#2** | MED | gate 419→**497**、全量 1030→**1263** 计数滞后 | §8.1 / 附录 F / 附录 G / README 同步 |
| **#3** | MED | 文档地图缺 `adr-o11` 与 `docs/qa/` | README 文档地图补两项 |
| **#4** | MED | 附录 D（5.2 错误码表）缺告警码 W1045/W1046 | 补两行 |
| **#5** | MED | 附录 B `convert` 缺 `--format zip\|md\|both`（CH-491 已实现，`cli.py:175`） | 补登 |
| **#6** | MED | 附录 G `errors.py` 等改动模块行数滞后 | 按 `wc -l` 实测刷新 |
| **#7** | MED | 附录 G 覆盖面行数（44 .py / 14745 行）滞后 | 实测刷新 |
| **#8–#11** | LOW | 附录 C API 差异 5 处（409/E4091、E5031 http 500、limit=0 语义、audit「取全部」→缺省硬顶、配置级联 dev-plan:507）；§12.10 缺 v3.29 行 | 逐项校正 |

---

## 五、版本与对账

- `PIPELINE_VERSION` 2.15.0→**2.16.0**：A1（嵌套文本框去重）与 A2（字母/罗马编号还原）改变受影响文档的产物字节 → minor 升位；B2/B7 为等价改写（对照参考实现字节一致），不触发升位。
- `PLAN_VERSION` 3.28→**3.29**：登记面 + 附录规格刷新。
- `SCHEMA_VERSION` 14、`DB_VERSION` 9：无 schema / 库结构变更，不动。
- 五条 `test_ch477_version_sync` 对账红线（header / §1.4 / 版本表 / README↔10.1 编号 / pyproject）全部改写并复跑通过；`test_ch491_doc_sync`（错误码引用 + 零引用死函数）复跑通过。

## 六、gate 分账（419 → 497）

- **QA 轮 2.13–2.15（此前未登记，#1 补记）**：+41
  - 新文件：`test_llm_p0_contract` 16（module 级 gate）、`test_qa_n04_furniture_ext` 11（module 级 gate）
  - 补入既有文件：`test_v24_gates` +10、`test_ch490_fixes` +2、`test_ch473_fixes` +1、`test_ch485_artifacts` +1
- **CH-495（本轮）**：+37
  - `test_ch495_round2` 14、`test_ch495_pipeline` 13、`test_ch495_service` 7、`test_ch495_watch_commit` 3
- 合计 419 + 41 + 37 = **497**，与 `pytest -m gate --collect-only` 实测逐文件求和一致。

全量：1030（CH-494）+ QA 轮增量 + 37（CH-495）= **1263 = 1261 passed / 2 skipped**（2 skip 为 Windows junction 平台门控，非「宿主条件写进断言」病灶）。
