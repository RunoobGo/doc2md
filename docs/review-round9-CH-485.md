# Doc2md 第九轮全面审查报告与优化方案

> 审查编号：CH-485（下一变更号；`git log` 与 README §跟踪约定一致）
> 审查日期：2026-10-18
> 审查基线：`555b7f7c5233148de72d9c2dfb89f2bc7701bd65`（工作树干净；HEAD == origin/main）
> 审查性质：**只读审查**。除本报告外，没有修改任何代码、测试、配置、plist、ps1 或黄金资产；没有提交、推送、创建分支或 PR。
> 适用原则：功能全面优先、性能优先；需要我决策的项目由本报告按该原则直接裁定（D1–D10）；需要本机以外环境或人工验证的事项只登记、不实施（第 12 章），并在交付时提醒。
> 与上一轮的关系：第八轮 `docs/review-round8-CH-483.md` 的 25 项 A 类已全部落地，CH-484 已闭环；本轮不重复已关闭项，只报告新路径、修复残差或契约漂移。
> **实施状态（2026-10-18 补记）**：本报告最初为只读审查；用户随后指令「逐项完成所有问题修复」，B0a–B4 已按第 8 章全部落地。最终本机 gate **339 passed / 0 skipped（34.63s）**、全量 **920 passed / 0 skipped（71.69s）**、`ruff` 全绿、`doctor` 退 0（`pipeline 2.7.0`）。版本：PIPELINE **2.7.0**、DB **8**、SCHEMA 14、PLAN **3.19**。实现提交与远端 CI run id 见 `docs/dev-plan.md` 第十一章 CH-485 行/第十二章。B 类仍只登记，见第 12 章。

---

## 0. 阅读指引

| 章节 | 内容 | 读者 |
| --- | --- | --- |
| 第 1 章 | 执行摘要、基线、优先顺序 | 决策者 |
| 第 2 章 | 范围、方法、分级口径 | 全体 |
| 第 3 章 | 30 项发现总表 | 全体 |
| 第 4 章 | 11 项 HIGH 的逐项证据与修复 | 开发 |
| 第 5 章 | 15 项 MED 的逐项证据与修复 | 开发 |
| 第 6 章 | LOW 与性能机会 | 开发 |
| 第 7 章 | 决策裁定 D1–D10 | 决策者 + 开发 |
| 第 8 章 | 分批实施计划与退出条件 | 开发 |
| 第 9 章 | 性能方案与度量 | 开发 |
| 第 10 章 | 版本、DB、黄金与契约 | 全体 |
| 第 11 章 | 文档同步清单 | 文档 |
| 第 12 章 | B 类登记与提醒 | 决策者 |
| 第 13 章 | 风险、回滚与不实施声明 | 全体 |
| 附录 A–C | 探针记录、代码索引、测试缺口 | 开发 |

---

## 1. 执行摘要

### 1.1 总体结论

本轮在 `555b7f7` 基线上复跑本机 harness，并完成设计、代码、文档、测试四面的只读复核，共确认 **30 项发现：11 项 HIGH、15 项 MED、4 项 LOW/性能机会**。整体判断：

1. **数据安全的“第二层”仍有缺口**：第八轮堵住了同步转换期删除复活、外置备份失败轮出、恢复 CLI 等；本轮发现 **异步 worker 在内容漂移窗口仍可让已删文档复活**（R9-SVC-1），以及 **删除前快照的生命周期没有闭环**（R9-OPS-1：cron 不轮出 `predelete-*`、最新一份永久保留、无磁盘门禁、无份数上限）。两者都直接触碰 FR-128「删除即删除、已删数据 ≤14 天从全部副本轮出」。
2. **交付物与库面不是同一事实源**：PATCH 编辑只改库不改 zip，`GET /docs/<id>/markdown` 继续导出旧内容；重新转换遇到 E4091 时 zip 已被未编辑版本覆盖，库却保留编辑；`package()` 直接写最终 zip、无 `.part`/rename。恢复数据库后又没有 `out/*.zip`，导出 404。这是本轮最值得一次性收口的架构问题（R9-SVC-2 / R9-OPS-2）。
3. **备份/恢复链路的并发与兼容性仍未封死**：4 线程并发 `to_external` 实测产出 `InvalidTag` 容器；旧版 v1 容器 `backup restore` 直接失败（`decrypt_bytes` 支持，但 CLI 路径只走 `decrypt_stream`）；`pre-restore-*` 既不轮出也不 chmod。备份是最后防线，这些必须按 HIGH 处理（R9-OPS-2/5）。
4. **Windows 主路径存在一个启动即崩的缺陷**：`netguard._install` 无条件读取 `socket.socket.sendmsg`，该原语在 CPython 仅 Unix 可用；Windows 默认 `run.offline_assert=true`，每次 `convert` 都会在装桩阶段抛 `AttributeError`。本机无法执行 Windows 验证，登记 B-6/B-7；修复本身可在本机用模拟缺失属性的测试完成（R9-OPS-3）。
5. **管线结构在常见文档形态上仍会“内容在、结构错”**：10.5pt 正文的单级编号短行被 `modal_size` 的四舍五入抬成 H1/H2；DOCX 表格首列的 `1.1 范围` 被升成标题、整表消失；Word 自动编号的 bullet/多级/标题编号渲染错误；文本框内容静默丢失。这些不是边角格式，而是 FR-101/104 的直接违约（R9-PIPE-1…4）。
6. **性能主路径没有新的退化，但存在可安全摘取的收益**：320 页转换 10.98s（无 profiler）中 pdfplumber 占约 77%；自有 S1 的 `_line_style` 单函数实测 0.661s，可无输出差异地降到 0.357s（1.85×）；`save_ir` 把 JSON 序列化与 `cjk_aux` 放在 `BEGIN IMMEDIATE` 内（5,789 块实测 0.41s），大文档会延长写锁。搜索的 8000 字索引窗口使尾部词永久不可检索，且“零命中 LIKE 兜底”会全扫 FTS，属于功能 + 性能双问题（R9-SVC-4/6、R9-PERF-1）。

### 1.2 本机基线（实测）

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| `pytest -m gate` | **311 passed / 0 failed / 0 error / 0 skipped，34.853s** | `.venv/bin/python -m pytest -m gate -q --junitxml=/tmp/doc2md_r9_gate.xml`，exit 0 |
| 全量 `pytest` | **886 passed / 0 failed / 0 error / 0 skipped，77.089s** | `--junitxml=/tmp/doc2md_r9_junit.xml`，exit 0 |
| `ruff check doc2md tests config ops` | All checks passed | exit 0 |
| `doc2md doctor` | exit 0；`pipeline 2.6.0` | 本机 `.venv` |
| 包规模 | 39 个 `.py` / 10,178 行 | `find doc2md -name '*.py'` |
| 测试规模 | 40 个顶层 `.py` / 15,763 行 | `find tests -maxdepth 1 -name '*.py'` |
| 黄金资产 | git 跟踪 5,142；MANIFEST 5,140 条；磁盘 23,640KB | `git ls-files` / `json.load(MANIFEST)` / `du` |
| 仓库跟踪文件 | 5,256 | `git ls-files \| wc -l` |
| 版本常量 | PIPELINE 2.6.0 / SCHEMA 14 / DB 7 / PLAN 3.18 | `doc2md/__init__.py`、`storage/db.py` |

> 说明：`tests/golden/MANIFEST.json` 是 pretty-print JSON，`wc -l` 的 35,981 行不是条目数；条目数以 `json.load` 的列表长度 5,140 为准。README / dev-plan 中的旧数字见 R9-DOC-1。

### 1.3 最优先的 6 件事（按风险）

1. **R9-SVC-1**：异步 drift × 删除的事务内墓碑检查（防复活）。
2. **R9-OPS-1 / R9-OPS-5**：删前快照的轮出/上限/磁盘门禁 + 备份并发锁与原子写（防“最后一份备份坏掉/被删”）。
3. **R9-SVC-2 / R9-OPS-2**：产物与库面统一事实源；zip 原子发布；编辑后导出与恢复后导出可用。
4. **R9-OPS-2**：v1 旧容器恢复兼容 + 恢复失败语义/pre-restore 生命周期。
5. **R9-OPS-3 / R9-OPS-4**：Windows offline_assert 主路径 + CLI `--root` 优先级（后者可导致 restore 覆盖错库）。
6. **R9-PIPE-1…4**：DOCX 文本框 / 10.5pt 标题 / 自动编号 / 表格首列四组结构修复，随后一次性更新黄金与 `PIPELINE_VERSION`。

### 1.4 本轮没有做什么

- 没有修改任何 `.py`、`.toml`、`.plist`、`.ps1`、测试或黄金资产（本报告是唯一新增文件）。
- 没有执行删除、恢复、备份轮出、launchd 加载等有副作用的操作；所有探针在 `/tmp` 沙箱内完成。
- 没有提交、推送、创建分支或 PR。
- Windows / 真实 Word 模板 / 外置卷 / 真实语料 / 浏览器交互等 B 类事项只登记，见第 12 章。

---

## 2. 审查范围、方法与分级

### 2.1 范围

| 面 | 覆盖 |
| --- | --- |
| 设计/需求 | `docs/Doc2md项目开发方案V1.0.md`、`docs/dev-plan.md`、`README.md`、`ops/backup.md`、`ops/llm.md`、launchd / Windows 资产 |
| 管线 | `pipeline/s1_parse.py`、`s1_5_gate.py`、`sharding.py`、`s3_structure.py`、`s4_render.py`、`convert.py`、`batch.py`、`metrics.py` |
| 服务/存储 | `api.py`、`worker.py`、`scheduler.py`、`auth.py`、`edits.py`、`storage/db.py`、`repo.py`、`fts.py` |
| 运维/安全/配置 | `ops/backup.py`、`drill.py`、`logging.py`、`diskguard.py`、`cron.py`、`cli.py`、`security/*`、`config/*` |
| 测试 | `tests/` 全量、CH-483/484 新增用例、黄金与门禁标记 |

### 2.2 方法

1. **只读代码核对**：逐文件通读，结合调用路径、事务边界、异常路径和配置口径确认问题。
2. **隔离探针**：在 `/tmp` 构造最小 PDF/DOCX/SQLite/容器，复现 HIGH 与关键 MED；本报告附录 A 记录输出。
3. **基线复跑**：gate/full/ruff/doctor 本机各一次，用 JUnit XML 取精确计数（不用进度点计数）。
4. **文档反查**：以 FR/NFR、登记行、README、ops 文档为锚，核对“声称已实现/已同步”与代码事实。
5. **历史去重**：CH-476…CH-484 已关闭项不重复；只有出现回归、修复残差或新触发路径才登记。

### 2.3 分级

