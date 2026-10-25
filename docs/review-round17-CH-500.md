# 第十七轮全面审查报告（CH-500）

- **审查基线**：`main` @ `3b3e35e`（v2.17.0 / DB_VERSION 10 / PLAN 3.33；第十六轮 CH-499 收口后）
- **审查范围**：架构设计与模块边界 / GUI 与 API 层 / 代码实现与性能 / 文档一致性，四层并行只读审查 + 本机黑盒探针（QA 第三轮 P01–P16）
- **决策原则**：功能完整、性能优先；本机可自动验证者即修即验并补红线，需本机以外系统环境、外部服务接入或人工测试者**仅登记 B 类不实施**
- **出口版本**：`PIPELINE_VERSION` 2.17.0→**2.18.0**（交付 meta 摘除 `shard`、外链链接文字补转义、DOCX 行字号改加权众数——三项均改变产物字节）、`PLAN_VERSION` 3.33→**3.34**、`SCHEMA_VERSION` 14、`DB_VERSION` 10 不动
- **出口取证**：本机（macOS arm64 / Python 3.15，`.venv`）gate **580 项全绿 / 0 skipped（44.6 s；文档全改完后终轮复跑 `/tmp/qa-r4/gate_final.xml` 38.155 s，末两轮 `/tmp/qa-r4/gate_v4.xml` 37.716 s、`/tmp/qa-r4/gate_v5.xml` 38.137 s——本报告 §五/§六 与 dev-plan 的 50/18/17/5 口径、O-h 双写全部落地后复跑，项数与通过态四点一致不变）**、全量 **1390 收集 = 1382 passed / 0 failed / 8 skipped（95.6 s；终轮 `/tmp/qa-r4/full_final.xml` 85.489 s、末两轮 `/tmp/qa-r4/full_v4.xml` 86.011 s、`/tmp/qa-r4/full_v5.xml` 86.597 s，逐条计数相同）**（8 条 skip = 2 条既有 Windows junction 平台门 + 6 条难度语料骨架的 TODO-skip/xfail，`corpus` 标记不进 gate，登记 B-46；同轮追加 `tests/qa/` 评估 harness 前的 1349 项口径为 91.3 s）、语料 harness **22/22**、难度语料对账 **4/7**（`tests/corpus/run_hard.py`：D01/D04/D06/D07 达成，D02→B-43、D05→B-44、D08→B-45）、覆盖率 **45 模块 / 7797 stmts / 740 miss = 精确 90.51%（`coverage report` 打出 91%）**（分母 + 精确值 + 打出值三写，见 六、6）、`ruff check doc2md tests config ops` 全绿、`node --check` rc=0、`doctor` 退 0、三套探针 **49/49 · 32/32 · 30/30**。**测量终止声明**（O-h 规则的自我适用边界）：本行的 `gate_v5` / `full_v5` 是**最后一次代码改动之后**的取证；此后对本文与 dev-plan / README 的追加只写测量记录本身、不触碰任何断言，因此不再往里追加新的采样 hop。收口后的引用性追加（本轮为附录 G 资产段补记 + `.gitignore` 两条忽略项）由文档红线子集（`test_ch491_doc_sync` / `test_ch477_version_sync` / `test_ch498_gate_budget` / `test_ch481_capture_clip`，22 项全过）与门禁复跑当次确认：`/tmp/qa-r4/gate_v8.xml` **580 / 0 / 0 / 0（37.890 s）** 与其后的 `gate_v9.xml`（50 项对账恒等式补写后复跑）**580 / 0 / 0 / 0（37.891 s）**，与上列各 hop 项数、通过态一致——这类追加不改变任何断言，只登记在**本声明内**，不再进入上面的取证行
- **本轮新增红线**：`tests/test_ch500_round17.py` **38 项**（全部 `pytest -m gate`），逐条对应下表「落点」列

> **发现计数**（收口重点，逐条与本文各表行同源）：架构层 12（AR-1…AR-12）；GUI/API 层 11（R17-API-1…8 + AG-1…3）；管线/性能层 10（R17-PIPE-1…6 + PERF-1…4）；SQL/服务/打包层 4（R17-SQL-1 / R17-SVC-1…2 / R17-PKG-1）；文档层漂移 13 处。**编号合计 50 项**。
>
> **处置**：**独立实施 18 项**（本机可验证、逐条附红线）——架构 6（AR-1/2/4/7/8/9）+ GUI/API 8（R17-API-1…8）+ 管线 3（R17-PIPE-1/3/6）+ 打包 1（R17-PKG-1）；**同题交叉引用 6 行**（R17-PIPE-2≡AR-4、R17-PIPE-4≡AR-9、R17-PIPE-5≡AR-7、R17-SQL-1≡AR-2、R17-SVC-1≡AR-1、R17-SVC-2≡AR-8，只记落点不重复计实施）；**B 类登记 17 项**（B-30…B-46，其中 10 项由本轮四层发现引出：AR-3/6/10/11/12 + AG-1/2/3 + PERF-2/3；B-40/B-41 为受限环境专项；B-42…B-46 由 QA 第三轮引出——B-42 来自 X-14 复核，B-43/B-44/B-45/B-46 来自难度语料的**独立真值腿** `tests/corpus/run_hard.py`，见 五、）；**实测结论无动作 2**（PERF-1 排序依据 / PERF-4 未见退化）；**撤销 5**（AR-5 与 AR-4 同题 + 4 处文档漂移复核后前提不成立）。文档层 13 处 = **8 改正 / 4 撤销 / 1 收口动作**。**与 50 项编号的对账恒等式**（收口自检）：18 实施 + 6 交叉引用 + 10 B 类（占编号者，即 AR-3/6/10/11/12 + AG-1/2/3 + PERF-2/3）+ 2 无动作 + 5 撤销（含文档层 4 处）+ 8 文档改正 + 1 收口动作 = **50**；B 类登记总数 **17** 中有 **7 项不占本轮编号**（B-40/B-41 受限环境专项 + B-42…B-46 QA 第三轮引出），故「18+6+17+2+5」这种写法**不等于 50**，属 O-h 型口径移植缺陷，已在 dev-plan 两处同步改为上面的恒等式。
>
> R17-API-7 / R17-API-8 与 AG-3 是本轮**收口阶段追加**（QA 第三轮探针跑出的真实缺口：`has_more` 无游标、`/search` 翻页死胡同、表格单元格转义口径不对称），故与本轮**初次登记的快照口径**（当时记 46 项发现 / 19 项实施 / 8 项 B 类）不同。该快照是收口前中途状态，**不与 50 项构成加法关系**（46 + 这 3 项 = 49，差额无从复算），因此本文与 dev-plan / README 一律只引用**最终口径 50 = 18 + 6 + 10 + 2 + 5 + 8 + 1**（上面的恒等式），快照数字仅保留在本段作为病史，不得再被下游引用——这正是 O-h「显示值 / 未复核结论被继承」的第三种形态：**中途快照被当作终值**。
>
> **发布判定：本轮出口为「阻断」，不是「可发布」**。门禁 580 项、全量 1390 项、核心语料 22/22 三条腿全绿，但难度语料的独立真值腿 **4 通过 / 2 失败 / 1 缺口**，两条失败（**B-43** 跨页表整列脱落 + 裂表、**B-44** 目录页误判为表 + 塌词）都是 `ir.warnings` **零告警**的静默结构损伤，直接违背 `W1045`「表格降维必须入账」与文本保真承诺，按「宁可出红、不可无声」的既有纪律定档为**契约缺陷（失败）而非缺口**——分档判据只看「行为是否与既有承诺冲突」，不看修复成本、也不看是否需要外部人工对照；后者只决定**路径**，不决定**档位**。把这两条降回「缺口」能让收口数字变好看，但那是把判定层重新拆掉，本轮明确拒绝。

