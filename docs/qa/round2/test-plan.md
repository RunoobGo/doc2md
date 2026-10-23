# Doc2md 转换管线功能测试文档（第二轮，含执行记录）

- 文档编号：QA-D2M-2026-10-23-R2
- 测试轮次：第二轮全面功能测试（黑盒为主 + 源码定位佐证）
- 被测版本：`PIPELINE_VERSION = 2.15.0` / `SCHEMA_VERSION = 14` / `PLAN_VERSION = 3.28`
- 执行日期：2026-10-23
- 执行环境：macOS arm64、Python 3.15.0（仓库 `.venv`）、10 逻辑 CPU、LibreOffice 可用；转换全程离线，LLM 探针只连 `127.0.0.1:8080`（未启动，用于验证失败/回退路径）
- 证据根目录：`tmp/qa-r2/`（探针脚本、夹具、产物、套件日志、覆盖率数据均保留，可逐条复核）
- 上一轮基线：`docs/qa/test-plan.md`（QA-D2M-2026-10-21，66 例）；本轮不覆盖上一轮文档
- 配套文档：《对照表》（`comparison.md`）、《偏差归纳与根因》（`deviation-analysis.md`）、《优化方案》（`optimization-plan.md`）

---

## 1. 测试范围与模块划分

被测链路：`输入识别 → S1 解析 → S1.5 门禁 → S2 分片/缝合 → S3 结构映射 → S4 渲染 → LLM 修复 → 打包 → CLI/API/存储/运维出口`。

| 模块 ID | 模块 | 对应源码 | 用例数 | 通过 | 失败 |
| --- | --- | --- | ---: | ---: | ---: |
| M1 | 输入识别与前置校验 | `pipeline/s1_parse.py::sniff`、`ir/model.py`、`security/names.py` | 5 | 5 | 0 |
| M2 | S1 解析（PDF/DOCX/legacy .doc） | `pipeline/s1_parse.py`、`pipeline/legacy_doc.py` | 9 | 8 | 1 |
| M3 | S1.5 文本层门禁 | `pipeline/s1_5_gate.py` | 4 | 4 | 0 |
| M4 | S2 分片、缝合与确定性 | `pipeline/sharding.py`、`pipeline/convert.py` | 4 | 4 | 0 |
| M5 | S3 结构映射（七类块） | `pipeline/s3_structure.py` | 9 | 6 | 3 |
| M6 | S4 Markdown 渲染 | `pipeline/s4_render.py` | 8 | 6 | 2 |
| M7 | LLM 修复、回退与 op 契约 | `llm.py`、`pipeline/convert.py` | 4 | 4 | 0 |
| M8 | 打包与产物形态 | `pipeline/convert.py::package`、`artifacts.py` | 6 | 6 | 0 |
| M9 | 存储、检索与库操作 | `storage/`、`cli.py`（docs/search） | 9 | 9 | 0 |
| M10 | API 服务、权限与编辑锁 | `api.py`、`auth.py`、`edits.py` | 9 | 7 | 2 |
| M11 | CLI 批量、退出码与格式出口 | `cli.py`、`pipeline/batch.py` | 4 | 2 | 2 |
| M12 | 运维、性能与可观测性 | `ops/`、`worker.py`、`cron.py` | 5 | 3 | 2 |
| MR | 回归（全量套件/门禁/语料/文档一致性） | `tests/`、`docs/` | 4 | 3 | 1 |
| **合计** |  |  | **80** | **67** | **13** |

## 2. 统计口径与判定标准

### 2.1 用例状态

| 状态 | 判定 |
| --- | --- |
| **通过** | 实际结果与期望结果在**状态、错误码、退出码、产物结构、关键文本/字节**上全部一致；性能用例同时满足时延与内存阈值 |
| **失败** | 任一可观测断言不一致即记失败；即使偏差影响等级为「低」也不改记为通过 |
| **未执行** | 因平台/环境无法执行；本册不计入通过率，也不以「跳过」充抵通过 |

本轮 80 条用例全部执行，无「未执行」。全量套件中的 2 项 Windows junction 用例为平台性 skip，只影响 MR-01 的套件口径，不影响用例状态。

### 2.2 优先级

| 优先级 | 定义 |
| --- | --- |
| **P0** | 核心转换正确性、数据完整性、权限边界、安全边界；失败即阻断发布 |
| **P1** | 边界/异常输入、API 契约、存储检索、批处理；失败需在发布前修复或书面豁免 |
| **P2** | 可观测性、文档一致性、结果行展示等非阻断项；可排入后续迭代，但必须登记 |

### 2.3 通过率与覆盖率公式

- 用例通过率 = 通过用例数 ÷ 已执行用例数 × 100%；本轮 = 67 ÷ 80 = **83.75%**。
- 优先级通过率：P0 = 26 ÷ 29 = **89.66%**；P1 = 39 ÷ 46 = **84.78%**；P2 = 2 ÷ 5 = **40.00%**。
- 模块覆盖率 = 有用例的模块数 ÷ 计划模块数 × 100%；本轮 13 ÷ 13 = **100%**（模块均有用例，但不等于需求全覆盖）。
- 错误码覆盖率 = 已实测触发的契约错误码数 ÷ 契约错误码总数 × 100%；契约码以 `doc2md/errors.py::CODE_INDEX` 的 14 项为准；本轮 9 ÷ 14 = **64.29%**（E4001/E4011/E4031/E4041/E4091/E4151/E4221/E5031/E5041；其中 E5031 仅在 D1 的 stderr 分支触发、状态未正确回传；未触达 E4003/E4092/E4131/E4133/E4251；E5000 为兜底基类，不计入契约码）。
- 告警覆盖率 = 已实测触发的告警码数 ÷ 告警码总数 × 100%；本轮 2 ÷ 4 = **50.00%**（W1044、W1045；W1043、W1046 本轮未单独构造）。
- 语句覆盖率 = `coverage.py` 全量套件统计；本轮 **90%**（7380 条语句，720 条未覆盖）。
- 语料覆盖率 = 语料 harness 通过数 ÷ 语料总数；本轮 **22 ÷ 22 = 100%**。

### 2.4 发布门槛（测试前设定）

| 指标 | 门槛 | 实测 | 结论 |
| --- | --- | --- | --- |
| P0 用例通过率 | 100% | 89.66% | **未达标** |
| P1 用例通过率 | ≥ 95% | 84.78% | **未达标** |
| P2 用例通过率 | ≥ 90% | 40.00% | **未达标** |
| 总体用例通过率 | ≥ 95% | 83.75% | **未达标** |
| 全量自动化套件 | 0 failed | 1208 passed / 2 skipped | 达标 |
| 语料 harness | 22/22 | 22/22 | 达标 |
| 语句覆盖率 | ≥ 90% | 90% | 达标（贴线） |

结论：自动化回归面是绿的，但功能契约面存在 13 项可复现偏差，**按测试前门槛不满足发布条件**；其中 3 项 P0（畸形标题、Markdown 字面量失真、原始 HTML/链接语法直通）需要优先处置。

## 3. 用例全集与逐条执行结果

说明：每条用例的「实际结果」均来自本轮实测；「套件佐证」表示由全量 pytest 套件中的具体测试覆盖，套件整体结果见 MR-01。命令均在仓库根目录执行，`doc2md` 指 `.venv/bin/doc2md`。

### 3.1 M1 输入识别与前置校验

#### TC-M1-01 文本型 PDF 识别与内容寻址

- **所属模块**：M1
- **测试目标**：`sniff` 正确识别 PDF；`doc_id` 由内容哈希决定，重复转换稳定。
- **输入与前置条件**：`tests/corpus/sources/pdf_01_simple_1p.pdf`；同一文件连续两次转换；另以同内容改名文件验证（见 TC-M9-07）。
- **执行步骤**：1. 连续两次 `doc2md convert ... --out perf/1p_pdf_{1,2}`；2. 比对两次结果行的 `doc_id`；3. 同内容改名后 `--skip-existing` 复核。
- **期望结果**：识别为 `pdf`；两次 `doc_id` 相同；改名不改变 `doc_id`。
- **实际结果**：两次均 `status=ok`、`doc_id=a545becc4d31a988`、`blocks=4`、`chars=66`、退出码 0；改名副本 `stale.pdf` 仍解析为同一 `doc_id`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；`sniff` PDF 分支；内容寻址；IR 约定 14；证据 `tmp/qa-r2/perf/results.json`、`tmp/qa-r2/ops/results.json`。

#### TC-M1-02 DOCX 识别与转换

