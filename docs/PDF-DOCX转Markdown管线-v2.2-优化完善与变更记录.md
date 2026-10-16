# 文本型 PDF / DOCX → Markdown 离线转换管线 v2.2

## 需求与方案优化完善报告 + 变更项点记录（v2.1 → v2.2，含 v2.0 → v2.1 回溯）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | **v2.2**（对 v2.1 的优化完善；v2.1 = 产品形态 + PRD + API/前端；v2.0 = 可行性评估 + M4 适配；v1 = Win11 低配原始方案） |
| 编写日期 | 2026-10-13 |
| 文档性质 | **Delta 文档（修订增量）**：不重抄 v2.1 全文，所有修订条目均标注 v2.1 的章节锚点，按锚点替换/插入即得到完整 v2.2 |
| 基线文档 | `pdf-docx-to-md-plan-v2.1-macmini-m4.md`（2026-10-13） |
| 本次目标 | ① 研究两代文档，找出需求矛盾、契约缺口与工程缺陷；② 给出可执行的修订（需求条目全文、代码补丁、ADR）；③ 建立三代版本（v2.0 → v2.1 → v2.2）的变更项点总表与追溯矩阵 |
| 变更规模 | 勘误 6 项 · 一致性修复 8 项 · 需求新增 6 条 / 修订 6 条 · 方案修订 9 项 · ADR 新增 7 条 |

---

## 0. 本文档怎么用

1. **如果你是接手开发的 Agent**：先读 §2（问题清单）中的【高】【中】项——它们是开工前必须先修的契约矛盾；再按 §3/§4 的修订条目更新你对 v2.1 的理解；任务卡调整见 §5。**v2.1 的 §9 任务卡仍是唯一开工入口，本文件不另立任务卡序列**。
2. **如果你是验收的人**：v2.2 的增量验收项集中在 §3（每条需求自带验收标准）与 §9（回归断言清单），叠加在 v2.1 §6.5 DoD 之上。
3. **如果你要做后续变更**：读 §7 的变更项点表与 §8 追溯矩阵，新变更必须沿用 CH-xxx 编号规则并登记，这是本项目「保留可追溯性」的机制本体。
4. **如何合成完整 v2.2**：拿 v2.1 原文，按 §3/§4/§5 每条修订的「位置」锚点执行 替换/插入/删除，再把 §7 的 v2.2 变更段追加进新的「附录 J. 变更记录」。

> **版本治理约定（本次新增，写进未来所有版本）**：需求（FR/NFR）、红线指标、API/CLI 契约、IR schema 的任何变更 = **minor 升版（v2.x）+ 变更表登记**；纯错别字/笔误 = errata 行，不升版；架构级变更（如重新引入图片提取、换平台）= **major 升版（v3）**，必须重开 ADR 评审。

---

## 1. 研究结论（TL;DR）

**v2.1 是一份成熟度很高的方案：产品定位清晰（§5 场景驱动）、防漂移机制完备（§6.7 触发器表 + 三入口同内核纪律）、工程约束具体到可执行（macOS 差异清单、内存预算、launchd 配置）。总体评级 A。本次研究未发现方向性错误，但发现 8 处内部矛盾 / 契约缺口（其中 4 处若不修复，会在 M6–M8 联调时直接造成返工），以及一批可低成本补齐的工程与运维缺口。**

最重要的四个发现（详见 §2）：

1. **FR-121 与内容寻址自相矛盾（P-01）**：`doc_id = sha256(文件内容)`，内容一变 doc_id 必变，「内容变 → 同 doc_id 的新 revision」在数据模型上不可能成立。v2.2 引入 `source_key`（逻辑文档标识）分离「文档身份」与「内容指纹」。
2. **FR-704「三入口字节一致」与 FR-104「front-matter 含转换时间」互斥（P-02）**：时间戳进 MD，同一输入两次转换的 sha256 必不相同。v2.2 将易变元数据外置到伴生 `meta.json`，MD 本体确定性化。
3. **NFR-4「≤300ms 返回 job_id」与 FR-102「500 文件 / 2GB」冲突（P-03）**：2GB 的 sha256 在 M4 上也需 1–2 秒，哈希同步做不可能达标。v2.2 引入「intake 两段式提交」。
4. **FR-503「本篇质量分（标题 F1 等）」在生产环境不可计算（P-04）**：F1 需要黄金标注真值，任意上传的文档没有真值。v2.2 区分「评测指标（仅黄金集回归）」与「生产信号（无真值代理指标）」。

---

## 2. 问题清单（按严重度排序）

严重度定义：【高】不修会在开发/联调期造成返工或契约破裂；【中】不修会在验收期或运维期暴露；【低】勘误或优化建议。

### 2.1 一致性 / 契约类

