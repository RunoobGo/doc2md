# 文本型 PDF / DOCX → Markdown 离线转换管线 v2.1

## 可行性评估 + Mac mini M4 适配 + 方案完善（可直接指导 AI Agent 开发）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | **v2.1**（v1 = Win11/i5-12500T/8G 离线管线方案；v2 = 可行性评估 + Mac mini M4 适配；v2.1 = 新增产品形态、PRD 需求基线、API 与前端设计，并按「内部自用 / 不处理图片与扫描件 / 按较高性能设定阈值」三项确认调整全篇） |
| 编写日期 | 2026-10-13 |
| 适用目标平台 | ① Windows 11 + i5-12500T + 8GB（下称 **T8 低配档**）<br>② Mac mini M4 / M4 Pro，16–32GB 统一内存（下称 **M4 档**） |
| 处理范围 | **仅文本型（born-digital）PDF 与 DOCX**。扫描件、图片型 PDF、公式密集型学术论文不在范围内；**图片与插图内容不做提取、不做 OCR**（见 §6.2 范围声明） |
| 交付形态 | **内部自用，不对外分发**；单机部署、离线运行、可插拔流水线 + 每阶段落盘中间产物 + 校验报告 |
| 使用界面 | CLI（自动化/回归）+ **HTTP API（服务化）+ Web 控制台（日常使用）** 三入口，共用同一内核 |
| 性能档位 | **按较高性能设定阈值**：以 Mac mini M4 满载为基准做并发与内存预算，T8 只保证「能跑通」而不保证吞吐 |
| 文档定位 | 既是决策依据（可行性/风险）、需求基线（§6 PRD），也是工程规格（架构/API/前端/schema/任务卡） |

---

## 0. 本文档怎么用（AI Agent 请先读这一节）

本文档按「决策 → 需求 → 规格 → 实施」四段组织。不同角色按需取用：

- **做决策的人**：读 §1（结论摘要）、§2（可行性）、§3（M4 适配性）、§4.2（风险登记册）、§6.2（范围声明与非目标）。
- **写代码的 Agent**：先读 **§6（PRD 需求基线，含每条需求的验收标准与优先级）**，再读 §4（内核规格）、§7（API 契约）、§8（前端规格）、§9（任务卡）、附录 B（代码骨架）、附录 C（规则库）、附录 D（Prompt 契约）。**§9 的任务卡是唯一的开工入口**，按 `M0 → M9` 顺序执行，不要跨卡并行改接口。
- **验收的人**：读 §6.5（Definition of Done）、§4.8（质量指标）与附录 G（基准脚本）、附录 H（决策记录）。
- **接手维护/被要求加功能的人**：先读 **§6.7（变更控制与防需求漂移）**。任何未列入 §6.3 需求表的功能，都属于范围外，需先走 ADR 再动手。

四条硬约束，任何 Agent 不得自行放宽：

1. **不做图片、不做 OCR、不做版面视觉模型**。图片与插图一律按 §6.2 丢弃并登记；扫描件在 S1.5 直接拒收。唯一例外是 §7/§8 里明确标注的「人工复核界面可查看原 PDF 页面」，那是给人眼看的，不参与转换。
2. **`immutable=true` 的块（表格/代码/公式）在任何阶段、任何界面都不得被改写**。Web 控制台允许人工编辑，但编辑走 §7.5 的 `edit` 审计通道并逐块重算 checksum，表格/代码块在 UI 里是**只读围栏**。
3. **依赖版本一经锁定即冻结**（`requirements.lock` / `uv.lock`），任何升级必须重跑 §4.8 的黄金集回归并取得通过记录。
4. **阈值按较高性能（M4 满载）设定，但必须可降档**。任何为「跑得更快」而放宽质量门禁的改动，都要在 §6.7 登记为决策，不得静默修改红线指标。

---

## 1. 结论摘要（TL;DR）

**v1 方案整体可行，工程方向正确，评级 A−；补齐缺口后，在 Mac mini M4 上可以做成一台「日常真的会打开用」的内部文档转换服务，而不只是一条命令行流水线。**

v1 最有价值的三个设计决定，v2 全部保留并强化：

1. **中间件（每步落盘）+ 统一中间态 JSON**。这让离线环境里「转换效果不满意」从一个无法定位的黑盒问题，变成可 diff、可单步重跑、可回滚的工程问题。这是整个方案的地基，不要因为嫌麻烦而跳过。也正因为有这个 IR，§7/§8 的 API 与前端才可能做「块级定位、单步重跑、diff 审阅」这类真正好用的交互——**没有 IR 就没有可交互性**，这是本方案不做成「一个 pandoc 脚本」的根本原因。
2. **解析器与修复器解耦**（S2 只忠实抽取，S4/S5 只操作 JSON）。这一条决定了后续换解析引擎的成本，也决定了回归测试是否可写。
3. **`immutable` + checksum 校验**。表格与代码被 LLM「热心改写」是此类管线最隐蔽、破坏最大的故障模式，v1 用红线指标（篡改率 = 0）来守，非常专业，v2 把它升级为强制的门禁（gate），失败即整文件回滚并进人工队列，并在前端做成「不可编辑围栏」的视觉约束。

v1 需要修正的三个判断：

1. **「文本型 PDF 就不要碰带模型的解析器」在 T8 档成立，在 M4 档不成立**。M4 有 Metal GPU（10 核 GPU + 16 核神经网络引擎 + 120GB/s 统一内存带宽），Docling 支持 Apple Silicon MPS 加速，MinerU 的 pipeline 后端可把 `device-mode` 设为 `mps`，Marker 明确支持 CPU/MPS。也就是说在 M4 上，「模型辅助的结构识别」成本变得可接受，应当作为**可选增强层**提供。但注意：它解决的是**版面/表格结构**，不是图片内容——本项目对图片仍不做任何提取。
2. **「LLM 只输出 patch 不改写」的铁律方向完全正确，但 0.6B 模型档位偏低**。T8 档可继续用 0.6B（且应当默认关闭）；M4 档的**生产默认是 8B Q4 常驻**（`enhanced`，`llm_clients=2`），4B 只作为内存告警时的降档回退。8B 带来的标点/断句正确率收益远大于额外的 2–4 秒延迟，而 32GB 统一内存放得下一个约 6–7GB RSS 的常驻模型。
3. **许可合规的结论因交付形态而改变**。既然确认 **不对外分发、纯内部自用**，PyMuPDF 的 **AGPL-3.0** 义务一般不被触发（AGPL 的传染点在「分发」与「网络服务对外提供」，内网单机自用不在此列），因此 **v2 直接把 PyMuPDF 定为唯一生产后端，不再投入实现 MIT 替代路线**（省 3–5 天）。但保留 `ExtractorBackend` 抽象不删——它现在的用途是「未来若改为对内网其他同事提供 Web 服务时，需要重新评估 AGPL 网络服务条款」的逃生门，这个触发条件写进 §6.7 的变更控制表。

本次新增的两块内容（§7 API、§8 前端）与一个关键提醒：

- **API 采用「作业队列 + SSE 事件流」而非同步返回**。一份 200 页文档转换需要数十秒到数分钟，同步 HTTP 会超时并诱发前端重试风暴；而且 §7.4 的单步重跑、§7.6 的人工复核都必须以「作业 + 事件」为模型才干净。
- **前端的核心不是转换器按钮，而是「审阅与修订」**。经验规律是：这类管线 90% 的产出问题集中在少数文档上，真正的时间消耗在人怎么高效发现并修好它们。所以 §8 把「双栏 diff 审阅器 + 块级定位 + 拒收队列工作台」放在一等位置，而不是做一个上传进度条。
- **M4 的性能红利必须靠服务化才吃得到**：LLM 常驻（省每次加载 2.5GB 模型的冷启动）+ 三段流水并发（§4.9），这两条在纯 CLI 用法里收益有限，在 API 服务里是数量级差异。

M4 适配结论一句话：**Mac mini M4 是这台管线理想的「开发+生产」双用机，不是「低配版替代」。** 它让 v1 里所有被标注为「成本高、建议放到下一迭代」的能力（结构化解析器、标点恢复、更大的修补模型、并发流水）第一次变得可行；同时它带来一批 macOS 独有的工程约束（Gatekeeper、TCC 隐私权限、Homebrew PEP 668、Docker 内无 Metal、Rosetta 陷阱、`ulimit -n`、睡眠策略），这些必须写进 S0，否则会在第一天就卡住。

**同一套代码跑两个平台**是可行的，做法是：一套流水线 + 一份能力开关矩阵（profile），平台差异收敛到 `platform_caps` 一个模块，业务逻辑层禁止出现 `if sys.platform`。

---

## 2. 可行性分析

### 2.1 范围收窄带来的成本坍塌

把范围限定为「文本型 PDF + DOCX」，本质上是把问题从「文档理解」降级为「结构还原」：

| 被排除的高成本组件 | 排除后省下的代价 |
| --- | --- |
| OCR（PaddleOCR / EasyOCR / RapidOCR） | 模型权重 100MB–1GB+、每页 0.3–2s、torch 依赖、内存 1–2GB |
| 版面分析模型（DocLayNet / layoutlmv3 / YOLO 系） | torch + 视觉模型、每页 0.1–0.5s、CPU 上极慢 |
| 表格结构模型（TableFormer） | 500MB+ 权重、显存/内存压力 |
| 公式识别（UniMERNet / pix2tex / CodeFormula） | CodeFormula 单模型约 506MB，且无法用 MPS 加速 |

参考：Docling 官方明确其代码库为 MIT 许可、依赖 DocLayNet 版面分析与 TableFormer 表格结构识别这类专用 AI 模型；其离线模型包 `ds4sd/docling-models` 约 506MB，`CodeFormula` 约 506MB，`DocumentFigureClassifier` 约 16MB，合计约 1.2GB 权重。这组数字就是 v1「在 8GB 机器上不想装 torch」判断的量化依据。

结论：**收窄范围后，T8（8GB / 无 GPU）完全够用**。这是 v1 最重要的正确判断——它没有掉进「PDF 转换 = 一定要上 AI 解析器」的常见陷阱。

### 2.2 架构合理性核验

| v1 设计 | 评价 | 依据 |
| --- | --- | --- |
| 五落盘点流水线（S2→S3→S4→S5→S6） | ✅ 保留 | 离线环境的调试唯一可行解；断点重跑省 10× 时间 |
| 统一中间态 JSON schema | ✅ 保留并扩充（见 §4.3） | 双通道收敛点是解耦的前提 |
| 规则优先、模型兜底 | ✅ 保留 | 规则零幻觉、可解释、可测试；「70–85% 靠规则」的估计符合实践 |
| 编号连续性校验判定标题 | ✅ 保留（关键洞察） | 这是全文档最有价值的一条启发式，能筛掉大部分假标题 |
| LLM 只输出 patch ops | ✅ 保留并强化为门禁 | 直接生成 Markdown 会让表格/代码静默损坏且不可回归 |
| 单文件串行、`max_workers=1` | ⚠️ 仅适用于 T8 | M4 档可三段流水并行（见 §4.9） |
| 页眉页脚用文本指纹去噪 | ✅ 保留 | 跨页 ≥3 次出现 + 边距区，是稳定做法 |
| 50 篇黄金集 | ⚠️ 偏小 | 建议 150–300 篇分层抽样（见 §4.8） |
| 「S5 第二级标点模型放下一迭代」 | ✅ 保留该判断 | `ct-punc` 依赖 torch，文本型 PDF 里真实收益极低 |

### 2.3 关键选型事实核验（截至本文档日期）

| 工具 | 许可 | 目标平台可用性 | v2 定位 |
| --- | --- | --- | --- |
| PyMuPDF (`fitz`) | **AGPL-3.0** 或 Artifex 商业授权 | Win / macOS（含 `universal2` wheel）/ Linux | **唯一生产 PDF 引擎**（已确认内部自用，AGPL 分发义务不触发；对同机房其他同事提供 Web 服务前需重评，见 §6.7 触发器 T-1） |
| pdfplumber / pdfminer.six | **MIT** | 纯 Python，跨平台无差异 | **不实现**（原 AGPL 逃生路线，本次判定为过度设计）。保留接口位，触发 T-1 时再补 |
| pypdf | **BSD** | 纯 Python | 只做元数据/页面操作，不做主抽取 |
| pikepdf | **MPL-2.0** | 基于 QPDF，C++ 预编译 | 读取 PDF **结构树（StructTreeRoot）**；也是「插图对象检测」的辅助手段 |
| pymupdf4llm | AGPL（随 PyMuPDF） | — | 快速基线（打「下限对照」用），不作主力 |
| pandoc | GPL-3.0（作为独立进程调用不传染） | 官方提供 Windows msi 与 macOS pkg | **DOCX 主通道**（AST JSON）；许可风险与 PyMuPDF 不同，进程边界隔离 |
| mammoth.py | **BSD-2** | 纯 Python | DOCX 兜底通道 |
| python-docx | MIT | 纯 Python | 读 `styles.xml` / `numbering.xml` 元数据，交叉校验标题层级 |
| Docling | **MIT（代码库）**，模型许可见原包 | 支持 Apple Silicon **MPS** 加速；Linux/Win 走 CPU/CUDA | **仅 M4 `enhanced` 档**的结构识别增强器 |
| MinerU | AGPL-3.0（其依赖含 PyMuPDF，官方 README 已提示 AGPL 限制） | 支持 Windows/Linux/macOS；pipeline 后端 `--device` 可取 `cpu/cuda/npu/mps` | M4 `enhanced` 档可选，作为 Docling 的对照评测对象 |
| Marker (datalab) | 代码 **Apache-2.0**，权重为修改版 AI Pubs Open Rail-M | 明确支持 GPU / CPU / **MPS** | M4 档候选，**许可最友好**，值得在评测阶段与 Docling 二选一 |
| llama.cpp | MIT | macOS Metal 一等公民 | LLM patch 运行时（M4 推荐，优于 Ollama 一层封装） |
| Ollama | MIT | macOS 原生 | 开发期便利；生产推荐直连 `llama-server` |
| FunASR `ct-punc` | Apache-2.0，依赖 torch | Win/macOS 均可装 | **不做**（除非混入语音转写稿，届时单开支路） |

许可信息来源摘录：Docling 官方说明代码库在 MIT 许可下、单个模型许可见原始包；MinerU 官方 README 的 License Information 一节明确指出其使用 PyMuPDF 实现部分功能，因 AGPL 许可可能对某些使用场景产生限制；Marker 被社区评测列为支持 GPU/CPU/Apple MPS，并采用 Apache-2.0 代码许可 + Open Rail-M 权重许可。

### 2.4 v1 的 10 个缺口（v2 的补强清单）

| # | 缺口 | 后果 | v2 补充 |
| --- | --- | --- | --- |
| 1 | **假文本层 / 编码损坏未检测**。v1 只用「抽出文字量 / 页面面积」阈值判扫描件，但 Type 3 字体、缺 ToUnicode 的 CID 子集字体、双层 PDF（图像上叠一层垃圾 OCR 文本）都能抽出大量「非空但不可读」的字符 | 静默产出乱码 Markdown，且比空文本更晚被发现 | 新增 **S1.5 文本层体检**：私有区码点（U+E000–U+F8FF）、`.notdef` 比例、可打印 CJK/ASCII 覆盖率、单字重复率、字体 ToUnicode 存在性；不达标判 `DEGRADED_TEXT` 拒收 |
| 2 | **未利用 PDF 结构树（Tagged PDF / StructTreeRoot）** | 明明有权威的标题/列表/表格语义，却靠字号启发式猜，白白降低准确率 | 新增 S2-C 通道：若 PDF 带结构树，优先用结构元素映射到 schema（`H1/H2/L/Table/P`），启发式退居校验角色 |
| 3 | **AGPL 合规未评估** | 交付形态不明时是隐患 | 已确认**内部自用不分发** → 结论为不触发，PyMuPDF 定为唯一生产后端，省掉 MIT 替代路线的工作量（ADR-03 已改）；`ExtractorBackend` 抽象保留作逃生门 |
| 4 | **并发与吞吐在 M4 上浪费** | M4 十核只用一核，产能低 4–6× | §4.9 三段流水 + 分片并行；本次要求「按较高性能设定阈值」，故 M4 侧并发/内存阈值全部按满载设定，T8 降为「兼容跑通」档 |
| 5 | **无幂等 / 断点续跑 / 目录级去重** | 内网批量任务中断后要全量重跑 | 内容寻址（sha256 前缀）作为任务 ID，`state.sqlite` 记录阶段状态，重跑天然幂等 |
| 6 | **表格输出格式未定**（v1 存 HTML，但下游可能是 RAG/预览器） | HTML 表格在部分渲染器/RAG 分块里不可读 | `table.format` 可选 `gfm` / `html` / `csv+占位`，按 `profile` 与消费者决定，原文永远保留一份 |
| 7 | **评测集与指标不可计算**（「断句 F1 ≥ 93%」没说怎么算） | 指标无法证伪，回归形同虚设 | §4.8 给出每个指标的**精确定义 + 计算脚本 + 抽样分层法** |
| 8 | **图片/插图处理未定义** | 图文混排文档丢图且无提示，或图片文件名与正文错位 | ~~新增媒体抽取~~ → **本次明确不做**（范围外）。改为：S2 检测插图对象 → 在 IR 里留 `type=image` 的**占位块**（页码+bbox+尺寸+是否有邻近题注），MD 里输出可配置的注释标记，并在报告中汇总「本文丢弃 N 张插图」。既守住范围，又不静默丢信息（见 §6.2） |
| 9 | **未处理 DOCX 特有结构**：脚注/尾注、批注、文本框、SmartArt、域代码（TOC/交叉引用）、多级列表实际层级 | 目录被当正文、列表层级塌陷、批注内容丢失且无提示 | S2-A 补充「OOXML 语义元数据通道」（python-docx 读 `styles.xml`/`numbering.xml`），并在报告里显式列出丢弃项 |
| 10 | **运维视角缺失**：Mac mini 长期批处理的睡眠、权限、日志轮转、磁盘写放大 | 任务半夜停摆、首次运行卡在隐私授权 | §3.4 平台差异清单 + S0 的 `setup_mac.sh` / launchd 配置 |

### 2.5 可行性总评

