r"""CH-504（第十九轮）服务面与审阅页红线：R19-ARCH-1/3/4/5/6 · R19-API-1/5/6 · R19-GUI-1/2/3/4。

本轮的服务面发现有一个共同形态：**每条路径单独看都「不崩」，合起来却给出与自身文档
或自身另一分支矛盾的结果**。所以这里的断言几乎都在钉「两个面必须一致」，而不是钉
某一面单独的行为：

- R19-ARCH-1：`DOCS_MAX_ROWS` 是**单页行数**上限，`_nonneg` 却拿它同时夹 `--offset`。
  `docs list --offset 600` 于是静默从 500 开窗——退出码 0、结果不是请求的那一页。
  API 侧 `_window` 只夹 limit 不夹 offset、CLI 的 `cmd_search` 早在 R17-API-7 就按
  「翻页游标天然可以大于单页上限」绕过；三个面里只有 docs 这两个子命令没跟上。
- R19-ARCH-3：`cron.run` 的步骤 1（日报）裸在 CH-495 加的那个 `try/finally` **之前**，
  于是它一抛，整份 cron 报告连同 finally 里的 writer.close / store.close / 报告落盘
  全部丢失——正是那个 finally 要防的形态，只是漏在了它前面一步。
- R19-ARCH-4：`_purge._kill` 把 `FileNotFoundError` 记成 failed，让 delete_doc /
  delete_source 冒 E5031（500）。列举与删除之间被并发方抢先移除时，期望终态已经达成。
- R19-ARCH-5 / R19-API-6：一行损坏的 `cascade_json` / `meta_json` 让整份审计列表、
  整个 `GET /docs/<id>` 变 500。两者都降级成 `{}`；**但都不推广到块级 IR 字段**
  （`list_blocks` 的 flags/marks/links/font/provenance）——那是正文，静默清空等于
  把「内容丢了」伪装成成功。这条不对称是本文件要守的重点，见 test_api6 的第二半。
- R19-ARCH-6：`Store.close()` 原先重新赋值 `self._local`。`threading.local()` 实例是
  各线程共用的一个容器，换对象会把**其它线程**缓存的 conn/holder 一并抹掉：那些线程
  的下一次 `_conn()` 静默新建连接（Store「关了又活」），原连接从此失去 close 路径。
- R19-API-1：`POST /tokens` 缺 `role` 时静默签 reader。附录 C 写的是 `body {role,
  label?}`——只有 label 带 `?`。判据与 CH-495 / CH-500 两次修 `body()` 折叠时写下的
  完全同一条（那两处的病灶原文就是「POST /tokens 静默签出 reader token」）。
- R19-API-5：`auth.MATRIX` 里 `"rerun"` 零调用，docstring 却写 editor 有「重跑」。
- R19-GUI-1/2/3/4：审阅页四处。**GUI 面本机无浏览器**，故按本仓既有口径
  （`test_ch469_service.py` 扫 innerHTML 模板、`test_ch496_local.py` 等价 `node --check`）
  用**源码静态断言**钉住，并把浏览器人工走查登记为 B 类待验证项。四条都是「读源码即可
  判定真伪」的形态（一个 `const` 重赋值必然 TypeError；ASC + LIMIT 必然取到最旧页；
  同一列在两个分支里从不同时刻起算；`input` 上直挂两个 fetch 必然每键一发），
  不需要运行时才能确认。

变异证明：本文件每条红线都做过反向变异（把修复退回原样），逐条实测转红，见轮次报告。
"""
from __future__ import annotations

import ast
import json
import re
import threading
from pathlib import Path

import pytest

from doc2md import auth, cli, cron
from doc2md.ir import IR, Block
from doc2md.storage import repo
from doc2md.storage.db import Store

pytestmark = pytest.mark.gate

EDITOR_HTML = (Path(cli.__file__).resolve().parent / "static" / "editor.html")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(store: Store, doc_id: str = "d1", source_key: str = "s1", n: int = 3) -> IR:
    ir = IR(doc_id=doc_id, source_key=source_key, source_name="demo.pdf",
            format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"第 {i} 块 例外情况", page=1)
                 for i in range(1, n + 1)]
    repo.save_ir(store, ir)
    return ir


def _app_store(client) -> Store:
    return client.application.config["doc2md"]["store"]


def _mklib(cfg: dict) -> Store:
    """`cli._open_store` 对不存在的库直接 E4041，故先按 cfg 的 root 把库建出来。"""
    db = cli._root(cfg) / "state.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    return Store(db)


