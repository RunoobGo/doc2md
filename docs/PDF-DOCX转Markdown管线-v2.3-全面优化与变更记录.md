# 文本型 PDF / DOCX → Markdown 离线转换管线 v2.3

## 全面优化完善报告 + 变更项点记录（v2.2 → v2.3，含 v2.2 自检）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | **v2.3**（对 v2.2 的全方位优化；v2.2 = 契约矛盾修复 + 运维闭环；v2.1 = 产品形态 + PRD + API/前端；v2.0 = 可行性 + M4 适配） |
| 编写日期 | 2026-10-13 |
| 文档性质 | **Delta 文档（修订增量）**：基线 = v2.1 原文 + v2.2 全部修订（合成后下称「v2.2 合成稿」）。本条目不重抄前文，全部标注锚点；v2.2 的编号体系（P-xx / CH-xxx / FR / NFR / ADR / R-xx）全部顺延 |
| 本轮审查维度 | ① **v2.2 自检**（其修订自身引入的新矛盾）；② 输出保真度盲区（链接/样式/目录页/修订痕迹）；③ 服务与前端事件模型；④ 数据演进与迁移；⑤ 安全细化；⑥ 评测与黄金集治理；⑦ 下游 RAG 对接口径 |
| 变更规模 | 问题 23 项（P-25…P-47）· 变更 24 项（CH-301…CH-324）· 需求新增 6 / 修订 4 · 规则库 +2（R-19/R-20，18 条 → 20 条）· ADR 新增 5（ADR-22…26）· 增量回归断言 7 条 |

---

## 0. 本文档怎么用

1. **开发 Agent**：v2.3 中【高】项只有 2 个（P-25、P-26），但它们直接影响 M6/M8 的 CI 设计，**必须在写第一行测试代码前吸收**；【中】项按任务卡归属在对应卡片开工前吸收。
2. **验收**：增量验收 = §3 需求验收标准 + §9 回归断言，叠加在 v2.1 §6.5 DoD 与 v2.2 §9 之上。
3. **合成完整 v2.3**：v2.2 合成稿 + 本文件 §3/§4/§5 锚点修订；变更记录 = 附录 J 累加 v2.2 的 CH-2xx 段与本文件 §7 的 CH-3xx 段。
4. 版本治理约定（v2.2 §0）继续生效：本文件即一次 minor 升版的登记实例。

---

## 1. 研究结论（TL;DR）

**v2.2 合成稿在契约一致性上已经成立，剩余风险从「自相矛盾」转移到「未定义行为」：一批长期被默认假设覆盖的语义（PDF 超链接、粗斜体、目录页、DOCX 修订痕迹、300 页分片切点、SSE 连接模型、sqlite 演进、黄金集更新流程）从未被写明。这些正是开发期「每个人都以为自己懂、写出来各不相同」的高发区。本轮将其全部落成契约。**

本轮最重要的三个发现：

1. **v2.2 的 FR-505「字节确定性」在 L3 路径上物理不可达（P-26）**：llama.cpp 的 continuous batching 在 Metal 上即使 `temperature=0` 也不保证逐位一致（batch 大小影响浮点归约顺序）。若按 v2.2 原文把「全格式字节一致」写进 CI，会得到一个永远 flaky 的红绿灯。**必须缩小确定性承诺的范围：字节级只保 L0/L1 纯规则路径；L3 路径改用「指标带宽」门禁。** 这是对 v2.2 最重要的一次自我修正。
2. **v2.2 的 FR-505 与 NFR-15 自身打架（P-25）**：FR-505 验收范围含 `json-ir`，而 NFR-15 又豁免了 IR 里的 `ts_utc`——同一文档两条规则给出相反结论。
3. **前端事件模型有结构性瓶颈（P-38）**：每个作业一条 SSE × 浏览器单域 6 连接上限，20 个并发标签页（NFR-2）场景下必然饿死；应改为**全局单条事件总线**。

此外补齐五个长期空白的语义域：**PDF 超链接保留、inline 样式（粗/斜/删除线）、目录页（TOC）识别、DOCX track-changes 固定处置、>300 页分片切点策略**——其中 TOC 识别缺失会让 v2.1 最引以为傲的「编号连续性校验（R-04）」被目录页反杀（目录条目天然连续编号），属于规则体系的自洽性漏洞。

---

## 2. 问题清单（P-25…P-47，按严重度）

### 2.1 v2.2 自检发现（其修订引入的新问题）

| ID | 严重度 | 位置 | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-25 | 【高】 | v2.2 FR-505 vs NFR-15 | FR-505 验收「md/md-plain/html/**json-ir**/chunks 全格式双跑 sha256 一致」，NFR-15 又允许 `json-ir` 的 `ts_utc` 非确定——同一版文档两条规则相反 | CI 必挂或被迫偷偷豁免，契约信誉受损 |
| P-26 | 【高】 | v2.2 FR-505 / ADR-16 | llama.cpp Metal + continuous batching 下 temp=0 也**不保证逐位确定**（batch 调度改变浮点归约顺序）；含 L3 的输出字节级一致不可承诺 | 「双跑 diff 进 CI」对生产配置（enhanced 含 L3）永远 flaky |
| P-27 | 【中】 | v2.2 §4.6 引入的 `W1091/E1092` | 新的错误码未按 v2.2 自己的纪律登记进 §7.8 错误码表 | v2.2 刚修完 P-06 又制造同类不一致 |
| P-28 | 【低】 | v2.2 NFR-14 | 「每日 00:00（**本机时区**）落盘」与 v2.1 §3.4「全流程统一 UTC + TZ=UTC」冲突 | 日报日期口径双标，跨年/跨时区审计对不上 |
| P-29 | 【中】 | v2.2 FR-121（source_key 自动聚合） | Web 上传没有「目录」上下文，auto_group 只能按规范化文件名；不同文档同名（各月「月报.pdf」）会被错误聚合到同一 source_key | 版本历史张冠李戴，比不聚合更糟 |
| P-30 | 【低】 | v2.2 §4.4 新增 `/sources` API | 未同步 §6.7(4) 需求覆盖矩阵与 §8.2 前端路由（版本历史入口挂哪） | 契约与界面脱节 |

