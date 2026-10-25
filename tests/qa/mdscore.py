#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdscore.py —— Doc2md 转换效果的量化指标引擎（纯标准库、零第三方依赖）。

定位（与既有资产的关系，不得混淆）：
  - `doc2md/qa_eval.py`（O-06）提供 char_f1 / heading_pr / table_col，是**生产侧**
    轻量指标，真值来自 `tests/corpus/ideal/` 的标题标注。本模块是**评测侧**的
    完整指标体系，覆盖转换效果的全部可量化维度。
  - `tests/corpus/expected/` 是**程序产物基线**，对它算分只能监控回归漂移；
    `tests/corpus/ideal/` 是**人工理想标注**，对它算分才是质量真值。
    **双口径纪律**：本模块的 `score()` 必须显式声明参照系（`ref_kind`），
    两者不得混用、不得把基线分当质量分上报。

指标体系（八项 + 三项项目扩展）：

  ┌── 保真度（需真值，回答「跟原文对不对得上」）────────────────┐
  │ NED   文本保真率   1 − Levenshtein/max(len)，字符级            │
  │ BSF   结构保真率   块序列对齐后的 F1                           │
  │ MHS   标题层级率   标题匹配 F1 × 层级准确率                    │
  │ TEDS  表格保真率   树编辑距离相似度（本模块自实现，无依赖）    │
  │ NID   阅读顺序率   1 − indel距离/max(len)，块 ID 序列          │
  └───────────────────────────────────────────────────────────────┘
  ┌── 一致性（不需真值，回答「跟自家规范一不一致」）──────────────┐
  │ LINT  规范合规率   markdownlint 关键规则加权违规密度            │
  │ ASSET 资产完整率   链接/锚点可解析比例                          │
  │ IDEM  幂等率       同一输入 N 次输出哈希一致比例                │
  └───────────────────────────────────────────────────────────────┘
  ┌── 项目扩展（Doc2md 特有契约）────────────────────────────────┐
  │ RENDER 渲染红线    ~~ / 自造锚点 / 裸 HTML 等契约违规计数      │
  │ HONEST 降级诚实度  warnings 留痕率（降级是否显式暴露）          │
  │ TCOLC  表格列一致率 源行形众数一致占比（S3 row_shape）         │
  └───────────────────────────────────────────────────────────────┘

综合分 = 加权求和。权重按文档类型可调（见 WEIGHTS / PROFILES）。

用法：
    from tests.qa.mdscore import score
    r = score(actual_md, ref_md, ref_kind="ideal")
    r = score(actual_md, ref_kind="none")          # 只算一致性三项 + 项目扩展
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

__all__ = [
    "Block", "parse_blocks", "strip_front_matter",
    "levenshtein", "ned", "indel_distance", "nid", "normalize_text",
    "teds", "structure_f1", "heading_score", "reading_order_nid", "table_score",
    "lint_report", "asset_report", "idempotent_rate",
    "render_redlines", "score", "PROFILES",
]

# ============================================================================
# 0. 权重与文档画像
# ============================================================================

# 默认权重（通用档）。无真值时自动只取一致性侧并重新归一化。
WEIGHTS: dict[str, float] = {
    "text": 0.18,       # NED
    "structure": 0.18,  # BSF
    "heading": 0.10,    # MHS
    "table": 0.15,      # TEDS
    "order": 0.10,      # NID
    "lint_contract": 0.10,  # LINT（契约类；方言类只报不计分，见 LINT_CLASS）
    "asset": 0.07,      # ASSET
    "idempotent": 0.05,  # IDEM
    "render": 0.05,     # RENDER（项目扩展）
    "honest": 0.02,     # HONEST（项目扩展）
}

# 保真侧键（需要真值才出数）
FIDELITY_KEYS = ("text", "structure", "heading", "table", "order")
# 一致性侧键（无真值也能算）
CONSISTENCY_KEYS = ("lint_contract", "asset", "idempotent", "render", "honest")