---

## 一、架构层（模块划分与职责边界）

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **AR-1** | HIGH | 事件的双写面（SQLite `events.code` 与 `logs/app.jsonl` 的 `"code"`）**不是同一真源**：`events.emit` 写日志用 `{"code": code, ..., **payload}`，而 `api.py` 四处拒绝路径的 payload 本身就带 `"code"`（那是业务错误码，进 `detail` 用）。同一条事件在库里是 `job_rejected`、在日志里是 `E4091`——按日志做告警规则的人永远匹配不到事件名 | 保留键权威：事件名占日志行的 `code`，payload 自带的业务码换名落 `biz_code` 同行保留（只后置不换名的话，按错误码 grep 日志那条路就断了）；入库面口径不变 | `doc2md/events.py:49-64`；红线 `test_ch500_round17.py::test_emit_reserved_keys_win_over_payload` |
| **AR-4** | HIGH | `s4_render.assert_render_contract`（CH-417 立的渲染红线）**自落地起没有任何生产调用方**——只被测试与 `pipeline/__init__` 导出引用。红线只能证明「测试里的输出干净」，不能证明「交付的产物干净」，于是 R17-PIPE-1 那类漏网分支可以静默出门 | 把「判什么」拆成 `_contract_violation()`，生产路径 `render(contract=True)` **逐块**执行；整篇口径保留给测试。逐块而非整篇是必要的：代码块正文是源字面量（`~~`、`<script>`、`](#…` 在示例代码里合法且必须原样保留），整篇扫描必然误杀正常转换——这正是当年接不上生产的原因 | `doc2md/pipeline/s4_render.py:109-137/215-249`；红线 3 项（守卫真的有牙齿 / 代码块豁免 / `preserve_strike` opt-in 不误判） |
| **AR-7** | MED | `_strip_page_furniture` 重建 `RawPage` 只搬 `index/lines/height/size_hist`，`rotation` / `rtl_ratio` 静默归零。与 CH-493（P-2）修 `size_hist` 前是同一种失效形态 | 重建时携带两字段。实测当前唯一读取方（`convert.py` 的 W1046）用的是**剔除前**的 `pages_all`，故线上未露馅（P12 探针：旋转页 / RTL 页的 W1046 正常交付）——属潜伏项，按同一纪律一并收口 | `doc2md/pipeline/s3_structure.py:350-359`；红线 `test_strip_furniture_keeps_layout_probe_fields` |
| **AR-8** | MED | `worker.run_work` 的 `try` 起点在主循环之前，启动段（`reclaim_stale` / 配置读 / `RotatingJsonlWriter` 构造 / `WORK_START` 写盘）全在保护之外。B-17 那类「新库启动即锁冲突」抛出时 `store.close()` 不执行（连接与 WAL 句柄悬着），常驻进程的死法在日志里只剩一个 `WORK_START`，`drained` 无从判定 | `try` 上移到 `Store` 建成那一刻；`writer`/`total` 改为可空并在 `finally` 判空收尾 | `doc2md/worker.py:708-792`；红线 2 项（启动失败仍关连接 / 正常退出 `WORK_START`+`WORK_STOP` 成对） |
| **AR-9** | MED | `IR.meta()` 把 `extra_meta` **平铺**进交付 meta，而 `extra_meta["shard"]` 是 convert 逐片写的**片级内部锚**（`sharding.stitch_shards` 靠它对齐片界）。缝合后交付的是 0 号片的页范围（P05 实测 `{"index":0,"start":0,"end":1}`，同篇 3 片）——既不完整也不正确，却出现在 `ir.json` 与 `/docs/<id>` 响应里供用户解读 | 缝合消费完毕后一律 `pop`；写入侧不动。文档级 `shard_count` / `sharded` 保留 | `doc2md/pipeline/convert.py:215-222`；红线 `test_shard_internal_anchor_absent_from_delivered_meta`（含 zip 内 `ir.json` 实测） |
| **AR-2** | MED | 仓储层空心：`storage/db.py` 只是连接与迁移层，SQL 直接散在约 12 个模块（api / worker / cron / scheduler / edits / cli / ops…）。本轮把两处**会坏**的收进 `repo`：状态 GROUP BY 在 api/worker/cron 复制三份 → `task_status_counts()`；`status IN {ACTIVE}` 靠 Python 元组 repr 拼进 SQL → `ACTIVE_SQL` 参数化（元组是模块常量故今天无注入面，但一旦有人把状态名改成带引号/空格的值，拼出来就是语法错误甚至半条语句） | 已实施（见上）；**B-30 登记**：其余散装 SQL 的仓储化属大面积机械改动，收益是可读性而非缺陷修复，须单独一轮 | `doc2md/storage/repo.py:274-285`、`doc2md/scheduler.py:22/93/114`；红线 `test_active_sql_predicate_is_placeholder_list` + `test_submit_still_detects_active_job_via_bound_params` |
| **AR-3** | MED | 三个单文件承载多职责：`s1_parse.py` 1398 行（magic / PDF / DOCX / legacy / 几何 / 转义预置）、`cli.py` 1351 行、`api.py` 1194 行。职责边界靠注释与函数名维持，没有编译期边界 | **B-31 登记**：拆分是纯机械大改，触及本轮「避免破坏性重构」约束；建议按「S1 按格式拆三模块」优先，且必须在一次产物字节冻结（黄金全绿）的独立轮次里做 | — |
| **AR-6** | MED | 默认档位下 `s1_5.reject_on_any_bad_page` **不生效**：`GateCollector.finish()` 只在 `mode != "auto"` 分支读它（`s1_5_gate.py:236-253`），而默认 `mode = auto`。方案 892 行却写成「`auto` 模式下任一采样页不达标即拒收的总开关」——这是 CH-424 全量判定改造留下的口径分裂：auto 用「连续坏页成段 / 坏页占比」替代了「任一」，开关本身没人读了 | 本轮**只改正文档口径**（auto 的拒收判据是 run/ratio/all_pages 三条，`reject_on_any` 仅对 even5/legacy3 生效）。让 auto 尊重该键等于把默认档位改成「单页坏即整篇拒」，是会被大面积误拒的契约变更 | **B-32 登记**（需真实语料库的误拒率评估 + 人工确认，属外部语料依赖）；文档改正见 四、 |
| **AR-10** | HIGH（功能缺口） | DOCX 的**页眉页脚从未被解析**：P06 探针在 `word/document.xml` 的 `w:headerReference` 文档上构造页眉/页脚文本，产物 `content.md` 命中 0 次——不是被过滤，是根本没进 S1。方案与 README 均未声明支持，用户按「Word 里能看见的都在 MD 里」理解就会丢内容 | **B-33 登记**：提取页眉页脚会直接改变产物字节且需要新的降维口径（重复页眉该并入还是逐页出现），设计上应先立 `W1047` 告警码 + 一条 ADR，再实施；本轮只在文档面写明「页眉页脚不支持」 |
| **AR-11** | LOW | `doc2md/qa_eval.py`（`char_f1` / `heading_pr` / `table_col_consistency`）被 `tests/test_qa_n03_ideal.py` 以 46 项断言使用，但**没有指标报告出口**：语料 harness（`tests/corpus/run_corpus.py`）只把 markdown 正文与 `expected/*.md`（管线重烤基线）做字节比对，质量数字从不上报、无趋势视图 | **B-34 登记（本轮收口更正定级）**：原判「缺 ground truth，须等 B-35」经复核**不成立**——真值已存在（见 AR-12 更正），缺的只是 runner/report 出口，属**本机可自证**项；顺序改为 OPT-11a（runner）→ OPT-11b（真实 PDF 标注） | 与 AR-12 同批 |
| **AR-12** | MED（度量面，**原判 HIGH 已更正**） | 原判「标题识别无独立真源、PDF 侧 `heading_pr` 缺失、两侧真值都由同一套 S3 规则生成的 `expected/*.md` 反推——自证循环」。**收口对账翻案**：`tests/corpus/ideal/` 有 21 份不经管线的标注（DOCX 11 份 / 111 标题，`make_ideal_docx.py` 用 python-docx 直读源 XML `Heading N`/`Title`；PDF 10 份 / 102 标题，`make_ideal_pdf.py` 用 pdfplumber 逐行字号 + `make_corpus.py` 生成器字号约定），并由 `tests/test_qa_n03_ideal.py` 钉住「标注==源重生成」与「S3 F1==1.0」（`pdf_10` 要求 pred 空集；`pdf_06` 无文本层故无标注）。**真实残余只有两条**：① 产品侧 `Result.metrics.heading_pr` 的 ref 取 S1 自己的 `Line.level_hint`（`pipeline/convert.py:388-398`），只证明样式直通，PDF 侧按设计不产出（`convert.py:551` 明示「缺真值显示 —，不是满分」）；② ideal 真值来自合成语料的生成器约定，**语料之外的真实 PDF 无标注**，启发式在野外文档上的准确率仍不可证 | **B-35 重新定界**：需要的是「10–20 篇真实世界 PDF 的人工逐页标题标注」，不再是「10 篇语料 PDF 的标注」；`heading_pr` 的 ref 改走 `ideal/` 归 OPT-11a。翻案过程记为 QA 三轮 `docs/qa/round3/comparison.md` 观察项 **O-e**（前提未回文件复核即继承），`docs/dev-plan.md` 的 B-34/B-35 行同批更正 |
| **AR-5** | — | （与 AR-4 同题，撤销独立编号） | 撤销 | — |

