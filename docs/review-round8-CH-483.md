# Doc2md 第八轮全面审查报告与优化方案

> 审查编号：CH-483（下一变更号）
> 审查日期：2026-10-17 ～ 2026-10-18
> 审查基线：`c1ad882`（工作树干净）
> 审查性质：**只读审查**。本文只编制报告与方案，**不包含任何代码/测试/配置修改，也未提交或推送**。
> 适用原则：功能全面优先、性能优先；决策项由审查方按该原则直接裁定（C 类）；需本机以外环境或人工验证的事项只登记、不实施（B 类）。
>
> **实施状态（2026-10-18 补记）**：A 类 25 项已全部落地（Batch 1–3 + Batch 4 收口），本机最终验证 **gate 308 passed / full 883 passed / 0 skipped**，`ruff` 全绿、`doctor` 退出 0（pipeline `2.6.0`）。实现与本文的差异只有一处：删除守护基准由时间戳改为 `deletions.id` 自增序列（避免同毫秒边界误判），索引名为 `ix_deletions_doc_seq`。本版实现提交为 `b7e2513`（记录提交 `b647405`），远端 CI **run `39050319814` 五作业全绿**（三档各 308 passed / 575 deselected；全量 883 passed / 0 skipped；doctor 2.6.0）。B 类（第 13 章）仍只登记、未实施。

---

## 0. 阅读指引

| 章节 | 内容 | 读者 |
| --- | --- | --- |
| 第 1 章 | 执行摘要、关键结论、优先顺序 | 决策者 |
| 第 2 章 | 审查范围、方法、基线与分级口径 | 全体 |
| 第 3 章 | 25 项发现总览与需求覆盖矩阵 | 全体 |
| 第 4～6 章 | HIGH / MED / LOW 逐项证据与修复设计 | 开发 |
| 第 7 章 | 12 项决策（D1–D12）的裁定与备选方案 | 决策者 + 开发 |
| 第 8 章 | 分批实施计划（Batch 0–4）与退出条件 | 开发 |
| 第 9 章 | 测试、黄金样本、G6 与变异验证计划 | 测试 |
| 第 10 章 | 性能方案与度量口径 | 开发 |
| 第 11 章 | 版本、数据库迁移与契约治理 | 全体 |
| 第 12 章 | 文档同步清单 | 文档 |
| 第 13 章 | B 类登记（需外部环境/人工）与提醒机制 | 决策者 |
| 第 14 章 | 风险、回滚与不实施声明 | 全体 |
| 附录 A–C | 探针记录、代码位置索引、契约增量 | 开发 |

---

## 1. 执行摘要

### 1.1 总体结论

本轮在 `c1ad882` 基线上完成了设计、代码、文档、测试四个面的复核，共产出 **25 项发现**：**7 项 HIGH、14 项 MED、4 项 LOW**。整体判断如下：

1. **安全底座与数据纪律仍然可靠**：鉴权、路径消毒、SQL 参数化、FTS 删除映射、备份容器认证、LLM 算子白名单等既有防线没有发现新的可绕过缺陷；本轮的 HIGH 主要不是“被攻破”，而是**数据安全承诺与实现不一致**、**静默丢内容**、**恢复链路缺少安全入口**。
2. **DOCX 结构化抽取仍是功能完整性的最大短板**：内容控件（`w:sdt`）整块丢失、自动编号（`w:numPr`）完全未识别、合并单元格（`gridSpan`/`vMerge`）未归一、单元格内换行/制表符被吃掉。这些不是边角格式，而是 Word 文档的常见结构，直接违反 FR-101/FR-104。
3. **中文标题识别存在“黄金样本自己也测不出来”的盲区**：S3 的 CJK 标题正则不允许 `第` 与数字之间有空格，导致 320 页黄金样本的 **64 个章标题全部降级为普通段落，H1 = 0**；而生成器自检正则允许空格，测试保持全绿。这是本轮最典型的“测试通过但功能不成立”。
4. **删除/恢复链路是数据安全的最大风险面**：同步 `/jobs` 在转换期间被删除会把文档“复活”；文档承诺的“删除前本地快照”并未实现；没有恢复 CLI，现有恢复文档教用户直接覆盖在线库且不清理 `-wal/-shm`；cron 在外置备份失败时仍执行轮出，可能删掉最后一份外置副本。四项构成一条完整的事故链，应最优先处理。
5. **性能问题集中在“删除/对账”而非转换主路径**：单文档删除对三棵产物树做全量 `rglob` 且硬编码目录、搜索对账的孤儿清理对 UNINDEXED 的 FTS 列做全表 `NOT IN`。这两项在“文档多、删除频繁、启动频繁”的部署下会持续恶化，修复成本低、收益明确。

### 1.2 基线数字（本机实测）

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 红线门禁 | **285 passed, 0 failed, 0 error, 0 skipped，38.4s** | `pytest -m gate -q --junitxml=/tmp/doc2md_r8_gate.xml`，exit 0 |
| 全量测试 | **852 passed, 0 failed, 0 error, 0 skipped，85.0s** | `pytest -q --junitxml=/tmp/doc2md_r8_junit.xml`，exit 0 |
| PIPELINE_VERSION | `2.5.0` | `doc2md/__init__.py:13` |
| SCHEMA_VERSION | `14` | `doc2md/__init__.py:14` |
| DB_VERSION | `6` | `doc2md/storage/db.py:22` |
| PLAN_VERSION | `3.16` | `doc2md/__init__.py:15` |
| 黄金样本规模 | 320 页；生成器声明 640 个编号标题；实测 S3 产出 576 个 heading、**0 个 H1** | `tests/make_golden.py:103/111`；本地探针 |
| 黄金资产实测 | 5249 个文件；git 跟踪 5142；MANIFEST 5140 条；23640 KB；2211232 bytes | 本地 `find`/`wc` 探针（2026-10-18） |

> 说明：黄金资产数字与 `docs/dev-plan.md:692/694` 的旧口径不一致，见 R8-DOC-3。该差异只影响文档准确性，不影响产物正确性。

### 1.3 最优先的 5 件事（按风险排序）

1. **R8-SVC-1**：同步转换期间的删除守护（防“删除后复活”，FR-128）。
2. **R8-OPS-3**：外置备份失败时禁止外置轮出，并永久保留最新一份（防“最后备份被删”）。
3. **R8-OPS-1**：真正落地“删除前本地快照”，并在快照失败时 fail-closed。
4. **R8-OPS-2**：提供安全的 `doc2md backup restore`（完整性校验、旧库备份、清理 WAL/SHM、原子替换）。
5. **R8-PIPE-1**：修复中文空格标题识别，并让黄金样本对 H1 数量做硬断言。

随后是 DOCX 结构化补全（R8-PIPE-2/4/5/6）与 FR-108 格式标记（R8-PIPE-7）。这两组会改变产物字节，必须在同一批次内一次性更新黄金样本与 `PIPELINE_VERSION`，避免多轮黄金漂移。

### 1.4 本轮没有做什么

- 没有修改任何源代码、测试、配置、plist、文档（本文除外）。
- 没有提交、推送、创建分支或 PR。
- 没有对备份、删除、恢复执行真实破坏性操作；所有结论来自代码静态核对、只读探针与隔离测试。
- B 类事项（真实语料、真机 launchd、外置卷恢复、Windows、并发压测等）只登记，见第 13 章。

---

## 2. 审查范围、方法与基线

### 2.1 审查范围

| 面 | 覆盖对象 |
| --- | --- |
| 设计/需求 | `docs/Doc2md项目开发方案V1.0.md`、`docs/dev-plan.md`、`README.md`、`ops/backup.md`、`ops/launchd/*`、`ops/windows/*` |
| 管线 | `doc2md/pipeline/`：S1 解析、S1.5 门禁、S2 分片、S3 结构、S4 渲染、批处理、转换与打包 |
| 服务/调度 | `doc2md/api.py`、`worker.py`、`cron.py`、`edits.py`、`llm.py` |
| 存储/检索 | `doc2md/storage/db.py`、`repo.py`、FTS5 与 `search_map`、删除级联、保留期 |
| 安全 | `doc2md/security/`：名称消毒、路径守卫、符号链接、token、权限 |
| 运维 | `doc2md/ops/`：备份、恢复、演练、轮出、日志、磁盘守卫、launchd |
| 配置/CLI | `doc2md/config/loader.py`、`doc2md/cli.py` |
| 测试 | `tests/` 全量、黄金样本、门禁标记、版本同步测试 |

### 2.2 方法

1. **需求反向追踪**：以 FR-101/104/107/108/127/128、FR-607、NFR-18/19/20 为锚，逐条定位实现与测试。
2. **静态代码核对**：对每条候选问题确认调用路径、异常类型、事务边界、并发窗口与配置口径。
3. **只读运行时探针**：
   - 黄金样本转换后统计 heading/H1/段落类型；
   - 构造最小 DOCX 验证 `w:sdt`、`w:gridSpan`、单元格 `w:br`/`w:tab`；
   - 直接调用 `OpValidator` 验证非字符串 `op` 的异常类型；
   - 构造符号链接目录验证 `follow_symlinks` 的实际行为。
4. **测试基线复跑**：门禁与全量各跑一次，用 JUnit XML 取精确计数，避免管道截断误判。
5. **历史去重**：CH-476～CH-482 已关闭项不重复报告；只有出现回归或新变体时才重新登记。第七轮仅 **R7-PIPE-5**（噪声判据）仍未闭环，本轮以 R8-PIPE-3/R8-DOC-1 承接。

### 2.3 分级口径

**严重度**

| 级别 | 定义 |
| --- | --- |
| HIGH | 数据丢失/复活/不可恢复、删除与备份安全承诺不成立、核心结构静默丢失、恢复链路可造成二次损坏 |
| MED | 功能不完整或性能随规模显著退化，但有绕行路径；或文档与实现明确冲突 |
| LOW | 文档陈旧、安装说明缺失、边界输入返回 500 等易修问题 |

**处置分类**

| 分类 | 定义 | 本轮动作 |
| --- | --- | --- |
| A | 本机可实现、可测试、可验证 | 纳入批次实施 |
| B | 需真实语料、真机、外置卷、Windows、并发环境或人工走查 | 只登记，完成后提醒 |
| C | 需产品/契约取舍 | 由审查方按“功能全面 + 性能优先”直接裁定（D1–D12） |

### 2.4 基线可复现命令

```bash
git rev-parse HEAD                 # c1ad882
git status --short                 # 空
.venv/bin/pytest -m gate -q --junitxml=/tmp/doc2md_r8_gate.xml
.venv/bin/pytest -q --junitxml=/tmp/doc2md_r8_junit.xml
```

### 2.5 排除项与回归边界

以下 CH-476～CH-482 已修复项**不作为本轮新发现**重复登记：

- CH-476：修订视图、脚注/尾注、表格段落边界、FTS rowid 映射、同步作业提交窗口、源名消毒等；
- CH-477：版本三处同步；
- CH-478：同字号有序列表误判 H1；
- CH-479～CH-481：启动对账、launchd 捕获档有界化、日志空间治理；
- CH-482：代码块行首缩进几何重建。

本轮新增发现均给出与既有修复不同的触发路径或新的契约冲突；例如 R8-SVC-1 是 CH-476 同步删除守护**未覆盖的转换窗口**，不是同一窗口的重复报告。

---

## 3. 发现总览

### 3.1 25 项发现

| 编号 | 级别 | 分类 | 主题 | 主要位置 |
| --- | --- | --- | --- | --- |
| R8-PIPE-1 | HIGH | A+C | 中文空格章标题不被识别，黄金样本 0 个 H1 | `s3_structure.py:30/509/523`、`make_golden.py:106/1348` |
| R8-PIPE-2 | HIGH | A | 块级/行内 `w:sdt`、`w:customXml` 内容静默丢失 | `s1_parse.py:600-611`、`454-461` |
| R8-SVC-1 | HIGH | A+C | 同步转换期间删除文档会被“复活” | `api.py:293-340`、`repo.py:323-379` |
| R8-OPS-1 | HIGH | A+C | “删除前本地快照”未实现 | `api.py:522-528`、`repo.py:323-379`、`dev-plan.md:705` |
| R8-OPS-2 | HIGH | A+C | 无恢复 CLI；现有恢复步骤可损坏在线库 | `cli.py:77-148`、`backup.py:287-307`、`ops/backup.md:51-58` |
| R8-OPS-3 | HIGH | A | 外置备份失败后仍轮出，可能删掉最后一份 | `cron.py:158-183`、`backup.py:309-343` |
| R8-DOC-1 | HIGH | A | B-1 登记谎称噪声判据已落地 | `dev-plan.md:1762`、`s1_5_gate.py:197-230` |
| R8-PIPE-3 | MED | A+B | S1.5 auto 模式完全忽略噪声页 | `s1_5_gate.py:89-107/197-230` |
| R8-PIPE-4 | MED | A+B | DOCX 自动编号 `w:numPr` 未识别 | `s1_parse.py`（无编号处理）、`s3_structure.py:373-394` |
| R8-PIPE-5 | MED | A | DOCX `gridSpan`/`vMerge` 未归一，列错位 | `s1_parse.py:567-578` |
| R8-PIPE-6 | MED | A | 单元格内 `w:br`/`w:tab` 丢失 | `s1_parse.py:554-564` |
| R8-PIPE-7 | MED | A+C | FR-108 格式标记无生产者，`Block.marks` 恒空 | `s4_render.py:80-85`、全仓无赋值 |
| R8-PIPE-8 | MED | A+C | `source_name_raw` 进入 zip 内 `ir.json`/`report.html`，与“不进 zip”冲突 | `convert.py:234/244/246-247/290` |
| R8-LLM-1 | MED | A | 非字符串 `op` 触发 `TypeError`，整篇 500 | `llm.py:163/182/463` |
| R8-SVC-2 | MED | A+C | 同路径多作业中陈旧作业被标 done 但无正文 | `worker.py:132-165/219-261` |
| R8-PERF-1 | MED | A | 对账孤儿清理对 UNINDEXED FTS 列全表扫描 | `repo.py:164-198` |
| R8-PERF-2 | MED | A | 单文档删除对三棵产物树全量 `rglob`，且忽略配置路径 | `repo.py:428-448` |
| R8-OPS-4 | MED | A | 演练用 `shutil.copy2` 复制在线 SQLite，WAL 不安全 | `drill.py:137` |
| R8-SEC-1 | MED | A+C | `follow_symlinks` 为无效果开关，链接被静默跳过 | `dirguard.py:89-108` |
| R8-CFG-1 | MED | A+C | 配置“层叠”文档与实现不符 | `loader.py:166-188`、`README.md:51` |
| R8-TEST-1 | MED | A | 上述行为的测试覆盖缺口 | `tests/` |
| R8-API-1 | LOW | A | 非对象 JSON 请求体导致 500 | `api.py:154-155` |
| R8-DOC-2 | LOW | A | README 版本 v2.4 与 2.5.0 不一致 | `README.md:3`、`__init__.py:13` |
| R8-DOC-3 | LOW | A | 黄金资产统计数字陈旧 | `dev-plan.md:692/694` |
| R8-OPS-5 | LOW | A | launchd 安装说明缺路径改写与日志目录创建 | `README.md:94-116`、`ops/launchd/*.plist` |

