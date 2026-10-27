# 第十九轮全面审查报告（CH-504）

- **审查基线**：`main` @ `694279a`（v2.19.0 / SCHEMA 14 / DB 10 / PLAN 3.37；第十八轮 CH-503 及其远端取证闭合之后）
- **审查范围**：三层并行只读审查 —— ① 架构与模块边界（层间依赖、责任划分、文档承诺的架构不变式）② GUI 与 API（对外契约、两面一致性、审阅页）③ 代码实现与文档（管线正确性、性能、QA 判定层、活文档精度）；另按用户第二部分要求完成 QA 第四轮（`docs/qa/round4/`，137 例）。
- **决策原则**：功能完整、性能优先；**本机可自动验证者即修即验并补红线**，需本机以外系统环境、外部服务接入或人工测试者**仅登记 B 类不实施**；改动保持现有代码风格、命名规范、对外接口与既有测试兼容，不做破坏性重构。
- **出口版本**：`PIPELINE_VERSION` 2.19.0→**2.20.0**（R19-PIPE-1 改变交付 zip 容器字节；R19-PIPE-3 使 LibreOffice 写出的 DOCX 正文不再凭空多出 `1. ` 前缀 = 交付内容变化）、`PLAN_VERSION` 3.37→**3.38**、`SCHEMA_VERSION` 14 / `DB_VERSION` 10 不动、`pyproject.toml` 同步 2.20.0。
- **本轮新增红线**：`tests/test_ch504_*.py` 四个新文件 **40 项**（numbering 4 / pipeline_links 9 / qa_caliber 8 / service_faces 19，全部 `gate`）+ `test_ch491_doc_sync.py` **+1**（R19-DOC-1 交叉引用可解析，6 → 7）= **gate +41 项**。
- **出口取证**（本机 macOS arm64 / Python 3.15，内容定型后实测）：`pytest -m gate --junitxml` **tests=683 / failures=0 / errors=0 / skipped=0**（套件墙钟 47.444s；文档红线修复前的收口采样 47.743s 只作历史）；全量 **tests=1516 / 0 / 0 / skipped=16（真 skip 10 + xfail 6）**（106.437s；修复前 113.560s 只作历史）；覆盖率 **7,843 stmts / 737 miss = 打出 91% / 精确 90.60%**（CH-503：7,804/738 = 90.54%）；语料回归 **22/22 逐字节一致**；六样本「HEAD 工作树 vs 工作副本」等价性 **6/6 逐字节一致**；`ruff check doc2md tests config ops` 全绿、`doctor` 退 0。

> **发现计数**（逐条与本文各表同源）：管线与产物层 3（PIPE-1/2/3）+ 性能 2（PERF-1/2）+ 服务面 13（ARCH-1/3/4/5/6、ARCH-2/8/9、API-1/4/5/6、SVC-1）+ 审阅页 4（GUI-1..4）+ 判定层 4（QA-1..4）+ 文档层 7（DOC-1..5、API-2/3）+ 改判撤销 1（ARCH-7）= **34 项编号**。
>
> **处置恒等式**：**22 实施**（PIPE-1/2/3、PERF-1、ARCH-1/3/4/5/6、API-1/4/5/6、SVC-1、GUI-1..4、QA-1..4）+ **10 文档收口**（DOC-1..5、ARCH-2、ARCH-8、ARCH-9、API-2、API-3）+ **1 撤销**（ARCH-7）+ **1 登记未实施**（PERF-2，与 B-44 同批）= **34**。PIPE-2 的残余面（显式 `fence` / `indent` 段内的链接仍被吸收）不另占编号，登记为 OPT4-18。
>
> **编号裁定**：审查过程中附录 C 的六处契约漂移曾用 `C-1…C-6` 作工作编号，与上游蓝本附录 C、本文 9.4 的 `C-1` 撞名——同一编号指向两种内容正是本项目 O-g 纪律所禁止的形态。最终登记把它们并入 **R19-DOC-2** 并以 `C-1…C-6` 作**子项序号**（只在本条内部使用，不参与全局编号），`R19-DOC-4` 改为 **B-44 塌词根因补登**（dev-plan §12.7 已按此引用）。

---

