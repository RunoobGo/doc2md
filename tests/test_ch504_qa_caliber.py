r"""CH-504（第十九轮）R19-QA-1 / R19-QA-2 / R19-QA-3：QA 判定面口径红线。

三条同属一个病灶：**判定层的口径写在了查无此物或已被否掉的依据上**，于是报告每轮都
产出无人能施工的条目，而真正的发布阻断项（B-43 / B-44）反而不在单子里。

R19-QA-1（MD040 归档）——`mdscore.LINT_CLASS` 把 MD040 记作 `contract`，注释写「S3
未带 lang」，权威引作 `MARKDOWN_SPEC`（仓库里从来没有这个符号）。但 PDF / DOCX 都不
携带代码语言元数据、`ir.model.Block` 也没有 `lang` 字段——S4 无从渲染一个上游不存在的
值，裸围栏是**忠实还原**，正落在 dialect 档自己写下的判据里。改档前本机实测：两个代码
样本 `lint_contract` = 0.375 / 0.167 对门禁 0.95，构成本轮**唯一**的 `blocking=True` P0；
归因层把 0.226226 总损失里的 0.177842（**78.6%**）记到 `S4_render` 头上并判 `major`。
改档后同一套腿实测：`gate_failures=[]`、`lint_contract` 宏平均 1.0、`S4_render` 归因
0.000000、平均 total_gap 0.010283 → 0.002304。

本文件钉住三件事，缺一件修复就会退化：
① **可见性没有下降**——MD040 仍被 `lint_report` 命中、仍计入 dialect、`defect_scan`
   的 `fence_no_lang` 规则与 `test_ch503_qa_face.py` 的冻结命中表继续盯着它；
② **档位与产品能力绑定**——`Block` 有 `lang` 字段 ⟺ MD040 属 contract。这条是双向的：
   产品长出 lang 能力而不改档要红，产品没有 lang 能力却改回 contract（即本轮缺陷本身）
   也要红。写成双向是为了避免「前件恒假 ⇒ 断言恒真」的空跑形态（O-g）；
③ **改档不改判级**——known_benign 只动处置，不动 impact / frequency / score / priority
   （与「fixability 不进分档」同一条纪律，O-f：判定不看修复代价）。
"""
from __future__ import annotations

import dataclasses

import pytest

from doc2md.ir.model import Block
from tests.qa import defect_scan as ds
from tests.qa import mdscore
from tests.qa import prioritize as pr

#: 最小 §12.7：只为让 `release_blocking` / `next_free_b` 有确定输入。
_DP = ("### 12.7 未验证项（诚实标注）\n\n"
       "| B-43 | D02 跨页表格 | **仅登记 · 发布阻断**：说明 |\n"
       "| B-45 | D08 序号口径 | **仅登记**：说明 |\n\n"
       "### 12.8 教训条目\n")

_BARE_FENCE = "```\nprint(1)\n```\n"


# --- R19-QA-1：MD040 归档 ----------------------------------------------------
@pytest.mark.gate
def test_md040_class_tracks_product_lang_capability():
    """档位 ⟺ 产品能力，双向。

    今天 `Block` 无 `lang`、MD040 属 dialect，两侧同为 False 故断言成立；任一侧单独
    变动都转红——包括「把档位改回 contract」这个**本轮缺陷本身**，所以它不是空跑。
    """
    has_lang = "lang" in {f.name for f in dataclasses.fields(Block)}
    is_contract = mdscore.LINT_CLASS["MD040"] == "contract"
    assert has_lang == is_contract, (
        f"MD040 归档与产品能力脱钩：Block 有 lang 字段={has_lang}，"
        f"LINT_CLASS['MD040']='{mdscore.LINT_CLASS['MD040']}'。"
        "S4 渲染不出上游不存在的值——没有 lang 能力时裸围栏是忠实还原（dialect），"
        "长出 lang 能力后它就是可修的契约缺口（contract）。两侧必须同时改。")


@pytest.mark.gate
def test_md040_stays_visible_after_reclass():
    """改档不等于不查：命中、dialect 记账、契约集合三处一起钉。"""
    v = mdscore.lint_report(_BARE_FENCE)
    assert v.get("MD040") == 1, v          # 仍然命中（改的是分类，不是探测）
    assert "MD040" not in mdscore.CONTRACT_RULES
    assert mdscore.LINT_CLASS["MD040"] == "dialect"
    # 契约分不受方言违规影响；方言分必须仍然扣分——否则「只报不计分」退化成「不报」
    n = _BARE_FENCE.count("\n") + 1
    assert mdscore._lint_score(v, n, contract_only=True) == 1.0
    assert mdscore._lint_score(v, n) < 1.0
    assert mdscore.MD040_DIALECT_REASON.strip(), "改档理由被清空——依据再次变得不可核"


@pytest.mark.gate
def test_fence_no_lang_rule_still_fires_and_cites_dialect():
    """`defect_scan` 侧的探测与证据串：命中不减，且不得再自称契约类。"""
    hits = ds.r_fence_no_lang(_BARE_FENCE, mdscore.parse_blocks(_BARE_FENCE), None)
    assert len(hits) == 1 and hits[0].rule == "fence_no_lang"
    ev = hits[0].evidence
    assert "契约" not in ev, f"证据串仍自称契约类，与 LINT_CLASS 分叉：{ev}"
    assert "dialect" in ev and mdscore.MD040_DIALECT_REASON in ev, ev
    # 自检对（正向命中 / 干净沉默）是 CH-501 的既有红线，这里只钉干净腿不被本改动打破
    assert ds.r_fence_no_lang("```python\nprint(1)\n```\n",
                              mdscore.parse_blocks("```python\nprint(1)\n```\n"),
                              None) == []