| ID | 严重度 | 位置（v2.1） | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-01 | 【高】 | §4.9、§7.2、FR-121 | `doc_id = sha256(文件内容)`（§4.9 幂等键），而 FR-121 说「同一篇文档更新了 → 内容变 → 新 revision」「/docs/{doc_id} 历史 revision 列表」。内容变化必然产生新 doc_id，revision 机制在数据模型上无处安放新版本；U7 场景（新版 PDF 覆盖上传）无法按现契约实现 | U7、FR-121、FR-205、M8 的版本历史接口全部悬空 |
| P-02 | 【高】 | FR-104 vs FR-704/NFR-11 | front-matter 含「转换时间」与「指标摘要」；而 FR-704/NFR-11 要求「同一输入经 CLI 与 API 产出 MD 的 sha256 相同」「三入口字节一致」。时间戳使字节一致永远不成立；指标摘要（含 LLM patch 结果）也不具备跨次稳定性 | CI 的对比测试（M8 验收 5）必然随机失败 |
| P-03 | 【高】 | NFR-4 vs FR-102、附录 B-8 约束 2 | NFR-4 要求「上传后 ≤300ms 得到 job_id」；FR-102 允许单次 500 文件 / 2GB；B-8 约束 2 要求 submit 只做「hash、写 tmp、入队」。对 2GB 源做 sha256 在 M4 上需 1–2s，物理上不可能 <300ms | 批量迁移（U2）的核心路径违反 SLA，或被迫偷懒跳过哈希（破坏幂等） |
| P-04 | 【高】 | FR-503、§7.4 job_done metrics、§8.3 P3 | 「本篇质量分（标题 F1、表格保真）」与 `job_done` 事件中的 `heading_f1` 等指标，在无黄金标注的生产文档上**不可计算**（F1 的定义是与黄金对齐，§4.8）。生产环境无真值 | 前端质量分列、按质量分排序（P3 页核心交互）拿不到数据 |
| P-05 | 【中】 | §4.6 vs §4.10 / M0 / 附录 E | `doc2md license-check`（§4.10 要求、M0 验收要求）与 `doc2md check --cross-cli-api`（附录 E 引用）均未列入 §4.6 CLI 契约 | Agent 按 §4.6 实现会漏掉两个命令 |
| P-06 | 【中】 | §4.7 vs 附录 B-6 vs §7.8 | §4.7 正文说校验失败「记 `W3xxx`」，B-6 代码用 `E3001`–`E3006`；§7.8 错误码表没有 3xxx 系列 | 错误码契约不稳定，违反「报表只依赖 code」的自身纪律 |
| P-07 | 【中】 | §4.5 vs §4.7 / B-6 | `llm_window` 配置 = 800，而 §4.7 请求示例与 B-6 代码用 `[:600]`；滑窗按「字数」但与 token 预算（`-c 8192`、`--parallel 2` 平分 KV）的关系未说明 | 实现时窗口取值随机漂移，评测不可比 |
| P-08 | 【中】 | FR-203 vs §4.3 约定 4 | 「raw_lines 任何阶段不得丢弃或就地改写」与人工编辑（PATCH 只改 text）冲突未定义：编辑后 `text ≠ raw_lines 合并视图`，字段约定 4 被打破，且无规则阻止 L3 再次改写人工结果 | 审阅闭环（U3）与 IR 不变量冲突，S6 校验语义不明 |
| P-09 | 【中】 | NFR-1 内部、NFR-1 vs §3.6 | NFR-1 同节给出「≥3000 页/小时」与「100 页 ≤30s（≈12000 页/h）」，量级差 4×，未说明哪个是 SLA 下限、哪个是设计目标；§3.6 又给出日产能 ~10 万页 | 验收时无法判定 NFR-1 是否达标 |

### 2.2 工程实现类

| ID | 严重度 | 位置（v2.1） | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-10 | 【中】 | 附录 B-6 | `merge_lines` 在 op 白名单里但 `apply_ops` 没有对应分支——**静默 no-op**；`split_at` 只写 `children` 字段，并未真正把块拆成两个，pos 语义（字符下标还是行号）未定义 | L3 的 4 种 op 实际只有 3 种可用，且 merge_lines 会「假成功」 |
| P-11 | 【中】 | 附录 B-8 | `app.mount("/", StaticFiles(...))` 写在路由定义**之前**：Starlette 按注册顺序匹配，mount("/") 会吞掉其后所有路径，`/api/v1/*` 全部 404 | M8 起手骨架照抄即挂 |
| P-12 | 【中】 | §4.9 / 全局 | `per_job_mem_mb = 2048` 配额没有任何测量与执行手段（如何知道单作业内存峰值？超了怎么熔断？） | 配额形同虚设，P4 大文档分片缺触发条件 |
| P-13 | 【低】 | NFR-2 vs NFR-6 | 「队列深度 ≥5000 作业不降级」与磁盘门禁（20GB 余量停止接单）在极端大批量下未闭环：5000 × 500MB 源文件远超 256GB SSD | 大批量投递时行为未定义 |
| P-14 | 【低】 | §7.4 | SSE 事件重放存 events 表，但保留期/容量上限未定义（logs 无限增长） | 磁盘慢性泄漏 |
| P-15 | 【低】 | NFR-7 / 全局 | 7×24 无人值守（月可用性 ≥99%）但**没有任何告警出口**：坏了没人知道，直到用户发现 | NFR-7 的可用性无从观测 |
| P-16 | 【低】 | 全局 | `state.sqlite`（作业状态 + 编辑审计流水）是单点，无备份/恢复策略；golden 与 out 也无备份要求 | 一次磁盘故障丢失全部审计与队列状态 |
| P-17 | 【低】 | §4.8 / M4 | 黄金集 150–300 篇人工标注成本高，但无标注工具支撑；`expected/` 只能从零手写 | M4 开工前置成本被低估 |
| P-18 | 【低】 | §9 | 单人 7–8 周对 M9（10 个页面 + 虚拟化 + 键盘流 + 审计 + 回滚）偏乐观；且 M9 内部核心页（审阅页）与外围页（settings/compare）风险差异大，未拆分 | 排期风险集中 |

### 2.3 勘误类（不升版也应修）

| ID | 位置 | 勘误内容 |
| --- | --- | --- |
| P-19 | 附录 B-1 | `CAPTION_RE = re.compile(...)` 出现在模块级，但 B-1 顶部只有 `import fitz, hashlib, statistics`——缺 `import re`；照抄即 NameError |
| P-20 | 附录 B-1 `cluster_columns` | 样本过滤条件是「宽度 > 40pt 参与聚类」，但未排除**整宽块**（跨栏表格、通栏标题）：整宽块的 x0 会把聚类中心拉偏，双栏误判。应先剔除宽度 > 页宽 70% 的块再聚类 |
| P-21 | §7.4 事件示例 | `"artifacts":{"s3_ir":"ir_version":"2.0","bytes":812334}` 是非法 JSON（嵌套引号未转义），应为 `"artifacts":{"s3_ir_version":"2.0","s3_bytes":812334}` |
| P-22 | 附录 F 5.1 注释 | 「如需同事访问，走 §8.6 的反向代理」——§8.6 是「API 与前端责任边界」表，无反向代理内容；正确引用应为 §4.10 第 4 条（绑内网段 + token + 防火墙） |
| P-23 | NFR-12 | 「除本机 llama-server 外无任何网络调用」与 FR-703 webhook（P2）预留冲突；应表述为「除 `127.0.0.1`/`localhost` 与显式白名单回调外无任何出站网络调用」 |
| P-24 | §3.4 换行符行 | 「`chardet`/`charset-normalizer` 探测编码（PDF/DOCX 内部 UTF-16/GBK 混合场景常见）」表述有误导：PDF/DOCX 的文本层解码由 PyMuPDF/pandoc 负责，编码探测只用于**纯文本旁路输入**（若有）与日志/配置文件；若 v2.2 范围内无纯文本输入，此依赖可删 |

---

## 3. 需求修订与新增（PRD 增量，直接并入 v2.1 §6）

> 格式与 v2.1 §6.3/§6.4 一致。每条标注【新增】/【修订】及对应问题 ID 与 v2.1 锚点。

### 3.1 FR 修订条目