| 维度 | T8（12500T/8G/无 GPU） | M4 Mac mini（16–32GB，Metal） |
| --- | --- | --- |
| 纯规则管线（L0 + L1 规则修补） | **可行，A** | 可行且快 3–5× |
| LLM patch 层（L3） | **勉强可行**（0.6B、按需启停、必须单文件串行） | **可行，A**（Metal 加速，4B/8B 可用，可常驻） |
| 结构识别模型层（L2，Docling/Marker） | **不可行**（torch + 500MB+ 权重 + 内存挤占） | **可行，B+**（MPS 加速，需按文档类型验收，默认关） |
| 吞吐（100 页常规文档，L0+L3） | 2–5 分钟（v1 估计，量级合理） | 生产档预计 8–20 秒（待实测校准，§3.6） |
| 一次性部署成本 | 半天（wheel + GGUF 拷贝） | 半天（arm64 wheel + 签名/Gatekeeper 处理） |
| 许可风险 | 低（内部自用，AGPL 不分发不触发） | 低；若改为对外提供服务则升为中，见 §6.7 T-1 |
| **服务化与 Web 控制台** | **不建议**（内存不足以同时常驻 LLM 与 uvicorn + 队列） | **可行，A**（`doc2md serve` 常驻 + launchd，§7/§8 即为此设计） |

---

## 3. Mac mini M4 适配性分析

### 3.1 硬件画像

Mac mini（2024，M4）的官方规格：Apple M4 芯片为 **10 核 CPU（4 性能核 + 6 能效核）、10 核 GPU、16 核神经网络引擎、120GB/s 内存带宽**，标配 **16GB 统一内存**，可选 24GB 或 32GB；M4 Pro 版本为 12 核 CPU（8P+4E）/16 核 GPU、**273GB/s 内存带宽**、标配 24GB，可选 48GB/64GB；存储 256GB 起（M4）/512GB 起（M4 Pro），可选配 10Gb 以太网。

与本管线相关的三个要点：

1. **4P+6E 的混合架构**：CPU 密集的单页解析属于可并行短任务，适合丢到 E 核集群；LLM 推理与 tokenizer 预热适合 P 核。macOS/GCD 会自动调度，工程侧只需控制并发度，不必手动绑核。
2. **统一内存 + 120GB/s 带宽**：这是 LLM 与 GPU 模型「可用」的根本原因——没有独立显存墙，Metal 与 CPU 共享同一片内存。也意味着 **模型驻留与文件解析争抢同一份内存预算**，16GB 机型要按 §3.5 分配。
3. **内置 MediaEngine 与本任务无关**：本管线不用视频/图像编解码加速，不必为此选 M4 Pro。

**选型建议**：若这台机器专职跑文档转换，**M4 + 32GB（2TB SSD）** 性价比最好——比 16GB 多出的内存让 Docling/Marker 的 MPS 路径与 8B LLM 可以共存，比 M4 Pro 省下的带宽预算对纯文本管线收益极小。若只做规则 + 小模型 patch（L0/L1 + L3，用 4B），16GB 足够。

### 3.2 与 T8 平台的本质差异

| 维度 | T8 (12500T/8G) | M4 Mac mini | 对方案的影响 |
| --- | --- | --- | --- |
| 加速器 | 无 | 10 核 GPU（Metal）+ 16 核 ANE | **torch CPU → MPS**；Docling/Marker 从「禁用」变「可选」 |
| 内存 | 8GB 系统内存 | 16/24/32GB 统一内存（GPU 可直接用） | LLM 可常驻；并发度从 1 提到 3–5 |
| 带宽 | 双通道 DDR4 ~50GB/s | 120GB/s（M4）/ 273GB/s（M4 Pro） | token 生成速度主要受限于此，M4 上 4B Q4 通常数十 token/s 量级 |
| CPU 核 | 6C12T @2.0GHz | 10C（4P+6E） | 单文件串行不再是唯一选择 |
| 存储 | SATA/NVMe 均可 | **板载焊接 NVMe，不可扩容**；256GB 版偏小 | 中间产物落盘量大 → 必须做 gzip/zstd 与保留策略，或外置雷雳 SSD |
| 系统 | Win11（可任意关防护） | macOS（Gatekeeper / TCC / SIP / 签名） | 部署脚本要处理 quarantine、隐私授权、Python 环境 |
| Docker | 一般能用 | **容器内拿不到 Metal**，跑成纯 CPU 且更慢 | **本管线不要用 Docker Desktop 跑推理**，直接宿主机 venv |
| 便携性 | 一台机器一份环境 | 同一二进制可在 M 系列 Mac 间迁移 | 归档格式（GGUF + arm64 wheel）天然可复用 |

### 3.3 M4 上应当打开的能力（分层解锁）

- **L3 解锁 · LLM patch 常驻 + 升档**（层级定义见附录 I：L0/L1 规则、L2 结构模型、L3 LLM patch）：Metal 后端下 `llama-server` 常驻内存约 3–4GB（8B Q4 量级），换来 patch 质量显著提升；T8 上「用完即退」的做法在 M4 上可改为「常驻 + 空闲 300s 自动退出」。社区在 16GB M4 上以 Ollama 跑 7B 级模型的实测报告为约 42 token/s、整机内存占用约 7GB；llama.cpp 直连通常比 Ollama 更快（社区基准给出 M4 上 15%–22% 的优势），生产路径建议直接用 `llama-server`。这些数字来自社区实测，**上线前必须用附录 G 的脚本在本机重测**。
- **L2 解锁 · 结构识别增强器（可选）**：Docling 支持在 Apple Silicon 上启用 MPS；MinerU 的 pipeline 后端可通过 `magic-pdf.json` 的 `"device-mode": "mps"` 启用（其公式识别不走 MPS，若不需要公式可关闭以提速）；Marker 明确兼容 GPU/CPU/MPS 并可用 `TORCH_DEVICE=mps` 强制指定。三者在文本型 PDF 上的**净收益必须用黄金集实测**——v1 关于「原生 PDF 上提升有限」的判断在 M4 上同样可能成立，解锁 ≠ 默认启用。
- **仍不解锁 · 标点恢复模型（ct-punc）**：技术上可行（torch CPU/MPS），但文本型 PDF 里「整段无标点」的情况极少，**v2.1 仍不启用**（ADR-08），仅当输入源混入语音转写稿时开支路。它不占用 L 层编号，属于候选能力而非层级。
- **并发解锁**：M4 上可采用「抽取 / 规则 / LLM」三段流水 + 多进程分片，详见 §4.9。

### 3.4 macOS 平台差异清单（工程必读，照做即可）

| 项 | 现象 | 处置 |
| --- | --- | --- |
| **架构** | 同一条 `python3` 可能是 arm64 或 x86_64（Rosetta 终端） | 脚本首行 `arch=$(uname -m)` 断言等于 `arm64`；Python 内用 `sysconfig.get_platform()` 校验；装错架构会静默回退 CPU、torch 报 `Illegal instruction` 或找不到 MPS |
| **torch wheel** | 通用 x86 wheel 触发 Rosetta，Metal 加速失效 | 必须装 `macosx_*_arm64` wheel；验证 `torch.backends.mps.is_available()` 返回 `True` 才允许启用 L2 结构模型 |
| **PyMuPDF wheel** | PyMuPDF 官方提供 macOS `universal2` 预编译 wheel | 离线下载时 `--platform macosx_11_0_universal2`，一份包覆盖 arm64/Intel |
| **Gatekeeper / quarantine** | 下载的 `llama-server`、pandoc pkg 首次执行被拦「无法验证开发者」 | `xattr -dr com.apple.quarantine <path>` 或系统设置→隐私与安全性→「仍要打开」；批量部署走 MDM/脚本 |
| **TCC 隐私权限** | 读取 `~/Desktop`、`~/Documents`、外置卷、网络盘时进程被静默拒绝 | 工作目录放 `~/doc2md`（不受 TCC 保护）；或给终端/launchd 授予「完全磁盘访问」 |
| **PEP 668** | Homebrew Python 是 externally-managed，`pip install` 直接报错 | 一律用 venv（`python3.12 -m venv`）或 `uv venv`；禁止 `--break-system-packages` |
| **Docker** | Docker Desktop 是 VM，**不透出 Metal** | 推理链路不容器化；需要隔离时用 `venv` + `launchd` |
| **ulimit** | macOS 默认 `ulimit -n` = 256，批量打开大量文件/媒体会报 `Too many open files` | `launchd` plist 里 `SoftResourceLimits.NumberOfFiles=4096`，或代码里显式及时 `close` 并用 `with` |
| **睡眠** | 空闲后系统睡眠，长任务停摆 | `pmset -c sleep 0 disablesleep 1`（接电源常驻）或 `caffeinate -is <cmd>` 包裹 |
| **文件系统大小写不敏感** | 默认 APFS 大小写不敏感：`A.md` 与 `a.md` 冲突 | 所有派生文件名一律小写 + hash 前缀；跨平台归档必须用 zip 校验一遍 |
| **符号链接与冒烟** | macOS 的 `lsapp`/Spotlight 会索引输出目录造成 IO 抖动 | 输出目录加入 Spotlight 隐私排除列表 |
| **签名与 notarization** | 自研 CLI 无签名不影响本机使用，但分发到其他 Mac 会被拦 | 内网自用可 ad-hoc 签名 `codesign -s - <bin>`；跨机分发考虑 Developer ID |
| **时区/locale** | PDF 内日期与 `date` 混用导致 diff 噪声 | 全流程统一 UTC + `TZ=UTC`；日志时间戳 ISO8601 |
| **换行符** | macOS 是 LF；若管线读 Windows 导出的 CJK 文本会混 CRLF | S1 归一化：所有文本统一 LF，`chardet`/`charset-normalizer` 探测编码（PDF/DOCX 内部 UTF-16/GBK 混合场景常见） |

### 3.5 内存预算（按较高性能档位设定：以 M4 32GB 满载为生产基线）

**本次确定：阈值按较高性能设定。** 即生产配置默认取 **M4 + 32GB**、`profile=enhanced`、并发满载；16GB 机型与 T8 只作为「兼容降档」运行，不作为调参依据。Metal 下 GPU 与 CPU 共用同一份统一内存，所以「模型驻留」和「文档处理」是同一个预算池，必须排他使用。

| 场景 | macOS 基线 | 管线主进程 | LLM 驻留 | 可选 L2 模型 | 峰值合计 | 32GB 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| **P0 · L0 纯规则** | 2.0–3.0GB | 0.3–0.8GB | 0 | 0 | ≤ 3.8GB | 极安全，可 6 路并发 |
| **P1 · L0/L1+L3（4B Q4 常驻）** | 2.0–3.0GB | 0.4–1.0GB | 3.0–3.5GB | 0 | ≤ 7.5GB | 安全（16GB 档的推荐上限） |
| **P2 · L0/L1+L3（8B Q4 常驻）＝ §4.5 生产配置** | 2.0–3.0GB | 1.5–3.0GB（`workers=5`，L2 默认 `off`） | 5.5–7.0GB | 0 | **≤ 13GB** | 生产基线，硬上限设 **20GB** |
| **P3 · 满载（L3+L2 同池，仅结构模型实验期）** | 2.0–3.0GB | 1.5–3.0GB（`workers=5`） | 5.5–7.0GB | 3.0–5.0GB | ≤ 18GB | 仍在 20GB 内，但 L2 必须排他加载（规则 2） |
| **P4 · 超大文档（>300 页）分片** | 2.0–3.0GB | 2.0–3.5GB | 5.5–7.0GB | 0 | ≤ 13.5GB | 分片阈值必须生效 |

四条规则（生产阈值，直接写进 `config/enhanced.toml`）：

1. **内存硬上限 20GB / 预留 12GB 给 OS 与突发**：`memory_pressure` 持续 `warn` 超过 30s → 自动把 `workers` 降一档并暂停接单（返回 503），队列不丢；连续 5 分钟 `warn` → `degrade_to_L0`。**宁可质量下降，不可 OOM 杀批次**。
2. **L3 常驻但 L2 排他**：8B LLM（L3）常驻，省冷启动、收益最大；L2（Docling/Marker）按批次加载/释放，**不并发两个 L2 实例**，也不与 L3 同时做「双模型预加载」。
3. **单作业内存配额**：`per_job_mem_mb = 2048`（页级流式抽取，超过即分片）；单文件解析内存超配额 → 自动按章节切，不报错。
4. **256GB 存储机型注意写放大**：中间产物 zstd(3)，5000 篇的 S2/S3 产物从 ~40GB 压到 ~6GB；磁盘门禁 `min_free_gb = 20`。

### 3.6 吞吐估算（生产 SLA 依据，必须实测校准）

**以下数字是设计输入值与 SLA 目标，不是承诺值。** 上线前用附录 G 的基准脚本在真实语料上重测并写进 ADR-07。生产档 = M4 32GB / `enhanced` / `workers=5`。

| 阶段 | T8（i5-12500T，兼容档） | M4 单核等价 | **M4 `enhanced` 满载（生产）** |
| --- | --- | --- | --- |
| S1 探测 + S1.5 体检 | 5–20 页/s | 15–60 页/s | **60–200 页/s** |
| S2-B PyMuPDF 抽块 | 15–40 页/s | 30–90 页/s | **120–350 页/s** |
| S2-A pandoc AST（DOCX） | 1–5 万字符/s | 3–10 万字符/s | 近线性 |
| S4 规则重建 | 5–15 页/s | 15–40 页/s | **60–180 页/s** |
| S5-L3 LLM patch（8B/Metal） | 1–3 页/s（0.6B） | 10–25 页/s（4B） | **25–50 页/s** |
| S6 渲染 + 校验 | 20–50 页/s | 60–150 页/s | 高 |
| **单份 100 页常规文档端到端** | 2–5 分钟 | 25–60 秒 | **8–20 秒** |
| **日产能（8 小时稳定运行）** | ~1000–3000 页 | ~3 万页（仅规则 L0/L1） | **~10 万页（含 L3）** |

**由「日均页数不定」推导出的容量设计原则**（写进 PRD，Agent 照此实现，不要按固定日量做假设）：

1. **按峰值而非均值设计**：日均不定意味着会有「一天灌几千篇」的突发。系统必须能在 8 小时内吃下 ≥ 10 万页峰值，靠的是队列削峰而非并行堆机器。
2. **入队不设硬上限，处理按背压**：`max_queue_len` 默认无限（磁盘够就收），`accept` 接口永远快速返回 202；只有当 §3.5 内存门禁触发时才 `503 + Retry-After`。
3. **小文件不能被大文件饿死**：队列按「页数分桶 + 加权公平调度」（小/中/大三桶），避免一批 500 页文档把交互式的小请求排在后面——这条是「日常使用体验」的关键，见 §8.4。
4. **可预测的等待时间必须暴露给用户**：作业排队时返回 `queue_position` 与 `eta_seconds`（基于最近 20 个作业的 `throughput_pps` 滑动均值估计），前端展示。日均不定的环境里，用户能忍「慢」，不能忍「不知道要等多久」。

### 3.7 双平台统一策略（一份代码，两档能力）

```
core/              # 纯逻辑：schema、规则、渲染、校验 —— 禁止 sys.platform 判断，禁止依赖 Web 框架
platform_caps/     # 唯一允许平台分叉的地方
  base.py          # CapProbe: has_metal, has_cuda, mem_total, cores, disk_free
  profiles.py      # minimal / standard / enhanced 三档 profile 的解析与裁剪
backends/
  pdf_pymupdf.py   # PyMuPDF 实现（唯一生产 PDF 后端）
  pdf_alt.py       # 接口占位（ExtractorBackend 协议）；MIT 替代路线，仅在触发 §6.7 T-1 时实现
  docx_pandoc.py   # pandoc AST 通道（默认）
  docx_mammoth.py  # BSD 兜底通道
  struct_layout.py # L2: Docling / Marker 适配器（仅 enhanced，默认关闭）
runtime/
  llm_server.py    # llama-server 生命周期（Metal on macOS / CPU on T8）
  scheduler.py     # 三段流水 + 分桶公平队列 + 内存门禁（§4.9）
service/           # §7 API 层：只做编排与契约，不含业务逻辑
  app.py  routers/  schemas/  events.py  auth.py
web/               # §8 前端：独立构建产物，静态文件由 service 挂载
```

**分层纪律（API 与前端落地时最容易出事的地方）**：`core` 不得 `import fastapi`；`service` 不得直接读写 `work/` 目录里的文件（一律经 `core/ir_io` 与 `runtime/scheduler`）；`web` 不得假设后端是本地（所有路径走 API，不拼文件系统路径）。这样 CLI（§4.6）、API（§7）、未来的批处理脚本三个入口共用同一内核，行为一致。

profile 决策表（`CapProbe` 自动判定，可被 `--profile` / API 参数覆盖）。**生产默认 = `enhanced`（本次按较高性能设定）**；低档只作为兼容与降级路径存在。

| profile | 触发条件 | 启用的能力 | 并发 | LLM | 定位 |
| --- | --- | --- | --- | --- | --- |
| `minimal` | 内存 < 12GB **或** 无 Metal/CUDA | L0 规则 + 可选 0.6B LLM（按需启停） | `workers=1` | 默认关 | **兼容档**（T8），只保证跑通 |
| `standard` | 内存 14–21GB | L0/L1 规则 + L3（4B 常驻） | `workers=2–3` | 4B | 16GB 机型的日常档 |
| **`enhanced`** | 内存 ≥ 22GB 且 MPS 可用 | L0/L1 规则 + L3（8B 常驻）+ L2 可选（Docling/Marker，MPS） | `workers=5` | 8B | **生产基线（Mac mini M4 32GB）** |

---

## 4. 完善后的方案（工程规格）

### 4.1 架构总览（v2.1，内核 S0–S7 + 服务层 + 前端层）

```
【接入层】  CLI(§4.6)   │   HTTP API(§7)   │   Web 控制台(§8)
                        └────────┬─────────┘
                                 │  同一内核，三个入口
【编排层】  runtime/scheduler：作业队列(分桶公平) · 三段流水 · 内存/磁盘门禁 · 事件广播
                                 │
【内核层】  S1 探测分流 → S1.5 文本层体检 ─┬→ 拒收队列(reject)
                                          │
            ┌───────────────┬─────────────┼──────────────┐
            ▼               ▼             ▼              ▼
      S2-A DOCX→pandoc  S2-B PDF→PyMuPDF  S2-C PDF→结构树  S2-D 插图对象检测
          AST JSON         逐块+raw_lines     (Tagged PDF)     (仅登记，不提取)
            └───────────────┴─────────────┴──────────────┘
                                 ▼
                  【S3】统一中间态 IR JSON（唯一契约）
                                 ▼
                  【S4】结构重建（去噪/标题层级/列表/先合并/再切分/表格隔离）
                                 ▼
                  【S5】语义修补（L1 规则 → L3 LLM patch →〔可选 L2 结构模型〕）
                                 ▼
                  【S6】渲染 Markdown + 校验（immutable checksum + 红线门禁）
                                 ▼
                  【S7】下游适配与发布（RAG 分块、front-matter、报表、归档）
```

新增/变更点：

