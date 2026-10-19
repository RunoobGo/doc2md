# Doc2md v2.12.0 第十三轮全面技术审查报告（CH-491）

> 审查人：高见远（架构师） · 原则：**功能全面、性能优先**
> 审查对象：`/Volumes/fx/Object/Doc2md` @ `main` / `cdea8b0`
> 审查性质：**只读审查**。本报告未修改任何源码或既有文档，唯一新建文件即本文件。
> 编号：本轮变更自 **CH-491** 起（README.md 与 `docs/dev-plan.md` 均声明当前编号至 CH-490）。

---

## 0. 审查口径与取证方式

| 项 | 说明 |
| --- | --- |
| 取证手段 | `Read` / `Grep` / `Glob` 全文通读 + 只读 Bash（`.venv/bin/ruff check`、`pytest --collect-only -q` 解析、AST 静态扫描、`tomllib` 键集比对）。**未运行全量测试**，未写 `work/` 目录。 |
| 覆盖面 | `doc2md/` 下 **41 个 `.py` 全部通读或逐行命中检索**；`docs/dev-plan.md`（1856 行）头部、第三章、附录 A–G 逐段比对；`README.md`（273 行）全文。 |
| 约束遵守 | 全程离线（无一次出网调用）；不引入任何第三方运行时依赖；所有建议均可用标准库实现。 |

---

## 1. 审查结论摘要

Doc2md v2.12.0 是一条**工程质量显著高于同类中型 Python 项目平均水平**的离线管线：`doc2md/` 41 模块 / 12387 行（按 `wc -l` 口径，与 `docs/dev-plan.md:214` 记载一致），`tests/` 55 文件 / 19112 行、收集 **998 项（990 passed + 8 platform skips）**，其中 `gate` 红线层 **402 项**；`.venv/bin/ruff check doc2md/` 输出 `All checks passed!`；配置键三处来源（`config/loader.py` 的 `SPEC`、`config/default_toml.py` 内置默认、`config/doc2md.example.toml` 分发示例）**键集完全相等，均为 64 键**，无漂移。架构分层（安全 / 运维 / 存储 / 管线）边界清晰，IR 契约（`doc2md/ir/model.py`）未被旁路，`doc_id` 始终由内容哈希派生（文件名不参与，约定 14）。性能面上，前期多轮已经把主要热点收拾过一轮：FTS 删除走 `search_map` rowid 映射（`storage/repo.py:20-42`，避免写锁内全库扫）、检索取消第二遍 `COUNT(*)` 改 `LIMIT limit+1` 探测（`repo.py:315-319`）、FTS 零命中不再付全表 LIKE（`repo.py:352`）、写路径三写合并单事务（`api.py:568-594`）、进程池无条件 terminate/kill 防解释器退出死锁。因此本轮**未发现 P0 级性能缺陷**。

本轮的实质问题集中在**三处**：① **文档与代码的事实性漂移**（附录 A 键数 62 → 实为 64 且缺 CH-487 两个新键行；附录 E `DB_VERSION = 6` 与 `storage/db.py:22` 的 `9` 冲突且与同书 line 75 自相矛盾；附录 B `doctor` 项数 33 → 实为 39；附录 A 的配置叠加顺序描述与 `loader.py:182-223` 的级联实现相反）——这四类都是**纯文档改动、零回归风险、且直接误导使用者**，故列为 **P0 立即实施**；② **一处错误码双轨 + 一处 CLI/API 能力不对齐**（`E4092` 有类有登记却从不抛出、`GET /search` 无 CLI 对等入口）；③ **三个超长函数**（`api.create_app` 852 行、`s3_structure.analyze` 360 行、`worker.drain_once` 152 行）与 **4 处零引用死代码**。

性能面上最值得做的一件是**把 `modal_size` 的全文档第二次行遍历并入 S1 解析单趟**（`convert.py:157` 调 `s3_structure.py:847`，对全文档每个 `Line` 做 `round + len + Counter`；而 `read_any` 已经逐行构造 `Line.size`，顺带累计直方图几乎零成本）。另有一条**必须先澄清的认知**：`s1_5_gate.py:157-161` 自述的「流式喂入已具备未接线」**不是可直接兑现的性能收益**——`convert.py:123-128` 记录了 CH-460 的实测结论「分片读取后 320 页峰值内存 132.9MB → 134.9/137.9MB，**反而更差**」，因为驻留主体是 IR blocks（5890 块）而非 `pages_all`。故该项**只登记不实施**（P2）。

共 **17 项**：P0 **2** / P1 **9** / P2 **6**。类别分布：架构 2、性能 4、功能 3、质量 4、文档 4。

---

## 2. 本次基线实测

| 项 | 实测值 | 来源 |
| --- | --- | --- |
| `doc2md/` 模块数 / 行数 | **41** / **12387**（`wc -l` 口径） | 本机统计，与 `dev-plan.md:214` 一致 |
| `tests/` 文件数 / 行数 | **55** / **19112** | 本机统计，与 `dev-plan.md:1279` 一致 |
| 收集用例 | **998**（gate **402**） | `pytest --collect-only -q` 逐文件求和，与 README 基线一致 |
| `ruff check doc2md/` | **All checks passed!** | `.venv/bin/ruff`（select F/E/I，ignore E501，py311） |
| 配置键集 | `len(SPEC)` = `DEFAULT_TOML` = `example.toml` = **64**，三处集合**完全相等** | `tomllib` 解析 + 集合比对 |
| 版本常量 | `PIPELINE_VERSION=2.12.0` / `SCHEMA_VERSION=14` / `PLAN_VERSION=3.26` / `DB_VERSION=9` | `doc2md/__init__.py`、`doc2md/storage/db.py:22` |
| 零消费配置键扫描 | 64 键中仅 `security.sanitize_fail_action` 无行为消费点 | 见 §3 A-2 |
| 疑似死代码（AST，生产引用=1 即定义本身、测试引用=0） | 4 处 | 见 §3 Q-4 |
| 超长函数（≥150 行） | 5 处；其中 ≥300 行 3 处 | 见 §3 Q-1/Q-2/Q-3 |
| 未实例化错误类 | `E4092_SourceSuperseded`（1 处） | 见 §3 F-1 |

---

## 3. 优化项清单

### 3.1 架构类