**FR-104【修订】**（锚点：v2.1 §6.3 FR-104；动因：P-02）

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-104 | 输出 MD 文件，front-matter **确定性化**：仅含 `doc_id / source_key / source_name / pages / profile / ir_version / revision / table_output`（全部为输入与配置的确定性函数）。转换时间、指标摘要、环境指纹等易变/派生信息一律移入伴生 `xxx.meta.json` | 同一输入 + 同一配置，两次转换（间隔 >1s、经 CLI 与 API 两入口）MD 的 `sha256` 完全一致（进 CI，即 FR-704 的落地测试）；`meta.json` 与 MD 同生命周期保留 |

**FR-121【修订】**（锚点：v2.1 §6.3 FR-121；动因：P-01）

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-121 | **文档身份与内容指纹分离**：`doc_id = sha256(内容)`（内容指纹，幂等键，语义不变）；新增 `source_key`（逻辑文档标识：首投时分配 `{source_name 的规范化形式}@{首版 doc_id 前 12 位}`，用户可在提交时显式指定以实现「覆盖上传即同源」）。同一 `source_key` 下，新内容 → 新 `doc_id` + 新 `revision` 入该源的历史链；同内容改名/重传 → 复用既有产物，仅追加投递记录 | ① 上传「报告 v1.pdf」→ 修改内容后上传「报告 v2.pdf」（指定同 source_key）→ `/sources/{source_key}` 返回 2 个 doc 版本、revision 链完整、块级 diff 可用；② 不指定 source_key 时同名同目录自动聚合规则生效；③ 同内容不同文件名重传：产物复用，无重复计算（复用 §4.9 幂等） |

**其余 FR-1xx/2xx/3xx/4xx/5xx/6xx/7xx 条目不变。**

### 3.2 FR 新增条目

| 编号 | 需求 | 验收标准 | 优先级 | 动因 |
| --- | --- | --- | --- | --- |
| FR-123 | **大批量两段式提交（intake）**：单次提交超过阈值（默认 >20 文件或总量 >200MB）时，`POST /jobs` 快速落盘并返回 intake 票据（202 + `intake_id` + 事件流 href），哈希/去重/入队异步执行；异步完成经 `intake_done` 事件（或轮询）给出全部 job_id。小批量仍同步返回 job 列表 | ① 500 文件 / 2GB 提交：接收响应 ≤ max(2s, 50ms × 文件数)；② intake 阶段崩溃重启后可续；③ 异步去重结果与同步路径一致（同 doc_id 不重复计算）；④ Idempotency-Key 在接收期即生效（重复整包提交返回同一 intake） | P1 | P-03 |
| FR-505 | **确定性产物**：`md / md-plain / html / json-ir / chunks` 全部导出格式对「同输入 + 同配置 + 同代码版本」字节稳定；任何非确定性来源（时钟、随机、模型温度>0、迭代顺序）禁止进入导出内容 | CI 中对黄金集 10 篇做「双跑 diff」，任一导出格式 sha256 不一致 = 构建失败 | **P0** | P-02 |
| FR-606 | **异常告警（最小运维闭环）**：以下情形触发本机通知（macOS `osascript` notification）并写入 `system` 事件流：① 连续 ≥3 个作业以同一 `err_code` 失败；② 磁盘余量 < 25GB（一级）/ < 20GB 门禁触发（二级）；③ 资源降级（503 / degrade_to_L0）持续 >5min；④ LLM 服务不可达 >2min | 触发任一条件后 ≤60s 内通知送达（本机可见）且 `GET /system/stats` 的 `alerts` 数组可查历史；告警去重（同类 15min 内不重复） | P1 | P-15 |
| FR-607 | **备份与恢复**：`state.sqlite` 开 WAL + 每日快照至 `reports/backups/`（保留 14 份）；`golden/` 与 `out/` 每周增量打包至外置卷或独立目录（可配）；提供 `doc2md restore --backup <file>` | ① kill -9 后重启无状态丢失（原有要求）；② 删除 state.sqlite 后 `restore` 能恢复作业表 + 审计流水 + 事件重放指针；③ 恢复演练进 M8 验收 | P1 | P-16 |
| FR-801 | **黄金集标注辅助（golden-init）**：`doc2md golden-init --src <file>` 用当前最优输出预填 `expected/*.md`，人工仅修改差异（复用审阅页 diff 能力）；manifest 模板自动生成 | 标注一篇 50 页文档的人工修改量 ≤ 直接手写的 50%；产出物通过 §4.8 manifest 校验 | P2 | P-17 |

### 3.3 NFR 修订条目

**NFR-1【修订】**（动因：P-09）：

| 编号 | 类别 | 要求 |
| --- | --- | --- |
| NFR-1 | 吞吐 | 拆为两层：**SLA 下限**——混合负载（含 20% 双栏 + 30% 带表格）稳定 ≥ 3000 页/小时，不达标即事故；**设计目标**——黄金集均值 ≥ 8000 页/小时（`enhanced` 满载，对应 §3.6 估算的保守折减）。另：**「100 页 ≤30s」是单作业时延指标**（P50），不得换算为吞吐口径混用 |

**NFR-2【修订】**（动因：P-13）：「队列深度 ≥5000 作业不降级」后追加限定：排队作业的**未处理源文件总量**计入磁盘门禁；超出门禁时 intake 返回 `413 / E4132`（disk_quota），**不算服务降级**（属于容量拒绝，行为可预期）。

**NFR-4【修订】**（动因：P-03）：「上传后 ≤300ms 得到 job_id」改为「**小批量**（≤20 文件且 ≤200MB）≤300ms 返回 job_id；**大批量**按 FR-123 返回 intake 票据，接收响应 ≤ max(2s, 50ms × 文件数)；进度事件推送延迟 ≤1s；ETA 误差 ≤±40%」。

### 3.4 NFR 新增条目

| 编号 | 类别 | 要求 |
| --- | --- | --- |
| NFR-13 | 数据安全 | SQLite 全程 WAL；每日快照 + 每周增量备份（FR-607）；`work/` 之外目录一律只读；备份介质与 `work/` 不同物理位置（外置卷或至少不同 APFS 卷） |
| NFR-14 | 可观测 | 每日 00:00（本机时区）落盘 `reports/daily/<date>.json`：作业数、页数、拒收分布、err_code Top10、吞吐 p50/p95、内存/磁盘峰值、降级次数——不引入外部监控栈，纯文件，供健康页与周报消费 |
| NFR-15 | 确定性 | 与 FR-505 对齐：所有导出格式满足字节稳定；`--fmt json-ir` 中的 `ts_utc` 允许非确定（审计字段），但 MD 类导出禁止任何时钟字段 |

