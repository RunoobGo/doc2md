# Doc2md 转换管线功能测试文档（第三轮，含执行记录）

- 文档编号：QA-D2M-2026-10-24-R3
- 测试轮次：第三轮全面功能测试（黑盒实测为主 + 源码/套件定位佐证），同时承担第二轮 13 项偏差的复验
- 被测版本：`PIPELINE_VERSION = 2.18.0` / `SCHEMA_VERSION = 14` / `DB_VERSION = 10` / `PLAN_VERSION = 3.34`
- 执行日期：2026-10-24（收口取证跨至 10-25 00:1x）
- 执行环境：macOS arm64、Python 3.15.0（仓库 `.venv`）、10 逻辑 CPU、LibreOffice 可用；转换全程离线，LLM 探针只连 `127.0.0.1:8080`（未启动，用于验证失败/回退路径）
- 证据根目录：`/tmp/qa-r3/`（探针脚本、夹具、产物、套件/覆盖率 XML 全部保留，可逐条复核；见 五、证据索引）
- 上一轮基线：`docs/qa/round2/test-plan.md`（QA-D2M-2026-10-23-R2，80 例 / 13 失败）；本轮不覆盖上一轮文档
- 本轮修复来源：第 17 轮系统审查 CH-500（`docs/review-round17-CH-500.md`），新增红线 `tests/test_ch500_round17.py` 38 项
- 配套文档：《对照表》（`comparison.md`）、《偏差归纳与根因》（`deviation-analysis.md`）、《优化方案》（`optimization-plan.md`）、《修复复验》（`fix-verification.md`）

---

## 1. 测试范围与模块划分

被测链路：`输入识别 → S1 解析 → S1.5 门禁 → S2 分片/缝合 → S3 结构映射 → S4 渲染 → LLM 修复 → 打包 → 存储/检索 → CLI/API/GUI/运维出口`。

| 模块 ID | 模块 | 对应源码 | 用例数 | 通过 | 缺口 | 失败 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| M1 | 输入识别与前置校验 | `pipeline/s1_parse.py::sniff`、`security/names.py`、`security/dirguard.py` | 7 | 7 | 0 | 0 |
| M2 | S1 解析（PDF/DOCX/legacy .doc） | `pipeline/s1_parse.py`、`pipeline/legacy_doc.py` | 8 | 7 | 1 | 0 |
| M3 | S1.5 文本层门禁 | `pipeline/s1_5_gate.py` | 4 | 3 | 1 | 0 |
| M4 | S2 分片、缝合与确定性 | `pipeline/sharding.py`、`pipeline/convert.py` | 4 | 4 | 0 | 0 |
| M5 | S3 结构映射（七类块） | `pipeline/s3_structure.py` | 7 | 7 | 0 | 0 |
| M6 | S4 Markdown 渲染与安全红线 | `pipeline/s4_render.py` | 12 | 12 | 0 | 0 |
| M7 | LLM 修复、回退与 op 契约 | `llm.py`、`pipeline/convert.py::repair_ops` | 4 | 3 | 1 | 0 |
| M8 | 打包与产物形态 | `pipeline/convert.py::package`、`artifacts.py` | 5 | 5 | 0 | 0 |
| M9 | 存储、检索与库操作 | `storage/`、`cli.py`（search/docs/backup）、`ops/` | 4 | 4 | 0 | 0 |
| M10 | API 服务、权限、分页与编辑锁 | `api.py`、`auth.py`、`edits.py`、`events.py` | 20 | 20 | 0 | 0 |
| M11 | CLI 批量、退出码与格式出口 | `cli.py`、`pipeline/batch.py`、`__main__.py` | 7 | 7 | 0 | 0 |
| M12 | 运维、性能与可观测性 | `ops/`、`worker.py`、`cron.py`、`scheduler.py` | 6 | 6 | 0 | 0 |
| MR | 回归（套件/门禁/语料/覆盖率/文档一致性） | `tests/`、`docs/`、`README.md`、`pyproject.toml` | 7 | 7 | 0 | 0 |
| HD | 难度语料（独立真值腿，本轮新增） | `tests/corpus/hard/`、`tests/corpus/run_hard.py`、`tests/qa/gen_cases.py` | 7 | 4 | 1 | 2 |
| **核心合计（M1–MR）** |  |  | **95** | **92** | **3** | **0** |
| **本轮合计（含 HD 腿）** |  |  | **102** | **96** | **4** | **2** |

## 2. 统计口径与判定标准

### 2.1 用例状态

| 状态 | 判定 |
| --- | --- |
| **通过** | 实际结果与期望结果在**状态、错误码、退出码、产物结构、关键文本/字节**上全部一致；性能用例同时满足时延与内存阈值 |
| **失败** | 任一可观测断言不一致，且该行为与既有契约/文档承诺相矛盾即记失败；影响等级为「低」也不改记为通过 |
| **缺口** | 实际行为**与当前文档口径一致**，但存在用户可感知的能力缺失或契约决策未裁定（本轮 4 项全部登记 B 类待办：核心面 B-32/B-33/B-42，难度腿 B-45）。缺口计入分母、不计入分子，即同样拉低通过率 |
| **未执行** | 因平台/环境无法执行；不计入通过率，也不以「跳过」充抵通过（本轮 2 项：Windows junction、远端 CI 腿，见 4.2） |

引入「缺口」这一档是本轮与第二轮的口径差异：第二轮把 13 项全部记为失败，其中若干项本质是「能力未实现」。本轮按可观测契约判定，缺陷与缺口分开登记，避免把产品决策问题混成回归问题——两者都必须闭合，但闭合路径不同。**分档纪律在本轮被反向使用过一次**：难度腿首两条候选（TC-HD-02 / TC-HD-04）起初按「需人工裁定阈值」记成缺口，复核既有承诺（`W1045` 降维账本 + 文本保真）后改判失败——「修复要靠外部证据」不是把缺陷降级为缺口的理由，只有「行为与文档口径一致」才是。

本轮 102 条用例（核心 95 + 难度腿 7）全部执行，无「未执行」项进入用例表。

### 2.2 优先级

| 优先级 | 定义 |
| --- | --- |
| **P0** | 核心转换正确性、数据完整性、权限边界、安全边界、失败必须可见；失败即阻断发布 |
| **P1** | 边界/异常输入、API 契约与分页口径、存储检索、批处理、性能基线；失败需在发布前修复或书面豁免 |
| **P2** | 可观测性、文档一致性、结果行展示、GUI 静态页可用等非阻断项；可排入后续迭代，但必须登记 |

### 2.3 通过率与覆盖率公式

- 用例通过率 = 通过用例数 ÷ 已执行用例数 × 100%。**分母先声明再算**（本轮 O-c 纪律，避免混比）：
  - 核心面（M1–MR，真值来自契约与文档口径）= 92 ÷ 95 = **96.84%**；
  - 难度腿（HD，真值来自生成器声明的构造意图）= 4 ÷ 7 = **57.14%**；
  - 合并 = 96 ÷ 102 = **94.12%**（下表与发布门槛一律按合并口径，核心口径同列以便与第二轮 80 例对照）。
- 优先级通过率（合并口径，优先级分布 P0/P1/P2 = 38/53/11）：P0 = 37 ÷ 38 = **97.37%**（核心 34/34 = 100%，难度腿 TC-HD-02 为 P0 失败）；P1 = 48 ÷ 53 = **90.57%**（核心 47/50 = 94.00%，难度腿 1 通过 / 1 失败 / 1 缺口）；P2 = 11 ÷ 11 = **100%**。
- 模块覆盖率 = 有用例的模块数 ÷ 计划模块数 × 100%；本轮 **14 ÷ 14 = 100%**（13 个链路模块 + HD 独立真值腿；模块均有用例不等于需求全覆盖）。
- 错误码覆盖率 = 已实测触发的契约错误码数 ÷ 契约错误码总数 × 100%；契约码以 `doc2md/errors.py::CODE_INDEX` 的 14 项为准；本轮 **14 ÷ 14 = 100%**（E4001/E4003/E4011/E4031/E4041/E4091/E4092/E4131/E4133/E4151/E4221/E4251/E5031/E5041；E5000 为兜底基类，不计入契约码，但 500 面另记 TC-M10-12）。
- 告警覆盖率 = 已实测触发的告警码数 ÷ 告警码总数 × 100%；本轮 **4 ÷ 4 = 100%**（W1043、W1044、W1045、W1046）。
- 语句覆盖率 = `coverage.py` 全量套件统计（`doc2md/` 口径）；本轮收口复跑 `coverage report` 打出 **91%**（精确 **90.51%**：7797 条语句 / 740 条未覆盖）。轮中同一指标的 7800/743 打出的是 **90%**（精确 90.47%），两档之间没有真实回归，只差一个 .5 取整边界——**引用覆盖率须同时给出打出值与精确值**（见 O-h）。安全面单列：`doc2md/security/perms.py` **40%**（75 条语句，45 条未覆盖）。
- 语料覆盖率 = 语料 harness 通过数 ÷ 语料总数；本轮 **22 ÷ 22 = 100%**。
- 探针用例规模：API 面 49 项、管线面 32 项、扩展面（打包/存储/LLM/CLI/运维）30 项，合计 **111 项**；本轮核心 95 条用例由这 111 项实测与套件/性能取证归并而成，难度腿 7 条另计，执行器为 `tests/corpus/run_hard.py`（不属 111 项探针）。

### 2.4 发布门槛（测试前设定）

| 指标 | 门槛 | 核心 95 例（测试前口径） | 含难度腿 102 例（收口口径） | 结论 |
| --- | --- | --- | --- | --- |
| P0 用例通过率 | 100% | 34/34 = 100% | 37/38 = **97.37%** | 核心达标；**合并未达标**（TC-HD-02） |
| P1 用例通过率 | ≥ 95% | 47/50 = 94.00% | 48/53 = **90.57%** | **未达标**（两口径均未达标） |
| P2 用例通过率 | ≥ 90% | 11/11 = 100% | 11/11 = 100% | 达标 |
| 总体用例通过率 | ≥ 95% | 92/95 = 96.84% | 96/102 = **94.12%** | 核心达标；**合并未达标** |
| 契约缺陷数 | 0 | 0 | **2**（TC-HD-02 静默掉列、TC-HD-04 引导符污染 + 塌词） | **未达标** |
| 全量自动化套件 | 0 failed | — | 1382 passed / 8 skipped（collected 1390）；8 条 skip 是已登记的显形缺口（2 Windows junction 平台门 + 6 难度骨架 TODO-skip/xfail），非新增失败 | 达标 |
| 门禁套件 | 0 failed | — | 580 passed / 0 skipped | 达标 |
| 语料 harness | 22/22 | 22/22 | 22/22 | 达标 |
| **难度语料 harness**（本轮新增） | 收口时设定为「逐条如实报告、不充抵」 | — | **4/7**（`run_hard.py` 退 1） | **未达标（2 失败 + 1 缺口）** |
| 语句覆盖率 | ≥ 90% | — | 打出 91% / 精确 90.51%（`doc2md/` 口径 7797 stmts / 740 miss） | 达标（贴线，距 92% 目标 1.5 个百分点） |
| 错误码 / 告警覆盖率 | ≥ 90% | 100% / 100% | 100% / 100% | 达标 |

结论（收口口径已改判，测试前口径同列以便对照）：

1. **第二轮 13 项偏差与 CH-500 的 18 项修复全部复验为通过，核心面 0 契约缺陷**——这一条不变。
2. **但本轮新增的独立真值腿推翻了「本轮不存在契约缺陷」**：D02 跨页表整列脱落、D05 目录引导符污染并塌词，两条都是**内容损失 + 零告警**，直接违背 `W1045` 降维账本与文本保真两条既有承诺，按 2.1 记失败不记缺口。契约缺陷数因此从 0 变 **2**，P0 通过率从 100% 变 **97.37%**。
3. **发布判定随之改变**：按 2.2「P0 失败即阻断发布」，工程侧不能再出具「不阻断发布」的结论。B-43 / B-44 必须在发布前闭合，或由人工逐页对照真实 PDF 复核后出具书面豁免——两者都需要人工介入，属受限项，本轮只登记不实施（纪律见 `docs/review-round17-CH-500.md` 五、）。
4. 三项核心缺口（B-32 / B-33 / B-42）仍需人工裁定或外部样本，另加难度腿的 B-45（层级口径裁定）与 B-46（结构断言入门禁，前置 B-43/B-44），见 4.1 与《优化方案》。

## 3. 用例全集与逐条执行结果

说明：每条用例的「实际结果」均来自本轮实测原文（探针日志摘录，未做美化）；「套件佐证」表示由全量 pytest 套件中的具体测试覆盖，套件整体结果见 TC-MR-01/02。命令均在仓库根目录执行，`doc2md` 指 `.venv/bin/doc2md`。三个探针脚本为 `probe_api_v.py` / `probe_pipe.py` / `probe_extra.py`，各自重建独立 `--root`，互不串库。

### 3.1 M1 输入识别与前置校验

#### TC-M1-01 损坏 ZIP 的诊断分类（容器损坏 ≠ 类型不支持）

- **所属模块**：M1
- **测试目标**：ZIP magic 合法但内容截断的 `.docx` 必须诊断为「损坏 ZIP」，不得说成「类型不支持」（CH-500 R17-PIPE-3）。
- **输入与前置条件**：`tmp/qa-r3/verify/trunc.docx`（正常 docx 截断尾部字节）。
- **执行步骤**：`doc2md convert trunc.docx --out v01 --json`，读结果行 `code/message/detail`。
- **期望结果**：E4151、退出码 4、`detail.reason=corrupt_zip`、message 明确「damaged ZIP container」。
- **实际结果**：`rc=4`、`{"status":"error","code":"E4151","message":"trunc.docx is a damaged ZIP container, not a readable .docx: BadZipFile","detail":{"file":"trunc.docx","magic":"504b030414000000","reason":…}}`（V-PIPE-01）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常流；`sniff`/`_docx_zip_diagnosis` 三分支之一；第二轮无此分类；证据 `pipe_probe8.log`。

#### TC-M1-02 ZIP 容器但缺必备部件（xlsx/epub 冒充 .docx）

- **所属模块**：M1
- **测试目标**：ZIP magic 合法、无 `word/document.xml` 时诊断为「不是 docx」，与 TC-M1-01 文案不同。
- **输入与前置条件**：`notdocx.docx`（普通 zip 改名）。
- **执行步骤**：`doc2md convert notdocx.docx --out v02 --json`。
- **期望结果**：E4151、rc=4、`detail.reason=not_a_docx`、message 提示 xlsx/epub/odt 不支持。
- **实际结果**：`rc=4`、`"notdocx.docx is a ZIP container but has no word/document.xml (not a .docx — xlsx/epub/odt are not supported)"`（V-PIPE-02）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常流；诊断分支二；证据 `pipe_probe8.log`。

#### TC-M1-03 容器不可读（权限）与三结局文案互斥

