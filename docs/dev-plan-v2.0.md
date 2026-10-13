# Doc2md 项目开发方案 V2.0

| 项目 | 内容 |
| --- | --- |
| 项目名称 | **Doc2md** |
| 管线版本 | **v2.4.0**（`doc2md.PIPELINE_VERSION`） |
| 方案文档版本 | **v2.0**（2026-10-13 重写；上一版 v1.0 为面向开发前的计划书） |
| 文档性质 | 开发主文档：主题篇为**当前有效规格**（as-built + 待办），登记篇记录阶段性变更与优化项 |
| 编制依据 | 仓库 `RunoobGo/doc2md` 实际代码与实测结果；管线 v2.4 Delta 记录；方案 v1.0 计划书 |
| 结构约定 | 主题篇（第一–九章）+ 附录 A–F（规格表）保持独立可读，**不含变更编号**；所有阶段性变更与优化项集中在登记篇（第十–十二章） |

## 阅读指南

- **想理解产品与规格**：读第一–九章。叙述顺序为「定位 → 需求 → 架构 → 管线 → 契约 → 安全运维 → 低配机运行 → 质量验收 → 实施计划」，每一章只依赖前一章已建立的概念。
- **想查具体取值**：查附录 A（配置全表）、B（CLI 全表）、C（API 全表）、D（错误码与告警码）、E（数据库结构）、F（门禁断言清单）。
- **想知道为什么某处是这样、改过什么**：查登记篇第十一章（CH-422 … CH-453 逐条登记）与第十二章（实测证据汇总、实现偏差、本文档变更记录）。
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
- **方案文档版本**独立编号（本版 v2.0），每次实质性修订升 minor；与管线版本解耦。文档自身的变更记录见第十二章。
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
| NFR-1 | 离线 | 主链路零网络调用，由依赖清单与代码事实保证（包内无任何出站请求实现，运行时依赖仅 4 项本地库）；`run.offline_assert` 目前为声明性键，尚未实现为可开关的断言（附录 A / D-8） | `pipeline/*`、`llm.py`（只连 `127.0.0.1`） |
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

**离线实现**：无本地模型服务时走确定性占位实现，只产出白名单内的层级修正 op，保证主链路可离线验收；真实服务接入属待办（第九章），接入后同一套校验与断言不变。

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

角色按 rank 比较（reader 1 < editor 2 < admin 3）；无 token/未知/已吊销 → 401，角色不足 → 403。**授权每次请求实时查库**（`tokens.revoked_at` 一写即生效），因此「吊销 ≤5s 生效」是天然满足而非依赖窗口配置；`api.revoke_effective_seconds` 目前只作为对外承诺值在 CLI/自检面展示（属附录 A 声明性键，不驱动代码行为）。CLI 侧 `doc2md serve token` 是同一能力的事务型等价物，可在服务未启动时签发。

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
| L1 红线（merge-gate） | 六条产品级断言 + 开发期补强断言（当前 65 项） | 每次合并前必须全绿 | 阻断合并；按 8.5 降级表处置 |
| L2 全量回归 | 功能/契约/运维面全量（当前 143 项，1 项按环境跳过） | 每次发布前 | 阻断发布 |
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

**资产自身的可复现性是验收对象**：生成过程必须剥离时间戳（PDF 创建日期、DOCX zip mtime 与 core 日期），路径不得写死绝对目录，循环链接用相对目标——否则换机即失真，且「两次生成一致」断言不可能成立。120MB 大文件不入库（分发包体积），相应判定在测试内自造，避免留下无人消费的占位资产。

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

**当前状态**：workflow 已进远端并在每次 push 到 `main` 时执行（第十二章 12.1 有首轮实测行），红线失败会以红色检查呈现。**但「失败即阻断合并」尚未强制生效**：本仓库是 private 且为免费计划，分支保护与规则集两类 API 均返回 `403 Upgrade to GitHub Pro or make this repository public`，因此当前无法把 merge-gate 设为必需检查。在补齐该能力之前，合并前的红线判定仍以人工执行 `pytest -m gate` 留存结果为准——「红线没有强制执行点」等价于「没有红线」。

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

