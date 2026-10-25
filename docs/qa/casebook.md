# Doc2md 测试用例集（自动生成）

> 由 `tests/qa/gen_cases.py` 生成。**难度清单**来自 PDF 转换的公开难度分档，**场景清单**来自日常实际使用；两者交叉处即为必须覆盖的用例。
> 指标口径见 `tests/qa/mdscore.py`，真值口径见 `tests/qa/srcstruct.py`。

## 一、难度清单（D-xx）

| ID | 难度项 | 星级 | 日常频率 | 现有覆盖 | 为什么难 / 边界 |
|---|---|---|---|---|---|
| D01 | 无线框表格 | ★★★★★ | 高 | ❌ **缺口** | PDF 不存『第3行第2列』，只存线条与散落文字；无线框只能靠间隙聚类猜，是最高难度档。现有语料 pdf_03 是有线框，未触达真实难点。 |
| D02 | 跨页表格 | ★★★★ | 中 | ❌ **缺口** | 表头在上页、数据在下页；跨页续表若不强判会裂成两张表或丢表头。 |
| D03 | 合并单元格（DOCX gridSpan/vMerge） | ★★★★ | 高 | 🟡 部分 | GFM 管道表无 colspan 语义；§1.2 非目标 6 明确不还原，但降级必须诚实留痕。 |
| D04 | 断词连字符（de-hyphenation） | ★★ | 中 | ❌ **缺口** | 英文行尾 comput-\ner 需并回 computer，否则正文被切碎。 |
| D05 | 点线引导符 | ★★★ | 中 | ❌ **缺口** | 目录/明细表的 ...... 会被当成真实字符，污染文本与表格单元格。 |
| D06 | 数值形态（千分位 / 括号负数） | ★★★ | 高 | ❌ **缺口** | 『(1,234)』被读成正数、千分位逗号丢失——不改位置只改数值，比串位更难发现，是数字类文档的头号静默错误。 |
| D07 | 加密 PDF | ★ | 低 | ❌ **缺口** | E4151 拒收路径；口令加密与结构损坏必须可区分（detail.password_required）。 |
| D08 | 页眉逐页变体 | ★★ | 高 | 🟡 部分 | O-09 只归一了『第 N 页第 M 栏』；奇偶页眉、章节名页眉仍未覆盖。 |
| D09 | 目录（TOC 域） | ★★ | 中 | 🟡 部分 | TOC 域会把目录文本注入正文；docx_05 有域但未造真 TOC。 |
| D10 | CID 字体无 ToUnicode / Type3 转曲 | ★★★★★ | 低 | ❌ **缺口** | 复制出来是 (cid:142) 或乱码。**本项目不作 OCR**，正确行为是门禁拒收或留 W1046 告警，而非输出乱码——合成成本高，登记为真机样本待办。本机以**代理腿**覆盖检出路径（`proxy: true`），真实字体样本仍登记待办（B-47）。 |
| D11 | 双栏阅读顺序 | ★★★ | 高 | ✅ 已覆盖 | pdf_10 已覆盖；朴素提取会得到『左一句右一句』乒乓错乱。 |
| D12 | 文本框 / SmartArt（DOCX） | ★★★ | 中 | ✅ 已覆盖 | docx_06 已覆盖；文本框内容极易丢或顺序错乱。 |

## 二、使用场景（S-xx）

| ID | 场景 | 画像 | 关注点 | 关键指标 |
|---|---|---|---|---|
| S1 | 合同 / 协议归档 | `financial` | 条款编号层级、表格金额、跨页续表 | heading_f1, table_teds, order_nid |
| S2 | 财报 / 报表入库 | `financial` | 数值形态、合并单元格、表头重复 | table_teds, text, lint_contract |
| S3 | 技术手册 / 文档站 | `manual` | 标题大纲、代码块语言、内部链接 | heading_f1, structure_bsf, lint_contract, asset |
| S4 | 论文 / 研究报告 | `academic` | 双栏阅读顺序、脚注尾注、跨页表 | order_nid, table_teds, structure_bsf |
| S5 | 批量历史档案转换 | `batch` | 确定性、规范合规、拒收可判定 | idempotent, lint_contract, render, asset |
| S6 | 扫描件 / 图片型 PDF | `batch` | 门禁拒收而非残缺输出 | render, honest |

## 三、用例矩阵（场景 × 难度 → 用例编号 TC-xx）

