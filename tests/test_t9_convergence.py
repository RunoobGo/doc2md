"""T9 收敛轮单测（CH-456）：声明性配置键落地 + 预留码/死代码清理的回归防线。

覆盖：
- `run.profile` 三档覆盖与「显式键赢过 profile」；
- `run.offline_assert` 运行期守护（NFR-1）与 convert 入口施加；
- `s1_5.reject_on_any_bad_page` / `s1_5.page_detail_in_report` 的 convert 接线；
- W1044 分片说明真实写入；
- `ops.work_dir_mode` 可配；
- `api.token_roles` 限制签发；
- 冻结键 `security.sanitize_fail_action` 配错即报；
- 已删死代码（E5051/W1999/Scheduler.next_queued/active_for、auth.require/
  timing_safe_equal/RevocationCache、db._Tx.lastrowid、metrics.line_similarity/
  diff_lines）不得回潮。
"""
from __future__ import annotations

import copy
import importlib
import socket

import pytest

from doc2md import auth, config
from doc2md.errors import WARN_SHARD_NOT_MERGED, E4221_GateReject
from doc2md.pipeline.convert import convert
from doc2md.security.netguard import OfflineViolation, offline_guard
from doc2md.storage.db import Store

pytestmark = pytest.mark.milestone("M0+M2")

# doc2md.pipeline 门面把 convert 函数再导出，`from doc2md.pipeline import convert`
# 拿到的是函数而非模块——monkeypatch 必须经 importlib 取真模块。
convert_mod = importlib.import_module("doc2md.pipeline.convert")


# --- run.profile 三档 ---------------------------------------------------------
def _write_cfg(tmp_path, text: str):
    p = tmp_path / "doc2md.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_profile_lite_overrides_defaults(tmp_path):
    p = _write_cfg(tmp_path, '[run]\nprofile = "lite"\n')
    cfg = config.load(p)
    assert cfg["s1"]["chunk_pages"] == 16
    assert cfg["perf"]["workers"] == 1
    assert cfg["perf"]["queue_async"] is True


def test_profile_strict_overrides_defaults(tmp_path):
    p = _write_cfg(tmp_path, '[run]\nprofile = "strict"\n')
    cfg = config.load(p)
    assert cfg["perf"]["queue_async"] is True
    assert cfg["perf"]["min_free_mb"] == 2048


def test_profile_explicit_key_wins_over_profile(tmp_path):
    p = _write_cfg(tmp_path, '[run]\nprofile = "lite"\n\n[perf]\nworkers = 3\n')
    cfg = config.load(p)
    assert cfg["perf"]["workers"] == 3          # 显式配置赢过 profile
    assert cfg["s1"]["chunk_pages"] == 16       # 未显式设置的键仍按 profile


def test_profile_unknown_value_rejected(tmp_path):
    p = _write_cfg(tmp_path, '[run]\nprofile = "turbo"\n')
    with pytest.raises(ValueError, match="unknown run.profile"):
        config.load(p)


# --- run.offline_assert 守护 ---------------------------------------------------
def test_offline_guard_blocks_non_loopback():
    with pytest.raises(OfflineViolation):
        with offline_guard(True):
            socket.socket().connect(("93.184.216.34", 80))


def test_offline_guard_allows_loopback():
    # 回环地址不被拦截（用已监听端口验证放行；连接被拒只可能是 OS 层，不是守护）
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        with offline_guard(True):
            c = socket.socket()
            c.connect(srv.getsockname())
            c.close()
    finally:
        srv.close()


def test_offline_guard_disabled_is_passthrough():
    original = socket.socket.connect
    with offline_guard(False):
        assert socket.socket.connect is original


def test_offline_guard_restores_original_on_exit():
    original = socket.socket.connect
    with offline_guard(True):
        assert socket.socket.connect is not original
    assert socket.socket.connect is original


def test_convert_applies_offline_guard_per_config(tmp_path, monkeypatch, small_pdf, tmp_cfg):
    seen = []
    real = convert_mod.offline_guard

    class _Recording:
        def __init__(self, cm, enabled):
            self.cm, self.enabled = cm, enabled

        def __enter__(self):
            seen.append(self.enabled)
            return self.cm.__enter__()

        def __exit__(self, *exc):
            return self.cm.__exit__(*exc)

    monkeypatch.setattr(convert_mod, "offline_guard",
                        lambda enabled=True: _Recording(real(enabled), enabled))
    convert(small_pdf, cfg=tmp_cfg)
    assert seen == [True]

    seen.clear()
    c = copy.deepcopy(tmp_cfg)
    c["run"]["offline_assert"] = False
    convert(small_pdf, cfg=c)
    assert seen == [False]


