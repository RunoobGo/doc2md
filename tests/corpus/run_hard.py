#!/usr/bin/env python3
"""难度语料对账器（信息层，非门禁）——`tests/corpus/hard/expectations.json` ↔ 当前产物。

定位与纪律
----------
- 真值侧来自 `tests/qa/gen_cases.py`：**先写样本再造文档**，expectations 是生成时
  声明的构造意图，不是从产物回抄的，因此对本模块而言是独立真值（C-31 自啮合禁令的
  前提在此成立）。
- 本脚本**不被 pytest 收集**，也不进 `pytest -m gate`：D02 / D05 目前仍是产品缺口
  （见 `docs/qa/round3/deviation-analysis.md` 与 dev-plan 12.7 的 B-43 / B-44），
  把它写成断言只会得到一条红或一次 skip，两者都不等于「修好了」。缺口闭合后由
  `gen_cases.py --pytest` 生成收口测试再入库（B-46 的出口条件）。
- 与 `tests/qa/test_hard_corpus.py`（同一 `expectations.json` 的自动骨架）的分工：
  骨架只核对 `contains` / `not_contains`，**不看 `tables` / `headings` 结构**，所以
  D02 的裂表在骨架里是一条「TODO 未填断言」的 skip 而非缺陷；本脚本核对结构面。
  D08 的层级冲突（生成器声明 H1、产品按 CJK 序号映射给 H2）属真值侧口径待裁定，
  登记 B-45，本脚本按声明如实报 FAIL，不擅自改期望。
- 结构面的 **pytest 可判定形态**由 `tests/qa/test_hard_gate.py` 承担（复用本模块的
  `check_spec`，`sink=` 交出同一次转换的产物）；本脚本仍是信息层，不被 pytest 收集。
- 退出码：全绿 0；仍有缺口 1（供人手工复核与后续 CI 信息层采集）。

用法
----
    .venv/bin/python tests/corpus/run_hard.py            # 全 10 份
    .venv/bin/python tests/corpus/run_hard.py D02 D05    # 按难度号挑
    .venv/bin/python tests/corpus/run_hard.py --json work/qa/hard.json   # 供用例卡回填
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402
from tests.qa.mdscore import parse_blocks, strip_front_matter  # noqa: E402

HARD = ROOT / "tests" / "corpus" / "hard"
BLOCKS = ("tables", "headings", "contains", "not_contains", "behavior")


def _tables(md: str) -> list[dict]:
    return [b.meta for b in parse_blocks(md) if b.type == "table"]


def _headings(md: str) -> list:
    return [b for b in parse_blocks(md) if b.type == "heading"]


def _check_tables(exp: list[dict], got: list[dict]) -> list[tuple[str, bool, str]]:
    rows = []
    for i, want in enumerate(exp):
        if i >= len(got):
            rows.append((f"table[{i}]", False, f"漏检：产物只有 {len(got)} 张表"))
            continue
        header = [c.strip() for c in got[i].get("header") or []]
        body = [[c.strip() for c in r] for r in (got[i].get("body") or [])]
        ncol = got[i].get("ncol") or len(header)
        nrow = len(body) + (1 if header else 0)
        bad = []
        if "ncol" in want and ncol != want["ncol"]:
            bad.append(f"列数 {ncol} != {want['ncol']}")
        if "nrow" in want and nrow != want["nrow"]:
            bad.append(f"行数 {nrow} != {want['nrow']}")
        if "nrow_min" in want and nrow < want["nrow_min"]:
            bad.append(f"行数 {nrow} < 下限 {want['nrow_min']}")
        if "header" in want and header != [c.strip() for c in want["header"]]:
            bad.append(f"表头 {header} != {want['header']}")
        if "body" in want:
            want_body = [[c.strip() for c in r] for r in want["body"]]
            for r, (g, w) in enumerate(zip(body, want_body)):
                if g != w:
                    bad.append(f"体行{r + 1} {g} != {w}")
            if len(body) != len(want_body):
                bad.append(f"体行数 {len(body)} != {len(want_body)}")
        if len(exp) == 1 and len(got) > 1:
            bad.append(f"裂表：产物 {len(got)} 张")
        rows.append((f"table[{i}]", not bad, "; ".join(bad) or f"{nrow}x{ncol} 一致"))
    if exp and len(got) > len(exp):
        rows.append(("table 多余", False, f"产物 {len(got)} 张 > 期望 {len(exp)} 张"))
    return rows


def check_spec(spec: dict, out_dir: Path, *, sink: dict | None = None) -> list[tuple[str, bool, str]]:
    """逐条对账一个难度样本；返回 (检查项, 是否达成, 实测说明)。

    `sink` 给出时把中间产物交给调用方（`md` / `res` / `error_code` / `detail`），供
    `tests/qa/test_hard_gate.py` 复用同一次转换——否则门禁腿要么二次转换（双倍
    墙钟），要么自己重写一套检查（两处维护、必然漂移）。
    """
    src = HARD / spec["source"]
    exp = spec["expect"]
    if not src.is_file():
        return [("__asset__", False, f"{spec['source']} 未生成（先跑 gen_cases.py）")]
    try:
        res = convert(src, out_dir=out_dir, mode="single", force=True)
    except Doc2mdError as exc:
        want = exp.get("behavior")
        detail = getattr(exc, "detail", None) or {}
        if sink is not None:
            sink["error_code"] = exc.code
            sink["detail"] = detail
        return [("behavior", want == f"reject:{exc.code}",
                 f"reject:{exc.code} detail={detail}，期望 {want}")]
    if exp.get("behavior"):
        if sink is not None:
            sink["res"] = res
        return [("behavior", False, f"期望 {exp['behavior']}，实际转换成功")]
    md = strip_front_matter(res.markdown)[1]
    if sink is not None:
        sink["md"], sink["res"] = md, res
    rows = _check_tables(exp.get("tables", []), _tables(md))
    heads = _headings(md)
    for h in exp.get("headings", []):
        hit = [b for b in heads if (b.text or "").strip() == h["text"].strip()]
        if not hit:
            rows.append((f"heading[{h['text']}]", False, "未出现"))
        elif h.get("level") and hit[0].level != h["level"]:
            rows.append((f"heading[{h['text']}]", False,
                         f"层级 H{hit[0].level} != 声明 H{h['level']}"))
        else:
            rows.append((f"heading[{h['text']}]", True, f"H{hit[0].level}"))
    for tok in exp.get("contains", []):
        rows.append((f"contains[{tok[:20]}]", tok in md,
                     "命中" if tok in md else "缺失"))
    for tok in exp.get("not_contains", []):
        rows.append((f"absent[{tok[:20]}]", tok not in md,
                     "已剔除" if tok not in md else "残留"))
    return rows


def _split_json_arg(argv: list[str]) -> tuple[list[str], str | None]:
    """`--json PATH` 与难度号混用：先把开关摘掉，剩下的才是难度号。"""
    args = list(argv)
    path = None
    if "--json" in args:
        i = args.index("--json")
        if i + 1 >= len(args):
            raise SystemExit("--json 需要一个路径参数")
        path = args[i + 1]
        del args[i:i + 2]
    return args, path


def main(argv: list[str]) -> int:
    argv, json_path = _split_json_arg(argv)
    specs = json.loads((HARD / "expectations.json").read_text(encoding="utf-8"))
    if argv:
        wanted = set(argv)
        specs = [s for s in specs if s["difficulty"] in wanted]
        if not specs:
            print(f"未知难度号：{' '.join(argv)}", file=sys.stderr)
            return 2
    passed = 0
    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="d2m-hard-") as tmp:
        out_dir = Path(tmp)
        for spec in specs:
            rows = check_spec(spec, out_dir)
            ok = all(r[1] for r in rows)
            passed += int(ok)
            print(f"== {spec['difficulty']} {spec['name']} "
                  f"({spec['source']}) → {'PASS' if ok else 'FAIL'}")
            for name, hit, msg in rows:
                print(f"   {'PASS' if hit else 'FAIL'} {name}: {msg}")
            records.append({
                "difficulty": spec["difficulty"], "name": spec["name"],
                "source": spec["source"], "ok": ok, "star": spec.get("star"),
                "judge": spec.get("judge", ""),
                "failed_rows": [{"check": n, "detail": m} for n, h, m in rows if not h],
            })
    summary = f"SUMMARY {passed}/{len(specs)} 难度样本全绿"
    print(f"\n{summary}"
          f"（{'信息层，未入门禁' if passed < len(specs) else '缺口已清零，可入库为 gate'}）")
    if json_path:
        # Task 8 的用例卡回填要按难度号取**本轮**实际结果；没有这条 JSON，
        # D01–D10 十张卡只能集体记「未执行」，而那十条腿其实刚刚跑过。
        from datetime import datetime, timezone

        payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "n": len(specs), "passed": passed, "summary": summary,
                   "records": records}
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return 0 if passed == len(specs) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