- **三个入口共用一个内核**（§3.7 分层纪律）。API 与 Web 不是「另一套实现」，只是 `core` 的远程调用面。这条不守住，后期一定会出现「命令行能跑、Web 上结果不一样」的经典分裂。
- **S1.5 文本层体检**：解决「假文本层」静默失败（缺口 #1）。
- **S2-C 结构树通道**：Tagged PDF 的权威语义优先于字号启发式（缺口 #2）。
- **S2-D 改为插图对象检测（不做提取）**：只统计、定位、在 IR 里留占位块并在报告里汇总「丢弃 N 张插图」，**不落盘图片、不做 OCR**（缺口 #8 的新处置，符合本次范围决定）。
- **S5 内部三小层**：L1 规则 → L3 LLM patch →〔可选 L2 结构模型，默认关〕，全部按 profile 裁剪。
- **S7**：把「Markdown 不只是给人看，还给 RAG/搜索/预览看」显式化，并承载 §8 的导出格式。

### 4.2 风险登记册

| ID | 风险 | 概率 | 影响 | 缓解 | 责任阶段 |
| --- | --- | --- | --- | --- | --- |
| R1 | 交付形态变化触发 PyMuPDF AGPL 网络服务条款 | 低（当前内部自用） | 高 | 内部自用不触发；`ExtractorBackend` 保留接口位；§6.7 触发器 T-1 监控「对外/跨部门提供服务」 | S2 |
| R2 | 假文本层产出乱码 | 高 | 高 | S1.5 门禁，阈值可配 | S1.5 |
| R3 | LLM 改写表格/代码 | 高 | 极高 | patch-only + checksum + 温度 0 + 回滚 | S5/S6 |
| R4 | 跨页表格被腰斩 | 高 | 中 | 专列可开关规则；识别失败则保持原样并报告 | S4 |
| R5 | 双栏/多栏阅读顺序错 | 中 | 高 | x0 聚类 + 栏序，报告里可视化 diff | S2/S4 |
| R6 | 内存超预算（M4 16GB） | 中 | 高 | 300 页分片、串行队列、`degrade_to_L0` | 全局 |
| R7 | macOS 架构/Rosetta 装错 | 高（首装） | 高 | `setup_mac.sh` 断言 + MPS 自检 | S0 |
| R8 | 依赖升级导致「修好 A 类、搞坏 B 类」 | 中 | 极高 | lockfile 冻结 + 黄金集回归门禁 | S0 |
| R9 | 硬换行合并把两段并成一段 | 中 | 中 | 先合后切顺序固定 + 保守合并 + 抽检 | S4 |
| R10 | 磁盘写满（中间产物） | 中 | 中 | zstd 压缩 + 保留策略 + `disk_free` 门禁 | S7 |
| R11 | pandoc 解析损坏 docx 崩溃 | 低 | 中 | 子进程隔离 + mammoth 兜底 | S2-A |
| R12 | 中文全半角/标点归一化引入语义变化 | 低 | 中 | 白名单 + 只做「加空格/统一全角标点」两件事 | S5-L1 |

### 4.3 统一中间态 Schema v2（IR）

**这是全系统唯一契约。** 所有阶段只读写它，解析器差异、修复策略、渲染输出都不得泄漏到别的层。

```json
{
  "ir_version": "2.0",
  "meta": {
    "doc_id": "sha256:1a2b...（原文内容 hash，任务唯一标识）",
    "source_path": "in/a.pdf",
    "source_name": "a.pdf",
    "ext": "pdf",
    "channel": "pdf-pymupdf | pdf-structtree | docx-pandoc | docx-mammoth",
    "backend": "pymupdf-1.24.x",
    "pages": 42,
    "lang_hint": "zh-Hans",
    "produced_by": "s2b@<git-sha>",
    "ts_utc": "2026-10-13T02:11:09Z",
    "caps": { "profile": "standard", "metal": true },
    "flags": ["multi_column", "has_footnotes", "tagged_pdf"]
  },
  "stats": {
    "text_layer_score": 0.982,
    "reject_reason": null,
    "warnings": [ { "code": "W1042", "msg": "第 7 页跨页表格未合并", "block_ids": ["b0088"] } ]
  },
  "headings": [ { "id": "b0001", "level": 1, "number": "1", "text": "概述" } ],
  "blocks": [
    {
      "id": "b0001",
      "type": "heading | paragraph | list | list_item | table | code | image | caption | quote | footnote | ref | pagebreak | furniture | raw",
      "level": null,
      "text": "第一章 概述",
      "children": [],
      "bbox": [40, 120, 300, 142],
      "page": 1,
      "column": 0,
      "indent_x": 40,
      "raw_lines": ["第一章 概述"],
      "line_kinds": ["title"],
      "font": { "name": "SimHei", "size": 16.0, "bold": true, "mono": false },
      "style_src": "docx-outlineLvl | pdf-structtree | heuristic",
      "flags": { "numbered": true, "number": "第一章", "unclosed_pair": false },
      "provenance": { "extracted_by": "s2b", "modified_by": ["s4:heading_levels"], "llm_applied": false },
      "immutable": false,
      "confidence": 0.93
    },
    {
      "id": "b0003",
      "type": "table",
      "format": "gfm | html | csv+placeholder",
      "content": "<table>…原样…</table>",
      "rows": 8, "cols": 4,
      "checksum": "sha1:...",
      "immutable": true,
      "page": 3
    }
  ]
}
```

字段约定（Agent 实现时逐条对齐）：

1. `blocks[].id` 在整个生命周期内**永不改变、永不复用**；合并块时新块保留被吸收块的 `absorbed_ids` 列表。
2. `type=furniture` 的块在 S4 被物理删除（不进入渲染），但保留在 S4 产物里以便复盘。
3. `immutable=true` 的块：S4/S5 **禁止任何修改**（包括空白、标点、大小写）；S6 渲染前必须复算 `checksum`。
4. `raw_lines` 是断句合并的唯一依据，**任何阶段不得丢弃或就地改写**；`text` 是 `raw_lines` 合并后的视图。
5. `style_src` 标明层级判定来源，`confidence < 0.7` 的标题块必须进人工复核清单。
6. `headings[]` 是 `blocks` 的派生索引，S4 结束时重建；渲染目录/分块依赖它。
7. 未知字段解析策略：宽松读取、严格写出（`extra=forbid` 只在测试里用）。
8. **`type=image` 是「登记块」而非「内容块」**：本项目不提取图片，`image` 块只带 `page` / `bbox` / `width` / `height` / `has_nearby_caption`（布尔）与可选的 `caption_text`（取自邻近「图 x」文字块，是本来就有的文本层内容，不算 OCR），**不允许出现 `src` / `data` / `file` 等指向落盘图片的字段**。渲染期按 `s2.image_placeholder` 决定输出 `<!-- 图 3（已丢弃，原位于 p7） -->` 或完全省略；`stats.dropped_images` 记录总数并进报告（§6.2）。

### 4.4 目录结构与产物保留策略

```
~/doc2md/
  project/
    core/  platform_caps/  backends/  rules/  render/  cli/  tests/
    runtime/          # scheduler.py（队列+流水+门禁）, llm_server.py
    service/          # §7 API：app.py routers/ schemas/ events.py auth.py
    web/              # §8 前端源码（构建产物 dist/ 由 service 静态挂载）
    pyproject.toml  requirements.lock
    config/
      base.toml  profile.minimal.toml  profile.standard.toml profile.enhanced.toml
      api.toml        # 监听地址、token、CORS、上传上限、SSE 心跳
  vendor/
    bin/            # pandoc, llama-server, soffice（Mac 为 .app/universal 二进制）
    models/
      llm/          # qwen3-8b-q4_k_m.gguf（生产默认）, qwen3-4b / qwen3-0.6b（降档）
      layout/       # docling / surya 权重（仅 enhanced 且经 A/B 转正后下载）
    wheels/         # 离线 wheelhouse（win_amd64 / macosx_arm64 / universal2 分目录）
  work/             # 运行时，可整体删除重建
    in/  s1/  s2/  s3/  s4/  s5/  out/  reject/  logs/  tmp/
    state.sqlite    # 作业状态机 + 编辑审计流水（幂等续跑，§7.5）
    golden/         # 黄金集：src/ expected/ manifest.csv
  reports/          # 汇总报表与回归结果（保留全部历史）
```

保留策略（`config/base.toml`）：S2/S3 产物 `zstd(3)`，保留 7 天；S4/S5 保留 30 天；`out/` 永久；`golden/` 永久；`reject/` 保留 90 天（供复盘与重投）。磁盘余量 < 20GB 时新作业拒绝入队（门禁，§3.5 规则 4）。**注意：`work/` 下没有 `media/` 目录——本版本不提取图片（§6.2）。**

### 4.5 配置样例（`config/base.toml` + `profile.enhanced.toml` 生产覆盖，节选）

**本文件里的数值即「生产阈值」**，按较高性能档设定（§3.5/§3.6）。Agent 落地时以此为默认值，不要自行调低。

```toml
[run]
profile   = "enhanced"      # 生产默认；auto 会按 CapProbe 裁剪
workers   = 5               # 抽取/规则段并发；minimal=1, standard=3
llm_clients = 2             # 共享一个 llama-server 的客户端数
max_pages_per_task = 300    # 超过则按章节切分
per_job_mem_mb = 2048       # 单作业内存配额，超则再分片
mem_ceiling_gb = 20         # 全局硬上限（§3.5 规则 1）
resume    = true
log_level = "INFO"

[gate]                      # 资源门禁 → API 侧表现为 503 + Retry-After
memory_pressure_warn_s = 30 # 持续 warn 30s → 降一档 workers 并暂停接单
memory_pressure_degrade_s = 300  # 持续 5min → degrade_to_L0
min_free_gb = 20

[s1]
trusted_ext = false                # 只看 magic，不信任扩展名
legacy_doc  = "convert"            # .doc/.wps -> .docx via LibreOffice；或 "reject"
max_mb      = 500                  # 高性能档放宽单文件上限（T8 兼容档设 200）

[s1_5]                             # 文本层体检（缺口 #1）
enabled = true
min_glyph_coverage = 0.85          # CJK+ASCII 可打印字符占比下限
max_pua_ratio      = 0.05          # U+E000-F8FF 私有区比例上限
max_notdef_ratio   = 0.02
max_single_glyph_repeat = 0.30     # 同一字符反复出现 = 缺 ToUnicode
sample_pages = [0, "mid", -1]
on_fail = "reject"                 # 一律拒收；本项目不做 OCR，无 degrade_to_ocr_queue

[s2]
pdf_backend = "pymupdf"            # 唯一生产后端；pdf_alt 仅在触发 §6.7 T-1 时启用
prefer_struct_tree = true          # Tagged PDF 优先（缺口 #2）
detect_images = true               # 仅检测/登记，不提取（§6.2）
image_placeholder = "comment"      # none | comment | ref —— MD 里的插图占位写法

[s4]
drop_furniture = true
heading_numbering = ["cn_chapter", "dot_decimal", "cn_paren"]
heading_continuity_check = true    # 跳号即降级为正文（v1 最有价值的规则）
merge_hard_break = true            # 先合
split_glued_paragraphs = true      # 后切（顺序不可颠倒）
table_output = "html"              # gfm | html | csv+placeholder
```

`profile.enhanced.toml` 只写差异项（`[run] workers=5, llm_model="qwen3-8b"`、`[s5] struct_model="off"` 等）；`minimal` 档把 `workers` 打回 1、`llm="off"`、`max_mb=200`。**profile 之间的差异必须只有这些数字和开关，不允许出现「不同 profile 走不同代码路径」**——否则回归测试覆盖不到，一定会出线上事故。

```toml
[s5]
rules = true
llm = "patch"                      # off | patch（唯一支持的模式，禁止让 LLM 直接生成 MD）
llm_model = "qwen3-8b"             # minimal=0.6B(默认关) / standard=4B / enhanced=8B
llm_window = 800                   # 滑窗字数（高性能档可放大）
llm_overlap = 100
llm_timeout_s = 30                 # 超时 = 跳过并记录 W3001，绝不放宽
llm_temperature = 0.0
llm_low_conf_threshold = 0.7       # 只有低于此置信度的块才送 LLM，控成本
struct_model = "off"               # off | docling | marker（仅 enhanced，且 A/B 转正后才开）

[s6]
enforce_immutable = true           # 校验失败 → 整文件回滚 + 进人工队列 + exit(20)
emit_html_diff = true

[api]                              # §7
bind = "127.0.0.1:8765"            # 内网自用默认只监听本机/内网段，不暴露公网
max_upload_mb = 500
queue_persist = true               # 重启后队列恢复
sse_heartbeat_s = 15
```

### 4.6 CLI 契约（Agent 按此实现，不要自创接口）

```
doc2md run   --in DIR|FILE --out DIR [--profile X] [--workers N] [--only-ids ...] [--dry-run]
doc2md stage --from s3 --to s5 --ids <file.txt>   # 单步重跑（调试核心能力）
doc2md inspect --id <doc_id> [--block b0042]      # 打印 IR 片段与 diff
doc2md check   --golden work/golden               # 黄金集回归，输出指标表 + 是否过红线
doc2md bench   --corpus DIR [--stages s2,s4,s5]   # 附录 G 吞吐基准
doc2md doctor                                     # 平台自检：架构/MPS/ulimit/TCC/内存/磁盘
doc2md serve-llm --model qwen3-8b [--start|--stop|--status]
doc2md serve      [--bind 127.0.0.1:8765]         # 启动 §7 API + 挂载 §8 Web 控制台
doc2md serve status|reload|token                  # 服务管理；token 生成/吊销
doc2md queue      [--list|--retry-failed|--clear-done]   # 队列运维（不依赖前端）
doc2md export     --id <doc_id> --fmt md|md-plain|html|json-ir|chunks|csv  # §7.7 导出
```

CLI 与 API 是同一内核的两个门面：**`doc2md serve` 起来后，CLI 的 `run` 会自动改为向本地服务提交作业**（除非 `--no-server`），这样「手工跑一篇」和「前端点一下」的行为完全一致，避免两套逻辑漂移。

**退出码约定**（供调度与 CI 判定）：

| 码 | 含义 |
| --- | --- |
| 0 | 全部成功 |
| 10 | 部分文件被拒收（`reject` 队列非空），其余成功 |
| 20 | 存在 immutable checksum 违规（红线） |
| 30 | LLM 超时/不可用导致跳过（未达红线，可重跑） |
| 40 | 资源门禁失败（内存/磁盘不足） |
| 50 | IR schema 校验失败（代码 bug，必须修） |
| 90+ | 环境/依赖异常 |

日志：结构化 JSON lines 写 `work/logs/`，每行 `{ts, level, stage, doc_id, block_id, code, msg, dur_ms}`；`code` 是稳定的机器可读标识（如 `W1042`, `E2003`），**报表和测试断言只依赖 `code`，不依赖 msg 文本**。

### 4.7 LLM 层契约（L3）

沿用 v1 的「只出 patch」设计，补充三条可实现的细节：

**请求**（`/v1/chat/completions`，`temperature=0`，`response_format=json_object`，关思维链）：

```json
{
  "block_id": "b0017",
  "task": "split_or_punct | heading_level",
  "heading_path": ["第三章 实施", "3.2 部署"],
  "text": "…600 字滑窗…",
  "candidates": [{ "pos": 42, "why": "unclosed_quote" }]
}
```

**响应必须通过 5 道校验才能应用，任一失败即丢弃该 op 并记 `W3xxx`**：

1. JSON 可解析且 `block_id` 与请求一致；
2. `pos` 越界检查（0 ≤ pos < len(text)，UTF-8 字符索引，非字节）；
3. op 白名单：仅允许 `insert_punct` / `split_at` / `set_level` / `merge_lines`；出现 `replace_text`、`delete` 一律拒绝；
4. 目标块 `immutable == false`；
5. 单请求 op 数量上限（默认 8，超出视为失控）。

**模型档位建议**：

| 档位 | 模型 | T8 | M4 16GB | M4 32GB | 说明 |
| --- | --- | --- | --- | --- | --- |
| nano | Qwen3-0.6B Q8_0（~0.75GB） | 可用（按需启停） | 可用 | 可用 | 只处理明显歧义，预期收益 5–10% |
| base | Qwen3-4B Q4_K_S（~2.5GB） | 不推荐 | **推荐默认** | 降档回退 | 断句/层级性价比最高，16GB 机型的主力档 |
| plus | Qwen3-8B Q4_K_M（~5GB） | 不可行 | 可用（观察 swap，不建议常驻） | **生产默认** | 复杂公文、层级判定准确率明显更好；按较高性能设定阈值即取此档 |

**成本模型（用于决定是否值得开 L3）**：L3 只对 S4 打了 `low_confidence` 标记的块调用。经验规律是「被调用块占比 10–20%」，若实测超过 40%，说明 S4 规则没做够，**优先补规则而不是加模型**。

### 4.8 质量指标与评测（可计算，可门禁）

**黄金集**（`work/golden/`）：分层抽样 **150–300 篇**（v1 的 50 篇不足，方差太大），分层维度：

| 维度 | 分层 |
| --- | --- |
| 来源类型 | 合同 / 制度公文 / 技术手册 / 论文 / 会议纪要 / 混合 |
| 版式 | 单栏 / 双栏 / 带表格 / 带脚注 / 跨页表 / 有无结构树 |
| DOCX 特征 | 真样式标题 / 伪样式标题（加粗代替）/ 多级列表 / 文本框 |
| 规模 | 1–5 页 / 6–50 页 / 51–200 页 / 200+ 页 |
| 语言 | 纯中 / 中英混 / 纯英 |

每篇配 `expected/` 黄金 Markdown + `manifest.csv`（doc_id、层标签、红线权重、已知缺陷标注）。**标注成本最高但一次性；没有黄金集就不要开工 S4**。

**指标定义**（`doc2md check` 输出，全部有确定算法）：

| 指标 | 定义 | 红线 |
| --- | --- | --- |
| `immutable_tamper_rate` | 违规 checksum 块数 / immutable 块总数 | **必须 = 0** |
| `heading_precision/recall/F1` | 按 (level, normalized_text) 与黄金对齐；normalized = 去空白 + 全角转半角 | F1 ≥ 0.90 |
| `heading_level_accuracy` | 判定层级与黄金层级一致率（仅对有标题的篇） | ≥ 0.88 |
| `table_struct_similarity` | 单元格集合 Jaccard + 行列数一致；不做文本 diff | ≥ 0.95 |
| `sentence_boundary_F1` | 与黄金 Markdown 逐段比较句子切分点集合（正则切句后取位置） | ≥ 0.93 |
| `char_similarity` | 去噪后正文字符级 `difflib.SequenceMatcher` 比率 | ≥ 0.97 |
| `junk_rate` | 输出中私有区字符/`.notdef`/孤立页码残留数 | ≤ 0.1% |
| `reject_recall` | 应拒收文件（扫描/乱码）中被正确拒收的比例 | ≥ 0.98 |
| `throughput_pps` | 页/秒，按 profile 记录 | 基线 −15% 内 |

