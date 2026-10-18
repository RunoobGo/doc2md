#!/usr/bin/env python3
"""把 Office `.dotx` 真实模板复制为 `.docx`（只改 OOXML content-type）。

Word 模板与文档的正文 XML 相同；python-docx 仅因 content-type 为 template 而
拒绝打开。本脚本不做内容改写，保留真实样式/编号/页眉页脚/表格/文本框。
样本默认放 work/real_samples/word（work/ 已被 .gitignore 排除）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

TEMPLATE = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
DOCUMENT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def convert(src: Path, dst: Path) -> dict:
    with zipfile.ZipFile(src) as z:
        items = {n: z.read(n) for n in z.namelist()}
    ct = items["[Content_Types].xml"].decode("utf-8")
    if TEMPLATE not in ct:
        raise ValueError("not a Word template content type")
    items["[Content_Types].xml"] = ct.replace(TEMPLATE, DOCUMENT).encode("utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in items.items():
            z.writestr(name, blob)
    return {"source": src.name, "docx": dst.name, "bytes": dst.stat().st_size,
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default=r"C:\Program Files\Microsoft Office\root\Templates\2052")
    ap.add_argument("--out-dir", default="work/real_samples/word")
    args = ap.parse_args()
    src_dir, out_dir = Path(args.src_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for src in sorted(src_dir.glob("*.dotx")):
        if src.name.startswith("~$"):
            continue
        dst = out_dir / (src.stem + ".docx")
        try:
            report.append({**convert(src, dst), "ok": True})
            print(f"OK {src.name} -> {dst.name}")
        except Exception as exc:  # noqa: BLE001
            report.append({"source": src.name, "ok": False,
                           "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {src.name}: {type(exc).__name__}: {exc}")
    (out_dir.parent / "template_docx_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok {sum(1 for r in report if r['ok'])}/{len(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