### 9.2 待办（按优先级）

| # | 待办 | 判据 / 出口条件 | 风险 |
| --- | --- | --- | --- |
| T1 | 让红线**真正阻断合并**（不只是「跑并显示红」） | 仓库具备必需检查能力：分支保护或规则集 API 不再 403，且 `merge-gate (3.11/3.12/3.13)` 被列为必需检查；一次故意失败的 PR 被拦下 | 工作流本身已进远端并开始执行（12.1 首轮记录）。阻碍改为平台能力：private + 免费计划下分支保护与规则集均返回 403，可选路径为升级计划、改为 public，或接受「CI 显示 + 人工判绿」的半强制态 |
| T2 | 接入本地模型服务，替换离线占位实现 | 同一套白名单与注入断言在真实服务下全绿；服务不可用时明确 503 并保持主链路 | 真实模型输出会引入不确定性，等价断言范围需重新界定 |
| T3 | 跨机吞吐基线：把内存/耗时表跑成 Windows 与 macOS 实测 | 至少各一组同口径数字进入第十二章（当前仅 Linux 沙箱实测） | 现有数字不可直接外推到其他平台 |
| T4 | 队列可视化：审阅页展示队列进度与失败原因 | 页面无需读日志即可判断「卡在哪一步」 | 需保持 NFR-20（不含正文） |
| T5 | Windows 侧运维面：任务计划程序单元与安装校验脚本 | 与 launchd 同等能力并有校验命令 | 当前仅有 macOS 定时资产 |
| T6 | 大语料吞吐与驻留分布（>1000 文件、混合类型） | 给出队列积压、内存、失败率三张表 | 现有语料规模小 |
| T7 | 备份恢复演练自动化（DoD 第 11 条脚本化） | 一条命令完成「删除→轮出确认→解密恢复→integrity_check→比对」 | 当前依赖手工步骤 |
| T8 | 门禁时长优化 | 红线层单轮 < 60s（低负载沙箱已实测 72.5s，仍需稳定达标并给出可复现的裁剪口径） | 变慢会削弱「红线值得每次都跑」的执行意愿 |
| T9 | 声明性配置与预留码收敛（D-8 / D-9） | 11 个未消费配置键要么实现要么从默认 TOML 删除；`E5041`/`E5051`/`W1044`/`W1999` 在启用时同步正文；`Scheduler.next_queued()` 死代码删除或接入消费路径 | 「看起来可配」的开关比没有开关更容易造成误用与错误排障 |

### 9.3 近期迭代节奏

1. **T1 收尾**：红线已有执行点，但还差「失败不可合并」这一硬约束；在补齐之前，其余工作的回归成本仍靠人工承担。
2. 之后按「真实模型接入（T2）」与「跨机基线（T3）」并行推进；两者都会影响规格中的数字，须同批更新第十二章。
3. 每个迭代出口条件固定为：红线全绿 + 等价性三态复核 0 不匹配 + 新增能力有实测行。

---

# 附录（规格表）

附录是查询面，不叙述理由。凡与正文冲突处，以正文的规格陈述为准、附录的取值为事实来源；两者不一致即为缺陷，须登记。

## 附录 A 配置全表

来源：`doc2md/config/default_toml.py`（内置默认，49 键）与 `config/doc2md.example.toml`（分发示例，42 键）。
解析顺序（**只叠加一个用户文件**）：内置默认 → 若给了 `--config PATH` 就用它；否则取 `./doc2md.toml`，不存在再取 `$DOC2MD_HOME/doc2md.toml`（`DOC2MD_HOME` 默认 `~/.doc2md`）→ 与该默认值深合并。两个候选位置**不是级联**：存在 `./doc2md.toml` 时 `$DOC2MD_HOME` 下的文件不会被读。服务侧再叠加 `work/settings_override.json`（`PUT /settings` 落盘，`create_app()` 时合并）。
相对路径一律经 `config.path_setting()` 解析：绝对值原样；相对值若首段等于实际 root 名或默认根名 `work`，剥掉该段后挂到 root 下（`paths.root` 自身不剥前缀）。