### 2.2 输出保真度盲区（v2.1+v2.2 均未定义）

| ID | 严重度 | 位置 | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-31 | 【中】 | v2.1 §4.3/§4.4 全局 | **PDF 超链接（link annotations）去向未定义**：born-digital PDF 大量带链接，现状是静默丢失。链接是文本层既有信息，保留它不属于范围扩张 | 法务/技术文档交叉引用全部丢失且无人察觉 |
| P-32 | 【中】 | v2.1 §4.3 schema | **inline 样式（粗体/斜体/删除线）保留策略未定义**：blocks 只有纯 text，DOCX 经 pandoc 本来有 Strong/Emph，PDF 有 font flags，全部抹平 | 公文强调语义丢失；且 PDF 与 DOCX 两条通道保真度不对等 |
| P-33 | 【中】 | v2.1 附录 C 规则库 | **目录页（TOC）识别缺失**：目录条目「1.1 概述 …… 3」编号天然连续，**正好骗过 R-04 连续性校验** → 目录全部变假标题，与正文真标题重复 | 标题 F1 被系统性拉低，且污染 headings 索引（目录/分块全错） |
| P-34 | 【中】 | v2.1 附录 B-5 / §2.4-9 | **DOCX 修订（track changes）处置未固定**：pandoc 的默认行为依版本而异（accept/reject/all），批注的丢弃也未写进产物 flags | 同一份带修订的 docx 换 pandoc 版本输出不同，回归莫名失败 |
| P-35 | 【低】 | v2.1 §3.5 规则 3 / §4.5 `max_pages_per_task` | **>300 页分片的切点未定义**：按页范围硬切会把句子/段落/表格从中间腰斩 | 分片文档的边界块损坏且不可归因 |
| P-36 | 【低】 | v2.1 §4.5 `legacy_doc="convert"` | `.doc→.docx` 转换无验收标准：转换失败走什么码、mammoth 兜底对 .doc 不适用、soffice 超时多少，均未定义 | 遗留 .doc 批量迁移时行为随机 |
| P-37 | 【低】 | v2.1 §7.7 导出 | zip 内中文文件名编码、csv 用 Excel 打开乱码、front-matter 含特殊字符文件名破坏 YAML——三个导出层细节未定义 | 用户在 Windows/Excel 上的第一印象就是乱码 |

### 2.3 服务 / 前端 / 数据演进

| ID | 严重度 | 位置 | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-38 | 【中】 | v2.1 §7.4 / §8.5 | **每作业一条 SSE vs 浏览器单域 6 连接上限**：NFR-2 要求 20 并发标签页，队列页 + 多作业详情页同时开 ≥6 条 EventSource 后新连接全部挂起，表现为「进度卡死」 | §8.4 第 2 条「进度不能卡 99%」被传输层反杀 |
| P-39 | 【中】 | v2.1 §4.9 / v2.2 FR-607 | **state.sqlite 无 schema 版本与迁移策略**：v2.2 又往 tasks 表加了 `peak_rss_mb`，未来每次加字段都靠手工？ | 升级即丢库或启动即崩；备份恢复（FR-607）跨版本不可用 |
| P-40 | 【低】 | v2.1 §4.10-4 / §7.2 | token 安全只到「可吊销」：明文存哪、配置文件权限、是否记进日志、能否轮换，未定义 | 内网 token 泄漏后无法归因与止血 |
| P-41 | 【低】 | v2.1 §7.2 `PUT /settings` | 热更新边界未定义：哪些键即时生效、哪些需重启/重载 LLM | 改了 profile 没生效 → 用户以为服务坏了 |
| P-42 | 【低】 | v2.1 §4.9 | **worker 子进程崩溃（PyMuPDF 段错误等）处置未定义**：坏 PDF 可能反复杀死 worker 池 | 一份恶意/损坏文件拖垮整机吞吐 |
| P-43 | 【低】 | v2.1 §4.10-5 / M5 | 报告 HTML 无 CSP：报告含来源文件名等不可信字符串，被当钓鱼/脚本载体无防线 | 内网钓鱼面 |
| P-44 | 【低】 | v2.1 §8.1 / 交付物清单 | 「目标机离线」与「前端构建」的关系只写了一半：应明确 **web/dist 在有网机构建、随部署包分发、目标机零 Node 依赖** | 交付时在 M4 上现装 Node 现 npm install，离线纪律破功 |

### 2.4 评测治理与下游口径

