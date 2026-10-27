#!/usr/bin/env python3
"""语料指标汇总器（B-34 的「出口」那一半）——`tests/corpus/ideal/` 真值 ↔ 当前产物逐篇上报。

定位与纪律
----------
- 补的是**上报面**，不是判据面：`doc2md/qa_eval.py` 的三个指标此前只有两处消费者——
  `convert._metrics`（单篇，随报告走）与 `tests/test_qa_n03_ideal.py`（46 项断言，
  只钉「标注与源一致」「S3 不回退」）。没有任何地方把 24 份标注的指标**逐篇汇总**，
  于是「指标存在」与「指标看得见」之间断了链（CH-500 报告 AR-11 / B-34）。
- **双口径物理分列**（`qa_eval` 模块 docstring 的纪律，本脚本是唯一把它落到版式上的地方）：
  · `ideal` 列 = 对 `tests/corpus/ideal/*.ideal.json` 的标题 P/R 与表格列一致率；
  · `drift` 列 = 对 `tests/corpus/expected/*.md`（**管线自己重烤的基线**）的 `char_f1`。
  后者随基线重烤自啮合，**只用于回归漂移监控**，不得当质量真值、也不得进 `ideal` 的任何聚合。
- **真值 provenance 必须随行打印**：DOCX 侧标注来自 python-docx 直读 XML 样式（人工可复核），
  PDF 侧来自生成器字号约定（`make_ideal_pdf.py`）。两侧都不是「人工逐页标注」——
  所以 `f1=1.0` 在这里的含义是「与程序化真值一致」，不是「人看过了」。野外真值属 B-35。
- **无真值 ≠ 满分**：`heading_pr` 在 `ref==0` 时各项返回 `None`。本脚本把这类行标成
  `no_truth` 单独计数（`pdf_10_two_column_6p` 正是这种：双栏样本没有标题可标），
  聚合时排除，绝不按 1.0 计入——这是 OPT-2 反空跑纪律在度量面上的形态。
- 退出码：跑通即 0（信息层）。指标**是否回退**由 `tests/test_ch503_metrics_runner.py`
  按冻结基线判定，不靠人看输出。

用法
----
    .venv/bin/python tests/corpus/run_metrics.py                  # 全 24 份，打印对照表
    .venv/bin/python tests/corpus/run_metrics.py --only docx_     # 按文件名前缀过滤
    .venv/bin/python tests/corpus/run_metrics.py --json work/qa/metrics.json
    .venv/bin/python tests/corpus/run_metrics.py --write-baseline # 重烤冻结基线（人工审阅后提交）
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402
from doc2md.qa_eval import (  # noqa: E402
    char_f1,
    heading_pr,
    pred_headings,
    table_col_consistency,
)
from tests.qa.mdscore import strip_front_matter  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
IDEAL = CORPUS / "ideal"
SOURCES = CORPUS / "sources"
EXPECTED = CORPUS / "expected"
BASELINE = CORPUS / "metrics" / "ideal-baseline.json"


def annotations() -> list[dict]:
    """现存 ideal 标注清单，按文件名序——**分母口径从这里出发，不靠调用方数**。"""
    out = []
    for path in sorted(IDEAL.glob("*.ideal.json")):
        ann = json.loads(path.read_text(encoding="utf-8"))
        stem = path.name[: -len(".ideal.json")]
        out.append({"stem": stem, "annotation": ann, "kind": ann.get("kind", "")})
    return out


def measure(only: str | None = None) -> dict:
    """逐篇转换并计算指标；返回 {"rows": [...], "summary": {...}}。

    `only` 是文件名前缀过滤（调试用）；过滤会写进结果的 `filter`，
    冻结基线比对要求 `filter is None`——半集上的聚合数没有可比性。
    """
    rows: list[dict] = []
    for item in annotations():
        stem = item["stem"]
        if only and not stem.startswith(only):
            continue
        src_name = item["annotation"].get("source", "")
        src = SOURCES / src_name
        row = {
            "stem": stem,
            "batch": "docx" if stem.startswith("docx_") else "pdf",
            "kind": item["kind"],
            "provenance": "xml_style" if stem.startswith("docx_") else "font_size",
            "source": src_name,
        }
        if not src.is_file():
            row["skipped"] = "source_missing"
            rows.append(row)
            continue
        try:
            with tempfile.TemporaryDirectory() as td:
                res = convert(src, out_dir=Path(td))
        except Doc2mdError as exc:
            row["skipped"] = f"convert_error:{exc.code}"
            rows.append(row)
            continue
        ref = item["annotation"].get("headings", [])
        hp = heading_pr(pred_headings(res.ir.blocks), ref)
        tc = table_col_consistency(res.ir.blocks)
        row["headings"] = {
            "pred": hp["pred"], "ref": hp["ref"], "matched": hp["matched"],
            "p": hp["p"], "r": hp["r"], "f1": hp["f1"],
            # ref==0 → 无真值：单独成旗标，聚合与打印都不许当满分
            "no_truth": not ref,
        }
        row["tables"] = tc
        exp = EXPECTED / f"{stem}.md"
        if exp.is_file():
            got = strip_front_matter(res.markdown)[1].strip()
            want = strip_front_matter(exp.read_text(encoding="utf-8"))[1].strip()
            row["drift"] = {**char_f1(got, want), "bytes": len(got.encode())}
        else:
            row["drift"] = None
        row["warnings"] = sorted(set(getattr(res.ir, "warnings", None) or []))
        rows.append(row)
    return {"filter": only, "rows": rows, "summary": summarize(rows, only)}


def summarize(rows: list[dict], only: str | None) -> dict:
    """聚合：`ideal` 口径只吃非 `no_truth` 行；`drift` 口径单独聚合，不交叉。"""
    out: dict = {"filter": only, "n_rows": len(rows),
                 "skipped": [r["stem"] for r in rows if "skipped" in r]}
    for batch in ("docx", "pdf"):
        sel = [r for r in rows if r["batch"] == batch and "headings" in r]
        scored = [r for r in sel if not r["headings"]["no_truth"]]
        matched = sum(r["headings"]["matched"] for r in scored)
        ref_total = sum(r["headings"]["ref"] for r in scored)
        rates = [r["tables"]["rate"] for r in sel if r["tables"]["rate"] is not None]
        out[batch] = {
            "n": len(sel),
            # micro：按标题条数加权（一个漏掉的标题就是一票），macro：按篇平均
            "heading_micro_recall": (None if not ref_total
                                     else round(matched / ref_total, 4)),
            "heading_macro_f1": (None if not scored
                                 else round(sum(r["headings"]["f1"] for r in scored)
                                            / len(scored), 4)),
            "headings_matched": matched, "headings_ref": ref_total,
            "no_truth": [r["stem"] for r in sel if r["headings"]["no_truth"]],
            "table_macro_rate": (None if not rates else round(sum(rates) / len(rates), 4)),
            "tables_with_rows": len(rates),
            "drift_macro_f1": (None if not [r for r in sel if r["drift"]]
                               else round(sum(r["drift"]["f1"] for r in sel if r["drift"])
                                          / len([r for r in sel if r["drift"]]), 4)),
        }
    return out


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def print_report(result: dict) -> None:
    rows = result["rows"]
    print(f"{'样本':34s} {'批':5s} {'标题 命中/真值':14s} {'P':7s} {'R':7s} "
          f"{'F1':7s} {'列一致率':9s} {'漂移F1':7s} 告警")
    print("-" * 118)
    for r in rows:
        if "skipped" in r:
            print(f"{r['stem']:34s} {r['batch']:5s} （跳过：{r['skipped']}）")
            continue
        h, t = r["headings"], r["tables"]
        truth = "无真值" if h["no_truth"] else f"{h['matched']}/{h['ref']}"
        print(f"{r['stem']:34s} {r['batch']:5s} {truth:14s} {_fmt(h['p']):7s} "
              f"{_fmt(h['r']):7s} {_fmt(h['f1']):7s} {_fmt(t['rate']):9s} "
              f"{_fmt((r['drift'] or {}).get('f1')):7s} "
              f"{','.join(r['warnings']) or '—'}")
    print("-" * 118)
    for batch in ("docx", "pdf"):
        s = result["summary"][batch]
        print(f"[{batch}] 篇数={s['n']} 标题 micro 召回={_fmt(s['heading_micro_recall'])}"
              f"（{s['headings_matched']}/{s['headings_ref']}）"
              f" macro F1={_fmt(s['heading_macro_f1'])}"
              f" 表格列一致率 macro={_fmt(s['table_macro_rate'])}"
              f"（{s['tables_with_rows']} 篇有表）"
              f" 无真值={s['no_truth'] or '无'}"
              f" 漂移 F1={_fmt(s['drift_macro_f1'])}")
    if result["summary"]["skipped"]:
        print(f"[跳过] {result['summary']['skipped']}")
    print("口径：ideal 列真值为程序化标注（DOCX=XML 样式 / PDF=生成器字号），"
          "非人工逐页标注；drift 列对管线重烤基线，只作回归漂移监控，不作质量真值。")


def write_baseline(result: dict) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "tests/corpus/run_metrics.py --write-baseline 重烤；"
                "改动前请先人工确认每篇的标题命中数变化是有意的。",
        "rows": result["rows"],
        "summary": result["summary"],
    }
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return BASELINE


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default=None, help="按样本文件名前缀过滤（如 docx_）")
    ap.add_argument("--json", default=None, help="把本轮结果写入该路径（work/ 下，不入库）")
    ap.add_argument("--write-baseline", action="store_true",
                    help="用本轮结果重烤冻结基线 ideal-baseline.json")
    args = ap.parse_args(argv)
    result = measure(args.only)
    print_report(result)
    if args.write_baseline:
        if args.only:
            print("FAIL --write-baseline 需要全集（--only 会写出半集基线）")
            return 1
        print(f"基线已重烤 → {write_baseline(result)}")
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写出 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
