# 「不得出现」型断言存量清单（OPT4-10 / CH-507）

日期：2026-10-27　轮次：R19　扫描面：`tests/` 下 121 个 `.py`，AST 遍历测试函数内的 `assert`

## 口径

只收「某物不得出现」型否定断言：`assert X not in Y`、`assert not re.search(...)`、
`assert not any(...)`。`assert not <布尔名 / 下标 / 属性>` 这类状态断言（`assert not hit` / `assert not out['k']`）
不计入——它们否定的是布尔结果，不是「某物是否出现」，本项不排查。

扫描器是**线索生成器**，不是判定器。`同主语正断言` 只表示同一测试函数里存在一条
非否定断言、且与被否定表达式共享标识符；它**不能证明**检查器有牙齿（共享的可能是
无关变量），`缺`也不等于没牙齿（正夹具可能在同一文件的别处或 fixture 里）。真正的
判据仍是 OPT4-10 纪律②：同一检查必须在应当命中的地方确实命中，且该命中数要打进输出。

**纪律③（CH-517 补）：成对判据的「命中数」本身要按宿主中立口径取。** CH-511 第二批把代理泄漏
判据改写成成对形态时，注入面写成字面列表比对（`[k for k in _PROXY_VARS if k in os.environ] ==
[injected]`），而 Windows 的 `os.environ` 键大小写不敏感（CPython 的 `_Environ` 用
`encodekey=str.upper`），注入 `HTTP_PROXY` 后 `http_proxy` 也算命中——同一份 735 项在 Linux 三档
`merge-gate` 与本机 macOS 全绿，只在 `windows-suite` 门禁层连红六推（CH-511～CH-516）。所以凡是
「数一数命中了几个」的判据，键名要做大小写归一、路径要做分隔符归一、编码要做口径归一，并把**实际值
与期望值同时**打进失败消息；`windows-suite` 在门禁层零跳过（CH-498 立的口径）正是让这一类病灶当场
显形、而不是被 skip 藏起来的机制。

`控制流内` 标出位于 `for` / `while` / `if` / `try` / `with` 内的断言——这是 O-b 型
「前件恒假」最常落脚的位置（循环集合为空时断言根本不执行），列为首批复核对象。

## 汇总

| 项 | 数 |
| --- | --- |
| 否定断言总数（含 out-of-scope 的 `not <布尔名>`） | 206 |
| 本项扫描面（`not in` / `not re.*` / `not any`） | 153 |
| 其中 `not in` | 137 |
| 其中 `not re.search` / `not any` | 3 / 13 |
| 红线层（gate 标记） | 82 |
| 同函数内有同主语正断言（候选有牙齿） | 18 |
| 位于控制流内（O-b 型前件恒假的首批复核对象） | 20 |
| 既无同主语正断言、也不在控制流内（需人工读） | 115 |
| out-of-scope：`assert not <布尔名/下标/属性>` | 53 |

### 补位进度（B-53 分批，本清单随批次更新）

| 批次 | 文件 | 补位数 | 剩余（需人工读） |
| --- | --- | --- | --- |
| CH-510（首批） | `tests/test_ch469_service.py` | 3（1 条 gate + 2 条非 gate） | 112 |
| CH-511（第二批） | `tests/test_v24_gates.py` | 4（全为 gate：#79/#80/#81/#82） | 108 |
| CH-512（第三批） | `tests/test_ch500_round17.py` | 6（全为 gate：#38/#39/#40/#41/#42/#43） | 102 |
| CH-513（第四批） | `tests/test_ch476_fixes.py` | 6（全为 gate：#9/#10/#11/#12/#13/#15） | 96 |
| CH-514（第五批） | `tests/test_llm_p0_contract.py` | 5（全为 gate：#68/#69/#70/#71/#72） | 91 |
| CH-515（第六批） | `tests/test_ch501_qa_auto.py` | 4（全为 gate：#44/#45/#46/#47） | **87** |
| CH-516（第七批） | `tests/test_ch504_qa_caliber.py` | 4（全为 gate：#54/#55/#56/#57） | 83 |
| CH-518（第八批） | `tests/test_ch504_service_faces.py` | 4（全为 gate：#58/#59/#60/#61） | **79** |
| CH-519（第九批） | `tests/test_ch473_fixes.py` | 4（全为 gate：#2/#3/#4/#5） | **75** |
| CH-520（第十批） | `tests/test_ch503_furniture_wrap.py` | 3（全为 gate：#51/#52/#53） | **72** |
| CH-521（第十一批） | `tests/test_ch503_docs_panel.py` | 3（全为 gate：#48/#49/#50） | **69** |
| CH-522（第十二批） | `tests/test_ch496_local.py` | 3（全为 gate：#34/#35/#36） | **66** |

首批实测出一条**判据本身失真**的形态：#89 原口径 `json.dumps(body)` 用默认
`ensure_ascii=True`，中文查询被写成 `\uXXXX`，`'例外' not in ...` 对任何中文查询
恒真——不是「实现干净」，是断言看不见缺陷。教训：**否定断言的字符串口径要与被测
数据的字符集一致**，同主语正断言（把缺陷注入同一份产物再计数）是唯一能证明这点的
形态。

第二批（CH-511）实测出同一病灶的**另一头**：#80/#81 的判据读的是日志**原文**，中文 needle 能不能被看见不由
测试这一侧决定，而由产品 writer 是否 `ensure_ascii=False` 决定——所以正例不能只在测试里自证，必须**走同一个
sink 类**（`api.py` 用的就是 `RotatingJsonlWriter`）：短字段原样命中 1、长字段只剩 `<sha256:` 1，两个数同时成立
才证明「读原文」这个口径真有牙齿。#82 是控制流内循环判据在干净宿主上恒真，正例改为先注入一个代理变量让
同一判据命中 1，再驱动夹具本体清除、判据归零；夹具在 pytest 9 被包成 `FixtureFunctionDefinition`，取生成器本体
时用 `isgeneratorfunction` 断言兜底——取不到就红，不许静默跳过这段。

第三批（CH-512）的形态与前两批不同：六条判据读的都是**键名/字符是否出现在真实容器里**，而触发它们的缺陷形状在正常样本上压根不产生。于是补位有两个可复用的写法：① **注回真实对象而不是造一个合成 dict**——`IR.meta()` 把 `extra_meta` 平铺进交付面，所以往 `res.ir.extra_meta` 里加一个 `shard` 就能同时验证「否定面为 0」与「平铺通路是活的」（变异把 `**self.extra_meta` 摘掉，注入面立刻归零、判据红）；② **用同域的正常产物做命中基准**——`~~` 走 opt-in 档必须计 2、正文软换行必须计 1、`POST /tokens` 正常签发必须计 1 个 `token` 键，这些 > 0 的数证明判据的分子分母都在同一份产物上可数，剩下的 0 才是观测而不是恒真。
第四批（CH-513）暴露出**第三种恒真形态**，与前两批都不同：#9 原判据读 `SELECT code FROM events`，而删除竞态路径**刻意不落任何事件**（`mark` 返回 False 即放弃留痕，见 `api.py` 该分支的注释）——空表上「没有 `job_done`」对任何实现都真。这类判据的正例不能靠注入缺陷形状，只能**给同一张表补一条正常完成的作业**：正常作业必须写出 1 条 `job_done`、同一判据在竞态轮必须仍是 0，两个数进同一条消息。同批另外五条用**源产物自身**当命中基准——`w:delText` 在夹具与黄金样本的 `word/document.xml` 里各计 1（正文计 0 才叫「接受口径」，源里压根没有则那个 0 是空话）、`page_break` 判据用一份真分页 DOCX 命中 1 证明检测器是活的、块级 `flags.has_revision` 用「带信号段落数 == 3」证明写痕通路存在。方法论收获：**判据的三个可数面——被检产物、源产物、同域正常产物——任取其一命中 > 0 就足以把「看不见」与「干净」分开**；三者都取不到，才说明这条否定断言没有观测对象。
第五批（CH-514）不再是「找不到命中面」，而是**补位夹具自己没通过产品的准入门槛**：#70 的第一版正例把一条 47 字的标题放进同批，判据却恒红——`_candidate_score` 对标题的入门条件是 `len(t) > 60`，不足 60 字的标题压根不进候选，于是「补的正例」命中仍是 0。教训一：**先把命中基准跑出来再写断言**（本轮用一段独立脚本打印每条夹具的分数与 `op_schema`，确认 > 0 才落到测试里）。教训二：**命中数按实现的真实口径算，不按直觉算**——#69 的 `"marker"` 天然出现两处（`"required": ["target", "marker"]` 的表元素 + 独立的 `"marker"` 说明键），写成「应为 0 / 1」会把正确实现打红，基准是 2。教训三：#70/#71/#72 三条否定合并成一条 `assert (narrow, wide) == ([0,0,0], [1,1,1])`，变异面必须逐 op 独立（`_ALLOWED_BY_TYPE` 按类型摘一个 op、`_allowed_ops_for` 摘掉表边界过滤），才能证明三个位置**各自**有牙齿而不是共享一次红。
第六批（CH-515）立出**判据归属**这条纪律：#44/#45/#46 三条否定所在的测试里，本来就站着更强的同夹具判据（CH-501 的 `_input_keys` 异源恒等式、精确条目数 7、精确拟新号 `B-46`），变异一跑，红永远落在那几条老断言上——补上去的成对判据**根本没被执行到**，看起来"批次全绿"却拿不到归属证据。修法不是删掉老判据，而是**把成对判据排到它们之前**（`#44/#45` 提到 `assert ident["ok"]` 前、`#46` 提到 `hr["b_draft"]` 前），M-a/M-b 随即落在新行 257、M-c 落在新行 287。第二个收获是补位方向本身：恒等式与精确条目数只看得到「不该出项的记录漏进来」，看不到「该出项的记录压根不出项」——两者在单夹具上都可能是 0，所以放行轮（把 `c.pdf` 两条记录改回 `status="ok"`、其余输入一字不动）才是这条否定的另一半：两个键必须各命中 1、条目账必须 7→9，M-a2/M-b2（在 `collect` 里按 kind/rule 名单跳过这两个键）只有补位之后才会红。#47 沿用 CH-514 的教训一，先把 `W={'impact':0.6,'frequency':0.4}` 与 `score_item(1,0)=0.6 / score_item(0,1)=0.4` 跑出来再写断言，`fixability` 命中 0 与「两维各自单独打满仍落出严格介于 0 与 1 的分」命中 2 进同一条消息。第三个收获是**对照变异**：另设一条「`b_rows` 锚点失效返回 `{}`」的变异没有落在本批新判据上，而是被既有断言先抓住——line 233 打 `ValueError: §12.7 锚点缺失：解析不到任何 | B-nn | 行`，另有 342/372/520 三条同时红，说明「锚点解析成功再比值」这条 CH-477 立的纪律本身早有牙齿，本批不为它补位、也不把它记成本批的变异证据。六次变异（摘归因层 `status` 过滤 / 摘缺陷层 `status` 过滤 / 跳过 `gate_verdict` 出项 / 跳过 `split_table` 出项 / `b_rows` 改成全文扫描 / `W` 加 `fixability: 0.0`）各自转红，连同对照变异共七次还原后 `tests/qa/prioritize.py` md5 与改前逐字节一致（`855e9291…`）。