**门禁规则**：任一红线未达 → CI 失败；连续 2 次同指标劣化 → 冻结对应规则改动，回滚到上一个通过的 git tag。**任何 PR 必须附 `doc2md check` 的 JSON 结果**，这是本项目的唯一「测试通过」证据。

### 4.9 并发、幂等与状态机

**任务状态机**（`work/state.sqlite`，表 `tasks(doc_id, path, stage, status, err_code, ts, dur_ms)`）：

```
queued → s1 → s1_5 → s2 → s3 → s4 → s5 → s6 → s7 → done
                                        ↘ rejected（S1/S1.5 门禁）
                                        ↘ failed(err_code) → 重试队列（同 code 计数）
```

- **幂等键**：`doc_id = sha256(文件内容)`。同一内容重复投递不重跑；同内容改名重跑只走 S1（复用 S2–S5 产物）。
- **断点续跑**：每阶段产物写 `tmp/` 后 `os.rename` 原子落盘 + 更新 DB；崩溃后 `--resume` 从 DB 记录的下一个 `pending` 阶段继续。

**并发模型（两平台分化，生产档取高值）**：

- T8（兼容档）：`workers=1`，单进程串行，LLM 按需启停。**不要尝试并发，8GB 下会 swap。**
- M4（生产档 `enhanced`）：三段流水（生产者-消费者），阶段间用有界队列（`maxsize = 2×workers`）形成背压：

```
[抽取进程池 x N=5] → s3 → [规则进程 x M=5] → s5 → [LLM 客户端 x K=2] → [渲染 x 1]
```

  - **N=5、M=5、K=2**（K 受限于单点 `llama-server` 的 Metal 吞吐，堆更多客户端只会排队）。
  - **队列调度必须公平**：按页数分小/中/大三桶加权轮转（§3.6 原则 3），避免大文档饿死交互式小请求。这条由 `runtime/scheduler.py` 统一实现，CLI 与 API 共用。
  - **PyMuPDF 限制**：MuPDF 上下文非线程安全 → **一个 `fitz.Document` 只在创建它的进程内使用**，禁止跨进程传对象；每进程独立 `open`/`close`。多进程而非多线程是硬性要求。
  - `enhanced` profile 下 L2 结构识别（Docling/Marker）单独一批队列，与 L3 LLM **不并行**（内存争抢，§3.5 规则 2）。
  - **服务层不得自起并发**：`service/` 只能提交作业给 `scheduler`，永远不要自己开线程池跑转换（§3.7 分层纪律）。

### 4.10 安全、许可与运行合规

1. **全程离线**：所有出站网络调用默认禁止。代码里加「离线断言」——运行时 monkeypatch `socket.socket` 抛异常（除 `127.0.0.1` / `localhost`）。这条测试要进 CI，防止某天有人偷偷加了 HuggingFace 在线下载。
2. **许可结论（已按「内部自用、不对外分发」定档）**：

| 事实 | 结论 |
| --- | --- |
| PyMuPDF 为 **AGPL-3.0**（或向 Artifex 购买商业授权） | 内部自用、不分发、不做对外网络服务 → AGPL 的分发/网络服务义务一般不触发，**可直接用于生产** |
| 本项目是否会「把工具交付给第三方 / 作为 SaaS 对外」 | **明确不会**（本次已确认）→ 不投入实现 MIT 替代后端，省 3–5 天 |
| 若未来对内网**其他部门/其他同事**开放 Web 服务 | 属于新的使用形态，需重评 AGPL §13 网络服务条款 → 触发 §6.7 **T-1**，届时再实现 `backends/pdf_alt.py`（pdfplumber/pdfminer.six 为 MIT）或购商业授权 |
| pandoc（GPL-3.0）作为**独立进程**调用 | 进程边界隔离，不传染；mammoth 为 BSD-2，Docling 代码库为 MIT |

   **实现要求**：`doc2md license-check` 子命令扫描依赖树并输出 `vendor/licenses.txt`，每次发布前必跑，并把结论写进发布说明。

3. **模型许可**：LLM GGUF（Qwen 系多为 Apache-2.0，仍需逐个核对模型页）、Marker 权重（修改版 AI Pubs Open Rail-M）、MinerU（含 AGPL 依赖）。`enhanced` profile 引入 L2 权重前，必须在 ADR 里记录许可结论。
4. **服务安全（新增，因为现在有 HTTP 端口）**：默认只绑 `127.0.0.1`；如需内网访问，绑内网段 + **强制 Bearer token**（`doc2md serve token` 生成），无 TLS 时不得跨网段暴露；上传文件先落 `work/tmp` 并做 magic 校验后再入队；`/api/v1/*` 全部要求鉴权，只有 `/api/v1/healthz` 匿名；前端不得把原文写进 localStorage。
5. **数据**：输出目录权限 `700`；报告 HTML 里不嵌入原文正文片段（只嵌 block_id 与偏移）；日志不含敏感正文；API 事件流不携带全文（只带 id 与摘要，正文经单独鉴权接口按需取）。
6. **资源破坏防护**：管线**只读** `work/in/`，绝不写回原目录；删除只允许 `tmp/` 与超过保留期的 `work/s*/`；人工编辑（§7.5）不改写 `.md` 原产物，而是生成新版本号，保留可回滚链。

---

## 5. 产品形态与日常使用场景

在写需求和接口之前，先把「这台 Mac mini 上跑的东西，人每天怎么跟它打交道」讲清楚。**方案能否指导开发，取决于这里定的场景是否具体**；§6–§8 的每一条功能都必须能回溯到本节某个场景，否则就是过度设计。

### 5.1 系统定位

一台**内网自用的文档转换工作站**：把散落的 PDF/DOCX 变成干净、结构化、可校验的 Markdown，供知识库/RAG 语料准备、归档、编辑复用。它不是通用扫描件识别服务，也不是对外产品。

**三种使用入口，一个内核**（§4.1）：Web 控制台（日常主入口）、HTTP API（脚本/其他系统对接）、CLI（批量、回归、运维）。选 Mac mini M4 作为宿主，正是因为它可以 7×24 常开、Metal 常驻 LLM、且成本远低于带独显的工作站。

### 5.2 用户角色

| 角色 | 谁 | 主要诉求 | 主要入口 |
| --- | --- | --- | --- |
| **编辑者 / 知识管理员**（主用户） | 写文档、整理知识库的人 | 上传→拿到可用 MD；有问题能自己改、改完能确认没改坏 | Web |
| **批量操作员** | 负责历史存量文档迁移 | 一次投几百篇，看着进度走，异常的先跳过、回头集中处理 | Web 队列页 + CLI |
| **下游开发者** | 做 RAG / 检索 / 展示的人 | 稳定 API、结构化输出（IR / 分块）、可预测的表格与标题 | HTTP API |
| **运维 / 项目本人** | 维护这台机器 | 服务健康、内存与磁盘、失败原因、版本与回归是否退化 | Web 设置页 + CLI |

### 5.3 日常场景（用例，逐条对应到 §6 需求编号）

| # | 场景 | 用户实际会怎么做 | 系统必须做到 | 需求 |
| --- | --- | --- | --- | --- |
| U1 | **临时转换一篇** | 拖一个 PDF 进浏览器 → 等十几秒 → 看结果 → 下载 MD | 拖拽上传、实时进度、结果内联预览、一键下载 | FR-10x |
| U2 | **批量迁移一目录** | 选一个文件夹（或告诉服务盯某个目录）→ 去干别的 → 回来看汇总 | 目录递归、并行流水、进度百分比 + ETA、完成邮件/提醒（可选）、可暂停 | FR-11x |
| U3 | **结果有问题要修** | 打开某篇的审阅页，左侧原 PDF、右侧 MD，逐块定位「这段被切断」「这个标题层级错了」→ 手工修 | 双栏对照、块级定位（点 MD 跳到 PDF 对应位置）、编辑走审计、表格/代码只读 | FR-2xx |
| U4 | **修完想验证 / 想重跑某一步** | 「我刚改了规则 R-05，重跑 S4 看看这批文件会变成啥样」 | 单步重跑、两个版本 diff、不重跑昂贵的 S2 | FR-3xx |
| U5 | **扫描件混在里面** | 批量投 300 篇，其中 12 篇是扫描件 | 明确拒收清单 + 原因（中文，可执行）+ 单独队列，**绝不产出乱码** | FR-4xx |
| U6 | **给 RAG 供语料** | 开发者调 API 要「按标题分块 + 元数据」的 JSON | 结构化导出（`chunks`）、checksum、可复现的版本号 | FR-5xx |
| U7 | **同一篇文档更新了** | 新版 PDF 覆盖上传 | 内容寻址识别为「同 doc_id 的新版本」，保留历史版本与差异 | FR-12x |
| U8 | **机器表现不对** | 「今天特别慢」「这个为什么一直失败」 | 健康页显示队列/内存/吞吐/失败 Top 原因，能一键 `doctor` | FR-6xx |
| U9 | **不想用浏览器（脚本化）** | 定时任务扫目录丢给 API | Token 鉴权、幂等提交、失败重试、退出码语义与 CLI 一致 | FR-7xx |

### 5.4 明确不做的事（防止场景膨胀）

- 不做在线协作编辑（没有多人同时编辑同一篇的需求，别做 CRDT）。
- 不做文档存储/DMS/权限体系（内部自用，单用户或小团队互信，只做 token 级访问控制）。
- 不做图片/OCR/翻译/摘要/问答等「AI 增值功能」——那是另一个产品的范畴（§6.2）。
- 不做多租户、不做用户账号体系、不做 SSO。
- 不做移动端适配之外的桌面级复杂交互（手机能看一眼队列状态即可，编辑操作以桌面为准）。

---

## 6. 项目需求基线（PRD）

> **本节是防需求漂移的锚。** 每条需求都有编号、优先级（P0 必做 / P1 应该做 / P2 有余力）、验收标准。§9 的任务卡逐条引用这些编号；验收按 §6.5 的 DoD 打勾。任何新增功能必须先在本节登记并写明它服务的场景（U1–U9），否则不实现。

### 6.1 目标与非目标

**目标（v2.1 交付即达成）**

1. 文本型 PDF 与 DOCX 转 Markdown 的**质量稳定可度量**（红线指标见 §4.8）。
2. **可调试、可回滚、可重跑**：中间产物落盘 + 单步重跑 + 版本化输出。
3. **日常真的能用**：Web 控制台覆盖 U1–U5、U7–U8，无需命令行即可完成 90% 的日常工作。
4. **可对接**：HTTP API 覆盖 U6、U9，契约稳定。
5. **一台机器自转**：M4 上 7×24 无人值守，异常自降级（§3.5）。

**非目标**：扫描件/图片处理（§6.2）、对外产品化、协作编辑、账号体系、云端同步、公式识别。

### 6.2 范围声明：图片与扫描件（本次确认，硬性）

| 输入类型 | 处置 | 用户看到什么 |
| --- | --- | --- |
| 文本型 PDF（有文本层） | 正常转换 | MD + 报告 |
| DOCX（纯文本/表格/样式） | 正常转换 | MD + 报告 |
| **纯扫描件 / 图片型 PDF** | **S1.5 拒收** | 拒收队列，原因「无文本层，属扫描件」+ 建议（转其他工具） |
| **双层 PDF（图像 + 垃圾文本层）** | **S1.5 拒收**（`DEGRADED_TEXT`） | 同上，原因写「文本层不可信」 |
| **文档中的插图 / 图片对象** | **不提取、不落盘、不 OCR**，仅在 IR 留登记块 | MD 中按 `s2.image_placeholder` 输出注释占位或省略；报告汇总「本文丢弃 N 张插图，位于 p3/p7…」 |
| DOCX 内嵌图片 / SmartArt / 文本框 | 同上丢弃；文本框内文字**尽力抽取**（它是文本层） | 报告分类列出丢弃项与原因 |
| 公式 | 不做识别；保留原文本层字符 | 报告标注「含疑似公式，未解析」 |

这条决定的工程后果：**不需要 torch 视觉模型作为必需依赖**（L2 结构识别仍可选项 Docling/Marker，但默认关）；**不需要 `media/` 目录与图片落盘**；**MD 输出里不会有 `![](...)` 图片链接**。Agent 若在输出里生成图片语法，即为实现错误，测试应断言拒绝。

### 6.3 功能需求

**FR-1xx 转换与提交（P0）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-101 | 单文件上传（PDF/DOCX，含 `.doc` 自动转 `.docx`） | 拖拽/选择后 ≤ 300ms 返回 `202 + job_id`；magic 校验失败立即 `415` |
| FR-102 | 多文件与整目录上传 | 支持 ≥ 500 文件/次；总大小 ≤ 2GB；重复内容自动去重（同 doc_id） |
| FR-103 | 转换参数可按作业覆盖 | `profile / table_output / image_placeholder / llm on/off / front_matter`；非法值 `422` 并列出可选值 |
| FR-104 | 输出 MD 文件 | 含可选 YAML front-matter（来源名、页数、doc_id、转换时间、profile、指标摘要） |
| FR-105 | 三种导出格式 | `md` / `html`（自包含预览）/ `json-ir`；`chunks`（RAG 分块）与 `csv`（清单）为 P1 |
| FR-106 | 打包下载 | 单篇 zip（md+报告+checksum）；批量 zip（可只导成功项） |
| FR-111 | 目录监听（watch folder） | 指定目录后新文件自动入队；删除源文件不影响已产出；P1 |
| FR-112 | 队列可暂停/恢复/取消 | 取消后不留半成品（`tmp/` 清理，DB 标 `cancelled`） |
| FR-121 | 同文档新版本 | 内容变 → 新 `revision`，保留历史与 diff；同内容改名 → 复用产物 |

**FR-2xx 审阅与修订（P0，产品的核心价值）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-201 | 双栏对照审阅 | 左原 PDF 页（人眼参考，不参与转换）/ 右 MD；滚动联动；块级高亮 |
| FR-202 | **块级双向定位** | 点 MD 任一段 → 高亮 PDF 对应 bbox；点 PDF 区域 → 跳到对应 MD 块 |
| FR-203 | 人工编辑正文块 | 仅 `immutable=false` 可编辑；保存生成新 `revision`；表格/代码在 UI 中显示为**只读围栏**且尝试编辑给出解释 |
| FR-204 | 人工设定标题层级 | 下拉 1–6；改后重建目录并提示受影响的子树 |
| FR-205 | 编辑历史与回滚 | 每次编辑记 `who/when/before/after/reason`；可逐条撤销；可整篇回滚到任一 revision |
| FR-206 | 待复核清单 | `confidence < 0.7` 的标题、超长句、跳号、疑似表格错乱自动汇总为可点击队列 |
| FR-207 | 批注/标记 | 给块打标签（如「需核对原件」），可导出为 checklist；P1 |
| FR-208 | 搜索 | 在结果库内全文搜索（标题/正文/来源文件名），返回块级定位；P1 |

**FR-3xx 重跑与实验（P1，但工程上极重要）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-301 | 单步重跑 | 从任一阶段（s2/s4/s5/s6）重跑到指定阶段，复用上游产物；UI 与 API 都支持 |
| FR-302 | A/B 对比 | 同一 doc 两次运行结果做块级 diff（新增/删除/修改/移动），并排展示 |
| FR-303 | 规则开关实验 | 可临时关闭某条 R-xx 重跑，量化「这条规则贡献了什么」 |

**FR-4xx 拒收与异常（P0）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-401 | 拒收清单 | 每个拒收文件有中文原因码（扫描件/乱码文本层/加密/超限/格式不支持）+ 处置建议 |
| FR-402 | 拒收文件可重投 | 参数调整后重投（如放宽阈值），不必重新上传 |
| FR-403 | 部分失败不阻断批次 | 单文件失败继续跑；批次汇总显示成功/失败/拒收三栏 |
| FR-404 | 失败原因可诊断 | 每条失败带 `err_code` + 阶段 + 可复制的诊断命令 |

**FR-5xx 输出质量与可复现（P0）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-501 | 校验产物 | 每篇产出 `xxx.md` / `xxx.checksum.json` / `xxx.report.html` 三件套 |
| FR-502 | 红线门禁 | `immutable` 篡改即整篇回滚并标记 `BLOCKED`，前端红标显示 |
| FR-503 | 指标摘要 | 报告首页给本篇质量分（标题 F1、表格保真、疑似问题数），批次页给分布 |
| FR-504 | 可复现 | 每篇记录 `git_sha + config hash + 模型版本 + 依赖 lock hash`，可据此重现同结果 |

**FR-6xx 运维与健康（P0/P1）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-601 | 健康页 | 显示：队列长度、`workers` 实际值、内存压力、磁盘余量、吞吐 pps 滑动均值、LLM 状态与驻留内存 |
| FR-602 | 自降级可视化 | 触发 §3.5 门禁时，前端明确显示「已降为 L0 / 已暂停接单」与原因，而非静默变慢 |
| FR-603 | `doctor` 一键自检 | Web 上可触发，输出与 CLI 一致 |
| FR-604 | 日志查看 | 按 `doc_id` / `err_code` 过滤，最近 500 行内联展示；P1 |
| FR-605 | 保留策略管理 | 可手动清理 S2/S3 中间产物（前端显示可回收空间）；P1 |

**FR-7xx 集成（P0）**

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-701 | HTTP API + Token | 见 §7；`401/403` 语义正确，token 可吊销 |
| FR-702 | OpenAPI 文档 | `/api/v1/openapi.json` 自动生成，前端类型由其生成（单一契约源） |
| FR-703 | Webhook（P2） | 作业完成回调到内网 URL；离线断言需白名单放行该地址 |
| FR-704 | CLI/API 行为一致 | 同一输入经 CLI 与 API 产出 MD 的 `sha256` 相同 |

### 6.4 非功能需求（NFR）

| 编号 | 类别 | 要求（按较高性能档设定） |
| --- | --- | --- |
| NFR-1 | 吞吐 | 生产档（M4 enhanced，`workers=5`）稳定 **≥ 3000 页/小时**；单份 100 页文档端到端 **≤ 30 秒**（P50）、≤ 90 秒（P95） |
| NFR-2 | 并发接入 | ≥ 20 个并发 API 会话 / 浏览器标签；队列深度 ≥ 5000 作业不降级（磁盘够就收） |
| NFR-3 | 弹性 | 日均页数按「不定」设计：0 页到 5 万页/天突发均可，突发靠队列削峰，不靠拒绝服务 |
| NFR-4 | 延迟反馈 | 上传后 **≤ 300ms** 得到 `job_id`；进度事件推送延迟 ≤ 1s；ETA 误差 ≤ ±40% |
| NFR-5 | 内存 | 峰值 ≤ 20GB（§3.5）；`memory_pressure warn` 持续 30s 自动降一档且不丢作业 |
| NFR-6 | 磁盘 | 常驻占用 ≤ 25GB；中间产物自动轮转；低于 20GB 余量停止接单 |
| NFR-7 | 可用性 | 单机 7×24，目标月可用性 ≥ 99%；服务重启后**队列与作业状态完整恢复**（§7.4）；崩溃作业自动重投 |
| NFR-8 | 可维护 | 冷启动（服务起来到能接单）≤ 60s（LLM 常驻加载可后台预热，期间以 L0 接单） |
| NFR-9 | 安全 | 只监听本机/内网段；强制 token；无匿名文件读取；不写日志正文 |
| NFR-10 | 兼容 | 同一代码库在 M4（生产）与 T8（兼容档）可运行；`minimal` profile 跑通全流程 |
| NFR-11 | 一致性 | CLI / API / Web 三入口对同一输入产出字节一致（FR-704） |
| NFR-12 | 无外部依赖 | 完全离线运行，除本机 `llama-server` 外无任何网络调用 |

