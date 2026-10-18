# 真实 Word/PDF 样本收集（B-13 / B-15）

本目录记录真实样本的**收集方法、清单与分析脚本**；样本二进制默认放在
`work/real_samples/`（`work/` 已被 `.gitignore` 排除）。原因是 Office 模板受
Microsoft 许可约束、用户文档可能含隐私，不能未经确认直接提交到公开仓库。

## 当前收集结果（2026-10-18）

### B-13 真实 Word 模板

- 来源：本机安装的 Microsoft Office 2052 真实模板（`.dotx`）
- 收集 **23 份**，其中 **20 份可被 Doc2md 正常转换**（满足 B-13 的 ≥20 份门槛）
- 3 份未通过：
  - `Blog.docx`：模板包自身缺 `docProps/thumbnail.jpeg`，python-docx 无法打开
  - `Office Word 2003 Look.docx`、`Word 2010 look.docx`：文本层门禁 E4221
- 特征覆盖（按可读模板统计）：

| 特征 | 文件数 | 出现次数 |
| --- | ---: | ---: |
| 文本框 `txbxContent` | 11 | 116 |
| 自动编号 `numPr` | 9 | 22 |
| 表格 `w:tbl` | 11 | 155 |
| 合并单元格 `gridSpan`/`vMerge` | 3 | 10 |
| 内容控件 `w:sdt` | 20 | 1495 |
| 域 `w:fldSimple` | 1 | 1 |
| 超链接 | 2 | 14 |
| 图片/绘图 | 13 | 46 |
| 页眉 part | 16 | 33 |
| 页脚 part | 20 | 33 |

> Office 真实模板中没有 `w:smartTag`；该特征由合成语料
> `tests/corpus/sources/docx_05_revisions_fields.docx` 覆盖，不能把两者混为
> “真实模板已验证 smartTag”。

### B-15 真实中文 PDF

- 来源：用 Microsoft Word 16 将同一批 Office 中文模板导出为 PDF（不是 reportlab 合成）
- 收集 **10 份**，全部可转换；页数 1–8，CJK 字符 114–1256
- 正文字号分布：8.0 / 9.0 / 9.5 / 10.0 / 10.6 / 11.0pt（含真实 10.5pt 附近字号）
- PIPELINE 2.9.x 增加无编号大字号短行标题规则后，**9/10 份检出了标题块**
  （`ApothecaryLetter` 本身没有章节标题）
- 已建立 **10 份人工 ground truth**（`ground_truth.json`），不再用 DOCX Heading
  作最终代理。主要结果：
  - RedAndBlackReport：recall **0.967** / precision **0.906**
  - TimelessReport：recall **0.567** / precision **1.000**
  - AdjacencyReport 0.333 / 1.000、ApothecaryNewsletter 0.333 / 0.500
  - Essential/Origin/Student/RedAndBlackLetter 等仍有标题漏判，B-15 保持开放
- **重要口径**：样本仍是 Office 模板导出的 Word PDF，不等于任意真实中文 PDF；
  B-15 的最终出口还需扩展到非模板真实文档，并重点校准小字号章节标题。

### 本轮顺带修复

- Office 真实文本框常把段落包在 `w:sdt` 内，且可能嵌套另一个文本框；原
  `_docx_textbox_paragraphs` 只取 direct `w:p`，会整块漏抽。已改为递归展开
  sdt/customXml/嵌套文本框并按元素去重；模板抽查中多数文本框从 0–4/8 提升到 8/8。
- 新增 +6pt 无编号大字号短行标题规则，避免真实 Word 报告的无编号章节标题全部漏判。

## 复现

```bash
# 1) 真实 .dotx -> .docx（只改 content-type，不改正文）
python tests/corpus/real/convert_dotx.py

# 2) 逐份用 Word 导出 PDF（单份运行，避免批量 COM 卡死）
powershell -File tests/corpus/real/export_pdf.ps1 `
  -Docx "F:/Dev/Object/doc2md/work/real_samples/word/OriginReport.docx" `
  -Pdf  "F:/Dev/Object/doc2md/work/real_samples/pdf/OriginReport.pdf"

# 3) 分析特征与标题对照
python tests/corpus/real/analyze_local_samples.py
```

`analyze_local_samples.py` 输出 `work/real_samples/analysis.json`；不含正文摘要，
只保留文件、特征计数、页数、字号与标题文本。不要将 `work/real_samples/` 直接
提交到公开仓库，除非已确认模板/文档许可与隐私边界。

## 下一步（B-13/B-15 出口）

1. **B-13**：对 20 份可读模板抽查真实样式联动，重点看文本框/编号/合并单元格/
   页眉页脚；将失败 3 份分类为“模板自身损坏”或“产品门禁行为”。
2. **B-15**：人工标注 10–20 份真实中文 PDF 的标题集合（至少覆盖 10.5pt 正文、
   数字表、跨行链接、目录页、`第 N 章` 空格变体），再计算 H1 precision/recall；
   不要使用模板占位 Heading 自动对照作为最终证据。
3. 将收集到的非模板真实文档（脱敏后）另行登记来源与许可，不默认入库。
