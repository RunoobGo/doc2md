# Doc2md 本地 LLM 接入指引（4.6 / CH-456 T2，CH-466 实操口径）

## 架构一句话

项目**不下载、不托管模型**——`LlmClient`（`doc2md/llm.py`）只做本地客户端，
连 **llama.cpp 的 `llama-server`**（OpenAI 兼容 `/v1/chat/completions`，
仅限 `llm.bind:llm.port` = `127.0.0.1:8080`，NFR-1 离线红线）。
模型以 **GGUF 文件**自备；默认 `llm.mode = "offline"`（确定性占位，零网络，主链路完全可用）。

## 模型选型

LLM 在管线里只做**块级修补 op**（`split_block` / `merge_block` / `fix_heading_level` /
`fix_list_marker` / `mark_table_boundary`，白名单外一律拒绝）。输入不是全文，而是
**候选块的正文片段 + 扁平前后文 + 定位元数据**；每批 ≤8 个候选、总计 ≤16 个，
单批 JSON ≤6k 字符（P0：原 12k 会超过 8k 上下文）。选型建议：

| 维度 | 建议 | 理由 |
| --- | --- | --- |
| 参数量 | 3B～7B instruct | op 输出是短 JSON，3B 已足够；7B 换取更稳的 JSON 格式服从性 |
| 量化 | Q4_K_M | 低配机友好（3B ≈ 2GB），质量损失对结构化输出可忽略 |
| 语言 | 中文语料选中文强的 instruct（如 Qwen 系列） | 黄金样本以中文为主 |
| 格式 | **必须是 GGUF** | llama.cpp 唯一消费格式；`tokenizer verify` 可校验完整性 |

### P0 参数契约与校验（v2.5，2026-10-21）

v2.4 的系统提示词只给了一个 `merge_block` 示例，0.8B 会把它当成模板，输出缺
`level` / `marker` / `at` 的 op；旧校验器又只校验“出现了的参数”，于是出现
`accepted=20` 但 `effective=8`、正文变更 0 的假成功。v2.5 起：

| op | 必填 | 校验 |
| --- | --- | --- |
| `fix_heading_level` | `level` | int 1–6；提示词要求每次只下调一级 |
| `fix_list_marker` | `marker` | `^[-*+·•‣]{1,3}$` |
| `split_block` | `at` | `0 < at < len(text)`；不得切在英文单词/数字串/URL 中间；须在句末/分句标点后或空白边界，两侧去空白后 ≥4 字符 |
| `merge_block` | `target` | 现有目标/类型/题注/结构/句末校验 |
| `mark_table_boundary` | `target` | 必须是 table，或紧邻 table 的题注/表头段落 |

缺参一律 `missing_param:<name>` 拒绝；切点不安全分别报 `bad_at` /
`split_inside_token` / `split_inside_url`；普通段落误标表边界报
`not_table_boundary`。请求侧同时下发 `op_schema`（机器可读）与系统提示词
（自然语言）两份契约。

管线审计口径改为三分账：

- `accepted_ops`：通过校验；
- `applied_ops`：真正改变 IR（`Result.applied_ops` 只含这一层）；
- `noop_ops`：过校验但没落笔（缺参已被拒，这里主要是同值/重复旗标）。

`report.html` 的 LLM 面板与 `Result.llm` 都会给出 `response / accepted /
effective / noop / rejected` 计数；`content.md` 不写这些易变值。

### 单 slot 与超时

- 单进程/单文档转换：`llama-server ... -np 1`。默认 4 slot 下实测同一 payload
  会出现 2 个 / 15 个 op 交替；`-np 1` 连续两轮稳定。
- 并发转换：每个 worker 独立 `llama-server`，不要共享多 slot 实例。
- `timeout_ms` 默认 **60000**；CPU 0.8B 的 768-token 输出用 15s 会误判超时。
- 上下文建议 `-c 16384`；客户端 payload 已压到 6k 字符。

### 本机实测推荐（2026-10-18，RTX 3070 8GB + i5-13600K / 34GB）

本机 llama.cpp 为 winget 的 **b11680 Vulkan 构建**；选定：

- **Qwen3.5-4B Q4_K_M**（4.21B，2.54GiB）——质量为优先；可全量 `-ngl 99` 上 GPU
- 上下文 **32768**、KV **q4_0**、flash-attn on、单 slot；实测显存约 **3.7GB used / 4.3GB free**
- 若 KV 用 q8_0，32k 会到约 **7.9GB used**，8GB 卡只剩几十 MB，不建议
- `--reasoning-budget 0` 关闭思考；`--jinja` 使用模型聊天模板；`--alias doc2md-llm`

