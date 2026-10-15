# Doc2md 项目开发方案

| 项目 | 内容 |
| --- | --- |
| 项目名称 | **Doc2md** |
| 管线版本 | **v2.4.0**（`doc2md.PIPELINE_VERSION`） |
| 方案文档版本 | **v2.5**（本版；文件名固定为 `docs/dev-plan.md`，版本只写在这里，不写进路径） |
| 文档性质 | 开发主文档：主题篇为**当前有效规格**（as-built + 待办），登记篇记录阶段性变更与优化项 |
| 编制依据 | 仓库 `RunoobGo/doc2md` 实际代码与实测结果；管线 v2.4 Delta 记录；方案 v1.0 计划书 |
| 结构约定 | 主题篇（第一–九章）+ 附录 A–G（规格表）保持独立可读，**不含变更编号**；所有阶段性变更与优化项集中在登记篇（第十–十二章） |

## 阅读指南

- **想理解产品与规格**：读第一–九章。叙述顺序为「定位 → 需求 → 架构与代码结构与技术栈 → 管线 → 契约 → 安全运维 → 低配机运行 → 质量验收 → 实施计划」，每一章只依赖前一章已建立的概念。
- **想找代码在哪里、由谁调用**：读 3.5（模块地图）与 3.6（依赖方向与结构纪律），逐模块的行数、公开符号与引用方查附录 G。
- **想知道用了什么、没用什么**：读 3.7（技术栈与选型边界），依赖闭包与版本口径同见该节。
- **想查具体取值**：查附录 A（配置全表）、B（CLI 全表）、C（API 全表）、D（错误码与告警码）、E（数据库结构）、F（门禁断言清单）、G（模块与代码清单）。
- **想知道为什么某处是这样、改过什么**：查登记篇第十一章（CH-422 … CH-459 逐条登记）与第十二章（实测证据汇总、实现偏差、本文档变更记录）。
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
  | 状态库结构版本 | `DB_VERSION`（SQLite `user_version`） | 4 | `state.sqlite` 表结构变更时递增，迁移必须幂等 |
- **方案文档版本**独立编号（本版 v2.3），每次实质性修订升 minor；与管线版本解耦。文档自身的变更记录见第十二章。**文件名不带版本号**（恒为 `docs/dev-plan.md`），版本只写在本文头部表格与 `doc2md.PLAN_VERSION` 两处——CH-452 那轮把 40 余处旧锚点改指「方案 v2.0 的章节号」之后，版本号一旦升位这些引用就再次失效；去版本化是同一个失效形态的根治（CH-454）。
- **变更编号 `CH-xxx`** 按百位分段（4xx = v2.4 轮次与之后的开发期登记），只出现在登记篇。

## 二、需求规格

### 2.1 功能需求

| 编号 | 名称 | 优先级 | 规格要点 | 实现落点 |
| --- | --- | --- | --- | --- |
| FR-101 | 文本型 PDF 解析 | P0 | 页级行结构 + 字号/粗斜体/删除线样式；magic 判定不符即拒收 | `pipeline/s1_parse.py: read_pdf` |
| FR-102 | DOCX 解析 | P0 | 段落/样式/链接/修订痕迹；DOCX 无物理页，按每 40 行合成页序列 | `pipeline/s1_parse.py: read_docx` |
| FR-103 | 文本层体检门禁 | P0 | 采样判定是否为文本型；不达标整篇拒收并给页级明细 | `pipeline/s1_5_gate.py: check` |
| FR-104 | 结构分析 | P0 | 标题层级、列表、表格、代码、引用、分页符；编号体系参与层级判定 | `pipeline/s3_structure.py: analyze` |
| FR-105 | Markdown 渲染 | P0 | front-matter + 正文；渲染红线由断言守门 | `pipeline/s4_render.py` |
| FR-106 | 产物打包 | P0 | `<doc_id>.zip` 内三条目：`content.md` / `ir.json` / `report.html` | `pipeline/convert.py: package` |
| FR-107 | 链接渲染分级 | P2 | 外部链接（http/https/mailto/ftp）→ `[text](url)`；PDF 内部跳转与 DOCX 书签链接降级为纯文本 | `s4_render.py: _inline`（`s4.link_internal = plain \| anchor`） |
| FR-108 | marks 渲染分级 | P2 | 粗体/斜体默认开；删除线默认关（关闭时输出不得含 `~~`） | `s4_render.py`（`s4.preserve_marks_strike`） |
| FR-111 | watch folder | P2 | 大小 + mtime 连续稳定后才入队；与目录投递防护共用扫描实现 | `security/watcher.py: StabilityTracker` |
| FR-125 | 事件通道纪律 | P1 | 前端只消费全局事件总线；per-job 端点仅脚本型 User-Agent 可用 | `api.py: /events`、`/jobs/<id>/events` |
| FR-126 | 大文档两阶段分片 | P1 | 按页范围粗切（默认 300 页/片，2 页重叠）→ 各片独立结构分析与渲染 → 边界缝合 | `pipeline/sharding.py` |
| FR-127 | 文件名消毒与路径安全 | P1 | NFC 归一、剥离分隔符与控制字符、Windows 保留名重写、字节预算；zip 条目名二次断言 | `security/names.py` |
| FR-128 | 文档删除级联 | P1 | 删除产物 / IR / 事件 / revision / 审计，仅保留不含正文的删除留痕；admin + `confirm=true`；文件删除失败则整体失败 | `storage/repo.py: delete_doc` |
| FR-129 | 目录投递防护 | P2 | 不跟随符号链接、深度 ≤ 20、跳过隐藏项、单目录文件数上限、硬截止时间；超限返回明确错误码 | `security/dirguard.py: guard_walk` |
| FR-203 | 编辑与乐观并发 | P1 | PATCH 必填 `base_revision`；冲突返回 `425 / E4251` 并附 `current_revision`；审计 `before` 与 `from_revision` 严格对应 | `edits.py: patch_block` |
| FR-206 | 回滚 | P1 | 按目标 revision 取回内容并另起新 revision，不抹历史 | `edits.py: rollback` |
| FR-208 | 搜索索引刷新 | P1 | 编辑/回滚后同步重建该块索引 | `storage/fts.py` + `edits.py` |
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
| NFR-22 | 吞吐 | 多文件批量在近线性于核数（上限内）地缩短总时长；并行改造不得改变产物字节 | `pipeline/batch.py`、等价性断言 |
| NFR-23 | 目标机型可用 | Windows 中文控制台与 macOS 终端下均不得因编码问题崩溃；输出可被脚本稳定解析 | `cli.py: _console_safe / _jline` |
| NFR-24 | 可复现性 | 黄金样本资产两次生成 sha256 全表一致；分发包可复现 | `tests/make_golden.py`、`MANIFEST.json` |

### 2.3 验收口径的来源

需求表中的每条「可判定」列即为验收口径，落到三处之一：附录 F 的门禁断言清单、第十二章的实测证据表、或第九章待办的验证计划。凡既无断言也无实测的需求，不得在文档中标注为「成立」。

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

**标识符纪律**：`doc_id` 只由文件内容哈希得出（16 位前缀），任何文件名字段都不得参与；`source_key`（24 位前缀）代表同一来源，用于整源删除。二者都用流式哈希计算，不把整份文件读进内存。

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
- **原子领取**：`SELECT queued … LIMIT n` 与 `UPDATE … WHERE status='queued'` 必须在同一事务内，否则两个消费者会重做同一任务。

### 3.5 代码层结构（模块地图）

仓库只有一个 Python 包 `doc2md`（39 个 `.py`，约 5984 行），加一个测试包 `tests`（18 个 `.py`，约 5556 行）。顶层没有 `src/` 目录、没有子包多版本布局——这是刻意的：产品是单机自持的一体化管线，分包布局带来的版本协同成本没有对应收益（依赖打包口径见 3.7、附录 G）。

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
| `doc2md/static/` | 展示层 | `editor.html`：单文件审阅页（纯原生 JS，319 行，含队列进度面板） |

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
| CI | GitHub Actions（`.github/workflows/ci.yml`） | — | merge-gate 三版本矩阵 + full-suite 依赖层（8.6、12.1） |

**依赖闭包**（4 项直接依赖展开）：`Flask, blinker, cffi, charset-normalizer, click, cryptography, itsdangerous, jinja2, lxml, markupsafe, pdfminer.six, pdfplumber, pillow, pycparser, pypdfium2, python-docx, typing-extensions, werkzeug` = 18 个包；加 `[dev]`（`pytest` / `reportlab` / `pypdf`）为 25 个。全部为 MIT / BSD / Apache-2.0 / PSF / MIT-CMU 级许可；仓库已补齐 `LICENSE`（MIT）与 `pyproject` 的 `license` / `classifiers` 元数据声明（CH-456 / T11，第十二章 D-11 已关闭）。

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

**续行并回**（红线）：段落被标题行、空行或分页打断时，若前一块文本不以句末标点结束、当前行不是新的结构起始（编号、项目符号、列表标记等），必须并回前一块。并回发生在块 flush 阶段而非渲染阶段，以保证 IR 与 Markdown 的块边界一致。判定「块是否为半句开头」由度量层（4.7）提供，同一实现同时用于门禁与报告。

该规则的来源是实测：一段正文被中间一行短标题打断，会留下成批「以小写字母/逗号/右引号开头」的块，直接违反下游切块质量红线；单片与分片路径同现，因此修在 S3 而不是修在缝合。

### 4.5 S4 渲染

**职责**：IR → Markdown 文本 + 审阅页 HTML。渲染不做结构判断。

**分级规则**（FR-107 / FR-108）：

| 情形 | 默认输出 | 可配 |
| --- | --- | --- |
| 外部链接（http/https/mailto/ftp） | `[text](url)` | — |
| PDF 内部跳转 / DOCX 书签链接 | 纯文本（不产锚点） | `s4.link_internal = plain \| anchor` |
| 粗体 / 斜体 | `**` / `*` | — |
| 删除线 | 不输出 | `s4.preserve_marks_strike = true` 时输出 `~~` |

**渲染红线**由 `assert_render_contract` 机检：默认配置输出不得含 `~~`、不得含 `](#` 或 `#page-` 类自造锚点。

**front-matter**：`content.md` 头部为 JSON 形态的 meta（`doc_id`、`source_key`、`source_name`、格式、页数、分片标记、schema 版本、告警、门禁摘要、耗时等），**剔除 `source_name_raw`**（隐私边界）。其中耗时字段天生不可复现，因此等价性比对一律先剥离 front-matter 再比正文。

### 4.6 LLM 修补助手层

**立场纪律**：文档正文一律视为**不可信数据**，不是指令；防线只有两道——op 白名单与五道校验，模型「听话」不算防线。系统提示词第 7、8 条把该立场写成对模型的显式宣告，代码层同时以白名单强制执行（提示词不是安全边界，只是可观测性对齐）。

**五道校验**：op 名称在白名单内；目标块存在；目标区间未被前序 op 占用；`immutable` 块不得修改/删除；输出结构可解析。被拒绝的 op 不计错误，也不允许重试同类 op。

**归因**：拒绝路径必须可区分「模型胡话」与「注入得手」——命中注入触发特征的记录标 `injection_candidate`，否则标 `model_hallucination`。触发特征集需要覆盖中英文常见注入句式；覆盖不足会让全部拒绝被错标为胡话，使「注入是否得手」这一可观测性指标失真。

**双模式实现**（`llm.mode`，附录 A）：`offline`（默认）走确定性占位实现，只产出白名单内的层级修正 op，保证主链路可离线验收、零网络；`server` 连接本地 llama-server（仅 `127.0.0.1`，NFR-1），请求体为 IR 定位摘要 + 白名单，`temperature=0`，响应只接受白名单内的 op 数组（围栏与尾杂音容错解析，单次上限 64 个）。两种模式的输入校验、白名单与归因口径完全相同，切换不改变任何断言。服务不可用时：`llm.fallback=true`（默认）回退 offline 结果并打 `fallback`/`fallback_stage` 标记；`false` 则明确失败 **E5041 / HTTP 503**（detail 只含 stage/bind/port/hint，不含正文）。离线守护 `run.offline_assert` 把 socket 出站限制为回环，两个入口都不可绕过。

