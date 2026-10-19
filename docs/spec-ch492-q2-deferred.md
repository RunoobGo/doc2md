# CH-492 Q-2 推迟登记：`s3_structure.analyze` 拆分结构分析

> **本文档为 Q-2 推迟登记（CH-492）。**
> `analyze`（`doc2md/pipeline/s3_structure.py`，CH-492 落点 :480-839，约 360 行）的
> 「拆 7 个 `_detect_*` 模块级函数」方案经工程侧通读审查，判定不可行（四个结构性
> 障碍，见 §2），team-lead 裁决**方案 3（推迟）**：设计与实施统一归入未来的
> **「端点闭包 + 状态提取轮」**（与 Q-1 `create_app` 内嵌函数提取同属「大函数收敛」
> 主题，合并规划，避免同区域两轮 churn）。
> 本文档的边界分析与障碍证据是该轮的关键设计输入。
>
> 行号均以 CH-492 落点为准；未来轮实施时需先以 grep 锚点重新核对（文件会漂移）。

---

## 1. 背景与结论

架构师审查 Q-2 的原方案：`analyze` 约 360 行、7 类块判定全内联，拟每类块提一个
模块级 `_detect_*(...)` 函数，`analyze` 收敛为薄壳编排。

工程侧通读全文后的判定：**该方案的前提与实际代码结构不符。** `analyze` 不是
「逐信号分发独立检测器」的结构，而是一个**单块状态机**——4 组跨页行组缓冲 +
4 个 flush 闭包 + ~19 个严格有序、带落穿语义的分支链。按块型提取检测器需要先引入
中间状态传递层（状态容器），属方案级设计，按派令停机条件停下报告（未改任何代码）。

**结论：Q-2 本轮不实施。** 四个结构性障碍（§2）+ 7 类块完整边界（§3）已落盘本文档，
供「端点闭包 + 状态提取轮」直接复用；候选方案与推荐见 §4。

---

## 2. 四个结构性障碍（停机依据，行号证据）

### 2.1 障碍一：4 组跨页行组状态（16 变量）被所有分支共享

:497-513 四组跨页存续状态（CH-469 起段落缓冲亦跨页存续）：

| 组 | 变量 | 行号 |
|---|---|---|
| code | `code_buf` / `code_mode` / `code_lines` / `code_page` | :497-499, :507 |
| quote | `quote_buf` / `quote_lines` / `quote_page` | :500-501, :505 |
| row | `row_buf` / `row_x0` / `row_lines` / `row_page` | :502-504, :506 |
| para | `para_buf` / `para_lines` / `para_font` / `para_page` | :510-513 |

heading / list×4 / link / note / page_break 分支自身无缓冲，但**每一个都调用
flush_para + flush_quote + flush_rows**——即每个检测器都需要操作全部 4 组状态。
任何 `_detect_*` 签名要么携带 16 个变量，要么携带状态容器；不存在「各司其职」的
窄签名。4 个 flush 闭包（:515-574）是所有分支的公共内核，调用点计数：

- `flush_para`：14 处（:583, :603, :613, :651, :666, :696, :711, :723, :733, :749, :783, :792, :813, :828）
- `flush_quote`：13 处（:584, :604, :614, :642, :667, :697, :712, :724, :734, :750, :771, :793, :829）
- `flush_rows`：14 处（:585, :609, :615, :643, :668, :698, :713, :725, :735, :753, :756, :763, :794, :830）
- `emit_code`：5 处（:587, :620, :628, :631, :832）

### 2.2 障碍二：检测与发射分离（4/7 类块的 Block 不在检测分支内落）

code Block 只在 `emit_code`（:527）落、quote 只在 `flush_quote`（:549）落、
table 只在 `flush_rows`（:563）落、paragraph 只在 `flush_para → _flush`
（:536 → :955-968）落。这四类块的「检测分支」实际只做**缓冲累积**，发射由**别的行**
（空行 / 结构行 / 页界 / 文末 :828-832）触发。

