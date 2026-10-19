# Doc2md v2.12.0 产品审查：功能全面性与文档一致性（CH-491）

- 审查人：许清楚（产品经理）
- 审查时点代码基线：`PIPELINE_VERSION = 2.12.0`、`PLAN_VERSION = 3.26`、`SCHEMA_VERSION = 14`、`DB_VERSION = 9`（`doc2md/__init__.py:14-16`、`doc2md/storage/db.py`）
- 审查性质：**只读审查**。本报告是唯一新建文件，未改动任何源码与既有文档。
- 审查原则：功能全面 > 性能；离线单机姿态不可破；建议须可落在现有架构上。

## 0. 取证方法与口径

本报告的全部结论均带可复核证据。取证命令（本机 macOS arm64 / `.venv`）：

| 取证项 | 命令 / 位置 | 实测结果 |
| --- | --- | --- |
| 包内规模 | `wc -l doc2md/*.py doc2md/*/*.py` | **41 个 `.py` / 12,387 行** |
| 配置键权威键集 | `len(doc2md.config.loader.SPEC)` | **64**（`DEFAULT_TOML` 扁平键同为 64） |
| `doctor` 输出键数 | `.venv/bin/python -c "cli.main(['doctor'])"` → `len(json)` | **39**（固定 35 + 依赖探测 4；`llm_server` 为条件项） |
| 红线层收集数 | `pytest -m gate --collect-only -q`（28 个文件） | **402** |
| 全量收集数 | `pytest --collect-only -q`（47 个文件） | **998** |
| 综合语料 | `find tests/corpus/sources -type f` / `expected/*.md` | 21 份源 / 20 份目标 |
| CI 超时上界 | `.github/workflows/ci.yml:36,60,82,116` | merge-gate 6 / lint 10 / windows-suite 25 / full-suite 30 |

「文档说的」一列一律给 `文件:行号`；「代码实际」一列一律给 `文件:行号`。两者都可直接在仓库里复核。

---

## 1. 产品现状

### 1.1 一句话定位

> **Doc2md 是一台机器上的「文档 → Markdown → 可检索可编辑的本地知识库」离线管线：以 402 条红线断言和逐字节等价纪律换来了高度可信的转换与数据安全，但「知识库」这一半目前只有 HTTP 面可达，CLI 用户转完之后看不见、拿不到、查不了自己的内容。**

前半句（转换 + 安全 + 门禁）已达生产可用；后半句（读取面 + 可观测 + 文档体系）是本次审查发现的主要缺口带。

### 1.2 能力全景表

成熟度：★★★★★ 可直接依赖 / ★★★★☆ 可用但有已知边角 / ★★★☆☆ 能用但覆盖不完整 / ★★☆☆☆ 有明显失真或缺口

| # | 能力域 | 现有能力（代码事实） | 成熟度 | 主要证据 |
| --- | --- | --- | --- | --- |
| 1 | PDF 文本层转换 | 分块重开（`s1.chunk_pages`）、双栏检测与分栏阅读顺序、页眉页脚/页码剔除、首行缩进几何分段、七类块（heading/paragraph/list_item/table/code/quote/page_break）、代码块缩进几何重建 | ★★★★☆ | `pipeline/s1_parse.py:189-285`、`:1058`；`pipeline/s3_structure.py:201-289`；`ir/model.py:20` |
| 2 | DOCX 转换 | 修订「接受后」口径（`w:ins`/`w:del`）、脚注尾注独立抽行、单元格多段落边界、文本框/`sdt`/`smartTag`/`fldSimple`、显式 `Heading N` 层级、`numPr` 编号、`gridSpan`/`vMerge` | ★★★★☆ | `pipeline/s1_parse.py:14-21,810-1010`；12.10 v3.20/v3.23 行 |
| 3 | 旧版 `.doc` | OLE sniff + LibreOffice / Word 后端，临时文件只落私有 temp，无后端按 E4151 拒收 | ★★★☆☆ | `pipeline/legacy_doc.py:183`；`config/default_toml.py:23-24` |
| 4 | 输入门禁 | S1.5 文本层体检（采样页密度/坏页）、magic 415、目录过宽 413、磁盘门禁 413 | ★★★★★ | `pipeline/s1_5_gate.py:303`；`errors.py:31-101` |
| 5 | 大文档分片 | S2 粗切片 + 重叠去重 + 缝合，跨片表格留 W1043/W1044 | ★★★★☆ | `pipeline/sharding.py:362`；`errors.py:107-108` |
| 6 | LLM 本地修补 | offline 占位 / 本地 llama-server 双模式、5 op 白名单 + 多道校验、注入归因 | ★★★☆☆ | `llm.py:820`；`config/default_toml.py:98-112`；12.7「真实本地模型服务」行 |
| 7 | 批量与队列 | 目录有界进程池、watch 稳定窗口、`tasks` 队列 + 常驻 `work`、异步入队 202 | ★★★★☆ | `pipeline/batch.py:252`；`worker.py:581`；`security/watcher.py:149` |
| 8 | 检索 | FTS5 + CJK unigram/bigram 辅助列、bm25 排序、snippet、LIKE 复核、`full` 模式 | ★★★☆☆ | `storage/fts.py:115`；`storage/repo.py:17,306-376` |
| 9 | 编辑与版本 | 块级 PATCH + 乐观锁（E4251）+ 回滚 + 审计流水 + E4091 护编辑 | ★★★★★ | `edits.py:116`；`api.py:828-860` |
| 10 | 产物 | 内容寻址 `doc_id`、zip（`content.md`/`ir.json`/`report.html`）、原子 `.part` 落盘、API 流式 markdown | ★★★★☆ | `pipeline/convert.py::package`；`api.py:782-826` |
| 11 | 数据安全 | `work/` 700、文件名双闸消毒、`doc_id` 只哈希内容、日志 sha256 脱敏、删除墓碑 + 级联 + 删前快照 | ★★★★★ | `security/names.py`；`api.py:85-109`；`ir/model.py:23-25` |
| 12 | 备份运维 | AES-256-GCM v1/v2 容器、外置卷、14 天轮出、`backup drill` 8 步闭环、`backup restore` | ★★★★☆ | `ops/backup.py:744`；`ops/drill.py:238`；`cli.py:114-128` |
| 13 | 定时与常驻 | launchd + Windows 任务计划四档；崩溃自恢复 + 可停性 | ★★★★☆ | `ops/launchd/*.plist`；`ops/windows/*.xml`；12.10 v3.14 行 |
| 14 | 可观测性 | 三档 JSONL 自持轮转、launchd 捕获档就地截断、`/events` 总线、`/jobs`、`doctor` 自检 | ★★★☆☆ | `ops/logging.py:260`；`cron.py:324`；`cli.py:644-735` |
| 15 | 质量体系 | 402 红线 / 998 全量、黄金样本逐字节可复现、21 份综合语料、CI 双工作流 + Windows 腿 | ★★★★★ | `pytest.ini`；`.github/workflows/ci.yml`；`tests/corpus/` |
| 16 | **CLI 读取面** | **无** `docs list/show/export`、**无** `search`、无 md 直出、无增量跳过 | ★☆☆☆☆ | `cli.py:746-749`（`COMMANDS` 仅 8 项） |
| 17 | **文档体系** | 唯一权威主文档 1,856 行；但附录 A/B/C/F/G 与代码有多处失真，`docs/` 16 份 md 里 7 份零索引、5 份零引用 | ★★☆☆☆ | 见 §3、§4 |

