"""tests/test_ch496_local.py — CH-496 本机可执行待办的红线。

覆盖：
- F11：转换档位白名单单一来源（`config.MODES`）——API / CLI / 配置文件三处
  不得再各自硬编码，避免「能传/拒绝」口径分裂；
- F7：生产模块不得再出现错误码字符串字面量（测试保留字面量作线上口径）；
- B5：`apply_ops` 合并后不得留下可寻址的已吸收块；
- G8：`--max-jobs` 负数必须是用例错误，不得静默只跑一批；
- 架构：`reclaim_stale` 计数与 `_claim_batch` 同路径模式冲突口径；
- G4'：审阅页必须提供回滚入口且不拼接 innerHTML。
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from doc2md import config
from doc2md.api import create_app  # noqa: F401  —— 确保模块可导入（F11 删除 _MODES 后）
from doc2md.cli import build_parser
from doc2md.config.loader import SPEC

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_ROOT = REPO_ROOT / "doc2md"


@pytest.mark.gate
def test_f11_modes_single_source():
    from doc2md import api as api_mod

    assert config.MODES == ("auto", "single", "two_phase")
    assert not hasattr(api_mod, "_MODES"), "api._MODES 应已删除，统一走 config.MODES"

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    convert = sub.choices["convert"]
    mode = next(a for a in convert._actions if "--mode" in a.option_strings)
    assert tuple(mode.choices) == config.MODES

    assert SPEC["s2.mode"] == ("enum", config.MODES)


@pytest.mark.gate
def test_f7_no_error_code_literals_in_production():
    """生产代码里的错误码必须引用 errors 常量，不能写字面量。

    排除 `errors.py`（码的定义处）。只认长度 5 且完整匹配 `E\\d{4}` 的常量，
    因此 docstring / 说明文本里的码不会被误判（它们是更长的字符串）。
    """
    import re

    lit = re.compile(r"^E\d{4}$")
    offenders: list[str] = []
    for path in sorted(PROD_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "errors.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and lit.match(node.value)):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, "生产模块仍有错误码字面量：\n" + "\n".join(offenders)


@pytest.mark.gate
def test_b5_merge_prunes_id_index():
    """合并后索引同步：已被吸收的 id 不得再被后续 op 寻址。

    CH-496 / B5：`index` 只在 split 时增项、merge 时不减项——第二次 op
    命中已移除的块时 `b` 仍非空，op 照常执行并计入 changed，产物却已无该块。
    """
    from doc2md.ir.model import IR, Block
    from doc2md.llm import apply_ops

    b1 = Block(id="b1", type="paragraph", text="甲", page=1)
    b2 = Block(id="b2", type="paragraph", text="乙", page=1)
    ir = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=1)
    ir.blocks = [b1, b2]

    eff = apply_ops(ir, [{"op": "merge_block", "target": "b2"}], return_report=True)
    assert eff[0].changed and len(ir.blocks) == 1, eff

    eff2 = apply_ops(ir, [{"op": "fix_heading_level", "target": "b2", "level": 2}],
                     return_report=True)
    assert not eff2[0].changed
    assert eff2[0].reason == "target_missing", eff2


@pytest.mark.gate
def test_g8_max_jobs_negative_is_usage_error():
    """`--max-jobs -1` 是用例错误（argparse exit 2），不是「第一批后静默退出」。"""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["work", "--max-jobs", "-1"])
    assert exc.value.code == 2


@pytest.mark.gate
def test_g8_drop_orphan_waits_for_artifact_lock(tmp_path):
    """孤儿清除与同 doc 重建串行：锁被占用时不得先查后删。

    CH-496 / G8：原实现检查 docs 与 purge 之间无互斥，重建刚发布的新 zip
    可能被删。此用例在持锁期间启动清除线程，断言它必须等待锁释放。
    """
    from doc2md import artifacts
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    out = root / "out"
    tmp = root / "tmp"
    out.mkdir(parents=True)
    tmp.mkdir()
    (out / "d1.zip").write_bytes(b"x")
    store = Store(root / "state.sqlite")
    cfg = {"paths": {"root": str(root), "out": str(out), "tmp": str(tmp)}}
    done = threading.Event()

    def _run() -> None:
        artifacts.drop_orphan_artifact(store, root, cfg, "d1")
        done.set()

    with artifacts._artifact_lock(out, "d1"):
        t = threading.Thread(target=_run)
        t.start()
        assert not done.wait(0.2), "锁被占用时清除线程不应完成"
    t.join(5)
    assert done.is_set()
    assert not (out / "d1.zip").exists()
    store.close()


@pytest.mark.gate
def test_arch_reclaim_stale_counts_actual_rows(tmp_path):
    """回收计数 = 实际改到的行数；第二次调用必须是 0（不虚报）。"""
    from doc2md.storage.db import Store
    from doc2md.worker import reclaim_stale

    store = Store(tmp_path / "state.sqlite")
    with store.tx() as tx:
        tx.execute(
            "INSERT INTO tasks(job_id, doc_id, status, created_at, started_at,"
            " source_path) VALUES (?,?,?,?,?,?)",
            ("j1", "d1", "running", "2020-01-01T00:00:00.000+00:00",
             "2020-01-01T00:00:00.000+00:00", "/tmp/a.pdf"))
    cfg = {"perf": {"stale_job_minutes": 30}}
    assert reclaim_stale(store, cfg) == 1
    assert reclaim_stale(store, cfg) == 0
    store.close()


@pytest.mark.gate
def test_arch_claim_batch_pins_first_mode_and_notes_conflict(tmp_path, monkeypatch):
    """同路径多作业档位冲突：钉死「最早已入队作业的 mode」，单池一次跑完并留 note。"""
    from doc2md import worker
    from doc2md.storage.db import Store
    from doc2md.worker import WorkReport, _claim_batch

    store = Store(tmp_path / "state.sqlite")
    with store.tx() as tx:
        for job, doc, ts, mode in (
                ("j1", "d1", "2026-01-01T00:00:00.000+00:00", "single"),
                ("j2", "d2", "2026-01-01T00:00:01.000+00:00", "two_phase")):
            tx.execute(
                "INSERT INTO tasks(job_id, doc_id, status, created_at,"
                " source_path, mode) VALUES (?,?,?,?,?,?)",
                (job, doc, "queued", ts, "/tmp/a.pdf", mode))
    calls: list[dict] = []
    monkeypatch.setattr(worker, "run_batch", lambda *a, **kw: calls.append(kw) or [])
    rep = WorkReport()
    _claim_batch(store, {"paths": {"out": str(tmp_path / "out")}},
                 tmp_path, 10, rep)
    assert len(calls) == 1, calls
    assert calls[0]["plans"] == [("/tmp/a.pdf", "single", False)], calls
    assert "mode" not in calls[0] and "force" not in calls[0], calls
    assert any("mixed modes" in n for n in rep.notes), rep.notes
    store.close()


@pytest.mark.gate
def test_arch_db_permissions_are_private(tmp_path):
    """库目录/主库/WAL 必须只对当前用户可访问（含首次迁移创建出的边车文件）。

    CH-498 / B-16：原来整项用 `skipif(nt)` 跳过，而 CH-496 恰恰改了 `Store.__init__`
    的收紧顺序（目录先 0700 再建库，随后主库与 -wal/-shm）——Windows 走的是
    `ensure_private` 的 icacls 分支，改却没有回归。改为两端都跑：POSIX 断精确 mode 位，
    Windows 断 `is_private()`（仅当前用户 DACL、无继承 ACE）。
    """
    from doc2md.security.perms import is_private
    from doc2md.storage.db import Store

    root = tmp_path / "work"
    store = Store(root / "state.sqlite")
    sides = [root / f"state.sqlite{s}" for s in ("-wal", "-shm")
             if (root / f"state.sqlite{s}").exists()]
    paths = [root, root / "state.sqlite", *sides]
    if os.name == "nt":
        not_private = [str(p) for p in paths if not is_private(p)]
        assert not not_private, f"Windows DACL 未收紧到仅当前用户: {not_private}"
    else:
        assert (root.stat().st_mode & 0o777) == 0o700
        for p in paths[1:]:
            assert (p.stat().st_mode & 0o777) == 0o600, str(p)
    store.close()


# ------------------------------------------------------------------ G4' / B-11

EDITOR = PROD_ROOT / "static" / "editor.html"
_SCRIPT_RE = re.compile(r"<script>\s*(.*?)\s*</script>", re.S)


def _editor_source() -> str:
    return EDITOR.read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    m = re.search(rf"^async function {name}\(.*?\) \{{\n(.*?)^\}}", src, re.S | re.M)
    assert m is not None, f"editor.html 缺少 async function {name}（G4' 入口漂移）"
    return m.group(1)


@pytest.mark.gate
def test_g4_rollback_ui_contract():
    """G4'（FR-211）：块编辑区必须有「历史 → 选择版本 → 回滚」入口。

    回滚数据来自 `GET /docs/<id>/audit?block_id=`（before/from_revision 一一对应
    `edits.rollback` 的取文口径），提交走 `POST .../rollback`。审计正文来自源文档，
    属不可信输入——新入口一律 DOM + textContent 构造，不得拼 innerHTML。
    """
    src = _editor_source()
    assert 'data-act="history"' in src, "缺少「历史」按钮"
    assert 'data-hist' in src, "缺少块内历史面板容器"
    assert 'method: "POST"' in src and "/rollback`" in src, "缺少回滚 POST 调用"
    assert "to_revision" in src, "回滚请求必须携带 to_revision"

    # 容器身份面与否定面同条消息：成员命中证明取到的是真函数体（不是空壳切片），
    # innerHTML 计 0 才是 XSS 红线本身——两个数缺一个，切错容器时否定断言恒真。
    _members = {"showHistory": ("textContent", "createElement", "appendChild"),
                "rollbackTo": ("/rollback", "to_revision", "fetch(")}
    for name in ("showHistory", "rollbackTo"):
        body = _function_body(src, name)
        _hits = sum(m in body for m in _members[name])
        assert (_hits, body.count("innerHTML")) == (len(_members[name]), 0), (
            f"{name} 容器判据失准：DOM/回滚成员命中 {_hits}/{len(_members[name])}"
            f"（缺 {[m for m in _members[name] if m not in body]}），"
            f"innerHTML 命中 {body.count('innerHTML')} 次（应为 0；G4' XSS 红线）")
    # 列表渲染（唯一写入审计文本的入口）必须用 textContent；rollbackTo 只提交，
    # 成功后复用 showHistory 重绘。
    assert "textContent" in _function_body(src, "showHistory")


@pytest.mark.gate
def test_g4_editor_script_syntax(tmp_path):
    """提取内联脚本做语法校验（等价 `node --check`）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过 JS 语法校验（不改变产品行为）")
    src = _editor_source()
    m = _SCRIPT_RE.search(src)
    assert m is not None, "editor.html 的 <script> 结构漂移"
    js = tmp_path / "editor.js"
    js.write_text(m.group(1), encoding="utf-8")
    proc = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.gate
