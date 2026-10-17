# Doc2md 第六轮全面审查报告与优化方案（CH-475）

- 轮次：第 6 轮（前五轮 CH-459 / CH-464 / CH-469 / CH-471 / CH-473·474，见 `dev-plan.md` §9.4、§11）
- 日期：2026-10-17
- 范围：设计、代码、文档全面审查；原则「功能全面、性能优先」
- 口径：**只读审查，本轮不实施任何改动**；交付为本报告 + 优化方案
- 审查面：管线核心（S1/S1.5/S3/S4/metrics/convert/llm）、服务层（api/scheduler/worker/auth/storage）、运维与 CLI（cli/cron/ops/logging + `ops/` 资产）、文档一致性
- 分诊法（沿用 §9.4）：**A = 沙箱内可修**（给出精确改法，待批准后实施）；**B = 需本机以外环境/人工**（仅登记）；**C = 需人决策**（已按原则代为制定最佳方案，见第 5 章）
- 置信度：high（本人逐行复读确认）/ medium（子代理通读全文报告、本人未逐行复读）/ low，逐条标注

> 命名说明：本报告发现一律前缀 `R6-`，避免与前五轮已闭环编号混淆。前五轮 HIGH 项（PIPE-1 base_size、STOR-5 重复转换清空编辑、STOR-3 FTS 非原子重建、TEST-1 golden 静默 skip）经复读**确认均已修复**，本轮不再列出；唯 TEST-3（CI 无 Windows 腿）仍开放，转入第 6 章 B 类登记。

## 0. 门禁基线复现（本机）

在本机复跑合并门禁与 doctor，结果与 `README.md` 宣称一致：

```
pytest -m gate ：146 项全绿（72+72+2 个通过点，无 F/E；较第四轮 132 项净增 14）
doctor        ：dep_pdfplumber/docx/flask/cryptography 全 ok
                root_exists=false, llm_mode=offline, cpu_count=10
                min_free_mb=512, free_mb=511637
```

结论：146 项门禁在本机全绿。**本报告所有发现都是在这套「已全绿」门禁之外新挖出的盲区**——即现有红线未覆盖的路径。

## 1. 执行摘要

项目成熟度极高：分层 DAG 架构清晰、离线契约（netguard）真实加固、并发模型（WAL + 线程局部连接 + BEGIN IMMEDIATE）正确、加密（AES-256-GCM 分块容器 v2）稳健、等价性纪律（G6 逐 zip 条目 sha256）到位、前五轮已把绝大多数显性问题修净。

本轮新发现 **14 项确定缺陷 + 1 组文档漂移**，其中 **HIGH 3 项**，集中在四类盲区：

1. **正确性盲区（R6-PIPE-1，HIGH）**：S3 结构判定的「第三标题回退」无字号/加粗门，短编号列表项（如「1. 安装依赖」）被误判为 H1 标题，破坏正文结构。现有门禁用的是标题/列表区分明确的语料，漏检。
2. **健壮性盲区（R6-LLM-1，HIGH）**：LLM 修复算子的**参数值**未校验（只校验算子白名单/目标存在性），`level:"3"`（字符串）或 `marker:"\1"` 会让 `apply_ops` 在渲染期抛 TypeError / re.error——而文档正文是**不可信输入**，模型可被诱导产出此类算子。
3. **运维盲区（R6-OPS-1，HIGH）**：macOS launchd 定时任务 plist **缺 `WorkingDirectory` 且未传 `--root`**，而 `root_setting` 把相对根名 `"work"` 解析到**当前工作目录**；launchd 默认 cwd=`/` → 解析成 `/work` → 每日 cron 要么失败要么操作错误根。Windows 孪生文件已带 `WorkingDirectory`，属 macOS 腿漏配。

其余为 MED 级的安全加固（reload 绕过不可变守卫、明文 token 前缀入审计、scrub_record 漏 bytes）、数据安全（幂等重执行 + mark 无状态守卫、claim/reclaim 的 source_path 不对称）、等价性度量盲区（norm_lines 抹掉全部空白）与 doctor 退出码缺口。

