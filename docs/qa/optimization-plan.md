# 优化方案（按优先级）

- 文档编号：QA-D2M-2026-10-21-O
- 输入：《偏差归纳与根因》（`deviation-analysis.md`）根因 R-1~R-6；《对照表》（`comparison.md`）C-01~C-36
- 约束（本项目不可破的纪律，所有方案在此约束内设计）：
  1. **G6 字节等价**：任何改动若影响现有产物字节，必须连带全语料 + 黄金样本重烤并人工审阅；
  2. **NFR-1 离线单机**：不引入出网依赖、不新增运行时依赖（除非明确登记）；
  3. **登记流**：契约变更 / 缺陷 / 更优做法写入方案第十一章（下一条自 CH-495），版本号按规递增；
  4. **红线先行**：每项改进先立可测断言（含「恒真断言」防护），再改实现。

---

## P0（先做：低成本、判据性质、防复发）

### O-01 折行空白保真（治 C-04 / R-5）

| 项 | 内容 |
| --- | --- |
| 调整策略 | S1 在 `_line_from_raw` 增加行尾/行首空白几何证据：若页 chars 中存在落在该行纵向带内、`x0` 大于行尾字符 `x1` 的空白字符 → `Line.trailing_space=True`（行首同理）；S3 `_join_line` 对 CJK↔拉丁/数字边界在任一侧 `trailing/leading_space` 时补一个空格 |
| 涉及模块 | `pipeline/s1_parse.py`（Line 增字段 + 几何判定）、`pipeline/s3_structure.py`（`_join_line`） |
| 预期效果 | 折线落在中西文空格上的保真丢失归零；不影响 CJK-CJK、拉丁-拉丁既有口径 |
| 回归验证 | ① 新增语料样本（`make_corpus.py` 增加 wrap-at-space 定向样本，wrap 宽度精确切在空格上）；② 既有 21 份语料 + 黄金样本逐字节重烤（预期仅新样本变化）；③ gate 增 1 条红线：`数字 2026` 类样本 22/22 保留空格 |
| 风险与取舍 | 改 `_join_line` 是全局热路径，已有大量字节级断言——必须全量重烤；几何判定的误判面（装饰性空白字符）需用「空白字符宽度 ≈ 空格宽」收口；若重烤后发现既有语料大面积漂移，说明 wrap 边界空格比预期常见，需评估是否默认开启 |

### O-02 测试环境中立化（治 C-34 / R-6）

| 项 | 内容 |
| --- | --- |
| 调整策略 | `tests/conftest.py` 增 autouse 夹具：① 清理 `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy/ALL_PROXY`（保存-清除-恢复），回环测试流量永不进程代跳；② 探测沙箱能力（`mkdir` EEXIST 语义、进程树 RSS 采样）并在受限时把相应用例转 skip 且 skip 原因写入报告，而非 fail |
| 涉及模块 | `tests/conftest.py`（新增）、`tests/test_t2_llm_server.py`、`tests/test_t6_corpus_bench.py`、`tests/test_ch476_fixes.py` |
| 预期效果 | 本机/沙箱与 CI 口径一致；9 项环境失败归零或转明确 skip；每次环境差异归因成本从「小时级」降为「零」 |
| 回归验证 | ① 代理变量在位时 T2 4 项转绿；② 沙箱内 T6 3 项转 skip 且摘要可见；③ ch476 2 项在干净/脏临时根两种状态下都绿；④ gate 增 2 条：夹具存在性 + 代理变量清除断言 |
| 风险与取舍 | 清代理变量可能影响「确需走代理」的未来用例——用 fixture 显式 opt-in（`@pytest.mark.usefixtures("keep_proxy")` 反向标记）；沙箱探测本身要防「探测行为被沙箱记日志」噪声，用一次性 module 级探测 |

### O-03 批量/串行结果行同构（治 C-21）

| 项 | 内容 |
| --- | --- |
| 调整策略 | 批量工人进程与串行路径共用同一个结果行组装函数（收敛到 `cli.py` 或 `pipeline/batch.py` 的单点），`sharded` 恒为布尔 |
| 涉及模块 | `doc2md/cli.py`、`doc2md/pipeline/batch.py` |
| 预期效果 | 两种出口字段逐字节同构；下游脚本可统一解析 |
| 回归验证 | gate 增 1 条：同输入批量与串行结果行 JSON 键集与取值类型一致（值可比对） |
| 风险与取舍 | 纯一致性修复，低风险；注意批量行含 workers/退出码聚合等独有字段，同构指共有字段 |

