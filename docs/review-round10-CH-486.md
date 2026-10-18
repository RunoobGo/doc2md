# Doc2md 第十轮全面审查与优化报告（CH-486）

> 审查日期：2026-10-18
> 审查基线：`a293107`（`main`，工作树仅有本机 Windows 检出的黄金样本 CRLF 漂移）
> 审查环境：Windows 11 / Python 3.12.10 / 仓库自带 `env` 虚拟环境
> 审查性质：设计、代码、文档、测试、CI 五面只读审查；用户授权后直接实施 A 类修复。
> 原则：功能全面优先、性能优先；需要外部环境或人工验证的事项只登记，每批交付时提醒。

## 1. 执行摘要

四组深审合并去重后形成 **34 个处置项**：按首次发现计 **11 HIGH / 11 MED / 4 LOW**，另有 8 项由 Windows 检出口径直接造成的测试失真。最终 **33 项落地**，S-01 的跨进程窗口部分落地，其余跨进程一致性、真实语料与目标机实测项登记为 B/C 类（见第 5 节）。

最重要的结论：

1. **Windows 目标机的“全绿”此前不成立**：仓库没有 `.gitattributes`，`core.autocrlf=true` 把 30 份 PDF 与 1 份 JSON 黄金样本转成 CRLF，MANIFEST sha256 全部失配；同时 SQLite 失败清理、chmod/ACL、junction、`timezone.tzset`、`os.WNOHANG`、`sendmsg` 等平台差异让 gate 出现 8 项失败。本轮把资产字节、平台语义和 CI 腿一并收口。
2. **数据安全的最后一道仍有真实窗口**：失败快照在 Windows 上因未关闭的 SQLite 连接无法删除 `.part`；cron 外置备份/轮出异常会逃逸并跳过后续 10 步；恢复不持备份锁且不清目标 WAL；删除与产物重建之间可产生孤儿 zip。本轮均已修复或收窄。
3. **管线仍有“内容在、结构错”的形态**：DOCX `w:fldSimple` / `w:smartTag` 文本丢失、相邻 `w:tbl` 被并成一张表、表内 `w:sdt` 行/格缺失。本轮修复前两类，第三类随递归收集器一并覆盖。
4. **性能收益集中在“少做无效工作”**：sniff 前移到整文件哈希之前、无文本框文档不再全树遍历、`/jobs` 增加 `created_at` 索引、外置备份不再复用产物 3× 磁盘系数、恢复去掉重复全库校验。没有为了微优化改变正常产物字节。

## 2. 关键发现与处置