# --- R19-ARCH-1：翻页游标不受单页上限夹 ----------------------------------------


def test_docs_offset_is_not_clamped_to_page_size(tmp_cfg, tmp_path, monkeypatch, capsys):
    """`docs list/show --offset 600` 必须真的从 600 开窗，而 `--limit` 照旧截到 500。"""
    monkeypatch.setattr(cli, "_cfg", lambda args: tmp_cfg)
    st = _mklib(tmp_cfg)
    _seed(st, "d1")
    st.close()

    rc = cli._cmd_docs_show(type("A", (), {"doc_id": "d1", "limit": 20, "offset": 600})())
    out = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_OK
    # 库里只有 3 块，600 开窗外必然空——断言的是**回读的游标**，不是有没有数据。
    assert out["blocks_offset"] == 600, out["blocks_offset"]
    assert out["blocks"] == []

    rc = cli._cmd_docs_list(type("A", (), {"limit": 600, "offset": 700, "status": None})())
    out = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_OK
    assert out["offset"] == 700, f"offset 被单页上限夹住：{out['offset']}"
    assert out["limit"] == cli.DOCS_MAX_ROWS, f"limit 不再截断：{out['limit']}"


def test_docs_negative_window_still_rejected(tmp_cfg, tmp_path, monkeypatch, capsys):
    """放开游标不得顺带放开负值：SQLite `LIMIT -1` 是无界的反直觉语义。"""
    monkeypatch.setattr(cli, "_cfg", lambda args: tmp_cfg)
    st = _mklib(tmp_cfg)
    _seed(st, "d1")
    st.close()
    rc = cli._cmd_docs_list(type("A", (), {"limit": 10, "offset": -1, "status": None})())
    err = capsys.readouterr()
    assert rc == cli.EXIT_USAGE, rc
    assert "E4001" in err.err, err.err
    assert not err.out.strip(), "负值请求不该产出任何结果 JSON"


# --- R19-ARCH-3：cron 步骤 1 失败不吞掉整份报告 --------------------------------


def test_cron_daily_report_failure_keeps_the_rest(tmp_cfg, tmp_path):
    """日报抛异常时：后续步骤照跑、报告照落盘、`ok=False` 如实上报。"""
    def boom(*a, **kw):
        raise RuntimeError("ENOSPC 的代理形态")

    orig = cron._daily_report
    assert callable(orig), "cron._daily_report 改名了——本项的注入点失效"
    cron._daily_report = boom
    try:
        rep = cron.run(tmp_cfg, root=tmp_path / "work", mode="daily", with_backup=False)
    finally:
        cron._daily_report = orig

    steps = {s["step"]: s for s in rep.steps}
    assert "daily_report" in steps and steps["daily_report"].get("error") == "RuntimeError", steps
    assert rep.ok is False, "单步失败必须把整份报告标成不 ok"
    # finally 之前的步骤都得跑到（挑步骤 7/8：它们在 try 体最末，能证明没被中途掀掉）
    assert {"tasks_purge", "search_reconcile"} <= set(steps), sorted(steps)
    reports = list((tmp_path / "work" / "reports").glob("cron-daily-*.json"))
    assert reports, "报告没落盘——finally 没执行"
    body = json.loads(reports[0].read_text(encoding="utf-8"))
    assert body["ok"] is False and body["finished_at"], body


# --- R19-ARCH-4：并发移除算「已删」不算「失败」 --------------------------------


def test_purge_treats_a_vanished_target_as_removed(tmp_path, monkeypatch):
    """列举与删除之间目标被抢先移除 → 记入 removed，failed 保持空。"""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "d1.zip").mkdir()                 # 目录形态：走 shutil.rmtree 那一支

    def racedir(p, *a, **kw):
        raise FileNotFoundError(p)

    monkeypatch.setattr(repo.shutil, "rmtree", racedir)
    removed, failed = repo.purge_files("d1", tmp_path, out_dir=out)
    assert failed == [], f"并发移除被记成 purge 失败：{failed}"
    assert any(p.endswith("d1.zip") for p in removed), removed


def test_purge_still_reports_a_real_oserror(tmp_path, monkeypatch):
    """反方向：真故障（权限等）仍须进 failed——不得把 OSError 一起吞掉。"""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "d1.zip").mkdir()

    def denied(p, *a, **kw):
        raise PermissionError(p)

    monkeypatch.setattr(repo.shutil, "rmtree", denied)
    removed, failed = repo.purge_files("d1", tmp_path, out_dir=out)
    assert removed == [] and len(failed) == 1, (removed, failed)