---

## 2. 功能缺口清单

编号规则：`F` = 功能缺口。排序按用户价值（「一个真实用户拿这个工具处理自己文档」的受阻程度）降序。
「验收口径」列全部写成可测断言；「外部依赖」列 = 是否需要真机 / 真实语料 / 人工（本轮只登记不实施，见 §5）。

| 编号 | 类别 | 优先级 | 用户价值 | 建议方案要点 | 验收口径（可测） | 外部依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| **F-01** | 检索/体验 | **P0** | 转完之后**看不到**自己的库。`COMMANDS`（`cli.py:746-749`）只有 serve/cron/convert/watch/work/tokenizer/doctor/backup，没有任何读库入口；要列出、查看、检索、导出文档必须起服务 + 签 token + curl。工具定位是「本机个人知识库」，知识库面却只有 HTTP 可达 | 新增 `doc2md docs list|show|export` 与 `doc2md search`：复用 `storage/repo` 的 `search`/`list_blocks`/`get_doc` 与 `pipeline/convert.package` 的 zip 读路径，**只读 DB + 只读 zip，零网络、不新增依赖**；全部出口走 `_jline()`（CH-475 纪律） | ① `doc2md docs list --json` 输出 `{doc_id, source_name, pages, blocks, updated_at}`，条数 == `SELECT COUNT(*) FROM docs`；② `doc2md search -q 例外 --limit N` 的 hits 与 API `GET /search` 同参结果集逐条相同；③ `docs export <doc_id> --out X.md` 产物与 zip 内 `content.md` 逐字节相同；④ 三条 gate 红线 + 变异（改 limit 语义 / 改字节）转红 | 否，本机可验 |
| **F-02** | 体验 | **P0** | 转一个 PDF 想拿到 `.md`，只能拿到 `out/<doc_id>.zip`——用户不认识 doc_id，还得 unzip 才知道里面有几个文件。`convert` 无任何 `--format` 开关 | `convert --format zip\|md\|both`（默认 `zip`，保既有契约与 G6 基线）；md 文件名取消毒后 `source_name`，碰撞时后缀 `-<doc_id 前 8>`；不改动 zip 内任何字节 | ① `--format md` 产出 `<sanitized>.md`，与 zip 内 `content.md` 逐字节相同；② `--format zip`（缺省）产物与当前 HEAD 逐字节相同（G6 等价，31 个黄金样本不重烤）；③ 恶意文件名在 `--format md` 下同样走 `assert_name_safe`；④ 4 条 gate 红线 | 否 |
| **F-03** | 可观测 | **P0** | 被拒收/失败时**不知道下一步该干什么**。E4221 只回 `detail{mode,sampled_pages,reasons,stats}`（`errors.py:67`），用户看不懂「采样页密度低」意味着「这是扫描件，本工具不做 OCR」；E4151 把「结构损坏」「加密带口令」「非 PDF」三件事压成同一个 415 | 错误响应增 `hint` 字段：静态文案表（放 `errors.py`，按 code 索引），只写「可能原因 + 建议动作」，**不含任何正文与路径**（NFR-20）；README 增「错误码 → 处置」四列表（码 / 含义 / 常见原因 / 建议动作） | ① E4001/E4011/E4031/E4041/E4091/E4131/E4133/E4151/E4221/E4251/E5031/E5041 十二条经 `to_body()` 后 `hint` 非空且长度 ≤200；② `hint` 不含源文件 basename、不含正文片段（复用 NFR-20 探针）；③ README 表与实际 `hint` 表同键（静态红线，变异改文案即红） | 否 |
| **F-04** | 可观测 | **P0** | 300 页单文件同步转换期间，CLI 无输出、API 无进度（同步 `/jobs` 行直到事务提交才落，中途 `GET /jobs` 看不到），用户只能干等。低配机上这一段是分钟级 | 最小可用方案：CLI 阶段进度打点（stderr）——S1 解析 / S1.5 门禁 / S3 结构 / S4 渲染 / 打包五个阶段，各打一行 `{stage, pages, elapsed_s}`；非 `--json` 时以人读行输出，`--json` 时静音。DB 面进度（异步作业 `detail` 写阶段）作第二阶段，不混入本轮 | ① 320 页样本转换期间 stderr 出现 ≥5 行 stage 且顺序固定；② `--json` 时 stdout 仅一条 JSON、stderr 无 stage 行；③ 转换产物字节与 HEAD 逐字节相同（G6）；④ 3 条 gate 红线（含「阶段名闭集」） | 否 |
| **F-05** | 转换质量 | **P1** | 加密/带口令 PDF 无法诊断。`grep -i "encrypt\|password" doc2md/` 在解析路径**零命中**（`pipeline/s1_parse.py`、`legacy_doc.py` 均无），加密 PDF 落到 E4151，与「结构损坏」同码同文案，用户以为文件坏了 | S1 打开边界探测 PDF `/Encrypt` 标志；命中则仍用 E4151（段位冻结，不自造码），但 `detail.reason = "encrypted"` + F-03 的 hint；DOCX 侧对加密 OOXML 同理。与 F-03 同批 | ① 构造加密 PDF fixture（可用现有生成器 + 加密步骤）→ `detail.reason=="encrypted"`；② 非加密语料（31 个黄金样本）`reason` 字段不出现、产物逐字节不变；③ 2 条 gate 红线 | **部分**：真实带口令 PDF 语料的误杀面评估需人工（§5-6） |
| **F-06** | 检索 | **P1** | 检索只有 `q` 一个自由度。① 索引窗口 `SEARCH_INDEX_WINDOW = 8000`（`repo.py:17`），长块尾部默认搜不到，要靠 `mode=full` 全表 LIKE；② 无按文档/块类型/时间过滤；③ 无词组/排除/布尔；④ snippet 长度与高亮符硬编码（`repo.py:344`）；⑤ 无 CLI 入口（F-01） | ① `/search` 增可选 `doc_id` / `type` 过滤（纯 SQL `AND`，不触 G6）；② 新增配置键 `search.index_window`（默认 8000，`0` = 全文）进 `SPEC`/默认/样例/附录 A 四处；③ `?before=?after=` 可选时间窗 | ① `?doc_id=X` 的 hits 全部 `doc_id==X`；② `search.index_window` 四源键集一致（复用 `test_spec_defaults_and_example_share_one_key_set` 同族断言）；③ 附录 A 表键数 == `len(SPEC)`；④ 4 条 gate 红线 | 否 |
| **F-07** | 批量增量 | **P1** | 目录批量与 watch **没有增量语义**：① `convert` 每次全量重转，只有撞上 E4091（已有编辑）才停；② `StabilityTracker` 的 `pending`/`enqueued` 是纯内存 dataclass（`security/watcher.py:42-47`），watch 进程重启即清零 → 重启后整目录重转一遍；③ 无 `--if-newer` 之类的口径 | ① `convert --skip-existing`：按 `doc_id` 命中库行即跳过，结果行 `status:"skipped"`；② watch 把「已提交且未变」的判据落到库面（复用 `tasks` 的 `source_path` + `source_key`，不新增表）或提供 `--state-file`（默认 `$DOC2MD_HOME/watch-state.json`，600 权限）；③ `--if-newer` 走 mtime 比较 | ① 同一目录连转两次：第二次 N 条 `skipped`、退出码 0、产物 mtime 与 sha 不变、耗时 < 首次 20%；② watch 重启后对未变更目录零转换（当前会 N 次转换，先立反向断言锁住行为再改）；③ 3 条 gate 红线 | **部分**：大目录（>1000 文件）实测收益需人工/真机（§5-11） |
| **F-08** | 运维 | **P1** | 没有「搬家」和「卸干净」的路径。`ops/windows/install-doc2md-tasks.ps1 -Uninstall` 只覆盖 Windows 任务档；launchd 侧卸载在 README 里**完全没有**（只有 `launchctl load`）；把 `work/` 搬到另一台机器无任何口径说明（`state.sqlite` + `out/` + `backups/` 的相对路径迁移、DB 版本升级、token 是否随迁） | README 增「卸载与迁移」节：① launchd 四档 `launchctl bootout gui/$UID/<label>` + 删 plist（与 Windows `-Uninstall` 对等）；② 迁移 = 停服 → 整根打包（`tar` 保软链，README:239 已有解包口径）→ 目标机 `doc2md doctor` → `backup restore` 自证；③ 说明 token 随 `state.sqlite` 走、外置备份口令不随迁 | ① 文档步骤在本机 arm64 完整走一遍可执行、跑毕 `launchctl list \| grep doc2md` 为空；② 迁移后 `docs` 条数与源机相同、`doctor` 退 0；③ 1 条静态红线钉住「README 四档标签卸载命令与 `ops/launchd` 实存文件集合相等」（复用 CH-480 词边界口径） | **部分**：launchd 真机卸载需 macOS（本机可做）；Windows 端归 §5-3 |
| **F-09** | 体验 | **P1** | `doctor` 自检覆盖面偏「配置面」：不验 DB 可开与版本、不验 `out/`/`backups/` 可写、不验产物 zip 完整性、不验 FTS 索引健康（`reconcile_search` 只在 `serve` 启动与 `cron --daily` 跑，`api.py:305`、`cron.py` 步骤 10）、不验有效 token 数 | 增 5 项只读探查：`db_open`（可连 + `DB_VERSION` + `docs` 数）、`out_writable`、`backup_dir_writable`、`fts_health`（`docs` 块数 vs `search_blocks` 行数差，超阈告警）、`tokens_active`。失败口径沿用 CH-475 的 `EXIT_SYS` | ① 注入只读目录 → 对应项非 ok 且退出码 5；② FTS 被人为清空 → `fts_health` 非 ok（证明不是恒真断言，符合 12.8 教训 1）；③ 附录 B 的字段名单与实测 `len(doctor)` 相等（同步修 D-06）；④ 5 条 gate 红线 | 否 |
| **F-10** | 可观测 | **P1** | 「为什么这篇被拒收/哪一页坏」只写在 zip 内的 `report.html` 里，CLI 结果行（`cli.py:457-482`）不给出报告路径，用户要自己拼 `out/<doc_id>.zip` | `convert` 结果行增 `report` 字段（zip 内 `report.html` 的相对路径，取 `assert_entry_safe` 后的安全名）；`--json` 与非 json 两种形态都带；可选 `convert --report-out DIR` 把报告另存到产物目录外 | ① 每条结果行 `report` 非空且指向的文件存在；② 非 `--json` 人读行增 `report=…` 不破坏现有字段顺序；③ 2 条 gate 红线 | 否 |
| **F-11** | 转换质量 | **P2** | 表格跨片不合并（`errors.py:107` W1043）与嵌套表格扁平化（`s1_parse.py:864` 注释自陈「无版面模型，本轮不表达表格嵌套」）。真实合同/报价单/财务报表常见。当前状态：静默降级，用户以为表格完整 | 不做自动合并（风险高）；做**诚实暴露**：① 命中 W1043/W1044 的块在 `report.html` 顶部显「本产物含跨片未合并表格 / 嵌套表格已扁平」；② `ir.warnings` 已有留痕，`/docs/<id>` 响应透出 warnings 计数；③ README 增「已知限制」小节 | ① 构造跨片表格样本 → 报告含提示行、`ir.warnings` 含 W1043；② 无此形态的语料报告零提示（无噪音）；③ 2 条 gate 红线 | **部分**：真实嵌套表格语料需人工（§5-9） |
| **F-12** | 转换质量 | **P2** | RTL（阿/希伯来文）、竖排（日文）、旋转页：`grep -i "rtl\|bidi\|竖排\|rotation" doc2md/` 在解析路径零命中，而方案 1.2「非目标」**也没写**。用户拿日文竖排 PDF 进来，得到错乱的逐行文本且无任何提示 | 先在契约面补齐：方案 1.2 非目标表显式列出 RTL / 竖排 / 旋转页；`s1_parse` 探测到页面 `Rotation % 180 != 0` 或 RTL 字符占比 >50% 时写 `ir.warnings`（新告警码或复用 W1044 语义的独立码，按附录 D 段位纪律登记） | ① 1.2 非目标表新增 3 行；② 旋转 90° 的合成 PDF → 产物 warnings 非空且转换不失败；③ 常规语料零新增 warning（G6） | 否（真实 RTL/竖排语料评估归 §5-9） |
| **F-13** | 体验 | **P2** | 用户不知道本工具**不做什么**。图片、公式、扫描件 OCR 已是 1.2 非目标（`s1_parse.py:3` 自陈），但 README 全文没有一句「不支持」——这是最常见的期望落差来源（用户拿扫描件进来得到 E4221，第一反应是「工具坏了」） | README 增「支持与不支持的输入」小节（三行表：支持 / 有条件支持 / 不支持 + 不接受时的错误码），与 F-03 的处置表交叉引用 | ① README 出现「不支持：图片抽取、公式识别、扫描件 OCR」且指向 E4221/E4151；② 1 条静态红线钉住该小节三个关键词存在（复用 CH-480 词边界口径） | 否 |
| **F-14** | 批量增量 | **P2** | 无法把库里 N 篇 Markdown 一次性导到一个目录（喂给 Obsidian / 其他笔记工具）。`convert` 是「输入目录 → 产物 zip 目录」，`docs export`（F-01）是单篇 | `doc2md docs export --all --out DIR`：文件名 = 消毒后 `source_name` + `-<doc_id 前 8>`，`manifest.json` 记录 `doc_id → 文件名` 映射便于对账 | ① N 篇导出文件名两两不同、无 `..`、无 `/`（复用 `assert_entry_safe` 口径）；② 每篇与 zip 内 `content.md` 逐字节相同；③ `manifest.json` 条数 == `docs` 行数 | 否 |