| 编号 | 类别 | 优先级 | 问题描述（文件:行号） | 建议方案 | 预期收益 | 风险与回归面 | 外部/人工 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A-1 | 架构 | **P1** | **`api.py` 与 `worker.py` 存在双向（循环）依赖，仅靠函数内延迟 import 掩盖**。`doc2md/worker.py:318` 与 `worker.py:408` 各自 `from .api import _emit`，`worker.py:246` `from .api import _rebuild_artifact`；反向 `doc2md/api.py:490` `from .worker import enqueue_many`、`api.py:605` `from .worker import _drop_orphan_artifact`、`api.py:649` `from .worker import _read_ir`，**共 3 处**。`docs/dev-plan.md` 第三章 3.6 只记载「唯一的向上引用是 `worker.py`（编排层）→ `api.py`（入口层）的 `_emit`」，与实际的双向事实不符，新加端点极易误触成环。 | 抽出两个**无环**的中性模块：`doc2md/events.py`（事件入库 + `work.jsonl` 落盘，即现 `api._emit` 与 `worker.py:407-413` 的公共体）与 `doc2md/artifacts.py`（`_rebuild_artifact` / `_drop_orphan_artifact` / `_read_ir`）。`api.py` 与 `worker.py` 改为**同向依赖**这两个模块，删除 5 处延迟 import。 | 消除运行期循环导入面；把「改 api 触发 worker 重载」的隐性成本降为 0；3.6 节的记载重新成立。 | 中。需同步改 tests 中对 `api._emit` / `worker._emit` 的 monkeypatch 路径（gate 面 402 项必须全绿）；函数签名保持不变则行为零变化。 | 否（本机可验） |
| A-2 | 架构 | P2 | **冻结键 `security.sanitize_fail_action` 无任何行为消费点**。`doc2md/config/default_toml.py:130` 声明且注释「唯一合法值，配错即启动期 ValueError」；`doc2md/config/loader.py:32` 进 `FROZEN_KEYS`、`:166` 进 `_enum` 校验；但 `doc2md/security/names.py`（`sanitize_name` / `assert_name_safe` / `assert_entry_safe`）**从不读取该键**。对 64 键做「模块外引用」扫描，它是**唯一零消费键**。即：枚举只有一个值、被冻结、且无消费点，是纯声明性键。 | 仅登记。两个可选去向：① 接进 `names.py` 的清洗失败分支（`reject` → 抛 `UnsafeNameError`）；② 在附录 A 明确标注「保留键，暂未接线」。补键或修改语义都会牵动 `SPEC` 键数与 gate 键集用例，故不在本轮动。 | 澄清「配置项是否真的生效」，避免用户以为可调。 | — | 否（决策项） |

### 3.2 性能类

| 编号 | 类别 | 优先级 | 问题描述（文件:行号） | 建议方案 | 预期收益 | 风险与回归面 | 外部/人工 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P-1 | 性能 | **P1** | **`modal_size` 造成全文档第二次逐行遍历**。`doc2md/pipeline/convert.py:157` `base_size = modal_size(pages_all)`，`doc2md/pipeline/s3_structure.py:847` 的实现对**全文档每一个 `Line`** 执行 `round(ln.size,1)` + `len(ln.text.strip())` + `Counter` 累加。而 `read_any`（`convert.py:129`）在 S1 解析时**已逐行构造 `Line.size`**，同一批数据被遍历两遍。 | 在 S1 解析的单趟内顺带累计字号直方图（`RoundKey → 字符数`），`RawPage`(或 `read_any` 的返回容器) 携带增量；`modal_size` 改为对**全文档直方图求和后取众数**。`convert.py:157` 的调用点与「必须先算全文档众数再传每片」的 CH-471（PIPE-1）纪律**保持不变**（口径仍是全文档，不退化成逐片）。 | 单趟合并后：**O(总行数) 的一次遍历降为 O(1) 直方图归并**。320 页 / 5890 块样本文档少约 3~5 万次 Python 层 `round+len+Counter` 操作（本机量级 30~80ms/文档）；单文档占比仅 1%~3%，但**在批量场景线性叠加**（1000 文件批 ≈ 30~80s 量级的纯 CPU 节省），同时削掉一次全量 pass 带来的峰值 CPU 抖动。 | 中。`base_size` 是「分片≡单片」的判据根（`convert.py:153-156`），必须与 `gate` + golden 样本逐字节比对；`s1.chunk_pages > 0` 分块 open/close 时直方图需跨块累计。 | 否（本机 `test_perf_lowend.py` 29 项红线可验） |
| P-2 | 性能 | **P1** | **`Store` 每开一条连接就跑 5 次 `PRAGMA table_info` 探测，与版本号无关**。`doc2md/storage/db.py:281`（blocks）、`:290`（docs）、`:293`（tasks）、`:306`（tokens）、`:341`（search_blocks）在 `_migrate()` 内无条件执行；同处还有 `db.py:235-251` 的 6 条 PRAGMA（含 `busy_timeout=30000`、`synchronous=NORMAL`、`cache_size=-8192`）与全量 DDL 重放。进程池每 worker 进程 × 每线程连接各付一次（`db.py` 用 `threading.local` + weakref 注册表，见 `doc2md/storage/db.py` 的 `Store`）。 | **只门控探测、不动 DDL 重放**：把 5 处 `PRAGMA table_info` 移到 `if cur_ver < DB_VERSION:` 分支内（`db.py:274` 已有该判定），DDL 无条件重放保持原样——CH-476（R7-STOR-2，决策 D1）明确要求「DDL 重放不得关在版本门之后」，此条不可动。 | 每条连接省 5 次字典表扫描；`batch` 8 worker × 每 worker 多线程下，一个 1000 文件批累计省**数百至上千次** `table_info`；连接建立耗时（进程池预热期的主要成本之一）可见下降。 | 低。探测仅用于「存量库补列」，`user_version` 已达 `DB_VERSION=9` 时不存在待补列；不触碰任何数据路径。gate 402 项与 golden 面不受影响。 | 否 |
| P-3 | 性能 | **P1** | **`POST /jobs` 小/中批（< `perf.sync_min_parallel`）在请求线程内串行 convert**。`doc2md/api.py:646-647`：`parallel_min = max(2, int(config.get(cur(), "perf.sync_min_parallel", 8) or 0))`，`if len(pending) >= parallel_min:` 才走 `run_batch` 进程池（`api.py:648-665`）；否则落到进程内串行。默认阈值 8 意味着 **2~7 个文件的目录请求全部串行**。`api.py:639-645` 的注释已如实说明「小批次建池 + 子进程预热 pdfplumber 的成本高于收益」，但该结论是 CH-471 轮实测，未给出拐点数据。 | 本机重测拐点：以 2/3/4/6/8 文件 × 小/中/大 PDF 网格测 `串行 Σt` vs `池 max(t)+建池开销`，把实测拐点写回 `perf.sync_min_parallel` 默认值与 README「低配机」表。若拐点显著低于 8，则下调默认值。 | 若拐点实为 3~4，则 4~7 文件目录请求耗时由 **Σt 降为约 max(t)**（4 核下 3~5×）。 | 中。`api.py:650-659` 的 `_SYNC_POOL_GATE` 有界等待 + 503 + `Retry-After` 契约不得破坏；池生命周期（建池→转换→读产物）须整体留在临界区内。 | **是（需本机人工实测拐点）**——见 §5 第 4 项 |
| P-4 | 性能 | P2 | **`watcher` 每轮仍是全目录 O(files) 扫描**。`doc2md/security/watcher.py:15-22` 的 docstring 已如实记载：CH-473（R5-PERF-1）的 mtime 短路**只省下了转换与哈希，目录扫描本身仍是每轮全量**。 | 仅登记。改为 OS 事件源（inotify / FSEvents）需引入 `watchdog` 等第三方运行时依赖，**违反「不新增第三方运行时依赖」硬约束**；退而求其次的可行方向是「扫描与转换解耦 + 扫描结果缓存 mtime 树」，但收益待实测。 | 投递目录文件数上万时，watch 轮询的单轮成本从 O(files) 降为 O(变化)。 | — | **是（Windows/macOS 双平台实测）**——见 §5 第 5 项 |