---

## 4. 方案修订（工程规格增量，锚点到 v2.1 章节）

### 4.1 IR Schema 增补（锚点：v2.1 §4.3）

新增字段与约定（编号顺延 v2.1 的 1–8）：

```json
"meta": {
  "doc_id": "sha256:…",            // 内容指纹（不变，幂等键）
  "source_key": "report_v3@1a2b3c4d5e6f",  // 新增：逻辑文档标识（FR-121）
  …
}
"blocks[i]": {
  "flags": { "text_edited": false }   // 新增：人工编辑过 text 的标记（约定 9）
}
```

**约定 9（人工编辑与 raw_lines 的关系，解 P-08）**：人工编辑（FR-203 / PATCH）只改写 `text`，`raw_lines` 永远保留抽取原值且加 `flags.text_edited = true`；`provenance.modified_by` 追加 `human:edit@rev4`。S6 渲染对 `text_edited` 块以 `text` 为准。**`text_edited` 块此后跳过 L3 LLM patch**（人工结论优先于模型，防止二次改写）；若该文档整体重跑（FR-301 单步重跑 S4/S5），`text_edited` 标记随上游产物重建而丢失，重跑确认对话框必须提示「N 处人工修订将被覆盖」。

**约定 10（job_done 指标口径，解 P-04）**：IR 与事件流中的质量字段分两类——`metrics`（评测指标，仅黄金集作业 / `doc2md check` 产出，含 `heading_f1` 等）与 `signals`（生产信号，任意作业都产出：`conf_p10`（块置信度 10 分位）、`warning_count`、`needs_review`、`llm_patch_accepted`、`llm_patch_rejected`、`dropped_images`、`char_recovery_rate`（输出正文字符数 / 抽取字符数））。前端 P3 结果库的「质量分」列与筛选条件全部改用 `signals`。

### 4.2 配置增补（锚点：v2.1 §4.5，并入 `base.toml`）

```toml
[run]
deterministic_output = true          # FR-505/NFR-15：MD 类导出禁止时钟/随机字段

[source]                             # FR-121：逻辑文档聚合
auto_group = true                    # 未显式指定 source_key 时按「规范化文件名+目录」自动聚合
explicit_key_policy = "allow"        # allow | require | forbid（内网自用默认 allow）

[api]
intake_async_files = 20              # FR-123：超过则走两段式 intake
intake_async_mb     = 200
disk_quota_reserve_gb = 10           # NFR-2：排队源文件总量的额外预留

[ops]                                # FR-606/607、NFR-13/14
sqlite_wal        = true
backup_daily      = true
backup_keep       = 14
backup_weekly_to  = ""               # 外置卷路径；空 = 仅本地快照
events_retention_days = 7            # P-14：events 表保留期
events_retention_rows = 100000
alert_consecutive_failures = 3
alert_dedupe_minutes = 15

[s5]
llm_window   = 800                   # P-07：统一 800（字符），并写明与 token 预算的换算注释
llm_overlap  = 100
# 注：800 汉字 ≈ 800–1000 token；llama-server --parallel 2 下单槽 KV ≈ 4096 token，
# 含 system prompt + heading_path 后余量充足；调整窗口时同步核算此不等式。
```

### 4.3 CLI 契约增补（锚点：v2.1 §4.6）

在命令列表中**补登**（这些命令 v2.1 正文已要求但契约表漏列，P-05）：

```
doc2md license-check                       # 扫描依赖树输出 vendor/licenses.txt（§4.10 / M0 验收）
doc2md check --golden DIR [--cross-cli-api] # 后者：同一输入经 CLI 与 API 双跑并比对 sha256（附录 E 引用、FR-704 落地）
doc2md restore --backup FILE               # FR-607 状态恢复
doc2md golden-init --src FILE [--out DIR]  # FR-801（P2）
```

### 4.4 API 增补与修订（锚点：v2.1 §7）

1. **`/sources` 资源（新增，解 P-01）**：

```
GET    /sources                       # 逻辑文档列表（?q=&updated_since=）
GET    /sources/{source_key}          # 该源下全部 doc 版本（doc_id 列表 + revision 链 + 投递历史）
POST   /sources/{source_key}/versions # 把既有 doc_id 归入该源（修正误聚合）
GET    /sources/{source_key}/diff?a=<doc_id1>&b=<doc_id2>   # 跨版本块级 diff（U7 主路径）
```

`GET /docs/{doc_id}` 保留（单内容指纹视角，revision 只含人工编辑产生的历史）；版本演进查询一律走 `/sources`。

2. **错误码表增补（锚点 §7.8）**：

| HTTP | code | 含义 | 前端处置 |
| --- | --- | --- | --- |
| 413 | `E4132` | 排队源文件总量超磁盘配额（NFR-2） | 提示分批投递或清理队列，显示当前配额 |
| — | `W3001`–`W3009` | LLM 层可跳过类告警（超时/低置信/格式拒绝），作业继续 | 黄色标记，可重跑 S5 |
| — | `E3001`–`E3009` | LLM patch 单 op 拒绝（immutable 越权 / op 越界 / 白名单外），该 op 丢弃 | 计入 `llm_patch_rejected` 信号 |
| 422 | `E4223` | 提交参数与 profile 能力不符（如 minimal 档请求 llm=patch 常驻 8B） | 表单内联提示并建议降级选项 |

（P-06：`W30xx` 与 `E30xx` 两系列自此为正式契约；B-6 代码注释同步改用此口径。）

3. **`job_done` 事件 data 修订（锚点 §7.4，解 P-04/P-21）**：

```json
{ "job_id":"j_9f3c2a","status":"succeeded","revision":"rev3","total_ms":9820,
  "signals":{"conf_p10":0.72,"warning_count":3,"needs_review":2,
             "llm_patch_accepted":11,"llm_patch_rejected":1,
             "dropped_images":3,"char_recovery_rate":0.994},
  "metrics": null,
  "resources":{"md":"/api/v1/docs/sha256:1a2b/md","report":"…","bundle":"…"}}
```

`metrics` 仅在黄金集/评测作业中非空。事件示例中的 `artifacts` 字段修正为合法 JSON（P-21）。

### 4.5 代码骨架补丁（锚点：v2.1 附录 B）

**B-1 补丁（P-19 / P-20）**：

