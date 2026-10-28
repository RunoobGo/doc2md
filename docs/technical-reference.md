# Doc2md 项目技术文档

| 项 | 值 |
| --- | --- |
| 文档地位 | 综述与技术分析（**不是规格权威**，规格以 `docs/dev-plan.md` 为准） |
| 对应代码版本 | `PIPELINE_VERSION` **2.22.0** / `SCHEMA_VERSION` **14** / `DB_VERSION` **10** |
| 对应方案版本 | `PLAN_VERSION` **v3.55**（撰写时；本文与方案同轮收口） |
| 取证基线 | **CH-523 收口轮（B-53 第十三批：附录 C 契约对账红线文件的三条 gate 负断言补同主语正断言；零产品改动）**，本机 macOS arm64 / Python 3.15，gate **735 项全绿 / 0 skipped**（43.940s；零新增用例，构成与 CH-522/CH-521/CH-520/CH-519/CH-518/CH-517/CH-516/CH-515/CH-514/CH-513/CH-512/CH-511/CH-510/CH-509 相同，**十五轮同数**）、全量 **1575 收集 = 1559 passed / 16 skipped（真 skip 10 + xfail 6）**（98.856s）、覆盖率 **7,942 stmts / 678 miss = 精确 91.46%**（本轮直接复测；两个数与 CH-515～CH-522 全等 = `doc2md/` 语句零变化）、六层一条命令 `overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（逐层 ruff rc=0 0.04s / `L1` 43.78s / `L2` 96.34s / `L3` 0.74s / `L4` 四探针 0.79s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.82s）、语料面 **25/25** 未重烤、难度语料 6/10；本轮取证物是 `tests/test_ch505_contract_sync.py`（380→397 行，三条「不得出现」改为成对判据，收集 2 不变）与 `docs/qa/round4/negative-assertion-inventory.md`（407→419 行：第十三批进度行 + 第十三批判据纪律段 + 三条勾销改写）。#62 的容器是解析器本身：否定面「重复登记列表为空」与正例面「解析键数 + 扫描端点行数都 == 契约常量 21」合成一条 `(dups, len(rows), n_lines) == ([], 21, 21)`（空解析下的 `([], 0, 0)` 不再恒真）；#63/#64 合成 `(5, False, False)`——`POST /jobs` 错误列必存 `{400,404,413,415,503}` 5/5、409/422 各 0。red-first 变异五个（M-a 重复登记行 → 新行 127；M-b 空壳解析 → 新行 127 `([], 0, 0)`；M-c/M-d/M-e → 新行 168 的 `(5,1,0)` / `(5,0,1)` / `(4,0,0)`），对照 C-1 保持全绿；还原后 `docs/dev-plan.md`（`b7590164…`）与测试文件（`b02cdf03…`）md5 逐字节一致。下列为上一轮 CH-522 档取证：CH-522 收口轮（B-53 第十二批：本机收口三条 gate 负断言补同主语正断言；零产品改动）**，本机 macOS arm64 / Python 3.15，gate **735 项全绿 / 0 skipped**（45.950s；零新增用例，构成与 CH-521/CH-520/CH-519/CH-518/CH-517/CH-516/CH-515/CH-514/CH-513/CH-512/CH-511/CH-510/CH-509 相同，**十四轮同数**）、全量 **1575 收集 = 1559 passed / 16 skipped（真 skip 10 + xfail 6）**（100.011s）、覆盖率 **7,942 stmts / 678 miss = 精确 91.46%**（本轮直接复测；两个数与 CH-515～CH-521 全等 = `doc2md/` 语句零变化）、六层一条命令 `overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（逐层 ruff rc=0 0.1s / `L1` 47.32s / `L2` 104.22s / `L3` 0.83s / `L4` 四探针 0.73s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 8.01s）、语料面 **25/25** 未重烤、难度语料 6/10；本轮取证物是 `tests/test_ch496_local.py`（309→333 行，三条「不得出现」改为「否定面命中 0 + 同容器命中 > 0」成对判据，收集 13 不变）与 `docs/qa/round4/negative-assertion-inventory.md`（391→407 行：第十二批进度行 + 第十二批判据纪律段 + 三条勾销改写）。三条容器原本都是切片不自证的形态（同页相邻函数体、`cmd_serve` 体、chip 的 onclick 组），本轮升级为「切分命中恰好一次 + 分隔符非空 + 同容器必存成员 3/3」：#34 `showHistory` / `rollbackTo` 各 3/3、判据 `(3, 0)`；#35 `make_server(` / `server.server_port` / `print(` 3/3、判据 `(3, 0)`；#36 `QSTATE.filter = ` / `loadQueue()` / `(QSTATE.filter === st` 3/3、判据 `(3, 0)`。red-first 变异七个（M-a/M-b 各注入 `innerHTML` → 新行 249 红 `(3, 1)`；M-c 12 处 `document.createElement(` 改名 → 新行 249 红 `(2, 0)`；M-e 注释注入 `app.run(host=` → 新行 286 红 `(3, 1)`；M-f 注入 `renderQueue(QSTATE.tasks)` → 新行 329 红 `(3, 1)`；M-d/M-g 改名先被同函数既有正例断言拦在旧行 281 / 327 = 判据归属），对照 C-1 保持全绿；同批立出**正例成员不得取自注释**（chip 切片里的 `status=` 只出现在注释中，换实现串后 M-f 才落新判据）；还原后 `doc2md/static/editor.html`（`b8fe2d0e…`）与 `doc2md/cli.py`（`bfea7fdb…`）md5 逐字节一致。**CH-522 的远端闭合（本轮台账回填）**：run `40681623636`（head `4dc1d96`）六作业全绿——`windows-suite` 门禁层 **735 passed / 0 failed / 0 skipped / 840 deselected**（144.37s）+ Windows 全量 **1554 passed / 15 skipped / 6 xfailed**（306.16s）、ubuntu `full-suite` **1558 passed / 11 skipped / 6 xfailed**（205.68s）、`merge-gate` 三档各 **735 passed / 840 deselected**（3.11 81.22s / 3.12 101.63s / 3.13 92.81s）、`lint` 15s，两侧 `passed + skipped + xfailed` 合计仍 1575。下列为上一轮 CH-521 档取证：CH-521 收口轮（B-53 第十一批：审阅页文档清单红线文件的三条 gate 负断言补同主语正断言；**零产品改动**），本机 macOS arm64 / Python 3.15，gate **735 项全绿 / 0 skipped**（45.917s；零新增用例，构成与 CH-520/CH-519/CH-518/CH-517/CH-516/CH-515/CH-514/CH-513/CH-512/CH-511/CH-510/CH-509 相同，**十三轮同数**）、全量 **1575 收集 = 1559 passed / 16 skipped（真 skip 10 + xfail 6）**（100.357s）、覆盖率 **7,942 stmts / 678 miss = 精确 91.46%**（本轮直接复测，不沿用上一档；两个数与 CH-515/CH-516/CH-517/CH-518/CH-519/CH-520 全等 = `doc2md/` 语句零变化）、六层一条命令终轮 `overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（逐层 ruff rc=0 0.13s / `L1` 48.59s（同轮 `--junitxml` 独立采样 45.917s）/ `L2` 105.46s（同轮 `--junitxml` 独立采样 100.357s）/ `L3` 0.81s / `L4` 四探针 0.73s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 7.55s）、语料面 **25/25** 未重烤、难度语料 6/10；本轮的取证物是 `tests/test_ch503_docs_panel.py`（246→278 行，三条「不得出现」改写为「否定面命中 0 + 同容器/同夹具命中 > 0」的成对判据，收集数 9 不变）与 `docs/qa/round4/negative-assertion-inventory.md`（375→391 行：第十一批进度行 + 第十一批判据纪律段 + 三条勾销与优先级表行号改写）。三条的容器切片原本都「边界不自证」（`split(锚点)[1]` 取到文件尾），本轮改为「切分命中恰好一次 + 分隔符非空 + 同容器必存成员 4/4」：#48 判据 `(4, 0)`、#50 判据 `(4, 0)`（`save_ir` 在 `cli.py` 别处共 5 次，0 指这条路径不落库）、#49 同一串摘掉 `esc()` 后必须恰好命中 2（`(0, 2)`）。red-first 六个变异各自转红（注入 `QSTATE.tasks` → 新行 85；删 `DSTATE.hasMore` → 同落新行 85 的 3/4；整行删锚点 → `assert _sep`；字段集漏 `format` → 只打新行 135，隔离出正例面的归属；cmd_convert 调 `save_ir` 落库 → 新行 274；`run_batch` 成员改名 → 同落新行 274），另设对照变异 C-1（loadDocs 体内加无害注释）保持全绿、按 CH-515 纪律不计入牙齿证据；同批实测出**保前缀改名不是切断锚点**——把 `function dAutoToggle` 改成 `dAutoToggleRenamed` 时 `partition` 仍在前缀切开、变异 rc=0，要真正打破边界必须整行删除或改写。还原后 `doc2md/static/editor.html`（`b8fe2d0e…`）与 `doc2md/cli.py`（`bfea7fdb…`）md5 与改前逐字节一致。**远端台账（CH-517 回填 + CH-518 闭合）**：CH-510 的 run `40541731995` 六作业全绿（`windows-suite` 门禁层 **735 passed / 0 failed / 0 skipped / 840 deselected** 140.92s、Windows 全量 **1554 passed / 15 skipped / 6 xfailed** 288.91s、`merge-gate` 三档各 **735 passed / 840 deselected**、ubuntu `full-suite` **1558 passed / 11 skipped / 6 xfailed**）；CH-511～CH-516 六推（run `40545285156` / `40548764309` / `40554178293` / `40557797563` / `40561755458` / `40566970109`）五作业绿而 `windows-suite` 门禁层逐轮同为 **1 failed / 734 passed / 840 deselected**，失败项六轮同一个 `tests/test_v24_gates.py::test_gate_test_env_neutralization`——**同一份 735 项在 Linux 三档 `merge-gate` 与 ubuntu `full-suite` 全绿、只在 Windows 红**，这正是宿主耦合而非产品缺陷的判据形态；**CH-517 的 run `40572366807` 六作业全绿**：`windows-suite` 门禁层 **735 passed / 0 failed / 0 skipped / 840 deselected** 135.01s + Windows 全量 **1554 passed / 15 skipped / 6 xfailed** 291.21s（门禁层转绿后 Windows 全量步骤才重新执行，该层自 CH-510 档以来的第一份新鲜证据）、`merge-gate` 三档各 **735 passed / 840 deselected**（3.11 92s / 3.12 105s / 3.13 77s）、ubuntu `full-suite` **1558 passed / 11 skipped / 6 xfailed** 134.33s。修法、red-first 变异见 dev-plan 第十一章 CH-519 行。**CH-518 远端闭合**：run `40576167209`（head `c34bfc7`）六作业全绿——`windows-suite` 门禁层 **735 passed / 0 skipped / 840 deselected**（141.72s）+ Windows 全量 **1554 passed / 15 skipped / 6 xfailed**（279.47s）、ubuntu `full-suite` **1558 / 11 / 6**（141.03s）、`merge-gate` 3.11 111s / 3.12 100s / 3.13 115s、`lint` 17s；**CH-519 的远端闭合（台账回填）**：run `40583239078`（head `37bcbd9`）六作业全绿——`windows-suite` 门禁层 **735 passed / 0 skipped / 840 deselected**（158.46s）+ Windows 全量 **1554 passed / 15 skipped / 6 xfailed**（297.65s）、ubuntu `full-suite` **1558 / 11 / 6**（201.44s）、`merge-gate` 三档各 735 passed / 840 deselected（3.11 85.25s / 3.12 79.42s / 3.13 76.09s）、`lint` 18s，两侧合计仍 1575；远端完成态以 `gh run view <id> --json status,conclusion,jobs` 为准（本机 `gh run watch` 会提前打印别的 run 的 success）；**CH-520 的远端闭合（台账回填）**：run `40672220158`（head `a272fe3`）六作业全绿——`windows-suite` 门禁层 **735 passed / 0 skipped / 840 deselected**（106.36s）+ Windows 全量 **1554 passed / 15 skipped / 6 xfailed**（236.88s）、ubuntu `full-suite` **1558 / 11 / 6**（136.43s）、`merge-gate` 三档各 **735 passed / 840 deselected**（3.11 64.59s / 3.12 69.47s / 3.13 102.53s）、`lint` 18s，两侧合计仍 1575；**CH-521 的远端闭合（台账回填）**：run `40678213239`（head `ab909f9`）六作业全绿——`windows-suite` 门禁层 **735 passed / 0 skipped / 840 deselected**（138.26s）+ Windows 全量 **1554 passed / 15 skipped / 6 xfailed**（283.00s）、ubuntu `full-suite` **1558 / 11 / 6**（197.84s）、`merge-gate` 三档各 **735 passed / 840 deselected**（3.11 84.37s / 3.12 99.92s / 3.13 97.42s）、`lint` 16s，两侧合计仍 1575 |
| 覆盖的历史 | 导入提交 `23f8906` 起、**截至 `0b209f8` 共 139 个提交**、`CH-422 … CH-523`、十九轮全面审查、四轮 QA 优化（第四轮实跑）、实机自动测试体系 CH-501 |
| 代码口径 | `doc2md/` 包 **15,802** 行 Python / 47 个 `.py`（其中 6 个是子包 re-export 门面） |

---

## 0 本文的地位与读法

`docs/dev-plan.md`（v3.55，2,369 行）是这个项目**唯一的规格权威**：主题篇一–九章给当前有效规格，附录 A–G 给配置键 / CLI / API / 错误码 / 数据库 / 门禁断言 / 模块清单的查询表，登记篇十–十二章给逐条变更与实测证据。本文不与它竞争，只做它结构上做不到的四件事：

1. **按「理解一个系统」的顺序重排**——dev-plan 是围绕「需求→契约→计划→登记」组织的，本文围绕「需求→架构→选型→实现→难点」组织，适合新读者一次读完建立心智模型。
2. **把难点抽成独立目录**——审查报告按轮次分册（截至第十八轮共 12 份），有价值的技术判断散落在其中与 12.7 的 B 项表里。本文第六章把它们重写成「现象 / 根因 / 解法 / 落点 / 证据 / 残留」的统一形态。
3. **给出四向映射**——需求 ID ↔ 实现落点 ↔ 门禁断言 ↔ 文档章节（第二、九章）。
4. **总结开发史**——135 个提交（截至 `12f711d`；提交数每轮都涨，故本文的计数一律锚定到具名提交而非「当前值」。CH-504～CH-507 的代码已随 `7b297df` 推送、CH-508 随 `da1d3f7`、CH-509 随 `ae17fbd`、CH-510 随 `c2f0a51`、CH-511 随 `3b178ee`、CH-512 随 `eb9bdaf`、CH-513 随 `a1cbdb2`、CH-514 随 `3d22f7d`、CH-515 随 `b1c49c4`、CH-516 随 `ac2c8b9`、CH-517 随 `f8d706f`、CH-518 随 `c34bfc7`，CH-519 随本轮收口提交，CH-509 的远端闭合已随 `39a8175` 回填（run `40529390636` 六作业全绿），CH-510～CH-516 六轮的远端 run 取证已随 CH-517 回填——六轮里 CH-510 六作业全绿、CH-511～CH-516 各有一项 `windows-suite` 门禁层的宿主耦合红（同一个代理注入判据），由 CH-517 在测试侧根治而非加 skip；CH-517 的 run `40572366807` 六作业全绿、Windows 门禁层与全量层同时转绿，该轮远端闭合已随 CH-518 回填（run `40576167209`，head `c34bfc7`）；CH-510～CH-516 与 CH-518、CH-519 是同一条纪律的九批存量补位——负断言必须配同主语正断言）、十九轮审查的脉络与质量趋势（第七章）。

冲突处理口径：**任何数字或结论与 dev-plan 不一致时，以 dev-plan 与实测为准**；本文的行号锚点在写作时逐个 `grep` 复核过，但代码会移动，行号只作导航不作断言。

---

## 1 项目定位与设计约束

### 1.1 一句话

一个**完全离线**的文档转 Markdown 引擎：输入 PDF / DOCX / 旧版 DOC，输出结构化 Markdown 与可校验的 zip 产物，并且自带任务队列、全文检索、块级编辑与回滚、加密备份、常驻服务与日/周运维流程——全部跑在一台不需要网络的普通机器上。

### 1.2 三条设计约束（dev-plan §1.1）

这三条不是价值观宣示，每一条都有对应的机器可验机制，遇到取舍时按此顺序裁决：