### 2.1 需求池小结

| 优先级 | 数量 | 编号 | 是否含外部依赖 |
| --- | --- | --- | --- |
| P0 | 4 | F-01 / F-02 / F-03 / F-04 | 0（全部本机可验） |
| P1 | 6 | F-05 / F-06 / F-07 / F-08 / F-09 / F-10 | 3 项含部分外部子项 |
| P2 | 5 | F-11 / F-12 / F-13 / F-14 | 2 项含部分外部子项 |

P0 四项共用一条主线：**把「转入」之后的「读取/检索/导出/解释」补齐**，且全部落在既有 `storage/repo` + `errors` + `cli` 三层上，不引入新依赖、不触碰 G6 产物字节（除 F-02 的 md 直出是纯增量开关）。

---

## 3. 文档失真清单

严重度定义：**高** = 会影响用户/实施者按文档做出错误判断，或权威表缺了实际存在的能力；**中** = 口径数字过期、参数缺失；**低** = 表述精度问题。

| 编号 | 位置（文件:行号） | 文档说的 | 代码实际 | 建议修正 | 严重度 |
| --- | --- | --- | --- | --- | --- |
| **D-01** | `docs/dev-plan.md:1043`（附录 F 构成式） | 402 = 40+29+10+54+12+**1**+7+91+4+4+4+4+7+8+10+23+3+28+**5**+15+7 | 该式逐项求和 = **366**，与声称的 402 **差 36**。缺口是 CH-488 的 +36：附录 F 的构成式从 CH-486(5) 直接跳到 CH-489(15)，而 12.10 v3.24 行（`dev-plan.md:1828`）明记 CH-488「本机 gate **380**」，且 CH-486 时为 344、CH-489 时为 395 —— 344+36=380、380+15=395、395+7=402，三处数字互相印证 | 构成式补「+ CH-488 36 项」，并在附录 F 增 CH-488 层级表（见 D-16） | **高** |
| **D-02** | `README.md:227-228` | 402 项构成列举「…CH-482 10 项 + CH-489 15 项 + CH-490 7 项」，当前 402 项（CH-490） | 同 D-01：列举式缺 CH-488 的 36 项，各分项之和不等于 402 | 同口径补 CH-488 36 项 | **高** |
| **D-03** | `docs/dev-plan.md:1041-1205`（附录 F 层级详表） | 详表止于**第十三层（CH-482）** | 402 项红线里 CH-483(23)+CH-484(3)+CH-485(28)+CH-486(5)+CH-488(36)+CH-489(15)+CH-490(7) = **117 项在附录 F 只有构成式里的一个数字，没有任何断言要点表** —— 权威的「门禁断言清单」对近三分之一红线无内容 | 补第十四～二十层详表；最小可行动作是先补 CH-488（唯一连构成式都漏的一轮，36 项）与 CH-485（28 项，数据安全链） | **高** |
| **D-04** | `docs/dev-plan.md:858-860`（附录 A 键数口径）+ 表体 870-929 | 「实测本机三处各 **62**」「本表行数是 60 行」「当前 62 键全部有真实消费方」 | `len(loader.SPEC)` = **64**、`DEFAULT_TOML` 扁平键 = **64**；附录 A 表体**缺 `s1.legacy_doc_backend` 与 `s1.legacy_doc_timeout_s` 两行**（CH-487 新增，`default_toml.py:23-24` 已在默认配置里）。README.md:13-15 反而已记 `s1.legacy_doc_backend` —— 权威附录表比 README 更旧 | 表体补两行（`s1.legacy_doc_backend` = `"auto"` / 消费方 `s1_parse`；`s1.legacy_doc_timeout_s` = `120` / 消费方 `legacy_doc`），键数口径改 **64** | **高** |
| **D-05** | `docs/dev-plan.md:1271,1272`（附录 G.5） | 「`SPEC` 表（**62 键**…）」「**62** 个配置键的内置默认值唯一来源（附录 A）」 | 实测 64（`loader.py:88-167`） | 两处改 64 | 中 |
| **D-06** | `docs/dev-plan.md:948`（附录 B doctor 字段数） | 「固定 **29** 项 + 4 依赖探测 = **33 项**」，并列出 29 个字段名 | 实测 `doc2md doctor` 输出 **39** 键（固定 35 + 依赖 4）。文档名单缺 6 项：`root_private`（`cli.py:665`）、`config_unknown_keys`（:675）、`backup_external_dir`（:676）、`backup_passphrase_env_set`（:677）、`offline_sendmsg`（:695）、`legacy_doc_backend`（:714） | 名单补 6 项，计数改「固定 **35** + 依赖 4 = **39**」，并注明 `llm_server` 为 `llm.mode=server` 时的条件项（已有说明保留） | 中 |
| **D-07** | `docs/dev-plan.md:931-946`（附录 B CLI 全表，10 行） | 命令表：serve / serve token / cron / convert / work / watch / tokenizer export / tokenizer verify / backup drill / doctor | **缺 `doc2md backup restore` 整行**（`cli.py:121-128`，含 `src` / `--to` / `--force` / `--passphrase-env`），也缺其 `--passphrase-env`（`cli.py:127`）。README.md:32 已记 `backup restore`，附录 B 是 CLI 权威表却漏 | 附录 B 增一行：`doc2md backup restore` 参数 `SRC`、`--to`、`--force`、`--passphrase-env`，行为「v1/v2 容器离线恢复 + 补建缺失产物（`cli.py:356-382`）」，退出码 0 / 2（目标已存在、备份忙）/ 4（恢复失败） | **高** |
| **D-08** | `docs/dev-plan.md:940`（附录 B convert 行） | 参数列 `PATH --mode --out --json --follow-symlinks --workers --llm-mode --llm-no-fallback` | 缺 **`--force`**（`cli.py:139-140`）：允许覆盖库内已有用户编辑，缺省撞 E4091 拒收。附录 D 已记 E4091 语义，附录 B 的参数列却没有放行手段 | 参数列补 `--force` 并写明与 E4091 的关系 | 中 |
| **D-09** | `README.md:25-26`（CLI 块） | 同样缺 `convert --force`；`backup restore` 缺 `--passphrase-env` | 同 D-07 / D-08 | 与 附录 B 同口径补齐（`--passphrase-env` 至少提示默认值 `DOC2MD_BACKUP_PASSPHRASE`） | 中 |
| **D-10** | `docs/dev-plan.md:968`（附录 C `/search` 行） | 「`?q=&limit=`（默认 **50**，上限 500、负数 400/E4001）」，响应 `{total, hits[], mode, match}` | 默认 **20**：`api.py:955` `limit = _int_arg("limit", 20)`、`repo.py:306` `limit: int = 20`。响应还恒带 `has_more`（`repo.py:317`）与错误路径的 `fts_error`（`repo.py:374`），文档未列 | 改默认 **20**（代码为准，附录 C 是跟随方）；响应字段补 `has_more`、`fts_error?` | 中 |
| **D-11** | `docs/dev-plan.md:958`（附录 C `POST /jobs` 行） | body `{path, mode?, idempotency_key?}` | 还接受 **`force`**（`api.py:439-441`：只认布尔 `true` 与 `"true"/"1"/"yes"`，与 CLI `--force` 同语义），是 E4091 的唯一放行手段 | body 参数补 `force` 并写明字符串形态解析口径（CH-473 / R5-SVC-6） | 中 |
| **D-12** | `docs/dev-plan.md:975`（附录 C `DELETE /tokens/<token>`） | 主要错误列「—」 | `api.py:1109-1110`：未命中即 **404 / E4041**（CH-469 / V-13，与 CLI `--revoke` 同口径） | 错误列补「404 未知 token（E4041）」 | 低 |
| **D-13** | `docs/dev-plan.md:960, 966`（附录 C `/docs/<doc_id>`、`/docs/<doc_id>/audit`）；`:968` `/search` | 参数列写「—」与「`?block_id=`」 | 三处都还有未登记参数：`/docs/<id>` 支持 `?offset=&limit=`（默认/硬顶 500，回 `blocks_total`/`blocks_offset`/`blocks_limit`，`api.py:774-779`）；`/audit` 同样走 `_window`（`:904-923`，回 `total`）；`/search` 支持 `mode=full` 或 `full=1`（`:959-961`，CH-485 / R9-SVC-4 的全表兜底） | 三行参数列补齐，并说明「`limit` 缺省 0 归一为 500 硬顶」（CH-489 口径） | 低 |
| **D-14** | `README.md:68` vs `doc2md/config/default_toml.py:21` vs `docs/dev-plan.md:603` | 「320 页样本峰值驻留 **1290MB（改造前）→ 176MB**」/「实测 320 页样本 **1025MB → 177MB**」/「不分块 **1025MB** → 分块 32 **176MB**」 | 三处基准与尾数互不统一（1290 vs 1025；176 vs 177 vs default_toml 的「≈180MB」）。完整三档只存在于 `dev-plan.md:1581-1585`（1290 改造前 HEAD / 1025 `chunk=0` / 176 `chunk=32`） | 四处统一为「1290（改造前）→ 1025（chunk=0）→ 176（chunk=32）」，README 与两份 TOML 注释注明基准并指向方案 12.3 | 低 |
| **D-15** | `docs/dev-plan.md:1813-1854`（12.10 本文档变更记录） | 表内版本行顺序：…v2.4 → v3.5 → v3.6 → v3.7 → **v3.24 → v3.25 → v3.26 → v3.23 → v3.22 → … → v3.19 → v3.17 → … → v3.8 → v3.4 → … → v2.5** | 全文**无一处按版本或时间单调排列**（先升、再降、再升、再降）；且 **v3.18 整行缺失** —— CH-484 行（`dev-plan.md:1432`）记「仅 `PLAN_VERSION` 3.18」，但 12.10 表里 v3.19 与 v3.17 之间没有 v3.18 行 | 全表按版本号升序重排；补 v3.18 行（或在该行注明 CH-484 未单列版本行） | 中 |
| **D-16** | `docs/dev-plan.md:1209-1211`（附录 G 覆盖面口径行） | 「G.1–G.5 共 **33 行** = 包内 **39 个 `.py`** 减去 6 个只做 re-export 的子包 `__init__.py`（合 **69 行**），故 33 行合计 **9428**、整包 **9497**」 | 实测（`wc -l`）：**41 个 `.py`**；组头行数 3307+3760+1152+2388+1709 = **12316**（五个组头与逐行和一致，无漂移）；6 个 re-export `__init__` 实合 **71 行**（3+21+4+9+17+17）；整包 **12387 行**。且该行自陈的「33 行」与其自身五张表的行数（8+9+3+10+5 = **35**）**自相矛盾** | 按 `wc -l` 重算为「41 个 `.py` / 35 行 / 12316 行 / 6 个 `__init__` 合 71 行 / 整包 12387 行」；与该行已登记的「改代码后未重算组头」同族病灶，建议把该计数做成一条静态红线（与 CH-477 版本对账红线同形态） | 中 |
| **D-17** | `README.md:191-196`（文档与变更跟踪表） | 只索引 4 项：`docs/dev-plan.md`、`docs/plan-delta-v1.1.md`、`ops/backup.md`、`ops/windows/install-doc2md-tasks.ps1` | `docs/` 下 **16 份 md（12,303 行）**，其中 **7 份完全无索引**：8 份 `review-round*`（约 4,300 行，`dev-plan` 有引用但 README 无）、`compliance-audit-CH-487.md`、`spike-vlm-layout-hints.md`。另有 **5 份被 v2.0 重写吸收的历史方案**（`PDF-DOCX转Markdown离线管线-v2方案(Mac_mini_M4适配).md` 1182 行、`pdf-docx-to-md-plan-v2.1-macmini-m4.md` 1946 行、`…-v2.2` 491 行、`…-v2.3` 415 行、`…-v2.4` 417 行，共 4,451 行）在 `README.md` / `docs/dev-plan.md` / `ops/*.md` 中**零引用**（实测 `grep` 命中 0）。其中 `compliance-audit-CH-487.md` 与 `spike-vlm-layout-hints.md` 连 `dev-plan` 都不引用 = 孤儿文档 | README 增「文档地图」表（见 §4）；孤儿文档回灌或显式归档 | **高** |
| **D-18** | `README.md:36-38`（退出码段） | 只说明 `serve --host`、`serve token --role` 退 **2**，与 `--revoke` 未知 token 退 **4** | 还有四条未登记：`cmd_cron` 有失败步骤退 **5**（`cli.py:303`）、`cmd_backup_drill` 失败退 **4**（`cli.py:318`）、`doctor` 配置语义非法退 **2**（`cli.py:722`）、`doctor` 环境不健康退 **5**（`cli.py:735`）。附录 B 已记全，但 README 是用户第一入口 | README 退出码段补这四条（或加一行「逐命令退出码见方案 附录 B」） | 中 |
| **D-19** | `README.md:39` | 「CI 里 `doc2md convert --json` 的 stderr 会打印门禁摘要（键为 `mode`/`sampled_pages`/`reasons`/`stats`）」 | `cli.py:440` **无论是否 `--json`** 都往 stderr 打该摘要；`--json` 只影响 stdout（`cli.py:470-479`）。措辞会让人以为 `--json` 是触发条件 | 改为「拒收时 stderr 恒打门禁摘要（`--json` 只改变 stdout 形态）」 | 低 |
| **D-20** | `doc2md/auth.py:35`（`MATRIX["rerun"] = "editor"`） | 权限矩阵声明 10 个 action | `grep -oE 'need\("[a-z_]+"\)' doc2md/api.py` 实际只用 9 个（delete / edit / export / read / rollback / settings_put / submit_job / system_reload / token_admin）；**`rerun` 无任何调用方** —— 与项目已清零的 D-9 死代码同形态 | 删除 `rerun`，或登记为「为未来重跑端点预留」并写明预留理由（避免又被误当作既有能力） | 低 |

