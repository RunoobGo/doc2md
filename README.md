# Doc2md

文本型 PDF / DOCX / 旧版 DOC → Markdown 的**离线**转换管线（v2.22.0）。
单机、无出站网络、无云端依赖；LLM 修补助手同样只连本地 `127.0.0.1`。

## 安装

```bash
python3 -m venv env && env/bin/pip install -e ".[dev]"
env/bin/doc2md doctor                    # 环境自检（依赖 / 目录权限 / tokenizer 口径）
```

输入格式：文本型 PDF、DOCX 与旧版 `.doc`。`.doc` 通过 `s1.legacy_doc_backend`
（`auto`/`libreoffice`/`word`/`none`，默认 `auto`）调用本机 LibreOffice 或 Microsoft Word
转换为临时 DOCX 后解析；无可用后端时按 E4151 拒收，临时文件只落在私有 `work/tmp`。

命名纪律：标识符一律小写 `doc2md`；四个常驻/定时标签 `md.doc2md.cron`、`md.doc2md.cron.weekly`、`md.doc2md.serve`、`md.doc2md.work`，macOS 的 plist 与 Windows 的任务名逐一对应（定时两档随包分发即用；常驻两档是 CH-480 / T12 补齐的资产，会长期占驻进程，需按下节显式安装）。

## CLI

```bash
doc2md serve [--host H --port P --root DIR --config F]   # API 服务（默认 127.0.0.1:8090）
doc2md serve token [--role R --label L | --revoke TOKEN | --list]
doc2md cron --daily | --weekly [--no-backup]
doc2md convert <file|dir> [--mode auto|single|two_phase --out DIR --workers N --json --follow-symlinks \
              --force --skip-existing --format zip|md|both --llm-mode offline|server --llm-no-fallback]
doc2md work [--once --max-jobs N --workers N --idle S --enqueue PATH --status]
doc2md watch <dir> [--once --interval S]
doc2md search QUERY [--full] [--limit N]           # 检索已入库文档（API /search 的 CLI 等价物；CH-491）
doc2md docs list [--limit N --offset N --status S] # 库内文档清单（分页信封 + 块计数；O-07）
doc2md docs show <doc_id> [--limit N --offset N]   # 单篇元数据 + 块窗口（API /docs/<id> 等价物）
doc2md docs export <doc_id...> | --all [--out DIR] # 导出 content.md（与 zip 内逐字节相同）+ manifest.json 对账
doc2md tokenizer export F [--vocab V --manifest M]
doc2md tokenizer verify --manifest M --gguf F
doc2md backup drill [--keep]                     # 隔离沙箱内跑完整恢复闭环（ops/backup.md）
doc2md backup restore SRC [--to PATH] [--force]  # 离线恢复（v1/v2 容器；CH-485）
doc2md doctor
```

`--format`（CH-491）：`zip`（默认，行为不变）= `content.md` + `ir.json` + `report.html`；`md` = 只写 Markdown 直出文件（与 zip 内 `content.md` 逐字节相同，提取后移除 zip）；`both` = 两者都写。文件名取消毒后的源文件名。

`doc2md search`（CH-491）：不启 serve 也能检索已入库文档，与 `GET /search` 同一实现、同一默认口径（`limit` 默认 20、上限 500 截断不报错），结果集逐条相同；`--full` 走 `blocks.text` 全表 LIKE，覆盖 FTS 索引窗口外的块尾部；`--offset N`（CH-500 / R17-API-7）与 API `?offset=` 同一游标，用于取回 `has_more=true` 之后被截断的命中；库不存在时明确 `E4041` / 退出码 4。


`doc2md convert --skip-existing`（O-08 + N-1）：内容寻址增量跳过——优先查库（源文件 sha256 与库内已有文档命中即跳）；库未命中时回退查产物（`out/<doc_id>.zip` 存在**且** front-matter `pipeline_version` 与当前管线一致才跳，陈旧产物/字段缺失/产物损坏一律照常重转）。跳过报告 `status=skipped`、退出码 0，既有产物逐字节不触碰；源文件内容变过（含追加字节）则照常重转，不会误跳。watch 重启「只转新文件」与它共用同一实现。

旧版 `.doc`（Word 97-2003 二进制格式）依赖系统安装 LibreOffice 或 Microsoft Word 做转换后端（`s1.legacy_doc_backend`，默认 auto）：两者都不可用时该类文件明确报错 E4151 而非静默产出残缺结果；纯 Python 内置解析器不在路线图内（C-35 评估结论，见 `docs/qa/optimization-plan.md` O-12）。
`doc2md docs`（O-07）：不起 serve 的完整读取面——`list` 列库内文档（分页信封 + 每篇块计数，条数与 `SELECT COUNT(*) FROM docs` 一致，gate 红线把住）；`show <doc_id>` 看单篇元数据与块窗口（与 `GET /docs/<id>` 同参逐字段相同，含 `warnings` 降级告警）；`export` 把 `content.md` 原样落盘（与 zip 内逐字节相同，zip 丢失时按 DB 自动重建）并写 `manifest.json` 对账（doc_id ↔ 文件 ↔ sha256），`--all` 全量导出、同批同名冲突自动加 `-<doc_id 前 8>` 后缀。三个子命令全部只读（零 DB 写入）。

转换期间 stderr 打五阶段进度（`s1` / `s1_5` / `s3` / `s4` / `package`），`--json` 时静音（CH-491）。

退出码：`0` 成功 / `2` 用法错误 / `3` 门禁拒收（E4221 等） / `4` 业务错误 / `5` 系统错误。多文件转换按「取最严重」聚合（逐条算码后 `max`）。
`2` 覆盖四类「调用方写错」：`serve --host` 非回环且未设 `api.allow_remote_bind`、`serve token` 的
`--role` 不在 `api.token_roles`、**配置错**（`doc2md.toml` 语法错误、`--config` 指向不存在的文件、`doctor` 检出冻结键/配置语义非法）、
`docs` 三子命令的负 `--limit` / `--offset` 与互斥参数（SQLite `LIMIT -1` 是无界的反直觉语义，与 argparse 对 `--limit abc` 同口径退 2）。
`4` 覆盖业务失败：`serve token --revoke` 的未知 token（E4041）、转换撞上 E4091 已有编辑且未 `--force`、
`backup drill` 演练失败（`DrillFailure`）、`backup restore` 恢复失败（`E5031`）、`work` 入队拒收、
`docs show` / `docs export` 的未知 doc_id（E4041）与导出单篇失败（逐篇报错后继续，rc 取 4）。
`5` 覆盖系统/环境失败：`serve` 端口占用等 `OSError`、`cron` 任一步骤失败、**`doctor` 判定环境不健康**（依赖 `MISSING` /
server 模式 LLM 不可达 / 显式指定的 `.doc` 后端不可用 / 磁盘不足 `free_mb < min_free_mb` 或探测失败 `-1`）、
`work` 存在失败作业、未归类异常（消息只带异常类名，不含正文，NFR-20）。
逐命令的完整退出码表见 `docs/dev-plan.md` 附录 B。
CI 里 `doc2md convert --json` 的 stderr 会打印门禁摘要（键为 `mode` / `sampled_pages` / `reasons` / `stats`，CH-462 实核）。

## 快速开始

```bash
doc2md serve &                                  # 起 API（root 自动 chmod 700）
TOK=$(doc2md serve token --role admin --label local | tail -1)
curl -s -X POST localhost:8090/jobs -H "X-Doc2md-Token: $TOK" \
     -H 'Content-Type: application/json' -d '{"path":"./samples/contract.pdf"}'
curl -s "localhost:8090/search?q=例外" -H "X-Doc2md-Token: $TOK"
```

`POST /jobs` 的 `mode?` 与 CLI `--mode` 同集合（`auto` / `single` / `two_phase`），非白名单值在转换与入队之前即 **400/E4001**、队列零增长（CH-476 / R7-SVC-2）。大批量目录共享一把进程池闸门，60s 内排不到队回 **503 + `Retry-After`**（`detail.reason = sync_pool_busy`，CH-476 / R7-SVC-3）。

## 配置

内置默认值见 `doc2md/config/default_toml.py`；部署样例见 `config/doc2md.example.toml`；逐键语义与默认值见方案 附录 A。
读取顺序（后者覆盖前者）：默认 → `$DOC2MD_HOME/doc2md.toml` → `./doc2md.toml` → 显式 `--config`。

相对路径（`work/out`、`work/reports/backups` 等）会随 `[paths].root` / `--root` 一起迁移（CH-432）。

## 低配机器（Win11 无 GPU / Mac mini 16GB）

> **测量口径（CH-491 统一）**：下表驻留数字一律为「**320 页样本 / 单进程 / `ru_maxrss`**」，取自 **Linux 4 vCPU / 8GB 沙箱**（本机 macOS arm64 复测见方案 12.1）。同一事实此前在本文、`config/default_toml.py`、`config/doc2md.example.toml`、方案第七章与第十二章**五处写法不一**（1290→176 / 1025→177 / 1025→176 /「32 页 ≈ 180MB」），原因是各自省略了基准档；现统一为下面的**三档序列**，完整三档与耗时、解析阶段单独归因见方案 **12.3**。Win11 与 Mac mini 真机同口径数字登记为 B-6 / B-8，未实测前不得当作目标机结论。

