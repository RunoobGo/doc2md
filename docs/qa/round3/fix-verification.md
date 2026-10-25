# 第三轮修复取证（CH-500 收口 · 第十七轮系统性审查）

本文件回答一个问题：**本轮声称改好的每一项，凭什么说它好了。**
口径纪律：

- 只登记**已实施且本机可自证**的改动；未实施者一律引向 dev-plan 12.7 的 B 编号，
  本文不替它们声称任何完成度。
- 每项给三层证据中的可得层：① 门禁红线（`pytest -m gate` 的具体用例）、② 修复前探针
  （`/tmp/qa-r3/r3probe/RESULTS.md` 的 P01…P16，逐条留下修复前的原文输出）、
  ③ 收口复跑（本文 §3 的数字）。
- 「红线通过」不等于「修好了」，只等于「该行为被钉住且当前与声明一致」。因此 §5 单独
  列出**本轮无法自证的部分**，包括反向验证只做了白盒的那几项。

---

## 1. 代码面修复 → 红线映射（18 个登记项 / 38 项红线）

| 登记项 | 修复前实测症状 | 落点 | 红线（`tests/test_ch500_round17.py`） | 修复后取证 |
| --- | --- | --- | --- | --- |
| R17-API-1 | `PUT /settings` 带 urlencoded `api.role=admin` 返回 200 但静默按默认档写回；`request.data` 恒空（表单被 Werkzeug 消费进 `request.form`） | `doc2md/api.py` `body()` | 表单型 Content-Type 且非空 → E4001；真·空 body 仍默认 `{}`；JSON 路径不变 | 探针 P-A 复现 → 现由红线钉住；gate 全绿 |
| R17-API-2 | `?since=1e20` 原样进 SQLite 绑定 → `OverflowError` → 500/HTML | `doc2md/api.py` `_int_arg` | 三个端点（`/events?since`、`/audit/deletions?offset`、`/jobs?limit`）越界整数一律 400 + 契约体（一条用例按三端点参数化 = 3 项） | gate 全绿 |
| R17-API-3 | 路由级 `HTTPException`（405/415）透传成 Werkzeug HTML 页，违反附录 C 契约 | `doc2md/api.py` 错误处理 | 405 走 `{code,message,detail}`；404 与 413 保留各自专用处理器（防止过度收口） | gate 全绿 |
| R17-API-4 | 连续三次 500 后 `logs/app.jsonl` **字节数不变**（`RotatingJsonlWriter.write()` 不 flush） | `doc2md/api.py` `_unhandled` | 500 日志同请求即落盘（断言文件大小与行数即时变化） | 实测复现过 → 修复后红线覆盖 |
| R17-API-5 | `/jobs` 缺 `total`/`has_more`，`/docs/<id>/audit` 有 `total` 缺 `has_more`——截断被调用方当全集 | `doc2md/api.py` + `storage/repo.py` | `/jobs` 暴露 `total`+`has_more`；audit 补 `has_more` 且保持 rowid 升序 | gate 全绿 |
| R17-API-6 | 附录 C 一直把 `GET /docs` 写成契约面，**路由不存在**；`repo.list_docs` 只有测试消费 | `doc2md/api.py` | 新增 `GET /docs` 信封（分页 + `has_more`），并把 D9 的 `limit=0 → has_more=False` 铺到 `/jobs`、`/docs`、`/audit` 三处 | gate 全绿；GUI 队列页数据面见 §5 边界 |
| R17-API-7 | `/search` 的 `has_more=True` 是死胡同（无 `offset`），第 21 条命中永久拿不到；三条查询两条无 `ORDER BY` | `doc2md/storage/repo.py` + `api.py` | 检索游标 + 稳定排序（`limit=0` 口径同 D9） | gate 全绿 |
| R17-API-8 | `/jobs` 补了 `total` 却没补游标，第 51 条作业不可见；`created_at DESC` 无决胜位；信封不回显 `offset`/`limit` | `doc2md/api.py` + `repo.py` | 作业游标 + 决胜位 + 信封回显 | gate 全绿 |
| R17-SVC-1 | `events.emit` 的日志行 `{code, **payload}` 让 payload 的业务码**覆盖**事件名：库里 `job_rejected`、jsonl 里 `E4091` | `doc2md/events.py` | 保留键恒胜（`{**payload, "code": …}`） | gate 全绿 |
| R17-SVC-2 | `run_work` 启动段在 `try` 之外——启动即失败时 `Store` 句柄泄漏、无 `WORK_STOP` | `doc2md/worker.py` | 启动失败也关库并落 `WORK_STOP`（2 项） | gate 全绿 |
| R17-SQL-1 | `status IN {ACTIVE}` 把 Python 元组 repr 直拼 SQL；状态 GROUP BY 在 api/worker/cron 复制三份 | `storage/repo.py` `ACTIVE_SQL` + `task_status_counts` | `ACTIVE_SQL` 占位符形态 + `submit` 附着仍检在途作业 | gate 全绿 |
| R17-PIPE-1 | 外链分支早于 `_escape_text`：`[<script>](…)` 的链接文字原文出门 | `pipeline/s4_render.py` | 外链文字转义 + 换行压平 | 产物字节变化 → `PIPELINE_VERSION` 升位 |
| R17-PIPE-2 | **渲染红线自 CH-417 落地起无生产调用方**——`assert_render_contract` 只被测试引用，红线只能证明「测试里的输出干净」 | `pipeline/s4_render.py` `render()` | 逐块 `_contract_violation()`，违规即 E5031 + `detail.reason=render_contract`；代码块字面量豁免；`preserve_strike` 灰度档经 `allow_strike` 放行 `~~` | gate 全绿（3 项）。判定面必须**按块**：整篇扫描会误判代码块里的合法 `~~` / `<script>` / `](#` |
| R17-PIPE-3 | `sniff()` ZIP 分支裸 `except Exception: pass`，把「截断的 .docx」与「xlsx 改名成 .docx」归成同一条 `unsupported magic`（且 magic 字段还打印出 PK 头自称「不支持」） | `pipeline/s1_parse.py` | 三分诊断 `corrupt_zip` / `zip_unreadable` / `not_a_docx` + 真 DOCX 仍识别 | 探针 P01 原文留档。`zip_unreadable` 分支本机黑盒不可复现（`chmod 000` 先被开头 8 字节读取拦下，走另一条消息），**只用白盒测试钉住**——见 §5 |
| R17-PIPE-4 | 片级内部锚 `extra_meta["shard"]` 被 `IR.meta()` 平铺进交付面：多片文档向用户交付一个**假的页范围** | `ir/model.py` | 交付 meta 不含 `shard` 内部锚 | 产物字节变化 → 升位 |
| R17-PIPE-5 | `_strip_page_furniture` 重建 `RawPage` 时丢 `rotation` / `rtl_ratio`（与 CH-493 P-2 同形态） | `pipeline/s3_structure.py` | 去页边家具后两字段仍携带 | gate 全绿 |
| R17-PIPE-6 | DOCX 行字号取「最后一个带显式字号的 run」——同一 pair 换 run 顺序实测得 10.0 / 22.0，**标题级别由 run 排列决定** | `pipeline/s1_parse.py` | 行字号改按字符数加权众数：与 run 顺序无关 + 空 run 不参与 | 探针 P11 复现 → 产物字节变化 → 升位 |
| R17-PKG-1 | `python -m doc2md` 报 `No module named doc2md.__main__` | `doc2md/__main__.py`（新增） | `python -m doc2md` 可运行 | gate 全绿 |