`_detect_table(...) -> Block` 的提取模型与这个事实相反——表格检测本质是多行状态机
的**延迟发射**，不存在「一行进、一个 Block 出」的检测器形态。

### 2.3 障碍三：分支链严格顺序依赖 + 落穿副作用

- :650-651 struct-start 的 `flush_para` 改变了紧随其后 :765 quote 分支的入口谓词
  （`not para_buf`）；
- :762-763 非 row_ok 时 `flush_rows(True)` 向 `blocks` 追加 paragraph 块，改变后续
  :802 `_join_back` 看到的 `blocks[-1]`，然后**落穿**到 quote 分支；
- :771-772 无条件 `flush_quote()` 夹在 quote 检测与 mono 检测之间（mono 入口 :774
  同样要求 `not para_buf`；CH-464 注释明载此冲刷是为修复 quote 块乱序）；
- :801 续句分支要求 `not para_buf`——该状态由之前全部分支的 flush 历史决定。

四个「**有副作用但落穿**」分支（bool 返回值的检测器无法表达「副作用已完成 +
继续分发」）：

| 分支 | 行号 | 副作用 | 落穿去向 |
|---|---|---|---|
| 代码累积关闭 | :626-636 | `emit_code` | 本行走常规分流（indent/struct/heading/...） |
| struct-start | :650-651 | `flush_para` | heading 检测 |
| 表格非 row_ok | :762-763 | `flush_rows(True)` | quote 检测 |
| 续句并回未命中 | :802-807 | 无（join 失败） | 普通正文累积 |

若把落穿分支的副作用挪回 analyze 编排层，状态机仍留在 analyze，「7 个检测器」
即成虚构。

### 2.4 障碍四：7 类块 ≠ 7 个分支

- `list_item` 有 **4 个入口分支**：:695（DOCX 自动编号）/ :710（bullet）/
  :722（ordered）/ :732（alpha），产出同构但入口谓词各异；
- `paragraph` 有 **3 个成块入口**（:784 note / :795 link / :822 累积回退）+
  **1 个并回入口**（:801 续句 join，修改既有块而非落新块）。

按块型提取是 7 个函数、按分支提取是 ~19 个函数，映射本身不成立。

---

## 3. 7 类块边界分析（未来轮设计输入）

### 3.1 page_break（:582-591，单行成块，最干净）

- **入口**：`"\x0c" in ln.text`——必须先于 rstrip 判定（\x0c 是 Python 空白字符，
  rstrip 会剥掉它；:580-581 注释载明 CH-459 收尾自查实测确认）；
- **行组累积**：无（单行成块）；
- **冲刷/终止**：`flush_para` + `flush_quote` + `flush_rows(True)` + `emit_code`
  （若 `code_buf is not None`）——四处全冲后落块；
- **产出 Block**：`Block(id="", type="page_break", text=ln.text.replace("\x0c","").strip(), page=page.index)`；
- 始终 `continue`。

### 3.2 code（4 个分支 + emit_code 闭包 :515-531）

- **fence 起止**（:612-624）：入口 `_FENCE.match(text)`；起：`code_buf=[],
  code_mode="fence", code_page=page.index`；止：`code_mode=="fence"` → `emit_code`；
  indent/mono 块内的围栏行按普通内容追加（:621-623）；
- **累积**（:626-636）：入口 `code_buf is not None`；mono 模式遇非 mono 行 →
  `emit_code` 后**落穿**（:627-628）；indent 模式遇非缩进行 → `emit_code` 后**落穿**
  （:629-631）；否则追加（indent 模式 `re.sub(r"^(    |\t)", "", text)` 剥一级缩进）
  并 continue；
- **indent 起**（:640-647）：入口 `text.startswith(("    ","\t"))` 且 `not ln.mono`
  且 **`not para_buf`**（CH-482：等宽行不走本分支，前导空白是内容，交给 mono 分支）；
