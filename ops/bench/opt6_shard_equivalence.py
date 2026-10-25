#!/usr/bin/env python3
r"""ops/bench/opt6_shard_equivalence.py —— OPT-6 第一步：页区间分段的逐字节等价性证明。

**为什么先证再改**：B-38（页区间并行）是 OPT-6 里唯一能改变墙钟量级的杠杆，但它把
「解析顺序」从隐式保证变成需要显式合并的逻辑——块 ID 稳定性、告警去重、跨片接续都是
新失败面。方案（docs/qa/round3/optimization-plan.md OPT-6）要求的出口条件是：**先用
证据说明分段与整篇等价，再谈实施**。本脚本只证不改：它不新增并行路径，只把现有的
串行两阶段路径（`s2.mode=two_phase`）按 1/2/3/4 段切分后与整篇解析对齐到字节。

**五个被测面**（前三条 = OPT-6 点名的三处跨段依赖；第四条是进程级并行新增的读入面；
第五条是本步骤实测**新发现**的第四处依赖，登记为 R18-PIPE-2）：
- D0 页区间读取 ≡ 整本读取切片：`read_any(page_span=(lo,hi))` 与 `read_any()[lo:hi]`
  必须同形（B-38 的 worker 只读自己那段页，`RawPage.index` 必须保持绝对页序）；
- D1 跨页折行并回：金样本的片界可能恰好落在两个完整段之间，那时「等价」并不能证明
  并回逻辑被行使。牙齿来自一个**必然折断**的样本（每页末尾留一条逗号收尾的半句）：
  缝合吸收或截断替换至少一条通道必须真的触发，且产物仍与整篇逐字节相同；
- D2 `modal_size` 全文档众数：`base_size` 必须整篇算一次再传每片（CH-471 / PIPE-1）。
  320 页金样本是均匀字体文档，分段众数与全局众数**恰好相等**，所以该面的牙齿来自
  一个混合字号负夹具：负夹具上「各片自取众数」必须与整篇结果**不同**（证明判据有
  牙齿），「全局众数」必须与整篇结果**相同**（证明口径正确）；
- D3 S1.5 门禁的全量判定：门禁必须在切段**之前**吃整本 `pages_all`
  （`all_pages_empty`、空页占比、连续坏页成段都依赖全局页序）。两条判据：
  每种段数的 `gate` 明细逐字节相同且覆盖全部 320 页；再对 `convert()` 源码做静态
  顺序断言（门禁调用在 `shard_plan` 之前，且实参是 `pages_all` 本身而不是片切片）；
- D4 页眉页脚重复计数的窗口敏感性：`_strip_page_furniture` 的「同一边距行在 ≥2 页
  重复」按**传入页数**计数，片窗口 ≠ 整本窗口时正文会被误删。首轮实测（修复前）：
  整篇 5770 块、160 页窗口 5775 块、正文首处差异在页 14/15 的折行残句
  （「…本条款应当遵循补」+「下情形涉及例外情况。」错接，「充说明。」静默消失），
  差异位置**与片界无关**——说明它不是缝合缺陷，而是跨段依赖漏登记。

**判据（逐字节，不是相似度）**：现有门禁 `test_v24_gates.py::test_shard_two_phase`
用的是 `char_similarity ≥ 0.99` + 块数相等——0.99 允许约 210 字误差，一份重复段落能
在红线下静默通过。本脚本收紧到字节：
- 正文面 `content.md`（`Result.markdown`）：**零豁免**逐字节相同；
- 结构面 `ir.json`（`IR.to_dict(for_export=True)`）：剥掉**审阅专用**的分片留痕
  （`flags.shard_edge` / `absorbed_ids` / `dropped_dup_of`、`provenance` 里的
  `s4:shard_stitch`、meta 的 `sharded` / `shard_count` / `warnings`）后逐字节相同；
  剥了什么、各剥几处，一律打印——豁免面必须可见，不得静默；
- 告警集合：与整篇的差集只允许 W1044（分片转换说明）与 W1043（跨片表格未合并），
  其余任何告警增减都判不等价；
- 门禁明细 `gate`：逐字节相同。

用法：
    .venv/bin/python ops/bench/opt6_shard_equivalence.py
        [--sample tests/golden/large_320p.pdf] [--segments 1,2,3,4] [--json /tmp/opt6.json]

退出码：0=四面全部等价（OPT-6 第一步出口条件达成，可进入 B-38 裁定）；
1=任一面不等价（打印差异面与计数）；2=样本缺失或环境不满足。

耗时：金样本 4 档整本转换 + 整读/段读 ≈ 1 分钟量级（表内打印实测墙钟），属重测量，
不进 pytest 门禁（`test_ch498_gate_budget` 的 heavy 口径禁止门禁项拉 `large_*` 夹具）。
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

#: 分片留痕里**只服务审阅**的键：它们记录「缝合在哪里发生过」，不记录正文。
#: 单片路径恒为假/空，多片路径按片界分布——段数不同则形态不同，故必须在字节比对前
#: 显式豁免并计数打印（见模块 docstring 的「豁免面必须可见」）。
SEAM_FLAG_KEYS = ("shard_edge", "absorbed_ids", "dropped_dup_of")
SEAM_PROVENANCE = "s4:shard_stitch"
SEAM_META_KEYS = ("sharded", "shard_count")
#: 允许出现在告警差集里的信息性告警：W1044 分片说明、W1043 跨片表格未合并。
INFO_WARNINGS = ("W1043", "W1044")


def load_cfg():
    """与 tests/conftest.py::cfg 同口径：在隔离 cwd 下 load，避免本机恰有
    ./doc2md.toml 时把开发者配置渗进测量。"""
    from doc2md import config
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="opt6-cfg-")
    try:
        os.chdir(tmp)
        cfg = config.load()
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    return cfg


def span_size(pages: int, segments: int) -> int:
    """把 pages 切成**恰好** segments 段所需的 `s2.max_pages_per_shard`。

    `shard_plan` 按 `start = end` 每次推进 max_pages，故段数 = ceil(pages/max_pages)；
    取 ceil(pages/segments) 即目标段数。重叠页（默认 2）由缝合去重处理，不改段数。
    """
    return max(1, math.ceil(pages / max(1, segments)))


def normalize_export(res) -> tuple[bytes, dict]:
    """返回 (ir.json 归一化字节, 豁免计数)。豁免只动分片留痕，正文与结构不动。"""
    d = res.ir.to_dict(for_export=True)
    exempt: dict = {"meta": {}, "flags": {k: 0 for k in SEAM_FLAG_KEYS},
                    "provenance": 0, "warnings": []}
    meta = d.get("meta") or {}
    for k in SEAM_META_KEYS:
        if k in meta:
            exempt["meta"][k] = meta.pop(k)
    for w in meta.pop("warnings", []) or []:
        exempt["warnings"].append(w)
    for b in d.get("blocks") or []:
        flags = b.get("flags") or {}
        for k in SEAM_FLAG_KEYS:
            # 只数**真正留下**的痕迹：`Block.to_dict` 恒写 `shard_edge: false` /
            # `absorbed_ids: []`，按「键存在」计数会把 5775 个空形态报成豁免面。
            if flags.get(k):
                exempt["flags"][k] += 1
            flags.pop(k, None)
        prov = b.get("provenance") or {}
        modified = prov.get("modified_by") or []
        if SEAM_PROVENANCE in modified:
            prov["modified_by"] = [m for m in modified if m != SEAM_PROVENANCE]
            exempt["provenance"] += 1
        b["flags"] = flags
    return json.dumps(d, ensure_ascii=False).encode("utf-8"), exempt


def run_convert(sample: Path, cfg: dict, segments: int, out_root: Path) -> dict:
    """跑一档段数并抽出四个可比面。segments=1 走整篇（mode=single）。"""
    import copy

    from doc2md.pipeline.convert import convert
    c = copy.deepcopy(cfg)
    mode = "single" if segments == 1 else "two_phase"
    if segments > 1:
        c["s2"]["max_pages_per_shard"] = span_size(_page_count(sample), segments)
    out_dir = out_root / f"seg{segments}"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    res = convert(sample, cfg=c, mode=mode, out_dir=out_dir)
    wall = round(time.monotonic() - t0, 2)
    ir_bytes, exempt = normalize_export(res)
    stats = res.meta.get("shard_stats") or {}
    return {
        "segments": segments,
        "actual_shards": int(res.meta.get("shard_count") or 1),
        "fallback": bool(stats.get("fallback")),
        "seams": int(stats.get("seams") or 0),
        "stitched": int(stats.get("stitched") or 0),
        "dropped_dupes": int(stats.get("dropped_dupes") or 0),
        "blocks": len(res.ir.blocks),
        "pages": res.ir.pages,
        "wall_s": wall,
        "markdown_bytes": res.markdown.encode("utf-8"),
        "md_len": len(res.markdown.encode("utf-8")),
        "ir_bytes": ir_bytes,
        "ir_raw_bytes": json.dumps(res.ir.to_dict(for_export=True),
                                   ensure_ascii=False).encode("utf-8"),
        "exempt": exempt,
        "warnings": sorted(res.ir.warnings),
        "gate_bytes": json.dumps(res.gate, ensure_ascii=False,
                                 sort_keys=True).encode("utf-8"),
        "gate_pages": len((res.gate or {}).get("page_detail") or []),
        "res": res,
    }


def _page_count(sample: Path) -> int:
    import pdfplumber
    with pdfplumber.open(str(sample)) as pdf:
        return len(pdf.pages)


# --- D0：页区间读取 ≡ 整本读取切片 ------------------------------------------------
def _page_json(pages) -> bytes:
    return json.dumps([dataclasses.asdict(p) for p in pages],
                      ensure_ascii=False, default=str).encode("utf-8")


def check_page_span_read(sample: Path, pages_all, spans) -> dict:
    """每段独立 open 只读自己的页区间，与整本读取后切片必须逐字节同形。"""
    from doc2md.pipeline.s1_parse import read_any
    out = {"spans": len(spans), "identical": 0, "diff": []}
    for lo, hi in spans:
        sub = read_any(sample, page_span=(lo, hi), fmt="pdf")
        base = pages_all[lo:hi]
        if _page_json(sub) == _page_json(base):
            out["identical"] += 1
        else:
            first = None
            if len(sub) == len(base):
                first = next((i for i in range(len(sub))
                              if _page_json([sub[i]]) != _page_json([base[i]])), None)
            out["diff"].append({"span": [lo, hi], "pages": [len(sub), len(base)],
                                "first_diff_page": first})
    return out


# --- D2 负夹具：混合字号文档上「各片自取众数」必须与整篇不同 --------------------------
# --- D4：家具探测的窗口敏感性（R18-PIPE-2 发现的第四处跨段依赖） ----------------------
def check_window_sensitivity(pages_all, window: int, overlap: int) -> dict:
    """同一段页区间，**带重叠尾窗**解析与整篇解析必须同形。

    `_strip_page_furniture` 的「同一边距行在 ≥2 页重复」按**传进来的页数**计数：窗口越宽
    越容易把正文里合法重复出现的短句聚成「页眉」。分片路径交给 S3 的是片窗口而不是整本，
    所以这一条决定 B-38 的 worker 能否只拿自己那段页——它不在 OPT-6 原来点名的三条依赖里，
    是本步骤的实测新增（R18-PIPE-2）。窗口取 `[0, window+overlap)`，即分片实际携带重叠页
    的形态；只比对落在 `[0, window)` 内的块，重叠页自身的归属差异不混进这一面。
    """
    from doc2md.pipeline.s3_structure import analyze, modal_size

    gm = modal_size(pages_all)
    whole = [(b.type, b.page, b.level, b.text)
             for b in analyze(pages_all, base_size=gm).blocks if (b.page or 0) < window]
    wind = [(b.type, b.page, b.level, b.text)
            for b in analyze(pages_all[0:window + overlap], base_size=gm).blocks
            if (b.page or 0) < window]
    diff = [i for i in range(min(len(whole), len(wind))) if whole[i] != wind[i]]
    return {"window": window, "overlap": overlap, "blocks_whole": len(whole),
            "blocks_window": len(wind), "diff_positions": len(diff),
            "first_diff": None if not diff else {"whole": whole[diff[0]][:4],
                                                 "window": wind[diff[0]][:4]},
            "identical": not diff and len(whole) == len(wind)}


# --- D1 牙齿：片界必然落在句中断续处的样本 ------------------------------------------
def seam_split_pdf(path: Path, pages: int = 6) -> None:
    """每页 9 句完整句 + 1 行以逗号收尾（`续，`）→ 片界必然落在句中断续处。

    构造口径取自 `tests/test_v24_gates.py::_mid_sentence_pdf`（CH-456 / T8），这里只把
    页数从 4 提到 6，好让 2/3 段档都有非平凡片界。不设标题行：片首块须与上一片片尾块
    同字号，大标题会挡掉 `_continuation` 的字体分支。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas as rl_canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = rl_canvas.Canvas(str(path), pagesize=A4)
    _w, height = A4
    for page in range(pages):
        c.setFont("STSong-Light", 10.5)
        y = height - 4 * cm
        for i in range(9):
            c.drawString(2 * cm, y,
                         f"第{page + 1}页第{i + 1}个完整句子，句末以句号收尾，内容足够长。")
            y -= 0.6 * cm
        c.drawString(2 * cm, y, "续，")
        c.showPage()
    c.save()