## 一、管线与产物层

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **R19-PIPE-1** | HIGH | **交付 zip 不可复现，交付物的哈希不是其内容的函数。** `convert` 的打包段用 `ZipFile.writestr(str, …)`，由 `ZipFile` 在写盘时按**墙钟**现造 `ZipInfo`：同一份 IR / md / meta 两次打包得到两个 sha256，而条目的 `file_size` 与 CRC 逐字节相同——变的只有容器。后果不是「不好看」：`artifacts._rebuild_artifact_body` 的空操作 PATCH / rollback 会产出与初次转换不同的 zip，「产物变没变」的比对、内容寻址校验、备份去重全部拿到假阳性；这也与本函数剔除 `llm_stats`（front-matter 不得污染正文哈希）的理由自相矛盾。`tests/test_perf_lowend.py::_entry_hashes` 之所以按**条目内容**取哈希，正是在绕这个洞 | 改为显式 `ZipInfo` 并钉三处：`date_time` 固定 DOS 纪元 `(1980,1,1,0,0,0)`（DB 重建路径没有源文件，两条路径必须同字节）、`create_system=0`（不钉则 Windows=0 / Unix=3 随生成机漂移，跨 OS 比对静默失效）、`compress_type` **必须在 ZipInfo 上重申**（`ZipFile(..., ZIP_DEFLATED)` 的默认只作用于 str 弧名那条路径，漏写会退成 STORED——本机实测同条目 36 B 对 4,000 B） | `doc2md/pipeline/convert.py`；红线 `test_ch504_pipeline_links.py` 2 项。实测（`small_3p.pdf`）：旧码间隔 3s 两次 → `b35a0ec174a1f1d5` / `d1cb7bddfa84b860`，新码两次同为 `e9a11e0a24f4e3eb` |
| **R19-PIPE-2** | MED | **等宽行吞掉链接标注。** `s3_structure.analyze` 的分支顺序里「等宽连续段 → 代码块」排在「块级链接独立成块」之前，带 Link 标注的等宽行落进 code 块，`_attach_link` 永不执行：外链 URL 与内部 GoTo 目标（`#page=7`）从 IR 与产物里静默消失，`ir.warnings` 为空——违反 C-29 / O-05「诚实暴露而非静默降级」。内部跳转尤其致命：正文文字完全看不出这里曾有交叉引用 | 两处只加**前件**、不动顺序：`ln.mono and not para_buf and not (ln.link or ln.link_internal)`；mono 段遇链接行收块。不调顺序是因为含链接的注文会从 note 块退化成普通链接段（红线两形态各一项） | `doc2md/pipeline/s3_structure.py`；红线 5 项。31 篇夹具扫描 `mono=6 link=2 BOTH=0`——**当时零命中、属潜伏缺陷**，故红线用合成 `Line` 钉住。残余：显式 `fence` / `indent` 段内的链接仍被吸收且无声，登记 OPT4-18（需新 W 码 + 附录 D 预留） |
| **R19-PIPE-3** | HIGH | **编号凭空生成，文档里不存在的字符进入交付正文。** `_DocxNumbering.marker` 对空 `lvlText` 兜底成 `"%1."`。LibreOffice（含 `.doc` → `.docx` 中间件）给 `Heading1..9` 挂样式级 `w:numPr`，指向每级 `numFmt="none"` + `lvlText=""` 的 `abstractNum`；按 ECMA-376 §17.9.19，`none` 的语义就是「不显示编号」。原实现于是给每个标题加上 `1. ` 前缀，`warnings` 零告警 | 区分**三态**：「`lvlText` 元素在但为空」与「元素缺失」分开读（`has_text`；两者都读成 `""` 会把前者误判成后者），`numFmt="none"` 或空文本返回 `("", -1)`，**计数器照常推进**（深层 `%2` 引用仍算得对） | `doc2md/pipeline/s1_parse.py`；红线 4 项（含反向守卫：真实 `%1.` / `%1.%2.` 级联必须仍产出 `["1.","1.1.","1.2."]`）。实测：旧码 `.doc` 得 `# 1. 一级标题` 而同源 `.docx` 干净（同一文档两条格式路径交付不同）；新码两条路径一致 |
| **R19-PERF-1** | MED（收益） | `page_titles` 空集仍逐行付 `_furniture_key`：31 篇样本里 29 篇该集合为空，而 N-8 臂对每个非表格行都要算一次（内含 3 次预编译 sub + 一次模块级 `re.sub`）。320 页金样本实测 **18,312 次调用中 15,613 次（85%）来自该臂**，占 `analyze()` 118.4 ms 里的 **27.2 ms**（S3 的 23.0%） | 空集短路：`page_titles` 为空时该臂直接不参与。空集上 `in` 恒为 False，**不改语义、不改产物字节**；墙钟不作断言（按机器快慢变色的断言不进红线），改由「空集时调用数为 0」这一可静态复算的量守住 | `doc2md/pipeline/s3_structure.py`；红线 2 项 |
| **R19-PERF-2** | MED（收益） | 双栏路径 `_blanks_in_band` / `keep()` 为二次复杂度，实测 32.9×–323× | **登记未实施**（与 B-44 单元格修复同批，OPT4-17）：改它要动分栏几何判据，属 B-43/B-44 的人工真值裁定面 | `docs/qa/round4/optimization-plan.md` 的 OPT4-17 |

