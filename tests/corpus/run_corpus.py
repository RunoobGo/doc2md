#!/usr/bin/env python3
"""运行 tests/corpus 语料，并与 expected/*.md 目标 Markdown 比对。

用法：
  python tests/corpus/run_corpus.py                 # 全量比对
  python tests/corpus/run_corpus.py --only pdf_    # 过滤文件名
  python tests/corpus/run_corpus.py --write-baseline # 用当前输出更新目标（人工审阅后提交）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只运行名称包含该子串的样本")
    ap.add_argument("--write-baseline", action="store_true",
                    help="用当前转换结果覆盖 expected/*.md")
    args = ap.parse_args()
    entries = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if args.only:
        entries = [e for e in entries if args.only in e["source"]]
    passed = failed = 0
    diff_dir = HERE.parents[1] / "work" / "corpus_diff"
    diff_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="doc2md-corpus-") as td:
        out = Path(td) / "out"
        out.mkdir()
        for e in entries:
            src = HERE / "sources" / e["source"]
            if not src.is_file():
                print(f"FAIL {e['source']}: source missing")
                failed += 1
                continue
            if _sha256(src) != e.get("sha256"):
                print(f"FAIL {e['source']}: source sha256 drifted; rerun make_corpus.py")
                failed += 1
                continue
            try:
                res = convert(src, out_dir=out)
            except Doc2mdError as exc:
                want = e.get("expected_behavior", "")
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
                continue
            actual = _body(res.markdown)
            if e.get("expected_behavior", "").startswith("reject:"):
                print(f"FAIL {e['source']}: expected rejection, got markdown")
                failed += 1
                continue
            ep = _expected_path(e)
            if args.write_baseline:
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
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