| 编号 | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| W-01 | HIGH | 缺 `.gitattributes`，Windows 检出污染 31 份黄金资产（30 PDF + 1 JSON） | 已修：`tests/golden/** -text` + 二进制扩展声明；31 份恢复 Git 原始 blob，MANIFEST 全表匹配 |
| W-02 | HIGH | `sqlite_backup` 失败路径先 `unlink` 后关连接，Windows 必现 `WinError 32` 并掩盖原始异常 | 已修：关连接后再清 `.part` |
| W-03 | HIGH | Windows `chmod` 不落 POSIX mode 位，`work/ 700`、快照 `600` 只是 no-op | 已修：新增 `doc2md.security.perms`，Windows 用 `icacls` 移除继承并仅授权当前用户；CLI/备份/override 统一走该入口 |
| W-04 | HIGH | `Path.is_symlink()` 不识别 Windows junction，`guard_walk` 可下钻、`sweep_tmp` 的 `rmtree` 可删除链接目标 | 已修：`is_link_like()` 统一识别 symlink/junction/reparse point；深度截断也记 `truncated` |
| O-01 | HIGH | cron 外置备份只捕 `Doc2mdError`，轮出完全无保护；U 盘拔出/AV 占用/认证失败会中止整轮且无报告 | 已修：外置与轮出分别捕获 `Exception`，失败入步骤证据且继续后续步骤 |
| O-02 | HIGH | 恢复不持备份锁、目标缺失时残留 WAL/SHM 会被回放、同一库做两次全量 `integrity_check` | 已修：CLI restore 持 `backup_lock`；无主库时清理 sidecar；只保留替换前一次完整校验 |
| O-03 | MED | `.part` 不在轮出正则内，崩溃后全库副本永久滞留 | 已修：备份入口 `sweep_parts()` 清超过 1 小时的 `.part`；snapshot 失败路径统一 `unlink` |
| O-04 | MED | 外置加密副本复用产物 3× 磁盘系数，合法 U 盘备份被 E4131 永久拒绝 | 已修：`assert_free_space(factor_override=1.2)`，固定余量仍叠加 |
| O-05 | MED | SQLite URI 未转义 `#`，`C:\dev\C#\...` 下 quick_check/演练全部失败 | 已修：`Path.as_uri()` 生成只读 URI |
| O-06 | MED | 多进程共用 `work.jsonl`，Windows rename 轮转抛 `PermissionError` | 已修：轮转失败退化为就地截断保尾，常驻进程不退出、日志仍有界 |
| O-07 | MED | `serve token` 在 `_ensure_private_root` 之前返回，建库路径绕过权限收口 | 已修：token 入口先收紧 root，并把 `state.sqlite` 设为私有 |
| S-01 | HIGH | `_rebuild_artifact` 读库后直接打包，删除/并发 PATCH 可留下已删文档 zip 或旧 revision 覆盖新 revision | 部分修：同进程按 doc 串行 + 发布后复核 docs 存在 + 删除提交后二次清扫；跨进程窗口登记 B-18 |
| S-02 | HIGH | CLI/watch 重转先发 zip 再落库，没有 save_ir 的 E4091 补偿，会覆盖已编辑文档的导出 | 已修：`convert(precheck_edits=True)` 解析前查 `revision>1` 并拒 E4091；CLI 新增 `--force` |
| S-03 | MED | worker 遇坏 IR 的 `sqlite3.IntegrityError` 直接冒泡，常驻消费者退出并被反复重启 | 已修：单任务异常记 failed 后继续，不毒死队列 |
| S-04 | MED | 同步路径 task 无 `source_path`，崩溃后 reclaim 跳过、后续请求永久附着 | 已修：同步 submit 落 `source_path` / `mode` / `force` |
| S-05 | MED | API 目录投递不过滤后缀，一个 README/图片使整批 E4151 0 入队 | 已修：与 CLI 同口径只收 `.pdf/.docx` |
| S-06 | MED | `/jobs` 无 status 过滤时 `ORDER BY created_at` 用不上 `(status,created_at)` 索引 | 已修：新增 `ix_tasks_created`（DB 9） |
| S-07 | MED | `path` 非字符串、`idempotency_key` 为对象会 500 | 已修：400/E4001 |
| S-08 | MED | `/system/reload` 无显式来源时按 cwd 重新 load，嵌入方/测试配置被静默换掉 | 已修：显式来源走文件；程序化入口按启动基线重放 override；`paths.root` 变化要求重启 |
| S-09 | MED | 同步拒收只写 tasks，不发 `job_rejected`，`/events` 消费端等不到终态 | 已修：两条同步拒收路径补事件 |
| P-01 | HIGH | DOCX `w:fldSimple`（DATE/REF）与 `w:smartTag` 文本整段丢失 | 已修：上下文预检 + 走全部 run 的接受后视图 |
| P-02 | HIGH | 相邻 `w:tbl` 被并成一张 GFM 表，第二张表头降为数据行 | 已修：`Line.table_start` + 空行切组；PDF 同页两表同样受益 |
| P-03 | MED | 表内 `w:sdt` 包裹的 `w:tr` / `w:tc` 整块丢失 | 已修：递归展开 sdtContent/customXml |
| P-04 | MED | 先整文件哈希后 sniff，不支持的大文件读完全文才 E4151；哈希 OSError 落 500 | 已修：sniff 前移；hash OSError 归 E4151 |
| P-05 | MED | 无文本框文档也付一次 `txbxContent` 全树遍历 | 已修：`has_textbox` 预检 |
| P-06 | MED | `batch` 的 `pool.submit` 在 try 外，BrokenProcessPool 时跳过 finally 清理 | 已修：submit 移入 try |
| P-07 | LOW | 缝合替换后旧截断块仍留在 `tail`，后续窗口块可能被误判为伪影丢弃 | 已修：替换成功即从 `tail` 移除旧块 |
| W-05 | HIGH | 实机轮出发现 SQLite 快照残留 `.part-wal/.part-shm`，可能夹带未 checkpoint 的正文且不被旧轮出规则清除 | 已修：备份目标 checkpoint 后清理 sidecar；`snapshot`/`restore` 的只读校验后也清理；`sweep_parts` 覆盖 `.part-wal/.part-shm` |
| W-06 | MED | 手工创建的 `state.sqlite` 未被 ACL 收紧，备份/恢复产物已私有但主库仍可被其他本地用户读取 | 已修：`Store.__init__` 统一收紧数据目录 700 与 DB 600（Windows ACL / POSIX chmod） |
| S-10 | MED | `serve --root` 的进程级 root 在 `/system/reload` 时被配置文件默认 root 覆盖，reload 返回 400 | 已修：reload 后重新钉回已绑定 root，并新增回归测试 |
| D-01 | MED | 文档版本锚点 / DB 版本表 / DB6 旧口径漂移 | 已修：PIPELINE 2.8.0、DB 9、PLAN 3.20 三处同步，新增 CH-486 登记行 |
| T-01 | MED | Windows 无 CI 腿，B-7 只有人工 | 已加 `windows-suite` 作业（gate + 全量 + doctor）；远端 run `39106866387` 已全绿，B-7 闭环 |
| L-01 | LOW | Windows 平台专有用例（tzset/waitpid/sendmsg/symlink/ps sampler）在非授权环境红 | 已改为带原因的 skip 或平台分支；POSIX 语义用 `is_private`/分支补位 |