**op 白名单**：`split_block`、`merge_block`、`fix_heading_level`、`fix_list_marker`、`mark_table_boundary`（`llm.op_whitelist` 可配，但新增项需相应断言）。

### 4.7 度量口径

度量层同时服务门禁与报告，口径必须**在实现层固定**，不能靠注释描述。

- **`char_similarity`**：行锚定 + 区间内字符级精比。两个陷阱已被实测证实：① 通用序列比对库对高字符集（CJK）的「高频字符自动忽略」启发式会把大文档相似度假性压低；② 定长分块比对对任何插入都会整体错位。因此先按行对齐取锚，再在锚区间做字符级比对，并显式关闭启发式。
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

块类型集合、告警码、边界标志（`shard_edge` / `absorbed_ids`）在 IR 中固定；`flags` 为开放字典，新增标志需同步报告展示与断言。

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
| E5041 | 503 | 模型服务不可用（**预留**，本地模型服务接入后启用） | — |
| E5051 | 500 | 缝合失败（**预留**；当前缝合异常被降级口径吸收，见 4.3） | `reason` |

告警码（非 HTTP，写产物 meta 与报告）：`W1043` 跨片表格未合并（当前落码，并作为降级留痕）；`W1044` 分片转换说明、`W1999` 不安全降级兜底为**预留槽位**（当前无写入点，启用时须同步附录 D 与第九章待办）。

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

**机器可读输出**：`--json` 行必须是纯 ASCII（转义输出），使同一命令在 UTF-8 与 GBK 终端下字节一致，可被 PowerShell/CI 稳定消费；人读模式允许 `errors="replace"` 降级，但**绝不允许因编码抛异常**。

### 5.4 HTTP API 契约

端点全表见附录 C。要点：

- **`POST /jobs`**：`{path, mode?, idempotency_key?}`；`path` 可为文件或目录（目录走投递防护）。
  - 同步形态（默认）：逐文件转换，返回 202 + `jobs[]`（`doc_id`/`job_id`/`sharded`/`attached`/`idempotency_hit`），门禁拒收项以 `{status:"rejected", code}` 表达而不整体失败。
  - 异步形态：仅做判定与入队，返回 202 + `mode:"queued"` + `consume_with:"doc2md work"`；校验阶段**要么全入队要么零副作用**，禁止「已入队一半却整体报错」。
- **`GET /jobs`**：队列可观测（状态计数 + 队头明细，含源路径与模式），不含正文。
- **编辑族**：`PATCH /docs/{id}/blocks/{bid}`（必填 `base_revision`，可灰度为可选）、`POST …/rollback`（`to_revision`）、`GET /docs/{id}/audit`（审计含正文，故高于 reader 权限）。
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

表：`docs`、`blocks`、`edit_audit`、`deletions`、`tokens`、`tasks`、`events`，FTS5 虚拟表 `search_blocks`。关键索引：进行中作业对 `doc_id` 的**部分唯一索引**、`tasks(status, created_at)`（队列领取）、`docs(source_key)`、`edit_audit(doc_id, block_id)`、`tasks(idem_key)`。

迁移纪律：

1. 结构版本记在 `PRAGMA user_version`，与 IR 的 `SCHEMA_VERSION` 是两套口径，不得混用同名概念。
2. 每次迁移必须**幂等**（先探测实际结构再决定是否 `ALTER`），旧库直接升级到当前版本，不接受「先升中间版」。
3. 队列管道数据（源路径、模式）用**真列**而非塞进结果明细 JSON 字段：明细字段是对外契约面，混入管道数据会互相污染，也避免依赖可选的 JSON 扩展编译开关。
4. 连接按线程隔离（HTTP 服务是多线程的），事务用 `BEGIN IMMEDIATE`；自带 `BEGIN` 的子流程（如索引重建）不得嵌在外部事务内。

### 5.7 配置契约

全部键、默认值与语义见附录 A。结构性约定：

- 读取优先级：内置默认 → **单个**用户文件（`--config` 指定优先；否则 `./doc2md.toml`，缺失再取 `$DOC2MD_HOME/doc2md.toml`）深合并；两个候选位置不级联，同时存在时只有工作目录那份生效。服务进程另在 `create_app()` 时叠加 `settings_override.json`（`PUT /settings` 的落盘层），故其在内存中优先于 TOML；`POST /system/reload` 重读 TOML 并**丢弃覆写层**（这是该端点的既定语义，不是 bug）。
- **相对路径随根迁移**：`[paths]` 下的相对值一律相对 `[paths].root`（或其 `--root` 覆盖）解析，绝不因当前工作目录不同而写到别处。
- 取值路径为点号形式（`s1.chunk_pages`），读取处必须给默认值以兼容旧配置文件。

## 六、安全与运维规格

### 6.1 输入面防护

**文件名消毒**（唯一入口，所有投递路径共用）：NFC 归一化 → 取末段名（丢弃任何路径成分）→ 非白名单字符替换为 `_`（白名单：ASCII 字母数字、中文、`.` `_` `-` 空格）→ 去首尾空白与点 → **长度按 UTF-8 字节预算 128 判定并截断** → Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）前加 `_`。无扩展名时，保留名判定必须以全名为词干，否则保留名检查会被旁路（实测缺陷）。消毒结果仍不安全时抛专用错误并映射 **400/E4003**——不得使用裸异常，否则 HTTP 面落成 500，把「输入不安全」误报成「服务端故障」。消毒失败即拒收，**不设降级开关**。

**zip 双闸**：打包前对每个条目名再断言「无 `..`、无前导 `/` 或 `\`、无路径分隔符、无控制字符、长度不超预算、非保留名」。理由：消毒函数与打包路径是两个独立演进面，任何一侧回归都必须被另一侧抓住。

**目录投递防护**（API 目录投递与 watch folder 共用同一实现）：显式栈遍历、不跟随符号链接（对已访问真实路径去重）、深度上限 20、跳过隐藏项、单目录文件数上限 5000、**硬截止时间 10s**（超时截断并标记）。超限返回 413/E4133，而不是挂起或静默截断——「明确失败」优先于「看似成功」。

**写入稳定性判定**：文件大小 + mtime 连续 `watch.stable_seconds`（默认 5s）无变化才入队，期间只登记不计算。判定必须以「观察次数 + 时间戳」推进，可用注入时钟验收，不得依赖真实等待。

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
容器格式： b"D2MBAE1" + nonce(12B) + AESGCM(scrypt(n=2^14, r=8, p=1))
口令来源： 环境变量 DOC2MD_BACKUP_PASSPHRASE —— 不入库、不随备份走、不入任何日志
轮出：     按 mtime 删除超过 ops.backup_rotation_days（默认 14）的本地与外置副本
```

外置卷的三态语义必须区分清楚：**未配置外置卷 = 跳过**（记 `skipped`，不判失败，否则每个未用外置备份的部署每周都被记一次假故障）；**配置了但缺口令 = 失败且绝不落明文**；**成功才轮转**。macOS 备选介质为加密 sparsebundle，`doc2md.ops.backup.hdiutil_recipe()` 输出可直接使用的命令；两种介质只选其一写入运维文档。

恢复演练口径：解密落盘后必须跑 `PRAGMA integrity_check`，并任取一个历史快照比对内容——只做「容器能挂载」不算恢复验证。

### 6.5 日志与定时任务

**轮转**：`RotatingJsonlWriter` 由**写入进程自身**执行，阈值取「单文件字节数 ≥ `logs.rotate_mb` 或跨 UTC 日」，保留 `logs.rotate_keep` 份，轮转标记 `E_LOG_ROTATE` 落在新文件首行。两条硬口径：① 大小判定用**内存字节计数器**，不能用 `stat().st_size`——句柄缓冲会让磁盘大小滞后，实测 2KB 阈值被放大到约 8.2KB 才触发；② 每个活动文件只有一个属主，serve / work / cron 分别写 `app.jsonl` / `work.jsonl` / `cron.jsonl`。

**服务启动即清扫** `work/tmp/`，与 `cron --daily` 共用同一路径口径（两处都要做，且必须是同一个函数）。

**定时任务**：macOS 用 launchd（`StartCalendarInterval`，00:00 UTC 日任务 / 周日 00:30 周任务），Windows 用任务计划程序，Linux 用 systemd timer；serve 进程内不起常驻定时线程（保持分层，且崩溃隔离）。`--weekly` 定义为 `--daily` 的**超集**：先完整执行日流程（各步骤幂等），再追加外置加密增量。步骤序列：日报 → 磁盘门禁 → 本地快照 → （周）外置加密 → 事件保留期清理 → tmp 清扫 → 轮转检查。

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
| `doc2md doctor` | Python / 管线版本、根目录与其权限、tokenizer 口径、门禁采样口径、分片模式与片大小、op 白名单、乐观锁开关、轮转阈值、外置备份是否加密、低配机三键（分块页窗 / workers / queue_async）、CPU 核数、空间门禁阈值与**实际剩余空间**、**控制台编码**、每个运行时依赖的导入自检 |

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

同步 `POST /jobs` 会把请求线程占满整个转换期（大文档可达数十秒），客户端超时→重试→队列放大。因此提供异步形态：`perf.queue_async = true` 时提交只做类型判定与入队（实测 13 个作业条目 13ms 返回 202），消费由独立 `doc2md work` 进程按容量领取。

三条纪律：

1. **全入队或零副作用**——目录里混一个非法文件时，请求整体 415 但队列必须一条不加（实测缺陷，曾因边校验边入队留下半成品队列）。
2. **原子领取**——查询与置为 running 在同一事务内完成，多消费者不重复处理同一任务。
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
| L1 红线（merge-gate） | 六条产品级断言 + 开发期补强断言（当前 67 项） | 每次合并前必须全绿 | 阻断合并；按 8.5 降级表处置 |
| L2 全量回归 | 功能/契约/运维面全量（当前 276 项，1 项按环境跳过） | 每次发布前 | 阻断发布 |
| L3 资产自验 | 黄金样本两次生成逐文件 sha256 一致；清单可复核 | 每次样本变更后 | 资产不可信则相关断言作废 |
| L4 装机面 | 空环境安装 → `doctor` 全 ok → 起服务 → 全量测试 | 发布前 | 阻断发布 |

六条红线断言（产品级，不可撤）：

| 断言 | 判据 |
| --- | --- |
| `shard_two_phase` | >300 页样本：分片 vs 单片 `char_similarity ≥ 0.99`；无半句开头块；`shard_edge` 可筛选；**分片真实发生（非降级）** |
| `malicious_filenames` | 恶意文件名集合全部安全落库；zip 条目零越界；消毒失败 → 400/E4003 |
| `injection_resistance` | 注入样本 vs 干净样本指标差 ≤ 0.005；白名单外 op 应用数 = 0；拒绝路径有归因 |
| `edit_optimistic_lock` | 并发同块编辑：后保存者 425/E4251 且附 `current_revision`，正文未被覆盖，审计 `before` 与 revision 对应 |
| `delete_cascade` | 缺 `confirm` → 400；删除后 GET 404、搜索 0 残留、留痕无正文；文件删除失败则整体失败且库行保留 |
| `log_rotation` | 服务内置轮转、保留 N 份、**服务不重启**；跨进程日志互不重命名；日志无正文 |

低配机专项断言（当前归入红线层，共 25 项）：分块默认值、分块/不分子块与分块 1/2/3/32 的产物等价、流式哈希与全量哈希等价、workers 边界、进程池 vs 串行产物等价（含 spawn 参数化）、逐文件拒收上报、CLI 目录池化、异步入队→消费→文档可见、幂等与附着、领取原子性、无源路径任务判失败、work 日志隔离、磁盘门禁四态（阻断/系数/仅双零关闭/API 413）、类型判定与错误码归位、ASCII 机器输出、cp936 崩溃复现与修复、门禁退出码、`doctor` 暴露关键开关。

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

