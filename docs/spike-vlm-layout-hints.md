# Spike：PDF 多模态版面提示层（候选 CH-491，未登记）

> 状态：提案 / 未实施，不改变当前 `2.12.0` 默认管线
> 目标：验证「本地多模态模型为 PDF 提供结构提示」能否在**不牺牲文本保真与低配机可用性**的前提下提升复杂版面的转换效果
> 原则：功能全面、性能优先；模型只提供弱证据，确定性文本层与规则仍是事实源

## 1. 结论先行

不建议实现「VLM 先给文档行打标、再让解析器按标注重写」的强耦合方案。建议实现一个**可选的 S1.6 版面提示层**：

```
PDF → S1 文本/几何行（不变） → S1.5 文本层门禁（不变）
    → [可选] S1.6 本地 VLM 页面/区域提示（默认关闭）
    → S3 规则 + 提示弱合并 → S4 渲染（不变）
```

- VLM 只输出**页面区域、阅读顺序、行索引级类型/层级候选、表格区域**；
- **正文永远来自文本层**，VLM 不生成、不改写、不删除文本；
- 模型不可用、超时、预算耗尽、JSON 不合法或置信度不足 → **完整跳过**，产物与无模型路径逐字节一致；
- 先用 20–50 份真实 PDF 做时间盒 Spike，达到验收阈值后才考虑进入默认档或新增正式 CH。

## 2. 问题与假设

### 2.1 当前管线的强项与边界