「消费」列标明该键是否被运行时代码读取。**「声明性」= 出现在默认配置中，但当前代码未读取**（第十二章偏差项 D-8 逐条列出）；这类键不得被当成可用开关写进用户文档，实现前须在登记篇补条目。

| 键 | 默认值 | 消费方 | 规格语义 |
| --- | --- | --- | --- |
| `paths.root` | `"work"` | cli / api / cron / loader | 数据根目录（state.sqlite、out、logs、tmp、reports 的父级） |
| `paths.out` | `"work/out"` | cli | 产物 zip 目录 |
| `paths.reports` | `"work/reports"` | cron | 日报与 cron 结果 JSON |
| `paths.logs` | `"work/logs"` | api / cron / cli | 三类 JSONL 日志目录 |
| `paths.tmp` | `"work/tmp"` | cron / api | 中转目录；服务启动与 `cron --daily` 各清扫一次 |
| `paths.vendor_models` | `"vendor/models/llm"` | 声明性 | 预留：本地模型资产目录（真实接入待办 T2） |
| `run.profile` | `"standard"` | 声明性 | 预留：lite/standard/strict 三档尚未映射到具体键集（第七章给出手工等价配置） |
| `run.offline_assert` | `true` | 声明性 | 预留：出站网络断言（当前由依赖清单与代码零出站调用保证） |
| `s1.chunk_pages` | `32` | cli / convert | 每重新打开一次文档最多解析页数；`0` = 整本一次读完 |
| `s1_5.sample_pages` | `"auto"` | cli / convert | 体检模式：`auto` / `even5` / `legacy3` |
| `s1_5.min_chars_per_page` | `120` | convert | 单页字符下限 |
| `s1_5.reject_on_any_bad_page` | `true` | 声明性 | 实际行为由 `sample_pages` 模式决定（`auto` 只按空段/占比拒收） |
| `s1_5.page_detail_in_report` | `true` | 声明性 | 页级明细恒写入报告，无开关 |
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
| `api.token_roles` | `["admin","editor","reader"]` | 声明性 | 角色集合由 `auth.ROLES`/`MATRIX` 定义，不读配置 |
| `api.revoke_effective_seconds` | `5` | cli（仅用于 `token --revoke` 与 `doctor` 的展示字段） | 对外承诺的吊销生效窗口；授权实时查库，故不驱动任何等待逻辑 |
| `api.base_revision_required` | `true` | api / cli | `false` = 灰度期允许无 `base_revision` 的 PATCH |
| `llm.bind` / `llm.port` / `llm.auth` | `"127.0.0.1"` / `8080` / `false` | 声明性 | 预留：本地模型服务地址与鉴权（待办 T2；`llm.auth=false` 属已知接受项） |
| `llm.op_whitelist` | 5 个 op | convert / cli | 补助手可应用 op 白名单（唯一权威来源） |
| `logs.rotate_mb` | `50` | api / cron / worker | 单文件轮转阈值（MB） |
| `logs.rotate_keep` | `5` | api / cron / worker | 轮转保留份数 |
| `ops.events_retention_days` | `30` | cron | `events` 表保留期 |
| `ops.backup_local_dir` | `"work/reports/backups"` | cron | 本地快照目录（权限 700） |
| `ops.backup_external_dir` | 未设（示例给出） | cron | 外置卷目录；未配置 → 周报记 `skipped` 且 `ok=True` |
| `ops.backup_external_encrypted` | `true` | cron / cli | 外置副本强制加密；置 `false` 时写外置即抛 `PermissionError` |
| `ops.backup_rotation_days` | `14` | cron | 副本轮出天数 |
| `ops.work_dir_mode` | `"700"` | 声明性 | 目录权限由备份模块硬编码 `0o700`，不读该值 |
| `security.max_name_bytes` | `128` | convert | 文件名消毒后的 UTF-8 字节预算 |
| `security.sanitize_fail_action` | `"reject"` | 声明性 | 消毒失败一律拒收（E4003），无降级分支，故无动作开关 |

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
| `E4151` | 415 | ApiError | 非文本型 PDF / 非 DOCX（magic 与部件判定）；打开源文件 `OSError` 同归此码 |
| `E4221` | 422 | ApiError | S1.5 体检门禁拒收（`detail={mode, sampled_pages, reasons, stats}`） |
| `E4251` | 425 | ApiError | 乐观锁冲突（`detail={current_revision}`） |
| `E5031` | 500 | Doc2mdError | 存储层失败：产物清除失败（fail-closed）、写盘 `OSError`；CLI 兜底 `OSError` 亦映射此码 |
| `E5041` | 503 | Doc2mdError | **预留**：本地模型服务不可用（真实接入待办 T2 时启用；当前无抛出点） |
| `E5051` | 500 | Doc2mdError | **预留**：分片缝合失败。当前缝合异常由第四章的分片降级口径捕获（降级单片 + 记 `W1043` 留痕并如实标注片数），不抛该码 |