| ID | 严重度 | 位置 | 问题 | 影响 |
| --- | --- | --- | --- | --- |
| P-45 | 【中】 | v2.1 §4.8 | **黄金集更新无治理**：规则改进后 expected 需要跟进修改，但没有「谁能改、怎么记、改前改后指标对比」的流程——黄金集会被悄悄改成「永远通过」 | 红线指标名存实亡（自己改考题） |
| P-46 | 【低】 | v2.1 §7.7 chunks | **chunks 的 token 计数口径未定义**：`max_tokens` 用哪个 tokenizer？离线环境必须固定 | 下游 RAG 按 token 截断时与导出方口径不一 |
| P-47 | 【低】 | v2.1 §7.7 chunks | chunks 的表格形态与 `s4.table_output` 的联动未定义：`table_html?` 字段在 gfm / csv+placeholder 两种格式下放什么？ | 下游三种配置三种行为，对接文档写不出来 |

---

## 3. 需求修订与新增（PRD 增量）

### 3.1 FR 修订条目

**FR-505【再修订】**（动因：P-25 + P-26，覆盖 v2.2 同名条目）：

| 编号 | 需求 | 验收标准 |
| --- | --- | --- |
| FR-505 | **确定性产物（分层承诺）**：<br>① **字节级确定性仅承诺 L0/L1 纯规则路径**（`llm=off`）：同输入+同配置+同代码版本，`md / md-plain / html / chunks` 四格式 sha256 稳定；<br>② **L3 路径不承诺字节级**，承诺「指标带宽」：同输入双跑，§4.8 各指标差值 ≤ 0.005，且 immutable checksum 违规恒 = 0；<br>③ `json-ir` 默认含审计字段（`ts_utc` 等）不参与字节比对；加 `?stable=true` 参数时剔除全部时钟/随机字段后纳入①的承诺范围 | CI 双层：a) `llm=off` 基线双跑字节一致（黄金集 10 篇 × 4 格式）；b) `enhanced` 双跑指标带宽比对 + immutable 校验。c) `json-ir?stable=true` 双跑字节一致。任一层失败 = 构建失败 |

**FR-121【补充】**（动因：P-29，追加在 v2.2 修订之后）：`auto_group` 的自动聚合键改为「**规范化文件名 + 首投来源通道**（web-upload / cli-path / watch-folder）」；检测到「同名但内容相似度 < 0.5」的新投递时**不自动聚合**，创建独立 source_key 并在该源的 UI 上提示「存在疑似同源文档，是否合并？」（用户确认走 `POST /sources/{key}/versions`）。验收：两个不同月份的「月报.pdf」不被自动合并；同一文档改名重投仍复用产物（doc_id 幂等路径不受影响）。

**NFR-4【维持 v2.2 修订，不变】；NFR-14【修订】**（动因：P-28）：「每日 00:00（本机时区）」改为「**每日 00:00 UTC** 落盘 `reports/daily/<UTC日期>.json`」；前端健康页展示时转本机时区，存储与文件名一律 UTC。

**NFR-9【修订】**（动因：P-40，追加安全细化）：token 只存 `sha256(token)`（`work/state.sqlite` 的 tokens 表），明文仅生成时展示一次；`config/api.toml` 与 tokens 表文件权限 `600`；任何日志/SSE 事件禁止记录 `Authorization` 头与完整 token（脱敏为前 6 位）；新增 `doc2md serve token --rotate <name>`（旧 token 立即吊销、新 token 一次展示）。

### 3.2 FR 新增条目

| 编号 | 需求 | 验收标准 | 优先级 | 动因 |
| --- | --- | --- | --- | --- |
| FR-107 | **PDF 超链接保留**（规则 R-20）：S2-B 抽取 link annotations，与 span bbox 重叠匹配后在渲染层输出 `[text](url)`；邮件/内部路径（`file://`）原样保留文本不转成链接；DOCX 侧 pandoc 天然保留，两通道行为对齐 | 黄金集「带链接」子集上链接保留率 ≥ 0.95、错配率（链接挂错文字）≤ 0.02；关闭 R-20 后输出不含任何 `](http` | P1 | P-31 |
| FR-108 | **inline 样式保留**：IR blocks 增加可选 `marks: [{start, end, kind}]`（kind ∈ bold/italic/strikethrough）；S6 渲染为 `**`/`*`/`~~`；标题块内 marks 一律丢弃；表格/代码块（immutable）不受影响；默认开，可关 | 黄金集带样式子集：bold 召回 ≥ 0.90、误标率 ≤ 0.05；marks 区间越界被 schema 校验拒绝 | P1 | P-32 |
| FR-109 | **目录页（TOC）识别**（规则 R-19）：命中特征（leader dots 重复 / 行尾右对齐页码 / 行密度 > 阈值 / 邻近「目录」标题）的连续块群标记为 `type=toc`，**不进 headings、不进渲染目录索引**，正文保留与否可配（默认保留为普通段落并打 `toc` 标记） | 黄金集含目录篇目：TOC 块识别召回 ≥ 0.95；**目录条目泄漏进 headings 数 = 0**；无误伤正文一级标题（回归标题 F1 不降） | **P0** | P-33 |
| FR-124 | **DOCX 修订痕迹固定处置**：pandoc 调用固定 `--track-changes=accept`；IR meta.flags 记 `had_tracked_changes`（检测到 `w:ins/w:del` 即置位）；批注（comments）丢弃并在报告丢弃清单中单列 | 同一份带修订 docx 跨 pandoc 版本（锁定的两个版本）输出一致；flags 与报告均可见 | P1 | P-34 |
| FR-125 | **全局事件总线**：新增 `GET /api/v1/events?jobs=id1,id2,…`（不带 jobs 参数 = 订阅全部），前端所有页面共用单条 EventSource；per-job `/jobs/{id}/events` 保留（CLI/脚本用），前端禁止直连 | 20 标签页 + 50 个进行中作业：并发 SSE 连接数 = 1/标签页；断线重连 `Last-Event-ID` 补齐后无重复渲染（客户端按 event id 去重） | P1 | P-38 |
| FR-126 | **大文档分片切点策略**：>300 页文档在 S2 之后按「一级标题边界」选切点（允许 ±10% 页数弹性）；无标题文档按页范围切，边界块打 `flags.shard_edge=true`；跨片表格**不合并**（记 `W1043`），跨片段落续接在合并报告中说明 | 分片文档：切点落在标题边界的比例 ≥ 90%（有标题时）；shard_edge 块可在审阅页筛选；无「半句开头」的块（抽样断言） | P1 | P-35 |