### 3.2 需求覆盖矩阵（本轮相关）

| 需求 | 现状 | 结论 | 对应发现 |
| --- | --- | --- | --- |
| FR-101 文本完整抽取 | PDF 主路径完整；DOCX 的 sdt/customXml/编号/单元格换行丢失 | **不完整** | R8-PIPE-2/4/5/6 |
| FR-104 标题结构 | 普通编号标题可用；`第 N 章`（带空格）全漏 | **不完整** | R8-PIPE-1 |
| FR-107 链接 | 整段链接可用；行内局部链接保守不抽（既定口径） | 符合 | — |
| FR-108 格式标记 | 渲染端已消费 `marks`，生产端从未写入 | **未交付** | R8-PIPE-7 |
| FR-127 名称安全 | content.md/文件名安全；raw 名进入 zip 内 ir.json/report.html | **契约冲突** | R8-PIPE-8 |
| FR-128 删除与轮出 | 级联与留痕可用；转换窗口复活、无删前快照、轮出可删最后副本 | **不成立** | R8-SVC-1/OPS-1/OPS-3 |
| FR-607 备份 | 日快照/周外置/演练可用；无恢复 CLI，恢复步骤不安全 | **不完整** | R8-OPS-2/OPS-4 |
| NFR-18 外置加密 | 容器与认证可用 | 符合 | — |
| NFR-19 日志轮转 | 自持日志与 launchd 捕获档均有界 | 符合 | — |
| NFR-20 日志不含正文 | 事件/演练报告口径保持 | 符合 | — |

### 3.3 本轮未发现的问题（复核结论）

以下面经核对**未发现新的可利用缺陷**，避免后续重复投入：

- **鉴权/权限**：admin/editor/viewer 分档、token 哈希存储、删除双闸（`confirm=true` + `delete` 权限）逻辑完整。
- **注入/XSS**：SQL 全部参数化；审阅页对 `source_name_raw` 做了 HTML 转义；路径进入 zip 前有双闸。
- **路径穿越**：名称消毒与 zip 条目守护有效；本轮的 symlink 问题属于“开关无效”，不是已实现的穿越。
- **LLM 算子执行**：白名单 + 目标存在/immutable/参数校验 + `apply_ops` 二次设防的结构正确；本轮只发现类型校验缺口。
- **备份容器**：AES-GCM 分块认证、AAD 绑定头、`.part` + fsync + 原子替换路径正确；本轮问题在“恢复入口与轮出策略”，不在容器密码学。
- **队列幂等**：内容寻址 + 幂等键 + 附着既有作业的主体设计正确；本轮问题集中在“同路径多作业”和“转换窗口”。

---

## 4. HIGH 级发现

### 4.1 R8-PIPE-1 中文空格章标题不被识别（FR-104）

**位置**

- `doc2md/pipeline/s3_structure.py:30`：`_CJK_HEAD = re.compile(r"^\s*(第[一二三四五六七八九十百千\d]+[章节条款项编部篇])")`
- `doc2md/pipeline/s3_structure.py:509`：`numbered = bool(_NUM.match(t) or _CJK_HEAD.match(t))`
- `doc2md/pipeline/s3_structure.py:523`：第三回退的 `cjk_head`
- `tests/make_golden.py:106`：`chapters.append(f"第 {i} 章")`
- `tests/make_golden.py:1348`：自检正则 `第\s*\d+\s*章`（允许空格）

**现象与证据**

- 本地探针：
  - `"第 1 章 总则"` → `None`（不判标题）
  - `"第1章 总则"` → `1`
  - `"第 1 章"` → `None`
  - `"1. 安装依赖"` → `1`（有序列表/标题第三回退，CH-478 口径）
  - `"1.1 范围"` → `2`
- 黄金样本转换统计：S1 有 64 行章标题；S3/convert 产出 **576 个 heading、0 个 H1**；64 个章标题全部成为 `paragraph`，部分与后续正文合并（例如 `第 2 章此项适用于...`）。
- 生成器声明 `64 * (1 + 3 + 6) = 640` 个标题（`make_golden.py:103/111`），实测正好少 64 个，缺口与章标题数量完全吻合。
- 生成器自检正则允许 `第\s*\d+\s*章`，所以「资产里有 64 个章标题」这一断言仍然通过；测试绿而功能不成立。

**影响**

- FR-104 标题结构在中文文档的常见写法下失效；TOC、层级校正、下游检索与人工审阅都受影响。
- 黄金样本作为“最终验收资产”失去对 H1 的覆盖，属于验收盲区。
- 不是 CH-478 的回归：CH-478 处理的是“同字号单级编号项误判 H1”，这里是有明确章标记却被漏判。

**修复方案**

1. 放宽 CJK 章标记：
   ```python
   _CJK_HEAD = re.compile(
       r"^\s*(第\s*[一二三四五六七八九十百千\d]+\s*[章节条款项编部篇])")
   ```
   允许 `第`、数字、单位之间的水平空白（含全角空格）；不在数字内部任意插空白，避免过度匹配。
2. 增加正/反例：
   - 正例：`第 1 章 总则`、`第1章 总则`、`第 一 章 总则`（若决定支持）、`第 12 节 范围`；
   - 反例：`第 3 章所述内容，详见后文。`（长句/句末标点）、`第 3 章的内容需要结合上下文理解`（短但无句末标点，需评估）。
3. 黄金自检从“正则能找到章标题”升级为“S3 产出的 heading 数量与层级必须匹配”：至少断言 `H1 == 64`、`heading == 640`，并把实际计数写入 report。
4. 重新生成黄金样本与 MANIFEST；按 D12 升 `PIPELINE_VERSION`。
5. 把真实中文 PDF 的误报/漏报评估登记为 B-15。

**测试**

- `tests/test_ch483_pipeline.py::test_cjk_spaced_chapter_headings`（正例）
- `...::test_cjk_spaced_chapter_negative`（长句/句末标点）
- 黄金回归：`test_golden_320p` 增加 `h1_count == 64` 与 `heading_count == 640`；`make_golden.py` 自检同步改为结构断言。

**风险与取舍**

- 放宽后会扩大 `_CJK_HEAD` 的命中面；第三回退对“短行 + 无句末标点 + CJK 章标记”本来就会判标题，带空格只是补齐同一语义。建议同时把“短行 + 无标点”的阈值与反例加入测试，避免把目录页/正文引用误判。

**工作量**：0.5 人日（含黄金重生成与断言）。

---

### 4.2 R8-PIPE-2 块级/行内 `w:sdt`、`w:customXml` 静默丢失（FR-101）

**位置**

- `doc2md/pipeline/s1_parse.py:600-611`：`read_docx` 只遍历 `document.element.body` 的直接子级 `w:p` / `w:tbl`。
- `doc2md/pipeline/s1_parse.py:417-418`：非修订段落用 `p.runs` / `p.text`。
- `doc2md/pipeline/s1_parse.py:454-461`：`_docx_all_runs` 只在 `_docx_has_revision` 命中时调用。
- python-docx 1.2.0 的 `CT_P.text` 只拼接直接 `w:r | w:hyperlink`，不包含 `w:sdt`、`w:customXml`。

**现象与证据**

- 构造块级内容控件：

  ```xml
  <w:sdt><w:sdtContent><w:p><w:r><w:t>内容控件内正文</w:t></w:r></w:p>
  </w:sdtContent></w:sdt>
  ```

  `read_docx` 的返回值中**完全没有**该段落；不是文本被截断，而是整块消失。

- 行内内容控件（`<w:p><w:r>前</w:r><w:sdt>...<w:t>中</w:t>...</w:sdt><w:r>后</w:r></w:p>`）：
  `p.text` 只得到“前后”，`p.runs` 也不含 sdt 内 run；由于段落没有 `w:ins`/`w:del`，不会走 `_docx_all_runs`。
- `w:customXml` 包裹的段落/表格同样被直接子级遍历跳过。

**影响**

- FR-101 文本完整抽取不成立；内容控件在合同、表单、模板类 Word 文档中很常见。
- 内容缺失是静默的：没有告警、没有 `has_revision`、报告里也看不出来。
- 现有的 CH-476 修订修复只解决了“有修订时”的全 run 抽取，不能覆盖无修订的 sdt。

**修复方案**

1. 新增递归块遍历器：
   ```text
   _iter_docx_blocks(container):
       按文档顺序遍历 container 的直接子级：
         w:p        -> 产出段落
         w:tbl      -> 产出表格
         w:sdt      -> 递归进入 w:sdtContent
         w:customXml-> 递归进入其子级
         其他       -> 忽略（保持现有边界）
   ```
   `read_docx` 的正文遍历改用它；脚注/尾注 part 仍走原有 `w:p` 逻辑（注文里也应按需支持 sdt，见测试）。
2. 段落文本/格式采用“按需全 run”策略，兼顾正确性与性能：
   - `_DocxCtx` 增加文档级预检：`has_sdt`（`w:sdt`）、`has_custom_xml`、`has_links`；
   - 若段落无修订、文档无 sdt/customXml、段落无超链接 → 走原 `p.text` / `p.runs` 快路径，保证普通 DOCX 逐字节不变（G6）；
   - 否则用 `_docx_all_runs` 的“接受后”视图，按文档顺序拼接，`w:delText` 不取；超链接文本只计一次（`p.text` 已包含 hyperlink，全 run 也会包含，二者一致）。
3. 页分隔符检测随递归遍历保留：在段落所在块处理时检查 `w:br[@w:type='page']`；sdt 内的分页符同样生效。
4. 嵌套表格仍不在本轮范围（与 CH-476 口径一致），但 sdt 包裹的表格要能抽取。

**测试**

- 块级 sdt 段落/表格、行内 sdt、`w:customXml` 包裹、sdt 内分页符、sdt 内超链接。
- 普通 DOCX 的 G6 字节等价（现有黄金与普通样本）。
- 内容顺序：sdt 前后的段落顺序不得改变。

**风险**

- 无条件使用全 run 会改变超链接/域的格式提取口径；采用上述“按需”策略可把变更限制在含 sdt/customXml/链接/修订的文档。
- 递归遍历要防止异常嵌套导致重复输出；以“容器只遍历一次、sdtContent 只进入一次”为纪律写测试。

**工作量**：1–1.5 人日。

---

### 4.3 R8-SVC-1 同步转换期间的删除会导致文档复活（FR-128）

**位置**

- `doc2md/api.py:293-340`：`_finish_one` 在**转换完成之后**才 `sched.submit(...)`，随后在事务内 `mark(done)` + `save_ir`。
- `doc2md/api.py:387`（并行批次）与 `:397/417`（串行批次）：同样是“先 convert，后 submit/落库”。
- `doc2md/api.py:522-528`：删除端点执行 `delete_doc`，无转换期协调。
- 现有守护注释位于 `api.py:310-313`，只覆盖 submit 与事务之间的小窗口。
- 现有测试 `tests/test_ch476_fixes.py:172-221` 只模拟“submit 之后、落库之前”删行；没有覆盖“转换期间删除”。

**现象与证据（代码路径推演）**

1. 客户端 A 发起同步 `POST /jobs`，服务端开始 convert（耗时可能数十秒/数分钟）。
2. 客户端 B 在转换期间 `DELETE /docs/<id>?confirm=true`：`delete_doc` 删除 docs/blocks/tasks/产物并写入 `deletions` 墓碑。
3. A 的 convert 返回，`_finish_one` 调 `sched.submit`：由于旧 task 已被级联删除，submit **新建一条 queued 行**。
4. 事务内 `mark(job,"done")` 成功（新行存在），`save_ir` 重新插入 docs/blocks/FTS。
5. 结果：B 删除的文档复活；`deletions` 有留痕但数据仍在。若 B 删除的是整源，同样复活。

**影响**

- 直接违反 FR-128“删除即删除”的承诺；用户以为已删除的敏感文档重新出现。
- 审计面出现“有删除留痕但文档仍可访问”的矛盾状态。
- 与 CH-476 修复不是同一窗口：该修复假设 task 行在 submit 后保持存在，转换期删除会先收走旧行、再由 submit 新建。

**修复方案（D1）**

1. 在请求入口记录当前 `deletions` 最大自增 id：`since_seq = MAX(id)`（转换开始前）。
2. `_finish_one` 接收 `since_seq`，在**同一个 `BEGIN IMMEDIATE` 事务**内、`mark` 与 `save_ir` 之前检查墓碑：
   ```sql
   SELECT 1 FROM deletions
    WHERE (doc_id=? OR doc_id=?)
      AND id > ?
    LIMIT 1
   ```
   第二个 `doc_id` 为 `source:<source_key>`（整源删除墓碑）。用自增 id 而非时间戳，
   避免“同一毫秒内先删后提交”被 `ts >= since` 误判。
3. 命中即抛 `E4041_NotFound`（保持现有 `doc_deleted_during_conversion` 的客户端契约），事务回滚；随后在新事务外把 submit 新建的 task 行标为 `rejected`/E4041（若行仍存在），并写 `job_rejected` 事件。调整现有 catch：不能假设“行已不在”。
4. 为 `deletions` 增加 `(doc_id, id)` 索引（`ix_deletions_doc_seq`），使检查 O(log n)；按 D12 升 `DB_VERSION`。
5. 该序列在单机 SQLite 中单调递增；不需要依赖系统时钟。

**为什么不是“预建 job 行”**

- 预建后 worker 可能认领同一 doc 的作业，造成双执行/双落库；现有 `uq_tasks_doc_active` 与附着逻辑会变复杂。墓碑检查在提交事务内一次完成，成本更低、语义更清晰。