### 6.5 验收定义（Definition of Done）

一个版本可称「完成」，必须同时满足：

1. §4.8 全部红线指标在黄金集上达标，且 `immutable_tamper_rate = 0`。
2. U1–U5、U7–U9 九个场景**在 Web 上手工走通一遍**（截图存档），不是只在 CLI 上走通。
3. NFR-1/2/5/6/7/11 有实测数据支撑（附录 G 脚本 + 一轮 5000 篇的浸泡测试），非估算。
4. `doc2md check`、`doc2md doctor`、`doc2md license-check` 三个命令全部通过。
5. 服务重启后队列恢复测试通过：kill -9 服务进程 → 重启 → 未完成作业全部续跑，无重复无丢失。
6. 文档更新：ADR 追加、`README` 部署步骤在**干净机器**上照做成功一次。

### 6.6 优先级与发布切分

| 发布 | 内容 | 判据 |
| --- | --- | --- |
| **R1 · 内核可用** | M0–M5（S0–S6，纯规则 + 校验 + CLI） | 黄金集达 L0 门槛；CLI 三个子命令可用 |
| **R2 · 服务可用** | M6–M8（LLM patch + 并发 + API + 基础 Web：上传/进度/结果/下载/拒收） | U1/U2/U5/U8/U9 走通；NFR-1/4/7 达标 |
| **R3 · 好用** | M9（双栏审阅 + 块级定位 + 人工修订 + 审计）+ FR-3xx 重跑 | U3/U4 走通；人工修订闭环可回滚 |
| **R4 · 可选** | M7（L2 结构识别 A/B）、FR-208 搜索、FR-111 目录监听、FR-703 webhook | 有明确收益再做 |

**M9（审阅/修订）是这个产品区别于「一个转换脚本」的地方**，但它依赖 IR 与 API 先稳定，所以排在 R3——不要因为想先做出漂亮的界面而把它提到 R1，那会让前端反复返工。

### 6.7 变更控制与防需求漂移（交接必读）

> 交接后最常见的事故不是代码写错，而是「需求悄悄变了」：某天有人加了图片提取、有人把红线调低了、有人为省事让 LLM 直接生成 Markdown。本节把这些变成**必须显式声明**的动作。

**（1）范围守卫：触发器表**——出现下列情况，**必须新开一条 ADR 并重新评估方案**，不允许直接改代码：

| ID | 触发事件 | 需要重评的内容 |
| --- | --- | --- |
| T-1 | 服务从「本机/本人使用」变为「向其他部门/外部提供」 | PyMuPDF AGPL 网络服务条款；是否需实现 `pdf_alt`（MIT）或购授权 |
| T-2 | 有人提出「顺便把扫描件也支持了吧」 | §6.2 非目标；OCR 引入的依赖/内存/许可连锁反应；这是**范围变更**，不是加个开关 |
| T-3 | 有人提出「把图片也抽出来」 | 同上，涉及 `media/` 落盘、体积、命名、IR schema 变更 |
| T-4 | 想放宽任一红线指标（如表格保真从 95% 降到 90%） | 必须附黄金集回归数据 + 决策记录 |
| T-5 | 升级任一依赖（含 torch/PyMuPDF/pandoc/模型权重） | 必须重跑黄金集并对比指标；单字「顺手升级」禁止 |
| T-6 | 更换宿主硬件（M4 → 别的机器 / 上云） | §3 全部重估：内存档、并发档、Metal 前提失效 |
| T-7 | 日均量长期超过 5 万页 | §3.6 容量原则重估，考虑多机分片（当前设计不支持） |

**（2）需求变更流程**（三步，写进 `CONTRIBUTING.md`，不接受口头变更）：

1. 在 §6.3 追加/修改需求条目，**必须写明它服务的场景（U1–U9 之一）**，写不出来的功能 = 范围外。
2. 同步更新 §9 对应任务卡的验收标准（需求与任务卡必须一致，否则视为未登记）。
3. 若影响架构或红线，追加 ADR；否则在 PR 描述里引用需求编号即可。

**（3）实现纪律**（防止「写着写着漂了」）：

- **接口单一源**：API schema 以 `service/schemas` 为唯一定义处，前端类型由其生成（FR-702）。手工在前端复制一份类型定义 = 漂移起点。
- **三入口一致**（NFR-11）：任何功能先实现于 `core`，再暴露给 CLI/API/Web。**禁止在 Web 层实现业务规则**（例如「前端帮用户把表格转成文字」这类，会立刻破坏 immutable 红线）。
- **golden set 是唯一裁判**：争论「这个输出好不好」时，不接受印象，只接受 §4.8 指标与 diff 报告。
- **不可逆约束写在代码里而不是文档里**：`immutable` 校验、离线断言、拒收门禁必须是断言/异常，不靠人自觉。

**（4）需求覆盖矩阵**（交接后自查用）：

| 场景 | 覆盖需求 | 实现任务卡 |
| --- | --- | --- |
| U1 | FR-101/104/105/106 | M0–M5, M8, M9 |
| U2 | FR-102/112/403/404 | M6, M8, M9 |
| U3 | FR-201–207 | M9 |
| U4 | FR-301–303 | M8, M9 |
| U5 | FR-401/402 | M2, M8, M9 |
| U6 | FR-105/701/702 | M8 |
| U7 | FR-121/205 | M8, M9 |
| U8 | FR-601–605 | M8, M9 |
| U9 | FR-701/704 | M6, M8 |

---

## 7. API 设计（`doc2md serve`）

### 7.1 设计原则

1. **作业制，不同步**：转换是长任务，一律 `202 + job_id`，进度靠 SSE。唯一例外是 `POST /convert/sync`（P2，仅限 ≤ 20 页小文件，`timeout` 上限 60s）——留着是为了脚本方便，不作为主路径。
2. **IR 是一等公民资源**：可以按 `doc_id` 取 IR、取某一块、取某阶段产物。前端的一切定位能力都建立在这上面，不要让前端去猜。
3. **幂等**：上传带 `Idempotency-Key`（或直接用 `doc_id`）重复提交返回同一作业，不重复计算。
4. **错误可执行**：错误体固定 `{"error":{"code":"E4015","message":"该 PDF 无文本层，属扫描件，已拒收","stage":"s1_5","hint":"本工具不处理扫描件，请用 OCR 服务或转人工","doc_id":"..."}}`。`code` 稳定，`hint` 面向人。
5. **不泄漏文件路径**：响应里永远不出现沙箱文件系统路径，一律用 `doc_id`/`job_id`/`href`。

### 7.2 端点总览

```
Base:  http://127.0.0.1:8765/api/v1     鉴权：Authorization: Bearer <token>

# 作业与转换
POST   /jobs                      提交作业（multipart 单/多文件，或 json 指定已上传的 doc_id）
GET    /jobs                      列表（?status=&q=&page=&sort=）
GET    /jobs/{job_id}             详情（阶段、耗时、指标、错误）
GET    /jobs/{job_id}/events      ★ SSE 事件流（进度/阶段/完成/失败）
POST   /jobs/{job_id}/cancel      取消
POST   /jobs/{job_id}/retry       重投（可带新参数）
DELETE /jobs/{job_id}             移除记录（产物按保留策略清）

# 文档与产物
GET    /docs/{doc_id}                    元数据 + 最新版本 + 历史 revision 列表
GET    /docs/{doc_id}/ir                 全量 IR JSON（?stage=s4 取中间态；?page=7 过滤）
GET    /docs/{doc_id}/blocks/{block_id}  单块（含 raw_lines / bbox / provenance / 只读标志）
GET    /docs/{doc_id}/md                 Markdown（?revision=&fmt=md|md-plain|frontmatter）
GET    /docs/{doc_id}/report             HTML 报告
GET    /docs/{doc_id}/checksums          immutable checksum 清单
GET    /docs/{doc_id}/bundle             zip（md+report+checksums）
GET    /docs/{doc_id}/pages/{p}.png      ★ 仅用于人眼对照的原页面渲染（可选开关，不参与转换）
POST   /docs/{doc_id}/rerun              单步重跑（?from=s4&to=s6）
GET    /docs/{doc_id}/diff?a=rev3&b=rev5 块级 diff

# 修订（FR-203–205）
PATCH  /docs/{doc_id}/blocks/{block_id}  编辑正文块 / 设标题层级（body: {text|level|reason})
GET    /docs/{doc_id}/revisions          版本历史
POST   /docs/{doc_id}/revisions/{rev}/rollback   回滚
GET    /docs/{doc_id}/audit              编辑审计流水

# 队列与拒收
GET    /queue                队列状态（深度、桶分布、workers、是否降级）
GET    /rejects              拒收清单（?reason=&since=）
POST   /rejects/{doc_id}/reprocess       参数调整后重投

# 搜索（P1）
GET    /search?q=&type=block|doc|heading&limit=

# 系统与运维
GET    /healthz              匿名存活探针
GET    /system/stats         内存/磁盘/吞吐/LLM 状态
POST   /system/doctor        触发自检（同步返回结果）
GET    /settings  / PUT      profile / 规则开关 / 保留策略（写操作需 admin token）
GET    /openapi.json         契约（前端类型的唯一来源）
```

### 7.3 提交作业（multipart）

```bash
curl -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: sha256:1a2b..." \
  -F "files=@合同A.pdf" -F "files=@制度B.docx" \
  -F 'options={"profile":"enhanced","llm":"patch","table_output":"html",
              "front_matter":true,"on_reject":"queue","tags":["法务","2026"]}'
```

响应 `202`：

```json
{ "jobs": [
    {"job_id":"j_9f3c2a","doc_id":"sha256:1a2b...","name":"合同A.pdf","pages":37,
     "bucket":"small","queue_position":0,"eta_seconds":6,
     "status":"queued","events_href":"/api/v1/jobs/j_9f3c2a/events"},
    {"job_id":"j_1b7d88","doc_id":"sha256:77ee...","name":"制度B.docx","pages":12,
     "bucket":"small","queue_position":1,"eta_seconds":11,"status":"queued"}],
  "rejected_at_intake": [] }
```

`bucket` 即 §3.6 的分桶（`small ≤ 20页` / `medium ≤ 120页` / `large > 120页`），调度按桶加权公平轮转。

### 7.4 事件流（SSE，前端主用；不用 WebSocket）

选 SSE 的理由：单向服务端推送足够覆盖需求、走普通 HTTP、断线重连语义简单（`Last-Event-ID`）、浏览器原生、不引入 ws 依赖与额外鉴权面。

```
GET /api/v1/jobs/j_9f3c2a/events        Accept: text/event-stream
```

```
id: 41
event: stage_done
data: {"job_id":"j_9f3c2a","doc_id":"sha256:1a2b","stage":"s2","pages":37,
       "dur_ms":1240,"artifacts":{"s3_ir":"ir_version":"2.0","bytes":812334}}

id: 42
event: progress
data: {"job_id":"j_9f3c2a","stage":"s4","page":21,"total_pages":37,"pct":0.57}

id: 43
event: warning
data: {"job_id":"j_9f3c2a","code":"W1042","block_id":"b0088","msg":"跨页表格未合并，保持原样"}

id: 44
event: job_done
data: {"job_id":"j_9f3c2a","status":"succeeded","revision":"rev3","total_ms":9820,
       "metrics":{"heading_f1":0.94,"table_fidelity":1.0,"sentence_f1":0.951,
                  "immutable_tamper":0,"dropped_images":3,"needs_review":2},
       "resources":{"md":"/api/v1/docs/sha256:1a2b/md","report":"...","bundle":"..."}}
```

| 事件 | 时机 | 前端用途 |
| --- | --- | --- |
| `job_queued` | 入队 | 显示排队位与 ETA |
| `stage_start` / `stage_done` | 每阶段 | 阶段进度条（S1→S6 可视化，对应 U2/U4） |
| `progress` | 每页/每块，节流 ≤ 5 次/秒 | 百分比 |
| `warning` | 规则级告警 | 黄色标记，点击跳到块 |
| `reject` | S1/S1.5 拒收 | 进拒收队列（U5） |
| `job_done` / `job_failed` | 终态 | 结果链接 / 诊断信息（FR-404） |
| `system_degraded` | 资源门禁触发 | 顶部横幅（FR-602） |

**实现要求**：心跳 15s（`event: ping`）防代理断流；事件必须可重放（写 `work/logs/events.sqlite` 或 DB 表），前端重连用 `Last-Event-ID` 补齐；**单条事件载荷 ≤ 4KB，绝不塞正文**（§4.10 第 5 条）。

### 7.5 修订接口与安全边界

```json
PATCH /api/v1/docs/{doc_id}/blocks/b0017
{ "text": "修正后的段落文本", "reason": "原件第 12 行漏字，人工核对", "as": "editor-token" }
→ 200 {"revision":"rev4","affected_blocks":["b0017"],"recalc":{"sentence_f1":0.951},
       "audit_id":"a_77c1"}
→ 409 {"error":{"code":"E4091","message":"该块为只读（表格），不允许编辑","hint":"表格如需修正，请回源文件重新导出"}}
```

后端强制：① `immutable` 块 → `409`；② 编辑后重跑 S6 渲染 + checksum 复算 + 指标重算；③ 写审计流水（`who/when/before/after/reason`）；④ 生成新 `revision`，旧版本保留；⑤ 单次编辑只允许改一个块（批量修改由前端循环调用，便于逐条审计）。

### 7.6 人工复核闭环（U3/U5 的服务端支撑）

```
GET /api/v1/docs/{doc_id}/review-queue
→ 200 {"items":[
  {"block_id":"b0104","kind":"heading_level_low_conf","confidence":0.55,
   "page":9,"bbox":[40,120,300,142],"excerpt":"3.1 实施计划"},
  {"block_id":"b0088","kind":"table_suspect","code":"W1042","page":7},
  {"block_id":"b0210","kind":"long_sentence_no_punct","len":118,"page":14}]}
```

`kind` 取值固定为可枚举集合（前端按类型渲染不同修正控件），新增类型 = 需求变更（§6.7）。

### 7.7 导出格式与下游对接（U6）

| `fmt` | 内容 | 用途 |
| --- | --- | --- |
| `md` | 默认，含 front-matter（YAML：doc_id/name/pages/profile/revised/metrics/`immutable_verified:true`） | 人读、入库 |
| `md-plain` | 无 front-matter，纯 MD | 直接编辑复用 |
| `html` | 自包含单文件（内联 CSS，表格/代码原样） | 预览、给不看 MD 的人 |
| `json-ir` | 全量 IR（含 bbox 与 provenance） | 二次开发、审计 |
| `chunks` | **按标题层级切分的分块**：`{chunk_id, heading_path[], level, text, table_html?, page_start, page_end, checksum}`，可选 `max_tokens` 上限与相邻块重叠 | RAG 语料（这是下游最需要的形态） |
| `csv` | 批次清单：文件名、页数、状态、指标、耗时、问题数 | 迁移项目的进度台账 |

**RAG 分块规则（写死，避免下游各切各的）**：以 `heading` 为切分边界，表格与代码块**不拆**（immutable 约束延续）；超长段落按句子边界（§S5-L1 结果）二次切；每个 chunk 携带 `heading_path` 以便召回后带上下文。

### 7.8 错误码表（节选，稳定契约）

| HTTP | `code` | 含义 | 前端处置 |
| --- | --- | --- | --- |
| 400 | `E4001` | 参数非法（列可选值） | 表单内联报错 |
| 401 | `E4011` | 缺 token | 跳登录/提示配置 |
| 409 | `E4091` | 编辑只读块 | 弹解释，不给编辑框 |
| 413 | `E4131` | 超过 `max_upload_mb` | 提示拆分或走批量入口 |
| 415 | `E4151` | magic 与扩展名不符 / 不支持的格式 | 直接说明真实识别类型 |
| 422 | `E4221` | 文本层不可信（`DEGRADED_TEXT`） | 进拒收队列（U5） |
| 422 | `E4222` | 扫描件（无文本层） | 同上，附「本工具不处理扫描件」 |
| 423 | `E4231` | 加密 / 权限受限 | 提示去密码后重投 |
| 425 | `E4251` | 版本冲突（revision 已被他人改） | 提示刷新对比 |
| 503 | `E5031` | 资源门禁，暂停接单 | 显示 `Retry-After`，队列保留（FR-602） |
| 500 | `E5001` | IR schema 校验失败（代码 bug） | 提示运行 `doc2md inspect` 并上报 |

### 7.9 OpenAPI 生成与前端类型

`service/schemas` 用 Pydantic v2 定义 → FastAPI 自动产出 `/openapi.json` → 前端用 `openapi-typescript` 生成 `web/src/api/schema.d.ts`，**纳入 CI 检查**：契约变了但类型没重新生成 = 构建失败。这是 §6.7「接口单一源」的落地方式。

---

## 8. 前端设计（Web 控制台）

### 8.1 技术选型（内网优先）

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 框架 | React 18 + TypeScript + Vite | 生态稳、Agent 生成质量高、构建快；Vue 亦可，**但选定后不得中途换** |
| 服务端状态 | TanStack Query + 自建 SSE hook（`useJobEvents`） | 长任务进度天然适合「订阅 + 缓存」，别用 Redux 手搓 |
| 组件与样式 | Radix UI（无样式原语）+ Tailwind CSS + lucide-react | 内网可离线构建；不引重型 UI 框架（Ant Design 可选，但包体大且样式override 成本高） |
| Markdown 渲染 | `markdown-it` + DOMPurify | **必须净化**：MD 里含表格 HTML 与题注文本，禁止直出 `dangerouslySetInnerHTML` |
| PDF 对照 | `pdfjs-dist`（本地打包，禁 CDN） | 仅作人眼参考；渲染走浏览器，不依赖后端出图（也可用 `GET /pages/{p}.png` 兜底） |
| 状态持久 | 仅 UI 偏好进 `localStorage`；正文一律不落本地 | §4.10 数据安全 |
| 部署 | `web/dist` 由 FastAPI `StaticFiles` 挂载到 `/` | 同源、无 CORS、无 Nginx——内网最省事的部署形态 |