# --- R19-ARCH-5：审计面一行损坏不掀掉整份列表 ----------------------------------


def test_deletion_audit_survives_a_corrupt_cascade_row(api_client):
    client, tokens = api_client
    st = _app_store(client)
    with st.tx() as tx:
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES ('d-bad','s1','admin','2026-01-01T00:00:00+00:00','doc','{')")
        tx.execute("INSERT INTO deletions(doc_id, source_key, actor, ts, kind, cascade_json)"
                   " VALUES ('d-ok','s1','admin','2026-01-02T00:00:00+00:00','doc',"
                   "'{\"blocks\": 3}')")
    r = client.get("/audit/deletions", headers={"X-Doc2md-Token": tokens["admin"]})
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    rows = {d["doc_id"]: d for d in r.get_json()["deletions"]}
    assert rows["d-bad"]["cascade"] == {}, rows["d-bad"]
    assert rows["d-ok"]["cascade"] == {"blocks": 3}, rows["d-ok"]
    assert "cascade_json" not in rows["d-bad"], "原始列名不该漏进响应体"


# --- R19-ARCH-6：close 只清本线程槽位 ------------------------------------------


def test_close_on_one_thread_leaves_other_threads_conn_alive(tmp_path):
    st = Store(tmp_path / "state.sqlite")
    mine_before = st._conn                   # 主线程建连
    assert st.live_conn_count() == 1

    def other():
        st.q1("SELECT 1")                    # 另一线程建自己的连
        st.close()                           # 只该关它自己那条

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert st.live_conn_count() == 1, (
        f"另一线程 close 后存活连接数 = {st.live_conn_count()}；"
        "替换 threading.local 对象会把两条一起抹掉")
    assert st._conn is mine_before, (
        "主线程的连接被另一线程的 close 抹掉了——下一次 _conn() 会静默新建，"
        "Store「关了又活」，而原连接从此失去 close 路径（PRAGMA optimize 不再执行）")
    assert st.q1("SELECT 1")[0] == 1
    st.close()


# --- R19-API-6：meta 降级，块级 IR 字段不降级 ----------------------------------


def test_get_doc_degrades_meta_but_never_block_payload(store):
    _seed(store, "d1")
    with store.tx() as tx:
        tx.execute("UPDATE docs SET meta_json='{' WHERE doc_id='d1'")
    d = repo.get_doc(store, "d1")
    assert d["meta"] == {}, d["meta"]

    # 不对称的另一半：块级字段坏在那里就该响。静默清空 links/flags 等于把
    # 「内容丢了」伪装成成功，与损失台账契约（C-29 / O-05）相悖。
    with store.tx() as tx:
        tx.execute("UPDATE blocks SET links_json='[' WHERE doc_id='d1'")
    with pytest.raises(ValueError):
        repo.list_blocks(store, "d1")


# --- R19-API-1：POST /tokens 的 role 必填 --------------------------------------


def test_tokens_requires_an_explicit_role(api_client):
    client, tokens = api_client
    st = _app_store(client)
    h = {"X-Doc2md-Token": tokens["admin"]}
    before = st.q1("SELECT COUNT(*) c FROM tokens")["c"]

    r = client.post("/tokens", json={}, headers=h)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json()
    assert body["code"] == "E4001", body
    assert set(body["detail"]["allowed"]) == set(auth.ROLES), body["detail"]
    # 空体（无 Content-Type）走 body() 的 `{}` 分支，必须同判
    r2 = client.post("/tokens", headers=h)
    assert r2.status_code == 400 and r2.get_json()["code"] == "E4001", r2.get_json()
    assert st.q1("SELECT COUNT(*) c FROM tokens")["c"] == before, "被拒的签发不得建行"

    ok = client.post("/tokens", json={"role": "reader", "label": "x"}, headers=h)
    assert ok.status_code == 200 and ok.get_json()["role"] == "reader", ok.get_json()


# --- R19-API-5：权限矩阵无死键 -------------------------------------------------