### 3.1 失真分布统计

| 位置 | 高 | 中 | 低 | 小计 |
| --- | --- | --- | --- | --- |
| `docs/dev-plan.md`（附录 A/B/C/F/G、12.10） | 4 | 6 | 2 | 12 |
| `README.md` | 3 | 3 | 1 | 7 |
| 源码注释（`default_toml.py`、`auth.py`） | 0 | 0 | 2 | 2 |
| **合计** | **7** | **9** | **5** | **21** |

失真最集中的两类：**①「项数/键数」这类硬口径过期**（D-01/02/04/05/06/16 —— 与 12.8 教训「数字必须带语料与测量方式」同源，且附录 A 自己在 :866 就写过「CH-473 收口轮复核对出 CH-471 三个键整行缺失……这正是『散文承诺不可依赖』的又一次实证」，本轮是第四次同形态复发）；**②权威表缺了实际存在的能力**（D-03/07/08/11/13）。

---

## 4. 文档体系整理建议

目标判据（可测）：**新人打开仓库 5 分钟内能回答三个问题——① 规格看哪份；② 这份报告/审计/spike 是什么地位；③ 遇到错误码去哪查。**

### 4.1 保留 · 权威（3 + 2）

| 文件 | 处置 | 本轮必做的补齐 |
| --- | --- | --- |
| `docs/dev-plan.md` | 唯一权威主文档，地位不变 | 修 D-01 / D-03 / D-04 / D-05 / D-06 / D-07 / D-08 / D-10 / D-11 / D-12 / D-13 / D-15 / D-16 |
| `README.md` | 用户第一入口 | 修 D-02 / D-09 / D-14 / D-17 / D-18 / D-19；增「文档地图」（§4.4）、「支持与不支持的输入」（F-13）、「卸载与迁移」（F-08）、「错误码 → 处置」（F-03） |
| `ops/backup.md` / `ops/llm.md` | 运维细则，README 已挂引用 | 与 F-03 的处置表交叉引用 |