**性能优先视角**：本轮发现以正确性/安全/健壮性为主，修复均为**性能中性或微正**（R6-PIPE-1 加一道字号比较、R6-LLM-1 加一次类型校验、R6-SVC-4 给 claim 加一个 WHERE 条件），不引入新开销，且不破坏 G6 等价（修复只改「误判/崩溃/越权」路径，正常语料产物字节不变）。

> 本章不代替第 3 章逐条证据；实施顺序见第 4 章；C 类决策见第 5 章。

## 2. 审查方法与证据口径

- 证据优先：每条发现给 `文件:行` + 问题 + 影响 + 建议 + A/B/C + 置信度 + 是否需外部验证。
- 只读：未修改任何源码或权威文档（`dev-plan.md`）。文档漂移项一律登记为「待批准的文档更新建议」，本轮不落笔。
- 信任但验证：3 个子代理分区通读全文（管线 / 服务层 / 运维·CLI·文档）；本人对其中 **11 项 HIGH/MED-HIGH 逐行复读确认**（标 high），其余子代理报告项标 medium，文档漂移组标 medium/low。
- 决策代理：C 类（需人决策）按「功能全面、性能优先」+ 项目核心价值（离线、4 依赖闭环、数据安全、G6 等价）代为制定最佳方案，见第 5 章。
- 外部/人工项：仅登记（第 6 章），并在文末提醒（第 8 章）。

## 3. 发现清单（逐条证据）

### 3.1 管线核心（R6-PIPE）

**R6-PIPE-1 · 正确性/结构误判 · HIGH · 置信度 high · A 类**
- 证据：`doc2md/pipeline/s3_structure.py:24` `_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[、\.::]?\s*")` 会匹配「1.」「2、」等编号；`:26` `_BULLET` 字符集 `[·•‣∙○●■□\-\*]` **不含数字**，故编号项不会走列表分支；标题分支（约 `:248`）在列表分支（约 `:259`）**之前**判定；第三回退 `:372` `if numbered and len(t) < 30 and not t.endswith(("。","！","？",".","!","?")): return _level_from_number(t, size or base, base)` —— **无字号门、无加粗门**。
- 问题：任何「短编号 + 无句末标点」的正文行（极常见的有序列表项，如「1. 安装依赖」「2. 运行测试」）被 `_level_from_number` 判成标题（编号「1」→ H1）。
- 影响：有序列表整段被提升为标题层级，`content.md` 结构错乱、TOC 污染、`ir.json` 块类型误标。现有门禁语料的标题/列表区分明显（标题靠字号/加粗命中前两条回退），未覆盖「字号与正文相同的纯编号列表」这一路径，属静默正确性风险。
- 建议（A）：第三回退加门——仅当 `size > base`（或带加粗标志）时才允许 `_level_from_number`；否则归为 `list_item`。即把 `:372` 改为 `if numbered and (size or base) > base and len(t) < 30 and not t.endswith(...)`，与「标题靠字号/加粗而非纯编号」的既有口径一致。修复对正常标题（字号>正文）字节不变，仅纠正误判路径，G6 等价保持。
- 需外部验证：否（逻辑可由代码推定）；但**构造反例**需一份「编号列表字号==正文」的 golden（登记 B，见 6.1）。

