# Doc2md

文本型 PDF / DOCX → Markdown 的**离线**转换管线（v2.6）。
单机、无出站网络、无云端依赖；LLM 修补助手同样只连本地 `127.0.0.1`。

## 安装

```bash
python3 -m venv env && env/bin/pip install -e ".[dev]"
env/bin/doc2md doctor                    # 环境自检（依赖 / 目录权限 / tokenizer 口径）
```

命名纪律：标识符一律小写 `doc2md`；四个常驻/定时标签 `md.doc2md.cron`、`md.doc2md.cron.weekly`、`md.doc2md.serve`、`md.doc2md.work`，macOS 的 plist 与 Windows 的任务名逐一对应（定时两档随包分发即用；常驻两档是 CH-480 / T12 补齐的资产，会长期占驻进程，需按下节显式安装）。

## CLI

```bash
doc2md serve [--host H --port P --root DIR --config F]   # API 服务（默认 127.0.0.1:8090）
doc2md serve token [--role R --label L | --revoke TOKEN | --list]
doc2md cron --daily | --weekly [--no-backup]
doc2md convert <file|dir> [--mode auto|single|two_phase --out DIR --workers N --json --follow-symlinks \
              --llm-mode offline|server --llm-no-fallback]
doc2md work [--once --max-jobs N --workers N --idle S --enqueue PATH --status]
doc2md watch <dir> [--once --interval S]
doc2md tokenizer export F [--vocab V --manifest M]
doc2md tokenizer verify --manifest M --gguf F
doc2md backup drill [--keep]                     # 隔离沙箱内跑完整恢复闭环（ops/backup.md）
doc2md doctor
```

退出码：`0` 成功 / `2` 用法错误 / `3` 门禁拒收（E4221 等） / `4` 业务错误 / `5` 系统错误。
`2` 也覆盖两类安全拒收：`serve --host` 非回环且未设 `api.allow_remote_bind`、`serve token` 的
`--role` 不在 `api.token_roles`；`4` 覆盖 `serve token --revoke` 的未知 token（E4041）。
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

文本管线是 CPU + 内存带宽问题，GPU 无收益；下列口径全部有实测支撑（方案 第七章 + 第十二章证据）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `[s1].chunk_pages` | `32` | S1 分块重开 + 每页缓存清理。320 页样本转换峰值驻留 **1290MB（改造前）→ 176MB**，产物字节级等价 |
| `[perf].workers` | `0` | 批量转换进程数，`0 = min(4, CPU)`。内存 ≈ workers × chunk_pages（4 进程并发：分块关合计约 4100MB，分块 32 约 708MB），8GB 机器别调到 8 |
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
已删数据 ≤14 天从全部副本自然轮出（FR-128）。

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
`doc2md work --once` 排空即退。实机「开机自启 + 崩溃自恢复」的行为待真机确认（方案 12.7 B-9）。

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

## 文档与变更跟踪

| 文档 | 作用 |
| --- | --- |
| **`docs/dev-plan.md`** | **开发方案主文档（唯一权威）**。主题篇（一–九章）为当前有效规格，正文不含变更编号，其中 3.5–3.7 给代码层结构、依赖纪律与技术栈选型边界；附录 A–G 是配置 / CLI / API / 错误码 / 数据库 / 门禁 / 模块清单的查询表；登记篇（十–十二章）集中记录阶段性变更与优化项、实测证据与未验证项 |
| `docs/plan-delta-v1.1.md` | 已归档存根，指向方案（原 v1.0 附录 H 的增量登记已整体迁入 v2.0 第十一/十二章） |
| `ops/backup.md` | 备份介质与恢复演练细则 |
| `ops/windows/install-doc2md-tasks.ps1` | Windows 任务计划安装 / 校验 / 卸载（与 launchd 同等能力） |

跟踪约定：契约变更、缺陷、更优做法一律按登记流写入方案 第十一章（当前编号至 `CH-483`，下一条自 `CH-484`），实测数字进第十二章并标注语料与测量方法；规格发生变化时同步主题篇章节与本文相应小节，并递增 `doc2md.PLAN_VERSION`（管线契约变化另递增 `PIPELINE_VERSION`；状态库结构变化另递增 `DB_VERSION`，CH-469 起为 `5`、CH-476 起为 `6`、CH-483 起为 `7`）。

## 验收与测试

六条 merge-gate 的 CI 定义见 `.github/workflows/ci.yml`：`merge-gate` job 跑
`pytest -m gate`（Python 3.11/3.12/3.13 矩阵，失败即 CI 红并在 CI 内阻断 `full-suite`），
`full-suite` job 依赖它跑全量 + 黄金样本可复现性 + `doc2md doctor`；`lint`（ruff）为
无人依赖的信息作业，不阻断。三个作业都带 `timeout-minutes`（**6** / 10 / 30，merge-gate 原 15 分钟
按 6 轮成功 CI 的 18 条 leg 实测——中位 ~83s、最慢 2m40s——右调到 6，仍留 >2 倍余量）且测试步骤用
`-vv`（CH-471）：没有上界时一次挂死会占满 6 小时默认限，红线在那几小时里既非绿也非红；
`-vv` 才打得出逐用例行（`pytest.ini` 的 `addopts` 已含 `-q`，单 `-v` 会被抵成 0 档），
挂死时能直接指认到用例。

