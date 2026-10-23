# Doc2md 第二轮 QA 偏差修复验证

- 文档编号：QA-D2M-2026-10-23-R2-FIX
- 对照文档：`docs/qa/round2/comparison.md`（D1–D13）
- 被测版本：2.15.0 / SCHEMA 14 / PLAN 3.28
- 修复日期：2026-10-23
- 结论：**D1–D13 全部修复，回归全绿**

## 1. 验证口径

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 偏差专项回归 | `python -m pytest tests/test_qa_round2_fixes.py -q` | **16 passed** |
| 全量自动化 | `python -m pytest --junitxml=/tmp/doc2md-junit.xml` | **1226 tests / 0 failed / 0 errors / 2 skipped**（95.4s） |
| 语料比对 | `python tests/corpus/run_corpus.py` | **22 passed / 0 failed** |
| 语句覆盖率 | `coverage run --source=doc2md -m pytest -q && coverage report` | **90.3%**（7514 语句 / 730 未覆盖，与修复前 90% 持平） |

修复采用 TDD：`tests/test_qa_round2_fixes.py` 先于实现落地，首轮 15/16 红灯（唯一先绿的是 D7 的 PDF 集成用例，其单元判定由同一文件的 `_heading_level` 用例钉住）；实现后逐项转绿，再跑全量。

## 2. 逐项修复与验证