**R6-PIPE-2 · 等价性度量盲区 · MED · 置信度 high · A 类**
- 证据：`doc2md/pipeline/metrics.py:22` `_WS = re.compile(r"\s+")`；`:32-33` `norm_lines` 对每行做 `_WS.sub("", ln)`，即**抹掉全部空白**（含缩进、代码围栏内的空格、列表标记后的空格）后再比 `char_similarity`（门 ≥0.99）。
- 问题：等价性门对「缩进变化、代码块围栏破损、列表标记 `-`↔`*` 替换、行内空格增删」**完全失明**——这些改动把空白抹掉后字符序列相同，门照样判绿。
- **校正（CH-478 批次② 实测，`tests/test_ch478_gate_drift.py`）**：上面这句只有前半成立。在真实产物（黄金样本 `codeindent_1p.pdf`）上，收紧后 / 收紧前的相似度分别是**行首缩进 0.973958 / 精确 1.000000**（旧口径确实结构失明）、**行内空格 0.997333 / 1.000000**（脱盲但未过阈），而**围栏破损 0.991914 / 0.991667**、**列表标记 `-`↔`*` 0.994652 / 0.994490**——后两类是内容级漂移，删不删空白都照样吃掉字符数，**并非旧口径失明**；本项收紧带来的敏感度只覆盖空白类漂移。另得一条度量边界：**0.99 是粗大漂移探测器，不是等值证明**（判红取决于漂移量相对语料分母），逐字节等值仍由产物 sha 对照负责。
- 影响：G6「分片≡单片」「修复前后等价」的度量被削弱；一类真实回归（代码块缩进丢失、列表标记漂移）可绕过门禁。与用户「绝不用宽松口径掩盖回归」的原则相悖。
- 建议（A）：`char_similarity` 保留「抹空白」口径用于正文散文比较，但**新增一条结构化比较**：对 `code` / `list_item` 块保留原始空白做逐行精确比对（或比较 `norm_lines` 时仅折叠连续空白为单空格、而非删尽）。两者取严。修复只收紧门，不改产物，性能中性。
- 需外部验证：否。

### 3.2 服务层（R6-SVC）

**R6-SVC-1 · 安全/不可变守卫绕过 · MED · 置信度 high · A 类**
- 证据：`doc2md/api.py` 中 `check_runtime_immutable` **仅**在 `PUT /settings`（约 `:563`）调用；`reload_`（约 `:571-591`）重载配置时**不调用**该守卫。
- 问题：`run.offline_assert` 等运行期不可变键，虽不能经 `PUT /settings` 改，但可经「直接改配置文件 → 调 reload 端点」绕过守卫生效，等于离线断言可被运行期关闭。
- 影响：离线契约（netguard 的核心开关）存在运行期旁路；对「离线优先」项目是安全语义漏洞（虽仍需本地文件写权限，但守卫的存在意义就是防运行期篡改）。
- 建议（A）：`reload_` 在应用新配置前同样调用 `check_runtime_immutable(old, new)`，不可变键变化则拒绝 reload（返回与 `PUT /settings` 一致的错误码）。修复只增校验，正常 reload 不受影响。
- 需外部验证：否。

**R6-SVC-2 · 安全/凭据片段入审计 · MED · 置信度 high · A 类**
- 证据：`doc2md/api.py:106-108` `tok()` 返回**明文** bearer token；patch 路径 actor 取 `b.get("as", tok() or "anon")[:12]`（约 `:400`），rollback 路径同（约 `:418-419`）——把明文 token 的前 12 字符写进 `edit_audit`。
- 问题：审计记录里落了凭据前缀。token 在库内本应**只存哈希**（项目既定口径），此处却把明文前 12 字符持久化到审计表。
- 影响：`edit_audit` 泄露凭据片段，缩短暴力/关联搜索空间；违反「token hashed at rest」口径与 NFR-20 精神（敏感值不落库）。
- 建议（A）：actor 缺省时改用**已认证的主体标识**（如 token 的 sha256 前 12 位，或角色名 + 会话 id），绝不取明文 token 前缀。`tok()` 仅用于发响应头，不得入审计。
- 需外部验证：否。

**R6-SVC-3 · 数据安全/幂等重执行 + 状态守卫缺失 · MED · 置信度 high · A 类（含 C 类决策 D1）**
- 证据：`doc2md/api.py` `_finish_one`（约 `:244-268`）**忽略** `j.attached` / `j.idem_hit` 标志，对幂等命中的作业仍重跑 `save_ir`；E4091 分支（约 `:256-261`）调 `mark(rejected)`。`doc2md/scheduler.py:81` `mark()` 的 UPDATE（`:94` 带 detail / `:99` 不带）**均无状态守卫**：`UPDATE tasks SET status=? ... WHERE job_id=?`，没有 `AND status IN (...)`。
- 问题：① 幂等命中（本应直接复用既有产物）却重跑落库，浪费且可能覆盖；② `mark()` 无守卫 → 一个已 `done` 的作业可被后续 `mark(rejected)` 翻转，终态不幂等。
- 影响：对「数据安全至上」是隐患——终态可被回退、幂等语义被破坏；并发下尤其危险（与 R6-SVC-4 叠加）。
- 建议（A）：① `_finish_one` 在 `j.idem_hit`/`j.attached` 为真时跳过 `save_ir`，直接复用；② `mark()` 加状态守卫 `AND status IN ('queued','running')`（仅允许从活动态迁移），终态（done/failed/rejected）不可再被 mark 改写，需要重开走显式 requeue。**决策见 5.D1**。
- 需外部验证：否。

