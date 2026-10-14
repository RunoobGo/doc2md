# Doc2md

文本型 PDF / DOCX → Markdown 的**离线**转换管线（v2.4）。
单机、无出站网络、无云端依赖；LLM 修补助手同样只连本地 `127.0.0.1`。

## 安装

```bash
python3 -m venv env && env/bin/pip install -e ".[dev]"
env/bin/doc2md doctor                    # 环境自检（依赖 / 目录权限 / tokenizer 口径）
```

命名纪律：标识符一律小写 `doc2md`；launchd 标签 `md.doc2md.cron` / `md.doc2md.cron.weekly` / `md.doc2md.serve` / `md.doc2md.llm`。

## CLI

```bash
doc2md serve [--host H --port P --root DIR --config F]   # API 服务（默认 127.0.0.1:8090）
doc2md serve token [--role R --label L | --revoke TOKEN | --list]
doc2md cron --daily | --weekly [--no-backup]
doc2md convert <file|dir> [--mode auto|single|two_phase --out DIR --workers N --json --follow-symlinks]
doc2md work [--once --max-jobs N --workers N --idle S --enqueue PATH --status]
doc2md watch <dir> [--once --interval S]
doc2md tokenizer export --gguf F [--vocab V --manifest M]
doc2md tokenizer verify --manifest M --gguf F
doc2md doctor
```

退出码：`0` 成功 / `2` 用法错误 / `3` 门禁拒收（E4221 等） / `4` 业务错误 / `5` 系统错误。
CI 里 `doc2md convert --json` 的 stderr 会打印门禁摘要（`shards` / `char_sim` / `half_heads`）。

## 快速开始

```bash
doc2md serve &                                  # 起 API（root 自动 chmod 700）
TOK=$(doc2md serve token --role admin --label local | tail -1)
curl -s -X POST localhost:8090/jobs -H "X-Doc2md-Token: $TOK" \
     -H 'Content-Type: application/json' -d '{"path":"./samples/contract.pdf"}'
curl -s "localhost:8090/search?q=例外" -H "X-Doc2md-Token: $TOK"
```

## 配置

内置默认值见 `doc2md/config/default_toml.py`；部署样例见 `config/doc2md.example.toml`；逐键语义与默认值见方案 附录 A。
读取顺序：默认 → `--config` → `./doc2md.toml` → `$DOC2MD_HOME/doc2md.toml`。

相对路径（`work/out`、`work/reports/backups` 等）会随 `[paths].root` / `--root` 一起迁移（CH-432）。

## 低配机器（Win11 无 GPU / Mac mini 16GB）

文本管线是 CPU + 内存带宽问题，GPU 无收益；下列口径全部有实测支撑（方案 第七章 + 第十二章证据）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `[s1].chunk_pages` | `32` | S1 分块重开 + 每页缓存清理。320 页样本转换峰值驻留 **1290MB（改造前）→ 176MB**，产物字节级等价 |
| `[perf].workers` | `0` | 批量转换进程数，`0 = min(4, CPU)`。内存 ≈ workers × chunk_pages（4 进程并发：分块关合计约 4100MB，分块 32 约 708MB），8GB 机器别调到 8 |
| `[perf].queue_async` | `false` | `true` → `POST /jobs` 只入队（实测 13 个作业 **13ms** 返回 202），后台 `doc2md work` 消化；`GET /jobs` 看进度 |
| `[perf].min_free_mb` / `disk_safety_factor` | `512` / `3` | NFR-6 磁盘门禁：写产物/备份前校验剩余空间，不足 → **413 / E4131**，不留截断 zip |
| `[perf].stale_job_minutes` | `30` | 崩溃遗留的 `running` 队列任务超时重回 `queued`（仅限队列来源，不动同步请求行） |
| `[llm].mode` / `fallback` | `offline` / `true` | 修补助手实现：`offline` = 确定性占位（默认，主链路零网络）；`server` = 本地 llama-server（仅 `127.0.0.1:8080`）。服务不可用时回退 offline 保持主链路；`--llm-no-fallback` 则明确失败（E5041 / 503） |
| `[llm].op_whitelist` | 5 个 op | 白名单是唯一防线（+5 道校验 + 不可信数据立场），模型听话不是防线；server 与 offline 两条路径同一套校验 |

