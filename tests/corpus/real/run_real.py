#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_real.py —— 真实样本腿：用人工真值对账真实 Word / PDF 文档。

三条口径：

  1. **真值来自 `ground_truth.json`（人工标注）**，不是产物、不是 `expected/` 基线。
     第十七轮的 G9：真值若与被测物同源，结构损失是看不见的。
  2. **幂等率必须恰为 1.0**（`--repeats 2`，逐字节比对两次产物）。确定性转换的幂等
     不是「越高越好」，不等于 1 就是缺陷。
  3. **样本不在仓库里**（`work/real_samples/`，gitignored）。目录不存在时本腿记
     `not_run` + 原因并退 0——「没样本」不是「产品好」，但也算不上产品失败；
     报告首行必须把这件事说出来。

用法：
    .venv/bin/python tests/corpus/real/run_real.py --sample 5 \\
        --json work/qa/real.json --md work/qa/real_sampling.md
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SAMPLE_DIR = REPO / "work" / "real_samples"
GT = HERE / "ground_truth.json"
sys.path.insert(0, str(REPO))

from tests.qa import attribute, defect_scan, mdscore  # noqa: E402

SUFFIXES = (".pdf", ".docx", ".doc")


def discover(samples_dir: Path = SAMPLE_DIR) -> list[Path]:
    if not samples_dir.is_dir():
        return []
    return sorted(p for p in samples_dir.rglob("*") if p.suffix.lower() in SUFFIXES)


def pick(files: list[Path], n: int) -> list[Path]:
    """确定性抽样：排序后等距取 n 条。重跑取到同一批，结论才可累积。"""
    if n <= 0 or len(files) <= n:
        return list(files)
    stride = len(files) / n
    return [files[int(i * stride)] for i in range(n)]


def run_one(src: Path, *, out_dir: Path, profile: str = "batch",
            repeats: int = 2) -> dict:
    """单份真实样本：转换 → 与人工真值对账 → 缺陷扫描 → 幂等。"""
    rec: dict = {"source": src.name, "kind": src.suffix.lstrip(".").lower(),
                 "bytes": src.stat().st_size}
    gt = json.loads(GT.read_text(encoding="utf-8")) if GT.is_file() else {}
    mine = gt.get(src.name) or {}
    rec["ground_truth"] = "yes" if mine else "no"
    work = out_dir / src.stem
    work.mkdir(parents=True, exist_ok=True)
    sink: dict = {}
    att = attribute.attribute_one(src, out_dir=work, profile=profile,
                                  repeats=repeats, sink=sink)
    rec.update({k: att.get(k) for k in
                ("status", "error_code", "error", "total_gap", "tcolc")})
    rec["by_stage"] = att.get("by_stage", {})
    rec["identity_ok"] = (att.get("identity") or {}).get("ok")
    rec["deviations"] = [d for d in att.get("deviations", [])
                         if d.get("severity") in ("blocking", "major")]
    if att.get("status") != "ok":
        rec["idempotent"] = None
        rec["heading_recall"] = None
        rec["heading_want"] = []
        return rec
    rec["idempotent"] = (att.get("scores") or {}).get("idempotent")
    md = sink.get("md", "")
    # 人工真值只声明了标题**文本**（没有全量层级），所以只算召回、不算层级准确率——
    # 把没有真值的那一维也算进去，就是 G9 的另一种形态。
    want = mine.get("headings") or []
    got = [b.text for b in mdscore.parse_blocks(md) if b.kind == "heading"]
    rec["heading_want"] = want
    rec["heading_got"] = got[:20]
    rec["heading_recall"] = (round(sum(1 for h in want
                                       if any(h in g for g in got)) / len(want), 4)
                             if want else None)
    # 复用同一次转换的产物（sink），不二次转换：真实文档一篇可达数 MB / 数十页
    ctx = defect_scan.ScanCtx(ref_headings=([{"text": h} for h in want] or None),
                              warnings=(att.get("signals") or {}).get("warnings"))
    findings = defect_scan.scan(md, ctx=ctx)
    rec["findings"] = [f.to_dict() for f in findings]
    rec["by_rule"] = {n: sum(1 for f in findings if f.rule == n)
                      for n in defect_scan.RULES}
    return rec


def verdict(records: list[dict]) -> tuple[str, list[str]]:
    """(退出码结论, 原因清单)。幂等 ≠ 1 或 blocking 偏差 ⇒ fail。"""
    why: list[str] = []
    for r in records:
        if r.get("status") == "error":
            why.append(f"{r['source']}: 转换异常 {r.get('error')}")
            continue
        if r.get("idempotent") not in (None, 1.0):
            why.append(f"{r['source']}: 幂等率 {r['idempotent']} ≠ 1.0")
        if any(d.get("severity") == "blocking" for d in r.get("deviations", [])):
            kinds = sorted({d["kind"] for d in r["deviations"]
                            if d.get("severity") == "blocking"})
            why.append(f"{r['source']}: blocking 偏差 {kinds}")
        if r.get("identity_ok") is False:
            why.append(f"{r['source']}: 归因恒等式不成立")
    return ("fail" if why else "pass"), why