---

## 二、GUI 与 API 层

### 2.1 已实施

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **R17-API-1** | HIGH | `body()` 用 `request.data` 判「有没有正文」，而 Werkzeug 对**表单型 Content-Type** 已把正文消费进 `request.form` → `request.data` 恒空。P03/P16 实测：`PUT /settings` 带 `api.bind=…` 的 urlencoded 请求回 **200** 且落盘 `{}`；`POST /tokens` 带 `role=admin` **静默降级成 reader 票**。CH-495（R14-API-2）想根治的两个「看似成功」形态原样复活 | 先认 Content-Type：`not request.is_json` 且（有 `content_length` 或原始体）→ 400/E4001；真·空体仍按缺省 `{}` | `doc2md/api.py:251-276`；红线 3 项 |
| **R17-API-2** | HIGH | 整数查询参数没有值域：`?since=1e20` 型超大整数原样进 SQLite 绑定 → `OverflowError` → **500 / E5000（HTML）**。实测三处同形态（`/events?since`、`/audit/deletions?offset`、`/docs/<id>/audit?offset`） | `_int_arg` 钉在 `±(2^63-1)`（SQLite INTEGER 域），越界 400/E4001 | `doc2md/api.py:79/295-312`；红线参数化 3 项 |
| **R17-API-3** | MED | 附录 C 写「响应错误一律走 `{code,message,detail}` 契约，5xx 同样」，但 `_unhandled` **刻意透传**带 int `.code` 的 HTTPException → `PUT /healthz` 实测回 `405 text/html`。前端 `api()` 对非 2xx 读 `j.code`，HTML 里取不到，只能显示「请求失败」 | 显式注册 `@app.errorhandler(werkzeug HTTPException)`：状态码原样保留，正文换契约；**不新增错误码**（附录 D 段位冻结、`CODE_INDEX` 是 QA 覆盖率分母），沿用 CH-495 F5 先例——复用 `E4001` + `detail.reason`/`detail.status`/`detail.allowed`。404/413 由更具体的处理器优先命中 | `doc2md/api.py:334-356`；红线 2 项（405 契约 / 404·413 不被抢） |
| **R17-API-4** | MED | 500 排障证据丢失：`RotatingJsonlWriter.write()` 不 flush（只在阈值轮转时随 `check()` 落盘），而 500 是低频事件。P15 实测连续三次 500 后 `logs/app.jsonl` 字节数**不变**，三行 `E_UNHANDLED` 直到下一个无关请求触发 flush 才出现 | 写完立即 `logw().flush()`（与同口径的 `events.emit` 一致） | `doc2md/api.py:371-377`；红线 `test_unhandled_error_line_is_flushed_immediately`（不追发第二个请求即断言落盘） |
| **R17-API-5** | MED | `/jobs` 只回 `tasks`（已被 `limit` 截断）+ `counts`，调用方无法判定「还有没有下一页」——GUI 队列面板把截断当全集显示；`/docs/<id>/audit` 有 `total` 却缺 `has_more` | `/jobs` 补 `total`（过滤后全集条数，由 `task_status_counts` 派生）与 `has_more`；audit 补 `has_more`。**排序一律不动**：`editor.html:370` 明示「服务端 rowid 升序（最旧在前）」并据此挑可回滚版本，翻 DESC 是把可视契约改坏。`limit=0` 按 D9 既有口径 `has_more=False`（空前缀没有下一页），三处端点统一 | `doc2md/api.py:741-749/928-933/953-957`；红线 2 项 |
| **R17-API-6** | MED | `GET /docs` 在附录 C 与 12.x 一直是契约面（QA 第二轮 D9 的收口口径就是按它做的），实测 URL 表里**根本没有这条路由**：`repo.list_docs`（分页信封、每行 blocks 计数、`created_at DESC, doc_id` 稳定排序）只有 CLI 两个消费者，HTTP 面缺位，GUI 只能退而用 `GET /jobs?limit=50` 猜「有哪些文档」 | 挂上出口，复用既有被测函数：不新增查询、不新增 SQL、窗口走统一 `_window`（`_MAX_ROWS` 硬顶、`limit=0` 取空集）、支持 `?status=` | `doc2md/api.py:753-768`；红线 `test_docs_route_exposes_list_docs_envelope` |
| **R17-API-7** | MED | `/search` 的 `has_more=True` 是**死胡同**：`repo.search()` 只收 `limit`（探测多取一行算 `has_more`），既没有 `offset`，`_search()` 也只解析 `limit`——响应明说「还有更多」，调用方却无任何游标可取，第 21 条命中永远拿不到（QA 三轮 V-API-13c 实测）。附带两处同族漂移：`limit=0` 未归入 D9 口径（探测行仍取 `LIMIT 1` → 空前缀反报 `has_more=True`），三条查询里两条**无 ORDER BY**、FTS 侧只按 `bm25`（同分命中先后随 SQLite 计划变）→ 翻页必然重叠/漏行。信封也不回显 `offset`/`limit`，客户端无法自证窗口 | `search()` 增 `offset` 参数、三条查询统一 `LIMIT ? OFFSET ?`，FTS 追加 `doc_id, block_id` 决胜位、非 FTS 两条显式 `ORDER BY rowid`；`limit=0` 时 `probe=0`（D9 同口径）；`_search()` 改走 `_window(default_limit=20)`（非数字/负值/超 int64 → 400/E4001、`_MAX_ROWS` 硬顶）；信封回显 `offset`/`limit`；CLI `doc2md search --offset N`、GUI 检索面板加「更多命中」按钮（`has_more` 从此可点） | `doc2md/storage/repo.py:357-440`、`doc2md/api.py:962-976`、`doc2md/cli.py`、`doc2md/static/editor.html:87/248/469/497`；红线 5 项（翻页不重叠不漏 / `limit=0` 口径 / full 档稳定序 / HTTP 信封契约 / GUI 出口存在） |
| **R17-API-8** | MED | `/jobs` 同族缺口（R17-API-5 补了 `total`/`has_more` 却没补游标）：只有 `limit`、没有 `offset`，第 51 条作业永久不可见；`ORDER BY created_at DESC` 无决胜位，同批入队的多行时间戳相同 → 翻页重复或漏行；响应不回显 `offset`/`limit`，GUI 队列面板也无「窗口截断」提示，`has_more` 到 UI 就断信 | 改走 `_window(default_limit=50)`（负值/非数字/超 int64 → 400/E4001、`_MAX_ROWS` 硬顶），排序补 `job_id` 决胜位，信封补 `offset`/`limit` 回显；GUI 在 `has_more` 时明示「仅显示最近 N 条（共 M 条）」 | `doc2md/api.py:715-749`、`doc2md/static/editor.html`（`loadQueue`）；红线 2 项（`test_jobs_window_pages_with_offset` / `test_queue_panel_shows_window_truncation`） |

