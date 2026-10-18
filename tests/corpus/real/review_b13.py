#!/usr/bin/env python3
"""B-13 真实模板抽查：把真实模板的关键文本与转换 Markdown 对照。

检查：
- 文本框 / sdt / 表格单元格关键文本是否出现在输出
- 页眉/页脚文本是否被排除
- Heading 数、表格块数与 DOCX 结构是否同量级
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from doc2md.errors import Doc2mdError
from doc2md.pipeline.convert import convert

ROOT = Path(__file__).resolve().parents[3] / "work" / "real_samples"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _xml_texts(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        headers = []
        footers = []
        for name in z.namelist():
            if name.startswith("word/header") and name.endswith(".xml"):
                headers.append(z.read(name).decode("utf-8", "replace"))
            if name.startswith("word/footer") and name.endswith(".xml"):
                footers.append(z.read(name).decode("utf-8", "replace"))
    def visible(fragment) -> list[str]:
        if isinstance(fragment, str):
            fragment = ET.fromstring(fragment.encode("utf-8"))
        return [t.text.strip() for t in fragment.iter(f"{W}t")
                if (t.text or "").strip()]

    root = ET.fromstring(xml.encode("utf-8"))
    tb = [x for el in root.iter(f"{W}txbxContent") for x in visible(el)]
    sd = [x for el in root.iter(f"{W}sdtContent") for x in visible(el)]
    hdr = [x for blob in headers for x in visible(blob)]
    ftr = [x for blob in footers for x in visible(blob)]
    return {"textbox": tb, "sdt": sd, "header": hdr, "footer": ftr,
            "all": visible(root)}


def _coverage(samples: list[str], md_norm: str, limit: int = 8) -> tuple[int, int, list[str]]:
    picked = [s for s in samples if len(s) >= 2][:limit]
    hit = [s for s in picked if _norm(s) in md_norm]
    return len(hit), len(picked), picked


def main() -> int:
    rows = []
    out_dir = ROOT / "review_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "word").glob("*.docx")):
        if path.name == "Blog.docx":
            rows.append({"file": path.name, "status": "unreadable_template_package"})
            continue
        try:
            texts = _xml_texts(path)
            r = convert(path, out_dir=out_dir)
            md = _norm(r.markdown)
            tb_hit, tb_n, tb_sample = _coverage(texts["textbox"], md)
            sd_hit, sd_n, sd_sample = _coverage(texts["sdt"], md)
            hf_leak = [x for x in texts["header"] + texts["footer"]
                       if len(x) >= 3 and _norm(x) in md]
            from docx import Document
            d = Document(path)
            docx_heads = sum(1 for p in d.paragraphs
                             if p.style and p.style.name.lower().startswith("heading"))
            md_heads = sum(1 for b in r.ir.blocks if b.type == "heading")
            rows.append({
                "file": path.name, "status": "ok",
                "textbox": {"hit": tb_hit, "sampled": tb_n},
                "sdt": {"hit": sd_hit, "sampled": sd_n},
                "header_footer_leak": hf_leak[:5],
                "docx_headings": docx_heads, "md_headings": md_heads,
                "docx_tables": len(d.tables),
                "md_tables": sum(1 for b in r.ir.blocks if b.type == "table"),
                "blocks": len(r.ir.blocks),
            })
        except Doc2mdError as exc:
            rows.append({"file": path.name, "status": f"reject:{exc.code}"})
        except Exception as exc:  # noqa: BLE001
            rows.append({"file": path.name, "status": f"error:{type(exc).__name__}",
                         "error": str(exc)[:160]})
    report = ROOT / "b13_review.json"
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"templates={len(rows)} ok={len(ok)}")
    for r in rows:
        if r.get("status") != "ok":
            print("NON-OK", r["file"], r.get("status"), r.get("error", ""))
        else:
            print(f"{r['file']}: tb {r['textbox']['hit']}/{r['textbox']['sampled']} "
                  f"sdt {r['sdt']['hit']}/{r['sdt']['sampled']} "
                  f"heads {r['md_headings']}/{r['docx_headings']} "
                  f"tables {r['md_tables']}/{r['docx_tables']} "
                  f"hf_leak={len(r['header_footer_leak'])}")
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