| 约束 | 含义 | 落地机制 |
| --- | --- | --- |
| **确定性优先，逐字节可复现** | 同一输入在任何机器上产出同一字节 | 产物等价纪律 G6（逐条目 `sha256` 比对，禁用整包 md5）；语料回归 25/25 逐字节一致；`llm.mode=offline` 默认（`doc2md/llm.py:457`） |
| **资源受限是默认前提** | 目标机是 4–8 核 / 8–16GB 的普通机器，不是开发机 | 有界内存（NFR-21，`chunk_pages=32`）、目录硬截止（深度 20 / 5,000 文件 / 10s）、请求体 32MiB 上限（`doc2md/api.py:75`） |
| **内部自用也要设边界** | 单机自用不等于没有安全模型 | 令牌角色矩阵（`doc2md/auth.py:87`）、数据边界（日志/事件/留痕禁正文，NFR-20）、目录与文件系统五道守护（§3.6） |

### 1.3 目标运行环境

dev-plan §1.3 定义三类，工程上「A 类是瓶颈，B 类是主力，C 类是取证地」：

- **A｜中低端 Windows 11**：4–8 核 / 8–16GB / GBK 控制台 / **无 fork**（这决定了并行只能用 `spawn`，见 §6.4）
- **B｜Mac mini M4 16GB**：**无 cron**（这决定了日/周流程必须自带 `cron` 子命令 + launchd 档，见 §5.11）
- **C｜开发与 CI 环境**：Linux / GitHub Actions，Python 3.11–3.15 矩阵

### 1.4 明确的非目标

不做（探测到即诚实降级，不假装支持）：图片与公式还原、扫描件 OCR、旋转页 / RTL 页的版面还原（仅探测并给 **W1046** 告警）、合并单元格的双向还原（拍平并给 **W1045**）、行内跨度级链接（保持块级链接模型，≤1 link/block）、远程与多用户部署。头部页眉页脚之外的复杂版式（多栏超过两栏、图文环绕）同样不在承诺面。

「非目标」在这个项目里是**有牙齿的口径**：每一项都对应一个可观测的告警码或一条测试断言，而不是一句免责声明——因为「静默地把不支持的东西转成看起来支持的输出」被列为最严重的缺陷形态（见 §6.8 的 B-43/B-44）。

---

## 2 设计需求

### 2.1 功能需求（dev-plan §2.1）

逐条给出落点。`file:line` 是主要实现位置，「门禁」列指最贴近该需求的红线断言面。

| 需求 | 内容 | 实现落点 |
| --- | --- | --- |
| FR-101 | 文本型 PDF 解析（版面 / 表格 / 双栏 / 链接） | `doc2md/pipeline/s1_parse.py:1375`（`read_any`）→ `:457`（`_extract_two_column`） |
| FR-102 | DOCX 解析（修订、脚注、单元格内段落界） | `s1_parse.py:867`（段落）、`:959`（修订探测）、`:1047`（脚注尾注）、`:1130`（表格行） |
| FR-103 | 文本层体检门禁（不可转换就拒绝，不出半成品） | `doc2md/pipeline/s1_5_gate.py:276`（`check`），退 **E4221** |
| FR-104 | 结构分析（标题层级、列表、代码块、引用、表格） | `doc2md/pipeline/s3_structure.py:623`（`analyze`） |
| FR-105 | Markdown 渲染 | `doc2md/pipeline/s4_render.py:109`（`render`）+ 同文件 `:238` 的契约自检 |
| FR-106 | 产物打包（zip：正文 + IR + 报告 + manifest） | `doc2md/pipeline/convert.py:402` 一带的 `package` |
| FR-107 | 链接渲染分级（外链 / 内部锚 / 关闭态） | `s4_render.py` 的 `link_internal` 参数族 |
| FR-108 | marks（加粗 / 删除线等）渲染分级 | `s4_render.py` 的 `preserve_strike` 等；决策见 `docs/adr-o11-inline-marks.md` |
| FR-111 | watch folder 稳定投递 | `doc2md/security/watcher.py:41`（`StabilityTracker`，size+mtime 双稳才提交） |
| FR-125 | 事件通道纪律（只发结构化事件，禁正文） | `doc2md/events.py`；口径在 dev-plan §6.3 |
| FR-126 | 大文档两阶段分片 | `doc2md/pipeline/sharding.py:28`（`shard_plan`）+ `:277`（`stitch_shards`） |
| FR-127 | 文件名消毒与路径安全（**失败即报错，不降级改名**） | `doc2md/security/names.py:52`，退 **E4003** |
| FR-128 | 文档删除级联（产物 / 库 / 索引同生同死） | `doc2md/storage/repo.py:443`（`delete_doc`）、`:514`（`delete_source`）、`doc2md/artifacts.py:204`（孤儿清理） |
| FR-129 | 目录投递防护（深度 / 宽度 / 文件数硬截止） | `doc2md/security/dirguard.py:17`（`WalkLimits`）+ `:175`（`scan_or_raise`），退 **E4133** |
| FR-203 | 块级编辑与乐观并发 | `doc2md/edits.py`，冲突退 **E4251** |
| FR-204 | 全文检索读侧 | `doc2md/storage/fts.py:73`（`build_match`）+ `:108`（`like_escape` 兜底） |
| FR-205 | 产物 Markdown 直读 | API `GET /docs/<id>/markdown`（`doc2md/api.py:789`） |
| FR-206 | 修订回滚 | `api.py:853` 的 `.../blocks/<id>/rollback` |
| FR-207 | 令牌生命周期（签发 / 角色 / 吊销） | `doc2md/auth.py`、`api.py` 的 `/tokens` |
| FR-208 | 搜索索引刷新（与写同事务） | `repo.py:84`（`save_ir`）+ `:185`（`reconcile_search`） |
| FR-209 | 运行期配置覆写与热重载 | `api.py` `/settings`、`/system/reload` + `doc2md/config/loader.py:45` 的禁改键 |
| FR-210 | 审计读回 | `api.py` `/docs/<id>/audit`、`/audit/deletions` |
| FR-211 | 审阅页（浏览器内看结果） | `doc2md/static/editor.html`（677 行原生 JS） |
| FR-404 | 修补助手（LLM 语义修复） | `doc2md/llm.py:256`（`OpValidator`）/ `:457`（`offline_llm`）/ `:636`（`LlmClient`） |
| FR-607 | 本地快照 + 外置加密副本 | `doc2md/ops/backup.py:37`（`MAGIC_V2` 容器） |
| FR-701 | 批量转换 | `doc2md/pipeline/batch.py:250`（`run_batch`） |
| FR-702 | 任务队列与异步消费 | `doc2md/worker.py:67`（`claim`）+ `doc2md/cli.py` 的 `work` |
| FR-703 | 环境自持诊断 | `doc2md/cli.py` 的 `doctor`（离线断言 / 后端可用 / 版本自洽） |

### 2.2 非功能需求（dev-plan §2.2）

注意一个易踩的口径：**不存在 NFR-2**，隐私前提由 NFR-18 / NFR-20 与里程碑 M8 承担；「零遥测」也不是独立条款，它由 NFR-1（离线）+ `run.offline_assert` + netguard 三处机制共同保证。

| 需求 | 内容 | 落地与验证 |
| --- | --- | --- |
| NFR-1 | 完全离线 | `run.offline_assert` 为冻结键（不可经 `PUT /settings` 改）；`doc2md/security/netguard.py` 猴子补丁拦截 `connect/connect_ex/sendto/sendmsg` |
| NFR-6 | 磁盘安全（写前有余量） | `doc2md/ops/diskguard.py`：用 `available` 而非 `free` 判定（Windows 侧退 `free`） |
| NFR-9 | 权限分级 | reader / editor / admin × 动作矩阵（`doc2md/auth.py:22` 角色、`:30` `MATRIX`、`:87` `check`） |
| NFR-14 | 日报 | `doc2md/cron.py:130`（`run`）链式产出，正文禁入 |
| NFR-18 | 备份加密（外置介质强制密文） | AESGCM 流式 + scrypt；`ops/backup.md` 细则 |
| NFR-19 | 日志轮转 | `doc2md/ops/logging.py:121`（`check`）：按 `logs.rotate_mb` 或 UTC 日切；launchd 捕获档就地截断保尾（CH-481） |
| NFR-20 | 数据边界（日志 / 事件 / 留痕不含正文） | `events.py` 只发结构化字段；`work` 目录 0700、覆写文件 0600（`perms.py:90`） |
| NFR-21 | 有界内存 | `chunks.chunk_pages=32` 重开窗口 + 逐页缓存回落 → 峰值与页数解耦；实测 1290MB → 1025MB → 176MB |
| NFR-22 | 吞吐（分档） | 8×40 页 15.5s → 7.6s；13 作业入队 13ms；页区间并行实测 2.79×（CH-500 定档） |
| NFR-23 | 目标机型可用 | Windows 真机与 CI `windows-suite` 腿；GBK 控制台与无 fork 前提 |
| NFR-24 | 可复现性 | 语料回归 25/25 逐字节；黄金资产逐条目 `sha256` |

### 2.3 需求→实现→门禁 的追溯层

CH-501 把「需求是否真的有断言盯着」做成了可计算的一层：`tests/qa/trace.py`（408 行）解析 dev-plan 的需求表、测试的 `pytestmark`/marker 与代码落点，输出「哪些 FR/NFR 只有文档没有断言」。这一层的判据刻意**不进门禁**（避免为凑覆盖率写空断言），只作为信息层报告——这条口径见 dev-plan §8.7 的三条不可让之一「效果分不进红线」。

---

## 3 总体架构

### 3.1 分层与依赖纪律

包内 47 个 `.py` 按依赖方向分五层，**只允许上层依赖下层**（dev-plan §3.6 的 8 条结构纪律是唯一的权威口径，附录 G 逐模块列了真实导入点）：

```
入口层      cli / api                                           ← 唯一允许出现「用户视角」的地方
编排层      worker / cron / scheduler / edits / auth            ← 只认队列、配置与库
领域层      pipeline.* / storage.*                              ← 转换主链路与仓储
支撑层      security.* / ops.*                                  ← 只认路径、字节与守护规则
基座层      errors / llm / config.* / ir.model / 包根常量        ← 不认识任何业务概念
```

**CH-504 / R19-ARCH-8** 校正：本段原另立一套层名，与 dev-plan §3.6 的实测 DAG 冲突（`storage.*` 在此列支撑层、在 §3.6 列领域层；`security.*` 在此列基座层、在 §3.6 列支撑层；`llm` 在此列领域层、在 §3.6 列基座层；`edits` / `auth` / `qa_eval` 在此根本没列）。冲突不只是措辞分歧：按原来那套，`artifacts` 属支撑层而 `pipeline.*` 属领域层，于是 `artifacts.py::rebuild_artifact` 在函数体内 `from .pipeline.convert import package` 就成了**下层引用上层**，把本段自己写下的「只允许上层依赖下层」当场证伪。dev-plan §3.5 把 `events.py` / `artifacts.py` / `qa_eval.py` 定为**根目录中立模块**——被各层引用、不属于任何子包、因而不参与分层排序——正是为了让 CH-492（A-1）抽出它们之后不再产生环。两份文档现在共用 §3.6 那一张图；`static/editor.html` 不参与 Python 依赖图，单独标为展示层。按此口径 AST 实测 185 条包内相对导入边，**向上引用 0 条**。

三条被红线钉住的具体纪律：

- **抽取层不得依赖理解层**：`s1_parse` / `s1_5_gate` / `sharding` 不能 import `s3_structure` 或 `s4_render`——否则「先看结构再决定怎么读」的循环依赖会让分片等价性无法证明。
- **`s2` 不是文件而是配置名空间**：包内没有 `s2_*.py`，`[s2]` 分片参数由 `pipeline/sharding.py` 消费（dev-plan §3.5）。
- **门面文件不算模块**：6 个子包 `__init__.py` 只做 re-export，共 72 行，附录 G 的行数口径明确把它们从「40 行主表」里排除。

### 3.2 数据流

```
输入路径
  → sniff()                     魔数判型（不靠扩展名）        s1_parse.py:135
  → hash_of_path()              流式算 doc_id / source_key    ir/model.py:28
  → read_any()                  抽取为 RawPage 流             s1_parse.py:1375
  → s1_5_gate.check()           文本层体检，坏页即拒           s1_5_gate.py:276
  → shard_plan()                >300 页切段，段间重叠 2 页     sharding.py:28
  → modal_size()                全局正文众数（只算一次）       s3_structure.py:1050
  → analyze(base_size=…)        逐片结构分析                   s3_structure.py:623
  → stitch_shards()             缝合；任何异常降级单片         sharding.py:277 / convert.py:292
  → LLM 修复层                  五算子白名单 + OpValidator     llm.py:256
  → s4_render.render()          Markdown 生成 + 契约自检       s4_render.py:109
  → package()                   zip 产物（原子写 + 父目录 fsync）
  → repo.save_ir()              库与 FTS 同事务落盘            repo.py:84
```

`_convert` 在 `doc2md/pipeline/convert.py:109` 把这条链收成一个函数；`docs=` / `blocks=` 的计数从这里来。

### 3.3 IR 契约（系统的唯一中间语言）

`doc2md/ir/model.py:20` 固定了七种块类型，**封闭集**，新增类型是契约变更：

```python
BLOCK_TYPES = ("heading", "paragraph", "list_item", "table", "code", "quote", "page_break")
```

- `Block` 携带 `id / type / text / level / font / marks / links / page / flags / provenance / immutable / revision`；另有 `line_trail`、`row_shape` 两个**几何暂存字段，永不序列化**——这是「抽取层用几何、渲染层只用 IR」纪律的技术支点。
- **标识符纪律**（`ir/model.py:23-42`，dev-plan §5.1 标为「不可动摇」）：`doc_id` 与 `source_key` **只哈希内容，文件名一律不参与**。同名换内容 → 新 doc；换目录同名同内容 → 同一 doc。这让「移动文件」不产生重复文档，也让删除可以按内容指纹彻底级联。
- 两个版本计数器**故意不合并**：`SCHEMA_VERSION 14`（IR 与库表结构）与 `DB_VERSION 10`（迁移步骤）语义不同，合并过一次就在 CH-469 翻过车（见 §6.5）。

### 3.4 存储与调度

| 面 | 关键实现 | 设计要点 |
| --- | --- | --- |
| 连接与迁移 | `doc2md/storage/db.py:218`（`Store`）、`:342`（`_migrate`）、`:257`（`_set_wal`） | 拒绝对比库更新（`user_version > DB_VERSION` 直接失败）；同版本也**补列**，不只看版本号；WAL 切换限时重试 + 未确认即 fail-closed |
| 写路径 | `repo.py:84`（`save_ir`，可复用外层事务）、`:67`（事务外预计算 FTS 行） | JSON 序列化与 `cjk_aux` 是写锁时长主因，因此搬到事务外 |
| 检索 | `fts.py:49`（`cjk_aux` 辅助列）、`:73`（`build_match`）、`:108`（`like_escape`） | FTS5 `unicode61` 对 CJK 无效 → 自建 n-gram 辅助列；FTS 不可用时退 LIKE 且必须转义 `%_\[` |
| 队列 | `worker.py:67`（`claim`，owner+lease 同事务）、`:225`（`_lease_keeper` 续约线程）、`:105`（`reclaim_stale`） | 回收谓词三分支：租约过期 / 无租约且超 `stale_job_minutes` / 超 `job_lease_cap_minutes` 硬上限（默认 240，0=关） |
| 批量 | `batch.py:250`（`run_batch`，`paths` 与 `plans` 二选一） | 有界进程池；串行快路径只在**未设** `perf.batch_file_timeout_s` 时启用 |

### 3.5 服务面

18 个 API 路径（`/`、`/healthz`、`/jobs`、`/jobs/<id>/events`、`/docs`、`/docs/<id>`、`/docs/<id>/markdown`、`/docs/<id>/blocks/<id>` 及其 `/rollback`、`/docs/<id>/audit`、`/audit/deletions`、`/search`、`/sources/<key>`、`/events`、`/settings`、`/system/reload`、`/tokens`、`/tokens/<token>`）+ 17 个 CLI 子命令（`convert / batch(→work) / work / watch / serve / docs / list / show / search / export / backup / restore / verify / drill / token / cron / doctor / tokenizer`）。

错误码是**冻结段位**（dev-plan §5.2、附录 D）：`E4001 / E4003 / E4011 / E4031 / E4041 / E4091 / E4092 / E4131 / E4133 / E4151 / E4221 / E4251 / E5031 / E5041 / E5000`。三条反直觉但被钉死的口径：`E5031`（存储错误）的 HTTP 是 **500 不是 503**；`E5041` 被池忙复用并以 `detail.reason="sync_pool_busy"` 区分（独立码 `E5051` 已删且由红线守着不许复活）；`?token=` 是**被拒绝的设计**而非待补功能（§6.6）。

### 3.6 安全边界（五道守护及其真实强度）