### 2.2 GUI 与登记项

| 编号 | 严重度 | 问题 | 处置 |
| --- | --- | --- | --- |
| **AG-1** | MED | GUI 文档清单的数据源是作业表（`editor.html:216` 用 `GET /jobs?limit=50`），不是文档表：一篇文档转换失败或队列截断时，「有哪些文档」就少一项——语义错配（作业列表 ≠ 文档列表）。本轮已把 `GET /docs` 补上，切换本身只剩机械改动 | **B-36 登记**：改 `editor.html` 属 UI 行为变更，需本机浏览器验收（人工测试面），按受限纪律不实施。建议顺序：B-36 → B-37 |
| **AG-2** | LOW | `editor.html` 的审计面板依赖 `rowid` 升序挑「可回滚版本」，而 audit 端点没有 `has_more` 时无法判定流水是否被窗口截断——超过 `_MAX_ROWS` 的历史会静默只显示最旧一批 | R17-API-5 已补 `has_more`；GUI 侧的「还有更多历史」提示归入 B-36 同批改 |
| **AG-3** | MED | PDF 表格单元格的转义口径不对称：DOCX 单元格在 S1 预转义 `&`/`<`/`>`（`s1_parse.py:1078-1079`），S3 的 `cell_md` 转义 `<` 但**不转义 `&`**（`s3_structure.py`）——同一篇 PDF 正文里的 `&` 走 `_escape_text`、表格里的 `&` 不走。P09/复核项 C 实测确认 | **B-37 登记**：改任一侧都动产物字节且要重烤黄金；建议统一为「单元格与正文同一转义源」，与 AR-10 的页眉页脚一并做，避免两次字节重烤 |

---

## 三、管线、性能与实现