告警码（写入 `ir.warnings` 与报告，不影响成功判定）：

| 码 | 语义 |
| --- | --- |
| `W1043` | 跨片表格不合并（缝合处任一侧为 table）；同时是降级单片时的留痕标记 |
| `W1044` | 分片转换说明（报告口径）。当前代码未写入该常量（第十二章偏差 D-9） |
| `W1999` | 兜底告警槽位。当前代码未使用（第十二章偏差 D-9） |

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

红线层 = `pytest -m gate`，当前 **65 项**（墙钟耗时随机器负载在 72–110s 区间，两轮实测见第十二章 12.1）。这里的「项」是 pytest 收集数，含参数化展开（如恶意名单 12 例各算一项）。全量套件 143 收集 → 142 passed + 1 skipped。

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

第二层：低配机专项（`tests/test_perf_lowend.py`，收集 25 项）

分块默认值有界、分块产物等价（chunk 1/2/3/32 vs 整本）、流式哈希与全量哈希一致、workers 边界（0→`min(4,cpu)`、负→1）、进程池 vs 串行产物逐条目一致（含 `spawn` 参数化 = Windows 语义）、逐文件门禁拒收上报、CLI 目录确实走池、异步入队→消费→文档可见、幂等与附着、领取原子性、无源路径任务判失败、`work` 日志独立、磁盘门禁四态（阻断 / 系数 / 仅双零关闭 / API 413）、cron 记磁盘门禁失败、类型判定映射 415、API 魔术码不符 415、CLI 机器行纯 ASCII、`_console_safe` 替换不可编码字符、不支持类型退出码 4、目录含坏文件退出码聚合、`doctor` 暴露关键开关。

覆盖纪律：任何「资产存在但无测试消费」「断言恒真（如中文检索零命中下的 0 残留）」都算伪覆盖，必须在本清单里显式失效或补真判定用例（第十二章教训条目）。

---

# 登记篇

本章之后的内容**只属于登记面**：阶段性缺陷、更优方案、实测证据、实现偏差与本文档自身的版本记录。主题篇不引用这里的编号，读者可整篇跳过而不损失任何规格信息。

## 十、变更与登记机制

### 10.1 登记流

| 要素 | 规则 |
| --- | --- |
| 编号 | `CH-xxx` 单调递增，不复用、不回收。`CH-401…CH-421` 为 v2.4 需求/ADR 阶段占用；开发期从 `CH-422` 起续号，当前至 `CH-453`。下一条目自 `CH-454` 起 |
| 触发 | ① 实测发现缺陷；② 采用更优方案改变了原设计；③ 契约面（错误码 / 退出码 / HTTP / 产物结构）发生变化；④ 验收口径本身被证明不可判定 |
| 必填四要素 | 动因（含可复现证据）、落点（文件:函数）、实测数字、契约影响（无 / 内部 / 对外） |
| 禁止 | 以「优化」名义改动产物字节而不做等价复核；新增错误码段位；把未测项写成已实现 |
| 关闭 | 主题篇相应规格已更新 + 门禁有对应断言 + 第十二章有证据行，三者齐备才算关闭 |