**测试**

- 新增 `tests/test_ch483_service.py::test_sync_delete_during_conversion_does_not_resurrect`：
  - monkeypatch `convert` 为“先执行删除（调用真实 `delete_doc`），再返回 IR”；
  - 断言响应 `rejected/E4041`、`docs` 无行、`blocks` 无行、`tasks` 无 done、`deletions` 有留痕；
  - 断言 `save_ir` 未被调用（计数替身）。
- 整源删除变体：删除 `source:<key>` 后同样不复活。
- 反向用例：删除发生在请求开始**之前**（用户重新上传同一内容）→ 允许落库；用 `since` 边界测试。
- 并发测试：删除线程与转换线程同时跑，重复 50 次无复活（沙箱可做；真实多进程压测仍归 B-4）。

**工作量**：1 人日（含索引迁移与测试）。

---

### 4.4 R8-OPS-1 “删除前本地快照”未实现（FR-128 / FR-607）

**位置**

- `doc2md/api.py:522-528`：删除端点只做 `confirm` + 权限检查，然后直接 `delete_doc`。
- `doc2md/storage/repo.py:323-379`：`delete_doc` 先删产物，再在一个事务内级联删行并写墓碑；**没有快照**。
- 文档承诺：
  - `docs/dev-plan.md:705`：“删除级联 | `confirm=true` + admin 双闸；删除前本地快照可恢复”
  - `docs/Doc2md项目开发方案V1.0.md:427/480`：“删除前本地快照（FR-607）可恢复”

**影响**

- 误删后能否恢复完全取决于“恰好有一份定时快照”；日快照窗口内最多丢 24 小时，cron 未运行/失败时可能完全没有可恢复副本。
- 与文档承诺不符，属于验收口径失真；在真实误删投诉场景中会被直接放大。
- `delete_source` 一次删多个版本，恢复面更大。

**修复方案（D2）**

1. 删除入口（单文档与整源）在执行删除前，调用 `BackupPlan.snapshot(state, kind="predelete")` 生成**整库** SQLite 在线快照（sqlite3 backup API，WAL 安全）。
2. 落点：`ops.backup_local_dir`（默认 `work/reports/backups`），命名 `predelete-<UTC 时间戳>-<随机后缀>.sqlite`，与定时 `state-*.sqlite` 区分，避免误认。
3. **Fail-closed**：快照失败（磁盘不足、IO 错误）→ 删除不执行，返回 `E5031_StorageError`（500）并保留库行；前端/CLI 可重试。磁盘守卫复用 `assert_free_space`。
4. 保留期：复用 `ops.backup_rotation_days`（默认 14 天）；`BackupPlan.rotate()` 扩展识别 `predelete-*`，或在删除路径追加一次轻量清理。必须保证“至少保留最新一份”，即使时间超过 14 天（防止长期无删除后首次删除前把唯一旧快照清掉——虽然新快照刚生成，逻辑上安全，但防御性保留更稳）。
5. 审计：`deletions.cascade_json` 或事件中记录快照文件名指纹（不含路径正文），便于事后定位；NFR-20 口径不变。
6. 性能：每次删除增加一次 O(DB 大小) 的备份写。删除是低频操作，按“数据安全 > 删除延迟”排序；在报告中记录 p95 目标（100MB 库 < 2s，实测后再校准）。
7. 对 `delete_source`：整源只做**一次**快照，再循环删除，避免 N 份重复快照。

**测试**

- 删除后快照存在、可用 `restore` 恢复、恢复后目标文档可见。
- 快照失败注入（monkeypatch `snapshot` 抛 OSError）→ 删除返回 500，库行与产物仍在。
- 保留期清理只删 `predelete-*` 且保留最新一份。
- 整源删除只生成一份快照。
- 现有删除测试需允许新增文件，不改变响应主体。

**工作量**：1 人日。

---

### 4.5 R8-OPS-2 无恢复 CLI，现有恢复步骤不安全（FR-607）

**位置**

- `doc2md/cli.py:77-148`：仅有 `serve/token`、`cron`、`backup drill`、`convert`、`work`、`watch`、`tokenizer`、`doctor`；**没有 `backup restore`**。
- `doc2md/ops/backup.py:287-307`：`restore` 解密到 `.part`、fsync、`replace(dst)`、fsync 目录、chmod；**不清理目标旁的 `-wal`/`-shm`**，也不检查服务是否在运行。
- `ops/backup.md:51-58` 教用户直接 `plan.restore(..., Path("work/state.sqlite"), p)`，随后才跑 `PRAGMA integrity_check`；没有停服前置、没有旧库备份。

**影响**

- 恢复是灾难恢复的最后一步，却要用户手写 Python；步骤不可审计、不可脚本化。
- 在线恢复时：
  - 旧 `state.sqlite-wal`/`-shm` 可能残留；新库打开时若 WAL 头与库不匹配，可能应用旧帧或直接损坏。
  - 正在运行的 serve/work 持有旧库连接，替换文件后连接仍指向旧 inode，后续写入会落到“已删除的旧库”，造成双库分叉。
- 没有旧库保留：恢复选错文件后无法回退。

**修复方案（D3）**

1. 新增 CLI：
   ```text
   doc2md backup restore SRC [--to PATH] [--force] [--passphrase-env ENV]
   ```
   - `SRC` 支持 `.enc`（AES-GCM 容器）与 `.sqlite`（明文快照）；默认 `--to $ROOT/state.sqlite`。
   - 口令默认读 `DOC2MD_BACKUP_PASSPHRASE`。
2. 安全序列（严格顺序）：
   1. 解密/拷贝到目标同目录的 `.part`；
   2. 对 `.part` 执行 `PRAGMA integrity_check`，非 `ok` 即失败；
   3. 若目标已存在且未给 `--force` → 拒绝；给 `--force` 后，先对旧库做一次 `pre-restore-<ts>.sqlite` 在线备份（复用 sqlite3 backup API）；
   4. 删除/重命名目标旁 `-wal`、`-shm`（仅在确认无服务运行时；本产品单机自用，要求用户先停 serve/work，文档明确写出；`--force` 不绕过该要求，只绕过“目标存在”检查）；
   5. 原子替换 `.part` → 目标，fsync 目录；
   6. 对恢复后的库再跑一次 `PRAGMA integrity_check`，并打印表计数摘要；
   7. 退出码：0 成功、2 参数/拒绝、4 完整性/恢复失败（与 drill 的 4 保持一致）。
3. 不在本轮实现跨平台进程锁；以“离线恢复 + 文档前置 + 旧库备份”作为安全边界。真机外置卷恢复与停服流程登记 B-10。
4. 更新 `ops/backup.md`：删除 Python 直接调用示例，改为 CLI 序列；明确“先停 serve/work、再恢复、再启动”。
5. `restore` 的库文件权限沿用 `_chmod600`；失败时保留 `.part` 供诊断还是删除？选择**删除半成品**（与现有实现一致），但把错误与路径写入 stderr。

**测试**

- 加密容器恢复成功；错误口令失败且不产生目标文件。
- 篡改容器失败（认证失败）。
- 目标已存在无 `--force` → 拒绝；有 `--force` → 生成 `pre-restore-*`。
- 目标旁存在 `-wal`/`-shm` → 恢复后不残留，且完整性通过。
- `.part` 完整性失败 → 目标保持不变。
- CLI 参数/退出码/帮助文本。

**工作量**：1 人日（含文档与测试）。

---

### 4.6 R8-OPS-3 外置备份失败后仍轮出，可能删掉最后一份（FR-128 / NFR-18）

**位置**

- `doc2md/cron.py:158-183`：`external_backup` 失败只记 `ok=False`，随后无条件执行 `plan.rotate()`。
- `doc2md/ops/backup.py:309-343`：`rotate` 删除本地与外置两个目录中所有过期副本；没有“保留最新一份”的保护。

**现象**

- 周流程中，本地快照成功后：
  - 外置卷未挂载 / 无口令 / 空间不足 / 写入失败 → `external_backup` 失败；
  - 但 `plan.rotate()` 仍扫描 `external_dir`，把超过 `rotation_days` 的旧外置副本全部删除。
- 若外置备份连续失败超过保留期（默认 14 天），最后一份外置副本会在某次成功的本地快照后被删掉；此时外置卷上**没有任何可恢复副本**。
- 本地轮出不受影响：`rotate` 只在 `snap is not None` 分支内执行，本地快照失败时不会轮出；问题特定于外置目录。

**影响**

- 这是“备份存在性”级别的数据安全缺陷：本地盘损坏/丢失时，外置副本是最后防线，却可能因“备份失败”被清空。
- 现有测试没有覆盖“外置失败 + 旧外置副本过期”的组合。

**修复方案（D4）**

1. 将轮出拆成两个语义：
   - `local_rotation`：本地快照成功即可执行；
   - `external_rotation`：仅当本轮 `external_backup` **成功**时执行；未配置外置目录时跳过（不是失败）；配置了但无口令/失败时跳过，并在报告中记录 `external_rotation: skipped`。
2. `rotate(include_external=False)` 增加参数；默认保持兼容，但 cron 显式传参。
3. 对每个目录始终保留**最新一份有效副本**（按生成时间/名字），即使其年龄超过保留期；这防止时钟异常/长期失败后的“全删”。
4. `rotation_days` 防御：`max(1, int(...))`；配置校验已有 `_int(1, ...)`，直接构造 `BackupPlan` 时也兜底。
5. 周报字段：`external_rotation` 的 `removed/skipped/reason` 与 `external_backup` 状态并列，便于运维判断。

**测试**

- 外置备份失败 + 旧副本过期 → 外置副本保留；`external_rotation.skipped` 存在。
- 外置备份成功 + 旧副本过期 → 删除旧副本，保留新副本。
- 未配置外置 → 不报失败，本地轮出正常。
- 最新一份即使过期也不删（本地与外置各一条）。
- 连续失败 20 天模拟（伪造时间）→ 至少一份仍在。

**工作量**：0.5 人日。

---

### 4.7 R8-DOC-1 B-1 登记谎称噪声判据已落地

**位置**

- `docs/dev-plan.md:1762`（B-1）：“判据已落地（`noise>0.9 且 letters/chars<0.1`、连续 ≥3 页或全文占比超限才拒收），**误杀面未评估**”。
- 实际实现：
  - `doc2md/pipeline/s1_5_gate.py:101`：`letters` 只用于 `cjk_ratio`，没有返回/存储；
  - `s1_5_gate.py:204-205`：`noise > 0.9` 只追加原因字符串；
  - `s1_5_gate.py:197-207`：`strict=False` 时硬判据只看 `chars < empty_chars or nlines == 0`；
  - `s1_5_gate.py:220`：`strict = self.mode != "auto"`；`finish()` 的 auto 分支只用 empty 标记做连续页/占比判断。
- 因此 auto 模式（默认）下，噪声比再高也不会拒收；`letters/chars` 根本没有参与判定。

**影响**

- 文档对“已落地”的声明不成立，后续审查/验收会误以为 R7-PIPE-5 已闭环。
- 对“ToUnicode 损坏、乱码但非空”的 PDF，auto 模式会放行，产出低质量正文；这正是 R7-PIPE-5 要解决的问题。
- 这是本轮唯一的“文档直接说谎”级 HIGH，必须立即更正，不能等到实现完成。

**修复方案**

1. **立即（Batch 0）**把 B-1 文字改为真实状态：
   > 指标已采集（noise_ratio），但 auto 模式尚未将噪声作为硬判据；仅 strict 模式记录原因。计划在 CH-483 实现 `letters_ratio` 与 auto 噪声拒收，需真实语料评估误杀面。
2. 实现见 R8-PIPE-3（D8）；实现完成后再把 B-1 恢复为“判据已落地，误杀面待真实语料评估”。
3. 在报告中保留“文档状态与代码状态一致性”检查项，避免同类漂移。

**测试**

- 文档无自动化测试可断言语义；可在 `test_ch477_version_sync.py` 增加一条“B 类登记中的‘已落地’关键词必须伴随实现文件/测试锚点”的静态检查（可选，成本低）。
- 更实际的是：R8-PIPE-3 的测试直接断言 auto 模式对噪声页拒收，文档随代码更新。

**工作量**：0.2 人日。

---

## 5. MED 级发现

### 5.1 R8-PIPE-3（A+B）S1.5 auto 模式忽略噪声页

**位置**

- `s1_5_gate.py:89-107`：`_page_metrics` 返回 `(chars, cjk_ratio, nlines, noise_ratio)`，`letters` 未返回。
- `s1_5_gate.py:197-207`：`_level` 在 `noise > 0.9` 时追加原因，但 `hard` 只由 empty 决定（strict=False）。
- `s1_5_gate.py:209-243`：auto 分支用 `bad_flags`（empty）做连续页/占比判断。

**影响**

- 默认 auto 模式对“文本层存在但大量乱码”的 PDF 不拒收；S1 会把这些行当正文，S3 结构判定被噪声污染。
- strict 模式（`even5`/`legacy3`）会因任意采样页 `noise>0.9` 拒收，口径比 auto 严得多；两种模式行为差异没有文档化。

**修复方案（D8）**

1. `PageReport` 增加 `letters_ratio` 字段；`_page_metrics` 返回 5 元组（或返回小 dataclass，避免位置参数蔓延）。
2. auto 模式定义噪声硬标记：
   ```text
   noise_hard = noise_ratio > 0.95 and letters_ratio < 0.05 and nlines > 0
   ```
   与 empty 标记合并为 `bad_flags`，沿用现有 `bad_run_min`（2）与 `bad_ratio_max`（5%）；
   纯图片页（nlines=0）仍由 empty 处理，不重复计算。
3. strict 模式保持现有口径（任意 `noise>0.9` 采样页拒收），避免行为回退；但在 detail 中补 `letters_ratio`。
4. 阈值写成模块常量并注释来源；不新增配置键，避免运维面膨胀。
5. 误杀评估归 B-1（真实乱码/图表语料），实现只保证“明显噪声”被拦。

**测试**

- 构造合成页：文本为空但噪声行占比高、letters_ratio 低 → auto 拒收。
- 图表/公式页：nlines 少、letters_ratio 正常 → 不拒收。
- 单页噪声 + 大量正常页（占比 <5%）→ 不拒收；连续 2 页噪声 → 拒收。
- strict 模式原行为不变（回归测试）。

