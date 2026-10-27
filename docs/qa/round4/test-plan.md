# Doc2md 转换管线功能测试文档（第四轮，含执行记录）

- 文档编号：QA-D2M-2026-10-26-R4
- 测试轮次：第四轮全面功能测试（黑盒实测为主 + 源码/套件定位佐证），同时承担第三轮 2 项失败与 4 项缺口的复验
- 被测版本：`PIPELINE_VERSION = 2.20.0` / `SCHEMA_VERSION = 14` / `DB_VERSION = 10` / `PLAN_VERSION = 3.38`
- 执行日期：2026-10-25 起，收口取证跨至 2026-10-26
- 执行环境：macOS arm64（darwin 27.0.0）、Python 3.15.0（仓库 `.venv`）、10 逻辑 CPU、node v24.18.0、LibreOffice 可用（`/Applications/LibreOffice.app/Contents/MacOS/soffice`）；转换全程离线，LLM server 探针只连 `127.0.0.1:8080`（未启动，用于验证失败/不回退路径）
- 证据根目录：`/tmp/qa-r5/`（四份探针脚本 `r4_api.py` / `r4_cli.py` / `r4_struct.py` / `r4_gui.py`、四份逐条结果 JSON、夹具、产物、套件/覆盖率 XML 全部保留，可逐条复核；见 §5 证据索引）
- 上一轮基线：`docs/qa/round3/test-plan.md`（QA-D2M-2026-10-24-R3，102 例 / 96 通过 / 4 缺口 / 2 失败）；本轮不覆盖上一轮文档
- 本轮修复来源：第 19 轮系统性审查 **CH-504**（架构 / GUI·API / 代码·文档三层），新增红线 `tests/test_ch504_service_faces.py` 19 项 + `tests/test_ch504_numbering.py` 4 项
- 配套文档：《对照表》（`comparison.md`）、《偏差归纳与根因》（`deviation-analysis.md`）、《优化方案》（`optimization-plan.md`）

---

## 1. 测试范围与模块划分

被测链路：`输入识别 → S1 解析 → S1.5 门禁 → S2 分片/缝合 → S3 结构映射 → S4 渲染 → LLM 修复 → 打包 → 存储/检索 → CLI/API/GUI/运维出口`。

| 模块 ID | 模块 | 对应源码 | 用例数 | 通过 | 缺口 | 失败 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| M1 | 输入识别与前置校验 | `pipeline/s1_parse.py::sniff`、`security/names.py::sanitize_name`、`security/dirguard.py` | 7 | 7 | 0 | 0 |
| M2 | S1 解析（PDF / DOCX / legacy .doc） | `pipeline/s1_parse.py`、`pipeline/legacy_doc.py` | 6 | 6 | 0 | 0 |
| M3 | S1.5 文本层门禁 | `pipeline/s1_5_gate.py` | 3 | 3 | 0 | 0 |
| M4 | S2 分片、缝合与确定性 | `pipeline/sharding.py`、`pipeline/convert.py` | 6 | 6 | 0 | 0 |
| M5 | S3 结构映射（七类块） | `pipeline/s3_structure.py`、`ir/__init__.py::BLOCK_TYPES` | 14 | 14 | 0 | 0 |
| M6 | S4 Markdown 渲染与安全红线 | `pipeline/s4_render.py` | 10 | 10 | 0 | 0 |
| M7 | LLM 修复、回退与 op 契约 | `llm.py`、`pipeline/convert.py::repair_ops` | 3 | 3 | 0 | 0 |
| M8 | 打包与产物形态 | `pipeline/convert.py`（打包段）、`artifacts.py` | 4 | 4 | 0 | 0 |
| M9 | 存储、检索与库操作 | `storage/`、`cli.py`（search/docs）、`ops/` | 4 | 4 | 0 | 0 |
| M10 | API 服务、权限、分页与编辑锁 | `api.py`、`auth.py`、`edits.py`、`events.py` | 45 | 45 | 0 | 0 |
| M11 | CLI 批量、退出码与格式出口 | `cli.py`、`__main__.py` | 5 | 5 | 0 | 0 |
| M12 | 运维、性能与可观测性 | `ops/`、`worker.py`、`cron.py`、`scheduler.py` | 4 | 4 | 0 | 0 |
| MG | GUI 审阅 / 编辑静态页 | `static/editor.html`、`api.py::index` | 9 | 8 | 1 | 0 |
| MR | 回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性） | `tests/`、`docs/`、`README.md`、`pyproject.toml` | 7 | 7 | 0 | 0 |
| HD | 难度语料（独立真值腿） | `tests/corpus/hard/`、`tests/corpus/run_hard.py`、`tests/corpus/hard/expectations.json` | 10 | 6 | 2 | 2 |
| **核心合计（M1–MG + MR）** |  |  | **127** | **126** | **1** | **0** |
| **本轮合计（含 HD 腿）** |  |  | **137** | **132** | **3** | **2** |

与第三轮的模块面差异：① 新增 **MG**（GUI 静态页）独立成模块——第 19 轮 4 项 GUI 修复（R19-GUI-1..4）此前只以源码静态断言存在，本轮把「服务出口 / 脚本语法 / 转义纪律 / 离线承诺 / 资源可达」五项做成可执行探针，只把**交互时序**留作人工走查缺口；② **M2** 的 legacy `.doc` 从第三轮的缺口改为可执行——本机 LibreOffice 即可生成真实 OLE 夹具（`tests/test_legacy_doc.py::test_libreoffice_doc_roundtrip` 早已用同一手法），第三轮把它记作「需外部样本」是误判，本轮更正并端到端复证；③ **M4/M5/M6** 由 2/4/2 补到 6/14/10，七类块（`BLOCK_TYPES`）逐类取证。

## 2. 统计口径与判定标准

### 2.1 用例状态

| 状态 | 判定 |
| --- | --- |
| **通过** | 实际结果与期望结果在**状态、错误码、退出码、产物结构、关键文本/字节**上全部一致；性能用例同时满足时延与内存阈值 |
| **失败** | 任一可观测断言不一致，且该行为与既有契约/文档承诺相矛盾即记失败；影响等级为「低」也不改记为通过 |
| **缺口** | 实际行为**与当前文档口径一致**，但存在用户可感知的能力缺失或契约决策未裁定；或因需本机以外环境/人工而无法判定。缺口计入分母、不计入分子，即同样拉低通过率 |
| **未执行** | 因平台/环境无法执行；不计入通过率，也不以「跳过」充抵通过（本轮 2 项，见 §4.2） |

**分档纪律（本轮两次反向使用）**：判定「失败 vs 缺口」只看一条——**行为是否与既有承诺矛盾**，不看修复成本，也不看「是否需要人工/外部样本才能修」。

- 反向使用之一：TC-M2-03（legacy `.doc`）第三轮记缺口，理由是「需外部 `.doc` 样本」。本轮复核发现本机 LibreOffice 就能生成真实 OLE 夹具，且仓库里早有同手法的测试——**「需要夹具」不是缺口理由，「本机造不出夹具」才是**。改判为可执行，并在执行中抓出真缺陷 R19-PIPE-3（见 §3.2 TC-M2-03 / TC-M5-11）。
- 反向使用之二：TC-M6-04（HTML/JS 注入）初版断言「产物中不得出现 `javascript:` 字样」，实测为失败。复核后确认**断言本身错了**：产物是 `\[click\](javascript:alert(3))`——`]` 已被转义，Markdown 不会生成锚，渲染后既不可执行也不可点击；把这串字抹掉才是内容损失。安全红线的正确口径是「渲染后不可执行/不可点击」，据此改判通过，并把 `report.html` 的 `href="javascript:` 一并纳入断言。

本轮 137 条用例（核心 127 + 难度腿 10）全部执行完毕，无「未执行」项进入用例表；「未执行」另记 2 项于 §4.2（不属用例表，不参与任何通过率）。

### 2.2 优先级

| 优先级 | 定义 |
| --- | --- |
| **P0** | 核心转换正确性、数据完整性、权限边界、安全边界、失败必须可见；失败即阻断发布 |
| **P1** | 边界/异常输入、API 契约与分页口径、存储检索、批处理、性能基线；失败需在发布前修复或书面豁免 |
| **P2** | 可观测性、文档一致性、结果行展示、GUI 静态页可用等非阻断项；可排入后续迭代，但必须登记 |

### 2.3 通过率与覆盖率公式

- **用例通过率**：通过数 ÷ 已执行数 × 100%。**分母先声明再算**（O-c 纪律），本轮三口径并列：
  - 口径 A（全量，含缺口）= 132 ÷ 137 = **96.35%**；
  - 口径 B（可执行，分母剔除 3 项缺口）= 132 ÷ 134 = **98.51%**；
  - 口径 C（核心面 M1–MG + MR，真值来自契约与文档口径）= 126 ÷ 127 = **99.21%**；
  - 难度腿 HD（真值来自生成器声明的构造意图）= 6 ÷ 10 = **60.00%**。**发布门槛一律按口径 A**，其余同列以便定位差异来源。
- **优先级通过率**（合并口径，分布 P0/P1/P2 = 73/55/9）：P0 = 71 ÷ 73 = **97.26%**；P1 = 52 ÷ 55 = **94.55%**；P2 = 9 ÷ 9 = **100.00%**。
- **模块覆盖率** = 有用例的模块数 ÷ 计划模块数 = **15 ÷ 15 = 100%**（13 个链路/出口模块 + MR 回归腿 + HD 独立真值腿；模块均有用例不等于需求全覆盖，需求面另见 `tests/qa/trace.py` 口径）。
- **错误码覆盖率** = 已实测触发的契约错误码数 ÷ 契约错误码总数；契约码以 `doc2md/errors.py::CODE_INDEX` 的 14 项为准：**14 ÷ 14 = 100%**（E4001/E4003/E4011/E4031/E4041/E4091/E4092/E4131/E4133/E4151/E4221/E4251/E5031/E5041）。E5000 为兜底基类不计入契约码，500 面另记 TC-M10-12。
- **告警覆盖率** = 已实测触发的告警码数 ÷ 告警码总数 = **4 ÷ 4 = 100%**（W1043/W1044/W1045/W1046）。W1047–W1051 为附录 D 预留位，尚无实现，不进分母。
- **语句覆盖率** = `coverage.py` 全量套件统计（`doc2md/` 口径）。**引用时须同时给出打出值与精确值**（O-h 纪律）：本轮 `TOTAL 7843 stmts / 737 miss` → 打出 **91%**、精确 **90.60%**。
- **语料覆盖率** = 语料 harness 通过数 ÷ 语料总数 = **22 ÷ 22 = 100%**（逐字节比对面）；难度语料 harness = **6 ÷ 10 = 60%**（独立真值面，信息层，不入门禁）。
- **探针用例规模**：API 面 45 项、CLI/管线/运维面 44 项、结构/分片/渲染面 22 项、GUI 面 9 项，合计 **120 项**；加 MR 腿 7 项与 HD 腿 10 项，共 **137 条**。四个探针脚本各自重建独立 `--root`，互不串库；子进程退出码一律不经管道读取（`cmd | head` 之后 `$?` 是 `head` 的，会吞掉真实 rc——本轮所有 rc 断言均按此纪律取证）。