**R6-SVC-4 · 并发/认领口径不对称 · MED · 置信度 high · A 类**
- 证据：`doc2md/worker.py` `claim()`（约 `:57-59`）`SELECT ... WHERE status='queued'` **不带 source_path 过滤**；而 `reclaim_stale`（约 `:94`）**带** `AND source_path IS NOT NULL`；`drain_once`（约 `:127-133`）对无 path 的作业直接 `mark(failed, E4001)`。同步 `serve` 路径在 submit 事务与 `mark(done)` 事务之间会提交一条 `source_path=NULL` 的 queued 行。
- 问题：常驻 worker 的 `claim()` 可认领这条「同步路径中间态、无 source_path」的 queued 行，随即在 `drain_once` 判 E4001 失败——而同步路径自己正要把它 mark(done)。两条路径对同一作业竞争，且 claim/reclaim 的过滤口径不一致（reclaim 知道要排除 NULL path，claim 不知道）。
- 影响：并发下同步转换的作业可能被 worker 误判失败（E4001），或状态被互相覆盖（叠加 R6-SVC-3 的无守卫 mark）。属数据正确性/并发安全缺陷。
- 建议（A）：`claim()` 的 SELECT 加 `AND source_path IS NOT NULL`，与 `reclaim_stale` 口径对齐——无消费路径的行本就不该被进程池认领（同步路径自己负责其终态）。修复只收紧认领范围，正常异步作业（带 path）不受影响。
- 需外部验证：否（并发竞争可由代码推定）；构造稳定复现需并发压测（可选，登记 B）。

### 3.3 LLM 修复（R6-LLM）

