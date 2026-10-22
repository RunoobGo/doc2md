#!/usr/bin/env python3
"""tests/corpus/make_ideal_pdf.py — N-3（QA-OPT-3）：PDF 标题真值标注生成。

真值口径：**生成器绘制意图**。`make_corpus.py` 用 reportlab 以标题字号
绘制标题行（18/20 → 章级 H1；16 → 章级 H1；14 → 节级 H2，但 pdf_09 的
「三、/五、」中文序号 14pt 是章级 H1），字号约定与正文（10~12pt）明确
分层。本脚本从**既有源 PDF**（pdfplumber 逐行取 字号+文本，不重跑生成
器、不动源文件哈希）按每样本字号规则导出真值 → `ideal/pdf_*.ideal.json`，
与 S3 启发式（pred）两层独立，满足 ideal/README.md 的双口径纪律。

不在覆盖面的样本（如实记录，非漏标）：
- pdf_06_scanned_1p.pdf：无文本层，S1.5 门禁预期拒收，无标题真值；
- pdf_10_two_column_6p.pdf：全部 14/12pt 行是「第 N 页第 M 栏」栏头，
  O-09（C-02）已定性为页眉家具，不判标题（真值=空集，仍出标注文件）。

用法：`.venv/bin/python tests/corpus/make_ideal_pdf.py [--check]`
- 默认：全量（重）生成 ideal/pdf_*.ideal.json；
- `--check`：不写盘，校验现有标注与源 PDF 字号分布一致（防标注漂移）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
IDEAL = HERE / "ideal"
SOURCES = HERE / "sources"

# 无标题真值的样本：pdf_06 无文本层（门禁拒收）；其余可转换样本全部覆盖，
# pdf_10 出空集标注（家具定性见模块 docstring）。
SKIPPED = {"pdf_06_scanned_1p.pdf"}


def _lines_by_size(path: Path) -> list[tuple[float, str]]:
    """源 PDF → [(字号, 行文本)]（pdfplumber extract_text_lines，行首字符字号）。"""
    out: list[tuple[float, str]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for ln in page.extract_text_lines():
                size = round(ln["chars"][0]["size"], 1) if ln["chars"] else 0.0
                t = ln["text"].strip()
                if t:
                    out.append((size, t))
    return out


# --- 每样本字号规则（注释对应 make_corpus.py 的绘制意图）--------------------

def _h1_18_h2_14(lines):
    """pdf_01/02/03/04/07/08：18=章级 H1、14=节级 H2、其余正文。"""
    return [(t, 1 if s == 18 else 2) for s, t in lines if s in (18.0, 14.0)]


def _pdf05(lines):
    """pdf_05：三页章标题一律 18pt → H1。"""
    return [(t, 1) for s, t in lines if s == 18.0]


def _pdf09(lines):
    """pdf_09：20=主标题 H1；16=章级 H1；14 按**中文序号形态**分派——
    「三、/五、」带顿号序号是章级 H1，「（一）」括号形态是节级 H2
    （与 _level_from_number 的 CJK_ORD→1 / PAREN_HEAD→2 同一约定）。
    12pt「副标题」、10.5pt「表 1/图 1」题注、9pt 页眉页脚不判标题。"""
    out: list[tuple[str, int]] = []
    for s, t in lines:
        if s == 20.0 or (s == 16.0 and t[:2] in ("二、", "四、", "六、")):
            out.append((t, 1))
        elif s == 14.0:
            out.append((t, 1 if t[:2] in ("三、", "五、") else 2))
    return out


def _pdf11(lines):
    """pdf_11：16pt「七、折行空白保真」→ H1；正文 11pt。"""
    return [(t, 1) for s, t in lines if s == 16.0]


def _pdf10(lines):
    """pdf_10：栏头（14/12pt「第 N 页第 M 栏」）是页眉家具 → 空集。"""
    return []


# 键 = 样本 stem 前缀（glob 匹配 sources/<key>*.pdf）
RULES: dict[str, callable] = {
    "pdf_01": _h1_18_h2_14,
    "pdf_02": _h1_18_h2_14,
    "pdf_03": lambda lines: [(t, 1) for s, t in lines if s == 18.0],
    "pdf_04": _h1_18_h2_14,
    "pdf_05": _pdf05,
    "pdf_07": _h1_18_h2_14,
    "pdf_08": _h1_18_h2_14,
    "pdf_09": _pdf09,
    "pdf_10": _pdf10,
    "pdf_11": _pdf11,
}


def heading_truth(path: Path) -> list[dict]:
    stem = path.stem
    rule = next((r for k, r in RULES.items() if stem.startswith(k)), None)
    if rule is None:
        raise KeyError(f"未登记的 PDF 样本：{path.name}（规则表需同步补）")
    truth = rule(_lines_by_size(path))
    return [{"seq": i, "text": t, "level": lv}
            for i, (t, lv) in enumerate(truth, 1)]


def build_annotation(path: Path) -> dict:
    return {
        "source": path.name,
        "kind": "headings",
        "provenance": "程序化生成自源 PDF 字号分布（pdfplumber 逐行字号 + "
                      "make_corpus.py 生成器的标题字号约定：18/20/16=章级，"
                      "14=节级或带顿号中文序号的章级）；生成脚本 "
                      "tests/corpus/make_ideal_pdf.py，人工可复核。与 "
                      "expected/ 基线（管线产物）无关。",
        "headings": heading_truth(path),
    }


def _sources() -> list[Path]:
    return sorted(p for p in SOURCES.glob("pdf_*.pdf")
                  if p.name not in SKIPPED)


def main() -> int:
    check = "--check" in sys.argv
    sources = _sources()
    if not sources:
        print("no pdf sources", file=sys.stderr)
        return 1
    if not check:
        IDEAL.mkdir(exist_ok=True, parents=True)
    bad = []
    for p in sources:
        ann = build_annotation(p)
        target = IDEAL / f"{p.stem}.ideal.json"
        payload = json.dumps(ann, ensure_ascii=False, indent=2) + "\n"
        if check:
            if not target.is_file():
                bad.append(f"{target.name}: 缺失")
                continue
            if target.read_text(encoding="utf-8") != payload:
                bad.append(f"{target.name}: 与源字号分布不一致")
        else:
            target.write_text(payload, encoding="utf-8")
            print(f"IDEAL {target.name}: {len(ann['headings'])} headings")
    if check and bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