- **所属模块**：M1
- **测试目标**：ZIP magic + `word/document.xml` 的 DOCX 走完整链路。
- **输入与前置条件**：`tests/corpus/sources/docx_08_large_30sections.docx`。
- **执行步骤**：`doc2md convert docx_08_large_30sections.docx --out ...`。
- **期望结果**：识别为 `docx`，退出码 0，块数与字符数非零。
- **实际结果**：`status=ok`、`doc_id=c11735fc2c2a5c12`、`blocks=270`、`chars=4569`、`pages=8`、退出码 0；耗时 0.125–0.172 s。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；DOCX 分支；`tmp/qa-r2/perf/results.json`、`tmp/qa-r2/perf/30sec_docx_1/out/*.zip`。

#### TC-M1-03 文件名消毒（emoji 源名）

- **所属模块**：M1
- **测试目标**：源文件名含 emoji 时仍能转换，落盘名经过消毒且不越界。
- **输入与前置条件**：自建 `报告😀样本.docx`（`tmp/qa-r2/run/_________________docx/`）。
- **执行步骤**：`doc2md convert '报告😀样本.docx' --out ...`。
- **期望结果**：退出码 0；产物名安全（不含 emoji/路径分隔符）；IR `source_name` 为消毒后可读名。
- **实际结果**：退出码 0；`doc_id=0ce7cddb6afbc150`、`blocks=9`、`chars=85`；Markdown 落盘名 `报告_样本.docx.md`；IR `source_name='报告_样本.docx'`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；`security/names.py`；文件名单一来源；证据 `tmp/qa-r2/run/_________________docx/stdout.txt`。

#### TC-M1-04 伪 DOCX（ZIP magic 但缺必备部件）

- **所属模块**：M1
- **测试目标**：ZIP magic 合法但无 `word/document.xml` 的 `.docx` 不得冒充 DOCX。
- **输入与前置条件**：`tmp/qa-r2/fixtures/fake.docx`（普通 zip 改名）。
- **执行步骤**：`doc2md convert fake.docx --out ...`。
- **期望结果**：E4151，退出码 4，detail 带 magic。
- **实际结果**：`{"status":"error","code":"E4151","message":"unsupported magic for fake.docx","detail":{"magic":"504b030414000000"}}`，退出码 4。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E4151；`tmp/qa-r2/run/fake_docx/stderr.txt`。

#### TC-M1-05 零字节文件

- **所属模块**：M1
- **测试目标**：空文件快速失败，不进入解析器。
- **输入与前置条件**：`tmp/qa-r2/fixtures/zero.bin`（0 字节）。
- **执行步骤**：`doc2md convert zero.bin --out ...`。
- **期望结果**：E4151，退出码 4，detail `magic` 为空串。
- **实际结果**：`{"status":"error","code":"E4151","message":"unsupported magic for zero.bin","detail":{"magic":""}}`，退出码 4。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；E4151；`tmp/qa-r2/run/zero_bin/stderr.txt`。

### 3.2 M2 S1 解析（PDF / DOCX / legacy .doc）

#### TC-M2-01 简单 PDF 的标题/段落解析

- **所属模块**：M2
- **测试目标**：文本型 PDF 的标题层级与段落边界正确进入 IR。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：`doc2md convert pdf_01_simple_1p.pdf --out ...`，读取 `ir.json`。
- **期望结果**：1 个 H1、1 个 H2、2 个正文段落，共 4 块。
- **实际结果**：`b1 heading level=1`、`b2 heading level=2`、`b3/b4 paragraph`，共 4 块；`chars=66`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；S1 PDF 分支；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M2-02 40 页 PDF 解析规模

- **所属模块**：M2
- **测试目标**：中等规模 PDF 的块数/字符数稳定，无截断或爆炸。
- **输入与前置条件**：`tests/corpus/sources/pdf_08_large_40p.pdf`。
- **执行步骤**：两次 `doc2md convert`，比对块数、字符数与耗时。
- **期望结果**：两次块数/字符数一致；退出码 0；耗时 < 3 s。
- **实际结果**：两次均 `blocks=560`、`chars=12803`、退出码 0；耗时 0.386/0.394 s。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流/规模；PDF 解析；`tmp/qa-r2/perf/results.json`。

#### TC-M2-03 30 节 DOCX 解析

- **所属模块**：M2
- **测试目标**：多节 DOCX 的段落、标题与分页信息完整。
- **输入与前置条件**：`docx_08_large_30sections.docx`。
- **执行步骤**：`doc2md convert`，核对 `pages/blocks/chars`。
- **期望结果**：退出码 0；`pages=8`；块数与字符数与基线一致。
- **实际结果**：`pages=8`、`blocks=270`、`chars=4569`；两次运行耗时 0.125/0.172 s。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；DOCX 节/分页；`tmp/qa-r2/perf/results.json`。

#### TC-M2-04 legacy `.doc` 经 LibreOffice 解析

- **所属模块**：M2
- **测试目标**：旧版二进制 `.doc` 能经外部后端转换，标题层级进入 IR。
- **输入与前置条件**：`tmp/qa-r2/legacy/sample_simple.doc`；LibreOffice 可用。
- **执行步骤**：`doc2md convert sample_simple.doc --out ...`。
- **期望结果**：退出码 0；输出含 H1/H2/H3 与正文。
- **实际结果**：`doc_id=0c3e6e7f296e5d2a`、`blocks=6`、退出码 0；Markdown 含 `# 1. 一级标题`、`## 1. 二级标题`、`### 1. 三级标题`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：兼容性；`pipeline/legacy_doc.py`；`tmp/qa-r2/legacy/root/out/0c3e6e7f296e5d2a.zip`。

#### TC-M2-05 单元格内段落边界映射为 `<br>`

- **所属模块**：M2
- **测试目标**：DOCX 单元格内多个段落用 `<br>` 连接，`w:br`/`w:tab` 不丢字。
- **输入与前置条件**：`tmp/qa-r2/run/cell_br_docx/` 夹具（含 `<br>` 字面、`w:br`）。
- **执行步骤**：转换后读取表格块文本与 warnings。
- **期望结果**：单元格段落边界为 `<br>`；`w:br`/`w:tab` 转为换行/制表不粘连；无异常告警。
- **实际结果**：表格行含 `字面<br>标记`；`w:br` 内容未粘连；`meta.warnings=[]`。管道/反斜杠组合的转义问题单列 TC-M5-09。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；O-05 单元格段落边界；`tmp/qa-r2/run/cell_br_docx/out/5e94e47305807b40.zip`。

#### TC-M2-06 段内分页符的位置

- **所属模块**：M2
- **测试目标**：`w:br type="page"` 出现在段落中间时，IR 在**分页符位置**切分前后文。
- **输入与前置条件**：`tmp/qa-r2/fixtures/pagebreak_in_para.docx`（同一段：分页前文字 + 分页符 + 分页后文字）。
- **执行步骤**：转换后读取 `ir.json` 的块序。
- **期望结果**：分页前文字、`page_break`、分页后文字三段按序排列。
- **实际结果**：`b2` 为合并段落 `同一段内分页前文字。同一段内分页后文字。`，`b3` 才是 `page_break`，`b4` 为下一段；分页点落在整段之后。
- **通过状态**：**失败**（偏差 D6）
- **优先级**：P1
- **覆盖指标**：边界；页面结构；`tmp/qa-r2/run/pagebreak_in_para_docx/out/df6b0fff24537a44.zip`。

#### TC-M2-07 加密 PDF 的解析失败路径

- **所属模块**：M2
- **测试目标**：需要密码的 PDF 不得被当成正常文本 PDF，错误码可判定。
- **输入与前置条件**：`tmp/qa-r2/fixtures/encrypted.pdf`（密码保护）。
- **执行步骤**：`doc2md convert encrypted.pdf --out ...`。
- **期望结果**：E4151，退出码 4，detail 标注需要密码。
- **实际结果**：`{"code":"E4151","message":"unreadable pdf: encrypted.pdf","detail":{"exc":"PdfminerException","password_required":true}}`，退出码 4。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E4151；`tmp/qa-r2/run/encrypted_pdf/stderr.txt`。

#### TC-M2-08 行尾空格几何回归（O-01）

- **所属模块**：M2
- **测试目标**：PDF 换行处的行尾空格不被吞掉/误拼，O-01 修复不回归。
- **输入与前置条件**：`tests/corpus/sources/pdf_11_wrap_space_1p.pdf`。
- **执行步骤**：语料 harness 运行。
- **期望结果**：PASS，输出与目标一致。
- **实际结果**：`PASS pdf_11_wrap_space_1p.pdf`；语料总 22/22。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；O-01；`tmp/qa-r2/corpus.log`。

#### TC-M2-09 文本框/图片容器不破坏正文顺序

- **所属模块**：M2
- **测试目标**：DOCX 文本框、图片等容器不导致正文丢失或块序错乱。
- **输入与前置条件**：`tests/corpus/sources/docx_06_textbox_image.docx`。
- **执行步骤**：语料 harness 运行。
- **期望结果**：PASS；正文顺序与目标一致。
- **实际结果**：`PASS docx_06_textbox_image.docx`；语料总 22/22。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；`_docx_textbox_paragraphs`；`tmp/qa-r2/corpus.log`。