### 3.1 已实施

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **R17-PIPE-1** | HIGH | 外链渲染在 `_inline()` 里**提前返回**，绕过了散文分支的 `_escape_text`：链接文字里的 `<script>`、`*`、`~`、换行原样进产物（P10/复核项 D 的姊妹形态）。链接文字是**源文档字面量**，与 `_link_dest` 已做的 URL 消毒不对称——目标消毒了，标签没消毒 | `_link_label()` 改「换行/回车压平 + `_escape_inline`」，与散文同口径 | `doc2md/pipeline/s4_render.py:51-62`（`_link_label`）；红线 2 项（转义 / 换行压平） |
| **R17-PIPE-2** | HIGH | 同 AR-4（渲染红线无生产调用方） | 同 AR-4 | `doc2md/pipeline/s4_render.py` |
| **R17-PIPE-3** | MED | `sniff()` 的 ZIP 分支用裸 `except Exception: pass` 吞掉 `BadZipFile`：截断/损坏的 .docx（下载不完整是最常见成因）与「xlsx/epub 改名成 .docx」拿到**逐字节同文**的 `unsupported magic`，`detail` 还回显 PK magic——「类型不支持」把「文件坏了」彻底盖住，用户只能重命名重试 | 三种结局分别可判：`corrupt_zip`（BadZipFile）/ `zip_unreadable`（OSError）/ `not_a_docx`（缺 `word/document.xml`），消息各说各事；E4151 段位与码不变（不新增码，`detail.reason` 精确化） | `doc2md/pipeline/s1_parse.py:152-180`；红线 3 项 |
| **R17-PIPE-4** | MED | 同 AR-9（`shard` 内部锚进交付面） | 同 AR-9 | `doc2md/pipeline/convert.py` |
| **R17-PIPE-5** | LOW | 同 AR-7 | 同 AR-7 | `doc2md/pipeline/s3_structure.py` |
| **R17-PIPE-6** | HIGH | DOCX 行字号取「**最后一个**带显式字号的 run」，于是段内 run 的**排列顺序**决定结果。P11 实测同一对 run 换序：22pt 在前记 10.0、10pt 在前记 22.0，短 pair 里后者直接被判成 `heading level 1`——「这行正文有多大」应当只由正文内容决定，却由 XML 子节点顺序决定。字号往下游喂 `_heading_level` / `modal_size`，是标题识别的真缺陷 | 改**可见字符加权的众数**（`sizes[pt] += max(1, len(text.strip()))`，取权重最大者；无显式字号的 run 不投票，保持继承行为），与 PDF 侧 `_line_flags` 的众数口径同构 | `doc2md/pipeline/s1_parse.py:901-925`；红线 2 项（换序一致 / 空 run 不投票） |
| **R17-SQL-1** | MED | 同 AR-2（`ACTIVE` 元组 repr 拼进 SQL + 状态 GROUP BY 三处复制） | 同 AR-2，且 `worker.queue_stats` / `cron._daily_report` / `api` 三处改为共用 `task_status_counts` | `doc2md/{scheduler,worker,cron,api}.py`、`doc2md/storage/repo.py:274-285` |
| **R17-SVC-1** | HIGH | 同 AR-1（`events.emit` 的 `{..., **payload}` 让 payload 的业务错误码顶掉事件名） | 同 AR-1；日志行的 `code` 归事件名、业务码换名 `biz_code` | `doc2md/events.py:49-64`；红线 `test_emit_reserved_keys_win_over_payload` |
| **R17-SVC-2** | MED | 同 AR-8（`run_work` 的 `try` 起点在主循环之后，启动段抛出时 `store.close()` 不执行） | 同 AR-8；`try` 上移到 `Store` 建成处，`writer`/`total` 可空并在 `finally` 判空 | `doc2md/worker.py:708-792`；红线 2 项 |
| **R17-PKG-1** | MED | `python -m doc2md` 报「No module named doc2md.__main__」——`pyproject` 只声明了 `console_scripts`，包入口缺失，`PYTHONPATH` 场景与 venv 未装脚本的用户拿不到 CLI | 新增 `doc2md/__main__.py`（`from .cli import main` + `SystemExit(main())`） | `doc2md/__main__.py`；红线 `test_python_m_doc2md_runs` |

### 3.2 性能实测（PERF）

**口径**：本机 macOS arm64 / Python 3.15、10 逻辑 CPU；两轮独立取证——审阅轮脚本（本目录既有记录）与 QA 第三轮脚本 `/tmp/qa-r3/verify/probe_parallel.py` / `probe_prof.py` / `rss_probe.log`（原始输出 `/tmp/qa-r3/verify/perf_probe.log`）；计时取多次中位。QA 三轮的探针自带一处缺陷：阶段行的 `_tail` 字段是错误的 monotonic 基线差值（−277390 s 量级，无意义），故本轮只采用 `s1`/`s1_5`/`s3`/`s4`/`package` 五个具名阶段——它们相加 10.702 s 与独立测量的整篇中位 10.707 s 相符，口径可用；QA 三轮未单列 S2 阶段。

| 编号 | 发现 | 实测数据 | 处置 |
| --- | --- | --- | --- |
| **PERF-1** | 一次 320 页转换的耗时几乎全压在 S1 字符提取，其后各阶段是噪声级 | 审阅轮：S1 `read_pdf` ≈**12.7 s**（占单篇 ≈97%），S1.5 0.028 / S2 0.148 / S3 0.189 / S4 0.280 s。QA 三轮复证：整篇中位 10.707 s，S1 **10.43 s = 97.4%**，S1.5 0.024 / S3 0.11 / S4 0.055 / 打包 0.083 s。档位墙钟（三次中位）：1 页 PDF 0.158 s / 40 页 0.368 s / 320 页 9.998 s / 30 节 DOCX 0.130 s；峰值 RSS 59.7 / 83.0 / 236.8 / 46.6 MB，8 页复杂 PDF 64.6 MB。结论：**优化 S2–S4 任何一处都不构成性能改进**，唯一有效杠杆在 S1 与并行度 | 作为优化方案的排序依据（见 QA 三轮 `optimization-plan.md` P1） |
| **PERF-2** | 页区间并行是明确可得的加速 | 同一 320 页文档按页区间切分：审阅轮 1 段 12.68 s / 2 段 7.89 s（**1.61×**）/ 3 段 4.55 s（**2.79×**）；QA 三轮独立复证 1 段 10.04 s / 2 段 5.65 s（**1.78×**）/ 3 段 3.80 s（**2.64×**）。两轮三档的 `lines=15613` / `pages=320` 完全一致，且各档内层耗时相加 ≈ 单段墙钟（10.04 / 10.51 / 10.35 s）——即加速来自并行展开，不是少算了活 | **B-38 登记**：并行档位改动产物前，必须先证「分段解析结果与整篇解析逐字节等价」（含跨页折行、`modal_size` 全文档众数、门禁全量判定三处跨段依赖）。本机可做等价性证明但实施属契约级改动，须与 `PIPELINE_VERSION` 升位同批 |
| **PERF-3** | `read_pdf` 内部按对象展开的开销无界（pdfplumber 逐对象构造 `LTChar`） | 320 页 / 60 万字量级下峰值 RSS 236.8 MB，对 40 页档（83.0 MB）与 1 页档（59.7 MB）近线性；`_page_chars` 构造是主要分配源 | **B-39 登记**：绕过 pdfplumber 的 per-object 展开、直接用 pdfminer `CTFont` 度量层取文本，收益大但等价面极宽（旋转、连字、CJK 宽度、`/ToUnicode`），需逐文档比对，属独立轮次 |
| **PERF-4** | `/docs/<doc_id>` 与 `/jobs` 的重响应已有窗口，`_MAX_ROWS` 硬顶一致 | QA 三轮实测同一篇 320 页文档：`?limit=500` 响应 324,038 B / 0.045 s，`?limit=20` 响应 16,940 B / 0.002 s（体积 **19.1×**、耗时 22×）——窗口确实在生效 | 既有 CH-469（V-5）改造有效，无需动作；但 R17-API-7 / R17-API-8 证明「有窗口却没有游标」是同族缺口，翻页面已同批收口 |

---

## 四、文档一致性（代码 ↔ 文档）

实测漂移 13 处（收口复核对账后为 **8 处改正 / 4 处撤销 / 1 处收口动作**），本轮处理如下：

