#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""realtest.py —— 实机自动测试编排器：一条命令跑完六层，出一份可复核的证据。

层的编号沿用方案 §8.1（L1 红线 / L2 全量回归 / L3 资产自验 / L4 装机面），
另加两层：L0 静态面（ruff，秒级）与 L5 质量对账（信息层，evaluate/attribute/
defect_scan/trace/prioritize + 难度腿 + 真实样本腿）。

三条口径纪律，写死在代码里：

  1. **计数只从 `--junitxml` 读**（`junit_counts`），不认终端汇总行、不认上一轮的数字；
     XML 缺失返回 `None` 而不是 0——0 会被读成「全绿」，这正是 O-h 的第三种形态。
  2. **`not_run` 永不写成 `pass`**：跑不了的腿记 `not_run` + 原因，空层也是 `not_run`。
  3. **只有阻断层进退出码**：L5 是信息层，质量偏差不使编排器失败（它交给 Task 6 排序），
     但它的 `not_run` 一样打在报告首屏。

用法：
    .venv/bin/python -m pytest -m gate -q          # 只跑红线层
    .venv/bin/python tests/qa/realtest.py           # 六层全跑
    .venv/bin/python tests/qa/realtest.py --only L0,L1
    .venv/bin/python tests/qa/realtest.py --coverage
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from doc2md import PIPELINE_VERSION, auth, config  # noqa: E402
from doc2md.api import create_app  # noqa: E402
from tests.qa import mdscore  # noqa: E402

LAYERS = ("L0_static", "L1_gate", "L2_full", "L3_assets", "L4_install", "L5_quality")
BLOCKING = frozenset({"L0_static", "L1_gate", "L2_full", "L3_assets", "L4_install"})
PASS, FAIL, NOT_RUN = "pass", "fail", "not_run"
CORPUS = REPO / "tests" / "corpus"
DEFAULT_TIMEOUT = {"L0_static": 300, "L1_gate": 1200, "L2_full": 3600,
                   "L3_assets": 1200, "L4_install": 600, "L5_quality": 2400}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    evidence: str = ""
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Layer:
    name: str
    blocking: bool
    checks: list[Check] = field(default_factory=list)
    note: str = ""
    #: **本机恒不可重复**的项记在这里，而不是塞进 `checks`。
    #: 依据 `test_realtest_never_reports_pass_for_not_run` 钉死的语义，阻断层里只要
    #: 有一条 `not_run` 检查，该层与总判就永远是 `not_run`——「空 venv 安装」属发布前
    #: CI 动作（§8.6），本机每次跑都缺席，把它当检查项等于让整份报告永久变黄，
    #: 而永久黄和永久红一样没有信号。它必须是**显式登记的缺口**，首屏照样看得见。
    gaps: list[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        return layer_status(self.checks)

    def to_dict(self) -> dict:
        return {"name": self.name, "blocking": self.blocking, "status": self.status,
                "note": self.note, "checks": [c.to_dict() for c in self.checks],
                "gaps": [c.to_dict() for c in self.gaps],
                "elapsed_s": round(sum(c.elapsed_s for c in self.checks), 2)}


def layer_status(checks: list[Check]) -> str:
    st = [c.status for c in checks]
    if FAIL in st:
        return FAIL
    if NOT_RUN in st or not st:
        return NOT_RUN
    return PASS


def overall(layers: list[Layer]) -> str:
    st = [ly.status for ly in layers if ly.blocking]
    if FAIL in st:
        return FAIL
    if NOT_RUN in st or not st:
        return NOT_RUN
    return PASS


def junit_counts(path: Path) -> dict | None:
    """从 junitxml 读计数。**缺失/解析失败返回 None**，绝不返回全 0。

    输入是本机 pytest 自己写的 junitxml（可信来源），不接受外部 XML。
    `xfail` 单独数：pytest 把 `<skipped type="pytest.xfail">` 折进 `skipped` 属性
    （本机实测），四元组会把「已知缺口」和「真跳过」混成一个数——报告必须拆开。
    """
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    out = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in out:
            out[k] += int(s.get(k) or 0)
    out["xfail"] = sum(1 for sk in root.iter("skipped")
                       if sk.get("type") == "pytest.xfail")
    out["passed"] = out["tests"] - out["failures"] - out["errors"] - out["skipped"]
    return out


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str, float]:
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=timeout)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        # 超时 = 这条腿没跑到结论，但绝不当通过：记 rc=124 + TIMEOUT 原因，
        # 由所属层落成 fail（阻断层因此非零退出，报告首屏可见）。
        rc, out, err = 124, "", f"TIMEOUT after {timeout}s: {' '.join(cmd)}"
    return rc, out, err, round(time.monotonic() - t0, 2)