**版本口径**：R17-PIPE-1 / -4 / R17-PIPE-6 三项改变产物字节，`PIPELINE_VERSION` 按契约级修订
**2.17.0 → 2.18.0**（`pyproject.toml` 同步）；`SCHEMA_VERSION` **14**、`DB_VERSION` **10** 不动。

---

## 2. 本轮新增的独立真值腿（难度语料 D01–D08）

前 21 篇语料的对照面有两个分母：`expected/`（字节回归）与 `ideal/`（人工标注的理想产物）。
本轮补上第三个分母——**构造时即声明意图、不经管线**的难度样本（`tests/corpus/hard/`，
由 `tests/qa/gen_cases.py` 生成）。这不违反 C-31 自啮合禁令：真值来自「我造了一张 3 列 × 41 行的表」，
不是「产物现在长这样所以期望长这样」。

| 编号 | 构造意图（声明真值） | 实测 | 判定 |
| --- | --- | --- | --- |
| D01 无线框表格 ★5 | 1 张 3 列 × 4 行，表头 `项目/本期金额/上期金额`，三行数据逐格相等 | 一致 | **达成** |
| D02 跨页表格 ★4 | ≥1 张 3 列 × ≥41 行，表头 `序号/科目/金额`，行数不得丢 | 裂 2 张：2 列 × 37 行 + 3 列 × 5 行；`金额` 列整体脱落成正文 `金额10011002…1036`；`ir.warnings` **0 条** | **失败（契约缺陷）→ B-43** |
| D04 断词连字符 ★2 | 含 `configuration` / `parameter` / `preserved` 完整词 | 三词命中 | **达成** |
| D05 点线引导符 ★3 | 含 `第一章 总则` / `第二章 职责` / `第三章 附则` | `\| 第一章总则 \| .................... \| 1 \|`——引导符进单元格、整页目录判成表、run 间空格丢失；`ir.warnings` **0 条** | **失败（契约缺陷）→ B-44** |
| D06 数值形态 ★3 | 1 张 3 列 × 5 行，`(1,234)` 保持括号形态不得转成 -1234 | 逐格一致，千分位/百分号/￥ 原样 | **达成** |
| D07 加密 PDF ★1 | `behavior = reject:E4151` | `E4151`，`detail.password_required = True`（可与结构损坏区分） | **达成** |
| D08 页眉逐页变体 ★2 | 8 个 `第 N 节 正文章节标题` 保留（声明 H1）；`奇数页`/`偶数页` 不出现 | 剔除面全对、8 个标题全保留；**层级 H2**（产品按 `s3_structure.py:1155` 的 CJK 序号映射 `节→2`） | **缺口（真值侧冲突）→ B-45** |