```python
import fitz, hashlib, re, statistics        # ← 补 import re

def cluster_columns(pages_blocks, page_width):
    # 先剔除整宽块（跨栏表格/通栏标题），它们不参与栏判定（P-20）
    samples = [b for b in pages_blocks
               if 40 < (b["bbox"][2]-b["bbox"][0]) < page_width * 0.70]
    if len(samples) < 8:
        return [0] * len(pages_blocks), 1
    xs = sorted(b["bbox"][0] for b in samples)
    centers = []
    assign = {}
    for x in xs:
        for i, c in enumerate(centers):
            if abs(x - c) <= COLUMN_X_TOL:
                assign[x] = i; break
        else:
            centers.append(x); assign[x] = len(centers) - 1
    labels = [assign.get(b["bbox"][0], 0) for b in pages_blocks]   # 整宽块落 0 号栏，保持原序
    return labels, max(len(centers), 1)
```

**B-6 补丁（P-10）**：`merge_lines` 定义为「合并 `raw_lines` 中第 pos 与 pos+1 行」；`split_at` 定义为「在字符 pos 处把本块拆成两个独立块（复用 absorbed_ids/provenance 机制）」，替代原来只写 `children` 的假实现：

```python
        elif k == "merge_lines":
            # pos = raw_lines 行号；合并第 pos 与 pos+1 行（撤销一次错误切分）
            rl = block.get("raw_lines") or []
            if not (0 <= pos < len(rl) - 1):
                errs.append("E3007:pos"); continue
            rl[pos] = rl[pos] + rl[pos+1]; del rl[pos+1]
            block["raw_lines"] = rl
            block["text"] = "".join(rl) if _has_cjk("".join(rl)) else " ".join(rl)
        elif k == "split_at":
            # pos = text 字符下标；真正分裂为两个块（由调用方插入 blocks 列表）
            if not (0 < pos < len(text)):
                errs.append("E3004:pos"); continue
            new_block = {
                "id": f"{block['id']}s{pos}", "type": block["type"],
                "text": text[pos:].strip(),
                "raw_lines": [], "provenance": {"extracted_by": "llm:split_at",
                                                "modified_by": [], "llm_applied": True},
                "confidence": 0.6,
            }
            text = text[:pos].strip()
            block["children"] = new_block            # 渲染层负责展开为兄弟块
```

（若实现方判断 `merge_lines` 收益低，可整体从白名单移除——**但必须同步改附录 D 的 System Prompt 与 §4.7 白名单描述，三处必须一致**，不允许代码里删了契约里还留着。）

**B-8 补丁（P-11 / P-03）**：

```python
# ① mount 必须放在所有 API 路由注册之后（或用 app.router.routes.append），
#    否则 StaticFiles("/") 会吞掉 /api/v1/*：
#    ...先注册全部 API 路由...
app.mount("/", StaticFiles(directory="web/dist", html=True), name="web")

# ② submit() 的 300ms 纪律按批量分档（FR-123）：
#    小批量（≤intake_async_files 且 ≤intake_async_mb）：同步 hash → 返回 jobs（现状）
#    大批量：仅落盘 + 分配 intake_id → 返回 202 {"intake_id":…, "events_href":…}
#            worker 后台做 sha256 / 去重 / 建作业，发 intake_done 事件（含全部 job_id）
#    IntakeReject 语义不变（提交期 magic 校验失败仍即时拒收）。
```

### 4.6 内存配额落地（锚点：v2.1 §4.9 / §3.5 规则 3，解 P-12）

- worker 子进程在每阶段结束时把 `resource.getrusage(RUSAGE_SELF).ru_maxrss` 峰值回写 `state.sqlite` 的 `tasks.peak_rss_mb`（M6 产出）。
- 调度器规则：单作业 `peak_rss_mb > per_job_mem_mb` 且 `pages > 100` → 下一次执行自动按章节分片重跑（记 `W1091:mem_shard`）；连续 2 次分片后仍超 → 作业失败进人工队列（`E1092`），**不静默、不 OOM**。
- 健康页（P9）展示最近 24h 作业 RSS p95，用于校准 `per_job_mem_mb` 初值 2048。

---

## 5. 任务卡与发布切分调整（锚点：v2.1 §9）

| 卡 | 调整内容 | 动因 |
| --- | --- | --- |
| M0 | 验收追加：`doc2md license-check` 输出许可清单（v2.1 M0 正文已要求，此处确认其在 §4.6 契约中已补登）；`setup_mac.sh` 增加「外置备份卷探测（可配，缺失只警告不失败）」 | P-05、FR-607 |
| M1 | 产出追加：`meta.source_key`、`blocks[].flags.text_edited` 字段与校验；`ir_io` 按页分片读取之外增加「确定性序列化」选项（键排序、无时间戳），支撑 FR-505 | P-01、P-02、P-08 |
| M2 | 不变（拒收原因码集合照旧冻结为对外契约） | — |
| M4 | 交付物追加：`golden-init` 的 diff 预填能力排入本卡末尾（P2，可延后到 R4，不阻塞 M4 验收） | FR-801 |
| M6 | 产出追加：worker RSS 峰值回写 + 分片熔断（§4.6）；`merge_lines/split_at` 语义按 §4.5 补丁实现并有单测 | P-10、P-12 |
| M8 | 验收追加：① **双跑确定性测试**（黄金集 10 篇 × CLI/API 两入口 × md/chunks 两格式，sha256 全等，进 CI）；② **intake 两段式**：500 文件/2GB 提交按 FR-123 验收；③ **备份恢复演练**：删 `state.sqlite` → `doc2md restore` → 队列/审计/事件指针完整；④ **告警演练**：人为触发连续 3 次同码失败，60s 内通知送达 | P-02、P-03、FR-605/606/607 |
| M9 | **拆分为 M9a / M9b**：M9a（5–6 天，R3 核心）= 审阅页三栏 + 双向定位 + 行内编辑/只读围栏 + revision/审计/回滚 + 键盘流；M9b（3–4 天，可滑入 R4）= compare 页、review 总队列、settings 页、报告页增强。FR-207/208 维持 P1 不进 R3 验收 | P-18 |
| 发布切分 | R3 判据改为「U3/U4 走通（M9a 范围）」；R4 追加 M9b 与 FR-801 | P-18 |

**DoD 增补（叠加 v2.1 §6.5 六条之后）**：

7. FR-505 双跑确定性测试在 CI 常绿。
8. FR-121 场景验收：同一逻辑文档三连投（v1 / v1 改名 / v2 覆盖），`/sources` 历史链与 diff 全部正确。
9. 备份恢复演练与告警演练各通过一次（M8 验收 ⑥⑦）。

