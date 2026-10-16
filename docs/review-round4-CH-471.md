# Doc2md 第四轮全面审查报告与优化方案（CH-471）

- 轮次：第 4 轮（前三轮 CH-459 / CH-464 / CH-469 见 `dev-plan.md` §9.4，累计 106 项已修）
- 日期：2026-10-15
- 范围：设计、代码、文档全面审查；原则「功能全面、性能优先」
- 口径：**只读审查，本轮不实施任何改动**；交付为本报告 + 优化方案
- 审查面：管线核心（S1/S1.5/S2/S3/S4/batch/sharding/metrics/ir）、存储与检索、服务层（api/cli/worker/scheduler/cron/auth/llm/errors）、安全/运维/配置、测试/CI、文档一致性
- 分诊法（沿用 §9.4）：**A = 沙箱内可修**（给出精确改法，待批准后实施）；**B = 需本机以外环境/人工**（仅登记）；**C = 需人决策**（已按原则代为制定最佳方案，见第 5 章）
- 置信度：high / medium / low，逐条标注

## 0. 门禁基线复现（本机）

在本机复跑合并门禁，结果与 `README.md` 宣称一致：

```
132 passed, 349 deselected in 24.05s
doctor: 依赖全 ok, root_exists=false, free_mb=508357, cpu_count=10, llm_mode=offline
```

结论：132 项门禁在本机全绿；6 条合并红线（shard_two_phase / malicious_filenames / injection_resistance / edit_optimistic_lock / delete_cascade / log_rotation）均为真实行为测试，非冒烟（见 CONS-1）。**本报告的所有发现都是在这套「已全绿」的门禁之外新挖出的问题**——即现有红线未覆盖的盲区。

## 1. 执行摘要

项目成熟度极高：分层 DAG 架构清晰、离线契约（netguard）真实加固、并发模型（WAL + 线程局部连接 + BEGIN IMMEDIATE）正确、加密（AES-256-GCM 分块容器）稳健、配置键三方对齐（SPEC=default=example=57）、UI 全转义防 XSS、等价性纪律（G6 逐 zip 条目 sha256）到位。前三轮已把绝大多数显性问题修净。

本轮新发现 **40 项**，其中 **HIGH 5 项**，集中在四类盲区：

1. **正确性盲区（PIPE-1，HIGH）**：分片转换按**片**计算正文字号众数（`convert.py:98` 的 `analyze(sub)` 未传 `base_size`），对「字体跨片异质」的文档会破坏 G1「分片≡单片」等价不变量；现有门禁用的是**字体均匀**的 `large_320p.pdf`，因此漏检。这是前三轮都未触及的新缺陷。
2. **数据安全盲区（STOR-5，HIGH）**：同源文件重复转换 → `doc_id` 相同 → `INSERT OR REPLACE` + `DELETE FROM blocks` **静默清空用户编辑**、重置 `revision`、孤儿化 `edit_audit`，且 `rollback` 可能把旧文本当新版本复活。对「数据安全至上」的诉求，这是最高优先。
3. **健壮性盲区（STOR-3，HIGH）**：FTS 重建非原子（`DROP` + 自动提交 DDL + 独立事务回填），中途崩溃 → 索引永久为空且不再自愈；`reindex()` 是**零调用者的死代码**，无启动对账。
4. **门禁自噬盲区（TEST-1，HIGH）**：golden 样本缺失时门禁**静默跳过后仍判绿**（`pytest.skip`），CI 从不生成 golden——红线可能在无人察觉时消失。此项与用户既定原则「绝不用 skip 掩盖」直接相关。

另有 TEST-3（HIGH，CI 无 Windows 腿，而头号目标机是 8GB Win11）。其余为 MED/LOW 的性能、安全加固、契约与文档漂移项。

**性能优先的净收益点（低风险、可等价保持）**：STOR-1（`synchronous=NORMAL`+mmap/cache，已代决策）、STOR-2（bm25 排序 + 免二次 COUNT）、STOR-4（FTS 回填流式化）、SVC-3（同步 `POST /jobs` 复用进程池）、PERF-2（日志单次编码）、PIPE-2（删除恒真重过滤）。

> 本章不代替第 3 章逐条证据；实施顺序见第 4 章。

## 2. 审查方法与证据口径

- 证据优先：每条发现给 `文件:行` + 问题 + 影响 + 建议 + A/B/C + 置信度 + 是否需外部验证。
- 只读：未修改任何源码或权威文档（`dev-plan.md`）。文档漂移项一律登记为「待批准的文档更新建议」，本轮不落笔。
- 决策代理：C 类（需人决策）按「功能全面、性能优先」+ 项目核心价值（离线、4 依赖闭环、数据安全、G6 等价）代为制定最佳方案，见第 5 章。
- 外部/人工项：仅登记（第 6 章），并在文末提醒（第 8 章）。

## 3. 发现清单（逐条证据）

### 3.1 管线核心（PIPE）

**PIPE-1 · 正确性/G1 等价 · HIGH · 置信度 high**
- 证据：`doc2md/pipeline/convert.py:98` 分片循环内 `ir = analyze(sub)` **未传 `base_size`**；`doc2md/pipeline/s3_structure.py` 的 `analyze(pages, base_size=None)` 在 `base_size is None` 时 `base_size = _modal_size(pages)`，即对**传入的片**算众数字号。对照 `convert.py:164` 的 `_fallback_single` 走 `analyze(pages)`（**全文档**）——两条路径口径不一致。
- 问题：正文字号众数是标题判定（`_heading_level`）的基准。文档字体跨片异质（如前置页大字号、正文小字号，或不同章不同字体）时，各片自算基准 → 同一页在「分片」与「单片」下被判成不同层级/是否标题，违反 G1「分片与单片等价」与方案 §4.4。
- 影响：`shard_two_phase` 门禁用的是字体均匀的 `large_320p.pdf`，`char_similarity≥0.99` 通过；异质字体文档的真实等价性**未被任何门禁覆盖**，属静默正确性风险（产物层级错乱）。
- 建议（A）：在 `convert.py` 分片前对**全文档**算一次 `base_size = _modal_size(pages_all)`，传入每片 `analyze(sub, base_size=base_size)`；`_fallback_single` 同口径。开销 = 一次全文档众数扫描（性能中性），且使分片≡单片对字号成立。
- 需外部验证：否（逻辑可由代码推定）；但**构造反例**需一份「字体异质 + >300 页」的 golden（登记 B，见 6.1）。

**PIPE-2 · 性能（恒真重过滤）· LOW · 置信度 high**
- 证据：`s1_parse.py:149-150` 先 `_slice_chars(chars, cs_index, top, bottom)`（bisect 取 `[top-1.0, max(bottom,top+1.0))`，见 `:234-235`），再 `_line_style(lchars, top, bottom)`；而 `_line_style:247-248` 用**完全相同**的边界把 `lchars` 再过滤一遍。
- 问题：`_slice_chars` 返回的每个 char 必已满足该边界，故 `_line_style` 内的列表推导**恒等于原列表**（`cs == chars`），是每行一次的纯浪费（`_char_index` 注释 `:218-222` 说明 bisect 优化本就是为了消除逐行全扫，此残留是漏删）。
- 影响：微小 CPU 浪费（O(行内字符)/行）；无正确性影响。
- 建议（A）：`_line_style` 内直接 `cs = chars`（删去重过滤）。**G6 安全**：删除恒真过滤不改输出，逐字节等价。