黄金样本（`tests/golden/`，由 `tests/make_golden.py` 生成）：小样本 PDF、链接样本 PDF、320 页大样本、DOCX 三件（简单/链接/修订痕迹）、注入样本与配对干净样本各 10 篇、恶意文件名清单、坏尾样本（前置/后置/扫描插页三种）、目录防护场景（深目录 / 隐藏项 / 大量文件 / 符号链接循环）、清单文件 `MANIFEST.json`。

**资产自身的可复现性是验收对象**：生成过程必须剥离时间戳（PDF 创建日期、DOCX zip mtime 与 core 日期），路径不得写死绝对目录，循环链接用相对目标——否则换机即失真，且「两次生成一致」断言不可能成立。120MB 大文件不入库（分发包体积），相应判定在测试内自造，避免留下无人消费的占位资产。逐项数量与文件形态清单（5136 个受版本控制文件、各目录的触发阈值关系）见附录 G.6——两处口径以附录 G 的采集方法复核，避免规格叙述与资产实际漂移。

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

**静态检查的定位**（CH-456 / T10）：`ruff check .`（默认规则集 F/E/I）作为独立信息作业与 merge-gate **并行**运行，互不 `needs`。三条纪律：其一，ruff 不设 `--select` 之外的严格度加码，保持「卫生级」而非「风格警察」；其二，它的红不影响合并判定——风格问题不得升级为红线；其三，它不得拖慢红线层（并行的意义正在于此）。mypy / black 仍未接入（3.7）。

**当前状态**：workflow 已进远端并在每次 push 到 `main` 时执行，截至 CH-454 累计四次 push 触发的 run 全部 completed / success（第十二章 12.1 有逐轮记录），红线失败会以红色检查呈现。**但「失败即阻断合并」尚未强制生效**：本仓库是 private 且为免费计划，分支保护与规则集两类 API 均返回 `403 Upgrade to GitHub Pro or make this repository public`，因此当前无法把 merge-gate 设为必需检查。在补齐该能力之前，合并前的红线判定仍以人工执行 `pytest -m gate` 留存结果为准——「红线没有强制执行点」等价于「没有红线」。

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
| 有界内存 / 进程池批量 / 异步入队 + 队列消费 / 磁盘门禁 / 控制台加固 | 已实现 | 低配机专项 25 项 + 等价三态复核 |
| 黄金样本逐字节可复现 | 已实现 | 两次生成全表 sha256 一致 |
| CI 执行点（merge-gate 三版本矩阵 + full-suite 依赖层） | 已在远端运行 | 远端首轮四个作业全绿（第十二章 12.1）；「不可合并」硬约束仍待 T1 |
| 本地模型服务客户端（双模式 + fallback + E5041） | 已实现（CH-456 / T2） | 假 llama-server 契约测试 22 项 + e2e 实测（12.5） |
| 备份恢复演练自动化（8 步闭环一条命令） | 已实现（CH-456 / T7） | `doc2md backup drill` 沙箱闭环 + 13 项测试 |
| 审阅页队列可视化（五态 + 失败原因） | 已实现（CH-456 / T4） | 无头浏览器实测 + 5 项页面契约测试 |
| Windows 任务计划程序资产（schtasks + 安装校验） | 已实现（CH-456 / T5） | 17 项与 launchd 等价测试 |
| 大语料吞吐 / 驻留 / 失败率三张表 | 已实现（CH-457 / T6） | 1080 文件混合语料，workers=1/4 两轮（12.3） |
| 门禁耗时 < 60s | 已达标（CH-456 / T8） | 67 项稳定 53.0s（12.1） |
| 声明性配置键收敛 + 死代码清零 + 预留码处置 | 已完成（CH-456 / T9） | D-8 / D-9 / D-10 全部关闭 |
| 静态检查工具链（ruff，CI 独立信息作业） | 已接入（CH-456 / T10） | `ruff check .` 全绿 + CI 并行作业 |
| 许可与元数据声明 | 已完成（CH-456 / T11） | `LICENSE`（MIT）+ `pyproject` `license`/`classifiers` |

### 9.2 待办（按优先级）

T2–T11 已在 CH-456 / CH-457 轮次全部收口（逐项判据与实测证据见 9.1 对应行、第十二章与第十一章两条登记）；下表只列尚未完成的项。

| # | 待办 | 判据 / 出口条件 | 风险 |
| --- | --- | --- | --- |
| T1 | 让红线**真正阻断合并**（不只是「跑并显示红」） | 仓库具备必需检查能力：分支保护或规则集 API 不再 403，且 `merge-gate (3.11/3.12/3.13)` 被列为必需检查；一次故意失败的 PR 被拦下 | 工作流本身已进远端并开始执行（12.1 首轮记录）。阻碍为平台能力：**CH-458 轮用 fine-grained PAT（`admin:true`）复测，分支保护与规则集两类 API 仍返回 `Upgrade to GitHub Pro or make this repository public`——是计划级限制而非令牌权限**（仓库 `private:true`、`plan:null`），可选路径为升级计划、改为 public，或接受「CI 显示 + 人工判绿」的半强制态 |
| T3 | 跨机吞吐基线：把内存/耗时表跑成 Windows 与 macOS 实测 | 至少各一组同口径数字进入第十二章。**测量工具已就绪（CH-458）**：`ops/bench/t6_large_corpus.py` 已跨平台化（proc / ps / psutil 三后端 + none 降级，Windows 路径经 `T6_SAMPLER` 覆盖实测、macOS 路径借 GNU procps 与 BSD ps 一致的输出规格在 Linux 上实测），目标机一条命令即可产出同口径三张表；剩余动作是在目标机上执行并回填数字 | 现有数字不可直接外推到其他平台；沙箱为 Ubuntu，无法产出目标机数字 |
| T12 | `serve` / `work` 常驻资产（launchd plist / schtasks XML，与 cron 档同构） | 资产文件进 `ops/launchd/` 与 `ops/windows/`，静态等价测试把住（URI/Label/时刻/动作 parity + 合法性），README 才可枚举；实机「开机自启 + 崩溃自恢复」行为待真机确认 | 审查（9.4 B-1）发现 README 已枚举不存在的标签；先修文档，资产列此待办 |
| T13 | 备份容器 v2（随机盐 + 分块流式，CH-461）的真机恢复验证 | 外置卷（移动硬盘 / 网络卷）实景恢复一轮通过；沙箱内 drill 闭环已覆盖加解密 round-trip 与篡改失败 | 容器格式变更的兼容面（v1 旧备份仍可解密）有单测把住，实景卷属真机项 |
| T14 | `PRAGMA synchronous=NORMAL` 取舍（WAL 下仅丢 OS 崩溃 / 断电前的最后若干事务，应用崩溃不丢；每任务写开销可再降一档） | 人工决策后实施或明确否决；若实施需在第十二章补「断电模拟」证据行 | 属数据耐用性取舍，按 9.4 C-1 不擅自改 |

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
| C-1 | `PRAGMA synchronous=NORMAL`（WAL 下仅丢 OS 崩溃前的最后事务，应用崩溃不丢）可把每任务固定开销再降一档，但属数据耐用性取舍，不擅自改 | T14 |
| C-2 | 12.5「双代证明」措辞降档为实际证据强度（server 模式 1 次 HTTP + 死端口失败路径 + 假服务契约测试，非双模型代际对比） | 本轮随 A-17 修正措辞，证据强度不再声称更强 |

**实施顺序与出口条件**：五个批次——① 功能全面性（A-1/A-2/A-7）；② 性能与存储（A-3…A-6、A-8…A-15）；③ 备份加固（A-11）；④ 测试体系（A-16）；⑤ 文档口径（A-17）。每批次出口：红线 gate 67 项全绿 + 全量套件 + `ruff check .` 全绿 + 等价性三态复核 0 不匹配（改产物路径的批次）+ 第十一章登记 CH-459 起逐批落条 + 第十二章证据行。B/C 类在每次完成任务后提醒。

**进度**：批次①已完成并登记为 CH-459（A-1/A-2/A-7 三项 + 收尾自查修掉三个真缺陷，gate 67 项全绿、全量 276 收集、等价性三态复核符合设计），A-13 随批次①提前落地。批次②已完成并登记为 CH-460（A-3/A-4/A-5/A-6/A-9/A-10/A-12/A-14/A-15 九项 + 顺带修 `detail_json` 被覆盖的静默丢失；gate 67 全绿、全量 302 收集、六个黄金样本 gate 明细与产物 sha256 逐字段一致）。**A-8 经实测回退**：按片读取在 large_320p 上峰值内存不降反升（132.9MB → 134.9/137.9MB，见 CH-460 与 12.3），改动已撤销、纪律改由 `s1.chunk_pages` 承担——这是「性能优先」原则的应有结果：实测无收益的改动不进主干。批次③–⑤按上表顺序推进。

---

# 附录（规格表）

附录是查询面，不叙述理由。凡与正文冲突处，以正文的规格陈述为准、附录的取值为事实来源；两者不一致即为缺陷，须登记。

## 附录 A 配置全表

来源：`doc2md/config/default_toml.py`（内置默认，53 键）与 `config/doc2md.example.toml`（分发示例，54 键 = 默认 53 键 + 故意多给的 `ops.backup_external_dir`；CH-456 起两处键集差清零，新增键须同时决定两边）。
解析顺序（**只叠加一个用户文件**）：内置默认 → 若给了 `--config PATH` 就用它；否则取 `./doc2md.toml`，不存在再取 `$DOC2MD_HOME/doc2md.toml`（`DOC2MD_HOME` 默认 `~/.doc2md`）→ 与该默认值深合并。两个候选位置**不是级联**：存在 `./doc2md.toml` 时 `$DOC2MD_HOME` 下的文件不会被读。服务侧再叠加 `work/settings_override.json`（`PUT /settings` 落盘，`create_app()` 时合并）。
相对路径一律经 `config.path_setting()` 解析：绝对值原样；相对值若首段等于实际 root 名或默认根名 `work`，剥掉该段后挂到 root 下（`paths.root` 自身不剥前缀）。