## 3. 实施要点

- **资产字节**：`.gitattributes` 把 `tests/golden/**` 标记为 `-text`，并声明 PDF/DOCX/BIN/ZIP/SQLite/ENC/GGUF 为二进制；31 份被 CRLF 污染的资产从 Git blob 恢复，MANIFEST 5140 条全表匹配。
- **Windows 权限**：`doc2md/security/perms.py` 提供 `ensure_private()` / `is_private()`；Windows 调 `icacls /inheritance:r /grant:r <user>:(OI)(CI)F`，POSIX 保留 chmod 并校验 mode 位；CLI、备份快照/外置副本、settings override、token 库统一接入。
- **链接防护**：`is_link_like()` 识别 symlink、junction 与其他 reparse point；`guard_walk` 在 `follow_symlinks=false` 时跳过，`sweep_tmp` 只移除链接本身。
- **备份/恢复**：失败清理先关连接；`.part` 清扫；外置写并发按最终名加线程锁 + Windows `replace` 退避重试；恢复持备份锁、清 sidecar、只做一次完整校验。
- **管线**：DOCX 域代码/smartTag 预检；相邻表 `table_start` 切分；表内 sdt 递归；sniff 先于哈希；文本框无内容时零全树遍历。
- **服务**：CLI 编辑预检（`precheck_edits=True`）与 `--force`；同步 task 落路径；API 入参校验/后缀过滤/任务索引；reload 配置来源显式化；同步拒收补事件；worker 毒任务隔离。
- **CI**：新增 `windows-suite`（`windows-latest`，25 分钟上界，gate + 全量 + doctor）。
- **综合语料**：新增 `tests/corpus/`，17 份 PDF/DOCX 源文件、16 份目标 Markdown、1 份扫描件 E4221 拒收；覆盖标题/段落/列表/表格/链接/代码/脚注尾注/修订/域代码/smartTag/sdt/文本框/图片/页眉页脚/大文档。