### 3.3 M3 S1.5 文本层门禁

#### TC-M3-01 正常文本 PDF 通过门禁

- **所属模块**：M3
- **测试目标**：有文本层的 PDF 不被误拒，门禁统计进入报告。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：转换后读取 `report.html` 的 S1.5 段。
- **期望结果**：`passed=True`；样本页统计有 chars/avg_chars。
- **实际结果**：`S1.5 gate mode=auto passed=True sampled=1`；页 1 `chars=63`、`avg_chars` 正常；报告可见。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；S1.5；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M3-02 扫描件/无文本层 PDF 拒收

- **所属模块**：M3
- **测试目标**：全空文本层按 E4221 拒收，不产出空文档。
- **输入与前置条件**：`tests/corpus/sources/pdf_06_scanned_1p.pdf`。
- **执行步骤**：语料 harness 运行。
- **期望结果**：`rejected E4221`，退出码 3。
- **实际结果**：`PASS pdf_06_scanned_1p.pdf: rejected E4221`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：异常输入；E4221；`tmp/qa-r2/corpus.log`。

#### TC-M3-03 空 DOCX 拒收

- **所属模块**：M3
- **测试目标**：无正文 DOCX 由门禁拒收，stats/reasons 可审计。
- **输入与前置条件**：`tmp/qa-r2/fixtures/empty.docx`。
- **执行步骤**：`doc2md convert empty.docx --out ...`。
- **期望结果**：E4221、退出码 3；`reasons=["all_pages_empty"]`。
- **实际结果**：`status=rejected`、`code=E4221`、退出码 3；`total_pages=1`、`empty_pages=1`、`avg_chars=0.0`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；E4221；`tmp/qa-r2/run/empty_docx/stderr.txt`。

#### TC-M3-04 仅空段落的 DOCX 拒收

- **所属模块**：M3
- **测试目标**：有段落对象但全为空文本时仍按空文档处理。
- **输入与前置条件**：`tmp/qa-r2/fixtures/empty_para.docx`。
- **执行步骤**：同 TC-M3-03。
- **期望结果**：E4221、退出码 3、`all_pages_empty`。
- **实际结果**：`status=rejected`、`code=E4221`、退出码 3；`sampled_pages=[1]`、`empty_pages=1`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；E4221；`tmp/qa-r2/run/empty_para_docx/stderr.txt`。

### 3.4 M4 S2 分片、缝合与确定性

#### TC-M4-01 320 页 PDF 自动两阶段分片

- **所属模块**：M4
- **测试目标**：超过 `max_pages_per_shard=300` 时自动切两片，产物带 W1044。
- **输入与前置条件**：`large_320p.pdf`（320 页）；默认 `s2.mode=auto`。
- **执行步骤**：`doc2md convert large_320p.pdf --out ... --format zip`；读取 `meta`。
- **期望结果**：`sharded=true`、`shard_count=2`、warnings 含 W1044；退出码 0。
- **实际结果**：`sharded=true`、`shard_count=2`、`pages=320`、`warnings=["W1044"]`、`blocks=5770`；第一片 `shard.index=0,start=0,end=300`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：边界；S2 两阶段；W1044；`tmp/qa-r2/perf/320p_pdf_1/out/01d8f64f3fea1a18.zip`。

#### TC-M4-02 单页文档不分片

- **所属模块**：M4
- **测试目标**：单页输入保持单片，`shard_count=1`。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：转换后读取 `meta`。
- **期望结果**：`sharded=false`、`shard_count=1`、`shard={index:0,start:0,end:1}`。
- **实际结果**：`sharded=false`、`shard_count=1`、`shard={index:0,start:0,end:1}`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；分片阈值下界；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M4-03 重复运行的产物确定性

- **所属模块**：M4
- **测试目标**：同输入重复运行，内容产物逐字节一致，`doc_id` 稳定。
- **输入与前置条件**：1 页 PDF 与 320 页 PDF 各跑两次。
- **执行步骤**：比较 zip 内 `content.md`/`ir.json`/`report.html` 的 SHA-256；同时比较 zip 容器 hash。
- **期望结果**：内部三件产物逐字节一致；`doc_id` 一致。zip 容器可因归档时间戳不同而字节不同。
- **实际结果**：1p 与 320p 的 `content.md`/`ir.json`/`report.html` 两次运行 SHA-256 分别完全一致；`doc_id` 一致；1p zip 容器也逐字节一致，320p zip 容器 hash 不同（内部内容相同）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归/确定性；`tmp/qa-r2/perf/`；`sha256` 复核记录。

#### TC-M4-04 非法分片配置快速拒绝

- **所属模块**：M4
- **测试目标**：`max_pages_per_shard=0/-1` 必须在配置加载期拒绝，不得进入死循环。
- **输入与前置条件**：临时配置 `[s2] max_pages_per_shard = 0` 与 `= -1`。
- **执行步骤**：`doc2md convert pdf_01_simple_1p.pdf --config bad.toml`。
- **期望结果**：E4001、退出码 2、消息含合法区间 `[1, 100000]`。
- **实际结果**：两次均退出码 2；`config error: s2.max_pages_per_shard 必须在 [1, 100000] 区间内，当前为 0 / -1`；无产物、无挂起。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；配置校验；E4001；`tmp/qa-r2/edge/badshard/`。

### 3.5 M5 S3 结构映射

#### TC-M5-01 编号标题层级

- **所属模块**：M5
- **测试目标**：编号标题按字号/层级正确映射为 H1/H2。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：读取 IR 中 heading 块的 `level`。
- **期望结果**：`1. Doc2md 简单转换测试`=H1；`1.1 摘要`=H2。
- **实际结果**：`b1 level=1`、`b2 level=2`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；`_heading_level` 编号分支；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M5-02 无编号 14pt 提示框文字的标题判定（N-8）

- **所属模块**：M5
- **测试目标**：N-8 的「无编号、字号 ≥ 正文+3.5、长度≤60、无句末标点→H2」不得把提示框/标注文字误判为标题。
- **输入与前置条件**：`tmp/qa-r2/fixtures/big_subtitle.pdf`：H1「第一章 系统概述」、10.5pt 正文基准、14pt 无编号提示框文字「以下一行是 14pt 的提示框文字，没有句末标点」。
- **执行步骤**：转换后读取 IR/Markdown 的块类型与 level。
- **期望结果**：提示框文字保持 `paragraph`（或由版面模型判定为标注块），不得升级为 H2。
- **实际结果**：该行输出为 `## 以下一行是 14pt 的提示框文字，没有句末标点`，IR 为 `heading level=2`；前后正文均为 paragraph。
- **通过状态**：**失败**（偏差 D7）
- **优先级**：P0
- **覆盖指标**：边界；N-8；`_heading_level` 无编号分支；`tmp/qa-r2/run/big_subtitle_pdf/out/2307b038ce79efc9.zip`。

#### TC-M5-03 紧凑列表（O-04）

- **所属模块**：M5
- **测试目标**：无序/有序列表项连续输出，项目符号与编号不被误并段。
- **输入与前置条件**：`docx_02_lists.docx`。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：`- 项目甲/乙/丙` 与 `1./2./3.` 各自成项，无空段插入。
- **实际结果**：输出 `- 项目甲`、`- 项目乙`、`- 项目丙`、`1. 第一项`、`2. 第二项`、`3. 第三项`，无多余空段；退出码 0。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；O-04 紧凑列表；`tmp/qa-r2/edge/lists/out/*.md`。

#### TC-M5-04 等宽/代码块映射

- **所属模块**：M5
- **测试目标**：等宽/代码段映射为 fenced code block，内部缩进与换行保留。
- **输入与前置条件**：`docx_07_code_quote.docx`。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：代码段为三反引号围栏，函数体三行保持缩进。
- **实际结果**：输出围栏代码块，内容为 `def convert(path):` / `    result = pipeline(path)` / `    return result`；围栏外段落正常。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；`_code_fence`；`tmp/qa-r2/edge/codequote/out/*.md`。

#### TC-M5-05 引用块映射

- **所属模块**：M5
- **测试目标**：引用样式段落映射为 `>` blockquote，连续行合并。
- **输入与前置条件**：`docx_07_code_quote.docx`。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：引用段落每行带 `> `，不与正文粘连。
- **实际结果**：`> 这是一段引用文本，应输出为 blockquote。`，前后均有空行。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；`quote` 分支；`tmp/qa-r2/edge/codequote/out/*.md`。

#### TC-M5-06 GFM 表格组装