def _used_actions() -> set[str]:
    """AST 扫全仓 `need("x")` / `auth.check(..., "x")` 的字面量 action。"""
    used: set[str] = set()
    paths = [p for d in ("doc2md", "tests", "ops") for p in (REPO_ROOT / d).rglob("*.py")
             if "__pycache__" not in p.parts]
    assert paths, "扫描面为空——红线锚点失效"
    for p in paths:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "need" and node.args and isinstance(node.args[0], ast.Constant):
                used.add(node.args[0].value)
            elif name == "check" and len(node.args) == 3 \
                    and isinstance(node.args[2], ast.Constant):
                used.add(node.args[2].value)
    return used


def test_matrix_has_no_dead_action():
    """`MATRIX` 的键与实际用到的 action 字面量必须**双向**相等。"""
    used = _used_actions()
    assert used, "一个 action 字面量都没扫到——AST 口径漂移即视为红线失效"
    dead = set(auth.MATRIX) - used
    assert not dead, (
        f"权限矩阵里的死键 {sorted(dead)}：没有任何 `need(...)` / `auth.check(...)` 会查到它。"
        "读者会以为存在这么个受控动作（本轮的 `rerun` 就是：docstring 还据此声称 editor "
        "有「重跑」，而产品的重跑路径是 `POST /jobs` 带 force，走 submit_job）。")
    undeclared = used - set(auth.MATRIX)
    assert not undeclared, (
        f"代码里用到的 action {sorted(undeclared)} 不在矩阵里——`check` 会一律 403，"
        "端点等于永久不可用")
    assert "rerun" not in auth.MATRIX, "R19-API-5 删掉的死键复活了"


def test_unknown_action_is_still_forbidden(store):
    """矩阵外动作 → 403/E4031（不是 KeyError 逃逸成 500）。"""
    from doc2md.errors import E4031_Forbidden
    # 拆开拼：`_used_actions` 会扫 tests/ 下每个 .py 的正文，写成完整字面量会被
    # 它当成「代码里用到的 action」，于是 `undeclared` 非空、上一项自己转红。
    dead = "re" + "run"
    tok = auth.issue(store, "admin", "t")
    with pytest.raises(E4031_Forbidden):
        auth.check(store, tok, dead)


# --- R19-GUI-1/2/3/4：审阅页（源码静态断言，本机无浏览器） ----------------------


def _editor() -> str:
    text = EDITOR_HTML.read_text(encoding="utf-8")
    assert "<script>" in text, "editor.html 没有内联脚本？扫描口径需复核"
    return text


def test_gui1_srch_is_mutated_not_reassigned():
    """R19-GUI-1：顶层 `const SRCH` 只能按属性改写，重新赋值必然 TypeError。

    原实现 `SRCH = {...}` 在检索翻页时抛 TypeError 并**整个中止** doSearch——
    用户点「更多命中」毫无反应，控制台之外没有任何提示。
    """
    html = _editor()
    assert "const SRCH = {" in html, "SRCH 的声明形态变了——本项锚点失效"
    # `SRCH =` 只允许出现在声明行；任何其它位置的裸赋值都是 const 重赋值
    bare = [ln for ln in html.splitlines()
            if "SRCH =" in ln and not ln.strip().startswith("const SRCH")]
    assert not bare, f"SRCH 被重新赋值（const 绑定不可改，运行时 TypeError）：{bare}"
    assert "Object.assign(SRCH" in html, "翻页游标的改写口径应走 Object.assign"


def test_gui2_audit_panels_fetch_the_newest_window():
    """R19-GUI-2：审计面服务端 ASC 且方向被冻结，客户端必须取**最后一页**。

    两个面板原先都直接 `?limit=N`，拿到的是**最旧 N 条**，却分别标成「最近 N/total 条」
    与「最新可回滚版本在前」。块被编辑超过 N 次以后标签与内容直接矛盾。
    """
    html = _editor()
    assert "async function auditNewest(" in html, "auditNewest 助手不见了——锚点失效"
    # 溢出时必须按 total-n 重取；未溢出时必须原样返回（不多付一次请求）
    assert "total <= n ? j : api(" in html, "未溢出时应复用第一次的结果"
    assert "offset=${total - n}" in html, "溢出时应取最后一页"
    # 两个面板都不得再直连审计端点。按「`api(` 的模板串里带 /audit」判定，而不是按
    # 行里出现 `/audit?`：G4' 那段注释就写着 `GET /docs/<id>/audit?block_id=…`，
    # 逐行文本匹配会把注释误判成直连调用。
    direct = re.findall(r"api\(`[^`]*?/audit", html)
    assert not direct, f"仍有面板直连审计端点（会取到最旧一页）：{direct}"
    panels = re.findall(r"auditNewest\(`[^`]*?/audit", html)
    assert len(panels) == 2, f"审计面板应是两个（showAudit / showHistory）：{panels}"
    # 服务端方向若哪天翻成 DESC，客户端这层补偿就成了双重取反——必须同时改
    api_src = (REPO_ROOT / "doc2md" / "api.py").read_text(encoding="utf-8")
    assert "FROM edit_audit WHERE {cond} ORDER BY rowid LIMIT" in api_src, (
        "审计排序已不是 rowid 升序：客户端 auditNewest 的「取最后一页」补偿随之失效，"
        "两处必须一起改（CH-500 / R17-API-5 冻结的是升序方向）")