Windows 中文控制台（cp936）下含 emoji 的文件名不再崩进程，`--json` 输出恒为纯 ASCII
（两种代码页字节一致，可直接管道给 PowerShell / CI）；`doc2md work --enqueue DIR --once`
可替代 API 走纯 CLI 批量。

## 权限与数据边界

- `work/` 目录权限 **700**（M8 验收；CH-437 起由备份快照路径实际 chmod）；
- 文件名一律消毒后入库（`source_name`），原始名只出现在报告 HTML 展示层（`source_name_raw`），
  zip 打包二次断言无 `..` / 无 `/`（双闸，CH-404 / CH-419）；
- 任何文件名字段不参与 `doc_id` 计算（IR 约定 14：`doc_id` 永远只哈希文件内容）；
- 日志 / 日报 / 事件流**不含正文**，长文本字段落 `sha256` 指纹（NFR-20）。

## 备份加密（NFR-18）

要点在 `ops/backup.md`：外置副本强制 AES-256-GCM 容器（`b"D2MBAE1"` 前缀），口令只走
`DOC2MD_BACKUP_PASSPHRASE`；macOS 备选 `hdiutil create -encryption AES-256 … SPARSEBUNDLE`。
未配置外置卷 → 周报该步骤 `skipped`（不算失败）；配置了外置卷但无口令 → `ok=False`，**不落明文**。
已删数据 ≤14 天从全部副本自然轮出（FR-128）。

## 定时任务（launchd）

```bash
cp ops/launchd/md.doc2md.cron*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.weekly.plist
launchctl list | grep doc2md          # 校验（R6）：应列出两个标签
```

`--daily` = 日报 → 本地快照 → tmp 清理 → 日志轮转检查；`--weekly` = `--daily` 超集 + 外置加密增量。
口令不要写进 plist；用 `launchctl setenv DOC2MD_BACKUP_PASSPHRASE …` 或 Keychain 包装脚本注入。

## 定时任务（Windows 任务计划）

```powershell
pwsh ops/windows/install-doc2md-tasks.ps1 -SetPassphrase   # 安装 + 交互式设置用户级口令
pwsh ops/windows/install-doc2md-tasks.ps1 -VerifyOnly       # 校验（等价 launchctl list）
pwsh ops/windows/install-doc2md-tasks.ps1 -Uninstall        # 卸载
```

注册 `md.doc2md.cron`（每日 00:00）与 `md.doc2md.cron.weekly`（周日 00:30）两个任务，
以当前用户最低权限运行，无需管理员；电池供电照跑、错过的时间窗开机补跑。
任务定义（`ops/windows/*.xml`）不含任何口令；时间触发按**本机本地时间**解释
（launchd 侧固定 UTC，要同口径可把机器时区设为 UTC，或用 `-DailyAt` 换算）。
排障：`Get-ScheduledTask -TaskName md.doc2md.cron* | Format-List *`。

## 日志（NFR-19 / CH-444）

轮转由**服务自身**执行，不依赖 newsyslog/cron：`doc2md serve` 启动即打开 `work/logs/app.jsonl`，
按 `[logs].rotate_mb`（默认 50MB）或跨 UTC 日滚动到 `app.jsonl.1 … .{rotate_keep}`，
轮转事件记于新文件首行 `{"code":"E_LOG_ROTATE", …}`；服务启动同时清理 `work/tmp/` 残留。
`job_done` / `text_edited` / `settings_updated` / `system_reload` 除入 events 表外同步落该日志，
正文类字段一律以 sha256 指纹写入（NFR-20）。

`doc2md cron` 是与 serve 分离的进程，各写自己的 `work/logs/cron.jsonl`（同阈值同轮转器），
不与服务的活动文件互踩。`doc2md work`（CH-449）同理写自己的 `work/logs/work.jsonl`——
rename 轮转只能有一个属主，三类进程三份文件。