- **所属模块**：M1
- **测试目标**：三条 ZIP 结局（损坏 / 非 docx / 不可读）message 两两不同；`cannot read` 分支端到端可观测。
- **输入与前置条件**：`unread.docx` 置 `chmod 000`；另备一份仅 ZIP 头损坏的文件作对照。
- **执行步骤**：1. `doc2md convert unread.docx --out v03 --json`；2. 比对三条 message 是否两两不同。
- **期望结果**：E4151、rc=4、message 前缀 `cannot read`；三结局 message 两两不同。
- **实际结果**：`rc=4`、`"cannot read unread.docx: [Errno 13] Permission denied"`；`V-PIPE-03b` 三条 message 两两不同（V-PIPE-03/03b）。补充：`detail.reason=zip_unreadable`（先打开后读头）分支在本机黑盒不可达——`chmod 000` 时 8 字节头读取先抛权限错误，故该分支由白盒红线钉住（`tests/test_ch500_round17.py`），已如实登记而非记为黑盒覆盖。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常流；诊断分支三 + 文案互斥回归；证据 `pipe_probe8.log`、`tests/test_ch500_round17.py`。

#### TC-M1-04 消毒后仍不安全的文件名 → E4003

- **所属模块**：M1
- **测试目标**：文件名消毒后仍触碰 Windows 保留名红线时必须明确失败（第二轮记为「未构造成功」的错误码缺口）。
- **输入与前置条件**：`con.<130 个 x>.docx`（win-reserved 前缀 + 超长）。
- **执行步骤**：`doc2md convert 'conxxx.docx' --out v04 --json`。
- **期望结果**：E4003、rc=4、message 带 `win-reserved` 与消毒后名。
- **实际结果**：`rc=4`、`{"code":"E4003","message":"unsafe name 'con.xxxxx…': win-reserved","file":…}`（V-PIPE-04）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；`security/names.py`；补齐第二轮 4.1 登记的 E4003 缺口；证据 `pipe_probe8.log`。

#### TC-M1-05 加密 PDF → 明确失败而非空产物

- **所属模块**：M1
- **测试目标**：需要口令的 PDF 必须失败并标注口令原因，不得产出空 Markdown 冒充成功。
- **输入与前置条件**：`enc.pdf`（`pypdf` 以口令加密写入的 PDF）。
- **执行步骤**：`doc2md convert enc.pdf --out x16 --json`。
- **期望结果**：rc≠0、E4151、`detail.password_required=true`。
- **实际结果**：`rc=4`、`{"code":"E4151","message":"unreadable pdf: enc.pdf","detail":{"file":"enc.pdf","exc":"PdfminerException","password_required":true}}`（X-16）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常流；明确失败优先于看似成功；证据 `extra_probe5.log`。

#### TC-M1-06 零字节文件 → magic 为空仍归类型不支持

- **所属模块**：M1
- **测试目标**：空文件的 `sniff` 不得崩栈，须给出带 magic 的 E4151。
- **输入与前置条件**：`zero.pdf`（0 字节）。
- **执行步骤**：`doc2md convert zero.pdf --out x16b --json`。
- **期望结果**：E4151、rc=4、`detail.magic=""`。
- **实际结果**：`rc=4`、`"unsupported magic for zero.pdf"`、`detail={"file":"zero.pdf","magic":""}`（X-16b）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；空输入；证据 `extra_probe5.log`。

#### TC-M1-07 内容寻址稳定性与 `--skip-existing`

- **所属模块**：M1
- **测试目标**：`doc_id` 由内容哈希决定；同内容重复入库时跳过而非重转。
- **输入与前置条件**：`tests/corpus/sources/docx_09_mixed_cn_en.docx` 已入 `x17root`。
- **执行步骤**：`doc2md convert docx_09_mixed_cn_en.docx --root x17root --skip-existing --json`。
- **期望结果**：`status=skipped`、`doc_id` 与库内一致、不新增产物。
- **实际结果**：`row={'file':'docx_09_mixed_cn_en.docx','status':'skipped','doc_id':'5250ce38eff3c27e','blocks':0,'chars':0,'zip':''}`（X-17b）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；IR 约定「内容寻址」；证据 `extra_probe5.log`。

### 3.2 M2 S1 解析（PDF / DOCX / legacy .doc）

#### TC-M2-01 文本型 PDF 解析基线

- **所属模块**：M2
- **测试目标**：单页文本 PDF 走通 S1，块数/字符数与第二轮基线一致，无回归。
- **输入与前置条件**：`tests/corpus/sources/pdf_01_simple_1p.pdf`。
- **执行步骤**：连续 3 次 `doc2md convert … --out perf/1p_pdf_{1,2,3}`，取墙钟中位数。
- **期望结果**：`doc_id=a545becc4d31a988`、`blocks=4`、rc=0；时延 ≤ 第二轮基线量级。
- **实际结果**：`rc=0`、中位 `0.158 s`（三次 0.192/0.158/0.141）、子进程峰值 RSS 60.9 MB；库内块长 16/6/23/25 字符，`doc_id=a545becc4d31a988`（与第二轮一致）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流 + 性能基线；`sniff` PDF 分支；证据 `perf_probe.log`、`rss_probe.log`。

#### TC-M2-02 大 DOCX 全链路（30 章节）

- **所属模块**：M2
- **测试目标**：ZIP+OOXML 大文档解析、入库、可检索，产物三成员齐备。
- **输入与前置条件**：`docx_08_large_30sections.docx`；`x*root` 内经 `work --enqueue` + `--once` 入库。
- **执行步骤**：1. `doc2md work --root … --enqueue docx_08…`；2. `doc2md work --root … --once`；3. 解包 zip 校验成员；4. 读 `GET /docs` 行。
- **期望结果**：rc=0；`content.md`/`ir.json`/`report.html` 三成员；`/docs` 行带 `blocks` 计数与稳定排序字段。
- **实际结果**：`rc=0`、中位 `0.130 s`；成员 `['content.md','ir.json','report.html']`、front-matter 含 `pipeline_version 2.18.0`（X-11）；`GET /docs` 返回 `total=5 rows=5`，行键含 `blocks/created_at/doc_id/format/pages/profile/shard_count/sharded/source_key/source_name`（V-API-01/01b）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；DOCX 分支 + 存储入库 + 打包；证据 `extra_probe5.log`、`api_probe5.log`。

#### TC-M2-03 旋转页识别与 W1046 降维告警

- **所属模块**：M2
- **测试目标**：`/Rotate 90` 页必须触发 W1046，并在 `layout_probe` 中精确到页号（第二轮记为「未构造夹具」的告警缺口）。
- **输入与前置条件**：`rotated_page.pdf`（第 2 页旋转 90°）。
- **执行步骤**：`doc2md convert rotated_page.pdf --json` → 读 `ir.json` 的 `warnings` 与 `layout_probe`。
- **期望结果**：`warnings` 含 W1046；`layout_probe.rotated_pages=[2]`；`rtl_pages` 为空。
- **实际结果**：`warnings=['W1046']`、`layout_probe={'rotated_pages':[2],'rtl_pages':[]}`（V-PIPE-06 rot）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：告警覆盖 W1046；补齐第二轮缺口；证据 `pipe_probe8.log`。

#### TC-M2-04 RTL 版式识别与 W1046

- **所属模块**：M2
- **测试目标**：希伯来语 RTL 页触发 W1046 且不误报旋转页。
- **输入与前置条件**：`rtl_hebrew.pdf`（第 1 页 RTL）。
- **执行步骤**：同 TC-M2-03。
- **期望结果**：`warnings` 含 W1046；`layout_probe.rtl_pages=[1]`；`rotated_pages` 为空。
- **实际结果**：`warnings=['W1046']`、`layout_probe={'rotated_pages':[],'rtl_pages':[1]}`（V-PIPE-06 rtl）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：告警覆盖 W1046；RTL 阈值 N-5 仍未校准（见 4.2）；证据 `pipe_probe8.log`。

#### TC-M2-05 去页家具后旋转元数据不被归零（潜伏缺陷复验）

- **所属模块**：M2
- **测试目标**：`_strip_page_furniture` 重建页面时必须保留 `rotation` 字段（CH-500 R17-PIPE-5/AR-7 系）。
- **输入与前置条件**：同 TC-M2-03 夹具的 `RawPage` 对象。
- **执行步骤**：白盒取 `strip` 前后逐页 `(index, rotation, …)` 三元组并比较。
- **期望结果**：`strip` 后旋转页仍为 90°，不归零。
- **实际结果**：`before=[(0,0,0.0),(1,90,0.0),(2,0,0.0)]`、`after=[(0,0,0.0),(1,90,0.0),(2,0,0.0)]`——逐页一致，rotation 未被吃掉（V-PIPE-07）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；`pipeline/s1_parse.py`/`s3_structure.py::_strip_page_furniture`；证据 `pipe_probe8.log`、`tests/test_ch500_round17.py`。

#### TC-M2-06 DOCX 行字号不受 run 顺序影响（加权众数）

- **所属模块**：M2
- **测试目标**：同一行内「22pt 标题 run + 10pt 正文 run」无论谁在前，行字号判定必须一致（CH-500 R17-PIPE-6，标题级别误判根因）。
- **输入与前置条件**：自建 docx，同一行两个 run：`附录正文…`（10.5/22pt 混排）与换序对照件。
- **执行步骤**：分别转换两种换序文件，读 IR 中该行的 `type` 与 `font`。
- **期望结果**：两次行字号相同，取加权众数（=10.0）而非首个 run 的字号。
- **实际结果**：`22pt在前` → `type=paragraph font={'family':'serif','size':10.0,'bold':True}`；`10pt在前` → 同样 `size:10.0`（V-PIPE-08 双跑），换序不再左右判定。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正确性；S1 字号聚合；证据 `pipe_probe8.log`。

#### TC-M2-07 短 pair 换序后结构判定同构

- **所属模块**：M2
- **测试目标**：短文本 run 对（头尾长度接近）换序后，S1/S3 给出的块类型与顺序保持同构，避免「换序即换级」。
- **输入与前置条件**：`短头内容内容内容` / `附录C短尾内容内` 两 run 的两个换序版本。
- **执行步骤**：转换两版，比较 `blocks` 的 `(type, level, 前 7 字)` 序列。
- **期望结果**：两版块类型序列一致（均判 paragraph，不误升标题）。
- **实际结果**：`blocks=[('paragraph',None,'附录C短尾内容内'),('paragraph',None,'短头内容内容内容'),('paragraph',None,'正文说明段落：这')]`——加权众数对称，无标题晋升（V-PIPE-09）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；标题候选判定稳定性；证据 `pipe_probe8.log`。

#### TC-M2-08 DOCX 页眉页脚内容是否进入产物

- **所属模块**：M2
- **测试目标**：确认页眉/页脚正文在产物中的处置口径，并检验用户可感知内容是否丢失。
- **输入与前置条件**：自建 docx，页眉写 `HEADER-MARKER-DOC2MD-QA3`、页脚写 `FOOTER-MARKER-DOC2MD-QA3`。
- **执行步骤**：`doc2md convert … --json` → 在 `content.md` 统计两处 marker 命中数，并读 `warnings`。
- **期望结果**：按当前实现口径——页眉页脚**不采集**，产物不含 marker，且不产生虚假成功告警；用户可感知内容缺失需以告警或文档明示。
- **实际结果**：`header_hits=0 footer_hits=0 warnings=[] blocks=2`（V-PIPE-16）。行为与实现口径一致，但没有任何告警提示「页眉页脚内容未进入产物」，用户视角等同内容静默丢失。
- **通过状态**：**缺口**（登记 B-33：需先立 `W1047` 降维告警码 + ADR，「并入正文」还是「逐页出现」属产品决策；语料重烤需人工审阅 11 篇 DOCX 产物）
- **优先级**：P1
- **覆盖指标**：功能完整性；`pipeline/s1_parse.py` DOCX 部件遍历；建议顺序：高；证据 `pipe_probe8.log`、`docs/review-round17-CH-500.md` 五、B-33。

### 3.3 M3 S1.5 文本层门禁

#### TC-M3-01 无文本层扫描件拒收

- **所属模块**：M3
- **测试目标**：纯图像 PDF 必须被门禁拒收并给出可定位原因，不得产出空正文。
- **输入与前置条件**：`tests/corpus/sources/pdf_06_scanned_1p.pdf`。
- **执行步骤**：`doc2md convert pdf_06_scanned_1p.pdf --json`。
- **期望结果**：`status=rejected`、E4221、rc=3、`gate.reasons` 非空。
- **实际结果**：`rc=3`、`{"file":"pdf_06_scanned_1p.pdf","status":"rejected","code":"E4221","gate":{"mode":"auto","sampled_pages":[1],"reasons":["all_pages_empty"],"stats":{"total_pages":1,…}}}`（X-16c）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常流；门禁主判定；证据 `extra_probe5.log`。

#### TC-M3-02 `auto` 档是否尊重 `reject_on_any_bad_page`

- **所属模块**：M3
- **测试目标**：确认配置键在默认档下是否真正生效（CH-500 结论「判定必须长在交付路径上」的门键面）。
- **输入与前置条件**：`doc2md.toml` 设 `[s1_5] mode="auto"` + 单页稀字夹具（page 1 chars=37）。
- **执行步骤**：分别在 `reject_on_any_bad_page=true/false` 下转换，比较 `status` 与 `gate.reasons`。
- **期望结果**：键生效则 true→拒收；不生效则两种取值行为相同，**必须在文档中明示该键在 auto 档被忽略**。
- **实际结果**：`auto_on=(True,[])`、`auto_off=(True,[])`——auto 档两种取值行为相同，键确实不参与判定；`even5_on=(False,['page 1: chars=37 < 120'])` 证明判定本身可用（V-PIPE-10）。文档口径已在 CH-500 更正，但键的存废属产品决策。
- **通过状态**：**缺口**（登记 B-32：默认档尊重该键会改变线上误拒率，本机 22 篇语料样本不足以评估误杀面；需外部语料 + 人工抽检拒收清单；CH-501 裁定键存废）
- **优先级**：P1
- **覆盖指标**：配置有效性；`pipeline/s1_5_gate.py`；建议顺序：中；证据 `pipe_probe8.log`。

#### TC-M3-03 采样档（even-5）拒收判定与开关可观测

- **所属模块**：M3
- **测试目标**：非 auto 档下门禁逐页阈值判定生效，且拒收原因可定位到页。
- **输入与前置条件**：同 TC-M3-02 夹具，`mode="even5"`。
- **执行步骤**：`reject_on_any_bad_page` 取 true / false 各跑一次。
- **期望结果**：true → `status≠ok` 且 `reasons` 含页号与字符数；false → 放行。
- **实际结果**：true → `(False, ['page 1: chars=37 < 120'])`；false → `(True, [])`（V-PIPE-10）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流 + 开关分支；证据 `pipe_probe8.log`。

#### TC-M3-04 门禁摘要进入审阅报告

- **所属模块**：M3
- **测试目标**：`report.html` 必须可见门禁结论，否则运维只能读 JSON 排障。
- **输入与前置条件**：任一成功转换产物。
- **执行步骤**：解包 zip 读 `report.html`，检索区块标题。
- **期望结果**：含 S1.5 gate、LLM、Quality 三个摘要区块。
- **实际结果**：`sections=['S1.5 gate','LLM','Quality']`（X-11b）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：可观测性；证据 `extra_probe5.log`。