当前 [s1_parse.py](file:///F:/Dev/Object/doc2md/doc2md/pipeline/s1_parse.py) 已提供字号、粗体、位置、页高、等宽、链接、表格行候选等信号；[s3_structure.py](file:///F:/Dev/Object/doc2md/doc2md/pipeline/s3_structure.py) 已用确定性规则处理标题、列表、表格、代码、引用、页眉页脚、双栏阅读顺序；[llm.py](file:///F:/Dev/Object/doc2md/doc2md/llm.py) 的文本 LLM 只做白名单块级修补。

仍然薄弱的主要是：

- 复杂多栏/跨栏标题的阅读顺序；
- 跨页/合并单元格/无框线表格的边界；
- 图表题注与正文的归属；
- 真实业务 PDF 的小字号/无编号标题；
- 目录页、页眉页脚与正文混排。

### 2.2 待验证假设

| 假设 | 判据（Spike 出口） |
| --- | --- |
| H1 结构收益 | 在复杂真实 PDF 集上，标题 F1、表格结构 exact match、阅读顺序准确率至少一项相对基线提升 **≥10%**，且 clean 集不下降 |
| H2 文本无损 | 启用提示后正文 `char_similarity` 相对文本基线 **≥0.999**；正文不能出现 VLM 生成的新字/改字 |
| H3 成本可接受 | 目标 GPU 上 p95 **≤2.5s/页**、单文档提示预算可配置；无模型/超时路径不增加转换耗时 |
| H4 安全降级 | 模型不可用、超时、坏 JSON、低置信度、行/区域映射失败时全部跳过；关闭时产物 **逐字节等价** |
| H5 可维护性 | 模型、提示词、缓存键可版本化；不引入 torch/transformers 等重依赖；不改变 DB schema |

## 3. 范围

### 3.1 In scope

- 文本型 PDF（当前支持范围）；
- 页面渲染、区域/行索引提示、提示合并、缓存、预算、降级；
- 本地 loopback VLM 服务客户端；
- 可复现的小规模评测脚本与指标。

### 3.2 Out of scope

- 扫描件/纯图片 PDF、OCR、公式识别；
- 让 VLM 生成/改写正文；
- 云端模型、出站网络；
- 默认启用、IR schema/DB schema 变更；
- 把 VLM 资产打进发布包。

> 若未来要支持扫描件，应作为独立产品决策与独立 Spike；不要借本项偷偷扩大输入范围。

## 4. 架构设计

### 4.1 阶段位置

- S1 生成 `RawPage.lines`（已有 `Line`：text/size/bold/italic/mono/x0/top/bottom/row…）；
- S1.5 继续对坏文本层拒收（E4221）；
- **S1.6** 对每页生成 `PageHints`，只作为 `analyze(pages, hints=...)` 的可选入参；
- S3/S4 不感知模型；`hints=None` 时行为与当前完全一致；
- sharding 时提示按**全局页索引**保存，分片后按 `lo:hi` 切片传递。

### 4.2 提示数据结构（建议）

```python
@dataclass
class RegionHint:
    kind: str          # column | table | figure | caption | header | footer
    bbox: tuple[float, float, float, float]   # 页面坐标，已归一化到 0..1
    line_indices: list[int]                   # 该页 RawPage.lines 下标
    confidence: float

@dataclass
class PageHints:
    page_index: int
    reading_order: list[int]                  # 行下标；仅接受完整且无重复的排列
    regions: list[RegionHint]
    line_roles: dict[int, tuple[str, int | None, float]]  # role, level, confidence
    table_hints: list[dict]                   # bbox / row_indices / col_count
    source: str = "vlm"
    model: str = ""
    prompt_version: str = ""
```

约束：

- **只用页内行下标**，不把全文/跨页 id 交给模型，避免分片和 id 漂移；
- `reading_order` 必须是 `0..n-1` 的排列；缺失/重复/越界即整页丢弃；
- `line_roles` 只允许白名单角色；`level` 只允许 1–6；
- 不接受模型返回的文本字段；任何文本型输出都丢弃并计数。

### 4.3 请求与模型

- 复用 OpenAI 兼容 `/v1/chat/completions`，本地 `127.0.0.1`，由 `offline_guard` 守护；
- VLM 通过 `llama-server --mmproj`（或等价的本地 OpenAI-compatible 服务）提供图片输入；
- 页面渲染建议 144–200 DPI，灰度/压缩图，单页一次请求；支持 `image_url` data URI；
- prompt 固定版本，要求只输出 JSON；`temperature=0`、`max_tokens` 有界、响应大小有界；
- 不把 prompt/响应/页面图写入日志（NFR-20）；只记录页号、耗时、token/大小、状态类名。

建议配置（Spike 先只用环境变量/CLI 参数，稳定后再进 `[vlm]`）：

```toml
[vlm]
mode = "off"                 # off | server
bind = "127.0.0.1"
port = 8081
model = ""
dpi = 150
timeout_ms = 30000
max_pages_per_doc = 20       # 0 = 不限制（不建议）
confidence_min = 0.65
cache_dir = "work/vlm_cache"
```

### 4.4 渲染与缓存

- 渲染器优先用 `pypdfium2`（pdfplumber 已依赖它）；不新增 torch/transformers；
- 缓存键 = `doc_id + page_index + 页面图 sha256 + prompt_version + model_id`；
- 缓存只存**提示 JSON**，不存页面图；页面图处理完立即删除；
- 缓存目录随 `paths.tmp`/私有 ACL；cron 保留期清理；
- 缓存命中必须与实时结果走同一套校验；缓存不改变关闭路径。

### 4.5 提示合并策略（关键）

S3 只把提示当**弱证据**，并按以下优先级合并：

1. **文本层/显式样式 > 规则 > VLM**：DOCX Heading、文本层清晰的字号/粗体/编号信号优先；
2. **区域优先、逐行次之**：先接受 column/table/figure/caption/header/footer 区域；逐行 `line_roles` 仅在区域映射完整且置信度达标时使用；
3. **阅读顺序全有或全无**：只有 `reading_order` 覆盖当前页所有行且无重复时才应用；否则保留 S1 顺序；
4. **表格只影响分组，不生成单元格文本**：提示用于切分/合并行组、确定列边界；单元格文本仍由 `Line.row`/几何抽取；
5. **禁止降级**：提示不能把 heading 变 paragraph、不能删除块、不能把正文变 code/quote；冲突时保留确定性结果；
6. **留痕**：命中提示的块写 `flags.layout_hint = {source, confidence, region_id}`，报告统计接受/拒绝数，不进正文。

### 4.6 失败与降级

| 失败 | 行为 |
| --- | --- |
| `vlm.mode=off` | 完全跳过，零网络、零渲染 |
| 服务不可达/超时 | 跳过本页/本文件，记录计数；主链路继续 |
| 返回非 JSON / schema 不符 | 丢弃整页提示；不部分采用 |
| 行号越界/重复/缺行 | 丢弃 `reading_order` 或该页提示 |
| 置信度不足 | 丢弃该区域/行角色 |
| 超过页数/时间预算 | 停止后续提示，已接受提示保留；统计 `hints_budget_exhausted` |
| 缓存损坏 | 删除缓存项并重新请求；失败则跳过 |

## 5. 测试与评测设计

### 5.1 单元测试

- schema 校验：角色/层级/bbox/行号范围/重复/缺行；
- 合并优先级：规则与提示冲突、表格区域、题注、双栏顺序；
- 全有或全无：不完整 `reading_order` 不改变顺序；
- 降级：off/超时/HTTP 错误/坏 JSON/低置信度/预算耗尽；
- G6：`hints=None` 与当前产物逐字节一致；
- 缓存：键、命中、损坏、私有权限；
- 安全：提示中夹带正文/指令不进入输出；不落日志。

### 5.2 合成 + 真实语料

- 合成：现有 21 份综合语料 + 新增 2–4 份双栏/合并单元格/题注/跨页表格/页眉页脚 PDF；
- 真实：现有 14 份本地真实文档（不入库）+ 10 份人工 ground truth PDF；
- 每份跑三态：`offline`（无提示）、`offline+hints`、`server+hints`；
- 对每份保存：IR 块数、标题集合、表格结构、阅读顺序、`char_similarity`、耗时、峰值内存、提示接受/拒绝统计。

### 5.3 指标

**结构质量**

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| Heading P/R/F1 | 与人工标题集按文本+层级匹配 | 复杂集 F1 相对提升 ≥10%；clean 集不下降 |
| Heading level accuracy | 层级完全正确比例 | 不下降 |
| Block type macro-F1 | 七类块（heading/paragraph/list_item/table/code/quote/page_break） | 复杂集提升 ≥10% |
| Table detection P/R | 表格区域命中 | 不下降 |
| Table structure exact match | 行数/列数/单元格文本完全一致 | 复杂集提升 ≥10% |
| Reading order accuracy | 行序列与人工顺序的成对顺序准确率 | 复杂集 ≥95%，或相对提升 ≥10% |
| Caption/figure attribution | 题注归属正确率 | 人工抽查 ≥90% |

**文本与回归**

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| `char_similarity` | 与无提示基线正文比较 | ≥0.999 |
| 正文新增/丢失字符 | 非空白字符集合差异 | 0 新增；不丢正文 |
| `half_sentence_heads` | 半句开头块 | 0 |
| 产物 G6 | 关闭提示时逐字节一致 | 100% |
| 分片≡单片 | blocks_after/相似度 | 通过现有红线 |

**成本与稳定性**

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| 秒/页 p50/p95 | 渲染 + 请求 + 解析 | p95 ≤2.5s（目标 GPU）；CPU 只做记录 |
| 文档总耗时 | 20/100/320 页 | 相对无提示增量可解释 |
| 峰值驻留 | 进程 working set / GPU VRAM | 不突破低配机既定档位 |
| 失败率 | 超时/坏 JSON/映射失败/低置信度 | 可观测；坏 JSON 不得重试爆炸 |
| 缓存命中率 | 重复运行 | 二次运行显著下降 |
| 模型不可用回退 | 主链路成功率 | 100% 不中断 |

**人工验收**

- 10 份复杂 PDF 盲评：结构修复时间、错误类型（漏标题/错层级/表格错列/顺序错）；
- 记录“模型改对了”与“模型改错了”的案例数，避免只看平均分。

### 5.4 验收阈值（建议）

Go（可进入默认候选）必须同时满足：

1. 复杂集任一项结构指标相对提升 ≥10%，且 clean 集不下降；
2. 文本 `char_similarity` ≥0.999、无正文新增/丢失；
3. p95 ≤2.5s/页（目标 GPU），无模型路径零额外耗时；
4. 失败/超时/坏 JSON 下主链路 100% 成功；
5. 关闭提示产物逐字节一致；
6. 不新增重依赖、不改 DB schema、不突破私有数据边界。

No-Go 或只保留“复杂文档可选增强”的条件：任一结构收益不足、clean 集回归、p95 超预算、或维护成本明显高于规则改进。

## 6. 实施阶段（时间盒）

| 阶段 | 工作 | 出口 |
| --- | --- | --- |
| S0 可行性（1–2 天） | 本机安装 VLM + mmproj，选 20 页跑通图片输入；测延迟/显存/JSON 稳定性 | 能稳定拿到合法 JSON；记录 p50/p95 |
| S1 适配器（2–3 天） | 页面渲染、`PageHints` schema、假 VLM 服务、`analyze(hints=)` 空实现；默认 off | 单测通过；关闭路径 G6 |
| S2 合并策略（3–5 天） | 区域/阅读顺序/标题/表格弱合并；冲突与留痕 | 合成语料定向用例通过 |
| S3 评测（3–5 天） | 21 合成 + 14 真实 + 10 ground truth 三态跑；指标 JSON；人工抽查 | 报告 + Go/No-Go 建议 |
| S4 决策 | 只登记；达到阈值才讨论默认档/正式 CH | 不自动改默认 |

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 模型幻觉/改字 | 正文错误 | 禁止文本输出；文本层唯一来源；schema 白名单 |
| 行号/区域漂移 | 错块、错表 | 只用页内行号；全有或全无；bbox 覆盖率校验 |
| 单页延迟高 | 低配机不可用 | 默认 off；页数/时间预算；只对抽样/低置信页 |
| 显存不足/服务崩溃 | 转换中断 | 独立进程/端口；超时跳过；主链路 fallback |
| 依赖膨胀 | 打包/装机失败 | 不引入 torch；VLM 走本地 HTTP；渲染用已有闭包 |
| 隐私 | 页面图/正文外泄 | 仅 loopback；不落日志/不存图；缓存只存结构提示；私有 ACL |
| 缓存失效 | 结果漂移 | 键含图片 hash/prompt/model 版本；缓存与实时同校验 |
| 维护复杂 | 规则与提示双轨 | 提示只增弱证据；所有接受/拒绝可观测；默认路径不依赖模型 |
| 评测偏差 | 只对模板有效 | 合成 + 非模板真实 + 人工 ground truth；clean 集回归门 |

## 8. 交付物

- 本设计文档；
- Spike 代码（默认 `vlm.mode=off`，不改变现有产物）；
- 假 VLM 契约测试 + 真实 VLM 本机记录；
- `ops/bench/` 下的评测脚本与指标 JSON；
- `docs/review-...` 或独立 Spike 报告（含 Go/No-Go 结论）；
- 若 No-Go：只保留设计记录，不进入正式登记；若 Go：另开 CH 并同步版本/附录/测试。

## 9. 与现有登记的关系

- 本项与 B-15（真实 PDF 标题 ground truth）、B-25（DOCX 页眉页脚/分页）有交集，但不替代它们；
- 不改变当前 `PIPELINE_VERSION 2.12.0` / `PLAN_VERSION 3.26`；
- 候选编号 `CH-491` 仅在 Spike 通过后占用；在此之前不写登记表、不改默认配置。