| 级别 | 定义 |
| --- | --- |
| HIGH | 数据复活/丢失/不可恢复、备份或恢复承诺不成立、交付物与库面不一致、核心文档结构静默丢失、Windows 主路径不可用 |
| MED | 功能不完整或随规模显著退化但有绕行；文档与实现明确冲突；隐私/权限口径不一致 |
| LOW | 边界输入、运维提示、文档陈旧、可安全摘取的性能微优化 |

处置：A = 本机可实现/可验证；B = 需真实语料、Windows、外置卷或人工；C = 需产品取舍，由本报告按原则裁定（D1–D10）。

---

## 3. 发现总表

| 编号 | 级别 | 主题 | 主要位置 |
| --- | --- | --- | --- |
| R9-PIPE-1 | HIGH | DOCX 文本框内容静默丢失；AlternateContent 可重复 | `s1_parse.py:529/550/603/780` |
| R9-PIPE-2 | HIGH | 10.5pt 正文短编号行被抬成 H1/H2；CJK 章标记后接正文误判 | `s3_structure.py:514/529` |
| R9-PIPE-3 | HIGH | DOCX 自动编号：bullet→有序 “1.”、多级 marker 退化为 bullet、标题编号丢失 | `s1_parse.py:476`、`s3_structure.py:396`、`s4_render.py:61` |
| R9-PIPE-4 | HIGH | DOCX 表格首列编号行被升为标题，整表消失 | `s3_structure.py:374-386` |
| R9-PIPE-5 | MED | 块级 marks 增量合并错误；PDF 以 60% 字符多数派冒充“整段统一” | `s3_structure.py:187-212`、`s1_parse.py:292` |
| R9-PIPE-6 | MED | 纯数字表格页被 auto 门禁判为噪声整篇拒收 | `s1_5_gate.py:23/92/216` |
| R9-PIPE-7 | MED | PDF 多行段落首行链接包住整段 | `s3_structure.py:174`、`s4_render.py:82` |
| R9-PIPE-8 | MED | 跨片 list_item 被缝合，列表边界丢失 | `sharding.py:73/76/95` |
| R9-PIPE-9 | MED | batch 未预期异常把 `str(exc)` 写入任务明细，可能夹带正文 | `batch.py:80-82`、`worker.py:235` |
| R9-SVC-1 | HIGH | 异步 drift 消费绕过删除墓碑，已删文档复活 | `worker.py:242-285`、`scheduler.py:62-90` |
| R9-SVC-2 | HIGH | 产物与库面双写不一致：编辑后导出旧内容、E4091 覆盖 zip、非原子发布、重复内容竞争 | `edits.py:57-93`、`convert.py:249`、`api.py:329-370`、`worker.py:277` |
| R9-SVC-3 | MED | 幂等键复用可取消在途作业；终态命中回报 queued；异步 force 被忽略；work 主循环无重试 | `scheduler.py:62-102/105`、`api.py:310`、`worker.py:449` |
| R9-SVC-4 | MED | 搜索只索引/兜底 8000 字窗口，尾部词不可检索；零命中 LIKE 全扫 FTS | `repo.py:17/283-338` |
| R9-SVC-5 | MED | `paths.out` 只在删除/worker 生效，API 同步、导出、watch 硬编码 root/out | `api.py:422/452/522`、`cli.py:512` |
| R9-SVC-6 | MED | `save_ir` 的 JSON 序列化与 `cjk_aux` 在写锁内，放大阻塞 | `repo.py:66-131` |
| R9-OPS-1 | HIGH | 删前快照：无磁盘门禁/份数上限、cron 不轮出、最新份永久保留、默认 0644、404 也快照 | `backup.py:42/233-272`、`cron.py:186`、`api.py:81-103` |
| R9-OPS-2 | HIGH | v1 历史容器无法通过 `backup restore`；恢复失败语义/pre-restore 生命周期不成立 | `backup.py:108-178/365-418`、`ops/backup.md:55-56` |
| R9-OPS-3 | HIGH | Windows `socket.socket.sendmsg` 缺失导致 offline_assert 每次 convert 失败 | `netguard.py:143-158` |
| R9-OPS-4 | HIGH | CLI `--root/--config` 放在子子命令前被 None 覆盖；restore 可覆盖错库 | `cli.py:66-110/281-315` |
| R9-OPS-5 | HIGH | 并发 cron/外置备份无锁、固定 `.part`/最终名；实测产出 InvalidTag；keep_newest 反保护坏副本 | `backup.py:219-340/421-470`、`cron.py:139-190` |
| R9-OPS-6 | MED | cron weekly 外置段只捕 `Doc2mdError`，PermissionError/OSError 中止后续步骤且不留报告 | `cron.py:144-190` |
| R9-OPS-7 | MED | 配置文档/实现矛盾；未知键仅 warning；doctor 不暴露外置备份配置 | `config/loader.py:1-3/166-205`、`cli.py:588-591` |
| R9-OPS-8 | MED | CLI convert/work/watch 不 chmod work 700；纯 CLI 使用下正文目录可能 755 | `cli.py:191/328-476` |
| R9-OPS-9 | LOW | `drill` 为取 7 字节魔数 `read_bytes()` 整个容器，10GB 级 OOM | `drill.py:165` |
| R9-OPS-10 | LOW | `watch.stable_seconds=0` 自旋；example.toml 注释错位；磁盘公式文档 max/实现不一致 | `cli.py:483`、`config/doc2md.example.toml:52`、`dev-plan.md:573` |
| R9-DOC-1 | MED | 计数/编号/CI 证据漂移：README 仍 285、dev-plan 仍 852、§11 标题止于 CH-482、12.10 缺 v3.18 | `README.md:219-220`、`dev-plan.md:18/654-655/1359/1816` |
| R9-DOC-2 | MED | `pyproject.toml` 2.5.0 与 `PIPELINE_VERSION` 2.6.0 不一致 | `pyproject.toml:7` |
| R9-DOC-3 | MED | 契约文档缺 E4092 / backup restore；v1 可恢复、symlink 不跟随、恢复失败“目标不变”均与实现冲突 | `dev-plan.md` 附录 B/D、`README.md:27`、`ops/backup.md:55-56` |
| R9-SVC-7 | LOW | 异步目录投递在扫描后静默剔除消失文件 | `api.py:310-316` |
| R9-PERF-1 | LOW | `_line_style` 五趟扫描 + 未缓存字体判定；实测可 1.85× 且零输出差异 | `s1_parse.py:278-300` |

> 计数：HIGH 11 / MED 15 / LOW 4（含性能机会）。

---

## 4. HIGH 级发现

### 4.1 R9-PIPE-1 DOCX 文本框内容静默丢失（FR-101/102）

**位置**：`doc2md/pipeline/s1_parse.py:529-601`（`_docx_para_line`）、`:603-610`（`_docx_all_runs`）、`:780-815`（`read_docx`）。

**证据（本机探针）**

构造一个 `w:p`：`前` + `w:drawing/wps:txbx/w:txbxContent` 内的“框内文字” + `后`。结果：

```text
python-docx p.text: '前后'
read_docx lines: ['前后']
```

原因：无链接 / 无 sdt / 无修订时走 `p.text` / `p.runs`，python-docx 不进入 `w:drawing`；文本框段落不是 `body` 的直接 `w:p`，`_iter_docx_blocks` 也不会遍历。若文档任意位置存在链接或 sdt，`use_all=True` 后 `_docx_all_runs` 会 `p._element.iter(w:r)`，反而把文本框文字拼进宿主段落；遇到 `mc:AlternateContent` 的 Choice/Fallback 双分支会重复一次。页眉/页脚（`header*.xml` / `footer*.xml`）从未读取。

**影响**

- 文本框在合同、表单、模板、封面上很常见；内容丢失是静默的，报告/IR 都没有告警。
- Choice/Fallback 重复会制造重复正文；与 FR-101“文本完整抽取”直接冲突。

**修复方案（D2）**

1. 从 `_docx_all_runs` 排除 `w:drawing`、`w:txbxContent`、`mc:AlternateContent` 内的 run（只保留宿主段落正文），消除重复。
2. 新增 `_iter_docx_textboxes`：按文档顺序遍历 `w:txbxContent` 的 `w:p`，每个文本框段落作为独立 `Line`（可标 `flags.text_box=True`），在宿主段落之后落块；同一文本只输出一次。
3. 页眉/页脚默认**不抽取**（页面家具、重复页码/标题会污染 Markdown），但把这条写进非目标清单；若后续要归档版式，再加 `docx.page_furniture=false|append` 配置，默认 false。
4. 测试：纯文本框、文本框+链接、AlternateContent 双分支、多个文本框顺序、空文本框、无文本框 G6。

**工作量**：1.5 人日（含样本与黄金）。

---

### 4.2 R9-PIPE-2 10.5pt 正文短编号行被抬成标题（FR-104）

**位置**：`doc2md/pipeline/s3_structure.py:514-526`（`modal_size` 的 `round(size, 0)`）、`:529-570`（`_heading_level` 第三回退）。

**证据（本机探针）**

用 10.5pt 正文生成 PDF：

```text
sizes [10.5, 10.5, ...]
modal 10.0
heading level=1 '1. 第一项'
heading level=2 '1.1 范围'
heading level=1 '2. 第二项'
```

`modal_size` 把 10.5 四舍五入成 10.0，第三回退的 `(size or base) > base` 对 10.5 > 10.0 恒真，于是所有单级编号短行绕过 CH-478 的“同字号 → list_item”信号门，直接变成 H1/H2。`第 1 章的内容需要结合上下文理解` 这类无句末标点的正文也会因 `cjk_head` 信号被抬成标题（本机用 ASCII 字体无法正确复现 CJK 文本，但代码路径确定；真实语料归 B-15）。

**影响**

- 10.5pt / 11.5pt 是 Word/PDF 的常见正文字号；列表被误判成标题会污染 TOC、层级校正和下游检索。
- CH-478 的修复被绕过，属于“已有门禁对常见字号失效”。

**修复方案（D2）**

1. `modal_size` 保留 0.1 精度（`round(size, 1)`），或第三回退用 `size > base + 0.5` 容差；二者同时做，避免 10.4/10.5 抖动。
2. `_CJK_HEAD` 后接正文的负例：在 `_heading_level` 对 `第 N 章` 后的常见虚词（的/所/中/里/内/是/为/规/要/内容…）加否定断言，或要求 CJK 章标记同时具备字号/粗体信号；正例仍保留 `第 1 章 总则`。
3. 新增 10.5/11.5pt PDF 样本与 CJK 正反例；黄金 H1 计数只在明确标题样本上断言。

**工作量**：0.3 人日（含样本）。

---

### 4.3 R9-PIPE-3 DOCX 自动编号渲染语义错误（FR-104）

**位置**：`s1_parse.py:476-527`（`_DocxNumbering.marker`）、`s3_structure.py:390-404`（S3 列表分支）、`s4_render.py:59-68`（S4 列表渲染）。