「消费」列标明该键是否被运行时代码读取。CH-456 / T9 之前，本附录曾用「声明性」标注「出现在默认配置中、但无代码消费」的键（第十二章偏差项 D-8 逐条列出 11 个）；该批键已全部落地消费，标注随之从本表清除——当前 53 键**全部有真实消费方**。纪律保留：新增键时「消费」列必须写下真实读取方，找不到消费方的键不得进默认配置；展示面-only 的键（如 `api.revoke_effective_seconds`）也要写明「仅展示、不驱动行为」，避免又被误读成可作用开关。

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
| `s4.link_internal` | `"plain"` | convert | 内部跳转链接渲染：`plain` / `anchor` |
| `perf.workers` | `0` | cli / batch | 批量进程数上限；`0` → `min(4, cpu_count())`，负数按 1 |
| `perf.queue_async` | `false` | api / cli | `POST /jobs` 只入队返回 202 |
| `perf.min_free_mb` | `512` | cli / diskguard | 写盘前剩余空间下限 |
| `perf.disk_safety_factor` | `3` | diskguard | 需求 = 待写字节 × 系数 |
| `perf.stale_job_minutes` | `30` | worker | 队列遗骸回收阈值（仅队列来源） |
| `watch.stable_seconds` | `5` | cli | 稳定性静默窗口 |
| `watch.follow_symlinks` | `false` | cli / api | 目录遍历是否跟随软链 |
| `watch.max_depth` | `20` | cli / api | 遍历深度上限 |
| `watch.skip_hidden` | `true` | cli / api | 跳过隐藏项 |
| `watch.max_files_per_dir` | `5000` | cli / api | 单目录条目上限（超出 → 413 / E4133） |
| `api.bind` | `"127.0.0.1"` | cli | 服务绑定地址（隐私边界，禁改公网绑定） |
| `api.port` | `8090` | cli | 服务端口 |
| `api.token_roles` | `["admin","editor","reader"]` | auth / api / cli | 可签发角色集：`auth.issue(allowed=…)` 之外的角色的签发即 `ValueError`（CH-456 / T9） |
| `api.revoke_effective_seconds` | `5` | cli（仅用于 `token --revoke` 与 `doctor` 的展示字段） | 对外承诺的吊销生效窗口；授权实时查库，故不驱动任何等待逻辑 |
| `api.base_revision_required` | `true` | api / cli | `false` = 灰度期允许无 `base_revision` 的 PATCH |
| `llm.mode` | `"offline"` | convert / cli / doctor | 补助实现：`offline` = 确定性占位（默认，主链路零网络）；`server` = 本地 llama-server（仅 127.0.0.1） |
| `llm.fallback` | `true` | convert | `server` 不可用时回退 offline 占位实现并打 `fallback` 标记；`false` = 明确失败（E5041 / 503） |
| `llm.timeout_ms` | `15000` | llm | 单次请求超时（连接 + 读取合计） |
| `llm.model` | `""` | llm | 请求里的模型名；空 = 取 `paths.vendor_models` 下发现的 gguf 文件名 |
| `llm.bind` / `llm.port` / `llm.auth` | `"127.0.0.1"` / `8080` / `false` | llm / convert / doctor | 本地模型服务地址与鉴权（CH-456 / T2 落地；`llm.auth=false` 属已知接受项，`true` 时口令走 `DOC2MD_LLM_TOKEN` 环境变量，不落配置不入日志） |
| `llm.op_whitelist` | 5 个 op | convert / cli | 补助手可应用 op 白名单（唯一权威来源） |
| `logs.rotate_mb` | `50` | api / cron / worker | 单文件轮转阈值（MB） |
| `logs.rotate_keep` | `5` | api / cron / worker | 轮转保留份数 |
| `ops.events_retention_days` | `30` | cron | `events` 表保留期 |
| `ops.backup_local_dir` | `"work/reports/backups"` | cron | 本地快照目录（权限 700） |
| `ops.backup_external_dir` | 未设（示例给出） | cron | 外置卷目录；未配置 → 周报记 `skipped` 且 `ok=True` |
| `ops.backup_external_encrypted` | `true` | cron / cli | 外置副本强制加密；置 `false` 时写外置即抛 `PermissionError` |
| `ops.backup_rotation_days` | `14` | cron | 副本轮出天数 |
| `ops.work_dir_mode` | `"700"` | ops/backup | `work/` 目录权限（CH-456 / T9：原为硬编码 `0o700`） |
| `security.max_name_bytes` | `128` | convert | 文件名消毒后的 UTF-8 字节预算 |
| `security.sanitize_fail_action` | `"reject"` | config/loader | 冻结键：唯一合法值 `reject`（配错即启动期 `ValueError`）；消毒失败一律拒收（E4003），无降级分支 |

## 附录 B CLI 全表

程序名 `doc2md`（字面小写）。全局子命令前置项：`--version`。所有子命令接受 `--config PATH`、`--root DIR`。

| 命令 | 参数 / 开关 | 行为 | 退出码 |
| --- | --- | --- | --- |
| `doc2md serve` | `--host` `--port` | 启动 API 服务（Flask threaded）；启动即写 `app.jsonl` 的 `SERVICE_START` 并清扫 `paths.tmp` | 0 正常；5 启动异常 |
| `doc2md serve token` | `--role admin\|editor\|reader`（默认 admin）、`--label`、`--revoke TOKEN`、`--list` | 令牌管理（API 等价物，服务未启动也可用）；签发只打印 token 裸串，吊销/列举打印 JSON（`revoked` + `effective_within_s`） | 0 |
| `doc2md cron` | `--daily` \| `--weekly`（必选其一，互斥）、`--no-backup` | 运维流水线（见附录 E 步骤表）；结果落 `reports/cron-<mode>-<YYYYMMDD>.json` | 0 全 ok；5 有失败步骤 |
| `doc2md convert` | `PATH`（文件或目录）、`--mode`、`--out DIR`、`--json`、`--follow-symlinks`、`--workers N` | 单文件/目录批量转换；目录走有界进程池，`--workers 1` 退回串行；目录无匹配文件时打印 `status:empty` 并按 0 退出 | 2 路径不存在；0；3 门禁拒收；4 业务错误；5 系统错误（多项取最大值聚合） |
| `doc2md work` | `--once`、`--max-jobs N`、`--workers N`、`--idle S`（默认 5.0）、`--enqueue PATH`、`--status` | 消费 `tasks` 队列；`--status` 仅打印计数；`--enqueue` 先入队（配合 `--once` 即入队后排空） | 0；4 入队拒收；5 有失败作业 |
| `doc2md watch` | `DIR`、`--once`、`--interval S` | 投递目录稳定性轮询（`size+mtime` 静默满窗口才处理）；`perf.queue_async = true` 时稳定即入队交 `work` 消费，否则就地转换；非 PDF/DOCX 扩展名静默跳过 | 0；3 门禁拒收；4 入队/转换业务错误 |
| `doc2md tokenizer export` | `GGUF`、`--vocab`、`--manifest`（清单默认取 `chunks.manifest`，词表默认取其同名 `vocab.jsonl`） | 从 GGUF 抽取内嵌词表与清单，打印 manifest/vocab/n_vocab/哈希前缀 | 0（异常经 `main()` 兜底为 5） |
| `doc2md tokenizer verify` | `--manifest`（必填）、`--gguf`（必填） | 清单与 GGUF 逐项比对（`gguf_ok` / `vocab_ok` / `name_ok` / `n_vocab_ok` 四项） | 0；5 任一项为假 |
| `doc2md doctor` | — | 环境自检 JSON（见下表字段） | 0；5 存在 `MISSING` |

`doctor` 输出字段（当前 24 项，键名即字段）：`python` `pipeline` `root` `root_exists` `root_mode` `tokenizer` `s1_5_sample_pages` `shard_mode` `max_pages_per_shard` `op_whitelist` `base_revision_required` `logs_rotate_mb` `backup_encrypted_external` `s1_chunk_pages` `perf_workers` `cpu_count` `perf_queue_async` `min_free_mb` `free_mb` `console_encoding`，以及 `dep_pdfplumber` `dep_docx` `dep_flask` `dep_cryptography`。

机器可读输出纪律：`--json` 与错误行走 `_jline()`（`ensure_ascii=True`），故在 cp936 与 UTF-8 终端下字节一致；人读模式在 `main()` 最前执行 `_console_safe()`，仅当 stdout/stderr 编码非 UTF-8 时 `reconfigure(errors="replace")`。

## 附录 C API 全表

前缀：无（根路径即 API）。鉴权：`X-Doc2md-Token` 头（或 `?token=`）。响应错误一律 `{code, message, detail}`，5xx 类同样走该契约。状态码为实际返回值。

| 方法 路径 | 所需角色 | 参数 | 成功码 | 主要错误 |
| --- | --- | --- | --- | --- |
| `POST /jobs` | editor | body `{path, mode?, idempotency_key?}`，头 `Idempotency-Key` | 202 `{jobs:[...]}` | 400 缺 path；404 路径不存在；413 空间不足（仅同步形态，经转换打包闸）/ 目录过宽；415 类型不符（异步形态整批拒绝，队列零增长）；422 门禁拒收（同步下逐文件记 `rejected`，不整体失败） |
| `GET /jobs` | reader | `?status=&limit=`（默认 50） | 200 `{tasks, counts}` | 401 / 403 |
| `GET /docs/<doc_id>` | reader | — | 200（含 `blocks[]`） | 404 |
| `GET /docs/<doc_id>/markdown` | reader(export) | — | 200 `text/markdown` | 404 产物缺失 |
| `PATCH /docs/<doc_id>/blocks/<block_id>` | editor | body `{text, base_revision?, as?}` | 200 `{revision,...}` | 400 缺 `base_revision`；404；425 `E4251` 版本冲突 |
| `POST /docs/<doc_id>/blocks/<block_id>/rollback` | editor | body `{to_revision, as?}` | 200 | 400 / 404 |
| `DELETE /docs/<doc_id>` | admin | query `confirm=true` | 200 `{...counts, cascade}` | 400 缺 confirm；403；500 `E5031` 产物清除失败（fail-closed，库行保留） |
| `DELETE /sources/<source_key>` | admin | query `confirm=true` | 200 | 同上 |
| `GET /docs/<doc_id>/audit` | editor | `?block_id=` | 200 `{audit:[]}`（含 before/after 正文，故不进 reader） | 403 / 404 |
| `GET /audit/deletions` | admin | — | 200 `{deletions:[]}`（只含计数与指纹，无正文） | 403 |
| `GET /search` | reader | `?q=&limit=` | 200 `{total, hits[], mode, match}` | — |
| `GET /events` | reader | `?since=SEQ` | 200（单次 ≤200 条） | — |
| `GET /jobs/<job_id>/events` | reader | — | 200 | 403 非脚本 UA（FR-125 硬规则） |
| `GET /settings` | reader | — | 200（当前合并后配置） | — |
| `PUT /settings` | admin | body 对象 | 200 `{updated:[keys]}`；覆写 `settings_override.json`（0600）。**该文件整体重写为本次提交的键集**（非累计），重启后只有最后一次 `PUT` 的键生效——长期部署请用 TOML 或 `POST /system/reload` 口径 | 400 非对象 |
| `POST /system/reload` | admin | — | 200 `{reloaded, ts}`（重读 TOML，丢弃覆写层） | — |
| `POST /tokens` | admin | body `{role, label}` | 200 `{token, role}` | — |
| `DELETE /tokens/<token>` | admin | — | 200 `{revoked:true}` | — |
| `GET /` | 无（静态） | — | 200 审阅页 `editor.html` | — |
| `GET /healthz` | 无 | — | 200 `{ok, docs, pipeline, ts}` | — |

`POST /jobs` 两条形态（由 `perf.queue_async` 决定）：同步 = 逐个 `convert()` 后立即返回 `doc_id`/`job_id`；异步 = 先对全部文件做类型嗅探、再全部入队，返回 202 `{jobs, mode:"queued", consume_with:"doc2md work"}`。异步路径的入队是**全或无**：任一文件类型不符即整体 415 且队列零增长。

## 附录 D 错误码与告警码

段位冻结：本期（及后续）只在既有段位内增补语义，不自造新码值。历史自造码已删除（见第十一章）。

| 码 | HTTP | 类 | 触发口径 |
| --- | --- | --- | --- |
| `E4001` | 400 | ApiError | 请求体/参数不合法；破坏性操作缺 `confirm=true`；PATCH 缺 `base_revision` |
| `E4003` | 400 | ApiError | 文件名消毒后仍不安全（`UnsafeNameError`，同时继承 `ValueError` 以兼容既有调用方） |
| `E4011` | 401 | ApiError | 缺 token / token 不存在或已吊销 |
| `E4031` | 403 | ApiError | 角色不足（`detail={role, required}`）；未知 action；per-job 事件端点非脚本 UA |
| `E4041` | 404 | ApiError | 文档、产物、源路径不存在 |
| `E4131` | 413 | ApiError | 剩余空间不足（`detail={need_mb, free_mb, rule:"NFR-6", what}`） |
| `E4133` | 413 | ApiError | 目录过宽（条目数或深度超 `watch.*` 上限） |
| `E4151` | 415 | ApiError | 非文本型 PDF / 非 DOCX（magic 与部件判定）；打开源文件不可读（含 pdfplumber / python-docx 打开失败）同归此码（CH-457 起含结构损坏文件） |
| `E4221` | 422 | ApiError | S1.5 体检门禁拒收（`detail={mode, sampled_pages, reasons, stats}`） |
| `E4251` | 425 | ApiError | 乐观锁冲突（`detail={current_revision}`） |
| `E5031` | 500 | Doc2mdError | 存储层失败：产物清除失败（fail-closed）、写盘 `OSError`；CLI 兜底 `OSError` 亦映射此码 |
| `E5041` | 503 | Doc2mdError | 本地模型服务不可用（CH-456 / T2 启用：`llm.mode=server` 且 `llm.fallback=false` 时抛；`detail` 只含 stage/bind/port/hint，不含正文） |

