# 优化实施效果报告（O-01 ~ O-12）

日期：2026-10-22 ｜ 依据：`docs/qa/optimization-plan.md`（QA-D2M-2026-10-21）
复测口径：与第一轮 QA 相同——语料比对（22 样本）、全量 pytest、gate 红线层、
性能计时、黄金样本逐字节复核。

## 1. 实施总览

| 项 | 内容 | 状态 | 关键证据 |
| --- | --- | --- | --- |
| O-01 | 折行空格几何证据（S1 `_edge_spaces` + S3 六拼接入口 space 穿线） | ✅ | pdf_09 唯一变化 `数字2026`→`数字 2026`；新增 pdf_11 定向样本（A–F 六形态）；单测 14 项 |
| O-02 | 测试环境中立化（conftest 三件套） | ✅ | 跨平台临时目录/编码隔离；gate 钉住 |
| O-03 | 串行/批量结果行同构（`batch.result_row` 单点） | ✅ | 10 共有字段逐键一致；同构 gate + 进程池出口断言 |
| O-04 | tight list（连续列表项单换行） | ✅ | 6 份语料重烤（纯空行删除）；gate：列表项间空行零命中 |
| O-05 | 单元格 `<br>` 段落边界 + W1045 拍平诚实暴露 | ✅ | docx_03/10/11 重烤；报告顶部提示 iff warnings 非空（双向 gate）；API `/docs/<id>` 透出 |
| O-07 | CLI `docs list/show/export` | ✅ | 专项 17 项 + gate 3 条；list↔COUNT、export↔zip 逐字节、消毒闸红线 |
| O-08 | `--skip-existing` 增量跳过 + watch 重启零重转 | ✅ | 专项 12 项 + gate 3 条；源改写不误跳；产物 sha 不变 |
| O-06 | 质量指标量化（`qa_eval.py`：char_f1 / heading_pr / table_col） | ✅ | 空 IR/单块 IR 边界 gate；metrics 不进 front-matter（字节零影响）；coverage 进 CI（信息面） |
| O-09 | 页眉变体剔除（`第 N 页第 M 栏` 归一化） | ✅ | pdf_10 六页栏标题全部剔除、零其他漂移；真标题量词（章/节）不折算 |
| O-10 | 旋转页 / RTL 探测（W1046 + `layout_probe` 明细） | ✅ | 合成旋转 90°/270° PDF 告警不失败；常规语料零新增告警；非目标表补 3 行 |
| O-11 | 行内 marks 行级化 | 📋 ADR | `docs/adr-o11-inline-marks.md`：接受原则、分阶段推迟，触发条件 T1/T2/T3 |
| O-12 | legacy `.doc` 内置解析 | 📋 文档明示 | README 补 E4151 依赖说明；解析器另立 spike（按方案建议） |

## 2. 复测结果（同口径）

| 口径 | 第一轮基线 | 本轮 | 判定 |
| --- | --- | --- | --- |
| 语料比对（tests/corpus） | 22 样本 | **22/22 PASS** | ✅（pdf_09/10、6 列表、3 docx 表格基线重烤，diff 逐行审阅均为预期修复） |
| 全量 pytest | 1072（第一轮）/ 1076（O-04 后） | **1140 passed + 2 skipped**（1142 collected） | ✅ |
| gate 红线层 | 153（R7 后 244） | **449 passed**（含本轮新增 27 条） | ✅ |
| 黄金样本逐字节 | 6 样本 sha 绑定 | `test_golden_repro` 全绿（DOCX zip `create_system` 冻结修复后跨 OS 稳定） | ✅ |
| ruff | 全绿 | 全绿 | ✅ |
| 性能计时 | — | 见 §3 | — |
| CLI/API 契约 | — | docs list↔SQL COUNT、export↔zip 逐字节、search↔API 同参逐条相同（既有红线扩展） | ✅ |

## 3. 性能计时（本机 macOS arm64 / Python 3.15）

- 单样本转换（pdf_09 8 页，3 次取最小）：**66ms**——O-01 的空白证据提取
  在 `_slice_chars` 同带口径内完成，无可测回退。