### 4.2 保留 · 过程存档（11 份），需在文件头加一行地位声明

| 文件 | 行数 | 当前地位 | 处置 |
| --- | --- | --- | --- |
| `docs/review-round4-CH-471.md` | 470 | `dev-plan` 有引用，README 无 | 加头行「过程存档（第四轮审查报告）——权威副本见 `docs/dev-plan.md` 第十一/十二章；**勿据以实施**」 |
| `docs/review-round6-CH-475.md` | 228 | 同上 | 同上（第六轮） |
| `docs/review-round7-CH-476.md` | 329 | 同上 | 同上（第七轮） |
| `docs/review-round8-CH-483.md` | 1754 | 同上 | 同上（第八轮） |
| `docs/review-round9-CH-485.md` | 1112 | 同上 | 同上（第九轮） |
| `docs/review-round10-CH-486.md` | 143 | 同上 | 同上（第十轮） |
| `docs/review-round11-CH-489.md` | 111 | 同上 | 同上（第十一轮） |
| `docs/review-round12-CH-490.md` | 44 | 同上 | 同上（第十二轮） |
| `docs/Doc2md项目开发方案V1.0.md` | 1142 | v1 历史蓝本，`dev-plan` 12.10 沿革行有引用 | 加头行「历史蓝本（v1.0）——已被 `docs/dev-plan.md` 取代，仅供沿革追溯」 |
| `docs/compliance-audit-CH-487.md` | 90 | **孤儿**（`dev-plan`/README/`ops` 零引用） | 二选一：① 在 `dev-plan` 第十一章 CH-487 行挂一句引用（推荐，结论应可追溯）；② 按 §4.3 归档 |
| `docs/spike-vlm-layout-hints.md` | 267 | **孤儿**（零引用） | 把结论回灌到 `dev-plan` 9.2 待办或 12.7 一行（VLM 版面线索是否采纳/搁置需有结论），再按 §4.2 加头行；否则按 §4.3 归档 |