**等价性取证（本轮最强的一条「没有回归」证据）**：临时 `git worktree` 检出 HEAD `694279a`（2.19.0）与工作副本（2.20.0），六个可比黄金样本各转一次（auto 档、剥 front-matter），**三条目内容 6/6 逐字节一致**（`ir.json` 的差异只有 `.meta.pipeline_version`，回写后相等），且六个值逐字符复现 CH-503 基线。zip 容器 sha 六个全变是 PIPE-1 的设计后果而非回归。`tests/corpus/` 22 篇语料逐字节不变（受影响面只到带样式级 `numPr` + `numFmt=none` 的 DOCX）。

---

## 二、服务面（「两个面必须一致」）

本轮服务面发现有一个共同形态：**每条路径单独看都「不崩」，合起来却给出与自身文档或自身另一分支矛盾的结果**。

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **R19-ARCH-1** | P1 | `DOCS_MAX_ROWS` 是**单页行数**上限，`_nonneg` 却拿它同时夹 `--offset`。`docs list --offset 600` 静默从 500 开窗——退出码 0、结果不是请求的那一页。API `_window` 只夹 limit、`cmd_search` 早在 R17-API-7 就绕过；三个面里只有 docs 两个子命令没跟上 | `_nonneg(capped=False)`，负值仍挡回 | `doc2md/cli.py` |
| **R19-ARCH-3** | P1 | `cron.run` 的日报步骤裸在 CH-495 那个 `try/finally` **之前**：ENOSPC / 只读卷 / 库损坏一抛，整份 cron 报告连同 `writer.close` / `store.close` / 报告落盘全部丢失——正是那个 finally 要防的形态，只是漏在它前面一步 | 单步 `try/except` 守护（与备份段同款），`rep.ok=False` | `doc2md/cron.py` |
| **R19-ARCH-4** | P1 | `_purge._kill` 把 `FileNotFoundError` 记 failed，让 delete_doc / delete_source 冒 E5031（500）。列举与删除之间被并发方抢先移除时期望终态**已达成** | `missing_ok=True` + `FileNotFoundError` 分支判成功；真 `OSError` 仍失败 | `doc2md/storage/repo.py` |
| **R19-ARCH-5** | P2 | 一行损坏的 `cascade_json` 让整份删除审计 500 | 降级 `{}`（与 `/jobs` 读 `detail_json` 同款） | `doc2md/api.py` |
| **R19-ARCH-6** | P1 | `Store.close()` 重新赋值 `self._local`。`threading.local()` 实例是**各线程共用**的一个容器，换对象把其它线程缓存的 conn/holder 一并抹掉：那些线程的下一次 `_conn()` 静默新建连接（Store「关了又活」），原连接从此失去 close 路径（`PRAGMA optimize` 不执行、fd 挂到 GC） | 只清本线程槽位 | `doc2md/storage/db.py` |
| **R19-API-1** | P1 | `POST /tokens` 缺 `role` 时 `b.get("role","reader")` 静默签 reader。附录 C 写的是 `body {role, label?}`——只有 label 带 `?` | 400 + `detail.allowed`；**缺省不取 `auth.issue` 的 admin**——缺省抬高权限比缺省拒绝更糟 | `doc2md/api.py` |
| **R19-API-4** | P2 | Content-Type 错误的 400 承诺带 `detail.field`，实测 `detail` 整个缺失（`errors.to_body` 对空 detail 省略该键） | 补 `{"field","got"}` | `doc2md/api.py` |
| **R19-API-5** | P2 | `auth.MATRIX["rerun"]` 全仓零调用（重跑其实走 `POST /jobs` + `force`），docstring 却写 editor 有「重跑」 | 删键 + AST 反向红线（矩阵每个键必须被 `need()` 用到） | `doc2md/auth.py` |
| **R19-API-6** | P2 | 一行损坏的 `meta_json` 让 `GET /docs/<id>` 变 500 | 降级 `{}`；**但不推广到块级 IR 字段**（flags/marks/links/font/provenance）——那是正文，静默清空等于把「内容丢了」伪装成成功。这条**不对称**由红线第二半专门守住 | `doc2md/api.py`、`doc2md/storage/repo.py` |
| **R19-SVC-1** | P1 | `PUT /settings` 只认嵌套对象，而附录 A 与 `config.get` 的键名口径都是点号串：平铺 `{"logs.rotate_mb": 11}` 被当字面顶层键写进 override，读取侧按嵌套逐段走永远取不到——回 200、文件里躺着错键、有效值纹丝不动。`config.unknown_keys` 的 `continue` 还把平铺拼错分表整条吞掉（实测返回 `[]`） | `_expand_dotted` 展开后两形同归一路；`unknown_keys` 报**最短未知祖先前缀** | `doc2md/api.py`、`doc2md/config/loader.py` |