# 文档画像：不同类型卡不同的项（参考资料「阈值怎么定」表的落地）
PROFILES: dict[str, dict[str, Any]] = {
    "generic": {
        "weights": WEIGHTS,
        "gates": {"lint_contract": 0.95, "asset": 1.0, "idempotent": 1.0, "render": 1.0},
    },
    "financial": {   # 财报 / 合同：数字敏感，表格与文本是命门
        "weights": {**WEIGHTS, "table": 0.26, "text": 0.26, "order": 0.06},
        "gates": {"table": 0.90, "text": 0.98, "lint_contract": 0.95, "idempotent": 1.0},
    },
    "academic": {    # 学术论文：多栏易翻车，阅读顺序是命门
        "weights": {**WEIGHTS, "order": 0.20, "table": 0.18, "text": 0.14},
        "gates": {"order": 0.90, "table": 0.85, "lint_contract": 0.95, "idempotent": 1.0},
    },
    "manual": {      # 产品手册 / 文档站：结构与标题是门面
        "weights": {**WEIGHTS, "structure": 0.26, "heading": 0.16, "asset": 0.10},
        "gates": {"structure": 0.95, "heading": 0.90, "lint_contract": 0.95, "idempotent": 1.0},
    },
    "batch": {       # 批量入库（无标注）：只看一致性
        "weights": {"lint_contract": 0.45, "asset": 0.30, "idempotent": 0.15, "render": 0.10},
        "gates": {"lint_contract": 0.95, "asset": 1.0, "idempotent": 1.0},
    },
}

# 判定「块匹配」的文本相似度阈值
BLOCK_MATCH_THRESHOLD = 0.80


# ============================================================================
# 1. 基础工具
# ============================================================================