> 生成规则：场景关注点与难度项相关即产出一条用例；「—」表示该组合不构成有效用例（如扫描件场景不测双栏）。

| 场景 \ 难度 | D01 | D02 | D03 | D04 | D05 | D06 | D07 | D08 | D09 | D10 | D11 | D12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **S1 合同 / 协议归档** | — | — | TC-001 | — | — | — | — | TC-002 | TC-003 | — | — | — |
| **S2 财报 / 报表入库** | TC-004 | TC-005 | TC-006 | — | — | TC-007 | — | — | — | — | — | — |
| **S3 技术手册 / 文档站** | — | — | — | — | TC-008 | — | — | TC-009 | TC-010 | — | — | — |
| **S4 论文 / 研究报告** | — | TC-011 | — | — | — | — | — | — | — | — | TC-012 | — |
| **S5 批量历史档案转换** | — | — | — | — | — | — | TC-013 | — | — | TC-014 | — | — |
| **S6 扫描件 / 图片型 PDF** | — | — | — | — | — | — | — | — | — | TC-015 | — | — |

共生成 **15** 条交叉用例。

## 四、缺口难度的目标声明（expectations.json）

> 由 `gen_sources()` 随样本一同生成，**不经过管线**，是「我按这个意图画的」；与 `srcstruct` 的「我从文件里读出来是这个」互为校验。

### D01 无线框表格（★★★★★）—— `hard_01_noframe_table.pdf`

- 判定口径：列数与单元格文本须与 expect 一致；无线框是本项唯一难点，拿不到 3 列即为缺陷（不是『降级』，是漏检）。
- 目标声明：`{"tables": [{"nrow": 4, "ncol": 3, "header": ["项目", "本期金额", "上期金额"], "body": [["营业收入", "1,234,567", "987,654"], ["营业成本", "765,432", "654,321"], ["净利润", "469,135", "333,333"]]}], "headings": [{"text": "一、无线框表格测试", "level": 1}]}`

### D02 跨页表格（★★★★）—— `hard_02_cross_page_table.pdf`

- 判定口径：跨页续表若裂成两张表（n_pred=2）即为缺陷；表头重复出现是否合并由人工裁决，但**行数不得丢**。
- 目标声明：`{"tables": [{"nrow_min": 41, "ncol": 3, "header": ["序号", "科目", "金额"]}], "headings": [{"text": "二、跨页表格测试", "level": 1}]}`

### D03 合并单元格（DOCX gridSpan/vMerge）（★★★★）—— `hard_03_merged_cells.docx`

- 判定口径：§1.2 非目标 6：不还原 colspan/rowspan。产物必须**要么**保持 4×3 管道表且 4 个 contains 全中，**要么**降级为 HTML 表格并带 W1045；两者都不满足（内容丢失且无告警）即静默降级缺陷。
- 目标声明：`{"headings": [{"text": "三、合并单元格测试", "level": 1}], "tables": [{"nrow": 4, "ncol": 3, "header": ["季度", "收入", "成本"]}], "contains": ["1,234,567 / 987,654", "上半年", "2,000,000", "3,000,000"]}`

### D04 断词连字符（de-hyphenation）（★★）—— `hard_04_hyphenation.pdf`

- 判定口径：产物须含 configuration / parameter / preserved 三个完整词；若出现 'configu- ration' 即为 de-hyphenation 缺失。
- 目标声明：`{"contains": ["configuration", "parameter", "preserved"], "headings": [{"text": "四、断词连字符测试", "level": 1}]}`

### D05 点线引导符（★★★）—— `hard_05_leader_dots.pdf`

- 判定口径：引导符不得成为单元格内容；页码须与条目同行关联。若 TOC 形态被误判为表格，须显式降级告警。
- 目标声明：`{"headings": [{"text": "五、点线引导符测试", "level": 1}], "contains": ["第一章 总则", "第二章 职责", "第三章 附则"]}`

### D06 数值形态（千分位 / 括号负数）（★★★）—— `hard_06_numeric_forms.pdf`

- 判定口径：『(1,234)』**必须**保持括号形态——把它读成 1234 或 -1234 都改变了数值语义，是数字类文档的头号静默错误。千分位逗号与货币符号须保留。
- 目标声明：`{"tables": [{"nrow": 5, "ncol": 3, "header": ["科目", "本期", "上期"], "body": [["营业收入", "1,234,567", "987,654"], ["营业外支出", "(1,234)", "(5,678)"], ["毛利率", "36.5%", "33.8%"], ["资产总额", "￥8,765,432", "￥7,654,321"]]}], "headings": [{"text": "六、数值形态测试", "level": 1}]}`