架构不变式的三处收口（ARCH-2 / ARCH-8 / ARCH-9）在 §五，因为它们改正的是**文档对架构的承诺**，不是代码行为。

---

## 三、审阅页（R19-GUI-1…4）

**GUI 面本机无浏览器**，故按本仓既有口径用**源码静态断言**钉住（`test_ch469_service.py` 扫 innerHTML 模板、`test_ch496_local.py` 等价 `node --check`），并把浏览器人工走查登记 **B-11**。四条都是「读源码即可判定真伪」的形态，不需要运行时才能确认——但**这不等于已做人工走查**。

| 编号 | 严重度 | 问题 | 处置 |
| --- | --- | --- | --- |
| **R19-GUI-1** | P1 | `SRCH` 是顶层 `const`，fetch 成功后整体赋值抛 TypeError，令「检索」**静默失效** | `Object.assign(SRCH, …)` |
| **R19-GUI-2** | P1 | 审计面按 `rowid` 升序且该方向被 R17-API-5 冻结，`?limit=N` 因此取到**最旧** N 条，而面板标「最近」、`showHistory` 还要给「最新可回滚版本」 | 新 `auditNewest()`：先读 `total`，溢出才按 `offset=total-N` 取最后一页（未溢出单请求） |
| **R19-GUI-3** | P2 | 终态行耗时原为 `finished - created`，把排队等待又算了一遍（同一时段出现在两列、与 running 行的 `now - started` 不同源） | `finished - (started \|\| created)` |
| **R19-GUI-4** | P2 | `#tok` 的 `input` 直挂 `loadQueue()+loadDocs()`：64 位 hex 逐字输入 = 128 次请求，本机实测 401 秒内 86 次请求即 86 次真实库查询（鉴权每请求查库、无缓存） | 400ms 去抖 + 同值去重。**不换 `change`**——那是 UI 行为变更，须走人工浏览器验收 |

---

## 四、判定层（决定「哪条缺陷进单子」的一层）

