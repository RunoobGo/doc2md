# 文本型 PDF / DOCX → Markdown 离线转换管线 v2.0

## 可行性评估 + Mac mini M4 适配 + 方案完善（可直接指导 AI Agent 开发）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.0（对 v1「Win11 / i5-12500T / 8G / 无 GPU 内网」方案的评估与重构） |
| 编写日期 | 2026-10-13 |
| 适用目标平台 | ① Windows 11 + i5-12500T + 8GB（下称 **T8 低配档**）<br>② Mac mini M4 / M4 Pro，16–32GB 统一内存（下称 **M4 档**） |
| 处理范围 | **仅文本型（born-digital）PDF 与 DOCX**。扫描件、图片型 PDF、公式密集型学术论文不在 v2 范围内 |
| 交付形态 | 离线、单机、可插拔流水线 + 每阶段落盘中间产物 + 校验报告 |
| 文档定位 | 既是决策依据（可行性/风险），也是工程规格（架构/schema/接口/任务卡） |

---

## 0. 本文档怎么用（AI Agent 请先读这一节）

本文档按「决策 → 规格 → 实施」三段组织。不同角色按需取用：

- **做决策的人**：读 §1（结论摘要）、§2（可行性）、§3（M4 适配性）、§4.1（风险登记册）。
- **写代码的 Agent**：只需读 §4（工程规格）、§5（实施计划与任务卡）、附录 B（代码骨架）、附录 C（规则库）、附录 D（Prompt 契约）。**§5 的任务卡是唯一的开工入口**，每张卡自带输入/输出/验收标准，卡内已声明依赖，按 `M0 → M6` 顺序执行即可，不要跨卡并行改接口。
- **验收的人**：读 §4.8（质量指标与评测）与附录 G（基准测试脚本）、附录 H（决策记录）。

三条硬约束，任何 Agent 不得自行放宽：

1. **不引入 OCR、不引入视觉版面模型**，除非任务卡明确要求（T8 档禁止，M4 档需显式开启 `profile=enhanced`）。
2. **`immutable=true` 的块（表格/代码/公式）在任何阶段都不得被改写**，只能被搬运与被校验。
3. **依赖版本一经锁定即冻结**（`requirements.lock` / `uv.lock`），任何升级必须重跑 §4.8 的黄金集回归并取得通过记录。

---

## 1. 结论摘要（TL;DR）

**v1 方案整体可行，工程方向正确，评级 A−；但存在 10 个必须补的缺口，补齐后在两台目标机上都能落地。**

v1 最有价值的三个设计决定，v2 全部保留并强化：

1. **中间件（每步落盘）+ 统一中间态 JSON**。这让离线环境里「转换效果不满意」从一个无法定位的黑盒问题，变成可 diff、可单步重跑、可回滚的工程问题。这是整个方案的地基，不要因为嫌麻烦而跳过。
2. **解析器与修复器解耦**（S2 只忠实抽取，S4/S5 只操作 JSON）。这一条决定了后续换解析引擎的成本，也决定了回归测试是否可写。
3. **`immutable` + checksum 校验**。表格与代码被 LLM「热心改写」是此类管线最隐蔽、破坏最大的故障模式，v1 用红线指标（篡改率 = 0）来守，非常专业，v2 把它升级为强制的门禁（gate），失败即整文件回滚并进人工队列。

v1 需要修正的三个判断：

