#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""defect_scan.py —— 产物侧结构缺陷扫描（9 条规则，每条自带变异自检）。

**为什么规则要带自检**：第十七轮的观察 O-g 是「判定层没有牙齿」——断言写成
「有缺口就 xfail、没缺口就 skip」，于是产品好坏都绿。规则型扫描器有同一个退化形态：
一条**永远不命中**的规则和一条**不存在**的规则，在报告里长得一模一样。所以每条规则
都配一对变异样本（`RULE_SELFTESTS`）：正向必须命中、干净必须沉默，两者由
`tests/test_ch501_qa_auto.py` 的两条 gate 红线钉住。

规则来源不是凭空设计：R1/R2/R3 对应已登记的 B-43/B-44，R4 对应 B-47（乱码文本层），
R5 对应 D08 页眉变体，R6 对应 C-29/O-05 的「诚实暴露而非静默降级」，R7/R9 对应
`mdscore.LINT_CLASS` 里的 MD040 与 FR-107，R8 对应 S3 的 row_shape / TCOLC。

R19-QA-4：上一行原写作「MARKDOWN_SPEC 的 MD040」，而仓库里从来没有 `MARKDOWN_SPEC`
这个符号——它只出现在 `docs/superpowers/plans/2026-10-25-ch501-realtest.md`。引用一个
不存在的权威，等于把「MD040 属契约类」这个前提置于**无人能核**的状态；它随后被
`mdscore.LINT_CLASS` 抄走，一路污染 evaluate 的门禁判定、attribute 的阶段归因与
prioritize 的 P0 分档。凡判定口径，一律指向可 grep 的符号。

本模块是**信息层**：命中只报告、不阻断（退出码恒 0）。要变成红线，先在
`tests/qa/test_hard_gate.py` 里以 expectations 的形式落地，别在这里加 sys.exit(1)。

用法：
    .venv/bin/python tests/qa/defect_scan.py
    .venv/bin/python tests/qa/defect_scan.py --only pdf_ --json work/qa/defects.json \\
        --md work/qa/defects.md

退出码：逐样本失败记为 `status="rejected"/"error"`，不中断批次，运行结果恒 0；
参数/清单错误退 2。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CORPUS = HERE.parent / "corpus"
sys.path.insert(0, str(REPO))

from doc2md.errors import Doc2mdError  # noqa: E402
from doc2md.pipeline.convert import convert  # noqa: E402
from tests.qa import attribute, evaluate, mdscore  # noqa: E402
from tests.qa import srcstruct as src_extract  # noqa: E402


@dataclass
class Finding:
    rule: str
    severity: str
    where: str          # 块下标 / 行号，便于人工复核
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanCtx:
    """规则可选的上下文。没有 ctx 时，依赖 ctx 的规则**跳过并在报告里计数**，
    不静默当作「无缺陷」（跳过的规则数必须可见，否则又是一层空转）。"""
    ref_headings: list[dict] | None = None
    warnings: list[str] | None = None


SEVERITY = {"split_table": "blocking", "leader_dots_in_cell": "blocking",
            "cjk_space_collapse": "major", "mojibake_residue": "blocking",
            "furniture_residue": "major", "silent_degrade": "blocking",
            "fence_no_lang": "minor", "col_count_drift": "major",
            "duplicate_anchor": "major"}

#: 语料基线上的**已知非缺陷命中**（`source stem → {规则名: 命中数}`）。R19-QA-3 把这张
#: 表从 `tests/test_ch503_qa_face.py`（CH-503 / R18-QA-2 冻结）挪到扫描器侧：`prioritize.py`
#: 要据此区分「可施工的缺陷」与「语料设计的预期形态」，而生产侧 QA 工具去 import 一个
#: `test_` 模块是反向依赖。红线强度不变——CH-503 改为 import 本表，并继续断言「22 篇基线
#: 的命中集合 == 本表」；想让一条新命中过线，仍必须显式改这里。
#:
#: 两条登记的成因都由语料生成器证实，不是推断：
#: - `fence_no_lang`（minor）：`make_corpus.py` 特意造了**不带语言标注**的围栏样本，这是
#:   MD040 的展示用例，不是管线丢语言——语言标注在源侧本就不存在（见
#:   `mdscore.MD040_DIALECT_REASON`）。
#: - `duplicate_anchor`（major）：大样本生成器循环输出同名二级标题（`make_corpus.py:190`
#:   的 `_para(…, "1.1 章节说明", 14)` 与 `:633` 的 `add_heading("1.1 章节说明", level=2)`），
#:   重复标题是**语料设计**，不是 TOC 缓存被当正文（D09 的真实形态）。
#:
#: 登记是 **(样本, 规则)** 级的，不是规则级的：真实样本腿上同一条规则命中不在本表里，
#: 照常出项、照常施工。
CORPUS_KNOWN_BENIGN: dict[str, dict[str, int]] = {
    "pdf_04_links_code_2p": {"fence_no_lang": 1},
    "docx_07_code_quote": {"fence_no_lang": 1},
    "pdf_08_large_40p": {"duplicate_anchor": 1},
    "docx_08_large_30sections": {"duplicate_anchor": 1},
}