---

## 6. ADR 增补（锚点：v2.1 附录 H，编号顺延）

| ID | 决策 | 理由 | 重议条件 |
| --- | --- | --- | --- |
| ADR-15 | 引入 `source_key` 分离「逻辑文档身份」与「内容指纹 doc_id」 | FR-121 的「同 doc_id 新版本」与内容寻址在数学上矛盾（P-01）；分离后幂等（doc_id）与版本演进（source_key）各归其位 | 出现跨机器聚合需求（多台转换站共享源库）时重评 key 分配 |
| ADR-16 | 导出产物确定性化：MD 类导出禁止时钟/随机字段，易变元数据外置 `meta.json` | FR-704/NFR-11 字节一致是三入口同内核的可验证形态，时间戳入 MD 使其永不可达（P-02） | 永不（只可增补字段白名单） |
| ADR-17 | 大批量提交采用两段式 intake（异步哈希 + 事件通知） | 2GB 哈希物理上无法 <300ms（P-03）；两段式保住「快速反馈」体验且不牺牲幂等 | 硬件哈希吞吐发生数量级变化 |
| ADR-18 | 生产环境质量评价只用代理信号（signals），评测指标（metrics）仅存在于黄金集回归 | F1 类指标需要真值，生产文档无真值（P-04）；混用会让前端展示永远为空或造假 | 引入在线人工标注回流且样本量足够时 |
| ADR-19 | 人工编辑边界：只改 `text`、置 `text_edited`、跳过后续 L3、整体重跑前强提示 | 保住 raw_lines 不变量（§4.3 约定 4）与人工结论优先（P-08） | 永不 |
| ADR-20 | 最小运维闭环 = WAL + 每日快照 + 每周增量 + 本机通知告警 + 每日 JSON 快报，不引入外部监控栈 | 内网单机自用，Prometheus/Grafana 属过度设计；NFR-7 的 99% 可用性必须可观测（P-15/P-16） | 出现第二台机器或多租户 |
| ADR-21 | `merge_lines` 语义固化为「合并 raw_lines 相邻两行」；`split_at` 真实分裂为兄弟块 | v2.1 B-6 的白名单 op 有一个静默 no-op、一个假实现（P-10）；语义不定则 L3 评测不可比 | 若黄金集显示两 op 使用率 <1% 且错误率高，可从白名单移除（须同步改附录 D） |

---

## 7. 变更项点总表（CH-xxx，三代版本追溯）

> 编号规则：`CH-1xx` = v2.0 → v2.1（回溯登记，本次补录）；`CH-2xx` = v2.1 → v2.2（本次）。状态：已生效。

### 7.1 CH-1xx：v2.0 → v2.1 回溯（依据两版文档比对，补录）

| ID | 变更 | v2.0 | v2.1 | 依据章节（v2.1） | 关联 ADR |
| --- | --- | --- | --- | --- | --- |
| CH-101 | 交付形态确认 | 待定（两形态都评估） | **内部自用、不对外分发** | 文档头表、§6.1 | — |
| CH-102 | PyMuPDF 许可路线 | 双后端（AGPL 默认 + MIT 替代实现） | **唯一生产后端，MIT 路线降为接口占位** | §2.3、§2.4-3、§4.10 | ADR-03（改写） |
| CH-103 | 图片处理 | S2-D 媒体抽取（落盘 + caption 关联 + `out/media/`） | **仅检测登记占位块，不提取不 OCR**；删除 `media/` 目录 | §4.1、§4.4、§6.2、附录 B-1 | ADR-11（新增） |
| CH-104 | 扫描件处置 | `on_fail = reject \| degrade_to_ocr_queue` | **一律 reject**（本项目无 OCR 队列） | §4.5 `[s1_5]` | ADR-08 |
| CH-105 | 生产默认档 | standard（4B 常驻）为推荐 | **enhanced（8B 常驻、workers=5）为生产基线**；4B 降为回退档 | §3.5、§3.7、§4.5 | ADR-06（改写）、ADR-10（新增） |
| CH-106 | 产品形态 | 纯 CLI 流水线 | **三入口**：CLI + HTTP API（作业制 + SSE）+ Web 控制台（审阅/修订为一等公民） | §5、§7、§8 | ADR-12、ADR-13（新增） |
| CH-107 | 需求基线 | 无（只有工程规格） | **新增 PRD（§6）**：FR-1xx…7xx、NFR-1…12、DoD、发布切分 R1–R4、变更控制 §6.7（触发器 T-1…T-7） | §6 | — |
| CH-108 | 队列模型 | 有界队列背压、M4 三段流水 | **+ 三桶（small/medium/large）加权公平轮转 5:3:1，large 并发上限 1**；入队无硬上限、门禁 503 | §3.6、§4.9、附录 B-8 | — |
| CH-109 | 资源阈值 | 磁盘门禁 5GB；内存规则 3 条（16GB 视角） | **磁盘门禁 20GB；内存硬上限 20GB（32GB 视角）；`max_mb` 200→500** | §3.5、§4.5 | ADR-10 |
| CH-110 | 目录与保留 | `work/media/` 存在；reject 无独立保留策略 | **删 `media/`；新增 `work/reject/`（90 天）** | §4.4 | CH-103 连带 |
| CH-111 | 任务卡 | M0–M7（可选 L2） | **M0–M9**：新增 M8（API 服务）、M9（Web 控制台）；总量 4–5 周 → 7–8 周 | §9 | — |
| CH-112 | 代码骨架 | B-1…B-7 | **新增 B-8（API/调度骨架）；B-1 增 `scan_images`；B-5 去掉 `--extract-media`** | 附录 B | — |
| CH-113 | CLI | 8 个子命令 | **+ `serve / serve status|reload|token / queue / export`** | §4.6 | — |
| CH-114 | 故障排查/部署 | 11 行排查表；launchd 仅 LLM | **+ 服务化条目（队列丢失/进度卡死/CLI-API 不一致等）；launchd 增 serve plist** | 附录 E/F | — |

### 7.2 CH-2xx：v2.1 → v2.2（本次全部变更）