**离线纪律**：所有前端依赖打包进构建产物，禁止任何 CDN/字体/统计脚本；`index.html` 里出现 `https://` 外部域 = CI 失败。

### 8.2 信息架构与路由

```
/doc2md/
  /                 工作台（首页：快速上传 + 今日概览 + 待我复核）
  /jobs             任务队列（进行中 / 全部 / 我的批次）
  /docs             结果库（搜索、筛选、批量操作、导出）
  /docs/:docId      审阅页 ★（双栏对照 + 块级编辑）
  /docs/:docId/report   报告页（指标、告警、丢弃清单）
  /docs/:docId/compare?a=&b=  版本/配置对比（FR-302）
  /rejects          拒收队列（U5）
  /review           待复核总队列（跨文档，FR-206）
  /system           健康与运维（FR-601–605）
  /settings         参数（profile、规则开关、保留策略、token 管理）
```

### 8.3 页面规格（逐页给出组件与交互，可直接实现）

**P1 工作台 `/`**

- 大拖拽区（drop 即上传，支持文件夹 `webkitdirectory`）；上传后立即显示卡片流，每卡一行进度。
- 「今日概览」条：已转换 X 篇 / Y 页，拒收 Z 篇，待复核 N 项，吞吐与内存小指示灯（点击进 `/system`）。
- 「待我复核」前 5 项，直跳 `/review`。
- 空状态放三条「怎么开始」（拖文件 / 复制 curl 示例 / 打开结果库），降低交接后的陌生感。

**P2 任务队列 `/jobs`**

- 表格列：名称｜状态（阶段徽章 S1…S6）｜页进度｜耗时｜ETA｜问题数｜操作。
- 顶部显示 `queue_position` 汇总与 `workers` 实际值；资源降级时整页顶部红/黄横幅（FR-602）。
- 批量操作：重投失败项、取消、导出清单 CSV（FR-105 `csv`）。
- 点击行 → 抽屉显示事件时间线（每阶段 `dur_ms`），这是 U4 诊断的第一现场。

**P3 结果库 `/docs`**

- 筛选：时间、profile、状态、指标区间（如 `heading_f1 < 0.8`）、标签、来源类型。
- 列：名称｜页数｜标题数｜表格数｜丢弃插图数｜待复核数｜质量分｜版本｜最近修改｜操作（查看/下载/重跑/对比）。
- 支持多选打包下载（zip）；「按质量分升序」是默认——**先看最差的那些**，这是效率关键。

**P4 审阅页 `/docs/:docId`（核心页）**

三栏布局：

```
┌─ 左：原 PDF 页（pdf.js）      ─┬─ 中：Markdown 渲染视图 ─┬─ 右：块信息面板 ─┐
│  页面缩略图 + 当前页            │  按块渲染，块可 hover 高亮 │  bbox/字号/来源  │
│  bbox 覆盖层（虚线框）          │  表格/代码 = 只读围栏(锁图标)│  confidence      │
│  滚动联动                      │  待复核块左侧黄条 + 序号    │  provenance 链   │
└──────────────────────────────┴─────────────────────────┴────────────────┘
```

- **双向定位**（FR-202）：点中栏块 → 左栏滚动到该页并高亮 bbox；点左栏区域 → 中栏跳到对应块。实现依据是 §4.3 的 `bbox` + `page`，这是 IR 保留坐标的回报。
- **编辑**：正文块点击进入行内编辑（保留原始换行为软换行显示）；保存调 §7.5 PATCH；表格/代码块显示锁图标 + tooltip「表格与代码为不可变块，如需修正请修改源文件后重转」。
- **标题层级**：块右键 → 「设为 1–6 级」；改后中栏目录树与层级即时重排，并提示「本修改影响其下 4 个块」。
- **顶部工具**：版本选择器（revision）、`对比`（进 compare）、`重跑`（选起点阶段，FR-301）、`下载`（md/html/zip）、`下一个待复核`（`n` 键跳转）。
- **键盘优先**：`j/k` 跳块、`e` 编辑、`Enter` 保存、`Esc` 取消、`1–6` 设层级、`n` 下一个待复核项。复核是重复劳动，键盘效率决定这个页面到底有没有人用。
- **右栏 provenance**：显示该块被哪些规则改过（`s4:merge_hard_break`、`llm:insert_punct@42`），让「为什么变成这样」可回答——这是消除对自动管线不信任的最有效设计。

**P5 报告页 `/docs/:docId/report`**：指标仪表（标题 F1、表格保真、句切 F1、字符相似）、告警列表（按 `code` 聚合，点击跳块）、**丢弃清单**（N 张插图 + 页码，对应 §6.2）、环境指纹（git sha / config hash / 模型版本）。

**P6 版本对比 `/compare`**：左右两版并排，块级对齐显示增删改；顶部显示「本次对比的变量是 R-05 关闭」这类元信息（FR-302/303）。

**P7 拒收队列 `/rejects`**：按原因分组（扫描件/文本层不可信/加密/超限/格式不符），每行显示识别到的真实类型与 `hint`；支持「调宽阈值后重投」（FR-402），**不支持强行转换**（§6.2 硬约束）。

**P8 待复核总队列 `/review`**：跨文档聚合 `kind` 类型，逐条处理并「标记已解决」；进度条给「今天清了多少」的反馈。这是把「人工修」从隐性劳动变成可见工作的关键页面。

**P9 健康与运维 `/system`**：队列深度、各桶等待、`workers` 实际值（是否被降级）、内存压力条 + 20GB 上限刻度、磁盘余量与可回收空间、吞吐 pps 滑动曲线、LLM 驻留与 tokens/s、失败 `err_code` Top10；`运行自检` 按钮（`POST /system/doctor`，结果内联展示）。

**P10 设置 `/settings`**：profile 切换（含「本次实验临时参数」提示，说明仅影响新作业）、18 条规则开关（每条带一句话说明与影响范围）、保留策略与清理、token 管理、危险操作（清空队列）需二次确认输入队列名。

### 8.4 交互细节（日常好不好用全在这里）

1. **ETA 与排队透明化**：所有等待中的作业显示「前面还有 4 个，预计 38 秒」。日均量不定，用户对「不知道等多久」的容忍度远低于「要等」。（对应 §3.6 原则 4）
2. **进度不能只到 99% 就卡住**：S5 的 LLM 段最慢，必须发 `progress` 逐块事件，而不是停在「转换中」。
3. **乐观失败**：拒收在**提交阶段**就返回（不占队列位），让用户立刻知道这篇不行。
4. **表格/代码的锁不可绕过**：前端不给编辑入口，后端 `409` 兜底——两处都要有（§6.7「不可逆约束写进代码」）。
5. **一次点击看到「差在哪」**：待复核块在中栏有黄条 + 右上角计数，不需要打开报告页才知道文档有问题。
6. **可复制的 curl**：结果页和设置页都有「用 API 重跑这个作业」的复制按钮，方便下游开发者从 UI 迁移到脚本（U9 的顺滑路径）。
7. **移动端只读适配**：手机上能看队列状态与结果预览即可，不暴露编辑功能（避免移动端误改；§5.4）。
8. **空/慢/错三种状态都有设计**，不允许白屏。
9. **深浅色与中文排版**：正文渲染需支持 CJK 行高与中英混排间距（复用 §S5-L1 的规范化结果，不要在 CSS 里二次 hack）。

### 8.5 性能与工程约束

- 审阅页**按页/按块虚拟化**（`react-virtual`），500 页文档不一次性渲染全部块；IR 分片接口 `GET /ir?page=` 支撑。
- SSE 断线自动重连（指数退避，`Last-Event-ID` 补事件），重连后与后端状态对账一次（防「前端显示完成但后端失败」）。
- 构建产物 gzip ≤ 500KB（首屏），pdf.js 按需懒加载（只在审阅页 import）。
- 无外部请求（§8.1）；无埋点；控制台零错误为验收项。
- 组件测试：Playwright 覆盖 U1（上传→下载）、U3（定位+编辑+回滚）、U5（拒收可见）三条主路径。

### 8.6 API 与前端的责任边界（再次强调，最容易漂的地方）

| 事项 | 归属 | 说明 |
| --- | --- | --- |
| 是否可编辑某块 | **后端** | 前端只按 `read_only` 渲染；后端 `409` 兜底 |
| 质量分计算 | **后端** | 前端只展示，绝不本地重算指标 |
| 分块（RAG chunks） | **后端** | 见 §7.7，规则唯一 |
| 层级/标题判定 | **后端** | 人工可覆盖（FR-204），但判定逻辑不在前端 |
| 显示虚拟化、滚动联动、键盘 | **前端** | 纯呈现逻辑 |
| 缓存与乐观更新 | **前端** | 失败必须回滚 UI 并展示后端真实状态 |

---

## 9. 实施计划（AI Agent 任务卡）

总量：单人 **7–8 周**（内核 4–5 周 + 服务与前端 3 周）。每张卡都是可独立验收的最小闭环，**验收不过不许进下一张**。每张卡标注其覆盖的需求编号（§6.3），交接后任何人加功能都要回来补卡。

### M0 · 环境与自检（1 天，两平台各一次）｜覆盖 NFR-9/12、FR-603

- **产出**：`setup_win.bat` / `setup_mac.sh`、`doc2md doctor` 实现、`pyproject.toml` + lockfile、`vendor/` 目录就位、**离线断言**（§4.10 第 1 条，进 CI）。
- **必做**：`doctor` 必须打印 `arch / sysconfig platform / macOS version / mps_available / mem_total / ulimit -n / disk_free / pandoc --version / llama-server --version`；任一不满足退出码 90。
- **验收**：在 M4 上 `arch -x86_64 zsh` 里跑 `setup_mac.sh` 必须**明确报错并拒绝安装**（Rosetta 防线）；`doctor` 全绿；`doc2md license-check` 能输出许可清单。
- **陷阱**：Mac 上先跑 `xattr -dr com.apple.quarantine vendor/bin/*`；用 venv 不用系统 Python。

### M1 · Schema + IR 骨架（2 天）｜覆盖 FR-202/504 的数据前提、§7.2 可取 IR 的前提

- **产出**：`core/schema.py`（IR v2 定义 + 校验器，含 `image` 登记块约束 §4.3-8）、`core/ir_io.py`（原子写 + zstd + 读回 + 按页分片读取）、`tests/test_schema.py`。
- **验收**：任意构造的非法 IR（缺 id、pos 越界、immutable 块带 `modified_by`、`image` 块带 `src` 字段）被拒；`ir_io` 写入中断不留半成品文件；`doc2md inspect` 能读；**按页取 IR 的接口可用**（前端虚拟化依赖它）。

### M2 · S1 + S1.5 探测与体检（3 天）｜覆盖 FR-401/402、§6.2

- **产出**：magic 探测（不信任扩展名）、`.doc` 转换支路、页数/体积门禁、**文本层体检 5 个指标**、拒收队列（含中文原因码，供 §7.8 错误码与 §8.3 P7 直接使用）。
- **验收**：构造 4 类样本（原生 PDF / 纯扫描 / 双层假文本 / 缺 ToUnicode 的 CID 字体）与 3 类 DOCX（真样式 / 加粗伪标题 / 加密），体检必须把后三类**全部拦下**且 `reject_reason` 各不相同；`reject_recall ≥ 0.98`。
- **注意**：假文本层的判定不能只看「有没有文字」，要看**字符是否落在合理 Unicode 区**——这是 v1 的最大遗漏。原因码集合一旦定稿即对外契约（§7.8），不要随意改名。

### M3 · S2 通道（5–7 天，核心）｜覆盖 FR-101/102/121 的前置

- **产出**：`docx_pandoc.py`（AST→IR，含 `numbering.xml`/`styles.xml` 元数据交叉）、`pdf_pymupdf.py`（逐块 + `raw_lines` + 字体 + x0 栏聚类）、`pdf_struct_tree.py`（pikepdf 读 StructTreeRoot，映射 H/L/Table）、**`image_scan.py`（仅检测登记插图对象，不落盘，§4.3 约定 8）**。
- **验收**：20 篇多样本文档跑通；`raw_lines` 与 PDF 实际视觉行数一致率 ≥ 99%（脚本比对 `page.get_text("text")` 的换行）；双栏文档阅读顺序错序率 ≤ 5%；带结构树的 PDF 走 `pdf-structtree` 通道并记录 `style_src`；**含插图的文档产出 `stats.dropped_images` 且 MD 中不含任何 `![]()` 图片语法**（单测断言）。
- **交付物**：同时产出「结构/断句/字符」三类错误分布统计，作为 M4 规则设计输入。

### M4 · S4 规则重建（6–8 天，收益最大）｜覆盖 FR-201/202 的数据前提

- **产出**：`rules/` 下每规则一文件 + 配置化开关（清单见附录 C，共 18 条），每条带单元测试与「开关影响面」注释。**每块必须带 `confidence` 与 `provenance`**——这直接决定 §8.3 P4 的「待复核黄条」和「provenance 链」能不能做。
- **顺序不可变**：去噪 → 标题判定 → 编号连续性校验 → 列表重建 → **先合并硬换行/跨页** → **再切分粘连段落** → 表格/代码隔离 + 插图登记。
- **验收**：黄金集上 `heading_F1 ≥ 0.85`（未接 LLM 前的门槛）、`table_struct_similarity ≥ 0.95`、`immutable_tamper_rate = 0`。
- **纪律**：**每条规则必须能单独关掉且不崩管线**（FR-303 的前提）；关不掉 = 该规则不许合入。

### M5 · S5-L1 规则修补 + S6 渲染校验（4 天）｜覆盖 FR-501/502/503/504

- **产出**：断句规则集、全半角/中英空格规范化、Markdown renderer、`checksum.json`、`report.html`（含 diff、拒收清单、复核清单）、红线门禁、**环境指纹写入产物元数据**（FR-504）。
- **验收**：`sentence_boundary_F1 ≥ 0.90`（未接 L3 前）；人为篡改一个表格块，S6 必须失败退出码 20 并回滚该文件；报告页能在浏览器直接打开且不含原文大段复制（§4.10 第 5 条）。

### M6 · L3 LLM patch + 并发调度 + 双平台调优（5 天）｜覆盖 NFR-1/5/10、FR-704

- **产出**：`llm_server.py` 生命周期（M4 常驻、T8 按需启停）、patch 客户端 + 5 道校验、`runtime/scheduler.py`（**三段流水 + 分桶公平队列 + 资源门禁 + 事件广播 + 状态持久化**，§4.9）、`profile` 自动裁剪、`doc2md bench`。
- **要点**：调度器是 CLI 与 API 的共用底座，**必须在 M8 之前完成并可独立被 CLI 驱动验证**，否则 API 层会被迫自己实现一套队列，造成双份逻辑（§6.7 纪律）。
- **验收**：`enhanced` 档下 100 页文档端到端 **≤ 30 秒（P50）/ ≤ 90 秒（P95）**（NFR-1）；`minimal` 档在 T8 上能独立跑完（**必须在真 T8 或等价 8GB 虚拟机上验证一次**，NFR-10）；LLM 超时不阻塞，只记 `W3001` 并跳过；`immutable_tamper_rate` 仍为 0；kill -9 调度进程后重启，队列完整恢复（NFR-7 前半）。

### M7（可选）· L2 结构识别增强（3 天，仅 M4 `enhanced`）｜R4

- **产出**：`struct_layout.py` 的 Docling / Marker 适配器，与纯规则结果做 A/B。
- **验收标准是数据，不是印象**：黄金集上「标题 F1 + 表格保真 + 吞吐 + 内存峰值」四列对比表写进 ADR-05，**若 F1 提升 < 3% 则默认关闭 L2**（v1 的判断在 M4 上也可能是对的，要用数字确认）。**注意：L2 仍不得引入图片内容提取**（§6.2）。

### M8 · HTTP API 服务（6–8 天）｜覆盖 FR-101/102/105/106/112/121、FR-401–404、FR-6xx、FR-7xx、U1/U2/U5/U6/U8/U9

- **产出**：`service/`（FastAPI + Pydantic v2 schemas）、§7.2 全部端点、SSE 事件流（可重放 + 心跳）、token 鉴权与吊销、错误码表落地、`openapi.json` + 前端类型生成脚本、静态挂载 `web/dist`、launchd 常驻配置。
- **范围**：M8 只做 API 与「最小可用前端」（上传 + 队列 + 结果列表 + 下载 + 拒收页）；**审阅/编辑页属于 M9**，不要在这里顺手做。
- **验收**：
  1. 用 `curl` 完成 U9 全流程（提交 → 订阅事件 → 取 md/chunks → 下载 bundle），不需要浏览器；
  2. `Idempotency-Key` 重复提交返回同作业且不重算（FR-幂等）；
  3. 20 并发会话 + 队列 1000 作业压测，无丢事件、无 OOM（NFR-2）；
  4. 触发资源门禁时 `503 + Retry-After` 且事件流发出 `system_degraded`（FR-602）；
  5. **FR-704**：同一文件经 CLI 与 API 产出 MD 的 `sha256` 一致（进 CI 的对比测试）。

### M9 · Web 控制台完整版（7–10 天）｜覆盖 FR-2xx、FR-3xx、U3/U4/U7/U8

- **产出**：§8.3 十个页面全部实现；双向块定位（bbox ↔ PDF 覆盖层）；行内编辑 + 只读围栏；revision 与回滚；审计流水视图；`/review` 跨文档复核队列；`/system` 健康页；键盘操作。
- **验收**：
  1. **U3 走通**：一篇 60 页含双栏与表格的 PDF，从上传 → 发现 3 处标题层级错 → 逐个修正 → 保存新 revision → 回滚其中一个 → 下载，全程不碰命令行；
  2. **U4 走通**：关闭规则 R-05 重跑 S4，与开启版本做块级 diff，并排显示差异；
  3. 500 页文档打开审阅页首屏 ≤ 2s（虚拟化生效），内存不爆；
  4. 表格/代码块在 UI 中无编辑入口，且直接调 PATCH 返回 `409`（前后端双闸，§8.6）；
  5. Playwright 三条主路径（U1/U3/U5）通过；控制台零错误；构建产物无外部域请求（§8.1）。

### 交付物清单（最终）

```
doc2md.zip / 内网部署包
  ├── 可运行程序（venv + vendor 二进制 + 模型 + web/dist 前端产物）
  ├── config/（base + 三个 profile + api.toml）
  ├── setup_{mac,win}.{sh,bat} + README.md（部署手册，含 Gatekeeper/TCC/launchd 步骤）
  ├── docs/（本方案 + ADR + 规则库说明 + API 契约 openapi.json + 前端页面说明 + 故障排查）
  ├── tests/（单测 + 黄金集回归 + Playwright 三条主路径 + check 报告样例）
  └── vendor/licenses.txt（许可清单）
```