def _tail(text: str, n: int = 1200) -> str:
    return (text or "")[-n:]


def _last_line(text: str) -> str:
    return next((ln for ln in reversed(text.strip().splitlines()) if ln.strip()), "")


def _json_line(text: str) -> dict | None:
    """取输出里**最后一条**可解析的 JSON 行（CLI 单行机器可读出口的惯例）。"""
    for ln in reversed(text.strip().splitlines()):
        try:
            v = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        return v if isinstance(v, dict) else None
    return None


def _json_doc(text: str) -> dict | None:
    """整段 stdout 作为一个 JSON 文档解析（doctor 用 `indent=2` 多行美化输出）。

    逐行找 JSON 的那条路在这里必然失败：多行 JSON 的每一行都不是合法文档，
    于是返回 `None` → 空 dict → `pipeline` 键「不存在」→ 探针假红。
    """
    try:
        v = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def l0_static(timeout: int = DEFAULT_TIMEOUT["L0_static"]) -> Layer:
    rc, out, err, el = _run([sys.executable, "-m", "ruff", "check",
                             "doc2md", "tests", "config", "ops"], timeout)
    return Layer("L0_static", True, [Check(
        "ruff", PASS if rc == 0 else FAIL, f"rc={rc}", _tail(out + err), el)])


def _detail(c: dict | None) -> str:
    if c is None:
        return "junitxml 缺失/不可解析"
    return (f"tests={c['tests']} passed={c['passed']} failures={c['failures']} "
            f"errors={c['errors']} skipped={c['skipped']}(真skip="
            f"{c['skipped'] - c['xfail']} xfail={c['xfail']})")


def _pytest_layer(name: str, xml: Path, args: list[str],
                  timeout: int) -> tuple[Layer, dict | None]:
    rc, out, err, el = _run([sys.executable, "-m", "pytest", *args,
                             f"--junitxml={xml}"], timeout)
    c = junit_counts(xml)
    if c is None:
        return Layer(name, True, [Check("pytest", NOT_RUN,
                                       f"rc={rc}，junitxml 缺失/不可解析",
                                       _tail(out + err), el)]), None
    # L1 红线层额外要求 0 skipped：红线里出现 skip 就是「有腿没跑却被记成绿」。
    ok = (rc == 0 and c["failures"] == 0 and c["errors"] == 0 and c["skipped"] == 0) \
        if name == "L1_gate" else (rc == 0 and c["failures"] == 0 and c["errors"] == 0)
    return Layer(name, True, [Check("pytest", PASS if ok else FAIL, _detail(c),
                                    _tail(out + err), el)]), c


def l1_gate(out_dir: Path) -> tuple[Layer, dict | None]:
    return _pytest_layer("L1_gate", out_dir / "l1-gate.xml", ["-m", "gate", "-q"],
                         DEFAULT_TIMEOUT["L1_gate"])


def l2_full(out_dir: Path, *, coverage: bool = False) -> tuple[Layer, dict | None]:
    xml = out_dir / "l2-full.xml"
    if coverage:
        rc, out, err, el = _run([sys.executable, "-m", "coverage", "run", "-m",
                                 "pytest", "-q", f"--junitxml={xml}"],
                                DEFAULT_TIMEOUT["L2_full"])
        rc2, rep, _, el2 = _run([sys.executable, "-m", "coverage", "report",
                                 "--include=doc2md/*"], 300)
        c = junit_counts(xml)
        line = next((ln for ln in rep.splitlines() if ln.startswith("TOTAL")), "")
        chk = Check("pytest+coverage",
                    PASS if (rc == 0 and c and c["failures"] == 0
                             and c["errors"] == 0 and rc2 == 0) else FAIL,
                    f"{_detail(c)} ｜ COVERAGE {line.strip()}",
                    _tail(out + err) + "\n" + _tail(rep), el + el2)
        return Layer("L2_full", True, [chk]), c
    return _pytest_layer("L2_full", xml, ["-q"], DEFAULT_TIMEOUT["L2_full"])


def l3_assets() -> Layer:
    rc, out, err, el = _run([sys.executable, str(CORPUS / "run_corpus.py")],
                            DEFAULT_TIMEOUT["L3_assets"])
    line = _last_line(out)
    return Layer("L3_assets", True, [Check(
        "corpus_22_byte_identical", PASS if rc == 0 else FAIL,
        line or f"rc={rc}", _tail(out + err), el)])