**工作量**：0.5 人日。

---

### 5.2 R8-PIPE-4（A+B）DOCX 自动编号完全未识别

**位置**

- 全仓 grep 无 `numPr` / `numbering.xml` 处理（`doc2md/` 下为空）。
- `s1_parse.py:_docx_para_line` 只取段落文本/样式；`s3_structure.py:373-394` 只识别文本里已有的 `1.`/`•` 等字面标记。

**现象**

- Word 自动编号段落的 `p.text` 不含编号（编号由 `numbering.xml` + `w:numPr` 渲染）；抽取后成为普通段落，列表结构、层级、序号全部丢失。
- 多级列表（1 / 1.1 / 1.1.1）尤其明显；项目符号列表同样丢失。
- 与 CH-478 不同：CH-478 处理“文本里本来就有编号”的 PDF/DOCX 行；这里编号根本不在文本里。

**影响**

- FR-104/列表结构不完整；下游 Markdown 丢失有序/无序语义。
- 标题外的编号段落可能被误当正文甚至误判标题（取决于文本内容），进一步污染结构。

**修复方案（D5）**

1. 读取 `word/numbering.xml`（若存在），建立：
   ```text
   numId -> abstractNumId -> level(0..8) -> {numFmt, lvlText, start, pStyle}
   ```
   同时解析 `styles.xml` 中样式级别的 `w:numPr`（Word 常把编号挂在样式上）。
2. `Line` 增加内部字段（不进入 IR schema）：
   - `list_marker: str = ""`（如 `"1."`、`"•"`、`"1.1."`）
   - `list_level: int = -1`
3. 解析段落时：
   - 若段落有 `w:numPr`（直接或经样式），解析 `ilvl`/`numId`，按 `numFmt` 生成 marker；
   - 支持 `decimal`、`lowerLetter`、`upperLetter`、`lowerRoman`、`upperRoman`、`bullet`；其他格式回退为 decimal；
   - 计数器以 `(numId, ilvl)` 维护，较高层级递增时重置低层级；`start` 生效；
   - 多级 marker 按 `lvlText` 拼接（如 `%1.%2`）；无法解析时用“层级缩进 + 序号”。
4. S3 列表分支优先使用 `ln.list_marker`：
   - 生成 `list_item`，文本前缀保留 marker（`1. ` / `- `），层级用 2 空格缩进；
   - 标题样式段落（`level_hint`）不转为列表；
   - 表格单元格内的编号段落：本轮不平铺为行，保持单元格文本（避免列语义混乱）。
5. 性能：`numbering.xml` 每文档解析一次，O(段落数)；计数器字典 O(1)。

**测试**

- 单级 decimal、bullet、start=3、多级 1/1.1/1.1.1、样式挂载编号、重启编号。
- 表格内编号不破坏表格。
- 无 numbering.xml 的文档逐字节不变（G6）。
- 与 `_is_ordered_item` 共存：已有字面 marker 不被重复添加。

**B 类**：真实 Word 模板（含列表样式、编号重启、中文编号）归 B-13；本轮用 python-docx/原始 XML 构造最小样本。

**工作量**：2 人日（含编号解析与测试）。

---

### 5.3 R8-PIPE-5 DOCX `gridSpan`/`vMerge` 未归一，列错位

**位置**

- `s1_parse.py:567-578`：`_docx_table_rows` 只遍历直接 `w:tr`/`w:tc`，每行单元格数即实际 `w:tc` 数。
- 未读取 `w:tcPr/w:gridSpan` 与 `w:tcPr/w:vMerge`。

**现象与证据**

- 构造一行 `gridSpan=2` 的表格：`_docx_table_rows` 返回 1 个 cell；GFM 渲染时该行只有 1 列，与后续行的 2 列不对齐，`_gfm_table` 会补空列，导致内容整体错位。
- 纵向合并（`vMerge` continue）会产生“缺失单元格”的行；当前实现把后续行的单元格左移，列语义错误。
- 空单元格本身保留空串（既有行为正确），但合并语义没有表达。

**影响**

- 表格是 FR-101 的常见结构；列错位会让下游解析、检索、人工审阅都误读。
- 合并单元格在报告/合同/规格书中非常常见。

**修复方案（D5）**

1. 读取每行 `w:tc` 的 `gridSpan`（默认 1），将单元格文本展开到网格：
   - 第一个网格位置放文本，其余位置放 `""`；
   - 保证每行列数 = 表格网格列数（取全表最大网格宽度）。
2. `vMerge`：
   - `w:val="restart"` 的单元格作为锚点，保留文本；
   - `w:val` 缺省/`continue` 的单元格输出 `""`（不伪造重复文本），保持行网格对齐；
   - 可选在 `Line.row_meta`/块 `flags` 记录合并数量，供审阅页说明（不进入 GFM）。
3. 表格网格宽度以 `w:tblGrid/w:gridCol` 数量为准；缺失时回退到最大展开宽度。
4. 嵌套表格继续拍平进单元格（现有口径），但展开后不得打乱外层列。
5. 性能：每表一次线性扫描，无额外全树遍历。

**测试**

- gridSpan=2、gridSpan=2+3 混合、vMerge restart/continue、右上/左下不规则合并、空表。
- GFM 每行列数一致；内容只出现一次。
- 普通表格逐字节不变（G6）。

**B 类**：真实 Word 模板归 B-13。

**工作量**：1 人日。

---

### 5.4 R8-PIPE-6 单元格内 `w:br`/`w:tab` 丢失

**位置**

- `s1_parse.py:554-564`：`_docx_cell_text` 用 `"".join(t.text for t in p.iter(w:t))`，只取 `w:t`。
- `w:br`、`w:cr`、`w:tab` 不是 `w:t`，被直接丢弃；同一段落内多个 run 无分隔符拼接。

**现象与证据**

- 段落文本 `A\nB\tC`（run 内含 `w:br` 与 `w:tab`）→ 单元格文本 `ABC`。
- 代码、地址、多行单元格、键值表会因此变成不可读的粘连文本。

**影响**

- 表格内容语义丢失；与 R8-PIPE-2 同属“静默丢内容”，但发生在单元格路径，现有修订修复未覆盖。

**修复方案（D5）**

1. 单元格文本改用接受后视图的 run 提取：
   ```text
   text = "".join(Run(r, p).text or "" for r in tc.iter(w:r))
   ```
   `Run.text` 已把 `w:tab` 映射为 `\t`、`w:br`/`w:cr` 映射为 `\n`；`w:delText` 不取。
2. 段落之间仍以单空格连接（CH-476/R7-GAP-3 口径），避免破坏既有单段落单元格的 G6。
3. 单元格文本在进入 GFM 前：`\n` 按现有 `_gfm_table` 的 `.replace("\n", " ")` 处理（保持表格单行），但 IR/审阅页保留原始换行；若希望代码型单元格保留换行，需要表格渲染模型升级，本轮不做。
4. 注意 sdt 内 run：`tc.iter(w:r)` 天然包含，和 R8-PIPE-2 修复一致。

**测试**

- `A\nB\tC` 单元格 → 文本含换行/制表符（IR 层）；GFM 层按既定口径替换为空格。
- 多段落单元格连接不变（G6）。
- 修订删除文本不出现；插入文本出现。
- 含 sdt 的单元格。

**工作量**：0.5 人日。

---

### 5.5 R8-PIPE-7（A+C）FR-108 格式标记无生产者

**位置**

- `doc2md/ir/model.py:52`：`Block.marks` 字段存在。
- `doc2md/pipeline/s4_render.py:80-85`：渲染端消费 `bold/italic/strikethrough`。
- 全仓检索：**没有任何地方给 `Block.marks` 赋值**（只有测试手工构造）。
- `tests/test_m5_m6c.py:65-76` 只验证“给了 marks 能渲染”，不验证生产。

**影响**

- FR-108 在端到端链路上未交付：DOCX/PDF 的粗体、斜体、删除线不会进入 Markdown。
- 现有测试造成“已实现”的错觉；与 R8-DOC-1 同类的验收盲区。

**修复方案（D6）**

1. 在 S3 生成块时写入统一块级标记，规则保守：
   - 仅对 `paragraph` 与 `list_item`；
   - 仅当块内**所有非空白文本**都带同一标记时写入（避免把局部粗体扩散到整段）；
   - 标题、代码、表格、引用不写 marks（渲染语义不同，避免 `**# 标题**` 之类）；
   - `strikethrough` 只在 `preserve_strike` 配置下渲染（默认关闭，现有红线 `assert_render_contract` 不允许 `~~`）。
2. 数据来源：
   - DOCX：`Line` 已有整行 `bold/italic/strike`（当前是“任一 run 为真即整行真”，需要改为三态：`all/any/none` 或额外字段 `uniform_bold` 等）；
   - PDF：`Line.bold/italic/strike` 来自字体名，按“块内所有非空行一致”判定。
3. S3 在合并/续行时维护标记一致性：合并后若各行的标记不一致，清除该标记（`_join_back` 等路径）。
4. 渲染端不需要改动；`Block.marks` 已有的 IR JSON 结构不变，SCHEMA_VERSION 不变。
5. 黄金样本会新增 `**`/`*`，必须重生成并人工抽查（见第 9 章）。

**测试**

- DOCX 整段粗体/斜体/删除线 → marks 写入、Markdown 正确。
- 混合粗体段落 → 不写 marks（不制造错误强调）。
- PDF 整段粗体 → marks；标题不写。
- 代码/表格/引用不写。
- `preserve_strike=false` 时 `~~` 不出现；true 时出现。
- G6：不含格式标记的样本逐字节不变。

**风险**

- PDF 字体名判定可能有误报（如标题字体被用于正文）；通过“块内一致 + 排除标题”降低风险，仍需黄金人工抽查。真实语料归 B-15/B-13。

**工作量**：1.5 人日（DOCX 三态 + PDF 一致性 + 测试/抽查）。

---

### 5.6 R8-PIPE-8（A+C）`source_name_raw` 进入 zip，与契约冲突

**位置**

- `doc2md/pipeline/convert.py:234`：docstring 写“source_name_raw 不进 zip（约定 14）”。
- `convert.py:244`：front-matter 过滤了 raw。
- `convert.py:246-247`：zip 内写入 `ir.json`（`ir.to_dict()` 含 raw）与 `report.html`（`render_report` 在 `:290` 转义展示 raw）。
- `doc2md/ir/model.py:5/92/107/117`：raw 字段在 `meta()`/`to_dict()` 中。
- 设计文档：`docs/Doc2md项目开发方案V1.0.md:134/321/241`（raw 只在报告展示层，不进 zip）。

**影响**

- zip 是交付物；`ir.json` 内出现原始文件名，与 FR-127/约定 14 的“产物不含未消毒名”冲突。
- `report.html` 虽做了 HTML 转义（无 XSS），但它也在 zip 内，因此“仅在展示层”的边界被交付物打破。
- 下游若直接消费 `ir.json`，会拿到未经消毒的原始名，可能再次进入日志/索引/其他文件。

**修复方案（D11）**

1. `ir.json` 导出时过滤 raw：新增 `IR.to_dict(for_export=True)` 或 `meta_for_export()`，`package()` 使用导出视图；`content.md` 的 front-matter 已过滤，保持不变。
2. `report.html` 的策略二选一，本轮裁定：
   - **保留** raw 的转义展示（满足“展示层可追溯”），但把文档改成“raw 不进 content.md/ir.json；仅 report.html 展示层（HTML 转义）”，并确保 report.html 只用于人工审阅；
   - 若后续要求“zip 完全不含 raw”，则在 `package()` 传入 `include_raw=False`，report.html 也不显示 raw。
   选择前者：功能上保留审阅可追溯性，风险面最小；文档与实现统一。
3. 增加测试：zip 内 `ir.json` 不含 raw；`content.md` 不含 raw；`report.html` 含转义后的 raw（若保留策略）。
4. 同步 `model.py` docstring、`convert.py` docstring、设计文档 134/321/241、README（如有）。

**工作量**：0.5 人日（含文档）。

---

### 5.7 R8-LLM-1（A）非字符串 `op` 触发 `TypeError`，整篇 500

**位置**

- `doc2md/llm.py:463`：形状校验只要求 `isinstance(o, dict) and "op" in o`，不要求 `o["op"]` 是字符串。
- `llm.py:163`：`self.whitelist = set(...)`。
- `llm.py:182`：`if name not in self.whitelist`；当 `name` 是 list/dict 时抛 `TypeError: unhashable type`。
- `convert.py:195-220` 的 `repair_ops` 只捕获 `LlmServiceError`；`TypeError` 逃逸为 500/E5000。

**现象与证据（本地探针）**

```text
OpValidator([...]).apply([{"op": ["split_block"]}])
→ TypeError: cannot use 'list' as a set element (unhashable type: 'list')
```

**影响**

- 模型输出畸形（或受到提示注入）时，整篇转换失败；不是“拒绝该算子”，而是进程级异常路径。
- 与“LLM 不可用/输出畸形应降级为拒绝算子”的既有设计不一致；`MAX_OPS_PER_RESPONSE` 与校验器的防御被一个类型错误绕过。

**修复方案**

1. `_parse_ops` 形状校验收紧：
   ```python
   all(isinstance(o, dict) and isinstance(o.get("op"), str) for o in ops)
   ```
   非字符串直接 `LlmServiceError("ops payload shape invalid", stage="parse")`。
2. `OpValidator._check` 纵深防御：
   ```python
   name = op.get("op")
   if not isinstance(name, str) or name not in self.whitelist:
       return ["not_whitelisted"]
   ```
3. 顺带修正 `whitelist or DEFAULT_OP_WHITELIST` 的语义：显式空列表应表示“全部拒绝”，而不是回退默认。改为 `DEFAULT_OP_WHITELIST if whitelist is None else whitelist`；`convert.py:137` 从配置读取时若为空列表，是否允许“全部拒绝”需与配置校验一致（当前 `llm.op_whitelist` 可能不允许空？需在实现时核对）。这是低风险的一致性修正。
4. 测试：`{"op": []}`、`{"op": {}}`、`{"op": 1}`、`{"op": null}`、空列表 whitelist。
5. `_attribute` 对畸形 op 的字符串化也要防崩（若它直接拼接 op，需要 `str()` 或跳过）。

**工作量**：0.3 人日。

---

### 5.8 R8-SVC-2（A+C）同路径多作业中陈旧作业被标 done 但无正文

