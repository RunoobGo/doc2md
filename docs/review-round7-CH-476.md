# Doc2md 第七轮全面审查报告与优化方案（CH-476）

- 轮次：第 7 轮（前六轮 CH-459 / CH-464 / CH-469 / CH-471 / CH-473·474 / CH-475，见 `dev-plan.md` §9.4、§11）
- 日期：2026-10-17
- 范围：设计、代码、文档全面审查 + **功能完整性 gap 与性能专项**（本轮新增两个专审面）
- 原则：功能全面、性能优先
- 口径：**只读审查，本轮不实施任何改动**；交付为本报告 + 优化方案
- 审查面：管线核心（S1/S1.5/S3/S4/sharding/metrics/convert/llm）、服务层（api/scheduler/worker/storage/security）、运维与 CLI（cli/cron/ops 资产）、文档一致性、需求规格对照（第二章 FR/NFR）、性能专项
- 分诊法（沿用 §9.4）：**A = 沙箱内可改可验证**（给出精确改法，待批准后实施）；**B = 需本机以外环境/人工/构造语料**（仅登记）；**C = 需人决策**（已按原则代为制定最佳方案，见第 5 章）
- 置信度：high（四代理报告的关键项已由本人逐行复读确认）/ medium（子代理通读、本人未逐行复读），逐条标注

> 命名说明：本报告发现一律前缀 `R7-`。第六轮 A 类 12 项经复读确认均已落地（R6-PIPE-1 信号门、R6-PIPE-2 缩进保留、R6-LLM-1 valid_level/valid_marker、R6-SVC-1/2/3、R6-OPS-1/2/3 均在位）；第六轮延期项 B-3（注入归因循环）、B-4（claim 过滤）仍开放，转入本报告 6 章。

## 0. 门禁基线复现（本机）

```
pytest -m gate ：153 项全绿（72+72+9 通过点，无 F/E，EXIT=0）
doctor        ：dep_pdfplumber/docx/flask/cryptography 全 ok；cpu_count=10；
                min_free_mb=512，free_mb=448673；llm_mode=offline
```

结论：与 README 宣称一致。**本轮所有发现仍是这套已全绿门禁之外的盲区**。与第六轮不同的是：本轮在缺陷之外专审了**功能完整性（FR 对照）与性能**，并挖出前六轮性能优化（T14 synchronous=NORMAL、chunk_pages 分块、单事务合并）之后仍然存在的存量机会，其中最大一项直接解释了 12.3 实测「4 进程仅 4.5% 增益」之谜（见 R7-PERF-1）。

## 1. 执行摘要

项目成熟度评价维持「极高」：六轮修复后数据安全主线（终态守卫、乐观锁、级联复核、fail-closed 删除）扎实，离线契约、加密容器、等价性纪律无回潮。本轮新发现 **3 项 HIGH + 11 项 MED + 若干 LOW，另有功能 gap 5 组、性能优化机会 10 条**，集中在四类盲区：

1. **分片缝合盲区（R7-PIPE-1，HIGH）**：`_continuation` 的 R-04 编号续接比较**方向写反**——`ka` 是后块头编号、`kb` 是前块尾编号，续接本应要求 `ka[-1] == kb[-1] + 1`，代码写成 `kb[-1] == ka[-1] + 1`。自然递增序列（5→6）永不命中（该分支对真实语料是死代码），递减序列（6→5）反而命中；且该分支在 heading 守卫**之前** return，标题可被吸收进前片段落。子代理已端到端复现：前片末段「本方案共分 4.3」+ 后片首标题「4.2 部署要点」→ 标题文本被污染、正文并入 H2；片尾代码块可被片首引用吸收，极端时代码块渲染为空串（静默丢内容）。
2. **检索面库龄税（R7-STOR-1，HIGH）**：FTS5 表 `search_blocks` 的 `doc_id/block_id` 均为 UNINDEXED——`DELETE FROM search_blocks WHERE doc_id=?`（save_ir / reindex / delete_doc）与单块编辑的 `WHERE doc_id=? AND block_id=?` **全部退化为对全 FTS 内容表的线性扫描**。10 万块级库上每次单块保存都是一次全库扫描，且发生在 `BEGIN IMMEDIATE` 写锁内（阻塞同期 writer）；worker 批量落库 100 文档 = 100 次全库扫。检索/编辑面随库龄单调劣化，小测试库门禁测不出。
3. **不可信输入残余（R7-LLM-1，HIGH）**：R6-LLM-1 只规约了 `level`/`marker`，**target 未设防**——`target:5` 在 `_targets` 抛 `TypeError`；`target:["b1"]` 通过全部 5 道校验后在 `apply_ops` 的 `index.get(["b1"])` 抛 unhashable，整篇转换 E5000。server 模式下模型输出是不可信 JSON，违反「单算子畸形不得让整篇转换崩溃」的既定口径。

