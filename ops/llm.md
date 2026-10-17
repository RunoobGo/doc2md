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
- **诚实边界**（12.7）：已验证面 = 请求契约、故障分级、fallback、注入归因
  （假 llama-server 22 项 + e2e）；**真实 GGUF 全链路**（真实模型加载与真实输出下的
  产物等价性）未在沙箱验证——首次接入后建议对黄金样本跑一轮 `convert --llm-mode server`
  并与 offline 产物比对，作为一次真机验收。
- 模型资产不入 git（`vendor/models/` 不进版本库）；换模型 = 换 gguf 文件 +
  （可选）更新 `llm.model`，无 schema / 版本常量影响。
