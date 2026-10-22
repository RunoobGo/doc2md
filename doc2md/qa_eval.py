"""O-06（QA-D2M-2026-10-21 / C-31 / R-4）：结构质量指标——纯函数、零依赖。

背景：`convert._metrics` 原有的 `f1 = chars/len(md)` 是**自啮合**指标（C-31）
——它只度量「md 里非空白占比」，与「转换得好不好」无关。本模块提供三类
有真值（ground truth）或确定性判据的指标，供 `_metrics` 与 QA 工具面调用：

- `char_f1`：字符级 P/R/F1，参照系由调用方给（expected 基线 / 人工理想标注）。
  **双口径纪律**：对 expected 基线算 F1 会随基线重烤自啮合，只用于回归
  漂移监控；对 `tests/corpus/ideal/` 的人工标注算 F1 才是质量真值——
  两者必须分开报告，不得混用（见 ideal/README.md）。
- `heading_pr`：标题 P/R。DOCX 的 ground truth 是 S1 的 `Line.level_hint`
  （`Heading N` 样式显式层级，非启发式）；PDF 无真值——调用方必须传
  `ref=[]` 或不调用，**不得**用「编号连续性代理」冒充真值（宁漏勿误）。
- `table_col_consistency`：表格列一致率。判据确定性：GFM 行单元格数
  等于该表众数列数的行占比（S3 的 `_gfm_table` 本就把短行补齐到最多列，
  补齐率越高说明源表格越不规整——拍平/解析噪声的直接信号）。

全部函数对空输入返回定义良好的零值/空值，不抛异常（gate 边界红线依据）。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NORM_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """标题匹配用归一化：去全部空白。全半角/大小写不折算——
    折算会掩盖真偏差（如全角数字被 S3 规整后漏报）。"""
    return _NORM_RE.sub("", text or "")


def char_f1(pred: str, ref: str) -> dict:
    """字符级 P/R/F1（剥空白后 SequenceMatcher 对齐）。

    matched 取所有匹配块长度和（最长公共子序列的贪心近似）；
    pred/ref 任一为空时：两者皆空 = 完美（1.0），单侧空 = 0.0。
    """
    p = _norm(pred)
    r = _norm(ref)
    if not p and not r:
        return {"p": 1.0, "r": 1.0, "f1": 1.0}
    if not p or not r:
        return {"p": 0.0, "r": 0.0, "f1": 0.0}
    matched = sum(m.size for m in SequenceMatcher(None, p, r, autojunk=False)
                  .get_matching_blocks())
    prec = matched / len(p)
    rec = matched / len(r)
    f1 = 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
    return {"p": round(prec, 4), "r": round(rec, 4), "f1": round(f1, 4)}


def heading_pr(pred: list[dict], ref: list[dict]) -> dict:
    """标题 P/R：pred/ref 各为 [{"text": str, "level": int|None}, ...]。

    匹配判据：归一化文本相等**且**层级一致（level 两侧都非 None 才比层级，
    单侧 None 只比文本——PDF 预测侧层级是字号启发式，不与 DOCX 显式层级
    硬碰）。多重匹配按可配对计数取 min（multiset 语义，重复标题不虚增）。
    返回 {"p", "r", "f1", "pred", "ref", "matched"}；ref 为空时各项 None
    （无真值 ≠ 满分，调用方不得把 None 当 1.0 聚合）。
    """
    from collections import Counter

    def key(item: dict) -> tuple:
        t = _norm(item.get("text", ""))
        lv = item.get("level")
        return (t, lv) if (lv is not None
                           and item.get("level_is_explicit", True)) else (t, None)

    pc = Counter(key(x) for x in pred)
    rc = Counter(key(x) for x in ref)
    matched = sum((pc & rc).values())
    tp, tr = sum(pc.values()), sum(rc.values())
    if tr == 0:
        return {"p": None, "r": None, "f1": None,
                "pred": tp, "ref": 0, "matched": 0}
    prec = matched / tp if tp else 0.0
    rec = matched / tr
    f1 = 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
    return {"p": round(prec, 4), "r": round(rec, 4), "f1": round(f1, 4),
            "pred": tp, "ref": tr, "matched": matched}


def pred_headings(blocks: list) -> list[dict]:
    """IR blocks → `heading_pr` 的 pred 形态（[{"text", "level"}, ...]）。

    只取 type=="heading" 的块；text 去掉渲染用 `#` 前缀不必要——S3 落库的
    heading 块 text 本就是标题文本（`#` 只在 S4 渲染时加）。
    """
    return [{"text": getattr(b, "text", "") or "",
             "level": getattr(b, "level", None)}
            for b in blocks if getattr(b, "type", "") == "heading"]


def table_col_consistency(blocks: list) -> dict:
    """表格列一致率：每张表的「众数列数行占比」（补齐前的**源行形**），全表聚合。

    判据来自 S3 落在表格块上的瞬态 `row_shape`（补齐前每行实义列数）——
    渲染文本里 S3 已把短行补齐到最多列，补齐空位与实义空单元格在文本上
    不可区分，列一致率必须看原始行形。无 `row_shape` 的表（反序列化块 /
    手造块）跳过：无真值不出数（宁漏勿误）。

    返回 {"tables": 表数, "rows": 计入的数据行总数, "rate": 一致率}；
    无可计行时 rate=None（无数据 ≠ 满分）。
    """
    tables = [b for b in blocks if getattr(b, "type", "") == "table"]
    total_rows = 0
    consistent = 0
    for b in tables:
        shape = getattr(b, "row_shape", None)
        if not shape or len(shape) < 2:     # 至少表头 + 1 数据行才有「一致」可言
            continue
        body = shape[1:]
        from collections import Counter
        modal = Counter(body).most_common(1)[0][0]
        total_rows += len(body)
        consistent += sum(1 for c in body if c == modal)
    rate = None if not total_rows else round(consistent / total_rows, 4)
    return {"tables": len(tables), "rows": total_rows, "rate": rate}