**测试面分布**（按 §2.1 的度量类型，探针面实测计数）：正常流 52、边界 23、异常输入 33、权限边界 8、回归 9、契约一致性 1、受限缺口 1。四类必覆盖面（正常/边界/异常/回归）均非空。

### 2.4 发布门槛（测试前设定，与第三轮同表）

| 指标 | 门槛 | 核心面（测试前口径） | 含难度腿（收口口径） | 结论 |
| --- | --- | --- | --- | --- |
| P0 用例通过率 | 100% | 70 ÷ 70 = 100% | 71 ÷ 73 = **97.26%** | 核心达标；**合并未达标**（TC-HD-02 / TC-HD-05） |
| P1 用例通过率 | ≥ 95% | 47 ÷ 48 = 97.92% | 52 ÷ 55 = **94.55%** | **两口径均未达标**（缺口 TC-MG-09 / TC-HD-08 / TC-HD-10 计入分母） |
| P2 用例通过率 | ≥ 90% | 9 ÷ 9 = 100% | 9 ÷ 9 = 100% | 达标 |
| 总体用例通过率 | ≥ 95% | 126 ÷ 127 = **99.21%** | 132 ÷ 137 = **96.35%** | 核心达标；**合并未达标** |
| 契约缺陷数 | 0 | **1**（R19-PIPE-3 编号伪造，本轮已修） | **2**（TC-HD-02 静默掉列、TC-HD-05 引导符吞行） | **未达标** |
| 全量自动化套件 | 0 failed | — | 1516 收集 = 1500 passed / 0 failed / 10 skipped / 6 xfailed | 达标 |
| 门禁套件 | 0 failed / 0 skipped | — | 683 passed / 0 skipped | 达标 |
| 语料 harness | 22/22 | 22/22 | 22/22 | 达标 |
| **难度语料 harness** | 逐条如实报告、不充抵 | — | **6/10**（`run_hard.py` 退出码 1） | **未达标（2 失败 + 2 缺口）** |
| 语句覆盖率 | ≥ 90% | — | 打出 91% / 精确 90.60%（7843 stmts / 737 miss） | 达标（贴线，距 92% 目标 1.4 个百分点） |
| 错误码 / 告警覆盖率 | ≥ 90% | 100% / 100% | 100% / 100% | 达标 |

结论：

1. **核心面（M1–MG + MR，127 例）零失败**：119 通过 / 1 缺口（TC-MG-09 浏览器人工走查）/ 0 失败，口径 C = 99.21%。第三轮的 13 项 API 契约偏差与 CH-500/CH-503 的修复全部复验通过。
2. **本轮在执行中抓出并修掉一项真缺陷（R19-PIPE-3）**：`numFmt="none"` / 空 `lvlText` 的编号定义被兜底成 `"%1."`，导致 LibreOffice 写出的 DOCX（含 `.doc` → `.docx` 中间件）每个标题被凭空加上 `1. ` 前缀——文档里不存在的字符进入交付正文，且 `warnings` 为空（静默失效）。已修并做三次变异证明（见 §3.2 TC-M5-11、`docs/review-round19-CH-504.md`）。**这是本轮唯一新增的契约缺陷，也是「legacy .doc 改判为可执行」直接换来的收益。**
3. **难度腿仍是发布阻断项，且与第三轮同两条**：TC-HD-02（跨页表整列脱落）、TC-HD-05（引导符吞行）都是内容损失 + 零告警，违背 W1045 降维账本与文本保真两条既有承诺。按 §2.2「P0 失败即阻断发布」，**B-43 / B-44 未闭合前不得发布**，或由人工逐页对照真实 PDF 复核后出具书面豁免——两者都需人工介入，属受限项，本轮只登记不实施。
4. 缺口 3 项（TC-MG-09 / TC-HD-08 / TC-HD-10）分别对应 B-11（浏览器人工走查，既有登记项）/ B-45（层级口径裁定）/ B-47（CID 产物口径裁定），全部需要人工判定或外部样本，见 §4.1。

## 3. 用例全集与逐条执行结果

说明：每条用例的「实际结果」均来自本轮实测原文（探针 JSON 的 `actual` 字段，未做美化）；「判定」由探针内的断言表达式算出，不手填。命令均在仓库根目录执行，`doc2md` 指 `.venv/bin/doc2md`。四个探针脚本为 `r4_cli.py` / `r4_struct.py` / `r4_api.py` / `r4_gui.py`，各自重建独立 `--root`，互不串库。

### 3.1 M1 输入识别与前置校验

#### TC-M1-01 PDF 嗅探识别为 pdf 且转换成功

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：PDF 嗅探识别为 pdf 且转换成功
- **输入与前置条件**：small pdf
- **执行步骤**：convert --json
- **期望结果**：rc=0 status=ok 有 doc_id
- **实际结果**：rc=0 format=pdf status=ok doc_id=y
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-01` 条）

#### TC-M1-02 DOCX 嗅探识别且转换成功

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：DOCX 嗅探识别且转换成功
- **输入与前置条件**：small docx
- **执行步骤**：convert --json
- **期望结果**：rc=0 status=ok
- **实际结果**：rc=0 status=ok doc_id=y
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-02` 条）

#### TC-M1-03 不支持类型 .txt → 退出码 4

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：不支持类型 .txt → 退出码 4
- **输入与前置条件**：一个 .txt
- **执行步骤**：convert --json
- **期望结果**：rc=4（EXIT_BIZ）
- **实际结果**：rc=4 code=E4151 status=error
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-03` 条）

#### TC-M1-04 路径不存在 → 退出码 2（EXIT_USAGE）

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：路径不存在 → 退出码 2（EXIT_USAGE）
- **输入与前置条件**：不存在的 path
- **执行步骤**：convert --json
- **期望结果**：rc=2
- **实际结果**：rc=2 stderr=not found: /tmp/qa-r5/tmp/cli-l_yjrblp/nope.pdf
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-04` 条）

#### TC-M1-05 魔术码不符（假 .pdf）→ E4151 / 退出码 4

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：魔术码不符（假 .pdf）→ E4151 / 退出码 4
- **输入与前置条件**：文本内容写成 .pdf
- **执行步骤**：convert --json
- **期望结果**：rc=4 code=E4151
- **实际结果**：rc=4 code=E4151
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-05` 条）

#### TC-M1-06 文件名净化：穿越/保留名全部按期望改写

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：文件名净化：穿越/保留名全部按期望改写
- **输入与前置条件**：malicious_names.json
- **执行步骤**：sanitize_name 逐条比对
- **期望结果**：全部命中 expected
- **实际结果**：total=15 mismatches=[]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-06` 条）

#### TC-M1-07 目录扫描：symlink 环不挂死、非输入文件被忽略