文本管线是 CPU + 内存带宽问题，GPU 无收益；下列口径全部有实测支撑（方案 第七章 + 第十二章证据）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `[s1].chunk_pages` | `32` | S1 分块重开 + 每页缓存清理。320 页样本峰值驻留三档：**1290MB**（改造前 HEAD，整本一次读完）→ **1025MB**（`chunk_pages=0`，仅每页 `flush_cache`）→ **176MB**（`chunk_pages=32`，默认档；早期登记的 177MB 与本轮复测 176MB 属同一量级抖动，统一取 176）。产物字节级等价 |
| `[perf].workers` | `0` | 批量转换进程数，`0 = min(4, CPU)`。内存 ≈ workers × 页窗口（4 进程并发跑同一 320 页样本：分块关约 **4100MB**、分块 32 约 **708MB**），8GB 机器别调到 8 |
| `[perf].queue_async` | `false` | `true` → `POST /jobs` 只入队（实测 13 个作业 **13ms** 返回 202），后台 `doc2md work` 消化；`GET /jobs` 看进度 |
| `[perf].min_free_mb` / `disk_safety_factor` | `512` / `3` | NFR-6 磁盘门禁：写产物/备份前校验剩余空间，不足 → **413 / E4131**，不留截断 zip |
| `[perf].stale_job_minutes` | `30` | 崩溃遗留的 `running` 队列任务超时重回 `queued`（仅限队列来源，不动同步请求行） |
| `[perf].stale_check_seconds` | `60` | 常驻 `doc2md work` 进程内重跑遗骸回收的节流周期；`0` = 只在启动时回收一次（CH-469：跑几天后不再靠重启清遗骸） |
| `[perf].batch_file_timeout_s` | `0` | 批量进程池内**单文件**墙钟上限，`0` = 不设限。判定口径是**空窗**：连续这么多秒没有任何文件完成，就把仍在排队的件逐件记 `E5000` 并收池（CH-469：损坏 PDF 能让 pdfplumber 无限挂，此前一件挂死整批陪绑） |
| `[llm].retries` | `2` | **仅连接类瞬断**（`URLError` / 超时 / `OSError`）重试，覆盖 llama-server 冷启动与重启窗口；HTTP 状态类错误不重试（那是响应不是抖动），`0` = 关闭 |
| `[llm].mode` / `fallback` | `offline` / `true` | 修补助手实现：`offline` = 确定性占位（默认，主链路零网络）；`server` = 本地 llama-server（仅 `127.0.0.1:8080`）。服务不可用时回退 offline 保持主链路；`--llm-no-fallback` 则明确失败（E5041 / 503） |
| `[llm].op_whitelist` | 5 个 op | 白名单是唯一防线（+5 道校验 + 不可信数据立场），模型听话不是防线；server 与 offline 两条路径同一套校验 |

模型接入实操（选型 / 下载 GGUF / llama-server 启动 / 故障排查）见 `ops/llm.md`。

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

要点在 `ops/backup.md`：外置副本强制 AES-256-GCM 分块容器（v2，`b"D2MBAE2"` 前缀、
随机盐 + 序号 AAD；历史 v1 `b"D2MBAE1"` 只读兼容），口令只走
`DOC2MD_BACKUP_PASSPHRASE`；macOS 备选 `hdiutil create -encryption AES-256 … SPARSEBUNDLE`。
未配置外置卷 → 周报该步骤 `skipped`（不算失败）；配置了外置卷但无口令 → `ok=False`，**不落明文**。
已删数据 ≤14 天从全部副本自然轮出（FR-128）；`predelete-*` / `pre-restore-*` 由每日 cron 同步轮出，最新一份也受 14 天约束。v1 历史容器可用 `backup restore` 恢复（CH-485）。

## 定时任务（launchd）

```bash
mkdir -p work/logs                    # launchd 在 spawn 阶段就要打开重定向文件
cp ops/launchd/md.doc2md.cron*.plist ~/Library/LaunchAgents/
# 资产里的 /Users/you/doc2md 是示例前缀，安装前必须改成实际仓库路径
sed -i '' "s#/Users/you/doc2md#$PWD#g" ~/Library/LaunchAgents/md.doc2md.cron*.plist
plutil -lint ~/Library/LaunchAgents/md.doc2md.cron*.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.cron.weekly.plist
launchctl list | grep doc2md          # 校验（R6）：应列出两个标签
```

> GNU sed 用户把 `sed -i ''` 写成 `sed -i`；路径替换后 `grep /Users/you` 应为空。

`--daily` = 日报 → 本地快照（磁盘门禁是它的前置检查，不是独立步骤）→ 轮出 → tmp 清理 → 日志轮转检查
→ events / 终态 tasks 保留期清理 → 搜索索引对账；`--weekly` = 同一序列，只在本地快照成功后、**轮出之前**插入
外置加密增量（新副本当轮即受轮出保护）。
全部 10 个步骤名与失败语义见方案 附录 E。
口令不要写进 plist；用 `launchctl setenv DOC2MD_BACKUP_PASSPHRASE …` 或 Keychain 包装脚本注入。

## 常驻服务（launchd，CH-480 / T12）

```bash
mkdir -p work/logs
cp ops/launchd/md.doc2md.serve.plist ops/launchd/md.doc2md.work.plist ~/Library/LaunchAgents/
sed -i '' "s#/Users/you/doc2md#$PWD#g" ~/Library/LaunchAgents/md.doc2md.serve.plist \
    ~/Library/LaunchAgents/md.doc2md.work.plist
plutil -lint ~/Library/LaunchAgents/md.doc2md.serve.plist \
    ~/Library/LaunchAgents/md.doc2md.work.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.serve.plist
launchctl load ~/Library/LaunchAgents/md.doc2md.work.plist
launchctl list | grep doc2md          # 校验：定时 + 常驻四个标签齐全
```

`md.doc2md.serve` 登录即拉起回环 API：ProgramArguments 显式钉 `--host 127.0.0.1`（NFR-1 离线
单机姿态），端口不写进资产、取配置 `api.port`；要对外暴露还得同时设 `api.allow_remote_bind`，
缺一 serve 直接拒绝启动。`md.doc2md.work` 登录即拉起 tasks 队列的常驻消费者（并发取
`perf.workers`，队列空时 5 秒一轮；serve 不起常驻线程，异步转换归它，见 ADR-30 / CH-449）。

两档均为 `RunAtLoad` + `KeepAlive{SuccessfulExit: false}` + `ThrottleInterval 30`：
**崩溃（非零退出）自动重启，正常退出与 `launchctl unload` 不会被复活**，重启风暴由 30 秒节流兜住。
它们会长期占用一个监听进程加一个转换进程池，8GB 机器可只装 serve、需要时手跑
`doc2md work --once` 排空即退。实机「开机自启 + 崩溃自恢复」：macOS 已验（2026-10-18），Windows `Register-ScheduledTask` 腿待验（B-9）。

## 定时任务（Windows 任务计划）

```powershell
pwsh ops/windows/install-doc2md-tasks.ps1 -SetPassphrase   # 安装 + 交互式设置用户级口令
pwsh ops/windows/install-doc2md-tasks.ps1 -WithResident     # 定时两档 + 常驻两档
pwsh ops/windows/install-doc2md-tasks.ps1 -VerifyOnly       # 校验（等价 launchctl list）
pwsh ops/windows/install-doc2md-tasks.ps1 -Uninstall        # 卸载
```

注册 `md.doc2md.cron`（每日 00:00）与 `md.doc2md.cron.weekly`（周日 00:30）两个任务，
以当前用户最低权限运行，无需管理员；电池供电照跑、错过的时间窗开机补跑。
常驻两档（`md.doc2md.serve` / `md.doc2md.work`，CH-480 / T12）**默认不装**，要显式加
`-WithResident`：登录触发（LogonTrigger）、`ExecutionTimeLimit` 取 `PT0S`（不限时长）、
`RestartOnFailure` 间隔 1 分钟最多 10 次、`MultipleInstancesPolicy=IgnoreNew`（重复触发让位给
在跑的实例）。平台语义差如实保留：launchd 侧是无限重启 + 30 秒节流，Windows 侧重启次数有上界，
超限后需手工重启任务。`-VerifyOnly` 不带该开关时，未安装的常驻档记 SKIP 不算失败；
`-Uninstall` 不分口径，四档一律清理，常驻档先 `Stop-ScheduledTask` 再注销。
任务定义（`ops/windows/*.xml`）不含任何口令；时间触发按**本机本地时间**解释
（launchd 的 `StartCalendarInterval` 同样按**系统本地墙钟**触发——plist 里的 `TZ=UTC` 只设定子进程读到的时钟口径、不改触发时刻；要与 Windows 严格同频两边都按本地钟即可，需要 UTC 口径则把机器时区设为 UTC，或用 `-DailyAt` 换算）。
排障：`Get-ScheduledTask -TaskName md.doc2md.* | Format-List *`。

## 日志（NFR-19 / CH-444）