1. **「文本型 PDF 就不要碰带模型的解析器」在 T8 档成立，在 M4 档不成立**。M4 有 Metal GPU（10 核 GPU + 16 核神经网络引擎 + 120GB/s 统一内存带宽），Docling 支持 Apple Silicon MPS 加速，MinerU 的 pipeline 后端可把 `device-mode` 设为 `mps`，Marker 明确支持 CPU/MPS。也就是说在 M4 上，「模型辅助的结构识别」从零风险区间的成本变成了可接受成本，应当作为**可选增强层**提供，而不是永久排除。
2. **「LLM 只输出 patch 不改写」的铁律方向完全正确，但 0.6B 模型档位偏低**。Qwen3-0.6B 在断句歧义和标题层级判断上能力有限。T8 档可继续用 0.6B（且应当默认关闭）；M4 档建议默认 4B，`enhanced` 档上 8B，收益远大于额外的 2–4 秒。
3. **漏了一个关键的许可合规问题**：v1 的核心依赖 PyMuPDF 采用 **AGPL-3.0（或向 Artifex 购买商业授权）**。个人/内部自用的离线管线一般不触发 AGPL 的分发义务（不对外分发、不做 SaaS 时风险低），但**一旦这套管线作为产品交付给客户，AGPL 会传染整个项目**。v2 增设「许可合规」维度，给出 MIT/BSD 替代路线（pdfplumber / pdfminer.six / pikepdf）与双后端抽象。

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
| PyMuPDF (`fitz`) | **AGPL-3.0** 或 Artifex 商业授权 | Win / macOS（含 `universal2` wheel）/ Linux | T8+M4 默认 PDF 抽块引擎；**产品化交付时必须评估许可风险或购授权** |
| pdfplumber / pdfminer.six | **MIT** | 纯 Python，跨平台无差异 | AGPL 替代路线；表格线框检测能力好，速度约为 PyMuPDF 的 1/3–1/10 |
| pypdf | **BSD** | 纯 Python | 只做元数据/页面操作，不做主抽取 |
| pikepdf | **MPL-2.0** | 基于 QPDF，C++ 预编译 | 读取 PDF **结构树（StructTreeRoot）** 的合规入口 |
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
| 3 | **AGPL 合规未评估** | 产品化时整项目被迫开源或需购买授权 | §4.10 + ADR-04：`ExtractorBackend` 抽象，提供 `agpl` / `mit` 双实现与交付形态判定表 |
| 4 | **并发与吞吐在 M4 上浪费** | M4 十核只用一核，产能低 4–6× | §4.9 三段流水 + 分片并行；T8 保持 `workers=1` |
| 5 | **无幂等 / 断点续跑 / 目录级去重** | 内网批量任务中断后要全量重跑 | 内容寻址（sha256 前缀）作为任务 ID，`state.sqlite` 记录阶段状态，重跑天然幂等 |
| 6 | **表格输出格式未定**（v1 存 HTML，但下游可能是 RAG/预览器） | HTML 表格在部分渲染器/RAG 分块里不可读 | `table.format` 可选 `gfm` / `html` / `csv+占位`，按 `profile` 与消费者决定，原文永远保留一份 |
| 7 | **评测集与指标不可计算**（「断句 F1 ≥ 93%」没说怎么算） | 指标无法证伪，回归形同虚设 | §4.8 给出每个指标的**精确定义 + 计算脚本 + 抽样分层法** |
| 8 | **图片/插图处理缺失** | 图文混排文档丢图，或图片文件名与正文错位 | 新增 S2-D 媒体抽取 + caption 关联规则 + `out/media/` 命名规范 |
| 9 | **未处理 DOCX 特有结构**：脚注/尾注、批注、文本框、SmartArt、域代码（TOC/交叉引用）、多级列表实际层级 | 目录被当正文、列表层级塌陷、批注内容丢失且无提示 | S2-A 补充「OOXML 语义元数据通道」（python-docx 读 `styles.xml`/`numbering.xml`），并在报告里显式列出丢弃项 |
| 10 | **运维视角缺失**：Mac mini 长期批处理的睡眠、权限、日志轮转、磁盘写放大 | 任务半夜停摆、首次运行卡在隐私授权 | §3.4 平台差异清单 + S0 的 `setup_mac.sh` / launchd 配置 |

### 2.5 可行性总评

| 维度 | T8（12500T/8G/无 GPU） | M4 Mac mini（16–32GB，Metal） |
| --- | --- | --- |
| 纯规则管线（L0） | **可行，A** | 可行且快 3–5× |
| LLM patch 层（L1） | **勉强可行**（0.6B、按需启停、必须单文件串行） | **可行，A**（Metal 加速，4B/8B 可用，可常驻） |
| 结构识别模型层（L2，Docling/Marker） | **不可行**（torch + 500MB+ 权重 + 内存挤占） | **可行，B+**（MPS 加速，需按文档类型验收） |
| 吞吐（100 页常规文档，L0+L1） | 2–5 分钟（v1 估计，量级合理） | 预计 30–90 秒（待实测校准） |
| 一次性部署成本 | 半天（wheel + GGUF 拷贝） | 半天（arm64 wheel + 签名/Gatekeeper 处理） |
| 许可风险 | 中（PyMuPDF AGPL） | 中；可切 Marker(Apache)/pdfplumber(MIT) 路线降为低 |

---

## 3. Mac mini M4 适配性分析

### 3.1 硬件画像

Mac mini（2024，M4）的官方规格：Apple M4 芯片为 **10 核 CPU（4 性能核 + 6 能效核）、10 核 GPU、16 核神经网络引擎、120GB/s 内存带宽**，标配 **16GB 统一内存**，可选 24GB 或 32GB；M4 Pro 版本为 12 核 CPU（8P+4E）/16 核 GPU、**273GB/s 内存带宽**、标配 24GB，可选 48GB/64GB；存储 256GB 起（M4）/512GB 起（M4 Pro），可选配 10Gb 以太网。