| ID | 类型 | 变更 | 位置（v2.1 锚点） | 动因（问题 ID） | 关联需求/ADR/任务卡 |
| --- | --- | --- | --- | --- | --- |
| CH-201 | 修订 | 引入 `source_key`，FR-121 改写，新增 `/sources` API | §4.3、§4.9、§6.3 FR-121、§7.2 | P-01 | FR-121（修订）、ADR-15、M1/M8 |
| CH-202 | 修订 | front-matter 确定性化，易变元数据外置 `meta.json` | §6.3 FR-104 | P-02 | FR-104（修订）、FR-505（新增）、ADR-16、M1/M8 |
| CH-203 | 新增 | FR-505 确定性产物 + CI 双跑测试 | §6.3、§6.5 | P-02 | FR-505、NFR-15、ADR-16、M8 |
| CH-204 | 新增 | FR-123 两段式 intake（异步哈希） | §6.3、§7.3、附录 B-8 | P-03 | FR-123、NFR-4（修订）、ADR-17、M8 |
| CH-205 | 修订 | metrics/signals 二分，job_done 与前端质量分改用代理信号 | §6.3 FR-503、§7.4、§8.3 P3 | P-04 | FR-503（修订）、ADR-18、M8/M9 |
| CH-206 | 补登 | CLI 契约补 `license-check`、`check --cross-cli-api`（+`restore`、`golden-init`） | §4.6 | P-05 | M0、M8 |
| CH-207 | 修订 | 错误码统一 `W30xx/E30xx` 两系列并入 §7.8；新增 `E4132`、`E4223` | §4.7、§7.8、附录 B-6 | P-06、P-13 | M2/M8 |
| CH-208 | 修订 | `llm_window` 统一 800 并附 token 预算换算注记 | §4.5、§4.7、B-6 | P-07 | M6 |
| CH-209 | 新增 | IR 约定 9（人工编辑边界：`text_edited`、跳过 L3、重跑强提示） | §4.3、§7.5 | P-08 | ADR-19、M1/M9a |
| CH-210 | 修订 | NFR-1 拆「SLA 下限 / 设计目标 / 单作业时延」三个口径 | §6.4 NFR-1 | P-09 | M6 验收 |
| CH-211 | 修订 | B-6 补 `merge_lines` 真实现、`split_at` 真分裂语义 | 附录 B-6、附录 D | P-10 | ADR-21、M6 |
| CH-212 | 勘误 | B-8 `app.mount("/")` 移至路由注册之后 | 附录 B-8 | P-11 | M8 |
| CH-213 | 新增 | 内存配额落地：worker RSS 回写 + 分片熔断 `W1091/E1092` + 健康页展示 | §3.5、§4.9、§8.3 P9 | P-12 | M6/M9 |
| CH-214 | 修订 | NFR-2 加磁盘配额限定；intake 容量拒绝 `E4132` | §6.4 NFR-2 | P-13 | M8 |
| CH-215 | 新增 | events 表保留期（7 天 / 10 万条，可配） | §7.4、§4.5 `[ops]` | P-14 | M8 |
| CH-216 | 新增 | FR-606 异常告警（本机通知 + system 事件流 + 去重） | §6.3、§8.3 P9 | P-15 | ADR-20、M8 |
| CH-217 | 新增 | FR-607 备份恢复 + NFR-13 + `doc2md restore` | §6.3、§6.4 | P-16 | ADR-20、M0/M8 |
| CH-218 | 新增 | NFR-14 每日 JSON 快报（reports/daily） | §6.4、§8.3 P9 | P-15 | M8 |
| CH-219 | 新增 | FR-801 `golden-init` 标注辅助（P2） | §4.8、§9 M4 | P-17 | M4（可延 R4） |
| CH-220 | 修订 | M9 拆 M9a/M9b；R3/R4 判据调整；DoD 增补 3 条 | §6.5、§6.6、§9 | P-18 | M9 |
| CH-221 | 勘误 | B-1 补 `import re`；`cluster_columns` 剔除整宽块再聚类 | 附录 B-1 | P-19、P-20 | M3 |
| CH-222 | 勘误 | SSE `artifacts` 示例改合法 JSON | §7.4 | P-21 | M8 |
| CH-223 | 勘误 | 附录 F 反向代理引用改指 §4.10 第 4 条 | 附录 F 5.1 | P-22 | — |
| CH-224 | 勘误 | NFR-12 措辞加白名单回调例外 | §6.4 NFR-12 | P-23 | — |
| CH-225 | 勘误 | §3.4 编码探测适用范围澄清（仅纯文本旁路，非 PDF/DOCX 主通道） | §3.4 | P-24 | — |
| CH-226 | 新增 | 版本治理约定（minor/major/errata 升版规则 + 附录 J 变更记录制度） | 文档头、新附录 J | P-14（文档层面） | 全局 |

---

## 8. 追溯矩阵（变更 ↔ 问题 ↔ 需求 ↔ 章节 ↔ 任务卡 ↔ ADR）

### 8.1 问题 → 变更 → 落点（正向：我发现了什么，改到哪里）

| 问题 | 变更 | 需求条目 | v2.1 章节锚点 | 任务卡 | ADR |
| --- | --- | --- | --- | --- | --- |
| P-01 | CH-201 | FR-121（修订） | §4.3/§4.9/§7.2 | M1、M8 | ADR-15 |
| P-02 | CH-202/203 | FR-104（修订）、FR-505、NFR-15 | §6.3、§6.5 | M1、M8 | ADR-16 |
| P-03 | CH-204 | FR-123、NFR-4（修订） | §7.3、附录 B-8 | M8 | ADR-17 |
| P-04 | CH-205 | FR-503（修订） | §7.4、§8.3 P3 | M8、M9 | ADR-18 |
| P-05 | CH-206 | — | §4.6 | M0、M8 | — |
| P-06 | CH-207 | — | §4.7、§7.8 | M2、M8 | — |
| P-07 | CH-208 | — | §4.5、§4.7 | M6 | — |
| P-08 | CH-209 | — | §4.3、§7.5 | M1、M9a | ADR-19 |
| P-09 | CH-210 | NFR-1（修订） | §6.4 | M6 | — |
| P-10 | CH-211 | — | 附录 B-6、D | M6 | ADR-21 |
| P-11 | CH-212 | — | 附录 B-8 | M8 | — |
| P-12 | CH-213 | — | §3.5、§4.9 | M6、M9 | — |
| P-13 | CH-214 | NFR-2（修订） | §6.4 | M8 | — |
| P-14 | CH-215/226 | — | §7.4 | M8 | — |
| P-15 | CH-216/218 | FR-606、NFR-14 | §6.3、§6.4 | M8 | ADR-20 |
| P-16 | CH-217 | FR-607、NFR-13 | §6.3、§6.4 | M0、M8 | ADR-20 |
| P-17 | CH-219 | FR-801 | §4.8 | M4 | — |
| P-18 | CH-220 | — | §6.5/§6.6/§9 | M9a/M9b | — |
| P-19~P-24 | CH-221~225 | — | 附录 B-1、§7.4、附录 F、§6.4、§3.4 | M3、M8 | — |