def test_gui3_duration_column_has_one_origin():
    """R19-GUI-3：「耗时」在 running 与终态两行必须同源（都从认领起算）。

    原实现终态行写 `finished - created`，把排队等待又算了一遍——同一时间段同时出现在
    「排队等待」和「耗时」两列，读者按列相加会得出大于真实时长的数；且与 running 行的
    `now - started` 不同源，同一列在两种状态下含义不一样。
    """
    html = _editor()
    fn = html.split("function qStageCell(", 1)[1].split("\n}", 1)[0]
    assert fn, "qStageCell 解析失败——锚点失效"
    # 只看「耗时」列的赋值点：`wait` 那支的 `started ? started - created : finished
    # - created` 是**合法**用法（`started` 缺失时的兜底），整函数级 `"finished -
    # created" not in fn` 会把它一起误伤成假红。
    durs = re.findall(r"dur = fmtDur\((.+?)\);", fn)
    assert len(durs) == 2, f"「耗时」应有 running 与终态两个赋值点：{durs}"
    assert durs[0] == "now - started", f"running 行的耗时口径被顺手改掉：{durs[0]}"
    assert "finished - created" not in durs[1], (
        f"终态行的「耗时」仍从 created 起算（{durs[1]}），与「排队等待」列重复计同一段时间")
    assert "started" in durs[1], f"终态耗时应从认领时刻起算：{durs[1]}"


def test_gui4_token_input_is_debounced_and_deduped():
    """R19-GUI-4：`input` 上不得直挂两个 fetch。

    token 是 64 位十六进制，逐字输入 = 128 次请求，除最后一次全部 401；而鉴权口径是
    「每请求实时查库、无缓存」（CH-473 / R5-SVC-9），所以那是一次次真实的 SQL 查找。
    折中不是换 `change`：既有接线（editor.html 的 T4「填 Token 即触发一次」与 B-36
    「填 Token 后队列与清单一起刷」）说的是**填了就刷**，而 `change` 要等失焦才触发
    ——那是 UI 行为变更、需人工浏览器验收，不该夹在一次性能收口里。改为去抖 + 同值不重发。
    """
    html = _editor()
    tail = html.split('$("tok").addEventListener("input"', 1)
    assert len(tail) == 2, "token 的 input 监听器不见了——锚点失效"
    listener = tail[1].split("});", 1)[0]
    assert "loadQueue()" not in listener and "loadDocs()" not in listener, (
        f"input 回调里仍在直接拉数据：{listener.strip()}")
    assert "setTimeout(" in listener, "input 应经去抖后再触发"
    assert "clearTimeout(" in listener, "去抖必须重置上一次的定时器，否则收敛不了逐字输入"
    kick = html.split("function tokKick()", 1)
    assert len(kick) == 2, "tokKick 不见了——锚点失效"
    body = kick[1].split("\n}", 1)[0]
    assert "TOKSEEN.v" in body, "同值不重发的去重槽不见了"
    assert "loadQueue(); loadDocs();" in body, "B-36 的两面一起刷不得被拆开"


# --- R19-SVC-1：`PUT /settings` 的平铺点号键 ---------------------------------