## 文档与变更跟踪

| 文档 | 作用 |
| --- | --- |
| **`docs/dev-plan.md`** | **开发方案主文档（唯一权威）**。主题篇（一–九章）为当前有效规格，正文不含变更编号，其中 3.5–3.7 给代码层结构、依赖纪律与技术栈选型边界；附录 A–G 是配置 / CLI / API / 错误码 / 数据库 / 门禁 / 模块清单的查询表；登记篇（十–十二章）集中记录阶段性变更与优化项、实测证据与未验证项 |
| `docs/plan-delta-v1.1.md` | 已归档存根，指向方案（原 v1.0 附录 H 的增量登记已整体迁入 v2.0 第十一/十二章） |
| `ops/backup.md` | 备份介质与恢复演练细则 |
| `ops/windows/install-doc2md-tasks.ps1` | Windows 任务计划安装 / 校验 / 卸载（与 launchd 同等能力） |

跟踪约定：契约变更、缺陷、更优做法一律按登记流写入方案 第十一章（当前编号至 `CH-459`，下一条 `CH-460`），实测数字进第十二章并标注语料与测量方法；规格发生变化时同步主题篇章节与本文相应小节，并递增 `doc2md.PLAN_VERSION`（管线契约变化另递增 `PIPELINE_VERSION`）。

## 验收与测试

六条 merge-gate 的 CI 定义见 `.github/workflows/ci.yml`：`merge-gate` job 跑
`pytest -m gate`（Python 3.11/3.12/3.13 矩阵，失败阻断合并），`full-suite` job 依赖它跑全量 +
黄金样本可复现性 + `doc2md doctor`。
> 现状：workflow 已进远端，每次 push 到 `main` 触发 run（首轮四作业全绿：merge-gate 3.11/3.12/3.13 + full-suite）。
> 但本仓库是 private + 免费计划，分支保护/规则集 API 返回 403，**无法把 merge-gate 设为必需检查**，
> 因此「失败不可合并」尚未强制生效（见方案 CH-446/CH-453 与待办 T1）。合并前仍需人工 `pytest -m gate` 判绿。

```bash
pytest -m gate            # 红线层：六条 merge-gate + 低配机专项（全绿方可合并；共 67 项，T8 优化后稳定 53s，历史轮次 72–110s 视机器负载）
pytest tests/             # 全量（含 M0/M2/M3/M4M6/M5/M6c/M8/M9a/DoD/PERF）
python3 tests/make_golden.py            # 重建黄金样本（输出目录随脚本位置，--golden 可覆盖）
python3 tests/make_golden.py --force    # 强制重生成
```

黄金样本集逐字节可复现（CH-442）：同一脚本跑两遍产物 sha256 全表一致，`MANIFEST.json`
的哈希绑定可复核。分发包**不含** `tests/golden/bigfile.bin`（120MB，CH-443 已把
「大文件拷贝中途不入队」的判定搬进 `tests/test_m3_bigcopy.py`，该资产为可选项，
需要时由生成器现补）；`dirs/symlink_loop/A↔B` 为**相对**悬空链接，换目录解包后仍然有效。
解包口径（实测）：`tar` 与 Info-ZIP `unzip`（**默认即可**，按 unix mode `120000` 还原软链）
都保留符号链接；`unzip -y` 在 Info-ZIP 6.00 上不是合法选项（直接吐 usage 并退出码 10），
`python -m zipfile -e` 会把链接**降级成普通文件**（防护用例随即失真）——两者都不要用。

六条门禁：`shard_two_phase`、`malicious_filenames`、`injection_resistance`、
`edit_optimistic_lock`、`delete_cascade`、`log_rotation`；另含本实现补强的 CJK 检索、渲染分级、
目录递归防护与低配机专项断言（完整清单见方案 附录 F）。

正文里出现的 `CH-xxx` 只是索引，条目原文与实测证据统一收录在
`docs/dev-plan.md` 第十一、十二章。