- `--skip-existing` 二转（3 文件目录，落库口径）：skipped=3/3、rc=0、
  产物 sha 不变；耗时占比 80%（小样本）/ 74%（40p PDF + 30 节 DOCX）。
  **解读**：跳过路径的剩余耗时是进程启动 + 配置装载 + 逐文件哈希（各
  数十 ms 级），样本越重占比越低；方案「< 首次 20%」的预设环境是低配机
  （单文件转换秒级），本机（M 系）转换太快使固定开销占主导。大目录
  （>1000 文件）收益仍需真机登记项（沿 B-x）。
- watch 重启零重转：专项测试钉住（重启判据与 `--skip-existing` 共用实现）。

## 4. 质量提升对照（用户可见面）

| 缺陷 | 修复前 | 修复后 |
| --- | --- | --- |
| C-04 折行空格 | `数字2026`（空格丢失） | `数字 2026`（几何证据补回，宁误勿漏反例钉住） |
| C-05 松散列表 | 列表项间空行（Obsidian 渲染成松散列表） | tight list 单换行 |
| C-06 单元格段界 | 多段单元格粘连 + 拍平静默 | `<br>` 保界 + W1045 + 报告提示 + API 透出 |
| C-02 栏标题页眉 | `第 N 页第 M 栏` 六页全泄漏 | 零泄漏（真标题不误伤） |
| C-26/F-01 CLI 读取面 | 只能起 serve 查库 | `docs list/show/export` + manifest 对账 |
| C-27/F-07 重复转换 | watch 重启全部重转 | 落库面增量跳过（内容寻址，改写不误跳） |
| C-31 自啮合指标 | `f1=chars/len(md)` 无意义 | char_f1 / heading_pr / table_col 三类有参照指标 |
| C-28/F-12 旋转/RTL | 静默错乱 | W1046 + 页号明细 + 非目标声明 |

## 5. 过程中发现并当场修复的问题

1. **pytest 并发互铲临时目录**（setup ERROR 风暴）：conftest 固定 basetemp
   下并发两个 pytest 互删临时目录——纪律：本仓测试必须顺序执行。
2. **zip `create_system` 跨 OS 漂移**：黄金样本哈希绑定在 Unix 上失效——
   `make_golden` 冻结 `create_system=0`。
3. **O-01 探针误判修正**：1.86pt 空白实为页脚 9pt 空格而非 CID 噪声，
   据此重写 `_edge_spaces` 判据（tol 0.5pt + 双界相邻性）。
4. **O-05 测试文件未实跑过**（上轮误报闭环）：plain 样本 7 字符被门禁拒收，
   补足正文后 22/22；`__import__("pathlib")` 规整为正常导入。
5. **O-07 `_clamp_limit` 异常映射错位**：负 limit 由 E4001 改显式 None
   哨兵 + 统一退 2（与 argparse 用法错一致）。

## 6. 下一轮改进方案（本轮实测新登记）

> **2026-10-22 追记**：N-1 / N-3 / N-6 已在同日实施完毕（见 §8）；
> N-2 / N-4 已在第二批实施完毕（见 §9，含附带发现 `--out` 回灌修复）；
> N-5 / N-7 维持原判（依赖真实语料 / 条件触发）。

