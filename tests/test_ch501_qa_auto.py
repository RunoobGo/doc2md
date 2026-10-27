"""CH-501 实机自动测试体系的静态红线（全部毫秒级，无子进程、无真实转换）。

分层定位（dev-plan §8.1）：本文件属 L1 红线层，只守**测试体系自身**的形态：
判定层不许空转（规则必须能被变异样本触发）、可追溯锚点不许失效（解析不到就报
「锚点失效」而不是静默恒真）、难度样本的声明字段不许缺、真实语料的隐私边界不许破。
需要拉起真实转换的断言一律在 tests/qa/（`corpus` 标记）里，不进本文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QA = ROOT / "tests" / "qa"
HARD = ROOT / "tests" / "corpus" / "hard"

#: 严重度红线：**独立冻结的字面量**，不得从 `defect_scan.SEVERITY` 推导。
#: 规则输出与断言若读同一个 dict，`severity == SEVERITY[name]` 就恒真，某条
#: blocking 规则被降级成 minor 也不会转红——这份字面量是唯一的异源对照。
EXPECTED_SEVERITY = {"split_table": "blocking", "leader_dots_in_cell": "blocking",
                     "cjk_space_collapse": "major", "mojibake_residue": "blocking",
                     "furniture_residue": "major", "silent_degrade": "blocking",
                     "fence_no_lang": "minor", "col_count_drift": "major",
                     "duplicate_anchor": "major"}


def _load(name: str):
    """按路径加载 tests/qa 下的模块（gen_cases 顶层无副作用，可安全 import）。"""
    import importlib

    return importlib.import_module(f"tests.qa.{name}")


@pytest.mark.gate
def test_hard_expectations_have_judge_and_star():
    """每条难度声明必须自带判定口径与星级——没有 judge 的期望值不可裁定。"""
    path = HARD / "expectations.json"
    assert path.is_file(), "先跑 .venv/bin/python tests/qa/gen_cases.py --sources"
    specs = json.loads(path.read_text(encoding="utf-8"))
    assert len(specs) >= 10, f"难度样本 {len(specs)} 条 < 10（D03/D09/D10 未生成）"
    for s in specs:
        assert s.get("judge"), f"{s['difficulty']} 缺 judge（判定口径）"
        assert isinstance(s.get("star"), int) and s["star"] >= 1, f"{s['difficulty']} 缺星级"
        assert s.get("expect"), f"{s['difficulty']} 缺 expect（目标声明）"
        assert (HARD / s["source"]).is_file(), f"{s['source']} 声明了但文件不存在"


@pytest.mark.gate
def test_generator_registry_covers_all_open_difficulties():
    """DIFFICULTIES 里 covered 非 True 的项必须都有生成器（纸面覆盖 = 没有覆盖）。"""
    gc = _load("gen_cases")
    open_ids = [d["id"] for d in gc.DIFFICULTIES if d.get("covered") is not True]
    missing = [d["id"] for d in gc.DIFFICULTIES
               if d.get("covered") is not True and not d.get("gen")]
    # D10 的真实 CID 字体本机不可合成，允许以「代理腿」形态存在，但必须显式声明
    allowed_proxy = {"D10"}
    missing = [m for m in missing if m not in allowed_proxy]
    assert open_ids, "DIFFICULTIES 解析为空——锚点失效"
    assert not missing, f"这些难度项没有生成器，用例矩阵的交叉用例无资产：{missing}"
    for name in [d["gen"] for d in gc.DIFFICULTIES if d.get("gen")]:
        assert name in gc._GENERATORS, f"_GENERATORS 未注册 {name}"


@pytest.mark.gate
def test_tcolc_metric_key_matches_product():
    """evaluate.py 读的 metrics 键必须是产品真实写出的键，否则 TCOLC 永远缺席。

    本轮实测缺陷：evaluate.py 读 `table_col_consistency`，产品写 `table_col`
    （doc2md/pipeline/convert.py:388）⇒ tcolc 恒 None ⇒ S3 列一致率从未进过报告，
    而它正是 B-43 裂表最直接的信号。断言两侧同源，任一侧改名即转红。
    """
    import re

    conv = (ROOT / "doc2md" / "pipeline" / "convert.py").read_text(encoding="utf-8")
    ev = (QA / "evaluate.py").read_text(encoding="utf-8")
    attr = (QA / "attribute.py").read_text(encoding="utf-8")
    assert 'out["table_col"]' in conv, "convert._metrics 的 table_col 键消失——锚点失效"
    used = set(re.findall(r'\.get\("(table_col[a-z_]*)"\)', ev))
    assert used == {"table_col"}, f"evaluate.py 的 TCOLC 键应为 table_col，实际 {sorted(used)}"
    # 键对了还不够：metrics["table_col"] 是 {"tables","rows","rate"}，打分只认 rate。
    # 把整份 dict 交给 mdscore.score 会在 round() 时 TypeError（Task 3 实跑复现）。
    rate_read = '"table_col") or {}).get("rate")'
    assert rate_read in ev, "evaluate.py 必须读 table_col.rate，不能把整份 dict 交给打分"
    assert rate_read in attr, "attribute.py 必须读 table_col.rate，不能把整份 dict 交给打分"


@pytest.mark.gate
def test_attribution_identity_holds():
    """归因恒等式：Σ 逐指标预算 == 1 − 质量综合分（残差只能进 unattributed）。"""
    attr = _load("attribute")
    rec = {
        "heading": {"f1": 0.5, "p": 0.5, "r": 0.5, "pred": 2, "ref": 4,
                    "matched": 1, "matched_text_only": 2, "level_accuracy": 0.5,
                    "fp": 0, "fn": 2},
        "table": {"teds": 0.6, "found_only": 0.6, "n_pred": 1, "n_ref": 2,
                  "missed": 1, "extra": 0, "shape_ok": 0.5},
        "structure": {"bsf": 0.8}, "order": {"nid": 0.9},
    }
    sc = {"scores": {"lint_contract": 0.9, "asset": 1.0, "idempotent": 1.0,
                     "render": 1.0, "honest": 0.0},
          "weights": {}}
    out = attr.attribute(rec, sc, md="# a\n", blocks=[], ir=None, res=None)
    ident = out["identity"]
    assert ident["ok"], f"恒等式不成立：{ident}"
    assert abs(ident["residual"]) < 1e-9, ident
    # 静默降级（honest=0）必须归到 S1_parse，且严重度 blocking
    deg = [d for d in out["deviations"] if d["kind"] == "silent_degrade"]
    assert deg and deg[0]["stage"] == "S1_parse" and deg[0]["severity"] == "blocking"
    # 漏检标题归 S1_parse，层级错归 S3_structure（两条不同阶段，不许混）
    kinds = {d["kind"]: d["stage"] for d in out["deviations"]}
    assert kinds.get("heading_missed") == "S1_parse"
    assert kinds.get("heading_level") == "S3_structure"
    # 阶段合计 + unattributed 必须等于 total_gap（不是「差不多」）
    total = sum(s["loss"] for s in out["by_stage"].values()) + out["unattributed"]["loss"]
    assert abs(total - out["total_gap"]) < 1e-9, (total, out["total_gap"])


@pytest.mark.gate
def test_defect_scan_rules_are_not_vacuous():
    """每条规则都必须被自己的变异样本触发——永远不命中的规则等于不存在。"""
    ds = _load("defect_scan")
    assert set(ds.RULE_SELFTESTS) == set(ds.RULES), (
        f"自检样本与规则集不同源：缺 {sorted(set(ds.RULES) - set(ds.RULE_SELFTESTS))}")
    # 与冻结字面量异源对照：`SEVERITY` 自身被改（如 split_table 降成 minor）即转红。
    mismatch = {k: (ds.SEVERITY.get(k), v) for k, v in EXPECTED_SEVERITY.items()
                if ds.SEVERITY.get(k) != v}
    assert ds.SEVERITY == EXPECTED_SEVERITY, f"严重度登记被改写：{mismatch}"
    for name, (pos, _neg, pctx, _nctx) in ds.RULE_SELFTESTS.items():
        hits = [f for f in ds.scan(pos, ctx=pctx) if f.rule == name]
        assert hits, f"规则 {name} 在正向变异样本上没命中——它是空转的（判定层没牙齿）"
        assert hits[0].severity == ds.SEVERITY[name], f"{name} 严重度与登记不一致"
        for h in hits:
            assert h.severity == EXPECTED_SEVERITY[name], (
                f"{name} 命中严重度 {h.severity!r} 与冻结红线 {EXPECTED_SEVERITY[name]!r} 不符")


@pytest.mark.gate
def test_defect_scan_clean_fixture_is_silent():
    """干净样本必须全静默——否则规则误报，报告里的命中数不可信。"""
    ds = _load("defect_scan")
    for name, (_pos, neg, _pctx, nctx) in ds.RULE_SELFTESTS.items():
        hits = [f for f in ds.scan(neg, ctx=nctx) if f.rule == name]
        assert not hits, f"规则 {name} 在自己的干净腿上误报：{[h.evidence for h in hits]}"
    hits = ds.scan(ds.CLEAN_FIXTURE, ctx=ds.CLEAN_FIXTURE_CTX)
    assert not hits, f"共用干净样本上有命中：{[(h.rule, h.evidence) for h in hits]}"


@pytest.mark.gate
def test_trace_anchors_resolve():
    """锚点必须先解析成功再比值——解析不到就报失效，不许静默恒真。"""
    tr = _load("trace")
    out = tr.trace()
    reqs = out["requirements"]
    frs = [k for k in reqs if k.startswith("FR-")]
    nfrs = [k for k in reqs if k.startswith("NFR-")]
    assert len(frs) >= 28, f"dev-plan §2.1 的 FR 行只解析出 {len(frs)} 条（应 ≥28）——锚点失效"
    assert len(nfrs) >= 11, f"§2.2 的 NFR 行只解析出 {len(nfrs)} 条（应 ≥11）——锚点失效"
    assert len(out["codes"]["W"]) == 4, f"W 码应为 4 个，实际 {out['codes']['W']}"
    assert len(out["codes"]["E"]) >= 14, f"E 码应 ≥14 个，实际 {out['codes']['E']}"
    assert len(out["cli"]) >= 11, f"CLI 子命令应 ≥11 个，实际 {out['cli']}"
    assert len(out["api"]) >= 20, f"API 端点应 ≥20 个，实际 {out['api']}"
    assert all(reqs[k].get("priority") for k in frs), "FR 行缺优先级列——表头形态漂移"


@pytest.mark.gate
def test_no_orphan_requirement_ids_in_tests():
    """测试里引用了但产品/文档没声明的编号 = 孤儿（写错了或已删除，两种都要修）。"""
    tr = _load("trace")
    orphans = tr.trace()["orphans"]
    assert not orphans, f"孤儿编号 {orphans}：tests/ 引用了未声明的 FR/NFR/W/E"


@pytest.mark.gate
def test_p0_requirements_and_error_codes_are_covered():
    """P0 需求与全部错误码必须至少被一个测试引用（覆盖 = 可追溯，不等于测得对）。"""
    tr = _load("trace")
    out = tr.trace()
    assert not out["uncovered"]["FR_P0"], f"P0 需求无测试引用：{out['uncovered']['FR_P0']}"
    assert not out["uncovered"]["E"], f"错误码无测试引用：{out['uncovered']['E']}"


_DP = ("### 12.7 未验证项（诚实标注）\n\n"
       "| B-43 | D02 跨页表格 | **仅登记 · 发布阻断**：说明 |\n"
       "| B-44 | D05 目录点线 | **仅登记 · 发布阻断**：说明 |\n"
       "| B-45 | D08 序号口径 | **仅登记**：说明 |\n\n"
       "### 12.8 教训条目\n\n"
       # 段外诱饵：钉住 §12.7 的段内锚定。全文扫描会把它算进最大号（→ B-100），
       # 只有按标题截段才能仍然得出 B-46 / b_next=47。
       "| B-99 | 段外诱饵 | **仅登记**：说明 |\n")


@pytest.mark.gate
def test_priority_partition_is_exact():
    """优先级层的组合口径必须可复算，且阻断覆盖 / 拟号两条分支都真的跑过。"""
    pr = _load("prioritize")
    attribution = {"records": [
        {"source": "a.pdf", "status": "ok", "total_gap": 0.4, "deviations": [
            {"kind": "heading_missed", "stage": "S1_parse", "loss": 0.2,
             "severity": "major", "evidence": "漏 2 个标题", "budgeted": True},
            {"kind": "table_split", "stage": "S2_shard", "loss": 0.1,
             "severity": "blocking", "evidence": "裂成 2 张", "budgeted": True}]},
        {"source": "b.docx", "status": "ok", "total_gap": 0.2, "deviations": [
            {"kind": "heading_missed", "stage": "S1_parse", "loss": 0.1,
             "severity": "major", "evidence": "漏 1 个标题", "budgeted": True},
            # 残差伪类型：human、无 B 关联 ⇒ 必须拿到拟号（下文的 B-46）
            {"kind": "heading_residual", "stage": "unattributed", "loss": 0.05,
             "severity": "minor", "evidence": "标题缺口无法按计数拆分",
             "budgeted": True}]},
        # 拒收样本必须整条排除：若 status 过滤失效，gate_verdict 会凭空多出一条
        # 优先级项，gap 也会被这 0.9 污染
        {"source": "c.pdf", "status": "rejected", "total_gap": 0.9, "deviations": [
            {"kind": "gate_verdict", "stage": "S3_structure", "loss": 0.9,
             "severity": "blocking", "evidence": "拒收样本", "budgeted": True}]}]}
    defects = {"records": [
        {"source": "a.pdf", "status": "ok", "findings": [
            {"rule": "fence_no_lang", "severity": "minor", "where": "12",
             "evidence": "``` 无语言标注"}]},
        # 拒收样本里的同名规则同样不得出项（split_table 漏进来还会抢走 B-43）
        {"source": "c.pdf", "status": "rejected", "findings": [
            {"rule": "split_table", "severity": "blocking", "where": "3",
             "evidence": "拒收样本"}]}]}
    # 编号取**已声明**的 FR-101 / FR-102：本文件在 tests/ 树下，trace.scan_references
    # 会把函数体里的字面编号算作引用，写未声明的编号会直接污染 test_no_orphan_... 的门。
    tr = {"uncovered": {"FR_P0": ["FR-101"], "FR_other": [], "NFR": [], "E": [],
                        "W": []},
          "orphans": ["FR-102"]}
    ev = {"n_ok": 2, "gate_failures": [{"source": "a.pdf", "failed": ["表格列数"]}]}
    out = pr.prioritize(attribution=attribution, defects=defects, trace=tr,
                        eval_summary=ev, dev_plan=_DP)
    # status=rejected 的记录整条排除：它们的键既不出项，也不改变条目总数。
    # 同主语对照（B-53 / CH-515 #44–#45）：`status` 过滤失效与「压根没有这类记录」
    # 给出同一个 0。所以正例只能把 c.pdf 那两条记录改回 `status="ok"`、其余输入一字
    # 不动，再数同两个键——必须各出 1 项，且条目总数随之 7→9（排除是「少一条」而不是
    # 「少一层」）。放行轮的恒等式也要成立，否则正例只是撞上了另一条通路。
    # 本块排在恒等式与精确条目数之前：那两条更强的同夹具判据会把「漏进来」的方向
    # 先摘走，只有在这里数才有归属。
    def _flip(status_c: str) -> dict:
        def rep(recs):
            return [{**r, "status": status_c} if r["source"] == "c.pdf" else r
                    for r in recs]
        return pr.prioritize(attribution={**attribution, "records": rep(attribution["records"])},
                             defects={**defects, "records": rep(defects["records"])},
                             trace=tr, eval_summary=ev, dev_plan=_DP)

    out_ok = _flip("ok")
    pair = (("attribution", "gate_verdict"), ("defect", "split_table"))
    neg_hits = tuple(sum(1 for i in out["items"] if (i["origin"], i["key"]) == k)
                     for k in pair)
    pos_hits = tuple(sum(1 for i in out_ok["items"] if (i["origin"], i["key"]) == k)
                     for k in pair)
    assert out_ok["identity"]["ok"], out_ok["identity"]
    assert (neg_hits, pos_hits) == ((0, 0), (1, 1)), (
        f"rejected 记录排除判据无感：拒收轮两个键命中 {neg_hits}、放行轮命中 {pos_hits}"
        f"（应为 (0, 0) / (1, 1)；放行轮为 0 说明 `status` 过滤把该出项的记录也一起摘掉，"
        f"前两条否定只是空话）")
    assert (out["identity"]["n_items"], out_ok["identity"]["n_items"]) == (7, 9), (
        f"拒收排除的条目账不平：拒收轮 {out['identity']['n_items']} 项、"
        f"放行轮 {out_ok['identity']['n_items']} 项（应为 7 / 9，差 2 = c.pdf 两条记录）")
    ident = out["identity"]
    assert ident["ok"], ident
    # 3 个偏差类型（含残差伪类型）+ 1 条规则 + 2 个 trace 项（FR_P0 / orphans）
    # + 1 个 eval 项 = 7
    assert ident["n_items"] == 7, [(i["origin"], i["key"]) for i in out["items"]]
    assert ident["by_input"] == 7, ident
    assert out["origins"] == {"attribution": 3, "defect": 1, "trace": 2, "eval": 1}
    assert sum(out["counts"].values()) == 7
    # 阻断覆盖：table_split 分数不够 P0，但它是 blocking ⇒ 仍必须 P0，且关联 B-43
    ts = next(i for i in out["items"] if i["key"] == "table_split")
    assert ts["score"] < pr.P0_FLOOR, ts
    assert ts["priority"] == "P0" and ts["blocking"] is True
    assert ts["b_linked"] == "B-43"
    # §12.8 之后的 B-99 是段外诱饵：只有段内扫描才仍得出 B-46 / b_next=47；
    # 全文扫描会把它当最大号（B-100）并写进草稿号。
    # 同主语对照（B-53 / CH-515 #46）：`b_rows` 在标题锚点失效时返回 `{}`，
    # 「B-99 不在里面」对空表同样成立——所以正例必须数同一个 `b_rows(_DP)` 里
    # 段内的 B-43/B-44/B-45：三个都在（3）才证明段内扫描是活的，那个 0 才是观测。
    # 本块排在拟新号断言之前：段外诱饵一旦漏进最大号，`b_draft` 会先替它红，
    # 归属就落到不了这条判据上。
    rows = pr.b_rows(_DP)
    out_hits = sum(1 for b in ("B-99",) if b in rows)
    in_hits = sum(1 for b in ("B-43", "B-44", "B-45") if b in rows)
    assert (out_hits, in_hits) == (0, 3), (
        f"b_rows 段内锚定判据无感：段外诱饵命中 {out_hits}、段内三行命中 {in_hits}"
        f"（应为 0 / 3；段内为 0 说明 §12.7 压根没扫到行，前一条否定只是空表恒真）")
    # 拟新号只给「非本机可自证且未登记」的项：B-43 已登记 ⇒ 不占新号；
    # heading_residual 是 human 且无关联 ⇒ 拿到 _DP 最大号 +1 的草稿号
    assert ts["b_draft"] == ""
    hr = next(i for i in out["items"] if i["key"] == "heading_residual")
    assert hr["disposition"] == "human" and hr["b_linked"] == "", hr
    assert hr["b_draft"] == "B-46", hr
    assert out["b_next"] == 47


@pytest.mark.gate
def test_fixability_does_not_downgrade_grade():
    """第十七轮纪律：分档不看修复成本——成本只改同档内的顺序与处置路径。"""
    pr = _load("prioritize")
    assert abs(sum(pr.W.values()) - 1.0) < 1e-9, pr.W
    # 同主语对照（B-53 / CH-515 #47）：权重表若是空的（或只剩一个键），
    # 「fixability 不在表里」同样成立——那条否定就只是空表恒真。正例数的是同一张表里
    # **真实参与算分**的键：把 impact / frequency 各自单独打满、另一维归零，两次都必须
    # 落出严格介于 0 与 1 之间的分（0.6 / 0.4），证明两个权重都是活的。
    zero = sum(1 for k in ("fixability",) if k in pr.W)
    live = sum(1 for v in (pr.score_item(1.0, 0.0), pr.score_item(0.0, 1.0))
               if 0.0 < v < 1.0)
    assert (zero, live) == (0, 2), (
        f"权重表判据无感：fixability 命中 {zero} 个键、单维打满仍落出中间分的权重 {live} 个"
        f"（应为 0 / 2；live 不足 2 说明 `W` 残缺或压根不进 `score_item`，"
        f"前一条否定只是空表恒真）")
    hard = pr.make_item("attribution", "k", "t", "-", 1.0, 1.0, pr.FIX_EXTERNAL,
                        "loss", False, 0.5, 3, "ev", "")
    easy = pr.make_item("attribution", "k", "t", "-", 1.0, 1.0, pr.FIX_LOCAL,
                        "loss", False, 0.5, 3, "ev", "")
    assert hard.score == easy.score and hard.priority == easy.priority == "P0"
    assert hard.disposition == "external" and easy.disposition == "fix_local"
    # 分数必须能从打印字段复算：夹取发生在算分**之前**，越界输入不能出现
    # 「频率打印 1.0、分数按 2.0 算」这种对不上账的组合。
    over = pr.make_item("attribution", "k2", "t", "-", 1.0, 2.0, pr.FIX_LOCAL,
                        "loss", False, 0.5, 3, "ev", "")
    assert over.frequency == 1.0 and over.score == pr.score_item(1.0, 1.0) == 1.0
    order = {"P0": 0, "P1": 1, "P2": 2}
    assert order[pr.bucket(0.9, False)] <= order[pr.bucket(0.5, False)] \
        <= order[pr.bucket(0.1, False)]
    assert pr.bucket(0.5, False) == "P1" and pr.bucket(0.1, False) == "P2"
    assert pr.bucket(0.0, True) == "P0", "阻断项无条件 P0"

    # 同分同档、仅可修复性不同的两条必须在**真实 sort** 里分先后。键名刻意选
    # 「重烤项字母序在前」的一对：只按 (档, 分数, 来源, 键) 排序也会让它们保持
    # 现在的顺序，用字母序巧合冒充可修复性序——本地 key 排序因此不必再写（它测的是
    # 测试自己抄的一份 key，不是 prioritize 的那份）。
    attribution = {"records": [
        {"source": "a.pdf", "status": "ok", "total_gap": 0.2, "deviations": [
            {"kind": "idempotent_break", "stage": "S4_render", "loss": 0.1,
             "severity": "minor", "evidence": "幂等复检不一致", "budgeted": True},
            {"kind": "render_redline", "stage": "S4_render", "loss": 0.1,
             "severity": "minor", "evidence": "渲染红线", "budgeted": True}]}]}
    out = pr.prioritize(attribution=attribution, dev_plan=_DP)
    rank = {i["key"]: n for n, i in enumerate(out["items"])}
    local = out["items"][rank["render_redline"]]
    rebake = out["items"][rank["idempotent_break"]]
    assert local["priority"] == rebake["priority"] == "P0", (local, rebake)
    assert local["score"] == rebake["score"], (local["score"], rebake["score"])
    assert local["fixability"] == pr.FIX_LOCAL
    assert rebake["fixability"] == pr.FIX_REBAKE
    assert "idempotent_break" < "render_redline"      # 字母序方向与期望序相反
    assert rank["render_redline"] < rank["idempotent_break"], (
        "同分同档时本机可自证项应排在重烤项之前——prioritize 的 sort 丢了 -fixability")


@pytest.mark.gate
def test_priority_calibers_are_complete_and_derived():
    """口径表必须完整覆盖上游（否则运行时 KeyError），阻断集合与新 B 号必须从文档派生。"""
    pr, at, ds = _load("prioritize"), _load("attribute"), _load("defect_scan")
    upstream = set(at.KIND_STAGE) | set(ds.RULES)
    missing = upstream - set(pr.FIXABILITY)
    assert not missing, f"FIXABILITY 缺口径：{sorted(missing)}"
    assert set(pr.RULE_KIND) <= set(ds.RULES)
    for rule, kind in pr.RULE_KIND.items():
        assert pr.RULE_STAGE[rule] == at.KIND_STAGE[kind], f"{rule}/{kind} 阶段口径分家"
    # attribute.UNATTRIBUTED 的伪类型不在 KIND_STAGE 里，但同样必须登记口径
    assert {"heading_residual", "idempotent_unattributed"} <= set(pr.FIXABILITY)
    # 两层 trace 表必须同键：只改一边会让 TRACE_TITLE[grp] 在真实语料上 KeyError
    assert set(pr.TRACE_IMPACT) == set(pr.TRACE_TITLE) == {
        "FR_P0", "FR_other", "NFR", "E", "W"}
    dp = (ROOT / "docs" / "dev-plan.md").read_text(encoding="utf-8")
    rows = pr.b_rows(dp)
    assert len(rows) >= 30, f"§12.7 的 B 行锚点失效：只解析到 {len(rows)} 行（当前应为 33）"
    assert {"B-43", "B-44"} <= pr.release_blocking(dp), "发布阻断应从文档派生"
    nxt = pr.next_free_b(dp)
    assert nxt >= 47 and f"B-{nxt}" not in rows


@pytest.mark.gate
def test_real_sample_privacy_boundary():
    """真实文档二进制不许进仓库；work/ 必须被 gitignore；脚本本身必须在仓库里。"""
    real = ROOT / "tests" / "corpus" / "real"
    leaked = sorted(p.name for p in real.rglob("*")
                    if p.suffix.lower() in (".pdf", ".docx", ".doc", ".dotx"))
    assert not leaked, f"真实样本二进制进了仓库（许可 / 隐私）：{leaked}"
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert any(ln.strip().rstrip("/") in ("work", "./work") for ln in gi
               if not ln.strip().startswith("#")), ".gitignore 未排除 work/"
    assert (real / "run_real.py").is_file(), "真实样本腿脚本缺失"
    assert (real / "manifest.json").is_file() and (real / "ground_truth.json").is_file()


@pytest.mark.gate
def test_real_ground_truth_is_minimal():
    """入库的真值只能是「标题文本 + 一句 note」——不许把正文全文抄进仓库。"""
    gt = json.loads((ROOT / "tests" / "corpus" / "real"
                     / "ground_truth.json").read_text(encoding="utf-8"))
    assert gt, "ground_truth.json 解析为空——锚点失效"
    for name, v in gt.items():
        assert set(v) <= {"headings", "note"}, f"{name} 的真值字段越界：{sorted(v)}"
        assert all(isinstance(h, str) and len(h) <= 120 for h in v["headings"]), \
            f"{name} 的 headings 不像标题（过长或非字符串）——疑似抄了正文"
        assert len(v.get("note", "")) <= 300, f"{name} 的 note 过长"


@pytest.mark.gate
def test_realtest_never_reports_pass_for_not_run():
    """没跑的腿不许记通过——空层、缺腿、异常腿一律 not_run，且只由阻断层决定总判。"""
    rt = _load("realtest")
    assert rt.layer_status([]) == rt.NOT_RUN, "空层不是通过"
    assert rt.layer_status([rt.Check("a", rt.PASS), rt.Check("b", rt.NOT_RUN)]) == rt.NOT_RUN
    assert rt.layer_status([rt.Check("a", rt.PASS), rt.Check("b", rt.FAIL)]) == rt.FAIL
    assert rt.layer_status([rt.Check("a", rt.PASS)]) == rt.PASS
    blk = rt.Layer("L1_gate", True, [rt.Check("x", rt.PASS)])
    info = rt.Layer("L5_quality", False, [rt.Check("y", rt.FAIL)])
    assert rt.overall([blk, info]) == rt.PASS, "信息层不阻断总判"
    assert rt.overall([rt.Layer("L1_gate", True, [rt.Check("x", rt.NOT_RUN)]), info]) \
        == rt.NOT_RUN
    # 状态常量封闭：任何第四种状态都必须先改这里
    assert {rt.PASS, rt.FAIL, rt.NOT_RUN} == {"pass", "fail", "not_run"}
    assert not (rt.BLOCKING & {"L5_quality"})
    # `--only` 选空 = 六层全 skipped = 整体 not_run，形态上「跑了但没结论」，
    # 与「忘了跑」不可区分，所以无法匹配的 token 必须当场退出，不许静默选空。
    assert rt.expand_only("") == ()
    assert rt.expand_only("L0,L1") == ("L0_static", "L1_gate")
    assert rt.expand_only("l5") == ("L5_quality",), "大小写与前缀都要认"
    assert rt.expand_only("L1_gate,L1") == ("L1_gate",), "重复合并成一个"
    with pytest.raises(SystemExit):
        rt.expand_only("L9")


@pytest.mark.gate
def test_junit_counts_come_from_xml(tmp_path):
    """计数只认 junitxml；文件缺失返回 None（不是 0——0 会被读成「全绿」）。"""
    rt = _load("realtest")
    xml = tmp_path / "g.xml"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="2" '
        'tests="10" time="1.5"><testcase name="a"/>'
        '<testcase name="b"><skipped type="pytest.xfail" message="B-43 未闭合"/></testcase>'
        '<testcase name="c"><skipped type="pytest.skip" message="demo"/></testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8")
    c = rt.junit_counts(xml)
    assert c == {"tests": 10, "failures": 1, "errors": 0, "skipped": 2,
                 "xfail": 1, "passed": 7}, c
    assert rt.junit_counts(tmp_path / "missing.xml") is None
    single = tmp_path / "s.xml"
    single.write_text('<testsuite tests="3" failures="0" errors="0" skipped="0"/>',
                      encoding="utf-8")
    sc = rt.junit_counts(single)
    assert sc["passed"] == 3 and sc["xfail"] == 0, "根就是 testsuite 时也要能读"


#: 十字段的**独立字面量**：与 `gen_cases.CARD_FIELDS` 各写一份。只写一份再拿自己
#: 比自己，等于给生成器发了免检金牌——字段被删掉时断言仍然绿。
CARD_FIELDS = ("编号", "所属模块", "测试目标", "输入与前置条件", "执行步骤",
               "期望结果", "实际结果", "通过状态", "优先级", "覆盖指标")


@pytest.mark.gate
def test_casebook_cards_have_all_ten_fields():
    """用例卡十字段一个不许少；没跑的必须字面写「未执行」，不许留空。"""
    gc = _load("gen_cases")
    book = (ROOT / "docs" / "qa" / "casebook.md").read_text(encoding="utf-8")
    assert "## 五、用例卡（十字段）" in book, "第五节缺失——casebook 未按 Task 8 重生成"
    cards = [b for b in book.split("### TC-")[1:]]
    assert len(cards) == len(gc.tc_matrix()) == 15, (
        f"卡片数 {len(cards)} 与矩阵 {len(gc.tc_matrix())} 不同源")
    ids = set()
    for body in cards:
        head = body.splitlines()[0]
        for f in CARD_FIELDS:
            assert f"| {f} |" in body, f"TC-{head[:3]} 缺字段「{f}」"
        # 字段顺序也必须一致（顺序乱了说明是手改的，不是生成的）
        pos = [body.index(f"| {f} |") for f in CARD_FIELDS]
        assert pos == sorted(pos), f"TC-{head[:3]} 字段顺序与 CARD_FIELDS 不一致"
        ids.add(head.split(" ")[0])
        actual = body.split("| 实际结果 |", 1)[1].split("\n", 1)[0]
        assert actual.strip().strip("|").strip(), "实际结果为空——空字段会被读成「没问题」"
    assert len(ids) == 15, "编号重复"
    assert gc.CARD_FIELDS == CARD_FIELDS


@pytest.mark.gate
def test_casebook_expectations_come_from_declaration():
    """期望结果必须与声明侧同源；载体路径必须真实存在，否则该卡只能写「未执行」。"""
    gc = _load("gen_cases")
    specs = {s["difficulty"]: s
             for s in json.loads((ROOT / "tests" / "corpus" / "hard"
                                  / "expectations.json").read_text(encoding="utf-8"))}
    book = (ROOT / "docs" / "qa" / "casebook.md").read_text(encoding="utf-8")
    for body in book.split("### TC-")[1:]:
        d = body.splitlines()[0].split("×")[-1].strip().split(" ")[0]
        carrier = gc.CARRIER.get(d, "")
        if carrier:
            assert (ROOT / carrier).exists(), f"{d} 的载体不存在：{carrier}"
        if d in specs:
            # 期望里必须出现声明侧的判据片段（不是产物抄回来的）
            judge_frag = specs[d]["judge"][:12]
            exp = body.split("| 期望结果 |", 1)[1].split("\n", 1)[0]
            assert judge_frag in exp or "expectations.json" in exp, (
                f"{d} 的期望结果与声明侧不同源（judge 片段「{judge_frag}」未出现）")
        else:
            assert "manifest.json" in body or "未执行" in body, (
                f"{d} 既无 hard 声明也无 manifest 出处")


@pytest.mark.gate
def test_release_blocking_gaps_have_cards():
    """发布阻断缺口必须有用例卡。

    B-43 / B-44 只写在 `test_hard_gate` 的 xfail 里，交付面上等于不存在——人读的是
    `casebook.md` 的卡片。两边口径都是派生的：阻断集合来自 dev-plan §12.7 的
    「发布阻断」标记，难度↔B 号来自 `test_hard_gate.KNOWN_GAPS`，都不在测试里手抄。
    """
    gc, pr = _load("gen_cases"), _load("prioritize")
    dp = (ROOT / "docs" / "dev-plan.md").read_text(encoding="utf-8")
    blocking = pr.release_blocking(dp)
    assert blocking, "§12.7 的发布阻断集合为空——锚点失效"
    want = {d: b for d, b in gc._known_gaps().items() if b in blocking}
    assert want, f"难度缺口没有一个落在发布阻断集合 {sorted(blocking)} 里——先查 §12.7 标记"
    covered = {e["difficulty"] for e in gc.tc_matrix()}
    missing = {d: b for d, b in want.items() if d not in covered}
    assert not missing, (
        f"发布阻断缺口没有用例卡：{missing}。把难度号加进 `SCENARIO_DIFFICULTY` 会让 TC "
        "编号整体位移，同批更新卡片数字面量与 `docs/qa/automated-test-plan-v2.md` 的 TC 区间")
    book = (ROOT / "docs" / "qa" / "casebook.md").read_text(encoding="utf-8")
    for body in book.split("### TC-")[1:]:
        d = body.splitlines()[0].split("×")[-1].strip().split(" ")[0]
        # 只在缺口仍然开着时要求 P0：卡片转「通过」说明缺口已闭合，那时 §12.7 的
        # 「发布阻断」标记也该由人摘掉，本断言不该反过来逼卡片维持 P0。
        status = body.split("| 通过状态 |", 1)[1].split("\n", 1)[0].strip()
        if d in want and status.startswith("缺口"):
            prio = body.split("| 优先级 |", 1)[1].split("\n", 1)[0]
            assert prio.strip().startswith("P0"), (
                f"{d} 是 §12.7 的发布阻断缺口（{want[d]}），卡片优先级却是 {prio.strip()}")