第七批（CH-516）回到**空集恒真**这个最早的头号形态，并把它钉成两个可复算的具体缺陷面：`tests/test_ch504_qa_caliber.py`（CH-504 的 R19-QA-1/2/3 口径红线）四条否定里，两条的观测对象是**派生容器**——`CONTRACT_RULES = {r for r, c in LINT_CLASS.items() if c == "contract"}`，派生一断（变异置空集）`'MD040' not in CONTRACT_RULES` 依然全绿；另两条的观测对象是**证据串里的一个专有名词**——把 `MD040_DIALECT_REASON` 从串里摘掉，`'契约' not in ev` 依然全绿，因为一条不引任何依据的串本来就长这样。于是本批的命中基准取法明确为：**否定面读集合成员时，命中基准取「同一容器里必须存在的成员数」；读专有名词时，取「同一串里必须存在的另一个名词」**，两者都留在同一个容器/同一条串上，绝不另找一个不相干的键凑数。#57 沿用 CH-515 的判据归属：成对判据前置到精确串判据 `"另有 2 种未列" in ev` 之前，M-5（截断告知恒空）与 M-6（告知恒发）两个相反方向的变异才都落在新行 137——只写「恰好 5 种不得出现截断标记」这一半，M-5 是全绿的，看得见它的其实是排在它前面的精确串判据。七次变异（`MD040` 改回 `contract` / `CONTRACT_RULES` 置空 / 证据串写回「契约类」/ 证据串摘掉改档理由 / `duplicate_anchor` 定性从句换回已作废成因 / `more` 恒空 / `more` 恒发）各自转红，还原后 `tests/qa/mdscore.py`（`3a4b0f9e…`）与 `tests/qa/defect_scan.py`（`003ee557…`）md5 与改前逐字节一致。

第八批（CH-518）的四条都在**服务面/权限面**，命中基准全部取在**同一个容器**上，并实测出正例的第三种
取法：**当否定面读的是「原始列名/内部键有没有漏进响应体」，命中基准取同一容器里契约承诺的公开键数**——
`/audit/deletions` 的行由 `dict(r)` 再 `pop("cascade_json")` 净化而来，变异把 `pop` 换成 `get` 时漏出 1 处
（判据打 `(1, 6)` 红），而变异从 `SELECT` 里摘掉一个公开键（`actor`）时公开键掉到 5/6（`(0, 5)` 红）——
后者是「响应面少了一列」的真实退化形态，只写「原列名不得出现」的话它全绿。#59 用的是**同容器的
另一个键**：`rerun` 是 CH-504 删掉的死键，产品真正的重跑口径是 `POST /jobs` 带 force → `submit_job`，
于是判据钉 `(0, 1)`——加回 `rerun` 打 `(1, 1)`、摘掉 `submit_job` 打 `(0, 0)`，后者同时说明「矩阵里压根
没有重跑面」时前一条否定只是在空集合上恒真。#60 是 CH-516「读专有名词就取同一条串里的另一个名词」的
继续：`durs[1]` 里 `finished - created` 与 `started` 并进一条 `(0, 1)`，变异成 `finished - ts` 时两个数
打 `(0, 0)` 才红——原来那条「不含 finished - created」对这种写法完全无感。#61 把成对判据**前置**到
`body["code"]` / `body["detail"]` 三条值判据之前：`errors.to_body()` 丢掉 `detail` 键时那三条会先红，
前置后红落在新行 531 的 `(0, 2)` 上。本轮共跑九次变异（`pop`→`get` / SELECT 摘 `actor` / 加回
`rerun` / 摘 `submit_job` / 耗时退回 `finished - created` / 耗时换成 `finished - ts` / `to_body` 回显
`token` / `to_body` 丢 `detail` 八条落在本批新判据上，各自转红；另设「降级分支把整行抹平」作对照——它的红
落在测试自己的行收集表达式（`{d["doc_id"]: ...}` 抛 KeyError）而非新判据，故不计入本批牙齿证据）。全部
还原后 `doc2md/api.py`（`1657445f…`）、`doc2md/auth.py`、`doc2md/errors.py`、
`doc2md/static/editor.html` 四个文件 md5 与改前逐字节一致。

第九批（CH-519）打出**第四种恒真形态：在只装幸存者的容器上数被丢弃者的标记**。`test_pipe6_real_short_block
_before_longer_not_replaced` 原判据 `not any(b.flags.get('dropped_dup_of') for b in out)` 读的是 `overlap_dedupe`
的返回值，而该函数按构造把被丢弃块留在结果之外——标记只写在被丢弃的那个块对象上，`out` 里永远数不到，
所以对任何实现都真（把产品改成「锚定表覆盖所有块」，误判确实发生了，这条否定依然全绿：红的是它后面那条
精确列表判据）。正例面因此不能向 `out` 要，只能**用同一夹具另造一轮必然丢弃的场景**（片界 `[1,3]` 的截断块
必须被记一次标记），判据写成 `(clean, hit) == (0, 1)` 且**前置**到精确列表判据之前，M-6（锚定表覆盖所有块）/ M-7（替换时不写标记）/
M-8（截断分支整体短路）/ M-9（锚定轮不传片界，`[1,3]` 改回 `[3]`）四个方向的变异才都落在新行 173。

同批三条 v2.2 迁移前自检是 CH-516 规则①在 **schema 列集合**上的落地：判据读「`revision` / `role` / `aux`
不在同一容器的列名集合里」，命中基准就取「同一容器里 v2.2 形状必须存在的列数」（blocks 5、tokens 4、
search_blocks 3，实测取来而非推断）——把建表脚本写坏成缺列的空表，三条否定会一起全绿。三条面按 CH-514 的
独立变异面纪律合并成一条 `(leak, base) == ([0, 0, 0], [5, 4, 3])`，五个面各自单独打变异、各落新行 1223 转红：
漏出面 M-1 `revision` 没摘 → `[1, 0, 0]`、M-2 `role` 没摘 → `[0, 1, 0]`、M-3 `aux` 没摘 → `[0, 0, 1]`；基准面 M-4 `blocks.text` 改名 → `[4, 4, 3]`、M-5 `tokens.label` 改名 → `[5, 3, 3]`。另设一条
**对照变异**：把 blocks 的 `revision` 摘除动作直接失效（不摘），红并不落在本批判据上，而是先被建库夹具的位置插入拦下
（第 1209 行打 `sqlite3.OperationalError: table blocks has 12 columns but 11 values were supplied`）——按第六批
立的纪律不计入本批牙齿证据，但它说明夹具自己的 INSERT 是这一族断言的第一道守门人；漏出面因此必须**保列数**打（M-1/M-2 把 strip 目标改摘另一列，让 `revision` / `role` 留下而列数不变，判据才收得到）。九个变异各自转红，
还原后 `tests/test_ch473_fixes.py`（`e99058bc…`）与 `doc2md/pipeline/sharding.py`（`90023f58…`）md5 与改前逐字节
一致：产品语句零变化，`PIPELINE_VERSION` 保持 2.22.0，gate 仍 **735**、全量仍 **1575**、覆盖率仍
**7,942 stmts / 678 miss**。剩余 **75** 条。