**PIPE-3 · 质量（冗余编号）· LOW · 置信度 high**
- 证据：`s3_structure.py` 循环内多处 `Block(id=f"b{seq+1}", ...)` 且维护 `seq`，但 `analyze` 收尾（约 `:325-327`）`for i, b in enumerate(ir.blocks, start=1): b.id = f"b{i}"` 会**整体重排**，覆盖循环内 id。
- 问题：循环内的 id 赋值是死写；`sharding._renumber` 与 `stitch_shards` 之后还会再排一次。
- 影响：可读性/维护性；无正确性问题（最终 id 由重排决定）。
- 建议（A，可选）：循环内 id 用占位（如 `""`），只保留收尾重排；或删收尾重排、保证循环内 id 正确（单片路径依赖它）。二选一，避免三处编号并存。

**PIPE-4 · 功能（行内链接丢弃，已知限制）· LOW · 置信度 high**
- 证据：`s1_parse.py:344-369` 的 `_docx_para_link` 仅当超链接文本**完全覆盖**段落时才附着；`s4_render._inline` 每块至多渲染一条链接（块级模型，IR 约定）。
- 问题：段落中**部分跨度**的行内链接（句中某几字带链接）被丢弃。
- 影响：与「功能全面」有差距；但属 IR 块级链接模型的**既定取舍**（代码注释已声明 deliberate）。
- 建议（C→已决策，见 D7）：维持块级模型；把「部分覆盖行内链接被丢弃」写入文档已知限制；跨度级 inline 链接是更大的 IR 变更，登记为未来增强，不在本轮做。

### 3.2 存储 / 检索 / 编辑（STOR）

> 基线扎实（无需发现项）：SQL 全参数化；WAL + `busy_timeout=30000` + 线程局部连接 + `BEGIN IMMEDIATE` 多进程设计正确；事务批处理（`save_ir(tx=)` / `reindex_block(tx=)`）良好；CJK bigram-aux + LIKE 复核 + `MAX_QUERY_CHARS` 裁剪是合理的离线取舍；迁移为 DDL 重放 + 列探测，幂等自愈。

**STOR-5 · 健壮性/数据完整性 · HIGH · 置信度 medium**
- 证据：`storage/repo.py:30-42`（`INSERT OR REPLACE INTO docs` + `DELETE FROM blocks`）+ `pipeline/convert.py:61`（`doc_id = hash_of_path(p)`，只哈希内容）。
- 问题：同源文件重复转换 → 同 `doc_id` → `save_ir` **静默清空用户编辑**、`revision` 重置为 1、`edit_audit` 留下指向不存在版本的孤儿行；之后 `rollback(to_revision=N)` 可把转换前的旧文本当**新版本**复活；`created_at` 每次重置。
- 影响：用户编辑无声丢失——对「数据安全至上」是最严重的一类。
- 建议（A）：`save_ir` 前检测已存在且 `revision>1` 的文档，按策略之一处理：拒绝（E409 类）/ 要求 `force` 标志 / 对 `doc_id` 加版本维度；最低限度也要记 `doc_overwritten` 事件并保留 `created_at`。
- 需外部验证：否（行为由代码确定）。

**STOR-3 · 健壮性（FTS 重建非原子）· HIGH · 置信度 medium**
- 证据：`storage/db.py:207-220` `_migrate_fts`——`DROP TABLE search_blocks` + `executescript(DDL)` 在自动提交下执行，回填在**另一个**事务；`repo.py:56` 的 `reindex()` **零调用者**（包与测试均不引用）。
- 问题：drop 与回填之间崩溃 → FTS 索引永久为空（`aux` 列已存在，迁移不再触发），且无启动对账。
- 影响：检索静默失效（搜不到任何内容），无告警。
- 建议（A）：回填进新表后 `ALTER TABLE ... RENAME` 原子替换；或加启动/cron 一致性检查（`blocks` 数 vs `search_blocks` 数 → 触发 `reindex`，顺带让死代码复活）。
- 需外部验证：是——填充库上「迁移中途杀进程」测试（登记 B）。

**STOR-1 · 性能（SQLite PRAGMA 未调优）· MED · 置信度 high**
- 证据：`storage/db.py:166-171` `_new_conn` 只设 `journal_mode` / `busy_timeout` / `foreign_keys`。
- 问题：`synchronous` 停留默认 FULL（每提交 fsync——文档自称「每任务固定开销的主因」）；`mmap_size` / `cache_size` 未调（默认 ~0 / 2MB），不利于读密集检索。
- 建议（C→已决策 D1，A 实施）：`synchronous=NORMAL` + `PRAGMA mmap_size=268435456` + `cache_size=-65536` + 关闭时 `PRAGMA optimize`。
- 需外部验证：是——断电/崩溃 drill（登记 B，配套 D1）。

**STOR-2 · 功能/性能（检索无相关性排序 + 双查询）· MED · 置信度 high**
- 证据：`storage/repo.py:156-162` `SELECT ... FROM search_blocks WHERE ... MATCH ? ... LIMIT ?`，**无 `ORDER BY`**；每次检索跑两遍（取行 + `COUNT(*)`）。
- 问题：命中按 rowid（插入序）返回，未用 FTS5 `bm25()`；成本翻倍。
- 建议（A）：加 `ORDER BY bm25(search_blocks)`（或对 aux 列排序）；计数改 `LIMIT+1` 的 has_more，免精确 COUNT。
- 需外部验证：否。

**STOR-4 · 性能（FTS 回填全量入内存）· MED · 置信度 high**
- 证据：`storage/db.py:212-213` `SELECT doc_id, block_id, text FROM search_blocks` 后 `.fetchall()`。
- 问题：一次性把整库正文读进 RAM，与项目内存纪律相悖（大/分片库尤甚）。
- 建议（A）：服务端游标流式（在插入循环内 `for r in conn.execute(...)` 迭代）或 `fetchmany` 分块。
- 需外部验证：是——大库迁移 RSS 测量（t6 bench 已有仪器，登记 B）。

**STOR-6 · 功能（PATCH 空文本静默清块）· MED · 置信度 high**
- 证据：`storage/edits.py:56,62` `body.get("text", "")`；`api.py:277-286` 无字段校验。
- 问题：`PATCH` 带合法 `base_revision` 但 `text` 缺失/为 null → **静默把块清空**（并把 "" 当意图审计），而非 400。
- 建议（A）：present-check 失败时要求 `text` 为 str，否则 `E4001_BadRequest(detail={"field":"text"})`。
- 需外部验证：否。

**STOR-7 · 健壮性（外键 pragma 空转）· LOW · 置信度 high**
- 证据：`db.py:171` `PRAGMA foreign_keys=ON`，但 DDL（`db.py:25-118`）**无任何 FOREIGN KEY**。
- 问题：pragma 是 no-op；父子完整性全靠 `delete_doc` 手写级联，新写入者可无察觉插孤儿。
- 建议（C→已决策 D6，A）：不新增 FK（避免表重建）；文档明确「手写级联是唯一机制」，并加断言测试防孤儿。

**STOR-8 · 功能（长文本不可搜，无告警）· LOW · 置信度 high**
- 证据：`repo.py:46,67,89` 三处索引写入均 `b.text[:8000]`。
- 问题：>8000 字符的块（大表格很可能）被静默截断，搜不到，且无 flag/告警。
- 建议（A）：截断时发 warning/flag；或把尾部作为额外 FTS 行索引。