### D07 加密 PDF（★）—— `hard_07_encrypted.pdf`

- 判定口径：必须拒收且 `detail.password_required` 为真；不得与『结构损坏』混淆，也不得静默产出残缺结果（§1.2 非目标 1）。
- 目标声明：`{"behavior": "reject:E4151"}`

### D08 页眉逐页变体（★★）—— `hard_08_header_variants.pdf`

- 判定口径：8 个章节标题须全部保留；页眉里的『奇数页/偶数页』与孤立页码『- N -』须全部剔除。**误剔除正文是比漏剔除更严重的缺陷**。
- 目标声明：`{"headings": [{"text": "第 1 节 正文章节标题", "level": 1}, {"text": "第 2 节 正文章节标题", "level": 1}, {"text": "第 3 节 正文章节标题", "level": 1}, {"text": "第 4 节 正文章节标题", "level": 1}, {"text": "第 5 节 正文章节标题", "level": 1}, {"text": "第 6 节 正文章节标题", "level": 1}, {"text": "第 7 节 正文章节标题", "level": 1}, {"text": "第 8 节 正文章节标题", "level": 1}], "not_contains": ["奇数页", "偶数页"]}`

### D09 目录（TOC 域）（★★）—— `hard_09_toc_field.docx`

- 判定口径：目录缓存结果不得被当成正文标题重复输出：三个章节标题在产物里**各出现一次**（`no_duplicate_headings`）。若产品选择保留目录文本，必须与真实章节标题可区分（不产生重复锚点）；引导用的制表位与页码不得成为独立段落噪声。
- 目标声明：`{"headings": [{"text": "九、目录域测试", "level": 1}, {"text": "第一章 总则", "level": 1}, {"text": "第二章 职责分工", "level": 1}, {"text": "第三章 附则", "level": 1}], "contains": ["本章规定适用范围与术语定义。"], "no_duplicate_headings": true}`

### D10 CID 字体无 ToUnicode / Type3 转曲（★★★★★）—— `hard_10_cid_proxy.pdf`

- 判定口径：本项目不作 OCR（§1.2 非目标 1），乱码文本层的正确行为是**门禁拒收**或**显式告警**，而不是静默产出乱码。契约本身待裁定（拒收 E4221 还是新增 W 码，B-47 需人拍板）；裁定前本项以 xfail(strict=True) 钉住，不得当作已通过。
- 目标声明：`{"behavior": "reject:E4221"}`

## 五、用例卡（十字段）

> 十字段口径见 `gen_cases.CARD_FIELDS`。**期望结果只来自声明侧**（`expectations.json` / `manifest.json`），实际结果只来自本轮 `work/qa/*.json` 回填并带 `generated_at`；没跑的一律写「未执行」+ 原因。

### TC-001 ｜ S1 合同 / 协议归档 × D03 合并单元格（DOCX gridSpan/vMerge）（★★★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-001 |
| 所属模块 | S1 解析（gridSpan/vMerge）→ S4 渲染（GFM 无 colspan） |
| 测试目标 | §1.2 非目标 6：不还原 colspan/rowspan。产物必须**要么**保持 4×3 管道表且 4 个 con |
| 输入与前置条件 | `tests/corpus/hard/hard_03_merged_cells.docx`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D03`（或 `run_corpus.py --only hard_03_merged_cells`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | §1.2 非目标 6：不还原 colspan/rowspan。产物必须**要么**保持 4×3 管道表且 4 个 contains 全中，**要么**降级为 HTML 表格并带 W1045；两者都不满足（内容丢失且无告警）即静默降级缺陷。｜声明：`{"headings": [{"text": "三、合并单元格测试", "level": 1}], "tables": [{"nrow": 4, "ncol": 3, "header": ["季度", "收入", "成本"]}], "contains": ["1,234,567 / 987,654", "上半年", "2,000,000", "3,000,000"]}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | table_shape_ok, silent_degrade, honest |