def test_serve_startup_reports_bound_port():
    """B-11 走查发现：`--port 0` 时启动行原打印用户请求的 `:0`，与 Flask
    实际绑定端口不一致。serve 必须先绑定、再按 `server.server_port` 报告。"""
    src = (PROD_ROOT / "cli.py").read_text(encoding="utf-8")
    m = re.search(r"^def cmd_serve\(.*?(?=^def )", src, re.S | re.M)
    assert m is not None, "cli.cmd_serve 结构漂移"
    body = m.group(0)
    assert "make_server(" in body and "server.server_port" in body
    # 「先绑定、再报告」的三个成员与「请求端口直跑」的否定同条消息：容器切空时
    # 只会剩 0/3，读的人立刻知道是切片失准而不是启动行合规。
    _members = ("make_server(", "server.server_port", "print(")
    _hits = sum(m in body for m in _members)
    assert (_hits, body.count("app.run(host=")) == (len(_members), 0), (
        f"cmd_serve 容器判据失准：绑定/报告成员命中 {_hits}/{len(_members)}"
        f"（缺 {[m for m in _members if m not in body]}），"
        f"app.run(host= 命中 {body.count('app.run(host=')} 次（应为 0）"
        "——启动行必须使用实际绑定端口，而不是请求端口")


@pytest.mark.gate
def test_search_panel_reports_truncation_and_cross_doc_hits():
    """CH-498 / B-11 浏览器走查发现：检索面板把两件 API 已经明说的事藏了。

    Chromium 实测（5770 块 / 跨 5 文档的库）：`GET /search` 在 `has_more=true`
    （命中多于本页）时，页面仍只报「20 命中」，用户会以为是全部；而 `hits[].doc_id`
    属其他文档的命中被计入「未载入的块窗口内（点「加载更多」后重新检索）」——
    当前文档的窗口再大也永远不会出现它们。两处都是 GUI 与 API 契约不一致，
    数据本身没错，故只补提示、不动服务端。
    """
    body = _function_body(_editor_source(), "doSearch")
    assert "has_more" in body, "未读 has_more：截断结果被当成全部命中报出"
    assert "VIEW.doc" in body, "未按 doc_id 区分跨文档命中"
    # 失败 / 主行 / 窗口外 / 跨文档 四条提示各自独立，合并即回退成走查看到的旧行为
    assert body.count("log(") >= 4, "跨文档命中与窗口外命中必须分别提示"