## P1（再做：质量判据与表达力，需语料重烤）

### O-04 紧凑列表渲染（治 C-05 / R-2）

| 项 | 内容 |
| --- | --- |
| 调整策略 | 两案择一（建议 A）：<br>**A. 生成侧间距感知**：S4 `render()` 跟踪上一块类型，连续 `list_item` 间用 `"\n"` 连接；块内多行 list_item（折行并回）不受影响。<br>B. 模型侧列表容器：IR 增 `list` 容器块（改动大，牵涉 IR schema 与 DB，不建议本轮） |
| 涉及模块 | `pipeline/s4_render.py`（A 案仅此一处） |
| 预期效果 | 列表产物与手写 Markdown 一致；Obsidian/VS Code 等渲染为 tight list |
| 回归验证 | ① docx_02 / pdf_02 / pdf_09 语料重烤 + 人工审阅（预期字节变化仅列表区）；② gate 红线：产物中不得出现 `\n\n- ` 连续空行-列表模式；③ 既有六条 gate 复跑 |
| 风险与取舍 | 源文档中「确有间行」的松散列表会变紧凑——Markdown 语境下紧凑是主流期待，可接受；若未来需要保留松散语义，再走 B 案 |

### O-05 单元格多段落 `<br>` + 合并单元格诚实标记（治 C-06/07/29 / R-3）

| 项 | 内容 |
| --- | --- |
| 调整策略 | ① `_docx_cell_text` 段落边界由单空格改 `<br>`（GFM 单元格内合法换行）；② `_gfm_table` 不再依赖调用方预拼，统一处理转义；③ 命中拍平/跨片降级时 `report.html` 顶部显式提示行 + `ir.warnings` 计数透出到 `/docs/<id>` 响应（F-11 的「诚实暴露」路线，不做自动合并） |
| 涉及模块 | `pipeline/s1_parse.py`（`_docx_cell_text`）、`pipeline/s3_structure.py`（`_gfm_table`）、`pipeline/convert.py`（`render_report`）、`doc2md/api.py`（warnings 透出） |
| 预期效果 | 单元格段落边界可辨；降级场景用户可见（不再静默） |
| 回归验证 | ① docx_11 语料重烤 + 人工审阅（单元格区字节变化）；② gate：报告含提示 iff `ir.warnings` 非空（双向断言，防恒真）；③ 常规语料报告零提示（防噪音红线） |
| 风险与取舍 | `<br>` 会让部分渲染器显示 literal——GFM 规范内行为，主流渲染器均支持；提示行属报告层，不动 content.md 字节 |

### O-06 质量指标量化 + 语句覆盖率口径落地（治 C-31 / R-4）

| 项 | 内容 |
| --- | --- |
| 调整策略 | ① `_metrics()` 增结构质量指标：标题 P/R（对照 DOCX `Heading N` 样式为 ground truth，PDF 侧用编号连续性代理）、表格列一致率、字符级 F1（对照 expected 基线时同时输出「对基线」与「对理想标注」两个口径）；② 把 coverage.py 纳入 dev 依赖并在 CI full-suite 出语句覆盖报告（阈值登记，不设红线）；③ 启动 B-13/B-15 语料的人工理想标注（标题 ground truth 优先），标注完成的样本进 `tests/corpus/ideal/` |
| 涉及模块 | `pipeline/convert.py`（metrics）、`pyproject.toml`（dev extra）、`.github/workflows/`（报告步骤）、`tests/corpus/`（ideal 目录） |
| 预期效果 | 「转换变好/变坏」可度量；expected 基线自啮合问题（C-31）从根上拆解 |
| 回归验证 | gate 红线：指标计算对空 IR/单块 IR 不崩（边界）；新指标不得低于当前基线（数值红线，随语料标注推进滚动更新） |
| 风险与取舍 | 人工标注是持续成本（本轮只登记方法，标注分批推进）；F1 对折行空格类偏差敏感——正好与 O-01 形成度量闭环；指标阈值设红线会逼出「为指标优化」，先只报告不阻断 |