MED 级集中在：迁移漏索引（R7-STOR-2：`ix_events_*`/`ix_tasks_doc` 补丁未 bump `DB_VERSION`，user_version=5 的存量库**永远不会获得**这些索引）、删除×在途转换竞争（R7-SVC-1：`_finish_ok` 先 `save_ir` 后无条件 UPDATE，删除级联与转换竞争可**复活已删文档**并进入后续备份，破坏 FR-128「已删数据 14 天轮出」）、缝合页差约束过窄（R7-PIPE-2：跨 ≥3 页长块在片界漏判去重，内容重复两份）、代码围栏字面行（R7-PIPE-3：code 块含 ``` 行时渲染破损）、DOCX Heading hint 被 `_fix_levels` 无条件覆盖（R7-PIPE-4）、`backup drill --keep` 取证目录落在 `work/tmp/` 被 30 分钟后清扫（R7-OPS-1）、噪声页判定缺口（R7-PIPE-5）与双栏链接误附（R7-PIPE-6）。

**功能完整性（gap 5 组）**：最重是 **FR-102「修订痕迹」声明未实现且属静默丢内容**——python-docx 的 `p.text` 不含 `w:ins` 内的 run，含修订的 DOCX 修订插入正文**整段丢失、无任何提示**（GAP-1，P1）；其次脚注/尾注未声明未抽取（GAP-2，P2）、表格单元格多段落并为一行（GAP-3，P2）、一批已实现功能无 FR 编号验收口径（GAP-4，P3）。

**性能专项（性能优先，本轮重点）**：最大存量机会是 **队列消费每轮重建进程池**（PERF-1）——`drain_once` 每轮只认领 `workers` 个作业、`run_batch` 每次新建 `ProcessPoolExecutor` 并在 finally 无条件 terminate 全部 worker，每个新 worker 重付 `import pdfplumber`（实测 0.166s）；12.3 的「workers=4 仅快 4.5%」正是被池重建+重导入吃掉，spawn 平台（Windows）每 worker 还要重导 doc2md+解释器启动，**预期更差且从未实测**。其余：同源文件三次全量哈希（PERF-2）、大目录串行哈希入队（PERF-3）、FTS rowid 映射（STOR-1）、启动对账瘦身、入队批量化等，均为 G6 零风险或门禁可验证。

**性能优先视角**：本轮 A 类修复多为性能中性，但 STOR-1/STOR-2 与 PERF-1…3 是**真正的性能收益项**——这是六轮以来首个「优化本身即主线」的轮次；全部方案不破坏 G6 逐字节等价（或差异可由门禁重跑验证）。

## 2. 审查方法与证据口径

- 证据优先：每条发现给 `文件:行` + 问题 + 影响 + 建议 + A/B/C + 置信度。
- 只读：未修改任何源码或权威文档。
- 信任但验证：4 个子代理分区通读全文（管线核心 / 服务层 / 运维·CLI·文档 / 功能完整性+性能专项）；本人对全部 **HIGH 与 MED-HIGH 关键项逐行复读确认**（PIPE-1、LLM-1、STOR-1、STOR-2、SVC-1、PERF-1、PERF-2、OPS-1、DOC-1，另复读 s3_structure.py / worker.py / scheduler.py / batch.py / metrics.py / fts.py 全文与 sharding.py、llm.py 关键段），标 high；其余标 medium。
- 门禁基线本机复现（§0）。
- 决策代理：C 类按「功能全面、性能优先」+ 核心价值（离线、4 依赖闭环、数据安全、G6 等价）代为制定，见第 5 章。

## 3. 发现清单（逐条证据）

### 3.1 管线核心（R7-PIPE / R7-LLM）

**R7-PIPE-1 · 正确性/分片缝合编号续接方向反置 · HIGH · 置信 high · A 类（含决策 D3）**
- 证据：`doc2md/pipeline/sharding.py:83-87`
  ```python
  ma, mb = _NUM_HEAD.match(tb), _NUM_TAIL.search(ta)
  ka, kb = _num_key(ma.group(1)), _num_key(mb.group(1))
  if ka[:-1] == kb[:-1] and kb[-1] == ka[-1] + 1: return True
  ```
  `ma` 匹配**后块头部**编号（`tb`）、`mb` 匹配**前块尾部**编号（`ta`）；跨片续接（前片尾 N、后片头 N+1）应判 `ka[-1] == kb[-1] + 1`，现条件为 `kb[-1] == ka[-1] + 1`——**方向反置**。子代理隔离验证：递增 5→6、4.2→4.3 均 False（对自然序列是死代码），递减 6→5、4.3→4.2 均 True。该分支在 `:90-91` 的 heading 守卫**之前** return（绕过 CH-469「标题不得被吸收」）；字体分支（`:92-96`）只挡 heading/table，不挡 code/quote/page_break（三者 `font={}` 时 family/size 恒匹配）。端到端复现（max_pages=2）：前片末段「本方案共分 4.3」+ 后片首标题「4.2 部署要点」→ stitched=1，标题文本变 `'本方案共分 4.3\n4.2 部署要点'`；code→quote 经字体分支同样命中；page_break 吸收 code 后渲染空串 = 代码整块静默消失。
- 影响：分片模式下片界结构块可被错误吸收，正文丢失/标题污染——「分片≡单片」（G1/G6）在 seam 形态语料上被破坏；现有 320p 门禁语料无此 seam 形态故漏检，单测只测字体分支续接。
- 建议（A）：① 比较改 `ka[-1] == kb[-1] + 1`；② 结构块守卫（heading/code/quote/page_break）移到编号分支之前，编号续接仅限 `paragraph/list_item`；③ 补递增/递减/结构块三类 seam 回归测试。现有语料无该 seam，修后产物应逐字节不变（G6 门禁重跑确认）。

**R7-LLM-1 · 健壮性/算子 target 参数未设防 · HIGH · 置信 high · A 类**
- 证据：`doc2md/llm.py:167-168` `t = op.get("target") or op.get("block_id")`；`targets = [t] if isinstance(t, str) else list(t or [])`——`t=5`（truthy int）→ `list(5)` 抛 `TypeError`。`doc2md/llm.py` apply_ops 段 `b = index.get(op.get("target"))`——`target=["b1"]` 经 `_targets` 规约成 `["b1"]` 后通过全部 5 道校验（目标存在、非 immutable、未占用），`index.get(["b1"])` 抛 unhashable → 整篇转换 E5000。子代理两条路径均已实测复现。
- 影响：R6-LLM-1 收口了 level/marker，target 是同一防线的漏项；server 模式下模型输出（不可信 JSON）单字段即崩整篇转换，违反「拒绝而非崩溃」口径。offline 路径 op 为内部生成不受影响。
- 建议（A）：`_targets` 对非 `str/list/tuple` 一律归空（计 `no_target` 拒绝）；列表元素逐个 `isinstance(str)` 过滤；`apply_ops` 取 target 同步规约。补畸形 target 参数化单测（5 / True / ["b1"] / None）。

**R7-PIPE-2 · 正确性(G6)/缝合页差约束过窄 · MED · 置信 high · A 类**
- 证据：`doc2md/pipeline/sharding.py:192` `_seam_suffix` 的 `b.page != a.page + 1: continue`。R5-PIPE-5 的相邻页约束只对「完整块起始页 = 重叠窗口首页−1」成立；跨 ≥3 页长块（长段/代码清单/表格，起始页更早）页差 ≥2 → 尾缀漏判、重复内容保留两份。子代理实测：a(page=0,全文)+b(page=2,真后缀) 留 2 块；a(page=1)+b(page=2) 正确去重。G6 门禁 0.99 相似度容忍重复（`test_v24_gates.py:90` 注释自认 101 字重复段可过）。
- 建议（A）：页差放宽为 `0 < b.page - a.page <= overlap+1`（保留对非相邻重复短句的防误删语义），或仿 `_seam_truncated` 用 `shard_bounds` 锚定「完整块属前片、后缀块属后片」；补跨 3 页块 fixture 的块数相等断言。

**R7-PIPE-3 · 正确性/代码围栏字面行 · MED · 置信 high · A 类**
- 证据：`doc2md/pipeline/s4_render.py:53` `return "```\n" + text + "\n```"` 固定三反引号；S3 的 mono/indent 代码块可含 ``` 字面行（`s3_structure.py:217-228` 作为内容累积）。code 块含 ```` ```bash ```` 行时渲染出嵌套破损 Markdown（内层提前闭栏）。
- 建议（A）：按 CommonMark 规则取 text 内最长反引号/波浪线 run，围栏长度 = run+1。仅影响含围栏字面行的文档，现有语料字节不变；补 fixture。

**R7-PIPE-4 · 正确性/DOCX hint 被 `_fix_levels` 覆盖 · MED · 置信 high · A 类**
- 证据：`doc2md/pipeline/s3_structure.py:361-362` `if hint: return hint`（显式声明优先）；`:452-469` `_fix_levels` 对 `_NUM.match` 命中的标题一律改为 `dots+1`，无条件覆盖 hint。实测 level_hint=2、文本「3.2.1 实施细则」→ 最终 level=3，与模块 docstring 直接矛盾。附带：同前缀 key 的点数数学上恒相同，`seen` 兄弟机制是死逻辑（sibling 恒等于 lvl）。
- 建议（A）：hint 命中时打 `flags["level_from_hint"]`，`_fix_levels` 跳过该块；死逻辑删除或如实改注释。补 hint≠编号深度的 DOCX fixture。分片/单片行为一致，G6 不破。

**R7-PIPE-5 · 健壮性/噪声页判定缺口 · MED · 置信 high · C 类（阈值需语料，转 B 验证）**
- 证据：`doc2md/pipeline/s1_5_gate.py:189-190` `empty = chars < self.empty_chars or nlines == 0`——auto 判定面只认 empty-ish；noise_ratio>0.9 的纯噪声页只要 ≥20 字符即放行（30 页纯符号噪声实测 PASS）。ToUnicode 损坏的 PDF 常产出高密度乱码层（chars 高、letters≈0），`cjk_ratio/noise` 均不参与拒收。
- 建议（C，见 5.D5）：增设噪声判据（暂拟 `noise>0.9 且 letters/chars<0.1`，全文档连续 ≥3 页或占比超限拒收），**须配真实乱码语料评估误杀面**（图表页不误杀）→ 实施前登记 B。

**R7-PIPE-6 · 正确性/链接横向重叠缺失 · MED · 置信 medium · B 类**
- 证据：`doc2md/pipeline/s1_parse.py:200` Link 附着仅查纵向条带相交（`atop<bottom and abot>top`），无横向重叠。双栏/页眉带链接 PDF 中，右栏或页眉 URI 附到同纵向带首行，S3/S4 将整段包成 `[整段](错url)`。附注（LOW）：`s4_render.py:74` `[label](url)` 未转义 `()[]`，URL 含 `)` 即断链。
- 建议（B）：geoms 扩为 (top,bottom,x0,x1) 要求横向重叠；会改变含链接 PDF 产物字节（G6 门禁重跑）；误附面需真实双栏链接语料评估（6.1 B-2）。

**R7-PERF（管线侧）· 性能优化机会 · 置信 high · A 类（逐条）**
- **PERF-a**：`scan_injection`（`convert.py:188` → `llm.py:43-68` 30+ 分支大正则逐块全文扫）——先做少量高选择字面量子串预检再跑正则，6k 块/590k 字文档预估省 80~150ms；纯只读不破 G6。
- **PERF-b**：`_page_metrics`（`s1_5_gate.py:82-90`）auto 模式全页 4 正则 + 2 次全文 join——合并为一次 finditer/translate 计数，320 页预估省 50~100ms；以 gate.detail 逐字段等价断言验证。
- **PERF-c**：`batch.py:146` 逐件 submit、cfg 每件 pickle——改 `initializer` 一次传入，万件批次省 N×pickle(cfg) CPU。
- **PERF-d**：`_docx_para_link`（`s1_parse.py:353`）每段 `findall(.//hyperlink)`——body 无 hyperlink 元素时文档级跳过，10 万段 DOCX 预估省 100~300ms。
- **PERF-e**：`stitch` 降级前重试（`convert.py:118-124` 异常即全量重跑 analyze）——先 dedupe=False 重试一次缝合，异常路径省一遍 S3（320 页约数秒）；降级产物差异罕见路径，shard gate 补对照。
- **PERF-f**：`GateCollector` 流式接线（`s1_5_gate.py:134-138` 自认「已具备未接线」）——分块循环接 `feed(offset=)`，门禁拒收时页对象早释放，内存收益约 10%（A-8 实测 pages_all 占驻留约 1/10）。
- 评估后**不建议**：`_join_back` 扫描上限会改判定语义破坏 G6，仅登记；分片级 S3/S4 并行复杂度/收益比差（A-8 已实测回退）。

### 3.2 服务层与存储（R7-SVC / R7-STOR / R7-SEC）

**R7-STOR-1 · 性能/FTS UNINDEXED 删除全表扫 · HIGH · 置信 high · A 类（含决策 D1）**
- 证据：`doc2md/storage/repo.py:83`（save_ir）、`:114`（reindex）、`:164`（reindex_block，每次单块编辑 PATCH 都走）、`:315`（delete_doc 级联）均为 `DELETE FROM search_blocks WHERE doc_id=? [AND block_id=?]`；`db.py:120-122` 建表 `doc_id UNINDEXED, block_id UNINDEXED`。FTS5 对 UNINDEXED 列无索引，这些 DELETE 只能线性扫全 FTS 内容表。
- 影响：单块编辑 = O(全库已索引行数) 且在 `BEGIN IMMEDIATE` 写锁内；worker 批量落库每文档一次全库扫。库龄放大型隐性税，小测试库门禁测不出。
- 建议（A）：入库显式赋 rowid；另建普通映射表 `search_map(doc_id, block_id, fts_rowid, PRIMARY KEY(doc_id, block_id)) + INDEX(doc_id)`；删除改 `DELETE FROM search_blocks WHERE rowid IN (SELECT fts_rowid FROM search_map WHERE doc_id=?)` 后清映射。单块编辑从 O(N全库) 降为 O(1)。属 schema 变化 → `DB_VERSION` 5→6（与 R7-STOR-2 合并一笔，见 5.D1）。

**R7-STOR-2 · 迁移正确性/索引补丁未 bump 版本 · MED · 置信 high · A 类**
- 证据：`db.py:213-216` `cur_ver < DB_VERSION` 才 `executescript(DDL)`；`ix_events_job/ix_events_doc/ix_tasks_doc`（`db.py:107,117-118`，CH-469 加入）**未 bump `DB_VERSION`**（仍 5，4→5 的 bump 发生在更早的 525f80c）。在两版之间部署过的库 `user_version=5` → 永远跳过 DDL → 三个索引**永不创建**；后续 ALTER 补列逻辑不含索引。
- 影响：`GET /jobs/<id>/events`、删除级联的 events/tasks DELETE 在这类库上退化为全表扫描，事件表按保留期增长后随库龄线性变慢、无告警。
- 建议（A）：`_migrate` 无条件执行 `executescript(DDL)`（全部 `IF NOT EXISTS`，幂等毫秒级）；与 STOR-1 一并 bump `DB_VERSION=6`。

**R7-SVC-1 · 数据安全/删除×在途转换竞争 · MED · 置信 high · A 类（含决策 D5）**
- 证据：`repo.py:319` delete_doc 级联 `DELETE FROM tasks WHERE doc_id=?`；`worker.py:215-219` `_finish_ok` 事务内**先** `save_ir`（无条件重插 docs/blocks）**再** `UPDATE tasks SET status='done' WHERE job_id=?`（不看 rowcount，且绕过 `scheduler.mark` 的 R6-SVC-3 终态守卫）；`api.py` `_finish_one` 同构。
- 影响：admin 执行 `DELETE /docs/{id}` 后，在途转换随后 `_finish_ok` 把 docs/blocks **整篇复活**（zip 已 purge → 正文有、markdown 404 的半复活态）；墓碑仍在而新内容进入后续备份，破坏 FR-128「已删数据 ≤14 天轮出」。
- 建议（A）：`_finish_ok` 事务内**先** `UPDATE tasks ... WHERE job_id=?` 并查 rowcount：为 0（该行已被级联收走）→ 回滚、按 `doc_deleted_during_conversion` 记 rejected，不再 save_ir。api 侧同序。可用「先删 tasks 行再调 _finish_ok」的确定性单测验证（沙箱可验，故列 A；并发复现另登记 B-4）。

**R7-SVC-2 · 输入校验/mode 未白名单 · LOW · 置信 high · C 类**
- 证据：`api.py:249` `mode = b.get("mode")` 直送 tasks.mode→run_batch；`convert.py:89-91` 仅区分 `"single"`，任意串按 auto 执行；CLI 侧 `--mode` 有 choices（`cli.py:106`）而 API 面没有。
- 建议：`mode not in (None,"auto","single","two_phase")` → 400/E4001（与 role 白名单同口径）。

**R7-SVC-3 · 健壮性/同步池闸门无超时 · LOW · 置信 medium · C 类**
- 证据：`api.py:313` `with _SYNC_POOL_GATE:`——BoundedSemaphore.acquire 无超时，小时级大批量占池期间后续 `POST /jobs` 请求线程无限期挂起。
- 建议：`acquire(timeout=N)` 超时回 503/429 + Retry-After。

**R7-SVC-4 · 输入校验/reason/label 未验证 · LOW · 置信 high · C 类**
- 证据：`edits.py:71`、`api.py:639` `b.get("reason","")` / `b.get("label","")` 未验证类型/长度，传对象 → sqlite InterfaceError → E5000（应为 400）；label 无上限。
- 建议：`isinstance(str)` + 截断（256）。

**R7-SVC-5 · 并发参数张力/stale 与 batch 超时 · LOW · 置信 medium · B 类**
- 证据：`worker.py:91-95` reclaim 按 `perf.stale_job_minutes=30` 重排 running；`default_toml.py:61` `batch_file_timeout_s=0`。合法 >30min 千页转换会被另一进程重新入队 → 双份转换、双 save_ir（无编辑时幂等收敛但浪费 CPU）。
- 建议：文档明示两键联动，或 reclaim 阈值自动取 `max(stale_job_minutes, batch_file_timeout_s)`。

**R7-SEC-1 · 前端防御纵深/esc 不转义单引号 · LOW · 置信 medium · C 类**
- 证据：`editor.html:288` `esc()` 只转义 `& < > "`；当前无可达 XSS 向量（模板全双引号属性），但未来单引号属性插值即成漏洞。
- 建议：补 `'` → `&#39;`。另 `/healthz` 无鉴权泄露 docs 计数（仅元数据，登记备查）。

**R7-STOR（服务侧性能机会）**
- 入队批量化：`api.py:250-251` 异步路径每文件一次 `sched.submit`（一次 BEGIN IMMEDIATE+COMMIT）；5000 文件目录 ≈ 5000 次提交，加 `submit_many` 单事务 executemany，预计 3-8s → <0.5s。
- 启动对账瘦身：`repo.py:132-137` `reconcile_search` 每次启动对 blocks 与 FTS 各做全表 GROUP BY；先做一条总计数等值比较，不等才展开。
- 锁粒度：`repo.py:342-344` `delete_source` 首事务用 `store.tx()`（BEGIN IMMEDIATE）做只读 SELECT，白持写锁；改 `store.q` 普读。
- 审计排序索引：`api.py:483` `ORDER BY rowid + WHERE doc_id=?` 补 `edit_audit(doc_id, id)` 索引（可与 D1 合并进 DB_VERSION=6）。
- 检索/队列 limit 上限：`api.py:523/:370` limit 正值无上限，reader 可强制全库 snippet 物化；加硬上限（如 500）。
- 大文件 IO：`api.py:417` get_markdown 整篇读入内存；分片大文档 MB 级，建议 chunked Response。

### 3.3 运维与 CLI（R7-OPS）

**R7-OPS-1 · 运维/drill --keep 取证目录被清扫 · MED · 置信 high · A 类**
- 证据：`doc2md/ops/drill.py:123` `drill = root/"tmp"/f"drill-{ts}"`；`cron.py:62-83` `sweep_tmp` 对 tmp 全量清理（仅 30 分钟 mtime 保护窗）；`api.py:78` serve 启动同样清扫；`cli.py:101` `--keep` help 承诺「取证用」。`tests/test_t7_drill.py:101` 只断言立即存在。
- 影响：演练结束 30 分钟后，下一次 `cron --daily` 或 serve 重启即把取证目录整个 rmtree，「留存取证」承诺失效。
- 建议（A）：沙箱迁至 `reports/drill-<ts>/`，或 `sweep_tmp` 豁免 `drill-*` 前缀；补「清扫后仍在」断言。（迁移 + 断言均沙箱可验，故列 A。）

**R7-OPS-2 · cron 日报硬编码版本号 · LOW · 置信 high · A 类**
- 证据：`cron.py:243` 硬编码 `"pipeline": "2.4.0"` 绕过 `PIPELINE_VERSION`（doctor/healthz 均用常量）。另 `cron.py:219` 报告文件名本地 `strftime` 而日报日期 UTC 的不一致。
- 建议：改 `from . import PIPELINE_VERSION`，统一时区口径。

**R7-OPS-3 · CLI 权限口径分裂 · LOW · 置信 high · B 类**
- 证据：`cli.py:182` serve 硬编码 `os.chmod(root, 0o700)`；`cli.py:369` cmd_work 只 mkdir 不 chmod；备份面已参数化 `ops.work_dir_mode`（`ops/backup.py:186/213-215`）。
- 建议：serve/work 统一走 `_parse_mode`/`_enforce_mode` 同口径（行为面变更需在 POSIX/非 POSIX 双面验证 → 实施列 A、口径确认属 C）。

**R7-OPS（性能机会）**：cron `events_purge/tasks_purge` 各占一事务可合并（`cron.py:181-207`）；doctor 4 个重库逐个 `__import__`（pdfplumber 约 1s）改 `importlib.util.find_spec` 判存在、缺失才真导入（`cli.py:558-563`）；备份每块 nonce/密文两次 `dst.write` 拼单缓冲减半 syscall（`backup.py:98-99`，原子 rename+fsync 口径勿动）；daily 00:00 与 weekly 00:30 若 daily 超 30 分钟会双进程同写 cron.jsonl（轮转互踩风险）→ `cron.run` 入口加 flock 或错峰。

### 3.4 文档一致性（R7-DOC，组）

**R7-DOC-1 · README 编号口径滞后 · MED · 置信 high（本人复读确认）· A 类**
- 证据：`README.md:135`「当前编号至 `CH-474`，下一条 `CH-475`」；`dev-plan.md:1176` 已是「当前至 `CH-475`，下一条自 `CH-476` 起」。CH-472 重复占号事件的重演风险。

**R7-DOC-2 · 附录 E/主题篇 6.5 cron 步骤表缺两步 · MED · 置信 high · A 类**
- 证据：实际 cron 共 10 步（`cron.py:101-207`，含 CH-471 的 tasks_purge、search_reconcile），附录 E 步骤表（`dev-plan.md:968-971`）止于 events_purge；主题篇 6.5（`dev-plan.md:543`）顺序亦与代码相左。

**R7-DOC-3 · 备份容器/轮出描述停在 CH-469 前口径 · MED · 置信 high · A 类**
- 证据：`dev-plan.md:527-529`、`ops/backup.md:21/73`——AAD 实际绑**整个容器头**（`backup.py:63-71`，O-4）而文档写「魔数+total+序号」；rotate 只认 `state-<UTC秒>.sqlite[.enc]` 名形且取「名内时刻 vs mtime 更早者」（`backup.py:309-343`，O-2）而文档只说按 mtime。按文档写兼容解密器会认证失败。

**R7-DOC-4 · README CLI 摘要缺项 · LOW · A 类**：缺 `backup drill`；convert 行缺 `--llm-mode/--llm-no-fallback`；`README.md:13`「serve / llm 常驻资产」与 dev-plan 9.2 T12「serve / work」不符。

**R7-DOC-5 · 附录 B 退出码列不全 · LOW · A 类**：`serve token` 行漏 revoke 未命中→4、role 越界→2；`serve` 行漏非回环 `--host`→2（`cli.py:190-242`）。

**R7-DOC-6 · ops/llm.md 示例路径 · LOW · C 类**：`ops/llm.md:41` verify 示例与默认落点 `vendor/models/llm/tokenizer.manifest.json` 不符，照抄即退出码 5。

**R7-DOC-7 · 代码注释漂移（synchronous=FULL 口径）· LOW · A 类**：`scheduler.py:84-85`、`repo.py:26-27`、`worker.py:197-198` 仍以「synchronous=FULL 每次 fsync」为现行依据，`db.py:191-196` 已是 NORMAL（T14）。另 12.3 结论②的归因（DB 写入/进程调度/入队打印）先于 T14 与 A-4，fsync 已除后未复测，本轮证据指向池重建/重导入为主因 → 建议随 PERF-1 重跑 t6 重归因。

### 3.5 测试面（R7-TEST）

**R7-TEST-1 · CLI cron 入口零覆盖 · MED · 置信 high · A 类**
- 证据：全 tests/ 无一用例走 `cli.main(["cron",...])`/`cmd_cron`（cron 仅模块层直测，`test_m8_ops.py:367-405`）；launchd/schtasks 生产触发的恰是该入口——`--daily/--weekly` 互斥、`--no-backup` 接线、`rep.ok`→0/5、`_jline` ASCII 输出均无断言。
- 建议：仿 `test_cli_entry.py` 风格补 2-3 例（daily 成功 0、失败步→5、`--no-backup` 跳过备份三步）。沙箱可做，列 A。

**R7-TEST-2 · launchd 资产守卫增强 · LOW · 置信 medium · C 类**：R6-OPS-1 守卫已在位（不重复上报）；仍缺 StandardOutPath/ErrorPath 与 root/logs 派生关系、exe 路径与 WorkingDirectory 同根一致性断言。

### 3.6 功能完整性 gap（R7-GAP，规格对照）

**R7-GAP-1 · FR-102「修订痕迹」未实现，静默丢内容 · P1 · 置信 high（代理在 site-packages 验证）· A 类（含决策 D4）**
- 证据：`dev-plan.md` L85 声明「段落/样式/链接/修订痕迹」；`s1_parse.py:313-339` `_docx_para_line` 只用 `p.text`/`p.runs`，而 python-docx `CT_P.text` 只取 `w:r | w:hyperlink` 直接子级——`w:ins` 内的 run 不在其中 → **修订插入的正文整段丢失、无任何提示**。修订路径零测试覆盖；黄金样本自认「无真实修订标记」（`make_golden.py:551-562`）。
- 建议：`_docx_para_line` 遍历 `p._element.iter(w:r)`（含 `w:ins` 内），块 flags 记 `has_revision`；补真实 `w:ins` 样本与断言。G6：新增能力面，黄金样本需扩展（见 5.D4）。

**R7-GAP-2 · DOCX 脚注/尾注未声明未抽取 · P2 · A 类**
- 证据：非目标清单（L50）只排除图片/OCR/扫描件/公式；`read_docx`（`s1_parse.py:386-415`）只遍历 document.xml body，`footnotes.xml`/`endnotes.xml` 不读 → 学术/法律文档脚注正文整块丢失。
- 建议：读 footnote part 追加为段落块（内容保真）；非目标清单同步补声明，消除「未写即丢」。

**R7-GAP-3 · 表格单元格多段落并为一行 · P2 · A 类（DOCX 半）**
- 证据：`_docx_table_rows`（`s1_parse.py:374-383`）`tc.iter(w:t)` 把单元格内多段落并为一行、嵌套表拍平。DOCX 单元格内段落分隔（段落边界→空格）改动极小收益明确；PDF 合并单元格无版面模型不建议硬做（登记不做）。

**R7-GAP-4 · 已实现隐性功能无 FR 编号 · P3 · A 类（文档）**
- 证据：`/search` 读侧、`GET /docs/<id>/markdown`、token 签发/吊销、`PUT /settings`+`/system/reload`、审计端点、静态编辑器页均已实现但第二章无对应 FR——「凡既无断言也无实测不得标成立」纪律对这批功能失效。建议补编号与验收判据。

**R7-GAP-5 · 措辞含糊条目 · P3 · A 类（文档）**
- 证据：FR-104「编号体系」未定义形态集合（代码仅 `_NUM`+`_CJK_HEAD` 两类）；NFR-22「近线性于核数」无最小文件规模口径——12.3 实测单页语料仅 4.5% 增益，靠「上限内」措辞掩盖冲突。

### 3.7 性能专项（R7-PERF 主清单，性能优先）

**R7-PERF-1 · 队列消费每轮重建进程池 · 收益最大 · 置信 high（本人复读确认）· A 类（含决策 D2）**
- 证据：`worker.py:121` `jobs = claim(store, n)` 领取量=池大小 n；`worker.py:143-145` 每轮调 `run_batch`；`batch.py:143` 每次新建 `ProcessPoolExecutor`，`batch.py:196-224` finally 无条件 terminate 全部 worker；`import pdfplumber` 懒加载（`s1_parse.py:116`）→ 每轮每个新 worker 重付导入（本机实测 0.166s；doc2md.pipeline.batch 导入 0.067s）。12.3 T6 workers=4 排空 49.1s ≈ 267 轮 ×（池重建+导入 ≈0.17s + 4 个单页转换）——与「仅快 4.5%」互证：**并发增益被池重建吃掉**。T6 全部数字来自 Linux fork 沙箱；Win11 spawn 每 worker 还需重导 doc2md + 解释器启动，预期更差且从未实测。
- 方案（见 5.D2）：**领取量与 workers 解耦**（claim 4×workers，改动最小，不动 X-17 的「无条件 terminate」根治）；持久池为备选（与 X-17 收尾语义有张力，须 Win11 实测后再议）。收益：单页语料 workers=4 预期从 4.5% 提到接近 8×40 页语料的 ~2×；lite 档常驻 work 消除每轮固定开销。G6 不破（子进程转换代码不变）；验证：t6 A/B + 「队列产物 vs 串行产物逐条目 sha256」既有红线。

**R7-PERF-2 · 同源文件三次全量哈希 · 置信 high（本人复读确认）· A 类**
- 证据：`worker.py:278`（enqueue）、`convert.py:61`（_convert）、`worker.py:207`（_finish_ok 漂移复核）各一次 `hash_of_path` 全量流式读。大文档（20MB+）每任务省 2 次全文读 ≈100-300ms；G6 无影响（doc_id 值不变）。漂移检测改用子进程回报 doc_id + stat 比对属行为面变更，需补测试。

**R7-PERF-3 · 异步入队大目录串行哈希 · A 类**
- 证据：`api.py:246-255` 循环 sniff+enqueue；`worker.py:274-283` 单线程 `hash_of_path`。G5「秒级返回」实测（13 作业 13ms）基于小文件；GB 级目录按 ~300-500MB/s 需数秒。hashlib 对 >2KB 缓冲释放 GIL，线程池并行哈希近线性（1GB 目录 3-4s → ~1s）。G6 无影响；验证：入队延迟基准 + 附着/幂等不变断言。

**R7-PERF-4 · 逐 job 一事务 + 每事件 flush · 低优先级 A 类**：`worker.py:146-187` 逐条 `_finish_ok` 单事务；`api.py:752-754` `_emit` 每事件 flush。T14 后单提交已无 fsync，预估仅 ~2-5ms/job；整轮合单事务需 savepoint 解决单 IR 坏整批回滚问题。

**R7-PERF-5 · 同步小批逐文件重复开销 · 可选 A 类**：`api.py:337-359` 每文件 config.validate + offline_guard 装卸 + sniff，毫秒级。

（管线/服务/运维侧零散机会已并入 3.1–3.3 各 PERF 小节，不重复计数。）

### 3.7a 规格与实测脱节（性能归因）

- 12.3 结论②（L1406）把单页语料瓶颈归为「DB 写入、进程调度、入队逐条打印」——该归因先于 T14（synchronous=NORMAL）与 A-4（单事务合并）；fsync 已除后未复测，本轮证据指向**池重建/重导入**为主因。建议随 PERF-1 实施重跑 t6 重归因并回填第十二章。
- NFR-22「近线性于核数」与 12.3 单页语料 4.5% 增益的口径冲突由 GAP-5 收口（补最小文件规模口径）。

## 4. 实施顺序建议（待批准后执行，本轮不实施）

按「数据安全 > 正确性 > 性能主线（性能优先）> 健壮性 > 运维 > 功能补齐 > 文档」排序；每项实施后复跑 `pytest -m gate`（须仍 153 全绿）+ 受影响产物 G6 等价复核：

| 序 | 项 | 级别 | 类 | 说明 |
|----|----|------|----|------|
| 1 | R7-SVC-1（删除×在途转换竞争） | MED | A | 数据安全硬伤：半复活态 + 破坏 FR-128 轮出 |
| 2 | R7-PIPE-1（缝合编号方向 + 守卫顺序） | HIGH | A | 分片模式结构块被吸收/静默丢内容 |
| 3 | R7-LLM-1（target 参数设防） | HIGH | A | 不可信输入崩整篇转换 |
| 4 | R7-STOR-1 + STOR-2（FTS rowid 映射 + 迁移无条件 DDL，DB_VERSION 5→6） | HIGH/MED | A | 检索面库龄税 + 存量库索引缺位；**性能主线第 1 刀** |
| 5 | R7-PERF-1（认领量与 workers 解耦）+ 重跑 t6 重归因 | — | A | **性能主线第 2 刀**，解开 4.5% 之谜 |
| 6 | R7-PERF-2 / PERF-3（消三重哈希 / 入队并行哈希） | — | A | 性能主线第 3 刀 |
| 7 | R7-PIPE-2 / PIPE-3 / PIPE-4（缝合页差 / 围栏长度 / hint 优先） | MED | A | G6 与结构正确性收口 |
| 8 | R7-OPS-1（drill --keep 迁出 tmp）+ R7-TEST-1（cron 入口测试） | MED | A | 运维承诺兑现 + 生产入口覆盖 |
| 9 | R7-GAP-1（修订痕迹提取 + golden 扩展） | P1 | A | 功能全面：静默丢内容类 |
| 10 | R7-GAP-2 / GAP-3（脚注 / 单元格段落分隔） | P2 | A | 功能补齐 |
| 11 | R7-PERF-a…f + 服务/运维侧性能小项 | — | A | 零散性能机会（各 <150ms 级，批量做） |
| 12 | R7-SVC-2/3/4、R7-SEC-1（C 类已决方案落地） | LOW | A | 输入校验/纵深防御收口 |
| 13 | R7-DOC-1…7 + R7-GAP-4/5（集中文档对齐 pass） | MED/LOW | A | 收尾，逐条复读后落笔 |

## 5. C 类决策（按「功能全面、性能优先」代为制定）

**D1（R7-STOR-1/2）· FTS 检索面重构口径**
- 决策：入库显式赋 rowid + 新建普通映射表 `search_map(doc_id, block_id, fts_rowid)`（`PRIMARY KEY(doc_id, block_id)` + `INDEX(doc_id)`），删除路径改 rowid 子查询；**同笔**把 `_migrate` 改为无条件执行幂等 DDL，并 bump `DB_VERSION` 5→6（顺带收编 `ix_events_*`/`ix_tasks_doc` 的存量库缺位与 `edit_audit(doc_id, id)` 审计排序索引）。
- 理由：性能优先——这是唯一随库龄单调劣化的主线成本（单块编辑 O(全库)→O(1)）；无条件幂等 DDL 毫秒级、比「依赖版本号」更不易再犯（本次漏 bump 即例证）。存量库迁移走既有 `_migrate_fts` 同款一次性回填，G6 无涉（检索面非产物面）。

**D2（R7-PERF-1）· 池策略取舍**
- 决策：**领取量与 workers 解耦**（`claim(store, max(workers, 4*workers))` 口径，即按批取件而非按池取件），不动 `run_batch` 的池生命周期与 X-17「无条件 terminate」收尾。
- 理由：持久池与 X-16/X-17 根治（池收尾强杀防 manager 死锁）存在直接张力——重建池正是当前防挂死语义的一部分；解耦领取量一行改动即消除「每轮 4 件 × 池重建」的放大系数，G6 零风险。持久池作为后续可选项，**须先有 Win11 spawn 实测数据**（登记 B-6）再议。性能优先但不以回归 X-16 死锁为代价。

**D3（R7-PIPE-1）· 缝合续接口径**
- 决策：比较修正为 `ka[-1] == kb[-1] + 1`；结构块守卫（heading/code/quote/page_break）前移到编号分支之前，编号续接仅限 paragraph/list_item；字体分支同样补齐 code/quote/page_break 拦截。
- 理由：与 CH-469「标题不得被吸收」口径统一并推广到全部结构块；现有语料无该 seam，修后字节不变（G6 门禁重跑确认）；补递增/递减/结构块三类 seam 回归把口径钉死。

**D4（R7-GAP-1）· 修订痕迹处理策略**
- 决策：**提取而非拒收**——遍历含 `w:ins` 的 run，块 flags 记 `has_revision`，正文保真；同时把「修订标记以接受后口径提取」写进第二章验收口径，扩展黄金样本（真实 `w:ins` DOCX）。
- 理由：功能全面优先——静默丢内容是转换器最不可接受的失败形态；拒收会把一大批法律/协作文档挡在门外，与「文本型 DOCX 全支持」的定位冲突。提取路径离线、无新依赖（python-docx 已在依赖闭包内）。

**D5（R7-PIPE-5）· 噪声门禁阈值**
- 决策：暂拟判据 `noise>0.9 且 letters/chars<0.1`，**连续 ≥3 页或全文档占比超限才拒收**（保图表页不误杀）；实施前必须先有真实乱码语料评估误杀面（登记 B-1），评估通过才落阈值。
- 理由：门禁收紧方向正确但阈值无实测依据就上线会把「图表页误杀」这类假阳性引入主链路；宁可分两步（先语料后阈值），不违背「绝不用宽松口径掩盖回归」也不制造新的误杀回归。

**D6（R7-SVC-1）· 终态写入顺序语义**
- 决策：`_finish_ok` 事务内先 `UPDATE tasks WHERE job_id=?` 查 rowcount（0 = 行已被删除级联收走）→ 回滚记 `rejected(doc_deleted_during_conversion)`；api `_finish_one` 同序；顺带把该 UPDATE 收编进 `scheduler.mark` 的终态守卫口径。
- 理由：数据安全优先——「删除后复活 + 进入备份」直接违反 FR-128；先查行存在再写产物是 check-and-act 的最小原子化，与 CH-471 STOR-10 写锁下复核同方法论。

**D7（R7-GAP-2/3）· 脚注与表格补齐取舍**
- 决策：脚注/尾注**补**（内容保真、离线无冲突，P2）；DOCX 单元格内段落分隔**补**（改动极小）；PDF 合并单元格**不补**（无版面模型前提下硬做必误判，登记为明确边界写入第二章非目标）。
- 理由：功能全面优先但受离线 + 无外部依赖约束——前两项是纯增益；后一项在现有信号（横向间隙）下不可判定，宁缺勿误（与 S3 「宁漏勿误」纪律一致）。

**D8（性能实施节奏）· 性能主线三刀的验收口径**
- 决策：按第 4 章序 4/5/6 分三批独立实施与验证（各批独立 A/B 实测数字回填第十二章），不做一次性大合并。
- 理由：每刀都有独立可测的收益主张（FTS O(1) 删除、池重建消除、哈希减半），分批才能把「实测数字进第十二章并标注语料与测量方法」的纪律落到实处；一旦某刀引入回归可单独回退。

## 6. B 类与人工/外部环境项（仅登记，本轮不做）

### 6.1 需构造语料 / 真实环境样本（沙箱可备样本后转 A）
- **B-1（验 R7-PIPE-5）**：真实 ToUnicode 损坏/高密度乱码 PDF 语料，评估噪声判据误杀面（图表页不误杀）。
- **B-2（验 R7-PIPE-6）**：真实双栏/页眉带链接 PDF 语料，评估链接横向重叠修复的误附面。
- **B-3（沿 R6 B-3）**：真实注入语料评估 `_INJECT_HINT` 词表误报/漏报率 + `_attribute` 归因去循环。
- **B-4（沿 R6 B-4 + 验 R7-SVC-1）**：并发压测（同步 serve + 常驻 worker + 并发删除）稳定复现 claim/删除竞争。

### 6.2 需本机以外系统环境 / 人工
- **B-5（沿 R6 B-5）**：launchd plist 修复后的 macOS 真机 `launchctl load` 跑一次 `cron --daily`，确认 `WorkingDirectory`/`--root` 生效（**人工/真机，建议优先**）。
- **B-6（验 R7-PERF-1，新增）**：Win11 spawn 平台池策略实测——T6 全部数字来自 Linux fork，目标机（8GB Win11）吞吐零实测；持久池取舍依赖此数据（**需目标机**）。
- **B-7（沿 TEST-3）**：CI 增 Windows 腿（头号目标机是 8GB Win11），现三条 job 全 ubuntu-latest（**需 CI 环境/人工**）。
- **B-8（沿 dev-plan §12.7 T3）**：跨机性能基准（低配 Win11 vs 本机），含 PERF-1/2/3 修复后的复测（**需目标机**）。
- **B-9（沿 T12）**：serve/work 常驻资产（launchd plist / schtasks XML）实机「开机自启 + 崩溃自恢复」确认（**需真机**）。
- **B-10（沿 T13）**：备份容器 v2 真机恢复演练（AES-256-GCM 分块 + v1 兼容），现仅单元级（**需真机/人工，数据安全相关，建议优先**）。
- **B-11（沿 B-10）**：编辑器/前端真实浏览器人工走查（bus 事件、patch/rollback 审计展示），现仅单测（**需人工/浏览器**）。
- **B-12（验 R7-SVC-5）**：>30min 合法千页转换与 stale 回收的双进程实测（需长时运行环境）。

## 7. 性能优先净收益小结（本轮与往轮的差异）

往轮修复均「性能中性」；本轮 A 类清单里**性能收益项第一次成为主线**：

| 项 | 收益主张 | 等价性 |
|----|---------|--------|
| R7-STOR-1 FTS rowid 映射 | 单块编辑 O(全库)→O(1)；批量入库每文档省一次全库扫描（10 万块库 ≈ 10⁵ 行/文档） | 检索面非产物面，G6 无涉 |
| R7-STOR-2 无条件 DDL | 存量库删除级联/事件查询恢复索引 seek | 同上 |
| R7-PERF-1 认领解耦 | 单页语料 workers=4 预期 4.5% → 接近大语料口径（~2×）；消除每轮池重建+重导入（≈0.17s×轮数） | 子进程转换代码不变，G6 零风险 |
| R7-PERF-2 三重哈希 | 大文档（20MB+）每任务省 2 次全文读 ≈100-300ms | doc_id 值不变 |
| R7-PERF-3 并行哈希入队 | 1GB 目录 3-4s → ~1s（G5「秒级返回」兑现到 GB 级口径） | 附着/幂等语义不变 |
| PERF-a…f + 服务/运维小项 | 各 50-300ms 级（scan_injection 预过滤、page_metrics 单趟、cfg initializer、hyperlink 预检、对账瘦身、doctor find_spec、备份单缓冲） | 各自带等价断言验证 |
| R7-GAP-1 修订痕迹 | 非性能项，但消除「静默丢内容」最大正确性缺口 | 新能力面，golden 扩展 |

所有性能方案均不破坏 G6 逐字节等价，或差异可由既有红线（shard_two_phase 块数相等 + 逐 zip 条目 sha256 + gate.detail 等价断言）验证。

## 8. 文末提醒（需本机以外环境 / 人工测试的登记项）

按用户要求，完成任务后提醒以下**仅登记、未实施**的外部/人工项：

1. **B-5（沿 R6，HIGH 级收尾）**：launchd plist 修复后需 macOS 真机 `launchctl load` 实跑 `cron --daily` 确认根路径生效——第六轮唯一 HIGH 级待真机项，**建议优先安排**。
2. **B-10（沿 T13，数据安全）**：备份容器 v2 真机恢复演练需真机/人工，**建议优先**。
3. **B-6（本轮新增，验 PERF-1）**：Win11 spawn 池策略实测——目标机吞吐零实测，持久池取舍依赖此数据。
4. **B-7（沿 TEST-3）**：CI 无 Windows 腿，头号目标机 8GB Win11。
5. **B-8（沿 T3）**：跨机性能基准 + PERF-1/2/3 修复后复测。
6. **B-9（沿 T12）**：serve/work 常驻资产实机自启/自恢复确认。
7. **B-11（沿 B-10）**：前端真实浏览器人工走查。
8. **B-1…B-4 / B-12**：需构造语料（乱码/双栏链接/注入/并发压测/长时转换）后才能转 A 实施验证。

> 本轮**未实施任何代码/文档改动**（遵用户「审查完成后不实施」）。第 4 章 13 批 A 类待批准后按序实施；C 类 8 项决策（D1–D8）已按「功能全面、性能优先」代为制定，供确认。本轮审查报告存档于 `docs/review-round7-CH-476.md`，实施轮次编号取 **CH-476**（动笔前按纪律先 `git log` 核对已用编号）。