历史自造码已删除（第十一章）：`E4091`（CH-439）、`E5051`（CH-456：缝合失败由 4.3 降级口径吸收，抛码反而违背降级语义）。

告警码（写入 `ir.warnings` 与报告，不影响成功判定）：

| 码 | 语义 |
| --- | --- |
| `W1043` | 跨片表格不合并（缝合处任一侧为 table）；同时是降级单片时的留痕标记 |
| `W1044` | 分片转换说明：缝合降级为单片时写入 `ir.warnings`（CH-456 / T9 起真实写入） |

## 附录 E 数据库结构

`state.sqlite`，`PRAGMA user_version` = `DB_VERSION = 4`（与 IR 的 `SCHEMA_VERSION = 14` 是两套独立口径，不得混用）。连接策略：`journal_mode=WAL`、`busy_timeout=30000`、`foreign_keys=ON`，每线程独立连接（`threading.local`），写路径统一 `BEGIN IMMEDIATE`。

| 表 | 列 | 索引 / 约束 |
| --- | --- | --- |
| `docs` | `doc_id` PK, `source_key`, `source_name`, `source_name_raw`, `format`, `pages`, `sharded`, `shard_count`, `status`, `profile`, `created_at`, `updated_at`, `meta_json` | `ix_docs_source(source_key)` |
| `blocks` | `doc_id`, `block_id`（联合 PK）, `type`, `text`, `level`, `page`, `flags_json`, `marks_json`, `immutable`, `revision`, `updated_by`, `updated_at` | 主键 `(doc_id, block_id)` |
| `edit_audit` | `id` AI PK, `doc_id`, `block_id`, `before`, `after`, `from_revision`, `to_revision`, `actor`, `ts`, `reason` | `ix_audit_doc(doc_id, block_id)` |
| `deletions` | `id` AI PK, `doc_id`, `source_key`, `actor`, `ts`, `kind`(`doc`\|`source`), `cascade_json` | 只记数量与路径指纹，不记正文 |
| `tokens` | `token` PK, `role`, `label`, `created_at`, `revoked_at` | 授权每次实时查库 → 吊销即时生效 |
| `tasks` | `job_id` PK, `doc_id`, `source_key`, `status`, `idem_key`, `created_at`, `started_at`, `finished_at`, `error_code`, `detail_json`, `source_path`, `mode` | `ix_tasks_idem`；`ix_tasks_queued(status, created_at)`；`uq_tasks_doc_active` 部分唯一索引 `ON tasks(doc_id) WHERE status IN ('queued','running')` |
| `events` | `seq` AI PK, `ts`, `code`, `doc_id`, `job_id`, `payload_json` | 全局事件总线，前端唯一通道 |
| `search_blocks`（FTS5 虚表） | `doc_id UNINDEXED`, `block_id UNINDEXED`, `text`, `aux`；`tokenize='unicode61'` | `aux` = CJK 单字 + bigram + 非 CJK 原样段；虚表不能 `ADD COLUMN` → 缺列即 `DROP` 重建 + 回填 |

迁移纪律（四条，均幂等）：① `user_version < DB_VERSION` 时重放 DDL（全部 `IF NOT EXISTS`）；② `PRAGMA table_info` 判缺再 `ALTER`（`blocks.revision`、`tasks.source_path`/`mode`、`tokens.role` 存量默认 `admin`）；③ FTS 虚表按列检测重建回填；④ 队列管道数据用真列而非塞 `detail_json`（后者是结果/失败契约面，且 `json_extract` 依赖 JSON1 编译开关）。

`doc2md cron` 步骤序列（`report.steps[]` 的 `step` 名，按执行顺序）：

| 模式 | 步骤 | 失败语义 |
| --- | --- | --- |
| daily | `daily_report` → `local_snapshot` → （快照成功时）`rotation` → `tmp_clean` → `log_rotation` → `events_purge` | 任一步 `ok=False` → 整体 `ok=False`，退出码 5 |
| weekly | 同 daily，另在本地快照成功后执行 `external_backup`（位于 `rotation` 之前，故新副本当轮即受轮出保护） | 未配置 `ops.backup_external_dir` → `skipped` 且 `ok=True`；已配置但缺口令 → `ok=False`（宁可不备份不落明文） |

`--no-backup` 跳过 `local_snapshot` / `external_backup` / `rotation` 三步（排障用），其余照跑。轮出（`rotation`）对本地与外置两个目录按 mtime 清理超 `ops.backup_rotation_days`（默认 14）天的副本。

## 附录 F 门禁断言清单

红线层 = `pytest -m gate`，当前 **67 项**（第一层 40 + 第二层 27；墙钟耗时历史轮次 72–110s，T8 优化后 CH-456/457 轮起稳定 **53.0s**，<60s 达标——逐轮实测见第十二章 12.1）。这里的「项」是 pytest 收集数，含参数化展开（如恶意名单 12 例各算一项）。全量套件 **302 收集 = 301 passed + 1 skipped**（CH-460 轮口径；CH-459 轮为 276 收集，新增 26 项全部落在里程碑层，未进红线）。

第一层：六条契约红线（`tests/test_v24_gates.py`，收集 40 项）

| 组 | 断言要点 | 收集项 |
| --- | --- | --- |
| 1 `shard_two_phase` | 分片真实发生（非降级）+ `char_similarity ≥ 0.99` + 无半句开头块（1）；边界块在报告可筛选（1）；续接被吸收且留痕（1）；重叠去重后无重复块 id（1）；页数不重复计数（1）；缝合失败诚实标单片（1） | 6 |
| 2 `malicious_filenames` | 恶意名消毒落库（12 例参数化）；黄金清单全量复核（1）；zip 条目名合规（1）；恶意源名 zip 拒收 400/E4003（1）；符号链接循环 10s 内扫完（1）；目录过宽 413/E4133（1） | 17 |
| 3 `injection_resistance` | 干净 vs 注入最差 Δf1 绝对值 ≤ 0.005（1）；拒收归因含 `injection_candidate` + `model_hallucination`（1）；白名单外 op 应用数 = 0（1） | 3 |
| 4 `edit_optimistic_lock` | 双写后者 425/E4251 且正文未被覆盖、审计 `before` 与 `from_revision` 对应、缺 `base_revision` 400（1）；灰度期可选口径（1）；reader 不可编辑（1） | 3 |
| 5 `delete_cascade` | 缺 confirm 400/E4001 → 200 → GET 404、搜索零残留、留痕无正文（1）；级联产物文件清零（1） | 2 |
| 6 `log_rotation` | 写入器阈值/份数/首行标记（1）；服务内建轮转且不重启（1）；业务事件入日志且无正文（1）；启动清扫 `work/tmp`（1）；serve 与 cron 不共享活动文件（1）；服务遵从轮转配置（1）；轮转用内存字节计数（1）；日志零正文（1） | 8 |
| 附 | `watch_stability`（注入时钟：增长期零入队、静默满窗口恰好一次）随第 2 组同文件执行 | 1 |

第二层：低配机专项（`tests/test_perf_lowend.py`，收集 27 项）

分块默认值有界、分块产物等价（chunk 1/2/3/32 vs 整本）、流式哈希与全量哈希一致、workers 边界（0→`min(4,cpu)`、负→1）、进程池 vs 串行产物逐条目一致（含 `spawn` 参数化 = Windows 语义）、逐文件门禁拒收上报、CLI 目录确实走池、异步入队→消费→文档可见、幂等与附着、领取原子性、无源路径任务判失败、**遗骸回收命中真实时间戳格式（CH-455）**、**run_work 回收分支不再崩（CH-455）**、`work` 日志独立、磁盘门禁四态（阻断 / 系数 / 仅双零关闭 / API 413）、cron 记磁盘门禁失败、类型判定映射 415、API 魔术码不符 415、CLI 机器行纯 ASCII、`_console_safe` 替换不可编码字符、不支持类型退出码 4、目录含坏文件退出码聚合、`doctor` 暴露关键开关。

覆盖纪律：任何「资产存在但无测试消费」「断言恒真（如中文检索零命中下的 0 残留）」都算伪覆盖，必须在本清单里显式失效或补真判定用例（第十二章教训条目）。

---

## 附录 G 模块与代码清单

as-built 清单，逐模块给出行数、依赖方向（3.6 的 DAG 层次）、公开符号与被谁引用。「引用方」只列包内真实导入点（函数体内的延迟导入同样计入），不含 `__init__.py` 的 re-export。行数为 `wc -l` 口径，随提交浮动，不作为断言对象；采集方法见本节末。

### G.1 包根与入口 / 编排层（8 个模块，1711 行）

| 模块 | 行 | 层 | 职责 | 公开符号 | 引用方 |
| --- | --- | --- | --- | --- | --- |
| `doc2md/__init__.py` | 17 | 基座 | 三个版本常量的唯一来源 + 命名 / 版本治理说明 | `PIPELINE_VERSION` `SCHEMA_VERSION` `PLAN_VERSION` | `cli`（版本）、`ir/model`（schema） |
| `doc2md/cli.py` | 559 | 入口 | argparse 子命令、退出码聚合、控制台加固、`doctor` 自检（llm server 探测 + 冻结键校验） | `build_parser` `COMMANDS` `main` `DEPENDENCY_MODULES` + 8 个 `cmd_*` | 无（终端入口，`[project.scripts]` 指向 `cli:main`） |
| `doc2md/api.py` | 406 | 入口 | `create_app()` 工厂 + 20 个端点 + 鉴权挂钩 + 脱敏 | `create_app` `SCRIPT_UA_HINTS`（+ 内部 `_emit` `_deep`；脱敏已统一到 `ops.logging.scrub_record`，CH-460 / A-12） | `cli`（serve）、`worker`（仅 `_emit`，向上延迟引用） |
| `doc2md/worker.py` | 276 | 编排 | `tasks` 队列消费者：原子领取、遗骸回收、`work.jsonl` 属主 | `enqueue` `claim` `reclaim_stale` `drain_once` `run_work` `queue_stats` `WorkReport` | `api`（异步分支 `enqueue`）、`cli`（work） |
| `doc2md/scheduler.py` | 96 | 编排 | 作业投递 / 状态标记 / 事件写入；`mark(tx=)` 可并入调用方事务（CH-460 / A-4） | `Scheduler` `Job` `ACTIVE` `TERMINAL`（D-9 的零调用方法 `next_queued`/`active_for` 已在 CH-456 删除） | `api` `cli` `worker` |
| `doc2md/edits.py` | 85 | 编排 | 乐观锁校验 + 审计行 + **该块**索引刷新（`reindex_block`，CH-460 / A-6） | `patch_block` `rollback` | `api` |
| `doc2md/auth.py` | 72 | 编排 | 令牌签发 / 吊销 / 角色 rank 矩阵；`issue(allowed=…)` 按 `api.token_roles` 限制可签发角色 | `issue` `revoke` `check` `ROLES` `RANK` `MATRIX`（D-9 的零调用项 `require`/`timing_safe_equal`/`RevocationCache` 已在 CH-456 删除） | `api` `cli` |
| `doc2md/cron.py` | 200 | 编排 | 日 / 周步骤编排、`sweep_tmp`、周报落盘 | `run` `sweep_tmp` `CronReport` `Daily` | `api`（`sweep_tmp`）、`cli` |