轮转由**服务自身**执行，不依赖 newsyslog/cron：`doc2md serve` 启动即打开 `work/logs/app.jsonl`，
按 `[logs].rotate_mb`（默认 50MB）或跨 UTC 日滚动到 `app.jsonl.1 … .{rotate_keep}`，
轮转事件记于新文件首行 `{"code":"E_LOG_ROTATE", …}`；服务启动同时清理 `work/tmp/` 残留。
`job_done` / `text_edited` / `settings_updated` / `system_reload` 除入 events 表外同步落该日志，
正文类字段一律以 sha256 指纹写入（NFR-20）。

`doc2md cron` 是与 serve 分离的进程，各写自己的 `work/logs/cron.jsonl`（同阈值同轮转器），
不与服务的活动文件互踩。`doc2md work`（CH-449）同理写自己的 `work/logs/work.jsonl`——
rename 轮转只能有一个属主，三类进程三份文件。

任务计划的输出捕获另算一档：`ops/launchd/*.plist` 的 `StandardOutPath` / `StandardErrorPath` 落在
`work/logs/launchd-<档>.out` / `.err`，由 launchd 直接追加。**这两份文件握在 launchd 手里（O_APPEND），
改名式轮转会失效**——轮出后 launchd 仍往旧 inode 追加，新文件永远空着，增长只是换个地方。所以
`doc2md cron` 的 `log_rotation` 一步对它们走**就地截断保尾**（CH-481）：超过 `[logs].rotate_mb` 即把内容
削到 `rotate_mb / rotate_keep`（`rotate_keep=1` 时取半，保证必然收缩），保留 inode、不产生 `.1` 兄弟文件，
并在开头写一行 `{"code":"E_LOG_CLIP", …}` 标记，让读者分得清「日志从头就在这」与「头部已丢弃」。
保尾从完整行起头；证据只含档名与字节数入日报（NFR-20），单档截断失败只记 `capture_errors` 不拖垮日流程。
定时两档每轮只写几行，实际不会触界；**常驻 serve 会把 Werkzeug 的访问行一并写进 `.err`**，靠这一步封顶。
Windows 任务计划不捕获标准输出，日志目录里没有 `launchd-*` 档，这一步在那里是空证据、不是异常。

## 文档地图

`docs/` 现有 **28 份 / 15,691 行**（顶层 `.md`，`wc -l` 实测，CH-509 收口终测；较 CH-508 的 28 份 / 15,688 行 +3 = `dev-plan.md` +2 行（2,329→2,331：CH-509 登记行与 §12.10 v3.41 行） + `technical-reference.md` +1 行（728→729，CH-509 的取证基线与 §7.2 行内刷新）；CH-508 时为 28 份 / 15,688 行（较 CH-507 +4 = `dev-plan.md` +3（2,326→2,329）+ `technical-reference.md` +1（727→728））；CH-504 时为 28 份 / 15,655 行（+178 = `review-round19-CH-504.md` 126 + `dev-plan` +49 + `technical-reference` +3）、CH-503 时为 27 份 / 15,477 行（+154 = `review-round18` 100 + `dev-plan` +53 + `technical-reference` +1）、CH-502 时为 26 份 / 15,323 行、CH-501 时为 25 份 / 14,600 行、CH-500 时为 25 份 / 14,526 行、CH-499 时 24 份 / 14,340 行、CH-498 时 24 份 / 14,333 行、CH-497 时 14,327 行，增量除 `dev-plan` 自身的登记与口径同步外，CH-500 另有 `review-round17-CH-500.md` 151 行、CH-501 只增 `dev-plan` 自身 74 行）。另有 **`docs/qa/` 子树 24 份 / 9,705 行**（`find docs/qa -name '*.md'` 实测，CH-507 收口终测；较 CH-504 档 +2 份 / +360 行 = `round4/` 新增 `negative-assertion-inventory.md` 272 行 + `ooxml-polymorphism-audit.md` 88 行；CH-504 档较 CH-503 为 +4 份 / +2,703 行 = 新增 `round4/` 四份（`test-plan` 1,835 / `comparison` 104 / `deviation-analysis` 269 / `optimization-plan` 495，QA 第四轮 137 例）；CH-503 档较 CH-501 为 +47 行对账追加：顶层 +18（`casebook` / `eval-report-v2` / `automated-test-plan-v2` 的改判与派生列）、`round3/` +29（`fix-verification.md` 157→180 增第十八轮对账节、`optimization-plan.md` 368→374 增对账块）），分四层：**顶层 8 份 / 2,595 行**（QA 优化轮 2.13–2.15 的 `casebook` / `eval-report-v2` / `automated-test-plan-v2` 与两轮 `optimization-plan` / `optimization-report` 等，O-01~O-12 / N-1~N-8 的过程产物；**CH-501 的实机自动测试体系方案与用例卡就在这里**：`automated-test-plan-v2.md` 318 行 + `casebook.md` 332 行 / 15 张十字段用例卡）、**`docs/qa/round2/` 5 份 / 1,793 行**（第二轮 80 例 / 13 偏差 D1–D13 的 `test-plan` / `comparison` / `deviation-analysis` / `optimization-plan` / `fix-verification`）、**`docs/qa/round3/` 5 份 / 2,254 行**（第三轮 102 例 = 核心 95 + 难度腿 7：`test-plan` 1,360 / `comparison` 110 / `deviation-analysis` 230 / `optimization-plan` 374 / `fix-verification` 180；后两者含 O-h 覆盖率改判与收口终轮复跑取证）、**`docs/qa/round4/` 6 份 / 3,063 行**（第四轮 137 例 = 核心 127 + 难度腿 10：`test-plan` 1,835 / `comparison` 104 / `deviation-analysis` 269 / `optimization-plan` 495 / `negative-assertion-inventory` 272 / `ooxml-polymorphism-audit` 88；本轮核心面**自己抓出一项真缺陷** R19-PIPE-3，来源是把第三轮记作缺口的 legacy `.doc` 改判为可执行；CH-505～CH-507 又补上负断言清单与 OOXML 编号多态排查两份）。**判读地位**：三轮 QA 文档是「过程存档」，不是规格——规格仍以 `docs/dev-plan.md` 主题篇 + 附录 A–G 为唯一权威，实机测试体系的权威口径是 **§8.7**；但 round3 的 **B-43 / B-44 两项是发布阻断项**，`docs/dev-plan.md` §12.7 有对应登记行，两处必须同读。QA 产物不计入顶层 28 份。此前本文只索引 `docs/` 里的 2 份（`dev-plan.md` 与 `plan-delta-v1.1.md`），其余无任何入口——查规格的人无从判断「`review-round8-CH-483.md` 是权威还是历史」。下表按两个角度给出完整地图；**地位列是判读依据，不是推荐阅读顺序**。

### 表一：按问题找权威

| 我想知道 | 去哪看 |
| --- | --- |
| 规格 / 契约 / 配置键 / CLI / API / 错误码 / 数据库 / 门禁 | `docs/dev-plan.md` 主题篇（一–九章）+ **附录 A–G**（配置 / CLI / API / 错误码 / 数据库 / 门禁 / 模块清单的查询表） |
| 某次改动为什么做、证据与未验证项是什么 | `docs/dev-plan.md` 登记篇（十–十二章：登记流 / 逐条变更 CH-xxx / 实测证据与诚实标注的未验证项） |
| 代码层结构、依赖纪律、技术栈选型边界 | `docs/dev-plan.md` §3.5–3.7 + 附录 G（模块与代码清单） |
| 怎么装定时任务 / 常驻服务 / 备份 / 本地模型 | 本文对应小节（launchd / Windows 任务计划 / 备份加密 / 日志）+ `ops/backup.md` / `ops/llm.md` |
| 这个错误码意味着什么、我该怎么办 | `docs/dev-plan.md` 附录 D（段位与触发口径）；「下一步动作」类指引见各失败面的 `detail` |
| 本工具支持什么、不支持什么 | `docs/dev-plan.md` §1.2 目标与非目标（图片 / 公式 / 扫描件 OCR 均为非目标） |
| 低配机内存与吞吐怎么调 | 本文「低配机器」小节的**三档驻留序列** + `docs/dev-plan.md` §第七章与 **12.3**（完整三档与测量方法） |
| 实机自动测试怎么跑、六层口径与用例卡 | `docs/dev-plan.md` **§8.7**（六层体系 + 三条不可让口径 + 优先级口径）；方案与用例卡在 `docs/qa/automated-test-plan-v2.md` / `docs/qa/casebook.md`；一条命令见本文「实机自动测试」小节 |
| 想一次读懂项目全貌（设计需求 / 架构 / 技术选型 / 实现方案 / 难点与解法 / 开发史） | `docs/technical-reference.md`（综述层，按「理解一个系统」的顺序重排；**不新增规格条款**，冲突以 `docs/dev-plan.md` 为准） |

### 表二：`docs/` 全清单与地位（28 份）