#: 已知命中允许来自的规则集。**从上表派生**而不是另写一份——两份并集迟早分叉。
CORPUS_KNOWN_RULES = frozenset(r for v in CORPUS_KNOWN_BENIGN.values() for r in v)


def known_benign(source: str | Path, rule: str) -> bool:
    """该 (样本, 规则) 命中是否属已登记的预期形态。"""
    return rule in CORPUS_KNOWN_BENIGN.get(Path(source).stem, {})

#: 页眉/页码家具。页码页脚必须是**整行**（`第 N 页` / `第 N 页 共 M 页`），
#: 否则 pdf_08 的正文 `第 1 页第 1 段：…` 会被整段误判；
#: `奇数页/偶数页` 保持非锚定——D08 的页眉是 `第 N 章 …　奇数页`。
#: 每行单独 search，`^...$` 已按整行约束，无需 re.M。
#: 模式与 `tests/qa/attribute.py` 保持同一份（R5 与归因器的 furniture 信号同源）。
_FURNITURE = attribute._FURNITURE
_MOJIBAKE = attribute._MOJIBAKE
#: ASCII 点与间隔号保持 brief 口径；省略号收紧到 `…{3,}`——中文标准六点省略号
#: 「……」只是两个 U+2026，不是目录引导符，否则普通单元格会被当成 B-44 误报。
_LEADER = re.compile(r"(?:\.{4,}|…{3,}|·{4,})")
_CJK = r"[\u4e00-\u9fff]"


def body_lines(md: str) -> list[tuple[int, str]]:
    """剥 front-matter 后的正文行，排除代码围栏内与表格行（R5 用）。

    围栏按「字符 + 长度」跟踪：开栏记下是 ``` 还是 ~~~ 及长度 N，只有同字符且
    长度 ≥ N 的行才关闭——否则 4 反引号块里的 ``` 会提前收尾，块内 `第 3 页`
    被当正文（假阳性）。表格行只认行首 `|`，逐行独立判定、不维护「表格状态」；
    正文里的 `|` 因此不会吞掉其后的页码行（假阴性）。
    """
    _, body = mdscore.strip_front_matter(md or "")
    out: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for n, ln in enumerate(body.replace("\r\n", "\n").split("\n"), start=1):
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", ln)
        if m:
            run = m.group(1)
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1]:
                fence = None
            continue
        if fence is not None:
            continue
        if ln.lstrip().startswith("|"):
            continue
        out.append((n, ln))
    return out