# --- s1_5 两个键的 convert 接线 -------------------------------------------------
def test_reject_on_any_bad_page_false_tolerates_sampled_bad_page(tmp_path, golden_dir, tmp_cfg):
    # badtail_scan：20 页、坏区 15–17 页；even5 采样 [0,5,10,15,19] 命中 index 15
    sample = golden_dir / "badtail_scan.pdf"
    if not sample.is_file():
        pytest.skip("golden sample missing: badtail_scan.pdf")
    c = copy.deepcopy(tmp_cfg)
    c["s1_5"]["sample_pages"] = "even5"
    c["s1_5"]["reject_on_any_bad_page"] = False
    r = convert(sample, cfg=c)                  # 关：单个采样坏页被容忍
    assert r.doc_id
    c["s1_5"]["reject_on_any_bad_page"] = True
    with pytest.raises(E4221_GateReject):       # 开：同一采样面即拒收
        convert(sample, cfg=c)


def test_page_detail_in_report_toggles_gate_section(tmp_path, small_pdf, tmp_cfg):
    out = tmp_path / "out"
    r = convert(small_pdf, cfg=tmp_cfg, out_dir=out)
    import zipfile
    with zipfile.ZipFile(r.zip_path) as z:
        with_section = z.read(f"{r.doc_id}/report.html").decode("utf-8")
    assert "S1.5 gate" in with_section

    c = copy.deepcopy(tmp_cfg)
    c["s1_5"]["page_detail_in_report"] = False
    r2 = convert(small_pdf, cfg=c, out_dir=out)
    with zipfile.ZipFile(r2.zip_path) as z:
        without_section = z.read(f"{r2.doc_id}/report.html").decode("utf-8")
    assert "S1.5 gate" not in without_section


# --- W1044 分片说明 -------------------------------------------------------------
def test_w1044_written_when_sharded(tmp_path, small_pdf, tmp_cfg):
    c = copy.deepcopy(tmp_cfg)
    c["s2"]["mode"] = "two_phase"
    c["s2"]["max_pages_per_shard"] = 2
    c["s2"]["shard_overlap_pages"] = 0
    r = convert(small_pdf, cfg=c)
    assert r.ir.sharded is True
    assert WARN_SHARD_NOT_MERGED in r.meta["warnings"]


def test_w1044_absent_when_single(tmp_path, small_pdf, tmp_cfg):
    c = copy.deepcopy(tmp_cfg)
    c["s2"]["mode"] = "single"
    r = convert(small_pdf, cfg=c)
    assert r.ir.sharded is False
    assert WARN_SHARD_NOT_MERGED not in r.meta["warnings"]


# --- ops.work_dir_mode ----------------------------------------------------------
def test_work_dir_mode_configurable(tmp_path):
    from doc2md.ops.backup import BackupPlan
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups", external_dir=None,
                      work_root=tmp_path / "work", work_dir_mode="750")
    import os
    plan.snapshot(tmp_path / "state.sqlite")
    assert (tmp_path / "work").stat().st_mode & 0o777 == 0o750
    assert os.stat(tmp_path / "work/reports/backups").st_mode & 0o777 == 0o750


def test_work_dir_mode_default_is_700(tmp_path):
    from doc2md.ops.backup import BackupPlan
    plan = BackupPlan(local_dir=tmp_path / "work/reports/backups", external_dir=None,
                      work_root=tmp_path / "work")
    plan.snapshot(tmp_path / "state.sqlite")
    assert (tmp_path / "work").stat().st_mode & 0o777 == 0o700


# --- api.token_roles ------------------------------------------------------------
@pytest.mark.milestone("M9a")
def test_token_roles_restricts_issue(store: Store):
    with pytest.raises(ValueError, match="bad role"):
        auth.issue(store, "admin", "x", allowed=("reader",))
    tok = auth.issue(store, "reader", "ok", allowed=("reader",))
    assert tok.startswith("d2m_")


@pytest.mark.milestone("M9a")
def test_issue_default_allows_all_roles(store: Store):
    for role in auth.ROLES:
        assert auth.issue(store, role, "d").startswith("d2m_")


# --- 冻结键 ----------------------------------------------------------------------
def test_frozen_key_rejects_unknown_value():
    cfg = config.load()
    cfg["security"]["sanitize_fail_action"] = "sanitize"
    with pytest.raises(ValueError, match="sanitize_fail_action"):
        config.validate(cfg)


def test_frozen_key_accepts_default():
    config.validate(config.load())             # 默认 reject 不得误报


def test_frozen_key_enforced_at_load(tmp_path):
    p = _write_cfg(tmp_path, '[security]\nsanitize_fail_action = "rename"\n')
    with pytest.raises(ValueError, match="sanitize_fail_action"):
        config.load(p)


# --- 死代码不得回潮 ----------------------------------------------------------------
def test_removed_dead_code_stays_removed():
    import doc2md.errors as errors_mod
    from doc2md import scheduler
    from doc2md.pipeline import metrics
    from doc2md.storage.db import _Tx
    assert not hasattr(scheduler.Scheduler, "next_queued")
    assert not hasattr(scheduler.Scheduler, "active_for")
    assert not hasattr(auth, "require")
    assert not hasattr(auth, "timing_safe_equal")
    assert not hasattr(auth, "RevocationCache")
    assert not hasattr(metrics, "line_similarity")
    assert not hasattr(metrics, "diff_lines")
    assert "E5051" not in errors_mod.CODE_INDEX
    assert not hasattr(errors_mod, "WARN_UNSAFE_FALLBACK")
    assert not hasattr(_Tx, "lastrowid")