第十批（CH-520）是本族批次里第一个**纯单元夹具**文件（手工构造 `RawPage`，不拉语料也不建库），三条否定读的都是
「被剔除的那一行不该出现在幸存者列表里」。规则④在这里不必另造场景就能取到正例面：**同一主语在输入全集里的出现数**
（三条实测都是 2），于是判据统一写成 `(输入, 幸存) == (2, 0)`。但本批实测出规则④的**残余漏洞**：`(2, 0)` 对
「整带清空的黑洞实现」同样是绿的——把边距带连正文一起删光，两个数照样成立。所以正例面必须**两半**：输入计数
证明这条否定有观测对象，同夹具的「不该被删的东西还在」证明剔除是**有条件的**。#52 原先除了那条否定什么都没有，
故单独补一条正文存活面（`(2, 2)`，新行 109）；#51/#53 的同一函数里本来就有正文计数断言，本批不重复补。
六个变异各自转红：M-a `_strip_page_furniture` 整体失效 → 三条落新行 83 / 101 / 158，消息打「剔除后剩 2 处」；
M-b 整页清空黑洞 → **只有新行 109 的正文存活面响**（旧写法「`准流程。` 不在结果里」在这种形态下全绿）；
M-c `_is_wrap_tail` 恒真（豁免面放宽到页眉） → 三条 + `test_is_wrap_tail_caliber` 红；
M-d 夹具把 `_banded` 的 `top` 移出边距带（家具信号根本接触不到候选） → 三条红；
M-e 夹具只留一页（重复计数到不了 2） → 三条红；
M-f 夹具里主语串整体消失（`_banded` 前缀探针） → 三条打「在输入全集出现 **0** 处」，而**旧写法 `not in kept`
在这个形态下恒真**——这正是本批判据要挡的空壳，也是规则④存在的理由。
另设**对照变异** C-1（`_is_wrap_tail` 恒假）：红落在①侧（折行尾巴必须保留）的既有断言 63 / 126 / 参数化六档 /
端到端四面上，不落本批任何新判据——按第六批立的纪律不计入本批牙齿证据，同时说明这三条成对判据只管②侧，
没有越位去抢①侧的归属。还原后 `doc2md/pipeline/s3_structure.py`（`1f04782d…`）md5 与改前逐字节一致：
零产品改动，`PIPELINE_VERSION` 保持 2.22.0，gate 仍 **735**、全量仍 **1575**、覆盖率仍
**7,942 stmts / 678 miss**。剩余 **72** 条。

第十一批（CH-521）三条都在**静态判据**上，命中基准取「同一容器里必存的成员数」，并实测出**锚点变异的自证陷阱**：
把收尾锚点 `function dAutoToggle` 改名成 `dAutoToggleRenamed` 时，`partition("\nfunction dAutoToggle")` 仍在前缀处
切开——**保前缀改名不是切断锚点**，这条变异以 rc=0 通过，只能作对照、不能记成牙齿证据；要真正打破边界必须整行
删除或改写锚点（M-c' 随即打在 `assert _sep` 的 `assert ''` 上）。三条的配对形态：#48 在 loadDocs 容器内数
「依赖成员 4/4 + `QSTATE.tasks` 0」——旧写法 `src.split("async function loadDocs")[1]` 取到的是该函数之后的
**整段文件**，函数搬空后负例面照样绿；#49 用**同一份夹具只摘 `esc()`** 把「0 命中」钉成「因为转义才 0 命中」
（摘掉后必须恰好 2 命中，M-d' 把 `format` 从字段集里拿掉时只打新行 135、`bad` 面仍绿，隔离出正例面的归属）；
#50 在 cmd_convert 容器内数「产出面成员 4/4 + `save_ir` 0」，同轮实测 `save_ir` 在本文件别处共 5 次——「0」说的是
这条路径不落库，不是符号不存在。六个变异 M-a（loadDocs 注入 `QSTATE.tasks`，落新行 85）/ M-b（删
`DSTATE.hasMore`，落新行 85 的 3/4）/ M-c'（整行删除锚点，落 `assert _sep`）/ M-d'（字段集漏 format，落新行 135）/
M-e（cmd_convert 调 `save_ir` 落库，落新行 274）/ M-f（`run_batch` 成员改名，同落新行 274）各自转红，另设对照
C-1（loadDocs 体内加无害注释，保持绿）。还原后 `doc2md/static/editor.html`（`b8fe2d0e…`）与 `doc2md/cli.py`
（`bfea7fdb…`）md5 与改前逐字节一致：零产品改动，`PIPELINE_VERSION` 保持 2.22.0，gate 仍 **735**、全量仍 **1575**、
覆盖率仍 **7,942 stmts / 678 miss**。剩余 **69** 条。

第十二批（CH-522）三条同为**静态判据**，命中基准取「同一容器里必存的成员数」，两处容器不是函数体而是
**同一页面里的两个函数**与**onclick 切片**：#34 把 editor.html 的 `showHistory` / `rollbackTo` 各自收口，逐容器数
「DOM/回滚成员 3/3 + `innerHTML` 0」（旧写法只切 `showHistory` 一个函数，`rollbackTo` 搬到别处后负例面照样绿）；
#35 在 `cmd_serve` 容器内数「绑定/报告成员 3/3 + `app.run(host=` 0」；#36 在 chip 的 onclick 切片里数
「服务端过滤成员 3/3 + `renderQueue(QSTATE.tasks)` 0」。本批实测出**正例成员不得取自注释**：chip 切片里 `status=`
只出现在一行注释中，拿它当成员等于让注释替实现作证，改取 `QSTATE.filter = ` / `loadQueue()` /
`(QSTATE.filter === st` 三个实现串。七个变异 M-a（showHistory 注入 `innerHTML`）与 M-b（rollbackTo 注入 `innerHTML`）
各落新行 249 的 `(3, 1)`；M-c（全文件 12 处 `document.createElement(` 改名，制造成员缺失）落新行 249 的 `(2, 0)`；
M-e（cmd_serve 注入 `app.run(host=`）落新行 286 的 `(3, 1)`；M-f（onclick 切片注入 `renderQueue(QSTATE.tasks)`）落
新行 329 的 `(3, 1)`；M-d（cmd_serve 的 `make_server(` 改名）与 M-g（切片里 `loadQueue()` 改名）分别先被同函数里既有的
正例断言拦在旧行 281 / 327——按第六批纪律记**判据归属**、不计入本批牙齿证据，同时说明新判据与旧正例同侧
但不抢归属；另设对照 C-1（两处函数体内加无害注释，保持绿）。还原后 `doc2md/static/editor.html`（`b8fe2d0e…`）与
`doc2md/cli.py`（`bfea7fdb…`）md5 与改前逐字节一致：零产品改动，`PIPELINE_VERSION` 保持 2.22.0，gate 仍 **735**、
全量仍 **1575**、覆盖率仍 **7,942 stmts / 678 miss**。剩余 **66** 条。

## 红线层优先复核（gate 且无同主语正断言）