| 文件 | 行数 | 地位 | 什么时候读 |
| --- | --- | --- | --- |
| **`docs/dev-plan.md`** | 2,326 | **唯一权威** | 任何时候要查规格。主题篇为当前有效规格（正文不含变更编号），登记篇记录变更与证据 |
| **`docs/technical-reference.md`** | 727 | 综述（**非规格权威**，CH-502 新增） | 要一次读懂「设计需求 → 架构 → 技术选型 → 实现方案 → 难点与解法」，或要查某个难点当时的根因、落点与取证形态时；含 120 提交（锚定具名提交 `694279a`）/ 十九轮审查的开发史一览 |
| `docs/spec-ch492-p1-a1.md` | 1,000 | 过程存档（CH-492 实施规格） | 要查 P-1 单趟字号直方图 / A-1 api↔worker 解耦的原始论证与等价性实测时（**代码已落地**，勿据以重复实施） |
| `docs/spec-ch492-q2-deferred.md` | 274 | 过程存档 · **受限待办** | 要查 CH-492 主动搁置项与其受限原因时（同样勿据以实施） |
| `docs/pm-review-CH-491.md` | 258 | 过程存档（产品审查） | 要查「功能缺口 F-01…F-14 / 文档失真 D-01…D-20」的清单与验收口径时 |
| `docs/review-round18-CH-503.md` | 100 | 过程存档（第十八轮全面审查 · CH-503 出口） | 要查本轮 17 项编号发现（管线 R18-PIPE-1/2 两条产物字节级修复 / GUI R18-GUI-3/4 / 判定层 R18-QA-1/2 + R18-BA-34 / 文档层 10 处，含新立红线 R18-DOC-1）的原始论证、「等价性探针抓到 PIPE-2」的发现路径、以及 7 实施 + 10 文档 = 17 的处置对账时；出口取证（gate 642 / 全量 1475 / 覆盖率三写 / 六层 REALTEST）在报告头部与 §六 |
| `docs/review-round19-CH-504.md` | 126 | 过程存档（第十九轮全面审查 · CH-504 出口） | 要查本轮 **34 项编号**发现（管线 R19-PIPE-1/2/3 + PERF-1/2 / 服务面 ARCH-1/3/4/5/6 + API-1/4/5/6 + SVC-1 / 审阅页 GUI-1…4 / 判定层 QA-1…4 / 文档层 DOC-1…5 + API-2/3 / 撤销 ARCH-7）的原始论证、**22 实施 + 10 文档 + 1 撤销 + 1 登记 = 34** 的处置对账、以及 M1–M18 与 M-N1…M-N3 变异证明（含 M-N3 诚实记为不承重的死代码清理）时；出口取证（gate 683 / 全量 1516 / 覆盖率 7,843-737 / 六样本 6/6 等价）在报告头部与 §八 |
| `docs/review-round17-CH-500.md` | 151 | 过程存档（第十七轮全面审查） | 要查本轮 50 项编号发现（架构 AR-1…12 / GUI·API R17-API-1…8 + AG-1…3 / 管线 R17-PIPE-1…6 + PERF-1…4 / SQL·服务·打包 R17-SQL-1 + R17-SVC-1…2 + R17-PKG-1 / 文档漂移 13 处）与 18 项独立实施、17 项 B 类登记（B-30…B-46，含 B-43/B-44 两条发布阻断；其中 7 项不占 50 编号，对账恒等式见报告处置段）、5 项撤销的原始论证及「发布判定＝阻断」的分档依据时 |
| `docs/review-round14-CH-495.md` | 126 | 过程存档（第十四轮全面审查） | 要查本轮 46 项发现（架构 F1–F12 / API·GUI G1–G8 / 管线·性能 A1–A7·B1–B7 / 文档 #1–#11）的原始论证与 A 类实施 / B 类登记分类时 |
| `docs/review-round13-CH-491.md` | 322 | 过程存档（第十三轮技术审查） | 要查本轮架构 / 性能 / 死代码类发现的原始论证时 |
| `docs/review-round12-CH-490.md` | 44 | 过程存档 | 追某轮审查的原始论证时（勿据以实施） |
| `docs/review-round11-CH-489.md` | 111 | 过程存档 | 同上 |
| `docs/review-round10-CH-486.md` | 143 | 过程存档 | 同上 |
| `docs/review-round9-CH-485.md` | 1,112 | 过程存档 | 同上 |
| `docs/review-round8-CH-483.md` | 1,754 | 过程存档 | 同上 |
| `docs/review-round7-CH-476.md` | 329 | 过程存档 | 同上 |
| `docs/review-round6-CH-475.md` | 228 | 过程存档 | 同上 |
| `docs/review-round4-CH-471.md` | 470 | 过程存档 | 同上 |
| `docs/adr-o11-inline-marks.md` | 100 | 决策记录（ADR · QA 轮 O-11，**已接受原则 · 分阶段推迟**） | 要查「IR 行内 marks 由块级布尔改为行级粒度」的设计取舍、为何推迟、以及局部 bold/italic 当前丢失的根因时 |
| `docs/compliance-audit-CH-487.md` | 90 | 过程存档 · **孤儿（待回灌）** | CH-487 的合规审计结论。`dev-plan` 与本文件互相无引用，结论无入口——**登记 CH-492 决定是否回灌与如何挂引用** |
| `docs/spike-vlm-layout-hints.md` | 267 | 过程存档 · **孤儿（待回灌）** | VLM 版面线索的技术预研。同样零引用，**CH-492 决定是否把结论落进 9.2 待办或 12.7** |
| `docs/Doc2md项目开发方案V1.0.md` | 1,142 | 历史蓝本 | 追 v1 沿革（12.10 有引用）；已被 `dev-plan.md` 取代 |
| `docs/PDF-DOCX转Markdown离线管线-v2方案(Mac_mini_M4适配).md` | 1,182 | **已吸收存档** | 内容已被 v2.0 重写吸收（12.10 v2.0 行），**零引用** |
| `docs/pdf-docx-to-md-plan-v2.1-macmini-m4.md` | 1,946 | **已吸收存档** | 同上，v2.1 增量已并入 v2.0 |
| `docs/PDF-DOCX转Markdown管线-v2.2-优化完善与变更记录.md` | 491 | **已吸收存档** | 同上，CH-422…CH-446 已迁入第十一/十二章 |
| `docs/PDF-DOCX转Markdown管线-v2.3-全面优化与变更记录.md` | 415 | **已吸收存档** | 同上 |
| `docs/PDF-DOCX转Markdown管线-v2.4-全面优化与变更记录.md` | 417 | **已吸收存档** | 同上 |
| `docs/plan-delta-v1.1.md` | 33 | 存根 | 不读，仅指向主文档 |

> **归档口径（CH-491 决策）**：上表标「已吸收存档」的 5 份（合计 4,451 行）**本轮只做地位标注，不做物理移动**——保留历史优先。是否 `git mv` 到 `docs/archive/` 登记为 **CH-492** 再议。标「孤儿」的 2 份同理，先补引用还是先归档，一并归入 CH-492。

运维侧另有两份细则，不在 `docs/` 下：**`ops/backup.md`**（备份介质与恢复演练细则，94 行）、**`ops/llm.md`**（本地 LLM 模型接入实操：选型 / 下载 GGUF / llama-server / 故障排查，206 行）；Windows 任务计划的安装 / 校验 / 卸载见 `ops/windows/install-doc2md-tasks.ps1`（280 行）。

跟踪约定：契约变更、缺陷、更优做法一律按登记流写入方案 第十一章（当前编号至 `CH-509`，下一条自 `CH-510`），实测数字进第十二章并标注语料与测量方法；规格发生变化时同步主题篇章节与本文相应小节，并递增 `doc2md.PLAN_VERSION`（管线契约变化另递增 `PIPELINE_VERSION`；状态库结构变化另递增 `DB_VERSION`，CH-476 起为 `6`、CH-483 起为 `7`、CH-485 起为 `8`、CH-486 起为 `9`、CH-499 起为 `10`）。

## 验收与测试

六条 merge-gate 的 CI 定义见 `.github/workflows/ci.yml`：`merge-gate` job 跑
`pytest -m gate`（Python 3.11/3.12/3.13 矩阵，失败即 CI 红并在 CI 内阻断 `full-suite`），
`full-suite` job 依赖它跑全量 + 黄金样本可复现性 + `doc2md doctor`；`lint`（ruff）为
无人依赖的信息作业，不阻断；`windows-suite`（CH-486）在 `windows-latest` 上跑同一套 gate +
全量 + doctor，覆盖 ACL/junction/文件锁/spawn 进程池。四个作业都带 `timeout-minutes`（**6** / 10 / 30 / 25，merge-gate 原 15 分钟
按 6 轮成功 CI 的 18 条 leg 实测——中位 ~83s、最慢 2m40s——右调到 6，仍留 >2 倍余量）且测试步骤用
`-vv`（CH-471）：没有上界时一次挂死会占满 6 小时默认限，红线在那几小时里既非绿也非红；
`-vv` 才打得出逐用例行（`pytest.ini` 的 `addopts` 已含 `-q`，单 `-v` 会被抵成 0 档），
挂死时能直接指认到用例。