### TC-002 ｜ S1 合同 / 协议归档 × D08 页眉逐页变体（★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-002 |
| 所属模块 | S1 解析（页眉页脚）→ S3 结构（误升防护） |
| 测试目标 | 8 个章节标题须全部保留；页眉里的『奇数页/偶数页』与孤立页码『- N -』须全部剔除。**误剔除正文是比漏剔除更严重的 |
| 输入与前置条件 | `tests/corpus/hard/hard_08_header_variants.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D08`（或 `run_corpus.py --only hard_08_header_variants`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 8 个章节标题须全部保留；页眉里的『奇数页/偶数页』与孤立页码『- N -』须全部剔除。**误剔除正文是比漏剔除更严重的缺陷**。｜声明：`{"headings": [{"text": "第 1 节 正文章节标题", "level": 1}, {"text": "第 2 节 正文章节标题", "level": 1}, {"text": "第 3 节 正文章节标题", "level": 1}, {"text": "第 4 节 正文章节标题", "level": 1}, {"text": "第 5 节 正文章节标题", "level": 1}, {"text": "第 6 节 `（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 8 项未达；未达项 [{"check": "heading[第 1 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 2 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 3 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 4 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 5 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 6 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "head；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-45 |
| 优先级 | P1（有偏差但未进优先级表——先查 priorities.json 是否缺输入层） |
| 覆盖指标 | furniture_residue, heading_f1, lint_dialect |

### TC-003 ｜ S1 合同 / 协议归档 × D09 目录（TOC 域）（★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-003 |
| 所属模块 | S1 解析（TOC 域）→ S3 结构（锚点去重） |
| 测试目标 | 目录缓存结果不得被当成正文标题重复输出：三个章节标题在产物里**各出现一次**（`no_duplicate_headin |
| 输入与前置条件 | `tests/corpus/hard/hard_09_toc_field.docx`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D09`（或 `run_corpus.py --only hard_09_toc_field`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 目录缓存结果不得被当成正文标题重复输出：三个章节标题在产物里**各出现一次**（`no_duplicate_headings`）。若产品选择保留目录文本，必须与真实章节标题可区分（不产生重复锚点）；引导用的制表位与页码不得成为独立段落噪声。｜声明：`{"headings": [{"text": "九、目录域测试", "level": 1}, {"text": "第一章 总则", "level": 1}, {"text": "第二章 职责分工", "level": 1}, {"text": "第三章 附则", "level": 1}], "contains": ["本章规定适用范围与术语定义。"], "no_duplicate_headings": true}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | heading_f1, duplicate_anchor, structure_bsf |

### TC-004 ｜ S2 财报 / 报表入库 × D01 无线框表格（★★★★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-004 |
| 所属模块 | S1 解析（无线框几何聚类）→ S3 结构 |
| 测试目标 | 列数与单元格文本须与 expect 一致；无线框是本项唯一难点，拿不到 3 列即为缺陷（不是『降级』，是漏检）。 |
| 输入与前置条件 | `tests/corpus/hard/hard_01_noframe_table.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D01`（或 `run_corpus.py --only hard_01_noframe_table`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 列数与单元格文本须与 expect 一致；无线框是本项唯一难点，拿不到 3 列即为缺陷（不是『降级』，是漏检）。｜声明：`{"tables": [{"nrow": 4, "ncol": 3, "header": ["项目", "本期金额", "上期金额"], "body": [["营业收入", "1,234,567", "987,654"], ["营业成本", "765,432", "654,321"], ["净利润", "469,135", "333,333"]]}], "headings": [{"text": "一、无线框表格测试", "level"`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | table_teds, table_shape_ok, col_count_drift |

### TC-005 ｜ S2 财报 / 报表入库 × D02 跨页表格（★★★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-005 |
| 所属模块 | S1 解析（行装配按 y 聚类）→ S2 分片缝合 |
| 测试目标 | 跨页续表若裂成两张表（n_pred=2）即为缺陷；表头重复出现是否合并由人工裁决，但**行数不得丢**。 |
| 输入与前置条件 | `tests/corpus/hard/hard_02_cross_page_table.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D02`（或 `run_corpus.py --only hard_02_cross_page_table`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 跨页续表若裂成两张表（n_pred=2）即为缺陷；表头重复出现是否合并由人工裁决，但**行数不得丢**。｜声明：`{"tables": [{"nrow_min": 41, "ncol": 3, "header": ["序号", "科目", "金额"]}], "headings": [{"text": "二、跨页表格测试", "level": 1}]}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 2 项未达；未达项 [{"check": "table[0]", "detail": "列数 2 != 3; 行数 37 < 下限 41; 表头 ['序号', '科目'] != ['序号', '科目', '金额']; 裂表：产物 2 张"}, {"check": "table 多余", "detail": "产物 2 张 > 期望 1 张"}]；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-43 |
| 优先级 | P0（§12.7 发布阻断：阻断即 P0，不看修复成本、不等 priorities.json 收编） |
| 覆盖指标 | table_teds, table_shape_ok, split_table, W1045 |

### TC-006 ｜ S2 财报 / 报表入库 × D03 合并单元格（DOCX gridSpan/vMerge）（★★★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-006 |
| 所属模块 | S1 解析（gridSpan/vMerge）→ S4 渲染（GFM 无 colspan） |
| 测试目标 | §1.2 非目标 6：不还原 colspan/rowspan。产物必须**要么**保持 4×3 管道表且 4 个 con |
| 输入与前置条件 | `tests/corpus/hard/hard_03_merged_cells.docx`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D03`（或 `run_corpus.py --only hard_03_merged_cells`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | §1.2 非目标 6：不还原 colspan/rowspan。产物必须**要么**保持 4×3 管道表且 4 个 contains 全中，**要么**降级为 HTML 表格并带 W1045；两者都不满足（内容丢失且无告警）即静默降级缺陷。｜声明：`{"headings": [{"text": "三、合并单元格测试", "level": 1}], "tables": [{"nrow": 4, "ncol": 3, "header": ["季度", "收入", "成本"]}], "contains": ["1,234,567 / 987,654", "上半年", "2,000,000", "3,000,000"]}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | table_shape_ok, silent_degrade, honest |

### TC-007 ｜ S2 财报 / 报表入库 × D06 数值形态（千分位 / 括号负数）（★★★，画像 `financial`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-007 |
| 所属模块 | S1 解析（文本序）→ S4 渲染（数值形态保真） |
| 测试目标 | 『(1,234)』**必须**保持括号形态——把它读成 1234 或 -1234 都改变了数值语义，是数字类文档的头号静 |
| 输入与前置条件 | `tests/corpus/hard/hard_06_numeric_forms.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D06`（或 `run_corpus.py --only hard_06_numeric_forms`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 『(1,234)』**必须**保持括号形态——把它读成 1234 或 -1234 都改变了数值语义，是数字类文档的头号静默错误。千分位逗号与货币符号须保留。｜声明：`{"tables": [{"nrow": 5, "ncol": 3, "header": ["科目", "本期", "上期"], "body": [["营业收入", "1,234,567", "987,654"], ["营业外支出", "(1,234)", "(5,678)"], ["毛利率", "36.5%", "33.8%"], ["资产总额", "￥8,765,432", "￥7,654,321"]]}], "headings":`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | contains, text_ned |

### TC-008 ｜ S3 技术手册 / 文档站 × D05 点线引导符（★★★，画像 `manual`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-008 |
| 所属模块 | S1 解析（run 间空格）→ S3 结构（目录形态识别） |
| 测试目标 | 引导符不得成为单元格内容；页码须与条目同行关联。若 TOC 形态被误判为表格，须显式降级告警。 |
| 输入与前置条件 | `tests/corpus/hard/hard_05_leader_dots.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D05`（或 `run_corpus.py --only hard_05_leader_dots`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 引导符不得成为单元格内容；页码须与条目同行关联。若 TOC 形态被误判为表格，须显式降级告警。｜声明：`{"headings": [{"text": "五、点线引导符测试", "level": 1}], "contains": ["第一章 总则", "第二章 职责", "第三章 附则"]}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 3 项未达；未达项 [{"check": "contains[第一章 总则]", "detail": "缺失"}, {"check": "contains[第二章 职责]", "detail": "缺失"}, {"check": "contains[第三章 附则]", "detail": "缺失"}]；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-44 |
| 优先级 | P0（§12.7 发布阻断：阻断即 P0，不看修复成本、不等 priorities.json 收编） |
| 覆盖指标 | lint_contract, leader_dots_in_cell, cjk_space_collapse, heading_f1 |

### TC-009 ｜ S3 技术手册 / 文档站 × D08 页眉逐页变体（★★，画像 `manual`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-009 |
| 所属模块 | S1 解析（页眉页脚）→ S3 结构（误升防护） |
| 测试目标 | 8 个章节标题须全部保留；页眉里的『奇数页/偶数页』与孤立页码『- N -』须全部剔除。**误剔除正文是比漏剔除更严重的 |
| 输入与前置条件 | `tests/corpus/hard/hard_08_header_variants.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D08`（或 `run_corpus.py --only hard_08_header_variants`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 8 个章节标题须全部保留；页眉里的『奇数页/偶数页』与孤立页码『- N -』须全部剔除。**误剔除正文是比漏剔除更严重的缺陷**。｜声明：`{"headings": [{"text": "第 1 节 正文章节标题", "level": 1}, {"text": "第 2 节 正文章节标题", "level": 1}, {"text": "第 3 节 正文章节标题", "level": 1}, {"text": "第 4 节 正文章节标题", "level": 1}, {"text": "第 5 节 正文章节标题", "level": 1}, {"text": "第 6 节 `（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 8 项未达；未达项 [{"check": "heading[第 1 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 2 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 3 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 4 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 5 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "heading[第 6 节 正文章节标题]", "detail": "层级 H2 != 声明 H1"}, {"check": "head；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-45 |
| 优先级 | P1（有偏差但未进优先级表——先查 priorities.json 是否缺输入层） |
| 覆盖指标 | furniture_residue, heading_f1, lint_dialect |

### TC-010 ｜ S3 技术手册 / 文档站 × D09 目录（TOC 域）（★★，画像 `manual`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-010 |
| 所属模块 | S1 解析（TOC 域）→ S3 结构（锚点去重） |
| 测试目标 | 目录缓存结果不得被当成正文标题重复输出：三个章节标题在产物里**各出现一次**（`no_duplicate_headin |
| 输入与前置条件 | `tests/corpus/hard/hard_09_toc_field.docx`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D09`（或 `run_corpus.py --only hard_09_toc_field`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 目录缓存结果不得被当成正文标题重复输出：三个章节标题在产物里**各出现一次**（`no_duplicate_headings`）。若产品选择保留目录文本，必须与真实章节标题可区分（不产生重复锚点）；引导用的制表位与页码不得成为独立段落噪声。｜声明：`{"headings": [{"text": "九、目录域测试", "level": 1}, {"text": "第一章 总则", "level": 1}, {"text": "第二章 职责分工", "level": 1}, {"text": "第三章 附则", "level": 1}], "contains": ["本章规定适用范围与术语定义。"], "no_duplicate_headings": true}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | heading_f1, duplicate_anchor, structure_bsf |

### TC-011 ｜ S4 论文 / 研究报告 × D02 跨页表格（★★★★，画像 `academic`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-011 |
| 所属模块 | S1 解析（行装配按 y 聚类）→ S2 分片缝合 |
| 测试目标 | 跨页续表若裂成两张表（n_pred=2）即为缺陷；表头重复出现是否合并由人工裁决，但**行数不得丢**。 |
| 输入与前置条件 | `tests/corpus/hard/hard_02_cross_page_table.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D02`（或 `run_corpus.py --only hard_02_cross_page_table`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 跨页续表若裂成两张表（n_pred=2）即为缺陷；表头重复出现是否合并由人工裁决，但**行数不得丢**。｜声明：`{"tables": [{"nrow_min": 41, "ncol": 3, "header": ["序号", "科目", "金额"]}], "headings": [{"text": "二、跨页表格测试", "level": 1}]}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 2 项未达；未达项 [{"check": "table[0]", "detail": "列数 2 != 3; 行数 37 < 下限 41; 表头 ['序号', '科目'] != ['序号', '科目', '金额']; 裂表：产物 2 张"}, {"check": "table 多余", "detail": "产物 2 张 > 期望 1 张"}]；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-43 |
| 优先级 | P0（§12.7 发布阻断：阻断即 P0，不看修复成本、不等 priorities.json 收编） |
| 覆盖指标 | table_teds, table_shape_ok, split_table, W1045 |

### TC-012 ｜ S4 论文 / 研究报告 × D11 双栏阅读顺序（★★★，画像 `academic`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-012 |
| 所属模块 | S2 分片缝合（阅读顺序） |
| 测试目标 | 与已入库基线逐字节一致 |
| 输入与前置条件 | `tests/corpus/sources/pdf_10_two_column_6p.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D11`（或 `run_corpus.py --only pdf_10_two_column_6p`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | `expected_behavior=ok`，逐字节等于 `expected/pdf_10_two_column_6p.md`（出处 `tests/corpus/manifest.json`） |
| 实际结果 | total_gap=0.0；blocking 偏差 0 条；规则命中 {"split_table": 0, "leader_dots_in_cell": 0, "cjk_space_collapse": 0, "mojibake_residue": 0, "furniture_residue": 0, "silent_degrade": 0, "fence_no_lang": 0, "col_count_drift": 0, "duplicate_anchor": 0}；证据轮次 generated_at=2026-10-25T07:24:05+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | order_nid |

### TC-013 ｜ S5 批量历史档案转换 × D07 加密 PDF（★，画像 `batch`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-013 |
| 所属模块 | S1.5 门禁（加密判定 E4151） |
| 测试目标 | 必须拒收且 `detail.password_required` 为真；不得与『结构损坏』混淆，也不得静默产出残缺结果（ |
| 输入与前置条件 | `tests/corpus/hard/hard_07_encrypted.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D07`（或 `run_corpus.py --only hard_07_encrypted`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 必须拒收且 `detail.password_required` 为真；不得与『结构损坏』混淆，也不得静默产出残缺结果（§1.2 非目标 1）。｜声明：`{"behavior": "reject:E4151"}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 全绿；未达项 []；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 通过 |
| 优先级 | P2（守成：本卡已由自动化覆盖，只需保持绿） |
| 覆盖指标 | reject:E4151, password_required |

### TC-014 ｜ S5 批量历史档案转换 × D10 CID 字体无 ToUnicode / Type3 转曲（★★★★★，画像 `batch`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-014 |
| 所属模块 | S1.5 门禁（CID / 无 ToUnicode 判定） |
| 测试目标 | 本项目不作 OCR（§1.2 非目标 1），乱码文本层的正确行为是**门禁拒收**或**显式告警**，而不是静默产出乱码 |
| 输入与前置条件 | `tests/corpus/hard/hard_10_cid_proxy.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D10`（或 `run_corpus.py --only hard_10_cid_proxy`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 本项目不作 OCR（§1.2 非目标 1），乱码文本层的正确行为是**门禁拒收**或**显式告警**，而不是静默产出乱码。契约本身待裁定（拒收 E4221 还是新增 W 码，B-47 需人拍板）；裁定前本项以 xfail(strict=True) 钉住，不得当作已通过。｜声明：`{"behavior": "reject:E4221"}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 1 项未达；未达项 [{"check": "behavior", "detail": "期望 reject:E4221，实际转换成功"}]；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-47 |
| 优先级 | P1（有偏差但未进优先级表——先查 priorities.json 是否缺输入层） |
| 覆盖指标 | mojibake_residue, reject:E4221, W1046 |

### TC-015 ｜ S6 扫描件 / 图片型 PDF × D10 CID 字体无 ToUnicode / Type3 转曲（★★★★★，画像 `batch`）

| 字段 | 内容 |
|---|---|
| 编号 | TC-015 |
| 所属模块 | S1.5 门禁（CID / 无 ToUnicode 判定） |
| 测试目标 | 本项目不作 OCR（§1.2 非目标 1），乱码文本层的正确行为是**门禁拒收**或**显式告警**，而不是静默产出乱码 |
| 输入与前置条件 | `tests/corpus/hard/hard_10_cid_proxy.pdf`；离线、`llm.mode=offline`、默认配置、`--force` |
| 执行步骤 | 1) `.venv/bin/python -m tests.qa.realtest --only L5`  2) `.venv/bin/python tests/corpus/run_hard.py D10`（或 `run_corpus.py --only hard_10_cid_proxy`）  3) 读 `work/qa/{attribution,defects,priorities}.json` 中该源记录 |
| 期望结果 | 本项目不作 OCR（§1.2 非目标 1），乱码文本层的正确行为是**门禁拒收**或**显式告警**，而不是静默产出乱码。契约本身待裁定（拒收 E4221 还是新增 W 码，B-47 需人拍板）；裁定前本项以 xfail(strict=True) 钉住，不得当作已通过。｜声明：`{"behavior": "reject:E4221"}`（出处 `tests/corpus/hard/expectations.json`，声明侧不经管线） |
| 实际结果 | 结构面 1 项未达；未达项 [{"check": "behavior", "detail": "期望 reject:E4221，实际转换成功"}]；证据轮次 generated_at=2026-10-25T07:24:08+00:00 |
| 通过状态 | 缺口 · B-47 |
| 优先级 | P1（有偏差但未进优先级表——先查 priorities.json 是否缺输入层） |
| 覆盖指标 | mojibake_residue, reject:E4221, W1046 |