### G.2 领域层：管线（8 个模块，1875 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `pipeline/convert.py` | 284 | `api` `cli` `pipeline.batch` | S1 → S1.5 → 分片 → S3 → S4 编排、`repair_ops`（LLM 双模式调度，CH-456/T2）、`Result`、`package()` 打包、`render_report()`；CH-459 起 `sniff` 一次直传 `read_any`（A-13） |
| `pipeline/batch.py` | 134 | `cli` `worker` | `run_batch()` 进程池、`default_workers()`、`Summary`；顶层可 pickle 的 `_convert_one`（spawn 约束，3.6 第 8 条） |
| `pipeline/s1_parse.py` | 411 | `convert` `s1_5_gate` `s3_structure` `api` `worker` | 魔术码判定 `sniff`、`read_pdf` / `read_docx` / `read_any`、`Line` / `RawPage`；`_open_source` 打开边界统一映射 E4151（CH-457）；等宽 / 表格行 / 链接 / 标题提示四组行级信号（CH-459） |
| `pipeline/s1_5_gate.py` | 255 | `convert` | 文本层体检 `check()`（内部走 `GateCollector`）、`GateCollector`（CH-460 / A-5：采集与判定分离，逐片喂页、纯函数判定）、`even5_indices` / `legacy3_indices`、`PageReport` / `GateResult` |
| `pipeline/sharding.py` | 199 | `convert` | `shard_plan` / `stitch_shards` / `overlap_dedupe` / `fingerprint` / `mark_shard_edges` |
| `pipeline/s3_structure.py` | 434 | `convert` | `analyze()`：七类块全量判定——标题 / 列表 / 段落 / 表格 / 代码 / 引用 / 分页（纯规则，无模型；CH-459 补齐后四类，`BLOCK_TYPES` 声明与产出一致） |
| `pipeline/s4_render.py` | 83 | `convert` | `render()`、`assert_render_contract()`、`is_external()` |
| `pipeline/metrics.py` | 75 | 仅 `tests/test_v24_gates.py`（门禁第 1 组，导入 `char_similarity` / `half_sentence_heads` 两个） | 公开函数只剩上述 2 个（`line_similarity` / `diff_lines` 已在 CH-456 删除，D-9 关闭），`norm_lines` 与 `is_half_head` 仅被同文件内部复用 |

`pipeline/__init__.py`（9 行）re-export 10 个符号。`pipeline/metrics.py` 只被测试引用（门禁第 1 组的两个判定函数），生产链路不导入它——「有代码、调用方在领域层之外」与「有代码、无调用方」是两件事，前者是验收工具（合理），后者是死代码（D-9，已在 CH-456 清零）。本节的「引用方」列就是用来区分两者的：它由 `ast` 遍历得出，不区分生产与测试，因此 `tests` 也算引用方，需要额外注明。

### G.3 领域层：存储（3 个模块，621 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `storage/db.py` | 275 | `api` `auth` `cli` `cron` `edits` `scheduler` `worker` `storage.repo` | `DDL`（8 表 + FTS5 虚表）、`Store` 连接与 `threading.local`、`DB_VERSION`、探测式迁移、`now_iso`；连接登记表为**弱引用 `WeakSet[_ConnRef]`** + `live_conn_count()`（CH-460 / A-3）、`_Tx.executemany`（A-14）（D-9 的零调用属性 `_Tx.lastrowid` 已在 CH-456 删除） |
| `storage/repo.py` | 231 | `api` `cli` `edits` `worker` | `save_ir`（`tx=` 可并入调用方事务，A-4；`executemany` 批量，A-14）`reindex` `reindex_block`（A-6）`get_doc` `list_blocks` `search` `delete_doc` `delete_source`（两者均先做存在性检查，A-15）`purge_files` |
| `storage/fts.py` | 115 | `db` `repo`，**仅标准库** | `cjk_aux`（n-gram 辅助列）、`build_match`、`query_terms`、`like_escape`、`MAX_QUERY_CHARS`（查询串上限）+ covered-span 区间掩码（CH-460 / A-9） |

### G.4 支撑层：安全与运维（9 个模块，1061 行）

| 模块 | 行 | 引用方 | 内容 |
| --- | --- | --- | --- |
| `security/names.py` | 114 | `pipeline.convert` | `sanitize_name` / `assert_name_safe` / `assert_entry_safe`、`WIN_RESERVED`、`SAFE`、`UnsafeNameError` |
| `security/dirguard.py` | 99 | `api` `cli` `security.watcher` | `guard_walk` / `scan_or_raise`、`WalkLimits` / `WalkResult`（符号链接环、目录过宽 413/E4133） |
| `security/netguard.py` | 53 | `cli` | `offline_guard`（`run.offline_assert` 的运行期守护：把 `socket.socket.connect` 限制为回环，非回环抛 `OfflineViolation`；CH-456 / T9） |
| `security/watcher.py` | 71 | `cli` | `StabilityTracker`（写入静默窗口，注入时钟可测） |
| `ops/logging.py` | 138 | `api` `cron` `worker` | `RotatingJsonlWriter`（按字节阈值 rename 轮转）、`EventBus`（`/events` 拉取式总线）、`scrub_record`（NFR-20 脱敏唯一实现，`api` 侧事件入库同用此函数，CH-460 / A-12） |
| `ops/backup.py` | 131 | `cron` | `derive_key`（Scrypt）/ `encrypt_bytes` / `decrypt_bytes`（AESGCM）、`BackupPlan`（`work_dir_mode` 可配，CH-456/T9）、`hdiutil_recipe` |
| `ops/drill.py` | 198 | `cli`（`backup drill`） | 备份恢复演练闭环：`run_drill`（沙箱 8 步：copy_live→snapshot→external_encrypt→simulate_loss→rotate→restore→integrity_check→compare）、`DrillReport` / `DrillFailure` / `integrity_check`（CH-456 / T7） |
| `ops/diskguard.py` | 54 | `cli` `cron` `pipeline.convert` | `free_mb` / `need_bytes` / `assert_free_space`（NFR-6，E4131） |
| `ops/gguf.py` | 203 | `cli`（tokenizer 子命令） | GGUF v3 只读解析：`Gguf` / `read_metadata` / `export_vocab` / `write_manifest` / `verify_manifest` / `Tokenizer` |

### G.5 基座层（5 个模块，896 行）

| 模块 | 行 | 包内引用 | 为什么能当基座 |
| --- | --- | --- | --- |
| `errors.py` | 98 | **无**（零导入） | 12 个错误码类（`Doc2mdError` / `ApiError` 两个基类之外）+ 2 个告警码（`W1043` `W1044`）+ `CODE_INDEX` 反查表；`ApiError` / `Doc2mdError` 为共同祖先。`E5051` 已在 CH-456 删除（降级语义吸收） |
| `llm.py` | 421 | **无**（零导入） | `scan_injection` / `offline_llm` / `apply_ops` / `OpValidator` / `DEFAULT_OP_WHITELIST` + `LlmClient` / `LlmServiceError`（本地 llama-server 客户端，CH-456 / T2：错误码映射由上层 `convert.repair_ops` 负责，基座不向上引用）；修补助手层不碰存储与配置。CH-459：`apply_ops` 五个 op 全部落地（原仅 `fix_heading_level` 有实现，其余过校验静默无效），`_split_block` / `_merge_block` / `_cjk` 为新增内部助手 |
| `config/loader.py` | 142 | 只引用 `config.default_toml` | `load`（解析顺序）/ `get`（点号取值）/ `path_setting` / `root_setting` + `run.profile` 三档键集覆盖（CH-456 / T9），只用 `tomllib` |
| `config/default_toml.py` | 98 | **无** | 53 个配置键的内置默认值唯一来源（附录 A）+ `TOML_BASENAME` |
| `ir/model.py` | 137 | `convert` `s3` `s4` `sharding` `storage.repo` `worker` | `Block` / `IR` / `BLOCK_TYPES`（7 类）/ `doc_id_of` / `hash_of_path` / `source_key_of`；只引用包根的 `SCHEMA_VERSION`，是数据结构而非逻辑层 |

### G.6 测试与资产（非运行时）

测试资产的**规格口径**（为何入库、可复现性为何是验收对象、120MB 大文件为何不内置）在 8.4；本节只登记文件与数字清单。

`tests/` 共 19 个 `.py`、5922 行，收集 **302 项**（301 passed + 1 skipped；`gate` 67 项 = `test_v24_gates.py` 40 + `test_perf_lowend.py` 27；其余 235 项为里程碑与信息面）。

| 文件 | 行 | 收集 | 定位 |
| --- | --- | --- | --- |
| `conftest.py` | 133 | — | 13 个夹具：黄金样本路径（`golden_dir` / `large_pdf` / `small_pdf`）、`cfg` / `tmp_cfg`、`store`、真实 Flask test client 的 `api_client`（红线层三条 API 门禁的执行面，配套的非夹具辅助函数 `hdr` 与夹具 `headers` 负责带角色取 token）、`saved_doc`、`names_manifest`、大样本三件套（`large_single` / `large_sharded` / `large_zip_names`） |
| `test_v24_gates.py` | 487 | 40 | 六条契约红线（附录 F 第一层） |
| `test_perf_lowend.py` | 485 | 27 | 低配机专项（附录 F 第二层） |
| `test_t9_convergence.py` | 250 | 21 | 配置收敛：profile 覆盖、冻结键、netguard 回环、token_roles、work_dir_mode（CH-456 / T9） |
| `test_m8_ops.py` | 479 | 22 | 备份加密 / 轮转 / 磁盘门禁 / launchd 编排 / 事件保留期边界（CH-455） |
| `test_t2_llm_server.py` | 346 | 22 | 假 llama-server 契约：请求体 / 围栏容错 / 注入归因 / 故障分级 / Bearer / offline 零 HTTP（CH-456 / T2） |
| `test_s3_blocks.py` | 431 | 34 | CH-459 批次①：七类块可达性与四类块保守检测口径、链接抽取（PDF annot 几何 / DOCX 超链接整段覆盖）、`apply_ops` 五 op 语义、黄金样本 e2e |
| `test_batch2_storage.py` | 366 | 26 | CH-460 批次②：连接登记表弱引用有界（A-3）、`save_ir(tx=)` 并入外部事务（A-4）、`GateCollector` 与 `check` 同口径（A-5）、`reindex_block` 单块刷新（A-6）、FTS 查询上限与掩码（A-9）、多文件投递按文件派生幂等键（A-10）、脱敏不看键名只看长度（A-12）、删除/回滚参数校验（A-15） |
| `test_m9a_search.py` | 219 | 12 | FTS5 检索与 CJK n-gram、鉴权 |
| `test_t7_drill.py` | 219 | 13 | 演练 8 步闭环、沙箱隔离、篡改必须失败、NFR-20 无正文（CH-456 / T7） |
| `test_m5_m6c.py` | 181 | 21 | S4 渲染契约、编辑与回滚、调度器 |
| `test_t5_windows_ops.py` | 199 | 17 | schtasks XML 合法性、launchd parity、口令零泄露、plist 回归（CH-456 / T5） |
| `test_m0_m2.py` | 158 | 13 | GGUF 词表工具链、S1 解析与 S1.5 门禁 |
| `test_t4_queue_ui.py` | 158 | 5 | 队列面板页面契约、JS 语法、NFR-20 无正文探针（CH-456 / T4） |
| `test_golden_repro.py` | 118 | 7 | 黄金样本自身可复现性与可移植性（CH-442，CI 独立步骤） |
| `test_t6_corpus_bench.py` | 317 | 18 | 打开边界 E4151 回归 + 基准仪器自检与三平台采样后端（CH-457 / T6，CH-458 补采样后端三支） |
| `test_m3_bigcopy.py` | 92 | 4 | 大目录遍历与稳定性追踪 |
| `make_golden.py` | 1195 | 不参与收集 | 黄金样本生成器（reportlab 造 PDF、pypdf 数页），资产不是断言 |
| `gguf_fixture.py` | 89 | 不参与收集 | 合成最小合法 GGUF v3 的夹具 |

`tests/golden/` 是入库的黄金语料（`git ls-files` 计 5136 个受版本控制文件、约 23MB）：文档样本（`clean/` 10 篇、`injected/` 10 篇注入稿、`docx/` 3 篇含链接与修订痕迹形态、`small_3p.pdf` / `links_pdf.pdf` / `large_320p.pdf` 637KB / `badtail_front|late|scan.pdf` 三种坏尾）、恶意名单 `names/malicious_names.json`（15 条 input→expected 对照，由门禁的 `test_malicious_names_manifest` 逐条复核；第 2 组那 12 例参数化用的是 `test_v24_gates.py` 内联的 `MALICIOUS_NAMES` 常量，与清单是两套来源——两处都要改才算覆盖全）、目录防护场景 `dirs/`（`many/` 5100 个空文件超过 `watch.max_files_per_dir = 5000` 用于触发 413、`deep/` 25 层超过 `max_depth = 20` 的越界侧、`symlink_loop/` 与 `hidden/`）、以及 sha256 绑定的 `MANIFEST.json`（CH-442 的可复现断言打在它身上）。`bigfile.bin`（120MB）被 `.gitignore` 排除，由 `make_golden.py` 现补（CH-443）。