与本管线相关的三个要点：

1. **4P+6E 的混合架构**：CPU 密集的单页解析属于可并行短任务，适合丢到 E 核集群；LLM 推理与 tokenizer 预热适合 P 核。macOS/GCD 会自动调度，工程侧只需控制并发度，不必手动绑核。
2. **统一内存 + 120GB/s 带宽**：这是 LLM 与 GPU 模型「可用」的根本原因——没有独立显存墙，Metal 与 CPU 共享同一片内存。也意味着 **模型驻留与文件解析争抢同一份内存预算**，16GB 机型要按 §3.5 分配。
3. **内置 MediaEngine 与本任务无关**：本管线不用视频/图像编解码加速，不必为此选 M4 Pro。

**选型建议**：若这台机器专职跑文档转换，**M4 + 32GB（2TB SSD）** 性价比最好——比 16GB 多出的内存让 Docling/Marker 的 MPS 路径与 8B LLM 可以共存，比 M4 Pro 省下的带宽预算对纯文本管线收益极小。若只做 L0+L1（规则 + 小模型 patch），16GB 足够。

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

- **L1 解锁 · LLM 常驻 + 升档**：Metal 后端下 `llama-server` 常驻内存约 3–4GB（8B Q4 量级），换来 patch 质量显著提升；T8 上「用完即退」的做法在 M4 上可改为「常驻 + 空闲 300s 自动退出」。社区在 16GB M4 上以 Ollama 跑 7B 级模型的实测报告为约 42 token/s、整机内存占用约 7GB；llama.cpp 直连通常比 Ollama 更快（社区基准给出 M4 上 15%–22% 的优势），生产路径建议直接用 `llama-server`。这些数字来自社区实测，**上线前必须用附录 G 的脚本在本机重测**。
- **L2 解锁 · 结构识别增强器（可选）**：Docling 支持在 Apple Silicon 上启用 MPS；MinerU 的 pipeline 后端可通过 `magic-pdf.json` 的 `"device-mode": "mps"` 启用（其公式识别不走 MPS，若不需要公式可关闭以提速）；Marker 明确兼容 GPU/CPU/MPS 并可用 `TORCH_DEVICE=mps` 强制指定。三者在文本型 PDF 上的**净收益必须用黄金集实测**——v1 关于「原生 PDF 上提升有限」的判断在 M4 上同样可能成立，解锁 ≠ 默认启用。
- **L3 解锁 · 标点恢复模型**：技术上可行（torch CPU/MPS），但文本型 PDF 里「整段无标点」的情况极少，**v2 仍不启用**，仅当输入源混入语音转写稿时开支路。
- **并发解锁**：M4 上可采用「抽取 / 规则 / LLM」三段流水 + 多进程分片，详见 §4.9。

### 3.4 macOS 平台差异清单（工程必读，照做即可）

| 项 | 现象 | 处置 |
| --- | --- | --- |
| **架构** | 同一条 `python3` 可能是 arm64 或 x86_64（Rosetta 终端） | 脚本首行 `arch=$(uname -m)` 断言等于 `arm64`；Python 内用 `sysconfig.get_platform()` 校验；装错架构会静默回退 CPU、torch 报 `Illegal instruction` 或找不到 MPS |
| **torch wheel** | 通用 x86 wheel 触发 Rosetta，Metal 加速失效 | 必须装 `macosx_*_arm64` wheel；验证 `torch.backends.mps.is_available()` 返回 `True` 才允许进入 L2 |
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

### 3.5 内存预算（Mac mini M4，16GB 统一内存）

Metal 下 GPU 与 CPU 共用这 16GB，所以「模型驻留」和「文档处理」是同一份预算，必须排他使用。

| 场景 | macOS 基线 | 管线主进程 | LLM 驻留 | 可选 L2 模型 | 峰值合计 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| **P0 · L0 纯规则** | 2.0–3.0GB | 0.3–0.8GB | 0 | 0 | ≤ 3.8GB | 极安全 |
| **P1 · L0+L1（4B Q4 常驻）** | 2.0–3.0GB | 0.4–1.0GB | 3.0–3.5GB | 0 | ≤ 7.5GB | 安全，推荐日常配置 |
| **P2 · L0+L1（8B Q4 常驻）** | 2.0–3.0GB | 0.4–1.0GB | 5.5–7.0GB | 0 | ≤ 11GB | 可用，需 `swap` 观察 |
| **P3 · L2 Docling/Marker（MPS）** | 2.0–3.0GB | 0.5–1.2GB | 3.0–3.5GB | 2.5–4.5GB | ≤ 12GB | 24GB 机型建议；16GB 机型跑 ≤200 页文档 |
| **P4 · 超大文档（>300 页）分片** | 2.0–3.0GB | 1.0–2.5GB | 3.0–3.5GB | 0 | ≤ 11GB | 分片阈值必须生效 |