```powershell
llama-server.exe -m "D:\Models\Qwen3.5-4B-Q4_K-M\Qwen3.5-4B-Q4_K_M.gguf" `
  --host 127.0.0.1 --port 8080 `
  -ngl 99 -c 32768 -t 8 -tb 16 -fa on `
  -ctk q4_0 -ctv q4_0 -np 1 `
  --jinja --reasoning-budget 0 --alias doc2md-llm --no-webui --metrics
```

项目本地配置（`work/` 不入库）：

```toml
[llm]
mode = "server"
fallback = true
timeout_ms = 120000
retries = 1
model = "doc2md-llm"
bind = "127.0.0.1"
port = 8080
auth = false
```

本机实测结果：

| 项目 | 结果 |
| --- | --- |
| `/health` / `/v1/models` | ok / `doc2md-llm` |
| 4B `llama-bench` pp/tg | 134.6 / 64.1 t/s（16 token 短测） |
| 40 页语料（18,058 prompt tokens） | server 模式 **15.35s**，成功 |
| 3 页黄金样本 | server 模式 **1.09s**，成功 |
| 320 页（5789 块） | 旧版整篇摘要超 32k，`fallback=true` 回退 offline；CH-487 后按候选分批，不再整篇发送 |
| 注入探针 | `rewrite_table_to_prose`/`delete_block` 被拒，归因 `injection_candidate` |

### PDF 结构修复的分工（CH-487，2026-10-18）

真实中文 PDF 的分句/分段/页码问题**先由确定性规则处理**，模型只兜残余歧义：

| 层 | 职责 | 位置 |
| --- | --- | --- |
| S1 | 保留行 `top/bottom`、页高，供页边距与首行缩进判断 | `pipeline/s1_parse.py` |
| S3 | 页眉页脚/页码剔除；中文序号、括号序号、字母列表；首行缩进分段；列表/标题折行并回；图表题注与“附件/附录”结构起始切块 | `pipeline/s3_structure.py` |
| LLM | 只对候选块输出 `merge_block` / `split_block` / `fix_heading_level` 等白名单 op | `llm.py` |
| 校验 | 目标存在/immutable/占用之外，再拒绝：类型不符、题注 merge、跨类型 merge、结构块 merge、短独立行 merge、完整句后 merge | `llm.OpValidator` |

候选窗口的实现口径：

- 只选“可疑边界”：段落-段落无硬句末、超长块、页码/多章节残留、疑似漏判标题；
  正常列表项与已识别标题不进入候选，避免模型被噪声淹没。
- `candidates` 带 `text`（≤240 字）、`prev_id/prev_text`、`next_id/next_text`、
  `allowed_ops`（按块类型给出可落地算子）；`target` 只接受 id 字符串。
- 请求带 `response_format={"type":"json_object"}`；模型输出仍走同一套白名单与校验，
  JSON 约束只是格式辅助，不是防线。
- 单批失败按服务失败处理（`fallback` / E5041 口径不变）；批数、响应 op 数只记计数，
  正文不落日志（NFR-20）。

真实语料实测（7 份中文 PDF，同一台 RTX 3070 / Qwen3.5-4B Q4_K_M，32k ctx）：

| 模式 | 总耗时 | 实际落地 op | 结果 |
| --- | --- | --- | --- |
| offline | 10.73s | — | 页眉块 0；5p/23p/38p 句中拆分 25→15 / 68→52 / 25→4；附件巨段消失 |
| server | 30.76s | 4（3 个折行合并 + 1 个标题层级修正） | 其余模型 op 因类型/题注/结构/句界校验被拒；无内容回归 |

**边界**：4B 模型仍会输出“把独立短行并起来”的错误建议，因此模型 merge 只允许
“上一行未收句、非结构块、非短独立行”的边界；生产默认 `llm.mode=offline`，
server 模式作为可选增强。大文档不再整篇发送；候选上限保证不会超 32k 上下文。

## 引入五步