| 编号 | 发现 | 建议 | 优先级 |
| --- | --- | --- | --- |
| N-1 | `--skip-existing` 对 CLI 纯产物流（不落库）无收益：二转 skipped=0 | 产物面回退判据（`out/<doc_id>.zip` 存在即跳）前置依赖是产物记录 `pipeline_version`（当前 front-matter 只有 `schema_version`，管线语义升级无法判别陈旧产物）——先补 front-matter 版本字段（一次性全量重烤），再上 zip 判据 | P2 ✅ 已实施 |
| N-2 | CLI 每次运行 600–800ms 固定开销（cfg/store/哈希），小文件批量占比高 | 大目录收益随 N 线性放大，登记真机批量压测项（并入既有 B-4 并发压测） | P2 ✅ 本机批量已实测（§9）；跨机并入 B-4 |
| N-3 | `heading_pr` 仅 DOCX 有真值，PDF 侧指标缺位 | 推进 `tests/corpus/ideal/` 人工标注（首批 DOCX 标题真值样本），标注完成即解锁 PDF F1 基线 | P1 ✅ DOCX 批次已实施（PDF 待人工标注） |
| N-4 | `_furniture_key` 只覆盖 页/栏 量词；罗马数字页码（Page I/II）、「章节名+页码」同行混合页眉未覆盖 | 真实语料出现再扩展（宁漏勿误：无样本不动判据） | P3 ✅ 已实施（§9，合成样本钉住 + 宁漏勿误负例） |
| N-5 | O-10 阈值（RTL 50%、Rotation）未经真实语料校准 | 日文竖排/阿语文档真实样本到手后校准，误报面未知前保持探测-only | P2 |
| N-6 | `Result.metrics`（含 table_col / heading_pr）不落任何持久面，批量审计要看 stdout | 可选：quality 摘要进 report.html 尾节（报告层，不动 content.md 字节） | P3 ✅ 已实施 |
| N-7 | O-11 触发条件 T2（搭车 bump SCHEMA_VERSION）未出现前 span 模型不动 | 维持 ADR 状态；若下轮出现 IR 契约变更，按 ADR §3 骨架同批实施阶段 1（DOCX run 级） | P2（条件触发） |
| N-8 | ~~S3 无编号短标题漏判~~ **已修复（同日，见 §10）**：根因比登记时更精确——是**同页**续段合并（「代码说明」无句末标点 → `_continues_paragraph` 把下一行正文并进来），且 golden small_3p 页标题 +5.5pt 在 O-09 后已被 furniture 剔除、`+6.0` 阈值的历史顾虑消失 | 修复：unnumbered 阈值 `base+6.0` → `base+3.5`（与 numbered 分支对齐）+ **页标题豁免集**（无编号行数字折叠键跨 ≥2 页边距带重复 → 不抬标题，兜住 badtail「正常页面 N」逐页变体）；版本 2.14.0 → 2.15.0 | PDF F1 基线 0.8/0.6667 → **1.0**；语料重烤 2 份（pdf_04/pdf_07，diff 人工审阅通过）；全量 pytest 绿 |

## 7. 遗留与登记

- 测试执行纪律：本仓固定 basetemp，**pytest 必须顺序单进程**（并发互铲）。
- B-x（真机/外部依赖）项沿既有登记不变：B-4 并发压测、B-6 Win11 spawn、
  B-8 跨机基准等。
- 本轮全部基线重烤（pdf_09/10/11、6 列表、3 docx 表格）diff 均为逐行
  审阅后的预期修复，无夹带漂移；git status 中 expected 变更与 §1 一一对应。

## 8. N 轮实施追记（2026-10-22 同日）

| 项 | 内容 | 关键改动 | 验证 |
| --- | --- | --- | --- |
| N-1 | 产物版本契约 + zip 回退判据 | `PIPELINE_VERSION` 2.12.1→**2.13.0**（契约级 minor，三处同步）；IR 新增 `pipeline_version` 字段（front-matter / ir.json / rebuild 全链路）；`skip_existing_hit` 两层判据：库 → 产物回退（`_zip_front_version` fail-closed） | CLI 纯产物流二转 skipped 0→N（缺口闭合）；陈旧版本 / 无字段 / 损坏 zip 三向不跳；语料 22/22 零漂移（基线不含 front-matter）；+5 专项测试 |
| N-3 | DOCX 标题真值标注落地 | `tests/corpus/make_ideal_docx.py`（python-docx 直读源 XML `Heading N`/`Title`，不经管线）→ 11 份 `ideal/*.ideal.json`；`qa_eval.pred_headings` 助手 | gate 25 项：标注=源样式（防漂移）、S3 标题保真 F1==1.0（真断言，pred 走 S3 启发式 / ref 来自源 XML 两层独立）、全样本覆盖、JSON 契约；PDF 待人工标注 |
| N-6 | quality 摘要进 report.html 尾节 | `render_report`/`package` 增 `metrics` 参数；尾节 `<h2>Quality</h2>`（table_col / heading_pr / 计数）；缺真值显示 —；DB 重建路径整节缺省 | iff metrics 双向断言；front-matter 字节零影响；HTML 转义；无 metrics 不补零 |