| 守护 | 强制内容 | 诚实边界 |
| --- | --- | --- |
| `doc2md/security/perms.py:69` `check_applied` | chmod 后**读回值必须等于请求值** | 早期启发式只看「其他位有没有清零」，在 FAT32/exFAT 上恒真（§6.6）；Windows 走 `icacls` |
| `security/dirguard.py:175` | 深度 / 宽度 / 文件数硬截止 + 软链跟随后做 realpath 包含判定 | 可移动盘也允许软链，所以这层不能省 |
| `security/netguard.py` | 拦截四个 socket 系统调用；白名单**只收 IP 字面量**，`localhost` 被刻意移出（hosts 污染）；DNS 失败即拒绝 | **进程内猴子补丁，不是 OS 边界**——被恶意依赖绕过是已知事实，按护栏而非沙箱定位 |
| `security/names.py:52` | NFC → 剥离路径 → 字符白名单 → Windows 保留名 → UTF-8 字节预算 | 消毒失败抛 **E4003**，不设「自动改名继续跑」的降级路径 |
| `security/watcher.py:41` | size + mtime 双稳才投递 | 轮询实现（不引入 watchdog，守住 5 依赖闭环） |

### 3.7 配置级联

`DEFAULT_TOML` → `$DOC2MD_HOME/doc2md.toml` → `./doc2md.toml` → `--config <path>`（显式路径缺失即报错），深合并（`doc2md/config/loader.py:189` 一带）。`SPEC` 表校验类型 / 区间 / 枚举，未知键**只告警不失败**；lite / standard / strict 三档 profile 只覆写用户未显式设置的键（`:27`）。键集三处同源（SPEC / DEFAULT_TOML / example.toml）由红线 `test_spec_defaults_and_example_share_one_key_set` 钉住。`RUNTIME_IMMUTABLE_KEYS`（`:45`）冻结 `run.offline_assert`、`paths.out/logs`、`api.allow_remote_bind`——最后这条防的是「先远程绑定再改回」的两步逃逸。

---

## 4 技术选型

### 4.1 为什么是这五个依赖

`pyproject.toml:23-28` 声明且仅声明五个运行时依赖：`pdfplumber>=0.11`、`python-docx>=1.1`、`cryptography>=42`、`Flask>=3`、`werkzeug>=3`。选它们的理由各有一条不可替代的技术原因，而非习惯：

| 选型 | 原因 | 代价与补偿 |
| --- | --- | --- |
| **pdfplumber 0.11.10** | 唯一稳定给出 **逐字符 bbox**（`chars`）的纯 Python 库；本项目所有版面判断（双栏、字号众数、缩进重建、家具剥离）都建立在字符级几何上 | 慢且吃内存 → `chunk_pages=32` 重开窗口 + 分片 + 页区间并行；`s1_parse.py` 里按需懒导入 |
| **python-docx 1.2.0** | 直接暴露 OOXML 元素树，可按编号、文本框、脚注逐层遍历 | 不把 DOCX 映射成「页」→ 每 40 行合成一页，让 IR 的 `page` 字段对两类源同构 |
| **SQLite + FTS5**（标准库） | 单文件、无服务进程、事务能覆盖「文档 + 块 + 索引 + 事件」四张表同时落盘 | `unicode61` 对中文失效 → 自建 `cjk_aux` n-gram 辅助列；无 fork 前提下的并发 → WAL + busy_timeout |
| **Flask 3.1.3 threaded** | 18 个端点全是短请求，不需要异步框架；threaded 模式够用且部署面最小 | 不用 ASGI 意味着不能上 uvicorn 生态——本项目不需要，见 §4.2 |
| **cryptography 50.0.1（AESGCM + scrypt）** | NFR-18 要求外置介质强制密文，且不能引入自定义密码学 | scrypt `N=2^14` **刻意低于** OWASP 建议的 2^17，因为容器 header 不带 KDF 参数，抬高会让旧库不可解——这是一个「兼容性与推荐强度」的显式取舍，写在 `doc2md/ops/backup.py:36-43` |
| **llama-server（可选，仅 127.0.0.1）** | 语义修复需要模型，但模型必须能在 16GB 机器本地跑 | `temperature=0`；默认 `mode=offline` 用确定性启发式顶上，模型不在场时功能不退化（§5.5） |

标准库承担的额外两块：**自研 JSONL 轮转写手**（不用 `logging`，因为要结构化字段 + 多线程写锁 + 按大小或 UTC 日切，`doc2md/ops/logging.py`）与 **`tomllib` 只读**（不写 TOML，配置持久化走库表 + 覆写文件）。

所有第三方库都是**函数内延迟导入**（`s1_parse.py:247`、`:1305`、`api.py:156`），使 `doc2md --help` 与 `doctor` 在不装重依赖的机器上也能起来——这是低配机前提下的启动性能设计。

### 4.2 明确未采用（及理由）

dev-plan §3.7 末尾这张表的价值在于「每一项都写了代价」，而不是「我们不需要」：

| 未采用 | 理由与接受的代价 |
| --- | --- |
| ORM（SQLAlchemy 等） | 手写级联是唯一机制，表重建成本 > 收益；代价是删除逻辑必须靠断言防孤儿 |
| ASGI / 异步框架 | 短请求场景收益为零；代价是未来要推流时需重估 |
| OpenAPI 生成 | 端点少且面向自用；代价是契约靠附录 C 的文档红线维护 |
| Celery / RQ | 已有进程内队列 + `work` 常驻消费者；代价是无可视化面板 |
| `logging` 标准库 | 需要 JSONL 与自定义轮转；代价是自己处理写锁与时区 |
| mypy / black | 与「零依赖 + 逐字节产物」取向冲突；代价是类型面靠约定与红线测试 |
| Docker | 目标机是 Windows/Mac 桌面；代价是环境自持全靠 `doctor` |
| 前端构建 / SSE | `editor.html` 是单文件原生 JS，轮询取事件；代价是无组件化 |

### 4.3 四个版本计数器与各自的变更判据

这是本项目**最容易误伤也最有纪律**的一块，四个数字互不联动：

| 计数器 | 当前值 | 变更判据 |
| --- | --- | --- |
| `PIPELINE_VERSION` | 2.22.0 | **只有「交付产物字节发生变化」才升**（决策 D12）。2.21→2.22 一项（CH-508）：`_DocxNumbering` 接上三种间接编号形态——样式级 `numPr` 沿 `basedOn` 链继承、`w:abstractNum/w:numStyleLink` 委托编号样式、`w:lvl/w:pStyle` 反向绑定；环与悬空链保守回「无编号」，名为 `Heading` 的无数字样式不再兜底成 1 级。带这些构造的 DOCX 交付**内容**变化；25 篇语料逐字节不变，红线用合成样本钉住（`tests/test_ch508_indirect_numbering.py` 5 项）。2.20→2.21 一项（CH-506）：`w:num` 的 `w:lvlOverride`（含 `startOverride`）按 ECMA-376 §17.9.15 覆盖该 `numId` 的抽象级定义——覆盖层置 `numFmt="none"` / 空 `lvlText` 时不再凭空造出标记，`startOverride` 不再被忽略（`s1_parse.py::_DocxNumbering.__init__`），带该构造的 DOCX 交付**内容**变化；当前 14 份 DOCX 语料零命中，红线用合成样本钉住。2.19→2.20 三项（CH-504）：交付 zip 改按钉死的 `ZipInfo`（`convert.py::package` 显式构造，时间戳与 `create_system` 都取常量），同一份 IR/md/meta 两次打包 sha256 自此相同——交付物哈希成为其内容的函数（交付**容器**变化）、等宽行的链接标注不再被「等宽→代码块」分支吞掉，URL 与内部 GoTo 目标回到 IR 与产物（交付**内容**变化；本机 31 篇夹具扫描 `mono=6 link=2 BOTH=0`，属潜伏缺陷，红线用合成 `Line` 钉住）、`numFmt="none"` / 空 `lvlText` 不再凭空生成 `%1.`（`s1_parse.py::_DocxNumbering.marker` 区分「元素在但为空」与「元素缺失」；libreoffice 写出的 DOCX 此前每个标题都被加 `1. ` 且零告警——实测同一份文档经 `.doc` 与 `.docx` 两条路径交付的标题不同，现已一致）。2.18→2.19 两项（CH-503）：渲染红线判据改「未被反斜杠转义的语法位置」使此前被 `E5031` 整篇误拒的文档正常出产物（交付**集合**变化）、页脚剔除给跨页折行残句尾巴加豁免使长 PDF 被误删的正文回到产物（交付**内容**变化）。2.17→2.18 的理由恰好三项：交付 meta 摘掉片级内部锚 `shard`、外链链接文字补转义、DOCX 行字号改按字符数加权众数 |
| `SCHEMA_VERSION` | 14 | IR 结构 / 字段语义变更 |
| `DB_VERSION` | 10 | 迁移步骤变更（9→10 由 B-27 的 `tasks.owner` + `tasks.lease_expires_at` 触发） |
| `PLAN_VERSION` | 3.39 | 方案文档实质修订，与管线版本**解耦** |

解耦的历史原因很具体：CH-452 那轮把 40 余处「方案 v2.0 §x.y」锚点改写之后，版本号一升位这些引用就集体失效——于是文件名恒为 `docs/dev-plan.md`、版本只写在头部表格与 `doc2md.PLAN_VERSION` 两处，并由四条红线做三处对账（`tests/test_ch477_version_sync.py`）。**这四条断言的顺序是刻意的：先确认锚点解析成功，再比值**——解析不到就跳过会让红线静默恒真，比漂移更糟。

---

## 5 实现方案

### 5.1 S1 抽取：两类源、一条几何契约

PDF 与 DOCX 的抽取路径完全不同，但必须产出同构的 `RawPage` 流，这是后续所有层能统一的前提。

**PDF**（pdfplumber）：逐页取 `chars` → 行聚合 → 版面判定。两个关键细节：`extract_text_lines` 会剥掉行首空白，需要调用方显式补回（`s1_parse.py:375` 的注释就是这个坑）；双栏判定先排除**通栏块**（宽度 >70% 页宽的块会污染 x0 聚类），再在剩余行上找中缝，输出「左栏读完再右栏」的顺序（`:457`）。链接按竖直带重叠附着——这正是表头与右栏 URI 错挂的根源（R7-PIPE-6，残留为 B 项）。加密 PDF 在尾窗探测阶段判定，避免到抽取中途才炸。

**DOCX**（python-docx + lxml 直读 XML）：编号从 `numbering.xml` 重建（`_fmt_num`，`:789`）支持 decimal / roman / letter / bullet，含**双射 26 进制**（A…Z 后是 AA）与扩展罗马数字；文本框内容走独立遍历（`_docx_textbox_paragraphs:1180` + `_inside_textbox:965`）——因为 `w:drawing`/`wps:txbxContent` 里的 run 若被主段落与文本框各收一遍，会**叠到 4 倍**（嵌套 `AlternateContent` 的真实倍率）。脚注尾注单独成段。旧版 `.doc` 不在包内解析二进制，而是 OLE 嗅探后交给 **LibreOffice / Microsoft Word 子进程**（`pipeline/legacy_doc.py:131`）转成 DOCX 再走同一条路——决策 O-12 明确「记录这个外部依赖」而不是自己实现。

**输出必须带 provenance**：每块记页/段来源，否则后面的归因与审计无从谈起。

### 5.2 S1.5 文本层体检：宁可拒绝，不出半成品

`s1_5_gate.py:276` 等距采样 5 页，逐页算字符数、噪声比、字母占比、`.notdef`、CID `/ToUnicode` 有无，任一坏页即拒（**E4221**）。CH-483 补的第二组判据来自一个真实漏网：**噪声 >0.95 且字母/字符 <0.05** 的页能骗过只看字符数的门禁（R7-PIPE-5）。零页文档单独走 `no_pages` 分支（R11-01）。`auto` 档的严格度是否该按 `reject_on_any_bad_page` 可调，登记为 B-32（改默认会动产物字节）。

### 5.3 S2 分片与缝合

`shard_plan(:28)`：>300 页才切，段间**重叠 2 页**用于跨页结构对齐。三处「诚实性」教训直接写进了实现：

1. `modal_size(:1050)` 必须**全局算一次**再逐片传入 `base_size`——按片自算会让异质字体文档的分片结果 ≠ 单片结果（PIPE-1）。
2. 缝合不能靠预先打好的 `shard_edge` 标记：320 页样本上标记给出 **46 个假续接候选 vs 1 个真缝**（CH-427）。
3. 页数取真实覆盖，不用各片求和（CH-438 修的是 322→320 的虚增）。

任何缝合异常都降级为 `_fallback_single`（`convert.py:292`）并**如实带 warning**，绝不在降级后还标 `sharded=True`。

### 5.4 S3 结构分析

`s3_structure.py:623` 的 `analyze` 按顺序做：家具剥离 → 标题判定 → 列表/编号 → 表格 → 代码块 → 引用 → 分页。

- **家具剥离**（`_strip_page_furniture:289` + `_furniture_key:270`）用「宁漏勿误」三重约束：边距带内 + 字号 ≤ 正文 + 行长够短，三者同时满足才判为页眉页脚；`_furniture_key` 把 `第 N 页` / `第 M 栏` 这类逐页变体归一化（O-09），否则同一页眉在每页都是「新内容」。
- **标题判定**用三组正则（`_CJK_HEAD:30`、`_CJK_ORD`、`ORDERED_ITEM:61`）+ 字号/加粗门限。中文序号映射固定为 章/编/部/篇→1 级、节→2 级（`:1152` 一带）——这个映射本身是有争议的，见 §6.1 的 B-45。
- **代码块缩进**按 S1 的逐字符字宽几何重建（CH-482），DOCX 侧保留前导空白。「已缩进的代码再重建缩进」是已知风险，暂缓项写在 `docs/spec-ch492-q2-deferred.md:115`。

### 5.5 LLM 语义修复层：默认离线，在线可选

模型只被允许提交**五个白名单算子**——`split_block / merge_block / fix_heading_level / fix_list_marker / mark_table_boundary`（`llm.py:22`）——而不是自由文本。这是这个项目对 LLM 最关键的架构决策：**把不确定性关进类型系统**。三道防线：

1. `OpValidator._check(:256)` 校验算子名、位置、目标是否 `immutable`、是否与其他算子重叠，非法即拒。
2. 默认 `mode=offline` → `offline_llm(:457)` 是确定性启发式，**零网络、零模型也能跑完全部功能**。
3. `mode=server` 才走 `LlmClient(:636)`（stdlib urllib，打到 `127.0.0.1:8080`）；服务异常时 `llm.fallback=true`（默认）静默回落并打 `fallback` 标记，`=false` 时抛 **E5041**。

两个「被现实教育过」的实现细节：系统提示词 v2.5 里**内嵌了每个算子的参数 JSON schema**，因为 0.8B 级小模型会输出不带参数的算子（`llm.py:50-60`）；`scan_injection(:434)` 的定位在注释里写得很直白——**可观测性，不是防御**。

### 5.6 S4 渲染与产物

`s4_render.py:109` 渲染并 `assert_render_contract` 自检（渲染红线见 dev-plan §4.6）。产物 zip 由 `convert.py` 的 `package` 原子写（临时名 + `os.replace` + **父目录 fsync**，CH-495/A4），内含 Markdown、IR JSON、报告与 manifest。等价性判定纪律 G6：逐条目 `sha256`、剥掉 front-matter 的易变字段（`elapsed_ms` 必须排除）；**整包 md5 被明令禁用**——zip 里条目顺序与时间戳会让它假红/假绿。

### 5.7 队列、常驻与运维

`doc2md work` 支持 `--once / --loop / --enqueue / --max-jobs`；租约与 owner 见 §3.4。`cron.run(:130)` 把备份、轮转、清扫、日报串成一条链，第 6/7/8 步各自有守卫且报告在 `finally` 里出（F3），任何一步失败都不吞掉后面的步骤。`doc2md/ops/drill.py` 做恢复演练，输出**只有计数与指纹**，不含任何正文。`doc2md/ops/gguf.py` 解析 GGUF v2/v3 元数据并导出词表，manifest 绑定模型哈希——模型换了对称性就断，必须可查。

---

## 6 项目难点与解决方案

这一章是本文的重点。每项统一按 **现象 / 根因 / 解法 / 落点 / 证据 / 残留** 六段写，且只写实测过的项目。**「残留」不许留空**——没有残留就写「无」，因为「声称已彻底解决」正是本项目十七轮里反复出现的病灶。

### 6.1 PDF 版面恢复

#### 6.1.1 双栏与阅读顺序