**证据（本机探针，构造真实 numbering.xml）**

```text
marker 'o' level 0  -> BLOCK list_item '1. 项目甲'
marker 'o' level 0  -> BLOCK list_item '1. 项目乙'
marker '2.1.' level 1 -> BLOCK list_item '  2.1. 子项'
MD:
1. 项目甲
1. 项目乙
  -   2.1. 子项
```

- `numFmt=bullet` 且 `lvlText="o"` 时，`marker()` 返回 `"o"`；S3 `_BULLET` 只认 `·•‣∙○●■□-*`，于是把 `o` 归一成 `1.`，把项目符号列表错误渲染成有序列表。
- 多级 decimal（`lvlText="%1.%2."`）返回 `"2.1."`；S4 的 `_ORDERED_ITEM` 只支持 `\d{1,3}` 后接分隔符，`2.1.` 落入 bullet 分支，产出 `  -   2.1. 子项`（双标记 + 多余空格）。
- Heading 样式段落跳过编号解析（S1 的 `not style.startswith("heading")`），标题里的 “1.” 编号完全丢失，正文只剩“方法”。

**影响**

- FR-104 的列表/标题结构在真实 Word 列表样式下错误；项目符号与有序列表混淆会误导读者与下游。
- 现有测试只覆盖 decimal/lowerLetter，未覆盖 bullet、多级 decimal、Heading+numPr。

**修复方案（D2）**

1. `marker()` 对 `numFmt=bullet` 一律返回 `•`（或 `-`），不信任 `lvlText` 的任意字形。
2. Heading 段落仍解析 numPr；S3 headings 分支把 marker 前缀并入标题文本（或写入 `flags.docx_numbering` 并渲染），保留编号。
3. 多级 marker 的 GFM 策略二选一并在文档写死：
   - A（推荐，保号）：S4 对 `^\d+(\.\d+)+[.]?$` 的 list_item 渲染为 `indent + marker + " " + 内容`，作为缩进文本（GFM 不认多级有序标记，但不丢号）；
   - B（归一）：渲染为 `indent + "1. " + 内容`，仅在 flags 保留原始 marker。选定 A。
4. 测试：bullet（o / • / 空 lvlText）、多级 decimal、Heading+numPr、start/重启、表格内编号。

**工作量**：1 人日。

---

### 4.4 R9-PIPE-4 DOCX 表格首列编号行被升为标题（FR-104/106）

**位置**：`s3_structure.py:374-386`（heading 分支）早于 `:429-442`（table row 分组）。

**证据（本机探针）**

3×2 DOCX 表：

```text
LINE '1.1 范围 | 内容甲' row=['1.1 范围','内容甲']
LINE '1.2 目标 | 内容乙' row=['1.2 目标','内容乙']
BLOCK heading level=2 '1.1 范围 | 内容甲'
BLOCK heading level=2 '1.2 目标 | 内容乙'
BLOCK paragraph '普通 | 内容丙'
```

表格行 `size=0.0`、`bold=False`，但 `multi_level=True` 触发第三回退，heading 分支先于 table 分组执行，整表被拆成标题串；后续行不满足连续表格条件，表格块消失。

**影响**

- 规格书、参数表、合同附件常用“编号 + 名称”作为首列；这是 FR-104 表格结构的直接丢失。
- 与 R9-PIPE-2 共享第三回退的放大效应，但修复点不同。

**修复方案（D2）**

1. heading 判定加 `if ln.row: lvl = None`，或把 `row_ok` / 表格分组放到 heading 之前；推荐前者（最小改动，保留现有分组语义）。
2. 单行表格仍可能退化为 paragraph（既有口径），但不能是 heading。
3. 测试：3×2 表、首列编号/无编号、PDF 几何行不受影响、普通多列文本不被误判。

**工作量**：0.3 人日。

---

### 4.5 R9-SVC-1 异步 drift 消费绕过删除墓碑，已删文档复活（FR-128）

**位置**：`worker.py:242-285`（`_finish_ok`）、`scheduler.py:62-90`（`_submit_tx`）、`api.py:329-370`（同步路径已有 `since_seq`）。

**证据（本机真实 SQLite 探针）**

1. 队列任务 `doc_id=D1`（入队后源文件被改写为 `D2`，形成合法 drift）；
2. 库内已有文档 `D2`；
3. `delete_doc(D2)` 写墓碑、删 docs/blocks；任务行是 `D1`，**不会被级联删除**；
4. worker 以当前内容 `D2` 调 `_finish_ok`：`mark(D1, done)` 成功，`save_ir(D2)` 写回。

实测结果：

```text
D2 after delete: None
finish_ok returned drift= True
D2 after finish: {'doc_id': '2222222222222222', 'text': 'resurrected D2'}
task status: done
```

CH-483 的墓碑检查只覆盖同步 `_finish_one`，异步路径只靠“任务行是否还在”判断；drift 让任务行与消费内容 `doc_id` 分离，任务行存活即绕过。

**影响**

- 直接违反 FR-128；已删除的敏感文档在无人值守队列中复活，并会进入后续备份。
- 与 CH-484 的 zip 孤儿修复不冲突：这是库面复活，不是文件孤儿。

**修复方案（D1）**

1. `tasks` 增加 `del_seq INTEGER NOT NULL DEFAULT 0`（DB_VERSION 7→8）；入队时在同一事务内取 `MAX(deletions.id)` 写入。
2. `_finish_ok` 在 `mark`/`save_ir` 同一 `BEGIN IMMEDIATE` 事务内检查：
   ```sql
   SELECT 1 FROM deletions
    WHERE (doc_id=? OR doc_id='source:'||?)
      AND id > ?
    LIMIT 1
   ```
   参数为 `ir.doc_id`、`ir.source_key`、`task.del_seq`；命中即抛 E4041 回滚，任务记 rejected/E4041，并复用 `_drop_orphan_artifact`。
3. 同步路径继续用请求入口的 `since_seq`（不能改用 submit 时 MAX，否则“转换期间删除、submit 在删除之后”会漏判）；两条路径分别测试。
4. 迁移幂等；旧任务 `del_seq=0` 会保守地看到所有墓碑（可能误拒不删的旧任务，但旧队列在升级后通常已排空；如需兼容，可用任务 `created_at` 回填）。
5. 测试：drift × 单文档删除、drift × 整源删除、无删除正常 drift、删除发生在入队前（允许）、并发 50 次无复活。

**工作量**：1 人日（含 DB8 与测试）。

---

### 4.6 R9-SVC-2 产物与库面双写不一致（FR-106/205/128）

**位置**：`edits.py:57-93`（PATCH 只改 blocks/FTS）、`pipeline/convert.py:232-253`（`package()` 直接写最终 zip）、`api.py:329-370`（sync `_finish_one`）、`worker.py:242-285`（`_finish_ok`）、`api.py:522-540`（`/markdown` 读 zip）。

**证据（本机探针）**

1. 转换 → `save_ir` → `PATCH` 一个块为 `PATCHED-CONTENT`：
   ```text
   db text PATCHED-CONTENT
   zip has PATCHED? False   （zip 仍是旧文本）
   ```
2. 有编辑的文档再次转换：`convert()` 先把新 zip 写进 `out/{doc_id}.zip`，随后 `save_ir(force=False)` 抛 E4091；事务回滚，库里仍是 `EDITED-BY-USER`，zip 已被未编辑版本覆盖。
3. `package()` 使用 `ZipFile(zp, "w")` 直接写最终名；进程崩溃/ENOSPC 留下截断 zip，重转期间读者可看到半成品；目录内两个相同内容文件在进程池中并发写同一 `doc_id.zip`。
4. 删前/日备份只含 `state.sqlite`；删除会清 `out/{doc_id}.zip`；恢复 DB 后 docs 行存在，但 zip 不存在，`GET /docs/<id>/markdown` 404。

**影响**

- 编辑后的导出静默返回旧内容；重新转换被拒后导出返回未编辑内容——两种都比报错更隐蔽。
- zip 是 FR-106 的交付物、FR-205 的导出源；它不是“缓存”，而是与库面并列的事实源，当前没有一致性机制。
- 并发/崩溃会损坏交付物；恢复演练只验证 DB，不验证产物，给出虚假信心。

**修复方案（D4，分两阶段）**

**阶段 A（本批，低风险）**
- `package()` 写 `out/.tmp/{doc_id}-{uuid}.zip.part`，fsync 后由服务层在 DB 事务提交成功后 `os.replace` 到最终名；CLI 直转路径可在 `convert()` 返回前自行 finalize。失败/回滚时删临时件，绝不覆盖旧 zip。
- 转换前预检：若 `docs` 中该 `doc_id` 已有 `revision>1` 且 `force=False`，在**转换前**返回 E4091，避免浪费与覆盖（并发窗口仍由阶段 A 的原子发布兜底）。
- `run_batch` / worker 对同一 `doc_id` 去重或加内容哈希锁，避免多个进程写同一最终名。

**阶段 B（DB 成为唯一事实源）**
- blocks 表补 `links_json` / `font_json` / `provenance_json`，docs 补 `gate_json`；`save_ir` / `list_blocks` 读写；`SCHEMA_VERSION` 不必变（IR 字段未变），`DB_VERSION` 与阶段 A 的 `del_seq` 合并为 8。
- 新增 `render_artifacts(doc_id)`：从 DB 重建 `content.md` / `ir.json` / `report.html`，用于 `/markdown`（编辑后即时反映）与恢复后补产物。zip 只作为缓存/分发格式。
- 若暂不做阶段 B，则必须在文档中明确“FR-205 导出的是初始转换产物，不反映编辑”，并在恢复 CLI 中至少提示“产物未恢复、请重转”；不能继续让调用方默认二者一致。

**测试**：PATCH 后导出含新文本；E4091 后旧 zip 逐字节不变；崩溃注入不留最终半成品；两个相同内容文件并发不产生坏 zip；恢复后导出可用（阶段 B）。

**工作量**：阶段 A 1–1.5 人日；阶段 B 3–4 人日（含迁移与黄金）。

---

### 4.7 R9-OPS-1 删前快照生命周期不闭环（FR-128/NFR-6）

**位置**：`ops/backup.py:42`（`_SNAPSHOT_RE` 只认 `state-*`）、`:233-272`（`predelete_snapshot` / `rotate_predelete`）、`cron.py:186`（只调 `rotate`）、`api.py:81-103/584/597`。

**证据（本机探针）**

```text
# 预置 2020-01-01 的 predelete-...sqlite，执行 BackupPlan.rotate(now=2026-10-18)
cron rotate removed: []
predelete still exists after cron rotate: True
# 调 rotate_predelete(now=2026-10-18)（只有一份）
rotate_predelete removed: []
predelete still exists after own rotate: True
```

