"""M0 / M2 单测：GGUF tokenizer 口径唯一（CH-402）+ S1.5 采样门禁（CH-415 / CH-424）。"""
from __future__ import annotations

import json

import pytest
from gguf_fixture import build_gguf

from doc2md.errors import E4221_GateReject
from doc2md.ops import gguf
from doc2md.pipeline import s1_5_gate
from doc2md.pipeline.s1_parse import Line, RawPage

pytestmark = pytest.mark.milestone("M0+M2")

CJK = "本条款涉及例外情况，以下情形适用于特定场景，上述内容包括标准流程。"


def pages_of(densities: list[int]) -> list[RawPage]:
    """按每页字符数造页：density=0 → 空页；否则整句重复到目标字数。"""
    out = []
    for i, d in enumerate(densities):
        if d <= 0:
            out.append(RawPage(index=i, lines=[]))
            continue
        rep = max(1, d // len(CJK) + 1)
        text = (CJK * rep)[:d]
        per = 40
        lines = [Line(text=text[j:j + per], size=10.5) for j in range(0, len(text), per)]
        out.append(RawPage(index=i, lines=lines))
    return out


# --- M0：GGUF 读取 / 导出 / manifest / 计数口径 -------------------------------
def test_gguf_read_metadata(tmp_path):
    p = build_gguf(tmp_path / "m.gguf")
    g = gguf.read_metadata(p)
    assert g.version == 3 and g.tokens == ["的", "是", "一个人", "test", "，", "。"]
    assert g.kv["tokenizer.ggml.scores"] and g.kv["tokenizer.ggml.add_bos_token"] is True


def test_gguf_rejects_bad_magic_and_version(tmp_path):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"NOTGGUF" + b"\x00" * 64)
    with pytest.raises(gguf.GgufError):
        gguf.read_metadata(bad)
    with pytest.raises(gguf.GgufError):
        gguf.read_metadata(build_gguf(tmp_path / "v9.gguf", version=9))