### O-07 CLI `docs list/show/export`（治 C-26 / F-01 收口）

| 项 | 内容 |
| --- | --- |
| 调整策略 | 新增三个只读子命令，复用 `storage/repo` 的 `list_blocks/get_doc/search` 与 zip 读路径；全部出口走 `_jline()`；`docs export --all --out DIR` 带 `manifest.json` 对账映射 |
| 涉及模块 | `doc2md/cli.py`（COMMANDS + 三个 cmd_*）、`doc2md/storage/repo.py`（只读复用） |
| 预期效果 | 不起 serve 即可查/导已转内容；与 `search` 形成完整 CLI 读取面 |
| 回归验证 | ① `docs list --json` 条数 == `SELECT COUNT(*) FROM docs`；② `search -q X` 与 API 同参逐条相同（已有红线的扩展）；③ `docs export <id>` 与 zip content.md 逐字节相同；④ 恶意 doc_id/文件名走同一消毒闸；⑤ gate 增 3 条 |
| 风险与取舍 | 只读命令零 DB 写入风险；导出目录的文件名冲突用 `-<doc_id 前 8>` 后缀（F-14 既定口径）；需同步附录 B/README，与 D-07/D-08 同批修避免二次漂移 |

### O-08 增量跳过（治 C-27 / F-07）

| 项 | 内容 |
| --- | --- |
| 调整策略 | ① `convert --skip-existing`：按 `doc_id` 命中库行即跳过（结果行 `status:"skipped"`）；② watch 的「已提交且未变」判据落库面（复用 `tasks.source_path + source_key`，不新增表）或 `--state-file`；③ 文档化「重复转换仍走 E4091 兜底」的现状边界 |
| 涉及模块 | `doc2md/cli.py`、`doc2md/pipeline/batch.py`、`doc2md/security/watcher.py` |
| 预期效果 | 同目录二转零重复转换（耗时 < 首次 20%）；watch 进程重启不重转 |
| 回归验证 | ① 同目录连转两次：第二次 N 条 skipped、退出码 0、产物 mtime/sha 不变；② watch 重启后对未变更目录零转换（先立反向断言锁住现状再改）；③ gate 增 3 条 |
| 风险与取舍 | 大目录（>1000 文件）实测收益需真机（B-x 登记项）；`--skip-existing` 与「源文件已变但 doc_id 未变」不可能（doc_id=内容哈希），与「源文件未变但期望重转」的用户需用 `--force` 或不用该开关 |

## P2（择机：表达力上限与环境面）

### O-09 页眉变体剔除增强（治 C-02）

| 项 | 内容 |
| --- | --- |
| 调整策略 | `_furniture_key` 归一化时把「第 N 页第 M 栏」类逐页数字也抹成 `#` 再比重复度；或增「页眉带 + 逐页同形不同值」模式识别。宁漏勿误：只对边距带内、字号不大于正文的行生效 |
| 涉及模块 | `pipeline/s3_structure.py`（`_furniture_key`/`_strip_page_furniture`） |
| 预期效果 | pdf_10 类栏标题零入正文 |
| 回归验证 | pdf_10 语料重烤；gate：既有 pdf_09/10 页眉页码剔除断言扩展为「含栏标题变体」 |
| 风险与取舍 | 归一化过宽会把「第 1 章 / 第 2 章」式真标题当页眉删掉——用「仅在边距带 + 非粗体 + 短行」三重约束收口；收益低（合成语料特征），排 P2 |

### O-10 RTL/竖排/旋转页探测 + 非目标声明（治 C-28 / F-12）

| 项 | 内容 |
| --- | --- |
| 调整策略 | S1 探测页 `Rotation % 180 != 0` 或 RTL 字符占比 > 50% → 写 `ir.warnings`（新码按附录 D 段位纪律登记）；方案 1.2 非目标表显式补三行 |
| 涉及模块 | `pipeline/s1_parse.py`、`docs/dev-plan.md`（1.2 非目标）、`doc2md/errors.py`（如增 W 码） |
| 预期效果 | 日文竖排/旋转页输入有明确提示而非静默错乱 |
| 回归验证 | 合成旋转 90° PDF → warnings 非空且转换不失败；常规语料零新增 warning（G6） |
| 风险与取舍 | 探测阈值（50%）需真实语料校准（§5-9 外部依赖）；只探测不修复，修复属另一个量级的工作 |