同时：

- `_predelete_snapshot` 在删除前执行，**不调用 `assert_free_space`**；`min_free_mb=10TB` 也照写（本机代理实测）。
- 每次删除（包括删除不存在的 doc/source）都先做一次 O(DB) 全库快照；无份数/总字节上限，保留期只按 14 天年龄，且“永远保留最新一份”使单次删除的快照可永久留存。文件未 `chmod 600`（实测默认 0644；`mode_warnings` 被 API 丢弃）。
- 磁盘满时 `sqlite_backup` 失败 → E5031 → 删除被阻断；用户想靠删除释放空间时反而无法删除。

**影响**

- FR-128“已删数据 ≤14 天从全部副本轮出”不成立：`work/reports/backups/predelete-*.sqlite` 含全库正文，可长期留在明文目录。
- 500 篇逐篇删除 = 500 份全库副本；备份目录可能被快照撑满，进而阻断删除与其他写盘。
- 删除不存在对象也付出全库拷贝成本，是可被 admin 触发的磁盘放大面。

**修复方案（D5）**

1. **先存在性检查再快照**：API 删除端点先 `SELECT 1 FROM docs/sources`；不存在直接 404，零快照。
2. **磁盘门禁**：快照前 `assert_free_space(local_dir, db_bytes, cfg, what="predelete")`；不足返回 413/E4131，或按明确策略返回 E5031；不要把“快照失败”变成“删除永远不可用”。
3. **轮出**：`cron --daily` 明确调用 `rotate_predelete()`；`rotate_predelete` 删除所有超过 `backup_rotation_days` 的项（**包括最新一份**——FR-128 是上限不是下限），再按 `predelete_keep_max`（建议硬编码 50 或配置）与总字节上限淘汰最旧；`pre-restore-*` 同样纳入。
4. **权限**：快照文件 `chmod 600`；`mode_warnings` 上报 API/日报，非 POSIX 卷上至少让用户知道“700 前提不成立”。
5. **架构后手**：阶段 B 的 DB 事实源可用“单文档删除归档”替掉全库拷贝（doc row + blocks + audit + zip），从根本上消除 O(DB) 放大；在报告/配置中列为后续项。
6. 测试：旧 predelete 在 cron 后消失；最新份超过 14 天也消失；404 不产生快照；ENOSPC 行为；0644→0600；整源删除只一份快照。

**工作量**：1 人日（含 cron 接线与测试）。

---

### 4.8 R9-OPS-2 v1 历史容器无法通过 CLI 恢复（FR-607/NFR-18）

**位置**：`ops/backup.py:108-178`（`decrypt_stream` 只支持 v2；v1 抛 “use decrypt_bytes”）、`:365-418`（`restore_safe` 只走 `decrypt_stream`）、`ops/backup.md:55-56`、`dev-plan.md` 6.4。

**证据（本机探针）**

构造 v1 容器（`MAGIC + nonce + AESGCM(static salt)`）后：

```text
decrypt_bytes ok: b'SQLite f'
restore_safe FAILED: ValueError legacy v1 container: use decrypt_bytes
dst exists: False tmp leftover: []
```

`decrypt_bytes` 能读 v1，但唯一用户入口 `doc2md backup restore` / `BackupPlan.restore` 只调 `decrypt_stream`；`restore_safe` 读到 `MAGIC` 后进入同一分支。文档与 README 却写“v1 只读兼容、旧 .enc 副本仍可恢复”。

**影响**

- 升级前产生的 v1 外置备份无法用当前恢复入口恢复；备份的“最后防线”在历史副本上失效。
- `ops/backup.md` 的恢复步骤会把用户引向必然失败的路径。

**修复方案（D6）**

1. `restore_safe` 检测 `head == MAGIC`（v1）时：读取整个容器（v1 是单块，历史库大小有限；加 `MAX_V1_BYTES` 防御），`decrypt_bytes` 写到 `.part`，再走 `integrity_check`；或实现 v1 流式原语（成本更高，不必要）。
2. `restore`（非 safe）同步支持，或用 `restore_safe` 统一；drill 改为 `restore_safe`（当前 drill 仍用 `restore`，见 OPS-9）。
3. 恢复失败语义（D6 同批）：
   - `restore_safe` 当前在 `tmp.replace(dst)` 前删除 `dst-wal/-shm`，替换成功后才做第二次 integrity；第二次失败时新库已落盘，文档“目标保持不变”不成立。建议先把旧库及 `-wal/-shm` 改名到 `pre-restore-*` 保留，替换/复核失败时回滚；成功后再清理旧件。
   - `pre-restore-*` 纳入轮出与 `chmod 600`，否则每次 `--force` 永久留一份明文全库。
4. 文档修正与测试：v1 加密/明文恢复成功；错误口令/篡改；无 `--force`；旧库备份；WAL/SHM 清理；替换失败回滚；`pre-restore` 轮出。

**工作量**：1–1.5 人日（含测试与文档）。

---

### 4.9 R9-OPS-3 Windows `sendmsg` 缺失导致 offline_assert 主路径失败（B-6/B-7）

**位置**：`doc2md/security/netguard.py:143-158`。

**证据**

```text
# 模拟一个缺 sendmsg 的 socket 类（Windows 的 CPython 形态）
install failed: AttributeError type object 'FakeSocket' has no attribute 'sendmsg'
orig keys: ['connect', 'connect_ex', 'sendto']
```

CPython 文档与 #122153 明确：`socket.sendmsg()/recvmsg()` 只在 Unix-like 系统实现。Windows 上 `run.offline_assert` 默认 true，`convert()` 进入 `offline_guard` 即 `_install()`，在 `_ORIG["sendmsg"] = socket.socket.sendmsg` 处抛 `AttributeError`；`offline_guard` 已设置 `_STATE["allowed"]` 但未进入 try/finally，装桩失败，转换直接失败。`_uninstall` 同样无条件 `pop("sendmsg")`。

**影响**

- 目标机型 A（Win11）的**所有转换**在默认配置下不可用；CLI/API/worker 同路径。
- 该缺陷不会被本机 macOS/Linux harness 或当前 CI（仅 ubuntu）捕获。

**修复方案（D7）**

1. `_install` / `_uninstall` 用 `getattr(socket.socket, "sendmsg", None)`；缺失则跳过该原语的打桩/还原，并在 `is_armed`/doctor 中记录 `sendmsg_supported=false`（不是失败）。
2. 装桩失败时回滚已完成的 patch（try/except），保持 `_ORIG` 与 socket 类一致；补一个模拟缺失属性的单测。
3. 在 Windows CI 腿（B-7）真机跑一次 `doc2md convert` + `doctor`；B-6/B-7 的出口一并覆盖。
4. `doctor` 输出 `offline_sendmsg` 支持状态，避免下次再靠猜。

**工作量**：0.2 人日（A）+ B-6/B-7 真机。

---

### 4.10 R9-OPS-4 CLI 前置 `--root/--config` 被覆盖，restore 可覆盖错库

**位置**：`cli.py:66-68`（`_add_common`）、`:87-110`（父/子 parser 重复注册）、`:281-315`（`cmd_backup_restore`）。

**证据（本机 argparse 探针）**

```text
['serve','--root','/x','token','--role','admin'] -> root= None
['backup','--root','/x','restore','src','--force'] -> root= None
['tokenizer','--root','/x','verify',...] -> root= None
```

父 parser 与子 parser 都注册了同名 `--root/--config`；子 parser 的默认 `None` 覆盖父值。用户按常见写法把全局选项放在子子命令前时，`_cfg(args)` 看不到 root，`restore` 默认落到 `./work/state.sqlite`；配合 `--force` 可能覆盖错误的库。

**影响**

- restore 是灾难恢复入口，目标库错误属于二次事故。
- serve token/tokenizer/backup drill 同样受影响的路径口径问题。

**修复方案（D8）**

1. 只在父 parser 注册 `--root/--config`，或用 `argparse.SUPPRESS` 让子 parser 不写入默认值，再在 `main()` 中合并；两种方式都要覆盖所有两层子命令。
2. `cmd_backup_restore` 在替换前打印并校验目标路径：若 `--to` 未给且 root 来自默认值，要求显式 `--force` 并输出 `target=...`；可加 `--expect-root` 防误覆盖（可选）。
3. 测试：选项在子命令前/后、serve token、tokenizer、backup drill/restore 六组解析结果一致。

**工作量**：0.3 人日。

---

### 4.11 R9-OPS-5 并发备份/快照写坏容器并触发错误轮出（FR-607/NFR-18）

**位置**：`backup.py:219-231`（`snapshot` 直写最终名）、`:309-340`（`to_external` 的 `.part` 名）、`:421-470`（`rotate` 的 `keep_newest`）、`cron.py:139-190`（无进程锁）。

**证据（本机 4 线程探针）**

```text
errs [FileNotFoundError ×3]
outs [('state-20261018T000000Z.sqlite.enc', 8000091)]
state-...enc decrypt FAILED InvalidTag
```

四个线程对同一快照并发 `to_external`：共用 `out.name + ".part"`，互相截断/rename，最终 `.enc` 尺寸异常且 GCM 认证失败；另外三个线程因 `.part` 被 rename 而 FileNotFound。代理的 6 路并发也复现了 `67,109,347B ≠ 67,108,864B` 与 InvalidTag。日/周补跑重叠、手动 cron 与 launchd 重叠、同一秒两次 snapshot 都可能触发；`keep_newest` 会把坏副本当最新保护起来，轮出再把好的旧副本删掉。

**影响**

- 备份最后防线可能被静默写成不可解密容器；演练/恢复在需要时才失败。
- 并发模式目前没有任何锁或唯一临时名；这是数据安全问题，不只是性能。

**修复方案（D5）**

1. **备份阶段进程锁**：在 `local_dir` 放 `backup.lock`，用 POSIX `fcntl.flock` / Windows `msvcrt.locking` 或 `O_CREAT|O_EXCL` + 过期 PID 检测；`cron` 的 snapshot→external→rotate 全程持锁；拿不到锁记录 `backup_skipped: another run`，不并发写。
2. `snapshot()` 写 `state-<ts>-<rand>.sqlite.part`，fsync 后 `os.replace`；`to_external` 的 `.part` 加 UUID；写入后对容器做一次解密/认证自检（或至少首块 GCM 验证）再 rename。
3. `rotate` 不把未通过自检/尺寸异常的文件当作 newest；对 `state-*.sqlite` 与 `.enc` 分别做 `PRAGMA integrity_check` / GCM 首块验证；轮出在锁内执行。
4. 测试：2/4/8 路并发同一快照；cron 重入；SIGKILL 半成品；坏副本不被 keep_newest 保护。

**工作量**：1 人日（含跨平台锁与测试）。

---

## 5. MED 级发现