def check_seam_teeth(out_dir: Path, cfg: dict, *, segments: int = 3) -> dict:
    """金样本的片界可能恰好落在完整段之间（那样「并回」根本没被行使）；这里用必然
    折断的样本补上牙齿：缝合吸收或截断替换**至少一条必须真的发生**，且产物仍与整篇
    逐字节相同。"""
    import copy

    from doc2md.pipeline.convert import convert
    p = out_dir / "seam_split.pdf"
    seam_split_pdf(p)
    total = _page_count(p)
    c = copy.deepcopy(cfg)
    c["s2"]["max_pages_per_shard"] = span_size(total, segments)
    c["s2"]["shard_overlap_pages"] = 1
    single = convert(p, cfg=c, mode="single")
    shard = convert(p, cfg=c, mode="two_phase")
    st = shard.meta.get("shard_stats") or {}
    ir_shard, _ = normalize_export(shard)
    ir_base, _ = normalize_export(single)
    return {"pages": total, "segments": segments,
            "shards": int(shard.meta.get("shard_count") or 1),
            "seams": int(st.get("seams") or 0), "stitched": int(st.get("stitched") or 0),
            "dropped_dupes": int(st.get("dropped_dupes") or 0),
            "repair_fired": (int(st.get("stitched") or 0) + int(st.get("dropped_dupes") or 0)) > 0,
            "md_equal": shard.markdown == single.markdown,
            "ir_equal": ir_shard == ir_base,
            "fallback": bool(st.get("fallback"))}