三条规则：

1. **LLM 与 L2 模型不同时预加载**：`enhanced` profile 下按「L2 解析 → 释放 → L1 patch」串行，或让 L2 与 L1 分处两个批次队列。
2. **给 OS 留 3GB**：`memory_pressure` 持续 `warn` 时降级为纯规则模式（`degrade_to_L0` 开关），宁可质量下降也不要 OOM 杀掉批次。
3. **256GB 存储机型注意写放大**：中间产物按 §4.4 做 zstd 压缩，5000 篇文档的 S2/S3 产物可从 ~40GB 压到 ~6GB。

### 3.6 吞吐估算（必须实测校准）

**以下数字是设计输入值，不是承诺值。** 上线前用附录 G 的基准脚本在真实语料上重测并写进 ADR-07。

| 阶段 | T8（i5-12500T） | M4（单核等价） | M4（`workers=4` 流水） |
| --- | --- | --- | --- |
| S1 探测 + S1.5 体检 | 5–20 页/s | 15–60 页/s | 60–200 页/s |
| S2-B PyMuPDF 抽块 | 15–40 页/s | 30–90 页/s | 100–300 页/s |
| S2-A pandoc AST（DOCX） | 1–5 万字符/s | 3–10 万字符/s | 近线性 |
| S4 规则重建 | 5–15 页/s | 15–40 页/s | 50–150 页/s |
| S5-L1 LLM patch | 1–3 页/s | 8–20 页/s（4B/Metal） | 20–45 页/s |
| S6 渲染 + 校验 | 20–50 页/s | 60–150 页/s | 高 |
| **100 页常规文档端到端** | **2–5 分钟** | **25–60 秒** | **8–20 秒** |
| **日产能（8 小时）** | ~1000–3000 页（含 LLM） | ~3 万页（L0） | ~10 万页（L0+L1） |

### 3.7 双平台统一策略（一份代码，两档能力）

```
core/              # 纯逻辑：schema、规则、渲染、校验 —— 禁止 sys.platform 判断
platform_caps/     # 唯一允许平台分叉的地方
  base.py          # CapProbe: has_metal, has_cuda, mem_total, cores, disk_free
  profiles.py      # minimal / standard / enhanced 三档 profile 的解析与裁剪
backends/
  pdf_agpl.py      # PyMuPDF 实现（默认）
  pdf_mit.py       # pdfplumber + pdfminer.six 实现（许可替代路线）
  docx_pandoc.py   # pandoc AST 通道（默认）
  docx_mammoth.py  # BSD 兜底通道
  struct_layout.py # L2: Docling / Marker / MinerU 适配器（仅 enhanced）
runtime/
  llm_server.py    # llama-server 生命周期（Metal on macOS / CPU on T8）
```

profile 决策表（`CapProbe` 自动判定，可被 `--profile` 覆盖）：

| profile | 触发条件 | 启用的能力 | 典型机器 |
| --- | --- | --- | --- |
| `minimal` | 内存 < 12GB **或** 无 Metal/CUDA | L0 规则 + 可选 0.6B LLM（按需启停） | T8 (12500T/8G) |
| `standard` | 内存 ≥ 14GB | L0 + L1（4B 常驻，Metal/CPU 自适应） | Mac mini M4 16GB |
| `enhanced` | 内存 ≥ 22GB 且 MPS 可用 | L0 + L1（8B）+ L2 结构识别（Docling/Marker，MPS） | Mac mini M4 32GB / M4 Pro |

---

## 4. 完善后的方案（工程规格）

### 4.1 架构总览（v2，S0–S7）

```
                        ┌──────────────────────────────────────────┐
  输入目录 ──► S1 探测/分流 ──► S1.5 文本层体检 ──┐                    │
                        │                        ├─► 拒收队列(reject)│
                        └────────────────────────┘                    │
                                                             │
        ┌──────────────────┬─────────────────┬───────────────┴───────┐
        ▼                  ▼                 ▼                       ▼
  S2-A DOCX→pandoc    S2-B PDF→PyMuPDF   S2-C PDF→结构树        S2-D 媒体抽取
       AST JSON          逐块+行           (Tagged PDF)          图片/OLE
        └──────────────────┴─────────────────┴───────────────────────┘
                                    ▼
                    【S3】统一中间态 IR JSON（唯一契约）
                                    ▼
              【S4】结构重建（规则：去噪/标题层级/列表/合并/切分/表格隔离）
                                    ▼
              【S5】语义修补（L1 规则断句 → L2 可选结构识别增强 → L3 LLM patch）
                                    ▼
              【S6】渲染 Markdown + 校验（immutable checksum + 红线门禁）
                                    ▼
              【S7】下游适配与发布（分块/RAG 预处理、front-matter、报表、归档）
```