### 5.1 R9-PIPE-5 块级 marks 合并错误（FR-108）

**位置**：`s3_structure.py:187-212`（`_mark_provenance` 用 `setdefault` 且只传新行）、`:464-477`（`_join_back` 路径）、`sharding.py:313-322`（缝合）、`s1_parse.py:292-300`（PDF 的 `uniform_*` 是 ≥60% 多数派）。

**证据（本机探针）**

```text
粗体无句末 + 普通续行 -> paragraph '无句末的粗体段落，普通续行' {'bold': True}
普通无句末 + 粗体续行 -> paragraph '普通段落无句末，粗体续行' {'bold': True}
```

两种方向都把局部格式扩散成整段 `**...**`。PDF 侧 `_line_style` 的 bold/italic/strike 是行内 ≥60% 字符多数派，`uniform_bold` 直接复用该布尔值，与 CH-483 的“所有非空文本统一”不符；本机 89% 粗体 + 11% 常规的同一行被写成整段粗体。

**修复（D3）**：块级 marks 用 AND 语义；`_join_back`/缝合时若新行不满足统一格式就清除对应 mark（不新增）；PDF 的 `uniform_*` 对非空白字符逐字判定；新增续行、缝合、混合 PDF 三类测试。

### 5.2 R9-PIPE-6 纯数字表格页被 auto 门禁误拒（FR-103）

**位置**：`s1_5_gate.py:23`（`_NOISE` 含 `\d`）、`:92-113`、`:203-218`。

**证据（本机探针）**：两页各 20 行 `2024 100 250 390`（chars=440）→ `E4221`，reason `consecutive_empty_pages=2` / `all_pages_empty` / `noise_ratio=1.00`。

**影响/修复（D3）**：合法财务报表/附录/数据页被整篇拒收。`_NOISE` 去掉 `\d`（或 `letters` 计入数字），保留符号噪声判据；补充数字表、混合表、真乱码三类测试；真实语料归 B-1/B-15。

### 5.3 R9-PIPE-7 PDF 多行段落首行链接包住整段（FR-107）

**位置**：`s3_structure.py:174-185`（`_attach_link` 无条件把 `b.text` 当 link text）、`s1_parse.py:200-243`（行级链接附着）、`s4_render.py:82-105`。

**证据（本机探针）**：

```text
BLOCK paragraph 'See URL ... and the second line continues the paragraph' links=[url=...]
[See URL ... and the second line continues the paragraph](https://example.com/x)
```

链接只覆盖首行，却被扩成整段。**修复（D3）**：只有 `ln.text.strip() == b.text.strip()`（单行块）时才附着；多行块保持纯文本，链接信息保留在 IR 行级/报告或拆块；DOCX 路径已有“完整覆盖”口径，PDF 对齐。测试：首行/末行/跨行链接、单行 G6。

### 5.4 R9-PIPE-8 跨片 list_item 被缝合（FR-126）

**位置**：`sharding.py:73`（`_STRUCTURAL` 不含 `list_item`）、`:76-108`。

**证据（本机探针）**：两片各一个 list_item（`1. 第一项` / `2. 第二项`，同字号、无句末）→ `stitched=1`，文本 `1. 第一项2. 第二项`，列表边界丢失。

**修复（D3）**：把 `list_item` 纳入 `_STRUCTURAL`，或要求类型/marker/level 相同且语义续接才合并；优先前者（结构块不吸收）。测试：跨片列表、真正跨页的列表续行、G6。

### 5.5 R9-PIPE-9 batch 异常消息可能泄漏正文（NFR-20）

**位置**：`batch.py:80-82`（`message=f"{type(exc).__name__}: {exc}"`）、`worker.py:234-238`（把 message 写进 task detail）、`api.py` 的 `/jobs` 可读 detail。

`_open_source`、`convert` 的 fallback、worker 的 “worker died” 分支都刻意只带类名，唯独 `_convert_one` 的通用异常带 `str(exc)`；pdfminer/pdfplumber 的异常消息可能夹带页正文片段。reader 角色可读 `/jobs`，违反“队列明细不含正文”的既有口径。**修复（D3）**：通用异常只写类名或走 `_scrub`；补异常消息含敏感片段的测试。

### 5.6 R9-SVC-3 队列/幂等契约三处不一致（FR-702）

**位置**：`scheduler.py:62-102`、`worker.py:392-403`、`api.py:310-325`、`worker.py:449-478`。

- **幂等键复用取消在途作业**：`submit_rejected(name, idem_key=k)` 命中一个 queued 的有效作业（`_submit_tx` 先看 idem_key，不看状态/doc_id），随后 `mark(..., rejected)` 把它取消。本机实测：J1 queued → `submit_rejected` 返回 J1 并标 rejected/E4221。
- **终态命中回报 queued/错 doc_id**：`_submit_tx` 对 idem 命中硬编码 `status="queued"`，`enqueue_many` 又用本次哈希的 doc_id 组装响应；键命中一个已 rejected 的旧行时，客户端 202 后永久等待。
- **异步 force 被忽略**：`POST /jobs` 的 `force` 只传同步路径；worker `save_ir(force=False)` 恒拒绝编辑中的文档，客户端却已拿到 202。
- **work 主循环无重试**：`run_work` 的 while 没有 `sqlite3.OperationalError` 退避；busy/ENOSPC 直接退出，常驻消费者停摆到守护拉起。

**修复（D9）**：幂等键绑定 `(doc_id, source_path, mode)`，同键不同载荷 409/E4001；`submit_rejected` 用名空间 `key#reject` 或只在非活动行上复用；`Job` 返回真实 status/doc_id；`tasks` 增 `force` 列（与 del_seq 同迁移）；`run_work` 捕获瞬时 SQLite/OSError、退避并记 `WORK_RETRY`。测试：键冲突/终态命中/异步 force/锁超时。

### 5.7 R9-SVC-4 搜索窗口外不可检索 + 零命中全扫（FR-204）

**位置**：`repo.py:13-17`（`SEARCH_INDEX_WINDOW=8000`）、`:283-338`（`search` 的 LIKE 兜底）。

**证据（本机探针）**

- 8,608 字块，`TAILMARK` 在 8,500 字处：`search` 返回 `mode=like, total=0`；`blocks.length=8608`。`save_ir` 只把前 8,000 字写入 FTS，LIKE 也查 `search_blocks.text`，所以注释所称“尾部照常读得到/LIKE 兜底”不成立。
- 20,000 块库：命中 `fts` 19.1ms；不存在词 `like` 4.4ms 且 `EXPLAIN` 为 `SCAN search_blocks`；零命中每次全扫索引正文。

**修复（D9）**：正常 FTS 零命中直接返回，不再 LIKE 全扫；新增显式 `?mode=full`（或 `search.full_text` 配置）用 `blocks.text` 做有上限/超时的全量检索；或提高窗口并明确覆盖声明。测试：尾部词、零命中耗时、旧库 aux 缺失兜底、5000 字查询。

### 5.8 R9-SVC-5 `paths.out` 未统一（配置契约）

**位置**：`api.py:422/452`（同步/并行写 `root/"out"`）、`:522`（`/markdown` 读 `root/"out"`）、`cli.py:512`（watch 写 `root/"out"`）；`worker.py:71-72` 与 `api.py:587/600` 已用 `config.path_setting`。

**影响**：配置 `paths.out=/data/out` 时，worker 产物导出 404；API 同步产物写到 root/out，而删除只清 `/data/out`，删后 zip 残留（与 CH-483 修复的同类）。**修复（D8）**：所有写/读/删除统一 `config.path_setting(cfg,"paths.out",...)`；`api.get_markdown` 从配置解析；watch 用 `_out(cfg)`；补自定义 out 的端到端测试（转换→导出→删除→无残留）。

### 5.9 R9-SVC-6 `save_ir` 在写锁内做序列化与 n-gram（性能）

**位置**：`repo.py:66-131`；`_do(t)` 内构建 `json.dumps` 与 `cjk_aux`。

**证据**：320 页 / 5,789 块，`save_ir` 实测 **0.41s**；其中 JSON 序列化与两倍 8,000 字 n-gram 都在 `BEGIN IMMEDIATE` 内，期间所有 writer（其他 worker/PATCH/删除）被阻塞。大文档线性放大。**修复（D9）**：在事务外预计算 `block_rows` / `fts_rows`（`PreparedIR`），事务内只 `executemany`；调用方 `_finish_ok/_finish_one` 在 `store.tx()` 之前准备。目标：5,789 块写锁 <100ms；补锁时长断言。

### 5.10 R9-OPS-6 cron weekly 外置段异常未收口

**位置**：`cron.py:144-190`：外置段只 `except Doc2mdError`；`to_external` 可抛 `PermissionError`（`encrypted_external=false`）、`OSError`/`FileExistsError`（目录不可写/半成品冲突）。

**影响**：weekly 在快照成功后中止，不写报告，rotation/tmp/日志/events/tasks/reconcile 全跳过；快照失败又跳过 rotation，磁盘满时无法自愈。**修复（D8）**：逐步捕获 `Exception` 记 `ok=False` + `finally` 落报告；rotation 独立执行（本地快照成功即可；外置失败不影响本地轮出）；补 PermissionError/OSError 测试。

### 5.11 R9-OPS-7 配置文档矛盾 + 未知键/doctor 盲区

**位置**：`config/loader.py:1-3`（头注释写“显式 → cwd → home”，实现是 home → cwd → explicit）、`:166-205`；`dev-plan.md` 附录 A 仍说“只叠一个文件”；`cli.py:588-591` doctor 只列 `backup_encrypted_external`，不列 `backup_external_dir`/unknown keys。

**影响**：`[ops] backup_external_dir` 拼错时 `unknown_keys` 仅 UserWarning，cron 把“未配置”记为 skipped 且 `ok=true`，用户以为外置备份已启用；`doctor` 也看不到。**修复（D8）**：统一 loader 头注释/README/附录 A 为 D7 口径；doctor 输出外置目录、passphrase 是否可见、unknown keys（非空则 WARN/退非零）；cron 报告区分“未配置/拼写未知/口令缺失”。

### 5.12 R9-OPS-8 CLI 纯文本入口不落实 work 700

**位置**：`cli.py:191` 仅 serve chmod；`cron` 经 BackupPlan 快照 chmod；`convert/work/watch` 无 chmod；`grep chmod` 见 `api.py:865` 仅 tmp。

**影响**：不跑 serve/备份的纯 CLI 用户，`work/` 可能 755、`state.sqlite`/产物 644，含正文的目录对其他用户可读，违反 NFR-20/数据边界。**修复（D8）**：三入口统一 `chmod 700`（非 POSIX 卷记录 warning），产物/DB 文件 600；`doctor` 显示实际 mode；补权限断言。

### 5.13 R9-DOC-1 计数/编号/CI 证据漂移