### 3.4 M4 S2 分片、缝合与确定性

#### TC-M4-01 三片缝合：交付元数据不得携带片内锚

- **所属模块**：M4
- **测试目标**：多片文档的 `ir.json` meta 不应把「第 0 片的起止页」当作文档级元数据出门（CH-500 R17-PIPE-4），同时跨片告警齐备。
- **输入与前置条件**：`table_across_pages.pdf` + `[s2] max_pages_per_shard=1`（3 片）。
- **执行步骤**：转换后读 `meta` 的键集合、`warnings`、`shard_count`。
- **期望结果**：meta 无 `shard` 内部锚键；`warnings` 含 W1043/W1044；`shard_count=3`。
- **实际结果**：`keys=['doc_id','format','pages','pipeline_version','schema_version','shard_count','sharded','source_key','source_name','warnings']`、`warnings=['W1043','W1044']`、`shard_count=3`，无 `shard` 键泄漏（V-PIPE-05）。第二轮 P05 实测的 `meta["shard"]={'index':0,…}` 泄漏已闭合。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正确性 + 告警 W1043/W1044；补齐第二轮告警缺口；证据 `pipe_probe8.log`。

#### TC-M4-02 跨片表格：不合并也不重复

- **所属模块**：M4
- **测试目标**：缝合线两侧的行必须各出现恰好一次（既不合并成一张表，也不因重叠窗口重复投放）。
- **输入与前置条件**：同 TC-M4-01 夹具（3 页、每页 27 行的表格，行首标记 `p0r0alpha`/`p1r0alpha`/`p2r0alpha`）。
- **执行步骤**：`grep -c` 三个行首标记在 `content.md` 中的出现次数，并统计表块数量。
- **期望结果**：每个标记恰好 1 次；产出 3 张独立 GFM 表。
- **实际结果**：`counts=1/1/1 tables=3`（V-PIPE-05b）；与第二轮 P05 的 `stitched=0`、`dropped_dupes=6`（重叠文本行被去重）一致。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：数据完整性；分片缝合；证据 `pipe_probe8.log`。

#### TC-M4-03 确定性与重复转换稳定

- **所属模块**：M4
- **测试目标**：同一输入多次转换得到同一 `doc_id`、同一块数与同一正文首屏字节。
- **输入与前置条件**：`docx_01_simple.docx` 三次入 `aroot`；另有 `--format both` 双产物对照。
- **执行步骤**：1. 重复 `work --enqueue/--once`；2. `--format both` 比对 `md` 文件与 zip 内 `content.md` 字节数。
- **期望结果**：`doc_id` 稳定（`0ce7cddb6afbc150`）；md 与 zip 内正文等价。
- **实际结果**：`doc_id=0ce7cddb6afbc150`、`blocks=9`、`chars=85` 多次一致；`--format both` → `md=docx_01_simple.docx.md bytes=661 zip_md=661`（X-10、X-14b）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：确定性/幂等；证据 `extra_probe5.log`、`api_probe5.log`。

#### TC-M4-04 页区间并行收益（320 页金样本）

- **所属模块**：M4
- **测试目标**：量化 S1 页区间并行的实际加速，并确认输出规模不随并行度改变。
- **输入与前置条件**：`tests/golden/large_320p.pdf`；`workers ∈ {1,2,3}`；10 逻辑 CPU。
- **执行步骤**：三档各跑一遍，记录墙钟、内部耗时之和、产出行数。
- **期望结果**：随 workers 上升墙钟下降；产出行数逐档一致（15613 行）；本轮**不**据此发布并行开关。
- **实际结果**：`1/2/3 workers` → 墙钟 `10.04 / 5.65 / 3.80 s`，加速 `1.00× / 1.78× / 2.64×`，行数恒为 15613、页数恒 320；内部耗时之和 10.04→10.51→10.35 s（并行开销 ≈0～5%）。
- **通过状态**：通过（收益成立）；但**等价性尚未证明**：跨页折行、全文档 `modal_size`、门禁全量判定三处跨段依赖未逐项验证，故并行未进产品开关。
- **优先级**：P1
- **覆盖指标**：性能；B-38 前置条件；建议顺序：高（本轮性能结论的唯一大杠杆）；证据 `perf_probe.log`、`docs/review-round17-CH-500.md` 五、B-38。

### 3.5 M5 S3 结构映射（七类块）

#### TC-M5-01 大号提示文字不得晋升为标题

- **所属模块**：M5
- **测试目标**：仅凭字号（14pt）不足以判定标题，必须与编号/粗体/短行等信号联合（第二轮 D7）。
- **输入与前置条件**：自建 docx，三段正文夹一段 14pt 提示文字。
- **执行步骤**：转换后读块的 `(type, 前 12 字)`。
- **实际结果**：`blocks=[('paragraph',None,'这是普通正文段落，用于把'),('paragraph',None,'以下一行是 14pt 的'),('paragraph',None,'收尾正文段落，同样保持 ')]`——三段全为 paragraph，无误晋升（X-01）。
- **期望结果**：14pt 单信号不晋升标题。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：第二轮 D7 复验；`pipeline/s3_structure.py`；证据 `extra_probe5.log`。

#### TC-M5-02 编号 + 字号组合的层级判定稳定

- **所属模块**：M5
- **测试目标**：编号前缀与字号联合时应给出稳定层级。
- **输入与前置条件**：`1. 10.5pt 编号与正文…` + `结语`（14pt 编号）。
- **执行步骤**：转换后读 `headings` 的 `(level, 前 12 字)`。
- **期望结果**：一级/二级层次稳定，不因字号单一信号翻转。
- **实际结果**：`headings=[(1,'1. 10.5pt 编号与正文测'),(2,'结语')] n=2`（X-02）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流 + 边界；证据 `extra_probe5.log`。

#### TC-M5-03 多类块类型同时产出

- **所属模块**：M5
- **测试目标**：代码块、引用块、标题、正文在同篇内各自成块，类型不塌陷。
- **输入与前置条件**：自建 docx（含缩进代码段、`>` 引用、标题、正文）。
- **执行步骤**：转换后读 `ir.json` 的块类型集合。
- **期望结果**：类型集合含 code/heading/paragraph/quote。
- **实际结果**：`types=['code','heading','paragraph','quote']`（X-04）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：结构映射；七类块中四类同篇覆盖；证据 `extra_probe5.log`。

#### TC-M5-04 PDF 表格映射为列数一致的 GFM 表

- **所属模块**：M5
- **测试目标**：PDF 线框表格转 Markdown 后各行列数一致，不出现撕裂表。
- **输入与前置条件**：`tests/corpus/sources/pdf_*tables*` 表格页。
- **执行步骤**：统计 `content.md` 表行数与每行竖线数（去重集合）。
- **期望结果**：每行竖线数相同（=4，即 3 列 + 首尾）。
- **实际结果**：`rows=4 col_pipes=[4]`——列数唯一，表结构完整（X-05）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；表格映射；证据 `extra_probe5.log`。

#### TC-M5-05 单元格内 `|` 不撕裂表格

- **所属模块**：M5
- **测试目标**：源文本含 `\|` 组合时，产物须同时转义反斜杠与管道，保持列数（第二轮 D4）。
- **输入与前置条件**：单元格内容 `反斜杠\|组合`、`x|y`、`z`。
- **执行步骤**：读产物该行，统计未转义竖线数与原始竖线数。
- **期望结果**：保持 3 列；正文中的 `|` 全部带反斜杠转义。
- **实际结果**：`row='| 反斜杠\\\\\|组合 | x\|y | z |'`、`unescaped_pipes=4`（仅表分隔位）、`raw_pipes=6`（X-06）——第二轮 D4 闭合。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：第二轮 D4 复验；证据 `extra_probe5.log`。

#### TC-M5-06 嵌套表降维必须告警

- **所属模块**：M5
- **测试目标**：嵌套表格无法在 GFM 表达时降维处理并产出 W1045（第二轮 D5）。
- **输入与前置条件**：`docx_11_complex_tables.docx` 派生的嵌套表夹具。
- **执行步骤**：转换后读 `meta.warnings`。
- **期望结果**：`warnings` 含 W1045。
- **实际结果**：`warnings=['W1045']`（X-07）——第二轮「降维无声」缺陷闭合。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：告警覆盖 W1045；证据 `extra_probe5.log`。

#### TC-M5-07 段内分页符按位置切开前后文

- **所属模块**：M5
- **测试目标**：段中 `page_break` 必须落在两段之间且顺序正确，不得把后文并到前段（第二轮 D6）。
- **输入与前置条件**：自建 docx，段内插入 `WD_BREAK.PAGE`，前文/后文各 ≥20 字。
- **执行步骤**：转换后读块类型序列与 `page_break` 下标。
- **期望结果**：序列为 `paragraph → page_break → paragraph (→ paragraph)`，断点位置正确。
- **实际结果**：`seq=['paragraph','page_break','paragraph','paragraph'] pb_at=1`（V-PIPE-18）——第二轮 D6 闭合。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：第二轮 D6 复验；证据 `pipe_probe8.log`。

### 3.6 M6 S4 Markdown 渲染与安全红线

#### TC-M6-01 源文本字面量不得新增结构

- **所属模块**：M6
- **测试目标**：正文里的 `#`、`>`、`[]()`、`**`、`1.` 必须转义为字面文本（第二轮 D2「Markdown 字面量失真」P0）。
- **输入与前置条件**：自建 docx，逐行写入 8 条 Markdown 语法字面量。
- **执行步骤**：`doc2md convert literal.docx --json` → 读 `content.md`。
- **期望结果**：不出现未转义的标题/链接/加粗语法；`# 这是一行字面井号` 以 `\#` 形式落地。
- **实际结果**：`\# 这是一行字面井号`、`\[链接文字\](https://example.com)`、`1\. 字面有序列表`、`\<script>alert(1)\</script>`（V-PIPE-17/17b 样例行）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：第二轮 D2 复验；`s4_render::_escape_inline`；证据 `pipe_probe8.log`。

#### TC-M6-02 产物无未转义尖括号标签，`javascript:` 链接不直通

- **所属模块**：M6
- **测试目标**：源文本含 `<script>`、`<img onerror=…>`、`[点我](javascript:…)` 时不得形成可执行 HTML；同时正文文字不得丢失。
- **输入与前置条件**：同 TC-M6-01 夹具。
- **执行步骤**：正则扫描产物中未被反斜杠转义的 `<tag…>`，并检查 `alert(1)`/`onerror` 文本仍在。
- **期望结果**：未转义标签数 = 0；文字保留；`javascript:` 链接不成链。
- **实际结果**：`未转义标签=[]`，`[点我](javascript:alert(3))` 渲染为 `\[点我\](javascript:alert(3))`（V-PIPE-17b）。取证说明：首跑该行曾打印 `has_bare_script=True`，核查后确认是探针子串判断不严谨（`\<script>` 中的 `<script>` 片段），非产品直通；已把断言改为「未转义尖括号标签正则」后复跑为空。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：第二轮 D3 复验；安全边界；证据 `pipe_probe8.log`。

#### TC-M6-03 外链渲染：链接文字转义且换行压平

- **所属模块**：M6
- **测试目标**：外链文字里的 Markdown 语法与换行不得破坏链接语法，目标 URL 原样可达。
- **输入与前置条件**：DOCX 超链接，文字含 `<script>`、`*`、`~` 与换行，目标 `https://example.com/a b`。
- **执行步骤**：读产物该行。
- **期望结果**：`[转义后的文字](https://example.com/a%20b)`，无裸换行。
- **实际结果**：`rendered='[点我 \\<script>\\*\\~ 换行](https://example.com/a%20b)\n'`（V-PIPE-12）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流 + 边界；证据 `pipe_probe8.log`。

#### TC-M6-04 整段粗体/斜体产出 GFM 标记

- **所属模块**：M6
- **测试目标**：`bold`/`italic` 运行属性映射为 `**`/`*`。
- **输入与前置条件**：自建 docx，含整段粗体、整段斜体、整段删除线三行。
- **执行步骤**：转换后检查产物是否含 `**…**` 与 `*…*`。
- **期望结果**：粗体/斜体标记齐备。
- **实际结果**：`has_bold=True has_italic=True`（X-08）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；marks 映射；证据 `extra_probe5.log`。

#### TC-M6-05 删除线默认降级为纯文本（M-5/M-6c 红线）

- **所属模块**：M6
- **测试目标**：默认档不产出 `~~…~~`，且产物不得残留裸 `~~`。
- **输入与前置条件**：同 TC-M6-04 夹具，默认配置。
- **执行步骤**：转换后在产物中检索 `~~`。
- **期望结果**：`has_tilde=False`，文字保留。
- **实际结果**：`has_tilde=False`（X-08b）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：兼容性（GFM 方言差异）；证据 `extra_probe5.log`。

#### TC-M6-06 混合 marks 段落不扩散（CH-483 R8-PIPE-7 回归）

- **所属模块**：M6
- **测试目标**：同行内不同 run 的 marks 不得把整段判为同一 marks。
- **输入与前置条件**：三行分别整段粗体/整段斜体/整段删除线。
- **执行步骤**：读该行 `marks` 聚合结果。
- **期望结果**：混合段落的段级 `marks` 为空字典（不误并），各行 run 级 marks 正确。
- **实际结果**：`marks={} all={'粗体整段':{'bold':True},'斜体整段':{'italic':True},'删除整段':{'strikethrough':True}}`（X-08c）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回归；证据 `extra_probe5.log`。

#### TC-M6-07 opt-in `preserve_marks_strike` 保留删除线且红线不误判

- **所属模块**：M6
- **测试目标**：显式开启时删除线以 `~~` 保留，且渲染红线不因 opt-in 报错。
- **输入与前置条件**：`doc2md.toml` 设 `[s4] preserve_marks_strike=true`。
- **执行步骤**：转换含删除线的 docx。
- **期望结果**：产物含 `~~…~~`；rc=0；无 E5031。
- **实际结果**：`strike_md=True rc=0`（X-08d）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：配置分支；证据 `extra_probe5.log`。

#### TC-M6-08 连续列表项之间单换行（tight list）

- **所属模块**：M6
- **测试目标**：相邻列表项不得插入空行，否则被解析为松散列表。
- **输入与前置条件**：自建 docx，3 个无序项 + 3 个有序项。
- **执行步骤**：比对产物列表片段。
- **期望结果**：`- 甲\n- 乙\n- 丙\n1. 一\n2. 二\n3. 三\n` 无空行。
- **实际结果**：`match=True sample='- 项目甲\n- 项目乙\n- 项目丙\n1. 第一项\n2. 第二项\n3. 第三项\n'`（X-03）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：格式差异防线；证据 `extra_probe5.log`。

#### TC-M6-09 渲染红线有生产调用方并在命中时明确失败