def mixed_font_pdf(path: Path, *, pages: int = 8, big: float = 12.0,
                   small: float = 7.0, probe: float = 9.0) -> None:
    """前一半页正文 big、后一半页正文 small，每页再钉一条 `probe` 号粗体编号行。

    三个纪律让这一档**只测 base_size**，不混入别的跨段依赖：
    ① 每行都以句末点号收尾（不留跨页续写，片界落在完整段之间）；
    ② 每行文本含唯一编号（不会被「≥2 页重复」的页眉页脚信号命中），且全部落在
       200–740 的页中带（不进取边带，见 `_in_margin` 的上下 10%/12%）；
    ③ big 页比 small 页多写正文行——按字符加权的**全局**众数因此稳取 big，
       不必依赖同权时的先现序。

    `probe` 行是 base_size 敏感性探针（没有它这一档两个口径产出同形、断言空真）：
    取正确口径（全局众数 big）时 probe < big，编号行过不了 `size >= base + 0.8`
    的字号门，只作正文；若某片自取众数拿到 small（CH-471 修掉的漂移形态），同一行
    就晋升成标题。行尾点号是有意留下的——它封掉「短行无句末标点」那条不看字号的
    回退臂，让差异**只**能来自 base_size。
    """
    from reportlab.pdfgen import canvas as rl_canvas
    c = rl_canvas.Canvas(str(path), pagesize=(595, 842), invariant=1)
    for i in range(pages):
        big_half = i < pages // 2
        size = big if big_half else small
        n_body = 22 if big_half else 14
        c.setFont("Helvetica-Bold", 16)
        c.drawString(60, 740, f"Heading alpha {i + 1}")
        c.setFont("Helvetica", size)
        y = 700
        for w in range(n_body):
            c.drawString(60, y, f"sentence beta {i + 1}-{w + 1} body content sample.")
            y -= size + 8
        c.setFont("Helvetica-Bold", probe)
        c.drawString(60, max(200.0, y), f"{i + 1}. zone marker gamma.")
        c.showPage()
    c.save()