### 3.3 功能完整性类

| 编号 | 类别 | 优先级 | 问题描述（文件:行号） | 建议方案 | 预期收益 | 风险与回归面 | 外部/人工 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F-1 | 功能 | **P1** | **错误码 `E4092` 存在「类 / 字面量」双轨**：`doc2md/errors.py:87` 定义 `class E4092_SourceSuperseded(ApiError)`，`:116` 登记进 `CODE_INDEX`；但全仓**从不实例化该类**——唯一抛出点是 `doc2md/worker.py:191` 用字符串字面量 `sched.mark(job["job_id"], "rejected", "E4092", detail)`。后果：`E4092` 的拼写在生产路径上没有类型保护，任何人改 `errors.py` 的 `code` 常量都不会被 worker 感知；`CODE_INDEX` 也因此不能作为「每个码都有抛出点」的断言依据。 | ① `worker.py:191` 改为引用 `E4092_SourceSuperseded`（或直接引用其 `.code` 常量）；② 补一条 gate 断言：遍历 `errors.CODE_INDEX`，断言每个码在 `doc2md/` 内**至少有一处类级或字面量引用**，防后续再漏。 | 消除一处双轨；新增一条**结构性红线**防止同类问题回潮（当前 16 个错误码中仅此 1 处漏网）。 | 低。仅改引用形式，码值与 HTTP 映射不变；gate 新增 1 项。 | 否 |
| F-2 | 功能 | **P1** | **CLI 无检索入口，`GET /search` 是 API 独占能力**。`doc2md/api.py:949-961` 提供 `GET /search`（含 `full` 模式）；而 CLI 子命令表为 `serve / token / cron / backup(drill\|restore) / convert / work / watch / tokenizer(export\|verify) / doctor`（`doc2md/cli.py` `build_parser`），**无 `search`**。离线单机场景下，用户若不启服务（NFR-1 下很多用户就是纯 CLI），**已入库文档无法检索**——这是「功能全面」口径下最实在的一处缺口。 | 新增 `doc2md search QUERY [--full] [--limit N] [--json]`，实现直接复用 `doc2md/storage/repo.py:307` 的 `search()`；输出走 `cli.py` 的 `_jline()`（`ensure_ascii=True`）与既有退出码口径（0 / 4 业务 / 5 系统）。同步补 `docs/dev-plan.md` 附录 B 一行 + README 命令表一行。 | 补齐 CLI/API 能力对齐；离线纯 CLI 用户首次具备全文检索能力。约 +30 行实现 + 文档 2 行。 | 低。新增命令面，不改既有行为；需新增 gate 用例（退出码 / `_jline` 字节一致性 / `full` 模式）。 | 否 |
| F-3 | 功能 | P2 | **`WalkLimits.deadline_seconds` 硬编码，未进配置面**。`doc2md/security/dirguard.py:22` `deadline_seconds: float = 10.0`，被 `:85` `:94` `:111` `:188` 消费，但**不在 `SPEC`（64 键）内**，扫描确认只有测试在显式设置它。大目录 / 慢盘（机械盘、网络挂载）上 10s 是硬上限，用户无法调。 | 仅登记。补键会牵动 `SPEC` 64→65、附录 A/G 键数、`config/doc2md.example.toml` 与 gate 键集用例，属跨面改动，单独立项。 | 慢盘环境下目录门禁不再因 10s 硬上限误报 `E4133`。 | — | 否（决策项） |

### 3.4 代码质量类

