# Doc2md 综合转换语料（PDF / DOCX）

本目录用于验证 Doc2md 的转换能力与效果，独立于 `tests/golden/`（后者是逐字节门禁资产）。
语料覆盖从 1 页到 40 页、从简单段落到大文档，包含 PDF 与 DOCX 的主流结构。

## 目录

```text
sources/       17 份源文件（PDF 8 / DOCX 9）
expected/      16 份目标 Markdown（正文，不含 YAML frontmatter）
manifest.json  源文件 sha256、字节数、期望行为（ok / reject:E4221）
make_corpus.py 确定性生成器（reportlab / python-docx / pypdf）
run_corpus.py  转换并与 expected/*.md 比对
```

## 覆盖矩阵

### PDF

| 文件 | 覆盖点 |
| --- | --- |
| `pdf_01_simple_1p.pdf` | 1 页、编号标题、段落、中英混排 |
| `pdf_02_lists_2p.pdf` | 2 页、有序/无序列表、分页 |
| `pdf_03_table_1p.pdf` | 3×3 网格表、表后段落 |
| `pdf_04_links_code_2p.pdf` | 外部 URI 链接、等宽代码块 |
| `pdf_05_cjk_en_3p.pdf` | 3 页、中文/English/数字混排 |
| `pdf_06_scanned_1p.pdf` | 无文本层扫描页，预期 S1.5 门禁拒收 E4221 |
| `pdf_07_numbered_105pt_2p.pdf` | 10.5pt 正文中的单级编号列表（防标题误判） |
| `pdf_08_large_40p.pdf` | 40 页、560 块、长文档/性能 |

### DOCX

| 文件 | 覆盖点 |
| --- | --- |
| `docx_01_simple.docx` | Heading 样式、普通段落、整段加粗/斜体/删除线 |
| `docx_02_lists.docx` | Word 项目符号、编号列表 |
| `docx_03_tables.docx` | 普通表 + `gridSpan` 合并单元格 |
| `docx_04_links_notes.docx` | `w:hyperlink`、脚注、尾注 |
| `docx_05_revisions_fields.docx` | `w:ins`/`w:del`、`w:fldSimple`、`w:smartTag`、块级 `w:sdt` |
| `docx_06_textbox_image.docx` | 文本框、图片、分页符、页眉/页脚（后者不抽取） |
| `docx_07_code_quote.docx` | 等宽代码段、引用块 |
| `docx_08_large_30sections.docx` | 30 节、270 块、标题/段落/列表/表格混合 |
| `docx_09_mixed_cn_en.docx` | 中文/English/数字、Heading 编号、列表 |

## 目标 Markdown 的口径

`expected/*.md` 是**人工审阅后的目标基线**，只包含正文：转换器 `content.md` 的 YAML/JSON
frontmatter 由 `run_corpus.py` 剥离后再比对。当前基线反映以下已确认口径：

- PDF 标题需要字号显著大于正文且带编号/CJK 章标记等标题信号；本语料标题均带编号，避免把无编号短行标题误当段落。
- PDF 项目符号使用 `-`（ASCII），确保 S3 的 bullet 分支稳定识别。
- DOCX 整段加粗/斜体输出 `**...**` / `*...*`；删除线默认不输出 `~~`（需 `s4.preserve_marks_strike=true`）。
- DOCX 合并单元格在 GFM 中以空位补齐，例如 `| 合并列 | 值 备注 |  |`。
- 脚注/尾注正文作为尾部段落输出（不伪造 GFM 脚注语法）。
- 文本框内容当前输出在正文之后；这是已知的排版顺序限制，不是静默丢失。
- 页眉/页脚不进入 Markdown（页面家具）。
- `pdf_06_scanned_1p.pdf` 的目标是 **拒收 E4221**，不生成 Markdown；manifest 中 `expected_behavior` 记录该契约。

如果项目有意改变输出语义，请运行 `--write-baseline` 生成新基线，逐份审阅 diff 后再提交，
不要把“当前输出”直接当成“正确输出”。

## 使用

```bash
# 重新生成源文件（确定性：固定 PDF invariant / DOCX zip 时间戳）
python tests/corpus/make_corpus.py

# 转换全部语料并与目标 Markdown 比对
python tests/corpus/run_corpus.py

# 只跑某一类
python tests/corpus/run_corpus.py --only pdf_
python tests/corpus/run_corpus.py --only docx_

# 人工确认语义变化后更新基线
python tests/corpus/run_corpus.py --write-baseline
```

失败时实际输出写到 `work/corpus_diff/<name>.actual.md`，便于逐份 diff。