- **所属模块**：M5
- **测试目标**：普通表格输出合法 GFM 表头/分隔行/数据行，列数对齐。
- **输入与前置条件**：`docx_03_tables.docx`。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：3 列表格分隔行为 `| --- | --- | --- |`；数据行可被 GFM 解析。
- **实际结果**：输出 `| 项目 | 数量 | 单价 |`、`| --- | --- | --- |`、`| 苹果 | 3 | 5.00 |`、`| 香蕉 | 2 | 3.50 |`；退出码 0。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；`_gfm_table`；`tmp/qa-r2/edge/tables/out/*.md`。

#### TC-M5-07 合并单元格拍平与 W1045

- **所属模块**：M5
- **测试目标**：`gridSpan`/`vMerge` 展开为空格位，表级 `flattened_cells` 非零并触发 W1045。
- **输入与前置条件**：`docx_11_complex_tables.docx`（5 张含合并的复杂表）。
- **执行步骤**：转换后读取 `meta.warnings` 与 table 块 `flags`。
- **期望结果**：warnings 含 W1045；相关表 `flattened_cells >= 1`；列数保持网格宽度。
- **实际结果**：`warnings=["W1045"]`；b5/b9/b13/b17/b21 五张表 `flags.flattened_cells=1`；退出码 0。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；O-05；W1045；`tmp/qa-r2/edge/merge/out/9bc69a6e8033c5b4.zip`。

#### TC-M5-08 嵌套表格拍平的告警完整性

- **所属模块**：M5
- **测试目标**：外层表格单元格内的嵌套表被拍平进本格时，必须按 `_docx_cell_text` 文档口径留 W1045（诚实暴露降维）。
- **输入与前置条件**：`tmp/qa-r2/fixtures/nested_table.docx`（外层 1×2，右侧格内嵌 1×2 内表）。
- **执行步骤**：转换后读取 IR 表格文本、`flags` 与 `meta.warnings`。
- **期望结果**：拍平发生时有 W1045；`flattened_cells` 计入嵌套表带来的降维。
- **实际结果**：表格输出 `| 外层左 | 内层A<br>内层B |`，内表被静默拍平；`meta.warnings=[]`、表块无 `flattened_cells`。
- **通过状态**：**失败**（偏差 D5）
- **优先级**：P1
- **覆盖指标**：边界；嵌套表；W1045 完整性；`tmp/qa-r2/run/nested_table_docx/out/b7bc4eaa7a1ab17d.zip`。

#### TC-M5-09 表格单元格反斜杠-管道转义

- **所属模块**：M5
- **测试目标**：单元格字面量 `反斜杠\|组合` 在 GFM 中必须仍是一个单元格，渲染后文本为 `反斜杠\|组合`。
- **输入与前置条件**：`tmp/qa-r2/fixtures/cell_br.docx`，第 2 行第 3 列源文本 `反斜杠\|组合`（一个反斜杠 + 管道）。
- **执行步骤**：转换后读取表格行；按 GFM 反斜杠转义规则推算单元格边界与最终文本。
- **期望结果**：Markdown 源中该格应为 `反斜杠\\\|组合`（转义反斜杠 + 转义管道），列数保持 3。
- **实际结果**：输出为 `| 反斜杠\\|组合 |`（两个反斜杠后直接跟管道）；`\\` 渲染为字面反斜杠后，管道成为列分隔符，行被拆成 4 个单元格，原单元格语义被破坏。
- **通过状态**：**失败**（偏差 D4）
- **优先级**：P1
- **覆盖指标**：边界；`_gfm_table`；`tmp/qa-r2/run/cell_br_docx/out/5e94e47305807b40.zip`。

### 3.6 M6 S4 Markdown 渲染

#### TC-M6-01 粗体/斜体标记

- **所属模块**：M6
- **测试目标**：IR `marks.bold/italic` 渲染为 `**...**`/`*...*`，不叠加错位。
- **输入与前置条件**：`报告😀样本.docx`（含整段加粗/斜体段落）。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：加粗段 `**整段加粗**`；斜体段 `*整段斜体*`。
- **实际结果**：两段分别输出 `**整段加粗**` 与 `*整段斜体*`，无嵌套污染。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；`_inline` marks；`tmp/qa-r2/run/_________________docx/out/*.md`。

#### TC-M6-02 删除线默认关闭

- **所属模块**：M6
- **测试目标**：默认配置 `preserve_strike=false` 时不得输出 `~~`，且不触发渲染红线。
- **输入与前置条件**：`报告😀样本.docx`（含删除线段落）。
- **执行步骤**：默认转换，检查 Markdown 是否含 `~~`。
- **期望结果**：无 `~~`；正文以纯文本输出。
- **实际结果**：输出 `整段删除线（默认输出不保留删除线语法）`，无 `~~`；`assert_render_contract` 通过。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：配置分支；渲染红线；`tmp/qa-r2/run/_________________docx/out/*.md`。

#### TC-M6-03 外部链接渲染

- **所属模块**：M6
- **测试目标**：`http/https/mailto/ftp` 链接渲染为 `[text](url)`，标签与目标转义正确。
- **输入与前置条件**：`docx_04_links_notes.docx`。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：`[示例网站](https://example.com)`。
- **实际结果**：输出 `[示例网站](https://example.com)`；脚注/尾注以正文文本保留；退出码 0。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；`is_external`/`_link_label`/`_link_dest`；`tmp/qa-r2/edge/links/out/*.md`。

#### TC-M6-04 内部链接降级为纯文本

- **所属模块**：M6
- **测试目标**：内部链接（DOCX 书签/PDF GoTo）默认与 anchor 档均不产出 `](#dest)` 死链。
- **输入与前置条件**：`tests/test_m5_m6c.py::test_internal_link_downgrades_to_plain_by_default` 与 `test_internal_link_anchor_optin`。
- **执行步骤**：全量 pytest 运行（MR-01）。
- **期望结果**：两档输出均为 `见第 3 节`，不含 `](#`/`#page-`；IR 保留 `links[].dest`。
- **实际结果**：两条测试通过；`render(ir)` 与 `render(ir, link_internal="anchor")` 均为纯文本，红线断言通过。
- **通过状态**：通过（套件佐证）
- **优先级**：P1
- **覆盖指标**：契约；CH-464；`tests/test_m5_m6c.py:35-53`。

#### TC-M6-05 分页符在 Markdown 中的表示

- **所属模块**：M6
- **测试目标**：GFM 无分页语法时，`page_break` 只保留在 IR/审阅面，Markdown 不落字节，也不误删正文。
- **输入与前置条件**：`tmp/qa-r2/fixtures/pagebreak_in_para.docx`。
- **执行步骤**：比较 IR 与 Markdown。
- **期望结果**：IR 有 `page_break` 块；Markdown 无分页标记，正文前后均保留。
- **实际结果**：IR `b3 type=page_break`；Markdown 无分页标记，前后文字均在（但分页位置错误，见 TC-M2-06/D6）。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：格式边界；`_render_block` page_break 分支；`tmp/qa-r2/run/pagebreak_in_para_docx/out/*.zip`。

#### TC-M6-06 正文中的 Markdown 字面量

- **所属模块**：M6
- **测试目标**：源文档中的 `#`、`>`、`[x](url)`、`**x**`、`1.` 等**字面文本**必须按纯文本保留，不得被解释为 Markdown 结构。
- **输入与前置条件**：`tmp/qa-r2/fixtures/md_literals.docx`（六段普通文字，分别以这些符号开头/包裹）。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：六段均保持段落语义，符号被转义或包裹为字面量；不新增 H1/引用/链接/加粗/列表。
- **实际结果**：`# 这不是标题…` 变成 H1；`> 这不是引用…` 变成引用；`[点击这里](https://example.com)…` 变成链接；`**这不是加粗**` 变成粗体；`1. 这不是列表…` 在 IR 中已被映射为 `list_item`。仅第一段普通正文保持段落。
- **通过状态**：**失败**（偏差 D2）
- **优先级**：P0
- **覆盖指标**：边界；字面量安全；S3 列表启发式 + S4 转义；`tmp/qa-r2/run/md_literals_docx/out/b655aee500d0fcd0.zip`。

#### TC-M6-07 原始 HTML 与 `javascript:` 字面量

- **所属模块**：M6
- **测试目标**：正文中的原始 HTML 标签与 `[x](javascript:...)` 不得原样进入 Markdown 供下游渲染器执行。
- **输入与前置条件**：`tmp/qa-r2/html/html_probe.docx`（三段：`<script>alert(1)</script>`、`<img src=x onerror=alert(2)>`、`[点我](javascript:alert(3))`）。
- **执行步骤**：转换并读取 Markdown。
- **期望结果**：HTML 标签被转义（或明确标注为不安全并净化）；`javascript:` 语法不被渲染为可点击链接。
- **实际结果**：Markdown 原样包含 `<script>alert(1)</script>`、`<img src=x onerror=alert(2)>`、`[点我](javascript:alert(3))` 三段；`warnings=[]`。注：真实 hyperlink 的 `javascript:` 会被 `is_external` 白名单挡住，风险来自正文文本中的字面 Markdown/HTML。
- **通过状态**：**失败**（偏差 D3）
- **优先级**：P0
- **覆盖指标**：安全边界；HTML/Markdown 注入面；`tmp/qa-r2/html/root/out/aeb4ad48af2ba8d0.zip`。