| 编号 | 类别 | 优先级 | 问题描述（文件:行号） | 建议方案 | 预期收益 | 风险与回归面 | 外部/人工 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Q-1 | 质量 | **P1** | **`api.create_app` 852 行，内嵌 20 个端点闭包**。`doc2md/api.py:275-1126`，其中 `post_job` 单独 **314 行**（`api.py:423-736`）。端点清单：`/jobs`(POST 422 / GET 738)、`/docs/<id>`(768)、`/docs/<id>/markdown`(782)、`PATCH .../blocks/<id>`(828)、`rollback`(843)、`DELETE /docs/<id>`(862)、`DELETE /sources/<key>`(879)、`doc audit`(894)、`audit/deletions`(925)、`/search`(949)、`/events`(963)、`/jobs/<id>/events`(971)、`/settings`(GET 989 / PUT 994)、`/system/reload`(1033)、`/tokens`(POST 1087 / DELETE 1104)、`/`(1113)、`/healthz`(1118)。**任何新端点都只能继续往这 852 行里堆**。 | **分步、最小改动**：第一步（本轮）把 `post_job` 内 4 个**已无闭包依赖**的内嵌函数提为模块级纯函数——`_idem_for`(477-484)、`_idem_preflight`(512-535)、`_conflict_one`(537-552)、`_finish_one`(554-631)，显式传参替代闭包捕获；第二步（P2 登记）才考虑端点分模块。 | 第一步后 `create_app` 降至约 700 行、`post_job` 降至约 240 行；4 个纯函数首次可被**独立单测**（当前只能经 HTTP 面间接覆盖）。 | 中。第一步仅做函数提取与显式传参，不改变调用序；`_finish_one` 内的事务序（mark→save_ir→emit，`api.py:589-594`，CH-476 决策 D6）必须逐字不动。gate 402 项全绿 + golden 比对。 | 否 |
| Q-2 | 质量 | **P1** | **`s3_structure.analyze` 360 行**（`doc2md/pipeline/s3_structure.py:480-839`），7 类块判定（标题 / 表格 / 代码 / 列表 / 引用 / 分隔线 / 段落）全部内联在单一函数体内。 | 按块类型拆为模块级 `_detect_heading` / `_detect_table` / `_detect_code` / `_detect_list` / `_detect_quote` / `_detect_hr` / `_detect_para`，`analyze` 只保留编排（遍历 → 分派 → 收尾的 `_fix_levels` 层级校正）。 | 主函数降至约 120 行；新增/调整块类型不再需要改主流程；每个 `_detect_*` 可独立单测（当前 7 类判定只能经整文档转换间接覆盖）。 | 中高。块判定是 golden 字节级可比性的核心面，必须**逐样本比对**；`_fix_levels` 的 `flags.level_from_hint` 跳过语义（CH-476 R7-PIPE-4）不得变。 | 否（golden 样本本机可比对） |
| Q-3 | 质量 | **P1** | **`worker.drain_once` 152 行**（`doc2md/worker.py:135-286`）。另有 ≥100 行的：`batch.run_batch` 141 行（`doc2md/pipeline/batch.py:112`）、`convert._convert` 130 行（`doc2md/pipeline/convert.py:94`，含 CH-459/CH-471 等 6 处决策注释，可读性尚可）、`cron.run` 109 行（`doc2md/cron.py:182`）、`drill.run_drill` 105 行（`doc2md/ops/drill.py:119`）、`cli.cmd_doctor` 92 行（`doc2md/cli.py:644`）。 | 只拆 `drain_once`：分为 `_claim_batch`（`claim` + `reclaim_stale` 编排）、`_run_one`（单作业转换 + 落库）、`_handle_reject`（`E4092` / 孤儿产物清理）三段。`batch.run_batch` 与 `convert._convert` 因注释密度高、决策耦合紧，**本轮不拆**（P2 登记）。 | `drain_once` 三段各 ≤60 行，队列消费路径的失败分支首次可独立单测。 | 中。队列面涉及 `claim` 的 `limit` 语义与 `reclaim_stale` 的超时回收（CH-455 口径），需 gate 全绿。 | 否 |
| Q-4 | 质量 | **P1** | **4 处零引用死代码**（AST 扫描：生产引用数 = 1 即定义本身，测试引用数 = 0）：① `doc2md/llm.py:575` `LlmClient._build_payload`（`llm.py` 全仓仅此一处出现）；② `doc2md/pipeline/s1_5_gate.py:140` `_avg_chars`；③ `doc2md/pipeline/s1_parse.py:486` `_is_bold`；④ `doc2md/pipeline/s1_parse.py:493` `_is_italic`。另 `doc2md/auth.py:81` `revoke(..., revoke_window: float = 5.0)` 的形参自引入即无使用方（`auth.py:83` docstring 自述）。 | 删除 ①~④；`_avg_chars` 删除时**必须同步改** `s1_5_gate.py:155` 的 docstring（该行明写「`avg_chars` 不再对全文档重跑 `_page_metrics`」）。`revoke_window` 形参归入 P2（删形参属 API 面改动，需单独评估）。补一条 gate 断言：AST 扫描 `doc2md/` 模块级函数与类方法，零引用即失败（dev 依赖，仅测试期使用）。 | 净减约 60 行；新增一条**结构性红线**防死代码回潮。 | 低。删除前须再跑一次全仓引用扫描确认；`ruff`（F401 未使用导入、F811 重定义）已全绿，删除后仍须保持。 | 否 |

### 3.5 文档一致性类

| 编号 | 类别 | 优先级 | 问题描述（文件:行号） | 建议方案 | 预期收益 | 风险与回归面 | 外部/人工 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D-1 | 文档 | **P0** | **附录 A 配置键数过期 + 缺 CH-487 两个新键行**。`docs/dev-plan.md:860` 称「`doc2md/config/default_toml.py`（内置默认，**62 键**）与 `config/doc2md.example.toml`（分发示例，**62 键**）……实测本机三处各 **62**」，`:864` 再次出现「62 键」；`dev-plan.md:1271` 称 `SPEC` 表「（62 键……）」、`:1272` 称「62 个配置键的内置默认值唯一来源」、`:1325` 称 `doc2md.example.toml`「62 键」。**实测三处均为 64 键且集合完全相等**。更严重的是：附录 A 的键表中**没有 `s1.legacy_doc_backend` 与 `s1.legacy_doc_timeout_s` 两行**（CH-487 引入，`doc2md/config/default_toml.py` 内已存在，`convert.py:131-132` 已在读），按附录抄配置的用户抄不到这两个键。 | 附录 A 键数 62 → **64**，补 `s1.legacy_doc_backend` / `s1.legacy_doc_timeout_s` 两行（取值域与默认照抄 `default_toml.py`），同步改 `:1271` `:1272` `:1325` 三处键数表述。 | 文档键数与代码**事实一致**；CH-487 的两个 `.doc` 后端键首次在权威键表可见。纯文档改动，**零回归风险**。 | 无（纯文档）。 | 否 |
| D-2 | 文档 | **P0** | **附录 E 的 `DB_VERSION` 与代码冲突，且文档自相矛盾**。`docs/dev-plan.md:1014` 写「`PRAGMA user_version` = `DB_VERSION = 6`」；而 `doc2md/storage/db.py:22` 是 `DB_VERSION = 9`。同一份文档 `dev-plan.md:75` 的状态表**已经写明 9**（「CH-486 由 8→9：新增 `ix_tasks_created`……；CH-485 由 7→8：……」）——即正文与附录互相打架，读者按附录 E 校对存量库会得到错误结论。 | `dev-plan.md:1014` 的 `DB_VERSION = 6` 改为 **9**，并补 CH-485(7→8) / CH-486(8→9) 的变更说明（照抄 `:75` 的口径，含新增索引与列）。 | 消除文档内部矛盾；存量库版本校对口径唯一。纯文档改动，**零回归风险**。 | 无（纯文档）。 | 否 |
| D-3 | 文档 | **P1** | **附录 B 的 `doctor` 输出项数过期**。`docs/dev-plan.md:948` 称「固定 **29 项** + 4 依赖探测 = **33 项**」并列出 29 个固定键名。实测 `doc2md/cli.py:655-715`：固定键 **34 项** + `DEPENDENCY_MODULES` 4 项（`cli.py` 定义为 `("pdfplumber","docx","flask","cryptography")`）+ `legacy_doc_backend`（`cli.py:714`）**= 39 项**。附录列出的 29 键**缺 5 个**：`root_private`（`cli.py:665`）、`config_unknown_keys`（:675）、`backup_external_dir`（:676）、`backup_passphrase_env_set`（:677）、`offline_sendmsg`（:695，CH-485 R9-OPS-3 引入），且漏记 `legacy_doc_backend`（CH-487）。 | 改为「固定 **34 项** + 4 依赖探测 + 1 后端探测 = **39 项**」，补齐 6 个键名，并注明 `llm.mode=server` 时再追加 `llm_server`（`cli.py:709`）。 | `doctor` 输出字段清单与代码逐键对得上；用户不再因文档缺项误判「自检面少了什么」。纯文档改动。 | 无（纯文档）。 | 否 |
| D-4 | 文档 | **P1** | **附录 A 的配置叠加顺序描述与实现相反**。`docs/dev-plan.md:861` 称解析顺序「**只叠加一个用户文件**……两个候选位置**不是级联**」；而 `doc2md/config/loader.py:182-223` 的 `load()` 实现是 `home($DOC2MD_HOME/doc2md.toml)` → `cwd(./doc2md.toml)` → 显式 `--config` **逐层深度合并**（`loader.py:194` `sources = [home, cwd]`、`:202-219` 循环内 `_deep_merge(cfg, user)`）。`loader.py:185-187` 的注释明确记载 CH-483（R8-CFG-1）就是为修「旧实现只读取第一个命中的文件」而改的——**实现改了，附录没跟着改**。 | 改 `dev-plan.md:861` 该段为级联语义（默认 → home → cwd → `--config`，后者覆盖前者，逐层深度合并；显式 `--config` 必须存在且优先级最高）。 | 消除「按附录理解配置优先级会得出错误结论」的实质误导。纯文档改动。 | 无（纯文档）。 | 否 |