- **现象**：双栏 PDF 转出来左右交错，语义全乱。
- **根因**：`extract_text_lines` 按 y 排序，双栏同行即同键；更早的尝试直接用 x0 聚类，被**通栏块**污染——一个跨栏的标题会把中缝两侧都拉到同一簇。
- **解法**：先剔除宽度 >70% 页宽的通栏块，再在剩余行上找中缝分侧，输出「左栏整列读完再右栏」；行首空白由调用方显式补回（库会剥）。
- **落点**：`doc2md/pipeline/s1_parse.py:457`（`_extract_two_column`）、`:283`（调用与几何参数）、`:375`（补空白的原因注释）。
- **证据**：`tests/test_pdf_columns.py` 6/6 页顺序核对（`docs/qa/comparison.md`）+ 2 条红线；附录 G 记 `test_s3_blocks.py` 614 行含端到端。
- **残留**：链接按竖直带重叠附着，表头/右栏 URI 仍会错挂（R7-PIPE-6）；修正会改产物字节，作为 B 项暂缓。

#### 6.1.2 页眉页脚与页码家具

- **现象**：每页的「第 3 页 / 第 2 栏 / 公司名」被当成正文段落重复输出。
- **根因**：家具的判据本身逐页变化（页码递增），按字面比较永远找不到「重复」；且早期把数字折起来的做法会把正文里的「第 2 章」一起吞掉。
- **解法**：`_furniture_key` 做归一化（数字折叠为 `#`，`第 N 页` / `第 M 栏` 专项处理），再叠加「宁漏勿误」三重约束——**边距带内 ∧ 字号 ≤ 正文 ∧ 行长够短** 三者同时成立才剥。性能上限制为每行最多一次 key 计算（CH-493/P-2 把「最多 3 次」压成 1 次，配合单趟家具剔除）。
- **落点**：`doc2md/pipeline/s3_structure.py:270` / `:289` / `:638`。
- **证据**：O-09 的量化前后比对（`docs/qa/optimization-plan.md`）+ 家具相关红线。
- **残留**：**R17-PIPE-5 是一个典型的「修复自身引入静默损失」**——`_strip_page_furniture` 重建 `RawPage` 时只搬 `index/lines/height/size_hist`，把 `rotation` / `rtl_ratio` 两个版式探测字段**清零**。它当时线上没露馅，唯一原因很不体面：唯一的读取方（`convert.py` 的 W1046）用的是**剔除前**的 `pages_all`，所以任何在该函数之后读这两项的调用方都会拿到 0——与 CH-493（P-2）修 `size_hist` 前的是同一个失效形态。现由 `s3_structure.py:359` 随页携带，并由红线 `tests/test_ch500_round17.py:536`（`test_strip_furniture_keeps_layout_probe_fields`）钉住。另有 B-33（页眉页脚识别策略进一步收敛）登记未做。

#### 6.1.3 表格与合并单元格

- **现象**：合并单元格转出来列数对不上，跨页表格被切成两张。
- **根因**：PDF 表格没有「单元格合并」概念，只有画线与文本位置；跨页表在页面边界处结构信息中断。
- **解法**：DOCX 侧按 `gridSpan`/`vMerge` 做网格归一化（R8-PIPE-5）；PDF 侧拍平并**显式给 W1045 告警**（`convert.py` 的 warnings 族），单元格内多段落用 `<br>` 可见降级（O-05）。
- **落点**：`s1_parse.py:1130` 一带的表格行、`s3_structure.py` 的表格块、dev-plan 附录 D 的 W1045/W1046 行。
- **证据**：CH-485（B2）的表格结构修复带红线；`docs/qa/round3` 有专项对照。
- **残留**：**B-43（发布阻断）**——跨页表整列脱落 + 裂表，且 `ir.warnings` **零告警**。这是第十七轮 QA 第三轮在独立真值语料上发现的，门禁 580 项 / 全量 1390 项 / 语料 22/22 **全部为绿**。

#### 6.1.4 旋转页 / RTL 页

- **现象**：旋转 90° 或从右往左排版的页，转出来顺序错乱。
- **根因**：几何恢复需要真正的版面模型，与「确定性 + 零依赖」取向冲突。
- **解法**：**明确定为非目标，但要求可观测**——`/Rotate % 180 ≠ 0` 或 RTL 比例 >50% 时给 **W1046**，绝不静默输出看起来正常的结果。探测字段挂在 `RawPage` 的 transient 区，不进 IR。
- **落点**：`s1_parse.py:292`（`_rtl_ratio` 调用）、`s1_parse.py:111`（`RawPage.rtl_ratio` 字段）、dev-plan §2.1 FR-101 行与附录 D。
- **证据**：QA 第三轮 P05-P06 探针（`docs/qa/round3/`）在旋转/RTL 样本上实测拿到 W1046。
- **残留**：无（按设计立场定档；未来若要支持需新开 CH 并重估依赖）。

#### 6.1.5 CID 字体无 /ToUnicode（中文乱码）

- **现象**：某些中文 PDF 转出来是字形编号或方框。
- **根因**：字体没有 `/ToUnicode` 映射，抽取层拿不到码位；这是**输入不可解**，不是算法缺陷。
- **解法**：S1.5 把「ToUnicode 有无 + PUA 比例 + `.notdef` + 单字重复率 >0.30」做成体检项，命中即 **E4221 拒绝**，而不是输出乱码产物。
- **落点**：`doc2md/pipeline/s1_5_gate.py:276` 与体检指标族。
- **证据**：本地无法合成真 CID 样本，因此难度语料用 `hard_10_cid_proxy.pdf` 并**在 manifest 上标 `proxy:true`**（不假装是真值）；真样本登记 B-47。
- **残留**：**B-47**——乱码产物的对外口径（拒绝 vs 带告警输出）需人工定档。

#### 6.1.6 文本框、脚注与内容丢失

- **现象**：DOCX 里放在文本框/绘图里的正文整段消失，或重复 4 倍。
- **根因**：`w:drawing`/`wps:txbxContent` 不在主段落流里；`AlternateContent` 的 Fallback/Choice 双分支导致同一段被多次收；嵌套文本框让倍率叠加。
- **解法**：把 CH-495/R14-PIPE-1 的判定改成**「相对段落 p」的层级递归**——`txbxContent` 内的段落收自己的全部 run，但其内再嵌套的文本框交给下层递归；用 `seen: set[int]` 做元素身份去重。真语料规模已量化：116 处文本框 / sdt 20 处含 1,495 段。
- **落点**：`s1_parse.py:1180`（`_docx_textbox_paragraphs`）、`:965`（`_inside_textbox`）、`:1047`（脚注）。
- **证据**：CH-485 R9-PIPE-1 + CH-495 A1 的嵌套去重红线（`test_ch495_pipeline.py` 13 项）。
- **残留**：B-13 的真实 Word 模板样式联动待抽查。

### 6.2 DOCX 结构重建

#### 6.2.1 中文标题整体失效而测试全绿（本项目最著名的一课）

- **现象**：320 页中文黄金样本转换结果 **H1 数量 = 0**，64 个章标题全变正文段落。
- **根因**：产品侧 `_CJK_HEAD` 正则**不允许「第」与数字之间有空格**，「第 64 章」匹配失败；而 `make_golden.py` 的自检正则允许 `\s`。**产品与它的自检用了两套规则**，于是缺陷在测试体系内完全隐形。
- **解法**：正则允许空白，并补硬断言 `H1 == 64`、`headings == 640`；更重要的是把「自检不得复用被测实现的正则/规则」立为纪律。
- **落点**：`doc2md/pipeline/s3_structure.py:30`（`_CJK_HEAD`）、`:1103` / `:1118` / `:1152`（三处判定）。
- **证据**：R8-PIPE-1（`docs/review-round8-CH-483.md`，1,754 行报告里的第 1 项）；CH-486/487 的 B-15 中文 PDF 人工标题 ground truth（21 份文件 / 111 个 DOCX + 102 个 PDF 标题，46 条断言钉住）。
- **残留**：B-15 的业务 PDF 真值仍需人工标注；B-45（层级口径）见下。

#### 6.2.2 编号与列表

- **现象**：多级列表「2.1.」被拍平、bullet 的 `o` 被当成有序项、Heading 段带 `numPr` 时编号丢失。
- **根因**：OOXML 的编号是 `numbering.xml` 里的 abstract/instance 两级 + format 字符串，不是文本前缀；`numFmt` 有 decimal / lowerRoman / upperRoman / bijective 26 进制 / bullet 多族。
- **解法**：`_fmt_num`（`s1_parse.py:789`）实现**双射 26 进制**与扩展罗马数字；`ORDERED_ITEM`（`s3_structure.py:61`）从私有常量提升为公开符号并被 S3 复用，消除「两个层各自维护一份有序项规则」。
- **落点**：如上；F12 重构见 CH-495。
- **证据**：R8-PIPE-4 / R9-PIPE-3 的回归；`test_ch495_*.py`。
- **残留**：R9-PIPE-3 列的三类退化仍在，登记未闭合。

#### 6.2.3 无编号短标题的误判

- **现象**：正文里的「1.」条目被判成 H1。
- **根因**：第三条兜底判定只看形状不看字号，缺一个尺寸门限。
- **解法**：决策 D4——**字号 > base 或加粗**才允许走兜底标题判定。
- **落点**：`s3_structure.py` 的标题兜底分支；`_CJK_HEAD` 相关判定链。
- **证据**：R6-PIPE-1 的 B-1 出口闭环（CH-478 批次①），`test_ch478_fixes.py` 18 项（红线 4 + 信息层 14）。
- **残留**：无（该出口已闭合，但层级口径争议另计 B-45）。

#### 6.2.4 旧版 .doc 与外部后端

- **现象**：`.doc` 无法离线自解，包内也没有二进制解析器。
- **根因**：OLE 二进制 + Word 私有结构，实现成本远超收益。
- **解法**：OLE 嗅探 → LibreOffice / Word 子进程转 DOCX → 汇入同一抽取路径；决策 O-12 定为**「把这个依赖写进文档与 doctor」**，而不是偷偷装或假装支持。
- **落点**：`doc2md/pipeline/legacy_doc.py:131`、`doctor` 的 `legacy_doc_backend` 报告项。
- **证据**：CH-488 的旧版 DOC 转换与双模式验收；`doctor` 本机报 `legacy_doc_backend=libreoffice`。
- **残留**：**B-41**——LibreOffice 与 Word 两条后端的交叉验证需真实样本 + 人工比对。

### 6.3 大文档性能与有界内存

#### 6.3.1 内存峰值与页数解耦（含一个负结果）

- **现象**：1,290 页 PDF 在 8GB 机器上会打满。
- **解法**：`chunk_pages=32` 的重开窗口 + 逐页缓存回落，实测 **1290MB → 1025MB → 176MB**（三档驻留序列在 README 与 §12.3 各写了一遍）。
- **负结果（重要）**：CH-460 尝试「逐片流式化」想进一步降峰值，结果 320 页样本峰值**不降反升**（132.9 → 134.9 / 137.9MB）。根因：驻留主体是 **IR 块**而不是页缓冲。这个反例被**保留**在 `convert.py:145` 的「A-8 回退说明」注释里，防止后人重试。
- **另一条测量**：B-24 的 8/16/32 线程 → 473/867/2144MB；把缓存设 8MB + `mmap_size=0` → 16GB 档 164MB。
- **残留**：B-39（绕开 pdfplumber 直解）作为长期可选项登记。

#### 6.3.2 并行收益被池重建吃掉

- **现象**：开 4 worker，1,080 个小文件吞吐只提高 **+4.5%**——多年「为什么快不起来」的答案在这里。
- **根因**：每轮排空重建一次 `ProcessPoolExecutor`，每个新 worker 都要重新 `import pdfplumber`（约 0.166s），启动成本 ≥ 任务本身。
- **解法（两步）**：决策 D2 先把**领取量与池大小解耦**（claim 4×workers）而不是改常驻池（与 X-17 的 terminate 语义冲突）；CH-497 再让 `run_batch(plans=…)` **每次排空只建一个池**，`_claim_batch` 把混合 mode 的排平成一个 plan 列表一次调用。
- **落点**：`doc2md/pipeline/batch.py:250`、`worker.py` 的 `_claim_batch`。
- **证据**：本机 3×3 PDF，workers=3，三次中位 **1.631s → 1.194s**；30 分钟常驻长跑 83 轮、`done=83 failed=0`、RSS 67600→61936kB、FD 恒定；B-16 由 `test_ch498_drain_e2e.py` 证明「一次排空恰好一个真池」并靠 Windows 远端腿闭合。
- **残留**：B-38（页区间并行）性能优先级最高；B-8 跨机基准未出。

#### 6.3.3 S1 是绝对瓶颈

CH-500 实测 320 页文档里 **S1 ≈12.7s，占单文档 ≈97%**，页区间并行给 2.79×。这条数字直接决定了优化优先级：任何不在 S1 上的性能工作都是边角。

### 6.4 并行与进程生命周期

#### 6.4.1 X-16 / X-17：测试全绿之后进程不退出

- **现象**：CI 的 `merge-gate (3.12)` 腿在打出「132 passed」**之后**卡死到超时；随后 `full-suite` 也在「605 passed」之后挂 27 分钟。
- **根因**：CPython 3.12.x 的 `ProcessPoolExecutor` 对 `shutdown(wait=False, cancel_futures=True)` 放弃的「已 cancel 却未终止」worker，在解释器退出时会死锁。
- **解法（分两刀）**：X-16 只在 abandoned（空窗超时）分支按 pid 逐个 `terminate()` **本池**的 `_processes`（绝不碰全局 `active_children`，那会杀到别的池，破坏 G6 等价）；X-17 把根因治好——`batch.py` 收尾**无条件**终止本池 worker（terminate → join(5) → SIGKILL → join(1)）。
- **证据**：run `38851378872` 五作业全绿；X-17 修复后 run `38913173159` 的 `full-suite` 606 项 153.28s **干净退出**（同腿上一 run 挂 27 分钟）。
- **教训（记入方法论）**：**「测试通过」不等于「进程能退出」**。CI 必须给作业级 `timeout-minutes`，且日志要用 `-vv` 打到能看出「卡在哪个阶段」。
- **残留**：无（远端已证），但 `-p no:cacheprovider` 与 `pytest-randomly` 使墙钟散布 ±4–6s，故**耗时永不入断言**。

#### 6.4.2 X-18：Windows 上「探活」会杀死对方

- **现象**：备份锁的 PID 活性探测在 Windows 上引发 CI 挂死。
- **根因**：`os.kill(pid, 0)` 在 Windows **不是无信号探活**，而是向该进程组广播 Ctrl+C（bpo-14484）。一个「只是看一眼」的调用能终止被测工作负载。
- **解法**：`doc2md/ops/backup.py:123` 的 `_pid_alive` 做**平台分派**，Windows 走原生句柄探测；并用 **AST 红线**让「裸 `os.kill(x, 0)` 出现在 POSIX 分支之外」直接编译期失败。语义上「无法确认」按保守处理（视为仍存活），绝不因为探活不确定就抢锁。
- **证据**：CH-494 的 X-18 回归 15 项（AST 静态守卫 1 + 参数化矩阵 5 形态 + 行为/端到端 13）。注意 CH-494 还修了**红线自身的平台缺陷**——行为矩阵原先在错误的边界打桩，Windows 上测不到真路径。
- **残留**：无（六作业全绿闭合）。

#### 6.4.3 B-27：回收把正常长作业当遗骸

- **现象**：两个进程同时跑时，长跑 70s+ 的作业被 `reclaim_stale` 判为遗骸并重新领取 → **重复转换 + 假的 `rejected=1`（E4041）**。
- **根因**：回收谓词只有「`started_at` 超阈值」，无法区分「进程卡死」与「任务本来就长」。
- **解法**：`DB_VERSION 9→10` 加 `tasks.owner` + `tasks.lease_expires_at`；领取与租约同事务；`_lease_keeper(:225)` 续约线程按 `max(1.0, min(60.0, lease/3))` 心跳；`reclaim_stale(:105)` 改三分支谓词（租约过期 / 无租约走旧 `stale_job_minutes` 语义原样保留 / 超 `job_lease_cap_minutes` 硬上限兜住「活着但僵住」）。**刻意不加 `os.kill(pid,0)` 探活**——X-18 的教训直接约束了这个设计。谓词 SQL 由 SELECT 与 UPDATE 共用，UPDATE 只多一个 `AND status='running'`，CH-496 的 `changes()` 计数不回退。
- **证据**：`ops/bench/b27_reclaim_lease_probe.py`（421 行）双进程 `live`/`dead` 两腿——live：6000 页 / 4,168,827B 作业 A 在 `running` age=68.2s 时放 B 进场，`reclaimed_stale=0` / `rejected=0` / 库面 `job_rejected=0`，并观察到 **11 个不同的 `lease_expires_at` 取值**（证明续约真在跑）；dead：`kill -9` A 后 B 正常接管跑完。
- **残留**：Windows 真机上的长跑租约腿待补（属远端复验一类）。

### 6.5 并发一致性