### 4.3 归档（新建 `docs/archive/`，5 份 / 4,451 行 / 约 250KB，全程零引用）

统一做法：`git mv` 保留历史（沿用 v2.1 轮 `dev-plan-v2.0.md → dev-plan.md` 的 `git mv` 先例），文件头加「已归档——内容已被 `docs/dev-plan.md` v2.0 重写吸收（见 12.10 v2.0 行），保留仅供追溯，勿据以实施」。

| 文件 | 行数 | 归档理由 |
| --- | --- | --- |
| `docs/pdf-docx-to-md-plan-v2.1-macmini-m4.md` | 1946 | 被 v2.0 重写吸收（12.10 v2.0 行），零引用 |
| `docs/PDF-DOCX转Markdown离线管线-v2方案(Mac_mini_M4适配).md` | 1182 | 同上 |
| `docs/PDF-DOCX转Markdown管线-v2.2-优化完善与变更记录.md` | 491 | 同上，且 CH-422…CH-446 已迁入 v2.0 第十一/十二章 |
| `docs/PDF-DOCX转Markdown管线-v2.3-全面优化与变更记录.md` | 415 | 同上 |
| `docs/PDF-DOCX转Markdown管线-v2.4-全面优化与变更记录.md` | 417 | 同上 |

保留存根：`docs/plan-delta-v1.1.md`（33 行，README 已索引为「已归档存根」，不动）。