- **mono 起**（:774-778）：入口 `ln.mono` 且 **`not para_buf`**；
- **空行**（:599-602）：`code_buf` 非空时空行保留为 `""`（代码含空行）；
- **产出**（在 emit_code 内，不在检测分支）：fence/mono 模式先
  `_rebuild_code_indent(buf, code_lines)`（CH-482：indent 模式累积时已删一级，
  再按几何重建会双重缩进），去首尾空行，非空才落
  `Block(type="code", text="\n".join(buf), page=page)` + `_mark_provenance`。

### 3.3 heading（lvl 计算 :657-664 + 落块 :665-693）

- **入口三级**：`ln.row` → `lvl=None`（CH-485：表格行先走表格分组，首列「1.1 范围」
  会触发多级编号信号，代码注释明载）；`ln.level_hint is not None` → hint 优先
  （CH-459：DOCX Heading 样式显式声明）；否则 `ln.list_marker` → None，走
  `_heading_level(text, ln.size, base_size, ln.bold)` 启发式（:878-926）；
- **冲刷**：`flush_para` + `flush_quote` + `flush_rows(True)`（仅 lvl 真值时，:666-668）；
- **特殊路径**（:673-685）：与 `blocks[-1]` 同页 heading 且字号差 <0.6 且
  `len(prev.text)>=12` 且非 `_HARD_END` 结尾且非 `_STRUCT_START`/`_NUM`/`_CJK_HEAD`/
  `_CJK_ORD`/`_PAREN_HEAD` 起头 → **拼回前块**（`prev.text = _append_line(...)`，
  `_mark_provenance(prev, (ln,), merge=True)`），不落新块；
- **产出 Block**：`Block(type="heading", text=head_text, level=lvl, font=_font(ln),
  page=page.index)`，`head_text` 带 `ln.list_marker` 前缀（CH-485：自动编号并入标题文本）；
  hint 命中时打 `flags["level_from_hint"]=True`（CH-476：`_fix_levels` 据此不改写）；
  另 `_attach_link` + `_mark_provenance`；
- lvl 为假时**落穿**到 list 分支。

### 3.4 list_item（4 个独立分支，全部 continue，产出同构）

| 分支 | 行号 | 入口谓词 | 特有产出 |
|---|---|---|---|
| DOCX 自动编号 | :695-708 | `ln.list_marker` | `text=f"{'  '*list_level}{_normalize_marker(marker)} {text.strip()}"`；`flags["docx_numbering"]={"marker":..., "level":...}` |
| bullet | :710-718 | `_BULLET.match(text)` | `text=text.strip()` |
| ordered | :722-730 | `_is_ordered_item(text)` 且 `not (ln.row and len(ln.row)>=2)` | `text=text.strip()`（宁漏勿误：S1 已聚 ≥2 单元格时「1、」是单元格内容） |
| alpha | :732-740 | `_is_alpha_item(text)` 且 `not (ln.row and len(ln.row)>=2)` | `text=text.strip()` |

- **冲刷**：四分支各自 `flush_para` + `flush_quote` + `flush_rows(True)`；
- **产出 Block**：`Block(type="list_item", text=..., font=_font(ln), page=page.index)`
  + `_attach_link` + `_mark_provenance`。

### 3.5 table（累积 :744-761 + flush_rows 闭包 :555-574）

- **入口 row_ok**（:744-746）：`ln.row` 非空 且 `len(ln.row) >= min_cells`
  （`row_explicit ? 1 : 2`，CH-489：DOCX 单列表格也是显式表格，PDF 仍要求 ≥2 个
  几何聚类单元格）且（`row_explicit` 或 末单元格非 `_SENT_END` 结尾）；
- **行组累积**（:748-760）：空组 → `flush_para`+`flush_quote` 后 `row_page=page.index`；
  `ln.table_start` → `flush_rows(True)` 切开（新 DOCX 表格即使首行 x0 相同也切）；
  `abs(ln.x0 - sum(row_x0)/len(row_x0)) > _ROW_X0_TOL` → `flush_rows(True)` 切组
  （行首跳变）；追加 `row_buf` / `row_x0` / `row_lines`；
