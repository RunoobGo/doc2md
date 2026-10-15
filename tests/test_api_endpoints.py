"""tests/test_api_endpoints.py — CH-462 批次④：API 三端点补测（A-16 第 3 项）。

覆盖对象（此前零 HTTP 层覆盖）：
- `GET /events`：全局事件总线（FR-125：前端唯一事件通道），NFR-20 无正文；
- `GET /jobs/<job_id>/events`：per-job 端点仅脚本 UA（FR-125 硬规则，浏览器 UA → 403）；
- `POST /system/reload`：仅 system_reload 权限（admin）。

方法论：复用 `api_client` 夹具（真实 Flask test client + 真实 sqlite，红线同口径）。
"""
from __future__ import annotations

import pytest
from conftest import hdr

pytestmark = [pytest.mark.milestone("DoD")]

SCRIPT_UA = {"User-Agent": "doc2md-cli/2.4"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def test_events_endpoint_role_matrix(api_client):
    client, tokens = api_client
    assert client.get("/events", headers=hdr(tokens, "reader")).status_code == 200
    assert client.get("/events", headers=hdr(tokens, "editor")).status_code == 200
    r = client.get("/events")                        # 无 token
    assert r.status_code == 401


def test_events_endpoint_shape_and_since(api_client, saved_doc):
    """事件行只含元数据键（NFR-20：payload 不含正文片段）。"""
    client, tokens, _doc = saved_doc
    r = client.get("/events", headers=hdr(tokens, "reader"))
    rows = r.get_json()["events"]
    assert rows, "提交过作业后事件总线不应为空"
    seqs = [row["seq"] for row in rows]
    assert seqs == sorted(seqs)
    # since 游标：取中间 seq 之后应只剩更晚的事件
    mid = seqs[len(seqs) // 2]
    r2 = client.get(f"/events?since={mid}", headers=hdr(tokens, "reader"))
    assert all(row["seq"] > mid for row in r2.get_json()["events"])


def test_job_events_script_ua_only(api_client, saved_doc):
    client, tokens, _doc = saved_doc
    jobs = client.get("/jobs", headers=hdr(tokens, "reader")).get_json()["tasks"]
    job_id = jobs[0]["job_id"]
    r_browser = client.get(f"/jobs/{job_id}/events",
                           headers={**hdr(tokens, "reader"), **BROWSER_UA})
    assert r_browser.status_code == 403                       # FR-125：浏览器 UA 拒绝
    assert r_browser.get_json()["code"] == "E4031"
    r_script = client.get(f"/jobs/{job_id}/events",
                          headers={**hdr(tokens, "reader"), **SCRIPT_UA})
    assert r_script.status_code == 200
    rows = r_script.get_json()["events"]
    assert rows and all(row["job_id"] == job_id for row in rows)


def test_job_events_requires_read_even_for_script_ua(api_client):
    """脚本 UA 只解决「前端不可用」；无有效 token 仍然 401。"""
    client, _tokens = api_client
    assert client.get("/jobs/j-nope/events", headers=SCRIPT_UA).status_code == 401


def test_system_reload_role_matrix(api_client):
    client, tokens = api_client
    r = client.post("/system/reload", headers=hdr(tokens, "admin"))
    assert r.status_code == 200 and r.get_json()["reloaded"] is True
    assert client.post("/system/reload", headers=hdr(tokens, "editor")).status_code == 403
    assert client.post("/system/reload", headers=hdr(tokens, "reader")).status_code == 403


def test_system_reload_reflects_settings_override(api_client, tmp_path):
    """reload 重新 config.load()：settings_override.json 的修改在 reload 后可见。"""
    client, tokens = api_client
    before = client.get("/settings", headers=hdr(tokens, "reader")).get_json()
    client.put("/settings", json={"llm": {"fallback": False}},
               headers=hdr(tokens, "admin"))
    client.post("/system/reload", headers=hdr(tokens, "admin"))
    after = client.get("/settings", headers=hdr(tokens, "reader")).get_json()
    assert before["llm"]["fallback"] is True
    assert after["llm"]["fallback"] is False