**STOR-9 · 文档漂移 · LOW · 置信度 high**
- 证据：`dev-plan.md:74,960` 写 `DB_VERSION = 4`，`db.py:20` 为 `5`；附录 E 索引清单（`:969-971`）漏 `ix_tasks_doc` / `ix_events_job` / `ix_events_doc`（`db.py:103,113-114`）；`db.py:10` docstring 提到不存在的 `search_docs` FTS 表。
- 建议（A，文档更新，待批准）：附录 E 升到版本 5、补三个 CH-469 索引、删 `search_docs`；`storage/__init__.py` 的 `__all__` 去掉死 `reindex` 或接到 STOR-3 对账。

**STOR-10 · 质量（删除并发/事务粒度）· LOW · 置信度 high**
- 证据：`repo.py:204-234,247-261` 存在性检查在删除事务**外**；`delete_source` 逐 doc 调 `delete_doc`，各开事务含 3 次 COUNT；`_Tx.rowcount`（`db.py:282-284`）每次额外 `SELECT changes()`。
- 问题：并发删同一 doc 可双双通过检查、写重复 `deletions` 痕迹；源删除 O(N) 事务；rowcount 可用 `cursor.rowcount`。
- 建议（A）：存在性检查移入事务（`DELETE ... RETURNING` 或 BEGIN IMMEDIATE 后复检）；用 `cursor.rowcount`。

### 3.3 服务层（SVC）

> 扎实面：鉴权矩阵完整（每端点 `need(action)`，`auth.check` 正确映射 401/403）；netguard 可重入（引用计数 + 单分发器），覆盖 `connect_ex`/`sendto`，hostname 解析限 loopback，且包裹整条管线含 LLM 调用；`Store` 线程局部连接 + weakref 注册表 + `BEGIN IMMEDIATE` + `changes()` 行计数；worker 认领双认领安全、陈旧回收用 `datetime()`；`editor.html` 全转义防 XSS；LLM 日志仅计数（NFR-20）。

**SVC-1 · 安全/完整性（immutable 绕过）· MED · 置信度 high**
- 证据：`llm.py:424-448` `_merge_block` 改写 `prev = ir.blocks[idx-1]`（text/links/provenance），只校验 `prev.type in ("paragraph","list_item")`，**从不查 `prev.immutable`**；`OpValidator._check`（`llm.py:78-95`）只验 op **声明**的目标（`op.target/block_id`）；`merge_block` 在默认白名单（`llm.py:21`）。
- 问题：不变量「immutable 块不得以任何理由修改/删除」可被绕过——一个指向**非 immutable** 块的 `merge_block`，其紧邻前驱若是 immutable，则通过校验后 `apply_ops` 改写该 immutable 前驱。`llm.mode=server` 下可达（offline 占位只发 `fix_heading_level`）。
- 建议（A）：`_merge_block` 在 `prev.immutable` 时拒绝；更佳：`OpValidator` 计算每个 op 的**有效写集**（含 merge 目的地），在该集上查 immutable/占用。
- 需外部验证：否（可离线单测：对含 immutable 前驱的 IR 调 `apply_ops([{"op":"merge_block","target":<非immutable>}])`）。

**SVC-2 · 质量/契约（API 无通用 500 处理）· MED · 置信度 high**
- 证据：`api.py:121-132` 只注册 `ApiError` / `Doc2mdError` / `404` 处理器；`get_markdown`（`api.py:272-275`）`zipfile.ZipFile(zp)` 后 `[...content.md][0]` → `BadZipFile`/`IndexError` 未捕获。
- 问题：任何非 `Doc2mdError` 异常逃逸为 Werkzeug 默认 **HTML 500**，违反附录 C（「响应错误一律 {code,message,detail}，5xx 同样走该契约」）与 CH-440。CLI 有宽 `except Exception` 漏斗（`cli.py:607`），API 没有。
- 建议（A）：加 `@app.errorhandler(Exception)` 返回 `E5000` 式 JSON（HTTP 500）；`get_markdown` 包 zip 访问，抛 `E5031`/`E4041`。
- 需外部验证：否。

**SVC-3 · 性能（同步 POST /jobs 串行无池）· MED · 置信度 high**
- 证据：`api.py:200-223` 同步 `POST /jobs` 逐文件**内联串行** `convert(f)`；对照 `cli.cmd_convert:289-290` 与 `worker.drain_once:129-131` 用 `run_batch` 进程池；默认 `perf.queue_async=false`（`default_toml.py:49`）。
- 问题：API **默认路径**在一个请求线程内串行转换整目录，无进程池并行——200 文件的 `POST /jobs` 阻塞为所有转换之和（正是 `queue_async` 要避免的，但它默认关）。
- 建议（A）：同步模式复用 `run_batch(files, workers=default_workers(cfg))`（带每请求上限），保留即时 `doc_id` 契约。
- 需外部验证：是——真实 HTTP 负载 / N 文件目录量化（登记 B）。

**SVC-4 · 性能（tasks 无界增长 + 5s 轮询）· MED · 置信度 medium-high**
- 证据：`cron.run` 只清 `events`（`cron.py:155-163`），除逐 doc 级联（`repo.py:225`）外无 `DELETE FROM tasks`；`GET /jobs` 跑无界 `SELECT status,COUNT(*) FROM tasks GROUP BY status`（`api.py:247-249`）；`editor.html:219` 每 **5000ms** 轮询 `loadQueue`。
- 问题：长运行实例上 `tasks` 无界增长；每 5s UI 轮询重扫全表计数 → 延迟持续恶化。
- 建议（A）：cron 加 tasks 保留/清理（镜像 `events_retention_days`），或维护增量状态计数器。
- 需外部验证：是——长运行数据集/负载测试（登记 B）。

**SVC-5 · 安全（api.bind 可绑 0.0.0.0）· MED · 置信度 high**
- 证据：`cli.py:183-188` `host = args.host or config.get(cfg,"api.bind","127.0.0.1")` 后 `app.run(host=...)`；`api.bind` 在 SPEC 中是无约束 `STR`（`loader.py:107`），无 loopback 强制。
- 问题：与 LLM 路径（受 socket 守卫）不同，**入站** API 可经 `--host` 或配置绑到 `0.0.0.0`，暴露到本机以外；虽有 token 鉴权，但「仅 127.0.0.1」姿态未在代码强制。
- 建议（C→已决策 D4，A）：非 loopback `api.bind` 默认拒绝/告警，除非显式 opt-in；`config.validate` 校验。

**SVC-6 · 安全（红线键可运行时改）· LOW-MED · 置信度 high**
- 证据：`FROZEN_KEYS` 只含 `security.sanitize_fail_action`（`loader.py:29-31`）；`run.offline_assert`（BOOL，`loader.py:73`）、`llm.bind`、`api.bind` 是普通 SPEC 键；`PUT /settings`（`api.py:403-429`）接受任意已知键并持久化到覆写层。
- 问题：管理员可经 API 关掉 NFR-1 离线守卫（`run.offline_assert=false`）或运行时重指 `llm.bind`，且重启后仍在——「红线」是可变的非冻结键。（缓解：netguard 启用时仍拦非 loopback；但 `doctor` 的 `probe()` 在守卫**之外**跑。）
- 建议（C→已决策 D4，A）：冻结 `run.offline_assert`（理想上还有 loopback-only 的 `llm.bind`/`api.bind`），或禁止经 `PUT /settings` 修改。