### 3.3 NFR 新增条目

| 编号 | 类别 | 要求 |
| --- | --- | --- |
| NFR-16 | 可演进 | `state.sqlite` 含 `schema_version` 表；启动时自动执行 `runtime/migrations/` 增量迁移（只前进不回滚）；迁移失败拒绝启动并提示 `doc2md restore`；备份快照记录 schema_version，跨版本恢复时先迁移后挂载 |
| NFR-17 | 交付 | `web/dist` 在有网机构建并随部署包分发；**目标机零 Node 依赖**；CI 校验 dist 内嵌的 API 类型与当前 `openapi.json` 同源生成（漂移即构建失败） |

---

## 4. 方案修订（工程规格增量）

### 4.1 IR Schema 增补（锚点：v2.1 §4.3 + v2.2 §4.1，约定编号顺延至 11–13）

```json
"blocks[i]": {
  "type": "… | toc",                       // 新增枚举值 toc（FR-109）
  "marks": [ {"start": 3, "end": 9, "kind": "bold"} ],   // 新增可选（FR-108）
  "links": [ {"start": 0, "end": 5, "url": "https://…", "page": 2} ],  // 新增可选（FR-107）
  "flags": { "shard_edge": false }         // 新增（FR-126）
}
```

**约定 11（marks/links 的不变量）**：`marks`/`links` 的 start/end 是相对 `text` 的字符下标；S4 的合并/切分规则在改写 text 时**必须同步重映射 marks/links 区间**，无法重映射的（跨块切开的样式段）拆分为两段并记 provenance；`text_edited`（人工编辑）后 marks/links 由编辑器重算或清空，禁止保留指向旧文本的区间——schema 校验器断言 `end ≤ len(text)`。

**约定 12（toc 块）**：`type=toc` 不进 `headings[]` 索引、不参与 R-04 连续性校验序列、L3 不送修；渲染默认降级为普通段落并保留文本（用户能看见目录内容），`s4.render_toc = "paragraph" | "drop"` 可配。

**约定 13（immutable 优先级）**：immutable（表格/代码）> toc > 普通块；任何规则不得把 toc 块并入表格/代码块。

### 4.2 规则库更新（锚点：v2.1 附录 C，18 条 → 20 条）

| ID | 规则 | 阶段 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| R-19 | **目录页识别**（leader dots / 行尾右对齐页码 / 行密度 / 邻近「目录」标题 → `type=toc`） | S4 | on | 新增（FR-109）。防目录条目骗过 R-04 |
| R-20 | **超链接保留**（link annotations ↔ span bbox 重叠匹配 → `links[]`） | S4（数据来自 S2-B） | on | 新增（FR-107）。仅处理文本层链接，非图片/OCR，不违反 §6.2 |

> 按 v2.1 §6.7 流程：新增规则已同步 §6.3 需求（FR-107/109）、任务卡（M3/M4，见 §5）与本变更表，满足「需求与任务卡必须一致」的登记要求。§8.3 P10 前端规则开关页读表即从 18 条变 20 条。

### 4.3 配置增补（锚点：v2.2 §4.2，并入 `base.toml`）

```toml
[s4]
render_toc = "paragraph"        # paragraph | drop（FR-109）
preserve_links = true           # R-20
preserve_marks = true           # FR-108

[shard]                         # FR-126
max_pages_per_shard = 300       # 与 max_pages_per_task 对齐
heading_boundary_tolerance = 0.10

[api]
event_bus = true                # FR-125 全局事件总线
max_sse_per_client = 2          # 防御性上限

[security]
token_store = "sha256"          # NFR-9 修订
config_file_mode = "0600"

[export]
zip_filename_encoding = "utf-8" # P-37：zip UTF-8 flag
csv_bom = true                  # P-37：csv 导出 UTF-8-BOM（Excel 兼容）

[chunks]                        # P-46/P-47：RAG 分块口径固化
tokenizer = "qwen3"             # 使用 vendor/models/llm/qwen3-tokenizer（离线 vocab）
max_tokens = 800
overlap_tokens = 50
table_field = "auto"            # auto = 随 s4.table_output：html→table_html, gfm→table_md, csv→table_ref
```

### 4.4 API 增补（锚点：v2.1 §7.2 + v2.2 §4.4）

```
GET    /api/v1/events?jobs=a,b,c     # 新增：全局事件总线（FR-125）；不带 jobs = 订阅全部
POST   /system/reload                # 新增：热重载（P-41）；body 指明 scope：config | llm | rules
GET    /docs/{doc_id}/ir?stable=true # P-25：剔除时钟/随机字段的确定性 IR 导出
```