新增/变更点：

- **S1.5 文本层体检**：解决「假文本层」静默失败（缺口 #1）。
- **S2-C 结构树通道**：Tagged PDF 的权威语义优先于字号启发式（缺口 #2）。
- **S2-D 媒体通道**：图片落盘 + caption 关联（缺口 #8）。
- **S5 内部三小层**：L1 规则 → L2 可选模型结构识别 → L3 LLM patch，全部按 profile 裁剪。
- **S7**：把「Markdown 不只是给人看，还给 RAG/搜索/预览看」显式化。

### 4.2 风险登记册

| ID | 风险 | 概率 | 影响 | 缓解 | 责任阶段 |
| --- | --- | --- | --- | --- | --- |
| R1 | PyMuPDF AGPL 传染（产品化） | 中 | 高 | 后端抽象 + MIT 路线 + 交付形态判定表 | S2 |
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

### 4.4 目录结构与产物保留策略

```
~/doc2md/
  project/
    core/  platform_caps/  backends/  runtime/  rules/  render/  cli/  tests/
    pyproject.toml  requirements.lock
    config/
      base.toml  profile.minimal.toml  profile.standard.toml  profile.enhanced.toml
  vendor/
    bin/            # pandoc, llama-server, soffice（Mac 为 .app/universal 二进制）
    models/
      llm/          # qwen3-4b-instruct-q4_k_s.gguf, qwen3-0.6b-q8_0.gguf
      layout/       # docling / surya 权重（仅 enhanced）
    wheels/         # 离线 wheelhouse（win_amd64 / macosx_arm64 / universal2 分目录）
  work/             # 运行时，可整体删除重建
    in/  s1/  s2/  s3/  s4/  s5/  out/  media/  logs/  tmp/
    state.sqlite    # 任务状态机（幂等续跑）
    golden/         # 黄金集：src/ expected/ manifest.csv
  reports/          # 汇总报表与回归结果（保留全部历史）
```

保留策略（`config.base.toml`）：S2/S3 产物 `zstd(3)`，保留 7 天；S4/S5 保留 30 天；`out/` 永久；`golden/` 永久。磁盘余量 < 5GB 时任务拒绝启动（门禁）。

### 4.5 配置样例（`config/base.toml`，节选）

```toml
[run]
profile   = "auto"        # auto | minimal | standard | enhanced
workers   = "auto"        # minimal=1, standard=2, enhanced=3
max_pages_per_task = 300  # 超过则按章节切分
resume    = true
log_level = "INFO"

[s1]
trusted_ext = false                # 只看 magic，不信任扩展名
legacy_doc  = "convert"            # .doc/.wps -> .docx via LibreOffice；或 "reject"
max_mb      = 200

[s1_5]                             # 文本层体检（缺口 #1）
enabled = true
min_glyph_coverage = 0.85          # CJK+ASCII 可打印字符占比下限
max_pua_ratio      = 0.05          # U+E000-F8FF 私有区比例上限
max_notdef_ratio   = 0.02
max_single_glyph_repeat = 0.30     # 同一字符反复出现 = 缺 ToUnicode
sample_pages = [0, "mid", -1]
on_fail = "reject"                 # reject | degrade_to_ocr_queue（后者 v2 不实现）

[s2]
pdf_backend = "agpl"               # agpl(pymupdf) | mit(pdfplumber)
prefer_struct_tree = true          # Tagged PDF 优先（缺口 #2）
extract_media = true
media_dpi_cap = 150                # 渲染位图上限，控体积

[s4]
drop_furniture = true
heading_numbering = ["cn_chapter", "dot_decimal", "cn_paren"]
heading_continuity_check = true    # 跳号即降级为正文（v1 最有价值的规则）
merge_hard_break = true            # 先合
split_glued_paragraphs = true      # 后切（顺序不可颠倒）
table_output = "html"              # gfm | html | csv+placeholder

[s5]
rules = true
llm = "auto"                       # off | patch（唯一支持的模式）
llm_model = "qwen3-4b"
llm_window = 600                   # 滑窗字数
llm_overlap = 80
llm_timeout_s = 30                 # 超时 = 跳过并记录，绝不放宽
llm_temperature = 0.0
struct_model = "auto"              # off | docling | marker（仅 enhanced）

[s6]
enforce_immutable = true           # 校验失败 → 整文件回滚 + 进人工队列
emit_html_diff = true
```