def _api_probe(tmp: Path) -> tuple[str, str, bool]:
    """进程内起 app（不起端口）：/healthz 无需 token，/docs 需 admin token。"""
    try:
        cfg = config.load(None)
        app = create_app(cfg, tmp / "apiroot")
        st = app.config["doc2md"]["store"]
        tok = auth.issue(st, "admin", "realtest-l4")
        with app.test_client() as c:
            h = c.get("/healthz")
            hj = h.get_json() or {}
            d = c.get("/docs", headers={"X-Doc2md-Token": tok})
        ok = (h.status_code == 200 and hj.get("ok") is True
              and hj.get("pipeline") == PIPELINE_VERSION and d.status_code == 200)
        app.config["doc2md"]["writer"].close()
        return (f"healthz={h.status_code} pipeline一致={hj.get('pipeline') == PIPELINE_VERSION} "
                f"docs={d.status_code}", _tail(json.dumps(hj, ensure_ascii=False)), ok)
    except Exception as exc:                              # noqa: BLE001 — 探针用途
        return "探针异常", f"{type(exc).__name__}: {exc}", False


def l4_install(out_dir: Path) -> Layer:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="doc2md-l4-", dir=out_dir))
    man = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    src = CORPUS / "sources" / man[0]["source"]
    src2 = CORPUS / "sources" / man[1]["source"]
    checks: list[Check] = []

    t0 = time.monotonic()
    rc, out, err, _ = _run([sys.executable, "-m", "doc2md", "doctor",
                            "--root", str(tmp)], DEFAULT_TIMEOUT["L4_install"])
    dj = _json_doc(out) or {}
    ver_ok = dj.get("pipeline") == PIPELINE_VERSION
    checks.append(Check("doctor", PASS if rc == 0 and ver_ok else FAIL,
                        f"rc={rc} pipeline={PIPELINE_VERSION} 一致={ver_ok}",
                        _tail(out + err), round(time.monotonic() - t0, 2)))

    t0 = time.monotonic()
    rc, out, err, _ = _run([sys.executable, "-m", "doc2md", "convert", str(src),
                            "--out", str(tmp / "out"), "--json", "--force",
                            "--root", str(tmp)], DEFAULT_TIMEOUT["L4_install"])
    zips = list((tmp / "out").glob("*.zip")) if (tmp / "out").exists() else []
    checks.append(Check("cli_convert_zip", PASS if rc == 0 and zips else FAIL,
                        f"rc={rc} zip={len(zips)}", _tail(out + err),
                        round(time.monotonic() - t0, 2)))

    t0 = time.monotonic()
    api_detail, api_ev, api_ok = _api_probe(tmp)
    checks.append(Check("api_healthz_docs", PASS if api_ok else FAIL, api_detail,
                        api_ev, round(time.monotonic() - t0, 2)))

    t0 = time.monotonic()
    rc, out, err, _ = _run([sys.executable, "-m", "doc2md", "work", "--once",
                            "--enqueue", str(src2), "--root", str(tmp)],
                           DEFAULT_TIMEOUT["L4_install"])
    rc2, st, _, _ = _run([sys.executable, "-m", "doc2md", "work", "--status",
                          "--root", str(tmp)], 120)
    # `work --status` 走 `GROUP BY status`：队列空时**根本不出 queued 键**，
    # 排空后计数是 {"done": N}。所以判「排空」取 rep 的 done/failed + 队列里
    # queued/running 归零，而不是找字面量 "pending": 0（该键在本产品不存在）。
    rep = _json_line(out) or {}
    counts = _json_line(st) or {}
    left = int(counts.get("queued", 0)) + int(counts.get("running", 0))
    drained = (rc == 0 and rc2 == 0 and int(rep.get("done", 0)) >= 1
               and int(rep.get("failed", 0)) == 0 and left == 0)
    checks.append(Check("worker_once_drains", PASS if drained else FAIL,
                        f"rc={rc} status_rc={rc2} rep={rep} queue={counts}",
                        _tail(out + err + st), round(time.monotonic() - t0, 2)))

    gaps = [Check("clean_venv_install", NOT_RUN,
                  "需空 venv + `pip install -e .[dev]`，属发布前动作（§8.1 L4 全量口径）；"
                  "本编排器只覆盖本机可重复子集，不冒充整层",
                  "见 docs/dev-plan.md §8.1 / §8.6 ci-full-matrix.yml", 0.0)]
    return Layer("L4_install", True, checks, gaps=gaps)