- `PUT /settings` 响应增加 `{"applied": [...], "requires_reload": [...]}`：profile/规则开关/保留策略即时生效于**新作业**；`llm_model`、`bind`、`workers` 进 `requires_reload`（P-41）。
- 错误码表补登（修 P-27，v2.2 引入但未登记的 + 本轮新增）：

| HTTP | code | 含义 | 处置 |
| --- | --- | --- | --- |
| — | `W1091` | 单作业内存超配额，自动分片重跑 | 信息级，健康页计数 |
| — | `E1092` | 连续分片 2 次仍超配额，作业失败转人工 | 人工队列 |
| — | `W1043` | 跨分片表格未合并，保持原样 | 报告列出 |
| 422 | `E4152` | `.doc` 转换失败（soffice 崩溃/超时 60s） | 拒收队列，hint 引导手工另存 docx |
| — | `E9001` | worker 子进程异常退出（含段错误），附 stderr 尾部摘要；同 doc 自动重试 1 次 | 重试后仍败 → 人工队列，且该 doc 加入「毒文件」名单防止反复调度（P-42） |

### 4.5 代码骨架补丁

**TOC 识别（R-19 参考实现，附录 B 新增 B-9）**：

```python
import re
LEADER = re.compile(r"[\.·•…_—\-]{4,}\s*\d+\s*$")       # 点线/虚线 + 行尾页码
TAIL_PAGE = re.compile(r"\s\d{1,4}\s*$")

def detect_toc(blocks, min_run=5):
    """连续 ≥min_run 个「短行 + leader/行尾页码」的块群 → type=toc。
    防误伤：块文本含完整句子（句末标点+长度>40）的不判。"""
    run = []
    for b in blocks + [None]:
        is_toc_line = (
            b is not None and b["type"] == "paragraph"
            and 4 <= len(b["text"]) <= 60
            and (LEADER.search(b["text"])
                 or (TAIL_PAGE.search(b["text"]) and b.get("indent_x", 0) < 100))
            and not b["text"].rstrip().endswith(("。", "；", "："))
        )
        if is_toc_line:
            run.append(b)
        else:
            if len(run) >= min_run:
                for x in run:
                    x["type"] = "toc"
                    x["provenance"]["modified_by"].append("s4:toc_detect")
                    x["confidence"] = 0.9
            run = []
    return blocks
```

**marks/links 重映射（合并/切分时，附录 B-4 补丁）**：合并两行时区间平移 `+len(prev_line)`；切分时按切点把区间一分为二，跨切点的 mark 拆成两个（kind 不变），links 跨切点拆开时保留同一 url。实现要点：先算映射表 `old_pos → new_pos`，再整体重写区间，禁止边改边算。

**worker 崩溃隔离（附录 B-8 scheduler 约束追加，P-42）**：

```python
# 7) worker 子进程非零退出：作业标 failed(E9001)，记录 stderr 尾部 4KB；
#    同 doc_id 自动重试 1 次；再败 → 人工队列 + 写入 poison_docs 表，
#    调度器跳过 poison_docs 中的 doc_id（防坏文件反复杀 worker）。
# 8) 前端只许连 /api/v1/events 总线；per-job events 端点限 CLI/脚本 UA 使用（软约束，不强制拦截）。
```

**报告 CSP（M5 产出补丁，P-43）**：`report.html` 头部固定 `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`；报告不含任何 `<script>`（断言进测试）。

### 4.6 DOCX 通道修订（锚点：v2.1 附录 B-5，P-34/P-36）

```python
subprocess.run([pandoc, path, "-t", "json", "--wrap=none",
                "--track-changes=accept"],        # 固定：接受全部修订（P-34）
               capture_output=True, check=True, timeout=300)
# flags.had_tracked_changes：先扫描 document.xml 是否含 w:ins/w:del/w:commentRangeStart
# .doc 支路：soffice --headless --convert-to docx（timeout=60s），失败 → E4152 拒收（P-36）
```

### 4.7 数据演进（锚点：v2.2 §4.6，P-39）

- `state.sqlite` 建 `schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)`；`runtime/migrations/0001_init.sql …` 顺序执行；启动流程：`打开库 → 迁移到最新 → 再挂载调度器`；备份快照头部记录 schema_version，`restore` 时按「先 restore 旧版本 → 自动迁移到当前版本」处理。

### 4.8 黄金集治理（锚点：v2.1 §4.8，P-45）

新增「黄金集更新纪律」三条，写入 §4.8 末尾：

1. **expected 的任何修改必须伴随**：变更原因、涉及 doc_id 清单、修改前后 `doc2md check` 全指标对比，追加到 `reports/golden-changelog.md`（ADR-lite，不强制走正式 ADR）。
2. **禁止「让红灯变绿」式更新**：若指标下降且原因是规则缺陷，只能修规则，不能改 expected 迎合输出；golden-changelog 中「指标下降 + 原因 = 规则缺陷」的条目是审计重点。
3. `doc2md check` 输出除当次指标外，必须附**与上一通过基线的 delta 表**（每个指标 ±），delta 落盘 `reports/checks/<ts>.json` 永久保留。

---

## 5. 任务卡调整（锚点：v2.1 §9 + v2.2 §5）