- **已改正（同批落文档，8 处）**：
  - `docs/dev-plan.md:892` —— `s1_5.reject_on_any_bad_page` 的作用域按 AR-6 改为「仅 `even5` / `legacy3` 生效；`auto` 的拒收判据是连续坏页成段、坏页占比、全页空三条」。
  - `docs/dev-plan.md:278`（技术栈表的前端行）—— 原写「进度靠 `GET /docs` + 每块 revision 比对」，实测审阅页走的是 `GET /jobs`（队列面板）+ `GET /docs/<id>`（正文窗口），`GET /docs` 当时**尚不存在**（R17-API-6 本轮才补上路由）。已按实况改写该行，并把「审阅页改用 `GET /docs`」登记为 B-36。
  - `docs/dev-plan.md:287` —— 「4 项直接依赖」实际为 **5**（werkzeug 自 CH-496 起是直接依赖，用于 `make_server`）。
  - `docs/dev-plan.md:1450` —— B-3 已由 CH-499 闭合，仍列为 open。
  - `docs/dev-plan.md:1036` —— `DB_VERSION = 9` 实为 **10**，锚点行号由 `db.py:22` 漂到 `:23`。
  - `docs/dev-plan.md:966` —— `doctor` 的「35+4=39」项数口径与实测 **40/5** 不符。
  - `docs/dev-plan.md:1451 / :869 / :1452` —— SPEC 条目数一处写 64、两处写 66。
  - 附录 B/C 的行号锚点漂移（**收口时逐条重新实测**，初次登记的换算值同样不实，已按最终值校正）：`api.py:825/844/955` → `:916/:940/:970`（audit `_window` / deletions `_window` / `/search` 的 `_window(default_limit=20)`，R17-API-7 后 `/search` 不再走裸 `_int_arg`）、`api.py:76-77` → `:88-89`、`api.py:439-441` → `:398-400`、`api.py:1109-1110` → `:1119-1120`、`api.py:949` → `:959`；`repo.py:306` → `:357`；`cli.py` 的 `_apply_format` → `:538`、doctor 的 `legacy_doc_backend` → `:1277`；`convert.py:131-132` → `:153-154`、`s1_parse.py:1037/1048` → `:1377/1388`；另收口时新抓三处并同批改正：`config/loader.py:32/:166` → `:36/:173`（`FROZEN_KEYS` 与 `_enum` 校验）、`worker.py:427` 的 `WHERE status='running'` → `worker.py:571` + `scheduler.py:161` 的 `status NOT IN ('done','rejected')`（原行的谓词也不实）、`loader.py:188` → `:190`。
- **收口阶段追加改正 3 处**（随 R17-API-7 / R17-API-8 同批，不计入上表的 13 处）：
  - 附录 C 的 `GET /jobs` 行 —— 原只写 `?limit=`，现补 `?status=&limit=&offset=`、默认 50、`_MAX_ROWS` 硬顶、`created_at DESC, job_id` 决胜位与 D9 的 `limit=0` 口径；`GET /search` 行同理补 `?offset=`、`bm25, doc_id, block_id` 决胜位与信封回显。
  - 附录 B 的 `doc2md search` 行与 README 的检索段 —— 补 `--offset N`（CH-500 / R17-API-7），CLI 面与 HTTP 面重新同源。
  - **端点数口径对账**：文档「21 个端点」与 `url_map` 的「19 条 unique path」曾被判为漂移（探针初版按 unique path 计数即报 FAIL）。实测两者不矛盾：Flask 对同路径不同方法各注册一条 MapRule（`/jobs` GET+POST、`/docs/<id>` GET+DELETE、`/settings` GET+PUT），`url_map` 共 **22 条 rule-method 行**，减去 `/static/<path:filename>` 恰为 **21** = 附录 C 的行数。探针口径已改为**逐条集合相等**（V-API-18 现比对附录 C 解析集与 `url_map` 集），不再靠单个数字自证。
- **撤销 4 处**（复核后前提不成立）：「`GET /docs/<id>` 不带参数取全部」（CH-495 起 `_MAX_ROWS` 硬顶，代码注释已自证，本轮只补注释）、「`ir.warnings` 可能重复写入」（无活路径，仅卫生说明）、「audit 应改 DESC」（与 GUI 历史面板契约冲突，判定为不改）、**「`docs/dev-plan.md:1398` 的空文本 `PATCH` 期望需随 R17-API-1 改」——本行初次登记时误判**：该处是 `edits.py` 的 `空文本 PATCH → E4001（CH-471 / STOR-6）`，判定发生在 `body()` 的 Content-Type 层之上、与 R17-API-1 无耦合，空文本仍走 E4001，文档与代码当场一致，非漂移。
- **收口动作 1 处**：`README` 与附录 G / G.6 的历史口径对照串按本轮实测追加一档（gate **580** / 全量 **1390 = 1382 passed / 8 skipped** / 语料 **22/22** + 难度语料 **4/7**；附录 G 组头与逐行和、采集方法段相加串同批重算为 G.1 **4393** / G.2 **4955** / G.3 **1312** / G.4 **2567** / G.5 **2061** = **15288**，加六个门面 72 行 = 整包 **15360**；`tests/` 顶层 **80 个 `.py` / 24356 行**，子目录另有 15 个 `.py` / 4261 行），并新增 `doc2md/__main__.py` 与 `test_ch500_round17.py` 两行清单条目、把 B-30…B-41 十二项镜像进 12.7 的 B 表。**收口复跑再追加两处**：① `tests/qa/*`（度量层 / 评测器 / 生成器）与 `tests/corpus/*`（含 `run_hard.py` 与 `hard/` 难度资产）共 15 个子目录文件首次进 附录 G.6 清单，并改正该段遗留的「子目录另有 7 个 / 25750 行」旧串；② 12.7 的 B 表补 **B-42…B-46** 五项——其中 B-42 是第三轮 QA 文档（`comparison.md` N3 / `optimization-plan.md` OPT-4）已引用却从未在方案里登记过的悬空编号，本轮补登使两套编号对账一致。

---

## 五、B 类登记（本轮不实施）