### 4.4 补索引：README 增「文档地图」小节

建议在 `README.md:189-198` 的「文档与变更跟踪」处扩成两张表：

**表 1 · 权威文档（按问题找）**

| 我想知道 | 去哪看 |
| --- | --- |
| 规格 / 契约 / 错误码 / 配置键 | `docs/dev-plan.md` 主题篇（一–九章）+ 附录 A–G |
| 某次改动为什么做、证据是什么 | `docs/dev-plan.md` 登记篇（十–十二章） |
| 怎么装定时任务 / 常驻 / 备份 / 模型 | 本文对应小节 + `ops/backup.md` / `ops/llm.md` |
| 这个错误码意味着什么、怎么办 | 本文「错误码 → 处置」（F-03）+ `docs/dev-plan.md` 附录 D |
| 本工具支持/不支持什么 | 本文「支持与不支持的输入」（F-13） |
| 怎么搬家 / 卸载 | 本文「卸载与迁移」（F-08） |

**表 2 · `docs/` 全清单（16 份，逐份标地位）**

| 文件 | 地位 | 何时读 |
| --- | --- | --- |
| `dev-plan.md` | **权威** | 任何时候要查规格 |
| `review-round4/6/7/8/9/10/11/12-CH-*.md` | 过程存档 | 要追某轮审查的原始论证时 |
| `compliance-audit-CH-487.md` / `spike-vlm-layout-hints.md` | 过程存档（待回灌） | 追 CH-487 合规结论 / VLM 版面线索时 |
| `Doc2md项目开发方案V1.0.md` | 历史蓝本 | 追 v1 沿革 |
| `plan-delta-v1.1.md` | 存根 | 不读，指向主文档 |
| `archive/*.md`（5 份） | 已归档 | 不读 |

### 4.5 补红线：防失真再生（契合本项目既有纪律）

| 建议红线 | 钉住什么 | 形态 |
| --- | --- | --- |
| **R-DOC-1 附录 A 键数** | `len(SPEC)` == 附录 A 表体覆盖键数（现差 2，见 D-04） | 静态解析 `dev-plan.md` 附录 A 表体的反引号键名集合，与 `loader.SPEC` 求差集，非空即红；锚点解析失败先报「锚点失效」（CH-477 同形态） |
| **R-DOC-2 门禁项数构成式** | 附录 F 构成式各分项之和 == `pytest -m gate --collect-only` 实测数（现差 36，见 D-01） | 解析构成式里的加数求和，与测试文件红线收集数按文件分组比对；缺一轮即红 |
| **R-DOC-3 附录 G 覆盖面口径** | 组头行数之和 == 逐行之和 == `wc -l` 实测；`.py` 文件数与行数与实测一致（现四处过期，见 D-16） | 静态解析 + `wc -l` |
| **R-DOC-4 文档孤儿检测** | `docs/` 下每个 `.md` 必须在 `README.md` 或 `dev-plan.md` 中被引用（现 7 份孤儿/零索引，见 D-17） | 遍历 `docs/*.md` 的 basename 在两份权威文档中的出现次数，0 即红（白名单须显式登记理由） |
| **R-DOC-5 附录 B/C 参数闭集** | 附录 B 每条命令的参数列 == `cli.build_parser()` 实际选项；附录 C 每条路由的 body/query 参数 == 源码读取点（现缺 `--force` / `backup restore` / `passphrase-env` / `force` / `offset` / `limit` / `mode`，见 D-07/08/11/13） | `argparse` 内省 + 源码正则，双向差集非空即红 |

五条都是静态断言（不建库、不转换、不起子进程），与 CH-477 的四条对账红线同形态、同样「先断言锚点解析成功再比值」。项目已在 `dev-plan.md:866` 自陈「本表不由任何测试消费，文档侧漏行只能靠收口轮逐键核对」——R-DOC-1 正是把这句自陈的弱点关掉。

---

## 5. 「需外部系统环境或人工测试」专项清单（只登记，不实施）

按用户要求，下列项**本轮只登记，不做**。出口条件与既有 12.7 / 9.2 的 B-x、T-x 对齐，不重复编号，只标注与本报告 F 项的对应关系。