复测：全量 **1145→1172 passed**（+27）、2 skipped；语料 22/22；ruff 全绿。
版本同步（CH-477）三处一致 2.13.0。
实施中当场修复：`elapsed_ms` 一度进 Quality 表破坏 report.html 逐条目
字节等价 gate（test_chunking_is_output_equivalent 抓住）——剔除该字段，
耗时只走 stdout/DB 面（与既有「等价比对剥耗时」纪律对齐）。


## 9. N 轮第二批实施追记（2026-10-22）

| 项 | 内容 | 关键改动 | 验证 |
| --- | --- | --- | --- |
| N-4 | 页眉判据扩展：罗马数字页码 + 「章节名 · 页码」混合页眉 | `_PAGENUM_ATOM`（阿拉伯 ∥ 罗马数字 I~XXXIX）贯通 `_PAGE_NUM` / `_PAGE_NUM_INLINE` / `_FURNITURE_PAGE_COL`；`_FURNITURE_TAIL_NUM`（尾随页码片段**只在 key 层**抹平，不进 inline——整行丢弃会误杀章节名；分隔符不含连字符，日期/编号不折 `#`） | 合成样本端到端 11 项（`test_qa_n04_furniture_ext.py`）：三页「第一章 引言 · N」页眉全剔、罗马页眉/页脚全剔；负例钉住：裸 `IV` / `DIV` / `MIX` 不认、`第 IV 章` 不折、`2026-10-22` 连字符形态不折、正文日期行零误伤 |
| N-2 | 本机批量压测（macOS arm64 / Python 3.13，串行 workers=1 口径） | 探针 `tests/n2_probe.py`（200 份合成小 DOCX） | 见下表 |
| 附带 | **`--out` 回灌修复**（N-2 压测当场暴露） | `cmd_convert` 把 `--out` 覆盖回灌 `cfg["paths"]["out"]`——`skip_existing_hit` 的产物回退判据按 cfg paths.out 解析，不回灌则自定义 `--out` 的二转永远 skipped=0（串行与批量 worker 共用 cfg，单点修复） | +2 专项测试（串行 / 进程池两出口）；同内容两文件 → 同 doc_id 一份产物、二转双 skipped |

### N-2 计时结果（200 份小 DOCX，每次 2–3 段正文）

| 口径 | 首转 | 二转（--skip-existing） | 占比 |
| --- | --- | --- | --- |
| 单次 CLI 调用（1 文件，3 次取最小） | 329ms | — | 固定开销基线 |
| 40 文件批量 | 562ms | 302ms（skipped 40/40） | 53.7%（固定开销主导） |
| 200 文件批量 | 1565ms（7.8ms/文件摊薄） | 303ms（skipped 200/200） | **19.4%** |

**解读**：跳过路径的固定开销 ≈ 300ms（40/200 两档二转耗时几乎相同，
即纯进程启动 + 配置装载成本），逐文件边际成本 ≈ 0.2ms；方案「< 首转
20%」的预设口径在 N≈200 处达标（19.4%），批量越大占比越低。既有登记
「600–800ms 固定开销」为落库口径（_ingest + Store），CLI 纯产物流实测
329ms——两者不矛盾，差异在 DB 打开与逐文件哈希面。跨机对比沿 B-4。

版本随判据变更：`PIPELINE_VERSION` 2.13.0 → **2.14.0**（契约级 minor，
三处同步）。N-4 判据对本轮 22 份语料零漂移（无罗马页码 / 混合页眉样本，
负例形态均不受影响），黄金 sha 绑定不受影响（`test_golden_repro` 全绿）。

### N-3 二批：PDF 标题真值落地（同日追加）

`make_ideal_pdf.py` 从源 PDF 字号分布（pdfplumber）按生成器字号约定
程序化导出 10 份 `ideal/pdf_*.ideal.json`（106 个标题真值；pdf_10 空集
=栏头是家具；pdf_06 无文本层不覆盖）——**PDF 侧 heading F1 基线解锁**，
原「待人工标注」登记闭合（程序化口径与 DOCX 批次同构，两层独立）。