**SVC-7 · 文档漂移（dev-plan 落后 CH-469）· MED/LOW · 置信度 high**
- 证据与问题（`dev-plan.md` 权威但陈旧）：
  - `PUT /settings`：代码写**累计**稀疏 diff（`api.py:419-429`，`_sparse`/`write_override:501-528`）；`dev-plan.md:921` 说「整体重写为本次提交键集（非累计）…只有最后一次 PUT 的键生效」。
  - `POST /system/reload`：代码**应用**覆写（`api.py:438-451`，CH-462）；`dev-plan.md:484,922` 说「丢弃覆写层（既定语义，非 bug）」。
  - `DB_VERSION`：`db.py:20`=**5**；`dev-plan.md:960`=**4**；附录 E 漏三个新索引。
  - `doctor`：`cli.py:520-553` 有 **29** 基础字段（+4 依赖=33）；`dev-plan.md:897` 说「26+4=30」，漏 `s4_link_internal`/`s4_preserve_strike`/`watch_stable_seconds`。
  - `E5000`：被用作兜底（`cli.py:612`、`worker.py:158`、`batch.py:81`）但 `errors.py` 无该类，附录 D（`:934-947`）亦缺。
- 建议（A，文档更新，待批准）：附录 C/E 与 §5.7 更新为「累计覆写 + reload 保留覆写」；刷新 DB_VERSION/索引清单/doctor 计数；注册 E5000。

**SVC-8 · 质量（limit 负值 + 部分副作用）· LOW · 置信度 high**
- 证据：`api.py:231-233` `GET /jobs` 用裸 `int()` 解析 `limit` 且无负值保护 → `?limit=-1` 得 SQL `LIMIT -1`（全部）；与 `_window`（`api.py:107-108`，拒负）不一致。同步循环逐文件提交（`api.py:215-219`）但对非 E4221 再抛（`api.py:210`），故循环中途 `E4131` 会留下**已提交的先前文件**（部分副作用），不像异步路径全或无。
- 建议（A）：`GET /jobs` limit 走 `_int_arg`+非负校验；同步中途硬错误要么逐文件续（像 E4221），要么回滚整批。

**SVC-9 · 安全（token 明文 + UA 伪门禁）· LOW · 置信度 high**
- 证据：token 明文存（`db.py:74-80`；`auth.issue:44-47`）；`serve token --list` 打印完整 token（`cli.py:207-208`）；逐 job 的 `GET /jobs/<id>/events`（`api.py:385-395`）在 `need("read")` **之前**用**可伪造**的 `User-Agent` 子串把关，而文档称 FR-125 是「硬规则」（`api.py:7`，`dev-plan:919`）。
- 问题：DB/CLI 暴露活 token；UA 门仅咨询性（任何客户端可设 `curl/`）。数据是读者级元数据（无正文），影响有限。
- 建议（A）：存 token 哈希（按哈希查比对），`--list` 截断输出；文档把 UA 门标注为提示而非安全边界。

**SVC-10 · 质量（三处重复构造）· LOW · 置信度 high**
- 证据：`WalkLimits(...)` 在 `api.py:146-153`、`cli.py:399-405`、`cli.py:422-426` 重复；E4221 拒绝→`submit`/`mark` 在 `api.py:205-208` 与 `cli.py:464-472` 重复。
- 建议（A）：抽 `walk_limits_from_cfg(cfg)` 与 `record_rejection(sched,name,exc)`，消除三方漂移风险。

### 3.4 安全 / 运维 / 配置（SEC / OPS / PERF / CFG / Q）

**SEC-1 · 安全（netguard 是纵深防御非硬边界）· MED · 置信度 high**
- 证据：`netguard.py:111-117` `_install` 只重绑 `socket.socket.connect/connect_ex/sendto`；守卫仅在 `convert()` 内武装（`convert.py:52`）。
- 问题：进程内猴补丁。`sendmsg`（寻址 UDP）、裸 `_socket.socket`、未重新武装的子进程、或 `run.offline_assert=false` 均可绕过。模块 docstring 诚实声明「进程内」范围——这是**定性**而非隐藏 bug，但它**不**在 OS/密码学层强制无出口。
- 建议（A 部分 + B）：保留作断言；`sendmsg` 也打补丁（A，廉价）；真保证需在部署文档加 OS 级控制（网络命名空间/防火墙/seccomp）（B）。
- 需外部验证：是——带网络的真实沙箱确认无绕过路径被行使（登记 B）。

**SEC-3 · 安全/文档（Windows 口令明文入注册表）· MED · 置信度 high**
- 证据：`install-doc2md-tasks.ps1:200` `[Environment]::SetEnvironmentVariable(...,"User")`；注释 `:34`「不回显、不落盘」。
- 问题：用户级环境变量写入 `HKCU\Environment`（静态明文），该用户/管理员/恶意软件可读。「不落盘」不准确。
- 建议（A 文档 + B 验证）：默认走 Credential Manager 包装（已引用）或 DPAPI；修正注释披露注册表持久化。
- 需外部验证：是——Windows 主机确认存储位置（登记 B）。

**SEC-4 · 安全/功能（names.py 过拒合法内部 `..`）· MED · 置信度 high**
- 证据：`names.py:88` `if ".." in name: problems.append("dot-dot")`，经 `sanitize_name`→`assert_name_safe` 调用。
- 问题：`sanitize_name("a..b.pdf")` 抛 `UnsafeNameError`/E4003 而非消毒。穿越需要**分隔符**；叶名内裸 `..` 在 Unix 无害，却整文件被拒。
- 建议（A）：只把 `..` 当**完整路径段**标记（`assert_entry_safe` 已如此），或在 `sanitize_name` 末尾断言前把连续点折叠为单点。

**SEC-2 · 安全（守卫为所拦主机做 DNS 出口）· LOW · 置信度 high**
- 证据：`netguard.py:73` `socket.getaddrinfo(host, None)` 在 `_allowed` 内、抛 `OfflineViolation` 之前调用。
- 问题：对非 loopback **主机名**连接，守卫先解析名字（向解析器发 DNS = 出站流量）再拒 TCP。真离线机失败关闭（空→拒）；在线则把所试主机名泄给 DNS。
- 建议（A，可选）：文档标注；可对未知主机名不解析直接拒。

**SEC-5 · 安全（scrypt N 偏低 + 无法擦除密钥）· LOW · 置信度 high**
- 证据：`backup.py:53` scrypt `n=2**14,r=8,p=1`；`:81-91` 随机 16B salt + 每块随机 12B nonce + AAD=`header+seq`；`:116-119` `chunk_size` 上限 64MB（DoS 守卫）；GCM 认证（无字符串比较→无计时 oracle）；`derive_key` 每操作一次（非每块）。
- 问题：仅小疵——scrypt N=2^14 低于 OWASP 2^17 指引；Python 无法清零口令/密钥字节。对本地备份容器均可辩护。
- 建议（C→已决策 D5，A/低优先）：新备份将 N 提到 2^17（**前提**：header 存储 KDF 参数以保证解密向后兼容——需先核实 header 是否含 N）；文档说明内存擦除限制。

**OPS-1 · 文档（launchd「UTC」调度不实）· MED · 置信度 high**
- 证据：plist 注释 `md.doc2md.cron.plist:3`「00:00 UTC 触发」+ `EnvironmentVariables TZ=UTC`（`:16`）；`dev-plan.md:541`「StartCalendarInterval，00:00 UTC」；`README:111`「launchd 侧固定 UTC」。
- 问题：launchd `StartCalendarInterval` 按**本地墙钟**触发，非 UTC。`TZ=UTC` 只设子进程环境（影响渲染，不影响触发）。故 macOS 与 Windows 实际都在**本地午夜**触发；「UTC」保证是虚构的。备份正确性不受影响（代码用 `datetime.now(timezone.utc)`），但跨平台调度一致性宣称错误。
- 建议（A 文档 + B 验证）：修正文档/plist 注释为「本地时间」（与 Windows 侧一致），或去掉误导性 TZ。
- 需外部验证：是——macOS 主机观察实际触发时间（登记 B）。

