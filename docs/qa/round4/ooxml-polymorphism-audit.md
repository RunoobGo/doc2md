# OOXML 规范多态排查（OPT4-12 / CH-506）

日期：2026-10-27　轮次：R19　排查对象：`doc2md/pipeline/s1_parse.py::read_docx` 的 DOCX 读取链

## 0. 口径

只排查**兜底会产出内容**的路径（`or` 缺省、`if not x: x = 默认值`）。少读形态但兜底
**不产出**内容的（静默少产、静默丢失）另列，不计入本项修复范围——本项要抓的是 D-1
（R19-PIPE-3）那一类：**文档里不存在的字符被写进交付正文**。

每个构造问三问：① 规范定义了几种取值形态？② 实现读到几种？③ 少读的那些形态触发什么兜底？

判级：

| 记号 | 含义 |
|---|---|
| ✅ | 形态数与规范一致，或缺省值由规范明确声明 |
| ⚠️ | 少读形态，且兜底**产出内容**——D-1 同型风险 |
| 💤 | 少读形态，但兜底**不产出内容**（静默少产，超出本项口径，仅登记） |
| 🟡 | 兜底是有意的渲染近似（Markdown 结构要求所致），不构成凭空造字 |

章节号按 skelmis-docx 整理的 ECMA-376 CT_Lvl / CT_Num 编号；`lvlText` 缺失行为规范
**未定义**，故不引具体条文的「默认值」断言。

## 1. 编号链（`_DocxNumbering`）

| # | 构造 | 规范形态 | 实现读取 | 少读形态 → 兜底 | 判级 | 处置 |
|---|---|---|---|---|---|---|
| 1 | `w:lvl/w:numFmt` | `ST_NumberFormat` 二十余种（decimal / zeroDecimal / bullet / none / letter / roman / ordinal / cardinalText / ideograph\* / chicago 等）；缺省 = decimal（§17.9.17） | `none` / `bullet` / 两种 letter / 两种 roman / 其余 → `str(n)` | 二十余种少见格式 → 十进制数字 | ⚠️ | **F-4 登记不修**：产出的是「错字形」而非「多出来的标记」；补全需 Unicode 映射表与真实样本 oracle，模块文档已声明「无法解析的格式回退 decimal」 |
| 2 | `w:lvl/w:lvlText` | 元素缺失 = 规范未定义；存在但空 = 该级不显示编号（CH-504）；存在有值 = `%N` 模板 | 缺失 → `"%1."`；存在但空 → 无标记；有值 → 模板 | 无（缺失域规范未定义） | ✅ | CH-504 已把三态拆开；缺失态的 `"%1."` 是对畸形文档的自觉兜底，14 篇 DOCX 语料零命中 |
| 3 | `w:lvl/w:start` | 缺省 = 1（§17.9.25） | 缺省 1 | 无 | ✅ | — |
| 4 | `w:lvl/w:ilvl` | 缺省 = 0；非法值 = 畸形 | 缺省 0；非整数跳过该级 | 无 | ✅ | — |
| 5 | `w:num/w:lvlOverride/w:lvl` | 整条级别定义**替换**抽象层同 `ilvl` 的定义（§17.9.15） | 原实现**完全未读** | 覆盖层 `numFmt=none` / 空 `lvlText` → 凭抽象层造出 `1. `；格式替换被无视 | ⚠️ | **F-1 已修（CH-506）**，红测 + 变异证明 |
| 6 | `w:num/w:lvlOverride/w:startOverride` | 覆盖该 `numId` 实例的起始值（§17.9.15） | 原实现**完全未读** | 起始值 5 → 仍从 1 起（序号错，无声） | ⚠️ | **F-1 已修（CH-506）**，同一代码路径 |
| 7 | `w:lvl/w:lvlRestart` | `0` = 永不重启；`N` = 第 N 级（一基）出现时重启；缺省 = 上一级出现时重启（§17.9.10） | 不读；无条件在高级推进时重置全部更深级 | `0` / `N` 两种 → 产出**错误的序号**（非凭空多标记） | ⚠️ | **F-3 登记不修**：一基索引语义需 Word / LibreOffice 渲染 oracle 对照；14 篇语料零命中 |
| 8 | `w:lvl/w:isLgl` | `true` = 法律编号，复合模板中所有级别以十进制显示；缺省 = 各用自身 `numFmt`（§17.9.4） | 不读 | 引用级别仍按 roman / letter 渲染（Word 显示十进制） | ⚠️ | **F-2 登记不修**：同 F-3，缺 oracle；零语料命中 |
| 9 | `w:numPr/w:numId` | `0` = 无编号；缺 `val` = 畸形 | `0` → 无标记；缺 `val` → 无标记且不回落样式 | 畸形域取保守（不产内容） | ✅ | — |
| 10 | `w:numPr/w:ilvl` | 缺省 0；段落只给 `ilvl` 时继承样式 `numId`（§17.3.1.19） | 继承样式 `numId` + 段落 `ilvl` 覆盖 | 无 | ✅ | — |
| 11 | 样式级 `w:pPr/w:numPr` 的 `basedOn` 链 | 派生样式继承基样式的编号 | 只读该样式自身，不追 `basedOn` | 派生样式段落无标记 | 💤 | **F-6 登记**：静默少产，超本项口径 |
| 12 | `w:abstractNum/w:numStyleLink` | 抽象定义可委托给编号样式 | 不读 | 无级别 → 无标记 | 💤 | **F-6 登记** |
| 13 | `w:lvl/w:pStyle` | 级别可绑定段落样式（该样式段落自动获得此级编号） | 不读 | 该样式段落无标记 | 💤 | **F-6 登记** |
| 14 | `w:lvl/w:suff` | `tab` / `space` / `nothing`；缺省 tab（§17.9.28） | 不读；S3 一律 `marker + " " + text` | `nothing` → 仍补一个空格 | 🟡 | Markdown 列表必须有分隔；`nothing` 是版面提示，不是正文内容 |