### 3.6 P2 仅登记（不实施）

| 编号 | 类别 | 优先级 | 问题（文件:行号） | 备注 |
| --- | --- | --- | --- | --- |
| A-2 | 架构 | P2 | `security.sanitize_fail_action` 冻结键无消费点（`default_toml.py:130` / `loader.py:32,166`） | 见 §3.1 |
| P-4 | 性能 | P2 | `watcher` 每轮全目录 O(files) 扫描（`watcher.py:15-22`） | 改 OS 事件源需第三方依赖，违反硬约束 |
| F-3 | 功能 | P2 | `WalkLimits.deadline_seconds = 10.0` 硬编码未进 `SPEC`（`dirguard.py:22`） | 补键牵动键集 64→65 与附录 A/G |
| Q-5 | 质量 | P2 | `doc2md/cron.py:10` 与 `:11` **docstring 整行重复**（同一句「不装 cron、不在 serve 内起常驻线程；由 launchd StartCalendarInterval 触发。」连写两遍） | 一行删除，零风险 |
| Q-6 | 质量 | P2 | `cron.run()` 步骤注释编号 **1→2→4→5→6→7→8，缺「3」**（`cron.py:197` `1)`、`:203` `2)`、`:227` `4)`、`:231` `5)`、`:245` `6)`、`:255` `7)`、`:269` `8)`） | 需先核附录 E 步骤表实际步数再决定是补编号还是重排序 |
| Q-7 | 质量 | P2 | `auth.revoke(..., revoke_window: float = 5.0)` 形参自引入无使用方（`auth.py:81-83` docstring 自述） | 删形参属 API 面改动，单独立项 |
| P-5 | 性能 | P2 | **S1.5 门禁流式喂入「已具备未接线」不是可直接兑现的收益**。`s1_5_gate.py:157-161` 自述「当前生产路径只有 `check()` 一个入口，它一次性喂入全文档页列表……带非零 `offset` 的逐片流式喂入目前只有测试在演练」，`convert.py:135` 即 `s1_5_gate.check(pages_all, ...)`。但 `convert.py:123-128` 记录了 CH-460 实测：**改为「PDF 按片读取、单片驻留」后 320 页峰值内存 132.9MB → 134.9/137.9MB，反而更差**，因为驻留主体是 IR blocks（5890 块）与 pdfplumber 单页缓冲，`pages_all` 仅占约十分之一 | **前置条件未解决**：须先让 IR 不全部驻留（分片 IR 必须全部驻留供缝合），否则接线后无收益。故**只登记不实施** |
| Q-8 | 质量 | P2 | `batch.run_batch` 141 行（`batch.py:112`）、`convert._convert` 130 行（`convert.py:94`）、`cron.run` 109 行（`cron.py:182`）、`drill.run_drill` 105 行（`drill.py:119`）、`backup.restore_safe` 99 行、`cli.cmd_doctor` 92 行（`cli.py:644`） | 注释密度高、决策耦合紧，拆分收益低于风险；本轮不动 |

---

## 4. P0 + P1 任务分解

> 排序原则：**先零风险文档（P0）→ 再结构/性能（P1）**。所有任务均遵守最小改动原则，不新增第三方运行时依赖，不改 `doc_id` 口径，不改 golden 字节可比性。

### 依赖图

```
T1 (P0 文档订正) ──┐
                   ├─→ T3 (死代码清理) ──┐
T2 (错误码双轨) ───┘                     │
                                         ├─→ T5 (长函数收敛)
T4 (S1 单趟直方图) ─────────────────────┘
        └─ 需 golden 比对，与 T5 无依赖、可并行
```

- **T1 无依赖**，可立即开工。
- **T2 无依赖**，可与 T1 并行。
- **T3 无依赖**（纯删除），但建议排在 T1 之后——`_avg_chars` 删除需改 `s1_5_gate.py:155` 的 docstring，与 T1 同属「文档/注释订正」批次，合并提交更干净。
- **T4 依赖 golden 样本基线**（已存在，无需前置任务），但必须**先于或独立于 T5**，避免两类改动混在一起导致性能/字节回归无法归因。
- **T5 依赖 T3**（先清死代码再动结构，避免拆函数时把死代码一起搬）。
- **T6**（`POST /jobs` 并行拐点）**依赖人工实测**，不进本轮实施序列。

---

### T1 · P0 文档订正（附录 A 键数 / 附录 E DB_VERSION）— 优先级 P0

- **依赖**：无
- **改动文件**：仅 `docs/dev-plan.md`
- **改动点**：
  1. `:860` 「内置默认，62 键」「分发示例，62 键」「实测本机三处各 **62**」 → **64**；`:864` 同改。
  2. 附录 A 键表**新增两行**：`s1.legacy_doc_backend`（默认 `auto`，取值域照抄 `doc2md/config/default_toml.py`）、`s1.legacy_doc_timeout_s`（默认 `120`）。
  3. `:1014` 「`DB_VERSION = 6`」 → **9**，并补 CH-485(7→8) / CH-486(8→9) 变更说明（口径照抄 `:75`）。
  4. `:1271` 「`SPEC` 表（62 键」 → 64；`:1272` 「62 个配置键」 → 64；`:1325` 「62 键」 → 64。
- **验收**：`grep -c "62 键" docs/dev-plan.md` = 0；附录 A 键名集合 == `tomllib.loads(DEFAULT_TOML)` 扁平键集（64）。
- **风险**：无（纯文档）。**不触碰** `doc2md/` 与 `tests/`。
- **回归面**：无。

### T2 · P0/P1 文档订正（附录 B doctor 项数 / 附录 A 级联顺序）— 优先级 P0（D-3、D-4 同属零风险）

- **依赖**：无（可与 T1 并行）
- **改动文件**：仅 `docs/dev-plan.md`
- **改动点**：
  1. `:948` 「固定 **29 项** + 4 依赖探测 = **33 项**」 → 「固定 **34 项** + 4 依赖探测 + 1 后端探测 = **39 项**」；键名清单补 `root_private` / `config_unknown_keys` / `backup_external_dir` / `backup_passphrase_env_set` / `offline_sendmsg` / `legacy_doc_backend`；注明 `llm.mode=server` 追加 `llm_server`。
  2. `:861` 「只叠加一个用户文件……两个候选位置**不是级联**」 → 改为级联语义：**内置默认 → `$DOC2MD_HOME/doc2md.toml` → `./doc2md.toml` → 显式 `--config`，逐层深度合并、后者覆盖前者；显式 `--config` 必须存在且优先级最高**（与 `doc2md/config/loader.py:182-223` 一致）。