| 编号 | 严重度 | 问题 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| **R19-QA-1** | P0（判定面） | `mdscore.LINT_CLASS` 把 MD040 记作 `contract`，依据引作 `MARKDOWN_SPEC`——仓库里**从来没有这个符号**。但 PDF / DOCX 都不携带代码语言元数据、`Block` 也没有 `lang` 字段：S4 无从渲染上游不存在的值，裸围栏是**忠实还原**，正落在 dialect 档自己的判据里。改档前实测：两个代码样本 `lint_contract` = 0.375 / 0.167 对门禁 0.95，是本轮**唯一** `blocking=True` 的 P0；归因层把 0.226226 总损失里的 0.177842（**78.6%**）记到 `S4_render` 并判 `major` | 改档 `contract → dialect`；红线**双向**钉「`Block` 有 `lang` 字段 ⟺ MD040 属 contract」（只钉一侧会退化成空跑），并断言改档后 `lint_report` / `defect_scan` 仍命中——可见性没有下降 | 改档后实测：`gate_failures=[]`、`lint_contract` 宏平均 1.0、`S4_render` 归因 0.000000、平均 total_gap 0.010283 → 0.002304 |
| **R19-QA-2** | P2 | `defect_scan` 的重复锚点证据串把**已被否掉**的成因（TOC 缓存误判）写死进报告，还把逐次命中截成 `texts[:5]`（40 个同名标题抄五遍） | 按「不同标题 → 重数」报告、截断显式告知、成因交源文档复核 | `tests/qa/defect_scan.py` |
| **R19-QA-3** | P2 | `CORPUS_KNOWN_BENIGN` 冻结在测试侧；而 `prioritize.py` 要据此区分「可施工的缺陷」与「语料设计的预期形态」，生产侧工具去 import `test_` 模块是反向依赖 | 挪到扫描器侧；计数不因标注而减少，只改下游处置 | `tests/qa/{defect_scan,prioritize}.py`、`tests/test_ch503_qa_face.py` |
| **R19-QA-4** | P2 | 证据串引用不存在的 `MARKDOWN_SPEC` | 改指 `mdscore.LINT_CLASS` + `MD040_DIALECT_REASON` | `tests/qa/defect_scan.py` |

---

## 五、文档层（10 处收口，判据一律「以可复现实测为准」）

- **R19-DOC-1（活文档精度 + 交叉引用可解析，配红线）**：三处漂移即修 —— ① `dev-plan` §3.5 记 `editor.html` **391 行**、实 629；② `technical-reference` 记 **515 行**、实 629（第十八轮 +114 后只更新了附录 G）；③ `dev-plan` §3.4/§3.6 前端行仍写「登记 B-36 待人工浏览器验收」，而 B-36 早在 CH-503 已关闭并做过实机走查——这是「能力先落地、活行不自动刷新」同族病灶的第三次复发。新增**第七项文档红线**：活文档里的 `文件.py::符号` 交叉引用必须真实存在（`tests/test_ch491_doc_sync.py::test_cross_references_resolve`，带负夹具自证），并把 `文件.py:N` 行号引用做成**棘轮**（只减不增，基线冻结在测试内；存量清零见 **B-50**）。**为什么不给行数加红线**：`wc` 作为断言等于给每次改动加一道机械销账，收益只有「发现采样太早」一件事——那件事由「`wc` 复算必须发生在内容定型之后」这条收口纪律防。
- **R19-DOC-2（附录 C 契约六处，按 live API 探针实测改正）**：`C-1` `POST /jobs` 的 **409 不可达**（`E4091` 的两个抛出点都被就地捕获、折进 202 逐文件信封；`E4091` 类声明的 `http=409` 是段位登记，`E4092` 同理）；`C-2` `DELETE /docs` 实回 `{deleted:{blocks,events,audit}, doc_id, files:[…]}`（`cascade` 只出现在 `GET /audit/deletions` 的逐行里）；`C-3` 逐文件状态四形态键集（同步成功**无 `status` 键**、`status:"ok"` 不存在，`attached` / `idempotency_hit` 是并列布尔）；`C-4` 响应错误体的 `detail` 是**可缺省**的（`to_body` 对空 detail 省略该键）；`C-5` 幂等重放返回 `status="done"` + `idempotency_hit=true` 且**不重跑转换**，与首次响应的缺省形态不同；`C-6` 扩展名过滤只对受支持扩展名成立（`.txt` / `.md` 被静默忽略——实测「1 pdf + 1 txt」回 202 且 `jobs` 仅一条）。
- **R19-DOC-3（架构段计数与复核命令可复算）**：`ARCH-9` 端点计数改为 AST 口径 **27 个装饰器 = 21 端点 + 6 个 `errorhandler`**；函数体内包内导入由七个旧数改成本机实测 **`cli.py` 42 / 全包 77 / batch 5 / repo 5 / artifacts 4 / worker 4 / cron 3 / api 2**（旧七个数里只有 `api.py` 的 2 是对的）。`ARCH-8` 复核命令原以 `^from` 起锚、漏掉 `import x` 形态，改为 AST 口径后实测 **185 条包内相对导入边、0 条向上引用**。
- **R19-DOC-4（B-44 根因补登）**：塌词的病灶不在「run 拼接不补空格」，而在 `s1_parse.py::_row_cells` —— 入口过滤 `cs = [c for c in chars if (c.get("text") or "").strip()]` **先把空白字符丢掉**，再 `"".join(seg)` 拼回来；DOCX 路径保留空格（`w:t` 原文），两条路径因此对同一表格给出不同文本。受影响样本 `hard_05_leader_dots`、`hard_08_header_variants`（8 行）、`pdf_10_two_column_6p`（6 行）；下游连带 `if ln.row: lvl = None`（`s3_structure.py`）把目录行判成表行、标题全灭。**登记与 OPT4-17 合并**，仍属 B-44 的人工真值裁定面。
- **R19-DOC-5（ARCH-2 架构不变式改正）**：§3 纪律第 2 条原写「`pipeline/*` 与 `storage/*` 互不引用」——**这是假的**。AST 实测反向有两条边，都在 `pipeline` 侧**只读**：① `pipeline/batch.py::_skip_store` 函数体内延迟导入 `storage.db.Store`（`--skip-existing` 的进程内缓存，池 worker 常驻，千件批不该每件重放 DDL）；② `pipeline/convert.py::_edited_conflict` 直接开只读 `sqlite3` 连接查 `blocks.revision>1`（E4091 预检，查询失败 fail-closed）。裁定：**不改代码**（两处都刻意绕开 repo，且有性能/失败语义理由），把纪律改为成立的单向表述「`storage/*` 从不引用 `pipeline/*`」，并把两处反向读取逐条登记，注明「改库表结构必须回头看这两处」。
- **R19-API-2 / API-3（行号引用 → 符号引用）**：活文档里 6 处 `文件.py:NNN` 实测**全部漂移**（附录 C 的 7 处 `api.py:NNN` 有 3 处已漂移、doctor 段的 6 处 `cli.py:NNN` 指向了完全无关的函数）。整段改为只引函数名，机器可复核；存量棘轮见 B-50。
- **R19-ARCH-7（撤销，不实施）**：`auth.issue()` 混用 `ValueError` 与 `E4001_BadRequest` 两种约定。但既有红线就是 `pytest.raises(ValueError, match="bad role")`、docstring 即契约，两个调用方都已预校验——改异常类型既破坏既有测试又改对外承诺，按「与既有测试兼容、避免破坏性重构」的口径**撤销并记录裁定**，不冒充缺陷修复。