- **所属模块**：M6
- **测试目标**：`assert_render_contract` 必须被 S4 交付路径实际调用，命中违例时抛 E5031（CH-500 AR-4/R17-PIPE-2：原红线「有实现无调用方」）。
- **输入与前置条件**：构造块文本含 `<script` 的表格块。
- **执行步骤**：白盒调用 `render(ir, contract=True)`，捕获异常与 detail。
- **期望结果**：抛 E5031，`detail.reason=render_contract` 且带 `marker/block_type/block_id` 可定位。
- **实际结果**：`E5031 detail={'reason':'render_contract','marker':'<script','block_type':'table','block_id':'b1'}`（V-PIPE-11）；生产侧调用点由 `tests/test_ch500_round17.py` 钉住（38 项红线之一）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：校验层有效性；证据 `pipe_probe8.log`、`tests/test_ch500_round17.py`。

#### TC-M6-10 代码块字面量豁免（红线不误杀）

- **所属模块**：M6
- **测试目标**：代码块内出现 `<script` 属正当内容，不得被红线拒绝。
- **输入与前置条件**：含 `<script>` 文本的代码块 IR。
- **执行步骤**：`render(contract=True)` 后取正文长度。
- **期望结果**：不抛异常，代码块原样保留。
- **实际结果**：`len=34`（V-PIPE-11b），未触发红线。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：红线精度；证据 `pipe_probe8.log`。

#### TC-M6-11 `--format both` 双产物首屏等价

- **所属模块**：M6
- **测试目标**：同一次转换的独立 `.md` 与 zip 内正文必须字节等价，避免两个出口各说各话。
- **输入与前置条件**：`docx_01_simple.docx`。
- **执行步骤**：`doc2md convert … --format both --json` → 比对 md 文件与 zip 内 `content.md` 字节数。
- **期望结果**：两处字节数相同。
- **实际结果**：`md=docx_01_simple.docx.md bytes=661 zip_md=661`（X-10）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：接口一致性；证据 `extra_probe5.log`。

#### TC-M6-12 表格单元格特殊字符单一口径转义（`&` / `<` / `|`）

- **所属模块**：M6
- **测试目标**：确认单元格内 `&`、`<`、`|` 的转义与内联文本同口径，且已转义实体不被二次解释成错误形态。
- **输入与前置条件**：自建 docx，2×3 表格：`A & B`/`C < D`/`E | F` 与 `x&y`/`<script>`/`a&amp;b`。
- **执行步骤**：`doc2md convert` → 读 `content.md` 表行。
- **期望结果**：`&`→`&amp;`、`<`→`&lt;`、`|`→`\|`；字面 `a&amp;b` 双转义为 `a&amp;amp;b`（渲染回字面值）；表格行数与列数不变。
- **实际结果**：`| A &amp; B | C &lt; D | E \| F |` 与 `| x&amp;y | &lt;script&gt; | a&amp;amp;b |`，表头分隔行 `| --- | --- | --- |`，rc=0、`doc_id=443f0507049b564e blocks=2 chars=208`。
- **通过状态**：通过（本轮实测未发现转义口径分裂；`B-37` 的诉求「单一来源」属重构面而非缺陷，仍按登记处理）
- **优先级**：P0
- **覆盖指标**：安全边界 + 格式差异；证据 `/tmp/qa-r3/ampout2/443f0507049b564e.zip`。

### 3.7 M7 LLM 修复、回退与 op 契约

#### TC-M7-01 offline 模式零 op 应用，`llm_stats` 不外泄到正文

- **所属模块**：M7
- **测试目标**：`llm.mode≠server` 时走离线规则，且 `llm_stats` 只进 `report.html`/DB meta，不进 `content.md` front-matter 与 zip 内 `ir.json`。
- **输入与前置条件**：默认离线档转换 `docx_01_simple.docx`。
- **执行步骤**：解包读 `ir.json` 的 meta 键、`report.html` 的 LLM 表。
- **期望结果**：`ir.json` meta 无 `llm_stats`；报告记 `source=offline`；应用 op 数为 0。
- **实际结果**：`ir.json_meta_has_llm_stats=False report_has_offline=True`（X-13）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：接口一致性（产物面 vs 报告面）；`pipeline/convert.py:417-418`；证据 `extra_probe5.log`。

#### TC-M7-02 服务不可用时默认回退，产物仍成功

- **所属模块**：M7
- **测试目标**：`llm.fallback=true`（默认）时服务探活失败必须回退离线并完成转换。
- **输入与前置条件**：`llm.mode=server`，`127.0.0.1:8080` 未监听。
- **执行步骤**：`doc2md convert docx_01_simple.docx --json`。
- **期望结果**：rc=0、`status=ok`、产物 zip 存在，并带 `fallback` 标记。
- **实际结果**：`rc=0`、`{"status":"ok","doc_id":"0ce7cddb6afbc150","blocks":9,"chars":85,"zip":"…/x15/0ce7cddb6afbc150.zip"}`（X-15）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常流回退；`repair_ops`；证据 `extra_probe5.log`。

#### TC-M7-03 禁回退 + 服务不可用 → E5041 明确失败（条件可达）

- **所属模块**：M7
- **测试目标**：`llm.fallback=false` 且存在候选块时，服务不可用必须 E5041/rc=4，而不是静默成功。
- **输入与前置条件**：自建**含候选块**的 docx（粘连段落，使 `_candidate_indices()` 非空）；`127.0.0.1:8080` 未监听。
- **执行步骤**：`doc2md convert cand_probe.docx --llm-mode server --llm-no-fallback --json`。
- **期望结果**：E5041、rc=4、`detail` 含 `stage/bind/port` 与启动提示。
- **实际结果**：`rc=4`、`{"code":"E5041","message":"local llm service unavailable (http)","detail":{"stage":"http","bind":"127.0.0.1","port":8080,"hint":"启…"}}`（X-14）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：明确失败优先于看似成功；E5041 为**条件可达**——无候选块文档不触发探活（见 TC-M7-04）；证据 `extra_probe5.log`。

#### TC-M7-04 无候选块文档在 `llm.mode=server` 下不触发任何探活

- **所属模块**：M7
- **测试目标**：确认「服务不可用」在零候选文档上的可观测性。
- **输入与前置条件**：`docx_01_simple.docx`（本机 22 篇语料全部零候选）+ `--llm-mode server --llm-no-fallback`。
- **执行步骤**：转换后读退出码与 `report.html` 的 LLM 表。
- **期望结果**：契约上应能区分「服务已验证」与「服务从未接触」；当前实现不回错。
- **实际结果**：`rc=0`、`status=ok`，报告记 `source=server` / `batches=0`（X-14b）。根因：`_candidate_indices()` 判无候选 → `payload_batches()` 返回空 → `repair()` 批循环一次不执行，`fallback=false` 也不探活。
- **通过状态**：**缺口**（登记 B-42：改法要么零候选时跑一次 `llm.probe()` 并计入 `llm_stats`，要么把 `source` 分成 `server`/`server-untouched`——两者都动 `llm_stats`（进 report.html，即产物字节），需黄金重烤；「无候选算不算服务失败」属契约决策）
- **优先级**：P1
- **覆盖指标**：可观测性 + 契约决策；建议顺序：中，与 B-33/B-37 同批重烤；证据 `extra_probe5.log`、`docs/review-round17-CH-500.md` 五、B-42。

### 3.8 M8 打包与产物形态

#### TC-M8-01 zip 三成员与 front-matter 版本戳

- **所属模块**：M8
- **测试目标**：交付包必须含 `content.md`/`ir.json`/`report.html`，且 front-matter 版本与运行时版本同源。
- **输入与前置条件**：任一成功转换。
- **执行步骤**：`zipfile.namelist()` + 读 `content.md` front-matter。
- **期望结果**：三成员齐备；`pipeline_version: 2.18.0`。
- **实际结果**：`members=['content.md','ir.json','report.html'] fm_has_version=True`（X-11）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：产物结构；证据 `extra_probe5.log`。

#### TC-M8-02 磁盘余量不足 → E4131（NFR-6）

- **所属模块**：M8
- **测试目标**：打包前空间校验必须失败并给出 `need/free/rule`（第二轮记为「只有代码审查证据」的缺口）。
- **输入与前置条件**：`doc2md.toml` 设 `[perf] min_free_mb=10000000`。
- **执行步骤**：`doc2md convert … --json`。
- **期望结果**：E4131、rc=4、`detail.rule="NFR-6"`、`need_mb/free_mb` 可见。
- **实际结果**：`rc=4`、`{"code":"E4131","message":"insufficient free space for 867274fbd01205d4.zip: need 10000000MB, free 82763MB",…}`（V-PIPE-14；P04 同码 `detail={"target":…,"free_mb":81995,"need_mb":10000000,"rule":"NFR-6"}`）。附带事实：E4131 在完整解析之后、打包时才触发（`convert.py::assert_free_space`），不是预检——空间极紧张时仍会先付出解析成本。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：资源边界；补齐第二轮 E4131 缺口；证据 `pipe_probe8.log`、`r3probe/RESULTS.md` P04。

#### TC-M8-03 目录文件数超限 → E4133（CLI rc=4 / API 413 同码）

- **所属模块**：M8
- **测试目标**：超宽目录在 CLI 与 API 两条入口都给出同一错误码与 `detail.limit`。
- **输入与前置条件**：`widedir/` 放 4 个 docx，`[watch] max_files_per_dir=3`；API 侧 `PUT /settings` 同值后 `POST /jobs {path:widedir}`。
- **执行步骤**：分别跑 CLI 与 API。
- **期望结果**：CLI E4133/rc=4；API E4133/413；`detail={dir,limit}`；两侧同一 `scan_or_raise`。
- **实际结果**：CLI `rc=4 {"code":"E4133","message":"directory /tmp/qa-r3/verify/widedir exceeds max_files_per_dir=3","detail":{"dir":…,"limit":3}}`（V-PIPE-15）；API `413 {"code":"E4133","detail":{"dir":…,"limit":3}}`（P04）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：资源边界 + 接口一致性；补齐第二轮 E4133 缺口；证据 `pipe_probe8.log`、`r3probe/RESULTS.md` P04。

#### TC-M8-04 产物 zip 被误删后按 DB 重建并导出

- **所属模块**：M8
- **测试目标**：库内仍有块数据时，`docs export` 必须能重建 zip 与 md，并给出 manifest。
- **输入与前置条件**：`x17root` 已入库 `pdf_01_simple_1p.pdf`，其 zip 被删除。
- **执行步骤**：`doc2md docs export --root x17root …`，检查重建文件与 manifest。
- **期望结果**：rc=0；zip 依 DB 重建；md 与 manifest 产出。
- **实际结果**：`rc=0 rebuilt=['a545becc4d31a988.zip'] md=['pdf_01_simple_1p.pdf.md'] manifest=True`（X-19）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：数据完整性恢复路径；证据 `extra_probe5.log`。

#### TC-M8-05 纯产物转换不写库（只读转换面）

- **所属模块**：M8
- **测试目标**：不带 `--root` 的 `convert` 不得悄悄创建/写入 `state.sqlite` 的 docs 表。
- **输入与前置条件**：空目录执行 `doc2md convert …`。
- **执行步骤**：转换后枚举 `.sqlite` 文件与 docs 行数。
- **期望结果**：无库文件、无 docs 行。
- **实际结果**：`dbs=[] docs_rows=0`（X-12）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：职责边界（转换面 vs 入库面）；证据 `extra_probe5.log`。

### 3.9 M9 存储、检索与库操作

#### TC-M9-01 FTS 检索给出真命中，`--full` 兜底同向命中

- **所属模块**：M9
- **测试目标**：入库块可被 FTS 检索命中，且全表 LIKE 兜底模式与 FTS 命中同一块（第二轮只验了退出码，本轮把「零命中也算过」的空跑口径堵掉）。
- **输入与前置条件**：`x17root` 内 docx_09 入库 4~16 块；查询词从库内真实块文本取前 2 字（`unicode61` + aux bigram 对 2 字 CJK 词有效）。
- **执行步骤**：`doc2md search --root x17root <词>`；再 `doc2md search --root x17root --full <词>`；比对两侧 `block_id` 集合。
- **期望结果**：两侧命中数 ≥1 且交集非空。
- **实际结果**：`词=中英 fts_n=1 full_n=1 mode=fts 仅FTS=[] 仅full=[]`（X-17）。取证说明：该项首跑曾以「rc==0」通过但实际命中 0 条（`"Markdown"` 在本语料无命中、`unicode61` 对 2 字以上词组切词极稀）；改为强制真命中后复跑，故本轮数值 1 条是语料规模所限，不是检索面失效。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；`storage/repo.py::search`、`storage/fts.py`；证据 `extra_probe5.log`。

#### TC-M9-02 删除文档级联清块并写删除审计

- **所属模块**：M9
- **测试目标**：删除必须同时清 `blocks`、写 `deletions` 审计，且不留孤儿块。
- **输入与前置条件**：`x17root` 内 `doc_id=5250ce38eff3c27e`。
- **执行步骤**：`doc2md docs delete …` 后直查 sqlite 三表计数。
- **期望结果**：`docs` 剩 0、`blocks` 剩 0、删除审计 1 行，返回体给出各级联计数。
- **实际结果**：`rc=0 docs_left=0 blocks_left=0 audit_rows=1 res={'doc_id':'5250ce38eff3c27e','deleted':{'blocks':16,'audit':0,'events':1},…}`（X-18）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：数据完整性；`repo.py::delete_doc`；证据 `extra_probe5.log`。

#### TC-M9-03 备份演练全链路

- **所属模块**：M9
- **测试目标**：`backup drill` 各步（活库复制 / 快照 / 导出 / 校验）必须全部成功，口令缺失时明确失败。
- **输入与前置条件**：`DOC2MD_BACKUP_PASSPHRASE` 已设；`x17root` 有 1 篇文档。
- **执行步骤**：`doc2md backup drill …`。
- **期望结果**：rc=0，各 step `ok=true`；无口令时 E4001/rc=4（本轮实测确认，故探针须显式给口令）。
- **实际结果**：`rc=0 {"ok":true,"steps":[{"name":"copy_live","ok":true,"detail":{"bytes":147456}},{"name":"snapshot","ok":true,"detail":{"file":"state-20261024T155439Z-ce49e662.sqlite"}},…}`（X-20）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：运维面；证据 `extra_probe5.log`。

#### TC-M9-04 日维护报告产出

- **所属模块**：M9
- **测试目标**：`cron` 日维护档生成结构化日报并可定位路径。
- **输入与前置条件**：`x17root`。
- **执行步骤**：`doc2md cron daily --root x17root`。
- **期望结果**：rc=0、`mode=daily`、`ok=true`、`steps` 内含日报路径与 docs 计数。
- **实际结果**：`rc=0 {"mode":"daily","ok":true,"steps":[{"step":"daily_report","path":"…/reports/daily-2026-10-24.json","docs":1},…]}`（X-20b）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：运维面；证据 `extra_probe5.log`。

### 3.10 M10 API 服务、权限、分页与编辑锁

统一前置条件：探针自建 `aroot`，先 `work --enqueue` 5 篇 DOCX 再 `work --once` 排空，随后 `serve` 于 `127.0.0.1:<随机端口>`；票面由 `doc2md serve token --role admin|reader` 现场签发；正文一律 `application/json`，异常分支另发 `x-www-form-urlencoded` / `octet-stream` / 非法 JSON。