### 8.2 需求 → 场景（反向：每条新需求服务的用户场景，沿用 v2.1 U1–U9 编号）

| 需求 | 服务场景 | 说明 |
| --- | --- | --- |
| FR-121（修订，source_key） | **U7**（同一篇文档更新了） | 原契约无法实现 U7，修订后 U7 验收路径 = `/sources/{key}/diff` |
| FR-505 / NFR-15（确定性） | **U9**（脚本化）+ 内部 CI | 脚本依赖「同输入同输出」才有缓存与比对价值 |
| FR-123（两段式 intake） | **U2**（批量迁移一目录） | 500 文件/2GB 是 U2 的标称负载 |
| FR-503（修订，signals） | **U3/U8** | 复核优先级排序与健康页都需要无真值信号 |
| FR-606/607、NFR-13/14 | **U8**（机器表现不对） | 从「人去看」升级为「机器主动喊人」 |
| FR-801（golden-init） | 内部（评测基建） | 降低 §4.8 黄金集标注门槛 |

### 8.3 v2.0 → v2.1 → v2.2 关键决策演进线（一页看懂三代脉络）

| 决策线 | v2.0 | v2.1 | v2.2 |
| --- | --- | --- | --- |
| PDF 引擎 | PyMuPDF 默认 + MIT 替代双实现 | PyMuPDF 唯一（内部自用结论） | 不变；T-1 触发器与 `pdf_alt` 接口位照旧保留 |
| 图片 | S2-D 媒体抽取（落盘） | 仅登记占位（不提取） | 不变；补 B-1 勘误使登记可运行 |
| LLM 档位 | standard=4B 推荐 | enhanced=8B 生产默认 | 不变；窗口统一 800 字 + token 预算注记 |
| 版本模型 | —（无 revision 概念） | doc_id + revision（存在矛盾） | **doc_id（内容指纹）+ source_key（逻辑文档）+ revision（人工编辑链）三层各司其职** |
| 质量评价 | 黄金集指标 | 指标（含生产端误用） | **黄金集 metrics / 生产 signals 二分** |
| 提交反馈 | — | 300ms 一刀切（批量不可达） | **小批量 300ms / 大批量 intake 两段式** |
| 运维 | 平台差异清单 + launchd | + 健康页 | **+ 告警 / 备份恢复 / 每日快报（最小闭环，不上监控栈）** |
| 文档治理 | 无变更记录 | §6.7 变更控制（针对需求） | **+ 本身版本的 CH-xxx 登记制度（针对文档）** |

---

## 9. v2.2 增量回归断言（并入 `doc2md check` 与 CI）

在 v2.1 §4.8 指标之外，新增以下可计算断言（失败即 CI 红）：

| 断言 | 定义 | 红线 |
| --- | --- | --- |
| `deterministic_export` | 黄金集 10 篇 × {md, md-plain, chunks} × 双跑（间隔 >1s、CLI 与 API 各一次）的 sha256 全等率 | = 100% |
| `source_chain_integrity` | FR-121 验收剧本（v1 投递 → v1 改名重投 → v2 覆盖）后 `/sources` 历史：版本数 = 2、产物复用次数 = 1、diff 可产出 | 全部成立 |
| `intake_async_contract` | 500 文件/2GB 提交：接收响应 ≤ max(2s, 50ms×N)；intake 崩溃重启可续；去重与同步路径一致 | 全部成立 |
| `human_edit_invariants` | 人工编辑后：`raw_lines` 不变、`text_edited=true`、provenance 含 `human:edit`、后续 L3 调用数 = 0 | 全部成立 |
| `alert_e2e` | 人为注入连续 3 次同码失败 → 通知 ≤60s 送达且 `alerts` 可查、15min 去重生效 | 全部成立 |
| `backup_restore` | 删 `state.sqlite` → `doc2md restore` → tasks/audit/events 指针完整、重放不重复 | 全部成立 |
| `llm_ops_semantics` | 对 B-6 补丁的单测：`merge_lines` 合并指定行、`split_at` 产出兄弟块、两者均不触碰 immutable 块 | 全部成立 |
| `md_no_image_syntax` | v2.1 已有（M3 验收），升格为 CI 常驻断言：任何导出不含 `![](...)` | = 0 处 |

---

## 10. 本次研究未改动、但建议持续关注的事项

1. **吞吐数字全部仍是设计输入值**（§3.6 / ADR-07 纪律照旧）：M4 实测前，NFR-1 的「设计目标 8000 页/h」同样只是待校准值。
2. **L2（Docling/Marker）默认关闭的判断维持**：ADR-05 的「F1 提升 <3% 则不转正」判据不变；v2.2 未动。
3. **M9a 审阅页的 bbox 定位依赖 PyMuPDF 坐标与 pdf.js 视口换算**：涉及 PDF 旋转/裁剪页（/CropBox、/Rotate）时坐标需变换，v2.1 未展开——建议在 M9a 任务卡实现时补一个「旋转页定位」验收样例（含 /Rotate 90 的文档 1 篇）。
4. **`llama-server --parallel 2` 与 `llm_clients=2` 的对位**：若未来升 `--parallel 4`，需同步核算 §4.5 注记的 KV 预算不等式，这条已写进配置注释。
5. **黄金集 150–300 篇的规模**仍受标注成本制约（FR-801 只是缓解）；若首月只标出 80 篇，指标方差的置信区间会变宽，§4.8 红线判定需附样本量。

---

## 附录 J. 变更记录（本文件自身）

| 版本 | 日期 | 变更 | 登记 |
| --- | --- | --- | --- |
| v2.2 | 2026-10-13 | 初版：24 项问题（P-01…P-24）、26 项变更（CH-201…CH-226）、需求新增 6 / 修订 6、ADR 新增 7（ADR-15…21）、M8/M9 任务卡调整、增量回归断言 8 条 | 本文件 |

*本文件为 v2.1（`pdf-docx-to-md-plan-v2.1-macmini-m4.md`，2026-10-13）的 Delta 修订文档。按 §0 第 4 条合成完整 v2.2 后，本附录应随 v2.2 主文档一并维护，后续所有变更沿用 CH-xxx 规则登记，保证「每一次变化都可回答：为什么变、变了什么、影响哪些需求与任务卡、依据在哪一版哪一节」。*