实测：9/10 样本 S3 F1 == 1.0（含 40 页 80 标题的 pdf_08 零丢失零改级）；
**暴露并登记 N-8**（见 §6）：pdf_04/pdf_07 第 2 页顶部无编号 14pt 短标题
被跨页续段合并吞掉——这正是「PDF 指标缺位」掩盖至今的盲区，人工审阅
基线当时也是合并态。修复受 golden small_3p 页标题阈值约束，冻结基线
防漂移，留专项。

### B-4 本机并发压测（N-2 补充口径，同日追加）

200 份小 DOCX，workers=1/2/4 首转 + workers=4 二转（探针
`tests/b4_probe.py`）：

| 口径 | 耗时 | 加速比 |
| --- | --- | --- |
| workers=1（串行） | 1396ms | 1.00× |
| workers=2 | 1222ms | 1.14× |
| workers=4 | 1041ms | 1.34× |
| workers=4 + --skip-existing（二转） | 662ms（skipped 200/200） | — |

- **产物等价**：串行 ↔ 并发 200 zip / 600 成员逐字节相等（CH-448「批量
  与串行产物一致」红线本机实测成立）。注意等价口径是**成员字节**：
  zip 容器带写入时间戳，串行↔串行两次运行容器 sha 也必不同。
- **解读**：小文件单件转换仅 ~5.5ms，进程池 spawn（每 worker ~300ms
  固定成本）与 IPC 占主导 → 加速比次线性且随文件体量增长；重文件
  （pdf_08/09 类）才是进程池的主场。跨机/重文件口径仍沿 B-4 原登记。

复测（顺序单进程，basetemp 钉系统 tmp）：全量 **1204 passed + 4 skipped**
（前批 1183 + 本批 21：PDF 标注三组——标注=源一致性 10、F1 冻结基线 10、
覆盖完整性 1）；语料基线零漂移；ruff 全绿。
过程环境注记：本机沙箱对仓库内 `tmp/pytest-basetemp` 的批量清理有
bulk-delete 护栏（≥50 项拦截），全量跑需 `--basetemp=$TMPDIR/d2md-pytest`
（护栏对系统 tmp 放行）；pytest.ini 已带 `-q`，命令行再加 `-q` 会压掉
统计摘要行（`-qq` 语义）。

## 10. N-8 修复实施（2026-10-23）

**根因修正**（相对 §6/§9 登记时的判断）：

1. 合并点是**同页**而非跨页——「代码说明」无句末标点，
   `_continues_paragraph` 的 `not _ends_sentence` 分支把下一行正文
   并进同一段；
2. `base+6.0` 阈值豁免 golden small_3p 页标题的历史顾虑**已消失**——
   O-09 的 `_FURNITURE_PAGE_COL` 把「第 N 页」折 `#` 后，页标题
   （「小文档 #」三页聚合、短键）在 furniture 层已被剔除，到不了
   `_heading_level`。

**修复**（`doc2md/pipeline/s3_structure.py`）：

| 改动 | 内容 |
| --- | --- |
| 阈值下调 | `_heading_level` unnumbered 分支 `base+6.0` → `base+3.5`（与 numbered 分支的字号信号对齐）；+10pt 以上 H1、其余 H2 不变 |
| 页标题豁免集 | analyze 预扫描：无编号行**数字折叠键**（`正常页面 1`→`正常页面 #`）在 ≥2 页边距带内重复 → 主循环不抬标题。兜住 badtail「正常页面 N」逐页变体（裸计数 furniture 键不可折、漏到 S3 的残余风险） |

**影响面**（全语料 + golden 扫描 +3.0~+7.0 区间无编号行）：
sources 仅 pdf_04/pdf_07 两个目标行；golden 侧 badtail_front/scan（豁免
集兜住，基线不变）、links_pdf「目录页」（+5.0 单页 → 新判 H2，非字节
门禁样本，`test_links_pdf_markdown_carries_both_links` 绿）。

**验证**：PDF F1 冻结基线解除（0.8/0.6667 → 1.0，10 样本全绿）；
语料重烤 2 份（diff 人工审阅：标题解救、正文独立成段）；新增 3 项
S3 测试（短大字行升级 / 长行与句末标点负例 / 页标题豁免集）；全量
pytest 绿、golden 全绿、ruff 全绿。版本 `PIPELINE_VERSION` 2.14.0 →
**2.15.0**（判据变更沿 N-4 先例升 minor，三处同步）。