- **所属模块**：M1（输入识别与前置校验）
- **测试目标**：目录扫描：symlink 环不挂死、非输入文件被忽略
- **输入与前置条件**：dirs/symlink_loop
- **执行步骤**：convert 目录 --json
- **期望结果**：rc=0 不挂死
- **实际结果**：rc=0 lines=1 empty=True stderr={"status": "empty", "path": "/Volumes/fx/Object/Doc2md/tests
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M1-07` 条）

### 3.2 M2 S1 解析（PDF / DOCX / legacy .doc）

#### TC-M2-01 CJK+英文混排 PDF 解析出正文与 CJK 字符

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：CJK+英文混排 PDF 解析出正文与 CJK 字符
- **输入与前置条件**：pdf_05_cjk_en_3p
- **执行步骤**：convert --format both
- **期望结果**：rc=0 blocks>0 含 CJK
- **实际结果**：rc=0 blocks=30 chars=852 cjk=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-01` 条）

#### TC-M2-02 DOCX 修订/域部件遍历解析成功

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：DOCX 修订/域部件遍历解析成功
- **输入与前置条件**：docx_05_revisions_fields
- **执行步骤**：convert --json
- **期望结果**：rc=0 blocks>0
- **实际结果**：rc=0 blocks=9 chars=76
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-02` 条）

#### TC-M2-03 legacy .doc 全链路转换且正文与源 .docx 逐字节等价

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：legacy .doc 全链路转换且正文与源 .docx 逐字节等价
- **输入与前置条件**：本机 LibreOffice 由 docx_01_simple.docx 生成 OLE .doc
- **执行步骤**：convert .doc 与 convert .docx 各一次，剥 front-matter 后比对正文
- **期望结果**：rc=0 status=ok 块数与字数相同且正文一致（CH-504 编号修复后成立）
- **实际结果**：ole_magic=True doc_rc=0 doc_status=ok doc_blocks=9 docx_blocks=9 doc_chars=85 docx_chars=85 doc_id=364db64e8c5baf27 body_identical=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-03` 条）

#### TC-M2-03b legacy 后端不可用 → E4151/stage=backend_unavailable，退出码 4

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：legacy 后端不可用 → E4151/stage=backend_unavailable，退出码 4
- **输入与前置条件**：真实 OLE .doc + --config s1.legacy_doc_backend=none
- **执行步骤**：convert --config none.toml --json
- **期望结果**：rc=4 code=E4151 detail.stage=backend_unavailable
- **实际结果**：rc=4 code=E4151 stage=backend_unavailable hint=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-03b` 条）

#### TC-M2-03c 非 OLE 内容挂 .doc 后缀 → E4151 报 magic，不送外部后端

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：非 OLE 内容挂 .doc 后缀 → E4151 报 magic，不送外部后端
- **输入与前置条件**：文本内容写成 .doc
- **执行步骤**：convert --json
- **期望结果**：rc=4 code=E4151 detail.magic=前 8 字节十六进制
- **实际结果**：rc=4 code=E4151 magic=7468697320697320 stage=None
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-03c` 条）

#### TC-M2-04 链接/代码 PDF 解析出链接语法

- **所属模块**：M2（S1 解析（PDF / DOCX / legacy .doc））
- **测试目标**：链接/代码 PDF 解析出链接语法
- **输入与前置条件**：pdf_04_links_code_2p
- **执行步骤**：convert --format both
- **期望结果**：rc=0 产物含 ](
- **实际结果**：rc=0 has_link=True blocks=6
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M2-04` 条）

### 3.3 M3 S1.5 文本层门禁

#### TC-M3-01 扫描件（全文本层空）→ E4221 门禁拒收

- **所属模块**：M3（S1.5 文本层门禁）
- **测试目标**：扫描件（全文本层空）→ E4221 门禁拒收
- **输入与前置条件**：pdf_06_scanned_1p
- **执行步骤**：convert --json
- **期望结果**：status=rejected code=E4221
- **实际结果**：rc=3 status=rejected code=E4221 reasons=['all_pages_empty']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M3-01` 条）

#### TC-M3-02 尾部连续空页超阈 → E4221 且报连续空页理由

- **所属模块**：M3（S1.5 文本层门禁）
- **测试目标**：尾部连续空页超阈 → E4221 且报连续空页理由
- **输入与前置条件**：badtail_scan.pdf
- **执行步骤**：convert --json
- **期望结果**：rejected + consecutive_empty_pages
- **实际结果**：rc=3 status=rejected reasons=['consecutive_empty_pages=3 starting at page 15', 'empty_page_ratio=15.00% > 5%']
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M3-02` 条）

#### TC-M3-03 正常文本 PDF 不被门禁误拒

- **所属模块**：M3（S1.5 文本层门禁）
- **测试目标**：正常文本 PDF 不被门禁误拒
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：convert --json
- **期望结果**：status=ok
- **实际结果**：rc=0 status=ok
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M3-03` 条）

### 3.4 M4 S2 分片、缝合与确定性

#### TC-M4-01 同文件两次转换 content.md 逐字节相同（确定性）

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：同文件两次转换 content.md 逐字节相同（确定性）
- **输入与前置条件**：pdf_09_complex_long_8p
- **执行步骤**：两次独立 root 转换比对 sha256
- **期望结果**：字节级一致
- **实际结果**：rc=0/0 sha=5622bb324cd4448d/5622bb324cd4448d identical=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-01` 条）

#### TC-M4-02 single 与 two_phase 模式正文逐字节等价

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：single 与 two_phase 模式正文逐字节等价
- **输入与前置条件**：pdf_08_large_40p
- **执行步骤**：两模式转换比对
- **期望结果**：字节级一致
- **实际结果**：single_len=16528 two_phase_len=16528 identical=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-02` 条）

#### TC-M4-03 强制小分片 → sharded/shard_count 如实上报且留 W1044

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：强制小分片 → sharded/shard_count 如实上报且留 W1044
- **输入与前置条件**：pdf_08_large_40p + [s2] max_pages_per_shard=10
- **执行步骤**：convert 后读 front-matter
- **期望结果**：sharded=true、shard_count>1、warnings 含 W1044
- **实际结果**：sharded=True shard_count=4 pages=40 warnings=['W1044'] blocks=560
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-03` 条）

#### TC-M4-04 分片正文与整篇正文逐字节等价、块数一致

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：分片正文与整篇正文逐字节等价、块数一致
- **输入与前置条件**：pdf_08_large_40p，默认 300 页 vs 强制 10 页
- **执行步骤**：两次 convert 比对正文
- **期望结果**：正文逐字节相同且块数相同
- **实际结果**：whole_blocks=560 sharded_blocks=560 whole_chars=16083 sharded_chars=16083 identical=True shards=1/4
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-04` 条）

#### TC-M4-05 重叠缝合留账：shard_stats 如实计数，不静默吞重复

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：重叠缝合留账：shard_stats 如实计数，不静默吞重复
- **输入与前置条件**：pdf_08_large_40p + 10 页分片 + 4 页重叠
- **执行步骤**：convert 后读 shard_stats
- **期望结果**：shards>1 且 dropped_dupes 有计数出口
- **实际结果**：shard_stats={'shards': 4, 'stitched': 0, 'seams': 3, 'edges': 6, 'tables_across_shard': 0, 'dropped_dupes': 168, 'blocks_raw': 728, 'blocks_after_dedupe': 560, 'blocks_after': 560} dup_lines=39 nonblank=560
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-05` 条）

#### TC-M4-06 s2.mode=single/two_phase 小文档均不分片且口径一致

- **所属模块**：M4（S2 分片、缝合与确定性）
- **测试目标**：s2.mode=single/two_phase 小文档均不分片且口径一致
- **输入与前置条件**：pdf_05_cjk_en_3p，两种 s2.mode
- **执行步骤**：两次 convert 比对 front-matter
- **期望结果**：两者 sharded=false、shard_count=1
- **实际结果**：single=False/1 two_phase=False/1
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M4-06` 条）

### 3.5 M5 S3 结构映射（七类块）

#### TC-M5-01 表格块映射为 Markdown 管道表

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：表格块映射为 Markdown 管道表
- **输入与前置条件**：docx_03_tables
- **执行步骤**：convert --format both
- **期望结果**：产物含管道表
- **实际结果**：has_table=True len=602
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-01` 条）

#### TC-M5-02 列表块映射为 Markdown 列表项

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：列表块映射为 Markdown 列表项
- **输入与前置条件**：docx_02_lists
- **执行步骤**：convert --format both
- **期望结果**：产物含列表标记
- **实际结果**：has_list=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-02` 条）

#### TC-M5-03 代码块映射为围栏

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：代码块映射为围栏
- **输入与前置条件**：docx_07_code_quote
- **执行步骤**：convert --format both
- **期望结果**：产物含 ```
- **实际结果**：has_fence=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-03` 条）

#### TC-M5-04 标题块映射：30 节文档产出 ≥30 个标题行

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：标题块映射：30 节文档产出 ≥30 个标题行
- **输入与前置条件**：docx_08_large_30sections
- **执行步骤**：convert --format both
- **期望结果**：标题行 ≥30
- **实际结果**：heading_count=60
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-04` 条）

#### TC-M5-05 七类块（BLOCK_TYPES）在语料中全部出现并被映射

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：七类块（BLOCK_TYPES）在语料中全部出现并被映射
- **输入与前置条件**：tests/corpus/sources 全量 22 篇
- **执行步骤**：逐篇转换收集 ir.json 块类型
- **期望结果**：BLOCK_TYPES 七类无一缺失
- **实际结果**：覆盖 7/7：{'heading': 'docx_01_simple.docx', 'paragraph': 'docx_01_simple.docx', 'list_item': 'docx_02_lists.docx', 'table': 'docx_03_tables.docx', 'page_break': 'docx_06_textbox_image.docx', 'code': 'docx_07_code_quote.docx', 'quote': 'docx_07_code_quote.docx'} 缺=[]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-05` 条）

#### TC-M5-06 标题层级 ir.level 与 Markdown # 数一致且未被压平

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：标题层级 ir.level 与 Markdown # 数一致且未被压平
- **输入与前置条件**：docx_08_large_30sections
- **执行步骤**：convert 后比对 ir 层级分布与 # 计数
- **期望结果**：至少两级且两侧分布逐一相等
- **实际结果**：ir_levels={1: 30, 2: 30} md_levels={1: 30, 2: 30} pages=8
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-06` 条）

#### TC-M5-07 引用块映射为 `>` 且不退化成段落

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：引用块映射为 `>` 且不退化成段落
- **输入与前置条件**：docx_07_code_quote
- **执行步骤**：convert 后数 quote 块与 > 行
- **期望结果**：quote 块 ≥1 且 Markdown > 行 ≥1
- **实际结果**：quote_blocks=1 md_quote_lines=1
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-07` 条）

#### TC-M5-08 表格产出合法 GFM：表头+分隔行+各行列数一致

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：表格产出合法 GFM：表头+分隔行+各行列数一致
- **输入与前置条件**：docx_11_complex_tables
- **执行步骤**：convert 后逐表校验 GFM 形态
- **期望结果**：每张表都有分隔行且列数唯一
- **实际结果**：tables=5 malformed=[]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-08` 条）

#### TC-M5-09 合并单元格拍平 → W1045 告警可见

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：合并单元格拍平 → W1045 告警可见
- **输入与前置条件**：hard_03_merged_cells（4x3 含合并）
- **执行步骤**：convert 后读 warnings
- **期望结果**：warnings 含 W1045
- **实际结果**：docx_03=['W1045'] hard_03=['W1045']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-09` 条）

#### TC-M5-10 DOCX 列表映射为 list_item 且 Markdown 保留列表标记

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：DOCX 列表映射为 list_item 且 Markdown 保留列表标记
- **输入与前置条件**：docx_02_lists
- **执行步骤**：convert 后数 list_item 与列表标记行
- **期望结果**：list_item ≥2 且 Markdown 标记行 ≥2
- **实际结果**：list_items=6 ir_markers=['1.', '2.', '3.', '•'] md_markers=6
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-10` 条）

#### TC-M5-11 CH-504：numFmt=none 标题不伪造编号，.doc 与 .docx 标题文本相同

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：CH-504：numFmt=none 标题不伪造编号，.doc 与 .docx 标题文本相同
- **输入与前置条件**：本机 LibreOffice 生成的 OLE .doc vs 源 .docx
- **执行步骤**：两次 convert 比对标题文本与正文
- **期望结果**：标题文本逐一相等、正文逐字节相同、无 '1. ' 前缀
- **实际结果**：doc_headings=['Doc2md DOCX 简单测试', '摘要', '加粗', '斜体', '删除线'] docx_headings=['Doc2md DOCX 简单测试', '摘要', '加粗', '斜体', '删除线'] body_identical=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-11` 条）

#### TC-M5-12 逐页变体页眉不得作为正文交付

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：逐页变体页眉不得作为正文交付
- **输入与前置条件**：hard_08_header_variants.pdf
- **执行步骤**：convert 后在正文中搜页眉字样
- **期望结果**：奇/偶数页字样均不出现
- **实际结果**：body_len=320 furniture_hits=[]
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-12` 条）

#### TC-M5-13 外部链接映射为 Markdown 链接语法

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：外部链接映射为 Markdown 链接语法
- **输入与前置条件**：pdf_04_links_code_2p
- **执行步骤**：convert 后数带 links 的块与 [](  ) 形态
- **期望结果**：两侧均 ≥1
- **实际结果**：blocks_with_links=1 md_links=['https://example.com']
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-13` 条）

#### TC-M5-14 page_break 不渲染为可见控制符

- **所属模块**：M5（S3 结构映射（七类块））
- **测试目标**：page_break 不渲染为可见控制符
- **输入与前置条件**：pdf_05_cjk_en_3p
- **执行步骤**：convert 后检查正文控制符
- **期望结果**：正文不含 \x0c
- **实际结果**：page_break_blocks=0 pages=3 body_has_formfeed=False
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M5-14` 条）

### 3.6 M6 S4 Markdown 渲染与安全红线

#### TC-M6-01 渲染产物不含裸脚本/JS 伪协议（安全红线）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：渲染产物不含裸脚本/JS 伪协议（安全红线）
- **输入与前置条件**：links_pdf.pdf
- **执行步骤**：convert --format both
- **期望结果**：无 <script / javascript:
- **实际结果**：len=918 dangerous=False
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-01` 条）

#### TC-M6-02 缩进代码块样本转换成功（围栏字面量豁免路径）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：缩进代码块样本转换成功（围栏字面量豁免路径）
- **输入与前置条件**：codeindent_1p.pdf
- **执行步骤**：convert --format both
- **期望结果**：status=ok
- **实际结果**：rc_status=ok blocks=8
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-02` 条）

#### TC-M6-03 front-matter 键集合与版本口径与产品常量一致

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：front-matter 键集合与版本口径与产品常量一致
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：convert 后解析 YAML 头
- **期望结果**：键集合恰好相等且版本号匹配 PIPELINE_VERSION / SCHEMA_VERSION
- **实际结果**：missing=[] extra=[] pipeline=2.20.0 schema=14
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-03` 条）

#### TC-M6-04 注入文本渲染后不可执行/不可点击（转义而非抹除，保留字面内容）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：注入文本渲染后不可执行/不可点击（转义而非抹除，保留字面内容）
- **输入与前置条件**：自造 docx，含 <script> / onerror / javascript: 链接
- **执行步骤**：convert 后检查 content.md 的转义形态与 report.html 的 href
- **期望结果**：无原样 <script>、无活的 javascript: 链接、report.html 无 javascript: href
- **实际结果**：md_raw_script=False md_live_js_link=False html_js_href=False html_raw_script=False md_excerpt='\n\\<script>alert(1)\\</script>\n\n\\<img src=x onerror="alert(2)">\n\n\\[click\\](javascript:alert(3))\n'
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-04` 条）

#### TC-M6-05 report.html 中注入文本已转义

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：report.html 中注入文本已转义
- **输入与前置条件**：自造 docx，含 <script>
- **执行步骤**：convert 后搜 report.html
- **期望结果**：无原样 <script> 片段
- **实际结果**：html_len=1917 raw_script=False
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-05` 条）

#### TC-M6-06 代码块渲染为成对围栏（语言标签缺口另记 defect_scan minor）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：代码块渲染为成对围栏（语言标签缺口另记 defect_scan minor）
- **输入与前置条件**：docx_07_code_quote
- **执行步骤**：convert 后数 ``` 行与 code 块
- **期望结果**：code 块 ≥1 且围栏成对出现
- **实际结果**：fences=['', ''] code_blocks=1
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-06` 条）

#### TC-M6-07 CJK/西文混排正文保留拉丁词，不整体塌词

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：CJK/西文混排正文保留拉丁词，不整体塌词
- **输入与前置条件**：pdf_05_cjk_en_3p
- **执行步骤**：convert 后检查拉丁词与可疑塌词形态
- **期望结果**：拉丁词在位（塌词样本另由 defect_scan 的 cjk_space_collapse 把守）
- **实际结果**：body_len=1074 has_latin=True suspicious_collapse=[]
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-07` 条）

#### TC-M6-08 同输入三次转换正文与块类型序列完全一致（幂等）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：同输入三次转换正文与块类型序列完全一致（幂等）
- **输入与前置条件**：pdf_02_lists_2p
- **执行步骤**：连转三次比对
- **期望结果**：正文与块序列三者全等
- **实际结果**：三次正文一致=True 块类型序列一致=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-08` 条）

#### TC-M6-09 s4.preserve_marks_strike 开关真实生效（关=无 ~~，开=有 ~~）

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：s4.preserve_marks_strike 开关真实生效（关=无 ~~，开=有 ~~）
- **输入与前置条件**：docx_01_simple（含删除线段）
- **执行步骤**：两种配置各转一次
- **期望结果**：关闭时无 ~~、开启时有 ~~
- **实际结果**：strike_off_has_tilde=False strike_on_has_tilde=True off_len=111 on_len=115
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-09` 条）

#### TC-M6-10 渲染无静默截断：正文有效字符数与 ir 文本量同量级

- **所属模块**：M6（S4 Markdown 渲染与安全红线）
- **测试目标**：渲染无静默截断：正文有效字符数与 ir 文本量同量级
- **输入与前置条件**：docx_10_complex_long（92 块）
- **执行步骤**：convert 后比对字符量比值
- **期望结果**：比值落在 0.8–1.25
- **实际结果**：reported_chars=5952 ir_text_chars=6358 md_nonfmt_chars=5841 ratio=0.919
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M6-10` 条）

### 3.7 M7 LLM 修复、回退与 op 契约

#### TC-M7-01 LLM offline 模式零网络转换成功

- **所属模块**：M7（LLM 修复、回退与 op 契约）
- **测试目标**：LLM offline 模式零网络转换成功
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：convert --llm-mode offline
- **期望结果**：status=ok
- **实际结果**：status=ok rc_ok=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M7-01` 条）

#### TC-M7-02 LLM server+no-fallback 且有修复候选 → E5041/rc=4 不回退

- **所属模块**：M7（LLM 修复、回退与 op 契约）
- **测试目标**：LLM server+no-fallback 且有修复候选 → E5041/rc=4 不回退
- **输入与前置条件**：服务未启动；pdf_09_complex_long_8p（实测 2 个候选）
- **执行步骤**：convert --llm-mode server --llm-no-fallback
- **期望结果**：rc=4 且 code=E5041 status=error
- **实际结果**：rc=4 code=E5041 status=error detail={'stage': 'http', 'bind': '127.0.0.1', 'port': 8080, 'hint': '启动 llama-server 或把 llm.mode 改回 offline'}
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M7-02` 条）

#### TC-M7-03 无修复候选时 server 模式短路成功且产物与 offline 逐字节相同

- **所属模块**：M7（LLM 修复、回退与 op 契约）
- **测试目标**：无修复候选时 server 模式短路成功且产物与 offline 逐字节相同
- **输入与前置条件**：pdf_01_simple_1p（实测 0 候选）；服务未启动
- **执行步骤**：offline 与 server+no-fallback 各转一次比对 content.md
- **期望结果**：server rc=0 status=ok 且 content.md 与 offline 一致
- **实际结果**：offline_rc=0 server_rc=0 server_status=ok md_identical=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M7-03` 条）

### 3.8 M8 打包与产物形态

#### TC-M8-01 zip 产物为 {doc_id}/ 前缀的 content.md/ir.json/report.html 三件套

- **所属模块**：M8（打包与产物形态）
- **测试目标**：zip 产物为 {doc_id}/ 前缀的 content.md/ir.json/report.html 三件套
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：convert 后读 zip namelist
- **期望结果**：条目集合恰为 {doc_id}/ 三件套
- **实际结果**：doc_id=a545becc4d31a988 zip_entries=['a545becc4d31a988/content.md', 'a545becc4d31a988/ir.json', 'a545becc4d31a988/report.html']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M8-01` 条）

#### TC-M8-02 交付 zip 两次打包 sha256 相同（R19-PIPE-1 固定时间戳）

- **所属模块**：M8（打包与产物形态）
- **测试目标**：交付 zip 两次打包 sha256 相同（R19-PIPE-1 固定时间戳）
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：两次独立 root 转换比对 zip 字节
- **期望结果**：zip 字节级一致
- **实际结果**：zip_sha=f8030da57406efd5/f8030da57406efd5 identical=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M8-02` 条）

#### TC-M8-03 --format both 的 .md 直出与 zip 内 content.md 逐字节相同

- **所属模块**：M8（打包与产物形态）
- **测试目标**：--format both 的 .md 直出与 zip 内 content.md 逐字节相同
- **输入与前置条件**：pdf_01_simple_1p
- **执行步骤**：convert --format both 比对两产物
- **期望结果**：字节级一致
- **实际结果**：md_file_len=527 identical=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M8-03` 条）

#### TC-M8-04 交付正文无 shard 内部锚（ir.json 按设计保留，断言非恒真）

- **所属模块**：M8（打包与产物形态）
- **测试目标**：交付正文无 shard 内部锚（ir.json 按设计保留，断言非恒真）
- **输入与前置条件**：pdf_08_large_40p
- **执行步骤**：convert 后逐条目统计 token
- **期望结果**：content.md/report.html 命中 0 且 ir.json 命中>0
- **实际结果**：delivery_hits={'content.md': 0, 'report.html': 0} ir_json_hits=1120 entries=['content.md', 'ir.json', 'report.html']
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M8-04` 条）

### 3.9 M9 存储、检索与库操作

#### TC-M9-01 CLI 入库（enqueue→work）后 docs list 回两篇 + 分页信封

- **所属模块**：M9（存储、检索与库操作）
- **测试目标**：CLI 入库（enqueue→work）后 docs list 回两篇 + 分页信封
- **输入与前置条件**：两文件目录
- **执行步骤**：work --enqueue → work --once → docs list
- **期望结果**：rc=0 docs=2
- **实际结果**：rc=0 docs=2 envelope=['docs', 'has_more', 'limit', 'offset', 'total']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M9-01` 条）

#### TC-M9-02 CLI search 对已入库 CJK 文档可检索

- **所属模块**：M9（存储、检索与库操作）
- **测试目标**：CLI search 对已入库 CJK 文档可检索
- **输入与前置条件**：入库一篇 CJK
- **执行步骤**：search 内容
- **期望结果**：rc=0
- **实际结果**：rc=0 stdout_head={
  "total": 20,
  "has_more": true,
  "hits": [
    {
      "doc_id": "568ee05237b25b0c",
      "block_id": "b10",
    
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M9-02` 条）

#### TC-M9-03 库不存在时 search → 退出码 4 / E4041

- **所属模块**：M9（存储、检索与库操作）
- **测试目标**：库不存在时 search → 退出码 4 / E4041
- **输入与前置条件**：空 root
- **执行步骤**：search x
- **期望结果**：rc=4 code=E4041
- **实际结果**：rc=4 code=E4041
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M9-03` 条）

#### TC-M9-04 docs export 导出已入库文档 content.md

- **所属模块**：M9（存储、检索与库操作）
- **测试目标**：docs export 导出已入库文档 content.md
- **输入与前置条件**：入库一篇
- **执行步骤**：docs export <doc_id>
- **期望结果**：rc=0
- **实际结果**：rc=0 stdout_head= stderr=
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M9-04` 条）

### 3.10 M10 API 服务、权限、分页与编辑锁

#### TC-M10-01 无凭据访问受保护端点 → 401/E4011

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：无凭据访问受保护端点 → 401/E4011
- **输入与前置条件**：空库 + 三种角色 token 已签发
- **执行步骤**：GET /docs 不带任何鉴权头
- **期望结果**：401 且 body 为 {code:'E4011',message,detail}
- **实际结果**：status=401 body={"code": "E4011", "message": "missing token"}
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-01` 条）

#### TC-M10-02 reader 对三个只读端点均 200

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：reader 对三个只读端点均 200
- **输入与前置条件**：reader token
- **执行步骤**：GET /docs、/settings、/jobs
- **期望结果**：三者皆 200
- **实际结果**：{"reader /docs": 200, "reader /settings": 200, "reader /jobs": 200}
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-02` 条）

#### TC-M10-03 越权写操作一律 403/E4031（5 个组合）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：越权写操作一律 403/E4031（5 个组合）
- **输入与前置条件**：reader/editor token
- **执行步骤**：reader 提交作业/签 token；editor 删文档/改设置/reload
- **期望结果**：全部 403
- **实际结果**：status=[403, 403, 403, 403, 403] code=['E4031', 'E4031', 'E4031', 'E4031', 'E4031']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-03` 条）

#### TC-M10-04 未知 token → 401（不是 403）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：未知 token → 401（不是 403）
- **输入与前置条件**：伪造 d2m_ 前缀 token
- **执行步骤**：GET /docs 带伪 token
- **期望结果**：401/E4011
- **实际结果**：status=401 code=E4011
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-04` 条）

#### TC-M10-05 Bearer 头可用、?token= 查询参数不被承认

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：Bearer 头可用、?token= 查询参数不被承认
- **输入与前置条件**：reader/admin token
- **执行步骤**：GET /docs 用 Authorization: Bearer；再用 ?token=
- **期望结果**：200 与 401（附录 C：不支持查询参数）
- **实际结果**：bearer=200 query_token=401
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-05` 条）

#### TC-M10-06 吊销即时生效；未知 token 吊销 → 404/E4041

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：吊销即时生效；未知 token 吊销 → 404/E4041
- **输入与前置条件**：admin + reader token
- **执行步骤**：DELETE /tokens/<reader>；再用该 token 访问；再吊销未知 token
- **期望结果**：200 → 401 → 404/E4041
- **实际结果**：revoke=200/{'revoked': True} after=401 unknown=404/E4041
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-06` 条）

#### TC-M10-07 POST /tokens：role 必填 + 白名单；label 非串 400

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：POST /tokens：role 必填 + 白名单；label 非串 400
- **输入与前置条件**：admin token
- **执行步骤**：四种 body：非法 role / 缺 role / 超长 label / 非串 label
- **期望结果**：前两者 400 且 detail.allowed 回可签发集；非串 label 400
- **实际结果**：bad_role=400/E4001/allowed=['admin', 'editor', 'reader'] missing=400/E4001 long_label=200 non_str_label=400/E4001
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：契约一致性；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-07` 条）

#### TC-M10-08 auth.issue 对白名单外角色抛 ValueError（不建行）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：auth.issue 对白名单外角色抛 ValueError（不建行）
- **输入与前置条件**：空库
- **执行步骤**：auth.issue(store, 'root')
- **期望结果**：ValueError
- **实际结果**：ValueError(bad role root（可签发：['admin', 'editor', 'reader']）)
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-08` 条）

#### TC-M10-09 权限矩阵 9 键全可判；矩阵外动作 403 而非 KeyError

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：权限矩阵 9 键全可判；矩阵外动作 403 而非 KeyError
- **输入与前置条件**：admin token
- **执行步骤**：遍历 auth.MATRIX 每个 action 调 auth.check；再调未声明动作
- **期望结果**：9 键全通过；未声明动作抛 E4031
- **实际结果**：matrix_keys=9 all_pass=9 unknown_action=E4031_Forbidden
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-09` 条）

#### TC-M10-10 同步提交单文件 → 202 + doc_id（实测无 status 键，附录 C 的 ok 从未发出）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：同步提交单文件 → 202 + doc_id（实测无 status 键，附录 C 的 ok 从未发出）
- **输入与前置条件**：空库、small_3p.pdf
- **执行步骤**：POST /jobs {path}
- **期望结果**：202，jobs[0] 有 doc_id、attached=False、无 status 键
- **实际结果**：status=202 keys=['jobs'] job_keys=['attached', 'doc_id', 'file', 'idempotency_hit', 'job_id', 'shard_count', 'sharded'] job_status=None doc_id=y
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-10` 条）

#### TC-M10-11 POST /jobs 三类异常输入：缺 path / 路径不存在 / mode 非白名单

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：POST /jobs 三类异常输入：缺 path / 路径不存在 / mode 非白名单
- **输入与前置条件**：editor token
- **执行步骤**：三种 body 依次提交
- **期望结果**：400/E4001、404/E4041、400/E4001
- **实际结果**：missing=400/E4001 notfound=404/E4041 badmode=400/E4001/detail={'mode': 'str'}
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-11` 条）

#### TC-M10-12 非受支持类型 → 415/E4151

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：非受支持类型 → 415/E4151
- **输入与前置条件**：editor token、一个 .txt
- **执行步骤**：POST /jobs {path: x.txt}
- **期望结果**：415/E4151
- **实际结果**：status=415 code=E4151
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-12` 条）

#### TC-M10-13 重复转换未 force → 202 逐文件 conflict/E4091（非 409）；force=true 放行

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：重复转换未 force → 202 逐文件 conflict/E4091（非 409）；force=true 放行
- **输入与前置条件**：已入库一篇且已 PATCH 出一个编辑
- **执行步骤**：同一 path 再提交；再带 force 提交
- **期望结果**：202 status=conflict code=E4091 → 202 非 conflict
- **实际结果**：second=202/conflict/E4091 edited_blocks=1 forced=202/None
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-13` 条）

#### TC-M10-14 force 只认真值：有编辑时 'false'/'no'/'0'/1/None 护编辑(conflict)，仅真值放行

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：force 只认真值：有编辑时 'false'/'no'/'0'/1/None 护编辑(conflict)，仅真值放行
- **输入与前置条件**：已入库一篇且已 PATCH 出一个编辑
- **执行步骤**：十种 force 取值逐一提交，记录逐文件 status
- **期望结果**：仅 true/'true'/'1'/'yes' 非 conflict，其余 conflict
- **实际结果**：{"'false'": 'conflict', "'no'": 'conflict', "'0'": 'conflict', "''": 'conflict', 'None': 'conflict', '1': 'conflict', "'true'": 'ok', "'1'": 'ok', "'yes'": 'ok', 'True': 'ok'} non-conflict=["'1'", "'true'", "'yes'", 'True']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-14` 条）

#### TC-M10-15 Idempotency-Key 重放 → idempotency_hit=true/attached/status=done，同 job_id

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：Idempotency-Key 重放 → idempotency_hit=true/attached/status=done，同 job_id
- **输入与前置条件**：editor token
- **执行步骤**：同键提交两次
- **期望结果**：第二次 idempotency_hit=true、attached=true、status=done、job_id 同首次
- **实际结果**：first=202/job-cf2cbc53d95d second=202 idem_hit=True status=done attached=True same_job=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-15` 条）

#### TC-M10-16 异步面：目录整批入队 + consume_with 提示 + 逐文件 queued

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：异步面：目录整批入队 + consume_with 提示 + 逐文件 queued
- **输入与前置条件**：perf.queue_async=true、目录含两个 PDF
- **执行步骤**：POST /jobs {path: 目录}
- **期望结果**：202 mode=queued、consume_with、队列 2 行、逐文件 status=queued
- **实际结果**：status=202 mode=queued consume_with=doc2md work jobs=2 queued_rows=2
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-16` 条）

#### TC-M10-17 异步面全或无：目录内一个魔术码不符 → 整批 415/E4151 且队列零增长

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：异步面全或无：目录内一个魔术码不符 → 整批 415/E4151 且队列零增长
- **输入与前置条件**：queue_async=true、目录含 a.pdf + fake.pdf(坏魔术码)
- **执行步骤**：POST /jobs {path: 目录}
- **期望结果**：415/E4151，tasks 表 0 行
- **实际结果**：status=415 code=E4151 queue_rows=0
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-17` 条）

#### TC-M10-18 GET /jobs 分页信封：offset/limit/total/has_more + 四种非法值

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /jobs 分页信封：offset/limit/total/has_more + 四种非法值
- **输入与前置条件**：库内 ≥3 条任务
- **执行步骤**：limit=2&offset=1；limit=0；limit=9999；limit=-1；offset=1e20
- **期望结果**：信封六键齐；截断到 500；负值与越界 400/E4001；limit=0 为空集且 has_more=false
- **实际结果**：envelope=['counts', 'has_more', 'limit', 'offset', 'tasks', 'total'] total=3 offset=1 limit=2 has_more=False rows=2 limit0=(200,0,False) big=200/500 neg=400 overflow=400/E4001
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-18` 条）

#### TC-M10-19 GET /docs 集合级清单存在且信封完整（CH-500 新增路由）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /docs 集合级清单存在且信封完整（CH-500 新增路由）
- **输入与前置条件**：库内一篇
- **执行步骤**：GET /docs
- **期望结果**：200，五键信封，每行带 blocks 计数
- **实际结果**：status=200 envelope=['docs', 'has_more', 'limit', 'offset', 'total'] total=1 has_more=False rows=1 row0_keys=['blocks', 'created_at', 'doc_id', 'format', 'pages', 'profile', 'shard_count', 'sharded', 'source_key', 'source_name', 'status', 'updated_at']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-19` 条）

#### TC-M10-20 GET /docs/<id> 块窗口：默认 limit=500 且回三个窗口元信息

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /docs/<id> 块窗口：默认 limit=500 且回三个窗口元信息
- **输入与前置条件**：库内一篇
- **执行步骤**：GET /docs/<doc_id> 不带 limit
- **期望结果**：200，blocks_limit=500，元信息三项齐
- **实际结果**：status=200 keys=['blocks', 'blocks_limit', 'blocks_offset', 'blocks_total', 'created_at', 'doc_id', 'format', 'gate_json', 'meta', 'pages', 'profile', 'shard_count', 'sharded', 'source_key', 'source_name', 'source_name_raw', 'status', 'updated_at', 'warnings'] blocks=60 blocks_total=60 off=0 lim=500
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-20` 条）

#### TC-M10-21 块窗口 limit=0 是空集（不是全量）、limit=2 生效、负值 400

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：块窗口 limit=0 是空集（不是全量）、limit=2 生效、负值 400
- **输入与前置条件**：库内一篇
- **执行步骤**：三次 GET /docs/<id> 带不同 limit
- **期望结果**：0 行 / 2 行且 total 不变 / 400
- **实际结果**：limit0=(200,0) limit2=(200,2,60) neg=(400,E4001)
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-21` 条）

#### TC-M10-22 导出 markdown：200 text/markdown；不存在 → 404

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：导出 markdown：200 text/markdown；不存在 → 404
- **输入与前置条件**：库内一篇
- **执行步骤**：GET /docs/<id>/markdown；再取不存在 doc
- **期望结果**：200 + 正文；404/E4041
- **实际结果**：status=200 ctype=text/markdown; charset=utf-8 bytes=7135 notfound=404/E4041
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-22` 条）

#### TC-M10-23 同一 doc 两次导出逐字节相同（流式分块不改内容）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：同一 doc 两次导出逐字节相同（流式分块不改内容）
- **输入与前置条件**：库内一篇
- **执行步骤**：连续两次 GET markdown 比字节
- **期望结果**：字节相同
- **实际结果**：bytes_equal=True len=7135
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-23` 条）

#### TC-M10-24 PATCH 块：带正确 base_revision → 200 且 revision 递增

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：PATCH 块：带正确 base_revision → 200 且 revision 递增
- **输入与前置条件**：库内一篇
- **执行步骤**：取首块 revision，PATCH text
- **期望结果**：200，回新 revision
- **实际结果**：status=200 body={'block_id': 'b1', 'revision': 2} old_rev=1
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-24` 条）

#### TC-M10-25 PATCH 四种非法 body 全部 400/E4001（缺 text / 缺 base_revision / 非整数 / reason 非串）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：PATCH 四种非法 body 全部 400/E4001（缺 text / 缺 base_revision / 非整数 / reason 非串）
- **输入与前置条件**：库内一篇
- **执行步骤**：四种 body 依次 PATCH
- **期望结果**：四个 400/E4001
- **实际结果**：codes=[(400, 'E4001'), (400, 'E4001'), (400, 'E4001'), (400, 'E4001')]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-25` 条）

#### TC-M10-26 版本冲突：陈旧 base_revision → 425/E4251 且块保持首次结果

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：版本冲突：陈旧 base_revision → 425/E4251 且块保持首次结果
- **输入与前置条件**：库内一篇
- **执行步骤**：同 base_revision 连改两次；再取块与审计
- **期望结果**：第二次 425，正文=改A，审计 1 行
- **实际结果**：first=200 stale=425/E4251 text_now='改A' audit_rows=1 has_more=False total=1
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-26` 条）

#### TC-M10-27 回滚到指定 revision 后正文复原

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：回滚到指定 revision 后正文复原
- **输入与前置条件**：库内一篇、已改一次
- **执行步骤**：POST rollback {to_revision}
- **期望结果**：200 且正文回到原文
- **实际结果**：rollback=200 restored=True rev=3
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-27` 条）

#### TC-M10-28 审计窗口：rowid 升序 + total/has_more + 两页不重不漏

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：审计窗口：rowid 升序 + total/has_more + 两页不重不漏
- **输入与前置条件**：库内一篇、同块改 4 次
- **执行步骤**：全量取审计；再 limit=2 取两页
- **期望结果**：total=4、升序、第一页 has_more=true、第二页 false
- **实际结果**：total=4 rows=4 has_more=False limit=500 p1=2/True p2=2/False ascending=True ids=[2, 3, 4, 5]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-28` 条）

#### TC-M10-29 删除文档：缺 confirm → 400；带 confirm → 200 {doc_id,deleted,files}（无 cascade）、删除审计留痕

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：删除文档：缺 confirm → 400；带 confirm → 200 {doc_id,deleted,files}（无 cascade）、删除审计留痕
- **输入与前置条件**：库内一篇
- **执行步骤**：DELETE 不带 confirm；再带 confirm=true；再取 /audit/deletions
- **期望结果**：400 → 200（deleted/files 三键）→ 删除审计 1 行、信封五键、行内含 cascade
- **实际结果**：no_confirm=400/E4001 deleted=200/['deleted', 'doc_id', 'files'] deleted_counts={'audit': 0, 'blocks': 60, 'events': 1} deletions_env=['deletions', 'has_more', 'limit', 'offset', 'total'] rows=1 row_cascade=['deleted', 'files'] has_more=False
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-29` 条）

#### TC-M10-30 DELETE /sources/<key> 未知键的可观测形态（记录实测）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：DELETE /sources/<key> 未知键的可观测形态（记录实测）
- **输入与前置条件**：库内一篇
- **执行步骤**：DELETE /sources/nosuchkey?confirm=true
- **期望结果**：记录实际状态码（契约未逐字规定）
- **实际结果**：status=404 code=E4041
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-30` 条）

#### TC-M10-31 GET /search 信封与窗口：默认 limit=20、limit=0 空集、负值 400、offset 生效

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /search 信封与窗口：默认 limit=20、limit=0 空集、负值 400、offset 生效
- **输入与前置条件**：库内一篇（中文正文）
- **执行步骤**：七种查询串（命中词/limit=0/mode=full/空 q/负 limit/offset=1/缺省 limit）
- **期望结果**：信封七键；默认 limit=20；limit=0 空集且 has_more=false；空 q → mode=empty；负值 400
- **实际结果**：env=['has_more', 'hits', 'limit', 'match', 'mode', 'offset', 'total'] total=2 hits=2 mode=fts match=内容 has_more=True off=0 lim=2 default_lim=20 limit0=(200,0,False) full_mode=full full_total=20 empty=(200,empty) neg=400 offset1_hits=2
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-31` 条）

#### TC-M10-32 无命中与畸形查询：200 + total=0，降级路径带 fts_error 而非 500

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：无命中与畸形查询：200 + total=0，降级路径带 fts_error 而非 500
- **输入与前置条件**：库内一篇
- **执行步骤**：检索不存在的词（mode=full）；再检索 FTS 语法片段
- **期望结果**：两者 200；无命中 total=0
- **实际结果**：nohit=200 mode=full total=0 hits=0 | odd=200 mode=empty fts_error=None
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-32` 条）

#### TC-M10-33 repo.search 直调回同一信封（API 之下的同一真源）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：repo.search 直调回同一信封（API 之下的同一真源）
- **输入与前置条件**：库内一篇
- **执行步骤**：repo.search(store, '内容', limit=5)
- **期望结果**：dict，七键信封
- **实际结果**：type=dict keys=['has_more', 'hits', 'limit', 'match', 'mode', 'offset', 'total'] total=5 hits=5 mode=fts
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：回归；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-33` 条）

#### TC-M10-34 PUT /settings 累计稀疏差量：嵌套与平铺两种写法都生效、互不抹除、0600

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：PUT /settings 累计稀疏差量：嵌套与平铺两种写法都生效、互不抹除、0600
- **输入与前置条件**：admin token
- **执行步骤**：PUT 嵌套 logs.rotate_mb；再 PUT 平铺 api.port；读 override 文件与有效值
- **期望结果**：文件恰含两键的嵌套差量、权限 0600、平铺键真的生效（R19-SVC-1）
- **实际结果**：get=200 nested=200/{'updated': ['logs']} flat=200/{'updated': ['api.port']} override={"api": {"port": 9001}, "logs": {"rotate_mb": 11}} mode=0o600 flat_effective=9001
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-34` 条）

#### TC-M10-35 PUT /settings 六类拒绝：未知键 / 未知分表（平铺）/ 非对象 / 两条红线键 / 非法值，且一律不落盘

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：PUT /settings 六类拒绝：未知键 / 未知分表（平铺）/ 非对象 / 两条红线键 / 非法值，且一律不落盘
- **输入与前置条件**：admin token
- **执行步骤**：六种 body 依次 PUT；最后查 override 文件是否存在
- **期望结果**：皆 400/E4001；override 文件始终不存在（被拒的写入零副作用）
- **实际结果**：{'unknown_key': 400, 'unknown_section_flat': (400, 'E4001', 'unknown configuration key(s): log（见方案 附录 A）'), 'nonobj': 400, 'redline': (400, 'E4001'), 'redline2': (400, 'E4001'), 'bad_value': 400, 'override_exists': False}
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-35` 条）

#### TC-M10-36 POST /system/reload → 200 {reloaded, ts}

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：POST /system/reload → 200 {reloaded, ts}
- **输入与前置条件**：admin token
- **执行步骤**：POST /system/reload
- **期望结果**：200，两键
- **实际结果**：status=200 body=['reloaded', 'ts'] reloaded=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-36` 条）

#### TC-M10-37 坏配置的 reload → 400 且服务保持旧配置继续可用

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：坏配置的 reload → 400 且服务保持旧配置继续可用
- **输入与前置条件**：override 文件写坏值
- **执行步骤**：POST /system/reload；再 GET /settings
- **期望结果**：400 + 服务仍 200
- **实际结果**：reload=400/E4001 still_serving=200
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-37` 条）

#### TC-M10-38 HTTP 层异常也进 JSON 契约：405 带 detail.allowed；404 detail 为空时省略该键

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：HTTP 层异常也进 JSON 契约：405 带 detail.allowed；404 detail 为空时省略该键
- **输入与前置条件**：空库
- **执行步骤**：PUT /healthz；GET /no/such/route
- **期望结果**：405 三键（allowed 非空）+ 404 两键（to_body 对空 detail 省略）
- **实际结果**：405=405/application/json/['code', 'detail', 'message']/allowed=['GET', 'HEAD', 'OPTIONS'] 404=404/['code', 'message']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-38` 条）

#### TC-M10-39 非 JSON Content-Type + 非空 body → 400/E4001（detail.field=Content-Type）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：非 JSON Content-Type + 非空 body → 400/E4001（detail.field=Content-Type）
- **输入与前置条件**：admin token
- **执行步骤**：POST /tokens 用表单 Content-Type 传 role=admin；再传空 body
- **期望结果**：前者 400 且指明 Content-Type；空 body 仍按 {} 走 role 缺失 400
- **实际结果**：form=400/E4001/field=Content-Type empty=400/E4001
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-39` 条）

#### TC-M10-40 整数查询参数越界一律 400/E4001（四个端点，不是 500 HTML）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：整数查询参数越界一律 400/E4001（四个端点，不是 500 HTML）
- **输入与前置条件**：reader/admin token
- **执行步骤**：GET /events?since=1e20、/audit/deletions?offset=9*25、/jobs?offset=1e20、/docs?limit=1e20
- **期望结果**：四者皆 400/E4001
- **实际结果**：{'/events?since=1e20': (400, 'E4001'), '/audit/deletions?offset=9999999999999999999999999': (400, 'E4001'), '/jobs?offset=1e20': (400, 'E4001'), '/docs?limit=1e20': (400, 'E4001')}
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-40` 条）

#### TC-M10-41 /healthz 无鉴权且回 pipeline 版本；/ 出静态审阅页

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：/healthz 无鉴权且回 pipeline 版本；/ 出静态审阅页
- **输入与前置条件**：空库
- **执行步骤**：GET /healthz；GET /
- **期望结果**：200 四键（pipeline=2.20.0）；200 且 HTML 非空
- **实际结果**：health=200/['docs', 'ok', 'pipeline', 'ts'] index=200/len=34170/ctype=text/html; charset=utf-8
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-41` 条）

#### TC-M10-42 GET /events 单次不超过 200 条

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /events 单次不超过 200 条
- **输入与前置条件**：reader token
- **执行步骤**：GET /events?since=0
- **期望结果**：200 且条数 ≤200
- **实际结果**：status=200 type=dict n=0 cap_ok=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-42` 条）

#### TC-M10-43 GET /jobs/<id>/events 对浏览器 UA 403（FR-125 硬规则）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：GET /jobs/<id>/events 对浏览器 UA 403（FR-125 硬规则）
- **输入与前置条件**：已提交一作业
- **执行步骤**：分别用脚本 UA 与 Mozilla UA 取事件
- **期望结果**：200 与 403
- **实际结果**：script_ua=200 browser_ua=403/E4031
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-43` 条）

#### TC-M10-44 请求体超硬上限 → 413/E4131（JSON 契约，不是 HTML）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：请求体超硬上限 → 413/E4131（JSON 契约，不是 HTML）
- **输入与前置条件**：admin token
- **执行步骤**：POST /tokens 带 40MiB label
- **期望结果**：413/E4131
- **实际结果**：status=413 code=E4131
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-44` 条）

#### TC-M10-45 目录批量提交的可观测形态（60 个假 PDF）

- **所属模块**：M10（API 服务、权限、分页与编辑锁）
- **测试目标**：目录批量提交的可观测形态（60 个假 PDF）
- **输入与前置条件**：editor token、含 60 个 .pdf 的目录
- **执行步骤**：POST /jobs {path: dir}
- **期望结果**：记录实际（目录过宽/拒收/入队）
- **实际结果**：status=202 code=None
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M10-45` 条）

### 3.11 M11 CLI 批量、退出码与格式出口

#### TC-M11-01 目录批量混合合法+坏魔术码 → 退出码聚合为 4，合法件仍 ok

- **所属模块**：M11（CLI 批量、退出码与格式出口）
- **测试目标**：目录批量混合合法+坏魔术码 → 退出码聚合为 4，合法件仍 ok
- **输入与前置条件**：ok.pdf + bad.pdf
- **执行步骤**：convert 目录 --json
- **期望结果**：rc=4 且含 ok
- **实际结果**：rc=4 statuses=['error', 'ok']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M11-01` 条）

#### TC-M11-02 --skip-existing 二转 → status=skipped 退出码 0

- **所属模块**：M11（CLI 批量、退出码与格式出口）
- **测试目标**：--skip-existing 二转 → status=skipped 退出码 0
- **输入与前置条件**：已入库一篇
- **执行步骤**：convert --skip-existing
- **期望结果**：rc=0 status=skipped
- **实际结果**：rc=0 status=skipped
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M11-02` 条）

#### TC-M11-03 --workers 1 串行与 --workers 2 池产物逐字节等价

- **所属模块**：M11（CLI 批量、退出码与格式出口）
- **测试目标**：--workers 1 串行与 --workers 2 池产物逐字节等价
- **输入与前置条件**：两文件目录
- **执行步骤**：两种 workers 转换比对
- **期望结果**：产物哈希一致
- **实际结果**：serial={'a545becc4d31a988.zip': '0f28912973bb', 'b9900243eccc3bc0.zip': 'a515e6972dbc'} pool={'a545becc4d31a988.zip': '0f28912973bb', 'b9900243eccc3bc0.zip': 'a515e6972dbc'} equal=True
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M11-03` 条）

#### TC-M11-04 --json 时 stdout 纯 JSON（进度行人读面走 stderr）

- **所属模块**：M11（CLI 批量、退出码与格式出口）
- **测试目标**：--json 时 stdout 纯 JSON（进度行人读面走 stderr）
- **输入与前置条件**：small pdf
- **执行步骤**：convert --json
- **期望结果**：stdout 每行皆 JSON
- **实际结果**：rc=0 stdout_pure_json=True
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M11-04` 条）

#### TC-M11-05 --version 报当前 PIPELINE_VERSION 2.20.0

- **所属模块**：M11（CLI 批量、退出码与格式出口）
- **测试目标**：--version 报当前 PIPELINE_VERSION 2.20.0
- **输入与前置条件**：—
- **执行步骤**：doc2md --version
- **期望结果**：含 2.20.0
- **实际结果**：rc=0 version=doc2md 2.20.0
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M11-05` 条）

### 3.12 M12 运维、性能与可观测性

#### TC-M12-01 doctor 自检：报版本/tokenizer/冻结键且 rc=0

- **所属模块**：M12（运维、性能与可观测性）
- **测试目标**：doctor 自检：报版本/tokenizer/冻结键且 rc=0
- **输入与前置条件**：—
- **执行步骤**：doctor
- **期望结果**：rc=0 pipeline=2.20.0 frozen=ok
- **实际结果**：rc=0 pipeline=2.20.0 tokenizer=gguf-embedded frozen=ok
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M12-01` 条）

#### TC-M12-02 work --status 报队列计数

- **所属模块**：M12（运维、性能与可观测性）
- **测试目标**：work --status 报队列计数
- **输入与前置条件**：空 root
- **执行步骤**：work --status
- **期望结果**：rc=0 回计数 JSON
- **实际结果**：rc=0 status={}
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M12-02` 条）

#### TC-M12-03 异步全链路：enqueue→work 消费→docs 可见

- **所属模块**：M12（运维、性能与可观测性）
- **测试目标**：异步全链路：enqueue→work 消费→docs 可见
- **输入与前置条件**：单文件目录
- **执行步骤**：work --enqueue → --once → docs list
- **期望结果**：done=1 docs=1
- **实际结果**：enqueue_rc=0 drained=1 done=1 docs=1
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M12-03` 条）

#### TC-M12-04 320 页大样本转换成功（墙钟仅作样本记录）

- **所属模块**：M12（运维、性能与可观测性）
- **测试目标**：320 页大样本转换成功（墙钟仅作样本记录）
- **输入与前置条件**：large_320p.pdf
- **执行步骤**：convert --json 计时
- **期望结果**：rc=0 status=ok
- **实际结果**：rc=0 blocks=5789 wall=10.2s（样本，非红线）
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-M12-04` 条）

### 3.13 MG GUI 审阅 / 编辑静态页

#### TC-MG-01 GET / 出静态审阅页且五个关键挂载点齐备

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：GET / 出静态审阅页且五个关键挂载点齐备
- **输入与前置条件**：服务已建（独立 root）
- **执行步骤**：GET /
- **期望结果**：200 + text/html + blocks/log/tok/qrows/drows 五挂载点
- **实际结果**：status=200 ctype=text/html; charset=utf-8 bytes=28379 anchors=5/5 ['id="blocks"', 'id="log"', 'id="tok"', 'id="qrows"', 'id="drows"']
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-01` 条）

#### TC-MG-02 内联脚本通过 node --check（语法面无错）

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：内联脚本通过 node --check（语法面无错）
- **输入与前置条件**：editor.html 的单个 <script> 块
- **执行步骤**：抽出脚本写 .js 后 node --check
- **期望结果**：退出码 0
- **实际结果**：node=/Users/just/.nvm/versions/node/v24.21.0/bin/node rc=0 lines=544 stderr=''
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-02` 条）

#### TC-MG-03 innerHTML 汇点的插值一律过 esc()，无裸插 API 字段

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：innerHTML 汇点的插值一律过 esc()，无裸插 API 字段
- **输入与前置条件**：editor.html 全量脚本
- **执行步骤**：正则扫 innerHTML 模板的 ${} 插值
- **期望结果**：所有插值都以 esc( 开头（无裸插）
- **实际结果**：innerHTML 模板数=4 裸插值=[]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-03` 条）

#### TC-MG-04 审阅页零外部资源引用（离线承诺 NFR-1）

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：审阅页零外部资源引用（离线承诺 NFR-1）
- **输入与前置条件**：editor.html 全文
- **执行步骤**：扫 src/href 的 http(s) 与脚本内 URL
- **期望结果**：无任何 http(s) 引用
- **实际结果**：外链属性=[] 脚本内 URL=[]
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-04` 条）

#### TC-MG-05 页面引用的相对资源全部 200（无死链）

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：页面引用的相对资源全部 200（无死链）
- **输入与前置条件**：editor.html 的 src/href 相对引用
- **执行步骤**：逐条 GET
- **期望结果**：全部 200
- **实际结果**：引用=[] 状态={}
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-05` 条）

#### TC-MG-06 R19-GUI-2：审计/历史两面板均经 auditNewest 取最新窗口

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：R19-GUI-2：审计/历史两面板均经 auditNewest 取最新窗口
- **输入与前置条件**：editor.html 脚本
- **执行步骤**：静态取证 helper 定义与调用点数
- **期望结果**：helper 存在且被 ≥2 处调用（showAudit / showHistory）
- **实际结果**：auditNewest 定义=True 调用点=2 走 offset=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-06` 条）

#### TC-MG-07 R19-GUI-3：耗时列不再把排队等待算两遍

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：R19-GUI-3：耗时列不再把排队等待算两遍
- **输入与前置条件**：editor.html 脚本
- **执行步骤**：静态取证新旧口径串
- **期望结果**：新口径在位、旧口径无残留、与 running 行同源
- **实际结果**：终态新口径=True 旧双算口径残留=False running 同源=True
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-07` 条）

#### TC-MG-08 R19-GUI-1/4：SRCH 不被整体重赋值（const 语义可守）

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：R19-GUI-1/4：SRCH 不被整体重赋值（const 语义可守）
- **输入与前置条件**：editor.html 脚本
- **执行步骤**：静态取证 const 声明与重赋值形态
- **期望结果**：SRCH 是 const 且无整体重赋值
- **实际结果**：SRCH 为 const=True 存在整体重赋值=False token input 监听=False
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：异常输入；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-08` 条）

#### TC-MG-09 浏览器人工走查：编辑/冲突/回滚/审计四链路（受限）

- **所属模块**：MG（GUI 审阅 / 编辑静态页）
- **测试目标**：浏览器人工走查：编辑/冲突/回滚/审计四链路（受限）
- **输入与前置条件**：需人工 + 浏览器
- **执行步骤**：—
- **期望结果**：缺口：本机无浏览器 harness，登记待办，不充抵通过
- **实际结果**：本机无浏览器自动化 harness（Wails/webview 侧已知限制见记忆条目），四条交互链路需人工在浏览器中走查；静态面已由 TC-MG-02/03/06/07/08 与 tests/test_ch504_service_faces.py::test_gui1..4 把守，但**交互时序**（防抖、乐观锁 409 提示、回滚后重取）无法静态证明
- **通过状态**：缺口
- **优先级**：P1
- **覆盖指标**：受限缺口；证据 `/tmp/qa-r5/`（探针结果 JSON 的 `TC-MG-09` 条）

### 3.14 MR 回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性）

#### TC-MR-01 门禁套件 0 failed / 0 skipped

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：门禁套件 0 failed / 0 skipped
- **输入与前置条件**：仓库根，`.venv`（Python 3.15.0 / macOS arm64）
- **执行步骤**：`pytest -m gate --junitxml=/tmp/qa-r5/gate_ch504num.xml`
- **期望结果**：junit `failures=0 errors=0 skipped=0`
- **实际结果**：junit `tests=683 failures=0 errors=0 skipped=0 time=39.699`；控制台 `683 passed, 833 deselected in 40.00s`；退出码 0。683 = 上一轮 679 + 本轮新增 `tests/test_ch504_numbering.py` 4 项
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-02 全量套件 0 failed

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：全量套件 0 failed
- **输入与前置条件**：仓库根，`.venv`
- **执行步骤**：`pytest --junitxml=/tmp/qa-r5/full_ch504num.xml`
- **期望结果**：junit `failures=0 errors=0`；skip 均为已登记显形缺口
- **实际结果**：junit `tests=1516 failures=0 errors=0 skipped=16 time=89.767`；控制台 `1500 passed, 10 skipped, 6 xfailed in 89.79s`；退出码 0。1516 = 上一轮 1512 + 4（同上）。16 条 skip 构成不变：2 条 Windows junction 平台门 + 6 条难度骨架 TODO-skip/xfail + 8 条既有环境门
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-03 语句覆盖率不低于上一轮

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：语句覆盖率不低于上一轮
- **输入与前置条件**：`coverage` 已装（`pytest-cov` 未装，故走 `coverage run -m pytest`）
- **执行步骤**：`coverage run -m pytest tests/` 后 `coverage report --include="doc2md/*"`
- **期望结果**：打出值 ≥ 90%，精确值不低于上一轮（CH-503）**90.54%**
- **实际结果**：`TOTAL 7843 stmts / 737 miss` → 打出 **91%**，精确 **90.60%**（(7843-737)/7843）。**基数更正（收口时抓到）**：上一轮（CH-503）为 7804/738 = **90.54%**；本行原写「上一轮 7841/737 = 90.60%」，而 7841 是本轮 PIPE-3 落地前的首跑值、不是上一轮值——沿用了手边的数字而未核对来源，与 R18-DOC-1 同型。相对 CH-503 实为语句数 +39、未覆盖数 −1（PIPE-3 删掉兜底分支、`has_text` 与守卫为新增行），无回归。安全面单列：`doc2md/security/perms.py` **40%**（75 stmts / 45 miss）——与上一轮同，仍是全仓最低
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-04 静态检查全绿

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：静态检查全绿
- **输入与前置条件**：`.venv/bin/ruff`
- **执行步骤**：`ruff check doc2md tests`
- **期望结果**：退出码 0，无告警
- **实际结果**：`All checks passed!`，退出码 0
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-05 语料逐字节回归 22/22

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：语料逐字节回归 22/22
- **输入与前置条件**：`tests/corpus/expected/*.md` 基线
- **执行步骤**：`.venv/bin/python tests/corpus/run_corpus.py`
- **期望结果**：22 passed / 0 failed，退出码 0
- **实际结果**：`22 passed, 0 failed`，退出码 0。**本轮编号修复改了 `s1_parse.py` 的编号解析，22 篇语料逐字节不变**——说明现存语料无一携带 `numFmt="none"` 的标题样式，修复面窄且不触基线
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-06 文档跨引用红线全部可解析

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：文档跨引用红线全部可解析
- **输入与前置条件**：`docs/dev-plan.md`、`docs/technical-reference.md`、`README.md`
- **执行步骤**：`pytest tests/test_ch491_doc_sync.py`
- **期望结果**：7 项全绿；跨引用解析数 ≥ 地板值 40
- **实际结果**：7 项全绿（`test_cross_references_resolve` 实测解析 **51** 处 `文件.py::符号` 形式引用，地板 40）。口径纪律：活文档只允许 `文件.py::符号` 引用（机器可验），不允许 `文件.py:NNN`（行号必然漂移）——仓内仍有 181 处行号式引用未清，登记 **B-50**
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

#### TC-MR-07 版本与编号四处对账

- **所属模块**：MR（回归（套件 / 门禁 / 语料 / 覆盖率 / 文档一致性））
- **测试目标**：版本与编号四处对账
- **输入与前置条件**：`doc2md/__init__.py`、`docs/dev-plan.md`、`README.md`、`pyproject.toml`
- **执行步骤**：`pytest tests/test_ch477_version_sync.py`
- **期望结果**：5 项全绿
- **实际结果**：5 项全绿：`PIPELINE_VERSION` = **2.20.0**（`doc2md/__init__.py` / `pyproject.toml` / `README.md:3` 三处一致）、`PLAN_VERSION` = **3.38**（常量 / header「v3.38」/ §1.4「本版 v3.38」三处一致）、`SCHEMA_VERSION` = 14、`DB_VERSION` = 10、编号「当前至 CH-504／下一条 CH-505」两处一致
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；回归腿；证据 `/tmp/qa-r5/`（junit XML / 覆盖率报告 / runner 日志）

### 3.15 HD 难度语料（独立真值腿）

执行器：`.venv/bin/python tests/corpus/run_hard.py`（退出码 1，`SUMMARY 6/10 难度样本全绿（信息层，未入门禁）`）。真值来自 `tests/corpus/hard/expectations.json`，由生成器 `gen_cases.py` 按构造意图声明，**独立于管线自身输出**——这是它与 `run_corpus.py`（与管线重烤基线做字节比对，只防漂移）的根本区别。

#### TC-HD-01 D01 无线框表格（仅靠对齐推断表结构）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D01 无线框表格（仅靠对齐推断表结构）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：表格识别与列数均符合 `expectations.json` 声明
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-02 D02 跨页表格（41 行 3 列，跨页断裂）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D02 跨页表格（41 行 3 列，跨页断裂）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：FAIL `table[0]: 列数 2 != 3; 行数 37 < 下限 41; 表头 ['序号','科目'] != ['序号','科目','金额']; 裂表：产物 2 张`；FAIL `table 多余: 产物 2 张 > 期望 1 张`；PASS `heading[二、跨页表格测试]: H1`。**整列「金额」脱落 + 4 行丢失 + 裂成两张表，且 `warnings` 为空**——与 W1045 降维账本、文本保真两条既有承诺直接矛盾，按 §2.1 记失败不记缺口。登记 **B-43**（发布阻断）
- **通过状态**：失败
- **优先级**：P0
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-03 D03 合并单元格（DOCX gridSpan / vMerge，4x3）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D03 合并单元格（DOCX gridSpan / vMerge，4x3）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：拍平后交付并留 W1045（本轮 TC-M5-09 独立复证 `warnings` 含 W1045）
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-04 D04 断词连字符（de-hyphenation）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D04 断词连字符（de-hyphenation）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：行尾连字符折词正确还原，不留 `-` 残迹
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-05 D05 点线引导符（目录引导点）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D05 点线引导符（目录引导点）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS `heading[五、点线引导符测试]: H1`；FAIL `contains[第一章 总则]`／`contains[第二章 职责]`／`contains[第三章 附则]` **三条全部缺失**。引导符污染导致整行正文丢失，且零告警——内容损失 + 静默，按 §2.1 记失败。登记 **B-44**（发布阻断）
- **通过状态**：失败
- **优先级**：P0
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-06 D06 数值形态（千分位 / 括号负数，5x3）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D06 数值形态（千分位 / 括号负数，5x3）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：`1,234,567.89` 与 `(123)` 形态原样保真，未被规整
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-07 D07 加密 PDF

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D07 加密 PDF
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：`reject:E4151`、`password_required=True`——拒收且给出可操作原因，不静默产出空文档
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-08 D08 页眉逐页变体（奇偶页不同）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D08 页眉逐页变体（奇偶页不同）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS `absent[奇数页]`／PASS `absent[偶数页]`（页眉确未进正文，本轮 TC-M5-12 独立复证）；FAIL 8× `heading[第 N 节 正文章节标题]: 层级 H2 != 声明 H1`。**剔除面正确，层级面存疑**：生成器声明这些行是 H1，管线判 H2。孰对需人工按真实文档语义裁定（页眉变体被折平后，正文标题的字号众数发生变化，层级判定口径本身待裁）。按 §2.1 记缺口，登记 **B-45**
- **通过状态**：缺口
- **优先级**：P1
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-09 D09 目录（TOC 域）

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D09 目录（TOC 域）
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：PASS：`九、目录域测试` H1、`第一章 总则`／`第二章 职责分工`／`第三章 附则` 均 H1，`contains[本章规定适用范围与术语定义。]` 命中——域结果文本被完整取出
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

#### TC-HD-10 D10 CID 字体无 ToUnicode / Type3 转曲

- **所属模块**：HD（难度语料（独立真值腿））
- **测试目标**：D10 CID 字体无 ToUnicode / Type3 转曲
- **输入与前置条件**：`tests/corpus/hard/` 下对应样本 + `expectations.json` 声明
- **执行步骤**：`.venv/bin/python tests/corpus/run_hard.py`（可加样本代号只跑单条）
- **期望结果**：`expectations.json` 对该样本声明的全部断言通过
- **实际结果**：FAIL `behavior: 期望 reject:E4221，实际转换成功`。生成器声明该样本应当被 S1.5 门禁拒收（无 ToUnicode 的 CID 字体取不到可靠文本层），实际管线判定文本层合格并成功转换。**两种可能都成立**：① 门禁阈值过松（真缺陷）；② 生成器的构造意图与真实扫描件形态不符（夹具问题）。区分二者需要真实无 ToUnicode 样本对照，属受限项，按 §2.1 记缺口，登记 **B-47**
- **通过状态**：缺口
- **优先级**：P1
- **覆盖指标**：异常输入；独立真值腿；证据 `/tmp/qa-r5/hard_ch504num.log`

## 4. 缺口与受限项登记

### 4.1 缺口（计入分母、不计入分子）

| 用例 | 缺口内容 | 受限原因 | 验证方式 | 登记 |
| --- | --- | --- | --- | --- |
| TC-MG-09 | 编辑→冲突→回滚→审计四链路的**交互时序**（防抖、乐观锁 409 提示、回滚后重取） | 本机无浏览器自动化 harness（webview 侧键盘事件与 AX 行为另有已知限制）；B-11 的 CH-496/CH-498 两轮走查是人工在真实 Chromium 上做的，本轮不重复占用该人力 | 人工在浏览器中按四链路走查，逐条记录 | **B-11** |
| TC-HD-08 | 页眉变体折平后正文标题的**层级口径**（生成器声明 H1，管线判 H2） | 孰对需按真实文档语义人工裁定；剔除面已证正确 | 人工对照真实文档裁定层级口径，再决定改判定还是改声明 | **B-45** |
| TC-HD-10 | CID 字体无 ToUnicode 是否**应当**被 S1.5 门禁拒收 | 区分「阈值过松」与「夹具构造意图失真」需真实无 ToUnicode 样本对照 | 取真实扫描件/转曲 PDF 对照，再决定改阈值还是改声明 | **B-47** |

### 4.2 未执行（不计入任何通过率）

| 项 | 内容 | 受限原因 | 验证方式 | 建议顺序 |
| --- | --- | --- | --- | --- |
| L4_install/clean_venv_install | 空 venv + `pip install -e .[dev]` 的干净安装腿 | 需要一个不含本仓依赖的空 venv，属发布前动作；`realtest` 编排器只覆盖本机可重复子集，不冒充整层 | 发布前在干净 venv 中执行安装并跑门禁 | 发布前第 1 位 |
| L5_quality/real_leg | 真实样本腿（`work/real_samples`） | 目录为空，无真实用户样本 | 投入真实样本后执行 `realtest --only L5` | 发布前第 2 位（依赖外部样本） |

另：Windows junction 平台门（2 条 skip）与难度骨架 TODO-skip/xfail（6 条）在 §3.14 TC-MR-02 的 16 条 skip 中，均为既有显形缺口，不以「跳过」充抵通过。

## 5. 证据索引

| 文件 | 内容 |
| --- | --- |
| `/tmp/qa-r5/r4_api.py` · `api_results.json` | API 面 45 项探针脚本与逐条十字段结果 |
| `/tmp/qa-r5/r4_cli.py` · `cli_results.json` | CLI/管线/运维面 44 项 |
| `/tmp/qa-r5/r4_struct.py` · `struct_results.json` | 结构/分片/渲染面 22 项 |
| `/tmp/qa-r5/r4_gui.py` · `gui_results.json` | GUI 面 9 项 |
| `/tmp/qa-r5/gate_ch504num.xml` · `.log` | 门禁套件 junit 与控制台输出（683/0/0/0） |
| `/tmp/qa-r5/full_ch504num.xml` · `.log` | 全量套件 junit 与控制台输出（1516/0/0/16） |
| `/tmp/qa-r5/cov_ch504num.log` | 覆盖率运行日志（`coverage report --include="doc2md/*"` → 7843/737） |
| `/tmp/qa-r5/corpus_ch504num.log` | 语料逐字节回归（22/22） |
| `/tmp/qa-r5/metrics_ch504num.log` | ideal 真值指标（标题 micro 召回 1.0、漂移 F1 1.0） |
| `/tmp/qa-r5/eval_ch504num.log` | `tests/qa/evaluate.py` 宏观指标（heading_f1 1.0 / honest 1.0） |
| `/tmp/qa-r5/hard_ch504num.log` | 难度语料逐条（6/10，退出码 1） |
| `/tmp/qa-r5/trace_ch504num.log` | 需求↔用例双向追溯（FR=0.4643 / NFR=0.8182 / W=1.0 / E=1.0） |
| `/tmp/qa-r5/defect_ch504num.log` | 缺陷扫描（blocking 0 / major 2 / minor 2） |
| `/tmp/qa-r5/opt6_ch504num.log` | 分片等价五面取证（1/2/3/4 段均 5789 块、正文 1775484B 等价） |
| `/tmp/qa-r5/realtest_ch504num.log` | `realtest` L0–L5（overall=pass，NOTRUN=2） |
| `/tmp/qa-r5/num_pre.xml` · `num_post.xml` | R19-PIPE-3 修复前（2 failed / 2 passed）与修复后（4 passed） |
| `/tmp/qa-r5/s1_parse.orig` | 变异证明用的修复后原文件副本（md5 `706af6e4af34c86995497f305c0c76de`，三次变异后均按 md5 逐字节还原） |
| `/tmp/qa-r5/tmp/legacyfx/legacy_01.doc` | 本机 LibreOffice 生成的真实 OLE `.doc` 夹具（TC-M2-03 / TC-M5-11 用） |