**排期提示**：M0–M6 是内核（约 4–5 周），M8 与 M9 依赖内核稳定但彼此可部分并行（M8 期间前端可先用 mock 契约做脚手架，契约以 `openapi.json` 为准）。**别在 M4 规则没跑稳之前开工 M9 的审阅页**——审阅页的交互完全建立在 `bbox`/`confidence`/`provenance` 这些数据结构上，它们不稳就会导致大面积返工。

---

## 附录 A. 离线包获取（在有网机上执行）

**Windows 目标机（T8）**：

```bash
# 同版本 Python（3.11/3.12 x64）
pip download -d wheels/win_amd64 -r requirements.txt \
  --only-binary=:all: --platform win_amd64 --python-version 311
# pandoc 官方 windows-x64.msi；LibreOffice Portable（.doc 转换）；llama.cpp win-cu-cpu 包的 llama-server.exe
```

**macOS arm64 目标机（M4）**：

```bash
# 最稳的做法：直接在同架构 Mac 上 pip download（wheel 天然正确）
pip download -d wheels/macos_arm64 -r requirements.txt --only-binary=:all: \
  --platform macosx_11_0_arm64 --platform macosx_11_0_universal2 \
  --python-version 312

# 无 Mac 的 Linux 机上下载时，注意 PyMuPDF 的 macOS wheel 是 universal2
# torch（若启用 L2）：--platform macosx_12_0_arm64，包名后缀 macosx_*_arm64
pip install -r requirements.txt --no-index --find-links wheels/macos_arm64
```

**模型与二进制（M4）**：

```bash
# GGUF（HuggingFace 上下载单文件；三档按 §3.7 profile 选用）
qwen3-8b-instruct-q4_k_m.gguf           # ~5.1GB —— 生产默认（enhanced / M4 32GB 常驻）
qwen3-4b-instruct-2507-q4_k_s.gguf      # ~2.5GB —— standard 档 / 内存告警降档时回退
qwen3-0.6b-instruct-q8_0.gguf           # ~0.75GB —— 仅 T8 兼容层或 extreme 降级
# llama.cpp: brew build --HEAD 或下载 release 的 macos arm64 包
# 落地后必做：xattr -dr com.apple.quarantine ./llama-server && codesign -s - ./llama-server
# Docling/Marker 权重（L2，默认 off，仅结构模型实验时下载）：hf download ds4sd/docling-models 等
```

**启动 LLM（M4，Metal，生产默认 8B 常驻）**：

```bash
llama-server -m vendor/models/llm/qwen3-8b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 -ngl 99 -c 8192 \
  --parallel 2 --threads 6 --temp 0 --reasoning-budget 0 --timeout 60
# -ngl 99：全部层进 Metal；--parallel 2 对应 [run] llm_clients=2
# 8B 常驻实测 RSS ~6–7GB（含 KV），在 [run] mem_ceiling_gb=20 之内
# standard 档改 -m …qwen3-4b-instruct-2507-q4_k_s.gguf；T8 上 -ngl 0 并改回「按需启停」
```

---

## 附录 B. 代码骨架（可直接作为实现起点）

### B-1 PDF 抽块（S2-B，保留 `raw_lines`）

```python
import fitz, hashlib, statistics

MIN_BODY_SIZE = 9.0
HEADING_RATIO = 1.30          # 字号 > 正文中位数 ×1.3 判标题候选
COLUMN_X_TOL  = 18.0          # x0 聚类容差（pt）

def dominant_font_size(page) -> float:
    sizes = []
    for b in page.get_text("dict", flags=fitz.TEXT_PRESS_ALL)["blocks"]:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if s["text"].strip():
                    sizes.append(round(s["size"], 1))
    return statistics.median(sizes) if sizes else MIN_BODY_SIZE

def cluster_columns(pages_blocks):
    xs = sorted(b["bbox"][0] for b in pages_blocks if b["bbox"][2]-b["bbox"][0] > 40)
    if len(xs) < 8:
        return [0] * len(pages_blocks), 1
    clusters, centers = [], []
    for x in xs:
        for i, c in enumerate(centers):
            if abs(x-c) <= COLUMN_X_TOL:
                clusters.append(i); break
        else:
            centers.append(x); clusters.append(len(centers)-1)
    return clusters, len(centers)

def extract_pdf(path, max_pages=300):
    out, doc = [], fitz.open(path)
    try:
        for pno in range(min(len(doc), max_pages)):
            page = doc[pno]
            body = dominant_font_size(page)
            h, w = page.rect.height, page.rect.width
            for bi, b in enumerate(page.get_text("dict")["blocks"]):
                if b["type"] != 0:
                    continue
                lines, spans_meta = [], []
                for l in b["lines"]:
                    txt = "".join(s["text"] for s in l["spans"]).strip()
                    if txt:
                        lines.append(txt)
                        spans_meta += l["spans"]
                if not lines:
                    continue
                size = max((s["size"] for s in spans_meta), default=body)
                name = spans_meta[0]["font"] if spans_meta else ""
                flags = spans_meta[0].get("flags", 0) if spans_meta else 0
                y0, y1 = b["bbox"][1], b["bbox"][3]
                out.append({
                    "id": f"p{pno+1}b{bi:04d}",
                    "page": pno + 1,
                    "bbox": list(b["bbox"]),
                    "text": " ".join(lines),
                    "raw_lines": lines,
                    "indent_x": round(b["bbox"][0], 1),
                    "font": {"name": name, "size": round(size, 2),
                             "bold": bool(flags & 2 ** 4),
                             "mono": any(k in name.lower() for k in ("mono", "courier"))},
                    "is_furniture_candidate": y0 < h*0.10 or y1 > h*0.90,
                    "heading_candidate": size > body*HEADING_RATIO,
                })
    finally:
        doc.close()                       # 严禁跨进程复用 Document
    return out

CAPTION_RE = re.compile(r"^(图|表|Figure|Table|Fig\.?)\s*[\d０-９\-\.]+")

def nearby_caption(page, rect):
    """在图片下方 30pt / 上方 12pt 的文本行里找题注（纯文本层匹配，非 OCR）。"""
    probe = fitz.Rect(rect.x0, rect.y1, rect.x1, rect.y1 + 30)
    probe |= fitz.Rect(rect.x0, rect.y0 - 12, rect.x1, rect.y0)
    for line in page.get_text("text", clip=probe).splitlines():
        if CAPTION_RE.match(line.strip()):
            return line.strip()[:120]
    return None

def scan_images(page, pno):
    """S2-D：只登记插图对象，不落盘、不 OCR（§6.2）。产出 type=image 的登记块。"""
    reg = []
    for bi, img in enumerate(page.get_images(full=True)):
        try:
            rects = page.get_image_rects(img[0])
        except Exception:
            rects = []
        for r in rects or [fitz.Rect(0, 0, 0, 0)]:
            cap = nearby_caption(page, r)        # 文本层里找「图 x」「Figure x」，不是 OCR
            reg.append({"id": f"p{pno+1}img{bi:03d}", "type": "image",
                        "page": pno + 1, "bbox": [round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)],
                        "width": int(r.width), "height": int(r.height),
                        "has_nearby_caption": bool(cap),
                        "caption_text": cap,      # 来自文本层，允许保留
                        "note": "content not extracted by design"})
    return reg

def mark_furniture(blocks):
    """跨页文本指纹：边距区短块 + 出现 >=3 次 → furniture"""
    from collections import Counter
    import re
    def norm(t): return re.sub(r"\s+", "", t)[:60].lower()
    cand = [b for b in blocks if b["is_furniture_candidate"] and len(b["text"]) <= 60]
    cnt = Counter(norm(b["text"]) for b in cand)
    for b in blocks:
        if cnt.get(norm(b["text"]), 0) >= 3 and b["is_furniture_candidate"]:
            b["type"] = "furniture"
    return blocks

def checksum(content: str) -> str:
    return "sha1:" + hashlib.sha1(content.encode("utf-8")).hexdigest()
```

### B-2 文本层体检（S1.5，解决假文本层）

```python
import re, unicodedata

PUA   = lambda ch: 0xE000 <= ord(ch) <= 0xF8FF
OK    = re.compile(r"[\u4e00-\u9fff\u3000-\u303fA-Za-z0-9\s.,;:!?\-\u2014()\[\]{}\"'/%°]")

def text_layer_score(sample_texts) -> dict:
    raw = "".join(sample_texts)
    chars = [c for c in raw if not c.isspace()]
    if not chars:
        return {"score": 0.0, "reason": "EMPTY_TEXT"}
    pua    = sum(1 for c in chars if PUA(c)) / len(chars)
    nd     = raw.count("\ufffd") / len(chars)
    ok     = sum(1 for c in chars if OK.match(c)) / len(chars)
    counts = {c: chars.count(c) for c in set(chars)}
    repeat = max(counts.values()) / len(chars) if counts else 0
    bad = []
    if pua > 0.05:    bad.append("HIGH_PRIVATE_USE")      # 缺 ToUnicode 的典型症状
    if nd  > 0.02:    bad.append("HIGH_NOTDEF")
    if ok  < 0.85:    bad.append("LOW_GLYPH_COVERAGE")    # 抽出的是乱码而非可读文本
    if repeat > 0.30: bad.append("GLYPH_Collapse")         # 同一字符刷屏
    return {"score": round(ok - pua - nd, 4), "reason": ",".join(bad) or None,
            "detail": {"pua": pua, "notdef": nd, "coverage": ok, "repeat": repeat}}
```

### B-3 标题层级判定（S4，编号优先 + 连续性校验）

```python
import re
NUM_PATTERNS = [
    ("cn_chapter",  re.compile(r"^\s*(第\s*[一二三四五六七八九十百零\d]+\s*[章节章部篇])")),
    ("dot_decimal", re.compile(r"^\s*(\d+(?:\.\d+){0,3})[\s、\.．]")),
    ("cn_paren",    re.compile(r"^\s*([一二三四五六七八九十]+)[、\.]")),
    ("appendix",    re.compile(r"^\s*(附录|附\s*录|Appendix)\s*[A-Z]?")),
]
SENT_END = "。！？；：…”』】》"

def heading_candidates(blocks):
    for b in blocks:
        if b["type"] != "paragraph":
            continue
        t = b["text"].strip()
        if not t or len(t) > 60:
            continue
        if t[-1] in SENT_END:
            continue
        kind = number = None
        for k, pat in NUM_PATTERNS:
            m = pat.match(t)
            if m:
                kind, number = k, m.group(1) if m.groups() else m.group(0)
                break
        score = 0.4 + (0.35 if kind else 0) + (0.2 if b["font"]["bold"] else 0) \
                    + (0.25 if b["heading_candidate"] else 0)
        if score >= 0.7:
            b["type"], b["flags"]["numbered"] = "heading", bool(kind)
            b["flags"]["number_kind"], b["flags"]["number"] = kind, number
            b["confidence"], b["style_src"] = score, "heuristic"
    return blocks

def enforce_numbering_continuity(blocks):
    """跳号/回退 → 判为误检，降级正文。v1 的关键洞察，务必实现。"""
    last = {}
    for b in blocks:
        if b["type"] != "heading" or not b["flags"].get("numbered"):
            continue
        kind, num = b["flags"]["number_kind"], b["flags"]["number"]
        if kind == "dot_decimal":
            parts = [int(x) for x in str(num).split(".")]
            depth = len(parts) - 1
            prev = last.get("dot_decimal")
            if prev and depth == len(prev) - 1:
                if parts[:depth] != prev[:depth]:        # 兄弟节点跳号
                    b["type"], b["confidence"] = "paragraph", 0.2
                    continue
                if parts[depth] - prev[depth] > 1:       # 1.2 之后 1.7
                    b["type"], b["confidence"] = "paragraph", 0.2
                    continue
            last["dot_decimal"] = parts
        else:
            last[kind] = num
    return blocks
```

### B-4 硬换行合并 → 粘连段落切分（顺序不可颠倒）

```python
SENT_END = "。！？；：…”』】》."

def merge_hard_breaks(blocks):
    for b in blocks:
        if b["type"] in ("table", "code", "heading") or b.get("immutable"):
            continue
        lines, merged = b["raw_lines"], []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if merged:
                prev = merged[-1]
                joined_han = prev[-1] not in SENT_END and ln[0] not in "、。，；：）】》"
                hyphen = prev.endswith("-") and ln[:1].islower()
                if hyphen:
                    merged[-1] = prev[:-1] + ln          # 英文连字符断词
                elif joined_han:
                    merged[-1] = prev + ln               # 中文硬换行
                else:
                    merged.append(ln)
            else:
                merged.append(ln)
        b["raw_lines"] = merged
        b["text"] = "".join(merged) if _has_cjk("".join(merged)) else " ".join(merged)
    return blocks

def split_glued_paragraphs(blocks):
    """切：段内「句末标点 + 编号 + 主题词」→ 拆新块，打 low_confidence 标记给 L3。"""
    pat = re.compile(r"(?<=[" + SENT_END + r"])\s*(?=(?:第[\u4e00-\u9fff]+[章节]|[（(]?\d+[）).\、]))")
    for b in blocks[:]:
        if b["type"] != "paragraph" or len(b["text"]) < 200:
            continue
        parts = pat.split(b["text"])
        if len(parts) > 1:
            for i, p in enumerate(parts):
                nb = dict(b); nb["id"] = f"{b['id']}x{i}"; nb["text"] = p
                nb["provenance"]["modified_by"] = ["s4:glued_split"]; nb["confidence"] = 0.55
                blocks.insert(blocks.index(b) + 1 + i, nb)
            blocks.remove(b)
    return blocks

_has_cjk = lambda s: bool(re.search(r"[\u4e00-\u9fff]", s))
```

### B-5 pandoc AST → IR（S2-A，节选）

```python
import json, subprocess

def docx_to_ir(path, pandoc="vendor/bin/pandoc"):
    # 注意：不传 --extract-media（§6.2 不提取图片）。
    # pandoc 遇到图片会产出 Figure/Para[Image] 节点，这里只登记不落地。
    raw = json.loads(subprocess.run(
        [pandoc, path, "-t", "json", "--wrap=none"],
        capture_output=True, check=True, timeout=300).stdout)
    blocks = []
    for i, node in enumerate(raw["blocks"]):
        t = node["t"]
        if t == "Header":
            lvl, _attrs, inline = node["c"]
            blocks.append({"id": f"h{i:04d}", "type": "heading", "level": lvl,
                           "text": _plain(inline), "style_src": "docx-style", "confidence": 1.0})
        elif t == "Para":
            blocks.append({"id": f"p{i:04d}", "type": "paragraph", "text": _plain(node["c"][0])})
        elif t == "CodeBlock":
            attr, code = node["c"]
            blocks.append({"id": f"c{i:04d}", "type": "code",
                           "lang": (attr[1][0].split("=")[-1] if attr[1] else None),
                           "content": code, "immutable": True, "checksum": checksum(code)})
        elif t == "Table":
            html = _table_html(node)
            blocks.append({"id": f"t{i:04d}", "type": "table", "format": "html",
                           "content": html, "immutable": True, "checksum": checksum(html)})
        elif t == "LineBlocks":       # Word Shift+Enter 的硬换行：必须保留为 raw_lines
            blocks.append({"id": f"l{i:04d}", "type": "paragraph",
                           "raw_lines": [_plain(x["c"][0]) for x in node["c"]],
                           "text": "".join(_plain(x["c"][0]) for x in node["c"])})
    return blocks
```

### B-6 LLM patch 客户端与校验（S5-L3）

```python
import json, urllib.request

ALLOWED = {"insert_punct", "split_at", "set_level", "merge_lines"}

def apply_ops(block, ops, max_ops=8):
    if block.get("immutable"):
        return block, ["E3001"]           # 绝不透传 immutable
    if len(ops) > max_ops:
        return block, ["E3002"]
    text, errs = block["text"], []
    for op in sorted(ops, key=lambda o: -o.get("pos", 0)):   # 从后往前，避免位置漂移
        k = op.get("type")
        if k not in ALLOWED:
            errs.append(f"E3003:{k}"); continue
        p = op.get("pos", -1)
        if k != "set_level" and not (0 <= p <= len(text)):
            errs.append("E3004:pos"); continue
        if k == "insert_punct":
            text = text[:p] + op["char"] + text[p:]
        elif k == "split_at":
            block["children"] = [{"type": "paragraph", "text": text[p:].strip()}]
            text = text[:p].strip()
        elif k == "set_level":
            if op["level"] not in (1, 2, 3, 4, 5, 6):
                errs.append("E3005:level"); continue
            block["level"], block["style_src"] = op["level"], "llm"
    block["text"] = text
    block["provenance"]["llm_applied"] = True
    return block, errs

def ask_llm(client, block, heading_path, timeout=30):
    payload = {"model": client.model, "temperature": 0, "max_tokens": 400,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": SYS_PATCH},
                            {"role": "user", "content": json.dumps(
                                {"block_id": block["id"],
                                 "heading_path": heading_path,
                                 "text": block["text"][:600]}, ensure_ascii=False)}]}
    try:
        with client.chat(payload, timeout=timeout) as r:
            data = json.loads(r.read())["choices"][0]["message"]["content"]
    except TimeoutError:
        return block, ["W3001:timeout"]           # 超时=跳过，不放宽
    if data.get("block_id") != block["id"]:
        return block, ["E3006:bad_id"]
    return apply_ops(block, data.get("ops", []))
```

### B-7 校验与红线门禁（S6）

```python
def verify_immutable(ir_before_s5, ir_after_s6):
    bad = []
    for b in ir_after_s6["blocks"]:
        if not b.get("immutable"):
            continue
        orig = next(x for x in ir_before_s5["blocks"] if x["id"] == b["id"])
        if checksum(orig["content"]) != b["checksum"] or orig["content"] != b["content"]:
            bad.append(b["id"])
    return bad        # 非空 → 整文件回滚 + exit(20)
```

---

### B-8 API 与调度层最小骨架（M8 起手）