#### TC-M6-08 md 直出与 zip 内 `content.md` 字节等价

- **所属模块**：M6
- **测试目标**：`--format both` 时直出 md 与 zip 内 `content.md` 逐字节一致。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`，`--format both`。
- **执行步骤**：比较两个文件的 SHA-256 与长度。
- **期望结果**：字节完全一致。
- **实际结果**：均为 666 字节，SHA-256 均为 `5e8b4158321d9622…`，比较结果 `True`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：产物契约；CH-491；`tmp/qa-r2/mdcheck/out/`。

### 3.7 M7 LLM 修复、回退与 op 契约

#### TC-M7-01 offline 模式零网络、零 op

- **所属模块**：M7
- **测试目标**：默认 offline 模式不发起网络请求，统计明示 `source=offline`。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`；默认配置。
- **执行步骤**：经 `work` 入库后 `doc2md docs show`，读取 `meta.llm_stats`。
- **期望结果**：`source=offline`、`batches=0`、`accepted/effective=0`。
- **实际结果**：`source=offline`、`model=offline`、`batches=0`、`response_ops=0`、`accepted_ops=0`、`effective_ops=0`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；offline 分支；`tmp/qa-r2/ops/root`（docs show）。

#### TC-M7-02 server 不可用且禁止回退

- **所属模块**：M7
- **测试目标**：`--llm-mode server --llm-no-fallback` 且服务不可达时明确失败，不得静默产出。
- **输入与前置条件**：`tmp/qa-r2/llm/long_heading.docx`；`127.0.0.1:8080` 未监听。
- **执行步骤**：`doc2md convert long_heading.docx --llm-mode server --llm-no-fallback`。
- **期望结果**：E5041、退出码 4、detail 指明 stage/http/port。
- **实际结果**：`{"status":"error","code":"E5041","message":"local llm service unavailable (http)","detail":{"stage":"http","bind":"127.0.0.1","port":8080}}`，退出码 4。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E5041；`tmp/qa-r2/llm/run_nofb/`。

#### TC-M7-03 server 不可用时回退 offline

- **所属模块**：M7
- **测试目标**：`fallback=true` 时服务不可用仍以 offline 完成转换，并在统计中标记回退。
- **输入与前置条件**：同上，不加 `--llm-no-fallback`。
- **执行步骤**：`doc2md convert long_heading.docx --llm-mode server`。
- **期望结果**：退出码 0；产物完整；回退可审计。
- **实际结果**：退出码 0；`blocks=3`、`chars=90`；zip 正常生成；未出现 E5041。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：回退路径；`tmp/qa-r2/llm/run_fb/`。

#### TC-M7-04 长标题候选不被错误改写

- **所属模块**：M7
- **测试目标**：送入 LLM 边界判断的长标题在服务不可用/回退时保持原层级与文本，不产生空转或误改。
- **输入与前置条件**：`long_heading.docx`（超 60 字 H1）。
- **执行步骤**：回退模式转换后读取 IR。
- **期望结果**：标题仍为 `heading level=1`，文本逐字不变；后续段落顺序不变。
- **实际结果**：`b1 heading level=1` 文本保持原文；`b2/b3 paragraph` 顺序不变；共 3 块。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：LLM op 安全；`tmp/qa-r2/llm/run_fb/out/efe9169ec8b08882.zip`。

### 3.8 M8 打包与产物形态

#### TC-M8-01 zip 产物成员完整

- **所属模块**：M8
- **测试目标**：zip 含 `content.md`、`ir.json`、`report.html` 三件，且位于 `doc_id/` 前缀下。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`，默认 `--format zip`。
- **执行步骤**：`unzip -l` 与 `zipfile.namelist()`。
- **期望结果**：三件齐全，入口名以 `a545becc4d31a988/` 开头。
- **实际结果**：`a545becc4d31a988/content.md`（666 B）、`ir.json`（1595 B）、`report.html`（2483 B）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：正常流；`artifacts.artifact_entry`；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M8-02 front-matter 元数据

- **所属模块**：M8
- **测试目标**：Markdown front-matter 与 IR meta 一致，含 doc_id/schema/pipeline/shard 信息。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：解析 `content.md` 首部 JSON 与 `ir.json.meta`。
- **期望结果**：`doc_id=a545becc4d31a988`、`schema_version=14`、`pipeline_version=2.15.0`、`shard`/`shard_stats` 完整。
- **实际结果**：front-matter 含上述字段；`shard={index:0,start:0,end:1}`；`shard_stats` 六项齐全。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：产物契约；N-1；`tmp/qa-r2/mdcheck/out/`。

#### TC-M8-03 报告页质量摘要（N-6）

- **所属模块**：M8
- **测试目标**：`report.html` 展示质量指标，且明确「缺真值显示 —，不是满分」。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：读取 `report.html` 的 Quality 段。
- **期望结果**：含 blocks/chars/headings/table_col.rate/heading_pr 指标；缺真值显示 `—`。
- **实际结果**：可见 `Quality` 段：`blocks=4`、`chars=66`、`headings (md)=2`；`table_col.rate=—`、`heading_pr.p/r/f1=—`（PDF 无真值）；说明文字与实现一致。
- **通过状态**：通过
- **优先级**：P2
- **覆盖指标**：N-6；审阅面；`tmp/qa-r2/mdcheck/out/a545becc4d31a988.zip`。

#### TC-M8-04 CLI 纯产物流程不写库

- **所属模块**：M8
- **测试目标**：`convert` 是纯产物出口，不得隐式创建/写入 `state.sqlite`。
- **输入与前置条件**：全新 root，`--format both`。
- **执行步骤**：转换后检查 root 下是否出现 `state.sqlite`。
- **期望结果**：无数据库文件；只有 out 产物。
- **实际结果**：`db_exists=false`；zip 与 md 均落盘。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：CLI 与存储边界；`tmp/qa-r2/ops/results.json::convert_both`。

#### TC-M8-05 `--format both` 双产物

- **所属模块**：M8
- **测试目标**：both 同时保留 zip 与 md，且 md 来自 zip 内 `content.md`。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`。
- **执行步骤**：`doc2md convert ... --format both`。
- **期望结果**：结果行同时含 `zip` 与 `md`；两文件存在。
- **实际结果**：结果行 `zip=.../a545becc4d31a988.zip`、`md=.../pdf_01_simple_1p.pdf.md`；两文件均存在，md 666 B。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：格式出口；CH-491；`tmp/qa-r2/ops/results.json::convert_both`。

#### TC-M8-06 zip 完整性与成员命名

- **所属模块**：M8
- **测试目标**：产物 zip 无损坏，成员路径不越界，可直接被标准工具读取。
- **输入与前置条件**：TC-M8-01 的 zip。
- **执行步骤**：`unzip -t`。
- **期望结果**：`No errors detected`；成员均在 `doc_id/` 前缀下。
- **实际结果**：`No errors detected in compressed data`；3 个成员均为 `a545becc4d31a988/...`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：安全/完整性；`unzip -t` 证据。

### 3.9 M9 存储、检索与库操作

#### TC-M9-01 队列入库与排空

- **所属模块**：M9
- **测试目标**：`work --enqueue` 建任务、`work --once` 排空，状态与计数一致。
- **输入与前置条件**：全新 root；`pdf_01_simple_1p.pdf`。
- **执行步骤**：1. `doc2md work --enqueue ...`；2. `doc2md work --once`；3. `doc2md work --status`。
- **期望结果**：queued=1 → done=1，failed=0，drift=0。
- **实际结果**：`{"enqueued":1,"rejected":0,"counts":{"queued":1}}`；`{"drained":1,"done":1,"failed":0,"rejected":0,"drift":0}`；`{"done":1}`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；任务状态机；`tmp/qa-r2/ops/results.json`。

#### TC-M9-02 文档列表字段与分页

- **所属模块**：M9
- **测试目标**：`docs list` 返回 doc_id/source_name/blocks/total/limit/has_more 等稳定字段。
- **输入与前置条件**：TC-M9-01 入库 1 篇。
- **执行步骤**：`doc2md docs list`。
- **期望结果**：`total=1`、`limit=50`、`has_more=false`；文档 `blocks=4`。
- **实际结果**：`total=1`、`offset=0`、`limit=50`、`has_more=false`；文档 `doc_id=a545becc4d31a988`、`source_name=pdf_01_simple_1p.pdf`、`blocks=4`、`status=done`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；O-07；`tmp/qa-r2/ops/results.json::docs_list`。