合计 **4 通过 / 2 失败 / 1 缺口（7 例）**，`tests/corpus/run_hard.py` 打印 `SUMMARY 4/7 难度样本全绿（信息层，未入门禁）` 并退 1（取证 `/tmp/qa-r4/run_hard_final.log`）。
编号序列缺 **D03**——`gen_cases.py` 从未生成过它，这是覆盖面的真实空洞，不临时补样本凑数。

**分档改判记录（本文首稿 → 收口）**：D02 / D05 首稿记「缺口」，收口回读 `W1045` 语义后改记**失败**。
判据只有一条——「行为是否与既有承诺矛盾」；`W1045` 的存在即承诺「表格降维必须入账」，而两处都是
**内容已损失且 `warnings` 0 条**，属违背承诺。「修复需要人工逐页对照」只约束修复路径，不构成降级理由。
本轮发布判定因此从「工程侧不阻断」改为**「阻断发布」**（TC-HD-02 是 P0 失败），详见对照表 2b 节与
《优化方案》第 8 节。

**D02 / D05 的严重度定性**：两者都是**静默**结构损伤（`ir.warnings` 零告警）。D02 丢掉整整一列
数值并把它们拼进正文——对数字类文档是最高危形态；按「宁可出红、不可无声」的项目纪律，缺口的
一半在识别层（S3 没有把「列数突然变化 / 表裂成两张」升为告警），另一半在装配层（S1 行装配依赖
pdfminer 文本流顺序而非 y 坐标）。这两半都要动产物字节，且「正确列归属」只能人工逐页对照真 PDF
定夺，故按用户口径**仅登记不实施**。

---

## 3. 收口复跑取证（本机 macOS arm64 / Python 3.15，`.venv`）