### 4.6 CLI 契约（Agent 按此实现，不要自创接口）

```
doc2md run   --in DIR|FILE --out DIR [--profile X] [--workers N] [--only-ids ...] [--dry-run]
doc2md stage --from s3 --to s5 --ids <file.txt>   # 单步重跑（调试核心能力）
doc2md inspect --id <doc_id> [--block b0042]      # 打印 IR 片段与 diff
doc2md check   --golden work/golden               # 黄金集回归，输出指标表 + 是否过红线
doc2md bench   --corpus DIR [--stages s2,s4,s5]   # 附录 G 吞吐基准
doc2md doctor                                     # 平台自检：架构/MPS/ulimit/TCC/内存/磁盘
doc2md serve-llm --model qwen3-4b [--start|--stop|--status]
```

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
| base | Qwen3-4B Q4_K_S（~2.5GB） | 不推荐 | **推荐默认** | 推荐 | 断句/层级性价比最高 |
| plus | Qwen3-8B Q4_K_M（~5GB） | 不可行 | 可用（观察 swap） | **推荐** | 复杂公文、层级判定准确率明显更好 |

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

**并发模型（两平台分化）**：

- T8：`workers=1`，单进程串行，LLM 按需启停。**不要尝试并发，8GB 下会 swap。**
- M4：三段流水（生产者-消费者），阶段间用有界队列（`maxsize = 2×workers`）形成背压：

```
[抽取进程池 x N]  →  s3 目录  →  [规则进程 x M]  →  s5 目录  →  [LLM 客户端 x K]  →  [渲染 x 1]
```

  - `N=2–3`（CPU 密集，Metal 无关），`M=2–3`，`K=1–2`（LLM 推理单点，多客户端共享一个 `llama-server`）。
  - **PyMuPDF 限制**：MuPDF 上下文非线程安全 → **一个 `fitz.Document` 只在创建它的进程内使用**，禁止跨进程传对象；每进程独立 `open`/`close`。
  - `enhanced` profile 下 L2 结构识别（Docling/Marker）单独一批队列，与 L1 LLM **不并行**（内存争抢）。

### 4.10 安全、许可与交付合规

1. **全程离线**：所有网络调用默认禁止。代码里加「离线断言」——运行时 monkeypatch `socket.socket` 抛异常（除 `127.0.0.1` / `localhost`）。这条测试要进 CI，防止某天有人偷偷加了 HuggingFace 在线下载。
2. **PyMuPDF AGPL 判定表**：

| 交付形态 | 是否触发 AGPL 义务 | 建议 |
| --- | --- | --- |
| 内部自用、不分发（T8/M4 本机跑） | 一般不触发（AGPL §13 关注网络服务与分发） | 可用，留档说明 |
| 把工具/管线交付给第三方（含内网客户） | **触发**：整个作品需 AGPL 开源 | 改用 `pdf_mit` 后端（pdfplumber/pdfminer.six）或购买 Artifex 商业授权 |
| 作为 SaaS/API 对外服务 | **触发**网络服务条款 | 同上 |
| 只用 pandoc + mammoth（BSD），不用 PyMuPDF | 无 AGPL 传染（pandoc 为独立进程） | 许可最干净的兜底路线 |

   **实现要求**：`--license-check` 子命令扫描依赖树并输出许可清单（`vendor/licenses.txt`），交付前必跑。
3. **模型许可**：LLM GGUF（Qwen Apache-2.0 系）、Marker 权重（修改版 Open Rail-M，个人/研究/中小初创可用，商用需核对门槛）、MinerU（含 AGPL 依赖）。`enhanced` profile 引入这些权重前，必须在 ADR 里记录许可结论。
4. **数据**：输出目录权限 `700`；报告 HTML 里不嵌入原文正文片段（只嵌 block_id 与偏移），避免二次扩散；日志不含敏感正文。
5. **资源破坏防护**：管线**只读** `work/in/`，绝不写回原目录；删除只允许 `tmp/` 与超过保留期的 `work/s*/`。

---

## 5. 实施计划（AI Agent 任务卡）

总量：单人 3 周（v1 的节奏合理，v2 因新增 S1.5/S2-C/S7 与双平台，约 **4–5 周**）。每张卡都是可独立验收的最小闭环，**验收不过不许进下一张**。

### M0 · 环境与自检（1 天，两平台各一次）