**引用消歧（重要）**：登记篇正文里的「附录 B-10 / B-11 / B-12 / B-13 / B-14」「附录 C-1 / C-2」「附录 E」「§x.y」形式的引用，指的是**上游《Doc2md V1.0》计划书与其附录**（该文件不在仓库内，作为需求来源保留在项目上传记录中）；本文的「附录 A–F」是本文自己的规格表，两者字母含义不同。为避免混淆，登记篇新条目一律写作「上游蓝本 B-11」「上游 C-1」并在需要处给出本文对应章节号。

### 10.2 偏差登记（D-x）与缺陷登记（CH-xxx）的区分

`D-x` 记录「实现与方案文本不一致，但实现是对的或方案文本本身有矛盾」的落地口径，不产生代码改动义务；`CH-xxx` 记录需要（或已经）改动代码/文档的事实。两者都不得被用来掩盖未验证项——未验证项统一进第十二章的「未验证」表。

### 10.3 本文档的沿革

- 方案 v1.0 是开发前的计划书（面向任务卡、含伪码脚手架），登记流定义在其 §8.2。
- 管线 v2.4 的增量与验收记录以「附录 H」形式挂在 v1.0 之后（`docs/plan-delta-v1.1.md`）。
- 本 v2.0 把两者合并为单一主文档：规格叙述重写入主题篇与附录，历史事实原样迁入登记篇。`docs/plan-delta-v1.1.md` 保留为指向本文的存根，避免两处正文各自漂移。
- 迁移原则：条目内容不做「美化式」重写，只压缩表述；数字一律保留原始口径与测量方式，重测得到的新数字并列记录并注明语料（见 12.3）。

## 十一、逐条变更登记（CH-422 … CH-453）

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
| CH-452 | 文档 | 开发方案全面重写为单一主文档：主题篇规格化（不含变更编号）、附录 A–F 建表、登记篇收全部 CH/D/证据；`docs/plan-delta-v1.1.md` 降级为存根；`PLAN_VERSION` 1.0 → 2.0；清除代码里写死的过时基准数字；全仓 40 余处旧锚点引用改指本文章节 | `docs/dev-plan-v2.0.md` `docs/plan-delta-v1.1.md` `doc2md/__init__.py` `README.md` `doc2md/**`（注释）`tests/**`（docstring）`config/` `pytest.ini` `.github/workflows/ci.yml` | 无运行时行为变化（仅注释与文档） |
| CH-453 | 更优方案 | 令牌补 `Workflows` 权限后 workflow 首次推送成功，远端触发 run #1（push→`main`）四作业全绿：`merge-gate` 3.11/3.12/3.13 各 65 项、`full-suite` 143 项含可复现性与 `doctor`。据此把「CI 未验证 / 推送受阻」的口径整体改写为「已运行、但未强制阻断」——阻断仍缺必需检查能力（private + 免费计划下分支保护与规则集 API 均 403）。T1 判据随之从「推上远端」收紧为「故意失败的 PR 被拦下」 | `docs/dev-plan-v2.0.md`（8.6 / 9.1 / 9.2 / 9.3 / 12.1 / 12.7 / CH-446 行）`README.md` | 无运行时行为变化；交付流程从「无执行点」变为「有执行点、无硬阻断」 |

以下对影响规格判读最大的条目补一段说明；其余条目的细节以表格落点 + 第十二章证据为准。