跨版本覆盖分两套工作流（CH-479）：上面的 `ci.yml` 是 **push 面**，`full-suite` 只在 `3.12` 单档跑一次
（红线层本就是三档矩阵，把全量也扩成三档会让每次推送多两条 30 分钟腿，而覆盖增量只落在非红线的
551 项用例上，CH-480 后为 560、CH-481 后为 563、CH-482 后为 567、CH-494 后为 611、CH-495 后为 766、CH-496 后为 769、CH-497 后为 769 项）；**周期面** `.github/workflows/ci-full-matrix.yml` 每周一 02:17 UTC（另有
`workflow_dispatch` 供取证）用 3.11/3.12/3.13 三档跑全量 + 黄金逐字节复现 + `doctor`，补上这些非红线项的
跨版本空白。这个分工由 `tests/test_ch479_ci_matrix.py` 的四条红线钉住：两工作流的版本集合必须相等、
push 面 `full-suite` 必须保持无矩阵且钉死 `3.12`、每个作业必须带 `timeout-minutes`、周期腿不得挂
`push`/`pull_request` 触发。周期腿首证 = run `39032817663`（手动 `workflow_dispatch`，三档各 `811 passed`，含黄金逐字节复现与 `doctor` 自检），push 面 = run `39032796293`（三档各 `260 passed, 551 deselected` + `811 passed` 全量）；上一轮（CH-481）push 面 = run `39041920410`（三档各 `275 passed, 563 deselected`，全量 `838 passed` 干净退出）；上一轮（CH-482）push 面 = run `39043948338`（三档各 `285 passed, 567 deselected`，全量 `852 passed` 干净退出，`doctor` 打 `2.5.0`）；最新一轮（CH-483）push 面 = run `39050319814`（三档各 `308 passed, 575 deselected`，全量 `883 passed` 干净退出，`doctor` 打 `2.6.0`）；CH-484 push 面 = run `39092382196`（三档各 `311 passed, 575 deselected`，全量 `886 passed` 干净退出，回填 run `39092772664`）；CH-485 本机 gate **339 passed / 0 skipped（34.63s）**、全量 **920 passed / 0 skipped（71.69s）**、ruff 全绿、doctor 退 0（`pipeline 2.7.0`）；远端 CI run `39096948274` 五作业全绿（lint + 3× merge-gate + full-suite）。CH-486 Windows 本机 gate **344 项 = 343 passed / 1 平台 skip（47.46s）**、全量 **934 项 = 926 passed / 8 平台 skip（118.28s）**、ruff 全绿、doctor 退 0（`pipeline 2.9.1`）；Windows 平台 skip 均为 POSIX 专有语义（chmod/tzset/waitpid/sendmsg/symlink/ps sampler），由 `is_private`/平台分支测试补位。远端 CI run `39106866387` **六作业全绿**（lint + 3× merge-gate + full-suite + windows-suite；首轮 `39106420181` 仅 Windows checkout 因长路径失败，`7b99d00` 启用 `core.longpaths` 后复跑全绿）。CH-488 本机收口 gate **380（379 passed / 1 platform skip）**、全量 **976（968 passed / 8 platform skip）**；CH-489 本机 gate **395 项 = 394 passed / 1 platform skip（48.60s）**、全量 **991 项 = 983 passed / 8 platform skip（124.03s）**；CH-490 本机 gate **402 项 = 401 passed / 1 platform skip（48.35s）**、全量 **998 项 = 990 passed / 8 platform skip（125.11s）**、ruff 全绿、doctor 退 0（`pipeline 2.12.0`）。CH-493（远端源码轮）出口数字登记在方案第十一章对应行（gate 404 全绿、全量 1015 项 0 失败 0 错误）；其推送 run `39336292727` 的 `windows-suite` 腿两度 KeyboardInterrupt——`os.kill(pid, 0)` 在 Windows 上实为向进程组广播 Ctrl+C（bpo-14484），登记为 X-18。CH-494 根治后本沙箱 Linux / Python 3.11.1 实测：gate **419 项 = 419 passed / 0 skipped（收口复跑 126.31s；首次复跑 127.75s 曾因 `PLAN_VERSION` 升位与文档锚点改写之间的竞态残留 1 条 `test_plan_version_three_way_reconciliation` 失败，非代码缺陷，详见方案 §8.1）**、全量 **1030 项 = 1027 passed / 3 skipped / 0 failed（245.39s）**、`ruff check doc2md/ tests/` 全绿（3 个 skip 均为平台 / 环境预期：2 条 Windows junction 语义 + 1 条 LibreOffice 未安装）；CH-494 修复后的远端验证 run `39404094953`：Linux 四腿（lint + 3× merge-gate）全绿，windows-suite 腿 **KeyboardInterrupt 形态消失**（X-18 根治生效，419 项 gate 跑完 132.63s 以正常断言收场），但该腿 1 failed / 418 passed——失败是 X-18 回归红线自身的 Windows 平台缺陷（monkeypatch `os.kill` 被 `_pid_alive` 的 nt 分支绕过，对锁文件 PID 1 的真实探测返回「确证已不在」），非 X-18 复发；测试已修（行为矩阵改在 `_pid_alive` 分派边界打桩，项数 15 不变），第二轮 run `39408450314` **六作业全绿**（windows-suite 腿 gate 419 passed / 100.37s、full-suite 1023 passed / 7 skipped / 229.20s），X-18 闭环。CH-497 轮 run `40071605599` 的 windows-suite 腿 **1 failed / 511 passed / 1 skipped**——失败是本轮自引入的 `test_b12_soak_harness_contract`（`soak_resident.py --help` 在 cp1252 控制台 `UnicodeEncodeError`），已修（`_console_safe` + SUMMARY ASCII 转义 + 非 POSIX 显式退 2）。**CH-498 轮 push（`0583cdc`）= run `40079222715` 六作业全绿**：三条 merge-gate 各 `521 passed, 769 deselected`（3.11 64.64s / 3.12 92.49s / 3.13 86.43s）、`full-suite`（3.12）`1287 passed, 3 skipped` 干净退出（165.41s，语句覆盖 90%、黄金逐字节 7 项、`doctor` 打 `2.17.0`）、**windows-suite 腿 gate `521 passed / 0 skipped`（117.38s）+ 全量 `1283 passed, 7 skipped`（295.30s）**——Windows 的 B-16 判据（0 failed 且 0 skipped）由此到手，方案 §12.7 的 B-16 / B-17 / B-7 三行随之闭合。
> 现状（CH-468 起）：仓库已公开，`main` 分支保护已配置——`merge-gate (3.11/3.12/3.13)` 为**必需检查**，
> 三个 gate 任一失败即禁止合并（`full-suite` 因 `needs` 同样被阻断）。验收：故意失败的 PR #1 被
> `mergeStateStatus=BLOCKED` 拦下。另有本地 pre-push 钩子双保险（见「本地红线兜底」）。
> 边界（CH-470）：必需状态检查拦的是 **PR 合并**，直接 `git push origin main` 仍会成功（远端返回
> `Bypassed rule violations … 3 of 3 required status checks are expected`）；要让"红即不可入库"对直推
> 也成立，需为 `main` 打开「Require a pull request before merging」，那是改变日常工作流的决策项。