**证据（本机实测 vs 文档）**

| 项 | 实测 | 文档 |
| --- | --- | --- |
| gate | 311 | README 219-220 / dev-plan 654 / §9.1 仍 285 |
| full | 886 | dev-plan 655 / §8.1 / 附录 F 仍 852 |
| 包规模 | 39 `.py` / 10,178 行 | dev-plan 3.5 仍 39/9,497 |
| 测试规模 | 40 `.py` / 15,763 行 | dev-plan 3.5 / 附录 G 仍 35/14,928 |
| 仓库跟踪 | 5,256 | dev-plan 8.4 仍 5,249 |
| 第十一章标题 | 含 CH-483/484 | dev-plan:1359 仍 `CH-422 … CH-482` |
| 12.10 | 缺 v3.18 行 | CH-484 登记却称已同步 F–G/12.10 |
| README CI | 最新 CH-483 run 39050319814 | 缺 CH-484 run 39092382196 / 回填 39092772664 |

**修复（D10）**：统一更新或删除易变数字；把“gate/full 项数、包/测试行数、CH 编号标题”纳入版本/文档 gate（可扩展 `test_ch477_version_sync.py`）；CI 证据按实际 run 回填，禁止提前声称。

### 5.14 R9-DOC-2 `pyproject.toml` 版本漂移

`pyproject.toml:7` 为 `2.5.0`，`PIPELINE_VERSION`/README 为 `2.6.0`；CH-482 曾把 pyproject 升到 2.5.0，CH-483 升管线时漏掉。`doc2md --version` 2.6.0、包元数据 2.5.0，SBOM/升级定位失真。**修复（D10）**：升到 2.7.0（D10 版本裁定）并在 `test_ch477_version_sync.py` 加 pyproject 对账（需先确认 `importlib.metadata` 在 editable 安装下稳定）。

### 5.15 R9-DOC-3 契约文档缺项/错误

- `E4092` 只在 CH-483 登记行出现，附录 D 错误码表没有；客户端无法查契约。
- `backup restore` 已实现，但 README CLI 列表与附录 B 只有 `backup drill`。
- FR-129/§6.1 写“符号链接不跟随”，实现与 CH-483 测试支持 `follow_symlinks` opt-in + root containment（D10 例外未写）。
- `ops/backup.md:55-56` 写“失败退出码 4，目标保持不变”；实际替换前删 WAL、替换后复核失败时目标已变（R9-OPS-2）。
- 文档写 v1 “旧 .enc 副本仍可恢复”，CLI 实际不可（R9-OPS-2）。
**修复（D10）**：补 E4092/restore；改 symlink 例外、恢复失败语义、v1 口径；随版本升位统一。

---

## 6. LOW 与性能机会

### 6.1 R9-SVC-7 异步目录投递静默剔除消失文件

`api.py:310-316` 先 `[f for f in files if f.is_file()]` 再 `enqueue_many`；文件在扫描后消失时响应少一项且无错误码，与紧邻注释“按 E4041 整体失败”相反。**修复**：删掉过滤，交给 `enqueue_many` 预检；补竞态测试。

### 6.2 R9-OPS-9 `drill` 读整个容器取魔数

`ops/drill.py:165` `enc.read_bytes()[:7]` 会把 10GB 容器读进内存，与 drill 其余流式口径不一致。**修复**：`with enc.open("rb") as f: head=f.read(7)`；drill 同时改用 `restore_safe`（当前用 `restore`，见 R9-OPS-2）。

### 6.3 R9-OPS-10 watch 自旋与文档小漂移

`cli.py:483` 对 `watch.stable_seconds=0` 执行 `sleep(0)`，常驻 watch 会自旋吃满一核；建议配置校验 `>=0.1` 或按 0.1 下限。`config/doc2md.example.toml:52` 的注释错位；`dev-plan.md:573` 的磁盘公式写 `max(min_free_mb, 源大小×factor)`，实现是 `max` 口径但文字有“相加”歧义。**修复（D10）**：校验 + 文档对齐。

### 6.4 R9-PERF-1 `_line_style` 单函数 1.85×（零输出差异）

**证据（本机，15,613 行 / 589,765 字符）**

```text
base_s 0.6607  fast_s 0.3572  speedup 1.85
mismatch 0
```

现实现每行 5 趟扫描（size/bold/italic/strike/mono），且 `_is_mono/_is_bold/_is_italic` 对每个字符重扫 fontname；把 size + 四个计数合成一趟、按 fontname 缓存 `(bold,italic,mono)` 派生位，输出逐字段一致。320 页转换 10.98s 中可直接省约 0.3s（约 3%），无 G6 风险。**修复（D10）**：按等价性纪律先做 A/B 逐字节，再进主干；保留原函数名/签名。

### 6.5 其他性能机会（不单列发现）

- `save_ir` 锁外预计算（R9-SVC-6）是比 `_line_style` 更大的大文档收益。
- 搜索零命中不再 LIKE（R9-SVC-4）直接降低无结果查询的 P95。
- 预删除快照的存在性预检/上限（R9-OPS-1）消除 404/批量删除的 O(DB) 放大。
- 不做“PDF 页级并行解析”：pdfplumber 占 77% 且内存/复杂度代价高；若未来要做，先按 B-8 跨机基准和 G6 立项验证，不纳入本轮。

---

## 7. 决策裁定（D1–D10）

> 用户已授权按“功能全面、性能优先”制定最佳方案；以下为直接裁定，不再逐项询问。实施时如与代码事实冲突，按既有纪律先登记冲突再调整。

### D1 异步删除守护：`tasks.del_seq` + 事务内墓碑检查（选定）

| 方案 | 结论 | 理由 |
| --- | --- | --- |
| A. tasks 增 del_seq，_finish_ok 事务内查 `deletions.id > del_seq` | **选定** | 与删除共享 BEGIN IMMEDIATE；一次索引查询；durable、不受时钟影响 |
| B. 只查任务行是否还在 | 否决 | drift 下任务行与消费 doc_id 分离，正是 R9-SVC-1 |
| C. 转换后查 docs 是否存在 | 否决 | 无法区分“删除后重新上传”与“删除中”，且与 attach 语义冲突 |

同步路径保留请求入口 `since_seq`；异步路径用任务 `del_seq`；两者都要覆盖 doc 与 `source:<source_key>` 墓碑。

### D2 DOCX/PDF 结构范围（选定）

- 文本框：提取为独立块；排除 drawing/txbxContent 参与宿主段落；AlternateContent 只取一支。
- 页眉/页脚：默认不抽取，写入非目标清单；不做隐式追加。
- 10.5pt：`modal_size` 0.1 精度 + 0.5pt 容差；CJK 章标记后接正文的负例。
- 表格：表格行优先于 heading；首列编号绝不升标题。
- 编号：bullet 归一 `•`；Heading+numPr 保留编号；多级 marker 按“保号缩进文本”渲染（GFM 不支持多级有序标记）。
- 链接：仅整块覆盖时附着；跨行局部链接保持纯文本。
- marks：AND 语义；PDF 逐字符统一才写。
- 分片：list_item 视为结构块，不跨片吸收。

### D3 marks/表格/链接/分片规则（与 D2 同批实施）

见上；核心纪律是“宁可不加格式，不制造错误强调；宁可不合并，不丢结构”。

### D4 产物与库面：DB 为唯一事实源 + 原子发布（选定，分阶段）

- **阶段 A（本轮）**：转换前编辑预检；package 写 `.part`，DB 提交成功后原子 rename；失败/回滚删临时件；同 doc_id 并发去重/锁定。
- **阶段 B（本轮或下一轮）**：blocks 补 links/font/provenance、docs 补 gate_json；`render_artifacts` 从 DB 重建产物；`/markdown` 反映编辑；恢复后可补产物。若阶段 B 延期，必须改 FR-205 文档为“初始产物，不反映编辑”，并恢复 CLI 明示。
- 理由：当前 zip 与 DB 两个事实源没有一致性机制；只做“补 zip”会在 PATCH/E4091/恢复三条路径继续漏。

### D5 备份/删除前快照：有界、可轮出、并发安全（选定）

- 快照先存在性检查 + 磁盘门禁 + 0600 + mode_warnings 上报。
- `cron --daily` 调用 `rotate_predelete`；最新份也受 14 天约束；加份数/总字节上限（FR-128 是上限，不是保留承诺）。
- 备份全程进程锁；snapshot `.part` + fsync + rename；external `.part` 唯一化；写后自检。
- 不把“快照失败”静默变成“删除永久不可用”：磁盘不足按 E4131/E5031 明确报错，并给清理旧快照/导出单文档的路径。

### D6 恢复链路：v1 兼容 + 原子恢复（选定）

- `restore_safe` 分派 v1（整读 + decrypt_bytes，带上限）与 v2（流式）。
- 旧库及 `-wal/-shm` 先改名保留，替换/复核失败回滚；成功后再清旧件；`pre-restore-*` 轮出 + 0600；drill 改用 `restore_safe`。
- 文档不再声称“失败目标不变”，改为“可能已替换，pre-restore 可回滚”。

### D7 Windows offline_assert（选定）

- `getattr` 跳过缺失的 `sendmsg`；装桩失败回滚；doctor 显示支持状态。
- B-6/B-7 真机覆盖；Windows CI 腿必须加（这会同时兜住备份锁、CLI 参数等平台差异）。

### D8 CLI/配置/路径/权限（选定）

- 父/子 parser 只注册一次公共选项，或 `SUPPRESS` + 合并；restore 打印目标并要求显式 force。
- `paths.out` 所有读写删统一；`paths.vendor_models`、`chunks.manifest` 是否 root 相对按方案附录 A 统一（建议所有 `[paths]` 相对 root）。
- doctor 显示 external backup/unknown keys/实际权限；CLI 三入口 chmod 700。
- 配置文档与 loader 头注释统一为“默认 → home → cwd → explicit”。

### D9 队列/幂等/搜索/性能（选定）

- 幂等键绑定载荷；reject 命名空间；终态命中返回真实状态；async force 入列；work 循环退避重试。
- 搜索：FTS 零命中不再 LIKE 全扫；提供显式 full 模式；文档写明 8,000 字窗口（若阶段 B 不扩大到全文）。
- `save_ir` 锁外预计算；`_line_style` 单趟 + 字体缓存。

### D10 版本/黄金/文档/性能（选定）

| 常量 | 旧 | 新 | 原因 |
| --- | --- | --- | --- |
| `PIPELINE_VERSION` | 2.6.0 | **2.7.0** | 标题/编号/表格/文本框/链接/marks/分片产物变化 |
| `DB_VERSION` | 7 | **8** | `tasks.del_seq` + `tasks.force` + blocks links/font/provenance + docs gate_json（阶段 B 可同批） |
| `SCHEMA_VERSION` | 14 | **14** | IR 的 JSON 字段未增删（DB 存储面变化不算 schema） |
| `PLAN_VERSION` | 3.18 | **3.19** | 方案/批次/版本表/契约更新 |
| `pyproject.toml` | 2.5.0 | **2.7.0** | 与管线版本对齐 |