**R6-LLM-1 · 健壮性/算子参数值未校验 → 崩溃 · HIGH · 置信度 high · A 类（含 C 类决策 D2）**
- 证据：`doc2md/llm.py` `OpValidator._check`（约 `:87-104`）只校验**算子白名单 / 目标块存在 / 不可变块 / 占用冲突**，**不校验参数值**；`apply_ops`（约 `:387`）`b.level = op.get("level", b.level)` 未做类型/范围检查；（约 `:391-392`）`marker = str(op.get("marker","-"))[:3]` 直接当 `re.sub` 的**替换串**用。
- 问题：① `level:"3"`（字符串而非 int）会在 `s4_render.py:44` 触发 TypeError；`level:99` 越界不报错但产出非法标题层级；② `marker:"\1"` 作为 re.sub 替换串会触发 `re.error`（非法组引用）；`marker` 含 `\` 系列同理。文档正文是**不可信输入**，模型（或被注入的正文）可诱导产出此类算子。
- 影响：单个恶意/畸形算子可让整篇转换在渲染期崩溃（DoS 自身管线），违反「正文视为不可信数据」的既定安全口径。
- 建议（A）：在 `OpValidator._check` 增加**参数值校验**：`fix_heading_level.level` 必须 `int` 且 `1≤level≤6`，否则拒绝该算子；`fix_list_marker.marker` 必须匹配白名单（`-`/`*`/`+`/数字`. `）且对 `re.sub` 替换串做转义（`re.escape` 或改用 lambda 替换），杜绝 `\1` 注入。**拒绝而非崩溃**，与离线确定性口径一致。**决策见 5.D2**。
- 需外部验证：否。

**R6-LLM-2 · 安全/注入归因循环 + 误报漏报 · MED · 置信度 high · A 类**
- 证据：`doc2md/llm.py:43-54` `_INJECT_HINT` 含易误报 token（「扮演」「输出…debug」「返回…密钥」），却**漏掉**真实注入常用词（forget/disregard/ignore previous 等）；`_attribute`（约 `:121-125`）扫描的是**算子 JSON**而非**源文档正文**，与 `convert.py` 的 `_injected_ops` 形成循环归因（互相以对方结果为输入）。
- 问题：注入检测既误报（正常技术文档含「扮演/输出 debug」被标 injection_candidate）又漏报（真注入用英文/变体绕过）；归因链路循环，无法独立判定「是模型幻觉还是正文注入」。
- 影响：`injection_candidate` vs `model_hallucination` 的归因不可靠，安全审计信号失真；对「正文不可信」防线是弱化。
- 建议（A）：① `_INJECT_HINT` 改为**面向源文档正文**的扫描（在 S1.5/S3 阶段对原文匹配），而非对算子 JSON 事后扫描，打破循环；② 词表补充英文/变体注入模式，并把「扮演/输出 debug」这类高误报 token 降级为「需结合上下文（祈使句 + 指向系统/模型）才计入」；③ 归因以「正文是否含注入模式」为**唯一独立判据**，算子异常仅作佐证。
- 需外部验证：否；词表 effectiveness 需真实注入语料评估（登记 B，见 6.1）。

### 3.4 运维与 CLI（R6-OPS）

**R6-OPS-1 · 运维/launchd 缺 WorkingDirectory → cron 操作错误根 · HIGH · 置信度 high · A 类（运行时确认转 B）**
- 证据：`ops/launchd/md.doc2md.cron.plist` 含 Label / ProgramArguments（`.../doc2md cron --daily`）/ StartCalendarInterval / EnvironmentVariables(TZ=UTC) / StandardOutPath / ErrorPath，但**无 `WorkingDirectory`，ProgramArguments 也无 `--root`/`--config`**。Windows 孪生 `ops/schtasks/md.doc2md.cron.daily.xml` **已带** `<WorkingDirectory>`。`doc2md/config/loader.py:415-417` `root_setting` = `Path(str(get(cfg,"paths.root",DEFAULT_ROOT_NAME))).expanduser()`，相对根名 `"work"`（`DEFAULT_ROOT_NAME`，`:412`）**解析到当前工作目录**。
- 问题：launchd 启动作业默认 cwd=`/`，`cron --daily` 未传 `--root` → `paths.root="work"` 解析成 `/work` → 每日 cron 要么因 `/work` 不存在/不可写而失败，要么在错误根上创建 state.sqlite/备份，与用户实际 `--root` 数据**分家**。
- 影响：macOS 上「装好 launchd 定时任务」这一交付路径**实际不可用或操作错库**——对数据安全（备份写错位置）与功能完整（cron 静默失败）都是高危。Windows 腿已对，macOS 腿漏配。
- 建议（A）：plist 补 `<key>WorkingDirectory</key><string>{安装根}</string>`，并在 ProgramArguments 显式追加 `--root {安装根}/work`（与 Windows 孪生口径对齐，安装脚本按实际路径渲染）。**修复后需在 macOS 真机 `launchctl load` 跑一次确认 cwd/root 生效**（转 B，见 6.2）。
- 需外部验证：**是**（launchd 运行时行为需 macOS 真机确认；逻辑推断为 high，运行时为 B）。

**R6-OPS-2 · 安全/scrub_record 漏 bytes → NFR-20 泄露 · MED · 置信度 high · A 类**
- 证据：`doc2md/ops/logging.py` `scrub_record`（约 `:127-133`）只处理 dict / list / tuple / str，**无 bytes 分支**；`write`（约 `:75`）`json.dumps(_scrub(record), ..., default=str)` —— `default=str` 在 `_scrub` **之后**才把 bytes 字符串化。
- 问题：若事件 payload 含 bytes（如解析中间产物、文件头片段），`_scrub` 因其非 str 而**原样放过**，随后 `default=str` 把整段 bytes 逐字字符串化写进 JSONL 日志——绕过「>64 字符换指纹」的 NFR-20 脱敏。
- 影响：正文/二进制片段可经 bytes 通道泄露进日志，违反 NFR-20（日志不得含正文）。与库内 events.payload_json 的脱敏口径不一致。
- 建议（A）：`scrub_record` 增加 bytes 分支：`if isinstance(rec,(bytes,bytearray)): rec = rec.decode("utf-8","replace")` 后按 str 长度规则处理（>64 → 指纹）。与 api 侧「只看长度不看键名」口径统一。
- 需外部验证：否。

**R6-OPS-3 · CLI/doctor 退出码缺口 · MED · 置信度 high · A 类**
- 证据：`doc2md/cli.py` doctor（约 `:566-569`）把 llm_server 记为 `"unreachable"`，但失败判定（约 `:577`）是 `str(v).startswith("MISSING")` → unreachable **不触发**非零退出，doctor 仍 exit 0；`free_mb`（约 `:550`，OSError 时 `-1`）**从不与 `min_free_mb` 比较**；`frozen_ok` 失败 → `EXIT_USAGE`（约 `:575-576`）。另：`_jline`（`:49-55`）正确用 `ensure_ascii=True`（CH-451），但仍有若干 JSON 输出路径**绕过 `_jline`** 直接 dumps。
- 问题：doctor 作为「装好即可用」的自检口，对「LLM server 不可达」「磁盘不足（free_mb<min_free_mb 甚至 -1）」**不报失败**，用户拿到 exit 0 误以为环境健康；绕过 `_jline` 的输出在 GBK 控制台可能产非法 JSON。
- 影响：自检失真 → 用户在低配/异常机上误判「环境 OK」，后续转换才暴雷；与「低配机友好、早失败」口径相悖。
- 建议（A）：① 失败判定纳入 `unreachable`（server 模式下）与 `free_mb < min_free_mb`（含 `-1`）；② 所有机器可读 JSON 输出统一走 `_jline`，消除旁路。注意：offline 模式下 llm_server 不可达**不应**算失败（本就离线），需按 `llm_mode` 分支。
- 需外部验证：否。

### 3.5 文档一致性（R6-DOC，组）

**R6-DOC-1 · 文档漂移 · MED/LOW · 置信度 medium · A 类（含 C 类决策 D5）**
- 证据：子代理通读 `README.md` / `docs/dev-plan.md` 主题篇 / `ops/` 资产注释，报告约 12 处口径漂移（P2-1…P2-12），典型如：配置键计数与 SPEC 的同步、`ops/` 脚本注释与实际参数不一致、主题篇章节未随 CH-472/473/474 递增、部分 CLI 示例与 `_jline`/doctor 现状不符。**本人未逐条复读**，故整组标 medium。
- 问题：文档与代码存在多处小幅漂移，单条影响低，但累积降低「文档即权威」的可信度。
- 影响：新用户/未来维护者按文档操作可能踩坑；与项目「文档口径对齐」纪律（CH-473 批次④已做过一轮）有回潮。
- 建议（A）：实施阶段做一次**集中文档对齐 pass**（而非零敲碎打），逐条核对 P2-1…P2-12，统一在 CH-475 落笔；每条改前本人复读确认（把 medium 升 high）。**决策见 5.D5**。
- 需外部验证：否。

## 4. 实施顺序建议（待批准后执行，本轮不实施）

按「数据安全 > 正确性 > 安全加固 > 健壮性 > 运维 > 文档」与风险/收益排序：

| 序 | 项 | 级别 | 类 | 理由 |
|----|----|----|----|----|
| 1 | R6-SVC-3（幂等重执行 + mark 状态守卫） | MED | A | 数据安全至上，终态可回退是硬伤 |
| 2 | R6-SVC-4（claim 加 source_path 过滤） | MED | A | 与 1 叠加的并发误判，一行 WHERE 即收 |
| 3 | R6-SVC-2（明文 token 前缀入审计） | MED | A | 凭据落库，安全口径硬违 |
| 4 | R6-OPS-2（scrub_record bytes 分支） | MED | A | NFR-20 泄露，正文可能进日志 |
| 5 | R6-SVC-1（reload 不可变守卫） | MED | A | 离线断言运行期旁路 |
| 6 | R6-LLM-1（算子参数值校验） | HIGH | A | 不可信输入致管线崩溃，拒绝>崩溃 |
| 7 | R6-PIPE-1（第三标题回退加字号门） | HIGH | A | 结构误判，影响所有编号列表文档 |
| 8 | R6-LLM-2（注入归因去循环 + 词表） | MED | A | 安全信号失真 |
| 9 | R6-PIPE-2（等价性度量收紧空白） | MED | A | 门禁失明，收紧不改产物 |
| 10 | R6-OPS-3（doctor 退出码 + _jline 统一） | MED | A | 自检失真，早失败 |
| 11 | R6-OPS-1（launchd WorkingDirectory + --root） | HIGH | A→B | 改可沙箱做，运行时确认需 macOS 真机 |
| 12 | R6-DOC-1（集中文档对齐 pass） | MED | A | 收尾，逐条复读后落笔 |

每项实施后：复跑 `pytest -m gate`（须仍 146 全绿）+ 对受影响产物做 G6 等价复核（正常语料字节不变），并按 §11 登记 CH-475 子项。

## 5. C 类决策（按「功能全面、性能优先」代为制定）

**D1（对应 R6-SVC-3）· mark() 状态守卫语义**
- 决策：`mark()` 加 `AND status IN ('queued','running')` 守卫，终态（done/failed/rejected）**不可被 mark 改写**；需重开走显式 `requeue`（置回 queued 并清 started_at）。`_finish_one` 在 `idem_hit/attached` 为真时**跳过 save_ir**，直接复用既有产物。
- 理由：数据安全优先——终态幂等是「重复转换不丢编辑」（STOR-5 已修）的延伸；跳过幂等重执行既省 CPU（性能优先）又杜绝覆盖。

**D2（对应 R6-LLM-1）· 畸形算子处理策略**
- 决策：**拒绝而非崩溃**——参数值非法的算子在 `OpValidator` 阶段整条丢弃（计入 report 的 `rejected_ops`），不进入 `apply_ops`；`level` 强制 `int` 且 `1..6`，`marker` 强制白名单 + `re.escape`。
- 理由：离线确定性优先——单算子畸形不得让整篇转换失败（功能全面）；拒绝并记录比静默修正更可审计，且零额外性能开销。

**D3（对应 R6-OPS-1）· launchd 根路径口径**
- 决策：plist 补 `WorkingDirectory` = 安装根，ProgramArguments 显式追加 `--root {安装根}/work`，与 Windows 孪生完全对齐；安装脚本按实际安装路径渲染这两个值（不写死 `/Users/you/...` 占位）。
- 理由：功能完整优先——macOS 是与 Windows 并列的交付腿，定时任务必须开箱即用；显式 `--root` 比依赖 cwd 更稳（性能/正确性中性）。

**D4（对应 R6-PIPE-1）· 标题判定口径**
- 决策：纯编号**不**构成标题充分条件；第三回退仅在「字号 > 正文众数 或 带加粗」时生效，否则归 `list_item`。
- 理由：与既有「标题靠字号/加粗」口径统一（功能全面）；对正常标题字节不变，仅纠正误判（G6 等价保持，性能中性）。

**D5（对应 R6-DOC-1）· 文档漂移处理**
- 决策：做**一次集中对齐 pass**（CH-475 收尾批次），逐条本人复读确认后再落笔，不做零散修补。
- 理由：文档即权威，集中处理避免反复 diff 噪声；与 CH-473 批次④「文档口径对齐」同方法论。

## 6. B 类与人工/外部环境项（仅登记，本轮不做）

### 6.1 需构造语料 / 真实环境样本
- **B-1（验 R6-PIPE-1）**：构造「编号列表字号 == 正文众数 + 无加粗」的 PDF/DOCX golden，复现短编号项被误判 H1，并作为修复后的回归红线。
- **B-2（验 R6-PIPE-2）**：构造「代码块缩进 / 列表标记漂移」样本，验证收紧后的等价性门能捕获（现门失明）。
  - **闭环（CH-478 批次②）**：样本已造（`tests/golden/codeindent_1p.pdf`）、端到端对照已实测并钉成红线 4 项；结论见上一条的「校正」段——缩进漂移判红得到证实，围栏/标记两类原列为「门失明」不成立。
- **B-3（验 R6-LLM-2）**：准备真实注入语料（含英文 forget/disregard 变体 + 中文「扮演/输出 debug」正常技术文档），评估词表误报/漏报率。
- **B-4（验 R6-SVC-4，可选）**：并发压测（同步 serve + 常驻 worker 同时跑），稳定复现 claim 误认领 source_path=NULL 行。

### 6.2 需本机以外系统环境 / 人工
- **B-5（验 R6-OPS-1）**：macOS 真机 `launchctl load` 跑一次 `cron --daily`，确认 `WorkingDirectory`/`--root` 生效、state.sqlite 与备份落在正确根（**人工/真机**）。
- **B-6（沿 TEST-3，仍开放）**：CI 增 Windows 腿（头号目标机是 8GB Win11），现 `.github/workflows/ci.yml` 三条 job 全 `ubuntu-latest`（**需 CI 环境/人工**）。
- **B-7（沿 dev-plan §12.7 T3）**：跨机性能基准（低配 Win11 vs 本机），现仅本机数字（**需目标机**）。
- **B-8（沿 dev-plan §12.7 T12）**：serve/work 常驻资产（resident set）实测，现仅逻辑推断（**需目标机/长时运行**）。
- **B-9（沿 dev-plan §12.7 T13）**：备份容器 v2 真机恢复演练（AES-256-GCM 分块 + v1 兼容），现仅单元级（**需真机/人工**）。
- **B-10**：编辑器/前端真实浏览器人工走查（bus 事件、patch/rollback 审计展示），现仅单测（**需人工/浏览器**）。

## 7. 性能优先净收益小结

本轮修复**均为性能中性或微正**，无新增开销：
- R6-SVC-3 跳过幂等重执行 → **省** 一次 save_ir（CPU/IO 微正）。
- R6-SVC-4 claim 加 WHERE 条件 → 索引内过滤，**性能中性**，减少误认领返工。
- R6-PIPE-1 / R6-LLM-1 / R6-OPS-2 / R6-OPS-3 → 各加一次轻量比较/校验/分支，**性能中性**。
- R6-PIPE-2 收紧门 → 比较开销微增（仅 code/list 块逐行），**可忽略**。
- 所有修复**不破坏 G6 等价**：只改「误判/崩溃/越权/泄露」路径，正常语料产物字节不变。

## 8. 文末提醒（需本机以外环境 / 人工测试的登记项）

按用户要求，完成任务后提醒以下**仅登记、未实施**的外部/人工项：

1. **B-5 / R6-OPS-1（HIGH）**：launchd plist 修复后，**需 macOS 真机** `launchctl load` 跑一次 `cron --daily`，确认根路径生效——这是本轮唯一 HIGH 级却需真机确认的项，**建议优先安排**。
2. **B-6 / TEST-3**：CI **仍无 Windows 腿**（头号目标机 8GB Win11），需 CI 环境/人工补。
3. **B-7 / T3**：跨机性能基准需目标机。
4. **B-8 / T12**：serve/work 常驻资产实测需目标机/长时运行。
5. **B-9 / T13**：备份 v2 真机恢复演练需真机/人工（数据安全相关，**建议优先**）。
6. **B-10**：前端真实浏览器人工走查需人工。
7. **B-1…B-4**：需构造特定语料/并发压测来复现与回归（沙箱内可做，但需先备样本）。

> 本轮**未实施任何代码/文档改动**（遵用户「审查完成后不实施」）。上述 A 类 12 项待批准后按第 4 章顺序实施；C 类 5 项决策（D1–D5）已按「功能全面、性能优先」代为制定，供确认。