#### TC-M10-01 `GET /docs` 存在且信封/行字段齐备

- **所属模块**：M10
- **测试目标**：文档集合路由可用（第二轮 GUI 只能从 `/jobs` 反推列表），信封含 `total/has_more/offset/limit`，每行携带块计数与稳定排序字段。
- **输入与前置条件**：`aroot` 内 5 篇文档。
- **执行步骤**：`GET /docs`（admin 票）。
- **期望结果**：200；`keys=[docs,has_more,limit,offset,total]`；行键含 `blocks/created_at/doc_id/format/pages/profile/shard_count/sharded/source_key/source_name`。
- **实际结果**：`st=200 keys=['docs','has_more','limit','offset','total'] total=5 rows=5`；row0 键集合与期望一致（V-API-01/01b）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CH-500 AR-5/R17-API-5 复验；`api.py::docs_list`；证据 `api_probe5.log`。

#### TC-M10-02 D9 分页口径在五个列表端点上完全一致

- **所属模块**：M10
- **测试目标**：`limit=0` 在全项目统一解释为「只要信封不要行」，且 `has_more=False`。
- **输入与前置条件**：同上，且各端点均有数据。
- **执行步骤**：对 `/docs`、`/jobs`、`/search`、`/audit/deletions`、`/docs/<id>/audit` 各发 `?limit=0`。
- **期望结果**：五端点均 200 + 空数组 + `has_more=False` + `offset=0/limit=0` 回显。
- **实际结果**：五例全过——`/docs?limit=0 key=docs rows=0 has_more=False`；`/jobs?limit=0 key=tasks`；`/search?limit=0 key=hits`；`/audit/deletions?limit=0 key=deletions`；`/docs/0ce7cddb6afbc150/audit?limit=0 key=audit`（V-API-02 五连）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：第二轮 D9 复验 + 口径统一；`api.py::_window`；证据 `api_probe5.log`。

#### TC-M10-03 `/jobs` 补 `total/has_more`，`counts` 口径不变

- **所属模块**：M10
- **测试目标**：任务列表在截断时必须让 GUI 分辨「全集」与「截断集」，同时不改 `counts` 的既有语义。
- **输入与前置条件**：5 个已完成作业。
- **执行步骤**：`GET /jobs?limit=1` 与 `GET /jobs`（默认窗口）。
- **期望结果**：`total=5`、截断时 `has_more=True`、`counts={'done':5}` 仍按全库统计。
- **实际结果**：`limited total=5 has_more=True counts={'done': 5}`；`tasks=1 total=5 has_more=True`（V-API-03/03b）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CH-500 R17-API-8；`api.py:715-749`；证据 `api_probe5.log`。

#### TC-M10-04 `/jobs` 支持 offset 翻页且次序有决胜位

- **所属模块**：M10
- **测试目标**：`has_more=True` 不再是死胡同——翻页能取回剩下的行，且同 `created_at` 的作业有确定次序。
- **输入与前置条件**：5 个作业。
- **执行步骤**：`GET /jobs?limit=2&offset=0/2/4`，比对 `job_id` 集合与 `has_more`。
- **期望结果**：页页满窗、集合互不重叠、`has_more` 与窗口一致；SQL 带 `ORDER BY created_at DESC, job_id`。
- **实际结果**：窗口翻页与决胜位由红线 `test_jobs_window_pages_with_offset` / `test_queue_panel_shows_window_truncation` 钉住；探针侧 `has_more=True` 截断可见（V-API-03b），GUI 侧 `loadQueue` 已显示窗口截断提示。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CH-500 R17-API-8；`api.py` `list_jobs` + `static/editor.html`；证据 `tests/test_ch500_round17.py`、`api_probe5.log`。

#### TC-M10-05 审计窗口保持 rowid 升序且末页可判定

- **所属模块**：M10
- **测试目标**：文档审计与删除审计的次序口径唯一（最旧在前），末页 `has_more=False` 可作游标终点。
- **输入与前置条件**：`doc_id=0ce7cddb6afbc150` 有 3 次修订。
- **执行步骤**：`GET /docs/<id>/audit?limit=2`，再取第二页。
- **期望结果**：`revision` 升序；`total=3`、首页 `has_more=True`、末页 `has_more=False`。
- **实际结果**：`st=200 revs=[(1,2),(2,3)] total=3 has_more=True`；`page2=1 has_more=False`（V-API-04/04b）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：CH-500 R17-API-6；`api.py:905-957`；证据 `api_probe5.log`。

#### TC-M10-06 整数参数越界 → 400/E4001 JSON（不再 500）

- **所属模块**：M10
- **测试目标**：`1e20` 一类超 int64 的查询参数必须归入参数错误，而不是未捕获异常。
- **输入与前置条件**：`/events`、`/audit/deletions`、`/docs/<id>/audit`。
- **执行步骤**：分别注入 `since=1e20`、`offset=1e20`、非数字 `limit`、负数 `limit/offset`。
- **期望结果**：400 + `E4001` + 明确 message；负数与 `_window` 同口径。
- **实际结果**：三例均 `st=400 ct=application/json code=E4001 msg="… out of range (|value| <= 9223372036854775807)"`；`limit=abc` → `"limit must be an integer"`（V-API-05/05b）；`/search?limit=-1`、`?offset=-1` → 400/E4001（V-API-13b/13c）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常流；`api.py::_int_arg`/`_INT64_MAX`；证据 `api_probe5.log`。

#### TC-M10-07 非 JSON 正文一律 400，不再折叠成 `{}` 空转

- **所属模块**：M10
- **测试目标**：`body()` 不得把 urlencoded/二进制正文静默当空对象（CH-500 R17-API-1，权限面曾因此把 `POST /tokens` 降级成 reader 票）。
- **输入与前置条件**：admin 票。
- **执行步骤**：1. `PUT /settings` 发 urlencoded；2. `POST /tokens` 发 urlencoded；3. 发非法 JSON；4. 发真·空体对照。
- **期望结果**：前三者 400/E4001 且 message 指明原因；真·空体仍按 `{}` 处理（200）。
- **实际结果**：`PUT /settings` urlencoded → `400 {"code":"E4001","message":"Content-Type must be application/json (got application/x-www-form-urlencoded)"}`；`POST /tokens` 同样 400；非法 JSON → `"invalid JSON body"`；空体 → `st=200 body={"updated":[]}`（V-API-06/06b/06c/06d）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界 + 明确失败；`api.py::body`；证据 `api_probe5.log`。

#### TC-M10-08 405/404/413 全部走 JSON 契约，且处理器优先级正确

- **所属模块**：M10
- **测试目标**：HTTP 语义错误不再返回 Werkzeug HTML；更具体的 404 不被 405 抢；顺序口径固定。
- **输入与前置条件**：服务在线。
- **执行步骤**：1. `PUT /healthz`；2. `GET /不存在路由`；3. 33 MB 以上正文；4. `octet-stream` 且超大正文（顺序对照）。
- **期望结果**：405 JSON 带 `detail.allowed`；404 → E4041；413 → E4131 带 `max_bytes`；content-type 错误优先于体积错误。
- **实际结果**：`405 ct=application/json detail={'allowed':['GET','HEAD','OPTIONS'],'method':'PUT','path':'/healthz','reason':'method_not_allowed','status':405}`；`404 {"code":"E4041","message":"no such route"}`；`413 {"code":"E4131","detail":{"max_bytes":33554432},"message":"request body too large"}`；`octet-stream` 优先 `400/E4001`（V-API-07/07b/07c/07d）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：接口一致性；`create_app` 异常处理器注册（CH-500 R17-API-3，改用 werkzeug 显式注册）；证据 `api_probe5.log`。

#### TC-M10-09 乐观锁：过期 `base_revision` → 425/E4251，缺失 → 400/E4001

- **所属模块**：M10
- **测试目标**：编辑并发冲突必须当场可见，且「缺失基线」与「基线过期」不得混为同一码。
- **输入与前置条件**：`aroot` 内文档已有 revision=4。
- **执行步骤**：1. `PUT` 带 `base_revision=1`（过期）；2. 不带 `base_revision`。
- **期望结果**：425 + `E4251` + `detail.current_revision`；400 + `E4001 base_revision is required`。
- **实际结果**：`st=425 {"code":"E4251","detail":{"current_revision":4},"message":"revision conflict"}`；`st=400 {"code":"E4001","message":"base_revision is required"}`（V-API-08/08b）。第二轮登记的 E4251 缺口闭合。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：数据完整性；补齐 E4251；`edits.py`；证据 `api_probe5.log`。

#### TC-M10-10 权限矩阵：越权 403、无票 401、健康检查免鉴权

- **所属模块**：M10
- **测试目标**：角色边界与免鉴权白名单均按契约执行。
- **输入与前置条件**：admin 票 + reader 票。
- **执行步骤**：1. reader `POST /jobs`；2. `GET /healthz` 不带票；3. 任意受保护路由不带票；4. `DELETE /tokens/<未知票>`；5. `DELETE /tokens`（无票面段）。
- **期望结果**：403 + `detail={required:editor,role:reader}`；200；401/E4011；404/E4041（票面脱敏）；405 + allowed 列表。
- **实际结果**：`403 {"code":"E4031","detail":{"required":"editor","role":"reader"},"message":"action submit_job requires editor"}`；`/healthz` 免鉴权 `200 {"docs":5,"ok":true,"pipeline":"2.18.0",…}`；无票 `401 {"code":"E4011","message":"missing token"}`；未知票 `404 {"code":"E4041","detail":{"token":"nonexi…"}}`；无票面段 `405 detail.allowed=['OPTIONS','POST']`（V-API-09/09b/09c/09d/09e）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：权限边界 + 票面脱敏；`auth.py`；证据 `api_probe5.log`。

#### TC-M10-11 未归类异常 → 500 JSON 契约，且 `E_UNHANDLED` 当场落盘

- **所属模块**：M10
- **测试目标**：500 不再返回 HTML，且事件日志必须**立即**写入而不是等下一个请求（CH-500 R17-API-4/AR-2）。
- **输入与前置条件**：构造未捕获异常（`JSONDecodeError` 逃逸路径）。
- **执行步骤**：1. 触发 500；2. 立即读 `app.jsonl` 字节增量与尾行。
- **期望结果**：500 + `{code,message,detail}` JSON；日志字节即时增长并含 `E_UNHANDLED`。
- **实际结果**：`st=500 bytes 597->647 tail={"code": "E_UNHANDLED", "exc": "JSONDecodeError"}`——同请求内即落盘（`events.py` `write` 后 `flush()`）（V-API-10）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：可观测性/明确失败；补齐第二轮「500 面」证据；证据 `api_probe5.log`。

#### TC-M10-12 事件双写面同源：DB 与 app.jsonl 事件名一致

- **所属模块**：M10
- **测试目标**：payload 里的业务码不得顶掉事件名；两侧对同一事件记同名（CH-500 R17-API-2/AR-1）。
- **输入与前置条件**：admin 票，含一次 `emit(source='probe', code='E4091')`。
- **执行步骤**：比对 `events` 表与 `app.jsonl` 的事件名集合；再核 `emit` 行的 `code`/`biz_code` 与入库 payload。
- **期望结果**：业务事件名两侧一致；日志行 `code=事件名`、`biz_code=业务码`；入库 payload 保留业务码。
- **实际结果**：`db=['doc_overwritten','job_done','settings_updated','text_edited'] log=['E_UNHANDLED','SERVICE_START','settings_updated','text_edited']`（差异项仅为本探针自造的 `E_UNHANDLED`/`SERVICE_START` 系统事件，业务事件名两侧完全一致）；`emit` 行 `{'source':'probe','biz_code':'E4091','code':'job_rejected',…}`，`db_code=job_rejected db_payload={"code":"E4091","source":"probe"}`（V-API-11/11b）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：接口一致性；`events.py::emit`；证据 `api_probe5.log`。

#### TC-M10-13 文档详情：窗口字段齐备且 `warnings` 可派生

- **所属模块**：M10
- **测试目标**：单文档读取按窗口返回，并给出总块数与告警派生字段，GUI 才能显示「还有更多」。
- **输入与前置条件**：`doc_id=0ce7cddb6afbc150`（9 块）。
- **执行步骤**：`GET /docs/<id>?offset=0&limit=1`。
- **期望结果**：200；`blocks` 1 行、`blocks_total=9`、`blocks_limit=1`、含 `warnings`。
- **实际结果**：`st=200 blocks=1 total=9 limit=1 warnings=[]`（V-API-12）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：GUI 数据面；`api.py` 文档详情路由；证据 `api_probe5.log`。

#### TC-M10-14 `/search`：信封回显、参数校验与 offset 翻页真命中

- **所属模块**：M10
- **测试目标**：检索面补齐 offset 翻页（原 `has_more=True` 是死胡同）、`limit=0` 归入 D9、越界参数归 400（CH-500 R17-API-7）。
- **输入与前置条件**：`aroot` 5 篇文档入库。
- **执行步骤**：1. `GET /search?q=<词>&limit=5`；2. `limit=-1`/`offset=-1`；3. `limit=0`；4. `limit=500` 取真值宽窗；5. `limit=2&offset=0/2/4` 三页翻页。
- **期望结果**：信封含 `hits/total/offset/limit/has_more/match/mode`；负数 → 400/E4001；`limit=0` → 空集 + `has_more=False`；三页页页满窗、互不重叠、`total` 等于本页行数（窗口口径）、`has_more` 与真值同口径。
- **实际结果**：`st=200 q=段落 hits=1 total=1 keys=['has_more','hits','limit','match','mode','offset','total']`；负数两例 400/E4001；`limit=0` → `{"has_more":false,"hits":[],"limit":0,…}`；翻页例 `词=的 宽窗=120(精确=True) 页宽=[2,2,2] 重叠=0 has_more=[True,True,True] total回显=[2,2,2]`（V-API-13/13b/13c/13d/13e）。取证说明：该例首跑为**空跑**——`if len(ids)>=2` 在命中不足时静默跳过，PASS 什么也没测；改为「命中不足即判失败 + 有界三页」后才拿到 120 条真值证据。`total` 采用窗口口径（截断时为「本页可见数」，CH-471 STOR-2 取消逐次全表 COUNT），GUI `editor.html:470` 同口径显示「本页 N 命中」。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CH-500 R17-API-7；`repo.py:357-440`、`api.py:962-976`、`cli.py --offset`、`editor.html goMore/SRCH`；证据 `api_probe5.log`、`tests/test_ch500_round17.py`。

#### TC-M10-15 GUI 审阅页与静态资源可用

- **所属模块**：M10
- **测试目标**：`serve` 起的审阅页必须可直接打开（GUI 面的入口回归）。
- **输入与前置条件**：服务在线。
- **执行步骤**：`GET /`、`GET /static/editor.html`。
- **期望结果**：均 200，`/` 为 `text/html`；页内脚本语法自检通过。
- **实际结果**：`st=200 ct=text/html; charset=utf-8 len=21539`；`/static/editor.html st=200 len=21539`（V-API-14/14b）；内联脚本抽出 16882 字节后 `node --check` rc=0。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：GUI 入口；证据 `api_probe5.log`、`/tmp/qa-r3/verify/editor_inline.js`。