黄金：新增 10.5pt 编号、文本框、真实编号、首列编号表、跨行链接样本；现有样本按 G6 逐字节比对，只有预期差异才允许重烤；`MANIFEST` 与统计同步。

---

## 8. 分批实施计划

### 8.1 总览

| 批次 | 主题 | 包含 | 依赖 | 退出条件 |
| --- | --- | --- | --- | --- |
| B0 | 数据安全/恢复/Windows/CLI | R9-SVC-1、R9-OPS-1/2/3/4/5、R9-OPS-8 | D1/D5/D6/D7/D8 | 复活测试红转绿；v1 恢复；并发备份不再坏；CLI 参数六组一致；gate+full 全绿 |
| B1 | 产物与库面统一 | R9-SVC-2、R9-OPS-2 的产物面 | D4 | PATCH/E4091/恢复后导出正确；zip 原子；并发同 doc 不坏 |
| B2 | 管线结构 | R9-PIPE-1…9、R9-PERF-1 | D2/D3 | 新样本全绿；黄金差异分类；G6；gate+full |
| B3 | 队列/搜索/性能 | R9-SVC-3…7、R9-SVC-6、R9-OPS-6/7 | D9 | 幂等/force/重试/搜索/写锁测试；性能读数 |
| B4 | 文档/版本/收口 | R9-DOC-1…3、R9-OPS-9/10、R9-OPS-8 | 全部 | 版本四处一致；README/附录/ops 同步；最终 gate+full+doctor+ruff |

### 8.2 排序纪律

1. **先数据安全与可恢复性**（B0/B1）：删除、备份、恢复、产物一致性优先于格式改进。
2. **再管线结构**（B2）：PIPE-1…4 的产物字节变化集中在同一轮，只升一次 `PIPELINE_VERSION`、只重烤一次黄金。
3. **再队列/搜索/性能**（B3）：不改变主链产物字节；性能改动按 G6 逐字节验证。
4. **最后文档收口**（B4）：所有数字、run id、版本、B 类状态按实测回填，禁止提前声称。

### 8.3 每批必须满足

```text
1. 本机 gate 全绿（0 skipped）
2. 本机 full 全绿（0 skipped）
3. 新测试在旧代码上红、新代码上绿（至少 HIGH 项）
4. 涉及产物的批次：G6 逐字节 diff 分类 + 人工抽查
5. 版本/文档同步（B2/B4）
6. B 类提醒已发出
```

---

## 9. 性能方案与度量

### 9.1 基线

| 指标 | 本机值 | 方法 |
| --- | --- | --- |
| 320 页单文档转换 | **10.98s**（无 profiler）；zip 374,458B；5,789 块 | `.venv/bin/python -m doc2md.cli convert tests/golden/large_320p.pdf --root /tmp/... --out /tmp/...` |
| cProfile 总耗时 | 33.25s | 同文档 single；pdfplumber/pdfminer 约 77% |
| S1 自有热点 | `_line_style` 0.661s / 15,613 行 | 实际 slices 微基准 |
| S1.5 | `_page_metrics` 28ms / 320 页 | cProfile |
| S3 | `analyze` 0.124s | cProfile |
| `save_ir` | 0.41s / 5,789 块 | 临时 Store 实测 |
| 搜索 | 命中 19.1ms / 20k 块；零命中 4.4ms，EXPLAIN=SCAN | 临时库 |

### 9.2 变更与目标

| 变更 | 预期 | 验证 |
| --- | --- | --- |
| `_line_style` 单趟 + 字体缓存 | 0.661→0.357s（1.85×），320p 约省 0.3s | 逐字段等价 + G6 |
| `save_ir` 锁外预计算 | 5,789 块写锁 0.41s → <100ms（目标） | 锁时长断言 + 20k 块 |
| 搜索零命中不 LIKE | 零命中 P95 4.4ms → <1ms（20k 块） | EXPLAIN + 计时 |
| 搜索 full 模式 | 尾部词可检索（显式模式） | 8,608 字块探针 |
| 快照存在性预检/上限 | 404/批量删除不再 O(DB) | 计数断言 |
| 备份锁/原子写 | 并发不坏；单次锁开销可忽略 | 2/4/8 路并发 + GCM 自检 |
| 不做页级并行 | 维持内存与 G6 | 由 B-8 跨机基准决定后续 |

### 9.3 度量口径

- 产物等价：`content.md` 剥 front-matter + `ir.json` + `report.html` 逐条目 sha256；改动前后同机同数据。
- 写锁：在 `store.tx()` 前后用 `time.perf_counter` 记录；不要用整次转换耗时代替。
- 搜索：同一合成库（20k 块）分别测命中/零命中/尾部；记录 `EXPLAIN QUERY PLAN`。
- 备份：单次快照/加密墙钟与文件大小；并发 2/4/8 路后解密自检；不把备份耗时混入转换主路径。
- 所有收益只主张同机同轮相对值；跨机绝对值归 B-8。

---

## 10. 版本、DB、黄金与契约

### 10.1 版本

见 D10。补充：

- `DB_VERSION=8` 的迁移必须幂等：`tasks.del_seq` / `tasks.force`、blocks 三个 JSON 列、docs `gate_json` 均用 `PRAGMA table_info` 探测后 `ALTER`；旧库默认 0/空。
- `SCHEMA_VERSION=14` 不变；`IR.to_dict` 形态不变（阶段 B 只是把已存在于 IR 的字段持久化到 DB）。
- `PLAN_VERSION=3.19`，`test_ch477_version_sync.py` 的四处锚点同步；新增 pyproject 对账。
- 错误码：不新增段位；若幂等冲突需要 409，可在既有 4xx 段内复用 E4001/新增 E4002（需按段位纪律评审，优先复用 E4001 并给 detail.reason）。

### 10.2 黄金与 G6

- 新增：`pdf_105pt_numlist.pdf`、`docx_textbox.docx`、`docx_numbering_real.docx`（bullet/多级/Heading）、`docx_table_numbered.docx`、`pdf_multiline_link.pdf`；现有 `large_320p`、DOCX 四件、链接/表格样本作为回归。
- 变更分类：标题/列表/表格/文本框/marks/链接六类分别 diff；每类人工抽查 ≥10 个块。
- `content.md` 剥 front-matter；`ir.json`/`report.html` 原样比；不允许“顺手多改”。
- 分片等价：single vs two_phase；list_item 跨片修复后新增边界样本。
- 若阶段 B 改 DB 存储，不影响 zip 字节；但 `/markdown` 从 DB 渲染时必须以同一 S4 实现产出，避免双渲染漂移。

---

## 11. 文档同步清单

| 文档/位置 | 修改 |
| --- | --- |
| `README.md:3/27/51/92/210/219-220` | v2.7；补 `backup restore`；CH-484 run 39092382196/39092772664；gate 311/full 886（或去数字）；14 天口径加“外置失败保留最新”例外；性能表加 host/命令 |
| `docs/dev-plan.md` header/§1.4 | PIPELINE 2.7.0 / DB 8 / SCHEMA 14 / PLAN 3.19 |
| `dev-plan.md:18/214/654-655/745/1359/1816` | §11 标题至 CH-485；gate/full 311/886；包 39/10,178；测试 40/15,763；12.10 补 v3.19 与 CH-485 行 |
| `dev-plan.md` 附录 B/D | 补 `backup restore`、E4092；v1 恢复口径；symlink opt-in；恢复失败语义 |
| `dev-plan.md` 6.4/8.4/12.1 | 黄金统计 5,256/5,142/5,140；测量 host；快照/predelete 轮出 |
| `ops/backup.md:55-56/79-82` | v1 恢复；predelete/pre-restore 轮出与 0600；失败可能已替换；并发锁 |
| `config/doc2md.example.toml` | 磁盘公式与注释对齐；若新增 `tasks.force`/上限键则同步 |
| `pyproject.toml:7` | 2.7.0 |
| `docs/Doc2md项目开发方案V1.0.md` | FR-129 的 symlink opt-in 例外；FR-128 快照保留期/上限 |
| B 类表 | 更新 B-1/B-6/B-7/B-10/B-13/B-15 的出口条件（见第 12 章） |

---

## 12. B 类登记与提醒（需本机以外环境/人工）

> **提醒机制**：每完成一个批次，交付说明必须重列当前未闭环 B 项；本报告结尾再次提醒。

### 12.1 沿既有登记（编号不改）

| # | 项 | 状态/出口 |
| --- | --- | --- |
| B-1 | 真实 ToUnicode 损坏/高密度乱码 PDF 语料 | **新增数字表格页误拒面**（R9-PIPE-6）；修完后用真实语料评估误杀/漏检 |
| B-2 | 真实双栏/页眉带链接 PDF | 含 R9-PIPE-7 跨行链接；需语料评估 |
| B-3 | 真实注入语料 + `_attribute` 归因 | 未变 |
| B-4 | 并发删除/转换压测 | **已闭环（CH-484）**；R9-SVC-1 的异步 drift 新变体在本机 SQLite 探针已证，仍建议真实多进程复测 |
| B-5 | macOS cron launchd | **已验（macOS；cron 部分）**；Windows 腿归 B-6/B-7 |
| B-6 | Win11 spawn 池实测 | **扩展**：Windows offline_assert（R9-OPS-3）、备份锁、CLI 参数、常驻运行时 |
| B-7 | CI Windows 腿 | **扩展**：必须覆盖 `convert` + `doctor` + `cron` 锁；本缺陷不会被 ubuntu 捕获 |
| B-8 | 跨机性能基准 | 320p 基线/微优化/`save_ir` 锁时长在 Win11/Mac mini 复测 |
| B-9 | serve/work 常驻运行时 | macOS 半侧已验；Windows `Register-ScheduledTask` + `.ps1` 待验 |
| B-10 | 外置卷备份容器 v2 真机恢复 | **扩展**：v1 旧容器、predelete/pre-restore、并发 cron、失败回滚、产物恢复 |
| B-11 | 编辑器/审阅页真实浏览器人工走查 | 未变 |
| B-12 | >30min 合法转换与 `reclaim_stale` 双进程 | 未变；R9-SVC-3 的 worker 重试/租约需真实验证 |
| B-13 | 真实 Word 模板语料 | **扩展**：文本框、bullet/多级/Heading 编号、首列编号表、页眉页脚 |
| B-14 | 跨文件系统符号链接/权限 | 未变；R9-OPS-8 的 700 语义在 SMB/exFAT 上需验证 |
| B-15 | 真实中文 PDF 标题语料 | **扩展**：10.5pt 正文、数字表、跨行链接；统计 H1 准确率 |
| R7-OPS-3 | CLI 权限口径分裂 | 决策敏感；R9-OPS-4/8 使其更值得复核 |