- **产出**：`setup_win.bat` / `setup_mac.sh`、`doc2md doctor` 实现、`pyproject.toml` + lockfile、`vendor/` 目录就位。
- **必做**：`doctor` 必须打印 `arch / sysconfig platform / macOS version / mps_available / mem_total / ulimit -n / disk_free / pandoc --version / llama-server --version`；任一不满足退出码 90。
- **验收**：在 M4 上 `arch -x86_64 zsh` 里跑 `setup_mac.sh` 必须**明确报错并拒绝安装**（Rosetta 防线）；`doctor` 全绿。
- **陷阱**：Mac 上先跑 `xattr -dr com.apple.quarantine vendor/bin/*`；用 venv 不用系统 Python。

### M1 · Schema + IR 骨架（2 天）

- **产出**：`core/schema.py`（IR v2 定义 + 校验器）、`core/ir_io.py`（原子写 + zstd + 读回）、`tests/test_schema.py`。
- **验收**：任意构造的非法 IR（缺 id、pos 越界、immutable 块带 `modified_by`）被拒；`ir_io` 写入中断不留半成品文件；`doc2md inspect` 能读。

### M2 · S1 + S1.5 探测与体检（3 天）

- **产出**：magic 探测（不信任扩展名）、`.doc` 转换支路、页数/体积门禁、**文本层体检 5 个指标**、拒收队列。
- **验收**：构造 4 类样本（原生 PDF / 纯扫描 / 双层假文本 / 缺 ToUnicode 的 CID 字体）与 3 类 DOCX（真样式 / 加粗伪标题 / 加密），体检必须把后三类**全部拦下**且 `reject_reason` 各不相同；`reject_recall ≥ 0.98`。
- **注意**：假文本层的判定不能只看「有没有文字」，要看**字符是否落在合理 Unicode 区**——这是 v1 的最大遗漏。

### M3 · S2 三通道（5–7 天，核心）

- **产出**：`docx_pandoc.py`（AST→IR，含 `numbering.xml`/`styles.xml` 元数据交叉）、`pdf_pymupdf.py`（逐块 + `raw_lines` + 字体 + x0 栏聚类）、`pdf_struct_tree.py`（pikepdf 读 StructTreeRoot，映射 H/L/Table）、`media.py`。
- **验收**：20 篇多样本文档跑通；`raw_lines` 与 PDF 实际视觉行数一致率 ≥ 99%（脚本比对 `page.get_text("text")` 的换行）；双栏文档阅读顺序错序率 ≤ 5%；带结构树的 PDF 走 `pdf-structtree` 通道并记录 `style_src`。
- **交付物**：同时产出「结构/断句/字符」三类错误分布统计，作为 M4 规则设计输入。

### M4 · S4 规则重建（6–8 天，收益最大）

- **产出**：`rules/` 下每规则一文件 + 配置化开关（清单见附录 C，共 18 条），每条带单元测试与「开关影响面」注释。
- **顺序不可变**：去噪 → 标题判定 → 编号连续性校验 → 列表重建 → **先合并硬换行/跨页** → **再切分粘连段落** → 表格/图片隔离。
- **验收**：黄金集上 `heading_F1 ≥ 0.85`（未接 LLM 前的门槛）、`table_struct_similarity ≥ 0.95`、`immutable_tamper_rate = 0`。
- **纪律**：**每条规则必须能单独关掉且不崩管线**；关不掉 = 该规则不许合入。

### M5 · S5-L1 规则修补 + S6 渲染校验（4 天）

- **产出**：断句规则集、全半角/中英空格规范化、Markdown renderer、`checksum.json`、`report.html`（含 diff、拒收清单、复核清单）、红线门禁。
- **验收**：`sentence_boundary_F1 ≥ 0.90`（未接 L3 前）；人为篡改一个表格块，S6 必须失败退出码 20 并回滚该文件。

### M6 · L3 LLM patch + 并发 + 双平台调优（5 天）

- **产出**：`llm_server.py` 生命周期、patch 客户端 + 5 道校验、三段流水调度器、`profile` 自动裁剪、`doc2md bench`。
- **验收**：`standard` profile 下 100 页文档端到端 ≤ 90 秒；`minimal` profile 在 T8 上能独立跑完（**必须在真 T8 或等价 8GB 虚拟机上验证一次**）；LLM 超时时管线不阻塞，只记 `W3001` 并跳过；`immutable_tamper_rate` 仍为 0。

### M7（可选）· L2 结构识别增强（3 天，仅 M4 `enhanced`）

- **产出**：`struct_layout.py` 的 Docling / Marker 适配器，与纯规则结果做 A/B。
- **验收标准是数据，不是印象**：黄金集上「标题 F1 + 表格保真 + 吞吐 + 内存峰值」四列对比表写进 ADR-05，**若 F1 提升 < 3% 则默认关闭 L2**（v1 的判断在 M4 上也可能是对的，要用数字确认）。