def l5_quality(out_dir: Path, *, profile: str = "generic") -> Layer:
    steps = [
        ("evaluate", [sys.executable, str(HERE / "evaluate.py"), "--profile", profile,
                      "--json", str(out_dir / "eval.json"),
                      "--md", str(out_dir / "eval.md")]),
        ("attribute", [sys.executable, str(HERE / "attribute.py"), "--profile", profile,
                       "--json", str(out_dir / "attribution.json"),
                       "--md", str(out_dir / "attribution.md")]),
        ("defect_scan", [sys.executable, str(HERE / "defect_scan.py"),
                         "--json", str(out_dir / "defects.json"),
                         "--md", str(out_dir / "defects.md")]),
        ("trace", [sys.executable, str(HERE / "trace.py"),
                   "--json", str(out_dir / "trace.json"),
                   "--md", str(out_dir / "trace.md")]),
        ("prioritize", [sys.executable, str(HERE / "prioritize.py"),
                        "--attribution", str(out_dir / "attribution.json"),
                        "--defects", str(out_dir / "defects.json"),
                        "--trace", str(out_dir / "trace.json"),
                        "--eval", str(out_dir / "eval.json"),
                        "--json", str(out_dir / "priorities.json"),
                        "--md", str(out_dir / "priorities.md")]),
        ("hard_leg", [sys.executable, str(CORPUS / "run_hard.py"),
                      "--json", str(out_dir / "hard.json")]),
        ("real_leg", [sys.executable, str(CORPUS / "real" / "run_real.py"),
                      "--json", str(out_dir / "real.json"),
                      "--md", str(out_dir / "real_sampling.md")]),
    ]
    checks = []
    for name, cmd in steps:
        rc, out, err, el = _run(cmd, DEFAULT_TIMEOUT["L5_quality"])
        last = _last_line(out)
        # 腿自己宣告 `not_run`（如真实样本腿无样本）就记 not_run：把「没跑」写成
        # 「通过」正是本编排器要消灭的那件事。L5 是非阻断层，这条 not_run 不污染总判，
        # 但会原样进首屏清单。
        if "not_run" in last:
            status = NOT_RUN
        # 难度腿与真实样本腿退出码非 0 是**结论**（有缺口），不是编排故障：
        # 记 pass 但把退出码与末行原样打进 detail，由报告读者判断。
        elif rc in (0, 1):
            status = PASS
        else:
            status = FAIL
        checks.append(Check(name, status, f"rc={rc}｜{last[:200]}", _tail(out + err), el))
    return Layer("L5_quality", False, checks,
                 note="信息层：不进出码；偏差交 prioritize 排序")


def expand_only(raw: str) -> tuple[str, ...]:
    """`"L0,L1"` → `("L0_static", "L1_gate")`；空串 = 全跑。

    认不出的 token 直接退出：静默选空会让六层全 `skipped`、整体 `not_run`，
    报告形态与「忘了跑」一模一样。
    """
    if not raw.strip():
        return ()
    out: list[str] = []
    for tok in (t.strip() for t in raw.split(",")):
        if not tok:
            continue
        # 大小写不敏感：`l5`、`L5`、`L5_quality`、`l5_quality` 都认（层全名是蛇形，
        # 直接 upper() 会把它和 LAYERS 的字面量比出大小写差异）。
        key = tok.upper()
        hits = [n for n in LAYERS if n.upper() == key or n.split("_")[0].upper() == key]
        if not hits:
            raise SystemExit(
                f"--only 的 '{tok}' 不匹配任何层（可选前缀 L0…L5 或层全名："
                f"{', '.join(LAYERS)}）")
        out += hits
    return tuple(dict.fromkeys(out))


def run(*, only: tuple[str, ...] = (), out_dir: Path, coverage: bool = False,
        profile: str = "generic") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    want = set(only) if only else set(LAYERS)
    layers: list[Layer] = []
    counts: dict[str, dict | None] = {}
    if "L0_static" in want:
        layers.append(l0_static())
    if "L1_gate" in want:
        ly, c = l1_gate(out_dir)
        layers.append(ly)
        counts["L1_gate"] = c
    if "L2_full" in want:
        ly, c = l2_full(out_dir, coverage=coverage)
        layers.append(ly)
        counts["L2_full"] = c
    if "L3_assets" in want:
        layers.append(l3_assets())
    if "L4_install" in want:
        layers.append(l4_install(out_dir))
    if "L5_quality" in want:
        layers.append(l5_quality(out_dir, profile=profile))
    skipped = [n for n in LAYERS if n not in want]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {"platform": sys.platform, "python": sys.version.split()[0],
                 "executable": sys.executable, "pipeline": PIPELINE_VERSION},
        "selected": sorted(want), "skipped": skipped,
        "overall": overall(layers),
        "layers": [ly.to_dict() for ly in layers],
        "counts": counts,
        "not_run": [{"layer": ly.name, "check": c.name, "detail": c.detail}
                    for ly in layers for c in ly.checks if c.status == NOT_RUN],
        "gaps": [{"layer": ly.name, "check": c.name, "detail": c.detail}
                 for ly in layers for c in ly.gaps if c.status == NOT_RUN],
    }