#### 6.5.1 B-17：全新库首开的 WAL 竞态

- **现象**：全新 root 上，常驻 `doc2md work` 以 `E5000 unexpected: OperationalError` 死亡，同一时刻 `--enqueue` 却 rc=0。一次性、难复现。
- **根因**：多个 `Store()` 并发做首启迁移，而 **`PRAGMA journal_mode=WAL` 需要独占锁**——它**不吃** per-connection `busy_timeout`。**反直觉测量**：一次竞争实测等 **31.0s** 才抛（`timeout=30.0`），而裸默认是 5s，所以「加个 busy_timeout」是错的修法。
- **解法**：`db.py:257` 的 `Store._set_wal`——限时重试（`WAL_SWITCH_BUSY_MS=250` / `WAL_RETRY_DEADLINE_S=60.0`）→ 之后仍非 wal 则 **fail-closed** 抛明确错误；只吞「database is locked」，其他 `OperationalError` 原样上抛；切换后恢复 `busy_timeout=30000`。
- **证据**：`/tmp/b17_repro2.py`（15 轮 × 4 个真 CLI 子进程）+ `tests/test_ch498_db_init_race.py` 4 项红线（**两条腿各自用变异证伪**，即故意把修复改坏看断言是否变红）。Windows 侧由远端腿覆盖。
- **残留**：无。

#### 6.5.2 跨进程重建与产物孤儿

- **跨进程重建锁**：API 进程与 worker 进程可能同时给同一文档打包，锁必须在**文件系统层**（进程内锁无效）→ `out/.locks/<doc>.lock` 用 `O_EXCL` 创建 + 心跳 mtime + 释放令牌（B-18，CH-490；完整实现在 `docs/spec-ch492-p1-a1.md` §552-615）。
- **产物孤儿**：B-4 并发压测发现「删除 × 在途转换」会让 `out/{doc}.zip` 在回滚后存活。解法 `artifacts.py:204` 的 `drop_orphan_artifact`：**先确认文档真的没了**才删产物，且 `check + delete` 整段持 `_artifact_lock`（CH-495/G8）。压测夹具固化进仓库 `ops/bench/b4_delete_race_stress.py`。
- **同源重复转换清空用户编辑**（第四轮 STOR-5，HIGH）：`INSERT OR REPLACE` + `DELETE FROM blocks` 会静默丢掉人工修订并把 revision 归零。解法引入 **E4091**（重复转换护编辑，409 段位此前未占用）。

#### 6.5.3 迁移与版本的两处教训

- `_migrate` **不能只看版本号**：CH-469 实测发现「同版本就等于同结构」的前提是假的，于是加了对等版本的**补列**路径；同时新增「比程序更新」的库直接拒（fail-closed，避免旧二进制改新库）。
- **`datetime()` 包装是必需的**（CH-455）：SQLite 里 ISO 字符串的 `T` 与空格两种形态会让朴素字符串比较失效，遗骸回收与事件保留期因此长期判错。这条现在被写在 `worker.py:132-138` 的谓词里（三处时间比较一律 `datetime()` 包装）。

### 6.6 隐私与安全边界

#### 6.6.1 FAT32/exFAT 上「隐私权限」是装饰位

- **现象**：M8 / NFR-20 的隐私前提（工作目录 0700、覆写文件 0600）**从来没有真正生效过**，而且告警通道永久静默。
- **根因**：旧启发式 `if not mode & 0o077 and got & 0o077` 在「合成 0700 的卷」上永不触发——macOS 的 fskit 在 FAT32/exFAT 上把 `os.chmod` 实现成**完全 no-op**：写 0600 或 0777 都读回挂载默认的 `0o700`。
- **解法**：判据从「其他位是否清零」换成 **「请求值 == 读回值」**（`perms.py:69` 的 `check_applied`），不满足就告警；告警文案一字未改（红线 `tests/test_ch469_ops.py:669` 钉着）。
- **取证方式**：`ops/bench/b14_crossfs_probe.py`（395 行）用 `hdiutil` 现造三张 1200MB 映像（FAT32 / exFAT / **Case-sensitive APFS**）各挂 `-nobrowse`，测权限位真实性、软链、消毒建名、异卷建库 + 真转换（`docs=1/blocks=60/done`），跑完 detach 并删映像。结论：前两者 `no-op`，APFS-CS `real`；消毒名 6/6 可写。
- **第二个反直觉结论**：macOS fskit **允许在 FAT32/exFAT 上创建软链**（不返回 `EOPNOTSUPP`），「可移动盘没有软链所以不用防」这个前提是错的，dirguard 的 realpath 包含判定**必须保留**。
- **残留**：B-14 的 SMB/网络卷、Windows junction 真机复验（B-40）；AppleDouble `._*` 伴生文件被当产物计数（已发现 `._905ae8dd9dc301ee.zip`）。

#### 6.6.2 凭据通道：`?token=` 是被拒绝的设计

`api.tok()`（`api.py:228`）只接受 `X-Doc2md-Token` / `Authorization: Bearer`，URL 携带令牌一律 **401/E4011**。理由写在 dev-plan §5.5：查询串会进访问日志、代理缓存与 Referer。这是一次「把待办改成设计拒绝」的定档——审查提出的便利性诉求被明确否掉，并在 §12.7 标注为「不再是开放项」。

#### 6.6.3 注入归因的循环自证

- **现象**：算子被判「疑似注入」后，归因逻辑扫算子 JSON，再和「被注入的算子集合」比对——两者互相提供证据，形成循环。
- **根因**：R6-LLM-2。证据源本身取自被审对象，等价于让嫌疑人的说法自证。
- **解法**（B-3，CH-499）：`OpValidator._attribute` 的证据域改为 **算子 JSON ∪ 源文档块文本**，按实例惰性缓存 `_doc_hint`（本轮没有拒绝就一次都不扫）；扫描按块做 `scan_injection()` 而不是 `json.dumps` 整份 IR——320 页样本 5,696 块实测 **18ms**。响应契约与错误码不变，产物字节不变（黄金 7 + 63 产物项仍绿）。
- **诚实边界（必须留）**：文档侧证据是**相关性不是因果性**——真幻觉若恰好出现在带提示词的文档里，也会被标成 `injection_candidate`。误报/漏报率需要真实注入语料才能量化，故该项**只算「去循环」落地，语料半侧仍开放**。

#### 6.6.4 netguard 的强度定位

`netguard.py` 拦截四个 socket 系统调用，白名单只收 IP 字面量（`localhost` 因 hosts 污染风险被移出）、DNS 失败即拒、未绑定地址（`0.0.0.0`/`::`）拒绝。但它**是进程内猴子补丁，不是 OS 边界**：一个被投毒的依赖可以在它之前拿到 socket。文档按「护栏」定位陈述，不写成「保证」——这是第四轮 SEC-1 的结论。

#### 6.6.5 合规取证

CH-487 的 `docs/compliance-audit-CH-487.md` 对 PEM / AccessKey / token 模式做了三张网扫描：当前跟踪树、全历史、以及远端可达 blob，**命中 0**。备份容器 v2 的 AAD 绑定整个 header（含 `chunk_size`），篡改任一字段即解密失败（CH-469 的加固项）。

### 6.7 可复现性

- **G6 等价纪律**：分片结果 ≡ 单片结果、关掉提示词后逐字节不变。这两条不是「希望」而是红线（G1/G6 是门禁性质编号）。比对用逐条目 `sha256` 并剥 front-matter，`elapsed_ms` 必须排除；整包 md5 禁用。
- **黄金语料**：`tests/corpus/` 22 份核心样本，CH-500 的三项产物字节改动之后仍然 **22/22 逐字节一致**（这是升 `PIPELINE_VERSION` 的前置证据）。
- **一个平台级教训**：Windows 的 `autocrlf` 曾损坏 **31 份黄金资产**（W-01）——缺 `.gitattributes` 让「可复现」变成一个平台问题，而不是一个代码问题。
- **版本升级判据（D12）**：只有交付字节变化才升 `PIPELINE_VERSION`。第十七轮的三项改动各自动字节，故 2.17→2.18；纯测试/文档改动一律不升，本轮（CH-501）产品字节零改动即为一例。

### 6.8 测试与验证方法论（本项目最独特的产出）

这一节的价值可能高于任何单个缺陷修复——它回答「为什么十七轮审查之后还会漏掉发布阻断级缺陷」。

#### 6.8.1 覆盖数与发现力不相关（B-43 / B-44）

- **现象**：门禁 **580 项全绿**、全量 **1390 项 0 失败**、核心语料 **22/22 逐字节一致**，三条腿都绿；而同一个版本在**第一次运行的独立真值难度语料腿**上给出 4 通过 / 2 失败 / 1 缺口。
- **失败内容**：D02 跨页表**整列脱落 + 裂成两张**；D05 目录点线被当成表格单元格、「第一章 总则」塌成「第一章总则」。两者 `ir.warnings` 都是**零**——即静默结构损伤，违背 W1045 与文本保真承诺。
- **根因（方法论级）**：此前所有期望值都取自**产物自身**。真值来自被测实现，则结构损失在定义上不可见。
- **解法**：CH-500 立独立真值层（`tests/corpus/hard/` + `tests/qa/test_hard_gate.py` + `run_hard.py`），CH-501 把它接进六层编排器；判定口径同时改为 **「分档只看行为是否与既有承诺冲突，修复成本与所需环境只约束路径、不降级判定」**。
- **定档**：**发布判定＝阻断**。B-43 / B-44 须人工逐页对照真 PDF 修复，或复核后出具**书面豁免**；两者的用例卡 TC-005 / TC-011（D02）与 TC-008（D05）按同一派生口径自动记 **P0**。
- **一个实现约束值得单记**：B-43 与 B-44 **必须同批修**——D01 希望「无框线表」被识别、D05 希望「无框线目录」被拒，两者是同一套几何判据的两端，分开修必然互相打脸。

#### 6.8.2 独立真值层的构造纪律

生成器 `make_corpus.py` 的自检**直读源 XML/几何，绝不经过被测管线**（N-3），于是「管线判 H1」与「真值判 H1」是两层独立断言（F1==1.0 在两层各测一次）。这是 §6.2.1 那次教训的制度化。

#### 6.8.3 六层编排器与三条不可让口径

`tests/qa/realtest.py`（507 行）：`L0_static`(ruff) → `L1_gate`(红线，要求 **skipped==0**) → `L2_full` → `L3_assets`(语料逐字节) → `L4_install`(doctor / CLI / API / worker 四探针) → `L5_quality`(评估→归因→缺陷扫描→追溯→优先级→难度腿→真实样本腿)。三条口径（dev-plan §8.7）：

1. **`not_run` 永远不许写成通过**——本机不可能做的事显式登记 `Layer.gaps`，而非静默跳过。当前两条显形项：`L5_quality/real_leg` 因 `work/real_samples/` 无样本 `not_run`（B-49）；`L4_install/clean_venv_install` 需空 venv 安装，属发布前动作。
2. **计数只信 `--junitxml` 的 `tests=`**，缺失记 `None` 而不是 0；`xfail` 必须从 `skipped` 里**拆出来单写**（junitxml 会把两者合并），因为 **xfail 不是 pass**。
3. **效果分不进红线**——难度语料的分数是信息层，红线只钉契约。

本轮（CH-519）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，本轮只把 `tests/test_ch473_fixes.py` 的四条「不得出现」改写为成对判据，六层四元组与 CH-518 全等；逐层 ruff rc=0 0.04s / `L1` 41.19s（同轮 `--junitxml` 独立采样 41.030s）/ `L2` 90.7s（同轮 `--junitxml` 独立采样 89.671s）/ `L3` 25 passed 0.67s / `L4` 四探针 0.64s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.43s；本轮编排器未开 `--coverage`，覆盖率由同轮独立 `coverage run -m pytest tests/` + `coverage report --include="doc2md/*"` 给出 7,942 stmts / 678 miss = 精确 91.46%，与 CH-515/CH-516/CH-517/CH-518 两个数全等——本批只动测试与文档面，`doc2md/` 语句零变化）；上一轮（CH-518）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，本轮只把 `tests/test_ch504_service_faces.py` 的四条「不得出现」改写为成对判据，六层四元组与 CH-517 全等；逐层 ruff rc=0 0.05s / `L1` 41.94s（同轮 `--junitxml` 独立采样 40.732s）/ `L2` 92.06s（同轮 `--junitxml` 独立采样 91.205s）/ `L3` 25 passed 0.68s / `L4` 四探针 0.61s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.57s；本轮编排器未开 `--coverage`，覆盖率由同轮独立 `coverage run -m pytest tests/` + `coverage report --include="doc2md/*"` 给出 7,942 stmts / 678 miss = 精确 91.46%，与 CH-515/CH-516/CH-517 两个数全等——本轮只动测试与文档面，`doc2md/` 语句零变化）；再上一轮（CH-517）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.04s / `L1` 40.8s（同轮 `--junitxml` 独立采样 40.035s）/ `L2` 90.95s（同轮 `--junitxml` 独立采样 88.444s）/ `L3` 25 passed 0.66s / `L4` 四探针 0.57s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.46s；该轮编排器未开 `--coverage`，覆盖率由同轮独立 `coverage run -m pytest tests/` 给出 7,942 stmts / 678 miss = 精确 91.46%，与 CH-515 两个数全等——本批只动测试与文档，`doc2md/` 语句零变化）；更早一轮（CH-515）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.04s / `L1` 41.64s / `L2` 91.25s / `L3` 25 passed 0.69s / `L4` 四探针 0.64s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.57s；该轮编排器未开 `--coverage`，覆盖率由同轮独立 `coverage run -m pytest tests/` 给出 7,942 stmts / 678 miss = 精确 91.46%）；更早（CH-513）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.04s / `L1` 40.85s / `L2` 90.64s（junit 89.478s）且仪表化腿给 7,942 stmts / 680 miss / `L3` 25 passed 0.72s / `L4` 四探针 0.57s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.41s）；更早（CH-512）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.03s / `L1` 40.78s / `L2` 89.35s（junit 89.291s）且仪表化腿给 7,942 stmts / 680 miss / `L3` 25 passed 0.66s / `L4` 四探针 0.58s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.49s）；更早（CH-511）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.05s / `L1` 41.06s / `L2` 91.13s（junit 90.443s）且仪表化腿给 7,942 stmts / 680 miss / `L3` 25 passed 0.69s / `L4` 四探针 0.57s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.50s）；更早（CH-510）实跑头条：`REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`（零新增用例，逐层 ruff rc=0 0.03s / `L1` 41.36s / `L2` 92.06s 且仪表化腿给 7,942 stmts / 680 miss / `L3` 25 passed 0.67s / `L4` 四探针 0.57s（doctor 报 2.22.0）/ `L5` 六腿 pass + 真实样本腿 `not_run` 6.49s）；再往前（CH-509）与（CH-508 收口取证）为同一四元组 `L1=735/0/0/0 L2=1575/0/0/16`，CH-507 为 `L1=728/0/0/0 L2=1568/0/0/16`。

#### 6.8.4 零跳过纪律

`skip` 掩盖假绿是第四轮的 HIGH（TEST-1：golden 缺失时 `pytest.skip` 后仍判绿，而 CI 从不生成 golden）。此后规则是**金样本缺失即硬失败**；平台专属用例（Windows junction）必须显式列名，本机全量固定 10 条真 skip 里有 2 条就是它。

#### 6.8.5 门禁预算与「耗时不入断言」

`tests/test_ch498_gate_budget.py` 用纯 AST **冻结慢项身份**（触及 `large_*` 夹具的 7 项 + 2 项付费证明），新增慢项必须显式改表并重新测量才可能进门禁。为什么不断言墙钟：同一份门禁在 windows-latest 实测 **116.42s**、本机 42–56s——挂钟判据只会造出「带宿主色彩的红灯」，最后逼出 skip 来消音。

#### 6.8.6 度量口径卫生（四条反复踩过的坑）

| 坑 | 具体形态 | 纪律 |
| --- | --- | --- |
| **`-qq` 吞掉汇总行** | `pytest.ini` 的 `addopts` 已含 `-q`，命令行再加一个就是 `-qq`，「N passed in Xs」整行消失 | 需要计数时不带额外 `-q`；一律用 `--junitxml` |
| **覆盖率取数方式** | `pytest --cov` 在本案环境不可用（未装 pytest-cov） | 用 `coverage run -m pytest tests/` + `coverage report --include="doc2md/*"` |
| **组合口径必须可复算** | CH-500 的「50 项」写成一串分类时，读者必须能加回去：50 = 18+6+10+2+5+8+1 | 任何分档计数都附**恒等式**；本轮 B 类 17 项里 7 项不占 50 编号也照此说明 |
| **中途快照不得当终值** | 同一轮里 gate 出现 42.0–46.6s 与 52.4/55.9s 两群，其中低值是文档改动前的采样 | 收口立「**测量止句**」：终值只认项数与通过态，此后不再追加墙钟引用 |