#### TC-M10-16 设置读写与热重载

- **所属模块**：M10
- **测试目标**：`GET/PUT /settings` 与 `POST /system/reload` 三段可用，且 PUT 只回报实际变更段。
- **输入与前置条件**：admin 票。
- **执行步骤**：`GET /settings` → `PUT /settings {perf:{queue_async:false}}` → `POST /system/reload`。
- **期望结果**：三段 200；PUT 回显 `updated` 列表。
- **实际结果**：`get=200 put=200 reload=200 body={"updated":["perf"]}`（V-API-15）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：运维接口；证据 `api_probe5.log`。

#### TC-M10-17 冲突护编辑：已编辑文档重提交 → conflict/E4091；`force` 显式绕过

- **所属模块**：M10
- **测试目标**：人工编辑成果不得被后台重跑静默覆盖；绕过必须显式。
- **输入与前置条件**：`doc_id=0ce7cddb6afbc150` 已编辑 1 块。
- **执行步骤**：1. `POST /jobs {path:SRC}`；2. `POST /jobs {path:SRC, force:true}`。
- **期望结果**：首跑返回 `status=conflict`、`code=E4091`、`edited_blocks` 计数；force 后回到正常受理行（无 conflict/code，有 `job_id`）。
- **实际结果**：`st=202 {"jobs":[{…,"code":"E4091","edited_blocks":1,"job_id":"job-043d915a7c4d","status":"conflict"}]}`；force → `st=202 {"jobs":[{…,"idempotency_hit":false,"job_id":"job-c137646a0782","shard_count":1,"sharded":false}]}`（V-API-16/16b）。取证说明：该例首跑间歇失败，根因是探针从复用库中按 `rowid` 取 `doc_id` 而重提交用另一源文件，编辑与冲突落到不同文档；钉死 `source_name` 后稳定，属探针不确定性而非产品间歇失效。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：数据完整性 + 权限语义；第二轮 D8 复验；`api.py` `submit`/`edits.py`；证据 `api_probe5.log`。

#### TC-M10-18 异步队列：`perf.queue_async` 开启后仅回执 job_id，worker 可排空

- **所属模块**：M10
- **测试目标**：排队模式与同步模式的结果形状区分清楚，且独立 worker 能排空并如实回报。
- **输入与前置条件**：`PUT /settings {perf:{queue_async:true}}`。
- **执行步骤**：1. `POST /jobs {path:docx_02}`；2. `doc2md work --root aroot --once`。
- **期望结果**：202 + `mode=queued`，行内只给 `job_id`/`status=queued`，不冒充已完成的 `doc_id` 结果；worker 回报 `drained≥1`。
- **实际结果**：`st=202 …"jobs":[{…,"job_id":"job-d210dbd18804","status":"queued"}],"mod…`（含 `consume_with:"doc2md work"`）；worker `rc=0 out={"drained":1,"done":1,"failed":0,"rejected":0,"drift":0,"notes":[]}`（V-API-17/17b/17c）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：`worker.py:708-792`、`scheduler.py`；租约并发面需外部环境（B-27-remote）；证据 `api_probe5.log`。

#### TC-M10-19 路由清单与文档附录 C 严格同源

- **所属模块**：M10
- **测试目标**：文档承诺的路由集合与实际 `url_map` 的 (rule, method) 集合完全一致，防止「文档有、路由无」或反之。
- **输入与前置条件**：附录 C（`README.md`）可解析；应用可枚举 `url_map`。
- **执行步骤**：解析附录 21 行；枚举 `url_map` 的 (rule, method) 去掉 `/static`；做集合差。
- **期望结果**：两侧集合相等，双向差集为空。
- **实际结果**：`doc=21 url_map=21 仅在文档=[] 仅在路由=[]`；`GET /docs` 确在路由表内 `matched=['GET /docs']`（V-API-18/18b）。口径说明：Flask 对同一 rule 的多个方法各注册一个 MapRule，故 (rule, method) 为 22 行、唯一路径 19 条，减去 `/static` 后恰为 21——第二轮的「端点数漂移」是把两种口径混比造成的假警。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：文档一致性；证据 `api_probe5.log`。

#### TC-M10-20 源被新内容替代 → E4092（worker 面，非 HTTP 面）

- **所属模块**：M10
- **测试目标**：同一路径排队两次且内容不同时，先入队作业必须以 E4092 落为 `rejected`，并留事件痕迹（第二轮登记的 E4092 缺口）。
- **输入与前置条件**：`queue_async=true`；`supersede.docx` 先写入内容 A 入队，再原地改成内容 B 入队。
- **执行步骤**：`work --once` 排空后读 `tasks` 表与 `events` 表。
- **期望结果**：drain 回报 `rejected=1`；先作业行 `status=rejected error_code=E4092` 且 `detail` 给出 `enqueued_doc_id/consumed_doc_id`；`job_rejected` 事件成对可查。
- **实际结果**：`drain: {"drained":2,"done":1,"rejected":1,"notes":["job-ab0b4b30620d: source superseded"]}`；`tasks: job-ab0b4b30620d status=rejected error_code=E4092 detail={"reason":"source_superseded","enqueued_doc_id":"0ce7cddb6afbc150","consumed_doc_id":"9c7094a4d06cfa83"}`；`events seq=10 code='job_rejected'`（P03）。同时记录两条事实：E4092 只在 worker/drain 路径产生，**没有任何 HTTP 路由抛它**（`grep` 命中仅 `worker.py:42/330`）；首轮 drain 曾因 `drain_once` 收到 `str` 根路径在 `worker.py:102` 抛 `TypeError`，并把两个作业留在 `running` 租约内约 30 分钟。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：补齐 E4092；并发/租约面；租约恢复的并发复证依赖外部环境（B-27-remote）；证据 `r3probe/RESULTS.md` P03。

### 3.11 M11 CLI 批量、退出码与格式出口

#### TC-M11-01 `python -m doc2md` 可执行（打包红线）

- **所属模块**：M11
- **测试目标**：模块入口必须存在，`-m` 方式不得因缺 `__main__` 而失败（CH-500 R17-PKG-1）。
- **输入与前置条件**：仓库 `.venv`。
- **执行步骤**：`python -m doc2md --help`；`python -m doc2md doctor`。
- **期望结果**：均 rc=0；`--help` 含子命令用法；`doctor` 输出 JSON。
- **实际结果**：`rc=0 head='usage: doc2md [-h] [--version]\n {serve,cron,backup,convert,work,watch,tokenizer,doctor,search,docs} …'`；`doctor rc=0` 且 `"pipeline": "2.18.0"`（V-PIPE-13/13b）。修复前为 `No module named doc2md.__main__`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：打包/入口；新增 `doc2md/__main__.py`；证据 `pipe_probe8.log`。

#### TC-M11-02 Markdown 直出失败必须非零退出且标错（D1 复验）

- **所属模块**：M11
- **测试目标**：`--format md` 落盘目标被同名目录占位时，不得返回 0 冒充成功（第二轮 D1）。
- **输入与前置条件**：`d1.pdf` + 与输出 md 同名的已存在目录。
- **执行步骤**：`doc2md convert d1.pdf --format md --out v21/out`。
- **期望结果**：rc≠0、`status=error`、错误码 E5031、message 指明 `markdown emit failed`。
- **实际结果**：`rc=4 {"file":"d1.pdf","status":"error","doc_id":"a545becc4d31a988","blocks":4,"chars":66,"zip":"…/a545becc4d31a988.zip","code":"E5031","message":"markdown emit failed: IsADirectoryE…"}`（V-PIPE-21）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：第二轮 D1 复验；退出码契约；证据 `pipe_probe8.log`。

#### TC-M11-03 md 模式结果行的 `zip` 字段不指向不存在文件（D12 复验）

- **所属模块**：M11
- **测试目标**：只出 md 时不得在结果行谎报 zip 路径。
- **输入与前置条件**：`--format md` 正常成功场景。
- **执行步骤**：读结果行键集合与 `zip`/`md` 值。
- **期望结果**：`zip=null`（或 `zip_removed` 语义明确），`md` 指向真实存在的文件。
- **实际结果**：`row_keys=['blocks','chars','doc_id','file','md','status','zip_removed'] zip=None md=/tmp/qa-r3/verify/v22/pdf_01_simple_1p.pdf.md`（V-PIPE-22）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：第二轮 D12 复验；接口一致性；证据 `pipe_probe8.log`。

#### TC-M11-04 损坏配置文件 → E4001/rc=2 且带文件定位（D10 复验）

- **所属模块**：M11
- **测试目标**：`doc2md.toml` 语法损坏时必须以配置错误档退出（rc=2），并指出文件与解析原因（第二轮 D10）。
- **输入与前置条件**：`v23/doc2md.toml` 首行写入非法语句。
- **执行步骤**：`doc2md --config v23/doc2md.toml doctor`。
- **期望结果**：rc=2、E4001、message 含配置文件绝对路径与 `TOMLDecodeError` 位置。
- **实际结果**：`rc=2 {"code":"E4001","message":"config error: /private/tmp/qa-r3/verify/v23/doc2md.toml: 配置无法解析（TOMLDecodeError: Invalid statement (at line 1, column 1)）"}`（V-PIPE-23）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：第二轮 D10 复验；退出码 2 档；证据 `pipe_probe8.log`。

#### TC-M11-05 作业日志的 `file` 字段可定位（D13 复验）

- **所属模块**：M11
- **测试目标**：队列事件必须能回溯到具体源文件；同 basename 不同内容不得互相掩盖（第二轮 D13）。
- **输入与前置条件**：`aroot` 内 5 篇 DOCX 排队并排空。
- **执行步骤**：读 `work.jsonl` 的 `file` 字段集合。
- **期望结果**：完整路径，或 `basename#稳定哈希` 形式。
- **实际结果**：`files=['docx_01_simple.docx#9622e5e6']`（V-PIPE-24，`#哈希` 后缀使同名不同内容可区分）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：第二轮 D13 复验；可观测性；证据 `pipe_probe8.log`。

#### TC-M11-06 CLI `docs list` 与 API 同分页口径（D9 出口一致性）

- **所属模块**：M11
- **测试目标**：`limit=0` 在 CLI 面同样返回空集且 `has_more=false`，`--offset` 可翻页。
- **输入与前置条件**：`aroot` 内 1 篇以上文档。
- **执行步骤**：`doc2md docs list --root aroot --limit 0 --json`；`--limit 1` 对照。
- **期望结果**：`{total:1, offset:0, limit:0, has_more:false}` 且行集为空；`limit=1` 有行。
- **实际结果**：`limit0={'total':1,'offset':0,'limit':0,'has_more':False} limit1_rows=1`（V-PIPE-25）；`cli.py` 已新增 `--offset` 与 API 同口径。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CLI/API 接口一致性；证据 `pipe_probe8.log`。

#### TC-M11-07 退出码矩阵全档可复现

- **所属模块**：M11
- **测试目标**：0/2/3/4 四档退出码各自对应成功/配置错/门禁拒收/契约错误，不得串档。
- **输入与前置条件**：四组夹具（正常转换、坏配置、扫描件、坏 ZIP）。
- **执行步骤**：分别执行并记录 rc。
- **期望结果**：0（成功）、2（E4001 配置）、3（E4221 拒收）、4（E4003/E4131/E4133/E4151/E5031/E5041 契约错误）。
- **实际结果**：`0`（X-15/V-PIPE-13）、`2`（V-PIPE-23）、`3`（X-16c）、`4`（V-PIPE-01/04/14/15、X-14/16/16b）——四档均按契约复现，无串档。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：接口契约；证据 `pipe_probe8.log`、`extra_probe5.log`。

### 3.12 M12 运维、性能与可观测性

#### TC-M12-01 单次作业成对记录 WORK_START/WORK_STOP

- **所属模块**：M12
- **测试目标**：worker 生命周期事件必须成对，便于判断「卡住」与「排空」（CH-500 AR-8）。
- **输入与前置条件**：`aroot` 内 1 个待处理作业。
- **执行步骤**：`doc2md work --root … --once` 后读日志尾行。
- **期望结果**：一次 `WORK_STOP` 带 `drained/done/failed/rejected/drift/notes`，与 `WORK_START` 成对。
- **实际结果**：`rc=0`，尾两行为 `{"code":"job_done","doc_id":"0ce7cddb6afbc150","job_id":"job-dc4a04e2a03d"}` 与 `{"code":"WORK_STOP","drained":1,"done":1,"failed":0,"rejected":0,"drift":0,"notes":[]}`（V-PIPE-19）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：可观测性；`worker.py`；证据 `pipe_probe8.log`。

#### TC-M12-02 四档规模时延基线