**OPS-3 · 功能/安全（GGUF 解析无逐值上限）· MED/LOW · 置信度 medium**
- 证据：`gguf.py:51-53` `_rd_string` 读不可信 8 字节长度后 `f.read(n)`；`:62-70` 数组 `count`（8 字节）驱动 `range(count)`。`n_kv` 有上限（`:96-97`，O-9）但单个 string/array 尺寸无。
- 问题：损坏/恶意 GGUF 声明巨大 string 长度或 array count → 巨额分配/长循环（OOM/挂起）。信任边界是运维提供的模型文件，风险限于坏下载。
- 建议（A）：分配前对 string 长度与 array count 按文件大小/合理上限设帽。
- 需外部验证：是——确认模型文件信任模型（登记 B）。

**OPS-2 · 质量/文档（drill 指纹安全宣称夸大）· LOW · 置信度 high**
- 证据：`drill.py:91-93` 加性 `acc=(acc+int(sha256(row)))%2^256`；docstring `:76`「要伪造同指纹得先找到 sha256 原像」。
- 问题：模 2^256 加性合并与顺序无关，但可经子集和伪造，无需任何原像；docstring 宣称密码学上错误。对非对抗性「恢复 vs 丢失」比对（其唯一用途）功能无碍。
- 建议（A）：改注释措辞；若将来需防篡改，用排序集上逐行哈希的 XOR 或带键 MAC。

**OPS-4 · 质量（持久性小缺口）· LOW · 置信度 high**
- 证据：`backup.py:275` `tmp.replace(dst)` 无父目录 fsync；`to_external`/drill 不调磁盘守卫（只 `cron.py:105/134` 调）。
- 问题：rename 可能不抗崩溃；直接 `to_external`（如 ops/backup.md 手抄片段）跳过剩余空间检查。
- 建议（A）：rename 后 fsync 目录；`to_external` 内部可选守卫。

**PERF-1 · 性能（watcher 纯轮询全量重扫）· MED · 置信度 high**
- 证据：`watcher.py:121-138` `poll_all`→`guard_walk` 每间隔走整棵树；无 inotify/FSEvents。
- 问题：每轮 O(files) 重扫；大监听目录昂贵。是为稳定 + 递归安全（复用 dirguard）的刻意取舍，去重/`prune` 是 O(1) 字典/集合（好）。
- 建议（C→已决策 D3，A + 未来可选）：守住 4 依赖闭环——**不默认引入** watchdog；先加 mtime 短路跳过未变更文件的重复哈希（A，无新依赖）；事件驱动快路径（watchdog + 轮询对账）登记为未来可选增强。
- 需外部验证：是——大真实目录测量（登记 B）。

**PERF-2 · 性能（日志每行双次 UTF-8 编码）· LOW · 置信度 high**
- 证据：`logging.py:76-77` `self._fh.write(line)` 后 `self._size += len(line.encode("utf-8"))`。
- 问题：每行编码两次；`scrub_record` 每次写还递归整个 payload。微小，但在日志热路径。
- 建议（A）：一次编码为 bytes，写 bytes，加 `len(b)`。

**CFG-1 · 功能（配置键三方对齐）· 表扬 · 置信度 high**
- 证据：程序化核对——SPEC=57、default=57、example=57，三集合完全一致；与 `dev-plan.md:814` 宣称相符。表驱动 `validate`（类型/范围/枚举/八进制）、未知键 `UserWarning`、冻结 `sanitize_fail_action`、`path_setting` 根迁移均在且正确。无缺陷。

**CFG-2 · 质量（磁盘守卫 max 语义留白不足）· LOW · 置信度 high**
- 证据：`diskguard.py:69` `need_mb = max(floor_mb, src*factor)`。
- 问题：floor 与写估计用 `max`（非求和）→ 大写只要求 `free ≥ write_size`，写后无 `min_free_mb` 缓冲。
- 建议（A）：若意图写后留白则改 `floor_mb + src*factor`；否则文档说明 floor 非叠加。

**Q-1 · 质量（watcher.registered 冗余）· LOW**：`watcher.py:37,96` 每个键也在 `pending`/`first_seen`，只用于 `prune` 并集与一个测试。建议（A）清理。
**Q-2 · 质量（dirguard 物化全部条目）· LOW**：`dirguard.py:55` `sorted(os.scandir(...))` 在 `max_files_per_dir` 检查触发前物化全部条目 → 巨目录内存尖峰。建议（A）迭代计数、提前 break。

### 3.5 测试 / CI（TEST / CONS）

**CONS-1 · 一致性 · INFO · 置信度 high（表扬）**
- 证据：README 的 132 门禁与静态计数精确吻合：`test_v24_gates.py` 29 函数（一个 ×12 参数化）→40；`test_perf_lowend.py` 28（一个 ×2）→29；`test_ch469_service.py` 恰 10 个 `@gate`；`test_ch469_ops.py` 19（×12 + ×24）→53。合计 **132**。6 条命名门禁均映射到真实行为测试（相似度≥0.99 + 块数相等 + 诚实降级；425/E4251 + 审计前后/revision；404 + FTS 残留 + 墓碑无正文；轮转在服内无需重启）。真实红线，非冒烟。

**TEST-1 · 主机风险/CI（golden 缺失门禁判绿）· HIGH · 置信度 high**
- 证据：`tests/conftest.py:20,27,118`（`pytest.skip("golden sample missing…")`）、`test_perf_lowend.py:48`、`test_v24_gates.py:210,222`；`ci.yml` 从不跑 `make_golden.py`；门禁依赖 `small_pdf`/`large_single`/`large_sharded`/`names_manifest`。
- 问题：若 `tests/golden`（5134 文件，本机无 `.git` 不可核验是否入库）缺失/不全，`pytest -m gate` 大量跳过仍**退出 0**——红线静默消失；连 MANIFEST 完整性测试（`test_golden_repro.py:97`）也经 `golden_dir` 跳过。
- 影响：与用户既定原则「绝不用 skip 掩盖平台失败」直接冲突。
- 建议（A）：加一条门禁项，在 golden 资产/MANIFEST 缺失时**硬失败**（fail，非 skip）；或 CI 在门禁步骤前生成 golden。
- 需外部验证：是——确认 `tests/golden` 在 github.com/RunoobGo/doc2md 已入库，且一次 CI 报告 132 passed / 0 skipped（登记 B）。

**TEST-3 · 主机风险（CI 无 Windows 腿）· HIGH · 置信度 high**
- 证据：`ci.yml:21,44,59` 全 `ubuntu-latest`；`test_t5_windows_ops.py:4`「在 Linux 沙箱内做静态等价验证（真机注册待 T3）」；spawn 模式批处理测试（`test_perf_lowend.py:97`）是模拟非 Windows。头号目标机是「8GB Win11」（`test_perf_lowend.py:6`）。又 `test_shipped_symlink_loop_is_relative`（`test_golden_repro.py:85`）需真符号链接——无开发者模式的 Windows 检出会**失败**（非跳过）。
- 建议（B）：至少为非 golden 密集套件加 `windows-latest` 矩阵腿，并跑 T3 真机轮。
- 需外部验证：是——Windows 主机（schtasks 注册、cp936 控制台、真实 RSS）、macOS launchd plist。