# --- R19-QA-2：duplicate_anchor 的证据串 -------------------------------------
def _heads(n: int, text: str = "1.1 章节说明") -> list:
    return [type("B", (), {"type": "heading", "text": text})() for _ in range(n)]


@pytest.mark.gate
def test_duplicate_anchor_evidence_reports_multiplicity_not_a_retracted_cause():
    """证据串必须给出**重数**，且不得断言已被否掉的成因。

    原实现两处都错：把第十八轮已认定为「语料设计」的形态写死成「TOC 缓存结果被当正文
    标题的典型形态」（每轮把工程师指向 B-48 / D09），并把逐次命中列表截成 `texts[:5]`
    ——40 个同名标题截 5 条只是把同一字符串抄五遍，真正的信息量（40）看不见。
    """
    f = ds.r_duplicate_anchor("", _heads(40), None)
    assert len(f) == 1 and f[0].rule == "duplicate_anchor"
    ev = f[0].evidence
    assert "TOC 缓存结果被当正文标题" not in ev, f"已作废的成因被写回证据串：{ev}"
    assert "40" in ev, f"重数没有出现在证据串里：{ev}"
    assert ev.count("1.1 章节说明") == 1, f"仍在逐次重复同一标题：{ev}"


@pytest.mark.gate
def test_duplicate_anchor_truncation_is_announced():
    """截断必须留痕：>5 种重复标题时显式报出未列数量（与 `_join_capped` 同纪律）。"""
    blocks = [b for i in range(7) for b in _heads(2, f"第 {i} 章 总则")]
    ev = ds.r_duplicate_anchor("", blocks, None)[0].evidence
    assert "另有 2 种未列" in ev, ev
    # 恰好 5 种时不该出现「另有」——否则截断标记本身成了噪声
    blocks5 = [b for i in range(5) for b in _heads(2, f"第 {i} 章 总则")]
    assert "另有" not in ds.r_duplicate_anchor("", blocks5, None)[0].evidence


# --- R19-QA-3：已知非缺陷登记的单一事实源 -------------------------------------
@pytest.mark.gate
def test_known_benign_registry_is_per_source_and_derived():
    """登记是 (样本, 规则) 级的；规则集从表派生，不得另写一份。"""
    assert ds.known_benign("pdf_08_large_40p.pdf", "duplicate_anchor") is True
    assert ds.known_benign("pdf_08_large_40p", "duplicate_anchor") is True   # 传 stem 亦可
    # 真实样本腿上同一规则命中不在表里 ⇒ 照常出项、照常施工
    assert ds.known_benign("real_contract.pdf", "duplicate_anchor") is False
    assert ds.known_benign("pdf_08_large_40p.pdf", "split_table") is False
    assert ds.CORPUS_KNOWN_RULES == frozenset(
        r for v in ds.CORPUS_KNOWN_BENIGN.values() for r in v)
    assert ds.CORPUS_KNOWN_BENIGN, "登记表被清空 ⇒ CH-503 的冻结红线随之空转"


def _defects(records: list[dict]) -> dict:
    return {"records": [{"source": s, "status": "ok", "findings": f} for s, f in records]}


def _defect_item(items: list, rule: str):
    got = [i for i in items if i.origin == "defect" and i.key == rule]
    assert len(got) == 1, [(i.origin, i.key) for i in items]
    return got[0]


@pytest.mark.gate
def test_known_benign_changes_disposition_only_never_the_grade():
    """known_benign 只改处置：分数与档位必须与未标注时逐位相同（O-f 纪律）。"""
    fd = {"rule": "duplicate_anchor", "severity": "major", "where": "headings",
          "evidence": "重复锚点"}
    plain = _defect_item(pr.collect(defects=_defects([("a.pdf", [dict(fd)])]),
                                    dev_plan=_DP), "duplicate_anchor")
    benign = _defect_item(pr.collect(
        defects=_defects([("pdf_08_large_40p.pdf", [dict(fd, known_benign=True)])]),
        dev_plan=_DP), "duplicate_anchor")
    assert plain.disposition == "fix_local"
    assert benign.disposition == pr.DISP_KNOWN_BENIGN == "known_benign"
    for f in ("impact", "frequency", "score", "priority", "blocking", "fixability"):
        assert getattr(plain, f) == getattr(benign, f), (
            f"{f} 被 known_benign 改动（{getattr(plain, f)} → {getattr(benign, f)}）："
            "「已知非缺陷」不是修复代价，不得参与判级")
    # 不占 B 号：已登记的语料形态不该再拟新号
    assert benign.b_draft == ""


@pytest.mark.gate
def test_known_benign_never_exempts_an_unregistered_source():
    """反滥用红线：同一规则只要有一个**未登记**样本命中，本条就仍是可施工项。

    没有这条，往表里加一行就能把该规则在全部样本上的命中一起吞掉——登记表会从
    「冻结已知形态」退化成「静音开关」。
    """
    fd = {"rule": "duplicate_anchor", "severity": "major", "where": "headings",
          "evidence": "重复锚点", "known_benign": True}
    mixed = _defect_item(pr.collect(defects=_defects([
        ("pdf_08_large_40p.pdf", [dict(fd)]),
        ("brand_new_doc.pdf", [dict(fd, known_benign=False)]),
    ]), dev_plan=_DP), "duplicate_anchor")
    assert mixed.disposition == "fix_local", (
        f"未登记样本的命中被一并豁免：{mixed.disposition}")
    assert mixed.n_sources == 2, mixed.n_sources
