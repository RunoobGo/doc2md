"""tests/test_t4_queue_ui.py —— 队列可视化（CH-456 / T4）。

出口条件（方案 9.2 T4）：审阅页展示队列进度与失败原因，页面无需读日志即可
判断「卡在哪一步」；全程守 NFR-20（面板只渲染元数据，不含正文）。

三层验证：
1. 页面契约：editor.html 含队列面板骨架与 /jobs 数据面接线；
2. JS 语法：面板脚本过 node --check（无浏览器环境下的最低质量线）；
3. 数据面契约：/jobs 只回计数与任务元数据，失败任务带 error_code + reason，
   且任何正文串不得出现在响应里（NFR-20 反向探针）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from doc2md.storage.db import now_iso

pytestmark = [pytest.mark.milestone("DoD")]

MARKER = "队列面板唯一正文探针-T4-7c1e"      # NFR-20 反向探针


def _editor_path() -> Path:
    import doc2md
    root = Path(doc2md.__file__).resolve().parent.parent
    return root / "doc2md" / "static" / "editor.html"


# --- 1) 页面契约 --------------------------------------------------------------
def test_editor_serves_queue_panel(api_client, headers):
    client, tokens = api_client
    r = client.get("/", headers=headers(tokens, "reader"))
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # 面板骨架
    for marker in ('id="queue"', 'id="qcounts"', 'id="qrows"', 'id="qauto"',
                   'id="qrefresh"', 'id="qempty"'):
        assert marker in html, f"queue panel missing {marker}"
    # 数据面接线：拉 /jobs 计数 + 任务明细（CH-449）
    assert "/jobs?limit=50" in html
    # 失败原因渲染字段（「卡在哪一步」的两个直接判据）
    assert "error_code" in html and "reason" in html
    # 阶段机（queued/running/done/failed/rejected 五态齐全）
    for st in ("queued", "running", "done", "failed", "rejected"):
        assert st in html
    # CH-503（B-36 实机走查）：页面必须自带图标声明。浏览器默认会为 /favicon.ico
    # 发一次请求，没有声明就恒 404——控制台里唯一的错误是噪音，会盖住真问题。
    assert re.search(r'<link rel="icon" href="data:[^"]*">', html), (
        "审阅页缺内联图标声明（控制台恒有一条 404 噪音）")
    assert 'href="/favicon' not in html and 'href="favicon' not in html, (
        "图标不得指向仓库里不存在的静态路径（仍会 404）")


# --- 2) JS 语法 ---------------------------------------------------------------
def test_queue_panel_js_is_valid():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    html = _editor_path().read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    assert scripts, "editor.html has no inline script"
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(scripts[-1])                 # 最后一段是主脚本（含队列面板）
        tmp = fh.name
    try:
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        assert r.returncode == 0, f"editor.html script syntax error:\n{r.stderr}"
    finally:
        Path(tmp).unlink(missing_ok=True)


# --- 3) 数据面契约（NFR-20） ---------------------------------------------------
@pytest.fixture
def queue_env(tmp_path, tmp_cfg):
    """起真实服务 + 造五态任务各一条，其中 failed 带 error_code/reason。"""
    from doc2md import auth
    from doc2md.api import create_app
    root = tmp_path / "work"
    app = create_app(tmp_cfg, root)
    store = app.config["doc2md"]["store"]
    tokens = {r: auth.issue(store, r, f"t4-{r}") for r in ("admin", "editor", "reader")}
    ts = now_iso()
    with store.tx() as tx:
        tx.execute(
            "INSERT INTO docs(doc_id, source_key, source_name, format, pages, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("doc-t4-0001", "sk-t4-0001", "t4-sample.pdf", "pdf", 2, "done", ts, ts))
        tx.execute(
            "INSERT INTO blocks(doc_id, block_id, type, text) VALUES (?,?,?,?)",
            ("doc-t4-0001", "b1", "paragraph", MARKER))
        rows = [
            ("job-t4-q", "doc-t4-q", "sk-t4-q", "queued", ts, None, None, "", "{}",
             "/data/t4/queued.pdf", "standard"),
            ("job-t4-r", "doc-t4-r", "sk-t4-r", "running", ts, ts, None, "", "{}",
             "/data/t4/running.pdf", "lite"),
            ("job-t4-f", "doc-t4-f", "sk-t4-f", "failed", ts, ts, ts, "E4221",
             json.dumps({"reason": "页 3 抽样失败：bad page 命中 index 15"},
                        ensure_ascii=False),
             "/data/t4/bad.pdf", "strict"),
            ("job-t4-j", "doc-t4-j", "sk-t4-j", "rejected", ts, ts, ts, "E4151",
             json.dumps({"reason": "magic 不合法"}, ensure_ascii=False),
             "/data/t4/notpdf.bin", "standard"),
            ("job-t4-d", "doc-t4-0001", "sk-t4-d", "done", ts, ts, ts, "",
             json.dumps({"path": "/data/t4/ok.pdf", "zip": "x.zip", "blocks": 1},
                        ensure_ascii=False),
             "/data/t4/ok.pdf", "standard"),
        ]
        for r_ in rows:
            tx.execute(
                "INSERT INTO tasks(job_id, doc_id, source_key, status, created_at,"
                " started_at, finished_at, error_code, detail_json, source_path, mode)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", r_)
    yield app, app.test_client(), tokens, store
    app.config["doc2md"]["writer"].close()


def test_jobs_surface_reports_counts_and_failure_reason(queue_env, headers):
    _, client, tokens, _store = queue_env
    r = client.get("/jobs?limit=50", headers=headers(tokens, "reader"))
    assert r.status_code == 200
    j = r.get_json()
    assert j["counts"] == {"queued": 1, "running": 1, "failed": 1,
                           "rejected": 1, "done": 1}
    by_id = {t["job_id"]: t for t in j["tasks"]}
    bad = by_id["job-t4-f"]
    assert bad["error_code"] == "E4221"
    assert "页 3" in bad["detail"]["reason"]          # 失败原因可读（诊断元数据）
    assert by_id["job-t4-j"]["error_code"] == "E4151"
    assert by_id["job-t4-d"]["detail"]["blocks"] == 1  # 计数可读


def test_jobs_surface_is_nfr20_clean(queue_env, headers):
    """NFR-20：队列面只含元数据；正文探针不得以任何形态出现在响应里。"""
    _, client, tokens, _store = queue_env
    r = client.get("/jobs?limit=50", headers=headers(tokens, "reader"))
    blob = r.get_data(as_text=True)
    assert MARKER not in blob
    for t in r.get_json()["tasks"]:
        # 任务行只允许元数据键（块/正文/markdown 一律不得出现）
        for banned in ("text", "markdown", "body", "content", "before", "after"):
            assert banned not in t, f"task row leaks {banned}"
        assert set(t) <= {"job_id", "doc_id", "source_key", "status", "created_at",
                          "started_at", "finished_at", "error_code", "detail",
                          "source_path", "mode"}


def test_queue_panel_never_renders_block_text():
    """渲染层纪律：面板 JS 只取 error_code / reason / blocks 计数，不取任何正文字段。"""
    src = _editor_path().read_text(encoding="utf-8")
    panel = src[src.index("const QSTAGES"):src.index("async function load()")]
    assert "det.reason" in panel and "t.error_code" in panel
    assert "det.blocks" in panel                       # 只取块数计数
    # 面板不得读取任何任务/明细对象上的正文字段（.textContent 等 DOM 属性除外）
    assert re.search(r"\b(t|det|job|task|b)\.text\b", panel) is None
    assert "markdown" not in panel
    assert "before" not in panel and "after" not in panel