**TEST-2 · 质量（「有界内存」门禁只断言常量）· MED-HIGH · 置信度 high**
- 证据：`test_perf_lowend.py:53-55`（`assert chunk_pages == 32`；1290MB 数字只在 docstring）、`:489-495`（doctor 旋钮常量）。RSS 采样仪器存在（`test_t6_corpus_bench.py:258-311`）但非门禁，且跑在「只验证仪器接线」的小语料（`:343`）。
- 问题：令 320 页样本峰值内存翻倍的回归会让 132 门禁全绿；「性能优先」只经输出等价（优秀：逐 zip 条目哈希，`:58-69`）与一个真实计时测试（`dt<4`，`:135-158`）验证。
- 建议（A）：加门禁峰值 RSS 上限（子进程 + `resource.getrusage`/`sample_tree`）于 `large_320p.pdf` 转换，阈值宽松。
- 需外部验证：是——在真实 8GB Win11 目标校准阈值（T3，登记 B）。

**TEST-4 · 主机风险（计时敏感断言）· LOW-MED · 置信度 medium**
- 证据：`test_ch469_ops.py:100-118`（`time.sleep(0.05)`/`release.wait(5)` 线程重叠）、`test_perf_lowend.py:158`（`dt<4`，1s 超时 + 6s 睡眠者；三矩阵作业共享 runner）。余量合理但非免疫慢 runner。**未发现 t.Skip 掩盖平台失败**（好）——skip 仅用于缺资产守卫（见 TEST-1）与 `node not available`（`test_t4_queue_ui.py:58`，会在无 node 主机静默丢掉唯一 JS 语法检查）。
- 建议（A）：放宽批处理超时余量或改基于重试计数。

**TEST-5 · CI（次要缺口）· LOW · 置信度 high**
- 证据：(a) `lint` 作业 `ruff check .` 无 `continue-on-error: true`（`ci.yml:55-56`）——「信息性」只因分支保护不要求它，红 lint 仍染红提交；(b) `full-suite` 跑 `test_golden_repro.py` 两次（`ci.yml:72-74`，已在 `pytest -q` 内）；(c) 直推 `main` 绕过必需检查（已记录，CH-470 于 `dev-plan:726` 未决）；pre-push 钩子 opt-in（`core.hooksPath`）且 `--no-verify` 可绕。
- 建议（C→已决策 D2，B 执行）：在 `main` 启用「require PR before merging」。

**TEST-6 · 覆盖（次要）· LOW · 置信度 medium**
- 证据：覆盖异常广（netguard 含 `connect_ex`/`sendto`/DNS 投毒；watcher 注入时钟；backup v1+v2 遗留解密；调度/worker 认领原子性）。但 DB 迁移只测单步 v4→v5（无多版本链，`ch469_service:243`）；真实外部系统未测但诚实登记（真 llama-server + GGUF、真外部/加密备份卷、真浏览器编辑器）。
- 建议（B）：登记——真机 llama-server、真外部卷、浏览器通过。

### 3.6 文档漂移汇总（DOC，跨子系统）

权威 `dev-plan.md` 落后于 CH-469 代码（**只读审查，本轮不改，登记为待批准的文档更新**）：

| # | 项 | 文档 | 代码 | 来源 |
|---|---|---|---|---|
| 1 | DB_VERSION | 4（`:74,960`） | 5（`db.py:20`） | STOR-9/SVC-7 |
| 2 | 附录 E 索引清单 | 漏 3 索引 | `ix_tasks_doc`/`ix_events_job`/`ix_events_doc` | STOR-9/SVC-7 |
| 3 | doctor 字段数 | 26+4=30（`:897`） | 29+4=33（`cli.py:520-553`） | SVC-7 |
| 4 | PUT /settings 语义 | 整体重写非累计（`:921`） | 累计稀疏 diff（`api.py:419-429`） | SVC-7 |
| 5 | reload 语义 | 丢弃覆写层（`:484,922`） | 应用覆写（`api.py:438-451`） | SVC-7 |
| 6 | E5000 | 无类/附录 D 缺 | 兜底使用（`cli.py:612` 等） | SVC-7 |
| 7 | launchd 调度 | 00:00 UTC（`:541`/README:111） | 本地墙钟触发 | OPS-1 |
| 8 | Windows 口令 | 「不落盘」（ps1:34） | 明文入 `HKCU\Environment` | SEC-3 |
| 9 | drill 指纹 | 「需找 sha256 原像」（`:76`） | 加性可子集和伪造 | OPS-2 |
| 10 | search_docs 表 | `db.py:10` docstring 提及 | 不存在 | STOR-9 |

## 4. 优化方案（按「功能全面、性能优先」排序）

> 全部为**待批准的实施计划**，本轮不落地。每条标注 G6 等价性影响与验证法。实施时遵循用户既定原则：合并后在**本机**复跑门禁；平台相关判断在**产品内**修，绝不 `t.Skip`；未经要求不提交/推送。

### P0 — 正确性与数据安全（先做，阻塞性）

1. **PIPE-1 全文档 base_size**（HIGH）：`convert.py` 分片前 `base_size=_modal_size(pages_all)`，传每片 `analyze(sub, base_size=...)`；`_fallback_single` 同口径。G6：改变异质字体文档的分片产物使其**等于**单片（修复等价，非破坏）；均匀字体文档逐字节不变。验证：新增异质字体 >300p golden 的 shard≡single 断言（B 制样）。
2. **STOR-5 重复转换护编辑**（HIGH）：`save_ir` 检测 `revision>1` → 拒绝/`force`/版本化 doc_id；保留 `created_at`，记 `doc_overwritten` 事件。验证：转换→编辑→再转换→断言编辑仍在或明确报错（离线单测）。
3. **STOR-3 原子 FTS 重建 + 对账**（HIGH）：回填进新表后 `RENAME` 原子替换；启动/cron 对账 `blocks` vs `search_blocks` 触发 `reindex`（复活死代码）。验证：填充库迁移中途杀进程→重启→断言索引非空（B kill 测试）。
4. **TEST-1 golden 缺失硬失败**（HIGH）：加门禁项，资产/MANIFEST 缺失即 fail 非 skip；或 CI 门禁前生成 golden。验证：临时移走 golden→`pytest -m gate` 必须**非 0 退出**。

### P1 — 性能（性能优先，多为等价保持）

5. **STOR-1 PRAGMA 调优**（D1）：`synchronous=NORMAL`+`mmap_size=256MB`+`cache_size=-65536`+关闭 `PRAGMA optimize`。验证：断电 drill（B）+ 本机吞吐对照。
6. **STOR-2 bm25 排序 + 免二次 COUNT**：`ORDER BY bm25(search_blocks)`；`LIMIT+1` has_more。G6：检索**排序**变化（非转换产物），不影响 zip 等价。验证：检索相关性单测。
7. **STOR-4 FTS 回填流式**：游标迭代/`fetchmany` 替 `fetchall`。验证：大库迁移 RSS（B）。
8. **SVC-3 同步 POST /jobs 复用 run_batch**：带每请求上限，保即时 doc_id 契约。验证：N 文件目录负载对照（B）。
9. **PERF-2 日志单次编码** + **PIPE-2 删恒真重过滤**：均 G6 安全（不改输出）。验证：门禁全绿 + 输出逐字节等价。
10. **SVC-4 tasks 保留清理/增量计数**：cron 镜像 events 保留；验证：长运行负载（B）。
11. **PERF-1 mtime 短路**（D3，无新依赖）：跳过未变更文件重复哈希。验证：大目录轮询 CPU 对照（B）。

