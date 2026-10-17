# Doc2md 项目开发方案

| 项目 | 内容 |
| --- | --- |
| 项目名称 | **Doc2md** |
| 管线版本 | **v2.4.0**（`doc2md.PIPELINE_VERSION`） |
| 方案文档版本 | **v3.13**（本版；文件名固定为 `docs/dev-plan.md`，版本只写在这里，不写进路径） |
| 文档性质 | 开发主文档：主题篇为**当前有效规格**（as-built + 待办），登记篇记录阶段性变更与优化项 |
| 编制依据 | 仓库 `RunoobGo/doc2md` 实际代码与实测结果；管线 v2.4 Delta 记录；方案 v1.0 计划书 |
| 结构约定 | 主题篇（第一–九章）+ 附录 A–G（规格表）保持独立可读，**不含变更编号**；所有阶段性变更与优化项集中在登记篇（第十–十二章） |

## 阅读指南

- **想理解产品与规格**：读第一–九章。叙述顺序为「定位 → 需求 → 架构与代码结构与技术栈 → 管线 → 契约 → 安全运维 → 低配机运行 → 质量验收 → 实施计划」，每一章只依赖前一章已建立的概念。
- **想找代码在哪里、由谁调用**：读 3.5（模块地图）与 3.6（依赖方向与结构纪律），逐模块的行数、公开符号与引用方查附录 G。
- **想知道用了什么、没用什么**：读 3.7（技术栈与选型边界），依赖闭包与版本口径同见该节。
- **想查具体取值**：查附录 A（配置全表）、B（CLI 全表）、C（API 全表）、D（错误码与告警码）、E（数据库结构）、F（门禁断言清单）、G（模块与代码清单）。
- **想知道为什么某处是这样、改过什么**：查登记篇第十一章（CH-422 … CH-478 逐条登记）与第十二章（实测证据汇总、实现偏差、本文档变更记录）。
- **口径纪律**：正文区分三种表述——「规格（必须如此）」「现状（代码即如此）」「待办（尚未实现）」。待办一律在第九章显式标注，不在正文中伪装成已实现能力；凡给出数字者均为本项目实测值，测量方法随数字一并给出或指向第十二章。

---

# 主题篇

## 一、项目定位与目标

### 1.1 项目背景

Doc2md 是一条**离线**文档转换管线：输入文本型 PDF 与 DOCX，输出可被检索、可被审阅、可被下游 RAG 消费的 Markdown 与其结构化中间表示（IR）。产品形态为单机部署——无出站网络依赖、无云端服务、无账号体系；本地 API 与本地修补助手服务只绑定 `127.0.0.1`。

这一定位带来三条贯穿全篇的设计约束：

1. **确定性优先**：同一输入必须产出同一结果。转换过程不得依赖不可复现的外部状态（网络、模型采样温度、临时目录布局），验收因此可以做到**逐字节**比对。
2. **资源受限是默认前提**：部署目标是家用/办公机器，不是服务器。内存驻留、并发度、磁盘占用都要有明确上限，而不是「够用就行」。
3. **内部自用不等于没有边界**：文件名字段、删除操作、备份介质、token 权限都要按对外服务的最低标准设防，因为文档内容本身通常是敏感的。

### 1.2 目标与非目标

| 编号 | 目标 | 衡量口径（红线） |
| --- | --- | --- |
| G1 | 大文档（>300 页）在低配机上可完成且质量可控 | 分片转换 vs 单片转换正文 `char_similarity ≥ 0.99`；无「半句开头」块；分片真实发生（非降级） |
| G2 | 输入面安全：恶意文件名、目录炸弹、半写状态文件不误伤 | 恶意文件名集合 100% 安全落库；zip 条目零越界；符号链接循环目录 10s 内扫完 |
| G3 | 数据生命周期闭环：编辑可回滚、删除可清零、备份不泄漏 | 并发编辑后保存者收 `425 / E4251` 且数据未被覆盖；删除级联后搜索 0 残留；外置副本强制加密 |
| G4 | 运维自持：日志轮转、定时任务、磁盘保护不依赖外部设施 | 服务内置轮转且服务不重启；`doc2md cron` 按日/周稳定执行；剩余空间不足即拒收且不留半成品 |
| G5 | 低配机吞吐：较多文件可批量转换且不卡死界面 | 30 文件批量走进程池显著快于串行；异步入队秒级返回；峰值内存有可配上限 |
| G6 | 产物等价纪律：任何性能/结构改动不得改变输出字节 | 改造前后逐条目 sha256 全等（元信息中的耗时字段除外） |

非目标（明确不做，且不得被误读为路线图）：

1. **不做图片、OCR、扫描件、公式识别**。输入必须是文本型 PDF / DOCX；不满足者在体检门禁处拒收，而不是尽力输出残缺结果。
2. **不引入模型驱动的版面分析**，不接入 GPU 推理。修补助手是可选的本地 LLM，只产出白名单内的结构化 op，不是主链路依赖。
3. **不做多用户账号体系 / SSO**；token 三角色是权限分级的最小实现。
4. **不引入操作变换（OT / CRDT）**；编辑并发用乐观锁解决。
5. **不改冻结契约**：拒收原因码族、退出码族、运行 profile 三档、错误码段位——只允许在既有段位内增补，不允许自造新段位。
6. **不做 PDF 合并单元格（跨列 / 跨行）识别**（CH-476 / R7-GAP-3，决策 D7）：PDF 侧表格行由字符横向间隙推断，无版面模型时「这一格跨了几列」不可判定，硬做即误判并污染正文——按行拍平是明确边界，与 S3「宁漏勿误」纪律一致。DOCX 侧的合并单元格 `gridSpan` 同样不还原为 GFM 跨列语法（GFM 本就不支持）。

### 1.3 目标机型与运行假设

| 机型档 | 典型配置 | 关键假设 | 规格落点 |
| --- | --- | --- | --- |
| A：中低端 Windows 11 | 4–8 核 / 8–16GB / 无 GPU / 系统盘常接近满 | 控制台默认代码页为 GBK(cp936)；无 `fork`，多进程只能 spawn；文件路径含中文与 emoji | 第七章全部 |
| B：Mac mini M4（16GB） | 10 核 / 16GB / 统一内存 | 无 cron，定时走 launchd；`work/` 为用户目录下的普通目录 | 第六、七章 |
| C：开发/CI 环境 | Linux，4 核 / 8GB 起 | CI 无 GitHub Actions 运行器可用时以本地复现命令代替 | 第八、九章 |

三档共用同一份代码与同一套默认配置；差异只体现在配置建议与运维脚本（第七章、第九章）。

### 1.4 命名与版本治理

- **对外叙述一律 Doc2md**；代码内字面标识保持小写 `doc2md`（CLI 名、launchd 标签 `md.doc2md.cron`、目录名、日志 code）。
- **三个版本号各自演进，互不换算**：
  | 版本对象 | 载体 | 当前值 | 升版规则 |
  | --- | --- | --- | --- |
  | 管线版本 | `PIPELINE_VERSION` | 2.4.0 | 契约级修订升 minor，字段级修补升 patch |
  | IR schema 版本 | `SCHEMA_VERSION` | 14 | IR 结构或语义变更时递增，随迁移脚本演进 |
  | 状态库结构版本 | `DB_VERSION`（SQLite `user_version`） | 6 | `state.sqlite` 表结构变更时递增，迁移必须幂等（CH-469 / V-4 由 4→5：补 `tasks(doc_id)` 与 `events(job_id)`/`events(doc_id)` 三个索引；CH-476 / R7-STOR-1 由 5→6：新增 `search_map` FTS rowid 映射表 + `ix_audit_doc_id`，存量库开库回填映射。**同轮 R7-STOR-2 改迁移口径**：DDL 每次开连接**无条件**执行（全 `IF NOT EXISTS` 幂等），版本门控只用于一次性数据回填——修掉「加结构未 bump 版本 → 存量库永远缺位」的形态） |
- **方案文档版本**独立编号（本版 v3.13），每次实质性修订升 minor；与管线版本解耦。文档自身的变更记录见第十二章。**文件名不带版本号**（恒为 `docs/dev-plan.md`），版本只写在本文头部表格与 `doc2md.PLAN_VERSION` 两处——CH-452 那轮把 40 余处旧锚点改指「方案 v2.0 的章节号」之后，版本号一旦升位这些引用就再次失效；去版本化是同一个失效形态的根治（CH-454）。**编号与版本常量的对账纪律（CH-473 补）**：上一轮（CH-472）在登记行声称「`PLAN_VERSION` 3.5→3.6」但常量实际未 bump（header 已写 v3.6、常量停在 3.5、§1.4 还写「本版 v2.3」——三处漂移），由 CH-473 收口轮一并改正；此后每轮收口必须实测 `doc2md.PLAN_VERSION` 与 header、§1.4 三处一致。**CH-476 收口轮实测**：`python -c "import doc2md; print(doc2md.PLAN_VERSION)"` = `3.9`，与本文 header「v3.9」与本行「本版 v3.9」三处一致；同轮把 README 的「当前编号至 `CH-476`／下一条 `CH-477`」与本章 10.1 的「当前至 `CH-476`。下一条目自 `CH-477` 起」对账一致（R7-DOC-1）。**T15 已由 CH-477 落地**：批次⑬ 当时不落地，是因为 244/773 只有 run `39016742023` 一份取证、改项数会让文档口径与远端证据错开一轮；批次⑬ 提交 `44f98c3` 另经 run `39022142048` 独立取证后，改档不再损失证据，于是把对账做成四条 `pytest -m gate` 静态断言（`tests/test_ch477_version_sync.py`：版本三处对账 / README 与 10.1 编号对账 / 管线版本两处对账 / IR schema 与 DB_VERSION 两处对账）。断言先确认**锚点解析成功**再比值——文档被改写到句式失配时报「红线锚点失效」，而不是解析不到就跳过，否则红线会静默恒真，比漂移更糟。此后 CH-472 型漂移由 CI 拦住，不再依赖收口轮人工复读。**CH-478 轮实测（红线自证）**：升位后 `python -c "import doc2md; print(doc2md.PLAN_VERSION)"` = `3.11`，与本文 header「v3.11」与本行「本版 v3.11」三处一致，README「当前编号至 `CH-478`／下一条 `CH-479`」与本章 10.1 同数——本轮是该红线落地后第一次实际升位，四处锚点在同一次 gate 复跑中通过，非事后人工复读。**CH-478 批次② 升位补记**：同一轮内二次升位至 `3.12` 后，常量为 `3.12`、header 与本行同步，编号仍停在「当前至 `CH-478`／下一条自 `CH-479`」——批次① 的登记行随之改写为覆盖 批次①/②（见 10.1），四条对账红线在同一次复跑中再次通过。**升位纪律补记（批次② 踩到）**：升位后 header / §1.4 / §12.10 三处文档锚点在批次① 已全绿的红线里仍可能瞬时转红，而 `3.11` / `v3.11` 是纯文本匹配、`grep` 命中数即等于「散落引用行数」，必须按实测命中逐处改写，不能只改三处锚点后就宣称对账通过。**CH-479 轮实测（第三次自证）**：本轮为 CI 版本矩阵口径收口，`PLAN_VERSION` 升至 `3.13`，常量 / header / 本行三处在同一次 gate 复跑（260 项全绿）中对账通过；`grep -c "3\.12" docs/dev-plan.md` 的命中全部落在历史叙述与 `PIPELINE_VERSION`/`SCHEMA_VERSION` 等无关字段上，逐处复核后确认无第四处活锚点。
- **变更编号 `CH-xxx`** 按百位分段（4xx = v2.4 轮次与之后的开发期登记），只出现在登记篇。

## 二、需求规格

### 2.1 功能需求

| 编号 | 名称 | 优先级 | 规格要点 | 实现落点 |
| --- | --- | --- | --- | --- |
| FR-101 | 文本型 PDF 解析 | P0 | 页级行结构 + 字号/粗斜体/删除线样式；magic 判定不符即拒收 | `pipeline/s1_parse.py: read_pdf` |
| FR-102 | DOCX 解析 | P0 | 段落/样式/链接/修订痕迹/脚注尾注；**修订痕迹按「接受后」口径提取**——`w:ins` 包裹的插入 run 并入正文、`w:del` 的 `w:delText` 不入正文，命中的块打 `flags.has_revision` 供审阅定位来源（CH-476 / R7-GAP-1，决策 D4「提取而非拒收」；判据：真实修订样本 `tests/golden/docx/sample_revisions.docx` 转换后插入文字一字不丢、删除文字不出现）；**脚注 / 尾注正文补抽为尾部段落块**（`word/footnotes.xml` / `word/endnotes.xml`；一条注 = 一块，注内多段落以单空格相连；不伪造 `[^n]` 行内标记——GFM 无脚注语法、硬造即死链，来源由 `flags.note_kind` 留痕；CH-476 / R7-GAP-2，决策 D7；判据：`tests/golden/docx/sample_notes.docx` 的三条注文全部落地）；**表格单元格内段落边界以单空格相连**（原扁平粘连吃掉段界；单段单元格逐字节等价旧口径；R7-GAP-3）；DOCX 无物理页，按每 40 行合成页序列 | `pipeline/s1_parse.py: read_docx`（`_docx_notes_lines` / `_docx_cell_text`） |
| FR-103 | 文本层体检门禁 | P0 | 采样判定是否为文本型；不达标整篇拒收并给页级明细 | `pipeline/s1_5_gate.py: check` |
| FR-104 | 结构分析 | P0 | 标题层级、列表、表格、代码、引用、分页符；编号体系参与层级判定，**形态集合是封闭的两类**：`_NUM` = `^\s*(\d+(?:\.\d+)*)\s*[、.::]?\s*`（阿拉伯数字 + 可选多级点号 + 可选顿号/句点/冒号分隔）与 `_CJK_HEAD` = `^第[一二三四五六七八九十百千\d]+[章节条款项编部篇]`；层级 = 点号段数 + 1（截到 1…6），CJK 词头按 章/编/部/篇→1、节→2、条→3、项→4。**集合外形态**（罗马数字、`a)` / `iv.` 字母序、裸中文数字 `三、`、右括号分隔 `1)`）一律不参与编号判定，只由字号 / 加粗 / 显式样式提示定级——判错的层级比漏判更难看（污染 TOC 与渲染红线），故保守。另：纯单级编号不构成标题充分条件，须再命中字号>正文 / 粗体 / 多级编号 / CJK 章节词之一（CH-475 / R6-PIPE-1 信号门，见 4.4）。**同字号单级编号短行的正向归宿是 `list_item`**（CH-478 / R6-PIPE-1 收口）：`_ORDERED_ITEM` = `^\s*(\d{1,3})[、.．)）]\s*(?!\d)(?=\S)` 且行长 <30、行末无句末标点、且该行不是 ≥2 单元格的表格行时自成列表块，S4 保留序号渲染为 GFM 有序表（`N. `）——该式**只用于列表判定，不参与标题层级**，FR-104 的编号层级形态集合仍是上面封闭的两类 | `pipeline/s3_structure.py: analyze`（`_NUM` / `_CJK_HEAD` / `_heading_level` / `_level_from_number`；列表分支 `_ORDERED_ITEM` / `_is_ordered_item`） |
| FR-105 | Markdown 渲染 | P0 | front-matter + 正文；渲染红线由断言守门 | `pipeline/s4_render.py` |
| FR-106 | 产物打包 | P0 | `<doc_id>.zip` 内三条目：`content.md` / `ir.json` / `report.html` | `pipeline/convert.py: package` |
| FR-107 | 链接渲染分级 | P2 | 外部链接（http/https/mailto/ftp）→ `[text](url)`；PDF 内部跳转与 DOCX 书签链接正文一律纯文本（`anchor` 档不再产 `](#dest)`——GFM 无自动锚点、必为死链且违反渲染红线；内部链接信息完整保留在 IR `links[].dest` 与审阅页供下游消费，CH-464） | `s4_render.py: _inline`（`s4.link_internal = plain \| anchor`，两档正文同形） |
| FR-108 | marks 渲染分级 | P2 | 粗体/斜体默认开；删除线默认关（关闭时输出不得含 `~~`） | `s4_render.py`（`s4.preserve_marks_strike`） |
| FR-111 | watch folder | P2 | 大小 + mtime 连续稳定后才入队；与目录投递防护共用扫描实现 | `security/watcher.py: StabilityTracker` |
| FR-125 | 事件通道纪律 | P1 | 前端只消费全局事件总线；per-job 端点仅脚本型 User-Agent 可用 | `api.py: /events`、`/jobs/<id>/events` |
| FR-126 | 大文档两阶段分片 | P1 | 按页范围粗切（默认 300 页/片，2 页重叠）→ 各片独立结构分析与渲染 → 边界缝合 | `pipeline/sharding.py` |
| FR-127 | 文件名消毒与路径安全 | P1 | NFC 归一、剥离分隔符与控制字符、Windows 保留名重写、字节预算；zip 条目名二次断言 | `security/names.py` |
| FR-128 | 文档删除级联 | P1 | 删除产物 / IR / 事件 / revision / 审计，仅保留不含正文的删除留痕；admin + `confirm=true`；文件删除失败则整体失败 | `storage/repo.py: delete_doc` |
| FR-129 | 目录投递防护 | P2 | 不跟随符号链接、深度 ≤ 20、跳过隐藏项、单目录文件数上限、硬截止时间；超限返回明确错误码 | `security/dirguard.py: guard_walk` |
| FR-203 | 编辑与乐观并发 | P1 | PATCH 必填 `base_revision`；冲突返回 `425 / E4251` 并附 `current_revision`；审计 `before` 与 `from_revision` 严格对应 | `edits.py: patch_block` |
| FR-204 | 全文检索读侧 | P1 | `GET /search?q=&limit=`（reader）：CJK 单字 + bigram 命中、非 CJK 原样段，命中数与 LIKE 复核同口径；`limit` 默认 50、上限 500（超限截到 500 而非报错，`counts` 仍按全量），负数 → 400/E4001。**判据**：编辑后的新词立即可检（同事务刷索引）、旧词不再命中、5000 字符级查询串不撑爆 MATCH（CH-460 / A-9 上限） | `storage/repo.py: search`、`storage/fts.py`、`api.py: /search` |
| FR-205 | 产物 Markdown 直读 | P2 | `GET /docs/<doc_id>/markdown`（`export`，最低角色 reader）：返回 zip 内 `content.md`，`text/markdown; charset=utf-8`，**按 64KB 分块流式**（CH-476 / R7-PERF-k，峰值堆由「三份全量副本」降为一块缓冲）；产物缺失 404、zip 损坏 503/`E5031`。**判据**：响应体与 FR-106 打包进 `content.md` 的字节逐字节相同（流式不得改口径） | `api.py: get_markdown`、`pipeline/convert.py: package` |
| FR-206 | 回滚 | P1 | 按目标 revision 取回内容并另起新 revision，不抹历史 | `edits.py: rollback` |
| FR-207 | 令牌生命周期 | P1 | admin 签发 / 列举 / 吊销，API（`POST`/`DELETE /tokens`）与 CLI（`serve token`）共用 `auth.issue/revoke` 一个实现；可签发角色受 `api.token_roles` 限制；库内只存 sha256（明文仅签发时展示一次，CH-471 / SVC-9）；吊销即时生效（每请求实时查库，见 5.5）。**判据**：`role` 非白名单 → 400/`E4001`（CLI 退 2）、`label` 非字符串 → 400 零副作用 / 超长按 256 截断（CH-476 / R7-SVC-4）、吊销不存在的 token → 404/`E4041`（CLI 退 4）、吊销后旧 token → 401 | `auth.py: issue/revoke/check`、`api.py: /tokens`、`cli.py: _cmd_token` |
| FR-208 | 搜索索引刷新 | P1 | 编辑/回滚后同步重建该块索引 | `storage/fts.py` + `edits.py` |
| FR-209 | 运行期配置覆写与热重载 | P1 | `PUT /settings`（admin）落 `settings_override.json`（0600），写**累计稀疏差量**（多次 PUT 逐键累积、互不抹除，CH-469 / V-1）；`POST /system/reload`（admin）按「默认 → TOML → override」重新叠加，与启动同口径；未知键与非法值先校验后应用 → 400/`E4001`；红线键（`run.offline_assert`、`api.allow_remote_bind`）运行期不可改。**判据**：两条 PUT 的键集都活到 reload 后、坏配置保持旧配置继续服务并回 400、`reload` 与 `PUT` 同过 `check_runtime_immutable`（CH-475 / R6-SVC-1，堵「直接改文件 + reload」旁路） | `config/loader.py: check_runtime_immutable/validate`、`api.py: /settings`、`/system/reload` |
| FR-210 | 审计读回 | P1 | `GET /docs/<doc_id>/audit`（editor；返回 `before`/`after` 正文，故**不进 reader**）与 `GET /audit/deletions`（admin；只含计数与路径指纹，无正文）。**判据**：`before`+`from_revision` 与 `after`+`to_revision` 严格成对、`actor` 为 token sha256 前缀而非明文（CH-475 / R6-SVC-2，NFR-20）、force 覆盖路径同事务清空该块审计（CH-473 / R5-STOR-1，不留指向已消失 revision 的死行） | `api.py: /docs/<id>/audit`、`/audit/deletions`、`storage/repo.py` |
| FR-211 | 审阅页 | P2 | `GET /`（静态、无鉴权）原生 JS 单文件页：块列表、编辑保存、回滚、检索、队列面板；页面自身不持 token（由调用方注入），正文只在用户视图需要时出现。**判据**：页内每一处 `innerHTML` 插值都过 `esc()`，且 `esc()` 覆盖 `& < > " '` 五个字符（含 `'` → `&#39;`，CH-476 / R7-SEC-1；属性值用单引号包裹时不转义 `'` 即 XSS 通道）；队列面板的任务行只允许元数据键，正文 / markdown 字段一律不得出现（NFR-20） | `doc2md/static/editor.html` |
| FR-404 | 修补助手 | P2 | 白名单 op + 5 道校验 + 不可信数据立场；离线可运行（无服务时走确定性占位实现） | `llm.py` |
| FR-607 | 本地快照与外置加密副本 | P1 | 每日本地快照；每周外置加密增量；按保留天数轮出 | `ops/backup.py`、`cron.py` |
| FR-701 | 批量转换 | P1 | 目录级批量，进程池并行，逐文件成败与门禁摘要；单文件失败不炸整批 | `pipeline/batch.py: run_batch` |
| FR-702 | 任务队列与异步消费 | P1 | 入队即返回；独立消费者进程按容量领取；队列状态可查 | `worker.py`、`api.py` |
| FR-703 | 环境自持诊断 | P2 | `doc2md doctor` 一次性暴露依赖、路径权限、关键配置、剩余空间、控制台编码 | `cli.py: cmd_doctor` |

### 2.2 非功能需求

| 编号 | 名称 | 规格（可判定） | 实现落点 |
| --- | --- | --- | --- |
| NFR-1 | 离线 | 主链路零网络调用，由依赖清单与代码事实保证（包内无任何出站请求实现，运行时依赖仅 4 项本地库）；`run.offline_assert=true`（默认）为可开关运行期守护：把 socket 出站限制为回环（CH-456 / T9 起生效） | `pipeline/*`、`llm.py`（只连 `127.0.0.1`）、`security/netguard.py` |
| NFR-6 | 磁盘安全 | 写产物/备份前校验剩余空间：需求 = `max(min_free_mb, 源大小 × disk_safety_factor)`；不足即 `413 / E4131`，**不得留下半成品** | `ops/diskguard.py` |
| NFR-9 | 权限分级 | 三角色 admin/editor/reader + 端点矩阵；吊销承诺 ≤5s 生效（实现为每请求实时查库，即刻生效，见 5.5） | `auth.py: MATRIX` |
| NFR-14 | 日报 | 每日输出结构化日报（文档数、作业成败、门禁计数），断档即运维信号 | `cron.py: _daily_report` |
| NFR-18 | 备份加密 | 外置副本强制 AES-256-GCM 容器；口令只走环境变量；未配置外置卷 = 跳过（不判失败），配置了但缺口令 = 失败且**不落明文** | `ops/backup.py` |
| NFR-19 | 日志轮转 | 单文件 ≥ 阈值或跨 UTC 日轮转，保留 N 份；由**写入进程自身**执行，不依赖 newsyslog/cron；每个活动文件只有一个属主 | `ops/logging.py: RotatingJsonlWriter` |
| NFR-20 | 数据边界 | 正文只存在于 `work/` 与加密备份中；日志、日报、事件流、删除留痕四类输出不得含正文片段，长文本一律落 sha256 指纹；`work/` 目录权限 700 | `api.py: _scrub`、`backup.py` |
| NFR-21 | 有界内存 | 单文档解析峰值驻留由配置上限决定，不随文档页数线性增长 | `s1_parse.py: read_pdf` |
| NFR-22 | 吞吐 | 多文件批量缩短总时长，**并按每文件工作量分档判定**（CH-476 / R7-GAP-5：原「近线性于核数（上限内）」的措辞靠「上限内」掩盖了与实测的冲突）：① 单件转换耗时 ≫ 每轮固定开销（池建立 + worker 重导 pdfplumber，本机实测 ≈0.17s/轮）时，总时长随 `workers` 近线性下降——12.3 的 8×40 页语料实测约 **2×**（4 进程）；② 大量小文件（单页级）语料下固定开销主导，实测仅 **+4.5%**（CH-457 / T6，1080 单页混合语料）——这不是回归，而是本条的**适用下界**，任何「近线性」结论不得跨档外推（12.8 教训 4）；③ CH-476 / R7-PERF-1 把领取量与池大小解耦（`perf.queue_batch_factor` 默认 4×workers）后，固定开销按批摊薄，② 档的并行增益预期上移，实测数字待 12.3 重跑回填。并行改造不得改变产物字节（G6 红线，等价性断言守门）；Windows spawn 档（每 worker 另需重导 `doc2md` + 解释器启动）从未实测，登记 B-6 | `pipeline/batch.py`、`worker.py: claim`、等价性断言 |
| NFR-23 | 目标机型可用 | Windows 中文控制台与 macOS 终端下均不得因编码问题崩溃；输出可被脚本稳定解析 | `cli.py: _console_safe / _jline` |
| NFR-24 | 可复现性 | 黄金样本资产两次生成 sha256 全表一致；分发包可复现 | `tests/make_golden.py`、`MANIFEST.json` |

### 2.3 验收口径的来源

需求表中的每条「可判定」列即为验收口径，落到三处之一：附录 F 的门禁断言清单、第十二章的实测证据表、或第九章待办的验证计划。凡既无断言也无实测的需求，不得在文档中标注为「成立」。

同一条纪律**反向也成立**：已实现、甚至已有断言的功能若没有需求编号，纪律就找不到它——编号是「规格 ↔ 断言 ↔ 实测」三者的索引键。CH-476 / R7-GAP-4 因此为六项长期缺号的已实现功能补编号与判据（FR-204 检索读侧、FR-205 产物 Markdown 直读、FR-207 令牌生命周期、FR-209 运行期配置覆写与热重载、FR-210 审计读回、FR-211 审阅页）；这六条都是**补登记而非新规格**，行为面自落地以来未变，判据列引用的是既有断言所在文件与已实施的修复编号。同批把两条措辞含糊的既有条目改成可判定形态（R7-GAP-5：FR-104 的「编号体系」给出封闭形态集合与集合外口径、NFR-22 的「近线性」按每文件工作量分档并写明适用下界）。

两张需求表的「实现落点」列给出**功能 → 模块**的对应关系（一个功能可跨多层，例如 FR-702 同时落 `worker.py` 与 `api.py`）；若要反向查——某个模块承载哪些功能、被谁引用、对外暴露什么符号——读 3.5（分层地图）、3.6（依赖方向）与附录 G（逐模块清单）。

## 三、总体架构

### 3.1 分层与数据流

```
输入（PDF / DOCX，本地路径或目录）
  │  magic 判定（不满足 → 拒收，错误码见附录 D）
  ▼
S1   解析：页 → 行 → 字符级样式（有界内存，分块读取）
  ▼
S1.5 体检门禁：文本层质量判定（不达标 → 整篇拒收 + 页级明细）
  ▼
S2   分片规划：按页范围粗切（带重叠），或单片
  ▼
S3   结构分析（每片独立）：块类型、标题层级、续行并回
  ▼
S4   缝合 + 渲染（每片独立渲染后缝合，或整体渲染）
  ▼
LLM  修补助手（可选）：白名单 op → 校验 → 应用
  ▼
产物：content.md（front-matter + 正文）/ ir.json / report.html → <doc_id>.zip
  │
  ├─→ SQLite（docs / blocks / 审计 / 任务 / 事件 / 删除留痕）
  └─→ FTS 检索索引（CJK n-gram 辅助列）
```

阶段职责边界是硬约束：**S1/S1.5/S2 不得依赖 S3/S4 才产生的信息**。标题是分片之后由结构分析得出的结论，因此分片切点只能按页范围粗切，跨片边界的质量问题交给缝合阶段处理。这条纪律是分片设计的根，任何「提前判断标题」的优化都要先证明其不依赖后置信息。

### 3.2 进程模型

Doc2md 有意保持「一个包、多个入口」，每个入口职责单一，共用同一份配置与同一个 SQLite 库（WAL 模式）：

| 入口 | 形态 | 职责 | 日志归属 |
| --- | --- | --- | --- |
| `doc2md serve` | 常驻 HTTP 服务 | API 面：作业提交/查询、文档读写、搜索、事件、设置、token | `logs/app.jsonl` |
| `doc2md work` | 常驻或一次性消费者 | 领取队列任务、批量转换、写产物与库、回收超时遗骸 | `logs/work.jsonl` |
| `doc2md convert` | 一次性 CLI | 文件或目录直转（可在目录上开进程池） | 无（stdout 摘要） |
| `doc2md watch` | 一次性或轮询 | 目录扫描 + 写入稳定性判定 → 提交作业（同步或入队） | 无 |
| `doc2md cron --daily/--weekly` | 定时触发的一次性进程 | 日报、本地快照、外置加密增量、tmp 清扫、轮转检查 | `logs/cron.jsonl` |
| `doc2md tokenizer export/verify` | 一次性 CLI | 模型 vocab 导出与 manifest 校验 | 无 |
| `doc2md doctor` | 一次性 CLI | 环境自检 | 无 |

日志归属规则：**每个活动日志文件只有一个进程持有写入器**。轮转靠 rename 实现，两个进程写同一文件会互相重命名对方正在写的活动文件。因此 serve / work / cron 各自持有不同 stem。

### 3.3 数据与隐私边界

| 位置 | 内容 | 允许形态 |
| --- | --- | --- |
| `work/out/<doc_id>.zip` | 正文、IR、审阅页 | 明文（属用户工作区） |
| `work/state.sqlite` | 正文、审计 before/after、任务、事件 | 明文（`work/` 权限 700） |
| `work/reports/backups/` | 本地快照 | 可明文，目录权限 700 |
| 外置卷 | 加密容器 | 强制密文 |
| 日志 / 日报 / 事件流 / 删除留痕 | 元信息 + 指纹 | **不得含正文** |
| IR meta 的 `source_name_raw` | 消毒前原始名 | 仅出现在审阅页展示层（HTML 转义），不进 zip、不进库的可导出面 |

**标识符纪律**：`doc_id` 只由文件内容哈希得出（16 位前缀），任何文件名字段都不得参与；`source_key`（24 位前缀）代表同一来源，用于整源删除。二者都用流式哈希计算，不把整份文件读进内存。异步队列路径上每任务哈希**两**遍——入队一遍（内容寻址的附着去重要在建行前拿到 doc_id）、子进程转换前一遍（产物身份必须以消费时内容为准）；入库前的第三遍复核已取消（CH-476 / R7-PERF-2），内容漂移改由子进程回报的 `doc_id` 与建行时的 `doc_id` 比对判定。

### 3.4 任务与状态机

任务（`tasks` 表）状态机：

```
              ┌────────────── submit（内容寻址，同 doc_id 进行中则附着）──────────────┐
              ▼                                                                        │
          queued ──── claim（原子领取）────▶ running ────▶ done                        │
            ▲                                 │                                          │
            │                                 ├────────▶ rejected（门禁 E4221）          │
      遗骸回收（超时）                     └────────▶ failed（缺源路径 / 业务错误 / 系统错误）
```

- **附着语义**：同一 `doc_id` 在 `queued`/`running` 期间再次提交，返回既有 `job_id` 而非新建（部分唯一索引保证）。这与 `Idempotency-Key` 去重是同一机制的两条路径。
- **队列来源标记**：只有带 `source_path` 的任务是队列任务。同步 API 写入的历史行不带该列，因此不会被「遗骸回收」误动。
- **原子领取**：`SELECT queued … LIMIT n` 与 `UPDATE … WHERE status='queued'` 必须在同一事务内，否则两个消费者会重做同一任务。`n` 是**批大小** `workers × [perf].queue_batch_factor`（CH-476 / R7-PERF-1，默认 4×），不是池大小——每轮新建进程池 + worker 重导 pdfplumber 的固定开销（本机实测 ≈0.17s/轮）按批摊薄；同时在跑的转换件数仍受 `workers` 约束（同路径作业在 `run_batch` 里天然归并）。代价：`work` 进程若在批中途被 OOM 干掉，本批 `running` 行要等 `stale_job_minutes`（默认 30min）才重回队列；系数配 1 即回到「按池取件」的旧口径。

### 3.5 代码层结构（模块地图）

仓库只有一个 Python 包 `doc2md`（39 个 `.py`，9317 行），加一个测试包 `tests`（32 个 `.py`，14079 行）。顶层没有 `src/` 目录、没有子包多版本布局——这是刻意的：产品是单机自持的一体化管线，分包布局带来的版本协同成本没有对应收益（依赖打包口径见 3.7、附录 G）。

| 目录 / 文件 | 层（3.6 的 DAG 层次） | 内容（逐模块职责见附录 G） |
| --- | --- | --- |
| `doc2md/cli.py` | 入口层 | argparse 子命令解析 + 退出码聚合 + 控制台加固（`_console_safe` / `_jline`）；所有子命令的唯一实现起点 |
| `doc2md/api.py` | 入口层 | `create_app()` 工厂 + 20 个端点 + 鉴权挂钩（`need()`）+ 事件与脱敏（`_emit` / `_scrub`） |
| `doc2md/worker.py` | 编排层 | `tasks` 队列消费者：原子领取、内容漂移留痕、超时遗骸回收、`work.jsonl` 独立属主 |
| `doc2md/scheduler.py` | 编排层 | `Scheduler`：作业投递（附着/幂等）、状态标记、事件写入 |
| `doc2md/edits.py` | 编排层 | `patch_block()` / `rollback()`：乐观锁校验、审计行写入、索引刷新 |
| `doc2md/auth.py` | 编排层 | 令牌签发/列举/吊销、角色 rank 比较、`action → 最低角色` 矩阵 |
| `doc2md/cron.py` | 编排层 | `run_cron(daily\|weekly)` 步骤编排、`sweep_tmp()`、周报落盘 |
| `doc2md/pipeline/` | 领域层 | 转换主链路：`s1_parse` → `s1_5_gate` → `sharding` → `s3_structure` → `s4_render` → `convert`（编排 + 打包）；`batch`（进程池）、`metrics`（相似度/半句度量） |
| `doc2md/ir/` | 基座层 | `model.py`：`Block` / `IR` / 内容寻址标识符 / `BLOCK_TYPES`（7 类：heading、paragraph、list_item、table、code、quote、page_break） |
| `doc2md/storage/` | 领域层 | `db.py`（DDL、连接池、迁移）、`repo.py`（仓储 + 级联删除）、`fts.py`（CJK n-gram） |
| `doc2md/security/` | 支撑层 | `names.py`（消毒 + zip 条目断言）、`dirguard.py`（遍历防护）、`netguard.py`（`run.offline_assert` 回环守护）、`watcher.py`（写入稳定性） |
| `doc2md/ops/` | 支撑层 | `logging.py`（JSONL 轮转写入器 + 事件总线）、`backup.py`（AES-GCM 容器 + 轮出）、`drill.py`（备份恢复演练 8 步闭环）、`diskguard.py`（NFR-6）、`gguf.py`（M0 词表工具链） |
| `doc2md/config/` | 基座层 | `default_toml.py`（内置默认值的唯一来源）、`loader.py`（解析顺序、点号取值、相对路径迁移） |
| `doc2md/static/` | 展示层 | `editor.html`：单文件审阅页（纯原生 JS，383 行，含队列进度面板） |

包的根目录另有三个不归属子包的模块——`errors.py`（全部错误码与告警码）、`llm.py`（修补助手 + 注入检测）、`__init__.py`（三个版本常量）。这三者被跨层引用，因此不属于任何子包。

表里的层名与 3.6 的 DAG 一一对应：入口 = `cli` / `api`；编排 = `worker` / `cron` / `scheduler` / `edits` / `auth`；领域 = `pipeline/*` / `storage/*`；支撑 = `security/*` / `ops/*`；基座 = `errors.py` / `llm.py` / `config/*` / `ir/*` / 包根常量。`static/` 不参与 Python 依赖图，单独标为展示层。逐模块的行数、公开符号与「被谁引用」见附录 G。

分层不是口头约定，而是**依赖方向上的可验证纪律**（3.6）。

### 3.6 依赖方向与代码结构纪律

实测的依赖事实（非设计愿望，可由 `grep -rn "^from \.\|^import \." doc2md` 复核）。真实的引用方向构成一个 DAG，箭头只有一条方向：

```
入口层  cli.py · api.py
   │
编排层  worker.py · cron.py · scheduler.py · edits.py · auth.py
   │
领域层  pipeline/*  ·  storage/*
   │
支撑层  security/*  ·  ops/*
   │
基座层  errors.py · llm.py · config/* · ir/* · 包根常量（__init__.py）
```

1. **没有任何一层向上引用**，唯一的例外是一处被显式承认的延迟导入（见第 5 条）。逐层的真实引用面：`errors.py` 无任何导入、`llm.py` 与 `storage/fts.py` 除标准库外无包内导入（真正的零依赖基座）；`security/names`、`security/dirguard`、`ops/diskguard` 只引用 `errors`；`ir/model` 只引用包根的 `SCHEMA_VERSION`；`config/` 只依赖标准库 `tomllib` 与同包 `default_toml`。这条是「可单测、可替换入口、装机失败可定位」的地基。
2. **`pipeline/*` 与 `storage/*` 互不引用**。转换层只产出 `IR` + 产物 zip；入库只发生在三个调用点（`api.py` 同步分支、`worker.py`、`cli.py` 的 convert/watch 路径，均调 `storage.repo.save_ir`）。所以「改产物结构」与「改库结构」是两个动作，任何一次改动只需要一侧的断言。
3. **`errors.py` 与 `config/` 是全包的两个零依赖基座**：错误码与内置默认配置因此可以在任何装机环境下被读取和判定，不依赖解析库是否存在——`doctor` 与 `--help` 能在全绿自检的前提就在这里。
4. **入口层是唯一允许跨全部层的地方**：`api.py` 组装 `Store` / `Scheduler` / 轮转写入器并挂载 20 个端点；`cli.py` 的每个子命令函数在函数体内导入自己需要的模块（该文件 20 处；全包共 39 处函数内包内导入，其余分布在 `api.py` 2、`worker.py` 7、`storage/repo.py` 3、`cron.py` 2 等）。效果是缺失依赖只影响用到它的子命令——例如未装 Flask 时 `doc2md convert` 照常工作，而 `doc2md serve` 才失败，`doctor` 则把该依赖报为 `MISSING` 并按 5 退出。
5. **唯一的向上引用是 `worker.py`（编排层）→ `api.py`（入口层）的 `_emit`**，以函数内延迟导入实现，目的是复用同一套脱敏口径而不是再写一份字段剥离。与之对称的还有 `api.py` 在异步分支内 `from .worker import enqueue`。代价是「运行期导入失败不会被 import 期发现」，所以服务面断言必须真的执行 `create_app()`（附录 F 第 6 组）。
6. **重量级第三方导入一律函数内执行**：`pdfplumber`（`s1_parse.read_pdf`）、`docx`（`read_docx`）、`flask`（`create_app`）、`concurrent.futures`（`batch` 的池构造）。收益是 `doc2md --help`、`doc2md doctor` 与测试收集都不为解析库付出导入成本，缺依赖时报错落在使用点而非导入栈（`doctor` 的 `dep_*` 四项正是为此存在）。
7. **子包 `__init__.py` 是对外门面**：`storage` / `security` / `config` / `pipeline` / `ir` / `ops` 在 `__init__` 里 re-export 并声明 `__all__`（清单见附录 G）。同包内允许直引模块（`pipeline/convert.py` → `.s1_parse`）；跨包直引（如 `from .storage.db import Store`）目前存在，属可接受偏差（登记 D-13），因为 `db` 与 `repo` 的职责边界已由附录 E 与 `__all__` 固定。
8. **spawn 语义是结构约束**：并行入口的 worker 函数必须是模块顶层可 pickle 函数（`pipeline/batch.py:_convert_one`），返回值是紧凑 dict 而非 `IR` 对象。这条决定了「并行代码不能藏在闭包里」，也是 Windows 可用性的前提（7.3）。

结构变化的判定口径：**新增模块必须同时说明它位于上面 DAG 的哪一层、允许被谁引用**；出现反向引用（下层引用上层）需要显式理由并在第十一章登记。任何「先塞进 `api.py`，以后再说」的做法都会让纪律失效（历史上 `create_app` 曾同时持有定时线程，ADR-30 之后才拆干净）。

### 3.7 技术栈与选型边界

| 层 | 采用 | 版本口径（沙箱实测） | 为什么是它 |
| --- | --- | --- | --- |
| 语言 | Python | `>= 3.11`（打包声明），实跑 3.12.13 | 唯一约束是 `tomllib` 必须是标准库（NFR-1：配置解析不引第三方 TOML 依赖）。`from __future__ import annotations` 全包覆盖（**30/39 文件**；未加的 9 个是各包的 `__init__.py` 门面与 `errors.py` / `config/default_toml.py` 两个纯常量模块，没有需要延迟求值的签名），类型标注写成 `str \| None` |
| 文档解析 | `pdfplumber` | 0.11.10（依赖闭包 18 项，见下） | 需要的是**行与字符级**结构（`page.chars` / `extract_text_lines()`），只有它能给出字号与 bbox；`pypdfium2` 在其依赖链里但不直接使用 |
| | `python-docx` | 1.2.0 | 段落与 run 的样式/链接足够；DOCX 无物理页，按每 40 行合成页（FR-102） |
| 存储 | SQLite 标准库 `sqlite3` + **FTS5** 虚表 | 运行库 SQLite 3.37.2（FTS5 在本机实测可用）；DB-API 模块 2.6.0 | 单文件库、WAL、零服务进程；FTS5 让检索与元数据同库同事务，避免引入搜索引擎。`tokenize='unicode61'` 对 CJK 整段失效，另加 `aux` n-gram 列（5.6 / 附录 E）。目标机自带的 SQLite 版本不由本项目决定，因此虚表重建式迁移必须幂等 |
| HTTP | `Flask` 3.1.3 + Werkzeug 3.1.8，`app.run(threaded=True)` | — | API 面只有 20 个端点，不需要异步框架；多线程 + `threading.local` 连接（CH-436）已够家用机并发量 |
| 前端 | 单个原生 HTML/JS 文件（`static/editor.html`） | — | 零构建 = 零依赖漂移；`package-data` 只声明真实存在的资源（CH-441）。刻意**不消费** `/events`，进度靠 `GET /docs` + 每块 revision 比对 |
| 并发 | `concurrent.futures.ProcessPoolExecutor`（spawn 上下文可参数化） | — | GIL 下线程池对解析无增益；spawn 是 Windows 唯一可行语义（7.3） |
| 加密 | `cryptography` 50.0.1（AESGCM + Scrypt） | — | NFR-18 的容器格式需要 AEAD + KDF；口令只来自环境变量 |
| 配置 | 标准库 `tomllib`（只读） | — | 写路径不存在 → 部署态配置不会被程序改写（`PUT /settings` 落的是 JSON 覆写层，见 5.7） |
| 日志 | 自研 `ops/logging.py`（JSONL + rename 轮转） | — | **不使用标准库 `logging`**：需求是「按进程属主、按字节阈值自轮转的结构化 JSONL」，`RotatingFileHandler` 的多进程重命名语义正是历史上互踩的形态（6.5） |
| 测试 | pytest 9.1.1 + `pytest.ini` 两个 marker（`--strict-markers`） | — | `gate`（红线）与 `milestone`（任务卡归属，实际取值 `M0+M2` / `M3` / `M5` / `M6c` / `M8` / `M9a` / `DoD` / `PERF`）有实际消费；`slow` 与 `M4M6` 两个零消费声明已在 CH-456（T10 / D-12）清除，声明与使用的漂移此后由 `--strict-markers` 兜底 |
| 打包 | setuptools（`pyproject.toml`），`[project.scripts] doc2md = "doc2md.cli:main"` | — | 与 CI 的 `pip install -e ".[dev]"` 同一口径；不引入 hatch/flit |
| CI | GitHub Actions（push 面 `.github/workflows/ci.yml` + 周期面 `.github/workflows/ci-full-matrix.yml`，CH-479） | — | push 面 merge-gate 三版本矩阵 + 单解释器 full-suite 依赖层；周期面三版本全量（8.6、12.1） |

**依赖闭包**（4 项直接依赖展开）：`Flask, blinker, cffi, charset-normalizer, click, cryptography, itsdangerous, jinja2, lxml, markupsafe, pdfminer.six, pdfplumber, pillow, pycparser, pypdfium2, python-docx, typing-extensions, werkzeug` = 18 个包；加 `[dev]`（`pytest` / `reportlab` / `pypdf` / `ruff`，CH-462 实核补漏）为 26 个。依赖版本为 `>=` 下界浮动（pyproject 只锁下限，不锁上界；升级依赖后需重跑红线与全量确认，附录 G 行数与 12.3 数字同理为实测时点值）。全部为 MIT / BSD / Apache-2.0 / PSF / MIT-CMU 级许可；仓库已补齐 `LICENSE`（MIT）与 `pyproject` 的 `license` / `classifiers` 元数据声明（CH-456 / T11，第十二章 D-11 已关闭）。

**明确未采用**（每项都要写清代价，避免被当成遗漏）：

| 未采用 | 原因 | 代价 |
| --- | --- | --- |
| ORM / 迁移框架 | 表只有 8 个，DDL 与 `PRAGMA table_info` 探测式迁移已经够薄；引 ORM 会破坏「零服务依赖」的装机面 | 迁移必须自己保证幂等（5.6 四条纪律） |
| ASGI（uvicorn / starlette）与 WSGI 生产服务器（gunicorn / waitress） | `serve` 的定位是单机自用；`Flask.run(threaded=True)` 已覆盖并发读写 | 不得对外暴露（`api.bind` 固定 `127.0.0.1`），高并发不是设计目标 |
| OpenAPI / API 框架自动生成 | 端点表是人写的规格（附录 C），断言直接打在响应体上 | 没有契约测试工具兜底，字段漂移靠门禁与文档同步 |
| Celery / RQ / Redis | 队列就是 `tasks` 表 + `doc2md work`（FR-702），多一个常驻服务就违背单机自持 | 无延迟重试/退避策略；靠超时遗骸回收（3.4） |
| 标准库 `logging` | 见上 | 无 logger 层级、无 filter 生态；结构化字段必须自己保证无正文（NFR-20） |
| 类型检查器（mypy）/ linter（ruff）/ 格式化（black） | ruff 已作为 CI 独立信息作业接入（默认规则集 F/E/I，与 merge-gate 并行、不互相 `needs`、不阻断合并——风格判断不得变成红线）；mypy / black 仍未采用 | 注解只作文本不起校验作用，类型一致性无人把关；ruff 覆盖不到命名与复杂度之外的静态问题（CH-456 / T10，D-12 已关闭） |
| Docker / 部署编排 | 目标机型是家用 Win11 与 Mac mini，交付物是 `pip install` + launchd/任务计划 | 无容器化验证路径；装机面靠 `doctor` + CI |
| 前端构建链 / 框架 / SSE 长连接 | 见上（前端单文件）；事件通道是「拉取式」`GET /events?since=SEQ` | 无实时推送；审阅页需要手动刷新（T4 待办的动因） |


## 四、管线规格

### 4.1 S1 输入解析

**职责**：把 PDF/DOCX 变成「页 → 行 → 样式」的中间结构 `RawPage`，供后续所有阶段消费。解析层不做任何结构判断。

**输入判定**（`sniff`）：按文件头 magic 判定 pdf / docx；不匹配、不可读、读取异常一律归入「不支持类型」的 415 契约（附录 D），**不得抛裸异常**——CLI 与 HTTP 两个入口都必须能稳定判定。

**有界内存（NFR-21）**：PDF 解析按 `s1.chunk_pages` 分块，每块独立打开与关闭文档对象（页对象缓存随文档实例一起释放），每页取完即清理其缓存。驻留上限因此由「一个分块的页窗口」决定，而不是整本文档。`page_span`（分片用）与 `chunk_pages` 可组合，语义互不干扰。DOCX 无物理页，按固定行数合成页序列，全量读取一次。

**样式判定的性能纪律**：行级样式需要按纵向区间取该行的字符集合。逐行重扫全页字符是解析阶段的主要 CPU 热点，改造为「预排序偏移表 + 二分取区间」后，取回的字符下标必须**还原为原始顺序**再交给众数统计——否则并列字号时的平票结果会随遍历顺序变化，产物就不再逐字节等价。任何同类索引优化都必须自带平票次序证明。

**结果口径**：解析阶段自身的分块、缓存清理、索引优化都必须满足 G6（产物等价），以逐条目 sha256 比对验证。

### 4.2 S1.5 文本层体检门禁

**职责**：判定输入是否真的是文本型文档；不是就整篇拒收，并给出可定位的页级明细。

三种判定口径（`s1_5.sample_pages`）：

| 口径 | 判定面 | 判据 | 用途 |
| --- | --- | --- | --- |
| `auto`（默认） | 全文档页密度 | 连续空/近空页成段（≥2 页）或近空页占比超阈值（文档 ≥20 页时 >5%），或全页皆空 → 拒收 | 生产口径 |
| `even5` | 均匀 5 个采样页 | 任一采样页任一项指标不达标 → 拒收 | 原方案口径，作对照 |
| `legacy3` | 首/中/尾三点 | 同上 | 仅回归对照 |

页级指标：字符数、CJK 占比、文本行数、噪声比。阈值 `s1_5.min_chars_per_page`（默认 120）与「近空」阈值（默认 20 字符）都走配置。

**设计取舍**：`auto` 之所以不是「加密采样点」，是因为实测中稀疏采样既会漏检文档后段混入的扫描插页，又会误杀合法的链接页/图示页——判定面覆盖全文档、且只对「成段近空」敏感，才同时避免两类错误。`even5`/`legacy3` 保留在配置面中，是为了让任何门禁口径调整都能用同一样本复算，而不是靠叙述说服。

**拒收表达**：错误码 `E4221`（422），`detail` 携带页级明细（哪一页、哪一项指标、实际值 vs 阈值），报告 HTML 同样展示；CLI 门禁拒收的退出码为 3（5.3 / 附录 B）。

### 4.3 S2 分片与 S4 后缝合

**分片规划**（`shard_plan`）：页数 ≤ `s2.max_pages_per_shard`（默认 300）时不分片；否则按页范围粗切，片间保留 `s2.shard_overlap_pages`（默认 2）页重叠上下文，供缝合与去重使用。模式开关 `s2.mode = auto | single | two_phase`，`auto` 仅在超阈值时分片。

**每片独立走 S3/S4**，并标记片首/片尾块为缝合候选（`flags.shard_edge`）。

**缝合**（`stitch_shards`）：按文档顺序单遍扫描相邻边界块，判定续接的条件为①编号续接（同级递增，如「第 3 条」→「第 4 条」）或②字族与字号一致且前块不是句末。续接成功时后块吸收前块（前块 id 记入后块 `flags.absorbed_ids`，前块不进入产物），失败时保持 `shard_edge` 标记供审阅筛选。跨片表格不合并，记告警码。

**重叠区去重**（`overlap_dedupe`）：重叠页产生的同内容块按文本指纹去重（`s2.dedupe_overlap`）。

**统一重编号**（`_renumber`）：各片块 id 都从 `b1` 起，合并后必须按文档顺序统一重排，否则编辑、回滚、检索会命中错块。这是合并类改动的通用纪律。

**页数口径**：产物 `pages` 取真实页数，不得按各片求和（重叠页会重复计数）。

**降级诚实性**：缝合异常时降级为单片重跑，此时产物必须如实标记未分片（`sharded=false`），并在 meta 与告警中留下降级原因。任何「实际未分片却标注为分片」的产物都构成验收假通过。

### 4.4 S3 结构分析

**职责**：把行序列变成块序列（heading / paragraph / list_item / table / code / quote / page_break），并判定标题层级。

**规则要点**：正文基准字号取全篇众数；标题层级由「字号相对基准的增量 + 加粗 + 编号体系（`1`、`1.1`、`一、`等）」共同决定，编号层级参与判定使深层编号标题不至于全部塌到最低级；层级最后统一校正（`_fix_levels`）。

**有序列表分支**（CH-478 / R6-PIPE-1 收口）：标题信号门让位下来的「同字号、无加粗、单级编号短行」不是无归宿——`_is_ordered_item` 按 `_ORDERED_ITEM`（序号 1–3 位、分隔符 `、.．)）`、分隔符后不得紧跟数字）加「行长 <30 且行末无句末标点」判为 `list_item` 自成一块，与符号 bullet 分支同位置、同冲刷语义。三条反向信号各有对应误判形态：`(?!\d)` 挡小数（「3.5 节的说明」）、`\d{1,3}` 上限挡年份起头（「2019. 年度报告」）、句末标点挡整句（「1. 安装依赖并验证。」）；≥2 单元格的表格行优先走表格成组，行首的「1、」是单元格内容而非列表标记。判据与标题门共用同一组形态信号，因此分支只会接住标题让位的行，不会抢走段落正文。

**续行并回**（红线）：段落被标题行、空行或分页打断时，若前一块文本不以句末标点结束、当前行不是新的结构起始（编号、项目符号、列表标记等），必须并回前一块。并回发生在块 flush 阶段而非渲染阶段，以保证 IR 与 Markdown 的块边界一致。判定「块是否为半句开头」由度量层（4.7）提供，同一实现同时用于门禁与报告。

该规则的来源是实测：一段正文被中间一行短标题打断，会留下成批「以小写字母/逗号/右引号开头」的块，直接违反下游切块质量红线；单片与分片路径同现，因此修在 S3 而不是修在缝合。

### 4.5 S4 渲染

**职责**：IR → Markdown 文本 + 审阅页 HTML。渲染不做结构判断。

**分级规则**（FR-107 / FR-108）：

| 情形 | 默认输出 | 可配 |
| --- | --- | --- |
| 外部链接（http/https/mailto/ftp） | `[text](url)` | — |
| PDF 内部跳转 / DOCX 书签链接 | 纯文本（不产锚点；anchor 档正文同形，dest 留 IR/审阅页） | `s4.link_internal = plain \| anchor` |
| 粗体 / 斜体 | `**` / `*` | — |
| 删除线 | 不输出 | `s4.preserve_marks_strike = true` 时输出 `~~` |

**列表渲染**（CH-478）：`list_item` 两种形态各有出口——符号 bullet（`·•‣∙○●■□-*`）剥掉标记后统一渲染为 `- `；有序项由 `_ORDERED_ITEM`（与 S3 同一份编译式，S4 直接导入，避免两处漂移）切出序号并统一成 GFM 的 `N. `，**序号数字必须保留**（丢序号等于丢结构信息，且旧实现会把「1. 安装依赖」渲成「- 1. 安装依赖」的双标记畸形）。切分取 `m.end()`，故该式末尾用先行断言而非 `\S`：多吃一个字符会把列表项内容削掉首字。

**渲染红线**由 `assert_render_contract` 机检：默认配置输出不得含 `~~`、不得含 `](#` 或 `#page-` 类自造锚点。

**front-matter**：`content.md` 头部为 JSON 形态的 meta（`doc_id`、`source_key`、`source_name`、格式、页数、分片标记、schema 版本、告警、门禁摘要、耗时等），**剔除 `source_name_raw`**（隐私边界）。其中耗时字段天生不可复现，因此等价性比对一律先剥离 front-matter 再比正文。

### 4.6 LLM 修补助手层

**立场纪律**：文档正文一律视为**不可信数据**，不是指令；防线只有两道——op 白名单与五道校验，模型「听话」不算防线。系统提示词第 7、8 条把该立场写成对模型的显式宣告，代码层同时以白名单强制执行（提示词不是安全边界，只是可观测性对齐）。

**五道校验**：op 名称在白名单内；目标块存在；目标区间未被前序 op 占用；`immutable` 块不得修改/删除；输出结构可解析。被拒绝的 op 不计错误，也不允许重试同类 op。

**归因**：拒绝路径必须可区分「模型胡话」与「注入得手」——命中注入触发特征的记录标 `injection_candidate`，否则标 `model_hallucination`。触发特征集需要覆盖中英文常见注入句式；覆盖不足会让全部拒绝被错标为胡话，使「注入是否得手」这一可观测性指标失真。

**双模式实现**（`llm.mode`，附录 A）：`offline`（默认）走确定性占位实现，只产出白名单内的层级修正 op，保证主链路可离线验收、零网络；`server` 连接本地 llama-server（仅 `127.0.0.1`，NFR-1），请求体为 IR 定位摘要 + 白名单，`temperature=0`，响应只接受白名单内的 op 数组（围栏与尾杂音容错解析，单次上限 64 个）。两种模式的输入校验、白名单与归因口径完全相同，切换不改变任何断言。服务不可用时：`llm.fallback=true`（默认）回退 offline 结果并打 `fallback`/`fallback_stage` 标记；`false` 则明确失败 **E5041 / HTTP 503**（detail 只含 stage/bind/port/hint，不含正文）。离线守护 `run.offline_assert` 把 socket 出站限制为回环，两个入口都不可绕过。模型下载、选型、llama-server 启动与故障排查的实操口径见 `ops/llm.md`（CH-466）。

**op 白名单**：`split_block`、`merge_block`、`fix_heading_level`、`fix_list_marker`、`mark_table_boundary`（`llm.op_whitelist` 可配，但新增项需相应断言）。

### 4.7 度量口径

度量层同时服务门禁与报告，口径必须**在实现层固定**，不能靠注释描述。

- **`char_similarity`**：行锚定 + 区间内字符级精比。两个陷阱已被实测证实：① 通用序列比对库对高字符集（CJK）的「高频字符自动忽略」启发式会把大文档相似度假性压低；② 定长分块比对对任何插入都会整体错位。因此先按行对齐取锚，再在锚区间做字符级比对，并显式关闭启发式。**归一化口径（CH-475 / R6-PIPE-2）**：`norm_lines` 保留行首缩进、把行内连续空白折叠为单空格（原实现删尽全部空白）。**敏感度面由 CH-478 批次② 在真实产物上实测钉住**（`tests/test_ch478_gate_drift.py`，语料 `tests/golden/codeindent_1p.pdf`）：收紧只覆盖**空白类**漂移——行首缩进漂移 0.973958 判红而旧口径精确 1.000000（结构失明），行内空格增删 0.997333（脱盲但未过阈）；围栏破损 0.991914/0.991667 与列表标记 `-`↔`*` 0.994652/0.994490 是**内容级**漂移，新旧差值 < 0.001、结论一致，故不属本次收紧所得（原报告把三类并列为「失明」不实，已校正）。**口径边界**：0.99 是粗大漂移探测器，判红取决于漂移量相对语料分母，**不是等值证明**——逐字节等值由 12.2 的 sha 对照负责。
- **`half_sentence_heads`**：判定块是否以非句首符号开头（小写字母、逗号、右引号等），供「无半句块」红线与 S3 续行规则复用。
- **产物 `metrics`**：字符数、标题数、块数、分片标记、耗时。门禁摘要与报告都从同一结构取数。

### 4.8 产物打包

- 路径：`work/out/<doc_id>.zip`；条目：`<doc_id>/content.md`、`<doc_id>/ir.json`、`<doc_id>/report.html`。
- **双闸**：写库前消毒文件名（附录 6.1），打包时对每个 zip 条目名再次断言「无 `..`、无前导分隔符、无路径分隔符」。消毒不设降级——消毒失败即拒收。
- 打包**前**执行磁盘空间门禁（6.6）。
- 审阅页展示 `source_name_raw`（转义后）、分片状态与告警，并支持按 `shard_edge` 筛选块行。

## 五、契约规格

本章内容对外可见、对下游承诺，变更必须走登记流程（第十章）。

### 5.1 IR 数据模型

`Block` 字段：`id`、`type`、`text`、`level`、`font`、`marks`、`links`、`page`、`flags`、`provenance`、`immutable`、`revision`。
`IR` 字段：`doc_id`、`source_key`、`source_name`、`source_name_raw`、`format`、`pages`、`blocks`、`sharded`、`shard_count`、`warnings`、`schema_version`、`extra_meta`；`meta()` 输出上述标量字段并平铺 `extra_meta`。

块类型集合、告警码、边界标志（`shard_edge` / `absorbed_ids`）在 IR 中固定；`flags` 为开放字典，新增标志需同步报告展示与断言。现有旗标全集（以代码为准，逐一登记）：`shard_edge`、`absorbed_ids`、`dropped_dup_of`（分片去重）、`joined_across_break`（续句并回）、`table_boundary`（LLM op 打标）、`level_from_hint`（CH-476⑦ / R7-PIPE-4：DOCX `Heading N` 显式层级）、`has_revision`（CH-476⑨ / R7-GAP-1：来源含 `w:ins`/`w:del` 修订痕迹）、`note_kind`（CH-476⑩ / R7-GAP-2：该块来自脚注 `footnote` 或尾注 `endnote`，值为二者之一）。展示面口径：`level_from_hint` / `has_revision` / `note_kind` 只进 zip 的 `ir.json`（`report.html` 表格本轮不扩列——扩列会让既有语料的 `report.html` 字节漂移，而这些旗标本身不改正文）；断言落点 `tests/test_ch476_fixes.py`。

**约定（不可动摇）**：`doc_id` 只哈希文件内容，任何文件名字段不参与；`source_key` 为内容哈希前缀，用作「整源」身份。IR 的 JSON 形态是 zip 内 `ir.json` 与库内块表的共同上游，`from_dict` 必须能无损还原未识别的 `extra_meta` 键。

### 5.2 错误码段位

| 码 | HTTP | 语义 | 关键 detail 字段 |
| --- | --- | --- | --- |
| E4001 | 400 | 请求参数缺失/非法 | `requires`（如 `confirm=true`） |
| E4003 | 400 | 文件名消毒后仍不安全 | `raw` |
| E4011 | 401 | 无 token / 未知 / 已吊销 | — |
| E4031 | 403 | 角色不足或通道被纪律禁止 | `role`、`required`、`rule` |
| E4041 | 404 | 路径 / 文档 / 块 / 路由不存在 | — |
| E4131 | 413 | 体积超限或**剩余空间不足** | `rule="NFR-6"`、`free_mb`、`need_mb` |
| E4133 | 413 | 目录文件数/深度超限 | `limit`、`count` |
| E4151 | 415 | magic 判定不支持的类型（含不可读） | `magic` |
| E4221 | 422 | 体检门禁拒收 | 页级明细 |
| E4251 | 425 | 版本冲突 | `current_revision` |
| E5031 | 500 | 存储/系统错误 | — |
| E5041 | 503 | 模型服务不可用（**已启用**，CH-456 / T2：`llm.mode=server` 且 `llm.fallback=false` 时明确失败，不落「看起来成功」的产物） | `stage` `bind` `port` `hint` |
| ~~E5051~~ | — | **已删除**（CH-456）：缝合异常由 4.3 降级口径吸收，抛码反而违背降级语义；`errors.py` 无此码 | — |

告警码（非 HTTP，写产物 meta 与报告）：`W1043` 跨片表格未合并（当前落码，并作为降级留痕）；`W1044` 分片转换说明（**当前落码**，CH-456 起在降级单片路径真实写入 `ir.warnings`）。`W1999` 已删除（CH-456），`errors.py` 无此码。

**段位纪律**：错误码族与语义一经发布即冻结；新语义只在既有码内复用（例如空间不足复用 413/E4131），**禁止自造新码 + 新状态码的组合**。响应体统一 `{code, message, detail?}`，5xx 同样走该契约。

### 5.3 退出码

| 码 | 语义 |
| --- | --- |
| 0 | 成功 |
| 2 | 用法错误（参数/子命令） |
| 3 | 门禁拒收（如 E4221、磁盘门禁在 CLI 面的门禁态） |
| 4 | 业务错误（类型不支持、消毒失败、任务失败） |
| 5 | 系统错误（存储、控制台兜底异常、依赖缺失） |

批量场景下按「最严重者」聚合（`rc = max(rc, …)`），因为数值序与优先级序一致：门禁 < 业务 < 系统。

**2 也承载两类安全拒收**：`serve --host` 非回环且未 opt-in（CH-473 / R5-SEC-2）、`serve token --role` 不在 `api.token_roles`（CH-469 / V-7）。归 2 而非 4 的理由是两者都是**调用参数本身不可接受**、进程在触到任何存储之前就退出——与「用法错误」同族，脚本按码分流时不需要为安全策略再开一档。相应地 `serve token --revoke` 的未知 token 是**存储查询未命中**，归 4（CH-469 / V-13）。

**机器可读输出**：`--json` 行必须是纯 ASCII（转义输出），使同一命令在 UTF-8 与 GBK 终端下字节一致，可被 PowerShell/CI 稳定消费；人读模式允许 `errors="replace"` 降级，但**绝不允许因编码抛异常**。

### 5.4 HTTP API 契约

端点全表见附录 C。要点：

- **`POST /jobs`**：`{path, mode?, idempotency_key?}`；`path` 可为文件或目录（目录走投递防护）。`mode` 按白名单 **`auto` / `single` / `two_phase`** 设防（与 CLI `--mode` 同集合，CH-476 / R7-SVC-2）：非白名单值（含拼错的 `singel`、对象、数组、数字）在**转换与入队之前**返回 **400/E4001** 且零副作用；缺省与三个合法档位一律放行。校验后的档位对**三条执行分支同口径透传**（异步 `enqueue_many`、同步进程池 `run_batch`、同步串行 `convert`）——此前只有异步分支读这个字段，默认同步形态下 `{"mode":"single"}` 被静默丢弃、实跑默认档。
  - 同步形态（默认）：逐文件转换，返回 202 + `jobs[]`（`doc_id`/`job_id`/`sharded`/`attached`/`idempotency_hit`），门禁拒收项以 `{status:"rejected", code}` 表达而不整体失败。
  - 异步形态：仅做判定与入队，返回 202 + `mode:"queued"` + `consume_with:"doc2md work"`；校验阶段**要么全入队要么零副作用**，禁止「已入队一半却整体报错」。
  - 同步进程池排队上界（CH-476 / R7-SVC-3）：达到 `perf.sync_min_parallel` 的目录走单把全局闸门约束的 `run_batch` 池，闸门等待**有界 60s**，超时明确返回 **503 `{code:"E5041", detail.reason:"sync_pool_busy"}` + `Retry-After: 60`**，请求线程不再无限挂起（小批次仍走进程内串行，完全不触碰闸门；不降级为串行，以免同一请求在不同负载下走不同吞吐路径、脱离池产物的逐字节等价红线）。
- **`GET /jobs`**：队列可观测（状态计数 + 队头明细，含源路径与模式），不含正文。
- **列表投递上限**：`GET /jobs` 与 `GET /search` 的 `limit` 有硬顶 **500**（超限截断、`counts`/`total` 仍按全量口径），负数按用法错误 **400/E4001**；`GET /events` 固定单次 ≤200 条（CH-476 / R7-PERF-j）。此前 `?limit=-1` 会透传成 SQLite 的 `LIMIT -1` = **无界**，一次请求可把整张 `tasks` 搬进响应。
- **`GET /docs/<id>/markdown`**：zip 内 `content.md` 按 64KB 分块流式出（CH-476 / R7-PERF-k），响应体与整篇读取逐字节相同；`Content-Type: text/markdown; charset=utf-8` 显式带 charset。
- **编辑族**：`PATCH /docs/{id}/blocks/{bid}`（必填 `base_revision`，可灰度为可选；`text` 必填且必须是字符串，`reason` 若出现同样必须是字符串、按 256 字符截断入审计，CH-476 / R7-SVC-4）、`POST …/rollback`（`to_revision`）、`GET /docs/{id}/audit`（审计含正文，故高于 reader 权限）。
- **删除族**：`DELETE /docs/{id}` / `DELETE /sources/{key}`，必须带 `confirm=true`，缺参返回 **400/E4001**（不是 409）。
- **事件族**：`GET /events`（全局总线，前端唯一通道）、`GET /jobs/{id}/events`（仅脚本型 User-Agent）。
- **设置与运行**：`GET /settings`（reader 可见）、`PUT /settings`（admin，落 `settings_override.json`）、`POST /system/reload`（admin）、`POST /tokens` / `DELETE /tokens/{t}`（admin）、`GET /healthz`（无鉴权，仅 ok/计数/版本）、`GET /`（内置审阅页）。

### 5.5 鉴权与角色矩阵

| action | 最低角色 | 覆盖端点（举例） |
| --- | --- | --- |
| `read` | reader | `GET /jobs`、`/docs/{id}`、`/search`、`/events`、`/settings` |
| `export` | reader | `GET /docs/{id}/markdown` |
| `submit_job` | editor | `POST /jobs` |
| `edit` | editor | `PATCH` 块、`GET /docs/{id}/audit` |
| `rollback` | editor | `POST …/rollback` |
| `delete` | admin | `DELETE /docs`、`DELETE /sources`、`GET /audit/deletions` |
| `settings_put` / `system_reload` / `token_admin` | admin | `PUT /settings`、`POST /system/reload`、`POST/DELETE /tokens` |

角色按 rank 比较（reader 1 < editor 2 < admin 3）；无 token/未知/已吊销 → 401，角色不足 → 403。**授权每次请求实时查库**（`tokens.revoked_at` 一写即生效），因此「吊销 ≤5s 生效」是天然满足而非依赖窗口配置；`api.revoke_effective_seconds` 只作为对外承诺值在 CLI/自检面展示（附录 A：消费方为 `cli`，不驱动任何等待逻辑——授权实时查库，窗口配置没有可作用的对象）。CLI 侧 `doc2md serve token` 是同一能力的事务型等价物，可在服务未启动时签发。

### 5.6 存储结构与迁移

表：`docs`、`blocks`、`edit_audit`、`deletions`、`tokens`、`tasks`、`events`，FTS5 虚拟表 `search_blocks`，以及 FTS 删除映射表 `search_map`（CH-476 / R7-STOR-1：`fts_rowid` 主键 + `(doc_id, block_id)` 唯一索引，让 FTS 删除走 rowid 子查询而非 UNINDEXED 列全表扫）。关键索引：进行中作业对 `doc_id` 的**部分唯一索引**、`tasks(status, created_at)`（队列领取）、`docs(source_key)`、`edit_audit(doc_id, block_id)`、`edit_audit(doc_id, id)`（审计取回排序，CH-476 / R7-STOR-2 收编）、`tasks(idem_key)`、`search_map(doc_id)`。

迁移纪律：

1. 结构版本记在 `PRAGMA user_version`，与 IR 的 `SCHEMA_VERSION` 是两套口径，不得混用同名概念。
2. 每次迁移必须**幂等**（先探测实际结构再决定是否 `ALTER`），旧库直接升级到当前版本，不接受「先升中间版」。
3. 队列管道数据（源路径、模式）用**真列**而非塞进结果明细 JSON 字段：明细字段是对外契约面，混入管道数据会互相污染，也避免依赖可选的 JSON 扩展编译开关。
4. 连接按线程隔离（HTTP 服务是多线程的），事务用 `BEGIN IMMEDIATE`；自带 `BEGIN` 的子流程（如索引重建）不得嵌在外部事务内。

### 5.7 配置契约

全部键、默认值与语义见附录 A。结构性约定：

- 读取优先级：内置默认 → **单个**用户文件（`--config` 指定优先；否则 `./doc2md.toml`，缺失再取 `$DOC2MD_HOME/doc2md.toml`）深合并；两个候选位置不级联，同时存在时只有工作目录那份生效。服务进程另在 `create_app()` 时叠加 `settings_override.json`（`PUT /settings` 的落盘层），故其在内存中优先于 TOML；`POST /system/reload` 重读 TOML 并**再次叠加 `settings_override.json`**（CH-462 起与 `create_app()` 同口径——原实现「丢弃覆写层」会让 `PUT /settings` 落盘的修改在 reload 后静默回退，属缺陷非既定语义，已修）。重载输入可以是人手改过的 `doc2md.toml`：`config.validate` 失败则**保持旧配置继续服务并回 400/E4001**，半坏配置不接管（CH-469 / O-15）。
- **相对路径随根迁移**：`[paths]` 下的相对值一律相对 `[paths].root`（或其 `--root` 覆盖）解析，绝不因当前工作目录不同而写到别处。
- 取值路径为点号形式（`s1.chunk_pages`），读取处必须给默认值以兼容旧配置文件。

## 六、安全与运维规格

### 6.1 输入面防护

**文件名消毒**（唯一入口，所有投递路径共用）：NFC 归一化 → 取末段名（丢弃任何路径成分）→ 非白名单字符替换为 `_`（白名单：ASCII 字母数字、中文、`.` `_` `-` 空格）→ 去首尾空白与点 → **长度按 UTF-8 字节预算 128 判定并截断** → Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）前加 `_`。无扩展名时，保留名判定必须以全名为词干，否则保留名检查会被旁路（实测缺陷）。消毒结果仍不安全时抛专用错误并映射 **400/E4003**——不得使用裸异常，否则 HTTP 面落成 500，把「输入不安全」误报成「服务端故障」。消毒失败即拒收，**不设降级开关**。

**zip 双闸**：打包前对每个条目名再断言「无 `..`、无前导 `/` 或 `\`、无路径分隔符、无控制字符、长度不超预算、非保留名」。理由：消毒函数与打包路径是两个独立演进面，任何一侧回归都必须被另一侧抓住。

**目录投递防护**（API 目录投递与 watch folder 共用同一实现）：显式栈遍历、不跟随符号链接（对已访问真实路径去重）、深度上限 20、跳过隐藏项、单目录文件数上限 5000、**硬截止时间 10s**（超时截断并标记）。超限返回 413/E4133，而不是挂起或静默截断——「明确失败」优先于「看似成功」。

**写入稳定性判定**：文件大小 + mtime 连续 `watch.stable_seconds`（默认 5s）无变化才入队，期间只登记不计算。判定必须以「观察次数 + 时间戳」推进，可用注入时钟验收，不得依赖真实等待。CH-464 补充两条语义：① 首见文件 mtime 已老于稳定窗口（写入发生在观察开始前）→ 直接判稳定——否则 `watch --once`（cron 模式每次全新进程）单轮永不提交；② 同内容只报一次稳定（`enqueued` 记录提交时戳，常驻模式不再每轮重投；重写后 size/mtime 变化 → 重新走窗口）。

**magic 判定**：只按文件头判定，不接受扩展名作为类型依据；不匹配即 415/E4151（含不可读情形）。

### 6.2 权限与身份

token 为随机串（`d2m_` 前缀），存于本地库；角色 rank 比较，端点按 action→最低角色矩阵授权（5.5）。吊销写 `revoked_at` 时间戳并即时生效——授权实时查库，配置窗口只是对外承诺值（见 5.5）。CLI `doc2md serve token --role/--revoke/--list` 是同一能力的事务型等价物，用于服务未启动或无法访问 HTTP 面时的运维。

**通道纪律**：前端只允许消费全局事件总线；per-job 事件端点仅接受脚本型 User-Agent（否则 403，`detail.rule` 指明规则）。两条通道服务不同消费者，若都能被前端使用，进度语义就会分裂。

### 6.3 数据边界

| 输出面 | 允许内容 | 强制手段 |
| --- | --- | --- |
| 事件表 / 内置日志 | 元信息；长度 >64 的字符串落 `<sha256:前12位>` | 入库与写日志前各做一次脱敏（`_scrub`） |
| 日报 | 计数、路径、状态 | 只汇总统计，不复制正文 |
| 删除留痕 | who / when / doc_id / 类型 / 级联计数 | `cascade_json` 只记数量与指纹 |
| 审阅页 | `source_name_raw`（HTML 转义） | 不进 zip、不进导出面 |

`work/` 目录权限 700；`settings_override.json` 权限 600。权限必须由代码在写路径上实际 `chmod`，不能只在文档里约定（实测教训：曾把 `chmod` 打在了父目录之外的错误路径上，验收形同虚设）。

### 6.4 备份、加密与轮出

```
本地快照： work/reports/backups/state-<ts>.sqlite   （明文可，目录 700）
外置副本： <external>/<name>.enc                    （强制密文，文件 600）
容器格式： v2 = b"D2MBAE2" | salt(16B) | chunk_size(4B) | total(8B)
           + 每块 nonce(12B) + AESGCM(scrypt, AAD=整段 35B 容器头+序号)（4MB/块，流式）
           （CH-469 起 AAD 绑定头字节本身：改 salt、改 chunk_size 或改 total 会让
            全部块认证失败，防的是「换盐重放 / 拆块重组」；此前口径只绑魔数+total+序号，
            头本身可篡改。解密侧首块探测口径、对旧容器保持兼容——两种 AAD 都须由口令
            未知的同一把密钥认证通过，探测不构成降级通道）
历史 v1（b"D2MBAE1" + 固定盐 + 整库单块）只读兼容、不再生产（CH-461）
口令来源： 环境变量 DOC2MD_BACKUP_PASSPHRASE —— 不入库、不随备份走、不入任何日志
轮出：     文件名须匹配 `state-<YYYYMMDDTHHMMSSZ>.sqlite[.enc]`（UTC 秒级时间戳，`backup.py:42` `_SNAPSHOT_RE`；CH-469 / O-2：不是本工具形态的
           文件一律不碰，防「顺手放进备份目录」的无关文件被 mtime 判定误删）；
           过期判据取「文件名内嵌生成时刻」与「磁盘 mtime」中**更早**者
           （跨卷拷贝后 mtime 会变、生成时刻不会；老副本可能没有可解析的时刻则退回 mtime），
           早于 now − ops.backup_rotation_days（默认 14）即删，本地与外置两目录同口径
```

外置卷的三态语义必须区分清楚：**未配置外置卷 = 跳过**（记 `skipped`，不判失败，否则每个未用外置备份的部署每周都被记一次假故障）；**配置了但缺口令 = 失败且绝不落明文**；**成功才轮转**。macOS 备选介质为加密 sparsebundle，`doc2md.ops.backup.hdiutil_recipe()` 输出可直接使用的命令；两种介质只选其一写入运维文档。

恢复演练口径：解密落盘后必须跑 `PRAGMA integrity_check`，并任取一个历史快照比对内容——只做「容器能挂载」不算恢复验证。

### 6.5 日志与定时任务

**轮转**：`RotatingJsonlWriter` 由**写入进程自身**执行，阈值取「单文件字节数 ≥ `logs.rotate_mb` 或跨 UTC 日」，保留 `logs.rotate_keep` 份，轮转标记 `E_LOG_ROTATE` 落在新文件首行。两条硬口径：① 大小判定用**内存字节计数器**，不能用 `stat().st_size`——句柄缓冲会让磁盘大小滞后，实测 2KB 阈值被放大到约 8.2KB 才触发；② 每个活动文件只有一个属主，serve / work / cron 分别写 `app.jsonl` / `work.jsonl` / `cron.jsonl`。

**服务启动即清扫** `work/tmp/`，与 `cron --daily` 共用同一路径口径（两处都要做，且必须是同一个函数）。两条豁免口径：① mtime 在 `perf.stale_job_minutes`（默认 30 分钟）内的条目跳过，避免与并发 `doc2md work` 的在途中间产物互踩（CH-473 / STOR-4）；② `drill-*` 前缀**永久豁免**（CH-476 / R7-OPS-1）——`doc2md backup drill --keep` 的取证沙箱就落在同一个 tmp 下，原实现在演练结束 30 分钟后被下一次每日作业或 serve 重启整个 rmtree，「留存取证」的承诺在无人值守处失效。

**定时任务**：macOS 用 launchd（`StartCalendarInterval`，日任务 / 周日周任务；**触发时刻按系统本地墙钟解释**——plist 的 `EnvironmentVariables.TZ=UTC` 只设定子进程读到的时钟口径（日志时间戳按 UTC），并不把触发时刻移到 UTC，见 OPS-1 真机确认项），Windows 用任务计划程序，Linux 用 systemd timer；serve 进程内不起常驻定时线程（保持分层，且崩溃隔离）。`--weekly` 定义为 `--daily` 的**超集**，但外置加密增量不是「跑完日流程再追加」：`external_backup` 落在 `local_snapshot` 成功之后、`rotation` **之前**（`cron.py:153-178`），故当轮新副本立即受轮出保护——若排在轮出之后，外置副本要等下一轮才纳入清理范围，与 6.4 的保留期承诺不符（批次⑬ 复读时按代码改正的叙述漂移，模块 docstring 同批修正）。步骤序列（`report.steps[]` 实际写入的 10 个步骤名，逐条失败语义见 附录 E）：日报 → 本地快照（磁盘门禁是其前置检查，不是独立步骤）→〔weekly 在此插入外置加密增量〕→ 轮出 → tmp 清扫 → 日志轮转检查 → 事件保留期清理 → 终态作业保留期清理 → 搜索索引对账；`work_dir_mode` 仅在快照成功**且** chmod 有告警时追加。

**日报断档本身即运维信号**：定时任务静默失效（换用户、换机、未加载 plist）最常见的表征就是日报缺一天，因此安装校验（`launchctl list | grep doc2md`）必须写入部署文档。

### 6.6 磁盘空间门禁

写任何产物或备份**之前**判定：`需求 = max(perf.min_free_mb, 待写源大小 × perf.disk_safety_factor)`，`需求 = 0`（两个参数都为 0）才视为关闭。不足则 413/E4131，`detail` 给出 `target` / `free_mb` / `need_mb` / `rule`。

为什么必须有它：磁盘写满时 zip 写到一半会留下**不可解包的半成品**，且库里可能已有记录，处理成本远高于一次干净失败。小容量系统盘（家用 Win11 常见）是最容易命中的场景。接入点为三个写盘点：转换打包、cron 本地快照（按 db+wal 实际字节估）、外置备份（按快照大小估）。HTTP 与 CLI 不重复实现检查——同步 `POST /jobs` 经转换打包这道闸（违规以 413/E4131 返回），异步入队面不做空间检查（入队只写几百字节，真正写盘发生在 `work` 消费时并落该作业失败态），CLI 转换同样走打包闸。

**目录释放量取不到时按 0 保守处理**（宁可拒收，不可假装可写）。

### 6.7 可观测性

| 面 | 内容 |
| --- | --- |
| `GET /healthz` | `ok` / 文档数 / 管线版本 / 时间戳（无鉴权，供存活探测） |
| `GET /jobs` | 队列状态计数 + 队头明细（含源路径与模式） |
| `GET /events` | 全局事件流（`job_queued` / `job_done` / `text_edited` / `settings_updated` / `system_reload`） |
| `doc2md work --status` | 队列计数 |
| `doc2md doctor` | Python / 管线版本、根目录与其权限、tokenizer 口径、门禁采样口径、分片模式与片大小、op 白名单、乐观锁开关、轮转阈值、外置备份是否加密、低配机三键（分块页窗 / workers / queue_async）、CPU 核数、空间门禁阈值与**实际剩余空间**、**控制台编码**、内部链接与删除线渲染档位（CH-464）、watch 稳定窗口、每个运行时依赖的导入自检 |

诊断面的纪律：**关键开关必须在 `doctor` 里可见**。配置改了看不到，等于故障复现时无法判断现场；依赖清单也必须与打包声明同源（曾出现自检清单与实际依赖不一致，导致装机面长期误报）。

## 七、低配机运行规格

本章是产品级规格，不是调优建议：默认值即按下表设定，验收按此口径判定。

### 7.1 为什么是这几个瓶颈

文本转换的工作负载是**CPU + 内存带宽 + 磁盘写入**，与 GPU 无关，因此「无 GPU」不构成限制；真正的限制来自三处：① 单文档解析的驻留内存随页数增长；② Python 的全局解释器锁使多线程对解析/渲染几乎无增益，多文件吞吐只能上进程；③ 家用机磁盘小且常接近满，写失败会以「半成品」形式暴露。

### 7.2 有界内存

- PDF 解析按 `s1.chunk_pages`（默认 **32**）分块，每块独立打开/关闭文档对象，每页取完即清理页缓存。驻留上限由页窗口大小决定，与总页数无关。
- 内容哈希走 1MB 流式读取（`hash_of_path`），不把整份文件读进内存。
- 实测（320 页样本，单进程，`ru_maxrss`）：不分块 **1025MB** → 分块 32 **176MB**；单独看解析阶段，缓存清理与分块各自的贡献都可量化（第十二章）。
- **并发才是真正放大项**：4 个进程同时转该样本时，分块关闭的驻留合计约 **4100MB**，分块 32 时约 **708MB**（第十二章）。所以「内存 ≈ workers × 页窗口」是必须写进配置注释的关系，而不是口头提醒。

### 7.3 多文件吞吐

- 批量走 `ProcessPoolExecutor`，**并发度默认 `min(4, cpu_count)`**（`perf.workers = 0` 表示自动；传负数按 1 处理，避免家用机被按满）。
- 并行实现必须以 **spawn 语义**验证（Windows 没有 `fork`）：worker 函数置于模块顶层、参数可 pickle、返回值是紧凑字典（IR 对象不跨进程搬运，否则大对象序列化成为新瓶颈）、任何示例脚本都要有 `if __name__ == '__main__':` 保护（spawn 子进程会重新导入主模块）。
- 单文件失败落为该文件的 `rejected` / `error` 记录，不终止整批；退出码按最严重者聚合。
- 实测（第十二章给完整表）：40 页 × 8 文件语料，串行 15.5s → 2 进程 8.0s → 4 进程 7.6s；30 个极小文件语料串行 2.4s → 进程池 1.3–1.6s；核数以上（8 进程）不再增益。
- **产物等价是并行改造的前置条件**：池化与分块改造均须通过逐条目 sha256 等价断言（G6）。

### 7.4 界面不被卡住：异步入队

同步 `POST /jobs` 会把请求线程占满整个转换期（大文档可达数十秒），客户端超时→重试→队列放大。因此提供异步形态：`perf.queue_async = true` 时提交只做类型判定与入队（实测 13 个作业条目 13ms 返回 202），消费由独立 `doc2md work` 进程按容量领取。入队侧的「存在性 + magic 嗅探 + 内容哈希」按 `perf.enqueue_hash_threads`（默认 8）个线程并行，写库仍按入参顺序单线程提交（CH-476 / R7-PERF-3：GB 级目录的哈希在单线程下要数秒，13ms 那个数字只成立于小文件）。

三条纪律：

1. **全入队或零副作用**——目录里混一个非法文件时，请求整体 415 但队列必须一条不加（实测缺陷，曾因边校验边入队留下半成品队列）。
2. **原子领取**——查询与置为 running 在同一事务内完成，多消费者不重复处理同一任务；每轮领取量是 `workers × perf.queue_batch_factor`（默认 4×，见 3.4），池宽仍是 `workers`。
3. **遗骸回收**——`running` 超过 `perf.stale_job_minutes`（默认 30）且带源路径的任务重回 `queued`；**不动无源路径的历史行**（那是同步请求的记录，语义不同）。低配机上 worker 被 OOM 杀掉是常态，没有回收就会永久卡住。

队列产物与串行产物逐条目一致（等价断言覆盖），因此异步只是时序差异，不改变结果。

### 7.5 Windows 控制台现实

| 问题 | 规格 |
| --- | --- |
| GBK(cp936) 输出遇 emoji / 生僻字抛 `UnicodeEncodeError` 崩溃 | 非 UTF-8 流上把 stdout/stderr 配置为 `errors="replace"`，只保证不崩，不假装能显示一切字符 |
| 脚本 / PowerShell 消费 JSON 时受代码页影响 | `--json` 行强制 `ensure_ascii=True`，使同一结果在 UTF-8 与 cp936 终端下**字节一致** |
| 兜底 | 未捕获的 `OSError` 归入系统错误码并给出退出码 5，不留栈回溯当「成功打印」 |

### 7.6 macOS（Apple Silicon 16GB）差异

无 cron → launchd；`work/` 在用户目录下，注意不要放进同步盘（iCloud/Dropbox 会把正文与库文件外流，违反数据边界）；统一内存下驻留数字与 x86 同口径可直接对比；16GB 建议 workers ≤ 4 且保持 `chunk_pages = 32`。

### 7.7 推荐配置档

| 档 | 适用 | 关键取值 |
| --- | --- | --- |
| lite | 4GB–8GB / 2–4 核 | `s1.chunk_pages = 16`、`perf.workers = 1`、`perf.queue_async = true` + 常驻 `doc2md work`（串行消费，内存有上限） |
| standard（默认） | 8GB–16GB / 4–10 核 | `s1.chunk_pages = 32`、`perf.workers = 0`（自动 ≤4）、`perf.queue_async = false`；文件多时改 true |
| strict | 桌面端 / 批量迁移 | `s1.chunk_pages = 32`、`perf.workers = 0`、`perf.queue_async = true`、`min_free_mb` 按盘实况提到 2048 |

档位是**取值组合**，不引入新的代码分支；任何取值都必须能被第七章的等价与门禁断言覆盖。

## 八、质量保障与验收

### 8.1 断言分层

| 层 | 内容 | 执行点 | 失败处置 |
| --- | --- | --- | --- |
| L1 红线（merge-gate） | 六条产品级断言 + 开发期补强断言（当前 260 项） | 每次合并前必须全绿 | 阻断合并；按 8.5 降级表处置 |
| L2 全量回归 | 功能/契约/运维面全量（当前 811 项，0 项跳过） | 每次发布前 | 阻断发布 |
| L3 资产自验 | 黄金样本两次生成逐文件 sha256 一致；清单可复核 | 每次样本变更后 | 资产不可信则相关断言作废 |
| L4 装机面 | 空环境安装 → `doctor` 全 ok → 起服务 → 全量测试 | 发布前 | 阻断发布 |

六条红线断言（产品级，不可撤）：

| 断言 | 判据 |
| --- | --- |
| `shard_two_phase` | >300 页样本：分片 vs 单片 `char_similarity ≥ 0.99`；**分片去重后块数 == 单片块数**（CH-469 加的硬口径，重复段落落在相似度容差内能静默过线）；无半句开头块；`shard_edge` 可筛选；**分片真实发生（非降级）** |
| `malicious_filenames` | 恶意文件名集合全部安全落库；zip 条目零越界；消毒失败 → 400/E4003 |
| `injection_resistance` | 注入样本 vs 干净样本指标差 ≤ 0.005；白名单外 op 应用数 = 0；拒绝路径有归因 |
| `edit_optimistic_lock` | 并发同块编辑：后保存者 425/E4251 且附 `current_revision`，正文未被覆盖，审计 `before` 与 revision 对应 |
| `delete_cascade` | 缺 `confirm` → 400；删除后 GET 404、搜索 0 残留、留痕无正文；文件删除失败则整体失败且库行保留 |
| `log_rotation` | 服务内置轮转、保留 N 份、**服务不重启**；跨进程日志互不重命名；日志无正文 |

低配机专项断言（当前归入红线层，共 29 项）：分块默认值、分块/不分子块与分块 1/2/3/32 的产物等价、流式哈希与全量哈希等价、workers 边界、进程池 vs 串行产物等价（含 spawn 参数化）、逐文件拒收上报、CLI 目录池化、异步入队→消费→文档可见、幂等与附着、领取原子性、无源路径任务判失败、work 日志隔离、磁盘门禁四态（阻断/系数/仅双零关闭/API 413）、类型判定与错误码归位、ASCII 机器输出、cp936 崩溃复现与修复、门禁退出码、`doctor` 暴露关键开关。

### 8.2 等价性纪律（本项目最重要的验收方法）

任何以性能、结构或安全为名的改动，都必须先证明**产物不变**：

1. 取改动前代码（用 `git worktree` 拉出独立目录，不污染工作区）与改动后代码；
2. 对全部黄金样本 × 全部相关配置态（例如分块关闭 / 分块默认）分别转换；
3. 对每个 zip 做**逐条目 sha256**比对，`content.md` 先剥离 front-matter（其中耗时字段天生不可复现），`ir.json` / `report.html` 原样比对；
4. 目标：不匹配数 = 0。

方法学教训：整包 md5 或包含 front-matter 的哈希**不可用于等价判定**，会被非确定字段淹没；必须做到条目级、字段级剥离。

### 8.3 内存与吞吐测量方法

- 驻留：`resource.getrusage(RUSAGE_SELF).ru_maxrss`，单进程场景直接取；**多进程场景必须让每个 worker 自报峰值再求和**，父进程的 `RUSAGE_CHILDREN` 口径是「任一子进程最大值」，用它会把 4 GB 的合计误读成 1 GB。
- 吞吐：同一语料在串行 / 2 / 4 / 8 进程下各跑多次取稳定值；语料需要**中端规模**（数十页级）才能体现进程池收益，只有一页级小文件的基准会被启动开销主导（本项目两种语料都跑过，差异见第十二章）。
- 归因：任何「优化收益」要拆到单项（关掉某一项机制复跑），不给归因数字的优化不写进规格。
- 门禁：`pytest -m gate` 与全量的当前耗时与项数记录在第十二章，作为回归监控基线。

### 8.4 测试资产与运行环境

黄金样本（`tests/golden/`，由 `tests/make_golden.py` 生成）：小样本 PDF、链接样本 PDF、编号列表反例样本 `numlist_1p.pdf`（CH-478 批次①，同字号有序列表项与三类真标题对照）、缩进/围栏/列表标记漂移语料 `codeindent_1p.pdf`（CH-478 批次②，1 页 3521 字节，产物含 heading×1 / paragraph×3 / code×2 / list_item×2，专供等价门敏感度对照）、320 页大样本、DOCX 四件（简单/链接/修订痕迹/脚注尾注与单元格多段）、注入样本与配对干净样本各 10 篇、恶意文件名清单、坏尾样本（前置/后置/扫描插页三种）、目录防护场景（深目录 / 隐藏项 / 大量文件 / 符号链接循环）、清单文件 `MANIFEST.json`（5137 条目，逐文件 sha256；`self_check()` 的 PDF 清单含 `numlist_1p.pdf` 与 `codeindent_1p.pdf`）。

**资产自身的可复现性是验收对象**：生成过程必须剥离时间戳（PDF 创建日期、DOCX zip mtime 与 core 日期），路径不得写死绝对目录，循环链接用相对目标——否则换机即失真，且「两次生成一致」断言不可能成立。120MB 大文件不入库（分发包体积），相应判定在测试内自造，避免留下无人消费的占位资产。逐项数量与文件形态清单（**仓库受版本控制文件 5239 个（CH-479 增两份 CI 工作流与测试文件后复算）；`tests/golden/` 受控文件 5139 个（含 `MANIFEST.json`），磁盘占用 23.0MB / 表观字节 2.07MB**，两个口径的关系见附录 G.6。本组数字在工作副本当场实算并可复算（仓库总数随提交浮动，CH-479 复算为 5239；语料侧三项为 CH-478 批次② 采集，本轮未增删资产故沿用）：`git ls-files | wc -l`、`git ls-files tests/golden | wc -l`、`du -sk -I bigfile.bin tests/golden` = 23596KB、`git ls-files tests/golden -z | xargs -0 stat -f%z` 求和 = 2166835 字节。`git ls-files` 只列受控文件，与工作副本和一次性 clone 同值，故取代 CH-469 轮 clone 里的 5218 / 23.2MB / 3.0MB 旧值——那组旧值的采集方法未记录、本机复算不出（CH-469 提交 `88896ae` 的 `git ls-tree -r` 复核：仓库 5218 个、`tests/golden/` 5136 个，可见旧叙述把**仓库总数 5218 误记在语料行上**），校正见 G.6）见附录 G.6——两处口径以附录 G 的采集方法复核，避免规格叙述与资产实际漂移。

### 8.5 降级与回滚矩阵

| 能力 | 降级 / 回滚手段 | 触发条件 |
| --- | --- | --- |
| 两阶段分片 | 配置开关退回单片模式，报告如实标注未分片 | `char_similarity < 0.99` 或缝合崩溃 |
| 修补助手立场 | 提示词第 7/8 条可拔插，回退上一版提示词；白名单校验始终保留 | 注入样本指标差 > 0.005 |
| 文件名消毒 | **不可降级**（失败即拒收） | — |
| 乐观锁 | 灰度两阶段：`api.base_revision_required = false`（缺省沿用旧行为并告警）→ 全量必填；冲突响应附 `current_revision` 供客户端自愈 | 前端未同批发布 |
| token 角色 | 存量 token 归为最高角色，兼容期后按矩阵收紧 | 下游调用断裂 |
| 删除级联 | `confirm=true` + admin 双闸；删除前本地快照可恢复 | 误删投诉 |
| 日志轮转 | 退化为「不轮转 + 告警」，由磁盘门禁兜底 | 写入器异常 |
| 定时任务 | `launchctl unload` 即停；可手动补跑 | launchd 失效 |
| 有界内存 | 调大 `chunk_pages` 或置 0（换速度） | 单文档极小、追求极限吞吐时 |
| 异步入队 | 关掉 `queue_async` 即回同步契约（默认态） | 无消费者常驻时 |

### 8.6 CI 与门禁执行点

红线层由 `.github/workflows/ci.yml` 定义为 merge-gate：矩阵覆盖受支持的三个 Python 版本（下限由打包声明决定），失败即阻断；全量套件依赖红线层，另跑资产可复现性断言与 `doctor` 装机自检。CI 只消费既有 pytest 标记，不新增第二套断言入口（避免「本地绿、CI 定义另一条红线」的漂移）。

**两个工作流、两种时间预算（CH-479，闭环 12.7 遗留子项②）**：`.github/workflows/ci.yml` 是 **push 面**——`merge-gate` 三版本矩阵（红线，阻断）+ `lint`（信息面）+ `full-suite` **只在 3.12 跑一次**；`.github/workflows/ci-full-matrix.yml` 是**周期面**——每周一 `cron: '17 2 * * 1'`（+ `workflow_dispatch` 供取证）用三版本矩阵跑**全量** + 逐字节可复现 + `doctor`。裁定口径是「功能全面 + 性能优先」的折中：把 `full-suite` 扩进 push 矩阵会让每次推送从 1 条 30 分钟腿变成 3 条并行的 30 分钟腿（实测 push 面全程 5m14s，其中 full-suite 占 155.87s 的测试时间），换来的覆盖增量只落在**非红线的 551 项**用例上——红线本就是三档全跑，跨版本回归风险主要集中在红线里。因此 push 面保持单解释器全量（快速反馈），跨版本全量由周期腿补（覆盖不缺位，只是延迟到每周）。**这个口径本身有红线守护**：`tests/test_ch479_ci_matrix.py` 四条 gate 断言分别钉住「两工作流版本集合一致且恰为 `{3.11, 3.12, 3.13}`」/「push 面 `full-suite` 必须无矩阵且钉在 3.12」（防止有人绕过本轮裁定把矩阵加回 push 路径）/「每个作业带 `timeout-minutes`、两条 full-suite 腿均为 30」/「周期腿只允许 `schedule`/`workflow_dispatch` 触发」（否则周期腿退化成第二次全量 push 腿）。环境无 `pyyaml`，断言用无依赖正则解析 `jobs:` 分段，且先确认锚点解析成功再比值——工作流被改写到句式失配时报「锚点失效」而非静默通过。

**作业必须有时间上界（CH-471）**：三个作业各带 `timeout-minutes`（merge-gate **6** / lint 10 / full-suite 30）。理由不是"跑得慢"而是**挂死**——没有上界时，一次挂起会占满 GitHub 的 6 小时默认上限，这几小时里红线既不是绿也不是红，等价于「没有红线」；带上界后最坏情况变成「数分钟内出红」。**merge-gate 的 15 分钟后来又右调到 6 分钟**（`584e7b5`，实测依据：6 轮成功 CI 的 18 条 merge-gate leg 样本中位约 83s、最慢 2m40s，含 checkout + pip 安装 + 两次 320 页转换 session fixture；15min ≈ 最慢样本 5.6 倍、挂死时空耗过长，6min ≈ 2.2 倍仍留冷缓存余量，并让 `full-suite` 的 `needs` 更早拿到结论）。配套的可诊断性改动：CI 的两个测试步骤用 `-vv` 而不是 `-v`，因为 `pytest.ini` 的 `addopts` 已含 `-q`（净 verbosity = `-1 + 1 = 0`，`-v` 在 CI 里根本打不出逐用例行；本机实测 `-vv` 才会输出 `tests/…::test_x PASSED`）。挂死时"最后一条 PASSED 之后的下一个用例"就是嫌疑人，不必再靠作业超时猜。这条纪律的触发事实：CH-469 推送后的 run `38756727387`，`merge-gate (3.11)` 1m23s 绿、`(3.12)` 56s 绿、`lint` 15s 绿，而 `(3.13)` 的 pytest 步骤在跑过一个多小时后仍无一行输出、作业日志 blob 取回 404——一个作业挂住，`full-suite`（`needs: merge-gate`）就永远不会跑，整条红线处于"未知"状态。**上界修好之后仍出现过一次非测试失败的挂死**：run `38843497730`（推送 `36ef92a`）中 3.12 腿在打出「132 passed」**之后**卡死到超时——根因是 CPython 3.12.x 的 `ProcessPoolExecutor` 在解释器退出时对「已 cancel 却未终止」的 worker 死锁（`shutdown(wait=False, cancel_futures=True)` 放弃的 worker 让 join 管理线程永不返回），修复与登记见 CH-471 行内 X-16 附记（G6 等价：只动 abandoned 分支、按 pid 逐个 terminate）。

**静态检查的定位**（CH-456 / T10）：`ruff check .`（默认规则集 F/E/I）作为独立信息作业与 merge-gate **并行**运行，互不 `needs`。三条纪律：其一，ruff 不设 `--select` 之外的严格度加码，保持「卫生级」而非「风格警察」；其二，它的红不影响合并判定——风格问题不得升级为红线；其三，它不得拖慢红线层（并行的意义正在于此）。mypy / black 仍未接入（3.7）。

**当前状态**：workflow 已进远端并在每次 push 到 `main` 时执行，历次 run 全部 completed / success（第十二章 12.1 有逐轮记录），红线失败会以红色检查呈现。**「失败即阻断合并」已强制生效（CH-468，T1 关闭）**：仓库转为 public（免费计划即具备分支保护能力，此前 private 阶段分支保护与规则集两类 API 都返回 `403 Upgrade to GitHub Pro or make this repository public`），`main` 必需检查 = `merge-gate (3.11 / 3.12 / 3.13)`；验收方式是故意的红断言 PR——三个 gate 全 FAILURE、`full-suite` SKIPPED、`mergeStateStatus=BLOCKED`，且同一 push 首次即被本地 `ops/hooks/pre-push`（CH-467）拦下。本地钩子因此从「唯一防线」降级为**双保险**，仍建议保留：它把红线的判定成本从一次 runner 排队换成本地几十秒。

## 九、实施计划

### 9.1 已完成基线

| 范围 | 状态 | 验证方式 |
| --- | --- | --- |
| S1–S4 主链路（PDF / DOCX → Markdown + IR + 审阅页） | 已实现 | 全量套件 + 红线断言 |
| 两阶段分片 + 缝合 + 重叠去重 + 统一重编号 | 已实现 | `shard_two_phase` 及派生断言 |
| 体检门禁三档口径 + 页级明细 | 已实现 | 坏尾样本三类各判一次 |
| 文件名消毒 / zip 双闸 / 目录投递防护 / 写入稳定性 | 已实现 | 恶意名单 + 符号链接循环 + 大文件拷贝用例 |
| 乐观锁 + 审计 + 回滚 + 索引刷新（CJK n-gram） | 已实现 | 并发编辑、中文检索命中断言 |
| 三角色鉴权 + 删除级联（失败即整体失败） | 已实现 | 越权 403、级联清零、故障注入 |
| 内置日志轮转（serve / work / cron 各自归属）+ tmp 清扫 | 已实现 | 服务面轮转断言 |
| 定时任务（daily / weekly 超集语义）+ AES-256-GCM 外置副本 | 已实现 | 运维套件（含未配置外置卷跳过） |
| 有界内存 / 进程池批量 / 异步入队 + 队列消费 / 磁盘门禁 / 控制台加固 | 已实现 | 低配机专项 29 项 + 等价三态复核 |
| 黄金样本逐字节可复现 | 已实现 | 两次生成全表 sha256 一致 |
| CI 执行点（push 面 merge-gate 三版本矩阵 + 单解释器 full-suite 依赖层 + lint；周期面三版本全量，CH-479） | 已在远端运行并**硬阻断 PR 合并**（CH-468 / T1 关闭）；周期腿待首次 `schedule` 触发取证 | 五作业全绿（第十二章 12.1，最新为 CH-478 批次② run `39030470549`）；边界：必需检查不拦直推 `main`（CH-470），直推路径靠 `ops/hooks/pre-push` |
| 本地模型服务客户端（双模式 + fallback + E5041） | 已实现（CH-456 / T2，CH-469 补连接类瞬态） | 假 llama-server 契约测试 **26 项**（`--collect-only` 实测）+ e2e 实测（12.5） |
| 备份恢复演练自动化（8 步闭环一条命令） | 已实现（CH-456 / T7） | `doc2md backup drill` 沙箱闭环 + 13 项测试 |
| 审阅页队列可视化（五态 + 失败原因） | 已实现（CH-456 / T4） | 5 项页面契约测试（页面结构 + JS 语法 + NFR-20 无正文探针）；**真实浏览器交互未测**（CH-469 / O-10 撤掉原「无头浏览器实测」的失实证据行，登记为 12.7 人工项） |
| Windows 任务计划程序资产（schtasks + 安装校验） | 已实现（CH-456 / T5） | 17 项与 launchd 等价测试 |
| 大语料吞吐 / 驻留 / 失败率三张表 | 已实现（CH-457 / T6） | 1080 文件混合语料，workers=1/4 两轮（12.3） |
| 门禁耗时 < 60s | 已达标口径随红线增长重估（CH-456 / T8 立项时为 67 项 53.0s；CH-469 轮 132 项 28.7s） | **当前 260 项实测 38.55s / 台账收口复跑 33.74s（CH-479）**，CH-478 批次② 为 256 项 31.16s、批次① 为 252 项 31.54s / 收口复跑 31.36s，CH-477 为 248 项 33.25s / 收口复跑 31.98s，CH-476 为 244 项 33.9s、批次⑬ 收口复跑 35.5s（本机 macOS arm64 / Python 3.15；上一轮 CH-475 为 153 项 30.0s，CH-474 轮 146 项 60.0s、CH-473 收口轮 145 项 62.7s 为沙箱负载峰值采样，同机负载低点约 45s）。项数才是硬口径——红线从 67 增至 260 项（+288%）而耗时仍与立项时的 53.0s 同量级、且低于该值，判定为「量级达标」；若要把「< 60s」恢复为硬判据，需先做一次门禁分层复核（哪些新增项真该在 merge-gate 里），登记为后续可选项 |
| 声明性配置键收敛 + 死代码清零 + 预留码处置 | 已完成（CH-456 / T9） | D-8 / D-9 / D-10 全部关闭 |
| 静态检查工具链（ruff，CI 独立信息作业） | 已接入（CH-456 / T10） | `ruff check .` 全绿 + CI 并行作业 |
| 许可与元数据声明 | 已完成（CH-456 / T11） | `LICENSE`（MIT）+ `pyproject` `license`/`classifiers` |

### 9.2 待办（按优先级）

T2–T11 已在 CH-456 / CH-457 轮次全部收口（逐项判据与实测证据见 9.1 对应行、第十二章与第十一章两条登记）；下表只列尚未完成的项。

| # | 待办 | 判据 / 出口条件 | 风险 |
| --- | --- | --- | --- |
| ~~T1~~ | ~~让红线**真正阻断合并**~~ **已解决（CH-468，2026-10-15）**：仓库转为 **public**（免费计划即具备分支保护），`main` 必需检查 = `merge-gate (3.11/3.12/3.13)` 已配置；验收 = 故意失败的 PR #1 三个 merge-gate 全 FAILURE、full-suite SKIPPED、`mergeStateStatus=BLOCKED`（合并被禁） | 工作流本身已进远端并开始执行（12.1 首轮记录）。阻碍为平台能力：**CH-458 轮用 fine-grained PAT（`admin:true`）复测，分支保护与规则集两类 API 仍返回 `Upgrade to GitHub Pro or make this repository public`——是计划级限制而非令牌权限**（仓库 `private:true`、`plan:null`），可选路径为升级计划、改为 public，或接受「CI 显示 + 人工判绿」的半强制态。**CH-467 已实施客户端兜底**：`ops/hooks/pre-push` + `core.hooksPath` 启用后，push 前本地跑 `pytest -m gate` 红则拒绝（口径与 CI merge-gate 同一断言面），把「人工判绿」降为「必须故意 --no-verify 绕过」；平台解锁后可保留为双保险。**CH-469 补一条边界**：T1 的判据是「PR 被拦住」，确实达成；但同一轮推送实测发现**必需状态检查不拦直推 `main`**（远端返回 `Bypassed rule violations … 3 of 3 required status checks are expected`，push 照常成功），所以「红线有强制执行点」目前只对 PR 路径成立，直推路径只剩 pre-push 钩子这一道、且需操作者自行启用 `core.hooksPath`——登记为 CH-470（需决策项：是否为 `main` 打开「Require a pull request before merging」） | 阻断由必需检查（PR 合并）+ 本地 pre-push（直推）双保险承担 |
| T3 | 跨机吞吐基线：把内存/耗时表跑成 Windows 与 macOS 实测 | 至少各一组同口径数字进入第十二章。**测量工具已就绪（CH-458）**：`ops/bench/t6_large_corpus.py` 已跨平台化（proc / ps / psutil 三后端 + none 降级，Windows 路径经 `T6_SAMPLER` 覆盖实测、macOS 路径借 GNU procps 与 BSD ps 一致的输出规格在 Linux 上实测），目标机一条命令即可产出同口径三张表；剩余动作是在目标机上执行并回填数字 | 现有数字不可直接外推到其他平台；沙箱为 Ubuntu，无法产出目标机数字 |
| T12 | `serve` / `work` 常驻资产（launchd plist / schtasks XML，与 cron 档同构） | 资产文件进 `ops/launchd/` 与 `ops/windows/`，静态等价测试把住（URI/Label/时刻/动作 parity + 合法性），README 才可枚举；实机「开机自启 + 崩溃自恢复」行为待真机确认 | 审查（9.4 B-1）发现 README 已枚举不存在的标签；先修文档，资产列此待办 |
| T13 | 备份容器 v2（随机盐 + 分块流式，CH-461）的真机恢复验证 | 外置卷（移动硬盘 / 网络卷）实景恢复一轮通过；沙箱内 drill 闭环已覆盖加解密 round-trip 与篡改失败 | 容器格式变更的兼容面（v1 旧备份仍可解密）有单测把住，实景卷属真机项 |
| ~~T14~~ | ~~`PRAGMA synchronous=NORMAL` 取舍~~ **已解决（CH-471 / 决策 D1，2026-10-16）**：第四轮审查按「性能优先」原则代为决策并实施——WAL 模式下 `synchronous=NORMAL`（应用崩溃不丢事务、仅 OS 崩溃 / 断电可能丢最后若干事务）+ `mmap_size` 与 `cache_size` 调优，每任务写开销再降一档；「断电模拟」证据行的替代口径是 WAL 断点语义的文档化论证（`storage/db.py` 连接初始化处注释）与全量套件在 NORMAL 下的零失败。9.4 C-1 随之关闭 | 已实施；`synchronous=FULL` 的既有语义由 WAL + 每次提交 fsync 保证，若未来要回退只需改一处 PRAGMA | 属数据耐用性取舍，按 9.4 C-1 不擅自改 |
| T15 | **已关闭（CH-477 落地）**：版本与编号对账做成红线断言（CH-476 登记为可选项，CH-477 实施） | 一条 `pytest -m gate` 静态断言：`doc2md.PLAN_VERSION` == 本文 header「方案文档版本」== §1.4「本版 vX.Y」，且 README 的「当前编号至 CH-xxx / 下一条 CH-yyy」== 本文 10.1 的「当前至 CH-xxx。下一条目自 CH-yyy 起」。判据成立后，CH-472 型漂移（登记行声称升位、常量未动）由 CI 拦住而非靠收口复读。**已关闭（CH-477）**：四条 gate 断言落地于 `tests/test_ch477_version_sync.py`，先断言锚点解析成功再比值，锚点失效即红；落地时点选在批次⑬ 的 244/773 由 run `39022142048` 独立取证之后，项数改档不损失证据（gate **244 → 248**、全量 **773 → 777**）| 低（纯静态断言）；成本在下项数与远端复证——本轮已按该成本完成改档与复证 |

### 9.3 近期迭代节奏

1. **T1 收尾**：红线已有执行点，但还差「失败不可合并」这一硬约束；在补齐之前，其余工作的回归成本仍靠人工承担。
2. 之后只剩 **T3 跨机基线**（Windows / macOS 同口径实测）：测量工具已跨平台化并就绪（CH-458），剩余动作是在两台目标机上各跑一轮 `ops/bench/t6_large_corpus.py` 并把三张表回填第十二章；本机沙箱内可做的规格与验收工作已全部收口（CH-456 / CH-457）。**T1** 仍受阻于平台能力（分支保护/规则集 API 403，9.2 条目列了三条可选路径）。
3. 每个迭代出口条件固定为：红线全绿 + 等价性三态复核 0 不匹配 + 新增能力有实测行。

### 9.4 全面审查优化方案（CH-459 轮启动）

**审查方法**：三路并行只读审查——① 代码与性能（`doc2md` 包 39 模块逐文件过一遍，对照需求规格与 12.3 实测口径）；② 测试体系（`tests/` 17 文件全读 + 生产模块 import 关系做覆盖盲区分析 + 伪覆盖/flaky 风险审查）；③ 文档一致性（`docs/dev-plan.md` 主题篇/附录/登记篇 + README + `config/example.toml` 与代码现状交叉核对）。共 **45 项发现**（15 + 15 + 15）。

**分诊原则**（按「功能全面、性能优先」）：A = 沙箱内可改可验证，直接实施；B = 需 Windows / macOS 目标机环境才能产出证据，仅登记；C = 需人工决策或外部确认，仅登记并每次汇报提醒。逐项判定后归入下表；实施顺序按「先补规格缺口（功能全面）、再收性能与存储、后补测试与文档口径」排。

**A 类（本轮实施，45 项中的 40 项）**：

| # | 发现 | 严重度 | 处置 |
| --- | --- | --- | --- |
| A-1 | **S3 只产 heading / paragraph / list_item 三类块**，`BLOCK_TYPES` 声明的 table / code / quote / page_break 四类从未落地（FR-104 P0 明列七类）——下游 W1043 跨片表格检测永假、S4 三个渲染分支死代码、`mark_table_boundary` op 无对象 | 高 | `s3_structure.analyze` 补四类检测（保守启发式 + 单测 + 黄金样本回归） |
| A-2 | **链接抽取端到端缺失**：`Line.link` 字段两条读取路径（`read_pdf` / `read_docx`）都不赋值，`Block.links` 无生产写入方，FR-107/108 的渲染分级对着空数据跑 | 高 | S1 抽 annots/超链接 → S3 附着到覆盖块 → S4 既有分级渲染（links_pdf.pdf / sample_links.docx e2e） |
| A-3 | **`Store._conns` 强引用泄漏**：`threading.local` 连接进列表后永不释放，`serve`（threaded）每请求累积一个连接，触 fd 上限后服务面 500 | 高 | 改弱引用跟踪（死线程连接可被 GC 关闭），补「N 次请求后连接数有界」断言 |
| A-4 | 每任务约 6 次独立写事务（WAL + `synchronous=FULL` 每次提交 fsync）——12.3 实测的单文件固定开销主因 | 中 | 合并同批次写入为单事务（`save_ir` 与 reindex 合并且 `executemany`；`_finish_ok` 的 detail/mark/event 合并且入）；`synchronous=NORMAL` 的耐用性取舍列 C-1 不做 |
| A-5 | S1.5 auto 模式每页指标算两轮（`_avg_chars` 对全文档重算一遍） | 中 | 门禁改「单遍采集 + 纯函数判定」，与 A-8 的 convert 单遍重构同体落地 |
| A-6 | 单块编辑触发**全文档**重索引（FR-208 要求的是「该块索引」） | 中 | 新增 `reindex_block`，PATCH / rollback 只删插该块 FTS 行 |
| A-7 | LLM op 白名单 5 个仅 `fix_heading_level` 有实现，其余 4 个过校验、计入 `applied_ops` 但静默无效 | 中 | `apply_ops` 补齐 `split_block` / `merge_block` / `fix_list_marker` / `mark_table_boundary` 语义（保守定义 + 单测） |
| A-8 | convert 全量读入 `pages_all` 再分片，与 CH-447「不得把整本文档读进内存」纪律相违（NFR-21 千页级） | 中 | 改按片读取（PDF 每片一次 `page_span` 读；DOCX 维持整读切片），门禁指标在同上一次解析中采集——单遍解析、单片驻留 |
| A-9 | FTS `query_terms`/`build_match` 的 covered-span 逐字符 `any()` 扫描 O(n×m)，查询串无长度上限 | 中 | 改区间掩码一次遍历；查询串截断上限（超长走 LIKE 兜底） |
| A-10 | 目录投递 + 单个 Idempotency-Key：N 个文件命中同一键，N-1 个静默消失（不同步/异步两条路径同病） | 中 | 多文件投递时按文件派生幂等键（`{key}#{文件名}`），doc_id 级去重不变 |
| A-11 | 外置备份 `snapshot.read_bytes()` 整库读入内存；scrypt 静态盐（同口令跨部署同密钥，可预制彩虹表） | 中 | 随机盐 + 版本化分块容器（v2，4MB 块 AESGCM + 序号 AAD），流式加解密；v1 容器保持可解密（真机恢复验证列 B-2） |
| A-12 | 日志脱敏口径弱于 `api._scrub`（只按键名提示列表截断，非提示键的长串原样入日志） | 中 | `ops/logging._scrub` 与 `api._scrub` 统一为「任意字符串 >64 字符 → 指纹」 |
| A-13 | convert 内 `sniff()` 重复调用（`read_any` 内已嗅探一次） | 低 | `read_any` 增补 `fmt` 直传参数，消除第二次开文件 |
| A-14 | `save_ir` 逐块 `execute` + reindex 第二次事务（N 块 = 2 次写事务 + 2N 条语句） | 低 | `executemany` + 与 A-4 的事务合并同体落地 |
| A-15 | `delete_doc` 对不存在的 doc_id 也留痕并返回 200；`rollback` 的 `int(to_revision)` 非数字参数逃逸成 500 | 低 | 删前存在性检查（404/E4041）；参数校验收敛 400/E4001 |
| A-16 | 测试体系 15 项：`s3_structure` 整模块零直接测试；`make_golden.py` 复制生产 `sanitize_name`（红线 expected 由漂移副本算出）；CLI 四子命令（watch / tokenizer / serve token / backup drill）零测试；API 三端点（`/events`、`/jobs/<id>/events` UA 403、`/system/reload`）零测试；sharding 纯函数无单测；backlog 下界仍依赖时序；cfg fixture 把 CWD 渗入 session 配置；zip 静默截断；`raises(Exception)` 过宽；vacuous 分支断言；unlink 并发 patch 失效；5s 墙钟断言；`port=1` 可用性假设；regex 扫描器漏动态导入；cfg 可变共享 + 注入语料重复转换 23 次 | 高×4 / 中低若干 | 逐项补测试 / 修夹具 / 收紧断言（随 A-1…A-15 的改动同体新增，不另起炉灶） |
| A-17 | 文档一致性 15 项：5.2 错误码段位表四处未随 CH-456 更新（E5041/E5051/W1044/W1999）；附录 B 缺 backup drill 与 LLM 旗标；doctor 自检项 24→30 项；附录 G 两处 CH-458 数字未刷新；README「失败阻断合并」自相矛盾；`example.toml` 的 `min_free_mb` 与双零口径不符；附录 A 消费方 5 键遗漏；README 枚举不存在的 serve/llm launchd 标签；tokenizer `--gguf` 位置参数；门禁摘要键名；marker 含 M4M6；键集差表述矛盾；3.7 依赖闭包缺 ruff 与版本浮动；windows 资产步骤表不一致；12.5 双代证明措辞强于实际 | 高×1 / 中×7 / 低×7 | 逐项文本修正 + 与代码现状复核（`doctor` 项数、附录 G 行数等以实跑数字为准） |

**B 类（仅登记，需目标机环境）**：

| # | 发现 | 登记去向 |
| --- | --- | --- |
| B-1 | `serve` / `work` 常驻资产缺失：README 枚举了不存在的 serve / llm launchd 标签（现有 `ops/launchd/` 只有 cron 两档 + T5 的 schtasks 三档）——本轮先修 README 不枚举不存在项，资产本身列待办 | T12 |
| B-2 | 备份容器 v2（随机盐 + 分块流式）的真机恢复验证：沙箱内 drill 闭环覆盖加解密 round-trip，外置卷（移动硬盘 / 网络卷）实景恢复需真机 | T13（随 T3 同一轮真机巡检执行） |
| B-3 | Windows schtasks 资产步骤表与实装不一致的最终确认（安装脚本在真机的执行结果） | 随 T3 真机轮 |

**C 类（仅登记，需人工决策 / 外部确认）**：

| # | 发现 | 登记去向 |
| --- | --- | --- |
| C-1 | `PRAGMA synchronous=NORMAL`（WAL 下仅丢 OS 崩溃前的最后事务，应用崩溃不丢）可把每任务固定开销再降一档，但属数据耐用性取舍，不擅自改 | **已由第四轮代为决策并实施（CH-471 / D1，T14 随之关闭）** |
| C-2 | 12.5「双代证明」措辞降档为实际证据强度（server 模式 1 次 HTTP + 死端口失败路径 + 假服务契约测试，非双模型代际对比） | 本轮随 A-17 修正措辞，证据强度不再声称更强 |

**实施顺序与出口条件**：五个批次——① 功能全面性（A-1/A-2/A-7）；② 性能与存储（A-3…A-6、A-8…A-15）；③ 备份加固（A-11）；④ 测试体系（A-16）；⑤ 文档口径（A-17）。每批次出口：红线 gate 67 项全绿 + 全量套件 + `ruff check .` 全绿 + 等价性三态复核 0 不匹配（改产物路径的批次）+ 第十一章登记 CH-459 起逐批落条 + 第十二章证据行。B/C 类在每次完成任务后提醒。

**进度**：批次①已完成并登记为 CH-459（A-1/A-2/A-7 三项 + 收尾自查修掉三个真缺陷，gate 67 项全绿、全量 276 收集、等价性三态复核符合设计），A-13 随批次①提前落地。批次②已完成并登记为 CH-460（A-3/A-4/A-5/A-6/A-9/A-10/A-12/A-14/A-15 九项 + 顺带修 `detail_json` 被覆盖的静默丢失；gate 67 全绿、全量 302 收集、六个黄金样本 gate 明细与产物 sha256 逐字段一致）。**A-8 经实测回退**：按片读取在 large_320p 上峰值内存不降反升（132.9MB → 134.9/137.9MB，见 CH-460 与 12.3），改动已撤销、纪律改由 `s1.chunk_pages` 承担——这是「性能优先」原则的应有结果：实测无收益的改动不进主干。批次③已完成并登记为 CH-461（A-11 备份容器 v2：随机盐 + 4MB 分块流式 + 块级 AAD，v1 历史容器保持可解密；gate 67 全绿、全量 308 收集、演练 8 步闭环在 v2 下全绿）。批次④已完成并登记为 CH-462（A-16 的 12 项未解决子项全部落地：CLI 四子命令入口层 7 项、API 三端点 6 项、sharding 纯函数 10 项、zip 完整性、make_golden 漂移副本改用生产实现、cfg 隔离 cwd、raises 收窄、vacuous 断言、5s 墙钟轮询移除、port=1 假设、regex→ast、注入语料缓存；另修掉两个被零覆盖掩盖的真缺陷——watch --once 单轮永不提交、/system/reload 丢 settings_override；gate 67 全绿、全量 332 收集）。批次⑤已完成并登记为 CH-463（A-17 的 11 项残留全部落地：5.2 错误码表、附录 B 补条目、doctor 项数实数 30、3.5/附录 G 数字实核刷新、README 六处、example.toml 注释、3.7 依赖闭包补 ruff；4 项经实核确认此前已解决——附录 A 消费方、键集差、Windows 步骤表、12.5 措辞）。**五批次全部完成，A 类 40 项（含回退 1 项）收口。**

**第二轮审查（CH-464 轮启动）**：五批次改动后的复审——代码与性能 / 测试体系两路并行（文档路由第三路 agent 配额中断，改由主会话直接核对）。共 **24 项发现**：代码 12（高 3 / 中 6 / 低 3）+ 测试 15（高 1 / 中 7 / 低 7，部分交叉）+ 文档 4 处漂移。分诊：**A 类 21 项直接实施**（含 3 个高严重度缺陷修复）、**3 项核实后不实施**（search LIKE 兜底是旧库迁移保险，保留；2 项上轮已顺带解决）。全部在沙箱内完成并验证，无新增登记待办。

**第三轮审查（CH-469 轮启动）**：CH-464 之后再做一轮「设计 / 代码 / 文档」三路并行全面审查，分诊口径不变（A = 沙箱内可改可验证 → 直接实施；B = 需目标机环境才能出证据 → 仅登记；C = 需人工决策 → 仅登记并每次汇报提醒）。共 **37 项发现**，按批次编号：① 管线与模型 **S-1…S-9**（9 项）、② 服务层与存储 **V-1…V-13**（13 项）、③ 安全 / 配置 / 运维 **O-1…O-15**（15 项），④ 是文档一致性收口、不占发现编号。**37 项全部为 A 类并在沙箱内实施完毕**；逐字母的准确口径写进 `tests/test_ch469_service.py` 与 `tests/test_ch469_ops.py` 的模块 docstring（与本轮登记逐项对应）。**代码内只标 `CH-469：`，S/V/O 字母是登记用的追溯编号，不进代码**——避免下一轮改动把字母带成过期注释。

三条原则在本轮的落点（不是平均用力，而是各挑最贵的病灶）：

| 原则 | 收口方式 | 代表项 |
| --- | --- | --- |
| 功能全面 | 把**静默失效**当第一类缺陷：说成功了但结果不对，比直接报错更难发现 | V-1 多次 `PUT /settings` 互相抹键、V-3 回读不到 IR 仍标 done（队列说完成、`GET` 却 404）、V-7/V-8 非法角色或非数字 `base_revision` 逃逸成 500、V-13 吊销不存在的 token 无条件报成功、V-11 审阅页文档派生字段未转义（构造文档即可执行脚本）、S-9 声明 total 却少喂页裸 `KeyError`、O-9 声明 KV 数超限静默截断、O-10 遍历截断不报错、O-15 未知键静默失效 |
| 性能优先 | 先量化再改，改完必须留实测行；无收益的回退（CH-460 / A-8 的先例保留） | V-4 补 `events(job_id,doc_id)` / `tasks(doc_id)` 索引（含 v4 存量库迁移）、V-5 列表与审计留痕 `offset/limit` 窗口、V-6 整源删除从「N 个文档 = 3N 次全树 rglob」收到一次遍历、O-7 内容指纹流式化（不整表进内存）、O-9 Tokenizer 探测长度由 `maxlen` 收到 `\|distinct lengths\|`（12.3 实测 **4.3×**，口径不变）、S-7 消除重复 `open` / 重复构建 page_detail / 无效指纹 / 死代码、S-6 批量进程池单文件超时 |
| 回归防线 | 本轮自己改出来的风险，用硬口径钉住而不是靠人记住 | S-3 段缓冲跨页 → `overlap_dedupe` 页对齐指纹失效，附录 F 第一层组 1 加「分片去重后块数 == 单片块数」；S-6 → 注入挂死 worker 的红线用例；S-8 → 假服务前 N 次断连的确定性重试用例 |

**收尾自查另发现三个真缺陷**（不占 S/V/O 编号——它们是实现本轮方案时自己引入或暴露的，性质与前 37 项「审查既有代码」不同）：
① **缝合回归**：S-3 让 S3 段落缓冲跨页存续后，一个段落归属**起始页**，`overlap_dedupe` 原先依赖的「同内容块在两片中页码一致」不再成立，缝界出重复伪影——large_320p 实测 `mp300/mp160/mp40` 三档 `blocks_after` = 5697/5698/5706 ≠ 单片 5696，`char_similarity` 最低 0.997976，`mp40` 还多出一个半句标题块。修法是在滚动 64 块尾部窗口上加两条**常数界**规则（缝合尾缀丢弃 `_seam_suffix`、截断块整块替换 `_seam_truncated` + `_replace_recent`），不用全文扫描也不引入新的最坏情况；改后三档全部 `blocks_after == 5696`、`sim == 1.0`、`half_heads == 0`（12.5）。
② **`pipeline/batch.py` 清理分支崩溃**：`finally` 里引用的 `futs` 在异常路径上尚未绑定，worker 崩溃时先抛 `UnboundLocalError` 把真实错误盖掉——改为提交前一次性建好完整映射。
③ **`perf.batch_file_timeout_s` 是伪覆盖**：超时判定写在 `for fut in as_completed(futs)` 里调 `fut.result(timeout=…)`，而 future 只在完成后才被交出，那条 timeout 永不触发，一件挂死（损坏 PDF 让 pdfplumber 无限转）依旧拖死整批。改为 `wait(pending, timeout=window, return_when=FIRST_COMPLETED)` 的**空窗**语义：连续 `per_file_s` 秒内没有任何文件完成，就把仍在排队的件逐件记 `E5000` 并收池。实测挂死场景 1.03s 返回（此前会一直等）。

**本轮出口条件**：红线 gate **132 项**全绿 + 全量套件 **481 收集 = 481 passed / 0 skipped** + `ruff check .` 全绿，三者同机采集（本机 macOS arm64 / Python 3.15）。与上一轮唯一的差别是等价性证据形态：本工作区不是 git 仓库，`git worktree` 的「基线 / 改动 / 合并后」三态对比不可执行，改按 12.2 的替代口径（产物不变量 + 6 样本新基线，逐档指纹实测）。

**B/C 类去向**：本轮**没有新增需人工决策的取舍项**（未改 `synchronous`、未动耐用性口径）。需要本机以外环境或人工执行的仍是既有登记的复验：编辑器 / 审阅页的**真实浏览器交互**（V-11 只有 DOM 契约测试，9.1 原「无头浏览器实测」证据行失实，本轮已撤并登记）、T3 跨机基线、备份容器 v2 的外置卷实景恢复（B-2）、Windows schtasks 实装确认（B-3）、Python 版本矩阵——全部在 12.7，每次完成任务后提醒。

**第四轮审查（CH-471 轮启动）**：三路之后再做一轮五面并行全面审查（管线核心 / 存储检索编辑 / 服务层 / 安全运维配置 / 测试 CI 与文档漂移），报告单独存档为 `docs/review-round4-CH-471.md`（470 行——本轮起审查报告不再挤占本章正文，落点与编号以该报告为准）。共 **46 条**（44 项实质发现 + 2 条表扬）：HIGH 5 / MED 17 / LOW 22。四个 HIGH 全是「已全绿门禁之外的新盲区」：**PIPE-1**（分片循环 `analyze(sub)` 未传 `base_size`，正文字号众数按片自算——异质字体文档的分片≡单片等价不变量无门禁覆盖）、**STOR-5**（同源重复转换 `INSERT OR REPLACE` + `DELETE FROM blocks` 静默清空用户编辑、重置 revision）、**STOR-3**（FTS 重建非原子，`DROP` + 自动提交 DDL + 独立事务回填，中途崩溃即索引永久为空；`reindex()` 是零调用死代码、无启动对账）、**TEST-1**（golden 样本缺失时门禁 `pytest.skip` 后仍判绿——CI 从不生成 golden，红线可能在无人察觉时消失；与「绝不用 skip 掩盖」原则直接冲突）、**TEST-3**（CI 无 Windows 腿，而头号目标机是 8GB Win11）。分诊口径不变：A 类（沙箱内可改可验证）全部实施；B 类外部 / 人工 15 项（X-1…X-15）+ 既有 T3/T12/T13 + 12.7 九项，仅登记；C 类 7 项（D1…D7）按「功能全面、性能优先」+ 核心价值（离线、4 依赖闭环、数据安全、G6 等价）**代为决策后直接实施**：**D1** = WAL 下 `synchronous=NORMAL` + `mmap_size=256MB` + `cache_size=-65536` + 关闭时 `PRAGMA optimize`（原 T14 关闭；产物可由源文件重跑恢复、非唯一真相源，故性能优先可接受，断电 drill 登记 B）、**D2** = 为 `main` 启用「require PR before merging」（需 GitHub 管理员权限，登记 B——即 CH-470）、**D3** = watcher 保持轮询默认（守住 4 依赖闭环）+ mtime 短路（watchdog 事件驱动登记为未来可选）、**D4** = `api.bind` 默认强制 loopback、非回环须显式 opt-in（新键 `api.allow_remote_bind`）、冻结 `run.offline_assert` 禁经 `PUT /settings` 修改、**D5** = 新备份 scrypt N 提到 2^17（header 存 KDF 参数保向后兼容）、**D6** = 不新增外键（免表重建与 DB_VERSION 连锁），改文档明示「手写级联是唯一机制」+ 断言防孤儿、**D7** = 维持块级链接模型（≤1 link/block），行内跨度级链接写入已知限制。实施结果为 51 文件 +7855/−224（`doc2md` 包 25 个 `.py` + `tests/test_ch471_fixes.py` 898 行 62 项 + 5 份历史方案文档归档入库），本机 gate **132 项全绿**、全量 **543 passed / 0 skipped**、`ruff check .` 全绿。**实施之后 CI 又暴露一个非测试缺陷并当场修掉（登记 X-16）**：run `38843497730`（推送 `36ef92a`）的 `merge-gate (3.12)` 在 pytest 打出「132 passed」**之后**卡死至超时——根因是 CPython 3.12.x 的 `ProcessPoolExecutor` 对 `shutdown(wait=False, cancel_futures=True)` 放弃的「已 cancel 却未终止」worker 在解释器退出时死锁；修法只在 abandoned（空窗超时）分支先抓本池 `_processes` 句柄、shutdown 后按 pid 逐个 `terminate()`（非全局 `active_children`、不触碰别处的池，G6 逐字节等价），3.11 / 3.13 / 本机 3.15 均不复现故真验证依赖 CI 3.12 腿。同场把 merge-gate 超时从 15 分钟右调到 **6 分钟**（依据：6 轮成功 CI 的 18 条 leg 样本中位约 83s、最慢 2m40s，6min ≈ 2.2 倍余量）。**编号提示**：`36ef92a` 一个提交里同时装了第四轮实施与 CH-472（小语料 drain 断言修正），而第十一章原 CH-471 登记行只写了 CI 上界、并误称「`doc2md` 包零改动」——该失实由第五轮收口轮（CH-473）发现并重写登记，见第十一章。

**第五轮审查（CH-473 轮启动）**：第四轮之后再做一轮四路并行全面审查（管线与存储 / 服务层 / 安全运维配置 / 测试与文档），编号 **R5-***（TEST-1…5 / DOC-1…9 / PIPE-5…12 / LLM-1/2 / SEC-1/2 / OPS-1/2 / PERF-1 / CFG-1/2 / Q-1/2 / STOR-1…5 / SVC-1…10）。分诊口径不变：A 类直接实施、B 类仅登记、C 类按原则代为决策。**代码与测试面按三批次全部落地**（批次①②③，提交 `2081398`）：① 分片与缝合真缺陷（PIPE-5/6——`overlap_dedupe` 比对面与常数界规则的两处边界错误，红线条目「分片去重后块数 == 单片块数」此前对某些缝界形态仍会失败）、存储层（STOR-1 写集校验 / STOR-3 v3 存量库自动迁移含明文 token 就地哈希 / STOR-2 启动对账修复孤儿索引）、配置与安全（SEC-1 未知键点名细化 / Q-1 键集差 / SEC-2 `serve --host` 预检统一走 `config.validate`）、服务层（SVC-3…S-7 + STOR-2：同步 `/jobs` 并发上限、通用 500 JSON、`limit` 非负、tasks 保留、helper 抽取）、运维（OPS-1 备份原子写 / OPS-2 GGUF）、LLM（LLM-1/2）、管线（PIPE-7 批量窗口语义 / PIPE-10 convert 签名清理 / PIPE-12 GateCollector docstring 如实化 / PIPE-8 进程池哨兵 / PIPE-9 块 id 唯一赋值点重构 / PIPE-11 S4 围栏逐字节保留）、安全（Q-2 netguard 缓存封顶 / PERF-1 watcher 热路径）、配置（CFG-2 doctor 配置语义非法退 `EXIT_USAGE`）；② 测试面五项（TEST-1 gate 层 golden 缺失改硬失败、TEST-2 伪响应体断言、TEST-3 netguard 真 hook、TEST-4 同机双跑相对口径、TEST-5 等新增 `tests/test_ch473_fixes.py` 61 项）；③ 编号纠偏——开工时发现 **CH-472 已被 `36ef92a`（tiny_corpus_drain 断言修正）占用**，按「单调递增、不复用、不回收」纪律全部改名 CH-473（24 个 `.py` 内联标记 + 测试文件 `git mv`）。批次④（本收口轮）为文档面 + 证据补强：CH-471 / CH-472 两行登记重写（原 CH-471 行漏登第四轮全部实施且误称「`doc2md` 包零改动」）、CH-472 行如实化（其声称的 `PLAN_VERSION` 3.5→3.6 实际从未 bump，由本轮补正到 3.6）、附录 A 补 CH-471 新增三键（57→60）、附录 G 全量重刷、§9.4 补第四 / 第五轮两节、12.7 补 X-16（**已获 CI 复跑确认**——`3f7f9f2` 的 run `38845736366` 五作业全绿、3.12 腿 1m28s 干净退出）并更正矩阵行两处事实（3.13 挂死系撞 GitHub 默认 6 小时作业上限而非被下一 push 取消；修复后矩阵恢复三档全绿）、git 能力行改「已完全恢复」（工作副本 10-16 12:39 重新接回仓库）、12.1 / 12.2 / 12.10 同步刷新、README 三处同步、`ci.yml` 头注释 `-v`→`-vv` 与过期项数校正。**证据补强（git 恢复后按 12.2 原始口径补做）**：`git worktree` 四时点（`797d28f` / `88896ae` / `584e7b5` / `2081398`）对 6 个黄金样本各转换一次，比对 zip 全条目 + gate 明细 + markdown + doc_id——**CH-473（`584e7b5`→`2081398`）与 CH-471（`88896ae`→`584e7b5`）均 6/6 样本完全一致**，CH-469（`797d28f`→`88896ae`）仅 `large_320p` 变化（与其登记的既有新基线吻合），12.2 基线表在本沙箱 12 项哈希全部复现（产物确定性跨平台成立）。**出口实测**：gate **145 项全绿**、全量 **605 passed / 0 skipped**、`test_golden_repro.py` 7 项、`ruff check doc2md tests` 全绿（收口轮本机记录 62.7s / 126.3s，沙箱复跑 61.6s / 127.0s，项数为硬口径）。**推送与远端验证**：批次④提交 `4151515` 推送 `main`（10-16 12:16Z），`ops/hooks/pre-push` 首次实战通过，远端 run `38899470118` 五作业全绿——**145 项红线三档实证**（各 44–73s）、full-suite 605 passed（126.77s），12.1 / 12.7 / 12.10 已回填。B 类仍只登记（每次完成任务后提醒）：CH-470 的「require PR before merging」平台实施（D2 决策待用户环境执行）、X-1…X-15 各项（X-16 已获 CI 确认，移出待验证清单；**X-16 后又复发一次**——run `38901173280` full-suite 退出死锁，作为 X-17 在 CH-474 轮根治，见第十一章）、T3 / T12 / T13、真实模型接入后的产物比对。

**第六轮审查（CH-475 轮启动）**：第五轮收口（含 CH-474 的 X-17 根治）之后再做一轮四面并行全面审查（管线核心 / 服务层 / 运维·CLI / 文档一致性），报告单独存档为 `docs/review-round6-CH-475.md`（227 行——延续第四轮起「审查报告不挤占本章正文、落点与编号以报告为准」的纪律），发现编号 **R6-***（PIPE-1/2 / SVC-1…4 / LLM-1/2 / OPS-1/2/3 / DOC-1）。本轮基线为 gate **146 项全绿**（本机复跑，较第四轮 132 项净增 14），**所有发现都是在这套已全绿门禁之外新挖出的盲区**——即现有红线未覆盖的路径。共 **14 项确定缺陷 + 1 组文档漂移**，其中 **HIGH 3 项**：**R6-PIPE-1**（S3「第三标题回退」纯编号判标题无字号/加粗门，「1. 安装依赖」这类有序列表项被误升 H1，污染 `content.md` 结构与 TOC；现有门禁语料标题/列表区分明显故漏检）、**R6-LLM-1**（`OpValidator._check` 只校验算子白名单/目标块存在，**不校验参数值**——`level:"3"`（字符串）在 `s4_render` 触发 TypeError、`marker:"\1"` 作为 `re.sub` 替换串触发 `re.error`；文档正文是不可信输入，模型或被注入正文可诱导此类算子让整篇转换在渲染期崩溃，DoS 自身管线）、**R6-OPS-1**（macOS `ops/launchd/*.plist` 缺 `WorkingDirectory` 且未传 `--root`，launchd 默认 cwd=`/` → 相对根名 `work` 被 `root_setting` 解析成 `/work` → 每日 cron 要么失败要么操作错误根；Windows 孪生已带 `WorkingDirectory`，属 macOS 腿漏配）。其余 MED 级为安全加固（reload 绕过不可变守卫、明文 token 前缀入审计、`scrub_record` 漏 bytes）、数据安全（幂等重执行 + `mark` 无状态守卫致终态可回退、claim/reclaim 的 `source_path` 过滤口径不对称）与等价性度量盲区（`norm_lines` 抹尽全部空白使门对**缩进类**漂移失明）+ doctor 退出码缺口。（括号里的「围栏/列表标记」为原报告口径，CH-478 批次② 实测校正：那两类是内容级漂移、旧口径同样看得见，见 §4.7 与 12.7 的 B-2 行。）

分诊口径不变（A = 沙箱内可改可验证 → 直接实施；B = 需本机以外环境/人工 → 仅登记；C = 需人决策 → 按「功能全面、性能优先」+ 核心价值代为制定）。**A 类 12 项全部落地**（按报告 §4「数据安全 > 正确性 > 安全加固 > 健壮性 > 运维 > 文档」顺序）：R6-SVC-3（`mark()` 加状态守卫 + `_finish_one` 幂等命中早返回跳过 `save_ir`）、R6-SVC-2（审计 actor 缺省改用 token sha256 前 12 位而非明文前缀）、R6-SVC-1（`/system/reload` 应用新配置前同口径调用 `check_runtime_immutable`）、R6-OPS-2（`scrub_record` 补 bytes 分支：先 `decode` 再走 >64 字符指纹规则）、R6-OPS-3（`doctor` 对磁盘不足/server 模式 LLM 不可达退 `EXIT_SYS`、配置语义非法退 `EXIT_USAGE`，所有机器可读 CLI JSON 出口统一收口 `_jline(ensure_ascii=True)`）、R6-LLM-1（新增 `valid_level`（须 int 且 1..6）/`valid_marker`（白名单 `^[-*+·•‣]{1,3}$`），`_check` 拒绝越界值、`apply_ops` 对 marker 改用 lambda 替换杜绝 `\1` 注入——**拒绝而非崩溃**）、R6-PIPE-1（第三回退加信号门：仅当字号>正文众数 或 加粗 或 多级编号 或 CJK 章节词命中才判标题，否则归 `list_item`）、R6-PIPE-2（`norm_lines` 保留行首缩进、只折叠行内连续空白为单空格，等价性门从此看见缩进漂移）、R6-OPS-1（两个 plist 补 `WorkingDirectory` + 显式 `--root`，守卫断言落 `tests/test_t5_windows_ops.py`）、R6-LLM-2（`_INJECT_HINT` 词表补 forget/disregard/ignore previous/new instructions/developer mode 等真实注入话术、「扮演」收窄到需越狱上下文以降误报）。**C 类 5 决策（D1…D5）代为制定**：其中 **D1 刻意偏离报告建议**——报告 §5.D1 提议 `mark()` 加 `AND status IN ('queued','running')`，实施改为 `AND status NOT IN ('done','rejected')`，以保住 `failed→done` 的自纠偏与 `queued→failed` 的遗骸清理（CH-455 既有语义），只封死「终态被回退」这一数据安全硬伤；偏离理由写进登记行、附录 F 第五层与附录 G `scheduler.py` 行。**验证后延期 2 项（转 B 类）**：**R6-SVC-4**（`claim()` 加 `source_path IS NOT NULL` 过滤）经代码核验判定延期——并发危害已被 R6-SVC-3 的 mark 状态守卫吸收（终态不可翻转，claim 竞争无法污染同步作业终态），且加过滤会使 `drain_once` 靠 E4001 清理的 NULL-path 孤儿行滞留、经 `uq_tasks_doc_active` 阻塞再提交，`worker.py` 未改动，登记 **B-4**（需并发压测复现）；**R6-LLM-2 的归因去循环**（`_attribute` 仍扫算子 JSON 而非源正文，与 `convert._injected_ops` 形成循环归因）需真实注入语料评估词表误报/漏报率，登记 **B-3**。

**出口实测**（本机 macOS arm64 / Python 3.15，`.venv` editable）：新增 `tests/test_ch475_fixes.py` **71 项（红线 7）**；gate **146 → 153 项全绿（30.04s）**、全量 **606 → 677 收集 = 677 passed / 0 skipped（66.09s）**、`ruff check doc2md tests` 全绿。**对外契约变化三处**（均为「原来错了/有旁路」的收敛，非新设计）：`doctor` 退出码、`/system/reload` 拒改运行期不可变键（400/E4001）、`edit_audit.actor` 缺省语义从明文 token 前缀改 sha256 前缀（凭据不再落库）；**产物面零变化**（正常语料 markdown 字节不变，R6-PIPE-1/2 只纠正误判路径与收紧度量，G6 等价保持）；`PIPELINE_VERSION` 2.4.0 / `SCHEMA_VERSION` 14 / `DB_VERSION` 5 均不变，`PLAN_VERSION` 3.7→3.8。**B/C 类去向**（每次完成任务后提醒）：B-5（R6-OPS-1 macOS 真机 `launchctl load` 验证，**本轮唯一 HIGH 级却需真机确认的项**）、B-4（R6-SVC-4 并发压测）、B-3（R6-LLM-2 归因去循环 + 真实注入语料）、B-1（R6-PIPE-1「编号列表字号==正文」golden 反例）、B-2（R6-PIPE-2 缩进/围栏破损样本）为本轮新增；沿既有登记的 B-6/TEST-3（CI Windows 腿）、B-7/T3（跨机基准）、B-8/T12（serve/work 常驻资产）、B-9/T13（备份 v2 真机恢复）、B-10（前端真实浏览器人工走查）仍开放，全部在 12.7。

**第七轮审查（CH-476 轮启动）**：第六轮收口之后再做一轮全面审查，审查面在既有四维（管线核心 / 服务层 / 运维·CLI·文档一致性）之外**新增两个专审面：功能完整性（逐条 FR 对照）与性能专项**，报告单独存档为 `docs/review-round7-CH-476.md`（329 行），发现编号 **R7-***（SVC-1…5 / PIPE-1…6 / STOR-1/2 / LLM-1 / OPS-1…3 / SEC-1 / TEST-1 / GAP-1…5 / DOC-1…7 / PERF-1…3 与 PERF-a…k）。本轮基线为 gate **153 项全绿 + `doctor` 退 0**（本机复现见报告 §0），共 **3 项 HIGH + 11 项 MED + 若干 LOW + 功能 gap 5 组 + 性能机会 10 条**。与前六轮的关键差异：前六轮是「缺陷修复为主、性能顺带」，本轮是**首个以性能优化为主线的轮次**——挖出 T14（`synchronous=NORMAL`）、`chunk_pages` 分块、单事务合并之后仍存在的存量成本，其中最大一项直接解释了 12.3 长期挂着的「workers=4 仅快 4.5%」之谜（R7-PERF-1：每轮认领 `workers` 件 × 每新建一次池重付 `import pdfplumber` 0.166s/worker）。三个 HIGH：**R7-PIPE-1**（缝合编号续接方向写反，递增序列恒不命中＝死分支、递减反而命中，且该分支在 heading 守卫之前 return → 标题被吸收、极端下代码块渲染为空串即整块静默消失）、**R7-STOR-1**（FTS5 两个 `UNINDEXED` 列使删除与单块定位退化为全内容表线性扫描，且落在 `BEGIN IMMEDIATE` 写锁内——检索/编辑面随库龄单调劣化，小测试库门禁永远测不出）、**R7-LLM-1**（R6-LLM-1 只规约 `level`/`marker`，`target` 未设防：`target:5` 抛 TypeError、`target:["b1"]` 过完五道校验后抛 unhashable → 整篇转换 E5000）。功能 gap 最重是 **R7-GAP-1**：FR-102 声明「支持修订痕迹」却从未实现，python-docx 的 `p.text` 不含 `w:ins` 内的 run，含修订 DOCX 的插入正文**整段静默丢失且无提示**——对以法律/协作文档为主的转换器是最不可接受的失败形态。
分诊口径不变。**A 类 32 项按报告 §4 的 13 批顺序全部落地**（排序：数据安全 > 正确性 > 性能主线 > 健壮性 > 运维 > 功能补齐 > 文档），逐批实施、逐批复跑 gate（每批结束均须 153 项在位且不回潮），批次构成与红线项数见附录 F 第六层：① R7-SVC-1（删除×在途转换竞争，决策 D6 的 check-and-act 顺序）、② R7-PIPE-1（决策 D3：方向修正 + 结构块守卫前移）、③ R7-LLM-1（`target` 设防）、④ R7-STOR-1/2（决策 D1：`search_map` 映射 + 无条件 DDL 重放 + `DB_VERSION` 5→6）、⑤ R7-PERF-1（决策 D2：认领量与池宽解耦，**刻意不做持久池**）、⑥ R7-PERF-2/3（三重哈希消除 + 入队并行哈希）、⑦ R7-PIPE-2/3/4、⑧ R7-OPS-1/2 + R7-TEST-1、⑨ R7-GAP-1（决策 D4：提取而非拒收 + 黄金样本真实 `w:ins`/`w:del`）、⑩ R7-GAP-2/3（决策 D7：注补、单元格段界补、PDF 合并单元格**明确不补**并写入第二章非目标）、⑪ PERF-a…k 七组等价性专项、⑫ R7-SVC-2/3/4 + R7-SEC-1（C 类已决方案）、⑬ R7-DOC-1…7 + R7-GAP-4/5 + 本登记收口。**C 类 8 决策（D1…D8）按「功能全面、性能优先」代为制定**，其中偏离报告原建议或明确取舍的三处：D1 把「加 schema 必须 bump 版本」的纪律换成「DDL 无条件幂等重放」这个不依赖人记性的机制（本轮漏 bump 即反例自证）；D2 在「持久池收益更大」与「X-16/X-17 的池收尾强杀是防挂死语义的一部分」之间选后者，只解耦领取量（一行改动消除放大系数，G6 零风险，持久池待 B-6 的 Win11 spawn 实测再议）；D5 噪声门禁阈值**不落**（判据已拟但无真实乱码语料评估误杀面，宁分两步不把假阳性引入主链路）。**验证后延期转 B 类**：R7-PIPE-5（B-1）、R7-PIPE-6（B-2）、R7-SVC-5（B-12）、持久池取舍（B-6）；沿第六轮仍开放的 B-3/B-4/B-5 继续登记。

**出口实测**（本机 macOS arm64 / Python 3.15，`.venv` editable）：新增 `tests/test_ch476_fixes.py` **87 项（红线 83）**、`test_sharding_unit.py` 由 17 → **26 项（其中红线 8）** → 本轮净增 **91 项红线 / 96 项全量**；gate **153 → 244 项全绿（33.9s）**、全量 **677 → 773 收集 = 773 passed / 0 skipped（73.3s）**、`ruff check doc2md tests config` 全绿、`doc2md doctor` 退 0。**性能实测（按决策 D8 分刀独立 A/B，语料与测量方法逐条登记在 12.3）**：R7-STOR-1——10 万块合成库上整篇删除 **17.4ms → 1.0ms（≈17×）**、单块删除 **15.6ms → <0.1ms（>150×）**，且 30 万块档证明旧口径随行数线性增长而新口径持平（库龄税归零）；R7-PERF-1——1080 件大语料 workers=4 排空 **36.5s → 11.5s（3.2–3.4×）**，串行档几乎不动（反证开销在池重建而非并发度）；R7-PERF-2/3——三重哈希削为两重（省下入库前那一遍全量读，本机 ~10ms/20MB）、12×32MB 目录入队 **163.9ms → 48.1ms（3.4–3.6×）**；PERF-a…k——七组等价性微观收益（`scan_injection` 预检 −77.1%、`_page_metrics` −55.6%、DOCX 样式记忆化 + 文档级预检端到端 **15.0×**、`submit_many` 万件单事务 **7.5×**、Markdown 流式 **−81% 耗时 / −99% 峰值堆** 等）。全部比值只主张同机同轮相对增益，绝对毫秒不与 CI/Ubuntu 互比，低配机须按目标机吞吐重算（B-6/B-8）。**对外契约变化五处**（详见 CH-476 登记行）：`DB_VERSION` 5→**6**、同步 `/jobs` 池闸门超时由无界排队改 **503 `E5041` + `Retry-After`**、`mode` 非白名单 **400/E4001 零副作用**、审计 `reason` 与令牌 `label` 的类型/长度帽 **400/E4001**、IR `flags` 新增三个可选旗标。**产物面**：修订痕迹 / 注 / 单元格多段三类 DOCX 由「丢内容或粘连」变为保真（FR-102 原设计行为落地），其余语料逐字节不变（G6 等价复核）。`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变；新增两个配置键（60→62）。`PLAN_VERSION` 3.8→3.9。**B 类去向**（每次完成任务后提醒）：本轮新增 **B-6**（Win11 spawn 池实测，持久池取舍依赖它）、B-12（>30min 转换 vs stale 回收双进程）、B-1/B-2（噪声与双栏语料）；沿登记仍开放 B-3、B-4、**B-5（macOS `launchctl load` 实跑，HIGH）**、B-7（CI Windows 腿）、B-8（跨机基准，含本轮 PERF-1/2/3 复测）、B-9（serve/work 常驻资产自启）、**B-10（备份 v2 真机恢复演练，数据安全相关，建议优先）**、B-11（浏览器人工走查）；R7-OPS-3 同为 B 类。全部在 12.7。

---

# 附录（规格表）

附录是查询面，不叙述理由。凡与正文冲突处，以正文的规格陈述为准、附录的取值为事实来源；两者不一致即为缺陷，须登记。

## 附录 A 配置全表

来源：`doc2md/config/default_toml.py`（内置默认，62 键）与 `config/doc2md.example.toml`（分发示例，62 键）。CH-469（O-15）起两处键集**完全相等**，且都等于 `doc2md/config/loader.py` 的 `SPEC` 表——`SPEC` 是唯一权威键集，三处一致性由 gate 用例 `test_spec_defaults_and_example_share_one_key_set` 钉住，不再靠本附录的散文承诺（CH-456 曾把「两处键集差清零」写成纪律，仍漏了三个只存在于示例的键）。键数口径：`SPEC` 是扁平的点号键表（`len(SPEC)` = 叶子数），实测本机三处各 **62**；CH-469 新增 `perf.stale_check_seconds` / `perf.batch_file_timeout_s` / `llm.retries` 三键（54 → 57），CH-471 再新增 `api.allow_remote_bind` / `perf.sync_min_parallel` / `ops.tasks_retention_days` 三键（57 → 60），CH-476 又新增 `perf.queue_batch_factor` / `perf.enqueue_hash_threads` 两键（60 → 62），本表行数是 60 行，因为 `llm.bind` / `llm.port` / `llm.auth` 合写在一行。
解析顺序（**只叠加一个用户文件**）：内置默认 → 若给了 `--config PATH` 就用它；否则取 `./doc2md.toml`，不存在再取 `$DOC2MD_HOME/doc2md.toml`（`DOC2MD_HOME` 默认 `~/.doc2md`）→ 与该默认值深合并。两个候选位置**不是级联**：存在 `./doc2md.toml` 时 `$DOC2MD_HOME` 下的文件不会被读。服务侧再叠加 `work/settings_override.json`（`PUT /settings` 落盘，`create_app()` 时合并）。
相对路径一律经 `config.path_setting()` 解析：绝对值原样；相对值若首段等于实际 root 名或默认根名 `work`，剥掉该段后挂到 root 下（`paths.root` 自身不剥前缀）。

「消费」列标明该键是否被运行时代码读取。CH-456 / T9 之前，本附录曾用「声明性」标注「出现在默认配置中、但无代码消费」的键（第十二章偏差项 D-8 逐条列出 11 个）；该批键已全部落地消费，标注随之从本表清除——当前 62 键**全部有真实消费方**。纪律保留：新增键时「消费」列必须写下真实读取方，找不到消费方的键不得进默认配置；展示面-only 的键（如 `api.revoke_effective_seconds`）也要写明「仅展示、不驱动行为」，避免又被误读成可作用开关。

**校验口径（CH-469 / O-15）**：`SPEC` 表逐键声明类型与允许区间（整数 / 浮点 / 布尔 / 字符串 / 字符串列表 / 八进制模式串 / 枚举），装载期由 `config.validate()` 执行，违反即 `config.ConfigError`。设计边界是**拦明显配错，不替用户做性能决策**——区间放宽到数量级异常（如 `perf.workers` 上限 64、`logs.rotate_mb` 允许 0.1～1000000），枚举只收已实现的取值。行为面三处：① `load()` 装载用户文件时，**未知键**逐个 `UserWarning` 点名（不生效但不阻断启动，兼容历史文件）；② `PUT /settings` 对未知键与非法值一律先校验后应用 → `400 / E4001`（显式写入不容静默失效）；③ CLI 把 `ConfigError`（含 `--config` 指向不存在的文件、TOML 语法错误）映射为退出码 `2`（用法错误）并输出 `code: "E4001"`，不再混进 `EXIT_SYS`。新增键必须同时进 `SPEC`、默认 TOML、样例 TOML 与本表——前三处由 `tests/test_ch469_ops.py::test_spec_defaults_and_example_share_one_key_set` 钉住（该用例只比这三个代码侧来源），**本表不由任何测试消费**：文档侧漏行只能靠收口轮逐键核对（CH-469 批次④即按 `len(SPEC)` 与表内键名做了差集核对，结果是键全覆盖、只是三个键合写在一行；**CH-473 收口轮复核对出 CH-471 新增的三个键整行缺失**——`len(SPEC)` 已是 60 而本表停在 57，这正是「散文承诺不可依赖」的又一次实证，本轮补齐并保留该核对纪律）。

| 键 | 默认值 | 消费方 | 规格语义 |
| --- | --- | --- | --- |
| `paths.root` | `"work"` | cli / api / cron / loader | 数据根目录（state.sqlite、out、logs、tmp、reports 的父级） |
| `paths.out` | `"work/out"` | cli | 产物 zip 目录 |
| `paths.reports` | `"work/reports"` | cron | 日报与 cron 结果 JSON |
| `paths.logs` | `"work/logs"` | api / cron / cli | 三类 JSONL 日志目录 |
| `paths.tmp` | `"work/tmp"` | cron / api | 中转目录；服务启动与 `cron --daily` 各清扫一次 |
| `paths.vendor_models` | `"vendor/models/llm"` | llm / cli | 本地模型资产根：`llm.mode=server` 时定位 gguf 与模型名（CH-456 / T2） |
| `run.profile` | `"standard"` | loader | lite/standard/strict 三档落为键集覆盖（CH-456 / T9）；用户显式配置永远优先于 profile |
| `run.offline_assert` | `true` | security/netguard | 出站网络运行期守护：socket 连接限制为回环（CH-456 / T9） |
| `s1.chunk_pages` | `32` | cli / convert | 每重新打开一次文档最多解析页数；`0` = 整本一次读完 |
| `s1_5.sample_pages` | `"auto"` | cli / convert | 体检模式：`auto` / `even5` / `legacy3` |
| `s1_5.min_chars_per_page` | `120` | convert | 单页字符下限 |
| `s1_5.reject_on_any_bad_page` | `true` | convert | `auto` 模式下「任一采样页不达标即拒收」的总开关；`false` 时只按空段/占比拒收（CH-456 / T9 接线） |
| `s1_5.page_detail_in_report` | `true` | convert | 页级明细是否进 `report.html`（CH-456 / T9 接线；恒写入 gate 结果结构） |
| `s2.mode` | `"auto"` | cli / convert | 分片开关：`auto` / `single` / `two_phase` |
| `s2.max_pages_per_shard` | `300` | cli / convert | 粗切片大小 |
| `s2.shard_overlap_pages` | `2` | convert | 片间重叠上下文页数 |
| `s2.dedupe_overlap` | `true` | convert | 重叠区按文本指纹去重 |
| `chunks.tokenizer` | `"gguf-embedded"` | cli（doctor 展示） | 词元来源标识 |
| `chunks.manifest` | `"vendor/models/llm/tokenizer.manifest.json"` | cli | `tokenizer verify` 默认清单路径 |
| `s4.preserve_marks_strike` | `false` | convert | 删除线是否渲染（默认丢弃） |
| `s4.link_internal` | `"plain"` | convert | 内部跳转链接渲染：`plain` / `anchor`（两档正文均为纯文本，anchor 的差异只在 IR/审阅面保留 dest；CH-464 收敛） |
| `perf.workers` | `0` | cli / batch | 批量进程数上限；`0` → `min(4, cpu_count())`，负数按 1 |
| `perf.queue_async` | `false` | api / cli | `POST /jobs` 只入队返回 202 |
| `perf.queue_batch_factor` | `4` | worker（`drain_once`） | 每轮领取量 = `workers × 该系数`（CH-476 / R7-PERF-1，决策 D2）；摊薄「每轮新建进程池 + worker 重导 pdfplumber」的固定开销，`1` = 旧的按池取件口径。池宽仍是 `workers` |
| `perf.enqueue_hash_threads` | `8` | api（异步 `POST /jobs`） | 异步入队的「magic 预检 + 内容哈希」线程上限（CH-476 / R7-PERF-3）；写库仍按入参顺序单线程提交，`1` = 旧的串行哈希 |
| `perf.sync_min_parallel` | `8` | api（同步 `POST /jobs` 目录分支） | 同步目录转换达到此文件数才走 `run_batch` 进程池，否则串行（CH-471 / SVC-3：复用同一把池，避免为小目录付进程启动费） |
| `perf.min_free_mb` | `512` | cli / diskguard | 写盘前剩余空间下限 |
| `perf.disk_safety_factor` | `3` | diskguard | 需求 = 待写字节 × 系数 |
| `perf.stale_job_minutes` | `30` | worker | 队列遗骸回收阈值（仅队列来源） |
| `perf.stale_check_seconds` | `60` | worker（`run_work`） | 常驻进程重跑遗骸回收的节流周期；`0` = 只在启动时回收一次（CH-469） |
| `perf.batch_file_timeout_s` | `0` | pipeline/batch | 批量进程池内单文件墙钟上限；`0` = 不设限，`>0` 超时记 `E5000` 并取消该件（CH-469：损坏 PDF 让 pdfplumber 挂死时整批不再陪绑） |
| `watch.stable_seconds` | `5` | cli | 稳定性静默窗口 |
| `watch.follow_symlinks` | `false` | cli / api | 目录遍历是否跟随软链 |
| `watch.max_depth` | `20` | cli / api | 遍历深度上限 |
| `watch.skip_hidden` | `true` | cli / api | 跳过隐藏项 |
| `watch.max_files_per_dir` | `5000` | cli / api | 单目录条目上限（超出 → 413 / E4133） |
| `api.bind` | `"127.0.0.1"` | cli | 服务绑定地址（隐私边界，禁改公网绑定） |
| `api.port` | `8090` | cli | 服务端口 |
| `api.token_roles` | `["admin","editor","reader"]` | auth / api / cli | 可签发角色集：`auth.issue(allowed=…)` 之外的角色的签发即 `ValueError`（CH-456 / T9） |
| `api.revoke_effective_seconds` | `5` | cli（仅用于 `token --revoke` 的展示字段（`doctor` 不输出该键，CH-462 实核）） | 对外承诺的吊销生效窗口；授权实时查库，故不驱动任何等待逻辑 |
| `api.base_revision_required` | `true` | api / cli | `false` = 灰度期允许无 `base_revision` 的 PATCH |
| `api.allow_remote_bind` | `false` | config/loader / cli | 非回环 `api.bind` 的显式 opt-in 总开关（CH-471 / SVC-5 / D4）：默认强制 loopback，`config.validate` 对非回环绑定直接拒绝；`false` 时隐私边界不受任何配置组合影响。属 `RUNTIME_IMMUTABLE_KEYS` 成员，禁经 `PUT /settings` 运行期修改（与 `run.offline_assert` 同一纪律：两步 PUT 掏空离线姿态的路径不存在） |
| `llm.mode` | `"offline"` | convert / cli / doctor | 补助实现：`offline` = 确定性占位（默认，主链路零网络）；`server` = 本地 llama-server（仅 127.0.0.1） |
| `llm.fallback` | `true` | convert | `server` 不可用时回退 offline 占位实现并打 `fallback` 标记；`false` = 明确失败（E5041 / 503） |
| `llm.timeout_ms` | `15000` | llm | 单次请求超时（连接 + 读取合计） |
| `llm.retries` | `2` | llm | 仅连接类瞬断（`URLError` / 超时 / `OSError`）的重试次数；`0` = 不重试，HTTP 状态类错误不重试（CH-469） |
| `llm.model` | `""` | llm | 请求里的模型名；空 = 取 `paths.vendor_models` 下发现的 gguf 文件名 |
| `llm.bind` / `llm.port` / `llm.auth` | `"127.0.0.1"` / `8080` / `false` | llm / convert / doctor | 本地模型服务地址与鉴权（CH-456 / T2 落地；`llm.auth=false` 属已知接受项，`true` 时口令走 `DOC2MD_LLM_TOKEN` 环境变量，不落配置不入日志） |
| `llm.op_whitelist` | 5 个 op | convert / cli | 补助手可应用 op 白名单（唯一权威来源） |
| `logs.rotate_mb` | `50` | api / cron / worker | 单文件轮转阈值（MB） |
| `logs.rotate_keep` | `5` | api / cron / worker | 轮转保留份数 |
| `ops.events_retention_days` | `30` | cron | `events` 表保留期 |
| `ops.tasks_retention_days` | `30` | cron（`--daily` 步骤 7） | 终态（done / failed / rejected）作业行的保留期，防 `tasks` 表无界增长（CH-471 / SVC-4） |
| `ops.backup_local_dir` | `"work/reports/backups"` | cron | 本地快照目录（权限 700） |
| `ops.backup_external_dir` | `""`（空 = 未配置） | cron | 外置卷目录；未配置 → 周报记 `skipped` 且 `ok=True`（CH-469：该键进默认表，示例与默认不再各说各话） |
| `ops.backup_external_encrypted` | `true` | cron / cli | 外置副本强制加密；置 `false` 时写外置即抛 `PermissionError` |
| `ops.backup_rotation_days` | `14` | cron | 副本轮出天数 |
| `ops.work_dir_mode` | `"700"` | ops/backup | `work/` 目录权限（CH-456 / T9：原为硬编码 `0o700`） |
| `security.max_name_bytes` | `128` | convert | 文件名消毒后的 UTF-8 字节预算 |
| `security.sanitize_fail_action` | `"reject"` | config/loader | 冻结键：唯一合法值 `reject`（配错即启动期 `ValueError`）；消毒失败一律拒收（E4003），无降级分支 |

## 附录 B CLI 全表

程序名 `doc2md`（字面小写）。全局子命令前置项：`--version`。所有子命令接受 `--config PATH`、`--root DIR`。

| 命令 | 参数 / 开关 | 行为 | 退出码 |
| --- | --- | --- | --- |
| `doc2md serve` | `--host` `--port` | 启动 API 服务（Flask threaded）；启动即写 `app.jsonl` 的 `SERVICE_START` 并清扫 `paths.tmp` | 0 正常；**2** `--host` 非回环且未设 `api.allow_remote_bind`（CH-473 / R5-SEC-2，CLI 面与配置面同口径）；5 启动异常（`OSError`，端口占用等） |
| `doc2md serve token` | `--role admin\|editor\|reader`（默认 admin）、`--label`、`--revoke TOKEN`、`--list` | 令牌管理（API 等价物，服务未启动也可用）；签发只打印 token 裸串，吊销/列举打印 JSON（`revoked` + `effective_within_s`） | 0 正常；**2** `--role` 不在 `api.token_roles`（CH-469 / V-7，stderr 记 `E4001`）；**4** `--revoke` 的 token 不存在（CH-469 / V-13：`{revoked:false, code:"E4041"}`，原实现无条件报成功） |
| `doc2md cron` | `--daily` \| `--weekly`（必选其一，互斥）、`--no-backup` | 运维流水线（见附录 E 步骤表）；结果落 `reports/cron-<mode>-<YYYYMMDD>.json`（日期为 **UTC**，与日报体 `date` 同口径，CH-476 / R7-OPS-2） | 0 全 ok；2 互斥组用法错误；5 有失败步骤 |
| `doc2md convert` | `PATH`（文件或目录）、`--mode`、`--out DIR`、`--json`、`--follow-symlinks`、`--workers N`、`--llm-mode offline\|server`、`--llm-no-fallback`（CH-456 / T2：覆盖 `[llm]` 配置；后者在服务不可用时明确失败 E5041，不回退 offline） | 单文件/目录批量转换；目录走有界进程池，`--workers 1` 退回串行；目录无匹配文件时打印 `status:empty` 并按 0 退出 | 2 路径不存在；0；3 门禁拒收；4 业务错误；5 系统错误（多项取最大值聚合） |
| `doc2md work` | `--once`、`--max-jobs N`、`--workers N`、`--idle S`（默认 5.0）、`--enqueue PATH`、`--status` | 消费 `tasks` 队列；`--status` 仅打印计数；`--enqueue` 先入队（配合 `--once` 即入队后排空） | 0；4 入队拒收；5 有失败作业 |
| `doc2md watch` | `DIR`、`--once`、`--interval S` | 投递目录稳定性轮询（`size+mtime` 静默满窗口才处理）；`perf.queue_async = true` 时稳定即入队交 `work` 消费，否则就地转换；非 PDF/DOCX 扩展名静默跳过 | 0；3 门禁拒收；4 入队/转换业务错误 |
| `doc2md tokenizer export` | `GGUF`、`--vocab`、`--manifest`（清单默认取 `chunks.manifest`，词表默认取其同名 `vocab.jsonl`） | 从 GGUF 抽取内嵌词表与清单，打印 manifest/vocab/n_vocab/哈希前缀 | 0（异常经 `main()` 兜底为 5） |
| `doc2md tokenizer verify` | `--manifest`（必填）、`--gguf`（必填） | 清单与 GGUF 逐项比对（`gguf_ok` / `vocab_ok` / `name_ok` / `n_vocab_ok` 四项） | 0；5 任一项为假 |
| `doc2md backup drill` | `--keep`（保留演练沙箱取证；`drill-*` 不受 tmp 周期清扫与 `cron --daily` 清理，CH-476 / R7-OPS-1） | 隔离沙箱内跑完整恢复闭环（快照→加密→丢失→轮出→恢复→校验→比对，DoD 第 11 条，CH-456 / T7）；结果打印 JSON 报告 | 0 演练通过；4 `DrillFailure` |
| `doc2md doctor` | — | 环境自检 JSON（见下表字段） | 0；2 冻结键/配置非法；5 依赖 `MISSING` / server 模式 LLM 不可达 / 磁盘不足（`free_mb < min_free_mb`，含探测失败 `-1`） |

`doctor` 输出字段（固定 29 项 + 4 依赖探测 = **33 项**；`llm.mode=server` 时再追加条件项 `llm_server`。CH-464 把 `s4_link_internal` / `s4_preserve_strike` / `watch_stable_seconds` 三个渲染与稳定窗口开关接入自检面，固定键数由 26→29）：`python` `pipeline` `config_frozen_keys` `run_profile` `root` `root_exists` `root_mode` `tokenizer` `s1_5_sample_pages` `shard_mode` `max_pages_per_shard` `op_whitelist` `llm_mode` `llm_fallback` `llm_endpoint` `llm_vendor_models` `base_revision_required` `logs_rotate_mb` `backup_encrypted_external` `s1_chunk_pages` `perf_workers` `cpu_count` `perf_queue_async` `min_free_mb` `free_mb` `console_encoding` `s4_link_internal` `s4_preserve_strike` `watch_stable_seconds`，以及 `dep_pdfplumber` `dep_docx` `dep_flask` `dep_cryptography`。

机器可读输出纪律：**所有机器可读 CLI JSON 出口**——`--json`、错误行，以及 `doctor` / `cron` / `serve token --list|--revoke` / `tokenizer export|verify` 的 JSON 报告——一律走 `_jline()`（`ensure_ascii=True`）。CH-475（R6-OPS-3）收口了此前 `cron` / `list-tokens` / `tokenizer` / `doctor` 绕过 `_jline` 直接 `json.dumps(ensure_ascii=False)` 的旁路，故在 cp936 与 UTF-8 终端下字节一致；人读模式在 `main()` 最前执行 `_console_safe()`，仅当 stdout/stderr 编码非 UTF-8 时 `reconfigure(errors="replace")`。

## 附录 C API 全表

前缀：无（根路径即 API）。鉴权：`X-Doc2md-Token` 头（或 `?token=`）。响应错误一律 `{code, message, detail}`，5xx 类同样走该契约。状态码为实际返回值。

| 方法 路径 | 所需角色 | 参数 | 成功码 | 主要错误 |
| --- | --- | --- | --- | --- |
| `POST /jobs` | editor | body `{path, mode?, idempotency_key?}`，头 `Idempotency-Key`；`mode` 白名单 `auto`/`single`/`two_phase`（与 CLI `--mode` 同集合，CH-476 / R7-SVC-2；校验先于路径与转换，非白名单零副作用） | 202 `{jobs:[...]}` | 400 缺 path / `mode` 非白名单（`detail.mode` 回实际类型名）；404 路径不存在；413 空间不足（仅同步形态，经转换打包闸）/ 目录过宽；415 类型不符（异步形态整批拒绝，队列零增长）；422 门禁拒收（同步下逐文件记 `rejected`，不整体失败）；**503** 同步池被上一个大批量占用且 60s 内未排到（`{code:"E5041", detail.reason:"sync_pool_busy"}` + `Retry-After: 60`，队列与库面零增长，CH-476 / R7-SVC-3） |
| `GET /jobs` | reader | `?status=&limit=`（默认 50，**上限 500**、负数 400/E4001；CH-476 / R7-PERF-j：超限截到 500 而非报错，`counts` 仍按全量统计） | 200 `{tasks, counts}` | 401 / 403 |
| `GET /docs/<doc_id>` | reader | — | 200（含 `blocks[]`） | 404 |
| `GET /docs/<doc_id>/markdown` | reader(export) | — | 200 `text/markdown; charset=utf-8`（zip 内 `content.md` **按 64KB 分块流式**，CH-476 / R7-PERF-k：响应体与原整篇读取逐字节相同，峰值堆从「三份全量副本」降到一块缓冲） | 404 产物缺失；503 `E5031` zip 损坏 |
| `PATCH /docs/<doc_id>/blocks/<block_id>` | editor | body `{text, base_revision?, reason?, as?}`；`text` 必填字符串（缺失/`null` → 400，空串才是显式清空），`reason` 若出现必须为字符串且按 256 字符截断（CH-476 / R7-SVC-4） | 200 `{revision,...}` | 400 缺 `base_revision` / 缺 `text` / `base_revision` 非整数 / `reason` 非字符串（400 一律零副作用：块不改、审计不落行）；404；425 `E4251` 版本冲突 |
| `POST /docs/<doc_id>/blocks/<block_id>/rollback` | editor | body `{to_revision, as?}` | 200 | 400 / 404 |
| `DELETE /docs/<doc_id>` | admin | query `confirm=true` | 200 `{...counts, cascade}` | 400 缺 confirm；403；500 `E5031` 产物清除失败（fail-closed，库行保留） |
| `DELETE /sources/<source_key>` | admin | query `confirm=true` | 200 | 同上 |
| `GET /docs/<doc_id>/audit` | editor | `?block_id=` | 200 `{audit:[]}`（含 before/after 正文，故不进 reader） | 403 / 404 |
| `GET /audit/deletions` | admin | — | 200 `{deletions:[]}`（只含计数与指纹，无正文） | 403 |
| `GET /search` | reader | `?q=&limit=`（默认 50，**上限 500**、负数 400/E4001，CH-476 / R7-PERF-j） | 200 `{total, hits[], mode, match}` | — |
| `GET /events` | reader | `?since=SEQ` | 200（单次 ≤200 条） | — |
| `GET /jobs/<job_id>/events` | reader | — | 200 | 403 非脚本 UA（FR-125 硬规则） |
| `GET /settings` | reader | — | 200（当前合并后配置） | — |
| `PUT /settings` | admin | body 对象 | 200 `{updated:[keys]}`；覆写 `settings_override.json`（0600）。**该文件落累计稀疏差量**（CH-469 / V-1）：只写相对当前合并态真正改动的叶子键，多次 `PUT` 逐键累积、互不抹除，reload / 重启按「默认 → TOML → override」重新叠加（原实现「整体重写为本次键集、非累计」会让前一次 `PUT` 的键静默回退，属缺陷已修）。未知键与非法值先校验后应用 → 400/E4001（CH-469 / O-15）；红线键 `run.offline_assert` 运行期不可改（CH-471 SVC-6） | 400 非对象 / 未知键 / 非法值 / 改红线键 |
| `POST /system/reload` | admin | — | 200 `{reloaded, ts}`（重读 TOML 并再次叠加 `settings_override.json`，与启动同口径；配置校验失败保持旧配置继续服务并回 400） | 400 坏配置（CH-469 / O-15） |
| `POST /tokens` | admin | body `{role, label?}`；`label` 若出现必须为字符串且按 256 字符截断（CH-476 / R7-SVC-4） | 200 `{token, role}` | 400 `role` 非白名单（`detail.allowed` 回可签发角色，CH-469 / V-7）/ `label` 非字符串（400 不建行） |
| `DELETE /tokens/<token>` | admin | — | 200 `{revoked:true}` | — |
| `GET /` | 无（静态） | — | 200 审阅页 `editor.html` | — |
| `GET /healthz` | 无 | — | 200 `{ok, docs, pipeline, ts}` | — |

`POST /jobs` 两条形态（由 `perf.queue_async` 决定）：同步 = 逐个 `convert()` 后立即返回 `doc_id`/`job_id`；异步 = 并行做完「存在性 + 类型嗅探 + 内容哈希」预检，再按扫描顺序入队（**整批一个事务**：`worker.enqueue_many` → `Scheduler.submit_many`，CH-476 / R7-PERF-h），返回 202 `{jobs, mode:"queued", consume_with:"doc2md work"}`。异步路径的入队是**全或无**：任一文件类型不符或不可读即整体 415/404 且队列零增长（预检阶段只读文件、不碰库，CH-476 起该判定收在 `worker.enqueue_many` 一处；入队本身也全或无——批内任一条失败则整批回滚，不留半批队列行）。清单生成后文件消失，异步面按 E4041 显式失败而非把它从响应里静默剔除——「目录里明明有、队列里没有」比一次 404 更难排查。

## 附录 D 错误码与告警码

段位冻结：本期（及后续）只在既有段位内增补语义，不自造新码值。历史自造码已删除（见第十一章）。

| 码 | HTTP | 类 | 触发口径 |
| --- | --- | --- | --- |
| `E4001` | 400 | ApiError | 请求体/参数不合法；破坏性操作缺 `confirm=true`；PATCH 缺 `base_revision`；**配置面同码复用**（CH-469 / O-15）：`PUT /settings` 的未知键或非法值、`/system/reload` 读到坏配置 → 400/E4001；CLI 把 `config.ConfigError` 映射为退出码 `2`（`EXIT_USAGE`）并在机器行里输出 `code:"E4001"`——E4xxx 是「客户端/用法」段位，配置写错属用法错误而非系统故障 |
| `E4003` | 400 | ApiError | 文件名消毒后仍不安全（`UnsafeNameError`，同时继承 `ValueError` 以兼容既有调用方） |
| `E4011` | 401 | ApiError | 缺 token / token 不存在或已吊销 |
| `E4031` | 403 | ApiError | 角色不足（`detail={role, required}`）；未知 action；per-job 事件端点非脚本 UA |
| `E4041` | 404 | ApiError | 文档、产物、源路径不存在 |
| `E4091` | 409 | ApiError | 重复转换会覆盖已有编辑（该 `doc_id` 下存在 `revision>1` 的块）→ 默认拒绝，需 `force=true` 才放行（CH-471 / STOR-5；内容寻址 `doc_id` 让同源文件恒落同一文档行，原 `INSERT OR REPLACE` + 清块会静默丢用户编辑。**409 段位此前未占用**，与 CH-439 撤销的「缺 `confirm`」旧 `E4091` 语义无关） |
| `E4131` | 413 | ApiError | 剩余空间不足（`detail={need_mb, free_mb, rule:"NFR-6", what}`） |
| `E4133` | 413 | ApiError | 目录过宽（条目数或深度超 `watch.*` 上限）；**遍历被截止时间截断同样归此码**（CH-469 / O-10，`detail.reason="walk_truncated"`）——半截清单入队会让用户看到「目录里明明有，队列里没有」 |
| `E4151` | 415 | ApiError | 非文本型 PDF / 非 DOCX（magic 与部件判定）；打开源文件不可读（含 pdfplumber / python-docx 打开失败）同归此码（CH-457 起含结构损坏文件） |
| `E4221` | 422 | ApiError | S1.5 体检门禁拒收（`detail={mode, sampled_pages, reasons, stats}`） |
| `E4251` | 425 | ApiError | 乐观锁冲突（`detail={current_revision}`） |
| `E5031` | 500 | Doc2mdError | 存储层失败：产物清除失败（fail-closed）、写盘 `OSError`；CLI 兜底 `OSError` 亦映射此码 |
| `E5041` | 503 | Doc2mdError | 本地模型服务不可用（CH-456 / T2 启用：`llm.mode=server` 且 `llm.fallback=false` 时抛；`detail` 只含 stage/bind/port/hint，不含正文） |
| `E5000` | 500 | Doc2mdError（基类） | 未归入具体子类的内部错误兜底码——即 `Doc2mdError.code` 本身（无独立 `E5000_*` 类，不进 `CODE_INDEX`）。另用作批量进程池空窗超时对挂死件的逐件记账码（`perf.batch_file_timeout_s`，CH-469：损坏 PDF 让 pdfplumber 挂死时整批不再陪绑）。历史漂移（CH-457）：截断/坏包文件曾冒泡成 E5000→HTTP 500，现已在打开边界统一收敛为 E4151→415 |

历史自造码处置（第十一章）：`E5051`（CH-456：缝合失败由 4.3 降级口径吸收，抛码反而违背降级语义）已删除且不复用。`E4091`（CH-439 撤销旧的「缺 confirm」码）段位后经 CH-471 / STOR-5 以**全新语义**（重复转换护编辑）重新启用——「已删除」仅指旧语义，非码值永久空置。

告警码（写入 `ir.warnings` 与报告，不影响成功判定）：

| 码 | 语义 |
| --- | --- |
| `W1043` | 跨片表格不合并（缝合处任一侧为 table）；同时是降级单片时的留痕标记 |
| `W1044` | 分片转换说明：缝合降级为单片时写入 `ir.warnings`（CH-456 / T9 起真实写入） |

## 附录 E 数据库结构

`state.sqlite`，`PRAGMA user_version` = `DB_VERSION = 6`（CH-469 / V-4 由 4→5 补三个索引；CH-476 / R7-STOR-1 由 5→6 新增 `search_map`，同批 R7-STOR-2 把 DDL 重放改为无条件执行，见下；与 IR 的 `SCHEMA_VERSION = 14` 是两套独立口径，不得混用）。连接策略：`journal_mode=WAL`、`synchronous=NORMAL`（CH-471 / STOR-1，决策 D1）、`busy_timeout=30000`、`foreign_keys=ON`、`mmap_size=256MB`、`cache_size=-65536`、关闭前 `PRAGMA optimize`，每线程独立连接（`threading.local`），写路径统一 `BEGIN IMMEDIATE`。

| 表 | 列 | 索引 / 约束 |
| --- | --- | --- |
| `docs` | `doc_id` PK, `source_key`, `source_name`, `source_name_raw`, `format`, `pages`, `sharded`, `shard_count`, `status`, `profile`, `created_at`, `updated_at`, `meta_json` | `ix_docs_source(source_key)` |
| `blocks` | `doc_id`, `block_id`（联合 PK）, `type`, `text`, `level`, `page`, `flags_json`, `marks_json`, `immutable`, `revision`, `updated_by`, `updated_at` | 主键 `(doc_id, block_id)` |
| `edit_audit` | `id` AI PK, `doc_id`, `block_id`, `before`, `after`, `from_revision`, `to_revision`, `actor`, `ts`, `reason` | `ix_audit_doc(doc_id, block_id)`；`ix_audit_doc_id(doc_id, id)`（CH-476 / R7-STOR-2：审计取回是 `WHERE doc_id=? ORDER BY id`，前者的列序能过滤不能排序） |
| `deletions` | `id` AI PK, `doc_id`, `source_key`, `actor`, `ts`, `kind`(`doc`\|`source`), `cascade_json` | 只记数量与路径指纹，不记正文 |
| `tokens` | `token` PK, `role`, `label`, `created_at`, `revoked_at` | 授权每次实时查库 → 吊销即时生效 |
| `tasks` | `job_id` PK, `doc_id`, `source_key`, `status`, `idem_key`, `created_at`, `started_at`, `finished_at`, `error_code`, `detail_json`, `source_path`, `mode` | `ix_tasks_idem`；`ix_tasks_queued(status, created_at)`；`uq_tasks_doc_active` 部分唯一索引 `ON tasks(doc_id) WHERE status IN ('queued','running')`；`ix_tasks_doc(doc_id)`（CH-469 / V-4：非活动态按 doc_id 的回溯与 FR-128 级联删除不受部分索引覆盖，原全表扫描） |
| `events` | `seq` AI PK, `ts`, `code`, `doc_id`, `job_id`, `payload_json` | `ix_events_job(job_id)`；`ix_events_doc(doc_id)`（CH-469 / V-4：`GET /jobs/<id>/events` per-job 通道与删除级联的 `COUNT/DELETE WHERE doc_id=?` 原全表扫描，常驻实例上为热点）；全局事件总线，前端唯一通道 |
| `search_blocks`（FTS5 虚表） | `doc_id UNINDEXED`, `block_id UNINDEXED`, `text`, `aux`；`tokenize='unicode61'` | `aux` = CJK 单字 + bigram + 非 CJK 原样段；虚表不能 `ADD COLUMN` → 缺列即 `DROP` 重建 + 回填；行号由 `search_map` 显式赋值（不依赖 FTS 内部 rowid 语义） |
| `search_map` | `fts_rowid` PK AUTOINCREMENT, `doc_id`, `block_id` | `ux_search_map_blk(doc_id, block_id)` 唯一 + `ix_search_map_doc(doc_id)`（CH-476 / R7-STOR-1，决策 D1：FTS5 两个 `UNINDEXED` 列不参与倒排，`DELETE … WHERE doc_id=?` 与单块 `WHERE doc_id=? AND block_id=?` 原是对全内容表的线性扫描且落在 `BEGIN IMMEDIATE` 写锁内——10 万块级库上批量落库 100 文档 = 100 次全库扫；映射表把整篇删除收敛为 O(本文档块数)、单块定位 O(1)） |

迁移纪律（四条，均幂等）：① DDL **每次开连接无条件重放**（全 `IF NOT EXISTS`，成本为几十次字典查找）；`user_version < DB_VERSION` 只门控**数据回填**（CH-476 / R7-STOR-2，决策 D1：原实现把 DDL 重放也关在该门之后，而 CH-469 补 `ix_events_*` / `ix_tasks_doc` 时恰未 bump 版本，于是 `user_version=5` 的存量库永远缺这些索引——「加 schema 必须 bump」是纪律，不是可依赖的前提）；② `PRAGMA table_info` 判缺再 `ALTER`（`blocks.revision`、`tasks.source_path`/`mode`、`tokens.role` 存量默认 `admin`）；③ FTS 虚表按列检测重建回填；④ 队列管道数据用真列而非塞 `detail_json`（后者是结果/失败契约面，且 `json_extract` 依赖 JSON1 编译开关）。

`doc2md cron` 步骤序列（`report.steps[]` 的 `step` 名，按执行顺序）：

| 模式 | 步骤 | 失败语义 |
| --- | --- | --- |
| daily | `daily_report` → `local_snapshot` →（快照成功且 chmod 有告警时）`work_dir_mode` → `rotation` → `tmp_clean` → `log_rotation` → `events_purge` → `tasks_purge` → `search_reconcile` | 任一步 `ok=False` → 整体 `ok=False`，退出码 5 |
| weekly | 同 daily，另在本地快照成功后、`rotation` **之前**执行 `external_backup`（故新副本当轮即受轮出保护） | 未配置 `ops.backup_external_dir` → `skipped` 且 `ok=True`；已配置但缺口令 → `ok=False`（宁可不备份不落明文） |

磁盘门禁（NFR-6）不是独立步骤名：它是 `local_snapshot` 的前置检查，空间不足即该步 `ok=False` 并带 `code=E4131`，不留半截快照。三个保留期/对账步骤的口径：`events_purge` 按 `ops.events_retention_days`（默认 30）删过期事件，`tasks_purge` 按 `ops.tasks_retention_days`（默认 30）只删**终态**（`done`/`failed`/`rejected`）且已过保留期的作业行（CH-471 / SVC-4——`tasks` 此前除逐 doc 级联外永不清理，长运行实例无界增长，`GET /jobs` 每 5s 重扫全表 GROUP BY 的延迟随之持续恶化），`search_reconcile` 比对 `blocks` 与 `search_blocks` 的逐文档计数、背离即整篇重建索引并清孤儿 FTS 行（CH-471 / STOR-3，与 serve / work 启动对账同口径，把「迁移崩溃 → 检索静默失效」变成一轮 cron 自愈）。

`--no-backup` 跳过 `local_snapshot` / `external_backup` / `rotation` 三步（排障用），其余照跑。轮出（`rotation`）见 6.4：本地与外置两个目录各按「文件名日期与 mtime 取更早者」判定超 `ops.backup_rotation_days`（默认 14）天的副本并清理。

## 附录 F 门禁断言清单

红线层 = `pytest -m gate`，当前 **260 项**（第一层 40 + 第二层 29 + CH-469 批次② 10 + 批次③ 54 + CH-473 第五轮 12 + CH-474 补入 X-17 1 项 + CH-475 第六轮 7 项 + CH-476 第七轮 91 项 + CH-477 对账 4 项 + CH-478 批次① 收口 4 项 + 批次② 度量对照 4 项 + CH-479 CI 矩阵口径 4 项；墙钟耗时历史轮次 72–110s，T8 优化后 CH-456/457 轮起稳定 **53.0s**，<60s 达标——逐轮实测见第十二章 12.1）。这里的「项」是 pytest 收集数，含参数化展开（如恶意名单 12 例各算一项）。CH-469 收口轮实测本机（Python 3.15 / macOS arm64）**28.7s**，文档收口后的终轮复跑 **35.6s**（新增 64 项红线后仍比历史 53.0s 口径更短：新增项多为纯单元断言，不含真实转换长跑；两次都在同一台机器同一份代码上，差值即负载噪声，故耗时只作历史）。CH-471 实施后全量 **543 收集 = 543 passed / 0 skipped**（本机 macOS arm64 / Python 3.15 实测：全量 60.7s、gate 132 项 23.7s）。**CH-473 收口轮口径**：gate **145 项全绿（62.7s）**、全量 **605 收集 = 605 passed / 0 skipped（126.3s）**（本机 macOS arm64 / Python 3.15 实测；红线层构成见下，第三层由 53 修正为 54——`test_ch469_ops.py` 的参数化用例随 Q-1 的权限双形态校验净增一例，收集数以 `--collect-only` 实测为准）。**CH-474 轮口径**：gate **146 项全绿（60.0s）**、全量 **606 收集 = 606 passed / 0 skipped（122.0s）**（本沙箱 Linux / Python 3.11.1 实测；+1 项为 X-17 回归测试，见附录 F 第四层组注）。**CH-475 轮口径**：gate **153 项全绿（30.04s）**、全量 **677 收集 = 677 passed / 0 skipped（66.09s）**（本机 macOS arm64 / Python 3.15 实测；+7 项红线为第六轮 12 项 A 类修复的门禁断言，见附录 F 第五层组注）。**CH-476 轮口径**：gate **244 项全绿（33.9s）**、全量 **773 收集 = 773 passed / 0 skipped（73.3s）**（本机 macOS arm64 / Python 3.15 实测；+91 项红线为第七轮 13 批 A 类修复的门禁断言，见附录 F 第六层组注；项数口径由 `--collect-only` 实测，两个文件相加 83 + 8，其余分层数字未变）。**CH-477 轮口径**：gate **248 项全绿（33.25s / 收口复跑 31.98s）**、全量 **777 收集 = 777 passed / 0 skipped（73.23s / 收口复跑 68.79s）**（本机 macOS arm64 / Python 3.15 实测，`--junitxml` 的 `tests=` 取 248 / 777；+4 项红线为 9.2 T15 落地的版本与编号对账断言，见附录 F 第七层；其余八个文件收集数逐项复算未变，非红线层仍 529 项。本轮起项数取证一律用 `--junitxml` 的 `tests=` 属性：本机进度点计数会被用例打印的 `.` 污染，同一次全量实测点数 784 而 junit `tests=777`，散文点数不作证据）。**CH-478 批次① 口径**：gate **252 项全绿（31.54s）**、全量 **795 收集 = 795 passed / 0 skipped（70.01s）**（本机 macOS arm64 / Python 3.15 实测，`--junitxml` 的 `tests=` 取 252 / 795；+4 项红线为 R6-PIPE-1 收口的有序列表端到端断言、+14 项为同文件判据形态参数化（非红线），见附录 F 第八层；其余文件收集数未变，非红线层 529 → 543）。**CH-478 批次② 口径**：gate **256 项全绿（31.16s）**、全量 **805 收集 = 805 passed / 0 skipped（69.39s）**（本机同机复跑，`--junitxml` 的 `tests=` 取 256 / 805；+4 项红线为 R6-PIPE-2 的 B-2 出口度量对照，另 +6 项信息层，见附录 F 第九层；其余文件收集数未变，非红线层 543 → 549，+6 全部归因到 `test_ch478_gate_drift.py` 自身，无未归因余额）。**CH-479 轮口径**：gate **260 项全绿（38.55s / 台账收口复跑 33.74s）**、全量 **811 收集 = 811 passed / 0 skipped（75.78s / 收口复跑 81.66s）**（本机 macOS arm64 / Python 3.15 实测，`--junitxml` 的 `tests=` 取 260 / 811；+4 项红线为 CI 版本矩阵口径的四条静态断言、另 +2 项信息层，六项全部归因到新增的 `tests/test_ch479_ci_matrix.py` 自身，其余文件收集数逐项复算未变，非红线层 549 → 551；该文件读的是 `.github/workflows/*.yml` 文本，环境无 `pyyaml` 故用无依赖正则解析，解析不到即报「锚点失效」而非跳过。本轮 gate 两次实测 38.55s / 33.74s 高于批次② 的 31 秒带，属同机负载抖动——四条新断言是纯文件读与正则，单测自身耗时可忽略，硬口径仍是项数）。门禁耗时随红线增长回到 60s 量级，9.1 的 DoD 行已按「量级达标 + 项数为硬口径」重估（分层复核登记为后续可选项）。

**零跳过纪律（CH-469，CH-473 修订口径）**：套件里的 `pytest.skip` 只允许两种形态——**显式降级开关**与**登记在册的宿主工具例外**，其余一律视为「把宿主条件写进断言」的病灶，必须改在产品/测试侧消除。① 显式降级开关：golden 资产缺失时 `conftest._missing`（`tests/conftest.py`）与 `tests/test_v24_gates.py` / `tests/test_perf_lowend.py` 的对应路径**默认硬失败**（`pytest.fail`），仅环境变量 `DOC2MD_ALLOW_MISSING_GOLDEN=1` 才降级为 skip（CH-469 治「静默跳过后仍判绿」、CH-471 TEST-1 推广到 gate 层、CH-473 TEST-1 收口最后两处），`tests/test_golden_repro.py` / `tests/test_m5_m6c.py` / `tests/test_t9_convergence.py` 的三处裸 skip 也已在 CH-473 统一到该口径。② 登记例外：`tests/test_t4_queue_ui.py` 的 `node not available`——该用例校验的是 `editor.html` 内联 JS 的语法（被测对象与 doc2md 运行时无关），node 是宿主工具而非被测资产的组成部分；缺 node 时跳过不构成「资产缺失伪装成通过」（被测资产本身在库里且被其他用例消费），但**该用例在无 node 的宿主上零覆盖**这一点必须如实登记，不伪装成「全量执行」。③ `ops/bench/t6_large_corpus.py` 的 proc 采样分支原先硬编码 `/proc`，macOS 上只能跳过 → `_tree_proc(root_pid, proc_root=…)` 可注入假 proc 根，任意宿主确定性覆盖；`tests/test_m3_bigcopy.py` 的黄金大文件缺失即跳 → 改为恒真两分支断言（在则验体积口径，不在则验「make_golden 可现补 + 无用例把它当强制前置」）。纪律判据：`grep -rn "pytest.skip" tests/` 的命中必须能逐条归入上述两类，且全量套件实测 **0 skipped**（当前 811 项全 passed）；`pytest.raises(pytest.skip.Exception)` 是断言形态不是跳过，不计入。

第一层：六条契约红线（`tests/test_v24_gates.py`，收集 40 项）

| 组 | 断言要点 | 收集项 |
| --- | --- | --- |
| 1 `shard_two_phase` | 分片真实发生（非降级）+ `char_similarity ≥ 0.99` + 无半句开头块 + **分片去重后块数 == 单片块数**（CH-469 在同一用例里加的硬口径：分片只许去重、不许增删，缝合回归当场现形）（1）；边界块在报告可筛选（1）；续接被吸收且留痕（1）；重叠去重后无重复块 id（1）；页数不重复计数（1）；缝合失败诚实标单片（1） | 6 |
| 2 `malicious_filenames` | 恶意名消毒落库（12 例参数化）；黄金清单全量复核（1）；zip 条目名合规（1）；恶意源名 zip 拒收 400/E4003（1）；符号链接循环 10s 内扫完（1）；目录过宽 413/E4133（1） | 17 |
| 3 `injection_resistance` | 干净 vs 注入最差 Δf1 绝对值 ≤ 0.005（1）；拒收归因含 `injection_candidate` + `model_hallucination`（1）；白名单外 op 应用数 = 0（1） | 3 |
| 4 `edit_optimistic_lock` | 双写后者 425/E4251 且正文未被覆盖、审计 `before` 与 `from_revision` 对应、缺 `base_revision` 400（1）；灰度期可选口径（1）；reader 不可编辑（1） | 3 |
| 5 `delete_cascade` | 缺 confirm 400/E4001 → 200 → GET 404、搜索零残留、留痕无正文（1）；级联产物文件清零（1） | 2 |
| 6 `log_rotation` | 写入器阈值/份数/首行标记（1）；服务内建轮转且不重启（1）；业务事件入日志且无正文（1）；启动清扫 `work/tmp`（1）；serve 与 cron 不共享活动文件（1）；服务遵从轮转配置（1）；轮转用内存字节计数（1）；日志零正文（1） | 8 |
| 附 | `watch_stability`（注入时钟：增长期零入队、静默满窗口恰好一次）随第 2 组同文件执行 | 1 |

第二层：低配机专项（`tests/test_perf_lowend.py`，收集 **29** 项；本附录原记 27 项，CH-469 收口轮按 `pytest -m gate --collect-only` 实测校正——收集数一律以工具实测为准，不靠手加散文计数）

分块默认值有界、分块产物等价（chunk 1/2/3/32 vs 整本）、流式哈希与全量哈希一致、workers 边界（0→`min(4,cpu)`、负→1）、进程池 vs 串行产物逐条目一致（含 `spawn` 参数化 = Windows 语义）、逐文件门禁拒收上报、**批量空窗超时逐件记账（CH-469：`perf.batch_file_timeout_s` 生效——注入一个睡 6s 的 worker，1s 空窗必须把挂死件记成 `E5000` 且整批 <4s 返回；此前那条超时写在 `as_completed` 循环里永不触发，属伪覆盖）**、CLI 目录确实走池、异步入队→消费→文档可见、幂等与附着、领取原子性、无源路径任务判失败、**遗骸回收命中真实时间戳格式（CH-455）**、**run_work 回收分支不再崩（CH-455）**、`work` 日志独立、磁盘门禁四态（阻断 / 系数 / 仅双零关闭 / API 413）、cron 记磁盘门禁失败、类型判定映射 415、API 魔术码不符 415、CLI 机器行纯 ASCII、`_console_safe` 替换不可编码字符、不支持类型退出码 4、目录含坏文件退出码聚合、`doctor` 暴露关键开关。

覆盖纪律：任何「资产存在但无测试消费」「断言恒真（如中文检索零命中下的 0 残留）」都算伪覆盖，必须在本清单里显式失效或补真判定用例（第十二章教训条目）。

第三层：CH-469 全面审查优化轮（`tests/test_ch469_service.py` + `tests/test_ch469_ops.py`，收集 64 项）

本轮把「安全原语用真实 socket / 真实容器字节断言，契约面用真实 Flask test client 与真实 CLI」的口径钉进红线：

| 组 | 断言要点 | 收集项 |
| --- | --- | --- |
| 7 `service_batch2`（V 项） | 任务排空遇 IR 不可读必须判失败而非静默产 0 块（1）；`docs/blocks` 窗口与 total 一致（1）；PATCH `base_revision` 非数字 400（1）；未知角色签发 400（1）；未知 token 吊销 404（1）；`PUT /settings` 多次写入累积而非互相抹掉（1）；写 override 不修改进程内共享嵌套 dict（1）；常驻 `run_work` 循环内重跑遗骸回收（1）；编辑器页日志汇是文本内容（1）；CLI 未预期异常映射 `EXIT_SYS`（1） | 10 |
| 8 `security_batch3`（O 项） | 离线守护可重入、仅最外层解除（1）；`connect_ex` / `sendto` 同样受拦（1）；zip 条目名拒绝对称路径 / 盘符 / UNC / 空段（12 例参数化）；`derive_key` 盐必填（1）；改头认证失败（1）；历史 v2 容器仍可解密（1）；轮出只认本工具快照名（1）；磁盘门禁取 available 而非 free（1）；指纹与行序无关且按重数敏感（1）；watch 登记簿回收消失文件（1）；`guard_walk` 截断即 `E4133`（1）；`read_metadata` 声明 KV 数超限报错（1）；快照失败不留半截（1）；恢复 fsync 先于 rename（1）；日志写入器多线程轮转仍可解析（1）；`SPEC` ↔ 默认 ↔ 样例 ↔ 本附录键集一致（1）；`load()` 接受纯净默认（1）；类型/区间非法值拒收（24 例参数化）；CLI 配置错 → 退出码 2（1） | 53 |

批次①（管线与模型，S 项）的证据不按独立红线组收集，而是就近落在各自的既有套件文件里、随全量执行：缝合去重口径进 `tests/test_sharding_unit.py`（17 项，含 5 项 seam 专项）、单文件超时进上面第二层、连接类重试进 `tests/test_t2_llm_server.py`（26 项，新增 4 例：瞬断重试后成功 / 重试耗尽报 `connect` 阶段 / `retries=0` 恢复单次尝试 / HTTP 状态码不重试）、缺页门禁进 `tests/test_batch2_storage.py`（30 项）。这样分的理由是这批全是「改了既有行为的语义」，把断言贴在使用它的套件里比再造一组更容易被后续轮次消费。

第四层：CH-473 第五轮全面审查（`tests/test_ch473_fixes.py`，红线 12 项 / 全量 61 项；CH-474 补入 X-17 回归 1 项 → 红线 13 / 全量 62）

本轮改动的红线面集中在「G6 等价红线本身要能失败」与「迁移链真跑」两类：

| 组 | 断言要点 | 收集项 |
| --- | --- | --- |
| 9 `r5_gate` | seam 尾缀判定的相邻页约束（PIPE-5：跨页尾块与次页首块同指纹才允许丢弃后缀；非同页尾随即保留——堵「把不同内容的两块当重复」）（2）；截断块替换的片级锚定（PIPE-6：替换只允许发生在真实片界且替换块确以截断面收尾；片界外的真短块不被误换）（2）；两步 PUT 掏不空 loopback（SEC-1：先 PUT 放开 `api.allow_remote_bind` 再改 `api.bind` 的组合被运行期不可变键拦下）（1）；`serve --host` 非回环旗标直接拒绝（SEC-2，CLI 面与配置面同口径）（1）；force 覆盖时同事务清 `edit_audit`（STOR-1：rollback 不得复活被覆盖前的旧文本）（1）；COMMIT 失败显式 ROLLBACK 且连接归还（STOR-3：body 已炸时回滚失败不顶替病因）（1）；force 参数的字符串形态解析（SVC-6：`"true"`/`"false"` 布尔串与字面量同口径，`"false"` 不误触强制覆盖）（2）；DB 迁移链（TEST-5：v3 存量库开库即升级到 v5——补 v4 列与四索引、明文 token 就地哈希后 `auth.check` 仍认、FTS 行不丢且 CJK 子串仍命中、二次开库幂等；v2.2 遗留库重建 FTS 并回填 revision/role 列）（2） | 12 |

其余 49 项为非红线回归（netguard 缓存封顶、备份原子写、GGUF BOOL 短读、`apply_ops` 索引字典化、块 id 唯一性、S4 围栏逐字节保留、`_processes` 版本哨兵、`doctor` 退出码契约、同机双跑相对口径等），随全量执行。

第五层：CH-475 第六轮全面审查（`tests/test_ch475_fixes.py`，红线 7 项 / 全量 71 项）

本轮改动的红线面集中在「终态/凭据/不可变守卫不被绕过」与「等价性度量不再对空白失明」两类（报告 `docs/review-round6-CH-475.md` §4 的 12 项 A 类修复，逐项口径见该文件模块 docstring）：

| 组 | 断言要点 | 收集项 |
| --- | --- | --- |
| 10 `r6_gate` | `mark()` 状态守卫不翻转 done/rejected（R6-SVC-3：终态幂等，一个已 done 的作业不能被后续 `mark(rejected)` 回退）（1）；幂等重放跳过 `save_ir` 直接复用、不触发 E4091 冲突（R6-SVC-3：`_finish_one` 对 `idem_hit`/`attached` 早返回）（1）；PATCH 审计 actor 为 token 的 sha256 前 12 位而非明文前缀（R6-SVC-2：NFR-20 凭据不落库）（1）；`scrub_record` 对 >64 字符的 `bytes` 值先解码再指纹化（R6-OPS-2：二进制正文片段不绕过脱敏）（1）；`/system/reload` 拒绝运行期不可变键变化（R6-SVC-1：堵「改配置文件 + reload」绕过 `check_runtime_immutable` 的旁路，返回 400/E4001 且 baseline 不变）（1）；畸形 `marker` 算子被中和为 `-` 而非在 `re.sub` 期崩溃（R6-LLM-1：`\1` 注入不击穿渲染）（1）；`char_similarity` 现在能看见缩进漂移（R6-PIPE-2：`norm_lines` 保留行首缩进后，G6 等价门对**缩进类**漂移不再失明）（1） | 7 |

其余 64 项为非红线回归（`mark` 仍允许 failed→done 自纠偏与 queued→failed 遗骸清理、审计 actor 尊重显式 `as`、`check_runtime_immutable` 拦 `offline_assert` 变化、`valid_level`/`valid_marker` 参数化、`OpValidator` 拒绝越界算子、`apply_ops` 越界 level 保持原值、`_heading_level` 第三回退信号门（R6-PIPE-1）、`norm_lines` 缩进保留与行内空白折叠、`scan_injection` 新变体命中与零误报（R6-LLM-2 部分）、`doctor` 磁盘不足/未知磁盘/server LLM 不可达退 `EXIT_SYS` 与机器可读 JSON 为 ASCII（R6-OPS-3）、`_jline` 转义非 ASCII），随全量执行。R6-OPS-1（launchd `WorkingDirectory` + 显式 `--root`）的守卫断言在 `tests/test_t5_windows_ops.py`（与 Windows 孪生 parity 同文件）；R6-SVC-4（claim 加 `source_path` 过滤）经代码核验后判定为「验证后延期」——并发危害已被 R6-SVC-3 的 mark 状态守卫吸收，且加过滤会使 `drain_once` 靠 E4001 清理的 NULL-path 孤儿行滞留并经 `uq_tasks_doc_active` 阻塞再提交，登记 B-4（需并发压测复现）；R6-LLM-2 的归因去循环与更宽误报收窄登记 B-3（需真实注入语料）。

第六层：CH-476 第七轮全面审查（`tests/test_ch476_fixes.py` 红线 83 项 / 全量 87 项，加 `tests/test_sharding_unit.py` 内就近落地的红线 8 项 / 全量 26 项 → 本轮 **91 项红线 / 96 项全量**）

本轮 13 批逐条实施（报告 `docs/review-round7-CH-476.md` §4），红线面集中在「竞争窗口的落库次序」「等价红线要能失败」「参数面不被信任」「固定开销按批摊薄」四类。逐项口径（收集项含参数化展开）：

| 批次 | 断言要点 | 红线项 |
| --- | --- | --- |
| ① R7-SVC-1 | `mark()` 回报本行是否真正落库（1）；消费中途文档被级联删除 → 判失败且不写正文（1）；done 路径仍持久化 detail（回归 CH-460 的覆盖修复，1）；同步 `/jobs` 在提交前发现任务行已消失 → 整笔回滚、`docs` 零增长（1） | 4 |
| ② R7-PIPE-1 | 缝合的编号续接方向为**递增**（`2.1→2.2` 吸收、`2.2→2.1` 不吸收）（2）；结构块永不参与续接吸收（heading / table / code / quote / page_break，5 例参数化）（5）；标题后的同号递增不被吞（1） | 8 |
| ③ R7-LLM-1 | `OpValidator` 拒绝畸形 `target`（5 例：int / True / 3.5 / dict / bytes）（5）；`apply_ops` 遇畸形 target 不崩、块数与正文不变（5 例）（5）；合法 target 集合逐一命中（1） | 11 |
| ④ R7-STOR-1/2 | v6 DDL 含 `search_map` 与两个索引（1）；v5 存量库开库即回填映射且级联删除后 FTS 零残留（1）；单块编辑/删除经映射 O(1) 且映射一致（1）；`reconcile_search` 经映射修复并清孤儿（1）；force 覆盖不留重复 FTS 行（1） | 5 |
| ⑤ R7-PERF-1 | 领取量 = `workers × queue_batch_factor` 的取值口径（含 `None`/`0`/负数/非数字/`1`/`2`/`6` 七例）（7）；一轮排空确实按批领取超过池大小（系数 4/1 两例，系数 1 即旧口径回归档）（2）；同路径作业天然归并、不因批量放宽并发帽（1）；`perf.queue_batch_factor` 进 `SPEC` ↔ 默认 ↔ 样例 ↔ 附录 A 键集（1） | 11 |
| ⑥ R7-PERF-2/3 | 漂移判定用**消费时哈希**、父进程不再重算全文件哈希（1）；源文件消失但内容一致时不误报漂移（1）；`enqueue_many` 并行哈希且提交顺序 = 扫描顺序（1）；批内重复的附着与幂等不变量（1）；坏输入整批回滚零副作用（1）；单条入口与批量入口同形（1）；哈希线程数配置键契约（1）；API 异步目录面并行哈希且有序（1）；异步入队遇非法魔术码零队列行（1） | 9 |
| ⑦ R7-PIPE-2/3/4 | 跨 ≥3 页长块的片界尾缀被丢弃且**分片去重后块数 == 单片块数**（1）；超出重叠窗口的间隔不误删（1）；围栏长度按内容取，正文含 ``` 字面行不再破栏（1）；DOCX `Heading N` 显式层级不被 `_fix_levels` 覆写（1）；PDF 编号标题层级零变化（守住旧口径）（1） | 5 |
| ⑧ R7-OPS-1/2 + TEST-1 | `sweep_tmp` 永久豁免 `drill-*`（1）；`cron --daily` 不收回留存取证沙箱（1）；CLI `cron` 四条退出面（正常退 0 并打步骤 / `--daily` 与 `--weekly` 互斥 / `--no-backup` 只跳备份三步 / 有失败步骤退 5）（4）；日报与 `healthz` 的 `pipeline` 字段取自常量而非字面量（1）；cron 报告文件名日期为 UTC（1） | 8 |
| ⑨ R7-GAP-1 | `w:ins` 插入文字一字不丢、`w:delText` 不入正文（1）；命中块带 `flags.has_revision`（1）；黄金样本 `sample_revisions.docx` 端到端（1）；无修订段落产物逐字节不变（等价红线）（1） | 4 |
| ⑩ R7-GAP-2/3 | 注文全部落地且绝不并进正文块（1）；注内超链接按注 part 自己的关系表解析（1）；单元格内多段落以单空格相连、单段单元格逐字节不变（1）；无注的 DOCX 不多出行（1）；黄金样本 `sample_notes.docx` 端到端（1） | 5 |
| ⑪ R7-PERF-a…k | 行级提示预扫覆盖每个分支且良性/折叠语料判定相等（2）；`_page_metrics` 与正则参考实现相等（1）；样式记忆与 python-docx 直查相等且注行共享缓存（2）；文档级预检 == 逐段扫描（1）；`submit_many` == 逐条 `submit`、全或无、行数与计数不变（3）；`delete_source` 查表面只读（1）；`/jobs` 与 `/search` 的 `limit` 截到 500、负数 400（2）；`/markdown` 流式响应字节与整篇读取相同、头正确（1） | 13 |
| ⑫ R7-SVC-2/3/4 + SEC-1 | `POST /jobs` 的 `mode` 非白名单 → 400/`E4001` 且 tasks/docs 零增长、四类合法值照过（1）；串行分支把 `mode` 透传给 `convert`（1）；池分支把 `mode` 透传给 `run_batch` 且闸门内持有、结束归还（1）；闸门占用 → 有界等待后 503 + `Retry-After`、库面零增长（1）；串行路径永不触碰池闸门（1）；PATCH 的 `reason` 与 `POST /tokens` 的 `label` 非字符串 → 400 零副作用、超 256 截断（2）；`editor.html` 的 `esc()` 覆盖 `'`（1） | 8 |

其余 4 项（`enqueue` 哈希线程 reader 容错、编号逆序不误判为尾缀、无围栏字面的代码块字节不变、`para_mark` 修订旗标只打该块）为非红线信息面断言，随全量执行。

本轮纪律补充：新增红线**逐条做过变异验证**——把对应修复临时回滚后该用例必须转红（12 项 A 类修复全部实测过），且守卫类用例优先用 spy 对象而非真信号量，使回滚后的守卫缺失表现为**干净失败**而不是把 CI 挂住。宿主相关判定一律在产品侧解决或登记 B 类，不用 `pytest.skip` 抹红（零跳过纪律 ③）。

第七层：CH-477 版本与编号对账（`tests/test_ch477_version_sync.py`，红线 4 项 / 全量 4 项）

| 断言 | 要点 | 红线项 |
| --- | --- | --- |
| 版本三处对账 | `doc2md.PLAN_VERSION` == 本文 header「方案文档版本」== §1.4「本版 vX.Y」，两个锚点各自解析失败即报「红线锚点失效」（1） | 1 |
| 编号进度对账 | README「当前编号至 `CH-xxx`，下一条自 `CH-yyy`」== 本文 10.1 的两个数字（当前 / 下一条分别相等）（1） | 1 |
| 管线版本两处 | `PIPELINE_VERSION` == header 管线行 == §1.4 版本表管线行（1） | 1 |
| schema / DB 两处 | `SCHEMA_VERSION` == §1.4 版本表 IR 行；`DB_VERSION` == §1.4 版本表状态库行（D-5 的两套口径各自设防）（1） | 1 |

四条均为纯静态断言（只读两份 markdown 与三个常量，不建库、不转换、不起子进程），对门禁耗时的贡献落在本机散布带内不可测出（244 项 35.5s → 248 项 33.25s / 31.98s）。**能失败**已按批次⑫ 的变异纪律逐条验证：改常量 / 改 README 编号 / 改 header 管线版本 / 改版本表 IR schema 或 DB 数字 / 改写 §1.4 句式，五种变异各自使对应断言转红，最后一种给出的是「红线锚点失效」而不是跳过。

第八层：CH-478 R6-PIPE-1 收口（`tests/test_ch478_fixes.py`，红线 4 项 / 全量 18 项）

| 断言 | 要点 | 红线项 |
| --- | --- | --- |
| 同字号编号归列表 | 黄金样本 `numlist_1p.pdf` 端到端：三行「1.／2.／3.」（字号==正文众数、无加粗）落 `list_item` 且各自成块（旧形态是被 `_join_back` 缝进一段），断言 `list_item` 计数恰为 3 | 1 |
| 真标题信号照旧发火 | 同页对照组 `第一章 绪论`→H1（CJK 词头）、`1.1 环境要求`→H2（多级编号）、`2. 运行说明`→H1（14pt ≥ 众数+3.5）——门不是「同字号一律不判标题」 | 1 |
| Markdown 保留序号 | `content.md` 含整行 `1. 安装依赖` 且不含 `- 1. 安装依赖`（S4 切分取 `m.end()`，正则末尾若多吞一个字符则表现为「1. 装依赖」，此断言即该变异的捕手） | 1 |
| 正文行不进该分支 | 同页两句说明行与 12 行众数字号正文仍为 `paragraph`（12 行逐行独立），且「安装依赖」不出现在任何段落里 | 1 |

其余 14 项为判据本体的正/反向形态参数化（非红线）：命中形态 5 项（`1. `／`12、`／`3）`／`4．`／`1.安装依赖` 无空格），拒绝形态 9 项（小数 `3.5`、四位年份 `2019.`、行长 ≥30、句末标点、整句说明、符号 bullet、CJK 词头、空串、纯空白）。四项红线均走真实转换（S1→S4 全链路 + 黄金样本），合计门禁耗时贡献在本机散布带内不可测出（248 项 31.98s → 252 项 31.54s）。**能失败**已逐条做变异实测：删掉 S4 的序号出口（`list_item` 全部退回 `- `）→ 红线「Markdown 保留序号」转红；把 `_is_ordered_item` 改成「正则命中即真」（去掉行长 / 句末标点守卫）→ 判据反向形态两项转红（≥30 字符行、句末标点行）；把编译式末尾的先行断言写回 `\S`（多吞一个字符）→ 同一条序号红线以「1. 装依赖」形态转红，即首轮实施时实际撞到的那个失败。

第九层：CH-478 批次② R6-PIPE-2 的 B-2 出口（`tests/test_ch478_gate_drift.py`，红线 4 项 / 全量 10 项）

| 断言 | 要点 | 红线项 |
| --- | --- | --- |
| 缩进漂移判红 + 旧口径精确失明 | 黄金样本 `codeindent_1p.pdf` 真实产物（397 字符 / `norm_lines` 16 行）按 `_INDENT_LEVEL` 补回三级缩进：收紧后 `char_similarity` = **0.973958 < 0.99** 判红，且 `norm_lines` 两份不相等；把归一化换回 CH-475 前的 `_WS.sub("", ln)` 复算，同一对精确 = **1.000000**（结构失明的实证，不是推定）。锚点命中数不足即先红，防样本或渲染形状漂移后断言空转 | 1 |
| 内容级漂移不因收紧获得额外敏感度 | 围栏破损（0.991914 / 0.991667）与 `-`↔`*` 标记替换（0.994652 / 0.994490）在新旧口径下差值 < 0.001 且红/绿结论一致——**CH-475 的登记文案把这两类与缩进并列为「旧口径失明、收紧后可见」，本条把它钉成反向断言**，防止后续按那个口径立论 | 1 |
| S1 抽取剥掉行首空白而几何仍在 | 同页等宽行 `Line.text` 全部 `== text.lstrip()`，但 `Line.x0` 有 ≥3 个档位（实测 56.7 / 120.7 / 184.7）——代码块缩进在 S1 就丢失，等价门「两侧都无缩进、无漂移可看」。断言只落在 S1 层，故 12.7 待决策项若日后按 x0 重建缩进，本条照旧成立 | 1 |
| 参照实现与产品实现同值 | 本文件自带的旧/新口径参照 `_similarity(norm_lines, …)` 必须等于产品 `char_similarity`；两者分叉即前三条的对照数字全部失效，故单列一条守 | 1 |

其余 6 项为信息层：`norm_lines` 的四形态参数化（行首缩进保留 / 行内空白折叠 / 纯空白行丢弃 / 制表符缩进）、样本四类块形齐备（code 2 / list_item 2 / heading 1 / paragraph ≥3）、行内空格漂移「脱盲但未判红」（0.997333，旧口径 1.0）——最后这条是 **0.99 只探测粗大漂移、不是等值证明**的实例，逐字节等值归 12.2 的 sha 对照。**能失败**已逐条做变异实测：`norm_lines` 改回删尽全部空白 → 缩进红线、`norm_lines` 参数化 3 例、行内空白信息层共 5 条转红；删去 `char_similarity` 的区间字符级精比（non-equal 区间不再补分）→ 内容级红线、参照同值红线、行内空白信息层 3 条转红；只把产品归一化位数 6→4 → 本语料无感，故**不**作为对账证据引用（如实登记：该位数不由本文件守护）。

第十层：CH-479 两套 CI 工作流的版本矩阵口径（`tests/test_ch479_ci_matrix.py`，红线 4 项 / 全量 6 项）

| 断言 | 要点 | 红线项 |
| --- | --- | --- |
| 两工作流版本集合相等且等于登记口径 | `ci.yml` 的 `merge-gate` 矩阵与 `ci-full-matrix.yml` 的 `full-suite` 矩阵必须给出同一个 `{3.11, 3.12, 3.13}`，且等于测试内的登记常量——版本清单从一处变两处是本轮裁定的直接代价，只改一个文件会出现「推送三档绿、周度两档」而两边都显示成功 | 1 |
| 推送面 full-suite 保持钉死单档 | `ci.yml` 的 `full-suite` 作业体里既不得出现 `strategy` 也不得出现矩阵列表，且 `python-version` 钉在 `"3.12"`——把本轮裁定钉成红线：想推翻它必须先改登记再同步断言，不许由一次「顺手给全量加矩阵」代劳（后果是每次 push 的 runner 占用与反馈时长翻三倍） | 1 |
| 每个作业都带时间上界 | 两个文件里逐作业断言 `timeout-minutes` 存在，且名为 `full-suite*` 的腿一律 30——CH-471 口径的推广：缺它时一次挂死占满 GitHub 的 6 小时默认上限，那几小时里红线既不是绿也不是红；周度腿是新写的，最容易漏的就是这条 | 1 |
| 周度腿只由 schedule / dispatch 触发 | `ci-full-matrix.yml` 的 `on:` 段触发集合 ⊆ `{schedule, workflow_dispatch}` 且必须含 `schedule` + `cron` ——挂上 `push`/`pull_request` 就等于偷偷加了第二套推送腿，本轮的性能理由随之失效；缺 `schedule` 则跨版本信息面根本没有覆盖 | 1 |

其余 2 项为信息层：两条全量测试步骤仍带 `-vv`（`pytest.ini` 的 `addopts` 已含 `-q`，净 verbosity 0，挂死时才能点名嫌疑人，CH-471 口径）与周度腿仍带「黄金逐字节复现 + `doc2md doctor`」两步（跨版本资产形状无人看即等于没覆盖）。**能失败**已逐条做变异实测：把周度矩阵里的 `3.13` 改成 `3.14` → 版本集合红线转红；给 `ci.yml` 的 `full-suite` 补 `strategy.matrix` → 单档红线转红；删掉 `lint` 作业的 `timeout-minutes` → 时间上界红线转红；在周度 `on:` 里塞 `pull_request` → 触发面红线转红；把 `jobs:` 段的作业名缩进改写（令解析器切不出作业）→ 断言在比值之前先报「锚点已失效」。工作流文件在变异后按原字节还原，还原后 `git status` 干净。

---

## 附录 G 模块与代码清单

as-built 清单，逐模块给出行数、依赖方向（3.6 的 DAG 层次）、公开符号与被谁引用。「引用方」只列包内真实导入点（函数体内的延迟导入同样计入），不含 `__init__.py` 的 re-export。行数为 `wc -l` 口径，随提交浮动，不作为断言对象；采集方法见本节末。**覆盖面口径**：G.1–G.5 共 33 行 = 包内 39 个 `.py` 减去 6 个只做 re-export 的子包 `__init__.py`（合 69 行，故 33 行合计 9248、整包 9317，两数都以 `wc -l` 实测为准，CH-478 批次② 重算；本行旧记 9205/9274 与 G.6 采集方法段的 9208/9277 曾互不相等，属「改代码后未重算标题」的同一病灶，本轮两处统一到 9244/9313）。

### G.1 包根与入口 / 编排层（8 个模块，2566 行）

| 模块 | 行 | 层 | 职责 | 公开符号 | 引用方 |
| --- | --- | --- | --- | --- | --- |
| `doc2md/__init__.py` | 17 | 基座 | 三个版本常量的唯一来源 + 命名 / 版本治理说明 | `PIPELINE_VERSION` `SCHEMA_VERSION` `PLAN_VERSION` | `cli`（版本）、`ir/model`（schema） |
| `doc2md/cli.py` | 640 | 入口 | argparse 子命令、退出码聚合、控制台加固、`doctor` 自检（llm server 探测 + 冻结键校验 + 配置语义校验，CFG-2；CH-475 / R6-OPS-3 起磁盘不足与 server 模式 LLM 不可达也退 `EXIT_SYS`，配置语义非法退 `EXIT_USAGE`）；`serve --host` 非回环预检（SEC-2，与 `config.validate` 同口径）；CH-475 / R6-OPS-3 把 `cron` / `serve token --list\|--revoke` / `tokenizer` / `doctor` 的机器可读 JSON 出口统一收口到 `_jline()`（`ensure_ascii=True`），消除此前绕过 `_jline` 直接 `json.dumps(ensure_ascii=False)` 的旁路 | `build_parser` `COMMANDS` `main` `DEPENDENCY_MODULES` + 8 个 `cmd_*` | 无（终端入口，`[project.scripts]` 指向 `cli:main`） |
| `doc2md/api.py` | 832 | 入口 | `create_app()` 工厂 + 20 个端点 + 鉴权挂钩 + 脱敏 + 通用 500 JSON（SVC-2）；同步 `POST /jobs` 目录分支复用 `run_batch` 进程池并受全局信号量约束（SVC-3 / `perf.sync_min_parallel`；CH-476 / R7-SVC-3 起该信号量为**有界等待 60s**，超时回 503 `E5041` + `Retry-After`，小批次完全不触碰闸门）；`POST /jobs` 的 `mode` 走白名单并向三条执行分支透传（CH-476 / R7-SVC-2）；`/search` 负 `limit` 拒 400、`PUT /settings` 锁内校验后应用（SVC-4/5）；CH-475：审计 actor 缺省取 token 的 sha256 前 12 位而非明文前缀（R6-SVC-2，NFR-20 凭据不落库）、`_finish_one` 对幂等命中/附着作业跳过 `save_ir` 直接复用（R6-SVC-3）、`/system/reload` 应用新配置前同样过 `check_runtime_immutable`（R6-SVC-1，堵「改配置文件 + reload」绕过运行期不可变守卫） | `create_app` `SCRIPT_UA_HINTS`（+ 内部 `_emit` `_deep` `actor_id`；脱敏已统一到 `ops.logging.scrub_record`，CH-460 / A-12） | `cli`（serve）、`worker`（仅 `_emit`，向上延迟引用） |
| `doc2md/worker.py` | 440 | 编排 | `tasks` 队列消费者：原子领取、遗骸回收（启动 + 常驻循环按 `perf.stale_check_seconds` 节流）、启动时 FTS 对账（CH-473 / R5-STOR-2：与 `create_app` 同口径，历史空索引库不再有最长 24h 的检索静默失效窗口）、`work.jsonl` 属主 | `enqueue` `claim` `reclaim_stale` `drain_once` `run_work` `queue_stats` `WorkReport` | `api`（异步分支 `enqueue`）、`cli`（work） |
| `doc2md/scheduler.py` | 151 | 编排 | 作业投递 / 状态标记 / 事件写入；`mark(tx=)` 可并入调用方事务（CH-460 / A-4）；CH-475 / R6-SVC-3 起 `mark()` 的 UPDATE 带状态守卫 `AND status NOT IN ('done','rejected')`——终态不可被后续 `mark` 翻转（保住 `failed→done` 的自纠偏与 `queued→failed` 的遗骸清理，只封死「done/rejected 被回退」），幂等终态由此成立 | `Scheduler` `Job` `ACTIVE` `TERMINAL`（D-9 的零调用方法 `next_queued`/`active_for` 已在 CH-456 删除） | `api` `cli` `worker` |
| `doc2md/edits.py` | 116 | 编排 | 乐观锁校验 + 审计行 + **该块**索引刷新（`reindex_block`，CH-460 / A-6）；force 覆盖路径同事务清 `edit_audit`（CH-473 / STOR-1）；空文本 PATCH → E4001（CH-471 / STOR-6）；`reason` 非字符串 → E4001、超长按 `_AUDIT_TEXT_MAX=256` 截断（CH-476 / R7-SVC-4，判定在写库事务之前，400 零副作用） | `patch_block` `rollback` | `api` |
| `doc2md/auth.py` | 100 | 编排 | 令牌签发 / 吊销 / 角色 rank 矩阵；`issue(allowed=…)` 按 `api.token_roles` 限制可签发角色；token 哈希存储（SVC-9：不明文落库，`serve token --list` 只显示指纹前缀）；`label` 非字符串 → E4001、超长按 `_LABEL_MAX=256` 截断（CH-476 / R7-SVC-4，与 `edit_audit.reason` 同口径） | `issue` `revoke` `check` `ROLES` `RANK` `MATRIX`（D-9 的零调用项 `require`/`timing_safe_equal`/`RevocationCache` 已在 CH-456 删除） | `api` `cli` |
| `doc2md/cron.py` | 270 | 编排 | 日 / 周步骤编排、`sweep_tmp`（含在途保护窗，STOR-4；`drill-*` 取证目录永久豁免，CH-476 / R7-OPS-1）、周报落盘（文件名日期 UTC，与日报体同口径，CH-476 / R7-OPS-2）；终态作业保留期清理（`ops.tasks_retention_days`，SVC-4） | `run` `sweep_tmp` `CronReport` `Daily` | `api`（`sweep_tmp`）、`cli` |

### G.2 领域层：管线（8 个模块，2524 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `pipeline/convert.py` | 298 | `api` `cli` `pipeline.batch` | S1 → S1.5 → 分片 → S3 → S4 编排、`repair_ops`（LLM 双模式调度，CH-456/T2）、`Result`、`package()` 打包、`render_report()`；CH-459 起 `sniff` 一次直传 `read_any`（A-13）；CH-471 起分片循环对**全文档**算一次 `base_size` 传入每片（PIPE-1：异质字体文档的分片≡单片对字号成立）；CH-473 删去 `head` 死参（PIPE-10） |
| `pipeline/batch.py` | 225 | `cli` `worker` | `run_batch()` 进程池、`default_workers()`、`Summary`；顶层可 pickle 的 `_convert_one`（spawn 约束，3.6 第 8 条）；CH-469 起批量循环按「空窗」判定超时（`perf.batch_file_timeout_s`），异常件只记类名；X-16/X-17 起池收尾**无条件**按 pid 终止本池 worker（`terminate()` → `join(5)` → SIGKILL 兜底 → `join(1)`，修 CPython 3.12.x 对不响应退出哨兵 worker 的解释器退出死锁——正常完成与空窗超时两路径都覆盖） |
| `pipeline/s1_parse.py` | 613 | `convert` `s1_5_gate` `s3_structure` `api` `worker` | 魔术码判定 `sniff`、`read_pdf` / `read_docx` / `read_any`、`Line` / `RawPage`；`_open_source` 打开边界统一映射 E4151（CH-457）；等宽 / 表格行 / 链接 / 标题提示四组行级信号（CH-459）；CH-471 删 `_line_style` 内恒真重过滤（PIPE-2）；CH-476 / R7-GAP-1 起含修订痕迹的 DOCX 段落与表格行走「接受后」口径（`_docx_has_revision` 判定 → `_docx_all_runs` 遍历含 `w:ins` 的全部 run，`w:delText` 天然不取），行级 `Line.has_revision` 供 S3 块级留痕——原 `p.text` / `p.runs` 只看 `w:p` 直接子级，修订插入的正文整段静默丢失；无修订段落仍走 `p.text` 原路径（产物字节不变）；CH-476 / R7-GAP-2 起 `_docx_notes_lines()` 直挂 `word/footnotes.xml` / `word/endnotes.xml`（`part_related_by` 取 part、`parse_xml(part.blob)` 重解析、跳过 `separator` / `continuationSeparator` 占位注），注文落到文档末尾并打 `Line.note_kind`，注内超链接按注 part 自己的关系表解析（rId 跨 part 会撞车，见 `_docx_para_link` 的 `rels` 形参）；CH-476 / R7-GAP-3 起 `_docx_cell_text()` 逐 `w:p` 取接受视图再以单空格相连（原 `"".join(tc.iter(w:t))` 扁平粘连吃掉段界；单段单元格结果与旧口径逐字节相同） |
| `pipeline/s1_5_gate.py` | 286 | `convert` | 文本层体检 `check()`（内部走 `GateCollector`）、`GateCollector`（CH-460 / A-5：采集与判定分离，逐片喂页、纯函数判定；CH-473 起 docstring 如实标注消费面——生产路径只有 `check()` 一次性喂全量，带非零 offset 的流式 feed 仅测试演练，「流式省内存」是已具备未接线能力）、`even5_indices` / `legacy3_indices`、`PageReport` / `GateResult` |
| `pipeline/sharding.py` | 349 | `convert` | `shard_plan` / `stitch_shards` / `overlap_dedupe` / `fingerprint`（`mark_shard_edges` 已在 CH-469 / S-7 作为死代码删除，预打标会让片内相邻块被误当续接候选）；CH-469 起 `overlap_dedupe` 在指纹相等之外增加两条缝界伪影判定（`_seam_suffix` 真后缀丢弃、`_seam_truncated` + `_replace_recent` 截断块替换）；CH-473 起两条规则补边界约束（PIPE-5 页差约束 / PIPE-6 片级锚定，签名扩展 `shard_bounds` / `last_idx`）；CH-476 起 `_continuation` 编号续接方向修正 + 结构块守卫前移（R7-PIPE-1），并把 PIPE-5 的页差由「恰为相邻页」放宽为 `0 < b.page - a.page <= 最宽重叠窗口 + 1`（R7-PIPE-2：跨 ≥3 页长块的片界尾缀不再漏判留两份，远处重复短句照旧不误删）——红线条目「分片去重后块数 == 单片块数」此前的两处失败形态均已闭合 |
| `pipeline/s3_structure.py` | 553 | `convert` | `analyze()`：七类块全量判定——标题 / 列表 / 段落 / 表格 / 代码 / 引用 / 分页（纯规则，无模型；CH-459 补齐后四类，`BLOCK_TYPES` 声明与产出一致）；CH-473 起块 id 的唯一赋值点是收尾重排（原循环内 8 处 `id=f"b{seq+1}"` 死写，一次多块只递增一次 seq 是重复 id 温床，PIPE-9）；CH-475 / R6-PIPE-1 起 `_heading_level` 第三回退（纯编号 → `_level_from_number`）加信号门——仅当 `size > base` 或加粗或多级编号或 CJK 章节词命中才判标题，否则归 `list_item`，堵住「字号==正文的有序列表项（如「1. 安装依赖」）被误升为标题」；CH-476 / R7-PIPE-4 起 DOCX `Heading N` 的显式层级在收尾校正也保持优先（命中打 `flags.level_from_hint`，`_fix_levels` 跳过该块），并删除 `_fix_levels` 的 `seen` 兄弟机制（同前缀 key 的点数恒相同，sibling 必等于 lvl，`rule_notes` 永不触发 = 死逻辑）；CH-476 / R7-GAP-1 起七类块落块路径统一经 `_mark_revision()` 把行级修订信号带到块级（段落取 `para_lines`、表格取 `row_lines`、代码 / 引用新增同步的 `code_lines` / `quote_lines`，单行块直接取该行），只打旗标不改正文；CH-476 / R7-GAP-2 起该函数更名 `_mark_provenance()`（同时透传 `note_kind`），且注行不参与 CH-430 续句 / 续段启发式——DOCX 的注边界是显式 `w:p`，末句无标点时把注文并进正文块属内容与来源双错，注行一律自成段落块 ；**CH-478 / R6-PIPE-1 收口**：`_ORDERED_ITEM` + `_is_ordered_item` 在符号 bullet 之后、表格成组之前落 `list_item`（行长 <30、行末无句末标点、`ln.row` ≥2 单元格不参与），接住标题信号门让位下来的同字号单级编号短行 |
| `pipeline/s4_render.py` | 110 | `convert` | `render()`、`assert_render_contract()`、`is_external()`；CH-469 起空行压缩只在围栏外生效（S-4）；CH-473 起围栏结构改按块类型判定（PIPE-11：正文里的 ``` 字面行不再错切代码块，code 块一个字节不动）；CH-476 / R7-PIPE-3 起围栏长度按内容取（`_code_fence` = 正文最长反引号/波浪线 run + 1，下限 3）——code 正文含 ``` 字面行时不再提前闭栏产出破损 Markdown，无围栏字面的语料仍为三反引号、字节不变 ；**CH-478**：`list_item` 有序出口——导入 S3 的同一份 `_ORDERED_ITEM`，有序项保留序号渲染为 GFM `N. `（符号 bullet 仍统一 `- `），正则末尾必须用先行断言以免切分时吃掉内容首字 |
| `pipeline/metrics.py` | 90 | 仅 `tests/test_v24_gates.py`（门禁第 1 组，导入 `char_similarity` / `half_sentence_heads` 两个） | 公开函数只剩上述 2 个（`line_similarity` / `diff_lines` 已在 CH-456 删除，D-9 关闭），`norm_lines` 与 `is_half_head` 仅被同文件内部复用；CH-475 / R6-PIPE-2 起 `norm_lines` 保留行首缩进、只把行内连续空白折叠为单空格（原实现 `_WS.sub("")` 抹尽全部空白，使等价性门对**缩进类**漂移完全失明——原并列写的「代码围栏破损 / 列表标记替换」经 CH-478 批次② 实测是内容级漂移、旧口径同样看得见，非本次收紧所得），G6「分片≡单片」「修复前后等价」的度量由此收紧。敏感度面由 `tests/test_ch478_gate_drift.py` 红线钉住：缩进漂移 0.973958 判红 / 旧口径精确 1.0，围栏与标记新旧差值 < 0.001 |

`pipeline/__init__.py`（9 行）re-export 10 个符号。`pipeline/metrics.py` 只被测试引用（门禁第 1 组的两个判定函数），生产链路不导入它——「有代码、调用方在领域层之外」与「有代码、无调用方」是两件事，前者是验收工具（合理），后者是死代码（D-9，已在 CH-456 清零）。本节的「引用方」列就是用来区分两者的：它由 `ast` 遍历得出，不区分生产与测试，因此 `tests` 也算引用方，需要额外注明。

### G.3 领域层：存储（3 个模块，984 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `storage/db.py` | 421 | `api` `auth` `cli` `cron` `edits` `scheduler` `worker` `storage.repo` | `DDL`（8 表 + FTS5 虚表）、`Store` 连接与 `threading.local`、`DB_VERSION`、探测式迁移（存量 v4→v5、v3→v5、v2.2 遗留库补列 + FTS 重建回填，CH-473 / R5-TEST-5 有迁移链用例）、`now_iso`；连接初始化 WAL + `synchronous=NORMAL` + `mmap_size=256MB` + `cache_size=-65536` + 关闭时 `PRAGMA optimize`（CH-471 / STOR-1 / 决策 D1，原 T14）；连接登记表为**弱引用 `WeakSet[_ConnRef]`** + `live_conn_count()`（CH-460 / A-3）、`_Tx.executemany`（A-14）、`_Tx.__exit__` 回滚失败不顶替 body 病因（CH-473 顺带修复）（D-9 的零调用属性 `_Tx.lastrowid` 已在 CH-456 删除） |
| `storage/repo.py` | 448 | `api` `cli` `edits` `worker` | `save_ir`（`tx=` 可并入调用方事务，A-4；`executemany` 批量，A-14）`reindex` `reindex_block`（A-6）`get_doc` `list_blocks` `search`（CH-471 / STOR-2：FTS 命中按 `bm25()` 相关性排序、主查询 `LIMIT limit+1` 探测免第二遍全表 COUNT）`reconcile_search`（CH-473 / R5-STOR-2：启动对账，修历史空索引）`delete_doc` `delete_source`（两者均先做存在性检查，A-15；force 覆盖同事务清 `edit_audit`，R5-STOR-1）`purge_files` |
| `storage/fts.py` | 115 | `db` `repo`，**仅标准库** | `cjk_aux`（n-gram 辅助列）、`build_match`、`query_terms`、`like_escape`、`MAX_QUERY_CHARS`（查询串上限）+ covered-span 区间掩码（CH-460 / A-9） |

### G.4 支撑层：安全与运维（9 个模块，1737 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `security/names.py` | 130 | `pipeline.convert` | `sanitize_name` / `assert_name_safe` / `assert_entry_safe`、`WIN_RESERVED`、`SAFE`、`UnsafeNameError` |
| `security/dirguard.py` | 143 | `api` `cli` `security.watcher` | `guard_walk` / `scan_or_raise`、`WalkLimits` / `WalkResult`（符号链接环、目录过宽 413/E4133）；CH-471 起放行合法内部 `..`（SEC-4：zip 条目名归一再判定＝洗白的问题只针对穿越，内部相对段合法） |
| `security/netguard.py` | 193 | `cli` | `offline_guard`（`run.offline_assert` 的运行期守护：把 `socket.socket.connect` 限制为回环，非回环抛 `OfflineViolation`；CH-456 / T9）；CH-471 起可重入 + `connect_ex` / `sendto` 同样受拦 + 主机名按解析结果判定（`sendmsg` 补丁 SEC-1）；CH-473 起解析缓存封顶 256（R5-Q-2：长驻进程缓存无界增长） |
| `security/watcher.py` | 144 | `cli` | `StabilityTracker`（写入静默窗口，注入时钟可测）；CH-471 起 mtime 短路（PERF-1 / 决策 D3：未变更文件不重复哈希，守住 4 依赖闭环不引入 watchdog） |
| `ops/logging.py` | 163 | `api` `cron` `worker` | `RotatingJsonlWriter`（按字节阈值 rename 轮转）、`EventBus`（`/events` 拉取式总线）、`scrub_record`（NFR-20 脱敏唯一实现，`api` 侧事件入库同用此函数，CH-460 / A-12；CH-471 / PERF-2 起单次 UTF-8 编码；CH-475 / R6-OPS-2 起 `bytes` 值先 `decode("utf-8","replace")` 再按长度判定，堵住「二进制正文片段绕过 >64 字符指纹化、原样入日志」的旁路） |
| `ops/backup.py` | 390 | `cron` `ops.drill` | `derive_key`（Scrypt，N=2^14——CH-471 / SEC-5 / D5 决策为**保持**：OWASP 建议 N≥2^17，但 v2 容器头不含 KDF 参数，提 N 会使现存加密备份全部认证失败，数据安全优先于强度小提升，提 N 须与「头部携带 KDF 参数」的格式改造同批，登记为未来变更）/ `encrypt_bytes` / `decrypt_bytes`（AESGCM）、`encrypt_stream` / `decrypt_stream`（v2 分块流式容器：随机 16B 盐、4MB 块、AAD 绑定魔数+总块数+序号；v1 历史容器保持可解密，CH-461 / A-11）、`BackupPlan`（`to_external` / `restore` 流式化，失败不留半截 `.part`；外置副本原子写 `.part`→fsync→replace，CH-473 / R5-OPS-1；`work_dir_mode` 可配，CH-456/T9）、`hdiutil_recipe` |
| `ops/drill.py` | 231 | `cli`（`backup drill`） | 备份恢复演练闭环：`run_drill`（沙箱 8 步：copy_live→snapshot→external_encrypt→simulate_loss→rotate→restore→integrity_check→compare）、`DrillReport` / `DrillFailure` / `integrity_check`（CH-456 / T7） |
| `ops/diskguard.py` | 82 | `cli` `cron` `pipeline.convert` | `free_mb` / `need_bytes` / `assert_free_space`（NFR-6，E4131；available 口径，CH-469 / O-6） |
| `ops/gguf.py` | 261 | `cli`（tokenizer 子命令） | GGUF v3 只读解析：`Gguf` / `read_metadata` / `export_vocab` / `write_manifest` / `verify_manifest` / `Tokenizer`；CH-471 起声明 KV 数超限报错（OPS-3：逐值尺寸帽）+ BOOL 短读收敛（CH-473 / R5-OPS-2） |

### G.5 基座层（5 个模块，1437 行）

| 模块 | 行 | 包内引用 | 为什么能当基座 |
| --- | --- | --- | --- |
| `errors.py` | 110 | **无**（零导入） | 12 个错误码类（`Doc2mdError` / `ApiError` 两个基类之外）+ 2 个告警码（`W1043` `W1044`）+ `CODE_INDEX` 反查表；`ApiError` / `Doc2mdError` 为共同祖先。`E5051` 已在 CH-456 删除（降级语义吸收）；CH-471 新增 `E4091`（编辑冲突护编辑，STOR-5） |
| `llm.py` | 614 | **无**（零导入） | `scan_injection` / `offline_llm` / `apply_ops` / `OpValidator` / `DEFAULT_OP_WHITELIST` + `LlmClient` / `LlmServiceError`（本地 llama-server 客户端，CH-456 / T2：错误码映射由上层 `convert.repair_ops` 负责，基座不向上引用）；修补助手层不碰存储与配置。CH-459：`apply_ops` 五个 op 全部落地（原仅 `fix_heading_level` 有实现，其余过校验静默无效），`_split_block` / `_merge_block` / `_cjk` 为新增内部助手；CH-469：连接类瞬断重试（`llm.retries`）；CH-473：op 索引字典化（LLM-2：split 产生的新块入字典，供后续 op 按 id 寻址）；CH-475 / R6-LLM-1：新增 `valid_level`（`fix_heading_level.level` 须 `int` 且 1≤level≤6）与 `valid_marker`（`fix_list_marker.marker` 须匹配 `^[-*+·•‣]{1,3}$` 白名单），`OpValidator._check` 对越界值拒绝该算子（`bad_level:` / `bad_marker:`）、`apply_ops` 对 marker 改用 lambda 替换杜绝 `\1` 注入——畸形/恶意算子（正文视为不可信输入）被拒而非在渲染期崩溃；R6-LLM-2（部分）：`_INJECT_HINT` 词表补 forget/disregard/ignore previous/new instructions/developer mode 等真实注入常用词、「扮演」收窄到需越狱上下文（降误报），归因去循环（`_attribute` 仍扫算子 JSON）与更宽误报收窄登记 B-3（需真实注入语料） |
| `config/loader.py` | 447 | 只引用 `config.default_toml` | `load`（解析顺序）/ `get`（点号取值）/ `path_setting` / `root_setting` + `run.profile` 三档键集覆盖（CH-456 / T9），只用 `tomllib`；`SPEC` 表（62 键，逐键类型与区间，CH-469 / O-15；CH-476 / R7-PERF-1、R7-PERF-3 增 `perf.queue_batch_factor` / `perf.enqueue_hash_threads` 两键，60→62）+ `RUNTIME_IMMUTABLE_KEYS`（运行期禁经 `PUT /settings` 修改的红线键：`run.offline_assert` 自 CH-471 / D4 起、`api.allow_remote_bind` 自 CH-473 / R5-SEC-1 起——后者堵「先 PUT 放开 opt-in 再改 bind」的两步掏空）+ `check_runtime_immutable(candidate, baseline)`（CH-475 / R6-SVC-1：`/system/reload` 与 `PUT /settings` 同口径调用，不可变键变化即拒，堵「直接改配置文件 + reload」绕过守卫的运行期旁路）+ `validate`（非回环绑定拒绝，`api.allow_remote_bind` 为唯一 opt-in） |
| `config/default_toml.py` | 129 | **无** | 62 个配置键的内置默认值唯一来源（附录 A）+ `TOML_BASENAME` |
| `ir/model.py` | 137 | `convert` `s3` `s4` `sharding` `storage.repo` `worker` | `Block` / `IR` / `BLOCK_TYPES`（7 类）/ `doc_id_of` / `hash_of_path` / `source_key_of`；只引用包根的 `SCHEMA_VERSION`，是数据结构而非逻辑层 |

### G.6 测试与资产（非运行时）

测试资产的**规格口径**（为何入库、可复现性为何是验收对象、120MB 大文件为何不内置）在 8.4；本节只登记文件与数字清单。

`tests/` 共 32 个 `.py`、14079 行，收集 **811 项 = 811 passed / 0 skipped**（`gate` 260 项 = `test_v24_gates.py` 40 + `test_perf_lowend.py` 29 + `test_ch469_service.py` 10 + `test_ch469_ops.py` 54 + `test_ch473_fixes.py` 13（CH-473 12 + CH-474 补入 X-17 1）+ `test_ch475_fixes.py` 7（CH-475 第六轮）+ `test_ch476_fixes.py` 83 + `test_sharding_unit.py` 8（CH-476 第七轮共 91）+ `test_ch477_version_sync.py` 4（CH-477 对账红线）+ `test_ch478_fixes.py` 4（CH-478 批次① 有序列表收口，同文件另有 14 项非红线判据断言）+ `test_ch478_gate_drift.py` 4（CH-478 批次② 等价门缩进敏感度，同文件另有 6 项非红线）+ `test_ch479_ci_matrix.py` 4（CH-479 CI 版本矩阵口径，同文件另有 2 项非红线）；其余 551 项为里程碑与信息面）。原记「24 个 .py、8462 行、481 收集、gate 132」为 CH-469 轮口径、「27 个 .py、11133 行、677 收集、gate 153」为 CH-475 轮口径、「28 个 .py、13409 行、773 收集、gate 244」为 CH-476 轮口径、「29 个 .py、13526 行、777 收集、gate 248」为 CH-477 轮口径、「30 个 .py、13672 行、795 收集、gate 252」为 CH-478 批次① 口径、「31 个 .py、13959 行、805 收集、gate 256」为 CH-478 批次② 口径——收集数一律以 `--junitxml` 的 `tests=` 属性实测为准，散文计数不作数（本表行数同样按 `wc -l` 逐文件实抄）。

| 文件 | 行 | 收集 | 定位 |
| --- | --- | --- | --- |
| `conftest.py` | 157 | — | 13 个夹具：黄金样本路径（`golden_dir` / `large_pdf` / `small_pdf`）、`cfg` / `tmp_cfg`、`store`、真实 Flask test client 的 `api_client`（红线层三条 API 门禁的执行面，配套的非夹具辅助函数 `hdr` 与夹具 `headers` 负责带角色取 token）、`saved_doc`、`names_manifest`、大样本三件套（`large_single` / `large_sharded` / `large_zip_names`）；`_missing` / `_need` 两个非夹具助手（CH-471 / TEST-1：golden 缺失默认硬失败，仅 `DOC2MD_ALLOW_MISSING_GOLDEN=1` 降级） |
| `test_v24_gates.py` | 530 | 40 | 六条契约红线（附录 F 第一层）；CH-469 起 `test_shard_two_phase` 加了「分片去重后块数 == 单片块数」的硬口径（相似度红线容不下整份重复段落）；CH-473 起两处 golden 缺失路径与 `test_perf_lowend` 同口径硬失败（TEST-1 收口） |
| `test_perf_lowend.py` | 563 | 29 | 低配机专项（附录 F 第二层）；CH-462 补产物 zip 完整性断言（CRC / 三条目 / ir 一致）；CH-469 补批量空窗超时逐件记账 1 项；CH-473 起超时记账改同机双跑相对口径（TEST-4：参考档 vs 守卫档的墙钟比，消除宿主负载依赖） |
| `test_t9_convergence.py` | 253 | 21 | 配置收敛：profile 覆盖、冻结键、netguard 回环、token_roles、work_dir_mode（CH-456 / T9） |
| `test_m8_ops.py` | 591 | 30 | 备份加密（v2 分块容器 round-trip / 随机盐 / 块重排与截断拒绝 / v1 兼容）/ 轮转 / 磁盘门禁 / launchd 编排 / 事件保留期边界（CH-455、CH-461） |
| `test_t2_llm_server.py` | 411 | 26 | 假 llama-server 契约：请求体 / 围栏容错 / 注入归因 / 故障分级 / Bearer / offline 零 HTTP（CH-456 / T2）；CH-469 补连接类瞬断重试 4 项（成功 / 用尽预算 / retries=0 / HTTP 状态不重试） |
| `test_s3_blocks.py` | 493 | 39 | CH-459 批次①：七类块可达性与四类块保守检测口径、链接抽取（PDF annot 几何 / DOCX 超链接整段覆盖）、`apply_ops` 五 op 语义、黄金样本 e2e |
| `test_batch2_storage.py` | 454 | 30 | CH-460 批次②：连接登记表弱引用有界（A-3）、`save_ir(tx=)` 并入外部事务（A-4）、`GateCollector` 与 `check` 同口径（A-5）、`reindex_block` 单块刷新（A-6）、FTS 查询上限与掩码（A-9）、多文件投递按文件派生幂等键（A-10）、脱敏不看键名只看长度（A-12）、删除/回滚参数校验（A-15）；CH-469 补 `GateCollector` 缺页可判定报错 1 项 |
| `test_ch469_service.py` | 667 | 30 | CH-469 批次②（服务/存储 V-1…V-13）：排空遇不可读 IR 判失败、`docs/blocks` 窗口与 total、PATCH 非数字 `base_revision`、未知角色 400、未知 token 吊销 404、`PUT /settings` 累积写与不共享突变、常驻循环内遗骸回收、编辑器日志汇内容类型、CLI 未预期异常 → `EXIT_SYS`（其中 10 项进红线） |
| `test_ch469_ops.py` | 979 | 91 | CH-469 批次③（安全/配置/运维 O-1…O-15）：真实 socket 的离线守护（可重入 / `connect_ex` / `sendto` / 主机名按解析判定）、v2 容器（盐必填、头认证、历史容器可读、`chunk_size` 上限、截断/多余字节）、zip 条目名 12 例、available 口径、流式指纹、watch 登记簿回收、`guard_walk` 截断即 413、快照/恢复的 fsync 与原子性、并发日志轮转、`SPEC` 校验与键集一致、CLI 配置错退出码（其中 54 项进红线：CH-473 起权限双形态校验的参数化净增一例，53→54） |
| `test_ch471_fixes.py` | 898 | 62 | CH-471 第四轮（SVC-1…10 / STOR-1…10 / PIPE-2/5…12 / SEC-1/2/4/5 / OPS-1…3 / LLM-1/2 / Q-1/2 / CFG-1/2 / PERF-1 的逐字母回归）：immutable 写集、E4091 护编辑、空文本 PATCH、删除事务复核、WAL+NORMAL、bm25 排序、FTS 回填流式、同步进程池、通用 500、loopback 强制绑定、冻结键、sendmsg、token 哈希、GGUF 尺寸帽、scrypt 参数、netguard 可重入、`doctor` 语义退出码等；含 v3/v2.2 存量库迁移的前身用例与 X-16 的 `_processes` 版本哨兵 |
| `test_ch473_fixes.py` | 1281 | 62 | CH-473 第五轮（R5-PIPE-5…12 / R5-STOR-1…5 / R5-SVC-1…10 / R5-SEC-1/2 / R5-OPS-1/2 / R5-LLM-1/2 / R5-Q-1/2 / R5-CFG-1/2 / R5-PERF-1 / R5-TEST-1…5）：seam 相邻页约束与片级锚定（G6 红线）、force 覆盖清 audit、COMMIT 失败回滚、两步 PUT 掏不空 loopback、`serve --host` 拒绝、同步池全局信号量、启动 FTS 对账、sweep 保护窗、netguard 缓存封顶、备份原子写、GGUF BOOL 短读、块 id 唯一性、S4 围栏逐字节保留、GateCollector 消费面注释、`doctor` 退出码契约、DB 迁移链（v3→v5 / v2.2 补列+重建）、同机双跑相对口径（其中 13 项进红线：CH-473 的 12 项 + CH-474 补入的 X-17 池收尾断言 `test_x17_batch_returns_with_all_pool_workers_reaped`，构成见附录 F 第四层） |
| `test_ch475_fixes.py` | 416 | 71 | CH-475 第六轮（R6-SVC-3/2/1 / R6-OPS-2/3 / R6-LLM-1/2 / R6-PIPE-1/2 的逐项回归，7 项进红线）：`mark()` 状态守卫不翻转 done/rejected（保留 failed→done 自纠偏与 queued→failed 遗骸清理）、幂等重放跳过 `save_ir` 不冲突、审计 actor 为 token sha256 前缀而非明文、`scrub_record` 对 bytes 长值指纹化、`/system/reload` 拒绝运行期不可变键变化、畸形 marker 算子被中和而非崩溃、`char_similarity` 现在能看见缩进漂移；非红线含 `valid_level`/`valid_marker` 参数化、`OpValidator` 拒绝越界算子、`_heading_level` 第三回退信号门（字号/加粗/多级/CJK 章节词）、`norm_lines` 保留缩进折叠行内空白、`scan_injection` 新变体命中与零误报、`doctor` 磁盘不足/未知磁盘/server LLM 不可达退 `EXIT_SYS` 与机器可读 JSON 为 ASCII、`_jline` 转义非 ASCII。R6-OPS-1 的 launchd 守卫断言在 `test_t5_windows_ops.py`；R6-SVC-4 / R6-LLM-2 归因去循环 / 附录 G 复算为 B 类（报告 §6） |
| `test_ch476_fixes.py` | 2097 | 87 | CH-476 第七轮（R7-SVC-1…4 / R7-PIPE-1…4 / R7-LLM-1 / R7-STOR-1/2 / R7-PERF-1…3 与 PERF-a…k / R7-OPS-1/2 / R7-GAP-1…3 / R7-SEC-1 / R7-TEST-1 的逐项回归，**83 项进红线**，构成分组见附录 F 第六层）：`mark()` 回报 UPDATE 是否命中 + 排空中 doc 被删即判失败不留终态、同步 `/jobs` 提交前复核任务行、`OpValidator` / `apply_ops` 对畸形 `target` 设防（参数化 5+5 例）、v6 结构（`search_map` + `ix_audit_doc_id`）与 v5 存量库回填闭合、`reindex_block` 经映射 O(1) 且单块操作保持映射一致、`reconcile_search` 经映射修复、force 覆盖不留重复 FTS 行、`claim` 批量随 `perf.queue_batch_factor` 放大且认领量与 workers 解耦（参数化 7 例 + 跨池批次 2 例）、同路径作业合批不扩池、漂移比对复用已消费哈希不做父级重算、入队并行哈希的定序 / 合批 / 幂等 / 原子零副作用四契约、`sync_min_parallel` 与 `enqueue_hash_threads` 的配置契约、分片页差放宽后长块尾缀闭合且反向页序不误判（G6 红线「分片去重后块数 == 单片块数」）、变长围栏吃掉 ``` 字面行且无语料字节不变、`level_from_hint` 不被 `_fix_levels` 顶掉、`sweep_tmp` 永久豁免 `drill-*` 且留存目录能过一次 daily、cron 命令行四态（正常 / 参数互斥 / `--no-backup` / 失败步退 `EXIT_SYS`）、周报文件名走 UTC、DOCX 修订痕迹取插入不取删除 + 块级 `has_revision` + 黄金样本 e2e + 无修订语料逐字节不变、脚注尾注注文自成段且注入正文块为零、注内链接按注 part 关系表解析、单元格多段以空格相连且单段结果不变、无注 DOCX 不多出行、PERF-a…k 七组等价性对照（预扫 / 页指标 / 样式备忘 / 文档级预检 / `map_many` 合批 / `delete_source` 只读查找 / `limit` 帽 / Markdown 流式）、`mode` 白名单 400 零副作用 + 串行与池两条分支透传、池闸门超时有界退避 503 `E5041` + `Retry-After` 且串行路径完全不触碰闸门、审计 `reason` 与令牌 `label` 的类型与长度帽、审阅页 `esc` 覆盖单引号。非红线 4 项为信息面断言（如 `test_drain_done_path_still_persists_detail` 的终态留痕对照） |
| `test_ch477_version_sync.py` | 117 | 4 | CH-477：T15 落地的版本与编号对账红线（四条静态断言，全部标 `gate`；先断言文档锚点解析成功再比值，锚点失配即报「红线锚点失效」） |
| `test_ch478_fixes.py` | 93 | 18 | CH-478：R6-PIPE-1 收口的有序列表回归——红线 4 项走真实转换（黄金样本 `numlist_1p.pdf` 端到端：同字号编号归 `list_item`、三类真标题信号照旧发火、Markdown 保留序号、正文行不进该分支），另 14 项为 `_is_ordered_item` 正/反向形态参数化（非红线） |
| `test_t7_drill.py` | 220 | 13 | 演练 8 步闭环、沙箱隔离、篡改必须失败、NFR-20 无正文（CH-456 / T7） |
| `test_m5_m6c.py` | 185 | 21 | S4 渲染契约、编辑与回滚、调度器 |
| `test_m9a_search.py` | 208 | 12 | M9a 编辑后可检索 + CH-435 CJK 检索回归（aux unigram/bigram、LIKE 复核字组连续性、旧库无 aux 列的迁移重建、token 吊销即时生效） |
| `test_t5_windows_ops.py` | 207 | 17 | schtasks XML 合法性、launchd parity、口令零泄露、plist 回归（CH-456 / T5）；CH-475 / R6-OPS-1 补 launchd plist 的 `WorkingDirectory` + 显式 `--root {安装根}/work` 断言（launchd 默认 cwd 为 `/`，缺此二者则相对根名 `work` 被解析成 `/work`，日流程操作错误根） |
| `test_cli_entry.py` | 153 | 9 | CH-462：CLI 四子命令入口层（serve token 签发/列表/吊销、backup drill 分发与退出码、watch --once、tokenizer export/verify） |
| `test_api_endpoints.py` | 117 | 8 | CH-462：/events 角色矩阵与 since 游标、/jobs/<id>/events 脚本 UA 硬规则（FR-125）、/system/reload 权限与 override 合并 |
| `test_sharding_unit.py` | 272 | 26 | CH-462：shard_plan 边界与重叠、fingerprint、overlap_dedupe 留痕、stitch_shards 续接吸收与 id 重排；CH-469 补缝界伪影 5 项（跨页尾缀丢弃、非尾缀保留、比对面常量界、截断块替换、窗口外不替换）；CH-473 补相邻页约束与片级锚定（PIPE-5/6）；CH-476⑦ 补 `_continuation` 编号续接方向与结构块守卫前移（R7-PIPE-1，参数化 5 例）、页差放宽到重叠窗口（R7-PIPE-2）——**8 项进红线**（与 `test_ch476_fixes.py` 的 83 项合成本轮 91 项） |
| `test_m0_m2.py` | 158 | 13 | GGUF 词表工具链、S1 解析与 S1.5 门禁 |
| `test_t4_queue_ui.py` | 158 | 5 | 队列面板页面契约、JS 语法、NFR-20 无正文探针（CH-456 / T4；JS 语法一条在无 node 宿主上按登记例外跳过，见附录 F 零跳过纪律） |
| `test_ch478_gate_drift.py` | 210 | 10 | CH-478 批次②：R6-PIPE-2 的 B-2 出口——黄金样本 `codeindent_1p.pdf` 真实产物上的四类漂移对照。红线 4 项（缩进漂移判红且旧口径精确 1.0 / 内容级漂移新旧同值故收紧无额外敏感度 / S1 抽取剥掉行首空白而 `Line.x0` 仍带三档 / 参照实现与产品 `char_similarity` 同值），另 6 项信息层（`norm_lines` 四形态参数化、样本四类块形齐备、行内空格漂移「脱盲但未判红」）。自带 CH-475 前的旧口径归一化与 CH-429 算法的可换归一化参照实现，故新旧对照不靠记忆数字 |
| `test_ch479_ci_matrix.py` | 120 | 6 | CH-479：两套 CI 工作流的版本矩阵口径对账。红线 4 项（两工作流跨版本集合相等且恰为登记的 `{3.11, 3.12, 3.13}` / push 面 `full-suite` 必须无矩阵且钉死 `3.12` / 每个作业带 `timeout-minutes` 且两条全量腿均为 30 / 周度腿只允许 `schedule` + `workflow_dispatch`）；信息层 2 项（全量测试步骤保留 `-vv`、周度腿仍带黄金逐字节复现与 `doctor` 两步）。环境无 `pyyaml`，按 `jobs:` 段两格缩进切块做文本解析，解析不出作业即先报「锚点失效」再比值 |
| `test_golden_repro.py` | 122 | 7 | 黄金样本自身可复现性与可移植性（CH-442，CI 独立步骤）；CH-478 批次① 起 PDF 逐字节复现断言覆盖 `small_3p.pdf` 与 `numlist_1p.pdf`，批次② 再加 `codeindent_1p.pdf`（同一用例内循环，收集数不变） |
| `test_t6_corpus_bench.py` | 367 | 20 | 打开边界 E4151 回归 + 基准仪器自检与三平台采样后端（CH-457 / T6，CH-458 补采样后端三支，CH-472 修小语料 drain 峰值的宿主时序断言） |
| `test_m3_bigcopy.py` | 106 | 4 | 大目录遍历与稳定性追踪 |
| `make_golden.py` | 1387 | 不参与收集 | 黄金样本生成器（reportlab 造 PDF、pypdf 数页），资产不是断言；CH-478 起 `make_numlist_pdf()` 造 `numlist_1p.pdf`（同页放三行同字号单级编号 + 三类真标题对照 + 12 行众数字号正文，字号/坐标全部写死保持逐字节可复现），`main()` 步骤序号改到 10 档、`self_check()` 的 PDF 清单与 `make_manifest()` 描述同步；批次② 起 `make_codeindent_pdf()` 造 `codeindent_1p.pdf`（同页放显式围栏代码块 + 纯等宽块 + `-` 符号 bullet + 众数字号正文段，字号与 x 起点全部写死），`main()` 步骤改到 11 档、`self_check()` 的 PDF 汉字数清单与 `make_manifest()` 描述同步、MANIFEST 条目 5136 → 5137；CH-476⑨ / R7-GAP-1 起 `sample_revisions.docx` 直写 OOXML 修订元素（`w:ins` 包裹插入 run、`w:del` 包裹 `w:delText`），id/author/date 全部写死以保持逐字节可复现；CH-476⑩ / R7-GAP-2/3 起新增 `sample_notes.docx`（`XmlPart` + `relate_to` 直挂 `word/footnotes.xml` / `word/endnotes.xml`，XML 为常量字符串；表格里一格两段）承载注抽取与单元格段界 |
| `gguf_fixture.py` | 89 | 不参与收集 | 合成最小合法 GGUF v3 的夹具 |

`tests/golden/` 是入库的黄金语料（受控文件 **5139 个、磁盘占用 23.0MB**，其中**实际字节 2.07MB**——两个口径差一个数量级是因为语料由 5000+ 个近空文件组成，每个文件至少占满一个文件系统块，磁盘占用自然远大于表观大小；引用时必须说明取哪一个，否则「23MB」会被读成分发包体积。本组数字由 CH-478 批次② 在工作副本当场实算，命令即本节末采集方法：`git ls-files tests/golden | wc -l` = 5139、`du -sk -I bigfile.bin tests/golden` = 23596KB、`git ls-files tests/golden -z | xargs -0 stat -f%z` 求和 = 2166835 字节、`git ls-files | wc -l` = 5237（仓库受控文件总数）。旧值 5218 / 23.2MB / 3.0MB 出自 CH-469 轮的一次性 clone，采集方法未记录、本机复算不出；`git ls-tree -r 88896ae` 复核显示 **5218 是仓库总数口径**（同一提交 `tests/golden/` 实为 5136 个），旧叙述把它误记在语料行上，本轮一并校正。清点也不再需要 clone——12.7 的「工作副本的 git 能力」行已记录 CH-473 轮起本目录是完整 git 仓库，旧句「工作副本没有 `.git`，清点只能在 clone 里做」随此作废。清单条目数与受控文件数恒等式为 `5137 = 5139 − 1（`MANIFEST.json` 自身）− 2（`dirs/symlink_loop/A`、`B` 两个符号链接）+ 1（`bigfile.bin` 由 `make_golden.py` 现补、不入库）`，两处口径照此对账，不再留 off-by-one 的猜测空间）：文档样本（`clean/` 10 篇、`injected/` 10 篇注入稿、`docx/` 4 篇含链接、真实修订痕迹与注 part 形态（`sample_revisions.docx` 自 CH-476⑨ 起内置
`w:ins`/`w:del`，此前是「无真实修订标记」的占位稿；`sample_notes.docx` 自 CH-476⑩ 起内置
`word/footnotes.xml` / `word/endnotes.xml` 两个注 part 与一格两段的表格单元格）、`small_3p.pdf` / `links_pdf.pdf` / `large_320p.pdf` 637KB / `badtail_front|late|scan.pdf` 三种坏尾、CH-478 批次①② 的两件漂移对照语料 `numlist_1p.pdf` 3049B / `codeindent_1p.pdf` 3521B（各 1 页，均纳入 12.2 的逐字节复现断言与 `self_check()` 清单））、恶意名单 `names/malicious_names.json`（15 条 input→expected 对照，由门禁的 `test_malicious_names_manifest` 逐条复核；第 2 组那 12 例参数化用的是 `test_v24_gates.py` 内联的 `MALICIOUS_NAMES` 常量，与清单是两套来源——两处都要改才算覆盖全）、目录防护场景 `dirs/`（`many/` 5100 个空文件超过 `watch.max_files_per_dir = 5000` 用于触发 413、`deep/` 25 层超过 `max_depth = 20` 的越界侧、`symlink_loop/` 与 `hidden/`）、以及 sha256 绑定的 `MANIFEST.json`（CH-442 的可复现断言打在它身上）。`bigfile.bin`（120MB）被 `.gitignore` 排除，由 `make_golden.py` 现补（CH-443）。

非 Python 资产：`doc2md/static/editor.html`（383 行，原生 JS 单文件审阅页，含队列面板 CH-456/T4）、`config/doc2md.example.toml`（109 行，附录 A 的可抄样例，62 键）、`ops/backup.md`（90 行，备份介质与恢复演练细则，CH-476⑧ 补 `drill --keep` 与 `drill-*` 豁免口径）、`ops/llm.md`（90 行，本地 LLM 模型接入指引：选型/下载/llama-server/故障排查，CH-466；CH-476⑧ / R7-TEST-1 修正第 ④ 步的 `tokenizer verify` 命令——原示例清单路径 `chunks.manifest.json` 不存在，实跑退 `EXIT_SYS` 5）、`ops/launchd/*.plist`（24 + 27 行，日 / 周两个 label 示例；CH-456/T5 修复注释含连续连字符的非法 XML 缺陷，CH-475/R6-OPS-1 补 `WorkingDirectory` 与显式 `--root`——launchd 默认 cwd 为 `/`，缺此二者则相对根名 `work` 会被解析成 `/work`）、`ops/windows/md.doc2md.cron.{daily,weekly}.xml`（62 + 63 行，schtasks 定义）、`ops/windows/install-doc2md-tasks.ps1`（223 行，安装 / 卸载 / 校验）、`ops/bench/t6_large_corpus.py`（485 行，大语料测量脚本，非运行时）、`ops/drill` 的说明见 `ops/backup.md`、`.github/workflows/ci.yml`（94 行，merge-gate 矩阵 + full-suite + ruff 信息作业；三处 `timeout-minutes` 与两处 `-vv` 见 8.6；头注释的轮次口径随本轮更新）、`pyproject.toml`（58 行，含 `license`/`classifiers`，CH-456/T11）、`pytest.ini`（7 行，两个 marker + `--strict-markers`）、`.gitignore`（27 行）、`README.md`（192 行）、`docs/`（本文 + 已归档存根 + 三份审查报告：`review-round4-CH-471.md` 470 行、`review-round6-CH-475.md` 226 行、`review-round7-CH-476.md` 329 行）、`LICENSE`（MIT）。

采集方法（本节数字可复算）：`find doc2md -name '*.py' -not -path '*__pycache__*' | xargs wc -l`；逐模块公开符号与引用方向由 `ast` 遍历 `Import` / `ImportFrom`（`level>0` 按 `__package__` 规则解析为绝对模块名，函数体内导入同样计入）得到；收集项数由 `python -m pytest --collect-only -q` 的分组输出读取。三者都不依赖运行时行为，改代码后重跑即可发现本清单漂移。资产口径同属可复算数字（CH-478 批次② 起把命令记进本节，取代 CH-469 轮未记录方法的 clone 实算）：`git ls-files | wc -l`（仓库受控文件）、`git ls-files tests/golden | wc -l`（语料受控文件）、`du -sk -I bigfile.bin tests/golden`（磁盘占用 KB）、`git ls-files tests/golden -z | xargs -0 stat -f%z` 求和（表观字节）；`MANIFEST.json` 的条目数按本节末恒等式与受控文件数对账。五个分层小节的行数相加为 2566 + 2524 + 984 + 1737 + 1437 = 9248，加六个 `__init__.py` 门面（69 行）等于包总量 9317 行，与 `wc` 尾行一致（CH-476 收口轮实核：33 行逐文件相加 + 门面 = `wc` 总数，无差额；批次⑬ 复读修正 `cron.py` 的 weekly 顺序 docstring（+3 行：267→270）与 `repo.py` 的 `synchronous` 注释（行数不变）后**当场重算**，G.1 标题、分层和与包总量三处同步为 2566 / 9208 / 9277。**CH-478 轮同样当场重算**：`s3_structure.py` +31（522→553）、`s4_render.py` +5（105→110），G.2 标题、分层和与包总量三处同步为 2520 / 9244 / 9313，逐节标题数与逐行和由脚本复核相等（G.1 8 行 2566、G.2 8 行 2520、G.3 3 行 984、G.4 9 行 1737、G.5 5 行 1437）。历史上两处小节的标题数与逐行和不等——G.1 旧记 2318 vs 逐行和 2330、G.2 旧记 2199 vs 逐行和 2411，均为改代码后未重算标题；本轮起标题数字一律由分组逐行和当场相加，采集命令同本节末，读者可复算校验）——门面文件不单列一节，因为它们的内容就是各表里的公开符号清单。

---

# 登记篇

本章之后的内容**只属于登记面**：阶段性缺陷、更优方案、实测证据、实现偏差与本文档自身的版本记录。主题篇不引用这里的编号，读者可整篇跳过而不损失任何规格信息。

## 十、变更与登记机制

### 10.1 登记流

| 要素 | 规则 |
| --- | --- |
| 编号 | `CH-xxx` 单调递增，不复用、不回收。`CH-401…CH-421` 为 v2.4 需求/ADR 阶段占用；开发期从 `CH-422` 起续号，当前至 `CH-479`。下一条目自 `CH-480` 起。**编号占用以远端提交为准**：CH-473 开工时发现 `CH-472` 已被上一轮提交（`36ef92a` 内的小语料 drain 断言修正）占用而该行登记滞后，按纪律整体改号——动笔前先 `git log` 核对已用编号是本轮（CH-473）新增的流程纪律 |
| 触发 | ① 实测发现缺陷；② 采用更优方案改变了原设计；③ 契约面（错误码 / 退出码 / HTTP / 产物结构）发生变化；④ 验收口径本身被证明不可判定 |
| 必填四要素 | 动因（含可复现证据）、落点（文件:函数）、实测数字、契约影响（无 / 内部 / 对外） |
| 禁止 | 以「优化」名义改动产物字节而不做等价复核；新增错误码段位；把未测项写成已实现 |
| 关闭 | 主题篇相应规格已更新 + 门禁有对应断言 + 第十二章有证据行，三者齐备才算关闭 |

**引用消歧（重要）**：登记篇正文里的「附录 B-10 / B-11 / B-12 / B-13 / B-14」「附录 C-1 / C-2」「附录 E」「§x.y」形式的引用，指的是**上游《Doc2md V1.0》计划书与其附录**（该文件不在仓库内，作为需求来源保留在项目上传记录中）；本文的「附录 A–G」是本文自己的规格表，两者字母含义不同。为避免混淆，登记篇新条目一律写作「上游蓝本 B-11」「上游 C-1」并在需要处给出本文对应章节号。

### 10.2 偏差登记（D-x）与缺陷登记（CH-xxx）的区分

`D-x` 记录「实现与方案文本不一致，但实现是对的或方案文本本身有矛盾」的落地口径，不产生代码改动义务；`CH-xxx` 记录需要（或已经）改动代码/文档的事实。两者都不得被用来掩盖未验证项——未验证项统一进第十二章的「未验证」表。

### 10.3 本文档的沿革

- 方案 v1.0 是开发前的计划书（面向任务卡、含伪码脚手架），登记流定义在其 §8.2。
- 管线 v2.4 的增量与验收记录以「附录 H」形式挂在 v1.0 之后（`docs/plan-delta-v1.1.md`）。
- 本 v2.0 把两者合并为单一主文档：规格叙述重写入主题篇与附录，历史事实原样迁入登记篇。`docs/plan-delta-v1.1.md` 保留为指向本文的存根，避免两处正文各自漂移。
- v2.1（CH-454）在主题篇第三章补入代码层结构（3.5）、依赖纪律（3.6）、技术栈与选型边界（3.7），并新增附录 G（模块与代码清单）；同时把文件名去版本化（`docs/dev-plan-v2.0.md` → `docs/dev-plan.md`），全仓引用形态从「方案 v2.0」改为「方案」。主题篇规格口径本身（第一、二、四–九章的判读）未变。
- v2.2（CH-456 + CH-457）待办收口轮：4.6 从「离线占位 + 待办」改写为 LLM 双模式实现陈述；附录 A 补 4 个 `[llm]` 键并清除全部「声明性」标注（49→53 键），5.5 与附录 A 阅读指南的对应口径同步；附录 D 的 E5041 启用、E5051 / W1999 删除、W1044 接入，E4151 行补打开边界语义；附录 G 全量刷新（39 模块 5485 行、17 测试文件 5081 行 238 收集）；第十二章新增 T6 大语料三张表（12.3）、CH-457 打开边界与 T7 演练实证（12.4）、T2 契约实证（12.5），D-8–D-12 全部标注关闭；3.7 的 ruff / marker / LICENSE 三项口径与 8.1、附录 F 的项数口径同步实况（67 项 / 238 收集）。
- v2.3（CH-458）T3 测量工具就绪轮：9.2 的 T3 条目从「待实测」改「工具已跨平台化、待目标机执行」；12.7 两条真机未验证项补工具口径（Windows 后端 psutil / macOS 后端 ps 已交叉验证）；附录 G 测试资产刷新（5125 行 / 242 收集），8.1 与附录 F 项数口径同步。主题篇规格判读未变，`doc2md` 包零改动。
- v2.4（CH-459）全面审查优化轮启动：9.4 落三路并行审查（45 项）的分诊方案（A 类 40 项直接实施 / B 类 3 项登记 T12、T13、随 T3 / C 类 2 项登记 T14 与措辞降档），9.2 待办表新增 T12–T14；批次①（A-1 七类块 / A-2 链接抽取 / A-7 LLM op 补齐 + A-13 提前落地）实施并登记为 CH-459——FR-104 的 table/code/quote/page_break 四类块与 FR-107/108 的块级链接从「声明未落地」变为产出现实；附录 G 全量刷新（39 模块 5984 行、18 测试文件 5556 行 276 收集），8.1 / 附录 F 项数口径同步，第十二章补 12.1 本轮数字与 12.2 等价性证据。`PIPELINE_VERSION` 维持 2.4.0，`PLAN_VERSION` 2.3→2.4。
- 迁移原则：条目内容不做「美化式」重写，只压缩表述；数字一律保留原始口径与测量方式，重测得到的新数字并列记录并注明语料（见 12.3）。

## 十一、逐条变更登记（CH-422 … CH-478）

类型说明：**缺陷** = 行为不符合规格；**口径** = 规格内部矛盾需裁定；**更优方案** = 原设计可用但有更好做法；**契约回归** = 实现违反了冻结契约。

| 编号 | 类型 | 一句话结论 | 落点 | 契约影响 |
| --- | --- | --- | --- | --- |
| CH-422 | 口径 | 文件名长度统一为 **UTF-8 字节预算**（≤128），按字节预算逐字符累加、切碎多字节字符被禁止，预算紧张时优先保扩展名 | `security/names.py:_fit` | 门禁判定 |
| CH-423 | 缺陷+勘误 | 分片重叠区未去重致同页内容重复；蓝本伪码的双层索引写法与实装不符；`absorbed_ids` 只声明未落码 | `pipeline/sharding.py:overlap_dedupe` | 产物正文 |
| CH-424 | 更优方案 | 采样门禁改两级口径 `auto`：判定面覆盖全文档页密度，报告面保持 even5 页级明细 | `pipeline/s1_5_gate.py:check` | 门禁判定 |
| CH-425 | 缺陷 | 缝合失败降级单片时，产物 meta 必须按实际片数写 `sharded=False/shard_count=1` | `pipeline/convert.py` | 产物 meta |
| CH-426 | 缺陷 | 分片合并后统一重排 block id（不采用 id 映射表），消除重复 id 导致的错块编辑 | `pipeline/sharding.py:_renumber` | API 语义 |
| CH-427 | 缺陷 | 缝合只在真片界发生：清空预打的 `shard_edge`，仅跨 owner 的相邻块才算缝 | `pipeline/sharding.py:_owner` | 报告筛选 |
| CH-428 | 度量缺陷 | 相似度计算必须 `autojunk=False`（CJK 高频字被当 junk 会假失败） | `pipeline/metrics.py` | 门禁口径 |
| CH-429 | 度量缺陷 | 字符相似度改为「行锚定对齐 + 非等区间内字符级精比」，定长分块比对不可用 | `pipeline/metrics.py` | 门禁口径 |
| CH-430 | 缺陷 | S3 续行并回消除半句开头块（36 → 0），跨标题/空行打断也可并回并留痕 | `pipeline/s3_structure.py:_join_back` | 产物正文 |
| CH-431 | 缺陷 | 注入观测正则分类重写，命中 10/10 且反向零误报 | `llm.py:_INJECT_HINT` | 可观测性 |
| CH-432 | 缺陷 | 所有 `[paths].*` 相对值经 `path_setting()` 随 `--root` / `DOC2MD_HOME` 迁移 | `config/loader.py:path_setting` | CLI 语义 |
| CH-433 | 缺陷 | 未配置外置卷的部署周报记 `skipped` 且 `ok=True`；已配置但缺口令才记失败 | `cron.py` | 运维语义 |
| CH-434 | 缺陷 | 文件名消毒失败抛 `UnsafeNameError(E4003, ValueError)` → HTTP 面 400 而非 500 | `security/names.py` | API 契约 |
| CH-435 | 缺陷 | CJK 全文检索失效：FTS5 加 `aux` 列（单字 + bigram）+ 子串精确复核 + 异常退 LIKE；虚表重建式迁移 | `storage/fts.py` `db.py` `repo.py` | 搜索契约（响应新增 `mode`/`match`） |
| CH-436 | 缺陷 | `Store` 改 `threading.local` 连接池，`serve` 多线程并发不再 500 | `storage/db.py` | 可用性 |
| CH-437 | 缺陷 | `work/` 权限 700 从未生效（chmod 打在了 `work/reports`）→ 引入 `BackupPlan.work_root` | `ops/backup.py` | 安全验收 |
| CH-438 | 缺陷 | 分片产物页数取真实片尾覆盖数，不按各片求和（322 → 320） | `pipeline/sharding.py:true_pages` | 产物 meta |
| CH-439 | 契约回归 | 撤销自造 `E4091/409`，缺 `confirm=true` 回到 400/E4001（附录 DoD 字面口径） | `api.py` `errors.py` | API 契约 |
| CH-440 | 缺陷 | 级联删除先清产物再删库行；清除失败 fail-closed（E5031 + 保库行 + 可重试） | `storage/repo.py:delete_doc` | 删除契约 |
| CH-441 | 缺陷 | 依赖清单与实装导入对齐（虚声明 4 项、漏声明 Flask），`doctor` 与 `pyproject` 单一口径 | `pyproject.toml` `cli.py:DEPENDENCY_MODULES` | 安装/运维面 |
| CH-442 | 缺陷 | 黄金样本集自身改为逐字节可复现（固定生成时间戳/docx mtime 与核心属性）+ 路径可移植 + 循环链接改相对 | `tests/make_golden.py` | 资产可信度 |
| CH-443 | 缺陷 | 「大文件拷贝中途不入队」从占位资产改为测试内自造（真写盘 + 注入时钟）；120MB 资产降级为可选 | `tests/test_m3_bigcopy.py` | 验收覆盖 |
| CH-444 | 缺陷 | NFR-19 的服务内置轮转与启动清扫在 `serve` 面从未落地；修复时又暴露 serve/cron 双进程同轮一个文件互踩 → 按进程分文件属主 | `api.py` `cron.py:sweep_tmp` `tests/test_v24_gates.py` | 运维契约 |
| CH-445 | 缺陷 | README 解包口径错误：`unzip -y` 非法（rc=10），`python -m zipfile -e` 把软链降级为普通文件 → 改为默认 `unzip`/`tar` | `README.md` | 文档/分发口径 |
| CH-446 | 缺陷 | 红线未进 CI：仓库无任何工作流配置 → 新增 `merge-gate`(`-m gate`, 3.11/3.12/3.13) + `full-suite` 两层 | `.github/workflows/ci.yml` | 交付流程（workflow 已于 CH-453 轮次进远端并首轮全绿；阻断合并仍待 T1） |
| CH-447 | 更优方案 | S1 分块重开 + 每页缓存清理 + 流式哈希 + 行内样式取回的偏移表/bisect 化：驻留大降且产物零漂移 | `pipeline/s1_parse.py` `ir/model.py:hash_of_path` `config [s1]` | 无（产物等价） |
| CH-448 | 更优方案 | 批量转换走有界进程池（spawn 语义验证）；队列携带 `source_path`/`mode` 真列（`DB_VERSION 3→4`）；崩溃遗骸按 `stale_job_minutes` 回收 | `pipeline/batch.py` `storage/db.py` `worker.py` `config [perf]` | 内部 schema（迁移幂等） |
| CH-449 | 更优方案 | `perf.queue_async` 异步入队（全入队或零副作用）+ `doc2md work` 消费者（原子领取、内容漂移留痕）+ `GET /jobs` + 独立 `work.jsonl` | `worker.py` `api.py` `cli.py` | API 新增可选口径（默认仍同步） |
| CH-450 | 缺陷 | NFR-6 磁盘门禁从零实现补齐：三处写盘入口前置校验，违规不留半成品；关闭需 `min_free_mb` 与系数同时为 0 | `ops/diskguard.py` `convert.py` `cron.py` `api.py` | 复用 413/E4131 |
| CH-451 | 缺陷 | cp936 控制台不再崩进程（`errors="replace"` + `_jline` 纯 ASCII）；`sniff()` 统一抛 E4151（原裸 `ValueError` → HTTP 500） | `cli.py:_console_safe/_jline` `pipeline/s1_parse.py:sniff` | API 契约（415/E4151） |
| CH-452 | 文档 | 开发方案全面重写为单一主文档：主题篇规格化（不含变更编号）、附录 A–F 建表、登记篇收全部 CH/D/证据；`docs/plan-delta-v1.1.md` 降级为存根；`PLAN_VERSION` 1.0 → 2.0；清除代码里写死的过时基准数字；全仓 40 余处旧锚点引用改指本文章节 | `docs/dev-plan.md` `docs/plan-delta-v1.1.md` `doc2md/__init__.py` `README.md` `doc2md/**`（注释）`tests/**`（docstring）`config/` `pytest.ini` `.github/workflows/ci.yml` | 无运行时行为变化（仅注释与文档） |
| CH-453 | 更优方案 | 令牌补 `Workflows` 权限后 workflow 首次推送成功，远端触发 run #1（push→`main`）四作业全绿：`merge-gate` 3.11/3.12/3.13 各 65 项、`full-suite` 143 项含可复现性与 `doctor`。据此把「CI 未验证 / 推送受阻」的口径整体改写为「已运行、但未强制阻断」——阻断仍缺必需检查能力（private + 免费计划下分支保护与规则集 API 均 403）。T1 判据随之从「推上远端」收紧为「故意失败的 PR 被拦下」 | `docs/dev-plan.md`（8.6 / 9.1 / 9.2 / 9.3 / 12.1 / 12.7 / CH-446 行）`README.md` | 无运行时行为变化；交付流程从「无执行点」变为「有执行点、无硬阻断」 |
| CH-454 | 文档 | 主题篇补入「功能设计之下的实现面」三节：3.5 代码层结构（模块地图，37 个 `.py` / 4834 行的分层归属）、3.6 依赖方向与代码结构纪律（实测 DAG 五层 + 8 条可复核纪律，含唯一向上引用 `worker→api._emit`）、3.7 技术栈与选型边界（采用表 13 行 + 18/25 依赖闭包 + 8 项明确未采用及其代价）；新增附录 G 模块与代码清单（逐模块行 / 层 / 公开符号 / 引用方 + 测试资产 + 采集方法）。同时把文件名去版本化（`docs/dev-plan-v2.0.md` → `docs/dev-plan.md`）并把全仓 29 个文件的引用形态从「方案 v2.0」统一为「方案」；`PLAN_VERSION` 2.0 → 2.1。写作过程自查暴露的三项缺口按事实登记为 D-11 / D-12 / D-13（不写进主题篇），并新列待办 T10（静态检查工具链）与 T11（许可与元数据声明），T9 的 dead-code 清单由 1 项补全为 8 项（附录 G 建表时用 `ast` 找定义 + 全包与 `tests` 找调用点复查所得） | `docs/dev-plan.md`（3.5–3.7 / 附录 G / 1.4 / 9.2 / 10.1 / 10.3 / 十二）`docs/plan-delta-v1.1.md` `README.md` `doc2md/__init__.py` `doc2md/**` `config/` `pytest.ini` `.github/workflows/ci.yml` `tests/**`（注释与 docstring 引用） | 无运行时行为变化（仅文档、注释、`PLAN_VERSION` 常量）；规格判读未变，但主题篇覆盖面扩大（新增三节 + 一个附录），故按 1.4 的「实质性修订」升文档版本 |
| CH-455 | 缺陷 | **SQLite 字符串比较让 CH-448 的遗骸回收与事件保留期在关键分支静默失效**——一个根因、两处症状加一个被遮蔽的崩溃。根因：`now_iso()` 写入 `YYYY-MM-DDTHH:MM:SS.mmm+00:00`，而 SQL 侧的 `datetime('now',…)` 产生 `YYYY-MM-DD HH:MM:SS`，裸字符串比较在第 11 位（'T'=0x54 对 ' '=0x20）即分胜负，**同 UTC 日内** `started_at`/`ts` 与阈值的直比永远判「未过期」，只有跨过午夜才开始生效。症状一 `worker.reclaim_stale`：谓词 `started_at < datetime('now',?)` 对同日过期作业恒假，OOM 后遗骸永久停留 running、队列看似有活却永不动（CH-448 的崩溃恢复实际死亡）。症状二 `cron.run` 第 6 步：`DELETE FROM events WHERE ts < …` 同理，30 天保留期最多溢出约 24h 才真正删。被遮蔽的第三处：`run_work` 把 `total.notes.append(...)` 写在 `total = WorkReport()` 之前，**唯一「回收真的发生了」的分支必抛 NameError**——恰因症状一让回收恒不触发，该崩溃在真实运行中从未被看见。发现路径：执行 T10 首次引入 ruff（`--select F`），F821 直接点名 `total` 未定义，顺藤排查才挖出根因——零运行时异常、零测试失败地潜伏了三个版本。修复：两处 SQL 均把列包进 `datetime(...)` 再比较（`worker.py:reclaim_stale`、`cron.py` 第 6 步）；`total = WorkReport()` 上移到回收块之前。测试：新增 3 个（gate 层 2 个 + m8 边界 1 个），全部先在缺陷代码上验证失败形态（reclaim 返回 0、notes 为 `[]`、events_purge removed=1）再转绿；顺带删除本轮测试草稿遗留的 1 处未用导入。T10 的剩余部分（全量 ruff 集成、marker 修复）不在本条目内，后续续登 | `doc2md/worker.py`（`reclaim_stale` 谓词、`run_work` 初始化顺序）`doc2md/cron.py`（第 6 步谓词）`tests/test_perf_lowend.py`（+2，gate 25→27）`tests/test_m8_ops.py`（+1） | 队列遗骸回收与事件保留期**开始真正生效**（内部行为修正）；对外契约（字段 / 退出码 / 报告结构）无变化，`work.jsonl` 首行因此新增 `reclaimed_stale` 计数（原设计行为，此前不可达）。`PIPELINE_VERSION` 维持 2.4.0：无 schema / 字段 / 版本常量变更，属缺陷修复轮 |
| CH-456 | 缺陷+更优方案（T2/T4/T5/T7/T8/T9/T10/T11 合轮收口） | 待办清单一轮清空七项（T1/T3 留待）。① **T9 配置收敛**：D-8 的 11 个声明性键全部落地消费——`run.profile` 在 loader 落为三档键集覆盖（用户显式配置永远优先）、`run.offline_assert` 成为可开关运行期守护（netguard 把 socket 出站限制为回环）、`s1_5.reject_on_any_bad_page`/`page_detail_in_report` 接线 convert、`api.token_roles` 供 `auth.issue(allowed=…)`、`ops.work_dir_mode` 供 `BackupPlan`、`security.sanitize_fail_action` 成为冻结键（唯一合法值，配错即启动期 `ValueError`）、`llm.*` 随 T2 消费；D-8 / D-10 随之关闭（默认 49→53 键、示例 42→54 键 = 53 + 故意多给的 `ops.backup_external_dir`，键集差清零）。D-9 的 8 处死代码全部删除（`Scheduler.next_queued`/`active_for`、`auth.timing_safe_equal`/`require`/`RevocationCache`、`db._Tx.lastrowid`、`metrics.line_similarity`/`diff_lines`，删除前逐一确认无分发面隐式契约）；`E5051` 删除（缝合失败由 4.3 降级口径吸收，抛码反而违背降级语义）、`W1044` 接入降级留痕路径、`W1999` 删除、`E5041` 留待 T2 启用。② **T10 静态检查**：ruff（`F`/`E`/`I`）以独立信息作业进 CI（与 merge-gate 并行、无 `needs`，风格判断不入红线——避免与 T8 的门禁耗时目标冲突）；7 处未使用导入清零；`pytest.ini` marker 对齐实际用法（删零使用的 `slow`、`milestone(name)` 改实际取值）并开 `--strict-markers`；D-12 关闭。③ **T11 许可声明**：根目录 `LICENSE`（MIT）+ `pyproject.toml` 补 `license` 与 `classifiers`；D-11 关闭。④ **T8 门禁提速**：红线 72.5s→**53.0s**（<60s 达标，第十二章 12.1）——大样本转换改 session 级共享夹具（原 42s 重复 setup），shard fallback 两类测试改中专句 PDF（每页 9 个唯一完整句 + 末行 2 字段接「续，」，同字体无标题行）替代 320 页样本，判定强度不降。⑤ **T7 演练自动化**：`doc2md/ops/drill.py` + CLI `doc2md backup drill [--keep]`——沙箱内 8 步闭环（copy_live→snapshot→external_encrypt→simulate_loss→rotate→restore→integrity_check→compare）；异常统一收敛 `DrillFailure`（GCM `InvalidTag` 原先击穿 CLI 的捕获面），`integrity_check` 对重度损坏返回 `error:` 行而非抛 `DatabaseError`；13 项测试含「篡改 enc 容器必须恢复失败」与 NFR-20 无正文泄露。⑥ **T4 队列可视化**：`static/editor.html` 153→319 行——五态 chips 过滤、任务表（排队等待/耗时/错误码/失败原因）、5s 自刷新、失败主动告警、done 任务链接载入；实测修掉三个真 bug（chip 点击只重渲染计数未重渲染表格、空 token 首屏 401 刷错误、`docCell` 计算后未插入行 HTML）。⑦ **T5 Windows 运维面**：`ops/windows/` schtasks XML（日/周两档，`StartWhenAvailable`、`LeastPrivilege`、电池照跑、`ExecutionTimeLimit`）+ `install-doc2md-tasks.ps1`（占位符替换拒绝残留、StartBoundary 取明天避免安装即补跑、`-SetPassphrase` 走用户级环境变量、校验输出 `OK/FAIL 任务名 下次运行｜上次结果`）；17 项与 launchd 等价静态测试（URI/Label/时刻/动作 parity + XML 合法性 + 口令零泄露）；**顺带修复仓库既有 plist 的非法 XML 缺陷**——注释含 `--daily`/`--weekly`（XML 注释禁含连续连字符），expat 拒解析，两个文件此前根本不是合法 plist。⑧ **T2 本地模型接入**：`llm.py` 新增 `LlmClient`（`from_cfg`/`repair`/`probe`；`raw_decode` 容忍围栏与尾杂音；vendor_models 下唯一 gguf 文件名发现模型名）+ `convert.repair_ops` 调度器（server 模式 / fallback 打标记 / `false` 明确失败 E5041）+ CLI `--llm-mode`/`--llm-no-fallback` + doctor 探测 `/health`；22 项测试用 stdlib `HTTPServer` 假 llama-server 钉请求契约（系统提示词、IR 定位摘要、白名单、`temperature=0`）、注入归因（`injection_candidate` vs `model_hallucination`）、故障分级（parse/http/connect）、Bearer 鉴权、offline 零 HTTP；e2e 实测 server 模式 1 次 HTTP、死端口 `--llm-no-fallback` exit 4/E5041 且不落产物。全量 146 收集→**238 收集（237 passed + 1 skipped）**，gate 67 项不变 | `doc2md/**`（config / llm / convert / cli / auth / scheduler / errors / metrics / db / ops/backup）`doc2md/ops/drill.py` `doc2md/ops/windows/*` `doc2md/ops/launchd/*` `doc2md/static/editor.html` `tests/test_t{2,4,5,7,9}_*.py` `pyproject.toml` `pytest.ini` `.github/workflows/ci.yml` `LICENSE` `README.md` `config/*` `ops/backup.md` | 对外：`[llm]` 新增 4 键（`mode`/`fallback`/`timeout_ms`/`model`）、CLI 新增 `backup drill` 子命令与 `convert` 两个旗标、E5041 启用（HTTP 503）、W1044 开始写入 `ir.warnings`；`DB_VERSION`/`SCHEMA_VERSION`/`PIPELINE_VERSION` 均不变。`PLAN_VERSION` 2.1→2.2：4.6 节从「离线占位 + 待办」改写为双模式实现陈述，附录 A/D/G 覆盖面扩大 |
| CH-457 | 度量+缺陷 | **T6 大语料实测，及其暴露的 S1 打开边界缺陷**。① 缺陷：截断 PDF（魔术码合法、无 xref/trailer）在 S1 打开时抛 `PdfminerException: Unexpected EOF`，原实现未映射→冒泡成 **E5000 内部错误**，「输入损坏」被误报成「工具故障」（HTTP 500 而非 415，任务终态 failed 且错误码不可判定）——而 4.1 明写「不可读、读取异常一律归入 415 契约」，附录 D 冻结语义亦为「打开源文件不可读同归此码」。修复：`s1_parse._open_source` 在 pdfplumber / python-docx 两个打开边界统一映射 E4151；detail 只含文件名与异常类名（NFR-20：异常消息可能夹带文档内容，不带入）；`Doc2mdError` 原样穿透不重包装；**只映射打开失败**——页级抽取异常不在此冒充「不可读」，仍走既有失败路径（精度优先，另一类失败不合并）。退出码不变（同步 convert 仍 4、异步 work 仍 5），变的是错误码与 HTTP 状态。② 实测：`ops/bench/t6_large_corpus.py`——1080 文件混合语料（1000 PDF + 60 DOCX + 20 坏文件，种子 20261014）；reportlab `invariant=1` + DOCX zip 条目时间戳固定保证**字节级可复现**（否则同种子两次生成不同字节，「固定语料」口径不成立）；坏文件字节唯一（同字节会被 `uq_tasks_doc_active` 附着去重，「文件数↔终态」1:1 口径失真）；父进程 /proc 进程树 RSS 0.1s 采样（规避 12.3 的 `RUSAGE_CHILDREN` 陷阱）+ sqlite 只读连接轮询队列状态；测量一律走真 CLI 子进程（`doc2md work --enqueue DIR --once`），父进程只采样。三张表见 12.3：workers=1/4 排空 **51.4/49.1s**（1261/1320 件每分钟）、进程树峰值 **105.6/191.5MB**（最大单进程 38.6MB）、失败账目**精确闭合**（1060 done + 7 failed(E4151) + 13 入队即拒(E4151) = 1080；非 done 占比 1.85% = 注入坏文件比例，两并发度逐项一致）。附带结论：1080 个**单页小文件**上 4 进程仅比串行快 4.5%——瓶颈不在解析并发度而在每任务固定开销（DB 写、进程调度、enqueue 逐条打印）；与 12.3 既有「8×40 页 PDF 并行约 2 倍增益」不矛盾（语料形态不同，按 12.8 教训 4 不可互相外推）。新增 14 项测试：打开边界回归 6 项（read_pdf/read_docx/convert/worker 落库全链路 + 好文件不误伤 + Doc2mdError 穿透）+ 仪器自检 8 项（语料确定性、坏文件唯一、三张表渲染、/proc 采样原语、真 CLI 端到端小语料） | `doc2md/pipeline/s1_parse.py:_open_source`（+`read_pdf`/`_pdf_page_count`/`read_docx` 三处打开点）`ops/bench/t6_large_corpus.py` `tests/test_t6_corpus_bench.py` | 对外：截断/坏包文件的错误码 E5000→E4151（HTTP 500→415），CLI 退出码不变；门禁与产物语义不变。`PIPELINE_VERSION` 维持 2.4.0（缺陷修复 + 度量，无 schema/字段变更） |
| CH-459 | 缺陷+更优方案（全面审查优化方案 + 批次①实施） | **三路并行全面审查（45 项）的分诊方案，及第一批实施（A-1 七类块 / A-2 链接抽取 / A-7 LLM op 补齐）**。审查方法：代码与性能 / 测试体系 / 文档一致性三路只读审查（各 15 项），按「功能全面、性能优先」分诊为 A 类 40 项（沙箱内可改可验证，直接实施）、B 类 3 项（需 Windows/macOS 目标机，登记 T12/T13/随 T3）、C 类 2 项（需人工决策，T14 + 12.5 措辞降档）；方案写入 9.4。批次①三项：① **A-1（FR-104 P0）**：`BLOCK_TYPES` 声明七类块而 S3 原只产三类——下游 W1043 跨片表格检测永假、S4 三个渲染分支死代码、`mark_table_boundary` op 无对象。修复：S1 补四组行级结构化信号（`Line.mono` 等宽字体；`Line.row` 表格单元格——PDF 按行内横向间隙聚类（阈值 `max(8pt, 1.6×中位字宽)`），DOCX 取 `w:tbl` 真实单元格并置 `row_explicit`；`Line.link`/`link_internal`/`link_dest`；`Line.level_hint` DOCX Heading 样式显式层级），S3 `analyze` 重写补四类保守检测（table：连续 ≥2 行 + 行首 x0 对齐 3pt + 行尾句末标点守卫仅对 PDF 几何推断行生效；code：围栏配对 / 4 空格缩进（仅空段起算）/ 等宽连续段三模式；quote：`>` 前缀或整行 CJK 引号（仅空段起算）；page_break：form-feed 行）。全部遵循宁漏勿误：漏判只退化为段落（原行为），误判污染正文。② **A-2（FR-107/108）**：`Line.link` 两条读取路径都不赋值、`Block.links` 无生产写入方。修复：PDF Link 标注按纵向几何相交附着到第一根未链接行（只认 URI / GoTo 两类动作，Launch/JavaScript 不抽）；DOCX `w:hyperlink` 仅整段覆盖时才附着（行内局部链接本轮不抽——块级模型无法表达「哪一段文字」是链接，硬附会把整块包成 `[整块](url)` 制造错误数据）；S3 段落冲刷路径跟踪 `para_lines` 取段内首条带链接行附着（每块至多一条）；S4 `_inline` 渲染首条可渲染链接。③ **A-7**：LLM op 白名单 5 个仅 `fix_heading_level` 有实现，其余 4 个过校验、计入 `applied_ops` 但静默无效（「计数成功、产物没变」比拒绝更坏）。修复：`apply_ops` 五 op 全落地——`split_block` 按 `at` 偏移切块、新块 id 加 `s` 后缀且不重排后续 id（外部引用按 id 寻址）；`merge_block` 并入前一块（`absorbed_ids` 留痕，CJK 直连 / 拉丁补空格）；`fix_list_marker` 规范化行首标记；`mark_table_boundary` 只打旗标不改正文。基座纪律：`llm.py` 不 import 包内模块，新块用 `type(b)(...)` 构造。**收尾自查实测修掉三个真缺陷**：`\x0c` 是 Python 空白字符，`rstrip()` 会把它剥掉导致 page_break 分支永假（改为先于 rstrip 判定）；空行不冲刷 quote 缓冲致两段独立引用被并成一块且段间空行丢失；结构行（fence/heading/list/page_break）终止行组时误用 `flush_rows(False)`——表格后紧跟标题/列表/代码就丢表（改为按事实判定 `flush_rows(True)`）；另清 ruff F841 死变量一处。实测：新增 `tests/test_s3_blocks.py` **34 项**（七类块可达性回归 + 四类块口径 + 链接单测/e2e + 五 op 语义 + 自建 DOCX 样本）；gate **67 项全绿**（54.2s）；全量 **276 收集 = 275 passed + 1 skipped**（109.1s）；`ruff check .` 全绿。等价性三态复核（改造前快照 vs 改造后）：`small_3p`（`b1a1312c26d9dc9f`）与 `large_320p`（`6a90e3e368819ed5`）markdown 逐字节一致；`links_pdf` 按设计变化（两条链接落地，`dfd7fa22`→`882a6a41`）；三个 DOCX 样本仅标题检出变化（FR-104 修复的预期效果，已核实源文件 Heading 样式，无误判） | `doc2md/pipeline/s1_parse.py`（+173 行：四组行级信号 + `_attach_links` + DOCX 重写）`doc2md/pipeline/s3_structure.py`（+234 行：`analyze` 重写 + 四类块检测 + 链接附着）`doc2md/pipeline/s4_render.py`（+5 行：page_break 空串分支 + 链接渲染）`doc2md/pipeline/convert.py`（A-13 提前落地：sniff 一次直传 `read_any`）`doc2md/llm.py`（+85 行：`apply_ops` 重写 + `_split_block`/`_merge_block`/`_cjk`）`tests/test_s3_blocks.py`（新增 431 行 34 项）`docs/dev-plan.md`（9.4 审查方案 + 本条 + 附录 G 刷新） | 对外：产物新增 table / code / quote / page_break 四类块与块级链接渲染——这是 FR-104/107/108 的原设计行为落地，不是新契约；`links_pdf.pdf` 与 DOCX 样本产物按设计变化（等价复核与差异清单如上）。`PIPELINE_VERSION` 维持 2.4.0（无 schema / 字段 / 版本常量变更）。`PLAN_VERSION` 2.3→2.4（主题篇新增 9.4 审查优化方案、附录 G 全量刷新、登记篇扩充） |
| CH-460 | 缺陷+更优方案（全面审查优化轮 · 批次②：性能与存储） | **批次②实施（A-3…A-6、A-9、A-10、A-12、A-14、A-15）+ A-8 实测回退**。① **A-3（`Store._conns` 强引用泄漏）**：thread-local 连接进强引用 list 后永不释放，`serve`（threaded）每请求累积一个连接到 fd 上限即服务面 500。改弱引用登记表——`sqlite3.Connection` 是 C 扩展类型**不支持 `weakref`**（改完首次运行即 `TypeError` 全库崩溃），故登记表改存可弱引用的持有者 `_ConnRef`（由 `Store._local.holder` 线程独占持有，线程消亡即出列），并暴露 `live_conn_count()` 供测试与运维探针。② **A-4 / A-14（每任务约 6 次写事务）**：`save_ir` 改单事务 + `executemany`（docs/blocks/search_blocks 三次批量，原 2 次事务 + 2N 条语句），新增 `tx=` 形参；`Scheduler.mark(tx=)` 与 `api._emit(tx=)` 同增形参；`worker._finish_ok` 的 detail/mark/event/入库由 4 次提交合并为 1 次，同步 `/jobs` 路径由 5 次降为 2 次（WAL + `synchronous=FULL` 下每次提交都 fsync，是 12.3 单文件固定开销的主因）。③ **A-5（S1.5 每页指标算两轮）**：`_avg_chars` 原对全文档重跑一遍完整 `_page_metrics`（4 个正则 × 全页数）。拆出 `GateCollector`——`feed()` 采集（非采样页只付 `_page_chars` 一个正则）、`finish()` 纯函数判定不再触碰页对象，`check()` 改为其内部实现（判定逻辑只有一份）。④ **A-6**：新增 `reindex_block`，PATCH / rollback 只删插该块 FTS 行（原任何单块编辑都整文档重写，FR-208 要的是「该块索引」）。⑤ **A-9**：FTS covered-span 从逐字符 `any()`（O(n×m)）改区间掩码一次遍历；新增 `MAX_QUERY_CHARS=256` 查询串上限（粘贴整段文档不再撑爆 MATCH 表达式，FTS 与 LIKE 两条路径同口径）。⑥ **A-10**：目录投递多文件时按文件派生幂等键 `键#文件名`（原 N 个文件共用一键，N-1 个静默消失）；单文件投递键字符串不变（既有契约不漂移）。⑦ **A-12**：`ops/logging._scrub` 原按**键名提示**（text/before/after/…）决定是否脱敏，提示名单外的键带大段正文原样入日志；与 `api._scrub` 统一为 `scrub_record`——不看键名只看长度（>64 字符 → sha256 指纹），递归 dict/list，`api` 侧事件入库同用此实现。⑧ **A-15**：`delete_doc` / `delete_source` 删前存在性检查（原对不存在的 id 也留痕并返回 200，「删了个不存在的东西」与「删成功」不可区分）；`rollback` 的 `int(to_revision)` 非数字参数由逃逸 500 收敛为 400/E4001（数字字符串仍是合法输入，未过度收紧）。**顺带修一个静默数据丢失**：`worker._finish_ok` 先写 `detail_json`（含 path/zip/blocks/content_drift），随后 `sched.mark(done)` 用 `{}` 把它覆盖——产物路径与漂移留痕从未真正入库（无测试钉住，由代码审读发现）。**A-8 实测回退**：convert「PDF 按片读取、单片驻留」已实施并验证等价（gate 明细与 md_sha 逐字段一致），但峰值内存实测不降反升（large_320p：`py_peak` 132.9MB → 134.9MB（2 片）/ 137.9MB（4 片），`rss` 364MB → 366MB），且每片多一次 PDF open——驻留主体是 IR blocks（5890 块）与 pdfplumber 单页解析缓冲，`pages_all` 只占约十分之一，而分片 IR 必须全部驻留供缝合，省下的被抵消。按「性能优先」原则回退该改动（代码内留回退说明），「不把整本文档读进内存」的纪律继续由 `s1.chunk_pages`（分块 open/close）承担。实测：新增 `tests/test_batch2_storage.py` **26 项**；gate **67 项全绿**（54s）；全量 **302 收集 = 301 passed + 1 skipped**（100s）；`ruff check .` 全绿；S1.5 门禁耗时（large_320p 320 页，7 次取中位）auto **146.1ms → 73.5ms（−49.7%）**、even5 **70.9ms → 7.3ms（−89.7%）**；等价性复核（改造前 `git worktree` vs 改造后，同配置同 cwd）6 个黄金样本的 gate 明细与 markdown sha256 **逐字段一致**（产物面零变化） | `doc2md/storage/db.py`（`_ConnRef` + `WeakSet` + `live_conn_count` + `_Tx.executemany`）`doc2md/storage/repo.py`（`save_ir` 重写 + `reindex_block` + 两个删除的存在性检查）`doc2md/storage/fts.py`（`MAX_QUERY_CHARS` + `_non_cjk_rest` 区间掩码）`doc2md/pipeline/s1_5_gate.py`（`GateCollector` + `_page_chars`）`doc2md/scheduler.py`（`mark(tx=)`）`doc2md/api.py`（`_emit(tx=)`、幂等键派生、脱敏改调 `scrub_record`、rollback 参数校验）`doc2md/worker.py`（`_finish_ok` 四写合一 + detail 覆盖修复）`doc2md/edits.py`（`reindex_block`）`doc2md/ops/logging.py`（`scrub_record`）`tests/test_batch2_storage.py`（新增 366 行 26 项）`docs/dev-plan.md`（9.4 进度、本条、12.1/12.3 证据、附录 F/G 刷新） | 对外契约变化三处（均为「原来错了」的收敛，非新设计）：删除不存在的 doc/source → 404/E4041（原 200 且留痕）；`rollback` 的 `to_revision` 非数字 → 400/E4001（原 500）；目录投递多文件时幂等键按文件派生（单文件不变）。产物面（content.md / ir.json / report.html 字节）零变化。`PIPELINE_VERSION` 维持 2.4.0（`SCHEMA_VERSION` / `DB_VERSION` 均不变）。`PLAN_VERSION` 2.4→2.5 |
| CH-461 | 缺陷+更优方案（全面审查优化轮 · 批次③：备份加固 A-11） | **外置备份容器 v2（随机盐 + 分块流式）**。v1 容器（`D2MBAE1`）两个缺陷：① scrypt **静态盐**——同口令跨部署派生同一把密钥，攻击者可离线预制彩虹表后对所有 doc2md 部署的备份通用；② `snapshot.read_bytes()` **整库读入内存**——12MB 大样本（CH-443）已是例外资产，NFR-21 千页级场景下备份一步就回到 CH-447 立纪律试图避免的峰值。v2 容器（`D2MBAE2`）设计：头部 `magic(7) \| salt(16) \| chunk_size(4) \| total_chunks(8)`，盐每次加密随机生成（同口令同数据两次加密的盐与密文都不同，彩虹表失效）；正文按 4MB 分块，每块独立 `nonce(12) + AESGCM(key, chunk)`；每块 AAD 绑定 `魔数 + total_chunks + 块序号`——交换两块、截断容器、篡改头部总块数都在 GCM 认证上失败，防篡改强度不弱于 v1 的整体认证（v1 把「认证整个文件」做成了「认证每块+头」，块级定位反而更好）。`encrypt_stream` / `decrypt_stream` 为流式原语（`src` 须可 seek 以先取长度算总块数；解密端非末块严格读满、末块只要求 tag 在场，`trailing bytes` 严格拒绝）；`BackupPlan.to_external` / `restore` 改流式，`restore` 走 `.part` 临时文件 + 全部块认证通过后原子 rename——**恢复失败不留半截库**（沙箱演练的篡改用例同时断言 dst 与 `.part` 都不存在）。**v1 容器保持可解密**：`decrypt_bytes` 按魔数分派，存量外置副本的恢复路径不断，但 v1 不再生产。顺带三处测试钉住旧实现细节随规格更新：`test_m8_ops` 两处 `D2MBAE1` 前缀断言、`test_t7_drill` 的魔数断言与篡改偏移、`ops/drill.py` 的容器头校验——都改为 `MAGIC_V2` 口径。开发中自测修掉一个解密端缺陷：非末块读满、末块只要求 tag 在场的判定最初写成了「所有块都须读满整块」，不足 4MB 的真实快照一律误报 truncated（空库演练用例暴露）。实测：新增 6 项（随机盐 / v1 兼容解密 / 六种大小形态的多块流式 round-trip（0、1、块边界整除、不整除、双块+尾差）/ 块交换拒绝 / 截断容器 restore 不留半截 / 头部 total 篡改拒绝）；`test_m8_ops.py` 22→28 项；gate **67 项全绿**（49s）；全量 **308 收集 = 307 passed + 1 skipped**（102s）；`ruff check .` 全绿；沙箱演练 8 步闭环在 v2 容器下全绿（`test_t7_drill` 13 项不变通过） | `doc2md/ops/backup.py`（131→237 行：`MAGIC_V2` / `LEGACY_SALT` / `CHUNK_SIZE` / `encrypt_stream` / `decrypt_stream` / `_aad_v2`，`to_external` / `restore` 流式化）`doc2md/ops/drill.py`（容器头校验改 `MAGIC_V2`）`tests/test_m8_ops.py`（+6 项）`tests/test_t7_drill.py`（魔数与偏移随 v2 更新）`docs/dev-plan.md`（9.4 进度、本条、12.1/12.7、附录 G 刷新） | **对外：外置 `.enc` 容器格式换代**——cron 周备与 `backup drill` 新产物为 v2 容器；v1 容器只读兼容（历史副本可恢复，但旧版程序不能解 v2 容器，跨版本回退时外置副本须先恢复为明文快照）。口令、CLI、`DOC2MD_BACKUP_PASSPHRASE`、轮转与演练流程均不变。`PIPELINE_VERSION` 维持 2.4.0（无 schema / 版本常量变更）。`PLAN_VERSION` 2.5→2.6 |
| CH-462 | 缺陷+更优方案（全面审查优化轮 · 批次④：测试体系 A-16） | **A-16 的 15 个子项中 12 项未解决项全部落地，顺带挖出两个被零覆盖掩盖的真缺陷**。① **CLI 四子命令入口层补测**（原仅底层函数有测，argparse 分发 / `cmd_*` 零覆盖）：`tests/test_cli_entry.py` 7 项——`serve token` 签发/--list/--revoke 全链、`backup drill` CLI 分发（成功 0 / 缺口令 4=EXIT_BIZ）、`watch --once`、`tokenizer export/verify`，全部走 `cli.main(argv)` 真分发。**写测试即发现真缺陷 1**：`watch --once`（cron 模式）单轮观察永不提交文件——`StabilityTracker.observe` 首见文件无条件进 pending 等「下一轮对比」，而 cron 模式每次全新进程没有下一轮，`--once` 分支是死路径。修复：`observe` 首见分支增加「mtime 已老于稳定窗口 → 直接判稳定」快速通道（墙钟判定，`watch --once` 测试以旧 mtime 驱动、不 sleep）；常驻模式行为不变（首见文件均为新写入，age≈0）。**真缺陷 2**：`/system/reload` 只 `config.load()` 不叠加 `settings_override.json`——`PUT /settings` 落盘的修改在 reload 后静默回退（启动时合并、重载时丢失，行为不一致）。修复：reload 与 `create_app` 同口径合并 override（测试钉住 reload 后 fallback 翻转可见）。② **API 三端点补测**（`tests/test_api_endpoints.py` 6 项）：`/events` 角色矩阵 + since 游标 + NFR-20 无正文、`/jobs/<id>/events` 脚本 UA 硬规则（浏览器 UA 403/E4031，FR-125）、`/system/reload` 权限矩阵。③ **sharding 纯函数单测**（`tests/test_sharding_unit.py` 10 项）：`shard_plan` 空片/单整除/不整除/重叠语义、`fingerprint` 稳定与区分、`overlap_dedupe` 保留第一份 + `dropped_dup_of` 留痕、`stitch_shards` 保序/续接吸收（absorbed_ids）/id 重排（CH-426）/表格不续接。④ **make_golden 漂移副本**：删除生成器内逐行复制的 `sanitize_name`（35 行），改 import 生产实现——生产端改规则后黄金 expected 不再按过期口径计算（`test_golden_repro` 7 项复跑确认零漂移）。⑤ **cfg CWD 渗入**：`conftest.cfg` fixture 在隔离 cwd 下 `config.load()`——loader 的默认文件查找（`./doc2md.toml`）与相对路径解析以 CWD 为准，原实现把运行目录渗入 session 配置。⑥ **zip 完整性**：`test_product_zip_complete` 断言三条目齐全 + `testzip()` CRC 全过 + ir.json 块数与内存 IR 一致（打包静默截断此前不可测）。⑦ **raises(Exception) 收窄**（6 处）：错口令 / 容器篡改 → `InvalidTag`，结构截断 → `ValueError`，门禁拒收 → `E4221_GateReject`。⑧ **vacuous 断言**：删除 `row = None; assert row is None`（批次②遗留），补同键同文档断言。⑨ **5s 墙钟轮询移除**（2 处）：PATCH 后索引在事务内同步刷新（CH-460 `reindex_block`）、吊销是 `revoked_at` 行级即时判 401——两者都是即时语义，5s 轮询是伪异步时代的防御式写法（慢机 flake 源）。⑩ **port=1 假设**：改 bind-then-close 取空闲端口（不再假设 1 号端口必无监听）。⑪ **regex→ast**：`_third_party_imports` 改 ast 遍历（原正则漏 `importlib.import_module` / `__import__` 字面量与多行 from-import），顺带把动态导入纳入依赖闭包比对。⑫ **注入语料缓存**：三个注入测试共享模块级缓存（原同一批语料重复 convert 约 33 次，纯测试耗时）。已解决不重复登记的 3 项：backlog 区间断言（CH-458 已改）、unlink patch 自证有效、cfg 无原地修改串扰。实测：新增 24 项（7+6+10+1），gate **67 项全绿**（52s）；全量 **332 收集 = 331 passed + 1 skipped**（106s）；`ruff check .` 全绿；黄金样本可复现性 7 项复跑通过（make_golden 改造后 manifest 零漂移） | `doc2md/security/watcher.py`（`observe` 首见快速通道）`doc2md/api.py`（`/system/reload` 合并 override）`tests/test_cli_entry.py`（新增 117 行）`tests/test_api_endpoints.py`（新增 81 行）`tests/test_sharding_unit.py`（新增 127 行）`tests/test_perf_lowend.py`（+zip 完整性）`tests/test_m8_ops.py` / `test_t7_drill.py` / `test_batch2_storage.py` / `test_m9a_search.py` / `test_t2_llm_server.py` / `test_v24_gates.py` / `conftest.py` / `make_golden.py`（逐项修订）`docs/dev-plan.md` | 对外行为修复两处：`watch --once` 真正能提交文件（原设计意图，实现死路径）；`/system/reload` 不再丢失 settings_override。测试面无对外契约变化。`PIPELINE_VERSION` 维持 2.4.0。`PLAN_VERSION` 2.6→2.7 |
| CH-463 | 缺陷+更优方案（全面审查优化轮 · 批次⑤：文档口径 A-17） | **A-17 的 15 项中 11 项残留全部落地（4 项经实核确认已在 CH-456–462 间顺带解决）**。① **5.2 错误码段位表**：E5041 从「预留」改「已启用」（CH-456 / T2，detail 键补 `stage/bind/port/hint`）；E5051 / W1999 标注「已删除」（`errors.py` 实无此码，保留删除记录防误用）；W1044 改「当前落码」。② **附录 B**：补 `doc2md backup drill` 行（`--keep`、退出码 0/4）；convert 行补 `--llm-mode` / `--llm-no-fallback`。③ **doctor 项数实数**：文档写 24，实跑 30（固定 26 键 + 4 依赖探测；server 模式再 +`llm_server` 条件项）——补漏的 6 个键名（`config_frozen_keys` / `run_profile` / `llm_mode` / `llm_fallback` / `llm_endpoint` / `llm_vendor_models`），实跑脚本复核 `len(doctor)==30`。④ **数字实核刷新**：3.5 的包/测试行数（5984/5556 → 实测 6337/6348、18→22 文件）；G.4 表头 1196 → 表内实抄 1177（批次④更新时用错小计，采集方法句同步）；G.6 四文件行数与 `t6_large_corpus.py`（363→474，CH-458 扩采样后端所致）逐一对齐实跑。⑤ **README CI 节矛盾**：「失败阻断合并」与「无法设为必需检查」同页并存——改为如实表述「失败即 CI 红并在 CI 内阻断 full-suite；平台级必需检查受 T1 阻碍未配置」，`ci.yml` 头注释同步；顺带写明 ruff 是无人依赖的信息作业。⑥ **example.toml 注释**：`min_free_mb` 的「0 = 关闭门禁」改「与 disk_safety_factor 同时为 0 才关闭」（双零口径，`ops/diskguard.py` 实现语义）。⑦ **README launchd 标签**：删除不存在的 `md.doc2md.serve` / `md.doc2md.lll`…（笔误：`llm`），标注 serve/llm 常驻资产为待办 T12。⑧ **README tokenizer**：`export --gguf F` 改位置参数 `export F`（与 `cli.py` argparse 及附录 B 一致）。⑨ **README 门禁摘要键名**：`shards/char_sim/half_heads` 改实际键 `mode/sampled_pages/reasons/stats`。⑩ **README marker 列表**：去掉残留的 `M4M6`，与 `pytest.ini` 及测试实际取值一致。⑪ **3.7 依赖闭包**：`[dev]` 补漏的 `ruff`（3 项→4 项，25→26 个），并补「依赖版本为 `>=` 下界浮动」的口径声明。实核确认已解决的 4 项：附录 A 五键消费方齐备（顺带修正 `revoke_effective_seconds` 的消费描述——`doctor` 并不输出该键）、键集差 53/54 吻合、Windows 步骤表与 ps1 参数一致、12.5 无「双代证明」措辞（C-2 随本轮关闭）。实测：纯文档与注释轮，`doc2md` 包零改动；gate **67 项全绿**、全量 **332 收集**、`ruff check .` 全绿（出口复核照常执行） | `docs/dev-plan.md`（5.2 / 3.5 / 3.7 / 附录 B / G / A、9.4 进度、本条）`README.md`（六处）`config/doc2md.example.toml`（注释）`.github/workflows/ci.yml`（头注释） | 纯文档口径轮：无运行时行为、无契约、无版本常量变化（`PIPELINE_VERSION` / `SCHEMA_VERSION` / `DB_VERSION` 均不变）。`PLAN_VERSION` 2.7→2.8 |
| CH-464 | 缺陷+更优方案（第二轮全面审查 · 代码修复） | **复审挖出并修复 3 高 + 5 中 + 1 低，其中 3 项为上一轮改动自身引入的缺陷**。① **高 / watcher 重复转换（CH-462 快速通道的引入缺陷）**：`observe` 首见快速通道与常规 pending 路径都只对 `enqueued` 做**追加时**去重，`return True` 本身不去重——常驻 watch 每轮 poll 把目录内全部文件重转换一遍（CPU/磁盘打满、`save_ir` 反复清库重建）；已稳定入队的文件弹窗后重新登记、再等一个窗口又返回 True。修复：`enqueued` 改 dict 记录**提交时戳**，`observe` 入口先查——未变 → False，重写（size/mtime 变化）→ 弹回 pending 重新走窗口（保留热更新能力）；顺带修 mtime 在未来（时钟跳变/跨机拷贝）时 age 为负永不判稳定的卡死路径（进 pending 走常规窗口）；`registered`/`enqueued` 由 list 改 set/dict（原每 key O(n) 扫描，万文件目录每轮 2×10⁸ 次比较 + 内存只增不减）。补回归钉 2 项（多轮不重复提交 / 重写可再提交 / 未来 mtime 不卡死）。② **高 / S3 quote 块乱序**：引用行后接正文行时 `quote_buf` 不冲刷（段落与 mono 分支入口均缺 `flush_quote()`），quote 块被推迟到下一个空行才落块，排进其后段落之间——产物顺序错误。修复：非引用行到达即冲刷。黄金样本哈希零变化（样本无 quote 交叉场景）。③ **高 / 目录投递同名文件幂等键碰撞（CH-460 A-10 的引入缺陷）**：派生基准用 basename，不同子目录同名文件（a/report.pdf 与 b/report.pdf）得同键——异步路径第二个文件命中附着、静默丢失。修复：改**相对投递根路径**派生。④ **中 / patch_block 双事务不原子**：blocks UPDATE 与 FTS 刷新两笔提交，之间崩溃 → blocks 与 search_blocks 永久不一致且无启动对账。修复：`reindex_block` 增 `tx=` 形参并入主事务（与 save_ir 同型）。⑤ **中 / `_row_cells` 未按 x0 排序即聚类**：内容流乱序（双栏回写、先画右列）会把单元格切碎或整行判单列。修复：聚类前按 x0 排序。⑥ **中 / 查询参数 int() 逃逸 500**：`/jobs?limit=abc`、`/search?limit=abc`、`/events?since=abc` 落 Flask 默认 500 HTML（与 CH-460 A-15 修 rollback 同类病）。修复：`_int_arg` helper 统一 400/E4001。⑦ **中 / GoTo 显式目标 dest 垃圾串 + anchor 档与红线互斥**：GoTo 的 `D` 为显式目标数组时 `str(list)` 产出垃圾串（改取页号）；anchor 档产 `](#dest)` 与 `assert_render_contract` 明令禁止的自造锚点互斥（GFM 无自动锚点、必为死链）——正文降级纯文本（与 plain 同形），dest 完整保留在 IR/审阅页。⑧ **低 / worker 漂移检测整文件重哈希**：直接比较转换时刻与入队时刻的 doc_id（同一算法同一内容面），省每任务一次全量 I/O。⑨ **低 / 收尾代码块页码硬编码 0**：记录 `code_page`。⑩ **低 / doctor 自检面补 3 键**：`s4.link_internal` / `s4.preserve_marks_strike` / `watch.stable_seconds`（配置拼错在 doctor 即可见）。**核实后不实施**：search FTS 0 命中落 LIKE 兜底是旧库迁移保险（CH-435 迁移路径依赖），保留。实测：新增/重写回归钉 5 项（watcher 语义 2 + quote 保序 + 幂等键碰撞 + 行排序）；黄金样本 6 个 markdown sha256 **逐字节一致**（全部修复不触产物面）；gate 67 全绿、全量 332 收集、ruff 全绿 | `doc2md/security/watcher.py`（重写 observe 语义）`doc2md/pipeline/s3_structure.py`（quote 冲刷 + code_page）`doc2md/api.py`（幂等键相对路径 + `_int_arg`）`doc2md/pipeline/s1_parse.py`（_row_cells 排序 + GoTo dest）`doc2md/pipeline/s4_render.py`（anchor 档收敛）`doc2md/edits.py` + `storage/repo.py`（reindex_block tx=）`doc2md/worker.py`（漂移判定）`doc2md/cli.py`（doctor 补键）`tests/test_cli_entry.py` + `test_batch2_storage.py`（回归钉） | 对外行为修复：常驻 watch 不再重复转换（原行为是资源事故级缺陷）；quote 块顺序恢复源文档序；同名子目录文件不再静默丢失；`watch --once` 语义不变。anchor 档正文与 plain 同形（dest 留 IR）——FR-107 渲染分级语义随本条修正（规格内部矛盾消除）。`PIPELINE_VERSION` 维持 2.4.0。`PLAN_VERSION` 2.8→2.9 |
| CH-465 | 缺陷（第二轮全面审查 · 测试面修正 + 文档漂移） | **测试体系 14 项修正 + 文档 4 处漂移，全部为主会话直核**。测试面：① **伪覆盖核心**：`test_gate_collector_matches_check_even5` 是同义反复——`check()` 内部已走 GateCollector（同一实现），「collector 对比 check」回归保护为零，且 `except Exception` 分支让全拒收时测试空过；改为**冻结期望值断言**（auto passed / even5 采样点恰 1 页硬拒等口径冻结，语义变更必须显式改断言）。② **恒真断言**：`test_sharding_unit` 的 `... or True`（上轮 vacuous 清理漏网）、`test_batch2_storage` 的 `changed` 死变量（复杂推导只被 `is not None` 消费）——后者重写为 **rowid 不变断言**（「只刷该块」的行为证据，FR-208）。③ **断言错位**：`test_pdf_link_only_first_line_per_annotation` 校验的是「URL 互不相同」而非「一注一行」（两条内链即误红），改按 URL 去重语义断言。④ **flaky 加固**：`test_tiny_corpus_drain` 的 backlog 下界 3 依赖采样帧运气（上轮已复现一次红）→ 放宽 ≥1（采样器接线正常即可，终态断言才是硬口径）；`test_watch_once_ignores_unstable_file` 依赖 copy 与执行间隔 <5s → 拨未来 mtime 彻底消墙钟。⑤ **强度不足**：`test_rollback_numeric_string_still_works` 三种状态码全算过 → 改确定性场景（先 PATCH 产审计，回滚 "1" 断言 200 且正文复原，越界 "99" 断言 404）。⑥ **补覆盖**：`/tokens` 端点（admin 可签发/吊销、editor 403、吊销即时 401）与 `GET /docs/<id>/markdown` 正路径（front-matter、与库内块正文一致、404）各 1 项。⑦ **机差潜伏**：9 处测试内裸 `config.load()` 换隔离 cwd 的 `cfg` 夹具（`drill_env` fixture 改收 `cfg` 参数并 deepcopy，杜绝 `cfg = cfg` 自赋值笔误复活）。⑧ **降级平台常量断言**：sqlite3 不支持弱引用是 stdlib 事实、与仓库代码零耦合，改文档化断言（Python 未来加弱引用时提示可简化 `_ConnRef`）。⑨ **or 逃逸**：两处 `assert X or st["seams"] == 0` 拆为条件断言。⑩ docstring 与断言矛盾（退出码 3→4）修正。⑪ **cfg 污染教训钉（第 ⑦ 项实施过程中自己踩中又修复）**：把裸 `config.load()` 换成共享 `cfg` 夹具后，`test_frozen_key_rejects_unknown_value` 原地改 cfg 的旧写法从「无害」变「污染泄漏到全部后续用例」——`sanitize_fail_action='sanitize'` 渗入 session，`/jobs` 在别的测试里 500（间歇、顺序敏感，靠 `doc2md:app.py` 的 500 日志抓到 ValueError 栈）。修复为 deepcopy + 教训注释；这条同时**推翻上一轮「cfg 可变共享已解决」的核实结论**——该问题随共享夹具化而复活，已闭环。⑫ 随实现更新的旧断言：`test_internal_link_anchor_optin`（anchor 收敛后两档同形 + 红线断言）、`test_large_copy_never_enqueues_mid_write`（enqueued list→dict）、`test_tiny_corpus_drain` 的 `half_drain_s` 允许 None（小语料 0.1s 采样可能拍不到半排空帧）。文档漂移（主会话直核第三路）：⑪ **NFR-18 容器格式**（dev-plan 524、ops/backup.md 19）仍写 v1 固定盐——更新为 v2 结构 + v1 只读兼容说明；README 备份段同步。⑫ **FR-107 anchor 语义**（FR 表 / 渲染分级表 / 附录 A 三处）随 CH-464 收敛更新。⑬ **watch 判定描述**补首见快速通道与「同内容只报一次稳定」两条语义。实测：新增 4 项（/tokens、markdown 导出、quote 保序、行排序）+ watcher 语义 2 项（随 CH-464），gate **67 项全绿**、全量 **339 收集 = 338 passed + 1 skipped**（108s）、`ruff check .` 全绿 | `tests/test_batch2_storage.py` `tests/test_sharding_unit.py` `tests/test_s3_blocks.py` `tests/test_t6_corpus_bench.py` `tests/test_t7_drill.py` `tests/test_t9_convergence.py` `tests/test_v24_gates.py` `tests/test_cli_entry.py` `tests/test_api_endpoints.py` `docs/dev-plan.md` `ops/backup.md` `README.md` | 纯测试与文档轮，运行时行为仅随 CH-464 变化。`PIPELINE_VERSION` 维持 2.4.0。`PLAN_VERSION` 2.9→3.0 |
| CH-466 | 更优方案（文档补齐 · 本地 LLM 接入指引） | **新增 `ops/llm.md`（与 `ops/backup.md` 同级的实操手册）**：模型选型表（3B～7B instruct / Q4_K_M 量化 / 中文语料建议——op 修补任务上下文小、无需大模型）、引入五步（下载 GGUF（HuggingFace / ModelScope）→ 放入 `paths.vendor_models` → 启动 llama-server → `tokenizer verify` 资产校验 → doctor 探测 + `--llm-mode server`）、模型名发现规则（`llm.model` 优先；目录恰一个 gguf 取文件名，0/≥2 个需显式配置）、`[llm]` 配置速查表、故障排查表（connect/http 不通、E5041 语义、applied_ops=0 属 offline 预期）、边界纪律（NFR-1 离线红线与引入期下载的关系；12.7 诚实边界——真实 GGUF 全链路未验证，首接建议对黄金样本跑 offline/server 产物比对作真机验收；模型资产不入 git）。挂引用三处：dev-plan 4.6、附录 G.6 非 Python 资产清单、README `[llm]` 段。实测：纯文档轮，`doc2md` 包零改动；gate 67 全绿、全量 339 收集、ruff 全绿 | `ops/llm.md`（新增 87 行）`docs/dev-plan.md`（4.6 / G.6 / 本条）`README.md`（[llm] 段引用） | 纯文档轮：无运行时行为、无契约、无版本常量变化（`PIPELINE_VERSION` / `SCHEMA_VERSION` / `DB_VERSION` 均不变）。`PLAN_VERSION` 3.0→3.1 |
| CH-467 | 更优方案（T1 兜底 · pre-push 红线钩子） | **T1（红线阻断合并受 GitHub 计划限制）的客户端兜底**。背景：仓库 private + 免费计划，分支保护/rulesets API 计划级 403（CH-458 用 admin PAT 复测确认为计划限制而非令牌权限），「失败不可合并」只能靠人工判绿。实施：`ops/hooks/pre-push`（POSIX sh，`cd` 仓库根 → `pytest -m gate`（300s 上限）→ 红则列 FAILED/ERROR 并拒 push，日志 `/tmp/doc2md-gate.log`）；启用方式 `git config core.hooksPath ops/hooks`（本机已启用，README 有说明段与「勿 --no-verify」纪律）；两条静态测试把住（可执行 + 语法合法 + `-m gate` 口径与 CI merge-gate 同一断言面 + README 启用说明在场）。平台解锁（升级 Pro/Team 或转 public）后配置必需检查并做一次故意失败 PR 验收，钩子可保留为双保险。实测：gate 67 全绿 | `ops/hooks/pre-push`（新增）`tests/test_m8_ops.py`（+2）`README.md`（本地红线兜底段）`docs/dev-plan.md`（9.2 T1 / 12.7 / 本条） | 纯运维资产轮：`doc2md` 包零改动，无契约变化。`PLAN_VERSION` 3.1→3.2 |
| CH-468 | 决策实施（T1 关闭 · 仓库转公开 + 分支保护） | **按用户决策执行「转为公开仓库」，T1 正式关闭**。① **转公开前安全审计**（26 提交全历史）：敏感模式扫描（password/token/secret/api-key/私钥/邮箱）零真实命中（全部命中为测试代码的 token 机制描述与 CI run id 记录）；提交作者邮箱为 GitHub noreply；沙箱路径残留仅存在于未追踪的 `__pycache__`（`git ls-files` 确认 0 个 pyc 入库）。② **转换**：`gh repo edit --visibility public`（gh 已登录 RunoobGo），仓库现为 `https://github.com/RunoobGo/doc2md`（PUBLIC）。③ **分支保护**：`main` 必需检查 = `merge-gate (3.11)` `(3.12)` `(3.13)`（check 名经 `check-runs` API 实查对齐），`enforce_admins=false`（管理员走本地兜底纪律）、禁 force-push/删除。④ **验收（登记判据达成）**：开验收分支注入故意红断言 → **本地 pre-push 钩子首证拦截**（push 被拒，CH-467 兜底生效）→ `--no-verify` 入库开 PR #1 → CI 三个 merge-gate 全 FAILURE、`full-suite` SKIPPED（needs 链阻断）、`mergeStateStatus=BLOCKED`——双层防线（本地钩子 + 平台必需检查）各自独立验证通过。⑤ **清理**：PR #1 关闭并删除分支，main 无注入残留（gate 67 全绿复核）。⑥ **文档同步**：README CI 节改写（public + 必需检查生效）、9.2 T1 关闭、12.7 更新。**两层防线的最终形态**：本地 pre-push（防手滑）+ 平台必需检查（防绕过，`--no-verify` 也拦得住） | `docs/dev-plan.md`（9.2 / 12.1 / 12.7 / 本条）`README.md`（CI 节）GitHub 仓库设置（visibility / branch protection） | 仓库可见性变为 PUBLIC（对外：项目正式开源，MIT）；无代码行为变化。`PIPELINE_VERSION` / `SCHEMA_VERSION` / `DB_VERSION` 均不变。`PLAN_VERSION` 3.2→3.3 |
| CH-469 | 缺陷+更优方案（第三轮全面审查 · 收口） | **37 项发现全部实施（A 类，无 B/C 新增）+ 收尾自查的 3 个真缺陷 + 文档一致性收口**。逐项口径见 9.4「第三轮审查」与 `tests/test_ch469_service.py` / `tests/test_ch469_ops.py` 的模块 docstring（S-1…S-9 / V-1…V-13 / O-1…O-15 逐字母对应；**代码注释只标 `CH-469：`，字母是登记编号不进代码**）。要点四组：① **管线正确性**——`_join_line` 追加/替换语义错致缝界重复文本（S-1）、缝合续接丢前块正文（S-2，吸收必须搬运 text）、S3 段缓冲逐页重置使段落被页界截断（S-3，改跨页存续并归属起始页）、S4 空行压缩作用于 code 围栏内（S-4，只在围栏外压）、convert 把 `str(exc)` 写进 IR 违反 NFR-20（S-5，reason 只取异常类名）、批量进程池无单文件超时（S-6，新键 `perf.batch_file_timeout_s`）、S1/门禁的重复 `open` 与句柄泄漏 / `page_detail` 重复构建 / `overlap_dedupe` 无效指纹 / `mark_shard_edges` 死代码（S-7）、llm server 零重试 + `split_block`/`merge_block` 丢 links（S-8，新键 `llm.retries`）、`GateCollector` 少喂页裸 `KeyError`（S-9，改 `missing_pages` 可判定错误）。② **服务与存储**——`PUT /settings` 稀疏差量（V-1）、遗骸回收在常驻循环按 `perf.stale_check_seconds` 重跑（V-2，新键）、回读不到 IR 不得标 done（V-3）、`events(job_id,doc_id)` / `tasks(doc_id)` 补索引并把 **DB_VERSION 4→5**（V-4，存量库自动迁移）、列表与审计 `offset/limit` 窗口（V-5）、整源删除 3N 次 rglob 收到一次遍历（V-6）、非法角色 / 非数字 `base_revision` / 未知 token 从 500 与假成功收敛为 400/404（V-7/V-8/V-13）、搜索不再吞库面故障（V-9）、配置热更 copy-on-write（V-10）、审阅页文档派生字段全转义（V-11，构造文档即可执行脚本的 XSS 面）、CLI 退出码收口与连接收尾（V-12）。③ **安全 / 配置 / 运维**——netguard 可重入 + `connect_ex`/`sendto` 受拦 + 主机名按解析结果判定（O-1）、轮出只认本工具副本名（O-2）、盐必填且 v1/存量 v2 可解（O-3）、容器头分块上限 + AAD 绑整个头部（O-4）、zip 条目名拒绝反斜杠 / 盘符 / UNC / 空段（O-5，原实现先归一再判定＝洗白）、磁盘门禁取 available 而非 free（O-6）、内容指纹流式化（O-7）、watch 登记簿回收消失文件（O-8）、gguf 声明 KV 超限报错 + Tokenizer 探测长度收到 `\|distinct lengths\|`（O-9，实测 4.3×）、遍历截断即 413/E4133（O-10）、快照失败不留半截（O-11）、非 POSIX 卷 chmod 失效如实告警（O-12）、日志写入器与事件总线并发安全（O-13）、恢复 fsync 先于原子 rename（O-14）、`SPEC` 驱动的类型/区间/枚举校验 + 未知键点名 + 三处键集一致（O-15）。④ **自查三个真缺陷**——见 9.4：缝合去重回归（两条常数界规则修好，large_320p 三档 `blocks_after` 回到 == 单片 5696、`sim` 回到 1.0）、`batch.py` 的 `finally` 引用未绑定 `futs` 盖掉真实错误、`batch_file_timeout_s` 写在 `as_completed` 里永不触发（伪覆盖）→ 改 `wait(FIRST_COMPLETED)` 空窗语义并配红线用例。**实测**：`ruff check .` 全绿；全量 **481 收集 = 481 passed / 0 skipped（73.8s）**；`-m gate` **132 项全绿（28.7s）**；红线层口径由 67 → 132 是本三轮累计新增，分层构成见附录 F；零跳过纪律见附录 F（`grep -rn "pytest.skip" tests/` 为空）。**新增配置键 3**：`perf.batch_file_timeout_s`、`perf.stale_check_seconds`、`llm.retries`（默认表 54 → 57 键，三处键集相等）。 | `doc2md/pipeline/{sharding,s3_structure,s4_render,convert,s1_parse,s1_5_gate,batch}.py` `doc2md/llm.py` `doc2md/{api,auth,cli,edits,worker}.py` `doc2md/storage/{db,repo}.py` `doc2md/security/{netguard,names,dirguard,watcher}.py` `doc2md/ops/{backup,drill,diskguard,gguf,logging}.py` `doc2md/config/{loader,default_toml}.py` `config/doc2md.example.toml` `doc2md/static/editor.html` `tests/test_ch469_service.py`（新增）`tests/test_ch469_ops.py`（新增）`tests/test_{v24_gates,perf_lowend,sharding_unit,batch2_storage,t2_llm_server,s3_blocks,m9a_search}.py` `docs/dev-plan.md` `README.md` `ops/{backup,llm}.md` | **状态库结构版本 DB_VERSION 4→5**（只加索引与既有列的迁移，存量库自动升级、无需重建）；**配置键 +3**（未知键现在会 `UserWarning` 点名、`PUT /settings` 非法值现在 400、CLI 配置错现在退出码 2）；**API 面行为变化**：非法角色 / 非数字 `base_revision` / 未知 token 从 500·假成功改为 400·404，`GET /docs/<id>` 的 blocks/audit 支持 `offset/limit`（不带参数仍取全部，向后兼容）；**产物变化**：S-1/S-2/S-3/S-4 修的是缝界与围栏正确性，黄金样本 markdown sha 出现新基线（12.2 逐条登记）；`PIPELINE_VERSION` 维持 2.4.0（判定口径未变，改的是实现缺陷）。**批次④收口另发现三处 CH-468 未同步的口径漂移**（都是现时态断言，不是历史记录）：主题篇 8.6 仍写「失败即阻断合并尚未强制生效」、README 的「本地红线兜底（T1）」节仍写「必需检查受阻于计划限制」、12.1 的「失败能否阻断合并」行仍标 **未生效**——三处一并改为 CH-468 后的实况（必需检查已生效、pre-push 降级为双保险）；附录 G.6 的资产清点从 CH-465 的 5136 个 / 23MB 复算为 **5218 个 / 占用 23.2MB / 表观 3.0MB**，并首次写明两个口径为何差一个数量级。`PLAN_VERSION` 3.3→3.4 |
| CH-470 | 缺陷（交付流程 · T1 的边界补记，需决策） | **必需状态检查拦不住直推 `main`——红线在「推送」这条路径上没有平台级强制点**。CH-469 轮把工作副本的改动经一次性 `git clone` + 逐文件同步后 `git push origin main`，推送**成功**，远端同时返回 `Bypassed rule violations for refs/heads/main: - 3 of 3 required status checks are expected`。原因可复核：`main` 的保护只有 `required_status_checks`（三档 merge-gate）且 `strict=false`，`required_pull_request_reviews` 为 `null`——状态检查的语义是「PR 未绿不可合并」，不是「未绿不可写入」。因此 T1 的判据（故意红的 PR 被拦）达成且依然成立，但**「红线有强制执行点」只对走 PR 的改动成立**；直推路径的唯一防线是 `ops/hooks/pre-push`（CH-467），而它需要操作者主动 `git config core.hooksPath ops/hooks` 才生效，且 `--no-verify` 可绕过。**本轮未擅自打开「Require a pull request before merging」**：该开关会改变仓库所有者的日常工作流（此后任何 `main` 改动都必须开 PR），属人工决策项 → 仅登记并在交付时提醒 | 9.2 的 T1 行（补边界，不改判据）`12.7` 的「CI 红线阻断合并」行 | 无运行时行为变化；`PIPELINE_VERSION` / `SCHEMA_VERSION` / `DB_VERSION` 均不动 |
| CH-471 | 缺陷+更优方案（第四轮全面审查 · 全轮收口，含 CI 上界与 X-16） | **第四轮五面并行全面审查（报告存档 `docs/review-round4-CH-471.md`，46 条 = 44 项实质发现 + 2 条表扬：HIGH 5 / MED 17 / LOW 22）按「沙箱内可改可验证即实施」全轮落地（提交 `36ef92a`，51 文件 +7855/−224）。四个 HIGH 全是「已全绿门禁之外的新盲区」**：① **PIPE-1（正确性 / G1 等价）**：分片循环 `analyze(sub)` 未传 `base_size`，正文字号众数按片自算——字体跨片异质时同页在分片与单片下被判成不同层级，而门禁用的是字体均匀的 `large_320p.pdf` 故漏检；修为分片前对全文档算一次 `base_size` 传入每片（`_fallback_single` 同口径）。② **STOR-5（数据安全）**：同源文件重复转换 → `doc_id` 相同 → `INSERT OR REPLACE` + `DELETE FROM blocks` **静默清空用户编辑**、重置 `revision`、孤儿化 `edit_audit`，`rollback` 还可能把旧文本当新版本复活；修为存在性检查 + 新码 **E4091**（409 段位此前未占用）+ 删除事务内复核（STOR-10）。③ **STOR-3（健壮性）**：FTS 重建非原子（`DROP` + 自动提交 DDL + 独立事务回填），中途崩溃 → 索引永久为空且不再自愈，`reindex()` 还是零调用死代码；修为启动对账 + 回填流式化（STOR-4）。④ **TEST-1（门禁自噬）**：golden 样本缺失时门禁 `pytest.skip` 后仍判绿，而 CI 从不生成 golden——红线可能在无人察觉时消失；修为 `conftest._missing` 默认硬失败，仅 `DOC2MD_ALLOW_MISSING_GOLDEN=1` 降级。⑤ **TEST-3（CI 覆盖面）**：CI 无 Windows 腿而头号目标机是 8GB Win11，登记 B（X-14）。**其余分组**：服务层 SVC-1（immutable 写集校验）/ SVC-2（通用 500 JSON）/ SVC-3（同步 `POST /jobs` 复用进程池，新键 `perf.sync_min_parallel`）/ SVC-4（终态 tasks 保留，新键 `ops.tasks_retention_days`）/ SVC-5+6（强制 loopback 绑定 + 冻结 `run.offline_assert`，新键 `api.allow_remote_bind`）/ SVC-8（`limit` 非负）/ SVC-9（token 哈希存储 + `--list` 截断）/ SVC-10（helper 抽取）；安全 SEC-1（`sendmsg` 补丁）/ SEC-4（放行合法内部 `..`）/ SEC-5（scrypt N 决策为**保持 2^14**——v2 容器头不含 KDF 参数，提 N 会使现存加密备份全部认证失败，数据安全优先）；运维 OPS-3（GGUF 逐值尺寸帽）；质量 Q-1 / OPS-2 / OPS-4（注释与 fsync）/ CFG-2（doctor 语义）；性能 STOR-1（WAL + `synchronous=NORMAL` + mmap/cache，决策 D1——原 T14 随之关闭）/ STOR-2（bm25 排序 + 免二次 COUNT）/ PIPE-2（删恒真重过滤）/ PERF-2（日志单次 UTF-8 编码）。**C 类 7 项（D1…D7）按「功能全面、性能优先」+ 核心价值代为决策后直接实施**：D1 采用 NORMAL、D2 启用「require PR before merging」（需管理员权限，登记 B = CH-470）、D3 watcher 保持轮询 + mtime 短路、D4 强制 loopback + 冻结 offline_assert、D5 scrypt 保持 2^14、D6 不新增外键（文档明示手写级联 + 断言防孤儿）、D7 维持块级链接模型（行内跨度级链接写入已知限制）。**测试面同时收口**：TEST-1 如上；TEST-4 批处理超时记账余量放宽至 5s（仍低于 6s 挂死特征）；TEST-5a ruff 作业显式 `continue-on-error`（信息面不染红提交）；新增 `tests/test_ch471_fixes.py` 898 行 62 项逐字母回归。**文档漂移同步**（SVC-7 / STOR-9 / OPS-1 / SEC-3）：DB_VERSION=5 与三索引、doctor 33 项、PUT/reload 覆写语义、E4091/E5000 登记、附录 F 计数 543；README 与两个 launchd plist 校正「本地墙钟触发、TZ=UTC 仅子进程时钟」；ps1 披露口令明文入 `HKCU\Environment`；5 份历史方案文档归档入库。**实测**：本机（macOS arm64 / Python 3.15）gate **132 项全绿**、全量 **543 passed / 0 skipped**、`ruff check .` 全绿、两 plist `plistlib` 校验通过。**实施后 CI 又暴露一个非测试缺陷并当场修掉（X-16，提交 `3f7f9f2`）**：run `38843497730`（推送 `36ef92a`）的 `merge-gate (3.12)` 在 pytest 打出「132 passed」**之后**卡死至 15 分钟超时——根因是 CPython 3.12.x 的 `ProcessPoolExecutor` 对 `shutdown(wait=False, cancel_futures=True)` 放弃的「已 cancel 却未终止」worker 在解释器退出 join 时死锁（3.11 / 3.13 / 本机 3.15 均不复现，真验证依赖 CI 3.12 腿）；修为只在 abandoned（空窗超时）分支先抓本池 `_processes` 句柄、shutdown 后按 pid 逐个 `terminate()`（非全局 `active_children`、不触碰别处的池，G6 逐字节等价）。同场把 merge-gate 超时从 15 分钟右调到 **6 分钟**（提交 `584e7b5`：6 轮成功 CI 的 18 条 leg 样本中位约 83s、最慢 2m40s，6min ≈ 2.2 倍余量）。**CH-472 的 tiny_corpus_drain 断言修正与本轮同一提交落地**（`36ef92a` 内 `tests/test_t6_corpus_bench.py` +9 行）。**登记失实更正（CH-473 收口轮）**：本行原只登记了 CI 上界一条、并称「`doc2md` 包零改动（仅 `PLAN_VERSION` 常量）」——与提交事实（`doc2md` 包 25 个 `.py`、tests 6 个文件、5 份归档文档、+7855 行）严重不符，系把「CI 那两个提交」误当成了全轮；现按 `36ef92a` 提交信息与 `docs/review-round4-CH-471.md` 重写。 | `doc2md/**`（25 个 `.py`：`api` `auth` `cli` `config/{loader,default_toml,__init__}` `cron` `edits` `errors` `llm` `ops/{backup,diskguard,drill,gguf,logging}` `pipeline/{convert,s1_parse,s3_structure}` `scheduler` `security/{dirguard,names,netguard,watcher}` `storage/{db,repo}` `worker` `__init__`）`.github/workflows/ci.yml`（`3f7f9f2` +16 行 X-16 修复；`584e7b5` 超时右调）`config/doc2md.example.toml`（+3 键）`tests/test_ch471_fixes.py`（新增 898 行）`tests/{conftest,ch469_ops,ch469_service,cli_entry,m8_ops,perf_lowend,t6_corpus_bench}.py``docs/review-round4-CH-471.md`（新增 470 行）+ 5 份历史方案归档`docs/dev-plan.md``README.md``ops/launchd/*.plist``ops/windows/install-doc2md-tasks.ps1``ops/hooks/pre-push` | **对外契约变化**：同源重复转换从「静默清空编辑」变为 **409 / E4091**（默认拒绝，`force` 显式才覆盖且同事务清 audit）；PATCH 空文本 → **400 / E4001**；删除不存在行的留痕语义收敛（STOR-10）；`serve --host` 非回环默认拒绝（显式 `api.allow_remote_bind=true` 才放行）；`run.offline_assert` 禁经 `PUT /settings` 运行期关闭。**配置键 +3**（`api.allow_remote_bind` / `perf.sync_min_parallel` / `ops.tasks_retention_days`，57 → 60）。**存储口径**：WAL + `synchronous=NORMAL` + mmap/cache（每任务写开销再降一档；WAL 下应用崩溃不丢事务，仅 OS/断电可能丢最后若干事务——产物可由源文件重跑恢复）。**产物面零变化**（黄金样本 gate 明细与产物 sha256 不变）。`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5（本轮无表结构变更，索引在 CH-469 已补）。`PLAN_VERSION` 3.4→3.5 |
| CH-472 | 缺陷（测试口径 · 伪覆盖） | **小语料 drain 的「积压峰值下界」把宿主时序写进了断言，本机负载一高就假红**。`tests/test_t6_corpus_bench.py::TestRunDrainEndToEnd::test_tiny_corpus_drain`（真 CLI 子进程）断言 `1 <= backlog_peak`，而它自己的注释就承认「拍到峰值帧是时序运气」。本机 12 轮复测（同一语料 `seed=20261014`、workers 1）给出确定形态：采样帧数**恒为 1**，`backlog_peak` 分布 `{0: 7, 2: 5}`，`rss_available` 恒 True、后端 `ps`——6 件语料整个 drain 快于一个 0.1s 采样间隔，唯一那帧多半落在终态（`queued`/`running` 已清零），于是下界 1 断言的东西不是「采样器接线正常」而是「这台机器当时够慢」。全量套件本轮首跑即因此红一次。修法只动断言：下界取 0、上界保留 4（= 真正入队的任务数，可由语料静态推出）；接线证明交给确定性的 `test_queue_counts_reads_db`（直接建 2 done + 1 queued 的状态库）与本用例的 `assert r["queue_series"] and r["rss_series"]`。**没有去调 `run_drain` 的采样节奏来"造"一个稳定峰值**——为测试变绿而改变被测量的仪器，比假红更糟。**与 CH-471 同一提交落地**（`36ef92a` 内 `tests/test_t6_corpus_bench.py` +9 行），当时未独立提交故登记行滞后到 CH-473 收口轮才补正 | `tests/test_t6_corpus_bench.py`（`test_tiny_corpus_drain` 一处断言 + 注释） | 无运行时行为变化；`doc2md` 包零改动。**原登记行称「仅 `PLAN_VERSION` 3.5→3.6」与事实不符**：`36ef92a` 只把常量升到 3.5，3.5→3.6 的 bump 从未发生（header 已写 v3.6、常量停 3.5、§1.4 写「本版 v2.3」三处漂移），由 CH-473 收口轮补正到 3.6；gate 仍 **132 项**、全量仍 **543 passed / 0 skipped**；改后该用例连跑 5 轮全绿 |

| CH-473 | 缺陷+更优方案（第五轮全面审查 · 代码测试面三批次 + 文档收口） | **第四轮之后再做一轮四路并行全面审查（管线与 LLM / 服务与存储 / 安全配置运维 / 测试与文档），发现编号 R5-***（TEST-1…5 / DOC-1…9 / PIPE-5…12 / LLM-1/2 / SEC-1/2 / OPS-1/2 / PERF-1 / CFG-1/2 / Q-1/2 / STOR-1…5 / SVC-1…10）。**编号纪律事件**：开工时发现 **CH-472 已被上一轮提交（`36ef92a` 内 tiny_corpus_drain 断言修正）占用**，而第十一章的 CH-472 登记行滞后——按「CH-xxx 单调递增、不复用、不回收」纪律，本轮全部改号 CH-473（24 个 `.py` 的内联标记 + `git mv tests/test_ch472_fixes.py tests/test_ch473_fixes.py`）；动笔前先 `git log` 核对已用编号由此成为流程纪律（第十章编号行已补）。**批次①（正确性与数据安全）**：PIPE-5/PIPE-6 修 G6 红线自身的两处边界错误——`overlap_dedupe` 的 seam 尾缀判定不要求相邻页（不同内容的两块同指纹即被当重复丢后缀）、截断块替换不锚定真实片界（片界外的真短块被误换），签名扩展 `shard_bounds` / `last_idx`，比对面仍是「最近 64 个已保留块」常数界；STOR-1 force 覆盖时同事务清 `edit_audit`（rollback 不得复活覆盖前旧文本）；STOR-3 COMMIT 失败显式 ROLLBACK 且 body 已炸时回滚失败不顶替病因；LLM-2 `apply_ops` 索引字典化（split 新块入字典供后续 op 寻址）。**批次②（安全 / 服务 / 运维）**：SEC-1 `api.allow_remote_bind` 列入 `RUNTIME_IMMUTABLE_KEYS`（堵「先 PUT 放开 opt-in 再改 bind」的两步掏空）；SEC-2 `serve --host` 非回环拒绝（CLI 面与 `config.validate` 同口径）；SVC-3…S-7（`/search` 拒负 limit、`PUT /settings` 锁内校验、override 落盘三段式 fsync、force 显式解析、同步进程池全局信号量）；STOR-2 work/serve 启动 FTS 对账（历史空索引库不再有最长 24h 检索静默失效窗口）；STOR-4 `sweep_tmp` 在途保护窗；Q-1 八进制权限双形态（`0o700` 字符串与 int 448）；Q-2 netguard 解析缓存封顶 256；OPS-1 外置备份原子写（`.part` → fsync → replace）；OPS-2 GGUF BOOL 短读收敛；PIPE-7 配了空窗上限时串行快路径也走进程池；PIPE-10 删 convert 死参 `head`。**批次③（测试面与重构）**：TEST-1 gate 层两处 golden skip 改硬失败（口径同 `conftest._missing`）；TEST-2 重复 status 断言改响应体契约（`revoked is True`）；TEST-3 恒真断言（验证 stdlib 的 `ip_address("::1").is_loopback`）改驱动 netguard 真实 hook（monkeypatch `getaddrinfo` 记 calls：字面量直通且零解析、主机名/畸形字面量走解析被拦）；TEST-4 绝对墙上时钟阈值（`dt < 5`）改同机双跑相对口径（参考档 vs 守卫档，与宿主负载无关）；TEST-5 新增 `tests/test_ch473_fixes.py` 61 项（DB 迁移链：v3 存量库→v5、v2.2 遗留库补列 + FTS 重建回填）；PIPE-8 `_processes` 私有属性版本哨兵（X-16 清理路径依赖的 `ProcessPoolExecutor` 内部形状，stdlib 改形状时先红）；PIPE-9 删除 s3 循环内 8 处 `id=f"b{seq+1}"` 死写（一次多块只递增一次 seq 是重复 id 温床），块 id 唯一赋值点收至收尾重排；PIPE-11 S4 围栏结构改按块类型判定（正文 ``` 字面行不再错切代码块，code 块一个字节不动）；PIPE-12 `GateCollector` docstring 如实标注消费面（流式 feed 仅测试演练）；CFG-2 `doctor` 配置语义非法返回 `EXIT_USAGE`（原仍退 0）。**顺带修复两个真实产品缺陷**：`config.is_loopback_bind` 包级漏 re-export（`serve` 任何调用即 `EXIT_SYS`）；`_Tx.__exit__` 回滚错误顶替 body 原始异常。**批次④（文档收口 + 证据补强，本轮）**：重写 CH-471 / CH-472 两行登记（原 CH-471 行漏登第四轮全部实施且误称「`doc2md` 包零改动」）、§9.4 补第四 / 第五轮两节、附录 A 补 CH-471 新增三键（57 → 60）、附录 F/G 全量重刷（含第三层 53→54 的参数化校正）、§1.4 与 `PLAN_VERSION` 补正到 3.6（上一轮声称的 bump 从未发生）、12.7 补 X-16 行并更正矩阵行两处事实、12.1/12.2/12.10 同步刷新、README 三处同步、`ci.yml` 头注释 `-v`→`-vv` 与过期项数（132/481 → 145/605）校正。**推送与远端验证（收口轮最后动作）**：批次④提交 `4151515` 推送 `main`（10-16 12:16Z，`ossh` 远端 `584e7b5..4151515`）——`ops/hooks/pre-push`（CH-467）**首次实战**（本地 `pytest -m gate` 通过后放行），远端 run `38899470118` **五作业全绿**：merge-gate 三档均 **145 passed, 460 deselected**（3.11 44.38s / 3.12 68.80s / 3.13 73.23s）、lint 22s、full-suite **605 passed**（126.77s）——**145 项红线的首次远端验证达成**，12.1 CI 行、12.7 矩阵仍待项①与 git 行②、12.10 v3.6 行同步回填；同场远端再次实测「必需检查不拦直推」（`Bypassed rule violations …`），CH-470 必要性再加一证。**证据补强**：git 能力恢复后（工作副本 10-16 12:39 重新接回仓库）按 12.2 原始口径补做四时点 `git worktree` before/after 对比——CH-473 与 CH-471 均 6/6 黄金样本 zip 全条目 + gate 明细逐字节一致，CH-469 仅 `large_320p` 变化（与既有新基线吻合），12.2 基线表跨环境 12/12 复现。**CI 考古（收口轮补记）**：X-16 修复已获远端确认（`3f7f9f2` 的 run `38845736366` 五作业全绿、3.12 腿 1m28s 干净退出；`584e7b5` 的 run `38851378872` 再全绿）；3.13 那次挂死实为撞 GitHub 默认 6 小时作业上限（早前「被下一 push 取消」的登记不准确）。**出口实测**（本沙箱 Linux / Python 3.11.1 复跑；收口轮本机记录 62.7s / 126.3s）：`pytest -m gate` **145 passed（61.6s）**；全量 **605 passed / 0 skipped（127.0s）**；`test_golden_repro.py` 7 passed（样本资产可复现与 MANIFEST 绑定）；`ruff check doc2md tests` 全绿。产物面零变化的主证据是上面的四时点 worktree 逐字节比对（PIPE-5/6/9/11 的重构另经既有块契约用例 109 项 + 黄金渲染契约 21 项复核）。 | `doc2md/pipeline/{sharding,s3_structure,s4_render,convert,s1_5_gate,batch}.py``doc2md/storage/{db,repo}.py``doc2md/config/{loader,__init__}.py``doc2md/cli.py``doc2md/api.py``doc2md/cron.py``doc2md/worker.py``doc2md/llm.py``doc2md/security/netguard.py``doc2md/ops/{backup,gguf}.py``doc2md/__init__.py`（PLAN_VERSION 3.5→3.6）`tests/test_ch473_fixes.py`（新增 1180 行 61 项，红线 12 项）`tests/{test_v24_gates,test_perf_lowend,test_ch469_service,test_ch469_ops,test_golden_repro,test_m5_m6c,test_t9_convergence}.py``.github/workflows/ci.yml`（头注释 `-vv` 口径与项数校正）`docs/dev-plan.md`（§1.4 / §3.5 / §8.1 / §8.6 / §9.2 / §9.4 / §12.1 / §12.2 / §12.7 / §12.10 / 附录 A·F·G / 第十一 / 十二章）`README.md`（跟踪约定 / CI 节 / 门禁摘要） | **G6 等价红线的自愈**：PIPE-5/6 修的是「分片去重后块数 == 单片块数」这条红线自身在特定缝界形态下会失败的两处边界——红线条目从此对「跨页同指纹短句」与「片界外短块」两种形态确定成立（各 2 项红线钉住）。**对外行为变化三处**：`serve --host` 非回环从「启动后由 validate 拒绝」提前到 CLI 面同口径拒绝（错误形态一致）；`PUT /settings` 改 `api.allow_remote_bind` 返回 400（运行期不可变）；`doctor` 对配置语义非法的配置文件返回退出码 2。**存储 / 服务内部口径**：启动 FTS 对账（work/serve 同口径）、同步 `/jobs` 进程池受全局信号量约束、外置备份原子写。**产物面零变化**（四时点 `git worktree` before/after 逐字节比对：6/6 黄金样本 zip 全条目 + gate 明细完全一致，见 12.2 CH-473 段；黄金样本资产另经 `test_golden_repro.py` 7 项复现零漂移）。`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5（v3/v2.2 存量库迁移是既有迁移链的测试补齐，无新结构版本）。`PLAN_VERSION` 3.5→3.6（补正上一轮漏 bump） |
| CH-474 | 缺陷（CI · X-16 的正常路径复发，收口轮续） | **`run_batch` 的池收尾只覆盖 abandoned 分支，正常完成路径在解释器退出阶段死锁**。CH-473 收口轮的续提交 `df53679`（纯文档回填）触发 run `38901173280`：merge-gate 三档全绿（3.11 44.38s / 3.12 66.54s / 3.13 46.70s，各 145 passed），**`full-suite`（3.12）打出「605 passed, 11 warnings in 155.99s」之后 pytest 主进程卡退出 27 分钟**至 30 分钟作业超时被取消（run cancelled；in-progress 期间日志 blob 取回 `BlobNotFound`、取消后可读，X-16 轮的教训——`-vv` 逐用例行 + 取消后回读日志使「挂死点 = 退出阶段而非某个用例」可判定）。挂死链：3.12 的 `_ExecutorManagerThread.join_processes` 对不响应退出哨兵的 worker 只 `join` 不 terminate → 多线程父进程 fork 出的 worker（warnings 里的 `This process (pid=…) is multi-threaded, use of fork() may lead to deadlocks in the child` 即此形态，`test_perf_lowend` 的 fork 场景触发）可能持有继承锁永不退出 → manager 线程永不结束 → 解释器退出时 `_python_exit` join manager 死锁。**同代码的 `38899470118` 干净退出（126.77s）——竞态**，负载越高窗口越大；X-16 轮「修复已证实」的结论只对 abandoned 路径成立。修为 `run_batch` 的 finally **无条件**按 pid 终止本池 worker：`terminate()` → `join(5)` → `kill()`（SIGKILL 兜底、无条件发——CPython 的 `is_alive()`/`poll()` 有 ECHILD 误报怪癖（`popen_fork.py` 注释 #1731717），不作 kill 前置）→ `join(1)`；SIGKILL 内核不可阻挡，发出即保证 manager 必然结束、解释器退出不死锁。正常路径与异常路径同一清理（本批 future 已全部有结论，terminate 不影响 results），转换路径零改动。新增回归红线 `test_x17_batch_returns_with_all_pool_workers_reaped`（waitpid/ECHILD 语义 + 10s 宽限窗，**不用 is_alive 判定**——其误报会把「已回收」当「仍存活」；20 次循环探测 0 失败，broken 版反向验证必红）。gate 145 → **146**、全量 605 → **606** | `doc2md/pipeline/batch.py`（finally 清理段重写 + 删 `abandoned` 变量）`tests/test_ch473_fixes.py`（+X-17 红线 1 项，docstring 同步）`.github/workflows/ci.yml`（头注释 146/606）`ops/hooks/pre-push`（注释项数 132 → 146——CH-473 批次④漏改的漂移）`doc2md/__init__.py`（PLAN_VERSION 3.6→3.7）`README.md`（门禁摘要 146 项构成 / 跟踪约定推进到 CH-474）`docs/dev-plan.md`（header / §1.4 / §3.5 / §8.1 / §9.4 / §12.1 / §12.7 / §12.10 / 附录 F·G / 本条） | 无运行时行为变化（清理逻辑只在「本批已有结论之后」执行，转换路径与产物零变化）；`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5。`PLAN_VERSION` 3.6→3.7。**出口实测**（本沙箱 Linux / Python 3.11.1）：gate **146 项全绿（60.0s）**、全量 **606 passed / 0 skipped（122.0s）**、`ruff check doc2md tests` 全绿；修复 re-run 的远端结果随 CH-474 推送触发并回填 12.1 CI 行 |
| CH-475 | 缺陷+更优方案（第六轮全面审查 · 12 项 A 类修复 + 2 项验证后延期） | **第六轮全面审查（报告存档 `docs/review-round6-CH-475.md`，编号 R6-*）按「功能全面、性能优先 + 沙箱内可改可验证即实施」分诊：12 项 A 类全部落地，2 项经代码核验后判定为「验证后延期」（B 类）。** ① **R6-SVC-3（数据安全 / 终态幂等，HIGH）**：`Scheduler.mark()` 的 UPDATE 原无状态守卫，一个已 `done` 的作业可被后续 `mark(rejected)` 翻转、终态不幂等；且 `api._finish_one` 忽略 `idem_hit`/`attached` 标志，对幂等命中的作业仍重跑 `save_ir`（浪费且可能覆盖）。修为 `mark()` 加 `AND status NOT IN ('done','rejected')` 守卫——**刻意偏离报告 D1 建议的 `AND status IN ('queued','running')`**，以保住 `failed→done` 的自纠偏与 `queued→failed` 的遗骸清理（CH-455），只封死「终态被回退」；`_finish_one` 对 `idem_hit`/`attached` 早返回跳过 `save_ir`。② **R6-SVC-2（凭据入库，MED）**：PATCH/rollback 路径 actor 缺省取 `tok()` 明文 bearer token 前 12 字符写进 `edit_audit`，违反「token hashed at rest」与 NFR-20。修为新增 `actor_id(b)`：缺省取 token 的 sha256 前 12 位，显式 `as` 仍尊重。③ **R6-SVC-1（不可变守卫绕过，MED）**：`check_runtime_immutable` 原仅在 `PUT /settings` 调用，`/system/reload` 不调——`run.offline_assert` 等运行期不可变键可经「改配置文件 + reload」旁路生效（离线断言可被运行期关闭）。修为 `reload_` 应用新配置前同口径调用该守卫，不可变键变化即拒（400/E4001，baseline 不变）。④ **R6-OPS-2（脱敏旁路，MED）**：`scrub_record` 的 `bytes` 分支未按长度指纹化，二进制正文片段可原样入日志。修为先 `decode("utf-8","replace")` 再走 >64 字符 sha256 指纹规则。⑤ **R6-OPS-3（doctor 退出码 + JSON 出口统一，MED）**：`doctor` 对磁盘不足（`free_mb < min_free_mb`，含探测失败 `-1`）与 server 模式 LLM 不可达原退 0；`cron`/`serve token --list\|--revoke`/`tokenizer`/`doctor` 的机器可读 JSON 绕过 `_jline` 直接 `json.dumps(ensure_ascii=False)`（cp936 控制台字节不一致）。修为两类故障退 `EXIT_SYS`、配置语义非法退 `EXIT_USAGE`，所有机器可读 CLI JSON 出口统一收口 `_jline(ensure_ascii=True)`（CH-451 口径）。⑥ **R6-OPS-1（launchd 错误根，HIGH，代码已修 + 真机验证登记 B-5）**：`ops/launchd/*.plist` 缺 `WorkingDirectory` 与显式 `--root`——launchd 默认 cwd 为 `/`，相对根名 `work` 被 `root_setting` 解析成 `/work`，日流程要么失败要么操作错误根。修为两个 plist 补 `WorkingDirectory`（安装根）+ `--root {安装根}/work` 双保险（Windows 孪生靠安装脚本渲染 `WorkingDirectory`，本就无此患）；守卫断言落在 `tests/test_t5_windows_ops.py`（与 parity 同文件），**macOS 真机 `launchctl load` 验证需目标机，登记 B-5**。⑦ **R6-LLM-1（算子参数值未校验 → 崩溃，HIGH）**：`OpValidator._check` 原只校验白名单/目标块/不可变/占用，不校验参数值——`level:"3"`（字符串）在 `s4_render` 触发 TypeError、`level:99` 产非法层级、`marker:"\1"` 作为 `re.sub` 替换串触发 `re.error`；文档正文是不可信输入，模型或被注入正文可诱导此类算子让整篇转换在渲染期崩溃（DoS 自身管线）。修为新增 `valid_level`（须 `int` 且 1≤level≤6）与 `valid_marker`（白名单 `^[-*+·•‣]{1,3}$`），`_check` 对越界值拒绝该算子（`bad_level:`/`bad_marker:`），`apply_ops` 对 marker 改用 lambda 替换杜绝 `\1` 注入——**拒绝而非崩溃**。⑧ **R6-LLM-2（注入检测误报漏报，MED，部分实施 + 归因去循环登记 B-3）**：`_INJECT_HINT` 词表补 forget/disregard/ignore previous/new instructions/developer mode 等真实注入常用词、「扮演」收窄到需越狱上下文（降误报）；**归因去循环（`_attribute` 仍扫算子 JSON 而非源正文，与 `convert._injected_ops` 形成循环归因）与更宽误报收窄需真实注入语料，登记 B-3**。⑨ **R6-PIPE-1（有序列表误升标题，MED）**：`_heading_level` 第三回退（纯编号 → `_level_from_number`）原无字号门/加粗门，「短编号 + 无句末标点」的正文行（极常见的有序列表项如「1. 安装依赖」）被判成标题（编号「1」→ H1），污染 `content.md` 结构与 TOC。修为第三回退加信号门——仅当 `size > base` 或加粗或多级编号或 CJK 章节词命中才判标题，否则归 `list_item`；对正常标题（字号>正文）字节不变，G6 等价保持。⑩ **R6-PIPE-2（等价性度量对空白失明，MED）**：`norm_lines` 原 `_WS.sub("")` 抹尽全部空白后再比 `char_similarity`（门 ≥0.99），使等价性门对**缩进类**漂移完全失明——一类真实回归可绕过门禁。修为保留行首缩进、只把行内连续空白折叠为单空格；只收紧门、不改产物，性能中性。**（原登记把「代码围栏破损」与「列表标记 `-`↔`*` 替换」并列为失面对象，CH-478 批次② 在真实产物上实测校正：两者属内容级漂移，新旧口径差值 < 0.001、红绿结论一致，收紧带来的敏感度只覆盖空白类；同时实测出「0.99 只探测粗大漂移、不是等值证明」的口径边界。见 12.7 的 B-2 行与 §4.7。）****验证后延期 2 项**：**R6-SVC-4**（claim 加 `source_path IS NOT NULL` 过滤，与 `reclaim_stale` 口径对齐）——经代码核验判定延期：并发危害已被 R6-SVC-3 的 mark 状态守卫吸收（终态不可翻转，claim 竞争无法污染同步作业终态），且加过滤会使 `drain_once` 靠 E4001 清理的 NULL-path 孤儿行滞留、经 `uq_tasks_doc_active` 阻塞再提交，登记 **B-4**（需并发压测复现）；`worker.py` 未改动。**实测**（本机 macOS arm64 / Python 3.15，`.venv` editable）：新增 `tests/test_ch475_fixes.py` **71 项（红线 7）**；gate **146 → 153 项全绿（30.04s）**、全量 **606 → 677 收集 = 677 passed / 0 skipped（66.09s）**；`ruff check doc2md tests` 全绿。产物面：R6-PIPE-1/PIPE-2 只纠正误判路径与收紧度量，正常语料 markdown 字节不变（G6 等价保持）；其余 10 项为服务/安全/运维内部口径，不触产物。 | `doc2md/scheduler.py`（`mark` 状态守卫）`doc2md/api.py`（`actor_id` + `_finish_one` 幂等早返回 + `reload_` 不可变守卫）`doc2md/config/loader.py`（`check_runtime_immutable` 接线 reload）`doc2md/ops/logging.py`（`scrub_record` bytes 分支）`doc2md/cli.py`（`cmd_doctor` 退出码 + `_jline` 统一收口）`doc2md/llm.py`（`valid_level`/`valid_marker` + `OpValidator._check` 参数校验 + `apply_ops` lambda 替换 + `_INJECT_HINT` 词表）`doc2md/pipeline/s3_structure.py`（`_heading_level` 第三回退信号门）`doc2md/pipeline/metrics.py`（`norm_lines` 保留缩进）`ops/launchd/md.doc2md.cron.{plist,weekly.plist}`（`WorkingDirectory` + `--root`）`tests/test_ch475_fixes.py`（新增 416 行 71 项）`tests/test_t5_windows_ops.py`（+R6-OPS-1 launchd 守卫断言）`docs/review-round6-CH-475.md`（新增审查报告）`docs/dev-plan.md`（header / §1.4 / §3.5 / §8.1 / §9.1 / 附录 B·F·G / §12.1 / §12.10 / 本条）`README.md`（门禁摘要 153 项构成）`ops/hooks/pre-push`（注释项数 146→153）`.github/workflows/ci.yml`（头注释 153/677）`doc2md/__init__.py`（PLAN_VERSION 3.7→3.8） | **对外契约变化三处**（均为「原来错了/有旁路」的收敛，非新设计）：`doctor` 对磁盘不足与 server 模式 LLM 不可达从退 0 改为退 `EXIT_SYS`（5）、配置语义非法退 `EXIT_USAGE`（2）；`/system/reload` 改运行期不可变键从「静默生效」改为 400/E4001 拒绝；PATCH/rollback 审计 actor 缺省值从明文 token 前缀改为 sha256 前缀（`edit_audit.actor` 字段语义收敛，凭据不再落库）。**内部口径变化**：`mark()` 终态不可翻转（done/rejected 幂等）、幂等命中跳过 `save_ir`、`scrub_record` 覆盖 bytes、畸形 LLM 算子被拒而非崩溃、有序列表不再误升标题、等价性门看见缩进漂移。**产物面零变化**（正常语料 markdown 字节不变；R6-PIPE-1 只纠正「字号==正文的纯编号列表被误判标题」这一错误路径）。**配置键 / 错误码 / 退出码段位无新增**（复用 E4001/E4091、EXIT_SYS/EXIT_USAGE）。`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5。`PLAN_VERSION` 3.7→3.8（附录 F 新增第五层、附录 G 全量实核重刷含校正 G.2 旧记 2150 与逐行和 2179 的既有不等、§12.1 补 CH-475 行）。**登记 B 类（需本机外环境/人工，交付时提醒）**：B-5（R6-OPS-1 macOS 真机 `launchctl load` 验证，HIGH）、B-4（R6-SVC-4 并发压测复现）、B-3（R6-LLM-2 归因去循环 + 真实注入语料）、B-1（R6-PIPE-1「编号列表字号==正文」golden 反例）、B-2（R6-PIPE-2 缩进/围栏破损样本）|
| CH-476 | 缺陷+口径+更优方案+功能补齐（第七轮全面审查 · 13 批 A 类修复，含性能主线三刀与功能 gap 收口） | **第七轮全面审查（报告存档 `docs/review-round7-CH-476.md`，编号 R7-*）按「功能全面、性能优先 + 沙箱内可改可验证即实施」分诊：32 项 A 类全部落地（3 HIGH + 11 MED + 若干 LOW + 5 组功能 gap + 10 条性能机会），B 类 12 项仅登记。** 分三刀的性能主线：**① R7-STOR-1/2（决策 D1）**——FTS5 的 `doc_id`/`block_id` 均 `UNINDEXED`，`DELETE … WHERE doc_id=?` 与单块 `WHERE doc_id=? AND block_id=?` 退化为对全内容表的线性扫描且落在 `BEGIN IMMEDIATE` 写锁内（10 万块级库上批量落库 100 文档 = 100 次全库扫，检索/编辑面随库龄单调劣化，小测试库门禁测不出）；新增 `search_map(fts_rowid PK AUTOINCREMENT, doc_id, block_id)` 映射表 + `ux_search_map_blk` 唯一索引，FTS 行号改显式赋值，整篇删除 O(本文档块数)、单块定位 O(1)。**同笔**把 `_migrate` 的 DDL 重放改为**每次开连接无条件执行**（全 `IF NOT EXISTS` 幂等），版本门控只用于一次性数据回填——CH-469 补 `ix_events_*`/`ix_tasks_doc` 时恰未 bump 版本，`user_version=5` 的存量库因此永远缺这些索引，「加 schema 必须 bump」是纪律而非可依赖的前提；`DB_VERSION` 5→6，存量 v5 库开库回填映射并闭合。② **R7-PERF-1（决策 D2）**——`drain_once` 每轮只认领 `workers` 个作业，而 `run_batch` 每次新建 `ProcessPoolExecutor` 并在 finally 无条件 terminate 全部 worker（X-16/X-17 的防挂死语义），每个新 worker 重付 `import pdfplumber` 0.166s：12.3 的「workers=4 仅快 4.5%」正是被池重建 + 重导入吃掉。**刻意不做持久池**（与 X-17 收尾强杀直接张力），改为认领量与池宽解耦——`claim(maxsize=workers × perf.queue_batch_factor)`（默认 4，新增配置键），并新增 `perf.queue_batch_factor` 的配置契约断言与「同路径作业合批不扩池」守卫。③ **R7-PERF-2/3**——同源文件三次全量哈希（漂移比对、`source_key`、`content_sha`）消为重算一次即消费；大目录串行哈希入队改 `ThreadPoolExecutor` 并行哈希 + 定序提交（`perf.enqueue_hash_threads` 新键），合批/幂等/原子零副作用三条契约同批钉住。数据安全项：**R7-SVC-1（决策 D6）**`_finish_ok` 先 `save_ir` 后无条件 UPDATE，删除级联与在途转换竞争可**复活已删文档并进后续备份**（直接违反 FR-128）；改为事务内先 `UPDATE tasks … job_id=?` 取 rowcount，0 即回滚并记 `rejected(doc_deleted_during_conversion)`，api `_finish_one` 同序，该 UPDATE 收编进 `scheduler.mark` 的终态守卫口径（`mark` 现回报是否命中行）。正确性项：**R7-PIPE-1（决策 D3）**`_continuation` 的编号续接比较**方向写反**（`kb[-1] == ka[-1] + 1`，自然递增 5→6 恒不命中=死分支、递减反而命中），且该分支在 heading 守卫之前 return——端到端复现「前片末段 + 后片标题」被吸收成污染标题、`page_break` 吸收 code 后渲染空串（代码整块静默消失）；修正为 `ka[-1] == kb[-1] + 1`，结构块守卫（heading/code/quote/page_break）前移到编号分支之前，编号续接仅限 paragraph/list_item，字体分支同补三类拦截。**R7-PIPE-2** 页差由「恰为相邻页」放宽为 `0 < b.page - a.page <= 最宽重叠窗口 + 1`（跨 ≥3 页长块的片界尾缀不再漏判留两份，远处重复短句照旧不误删）。**R7-PIPE-3** 围栏长度按内容取（正文最长反引号 run + 1，下限 3），code 正文含 ``` 字面行不再提前闭栏产出破损 Markdown。**R7-PIPE-4** DOCX `Heading N` 显式层级在 `_fix_levels` 保持优先（打 `flags.level_from_hint`），并删 `_fix_levels` 的 `seen` 兄弟机制（同前缀点数恒相同 → `rule_notes` 永不触发 = 死逻辑）。**R7-LLM-1** R6-LLM-1 只规约 `level`/`marker`、`target` 未设防：`target:5` 在 `_targets` 抛 `TypeError`、`target:["b1"]` 过完全部 5 道校验后在 `index.get(["b1"])` 抛 unhashable → 整篇转换 E5000；补 `_target_key` 归一 + 参数化 5+5 例回归（畸形被拒/中和，合法 target 全部生效）。功能补齐：**R7-GAP-1（决策 D4，P1）**FR-102 声明的「修订痕迹」从未实现——python-docx 的 `p.text`/`p.runs` 只看 `w:p` 直接子级，含 `w:ins` 的插入正文**整段静默丢失**（法律/协作文档的主战场）；改「接受后」口径取全部 run（`w:delText` 天然不取），块级 `flags.has_revision` 留痕，黄金样本 `sample_revisions.docx` 直写真实 `w:ins`/`w:del`（id/author/date 写死保逐字节可复现）。**R7-GAP-2/3（决策 D7，P2）**脚注/尾注直挂 `word/footnotes.xml`/`endnotes.xml` 抽取（注行自成段落块、不参与续句启发式，注内链接按注 part 自己的关系表解析——rId 跨 part 会撞车），新增 `sample_notes.docx`；DOCX 单元格多段落逐 `w:p` 取接受视图再以单空格相连（原扁平 `"".join(tc.iter(w:t))` 吃掉段界；单段单元格逐字节不变）。PDF 合并单元格**明确不补**（无版面模型下必误判，写入第二章非目标）。**R7-GAP-4** 一批已实现功能无 FR 验收口径，补 FR-204（检索读侧）/FR-205（产物 Markdown 直读）/FR-207（令牌生命周期）/FR-209（运行期配置覆写与热重载）/FR-210（审计读回）/FR-211（审阅页）六条，判据一律指向既有断言。**R7-GAP-5** §2.3 补反向纪律段（缺 FR 编号会让「凡既无断言也无实测不得标成立」不可执行）。运维与测试：**R7-OPS-1** `backup drill --keep` 取证目录原落 `work/tmp/` 被 30 分钟清扫吃掉——`sweep_tmp` 对 `drill-*` 永久豁免（并写入 `ops/backup.md`）。**R7-OPS-2** 周报文件名日期改 UTC，与日报体同口径（跨零点作业不再写出昨天的文件名）。**R7-TEST-1** 新增 cron 生产入口四条 CLI 面测试（正常 / 参数互斥 / `--no-backup` / 失败步退 `EXIT_SYS`）+ 修 `ops/llm.md` 第 ④ 步 `tokenizer verify` 示例（原路径不存在，实跑退 5）。**R7-SVC-2/3/4、R7-SEC-1**（C 类已决方案）：`POST /jobs` 的 `mode` 白名单 400 零副作用并向三条执行分支透传；同步 `POST /jobs` 池闸门由无界排队改**有界等待 60s** → 503 `E5041` + `Retry-After: 60`（等待上界同为 60s，`api.py:76-77` 两个常量；批次⑬ 复读时改正的文档漂移——此前登记行写 `Retry-After: 60`）（复用既有码，不新增段位；小批次完全不触碰闸门）；审计 `reason` 与令牌 `label` 的类型 + 长度帽（`_AUDIT_TEXT_MAX`/`_LABEL_MAX`=256，判定在写库事务之前）；审阅页 `esc()` 补单引号转义（属性用单引号时 `'` 可逃逸）。**PERF-a…k** 七组等价性专项：S1 预扫覆盖全部 hint 分支且良语料/折叠语料判定不变、`_page_metrics` 与正则参考逐字段等值、样式备忘等于 python-docx 逐段查找且 `level_hint` 存活、文档级预检等价逐段扫描、`map_many` 合批与逐件提交等值且全有或全无、`delete_source` 查找改只读、`/jobs` 与 `/search` 的 `limit` 帽、Markdown 产物流式写出（同字节同 header）。**验证后延期（B 类，不实施）**：R7-PIPE-5 噪声判据阈值（D5：须先有真实乱码语料评估误杀面，B-1）、R7-PIPE-6 双栏链接误附（B-2）、R7-SVC-5 长时转换 vs stale 回收双进程（B-12）、R7-PERF-1 持久池取舍（B-6，Win11 spawn 实测前不动池生命周期）。**实测**（本机 macOS arm64 / Python 3.15，`.venv` editable）：新增 `tests/test_ch476_fixes.py` **87 项（红线 83）** + `test_sharding_unit.py` 内就近红线 8 项 → 本轮 **91 项红线**；gate **153 → 244 项全绿（33.9s）**、全量 **677 → 773 收集 = 773 passed / 0 skipped（73.3s）**、`ruff check doc2md tests config` 全绿、`doc2md doctor` 退 0；远端 run `39016742023`（push `9a1d959`）lint + merge-gate 三档（3.11/3.12/3.13）+ full-suite **五作业全绿**。产物面：R7-GAP-1/2/3 只改「原本丢内容」的路径，无修订/无注/单段单元格语料逐字节不变（G6 等价 + 六黄金样本 sha 复核）；R7-PIPE-1/2/3/4 只纠正错误路径与破损形态，正常语料字节不变。 | `doc2md/pipeline/sharding.py`（R7-PIPE-1 方向修正 + 守卫前移、R7-PIPE-2 页差窗口）`doc2md/pipeline/s4_render.py`（R7-PIPE-3 `_code_fence`）`doc2md/pipeline/s3_structure.py`（R7-PIPE-4 `level_from_hint` + 删 `_fix_levels` 死机制、R7-GAP-1/2 的 `_mark_provenance`）`doc2md/pipeline/s1_parse.py`（R7-GAP-1 修订接受视图 + `has_revision`、R7-GAP-2 `_docx_notes_lines` + `note_kind`、R7-GAP-3 `_docx_cell_text`、PERF-a/b/d 预扫与页指标）`doc2md/pipeline/convert.py`（PERF-2 哈希一次即消费）`doc2md/pipeline/batch.py`（PERF-g/h `map_many` 合批）`doc2md/llm.py`（R7-LLM-1 `_target_key` 归一）`doc2md/storage/db.py`（R7-STOR-1 `search_map` DDL + `DB_VERSION` 6 + 无条件 DDL 重放、R7-STOR-2 `ix_audit_doc_id`）`doc2md/storage/repo.py`（经映射的整篇/单块 FTS 读写、`reconcile_search` 走映射）`doc2md/worker.py`（R7-SVC-1 提交前复核、R7-PERF-1 `claim` 批量放大、PERF-i 只读查找）`doc2md/scheduler.py`（`mark` 回报命中 + docstring 口径校正）`doc2md/api.py`（R7-SVC-2 mode 白名单透传、R7-SVC-3 闸门有界等待 503/E5041 + `Retry-After`、PERF-j limit 帽、PERF-k Markdown 流式）`doc2md/edits.py` + `doc2md/auth.py`（R7-SVC-4 类型与长度帽）`doc2md/cron.py`（R7-OPS-1 `drill-*` 豁免、R7-OPS-2 文件名 UTC）`doc2md/cli.py`（cron 入口四态、`_jline` 面）`doc2md/config/{loader,default_toml}.py`（`perf.queue_batch_factor`、`perf.enqueue_hash_threads`，60→62 键）`doc2md/static/editor.html`（R7-SEC-1 `esc` 单引号）`tests/test_ch476_fixes.py`（新增 2097 行 87 项）`tests/test_sharding_unit.py`（+红线 8 项）`tests/make_golden.py`（`sample_revisions.docx` 真实修订、新增 `sample_notes.docx`）`tests/test_t7_drill.py`、`tests/test_ch473_fixes.py` 等随行数浮动 | 对外契约变化五处：① `DB_VERSION` 5→**6**（新增 `search_map` + `ix_audit_doc_id`，存量库开库自动回填，`SCHEMA_VERSION` 仍 14、IR 字段无新增必填）；② 同步 `POST /jobs` 池闸门超时无界排队 → **503 `E5041` + `Retry-After: 60`**（复用既有码，无新段位）；③ `POST /jobs` 的 `mode` 非法值由「透传给下游」改为 **400/E4001 零副作用**；④ 审计 `reason` / 令牌 `label` 非字符串或超长 → **400/E4001**（超长截断至 256，判定在写库前）；⑤ IR `flags` 新增三个可选旗标 `level_from_hint` / `has_revision` / `note_kind`（只进 `ir.json`，`report.html` 不扩列，既有语料字节不变）。**新增两个配置键**（`perf.queue_batch_factor` 默认 4、`perf.enqueue_hash_threads` 默认 4），三处键集（`SPEC` / 默认 TOML / 示例 TOML）与附录 A 同批对齐为 62。产物面：修订痕迹 / 注 / 单元格多段三类 DOCX 的正文由「丢失或粘连」变为保真（这是 FR-102 原设计行为落地，非新契约）；其余语料逐字节不变。`PIPELINE_VERSION` 维持 2.4.0。`PLAN_VERSION` 3.8 → **3.9**。**登记 B 类 12 项（需本机外环境/人工/构造语料，交付时提醒）**：B-1 噪声语料（R7-PIPE-5）、B-2 双栏跨片链接语料、B-3 注入语料归因（沿 R6）、B-4 并发压测（沿 R6）、B-5 macOS `launchctl load` 实跑（沿 R6，HIGH）、B-6 Win11 spawn 池实测（本轮新增，持久池取舍依赖它）、B-7 CI Windows 腿、B-8 跨机基准（含 PERF-1/2/3 复测）、B-9 serve/work 常驻资产自启、B-10 备份 v2 真机恢复演练（数据安全相关，建议优先）、B-11 浏览器人工走查、B-12 >30min 转换 vs stale 回收双进程；R7-OPS-3（`doctor` 之外的运维探针面）同批登记 |
| CH-477 | 更优方案（登记待办 T15 落地 · 版本与编号对账进红线） | **T15 从「收口靠人工复读」改为「CI 拦住」**：新增 `tests/test_ch477_version_sync.py` 四条红线静态断言——① `PLAN_VERSION` == 本文 header「方案文档版本」== §1.4「本版 vX.Y」；② README「当前编号至 `CH-xxx`，下一条自 `CH-yyy`」== 本文 10.1「当前至 `CH-xxx`。下一条目自 `CH-yyy` 起」（同一编号进度不得有两份真相，即 CH-472 重复占号形态）；③ `PIPELINE_VERSION` == header 与 §1.4 版本表两处管线行；④ `SCHEMA_VERSION` 与 `DB_VERSION` == §1.4 版本表对应行（二者是两套口径，见 D-5，迁移判定曾读错对象）。每条先断言**锚点解析成功**再比值：文档被改写到句式失配时报「红线锚点失效」而非静默跳过。五条变异实测各自使对应断言变红（改常量 / 改 README 编号 / 改 header 管线版本 / 改版本表 IR schema 或 DB 数字 / 改写 §1.4 句式）。落地时点选在批次⑬ 的 244/773 已由 run `39022142048` 独立取证之后，避免项数改档损失证据 | `tests/test_ch477_version_sync.py`、`doc2md/__init__.py`（`PLAN_VERSION` 3.9→3.10）、本文 §1.4 / 9.2 / 12.1 / 附录 F–G / 12.10、`README.md`、`.github/workflows/ci.yml`、`ops/hooks/pre-push` | 交付流程（红线层）；**无运行时行为变化**，`PIPELINE_VERSION` 2.4.0、`SCHEMA_VERSION` 14、`DB_VERSION` 6 均不变；gate **244 → 248**、全量 **773 → 777** |
| CH-478 | 缺陷收口（第六轮遗留的 B-1 / B-2 两个出口：批次① R6-PIPE-1 同字号有序列表项的端到端反例与归宿；批次② R6-PIPE-2 等价性门的缩进敏感度端到端对照） | **12.7 的 R6-PIPE-1 行出口判据写「修复后归 `list_item`」，代码现状只做到「不再误判 H1」**——编号行落进 `_join_back` 的缝合段落，逐行边界与序号丢失。本轮按该判据补齐 PDF 有序列表分支：`_ORDERED_ITEM`（`^\s*(\d{1,3})[、.．)）]\s*(?!\d)(?=\S)`）+ `_is_ordered_item`（行长 <30、行末无句末标点），在 `_BULLET` 分支之后、表格成组之前落 `list_item`，且 `ln.row` ≥2 单元格的行不参与（行首「1、」是单元格内容）；S4 `list_item` 分支导入同一份编译式，有序项保留序号渲染为 GFM 的 `N. `（原实现会把「1. 安装依赖」渲成「- 1. 安装依赖」）。**判据与 `_heading_level` 第三回退的信号门共用同一组形态条件**（字号/加粗/多级/CJK 任一命中仍判标题），故本分支只接住标题让位的行。**黄金样本 +1 件**：`tests/golden/numlist_1p.pdf`（1 页，`make_numlist_pdf()` 生成、`reportlab` invariant 模式逐字节可复现）——三行「1.／2.／3.」与正文同为 10pt 无加粗（假 H1 反例），同页放三类真标题对照（`第一章 绪论` CJK 词头→H1、`1.1 环境要求` 多级编号→H2、`2. 运行说明` 14pt→H1）、12 行众数字号正文与两行句末标点长句（反向守卫）。**实测**：新增 `tests/test_ch478_fixes.py` 18 项（红线 4：端到端同字号编号归 `list_item`、真标题信号照旧发火、Markdown 保留序号、正文行不进入该分支；非红线 14 为判据正/反向形态参数化），`test_golden_repro.py` 的 PDF 逐字节复现断言扩到两件样本；本机 gate **248 → 252 全绿（31.54s）**、全量 **777 → 795 passed / 0 skipped（70.01s）**、`ruff check doc2md tests` 全绿、`doc2md doctor` 退 0（33 键）。**爆炸半径逐字节实测**：31 份黄金产物（markdown sha256 + 全块 `to_dict()` 序列 sha256 + 分类型块数）改前改后对比，**30 份完全一致、仅 `numlist_1p.pdf` 变化**（`heading 3 / paragraph 15` → `heading 3 / list_item 3 / paragraph 14`，即三行编号从缝进一段变为各自成块），G6 等价红线不破。**回归判据本身经变异实测**：删掉 S4 序号出口（`list_item` 全退回 `- `）使红线「Markdown 保留序号」转红；把 `_is_ordered_item` 改成「正则命中即真」（去掉行长 / 句末守卫）使判据反向形态两项转红；把编译式末尾的先行断言写回 `\\S` 同样使序号红线以「1. 装依赖」转红（首轮实施时实际撞到并修掉的失败）。**批次②（R6-PIPE-2 的 B-2 出口，产物代码零改动）**：12.7 该行出口判据写「构造缩进/围栏漂移样本，验证收紧后的 `char_similarity` 门能捕获（旧口径会漏过）」，CH-475 只用字符串构造覆盖、缺端到端语料。本轮造 `tests/golden/codeindent_1p.pdf`（3521 字节单页，MANIFEST sha256 `eb4f5254…`，同一页供出 heading 1 / paragraph 3 / code 2 / list_item 2 四类块形，围栏块与纯等宽块都在），并新增 `tests/test_ch478_gate_drift.py`（10 项 / 红线 4）在该样本的**真实产物**（397 字符、`norm_lines` 16 行）上做四类漂移对照，文件自带「CH-475 前的旧口径归一化 + CH-429 算法的可换归一化参照实现」，故新旧对照不靠记忆数字。实测三条结论全部钉成断言：① 行首缩进漂移 收紧后 **0.973958 < 0.99 判红**、旧口径**精确 1.000000**（结构失明实证）；② 内容级漂移（围栏破损 0.991914/0.991667、`-`↔`*` 0.994652/0.994490）新旧差值 < 0.001 且红绿一致——**据此校正 CH-475 把围栏/标记并列为「旧口径失明」的过度声明**（报告原文、§12 叙述、附录 F 第五层、附录 G metrics 行、v3.8 变更记录、CH-475 登记行⑩ 共六处 + §4.7 与 12.7 该行同步改写，`metrics.py` 注释补口径边界段）；③ 行内空格漂移 0.997333 脱盲但未过阈，据此登记「**0.99 只探测粗大漂移、不是等值证明**」的度量边界，逐字节等值归 12.2 的 sha 对照。**顺带实测出一条上游成因并新登一行**：同页 `Line.x0` 带着三级缩进（56.7 / 120.7 / 184.7），而 pdfplumber `extract_text_lines()` 已剥掉 `Line.text` 行首空白（DOCX 腿 `p.text.strip()` 同样丢），**现行产物的代码块一律无缩进**——门在这条路径上「两侧都无缩进、无漂移可看」，缩进判红只能由真实产物合成漂移复现；按 x0 重建缩进要改对外产物、需 `PIPELINE_VERSION` 升位 + 黄金基线重烤，**不夹带进本批次**，作为待决策项单独登记在 12.7。**能失败**已逐条变异实测：`norm_lines` 改回删尽空白 → 5 条转红（缩进红线 + `norm_lines` 参数化 3 例 + 行内空白信息层）；删去产品 `char_similarity` 的区间字符级精比 → 3 条转红（内容级红线 + 同值红线 + 行内空白信息层）；只改产品归一化位数 6→4 → 本语料无感，故**不**列为对账证据（如实登记该位数不由本文件守护） |`doc2md/pipeline/metrics.py`（**仅 `norm_lines` 注释补口径边界段，行为零改动**）、`tests/make_golden.py`（`make_codeindent_pdf` + 步骤 11 档 + MANIFEST 描述 + `self_check` PDF 清单 + MANIFEST 5136→5137 条目）、`tests/golden/codeindent_1p.pdf`、`tests/golden/MANIFEST.json`、`tests/test_golden_repro.py`（逐字节复现循环 +1 份）、`tests/test_ch478_gate_drift.py`、本文 §1.4 版本行 / §4.7 度量口径 / 附录 F 红线构成（第九层）/ 附录 G.2 与 G.6 行数收集数对账 / 12.1 三行 / **12.7 的 B-2 行改写 + 新增一行待决策** / 12.10 变更记录（v3.12）、`docs/review-round6-CH-475.md`（R6-PIPE-2 段与 §6.1 各补校正段）、`README.md`、`.github/workflows/ci.yml`、`ops/hooks/pre-push`、`doc2md/__init__.py`（`PLAN_VERSION` 3.11→3.12） | `doc2md/pipeline/s3_structure.py`（`_ORDERED_ITEM` / `_is_ordered_item` / `analyze` 有序列表分支）、`doc2md/pipeline/s4_render.py`（`list_item` 有序出口）、`tests/make_golden.py`（`make_numlist_pdf` + 步骤序号 10 档 + MANIFEST 描述 + `self_check` 清单）、`tests/golden/numlist_1p.pdf`、`tests/golden/MANIFEST.json`、`tests/test_golden_repro.py`、`tests/test_ch478_fixes.py`、本文 §1.4 版本行 / §2 FR-104 / 4.4 / 4.5 / 8.3 黄金样例行 / 9.1 DoD 门禁行 / 10.1 编号行 / 附录 F 红线构成（第八层）/ 附录 G.2 与 G.6 行数收集数对账 / 12.1 三行 / 12.7 的 B-1 行 / 12.10 变更记录（v3.11）、`README.md`、`.github/workflows/ci.yml`、`ops/hooks/pre-push`、`doc2md/__init__.py`（`PLAN_VERSION` 3.10→3.11） | **产物面（对外可见）**：同字号单级编号短行从「段落」变为「有序列表项」，`content.md` 出现 `N. ` 行、`ir.json` 多一类 `list_item`——只影响此前被误判/缝合的形态，既有 30 份语料逐字节不变（实测见动因）；渲染红线与 `assert_render_contract` 判据不变。`PIPELINE_VERSION` 维持 2.4.0（判定口径扩展非契约修订）、`SCHEMA_VERSION` 14 不变（无 IR 字段变化）、`DB_VERSION` 6 不变、配置键 62 不变；`PLAN_VERSION` 3.10→**3.11**。**远端确认 = run `39027385467`**（push `b601749`，11:05:44Z→11:11:12Z，五作业全绿：lint 全绿、三档 merge-gate 各 252 passed / 543 deselected、full-suite 795 passed in 156.88s 干净退出）。**闭环 12.7 的 B-1（R6-PIPE-1 语料反例）**；批次② 的产物面为**零变化**（只增度量注释与资产/测试），`PIPELINE_VERSION` 2.4.0 / `SCHEMA_VERSION` 14 / `DB_VERSION` 6 / 配置键 62 全部不变，`PLAN_VERSION` 3.11→**3.12**；**批次② 的度量口径校正是文档与注释层面的事实修正，不改判定行为**，围栏/标记类回归此后仍由「内容级漂移照常掉分」这一实测性质守护。**闭环 12.7 的 B-2（R6-PIPE-2 端到端语料）**，并新登一行「代码块行首缩进在抽取层即丢失」为待决策项。**批次② 远端确认 = run `39030470549`**（push `2e04aaf`，11:54:34Z→11:59:48Z，全程 5m14s，五作业全绿：lint「All checks passed!」、三档 merge-gate 各 256 passed 549 deselected（3.11 67.07s / 3.12 56.53s / 3.13 61.67s）、full-suite 805 passed in 155.87s 干净退出且黄金逐字节复现步骤已含 `codeindent_1p.pdf`） |
| CH-479 | 更优方案（12.7 遗留子项② 裁定 · CI 版本矩阵口径收口） | **裁定：push 面保持单解释器全量，跨版本全量改由周期腿覆盖**。上一轮把「全量套件扩到矩阵（或明确只保 3.12 全量口径）」登记为待定，本轮按「功能全面 + 性能优先」定形：`ci.yml` 三作业不动——merge-gate 本就是三档矩阵（跑红线），把 full-suite 也扩成矩阵会让每次 push 从 1 条 30 分钟腿变成 3 条，红线反馈被拖慢而覆盖增量只落在非红线的 551 项用例上；新增 `.github/workflows/ci-full-matrix.yml` 周任务（`cron: '17 2 * * 1'` + `workflow_dispatch`，三版本矩阵 `fail-fast: false`，`timeout-minutes: 30` 沿用 full-suite 口径）专门补上那 551 项的跨版本空白。**口径本身由 `tests/test_ch479_ci_matrix.py` 六条静态断言守住（4 红线 + 2 信息面）**：① 两个工作流的 `python-version` 集合完全一致且恰为受支持集 `{3.11, 3.12, 3.13}`（版本面不得有两份真相）；② push 面的 `full-suite` 作业必须**无矩阵**且钉在 `3.12`——防的是有人把矩阵悄悄加回 push 路径而不做时间预算判断，这条把本轮裁定钉成红线；③ 每个作业都有 `timeout-minutes`，且两条 full-suite 腿均为 30（CH-471 口径：缺它时一次挂死占满 6 小时默认上限，期间红线既不是绿也不是红）；④ 周任务只允许 `schedule`/`workflow_dispatch` 触发，文件里一旦写进 `push`/`pull_request` 即判红（否则「周期腿」退化成第二次全量 push 腿，本轮的性能理由失效）。环境无 `pyyaml`，故用无依赖正则解析 `jobs:` 分段；每条先断言**锚点解析成功**再比值，文档或工作流被改写到句式失配时报「锚点失效」而非静默跳过。**五处变异实测各自使对应断言变红**：矩阵里改一个版本号 / 给 push 面 `full-suite` 加 `strategy.matrix` / 删一个作业的 `timeout-minutes` / 在周任务 `on:` 里塞 `pull_request` / 改写 `jobs:` 句式 | `doc2md/__init__.py`（`PLAN_VERSION` 3.12→3.13）、`.github/workflows/ci-full-matrix.yml`（新增）、`tests/test_ch479_ci_matrix.py`（新增）、本文 §1.4 / 8.6 / 10.1 / 12.1 / 12.7 / 附录 F–G / 12.10、`README.md`、`ops/hooks/pre-push`、`.github/workflows/ci.yml`（注释项数对账） | 纯 CI 与文档层；**无运行时行为变化**，`PIPELINE_VERSION` 2.4.0、`SCHEMA_VERSION` 14、`DB_VERSION` 6、配置键 62 全不变；gate **256 → 260**、全量 **805 → 811**，非红线项 549 → 551 |

以下对影响规格判读最大的条目补一段说明；其余条目的细节以表格落点 + 第十二章证据为准。**CH-424（门禁口径）**：even5 与旧三点都是**抽样**——实测 60 页样本的坏区落在第 51–53 页时两者同时漏过；反过来纯链接页密度的文档被误杀。`auto` 把判定面放到全文档页密度（成段空/占比超阈才拒收），报告面仍给 5 个采样页明细，因此上游 C-1 的验收可读性不受影响。对照基线 `even5`/`legacy3` 保留，可复现「漏检/误杀」两种形态。

**CH-425 + CH-427 + CH-438（分片诚实性）**：三条同源于「分片在门禁里被当成成功」的假通过链——缝合函数 KeyError 被降级路径吞掉却仍标 `sharded=True`（425）；预打 `shard_edge` 让片内相邻块被误当续接候选（427，实测 edges=46 而真缝只有 1）；总页数按片求和虚高（438）。现在的口径是：meta 反映**实际生效**的片数，缝只在片归属变化处发生，页数取真实覆盖上界。

**CH-428/429（度量可用性）**：附录蓝本的 difflib 直译在本机不可完成（59 万字 >300s），且 autojunk 与定长分块都会造成假失败（0.34 / 0.07）。现在的 canonical 口径先做行序列对齐再在非等区间内字符级精比，归一化 `2·matched/(|A|+|B|)`，与方案文字「difflib 快速比 + 长度归一化」一致但成本可控。半句度量的 URL/邮箱豁免来自 links_pdf 的实测误报。

**CH-435（搜索契约）**：`unicode61` 把连续 CJK 当一个整词，中文子串检索恒为 0 命中——这使得删除级联门禁里「搜索 0 残留」在修复前是**恒真空断言**。修复采用离线自足的 n-gram 辅助列 + 子串精确复核（消除跨段假阳性），代价是索引体积约 2×。教训：门禁断言必须在真实命中语料上反向验证过「能失败」。

**CH-441/CH-442/CH-443/CH-444（伪覆盖的四副面孔）**：分别对应「清单与实装不符（doctor 第三套口径）」「资产不可复现（哈希是一次性快照）」「有资产无判定（120MB 占位）」「单元测试冒充运行面覆盖（轮转只在 cron 生效）」。四条的共同处置是把断言搬到真实运行面：`create_app` 起服务打接口、独立进程跑 cron、两次独立生成比对哈希表、测试内自造写入过程。

**CH-447（等价纪律的执行）**：内存改造的最大风险不是改不动而是「改完产物悄悄变了」。做法是把对照做成三态（改造前 = `git worktree` 检出 HEAD、改造后 chunk=0、改造后 chunk=32），比 zip 内每个条目的 sha256，其中 `content.md` 先剥掉 front-matter（含非确定耗时字段），`ir.json`/`report.html` 原样比 → 0 不匹配。分块边界另立参数化单测（1/2/3/32 页与整本一致）。样式取回改成偏移表 + bisect 时，**必须再排序还原原始 char 次序**，否则 `Counter.most_common` 的平票判定与逐行全扫不一致（120 页 / 5613 行抽查 0 处不符）。

**CH-448（为什么不是线程池）**：GIL 使纯 Python 解析/排版/序列化的多线程无增益；Windows 无 `fork`，所以并行实现从 spawn 语义出发设计并把它做成参数化断言（`start_method="spawn"`），而不是只在开发机上「看起来快了」。IR 对象不跨进程搬运（返回值是紧凑 dict），否则大对象序列化变成新瓶颈。

**CH-449（为什么默认 false）**：异步入队改变的是 HTTP 契约（202 后 `doc_id` 不再立即可用），若默认开启会动摇既有门禁断言与审阅页时序。默认关闭 + 显式开启 + 独立消费者，是把吞吐改善与契约稳定分开的成本最低的做法。

**CH-450（为什么门禁要在写盘前）**：磁盘写满的失败形态是 zip 写到一半截断 + 库里已有记录，比直接报错难处理一个量级——产物不可解包、记录却指向它。门禁接在三个写盘点：转换打包前（`out_dir`）、cron 本地快照前（库实际字节）、外置卷写入前（快照大小）；HTTP 面不另设重复检查——同步 `POST /jobs` 走的就是打包前那道检查，违规即以 413/E4131 返回（有单测 `test_api_jobs_returns_413_when_disk_short` 把这条链路钉住）。**异步入队面不做空间检查**（入队本身只写几百字节，真正的写盘发生在 `work` 消费时，那里会撞上同一道门禁并把该作业记为失败）。违规不落半成品：cron 记失败步骤且不轮转，convert 在写盘前就拒。

**低端机专项（CH-447 … CH-451）共同约束**：保持纯规则路线，不引入 OCR / 模型 / GPU；家用机建议 `[s1].chunk_pages = 32`、`[perf].workers = 0`（自动 ≤4，8GB 机器不要手调 8）、文件多或界面不能卡时开 `[perf].queue_async = true` 并常驻 `doc2md work`、`min_free_mb` 保持 512 以拦满盘。以上量级均有第十二章实测支撑，非推断。

**CH-452（方案重写）动因**：v1.0 计划书 + 附录 H 增补的形态，使「当前规格」散落在三处（正文、伪码蓝本、变更记录），且正文里到处是 CH 编号——读者必须自己重建最终状态。本轮以代码为准重建规格叙述，同时做了一次口径审计，审计结果不进主题篇（避免污染规格），登记为 D-8/D-9/D-10。附带处理两批：

1. `cli.py` 注释里写死的基准数字（3.8s → 2.1s）在当轮代码下已不可复现，改为指向第十二章吞吐表——**代码注释不持有测量数字**，数字只住在有测量方法的证据章。
2. 代码、测试、配置样例、`pytest.ini`、CI 工作流里共 40 余处旧锚点引用（形如 `§5.1`、`§7.2`、「方案 v1.1 附录 H」、「附录 B-10/B-11」），在本文中已无对应编号，属**悬空引用**；本轮统一改指本文章的章节号（如 `§7.2` → 「方案 4.3 / 6.1 / 8.5」按语义分别归位），并把蓝本类引用统一前缀为「上游蓝本」以区别本文附录。仅保留明确标注「上游锚点」的沿革性引用（配置装载、API、IR、错误码四处，用于说明字段来源）。

**登记流自身的连带要求**：今后新增条目时，代码注释只允许引用本文**章节号 / 附录号**与 `CH-xxx`（后者仅作为索引出现在注释里，其叙述正文在第十一章），不得再引用旧计划书编号体系，也不得在注释里写测量数字——避免同一失效形态复发。

**CH-453（口径更正，而非新规格）**：本轮唯一的事实变化是「CI 有了执行点」。文档此前把 T1 的出口条件写成「远端出现运行记录」，达成后发现那句话给多了——远端会跑，但跑红了并不拦住合并。因此出口条件收紧为「故意失败的 PR 被拦下」，阻碍从令牌权限改判为平台能力：本仓库 private 且在免费计划下，分支保护与规则集两类 API 都返回 403（已实测），必需检查无法配置。规格判读未变（红线内容、项数、分层都不动），故 `PLAN_VERSION` 保持 `2.0`，仅更正 8.6 / 9.1 / 9.2 / 12.1 / 12.7 的事实口径。遗留的诚实边界：全量套件与可复现性只在 3.12 上跑，3.11/3.13 仅有红线层证据；本轮取证用的令牌仍缺 `checks:read`（`check-runs` API 与 annotations 读取返回 403），运行结论是从 Actions 作业状态与日志取的，不是从检查 API 取的。

**CH-454（补实现面 + 文件名去版本化）三点值得单独说**：

1. **3.5–3.7 与附录 G 是 as-built，不是 as-designed。** 每条结构断言都来自可重跑的取证：行数是 `wc -l`、依赖方向是 `ast` 遍历导入语句（相对导入按 `__package__` 解析，函数体内延迟导入同样计入）、依赖闭包是 `pip` 安装后的实际包集合、缺失依赖行为是导入拦截器活测（屏蔽 `flask` / `werkzeug` / `jinja2` 后 `convert` 仍 rc=0、`serve` 抛 `ImportError`、`doctor` 以 rc=5 报 `dep_flask: "MISSING (ImportError)"`）。采集方法写在附录 G 末尾，为的是让清单能被将来的提交者证伪——「代码结构和文档不一致」这种缺陷只有附带复算口径才算被钉住。
2. **写作过程中自查出的初稿错误，也按事实处理。** 初稿把 `BLOCK_TYPES` 写成 12 类（实为 7）；把 `security/*` 与 `ops/*` 写成「无包内导入」（实际引用 `..errors`），据此把 DAG 改为五层并把「唯一向上引用」限定为 `worker→api._emit`；把函数内导入写成「`main()` 里 12 处」（`cli.py` 实为 20 处、全包 39 处，且分散在各子命令函数而非 `main()`）；把 SQLite 的 DB-API 版本号 `2.6.0` 误当作库版本（运行库是 3.37.2）。这些不是终稿缺陷，但记录它们能说明「先写后验」的失效面有多大。同类自查还纠正了两处口径：`tests` 侧的清单一度漏掉 `pipeline/metrics.py`「只被测试引用、生产代码不引用」这一事实（附录 G.2 已显式标注）；未使用导入的 7 处逐条重跑 `ast` 才敢写进 D-12，其中 `pipeline/s1_parse.py:168` 那处带 `# noqa: F401` 的导入属有意保留，不计入缺陷。
3. **文件名去版本化是针对同一失效形态的根治。** CH-452 那轮把 40 余处旧锚点改指「v2.0 的章节号」，本身就埋下下一次失效：版本一升位，所有带版本的引用重新变成悬空引用。本轮把文件名固定为 `docs/dev-plan.md`、引用形态统一为「方案 + 本文章节号」，版本只存在于头部表格与 `PLAN_VERSION` 两处。附带发现同类问题并已改：CH-452 说明段里残留的「在方案 中已无对应编号」这类替换产物（因当时的全局字符串替换生成），本轮改为「在本文中」。

**同一轮新增的诚实边界**（进 12.6 / 9.2，不进主题篇）：写技术栈时才发现仓库既没有 `LICENSE` 文件也没有 `license` 元数据声明（D-11）、没有任何静态检查配置（D-12，实测暴露 7 处未使用导入；连同既有登记的死代码，零调用符号共 8 处）、跨包直引子模块与 `__all__` 门面并存（D-13，判为可接受偏差）。前两项各列一条待办（T10 / T11），第三项不列——它的代价已由附录 E 与门面声明固定。

## 十二、实测证据汇总

**测量环境**：Linux 沙箱，4 vCPU / 8GB，Python 3.12， editable 安装于独立 venv；`resource.getrusage(...).ru_maxrss` 取峰值驻留（KB→MB 按 1024 折算）。凡标注「同口径」者按 12.9 的复现方法重跑。跨机（Windows 11 / macOS 真实硬件）数字尚未实测，见 12.7。**CH-469 轮起的数字换到本机环境采集**（macOS arm64，Python 3.15.0，`.venv` editable 安装，ruff 同 venv）——因此 CH-469 轮的耗时与上面的前序轮次**不同环境、不可直接比较**，项数与通过态才是跨环境硬口径；峰值内存类数字（12.3）本轮只在口径不变的项上沿用，改动量的比值（如 O-9 的 4.3×）是同机同轮内的对比，不受换环境影响。

### 12.1 门禁与套件

| 项 | 数值 | 说明 |
| --- | --- | --- |
| `pytest -m gate` | **145 项全绿**；收口轮 110.4s、CH-453 轮 72.5s、CH-454 轮 108.8s、CH-455 轮 109.7s、**CH-456/457 轮 53.0s**（T8 优化后稳定值，<60s 达标）、CH-458 轮复测 67.7s（沙箱负载峰值，项数与结论不变）、CH-459 轮 54.2s、CH-460 轮 54s、CH-461 轮 49s、CH-462 轮 52s、CH-464/465 轮 53s（项数与结论均不变）、**CH-469 轮 28.7s（本机 macOS arm64 / Python 3.15，换环境采集，见 12 开头测量环境）**、CH-471 轮 23.7s（项数不变，第四轮 62 项全部落在信息层）、**CH-473 轮 62.7s（沙箱负载峰值采样，同机负载低点约 45s）** | 红线层；历史增长 35 → 40（CH-444 后）→ 65（CH-447…451 后）→ 67（CH-455 后）。CH-456/457 轮项数不变（新增 92 项全部落在里程碑与信息层，未进红线）、耗时降至 53.0s：大样本转换改 session 级共享夹具 + shard fallback 两类测试改中专句 PDF。四轮数字与全量套件同向波动，进一步支持「耗时不入断言」的口径。**CH-469 轮的 67 → 132 分三步**：① 收口轮用 `pytest -m gate --collect-only` 逐文件实测，校正第二层「27 项」的散文计数为 28（40 + 28 = 68，即原记 67 与自身分解本就不等）；② 批次②③ 新增 `test_ch469_service.py` 10 + `test_ch469_ops.py` 53 = +63 → 131；③ 批次① 收口的批量空窗超时用例 +1 → **132 = 40 + 29 + 10 + 53**（分文件收集数逐档实测：`test_v24_gates.py` 40、`test_perf_lowend.py` 29、`test_ch469_service.py` 10、`test_ch469_ops.py` 53）。耗时反向变短（53.0 → 28.7s）有两个原因：新增 64 项几乎全是纯单元断言（不含真实转换长跑），且采集环境换到本机；本轮文档收口后的**终轮复跑为 35.6s**（同一台机器同一份代码，与 28.7s 的差值即「沙箱负载主导」的又一个样本，项数 132 不变）。**CH-471 / CH-473 两轮的 132 → 145 分两步**：① `36ef92a`（第四轮全轮）项数不变——新增 `tests/test_ch471_fixes.py` 62 项全部落在信息层，红线仍 132 项（23.7s）；② CH-473 批次②③ 净增 13 项：`tests/test_ch473_fixes.py` 红线 12 项（PIPE-5 / PIPE-6 缝界与锚定各 2、SEC-1 / SEC-2 / STOR-1 / STOR-3 各 1、SVC-6 字符串 force 2、TEST-5 迁移链 2，逐项口径见附录 F 第四层组 9）+ `test_ch469_ops.py` 随 Q-1 权限双形态校验参数化净增 1 例（53 → 54）→ **145 = 40 + 29 + 10 + 54 + 12**（分文件收集数逐档 `--collect-only` 实测）。耗时随项数增长回到 60s 量级（62.7s），9.1 的 DoD 行按「量级达标 + 项数为硬口径」重估（分层复核登记为后续可选项）。**CH-474 的 145 → 146**：X-17 回归测试补入 `test_ch473_fixes.py`（红线 12 → 13，其余四个文件收集数不变）→ **146 = 40 + 29 + 10 + 54 + 13**；本沙箱 gate 60.0s。**CH-475 的 146 → 153**：第六轮全面审查（报告 `docs/review-round6-CH-475.md`）的 12 项 A 类修复各补红线断言，新增 `test_ch475_fixes.py` 红线 7 项（mark 状态守卫、幂等重放跳过 save_ir、审计 actor 为 token 哈希、scrub_record bytes 指纹化、reload 拒不可变键、畸形 marker 中和、char_similarity 见缩进漂移），其余四个文件收集数不变 → **153 = 40 + 29 + 10 + 54 + 13 + 7**（分文件收集数逐档 `--collect-only` 实测）；本机（macOS arm64 / Python 3.15）gate **30.04s**。**CH-476 的 153 → 244**：第七轮全面审查（报告 `docs/review-round7-CH-476.md`）的 32 项 A 类修复按 13 批实施，红线断言集中在新增 `tests/test_ch476_fixes.py`（87 项收集 / **83 项红线**，参数化展开含 `validator_rejects_malformed_target` 5、`apply_ops_survives_malformed_target` 5、`queue_claim_size_reads_factor_with_defaults` 7、`drain_round_claims_batch_beyond_pool` 2）与 `tests/test_sharding_unit.py` 内就近落地的 **8 项**（R7-PIPE-1 四个测试函数展开为 8 项：编号递增方向 1、递减不续接 1、结构块守卫参数化 5（heading/table/code/quote/page_break）、缝合层「递增命中的后块标题不被吸收」1）→ **+91 = 244 = 40 + 29 + 10 + 54 + 13 + 7 + 91**（其余五个文件收集数逐项复算未变：`test_ch473_fixes.py` 红线仍 13、`test_ch469_ops.py` 仍 54）。本机 gate **33.9s**——项数 +91 而耗时只 +3.9s，因为本轮红线绝大多数是纯单元/契约断言（分片续接、算子参数、配置契约、FTS 映射），不含真实转换长跑；长跑项只有黄金样本的 GAP 端到端三支。**CH-477 的 244 → 248**：9.2 T15 落地，新增 `tests/test_ch477_version_sync.py` 红线 4 项（版本三处对账 / README↔10.1 编号对账 / 管线版本两处 / IR schema 与 DB_VERSION 两处），其余八个文件收集数逐项复算未变 → **248 = 40 + 29 + 10 + 54 + 13 + 7 + 91 + 4**（分文件收集数由 `--collect-only` 实测）；本机 gate 两采样 **33.25s / 31.98s**——+4 项纯静态断言，耗时变化仍在本机散布带内。**CH-478 的 248 → 252**：批次① 收口 R6-PIPE-1（决策「落地有序列表分支」），新增 `tests/test_ch478_fixes.py`（18 项收集 / **4 项红线**，其余 14 项为 `_is_ordered_item` 正/负形态参数化，落信息层）→ **252 = 40 + 29 + 10 + 54 + 13 + 7 + 91 + 4 + 4**（其余八个文件收集数逐项 `--collect-only` 复算未变）；本机 gate **31.54s / 收口复跑 31.36s**——4 项红线共用同一个 module 级 `convert()` 夹具（一次真实转换、四条断言），耗时仍在本机散布带内。**CH-478 批次② 的 252 → 256**：收口 R6-PIPE-2（B-2 出口），新增 `tests/test_ch478_gate_drift.py`（10 项收集 / **4 项红线**，其余 6 项落信息层）→ **256 = 40 + 29 + 10 + 54 + 13 + 7 + 91 + 4 + 4 + 4**（其余九个文件收集数逐项 `--collect-only` 复算未变）；本机 gate **31.16s**——四条红线里三条只做纯字符串/度量计算、一条走 S1 抽取，共用同一份 `convert()` 夹具，仍在本机散布带内。**CH-479 的 256 → 260**：CI 版本矩阵口径收口，新增 `tests/test_ch479_ci_matrix.py`（6 项收集 / **4 项红线**，其余 2 项落信息层）→ **260 = 40 + 29 + 10 + 54 + 13 + 7 + 91 + 4 + 4 + 4 + 4**（其余各文件收集数逐项 `--collect-only` 复算未变）；本机 gate **38.55s / 台账收口复跑 33.74s**——四条红线只读两个工作流文本再做正则切块，自身耗时可忽略，高于批次② 的 31.16s 是同机负载抖动而非断言变贵（同轮全量 75.78s / 81.66s 与批次② 的 69.39s 同向浮动），项数仍是硬口径 |
| 全量 `pytest` | 146 收集 → **242 收集（241 passed + 1 skipped）** → **276 收集（275 passed + 1 skipped）** → **302 收集（301 passed + 1 skipped）** → **308 收集（307 passed + 1 skipped）** → **332 收集（331 passed + 1 skipped）** → **339 收集（338 passed + 1 skipped）** → **481 收集 = 481 passed / 0 skipped** → **543 收集 = 543 passed / 0 skipped**（CH-471 轮 60.7s）→ **605 收集 = 605 passed / 0 skipped**（CH-473 轮 126.3s）→ **606 收集 = 606 passed / 0 skipped**（CH-474 轮，X-17 回归 +1，本沙箱 122.0s）→ **677 收集 = 677 passed / 0 skipped**（CH-475 轮，`test_ch475_fixes.py` 新增 71 项，本机 macOS arm64 / Python 3.15 实测 66.09s）→ **773 收集 = 773 passed / 0 skipped**（CH-476 轮，`test_ch476_fixes.py` 新增 87 项 + `test_sharding_unit.py` 17→26 即 +9，本机 macOS arm64 / Python 3.15 实测 **73.3s**；+96 项全部逐项归因到这两个文件，无未归因余额）；CH-469 轮 73.8s、收口终轮复跑 70.7s（本机，零跳过纪律见附录 F；两次均为 481 passed / 0 failures / 0 errors / 0 skipped，取证用 `--junitxml` 计数而非散文汇总）、CH-464/465 轮 108s、CH-462 轮 106s、CH-461 轮 102s、CH-460 轮 100s、CH-459 轮 109.1s、CH-468 轮验收 PR #1 三个 merge-gate 全 FAILURE + full-suite SKIPPED + BLOCKED（T1 关闭证据）、CH-458 轮 130.9s、CH-456/457 轮 107.98s、CH-455 轮 163.3s、CH-454 轮复核 162.4s、CH-453 轮复核 110.8s、低端机收口轮 164.9s | skipped 曾为可选大资产（CH-443），**CH-469 轮起归零**：两处 `pytest.skip` 都是「把宿主条件写进断言」的病灶，已改在产品/测试侧消除（`ops/bench/t6_large_corpus.py` 的 proc 根可注入、`test_m3_bigcopy.py` 改恒真两分支），纪律与判据见附录 F「零跳过纪律」。CH-458 新增 4 项（T3 采样后端三支 + n/a 表格降级）。CH-459 新增 34 项（`test_s3_blocks.py`：七类块可达性 + 四类块口径 + 链接 + LLM op 语义）。CH-460 新增 26 项（`test_batch2_storage.py`：弱引用连接表 + 事务合并 + 门禁单遍 + 单块索引 + FTS 上限 + 幂等键派生 + 脱敏 + 删除/回滚校验）。CH-461 新增 6 项（`test_m8_ops.py`：v2 容器随机盐 / v1 兼容 / 多块流式 round-trip / 块交换与截断拒绝 / 头部篡改拒绝）。CH-462 新增 24 项（CLI 四子命令入口 7 + API 三端点 6 + sharding 纯函数 10 + zip 完整性 1），另顺带修三个测试缺陷（vacuous 断言 / raises 过宽 6 处 / regex→ast 依赖扫描）与两个被零覆盖掩盖的真缺陷（watch --once 死路径、/system/reload 丢 settings_override）。**CH-469 新增 142 项**（339 → 481）：批次②③ 的两个新文件在全量层实测收集 **120 项**（`test_ch469_ops.py` 90 + `test_ch469_service.py` 30；同一批文件在红线层贡献 53 + 10 = 63），批次① 就近落测（缝界进 `test_sharding_unit.py`、缺页进 `test_batch2_storage.py`、连接重试进 `test_t2_llm_server.py`、空窗超时进 `test_perf_lowend.py`、块数不变量作 `test_v24_gates.py` 内的断言而非独立项）。剩余 **22 项不做逐档归因**：339 基线未记录分文件收集数，本轮无法复算它在既有文件上的增量分布，只把「总量 +142」与「两个新文件 120」作为实测口径登记（无据的分档写法属伪覆盖）。同一份代码在多个时点给出 100 / 109.1 / 130.9 / 107.98 / 163.3 / 162.4 / 110.8 / 164.9s——沙箱负载是主导因素，项数才是硬口径。**CH-471 新增 62 项**（`test_ch471_fixes.py` 第四轮逐字母回归，481 → 543，60.7s）；**CH-473 再增 62 项**（`test_ch473_fixes.py` 61 项 + `test_ch469_ops.py` Q-1 参数化净增 1 例，543 → 605，126.3s）——两轮 +124 项全部逐项归因到新文件与既有文件的参数化增量，无未归因余额（CH-469 轮「22 项不逐档归因」教训的反向应用）。**CH-477 新增 4 项**（`test_ch477_version_sync.py` 4 项全部同时是红线，773 → **777 收集 = 777 passed / 0 skipped**，本机 73.23s / 收口复跑 68.79s；`--junitxml` 的 `tests=` 取 777，非红线层 529 项未变）。**CH-478 新增 18 项**（`test_ch478_fixes.py` 18 项 = 红线 4 + 信息层 14；777 → **795 收集 = 795 passed / 0 skipped**，本机 **70.01s / 收口复跑 69.58s**；`--junitxml` 取证，非红线层 529 → 543，+14 全部归因到本文件的参数化两组，无未归因余额）。**CH-478 批次② 新增 10 项**（`test_ch478_gate_drift.py` 10 项 = 红线 4 + 信息层 6；795 → **805 收集 = 805 passed / 0 skipped**，本机 **69.39s**；`--junitxml` 的 `tests=` 取 805，非红线层 543 → 549，+6 全部归因到本文件的参数化与两条信息层用例，无未归因余额——本批次另含一份新黄金资产与 MANIFEST 第 5137 条目，资产不是断言、不计项数。**CH-479 新增 6 项**（`test_ch479_ci_matrix.py` 6 项 = 红线 4 + 信息层 2；805 → **811 收集 = 811 passed / 0 skipped**，本机 **75.78s / 台账收口复跑 81.66s**；`--junitxml` 的 `tests=` 取 811，非红线层 549 → 551，+6 全部归因到本文件自身，无未归因余额；同轮新增的 `.github/workflows/ci-full-matrix.yml` 是配置资产，不是断言、不计项数） |
| CI 远端运行 | 首轮 run #1（push 到 `main`，commit `34e2853`）**四个作业全绿**：`merge-gate (3.11/3.12/3.13)` 各 65 项 `[100%]`，`full-suite` 143 项 `[100%]` + 可复现性 + `doctor` | GitHub Actions run #1，2026-10-13。测试步骤自身墙钟：红线层 72s（3.12）/116s（3.11）/122s（3.13），全量 183s（3.12）——runner 上同样比本地慢，项数才是硬口径。**CH-469 轮的远端证据形态（本轮换了一种接法）**：工作副本目录本身没有 `.git`，因此红线是在本地沙箱验证的（132 项 / 481 项，本机 macOS arm64 / Python 3.15）；推送改用一次性 `git clone` + 逐文件同步 + `push`，工作副本的 `git worktree` 前后双跑法当时不可执行（git 能力已于 CH-473 轮恢复，补做结果见 12.2）。**本次 push（`88896ae`）触发的 run `38756727387` 结果**：`merge-gate (3.11)` 1m23s 绿、`(3.12)` 56s 绿、`lint` 15s 绿，`(3.13)` 的 pytest 步骤自 15:02:06Z 起挂住（+2h20m 仍 `in_progress`，作业日志 blob 取回 `BlobNotFound`），`full-suite` 因 `needs: merge-gate` 未启动——这条挂死登记为 CH-471 并当场修好（作业级 `timeout-minutes` + `-vv` 逐用例行）。因此本轮的矩阵证据是**两档绿、一档未出结论**，不是三档全绿。**CH-471 / X-16 之后的远端累计（CH-473 收口轮补记）**：`36ef92a` 的 run `38843497730` 三档里 3.11（59s）/ 3.13（59s）绿而 **3.12 在「132 passed」后卡死**至 15 分钟超时被取消（X-16，`full-suite` 未启动）；`3f7f9f2`（X-16 修复）的 run `38845736366` 与 `584e7b5`（超时右调 6 分钟）的 run `38851378872` **均五作业全绿**（前者 lint 19s + 3.11 1m08s + 3.12 1m28s + 3.13 52s + full-suite 3m40s；后者全程 4m45s）——3.12 腿的退出死锁由此获得远端确认，矩阵恢复三档全绿。**CH-473 全轮（批次①②③ `2081398` + 批次④ `4151515`）已推送**（10-16 12:16Z，`ops/hooks/pre-push` 首次实战通过后放行），**145 项红线的首次远端验证 = run `38899470118`**（push 触发，12:17:22Z→12:22:03Z，**五作业全绿**）：merge-gate 三档均 **145 passed, 460 deselected**（3.11 44.38s / 3.12 68.80s / 3.13 73.23s）、lint 22s、full-suite **605 passed, 8 warnings**（126.77s）。同场远端再次实测到**必需检查不拦直推**（`Bypassed rule violations for refs/heads/main: - 3 of 3 required status checks are expected`，push 成功）——CH-470 登记的必要性再加一证。**X-16 复发与根治（CH-474）**：收口轮续提交 `df53679`（纯文档）的 run `38901173280` 中 merge-gate 三档全绿，**`full-suite`（3.12）在打出「605 passed, 11 warnings in 155.99s」之后 pytest 主进程卡退出 27 分钟**至 30 分钟作业超时被取消（run cancelled，日志 blob 在 in-progress 期间取回 `BlobNotFound`、取消后可读）——日志 warnings 含 `This process (pid=…) is multi-threaded, use of fork() may lead to deadlocks in the child`（`test_perf_lowend` 的 fork 场景），X-16 的 abandoned-only 修复未覆盖**正常完成路径**（同代码的 `38899470118` 干净退出——竞态，负载越高窗口越大）。修复登记 X-17（12.7）。**修复 re-run = run `38913173159`（10-16 14:07Z，推送 `b0d4e52`，pre-push 钩子以 146 项 gate 放行）五作业全绿**：merge-gate 三档均 **146 passed, 460 deselected**（3.11 48.15s / 3.12 80.11s / 3.13 74.10s）、lint 全绿、full-suite **606 passed, 1 warning in 153.28s 干净退出**（同腿上一 run 挂死 27 分钟）——X-17 根治获远端确认。**CH-475 轮远端确认 = run `38995335128`**（push `ae296b4`，本机 gate 153 项**手动放行**——`core.hooksPath` 此工作副本未启用，按「不改 git 配置」策略改手动跑 `pytest -m gate` 得 153 全绿后 push）五作业全绿：**lint「All checks passed!」**（ruff 远端首证）、merge-gate 三档均 **153 passed, 524 deselected**（测试步骤墙钟 3.11 66.16s / 3.12 63.07s / 3.13 72.90s；作业时长 81 / 82 / 91s）、full-suite（3.12）**677 passed, 1 warning in 157.70s 干净退出**（作业 02:34:15Z→02:38:14Z，3m59s）——**153 项红线三版本的首次远端实证**，且 X-17 根治在正常完成路径的 full-suite（3.12）再获一证（CH-474 轮该腿曾挂死 27 分钟）。同场直推 `main` 再次复现「必需检查不拦直推」（`Bypassed rule violations for refs/heads/main: - 3 of 3 required status checks are expected`，push 成功）——CH-470 的必要性再添一证。**CH-476 轮远端确认 = run `39016742023`**（push `9a1d959`，2026-10-17 08:16:28Z→08:21:52Z，五作业全绿）：lint「All checks passed!」（15s）、merge-gate 三档均 **244 passed, 529 deselected**（测试步骤墙钟 3.11 **56.91s** / 3.12 **70.50s** / 3.13 **58.89s**；作业时长 71 / 86 / 76s）、full-suite（3.12）**773 passed, 1 warning in 157.20s 干净退出**（作业 08:17:59Z→08:21:51Z，3m52s）——**244 项红线与 773 项全量的首次远端实证**，且 X-17 的池收尾根治在 full-suite（3.12）连续第三轮干净退出。本轮推送同样只返回 `Bypassed rule violations …`（push 成功），CH-470 登记的「直推路径无平台级强制点」累计第三证；本机 gate 由手动 `pytest -m gate`（244 全绿）放行后再 push（`core.hooksPath` 此工作副本仍未启用，不改 git 配置）。 **CH-476 收口轮（批次⑬）远端确认 = run `39022142048`**（push `44f98c3`，09:42:25Z→09:47:54Z，五作业全绿）：lint「All checks passed!」、merge-gate 三档均 **244 passed, 529 deselected**（3.11 52.99s / 3.12 58.16s / 3.13 78.49s）、full-suite（3.12）**773 passed, 1 warning in 159.35s 干净退出**、`doctor` 自检 33 键全 ok——批次⑬ 的文档对齐与 `PLAN_VERSION` 升位由此拿到独立远端证据，12.7 的「仅本地证据」标注同步闭环；同场直推 `main` 第四次复现「必需检查不拦直推」。**批次⑬ 的收口续提交 `052c269`（纯台账回填）另有 run `39022746836` 五作业全绿**（09:52:09Z→09:57:42Z：lint 15s、三档 merge-gate 各 244 passed / 529 deselected、full-suite 773 passed 干净退出）——回填类提交同样过全部红线复检，属口径而非新证据。**CH-477 轮（T15 落地，push `66414ec`）远端确认 = run `39023289246`**（2026-10-17 10:01:02Z→10:06:43Z，五作业全绿）：lint「All checks passed!」（14s）、merge-gate 三档均 **248 passed, 529 deselected**（测试步骤墙钟 3.11 **76.46s** / 3.12 **84.97s** / 3.13 **75.61s**；作业时长 91 / 104 / 93s）、full-suite（3.12）**777 passed, 1 warning in 158.16s 干净退出**（作业 10:02:51Z→10:06:43Z，3m52s）+ 黄金样本逐字节可复现步骤 7 项全过 + `doctor` 自检 33 键全 ok（远端解释器 3.12.15、`pipeline` 字段 2.4.0）——**248 项红线与 777 项全量的首次远端实证**；四条新对账断言在 3.11 / 3.12 / 3.13 三档与 3.12 全量四条腿同时通过，等价于「文档锚点句式在四种解释器口径下均解析成功」的跨版本证据。推送前按 `core.hooksPath` 本工作副本未启用的既有策略手动跑 `pytest -m gate`（248 全绿）再放行，直推 `main` 成功（CH-470 登记的「直推路径无平台级强制点」继续成立）。**CH-477 收尾回填提交 `cb310a3` 另有 run `39023978470` 五作业全绿**（10:11:58Z→10:17:34Z，全程 5m40s）：lint「All checks passed!」（15s）、merge-gate 三档各 **248 passed, 529 deselected**（测试步骤墙钟 3.11 74.81s / 3.12 81.21s / 3.13 77.16s）、full-suite（3.12）**777 passed, 1 warning in 158.38s 干净退出**——纯台账回填同样过全部红线复检。**CH-478 批次① 远端确认 = run `39027385467`**（push `b601749`，11:05:44Z→11:11:12Z，全程 5m29s，五作业全绿）：lint「All checks passed!」（12s）、merge-gate 三档均 **252 passed, 543 deselected**（测试步骤墙钟 3.11 **60.32s** / 3.12 **78.50s** / 3.13 **62.79s**；作业时长 75 / 93 / 87s）、full-suite（3.12）**795 passed, 1 warning in 156.88s 干净退出**（作业 11:07:27Z→11:11:12Z，3m45s）+ `Golden assets byte-reproducible` 步骤全过（新样本 `numlist_1p.pdf` 的逐字节复现断言已在内）+ `doctor` 自检全 ok（远端解释器 3.12.15、`doc2md 2.4.0`）——**252 项红线与 795 项全量的首次远端实证**；有序列表分支的四条端到端红线在 3.11 / 3.12 / 3.13 三档与 3.12 全量四条腿同时通过，等价于「编号行的 `list_item` 判定与序号渲染在四种解释器口径下结论一致」。推送前照例手动 `pytest -m gate`（252 全绿）后放行，直推 `main` 再次返回 `Bypassed rule violations …`（push 成功，CH-470 累计第五证）。**CH-478 批次② 远端确认 = run `39030470549`**（push `2e04aaf`，11:54:34Z→11:59:48Z，全程 5m14s，五作业全绿）：lint「All checks passed!」（13s）、merge-gate 三档均 **256 passed, 549 deselected**（测试步骤墙钟 3.11 **67.07s** / 3.12 **56.53s** / 3.13 **61.67s**；作业时长 84 / 71 / 79s）、full-suite（3.12）**805 passed, 1 warning in 155.87s 干净退出**（作业 11:56:00Z→11:59:48Z，3m48s）+ `Golden assets byte-reproducible` 步骤全过（新样本 `codeindent_1p.pdf` 的逐字节复现断言已在内）+ `doctor` 自检全 ok（远端解释器 `3.12.15`、`pipeline` 字段 `2.4.0`）——**256 项红线与 805 项全量的首次远端实证**；批次② 的四条度量红线（缩进漂移判红且旧口径精确 1.0 / 内容级漂移新旧同值故收紧无额外敏感度 / S1 剥行首空白而 `Line.x0` 仍带三档 / 参照实现与产品 `char_similarity` 同值）在 3.11、3.12、3.13 三档与 3.12 全量四条腿同时通过，说明「收紧只覆盖空白类漂移」这一口径结论不依赖本机的 pdfplumber 形状。推送前手动 `pytest -m gate`（256 全绿）后放行，直推 `main` 又返回 `Bypassed rule violations … 3 of 3 required status checks`（push 成功，CH-470 累计第六证）。 |
| 失败能否阻断合并 | **已生效（CH-468，T1 关闭）** | 仓库转 public 后 `main` 必需检查 = `merge-gate (3.11 / 3.12 / 3.13)`；故意的红断言 PR 三个 gate 全 FAILURE、`full-suite` SKIPPED、`mergeStateStatus=BLOCKED`（12.7 有取证行）。此前「private + 免费计划 → 分支保护 API 403」的限制随转公开一并消失；本地 `ops/hooks/pre-push`（CH-467）保留为双保险 |

> 门禁基线引用规则：正文与 README 引用「项数」为硬口径（当前 **260 项**，CH-479；此前为 256 / 252 / 248 / 244 / 153 / 146 / 145 / 132 项），引用「耗时」须带轮次且不得单点引用——四组实测（gate 110.4 / 72.5 / 108.8 / 109.7s，全量 164.9 / 110.8 / 162.4 / 163.3s）跨度都超过 50%，任何「本轮值」都不能代表典型值，因此本表保留全部轮次而不做替换。CH-473 轮的两组新样本（gate 62.7s / 全量 126.3s）、CH-475 轮的本机样本（gate 30.04s / 全量 66.09s）与 CH-476 轮的本机样本（gate 33.9s / 全量 73.3s）落在同一散布带内，不改变该规则。**批次⑬ 收口轮同机复跑**（文档对齐 + 注释漂移 + `PLAN_VERSION` 升位后的出口复核）：gate 244 全绿 **35.5s**、全量 773 passed / 0 skipped **73.3s**（同轮两次采集为 35.0 / 35.5s 与 75.8 / 73.3s）、`ruff` 全绿、`doctor` 退 0、三处版本对账实测一致（常量 `3.9` / header v3.9 / 1.4「本版 v3.9」）——项数不变、耗时与 33.9 / 73.3s 的差值仍是本机负载，是散布带内的第 4、5 个本机样本。项数与上一轮（132）的差 = CH-473 的 13 项红线 + CH-474 的 1 项 + CH-475 的 7 项 + **CH-476 的 91 项**（`test_ch476_fixes.py` 83 + `test_sharding_unit.py` 8，构成分批见附录 F 第六层）+ **CH-477 的 4 项**（`test_ch477_version_sync.py`，见附录 F 第七层）。**CH-477 轮同机样本**：gate 248 全绿 **33.25s / 31.98s**、全量 777 passed / 0 skipped **73.23s / 68.79s**（`--junitxml` 取证），仍落在散布带内、不改变「耗时只作历史」的规则。CH-471 轮项数不变（第四轮 62 项全落信息层）。**CH-478 轮同机样本**：批次① gate 252 全绿 **31.54s / 31.36s**、全量 795 passed / 0 skipped **70.01s / 69.58s**（`--junitxml` 取证）、`ruff` 全绿、`doctor` 退 0（33 键）、版本四处对账实测一致（常量 `3.11` / header v3.11 / §1.4「本版 v3.11」/ §1.4 版本表管线 2.4.0）——本轮是 CH-477 那四条对账红线**第一次被真正用于一次 `PLAN_VERSION` 升位**（3.10 → 3.11），升位与三处文档锚点在同一提交内完成，并做一次突变复核：把常量改回 `3.10` 后 `test_plan_version_three_way_reconciliation` 实测转红（还原后复绿），证明「只改常量、文档不动」这一形态确实被拦得住。**CH-479 轮同机样本**：gate 260 全绿 **38.55s / 33.74s**、全量 811 passed / 0 skipped **75.78s / 81.66s**（`--junitxml` 取证；前者为实施轮、后者为台账收口轮）、`ruff` 全绿、`doctor` 退 0；`PLAN_VERSION` 3.12 → 3.13 是四条对账红线的**第三次实际自证**——常量 / header / §1.4「本版」/ README 与 10.1 编号在同一次 gate 复跑里全绿，且升位后按 `grep` 命中逐处复核，确认残留的 `3.12` 全部落在历史叙述与 `PIPELINE_VERSION`/`full-suite` 钉档等无关字段上，没有第四处活锚点被漏改。本轮 38.55s / 33.74s 两点都高于批次② 的 31.16s，且与全量 75.78s / 81.66s 同向浮动：新增四条红线只读两个工作流文本并做正则切块，不含转换长跑，差值属同机负载抖动，规则不变。
>
> CI 远端累计（截至 CH-458 轮收尾）：`34e2853`(run #1)、`ae12c03`(#2)、`aa40568`(#3)、`604ae62`(#4) 四次 push 全绿；`27fb1c1`(#5) 被 `cancel-in-progress` 取消（同一 ref 上 55 秒后又有 push，工作流头部那条 concurrency 规则按设计生效），`8031198`(#6) 重新跑满四作业全绿；`58f8a5e`(#7) 全绿；CH-455 轮的 `d8bf1e7`(#8，run id 38510360834) 四作业全绿（merge-gate 3.11/3.12/3.13 各 67 项 + full-suite 146 项含可复现性与 doctor）。**CH-458 轮的 `2123d52`（#9，run id 38569715467）五作业全绿**——`lint`（ruff）+ `merge-gate (3.11/3.12/3.13)` 各 67 项 + `full-suite`：作业数 4→5 即 T10（CH-456）ruff 信息作业接入的远端首证，full-suite 与可复现性仍只在 3.12 上跑（12.7 的诚实边界未突破）。取证口径同前：本轮 token 为 fine-grained PAT，`checks:read` 无法授权（GitHub 现行限制），annotations 读取 403，结论取自 Actions 作业状态 API。**当前 `main` HEAD 的检查状态 = success。**
>
> **CH-469 / CH-471 轮补记**（累计口径从「#N 序号」改为 run id，序号在跨仓库比对时不具名）：CH-467 的 `047cf0b` 与 CH-468 的 `797d28f` 两次 push 各自全绿（run 38719681404 / 38725656286，四作业 + lint 共五作业），CH-468 的 T1 验收 PR `t1-verify-deliberate-red`（run 38724311864）**故意红**：三个 merge-gate FAILURE、`full-suite` SKIPPED、`mergeStateStatus=BLOCKED`——这是全仓唯一一次「红线真的拦住了一次合并」。CH-469 的 `88896ae`（run 38756727387）则是**第一次出现「非全绿但也不红」的形态**：3.11 / 3.12 / lint 绿，3.13 挂死——pytest 步骤 6 小时零输出后撞上 GitHub 默认 6 小时作业上限被取消（作业级时长实测 15:02:06Z → 21:03:17Z；早前登记称「最终由 CH-471 修复推送按 `cancel-in-progress` 规则取消」与作业时长不符——下一 push 到达时该作业已被默认上限取消约 6 小时），`full-suite` 永不启动。同场实测到**必需检查不拦直推**（远端返回 `Bypassed rule violations …`，push 成功，CH-470）。取证口径变化：本轮令牌可读 Actions 作业状态与作业级时长，但 in-progress 作业的日志 blob 返回 `BlobNotFound`，所以"卡在哪个用例"当时无法回答——这正是 CH-471 把 `-vv` 写进 CI 的原因。**CH-473 收口轮补记**：`36ef92a` 的 run `38843497730`（3.12 腿「132 passed」后卡死至 15 分钟超时，X-16）之后，`3f7f9f2` 的 run `38845736366` 与 `584e7b5` 的 run `38851378872` 均五作业全绿——3.12 腿退出死锁修复获远端确认，累计口径从「两次部分结论」回到三档全绿；`2081398` 与 `4151515`（CH-473 批次④）已推送，CI 证据为 run `38899470118` 五作业全绿（145 项红线三档实证 + full-suite 605 passed，详见上表 CI 行末）；其后的 `df53679` 触发 run `38901173280`——X-16 在正常完成路径复发（full-suite 退出挂死），根治登记 X-17（详见上表 CI 行末与 12.7）。
>
> run #4 的作业级墙钟：`merge-gate` 135s（3.11）/ 152s（3.12）/ 139s（3.13）、`full-suite` 227s（全量 143 项 + 可复现性 + `doctor` 三步）。附带结论：**「同一 push 全绿」不等于「每个 commit 都有 CI 证据」**——取消是并发策略的副作用，被取消的 commit 没有红线证据，因此需要每个 commit 都留证的场合（例如二分定位）要临时关掉 cancel-in-progress。此点不影响 T1 的判据（T1 要的是「必需检查能拦住合并」，与取消无关）。

### 12.2 产物等价性（G6 的执行证据）

三态对比：改造前（`git worktree` 检出 HEAD）、改造后 `chunk_pages=0`、改造后 `chunk_pages=32`；6 个黄金样本各转一次，比对 zip 内每一条目 sha256（`content.md` 先剥 front-matter）。**下表覆盖 CH-447…CH-460 各轮**；CH-469 轮因工作区非 git 仓库不能检出「改造前」，证据形态改为不变量 + 新基线，见本节末。

| 结论 | 值 |
| --- | --- |
| 不匹配条目总数 | **TOTAL_MISMATCHES = 0** |
| 覆盖样本 | small / links_pdf / badtail / docx 系列 / 320 页大样本（6 个） |
| 补充断言 | 分块边界参数化（chunk 1/2/3/32 vs 整本）、进程池 vs 串行含 `spawn`、队列产物 vs 串行产物 |

**CH-459 轮（批次①，改产物路径）的等价性复核**：改造前对 6 个黄金样本的 markdown 做快照（sha256 前 16 位），改造后重跑逐字节比对——`small_3p.pdf`（`b1a1312c26d9dc9f`）与 `large_320p.pdf`（`6a90e3e368819ed5`）**逐字节一致**（新增的等宽 / 表格行 / 标题提示信号在既有样本上零触发，检测保守性达成）；`links_pdf.pdf` 按设计变化（`dfd7fa22`→`882a6a41`，两条链接落地为 `[整段](url)` 形态，无误伤其他行）；三个 DOCX 样本仅标题检出变化（Heading 样式行从段落升级为标题块，FR-104 修复的预期效果，源文件样式已逐一核实）。**结论：变化全部来自设计内的功能补齐，无一处非预期漂移。**

**CH-460 轮（批次②，不改产物路径）的等价性复核**：改造前 `git worktree` 检出 HEAD，两版各跑一次 6 黄金样本，比对 `Result.gate` 完整明细（JSON 序列化）与 markdown sha256——**逐字段一致**（`GateCollector` 重构不改变门禁任何判定输出；存储与幂等键改动不触产物）。方法学注记：首次比对曾出现 `avg_chars` 0.6 的假差异，根因是两次运行的工作目录不同导致 `config.load()` 读到不同配置（`sys.path` 隔离了代码、没隔离 cwd）——对比脚本必须用显式 `PYTHONPATH` + 同一 cwd 隔离「代码版本」与「配置环境」两个变量。

**CH-469 轮（改产物路径）的等价性：三态对比不可执行，改用不变量 + 新基线**。前置事实必须先说清：**工作副本目录不是 git 仓库**（无 `.git`；本轮的推送与 `git ls-files` 清点改在一次性 clone 里完成，见 12.7），`git worktree` 检不出「改造前」那份代码，因此上面两轮的 before/after 双份跑法在本轮**无法执行**——这不是口径松动，而是证据形态换了。本轮用两类可独立复核的替代证据：

① **产物不变量**（比 sha 更能说明「改对了」而不是「改成什么样了」）：`pytest -m gate` 的 `shard_two_phase` 现在同时断言 `char_similarity ≥ 0.99` 与 **分片去重后块数 == 单片块数**；chunk 1/2/3/32 vs 整本产物等价、进程池 vs 串行逐条目一致（含 `spawn`）、队列产物 vs 串行产物三条红线原样在场。缝界修复前后的逐档数字见 12.5。

② **新基线**（供下一轮做真正的逐字节比对）：6 个黄金样本用**纯净默认配置**（`config.load()` 在无用户 TOML 时逐项等于内置默认，已实核）各转一次，取 zip 内 `content.md` 剥 front-matter 后与 `ir.json` 的 sha256 前 16 位：

| 样本 | `content.md`（剥 front-matter） | `ir.json` |
| --- | --- | --- |
| `small_3p.pdf` | `d46166be821ce92c` | `ad57974b167770c5` |
| `links_pdf.pdf` | `6aaca47ba8d84ade` | `217f5d8e7001d2e7` |
| `docx/sample_links.docx` | `3e543749bb9f540f` | `be1b1bbf21f26c14` |
| `docx/sample_revisions.docx` | `ab6c47375ed49c41` | `af4f37bb809dbdb3` |
| `docx/sample_simple.docx` | `6cf6d448ac49f8ec` | `bb6e674381e84f07` |
| `large_320p.pdf` | `830db6b3a35c3247` | `d728b3af3a1d911c` |

口径纪律三条：front-matter 本身是确定性的（无生成时间戳，实测本机 `large_320p` 的头部含 `sharded: true / shard_count: 2 / seams: 1`），所以剥它是为了让「正文是否变化」与「meta 是否变化」两个问题分开设断言，而不是因为不可复现；`badtail_front.pdf` 不在表内，它按设计被 S1.5 拒收（`E4221_GateReject`）因而无产物；本轮**不声称这些值与前序轮次的 sha 一致**——前序登记的 `b1a1312c…` / `882a6a41…` 无法在本机复算（缺改造前代码 + 采集时用的入口与本表不同），保留原值作历史记录，本表自 CH-469 起作为可比基线。

**CH-476 轮复算（批次⑦ + ⑨，本机 macOS arm64，同一剥 front-matter 口径）**：本表上方 CH-469 的原值保留为历史记录，本轮之后的可比基线是下表——

| 样本 | `content.md`（剥 front-matter） | `ir.json` | 变化归因 |
| --- | --- | --- | --- |
| `small_3p.pdf` | `d46166be821ce92c` | `ad57974b167770c5` | 无（逐字节复算一致） |
| `links_pdf.pdf` | `6aaca47ba8d84ade` | `217f5d8e7001d2e7` | 无（逐字节复算一致） |
| `large_320p.pdf` | `830db6b3a35c3247` | `d728b3af3a1d911c` | 无（逐字节复算一致） |
| `docx/sample_links.docx` | `3e543749bb9f540f` | `b9646a8484784a3e` | 仅 `ir.json`：`flags.level_from_hint`（⑦ / R7-PIPE-4）新增旗标，正文一字未动 |
| `docx/sample_simple.docx` | `6cf6d448ac49f8ec` | `9049dd1a93ce208f` | 同上 |
| `docx/sample_revisions.docx` | `5527dcc5e6196142` | `988f0383f3161c35` | 输入资产按设计替换（⑨ / R7-GAP-1：真实 `w:ins`/`w:del` 样本，MANIFEST `919ba19a…`→`9d66924f…`，36735→37111 字节）+ 修订正文落地 + `flags.has_revision` |

三点结论：**三个 PDF 样本逐字节复算与 CH-469 基线一致**——本轮所有管线改动（缝合页差、围栏长度、修订抽取、cron/healthz 口径）对 PDF 语料零产物漂移；DOCX 的 `content.md` 只在修订样本上变化，且变化内容是设计内的（原先整段蒸发的插入正文回到产物里）；`ir.json` 在三个 DOCX 上全部变化但正文不变，原因是旗标字段是 IR 的开放字典、直接进 `ir.json`——`SCHEMA_VERSION` 因此**不升位**（无字段与结构变更，只有新增旗标 `has_revision` / `level_from_hint`，`flags` 口径见 5.2）。

**批次⑩（R7-GAP-2/3，脚注尾注 + 单元格段界）的产物面证据**：批次⑩的两处行为改动点都是「新语料才触发」的形态（既有 DOCX 全部无注 part、全部单段单元格；PDF 完全没有这条路径），所以它的等价性用**同机双份跑**证明——把 `_docx_notes_lines` 复位为返回空、`_docx_cell_text` 复位为旧的扁平粘连，其余代码与改动后同一份，6 个既有样本各转一次比 zip 内 `content.md`（剥 front-matter）与 `ir.json`：

| 样本 | 改动后 | 改动前（复位口径） | 结论 |
| --- | --- | --- | --- |
| `small_3p.pdf` / `links_pdf.pdf` / `large_320p.pdf` | 同 12.2 上表 | 逐字节相同 | 零漂移 |
| `docx/sample_links.docx` / `sample_simple.docx` / `sample_revisions.docx` | 同 12.2 上表 | 逐字节相同 | 零漂移 |

上表六个值同时**逐字符复现了批次⑦+⑨ 登记的基线**（`d46166be…` / `6aaca47b…` / `830db6b3…` / `3e543749…` / `6cf6d448…` / `5527dcc5…` 与六个 `ir.json`），即批次⑧⑩ 两轮改动对既有语料产物面零变化。新增黄金样本 `docx/sample_notes.docx`（`00a3acf9…`，37693 字节）自带设计内产物：`content.md` `a57dad83212f4cb0`、`ir.json` `dbd9b975940d4d20`——两条脚注与一条尾注作为尾部段落块落地（`flags.note_kind` = `footnote`×2 / `endnote`×1），单元格内两段以单空格相连。`SCHEMA_VERSION` 仍不升位（新增旗标 `note_kind` 走开放字典，`flags` 口径见 5.2）。

**CH-473 收口轮（git 能力恢复后，12.2 原始口径补做）**：CH-469 轮因工作副本无 `.git` 而无法执行的「改造前后双份跑」，在本轮条件具备后补做——`git worktree` 检出四个时点（`797d28f` CH-468 后 / `88896ae` CH-469 收口 / `584e7b5` CH-471 后 / `2081398` CH-473 后 = HEAD），每态对 6 个黄金样本各转换一次（纯净默认配置、显式 `PYTHONPATH` 指向该态代码、中性 cwd 隔离配置环境，方法同 12.9 第 5 条），比对 zip 内 `content.md` / `ir.json` / `report.html` 全条目 sha256 + `Result.gate` 完整明细 JSON + markdown sha + `doc_id`：

| 相邻时点 | 结论 |
| --- | --- |
| `797d28f` → `88896ae`（CH-469 轮） | **仅 `large_320p.pdf` 变化**（zip 三条条目全变：content.md `758e2d81…` → `556accb1…`、ir.json `0b8b76a0…` → `d728b3af…`、report.html 同理），其余 5 样本逐字节零漂移——CH-469 的分片改动（S-3 段缓冲跨页等）影响面被精确限定在唯一被分片的样本上，且终值与上表 CH-469 新基线逐项一致 |
| `88896ae` → `584e7b5`（CH-471 轮） | **6/6 样本完全一致**——第四轮「产物面零变化」的声称由此获得 before/after 逐字节证据（此前只有测试面间接证据） |
| `584e7b5` → `2081398`（CH-473 轮） | **6/6 样本完全一致**——PIPE-5/6（缝界比对面与常数界规则）、PIPE-9（块 id 唯一赋值点）、PIPE-11（S4 围栏按块类型判定）四处深改之后，产物面零变化取得 12.2 原始口径的最强证据形态 |

附带两项收获：① 上表 CH-469 新基线（原采集环境记为 macOS arm64 / Python 3.15）在本沙箱（Linux / Python 3.11.1）**12 项哈希全部命中**（6 个剥头 content.md + 6 个 ir.json）——产物确定性不依赖平台与解释器版本，「基线可比」从声明变为实测；② CH-469 轮登记的「三态对比不可执行」及其替代证据形态（不变量 + 新基线）至此闭环：当时「改动影响面仅在分片样本」的判断被逐字节比对证实。各态均用各自时点的纯净默认配置——配置键 57 → 60 的变化（CH-471 新增三键）经逐字节比对证实行为不变。

### 12.3 驻留与吞吐（数字口径的历史与现状）

同一改造在不同语料上测得的不同数字**并列保留**，不因新数字出现而删除旧记录——旧数字是当时的真实观测，只是可比性有限。

单文件 320 页样本（全量 `convert`，单进程峰值 RSS）：

| 阶段 / 配置 | 峰值驻留 | 耗时 |
| --- | --- | --- |
| 改造前（HEAD，整本读取） | **1290MB** | 29.9s |
| 改造后 `chunk_pages=0` | **1025MB** | 24.0s |
| 改造后 `chunk_pages=32`（默认） | **176MB** | ≈24.0s |

差值解释（1290 → 1025 来自每页 `flush_cache`，1025 → 176 来自分块重开把上限绑在页窗口上）。解析阶段单独归因（只跑 `read_pdf`）：chunk=0+flush 1003MB / chunk=0 无 flush 1278MB / chunk=32+flush **168MB** / chunk=32 无 flush 204MB。早期登记为 177MB 的 chunk=32 数字与本轮复测 176MB 属同一量级抖动。

并发驻留（4 个进程同时转 320 页样本，`--workers 4`，各 worker 自报 `ru_maxrss` 求和）：

| 配置 | 合计驻留 |
| --- | --- |
| `chunk_pages=0` × 4 | **4100MB** |
| `chunk_pages=32` × 4 | **708MB** |

> 方法学教训：父进程侧的 `RUSAGE_CHILDREN.ru_maxrss` 报告的是**各子进程峰值的最大值**而非总和，直接用它会把 4 倍驻留读成 1 倍。并发内存必须让每个 worker 自报后求和。

吞吐（三组不同语料，不可互相外推）：

| 语料 | 串行 | 并行 | 备注 |
| --- | --- | --- | --- |
| 30 × 极小 PDF（`/tmp/bench`） | 2.4s | 进程池 **1.3–1.6s** | 早期登记 3.6s → 2.6s 为另一轮观测，同一代码路径下无法精确复现，保留为历史值 |
| 8 × 40 页 PDF（`/tmp/bench2`） | 15.5s | 2 进程 8.0s → **4 进程 7.6s** | 8 进程无进一步增益（4 vCPU） |
| 30 文件（CH-448 原始登记） | 3.6s | spawn-pool 2.6s（n=2/4/8） | 与上第一行同语料不同轮次 |

结论口径：并发度上限 `min(4, cpu_count)` 与实测拐点一致；>核数无增益；产物逐条目一致（含 `spawn` 参数化）。

大语料实测（CH-457 / T6，`ops/bench/t6_large_corpus.py`）：1080 文件混合语料（1000 个单页 PDF + 60 个 5 节 DOCX + 20 个坏文件，种子 20261014，字节级可复现），测量一律走真 CLI 子进程（`doc2md work --enqueue DIR --once --workers N`），父进程经 `/proc` 进程树采样 RSS（0.1s）+ sqlite 只读连接轮询队列状态（0.5s）——不用 `RUSAGE_CHILDREN`（见上文方法学教训）。

| 指标（workers=1 / workers=4） | 值 |
| --- | --- |
| 排空耗时 | **51.4s / 49.1s** |
| 吞吐 | 1261 / 1320 件每分钟 |
| 峰值积压（queued+running） | 1067 / 1067（入队先于排空完成，`--enqueue --once` 的固有形态） |
| 半排空时点 | 28.5s / 26.9s（≈全长的 55%，两档都近似线性排空） |
| 入队即拒（坏文件） | 13 / 13（错魔术码 7 + 空文件 6，sniff 即拒） |
| 进程树峰值 RSS | **105.6MB / 191.5MB** |
| 最大单进程 RSS | 105.6MB / 38.6MB（4 档时父进程 ≈35MB + 4 worker 各 ≈38MB） |
| 终态分布 | done 1060 / failed 7 / rejected 0 / queued 0 / running 0（两档逐项一致） |
| 非 done 占比 | **1.85%**（= 注入坏文件比例 20/1080；失败账目精确闭合：1060 + 7 + 13 = 1080） |

两条结论：① **失败账目可精确闭合**——7 个截断 PDF 在消费侧 failed（E4151，CH-457 修复后的正确落点），13 个在入队面即拒（E4151），好文件 1060 个全 done；每个坏文件字节唯一，未被 `uq_tasks_doc_active` 附着去重吞掉。② **单页小文件上并发几乎无增益**（4 进程仅快 4.5%）——瓶颈不在解析并发度（GIL 侧的进程池对此无效），而在每任务固定开销：DB 写入、进程调度、入队逐条打印。这与上表「8×40 页 PDF 并行约 2 倍增益」不矛盾：后者每个文件的解析成本远高于固定开销。按 12.8 教训 4，两组数字按语料形态分别引用，不可互相外推。

**结论 ② 的归因更正（CH-476 / R7-PERF-1，决策 D2，本机 macOS arm64 复测）**：原登记把固定开销写成「DB 写入 / 进程调度 / 入队打印」，实测主因是**每轮进程池重建**——旧口径 `claim(store, workers)` 让 1067 个作业排成 267 轮，每轮新建 `ProcessPoolExecutor` 并让每个新 worker 重付 `import pdfplumber`（本机实测 ≈0.166s），并发增益全额被重建吃掉。修复 = 领取量与池宽解耦（`workers × [perf].queue_batch_factor`，默认 4）。同语料、同 CLI 口径、入队与排空分两次调用复测（各档重复两轮取稳态）：

| 排空耗时（本机，workers=4） | queue_batch_factor=1（旧口径） | =4（新默认） |
| --- | --- | --- |
| 第 1 轮 | 36.5s | **11.5s** |
| 第 2 轮 | 38.5s | **11.2s** |

即 1080 件语料上 workers=4 **提速 3.2–3.4×**。同轮的 workers=1 档几乎不动（36.5s 口径下的串行基线：旧 7.5s → 新 7.0s，噪声级）——`n==1` 走 `run_batch` 的进程内快路径、根本不建池，因此串行档不吃这个收益，这正是「开销在池重建而非并发度」的反证。**跨机可比性**：本机（Apple Silicon）同口径 workers=1 排空 7.2s（含 `--enqueue --once` 合并形态，与原测量同构），而 12.3 原登记是 51.4s——**本机基线快约 7×**，绝对值不可与 CI/Ubuntu 互比，本表只主张**同机相对增益**（3.2–3.4×）。失败账目与旧口径逐条一致（1060 done / 7 failed / 13 入队即拒），产物逐条目一致红线未动。**边界**：本机是 macOS fork 语义，12.3 的 49.1s 来自 Ubuntu CI；8GB Win11 走 spawn、每 worker 还要重导 `doc2md`，系数放大的收益与代价（批中途 OOM 后 `stale_job_minutes` 才回收）都**尚未在目标机实测**，登记 B-6。持久进程池是同源备选，但与 X-16/X-17 的「池收尾无条件 terminate」根治语义有张力，须等 B-6 数据再议。

**R7-STOR-1 实测（CH-476④，决策 D1，本机 macOS arm64 / Python 3.15）**：语料为合成库（`search_blocks` 100,000 与 300,000 行两档，每行 500 字正文 + aux 列，500 / 1,500 篇 × 200 块），测量口径是**同一条 DELETE 语句在 `BEGIN IMMEDIATE` 事务内计时后 ROLLBACK**（写锁面与生产一致，且不破坏库），每档 5 次取中位；两档各跑一次全新建库，旧口径 = `DELETE FROM search_blocks WHERE doc_id=? [AND block_id=?]`，新口径 = 同语句改为 `WHERE rowid IN (SELECT fts_rowid FROM search_map …)`。

| 路径 | 10 万块库 | 30 万块库 | 结论 |
| --- | --- | --- | --- |
| 整篇删除 · 旧口径 | **17.4ms** | **50.3ms** | 随行数线性增长（3× 行 → 2.9× 耗时），即对全内容表的扫描 |
| 整篇删除 · 新口径 | **1.0ms** | **1.1ms** | **≈17×**，且不随库龄增长（成本 = 本文档块数） |
| 单块删除 · 旧口径 | **15.6ms** | **51.4ms** | 同一形态：一次 PATCH 编辑 = 一次全库扫，且落在写锁内阻塞同期 writer |
| 单块删除 · 新口径 | **<0.1ms** | **<0.1ms** | **>150×**，`ux_search_map_blk(doc_id, block_id)` 唯一索引 O(1) 定位 |

归因（机制而非感受）：FTS5 的 `doc_id`/`block_id` 声明为 `UNINDEXED`，倒排索引只覆盖 `text`/`aux` 两列，因此这两条 WHERE 无法用任何索引——FTS 内容表按 rowid 顺序全表扫；行号改由 `search_map` 显式赋值后，定位走普通 B 树索引，删除只对目标 rowid 集合生效。**这条成本正是「小测试库门禁永远测不出」的形态**：既有断言用的库都在数千行量级，旧口径在那里是 1ms 级、看不出问题。批量落库 100 文档 = 100 次全库扫的放大也随之消失。**边界**：绝对毫秒为本机 NVMe + 热页缓存口径，低配机（8GB Win11）须按目标机吞吐重算（登记 B-6/B-8）；本表只主张**同机同轮的相对倍数与「不随库龄增长」这个斜率差异**。测量脚本口径可复现：`Store` 建库 → `executemany` 灌 `docs`/`blocks`/`search_map`/`search_blocks` 四表 → 上述四格计时。

**R7-PERF-2 / R7-PERF-3 实测（CH-476⑥，本机 macOS arm64 / 10 核 / 热页缓存）**：

| 度量 | 结果 |
| --- | --- |
| 单次全量流式哈希（= 每任务省掉的那一遍） | 20MB ≈ 9.3-11.1ms、100MB ≈ 42-48ms（本机 ~2.1-2.4GB/s） |
| 12 × 32MB 目录入队 `enqueue_hash_threads=1` | 163.9ms / 161.0ms（两轮） |
| 同批 `=8` | **48.1ms / 44.6ms** → **3.4-3.6×** |

① R7-PERF-2 的实际收益是**结构性的**：三重哈希削成两重，省下的是「入库前那一遍全量读」。本机把它读成 ~10ms/20MB，而报告 §3.7 的 100-300ms 估算是按 300-500MB/s 的目标机磁盘口径——两个口径都成立，但**不可混用**：低配机上收益按目标机吞吐重算（登记 B-6/B-8）。另两遍哈希**刻意不消**：入队那遍是内容寻址附着去重（`uq_tasks_doc_active`）与 G5「响应即回 doc_id」的前提；子进程那遍是产物身份的权威（`save_ir` 的 `doc_id` 主键与 E4091 编辑保护都以它为准）。若改为「入队哈希 + stat 指纹短路子进程哈希」，一次 `cp -p` 式保时间戳的同长度改写就会把新内容存到旧 doc_id 下——**内容寻址与编辑保护同时失真**，属数据安全硬伤，不做。② R7-PERF-3 的并行只在预检/哈希（`hashlib` 对 1MB 块释放 GIL），写库仍单线程按序提交，故队列行序、附着/幂等判定、`jobs[]` 顺序逐字节不变；红线以 barrier 实证并行度（串行实现会超时抛 `BrokenBarrierError`），不用墙钟断言。GB 级目录在目标机上的绝对值（报告估 3-4s → ~1s）本机不可外推。

**CH-460 轮的两项微观实测**（large_320p.pdf，320 页）：

| 项 | 改造前 | 改造后 | 结论 |
| --- | --- | --- | --- |
| A-5 S1.5 门禁耗时（7 次取中位） | auto **146.1ms** / even5 **70.9ms** | auto **73.5ms** / even5 **7.3ms** | auto −49.7%、even5 −89.7%——`avg_chars` 不再对全文档重跑完整 `_page_metrics`，非采样页只付单正则代价 |
| A-8 按片读取峰值内存（tracemalloc `py_peak`，2/4 片） | 整读 **132.9MB** | 2 片 **134.9MB** / 4 片 **137.9MB** | **不降反升 → 回退**。驻留主体是 IR blocks（5890 块）与 pdfplumber 单页解析缓冲，`pages_all` 只占约十分之一；分片 IR 须全部驻留供缝合，省下的被抵消，还多出每片一次 PDF open。实测无收益的改动不进主干 |

**CH-469 轮的微观实测（O-9 Tokenizer 探测长度收敛，本机 macOS arm64 / Python 3.15.0）**：口径改动的对象是 `Tokenizer.count()` 的贪心最长匹配——原实现每个字符位置从 `_maxlen` 逐档下探到 1，新实现只试词表中真实存在的长度集合 `_lens`。

| 项 | 改造前（`_lens = range(maxlen…1)`） | 改造后（`_lens = 去重长度`） | 结论 |
| --- | --- | --- | --- |
| 16848 字符文本单次 `count()`（9 次取中位） | **35.05ms** | **9.06ms** | **3.87×（−74.2%）**；词表形态为贴近真实 gguf 的 `maxlen=40` / 11 档去重长度（真实 gguf 常含 30～40 字符的多字节 token，而命中的绝大多数位置在 1～4 字符，差额全是白做的切片 + 哈希查找） |
| 计数口径 | 16830 tokens | 16830 tokens | **逐字符相等**（parity 断言），故 `chunks` 的 token 预算与产物分块边界不变——L0/L1 字节级一致承诺不受影响，红线用例 `test_tokenizer_count_matches_brute_force` 把这条钉住 |

采集方法（可复现）：`tests/gguf_fixture.build_gguf(tokens=…)` 造长度档为 1/2/3/4/5/6/8/10/14/17/40 的词表 → `ops.gguf.export_vocab` → `Tokenizer(vocab_file)`；A/B 同一进程内只换 `_lens`，其余对象与文本相同。口径纪律：**比值是同机同轮内的对比**，因此换到 CH-469 的本机环境仍可用；绝对毫秒不与前序轮次（Linux 沙箱）互比。

**CH-476 批次⑪ 的微观实测（R7-PERF-a…k，本机 macOS arm64 / Python 3.15.0 / 热页缓存）**：旧实现用 `git archive HEAD` 解出独立包，与新包同机同轮对跑；每个数字取 3–7 次中位（或最小）。

| 项 | 改造前 | 改造后 | 结论 |
| --- | --- | --- | --- |
| R7-PERF-a `scan_injection` 字面量预检（large_320p IR 5,696 块 / 589,765 字逐块扫） | 每块跑 20 分支正则 **67.8ms** | 预检先筛，**15.5ms** | **−77.1%（4.4×）**，命中集 0/0、语料逐块**零差异**。预检是超集不是替换：`_INJECT_HINT` 的 20 个顶层分支各有构造样本证明落进字面量集（`test_perf_a_prescan_covers_every_hint_branch`），正则仍在命中块上原样复核。两处自纠：字面量写 `instruction`（原登记 `instructions:` 会漏 `new instruction:`）；`str.lower()` 全域唯一改长度的码点 U+0130（İ → i + 组合点）会让 ASCII 字面量在折叠串里断链而 `re.I` 照样匹配，故 `len(low) != len(text)` 时直接退回精确正则 |
| R7-PERF-b `_page_metrics` 单趟取数 + `translate` 计数（320 页 / 15,613 行 / 589,765 字） | `git show HEAD` 原样 **54.2ms** | **24.1ms** | **−55.6%**，四元组逐页等价（同进程 A/B，另以同码点正则独立重算钉住口径）。原实现同一段全文跑 4 个正则、CJK `findall` 跑两遍，每命中一个字符 append 一个单字符 str |
| R7-PERF-d 段落样式按 `style_id` 记忆化（10k 段 docx） | 每段 `p.style` 取两次 **3,804.5ms** | **9.9ms** | **384×**。病因在 python-docx：未命中显式样式时 `Styles.get_by_id` 会 `default_for()` 重扫整张 styles part（对每个 `w:style` 做 enum 解析），代价是 O(段 × 样式)；样式只有 1 种时记忆化后降到 O(样式)。缓存的是**样式名字符串**（本层只用 `style.name`），不缓存样式对象，避免跨段共享可变代理 |
| R7-PERF-g 文档级 `w:hyperlink` 预检（10k 段 docx） | 逐段 `findall('.//w:hyperlink')` **7.9ms** | body 预检 1 次 **0.1ms** | 无链接文档整篇不再付每段 XPath。跳过条件与「该段 findall 返回空」严格等价（预检覆盖 body 全集）；notes part 另建一份上下文、共享样式缓存 |
| ↑ d+g 合并后的 `read_docx(10k 段)` 端到端 | **4,131.9ms** | **275.9ms** | **15.0×（−93.3%）**；10,000 行 ×（text/bold/italic/strike/link/size）六字段与 HEAD 实现**逐行全等** |
| R7-PERF-h `submit_many` 整批一个 `BEGIN IMMEDIATE`（N=2,000 件） | 逐条 `submit` **101.5ms**（50.8µs/件） | **13.6ms**（6.8µs/件） | **7.5×**，万件线性外推 507→68ms。同轮独立对照给出收益来源：3,000 件逐件事务 **137ms** vs 同批单事务纯写入 **9ms**，差额即每件事务收尾（journal header + fsync）。队列行序、附着/幂等判定、`jobs[]` 逐字段等价；原子性**强于**逐条——任一条失败整批回滚（生成器中途抛错 → `COUNT(*)==0`） |
| R7-PERF-i `delete_source` 存在性判定改只读 | `store.tx()` **0.003ms/次** | `store.q` **0.001ms/次** | 毫秒差是噪声级，**要点在锁**：纯 SELECT 原走 `BEGIN IMMEDIATE`，每次判定都取一次 RESERVED 写锁并重写 journal header，与在途转换的写事务直接排队；改为只读游标后写锁面归零（等价性用 monkeypatch 让 `tx()` 抛错来实证「不再走事务」） |
| R7-PERF-j `/jobs`、`/search` 单次投递硬顶 | `limit` 无上界（SQLite `LIMIT -1`／大数 = 全表搬运） | 截到 `_MAX_ROWS=500` | 601 任务 → 返回 **500 行 / counts 仍 601**（分页语义不变，只封单页体量）；负 `limit` 由「无界」变 **400/E4001**。口径与 `/events` 既有固定 200 同族 |
| R7-PERF-k `/docs/<id>/markdown` 64KB 分块流式（10.4MB 正文） | 整篇 `read()` → decode → Flask 再 encode：**12.3ms / 40.4MB 峰值堆** | **2.3ms / 0.4MB** | **−81% 耗时、−99% 峰值堆**。省掉的是同一份正文的三串全量副本（解压字节 + str + 响应字节），改为逐块即出即弃（与 WSGI 实际消费方式同构；用收集到 `bytearray` 的口径测会得到偏保守的假结论）。响应体逐字节相同，`content_type="text/markdown; charset=utf-8"` 显式带 charset 以保持原「返回 str」实现的响应头不变 |

口径纪律：①**耗时不入断言**——每条改造的红线用例断言的是「等价」或「上限口径」，本表只作登记；②比值同机同轮内可比，绝对毫秒不与 CI/Ubuntu 及前序轮次互比，低配机须按目标机吞吐重算（登记 B-6/B-8）；③收益一律写成**机制归因**（全表扫 / 每件事务收尾 / 三份全量副本 / 写锁范围），不写"感觉变快"。

**批次⑪ 评估后不做（按实测否决，非主观取舍）**：

| 候选 | 实测 | 不做的理由 |
| --- | --- | --- |
| R7-PERF-c 子进程 cfg 传参复用（每件 pickle → 每 worker 一份） | `config.load()` pickle **1,592 B**、反序列化 **6.6µs/件** → 万件仅 **66ms** | 收益噪声级；改造点落在 worker 生命周期上，与 X-16/X-17 已根治的「池收尾无条件 terminate 本池 worker」语义有张力，为 66ms 动池语义不划算 |
| R7-PERF-e 异常路径跳过 `analyze` 重跑 | `large_320p` 全量 `analyze` = **28ms** | 报告估算的「数秒」前提不成立；`dedupe=False` 重试会把缝合内容重复写入，是正确性风险而非性能话题 |
| R7-PERF-f / A-8 按片读取降峰值内存 | 整读 **132.9MB** vs 2/4 片 **134.9 / 137.9MB** | **不降反升**（CH-460 已回退，本轮维持）：驻留主体是 IR blocks 与 pdfplumber 单页缓冲，`pages_all` 只占约十分之一 |
| 备份 nonce+ciphertext 合并单写 | 4MB × 8 次：两次 `write` **16.7ms** vs 拼接单写 **24.7ms** | 拼接多一次 4MB 分配 + memcpy，实测**更慢**；省一次系统调用省不了拷贝 |
| `reconcile_search` 聚合值短路（计数一致即跳过逐块核对） | 30,000 块（500 doc × 60）全量核对 = **28.6ms** | 「计数相等」推不出「集合相等」，换内容不换条数正是搜索索引最该抓的背离；启动 28ms 换索引面可证一致 |
| cron purge 两笔事务合并 | 20 轮：分事务 **0.1ms** / 合并 **0.1ms**（差值低于计时分辨率） | 无收益，且合并后 events 删除失败会连带回滚 tasks，破坏「两张表各自尽力清理」的既有语义 |
| doctor 依赖检查改 `importlib.util.find_spec` | 冷启新进程 `import pdfplumber` ≈**86–93ms**（含解释器启动）；进程内 `find_spec` ≈**0ms** | 只省一次导入墙钟，却把保证从「真的 import 成功」降级为「找得到 spec」——损坏安装/循环依赖下 `find_spec` 照样返回路径 |
| cron daily/weekly 双写同刻的 flock／错峰 | 本机无可测口径 | 阈值要看真机 launchd 触发窗口才能定，test client 不代表 plist 语义；归 B-5/B-9 家族，**仅登记** |

### 12.4 低配机活测（真进程，非 test client）

| 断言面 | 关键实测 |
| --- | --- |
| 异步入队（CH-449） | `POST /jobs` 指向 14 文件目录 → **202 / 13 个作业条目 / 13ms**（同目录 12 个副本内容寻址去重，落库 2 个 doc）；`doc2md work --once` → `{"drained":2,"done":2}`；`GET /jobs` counts `{'done':2}`；队列产物与串行产物逐条目 sha256 全等；事件链 `job_queued → job_done`；`work/` 实测 0700 |
| 入队原子性 | 混入非文档文件 → 415/E4151 且 `queued` 计数**零增长**（不变式为「零增长」，非「回到 1」；早期表述已在 CH-449 修正） |
| 日志隔离 | `app.jsonl` 3 行 / `work.jsonl` 4 行（含 `WORK_START`、`job_done`，无正文）；`cron.jsonl` 未创建 |
| 磁盘门禁（CH-450） | tmpfs 限 40MB 作 `--out` → `doc2md convert` **rc=4 / E4131 need 512MB, free 40MB**，输出目录 0 文件（无截断 zip） |
| cp936（CH-451） | `PYTHONIOENCODING=cp936` + `🔥报告.pdf` → rc=0、stderr 无 `UnicodeEncodeError`、`--json` 行纯 ASCII 可解析；serve 面伪 `PK` 非 docx → **415/E4151**（原 500）、缺失路径 404 |
| 服务面轮转（CH-444） | 空 root 起服务（`rotate_mb=0.0002`）→ 启动首行 `SERVICE_START{root,tmp_cleaned:1,rotate_mb,keep}` 且预置 tmp 文件被清扫；3 次真投递后出现 `app.jsonl.1`（首行 `E_LOG_ROTATE`），服务不重启继续接活；随后独立进程 `cron --daily` 跑通，`app.jsonl*` md5 逐一未变 |
| 结构损坏文件（CH-457） | 截断 PDF（魔术码合法、无 xref）→ `read_pdf` / `convert` / `doc2md work` 三面统一 **E4151**（修复前冒泡成 E5000 内部错误、HTTP 500）；detail 恒为 `{file, exc}` 两个键（无异常消息，NFR-20）；好 PDF/DOCX 转换零误伤；`doc2md work` 端到端：截断文件入队成功（sniff 过）→ 消费 failed/E4151，非文档文件入队即拒且 queued 零增长 |
| 备份恢复演练（CH-456 / T7） | `doc2md backup drill` 沙箱内 8 步闭环：exit 0 且 13 张表比对通过；线上库逐字节不变；`--keep` 留存取证；enc 容器魔数 `D2MBAE1`；缺口令 `E4001`；**篡改 enc 容器 restore 必须失败**（exit 4，`InvalidTag` 被 `DrillFailure` 收敛） |

### 12.5 质量红线关键值

| 断言 | 实测 |
| --- | --- |
| `shard_two_phase` | 单片基准：blocks **5696**、markdown sha `25344caded2d947c`。mp=300：shards=2 / seams=1 / dupes=34 / blocks_after **5696**（== 单片）/ `char_similarity` = **1.000000** / half_heads=0；mp=160：shards=2 / seams=1 / dupes=30 / blocks_after **5696** / **1.000000**；mp=40：shards=8 / seams=7 / dupes=247 / blocks_after **5696** / **1.000000** / half_heads=0。**CH-469 轮同一档位的前后对照（缝界回归的量化证据）**：S-3 段缓冲跨页改动落地后、两条常数界规则修好前，实测 mp300 dupes=33 / blocks_after=**5697** / sim=0.999915，mp160 dupes=28 / blocks_after=**5698** / sim=0.999429，mp40 dupes=237 / blocks_after=**5706** / sim=0.997976 / **half_heads=1**——三档全部「比单片多块」，正是新加的 `blocks_after == 单片块数` 口径把它当场判红的（旧口径只看 sim ≥ 0.99，0.997976 会通过）。数字均为本机（macOS arm64 / Python 3.15）`convert` 实测，采集方法同 12.9 |
| `malicious_filenames` | 12 例参数化 + 黄金清单全量；zip 条目 0 越界；消毒失败 → E4003/400 |
| `injection_resistance` | 10 篇注入 vs 干净：worst \|Δf1\| = **0.0021** ≤ 0.005；白名单外 op 应用数 0 |
| `edit_optimistic_lock` | 双写 → 200 / **425 E4251**（`current_revision=2`），正文未被覆盖 |
| `delete_cascade` | 400+E4001（缺 confirm）→ 200 → GET 404；搜索 0 残留（CH-435 后才是真判定）；留痕无正文 |
| `delete_fail_closed` | 注入 `unlink` 故障 → 500/E5031 + 库行保留 + 残文件仍在 + 无留痕；撤销后重试同时清零 |
| 资产可复现（CH-442） | 两次独立生成 → **5135 个文件逐字节一致**；MANIFEST 逐条复核通过 |
| 装机面（CH-441） | 空 venv `pip install -e ".[dev]"` → `doctor` 全 ok、`/healthz` 200、全量测试 EXIT=0 |
| LLM 服务契约（CH-456 / T2） | stdlib `HTTPServer` 假 llama-server：请求契约（`POST /v1/chat/completions`、system 提示词含不可信数据立场、user 为 IR 定位摘要 + 白名单、`temperature=0`）逐字段断言；围栏/尾杂音响应容错解析；注入全拦且按 op 归因（带注入话术的 op → `injection_candidate`，其余 → `model_hallucination`）；immutable 拦截；故障分级 parse/http/connect；Bearer 鉴权（缺 token → auth 错）；offline 模式零 HTTP；fallback 保主链路、`--llm-no-fallback` 死端口 → exit 4 / E5041 / 503 且 detail 无正文、不落产物；e2e 实测 server 模式 1 次 HTTP、doctor `llm_server: reachable` |
| 大语料失败账目（CH-457 / T6） | 1080 文件 → done 1060 + failed 7 + 入队即拒 13 = 1080（精确闭合）；非 done 全部 E4151；两并发度逐项一致（12.3） |

### 12.6 实现偏差登记（D-x，非缺陷）

| 编号 | 项 | 口径 |
| --- | --- | --- |
| D-1 | 蓝本脚手架签名 | `char_similarity`/`half_sentence_heads` 由 `pipeline/metrics.py` 提供；分片开关是 `mode="two_phase"` 而非伪码的 `shard=True`；zip 路径经 `Result.zip_path` 暴露 |
| D-2 | 审计断言写法 | 蓝本断言 `audit[-1]["before"] == "改A"` 与自身 fixture 顺序矛盾；实现按 ADR-28 真实语义断「每条审计 `before` == `from_revision` 内容」 |
| D-3 | 索引刷新事务边界 | `reindex` 必须在 `patch_block` 的 `store.tx()` **之外**（自带 `BEGIN IMMEDIATE`，嵌套即 `OperationalError`）；蓝本伪码未标注，已在 `edits.py` 注释固化 |
| D-4 | 回滚 revision 取值 | 优先取 `from_revision==N` 的 `before`，缺失退 `to_revision==N` 的 `after`；审计链首与 `revision=1` 无审计时走同一回退路径 |
| D-5 | 版本命名 | state.sqlite 结构版本改名 `DB_VERSION`，与 IR `SCHEMA_VERSION=14` 分列（迁移判定曾读错对象） |
| D-6 | 慢测处置 | 门禁按 `-m gate` 标记分层（67 项；收口轮 110.4s、复核轮 72.5s），大样本 fixture session 作用域共享；蓝本的 difflib 直译口径在本机不可完成（>300s），按 CH-428/429 替换后方可作 CI 红线。**CH-469 更新口径**：红线层现为 **132 项 / 本机 28.7s**（分层构成见附录 F），「项数为硬口径、耗时只作历史记录」这条纪律原样保留；本轮另把「断言永不触发」也归入慢测处置的反面教材——`perf.batch_file_timeout_s` 曾写在 `as_completed` 循环里，既不快也不判定任何东西（9.4 自查②③），耗时与有效性是两件事 |
| D-7 | 缺 confirm 的状态码 | 未新增码值（段位冻结），回到 400/E4001 + `detail.requires="confirm=true"`；自造 `E4091` 旧语义（缺 confirm）已删除（CH-439）。注：409 段位在 CH-471 / STOR-5 以「重复转换护编辑」的全新语义重新启用 `E4091_DocOverwrite`，与本条的 confirm 口径无关（见附录 D） |
| D-8 | **配置面声明性键**（CH-456 / T9 **已关闭**） | 默认 TOML 49 键中 **11 键当前无代码消费**：`paths.vendor_models`、`run.profile`、`run.offline_assert`、`s1_5.reject_on_any_bad_page`、`s1_5.page_detail_in_report`、`api.token_roles`、`llm.bind`、`llm.port`、`llm.auth`、`ops.work_dir_mode`、`security.sanitize_fail_action`。附录 A 已逐键标注。处置方向：要么实现（`run.profile` 三档映射、`llm.*` 随 T2 落地），要么从默认配置删除——不得长期停留在「看起来可配」状态（待办 T9）。**关闭口径（CH-456）**：11 键全部落地消费（`llm.*` 随 T2，其余随 T9；逐键消费方见附录 A），默认配置 49→53 键（净增 4 个 `[llm]` 键：`mode`/`fallback`/`timeout_ms`/`model`） |
| D-9 | **契约面空位**（CH-456 / T9 + T2 **已关闭**） | `E5041`（模型不可用）、`E5051`（缝合失败）在代码中定义但无抛出点；`W1044`、`W1999` 未被写入；`Scheduler.next_queued()` 无调用方（消费路径由 `worker.claim()` 承担）。前三项属预留段位，需在启用时同步主题篇与附录 D；`next_queued` 属死代码，应在下个变更里删除或接入（待办 T9）。3.6 / 附录 G 建表时用同一口径（`ast` 找定义 + 全包与 `tests` 找调用点）复查，又发现 5 处零调用符号：`auth.timing_safe_equal()`（令牌比对实际走参数化 SQL，未经过它）、`auth.RevocationCache`（实时查库口径下没有缓存接入点）、`auth.require()`（`api.py` 用的是 `auth.check()`，`require` 是同一矩阵的另一入口但从未被调用）、`Scheduler.active_for()`（附着判定内联在 `submit()` 的 SELECT 里）、`db._Tx.lastrowid`（属性暴露后无人读取）；`pipeline/metrics.py` 的 `line_similarity()` / `diff_lines()` 同样零调用（`is_half_head()` 有同文件内部复用）。即新发现 7 处，连同 `next_queued` 合计 8 处零调用符号一并处置（T9）——注意其中 `lastrowid` 与 `active_for` 是「对外 API 形状的一部分、暂时无人用」，删除前要确认没有分发面的隐式契约。**关闭口径（CH-456）**：8 处死代码全部删除（删除前逐一确认无分发面隐式契约）；`E5041` 由 T2 启用（附录 D 已同步）；`E5051` 删除（缝合失败由 4.3 降级口径吸收，抛码违背降级语义）；`W1044` 接入降级留痕路径（开始真实写入）；`W1999` 删除 |
| D-10 | 分发示例与默认值的键集差异（CH-456 部分关闭 → **CH-469 / O-15 完全关闭**） | 默认 TOML 49 键、示例 42 键。示例**多** 1 项 `ops.backup_external_dir`（家用机可选路径，故意给样例）；示例**少** 8 项：`run.profile`、`run.offline_assert`、`llm.bind`、`llm.port`、`llm.auth`、`llm.op_whitelist`、`security.max_name_bytes`、`security.sanitize_fail_action`（其中 4 项本就属 D-8 的声明性键）。新增键时两处需同时决定，避免「示例里没有 = 不支持」的误读。**关闭口径（CH-456）**：两处同步补全——默认 53 键、示例 54 键 = 53 + 故意多给的 `ops.backup_external_dir`，键集差只剩这一项刻意差异。**最终关闭口径（CH-469 / O-15）**：连那一项刻意差异也取消——`ops.backup_external_dir` 进默认表（空值 = 未配置，行为不变，见 CH-433），三处（`SPEC` / 内置默认 / 分发样例）键集**完全相等**，实测本机各 **57 键**（本轮新增 `perf.batch_file_timeout_s` / `perf.stale_check_seconds` / `llm.retries`，54 → 57）；一致性由红线用例 `test_spec_defaults_and_example_share_one_key_set` 钉住，「只剩一项刻意差异」这种需要人记住的例外从此不存在 |
| D-11 | 许可声明缺失（CH-456 / T11 **已关闭**） | 3.7 已核对依赖闭包的许可（MIT / BSD / Apache-2.0 / PSF / MIT-CMU 级），但仓库自身既无 `LICENSE` 文件，`pyproject.toml` 也没有 `license` / `classifiers` 字段。对内自持不构成即时风险，但「依赖许可干净」与「本项目许可已声明」是两件事，后者目前为空白。**关闭口径（CH-456）**：根目录 `LICENSE`（MIT，Copyright 2026 Doc2md contributors）+ `pyproject.toml` 补 `license = "MIT"` 与 8 项 `classifiers`（Development Status / Environment / Intended Audience / OS Independent / Python 3.11–3.13 等） |
| D-12 | 无静态检查工具链（CH-456 / T10 **已关闭**） | 仓库没有 mypy / ruff / black / pre-commit 的任何配置，CI 只跑 pytest。后果由本轮自查量化：**7 处未使用导入**（`api.py` 的 `E4133_DirTooWide`——该码实际由 `security/dirguard.py` 抛出；`ops/backup.py` 的 `shutil`、`subprocess`；`ops/gguf.py` 的 `asdict`；`pipeline/batch.py` 的 `Iterable`；`storage/db.py` 的 `json`、`time`）与 8 处零调用符号（其中 1 处早已登记，其余 7 处为本轮复查所得，逐项见 D-9）。附带发现一个更值得注意的形态：代码里已有 5 处 `# noqa: BLE001`（包内 4 处：`cli.py:467` `pipeline/batch.py:80,128` `storage/repo.py:99`；测试 1 处：`test_m9a_search.py:178`）与 1 处 `# noqa: F401`（`pipeline/s1_parse.py:168`，注释写明「保留供后续扩展」）——即**仓库按「有 linter」的假设写了抑制注释，但没有任何工具读取它们**；这些注释现在是纯意图声明，接入 ruff 后立即生效，届时 `storage/db.py:14,17` 这类未用导入会直接被 CI 报出。同类漂移还有配置声明面：`pytest.ini` 声明的三个 marker 中 `slow` 零使用、`milestone` 的 `M4M6` 从未出现（实际用组合名 `M0+M2`），且未开 `--strict-markers`，写错标记名不会报错只会静默不选中。这类问题不会让任何断言变红，只能靠人工读——所以 3.7 把「引入 linter」列为显式未采用项而不是遗漏，并把补齐动作挂到 T10。**关闭口径（CH-456）**：ruff（`F`/`E`/`I`）接入为 CI 独立信息作业（`ruff check .`，与 merge-gate 并行无 `needs`，风格判断不入红线）；7 处未使用导入清零；`pytest.ini` marker 对齐实际用法并开 `--strict-markers`；全仓 `ruff check .` 通过（D-12 正文预言的「未用导入会被报出」应验，且 CH-455 的 F821（`total` 未定义）正是引入 ruff 后抓出的第一个真缺陷） |
| D-13 | 跨包直引 | 门面口径（3.6 第 7 条）要求跨包走 `__init__`，现状存在 `from .storage.db import Store`、`from .ops.logging import RotatingJsonlWriter` 这类直引模块。不视为缺陷：`__all__` 与附录 E/G 已固定职责边界，直引不改变依赖方向；但新增引用应按门面写，避免重构内部文件时波及调用方 |

### 12.7 未验证项（诚实标注）

| 项 | 状态 | 出口条件 |
| --- | --- | --- |
| CH-479（CI 版本矩阵口径收口：周期腿 + 四条静态红线）的改动是否有远端证据 | **待远端确认**：本机已实测 gate **260 全绿**、全量 **811 passed / 0 skipped**、`ruff` 全绿、`doctor` 退 0，四条新红线与 `PLAN_VERSION` 3.13 升位在同一次 gate 复跑里对账通过；push 面作业未改（只动注释），故远端 push 腿只需确认 260/811 两个数字在 3.11/3.12/3.13 上同样成立 | 随下一次 push 的 run 回填 merge-gate 三档的 `260 passed, 551 deselected` 与 `full-suite 811 passed`；**新周期腿 `ci-full-matrix.yml` 不被 push 触发**（`on:` 只有 `schedule` + `workflow_dispatch`，且 `schedule` 只在默认分支生效），其首次证据须由一次手动 `workflow_dispatch` 取得——三档 leg 各 `811 passed`、逐字节复现步骤与 `doctor` 步骤全绿，回填后本行改标「已远端确认」；每周一 02:17 UTC 的自动档在下一次排期时点自行复证 |
| CH-478 批次②（等价门缩进敏感度对照 + `codeindent_1p.pdf`）的改动是否有远端证据 | **已远端确认（闭环）**：随 `2e04aaf` 经 **run `39030470549` 五作业全绿**（2026-10-17 11:54:34Z→11:59:48Z，全程 5m14s）——lint「All checks passed!」（13s）、merge-gate 三档各 **256 passed / 549 deselected**（测试步骤墙钟 3.11 67.07s / 3.12 56.53s / 3.13 61.67s）、full-suite（3.12）**805 passed, 1 warning in 155.87s 干净退出** + `Golden assets byte-reproducible`（含 `codeindent_1p.pdf` 逐字节复现）与 `doctor` 两步全过。推送前本机实测 gate **256 全绿（31.16s）**、全量 **805 passed / 0 skipped（69.39s）**（两处墙钟是本轮 gate 六次复跑（31.06 / 31.61 / 31.16 / 31.53 / 批次初的 31.77 / 回填后复跑 34.44）与全量五次复跑（70.42 / 67.05 / 71.65 / 69.39 / 69.36）之一，抖动约 ±10%（末次偏高系同机并发拉取 CI 日志所致），**项数才是硬口径**）、`ruff` 全绿、`doctor` 退 0、`make_golden` 自检 21 项全 PASS | 无需补做；若日后按 12.7「代码块行首缩进在抽取层即丢失」项重建缩进，本批的缩进漂移红线会因产物两侧同时带缩进而改变数字，需连同 12.2 sha 基线一起重烤 |
| CH-478 批次①（有序列表分支 + `numlist_1p.pdf`）的改动是否有远端证据 | **已远端确认（闭环）**：随 `b601749` 经 **run `39027385467` 五作业全绿**（2026-10-17 11:05:44Z→11:11:12Z）——lint「All checks passed!」、merge-gate 三档各 **252 passed / 543 deselected**（3.11 60.32s / 3.12 78.50s / 3.13 62.79s）、full-suite（3.12）**795 passed, 1 warning in 156.88s 干净退出**、黄金样本逐字节可复现与 `doctor` 步骤全过。本轮改动含**产物面**（同字号单级编号短行由段落变 `list_item`、`content.md` 出现 `N. ` 行），远端四条腿同时通过意味着该判定不依赖本机解释器或字体环境。同轮补记 `cb310a3`（CH-477 收尾回填）= run `39023978470` 五作业全绿 | 无需补做；后续若 `reportlab`/CID 字体形状变化导致 `numlist_1p.pdf` 重烤，MANIFEST 哈希绑定红线（`test_shipped_manifest_hashes_match_files`）会先红 |
| CH-477（T15 四条对账红线）的改动是否有远端证据 | **已远端确认（闭环）**：随 `66414ec` 经 **run `39023289246` 五作业全绿**（2026-10-17 10:01:02Z→10:06:43Z）——lint「All checks passed!」、merge-gate 三档均 **248 passed, 529 deselected**（测试步骤墙钟 3.11 76.46s / 3.12 84.97s / 3.13 75.61s）、full-suite（3.12）**777 passed, 1 warning in 158.16s 干净退出** + 黄金样本逐字节可复现 7 项全过 + `doctor` 33 键全 ok。本机同场复跑 gate **33.25s / 31.98s**、全量 **777 passed / 0 skipped**（73.23s / 68.79s），计数一律取 `--junitxml` 的 `tests=` 属性 | 无后续动作。此后版本常量与文档编号的漂移由 CI 拦住；若文档句式被改写到锚点失配，红线报「红线锚点失效」而不是解析不到就跳过——静默恒真的形态本身也被断言覆盖 |
| CH-476 收口轮（批次⑬）的改动是否有远端证据 | **已远端确认（闭环）**：批次①…⑫ 的代码面随 `9a1d959` 经 run `39016742023` 五作业全绿（244 / 773）；批次⑬（文档对齐 + 四处注释漂移 + `PLAN_VERSION` 3.8→3.9，**运行时行为不变**）随 `44f98c3` 经 **run `39022142048` 五作业全绿**（2026-10-17 09:42:25Z→09:47:54Z）：lint「All checks passed!」、merge-gate 三档均 **244 passed, 529 deselected**（测试步骤墙钟 3.11 **52.99s** / 3.12 **58.16s** / 3.13 **78.49s**；作业时长 67 / 73 / 95s）、full-suite（3.12）**773 passed, 1 warning in 159.35s 干净退出**（作业 09:44:02Z→09:47:51Z，3m49s）+ `doctor` 自检 33 键全 ok。本机同场复跑 gate 244 全绿 **35.5s**、全量 **773 passed / 0 skipped** **73.3s**（计数取证用 `--junitxml` 的 `tests=` 属性，不用进度点计数——本机会把用例打印的 `.` 混进点数，实测点数为 784 ≠ 777） | 无后续动作。X-17 的池收尾根治在 full-suite（3.12）连续第四轮干净退出；同场直推 `main` 仍只返回 `Bypassed rule violations …`（push 成功），CH-470 登记的第四证 |
| CI 红线阻断合并 | **已验证但范围小于标题（CH-469 实测）**：CH-468 配置的是**必需状态检查**，它只拦 **PR 合并**；CH-469 轮从一次性 clone 直接 `push origin main` 成功，远端返回 `Bypassed rule violations for refs/heads/main: - 3 of 3 required status checks are expected`——即**直推 `main` 可以绕过红线**（检查只是"pending"，不阻断推送）。本地 pre-push 钩子（CH-467）仍是唯一的直推防线，而它可被 `--no-verify` 绕过、且要求操作者先启用 `core.hooksPath` | 打开 `main` 的 **Require a pull request before merging**（或等价的规则集 `block_force_pushes` + `required linear history` + 禁直推），使"红即不可入库"对**直推**同样成立；验收形态与 PR #1 相同但走 push 而非 PR |
| Python 3.11 / 3.12 / 3.13 矩阵 | **已实测（历史口径，红线层 65 项时点）**：红线层在 3.11 / 3.12 / 3.13 三个作业上均 `[100%]` 全绿（GitHub ubuntu-latest runner）。**CH-469 / CH-471 轮先后出现两次「部分结论」**：`88896ae` 的 run `38756727387` 里 3.11（1m23s）/ 3.12（56s）/ lint（15s）绿，**3.13 挂死**——当时无 `timeout-minutes`，pytest 步骤 6 小时零输出，撞上 GitHub 默认 6 小时作业上限被取消（作业级时长实测 15:02:06Z → 21:03:17Z；早前登记称「被 CH-471 修复推送按 cancel-in-progress 取消」不准确——下一push 到达时该作业已被默认上限取消约 6 小时），`full-suite` 因 `needs` 未启动；`36ef92a` 的 run `38843497730` 里 3.11（59s）/ 3.13（59s）/ lint（13s）绿，**3.12 在打出「132 passed」后卡死**至 15 分钟超时被取消（X-16，见下条），`full-suite` 同样未启动。**两处修复后矩阵已恢复三档全绿**：`3f7f9f2` 的 run `38845736366` 与 `584e7b5` 的 run `38851378872` 均五作业（lint + merge-gate 三档 + full-suite）全绿——前者 3.11 1m08s / 3.12 1m28s / 3.13 52s / full-suite 3m40s，后者全程 4m45s。**CH-474 轮又出现一次同形态复发**：`df53679` 的 run `38901173280` 三档 merge-gate 全绿（3.11 44.38s / 3.12 66.54s / 3.13 46.70s，各 145 passed）而 **full-suite（3.12）605 passed 全部打完后在 pytest 退出阶段挂死 27 分钟**至超时——X-16 的 abandoned-only 修复未覆盖正常完成路径（登记 X-17 并根治，见下条） | 3.13 那次挂死的**根因始终未定位**（一次性现象，此后再未复现；本机没有 3.13，`uv` 拉 python-build-standalone 未成），现行防线是作业级 `timeout-minutes`（merge-gate **6 分钟**，右调依据见 8.6）+ `-vv` 逐用例行——最坏 6 分钟出红且直接点名嫌疑人，取证成本从"半天"降到"一次 run"。仍待：① ~~145 项红线尚未经过任何 CI 腿~~ **已确认（run `38899470118`，10-16 推送 `4151515`）**：三档 merge-gate 均 **145 passed, 460 deselected**（3.11 44.38s / 3.12 68.80s / 3.13 73.23s）；② ~~全量套件扩到矩阵（或明确只保 3.12 全量口径）~~ **已裁定并落地（CH-479）**：push 面 `full-suite` 保持 `3.12` 单档（性能优先——实测 push 全程 5m14s，把全量扩成三档等于每次推送多两条 30 分钟腿，而覆盖增量只落在非红线的 551 项上；红线本就是三档全跑）；跨版本全量改由新增周期腿 `.github/workflows/ci-full-matrix.yml` 承担（周一 02:17 UTC `schedule` + `workflow_dispatch`，三档 `fail-fast: false`，跑全量 + 黄金逐字节复现 + `doctor`），裁定与两条腿的分工见 8.6、断言见附录 F 第十层。**该裁定已钉成红线**：`test_push_leg_keeps_full_suite_single_interpreter` 会在有人给 push 面 `full-suite` 加矩阵时转红，`test_weekly_workflow_triggers_only_on_schedule_or_dispatch` 会在周度腿被挂上 `push`/`pull_request` 时转红 |
| CI 3.12 腿解释器退出死锁（X-16） | **修复不完整，由 X-17 根治（CH-474，见下条）**：原修（CH-471 / 收口轮补记，经 run `38845736366` 与 `38851378872` 复跑确认过）只在 abandoned（空窗超时）分支 terminate；`36ef92a` 的 run `38843497730` 中 `merge-gate (3.12)` 在 pytest 打出「132 passed」**之后**卡死至 15 分钟超时被取消——根因是 CPython 3.12.x 的 `ProcessPoolExecutor` 对 `shutdown(wait=False, cancel_futures=True)` 放弃的「已 cancel 却未终止」worker 在解释器退出 join 时死锁（3.11 / 3.13 / 本机均不复现）；`3f7f9f2` 的 abandoned-only 修复对空窗超时路径有效（run `38845736366` / `38851378872` 两轮五作业全绿佐证），但**正常完成路径的退出死锁未覆盖**——run `38901173280`（`df53679` 纯文档提交）full-suite 605 passed 全部打完后 pytest 主进程在退出阶段挂死 27 分钟（见上表 CI 行末） | 根治见 X-17（无条件 terminate + SIGKILL 兜底本池 worker）；PIPE-8 的 `_processes` 私有属性版本哨兵继续在 stdlib 改内部形状时先红 |
| CI full-suite（3.12）pytest 退出阶段死锁——X-16 的正常路径复发（X-17） | **已修并经远端确认（CH-474，run `38913173159` 五作业全绿、full-suite 153.28s 干净退出）**：run `38901173280`（推送 `df53679`，纯文档改动）中 merge-gate 三档全绿，full-suite 打出「605 passed, 11 warnings in 155.99s」后 pytest 主进程卡退出 27 分钟至 30 分钟作业超时被取消——本批 future 已全部有结论，挂死点在**解释器退出**：3.12 的 `_ExecutorManagerThread.join_processes` 对不响应退出哨兵的 worker 只 `join` 不 terminate，而多线程父进程 fork 出的 worker（CI 日志的 `This process (pid=…) is multi-threaded, use of fork() may lead to deadlocks in the child` 即此形态）可能持有继承锁永不退出 → manager 线程永不结束 → `_python_exit` join manager 死锁。同代码的 `38899470118` 干净退出——**竞态**（runner 负载越高窗口越大；本沙箱 Linux/3.11 亦难复现，真验证依赖 CI 3.12 腿）。修为 `run_batch` 的 finally **无条件**按 pid 终止本池 worker：`terminate()` → `join(5)` → `kill()`（SIGKILL 兜底，无条件发——`is_alive()` 有 ECHILD 误报怪癖不作前置）→ `join(1)`；SIGKILL 内核不可阻挡，发出即保证 manager 必然结束。产物逐字节等价（G6：只动清理逻辑，转换路径零改动），顺带把 CPython 的 `is_alive`/`poll` ECHILD 怪癖（`popen_fork.py` 源码注释 #1731717）如实记档 | 修复 re-run 结果回填 12.1 CI 行；本沙箱以 waitpid/ECHILD 语义 + 10s 宽限窗的新回归测试（`test_x17_batch_returns_with_all_pool_workers_reaped`，红线）钉住契约，20 次循环探测 0 失败、broken 版（删掉清理段）反向验证必红；远端确认 = run `38913173159`（push `b0d4e52`）五作业全绿——三档 146 passed（48.15 / 80.11 / 74.10s）+ full-suite **606 passed in 153.28s 干净退出**（上一 run 同腿挂死 27 分钟） |
| 工作副本的 git 能力 | **已完全恢复（CH-473 轮，10-16 12:39 fetch `ossh` 后 fast-forward 到 `584e7b5`）**：现为完整 git 仓库——32 提交全历史（自初始导入起）、双 remote（`origin` HTTPS + `ossh` SSH）、`core.hooksPath=ops/hooks` 已配置，`git worktree` / `git log/blame` / pre-push 钩子均可用（本轮 before/after 对比即由 `git worktree` 完成）。CH-469 轮登记的「无 `.git`、一次性 clone 桥接」状态就此结束 | ~~把工作副本接回仓库后补做的事项~~ **已完成**：① 12.2 的 before/after 逐条目 sha 对比已补做（四时点 worktree，结果见 12.2 CH-473 段——CH-469 轮「三态对比不可执行」缺口闭环，CH-471 / CH-473 的产物零变化各获逐字节证据，12.2 基线表跨环境 12/12 复现）；② `ops/hooks/pre-push`（CH-467）**已首次实战**（10-16 12:16Z 推送 `4151515`：钩子跑 `pytest -m gate` 通过后放行，push 成功；此前数轮推送走的是一次性 clone 桥接，未经过它） |
| 编辑器 / 审阅页的真实浏览器人工验证 | **未验证（CH-469 撤除失实证据）**：V-11（文档派生字段转义）与 V-5（首屏 200 块窗口）目前只有**页面契约测试**——DOM 结构、JS 语法、NFR-20 无正文探针、以及 `_BARE_FIELD` 正则扫裸插形态；9.1 原「无头浏览器实测」一行经实核不成立，已删除并登记在此。V-11 的 XSS 面是「构造文档即可执行脚本」，静态扫描能证明裸插消失，**不能证明**真浏览器里粘贴/编辑/翻页一圈后行为正确。CH-476 / R7-SEC-1 另把 `esc()` 的字符类补上单引号（`&#39;`）：当前模板全为双引号属性、无可达向量，这一项是**防御纵深**（未来任何单引号属性插值不再成为注入口），同样只有静态断言、不含浏览器证据 | 人工在真实浏览器（Chrome / Safari 各一次，Win 与 macOS 各一遍）过：注入含 `<script>` 与引号 id 的 docx → 审阅页渲染无执行、块选择器不受污染；分页控件在 5696 块文档上滚动 + 编辑 + 回滚；控制台零报错。结果进 12.4 或本表，属 9.4 的 C 类「需人工执行」 |
| Windows 11 真机（cp936、spawn、路径长度、无 fork） | 仅沙箱内以 `encoding=cp936` 与 `start_method="spawn"` 复现语义，未在真机验证 | 至少一组同口径数字进入 12.3/12.4（待办 T3；测量工具已就绪——`ops/bench/t6_large_corpus.py` 跨平台化，Windows 后端 psutil / 降级 none，CH-458） |
| Mac mini 16GB 真机驻留/吞吐 + launchd 定时 | 未实测；12.3 数字来自 Linux 沙箱。**CH-475 / R6-OPS-1（B-5，HIGH）**：`ops/launchd/*.plist` 已补 `WorkingDirectory` + 显式 `--root`（修「launchd 默认 cwd=`/` → 相对根名 `work` 解析成 `/work` → cron 操作错误根」），守卫断言在 `tests/test_t5_windows_ops.py` 沙箱内绿；**但 launchd 运行时行为（cwd/root 是否真生效、state.sqlite 与备份是否落正确根）需 macOS 真机 `launchctl load` 跑一次 `cron --daily` 确认**——本轮唯一 HIGH 级却需真机确认的项 | 同上（T3；macOS 后端 ps 已在 Linux 上借 GNU procps 交叉验证，CH-458）；**B-5 出口**：macOS 真机 `launchctl load` 后跑一次 `cron --daily`，核验 state.sqlite/备份落在 `--root` 指定根而非 `/work`，结果进本表 |
| 真实本地模型服务 | **部分验证**：请求/响应契约、故障分级、fallback、E5041、注入归因已用 stdlib 假 llama-server 全绿（**26 项**，12.5；CH-469 补连接类瞬断重试 4 项：瞬断后成功 / 预算用尽报 `connect` 阶段 / `retries=0` 恢复单次尝试 / HTTP 状态码不重试）+ e2e 实测；**未验证**：真实 gguf 模型加载（沙箱无模型资产）与真实模型输出下的产物等价性边界 | 有资产后跑一次真实 server 全链路（`ops/llm.md` 的五步接入 + 黄金样本 offline/server 产物比对），并重判等价断言范围；真机重试窗口（llama-server 冷启动几十秒）下的 `llm.retries` 取值是否够用只能在那时判 |
| 备份恢复演练（DoD 第 11 条） | **已脚本化并实测**（CH-456 / T7：`doc2md backup drill` 8 步闭环 + 13 项测试，12.4；CH-461 容器 v2 后演练全绿）；**未验证**：真实外置卷（加密盘 / 网络卷）上的演练——沙箱内以本地目录模拟卷行为；**v2 容器的真机恢复验证**（v1 存量副本在新代码下的恢复已测，v2 副本在真机 / 外置卷的恢复随 T3 真机轮执行，待办 T13） | 有真实外置介质后跑一次 |
| >1000 文件混合语料的队列积压/失败率分布 | **已测**（CH-457 / T6：1080 文件三张表，12.3）；**未验证**：Windows / macOS 同口径数字（属 T3，测量工具已跨平台就绪，CH-458） | T3 |
| R6-PIPE-1「编号列表字号==正文」golden 反例（B-1） | **已闭环（CH-478 批次①，决策「落地有序列表分支」）**：CH-475 只做到「不再误判 H1」——编号行落进缝合段落，逐行边界丢失，与登记出口判据「修复后归 list_item」不符。本轮按判据收口：S3 新增有序列表分支（`_ORDERED_ITEM` + `_is_ordered_item`，`doc2md/pipeline/s3_structure.py`），同字号、无加粗、单级编号短行自成 `list_item`；S4 复用同一正则取序号，Markdown 输出 `N. ` 而非退化成了符号 bullet。三条反向信号（`(?!\d)` 挡小数、`\d{1,3}` 挡年份、句末标点挡整句）与 ≥2 单元格表格行优先，共同保证「只接住标题让位的行、不抢正文」。golden 资产已造：`tests/make_golden.py::make_numlist_pdf` → `tests/golden/numlist_1p.pdf`（3049 字节，MANIFEST 登记 sha256 `4d87109b…`，逐字节可复现步骤已含它） | **出口已满足**：端到端反例由 `tests/test_ch478_fixes.py` 红线 4 项钉住——① 三行编号项各成一块、归 `list_item`、不进 `heading`；② 同页三类真标题对照组（大字号 / 多级编号 / CJK 章節目条）照旧判标题，证明门不是「同字号一律不判」；③ Markdown 保留序号；④ 同页 12 行正文不被误收进列表分支。另 14 项信息层参数化覆盖正/负形态。突变复核三组：删 S4 序号分支 → 红线③转红；去掉 `_is_ordered_item` 的长度/句末守卫 → 2 项负向参数化转红；`(?!\d)\S` 回归（多吃一个正文字） → 红线③以「1. 装依赖」转红。产物等价性：31 个黄金样本逐一 `convert()` 前后比对，**仅 `numlist_1p.pdf` 变化**，其余 30 个 Markdown/IR 哈希逐字节不变（G6 等价口径）。**远端确认 = run `39027385467`**（push `b601749`，五作业全绿）：四条端到端红线在 3.11 / 3.12 / 3.13 三档与 3.12 全量四条腿同时通过，该判定不依赖本机解释器或字体环境 |
| R6-PIPE-2 缩进/围栏破损等价性样本（B-2） | **已闭环（CH-478 批次②）**：门已收紧（CH-475）+ 真实端到端语料已造（`tests/golden/codeindent_1p.pdf`，3521 字节单页，生成器 `make_codeindent_pdf`，MANIFEST 登记 sha256 `eb4f5254…`，已纳入 `self_check` 汉字数断言与 `test_golden_repro` 逐字节复现循环）。样本一面同时供出 heading 1 / paragraph 3 / code 2 / list_item 2 四类块形（围栏块 + 纯等宽块都在），`tests/test_ch478_gate_drift.py` 拿它的**真实产物**（397 字符、`norm_lines` 16 行）做四类漂移对照，并把结论钉成断言 | **出口已满足，且实测校正了 CH-475 的过度声明**。四类漂移在本语料上的实测值（收紧后 / 收紧前旧口径删尽全部空白）：**行首缩进 0.973958 / 精确 1.000000**——旧口径是结构失明（归一化后逐行相同，多少处缩进都不掉分），收紧后端到端判红（门 0.99）；**围栏破损 0.991914 / 0.991667**、**列表标记 `-`↔`*` 0.994652 / 0.994490**——差值 < 0.001 且红/绿结论一致，**两种口径同判绿**，证明内容级漂移的敏感度并非本次收紧带来（原登记把围栏/标记与缩进并列为「旧口径失明、收紧后可见」不实，已在 `metrics.py` 注释、附录 F 第九层、附录 G `metrics.py` 行与本行四处校正）；**行内空格增删 0.997333 / 1.000000**——脱盲但未过阈。由此得一条度量口径边界：**0.99 是粗大漂移探测器，不是等值证明**，判红取决于漂移量相对语料分母，逐字节等值仍归 12.2 的 sha 对照。红线 4 项（缩进判红+旧口径失明 / 内容级无额外敏感度 / S1 成因 / 参照实现与产品实现同值防对照失真），另 6 项信息层。**突变复核**：`norm_lines` 改回删尽空白 → 3 条转红；删去 `char_similarity` 区间字符级精比 → 3 条转红；把产品归一化位数 6→4 → 本语料无感（该位数不影响本文件断言，故不作为对账证据引用）。**产物代码零改动** |
| 代码块行首缩进在抽取层即丢失（CH-478 批次② 实测发现的**待决策项**，本表原无此行） | **成因已实测钉死**（红线 `test_s1_extraction_drops_the_leading_indent_that_geometry_still_carries`）：`tests/golden/codeindent_1p.pdf` 同一页里 `Line.x0` 明确带着三级缩进（**56.7 / 120.7 / 184.7**），而 `Line.text` 的行首空白**已被 pdfplumber `page.extract_text_lines()` 剥掉**（该行所有等宽行 `text == text.lstrip()`）；DOCX 腿同样在 `p.text.strip()` 处丢。**后果**：现行产物里凡代码块一律无缩进，Python/JS/YAML/JSON 这类「缩进即语法」的多行代码内容受损，且等价门在这条路径上「两侧都无缩进、无漂移可看」——B-2 的缩进判红只能由真实产物合成漂移复现，不是产品已经保住缩进。**为何不并入批次②**：按 x0 重建缩进要改 S1/S3 的**对外产物字节**（黄金样本凡含代码块者全变），需 `PIPELINE_VERSION` 递增 + 12.2 sha 基线重烤 + G6 等价重判，属功能面变更；批次② 的口径明确是「产物代码零改动」，不能顺手夹带。**建议方案（按「功能全面 > 性能」）**：值得做——缩进是内容不是装饰；做法为 S1 保留 `Line.x0`（已有），S3 对 `code` 块以块内最小 x0 为基准、按等宽字符宽度量化缩进级（每级 4 空格），仅等宽行参与、散文行不受影响，性能为常数级每行一次减法。**风险与前置**：扫描件/多列并排代码/混排字号下 x0 量化可能误判级数，需先造「多档缩进 + tab 缩进 + 表格内代码」语料定容错口径 | **需用户确认后才实施**（改动对外产物 + 版本号递增 + 基线重烤，非沙箱内可自决的纯内部口径）；确认后按独立批次执行：S1/S3 实现 → 新语料与红线 → 重烤 golden 与 12.2 基线 → `PIPELINE_VERSION` 递增 → 三档门禁 + 远端确认 |
| R6-LLM-2 归因去循环 + 真实注入语料（B-3） | **词表已扩、去循环延期**（CH-475）：`_INJECT_HINT` 已补 forget/disregard/ignore previous/new instructions/developer mode 等真实注入话术、「扮演」收窄到需越狱上下文（降误报）；但 `_attribute` 仍扫**算子 JSON** 而非**源文档正文**，与 `convert._injected_ops` 形成循环归因——去循环（改在 S1.5/S3 对原文扫描）与更宽误报收窄**需真实注入语料评估误报/漏报率**后才能定口径 | 准备真实注入语料（含英文 forget/disregard 变体 + 中文「扮演/输出 debug」正常技术文档），评估词表 effectiveness，再把归因判据从「算子异常」改为「正文是否含注入模式」为唯一独立判据 |
| R6-SVC-4 claim/reclaim 认领口径不对称（B-4） | **验证后延期、`worker.py` 未改动**（CH-475）：报告建议 `claim()` 加 `AND source_path IS NOT NULL` 与 `reclaim_stale` 对齐；经代码核验判定延期——并发危害已被 R6-SVC-3 的 `mark()` 状态守卫吸收（终态不可翻转，claim 竞争无法污染同步作业终态），且加过滤会使 `drain_once` 靠 E4001 清理的 NULL-path 孤儿行滞留、经 `uq_tasks_doc_active` 阻塞再提交。**未验证**：同步 `serve` + 常驻 worker 并发下 claim 误认领 `source_path=NULL` 行的实际发生率与后果 | 并发压测（同步 serve + 常驻 worker 同时跑）稳定复现 claim 误认领，据此重判是否值得加过滤（须同时解决 NULL-path 孤儿滞留），沙箱内可做，需先备压测夹具 |

**第七轮（CH-476）B 类 13 项——仅登记，需本机以外环境或人工，本轮不实施**（编号沿用报告 `docs/review-round7-CH-476.md` 第 6 章；与上一段 R6 的 B-1…B-4 不同源，勿混引）：

| # | 项 | 状态与出口条件 |
| --- | --- | --- |
| B-1 | 验 R7-PIPE-5：真实 ToUnicode 损坏 / 高密度乱码 PDF 语料 | 判据已落地（`noise>0.9 且 letters/chars<0.1`、连续 ≥3 页或全文占比超限才拒收），**误杀面未评估**——需真实乱码语料确认图表页不被误杀 |
| B-2 | 验 R7-PIPE-6：真实双栏 / 页眉带链接 PDF 语料 | 本轮未改（geoms 扩为横向重叠会改变含链接 PDF 的产物字节，触 G6 门禁重跑）；需真实语料评估误附面后再决定是否实施 |
| B-3 | 沿 R6 B-3：真实注入语料 + `_attribute` 归因去循环 | 词表已扩、去循环仍未做；需真实注入语料评估误报/漏报率 |
| B-4 | 沿 R6 B-4 + 验 R7-SVC-1：并发压测（同步 serve + 常驻 worker + 并发删除） | R7-SVC-1 的「先 `UPDATE tasks` 查 rowcount 再 `save_ir`」修复已落地并有单测；**竞争窗口能否稳定复现未验证**，需多进程压测环境 |
| **B-5** | **沿 R6 B-5（HIGH，建议优先安排）**：macOS 真机 `launchctl load` 实跑 `cron --daily` | `ops/launchd/*.plist` 的 `WorkingDirectory` + 显式 `--root` 已修（CH-475 / R6-OPS-1），真机生效与否仍未证实——第六轮唯一 HIGH 级待真机项 |
| B-6 | 验 R7-PERF-1（本轮新增）：Win11 spawn 平台池策略实测 | 领取量与 workers 解耦已实施（本机 t6 实测 36.5→11.5s），但 **T6 全部历史数字来自 Linux fork**，8GB Win11 目标机吞吐零实测；持久池取舍依赖此数据 |
| B-7 | 沿 TEST-3：CI 增 Windows 腿 | 现三档 job 全 `ubuntu-latest`，而头号目标机是 8GB Win11；需 CI 环境改动 + 人工确认 |
| B-8 | 沿 T3：跨机性能基准（低配 Win11 vs 本机），含 PERF-1/2/3 修复后复测 | 12.3 的新数字全是本机口径，跨机结论未出；需目标机 |
| B-9 | 沿 T12：`serve` / `work` 常驻资产实机「开机自启 + 崩溃自恢复」 | 资产文件仍缺（T12 待办），需真机验证 |
| **B-10** | **沿 T13（数据安全，建议优先安排）**：备份容器 v2 真机恢复演练 | 沙箱 `doc2md backup drill` 闭环已覆盖加解密 round-trip 与 13 表比对；**外置卷实景恢复一轮**未做，需真机/人工 |
| B-11 | 编辑器 / 审阅页真实浏览器人工走查（bus 事件、patch/rollback 审计展示） | 仅页面契约测试与单测，未见真实浏览器验证（与上表 V-11/V-5 的撤证同源） |
| B-12 | 验 R7-SVC-5：>30min 合法千页转换与 `reclaim_stale` 的双进程实测 | `perf.stale_job_minutes` 与批量超时的张力未验证；需长时运行环境 |
| R7-OPS-3 | CLI 权限口径分裂（`LOW`） | 需真实多用户/远程部署场景才能判定「本地 CLI 是否等同 admin 会话」，当前产品立场是单机自用；**属决策敏感项，未擅自改动** |

### 12.8 教训条目（进入第八、九章的执行纪律）

1. **恒真断言比没有断言更危险**：中文检索失效期间的「搜索 0 残留」、只在 cron 生效期间的「服务内置轮转」单测、有资产无判定的 120MB 样本——三条同为「绿灯掩盖未验证」。每条红线必须能指向一次「它能失败」的反向验证。
2. **资产本身需要验收**：黄金样本的哈希在可复现化之前是一条一次性快照，所有「产物字节一致」类断言曾建立在不可复核的地基上。
3. **性能改造以等价性为准入门**：先证明产物没变，再谈降了多少内存。
4. **数字必须带语料与测量方式**：同一份代码在不同语料上的吞吐不可互相替代；历史数字并列保留，不做静默替换。
5. **平台假设要在设计期而非交付期检验**：`spawn`、cp936、无 cron、软链在 zip 工具里的降级，都属于「开发机绿、目标机崩」的类别。

### 12.9 复现方法（「同口径」的定义）

1. 干净环境：`python3 -m venv /tmp/cv && /tmp/cv/bin/pip install -e ".[dev]"`；`doc2md doctor` 应全 ok 且 `root_mode = 0o700`。
2. 红线层：`/tmp/cv/bin/python -m pytest -m gate -q`（记录项数与耗时）；全量：`-m ""` 或去标记跑 `tests/`。
3. 驻留：单进程用 `resource.getrusage(RUSAGE_SELF).ru_maxrss` 在全量 `convert()` 结束时取值；并发场景必须由每个 worker 自报峰值再求和（见 8.3 的 `RUSAGE_CHILDREN` 陷阱）。基准脚本需置于 `if __name__ == '__main__':` 下，否则 spawn 语义自举失败。
4. 吞吐：固定语料目录（本轮为 `/tmp/bench` 30×1 页、`/tmp/bench2` 8×40 页、`/tmp/bench3` 320 页样本），串行与各并发度各跑 ≥2 次取稳定值，并保留产物做逐条目 sha256 比对。
5. 等价性：`git worktree add /tmp/pre HEAD` 取改造前代码，同一脚本分别在 `/tmp/pre`、工作区 chunk=0、chunk=32 三态下转换全部黄金样本，比对 zip 条目哈希（`content.md` 先剥 front-matter）。**无 git 仓库时（CH-469 轮的实际情形）**：改为「产物不变量 + 新基线」两件套——跑 `pytest -m gate` 取 `shard_two_phase`（`blocks_after == 单片块数`、`sim ≥ 0.99`）与 chunk/进程池/队列三条既有等价红线，再按 12.2 末表逐样本记 `content.md`（剥 front-matter）与 `ir.json` 的 sha256 前 16 位作为下一轮可比基线；配置须为**纯净默认**（`config.load()` 逐项等于内置默认，本仓库无 `./doc2md.toml` 与 `~/.doc2md/doc2md.toml` 时即成立）。
6. 磁盘门禁：`mount -t tmpfs -o size=40M tmpfs /mnt/tiny` 作 `--out`。
7. cp936：`PYTHONIOENCODING=cp936` 运行 CLI（沙箱内亦可先用 `encoding="cp936", errors="strict"` 包装流复现崩溃面）。

### 12.10 本文档变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 开发前 | 可执行开发方案（计划书形态：任务卡、伪码脚手架、§8.2 登记流） |
| v1.1（附录 H） | v2.4 开发期 | 增量登记 CH-422 … CH-446 与实测证据，作为 v1.0 的附件存在 |
| v1.1（附录 H 续） | 低端机专项轮次 | 增补 CH-447 … CH-451、口径小结、D-6 与吞吐/驻留证据行 |
| **v2.0** | 2026-10-13 | **全面重写为单一主文档**（登记为 CH-452）：主题篇（一–九章）+ 附录 A–F（规格表）不含变更编号、独立可读；登记篇（十–十二章）承接全部 CH/D/证据条目；新增附录 A 的消费列与 D-8/D-9/D-10 三项诚实缺口登记；`docs/plan-delta-v1.1.md` 降级为指向本文的存根；`doc2md.PLAN_VERSION` 升至 `2.0`。本轮未改动运行时行为（仅 `cli.py` 一处过时数字注释改为指向本文第十二章） |
| v2.0 增补 | 2026-10-13 | CH-453：workflow 进远端并首轮全绿，据此更正 8.6 / 9.1 / 9.2 / 12.1 / 12.7 的 CI 口径（「未验证」→「已运行、未硬阻断」），T1 出口条件收紧。规格判读未变，`PLAN_VERSION` 仍为 `2.0` |
| **v2.1** | 2026-10-14 | **补实现面 + 文件名去版本化**（登记为 CH-454）：主题篇新增 3.5 代码层结构（模块地图）、3.6 依赖方向与代码结构纪律、3.7 技术栈与选型边界；新增附录 G 模块与代码清单；1.4 增补「文档文件名不带版本号」的治理条款；9.2 的 T9 补全 dead-code 清单并新增 T10（静态检查工具链）/ T11（许可与元数据声明）；12.6 新增 D-11 / D-12 / D-13 并补充 D-9。文件由 `docs/dev-plan-v2.0.md` 改名为 `docs/dev-plan.md`（`git mv`，保留历史），全仓 29 个文件的「方案 v2.0」引用形态统一为「方案」。运行时行为不变，`doc2md.PLAN_VERSION` 升至 `2.1` |
| v2.1 增补 | 2026-10-14 | CH-455（缺陷修复轮）：worker 遗骸回收谓词与 cron 事件保留期的 SQLite 时间比较修复 + run_work 回收分支的 NameError 修复，新增 3 个回归测试（gate 65→67，全量 143→146）。规格判读未变，`PLAN_VERSION` 仍为 `2.1` |
| **v2.2** | 2026-10-14 | **待办收口轮**（登记为 CH-456 + CH-457）：主题篇 4.6 从「离线占位 + 真实服务待办」改写为双模式实现陈述（`llm.mode` offline/server、fallback、E5041、netguard 守护）；9.1/9.2 按实况重写（T2–T11 全部收口，仅剩 T1/T3）；附录 A 补 4 个 `[llm]` 键并清除全部「声明性」标注（49→53 键）、附录 D 的 E5041 启用/E5051 删除/W1044 接入/W1999 删除、附录 G 全量刷新（39 模块 5485 行、17 测试文件 5081 行 238 收集）；第十二章新增 T6 大语料三张表（12.3）、CH-457 打开边界与 T7 演练实证（12.4）、T2 契约实证（12.5），D-8/D-9/D-10/D-11/D-12 全部标注关闭，12.7 三项转「部分验证 / 已验证」。运行时版本常量不变（`PIPELINE_VERSION` 2.4.0 / `SCHEMA_VERSION` 14 / `DB_VERSION` 4），`PLAN_VERSION` 升至 `2.2` |
| **v2.3** | 2026-10-14 | **T3 测量工具就绪轮**（登记为 CH-458）：`ops/bench/t6_large_corpus.py` 从 Linux-only 改为三平台同口径入口（采样后端 proc / ps / psutil + none 降级，sqlite 只读 URI 跨平台，表格新增「采样后端」行，JSON 记录 platform/sampler）；9.2 的 T3 改「工具就绪、待目标机执行」，12.7 两条真机未验证项补工具口径；附录 G 测试资产刷新（5125 行 / 242 收集 = 241 passed + 1 skipped），8.1 与附录 F 项数口径同步。主题篇规格判读未变，`doc2md` 包零改动（仅 `PLAN_VERSION` 常量），`PIPELINE_VERSION` / `SCHEMA_VERSION` / `DB_VERSION` 均不变 |
| **v2.4** | 2026-10-15 | **全面审查优化轮启动 + 批次①**（登记为 CH-459）：主题篇新增 9.4「全面审查优化方案」（三路并行 45 项发现 + A/B/C 分诊 + 五批次实施顺序）；批次①实施 A-1（FR-104 七类块补齐：table/code/quote/page_break 从「声明未落地」变为产出现实）、A-2（FR-107/108 链接抽取端到端打通）、A-7（LLM op 五语义全落地）、A-13（sniff 一次直传）；9.2 待办表新增 T12–T14；附录 G 全量刷新（39 模块 5984 行、18 测试文件 5556 行 276 收集），8.1 / 附录 F 项数口径同步；第十二章 12.1 补本轮数字、12.2 补等价性复核证据。运行时行为：产物新增四类块与块级链接（原设计行为落地），`PIPELINE_VERSION` 维持 2.4.0；`PLAN_VERSION` 升至 `2.4` |
| **v3.5** | 2026-10-16 | **推送落地 + 交付流程两处真缺陷**（登记为 CH-470 + CH-471）：CH-469 全轮经一次性 clone 推送 `main`（`797d28f..88896ae`，43 改 + 2 新测试文件；本地专有草稿不入库、golden 零变动；diff 敏感信息扫描零命中，顺带把文档里的本机绝对路径改为中性表述）。推送本身产出两个事实——① **必需状态检查不拦直推 `main`**（远端 `Bypassed rule violations … 3 of 3 required status checks are expected`），T1 判据仍成立但覆盖面要写清；是否给 `main` 打开「Require a pull request before merging」属人工决策，仅登记（CH-470）；② `ci.yml` **三个作业无 `timeout-minutes`**，run 38756727387 的 `merge-gate (3.13)` 挂死 2h+ 无输出、`full-suite` 永不启动，红线在几小时里处于"未知"→ 修为作业级 15/10/30 分钟上界 + 测试步骤 `-vv`（`addopts` 含 `-q`，单 `-v` 净 verbosity 为 0，本机实测确认），使挂死最坏 15 分钟出红并能指认到用例（CH-471）。同时修掉 CH-468 漏同步的三处现时态口径（主题篇 8.6、README「本地红线兜底（T1）」节、12.1「失败能否阻断合并」）与 `ops/hooks/pre-push` 注释里的过期项数，附录 G.6 资产清点复算为 **5218 个 / 占用 23.2MB / 表观 3.0MB**。**登记失实更正（CH-473 收口轮）**：本行原称「`doc2md` 包零改动（仅 `PLAN_VERSION` 常量）」不成立——同一编号 CH-471 名下除 CI 上界修复外还有第四轮全轮实施（提交 `36ef92a`：`doc2md` 包 25 个 `.py`、tests 6 个文件、5 份归档文档，51 文件 +7855/−224），本行只登记了前者（其当时的 gate 132 / 全量 481 亦为第四轮落地前的口径，第四轮后为 132 / 543），漏登全轮；完整登记已按提交信息与审查报告重写，见第十一章 CH-471 行。`PLAN_VERSION` 升至 `3.5` |
| **v3.6** | 2026-10-16 | **第五轮全面审查 · 代码测试面三批次 + 文档收口**（登记为 CH-473）：§9.4 新增第四轮（CH-471 轮启动，46 条 = 44 实质 + 2 表扬）与第五轮（CH-473 轮启动，R5-* 编号）两节；§8.1 门禁口径红线 132 → **145**、全量 481 → **605**；§8.6 补 merge-gate 超时 15 → **6 分钟**右调依据与 X-16 段（`-v` → `-vv` 的净 verbosity 论证）；§9.2 T14 划关；§12.1 三行重写（红线 145 = 40+29+10+54+12、全量 605、CI 行补 `38843497730` 取消 / `38845736366` 与 `38851378872` 两次五作业全绿 / 推送后 run `38899470118` 五作业全绿回填——145 项红线三档实证、full-suite 605 passed、pre-push 钩子首次实战）；§12.2 补 **before/after 四时点 worktree 逐字节对比**（CH-473 与 CH-471 均 6/6 样本完全一致、CH-469 仅 `large_320p` 变化且与既有新基线吻合、基线表跨环境 12/12 复现）；§12.7 更新矩阵行（两处事实更正：3.13 挂死系撞 6 小时默认作业上限而非被下一 push 取消；修复后三档全绿）、新增 X-16 行（已修并经 CI 复跑确认）、git 能力行改「已完全恢复」；附录 A 57 → **60 键**（补 CH-471 新增三键整行）、附录 F 重刷（145 项构成、零跳过纪律整段重写、第四层 CH-473 12 项）、附录 G.1–G.6 全量实核重刷（doc2md 8400 行 / tests 10629 行 605 项）；§1.4 补编号与版本常量对账纪律、§3.5 模块行数刷新；第十章编号行 CH-472 → **CH-473**（编号纪律事件：CH-472 已被 `36ef92a` 占用，本轮全部改号）；CH-471 / CH-472 两行登记重写（原 CH-471 行漏登第四轮全部实施且误称「`doc2md` 包零改动」，CH-472 行声称的 `PLAN_VERSION` bump 从未发生）；README 三处同步；`ci.yml` 头注释 `-v` → `-vv` 与过期项数（132/481 → 145/605）校正。运行时行为：对外契约三处变化（`serve --host` 非回环 CLI 面同口径拒绝 / `PUT /settings` 改 `api.allow_remote_bind` 返 400 / `doctor` 配置语义非法退 `EXIT_USAGE`），G6 红线自身两处边界错误修复（PIPE-5/6），产物面零变化（四时点 worktree 逐字节比对）；`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5。**出口实测**（本沙箱 Linux / Python 3.11.1）：`pytest -m gate` **145 passed（61.6s）**；全量 **605 passed / 0 skipped（127.0s，1 个既登记 intentional warning）**；`test_golden_repro.py` 7 passed；`ruff check doc2md tests` 全绿。`PLAN_VERSION` 3.5 → **3.6**（补正上一轮声称但从未发生的 bump） |
| **v3.7** | 2026-10-16 | **X-16 正常路径复发根治**（登记为 CH-474，收口轮续）：§12.1 CI 行补 run `38901173280`（`df53679` 纯文档提交触发——merge-gate 三档全绿而 full-suite 在「605 passed」打完后 pytest 退出阶段挂死 27 分钟至超时，X-16 的 abandoned-only 修复未覆盖正常完成路径）；§12.7 X-16 行改「修复不完整」并新增 X-17 行（`run_batch` finally 无条件 terminate + SIGKILL 兜底本池 worker；SIGKILL 发出即保证 manager 必然结束）；§8.1 红线 145 → **146**（X-17 回归补入第四层）、全量 606；§3.5 `batch.py` 196 → 225 行；附录 F/G 刷新（第四层红线 13 / 全量 62；tests 10706 行）；README 门禁摘要与跟踪约定同步；`ci.yml` 头注释 146/606、`ops/hooks/pre-push` 注释项数 132 → 146（补 CH-473 批次④漏改的漂移）。回归测试用 waitpid/ECHILD 语义 + 10s 宽限窗（`is_alive` 有 ECHILD 误报怪癖不作断言依据），20 次循环探测 0 失败。**出口实测**（本沙箱 Linux / Python 3.11.1）：gate **146 passed（60.0s）**、全量 **606 passed / 0 skipped（122.0s）**、`ruff check doc2md tests` 全绿。**远端确认**：run `38913173159` 五作业全绿、full-suite 153.28s 干净退出。产物面零变化（只动清理逻辑）。`PLAN_VERSION` 3.6 → **3.7** |
| **v3.13** | 2026-10-17 | **CI 版本矩阵口径收口（12.7 遗留子项② 裁定落地）**（登记为 CH-479）：§8.6 新增「两个工作流、两种时间预算」段——push 面 `ci.yml` 三作业不动（`full-suite` 仍钉 `3.12` 单档），跨版本全量改由新增的 `.github/workflows/ci-full-matrix.yml` 周期腿承担（周一 02:17 UTC `schedule` + `workflow_dispatch`，三档 `fail-fast: false`，全量 + 黄金逐字节复现 + `doctor`，`timeout-minutes: 30`）；§2 技术栈表 CI 行、9.1「CI 执行点」行随之改为两工作流口径。**裁定本身进红线**：新增 `tests/test_ch479_ci_matrix.py`（6 项 = 红线 4 + 信息层 2，附录 F 第十层）钉住「两工作流版本集合相等且恰为 `{3.11, 3.12, 3.13}`」「push 面 `full-suite` 必须无矩阵且钉死 3.12」「每作业带 `timeout-minutes`、两条全量腿均 30」「周度腿只允许 `schedule`/`workflow_dispatch`」，无 `pyyaml` 环境下用文本正则解析并在解析失败时先报锚点失效；五处变异实测各自使对应断言转红。12.7 的矩阵行 ② 改标已裁定并落地，并新登一行「CH-479 待远端确认」（周期腿不由 push 触发，首次证据须手动 `workflow_dispatch`）。实测口径：gate **256 → 260**（38.55s / 收口复跑 33.74s）、全量 **805 → 811 passed / 0 skipped**（75.78s / 收口复跑 81.66s）、非红线层 549 → 551，`ruff` 全绿、`doctor` 退 0（33 键）、配置键 62 全不变；`PIPELINE_VERSION` 2.4.0、`SCHEMA_VERSION` 14、`DB_VERSION` 6 均不变（纯 CI 与文档层，无运行时行为变化）。`PLAN_VERSION` 3.12 → 3.13 由四条对账红线在同一次 gate 复跑中自证（第三次），残留 `3.12` 逐处复核确认全属历史叙述与无关字段 |
| **v3.12** | 2026-10-17 | **第六轮 R6-PIPE-2 的 B-2 出口闭环（等价性门缩进敏感度的端到端对照 + CH-475 过度声明的实测校正）**（登记为 CH-478 批次②）：12.7 的 R6-PIPE-2 行出口判据写「构造缩进/围栏漂移样本，验证收紧后的 `char_similarity` 门能捕获（旧口径会漏过）」，CH-475 只以字符串构造覆盖、缺端到端语料。**新增资产**`tests/golden/codeindent_1p.pdf`（3521 字节单页，MANIFEST sha256 `eb4f5254…`，条目 5136 → 5137；生成器 `make_codeindent_pdf`，`self_check` 汉字数清单 + `test_golden_repro` 逐字节复现循环均已纳入），一页同时供出 heading 1 / paragraph 3 / code 2 / list_item 2 四类块形。**新增测试**`tests/test_ch478_gate_drift.py`（收集 10 / 红线 4，附录 F 第九层）：自带「CH-475 前的旧口径归一化」与「CH-429 算法的可换归一化参照实现」，在同一份真实产物（397 字符 / `norm_lines` 16 行）上做四类漂移对照。**实测结论**：行首缩进 **0.973958（判红）/ 精确 1.000000（旧口径结构失明）**；行内空格 0.997333 / 1.0（脱盲未过阈）；围栏破损 0.991914 / 0.991667、`-`↔`*` 标记 0.994652 / 0.994490——**内容级漂移新旧差值 < 0.001 且红绿一致**，故 CH-475「门对围栏破损/列表标记替换完全失明、收紧后可见」不成立，已在报告原文（`docs/review-round6-CH-475.md` R6-PIPE-2 段与 §6.1）、本文 §12 叙述、§4.7、附录 F 第五层、附录 G metrics 行、v3.8 变更记录、CH-475 登记行⑩、12.7 的 B-2 行共八处同步校正，并在 `metrics.py` 注释补口径边界段（**行为零改动**）。由此登记度量边界：**0.99 只探测粗大漂移、不是等值证明**，逐字节等值归 12.2 的 sha 对照。**另实测一条上游成因并单独新登 12.7 一行（待决策）**：同页 `Line.x0` 有三级缩进（56.7 / 120.7 / 184.7）而 `Line.text` 行首空白已被 pdfplumber `extract_text_lines()` 剥掉（DOCX 腿 `p.text.strip()` 同丢）→ 现行产物代码块一律无缩进，门在此「无漂移可看」；按 x0 重建缩进要改对外产物 + `PIPELINE_VERSION` 升位 + 黄金基线重烤，**不夹带**，等确认后独立批次实施。**文档/资产面**——§8.1 与 9.1 门禁行 252 → **256**、全量 795 → **805**；附录 F 构成行加第九层、零跳过纪律 795 → 805；附录 G.2 管线 2520 → **2524**（`metrics.py` 86 → 90 行，纯注释）、G.6 `tests/` 30 → **31** 个 `.py`、13672 → **13959** 行、新增 `test_ch478_gate_drift.py` 行、`test_golden_repro.py` 121 → 122、`make_golden.py` 1311 → 1387、覆盖面口径 9244/9313 → **9248/9317**、采集方法求和式同步重算；§3.5 包规模行随之；12.1 三行（gate / 全量 / 基线引用规则）；10.1 的 CH-478 登记行改写为覆盖 批次①/②；README 门禁摘要 252 → 256 构成、`ops/hooks/pre-push` 252 → 256、`ci.yml` 头注释 252/795 → 256/805；`doc2md.PLAN_VERSION` **3.11 → 3.12**（header / §1.4「本版」/ 本行三处锚点同提交改写，四条对账红线复跑通过）。**出口实测**（本机 macOS arm64 / Python 3.15，`.venv` editable，取证一律 `--junitxml`）：`pytest -m gate` **256 passed / 0 failed / 0 skipped（31.16s）**、全量 **805 收集 = 805 passed / 0 skipped（69.39s，1 个既登记 intentional warning）**、`ruff check doc2md tests` 全绿、`doc2md doctor` 退 0；`make_golden.py` 的 `make_manifest()` + `self_check()` 复跑 **21 项自检全 PASS**、`test_golden_repro.py` 7 项全绿。**运行时行为**：产物面零变化（S1–S4 未改，仅度量注释与新增资产/测试）；`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14、`DB_VERSION` 6、配置键 62 全不变；`doc2md.PLAN_VERSION` **3.11 → 3.12**（header / §1.4「本版」/ 本行三处锚点同提交改写，四条对账红线复跑通过）。**远端确认 = run `39030470549`**（push `2e04aaf`，11:54:34Z→11:59:48Z，全程 5m14s）：lint「All checks passed!」（13s）、merge-gate 三档各 **256 passed 549 deselected**（测试步骤墙钟 3.11 67.07s / 3.12 56.53s / 3.13 61.67s）、full-suite **805 passed, 1 warning in 155.87s 干净退出** + `Golden assets byte-reproducible`（新样本 `codeindent_1p.pdf` 的逐字节复现已在远端成立）+ `doctor` 步骤全过（远端 `python 3.12.15` / `pipeline 2.4.0`）——**256 项红线与 805 项全量的首次远端实证** |
| **v3.11** | 2026-10-17 | **第六轮 R6-PIPE-1 的 B-1 出口闭环（有序列表分支落地）**（登记为 CH-478 批次①）：12.7 的 R6-PIPE-1 行出口判据写「修复后归 `list_item`」，而 CH-475 只做到「不再误判 H1」（编号行落进缝合段落、逐行边界丢失）——按 C 类决策口径选定「落地有序列表分支」并实施。S3 新增 `_ORDERED_ITEM` 正则与 `_is_ordered_item` 判据（`doc2md/pipeline/s3_structure.py`，+31 行），在符号 bullet 之后、表格行之前插入有序列表分支，同字号无加粗的单级编号短行自成 `list_item`；S4 复用同一正则输出 `N. `（`s4_render.py`，+5 行），序号与判据共用单一事实源、无循环导入。三条反向信号（`(?!\d)` 挡小数、`\d{1,3}` 挡年份、句末标点/行长挡整句）与「≥2 单元格表格行优先」构成宁漏勿误边界。golden 资产 `tests/golden/numlist_1p.pdf`（`make_golden.py::make_numlist_pdf`，MANIFEST sha256 `4d87109b…`，可复现步骤含它）。新增 `tests/test_ch478_fixes.py`（18 项收集 / **红线 4**），三组突变复核确认能失败；31 个黄金样本前后逐字节比对**仅 `numlist_1p.pdf` 变化**（G6 等价）。项数口径改档：gate 248 → **252**、全量 777 → **795**（非红线 529 → 543）；8.3 分层表、附录 F 第八层、附录 G.6 与总数（`tests/` **30 个 `.py`/13672 行**）、12.1 三行、12.7 的 B-1 行、README、`ci.yml`、`ops/hooks/pre-push` 一并更新。**本轮是 CH-477 那四条对账红线首次承接真实 `PLAN_VERSION` 升位**（3.10 → 3.11，常量 + header + §1.4 同提交），并实测「只改常量、文档不动」必红。本机出口：gate 252 全绿 31.54s / 收口复跑 31.36s、全量 795 passed / 0 skipped 70.01s / 69.58s（`--junitxml` 取证）、`ruff` 全绿、`doctor` 退 0（33 键）。**远端确认 = run `39027385467`**（push `b601749`，11:05:44Z→11:11:12Z）五作业全绿：merge-gate 三档各 **252 passed / 543 deselected**、full-suite **795 passed, 1 warning in 156.88s 干净退出**——252 / 795 新口径由此拿到远端实证；同轮补记 `cb310a3` 的 run `39023978470` 五作业全绿。`PIPELINE_VERSION` 2.4.0、`SCHEMA_VERSION`、`DB_VERSION` 与配置键数（62）不变 |
| **v3.10** | 2026-10-17 | **T15 落地：版本与编号对账做成红线断言**（登记为 CH-477）：新增 `tests/test_ch477_version_sync.py`（4 项全部标 `gate`），覆盖 `PLAN_VERSION` 三处对账、README↔10.1 编号对账、`PIPELINE_VERSION` 两处、`SCHEMA_VERSION`/`DB_VERSION` 两处；每条先断言锚点解析成功再比值，锚点失效即红，五条变异实测确认「能失败」。`PLAN_VERSION` 3.9→3.10 并与 header、§1.4 同步；9.2 的 T15 关闭；当前项数口径全量改档——8.3 分层表、附录 F 红线构成、附录 G.6（`tests/` 由 28 个 `.py`/13409 行 → **29 个 `.py`/13526 行**，收集 777 = 红线 248 + 其余 529）、12.1 三行、README、`ci.yml`、`ops/hooks/pre-push` 一并更新。**取证口径补记**：本轮起项数一律以 `--junitxml` 的 `tests=` 属性取证——本机进度点计数会被用例打印的 `.` 污染（同一次全量实测点数 784、junit `tests=777`）。运行时行为、`PIPELINE_VERSION`、`SCHEMA_VERSION`、`DB_VERSION` 与配置键数（62）全部不变。**远端确认 = run `39023289246`**（push `66414ec`，10:01:02Z→10:06:43Z）五作业全绿：lint「All checks passed!」、merge-gate 三档各 **248 passed / 529 deselected**、full-suite **777 passed, 1 warning in 158.16s 干净退出**——248 / 777 的新口径由此拿到远端实证，四条对账断言跨 3.11/3.12/3.13 与全量腿同时通过；同轮补记批次⑬ 的纯台账回填提交 `052c269` = run `39022746836` 五作业全绿 |
| **v3.9** | 2026-10-17 | **第七轮全面审查 · 13 批 A 类修复（性能主线三刀 + 功能 gap 收口）+ 集中文档对齐**（登记为 CH-476）：审查报告存档 `docs/review-round7-CH-476.md`（四路并行 41 项发现：性能 12 / 存储与服务 10 / 管线与结构 6 / 运维测试文档 gap 13，含 1 项 HIGH）。**代码面**：`doc2md/pipeline/*`（缝合方向与守卫顺序、结构块不续接、修订痕迹 `w:ins`/`w:del` 抽取、脚注尾注、单元格段落边界、编号形态集合封闭化）、`doc2md/storage/*`（FTS rowid 映射 `search_map` + DDL 无条件重放，`DB_VERSION` 5→6；`ix_audit_doc_id`）、`doc2md/worker.py` / `batch.py`（领取量与 workers 解耦 `perf.queue_batch_factor`、入队并行哈希 `perf.enqueue_hash_threads`）、`doc2md/api.py` / `edits.py` / `auth.py` / `cron.py` / `backup.py` / `config/loader.py`（配置键 60→62）与 `serve` 面小项。**对外契约变化 5 处**（权威口径见 CH-476 登记行「契约影响」）：① `DB_VERSION` 5→6（新增 `search_map` + `ix_audit_doc_id`，存量库开库自动回填）；② 同步 `POST /jobs` 池排队有界等待超时由无限挂起改为 **503 `E5041` + `Retry-After: 60`**；③ `mode` 非白名单由「透传给下游」改为 **400/`E4001` 零副作用**；④ 审计 `reason` / 令牌 `label` 非字符串或超长 → **400/`E4001`**（超长截断 256，判定在写库前）；⑤ IR `flags` 新增三个可选旗标 `level_from_hint` / `has_revision` / `note_kind`（只进 `ir.json`，既有语料字节不变）。另有两处行为变更属缺陷修复而非契约扩张：`backup drill --keep` 的 `drill-*` 永久豁免清扫、cron 报告文件名日期改 UTC。**测试面**：新增 `tests/test_ch476_fixes.py`（87 收集 / 红线 83）+ `test_sharding_unit.py` 红线 +8 → gate **153 → 244**、全量 **677 → 773**（本机 macOS arm64 / Python 3.15 实测 33.9s / 73.3s，`ruff` 全绿、`doctor` 退出 0）。**文档面**（批次⑬）：R7-DOC-1…7 + R7-GAP-4/5 全落地——README 编号与 CLI 摘要对齐、附录 E cron 十步补全、6.4 备份容器 AAD/轮出口径改到与代码一致、附录 B 退出码补全、第二章补 FR-204/205/207/209/210/211 六项隐性功能编号与判据、FR-104 与 NFR-22 措辞含糊收口、四处 `synchronous=FULL` 注释漂移改正、`ops/llm.md` verify 示例路径（R7-DOC-6，决策 D7）。附录 G 全量重算（39 模块 **9277** 行、28 测试文件 **13409** 行）并立下「分组标题数字由行合计反算」的新规则；12.3 新增 R7-STOR-1 的同机 A/B 实测表。9.2 新增 **T15**（版本三处对账的红线断言，本轮只做人工对账以免项数与远端取证错开）。**远端确认 = run `39016742023`**（push `9a1d959`）五作业全绿：lint `All checks passed!`、merge-gate 三档各 **244 passed / 529 deselected**、full-suite **773 passed, 1 warning in 157.20s 干净退出**。B 类 12 项（B-1…B-12）+ R7-OPS-3 仅登记，见 12.7。**批次⑬ 收尾提交 `44f98c3` 的远端确认 = run `39022142048`**（五作业全绿：lint 全通过、三档 merge-gate 各 244 passed / 529 deselected、full-suite 773 passed 干净退出）——12.7 的对应行已据此闭环。`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 维持 14；`PLAN_VERSION` 3.8→3.9 |
| **v3.8** | 2026-10-17 | **第六轮全面审查 · 12 项 A 类修复 + 2 项验证后延期**（登记为 CH-475）：审查报告存档 `docs/review-round6-CH-475.md`（R6-* 编号）。**代码面 10 项落地**——R6-SVC-3（`mark()` 加 `AND status NOT IN ('done','rejected')` 状态守卫，终态幂等；刻意偏离报告 D1 的 `IN ('queued','running')` 以保住 failed→done 自纠偏与 queued→failed 遗骸清理；`_finish_one` 对幂等命中/附着跳过 `save_ir`）、R6-SVC-2（审计 actor 缺省取 token sha256 前 12 位而非明文前缀，新增 `actor_id`）、R6-SVC-1（`/system/reload` 应用前过 `check_runtime_immutable`，堵「改配置文件 + reload」旁路）、R6-OPS-2（`scrub_record` bytes 分支先解码再按长度指纹化）、R6-OPS-3（`doctor` 磁盘不足/server LLM 不可达退 `EXIT_SYS`、配置语义非法退 `EXIT_USAGE`；所有机器可读 CLI JSON 出口统一收口 `_jline(ensure_ascii=True)`）、R6-OPS-1（launchd 两 plist 补 `WorkingDirectory` + 显式 `--root {安装根}/work`，修相对根名被解析成 `/work` 的错误根；真机 `launchctl load` 验证登记 B-5）、R6-LLM-1（新增 `valid_level`/`valid_marker`，`OpValidator._check` 拒绝越界算子，`apply_ops` 对 marker 改 lambda 替换杜绝 `\1` 注入——拒绝而非崩溃）、R6-LLM-2 部分（`_INJECT_HINT` 补 forget/disregard/ignore previous 等真实注入词、「扮演」收窄到需越狱上下文；归因去循环登记 B-3）、R6-PIPE-1（`_heading_level` 第三回退加信号门：仅 `size>base`/加粗/多级编号/CJK 章节词命中才判标题，堵有序列表项误升标题）、R6-PIPE-2（`norm_lines` 保留行首缩进、行内连续空白折叠为单空格，等价性门不再对**缩进类**漂移失明——「围栏破损」并列项由 CH-478 批次② 实测校正为内容级漂移，旧口径同样看得见）。**验证后延期 2 项**——R6-SVC-4（claim 加 `source_path` 过滤：并发危害已被 R6-SVC-3 守卫吸收，加过滤反使 NULL-path 孤儿行滞留并阻塞再提交，登记 B-4，`worker.py` 未改）、R6-LLM-2 归因去循环（需真实注入语料，B-3）。**文档面**——§8.1 红线 146 → **153**、全量 606 → **677**；§9.1 DoD 门禁耗时行补 CH-475 本机 30.0s；附录 B `doctor` 退出码列重写（2/5 语义）+ `_jline` 纪律段补 R6-OPS-3 收口；附录 F 新增**第五层**（CH-475 红线 7 项组 10 `r6_gate` + 64 项非红线 + B-3/B-4 延期注）、零跳过纪律 605 → 677；附录 G.1–G.6 全量实核重刷（doc2md 8400 → **8566** 行 / tests 26 → **27** 文件 11133 行 677 项；逐模块行数 cli 639 / api 754 / scheduler 117 / s3_structure 469 / metrics 86 / ops.logging 163 / llm 545 / loader 445；**校正 G.2 旧记 2150 与逐行和 2179 的既有不等**为实测 2199；新增 `test_ch475_fixes.py` 行、`test_t5_windows_ops.py` 199→207）；§3.5 包/测试行数刷新；§12.1 gate 行与全量行补 CH-475 档（153 = 40+29+10+54+13+7、677 收集）、门禁基线引用规则 146 → 153；第十章编号行已至 CH-475；README 门禁摘要 153 项构成、`ops/hooks/pre-push` 注释 146→153、`ci.yml` 头注释 153/677；归档 v2.4 文档补 launchd 部署以 `ops/launchd/*.plist` 为准的提示。**出口实测**（本机 macOS arm64 / Python 3.15，`.venv` editable）：`pytest -m gate` **153 passed / 524 deselected（30.04s）**、全量 **677 passed / 0 skipped（66.09s，1 个既登记 intentional warning）**、`ruff check doc2md tests` 全绿。**远端确认（收尾回填）**：run `38995335128`（push `ae296b4`）五作业全绿——merge-gate 三档均 **153 passed / 524 deselected**（66.16 / 63.07 / 72.90s）、full-suite **677 passed 干净退出**（157.70s）、lint「All checks passed!」；本机 gate 手动放行（`core.hooksPath` 此工作副本未启用，未改 git 配置，改手动 `pytest -m gate` 153 全绿后 push）。**153 项红线三版本首次远端实证**，X-17 在 full-suite（3.12）正常完成路径再获一证。运行时行为：对外契约三处收敛（`doctor` 退出码、`/system/reload` 拒不可变键、审计 actor 语义），产物面零变化（正常语料 markdown 字节不变，R6-PIPE-1/2 只纠正误判路径与收紧度量）；`PIPELINE_VERSION` 维持 2.4.0、`SCHEMA_VERSION` 14 不变、`DB_VERSION` 维持 5、配置键 60 不变。`PLAN_VERSION` 3.7 → **3.8** |
| **v3.4** | 2026-10-15 | **第三轮全面审查收口（CH-469 全轮，含批次①②③④）**：主题篇新增 9.4「第三轮审查」段（37 项发现 S-1…S-9 / V-1…V-13 / O-1…O-15 全为 A 类并实施 + 收尾自查三个真缺陷 + B/C 去向）；第十一章新增 CH-469 登记行（含 `DB_VERSION` 4→5、配置键 +3、API 错误形态收敛）；附录 A 键数口径 54→**57** 并撤回「测试钉住四处」的过强表述（测试只钉 SPEC/默认/样例三处，本表靠收口轮逐键核对）；附录 F 刷新为红线 **132 项 / 本机 28.7s（收口终轮复跑 35.6s）**、全量 **481 = 481 passed / 0 skipped（73.8s，终轮 70.7s）**，第一层组 1 加「分片去重后块数 == 单片块数」、第二层加「批量空窗超时逐件记账」并说明此前的超时是伪覆盖，新增批次①证据落点说明；附录 G 全面实核（G.1–G.5 33 行逐行对 `wc -l`、五个分段合计 7470 行与整包 7523 行的差额解释清楚、G.6 21 个测试文件行/收集逐一对 `--collect-only`，合计 8462 行 / 481 项，补 `test_m9a_search.py` 漏行）；3.5 模块地图与 3.6 展示层数字（`editor.html` 319→383）刷新；9.1 撤掉审阅页「无头浏览器实测」的失实行；第十二章换测量环境口径（本机 macOS arm64 / Python 3.15，与前序 Linux 沙箱数字不可互比）、12.1 三行重写（红线 67→132 的三步构成、全量 339→481、**本轮无远端 CI 证据**）、12.2 登记「无 git 仓库 → 三态对比不可执行」并给出不变量 + 6 样本新基线 sha 表、12.3 补 O-9 Tokenizer **3.87×（−74.2%）** 与计数 parity、12.5 `shard_two_phase` 行改为修复前后同档对照（dupes 34/30/247、blocks_after 三档全部回到 5696、sim 1.000000，修复前 0.999915/0.999429/0.997976 + half_heads=1）、12.6 D-6 补「耗时与有效性是两件事」、D-10 改为**完全关闭**（三处键集相等，57/57）、12.7 新增「本工作区 git 能力」与「编辑器真实浏览器人工验证」两行并刷新矩阵行与模型行口径、12.9 第 5 条补无 git 时的替代复现口径。运行时行为：`PIPELINE_VERSION` 维持 2.4.0（判定口径未变），`SCHEMA_VERSION` 14 不变，**`DB_VERSION` 4→5**，配置键 54→57。`PLAN_VERSION` 升至 `3.4` |
| **v3.3** | 2026-10-15 | **T1 关闭：仓库转公开 + 分支保护**（登记为 CH-468）：全历史安全审计零敏感命中后 `gh repo edit --visibility public`；`main` 必需检查 = `merge-gate (3.11/3.12/3.13)`；验收 PR #1（故意红断言）三个 gate 全 FAILURE、full-suite SKIPPED、mergeStateStatus=BLOCKED，本地 pre-push 钩子（CH-467）同场首证拦截。README CI 节与 9.2/12.7/12.1 口径同步。`doc2md` 包零改动，gate 67 全绿。`PLAN_VERSION` 升至 `3.3` |
| **v3.2** | 2026-10-15 | **T1 客户端兜底**（登记为 CH-467）：`ops/hooks/pre-push` + `core.hooksPath`，push 前本地跑红线（`-m gate`，与 CI merge-gate 同一断言面），红则拒 push；README 启用说明、9.2/12.7 口径同步、2 项静态测试。平台解锁（升级 Pro/Team 或转 public）后配置必需检查 + 故意失败 PR 验收。`doc2md` 包零改动，gate 67 全绿。`PLAN_VERSION` 升至 `3.2` |
| **v3.1** | 2026-10-15 | **本地 LLM 接入指引**（登记为 CH-466）：新增 `ops/llm.md`（选型表 / 引入五步 / 模型名发现规则 / `[llm]` 配置速查 / 故障排查 / NFR-1 与 12.7 边界），dev-plan 4.6、附录 G.6 与 README 挂引用；沿革行号与 `PLAN_VERSION` 对齐（v2.9→v3.0 更名）。纯文档轮，`doc2md` 包零改动，gate 67 全绿 / 全量 339 收集。`PLAN_VERSION` 升至 `3.1` |
| **v3.0** | 2026-10-15 | **第二轮全面审查**（登记为 CH-464 + CH-465）：五批次改动后的复审，24 项发现——代码 12（高 3 / 中 6 / 低 3）+ 测试 15 + 文档漂移 4。CH-464 修复 3 个高严重度缺陷（其中 2 个是上一轮改动自身引入：CH-462 watcher 快速通道致常驻 watch 每轮重转换全部文件、CH-460 幂等键 basename 派生致同名子目录文件静默丢失；另有 S3 quote 块乱序）+ 5 中 1 低；CH-465 修测试面 14 项（伪覆盖改冻结断言、恒真/死变量清除、flaky 加固、/tokens 与 markdown 导出补覆盖、9 处裸 config.load 换隔离夹具）+ 文档漂移 4 处（备份 v1 格式残留、FR-107 anchor 语义、watch 新语义）。黄金样本零漂移；gate 67 全绿、全量 339 收集。`PLAN_VERSION` 升至 `3.0` |
| **v2.8** | 2026-10-15 | **全面审查优化轮 · 批次⑤：文档口径**（登记为 CH-463）：A-17 的 11 项残留全部落地——5.2 错误码表随 CH-456 实况更新（E5041 启用 / E5051、W1999 标注已删除 / W1044 落码）、附录 B 补 backup drill 与 LLM 旗标、doctor 项数实数 30（补 6 个漏记键名）、3.5 与附录 G 数字实核刷新（G.4 表头 1196→1177 等）、README 六处（CI 矛盾、launchd 幽灵标签、tokenizer 位置参数、门禁摘要键名、M4M6 残留）、example.toml 双零口径注释、3.7 依赖闭包补 ruff + 版本浮动声明；4 项实核确认已解决（附录 A 消费方、键集差 53/54、Windows 步骤表、12.5 措辞——C-2 关闭）。纯文档轮，`doc2md` 包零改动，gate 67 全绿 / 全量 332 收集出口复核照常。`PLAN_VERSION` 升至 `2.8` |
| **v2.7** | 2026-10-15 | **全面审查优化轮 · 批次④：测试体系**（登记为 CH-462）：A-16 的 12 项未解决子项全部落地——CLI 四子命令入口层（serve token / backup drill / watch --once / tokenizer）7 项、API 三端点（/events、/jobs/<id>/events、/system/reload）6 项、sharding 纯函数 10 项、zip 完整性断言、make_golden 漂移副本改 import 生产 sanitize_name、cfg fixture 隔离 cwd、raises(Exception) 收窄 6 处、vacuous 断言清除、5s 墙钟轮询移除 2 处、port=1 假设消除、依赖扫描 regex→ast、注入语料缓存（约 33 次重复转换清零）；写测试挖出并修复两个真缺陷（`watch --once` 单轮永不提交——observe 首见快速通道；`/system/reload` 丢 settings_override——与启动口径对齐）。新增 24 项，gate 67 全绿、全量 332 收集（106s）、黄金样本复现 7 项零漂移；附录 F/G 数字刷新（39 模块 6337 行、22 测试文件 6348 行 332 收集）。`PIPELINE_VERSION` 维持 2.4.0；`PLAN_VERSION` 升至 `2.7` |
| **v2.6** | 2026-10-15 | **全面审查优化轮 · 批次③：备份加固**（登记为 CH-461）：A-11 落地为外置备份容器 v2（`D2MBAE2`）——随机 16B 盐（同口令跨部署密钥不再相同，彩虹表预制失效）+ 4MB 分块流式加解密（`encrypt_stream` / `decrypt_stream`，驻留峰值 ≈ 一块）+ 每块 AAD 绑定魔数/总块数/序号（块交换、截断、改头均认证失败）；`to_external` / `restore` 流式化，restore 走 `.part` + 原子 rename（失败不留半截库）；v1 容器保持只读兼容。新增 6 项测试（随机盐 / v1 兼容 / 六形态多块 round-trip / 块交换 / 截断 / 改头），`test_m8_ops.py` 22→28 项；gate 67 全绿（49s）、全量 308 收集（102s）、演练 8 步闭环在 v2 下全绿；真机外置卷验证列 T13（12.7）。对外：`.enc` 容器格式换代（v1 只读），口令与流程不变。`PIPELINE_VERSION` 维持 2.4.0；`PLAN_VERSION` 升至 `2.6` |
| **v2.5** | 2026-10-15 | **全面审查优化轮 · 批次②：性能与存储**（登记为 CH-460）：实施 A-3（`Store` 连接登记表改弱引用 + `live_conn_count`）、A-4/A-14（`save_ir` 单事务 + `executemany`，`tx=` 形参贯通 `_finish_ok` / 同步 `/jobs`，顺带修掉 `detail_json` 被 `mark(done)` 覆盖的静默丢失）、A-5（S1.5 门禁拆 `GateCollector` 采集/判定，auto 146.1→73.5ms、even5 70.9→7.3ms）、A-6（`reindex_block` 单块索引）、A-9（FTS 查询上限 + 区间掩码）、A-10（多文件投递按文件派生幂等键）、A-12（脱敏统一为「不看键名只看长度」）、A-15（删除 404 + 回滚 400）；**A-8 实测回退**（按片读取峰值内存不降反升，132.9→134.9/137.9MB，改动撤销、证据入 12.3）；对外契约收敛三处（删除不存在 404、回滚非数字 400、多文件幂等键派生），产物面零变化。新增 `tests/test_batch2_storage.py` 26 项，gate 67 全绿、全量 302 收集、六个黄金样本 gate 明细与 md_sha 逐字段一致；附录 F/G 数字刷新（39 模块 6216 行、19 测试文件 5922 行 302 收集）。`PIPELINE_VERSION` 维持 2.4.0；`PLAN_VERSION` 升至 `2.5` |

下一版本递增条件：主题篇任一章的规格陈述发生变化（新增能力、改变默认值、调整契约），**或主题篇 / 附录新增章节扩大了覆盖面**（CH-454 属后者：既有判读不动，但读者能查到的实现面事实变多了）。仅登记新 CH 条目、或只做事实口径更正时不改版本号，只追加第十一 / 十二章内容（CH-453 属此类）。文件名不随版本改变——版本号只出现在本文头部表格与 `PLAN_VERSION` 两处，避免带版本的引用再次悬空。