| 命令 | 结果 | 与轮中口径 |
| --- | --- | --- |
| `pytest -m gate -q --junitxml` | **580 tests / 0 failures / 0 errors / 0 skipped**，墙钟 44.627s，per-case 求和 44.144s，45 个 classname | 同轮另三次 580 项采样 40.636s / 40.658s / 40.802s；**全部文档改完后的终轮复跑 `/tmp/qa-r4/gate_final.xml` = 580 / 0 / 0 / 0 skipped，墙钟 38.155s**（同机第四点，仍低于 60s 预算）；**末两轮 `/tmp/qa-r4/gate_v4.xml` = 580 / 0 / 0 / 0 skipped（37.716s）、`/tmp/qa-r4/gate_v5.xml` = 580 / 0 / 0 / 0 skipped（38.137s）**（O-h 与 §12.10 口径同步落地后复跑）。±6s 散布归因于同机并发与 `pytest-randomly` 的用例次序——**项数与通过态才是硬口径** |
| `pytest tests/ -q --junitxml` | **1390 tests / 1382 passed / 8 skipped / 0 failed**，墙钟 95.600s，per-case 求和 94.830s，81 个 classname 求和 = 1390 与 `tests=` 一致 | **文档改完后的终轮复跑 `/tmp/qa-r4/full_final.xml` = 1390 / 0 failures / 0 errors / 8 skipped，墙钟 85.489s**、**末两轮 `/tmp/qa-r4/full_v4.xml` = 1390 / 0 / 0 / 8 skipped（86.011s）、`/tmp/qa-r4/full_v5.xml` = 1390 / 0 / 0 / 8 skipped（86.597s）**（项数与跳过构成与 95.600s 那次逐条相同）；轮中 1349 项口径（91.334s / 100.253s）是追加 `tests/qa/` 评估 harness 之前；+41 项全部归因（34 + 7），无未归因余额 |
| `tests/corpus/run_corpus.py` | **22 passed / 0 failed**，逐字节一致，exit 0 | 与轮中同 |
| `tests/corpus/run_hard.py` | **4/7**，exit 1 | 本轮新增腿 |
| `coverage run -m pytest tests/` + `coverage report` | `doc2md/` 口径 **45 个模块 / 7797 stmts / 740 miss，`coverage report` 打出 91%**（精确 90.51%）；`doc2md/security/perms.py` **75/45 = 40%** | 轮中 7800/743 打出的是 **90%**（精确 90.47%）；差 3 条语句属 `__main__.py` 落地后的同轮漂移，**档位没变，只是 90.47%→90.51% 跨过 coverage 的 .5 取整边界导致显示从 90% 跳到 91%**——本册据此把「打出的整数」与「精确值」分开写，并把这条记为 O-h（把上一档的显示整数当成本档的事实继承，而未回读日志） |
| `ruff check doc2md tests config ops` | `All checks passed!` exit 0 | 收口阶段本轮自身的 `tests/corpus/run_hard.py` 曾报 2 条（I001 + F541），已改正后复跑全绿 |
| `python -m doc2md doctor` | exit 0，全键 `ok` | 与轮中同 |
| 三套探针 | API 49/49、管线 32/32、扩展面 30/30 | 与轮中同 |
| `tests/test_ch491_doc_sync.py` / `test_ch477_version_sync.py` / `test_ch498_gate_budget.py` | 11 项全绿（文档对账 / 版本四处对账 / 慢项清单冻结） | 本轮大量改文档口径后**专门复跑**，防止「改文档改坏红线」 |
| 编辑器内联脚本语法 | 由 `test_ch496_local.py` 的 G4' 用例在 gate 内自校验（提取内联 JS 过 `node --check` 等价断言） | 无需外部浏览器 |

**并发干扰声明（诚实边界）**：本轮收口期间，同一工作区有**另一个 agent 会话**
（WorkBuddy/CodeBuddy，session `8ca8eb5a…`）在并行编辑 `tests/qa/`、`tests/corpus/hard/`、
`work/qa/` 与 `docs/qa/*`，并至少在同一台机器上跑过一次全量套件（01:02:35 起）。
这解释了墙钟的散布，也解释了 `tests/qa/test_hard_corpus.py` 为何在本文口径中从「已删除」
变成「以 xfail/skip 形态存在」。本文所有数字取**最后一次的同机复跑**（`/tmp/qa-r4/*.xml`），
且对账以 junit 的 `tests=` 与 classname 求和互相校验，不采信任何一次未经复核的采样。

---

## 4. 收口阶段新抓出的工具与文档缺陷（本轮已修）

审查工具的缺陷比产品缺陷更隐蔽，因为它会让所有后续轮次都「看起来是绿的」。本轮抓到 6 处：

1. **`gen_cases.py` 的 D07 资产自我删除**（已修）。暂存名与终名同为 `hard_07_encrypted.pdf`，
   pypdf 的写出目标就是 `base` 自身，随后 `base.unlink()` 把刚生成的加密样本删掉。
   掩盖机制是自动骨架的 `pytest.skip("未生成")`——资产缺失被显示成「正常跳过」。
   改为暂存名 `hard_07_encrypted.raw.pdf` + `unlink(missing_ok=True)`，重烤得 2335 字节样本并跑通 D07。
   **这是 O-a「PASS 但什么也没测」病灶的第二个活例：骨架里的 skip 会吃掉缺失。**
2. **自动骨架的覆盖面错觉**（已登记 B-46，未擅自删除）。`test_hard_corpus.py` 只核对
   `contains` / `not_contains`，不看 `tables` / `headings`，因此 D02 的裂表在骨架里是一条
   TODO-skip 而非缺陷。若在无人核对骨架断言面的把它当门禁，会得出「难度语料无结构问题」的假结论。
   本文 §2 的结构面结论一律由 `run_hard.py` 出具。