---

## 六、变异证明（红线自己的牙齿）

纪律：每条修复退回原样后对应红线必须转红；文件在每次变异后**逐字节（md5）还原**；不用 `git checkout`（本会话改动尚未提交）。

| 组 | 变异 | 结果 |
| --- | --- | --- |
| 服务面 M1–M14、M4b | M1 游标重新被单页上限夹 / M2 负值不再被挡 / M3 日报退回 try 之外 / M4 与 M4b `FileNotFoundError` 与真 `OSError` 两个方向 / M5 `cascade_json` 不再降级 / M6 `close` 重新替换 `threading.local` / M7 `meta_json` 不再降级 / M8 `role` 退回缺省 reader / M9 `rerun` 死键复活 / M14 矩阵外动作退回放行 / M10 `SRCH` 退回整体赋值 / M11 审计面板退回直连最旧一页 / M12 终态耗时退回 `created` 起算 / M13 token input 退回每键直发 | **14/14 RED**（逐条命中对应的那一条测试），还原核对全绿 |
| SVC-1 / API-4 M15–M17 | M15 `unknown_keys` 退回「父分表未知即跳过」 / M16 `put_settings` 不再展开平铺键 / M17 Content-Type 的 400 退回无 detail | **3/3 RED**，两文件 md5 与变异前一致 |
| 复合 M18 | 拼错分表测试有**两个独立守卫**（展开层 + loader 层），任何单点变异都不转红——用「去掉展开 + 回退 loader」的复合变异证明它有牙齿 | **RED**（`200 != 400`），还原核对 `restored: True`。**如实记录：M15b / M16b 两条单点变异测出 GREEN，未被当作证明**，因为它们各自都被另一层挡住——这不是空跑，而是「修复冗余」的实证 |
| 编号 PIPE-3 M-N1…M-N3 | M-N1 删整条守卫 / M-N2 只留 `numFmt=none` 半 / M-N3 恢复 `marker or f"{counters[ilvl]}."` 兜底 | M-N1 **RED（2 failed / 2 passed）**；M-N2 **RED，恰好只红 `test_empty_lvltext_does_not_fabricate_marker`**（两半独立守卫）；M-N3 **GREEN**（4 passed、门禁 683 全绿）——**如实报告：这一处是装饰性死代码清理，不承重**，不冒充修复 |