还有一条舍入纪律（O-h）：覆盖率同时写**分母、精确值、显示值**三写——`90.51%` 显示为 `91%`，此前只写 `90%` 造成过对账失败。

### 6.9 文档与口径自洽（元问题）

十七轮里花时间最多、收益最隐形的一类难点：

- **文档散文本身是门禁读取对象**。红线会读 README 与 dev-plan 的字面数字（版本对账、NFR 行、门禁构成式），于是「改一个数字 → 门禁变红 → 改文档又动了行数 → 再红」。本轮 README 的取证段实测重跑六次才收。应对不是弱化守卫，而是把**判定挂在项数上**、把散文引用改成同一轮序列，并立测量止句。
- **自指计数最容易烂**。dev-plan 的资产段（`docs/` 份数与行数、README 行数、报告行数）曾出现五类漂移，最糟的一次是 CH-491 轮**声称已重算实则未测**，三个数字无一是当时的实测值，其中「组内行数 > 整包行数」在算术上不可能成立。现行纪律：**任何触及非 Python 资产的轮次，都要在同一轮里 `wc -l` 复算本段**，并把漂移史写进本段作为反例。
- **旧行不会自动刷新**。B-7 在 CH-486 早就加了 `windows-suite` 之后，仍长期写着「CI 无 Windows 腿」。收口时必须把 §12.7 **每一行**对着当前 `ci.yml` 重读，而不是只读本轮动过的行。
- **表格会被批量数字替换吃掉分隔符**。一轮改号后 dev-plan 出现断裂的 markdown 表格行；两个既有行的散文里合法含有裸 `|`（`launchd-*.out|err`），因此检查器必须**忽略转义与非表头行**再数列数。
- **历史数字不许凭记忆写**。`git show HEAD:file | wc -l` 之类必须实测（曾把 266 写成 296）。

---

## 7 历次开发与修复记录总结

### 7.1 六个阶段

| 阶段 | 提交区间 | 特征 |
| --- | --- | --- |
| **① 导入与骨架** | `23f8906` … `8d23dc0` | v2.4 离线管线入库，NFR-19 日志轮转与临时目录清扫落地 |
| **② 低配机与方案重写** | `525f80c` … `d8bf1e7` | CH-447…451 吞吐与有界内存；CH-452 把散份方案重写为**单一主文档** v2.0（并暴露 40+ 旧锚点失效问题，催生版本解耦）；CH-454 补代码结构 / 依赖纪律 / 技术栈三节与附录 G |
| **③ 首轮全面审查（五批次）** | `2e0c463` … `f31e317` | CH-459…463：FR-104 七类块补齐 + 链接抽取 + LLM 五语义、性能与存储批次、备份容器 v2、测试体系批次、文档口径批次 |
| **④ 审查轮常态化** | `15a8ae0` … `1589212` | 第二轮到第十七轮（第十七轮与其后的 CH-501 / CH-502 在 `1589212` 同批远端闭合），每轮「三层并行审查 → A 类本机实施 / B 类登记 / C 类代为决策」，A 类修复各配红线；CI 从三版本矩阵扩到含 `windows-suite`、lint、周度全量 |
| **⑤ QA 效果评估三线** | `92a412b` … `8536783` | QA 优化轮 2.13–2.15（O-01…O-12 / N-1…N-8）+ 第二轮偏差 D1–D13；这批**当时未登记 CH**，导致 +41 门禁项长期无归属，由 CH-495 补账（§6.9 的典型） |
| **⑥ 实机自动测试体系** | CH-500 … CH-501 | 第十七轮 QA 第三轮发现发布阻断 → 建独立真值难度语料 → 六层编排器 / 归因 / 缺陷扫描 / 追溯 / 优先级五模块落地 |

### 7.2 逐轮一览（gate 与全量为各轮收口实测值）