**位置**

- `doc2md/worker.py:132-165`：`drain_once` 按 `source_path` 分组，一个路径只转换一次；结果应用到 `by_path` 中的**所有** job。
- `worker.py:219-261`：`_finish_ok` 计算 `drift = consumed and enqueued and consumed != enqueued`，只把差异写入 `detail["content_drift"]`，随后标记 job **done** 并 `save_ir`。
- 现有测试 `tests/test_ch476_fixes.py:543-606` 只覆盖单作业的 drift，没有覆盖同路径多作业。

**现象**

1. 同一路径文件先提交作业 J1（内容哈希 H1），文件随后被替换并提交 J2（H2）。
2. worker 处理该路径时只转换当前内容（H2），把结果应用到 J1 和 J2。
3. `_finish_ok` 对 J1 发现 drift，但仍标 J1 done；J1 的 doc_id 是 H1，库里没有 H1 的 docs/blocks。
4. 客户端查 `GET /docs/<J1.doc_id>` → 404，但 `GET /jobs` 显示 done；状态与数据不一致。

**影响**

- 作业状态“说谎”，客户端无法区分“完成”和“内容已被更新版本替代”。
- 若下游按 job 的 doc_id 拉取产物，会得到 404；若按 source_key 查询，又可能拿到新版本，语义混乱。
- 不是数据损坏，但属于可观测性与契约缺陷。

**修复方案（D9）**

1. 新增错误码 `E4092`（409，source superseded）：同路径、已提交但内容被后续作业替代的作业，终态为 `rejected`，`detail` 带：
   ```json
   {"reason": "source_superseded",
    "enqueued_doc_id": "H1...",
    "consumed_doc_id": "H2..."}
   ```
2. `drain_once` 分组后，匹配“本轮消费内容”的作业（`doc_id == consumed_doc_id` 或 `source_key == consumed_source_key`）：
   - 该作业走现有 `_finish_ok` → done；
   - 其余作业标 `rejected/E4092`，不 `save_ir`，写 `job_rejected` 事件。
3. 若组内**没有任何作业**匹配当前内容（文件在最后一次提交后又被替换）：保留现有“单作业 drift → done + content_drift”语义，避免改变已测试的行为；但在 detail 中同时记录 `consumed_doc_id`，并在报告中标注“本轮未创建作业”。
   - 更严格的选择是把最新作业也 rejected，但会让“文件已更新、用户已提交”场景无终态；本轮不选。
4. API `GET /jobs` 已返回 `code`/`detail`，前端需展示新码；文档附录 C 更新。
5. 测试：两作业同路径、J1 陈旧、J2 匹配 → J1 rejected/E4092、J2 done、无 H1 docs 行；三作业同样。

**工作量**：1 人日（含调度分组改造与测试）。

---

### 5.9 R8-PERF-1（A）对账孤儿清理对 UNINDEXED FTS 列全表扫描

**位置**

- `doc2md/storage/repo.py:164-198`：`reconcile_search`。
- `repo.py:193`：
  ```sql
  DELETE FROM search_blocks WHERE doc_id NOT IN (SELECT doc_id FROM blocks)
  ```
  `search_blocks.doc_id` 是 `UNINDEXED`（`db.py` FTS 定义），该 DELETE 需要全表扫描 FTS 内容列。
- `repo.py:196`：`search_map` 的同类 DELETE（`doc_id` 有 `ix_search_map_doc`，但 `NOT IN` 仍可能走全表）。
- 调用点：`api.py:109`（每次启动）、`cron.py:230`（每日）。

**影响**

- 文档量大时，每次服务启动/日流程都要扫全量 FTS；FTS 内容列包含正文，扫描成本随正文体积增长。
- 与“性能优先”直接冲突，且修复不需要改变语义。

**修复方案**

1. 用映射表驱动删除：
   ```sql
   DELETE FROM search_blocks
    WHERE rowid IN (
      SELECT m.fts_rowid FROM search_map m
       WHERE m.doc_id NOT IN (SELECT doc_id FROM blocks)
    );
   DELETE FROM search_map
    WHERE doc_id NOT IN (SELECT doc_id FROM blocks);
   ```
   `search_map` 有 `ix_search_map_doc`，子查询按 doc_id 索引；FTS 删除按 rowid 主键定位。
2. 对“FTS 有行、映射无行”的历史残留，增加廉价计数短路：
   ```sql
   SELECT (SELECT COUNT(*) FROM search_blocks) AS fts,
          (SELECT COUNT(*) FROM search_map) AS map;
   ```
   仅当 `fts > map` 时执行一次 `DELETE FROM search_blocks WHERE rowid NOT IN (SELECT fts_rowid FROM search_map)`；该全扫描是兜底，不是常态。
3. `docs_scanned` 字段当前实际是“divergent 数量”，更名为 `docs_divergent`（返回结构变更需同步文档/测试）；保留 `docs_scanned` 兼容可加新字段，但这是内部 API，建议直接改名并更新测试。
4. 测试：10k blocks + 孤儿 doc 的 EXPLAIN QUERY PLAN 断言走 rowid/索引；孤儿清理后计数；映射缺失的兜底路径。

**工作量**：0.5 人日。

---

### 5.10 R8-PERF-2（A）单文档删除全量 `rglob`，且忽略配置路径

**位置**

- `repo.py:428-448`：`_purge` 对 `root/out`、`root/ir`、`root/tmp` 三棵树各 `rglob("*")`，按文件名子串匹配 doc_id。
- `repo.py:340`：`delete_doc` 默认调用 `purge_files`。
- 配置：`paths.out`/`paths.tmp` 可自定义（`config/loader.py:83/86`），但 `_purge` 硬编码 `root/out`/`root/ir`/`root/tmp`；`worker.py:71-72` 使用 `paths.out`，`cron.py:70` 使用 `paths.tmp`。
- `delete_source`（CH-469）已改为整源一次遍历，但单文档删除仍逐文档全树扫描。

**影响**

- 单文档删除的成本 = O(全部产物文件数)，与“删除一个文档”的语义不匹配；目录越大越慢，且删除在 API 请求线程内执行。
- 自定义 `paths.out`/`paths.tmp` 时，产物根本不在扫描目录里 → 删除后残留 zip/中间文件（数据安全 + 磁盘泄漏）。
- 文件名子串匹配可能误删名字包含该 doc_id 的其他文档（doc_id 是 16/24 位十六进制，碰撞概率低，但不是零；更稳妥的是精确文件名/目录名）。

**修复方案**

1. 产物路径直接构造：
   - `out_dir / f"{doc_id}.zip"`（`paths.out` 配置值）；
   - `tmp_dir` 下已知的中间文件名模式（需核对 batch/convert 的实际命名；若无固定模式，则保留受控扫描）。
2. `ir/` 目录目前没有生产写入点（grep 仅有 `_purge` 引用），属于遗留目录；只在存在时做一次惰性清理，且不参与每个文档的扫描。
3. 精确匹配：文件名等于 `doc_id + 后缀` 或以 `doc_id + "."` 开头，而不是 `d in p.name`。
4. 回退策略：直接路径不存在且目录中有历史遗留时，才做一次有界 `rglob`（例如仅当 `out` 目录 mtime 变化或提供 `--sweep`）；正常删除不扫描全树。
5. 配置路径统一：从 `cfg` 取 `paths.out`/`paths.tmp`；`delete_doc` 需要接收 cfg 或已解析的 `out_dir/tmp_dir`（当前签名只有 root）。可在 `repo` 层新增可选 `paths` 参数，API 调用时传入配置解析结果；避免 repo 依赖 config 模块（保持分层）。
6. 性能验收：在 10k 文件目录中删除 1 个文档，不做全树扫描（用 monkeypatch 计数 `rglob`/`scandir`）；自定义 `paths.out` 时产物确实删除。

**工作量**：1 人日（含签名调整与测试）。

---

### 5.11 R8-OPS-4（A）演练用 `shutil.copy2` 复制在线 SQLite

**位置**

- `doc2md/ops/drill.py:137`：
  ```python
  shutil.copy2(live_db, live_copy) if live_db.is_file() else _create_empty(live_copy)
  ```

**影响**

- WAL 模式下，已提交但未 checkpoint 的事务在 `-wal` 文件中；`copy2` 只复制主库，演练可能拿到旧数据或撕裂状态。
- 演练“通过”不能证明真实备份可恢复，属于验证有效性缺陷。
- 与 `BackupPlan.snapshot` 已使用 sqlite3 backup API 的正确做法不一致。

**修复方案**

1. 抽出 `sqlite_backup(src, dst)` 公共函数（或复用 `BackupPlan.snapshot` 的实现），drill 用它复制在线库。
2. 复制前确保源库连接可打开；复制后关闭连接。
3. 测试：写入未 checkpoint 的 WAL 数据，drill 的 live_copy 必须包含该数据；对比复制前后行数/哈希。
4. 这一步与 R8-OPS-1 的快照逻辑可以共用同一个 helper，减少重复。

**工作量**：0.5 人日。

---

### 5.12 R8-SEC-1（A+C）`follow_symlinks` 是无效开关

**位置**

- `doc2md/security/dirguard.py:25-35`：配置/CLI 的 `follow_symlinks` 取“或”。
- `dirguard.py:89`：链接在 `follow=False` 时跳过并计数。
- `dirguard.py:93/101`：`follow=True` 时仍用 `e.is_dir(follow_symlinks=False)` / `e.is_file(follow_symlinks=False)`，对符号链接均返回 False → 链接被静默跳过，既不计数也不跟随。
- 本地探针：`follow_symlinks=True` 时，链接目录未进入，`skipped_symlinks=0`，`WalkResult` 无 `followed` 字段。

**影响**

- 配置项承诺“跟随符号链接”但实际不生效；watch/批量转换会漏掉链接目录中的文件。
- 如果未来简单改成 `is_dir(follow_symlinks=True)` 而不做 containment，会引入越界读取（链接指向 root 外）和循环风险。

**修复方案（D10）**

1. 默认保持 `follow_symlinks=False`（安全默认不变）。
2. 开启时：
   - `real = os.path.realpath(e.path)`；
   - 必须 `Path(real).is_relative_to(os.path.realpath(base))`，否则计入 `skipped_escaping_symlinks` 并跳过；
   - 目录链接：用 `e.is_dir()`（follow=True）判断，`seen_real` 防循环；文件链接：`e.is_file()`；
   - 输出路径使用链接路径（`e.path`）还是真实路径？为保持用户视角与去重，建议输出真实路径并单独计数 `followed_symlinks`；若输出链接路径，同一文件可能被多个链接重复收录。选择**真实路径**，并在 `WalkResult` 增加 `followed_symlinks`/`skipped_escaping_symlinks`。
3. 限制：链接目标为目录时仍受 `max_depth`/`max_files_per_dir`/deadline 约束。
4. 测试：root 内链接文件/目录被收录一次；root 外链接被跳过并计数；循环链接终止；默认关闭行为不变。
5. B 类：SMB/exFAT/Windows junction 的真实语义归 B-14。

**工作量**：0.5 人日。

---

### 5.13 R8-CFG-1（A+C）配置“层叠”文档与实现不符

**位置**

- `doc2md/config/loader.py:166-188`：默认配置 → 找到第一个候选文件（显式 `--config`，否则 cwd 的 `doc2md.toml`，否则 `$DOC2MD_HOME/doc2md.toml`）→ 只合并这一个。
- `README.md:51`：“读取顺序：默认 → `--config` → `./doc2md.toml` → `$DOC2MD_HOME/doc2md.toml`”。

**影响**

- 用户同时放置 home 配置与项目配置时，只有先命中的一个生效；README 让人以为会层叠，导致“改了配置不生效”的排障成本。
- 与“配置可审计”的运维目标冲突。

**修复方案（D7）**

1. 选择**真正的层叠**（功能完整）：
   - 默认配置 → `$DOC2MD_HOME/doc2md.toml`（若存在）→ 当前目录 `doc2md.toml`（若存在）→ 显式 `--config`（若给出，最高优先级）。
   - 后者覆盖前者，深度合并；未知键告警仍按每个文件分别输出。
2. 显式 `--config` 不存在仍报错；未显式给出时，某个候选不存在不是错误。
3. `explicit` 集合用于 profile 判定的逻辑需要按“最终来源”合并，避免低层键把高层默认值误标为显式。实现时以“每个文件的 dotted keys 累积”处理。
4. 更新 README 与方案附录 A；增加测试：home+cwd 层叠、cwd 覆盖 home、显式覆盖两者、未知键告警、`--config` 不存在报错。
5. 兼容性：现有单文件用户行为不变；双文件用户行为变化属于修复，不是破坏。

**工作量**：0.5 人日。

---

### 5.14 R8-TEST-1（A）测试覆盖缺口

**位置**：`tests/` 全量；具体缺口见下表。

| 行为 | 现有覆盖 | 缺口 |
| --- | --- | --- |
| 中文空格章标题 | 无 | 正/反例、黄金 H1 计数 |
| 块级/行内 sdt | 无 | 段落、表格、分页、顺序 |
| `numPr` 编号 | 无 | 单级/多级/start/style |
| gridSpan/vMerge | 无 | 列对齐、内容只出现一次 |
| 单元格 br/tab | 无 | IR 文本、GFM 收敛 |
| marks 生产 | 仅渲染单测（手工构造） | S3 生产、混合格式、排除面 |
| raw 名不进 zip | 无 | ir.json/content.md/report.html 三面 |
| 非字符串 op | 无 | 类型矩阵 |
| 同步转换期删除 | 仅 submit 窗口 | 转换窗口、整源删除、反向边界 |
| 删除前快照 | 无 | 成功/失败注入/保留期 |
| 恢复 CLI | 无 | 成功/篡改/强制/旧库备份/WAL |
| 外置轮出保护 | 无 | 失败/成功/未配置/保最新 |
| 同路径陈旧作业 | 仅单作业 drift | 多作业、E4092 |
| 对账性能 | 功能测试 | 查询计划/大表 |
| 删除扫描 | 功能测试 | 直接路径/配置路径/不扫全树 |
| drill WAL | 无 | 未 checkpoint 数据 |
| symlink follow | 无 | 内部/越界/循环/计数 |
| 配置层叠 | 单文件 | 双文件优先级 |
| 非对象 JSON | 无 | 各端点 400 |
| 文档版本/资产数 | 版本同步测试 | README/资产统计 |