- **空行边界**（:608-609）：`ln.row is None` 才 `flush_rows(True)`——表格自己的空行
  （单元格全空）不触发切分；
- **非 row_ok 且 row_buf 非空** → `flush_rows(True)` 后**落穿**到 quote 分支（:762-763）；
- **产出**（在 flush_rows 内，不在检测分支）：`as_table` 且
  （`len(row_buf) >= _ROW_MIN` 或 单行显式表 `row_explicit`）→
  `Block(type="table", text=_gfm_table(row_buf), page=row_page)`；否则**逐行退回
  paragraph**（:566-573，漏判不误判，PDF 单行候选仍退回）。

### 3.6 quote（累积 :765-770 + flush_quote 闭包 :546-553 + 无条件冲刷 :771-772）

- **入口**：`_is_quote_line(text)` 且 **`not para_buf`**（段间边界起算，段中引用行
  按保守口径留在段内，:764 注释载明）；
- **累积**：新组置 `quote_page=page.index`；`quote_buf.append(_quote_text(text))`；
- **无条件冲刷**（:771-772）：任何非引用行到达 → `flush_quote()` **立即**落块
  （CH-464：原实现推迟到下一个空行/结构行，quote 块会排进其后的段落之间，乱序）；
- **产出**（在 flush_quote 内）：`Block(type="quote", text="\n".join(quote_buf),
  page=quote_page)` + `_mark_provenance`。

### 3.7 paragraph（note :782-788 / link :791-799 / 续句并回 :801-807 / 普通累积 :809-826 + flush_para 闭包 :533-544）

- **note**（:782-788）：入口 `ln.note_kind`（DOCX 显式 `w:p` 注边界，CH-476 决策 D7：
  不能被续句启发式并进前一个正文块）；`flush_para` 后单行成块；
- **link**（:791-799）：入口 `ln.link or ln.link_internal`；`flush_para` + `flush_quote`
  + `flush_rows(True)` 后单行成块（CH-485：块级链接无法表达行内范围，完整覆盖的链接行
  独立成块）；
- **续句并回**（:801-807）：入口 `_continuation_led(text)` 且 `not para_buf` 且
  `not ln.explicit_para`；`_join_back(blocks, text, _font(ln))` 命中 → 并回
  （`_mark_provenance(back, (ln,), merge=True)`，链接行且 `not back.links` 时补
  `_attach_link`）；未命中 → **落穿**；
- **普通累积**（:809-826）：`para_buf` 且 `_continues_paragraph(para_buf[-1], ln, layout)`
  → 追加；`para_buf` 且不续 → `flush_para` 后新起（`para_font=_font(ln)`，
  `para_page=page.index`）；无 `para_buf` → 依次尝试 `_join_heading_continuation` /
  `_join_list_continuation` 并回；否则新起 `para_buf`；
- **产出**（flush_para → `_flush` :955-968）：`Block(type="paragraph", text=缝合后,
  font=para_font or {}, page=para_page)`；另在 flush_para 闭包内取段内首条带链接行
  `_attach_link`（:538-541）+ `_mark_provenance`（CH-476）。

### 3.8 收尾（:827-839）

`flush_para`（CH-469：段缓冲跨页存续，统一文末落块）→ `flush_quote` →
`flush_rows(True)` → `emit_code(code_page)`（CH-464：原硬编码第 1 页，审阅页页码错位）
→ 构造 `IR` → CH-473 唯一赋值点按落块顺序重排 `b1..bN` → `_fix_levels(ir)`。

---

## 4. 候选方案与推荐（未来轮三选一）

### 4.1 方案 1：`_AnalyzeState` 容器化重写（推荐，须架构师先签字）

- 新增模块级 `_AnalyzeState`（16 状态字段 + `blocks`）；4 个 flush 闭包 → 模块级
  函数（`nonlocal` 改 `state` 字段，机械一一对应）；检测逻辑按**原分支序**保留，
  4 个落穿分支的副作用留在编排层、或检测器返回三态信号；