### 12.2 本轮无新增独立 B 号

R9-OPS-3（Windows）并入 B-6/B-7；R9-OPS-5（并发备份）需真机 launchd/任务计划重叠，并入 B-10；R9-PIPE-1/3/4 的真实模板并入 B-13；R9-PIPE-6/7 并入 B-1/B-15。所有本机可复现项均为 A 类，不登记为 B。

### 12.3 交付时提醒（请优先安排）

1. **B-6/B-7 Windows**：`sendmsg` 主路径、CI 腿、备份锁、CLI 参数；这是唯一能证明“Win11 目标机可用”的路径。
2. **B-10 外置卷恢复演练**：v1 历史容器、predelete/pre-restore、并发 cron、失败回滚、产物恢复；建议在 D5/D6 落地后立即安排。
3. **B-13/B-15/B-1 真实语料**：Word 文本框/编号/表格；中文 10.5pt/数字表/跨行链接；噪声误杀面。
4. **B-12**：>30min 合法转换与 reclaim 双进程；与 R9-SVC-3 的租约/重试同批复测。

---

## 13. 风险、回滚与不实施声明

### 13.1 主要风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 管线结构修复改变产物字节 | 黄金漂移、下游差异 | 一次性升版；六类 diff 分类；G6 逐字节 |
| DB8 迁移 | 旧库开库失败 | 幂等 ALTER；`PRAGMA table_info` 探测；备份后升级 |
| 产物原子发布与 DB 事实源 | 短暂双写复杂度 | 阶段 A 先行；阶段 B 用单一渲染入口；失败删临时件 |
| 备份锁 | 死锁/锁文件残留 | 锁超时 + PID/时间戳；跨平台测试；B-10 |
| v1 整读恢复 | 大容器内存 | `MAX_V1_BYTES` 上限；流式 v2 不受影响 |
| Windows 修复 | 只在本机模拟 | B-6/B-7 真机门禁；CI 腿 |
| 快照上限 | 极端批量删除下较早快照提前淘汰 | FR-128 是上限；文档明确；磁盘门禁 |

### 13.2 回滚

- 每个批次独立提交；B2 的黄金/版本与代码同批回滚。
- DB8 只加列/索引，回滚到 DB7 代码可忽略新列；但 `tasks.del_seq` 缺失时旧代码不会读，安全。
- 产物阶段 A 的 `.part` 可在异常时清理，旧 zip 保留；阶段 B 的 DB 字段可回退为不持久化，zip 路径保持。
- 备份锁/轮出改动若需回滚，保留“外置失败不轮出、至少保留最新一份”的既有安全语义，不得回到更危险状态。

### 13.3 不实施声明

审查阶段没有修改任何代码/测试/配置/资产；随后的实施阶段按用户「逐项完成所有问题修复」授权落地 B0a–B4（代码、测试、文档与版本），并提交推送。删除/恢复/轮出/launchd 的破坏性操作未在本机执行；B 类仍待外部环境验证。

---

## 附录 A：探针记录

### A.1 基线

```text
gate: tests=311 failures=0 errors=0 skipped=0 time=34.853s exit=0
full: tests=886 failures=0 errors=0 skipped=0 time=77.089s exit=0
ruff: All checks passed! exit=0
doctor: exit=0, pipeline 2.6.0
```

### A.2 10.5pt 标题误判

```text
sizes [10.5, 10.5, 10.5, 10.5, 10.5, 10.5]
modal 10.0
heading level=1 '1. 第一项'
heading level=2 '1.1 范围'
heading level=1 '2. 第二项'
```

### A.3 DOCX 编号

```text
marker 'o' level 0 -> '1. 项目甲'
marker 'o' level 0 -> '1. 项目乙'
marker '2.1.' level 1 -> '  2.1. 子项'（S4 输出 '  -   2.1. 子项'）
```

### A.4 DOCX 首列编号表

```text
LINE '1.1 范围 | 内容甲' row=['1.1 范围','内容甲']
BLOCK heading level=2 '1.1 范围 | 内容甲'
BLOCK heading level=2 '1.2 目标 | 内容乙'
BLOCK paragraph '普通 | 内容丙'
```

### A.5 DOCX 文本框

```text
python-docx p.text: '前后'
read_docx lines: ['前后']
```

### A.6 异步 drift 复活

```text
D2 after delete: None
finish_ok returned drift= True
D2 after finish: {'doc_id': '2222222222222222', 'text': 'resurrected D2'}
task status: done
```

### A.7 产物/编辑不一致

```text
db text PATCHED-CONTENT
zip has PATCHED? False
# E4091 复转后
save_ir rejected E4091 as expected
zip contains EDITED? False
db block text: EDITED-BY-USER
```

### A.8 搜索窗口

```text
TAILMARK like 0 []
blocks len 8608
20k blocks: hit fts 19.1ms; miss like 4.4ms; EXPLAIN SCAN search_blocks
```

### A.9 快照轮出/v1/并发/CLI/netguard

```text
cron rotate removed: []
predelete still exists after cron rotate: True
restore_safe FAILED: ValueError legacy v1 container: use decrypt_bytes
4 threads to_external -> final .enc InvalidTag
argparse ['backup','--root','/x','restore',...] -> root=None
missing sendmsg -> AttributeError; orig keys connect/connect_ex/sendto
```

---

## 附录 B：代码位置索引

| 主题 | 位置 |
| --- | --- |
| DOCX 段落/文本框 | `doc2md/pipeline/s1_parse.py:529-610/780-815` |
| DOCX 编号 | `doc2md/pipeline/s1_parse.py:476-527` |
| DOCX 表格 | `doc2md/pipeline/s1_parse.py:722-758` |
| `modal_size` / `_heading_level` | `doc2md/pipeline/s3_structure.py:514-570` |
| heading/table 顺序 | `doc2md/pipeline/s3_structure.py:374-442` |
| marks | `doc2md/pipeline/s3_structure.py:187-212/464-477` |
| PDF 样式 | `doc2md/pipeline/s1_parse.py:278-300` |
| 链接 | `doc2md/pipeline/s3_structure.py:174-185`、`s4_render.py:82-105` |
| 分片结构 | `doc2md/pipeline/sharding.py:73-108` |
| 门禁噪声 | `doc2md/pipeline/s1_5_gate.py:23/92-113/203-218` |
| batch 消息 | `doc2md/pipeline/batch.py:69-85` |
| 同步删除守护 | `doc2md/api.py:329-370` |
| 异步落库 | `doc2md/worker.py:242-285` |
| 幂等 | `doc2md/scheduler.py:62-102` |
| 搜索 | `doc2md/storage/repo.py:13-17/283-338` |
| save_ir | `doc2md/storage/repo.py:66-131` |
| 删除/产物 | `doc2md/storage/repo.py:341-526` |
| 编辑 | `doc2md/edits.py:57-93` |
| 快照/轮出/恢复 | `doc2md/ops/backup.py:219-470` |
| cron | `doc2md/cron.py:139-190` |
| CLI | `doc2md/cli.py:66-110/281-315/483/512` |
| netguard | `doc2md/security/netguard.py:143-158` |
| drill | `doc2md/ops/drill.py:165` |
| config | `doc2md/config/loader.py:1-3/166-205` |
| 文档计数 | `README.md:219-220`、`docs/dev-plan.md:18/214/654-655/1359/1816` |
| pyproject | `pyproject.toml:7` |

---

## 附录 C：测试缺口（本轮新增）

| 行为 | 现有 | 缺口 |
| --- | --- | --- |
| 文本框/AlternateContent | 无 | 纯文本框、链接+文本框、双分支、顺序 |
| 10.5pt 标题 | 无 | 10.5/11.5pt + 单级编号 + CJK 正反例 |
| DOCX 编号 | decimal/lowerLetter | bullet `o`、多级 `2.1.`、Heading+numPr |
| 表格首列编号 | 无 | 3×2 表、首列编号、单行表 |
| marks 续行/缝合 | 仅 DOCX 整段/混合 | 两个方向的续行、分片缝合、PDF 混合 |
| 数字页门禁 | 无 | 纯数字表、混合表、真乱码 |
| 跨行链接 | 无 | 首行/末行/跨行、DOCX 对齐 |
| 分片 list_item | 无 | 跨片列表、真续行 |
| 异步 drift×删除 | 无 | 单文档/整源/入队前删除/并发 |
| 产物编辑/E4091 | 无 | PATCH 后导出、E4091 旧 zip 不变、并发同 doc |
| v1 恢复 | 只测 decrypt_bytes | CLI restore_safe v1、篡改、失败回滚 |
| 并发备份 | 无 | 2/4/8 路、锁、坏副本不保护 |
| CLI 参数顺序 | 无 | serve token/tokenizer/backup restore 六组 |
| paths.out | 功能测试 | API/导出/watch 自定义路径 |
| 幂等 | 现有基础 | 键复用/终态命中/async force |
| 搜索尾部/零命中 | 功能测试 | 8,608 字块、EXPLAIN、20k 块耗时 |
| netguard 缺失 sendmsg | 无 | 模拟 Unix 属性缺失 + 回滚 |
| CLI 权限 | 无 | convert/work/watch 的 700/600 |

---

## 结语

本轮的核心不是“又发现了很多边角”，而是三条主线仍未收口：

1. **删除/备份/恢复的最后一道数据安全承诺**：异步复活、快照永久留存、并发写坏备份、v1 不可恢复，任何一条在真实事故中都会放大成“以为删了/以为备份了”。
2. **产物与库面的事实源分裂**：编辑、重转、恢复、导出四条路径对“当前文档是什么”给出不同答案；应把 DB 作为唯一事实源，zip 通过原子发布成为可重建的分发缓存。
3. **常见 DOCX/PDF 结构在“测试全绿”之外仍错**：10.5pt、首列编号表、Word bullet/多级/标题编号、文本框，都是真实文档的高频形态，且现有黄金/单测没有覆盖。

建议按 B0→B1→B2→B3→B4 推进：先封死数据安全与恢复，再统一产物事实源，再做结构补全，最后做队列/性能与文档收口。每批退出都必须有本机 gate/full 0 skipped、G6/黄金分类、旧代码红新代码绿，以及 B 类提醒。

**B 类提醒**：B-6/B-7（Windows `sendmsg`/CI 腿/备份锁/CLI 参数）、B-10（外置卷恢复：v1、predelete/pre-restore、并发、产物）、B-13/B-15/B-1（真实 Word/中文 PDF/噪声语料）、B-12（长转换 × reclaim）需要本机以外环境或人工参与，请优先安排；B-4/B-5/B-9 的 macOS 部分已验，Windows 腿仍开放。