另有两组「非变异」取证：PIPE-1 由**两次打包 sha 不同**（旧码）对**两次相同**（新码）直接证明；PIPE-2 由 31 篇夹具的 `mono=6 link=2 BOTH=0` 证明其为潜伏缺陷，红线改用合成 `Line`。

---

## 七、B 类与本机以外的受限项

**本轮新增 / 状态变化**：

| 项 | 状态 | 受限原因 / 出口条件 |
| --- | --- | --- |
| **B-50** | **新增登记**（本机可执行、未清零） | 活文档 `文件.py:N` 行号棘轮：本轮只保证「不新增」并把碰到的引用改为符号形式；存量需分批清零，每批下调一次基线。`docs/qa/round*/` 与历史审查报告（约 718 处）**不计入**分母——它们是当时快照，改写会销毁取证价值 |
| **B-11** | **追加范围** | R19-GUI-1…4 的源码静态断言不能代替**浏览器人工走查**（尤其 GUI-4 的去抖时序与 GUI-2 的翻页行为）；跨浏览器 / 跨平台仍按原登记 |
| **B-38** | 维持（第二步） | 页区间并行的产品内实施需产物字节与执行形态同批升位 + 远端六作业 / Windows 腿复验授权 |
| **B-43 / B-44** | **维持，发布阻断未解除** | 本轮把 B-44 的塌词根因定位到 `_row_cells` 入口过滤并登记，但**正确列归属与目录形态阈值需人工逐页真值**，本机造不出；B-43 的跨页表整列脱落同理 |
| **B-49** | 维持 | `work/real_samples` 为空 ⇒ `real_leg` 恒 `not_run`，这是诚实状态不是缺陷 |
| **OPT4-17 / OPT4-18** | 新增待办 | `PERF-2` 的二次复杂度与 `PIPE-2` 的 fence/indent 残余：前者随 B-44 同批、后者需新 W 码与附录 D 预留 |

**本轮远端取证缺口**：CH-504 的改动尚未提交（本次审查按约定不 commit / push），GitHub Actions 三档作业的远端复证只能在 push 之后产生。**本报告不以任何本机绿灯代替远端结论**，也不把「Windows CI 腿对 `create_system` 的确认」算作已验——那是跨 OS 面，本机只能证明 macOS 侧稳定。

---

## 八、结论与出口

1. **三条产物级修复里只有两条改变交付字节**：PIPE-1 改容器（可复现性）、PIPE-3 改正文（去掉凭空生成的编号），PIPE-2 是潜伏缺陷（本机语料零命中）。版本升位按前者记为 2.20.0；六样本等价性 6/6 证明**除此之外没有其它字节漂移**。
2. **服务面的共同形态是「单面不崩、两面矛盾」**：`offset` 被单页上限夹、终态耗时把排队算两次、`PUT /settings` 回 200 却写错键、`POST /tokens` 缺角色静默降权——这些都不是崩溃，而是**看起来成功**。判定层的问题同族：MD040 让两个代码样本成为本轮唯一 P0，而这个 P0 的依据是一个不存在的符号。这一轮把「哪条缺陷进单子」的标准重新钉回「行为是否与既有承诺矛盾」。
3. **出口口径**：`PIPELINE_VERSION` 2.20.0 / `PLAN_VERSION` 3.38 / `SCHEMA_VERSION` 14 / `DB_VERSION` 10；门禁 **683 项全绿 / 0 skipped**（642 + 41，逐文件 `--collect-only` 归因无余额）、全量 **1516 收集 = 1500 passed / 0 failed / 16 skipped（真 skip 10 + xfail 6）**、覆盖率 **7,843 / 737 = 90.60%**、语料回归 **22/22 逐字节一致**、六样本等价性 **6/6**、`ruff` 全绿、`doctor` 退 0。
4. **发布判定：仍为阻断**。B-43 / B-44 依旧是 `ir.warnings` 零告警的静默结构损伤，本轮没有让它们变绿——它们是人工逐页真值项，档位只看「行为是否与既有承诺冲突」，不因本轮 683 项全绿而改判。
