#!/usr/bin/env python3
"""运行 tests/corpus 语料，并与 expected/*.md 目标 Markdown 比对。

用法：
  python tests/corpus/run_corpus.py                 # 全量比对
  python tests/corpus/run_corpus.py --only pdf_    # 过滤文件名
  python tests/corpus/run_corpus.py --write-baseline # 用当前输出更新目标（人工审阅后提交）
  python tests/corpus/run_corpus.py --diff-report work/corpus_diff.md
                                                    # 重烤前先出逐行 diff 报告（OPT4-8）
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402

# OPT4-8：重烤前的人工复核面。四类变化里前三类可批量放行，结构变化必须逐条看。
# 判据保守：行数不等的块、低相似度的 1:1 替换一律归「结构变化」（宁可多报）。
DIFF_KINDS = ("仅空白", "仅标点转义", "文本增删", "结构变化")
BATCH_SAFE = frozenset(DIFF_KINDS[:3])
_WS_RE = re.compile(r"\s+")
# Markdown 反斜杠转义：只认 ASCII 标点（`\&` `\|` `\<` `\*` 之类）
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_TEXT_SIMILARITY_FLOOR = 0.3


def _body(md: str) -> str:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            md = md[end + 5:]
    return md.strip() + "\n"


def _expected_path(entry: dict) -> Path:
    return HERE / entry["expected_markdown"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nonspace(s: str) -> str:
    return _WS_RE.sub("", s)


def _unescape(s: str) -> str:
    return _ESCAPE_RE.sub(r"\1", s)


def classify_line(old: str, new: str) -> str:
    """一行变化 → 四类之一。

    仅空白：非空白字符逐个相同（`第一章总则` → `第一章 总则` 属此类）；
    仅标点转义：去掉反斜杠转义后非空白字符相同；
    文本增删：1:1 对齐且相似度 ≥ 0.3（同一行上增删文字）；
    结构变化：其余一切（含行块增删、低相似度替换）——必须逐条看。
    """
    if _nonspace(old) == _nonspace(new):
        return "仅空白"
    if _nonspace(_unescape(old)) == _nonspace(_unescape(new)):
        return "仅标点转义"
    if difflib.SequenceMatcher(None, old, new, autojunk=False).ratio() >= _TEXT_SIMILARITY_FLOOR:
        return "文本增删"
    return "结构变化"


def diff_lines(old_text: str, new_text: str) -> list[dict]:
    """按行对齐后返回逐行变化：[{"kind", "old", "new", "old_no"}]。

    `old` / `new` 为 None 表示该侧整行新增/删除——这类块（行数不等）一律
    归「结构变化」，不做逐行归类。
    """
    a, b = old_text.splitlines(), new_text.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                old, new = a[i1 + k], b[j1 + k]
                out.append({"kind": classify_line(old, new), "old": old,
                            "new": new, "old_no": i1 + k + 1})
            continue
        for k in range(i1, i2):
            out.append({"kind": "结构变化", "old": a[k], "new": None,
                        "old_no": k + 1})
        for k in range(j1, j2):
            out.append({"kind": "结构变化", "old": None, "new": b[k],
                        "old_no": None})
    return out


def build_report(entries: list[dict], results: dict[str, dict]) -> dict:
    """把逐样本比对结果整理成报告结构（不写盘）。

    `results`：{source: {"actual": str|None, "rejected": str|None}}。
    """
    items: list[dict] = []
    for e in entries:
        name = e["source"]
        r = results.get(name) or {}
        if r.get("rejected"):
            items.append({"source": name, "status": "rejected",
                          "code": r["rejected"], "lines": []})
            continue
        if r.get("actual") is None:
            items.append({"source": name, "status": "error",
                          "error": r.get("error", "no output"), "lines": []})
            continue
        ep = _expected_path(e)
        if not ep.is_file():
            items.append({"source": name, "status": "new",
                          "lines": [], "new_lines": len(r["actual"].splitlines())})
            continue
        lines = diff_lines(ep.read_text(encoding="utf-8"), r["actual"])
        items.append({"source": name, "status": "changed" if lines else "same",
                      "lines": lines})
    changed = [it for it in items if it["status"] == "changed"]
    new = [it for it in items if it["status"] == "new"]
    summary = {
        # 「N 篇中 M 篇有变化」的 N 只数有基线、可比较的样本；新增样本单列
        "total": len([it for it in items if it["status"] in ("same", "changed")]),
        "changed": len(changed),
        "new": len(new),
        "same": len([it for it in items if it["status"] == "same"]),
        "rejected": [it["source"] for it in items if it["status"] == "rejected"],
        "errors": [it["source"] for it in items if it["status"] == "error"],
        "whitespace_escape_only": 0,
        "text_only": 0,
        "structural": 0,
        "kind_counts": {k: 0 for k in DIFF_KINDS},
    }
    for it in changed:
        kinds = {ln["kind"] for ln in it["lines"]}
        for ln in it["lines"]:
            summary["kind_counts"][ln["kind"]] += 1
        if "结构变化" in kinds:
            summary["structural"] += 1
        elif kinds <= {"仅空白", "仅标点转义"}:
            summary["whitespace_escape_only"] += 1
        else:
            summary["text_only"] += 1
    return {"items": items, "summary": summary}


def _fence(text: str) -> list[str]:
    """把任意文本裹进不会与内含反引号冲突的围栏块。"""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    bar = "`" * max(3, longest + 1)
    return [bar, text, bar]


def render_report(report: dict) -> str:
    s = report["summary"]
    out = [
        "# 语料产物 diff 报告（重烤前人工复核面）",
        "",
        "- 生成：`tests/corpus/run_corpus.py --diff-report <path>`",
        "- 基线：`tests/corpus/expected/`（管线自己重烤的黄金产物）",
        "- 判类：仅空白 / 仅标点转义 / 文本增删 = 可批量放行；结构变化 = 必须逐条看",
        "- 保守口径：行块增删与低相似度（< 0.3）的 1:1 替换一律归「结构变化」",
        "",
        f"**汇总：{s['total']} 篇中 {s['changed']} 篇有变化，"
        f"其中 {s['whitespace_escape_only']} 篇仅空白/转义类、"
        f"{s['text_only']} 篇仅文本增删类、{s['structural']} 篇含结构变化；"
        f"另 {s['new']} 篇无基线（新增样本）。**",
        "",
        "逐类行数：" + "、".join(f"{k} {s['kind_counts'][k]}" for k in DIFF_KINDS),
    ]
    if s["rejected"]:
        out += ["", f"按预期拒收（不参与比对）：{', '.join(s['rejected'])}"]
    if s["errors"]:
        out += ["", "**转换失败（必须先看）**：" + ", ".join(s["errors"])]
    for it in report["items"]:
        if it["status"] == "new":
            out += ["", f"## {it['source']}（新增样本，无基线，{it['new_lines']} 行）"]
            continue
        if it["status"] != "changed":
            continue
        kinds = {}
        for ln in it["lines"]:
            kinds[ln["kind"]] = kinds.get(ln["kind"], 0) + 1
        detail = "、".join(f"{k} {v}" for k, v in kinds.items())
        out += ["", f"## {it['source']}（{len(it['lines'])} 行变化：{detail}）"]
        for ln in it["lines"]:
            where = f"旧第 {ln['old_no']} 行" if ln["old_no"] else "新增行"
            old = "<无>" if ln["old"] is None else ln["old"]
            new = "<无>" if ln["new"] is None else ln["new"]
            out += ["", f"- {ln['kind']} @{where}"]
            out += ["  " + line for line in _fence(f"旧：{old}")]
            out += ["  " + line for line in _fence(f"新：{new}")]
        out.append("")
    return "\n".join(out) + "\n"


def run(entries: list[dict], *, write_baseline: bool = False,
        diff_report: str | None = None,
        diff_dir: Path | None = None) -> dict:
    """全量转换并比对；返回 {"passed", "failed", "report", "summary_line"}。"""
    passed = failed = 0
    diff_dir = diff_dir or (HERE.parents[1] / "work" / "corpus_diff")
    diff_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="doc2md-corpus-") as td:
        out = Path(td) / "out"
        out.mkdir()
        for e in entries:
            src = HERE / "sources" / e["source"]
            if not src.is_file():
                print(f"FAIL {e['source']}: source missing")
                failed += 1
                results[e["source"]] = {"error": "source missing"}
                continue
            if _sha256(src) != e.get("sha256"):
                print(f"FAIL {e['source']}: source sha256 drifted; rerun make_corpus.py")
                failed += 1
                results[e["source"]] = {"error": "source sha256 drifted"}
                continue
            try:
                res = convert(src, out_dir=out)
            except Doc2mdError as exc:
                want = e.get("expected_behavior", "")
                results[e["source"]] = {"rejected": exc.code}
                if want == f"reject:{exc.code}":
                    print(f"PASS {e['source']}: rejected {exc.code}")
                    passed += 1
                else:
                    print(f"FAIL {e['source']}: unexpected {exc.code} (want {want})")
                    failed += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {e['source']}: {type(exc).__name__}: {exc}")
                failed += 1
                results[e["source"]] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            actual = _body(res.markdown)
            results[e["source"]] = {"actual": actual}
            if e.get("expected_behavior", "").startswith("reject:"):
                print(f"FAIL {e['source']}: expected rejection, got markdown")
                failed += 1
                continue
            ep = _expected_path(e)
            if write_baseline:
                ep.parent.mkdir(parents=True, exist_ok=True)
                ep.write_text(actual, encoding="utf-8")
                print(f"BASELINE {e['source']} -> {ep.relative_to(HERE)}")
                passed += 1
                continue
            want = ep.read_text(encoding="utf-8") if ep.is_file() else ""
            if actual == want:
                print(f"PASS {e['source']}")
                passed += 1
            else:
                dst = diff_dir / (src.stem + ".actual.md")
                dst.write_text(actual, encoding="utf-8")
                print(f"FAIL {e['source']}: diff -> {dst}")
                failed += 1
    report = build_report(entries, results)
    s = report["summary"]
    summary_line = (
        f"汇总：{s['total']} 篇中 {s['changed']} 篇有变化，"
        f"其中 {s['whitespace_escape_only']} 篇仅空白/转义类、"
        f"{s['text_only']} 篇仅文本增删类、{s['structural']} 篇含结构变化；"
        f"另 {s['new']} 篇无基线（新增样本）。")
    if diff_report:
        path = Path(diff_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_report(report), encoding="utf-8")
        print(summary_line)
        print(f"diff 报告 -> {path}")
    print(f"\n{passed} passed, {failed} failed")
    return {"passed": passed, "failed": failed, "report": report,
            "summary_line": summary_line}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只运行名称包含该子串的样本")
    ap.add_argument("--write-baseline", action="store_true",
                    help="用当前转换结果覆盖 expected/*.md")
    ap.add_argument("--diff-report", default=None, metavar="PATH",
                    help="把逐行变化（含类型标注）写到该路径；重烤前先看这份报告")
    args = ap.parse_args()
    entries = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if args.only:
        entries = [e for e in entries if args.only in e["source"]]
    result = run(entries, write_baseline=args.write_baseline,
                 diff_report=args.diff_report)
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