- **验收**：附录 B 列出的固定键名集合 == `cli.py:655-695` 实际输出的 34 个键名。
- **风险**：无（纯文档）。
- **回归面**：无。

### T3 · P1 错误码双轨收口 + 死代码清理 + 新增两条结构性红线

- **依赖**：T1（建议在其后，合并 docstring 修订）
- **改动文件**：
  - `doc2md/worker.py:191` — `"E4092"` 字面量 → `E4092_SourceSuperseded` 类的 `.code` 引用
  - `doc2md/llm.py:575` — 删除 `LlmClient._build_payload`
  - `doc2md/pipeline/s1_5_gate.py:140-143` — 删除 `_avg_chars`；**同步改 `:155` docstring**（该行提及 `avg_chars`）
  - `doc2md/pipeline/s1_parse.py:486-491` / `:493-498` — 删除 `_is_bold` / `_is_italic`
  - `tests/`（新增）— 两条 gate 断言：
    ① 遍历 `errors.CODE_INDEX`，断言每个码在 `doc2md/` 内至少一处类级或字面量引用；
    ② AST 扫描 `doc2md/` 的模块级函数与类方法，零引用即失败
- **验收**：`ruff check doc2md tests` 全绿；gate **402 + 2 = 404** 项全绿；全量 **998 + 2 = 1000** 项。
- **风险**：低。删除前须再跑一次全仓引用扫描二次确认；`ruff` F401/F811 面不得新告警。
- **回归面**：gate 402 项 + 全量 998 项。

### T4 · P1 性能：S1 单趟累计字号直方图，消除 `modal_size` 的第二次全文档遍历

- **依赖**：无（可独立开工；不建议与 T5 合并提交）
- **改动文件**：
  - `doc2md/pipeline/s1_parse.py` — 在 `read_any` / `Line` 构造的单趟内累计 `round(ln.size, 1) → 字符数` 直方图，随 `RawPage`（或 `read_any` 的返回容器）携带
  - `doc2md/pipeline/s3_structure.py:847` — `modal_size(pages)` 改为对**全文档直方图求和后取众数**（保留 `pages` 入参签名与「全文档口径」，不退化成逐片）
  - `doc2md/pipeline/convert.py:157` — 调用点保持不变（仍传 `pages_all`）
- **验收**：
  - **逐字节不变**：全部 golden 样本（含 `s2.mode=single` / `auto` / 分片三档）front-matter 与产物字节一致；
  - `tests/test_perf_lowend.py` **29 项红线**全绿，且记录改动前/后的 `large_320p` 峰值内存与耗时对照；
  - `base_size` 在均匀字体文档下取值与改动前**完全相同**。
- **风险**：中。`base_size` 是「分片≡单片」的判据根（`convert.py:153-156`）；`s1.chunk_pages > 0` 分块 open/close 时直方图必须跨块累计，不得按块重置。
- **回归面**：gate 402 项（含 `test_perf_lowend.py` 29 项）+ golden 样本比对。

### T5 · P1 长函数收敛（三处，分步）

- **依赖**：T3（先清死代码，避免拆函数时把死代码一起搬）
- **改动文件与改动点**：
  1. `doc2md/api.py` — 把 `post_job` 内 4 个**已无闭包依赖**的内嵌函数提为模块级纯函数：`_idem_for`(477-484)、`_idem_preflight`(512-535)、`_conflict_one`(537-552)、`_finish_one`(554-631)，显式传参替代闭包捕获。**不动** `_finish_one` 内的事务序（mark → save_ir → emit，`api.py:589-594`，CH-476 决策 D6）。
  2. `doc2md/pipeline/s3_structure.py:480-839` — `analyze` 按块类型拆为模块级 `_detect_heading` / `_detect_table` / `_detect_code` / `_detect_list` / `_detect_quote` / `_detect_hr` / `_detect_para`；`analyze` 只保留编排（遍历 → 分派 → `_fix_levels` 收尾）。**不动** `flags.level_from_hint` 跳过语义（CH-476 R7-PIPE-4）。
  3. `doc2md/worker.py:135-286` — `drain_once` 拆为 `_claim_batch` / `_run_one` / `_handle_reject`。
- **验收**：`create_app` ≤ 700 行、`analyze` ≤ 120 行、`drain_once` ≤ 60 行；`ruff` 全绿；gate 402 项全绿；golden 逐字节一致。
- **风险**：中高（Q-2 的块拆分风险最高）。建议**按 1 → 3 → 2 的顺序**提交（先低风险的两处，块拆分单独一批并单独跑 golden）。
- **回归面**：gate 402 项 + 全量 998 项 + golden 样本。

### T6 · P1（需人工实测，不进自动序列）`POST /jobs` 并行拐点重测

- **依赖**：P-3，需本机人工实测（见 §5 第 4 项）
- **改动文件（待测后决定）**：`doc2md/config/default_toml.py`（`perf.sync_min_parallel` 默认值）、`README.md`「低配机」表
- **说明**：`api.py:639-645` 的注释已如实记载「小批次建池 + 子进程预热 pdfplumber 的成本高于收益」，但**未给出拐点数据**。在实测数据出来前**禁止**下调默认阈值 8。

---

## 5. 「需外部系统环境或人工测试」专项清单（**只登记，不实施**）

> 以下各项**本轮不实施、不改代码**，仅登记待办。共同原因：需要本仓无法自证的外部环境、真实后端或人工判读，且任何一项的结论都会直接改变实现选择。

