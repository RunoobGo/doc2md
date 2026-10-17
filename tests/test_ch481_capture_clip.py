"""CH-481 —— 任务计划输出捕获纳入 NFR-19 预算（就地截断保尾）。

12.7 的「常驻 serve 的 launchd stderr 捕获无界」待裁定项在此落地为方案①，但**不新增
日报步骤名**：`log_rotation` 一步本来就承担「日志空间治理」，捕获通道只是第二条通道，
扩 payload 即可，附录 E 的步骤序列契约不动。

红线钉的是三条容易做错的纪律：
1. **只能就地截断**。捕获文件的句柄握在 launchd 手里（O_APPEND），改名式轮转会让我方
   新建的文件永远空着、增长只是换地方——所以断言 inode 不变且**不产生 `.1` 兄弟文件**。
2. **cron 不得成为别人文件的写入者**：只碰 `launchd-*.{out,err}`，jsonl 三份各归其主。
3. **截断失败不翻转 `rep.ok`**（后果是继续占盘，不是数据丢失），但必须在日报里可指认。

变异实测见仓库台账（dev-plan 附录 F 第十二层）：把 `_clip_to_tail` 换成「改名 + 重开」
或让 `capture_budget(50, 1)` 返回 tail == cap，本文件必须转红。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from doc2md.ops.logging import bound_capture_files, capture_budget


def _fill(path: Path, nbytes: int) -> bytes:
    body = "".join(f"line-{i:06d} {'x' * 40}\n" for i in range(nbytes // 56 + 1))
    raw = body.encode("utf-8")
    path.write_bytes(raw)
    return raw


# --- G1：就地截断——inode 不变、无兄弟文件、尾部整行可读 -----------------------


@pytest.mark.gate
def test_clip_is_in_place_not_rename(tmp_path):
    p = tmp_path / "launchd-serve.err"
    raw = _fill(p, 200_000)
    before_inode = p.stat().st_ino
    cap, tail = capture_budget(0.1, 5)          # 合法下限档：cap≈100KB、tail≈20KB

    ev = bound_capture_files(tmp_path, cap, tail)

    assert [e["name"] for e in ev] == ["launchd-serve.err"], ev
    assert p.stat().st_ino == before_inode, "换 inode == 走了改名式轮转，launchd 的旧句柄会被孤儿化"
    assert sorted(q.name for q in tmp_path.iterdir()) == ["launchd-serve.err"], \
        "捕获通道不得产生 .1 之类的兄弟文件（无属主的空轮出件）"
    assert p.stat().st_size < cap, "仍在预算上界之上，界定没有生效"
    assert ev[0]["before"] == len(raw)
    assert ev[0]["after"] == p.stat().st_size
    assert ev[0]["dropped"] == ev[0]["before"] - ev[0]["after"]


@pytest.mark.gate
def test_clip_keeps_readable_tail_and_marker(tmp_path):
    p = tmp_path / "launchd-x.out"
    raw = _fill(p, 200_000)
    cap, tail = capture_budget(0.1, 5)
    bound_capture_files(tmp_path, cap, tail)

    after = p.read_bytes()
    assert after.startswith(b'{"code": "E_LOG_CLIP"'), "缺截断标记，读者无法区分「日志从头就在这」与「头部已丢弃」"
    assert json.loads(after.split(b"\n", 1)[0])["kept"] == tail
    assert after.endswith(raw[-500:]), "尾部（最新输出）必须逐字节保住——截断保的是尾不是头"
    _, _, kept = after.partition(b"\n")
    assert raw.endswith(kept) and 0 < len(kept) <= tail, "保尾段必须是原文件的整段后缀且不超过预算"
    first = kept.split(b"\n", 1)[0]
    assert (b"\n" + first + b"\n") in raw, "保尾首行是残半截：应从一个完整行起头"


def test_under_cap_untouched(tmp_path):
    p = tmp_path / "launchd-daily.err"
    p.write_text("few lines only\n")
    cap, tail = capture_budget(0.1, 5)
    assert bound_capture_files(tmp_path, cap, tail) == []
    assert p.read_text() == "few lines only\n"


# --- G2：预算推导必然收缩（keep=1 时若不取半则等于不做事） ---------------------


@pytest.mark.gate
def test_capture_budget_always_shrinks():
    for keep in (1, 2, 5, 1000):
        cap, tail = capture_budget(50, keep)
        assert cap == 50 * 1024 * 1024, (keep, cap)
        assert 0 < tail < cap, (keep, cap, tail)
    assert capture_budget(50, 1)[1] == capture_budget(50, 1)[0] // 2


# --- G5：只碰捕获档，不碰别人的 jsonl ------------------------------------------


@pytest.mark.gate
def test_clip_does_not_touch_owned_logs(tmp_path):
    cap, tail = capture_budget(0.1, 5)
    fat = _fill(tmp_path / "launchd-serve.err", cap + 10_000)
    # 非捕获档同样**越过上界**：glob 口径一旦放宽（`*.out` / `*.err`）就会动到它们，
    # 这条断言才挡得住——属主纪律要求 cron 永远不是别人文件的写入者。
    mine = {
        "app.jsonl": b'{"code":"E_A"}\n' * 20_000,
        "cron.jsonl": b'{"code":"E_B"}\n' * 20_000,
        "work.jsonl.1": b"legacy\n" * 20_000,
        "notes.out": b"unrelated\n" * 20_000,
        "worker-legacy.err": b"stray\n" * 20_000,
    }
    for name, data in mine.items():
        (tmp_path / name).write_bytes(data)
    before = {n: (p.read_bytes(), p.stat().st_ino) for n, p in
              [(n, tmp_path / n) for n in mine]}

    ev = bound_capture_files(tmp_path, cap, tail)

    assert [e["name"] for e in ev] == ["launchd-serve.err"], ev
    for name, (data, ino) in before.items():
        assert len(data) > cap, name          # 前提自检：它们确实越过了上界
        p = tmp_path / name
        assert (p.read_bytes(), p.stat().st_ino) == (data, ino), name
    assert (tmp_path / "launchd-serve.err").stat().st_size < cap
    assert (tmp_path / "launchd-serve.err").stat().st_size < len(fat)


@pytest.mark.gate
def test_clip_oserror_recorded_not_raised(tmp_path):
    """单档截断失败：记 `error` 字段、不抛出、不拖累同目录其他档。

    用「目录占住捕获档名」构造确定性失败——不依赖 chmod/uid，任何解释器与 CI 档位同结果。
    """
    (tmp_path / "launchd-serve.err").mkdir()
    good = _fill(tmp_path / "launchd-daily.err", 200_000)
    ev = {e["name"]: e for e in bound_capture_files(tmp_path, 1, 200)}

    assert "error" in ev["launchd-serve.err"], ev
    assert "before" not in ev["launchd-serve.err"]
    assert ev["launchd-daily.err"]["after"] < len(good)


def test_missing_logs_dir_is_empty_evidence(tmp_path):
    # Windows 侧任务计划不捕获 stdout，日志目录里没有捕获档 —— 空证据不是异常
    assert bound_capture_files(tmp_path / "nope", 10, 5) == []


# --- G3/G4：cron 接线——步骤名不变、证据入 payload、失败不翻 ok ----------------


def _rot(tmp_path, cfg, *, rotate_mb=0.1):
    import copy

    from doc2md import cron
    c = copy.deepcopy(cfg)
    c.setdefault("logs", {})["rotate_mb"] = rotate_mb
    logs = Path(c["paths"]["logs"])
    logs.mkdir(parents=True, exist_ok=True)
    _fill(logs / "launchd-serve.err", 300_000)
    (logs / "launchd-work.out").write_bytes(b"startup banner\n")   # 未上界，原样留着
    return cron.run(c, root=tmp_path / "work", mode="daily", with_backup=False)


@pytest.mark.gate
def test_cron_log_rotation_bounds_capture_without_new_step(tmp_cfg, tmp_path):
    from doc2md import config
    rep = _rot(tmp_path, tmp_cfg)
    assert rep.ok is True, rep.to_json()
    steps = [s["step"] for s in rep.steps]
    # 附录 E 契约：本步只扩 payload、不新增步骤名——出现表外的名字即裁定走偏
    known = {"daily_report", "local_snapshot", "work_dir_mode", "rotation", "tmp_clean",
             "log_rotation", "events_purge", "tasks_purge", "search_reconcile",
             "external_backup"}
    assert set(steps) <= known, f"附录 E 之外的新步骤名：{set(steps) - known}"
    assert steps.count("log_rotation") == 1, steps
    step = next(s for s in rep.steps if s["step"] == "log_rotation")
    cap, tail = capture_budget(0.1, int(config.get(tmp_cfg, "logs.rotate_keep", 5)))
    assert step["capture_cap_bytes"] == cap and step["capture_tail_bytes"] == tail
    assert [e["name"] for e in step["captures"]] == ["launchd-serve.err"], step
    assert step["capture_errors"] == 0
    err = tmp_path / "work" / "logs" / "launchd-serve.err"
    assert err.stat().st_size < cap, "日报说截了但盘上没截"
    assert (tmp_path / "work" / "logs" / "launchd-work.out").read_bytes() == b"startup banner\n"


@pytest.mark.gate
def test_cron_clip_failure_is_visible_not_fatal(tmp_cfg, tmp_path, monkeypatch):
    """单档截断失败 → `capture_errors` 计数 + 步骤仍在，`rep.ok` 不翻转。

    打桩给出失败证据：被试对象是 cron 的失败口径（不是截断本身，那由
    `test_clip_oserror_recorded_not_raised` 直接覆盖），打桩换来的是与 uid /
    文件系统权限无关的确定性。
    """
    from doc2md import cron
    monkeypatch.setattr(cron, "bound_capture_files", lambda *a, **k: [
        {"name": "launchd-serve.err", "error": "[Errno 1] Operation not permitted"}])
    rep = _rot(tmp_path, tmp_cfg)
    assert rep.ok is True, "截断失败只意味着继续占盘，不是数据丢失——不得拖垮日流程"
    step = next(s for s in rep.steps if s["step"] == "log_rotation")
    assert step["capture_errors"] == 1, step
    assert step["captures"][0]["name"] == "launchd-serve.err"
    assert step["captures"][0]["error"].strip()


def test_cron_report_json_serialisable_with_capture_evidence(tmp_cfg, tmp_path):
    out = json.loads(_rot(tmp_path, tmp_cfg).to_json())
    step = next(s for s in out["steps"] if s["step"] == "log_rotation")
    assert step["captures"][0]["dropped"] > 0
    # NFR-20：证据只有档名与字节数，不含捕获内容
    assert set(step["captures"][0]) == {"name", "before", "after", "dropped"}


# --- G6：文档与资产口径同步（消掉「待裁定 / 手工截断」的旧说法） ----------------


@pytest.mark.gate
def test_docs_and_asset_comments_reflect_bounded_captures():
    repo = Path(__file__).resolve().parents[1]
    texts = {
        "README.md": (repo / "README.md").read_text(encoding="utf-8"),
        "serve plist 注释": (repo / "ops/launchd/md.doc2md.serve.plist").read_text(encoding="utf-8"),
        "work plist 注释": (repo / "ops/launchd/md.doc2md.work.plist").read_text(encoding="utf-8"),
    }
    for where, text in texts.items():
        assert "手工截断" not in text and "待裁定" not in text, f"{where} 仍写着已作废的裁定口径"
        assert "就地截断" in text, f"{where} 未写明捕获文件的界定口径"
    plan = (repo / "docs" / "dev-plan.md").read_text(encoding="utf-8")
    nfr19 = next(l for l in plan.splitlines() if l.startswith("| NFR-19 |"))
    assert "捕获" in nfr19, "NFR-19 规格行未纳入捕获通道口径"