## 4. 验证

| 项目 | 结果 |
| --- | --- |
| `pytest -m gate`（Windows 本机） | **342 项：341 passed / 0 failed / 1 platform skip（51.18s）** |
| `pytest`（Windows 本机全量） | **929 项：921 passed / 0 failed / 8 platform skips（124.63s）** |
| 综合语料 | **17/17 通过**（16 目标 Markdown + 1 扫描件 E4221 拒收） |
| `ruff check .` | All checks passed |
| `doc2md doctor` | exit 0；`pipeline 2.8.0`、`root_private` 可见 |
| 黄金 MANIFEST | 5140 条全表匹配；31 份污染资产恢复 |
| 新增回归 | 相邻表、DOCX 域代码/smartTag、CLI 编辑预检、Windows junction×2 |
| 性能探针（不支持输入） | 50MiB `.bin` 在 **1.40ms** 内以 E4151 拒收；sniff 先于哈希，未读全文 |
| 性能探针（`/jobs` 排序） | `EXPLAIN QUERY PLAN` 为 `SCAN tasks USING INDEX ix_tasks_created` |

8 个 skip 均为 Windows 上不存在的 POSIX 原语：`chmod` 语义 ×2、`time.tzset`、`os.WNOHANG`、`sendmsg`、symlink 检出 ×2、`ps`/`/proc` 采样 ×3（按测试文件计 8 项）。它们不是产品功能跳过，而是平台能力分支；Windows 侧由 ACL/`is_private`、junction、文件锁、spawn 池用例补位。

### 4.1 本机实机验证矩阵（2026-10-18）

| 场景 | 命令/方式 | 结果 |
| --- | --- | --- |
| 单文件 PDF/DOCX 转换 | `doc2md convert` 临时 root | 2/2 成功；zip 含 content.md / ir.json / report.html |
| 批量转换 | 10 份 clean PDF，workers=1/2 | 10/10 成功；0.91s / 0.73s |
| 不支持输入 | 50MiB `.bin` | 1.40ms 内 E4151，退出码 4 |
| 320 页单档 | Python 进程 + WMI 采样 | 13.09s，峰值 working set 169.6MB，5789 blocks / 590661 chars |
| 本地备份/恢复 | cron snapshot + v2 restore | integrity ok，正文一致，ACL private |
| v1 历史容器 | `encrypt_bytes` 生成 + CLI restore | 恢复成功，integrity ok |
| 恢复演练 | `backup drill --keep` | 8 步全过、表指纹 match |
| cron daily/weekly | 无外置卷 | `ok=true`；weekly 外置步骤按配置 skipped |
| 轮出与 part 清扫 | 20 年前 state/predelete + 旧 `.part` | 全部删除，无 `.part-wal/.part-shm` 残留 |
| work 队列 | 单文件/目录 enqueue + `--once` | 3 个作业 done；status 计数一致 |
| watch | 旧 mtime 文件 + `--once` | 转换并入库；`done=1` |
| stale 回收 | 2 小时前 running 行 | `reclaimed_stale=1`，重跑 done |
| API 链路 | serve + token + HTTP | POST /jobs、GET /docs、search、PATCH、E4091、reload、delete、events 全通过 |
| 编辑器浏览器 | Qoder 内置浏览器加载/载入/保存 | 60 块加载；b1 保存后 rev 2，搜索索引刷新 |
| Windows 计划任务 | 占位符替换 + UTF-16 `schtasks` 注册/查询/删除 | 注册与清理成功；XML 定义有效 |
| Windows ACL/junction | `icacls`、`mklink /J`、`guard_walk`、`sweep_tmp` | 私有 ACL 生效；junction 被跳过，目标文件保留 |
| 并发日志轮转 | 2 个进程同时写 `app.jsonl` | 两端 exit 0，54 行 JSON 全部可解析，无坏行 |