def check_modal_dependency(mixed: Path, cfg: dict) -> dict:
    """混合字号样本上三方向：整篇 = 分段（全局众数）≠ 分段（各片自取众数）。"""
    import copy

    from doc2md.pipeline.convert import convert
    from doc2md.pipeline.s1_parse import read_any
    from doc2md.pipeline.s3_structure import analyze, modal_size
    from doc2md.pipeline.sharding import shard_plan, stitch_shards

    # 8 页样本默认 300 页/片只会得到 1 段（等于没分段）；强制 2 段才谈得上漂移。
    c = copy.deepcopy(cfg)
    pages_all = read_any(mixed, fmt="pdf")
    total = len(pages_all)
    max_pages = max(1, math.ceil(total / 2))
    c["s2"]["max_pages_per_shard"] = max_pages
    c["s2"]["shard_overlap_pages"] = 2
    overlap = int(c["s2"]["shard_overlap_pages"])
    single = convert(mixed, cfg=c, mode="single")
    shipped = convert(mixed, cfg=c, mode="two_phase")

    spans = shard_plan(total, max_pages, overlap)
    naive_irs = []
    for si, (lo, hi) in enumerate(spans):
        sub = pages_all[lo:hi]
        ir = analyze(sub, base_size=modal_size(sub))        # 错误口径：本片自己的众数
        ir.doc_id, ir.source_key = single.ir.doc_id, single.ir.source_key
        ir.source_name, ir.format = single.ir.source_name, "pdf"
        ir.pages = len(sub)
        ir.extra_meta["shard"] = {"index": si, "start": lo, "end": hi}
        naive_irs.append(ir)
    naive_ir, _stats = stitch_shards(naive_irs, max_pages, dedupe=True)

    def shape(ir) -> bytes:
        return json.dumps([(b.id, b.type, b.level, b.page, b.text) for b in ir.blocks],
                          ensure_ascii=False).encode("utf-8")

    global_modal = round(modal_size(pages_all), 2)
    drift = [round(modal_size(pages_all[lo:hi]), 2) for lo, hi in spans]
    return {
        "spans": [list(s) for s in spans],
        "global_modal": global_modal,
        "per_shard_modal": drift,
        "modal_drift": any(d != global_modal for d in drift),
        "sharded_equals_single": shape(shipped.ir) == shape(single.ir),
        "naive_differs_from_single": shape(naive_ir) != shape(single.ir),
    }