- **等价性保证**：机械替换 + T-B 同款 golden 逐字节（7 样本/档位 zip 逐条目 sha256，
  content.md 剔除 YAML front matter 的 elapsed_ms，口径同 `test_perf_lowend._entry_hashes`）
  + gate 404 + 全量；
- **风险**：中高——覆盖面宽（360 行函数全摸过），但每一步机械，golden 可兜底行为漂移；
- **收益**：闭包变模块函数后可单测；`analyze` 收敛为 ~80 行编排；
- **待架构师确认的设计点**：
  1. 四个「首行页」（`quote_page` / `row_page` / `code_page` / `para_page`）的存续规则
     在容器中的表达（何时置位、跨页是否保留）；
  2. 落穿分支的三态信号设计（CONSUMED / FALL_THROUGH / SIDE_EFFECT_DONE+FALL_THROUGH）；
  3. `blocks` 放容器内还是继续作为参数传递（`_flush` / `_join_back` / `_attach_link` 等
     既有模块函数已接收 `blocks` 参数）。

### 4.2 方案 2：只提纯谓词（低风险低收益）

- `_heading_lvl(ln, text, base_size)`（:657-664 纯计算，零副作用）+
  `_row_ok(ln)`（:744-746 纯计算）；
- 等价性：逐字搬迁，零行为风险；`analyze` 仅减 ~12 行，单独一轮收益/改动比过低。

### 4.3 方案 3：推迟（CH-492 本轮裁决）

- 与 Q-1（`create_app` 端点闭包提取）合并到「端点闭包 + 状态提取轮」；
- 理由：`analyze` 非热路径、无待修 bug，纯组织性重构紧迫性不足；方案 1 是新一轮
  设计，合并规划避免同区域两轮 churn（与 Q-1 选 C 同一逻辑）。

---

## 5. 测试面核实结论（未来轮契约风险为零的依据）

- **60+ 处调用全经公开签名 `analyze(...)`**，分布于 10 个测试文件
  （test_s3_blocks / test_ch471_fixes / test_ch473_fixes / test_ch475_fixes /
  test_ch476_fixes / test_ch478_fixes / test_ch482_indent_rebuild /
  test_ch483_pipeline / test_ch485_pipeline / test_pdf_structure_repair /
  test_batch2_storage）；
- 测试直接 import 的模块级符号仅 `analyze` / `modal_size` / `_is_ordered_item` /
  `_heading_level` / `_append_line` / `_rebuild_code_indent`——**嵌套闭包
  （emit_code / flush_para / flush_quote / flush_rows）无测试直接引用**（嵌套函数
  本不可 monkeypatch）；
- 结论：未来重写（方案 1）对外契约零变化，**测试零改动**；等价性验证完全依赖
  golden 逐字节 + 全量回归。

---

## 6. 未来轮实施备忘

1. 动工前以 grep 锚点重新核对本文所有行号（文件会漂移）；
2. 动工前确认 Q-1 端点闭包提取轮的落点，两轮尽量同一变更批次，减少 churn；
3. 等价性口径固定为 T-B 同款：7 样本/档位（large_320p single/two_phase +
   small_3p + links_pdf + 3 个 docx）前后 zip 逐条目 sha256 比对，必须 IDENTICAL；
4. `base_size` 是「分片≡单片」判据根（convert.py CH-471/PIPE-1）——`analyze` 的
   `base_size` 入参口径（None 时片内自算）与 modal_size（CH-492 P-1 已改单趟直方图）
   的交互不得破坏；
5. 四个 flush 闭包 → 模块函数时，`_flush`（:955）已是模块级先例，可参照其
   (buf, blocks, font, page) 显式传参风格设计容器边界。

---

*本文档由软件工程师（寇豆码）于 CH-492 落盘；Q-2 推迟裁决来自 team-lead。*