#### TC-M9-03 文档详情块分页

- **所属模块**：M9
- **测试目标**：`docs show --limit 2` 只返回前 2 个块，`total` 仍为真实块数。
- **输入与前置条件**：同一 4 块文档。
- **执行步骤**：`doc2md docs show a545becc4d31a988 --limit 2`。
- **期望结果**：`blocks=[b1,b2]`；元数据完整。
- **实际结果**：`blocks=2`，ID 为 `b1`、`b2`；`meta.llm_stats.source=offline`；退出码 0。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；O-07；`tmp/qa-r2/ops/docs_show_limit2.json`。

#### TC-M9-04 FTS 基本检索

- **所属模块**：M9
- **测试目标**：中文词在 FTS 索引中可命中，返回块级 snippet。
- **输入与前置条件**：同一文档（含「摘要」）。
- **执行步骤**：`doc2md search 摘要 --root ...`。
- **期望结果**：`total=1`、`mode=fts`、命中 `b2`。
- **实际结果**：`total=1`、`has_more=false`、`mode=fts`；命中 `b2`，`snip="1.1 [摘要]"`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；FTS；`tmp/qa-r2/ops/results.json::search_cli`。

#### TC-M9-05 FTS 尾部失配与 `--full` 兜底

- **所属模块**：M9
- **测试目标**：超长段落中仅尾部出现的词，FTS 不命中时 `--full` 能命中。
- **输入与前置条件**：`tmp/qa-r2/searchtail/tail2.docx`（8406 字，词仅在段尾，出现 1 次）；已入库到 `searchtail/root2`。
- **执行步骤**：1. `doc2md search 尾部探针 --root .../root2`；2. 加 `--full` 再查。
- **期望结果**：普通检索 `total=0`；`--full` `total=1`、`mode=full`。
- **实际结果**：普通检索 `total=0`、`mode=fts`、`match="尾部 部探 探针"`；`--full` `total=1`、`mode=full`、`match="%尾部探针%"`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：边界；FTS 截断/兜底；`tmp/qa-r2/searchtail/root2_normal.json`、`root2_full.json`。

#### TC-M9-06 检索负数 limit

- **所属模块**：M9
- **测试目标**：负数 limit 在入口拒绝，不得传给 SQL。
- **输入与前置条件**：任意已入库文档。
- **执行步骤**：`doc2md search 摘要 --limit -1`。
- **期望结果**：E4001、退出码 2、消息 `limit must be >= 0`。
- **实际结果**：退出码 2；`{"code":"E4001","message":"limit must be >= 0"}`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E4001；`tmp/qa-r2/ops/results.json::search_cli_negative_limit`。

#### TC-M9-07 `--skip-existing` 内容命中

- **所属模块**：M9
- **测试目标**：内容哈希已入库时跳过转换；同内容改名不误判为新文档。
- **输入与前置条件**：同一内容已入库；另备改名副本 `stale.pdf`。
- **执行步骤**：1. 对原文件 `--skip-existing`；2. 对改名副本 `--skip-existing`。
- **期望结果**：两次均 `status=skipped`、`doc_id` 相同、退出码 0；不重复转换。
- **实际结果**：原文件与 `stale.pdf` 均 `status=skipped`、`doc_id=a545becc4d31a988`、`blocks=0`、退出码 0；zip 指向既有产物。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：O-08；内容寻址；`tmp/qa-r2/ops/results.json::skip_existing*`。

#### TC-M9-08 删除文档的级联与审计

- **所属模块**：M9
- **测试目标**：删除文档时 blocks/events/audit 级联清理，产物文件删除，删除记录可查。
- **输入与前置条件**：TC-M9-01 的已编辑文档（4 块、2 条编辑审计）。
- **执行步骤**：`POST /docs/<id>/delete`（带 confirm）→ `GET /docs/<id>` → `GET /deletions`。
- **期望结果**：删除计数与库内行数一致；删除后 GET 404；deletions 有 1 条。
- **实际结果**：`deleted={audit:3, blocks:4, events:5}`、`files=[.../a545becc4d31a988.zip]`；GET 返回 404 E4041；`deletions` 共 1 条。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：数据完整性；级联；`tmp/qa-r2/api/results.json`。

#### TC-M9-09 zip 丢失后的 `docs export` 重建

- **所属模块**：M9
- **测试目标**：zip 被外部删除后，`docs export` 能由库内 IR 重建 zip 与 md，manifest 哈希正确。
- **输入与前置条件**：已入库文档；手工删除 `out/<doc_id>.zip`。
- **执行步骤**：`doc2md docs export <doc_id> --out ...`。
- **期望结果**：zip 重建；导出 md 与清单 sha256 校验通过。
- **实际结果**：`zip_deleted=true`、`zip_rebuilt=true`、`count=1`、`sha_ok=true`；导出文件 `pdf_01_simple_1p.pdf.md`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：恢复路径；O-07；`tmp/qa-r2/ops/results.json::docs_export_after_zip_delete`。

### 3.10 M10 API 服务、权限与编辑锁

#### TC-M10-01 健康检查免鉴权

- **所属模块**：M10
- **测试目标**：`/healthz` 无需 token，返回版本与文档计数。
- **输入与前置条件**：临时 root，serve 监听 127.0.0.1。
- **执行步骤**：`GET /healthz`。
- **期望结果**：200；`ok=true`；`pipeline=2.15.0`；`docs` 计数存在。
- **实际结果**：200；`{"docs":0,"ok":true,"pipeline":"2.15.0","ts":"2026-10-23T11:38:04.293+00:00"}`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：正常流；API 基础；`tmp/qa-r2/api/results.json::healthz`。

#### TC-M10-02 缺少 token

- **所属模块**：M10
- **测试目标**：未携带 `X-Doc2md-Token` 时拒绝提交任务。
- **输入与前置条件**：serve 运行中；无 token。
- **执行步骤**：`POST /jobs`。
- **期望结果**：401；E4011；`missing token`。
- **实际结果**：401；`{"code":"E4011","message":"missing token"}`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：安全边界；E4011；`tmp/qa-r2/api/results.json::jobs_missing_token`。

#### TC-M10-03 reader 角色越权提交

- **所属模块**：M10
- **测试目标**：reader 只能读/导出，不能提交转换任务。
- **输入与前置条件**：reader token；合法 PDF 路径。
- **执行步骤**：`POST /jobs`（reader token）。
- **期望结果**：403；E4031；detail 指明需要 editor。
- **实际结果**：403；`{"code":"E4031","message":"action submit_job requires editor","detail":{"required":"editor","role":"reader"}}`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：RBAC；E4031；`tmp/qa-r2/api/results.json::jobs_reader_forbidden`。

#### TC-M10-04 非法 mode

- **所属模块**：M10
- **测试目标**：`mode` 仅允许 auto/single/two_phase，非法值入口拒绝。
- **输入与前置条件**：admin token；`{"mode":"str"}`。
- **执行步骤**：`POST /jobs`。
- **期望结果**：400；E4001；消息列出允许值。
- **实际结果**：400；`{"code":"E4001","message":"mode must be auto/single/two_phase","detail":{"mode":"str"}}`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E4001；`tmp/qa-r2/api/results.json::jobs_bad_mode`。

#### TC-M10-05 输入文件不存在

- **所属模块**：M10
- **测试目标**：路径不存在时返回 404，不进入转换。
- **输入与前置条件**：admin token；`/no/such/file.pdf`。
- **执行步骤**：`POST /jobs`。
- **期望结果**：404；E4041。
- **实际结果**：404；`{"code":"E4041","message":"path not found: /no/such/file.pdf"}`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：异常输入；E4041；`tmp/qa-r2/api/results.json::jobs_missing_file`。

#### TC-M10-06 编辑后重复转换护编辑

- **所属模块**：M10
- **测试目标**：块 revision>1 时重复转换默认返回 E4091，不静默覆盖用户编辑。
- **输入与前置条件**：已入库文档；PATCH b1 修改文本。
- **执行步骤**：PATCH → 再次 `POST /jobs` 同内容。
- **期望结果**：PATCH 成功 revision=2；重转返回 `status=conflict`、E4091、`edited_blocks=1`。
- **实际结果**：PATCH 200，`revision=2`；重转 202，`status=conflict`、`code=E4091`、`edited_blocks=1`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：数据完整性；E4091；`tmp/qa-r2/api/results.json`。

#### TC-M10-07 同值 PATCH 的幂等性