| # | 项 | 为什么必须外部 | 关联的 F / D | 出口条件 | 现状（来自 12.7 / 9.2） |
| --- | --- | --- | --- | --- | --- |
| 5-1 | Windows 11（8GB）真机吞吐/驻留同口径数字 | 沙箱 Ubuntu 无法产出目标机数字；`spawn` 进程池策略依赖此数据 | F-07（大目录实测收益） | 至少一组同口径数字进 12.3 | T3 / B-6 / B-8 未做；工具已就绪（`ops/bench/t6_large_corpus.py`） |
| 5-2 | Mac mini 16GB 真机驻留/吞吐 + launchd 定时 | 12.3 数字来自 Linux 沙箱 | F-08 | 同上 | T3 未做；B-5 的 launchd `cron --daily` 已在本机 arm64 验过，非目标机 |
| 5-3 | Windows `Register-ScheduledTask` 四档真机安装 + 崩溃自恢复 | CI 是 `ubuntu-latest`，不跑 PowerShell 任务注册；本机无 `pwsh` | F-08 | 四档注册成功 + `RestartOnFailure` 实测 | B-9 macOS 半侧已验，Windows 半侧未证；T12 沙箱半落地 |
| 5-4 | 真实外置卷（加密盘 / SMB / exFAT）备份恢复演练 | 沙箱用本地目录模拟卷 | — | 实景恢复一轮通过 | B-10 v2 外置卷实景已验（本机 USB APFS）；剩余：v1 真实容器、SMB/网络卷、并发 cron 重叠、恢复失败回滚；T13 未闭 |
| 5-5 | 真实本地 gguf 模型加载 + server 模式产物等价性 | 沙箱无模型资产 | — | 真实 server 全链路一轮 + offline/server 产物比对 | 12.7「真实本地模型服务」：契约 26 项已绿，真实模型未验 |
| 5-6 | **真实带口令/加密 PDF 语料** | 合成加密 PDF 能验 reason 字段，验不了误杀面 | **F-05** | 收集语料后评估「加密 / 非加密」判定的误报与漏报 | 本报告新增登记（当前解析路径 `encrypt`/`password` 零命中） |
| 5-7 | 真实浏览器编辑器走查（Windows 端） | 需真实 Chrome/Safari + Windows | — | 注入 docx → 审阅页无执行；大文档翻页+编辑+回滚 | B-11 未做；CH-490 已做一次真实浏览器验证（12.10 v3.26 行），Windows 端仍缺 |
| 5-8 | 真实注入语料（LLM 归因去循环与误报率） | 需真实中英注入话术语料 | — | 评估词表 effectiveness 后重定归因判据 | B-3 未做 |
| 5-9 | 真实 Word 模板 / 中文 PDF 标题 ground truth 人工标注 | 需人工标注 | **F-11（嵌套表格）、F-12（RTL/竖排）** | 模板样式抽查 + 标题 ground truth 标注完成 | B-13 已收 23 份模板、B-15 已收 10 份 PDF，标注未完成 |
| 5-10 | 跨文件系统符号链接/权限语义（SMB、exFAT、Windows junction） | 需至少两种目标文件系统 | — | APFS 之外再验两种 | B-14 未做 |
| 5-11 | 并发长跑压测（>30min 千页转换 × `reclaim_stale`，双进程） | 需长时运行环境 | **F-07（watch 重启重转的规模化后果）** | 稳定复现并给出结论 | B-12 未做；B-4 的短窗压测已做并固化夹具 `ops/bench/b4_delete_race_stress.py` |
| 5-12 | 远端 CI 取证（新红线项数上 push 面 / 周期腿回填） | 需推送到远端 | 全部新增红线 | 新一轮 push 面五作业 + 周期腿全绿 | 既有流程；每次项数改档都要重新取证 |
| 5-13 | 低配机数字口径统一后的重测（D-14 修正后） | 1290/1025/176 三档数字需在同一机器上重跑才能确认 | **D-14** | 一次同口径三档复跑，结果回填 12.3 | 数字散在四处且基准不明，建议与 5-1/5-2 同批 |

---

## 6. 建议的下一步（三条，按依赖顺序）

1. **先修文档（半天，零风险，收益最大）**：按 §3 修 7 条高严重度失真（D-01/02/03/04/07/16/17），并按 §4.5 落 R-DOC-1/2/3 三条静态红线——这三条正好把本轮发现的三类失真彻底关掉，且与 CH-477 的版本对账红线同形态，实施成本已被验证过。
2. **再做 P0 四项的 F-03 + F-04（1 天）**：错误处置指引与转换进度都属于「不改产物字节、不动数据库面」的纯增量，G6 等价门直接覆盖，是 P0 里风险最低、用户感知最强的一组。
3. **最后做 F-01 + F-02（2–3 天）**：CLI 读取面与 md 直出是本次审查认定的最大价值缺口，但要新增子命令与导出路径，须连带更新 附录 B / 附录 C / README，并与 D-07/D-08 的补齐同批（避免边修文档边改 CLI 造成二次漂移）。

F-05…F-14 按 §5 的出口条件排期；凡标「外部依赖」的子项一律只登记，等真机/语料/人工条件具备后另立 CH 条目实施。

---

## 7. 本报告的登记信息（供第十一章引用）

- **已登记为 CH-491**（见 `docs/dev-plan.md` 第十一章 CH-491 行、`README.md` 文档地图末尾的跟踪约定）。
  同一编号下另有架构师的第十三轮技术审查 `docs/review-round13-CH-491.md`（17 项：P0 2 / P1 9 / P2 6）——两份报告在同一基线 `main` / v2.12.0 上产出、互补不重叠，登记时合并为一条并注明两份落点；**下一条自 CH-492**。
- 登记类型：审查（功能全面性 + 文档一致性），非缺陷
- 登记内容：F-01…F-14（功能缺口 14 项：P0 4 / P1 6 / P2 4 + §2.1 表内 F-15 已并入 F-01）、D-01…D-20（文档失真 20 项：高 7 / 中 9 / 低 4，另有 D-20 为源码死代码 1 项）、§4 文档体系处置清单、§5 外部依赖专项 13 项
- **文档修订已落地（本轮）**：D-04/D-05（附录 A 键数与两键）、附录 A 级联语义、D-07/D-08（附录 B `backup restore` 与 `--force`）、D-06（附录 B doctor 39 项）、D-10/D-11/D-12/D-13（附录 C 四处）、附录 E `DB_VERSION` 6→9、D-17（README 文档地图）、D-18（README 退出码）、D-14（低配机三档口径），以及附录 G.6 的 9 处行数/键数漂移与 `ops/bench/b4_delete_race_stress.py` 漏登记。
- **留待收口（已按主理人指示留 TODO 标记）**：D-01/D-02（附录 F 构成式 402 与 CH-488 的 36 项差额）、D-03（附录 F 层级详表缺 CH-483~CH-490 共 117 项）、D-16（附录 G 覆盖面口径行数字）——工程师本轮另有增改，最终数字由主理人统一回填。
- **本轮范围外（建议登记 CH-492）**：D-09（README CLI 块同漏 `--force` 与 `backup restore --passphrase-env`）、D-19（README「`--json` 的 stderr 打门禁摘要」，实际恒打）、`config/doc2md.example.toml:23-24` 与 `doc2md/config/default_toml.py:21` 注释里的「1025MB → 177MB / ≈180MB」（属源码与配置资产，非本轮可改范围）、§4.3 的 5 份历史方案物理归档、§4.4 的 R-DOC-1…R-DOC-5 五条静态红线。
- 契约影响：**无**（本轮纯文档改动；`PIPELINE_VERSION` 2.12.0 / `SCHEMA_VERSION` 14 / `DB_VERSION` 9 / `PLAN_VERSION` 3.26 均不动）
- 若日后采纳 §4.3 的归档动作，属仓库文件移动，须用 `git mv` 保留历史（v2.1 轮先例）。