| 轮次 / CH | 主题 | 关键产出 | gate → |
| --- | --- | --- | --- |
| CH-444/445 | NFR-19 落地 | 服务内置日志轮转 + 启动清扫 `work/tmp` | 35→40 |
| CH-447…451 | 低配机吞吐 | 有界内存、分档吞吐 | 65 |
| CH-452…455 | 方案重写 + SQLite 时间比较修复 | 单一主文档 v2.0；遗骸回收/事件保留期修正 | 67 |
| CH-456/457 | 待办合轮 + T6 大语料 | 共享夹具（gate 53.0s）、S1 打开边界缺陷 | 67 |
| CH-458 | T3 跨平台测量 | 采样后端三支 + 仪器自检 | 67 |
| CH-459…463 | **第一轮全面审查** | 七类块、链接抽取、LLM 五语义、备份 v2 容器、测试体系批次、文档口径批次 | 67（全量 276→339） |
| CH-464/465 | **第二轮** | 3 高 5 中 1 低 + 测试面 14 项 + 文档漂移 4 处 | 67（项数不变，全量 339） |
| CH-466/467/468 | 运维与流程 | `ops/llm.md`、pre-push 红线钩子、仓库转公开 + 分支保护 | 67（无新增红线） |
| CH-469 | **第三轮** | 37 项 A 类 + 批次④文档口径；`_migrate` 补列、AAD 绑 header | 67→132 |
| CH-471 | **第四轮** | PIPE-1 / STOR-5 / STOR-3 / TEST-1 / TEST-3 五个 HIGH；X-1…X-15 登记 | 132（全量 543） |
| CH-473 | **第五轮** | 批次①②③④；145 项红线首证；serve 端口上报 | 145 |
| CH-474 | X-16 复发根治 | X-17 无条件终止本池 worker | 146 |
| CH-475 | **第六轮** | 12 项 A 类 + 2 项验证后延期；注入归因循环（R6-LLM-2） | 153 |
| CH-476 | **第七轮** | 32 项 A 类分 13 批；缝合方向反了（R7-PIPE-1）、池重建成本（R7-PERF-1）、分片 8 项就近红线 | 244 |
| CH-477 | T15 | 版本与编号四向对账做成红线 | 248 |
| CH-478 | R6 两个 B 出口 | PDF 有序列表分支 + 等价门缩进敏感度 | 252→256 |
| CH-479 | CI 矩阵口径 | push 面单解释器全量、跨版本改周度腿 | 260 |
| CH-480 | T12 常驻资产 | serve / work 双平台自恢复档 + 9.4 B-1 闭环 | 267 |
| CH-481 | NFR-19 捕获有界化 | launchd stdout/stderr 就地截断保尾 | 275 |
| CH-482 | 缩进几何重建 | S1 逐字符字宽 + S3 量化 + DOCX 保留前导空白 | 285 |
| CH-483 | **第八轮** | 数据安全链 + DOCX 结构 + 健壮性（A 类 25 项）；中文标题失明 R8-PIPE-1 | 308 |
| CH-484 | 产物孤儿 | 删除 × 在途转换修复 + 压测夹具入库 | 311 |
| CH-485 | **第九轮** | B0a/B0b/B0c/B1/B2/B3/B4 七批：Windows 守护兼容、异步删除守护、备份并发原子快照、DB 事实源、管线结构、队列搜索配置、2.7.0/DB8 收口 | 339 |
| CH-486 | **第十轮** | Windows 目标机收口 + 数据安全残差 + `windows-suite` + 综合语料；W-01 autocrlf 损坏 31 份黄金 | 344 |
| CH-487 | PDF 结构 / 合规 | 页眉页码、中文标题与候选窗口修复（2.9.2）+ 脱敏合规审计 | 368 |
| CH-488 | 复杂语料 / DOC | 旧版 DOC 转换 + 双模式验收（2.10.0） | 380 |
| CH-489 | **第十一轮** | 零页 / 表格 / 分页与数据安全收口（2.11.0） | 395 |
| CH-490 | **第十二轮** | 产物锁 / 编辑预检 / SQLite 驻留 / 编辑器修复（2.12.0）+ 跨进程重建锁 | 402 |
| CH-491 | **第十三轮** | 技术审查 + 产品审查（F-01…F-14 / D-01…D-20）+ 文档同步红线 | 404 |
| CH-492 / 493 | 规格与性能 | CLI 增强、事件/产物抽离、PDF 尾窗加密探测、并行阈值双条件、备份锁 PID 活性回收、S3 单趟家具剔除 | 404 |
| CH-494 | **X-18 根治** | 原生句柄探活 + AST 红线 + CI 挂死可定位性（2.12.1） | 419 |
| QA 2.13–2.15 | 效果优化三线 | O-01…O-12 / N-1…N-8 / D1…D13（当时未登记，CH-495 补账 +41） | 460 |
| CH-495 | **第十四轮** | F1–F12 / G1–G8 / A1–A7 / B1–B7 / 文档 #1–#11；A4/A5 等 37 项新红线 | 497 |
| CH-496 | 第十四轮 B 类本机项 | 14 项：白名单单一来源、`sweep_tmp` 抽模块、AST 错误码门、产物锁、0700/0600 收紧、serve 端口上报 | 508 |
| CH-497 | B1 + B-12 工具 | 单池排空（1.631s→1.194s）+ 常驻长跑夹具 | 513 |
| CH-498 | **第十五轮** | B-17 WAL fail-closed、B-16 缝合、B-11 第二轮走查（2 个 GUI/API 不一致）、§9.1 门禁预算冻结 | 521 |
| CH-499 | **第十六轮** | B-27 租约（DB 10）、B-3 归因去循环、B-14 跨卷取证 + `ensure_private` 真缺陷 | 542 |
| CH-500 | **第十七轮 + QA 第三轮** | 50 项编号发现 / 18 项独立实施 / B-30…B-46；**B-43、B-44 发布阻断**；2.18.0 | 580 |
| CH-501 | 实机自动测试体系 | 六层编排器 + 归因/扫描/追溯/优先级四模块 + 难度样本补全 + 用例卡回填 | **599** |
| CH-502 | **本文（技术综述层）** | 新增 `docs/technical-reference.md`：设计需求 / 架构 / 选型 / 实现 / 九域难点↔解法 / 开发史收敛；零代码、零产物字节 ⇒ 只升 `PLAN_VERSION` 3.35→3.36 | **599**（项数不变） |
| CH-508 | **B-52：OOXML 间接编号形态** | 三种间接编号接上：样式级 numPr 沿 `basedOn` 链继承、`numStyleLink` 委托编号样式、`lvl@pStyle` 反向绑定；环与悬空链保守回「无编号」；名为 `Heading` 的无数字样式不再兜底成 1 级。合成夹具 7 项（含三条反向守卫：悬空链 / 环状 `basedOn` / 缺 `numbering.xml`），六条独立变异中五条各自只打红对应一项、删 `pstyle_num` 初始化一条红 4 项（含目标项） ⇒ `PIPELINE_VERSION` 2.21→2.22 / `PLAN_VERSION` 3.39→3.40 | **735**（+7，逐项归因见 12.1） |
| CH-523 | **B-53 第十三批：附录 C 契约对账红线三条 gate 负断言补同主语正断言** | `tests/test_ch505_contract_sync.py` 三条「不得出现」（#62 解析器端点不得重复登记、#63/#64 附录 C 不得再承诺 `POST /jobs` 的 409 / HTTP 422）改写为成对判据：#62 `(dups, len(rows), n_lines) == ([], 21, 21)`（扫描行数与解析键数都钉契约常量 `EXPECTED_ENDPOINT_ROWS`）、#63/#64 `(5, False, False)`（必存错误码 5/5、409/422 各 0）；M-a～M-e 各自转红、C-1 对照保持绿；还原后 `docs/dev-plan.md`（`b7590164…`）与测试文件（`b02cdf03…`）md5 与改前逐字节一致 ⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.54→3.55 | **735**（项数不变，逐项归因见 12.1） |
| CH-522 | **B-53 第十二批：本机收口三条 gate 负断言补同主语正断言** | `tests/test_ch496_local.py` 三条「不得出现」（#34 G4' 回滚视图不得拼 `innerHTML`、#35 `cmd_serve` 不得用 `app.run(host=` 直跑、#36 队列 chip 点击不得退回 `renderQueue(QSTATE.tasks)` 纯客户端过滤）改写为「否定面命中 0 + 同容器命中 > 0」成对形态，两个数进同一条失败消息。三处容器都是**切片不自证**的形态（同页两个函数体、`cmd_serve` 体、chip 的 onclick 组），统一为「切分命中恰好一次 + 分隔符非空 + 同容器必存成员 3/3」：#34 `showHistory` / `rollbackTo` 各 3/3（`textContent`·`createElement`·`appendChild` 与 `/rollback`·`to_revision`·`fetch(`）、判据均 `(3, 0)`；#35 绑定/报告成员 `make_server(` / `server.server_port` / `print(` 3/3、判据 `(3, 0)`；#36 服务端过滤成员 `QSTATE.filter = ` / `loadQueue()` / `(QSTATE.filter === st` 3/3、判据 `(3, 0)`。本批打出**教训第 11 条：正例成员不得取自注释**——chip onclick 切片里 `status=` 只出现在一行注释中（命中即空壳），改取三个实现串后 M-f 注入 `renderQueue(QSTATE.tasks)` 才落新判据。red-first 变异：M-a `showHistory` 注入 `innerHTML`、M-b `rollbackTo` 注入 `innerHTML` → 均落新行 249 红 `(3, 1)`；M-c 全部 12 处 `document.createElement(` 改名 → 新行 249 红 `(2, 0)`；M-e `cmd_serve` 注释注入 `app.run(host=` → 新行 286 红 `(3, 1)`；M-f onclick 注入 `renderQueue(QSTATE.tasks)` → 新行 329 红 `(3, 1)`；M-d `make_server(` 改名与 M-g `loadQueue()` 改名分别先被同函数既有的正例断言拦在旧行 281 / 327（判据归属，按 CH-515 纪律不计入本批牙齿证据）。另设对照变异 C-1（同处加无害注释）保持全绿。还原后 `doc2md/static/editor.html`（`b8fe2d0e…`）与 `doc2md/cli.py`（`bfea7fdb…`）md5 与改前逐字节一致 ⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.53→3.54 | **735**（项数不变，逐项归因见 12.1） |
| CH-521 | **B-53 第十一批：审阅页文档清单三条 gate 负断言补同主语正断言** | `tests/test_ch503_docs_panel.py` 三条「不得出现」（#48 文档清单不得从队列 `QSTATE.tasks` 推断、#49 转义形态不得被裸插扫描器判成违规、#50 `cmd_convert` 不得落库）改写为「否定面命中 0 + 同容器/同夹具命中 > 0」成对形态，两个数进同一条失败消息。本批是 B-53 首个**纯静态判据**文件（读 `editor.html` 与 `cli.py` 的源码文本），三条原写法的容器切片边界不自证（`split(锚点)[1]` 取到文件尾），故改为「切分命中恰好一次 + 分隔符非空 + 同容器必存成员 4/4」：#48 依赖成员 `api(\`/docs?limit=` / `DSTATE.hasMore` / `DSTATE.rows` / `renderDocs()`、判据 `(4, 0)`；#50 产出面成员 `run_batch(` / `skip_existing_hit(` / `convert(` / `_apply_format(`、判据 `(4, 0)`（`save_ir` 在 `cli.py` 别处共 5 次，「0」说的是这条路径不落库）；#49 把 `good` 的 `esc()` 摘掉后同一串必须恰好命中 2，判据 `(0, 2)`。同批实测出**保前缀改名不是切断锚点**（改名成 `dAutoToggleRenamed` 时 `partition` 仍在前缀切开、变异 rc=0，只能作对照；要打破边界必须整行删除或改写）。六个变异各自转红（注入 `QSTATE.tasks` / 删 `DSTATE.hasMore` / 整行删锚点 / 字段集漏 `format` 只打新行 135 / cmd_convert 落库 / `run_batch` 改名），对照 C-1（无害注释）保持绿；还原后 `editor.html`（`b8fe2d0e…`）与 `cli.py`（`bfea7fdb…`）md5 逐字节一致。gate **735** / 全量 **1575** / 覆盖率 **7,942 stmts / 678 miss** 与上一轮全等，B-53 剩余 72→**69**。 |
| CH-520 | **B-53 第十批：页脚折行豁免三条 gate 负断言补同主语正断言** | `tests/test_ch503_furniture_wrap.py` 三条「不得出现」（#51 前一行硬句末时重复短行仍按页脚剔除、#52 文档首行无锚点不得自称残段、#53 跨页续段里那条页眉主语串不回潮）改写为「同主语在**输入全集**的出现数 + 剔除后的幸存数」成对形态，统一为 `(2, 0)`，两个数进同一条失败消息。本批立出**正例面要两半**：`(2, 0)` 对「整带清空的黑洞实现」同样全绿——边距带连正文一起删光，两个数照样成立；所以输入计数之外还必须钉「不该被动的内容还在」（#52 原先除了那条否定什么都没有，单独补正文存活面 `(2, 2)`、新行 109；#51/#53 同一函数里本来就有正文计数断言，不重复补）。六个变异各自转红（M-a `_strip_page_furniture` 整体失效 → 三条落行 83/101/158 并打「剔除后剩 2 处」；M-b 整页清空黑洞 → **只有新行 109 的正文存活面响**，旧写法「不在结果里」在这种形态下全绿；M-c `_is_wrap_tail` 恒真 → 三条 + 参数化六档红；M-d 把 `_banded` 的 `top` 移出边距带（家具信号根本接触不到候选）→ 三条红；M-e 夹具只留一页（重复计数到不了 2）→ 三条红；M-f 夹具里主语串整体消失 → 三条打「在输入全集出现 **0** 处」，而旧写法 `not in kept` 在这个形态下恒真——正是本批判据要挡的空壳），对照变异 C-1（`_is_wrap_tail` 恒假）的红落在①侧（折行尾巴必须保留）的既有断言面上、不落本批任何新判据 ⇒ 三条成对判据只管②侧，没有越位去抢①侧的判据归属；还原后 `doc2md/pipeline/s3_structure.py`（`1f04782d…`）md5 与改前逐字节一致 ⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.51→3.52 | **735**（项数不变，逐项归因见 12.1） |
| CH-519 | **B-53 第九批：分片缝合四条 gate 负断言补同主语正断言** | `tests/test_ch473_fixes.py` 四条「不得出现」（#2 未锚定片的真实短列表项不被「完整版」替换、#3/#4/#5 迁移前遗留表不含 `revision`/`role`/`aux` 列）逐条改写为「否定面命中 0 + 同主语命中 > 0」，两个数进同一条失败消息。本批补出**命中基准的第四种取法**：否定面读「被丢掉的元素不该出现在存活者容器里」时命中基准恒为 0——`overlap_dedupe` 的返回值压根不含丢弃块，所以在同一轮输出上数被丢标记是恒真的；改按**输入集合**计数，正数取**同一夹具构造器搭出的第二个锚定场景**（未锚定轮 0 / 锚定截断轮 1），同时把成对判据前置到既有 `assert out == [...]` 之前以保证判据归属。数据库形状自检是同一病灶的另一头：迁移前遗留表没建起来时「不该有的列不缺」与「该有的列缺」同时全绿，基准取**同一张表契约承诺的必存列数**（5 / 4 / 3），表被抹平即红。red-first 九个变异各自转红（M-1 `revision` 没摘 → 行 1223 红 `[1,0,0]`/`[5,4,3]`；M-2 `role` 没摘 → `[0,1,0]`/`[5,3,3]`；M-3 `aux` 没摘 → `[0,0,1]`/`[5,4,3]`；M-4 `blocks.text` 改名 → `[0,0,0]`/`[4,4,3]`；M-5 `tokens.label` 改名 → `[0,0,0]`/`[5,3,3]`；M-6 片界锚定表覆盖所有块 → 行 173 红 `1 / 1`；M-7 不写 `dropped_dup_of`、M-8 截断分支整体短路、M-9 锚定轮不传片界 → 同一行三处红 `0 / 0`），另设一条对照变异（`revision` 摘除动作直接失效、列数 +1）的红落在夹具自己的位置式 `INSERT`（行 1186/1209）、不落本批判据，故同族变异要保列数打；还原后 `doc2md/pipeline/sharding.py`（`90023f58…`）与 `tests/test_ch473_fixes.py`（`e99058bc…`）md5 与改前逐字节一致 ⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.50→3.51 | **735**（项数不变，逐项归因见 12.1） |
| CH-518 | **B-53 第八批：服务面四条 gate 负断言补同主语正断言** | `tests/test_ch504_service_faces.py` 四条「不得出现」（#58 审计降级行不带原始列名 `cascade_json`、#59 权限矩阵无 `rerun` 死键、#60 审阅页终态耗时不从 `finished - created` 起算、#61 Content-Type 报错体不漏 `token`）逐条改写为「否定面命中 0 + 同主语命中 > 0」，两个数进同一条失败消息。本批补出**命中基准的第三种取法**：否定面读「某个键不该出现在净化后的响应体里」时，命中基准取**同一容器契约上必须存在的公开键数**——审计降级行 6 个公开键（`doc_id`/`source_key`/`actor`/`ts`/`kind`/`cascade`）、错误体 3 个契约键（`code`/`message`/`detail`）；容器被抹平成空壳时字面否定仍然全绿，只有公开键数能把「干净」与「看不见」分开。同一批复证**判据归属**：#58 的成对判据前置到既有的 `cascade == {}` 之前，M-1a 才落在新行 196。red-first 七次变异各自转红（`pop`→`get` / 插入 `rerun` / 摘掉 `submit_job` / 退回 `finished - created` / 换成 `finished - ts` / 回显 `token` / 丢掉 `detail`），另设一条对照变异（降级行整行抹平）由测试自身的 `d["doc_id"]` 推导式先抛 `KeyError`、不落本批判据；七处还原后 `doc2md/api.py`、`doc2md/auth.py`、`doc2md/static/editor.html`、`doc2md/errors.py` md5 与改前逐字节一致 ⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.49→3.50 | **735**（项数不变，逐项归因见 12.1） |
| CH-517 | **Windows 门禁层宿主耦合根治 + 六轮远端取证回填** | `tests/test_v24_gates.py::test_gate_test_env_neutralization` 的注入面判据原写成字面列表 `[k for k in _PROXY_VARS if k in os.environ] == [injected]`：Windows 的 `os.environ` 用 `encodekey=str.upper`、键大小写不敏感，注入 `HTTP_PROXY` 后 `http_proxy` 也算命中，判据拿到两个元素恒不等于单元素列表——于是 `windows-suite` 门禁层自 CH-511 起连红六推（run `40545285156`…`40566970109`，逐轮 `1 failed / 734 passed / 840 deselected`，失败项同一个），而同一份 735 项在 `merge-gate (3.11/3.12/3.13)` 三档 Linux 与 ubuntu `full-suite` 全绿：症状形态本身就是宿主耦合的判据。修法沿用「宿主耦合落测试侧、不用 skip」的既有纪律——判据改为大小写归一的键集合，注入面断言单元素集、清除面断言空集，并把实际与期望集合同时打进失败消息（附「空集合 ⇒ 宿主吞掉注入 ⇒ 后一条归零断言退化成空集恒真」的说明），成对判据的牙齿不动。red-first：Windows 语义模拟下旧判据 `False`、新判据 `True`；M-1 删夹具清除步 → 清除面红、M-2 缩 `_PROXY_VARS` → 注入面红；还原后 `tests/conftest.py` md5 逐字节一致（夹具本体未改）⇒ 零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.48→3.49 | **735**（项数不变，逐项归因见 12.1） |
| CH-516 | **负断言补同主语正例（B-53 第七批，QA 判定面口径红线文件）** | `tests/test_ch504_qa_caliber.py` 四条「不得出现」改写为**否定面命中 0 + 同主语命中 > 0**，本批把最早那一种**空集恒真**做成两个可复算的面：① `CONTRACT_RULES` 是 `LINT_CLASS` 的派生集合，把派生断掉（置空集）后「MD040 不在契约档」原本照样全绿，成对判据打出 `(0, 0)` 对 `(0, 8)` 才红；② `fence_no_lang` 的证据串摘掉`MD040_DIALECT_REASON` 后「不得自称契约类」原本也全绿——一条不引任何依据的串本来就长这样，成对判据打 `(0, 0)` 对 `(0, 1)`。由此写明**命中基准的取法**：否定面读集合成员就取「同一容器里必须存在的成员数」，读专有名词就取「同一串里必须存在的另一个名词」，不另找不相干的键凑数。#57 沿用 CH-515 的判据归属纪律，把成对判据前置到精确串判据之前，恒空（M-5）与恒发（M-6）两个相反方向的变异才都落在新增行 137。七次 red-first 变异（`MD040` 改回 `contract` / `CONTRACT_RULES` 置空 / 证据串写回「契约类」/ 证据串摘掉改档理由 / 定性从句换回已作废成因 / 截断告知恒空 / 告知恒发）各自转红并落在新增行 71 / 91 / 120 / 137，还原后 `tests/qa/mdscore.py`（`3a4b0f9e…`）与 `tests/qa/defect_scan.py`（`003ee557…`）md5 逐字节一致；被测面在 `tests/qa/`、零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.47→3.48 | **735**（项数不变，逐项归因见 12.1） |
| CH-515 | **负断言补同主语正例（B-53 第六批，实机测试判定层红线文件）** | `tests/test_ch501_qa_auto.py` 四条「不得出现」改写为**否定面命中 0 + 同主语命中 > 0**，本批处理的是**判据归属**形态：同一测试里站着更强的同夹具判据（`_input_keys` 异源恒等式、精确条目数 7、精确拟新号 `B-46`）时，变异的红永远落在老断言上、新补的成对判据压根执行不到——修法是把成对判据前置，而不是删老判据。另一半方向由**放行轮**补上：把 `c.pdf` 两条记录改回 `status="ok"`、其余输入一字不动，`gate_verdict` 与 `split_table` 必须各命中 1、条目账必须 7→9（恒等式只看得见「漏进来」，看不见「该出项却不出项」）。#46 用同主语段内面（`b_rows(_DP)` 必须仍含 B-43/B-44/B-45 = 3）钉住 §12.7 截段，#47 用 `W` 的两维各自单独打满仍落出中间分（0.6 / 0.4）钉住权重表是活的。六次 red-first 变异（归因层与缺陷层各摘一次 `status` 过滤、按名单跳过两个键出项、`b_rows` 改全文扫描、`W` 加 `fixability: 0.0`）各自转红并落在新增行 256 / 257 / 287 / 311，还原后 `tests/qa/prioritize.py` md5 逐字节一致（`855e9291…`）；被测面在 `tests/qa/`、零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.46→3.47 | **735**（项数不变，逐项归因见 12.1） |
| CH-514 | **负断言补同主语正例（B-53 第五批，LLM P0 契约红线文件）** | `tests/test_llm_p0_contract.py` 五条「不得出现」改写为**否定面命中 0 + 同主语命中 > 0**，处理的是第四种恒真形态——**补位夹具自己没通过产品的准入门槛**：`_candidate_score` 对标题的入门条件是 `len(t) > 60`，47 字的「正例标题」根本不进候选，补上去的命中仍是 0（本轮把标题补到 68 字，并先跑命中基准再写断言）；命中基准按实现的真实计数口径取数——`"marker"` 在参数契约里天然出现 2 处（`required` 表元素 + 说明键），基准是 2 不是 1；#70/#71/#72 三条否定合并成一条 `assert (narrow_hits, wide_hits) == ([0, 0, 0], [1, 1, 1])`，变异面逐 op 独立。四次 red-first 变异（`_allowed_ops_for` 摘掉表邻过滤、`_ALLOWED_BY_TYPE` 逐类型摘掉 `fix_heading_level` / `fix_list_marker` / `mark_table_boundary`）各自转红并覆盖五个位置，还原后 `doc2md/llm.py` md5 逐字节一致（`d15b7277…`）；零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.45→3.46 | **735**（项数不变，逐项归因见 12.1） |
| CH-513 | **负断言补同主语正例（B-53 第四批，CH-476 轮红线文件）** | `tests/test_ch476_fixes.py` 六条「不得出现」改写为**否定面命中 0 + 同主语命中 > 0**，处理的是第三种恒真形态的极端情形——**被测通路在那一轮刻意什么都不写**：删除竞态分支 `mark()` 返回 False 即放弃留痕，那一轮 `events` 表是空的，「没有 `job_done`」对任何实现都真，补位只能给同一张表补一条正常完成作业（竞态轮 0 / 正常轮 1）；`content_drift` 用同函数的真漂移作业做基准（未改写 0 / 真漂移 1）；修订接受口径两条（夹具与黄金样本）改读**源产物自身**的 `word/document.xml`，`w:delText` 各计 1、正文必须 0；块级 `has_revision` 用同域对照（无修订段落 0 / 带信号段落 3）；脚注不分页的判据补一条同域真分页 DOCX，同一个 `page_break` 计数判据必须命中 1。五次 red-first 变异（`api.py` 竞态分支补落 `job_done`、`worker.py` detail 写入条件恒真、`s1_parse.py` 把 `w:delText` 拼进正文（同时打红两条）、`s3_structure.py` 修订信号无条件置位、同文件 `\x0c` 结构行分支短路）各自转红，还原后四份产物 md5 逐字节一致（`doc2md/api.py` `1657445f…`、`doc2md/worker.py` `99c48703…`、`doc2md/pipeline/s1_parse.py` `e890ff03…`、`doc2md/pipeline/s3_structure.py` `1f04782d…`）；零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.44→3.45 | **735**（项数不变，逐项归因见 12.1） |
| CH-512 | **负断言补同主语正例（B-53 第三批，第十七轮红线文件）** | `tests/test_ch500_round17.py` 六条「不得出现」改写为**否定面命中 0 + 同主语命中 > 0**：`POST /tokens` 的 400 响应零 `token` 键、同会话正常签发必须命中 1；外链换行压平与正文软换行用同一份 IR、同一个 `\n` 计数判据做**同域对照**（链接面 0 / 正文面 1）；`preserve_strike` 默认档 `~~` 计 0、opt-in 档计 2；交付 meta 的 `shard` 内部锚计 0、文档级 `shard_count` 计 1（后者为 0 即说明读到空 dict、前一条否定恒真），`IR.meta()` 的平铺用**注回真实对象**验证——注入 `extra_meta["shard"]` 后 `extra_meta` 与 `meta()` 必须各命中 1。五次 red-first 变异（`s4_render.py` 换行不压平、`render()` 缺省翻 True、`convert.py` 摘掉 `extra_meta.pop("shard")`、`ir/model.py` 不再平铺、`api.py` 表单折叠 + role 缺省回退）各自转红，还原后四份产物 md5 逐字节一致（`doc2md/api.py` `1657445f…`、`doc2md/pipeline/s4_render.py` `d59bc27d…`、`doc2md/pipeline/convert.py` `2eb1c0e1…`、`doc2md/ir/model.py` `c2f4437a…`）；零产品语句 ⇒ `PIPELINE_VERSION` 2.22.0 不动、`PLAN_VERSION` 3.43→3.44 | **735**（项数不变，逐项归因见 12.1） |
| CH-511 | **负断言补同主语正例（B-53 第二批，gate 第一层文件）** | `tests/test_v24_gates.py` 四条「不得出现」改写为**否定面命中 0 + 同主语注入面命中 > 0**：删除审计墓碑的 `text` 键（`ensure_ascii=False` 计数 + 注入缺陷形状必须恰好命中 1）、服务日志中文 needle 的对照记录**走同一个 `RotatingJsonlWriter`**（把「产品写入端保持 `ensure_ascii=False`」从隐性依赖变成红线）、`SCRUB_MAX_LEN` 的短/长字段双档（66 字必须落 `<sha256:` 指纹）、O-02 环境中立化夹具（解出生成器本体并断言 `isgeneratorfunction`，注入 `HTTP_PROXY` 后 setup 段必须归零、teardown 恢复快照）。四次 red-first 变异各自转红、还原后 md5 逐字节一致（`doc2md/api.py` `1657445f…`、`doc2md/ops/logging.py` `cf81f9ec…`、`tests/conftest.py` `4ae721c0…`） | **735**（项数不变，逐项归因见 12.1） |
| CH-510 | **负断言补同主语正例（B-53 首批）** | `tests/test_ch469_service.py` 三条「不得出现」改写为**命中数判据 + 对照面命中 > 0**：detail 泄漏判据显式 `ensure_ascii=False`（默认转义让中文关键词恒不出现，前件假）、CSS 选择器插值改返回列表、探针载荷改计数；三次 red-first 变异各自转红、还原后 md5 逐字节一致 | **735**（项数不变，逐项归因见 12.1） |
| CH-509 | **Windows 门禁层宿主耦合修复** | `tests/test_ch505_perms_coverage.py` 6 项在 `os.name == "nt"` 下被产品导向 `icacls` 或依赖真实 POSIX `stat` / 路径渲染；用 `posix` 夹具代理 `perms.os`、`Path.stat` / `Path.chmod` 打桩、缓存键由宿主 `Path` 现算，不用 `t.Skip`。零新增用例、零产品语句 ⇒ `PLAN_VERSION` 3.40→3.41，`PIPELINE_VERSION` 2.22.0 不动 | **735**（项数不变，逐项归因见 12.1） |
| CH-505～CH-507 | **QA 第四轮本机优化** | 九项本机可执行优化合批：契约表 ↔ 21 路由双向对账红线（OPT4-5）、`run_corpus --diff-report` 四类行差异（OPT4-8）、LibreOffice 形态 DOCX 三份语料（OPT4-4）、`perms.py` 平台 × 入口 × 失败矩阵 23 项（OPT4-11）、源文本转义单一来源 `escapes.py`（OPT4-16 / B-37 关闭）、双栏空白带查询索引（OPT4-17）、OOXML 编号多态排查与 `lvlOverride` 修复（CH-506 / OPT4-12）、QA 纪律条文 + 负断言清单（OPT4-10）、活文档行号引用棘轮 173（OPT4-19 / B-50 部分）⇒ `PIPELINE_VERSION` 2.20→2.21 / `PLAN_VERSION` 3.38→3.39 | **728**（+45，逐项归因见 12.1） |
| CH-504 | **第十九轮**（三条产物字节级修复 + 两面一致性 + 判定层口径） | 17 项实施 + 文档收口：**R19-PIPE-1** 交付 zip 可复现（显式 `ZipInfo`）、**R19-PIPE-2** 等宽行链接标注不再被吞（残留登记 OPT4-18）、**R19-PIPE-3** `numFmt="none"` 不伪造编号；R19-PERF-1 页眉去重空集短路；服务面九项（`docs --offset` / cron 日报 / 产物清除幂等 / 损坏 JSON 降级 / `Store.close` 线程隔离 / `POST /tokens` 角色必填 / 权限矩阵死键 / `meta` 降级边界 / `PUT /settings` 点号键）；审阅页四项（`SRCH` 赋值 / 审计取最后一页 / 耗时列原点 / token 去抖）；判定层四项（MD040 改档 dialect / 重复锚点证据 / 已知良性登记表归属 / 证据串引用）；配套四份 QA 文档 `docs/qa/round4/` 与审查报告 `docs/review-round19-CH-504.md` |
| CH-503 | **第十八轮**（第十七轮「本机可自证」项全部推到出口） | 7 项实施 + 文档 10 处收口：**两条产物字节级修复**（R18-PIPE-1 渲染红线判据、R18-PIPE-2 折行残句尾巴免剔除）、GUI 转义汇点化与扫描面按语句取全（R18-GUI-3）、空态文案假承诺经实机走查抓到（R18-GUI-4）、判定层反空跑与缺陷扫描基线冻结（R18-QA-1/2）、`GET /docs` 文档清单面板落地（B-36 关闭）、指标 runner（B-34 本机半）、页区间五面等价性证明（B-38 第一步）、文档自身的两处覆盖率继承错与 §12.2 产物口径未裁定（R18-DOC-1 新立「三写必须可复算」红线）⇒ `PIPELINE_VERSION` 2.18→2.19 / `PLAN_VERSION` 3.36→3.37 | **642**（+43，逐项归因见 12.1） |