# --- D3 静态顺序断言：门禁必须在切段之前吃整本 pages_all -------------------------------
def check_gate_ordering() -> dict:
    """AST 断言，不看运行结果：`_convert` 里 `s1_5_gate.check` 的调用行必须早于
    `shard_plan`，且首个实参必须是 `pages_all` 这个名字（不是 `sub` / 切片）。
    这条是「全量判定」的结构性保证——顺序一旦被挪，段内门禁就会接管裁决。"""
    src = (REPO / "doc2md" / "pipeline" / "convert.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_convert"), None)
    if fn is None:
        return {"ok": False, "reason": "convert.py 里找不到 _convert"}
    gate_lines, plan_lines, gate_arg = [], [], None
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or (f.id if isinstance(f, ast.Name) else None)
            dotted = ""
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                dotted = f"{f.value.id}.{f.attr}"
            if name == "check" and dotted == "s1_5_gate.check":
                gate_lines.append(node.lineno)
                a = node.args[0] if node.args else None
                gate_arg = getattr(a, "id", type(a).__name__ if a else None)
            if name == "shard_plan":
                plan_lines.append(node.lineno)
    return {"gate_calls": gate_lines, "shard_plan_calls": plan_lines,
            "gate_first_arg": gate_arg,
            "ok": bool(gate_lines) and bool(plan_lines)
            and max(gate_lines) < min(plan_lines) and gate_arg == "pages_all"}