- **所属模块**：M10
- **测试目标**：PATCH 提交与当前完全相同的文本应是 no-op：不递增 revision、不产生编辑审计、不影响后续重转。
- **输入与前置条件**：已入库文档；b2 当前 revision=1、文本未变。
- **执行步骤**：PATCH b2，`text` 与现值逐字相同；随后重转。
- **期望结果**：revision 保持 1（或明确返回 `unchanged=true`）；重转不因 b2 冲突；审计不新增。
- **实际结果**：PATCH 200，`revision=2`；重转 `status=conflict`、E4091、`edited_blocks=2`（b1 与 b2 均计为已编辑）。同值写入被当成真实编辑。
- **通过状态**：**失败**（偏差 D8）
- **优先级**：P1
- **覆盖指标**：边界；`edits.patch_block`；`tmp/qa-r2/api/results.json::patch_same_value`。

#### TC-M10-08 回滚与编辑审计

- **所属模块**：M10
- **测试目标**：回滚生成新 revision，审计链条完整可查。
- **输入与前置条件**：TC-M10-06 的已编辑文档（审计 2 条）。
- **执行步骤**：`GET /audit` → `POST /docs/<id>/blocks/b1/rollback` → `GET /docs/<id>`。
- **期望结果**：审计有记录；回滚后 revision 递增；GET 返回回滚后 revision。
- **实际结果**：审计 `total=2`；回滚返回 `revision=3`；GET b1 `revision=3`。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：审计/回滚；`tmp/qa-r2/api/results.json`。

#### TC-M10-09 `limit=0` 的跨出口语义

- **所属模块**：M10
- **测试目标**：同为「列表/检索 limit」，0 的含义应在 `/docs`、`/jobs`、`/search` 与 CLI `docs list` 间一致。
- **输入与前置条件**：已入库 1 篇 4 块文档、1 条任务。
- **执行步骤**：分别请求 `GET /docs/<id>?limit=0`、`GET /jobs?limit=0`、`GET /search?q=摘要&limit=0`、`doc2md docs list --limit 0`。
- **期望结果**：四者采用同一契约（统一返回空集，或统一拒绝 0）；不得一处当默认、一处当零行。
- **实际结果**：`/docs` 返回 4 块（按默认 500 处理）；`/jobs` 返回 0 条；`/search` 返回 0 命中；CLI `docs list` 返回 `docs=[]`、`total=1`、`has_more=true`。三种语义并存。
- **通过状态**：**失败**（偏差 D9）
- **优先级**：P1
- **覆盖指标**：契约一致性；`api._window` 与各端点；`tmp/qa-r2/api/results.json`、`tmp/qa-r2/ops/results.json`。

### 3.11 M11 CLI 批量、退出码与格式出口

#### TC-M11-01 批量转换与串行结果行同构

- **所属模块**：M11
- **测试目标**：批量并行与串行结果行字段一致；单条拒收按最严重退出码聚合。
- **输入与前置条件**：`tests/corpus/sources/` 下 22 份 PDF/DOCX（含扫描件）。
- **执行步骤**：`doc2md convert <dir> --out ... --workers 4`；与串行单文件结果行比较键集合。
- **期望结果**：21 条 ok、1 条 rejected E4221；退出码 3；结果行键与串行一致。
- **实际结果**：`21 ok + 1 rejected`（`pdf_06_scanned_1p.pdf`，E4221）；退出码 3；键集合 `file/status/doc_id/blocks/chars/zip` 与串行一致（附加 `code/message/gate` 仅失败行有）。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：O-03；批量/串行同构；`tmp/qa-r2/batch4.jsonl`、`tmp/qa-r2/serial1.json`。

#### TC-M11-02 `--format md` 的产物语义