非 Python 资产：`doc2md/static/editor.html`（319 行，原生 JS 单文件审阅页，含队列面板 CH-456/T4）、`config/doc2md.example.toml`（96 行，附录 A 的可抄样例）、`ops/backup.md`（71 行，备份介质与恢复演练细则）、`ops/launchd/*.plist`（19 + 21 行，日 / 周两个 label 示例；CH-456/T5 修复注释含连续连字符的非法 XML 缺陷）、`ops/windows/md.doc2md.cron.{daily,weekly}.xml`（62 + 63 行，schtasks 定义）、`ops/windows/install-doc2md-tasks.ps1`（220 行，安装 / 卸载 / 校验）、`ops/bench/t6_large_corpus.py`（363 行，大语料测量脚本，非运行时）、`ops/drill` 的说明见 `ops/backup.md`、`.github/workflows/ci.yml`（77 行，merge-gate 矩阵 + full-suite + ruff 信息作业）、`pyproject.toml`（58 行，含 `license`/`classifiers`，CH-456/T11）、`pytest.ini`（7 行，两个 marker + `--strict-markers`）、`.gitignore`（26 行）、`README.md`（160 行）、`docs/`（本文 + 已归档存根）、`LICENSE`（MIT）。

采集方法（本节数字可复算）：`find doc2md -name '*.py' -not -path '*__pycache__*' | xargs wc -l`；逐模块公开符号与引用方向由 `ast` 遍历 `Import` / `ImportFrom`（`level>0` 按 `__package__` 规则解析为绝对模块名，函数体内导入同样计入）得到；收集项数由 `python -m pytest --collect-only -q` 的分组输出读取。三者都不依赖运行时行为，改代码后重跑即可发现本清单漂移。五个分层小节的行数相加为 1667 + 1813 + 510 + 1047 + 896 = 5933，加六个 `__init__.py` 门面（51 行）等于包总量 5984 行——门面文件不单列一节，因为它们的内容就是各表里的公开符号清单。

---

# 登记篇

本章之后的内容**只属于登记面**：阶段性缺陷、更优方案、实测证据、实现偏差与本文档自身的版本记录。主题篇不引用这里的编号，读者可整篇跳过而不损失任何规格信息。

## 十、变更与登记机制

### 10.1 登记流

| 要素 | 规则 |
| --- | --- |
| 编号 | `CH-xxx` 单调递增，不复用、不回收。`CH-401…CH-421` 为 v2.4 需求/ADR 阶段占用；开发期从 `CH-422` 起续号，当前至 `CH-460`。下一条目自 `CH-461` 起 |
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

## 十一、逐条变更登记（CH-422 … CH-459）

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

**测量环境**：Linux 沙箱，4 vCPU / 8GB，Python 3.12， editable 安装于独立 venv；`resource.getrusage(...).ru_maxrss` 取峰值驻留（KB→MB 按 1024 折算）。凡标注「同口径」者按 12.9 的复现方法重跑。跨机（Windows 11 / macOS 真实硬件）数字尚未实测，见 12.7。

### 12.1 门禁与套件

| 项 | 数值 | 说明 |
| --- | --- | --- |
| `pytest -m gate` | **67 项全绿**；收口轮 110.4s、CH-453 轮 72.5s、CH-454 轮 108.8s、CH-455 轮 109.7s、**CH-456/457 轮 53.0s**（T8 优化后稳定值，<60s 达标）、CH-458 轮复测 67.7s（沙箱负载峰值，项数与结论不变）、CH-459 轮 54.2s、CH-460 轮 54s（项数与结论均不变） | 红线层；历史增长 35 → 40（CH-444 后）→ 65（CH-447…451 后）→ 67（CH-455 后）。CH-456/457 轮项数不变（新增 92 项全部落在里程碑与信息层，未进红线）、耗时降至 53.0s：大样本转换改 session 级共享夹具 + shard fallback 两类测试改中专句 PDF。四轮数字与全量套件同向波动，进一步支持「耗时不入断言」的口径 |
| 全量 `pytest` | 146 收集 → **242 收集（241 passed + 1 skipped）** → **276 收集（275 passed + 1 skipped）** → **302 收集（301 passed + 1 skipped）**；CH-460 轮 100s、CH-459 轮 109.1s、CH-458 轮 130.9s、CH-456/457 轮 107.98s、CH-455 轮 163.3s、CH-454 轮复核 162.4s、CH-453 轮复核 110.8s、低端机收口轮 164.9s | skipped 为可选大资产（CH-443）。CH-458 新增 4 项（T3 采样后端三支 + n/a 表格降级）。CH-459 新增 34 项（`test_s3_blocks.py`：七类块可达性 + 四类块口径 + 链接 + LLM op 语义）。CH-460 新增 26 项（`test_batch2_storage.py`：弱引用连接表 + 事务合并 + 门禁单遍 + 单块索引 + FTS 上限 + 幂等键派生 + 脱敏 + 删除/回滚校验）。同一份代码在多个时点给出 100 / 109.1 / 130.9 / 107.98 / 163.3 / 162.4 / 110.8 / 164.9s——沙箱负载是主导因素，项数才是硬口径 |
| CI 远端运行 | 首轮 run #1（push 到 `main`，commit `34e2853`）**四个作业全绿**：`merge-gate (3.11/3.12/3.13)` 各 65 项 `[100%]`，`full-suite` 143 项 `[100%]` + 可复现性 + `doctor` | GitHub Actions run #1，2026-10-13。测试步骤自身墙钟：红线层 72s（3.12）/116s（3.11）/122s（3.13），全量 183s（3.12）——runner 上同样比本地慢，项数才是硬口径 |
| 失败能否阻断合并 | **未生效** | 必需检查能力受平台限制（private + 免费计划 → 分支保护 / 规则集 API 403），见 T1 |