def test_unknown_keys_reports_an_unknown_section_in_flat_form():
    """`unknown_keys` 对**平铺**输入也必须报出未知分表（原来只报嵌套形态）。

    病灶：原实现在「父分表未知」时 `continue`，理由写的是「所属未知分表已单独报过」——
    那个前提只对嵌套输入成立（`{"mystery": {"a": 1}}` 经 `_dotted_keys` 会同时产出
    `mystery` 与 `mystery.a`）。平铺输入 `{"log.rotate_mb": 5}` 里分表名从来不是独立
    条目，于是整条被静默吞掉。
    """
    from doc2md import config

    # 平铺：拼错分表 → 报分表名；拼错键名（分表正确）→ 报整条点号键
    assert config.unknown_keys({"log.rotate_mb": 5}) == ["log"]
    assert config.unknown_keys({"logs.rotat_mb": 5}) == ["logs.rotat_mb"]
    # 嵌套：口径逐字不变——未知分表只报表名一条，不把它下面每个键都念一遍
    assert config.unknown_keys({"mystery": {"a": 1, "b": 2}}) == ["mystery"]
    # 已知键（两种形态）与空体一律不报
    assert config.unknown_keys({"logs": {"rotate_mb": 5}}) == []
    assert config.unknown_keys({"logs.rotate_mb": 5}) == []
    assert config.unknown_keys({}) == []


def test_put_settings_flat_dotted_key_actually_takes_effect(api_client, tmp_path):
    """平铺点号键必须真的生效——原实现回 200 却什么都不改（静默失效）。

    实测原形态：PUT `{"logs.rotate_mb": 11}` → `200 {updated:["logs.rotate_mb"]}`，
    `settings_override.json` 里躺着**字面键** `{"logs.rotate_mb": 11}`，而读取侧
    `config.get(cfg, "logs.rotate_mb")` 按嵌套逐段走，永远取不到它。附录 A 与
    `config.get` 的键名口径都是点号串，客户端照文档写就必然踩这个洞。
    """
    from doc2md import config

    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["admin"]}
    r = client.put("/settings", json={"logs.rotate_mb": 11}, headers=h)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    # `updated` 回显调用方原样给出的键名，不是内部嵌套表示
    assert r.get_json()["updated"] == ["logs.rotate_mb"], r.get_json()

    eff = client.get("/settings", headers={"X-Doc2md-Token": tokens["reader"]}).get_json()
    assert config.get(eff, "logs.rotate_mb", None) == 11, eff.get("logs")

    override = tmp_path / "work" / "settings_override.json"
    onfile = json.loads(override.read_text(encoding="utf-8"))
    # 落盘必须是嵌套差量：字面点号键在重启/reload 后同样读不回来
    assert onfile == {"logs": {"rotate_mb": 11}}, onfile

    # 与嵌套形态同归一路：两种写法交替 PUT，最终态一致且互不抹除
    r2 = client.put("/settings", json={"api": {"port": 9001}}, headers=h)
    assert r2.status_code == 200, r2.get_json()
    onfile = json.loads(override.read_text(encoding="utf-8"))
    assert onfile == {"logs": {"rotate_mb": 11}, "api": {"port": 9001}}, onfile


def test_put_settings_rejects_a_typoed_section_in_flat_form(api_client, tmp_path):
    """平铺形态下拼错分表 → 400/E4001，且不得把垃圾键写进 override 文件。

    原实现回 200：附录 C 明确承诺「未知键与非法值先校验后应用 → 400/E4001」，而
    `api.py::put_settings` 的代码注释写的正是「拼错的键进了 override 文件就会在每次
    重启时静默失效」——它只挡住了已知分表里的拼错键，挡不住拼错的分表本身。
    """
    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["admin"]}
    override = tmp_path / "work" / "settings_override.json"
    before = override.read_text(encoding="utf-8") if override.exists() else None

    r = client.put("/settings", json={"log.rotate_mb": 5}, headers=h)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json()
    assert body["code"] == "E4001", body
    assert "log" in body["message"], body

    after = override.read_text(encoding="utf-8") if override.exists() else None
    assert after == before, f"被拒的写入不得落盘：{before!r} → {after!r}"


# --- R19-API-4：Content-Type 400 必须指名是哪一项不合法 ----------------------


def test_content_type_error_names_the_field(api_client):
    """非 JSON Content-Type 的 400 要带 `detail.field`（附录 C 逐字承诺过）。

    原实现只给 message 里一句英文串，`detail` 整个缺失（`errors.py::to_body` 对空
    detail 省略该键），调用方要按字符串匹配才能分支——与 PATCH 的 `detail.field`
    不同口径。
    """
    client, tokens = api_client
    h = {"X-Doc2md-Token": tokens["admin"]}
    r = client.post("/tokens", data="role=admin",
                    content_type="application/x-www-form-urlencoded", headers=h)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json()
    assert body["code"] == "E4001", body
    assert body.get("detail", {}).get("field") == "Content-Type", body
    assert body["detail"].get("got") == "application/x-www-form-urlencoded", body["detail"]
    assert "token" not in body, body