**修复方案**

- 新增 `tests/test_ch483_pipeline.py`、`tests/test_ch483_service.py`、`tests/test_ch483_ops.py`、`tests/test_ch483_docs.py`（或按现有风格合并到 `test_ch483_fixes.py`）。
- 每个 HIGH 至少 1 项红线（`@pytest.mark.gate`），每个 MED 至少 1 项功能测试；关键路径加变异验证（见第 9 章）。
- 不为“可能发生但无证据”的场景写测试；只覆盖本轮确认的行为。

**工作量**：已计入各发现；集中测试建设约 2 人日。

---

## 6. LOW 级发现

### 6.1 R8-API-1 非对象 JSON 请求体导致 500

**位置**：`doc2md/api.py:154-155`

```python
def body() -> dict:
    return request.get_json(silent=True) or {}
```

若请求体是 `[]`、`"x"`、`1`，`get_json` 返回对应类型（非空），随后调用方执行 `b.get(...)` → `AttributeError` → 500。受影响端点包括 `PATCH /docs/<id>/blocks/<id>`、`POST /jobs`、token/settings 等。

**修复**：`body()` 内校验 `isinstance(data, dict)`，否则抛 `E4001_BadRequest("JSON object required")`；补一个参数化测试（数组/字符串/数字 → 400）。

**工作量**：0.2 人日。

### 6.2 R8-DOC-2 README 版本与常量不一致

**位置**：`README.md:3`（v2.4）vs `doc2md/__init__.py:13`（2.5.0）。

**修复**：README 改为“v2.6”（按 D12 新版本），或改为不写具体版本、引用 `doc2md --version`；在 `test_ch477_version_sync.py` 增加 README 版本断言（若保留具体版本）。同时检查 `docs/dev-plan.md:199` 的远端 run 证据是否需随新版本更新。

**工作量**：0.1 人日。

### 6.3 R8-DOC-3 黄金资产统计陈旧

**位置**：`docs/dev-plan.md:692/694`（旧数字 5244/5139/5137/23596KB/2166835 bytes）。

**实测（2026-10-18）**：5249 个文件 / git 跟踪 5142 / MANIFEST 5140 条 / 23640 KB / 2211232 bytes。

**修复**：更新文档数字，并记录测量命令（`find tests/golden -type f | wc -l`、`git ls-files tests/golden | wc -l`、`wc -l MANIFEST`、`du -sk`、`find ... -printf '%s\n' | awk ...` 或 macOS `stat` 等价命令）。黄金重生成后这些数字还会变，建议在 Batch 4 最后一次统一更新，避免反复改。

**工作量**：0.1 人日。

### 6.4 R8-OPS-5 launchd 安装说明缺路径改写与日志目录

**位置**：`README.md:94-116`；`ops/launchd/*.plist` 使用 `/Users/you/doc2md` 示例路径，`StandardOutPath` 指向 `work/logs/launchd-*.out/.err`。

**影响**：

- 用户直接 `cp` + `launchctl load`，plist 仍指向 `/Users/you/doc2md`，服务无法启动或操作错误根。
- `work/logs` 不存在时，launchd 在 spawn 阶段无法打开重定向文件，作业可能直接失败（日志无处可看）。
- README 没有提示用 `launchctl setenv` 之外的路径替换方式（sed/编辑器），也没有安装前 `mkdir -p`。

**修复**：

1. README 安装步骤增加：
   ```bash
   mkdir -p work/logs
   # 将 plist 中的 /Users/you/doc2md 替换为实际仓库路径
   sed -i '' "s#/Users/you/doc2md#$PWD#g" ~/Library/LaunchAgents/md.doc2md.*.plist
   ```
   macOS `sed -i ''` 与 GNU `sed -i` 差异需注明；Windows 文档另述。
2. 加载前用 `plutil -lint` 校验 plist。
3. 明确日志目录权限（700）与 `work/logs` 由安装者创建。
4. 保留 B-5 真机验证（launchd 实际加载/自愈）。

**工作量**：0.2 人日。

---

## 7. 决策项裁定（D1–D12）

> 用户要求“需要我决策的项目，按照功能全面、性能优先的原则制定最佳方案”。以下裁定不再向用户提问；实施时按此执行。每项列出备选与否决理由。

### D1 同步转换期删除守护：事务内墓碑检查（选定）

| 方案 | 结论 | 理由 |
| --- | --- | --- |
| A. `_finish_one` 事务内按 `deletions.id > since_seq` 检查 | **选定** | 与删除共享同一个写锁串行化；一次索引查询；不改变任务状态机；自增序列不受时钟粒度影响 |
| B. 转换前预建 task 行 | 否决 | worker 可认领同 doc 作业，双执行/双落库；需要额外认领锁 |
| C. 转换后再次检查 docs 是否存在 | 否决 | 删除后 docs 行不存在，但转换前就存在的情况无法区分；且与 submit 新行冲突 |

**契约**：命中墓碑时返回 `rejected/E4041` + `doc_deleted_during_conversion`，不写正文；submit 新建的 task 行标 rejected（若存在）。

### D2 删除前恢复：整库 SQLite 快照（选定）

| 方案 | 结论 | 理由 |
| --- | --- | --- |
| A. 每次删除请求一份整库在线快照，fail-closed，14 天轮出 | **选定** | 一次实现即可恢复杂志、审计、编辑历史；与现有 BackupPlan 复用 |
| B. 删除前导出该文档 IR/JSON | 否决 | 不能恢复编辑审计/事件；多文档删除碎片化 |
| C. 依赖日快照 | 否决 | 最长 24h 窗口；cron 失败时无副本；不满足文档承诺 |
| D. 软删除（标记删除，延迟物理删） | 否决 | 存储与生命周期复杂；检索/导出/权限都要改；本轮不引入 |

**注意**：快照失败不删除；整源删除只快照一次；保留期复用 `ops.backup_rotation_days`，始终保留最新一份。

### D3 恢复入口：离线 CLI + 安全序列（选定）

- `doc2md backup restore SRC [--to PATH] [--force]`；
- 默认要求停 serve/work（文档前置）；`--force` 只绕过“目标存在”，不绕过完整性；
- 旧库先备份为 `pre-restore-<ts>.sqlite`；清理 `-wal/-shm`；原子替换；前后各一次 `integrity_check`；
- 不实现跨平台进程锁（成本高、收益有限）；真实停服/外置卷流程归 B-10。

### D4 轮出策略：失败不轮外置，永远保留最新（选定）

- 外置备份成功才轮外置；未配置跳过；失败跳过；
- 每个目录至少保留最新一份；
- `rotation_days >= 1` 兜底。

### D5 DOCX 结构范围：递归遍历 + 编号 + 网格（选定）

- 块级 sdt/customXml 递归；行内 sdt 走全 run；
- `numPr` 支持 decimal/bullet/多级/start/style；
- gridSpan 展开、vMerge 连续格置空；
- 单元格 br/tab 保留；
- **嵌套表格**仍拍平，不做嵌套 Markdown 表格（GFM 不支持）。

### D6 格式标记：块级一致才写（选定）

- 仅 paragraph/list_item；仅当所有非空白文本统一格式；
- 标题/代码/表格/引用排除；
- 不引入行内 span IR（会改 SCHEMA，成本高）；
- PDF 与 DOCX 同口径；真实语料抽查归 B-13/B-15。

### D7 配置：真正的层叠（选定）

- 默认 → home → cwd → 显式 `--config`；后者覆盖前者；
- 更新 README/方案附录 A；
- 现有单文件用户行为不变。

### D8 噪声判据：auto 增加字母占比（选定）

- `noise > 0.95 && letters_ratio < 0.05 && nlines > 0` 为硬标记；
- 沿用连续 2 页 / 5% 占比阈值；
- strict 模式不变；
- 误杀评估归 B-1。

### D9 同路径陈旧作业：E4092 rejected（选定）

- 匹配“本轮消费内容”的作业 done；
- 其余 rejected/E4092，detail 带 enqueued/consumed doc_id；
- 无匹配作业时保留现有单作业 drift 语义（避免行为突变）。

### D10 符号链接：默认关闭，开启时限定 root 内（选定）

- realpath containment + 循环保护 + 计数；
- 输出真实路径，避免重复；
- 跨文件系统语义归 B-14。

### D11 `source_name_raw`：content.md/ir.json 不含，report.html 可转义展示（选定）

- `ir.json` 用导出视图过滤；
- report.html 保留转义展示，承载“审阅可追溯”；
- 更新三处文档，消除“不进 zip”的绝对表述；
- 若未来合规要求 zip 完全无 raw，再关闭 report.html 展示（一行开关）。

### D12 版本与黄金：一次性升版（选定）

- `PIPELINE_VERSION`：2.5.0 → **2.6.0**（产物字节变化：标题、DOCX 结构、marks、raw 过滤）；
- `DB_VERSION`：6 → **7**（新增 `ix_deletions_doc_seq`；DDL 幂等）；
- `SCHEMA_VERSION`：**14 不变**（IR 字段没有增删；`Line` 新字段不进入 IR）；
- `PLAN_VERSION`：3.16 → **3.17**（方案 header、§1.4、CH-483 行、B 类登记同步）；
- 黄金样本与 MANIFEST 在 Batch 2 末尾一次性重生成；`test_ch477_version_sync.py` 必须全绿；
- 每个 Batch 退出都要本机重跑 gate + full；远端 CI 归 B-7/发布流程，由用户推送后取证。

---

## 8. 分批实施计划

### 8.1 批次总览

| 批次 | 主题 | 包含发现 | 依赖 | 退出条件 |
| --- | --- | --- | --- | --- |
| Batch 0 | 文档真实性（零产品改动） | R8-DOC-1、DOC-2、DOC-3（数字可最后统一）、OPS-5（安装说明） | 无 | 文档与代码状态一致；无代码变更 |
| Batch 1 | 数据安全链 | R8-SVC-1、OPS-1、OPS-2、OPS-3、OPS-4（可与 OPS-1 共用 helper） | D1–D4、DB7 | 删除/备份/恢复/轮出测试全绿；无复活；恢复 CLI 可用 |
| Batch 2 | 功能完整性 | R8-PIPE-1、PIPE-2、PIPE-4、PIPE-5、PIPE-6、PIPE-7、PIPE-8 | Batch 1（DB7 已有）；D5/D6/D11 | 黄金 H1=64/heading=640；DOCX 结构样本；marks；raw 过滤；G6 复审 |
| Batch 3 | 健壮性与性能 | R8-LLM-1、SVC-2、PERF-1、PERF-2、SEC-1、CFG-1、API-1 | Batch 1/2 的代码稳定 | 类型矩阵、E4092、查询计划、配置层叠、400 测试全绿 |
| Batch 4 | 收口与证据 | R8-TEST-1 汇总、DOC-2/3、版本/CI/文档、性能复测 | 全部 | gate + full 全绿；黄金重生成；版本同步；远端证据（用户推送后） |

### 8.2 Batch 0：先让文档说真话（0.5 人日）

1. 修正 `docs/dev-plan.md:1762` B-1 描述（见 R8-DOC-1）。
2. README 版本改到 2.6（或去掉硬编码）、配置读取顺序改到 D7 口径、launchd 安装补 `mkdir -p work/logs` 与路径替换。
3. 黄金资产数字暂不更新（等 Batch 2 重生成后统一测），避免两次改动。
4. 不触碰代码；提交信息只含文档（是否提交由用户决定，本轮不提交）。

**退出**：文档中不再有“已落地但未实现”的表述；安装步骤可被新手照做。

### 8.3 Batch 1：数据安全链（3–4 人日）

**顺序**

1. **DB7**：新增 `ix_deletions_doc_seq`（DDL 幂等），更新 DB_VERSION；迁移测试 v6→v7。
2. **R8-SVC-1**：request_start、墓碑检查、catch 调整、同步/并行/串行三条路径统一；测试。
3. **R8-OPS-1**：`sqlite_backup` helper（可复用 drill/snapshot）；删除入口快照；fail-closed；保留期；整源一次快照；测试。
4. **R8-OPS-2**：CLI restore；integrity/旧库备份/WAL 清理/原子替换；文档；测试。
5. **R8-OPS-4**：drill 改用 helper；WAL 测试。
6. **R8-OPS-3**：cron 拆分 local/external rotation；保最新；测试。

**关键纪律**

- 先写失败测试（TDD）：删除复活、快照失败、WAL 残留、外置失败轮出；
- 每个测试必须能在旧代码上红、新代码上绿；
- 不在本批次改产物格式（PIPELINE 不变），便于定位；
- `DB_VERSION` 变更不改变 IR/产物。

**退出**

- 本机 gate + full 全绿；
- 新增测试覆盖三条删除路径（单文档、整源、转换期）；
- 恢复 CLI 手工演练通过（沙箱）；
- 报告一次 B 类提醒（B-4/B-10 相关）。

### 8.4 Batch 2：功能完整性（5–6 人日）

**顺序（依赖关系）**

1. **R8-PIPE-2 + R8-PIPE-6**：共享“接受后全 run”提取器与递归遍历（先做，后续 marks/编号复用）。
2. **R8-PIPE-5**：表格网格归一（与 PIPE-2 的表格递归配合）。
3. **R8-PIPE-4**：numbering 解析与 Line 字段。
4. **R8-PIPE-7**：marks 生产（DOCX 三态、PDF 一致性）。
5. **R8-PIPE-1**：CJK 标题正则；最后统一重生成黄金（避免多次漂移）。
6. **R8-PIPE-8**：ir.json 过滤 + report 策略 + 文档。

**黄金策略**

- 先跑现有黄金，记录所有差异（diff 分类：标题、列表、表格、marks、raw）；
- 人工抽查每类差异 ≥10 个样本；
- 重生成 MANIFEST 与统计数字；
- G6：不含新结构的样本必须逐字节不变；含新结构的样本只允许预期差异；
- 黄金测试增加 H1=64、heading=640、无 raw 名断言。

**退出**

- 黄金 diff 分类报告完成；
- 门禁与全量全绿；
- DOCX 结构样本（sdt/numPr/grid/vMerge/br）全部通过；
- 报告一次 B 类提醒（B-13/B-15）。

### 8.5 Batch 3：健壮性与性能（3–4 人日）