def worksheet(records: list[dict], *, fields: int = 3) -> str:
    """人工复核工作表。**人工两栏恒为空**——脚本不预填「通过」。"""
    L = ["# 真实样本人工复核工作表（每篇抽 %d 个字段）" % fields, "",
         "> 自动栏由 `run_real.py` 填；**人工栏必须由人填**，留空即「未复核」，",
         "> 不许由脚本预填、也不许把「自动通过」当成「人工通过」。", "",
         "| 样本 | 字段 | 期望（真值出处） | 自动实测 | 人工复核 | 结论 |",
         "|---|---|---|---|---|---|"]
    for r in records:
        rows = [("标题层级", ", ".join(r.get("heading_want") or []) or "（无人工真值）",
                 f"recall={r.get('heading_recall')}"),
                ("表格列一致率", "同表内列数恒定", f"tcolc={r.get('tcolc')}"),
                ("幂等（两次逐字节）", "1.0", f"{r.get('idempotent')}"),
                ("缺陷规则命中", "0 条 blocking",
                 json.dumps(r.get("by_rule", {}), ensure_ascii=False))]
        for name, want, got in rows[:fields]:
            L.append(f"| {r['source']} | {name} | {want} | {got} |  |  |")
    return "\n".join(L) + "\n"


def not_run_sheet(payload: dict) -> str:
    """无样本时的占位工作表。**只写「未执行 + 原因 + 怎么执行」**，不写任何结论。"""
    return "\n".join([
        "# 真实样本人工复核工作表（未执行）", "",
        f"- 状态：**{payload['status']}**（≠ 通过，也≠ 产品失败）",
        f"- 时间：{payload['generated_at']}",
        f"- 原因：{payload['reason']}",
        "- 怎么执行：把真实文档放进上面那个目录（**不要放进 `tests/`**，"
        "隐私边界红线会拦），然后重跑同一条命令，见 "
        "`tests/corpus/real/README.md`。",
        "- 人工复核栏：本表无行可填；有样本后由 `worksheet()` 生成，人工两栏恒为空。",
        "",
    ])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Doc2md 真实样本腿")
    ap.add_argument("--samples-dir", default=str(SAMPLE_DIR))
    ap.add_argument("--sample", type=int, default=0, help="抽样份数（0 = 全量）")
    ap.add_argument("--profile", default="batch", choices=sorted(mdscore.PROFILES))
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)

    sdir = Path(a.samples_dir)
    files = discover(sdir)
    if not files:
        payload = {"status": "not_run", "generated_at": _now(),
                   "samples_dir": str(sdir), "n": 0, "records": [],
                   "reason": f"样本目录不存在或为空：{sdir}（真实文档 gitignored，"
                             "需人工把样本放进去；见 tests/corpus/real/README.md）"}
        _write(payload, not_run_sheet(payload), a)
        print(f"REAL not_run n=0 reason=样本目录不存在（{sdir}）")
        return 0

    chosen = pick(files, a.sample)
    tmp = Path(tempfile.mkdtemp(prefix="d2m-real-"))
    try:
        records = [run_one(p, out_dir=tmp, profile=a.profile, repeats=a.repeats)
                   for p in chosen]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    status, why = verdict(records)
    idem = [r["idempotent"] for r in records if r.get("idempotent") is not None]
    rec_h = [r["heading_recall"] for r in records if r.get("heading_recall") is not None]
    payload = {
        "status": status, "generated_at": _now(), "samples_dir": str(sdir),
        "profile": a.profile, "repeats": a.repeats,
        "n_available": len(files), "n": len(records),
        "n_with_ground_truth": sum(1 for r in records if r["ground_truth"] == "yes"),
        "idempotent_min": min(idem) if idem else None,
        "heading_recall_mean": round(sum(rec_h) / len(rec_h), 4) if rec_h else None,
        "failures": why, "records": records,
    }
    _write(payload, worksheet(records), a)
    print(f"REAL {status} n={len(records)}/{len(files)} "
          f"gt={payload['n_with_ground_truth']} "
          f"idem_min={payload['idempotent_min']} "
          f"recall={payload['heading_recall_mean']}")
    for w in why:
        print(f"  FAIL {w}")
    return 0 if status == "pass" else 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(payload: dict, sheet: str, a: argparse.Namespace) -> None:
    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    if a.md and sheet:
        p = Path(a.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sheet, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
