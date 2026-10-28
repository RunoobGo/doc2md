"""CH-505 / OPT4-5：附录 C 契约表 ↔ 实测状态码双向对账（R2 / C-1..C-6）。

附录 C 的每一行过去由设计意图写出，没有任何断言读过它。QA 第四轮实测抓到六项
偏差，其中 C-1 最典型：`POST /jobs` 的错误列承诺「409 `E4091`」，而该状态码在
整个 API 面不可达——E4091 的两条抛出路径都被就地捕获、折进 202 的逐文件信封。
调用方按文档写 409 重试分支，分支永远不执行，同时漏掉真实需要处理的 202/conflict。

本文件把「文档承诺」与「实测可达」做成双向差：

- **仅在文档** → 文档承诺了探针打不出的形态（C-1 即此类）；
- **仅在实测** → 实测可达但契约未登记。本轮据此补登了三处：`GET /events` 的
  400（`since` 非数字）、`DELETE /docs|sources` 的 404（对象不存在），以及
  `POST /jobs` 的 422——它从未作为响应送出（门禁拒收与 409 同型，折进 202），
  原表把它写成可重试的 HTTP 码，与 C-1 是同一个病灶。

口径（写进附录 C 前言，本文件同时断言该前言存在）：

- 成功列只登记 2xx；错误列只登记**端点自身**的 4xx/5xx；
- 401 与角色型 403 由「所需角色」列统一推导（reader → 401；editor/admin →
  401 + 403），不在每行重复——避免「表里写 403、实际 reader 端点永远打不出」
  这类同业噪声；
- 括号内是解释，不是声明；`无 409` / `无 HTTP 422` 这类否定式不构成声明。

锚点解析失败必须红（沿用 `test_ch491_doc_sync.py` 的纪律）：解析不到 21 行、
角色列不认识、表格与路由表不等，都按红线失效处理，绝不 skip——解析不到就跳过
会让红线**静默恒真**，比漂移更糟。

探针矩阵只对生产代码里真实存在的异常出口做故障注入（磁盘清除失败、索引查询
失败、zip 损坏、同步池占用），不凭空制造状态码；每一次注入都在注释里写明它
模拟的故障形态。
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from doc2md import config

pytestmark = [pytest.mark.gate]

REPO_ROOT = Path(config.__file__).resolve().parents[2]
DEV_PLAN = REPO_ROOT / "docs" / "dev-plan.md"
SECTION_START = "## 附录 C API 全表"
SECTION_END = "## 附录 D"
AUTH_ANCHOR = "鉴权错误的统一口径"

ROW_RE = re.compile(r"^\| `([A-Z]+) (/[^`]*)` \|")
ROLE_RE = re.compile(r"^(reader|editor|admin)\b")
CODE_RE = re.compile(r"(?<![\d.])(\d{3})(?![\d])")

# 变更登记号不是状态码：`CH-504` / `R19-ARCH-5` / `E5031_StorageError` / `W1045`
# 里的三位数字若被当成声明，会把「CH-504 校正」读成 504、把错误码读成 409。
_TRACKERS = (
    re.compile(r"\b(?:[A-Z]{1,4}\d*)(?:-[A-Za-z0-9]+)+\b"),
    re.compile(r"\b[A-Z]{1,3}\d{4}"),
)
# 否定式：`**无 409**` / `无 HTTP 422` 不是声明（C-1 改正后的形态）。
_NEGATION = re.compile(r"(?:无|不可达|不返回|不送出)\s*(?:HTTP\s*)?\**\s*(\d{3})")

ROLES = ("reader", "editor", "admin")
# 附录 C 契约的端点行数：扫描到的端点行与解析出的键数都必须等于它，
# 否则「不得重复登记」这条否定对空集合恒真（B-53 成对判据的命中基准）。
EXPECTED_ENDPOINT_ROWS = 21


def _section() -> str:
    text = DEV_PLAN.read_text(encoding="utf-8")
    start = text.find(SECTION_START)
    assert start >= 0, "附录 C 标题漂移，契约对账锚点失效"
    end = text.find(SECTION_END, start)
    assert end > start, "附录 D 起始锚点漂移，契约对账锚点失效"
    return text[start:end]


def _strip_fullwidth_parens(text: str) -> str:
    """去掉全角括号段——括号里是解释与病史，不是契约声明。"""
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "（":
            depth += 1
        elif ch == "）":
            if depth:
                depth -= 1
            else:
                out.append(ch)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _codes(text: str) -> list[str]:
    s = _NEGATION.sub(" ", _strip_fullwidth_parens(text))
    for rx in _TRACKERS:
        s = rx.sub(" ", s)
    return CODE_RE.findall(s)


def _parse_section(section: str) -> dict[tuple[str, str], dict]:
    """`{(方法, 路径): {role, success, errors}}`；解析异常即红线失效。"""
    rows: dict[tuple[str, str], dict] = {}
    dups: list[tuple[str, str]] = []
    n_lines = 0
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        n_lines += 1
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"附录 C 行不是 5 列（格式漂移）：{line[:90]}"
        m = ROW_RE.match(line)
        assert m is not None, f"附录 C 端点格无法解析：{line[:90]}"
        key = (m.group(1), m.group(2))
        if key in rows:
            dups.append(key)
        rm = ROLE_RE.match(cells[1])
        role = rm.group(1) if rm else None
        if role is None:
            assert cells[1].startswith("无"), (
                f"附录 C 角色列既不是 reader/editor/admin 也不是「无」：{cells[1]!r}")
        success = {c for c in _codes(cells[3]) if c.startswith("2")}
        errors = {c for c in _codes(cells[4]) if c[0] in "45"}
        assert success, f"{key} 未解析到任何成功码（成功列锚点失效）"
        rows[key] = {"role": role, "success": success, "errors": errors}
    assert (dups, len(rows), n_lines) == (
        [], EXPECTED_ENDPOINT_ROWS, EXPECTED_ENDPOINT_ROWS), (
        f"附录 C 端点登记自证失败：重复登记 {dups}（应为空），"
        f"解析出 {len(rows)} 行 / 扫描到 {n_lines} 条端点行"
        f"（后两个数均应为 {EXPECTED_ENDPOINT_ROWS}；解析丢行或锚点失效）")
    return rows


def _auth_codes(role: str | None) -> set[str]:
    """「所需角色」列隐含的鉴权错误：reader 端点永远打不出角色型 403。"""
    if role is None:
        return set()
    assert role in ROLES, role
    return {"401"} | ({"403"} if role in ("editor", "admin") else set())


# ------------------------------------------------------------------ 解析器自检
def test_appendix_c_parser_has_teeth() -> None:
    """解析器先自证：否定式、括号解释、变更号都不得被读成声明。"""
    section = _section()
    assert section.count(AUTH_ANCHOR) == 1, (
        f"附录 C 前言缺少「{AUTH_ANCHOR}」——本文件的 401/403 推导规则必须在"
        "文档里可见，否则红线在核一份读者看不到的契约")

    rows = _parse_section(section)
    assert rows[("GET", "/")]["role"] is None
    assert rows[("GET", "/healthz")]["role"] is None

    # 否定式不是声明；带 `HTTP` 的否定式同样不是。
    assert _codes("**无 409**；413 空间不足；**无 HTTP 422**") == ["413"]
    # 变更登记号 / 错误码里的数字不是状态码。
    assert _codes("CH-504 校正；R19-ARCH-5；E5031_StorageError；500 存储失败") == ["500"]
    # 括号内是解释（含历史 503），不是声明。
    assert _codes("404；500 E5031（errors.py: E5031_StorageError.http=500；"
                  "本行原记 503）") == ["404", "500"]

    # 当前契约不得再承诺 C-1 的 409 / 同型的 HTTP 422；同一行必须仍登记实测
    # 可达的五个错误码——否则两条字面否定对空错误集合恒真。
    _errs = rows[("POST", "/jobs")]["errors"]
    _want = frozenset({"400", "404", "413", "415", "503"})
    _hits = len(_want & _errs)
    assert (_hits, "409" in _errs, "422" in _errs) == (len(_want), False, False), (
        f"POST /jobs 错误列自证失败：必存错误码命中 {_hits}/{len(_want)}"
        f"（缺 {sorted(_want - _errs)}），409 命中 {int('409' in _errs)} 次、"
        f"422 命中 {int('422' in _errs)} 次（应为 {len(_want)} / 0 / 0）")

    # 变异证明：把 C-1 的旧承诺塞回文档，解析器必须当场报出 409——
    # 红线若读不出它，下面的双向差也就抓不到「文档承诺不可达形态」。
    mutated = section.replace("**无 409**", "409", 1)
    assert mutated != section, "附录 C 里找不到「无 409」——变异锚点失效"
    assert "409" in _parse_section(mutated)[("POST", "/jobs")]["errors"]


def test_appendix_c_status_codes_match_live_routes(saved_doc, small_pdf,
                                                   tmp_path, monkeypatch) -> None:
    """附录 C 声明的状态码集合 == 实测可达集合（双向差，缺一即红）。"""
    client, tokens, doc_a = saved_doc
    app = client.application
    routes = {(method, r.rule)
              for r in app.url_map.iter_rules() if r.endpoint != "static"
              for method in r.methods
              if method in ("GET", "POST", "PUT", "PATCH", "DELETE")}
    rows = _parse_section(_section())
    assert set(rows) == routes, (
        "附录 C 与路由表不同源：仅在文档 "
        f"{sorted(set(rows) - routes)}，仅在路由 {sorted(routes - set(rows))}")

    observed: dict[tuple[str, str], set[str]] = defaultdict(set)
    specific: set[tuple[str, str]] = set()

    def url_for(rule: str, **params) -> str:
        return re.sub(r"<(\w+)>", lambda m: str(params.get(m.group(1), "probe")), rule)

    def hit(method: str, rule: str, url: str, *, role: str | None = None,
            kind: str = "specific", headers: dict | None = None, **kw):
        h = dict(headers or {})
        if role:
            h["X-Doc2md-Token"] = tokens[role]
        r = client.open(url, method=method, headers=h, **kw)
        observed[(method, rule)].add(str(r.status_code))
        if kind == "specific":
            specific.add((method, rule))
        return r

    # 鉴权前置：无凭据 → 401；角色不足 → 403。角色列是唯一真源。
    for method, rule in sorted(routes):
        role = rows[(method, rule)]["role"]
        if role is None:
            continue
        hit(method, rule, url_for(rule), kind="auth")
        if role in ("editor", "admin"):
            lower = "reader" if role == "editor" else "editor"
            hit(method, rule, url_for(rule), role=lower, kind="auth")

    # --- POST /jobs：202 全形态 + 400/404/413/415/503（409/422 必须不出现）---
    r = hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(small_pdf)})
    assert r.status_code == 202, (r.status_code, r.get_data(as_text=True)[:200])
    hit("POST", "/jobs", "/jobs", role="editor", json={})
    hit("POST", "/jobs", "/jobs", role="editor",
        json={"path": str(small_pdf), "mode": "singel"})
    hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(tmp_path / "missing.pdf")})
    bad = tmp_path / "not_really.pdf"
    bad.write_bytes(b"plain text, no magic")
    hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(bad)})
    # 门禁拒收（E4221）走 202 逐文件 rejected；若哪天冒出 HTTP 422，双向差会红。
    scanned = REPO_ROOT / "tests" / "corpus" / "sources" / "pdf_06_scanned_1p.pdf"
    hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(scanned)})
    # 413：MAX_CONTENT_LENGTH 超限（与磁盘不足同走 E4131 的 413 段位）。
    big = b"{" + b" " * (33 * 1024 * 1024) + b"}"
    hit("POST", "/jobs", "/jobs", role="editor", data=big,
        content_type="application/json")
    # 503：同步池被占用且等不到——把等待缩到 10ms，免得门禁真等 60s。
    r = hit("PUT", "/settings", "/settings", role="admin",
            json={"perf": {"sync_min_parallel": 2, "sync_min_bytes": 0}})
    assert r.status_code == 200, r.get_json()
    two = tmp_path / "two"
    two.mkdir()
    (two / "a.pdf").write_bytes(small_pdf.read_bytes())
    (two / "b.pdf").write_bytes(small_pdf.read_bytes())
    import doc2md.api as api_mod
    monkeypatch.setattr(api_mod, "_SYNC_GATE_WAIT_S", 0.01)
    assert api_mod._SYNC_POOL_GATE.acquire(timeout=1)
    try:
        r = hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(two)})
        assert r.status_code == 503, r.get_json()
    finally:
        api_mod._SYNC_POOL_GATE.release()

    # 第二篇文档（不同内容 → 不同 doc_id）：删除面两条端点各用一篇，互不干扰。
    docx_src = REPO_ROOT / "tests" / "corpus" / "sources" / "docx_01_simple.docx"
    r = hit("POST", "/jobs", "/jobs", role="editor", json={"path": str(docx_src)})
    assert r.status_code == 202, r.get_json()
    doc_b = r.get_json()["jobs"][0]["doc_id"]
    assert doc_b != doc_a

    # --- 读面 / 窗口参数 ---
    hit("GET", "/jobs", "/jobs", role="reader")
    hit("GET", "/jobs", "/jobs?limit=abc", role="reader")
    hit("GET", "/docs", "/docs", role="reader")
    hit("GET", "/docs", "/docs?limit=abc", role="reader")
    r = hit("GET", "/docs/<doc_id>", f"/docs/{doc_a}", role="reader")
    blocks = r.get_json()["blocks"]
    mutable = next(b for b in blocks if not b["immutable"])
    bid, rev, old = mutable["id"], int(mutable["revision"]), mutable["text"]
    hit("GET", "/docs/<doc_id>", f"/docs/{doc_a}?offset=abc", role="reader")
    hit("GET", "/docs/<doc_id>", "/docs/does-not-exist", role="reader")
    hit("GET", "/docs/<doc_id>/markdown", f"/docs/{doc_a}/markdown", role="reader")
    hit("GET", "/docs/<doc_id>/markdown", "/docs/does-not-exist/markdown", role="reader")

    # --- 编辑面：400（缺 base_revision）/ 404 / 425（陈旧版本）/ 200 ---
    block_url = f"/docs/{doc_a}/blocks/{bid}"
    hit("PATCH", "/docs/<doc_id>/blocks/<block_id>", block_url,
        role="editor", json={"text": "x"})
    hit("PATCH", "/docs/<doc_id>/blocks/<block_id>",
        "/docs/does-not-exist/blocks/b1", role="editor",
        json={"text": "x", "base_revision": 1})
    hit("PATCH", "/docs/<doc_id>/blocks/<block_id>", block_url,
        role="editor", json={"text": "x", "base_revision": rev + 1})
    r = hit("PATCH", "/docs/<doc_id>/blocks/<block_id>", block_url,
            role="editor", json={"text": old + " CH-505", "base_revision": rev})
    assert r.status_code == 200, r.get_json()
    rollback_url = f"/docs/{doc_a}/blocks/{bid}/rollback"
    r = hit("POST", "/docs/<doc_id>/blocks/<block_id>/rollback", rollback_url,
            role="editor", json={"to_revision": rev})
    assert r.status_code == 200, r.get_json()
    hit("POST", "/docs/<doc_id>/blocks/<block_id>/rollback", rollback_url,
        role="editor", json={"to_revision": "abc"})
    hit("POST", "/docs/<doc_id>/blocks/<block_id>/rollback",
        f"/docs/{doc_a}/blocks/nope/rollback", role="editor",
        json={"to_revision": rev})

    # --- 审计面 ---
    hit("GET", "/docs/<doc_id>/audit", f"/docs/{doc_a}/audit", role="editor")
    hit("GET", "/docs/<doc_id>/audit", f"/docs/{doc_a}/audit?limit=abc", role="editor")
    hit("GET", "/docs/<doc_id>/audit", "/docs/does-not-exist/audit", role="editor")
    hit("GET", "/audit/deletions", "/audit/deletions", role="admin")
    hit("GET", "/audit/deletions", "/audit/deletions?offset=abc", role="admin")

    # --- 搜索 / 事件 ---
    hit("GET", "/search", "/search?q=test", role="reader")
    hit("GET", "/search", "/search?limit=abc", role="reader")

    def search_boom(*_a, **_k):
        raise RuntimeError("index unavailable")     # 索引查询失败的形态

    monkeypatch.setattr("doc2md.api.search", search_boom)
    hit("GET", "/search", "/search?q=test", role="reader")
    hit("GET", "/events", "/events", role="reader")
    hit("GET", "/events", "/events?since=abc", role="reader")
    job_id = hit("GET", "/jobs", "/jobs", role="reader").get_json()["tasks"][0]["job_id"]
    hit("GET", "/jobs/<job_id>/events", f"/jobs/{job_id}/events", role="reader",
        headers={"User-Agent": "doc2md-cli/1.0"})
    hit("GET", "/jobs/<job_id>/events", f"/jobs/{job_id}/events", role="reader",
        headers={"User-Agent": "Mozilla/5.0"})

    # --- 设置 / reload / token ---
    hit("GET", "/settings", "/settings", role="reader")
    hit("PUT", "/settings", "/settings", role="admin", json={"nope": {"x": 1}})
    hit("POST", "/system/reload", "/system/reload", role="admin")
    override = Path(app.config["doc2md"]["root"]) / "settings_override.json"
    saved_override = override.read_bytes()
    try:
        override.write_text("{broken", encoding="utf-8")
        hit("POST", "/system/reload", "/system/reload", role="admin")
    finally:
        override.write_bytes(saved_override)

    fresh = hit("POST", "/tokens", "/tokens", role="admin",
                json={"role": "reader"}).get_json()["token"]
    hit("POST", "/tokens", "/tokens", role="admin", json={})
    hit("DELETE", "/tokens/<token>", "/tokens/does-not-exist", role="admin")
    hit("DELETE", "/tokens/<token>", f"/tokens/{fresh}", role="admin")
    hit("GET", "/", "/")
    hit("GET", "/healthz", "/healthz")

    # --- 产物损坏 → markdown 500（放在编辑/回滚重建之后，别毁掉它们的输入）---
    zp = Path(app.config["doc2md"]["root"]) / "out" / f"{doc_a}.zip"
    assert zp.exists(), f"产物缺失：{zp}"
    zp.write_bytes(b"not a zip")
    hit("GET", "/docs/<doc_id>/markdown", f"/docs/{doc_a}/markdown", role="reader")

    # --- 删除面：400（缺 confirm）/ 404 / 500（清除失败，fail-closed）/ 200 ---
    import doc2md.storage.repo as repo
    hit("DELETE", "/docs/<doc_id>", f"/docs/{doc_b}", role="admin")
    hit("DELETE", "/docs/<doc_id>", "/docs/does-not-exist?confirm=true", role="admin")
    real_purge_files = repo.purge_files
    monkeypatch.setattr(repo, "purge_files", lambda *_a, **_k: ([], ["fake-fail"]))
    r = hit("DELETE", "/docs/<doc_id>", f"/docs/{doc_b}?confirm=true", role="admin")
    assert r.status_code == 500, r.get_json()          # 库行保留，可重试
    monkeypatch.setattr(repo, "purge_files", real_purge_files)
    r = hit("DELETE", "/docs/<doc_id>", f"/docs/{doc_b}?confirm=true", role="admin")
    assert r.status_code == 200, r.get_json()

    src = hit("GET", "/docs/<doc_id>", f"/docs/{doc_a}", role="reader"
              ).get_json()["source_key"]
    hit("DELETE", "/sources/<source_key>", f"/sources/{src}", role="admin")
    hit("DELETE", "/sources/<source_key>", "/sources/does-not-exist?confirm=true",
        role="admin")
    real_purge_many = repo.purge_many
    monkeypatch.setattr(repo, "purge_many", lambda *_a, **_k: ([], ["fake-fail"]))
    r = hit("DELETE", "/sources/<source_key>", f"/sources/{src}?confirm=true",
            role="admin")
    assert r.status_code == 500, r.get_json()
    monkeypatch.setattr(repo, "purge_many", real_purge_many)
    r = hit("DELETE", "/sources/<source_key>", f"/sources/{src}?confirm=true",
            role="admin")
    assert r.status_code == 200, r.get_json()

    # --- 探针覆盖与双向差 ---
    assert routes == specific, (
        "以下端点在探针矩阵里没有任何具体形态（矩阵缩小即视为红线失效）："
        f"{sorted(routes - specific)}")
    missing: dict[tuple[str, str], list[str]] = {}
    extra: dict[tuple[str, str], list[str]] = {}
    for key in sorted(routes):
        declared = (rows[key]["success"] | rows[key]["errors"]
                    | _auth_codes(rows[key]["role"]))
        got = observed[key]
        assert got, f"{key} 一个响应都没采到（探针未执行）"
        if declared - got:
            missing[key] = sorted(declared - got)
        if got - declared:
            extra[key] = sorted(got - declared)
    problems: list[str] = []
    if missing:
        problems.append("仅在文档（承诺了探针打不出的形态）：" + "；".join(
            f"{m} {p} → {c}" for (m, p), c in missing.items()))
    if extra:
        problems.append("仅在实测（可达但契约未登记）：" + "；".join(
            f"{m} {p} → {c}" for (m, p), c in extra.items()))
    assert not problems, "\n".join(problems)
