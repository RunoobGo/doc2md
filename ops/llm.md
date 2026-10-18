# Doc2md 本地 LLM 接入指引（4.6 / CH-456 T2，CH-466 实操口径）

## 架构一句话

项目**不下载、不托管模型**——`LlmClient`（`doc2md/llm.py`）只做本地客户端，
连 **llama.cpp 的 `llama-server`**（OpenAI 兼容 `/v1/chat/completions`，
仅限 `llm.bind:llm.port` = `127.0.0.1:8080`，NFR-1 离线红线）。
模型以 **GGUF 文件**自备；默认 `llm.mode = "offline"`（确定性占位，零网络，主链路完全可用）。

## 模型选型

LLM 在管线里只做**块级修补 op**（`split_block` / `merge_block` / `fix_heading_level` /
`fix_list_marker` / `mark_table_boundary`，白名单外一律拒绝），输入是 IR 定位摘要而非全文，
上下文需求很小。选型建议：

| 维度 | 建议 | 理由 |
| --- | --- | --- |
| 参数量 | 3B～7B instruct | op 输出是短 JSON，3B 已足够；7B 换取更稳的 JSON 格式服从性 |
| 量化 | Q4_K_M | 低配机友好（3B ≈ 2GB），质量损失对结构化输出可忽略 |
| 语言 | 中文语料选中文强的 instruct（如 Qwen 系列） | 黄金样本以中文为主 |
| 格式 | **必须是 GGUF** | llama.cpp 唯一消费格式；`tokenizer verify` 可校验完整性 |

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
| 320 页（5789 块） | prompt 超 32k，`fallback=true` 回退 offline，**13.05s** 成功 |
| 注入探针 | `rewrite_table_to_prose`/`delete_block` 被拒，归因 `injection_candidate` |

**边界**：项目 `LlmClient` 当前把整篇块摘要一次发出，不做分块；超过服务上下文的文档会收到
http 错误。生产建议保持 `llm.fallback=true`（大文档自动 offline），或先修 LLM 层分块后再
考虑 `fallback=false`。2B 模型虽生成更快，但 32k q8 KV 占用约 5.6GB、且会输出 fenced JSON；
本轮以 4B 为质量/显存平衡选择。

## 引入五步

```bash
# ① 下载 GGUF（HuggingFace 或 ModelScope 任选其一）
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF \
  qwen2.5-3b-instruct-q4_k_m*.gguf --local-dir .
# ModelScope（国内更快）：
#   modelscope download --model Qwen/Qwen2.5-3B-Instruct-GGUF <文件名> --local_dir .

# ② 放入资产目录（paths.vendor_models，默认 vendor/models/llm/）
mkdir -p vendor/models/llm && mv qwen2.5-3b-instruct-q4_k_m.gguf vendor/models/llm/

# ③ 安装 llama.cpp 并启动 llama-server
llama-server -m vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080

# ④ 验证模型资产完整性（M0 工具链，非运行必需但建议做一次）
#   `export` 不指定 --manifest 时写到 [chunks].manifest（默认 vendor/models/llm/tokenizer.manifest.json），
#   `verify` 的两个参数都是必填，路径照 export 的落点填即可（照抄旧示例的
#   chunks.manifest.json 会因文件不存在直接退 5）。
doc2md tokenizer export vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf
doc2md tokenizer verify --manifest vendor/models/llm/tokenizer.manifest.json \
  --gguf vendor/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf

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
| `timeout_ms` | `15000` | 单次请求超时（连接 + 读取合计） |
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
| `applied_ops` 全为 0 但无报错 | offline 占位本来就只产出保守 op | 这是预期；要真实修块必须 `mode=server` |
| 模型名发现失败 | 目录内 gguf 数 ≠ 1 且 `llm.model` 为空 | 只留一个 gguf，或显式配 `llm.model` |

## 边界与纪律

- **离线纪律（NFR-1）**：运行期出站仅限 127.0.0.1；模型下载属于引入期人工动作，
  与运行期守护无关。不要把 HuggingFace 代理配置写进任何 `doc2md` 配置。
- **诚实边界**（12.7，2026-10-18 更新）：真实 GGUF 全链路已在本机 Qwen3.5-4B +
  llama.cpp b11680 Vulkan 上验证：`doctor`、`/health`、真实 `convert`、注入探针拒绝、
  大小文档 fallback 均通过；3 页 PDF 与 6 块 DOCX 的 server/offline `content.md`
  **逐字节相同**（sha 前缀 `e822313717df` / `4e976360e761`）。仍未验证：其他模型、
  跨机 GPU/驱动、320 页以上要 LLM 真正参与（当前超上下文回退 offline）。
- 模型资产不入 git（`vendor/models/` 不进版本库）；换模型 = 换 gguf 文件 +
  （可选）更新 `llm.model`，无 schema / 版本常量影响。
