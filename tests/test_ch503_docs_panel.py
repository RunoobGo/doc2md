r"""CH-503（第十八轮）B-36 / OPT-12：审阅页文档清单接 `GET /docs` 的红线。

缺陷形态（登记原文）：`editor.html` 里唯一的「有哪些文档」来源是队列面板
（`GET /jobs?limit=50`）。队列是**任务**口径，两个方向都会指错：
- 同一篇文档转三遍 → 面板列三条，读起来像三篇文档；
- `doc2md work` 跑完即出队 → 文档还在库里，面板却一条不剩。
服务端出口 `GET /docs`（`repo.list_docs` 的 HTTP 面）CH-500 已补（R17-API-6），
本轮把 GUI 接上去。

本文件守五处，缺一处都会让「接上了」变成「接错了」：
① 数据面必须是 `/docs`，且不得把队列状态当文档状态复用；
② **转义面**：文档行的 `source_name` / `profile` / `updated_at` / `pages` 全部来自
   文件名与库字段（可控），既有扫描 `_BARE_FIELD`（`tests/test_ch469_service.py`）的字段
   清单不含它们——清单不扩，回退成裸插就无人报警（负夹具钉住「扫描确实会红」）；
   并且判据按「模板内所有插值都含 esc」写，逐字段白名单会在下一轮新增列时漏放（本轮实测）；
   R18-GUI-3（本轮变异测试抓出）：旧扫描按「`.innerHTML =` 后第一个反引号段」取模板，
   而 `renderQueue` / `renderDocs` / `renderBlocks` 全是多段拼接——非首段的 `esc()`
   摘掉红线不红。取值面因此上收到 `tests/qa/static_scan.py`（按语句取到收尾分号），
   本文件用「尾段锚点 + 插值计数等式 + 非首段裸插负夹具」三处自证覆盖面；
③ 窗口不得显示成全部（R17-API-8 同一条纪律的文档侧）；
④ `/docs` 信封与行字段是面板的依赖面，缺键即红（面板不许靠猜）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.qa.static_scan import (
    inner_html_statements,
    interpolations,
    unescaped_interpolations,
)

EDITOR = Path(__file__).resolve().parents[1] / "doc2md" / "static" / "editor.html"

# 文档行里**由数据面决定**的字段名——转义扫描与信封断言的覆盖面口径。
# 注意 `source_name_raw` 不在其中：`repo.list_docs` 的 SELECT 不取该列（本轮实测），
# 面板因此只渲染 `source_name`；把它当依赖键会写出恒假断言。
DOC_ROW_FIELDS = ("doc_id", "source_name", "format", "pages",
                  "sharded", "shard_count", "status", "profile", "created_at",
                  "updated_at", "blocks")
# 扫描面比依赖面宽：`source_name_raw` 虽然不在清单行里，将来谁把它接回来仍必须被扫到
_SCAN_FIELDS = DOC_ROW_FIELDS + ("source_name_raw",)
_BARE_DOC = re.compile(
    r"\$\{\s*(?:d|doc)\s*\.\s*(?:" + "|".join(_SCAN_FIELDS) + r")\s*\}")


def _docs_statement(src: str) -> str:
    """文档行那条 `.innerHTML = …;` 的**右值全文**（多段拼接必须整段取）。

    R18-GUI-3：CH-469 的旧口径只取 `.innerHTML =` 后第一个反引号段，`renderDocs`
    是 9 段拼接——摘掉后 8 段任何一处 `esc()` 都无人报警。取值面交给
    `tests/qa/static_scan.py`，本文件用尾段锚点自证「真的取到了尾段」。
    """
    docs = [s for s in inner_html_statements(src) if "data-doc=" in s]
    assert len(docs) == 1, f"文档行 innerHTML 语句应唯一，实测 {len(docs)} 条"
    return docs[0]


# --- ① 数据面：文档口径不得再由队列推断 ------------------------------------
@pytest.mark.gate
def test_doclist_is_served_by_the_docs_route():
    """面板必须打 `/docs` 并读文档信封；队列面板保留原样，两条腿不得互相冒充。"""
    src = EDITOR.read_text(encoding="utf-8")
    assert 'id="drows"' in src and 'id="dtable"' in src, "文档清单面板骨架缺失"
    assert "async function loadDocs" in src, "loadDocs 入口漂移（G4' 同款静态断言）"
    assert re.search(r'api\(`/docs\?limit=\$\{DSTATE\.limit\}&offset=\$\{offset\}`\)', src), (
        "文档清单未走 /docs 的服务端窗口（limit/offset 都得由数据面给）")
    # 队列面板仍然是 /jobs（它本来就是任务口径），且文档清单不复用 tasks 数组
    assert 'api("/jobs?limit=50"' in src, "队列面板的数据面被改动了"
    # 成对判据（B-53 / CH-521）：负例面「容器里没有 QSTATE.tasks」必须配同容器正例面
    # 「容器确实是 loadDocs 的本体」。旧写法 `src.split("async function loadDocs")[1]`
    # 取到的是该函数之后的**整段文件**——函数改名或搬空后负例面照样绿（尾段里本来
    # 也没有队列数组），而判据想说的「这篇函数不读队列」从未被验证。收口锚点改用
    # 下一个顶层函数 `dAutoToggle`，并要求切分真的发生过：边界失准即红，而不是静默
    # 退回整段尾文。两个面的命中数都打进失败消息。
    _parts = src.split("async function loadDocs")
    assert len(_parts) == 2, f"loadDocs 入口漂移（切分命中 {len(_parts) - 1} 次，应 1 次）"
    _fn, _sep, _ = _parts[1].partition("\nfunction dAutoToggle")
    assert _sep, "loadDocs 的收尾锚点 `function dAutoToggle` 漂移——容器边界失准"
    _members = ("api(`/docs?limit=", "DSTATE.hasMore", "DSTATE.rows", "renderDocs()")
    _hits = sum(m in _fn for m in _members)
    assert (_hits, _fn.count("QSTATE.tasks")) == (len(_members), 0), (
        f"loadDocs 容器判据失准：依赖成员命中 {_hits}/{len(_members)}"
        f"（缺 {[m for m in _members if m not in _fn]}），"
        f"QSTATE.tasks 命中 {_fn.count('QSTATE.tasks')} 次（应为 0）")


# --- ② 转义面（含负夹具） ---------------------------------------------------
@pytest.mark.gate
def test_doc_row_interpolations_are_escaped():
    """文档行模板里的**每一处**插值都必须过 esc()：source_name 来自文件名，是可控输入。

    判据写成「模板内所有 `${...}` 都含 esc(」而不是逐字段白名单：白名单会漏掉
    下一轮新增的列（本轮 `pages` / `blocks` 用 `esc(String(...))` 就被白名单形态误报）。
    """
    src = EDITOR.read_text(encoding="utf-8")
    stmt = _docs_statement(src)
    # 取值面自证：语句里得同时有首段与尾段，否则下面的判据仍只覆盖拼接的第一片。
    assert stmt.count("`") >= 6, f"文档行语句只取到 {stmt.count('`') // 2} 段拼接，覆盖面存疑"
    assert "esc(stamp)" in stmt, "语句扫描没取到尾段（R18-GUI-3 的首段口径回退了）"
    assert "esc(d.source_name" in stmt, "语句扫描没取到中段的 source_name（同上）"
    # 插值计数等式：嵌套花括号不得让插值静默逃出判据
    assert len(interpolations(stmt)) == stmt.count("${"), (
        f"插值解析漏项：解析出 {len(interpolations(stmt))} 处 / 语句里有 {stmt.count('${')} 处")
    offenders = [i for i in _BARE_DOC.finditer(stmt)]
    assert not offenders, f"文档行字段裸插进 innerHTML：{[m.group(0) for m in offenders]}"
    unescaped = unescaped_interpolations(stmt)
    assert not unescaped, f"文档行模板存在未过 esc 的插值：{unescaped}"
    # 正例锚点：这几处是本轮亲手写的，回退时最容易被改掉
    assert "esc(d.source_name || \"\")" in src
    assert 'c-${esc(d.status)}' in src
    assert 'data-doc="${esc(d.doc_id)}"' in src


@pytest.mark.gate
def test_bare_doc_field_interpolation_is_caught_by_the_scan():
    """负夹具：扫描本身必须有牙齿——把一处改成裸插，它必须报出来。

    没有这条，`_BARE_DOC` 写错（字段名拼漏、别名漏了 `doc`）会让上一项恒真，
    正是 OPT-2 反空跑纪律在静态红线上的同一形态。
    第二组夹具钉住 R18-GUI-3：**拼接语句的非首段**裸插也必须被扫到——旧口径在这
    里恒真，本轮的缺陷形态就是它。
    """
    bad = '`<td>${d.source_name}</td><td>${esc(d.format)}</td>`'
    assert _BARE_DOC.search(bad), "扫描器对已知的裸插形态无感——红线是空的"
    good = '`<td>${esc(d.source_name)}</td><td>${esc(d.format)}</td>`'
    # 成对判据（B-53 / CH-521）：**同一份夹具**，只把 esc() 摘掉，扫描器必须从 0 命中
    # 变成 2 命中。旧写法只断言「good 不命中」——字段名或别名写歪时扫描器对任何输入
    # 都不命中，负例面恒真；同主语正例面把「不命中」钉成「因为转义才不命中」。
    _stripped = re.sub(r"esc\(([^()]*)\)", r"\1", good)
    _esc_hits, _raw_hits = len(_BARE_DOC.findall(good)), len(_BARE_DOC.findall(_stripped))
    assert (_esc_hits, _raw_hits) == (0, 2), (
        f"转义形态判据失准：esc 版命中 {_esc_hits} 次（应 0），摘掉 esc 后命中 "
        f"{_raw_hits} 次（应 2）——扫描器对这份夹具本该恰好命中两个字段")
    # 语句级：尾段裸插必须仍被取出并命中；首段转义不能替尾段担保
    tail_bad = ('tr.innerHTML =\n'
                '  `<td>${esc(d.doc_id)}</td>` +\n'
                '  `<td>${d.source_name}</td>`;\n')
    stmts = inner_html_statements(tail_bad)
    assert len(stmts) == 1, f"拼接语句应取 1 条，实测 {len(stmts)}"
    assert _BARE_DOC.search(stmts[0]), "非首段的裸插逃出了扫描——首段口径复现"
    assert unescaped_interpolations(stmts[0]) == ["d.source_name"]
    # 嵌套插值不得逃计数
    assert len(interpolations('`<td>${a ? `${esc(b)}` : ""}</td>`')) == 2


@pytest.mark.gate
def test_statement_extractor_boundaries_are_pinned():
    """取值面助手自己必须有红线：定界写错就又是「扫描面比声明面窄」的同一形态。

    三条边界各钉一个失败形态：① 模板串里的分号提前定句（尾段又逃出去）；
    ② 转义反引号提前收尾（同上）；③ 多条语句被并成一条（归因错位）。
    """
    semi = 'tr.innerHTML = `<td>a;b</td>` + `<td>${d.source_name}</td>`;\n'
    stmts = inner_html_statements(semi)
    assert len(stmts) == 1 and "a;b" in stmts[0], "模板串内的分号被当成语句结束——尾段又丢了"
    assert "d.source_name" in stmts[0]
    # 双引号里的分号同样不作定界
    quoted = 'el.innerHTML = f("x;y") + `<td>${d.pages}</td>`;\n'
    assert len(inner_html_statements(quoted)) == 1
    assert "d.pages" in inner_html_statements(quoted)[0]
    # 模板串里的转义反引号不能提前收尾
    escaped = "el.innerHTML = `a\\`b` + `${d.blocks}`;\n"
    esc_stmts = inner_html_statements(escaped)
    assert len(esc_stmts) == 1 and "b`" in esc_stmts[0], "转义反引号被当成语面结束"
    assert unescaped_interpolations(esc_stmts[0]) == ["d.blocks"]
    # 多条语句必须各取一条，且空右值（`innerHTML = ""`）也要被计入
    many = ('el.innerHTML = "";\n'
            'a.innerHTML = `<b>${esc(x)}</b>`;\n'
            'b.innerHTML = `<i>${esc(y)}</i>`;\n')
    assert len(inner_html_statements(many)) == 3
    assert interpolations(inner_html_statements(many)[0]) == []


# --- ③ 窗口口径 -------------------------------------------------------------
@pytest.mark.gate
def test_doclist_does_not_present_window_as_total():
    """与 R17-API-8 同纪律：`limit` 窗口要显形，`has_more` 要能翻页。"""
    src = EDITOR.read_text(encoding="utf-8")
    assert "DSTATE.hasMore" in src and "$(\"dmore\").disabled = !DSTATE.hasMore" in src, (
        "文档清单没有把截断显形，或没有接「更多文档」入口")
    assert re.search(r'列出 \$\{shown\} / 库内 \$\{DSTATE\.total\} 篇', src), (
        "清单提示未同时给窗口数与库内总数（只显示窗口数即回退为「把 50 条当全部」）")
    assert "服务端按 created_at DESC 截断" in src, "截断原因未写明（调用方无从判断口径）"


# --- ④ 依赖面：/docs 信封与行字段 -------------------------------------------
@pytest.mark.gate
def test_docs_envelope_carries_every_field_the_panel_renders(api_client):
    """面板读到的每个键都得在响应里——缺键的兜底是 `?? ""`，静默变空列不可接受。"""
    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["reader"]}
    j = client.get("/docs?limit=10&offset=0", headers=h).get_json()
    for key in ("docs", "total", "offset", "limit", "has_more"):
        assert key in j, f"/docs 信封缺键 {key}：{sorted(j)}"
    assert isinstance(j["docs"], list), "docs 必须是数组（面板按 rows.forEach 渲染）"
    assert isinstance(j["total"], int) and isinstance(j["has_more"], bool), (
        f"信封类型漂移：total={j['total']!r} has_more={j['has_more']!r}")
    # 行字段清单由下一项（库里有文档时）逐字段核对；这里不写 `if rows:` 的空分支兜底，
    # 空库时那条断言恒真，正是 OPT-2 反空跑要防的形态。


@pytest.mark.gate
def test_docs_envelope_rows_have_all_panel_fields(saved_doc):
    """有文档在库时逐字段核对——`blocks` 计数与 `source_name_raw` 都是面板列。"""
    client, tokens, doc_id = saved_doc
    j = client.get("/docs?limit=10", headers={"X-Doc2md-Token": tokens["reader"]}).get_json()
    row = next(r for r in j["docs"] if r["doc_id"] == doc_id)
    missing = [f for f in DOC_ROW_FIELDS if f not in row]
    assert not missing, f"/docs 行缺面板依赖字段：{missing}"
    assert int(row["blocks"]) > 0, f"blocks 计数没接上：{row}"
    assert row["status"] == "done", f"库内文档状态口径漂移：{row['status']}"


# --- ⑤ 样式面：CSS 点了 #dtable 的名，数据侧就得真的供上状态 -----------------
@pytest.mark.gate
def test_docs_row_status_class_drives_the_styled_rows():
    """`#dtable tr.st-failed` 一类规则靠行上的状态类生效；行不打类，三条规则就是死代码。

    本轮把队列的样式选择器分组成 `#qtable, #dtable`（两个面板同构），所以行状态类
    必须同口径——只有一侧打类，另一侧的规则永远不命中（实机走查时发现文档行
    `className` 为空，而 CSS 已按 `#dtable` 点名了三种状态）。
    """
    src = EDITOR.read_text(encoding="utf-8")
    css_states = set(re.findall(r"#dtable tr\.st-([a-z]+)", src))
    assert css_states == {"failed", "rejected", "running"}, (
        f"样式表里 #dtable 的行状态规则漂移：{sorted(css_states)}")
    docs_body = src.split("function renderDocs")[1].split("async function loadDocs")[0]
    assert 'tr.className = "st-" + d.status;' in docs_body, (
        "文档行未打状态类：#dtable tr.st-* 三条规则成了死代码")
    queue_body = src.split("function renderQueue")[1].split("async function loadQueue")[0]
    assert 'tr.className = "st-" + t.status;' in queue_body, (
        "队列行不再打状态类——两个面板的行状态口径分裂了")


# --- ⑥ 空态文案：产出面必须与产品事实同源 ------------------------------------
@pytest.mark.gate
def test_docs_empty_state_names_only_the_library_writing_faces():
    """实机走查抓到的假承诺：空态文案原本教用户用 `doc2md convert` 产出文档。

    本机实测（真实 serve + 独立 root）：`convert --root R` 连跑 5 次后 `R/state.sqlite`
    的 `docs` 表仍为 0 行，只有 `POST /jobs`（以及 `work` / `watch` 两条落库路径）
    才让面板出现条目——CLI 的 `convert` 是**纯产物流**，只写 `out/<doc_id>.zip`，
    不经 `save_ir` 落库（`doc2md/pipeline/batch.py` 的 `skip_existing_hit` 注释里
    就用这个口径做产物回退判据）。面板按文档口径读 `docs` 表，所以那句文案是
    一条会让人永远等不到结果的指引——与附录 G 引导段病史同族（绿着入库的假承诺）。

    判据两头都钉：文案侧列出三个入库面且不把 `convert` 当入库面；产品侧核对
    `cmd_convert` 函数体确实不写库——产品事实若变了（例如将来 convert 落库），
    这条会报红而不是让文案单方面过时。
    """
    src = EDITOR.read_text(encoding="utf-8")
    empty = re.search(r'<div id="dempty".*?</div>', src, re.S)
    assert empty, "文档清单空态文案被删：截断/空库时无任何指引"
    copy = empty.group(0)
    for face in ("work --enqueue", "doc2md watch", "POST /jobs"):
        assert face in copy, f"空态文案漏了入库面 {face}"
    assert "只出产物的 zip，不写库" in copy, (
        "空态文案未声明 `doc2md convert` 不落库——面板条目等不到，指引成了假承诺")

    cli = (EDITOR.parents[2] / "doc2md" / "cli.py").read_text(encoding="utf-8")
    parts = cli.split("def cmd_convert")
    assert len(parts) == 2, f"cmd_convert 入口漂移（切分命中 {len(parts) - 1} 次，应 1 次）"
    body = parts[1].split("\ndef ")[0]
    # 成对判据（B-53 / CH-521）：负例面「函数体不碰 save_ir」必须配同容器正例面
    # 「这确实是 cmd_convert 的本体」——函数被搬空或切成空串时，负例面恒真。
    # 正例面的基准取「产出面四个必存成员」；`save_ir` 在本文件别处（`_watch_commit` /
    # `cmd_watch`）确实存在，所以 0 命中说的是「这条路径不落库」，不是符号不存在。
    _members = ("run_batch(", "skip_existing_hit(", "convert(", "_apply_format(")
    _hits = sum(m in body for m in _members)
    assert (_hits, body.count("save_ir")) == (len(_members), 0), (
        f"cmd_convert 容器判据失准：产出面成员命中 {_hits}/{len(_members)}"
        f"（缺 {[m for m in _members if m not in body]}），"
        f"save_ir 命中 {body.count('save_ir')} 次（应为 0；文件别处 save_ir 共 "
        f"{cli.count('save_ir')} 次，符号本身存在）")