@pytest.mark.gate
def test_queue_chip_filter_requests_server_side_status():
    """CH-498 / B-11 浏览器走查发现：状态 chip 的客户端过滤会「假空」。

    实测（51 条任务 + `GET /jobs?limit=50`，`ORDER BY created_at DESC`）：点「失败」
    chip 时表格全空并弹出「暂无任务」，而 chip 上的计数写着 1——那条失败作业
    （`broken.pdf` / E4151）排在窗口之外。`/jobs` 本来就支持 `?status=`，走查后
    改为过滤态由服务端判定，窗口截断不再影响可见性。
    """
    src = _editor_source()
    load = _function_body(src, "loadQueue")
    assert "QSTATE.filter" in load and "status=" in load, "loadQueue 必须按过滤态请求 /jobs?status="
    m = re.search(r"b\.onclick = \(\) => \{(.*?)\};", src, re.S)
    assert m is not None, "renderCounts 的 chip 点击处理漂移"
    assert "loadQueue()" in m.group(1), "chip 点击须重新拉取（过滤交给服务端）"
    # 服务端过滤成员与「退回纯客户端过滤」的否定同条消息：组内命中 < 3 即切片
    # 取窄（正则失配只剩空串），此时旧的 `not in` 无论产品怎么改都恒真。
    _members = ("QSTATE.filter = ", "loadQueue()", "(QSTATE.filter === st")
    _hits = sum(t in m.group(1) for t in _members)
    assert (_hits, m.group(1).count("renderQueue(QSTATE.tasks)")) == (len(_members), 0), (
        f"chip 点击容器判据失准：服务端过滤成员命中 {_hits}/{len(_members)}"
        f"（缺 {[t for t in _members if t not in m.group(1)]}），"
        f"renderQueue(QSTATE.tasks) 命中 {m.group(1).count('renderQueue(QSTATE.tasks)')} 次"
        "（应为 0）——不得退回纯客户端过滤")