```bash
# ① 下载 GGUF（HuggingFace 或 ModelScope 任选其一）
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF \
  qwen2.5-3b-instruct-q4_k_m*.gguf --local-dir .
# ModelScope（国内更快）：
#   modelscope download --model Qwen/Qwen2.5-3B-Instruct-GGUF <文件名> --local_dir .

# ② 放入资产目录（paths.vendor_models 的相对值挂在 paths.root 下；
#    默认 root=work → work/vendor/models/llm/，不是仓库根 vendor/）
mkdir -p work/vendor/models/llm && mv qwen2.5-3b-instruct-q4_k_m.gguf work/vendor/models/llm/

# ③ 安装 llama.cpp 并启动 llama-server
llama-server -m work/vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 -c 16384 -np 1

# ④ 验证模型资产完整性（M0 工具链，非运行必需但建议做一次）
#   `export` 不指定 --manifest 时写到 [chunks].manifest（默认
#   work/vendor/models/llm/tokenizer.manifest.json，随 paths.root 迁移），
#   `verify` 的两个参数都是必填，路径照 export 的落点填即可（照抄旧示例的
#   chunks.manifest.json 会因文件不存在直接退 5）。
doc2md tokenizer export work/vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf
doc2md tokenizer verify --manifest work/vendor/models/llm/tokenizer.manifest.json \
  --gguf work/vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf

# ⑤ 自检 + 切换
doc2md doctor        # llm_mode=server 时探测 /health → 期望 llm_server: reachable
doc2md convert 文件.pdf --llm-mode server
```

## 模型名发现规则（`llm.py:_discover_model`）

- `llm.model` 显式配置**优先**；
- 为空时取 `paths.vendor_models` 下**恰有一个** `.gguf` 的文件名（去扩展名）；
- 目录内 0 个或 ≥2 个 gguf 且未显式配置 → 发现失败，server 模式按
  `llm.fallback` 语义处理（回退 offline 或 E5041）。

## 配置速查（`[llm]`，附录 A 有全表）

| 键 | 默认 | 语义 |
| --- | --- | --- |
| `mode` | `"offline"` | `"server"` = 走本地 llama-server |
| `fallback` | `true` | server 不可用回退 offline（主链路不断）；`false` = 明确 E5041 / HTTP 503，不落「看起来成功」的产物 |
| `timeout_ms` | `60000` | 单次请求超时（连接 + 读取合计）；CPU 0.8B 实测 15s 偏紧 |
| `model` | `""` | 空 = 自动发现（见上） |
| `bind` / `port` | `127.0.0.1:8080` | llama-server 地址；出站守护（netguard）只放行回环 |
| `auth` | `false` | `true` 时 Bearer 口令走 `DOC2MD_LLM_TOKEN` 环境变量（不落配置不入日志；无鉴权回环监听是 P-55 / CH-408 已知接受项） |
| `op_whitelist` | 5 个修块 op | 白名单外 op 一律拒绝并归因（`injection_candidate` / `model_hallucination`） |

## 故障排查

| 症状 | 原因 | 处置 |
| --- | --- | --- |
| doctor `llm_server: unreachable (connect:…)` | llama-server 未启动 / 端口不符 | 起 llama-server；核对 `bind`/`port` |
| `llm_server: unreachable (http:…)` | 端口被别的服务占用（返回非 llama-server 响应） | 换端口 |
| E5041 / 退出码 4 | `fallback=false` 且 server 不可用（设计行为：明确失败不蒙混） | 起 server 或改回 `fallback=true` / `mode=offline` |
| `applied_ops` 全为 0 但无报错 | offline 占位本来就只产出保守 op；server 模式下看 `llm_stats`：`accepted>0, effective=0` 说明模型只提了同值/已设置的 op，或全部被拒 | 这是预期；先看 `noop_ops` 与 `rejected` 原因，再决定是否扩候选规则 |
| server 模式耗时明显高于 offline | 候选批次串行请求本地模型 | 默认保持 offline；需要模型兜底时再开 server，或减少候选上限 |
| 模型名发现失败 | 目录内 gguf 数 ≠ 1 且 `llm.model` 为空 | 只留一个 gguf，或显式配 `llm.model` |

## 边界与纪律

- **离线纪律（NFR-1）**：运行期出站仅限 127.0.0.1；模型下载属于引入期人工动作，
  与运行期守护无关。不要把 HuggingFace 代理配置写进任何 `doc2md` 配置。
- **诚实边界**（12.7，2026-10-18 更新）：真实 GGUF 全链路已在本机 Qwen3.5-4B +
  llama.cpp b11680 Vulkan 上验证：`doctor`、`/health`、真实 `convert`、注入探针拒绝、
  大小文档 fallback 均通过；3 页 PDF 与 6 块 DOCX 的 server/offline `content.md`
  **逐字节相同**（sha 前缀 `e822313717df` / `4e976360e761`）。仍未验证：其他模型、
  跨机 GPU/驱动、320 页以上要 LLM 真正参与（当前超上下文回退 offline）。
- 模型资产不入 git（`work/vendor/models/` / `paths.vendor_models` 不进版本库）；换模型 = 换 gguf 文件 +
  （可选）更新 `llm.model`，无 schema / 版本常量影响。