### O-11 行内 marks 行级化（治 C-30 / R-2，大改）

| 项 | 内容 |
| --- | --- |
| 调整策略 | IR 增行内 span 模型（`Block.spans: [{start,end,marks}]`），S1 对 DOCX run 级格式 / PDF char 级格式生成 span，S4 渲染 span。需先写 ADR（与既有的「宁漏勿误」纪律显式对谈），并评估 DB `blocks` 表迁移 |
| 涉及模块 | `doc2md/ir/model.py`、`doc2md/pipeline/s1_parse.py`、`doc2md/pipeline/s3_structure.py`、`doc2md/pipeline/s4_render.py`、`doc2md/storage/db.py`（schema 迁移）、`doc2md/edits.py`（编辑面兼容） |
| 预期效果 | 行内加粗/斜体/删除线保留，技术文档保真度显著提升 |
| 回归验证 | 新语料（run 级格式样本）；黄金样本全量重烤；SCHEMA_VERSION 递增 + 迁移测试；gate 增「span 渲染不越界/不重叠」红线 |
| 风险与取舍 | 触及 IR 契约与 DB schema，是本清单最大改动——必须 ADR + 分阶段（先 DOCX run 级，PDF char 级第二步）；与 G6 的冲突最大，重烤成本最高；若不做，现状（块级 uniform）是可接受的保守站位 |

### O-12 legacy `.doc` 内置解析评估（治 C-35，可选）

| 项 | 内容 |
| --- | --- |
| 调整策略 | 评估引入纯 Python OLE/Word二进制 文本抽取器（如 `antiword`/`wvware` 的 Python 绑定或自研最小解析）作为 LibreOffice 不可用时的回退后端；或维持现状并在 README 明示依赖 |
| 涉及模块 | `pipeline/legacy_doc.py`、README |
| 预期效果 | 无 Office 环境的机器也能转 `.doc`（或至少错误信息更明确） |
| 回归验证 | 无 LibreOffice 环境构造（CI 加 `backend=none` 场景已有）；新后端样本比对 |
| 风险与取舍 | 自研二进制格式解析是长期负担；`antiword` 许可与维护状态需先核；建议先只做文档明示，解析器另立 spike |

---

## 实施顺序与依赖

```
O-02（环境中立化，零产品风险）
  └─ O-01 ─┐
O-03      ─┤（均只动连接层/一致性，可并行）
           │
O-04（列表紧凑）── 依赖语料重烤流程（O-01 已建立）
O-05（单元格 br + 诚实标记）── 可与 O-04 同批重烤
O-07（CLI 读取面）── 独立，文档同批
O-06（质量指标）── 建议在 O-01/O-04/O-05 之后：有理想标注才能度量
O-08（增量跳过）── 独立
O-09/O-10/O-12 ── 择机
O-11（行内 marks）── 最后，ADR 先行
```

## 全局回归基座（每项必跑）

1. `pytest -m gate`（红线层，439 项，全绿）；
2. `pytest tests/`（全量，1058 项）；
3. `python tests/corpus/run_corpus.py`（21 份）+ 受影响样本人工审阅 diff；
4. `python tests/make_golden.py --force` 前后 sha256 全表比对（黄金样本）；
5. `doc2md doctor` 退 0；
6. 涉及产物字节的改动：CI push 面五作业 + 周期面三档复跑（现行纪律）。

## 预期总体效果

- 保真度：折行空格丢失归零（O-01）、单元格段落边界可辨（O-05）——直接消除本轮全部「逻辑错误 + 结构差异」类中影响项；
- 判据：紧凑列表（O-04）+ 质量指标与理想标注（O-06）—— 转换质量从「红绿二值」进到「可度量」；
- 体验：CLI 读取面收口（O-07）+ 增量跳过（O-08）——补齐 CH-491 登记的 P0/P1 缺口主体；
- 工程：测试环境中立化（O-02）——本机/沙箱与 CI 口径一致，回归判定不再被环境噪声稀释。