### P2 — 安全加固

12. **SVC-1 immutable 有效写集校验**：`OpValidator` 在有效写集（含 merge 目的地）查 immutable/占用；`_merge_block` 拒 immutable 前驱。验证：离线单测（server 模式 op）。
13. **SVC-5/D4 loopback 强制 + SVC-6 冻结 offline_assert**：非 loopback `api.bind` 需 opt-in；冻结 `run.offline_assert`，禁 `PUT /settings` 改。验证：配置校验单测 + 绑定拒绝。
14. **SEC-4 names.py 放宽内部 `..`**：只标完整段或折叠连续点。验证：`a..b.pdf` 应消毒而非 E4003；穿越仍拒。
15. **SVC-2 API 通用 500 JSON 处理**：`@app.errorhandler(Exception)`→E5000 JSON；`get_markdown` 包 zip。验证：注入异常→断言 JSON 契约。
16. **SEC-1 sendmsg 补丁**（A 部分）+ **SVC-9 token 哈希/截断**+ **OPS-3 GGUF 尺寸帽**+ **SEC-5 scrypt N（D5，先核 header）**。

### P3 — 质量与文档

17. **STOR-6 PATCH 空文本→E4001**、**STOR-8 截断告警**、**STOR-10 删除事务内检查**、**SVC-8 limit 非负 + 同步原子性**、**SVC-10 抽公共 helper**、**PIPE-3 编号收敛**、**CFG-2 磁盘守卫语义**、**Q-1/Q-2 清理**、**OPS-2/OPS-4 注释与 fsync**。
18. **DOC 漂移批量更新**（第 3.6 节 10 项）：待批准后一次性修订 `dev-plan.md` 附录 C/D/E、§5.7、README、plist 注释、ps1 注释、drill/gguf docstring。**注意**：`dev-plan.md` 是权威文档，修订需用户批准；本轮只登记。
19. **TEST-4 计时余量**、**TEST-5 lint continue-on-error + 去重**、**TEST-2 峰值内存门禁（阈值待 T3 校准）**。

## 5. 需决策项 → 已按原则制定的最佳方案（C 类）

> 用户授权：「需要我决策的项目，按照上述原则制定最佳方案」。以下按「功能全面、性能优先」+ 核心价值（离线、4 依赖闭环、数据安全、G6 等价）代为决策。

- **D1（STOR-1 / 原 T14）SQLite `synchronous`**：**决策 = 采用 `NORMAL`** + `mmap_size=256MB` + `cache_size=-65536` + 关闭 `PRAGMA optimize`。理由：WAL 模式下 NORMAL 对**应用崩溃**安全，仅 OS/断电丢最后一事务；转换产物可由源文件重跑恢复，非唯一真相源 → 性能优先可接受。配套断电 drill 登记为 B。
- **D2（TEST-5 / 原 CH-470）main 分支合并保护**：**决策 = 启用「require PR before merging」**，保护 132 门禁红线不被直推绕过。执行需 GitHub 仓库管理员权限 → 登记为 B（外部系统）。
- **D3（PERF-1）watcher 事件驱动 vs 轮询**：**决策 = 保持轮询为默认**（守住 4 运行时依赖闭环这一核心价值），先加 mtime 短路（A，无新依赖）；watchdog 事件驱动登记为**未来可选增强**，不默认引入。
- **D4（SVC-5 / SVC-6）绑定与冻结安全姿态**：**决策 = `api.bind` 默认强制 loopback**，非 loopback 需显式 opt-in 标志；**冻结 `run.offline_assert`**（NFR-1 红线键），禁止经 `PUT /settings` 修改。理由：与离线单机姿态一致。
- **D5（SEC-5）scrypt N**：**决策 = 新备份容器将 N 提到 2^17**，**前提**是 header 存储 KDF 参数以保证旧备份可解密——实施前须先核实 header 是否含 N；若不含则保持 2^14 并登记。低优先。
- **D6（STOR-7）外键**：**决策 = 不新增 FK**（避免表重建与 DB_VERSION 连锁），改为文档明确「手写级联是唯一机制」+ 加断言测试防孤儿。低优先。
- **D7（PIPE-4）行内跨度级链接**：**决策 = 维持块级链接模型**（≤1 link/block），把「部分覆盖行内链接被丢弃」写入文档已知限制；跨度级 inline 链接登记为未来 IR 增强，不在本轮做。

## 6. 需本机以外环境 / 人工测试项登记表（B 类，仅登记）

> 用户要求：「需要本机以外系统环境或人工测试的项目，仅登记」。下表合并 §9.2 未决 TODO、§12.7 九项未验证、以及本轮各发现的 NEEDS_EXTERNAL_VALIDATION。**本轮不执行**。

### 6.1 本轮新增/关联的外部验证项

| 编号 | 关联发现 | 类型 | 验证方法 | 阻塞的决策/实施 |
|---|---|---|---|---|
| X-1 | PIPE-1 | 制样 | 构造「字体异质 + >300 页」PDF golden，跑 shard≡single 断言 | PIPE-1 修复的反例证明 |
| X-2 | STOR-3 | 故障注入 | 填充库 FTS 迁移中途杀进程→重启→断言索引非空/对账触发 | STOR-3 原子性 |
| X-3 | STOR-1 / D1 | 断电 drill | `synchronous=NORMAL` 下断电/崩溃，验最后一事务丢失范围可接受 | D1 落地 |
| X-4 | STOR-4 | 负载/内存 | 大库迁移 RSS 测量（t6 bench 仪器） | STOR-4 流式化阈值 |
| X-5 | SVC-3 | HTTP 负载 | N 文件目录 `POST /jobs` 同步 vs run_batch 对照 | SVC-3 并行化收益 |
| X-6 | SVC-4 | 长运行负载 | 长期实例下 tasks 增长 + 5s 轮询延迟曲线 | SVC-4 保留策略 |
| X-7 | SEC-1 | 网络沙箱 | 带网络真沙箱确认 sendmsg/裸 socket/子进程无绕过被行使 | SEC-1 OS 级控制 |
| X-8 | SEC-3 | Windows 真机 | 确认 `-SetPassphrase` 存储位置（`HKCU\Environment` 明文） | SEC-3 DPAPI/CredMan |
| X-9 | OPS-1 | macOS 真机 | 观察 launchd `StartCalendarInterval` 实际触发时间（本地 vs UTC） | OPS-1 文档修正 |
| X-10 | OPS-3 | 信任模型 | 确认 GGUF 模型文件信任边界（是否仅运维提供） | OPS-3 尺寸帽必要性 |
| X-11 | PERF-1 | 大目录测量 | 大真实监听目录下轮询 CPU/延迟 | PERF-1 mtime 短路收益 |
| X-12 | TEST-1 | CI 核验 | 确认 `tests/golden` 已入 github.com/RunoobGo/doc2md；CI 报告 132 passed / 0 skipped | TEST-1 硬失败门禁 |
| X-13 | TEST-2 | Win11 校准 | 真实 8GB Win11 上校准峰值 RSS 阈值 | TEST-2 内存门禁 |
| X-14 | TEST-3 | Windows 真机 | schtasks 注册、cp936 控制台、真实 RSS、符号链接测试 | TEST-3 Windows CI 腿 |
| X-15 | TEST-6 | 真外部系统 | 真 llama-server + GGUF、真外部/加密备份卷、真浏览器编辑器 | TEST-6 覆盖 |