def report_md(payload: dict) -> str:
    h = payload["host"]
    L = ["# Doc2md 实机自动测试编排报告（自动生成）", "",
         f"- 总判：**{payload['overall']}**（只看阻断层 L0–L4；L5 是信息层）",
         f"- 宿主：platform=`{h['platform']}` python=`{h['python']}` "
         f"pipeline=`{h['pipeline']}`",
         f"- 解释器：`{h['executable']}`",
         f"- 生成时间：{payload['generated_at']}",
         f"- 选中层：{', '.join(payload['selected']) or '（无）'}",
         f"- 未选层：{', '.join(payload['skipped']) or '无'}", "",
         "## not_run 清单（没跑的腿——**不等于通过**）", ""]
    nrs = payload["not_run"]
    if nrs:
        L += ["| 层 | 检查 | 原因 |", "|---|---|---|"]
        L += [f"| {n['layer']} | {n['check']} | {n['detail']} |" for n in nrs]
    else:
        L.append("无（本轮选中的每一条腿都实际执行过）。")
    gaps = payload.get("gaps", [])
    L += ["", "## 本机不覆盖（显式登记的缺口，**同样不等于通过**）", ""]
    if gaps:
        L += ["| 层 | 项 | 原因与去处 |", "|---|---|---|"]
        L += [f"| {g['layer']} | {g['check']} | {g['detail']} |" for g in gaps]
    else:
        L.append("无。")
    L += ["", "## 逐层", ""]
    for ly in payload["layers"]:
        L += [f"### {ly['name']} — {ly['status']}"
              f"（{'阻断' if ly['blocking'] else '信息'}层，"
              f"{ly['elapsed_s']}s）", ""]
        if ly.get("note"):
            L.append(f"> {ly['note']}")
            L.append("")
        L += ["| 检查 | 状态 | 详情 | 秒 |", "|---|---|---|---|"]
        for c in ly["checks"]:
            L.append(f"| {c['name']} | {c['status']} | {c['detail']} | {c['elapsed_s']} |")
        L.append("")
    for name in ("L1_gate", "L2_full"):
        c = payload["counts"].get(name)
        if c:
            L.append(f"- {name} junit 四元组：tests={c['tests']} passed={c['passed']} "
                     f"failures={c['failures']} errors={c['errors']} "
                     f"skipped={c['skipped']}（真 skip={c['skipped'] - c['xfail']} "
                     f"xfail={c['xfail']}）")
    L.append("")
    L.append("计数只从 `--junitxml` 读取；终端汇总行与上一轮数字一律不认。")
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Doc2md 实机自动测试编排器（L0–L5）")
    ap.add_argument("--only", default="",
                    help="只跑这些层（逗号分隔；前缀 L0…L5 或层全名；空 = 全跑）")
    ap.add_argument("--coverage", action="store_true",
                    help="L2 改用 coverage run + coverage report --include=doc2md/*")
    ap.add_argument("--profile", default="generic", choices=sorted(mdscore.PROFILES))
    ap.add_argument("--json", default=str(REPO / "work" / "qa" / "realtest.json"))
    ap.add_argument("--md", default=str(REPO / "work" / "qa" / "realtest.md"))
    a = ap.parse_args(argv)

    payload = run(only=expand_only(a.only), out_dir=Path(a.json).parent,
                  coverage=a.coverage, profile=a.profile)
    for path, text in ((a.json, json.dumps(payload, ensure_ascii=False, indent=2)),
                       (a.md, report_md(payload))):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def quad(name: str) -> str:
        d = payload["counts"].get(name)
        if d is None:
            return "—"
        return f"{d['tests']}/{d['failures']}/{d['errors']}/{d['skipped']}"

    print(f"REALTEST overall={payload['overall']} L1={quad('L1_gate')} "
          f"L2={quad('L2_full')} NOTRUN={len(payload['not_run']) + len(payload['gaps'])}")
    for nr in payload["not_run"]:
        print(f"  NOT_RUN {nr['layer']}/{nr['check']}: {nr['detail']}")
    for g in payload["gaps"]:
        print(f"  GAP {g['layer']}/{g['check']}: {g['detail']}")
    return 1 if payload["overall"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