| ID | 修复点 | 行为变更 | 验证用例 |
| --- | --- | --- | --- |
| D1 | `doc2md/cli.py` `_apply_format`（[cli.py:505](file:///Volumes/fx/Object/Doc2md/doc2md/cli.py#L505)） | md 落盘失败时把结果行改为 `status=error` / `code=E5031` / `message`，保留 zip，退出码 4 | `test_d1_md_emit_failure_returns_error_status` |
| D2 | `s4_render.py` 新增转义层（[s4_render.py:84](file:///Volumes/fx/Object/Doc2md/doc2md/pipeline/s4_render.py#L84)）；`s3_structure.py` 字面量文档判定（[s3_structure.py:86](file:///Volumes/fx/Object/Doc2md/doc2md/pipeline/s3_structure.py#L86)） | 源文本先转义再叠加本层生成的强调/链接；同时出现 ≥2 类 Markdown 字面量形态的显式 DOCX 段落不再被 `#`/`>`/`1.`/`-` 启发式改型 | `test_d2_markdown_literals_stay_plain_text` |
| D3 | `s4_render.py` 转义 + 渲染红线（[s4_render.py:189](file:///Volumes/fx/Object/Doc2md/doc2md/pipeline/s4_render.py#L189)）；`s1_parse.py` 单元格预转义 | `<script>` / `<img onerror>` 输出为 `\<script` / `\<img`；字面 `[x](javascript:)` 不成为链接；`assert_render_contract` 拒绝未转义 HTML 与 `javascript:`/`data:`/`vbscript:` 链接目标 | `test_d3_raw_html_and_javascript_are_escaped` |
| D4 | `s3_structure.py` `_gfm_table`（[s3_structure.py:520](file:///Volumes/fx/Object/Doc2md/doc2md/pipeline/s3_structure.py#L520)） | 单元格先转义反斜杠再转义管道（`\|` → `\\\|`），按 `<br>` 分段处理，列数不再被拆散 | `test_d4_table_cell_escapes_backslash_before_pipe`、`test_d4_docx_cell_survives_gfm_round_trip` |
| D5 | `s1_parse.py` `_docx_table_rows` | 非续接单元格内的嵌套 `w:tbl` 计入 `flat`，触发 `flattened_cells` 与 W1045 | `test_d5_nested_table_is_flat_counted_and_warned` |
| D6 | `s1_parse.py` `_docx_page_break_offsets` / `_split_docx_page_break`；`read_docx` 段落分支 | 段内 `<w:br type=page>` 按字符位置切成「前文 → page_break → 后文」；分页符不在 run 内时退化为段末信号 | `test_d6_page_break_inside_paragraph_splits_at_position`、`test_docx_table_rows_and_page_break_signal` |
| D7 | `s3_structure.py` `_heading_level`（[s3_structure.py:1063](file:///Volumes/fx/Object/Doc2md/doc2md/pipeline/s3_structure.py#L1063)） | 字号显著大于正文只是必要条件，还需页顶（≤10% 页高）、短行（≤16 字）或粗体之一；页中 14pt 提示框保持段落 | `test_d7_midpage_large_callout_is_not_heading`、`test_d7_pdf_callout_stays_paragraph` |
| D8 | `edits.py` `patch_block`（[edits.py:77](file:///Volumes/fx/Object/Doc2md/doc2md/edits.py#L77)）；`api.py` PATCH 处理器 | 同文提交返回 `unchanged=true` 与原 revision，不升版本、不写审计、不重建索引、不发 `text_edited` | `test_d8_same_text_patch_is_noop` |
| D9 | `api.py` `_window`（[api.py:248](file:///Volumes/fx/Object/Doc2md/doc2md/api.py#L248)）；`storage/repo.py` `list_docs` | 显式 `limit=0` 在各出口统一为「空集 + limit=0」；缺省仍取默认窗口并受 500 行硬顶；`docs list` 的 `has_more=false` | `test_d9_explicit_zero_limit_is_empty_everywhere`、`test_d9_cli_docs_list_zero_limit_is_empty` |
| D10 | `api.py` `create_app` override 加载（[api.py:144](file:///Volumes/fx/Object/Doc2md/doc2md/api.py#L144)） | 坏 JSON → `ConfigError`（带文件、line/column），CLI 映射 E4001 / 退出码 2；非对象 JSON 同样拒绝 | `test_d10_malformed_override_maps_to_config_error` |
| D11 | `README.md:3` | 版本口径改为 v2.15.0，与 `PIPELINE_VERSION` / pyproject 一致 | `test_d11_readme_version_matches_pipeline` |
| D12 | `cli.py` `_apply_format` / `_report`（[cli.py:626](file:///Volumes/fx/Object/Doc2md/doc2md/cli.py#L626)） | md 模式删除 zip 后移除结果行的 `zip` 键并给出 `zip_removed=true`；失败/跳过的结果行仍保留 `zip` 字段（含空串） | `test_d12_md_result_does_not_report_deleted_zip` |
| D13 | `worker.py` `_file_label`（[worker.py:421](file:///Volumes/fx/Object/Doc2md/doc2md/worker.py#L421)） | 事件文件标识改为 `basename#sha256(绝对路径)[:8]`，总长 ≤63，避免被 `scrub_record` 二次指纹化 | `test_d13_worker_event_path_is_locatable` |

## 3. 兼容性说明（三处有意的契约变化）

1. **`limit=0`**：此前 `/docs`、`/docs/<id>/audit`、`/audit/deletions` 把显式 0 当作默认窗口，而 `/jobs`、`/search` 当作空集。现统一为**空集**，与 HTTP 分页的通常直觉一致；缺省参数行为不变（仍为 500 行硬顶）。
   - 已同步更新 `tests/test_ch489_fixes.py::test_explicit_zero_limit_is_empty_and_default_caps_at_500`，并保留「缺省仍硬顶 500」的原始 B-20 断言。
2. **`--format md` 结果行**：zip 删除成功后不再出现 `zip` 键，改用 `zip_removed=true`；转换失败或 skip-existing 行的 `zip` 字段形状不变。
   - 已同步更新 `tests/test_ch491_features.py::test_format_md_removes_zip`。
3. **段落中的三个反引号字面量**：现在逐字符加反斜杠转义，否则会与本层代码块围栏配对、错切真实代码块。
   - 已同步更新 `tests/test_ch473_fixes.py::test_pipe11_stray_fence_in_text_does_not_expose_code_block`，其「代码块三连空行逐字节保留」的核心断言不变。

## 4. 残余风险与边界

| 项 | 说明 | 处置 |
| --- | --- | --- |
| D2 字面量判定的边界 | 判定依赖「显式 DOCX 段落 + ≥2 类 Markdown 形态」。只有一类字面量的文档（如整篇都是 `#` 开头的示例行）仍按结构解析。 | 语料 22/22 通过；如需更强判定，应引入文档级显式标记，而不是继续放宽启发式。 |
| 无 `w:br` 偏移的异常结构 | `<w:br type=page>` 直接挂在 `w:p` 下（不在 run 内）时按段末处理。 | 保住分页信号；该类结构非 OOXML 常规产出。 |
| 表格单元格转义的覆盖面 | 转义反斜杠、管道、星号、下划线、方括号、反引号、波浪号与 `<`；`&` 未二次转义（DOCX 单元格已在 S1 预处理，PDF 表格若源文本本身是 `&lt;` 会被渲染成 `<`）。 | 属显示层细节，不影响列结构；留待后续按需处理。 |
| 性能 | 新增转义为逐行线性扫描；全量套件 95.4s、覆盖率 90.3%，与修复前同量级。 | 无需额外优化。 |
