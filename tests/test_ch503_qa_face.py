"""CH-503（第十八轮）R18-QA-1 / R18-QA-2：QA 判定面的红线。

两块内容同属「判定层自己的牙齿」，都毫秒级、无子进程、无真实转换：

**R18-QA-1（OPT-2 反空跑）**——第三轮 111 项探针首跑 4 项假信号（3.6%），病灶都是
「判定式里有一个让断言在什么都没测时也成立的前件」。`tests/qa/assertions.py` 把
「取到了样本」变成显式前置，本文件钉住助手本身：**负夹具必须 FAIL**（助手不红就是
又一个空跑）；再把产品红线 / 打分侧 / 探针助手三处的「转义口径」逐样本做双向差——
三处各写一遍 `(?<!\\)` 早晚会分叉，本轮修的正是这条。

**R18-QA-2（OPT-9 残段）**——`defect_scan` 的 9 条规则有自检（`test_ch501_qa_auto.py`
钉「规则能被变异样本触发」），但**语料级结果一条红线都没有**：`defect_scan.py`
退出码恒 0（信息层纪律），22 篇语料扫出什么都只进报告。本文件把「基线产物的命中
集合」冻结成红线：新增一类结构缺陷即转红，而不是等人去看报告。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "tests" / "corpus"
EXPECTED = CORPUS / "expected"

#: 唯一没有基线产物的语料样本：S1.5 门禁拒收（无文本层），无产物可扫。
#: 冻结**具名**而不是「数量 1」——将来多一个拒收样本必须显式改这里。
CORPUS_REJECTED = frozenset({"pdf_06_scanned_1p"})

#: 冻结的已知命中（`source stem → {规则名: 命中数}`），来源是 CH-503 本机实测而不是推断：
#: - `fence_no_lang`（minor）：`make_corpus.py` 特意造了**不带语言标注**的围栏样本，
#:   这是 MD040 的展示用例，不是管线丢语言——语言标注在源侧本就不存在。
#: - `duplicate_anchor`（major）：大样本生成器按 30 节循环输出同名二级标题
#:   （`make_corpus.py` 的 `add_heading("1.1 章节说明", level=2)`），重复标题是
#:   **语料设计**，不是 TOC 缓存被当正文（D09 的真实形态）。
#: 两类都是「扫描器正确命中、但该形态不是缺陷」的已知集合。写在这里的意义是：想让
#: 一条新命中过线，必须先把它加进这张表——重烤基线不再能顺带吞掉结构退化。
CORPUS_KNOWN_FINDINGS = {
    "pdf_04_links_code_2p": {"fence_no_lang": 1},
    "docx_07_code_quote": {"fence_no_lang": 1},
    "pdf_08_large_40p": {"duplicate_anchor": 1},
    "docx_08_large_30sections": {"duplicate_anchor": 1},
}

#: 已知命中允许来自的规则集（同表并集，写成常量是为了让「意外规则」报得清楚）。
CORPUS_KNOWN_RULES = frozenset({"fence_no_lang", "duplicate_anchor"})

_SCAN_CACHE: dict[str, dict] = {}


def _scan_corpus_products() -> dict[str, dict]:
    """逐篇扫 22 篇语料的**基线产物**（`expected/*.md`），按 stem 返回并缓存。

    口径必须写清楚：本函数读的是冻结基线产物，不是当场转换的输出。「基线 == 新产物」
    由语料逐字节回归腿（`tests/corpus/run_corpus.py`，22/22）在同轮证明；两腿合起来
    才构成「语料级结构缺陷红线」，单看本函数只能证明基线自身没有那类形态。
    """
    if _SCAN_CACHE:
        return _SCAN_CACHE
    from tests.qa import defect_scan as ds
    from tests.qa import evaluate

    for md in sorted(EXPECTED.glob("*.md")):
        stem = md.name[:-3]
        refs = (evaluate.load_ideal(stem) or {}).get("headings", [])
        ctx = ds.ScanCtx(ref_headings=refs or None, warnings=[])
        findings = ds.scan(md.read_text(encoding="utf-8"), ctx=ctx)
        by_rule: dict[str, int] = {}
        for f in findings:
            by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
        _SCAN_CACHE[stem] = {"by_rule": by_rule,
                             "severities": sorted({f.severity for f in findings}),
                             "text_bytes": md.stat().st_size}
    return _SCAN_CACHE


@pytest.mark.gate
def test_corpus_products_and_manifest_are_two_way_consistent():
    """清单与基线产物双向差为空：新增样本忘了重烤、或留下孤儿产物，都必须红。"""
    from tests.qa.assertions import require_hits

    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    stems = {Path(e["source"]).stem for e in require_hits(manifest, 22, "语料清单")}
    products = {p.name[:-3] for p in EXPECTED.glob("*.md")}
    # 先声明分母口径（OPT-2 第 4 条）：清单 22 篇，其中 1 篇是拒收样本、无产物。
    missing = stems - products - set(CORPUS_REJECTED)
    orphans = products - stems
    assert not missing, f"清单里这些样本没有基线产物（重烤漏了？）：{sorted(missing)}"
    assert not orphans, f"基线目录有不在清单里的孤儿产物：{sorted(orphans)}"
    assert products == stems - CORPUS_REJECTED, (
        f"无产物样本集合漂移：实测 {sorted(stems - products)}，"
        f"冻结口径 {sorted(CORPUS_REJECTED)}")


@pytest.mark.gate
def test_defect_scan_corpus_baseline_matches_frozen_set():
    """22 篇语料的结构缺陷命中集合 == 冻结表：多一类命中即红，除非显式改表。"""
    from tests.qa.assertions import assert_no_vacuous, require_hits

    scanned = _scan_corpus_products()
    require_hits(scanned, 21, "语料基线产物（expected/*.md）")
    # 每篇都必须真的读到非空产物：空文件 / 读失败在这里转红，而不是「0 命中 = 清白」
    assert_no_vacuous(sorted(scanned), lambda s: scanned[s]["text_bytes"] > 0,
                      subject="语料逐篇扫描记录")
    unexpected = {s: v["by_rule"] for s, v in scanned.items()
                  if v["by_rule"] != CORPUS_KNOWN_FINDINGS.get(s, {})}
    assert not unexpected, (
        f"基线产物出现未登记的缺陷命中：{unexpected}\n"
        "新增结构性退化——先人工判定是缺陷还是新的已知形态，"
        "再决定改不改本文件的 CORPUS_KNOWN_FINDINGS")
    known_total = sum(sum(v.values()) for v in CORPUS_KNOWN_FINDINGS.values())
    measured_total = sum(sum(v["by_rule"].values()) for v in scanned.values())
    assert measured_total == known_total, (
        f"命中总数不一致：实测 {measured_total} / 冻结 {known_total}")


@pytest.mark.gate
def test_blocking_rules_are_silent_on_all_corpus_products():
    """blocking 级规则（裂表 / 引导符 / 乱码 / 静默降级）在 22 篇基线上一律 0 命中。"""
    scanned = _scan_corpus_products()
    from tests.qa import defect_scan as ds

    blocking_rules = {n for n, s in ds.SEVERITY.items() if s == "blocking"}
    assert blocking_rules, "SEVERITY 里没有 blocking 规则——分档表被改坏了"
    hit = {s: {r: n for r, n in v["by_rule"].items() if r in blocking_rules}
           for s, v in scanned.items()}
    assert not {s: v for s, v in hit.items() if v}, (
        f"基线语料出现 blocking 级命中（发布面缺陷）：{ {s: v for s, v in hit.items() if v} }")
    fired = {r for v in scanned.values() for r in v["by_rule"]}
    assert fired <= CORPUS_KNOWN_RULES, (
        f"基线语料上出现了预期外的规则：{sorted(fired - CORPUS_KNOWN_RULES)}")


# --- R18-QA-1：助手自身必须有牙齿 -------------------------------------------
@pytest.mark.gate
def test_require_hits_fails_when_sample_is_short():
    """负夹具：0 条与不足都必须 FAIL——助手不红就是又一个空跑。"""
    from tests.qa.assertions import require_hits

    with pytest.raises(pytest.fail.Exception, match="反空跑"):
        require_hits([], 2, "翻页命中")
    with pytest.raises(pytest.fail.Exception, match="取样口径"):
        require_hits([{"id": 1}], 2, "翻页命中")
    assert require_hits([1, 2], 2, "两个命中") == [1, 2]
    assert require_hits((n for n in range(3)), 3, "生成器取样") == [0, 1, 2]


@pytest.mark.gate
def test_assert_no_vacuous_fails_on_empty_and_on_failing_item():
    """负夹具：空集合 FAIL；逐项判据为假/抛异常都要 FAIL 并带下标归因。"""
    from tests.qa.assertions import assert_no_vacuous

    with pytest.raises(pytest.fail.Exception, match="待检集合"):
        assert_no_vacuous([], lambda x: True, subject="围栏样本")
    with pytest.raises(pytest.fail.Exception, match=r"\[1\]"):
        assert_no_vacuous(["ok", "bad"], lambda x: x == "ok", subject="围栏样本")
    with pytest.raises(pytest.fail.Exception, match="抛 ZeroDivisionError"):
        assert_no_vacuous([0, 2], lambda x: 10 / x, subject="倒数检查")
    assert assert_no_vacuous([2, 4], lambda x: x % 2 == 0, subject="偶数集") == 2


# --- R18-QA-1：三处判据同源对账 ---------------------------------------------
#: (样本, 该产品形态是否算违规, 参与对账的面)。「裸标签」三面都有；锚点形态产品与
#: 打分侧有，探针助手 `bare_tag_re` 只管标签族，不列入该样本的面。
CALIBER_SAMPLES: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
    ("[x](#a)", True, ("product", "scorer")),
    (r"\](#a)", False, ("product", "scorer")),
    ("<script>x</script>", True, ("product", "scorer", "probe")),
    (r"\<script>x\</script\>", False, ("product", "scorer", "probe")),
    ("<img src=x>", True, ("product", "scorer", "probe")),
    (r"\<img src=x\>", False, ("product", "scorer", "probe")),
    ('<a href="#s">', True, ("product", "scorer", "probe")),
    (r'\<a href="#s"\>', False, ("product", "scorer", "probe")),
    ("[y](javascript:alert(1))", True, ("product",)),
)


@pytest.mark.gate
def test_escape_caliber_is_the_same_rule_on_all_three_faces():
    """产品红线 / mdscore 打分 / 探针助手对「转义形态」逐样本分类必须一致。

    三个面各写一遍 `(?<!\\\\)` 迟早分叉（本轮修的正是这条），所以做的是 OPT-2 第 4 条
    的形态：先声明口径，再逐样本做双向差，而不是比一个总数。
    """
    from doc2md.pipeline.s4_render import _contract_violation
    from tests.qa import mdscore
    from tests.qa.assertions import bare_tag_re

    scorer = dict(mdscore.RENDER_REDLINES)
    probe = bare_tag_re("script", "img", "a")
    for sample, expect_hit, faces in CALIBER_SAMPLES:
        if "product" in faces:
            assert bool(_contract_violation(sample)) is expect_hit, f"产品面漂移：{sample!r}"
        if "probe" in faces:
            assert bool(probe.search(sample)) is expect_hit, f"探针面漂移：{sample!r}"
        if "scorer" in faces:
            # 打分侧按「这条样本命中了任一红线」判定，与产品的「任一违规即拒」同构
            got = {name: len(pat.findall(sample)) for name, pat in scorer.items()
                   if name != "unclosed_fence"}
            assert bool([n for n, c in got.items() if c]) is expect_hit, (
                f"打分面漂移：{sample!r} → {got}")


@pytest.mark.gate
def test_scorer_render_redlines_exempt_fenced_literals():
    """打分侧与产品侧同一条「代码块字面量豁免」：围栏内不判，围栏外仍判。"""
    from tests.qa import mdscore

    inside = "```python\n~~not-strike~~ and [t](#anchor) and <script>x</script>\n```\n"
    assert mdscore.render_redlines(inside)["hits"] == {}, mdscore.render_redlines(inside)
    outside = "正文里的\n\n~~删除~~ 与 [t](#anchor)。\n"
    hits = mdscore.render_redlines(outside)["hits"]
    assert hits.get("strike") == 1, hits
    assert hits.get("self_anchor") == 1, hits
    # 未闭合围栏不得把整篇后半段吞掉（配对判定另有 unclosed_fence 记账）
    assert mdscore.render_redlines("```\n开围栏\n")["hits"].get("unclosed_fence") == 1