```bash
pytest -m gate            # 红线层：六条 merge-gate + 低配机专项 + CH-469 两批安全/契约专项 + CH-473 第五轮 12 项 + CH-474 补入 X-17 1 项 + CH-475 第六轮 7 项 + CH-476 第七轮 91 项 + CH-477 版本对账 4 项 + CH-478 批次① 有序列表端到端 4 项 + 批次② 等价门缩进敏感度 4 项 + CH-479 CI 版本矩阵口径 4 项 + CH-480 常驻资产同构 7 项 + CH-481 捕获档截断 8 项 + CH-482 代码块缩进重建 10 项 + CH-483 结构修复 23 项 + CH-484 并发夹具 3 项 + CH-485 第九轮 28 项 + CH-486 第十轮 5 项 + CH-487 结构修复 24 项 + CH-488 复杂语料 12 项 + CH-489 第十一轮 15 项 + CH-490 本机收口 7 项 + CH-491 静态对账 2 项 + CH-494 X-18 回归 15 项 + QA 轮（2.13–2.15）41 项（LLM P0 契约 16 / N-04 家具扩展 11 / v2.4 门禁扩容 10 / 既有门禁补强 4）+ CH-495 第十四轮 37 项（pipeline 13 / round2 14 / service 7 / watch 3）+ CH-496 本机收口 11 项 + CH-497 B1/B-12 5 项 + CH-498 竞态/缝线/定档 8 项 + CH-499 租约/归因/跨卷 18 项与文档内部对账 3 项 + CH-500 第十七轮 38 项（API 19 / 管线 13 / 服务与运维 3 / 状态库谓词 2 / 包入口 1）+ CH-501 实机自动测试 19 项（判定层红线：难度真值只取自声明侧、TCOLC 键与产品同名、归因恒等式、9 条缺陷规则非空转、优先级分档精确且修复成本不降档、口径派生自 §12.7、真实样本隐私边界、`not_run` 不报 `pass`、junit 计数只认 XML、用例卡十字段且发布阻断缺口必有 P0 卡，全绿方可合并）+ CH-505～CH-507 QA 第四轮本机优化 45 项（契约对账 2 / 差异报告 3 / 权限矩阵 23 / 转义单一来源 11 / 编号覆盖层 4，另 `test_ch491_doc_sync` 7→8 与 `test_pdf_columns` 2→3 各 +1）+ CH-508 间接编号形态 7 项（`test_ch508_indirect_numbering.py`：basedOn 链 / numStyleLink 委托 / pStyle 反向绑定 / 悬空链不造号 / 环状 basedOn 不造号 / 缺 numbering.xml 不崩 / 裸 `Heading` 非标题）+ CH-509 Windows 宿主耦合修复 0 项（只改写 `test_ch505_perms_coverage.py` 6 项既有用例的宿主驱动方式，项数仍 735）
                          # 当前 **735** 项（CH-509 出口；构成与完整耗时历史见 docs/dev-plan.md 附录 F / 12.1。项数是硬口径，耗时只作历史）
                          # 耗时历史记录：Linux 沙箱轮次 72–110s、T8 优化后 53.0s、CH-469 本机（macOS arm64 / Py 3.15）28.7s、CH-473 62.7s、CH-475 30.0s、CH-476 33.9s、CH-477 33.25s / 31.98s、CH-478 批次① 31.54s、批次② 31.16s、CH-479 38.55s / 收口复跑 33.74s、CH-480 36.13s / 收口复跑 35.43s（新七条红线只做 plistlib 解析与 XML 读 DOM，差值属同机负载抖动）、CH-481 36.14s / 34.92s / 35.91s、CH-482 35.71s（`--junitxml` 35.36s）、QA 轮 + CH-495 本机复跑（macOS arm64 / Py 3.15，497 项全绿）`--junitxml` 33.58s、CH-497 `--junitxml` 38.30s、CH-498 `--junitxml` 41.979s（per-case 求和 41.31s，54.6% 压在两条 320 页真实转换上——见 dev-plan §9.1 门禁分层复核）、CH-499 `--junitxml` 41.934s（542 项 / 0 skipped，新增 21 项均为纯静态或纯单元断言，慢项清单未扩容）、CH-500 收口复跑 `--junitxml` **44.627s（580 项 / 0 skipped，542→572→580 的新增项同为纯单元/静态断言，未拉 320 页夹具，故慢项清单 `HEAVY_GATE_ITEMS` 与 `HEAVY_FIXTURES` 均未扩容；per-case 求和 44.144s，与套件墙钟差 0.483s 属收集与收尾；同轮另三次 580 项采样为 40.636s / 40.658s / 40.802s，±4s 散布是同机并发与 `pytest-randomly` 用例次序所致——项数与通过态才是硬口径）、CH-500 文档改完后终轮 38.155s / 末两轮 37.716s / 38.137s、**CH-501 文档全改完后终轮 `--junitxml` 同轮三点（per-case 求和 / 套件墙钟）43.776s / 44.429s、45.252s / 45.917s、41.958s / 42.454s：599 项 / 0 skipped / 0 failed 恒等，后两点是本段数字同源刷新后的复跑，同轮另一次 `--durations` 复跑 44.27s、编排器 `L1_gate` 腿墙钟 46.58s；本段散文自身是 gate 读取对象，改一次须重跑一次，故终值只认项数与通过态，八个墙钟采样同落 42.0–46.6s 带，收口后两次复跑 52.42s / 54.22s 是同机并发抬升的活例（项数仍 599 / 0 / 0 / 0）——这正是「耗时不入断言」的理由；按 12.1 的测量止句，此后不再追加墙钟引用，闭合判据只有项数与通过态；收口复跑 47.601s / 47.9s 与轮中 55.85s 属文档改动前的中途快照，按 O-h 第三形态作废——本轮 19 项新红线同为纯静态 / AST / 纯函数断言，最慢一项 `test_trace_anchors_resolve` 0.298s，`--durations` top-12 里没有任何一项来自 `test_ch501_qa_auto.py`，慢项清单 `HEAVY_GATE_ITEMS` 第三次保持未扩容**——项数是硬口径，耗时只作历史
pytest tests/             # 全量（marker 实际取值：M0+M2 / M3 / M5 / M6c / M8 / M9a / DoD / PERF，见 pytest.ini）
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

综合语料（效果回归）：`tests/corpus/` 提供 25 份 PDF/DOCX/DOC 源文件（1–40 页）与 24 份目标
Markdown，覆盖标题/段落/列表/表格/链接/代码/扫描件/10.5pt 编号/脚注尾注/修订/域代码/
smartTag/sdt/文本框/图片/页眉页脚/大文档。独立运行：

```bash
python tests/corpus/make_corpus.py       # 确定性重建源文件
python tests/corpus/run_corpus.py        # 转换并比对 expected/*.md
python tests/corpus/run_corpus.py --write-baseline  # 审阅后更新目标
```

`tests/test_corpus_expected.py` 已把该比对纳入全量信息面（非 merge-gate）；失败时实际输出
在 `work/corpus_diff/`。扫描件 `pdf_06_scanned_1p.pdf` 的目标是 E4221 拒收，不生成 Markdown。

真实样本（B-13/B-15）的收集方法、特征清单与分析脚本见
`tests/corpus/real/README.md`；Office 模板/用户文档二进制按许可与隐私要求保留在
`work/real_samples/`，不默认入库。

六条门禁：`shard_two_phase`、`malicious_filenames`、`injection_resistance`、
`edit_optimistic_lock`、`delete_cascade`、`log_rotation`；另含本实现补强的 CJK 检索、渲染分级、
目录递归防护与低配机专项断言（完整清单见方案 附录 F）。

### 实机自动测试（CH-501，六层一条命令）

上面几条腿各自回答「代码坏没坏」，但不回答「转化效果离目标还差多少、差在哪个阶段、下一步该修什么」。
CH-501 把这五问收进一条命令（完整口径见方案 **§8.7**，方案书 `docs/qa/automated-test-plan-v2.md`，逐例卡片 `docs/qa/casebook.md`）：

```bash
.venv/bin/python tests/qa/realtest.py                                   # 六层全跑 → work/qa/realtest.{json,md}
.venv/bin/python tests/qa/realtest.py --only L0,L1                      # 只跑静态 + 门禁（无法匹配的层名当场退出，不静默选空）
.venv/bin/python tests/qa/gen_cases.py --selfcheck                      # 生成器自检：我画的东西确实在源文件里
.venv/bin/python tests/qa/gen_cases.py --casebook --fill-results work/qa --out docs/qa/casebook.md
```

六层 = **L0 静态**（ruff）/ **L1 门禁**（`pytest -m gate`，`skipped>0` 即不算通过）/ **L2 全量**（可选 `coverage run`）/
**L3 资产**（25 篇语料逐字节）/ **L4 安装与探针**（`doctor`、CLI 出包、API 探针、worker drain）/
**L5 效果**（评测 → 归因 → 缺陷扫描 → 可追溯 → 优先级 → 难度腿 → 真实样本腿，信息层、不阻断总判）。
三条不可让的口径由红线钉住（`tests/test_ch501_qa_auto.py`，19 项全部 `gate`）：**计数只认 `--junitxml` 的 `tests=`**，
文件缺失返回 `None` 而不是 0（0 会被读成「全绿」）；**`xfail` 从 `skipped` 拆出**，永久红不是通过；
**没跑的腿一律记 `not_run`，永不报 `pass`**，总判只由阻断层决定。真值来源同一条纪律：期望只来自声明侧
（`tests/corpus/hard/expectations.json` 的 `judge`/`expect` 与 `tests/corpus/manifest.json`），
卡片的实际结果只来自同轮 `work/qa/*.json` 的 `generated_at`——**从产物里取期望，就永远看不见结构性损失**。
真实文档一律放 `work/real_samples/`（gitignored），不进 `tests/`；本机没有真实样本时该腿恒 `not_run`（B-49）。

本机最新收口取证（CH-509，macOS arm64 / Python 3.15，Windows 门禁层宿主耦合修复）：**零产品改动**，`pytest -m gate` **735 项全绿 / 0 failed / 0 skipped**（终轮 `--junitxml` 套件墙钟 51.109s；项数与 CH-508 相同，修的是 `tests/test_ch505_perms_coverage.py` 里 6 项只在 POSIX 宿主成立的用例——`posix` 夹具把 `perms.os` 代理成 POSIX、`Path.stat` / `Path.chmod` 打桩为伪 `stat_result`、缓存键期望由宿主 `Path` 现算，不用 `t.Skip`），全量 **1575 项 = 1559 passed / 16 skipped / 0 failed**（终轮 `--junitxml` 套件墙钟 118.427s，真 skip 10 + xfail 6 不变）；语句覆盖率沿用 CH-508 的 TOTAL **7,942 stmts / 680 miss = 精确 91.44%**（本轮产品语句零变化）、`ruff check doc2md tests config ops` 全绿、`doc2md doctor` 退 0 且打 **pipeline 2.22.0**、语料回归沿用 **25/25**（零产品改动，未重烤）；六层一条命令给出 `REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`，两条显形项仍是 `clean_venv_install`（GAP，发布前动作）与真实样本腿（NOT_RUN，`work/real_samples/` 为空，B-49）。**远端判据**：`da1d3f7` 的 run `40519331722` 在 `windows-suite` 门禁层红同样 6 项（729 passed / 6 failed / 840 deselected），`7b297df` 的 run `40512528571` 同形——两次都是这一步要修的宿主耦合，本轮 push 后的 `windows-suite` 结果见下段。

上一轮（CH-508，macOS arm64 / Python 3.15，B-52 关闭）：`pytest -m gate` **735 项全绿 / 0 failed / 0 skipped**（终轮 `--junitxml` 套件墙钟 53.490s；项数 +7，**+7 构成 = `test_ch508_indirect_numbering.py` 7**：basedOn 链 / numStyleLink 委托 / pStyle 反向绑定 / 悬空链不造号 / 环状 basedOn 不造号 / 缺 numbering.xml 不崩 / 裸 `Heading` 非标题，逐文件求和即 7，无未归因余额），全量 **1575 项 = 1559 passed / 16 skipped / 0 failed**（终轮 `--junitxml` 套件墙钟 116.722s；**16 条 skip 拆两栏写：真 skip 10**（8 条难度骨架 TODO-skip + 2 条 Windows junction 平台门）**+ xfail 6**（D05 骨架 1 + D02/D05/D08/D09/D10 五条 strict xfail），**xfail 不是 pass**）；语句覆盖率 TOTAL **7,942 stmts / 680 miss**（精确 **91.44%**、打出值 91%，分母较 CH-507 的 7,886 增 56 = 本轮产品侧新增语句，未覆盖数 685→680）、`ruff check doc2md tests config ops` 全绿、`doc2md doctor` 退 0 且打 **pipeline 2.22.0**、语料回归 **25/25 逐字节一致**（`numStyleLink` 0 命中；13 份 python-docx 默认模板的 `lvl@pStyle` 均指向样式自身已用的同一 `numId`，反向绑定不新增标记）、难度语料对账 **6/10**（红项仍指 B-43 / B-44 / B-45 / B-47）；六层一条命令给出 `REALTEST overall=pass L1=735/0/0/0 L2=1575/0/0/16 NOTRUN=2`，两条显形项仍是 `clean_venv_install`（GAP，发布前动作）与真实样本腿（NOT_RUN，`work/real_samples/` 为空，B-49）。**测量止句**（沿用 CH-500 条款）：上面两条墙钟取自本轮最后一次代码/测试改动之后的采样；此后只做文档措辞/数字改动时，由 gate 标记的 **22 项文档红线子集**（`test_ch491_doc_sync` 8 + `test_ch477_version_sync` 5 + `test_ch498_gate_budget` 1 + `test_ch481_capture_clip` 8） + 一次 gate 复跑复证，不再改写墙钟、也不再追加采样点。
上一轮（CH-507，macOS arm64 / Python 3.15，QA 第四轮本机优化）：`pytest -m gate` **728 项全绿 / 0 failed / 0 skipped**（终轮 `--junitxml` 套件墙钟 47.561s、进程墙钟 48.05s；项数 +45，**+45 构成 = `test_ch505_contract_sync.py` 2 + `test_ch505_diff_report.py` 3（第 4 项标 corpus）+ `test_ch505_perms_coverage.py` 23 + `test_ch505_escape_single_source.py` 11 + `test_ch506_numbering_override.py` 4 + `tests/test_ch491_doc_sync.py` 行号引用棘轮 +1 + `tests/test_pdf_columns.py` 双栏等价 +1**，逐文件求和即 45，无未归因余额），全量 **1568 项 = 1552 passed / 16 skipped / 0 failed**（终轮 `--junitxml` 套件墙钟 100.287s、进程墙钟 100.91s；**16 条 skip 拆两栏写：真 skip 10**（8 条难度骨架 TODO-skip + 2 条 Windows junction 平台门）**+ xfail 6**（D05 骨架 1 + D02/D05/D08/D09/D10 五条 strict xfail），**xfail 不是 pass**）；语句覆盖率 TOTAL **7,886 stmts / 685 miss**（精确 **91.31%**、打出值 91%，分母较 CH-504 的 7,843 增 43 = 本轮产品侧新增语句数）、`doc2md/security/perms.py` 由 40% 升至 **100%（75/0）**（平台 × 入口 × 失败矩阵补齐替身分支；真 DACL 仍属 B-14）；`ruff check doc2md tests config ops` 全绿、`doc2md doctor` 退 0 且打 **pipeline 2.21.0**、语料回归 **25/25 逐字节一致**（较 CH-504 增三份 LibreOffice 形态 DOCX：`docx_12_lo_heading_numbering` / `docx_13_bullet_glyph` / `docx_14_multilevel_hidden`）、难度语料对账 **6/10**（红项仍指 B-43 / B-44 / B-45 / B-47）；`ops/bench/opt6_shard_equivalence.py` 退 0、`tests/qa/defect_scan.py` 退 0（blocking 0 / major 2 / minor 2；24 篇产出扫描通过 + 1 篇具名拒收 `pdf_06_scanned_1p`）；六层一条命令给出 `REALTEST overall=pass L1=728/0/0/0 L2=1568/0/0/16 NOTRUN=2`，两条显形项仍是 `clean_venv_install`（GAP，发布前动作）与真实样本腿（NOT_RUN，`work/real_samples/` 为空，B-49）。**测量止句**（沿用 CH-500 条款）：上面两条墙钟取自本轮最后一次代码改动之后的终轮采样；此后只做文档措辞/数字改动时，由 gate 标记的 **22 项文档红线子集**（`test_ch491_doc_sync` 8 + `test_ch477_version_sync` 5 + `test_ch498_gate_budget` 1 + `test_ch481_capture_clip` 8——CH-507 棘轮使第一项 7→8） + 一次 gate 复跑复证，不再改写墙钟、也不再追加采样点。
上一轮（CH-504，macOS arm64 / Python 3.15，第十九轮全面审查 + QA 第四轮）：gate **683 项全绿 / 0 skipped**（终轮 `--junitxml` 套件墙钟 47.444s）、全量 **1516 = 1500 passed / 16 skipped / 0 failed**（真 skip 10 + xfail 6）、覆盖率 **7,843 stmts / 737 miss = 精确 90.60%**、语料 **22/22**、六个可比黄金样本三条目内容 6/6 逐字节一致（CH-503 基线逐字符复现）。**上一轮的分界在交付字节**：用临时 `git worktree` 检出 HEAD `694279a`（2.19.0）与工作副本（2.20.0）同脚本配对实测——`ir.json` 的字段级差异只有 `.meta.pipeline_version`，六个值同时逐字符复现 CH-503 基线；**zip 容器的 sha256 六个全变**是 R19-PIPE-1 的设计后果而非回归（新码两次打包同 sha、旧码间隔 3s 两次不同 sha）。
**发布判定仍为「阻断」**：B-43 / B-44 需人工逐页对照真实 PDF 出具真值，未受本轮任何改动影响（难度语料仍 6/10，失败项 D02 / D05 / D08 / D10 与 CH-504 逐条相同）。**CH-504～CH-508 的远端复证 = 两次 `windows-suite` 红**：run `40512528571`（`7b297df`）与 `40519331722`（`da1d3f7`）除该腿外五作业全绿，门禁层 729 passed / 6 failed / 840 deselected，失败项与 CH-509 修复清单逐条相同；上一档（CH-503）远端已闭合（run `40197367060`，口径 642 / 1475）。本轮产品侧判读语义扩展（间接编号三形态 + `Heading` 无数字样式名）：带这些构造的 DOCX 交付内容改变，当前 25 篇语料零命中、逐字节不变；依 D12 升 `PIPELINE_VERSION` **2.21.0→2.22.0**（`pyproject.toml` 同步），`SCHEMA_VERSION` **14** / `DB_VERSION` **10** 不动，`PLAN_VERSION` **v3.39→v3.40**。
上一轮（CH-501，macOS arm64 / Python 3.15，六层编排器同轮取证）：`pytest -m gate` **599 项全绿 / 0 skipped**（终轮 `--junitxml` 同轮三点：per-case 求和 / 套件墙钟 43.776s / 44.429s、45.252s / 45.917s、41.958s / 42.454s——**本段散文自身是 gate 读取对象，改一次就要重跑一次，故终值取「599 / 0 / 0 / 0 恒等」而非某一串墙钟**；同轮另一次 `--durations` 复跑 44.27s；编排器 `L1_gate` 腿墙钟 46.58s；收口复跑 47.601s / 47.9s 与轮中一次 55.85s 都是文档改动前的中途快照，按 O-h 第三形态作废、不作终值——同机并发与 `pytest-randomly` 的用例次序本就造成 ±4s 级散布，**项数与通过态才是硬口径**），全量 **1432 项 = 1416 passed / 16 skipped / 0 failed**（终轮 `--junitxml` per-case 求和 97.045s / 套件墙钟 97.804s / 进程墙钟 98.26s，编排器 `L2_full` 腿墙钟 106.15s，同轮覆盖率腿 `coverage run -m pytest tests/` 进程墙钟 96.50s（与 CH-500 的 95.600s 同口径可比）；收口复跑的 105.682s / 106.46s / 103.78s 与带 `--coverage` 的 134.75s 均属文档改动前的中途快照，只作历史；84 个 classname 求和 = 1432 与 `tests=` 一致；**16 条 skip 拆两栏写：真 skip 10**（2 条既有 Windows junction 平台门 + 8 条 `tests/qa/test_hard_corpus.py` 的「TODO 未填断言」骨架 skip）**+ xfail 6**（`tests/qa/test_hard_gate.py` 的 5 条 strict xfail 钉住 B-43 / B-44 / B-45 / B-47 / B-48，加骨架 1 条 D05 已知缺陷）——junitxml 把 xfail 折进 `skipped` 属性，**xfail 不是 pass**）；
语句覆盖率 **91%**（`coverage report --include="doc2md/*"` 的 TOTAL **7,797 stmts / 738 miss**，打出值 91%、精确 90.53%——该精确值按本轮新立的「三写必须可复算」口径由 738/7797 重算，原行的 90.51% 是沿上一档显示值未随分母重算的继承错，已改正）、`doc2md/security/perms.py` **40%**（75/45）；`ruff check doc2md tests config ops` 全绿、`doc2md doctor` 退 0、语料回归 **22/22 逐字节一致**；难度语料对账 **6/10**（`tests/corpus/run_hard.py` 退 1：D01 / D03 / D04 / D06 / D07 / D09 达成，D02→B-43、D05→B-44 记失败，D08→B-45、D10→B-47 记缺口（真值侧口径须人工定档）。D09 本器显示达成、门禁腿却记 xfail 不是矛盾：「目录域缓存被当正文重复输出」的判据写在 `test_hard_gate.py::extra_checks()` 里，`run_hard.py` 不认那个键）。该轮的六层一条命令给出 `REALTEST overall=pass`，其中一条 GAP 是 `clean_venv_install`（空 venv 安装属发布前动作，本编排器只覆盖本机可重复子集），一条 NOT_RUN 是真实样本腿（`work/real_samples/` 无样本，B-49）——两者都显式登记，不冒充通过。**发布判定仍为「阻断」**：B-43 / B-44 在 §12.7 标着发布阻断，其用例卡 **TC-005 / TC-011**（D02）与 **TC-008**（D05）按同一派生口径自动记 **P0**；须由人工逐页对照真实 PDF 修复，或复核后出具书面豁免。
该轮产品字节零改动（全部落在 `tests/qa/`、`tests/corpus/` 与文档面）⇒ `PIPELINE_VERSION` 保持 **2.18.0**、`SCHEMA_VERSION` **14** 与 `DB_VERSION` **10** 不动，只有方案文档版本 **v3.34→v3.35**（`doc2md.PLAN_VERSION` 同步）。该轮只动判定面与文档面，逐轮数字与构成见方案 12.1 / 附录 F；实机测试体系的方案与 15 张十字段用例卡见 `docs/qa/automated-test-plan-v2.md` / `docs/qa/casebook.md`。
上一轮（CH-500，第十七轮全面审查 + QA 第三轮）取证：gate **580 项全绿 / 0 skipped**（收口复跑 `--junitxml` 44.627s，终轮复跑 38.155s、末两轮 37.716s / 38.137s，同轮六点采样项数与通过态一致），全量 **1390 项 = 1382 passed / 8 skipped / 0 failed**（95.600s，81 个 classname 求和一致；8 条 skip = 2 条 Windows junction 平台门 + 6 条难度骨架 TODO-skip/xfail）；语句覆盖率 **91%**（45 个模块 / 7797 stmts / 740 miss，精确 90.51%）、`perms.py` **40%**（75/45）；三轮 QA 探针 **49/49 · 32/32 · 30/30** 全绿；难度语料对账当时 **4 通过 / 2 失败 / 1 缺口**（D03 尚无样本，故只有 7 份——该空洞由 CH-501 的三个生成器补齐）。该轮三项产物字节改动（交付 meta 摘掉片级内部锚 `shard`、外链链接文字补转义、DOCX 行字号改按字符数加权众数）触发 `PIPELINE_VERSION` **2.17.0→2.18.0**；B-43 / B-44 的发布阻断判定即出自该轮 QA 第三轮，且值得单独记一句：**这两项缺陷在门禁 580 项、全量 1390 项、语料 22/22 三条既有腿上全部为绿**，只有第一次运行的独立真值腿给出红——覆盖数与发现能力并不相关，差别只在期望值是否独立于被测实现。
上一轮（CH-499）取证：gate **542 项全绿 / 0 skipped**（41.934s），全量 **1311 项 = 1309 passed /
2 skipped / 0 failed**（97.972s），黄金逐字节 7 项与产物面 63 项全绿，bump 的是状态库结构版本
`DB_VERSION` **9→10**；远端确认 = run `40090700532`（push `6d01dd1`）六作业全绿：三档 merge-gate
各 **542 passed / 769 deselected**、`full-suite` 1308 passed / 3 skipped（黄金逐字节 7 项全过、
`doctor` 打 2.17.0）、`windows-suite` 门禁层 **542 passed / 0 skipped**——跨卷权限那条走 `icacls`
分支，不是跳过，三口径互证 542+769 = 1308+3 = 1304+7 = **1311**。
前一轮（CH-503）远端复证**已闭合** = run `40197367060`（push `694279a`，六作业全绿；口径为该轮的 642 / 1475，**不是本批 735 / 1575 的远端证据**；本批的远端证据以 CH-509 推送后的 `windows-suite` 结果为准，见上段）。
上一轮（CH-502 收口轮）远端复证**已闭合** = run `40177650472`（push `fca6b25`，六作业全绿；口径为该轮的 599 / 1432，**是更早一档**）：三档 merge-gate 各 **599 passed / 833 deselected**（pytest 步骤墙钟 88.80 / 85.23 / 57.78s，只作历史）、`full-suite`（3.12）**1415 passed / 11 skipped / 6 xfailed** 干净退出（覆盖 TOTAL 7797 stmt / 90%、`tests/test_golden_repro.py` 的逐字节复现 **7 项全 PASSED**（Linux 与 Windows 两条全量腿各 7 项）、`doctor` 打 `pipeline 2.18.0` 且该腿 `legacy_doc_backend: unavailable`）、`windows-suite` 门禁层 **599 passed / 0 skipped** + 全量 **1411 / 15 / 6**——三口径互证 599+833 = **1432** = 本机 1416+10+6；CH-500/501 新增的 57 项红线在 Windows 门禁层是真跑而非跳过，B-16 / B-17 的「门禁层零跳过」口径由此延续到 599 项。远端全绿不降档任何 B 项：B-43 / B-44 在远端同样只表现为 xfail，人工逐页真值仍缺，发布判定不变。
仍受本机以外环境限制的复证项：真实 SMB / 网络卷（B-40）需本机以外环境。本轮另登记三项：**B-51** 需 Word / LibreOffice 的渲染真值来裁定 `isLgl` / `lvlRestart` 两个编号多态（本机只完成静态排查，见 `docs/qa/round4/ooxml-polymorphism-audit.md`）；**B-52**（`basedOn` / `numStyleLink` / `pStyle` 级联与 `Heading` 无编号）**已由 CH-508 关闭**（三形态级联落地 + 无数字样式名判读修正，7 项新红线）；**B-53**（153 条负断言里 115 条缺同主题正夹具）是**本机可执行、尚未实施**的缺口，不属于外部受限项。B-37 已由 CH-505 的转义单一来源关闭。B-47 需人工裁定「CID 无 `/ToUnicode` 的乱码文本层」的产物口径
（按 E4221 类拒收 / 新增 W 码并保留占位 / 保持现状并在文档写明），B-48 需人工裁定「DOCX 目录域缓存结果」的产物口径
（整体丢弃 / 保留但不进标题 / 保留并按文本去重），B-49 需人工放入 ≥5 篇真实样本并标注标题真值——本机
`work/real_samples/` 为空 ⇒ 真实样本腿恒 `not_run`，这是诚实状态而非缺陷。B-46（难度腿入 gate）以 B-43 / B-44 为前置，
切换点只有 `tests/qa/test_hard_gate.py` 的 `GATE_READY` 一处；B-42 仍需起停外部 LLM 服务的双环境取证。

正文里出现的 `CH-xxx` 只是索引，条目原文与实测证据统一收录在
`docs/dev-plan.md` 第十一、十二章。

## 本地红线兜底（T1 的双保险）

平台级必需检查**已生效**（CH-468，T1 关闭）：仓库为 public，`main` 上 `merge-gate (3.11 / 3.12 / 3.13)`
为必需检查，红线失败即阻断合并（用故意的红断言 PR 验收过，`mergeStateStatus=BLOCKED`）。
客户端兜底保留为双保险：`git config core.hooksPath ops/hooks` 后，每次 `git push` 前自动跑
`pytest -m gate`，红则拒绝推送（日志在 `/tmp/doc2md-gate.log`），把红线判定从一次 runner 排队
换成本地几十秒。日常不要用 `--no-verify` 绕过。