| 卡 | 调整内容 | 关联 |
| --- | --- | --- |
| M0 | 验收追加：模型文件 sha256 登记与校验（`vendor/models/manifest.sha256`，doctor 校验）；`web/dist` 构建机与目标机职责写进 README（NFR-17） | P-44、P-49 沿用 |
| M3 | 产出追加：link annotations 抽取（S2-B）、`had_tracked_changes` 检测（S2-A）、`.doc` 支路 E4152 路径；验收追加：带链接/带修订样本各 3 篇 | FR-107、FR-124、P-36 |
| M4 | 产出追加：R-19（TOC 识别）、R-20（链接映射进 blocks）、marks 抽取与合并/切分重映射、分片切点策略（FR-126）；验收追加：**含目录篇目的「标题泄漏 = 0」断言** | FR-108/109/126 |
| M5 | 产出追加：marks/links 渲染（`**`、`*`、`~~`、`[t](u)`）、报告 CSP、front-matter `yaml.safe_dump`、zip/csv 编码 | FR-108、P-37、P-43 |
| M6 | 验收修订：确定性测试按 FR-505 新分层执行——`llm=off` 字节一致 + `enhanced` 指标带宽（替换 v2.2 的「全格式字节一致」）；追加 worker 崩溃隔离（E9001/毒文件名单）演练 | P-25/P-26、P-42 |
| M8 | 产出追加：全局事件总线、schema 迁移、`POST /system/reload`、settings `requires_reload` 语义、token sha256 存储与 rotate；验收追加：20 标签页单连接压测（FR-125）、迁移测试（旧版库 → 自动迁移 → 正常启动）、E4152/E9001 全链路 | FR-125、NFR-16、P-40/41 |
| M9a | 前端事件消费改为**仅总线**；追加 shard_edge 筛选视图；版本历史入口挂「结果库行内 → 版本视图」（P-30，不新增顶级路由，§6.7(4) 覆盖矩阵同步更新） | FR-125、FR-126、P-30 |
| DoD | 增补第 10 条：黄金集治理流程演练一次（修改 expected → golden-changelog 记录 → delta 表产出） | P-45 |

---

## 6. ADR 增补（编号顺延 v2.2 的 ADR-15…21）

| ID | 决策 | 理由 | 重议条件 |
| --- | --- | --- | --- |
| ADR-22 | **确定性承诺分层**：字节级只保 L0/L1；L3 用指标带宽（±0.005）+ immutable 门禁 | Metal continuous batching 下 temp=0 也不保证逐位一致（P-26）；把不可达的指标写进 CI 等于没有 CI | llama.cpp 官方提供 batch-invariant 推理且本机实测通过 |
| ADR-23 | 前端事件消费采用**全局单条总线**，per-job SSE 仅服务 CLI/脚本 | 浏览器单域 6 连接上限 × 20 标签页必然饿死（P-38）；总线模型同时简化重连与去重逻辑 | 浏览器连接限制策略发生本质变化 |
| ADR-24 | 保留超链接与 inline marks（bold/italic/strikethrough），**不保留**字号/颜色/字体族 | 链接与强调是语义信息（文本层既有），字号颜色是版式信息——前者提升下游可用性，后者引入渲染噪音；此属范围**细化**而非扩张，不触发 §6.7 T-2/T-3 | 下游（RAG/知识库）实测证明 marks 无收益 → 默认关 |
| ADR-25 | 黄金集更新强制走 golden-changelog + 指标 delta 落盘 | 防止「改考题让红灯变绿」（P-45）；评测系统公信力是本方案的地基之一 | 永不 |
| ADR-26 | sqlite 只前进迁移 + 启动自动执行 + 备份随版本恢复 | 状态库是审计与幂等的载体，结构演进必须有轨（P-39） | 数据量超出 sqlite 舒适区（如事件表 >1 亿行）时评估换 embedded KV |

---

## 7. 变更项点总表（CH-3xx：v2.2 → v2.3）

> 编号延续 v2.2 规则。类型：修订 / 新增 / 勘误。状态：已生效。