def r_split_table(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    # attribute.split_tables 返回的是**相邻边界**；三连裂表会给出 (3,4) 与 (4,5)。
    # 这里把共享端点的边界并成一条链，一条链只记一个 Finding——否则 T6 拿 findings
    # 数当缺陷数时，同一处裂表会被重复计数。
    chains: list[list[int]] = []
    for a, b in attribute.split_tables(blocks):
        if chains and chains[-1][-1] == a:
            chains[-1].append(b)
        else:
            chains.append([a, b])
    out = []
    for ch in chains:
        hdr = blocks[ch[0]].meta.get("header") or []
        where = "block" + "→".join(f"[{i}]" for i in ch)
        out.append(Finding("split_table", SEVERITY["split_table"], where,
                           f"{len(ch)} 个相邻表表头相同 {hdr}：跨页/跨片表被裂成"
                           f" {len(ch) - 1} 处（B-43 形态）"))
    return out


def r_leader_dots(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    out = []
    for i, b in enumerate(blocks):
        if b.type != "table":
            continue
        for r, row in enumerate([b.meta.get("header") or []] + list(b.meta.get("body") or [])):
            for c, cell in enumerate(row or []):
                if _LEADER.search(str(cell)):
                    out.append(Finding("leader_dots_in_cell",
                                       SEVERITY["leader_dots_in_cell"],
                                       f"block[{i}] row{r} col{c}",
                                       f"引导符成了单元格内容：{cell!r}（B-44 形态）"))
    return out


def r_cjk_space_collapse(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    """源端标题里的 CJK 间空格在产物中消失（`第一章 总则` → `第一章总则`）。"""
    if ctx is None or not ctx.ref_headings:
        return []
    pat = re.compile(rf"(?<=({_CJK}))\s+(?=({_CJK}))")
    want = {re.sub(r"\s+", "", h.get("text", "")): h.get("text", "")
            for h in ctx.ref_headings if pat.search(h.get("text", ""))}
    if not want:
        return []
    got = {re.sub(r"\s+", "", b.text): b.text
           for b in blocks if b.type == "heading"}
    out = []
    for key, src_text in want.items():
        prod = got.get(key)
        # U+3000 也是空白：只判 ASCII 空格会把「全角空格替换」误报为空格被吞。
        if prod is not None and prod != src_text and not re.search(r"\s", prod):
            out.append(Finding("cjk_space_collapse", SEVERITY["cjk_space_collapse"],
                               f"heading[{prod!r}]",
                               f"源端 {src_text!r} 的空格被吞成 {prod!r}（B-44 形态）"))
    return out


def r_mojibake(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    _, body = mdscore.strip_front_matter(md or "")
    hits = _MOJIBAKE.findall(body)
    if not hits:
        return []
    return [Finding("mojibake_residue", SEVERITY["mojibake_residue"], "body",
                    f"乱码残留 {len(hits)} 处，样例 {hits[:3]}（B-47：应拒收或告警）")]


def r_furniture(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    # 逐行 search：页码分支自带 `^...$` 整行约束，`奇数页|偶数页` 才需要非锚定命中
    hits = [(n, ln.strip()) for n, ln in body_lines(md) if _FURNITURE.search(ln)]
    if not hits:
        return []
    return [Finding("furniture_residue", SEVERITY["furniture_residue"],
                    f"line {hits[0][0]}…", f"页眉/页码残留 {len(hits)} 行：{hits[:3]}")]


def r_silent_degrade(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    """HTML 表降级必须伴随 W1043/W1045 告警；否则记为静默降级（C-29/O-05）。"""
    html = [i for i, b in enumerate(blocks) if b.type == "html"
            and re.search(r"<table\b", b.text, re.I)]
    if not html:
        return []
    warns = list((ctx.warnings if ctx else None) or [])
    # 只认真正针对「HTML 表降级」的码：W1045 是表格降级告警，W1043 是分片 fallback。
    # W1044 是每份分片文档必写的说明码、W1046 是版式（旋转/RTL/页眉）码，二者都会
    # 无条件掩盖真实降级，故不列入掩盖集合（与 mdscore.honesty_score 的口径差异在此：
    # 宁可多报也不能让「无 W1045」被一份无关说明码洗成清白）。
    if any(w.split(":")[0] in ("W1043", "W1045") for w in warns):
        return []
    return [Finding("silent_degrade", SEVERITY["silent_degrade"],
                    f"block{html}", f"{len(html)} 处 HTML 表格降级但无 W1043/W1045 告警"
                    f"（实际告警 {warns}）")]


def r_fence_no_lang(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    bare = [i for i, b in enumerate(blocks) if b.type == "code" and not b.meta.get("lang")]
    if not bare:
        return []
    # R19-QA-4：证据串原写「MD040 契约类」，随 mdscore.LINT_CLASS 的改档一并更正。
    # 类别与理由都从 mdscore 取，不再各写一份——两份口径正是上一轮走偏的起点。
    return [Finding("fence_no_lang", SEVERITY["fence_no_lang"], f"block{bare}",
                    f"{len(bare)} 个代码块缺语言标注（MD040 "
                    f"{mdscore.LINT_CLASS['MD040']} 类：{mdscore.MD040_DIALECT_REASON}）")]


def r_col_count_drift(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    out = []
    for i, b in enumerate(blocks):
        if b.type != "table":
            continue
        lines = [ln for ln in b.raw.split("\n") if ln.strip()]
        if len(lines) < 3:
            continue
        counts = {len(mdscore._split_row(ln)) for ln in [lines[0]] + lines[2:]}
        if len(counts) > 1:
            out.append(Finding("col_count_drift", SEVERITY["col_count_drift"],
                               f"block[{i}]", f"同表内列数不一致 {sorted(counts)}"))
    return out


def r_duplicate_anchor(md: str, blocks: list, ctx: ScanCtx | None) -> list[Finding]:
    # 只剥行尾「制表位 + 页码」（TOC 缓存形态，如 `第一章 总则\t1`）；标题非行尾的
    # 制表位按空白保留，由 slugger 归一为 `-`，不得把它当截断点（test_hard_gate D09 同口径）。
    heads = [re.sub(r"\t\s*\d+\s*$", "", b.text) for b in blocks if b.type == "heading"]
    slugs = [mdscore._slugify_heading(t) for t in heads]
    dup = sorted({s for s in slugs if slugs.count(s) > 1})
    if not dup:
        return []
    texts = [t for t, s in zip(heads, slugs) if s in dup]
    # R19-QA-2：证据串原先断言成因（「TOC 缓存结果被当正文标题的典型形态」）并截成
    # texts[:5]，两处都错。① 语料里 pdf_08 / docx_08 的重复标题出自 `make_corpus.py`
    # 的生成器循环（40 页 / 30 节各输出一次同名二级标题），第十八轮已认定是**语料设计**
    # 而非 TOC 误判，断言却把已被否掉的成因写死在报告里，每轮都把工程师指向 B-48/D09；
    # ② texts 是**逐次命中**的列表，40 个同名标题截 5 条只是把同一字符串抄五遍，真正
    # 有信息量的重数（40 / 30）反而看不见。改为按「不同标题 → 重数」报告，截断只作用于
    # 去重后的种类且显式说明剩余量。
    kinds = list(dict.fromkeys(texts))
    shown = kinds[:5]
    counts = {t: texts.count(t) for t in shown}
    more = f"，另有 {len(kinds) - len(shown)} 种未列" if len(kinds) > len(shown) else ""
    return [Finding("duplicate_anchor", SEVERITY["duplicate_anchor"], "headings",
                    f"重复锚点 {len(dup)} 个 {dup}，标题重数 {counts}{more}"
                    f"（源侧同名标题与 TOC 缓存误判同形，须复核源文档才能定性，不断言成因）")]


RULES = {
    "split_table": r_split_table,
    "leader_dots_in_cell": r_leader_dots,
    "cjk_space_collapse": r_cjk_space_collapse,
    "mojibake_residue": r_mojibake,
    "furniture_residue": r_furniture,
    "silent_degrade": r_silent_degrade,
    "fence_no_lang": r_fence_no_lang,
    "col_count_drift": r_col_count_drift,
    "duplicate_anchor": r_duplicate_anchor,
}

#: 规则名 → (正向变异样本, 干净样本, 正向 ctx, 干净 ctx)。
#: 这对样本是规则的**存在性证明**；四元组而不是三元组，因为 `silent_degrade` 的
#: 「干净腿」不是「没有 HTML 表」，而是「同一张 HTML 表 + 带 W1045 告警」——
#: 正反两腿需要**不同的 ctx**，靠定义后改写赋值太脆弱。
_CLEAN = """# 第一章 总则

| 季度 | 收入 | 成本 |
| --- | --- | --- |
| 上半年 | 2,000,000 | 1,500,000 |

正文段落，含 English 与 1,234,567。

```python
print("ok")
```
"""
_REF = [{"text": "第一章 总则", "level": 1}]
_HTML_TBL = "<table><tr><td>合并</td></tr></table>\n"

#: `duplicate_anchor` 的正向样本**同时**含两种重复形态：`# A 总则` 与
#: `# A 总则\t1`（TOC 缓存形态，只有先剥行尾页码才判得出来），以及一处
#: 普通重复标题——两者都命中才证明归一化真的生效，而不是靠某一种形态偶然过线。
_DUP_POS = ("# A 总则\n\n# A 总则\t1\n\n正文。\n\n"
            "# 第二章 职责\n\n# 第二章 职责\n")

RULE_SELFTESTS: dict[str, tuple[str, str, ScanCtx | None, ScanCtx | None]] = {
    "split_table": (
        "| 季度 | 收入 |\n| --- | --- |\n| Q1 | 1 |\n\n| 季度 | 收入 |\n| --- | --- |\n| Q2 | 2 |\n",
        "| 季度 | 收入 |\n| --- | --- |\n| Q1 | 1 |\n| Q2 | 2 |\n", None, None),
    "leader_dots_in_cell": (
        "| 条目 | 页 |\n| --- | --- |\n| 第一章 总则 ....... | 1 |\n",
        # 干净腿锁住「……」误报：两个 U+2026 是中文六点省略号，不是目录引导符。
        "| 条目 | 页 |\n| --- | --- |\n| 第一章 总则 | 1 |\n| 待定…… | - |\n",
        None, None),
    "cjk_space_collapse": (
        "# 第一章总则\n", "# 第一章 总则\n",
        ScanCtx(ref_headings=_REF), ScanCtx(ref_headings=_REF)),
    "mojibake_residue": (
        "(cid:142)(cid:87) report\n", "normal report 2026\n", None, None),
    "furniture_residue": (
        "# 标题\n\n正文。\n\n- 12 -\n\n第 3 页\n", "# 标题\n\n正文 2026 年。\n", None, None),
    "silent_degrade": (
        _HTML_TBL, _HTML_TBL, None, ScanCtx(warnings=["W1045"])),
    "fence_no_lang": (
        "```\nprint(1)\n```\n", "```python\nprint(1)\n```\n", None, None),
    "col_count_drift": (
        "| a | b |\n| --- | --- |\n| 1 | 2 | 3 |\n",
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n", None, None),
    "duplicate_anchor": (
        _DUP_POS, "# 第一章 总则\n\n# 第二章 职责\n", None, None),
}

#: `_CLEAN` 是**全规则共用**的干净样本：任何规则在它上面命中都算误报。
CLEAN_FIXTURE = _CLEAN
CLEAN_FIXTURE_CTX = ScanCtx(ref_headings=_REF, warnings=[])


def scan(md: str, *, ctx: ScanCtx | None = None) -> list[Finding]:
    blocks = mdscore.parse_blocks(md or "")
    out: list[Finding] = []
    for name, fn in RULES.items():
        out += fn(md or "", blocks, ctx)
    return out


def _neutral_record(source: Path, status: str) -> dict:
    """非 ok 记录的统一骨架：键集与 ok 记录一致，数值全部中性化。

    `status` 必填——骨架默认值一旦是 `"ok"`，将来漏改就是一份假 clean 记录。
    """
    return {
        "source": source.name,
        "status": status,
        "findings": [],
        "by_rule": {n: 0 for n in RULES},
        "by_severity": {s: 0 for s in ("blocking", "major", "minor")},
        "skipped_rules": [],
        "tcolc": None,
    }


def _error_record(source: Path, exc: Exception) -> dict:
    out = _neutral_record(source, "error")
    out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def scan_one(source: Path, *, out_dir: Path) -> dict:
    """真机转换 → 源端真值 → 扫描。拒收样本记 rejected（不扫，无产物可扫）。

    整个逐样本流程都在错误边界内：只有转换抛 `Doc2mdError` 才是 `rejected`
    （S1.5 门禁裁决），其余任何阶段失败都记 `status="error"` 并返回中性骨架，
    绝不向上抛——否则一份坏样本会中断整批并让 CLI 退非零（与 attribute_one 同规格）。
    """
    try:
        try:
            res = convert(source, out_dir=out_dir, force=True)
        except Doc2mdError as exc:
            out = _neutral_record(source, "rejected")
            out["error_code"] = exc.code
            return out

        md = res.markdown
        gt = src_extract.extract(source)
        ideal = evaluate.load_ideal(source.name)
        # 与 evaluate.evaluate_one 同口径：ideal 存在即权威，headings=[] 是真值空集，
        # 不得回退到源端启发式（pdf_10 的栏头会被误当成漏检标题）。
        ref_headings = (ideal or {}).get("headings", gt.get("headings", [])) \
            if (ideal or gt) else []
        ctx = ScanCtx(ref_headings=ref_headings,
                      warnings=list((res.meta or {}).get("warnings", []) or []))
        findings = scan(md, ctx=ctx)
        out = _neutral_record(source, "ok")
        out["findings"] = [f.to_dict() for f in findings]
        # R19-QA-3：逐条标注是否属已登记的预期形态。`by_rule` / `by_severity` 的计数
        # **不因标注而减少**——命中照样可见，改的只是下游 `prioritize` 给的处置。
        for fd in out["findings"]:
            fd["known_benign"] = known_benign(source, fd["rule"])
        out["by_rule"] = {n: sum(1 for f in findings if f.rule == n) for n in RULES}
        out["by_severity"] = {s: sum(1 for f in findings if f.severity == s)
                              for s in ("blocking", "major", "minor")}
        # cjk_space_collapse 的判定完全依赖源端标题真值；没有真值时必须显式跳过，
        # 不能把「没查」伪装成「无缺陷」。
        out["skipped_rules"] = ["cjk_space_collapse"] if not ref_headings else []
        # 产品 metrics["table_col"] 是 {"tables","rows","rate"}，与 attribute.py 的
        # `tcolc` 同口径只取 rate；整份 dict 交给下游会在数值比较处类型不符。
        tc = (res.metrics or {}).get("table_col")
        out["tcolc"] = tc.get("rate") if isinstance(tc, dict) else tc
        return out
    except Exception as exc:  # noqa: BLE001 —— 错误边界：逐样本失败不得中断批次
        return _error_record(source, exc)


def _cell(v) -> str:
    """Markdown 表格单元格：转义竖线、折叠换行。"""
    return str(v).replace("|", "\\|").replace("\n", " ")


def report_md(payload: dict) -> str:
    """人类可读报告：规则 × 命中数总表、跳过计数、逐样本命中。"""
    s = payload["summary"]
    records = payload["records"]
    skipped = [r for r in records if r.get("skipped_rules")]
    # 跳过原因按 skipped_rules **实际聚合**（规则名 + 份数），不写死某一条规则：
    # 将来新增依赖 ctx 的规则时，这行说明才不会失真。
    skip_by_rule = Counter(rn for r in records for rn in (r.get("skipped_rules") or []))
    skip_detail = "、".join(f"`{rn}` {c} 份" for rn, c in sorted(skip_by_rule.items()))
    skip_detail = skip_detail or "无"
    pdf_split = [(r["source"], r["by_rule"]["split_table"]) for r in records
                 if r["source"].startswith("pdf_") and r["by_rule"].get("split_table")]
    lines = [
        "# Doc2md 产物结构缺陷扫描报告（自动生成）", "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 样本：{s['n']} 总计 / {s['n_ok']} ok",
        f"- 跳过规则计数：{s['skipped']}（按规则：{skip_detail}；"
        f"跳过的规则不计入「无缺陷」）",
        "",
        "## 规则 × 命中数（跨样本）", "",
        "| 规则 | 严重度 | 命中 |", "|---|---|---|",
    ]
    for n in RULES:
        lines.append(f"| {n} | {SEVERITY[n]} | {s['by_rule'][n]} |")
    lines += ["", "| 严重度 | 命中 |", "|---|---|"]
    for sev in ("blocking", "major", "minor"):
        lines.append(f"| {sev} | {s['by_severity'][sev]} |")
    lines += [
        "",
        "## 信息层核对", "",
        f"- `skipped_rules` 非空：{len(skipped)} 份"
        + (f"（{', '.join(r['source'] for r in skipped)}）" if skipped else ""),
        "- `split_table` 在 pdf_* 上："
        + (f"{sum(n for _, n in pdf_split)} 处 / {len(pdf_split)} 份"
           f"（{', '.join(src for src, _ in pdf_split)}）" if pdf_split else "未命中"),
        f"- `silent_degrade`：{s['by_rule']['silent_degrade']}（期望 0，非 0 即新缺陷）",
        "- `split_table` 在合成语料上未命中是允许的：D02 难度腿才是它的证据",
        "",
        "## 逐样本", "",
        "| 样本 | 状态 | " + " | ".join(RULES) + " | skipped | tcolc |",
        "|---" * (len(RULES) + 4) + "|",
    ]
    for r in records:
        if r.get("status") != "ok":
            detail = r.get("error_code") or r.get("error") or ""
            lines.append(f"| {_cell(r['source'])} | {r.get('status')}（{_cell(detail)}） | "
                         + " | ".join("-" for _ in RULES) + " | - | - |")
            continue
        tc = r.get("tcolc")
        if isinstance(tc, dict):
            tc = tc.get("rate")
        tc_cell = "-" if tc is None else f"{tc:.4f}" if isinstance(tc, float) else _cell(tc)
        lines.append(
            f"| {_cell(r['source'])} | ok | "
            + " | ".join(str(r["by_rule"].get(n, 0)) for n in RULES)
            + f" | {_cell(','.join(r.get('skipped_rules') or []) or '-')} | {tc_cell} |")
    findings = [(r["source"], f) for r in records for f in r.get("findings", [])]
    lines += ["", f"## 命中明细（共 {len(findings)} 条）", "",
              "| 样本 | 规则 | 严重度 | 位置 | 证据 |", "|---|---|---|---|---|"]
    for src, f in findings[:200]:
        lines.append(f"| {_cell(src)} | {_cell(f['rule'])} | {_cell(f['severity'])} | "
                     f"{_cell(f['where'])} | {_cell(f['evidence'])} |")
    if len(findings) > 200:
        lines.append(f"| … | … | … | … | 余 {len(findings) - 200} 条见 JSON |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Doc2md 产物结构缺陷扫描（信息层）",
        epilog="运行结果恒 0（逐样本失败记为 status=rejected/error）；参数/清单错误退 2。")
    ap.add_argument("--only", default="", help="只跑名称含该子串的样本")
    ap.add_argument("--json", default=None, help="写 JSON 结果路径")
    ap.add_argument("--md", default=None, help="写 Markdown 报告路径")
    args = ap.parse_args()

    # 参数/清单错误是基础设施故障：致命（退 2），不混入逐样本 error 记录
    try:
        manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
        entries = [e for e in manifest if args.only in e["source"]] \
            if args.only else manifest
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("source"), str):
                raise ValueError(f"清单条目缺 source：{e!r}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"清单读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # `--only` 无匹配不是「全清白」：向 stderr 明说，退出码仍 0（信息层纪律）。
    if args.only and not entries:
        print(f"无匹配样本：--only {args.only!r} 未匹配清单中任何样本", file=sys.stderr)

    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="doc2md-defect-") as td:
        out_dir = Path(td) / "out"
        out_dir.mkdir()
        for e in entries:
            src = CORPUS / "sources" / e["source"]
            try:
                rec = scan_one(src, out_dir=out_dir)
            except Exception as exc:  # noqa: BLE001 —— scan_one 已兜底，再守一层
                rec = _error_record(src, exc)
            records.append(rec)
            n_hit = sum(rec["by_rule"].values())
            print(f"  {rec['source']:34s} {rec['status']:9s} hits={n_hit}"
                  f"{' skipped=' + ','.join(rec['skipped_rules']) if rec['skipped_rules'] else ''}")

    ok = [r for r in records if r.get("status") == "ok"]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "n": len(records), "n_ok": len(ok),
            "by_rule": {n: sum(r["by_rule"].get(n, 0) for r in records) for n in RULES},
            "by_severity": {s: sum(r.get("by_severity", {}).get(s, 0) for r in records)
                            for s in ("blocking", "major", "minor")},
            "skipped": sum(len(r.get("skipped_rules", [])) for r in records),
        },
        "records": records,     # records[i] 即 scan_one() 的返回值
    }

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON  -> {p}")

    print("\n" + "=" * 60)
    print("规则 × 命中数（跨样本）")
    for n in RULES:
        print(f"  {n:20s} {SEVERITY[n]:9s} {payload['summary']['by_rule'][n]}")
    print("-" * 60)
    print("  严重度 "
          + " / ".join(f"{s} {payload['summary']['by_severity'][s]}"
                       for s in ("blocking", "major", "minor")))
    print(f"  样本 {len(ok)} ok / {len(records) - len(ok)} non-ok（共 {len(records)}）｜"
          f"skipped {payload['summary']['skipped']}")

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report_md(payload), encoding="utf-8")
        print(f"Markdown -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