1. **R8-LLM-1**：类型校验、空 whitelist 语义、测试。
2. **R8-SVC-2**：E4092、分组匹配、事件、测试。
3. **R8-PERF-1**：映射驱动孤儿清理、计数短路、查询计划测试。
4. **R8-PERF-2**：直接产物路径、配置路径、精确匹配、回归测试；需要调整 `delete_doc` 签名（API 传入路径配置）。
5. **R8-SEC-1**：symlink 跟随 + containment + 计数。
6. **R8-CFG-1**：配置层叠 + 测试。
7. **R8-API-1**：body 类型校验 + 参数化测试。

**性能验收**

- `EXPLAIN QUERY PLAN` 断言：孤儿清理走 rowid/索引，不扫 FTS 内容列；
- 10k 文件目录删除 1 文档：不触发 `rglob` 全树（计数替身）；
- reconcile 在 10k blocks 上的耗时对比（修复前/后，方法见第 10 章）；
- 删除快照耗时单独记录，不与转换主路径混淆。

**退出**

- gate + full 全绿；
- 新增测试含变异验证；
- 报告一次 B 类提醒（B-4、B-6/B-8 性能相关）。

### 8.6 Batch 4：收口（1–2 人日）

1. 版本三处/四处同步：`PIPELINE_VERSION 2.6.0`、`DB_VERSION 7`、`SCHEMA_VERSION 14`、`PLAN_VERSION 3.17`；`test_ch477_version_sync.py` 更新。
2. 文档：dev-plan 第十二章新增 CH-483 行、§1.4 版本表、§9.2 批次、12.7 B 类新增 B-13～B-15；README 版本/配置/安装；`ops/backup.md` 恢复 CLI；V1.0 三处 raw/快照表述。
3. 黄金统计数字最终测量并写入。
4. 本机 gate + full + `doctor`，保存 JUnit 与命令输出。
5. 远端 CI 由用户推送后运行；报告预留 run id 占位（本轮不推送）。
6. 最终 B 类提醒清单与遗留风险。

**退出**

- 所有 A 类 HIGH/MED 关闭；
- B 类只登记且有出口条件；
- 版本一致，黄金可复现；
- 无未提交/未说明的改动。

---

## 9. 测试与验收计划

### 9.1 新增测试矩阵

| 测试文件 | 覆盖 | 红线（gate）建议 |
| --- | --- | --- |
| `tests/test_ch483_pipeline.py` | 标题、sdt、编号、合并单元格、单元格 br/tab、marks、raw | 标题、sdt、编号、marks |
| `tests/test_ch483_service.py` | 转换期删除、同路径陈旧作业、body 类型 | 转换期删除、E4092 |
| `tests/test_ch483_ops.py` | 快照、恢复 CLI、轮出、drill WAL、symlink | 快照 fail-closed、外置轮出、恢复 WAL |
| `tests/test_ch483_docs.py` | README/dev-plan 版本与资产数字 | 版本同步 |
| 既有黄金测试 | H1/heading 计数、raw 过滤、G6 | 黄金全量 |

### 9.2 关键用例（必须逐条落地）

**管线**

1. `第 1 章` / `第 1 章 总则` / `第1章` → H1；长句反例 → 非标题。
2. 黄金：H1=64、heading=640、章标题文本不与正文粘连。
3. 块级 sdt 段落、sdt 表格、行内 sdt、customXml、sdt 分页符、顺序保持。
4. numPr 单级/多级/start/bullet/style；标题不转列表；表格内编号不破坏列。
5. gridSpan 2/2+3、vMerge restart/continue、不规则合并、空表；每行列数一致。
6. 单元格 `A\nB\tC` → IR 保留 `\n`/`\t`；GFM 单行化；多段落仍空格连接。
7. 整段粗体/斜体/删除线 → marks；混合格式 → 无 marks；标题/代码/表格/引用排除。
8. zip 内 `ir.json` 无 raw；`content.md` 无 raw；`report.html` 转义展示（若保留）。

**服务/存储**

9. 转换期删除（单文档/整源）→ rejected/E4041，无复活；删除在请求前 → 允许。
10. 同路径两作业 → 旧 rejected/E4092，新 done；无 H1 docs。
11. 非字符串 op → 拒绝（LlmServiceError 或校验原因），不 500。
12. 非对象 JSON → 400/E4001。
13. 删除快照成功/失败注入/保留期/整源一份。
14. 恢复：加密/明文、错误口令、篡改、无 force、旧库备份、WAL/SHM 清理、完整性失败。
15. cron：外置失败不轮外置、成功才轮、至少保留最新、未配置跳过。
16. drill 未 checkpoint WAL 数据可见。
17. symlink：内部跟随一次、越界跳过计数、循环终止、默认关闭。
18. 配置：home+cwd 层叠、cwd 覆盖 home、显式覆盖两者、未知键、缺失显式报错。
19. reconcile：孤儿清理走 rowid/索引；映射缺失兜底；10k 规模耗时。
20. purge：直接路径、自定义 `paths.out`、不扫全树、精确匹配不误删。

### 9.3 变异验证（每个 HIGH 至少 1 个）

| HIGH | 变异 | 期望 |
| --- | --- | --- |
| R8-PIPE-1 | 把 `_CJK_HEAD` 改回无空格 | 标题测试 + 黄金 H1 断言转红 |
| R8-PIPE-2 | 跳过 sdtContent 递归 | sdt 内容测试转红 |
| R8-SVC-1 | 删除墓碑检查 | 转换期删除测试复活，转红 |
| R8-OPS-1 | 快照失败时继续删除 | fail-closed 测试转红 |
| R8-OPS-2 | 跳过 WAL/SHM 清理 | 恢复后完整性/WAL 测试转红 |
| R8-OPS-3 | 外置失败仍 rotate | 最后副本保留测试转红 |
| R8-DOC-1 | 文档回退旧表述 | 文档状态测试转红（或人工审查清单） |

### 9.4 G6 与分片等价

- **G6**：对不含新结构的 PDF/DOCX 样本，`content.md` 与基线逐字节一致；含新结构的样本按“预期差异清单”逐类审查，不允许“顺手多改”。
- **分片等价**：同一文档 single 与 two_phase 的 blocks/heading 结构一致；新增 marks/编号字段在分片边界不得丢失（特别是跨片列表计数器与 sdt 遍历）。
- **黄金**：重生成后 `doctor`、MANIFEST、zip 可复现性（剥离时间戳）保持。

### 9.5 退出判定

任一 Batch 不满足以下条件不得进入下一批：

```text
1. 本机 gate 全绿（0 skipped）
2. 本机 full 全绿（0 skipped）
3. 新增测试在旧代码上红、新代码上绿（至少 HIGH 项）
4. G6/黄金差异已分类并有人工抽查记录
5. 版本常量与文档同步（Batch 2/4）
6. B 类提醒已发出
```

---

## 10. 性能方案与度量

### 10.1 性能变更清单

| 变更 | 成本/收益 | 预期 | 度量 |
| --- | --- | --- | --- |
| 墓碑检查（D1） | 每次同步作业 +1 次索引查询；删除 +1 个索引 | 可忽略 | EXPLAIN；p95 作业延迟 |
| 删除前快照（D2） | 每次删除 +O(DB) 写 | 安全换延迟；100MB 目标 <2s | 删除 p50/p95；磁盘增量 |
| 恢复 CLI（D3） | 离线操作，不影响在线 | 无 | 恢复耗时/完整性 |
| 外置轮出（D4） | 无额外开销 | 安全 | 轮出计数 |
| DOCX 按需全 run（D5） | 普通文档走快路径；含 sdt/链接/修订才全 run | 普通 DOCX 不退化 | 1000 段落 DOCX 耗时 |
| 编号解析（D5） | 每文档一次 numbering.xml + O(段落) | 可忽略 | 解析耗时 |
| 表格网格（D5） | 每表线性 | 可忽略 | 大表（1000 行）耗时 |
| marks（D6） | 复用已有 run/行标志，无额外遍历 | 可忽略 | 转换耗时 |
| 噪声指标（D8） | 复用已算 letters，无额外遍历 | 可忽略 | 门禁耗时 |
| 孤儿清理（PERF-1） | 从全表 FTS 扫描改为索引+rowid | 启动/日流程显著下降 | EXPLAIN；10k blocks 耗时 |
| 删除产物（PERF-2） | 从 3 棵全树 rglob 改为直接路径 | 删除延迟与目录规模解耦 | 10k 文件删除耗时；rglob 调用计数 |
| drill 快照（OPS-4） | 与 copy2 同量级，正确性提升 | 无退化 | drill 耗时 |
| symlink realpath（SEC-1） | 仅开启时逐链接 | 可忽略 | 遍历耗时 |
| 配置层叠（CFG-1） | 启动时多读 0–1 个 TOML | 可忽略 | 启动耗时 |

### 10.2 度量口径

- **转换主路径**：不得用删除/恢复测试的耗时代表转换性能；分别记录。
- **对账**：在合成库（10k blocks、1k docs）上测 `reconcile_search`；记录 `EXPLAIN QUERY PLAN` 与墙钟；修复前后同机同数据。
- **删除**：在 10k 文件目录中删除 1 个文档；记录耗时与 `rglob`/`scandir` 调用次数（monkeypatch 计数）。
- **DOCX**：用 1000 段落普通文档 + 含 sdt/编号/合并表的文档，记录 S1/S3 耗时；与基线同机对比。
- **快照**：100MB/1GB 库的 snapshot 耗时与磁盘增量；验证保留期上限。
- **黄金**：重生成耗时与文件数/字节数，写入 dev-plan 第十二章。

### 10.3 性能优先的边界

- 不在转换主路径引入额外全量遍历；所有新解析尽量“一次遍历、按需分支”。
- 删除/启动对账属于低频但随规模恶化的路径，优先修复；快照的 O(DB) 写是数据安全的必要成本，用“删除低频 + fail-closed”换取正确性。
- 不为性能牺牲已经验证的安全边界（例如不能为了少一次查询而取消墓碑检查）。

---

## 11. 版本、数据库迁移与契约治理

### 11.1 版本裁定

| 常量 | 旧值 | 新值 | 原因 |
| --- | --- | --- | --- |
| `PIPELINE_VERSION` | 2.5.0 | **2.6.0** | 标题/DOCX 结构/marks/raw 过滤改变产物字节与结构 |
| `DB_VERSION` | 6 | **7** | 新增 `deletions(doc_id, id)` 索引；DDL 幂等，旧库开库自动补齐 |
| `SCHEMA_VERSION` | 14 | **14** | IR 字段未增删；`Line` 新字段仅管线内部 |
| `PLAN_VERSION` | 3.16 | **3.17** | 方案契约/批次/版本表更新 |

### 11.2 迁移与兼容

- `_migrate` 的 DDL 每次开库幂等执行（CH-476 起），新索引对存量库自动生效；`PRAGMA user_version` 升到 7。
- 旧库打开后：`deletions` 表无索引 → 自动 `CREATE INDEX IF NOT EXISTS`；无需数据回填。
- `search_map` 已在 v6 回填；PERF-1 不改变表结构，只改查询。
- 备份/恢复：旧版本备份容器 v1/v2 仍可被新 `restore` 读取；新版本生成的容器保持 v2。
- CLI 向后兼容：新增 `backup restore`，不改变既有子命令参数；`--force` 为新参数。
- 错误码：新增 `E4092`（409）；放入 `CODE_INDEX` 与错误码表；E4041 语义不变。

### 11.3 黄金与契约

- 黄金样本重生成后，`tests/golden/MANIFEST`、大小/哈希、`make_golden.py` 自检同步；
- `test_ch477_version_sync.py` 的三处 `PLAN_VERSION` 断言、SCHEMA/DB 断言必须更新并通过；
- `doctor` 输出应显示新版本；
- README 的版本、配置读取顺序、恢复流程与实现一致；
- 方案 V1.0 的 FR-127/约定 14 与 FR-128 快照表述按 D11/D2 修订，避免再次“文档承诺 > 实现”。

---

## 12. 文档同步清单

| 文档/位置 | 修改 |
| --- | --- |
| `docs/dev-plan.md:1762` | B-1 改为真实状态（Batch 0）；实现后更新为“已落地 + 待语料” |
| `docs/dev-plan.md:705`、V1.0:427/480 | 删除前快照改为“已实现（整库快照，fail-closed，14 天）” |
| `docs/dev-plan.md:692/694` | 黄金资产统计按最终重生成结果更新 |
| `docs/dev-plan.md` §1.4/§9.2/§12 | 新增 CH-483 批次、版本、测试与证据 |
| `docs/dev-plan.md` 12.7 | 新增 B-13/B-14/B-15；更新 B-1 状态 |
| `docs/Doc2md项目开发方案V1.0.md:134/321/241` | raw 名口径改为“content.md/ir.json 不含；report.html 转义展示” |
| `docs/Doc2md项目开发方案V1.0.md:427/480` | 快照与恢复语义更新 |
| `README.md:3` | 版本 2.6（或去硬编码） |
| `README.md:51` | 配置层叠顺序（D7） |
| `README.md:94-116` | launchd 安装：路径替换、`mkdir -p work/logs`、plutil 校验 |
| `ops/backup.md:51-58` | 改为 `doc2md backup restore` 序列；停服前置；旧库备份；WAL 清理 |
| `doc2md/ops/backup.py` docstring | 恢复安全序列说明 |
| `doc2md/llm.py` docstring / 错误码表 | 非字符串 op 拒绝；E4092 登记 |
| `doc2md/pipeline/s1_5_gate.py` docstring | 噪声判据实现后更新 |
| `doc2md/pipeline/convert.py:234` | raw 口径修正（D11） |
| `README`/`dev-plan` 远端证据 | 新 CI run id 由用户推送后回填，勿提前编造 |

---

## 13. B 类登记（需本机以外环境或人工验证）

> **提醒机制**：每完成一个 Batch，必须在交付说明中重列当前未闭环的 B 项；本报告结尾也会提醒。以下只登记，不实施。

### 13.1 既有 B 类（沿第七轮，编号不改）