### 6.2 既有未决项（§9.2 TODO，沿用登记）

| 编号 | 项 | 类型 | 状态 |
|---|---|---|---|
| T3 | 跨机基线（Win11 / Mac mini m4 真机轮） | 真机 | 未决；本轮 TEST-2/3、X-13/14 依赖它 |
| T12 | serve/work 常驻资产核验 | 真机 | 未决 |
| T13 | backup v2 真机恢复演练 | 真机 | 未决 |
| T14 | `synchronous=NORMAL` 耐久性决策 | 决策 | **已由 D1 决策 = NORMAL**；断电 drill（X-3）仍待真机 |
| CH-470 | main「require PR before merge」 | 决策 | **已由 D2 决策 = 启用**；GitHub 管理员执行仍待外部 |

### 6.3 §12.7 九项未验证（沿用登记，均 B）

CI 直推绕过 · Python 3.11/3.13 矩阵 · 工作副本 git 能力 · 真浏览器编辑器验证 · Windows 11 真机 · Mac mini 真机 · 真 llama-server + GGUF · 备份外部卷 · >1000 文件语料跨平台。

## 7. 发现全表（速查）

| ID | 子系统 | 严重度 | A/B/C | 置信度 | 需外部验证 |
|---|---|---|---|---|---|
| PIPE-1 | 管线/正确性 | HIGH | A（制样 B） | high | 是(X-1) |
| STOR-5 | 存储/数据安全 | HIGH | A | medium | 否 |
| STOR-3 | 存储/健壮性 | HIGH | A（kill 测试 B） | medium | 是(X-2) |
| TEST-1 | 测试/CI | HIGH | A（核验 B） | high | 是(X-12) |
| TEST-3 | 测试/CI | HIGH | B | high | 是(X-14) |
| STOR-1 | 存储/性能 | MED | A（D1，drill B） | high | 是(X-3) |
| STOR-2 | 存储/功能 | MED | A | high | 否 |
| STOR-4 | 存储/性能 | MED | A（RSS B） | high | 是(X-4) |
| STOR-6 | 存储/功能 | MED | A | high | 否 |
| SVC-1 | 服务/安全 | MED | A | high | 否 |
| SVC-2 | 服务/契约 | MED | A | high | 否 |
| SVC-3 | 服务/性能 | MED | A（负载 B） | high | 是(X-5) |
| SVC-4 | 服务/性能 | MED | A（负载 B） | med-high | 是(X-6) |
| SVC-5 | 服务/安全 | MED | A（D4） | high | 否 |
| SVC-7 | 服务/文档 | MED/LOW | A(文档) | high | 否 |
| SEC-1 | 安全 | MED | A+B | high | 是(X-7) |
| SEC-3 | 安全/文档 | MED | A+B | high | 是(X-8) |
| SEC-4 | 安全/功能 | MED | A | high | 否 |
| OPS-1 | 运维/文档 | MED | A+B | high | 是(X-9) |
| OPS-3 | 运维/安全 | MED/LOW | A | medium | 是(X-10) |
| PERF-1 | 性能 | MED | A(D3)+B | high | 是(X-11) |
| TEST-2 | 测试/质量 | MED-HIGH | A（校准 B） | high | 是(X-13) |
| SVC-6 | 服务/安全 | LOW-MED | A(D4) | high | 否 |
| TEST-4 | 测试/主机 | LOW-MED | A | medium | 否 |
| PIPE-2 | 管线/性能 | LOW | A | high | 否 |
| PIPE-3 | 管线/质量 | LOW | A | high | 否 |
| PIPE-4 | 管线/功能 | LOW | C(D7) | high | 否 |
| STOR-7 | 存储/健壮性 | LOW | A(D6) | high | 否 |
| STOR-8 | 存储/功能 | LOW | A | high | 否 |
| STOR-9 | 存储/文档 | LOW | A(文档) | high | 否 |
| STOR-10 | 存储/质量 | LOW | A | high | 否 |
| SVC-8 | 服务/质量 | LOW | A | high | 否 |
| SVC-9 | 服务/安全 | LOW | A | high | 否 |
| SVC-10 | 服务/质量 | LOW | A | high | 否 |
| SEC-2 | 安全 | LOW | A | high | 否 |
| SEC-5 | 安全 | LOW | A(D5) | high | 否 |
| OPS-2 | 运维/文档 | LOW | A(文档) | high | 否 |
| OPS-4 | 运维/质量 | LOW | A | high | 否 |
| PERF-2 | 性能 | LOW | A | high | 否 |
| CFG-2 | 配置/质量 | LOW | A | high | 否 |
| Q-1 | 运维/质量 | LOW | A | high | 否 |
| Q-2 | 运维/质量 | LOW | A | high | 否 |
| TEST-5 | 测试/CI | LOW | A+B(D2) | high | 是(管理员) |
| TEST-6 | 测试/覆盖 | LOW | B | medium | 是(X-15) |
| CFG-1 | 配置 | 表扬 | — | high | — |
| CONS-1 | 一致性 | 表扬 | — | high | — |

统计：HIGH 5、MED/MED-HIGH 17、LOW 22、表扬 2（合计 46 条，含 2 表扬；实质发现 44）。A 类（沙箱可修，待批准）占多数；B 类外部/人工 15 项（X-1..X-15）+ 既有 T3/T12/T13 + §12.7 九项；C 类决策 7 项（D1..D7）已全部代为制定。

## 8. 提醒（用户要求：每次完成任务后提醒）

本轮审查任务已完成（只读，未实施、未提交、未推送）。**以下事项需本机以外环境或人工测试，已仅登记、未执行，请在具备条件时安排验证：**

- **真机轮（T3 系列）**：Windows 11（8GB，头号目标机）与 Mac mini m4——覆盖 X-8（口令注册表）、X-9（launchd 触发时间）、X-13（内存阈值校准）、X-14（schtasks/cp936/符号链接）、T12（常驻资产）、T13（备份 v2 真机恢复）。
- **CI / 仓库管理**：X-12（确认 golden 已入库、CI 报 132 passed/0 skipped）、D2/CH-470（在 GitHub `main` 启用 require-PR，需管理员）、TEST-5（lint continue-on-error）。
- **故障注入 / 负载**：X-2（FTS 迁移中途杀进程）、X-3（断电 drill）、X-4（大库 RSS）、X-5（HTTP 负载）、X-6（长运行 tasks 增长）、X-11（大目录轮询）。
- **真外部系统**：X-15（真 llama-server + GGUF、真外部/加密备份卷、真浏览器编辑器）、X-7（带网络沙箱验 netguard 无绕过）、X-10（GGUF 信任模型）、X-1（异质字体 >300p golden 制样）。
- **待你批准的决策落地**：D1（synchronous=NORMAL）、D2（require-PR）、D3（watcher 保持轮询 + mtime 短路）、D4（loopback 强制 + 冻结 offline_assert）、D5（scrypt N，先核 header）、D6（不加 FK）、D7（块级链接维持）。
- **待你批准的文档更新**：第 3.6 节 10 项漂移（`dev-plan.md` 为权威文档，需你批准后我才修订）。

> 下一步建议：若批准，按第 4 章 P0→P1→P2→P3 顺序实施；每个 A 类批次合并后在**本机**复跑 132 门禁并核对输出逐字节等价（G6），平台相关判断在产品内修而非 skip。