**CH-424（门禁口径）**：even5 与旧三点都是**抽样**——实测 60 页样本的坏区落在第 51–53 页时两者同时漏过；反过来纯链接页密度的文档被误杀。`auto` 把判定面放到全文档页密度（成段空/占比超阈才拒收），报告面仍给 5 个采样页明细，因此上游 C-1 的验收可读性不受影响。对照基线 `even5`/`legacy3` 保留，可复现「漏检/误杀」两种形态。

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
2. 代码、测试、配置样例、`pytest.ini`、CI 工作流里共 40 余处旧锚点引用（形如 `§5.1`、`§7.2`、「方案 v1.1 附录 H」、「附录 B-10/B-11」），在方案 v2.0 中已无对应编号，属**悬空引用**；本轮统一改指 v2.0 的章节号（如 `§7.2` → 「方案 v2.0 4.3 / 6.1 / 8.5」按语义分别归位），并把蓝本类引用统一前缀为「上游蓝本」以区别本文附录。仅保留明确标注「上游锚点」的沿革性引用（配置装载、API、IR、错误码四处，用于说明字段来源）。

**登记流自身的连带要求**：今后新增条目时，代码注释只允许引用本文**章节号 / 附录号**与 `CH-xxx`（后者仅作为索引出现在注释里，其叙述正文在第十一章），不得再引用旧计划书编号体系，也不得在注释里写测量数字——避免同一失效形态复发。

**CH-453（口径更正，而非新规格）**：本轮唯一的事实变化是「CI 有了执行点」。文档此前把 T1 的出口条件写成「远端出现运行记录」，达成后发现那句话给多了——远端会跑，但跑红了并不拦住合并。因此出口条件收紧为「故意失败的 PR 被拦下」，阻碍从令牌权限改判为平台能力：本仓库 private 且在免费计划下，分支保护与规则集两类 API 都返回 403（已实测），必需检查无法配置。规格判读未变（红线内容、项数、分层都不动），故 `PLAN_VERSION` 保持 `2.0`，仅更正 8.6 / 9.1 / 9.2 / 12.1 / 12.7 的事实口径。遗留的诚实边界：全量套件与可复现性只在 3.12 上跑，3.11/3.13 仅有红线层证据；本轮取证用的令牌仍缺 `checks:read`（`check-runs` API 与 annotations 读取返回 403），运行结论是从 Actions 作业状态与日志取的，不是从检查 API 取的。

## 十二、实测证据汇总

**测量环境**：Linux 沙箱，4 vCPU / 8GB，Python 3.12， editable 安装于独立 venv；`resource.getrusage(...).ru_maxrss` 取峰值驻留（KB→MB 按 1024 折算）。凡标注「同口径」者按 12.9 的复现方法重跑。跨机（Windows 11 / macOS 真实硬件）数字尚未实测，见 12.7。

### 12.1 门禁与套件

| 项 | 数值 | 说明 |
| --- | --- | --- |
| `pytest -m gate` | **65 项全绿**；低端机专项收口轮实测 110.4s，本轮（纯文档/注释改动后）复核 **72.5s** | 红线层；历史增长 35 → 40（CH-444 后）→ 65（CH-447…451 后）。墙钟耗时随沙箱负载浮动，只作趋势监控不作断言 |
| 全量 `pytest` | 143 收集 → **142 passed + 1 skipped**；110.8s（本轮复核），收口轮 164.9s | skipped 为可选大资产（CH-443） |
| CI 远端运行 | 首轮 run #1（push 到 `main`，commit `34e2853`）**四个作业全绿**：`merge-gate (3.11/3.12/3.13)` 各 65 项 `[100%]`，`full-suite` 143 项 `[100%]` + 可复现性 + `doctor` | GitHub Actions run #1，2026-10-13。测试步骤自身墙钟：红线层 72s（3.12）/116s（3.11）/122s（3.13），全量 183s（3.12）——runner 上同样比本地慢，项数才是硬口径 |
| 失败能否阻断合并 | **未生效** | 必需检查能力受平台限制（private + 免费计划 → 分支保护 / 规则集 API 403），见 T1 |