```python
# service/app.py  —— 只做编排，业务逻辑一律在 core/ 与 runtime/
from fastapi import FastAPI, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
import asyncio, json

from runtime.scheduler import Scheduler
from service.schemas import JobOut, OptionsIn, ErrorBody      # Pydantic v2 = 唯一契约源
from service.auth import require_token
from service.events import subscribe

app = FastAPI(title="doc2md", version="2.1.0")
sched = Scheduler.get()            # 单例，CLI 用同一个对象；serve 起时由它托管 worker 进程

app.mount("/", StaticFiles(directory="web/dist", html=True), name="web")

@app.post("/api/v1/jobs", status_code=202, response_model=list[JobOut],
          dependencies=[Depends(require_token)])
async def create_jobs(files: list[UploadFile], options: OptionsIn = None,
                      idempotency_key: str | None = None):
    if idempotency_key and (hit := await sched.find_by_key(idempotency_key)):
        return hit                                   # 幂等：重复提交返回同作业，不重算
    saved = [await sched.save_upload(f) for f in files]     # 先落 work/tmp + magic 校验
    try:
        return await sched.submit(saved, options or OptionsIn())
    except sched.CapacityError as e:                 # 资源门禁（§3.5 / FR-602）
        raise HTTPException(503, detail=ErrorBody(code="E5031", hint=str(e)),
                            headers={"Retry-After": "30"})
    except sched.IntakeReject as e:                  # 提交即拒收（§8.4 第 3 条）
        return [JobOut.rejected(e.item)]

@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: str, last_event_id: str | None = None,
                     _=Depends(require_token)):
    q = await subscribe(job_id, replay_from=last_event_id)   # 可重放（§7.4）
    async def gen():
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"; continue   # 心跳防断流
            yield f"id: {ev['id']}\nevent: {ev['type']}\n" \
                  f"data: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
            if ev["type"] in ("job_done", "job_failed"): break
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.patch("/api/v1/docs/{doc_id}/blocks/{block_id}")
async def patch_block(doc_id: str, block_id: str, body: PatchIn, _=Depends(require_token)):
    blk = await sched.load_block(doc_id, block_id)
    if blk.get("immutable"):                        # 后端硬闸（§8.6）
        raise HTTPException(409, detail=ErrorBody(
            code="E4091", message="该块为只读（表格/代码），不允许编辑",
            hint="如需修正请修改源文件后重转"))
    return await sched.edit_block(doc_id, block_id, body)   # 内含 S6 重渲染+校验+审计+新 revision
```

```python
# runtime/scheduler.py 的关键约束（实现时逐条对齐）
# 1) 队列持久化在 state.sqlite；进程重启 → 从 DB 恢复 queued/running（running 标 pending 重投）。
# 2) submit() 必须 <300ms 返回（NFR-4）：只做 hash、写 tmp、入队，绝不做解析。
# 3) 三桶加权轮转：small:medium:large = 5:3:1，且 large 桶有并发上限 1（防饿死）。
# 4) 事件写入 events 表（id 单调），subscribe() 按 last_event_id 重放 —— 前端重连不丢状态。
# 5) 与 CLI 共用同一个 Scheduler 实例：doc2md run 默认 submit 到本机服务（§4.6）。
# 6) 禁止在本文件出现任何转换逻辑；转换只在 worker 子进程里跑 core 的 stage 函数。
```

---

## 附录 C. S4/S5 规则库清单（18 条，每条独立可开关）

| ID | 规则 | 阶段 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| R-01 | 页眉页脚指纹去重（边距区 + 跨页 ≥3 次） | S4 | on | v1 保留 |
| R-02 | 页码模式清理（`^\s*[ⅰ-ⅿ\d]+\s*$`、`— \d+ —`、`Page \d+ of \d+`） | S4 | on | |
| R-03 | 编号驱动标题判定（章/节/多级小数/中文序号/附录） | S4 | on | |
| R-04 | **编号连续性校验**（跳号/回退 → 降级正文） | S4 | on | 最高价值规则 |
| R-05 | 字号聚类分层（无编号时，分桶/k-means k=3–5） | S4 | on | 输出 `confidence` |
| R-06 | 列表重建（行首标记 + indent 一致 + 行尾无句号 + 序号连续） | S4 | on | |
| R-07 | 硬换行合并（中文：行尾非句末标点；英文：连字符 + 小写） | S4 | on | **先合** |
| R-08 | 跨页/同缩进块合并（垂直间距 < 阈值 且 类型相同） | S4 | on | |
| R-09 | 粘连段落切分（句末标点 + 编号 → 拆，打 low_confidence） | S4 | on | **后切** |
| R-10 | 表格检测与隔离（网格线 / 等距多列 → immutable） | S4 | on | |
| R-11 | 跨页表格合并（列数一致 + 首行表头相似 ≥0.7） | S4 | off | 高频坑，默认关，评测后再开 |
| R-12 | 代码块识别（Mono/Courier / 缩进 ≥4 空格 / 关键字密度） | S4 | on | |
| R-13 | 脚注/尾注关联（`[1]` 上标 → `footnote` 块 + 链接） | S4 | off | 缺失则丢弃并入报告 |
| R-14 | 引号/括号配对保护（未闭合不许断句，报警） | S5-L1 | on | |
| R-15 | 白名单保护（URL、邮箱、版本号、小数点、LaTeX、时间戳） | S5-L1 | on | |
| R-16 | 长句切分（>80 字无标点 → 在最近合法连词前切 + 标记复核） | S5-L1 | on | |
| R-17 | 中英排版规范化（中英文间补空格、全半角标点统一） | S5-L1 | on | 参考 chinese-copywriting-guidelines |
| R-18 | 语气词/连词边界（了、啊、呢、吧、嘛 后；以及/并且/此外/其中 前） | S5-L1 | on | |

**与图片相关的处理不属于 S4 规则**（没有 R-19）：插图在 S2-D 只做登记（附录 B-1 `scan_images`），S4 的职责仅仅是**保证登记块不被合并、不被切分、不被 LLM 触碰**（等同 `immutable` 的搬运纪律），渲染时按 `s2.image_placeholder` 决定输出形式。**任何把图片内容转成文字、生成 `![]()`、或调用 OCR 的改动都属于范围变更**（§6.7 T-3）。

**开关的可观测性要求**：§8.3 P10 的前端规则开关页直接读这张表，因此**新增/删除规则必须同步改这张表**（§6.7 需求变更流程第 2 步）。

---

## 附录 D. L3 System Prompt 模板（`SYS_PATCH`）

```
你是文档结构修补器，不是改写器。规则：
1) 只输出 JSON，格式：{"block_id":"<原样返回>","ops":[{"type":"...","pos":N,...}]}
2) 允许的 op 仅四种：
   insert_punct {pos, char}   —— 在 pos 前插入一个标点
   split_at     {pos}         —— 在 pos 处把本块拆成两段
   set_level    {level}       —— 判定标题层级，1–6
   merge_lines  {pos}         —— 撤销一次错误切分（pos 为要合并的位置）
3) pos 是字符下标（0 起，按可见字符计，不是字节）。
4) 禁止：改写、翻译、补全、删除任何字符；禁止输出除上述字段外的键。
5) 不确定就返回 {"block_id":"...","ops":[]}。漏改比错改好。
6) 上下文（标题路径）仅用于判断层级，不要把它写进输出。
```

---

## 附录 E. 故障排查表

| 现象 | 平台 | 根因 | 处置 |
| --- | --- | --- | --- |
| `torch.backends.mps.is_available()` = False | M4 | 装了 x86 wheel / 在 Rosetta 终端里 | `uname -m` 断言 arm64；重装 `macosx_*_arm64` wheel |
| `llama-server` 打开被 macOS 拦阻 | M4 | quarantine | `xattr -dr com.apple.quarantine` + `codesign -s -` |
| 读桌面/文稿目录报 `PermissionError` | M4 | TCC | 工作目录移到 `~/doc2md`，或授予完全磁盘访问 |
| `pip install` 报 externally-managed-environment | M4 | Homebrew Python | 用 venv/uv，别用 `--break-system-packages` |
| `Too many open files` | M4 | `ulimit -n 256` | 及时 close + launchd `NumberOfFiles=4096` |
| 长任务半夜不推进 | M4 | 系统睡眠 | `pmset -c sleep 0` 或 `caffeinate -is` |
| 服务重启后作业全丢 | M4 | 未启用队列持久化 / 用了内存队列 | `scheduler` 必须落 `state.sqlite`（附录 B-8 约束 1）；`config [api] queue_persist=true` |
| 前端进度卡在 99% 不动 | 服务 | LLM 段无逐块事件 / SSE 被代理断流 | `progress` 事件需逐块节流发送 + 15s 心跳（§7.4）；浏览器侧检查 `Last-Event-ID` 重连是否生效 |
| 上传 500 文件后浏览器超时 | 服务 | 同步等待 / 单请求过大 | 提交必须 `202` 快速返回（NFR-4）；大目录改分批提交或走 `watch folder`（FR-111） |
| 前端能编辑表格 | 服务 | 后端未做 `immutable` 闸 | PATCH 必须返 `409`（§8.6）；前端不给入口只是第二道 |
| CLI 与 API 结果不一致 | 通用 | Web 层自己实现了一遍业务规则 | 违反 §3.7 分层纪律；跑 `doc2md check --cross-cli-api` 定位分叉点 |
| Docling 反复联网下载失败 | M4 | 权重目录命名不符 | 按 `~/.cache/docling/models/ds4sd--docling-models` 规则预置软链或本地路径参数 |
| 抽取有字但全是方框/怪符号 | 两者 | 缺 ToUnicode / Type3 字体 | S1.5 已拦；若放行则入 OCR 队列（v2 不做，标 `DEGRADED_TEXT`） |
| 表格全变成一行文字 | 两者 | 未启用 R-10 或 PDF 无网格线（无线表） | 补「等距列」检测分支；报告标记 `W1042` |
| 标题层级全 1 级 | 两者 | DOCX 用加粗代替样式（pandoc 无法识别） | 入口样式检查脚本 + 退回作者修正（v1 已建议，v2 落进 `doc2md check`） |
| T8 上跑 3 个文件就卡死 | T8 | swap / LLM 常驻 | 回 `workers=1` + LLM 用完即退 + 300 页分片 |

---

## 附录 F. Mac mini M4 部署手册（一次性，约 30 分钟）

```bash
# 1) 基础
brew install python@3.12 pandoc git                          # 或从离线 pkg 安装
xcode-select --install                                       # llama.cpp 编译需要（若用 brew 装则自动）

# 2) 工作目录（避开 TCC 保护路径）
mkdir -p ~/doc2md/{vendor,work,reports} && cd ~/doc2md

# 3) 离线安装 Python 依赖
python3.12 -m venv env && source env/bin/activate
pip install --no-index --find-links vendor/wheels/macos_arm64 -r requirements.lock

# 4) 二进制 + 去隔离
cp vendor/bin/arm64/{pandoc,llama-server} ~/doc2md/vendor/bin/
xattr -dr com.apple.quarantine ~/doc2md/vendor/bin/* && codesign -s - ~/doc2md/vendor/bin/llama-server

# 5) 常驻化：睡眠策略 + launchd
sudo pmset -c sleep 0 disablesleep 1
sudo sh -c 'echo "*" -n 4096' >> /etc/launchd.conf   # 或写 plist 的 SoftResourceLimits
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/md.doc2md.llm.plist <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>md.doc2md.llm</string>
  <key>ProgramArguments</key><array>
    <string>/Users/you/doc2md/vendor/bin/llama-server</string>
    <string>-m</string><string>/Users/you/doc2md/vendor/models/llm/qwen3-8b-instruct-q4_k_m.gguf</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8080</string>
    <string>-ngl</string><string>99</string><string>-c</string><string>8192</string>
    <string>--parallel</string><string>2</string>
    <string>--temp</string><string>0</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/you/doc2md/work/logs/llm.log</string>
  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
</dict></plist>
PL
launchctl load ~/Library/LaunchAgents/md.doc2md.llm.plist

# 5.1) API + Web 服务（同一进程内共享调度器，见 §7.1 / NFR-11）
cat > ~/Library/LaunchAgents/md.doc2md.serve.plist <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>md.doc2md.serve</string>
  <key>ProgramArguments</key><array>
    <string>/Users/you/doc2md/env/bin/doc2md</string>
    <string>serve</string><string>--bind</string><string>127.0.0.1:8765</string></array>
  <key>WorkingDirectory</key><string>/Users/you/doc2md</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/Users/you/doc2md/work/logs/serve.log</string>
  <key>StandardErrorPath</key><string>/Users/you/doc2md/work/logs/serve.err.log</string>
  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
</dict></plist>
PL
launchctl load ~/Library/LaunchAgents/md.doc2md.serve.plist
# 依赖顺序：llm 先于 serve（serve 的 doctor 会探测 :8080，失败只降级不崩）
# 对外只监听 127.0.0.1；如需同事访问，走 §8.6 的反向代理 + 本机防火墙白名单

# 6) 自检（必过）
doc2md doctor      # 期望：arch=arm64, metal=true, mps_available=True, ulimit>=4096, disk_free>=20G
doc2md bench --corpus work/golden/src --stages s2,s4,s5   # 记录到 ADR-07 作为基线
```

---

## 附录 G. 基准测试脚本（吞吐与内存，上线必测）

```python
# doc2md bench 的实现要点
import time, subprocess, statistics, os, resource, json, pathlib

STAGES = ["s1", "s1_5", "s2", "s3", "s4", "s5", "s6"]

def measure(corpus: pathlib.Path, workers: int, profile: str):
    rows = []
    for f in sorted(corpus.glob("*")):
        if f.suffix.lower() not in (".pdf", ".docx"):
            continue
        pages = json.loads(subprocess.run(
            ["python", "-m", "doc2md", "inspect", "--file", str(f), "--json"],
            capture_output=True).stdout or b"{}").get("pages", 0)
        t0, before = time.perf_counter(), resource.getrusage(resource.RUSAGE_SELF)
        subprocess.run(["python", "-m", "doc2md", "run", "--in", str(f), "--out", "work/bench",
                        "--profile", profile, "--workers", str(workers)], check=True)
        dur = time.perf_counter() - t0
        rows.append({"file": f.name, "pages": pages, "sec": round(dur, 2),
                     "pps": round(pages / dur, 2) if dur else None,
                     "peak_rss_mb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // (1024*1024)})
    return {
        "n_files": len(rows),
        "total_pages": sum(r["pages"] for r in rows),
        "throughput_pps": {"median": statistics.median([r["pps"] for r in rows if r["pps"]]),
                           "p95": sorted([r["pps"] for r in rows if r["pps"]])[int(len(rows)*0.95)]},
        "peak_rss_mb": max(r["peak_rss_mb"] for r in rows),
        "rows": rows,
    }
```

同时在 Mac 上采集：`memory_pressure`、`sysctl vm.swapusage`、`powermetrics --samplers cpu_power,gpu_power -i 1000`（需 sudo）。判据：`peak_rss_mb` 超过 §3.5 预算行、或 swap 持续增长 → 降 `workers` 或关 `llm`，**不要靠调模型参数硬扛**。

---

## 附录 H. 决策记录（ADR）

| ID | 决策 | 理由 | 重议条件 |
| --- | --- | --- | --- |
| ADR-01 | 保留 v1 的「中间件 + 统一 IR + 解析/修复解耦」 | 离线可调试性的唯一解 | 永不 |
| ADR-02 | 双平台一份代码 + `platform_caps` 分叉 + profile 裁剪 | 避免维护两套逻辑导致行为漂移；CLI/API/Web 三入口同内核 | 出现第三个平台（Linux）时补 caps |
| ADR-03 | PDF 引擎唯一采用 PyMuPDF（AGPL，内部自用），**不实现 MIT 替代后端** | 已确认不对外分发 → AGPL 义务不触发；省 3–5 天。`ExtractorBackend` 接口位保留 | 触发 §6.7 **T-1**（对外/跨部门提供服务）时重评 |
| ADR-04 | 引入 S1.5 文本层体检 | 假文本层是静默失败主因 | 永不（只调阈值） |
| ADR-05 | L2（Docling/Marker MPS）默认关闭，`enhanced` 才解锁 | 解锁 ≠ 有收益，需 A/B 数据 | F1 提升 ≥3% 且吞吐降幅可接受则默认开 |
| ADR-06 | LLM 档位：minimal 用 0.6B（默认关），standard 用 4B，**enhanced（生产基线）用 8B Q4_K_M 常驻** | 性价比；0.6B 在断句歧义上能力不足；已确认按较高性能设定阈值，32GB 统一内存可容纳 8B 常驻 | 黄金集上更优的替代模型出现，或目标机型内存低于 24GB |
| ADR-07 | 吞吐与内存以本机 `bench` 结果为准，文档估算值仅作设计输入 | 社区数字不可信 | 换机/换系统版本 |
| ADR-08 | 不启用 OCR 与标点恢复模型 | 范围已排除扫描件；无标点文本极少 | 输入源混入语音稿 |
| ADR-09 | 生产路径用 `llama-server` 而非 Ollama | Metal 原生、少一层封装、社区测得更快 | — |
| ADR-10 | **阈值按较高性能档（M4 32GB / `enhanced` / `workers=5`）设定为生产基线** | 日均量不定 → 按峰值设计，队列削峰优于拒绝服务；低档只作兼容路径 | 触发 T-6（换硬件）或 T-7（日量 >5 万页） |
| ADR-11 | **不提取图片、不做 OCR**（v1 原计划的媒体抽取被撤销） | 本次明确排除图片/扫描件；省掉落盘、命名、体积与许可连锁复杂度 | 触发 T-2 / T-3，需重开范围评估 |
| ADR-12 | API 采用**作业制 + SSE**，不做同步转换、不用 WebSocket | 长任务同步必超时；单向推送 + `Last-Event-ID` 重放已覆盖全部需求，WebSocket 只增加鉴权与运维面 | 出现「服务端需接收客户端高频指令」的真实需求时 |
| ADR-13 | 前端主入口为 Web 控制台，核心价值在**审阅与修订**而非转换按钮 | 问题集中在少数文档，人工高效发现并修好才是时间消耗所在（U3/U4） | 若实际使用中审阅页无人访问 → 降级为只读结果页 |
| ADR-14 | 前端由 `openapi.json` 生成类型，禁止手写 DTO | 三入口契约单一源，防「前端自己抄一份类型」造成漂移（§6.7） | 永不 |

---

## 附录 I. 术语

`IR`（统一中间态 JSON）· `furniture`（页眉页脚页码等版式噪声）· `immutable`（禁止下游改写的块）· `ops/patch`（LLM 输出的最小改动指令）· `profile`（minimal/standard/enhanced 能力档）· `L0/L1/L2/L3`（规则 / 规则修补 / 结构模型 / LLM patch 四层）· `golden set`（黄金评测集）· `红线指标`（触碰即回滚的验收门禁）· `job`（一次提交的转换作业，API 的最小单位）· `doc_id`（文件内容 sha256，跨作业稳定）· `revision`（同一 doc 的一次产物版本，人工修订产生新版本）· `bucket`（队列页数分桶 small/medium/large）· `provenance`（块被哪些规则/模型改动过的来源链）· `SSE`（Server-Sent Events，服务端到浏览器的事件流）· `登记块`（`type=image`，只记录存在与位置、不含内容）。

---

*本文档基于 v1 方案（2026-10-12「文本型 PDF 与 DOCX 转 Markdown 离线管线」）扩展为 v2.1（新增产品形态、PRD 需求基线、API 与前端设计，并按「内部自用 / 不处理图片与扫描件 / 按较高性能设定阈值」三项确认调整全篇），其中工具许可、平台能力、芯片规格均已核对公开资料，性能数字为本管线的设计输入值，须在目标机上以附录 G 脚本实测替换。*