跨版本覆盖分两套工作流（CH-479）：上面的 `ci.yml` 是 **push 面**，`full-suite` 只在 `3.12` 单档跑一次
（红线层本就是三档矩阵，把全量也扩成三档会让每次推送多两条 30 分钟腿，而覆盖增量只落在非红线的
551 项用例上，CH-480 后为 560、CH-481 后为 563、CH-482 后为 567 项）；**周期面** `.github/workflows/ci-full-matrix.yml` 每周一 02:17 UTC（另有
`workflow_dispatch` 供取证）用 3.11/3.12/3.13 三档跑全量 + 黄金逐字节复现 + `doctor`，补上这些非红线项的
跨版本空白。这个分工由 `tests/test_ch479_ci_matrix.py` 的四条红线钉住：两工作流的版本集合必须相等、
push 面 `full-suite` 必须保持无矩阵且钉死 `3.12`、每个作业必须带 `timeout-minutes`、周期腿不得挂
`push`/`pull_request` 触发。周期腿首证 = run `39032817663`（手动 `workflow_dispatch`，三档各 `811 passed`，含黄金逐字节复现与 `doctor` 自检），push 面 = run `39032796293`（三档各 `260 passed, 551 deselected` + `811 passed` 全量）；上一轮（CH-481）push 面 = run `39041920410`（三档各 `275 passed, 563 deselected`，全量 `838 passed` 干净退出）；最新一轮（CH-482）push 面 = run `39043948338`（三档各 `285 passed, 567 deselected`，全量 `852 passed` 干净退出，`doctor` 打 `2.5.0`）。
> 现状（CH-468 起）：仓库已公开，`main` 分支保护已配置——`merge-gate (3.11/3.12/3.13)` 为**必需检查**，
> 三个 gate 任一失败即禁止合并（`full-suite` 因 `needs` 同样被阻断）。验收：故意失败的 PR #1 被
> `mergeStateStatus=BLOCKED` 拦下。另有本地 pre-push 钩子双保险（见「本地红线兜底」）。
> 边界（CH-470）：必需状态检查拦的是 **PR 合并**，直接 `git push origin main` 仍会成功（远端返回
> `Bypassed rule violations … 3 of 3 required status checks are expected`）；要让"红即不可入库"对直推
> 也成立，需为 `main` 打开「Require a pull request before merging」，那是改变日常工作流的决策项。

```bash
pytest -m gate            # 红线层：六条 merge-gate + 低配机专项 + CH-469 两批安全/契约专项 + CH-473 第五轮 12 项 + CH-475 第六轮 7 项 + CH-476 第七轮 91 项 + CH-477 版本对账 4 项 + CH-478 批次① 有序列表端到端 4 项 + 批次② 等价门缩进敏感度 4 项 + CH-479 CI 版本矩阵口径 4 项 + CH-480 常驻资产同构 7 项 + CH-481 捕获档截断 8 项 + CH-482 代码块缩进重建 10 项（全绿方可合并）
                          # 当前 285 项 = 275（上一轮口径：267（更早口径：40 契约红线 + 29 低配机 + 10 服务/存储 + 54 安全/配置/运维 + 13（CH-473 12 项 + CH-474 补入 X-17 1 项）+ 7（CH-475 第六轮）+ 91（CH-476：test_ch476_fixes.py 83 + test_sharding_unit.py 8）+ 4（CH-477 版本与编号对账）+ 4（CH-478 批次①：test_ch478_fixes.py 端到端反例）+ 4（CH-478 批次②：test_ch478_gate_drift.py 度量对照）+ 4（CH-479：test_ch479_ci_matrix.py 工作流文本静态断言）+ 7（CH-480：test_ch480_resident_assets.py 常驻 plist/schtasks 资产的静态同构断言））+ 8（CH-481：test_ch481_capture_clip.py 捕获档就地截断与 cron 接线））+ 10（CH-482：test_ch482_indent_rebuild.py 代码块行首缩进按几何重建：PDF 围栏/等宽块 2 + 几何守卫 1 + S1 字宽 1 + DOCX 保空白 1 + 表格不误判 1 + mono 首行缩进 1 + indent 模式不二次缩进 1 + 无字宽回退 1 + 缓冲/两行守卫 1）
                          # 耗时历史记录：Linux 沙箱轮次 72–110s、T8 优化后 53.0s、CH-469 本机（macOS arm64 / Py 3.15）28.7s、CH-473 62.7s、CH-475 30.0s、CH-476 33.9s、CH-477 33.25s / 31.98s、CH-478 批次① 31.54s、批次② 31.16s、CH-479 38.55s / 收口复跑 33.74s、CH-480 36.13s / 收口复跑 35.43s（新七条红线只做 plistlib 解析与 XML 读 DOM，差值属同机负载抖动）、CH-481 36.14s / 34.92s / 35.91s、CH-482 35.71s（`--junitxml` 35.36s）——项数是硬口径，耗时只作历史
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

六条门禁：`shard_two_phase`、`malicious_filenames`、`injection_resistance`、
`edit_optimistic_lock`、`delete_cascade`、`log_rotation`；另含本实现补强的 CJK 检索、渲染分级、
目录递归防护与低配机专项断言（完整清单见方案 附录 F）。

正文里出现的 `CH-xxx` 只是索引，条目原文与实测证据统一收录在
`docs/dev-plan.md` 第十一、十二章。

## 本地红线兜底（T1 的双保险）

平台级必需检查**已生效**（CH-468，T1 关闭）：仓库为 public，`main` 上 `merge-gate (3.11 / 3.12 / 3.13)`
为必需检查，红线失败即阻断合并（用故意的红断言 PR 验收过，`mergeStateStatus=BLOCKED`）。
客户端兜底保留为双保险：`git config core.hooksPath ops/hooks` 后，每次 `git push` 前自动跑
`pytest -m gate`，红则拒绝推送（日志在 `/tmp/doc2md-gate.log`），把红线判定从一次 runner 排队
换成本地几十秒。日常不要用 `--no-verify` 绕过。