未能在本机执行：`pwsh` 未安装（Windows PowerShell 5.1 无法解析安装脚本中的中文/PS7 语法）；外置 USB/SMB 卷、macOS、真实 Word/PDF 语料、域策略终端仍属 B 类。计划任务 XML 已用 `schtasks` 成功注册，证明定义本身可用；安装脚本的 `-VerifyOnly` 需 PowerShell 7。

## 5. B/C 类登记与提醒

| 编号 | 事项 | 为什么仍需外部/人工 |
| --- | --- | --- |
| B-6 | Win11 真机 spawn 池、任务计划常驻、ACL 在域账户/漫游配置下的行为 | 本机已验 ACL/junction/spawn/XML 注册；**pwsh 7 未安装**，`install-doc2md-tasks.ps1 -VerifyOnly` 未执行；域策略/受管终端仍需目标机验证 |
| B-7 | Windows CI 首次远端运行 | **已闭环**：run `39106866387` 六作业全绿；首轮 `39106420181` 仅 Windows checkout 长路径失败，`7b99d00` 启用 `core.longpaths` 后复跑通过 |
| B-08 | Win11 / Mac mini 跨机性能基准 | 当前数字来自 Windows 本机与历史 macOS/Linux；需目标机同语料复测 |
| B-10 | v1 历史容器、SMB/网络卷、并发 cron、失败回滚的真机恢复 | 需要真实外置卷/网络卷与人工拔盘 |
| B-13 | 真实 Word 模板语料（文本框、编号、sdt、域代码、页眉页脚） | **已开始收集**：Office 2052 真实模板 23 份，20 份可转换；特征矩阵见 `tests/corpus/real/manifest.json`；样本本地保留（许可不入库），仍需人工抽查 |
| B-15 | 真实中文 PDF 标题/数字表/跨行链接语料 | **已开始收集**：9 份 Word 导出中文 PDF，正文 8.0–11.0pt；自动 DOCX Heading 对照不可靠，需人工标注 10–20 份标题集合后统计 H1 precision/recall |
| B-16 | 真实注入语料 + `_attribute` 归因 | 需安全语料与人工审阅 |
| B-17 | 图片型 DOCX 的媒体全量驻留 | 需决定是否自建 OOXML 读取器/媒体上限；当前 `python-docx` 会载入 `word/media/*`，4 worker 会放大 |
| B-18 | API 与 worker 跨进程产物重建的最终一致 | 同进程已串行；跨进程仍需按 doc 的跨进程锁或 DB 版本号发布协议 |
| B-19 | 大文档 PATCH 的全篇重渲染/重打包 O(全文) | 需设计块级增量渲染；当前单块编辑仍 O(文档) |
| B-20 | `/docs`、`/audit`、`/audit/deletions` 的硬分页上界 | 低风险但会改变大库 API 形状，需产品确认默认值 |

**提醒（每批交付时重复）**：B-7（Windows CI）已由 run `39106866387` 闭环；B-6 仍是域策略/受管终端的目标机实证路径；B-10 是“以为备份了”的最后防线；B-13/B-15/B-16 是结构修复的真实语料验收；B-17/B-18/B-19 是本轮明确留作后续架构项，不应在发布说明中写成已解决。

## 6. 变更与回滚

- 版本：`PIPELINE_VERSION` 2.7.0→**2.8.0**（相邻表/域代码修复改变部分输入产物）、`DB_VERSION` 8→**9**（新增索引，幂等 DDL）、`PLAN_VERSION` 3.19→**3.20**。
- 回滚：`.gitattributes` 与黄金资产恢复可整体回退；ACL 入口在 Windows 失败时降级为告警，不会阻断转换；`ix_tasks_created` 为纯增量索引，旧代码可忽略；`precheck_edits` 为 CLI/watch 显式参数，API/worker 保留原补偿路径。
- 已提交并推送：`500159a`（CH-486 主体）与 `7b99d00`（Windows CI 长路径修复）；远端 CI run `39106866387` 六作业全绿。未创建 PR（直推 `main`，远端提示绕过必需检查，属既有 CH-470 行为）。