> 门禁基线引用规则：正文与 README 引用「项数」为硬口径（65 项），引用「耗时」须带轮次——两处文档统一以本轮复核值（gate ≈72s、全量 ≈109s）为准，110.4s / 164.9s 作为上一轮实测并列保留。

### 12.2 产物等价性（G6 的执行证据）

三态对比：改造前（`git worktree` 检出 HEAD）、改造后 `chunk_pages=0`、改造后 `chunk_pages=32`；6 个黄金样本各转一次，比对 zip 内每一条目 sha256（`content.md` 先剥 front-matter）。

| 结论 | 值 |
| --- | --- |
| 不匹配条目总数 | **TOTAL_MISMATCHES = 0** |
| 覆盖样本 | small / links_pdf / badtail / docx 系列 / 320 页大样本（6 个） |
| 补充断言 | 分块边界参数化（chunk 1/2/3/32 vs 整本）、进程池 vs 串行含 `spawn`、队列产物 vs 串行产物 |

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

### 12.4 低配机活测（真进程，非 test client）

| 断言面 | 关键实测 |
| --- | --- |
| 异步入队（CH-449） | `POST /jobs` 指向 14 文件目录 → **202 / 13 个作业条目 / 13ms**（同目录 12 个副本内容寻址去重，落库 2 个 doc）；`doc2md work --once` → `{"drained":2,"done":2}`；`GET /jobs` counts `{'done':2}`；队列产物与串行产物逐条目 sha256 全等；事件链 `job_queued → job_done`；`work/` 实测 0700 |
| 入队原子性 | 混入非文档文件 → 415/E4151 且 `queued` 计数**零增长**（不变式为「零增长」，非「回到 1」；早期表述已在 CH-449 修正） |
| 日志隔离 | `app.jsonl` 3 行 / `work.jsonl` 4 行（含 `WORK_START`、`job_done`，无正文）；`cron.jsonl` 未创建 |
| 磁盘门禁（CH-450） | tmpfs 限 40MB 作 `--out` → `doc2md convert` **rc=4 / E4131 need 512MB, free 40MB**，输出目录 0 文件（无截断 zip） |
| cp936（CH-451） | `PYTHONIOENCODING=cp936` + `🔥报告.pdf` → rc=0、stderr 无 `UnicodeEncodeError`、`--json` 行纯 ASCII 可解析；serve 面伪 `PK` 非 docx → **415/E4151**（原 500）、缺失路径 404 |
| 服务面轮转（CH-444） | 空 root 起服务（`rotate_mb=0.0002`）→ 启动首行 `SERVICE_START{root,tmp_cleaned:1,rotate_mb,keep}` 且预置 tmp 文件被清扫；3 次真投递后出现 `app.jsonl.1`（首行 `E_LOG_ROTATE`），服务不重启继续接活；随后独立进程 `cron --daily` 跑通，`app.jsonl*` md5 逐一未变 |

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

### 12.6 实现偏差登记（D-x，非缺陷）