| # | 事项 | 关联项 | 为什么必须外部/人工 | 需要的环境 |
| --- | --- | --- | --- | --- |
| 1 | **Windows 真机验证**：`icacls` 权限收紧（`security/perms.py` 的 `_windows_identity` / `ensure_private`）、无 `sendmsg` 的离线守护降级（`doctor` 的 `offline_sendmsg`，`cli.py:695`）、路径与控制台编码（cp936 下 `_console_safe()` / `_jline()`）三者在真实 Windows 上的行为 | 全局 | 本机 macOS arm64 无法执行 Windows 分支；`test_ch480_resident_assets.py` 只能保证两平台资产同构，不能证明运行时行为 | Windows 10/11 实机 + 管理员与非管理员两种令牌 |
| 2 | **LibreOffice headless / Microsoft Word COM 后端真机验证**（`s1.legacy_doc_backend` = `libreoffice` / `word`） | D-1（附录 A 待补的两键） | `pipeline/legacy_doc.py` 的 PowerShell 脚本串与 `_run` 超时逻辑只有在装了 LibreOffice 或 Word 的机器上才真正执行；无后端时 `doctor` 只输出 `unavailable` | 装 LibreOffice 7.x 的机器 + 装 Microsoft Word 的 Windows 机器各一台 |
| 3 | **远程 CI 实跑**（GitHub Actions / 自建 runner 的 three-OS 矩阵） | 全局 | `test_ch479_ci_matrix.py` 只锁版本矩阵**口径**，不实跑；本机跑不出 Windows/Linux 结果 | 具备 macOS/Windows/Linux 三 OS 的 CI |
| 4 | **`POST /jobs` 并行拐点人工实测** | P-3 / T6 | 需要「2/3/4/6/8 文件 × 小/中/大 PDF」网格的真机计时才能定 `perf.sync_min_parallel` 的拐点；静态审查给不出数字 | 本机（macOS）+ Windows 各跑一轮，含 8GB 低配机档 |
| 5 | **`watch` 全目录扫描成本实测 + 投递目录规模化验证** | P-4 | 需上万文件的投递目录才能测出 O(files) 扫描的单轮成本；改 OS 事件源前必须先有基线 | 大目录 + macOS/Windows 双平台 |
| 6 | **真实语料人工判读**（扫描件 PDF、复杂多栏排版、混合中英表格、公式/脚注/修订痕迹） | Q-2（`analyze` 拆分） | 块类型判定的拆分是否等价，最终要靠人眼看产出的 Markdown 是否合意；golden 只能保证「与旧实现一致」，不能保证「旧实现是对的」 | 领域语料集 + 人工判读 |
| 7 | **多机 / 异构硬件性能测量**（8GB 低配机档、机械盘、CPU 核数差异下的 `perf.workers` / `s1.chunk_pages` 默认值） | P-1 / P-2 / README 低配机表 | `test_perf_lowend.py` 只在本机给出阈值断言，换机器后最优默认值会变 | 至少两台不同规格机器（含 8GB / 4 核档） |
| 8 | **launchd / Windows 任务计划程序真实安装验证**（`ops/launchd` / `ops/` 下的常驻资产，`cron.run` 由 `StartCalendarInterval` 触发） | Q-6（`cron` 步骤编号） | 只有真正装上并跨天触发，才能验证 `cron-daily-<YYYYMMDD>.json` 的 UTC 日期口径（CH-476 R7-OPS-2）与 `drill-*` 豁免在真实周期清扫下的行为 | macOS（launchd 装载 + 跨零点触发）+ Windows（任务计划程序） |
| 9 | **备份介质与恢复演练的外部介质验证**（`ops/backup.md` 记录的外部目录 / 加密外拷） | F-3 / 运维面 | `ops/drill.py` 的沙箱演练是**同机同盘**闭环，不能证明「拷到 U 盘 / 网络挂载后再恢复」的行为（尤其 `deadline_seconds` 硬上限在慢盘上的表现） | 一块外置介质或网络挂载点 |

---

## 6. 约束遵守声明

| 硬约束 | 遵守情况 |
| --- | --- |
| 离线单机、无出网（NFR-1） | ✅ 审查全程无一次网络调用；所有建议不引入云端依赖或远程绑定（`api.allow_remote_bind` 保持默认关） |
| 不破坏 998 项测试 / gate 红线层 / golden 字节可复现 | ✅ 所有 P0/P1 项均给出明确回归面；T4/T5 强制要求 golden 逐字节比对；本报告**未修改任何测试** |
| 不新增第三方**运行时**依赖 | ✅ 全部建议可用标准库实现；唯一新增依赖建议是「AST 零引用扫描」的 gate 断言，属**测试期**用途，可用 `ast`（标准库）实现，无需 dev 依赖 |
| 保持 Windows + macOS 双平台语义 | ✅ 无一项建议引入平台分支差异 |
| 最小改动原则 | ✅ P0 两项为**纯文档**零代码改动；P1 中 T3 为纯删除、T1/T2 无代码改动；结构性改动（T5）已按「先提纯函数、再拆块」分步 |
| 只做审查与方案输出 | ✅ **未修改任何源码或既有文档**，唯一新建文件即本报告 `docs/review-round13-CH-491.md` |

---

## 7. 表扬（值得保持的工程纪律）

审查中发现的、应当延续而非改掉的做法（登记备查，勿在后续重构中破坏）：

1. **`s1_5_gate.py:157-161` 与 `watcher.py:15-22` 的「未接线」自述**——文档如实标注「已具备未接线」而非宣称已生效，是可信度最高的注释形态。
2. **`convert.py:123-128` 的负面实测留痕**——把「改了反而更差」的实验结论写进代码注释，避免后人重复踩坑。这是 P-5 能被正确判定为「不实施」的唯一依据。
3. **`convert.py:98-100` 的 CH-459 注释**——明确记录「原实现同一文件多开一遍（magic + zip 目录）」，把 IO 放大的根因固化在改动点旁。
4. **`repo.py:352` 的「零命中就是零命中」**——不为「没搜到」付全表 LIKE 的代价，是性能面最干净的一处取舍。
5. **`cron.py:278-281` 的「close 是 no-op 安全操作」**——注释交代了「为什么不做的理由」与「为什么现在可以做」。
6. **`auth.py:83` 的死形参自述**——明知有死代码仍如实写明「自引入起即无使用方」，使 Q-7 能被一眼定位。

---

## 8. 与 PM 审查（`docs/pm-review-CH-491.md`）的交集核对（补记）

PM 于本轮同步了三块与本报告重叠的内容。我对其中的**可断言事实逐条重新取证**，结论如下：✅ = 独立复核成立；⚠️ = 需修正范围；❌ = 与实测不符。

### 8.1 CLI 面（PM 第 1 块）— 全部成立

| PM 主张 | 复核 | 证据 |
| --- | --- | --- |
| `COMMANDS` 只有 8 项、无读库入口 | ✅ | `doc2md/cli.py:746-749` = `serve / cron / convert / watch / work / tokenizer / doctor / backup`（**8 项**），`cli.py:755` `COMMANDS.get(args.cmd)` 分派。确无任何读库子命令 → 与本报告 F-2 同源。 |
| `--config` / `--root` 用 `argparse.SUPPRESS`，两层子命令再注册同名选项会覆盖父值 | ✅ | `cli.py:70-73`（`_add_common`，CH-485 / R9-OPS-4） |
| 新增子命令须同步改四处 | ✅ 且当前已有现成反例 | 见下 8.2 |

**F-2 随之收紧**：新增 `doc2md search` 必须同时改 **`cli.build_parser()` → `COMMANDS`（`cli.py:746`）→ `README.md:21-34` CLI 块 → `dev-plan.md` 附录 B（`:931-950`）** 四处，且必须走 `_add_common()`（不得自建 `--config`/`--root`），机器可读出口走 `cli._jline()`。这一点吸收进本报告 T?/F-2 的验收口径。