| 编号 | 事项 | 受限原因 | 验证方式 | 建议顺序 |
| --- | --- | --- | --- | --- |
| **B-30** | 散装 SQL 仓储化（约 12 个模块） | 大面积机械改动，收益是可读性而非缺陷修复；与「避免破坏性重构」冲突 | 每模块一组 repo 层单测 + 全量套件字节面（黄金 7 项）全绿 | 低 |
| **B-31** | `s1_parse.py` / `cli.py` / `api.py` 三巨头拆分 | 同上；且拆分时机必须与产物字节冻结同批 | 拆分前后黄金逐字节一致 | 低 |
| **B-32** | `auto` 档尊重 `reject_on_any_bad_page` | 会改变默认档的拒收行为，需真实语料库的误拒率评估（本机语料仅 22 篇，样本不足以判断线上误杀面） | 外部语料 + 人工抽检拒收清单 | 中（文档口径已改，键的存废需在 CH-501 裁定） |
| **B-33** | DOCX 页眉页脚提取 | 功能缺口，需先立 `W1047` 降维告警码与 ADR（并入还是逐页出现是产品决策，非工程决策） | 新增 ADR + 语料重烤 + 人工审阅 11 篇 DOCX 产物 | 高（用户可感知「看不见的内容没了」） |
| **B-34** | `qa_eval.py` 指标报告出口（**本轮更正：不再依赖 B-35**） | 判据已具备（`ideal/` 21 份真值 + `test_qa_n03_ideal.py` 46 项断言），缺的是把指标逐篇汇总上报的 runner；`run_corpus.py` 目前只比 `expected/*.md` 重烤基线 | 本机可自证：新增 runner，逐篇输出对 `ideal/` 的 `char_f1` / `heading_pr` / `table_col`，双口径字段名强制区分（`expected/` 只作漂移监控） | 低（原记「依赖 B-35」已作废） |
| **B-35** | **真实世界** PDF 标题人工真值集（本轮重新定界） | **必须人工逐页标注**：语料之外的 10–20 篇真实 PDF（含多栏 / 扫描件 / 无显式字号层次）。合成语料的 ideal 真值来自生成器字号约定，不能替代野外文档 | 标注后按同格式产出 `*.ideal.json`，在 OPT-11a 的 runner 上读出非合成的 P/R/F1 | 高（是标题启发式优化的前置；不再是 B-34 的前置） |
| **B-36** | GUI 文档清单改用 `GET /docs` | 属 UI 行为变更，需浏览器人工验收（本轮只补了服务端出口） | 本机起 `serve` + 浏览器走一遍列表/筛选/翻页 | 中 |
| **B-37** | 表格单元格 `&` 转义单一来源 | 改任一侧都动产物字节，需黄金重烤与人工比对若干含 `&` 的表格文档 | 语料 22 篇 + 外部含 `&`/`<` 的表格样本 | 中，与 B-33 同批 |
| **B-38** | 页区间并行（实测 2.79× 上限） | 实施前须证分段与整篇逐字节等价（跨页折行 / 全文档 `modal_size` / 门禁全量判定三处跨段依赖） | 等价性证明脚本 + `PIPELINE_VERSION` 升位 + 语料全绿 | **高（本轮性能结论的唯一大杠杆）** |
| **B-39** | 绕过 pdfplumber per-object 展开 | 等价面极宽（旋转 / 连字 / CJK 宽度 / `/ToUnicode`），需逐文档比对 | 320 页金样本 + 22 篇语料逐字节 | 中（收益次于 B-38） |
| **B-12 / B-16 / B-27-remote** | 远端 CI 腿复证（Windows 门禁、30 分钟档长跑、租约并发） | 需本机以外的系统与 CI 环境 | 远端 six-job 全绿 + Windows 腿 0 skipped | 每轮收口例行 |
| **B-40** | 真实 SMB / 网络卷与 Windows junction 复测 | 本机无该文件系统 | 挂载外部卷后跑 `ops` 面 + 既有 junction 两项转绿 | 低 |
| **B-41** | LibreOffice 之外的 legacy `.doc` 解析路径 | 本机只有 LibreOffice 可用，无第二个转换器可交叉验证 | 外部安装另一转换器后比对 | 低 |
| **B-42** | `llm.mode=server` 在无候选块文档上不做任何服务探活（QA 三轮 X-14 复核引出） | `_candidate_indices()` 判无候选 → `payload_batches()` 返回空 → `repair()` 的批循环一次都不执行，`llm.fallback=false` 也回 rc=0，report.html 记 `source=server` / `batches=0`；**E5041 是条件可达**（本机 22 篇语料全部 0 候选，须自建粘连段落文档才触发）。改法要么无候选时跑一次 `llm.probe()`（`/health`）并把结果计入 `llm_stats`，要么把 `source` 区分成 `server` / `server-untouched`——两者都动 `llm_stats`，而它进 report.html，即产物字节变化，需黄金重烤；「无候选算不算服务失败」本身是契约决策 | 起 / 不起 llama-server 两种环境跑同一文档，比对 report.html 的 LLM 表与退出码；语料 22 篇 + 候选文档各一遍 | 中，与 B-33 / B-37 同批重烤 |
| **B-43** | D02 跨页表格整列脱落 + 裂表（**发布阻断**，QA 三轮 HD-1） | 独立真值腿实测：期望 1 张 3 列 × 41 行，实际裂成 2 张（`table[0]` **2 列 × 37 行** + `table[1]` 3 列 × 5 行），`金额` 列整体脱落成正文串 `金额10011002…1036`，且 `ir.warnings` **零告警**。**分档为「失败」而非「缺口」**：`W1045` 已承诺表格降维必须入账，内容损失 + 无声同时违背该承诺与文本保真；「修复需人工逐页对照真 PDF」只约束**路径**、不降级**判定**（判据 `docs/qa/round3/test-plan.md` 2.1）。核心 95 例 / 门禁 580 项 / 全量 1390 项 / 语料 22/22 四条腿对此全绿，仅 `run_hard.py` 腿为红 | 改 S1 行装配（文本流顺序 → y 坐标聚类）后，22 篇语料 + 320 页金样本逐字节复核，列归属需人工对照真实 PDF；`PIPELINE_VERSION` 契约级升位 | **最高（发布阻断，与 B-44 同批）**；根因记 G8 |
| **B-44** | D05 目录点线引导符进单元格 + 目录页误判为表且无降级告警（**发布阻断**，QA 三轮 HD-2） | 实测产物 `| 第一章总则 | .................... | 1 |`：引导符串成单元格内容、整页目录识别成表、run 间空格丢失使 `第一章 总则` 塌成 `第一章总则`（`contains` 三条全缺），同样 `ir.warnings` **零告警**。塌词单独即破坏检索面（搜 `第一章 总则` 搜不到），与 B-43 同为「损失无声」 | 引导符密度 / 页码右对齐 / 行数三阈值需人工在真实目录页上确认可误判面；**必须与 B-43 同批**——D01 要求「无边框也成表」、D05 要求「无边框的目录不成表」，同一几何特征两侧一起调，只修一边会把另一条样本推红。其中「run 间空格保真」一条不依赖人工裁定，可在 CH-501 单独实施（仍随批次重烤） | **最高（发布阻断）**；与 B-43 同批 |
| **B-45** | D08 中文序号层级口径裁定（`第 N 节 …` 该 H1 还是 H2） | **真值侧冲突，不是产品缺陷**：样本以 16pt 大字 + 每页首行造 `第 N 节 …`，`tests/qa/gen_cases.py` 声明期望 **H1**，产品按 `s3_structure.py:1155` 的 CJK 序号映射（`章/编/部/篇→1、节→2、条→3、项→4`）给出 **H2**。D08 的 `judge` 主旨已达成（8 个标题全保留、页眉与孤立页码全剔除）。两种口径各自自洽，且该映射**当前没有任何 gate 红线钉住**，改哪侧都动 `content.md` 字节 | 人工裁定 → 改正一侧 → 补一条「CJK 序号→层级」映射红线（现缺） | 中（人工裁定，不阻断发布） |
| **B-46** | 难度语料入门禁（`tests/corpus/hard/` → `pytest -m gate`） | 前置 B-43 / B-44。当前对账由 `tests/corpus/run_hard.py` 承担（信息层，**不被 pytest 收集**、不进 gate，有缺口退 1），实测 **4 通过 / 2 失败 / 1 缺口**；`tests/qa/test_hard_corpus.py` 骨架把已知缺口记 xfail、把未填断言记 skip——**这不是门禁**，且该骨架只核对 `contains`/`not_contains`、不看 `tables`/`headings`，若被当作门禁会得出「难度语料无结构问题」的假结论（G9 / O-g）。本轮 8 条 skip 中 6 条即来源于此 | 过渡口径：入门禁前 `run_hard.py` 是**收口必跑项**，红也必须显形。出口：B-43/B-44 闭合后把 `expectations.json` 的结构断言逐条落成 `assert`，入 gate 前不得保留 skip 或 xfail；入门禁时同步 `test_ch498_gate_budget.py` 慢项清单 | 高（是 B-43/B-44 的验收面） |