| 编号 | 项 | 口径 |
| --- | --- | --- |
| D-1 | 蓝本脚手架签名 | `char_similarity`/`half_sentence_heads` 由 `pipeline/metrics.py` 提供；分片开关是 `mode="two_phase"` 而非伪码的 `shard=True`；zip 路径经 `Result.zip_path` 暴露 |
| D-2 | 审计断言写法 | 蓝本断言 `audit[-1]["before"] == "改A"` 与自身 fixture 顺序矛盾；实现按 ADR-28 真实语义断「每条审计 `before` == `from_revision` 内容」 |
| D-3 | 索引刷新事务边界 | `reindex` 必须在 `patch_block` 的 `store.tx()` **之外**（自带 `BEGIN IMMEDIATE`，嵌套即 `OperationalError`）；蓝本伪码未标注，已在 `edits.py` 注释固化 |
| D-4 | 回滚 revision 取值 | 优先取 `from_revision==N` 的 `before`，缺失退 `to_revision==N` 的 `after`；审计链首与 `revision=1` 无审计时走同一回退路径 |
| D-5 | 版本命名 | state.sqlite 结构版本改名 `DB_VERSION`，与 IR `SCHEMA_VERSION=14` 分列（迁移判定曾读错对象） |
| D-6 | 慢测处置 | 门禁按 `-m gate` 标记分层（65 项；收口轮 110.4s、复核轮 72.5s），大样本 fixture session 作用域共享；蓝本的 difflib 直译口径在本机不可完成（>300s），按 CH-428/429 替换后方可作 CI 红线 |
| D-7 | 缺 confirm 的状态码 | 未新增码值（段位冻结），回到 400/E4001 + `detail.requires="confirm=true"`；自造 `E4091` 类已删除（CH-439） |
| D-8 | **配置面声明性键** | 默认 TOML 49 键中 **11 键当前无代码消费**：`paths.vendor_models`、`run.profile`、`run.offline_assert`、`s1_5.reject_on_any_bad_page`、`s1_5.page_detail_in_report`、`api.token_roles`、`llm.bind`、`llm.port`、`llm.auth`、`ops.work_dir_mode`、`security.sanitize_fail_action`。附录 A 已逐键标注。处置方向：要么实现（`run.profile` 三档映射、`llm.*` 随 T2 落地），要么从默认配置删除——不得长期停留在「看起来可配」状态（待办 T9） |
| D-9 | **契约面空位** | `E5041`（模型不可用）、`E5051`（缝合失败）在代码中定义但无抛出点；`W1044`、`W1999` 未被写入；`Scheduler.next_queued()` 无调用方（消费路径由 `worker.claim()` 承担）。前三项属预留段位，需在启用时同步主题篇与附录 D；`next_queued` 属死代码，应在下个变更里删除或接入（待办 T9） |
| D-10 | 分发示例与默认值的键集差异 | 默认 TOML 49 键、示例 42 键。示例**多** 1 项 `ops.backup_external_dir`（家用机可选路径，故意给样例）；示例**少** 8 项：`run.profile`、`run.offline_assert`、`llm.bind`、`llm.port`、`llm.auth`、`llm.op_whitelist`、`security.max_name_bytes`、`security.sanitize_fail_action`（其中 4 项本就属 D-8 的声明性键）。新增键时两处需同时决定，避免「示例里没有 = 不支持」的误读 |

### 12.7 未验证项（诚实标注）

| 项 | 状态 | 出口条件 |
| --- | --- | --- |
| CI 首次远端运行 | **已验证**（run #1 四作业全绿，见 12.1）；「失败阻断合并」仍未生效，属平台能力限制 | T1：必需检查落地并用一次故意失败的 PR 证明 |
| Python 3.11 / 3.13 矩阵 | **已实测**：红线层 65 项在 3.11 / 3.12 / 3.13 三个作业上均 `[100%]` 全绿（GitHub ubuntu-latest runner） | 全量套件目前仅在 3.12 上跑；如需其余版本的全量口径，扩矩阵或补运行 |
| Windows 11 真机（cp936、spawn、路径长度、无 fork） | 仅沙箱内以 `encoding=cp936` 与 `start_method="spawn"` 复现语义，未在真机验证 | 至少一组同口径数字进入 12.3/12.4（待办 T3） |
| Mac mini 16GB 真机驻留/吞吐 + launchd 定时 | 未实测；12.3 数字来自 Linux 沙箱 | 同上（T3） |
| 真实本地模型服务（`llm.*` 生效、E5041 启用） | 未实现（当前为离线占位实现） | T2 |
| 备份恢复演练（DoD 第 11 条） | 手工步骤走通过，未脚本化 | T7 |
| >1000 文件混合语料的队列积压/失败率分布 | 未测（现有语料规模 8–30 文件） | T6 |

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

下一版本递增条件：主题篇任一章的规格陈述发生变化（新增能力、改变默认值、调整契约）。仅登记新 CH 条目时不改版本号，只追加第十一/十二章内容。