def main() -> int:
    ap = argparse.ArgumentParser(description="OPT-6 第一步：分段与整篇逐字节等价性证明")
    ap.add_argument("--sample", default="tests/golden/large_320p.pdf")
    ap.add_argument("--segments", default="1,2,3,4")
    ap.add_argument("--json", help="把完整取证写成 JSON（默认只打印表格）")
    args = ap.parse_args()

    sample = (REPO / args.sample) if not Path(args.sample).is_absolute() else Path(args.sample)
    if not sample.is_file():
        print(f"[opt6] 样本缺失：{sample}\n       先跑 .venv/bin/python tests/make_golden.py")
        return 2
    targets = [int(x) for x in args.segments.split(",") if x.strip()]

    cfg = load_cfg()
    out_root = Path(tempfile.mkdtemp(prefix="opt6-equiv-"))
    failures: list[str] = []
    rows: list[dict] = []
    try:
        total_pages = _page_count(sample)
        print(f"[opt6] 样本 {sample.name} 页数={total_pages} 段数档={targets}")

        from doc2md.pipeline.s1_parse import read_any
        from doc2md.pipeline.sharding import shard_plan
        t0 = time.monotonic()
        pages_all = read_any(sample, fmt="pdf")
        whole_read_s = round(time.monotonic() - t0, 2)
        overlap = int(cfg["s2"].get("shard_overlap_pages", 2))

        results: dict[int, dict] = {}
        for n in targets:
            r = run_convert(sample, cfg, n, out_root)
            results[n] = r
            rows.append({k: v for k, v in r.items() if k not in ("res", "markdown_bytes",
                                                                 "ir_bytes", "ir_raw_bytes", "gate_bytes")})
            print(f"[opt6] 段数={n} 实得 shards={r['actual_shards']} 块={r['blocks']} "
                  f"seams={r['seams']} stitched={r['stitched']} 去重={r['dropped_dupes']} "
                  f"墙钟={r['wall_s']}s 降级={r['fallback']}")
            if n > 1 and r["actual_shards"] != n:
                failures.append(f"段数档 {n} 实得 {r['actual_shards']} 段（分段口径失控）")
            if r["fallback"]:
                failures.append(f"段数档 {n} 走了降级单片（非真分片，等价性未被检验）")

        base = results.get(1) or results[min(results)]
        label = base["segments"]
        for n, r in results.items():
            if n == label:
                continue
            if r["markdown_bytes"] != base["markdown_bytes"]:
                first = next((i for i in range(min(len(r["markdown_bytes"]), len(base["markdown_bytes"])))
                              if r["markdown_bytes"][i] != base["markdown_bytes"][i]), None)
                failures.append(f"正文面不等价：{label} 段 vs {n} 段"
                                f"（字节 {first}，长度 {len(base['markdown_bytes'])}→{len(r['markdown_bytes'])}）")
            if r["ir_bytes"] != base["ir_bytes"]:
                off = next((i for i in range(min(len(r["ir_bytes"]), len(base["ir_bytes"])))
                            if r["ir_bytes"][i] != base["ir_bytes"][i]), None)
                failures.append(f"结构面不等价：{label} 段 vs {n} 段（剥豁免后首个差异在字节 {off}，"
                                f"长度 {len(base['ir_bytes'])}→{len(r['ir_bytes'])}，"
                                f"块数 {base['blocks']}→{r['blocks']}）")
            if r["gate_bytes"] != base["gate_bytes"]:
                failures.append(f"门禁明细不等价：{label} 段 vs {n} 段")
            extra = sorted(set(r["warnings"]) - set(base["warnings"]))
            missing = sorted(set(base["warnings"]) - set(r["warnings"]))
            if extra and not set(extra) <= set(INFO_WARNINGS):
                failures.append(f"告警集合多出非信息性项：{extra}")
            if missing:
                failures.append(f"告警集合缺失：{missing}")
            if r["gate_pages"] != total_pages:
                failures.append(f"D3 空真：{n} 段档的门禁明细只覆盖 {r['gate_pages']} 页，"
                                f"应为整本 {total_pages} 页（全量判定失守）")

            # 豁免可见性：分片留痕剥了多少处，必须打印而不是静默
            raw_same = r["ir_raw_bytes"] == base["ir_raw_bytes"]
            print(f"[opt6]   {label}→{n} 段：正文 {r['md_len']}B 等价="
                  f"{r['markdown_bytes'] == base['markdown_bytes']}；"
                  f"结构（剥豁免）等价={r['ir_bytes'] == base['ir_bytes']}"
                  f"（未剥时{'本就相同' if raw_same else '有差异'}）；"
                  f"豁免分片留痕 flags={r['exempt']['flags']} "
                  f"provenance={r['exempt']['provenance']} 告警={r['exempt']['warnings']}")

        # D1 牙齿：金样本的片界可能恰好落在完整段之间，用必然折断的样本补上
        d1 = check_seam_teeth(out_root, cfg, segments=3 if len(targets) > 1 else 2)
        if d1["fallback"]:
            failures.append("D1 牙齿样本走了降级单片，并回逻辑未被行使")
        if not d1["repair_fired"]:
            failures.append(f"D1 空真：牙齿样本 {d1['seams']} 个片界，"
                            f"缝合吸收 {d1['stitched']} 条、截断替换 {d1['dropped_dupes']} 条，"
                            f"两条并回通道都没触发")
        if not d1["md_equal"] or not d1["ir_equal"]:
            failures.append(f"D1 不等价：牙齿样本（{d1['pages']} 页 / {d1['shards']} 段）"
                            f"正文等价={d1['md_equal']} 结构等价={d1['ir_equal']}")
        print(f"[opt6] D1 牙齿样本：{d1['pages']} 页 {d1['shards']} 段 "
              f"seams={d1['seams']} stitched={d1['stitched']} "
              f"替换={d1['dropped_dupes']} 正文等价={d1['md_equal']} "
              f"结构等价={d1['ir_equal']}")

        # D0：用最细一档的片区间做独立读取比对
        finest = max(results)
        spans = shard_plan(total_pages, span_size(total_pages, finest), overlap) \
            if finest > 1 else [(0, total_pages)]
        d0 = check_page_span_read(sample, pages_all, spans)
        if d0["diff"]:
            failures.append(f"D0 页区间读取与整读切片不同形：{d0['diff']}")
        print(f"[opt6] D0 页区间读取：{d0['identical']}/{d0['spans']} 段同形"
              f"（整本读取 {whole_read_s}s）")

        # D4：片窗口 vs 整篇的 S3 同形性（家具重复计数窗口）
        d4 = check_window_sensitivity(pages_all, span_size(total_pages, finest)
                                      if finest > 1 else total_pages, overlap)
        if not d4["identical"]:
            failures.append(f"D4 片窗口与整篇不同形：{d4['diff_positions']} 处不齐，"
                            f"块数 {d4['blocks_whole']}→{d4['blocks_window']}，"
                            f"首处 {d4['first_diff']}")
        print(f"[opt6] D4 窗口敏感性：整篇窗口内 {d4['blocks_whole']} 块 / "
              f"片窗口（{d4['window']}+{d4['overlap']} 页）{d4['blocks_window']} 块 "
              f"不齐 {d4['diff_positions']} 处 同形={d4['identical']}")

        # D2：金样本的众数漂移 + 混合字号负夹具的牙齿
        from doc2md.pipeline.s3_structure import modal_size
        modal_per_shard = {}
        for lo, hi in spans:
            modal_per_shard[f"{lo}-{hi}"] = round(modal_size(pages_all[lo:hi]), 2)
        global_modal = round(modal_size(pages_all), 2)
        print(f"[opt6] D2 金样本全局众数={global_modal} 各段众数="
              f"{sorted(set(modal_per_shard.values()))}（均匀字体：漂移=0 是预期，"
              f"牙齿来自负夹具）")

        mixed = out_root / "mixed_font.pdf"
        mixed_font_pdf(mixed)
        d2 = check_modal_dependency(mixed, cfg)
        if not d2["sharded_equals_single"]:
            failures.append("D2 负夹具：全局众数口径下分段仍不等于整篇（CH-471 回归）")
        if not d2["naive_differs_from_single"]:
            failures.append("D2 负夹具无牙齿：各片自取众数与整篇相同，说明该面断言为空真")
        if not d2["modal_drift"]:
            failures.append("D2 负夹具未产生众数漂移：各段众数与全局一致，牙齿不成立")
        print(f"[opt6] D2 负夹具：全局众数={d2['global_modal']} 各段众数="
              f"{d2['per_shard_modal']} 漂移={d2['modal_drift']} "
              f"分段≡整篇={d2['sharded_equals_single']} 各片自取众数≠整篇="
              f"{d2['naive_differs_from_single']}")

        d3 = check_gate_ordering()
        if not d3["ok"]:
            failures.append(f"D3 门禁顺序断言失败：{d3}")
        print(f"[opt6] D3 静态顺序：门禁行={d3.get('gate_calls')} 切段行="
              f"{d3.get('shard_plan_calls')} 实参={d3.get('gate_first_arg')} ok={d3['ok']}；"
              f"明细覆盖页数（各档）={[results[n]['gate_pages'] for n in targets]}")
    finally:
        shutil.rmtree(out_root, ignore_errors=True)

    print()
    print("| 档 | 实得段 | 块 | seams | stitched | 去重 | 正文 B | 墙钟 s |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['segments']} | {r['actual_shards']} | {r['blocks']} | {r['seams']} "
              f"| {r['stitched']} | {r['dropped_dupes']} | {r['md_len']} | {r['wall_s']} |")
    print(f"\n[opt6] 平均墙钟 {mean(r['wall_s'] for r in rows):.2f}s "
          f"（测量止句：耗时不入门禁红线，见 12.1 口径）")
    if failures:
        print(f"[opt6] **不等价 {len(failures)} 项**")
        for f in failures:
            print(f"  - {f}")
    else:
        print("[opt6] 五面（D0 读取 / D1 接续 / D2 众数 / D3 门禁 / D4 窗口）逐字节等价，"
              "OPT-6 第一步出口条件达成 → 可进入 B-38 第二步裁定")

    if args.json:
        payload = {"sample": sample.name, "pages": total_pages, "segments": targets,
                   "whole_read_s": whole_read_s, "rows": rows,
                   "d0": d0, "d1": d1, "d2": d2, "d3": d3, "d4": d4,
                   "modal": {"global": global_modal, "per_span": modal_per_shard},
                   "failures": failures}
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"[opt6] JSON 已写：{args.json}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