- **所属模块**：M11
- **测试目标**：md 模式只保留 md、移除 zip，且 md 与 zip 内原文件字节一致。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`；`--format md`。
- **执行步骤**：转换后列目录，比较 md 长度与既有 zip 内 `content.md`。
- **期望结果**：仅 md 存在；666 B；字节一致。
- **实际结果**：out 下仅 `pdf_01_simple_1p.pdf.md`（666 B），zip 已移除；与 mdcheck zip 内 `content.md` 字节一致。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：格式出口；CH-491；`tmp/qa-r2/edge/mdonly2/`。

#### TC-M11-03 md 直出失败不得静默成功

- **所属模块**：M11
- **测试目标**：`--format md` 落盘失败时，结果状态/退出码必须反映失败，不得仍报 `status=ok`。
- **输入与前置条件**：输出目录中预先创建名为 `pdf_01_simple_1p.pdf.md` 的**目录**，使 `os.replace` 必然抛 `IsADirectoryError`。
- **执行步骤**：`doc2md convert pdf_01_simple_1p.pdf --out ... --format md`。
- **期望结果**：非零退出码（业务错误 4）；结果状态为 error/failed；stderr 带 E5031；zip 不被误删。
- **实际结果**：退出码 **0**；stdout 结果行 `status=ok` 且无 `md` 字段；stderr 另打 `{"file":"pdf_01_simple_1p.pdf","status":"md_emit_failed","code":"E5031","message":"IsADirectoryError"}`。机器调用方据 stdout/rc 会误判成功。
- **通过状态**：**失败**（偏差 D1）
- **优先级**：P1
- **覆盖指标**：异常输入；CLI 退出码契约；`tmp/qa-r2/edge/md_collide2/`。

#### TC-M11-04 md 模式结果行的 zip 字段

- **所属模块**：M11
- **测试目标**：md 模式已删除 zip 时，结果行不得再声称一个不存在的 `zip` 路径。
- **输入与前置条件**：正常 `--format md` 转换。
- **执行步骤**：读取结果行并 `ls` 其 `zip` 字段。
- **期望结果**：省略 `zip`，或显式标注 `zip_removed=true`；字段与实际产物一致。
- **实际结果**：结果行含 `zip=.../a545becc4d31a988.zip`，但目录中只有 `pdf_01_simple_1p.pdf.md`，该 zip 已被删除。
- **通过状态**：**失败**（偏差 D12）
- **优先级**：P2
- **覆盖指标**：结果契约；`tmp/qa-r2/edge/mdonly2/stdout.txt`。

### 3.12 M12 运维、性能与可观测性

#### TC-M12-01 备份演练全链路

- **所属模块**：M12
- **测试目标**：复制活动库、快照、外部加密、模拟丢失、轮转、恢复、完整性校验、逐表比对全部成功。
- **输入与前置条件**：已入库 1 篇文档；设置 `DOC2MD_BACKUP_PASSPHRASE`；外部备份目录可写。
- **执行步骤**：`doc2md backup drill`。
- **期望结果**：8 个步骤全部 ok；`integrity_check=ok`；13 张表逐表 match。
- **实际结果**：8 个步骤均 `ok=true`；`integrity_check.result=ok`；`tables=13`、`match=true`；快照 `state-20261023T113318Z-97d02045.sqlite`，加密文件 mode `0o600`。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：恢复能力；加密/权限；`tmp/qa-r2/ops/results.json::backup_drill`。

#### TC-M12-02 cron 日常维护

- **所属模块**：M12
- **测试目标**：`cron --daily` 依次完成日报、tmp 清理、日志轮转检查、事件/任务清理、FTS 对账。
- **输入与前置条件**：已入库 1 篇文档。
- **执行步骤**：`doc2md cron --daily --root ...`。
- **期望结果**：全部步骤 ok；日报落盘；无异常。
- **实际结果**：6 个步骤均 ok；日报 `reports/daily-2026-10-23.json`（docs=1）；tmp 清理 0；轮转未触发（未达 50MB）；事件/任务清理 0；FTS 对账 0 修复。
- **通过状态**：通过
- **优先级**：P1
- **覆盖指标**：运维闭环；`tmp/qa-r2/ops/results.json::cron_daily_no_backup`。

#### TC-M12-03 四档输入的时延与内存

- **所属模块**：M12
- **测试目标**：1 页/40 页/320 页 PDF 与 30 节 DOCX 的时延、峰值 RSS 在可接受范围，且重复运行稳定。
- **输入与前置条件**：`pdf_01_simple_1p.pdf`、`pdf_08_large_40p.pdf`、`large_320p.pdf`、`docx_08_large_30sections.docx`；每档跑 2 次，`/usr/bin/time -l`。
- **执行步骤**：串行执行 8 次转换，记录 wall time 与 max RSS。
- **期望结果**：1p ≤1 s、40p ≤3 s、320p ≤30 s、30 节 DOCX ≤3 s；峰值 RSS ≤512 MB；退出码全 0。
- **实际结果**：1p 0.155/0.195 s、59.4/60.2 MB；40p 0.386/0.394 s、81.4/81.5 MB；320p 10.091/10.116 s、234.8/235.2 MB；30 节 DOCX 0.125/0.172 s、46.9 MB。全部达标。附注：235 MB 高于文档中的 Linux 参考值 176 MB，属 macOS 运行时差异，仍低于 512 MB 上限。
- **通过状态**：通过（附平台差异说明）
- **优先级**：P1
- **覆盖指标**：性能；NFR；`tmp/qa-r2/perf/results.json`。

#### TC-M12-04 work 日志的路径完整性

- **所属模块**：M12
- **测试目标**：`work.jsonl` 中的源路径应足以定位文件，不得从中间截断成无效路径。
- **输入与前置条件**：`work --enqueue` 使用长绝对路径 `/Volumes/fx/Object/Doc2md/tests/corpus/sources/pdf_01_simple_1p.pdf`。
- **执行步骤**：排空后读取 `root/logs/work.jsonl` 的 `job_done.file`。
- **期望结果**：完整路径，或至少保留可定位的 basename + 稳定哈希；不得出现 `/lumes/...` 这类断头路径。
- **实际结果**：日志为 `"file": "lumes/fx/Object/Doc2md/tests/corpus/sources/pdf_01_simple_1p.pdf"`；开头 `/Vo` 被 `[-64:]` 截掉，路径无法直接使用。日志未泄漏正文。
- **通过状态**：**失败**（偏差 D13）
- **优先级**：P2
- **覆盖指标**：可观测性；`worker.py` 路径截断；`tmp/qa-r2/ops/root/logs/work.jsonl`。

#### TC-M12-05 损坏的 settings override 启动错误

- **所属模块**：M12
- **测试目标**：`settings_override.json` 语法损坏时，应按配置错误（E4001/退出码 2）报告文件与位置，而不是内部错误。
- **输入与前置条件**：root 下写入 `{bad json`。
- **执行步骤**：`doc2md serve --root ... --port 18099`。
- **期望结果**：退出码 2；E4001；消息指出 override 文件与 JSON 解析失败。
- **实际结果**：进程立即退出，退出码 **5**；`{"code":"E5000","message":"unexpected: JSONDecodeError"}`；无文件路径/行号。
- **通过状态**：**失败**（偏差 D10）
- **优先级**：P1
- **覆盖指标**：异常输入；配置校验；`api.create_app`；`tmp/qa-r2/badsettings/`。

### 3.13 MR 回归

#### TC-MR-01 全量自动化套件

- **所属模块**：MR
- **测试目标**：全部单元/集成测试通过，无回归。
- **输入与前置条件**：仓库 `.venv`；`pytest`。
- **执行步骤**：`pytest -q`（带覆盖率）。
- **期望结果**：0 failed；跳过项有明确平台原因。
- **实际结果**：`1208 passed, 2 skipped, 1 warning in 93.31s`；退出码 0；2 skipped 为 Windows junction 平台用例。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；`tmp/qa-r2/pytest-coverage.log`、`pytest-coverage.rc`。

#### TC-MR-02 门禁套件与语句覆盖率

- **所属模块**：MR
- **测试目标**：`gate` 标记套件全绿；语句覆盖率不低于 90%。
- **输入与前置条件**：同上。
- **执行步骤**：`pytest -m gate -q`；`coverage report`。
- **期望结果**：0 failed；覆盖率 ≥90%。
- **实际结果**：`460 passed, 750 deselected in 38.18s`；语句覆盖率 **90%**（7380 stmts / 720 miss）。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：门禁/覆盖率；`tmp/qa-r2/pytest-gate.log`、`tmp/qa-r2/.coverage`。

#### TC-MR-03 语料 harness

- **所属模块**：MR
- **测试目标**：22 份语料的转换/拒收结果与 manifest 预期一致。
- **输入与前置条件**：`tests/corpus/`。
- **执行步骤**：语料 harness。
- **期望结果**：22/22 PASS。
- **实际结果**：`22 passed, 0 failed`；其中扫描件为 `rejected E4221`，其余 21 份转换通过。
- **通过状态**：通过
- **优先级**：P0
- **覆盖指标**：回归；`tmp/qa-r2/corpus.log`。

#### TC-MR-04 版本文档一致性

- **所属模块**：MR
- **测试目标**：README 标注版本应与 `pyproject.toml` 与 `doctor` 输出一致。
- **输入与前置条件**：`README.md`、`pyproject.toml`、`doc2md doctor`。
- **执行步骤**：比对三处版本。
- **期望结果**：三处均为 2.15.0。
- **实际结果**：README 首段为 **v2.12.1**；`pyproject.toml version=2.15.0`；`doctor pipeline=2.15.0`。
- **通过状态**：**失败**（偏差 D11）
- **优先级**：P2
- **覆盖指标**：文档一致性；`README.md:3`。

---

## 4. 覆盖缺口与未单独复测项

本轮 80 条用例覆盖 13 个模块，但以下面仍未被本轮用例直接触达，不能按「已覆盖」计：

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| 错误码 E4003（文件名消毒失败） | 未构造消毒后仍不安全的名称（macOS 下难以稳定触发） | 文件名红线缺少端到端证据 |
| 错误码 E4092（源被新内容替代） | 未构造同路径并发替换时序 | 并发覆盖语义未验证 |
| 错误码 E4131/E4133（超大文件/超宽目录） | 未构造超限输入 | 资源边界只有代码审查证据 |
| 错误码 E4251（乐观锁冲突） | 未构造过期 `base_revision` 的 PATCH | 编辑冲突的另一半未验证 |
| 告警 W1043（跨片表格不合并） | 未构造跨片表格 | 分片缝合告警未验证 |
| 告警 W1046（旋转/RTL 版式） | 未构造旋转页/RTL 夹具 | O-10 未在本轮复测 |
| O-09 页眉页脚变体 | 未单独构造；由全量套件/语料间接覆盖 | 未计入本轮用例覆盖 |
| O-11 行内跨度级链接 | 已登记 ADR，明确延后 | 块级链接模型的能力边界 |
| O-12 legacy 内置解析器 | 已登记延后，当前依赖 LibreOffice | 无 LibreOffice 环境不可用 |
| PDF 标题真值 | 仍缺人工标注全集 | `heading_pr` 在 PDF 上显示 `—`，无法量化标题准确率 |
| RTL 阈值 N-5 | 未校准 | W1046 可能漏报/误报 |
| 安全/权限代码覆盖 | `security/perms.py` 38% | 目录权限矩阵的异常分支未测 |

这些缺口不改变本轮 67/80 的用例通过率，但会限制「全面」的外推范围；优化方案中单列补齐计划。

## 5. 证据索引

| 证据 | 路径 |
| --- | --- |
| 全量套件与覆盖率 | `tmp/qa-r2/pytest-coverage.log`、`tmp/qa-r2/pytest-coverage.rc`、`tmp/qa-r2/.coverage` |
| 门禁套件 | `tmp/qa-r2/pytest-gate.log` |
| 语料 harness | `tmp/qa-r2/corpus.log` |
| API 探针（26 项） | `tmp/qa-r2/api/results.json`、`tmp/qa-r2/probe_api.py` |
| CLI/存储/运维探针（24 项） | `tmp/qa-r2/ops/results.json`、`tmp/qa-r2/probe_ops.py` |
| 性能（8 次） | `tmp/qa-r2/perf/results.json`、`tmp/qa-r2/probe_perf.py` |
| 边界夹具与产物 | `tmp/qa-r2/fixtures/`、`tmp/qa-r2/run/` |
| Markdown 安全探针 | `tmp/qa-r2/html/` |
| 表格字面量探针 | `tmp/qa-r2/run/cell_br_docx/`、`tmp/qa-r2/run/nested_table_docx/` |
| 分页探针 | `tmp/qa-r2/run/pagebreak_in_para_docx/` |
| 搜索尾部探针 | `tmp/qa-r2/searchtail/` |
| LLM 回退探针 | `tmp/qa-r2/llm/` |
| 损坏配置探针 | `tmp/qa-r2/badsettings/` |
| md 直出失败探针 | `tmp/qa-r2/edge/md_collide2/`、`tmp/qa-r2/edge/mdonly2/` |

## 6. 结论

1. 自动化回归面健康：全量 1208 passed、门禁 460 passed、语料 22/22、语句覆盖率 90%。
2. 功能契约面不健康：80 条用例中 13 条失败，总体通过率 83.75%，低于测试前设定的 95% 门槛；P0 通过率 89.66%，低于 100% 门槛。
3. 13 项失败均已复现并定位到源码，分为：渲染字面量/安全边界 2 项（D2/D3）、结构映射 3 项（D4/D5/D7）、CLI 契约 2 项（D1/D12）、API 契约 2 项（D8/D9）、解析 1 项（D6）、配置/可观测性 2 项（D10/D13）、文档 1 项（D11）。
4. 最优先的根因是：IR 文本没有区分「字面文本」与「Markdown 语法」，S4 没有转义层；其次是结构性降维缺少告警（嵌套表），以及 CLI/API 边界把失败当成功或把 0 当默认。