### 8.2 附录 B 的两处漏登记 — 部分成立，范围需修正

| PM 主张 | 复核 | 证据 |
| --- | --- | --- |
| 附录 B **漏了 `backup restore` 整行** | ✅ **成立（仅限附录 B）** | 附录 B（`:931-950`）只有 `doc2md backup drill`（`:945`），**无 `backup restore` 行**。 |
| ↳ 但称其为「CLI 面失真」的现成反例，隐含 README 也缺 | ⚠️ **README 不缺** | `README.md:32` 明确有 `doc2md backup restore SRC [--to PATH] [--force]  # 离线恢复（v1/v2 容器；CH-485）`。**失真面只有附录 B 一处**，不是两处。 |
| `convert --force` 在 README 与附录 B 两处都没登记 | ✅ **成立（两处都缺）** | `cli.py:139` `cv.add_argument("--force", action="store_true")`；`README.md:25-26` 的 convert 行与 `dev-plan.md:940` 的参数列**均不含 `--force`**。 |

→ **新增项 D-5（P1，纯文档）**：附录 B 补 `backup restore` 行；附录 B `:940` 与 `README.md:25-26` 的 convert 参数列补 `--force`。与 D-3 / D-4 同批（T2）提交。

### 8.3 PM 的 5 条静态红线 R-DOC-1~5 — 逐条复核

| 红线 | 复核 | 我的实测 |
| --- | --- | --- |
| **R-DOC-1** `len(loader.SPEC)` == 附录 A 表体键数；64 vs 62，缺 CH-487 两键 | ✅ **与本报告 D-1 完全一致** | `len(SPEC)` = `DEFAULT_TOML` = `example.toml` = **64**，三处集合完全相等；附录 A `:860`/`:864` 记 62，缺 `s1.legacy_doc_backend` / `s1.legacy_doc_timeout_s` |
| **R-DOC-2** 附录 F 构成式之和 == gate 实测；366 vs 402 | ✅ **成立** | 构成式逐项求和（`dev-plan.md:1043`）：40+29+10+54+12+1+7+91+4+4+4+4+7+8+10+23+3+28+5+15+7 = **366**；gate 实测 **402**。差 **36** = CH-488（PM 给的三处旁证：CH-486 时 344、12.10 记 CH-488 gate 380、CH-489 时 395） |
| **R-DOC-3** 附录 G 组头之和 == 逐行之和 == `wc -l` | ⚠️ **需修正：组头是对的，错的是 `:1211` 的自陈句** | 见下 8.4 |
| **R-DOC-4** `docs/` 下每个 md 必须在 README 或 dev-plan 中被引用 | 未复核（属 PM 独占面，我不重复取证） | — |
| **R-DOC-5** 附录 B 参数列 == `build_parser()`；附录 C 参数列 == 源码读取点 | 未复核（同上） | — |

### 8.4 R-DOC-3 的范围修正（重要：否则会误改正确数据）

PM 记「实测 **12316 / 41 个 .py / 整包 12387**，附录自陈的『33 行 / 39 个 .py / 9428 / 9497』四处过期」。数字全部正确，但**归因需修正**——**附录 G 的五个组头是准确的，不能改**：

```
组头实测（dev-plan.md:1213/1226/1242/1250/1265）
  G.1 = 8 模块 / 3307 行
  G.2 = 9 模块 / 3760 行
  G.3 = 3 模块 / 1152 行
  G.4 = 10 模块 / 2388 行
  G.5 = 5 模块 / 1709 行
  合计 = 35 模块 / 12316 行

整包实测 = 41 个 .py / 12387 行（wc -l）
差额     = 6 个模块 / 71 行
        = 6 个只做 re-export 的子包 __init__.py
          （doc2md/{config,ir,ops,pipeline,security,storage}/__init__.py
           = 21+4+3+9+17+17 = 71）
```

即 `35 + 6 = 41`、`12316 + 71 = 12387` **精确闭合**，附录 G 的组头与逐行数据是**自洽且正确**的（`:1327` 记载的采集方法 `find doc2md -name '*.py' | xargs wc -l` 仍然有效）。

**唯一过期的是 `dev-plan.md:1211` 那一句自陈**，四处数字全错：

| `:1211` 现写 | 实测应为 |
| --- | --- |
| 「G.1–G.5 共 **33** 行」 | **35** 个模块（且与它自己五张表的 8+9+3+10+5=35 自相矛盾） |
| 「包内 **39** 个 `.py`」 | **41** |
| 「减去 6 个……`__init__.py`（合 **69** 行）」 | **71** 行（注意：`doc2md/__init__.py` 的 17 行已计入 G.1 表内，不在这 6 个之列） |
| 「33 行合计 **9428**、整包 **9497**」 | **12316** / **12387** |

→ **新增项 D-6（P1，纯文档）**：只改 `dev-plan.md:1211` 这一句的四个数字，**不得改组头**（组头正确）。若按 R-DOC-3 的「组头之和 == 逐行之和 == `wc -l`」直接做断言，须写成 `35 == 35 == (41-6)`、`12316 == 12316 == (12387-71)`，否则断言本身会因把 `__init__.py` 算漏而误报红。

### 8.5 对 PM 两条「既有事实」的架构侧回应

1. **`SEARCH_INDEX_WINDOW = 8000`（`storage/repo.py:17`）升为配置键 `search.index_window`** —— 我**不建议**在本轮做。理由：① 会牵动 `SPEC` / `DEFAULT_TOML` / `example.toml` / 附录 A 四处键集（64→65），直接命中 R-DOC-1；② 该常量是 CH-471（STOR-8）刻意收敛的「三处 `[:8000]` 魔法数」单一口径，把它开放为可调键等于把口径控制权交回用户，与收敛意图相反；③ 仓库已有 `search(full=True)`（`repo.py:333-340`）覆盖窗口外尾部，功能面不缺。建议 P2 登记。
2. **`watcher` 已提交状态纯内存（`security/watcher.py:42-47`）** —— 与本报告 P-4 是同一处。补充一条给实现方的约束：若将来做持久化，**不得**与 CH-464 的「同内容只报一次稳定」语义（`watcher.py:6-16` 有该轮完整动机）打架——持久化的是「已处理指纹」而非「稳定性窗口计时」。当前归 P2 只登记。

### 8.6 测试文件命名建议

同意 PM 的 `tests/test_ch491_doc_sync.py` 命名，并补充：R-DOC-1 / R-DOC-3 / D-1 / D-2 / D-3 / D-4 / D-5 / D-6 属同一族（**文档↔代码静态对账**），建议与本报告 T3 提的两条红线（错误码引用、AST 零引用）**合并进同一文件**，避免 `tests/` 里再散出第二个对账文件。断言写法沿用 `tests/test_ch477_version_sync.py` 的「锚点失效先报红」。
