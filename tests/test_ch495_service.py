"""tests/test_ch495_service.py — CH-495（R14-API）服务面回归红线。

覆盖对象（对应第 14 轮审查登记）：
- R14-API-1：请求体硬上限（MAX_CONTENT_LENGTH=32MiB），超限走 413/E4131 JSON
  契约（Werkzeug 默认回 HTML，违反附录 C「响应错误一律走该契约」）；
- R14-API-2：非法 JSON / 错误 Content-Type 不再折叠成 {}——原口径下
  PUT /settings 空转重写 override 回 200、POST /tokens 静默签出 reader token；
- R14-API-3：per-job events 端点鉴权先于 UA 咨询——未认证请求不再拿到
  403/E4031 的 detail.rule（端点语义探测面）；
- R14-API-4：重复吊销同一 token → 404（原 UPDATE 无 `revoked_at IS NULL`，
  已吊销行 rowcount 仍为 1，与附录 C「404 未知**或已吊销**」相悖）。
"""
from __future__ import annotations

import pytest

from doc2md.api import _MAX_BODY_BYTES


def _hdr(tokens: dict, role: str = "admin") -> dict:
    return {"X-Doc2md-Token": tokens[role]}


@pytest.mark.gate
def test_body_limit_configured_and_413_is_json(api_client):
    client, tokens = api_client
    assert client.application.config["MAX_CONTENT_LENGTH"] == _MAX_BODY_BYTES
    # 把上限临时压到 10B 以触发 413 处理器（断言的是契约映射，不是 32MiB 本身）
    client.application.config["MAX_CONTENT_LENGTH"] = 10
    r = client.post("/jobs", data="x" * 64, content_type="application/json",
                    headers=_hdr(tokens, "editor"))
    assert r.status_code == 413
    body = r.get_json()
    assert body["code"] == "E4131", body
    assert body["detail"]["max_bytes"] == _MAX_BODY_BYTES


@pytest.mark.gate
def test_put_settings_invalid_json_400(api_client):
    client, tokens = api_client
    r = client.put("/settings", data="{not json",
                   content_type="application/json", headers=_hdr(tokens))
    assert r.status_code == 400
    assert r.get_json()["code"] == "E4001"
    # 空转不再发生：没有 settings_updated 事件
    ev = client.get("/events", headers=_hdr(tokens, "reader")).get_json()["events"]
    assert not [e for e in ev if e["code"] == "settings_updated"], ev


@pytest.mark.gate
def test_put_settings_wrong_content_type_400(api_client):
    client, tokens = api_client
    r = client.put("/settings", data="api.bind=127.0.0.1",
                   content_type="text/plain", headers=_hdr(tokens))
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"


@pytest.mark.gate
def test_put_settings_empty_body_still_ok(api_client):
    """合法空对象体（{}）与无体请求维持既有 200 口径——只收紧「非法」。"""
    client, tokens = api_client
    r = client.put("/settings", json={}, headers=_hdr(tokens))
    assert r.status_code == 200 and r.get_json()["updated"] == []


@pytest.mark.gate
def test_post_tokens_invalid_json_400(api_client):
    client, tokens = api_client
    r = client.post("/tokens", data="[1,2", content_type="application/json",
                    headers=_hdr(tokens))
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"


@pytest.mark.gate
def test_job_events_unauthenticated_is_401_not_403(api_client):
    client, _tokens = api_client
    r = client.get("/jobs/j-x/events",
                   headers={"User-Agent": "Mozilla/5.0 (browser)"})
    assert r.status_code == 401, (r.status_code, r.get_json())


@pytest.mark.gate
def test_double_revoke_returns_404(api_client):
    client, tokens = api_client
    tok = client.post("/tokens", json={"role": "reader", "label": "t"},
                      headers=_hdr(tokens)).get_json()["token"]
    r1 = client.delete(f"/tokens/{tok}", headers=_hdr(tokens))
    assert r1.status_code == 200 and r1.get_json()["revoked"] is True
    r2 = client.delete(f"/tokens/{tok}", headers=_hdr(tokens))
    assert r2.status_code == 404 and r2.get_json()["code"] == "E4041"
    # 已吊销 token 本身立即失效（既有口径不回退）
    r3 = client.get("/events", headers={"X-Doc2md-Token": tok})
    assert r3.status_code == 401
