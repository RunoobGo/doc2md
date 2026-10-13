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
doc2md convert <file|dir> [--mode auto|single|two_phase --out DIR --json --follow-symlinks]
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

内置默认值见 `doc2md/config/default_toml.py`；部署样例见 `config/doc2md.example.toml`（附录 C-1 全文 + 实现增量）。
读取顺序：默认 → `--config` → `./doc2md.toml` → `$DOC2MD_HOME/doc2md.toml`。

相对路径（`work/out`、`work/reports/backups` 等）会随 `[paths].root` / `--root` 一起迁移（CH-432）。

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

## 定时任务（launchd，附录 C-2）

```bash
cp ops/launchd/md.doc2md.cron*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.weekly.plist
launchctl list | grep doc2md          # 校验（R6）：应列出两个标签
```

`--daily` = 日报 → 本地快照 → tmp 清理 → 日志轮转检查；`--weekly` = `--daily` 超集 + 外置加密增量。
口令不要写进 plist；用 `launchctl setenv DOC2MD_BACKUP_PASSPHRASE …` 或 Keychain 包装脚本注入。

## 日志（NFR-19 / CH-444）

轮转由**服务自身**执行，不依赖 newsyslog/cron：`doc2md serve` 启动即打开 `work/logs/app.jsonl`，
按 `[logs].rotate_mb`（默认 50MB）或跨 UTC 日滚动到 `app.jsonl.1 … .{rotate_keep}`，
轮转事件记于新文件首行 `{"code":"E_LOG_ROTATE", …}`；服务启动同时清理 `work/tmp/` 残留。
`job_done` / `text_edited` / `settings_updated` / `system_reload` 除入 events 表外同步落该日志，
正文类字段一律以 sha256 指纹写入（NFR-20）。

`doc2md cron` 是与 serve 分离的进程，各写自己的 `work/logs/cron.jsonl`（同阈值同轮转器），
不与服务的活动文件互踩。

## 验收与测试

§5.1 的 6 条 merge-gate 已进 CI（`.github/workflows/ci.yml`，CH-446）：`merge-gate` job 跑
`pytest -m gate`（Python 3.11/3.12/3.13 矩阵，失败阻断合并），`full-suite` job 依赖它跑全量 +
黄金样本可复现性 + `doc2md doctor`。

```bash
pytest -m gate            # §5.1 六条 merge-gate（红线，全绿方可合并；共 40 项，≈1.5 分钟）
pytest tests/             # 全量（含 M0/M2/M3/M4M6/M5/M6c/M8/M9a/DoD）
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

六条门禁（§5.1）：`shard_two_phase`、`malicious_filenames`、`injection_resistance`、
`edit_optimistic_lock`、`delete_cascade`、`log_rotation`；
另附本实现补强的 CJK 检索（CH-435）、渲染分级（CH-416/417）、目录递归防护（CH-410）断言。

## 已知实现偏差登记

方案执行期间发现的缺陷与更优做法按 §8.2 变更登记流逐条登记，编号 CH-422 … CH-446，
正文见 `docs/plan-delta-v1.1.md`（附录 H）。