3. **悬空编号 B-42**（已补登）。`docs/qa/round3/comparison.md` 与 `optimization-plan.md` 引用
   `B-42`，而 dev-plan 12.7 的 B 表实际只到 `B-41`——上一轮引用了尚未登记的编号。
   本轮补 B-42…B-46 五项使两套编号对账一致。
4. **附录 G 的旧口径串**（已改正）。该段仍写「`tests/corpus/` 等子目录另有 7 个不参与收集的脚本，
   含子目录合计 87 个；全口径 25750 行」——实测是 **15 个 / 4261 行、合计 95 个 / 28617 行**。
   同段的 `tests/` 顶层行数写 24229，实测 **24356**（`test_ch500_round17.py` 从 30 项补到 38 项后
   未同步；历史口径串里已经是 24356，属同一处「改了活锚点没改另一处」的病史形态）。
5. **`tests/qa/*` 与 `tests/corpus/*` 全子目录从未进 G.6 清单**（已补 13 行）。度量层、评测器、
   生成器、难度资产、以及 4 个语料侧脚本此前在方案里没有登记位，等于「资产存在但规格不知道」。
6. **本轮自己的 `run_hard.py` 未过 ruff**（已修）。I001（import 块对齐注释）+ F541
   （`f"behavior"` 无占位符）。教训：给「校验别人」的工具补红线时，它自己也在这条红线范围内。

---

## 5. 无法自证的部分（明确边界，不得读成「已通过」）

| 项 | 边界性质 | 出口 |
| --- | --- | --- |
| R17-PIPE-3 的 `zip_unreadable` 分支 | 本机黑盒不可复现（`chmod 000` 先被读取拦下），**只有白盒单测钉住** | 真机权限面 + 外部容器取证 |
| D02 / D05 的修复 | 需人工逐页对照真实 PDF 才能确定列归属与目录识别阈值；且必动产物字节 | **B-43 / B-44** |
| D08 的层级 | 生成器声明与产品映射冲突，两侧各自自洽，该映射**当前无红线** | **B-45**（裁定后补映射红线） |
| 难度语料入门禁 | 前置 B-43 / B-44；入 gate 前不得保留任何 skip/xfail | **B-46** |
| 页区间并行 2.79× | 三档行数逐字节一致是**本机合成样本**的等价面，未在真实文档上证明；且需产物冻结决策 | **B-38** |
| GUI 跨浏览器 / 跨平台 | 本机 Chromium 已走查两轮，Chrome / Safari / Windows 腿缺 | **B-11**（人工） |
| PDF 标题真值的适用范围 | 语料内 21 份标注可证 `heading_pr`，**语料之外的真实 PDF** 仍无人工标注 | **B-35** |
| 580 项新红线的远端腿 | CI 三档 merge-gate、Windows 门禁层、30 分钟档长跑均需 CI 环境 | **B-12 / B-16 / B-27-remote** |
| 真实 SMB / 网络卷 | 本机无该文件系统 | **B-40** |
| legacy `.doc` 第二转换器 | 本机只有 LibreOffice | **B-41** |
| `doc2md/security/perms.py` 覆盖 40% | 未覆盖部分是 Windows ACL / `icacls` 分支，本机不可执行 | **B-14 / B-16** |

反向验证（把修复撤掉看红线是否转红）本轮只对**判定层**改动做了：R17-PIPE-2 的渲染红线撤掉
调用点后 `R17-PIPE-2` 用例转红；R17-API-1 的 Content-Type 判据退回 `request.data` 后对应用例转红。
其余 16 项依赖「修复前探针输出」作对照（§1 的症状列即修复前实测），未逐个反向拆除——
这是本轮取证的一个已知省力处，登记不隐藏。

---

## 6. 下一条

`CH-500` 已闭合本机可自证面。下一条自 **CH-501** 起，建议顺序：
**B-43（D02 静默裂表）→ B-44（D05 目录）→ B-45（D08 层级裁定，顺带补 CJK 映射红线）→
B-46（结构断言入 gate）→ B-34（指标出口，本机可自证）→ B-42（探活契约，需人工裁定）**。
B-34 排在结构性缺陷之后、外部腿之前，因为它只加报告出口、不动产物字节。