def test_vocab_export_and_manifest_roundtrip(tmp_path):
    p = build_gguf(tmp_path / "m.gguf", tokens=["a", "bb", "ccc"])
    man = gguf.export_vocab(p, tmp_path / "vocab.jsonl")
    gguf.write_manifest(man, tmp_path / "manifest.json")
    res = gguf.verify_manifest(tmp_path / "manifest.json", p)
    assert all(res[k] for k in ("gguf_ok", "vocab_ok", "name_ok", "n_vocab_ok")), res
    lines = (tmp_path / "vocab.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["token"] for x in lines] == ["a", "bb", "ccc"]


def test_manifest_detects_gguf_drift(tmp_path):
    """M0 验收：口径可复现 —— 换模型（内容变）后 manifest 必须校验失败。"""
    p = build_gguf(tmp_path / "m.gguf", tokens=["a", "b"])
    man = gguf.export_vocab(p, tmp_path / "vocab.jsonl")
    mp = gguf.write_manifest(man, tmp_path / "manifest.json")
    p.write_bytes(p.read_bytes() + b"\x00")       # 追加一字节 → sha 前缀变
    res = gguf.verify_manifest(mp, p)
    assert res["gguf_ok"] is False


def test_tokenizer_deterministic_count(tmp_path):
    p = build_gguf(tmp_path / "m.gguf")
    man = gguf.export_vocab(p, tmp_path / "vocab.jsonl")
    tok = gguf.Tokenizer(man["vocab_file"])
    a = tok.count("的是一件重要的事情")
    assert a == tok.count("的是一件重要的事情")           # 确定性
    assert tok.count("") == 0
    assert tok.count("test test") == 3                  # 贪婪最长匹配 + 空格
    assert tok.count("一个人") == 1                      # 多字 token 命中


# --- M2：S1.5 采样口径（even5 / legacy3 / auto）------------------------------
def test_even5_and_legacy3_indices():
    assert s1_5_gate.even5_indices(1) == [0]
    assert s1_5_gate.even5_indices(3) == [0, 1, 2]
    # round(i*(n-1)/4)：0, 4.75, 9.5, 14.25, 19 →（银行家取整）0,5,10,14,19
    assert s1_5_gate.even5_indices(20) == [0, 5, 10, 14, 19]
    assert s1_5_gate.legacy3_indices(20) == [0, 10, 19]


def test_gate_even5_catches_badtail_scan():
    """P-63：后段坏页 —— 三点采样漏检，even5 拦下。"""
    dens = [900] * 14 + [0, 0, 0] + [900] * 3          # 15-17 页坏
    with pytest.raises(E4221_GateReject):
        s1_5_gate.check(pages_of(dens), mode="even5")
    r = s1_5_gate.check(pages_of(dens), mode="legacy3")   # 对照：旧口径放行
    assert r.passed is True


def test_gate_even5_misses_late_badtail_but_auto_catches():
    """CH-424：even5 仍是抽样，坏区在 51~53 页（60 页文档）时照样漏；auto 全量判定拦下。"""
    dens = [900] * 50 + [0, 0, 0] + [900] * 7
    r_even = s1_5_gate.check(pages_of(dens), mode="even5")
    assert r_even.passed is True, "even5 不应改变口径（作为对照基线）"
    with pytest.raises(E4221_GateReject) as ei:
        s1_5_gate.check(pages_of(dens), mode="auto")
    assert "consecutive_empty_pages" in json.dumps(ei.value.detail, ensure_ascii=False)


def test_gate_auto_does_not_reject_sparse_single_pages():
    """auto 护栏：偶发稀疏页（图表页/章末留白）不误杀。"""
    dens = [900, 60, 900, 900, 30, 900, 900, 10, 900, 900]   # 零散低密度、不连续
    r = s1_5_gate.check(pages_of(dens), mode="auto")
    assert r.passed is True
    sparse = [p for p in r.pages if not p.ok]
    assert sparse, "稀疏页仍应进页级明细（报告面）"


def test_gate_auto_rejects_when_all_empty():
    with pytest.raises(E4221_GateReject):
        s1_5_gate.check(pages_of([0] * 5), mode="auto")


def test_gate_page_detail_carries_reasons():
    """验收「报告附页级明细」：哪一页、哪个指标。"""
    dens = [800] * 10
    r = s1_5_gate.check(pages_of(dens), mode="auto")
    d = r.detail()
    assert {"mode", "sampled_pages", "page_detail", "stats", "passed"} <= set(d)
    one = d["page_detail"][0]
    assert {"page", "chars", "lines", "cjk_ratio", "noise_ratio", "ok", "reasons"} <= set(one)


def test_gate_min_chars_boundary():
    """两级口径：119 字属「稀疏非空」→ 进页级明细标 bad，但 auto 不因此拒收；
    只有「近乎空页」（< empty_chars=20 或无行）成段才触发 E4221。"""
    dens = [119, 119, 119] + [900] * 17
    r = s1_5_gate.check(pages_of(dens), mode="auto")
    assert r.passed is True
    sparse = [p for p in r.pages if p.reasons]
    assert sparse, "低于 min_chars_per_page 的页必须在明细里可见"
    assert all("chars=" in ",".join(p.reasons) for p in sparse)
    assert all(p.ok for p in sparse), "auto 下 sparse 只标记不判坏"

    with pytest.raises(E4221_GateReject):              # 近乎空页成段 → 拒收
        s1_5_gate.check(pages_of([5, 5, 5] + [900] * 17), mode="auto")

    with pytest.raises(E4221_GateReject):   # CH-415 原口径：采样页里有 1 页稀疏即拒
        s1_5_gate.check(pages_of(dens), mode="even5")


def test_gate_even5_rejects_any_bad_sampled_page():
    """even5 严格语义（对照基线）：采样页里有 1 页稀疏即拒。"""
    dens = [900, 900, 50, 900, 900]                    # 5 页 → 全部为采样页
    with pytest.raises(E4221_GateReject):
        s1_5_gate.check(pages_of(dens), mode="even5")
    r = s1_5_gate.check(pages_of(dens), mode="auto")    # auto：单页稀疏不杀
    assert r.passed is True