| # | 项 | 状态/出口条件 | 优先级 |
| --- | --- | --- | --- |
| B-1 | 真实 ToUnicode 损坏/高密度乱码 PDF 语料，验证噪声判据误杀面 | 实现 R8-PIPE-3 后用真实语料评估；不得用合成样本替代 | HIGH（数据质量） |
| B-2 | 真实双栏/页眉带链接 PDF 语料 | geoms 扩为横向重叠会改变产物字节；需语料评估 | MED |
| B-3 | 真实注入语料 + `_attribute` 归因去循环 | 词表已扩，循环归因未改 | MED |
| B-4 | 并发压测（同步 serve + 常驻 worker + 并发删除） | R8-SVC-1 的墓碑守护需多进程压测复现 | HIGH（数据安全） |
| B-5 | macOS 真机 launchd 实跑 cron（serve/work 见 B-9） | **cron 部分已实机验证（2026-10-18，本机 arm64）**：`plutil -lint` 四档全绿；临时 label `bootstrap`+`kickstart` 拉起日任务 exit 0、stderr 空，`WorkingDirectory`+`--root` 生效、产物与 `launchd-daily.{out,err}` 落在指定根；对照实验证实 CH-475 堵住「相对根名解析成 `/work`」的失败面。serve/work 常驻运行时（自启/崩溃自愈/unload 不复活）已于 2026-10-18 于 B-9 本机 macOS 验证，仅 Windows 腿待验 | **HIGH→已验 cron** |
| B-6 | Win11 spawn 平台池策略实测 | T6 数字全部来自 Linux fork | MED |
| B-7 | CI 增 Windows 腿 | 现三档均 ubuntu-latest | MED |
| B-8 | 跨机性能基准（低配 Win11 vs 本机） | 12.3 数字为本机口径 | MED |
| B-9 | serve/work 常驻资产实机“开机自启 + 崩溃自恢复” | **macOS 半侧已实机验证（2026-10-18，本机 arm64，临时唯一 label + bootstrap/kickstart）**：serve 随 RunAtLoad 自起绑 `127.0.0.1`（curl 200、约 10s 冷启延迟）、`kill -9` 即时重启重绑；work 常驻自起、`kill -9` 后 +22s 自愈（落在 `ThrottleInterval=30` 窗口，实证风暴护栏）；`bootout` 后二者均不复活；`work --once` 排空 exit 0 后连续 poll 恒「not running/exit 0」不重启，证实 `KeepAlive.SuccessfulExit=false` 可停性。全程沙箱 + 临时 label，未写 `~/Library/LaunchAgents`，跑毕清扫。**未证**：Windows `Register-ScheduledTask` 四档定义 + `.ps1` 真机执行（本机无 `pwsh`），并入 Windows 腿 | **HIGH→已验 macOS 半侧** |
| B-10 | 备份容器 v2 外置卷真机恢复演练 | 沙箱闭环已覆盖；外置实景恢复未做；R8-OPS-2 后需重跑 | **HIGH（数据安全）** |
| B-11 | 编辑器/审阅页真实浏览器人工走查 | 仅页面契约与单测 | MED |
| B-12 | >30min 合法千页转换与 `reclaim_stale` 双进程实测 | 张力未验证 | MED |
| R7-OPS-3 | CLI 权限口径分裂（LOW） | 需真实多用户/远程部署判定；当前单机自用立场 | LOW（决策敏感） |

### 13.2 本轮新增 B 类

| # | 项 | 为什么本机不能完成 | 出口条件 |
| --- | --- | --- | --- |
| B-13 | 真实 Word 模板语料（sdt、编号样式、合并单元格、分节/分栏、页眉页脚） | 需要多种真实 Word 文件与人工核对；合成样本覆盖不了样式联动 | R8-PIPE-2/4/5/6 实现后，用 ≥20 份真实模板抽查抽取完整率与列对齐 |
| B-14 | 跨文件系统符号链接/权限语义（SMB、exFAT、Windows junction、macOS 别名） | 本机 APFS 无法代表目标机文件系统 | R8-SEC-1 实现后，在至少两种目标文件系统验证跟随/越界/循环/权限 |
| B-15 | 真实中文 PDF 标题语料（`第 N 章` 空格变体、目录页、正文引用） | 需要大量真实版式评估误报/漏报 | R8-PIPE-1 实现后，统计 H1 准确率与误报样本；据此微调正则 |

### 13.3 本轮结束时的提醒

**请优先安排**：

1. **B-5 / B-9**：cron 日任务与 serve/work 常驻运行时（自启/崩溃自愈/`bootout` 不复活/`--once` 正常退出不重启）均已于 2026-10-18 本机 macOS launchd 实机验证；唯余 Windows 腿（`Register-ScheduledTask` + `.ps1` 真机）待验。
2. **B-10**：外置卷备份恢复演练（在 R8-OPS-2 落地后执行）。
3. **B-4**：并发删除/转换压测（验证 R8-SVC-1 的事务守护）。
4. **B-1/B-13/B-15**：真实语料评估（噪声、Word 模板、中文标题）。

其余 B 项按第 8 章批次退出时逐批提醒。

---

## 14. 风险、回滚与不实施声明

### 14.1 实施风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 黄金重生成掩盖真实回归 | 产物质量下降但测试绿 | 差异分类 + 人工抽查 ≥10/类；G6 仅允许预期差异 |
| DOCX 递归遍历改变顺序/重复 | 内容重复或错序 | 顺序测试、sdt 只进一次、嵌套表格边界固定 |
| 编号解析误判 | 列表/标题混淆 | 标题优先、表格内不平铺、真实模板 B-13 |
| marks 过度加粗 | Markdown 语义错误 | 块内一致才写；标题/代码/表格排除；抽查 |
| 删除快照磁盘膨胀 | 磁盘压力 | 保留期 + 最新一份 + fail-closed 前磁盘守卫 |
| 恢复 CLI 误覆盖 | 二次事故 | 默认拒绝已存在、旧库备份、完整性前后校验、文档停服 |
| 配置层叠行为变化 | 现有双文件用户配置改变 | 文档醒目说明；单文件用户不变 |
| DB7 迁移 | 旧库开库失败 | DDL 幂等 + v6→v7 迁移测试 + 备份 |
| 性能修复改变语义 | 孤儿清理遗漏 | 映射+兜底计数；测试覆盖映射缺失 |

### 14.2 回滚策略

- 每个 Batch 独立提交（由用户决定是否提交）；出现回归可按 Batch 回滚，不牵连其他批次。
- DB7 只加索引，不改表/列；回滚代码到 DB6 时，多出的索引不影响旧代码（SQLite 忽略未知索引）。
- 黄金样本重生成放在 Batch 2 末尾；若回滚 Batch 2，同时回滚黄金与 PIPELINE_VERSION。
- 恢复 CLI/快照为新增能力，回滚不影响既有备份格式。
- 轮出策略回滚会导致旧副本被删的风险回归；不建议单独回滚 D4。

### 14.3 不实施声明

本报告**没有**：

- 修改任何 `.py`、`.toml`、`.plist`、`.ps1`、测试或黄金资产；
- 执行删除、恢复、备份轮出、launchd 加载等有副作用的操作；
- 提交、推送、创建分支/PR；
- 对 B 类事项做本机替代验证并宣称通过。

所有“修复方案”均为待实施设计；实施需用户明确授权后进行，并按第 8 章顺序执行。

---

## 附录 A：探针记录

### A.1 测试基线

```text
$ .venv/bin/pytest -m gate -q --junitxml=/tmp/doc2md_r8_gate.xml
tests=285 failures=0 errors=0 skipped=0 time=38.422s exit=0

$ .venv/bin/pytest -q --junitxml=/tmp/doc2md_r8_junit.xml
tests=852 failures=0 errors=0 skipped=0 time=85.037s exit=0
```

### A.2 中文标题

```text
"第 1 章 总则" -> None
"第1章 总则"  -> 1
"第 1 章"     -> None
"1. 安装依赖" -> 1
"1.1 范围"    -> 2
```

黄金样本：

```text
S1 chapter lines: 64
S3/convert: blocks=5696, headings=576, H1=0
64 chapter lines all type=paragraph
示例合并：第 2 章此项适用于...
```

### A.3 DOCX 结构

```text
块级 <w:sdt><w:sdtContent><w:p>内容控件内正文</w:p></w:sdtContent></w:sdt>
read_docx -> 该段落不存在

单元格段落 "A\nB\tC"（w:br + w:tab）
_docx_cell_text -> "ABC"

一行 gridSpan=2
_docx_table_rows -> 1 个 cell（未展开网格）
```

### A.4 LLM 非字符串 op

```text
OpValidator([...]).apply([{"op": ["split_block"]}])
-> TypeError: cannot use 'list' as a set element (unhashable type: 'list')
```

### A.5 符号链接

```text
root/real/a.pdf + root/link -> root/real
walk_limits(follow_symlinks=True)
files = [root/real/a.pdf]
skipped_symlinks = 0
followed_symlinks 字段不存在
```

### A.6 黄金资产

```text
文件数: 5249
git 跟踪: 5142
MANIFEST 行数: 5140
du: 23640 KB
总字节: 2211232
```

> 这些数字会在 Batch 2 重生成黄金后变化；最终以 Batch 4 的测量为准。

---

## 附录 B：代码位置索引

| 主题 | 文件:行 |
| --- | --- |
| CJK 标题正则 | `doc2md/pipeline/s3_structure.py:30` |
| 标题层级判定 | `doc2md/pipeline/s3_structure.py:495-530` |
| 黄金章标题生成 | `tests/make_golden.py:103/106/111` |
| 黄金自检正则 | `tests/make_golden.py:1348` |
| DOCX 正文遍历 | `doc2md/pipeline/s1_parse.py:582-617`（循环在 600-611） |
| 段落文本/格式 | `doc2md/pipeline/s1_parse.py:394-445`（415/417-418/426/432） |
| 全 run 提取 | `doc2md/pipeline/s1_parse.py:454-461` |
| 单元格文本 | `doc2md/pipeline/s1_parse.py:554-564` |
| 表格行 | `doc2md/pipeline/s1_parse.py:567-578` |
| 渲染 marks | `doc2md/pipeline/s4_render.py:80-85` |
| 打包/raw | `doc2md/pipeline/convert.py:232-252`（234/244/246-247）、`:290` |
| IR raw 字段 | `doc2md/ir/model.py:5/92/107/117/124` |
| LLM op 校验 | `doc2md/llm.py:163/182/463` |
| S1.5 指标/判定 | `doc2md/pipeline/s1_5_gate.py:89-107/197-230` |
| 同步作业落库 | `doc2md/api.py:293-340`、`:387`、`:397/417` |
| 删除端点 | `doc2md/api.py:522-528`、`:530+` |
| 删除级联 | `doc2md/storage/repo.py:323-379` |
| 删除产物扫描 | `doc2md/storage/repo.py:428-448` |
| 搜索对账 | `doc2md/storage/repo.py:164-198` |
| deletions 表 | `doc2md/storage/db.py:73-78` |
| search_map | `doc2md/storage/db.py:135-141` |
| worker 分组/落库 | `doc2md/worker.py:132-165/219-261` |
| 备份快照 | `doc2md/ops/backup.py:191-216` |
| 备份恢复 | `doc2md/ops/backup.py:287-307` |
| 备份轮出 | `doc2md/ops/backup.py:309-343` |
| drill 复制 | `doc2md/ops/drill.py:137` |
| 符号链接遍历 | `doc2md/security/dirguard.py:25-35/89-108` |
| 配置加载 | `doc2md/config/loader.py:166-188` |
| CLI 子命令 | `doc2md/cli.py:77-148` |
| cron 备份/轮出 | `doc2md/cron.py:139-183` |
| 恢复文档 | `ops/backup.md:51-58` |
| 删除前快照承诺 | `docs/dev-plan.md:705`、V1.0:427/480 |
| B-1 错误声明 | `docs/dev-plan.md:1762` |
| README 版本/配置/安装 | `README.md:3/51/94-116` |
| 黄金资产统计 | `docs/dev-plan.md:692/694` |

---

## 附录 C：契约增量摘要（供实施时同步文档）

### C.1 新错误码

| 码 | HTTP | 语义 | 触发 |
| --- | --- | --- | --- |
| `E4092` | 409 | source superseded | 同路径旧作业被新内容替代；`detail` 带 enqueued/consumed doc_id |

### C.2 CLI 增量

```text
doc2md backup restore SRC [--to PATH] [--force] [--passphrase-env ENV]
```

- 默认目标：`$ROOT/state.sqlite`；
- 口令：`DOC2MD_BACKUP_PASSPHRASE`；
- 退出码：0 成功，2 参数/拒绝，4 完整性/恢复失败；
- 前置：停止 serve/work；目标存在需 `--force`；强制时先备份旧库。

### C.3 数据/文件增量

| 项 | 说明 |
| --- | --- |
| `deletions(doc_id, id)` 索引 | DB_VERSION 7；幂等 DDL |
| `predelete-<ts>-<rand>.sqlite` | 删除前整库快照；保留期同 `ops.backup_rotation_days`，至少保留最新一份 |
| `pre-restore-<ts>.sqlite` | 恢复前旧库备份 |
| `external_rotation` 报告字段 | `removed/skipped/reason`；外置失败时不轮出 |
| zip 内 `ir.json` | 不含 `source_name_raw` |
| zip 内 `report.html` | 可含转义后的 raw（D11） |

### C.4 新增/变更测试锚点

```text
tests/test_ch483_pipeline.py
tests/test_ch483_service.py
tests/test_ch483_ops.py
tests/test_ch483_docs.py
tests/test_ch477_version_sync.py（更新版本）
tests/test_golden*.py（H1/heading/raw 断言）
```

---

## 结语

本轮的 7 项 HIGH 中，有 4 项集中在删除/备份/恢复链路，说明“数据安全承诺”已经从设计问题转化为实现与运维入口问题；另外 3 项分别指向中文标题、DOCX 内容控件和文档失真。建议严格按 Batch 0→1→2→3→4 推进：先把“删除不会复活、恢复有安全入口、轮出不会删最后副本”做实，再做 DOCX 功能补全，最后处理性能与文档收口。

**B 类提醒**：B-10（外置卷恢复演练）、B-4（并发删除/转换压测）、B-1/B-13/B-15（真实语料）需要本机以外环境或人工参与，请优先安排；**B-5（cron 日任务）与 B-9（serve/work 常驻运行时：自启/崩溃自愈/`bootout` 不复活/`--once` 正常退出不重启）已于 2026-10-18 本机 macOS launchd 实机验证，唯余 Windows 腿（`Register-ScheduledTask` + `.ps1` 真机执行，本机无 `pwsh`）待验，并入 B-6/B-7**。其余 B 项在每批完成后继续提醒。