- **所属模块**：M12
- **测试目标**：1 页 / 40 页 / 320 页 / 30 章节四档各跑 3 次，确认无数量级回退。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`、`pdf_08_large_40p.pdf`、`tests/golden/large_320p.pdf`、`docx_08_large_30sections.docx`。
- **执行步骤**：逐档 3 次取中位数。
- **期望结果**：中位数 1p ≈0.16 s、40p ≈0.37 s、320p ≈10 s、30sec ≈0.13 s；离散度小。
- **实际结果**：`0.158 s [0.192,0.158,0.141]` / `0.368 s [0.371,0.366,0.368]` / `9.998 s [9.982,9.998,10.136]` / `0.130 s [0.141,0.123,0.130]`，四档 rc 均为 0。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：性能基线；证据 `perf_probe.log`。

#### TC-M12-03 峰值内存基线（含 320 页档）

- **所属模块**：M12
- **测试目标**：单篇转换的进程峰值 RSS 随规模平缓增长，320 页档不越过告警线。
- **输入与前置条件**：同 TC-M12-02 四档 + `pdf_03_8p_complex`。
- **执行步骤**：`/usr/bin/time -l` 采峰值 RSS。
- **期望结果**：1p ≈60 MB、40p ≈83 MB、320p < 300 MB；无与页数成正比的爆炸增长。
- **实际结果**：`1p 59.7 MB / 40p 83.0 MB / 320p 236.8 MB / 30sec_docx 46.6 MB / 8p_complex 64.6 MB`（`rss_probe.log`）；perf 侧子进程峰值累计口径同为 60.9/81.6/236.7 MB，两口径一致。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：资源占用；证据 `rss_probe.log`、`perf_probe.log`。

#### TC-M12-04 阶段耗时占比与计时器口径

- **所属模块**：M12
- **测试目标**：定位真实瓶颈阶段，同时确认阶段计时的可解释性。
- **输入与前置条件**：320 页金样本、30 章节 DOCX。
- **执行步骤**：分阶段计时取中位数并算占比。
- **期望结果**：给出各阶段中位耗时与 S1 占比；不可解释的伪阶段必须排除并说明。
- **实际结果**：320p → 总 `10.707 s`，`s1=10.43 / s1_5=0.024 / s3=0.110 / s4=0.055 / package=0.083`，**S1 占 97.4%**；30sec_docx → 总 `0.027 s`，S1 占 70.4%，S2–S4 已是噪声量级。诚实口径：阶段表中 `_tail` 一项为 `-277390.336 s` 这类负巨值，源于尾部计时用了不同单调基准，**该项不参与任何占比计算**（占比只用命名列），已在《审查》3.2 与红线注释中记为度量面缺陷而非性能结论。
- **通过状态**：通过（性能结论成立；`_tail` 伪阶段属度量面待清理项）
- **优先级**：P1
- **覆盖指标**：性能定位 + 度量诚实性；证据 `perf_probe.log`、`docs/review-round17-CH-500.md` 3.2。

#### TC-M12-05 文档详情窗口化显著压缩响应体

- **所属模块**：M12
- **测试目标**：验证「按窗口取块」对响应体积与耗时的实际收益（性能优先原则下的已落地项）。
- **输入与前置条件**：同一 `doc_id`，分别取 `limit=500` 与 `limit=20`。
- **执行步骤**：测两次响应字节数与耗时，算比值。
- **期望结果**：字节量随窗口线性下降，耗时同步下降。
- **实际结果**：`limit500_bytes=324038 / 0.045 s`，`limit20_bytes=16940 / 0.002 s`，**payload 缩减 19.1×**，耗时降至 1/22（`perf_probe.log` `api` 段）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：性能收益量化；`api.py` 文档详情窗口；证据 `perf_probe.log`。

#### TC-M12-06 `doctor` 环境与依赖自检

- **所属模块**：M12
- **测试目标**：自检必须如实反映 Python/管线版本、根目录、依赖可用性与 legacy 后端。
- **输入与前置条件**：仓库 `.venv`。
- **执行步骤**：`doc2md doctor`。
- **期望结果**：rc=0，`dep_*` 全为 `ok`，`legacy_doc_backend` 标明实际后端。
- **实际结果**：`python 3.15.0 / pipeline 2.18.0 / run_profile standard / config_frozen_keys ok`，`dep_pdfplumber/docx/flask/cryptography/werkzeug = ok`，`legacy_doc_backend=libreoffice`，`console_encoding=utf-8`。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：运维自检；证据 `pipe_probe8.log`（V-PIPE-13b）、本机 `doctor` 输出。

### 3.13 MR 回归

#### TC-MR-01 全量自动化套件

- **所属模块**：MR
- **测试目标**：全部单元/集成测试通过，无回归。
- **输入与前置条件**：仓库 `.venv`；`pytest tests/ -p no:randomly --junitxml`。
- **执行步骤**：全量执行一次，解析 JUnit XML 取权威计数。
- **期望结果**：0 failed；跳过项有明确平台原因。
- **实际结果**：`tests=1349 failures=0 errors=0 skipped=2`，即 **1347 passed / 2 skipped**，用时 91.3 s；2 项跳过均为 Windows junction 平台用例（`test_junction_not_followed_by_dirguard`、`test_sweep_tmp_removes_junction_not_target`）。计数以 XML 为准（本仓库 pytest 配置抑制了摘要行）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；第二轮为 1208 passed / 2 skipped，本轮净增 141 项；证据 `full_r4.xml`。

#### TC-MR-02 门禁套件

- **所属模块**：MR
- **测试目标**：`gate` 标记套件全绿（发布门禁面）。
- **输入与前置条件**：`pytest -m gate -p no:randomly --junitxml`。
- **执行步骤**：门禁档执行并解析 XML。
- **期望结果**：0 failed、0 skipped（门禁面不允许平台性跳过充抵）。
- **实际结果**：`tests=580 failures=0 errors=0 skipped=0`，用时 40.6 s；本轮新增 `tests/test_ch500_round17.py` 38 项全部挂在 gate 档。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：门禁；第二轮 460 → 本轮 580；证据 `gate_r4.xml`。

#### TC-MR-03 语料 harness

- **所属模块**：MR
- **测试目标**：22 份语料的转换/拒收结果与 manifest 预期一致。
- **输入与前置条件**：`tests/corpus/`。
- **执行步骤**：`.venv/bin/python tests/corpus/run_corpus.py`。
- **期望结果**：22/22 PASS。
- **实际结果**：`22 passed, 0 failed`；扫描件为 `rejected E4221`，其余 21 份转换通过（含 11 篇 DOCX 与 PDF 各档）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；证据 `corpus_r3.log`。

#### TC-MR-04 语句覆盖率与安全面覆盖率

- **所属模块**：MR
- **测试目标**：整体语句覆盖率不低于 90%；安全面单列可观测。
- **输入与前置条件**：`coverage run --source=doc2md -m pytest tests/ -p no:randomly`。
- **执行步骤**：`coverage report`。
- **期望结果**：TOTAL ≥90%；`storage/repo.py` 高位；`security/perms.py` 应有改善（第二轮为 38%）。
- **实际结果**：TOTAL **7800 stmts / 743 miss，`coverage report` 打出 90%**（精确 90.47%）；收口复跑为 7797/740、精确 90.51%、打出 91%——同一档位的 .5 取整边界差异，非回归（见 2.3 与 O-h）；`storage/repo.py 257/10 = 96%`、`worker.py 376/55 = 85%`；`security/perms.py **75/45 = 40%**`——目录权限矩阵的异常分支本轮仍未测（第二轮 38%，本轮 40%，仅微升）。
- **通过状态**：通过（总体达标）；`perms.py` 覆盖不足单列为遗留缺口（见 4.1）
- **优先级**：P1
- **覆盖指标**：覆盖率；证据 `cov_report5.log`、`cov_run5.log`。

#### TC-MR-05 版本同源（D11 复验）

- **所属模块**：MR
- **测试目标**：README 首段版本、`pyproject.toml version`、`PIPELINE_VERSION` 三处必须一致（第二轮 D11 文档缺陷）。
- **输入与前置条件**：三处文件 + `tests/test_ch477_version_sync.py`。
- **执行步骤**：探针比对三处 + 运行版本同步与文档同步测试。
- **期望结果**：三处同为 2.18.0；同步测试全绿。
- **实际结果**：`PIPELINE=2.18.0 pyproject=2.18.0 readme_has=True`（X-21）；`test_ch477_version_sync.py` + `test_ch491_doc_sync.py` 共 10 项全部通过。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：第二轮 D11 复验；文档一致性；证据 `extra_probe5.log`、本机 pytest 输出。

#### TC-MR-06 静态检查与 GUI 脚本语法

- **所属模块**：MR
- **测试目标**：`ruff` 与前端内联脚本语法自检零告警。
- **输入与前置条件**：`.venv/bin/ruff`、`node`。
- **执行步骤**：`ruff check doc2md tests config ops`；抽出 `editor.html` 内联脚本后 `node --check`。
- **期望结果**：均通过。
- **实际结果**：`All checks passed!`；内联脚本 16882 字节（单 script 块），`node --check` rc=0。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：代码规范/静态面；证据 `editor_inline.js`、本机 ruff 输出。

#### TC-MR-07 黄金逐字节回归

- **所属模块**：MR
- **测试目标**：产物字节面未因 CH-500 的 18 项修复漂移。
- **输入与前置条件**：`tests/test_golden_repro.py`。
- **执行步骤**：单独运行黄金档。
- **期望结果**：全绿（逐字节一致）。
- **实际结果**：`rc=0` 全部点位通过（7 项）；配合语料 22/22 与全量 1347 passed 交叉印证。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归/字节冻结；证据 `pipe_probe8.log`（V-PIPE-20）。

### 3.14 难度语料 HD（D01–D08，本轮新增的独立真值腿）

这一组的真值**不来自产物**：`tests/corpus/hard/*.pdf` 由 `tests/qa/gen_cases.py` 生成，
`expectations.json` 里的 `tables` / `headings` / `contains` / `behavior` 是**生成时声明的构造意图**
（造了 3 列就说 3 列），因此对本管线是外部对照面，不构成 C-31 禁止的「拿当前产物当期望」。
执行器 `tests/corpus/run_hard.py`（信息层：不被 pytest 收集、有缺口退 1），逐条口径 =
`mdscore.parse_blocks()` 解析产物后的结构对账。

| 编号 | 用例 | 难度/星级 | 测试目标 | 期望（生成时声明） | 实际结果 | 状态 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-HD-01 | D01 无线框表格 | D01 ★5 | 无可见边框的表格仍被识别为 table | 1 张 3 列 × 4 行，表头「项目 / 本期金额 / 上期金额」且三行数据逐格相等；H1「一、无线框表格测试」 | 1 张 3 列 × 4 行、表头与三行数据逐格一致，标题 H1 | **通过** | P0 |
| TC-HD-02 | D02 跨页表格 | D02 ★4 | 跨页续表合一张、行数不丢 | 至少 1 张 3 列 × ≥41 行，表头「序号 / 科目 / 金额」 | 裂成 **2 张**：`table[0]` 2 列 × 37 行（表头只剩「序号/科目」）+ `table[1]` 3 列 × 5 行（第 2 页残余、表头重复）；`金额` 列整体脱落成正文一长串 `金额10011002…1036`；`ir.warnings` **0 条** | **失败**（静默结构损伤）→ B-43 | P0 |
| TC-HD-03 | D04 断词连字符 | D04 ★2 | 行尾 `-` 断词须缝合，且不吞合法连字符 | 含 `configuration` / `parameter` / `preserved` 三个完整词 | 三词全部完整命中 | **通过** | P1 |
| TC-HD-04 | D05 点线引导符 | D05 ★3 | 目录条目的「章节名 + 页码」须可读，引导符不得污染文本 | 含 `第一章 总则` / `第二章 职责` / `第三章 附则` | 产物 `\| 第一章总则 \| .................... \| 1 \|`——引导符成单元格、整页目录被判成表格、run 间空格丢失使词塌连；`ir.warnings` **0 条** | **失败**（文本保真被破坏且零告警）→ B-44 | P1 |
| TC-HD-05 | D06 数值形态 | D06 ★3 | 千分位与括号负数不得被「读成数字」而改变语义 | 1 张 3 列 × 5 行（表头「科目 / 本期 / 上期」+ 4 行数据），`(1,234)` 与 `￥8,765,432` 原样 | 3 列 × 5 行逐格一致，括号负数、千分位、百分号、货币符号全部原样保留 | **通过** | P0 |
| TC-HD-06 | D07 加密 PDF | D07 ★1 | 加密文档必须拒收且可与「结构损坏」区分 | `behavior = reject:E4151` | `Doc2mdError(E4151)`，`detail={'file': 'hard_07_encrypted.pdf', 'exc': 'PdfminerException', 'password_required': True}`——口令与损坏可区分 | **通过** | P0 |
| TC-HD-07 | D08 页眉逐页变体 | D08 ★2 | 奇偶页眉 / 章节名页眉 / 孤立页码全部剔除，且不误伤正文 | 8 个 `第 N 节 正文章节标题` 全部保留且声明 level=H1；`奇数页` / `偶数页` 不出现 | **剔除面全对**（`奇数页` / `偶数页` / `- N -` 零残留，8 个标题全部保留）；**层级为 H2** 而非声明的 H1——产品按 `s3_structure.py:1155` 的 CJK 序号映射（`章/编/部/篇→1、节→2、条→3、项→4`）判定，与生成器声明冲突 | **缺口**（真值侧待裁定，非产品缺陷）→ B-45 | P1 |

- **D03 缺位**：编号序列为 D01、D02、D04…D08，`gen_cases.py` 从未生成 D03。这是覆盖面的一处真实空洞，本册不粉饰、也不临时补一个「凑数」样本。
- **合计 4 通过 / 2 失败 / 1 缺口（共 7 例）**，`run_hard.py` 打印 `SUMMARY 4/7 难度样本全绿（信息层，未入门禁）` 并退 1（取证 `/tmp/qa-r4/run_hard_final.log`，逐条 PASS/FAIL 原文）。
- **为什么 TC-HD-02 / TC-HD-04 记「失败」而不是「缺口」**：缺口档的定义是「行为与既有口径一致、只是能力未实现」。这两条相反——`W1045` 的存在本身就承诺「表格降维必须入账」，而 D02 掉了整整一列、D05 把 `第一章 总则` 塌成 `第一章总则`，两者 `ir.warnings` 均为 **0 条**。**内容丢失 + 无声**同时违背了降维账本与文本保真两条既有承诺，属缺陷；「需人工逐页对照才能定阈值」只影响修复路径，不改变判定。
- **这一腿的价值就在反差上**：核心 95 例、门禁 580 项、语料 harness 22/22 在这两份样本上全部为绿，独立真值腿第一次跑就抓到 2 项 P0/P1 缺陷。故本轮把「真值不得来自产物」升级为口径要求，而不只是加 7 个用例。
- **另记一处工具缺陷（本轮修复）**：D07 样本长期不存在。根因是生成器把 reportlab 的暂存名与 pypdf 的终名取成同一个 `hard_07_encrypted.pdf`——写出目标即 `base` 自身，随后 `base.unlink()` 把刚生成的样本删了；而自动骨架的 `pytest.skip("未生成")` 把「资产缺失」显示成「正常跳过」。已改为暂存名 `hard_07_encrypted.raw.pdf` + `unlink(missing_ok=True)`，重烤得到 2335 字节样本并跑通 TC-HD-06。**教训：自动生成骨架里的 skip 会掩盖资产缺失，缺失必须显形**（与 O-a「PASS 但什么也没测」同病灶）。
- **自动骨架的口径边界**：`tests/qa/test_hard_corpus.py` 只核对 `contains` / `not_contains`，**不看 `tables` / `headings`**，所以 TC-HD-02 的裂表在骨架里是一条「TODO 未填断言」的 skip 而不是缺陷——若不经改写就把该骨架当门禁，会得出「难度语料无结构问题」的假结论。结构面由 `run_hard.py` 承担；把结构断言落进骨架并入门禁登记为 **B-46**（前置 B-43 / B-44）。

## 4. 覆盖缺口、未单独复测项与受限项

### 4.1 本轮登记的缺口 4 项与缺陷 2 项（全部需人工介入，均已登记 B 类）

前四项（TC-M2-08 / TC-M3-02 / TC-M7-04 / TC-HD-07）行为与既有口径一致、缺的是能力或裁定，记**缺口**；后两项（TC-HD-02 / TC-HD-04）内容损失且无声，违背既有承诺，记**失败**。两者在本表并列，因为「本轮不实施」的原因相同——都需要本机以外的人工确认或外部样本。

| 用例 | 现象 | 受限原因 | 验证方式 | 建议顺序 |
| --- | --- | --- | --- | --- |
| TC-M2-08 | DOCX 页眉/页脚正文完全不进产物，且无告警 | 需先立 `W1047` 降维告警码 + ADR；「并入正文」还是「逐页出现」是产品决策（B-33） | 新增 ADR + 语料重烤 + 人工审阅 11 篇 DOCX 产物 | **高**（用户可感知「看不见的内容没了」） |
| TC-M3-02 | `reject_on_any_bad_page` 在默认 `auto` 档不参与判定 | 尊重该键会改变线上误拒率，本机 22 篇语料样本不足（B-32） | 外部语料 + 人工抽检拒收清单；CH-501 裁定键的存废 | 中 |
| TC-M7-04 | `llm.mode=server` 在无候选块文档上不做任何探活，`fallback=false` 仍 rc=0 | 任一改法都动 `llm_stats`（进 report.html，即产物字节），需黄金重烤；「无候选算不算服务失败」是契约决策（B-42） | 起/不起 llama-server 两种环境跑同一文档，比对报告 LLM 表与退出码 | 中，与 B-33/B-37 同批重烤 |
| TC-HD-02（D02） | 跨页表格整列脱落 + 裂成两张，**零告警** | 改 S1 行装配判据（按 y 坐标聚类而非文本流顺序）后，「正确列归属」只能靠人工逐页对照真实 PDF 定夺；且必动产物字节、需 22 篇语料 + 320 页金样本重烤人工复核（B-43） | 难度样本结构断言转绿 + 语料逐字节人工复核 + `PIPELINE_VERSION` 契约级升位 | **最高**（静默结构损伤是数字类文档的头号风险） |
| TC-HD-04（D05） | 目录引导符进单元格、目录页误判为表、run 间空格丢失，**零告警** | 引导符密度 / 页码右对齐 / 行数三类阈值需人工在真实目录页上确认可误判面（正文里的点线填空不能被误判成目录），属人工裁定 + 产物重烤（B-44） | 同上：结构断言转绿 + 人工审阅误判面 | 中高 |
| TC-HD-07（D08） | 中文序号 `第 N 节` 的层级：生成器声明 H1，产品按 CJK 映射给 H2 | **真值侧冲突，不是产品缺陷**；两种口径各自自洽，且该映射当前无任何 gate 红线钉住，改哪一侧都动 `content.md` 字节（B-45） | 人工裁定 → 改正一侧 → 补「CJK 序号→层级」映射红线（现缺） | 中 |

### 4.2 未单独复测与平台受限项（不计入通过率，但不得按「已覆盖」计）

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| `security/perms.py` 语句覆盖 40% | 目录权限矩阵异常分支仍无端到端用例（TC-MR-04 已量化） | 权限红线缺少行为证据；建议 CH-501 补 pytest 面 |
| Windows junction 2 项 | 本机 macOS 无该文件系统，套件内平台性 skip | junction 防线只在 Windows 腿复证（B-40） |
| 远端 CI 六作业 / Windows 门禁 0 skipped | 需本机以外环境与 CI（B-12/B-16/B-27-remote） | 跨平台与租约并发面依赖上轮取证（HEAD `3b3e35e` 记录） |
| 真实 SMB/网络卷 | 本机无该挂载（B-40） | `ops` 面跨卷行为未在本轮复证 |
| legacy `.doc` 第二解析后端 | 本机仅 LibreOffice（B-41） | 无交叉验证器，后端可用性只有单点证据 |
| `zip_unreadable` 诊断分支（黑盒） | `chmod 000` 时 8 字节头读取先抛权限错误，本机黑盒不可达 | 已由白盒红线钉住（TC-M1-03），仍缺端到端黑盒样本 |
| PDF 标题**真实世界**人工真值 | 合成语料真值已存在（`tests/corpus/ideal/`：DOCX 11 份 111 标题 + PDF 10 份 102 标题，由 `make_ideal_docx.py` / `make_ideal_pdf.py` 从**源文件**导出，不经管线），并由 `tests/test_qa_n03_ideal.py` 46 项断言钉住；缺的是语料之外真实 PDF 的标注（B-35 重新定界，本轮收口更正） | 启发式在野外文档上的准确率仍不可证；但「F1=1.0 全是自证」的说法不成立，产品侧 `heading_pr` 的 ref 取 S1 自身 `level_hint`（`convert.py:388-398`）才是自证那一条 |
| RTL 阈值 N-5 | 未校准 | W1046 存在漏报/误报风险（TC-M2-04 只证单页可触发） |
| 作业幂等命中分支（黑盒） | 本轮未构造二次提交时序，仅套件佐证（TC-M10-20 之外） | 幂等语义由套件守住，缺端到端样本 |
| `qa_eval.py` 指标缺报告出口 | 判据已具备（ideal 真值 + 46 项断言在**全量套件**内，不在 580 门禁内），缺的是把指标汇总成报告/趋势的 runner（B-34 解除对 B-35 的依赖，改为本机可自证） | 语料 harness 仍只比 `expected/*.md` 重烤基线，质量数字不上报 |
| 表格单元格转义「单一来源」重构 | 本轮实测两侧口径一致（TC-M6-12），分裂属重构面（B-37） | 后续改任一侧需黄金重烤 |
| 页区间并行等价性 | 收益已量化（TC-M4-04），三处跨段依赖未逐项证明（B-38） | 并行开关不得发布 |

## 5. 证据索引

| 证据 | 路径 |
| --- | --- |
| API 面探针（49 项） | `/tmp/qa-r3/verify/probe_api_v.py`、`/tmp/qa-r3/verify/api_probe5.log` |
| 管线面探针（32 项） | `/tmp/qa-r3/verify/probe_pipe.py`、`/tmp/qa-r3/verify/pipe_probe8.log` |
| 扩展面探针（30 项：打包/存储/LLM/CLI/运维） | `/tmp/qa-r3/verify/probe_extra.py`、`/tmp/qa-r3/verify/extra_probe5.log` |
| 修复前基线探针（P01–P16） | `/tmp/qa-r3/r3probe/RESULTS.md`、`/tmp/qa-r3/r3probe/p03_api.py` |
| 性能（时延/阶段/并行/窗口） | `/tmp/qa-r3/verify/perf_probe.log` |
| 峰值内存 | `/tmp/qa-r3/verify/rss_probe.log` |
| 全量套件 JUnit | 轮中 `/tmp/qa-r3/full_r4.xml`（1349 collected / 0 failed / 2 skipped）→ 收口复跑 `/tmp/qa-r4/full.xml`（1390 collected / 0 failed / 8 skipped，墙钟 95.600s / per-case 求和 94.830s）→ **文档全改完后终轮 `/tmp/qa-r4/full_final.xml`（1390 / 0 failed / 8 skipped，墙钟 85.489s，项数与跳过构成与前者逐条相同）→ 末两轮 `/tmp/qa-r4/full_v4.xml`（1390 / 0 failed / 8 skipped，86.011s）、`/tmp/qa-r4/full_v5.xml`（1390 / 0 failed / 8 skipped，86.597s，本轮全部文档改动落地后复跑）** |
| 门禁套件 JUnit | 轮中 `/tmp/qa-r3/gate_r4.xml`（580 / 0 failed / 0 skipped）→ 收口复跑 `/tmp/qa-r4/gate.xml`（580 / 0 failed / 0 skipped，墙钟 44.627s / per-case 求和 44.144s）→ **文档全改完后终轮 `/tmp/qa-r4/gate_final.xml`（580 / 0 failures / 0 errors / 0 skipped，墙钟 38.155s）→ 末两轮 `/tmp/qa-r4/gate_v4.xml`（580 / 0 / 0 / 0 skipped，37.716s）、`/tmp/qa-r4/gate_v5.xml`（580 / 0 / 0 / 0 skipped，38.137s）** |
| 覆盖率 | `/tmp/qa-r3/cov_report5.log`（TOTAL 7800/743，打出 90%）→ 收口复跑 `/tmp/qa-r4/cov_report.log`（`doc2md/` 口径 45 个模块 / 7797 stmts / 740 miss，逐行求和复核 = 精确 90.51%；`perms.py` 75/45 = 40%）→ **文档全改完后终轮 `/tmp/qa-r4/cov_report2.log`（同 45 / 7797 / 740，`coverage report --include="doc2md/*"` 打出 91%）**、`.coverage` |
| 语料 harness | `/tmp/qa-r3/corpus_r3.log` → 收口复跑 `/tmp/qa-r4/corpus.log`（22 passed / 0 failed，exit 0） |
| 难度语料对账 | `tests/corpus/run_hard.py`、输出 `/tmp/qa-r4/run_hard_final.log`（`SUMMARY 4/7`，exit 1；同结果另见 `/tmp/qa-r4/run_hard2.log`）、样本 `tests/corpus/hard/`（7 份 PDF + `expectations.json`）、声明源 `tests/qa/gen_cases.py` |
| 质量度量层 | `tests/qa/mdscore.py`（十项指标）+ `tests/qa/test_mdscore.py`（34 项，`corpus` 标记）+ 报告 `work/qa/eval.md` / `eval.json`（MHS 1.0000 / TEDS 0.9874 含漏检 / BSF 1.0 / NID 1.0 / LINT 0.9306 契约类 / ASSET·IDEM·RENDER·HONEST 1.0） |
| GUI 内联脚本语法自检 | `/tmp/qa-r3/verify/editor_inline.js` |
| 表格特殊字符转义夹具 | `/tmp/qa-r3/amp_check2.docx`、`/tmp/qa-r3/ampout2/443f0507049b564e.zip` |
| 边界夹具与产物 | `/tmp/qa-r3/verify/`（`trunc.docx`、`notdocx.docx`、`unread.docx`、`enc.pdf`、`rotated_page.pdf`、`rtl_hebrew.pdf`、`literal.docx`、`pagebreak_in_para.docx`、`cand_probe.docx`、`widedir/`、`aroot/`、`extra/x*root/`） |
| 本轮修复与红线 | `docs/review-round17-CH-500.md`、`tests/test_ch500_round17.py` |

## 6. 结论

1. **第二轮 13 项偏差全部复验为通过**：D1（md 直出假成功）→ TC-M11-02；D2/D3（字面量失真、裸 HTML 直通）→ TC-M6-01/02/12；D4/D5/D6/D7（表格撕裂、嵌套表无声降维、段内分页符、字号晋升）→ TC-M5-05/06/07、TC-M5-01；D8/D9（冲突护编辑、分页口径）→ TC-M10-17、TC-M10-02；D10/D11/D12/D13 → TC-M11-04、TC-MR-05、TC-M11-03、TC-M11-05。
2. **核心面 0 契约缺陷，难度腿抓到 2 项——本轮结论因此改判**：核心 95 例 92 通过 / 3 缺口 / 0 失败（96.84%，P0 34/34、P2 11/11，P1 = 94.00%）；新增的独立真值腿 7 例 4 通过 / 1 缺口 / **2 失败**，合并 96 ÷ 102 = **94.12%**、P0 **37/38 = 97.37%**、P1 **48/53 = 90.57%**、契约缺陷 **2**。TC-HD-02（跨页表整列脱落、裂成两张）与 TC-HD-04（目录引导符成单元格、`第一章 总则` 塌成 `第一章总则`）都是**内容损失且 `ir.warnings` 为 0 条**，违背 `W1045` 降维账本与文本保真的既有承诺，按 2.1 记失败不记缺口。按 2.2「P0 失败即阻断发布」，本轮**不再出具「工程侧不阻断发布」的结论**：B-43/B-44 须发布前闭合，或经人工逐页对照真实 PDF 复核后出具书面豁免。核心面三项待裁定缺口（B-32/B-33/B-42）依旧需要人工介入，工程侧无可再自动闭合项。
3. **契约面首次全绿**：14 个契约错误码、4 个告警码全部端到端触达（第二轮为 9/14 与 2/4），其中 E4003/E4092/E4131/E4133/E4251 与 W1043/W1046 补齐了上一轮登记的缺口；E4092 需特别记入口径——它只存在于 worker/drain 路径，任何 HTTP 路由都不抛它。
4. **自动化面持续变宽，但宽度不等于强度**：全量收口复跑 **1390 collected（1382 passed / 8 skipped，第二轮 1208）**、门禁 580（第二轮 460）、语料 22/22、**难度语料 4/7（2 失败 + 1 缺口）**、覆盖率贴线 90.51%（7797/740，`coverage report` 打出 91%）、ruff 与 `node --check` 零告警、黄金逐字节 7 项全绿。8 条 skip 的构成必须说清：2 条既有 Windows junction 平台门 + 6 条难度语料骨架的 TODO-skip/xfail（**它们是显形的缺口，不是通过**）；其中 +41 项是收口阶段追加的 `tests/qa/` 度量层与骨架（`corpus` 标记，不进 gate，故门禁仍 580）。**关键对照**：这两项缺陷在 580 门禁、1390 全量、22/22 语料三条既有腿上全部为绿，只有第一次跑的独立真值腿给出红——所以本轮的收获不是「加了 7 个用例」，而是证实了「真值取自产物自身的腿，再加多也发现不了结构损失」。遗留：`security/perms.py` 仍只有 40%。
5. **性能结论可执行**：单篇耗时 97.4% 在 S1，S2–S4 已是噪声；页区间并行 2 worker 1.78×、3 worker 2.64×（产出行数逐档一致），但等价性未证，故只登记为 B-38 前置条件；已落地的窗口化实测把文档详情响应体压缩 19.1×、耗时降至 1/22。峰值内存 320 页档 236.8 MB，增长平缓。
6. **度量诚实性本轮被反复检验，包括对本报告自己的检验**：三个「PASS 但什么也没测」的探针（`/search` 翻页的空跑、`--full` 检索 0 命中仍算过、D3 回归里不严谨的裸标签子串判断）都是在本轮收口时被推翻重做的，最终证据以收紧后的断言重新取。同批还翻案了一处**前提未复核即继承**的结论（对照表 O-e）：《审查》AR-11/AR-12 与本册首稿曾写「PDF 标题无独立真值、F1=1.0 属被测规则自证」，回文件核对后不成立——`tests/corpus/ideal/` 早有 21 份不经管线的标注并由 46 项断言钉住。真正剩余的自证只有一处：产品侧 `heading_pr` 的 ref 取 S1 自己的 `level_hint`（样式直通证明，非人工正确性）；而「无法证明收益」的范围收窄为**语料之外的真实 PDF**（B-35），指标缺的只是报告出口（B-34，本机可自证）。
7. 出口取证：门禁 580 / 0 failed / 0 skipped；全量 **1390 collected（1382 passed / 8 skipped）**；语料 22/22；**难度语料 4/7**；覆盖率 `coverage report` 打出 **91%**、精确 **90.51%**（45 模块 / 7797 stmts / 740 miss）——本条首稿曾将覆盖率记作 **90%**，那是继承上一轮 `7800/743` 的**显示整数**而没回读产生它的日志，实际 `7797/740` 打出 91%、精确值跨过 .5 取整边界而**档位未变**，改判与规则见对照表 **O-h** 与 §2.4；`ruff All checks passed!`；`node --check` rc=0；`doc2md doctor` 全 `ok`。**出口发布判定＝阻断**（2.2 的「P0 失败即阻断」由 TC-HD-02 / D02 与 TC-HD-05 / D05 触发，对应 **B-43 / B-44**，须在发布前闭合或由人工逐页对照真实 PDF 后出具书面豁免）。受限项全部登记于 4.1/4.2 与 `docs/review-round17-CH-500.md` 五、，其中 **B-33/B-32/B-42 需产品决策与人工验收，B-35 需对语料之外真实 PDF 做人工标注，B-12/B-16/B-27-remote/B-40/B-41 需外部环境，B-43/B-44 需人工逐页对照真实 PDF 才能定行装配与目录识别阈值，B-45 需人工裁定中文序号的层级口径，B-46 以 B-43/B-44 为前置**；B-34 经本轮更正后**不再依赖外部**，可在 CH-501 本机自证。下一条自 **CH-501** 起。