| ID | 类型 | 变更 | 位置锚点 | 动因 | 关联需求/ADR/任务卡 |
| --- | --- | --- | --- | --- | --- |
| CH-301 | 修订 | FR-505 确定性承诺分层（字节级 = L0/L1 + `json-ir?stable=true`；L3 = 指标带宽） | v2.2 FR-505/NFR-15 | P-25、P-26 | FR-505、ADR-22、M6 |
| CH-302 | 补登 | 错误码表补 `W1091/E1092`（v2.2 漏登）并新增 `W1043/E4152/E9001` | v2.1 §7.8 | P-27 | M2/M6/M8 |
| CH-303 | 勘误 | NFR-14 日报改 UTC 00:00 | v2.2 NFR-14 | P-28 | M8 |
| CH-304 | 修订 | source_key 自动聚合键 = 规范化文件名 + 首投通道；低相似同名不自动合并、UI 提示人工确认 | v2.2 FR-121、§4.2 `[source]` | P-29 | FR-121、M8/M9a |
| CH-305 | 修订 | 版本历史入口定位：结果库行内版本视图；同步 §6.7(4) 覆盖矩阵与 §8.2 | v2.2 §4.4 | P-30 | M9a |
| CH-306 | 新增 | FR-107 超链接保留 + 规则 R-20 | v2.1 §4.3/附录 C | P-31 | FR-107、ADR-24、M3/M4/M5 |
| CH-307 | 新增 | FR-108 inline marks（bold/italic/strikethrough）+ schema `marks[]` + 重映射规则 | v2.1 §4.3/附录 B-4 | P-32 | FR-108、ADR-24、M4/M5 |
| CH-308 | 新增 | FR-109 目录页识别 + 规则 R-19 + `type=toc` + 约定 12/13 | v2.1 §4.3/附录 C | P-33 | FR-109、M4 |
| CH-309 | 新增 | FR-124 DOCX 固定 `--track-changes=accept` + `had_tracked_changes` + 批注丢弃入报告 | v2.1 附录 B-5、§2.4-9 | P-34 | FR-124、M3 |
| CH-310 | 新增 | FR-126 分片切点策略（标题边界 ±10% / shard_edge / W1043） | v2.1 §3.5、§4.9 | P-35 | FR-126、M4/M6 |
| CH-311 | 修订 | `.doc` 支路补验收：60s 超时、失败 E4152、flags 标记 | v2.1 §4.5 | P-36 | M3 |
| CH-312 | 修订 | 导出编码三件套：zip UTF-8 flag、csv BOM、front-matter `yaml.safe_dump` | v2.1 §7.7、M5 | P-37 | M5 |
| CH-313 | 新增 | FR-125 全局事件总线 `GET /api/v1/events` + 前端单连接纪律 | v2.1 §7.4/§8.5 | P-38 | FR-125、ADR-23、M8/M9a |
| CH-314 | 新增 | NFR-16 sqlite schema_version + 只前进迁移 + restore 跨版本流程 | v2.2 §4.6 | P-39 | NFR-16、ADR-26、M8 |
| CH-315 | 修订 | NFR-9 token 安全细化（sha256 存储 / 600 / 脱敏 / rotate） | v2.1 §4.10-4 | P-40 | NFR-9、M8 |
| CH-316 | 新增 | `POST /system/reload` + settings `requires_reload` 语义 | v2.1 §7.2 | P-41 | M8 |
| CH-317 | 新增 | worker 崩溃隔离：E9001 + 重试 1 次 + poison_docs 名单 | v2.1 §4.9、附录 B-8 | P-42 | M6/M8 |
| CH-318 | 新增 | 报告 CSP + 零 script 断言 | v2.1 §4.10-5、M5 | P-43 | M5 |
| CH-319 | 修订 | NFR-17 目标机零 Node：dist 有网机构建、随包分发、CI 校验同源 | v2.1 §8.1/交付物清单 | P-44 | NFR-17、M0/M8 |
| CH-320 | 新增 | 黄金集更新纪律三条 + check 输出基线 delta 表 | v2.1 §4.8 | P-45 | ADR-25、M4/DoD |
| CH-321 | 新增 | chunks token 口径固化（qwen3 tokenizer、max 800、overlap 50）+ table_field 联动规则 | v2.1 §7.7 | P-46、P-47 | M8 |
| CH-322 | 新增 | IR 约定 11（marks/links 重映射不变量）、约定 12（toc）、约定 13（immutable 优先级） | v2.1 §4.3 | P-31/32/33 | M1/M4 |
| CH-323 | 修订 | M0 验收追加模型 sha256 登记校验（vendor/models/manifest.sha256） | v2.1 §9 M0、附录 A | 供应链一致性 | M0 |
| CH-324 | 新增 | 增量回归断言 7 条（见 §9） | v2.2 §9 之后追加 | 全部 | CI |

---

## 8. 追溯矩阵

### 8.1 问题 → 变更 → 落点

| 问题 | 变更 | 需求/规则 | 章节锚点 | 任务卡 | ADR |
| --- | --- | --- | --- | --- | --- |
| P-25/P-26 | CH-301 | FR-505（再修订） | v2.2 §3.2 | M6 | ADR-22 |
| P-27 | CH-302 | — | §7.8 | M2/M6/M8 | — |
| P-28 | CH-303 | NFR-14（修订） | v2.2 §3.4 | M8 | — |
| P-29 | CH-304 | FR-121（补充） | v2.2 §3.1 | M8/M9a | — |
| P-30 | CH-305 | — | §6.7(4)/§8.2 | M9a | — |
| P-31 | CH-306 | FR-107 / R-20 | §4.3/附录 C | M3/M4/M5 | ADR-24 |
| P-32 | CH-307 | FR-108 | §4.3/附录 B-4 | M4/M5 | ADR-24 |
| P-33 | CH-308 | FR-109 / R-19 | 附录 C | M4 | — |
| P-34 | CH-309 | FR-124 | 附录 B-5 | M3 | — |
| P-35 | CH-310 | FR-126 | §3.5/§4.9 | M4/M6 | — |
| P-36 | CH-311 | — | §4.5 | M3 | — |
| P-37 | CH-312 | — | §7.7 | M5 | — |
| P-38 | CH-313 | FR-125 | §7.4/§8.5 | M8/M9a | ADR-23 |
| P-39 | CH-314 | NFR-16 | v2.2 §4.6 | M8 | ADR-26 |
| P-40 | CH-315 | NFR-9（修订） | §4.10-4 | M8 | — |
| P-41 | CH-316 | — | §7.2 | M8 | — |
| P-42 | CH-317 | — | §4.9/B-8 | M6/M8 | — |
| P-43 | CH-318 | — | §4.10-5 | M5 | — |
| P-44 | CH-319 | NFR-17 | §8.1 | M0/M8 | — |
| P-45 | CH-320 | — | §4.8 | M4/DoD | ADR-25 |
| P-46/P-47 | CH-321 | — | §7.7 | M8 | — |