### 交付物清单（最终）

```
doc2md.zip / 内网部署包
  ├── 可运行程序（venv + vendor 二进制 + 模型）
  ├── config/（三个 profile 模板）
  ├── setup_{mac,win}.{sh,bat} + README.md（部署手册，含 Gatekeeper/TCC 步骤）
  ├── docs/（本方案 + ADR + 规则库说明 + 故障排查）
  ├── tests/（单测 + 黄金集回归脚本 + check 报告样例）
  └── vendor/licenses.txt（许可清单）
```

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
# GGUF（HuggingFace 上下载 Q4_K_S / Q8_0 单文件）
qwen3-4b-instruct-2507-q4_k_s.gguf   # ~2.5GB
qwen3-0.6b-instruct-q8_0.gguf         # ~0.75GB（T8 用）
# llama.cpp: brew build --HEAD 或下载 release 的 macos arm64 包
# 落地后必做：xattr -dr com.apple.quarantine ./llama-server && codesign -s - ./llama-server
# Docling/Marker 权重（仅 enhanced）：hf download ds4sd/docling-models 等，注意目录命名规则
```

**启动 LLM（M4，Metal）**：

```bash
llama-server -m vendor/models/llm/qwen3-4b-instruct-2507-q4_k_s.gguf \
  --host 127.0.0.1 --port 8080 -ngl 99 -c 8192 \
  --threads 6 --temp 0 --reasoning-budget 0 --timeout 60
# T8 上：-ngl 0 --threads 6，并改回「按需启停」
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
    raw = json.loads(subprocess.run(
        [pandoc, path, "-t", "json", "--wrap=none", "--extract-media", "work/tmp/media"],
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
| R-18 | 语气词/连词边界（了啊啊呢吧嘛后、以及/并且/此外/其中前） | S5-L1 | on | |

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
    <string>-m</string><string>/Users/you/doc2md/vendor/models/llm/qwen3-4b-instruct-2507-q4_k_s.gguf</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8080</string>
    <string>-ngl</string><string>99</string><string>-c</string><string>8192</string>
    <string>--temp</string><string>0</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/you/doc2md/work/logs/llm.log</string>
  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
</dict></plist>
PL
launchctl load ~/Library/LaunchAgents/md.doc2md.llm.plist

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
| ADR-02 | 双平台一份代码 + `platform_caps` 分叉 + profile 裁剪 | 避免维护两套逻辑导致行为漂移 | 出现第三个平台（Linux）时补 caps |
| ADR-03 | 默认 PDF 引擎 PyMuPDF，同时实现 MIT 后端 | 速度优先，但保留许可逃生门 | 交付形态变更为对外分发时 |
| ADR-04 | 引入 S1.5 文本层体检 | 假文本层是静默失败主因 | 永不（只调阈值） |
| ADR-05 | L2（Docling/Marker MPS）默认关闭，`enhanced` 才解锁 | 解锁 ≠ 有收益，需 A/B 数据 | F1 提升 ≥3% 且吞吐降幅可接受则默认开 |
| ADR-06 | LLM 档位：minimal 用 0.6B（默认关），standard 用 4B，enhanced 用 8B | 性价比；0.6B 在断句歧义上能力不足 | 黄金集上更优的替代模型出现 |
| ADR-07 | 吞吐与内存以本机 `bench` 结果为准，文档估算值仅作设计输入 | 社区数字不可信 | 换机/换系统版本 |
| ADR-08 | 不启用 OCR 与标点恢复模型 | 范围已排除扫描件；无标点文本极少 | 输入源混入语音稿 |
| ADR-09 | 生产路径用 `llama-server` 而非 Ollama | Metal 原生、少一层封装、社区测得更快 | — |

---

## 附录 I. 术语

`IR`（统一中间态 JSON）· `furniture`（页眉页脚页码等版式噪声）· `immutable`（禁止下游改写的块）· `ops/patch`（LLM 输出的最小改动指令）· `profile`（minimal/standard/enhanced 能力档）· `L0/L1/L2/L3`（规则 / 规则修补 / 结构模型 / LLM patch 四层）· `golden set`（黄金评测集）· `红线指标`（触碰即回滚的验收门禁）。

---

*本文档基于 v1 方案（2026-10-12「文本型 PDF 与 DOCX 转 Markdown 离线管线」）扩展而成；其中工具许可、平台能力、芯片规格均已核对公开资料，性能数字为本管线的设计输入值，须在目标机上以附录 G 脚本实测替换。*