---

## 六、结论与出口

1. **本轮真正的功能缺陷集中在「判定/校验层没有牙齿」**：渲染红线无生产调用方（AR-4/R17-PIPE-2）、事件双写面各说各话（AR-1）、500 日志要等下一个请求才落盘（R17-API-4）、`body()` 把表单正文折叠成 `{}`（R17-API-1）、`sniff` 把「文件坏了」说成「类型不支持」（R17-PIPE-3）。五处的共同形态是：**看起来有防护，实际没人执行那条判定**。修复口径统一为——判定必须长在交付路径上，且失败必须当场可见（明确失败优先于看似成功）。
2. **同族的第二形态是「可观测契约不成套」**：列表端点各写各的窗口（`/jobs`、`/search` 有 `limit` 无 `offset`，`has_more` 成了死信；信封有的回显 `offset`/`limit` 有的不回显；排序普遍缺决胜位）。本轮把五个列表端点统一到 `_window` + D9 口径 + 决胜位 + 信封回显（R17-API-5/6/7/8），并把「`has_more` 不得是死信」写进 12.x 的收口口径。**判定标准**：任何一个 `True` 都必须能被调用方消费掉，否则它和没有等价。
3. **正确性面**两处会改变产物解读结果：DOCX 行字号被 run 顺序左右（R17-PIPE-6，标题级别误判）、片级内部锚冒充文档元数据出门（R17-PIPE-4）。
4. **性能面**给出可执行排序：S1 占单篇 97.4%（两轮独立复证：12.7 s / 10.43 s 量级），S2–S4 是噪声；页区间并行两轮 1.61×–2.79× / 1.78×–2.64× 且行数一致，是唯一大杠杆，但必须先过逐字节等价关（B-38）。
5. **度量面**本轮真正戳破的是**自己的一条未复核前提**（收口对账翻案，见 QA 三轮 O-e）：AR-12 原写「PDF 标题 F1=1.0 是被测规则自证、无独立真值」，回文件核对后不成立——`tests/corpus/ideal/` 早有 21 份不经管线的标注（DOCX 111 + PDF 102 标题）并由 `test_qa_n03_ideal.py` 46 项断言钉住。残余收敛为两条：产品侧 `heading_pr` 的 ref 取 S1 自身 `level_hint`（只证样式直通），以及**语料之外真实 PDF 无标注**。连带改判：B-34 不再依赖 B-35，降级为本机可自证的「指标报告出口」项。
6. **度量面的同形态第二次命中，对象是本轮自己上午写的数字**（QA 三轮 **O-h**）：四处文档把覆盖率记作 `7797 stmts / 740 miss = 90%`，回读产生它的日志并复跑 `coverage report --include="doc2md/*"` 实测打出的是 **91%**（精确 **90.51%**）——轮中另一份 `7800/743` 打出 90%（精确 90.47%），**档位从没变过，错的是把显示整数当事实继承**、跨过了 .5 取整边界。修法不是改数字了事，而是立规则：**任何会被取整的指标（覆盖率 / 通过率 / 占比）引用时须同时给出分母 + 精确值 + 打出值，且每次收口回读产生它的那份日志的 `TOTAL` 行，不得沿用上一轮的显示整数**。这条与 O-e 是同一个病灶（结论未回对象本身复核），只是继承距离从「若干轮」缩到「半天」、继承对象从「别人的结论」换成「自己的输出」。
7. **探针纪律**（QA 三轮的三条误判全部由证据翻案，没有一条靠 skip 消化）：X-14 的「E5041 不触发」是候选集合为空导致的路径未达（真缺陷是条件可达，登记 B-42）；X-11b / X-13 是断言关键词按中文「质量」与 `ir.json` 取样，而产物节名是 `<h2>Quality</h2>` 且 `llm_stats` 按设计不进 `ir.json`；V-API-16 间歇失败是探针取「rowid 首行」当被编辑文档，与重提交目标不同一篇。三条都改成钉死真实口径后全绿，另 V-API-18 从「单个数字」升级为「附录 C 集合 ↔ `url_map` 集合逐条相等」。
8. **分档纪律是本轮最需要留下的一条方法论**：难度语料腿的两条红（B-43 / B-44）在登记时曾被写成「缺口」，理由是「修复需人工逐页对照真 PDF，本机不可自证」。复核后改判为**失败 / 契约缺陷**——分档只由「行为是否与既有承诺冲突」决定，`W1045` 已承诺表格降维必须入账，而两条都是内容损失 + `ir.warnings` 零告警的静默损伤，冲突已经发生；「修复需要什么环境」只约束**路径**，不决定**档位**。与之配对的是 G9/O-g：**一条腿的真值若来自产物自身的基线，它就看不见结构性损伤**——`tests/qa/test_hard_corpus.py` 骨架对 D02 只给出一条「TODO 未填断言」的 skip，同一时间 `run_hard.py` 报的是「3 列变 2 列、41 行变 37 行」；覆盖数（1390 项）与发现能力（0 项看见 D02）并不相关。
9. 出口取证见文首；受限项全部登记于 五、，其中 **B-43 / B-44 为发布阻断**（需人工逐页对照真实 PDF 定列归属与目录页阈值，且必须同批改，见 六、8），**B-35（需对语料之外的真实 PDF 人工标注）、B-33/B-36/B-45/B-42（需产品决策、人工裁定或浏览器验收）、B-12/B-16/B-27-remote/B-40/B-41（需外部环境）** 需人工介入或外部环境确认；**B-46 是本轮新登记五项里唯一可本机自证的**（把 `expectations.json` 的结构断言逐条落成 `assert` 入门禁），但须排在 B-43/B-44 之后，否则只是把两条已知红固化成常红灯；**B-34 经本轮更正后移出受限清单**（判据已在，与 B-46 同属 CH-501 可本机自证项）。下一条自 **CH-501** 起。