> 门禁基线引用规则：正文与 README 引用「项数」为硬口径（67 项），引用「耗时」须带轮次且不得单点引用——四组实测（gate 110.4 / 72.5 / 108.8 / 109.7s，全量 164.9 / 110.8 / 162.4 / 163.3s）跨度都超过 50%，任何「本轮值」都不能代表典型值，因此本表保留全部轮次而不做替换。
>
> CI 远端累计（截至 CH-458 轮收尾）：`34e2853`(run #1)、`ae12c03`(#2)、`aa40568`(#3)、`604ae62`(#4) 四次 push 全绿；`27fb1c1`(#5) 被 `cancel-in-progress` 取消（同一 ref 上 55 秒后又有 push，工作流头部那条 concurrency 规则按设计生效），`8031198`(#6) 重新跑满四作业全绿；`58f8a5e`(#7) 全绿；CH-455 轮的 `d8bf1e7`(#8，run id 38510360834) 四作业全绿（merge-gate 3.11/3.12/3.13 各 67 项 + full-suite 146 项含可复现性与 doctor）。**CH-458 轮的 `2123d52`（#9，run id 38569715467）五作业全绿**——`lint`（ruff）+ `merge-gate (3.11/3.12/3.13)` 各 67 项 + `full-suite`：作业数 4→5 即 T10（CH-456）ruff 信息作业接入的远端首证，full-suite 与可复现性仍只在 3.12 上跑（12.7 的诚实边界未突破）。取证口径同前：本轮 token 为 fine-grained PAT，`checks:read` 无法授权（GitHub 现行限制），annotations 读取 403，结论取自 Actions 作业状态 API。**当前 `main` HEAD 的检查状态 = success。**
>
> run #4 的作业级墙钟：`merge-gate` 135s（3.11）/ 152s（3.12）/ 139s（3.13）、`full-suite` 227s（全量 143 项 + 可复现性 + `doctor` 三步）。附带结论：**「同一 push 全绿」不等于「每个 commit 都有 CI 证据」**——取消是并发策略的副作用，被取消的 commit 没有红线证据，因此需要每个 commit 都留证的场合（例如二分定位）要临时关掉 cancel-in-progress。此点不影响 T1 的判据（T1 要的是「必需检查能拦住合并」，与取消无关）。

### 12.2 产物等价性（G6 的执行证据）

三态对比：改造前（`git worktree` 检出 HEAD）、改造后 `chunk_pages=0`、改造后 `chunk_pages=32`；6 个黄金样本各转一次，比对 zip 内每一条目 sha256（`content.md` 先剥 front-matter）。

| 结论 | 值 |
| --- | --- |
| 不匹配条目总数 | **TOTAL_MISMATCHES = 0** |
| 覆盖样本 | small / links_pdf / badtail / docx 系列 / 320 页大样本（6 个） |
| 补充断言 | 分块边界参数化（chunk 1/2/3/32 vs 整本）、进程池 vs 串行含 `spawn`、队列产物 vs 串行产物 |

**CH-459 轮（批次①，改产物路径）的等价性复核**：改造前对 6 个黄金样本的 markdown 做快照（sha256 前 16 位），改造后重跑逐字节比对——`small_3p.pdf`（`b1a1312c26d9dc9f`）与 `large_320p.pdf`（`6a90e3e368819ed5`）**逐字节一致**（新增的等宽 / 表格行 / 标题提示信号在既有样本上零触发，检测保守性达成）；`links_pdf.pdf` 按设计变化（`dfd7fa22`→`882a6a41`，两条链接落地为 `[整段](url)` 形态，无误伤其他行）；三个 DOCX 样本仅标题检出变化（Heading 样式行从段落升级为标题块，FR-104 修复的预期效果，源文件样式已逐一核实）。**结论：变化全部来自设计内的功能补齐，无一处非预期漂移。**

**CH-460 轮（批次②，不改产物路径）的等价性复核**：改造前 `git worktree` 检出 HEAD，两版各跑一次 6 黄金样本，比对 `Result.gate` 完整明细（JSON 序列化）与 markdown sha256——**逐字段一致**（`GateCollector` 重构不改变门禁任何判定输出；存储与幂等键改动不触产物）。方法学注记：首次比对曾出现 `avg_chars` 0.6 的假差异，根因是两次运行的工作目录不同导致 `config.load()` 读到不同配置（`sys.path` 隔离了代码、没隔离 cwd）——对比脚本必须用显式 `PYTHONPATH` + 同一 cwd 隔离「代码版本」与「配置环境」两个变量。

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

**CH-460 轮的两项微观实测**（large_320p.pdf，320 页）：

| 项 | 改造前 | 改造后 | 结论 |
| --- | --- | --- | --- |
| A-5 S1.5 门禁耗时（7 次取中位） | auto **146.1ms** / even5 **70.9ms** | auto **73.5ms** / even5 **7.3ms** | auto −49.7%、even5 −89.7%——`avg_chars` 不再对全文档重跑完整 `_page_metrics`，非采样页只付单正则代价 |
| A-8 按片读取峰值内存（tracemalloc `py_peak`，2/4 片） | 整读 **132.9MB** | 2 片 **134.9MB** / 4 片 **137.9MB** | **不降反升 → 回退**。驻留主体是 IR blocks（5890 块）与 pdfplumber 单页解析缓冲，`pages_all` 只占约十分之一；分片 IR 须全部驻留供缝合，省下的被抵消，还多出每片一次 PDF open。实测无收益的改动不进主干 |

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
| 备份恢复演练（CH-456 / T7） | `doc2md backup drill` 沙箱内 8 步闭环：exit 0 且 12 张表比对通过；线上库逐字节不变；`--keep` 留存取证；enc 容器魔数 `D2MBAE1`；缺口令 `E4001`；**篡改 enc 容器 restore 必须失败**（exit 4，`InvalidTag` 被 `DrillFailure` 收敛） |

### 12.5 质量红线关键值

| 断言 | 实测 |
| --- | --- |
| `shard_two_phase` | mp=300：shards=2 / dupes=34 / `char_similarity` = **1.000000** / half_heads=0；mp=160：stitched=1 / 0.999573；mp=40：0.999033 |
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
| D-6 | 慢测处置 | 门禁按 `-m gate` 标记分层（67 项；收口轮 110.4s、复核轮 72.5s），大样本 fixture session 作用域共享；蓝本的 difflib 直译口径在本机不可完成（>300s），按 CH-428/429 替换后方可作 CI 红线 |
| D-7 | 缺 confirm 的状态码 | 未新增码值（段位冻结），回到 400/E4001 + `detail.requires="confirm=true"`；自造 `E4091` 类已删除（CH-439） |
| D-8 | **配置面声明性键**（CH-456 / T9 **已关闭**） | 默认 TOML 49 键中 **11 键当前无代码消费**：`paths.vendor_models`、`run.profile`、`run.offline_assert`、`s1_5.reject_on_any_bad_page`、`s1_5.page_detail_in_report`、`api.token_roles`、`llm.bind`、`llm.port`、`llm.auth`、`ops.work_dir_mode`、`security.sanitize_fail_action`。附录 A 已逐键标注。处置方向：要么实现（`run.profile` 三档映射、`llm.*` 随 T2 落地），要么从默认配置删除——不得长期停留在「看起来可配」状态（待办 T9）。**关闭口径（CH-456）**：11 键全部落地消费（`llm.*` 随 T2，其余随 T9；逐键消费方见附录 A），默认配置 49→53 键（净增 4 个 `[llm]` 键：`mode`/`fallback`/`timeout_ms`/`model`） |
| D-9 | **契约面空位**（CH-456 / T9 + T2 **已关闭**） | `E5041`（模型不可用）、`E5051`（缝合失败）在代码中定义但无抛出点；`W1044`、`W1999` 未被写入；`Scheduler.next_queued()` 无调用方（消费路径由 `worker.claim()` 承担）。前三项属预留段位，需在启用时同步主题篇与附录 D；`next_queued` 属死代码，应在下个变更里删除或接入（待办 T9）。3.6 / 附录 G 建表时用同一口径（`ast` 找定义 + 全包与 `tests` 找调用点）复查，又发现 5 处零调用符号：`auth.timing_safe_equal()`（令牌比对实际走参数化 SQL，未经过它）、`auth.RevocationCache`（实时查库口径下没有缓存接入点）、`auth.require()`（`api.py` 用的是 `auth.check()`，`require` 是同一矩阵的另一入口但从未被调用）、`Scheduler.active_for()`（附着判定内联在 `submit()` 的 SELECT 里）、`db._Tx.lastrowid`（属性暴露后无人读取）；`pipeline/metrics.py` 的 `line_similarity()` / `diff_lines()` 同样零调用（`is_half_head()` 有同文件内部复用）。即新发现 7 处，连同 `next_queued` 合计 8 处零调用符号一并处置（T9）——注意其中 `lastrowid` 与 `active_for` 是「对外 API 形状的一部分、暂时无人用」，删除前要确认没有分发面的隐式契约。**关闭口径（CH-456）**：8 处死代码全部删除（删除前逐一确认无分发面隐式契约）；`E5041` 由 T2 启用（附录 D 已同步）；`E5051` 删除（缝合失败由 4.3 降级口径吸收，抛码违背降级语义）；`W1044` 接入降级留痕路径（开始真实写入）；`W1999` 删除 |
| D-10 | 分发示例与默认值的键集差异（CH-456 **已关闭**） | 默认 TOML 49 键、示例 42 键。示例**多** 1 项 `ops.backup_external_dir`（家用机可选路径，故意给样例）；示例**少** 8 项：`run.profile`、`run.offline_assert`、`llm.bind`、`llm.port`、`llm.auth`、`llm.op_whitelist`、`security.max_name_bytes`、`security.sanitize_fail_action`（其中 4 项本就属 D-8 的声明性键）。新增键时两处需同时决定，避免「示例里没有 = 不支持」的误读。**关闭口径（CH-456）**：两处同步补全——默认 53 键、示例 54 键 = 53 + 故意多给的 `ops.backup_external_dir`，键集差只剩这一项刻意差异 |
| D-11 | 许可声明缺失（CH-456 / T11 **已关闭**） | 3.7 已核对依赖闭包的许可（MIT / BSD / Apache-2.0 / PSF / MIT-CMU 级），但仓库自身既无 `LICENSE` 文件，`pyproject.toml` 也没有 `license` / `classifiers` 字段。对内自持不构成即时风险，但「依赖许可干净」与「本项目许可已声明」是两件事，后者目前为空白。**关闭口径（CH-456）**：根目录 `LICENSE`（MIT，Copyright 2026 Doc2md contributors）+ `pyproject.toml` 补 `license = "MIT"` 与 8 项 `classifiers`（Development Status / Environment / Intended Audience / OS Independent / Python 3.11–3.13 等） |
| D-12 | 无静态检查工具链（CH-456 / T10 **已关闭**） | 仓库没有 mypy / ruff / black / pre-commit 的任何配置，CI 只跑 pytest。后果由本轮自查量化：**7 处未使用导入**（`api.py` 的 `E4133_DirTooWide`——该码实际由 `security/dirguard.py` 抛出；`ops/backup.py` 的 `shutil`、`subprocess`；`ops/gguf.py` 的 `asdict`；`pipeline/batch.py` 的 `Iterable`；`storage/db.py` 的 `json`、`time`）与 8 处零调用符号（其中 1 处早已登记，其余 7 处为本轮复查所得，逐项见 D-9）。附带发现一个更值得注意的形态：代码里已有 5 处 `# noqa: BLE001`（包内 4 处：`cli.py:467` `pipeline/batch.py:80,128` `storage/repo.py:99`；测试 1 处：`test_m9a_search.py:178`）与 1 处 `# noqa: F401`（`pipeline/s1_parse.py:168`，注释写明「保留供后续扩展」）——即**仓库按「有 linter」的假设写了抑制注释，但没有任何工具读取它们**；这些注释现在是纯意图声明，接入 ruff 后立即生效，届时 `storage/db.py:14,17` 这类未用导入会直接被 CI 报出。同类漂移还有配置声明面：`pytest.ini` 声明的三个 marker 中 `slow` 零使用、`milestone` 的 `M4M6` 从未出现（实际用组合名 `M0+M2`），且未开 `--strict-markers`，写错标记名不会报错只会静默不选中。这类问题不会让任何断言变红，只能靠人工读——所以 3.7 把「引入 linter」列为显式未采用项而不是遗漏，并把补齐动作挂到 T10。**关闭口径（CH-456）**：ruff（`F`/`E`/`I`）接入为 CI 独立信息作业（`ruff check .`，与 merge-gate 并行无 `needs`，风格判断不入红线）；7 处未使用导入清零；`pytest.ini` marker 对齐实际用法并开 `--strict-markers`；全仓 `ruff check .` 通过（D-12 正文预言的「未用导入会被报出」应验，且 CH-455 的 F821（`total` 未定义）正是引入 ruff 后抓出的第一个真缺陷） |
| D-13 | 跨包直引 | 门面口径（3.6 第 7 条）要求跨包走 `__init__`，现状存在 `from .storage.db import Store`、`from .ops.logging import RotatingJsonlWriter` 这类直引模块。不视为缺陷：`__all__` 与附录 E/G 已固定职责边界，直引不改变依赖方向；但新增引用应按门面写，避免重构内部文件时波及调用方 |

### 12.7 未验证项（诚实标注）

| 项 | 状态 | 出口条件 |
| --- | --- | --- |
| CI 首次远端运行 | **已验证**（run #1 四作业全绿，见 12.1）；「失败阻断合并」仍未生效，属平台能力限制 | T1：必需检查落地并用一次故意失败的 PR 证明 |
| Python 3.11 / 3.13 矩阵 | **已实测**：红线层 65 项在 3.11 / 3.12 / 3.13 三个作业上均 `[100%]` 全绿（GitHub ubuntu-latest runner） | 全量套件目前仅在 3.12 上跑；如需其余版本的全量口径，扩矩阵或补运行 |
| Windows 11 真机（cp936、spawn、路径长度、无 fork） | 仅沙箱内以 `encoding=cp936` 与 `start_method="spawn"` 复现语义，未在真机验证 | 至少一组同口径数字进入 12.3/12.4（待办 T3；测量工具已就绪——`ops/bench/t6_large_corpus.py` 跨平台化，Windows 后端 psutil / 降级 none，CH-458） |
| Mac mini 16GB 真机驻留/吞吐 + launchd 定时 | 未实测；12.3 数字来自 Linux 沙箱 | 同上（T3；macOS 后端 ps 已在 Linux 上借 GNU procps 交叉验证，CH-458） |
| 真实本地模型服务 | **部分验证**：请求/响应契约、故障分级、fallback、E5041、注入归因已用 stdlib 假 llama-server 全绿（22 项，12.5）+ e2e 实测；**未验证**：真实 gguf 模型加载（沙箱无模型资产）与真实模型输出下的产物等价性边界 | 有资产后跑一次真实 server 全链路，并重判等价断言范围 |
| 备份恢复演练（DoD 第 11 条） | **已脚本化并实测**（CH-456 / T7：`doc2md backup drill` 8 步闭环 + 13 项测试，12.4）；**未验证**：真实外置卷（加密盘 / 网络卷）上的演练——沙箱内以本地目录模拟卷行为 | 有真实外置介质后跑一次 |
| >1000 文件混合语料的队列积压/失败率分布 | **已测**（CH-457 / T6：1080 文件三张表，12.3）；**未验证**：Windows / macOS 同口径数字（属 T3，测量工具已跨平台就绪，CH-458） | T3 |

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
5. 等价性：`git worktree add /tmp/pre HEAD` 取改造前代码，同一脚本分别在 `/tmp/pre`、工作区 chunk=0、chunk=32 三态下转换全部黄金样本，比对 zip 条目哈希（`content.md` 先剥 front-matter）。
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
| **v2.5** | 2026-10-15 | **全面审查优化轮 · 批次②：性能与存储**（登记为 CH-460）：实施 A-3（`Store` 连接登记表改弱引用 + `live_conn_count`）、A-4/A-14（`save_ir` 单事务 + `executemany`，`tx=` 形参贯通 `_finish_ok` / 同步 `/jobs`，顺带修掉 `detail_json` 被 `mark(done)` 覆盖的静默丢失）、A-5（S1.5 门禁拆 `GateCollector` 采集/判定，auto 146.1→73.5ms、even5 70.9→7.3ms）、A-6（`reindex_block` 单块索引）、A-9（FTS 查询上限 + 区间掩码）、A-10（多文件投递按文件派生幂等键）、A-12（脱敏统一为「不看键名只看长度」）、A-15（删除 404 + 回滚 400）；**A-8 实测回退**（按片读取峰值内存不降反升，132.9→134.9/137.9MB，改动撤销、证据入 12.3）；对外契约收敛三处（删除不存在 404、回滚非数字 400、多文件幂等键派生），产物面零变化。新增 `tests/test_batch2_storage.py` 26 项，gate 67 全绿、全量 302 收集、六个黄金样本 gate 明细与 md_sha 逐字段一致；附录 F/G 数字刷新（39 模块 6216 行、19 测试文件 5922 行 302 收集）。`PIPELINE_VERSION` 维持 2.4.0；`PLAN_VERSION` 升至 `2.5` |

下一版本递增条件：主题篇任一章的规格陈述发生变化（新增能力、改变默认值、调整契约），**或主题篇 / 附录新增章节扩大了覆盖面**（CH-454 属后者：既有判读不动，但读者能查到的实现面事实变多了）。仅登记新 CH 条目、或只做事实口径更正时不改版本号，只追加第十一 / 十二章内容（CH-453 属此类）。文件名不随版本改变——版本号只出现在本文头部表格与 `PLAN_VERSION` 两处，避免带版本的引用再次悬空。