## 2. 相邻 OOXML 读取处

| # | 构造 | 规范形态 | 实现 | 判级 |
|---|---|---|---|---|
| 15 | `w:tcPr/w:gridSpan` | `val` 正整数；缺省 = 1 | `val` 非法 → `max(1, …)`；展开为网格空位 | ✅ |
| 16 | `w:tcPr/w:vMerge` | `restart` / `continue`（缺省）；无 `val` = continue | 无 `val` → continue；连续格置空并计入拍平数 | ✅ |
| 17 | `w:tblGrid/w:gridCol` | 声明网格宽度；可缺省 | 缺省时以最长行单元格数为宽度，短行补空位 | ✅（补的是对齐，不是文本） |
| 18 | `w:br/@w:type` | `textWrapping`（缺省）/ `page` / `column` | `page` → 分页；其余 → 换行 | 🟡（column break 降级为换行，少产分页语义，不产内容） |
| 19 | `mc:AlternateContent` | `Choice` 与 `Fallback` 是同一内容的两种表达 | 优先 `Choice`，缺失才 `Fallback`；元素 id 去重 | ✅（只取一支，防文本翻倍） |
| 20 | `w:sdt` / `w:customXml` 包裹层 | 块 / 行 / 单元格均可被内容控件包裹 | 透明下钻 | ✅ |
| 21 | `w:footnote/@w:type` / `w:endnote/@w:type` | `separator` / `continuationSeparator` / 普通注 | 分隔符占位注跳过 | ✅ |
| 22 | 标题样式名 | `Heading N`（N=1..9）；自定义样式名不受约束 | `heading` 前缀 + 末段整数；`Heading` 无数字 → 异常兜底为 1 级 | 🟡（**F-7 登记**：名为 `Heading` 的自定义样式会被误判成 H1，属样式名约定而非 OOXML 多态） |

## 3. 发现清单

| 编号 | 发现 | 判级 | 处置 |
|---|---|---|---|
| **F-1** | `w:lvlOverride`（`lvl` 与 `startOverride`）未被读取：覆盖层置 `none` 仍造标记（D-1 同型），`startOverride` 被忽略则编号从 1 起 | ⚠️ | **已修（CH-506）**：`_DocxNumbering.__init__` 逐 `numId` 深拷贝抽象级别后应用覆盖；`tests/test_ch506_numbering_override.py` 4 项红转绿，变异（覆盖循环置空）3 红 1 绿 |
| **F-2** | `isLgl` 未读：法律编号下引用级别仍用 roman / letter | ⚠️ | 登记不修（缺渲染 oracle；零语料） |
| **F-3** | `lvlRestart` 未读：`0`（永不重启）被当作默认重启，序号可能错 | ⚠️ | 登记不修（一基索引语义需 oracle；零语料） |
| **F-4** | 少见 `numFmt`（ordinal / ideograph / chicago 等）坍缩成十进制数字 | ⚠️ | 登记不修（错字形非造标记；模块契约已声明该回退） |
| **F-5** | `lvlText` 元素缺失 → `"%1."` | ✅ | 规范对缺失行为未定义；保留兜底 |
| **F-6** | `basedOn` 继承、`numStyleLink`、`lvl/w:pStyle` 三种**少产**形态未读 | 💤 | 登记，超本项口径（兜底不产内容） |
| **F-7** | 样式名 `Heading`（无数字）异常兜底为 1 级 | 🟡 | 登记（样式名约定，非 OOXML 多态） |

**为什么四条既有腿都没抓到 F-1**：14 篇 DOCX 语料全部由 python-docx 写出，而
python-docx 从不写 `lvlOverride` / `startOverride` / `isLgl` / `lvlRestart` / `numStyleLink`
（本机实测各 0 次）。与 D-1 的教训一致：**补一种文档形状比补十条同形状语料有效**。
`lvlOverride` 的形状已由 CH-506 的合成夹具覆盖；其余形态若将来出现真实样本，按本表
对应行补夹具即可。

## 4. 回归证据

```
.venv/bin/python -m pytest tests/test_ch506_numbering_override.py tests/test_ch504_numbering.py -q
# 8 passed
.venv/bin/python -m pytest tests/test_ch506_numbering_override.py -q   # 变异后
# 3 failed, 1 passed（三条覆盖形态红，抽象层守卫绿）
# 还原后 md5 ea5d06aabb9cbaf7af5f57ecbd487fb5 与变异前一致，4 passed
.venv/bin/python -m ruff check doc2md/pipeline/s1_parse.py tests/test_ch506_numbering_override.py
# All checks passed
```

语料侧：14 篇 DOCX 中 `numbering.xml` 均无上述高级构造（各键 0 命中），故本次修复
不改变任何黄金字节；`run_corpus.py` 的全量逐字节结果见本轮收口记录。