def levenshtein(a: str, b: str) -> int:
    """经典编辑距离（插入/删除/替换各记 1）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def ned(a: str, b: str) -> float:
    """NED：归一化编辑距离相似度，1 − ED/max(len)。越大越好。"""
    m = max(len(a), len(b))
    return 1.0 if m == 0 else 1.0 - levenshtein(a, b) / m


def indel_distance(a: list, b: list) -> int:
    """只允许插入/删除的编辑距离（用于阅读顺序 NID）。"""
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i]
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                cur.append(prev[j - 1])
            else:
                cur.append(1 + min(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def nid(pred: list, gt: list) -> float:
    """NID：归一化 indel 相似度，1 − indel/max(len)。越大越好。"""
    m = max(len(pred), len(gt))
    return 1.0 if m == 0 else 1.0 - indel_distance(pred, gt) / m


def normalize_text(s: str) -> str:
    """文本归一化：消掉纯排版差异，只留语义差异。

    注意：全半角与大小写**不折算**——折算会掩盖真偏差
    （如全角数字被 S3 规整后漏报），与 qa_eval._norm 同口径。
    """
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00a0", " ")            # 不换行空格
    s = re.sub(r"[ \t]+", " ", s)           # 连续空白折叠
    s = re.sub(r"\n{2,}", "\n", s)          # 空行折叠
    return s.strip()


def strip_front_matter(md: str) -> tuple[str, str]:
    """剥离 YAML front-matter，返回 (front_matter, body)。

    Doc2md 的 FR-105 规定产物以 front-matter 开头；块级解析必须跳过它，
    否则 `---` 会被当成分隔线、YAML 键值会被当成正文段落，污染全部指标。
    """
    if not md.startswith("---\n"):
        return "", md
    end = md.find("\n---\n", 4)
    if end == -1:
        return "", md
    return md[:end + 5], md[end + 5:]


# ============================================================================
# 2. Markdown 块级解析（适配 Doc2md 输出）
# ============================================================================

@dataclass
class Block:
    type: str                      # heading/para/code/table/list/quote/image/hr/html
    level: int = 0                 # 仅 heading 有意义
    text: str = ""
    raw: str = ""
    meta: dict = field(default_factory=dict)


FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)\s*(\S*)")
ATX_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"\s{0,3}([-*_])(?:\s*\1){2,}\s*")


def parse_blocks(md: str) -> list[Block]:
    """块级解析：标题/段落/代码/表格/列表/引用/图片/分隔线/HTML 块。

    Doc2md 适配点：
      1. 先剥 front-matter（见 strip_front_matter）；
      2. tight list（O-04 后的连续列表项单换行）仍按「列表块」整体收，
         与 loose list 同型，避免间距差异被误判成结构差异；
      3. `<br>` 是 O-05 引入的单元格内段界，不参与块级切分。
    """
    _, md = strip_front_matter(md)
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        # 代码围栏
        m = FENCE_RE.match(line)
        if m:
            fence, lang = m.group(1), m.group(2)
            buf = [line]
            i += 1
            while i < n and not lines[i].strip().startswith(fence[0] * len(fence)):
                buf.append(lines[i])
                i += 1
            if i < n:
                buf.append(lines[i])
                i += 1
            body = "\n".join(buf[1:-1])
            blocks.append(Block("code", text=body, raw="\n".join(buf),
                                meta={"lang": lang or ""}))
            continue

        # Setext 标题（=== / ---）——Doc2md 只出 ATX，命中即异常，仍要识别
        if (i + 1 < n and line.strip()
                and re.fullmatch(r"\s{0,3}(=+|-+)\s*", lines[i + 1])):
            under = lines[i + 1].strip()[0]
            if under == "=" or blocks:
                blocks.append(Block("heading", level=1 if under == "=" else 2,
                                    text=line.strip(), raw=line + "\n" + lines[i + 1],
                                    meta={"setext": True}))
                i += 2
                continue

        # ATX 标题
        m = ATX_RE.match(line)
        if m:
            blocks.append(Block("heading", level=len(m.group(1)),
                                text=m.group(2).strip(), raw=line,
                                meta={"setext": False}))
            i += 1
            continue

        # 分隔线
        if HR_RE.fullmatch(line):
            blocks.append(Block("hr", raw=line))
            i += 1
            continue

        # 表格：当前行含 | 且下一行是分隔符行
        if ("|" in line and i + 1 < n
                and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i + 1])
                and "-" in lines[i + 1]):
            buf = [line, lines[i + 1]]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                buf.append(lines[i])
                i += 1
            blocks.append(Block("table", raw="\n".join(buf), text="\n".join(buf),
                                meta=_parse_table(buf)))
            continue

        # HTML 块（复杂表降级为 <table> 时的形态）
        if line.lstrip().startswith("<") and re.match(
                r"^\s*<(table|div|figure|details|br)\b", line, re.I):
            buf = []
            depth = 0
            while i < n and lines[i].strip():
                buf.append(lines[i])
                depth += len(re.findall(r"<(table|div|figure|details)\b", lines[i], re.I))
                depth -= len(re.findall(r"</(table|div|figure|details)>", lines[i], re.I))
                i += 1
                if depth <= 0:
                    break
            blocks.append(Block("html", text="\n".join(buf), raw="\n".join(buf)))
            continue

        # 引用
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            blocks.append(Block("quote", text="\n".join(buf), raw="\n".join(buf)))
            continue

        # 列表（tight / loose 同收）
        if re.match(r"^\s{0,3}([-*+]|\d+[.)])\s+", line):
            buf = []
            while i < n and (re.match(r"^\s{0,3}([-*+]|\d+[.)])\s+", lines[i])
                             or (lines[i].startswith((" ", "\t")) and lines[i].strip())
                             or not lines[i].strip()):
                if not lines[i].strip() and i + 1 < n and not re.match(
                        r"^\s{0,3}([-*+]|\d+[.)])\s+", lines[i + 1]):
                    break
                buf.append(lines[i])
                i += 1
            blocks.append(Block("list", text="\n".join(buf), raw="\n".join(buf)))
            continue

        # 独立图片段
        if re.fullmatch(r"\s*!\[[^\]]*\]\([^)]*\)\s*", line):
            blocks.append(Block("image", text=line.strip(), raw=line))
            i += 1
            continue

        # 普通段落（吃到空行或下一个块起点为止）
        buf = []
        while (i < n and lines[i].strip() and not FENCE_RE.match(lines[i])
                and not ATX_RE.match(lines[i])):
            buf.append(lines[i])
            i += 1
        if buf:
            blocks.append(Block("para", text="\n".join(buf), raw="\n".join(buf)))
        else:
            i += 1

    return blocks


def _split_row(r: str) -> list[str]:
    r = r.strip()
    if r.startswith("|"):
        r = r[1:]
    if r.endswith("|"):
        r = r[:-1]
    # 尊重转义管道 \|（S4 _escape_inline 的产物）
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", r)]


def _parse_table(rows: list[str]) -> dict:
    """解析 GFM 管道表 -> {header, aligns, body, ncol}"""
    header = _split_row(rows[0])
    aligns = [c.strip() for c in _split_row(rows[1])]
    body = [_split_row(r) for r in rows[2:]]
    ncol = max([len(header)] + [len(r) for r in body]) if (header or body) else 0
    return {"header": header, "aligns": aligns, "body": body, "ncol": ncol}


# ============================================================================
# 3. TEDS：表格树编辑距离相似度
# ============================================================================

@dataclass
class TNode:
    tag: str
    text: str = ""
    span: tuple = (1, 1)
    children: list = field(default_factory=list)

    def size(self) -> int:
        return 1 + sum(c.size() for c in self.children)


def table_to_tree(t: dict) -> TNode:
    root = TNode("table")
    if t.get("header"):
        root.children.append(TNode("tr", children=[TNode("th", text=c) for c in t["header"]]))
    for row in t.get("body", []):
        root.children.append(TNode("tr", children=[TNode("td", text=c) for c in row]))
    return root


def _seq_edit(A: list[TNode], B: list[TNode]) -> float:
    m, n = len(A), len(B)
    prev = [float(j) for j in range(n + 1)]
    for i in range(1, m + 1):
        cur = [float(i)]
        for j in range(1, n + 1):
            cur.append(min(prev[j] + 1.0,
                           cur[j - 1] + 1.0,
                           prev[j - 1] + _tree_dist(A[i - 1], B[j - 1])))
        prev = cur
    return prev[-1]


def _tree_dist(a: TNode, b: TNode) -> float:
    """近似树编辑距离。对 table/tr/td 这种浅层树足够准确。"""
    if a.tag != b.tag:
        return float(max(a.size(), b.size()))
    if not a.children and not b.children:
        span_cost = 0.0 if a.span == b.span else 1.0
        content_cost = 1.0 - ned(a.text, b.text)
        return span_cost + content_cost
    if not a.children or not b.children:
        return float(max(a.size(), b.size()))
    return _seq_edit(a.children, b.children)


def teds(pred: dict, gt: dict) -> float:
    """TEDS = 1 − TreeEditDist / max(|T_pred|, |T_gt|)。

    已知边界（参考资料 §二）：`<tr>` 在树中比 `<td>` 高一层，**丢行惩罚重于丢列**
    （同表缺行 0.8125 / 缺列 0.84375）。金融表格优化时留意这个偏差，
    必要时用 TCOLC / GriTS 思路交叉验证。
    """
    tp, tg = table_to_tree(pred), table_to_tree(gt)
    d = _tree_dist(tp, tg)
    m = max(tp.size(), tg.size())
    return 1.0 if m == 0 else max(0.0, 1.0 - d / m)


# ============================================================================
# 4. 块序列对齐 -> BSF / MHS / NID
# ============================================================================

def block_sim(a: Block, b: Block) -> float:
    if a.type != b.type:
        return 0.0
    if a.type == "table":
        return teds(a.meta, b.meta)
    s = ned(normalize_text(a.text), normalize_text(b.text))
    if a.type == "heading" and a.level != b.level:
        s *= 0.5                       # 层级错了降权但不直接判死
    return s


def align_blocks(pred: list[Block], gt: list[Block], thr: float = BLOCK_MATCH_THRESHOLD):
    """块级对齐：SequenceMatcher 为主，类型+文本近似的补配为辅。"""
    key_a = [normalize_text(b.text)[:200] for b in pred]
    key_b = [normalize_text(b.text)[:200] for b in gt]
    sm = SequenceMatcher(None, key_a, key_b, autojunk=False)

    pairs, matched_a, matched_b = [], set(), set()
    for i, j, size in sm.get_matching_blocks():
        for k in range(size):
            ai, bj = i + k, j + k
            if block_sim(pred[ai], gt[bj]) >= thr or size > 1:
                pairs.append((ai, bj))
                matched_a.add(ai)
                matched_b.add(bj)

    for ai in range(len(pred)):
        if ai in matched_a:
            continue
        for bj in range(len(gt)):
            if bj in matched_b:
                continue
            if block_sim(pred[ai], gt[bj]) >= thr:
                pairs.append((ai, bj))
                matched_a.add(ai)
                matched_b.add(bj)
                break

    unmatched_a = [i for i in range(len(pred)) if i not in matched_a]
    unmatched_b = [j for j in range(len(gt)) if j not in matched_b]
    return pairs, unmatched_a, unmatched_b


def structure_f1(pred: list[Block], gt: list[Block]) -> tuple[float, dict]:
    """BSF：块序列对齐后的 F1。"""
    pairs, ua, ub = align_blocks(pred, gt)
    m = len(pairs)
    # 边界：两侧皆空 = 完美（与 char_f1 / heading_f1 同口径）。
    # pdf_10 这类「骨架为空」的样本，产物也零骨架即满分，不能判 0。
    if not pred and not gt:
        return 1.0, {"matched": 0, "precision": 1.0, "recall": 1.0,
                     "extra_blocks": 0, "missing_blocks": 0,
                     "n_pred": 0, "n_gt": 0}
    p = m / len(pred) if pred else 0.0
    r = m / len(gt) if gt else 0.0
    f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
    return f1, {"matched": m, "precision": round(p, 4), "recall": round(r, 4),
                "extra_blocks": len(ua), "missing_blocks": len(ub),
                "n_pred": len(pred), "n_gt": len(gt)}


def heading_score(pred: list[Block], gt: list[Block]) -> tuple[float, dict]:
    """MHS：标题匹配 F1 × 层级准确率。"""
    ph = [b for b in pred if b.type == "heading"]
    gh = [b for b in gt if b.type == "heading"]
    if not ph and not gh:
        return 1.0, {"n_pred": 0, "n_gt": 0, "matched": 0,
                     "level_exact": 0, "level_accuracy": 1.0}
    pairs, _, _ = align_blocks(ph, gh, thr=0.6)
    m = len(pairs)
    exact = sum(1 for a, b in pairs if ph[a].level == gh[b].level)
    p = m / len(ph) if ph else 0.0
    r = m / len(gh) if gh else 0.0
    f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
    lvl = exact / m if m else 0.0
    return f1 * lvl, {"n_pred": len(ph), "n_gt": len(gh), "matched": m,
                      "level_exact": exact, "level_accuracy": round(lvl, 4),
                      "precision": round(p, 4), "recall": round(r, 4)}


def reading_order_nid(pred: list[Block], gt: list[Block]) -> float:
    """NID：把块序列映射成 ID 序列后算归一化 indel 距离。"""
    pairs, _, _ = align_blocks(pred, gt)
    id_of_gt = {b: f"g{b}" for b in range(len(gt))}
    seq_gt = [id_of_gt[j] for j in range(len(gt))]
    map_pred = {a: id_of_gt[b] for a, b in pairs}
    seq_pred = [map_pred.get(i, f"x{i}") for i in range(len(pred))]
    return nid(seq_pred, seq_gt)


def table_score(pred: list[Block], gt: list[Block]) -> tuple[float, dict]:
    """表格 TEDS：漏检的表按 0 分计入（区分「没找到」与「找到了但错了」）。"""
    pt = [b for b in pred if b.type == "table"]
    gtb = [b for b in gt if b.type == "table"]
    if not pt and not gtb:
        return 1.0, {"n_pred": 0, "n_gt": 0, "matched": 0,
                     "missed": 0, "avg_found_only": None}
    pairs, _, _ = align_blocks(pt, gtb, thr=0.3)
    scores = [teds(pt[a].meta, gtb[b].meta) for a, b in pairs]
    missed = len(gtb) - len(pairs)
    found_only = round(sum(scores) / len(scores), 4) if scores else None
    total = sum(scores) / len(gtb) if gtb else 0.0
    return total, {"n_pred": len(pt), "n_gt": len(gtb), "matched": len(pairs),
                   "missed": missed, "avg_found_only": found_only}


# ============================================================================
# 5. LINT：规范合规（无真值也能算）
# ============================================================================

# 规则严重度：阻断级（结构/可访问性受损）权重高，排版级权重低。
# 选取原则：**单规则命中率比总分更有诊断价值**——每条都直接对应上游转换器的
# 一个具体缺陷。
SEVERITY: dict[str, float] = {
    "MD001": 2.0,   # 标题跳级        -> S3 层级判定错误
    "MD003": 2.0,   # Setext 标题     -> S4 渲染方言不归一
    "MD040": 2.0,   # 代码块缺语言    -> S3 代码块识别无 lang
    "MD045": 2.0,   # 图片缺 alt      -> 资产侧 alt 未填
    "MD055": 1.5,   # 表格管道破损    -> S4 表格渲染缺陷
    "MD056": 1.5,   # 表格列数不一致  -> S3 行形判定 / 补齐缺陷
    "MD060": 0.5,   # 分隔符未对齐    -> 纯排版，且中文按 CJK 宽度算必然争议
    "MD022": 0.5,   # 标题前后空行
    "MD031": 0.5,   # 围栏前后空行
    "MD026": 0.5,   # 标题末尾标点
    "MD009": 0.3,   # 行尾空格
    "MD012": 0.3,   # 连续空行
    "MD047": 0.3,   # 末尾换行
    "MD025": 1.0,   # 多个顶级标题
}
LINT_PENALTY = 5.0      # 加权违规率 -> 扣分的放大系数

#: 规则分类——**这一层比严重度更重要**。
#:   契约类（contract）：命中即指向上游转换器的一个具体缺陷，是质量信号；
#:   方言类（dialect）：只是 Markdown 风格偏好，源文档本就可能如此，
#:                      拿它扣分会惩罚「忠实还原」。典型是 MD025——
#:                      源文档有 39 个章级标题时产物就该有 39 个 H1，
#:                      「一个文档只能有一个 H1」是文档站规范，不是转换规范。
LINT_CLASS: dict[str, str] = {
    "MD001": "contract",   # 标题跳级      -> S3 层级判定
    "MD003": "contract",   # Setext 标题   -> S4 方言不归一
    "MD040": "contract",   # 代码块缺语言  -> S3 未带 lang
    "MD045": "contract",   # 图片缺 alt
    "MD055": "contract",   # 表格管道破损
    "MD056": "contract",   # 表格列数不一致 -> S3 行形/补齐
    "MD012": "contract",   # 连续空行
    "MD047": "contract",   # 末尾换行
    "MD009": "contract",   # 行尾空格
    "MD025": "dialect",    # 多个顶级标题  -> 忠实还原时必然命中
    "MD060": "dialect",    # 分隔符未对齐  -> CJK 宽度口径争议
    "MD022": "dialect",    # 标题前后空行
    "MD031": "dialect",    # 围栏前后空行
    "MD026": "dialect",    # 标题末尾标点  -> 源标题自带标点时应保留
}

CONTRACT_RULES = {r for r, c in LINT_CLASS.items() if c == "contract"}


def lint_report(md: str) -> dict:
    """markdownlint 关键规则子集的轻量实现，按规则 ID 分组返回违规数。

    Doc2md 适配：front-matter 不参与判定；`<br>`（O-05）不视为违规。
    """
    _, md = strip_front_matter(md)
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    v: dict[str, int] = {}
    n = len(lines)

    def hit(rule: str, k: int = 1):
        v[rule] = v.get(rule, 0) + k

    in_fence = False
    fence_tok = ""
    prev_level = 0
    h1_count = 0
    table_ncol: int | None = None

    for idx, ln in enumerate(lines):
        fm = FENCE_RE.match(ln)
        if fm:
            if not in_fence:
                in_fence, fence_tok = True, fm.group(1)
                if not fm.group(2):
                    hit("MD040")
                if idx > 0 and lines[idx - 1].strip():
                    hit("MD031")
            elif ln.strip().startswith(fence_tok[0] * len(fence_tok)):
                in_fence = False
                if idx + 1 < n and lines[idx + 1].strip():
                    hit("MD031")
            continue
        if in_fence:
            continue

        m = ATX_RE.match(ln)
        if m:
            lvl = len(m.group(1))
            if lvl == 1:
                h1_count += 1
            if prev_level and lvl > prev_level + 1:
                hit("MD001")
            prev_level = lvl
            if re.search(r"[。．.,，;；:：!！?？]$", m.group(2)):
                hit("MD026")
            if idx > 0 and lines[idx - 1].strip():
                hit("MD022")
            if idx + 1 < n and lines[idx + 1].strip() and not ATX_RE.match(lines[idx + 1]):
                hit("MD022")
            table_ncol = None
            continue

        if HR_RE.fullmatch(ln) and ln.strip() and idx == 0:
            # 剥 front-matter 后首行若为 hr：异常形态，不计 Setext
            pass

        # 表格行
        if ln.strip().startswith("|") and "|" in ln:
            cells = _split_row(ln)
            is_sep = bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", ln)) and "-" in ln
            if not is_sep:
                if table_ncol is None:
                    table_ncol = len(cells)
                elif len(cells) != table_ncol:
                    hit("MD056")
            if not re.fullmatch(r"\s*\|\s.*\s\|\s*", ln):
                hit("MD055")
            if is_sep and not re.search(r"\|\s*:?-{3,}:?\s*\|", ln):
                hit("MD060")
            continue
        table_ncol = None

        # 图片 alt
        for im in re.finditer(r"!\[([^\]]*)\]\(([^)]*)\)", ln):
            if not im.group(1).strip():
                hit("MD045")

        if re.search(r"[ \t]+$", ln):
            hit("MD009")

    if h1_count > 1:
        hit("MD025", h1_count - 1)
    if n and lines[-1] == "" and n >= 2 and lines[-2] == "":
        hit("MD012")
    if n and lines[-1] != "":
        hit("MD047")

    return v


def _lint_score(v: dict[str, int], nlines: int, *, contract_only: bool = False) -> float:
    """加权违规率 -> 合规分。contract_only=True 时只算契约类规则。"""
    items = ((r, c) for r, c in v.items()
             if (not contract_only) or r in CONTRACT_RULES)
    weighted = sum(SEVERITY.get(r, 1.0) * c for r, c in items)
    rate = weighted / max(1, nlines)
    return max(0.0, 1.0 - rate * LINT_PENALTY)


# ============================================================================
# 6. ASSET：资产与链接完整性
# ============================================================================

def _slugify_heading(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[`*_]", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", t)
    return re.sub(r"\s+", "-", t.strip())


def asset_report(md: str, base_dir: str = ".") -> dict:
    """图片/内链可解析率。

    Doc2md 适配：图片与 OCR 是明确非目标（§1.2），故图片引用常态为零，
    此处的重点是**内部锚点死链**——FR-107 判定「内部链接一律纯文本、不造
    `](#dest)` 死链」，一旦出现即为契约违规，必须零容忍。
    """
    _, body = strip_front_matter(md)
    anchors = {_slugify_heading(m.group(1))
               for m in re.finditer(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", body, re.M)}

    total = ok = 0
    broken: list[str] = []

    for l in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body):
        if l.startswith(("http://", "https://", "data:")):
            continue
        total += 1
        if os.path.exists(os.path.join(base_dir, l.split("#")[0])):
            ok += 1
        else:
            broken.append(l)

    for a in re.findall(r"\]\(#([^)]+)\)", body):
        total += 1
        if a.lower() in anchors:
            ok += 1
        else:
            broken.append("#" + a)

    return {"total": total, "resolved": ok,
            "rate": 1.0 if total == 0 else ok / total,
            "broken": broken[:10]}


# ============================================================================
# 7. IDEM：幂等率
# ============================================================================

def idempotent_rate(texts: list[str]) -> float:
    """多次转换输出哈希一致的比例。目标恒为 1.0——它不是质量分，是确定性开关。"""
    if len(texts) < 2:
        return 1.0
    hs = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    return sum(1 for h in hs if h == hs[0]) / len(hs)


# ============================================================================
# 8. 项目扩展：RENDER 渲染红线 / HONEST 降级诚实度
# ============================================================================

# 渲染红线：S4 契约明令禁止出现在正文中的形态
RENDER_REDLINES: list[tuple[str, re.Pattern]] = [
    ("strike", re.compile(r"(?<!\S)~~(?!\s)(.+?)(?<!\s)~~(?!\S)")),   # FR-108 默认关
    ("self_anchor", re.compile(r"\]\(#")),                             # FR-107 不造死链
    ("raw_script", re.compile(r"<script", re.I)),                      # 注入防线
    ("raw_iframe", re.compile(r"<iframe", re.I)),
    ("unclosed_fence", re.compile(r"^(```|~~~)", re.M)),               # 由配对检查兜底
]


def render_redlines(md: str, *, strike_enabled: bool = False) -> dict:
    """渲染红线命中检查。返回 {违规类型: 命中数} 与 0/1 分。

    `unclosed_fence` 用奇偶配对判定，不靠正则单条计数。
    """
    _, body = strip_front_matter(md)
    hits: dict[str, int] = {}
    for name, pat in RENDER_REDLINES:
        if name == "strike" and strike_enabled:
            continue
        if name == "unclosed_fence":
            continue
        c = len(pat.findall(body))
        if c:
            hits[name] = c

    # 配对：相同围栏符号成对闭合；按栈判定
    stack: list[str] = []
    unclosed = 0
    for ln in body.split("\n"):
        m = re.match(r"^\s{0,3}(```|~~~)", ln)
        if not m:
            continue
        tok = m.group(1)[0]
        if stack and stack[-1] == tok:
            stack.pop()
        else:
            stack.append(tok)
    unclosed = len(stack)
    if unclosed:
        hits["unclosed_fence"] = unclosed

    return {"hits": hits, "score": 0.0 if hits else 1.0}


def honesty_score(md: str, warnings: list[str] | None) -> dict:
    """降级诚实度：发生降级时是否显式留痕。

    C-29 / O-05 的教训——「诚实暴露而非静默降级」。此处只做**结构对账**：
    产物里出现了降级形态（HTML 表格块 / 拍平证据）却没有对应 warning，
    即为静默降级。
    """
    _, body = strip_front_matter(md)
    blocks = parse_blocks(md)
    html_tables = sum(1 for b in blocks if b.type == "html")
    warnings = warnings or []
    degraded = html_tables > 0
    flagged = any(w in ("W1043", "W1044", "W1045", "W1046") for w in warnings)
    if not degraded:
        return {"degraded": False, "flagged": flagged, "score": 1.0,
                "html_table_blocks": 0}
    return {"degraded": True, "flagged": flagged, "score": 1.0 if flagged else 0.0,
            "html_table_blocks": html_tables}


# ============================================================================
# 9. 汇总
# ============================================================================

def _disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def score(md: str, ref: str | None = None, *, ref_kind: str = "ideal",
          base_dir: str = ".", repeats: list[str] | None = None,
          warnings: list[str] | None = None, profile: str = "generic",
          strike_enabled: bool = False, tcolc: float | None = None) -> dict:
    """主入口。

    参数：
      md        实际转换产物（含 front-matter 的完整 content.md）
      ref       参照 Markdown。ref_kind="ideal" 时是人工理想标注；
                "baseline" 时是 expected/ 程序基线（只能用于回归漂移监控）
      ref_kind  "ideal" | "baseline" | "none"
      repeats   同一输入的多次转换结果（算幂等率）
      warnings  转换产生的告警码列表（算降级诚实度）
      profile   文档画像，见 PROFILES
      tcolc     S3 表格列一致率（由调用方从 IR 传入）
    """
    prof = PROFILES.get(profile, PROFILES["generic"])
    weights = dict(prof["weights"])

    pred = parse_blocks(md)
    out: dict[str, Any] = {"ref_kind": ref_kind, "profile": profile,
                           "scores": {}, "detail": {}}

    # --- 一致性侧（无真值也能算）---
    lint = lint_report(md)
    nlines = max(1, md.count("\n") + 1)
    out["scores"]["lint"] = round(_lint_score(lint, nlines), 4)
    out["scores"]["lint_contract"] = round(_lint_score(lint, nlines, contract_only=True), 4)
    out["detail"]["lint"] = {
        "raw_violations": sum(lint.values()),
        "weighted_violations": round(sum(SEVERITY.get(r, 1.0) * c
                                         for r, c in lint.items()), 2),
        "violations_per_1k_lines": round(sum(lint.values()) / nlines * 1000, 2),
        "by_rule": lint,
        "by_class": {
            "contract": {r: c for r, c in lint.items() if r in CONTRACT_RULES},
            "dialect": {r: c for r, c in lint.items() if r not in CONTRACT_RULES},
        },
    }

    asset = asset_report(md, base_dir)
    out["scores"]["asset"] = round(asset["rate"], 4)
    out["detail"]["asset"] = asset

    out["scores"]["idempotent"] = round(idempotent_rate(repeats or [md]), 4)
    out["detail"]["idempotent"] = {"n_runs": len(repeats) if repeats else 1}

    rend = render_redlines(md, strike_enabled=strike_enabled)
    out["scores"]["render"] = rend["score"]
    out["detail"]["render"] = rend

    hon = honesty_score(md, warnings)
    out["scores"]["honest"] = hon["score"]
    out["detail"]["honest"] = hon

    if tcolc is not None:
        out["scores"]["tcolc"] = round(tcolc, 4)
        out["detail"]["tcolc"] = {"rate": tcolc}

    # --- 保真侧（需真值）---
    if ref:
        gt = parse_blocks(ref)
        out["scores"]["text"] = round(ned(normalize_text(md), normalize_text(ref)), 4)
        f1, d = structure_f1(pred, gt)
        out["scores"]["structure"] = round(f1, 4)
        out["detail"]["structure"] = d
        mhs, d = heading_score(pred, gt)
        out["scores"]["heading"] = round(mhs, 4)
        out["detail"]["heading"] = d
        ts, d = table_score(pred, gt)
        out["scores"]["table"] = round(ts, 4)
        out["detail"]["table"] = d
        out["scores"]["order"] = round(reading_order_nid(pred, gt), 4)
        out["detail"]["block_count"] = {"pred": len(pred), "gt": len(gt)}
    else:
        weights = {k: v for k, v in weights.items() if k in CONSISTENCY_KEYS}

    tot = sum(weights.values())
    overall = sum(out["scores"][k] * v for k, v in weights.items()
                  if k in out["scores"]) / tot
    out["overall"] = round(overall, 4)
    out["weights"] = {k: round(v / tot, 3) for k, v in weights.items()}

    # --- 门禁判定 ---
    gates = prof["gates"]
    failed = []
    for k, thr in gates.items():
        got = out["scores"].get(k)
        if got is not None and got < thr:
            failed.append(f"{k}={got:.3f} < {thr}")
    out["gate"] = {"profile": profile, "thresholds": gates,
                   "failed": failed, "passed": not failed}
    return out