### 7.3 缺陷编号体系

读报告前先认这套命名（多套并存是历史事实，不是设计）：

| 前缀 | 含义 | 例 |
| --- | --- | --- |
| `R<n>-<域>-<i>` | 第 n 轮审查在某个域的发现（域 = PIPE/STOR/SVC/SEC/OPS/PERF/CMD/CFG/API/TEST/LLM/GAP/DOC/SQL/PKG） | R7-PIPE-1（缝合方向反） |
| `X-nn` | 「需本机以外环境才能验证」的登记表，后来被具体存活缺陷续编 | X-16/X-17 池退出死锁、X-18 `os.kill` |
| `B-nn` | 待办/受限项主表（dev-plan §12.7），B-1…B-49 | **B-43/B-44 = 发布阻断** |
| `D<n>` | 审查轮的代为决策 | D1 幂等 DDL 重放、D2 领取量解耦、D4 尺寸门限、D12 版本升级判据 |
| `O-nn` / `N-nn` | QA 优化第一 / 第二轮的过程项 | O-09 家具数字折叠、N-3 生成器自检直读源 |
| `C-nn` / `R-nn` / `F-nn` | QA 偏差分析的成因 / 缺口 / 功能项 | C-31「期望基线自测产物」 |
| `TC-M*-xxx` / `TC-0xx` | QA 用例卡（模块制 / 人设×难度制） | TC-005/TC-011（D02，P0） |
| `W-nn/S-nn/O-nn/P-nn` | 第十轮 Windows 真机发现 | W-01 autocrlf 损坏黄金 |
| `AR-nn` | 第十七轮架构审查线（该轮 50 项编号发现里的架构段 AR-1…12；同一轮的实现 ID 走 `R17-*` 族） | R17-PIPE-5 家具剥离清零探测字段 |
| `G1/G6` | 门禁性质编号（分片≡单片 / 关提示词后逐字节一致） | — |
| `CH-nnn` | 变更登记条目（唯一的「时间轴主键」） | CH-422…CH-512 |
| `E####/W####` | 错误码 / 告警码段位 | E4221 / W1045 / W1046 |

### 7.4 反直觉发现集（读代码前值得先看一眼）

1. **产品与它的自检用了两套正则**，导致「整体功能失效 + 测试全绿」——64 个 H1 全丢，测试一个没红（R8-PIPE-1）。
2. **`Counter.update` 累加 vs `dict.update` 覆盖**：同一行代码的两种语义，1122/3000 处不一致靠专门反例夹具才抓到。
3. **更细的流式化让内存变差**：驻留主体是 IR 块不是页缓冲（CH-460 负结果，至今保留注释）。
4. **并行收益被池重建吃光**：4 worker 只换来 +4.5%（R7-PERF-1）。
5. **`os.kill(pid,0)` 在 Windows 上会广播 Ctrl+C**：探活动作本身能杀掉被测负载（X-18）。
6. **`PRAGMA journal_mode=WAL` 不吃 `busy_timeout`**：竞争时等 31.0s 才抛，「加个超时」是错的修法（B-17）。
7. **macOS 的 FAT32/exFAT 卷允许软链**：「可移动盘没有软链」这个安全前提是假的（B-14）。
8. **chmod 在 FAT 卷上是 no-op**：隐私权限体系在这类介质上从未生效，且告警永久静默（B-14）。
9. **缝合比较方向写反**（`kb[-1]==ka[-1]+1`）：对递增序列是死代码，对递减序列是错合并——只在人工细看时才暴露（R7-PIPE-1）。
10. **0.99 字符相似度是粗漂移探测器，不是等式证明**；而「门禁看不见」这个说法本身也要靠实测校正（CH-478）。
11. **三层测试全绿同时存在发布阻断级静默损伤**：只有独立真值腿看见它（B-43/B-44）。
12. **`find_spec` 预检被否决**：虽然省 86–93ms，但把「真能 import」弱化成「spec 存在」，`doctor` 会失去意义（dev-plan §12.3）。
13. **junitxml 把 `xfail` 折进 `skipped` 属性**：不拆两栏就会把 6 个已知缺陷读成 6 个跳过。

### 7.5 质量趋势（数字只取各轮收口 `--junitxml`）

- 红线层 **35 → 735**（十九轮），其中六次跃迁各对应一次方法论升级：CH-469 的 67→132（补分文件实算）、CH-476 的 153→244（32 项 A 类逐批配红线）、CH-500 的 521→580（独立真值）、CH-501 的 580→599（实机层六条产品级断言）、CH-503 的 599→642（**判定层自身的牙齿**：红线判据双向、扫描面取全语句、助手带负夹具、文案反向核对产品源码）、CH-504 的 642→683（服务面与审阅页九项 + 判定层四项）、CH-505～CH-507 的 683→728（**本机可执行优化合批**：契约表 ↔ 路由双向对账、差异报告、权限矩阵、转义单一来源、编号覆盖层）、CH-508 的 728→735（间接编号三形态 + 三条反向守卫）；CH-509 为 Windows 宿主耦合修复，项数不变（仍 735）。
- 全量 **146 → 1575**，**0 failed**；skip 长期只有 2 条平台门控（Windows junction），CH-501 起增加难度骨架 TODO-skip 与 xfail，全部**显式列名并拆两栏**（CH-508 的 16 条 = 真 skip 10 + xfail 6；xfail 不是 pass；CH-509 只改测试的宿主驱动方式，skip 口径不变）。
- 语句覆盖率 **91%**（CH-508 终轮 7,942 stmts / 680 miss，精确 91.44%；分母较 CH-507 的 7,886 增 56 = CH-508 间接编号三形态的新增语句；`perms.py` 由 40%（75/45）升至 **100%（75/0）**——平台 × 入口 × 失败矩阵把此前不可达的替身分支全部覆盖，真 DACL 仍属 B-14 受限项；CH-509 为测试改动轮，覆盖数字沿用）；历史上两处「沿抄上一档百分比」的继承错由 R18-DOC-1 红线当场抓出并改正。
- 语料回归 **25/25 逐字节**（新增 `docx_12_lo_heading_numbering` / `docx_13_bullet_glyph` / `docx_14_multilevel_hidden` 三份 LibreOffice 形态 DOCX）；难度语料 **6/10**（D02→B-43、D05→B-44 失败，D08→B-45、D10→B-47 须人工定档）。

### 7.6 遗留项与发布判定

**发布判定＝阻断**（dev-plan §12.10 / §12.7，CH-500 定档，CH-501 未推翻）。

需要**人工介入**（本机无法验证，不许由工具代做）：B-43 / B-44 的逐页 PDF 真值修复或书面豁免；B-45（层级口径，样本期望与产品映射冲突且无红线钉住）、B-47（CID 乱码对外口径）、B-48（TOC 域缓存重复输出）三项需裁定；B-49（真实样本腿恒 `not_run` 属诚实状态，需投放样本）；B-15 业务 PDF 标题真值；B-35 PDF 标题人工真值（建议优先）；B-11 跨浏览器；B-41 `.doc` 双后端交叉验证。

**本批新登记**：B-51（`isLgl` / `lvlRestart` 两个编号多态需 Word / LibreOffice 渲染真值，本机只做静态排查——见 `docs/qa/round4/ooxml-polymorphism-audit.md`）；~~B-52~~（**已由 CH-508 关闭**：`basedOn` / `numStyleLink` / `pStyle` 三种间接编号与 `Heading` 无数字样式名）；B-53（153 条负断言里 115 条缺同主题正夹具）。B-53 **本机可执行、尚未实施**，不属于外部受限项。

需要**外部环境**：B-40 SMB/网络卷与 junction 复测；B-6 Windows spawn 池实测；B-8 跨机基准；B-9 `serve`/`work` Windows 半侧；B-12 真实长档长跑（30 分钟档只证明「无渗漏」）；B-14 剩余（真 DACL 分支——本批已把 `perms.py` 的替身分支覆盖到 100%，真卷仍待 B-14 环境）；B-34 **剩余半**＝把指标升成 CI 门禁判据（前置 B-35 野外人工真值；逐篇汇总的 `tests/corpus/run_metrics.py` 已由 CH-503 本机落地，不再是缺口）；B-46 难度腿入门禁（前置 B-43/44，切换点 `tests/qa/test_hard_gate.py:34` 的 `GATE_READY`（当前 `False`，`:45` 据此在 `gate` 与 `corpus` 之间二选一））；B-51 渲染真值；以及推送后的远端六作业复验（本会话未授权推送）。

**性能优先级最高的三项**：B-38（页区间并行，S1 占 97%）、B-39（绕 pdfplumber）、B-30/B-31（仓储化与三大文件拆分，属可维护性债）。

---

## 8 运维与使用要点（薄壳，细节在 README 与 ops/）

- **安装**：`pip install -e .`（发布前需空 venv 验证 `.[dev]`，属 `L4_install/clean_venv_install` 的 GAP）。
- **日常**：`doc2md convert` 单文件；`work --enqueue` 投递 + `work --loop` 常驻消费；`watch` 稳定投递；`serve` 起审阅页与 API。
- **自恢复**：macOS 四档 launchd（日/周定时 + serve/work 常驻），Windows 四档任务计划 XML（`install-doc2md-tasks.ps1`，常驻需 `-WithResident` 显式开关）。launchd 默认 cwd 是 `/`，故 plist 必须写 `WorkingDirectory` 与显式 `--root`。
- **备份**：`backup`（本地快照）/ `restore` / `verify` / `drill`（恢复演练，输出只含计数与指纹）。外置介质强制密文；v1 容器只解密不再生产。
- **体检**：`doctor` 是环境与契约的自洽检查（离线断言、legacy 后端、版本一致、模型可发现），退出码 0 才算可发布前置。
- **实机测试**：`.venv/bin/python -m tests.qa.realtest` 一条命令跑六层并给出 `overall` / `NOTRUN` / `GAP`。

---

## 9 附录：四向索引

| 主题 | 需求 | 实现 | 门禁/测试 | 规格与过程文档 |
| --- | --- | --- | --- | --- |
| PDF 版面恢复 | FR-101 | `s1_parse.py:457`/`:292`/`:511` | `test_pdf_columns.py`、`test_s3_blocks.py` | dev-plan §2.1 / §4.2；round8 / round9 报告 |
| 文本层体检 | FR-103 | `s1_5_gate.py:276` | 门拒绝矩阵 | dev-plan 附录 D（E4221）；`spec-ch492-p1-a1.md` |
| 结构与标题 | FR-104 | `s3_structure.py:623`/`:30`/`:270` | `test_ch478_fixes.py`、家具红线 | round6 / round8 / round17（AR-7） |
| 分片与缝合 | FR-126 | `sharding.py:28`/`:277`、`convert.py:292` | `test_sharding_unit.py` 26 项 | round7（R7-PIPE-1）、CH-425/427/438 登记 |
| LLM 修复 | FR-404 | `llm.py:22`/`:256`/`:457`/`:636`/`:434` | `test_t2_llm_server.py`、`test_ch499_attribution.py` | `ops/llm.md`；round6（R6-LLM-2） |
| 编辑与回滚 | FR-203 / 206 | `edits.py`、`api.py:835`/`:853` | `test_batch2_storage.py` | dev-plan §5.2（E4251） |
| 检索与 CJK | FR-204 | `fts.py:49`/`:73`/`:108` | `test_ch473_fixes.py`（STOR 族） | dev-plan §4.7 |
| 队列与租约 | FR-702 | `worker.py:67`/`:105`/`:225`、`db.py`（DB 10） | `test_ch499_lease.py` 11 项 | round7（X-16/17）、`ops/bench/b27_reclaim_lease_probe.py` |
| 批量与池 | FR-701 | `batch.py:250` | `test_ch497_local.py`、`test_ch498_drain_e2e.py` | §12.3（1.631→1.194s）、B-16/B-12 |
| 备份与恢复 | FR-607 / NFR-18 | `doc2md/ops/backup.py:37`/`:123`、`drill.py` | `test_m8_ops.py`、`test_ch494_fixes.py` 15 项 | `ops/backup.md`；CH-461/485/494 |
| 安全边界 | NFR-6/9/20、FR-127/129 | `security/*`、`perms.py:69` | `test_ch469_ops.py` 54 项、`test_ch499_crossfs.py` | `compliance-audit-CH-487.md`；§12.7 B-14/B-40 |
| 服务与 GUI | FR-205/209/210/211 | `api.py`、`auth.py:87`、`static/editor.html` | `test_api_endpoints.py`、`test_ch495_service.py` | dev-plan 附录 C；round14（G1–G8） |
| 配置契约 | FR-209 | `doc2md/config/loader.py:23`/`:45`/`:189` | `test_spec_defaults_and_example_share_one_key_set` | 附录 A、`config/doc2md.example.toml` |
| 实机测试 | 全部 | `tests/qa/realtest.py` + 四模块 | `test_ch501_qa_auto.py` 19 项、`test_hard_gate.py` | **dev-plan §8.7**；`docs/qa/automated-test-plan-v2.md`、`casebook.md` |

---

## 10 收口记录

- 本文写作基于以下实测：139 个提交（锚定 `0b209f8`，`git rev-list --count` 实测；CH-523 的收口提交随后产生）、`doc2md` 包 15,802 行 / 47 个 `.py`、7 类块封闭集、5 个运行时依赖、4 个版本计数器现值（2.22.0 / 14 / 10 / v3.55）、18 个 API 路径、17 个 CLI 子命令、错误码 15 个段位——全部由 `.venv/bin/python` 与 `grep` 在写作时逐个复核。
- 文中所有 `file:line` 锚点在写作时**逐个 grep 校验存在**（§2 的 34 个需求落点、§5–§6 的实现位置）。代码移动后行号会漂，函数名与模块路径更可靠。
- 与 dev-plan 的分工边界：本文**不新增规格条款、不改判定口径、不登记 B 项**。任何新结论先落 dev-plan，再回写本文。
- 已知本文的局限：逐轮一览表（§7.2）把 137 个提交压成 55 行 CH 条目，证据细节必须回到第十一章与对应审查报告；QA 四轮（O/N/D 三系列 + 第四轮 OPT4 系列）在本文只在 §6.8 出现，未逐条展开。