| 文件::测试 | 行 | 形态 | 被否定表达式 | 控制流内 |
| --- | --- | --- | --- | --- |
| `tests/test_ch469_service.py::test_editor_log_sink_is_text_content` | 620 | not-in | `'<img src=x onerror=' not in html`（**CH-510 已补同主语正断言**） |  |
| `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'revision' not in {r[1] for r in con.execute('PRAGMA table_info(blocks)')}`（**CH-519 已补同容器必存列面：blocks 5 / tokens 4 / search_blocks 3 必须计满，三面合并判据 `([0, 0, 0], [5, 4, 3])`**） |  |
| `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'role' not in {r[1] for r in con.execute('PRAGMA table_info(tokens)')}`（**CH-519 已并入同一条三面计数判据**） |  |
| `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'aux' not in {r[1] for r in con.execute('PRAGMA table_info(search_blocks)')}`（**CH-519 已并入同一条三面计数判据**） |  |
| `tests/test_ch475_fixes.py::test_idempotent_replay_skips_save_ir_no_conflict` | 95 | not-in | `'status' not in j1` |  |
| `tests/test_ch475_fixes.py::test_scrub_record_fingerprints_long_bytes` | 175 | not-in | `'正文' not in out` |  |
| `tests/test_ch476_fixes.py::test_sync_jobs_reject_when_task_row_gone_before_commit` | 238 | not-in | `done_hits = codes.count("job_done")`（**CH-513 已补同主语正断言**：同表补一条正常作业，判据 `(0, 1)`） |  |
| `tests/test_ch476_fixes.py::test_no_drift_when_content_matches_even_if_source_vanishes` | 651 | not-in | `clean_hits = list(detail).count("content_drift")`（**CH-513 已补同主语正断言**：同函数补一条真漂移作业，判据 `(0, 1)`） |  |
| `tests/test_ch476_fixes.py::test_gap1_inserted_runs_are_extracted_and_deletions_are_not` | 1231 | not-in | `body_hits = texts.count("被删除的旧文本")`（**CH-513 已补同主语正断言**：夹具 `document.xml` 的 `w:delText` 计 1，判据 `(0, 1)`） |  |
| `tests/test_ch476_fixes.py::test_gap1_blocks_carry_has_revision_flag` | 1256 | not-in | `plain_hits = list(plain.flags).count("has_revision")`（**CH-513 已补同主语正断言**：同域带信号段落计 3，判据 `(0, 3)`） |  |
| `tests/test_ch476_fixes.py::test_gap1_golden_revision_sample_end_to_end` | 1282 | not-in | `md_hits = r.markdown.count(must_not)`（**CH-513 已补同主语正断言**：黄金样本 `document.xml` 每条 `w:delText` 计 1，判据 `(0, 1)`） | 是 |
| `tests/test_ch476_fixes.py::test_gap2_note_text_survives_and_never_glues_into_body` | 1434 | not-in | `note_pb_hits = sum(1 for b in ir.blocks if b.type == "page_break")`（**CH-513 已补同主语正断言**：同函数用真分页 DOCX 走同一判据计 1，`(0, 1)`） |  |
| `tests/test_ch478_fixes.py::test_same_size_numbered_items_are_list_items_not_headings` | 30 | not-in | `item not in heads` | 是 |
| `tests/test_ch478_fixes.py::test_markdown_keeps_ordinal_for_numbered_items` | 55 | not-in | `'- 1. 安装依赖' not in md` |  |
| `tests/test_ch480_resident_assets.py::test_serve_stays_on_loopback_on_both_platforms` | 144 | not-in | `'--port' not in args` | 是 |
| `tests/test_ch480_resident_assets.py::test_serve_stays_on_loopback_on_both_platforms` | 147 | not-in | `'--port' not in _find(act, 'Arguments').text` | 是 |
| `tests/test_ch481_capture_clip.py::test_clip_oserror_recorded_not_raised` | 146 | not-in | `'before' not in ev['launchd-serve.err']` |  |
| `tests/test_ch483_pipeline.py::test_source_name_raw_not_in_zip_ir_json` | 244 | not-in | `'source_name_raw' not in meta` |  |
| `tests/test_ch483_pipeline.py::test_source_name_raw_not_in_zip_ir_json` | 245 | not-in | `'../../evil<b>' not in ir_raw` |  |
| `tests/test_ch483_robustness.py::test_follow_symlinks_counts_escape_and_does_not_read_outside` | 115 | not-in | `str(outside) not in files` |  |
| `tests/test_ch483_service.py::test_sync_delete_during_conversion_does_not_resurrect` | 53 | not-in | `'复活正文' not in str(text)` |  |
| `tests/test_ch485_ops.py::test_netguard_install_tolerates_missing_sendmsg` | 46 | not-in | `'sendmsg' not in ng._ORIG` | 是 |
| `tests/test_ch485_pipeline.py::test_batch_error_message_does_not_echo_exception_text` | 323 | not-in | `'SECRET-BODY' not in r.get('message', '')` |  |
| `tests/test_ch492_fixes.py::test_truncated_pdf_detail_has_no_password_flag` | 71 | not-in | `'password_required' not in ei.value.detail` |  |
| `tests/test_ch495_pipeline.py::test_nested_textbox_paragraph_appears_once` | 68 | not-in | `'NESTED-FB' not in texts` |  |
| `tests/test_ch495_watch_commit.py::test_watch_tombstone_blocks_commit` | 111 | not-in | `{'code': 'job_done'} not in events` |  |
| `tests/test_ch496_local.py::test_g4_rollback_ui_contract` | 249 | not-in | `'innerHTML' not in body`（**CH-522 已补同容器必存成员面：showHistory / rollbackTo 两容器各 3 名成员必须计满且 innerHTML 计 0，判据 `(3, 0)`**） | 是 |
| `tests/test_ch496_local.py::test_serve_startup_reports_bound_port` | 286 | not-in | `'app.run(host=' not in body`（**CH-522 已补同容器必存成员面：cmd_serve 绑定/报告成员 3/3 必须计满，判据 `(3, 0)`**） |  |
| `tests/test_ch496_local.py::test_queue_chip_filter_requests_server_side_status` | 329 | not-in | `'renderQueue(QSTATE.tasks)' not in m.group(1)`（**CH-522 已补 chip onclick 容器成员面：服务端过滤成员 3/3 必须计满，判据 `(3, 0)`**） |  |
| `tests/test_ch497_local.py::test_b12_soak_harness_contract` | 120 | not-in | `'ensure_ascii=False' not in src` |  |
| `tests/test_ch500_round17.py::test_form_encoded_role_admin_does_not_downgrade_to_reader` | 99 | not-in | `leak_hits = list(r.get_json() or {}).count("token")`（**CH-512 已改写为命中数判据 + 同主语正常签发面命中 1**） |  |
| `tests/test_ch500_round17.py::test_external_link_label_newline_flattened` | 442 | not-in | `flat_hits = md.strip().count("\n")`（**CH-512 已改写为命中数判据 + 正文软换行命中 1**） |  |
| `tests/test_ch500_round17.py::test_render_strike_optin_emits_tilde_without_error` | 493 | not-in | `off_hits = render(ir).count("~~")`（**CH-512 已改写为命中数判据 + 同一 IR 的 opt-in 档命中 2**） |  |
| `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 555 | not-in | `leak_extra = list(clean_extra).count("shard")`（**CH-512 已补同主语注入面：注回 `extra_meta` 后 `meta()` 平铺必须命中 1**） |  |
| `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 556 | not-in | `leak_flat = list(res.ir.meta()).count("shard")`（**CH-512 已补同主语注入面**） |  |
| `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 568 | not-in | `leak_hits = list(meta).count("shard")`（**CH-512 已补空 dict 守卫：交付 `shard_count` 必须命中 1**） |  |
| `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 257 | not-in | `('attribution', 'gate_verdict') not in keys`（**CH-515 已并入成对判据：把 c.pdf 记录改回 `status="ok"` 的放行轮必须命中 1**） |  |
| `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 257 | not-in | `('defect', 'split_table') not in keys`（**CH-515 与 #44 同一条 `((0, 0), (1, 1))`；条目账 7→9 另立一数**） |  |
| `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 287 | not-in | `'B-99' not in pr.b_rows(_DP)`（**CH-515 已补同主语段内面：`b_rows(_DP)` 必须仍含 B-43/B-44/B-45 = 3**） |  |
| `tests/test_ch501_qa_auto.py::test_fixability_does_not_downgrade_grade` | 311 | not-in | `'fixability' not in pr.W`（**CH-515 已补同主语权重面：`W` 的两维各自单独打满必须落出中间分 = 2**） |  |
| `tests/test_ch503_docs_panel.py::test_doclist_is_served_by_the_docs_route` | 85 | not-in | `'QSTATE.tasks' not in src.split('async function loadDocs')[1]`（**CH-521 已补同容器必存成员面：loadDocs 体收口到 `dAutoToggle` 锚点，依赖成员 4/4 必须计满且 `QSTATE.tasks` 计 0，判据 `(4, 0)`**） |  |
| `tests/test_ch503_docs_panel.py::test_bare_doc_field_interpolation_is_caught_by_the_scan` | 135 | not-search | `not _BARE_DOC.search(good)`（**CH-521 已补同夹具正例面：只摘 `esc()` 的同一串必须命中 2，判据 `(0, 2)`**） |  |
| `tests/test_ch503_docs_panel.py::test_docs_empty_state_names_only_the_library_writing_faces` | 274 | not-in | `'save_ir' not in body`（**CH-521 已补同容器必存成员面：cmd_convert 产出面成员 4/4 必须计满，判据 `(4, 0)`**） |  |
| `tests/test_ch503_furniture_wrap.py::test_real_furniture_after_hard_end_is_still_stripped` | 83 | not-in | `'机密文件请勿外传。' not in kept`（**CH-520 已补输入全集同主语计数面：该串在输入必须计 2，判据 `(2, 0)`**） |  |
| `tests/test_ch503_furniture_wrap.py::test_first_line_of_document_has_no_wrap_anchor` | 101 | not-in | `'准流程。' not in _texts(stripped)`（**CH-520 已补输入计数 `(2, 0)`，并另加同夹具正文存活面 `(2, 2)`（新行 109）挡「整带清空黑洞」形态**） |  |
| `tests/test_ch503_furniture_wrap.py::test_running_header_without_terminal_punctuation_is_stripped` | 158 | not-in | `'综合长文档测试 内部测试样本' not in kept`（**CH-520 已补输入全集同主语计数面：重复计数的前提必须真到 2，判据 `(2, 0)`**） |  |
| `tests/test_ch504_qa_caliber.py::test_md040_stays_visible_after_reclass` | 71 | not-in | `'MD040' not in mdscore.CONTRACT_RULES`（**CH-516 已补同主语命中面：派生集合的成员数必须 == 契约档数，判据 `(0, 8)`**） |  |
| `tests/test_ch504_qa_caliber.py::test_fence_no_lang_rule_still_fires_and_cites_dialect` | 91 | not-in | `'契约' not in ev`（**CH-516 已补同主语理由面：`MD040_DIALECT_REASON` 在同一证据串必须计 1，判据 `(0, 1)`**） |  |
| `tests/test_ch504_qa_caliber.py::test_duplicate_anchor_evidence_reports_multiplicity_not_a_retracted_cause` | 120 | not-in | `'TOC 缓存结果被当正文标题' not in ev`（**CH-516 已补同主语的「不断言成因」限定面：必须计 1，判据 `(0, 1)`**） |  |
| `tests/test_ch504_qa_caliber.py::test_duplicate_anchor_truncation_is_announced` | 137 | not-in | `'另有' not in ds.r_duplicate_anchor('', blocks5, None)[0].evidence`（**CH-516 已前置为成对判据：7 种面必须 1、恰好 5 种面必须 0，判据 `(1, 0)`**） |  |
| `tests/test_ch504_service_faces.py::test_deletion_audit_survives_a_corrupt_cascade_row` | 196 | not-in | `'cascade_json' not in rows['d-bad']`（**CH-518 已补同容器公开键面：`doc_id`/`source_key`/`actor`/`ts`/`kind`/`cascade` 必须计满 6，判据 `(0, 6)`**） |  |
| `tests/test_ch504_service_faces.py::test_matrix_has_no_dead_action` | 304 | not-in | `'rerun' not in auth.MATRIX`（**CH-518 已补同矩阵的真实重跑键面：`submit_job` 必须计 1，判据 `(0, 1)`，并前置到 `dead`/`undeclared` 之前**） |  |
| `tests/test_ch504_service_faces.py::test_gui3_duration_column_has_one_origin` | 401 | not-in | `'finished - created' not in durs[1]`（**CH-518 已并入同支认领时刻面：`started` 在同一支必须计 1，判据 `(0, 1)`**） |  |
| `tests/test_ch504_service_faces.py::test_content_type_error_names_the_field` | 531 | not-in | `'token' not in body`（**CH-518 已补同容器契约键面：`code`/`message`/`detail` 必须计满 3，判据 `(0, 3)`，并前置到三条值判据之前**） |  |
| `tests/test_ch505_contract_sync.py::_parse_section` | 110 | not-in | `key not in rows` | 是 |
| `tests/test_ch505_contract_sync.py::test_appendix_c_parser_has_teeth` | 153 | not-in | `'409' not in rows['POST', '/jobs']['errors']` |  |
| `tests/test_ch505_contract_sync.py::test_appendix_c_parser_has_teeth` | 154 | not-in | `'422' not in rows['POST', '/jobs']['errors']` |  |
| `tests/test_ch505_escape_single_source.py::test_escape_implementations_have_a_single_source` | 139 | not-in | `'str.maketrans' not in text` | 是 |
| `tests/test_ch505_escape_single_source.py::test_escape_implementations_have_a_single_source` | 140 | not-in | `'&lt;' not in text` | 是 |
| `tests/test_llm_p0_contract.py::test_allowed_ops_hides_table_boundary_for_far_paragraph` | 120 | not-in | `far_hits/near_ops.count("mark_table_boundary")`（**CH-514 已补同主语正断言**：紧邻表的段落命中 1，判据 `(0, 1)`） |  |
| `tests/test_llm_p0_contract.py::test_payload_carries_v25_schema_and_contract` | 198 | not-in | `clean_hits = user.count('"marker"')`（**CH-514 已补同主语正断言**：含 list_item 候选批命中 2，判据 `(0, 2)`） |  |
| `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | `narrow_hits = [only_para.count(op) …]`（**CH-514 已补同主语正断言**：标题+列表项+紧邻表段落同批 `wide_hits == [1, 1, 1]`，三行合判据） |  |
| `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | 同上（`fix_list_marker` 位） |  |
| `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | 同上（`mark_table_boundary` 位） |  |
| `tests/test_pdf_columns.py::test_single_column_long_paragraph_stays_whole` | 31 | not-in | `'双栏' not in first` |  |
| `tests/test_pdf_structure_repair.py::test_wrapped_heading_continuation_joins_heading` | 155 | not-in | `'继续部分' not in [b.text for b in ir.blocks if b.type == 'paragraph']` |  |
| `tests/test_qa_n04_furniture_ext.py::test_roman_decorated_number_not_in_inline_mixed_case` | 100 | not-search | `not _PAGE_NUM_INLINE.search('第一章 引言 · 12')` |  |
| `tests/test_v24_gates.py::test_delete_cascade` | 381 | not-in | `'text' not in json.dumps(tomb[-1], ensure_ascii=False)`（**CH-511 已补同主语正断言**） |  |
| `tests/test_v24_gates.py::test_service_business_events_land_in_logs` | 461 | not-in | `'本条款' not in dumped`（**CH-511 已补同主语正断言**） |  |
| `tests/test_v24_gates.py::test_logs_contain_no_document_text` | 543 | not-in | `'本条款' not in dumped`（**CH-511 已补同主语正断言**） |  |
| `tests/test_v24_gates.py::test_gate_test_env_neutralization` | 580 | not-in | `k not in os.environ`（**CH-511 已改写为命中数判据 + 注入面归零**） | 是 |

## 全量清单（gate 在前，其余按文件 / 行排序）

| # | 层 | 文件::测试 | 行 | 形态 | 被否定表达式 | 同主语正断言 | 控制流内 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | G | `tests/test_ch469_service.py::test_editor_log_sink_is_text_content` | 620 | not-in | `'<img src=x onerror=' not in html` → `assert html.count(probe) == 0` | CH-510 ✓（同计数注入探针后 =1） |  |
| 2 | G | `tests/test_ch473_fixes.py::test_pipe6_real_short_block_before_longer_not_replaced` | 173 | not-any | `not any((b.flags.get('dropped_dup_of') for b in out))` → 输入全集计数 `(clean, hit) == (0, 1)` | CH-519 ✓（锚定截断轮命中 1） |  |
| 3 | G | `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'revision' not in {r[1] for r in con.execute('PRAGMA table_info(blocks)')}` → 三面合并 `(leak, base) == ([0,0,0], [5,4,3])` | CH-519 ✓（同容器必存列 5/4/3） |  |
| 4 | G | `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'role' not in ...`（并入第 3 行的三面计数判据） | CH-519 ✓ |  |
| 5 | G | `tests/test_ch473_fixes.py::test_test5_v22_legacy_rebuilds_fts_and_backfills_columns` | 1223 | not-in | `'aux' not in ...`（并入第 3 行的三面计数判据） | CH-519 ✓ |  |
| 6 | G | `tests/test_ch475_fixes.py::test_idempotent_replay_skips_save_ir_no_conflict` | 95 | not-in | `'status' not in j1` |  |  |
| 7 | G | `tests/test_ch475_fixes.py::test_patch_audit_actor_is_token_hash_not_plaintext` | 144 | not-in | `editor_tok not in actor` | 143 |  |
| 8 | G | `tests/test_ch475_fixes.py::test_scrub_record_fingerprints_long_bytes` | 175 | not-in | `'正文' not in out` |  |  |
| 9 | G | `tests/test_ch476_fixes.py::test_sync_jobs_reject_when_task_row_gone_before_commit` | 238 | not-in | `done_hits = codes.count("job_done")` → `assert (done_hits, ok_done_hits) == (0, 1)` |  | CH-513 ✓ |
| 10 | G | `tests/test_ch476_fixes.py::test_no_drift_when_content_matches_even_if_source_vanishes` | 651 | not-in | `clean_hits = list(detail).count("content_drift")` → `assert (clean_hits, drift_hits) == (0, 1)` |  | CH-513 ✓ |
| 11 | G | `tests/test_ch476_fixes.py::test_gap1_inserted_runs_are_extracted_and_deletions_are_not` | 1231 | not-in | `body_hits = texts.count("被删除的旧文本")` → `assert (body_hits, xml_hits) == (0, 1)` |  | CH-513 ✓ |
| 12 | G | `tests/test_ch476_fixes.py::test_gap1_blocks_carry_has_revision_flag` | 1256 | not-in | `plain_hits = list(plain.flags).count("has_revision")` → `assert (plain_hits, marked_hits) == (0, 3)` |  | CH-513 ✓ |
| 13 | G | `tests/test_ch476_fixes.py::test_gap1_golden_revision_sample_end_to_end` | 1282 | not-in | `md_hits = r.markdown.count(must_not)` → `assert (md_hits, xml_hits) == (0, 1)` |  | CH-513 ✓ |
| 14 | G | `tests/test_ch476_fixes.py::test_gap1_plain_docx_unchanged_by_revision_path` | 1253 | not-any | `not any((ln.has_revision for ln in lines))` | 1252 |  |
| 15 | G | `tests/test_ch476_fixes.py::test_gap2_note_text_survives_and_never_glues_into_body` | 1434 | not-in | `note_pb_hits = sum(1 for b in ir.blocks if b.type == "page_break")` → `assert (note_pb_hits, br_hits) == (0, 1)` |  | CH-513 ✓ |
| 16 | G | `tests/test_ch476_fixes.py::test_gap2_docx_without_notes_gains_no_lines` | 1438 | not-any | `not any((ln.note_kind for ln in lines))` | 1437 |  |
| 17 | G | `tests/test_ch478_fixes.py::test_same_size_numbered_items_are_list_items_not_headings` | 30 | not-in | `item not in heads` |  | 是 |
| 18 | G | `tests/test_ch478_fixes.py::test_markdown_keeps_ordinal_for_numbered_items` | 55 | not-in | `'- 1. 安装依赖' not in md` |  |  |
| 19 | G | `tests/test_ch478_fixes.py::test_prose_lines_do_not_enter_the_ordered_branch` | 65 | not-any | `not any(('安装依赖' in t for t in prose))` | 62 |  |
| 20 | G | `tests/test_ch480_resident_assets.py::test_serve_stays_on_loopback_on_both_platforms` | 144 | not-in | `'--port' not in args` |  | 是 |
| 21 | G | `tests/test_ch480_resident_assets.py::test_serve_stays_on_loopback_on_both_platforms` | 147 | not-in | `'--port' not in _find(act, 'Arguments').text` |  | 是 |
| 22 | G | `tests/test_ch481_capture_clip.py::test_clip_oserror_recorded_not_raised` | 146 | not-in | `'before' not in ev['launchd-serve.err']` |  |  |
| 23 | G | `tests/test_ch483_pipeline.py::test_source_name_raw_not_in_zip_ir_json` | 244 | not-in | `'source_name_raw' not in meta` |  |  |
| 24 | G | `tests/test_ch483_pipeline.py::test_source_name_raw_not_in_zip_ir_json` | 245 | not-in | `'../../evil<b>' not in ir_raw` |  |  |
| 25 | G | `tests/test_ch483_robustness.py::test_follow_symlinks_counts_escape_and_does_not_read_outside` | 115 | not-in | `str(outside) not in files` |  |  |
| 26 | G | `tests/test_ch483_service.py::test_sync_delete_during_conversion_does_not_resurrect` | 53 | not-in | `'复活正文' not in str(text)` |  |  |
| 27 | G | `tests/test_ch485_ops.py::test_netguard_install_tolerates_missing_sendmsg` | 46 | not-in | `'sendmsg' not in ng._ORIG` |  | 是 |
| 28 | G | `tests/test_ch485_pipeline.py::test_docx_numbered_table_first_column_stays_table` | 165 | not-any | `not any((b.type == 'heading' for b in ir.blocks))` | 164 |  |
| 29 | G | `tests/test_ch485_pipeline.py::test_batch_error_message_does_not_echo_exception_text` | 323 | not-in | `'SECRET-BODY' not in r.get('message', '')` |  |  |
| 30 | G | `tests/test_ch492_fixes.py::test_truncated_pdf_detail_has_no_password_flag` | 71 | not-in | `'password_required' not in ei.value.detail` |  |  |
| 31 | G | `tests/test_ch495_pipeline.py::test_nested_textbox_paragraph_appears_once` | 68 | not-in | `'NESTED-FB' not in texts` |  |  |
| 32 | G | `tests/test_ch495_pipeline.py::test_nested_textbox_paragraph_appears_once` | 71 | not-any | `not any(('NESTED' in t and t != 'NESTED' for t in texts))` | 67 |  |
| 33 | G | `tests/test_ch495_watch_commit.py::test_watch_tombstone_blocks_commit` | 111 | not-in | `{'code': 'job_done'} not in events` |  |  |
| 34 | G | `tests/test_ch496_local.py::test_g4_rollback_ui_contract` | 249 | not-in | `'innerHTML' not in body` → `assert (_hits, body.count("innerHTML")) == (len(_members[name]), 0)`，两容器各 3 成员 | CH-522 ✓（showHistory / rollbackTo 各 3 名成员计满） | 是 |
| 35 | G | `tests/test_ch496_local.py::test_serve_startup_reports_bound_port` | 286 | not-in | `'app.run(host=' not in body` → `assert (_hits, body.count('app.run(host=')) == (3, 0)` | CH-522 ✓（make_server / server_port / print 三成员） |  |
| 36 | G | `tests/test_ch496_local.py::test_queue_chip_filter_requests_server_side_status` | 329 | not-in | `'renderQueue(QSTATE.tasks)' not in m.group(1)` → `assert (_hits, m.group(1).count('renderQueue(QSTATE.tasks)')) == (3, 0)` | CH-522 ✓（QSTATE.filter / loadQueue / 过滤比较三成员） |  |
| 37 | G | `tests/test_ch497_local.py::test_b12_soak_harness_contract` | 120 | not-in | `'ensure_ascii=False' not in src` |  |  |
| 38 | G | `tests/test_ch500_round17.py::test_form_encoded_role_admin_does_not_downgrade_to_reader` | 99 | not-in | `'token' not in (r.get_json() or {})` → 同一 `POST /tokens` 的正常签发面 `list(payload).count("token")` 须为 1 | CH-512 ✓（400 面 0 / 签发面 1 双写） |  |
| 39 | G | `tests/test_ch500_round17.py::test_external_link_label_newline_flattened` | 442 | not-in | `'\n' not in md.strip()` → 同判据对「正文段落内软换行」计数须为 1 | CH-512 ✓（链接 0 / 正文 1 双写） |  |
| 40 | G | `tests/test_ch500_round17.py::test_render_strike_optin_emits_tilde_without_error` | 493 | not-in | `'~~' not in render(ir)` → 同一 IR 的 `preserve_strike=True` 档须计 2 | CH-512 ✓（默认 0 / opt-in 2 双写） |  |
| 41 | G | `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 555 | not-in | `'shard' not in res.ir.extra_meta` → 注回 `extra_meta` 后须计 1 | CH-512 ✓（干净 0 / 注入 1） |  |
| 42 | G | `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 556 | not-in | `'shard' not in res.ir.meta()` → 注入 `extra_meta` 后经平铺须计 1（平铺失效即红） | CH-512 ✓（干净 0 / 注入 1） |  |
| 43 | G | `tests/test_ch500_round17.py::test_shard_internal_anchor_absent_from_delivered_meta` | 568 | not-in | `'shard' not in meta` → 同份交付 meta 的 `shard_count` 须计 1（读到空 dict 就红） | CH-512 ✓（shard 0 / shard_count 1） |  |
| 44 | G | `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 257 | not-in | `('attribution', 'gate_verdict') not in keys` |  | CH-515 ✓ |
| 45 | G | `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 257 | not-in | `('defect', 'split_table') not in keys` |  | CH-515 ✓ |
| 46 | G | `tests/test_ch501_qa_auto.py::test_priority_partition_is_exact` | 287 | not-in | `'B-99' not in pr.b_rows(_DP)` |  | CH-515 ✓ |
| 47 | G | `tests/test_ch501_qa_auto.py::test_fixability_does_not_downgrade_grade` | 311 | not-in | `'fixability' not in pr.W` |  | CH-515 ✓ |
| 48 | G | `tests/test_ch503_docs_panel.py::test_doclist_is_served_by_the_docs_route` | 85 | not-in | `'QSTATE.tasks' not in …` → 同容器依赖成员 4/4 必计满，判据 `(4, 0)` | CH-521 ✓（成员 4 / QSTATE.tasks 0） | CH-521 ✓ |
| 49 | G | `tests/test_ch503_docs_panel.py::test_bare_doc_field_interpolation_is_caught_by_the_scan` | 135 | not-search | `not _BARE_DOC.search(good)` → 同一串摘掉 `esc()` 必须命中 2，判据 `(0, 2)` | CH-521 ✓（esc 0 / 裸插 2） |  |
| 50 | G | `tests/test_ch503_docs_panel.py::test_docs_empty_state_names_only_the_library_writing_faces` | 274 | not-in | `'save_ir' not in body` → 同容器产出面成员 4/4 必计满，判据 `(4, 0)` | CH-521 ✓（成员 4 / save_ir 0） |  |
| 51 | G | `tests/test_ch503_furniture_wrap.py::test_real_furniture_after_hard_end_is_still_stripped` | 83 | not-in | `'机密文件请勿外传。' not in kept` → 输入全集/幸存者同主语计数 `(2, 0)` | CH-520 ✓（输入全集该串 2 处） |  |
| 52 | G | `tests/test_ch503_furniture_wrap.py::test_first_line_of_document_has_no_wrap_anchor` | 101 | not-in | `'准流程。' not in _texts(stripped)` → 输入全集/幸存者同主语计数 `(2, 0)` + 同夹具正文存活 `(2, 2)`（新行 109） | CH-520 ✓（输入 2 处 + 两条正文行存活） |  |
| 53 | G | `tests/test_ch503_furniture_wrap.py::test_running_header_without_terminal_punctuation_is_stripped` | 158 | not-in | `'综合长文档测试 内部测试样本' not in kept` → 输入全集/幸存者同主语计数 `(2, 0)` | CH-520 ✓（输入全集该串 2 处 = 重复计数前提） |  |
| 54 | G | `tests/test_ch504_qa_caliber.py::test_md040_stays_visible_after_reclass` | 71 | not-in | `'MD040' not in mdscore.CONTRACT_RULES` |  | CH-516 ✓ |
| 55 | G | `tests/test_ch504_qa_caliber.py::test_fence_no_lang_rule_still_fires_and_cites_dialect` | 91 | not-in | `'契约' not in ev` |  | CH-516 ✓ |
| 56 | G | `tests/test_ch504_qa_caliber.py::test_duplicate_anchor_evidence_reports_multiplicity_not_a_retracted_cause` | 120 | not-in | `'TOC 缓存结果被当正文标题' not in ev` |  | CH-516 ✓ |
| 57 | G | `tests/test_ch504_qa_caliber.py::test_duplicate_anchor_truncation_is_announced` | 137 | not-in | `'另有' not in ds.r_duplicate_anchor('', blocks5, None)[0].evidence` |  | CH-516 ✓ |
| 58 | G | `tests/test_ch504_service_faces.py::test_deletion_audit_survives_a_corrupt_cascade_row` | 196 | not-in | `'cascade_json' not in rows['d-bad']` → 同一行的 6 个公开键必须计满 |  | CH-518 ✓（cascade_json 0 / 公开键 6） |
| 59 | G | `tests/test_ch504_service_faces.py::test_matrix_has_no_dead_action` | 304 | not-in | `'rerun' not in auth.MATRIX` → 同矩阵的 `submit_job` 必须计 1 |  | CH-518 ✓（rerun 0 / submit_job 1） |
| 60 | G | `tests/test_ch504_service_faces.py::test_gui3_duration_column_has_one_origin` | 401 | not-in | `'finished - created' not in durs[1]` → 同一支的 `started` 必须计 1 |  | CH-518 ✓（finished-created 0 / started 1） |
| 61 | G | `tests/test_ch504_service_faces.py::test_content_type_error_names_the_field` | 531 | not-in | `'token' not in body` → 同一容器的 `code`/`message`/`detail` 必须计满 3 |  | CH-518 ✓（token 0 / 契约键 3） |
| 62 | G | `tests/test_ch505_contract_sync.py::_parse_section` | 110 | not-in | `key not in rows` |  | 是 |
| 63 | G | `tests/test_ch505_contract_sync.py::test_appendix_c_parser_has_teeth` | 153 | not-in | `'409' not in rows['POST', '/jobs']['errors']` |  |  |
| 64 | G | `tests/test_ch505_contract_sync.py::test_appendix_c_parser_has_teeth` | 154 | not-in | `'422' not in rows['POST', '/jobs']['errors']` |  |  |
| 65 | G | `tests/test_ch505_escape_single_source.py::test_escape_implementations_have_a_single_source` | 139 | not-in | `'str.maketrans' not in text` |  | 是 |
| 66 | G | `tests/test_ch505_escape_single_source.py::test_escape_implementations_have_a_single_source` | 140 | not-in | `'&lt;' not in text` |  | 是 |
| 67 | G | `tests/test_ch505_perms_coverage.py::test_harden_windows_reports_nonzero_exit_with_last_stderr_line` | 156 | not-in | `perms._cache_key(tmp_path) not in perms._HARDENED` | 155 |  |
| 68 | G | `tests/test_llm_p0_contract.py::test_allowed_ops_hides_table_boundary_for_far_paragraph` | 120 | not-in | `far_hits = far_ops.count("mark_table_boundary")` → `assert (far_hits, near_hits) == (0, 1)` |  | CH-514 ✓ |
| 69 | G | `tests/test_llm_p0_contract.py::test_payload_carries_v25_schema_and_contract` | 198 | not-in | `clean_hits = user.count('"marker"')` → `assert (clean_hits, marker_hits) == (0, 2)` |  | CH-514 ✓ |
| 70 | G | `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | `narrow_hits[0]` → `assert (narrow_hits, wide_hits) == ([0, 0, 0], [1, 1, 1])` |  | CH-514 ✓ |
| 71 | G | `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | `narrow_hits[1]` → 同一条 `assert (narrow_hits, wide_hits)` |  | CH-514 ✓ |
| 72 | G | `tests/test_llm_p0_contract.py::test_payload_op_schema_is_narrowed_to_batch_candidates` | 231 | not-in | `narrow_hits[2]` → 同一条 `assert (narrow_hits, wide_hits)` |  | CH-514 ✓ |
| 73 | G | `tests/test_pdf_columns.py::test_two_column_page_reads_left_column_before_right` | 18 | not-any | `not any(('双中栏文排' in t for t in texts))` | 17 |  |
| 74 | G | `tests/test_pdf_columns.py::test_single_column_long_paragraph_stays_whole` | 31 | not-in | `'双栏' not in first` |  |  |
| 75 | G | `tests/test_pdf_structure_repair.py::test_wrapped_heading_continuation_joins_heading` | 155 | not-in | `'继续部分' not in [b.text for b in ir.blocks if b.type == 'paragraph']` |  |  |
| 76 | G | `tests/test_pdf_structure_repair.py::test_repeated_body_line_near_top_is_kept` | 187 | not-any | `not any(('小页眉' in t for t in texts))` | 188 |  |
| 77 | G | `tests/test_qa_n04_furniture_ext.py::test_roman_decorated_number_not_in_inline_mixed_case` | 100 | not-search | `not _PAGE_NUM_INLINE.search('第一章 引言 · 12')` |  |  |
| 78 | G | `tests/test_v24_gates.py::test_malicious_filenames` | 175 | not-in | `out.rsplit('.', 1)[0].lower() not in WIN_RESERVED` | 173 |  |
| 79 | G | `tests/test_v24_gates.py::test_delete_cascade` | 381 | not-in | `'text' not in json.dumps(tomb[-1], ensure_ascii=False)` → 同判据对 `{**tomb[-1], "text": …}` 计数须为 1 | CH-511 ✓（注入 `text` 键命中 1） |  |
| 80 | G | `tests/test_v24_gates.py::test_service_business_events_land_in_logs` | 461 | not-in | `'本条款' not in dumped` → 同一个 `RotatingJsonlWriter` 写短记录后原文须命中 1 | CH-511 ✓（同 sink 短字段命中 1） |  |
| 81 | G | `tests/test_v24_gates.py::test_logs_contain_no_document_text` | 543 | not-in | `'本条款' not in dumped` → 短字段原样命中 1 且长字段只剩 `<sha256:` 1 | CH-511 ✓（短 1 / 指纹 1 双写） |  |
| 82 | G | `tests/test_v24_gates.py::test_gate_test_env_neutralization` | 580 | not-in | `k not in os.environ` → `hits = [k for k in _PROXY_VARS if k in os.environ]` + `assert not hits` | CH-511 ✓（注入 `HTTP_PROXY` 命中 1、夹具本体清除后归零）；CH-517 ✓（注入面/清除面两处判据由字面列表改写为**大小写归一的键集合**，纪律③——旧形态在 `windows-suite` 连红六推） | 是 |
| 83 | · | `tests/qa/test_mdscore.py::test_parse_blocks_skips_front_matter_heading_like_keys` | 70 | not-in | `'hr' not in types` |  |  |
| 84 | · | `tests/qa/test_mdscore.py::test_lint_classifies_md025_as_dialect_not_contract` | 156 | not-in | `'MD025' not in mdscore.CONTRACT_RULES` |  |  |
| 85 | · | `tests/qa/test_mdscore.py::test_score_without_ref_emits_only_consistency_keys` | 217 | not-in | `'text' not in out['scores']` |  |  |
| 86 | · | `tests/test_batch2_storage.py::test_scrub_covers_any_key_name` | 312 | not-in | `long not in out['note']` | 309 |  |
| 87 | · | `tests/test_batch2_storage.py::test_event_payload_scrubbed_in_db` | 337 | not-in | `long not in row['payload_json']` |  |  |
| 88 | · | `tests/test_ch469_ops.py::test_poll_all_flags_truncation_and_skips_prune` | 522 | not-in | `str(gone) not in t.enqueued` | 518 |  |
| 89 | · | `tests/test_ch469_service.py::test_search_db_fault_is_not_swallowed` | 496 | not-in | `'例外' not in json.dumps(ei.value.to_body())` → `assert _leak_hits(body, "例外") == 0` | CH-510 ✓（monkeypatch 泄漏 detail，同判据 =1） |  |
| 90 | · | `tests/test_ch469_service.py::test_editor_escapes_document_derived_fields` | 568 | not-in | `'[data-bid="${' not in html` → `assert not _css_selector_interp_hits(html)` | CH-510 ✓（注入 `[data-bid="${b.id}"]`，同扫描命中 1） |  |
| 91 | · | `tests/test_ch471_fixes.py::test_api_reconvert_conflict_keeps_edits` | 213 | not-any | `not any(('哨兵编辑例外条款ZZ' in b['text'] for b in after))` | 204 |  |
| 92 | · | `tests/test_ch471_fixes.py::test_api_unhandled_exception_is_json_500` | 410 | not-in | `'敏感正文' not in json.dumps(body, ensure_ascii=False)` |  |  |
| 93 | · | `tests/test_ch473_fixes.py::test_pipe10_fallback_single_has_no_dead_head_param` | 346 | not-in | `'head' not in params` |  |  |
| 94 | · | `tests/test_ch473_fixes.py::test_q2_resolver_cache_is_capped` | 887 | not-in | `'fresh-host' not in cache` |  | 是 |
| 95 | · | `tests/test_ch480_resident_assets.py::test_resident_assets_carry_no_secret_names` | 223 | not-in | `'DOC2MD_BACKUP_PASSPHRASE' not in path.read_text(encoding='utf-8')` |  |  |
| 96 | · | `tests/test_ch480_resident_assets.py::test_installer_keeps_the_cron_placeholders_and_defaults` | 246 | not-in | `'__DOC2MD_START__' not in path.read_text(encoding='utf-8')` |  | 是 |
| 97 | · | `tests/test_ch480_resident_assets.py::test_readme_enumerates_exactly_the_existing_labels` | 273 | not-in | `'尚无标签' not in text` |  |  |
| 98 | · | `tests/test_ch485_ops.py::test_junction_not_followed_by_dirguard` | 102 | not-any | `not any(('jump' in p or 'secret' in p for p in res.files))` | 101 |  |
| 99 | · | `tests/test_ch491_features.py::test_format_md_removes_zip` | 150 | not-in | `'zip' not in payload` |  |  |
| 100 | · | `tests/test_ch491_features.py::test_format_zip_is_default_and_unchanged` | 164 | not-in | `'md' not in payload` |  |  |
| 101 | · | `tests/test_ch491_features.py::test_progress_ticks_on_stderr_but_silent_under_json` | 186 | not-in | `'[s1]' not in captured.err` |  |  |
| 102 | · | `tests/test_golden_repro.py::test_generator_has_no_absolute_output_path` | 104 | not-in | `'GOLDEN_DIR = Path("/' not in text` |  |  |
| 103 | · | `tests/test_m3_bigcopy.py::test_bigfile_is_not_a_mandatory_fixture` | 102 | not-in | `'bigfile' not in conftest` |  |  |
| 104 | · | `tests/test_m5_m6c.py::test_internal_link_anchor_optin` | 53 | not-in | `'](#' not in render(ir, link_internal='anchor')` |  |  |
| 105 | · | `tests/test_m5_m6c.py::test_bold_italic_on_strike_off_by_default` | 71 | not-in | `'~~' not in md` |  |  |
| 106 | · | `tests/test_m5_m6c.py::test_default_pipeline_output_honors_contract` | 114 | not-in | `'~~' not in res.markdown` |  | 是 |
| 107 | · | `tests/test_m8_ops.py::test_encrypt_decrypt_roundtrip` | 40 | not-in | `'例外情况' not in blob.decode('latin-1')` |  |  |
| 108 | · | `tests/test_m8_ops.py::test_dod11_drill_end_to_end` | 464 | not-in | `PASS.encode() not in p.read_bytes()` |  | 是 |
| 109 | · | `tests/test_m8_ops.py::test_dod11_drill_end_to_end` | 481 | not-in | `'演练改写' not in json.dumps(tomb, ensure_ascii=False)` |  | 是 |
| 110 | · | `tests/test_qa_o01_wrap_space.py::test_two_column_wrap_space_restored` | 218 | not-any | `not any(('数字2026' in t for t in paras))` | 216 |  |
| 111 | · | `tests/test_qa_o01_wrap_space.py::test_block_line_trail_not_serialized` | 253 | not-in | `'line_trail' not in d` |  |  |
| 112 | · | `tests/test_qa_o05_cell_br.py::test_plain_table_has_no_flat_count_no_warning_no_notice` | 102 | not-in | `WARN_TABLE_FLATTENED not in r.meta['warnings']` |  |  |
| 113 | · | `tests/test_qa_o05_cell_br.py::test_plain_table_has_no_flat_count_no_warning_no_notice` | 105 | not-in | `'class="degraded"' not in report` |  |  |
| 114 | · | `tests/test_qa_o06_metrics.py::test_metrics_empty_ir_and_single_block_do_not_crash` | 82 | not-in | `'heading_pr' not in m0` |  |  |
| 115 | · | `tests/test_qa_o06_metrics.py::test_metrics_pdf_has_no_heading_pr_no_fake_truth` | 112 | not-in | `'heading_pr' not in m` |  |  |
| 116 | · | `tests/test_qa_o06_metrics.py::test_metrics_keys_do_not_reach_front_matter_bytes` | 119 | not-in | `'heading_pr' not in res.metrics` |  |  |
| 117 | · | `tests/test_qa_o06_metrics.py::test_row_shape_transient_not_serialized` | 131 | not-in | `'row_shape' not in _json.dumps(t.to_dict(), ensure_ascii=False)` |  |  |
| 118 | · | `tests/test_qa_o06_metrics.py::test_report_quality_section_iff_metrics` | 157 | not-in | `'<h2>Quality</h2>' not in r2` |  |  |
| 119 | · | `tests/test_qa_o06_metrics.py::test_report_quality_section_iff_metrics` | 161 | not-in | `'<h2>Quality</h2>' not in z.read(f'{res.doc_id}/report.html').decode('utf-8')` |  | 是 |
| 120 | · | `tests/test_qa_o06_metrics.py::test_report_quality_escapes_and_shows_dash_for_missing` | 175 | not-in | `'elapsed_ms' not in report` |  |  |
| 121 | · | `tests/test_qa_o08_skip_existing.py::test_skip_existing_without_library_is_noop` | 199 | not-in | `'status=skipped' not in out` |  |  |
| 122 | · | `tests/test_qa_o08_skip_existing.py::test_watch_restart_skips_done_and_zero_reconversion` | 238 | not-in | `'"status": "skipped"' not in first.out` |  |  |
| 123 | · | `tests/test_qa_o08_skip_existing.py::test_watch_rewrite_still_converts_after_skip` | 277 | not-in | `'"status": "skipped"' not in out` |  |  |
| 124 | · | `tests/test_qa_o10_layout_probe.py::test_unrotated_pdf_no_layout_warning` | 72 | not-in | `WARN_LAYOUT_ROTATED_RTL not in res.ir.warnings` |  |  |
| 125 | · | `tests/test_qa_o10_layout_probe.py::test_unrotated_pdf_no_layout_warning` | 73 | not-in | `'layout_probe' not in res.ir.extra_meta` |  |  |
| 126 | · | `tests/test_qa_o10_layout_probe.py::test_gate_corpus_zero_layout_warnings` | 82 | not-in | `WARN_LAYOUT_ROTATED_RTL not in fm` |  | 是 |
| 127 | · | `tests/test_qa_round2_fixes.py::test_d12_md_result_does_not_report_deleted_zip` | 89 | not-in | `'zip' not in payload` |  |  |
| 128 | · | `tests/test_qa_round2_fixes.py::test_d7_pdf_callout_stays_paragraph` | 264 | not-in | `'以下一行是 14pt 的提示框文字，没有句末标点' not in headings` |  |  |
| 129 | · | `tests/test_qa_round2_fixes.py::test_d11_readme_version_matches_pipeline` | 363 | not-in | `'v2.12.1' not in text` |  |  |
| 130 | · | `tests/test_s3_blocks.py::test_single_multi_column_row_is_not_a_table` | 85 | not-in | `'table' not in _types(ir)` |  |  |
| 131 | · | `tests/test_s3_blocks.py::test_pdf_row_tail_sentence_end_guard` | 97 | not-in | `'table' not in _types(ir)` |  |  |
| 132 | · | `tests/test_s3_blocks.py::test_row_x0_jump_splits_groups` | 114 | not-in | `'丙' not in tbls[0].text` |  |  |
| 133 | · | `tests/test_s3_blocks.py::test_indent_code_block_requires_empty_para` | 144 | not-in | `'code' not in _types(ir2)` |  |  |
| 134 | · | `tests/test_s3_blocks.py::test_quote_only_starts_at_para_boundary` | 176 | not-in | `'quote' not in _types(ir)` |  |  |
| 135 | · | `tests/test_s3_blocks.py::test_page_break_block_and_render_empty` | 189 | not-in | `'\x0c' not in md` |  |  |
| 136 | · | `tests/test_s3_blocks.py::test_fix_heading_level_same_level_is_noop` | 437 | not-in | `'modified_by' not in ir.blocks[0].provenance` |  |  |
| 137 | · | `tests/test_sharding_unit.py::test_overlap_dedupe_keeps_non_suffix_window_blocks` | 118 | not-any | `not any((b.flags.get('dropped_dup_of') for b in out))` | 117 |  |
| 138 | · | `tests/test_t2_llm_server.py::test_no_fallback_raises_e5041_with_503` | 312 | not-in | `MARKER not in json.dumps(ei.value.detail, ensure_ascii=False)` |  |  |
| 139 | · | `tests/test_t2_llm_server.py::test_error_messages_carry_no_document_text` | 388 | not-in | `MARKER not in str(ei.value)` |  |  |
| 140 | · | `tests/test_t4_queue_ui.py::test_jobs_surface_is_nfr20_clean` | 145 | not-in | `MARKER not in blob` |  |  |
| 141 | · | `tests/test_t4_queue_ui.py::test_jobs_surface_is_nfr20_clean` | 149 | not-in | `banned not in t` |  | 是 |
| 142 | · | `tests/test_t4_queue_ui.py::test_queue_panel_never_renders_block_text` | 163 | not-in | `'markdown' not in panel` |  |  |
| 143 | · | `tests/test_t5_windows_ops.py::test_no_secret_name_in_xml` | 149 | not-in | `'DOC2MD_BACKUP_PASSPHRASE' not in path.read_text(encoding='utf-8')` |  |  |
| 144 | · | `tests/test_t5_windows_ops.py::test_installer_has_no_hardcoded_secret` | 155 | not-search | `not re.search('(?i)passphrase\\s*[=:]\\s*[\'\\"][^\'\\"]{4,}', text)` | 157 |  |
| 145 | · | `tests/test_t5_windows_ops.py::test_installer_never_writes_secret_to_disk` | 165 | not-in | `banned not in text` |  | 是 |
| 146 | · | `tests/test_t5_windows_ops.py::test_xml_substitution_roundtrip` | 203 | not-in | `'__DOC2MD_' not in xml` |  | 是 |
| 147 | · | `tests/test_t6_corpus_bench.py::test_truncated_pdf_maps_to_e4151` | 112 | not-in | `'Unexpected EOF' not in json.dumps(exc.detail, ensure_ascii=False)` |  |  |
| 148 | · | `tests/test_t7_drill.py::test_drill_report_leaks_no_plaintext` | 132 | not-in | `MARKER not in blob` |  |  |
| 149 | · | `tests/test_t7_drill.py::test_drill_report_leaks_no_plaintext` | 133 | not-in | `'第三段正文' not in blob` |  |  |
| 150 | · | `tests/test_t7_drill.py::test_drill_report_leaks_no_plaintext` | 134 | not-in | `PASS not in blob` |  |  |
| 151 | · | `tests/test_t9_convergence.py::test_page_detail_in_report_toggles_gate_section` | 163 | not-in | `'S1.5 gate' not in without_section` |  |  |
| 152 | · | `tests/test_t9_convergence.py::test_w1044_absent_when_single` | 182 | not-in | `WARN_SHARD_NOT_MERGED not in r.meta['warnings']` |  |  |
| 153 | · | `tests/test_t9_convergence.py::test_removed_dead_code_stays_removed` | 261 | not-in | `'E5051' not in errors_mod.CODE_INDEX` |  |  |

## 处置

本清单是**文档产出，不进测试**（OPT4-10 明确「只出清单、不当轮改完」）。
补位自 **CH-510** 起按批进行（登记项 B-53）：每批取一个测试文件，逐条补同主语正断言，
先做 red-first 变异证明「缺陷存在时正例能红」，再在本清单勾销并更新行号。
复核顺序：① 红线层 + 控制流内；② 红线层其余；③ 非红线。
补正夹具的形态按纪律②：否定面命中数为 0 的**同时**，在对照面打出一个 > 0 的命中数，
两者写进同一断言的失败消息（TC-M8-04 的 `delivery_hits` / `ir_json_hits` 是样板）。