### 8.2 新需求 → 场景（沿用 v2.1 U1–U9）

| 需求 | 场景 | 说明 |
| --- | --- | --- |
| FR-107/108（链接/样式） | U6、U1 | RAG 语料与人读 MD 的保真度直接受益 |
| FR-109（TOC） | U3、U6 | 目录不进 headings → 复核清单与分块不再被目录污染 |
| FR-124 | U2 | 历史 docx 批量迁移的确定性前提 |
| FR-125 | U2、U8 | 队列页/健康页多标签并发的传输地基 |
| FR-126 | U2 | 大文档迁移的质量兜底 |

### 8.3 四代决策演进线（关键维度 v2.0 → v2.3 一页总览）

| 决策线 | v2.0 | v2.1 | v2.2 | v2.3 |
| --- | --- | --- | --- | --- |
| 版本/身份模型 | 无 | doc_id + revision（矛盾） | + source_key 三层 | 自动聚合防误并（同名低相似不合并） |
| 确定性承诺 | 未提 | 字节一致（FR-704，与时间戳冲突） | 全格式字节一致（与 L3 物理特性冲突） | **分层：L0/L1 字节级，L3 指标带宽** |
| 保真度 | 纯文本 | 纯文本 + 表格/代码 immutable | 不变 | **+ 链接 / marks / TOC 识别 / 修订痕迹固定** |
| 事件模型 | — | per-job SSE | 不变 | **全局总线（前端）+ per-job（CLI）** |
| 状态库 | state.sqlite | + 审计流水 | + WAL/备份/恢复 | **+ schema_version 只前进迁移** |
| 评测治理 | 指标定义 | 红线门禁 | 双跑确定性 | **+ golden-changelog + delta 表（防改考题）** |
| 规则库 | 18 条 | 18 条 | 18 条 | **20 条（+R-19 TOC、+R-20 链接）** |
| 变更治理 | 无 | §6.7 需求变更控制 | + CH-xxx 文档变更登记 | 沿用；本轮即一次完整登记实例 |

---

## 9. v2.3 增量回归断言（追加到 v2.2 §9 之后）

| 断言 | 定义 | 红线 |
| --- | --- | --- |
| `deterministic_l0l1` | `llm=off` 下黄金集 10 篇 × {md, md-plain, html, chunks, json-ir?stable=true} 双跑 sha256 全等率 | = 100% |
| `l3_metric_band` | `enhanced` 双跑：§4.8 各指标差值 | ≤ 0.005，immutable 违规 = 0 |
| `toc_leak` | 含目录篇目：目录条目进入 `headings[]` 的数量 | = 0 |
| `link_preservation` | 带链接子集：链接保留率 / 错配率 | ≥ 0.95 / ≤ 0.02 |
| `marks_integrity` | 任意 S4 变换后所有 `marks/links` 区间 `end ≤ len(text)` 且指向原语义片段（抽样人工比对 20 处） | 校验器 0 拒绝 + 抽样全对 |
| `event_bus_single_conn` | 20 标签页 + 50 进行中作业：每标签页 SSE 连接数 = 1；重连后无重复事件渲染 | 全部成立 |
| `schema_migration` | 用 v2.2 结构的老库启动 → 自动迁移到当前版本 → 队列/审计/事件完整可读；`restore` 旧备份后同样迁移成功 | 全部成立 |
| `worker_crash_containment` | 注入触发段错误的畸形 PDF：E9001 记录、自动重试 1 次、入 poison_docs、worker 池其余作业不受影响 | 全部成立 |
| `report_csp` | 全部报告 HTML 含 CSP meta 且无 `<script>` 标签 | = 100% |

---

## 10. 本轮明确不改的事项（防止范围蠕变的反向登记）

1. **仍不做图片/OCR/扫描件**：本轮的链接与 marks 均为文本层既有信息，不构成范围扩张（ADR-24 已论证）；§6.2 原样不动。
2. **不上监控栈、不做多机**：ADR-20 的「最小运维闭环」不变；NFR-14 日报 + FR-606 告警已够单机自用。
3. **不重写 v2.1 已冻结的契约**：拒收原因码集合、错误码既有段位、退出码表、profile 三档——本轮只做增补，不做改名/删除。
4. **不引入新的运行时依赖**：TOC 识别、marks、链接全部基于 PyMuPDF/pandoc 既有能力；qwen3 tokenizer 复用 LLM 模型附带 vocab，不新增包。
5. **L2（Docling/Marker）维持默认关闭**：ADR-05 判据不变，本轮无异议。

---

## 附录 J. 变更记录（累加段）

| 版本 | 日期 | 变更 | 登记 |
| --- | --- | --- | --- |
| v2.2 | 2026-10-13 | 24 项问题、26 项变更（CH-201…226）、ADR-15…21 | v2.2 文档 |
| v2.3 | 2026-10-13 | 23 项问题（P-25…P-47）、24 项变更（CH-301…324）、需求新增 6 / 修订 4、规则 +2（R-19/R-20）、ADR-22…26、任务卡 8 卡调整、回归断言 9 条 | 本文件 |

*本文件为 v2.2 合成稿（v2.1 + v2.2 Delta）的 Delta 修订文档。四代文档（v2.0 → v2.1 → v2.2 → v2.3）的全部变化均可经 CH-1xx/2xx/3xx 三段变更表逐条回溯到问题、需求、章节、任务卡与 ADR。*
