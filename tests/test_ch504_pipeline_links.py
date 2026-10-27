r"""CH-504（第十九轮）R19-PIPE-1 / R19-PIPE-2 / R19-PERF-1：交付面与 S3 的三条红线。

R19-PIPE-2（等宽行吞掉链接标注）——`s3_structure.analyze` 的分支顺序里「等宽字体连续段
→ 代码块」排在「块级链接独立成块」之前，于是带 Link 标注的等宽行落进 code 块，
`_attach_link` 永不执行：URL 与内部 GoTo 目标从 IR 和产物里静默消失，而 `ir.warnings`
为空。内部跳转尤其致命——`表 3 参数一览` → `#page=7` 丢失后，正文文字完全看不出这里
曾有交叉引用，违反损失台账契约（C-29 / O-05「诚实暴露而非静默降级」）。本机对 31 篇
夹具扫描 `mono=6 link=2 BOTH=0`：**当前语料零命中**，属潜伏缺陷；红线用合成 `Line`
钉住，不依赖语料里恰好有这样的样本。

R19-PIPE-1（交付 zip 不可复现）——`ZipFile.writestr(str, …)` 用写盘时的墙钟造 ZipInfo，
同一份 IR / md / meta 两次打包给出两个 sha256（条目内容与 CRC 逐字节相同，只有容器变）。
交付物的哈希因此不是其内容的函数：`artifacts._rebuild_artifact_body` 的空操作 PATCH /
rollback 会产出与初次转换不同的 zip，内容寻址校验、备份去重、「产物变没变」的比对全部
拿到假阳性——这也与本函数剔除 `llm_stats`（front-matter 不得污染正文哈希）的理由自相
矛盾。`tests/test_perf_lowend.py::_entry_hashes` 之所以按**条目内容**取哈希，正是在绕
这个洞。

R19-PERF-1（N-8 臂的空集短路）——纯性能，语义必须逐位不变；本文件钉的是**语义**，不是
耗时。理由与 `test_ch498_gate_budget` 同一条纪律：墙钟判据是「按机器快慢变色的断言」，
不进红线；短路本身由「`page_titles` 为空时 `_furniture_key` 在该臂的调用数为 0」这一
可静态复算的量守住。
"""
from __future__ import annotations

import hashlib
import itertools
import time as _time
import zipfile

import pytest

from doc2md.ir import IR, Block
from doc2md.pipeline import s3_structure as s3
from doc2md.pipeline.convert import package
from doc2md.pipeline.s1_parse import Line, RawPage
from doc2md.pipeline.s4_render import render

H = 792.0


def L(text: str, **kw) -> Line:
    return Line(text=text, size=kw.pop("size", 10.5), top=kw.pop("top", 100.0),
                x0=kw.pop("x0", 72.0), bottom=kw.pop("bottom", 112.0),
                explicit_para=True, **kw)


# --- R19-PIPE-2：等宽 vs 链接标注 --------------------------------------------
def _analyze(lines: list[Line], pages: int = 1) -> IR:
    return analyze_pages([lines] * pages)


def analyze_pages(page_lines: list[list[Line]]) -> IR:
    return s3.analyze([RawPage(index=i, lines=ls, height=H)
                       for i, ls in enumerate(page_lines)])


@pytest.mark.gate
def test_mono_link_line_keeps_its_url():
    """等宽的外链行必须落块并带上 `_attach_link`，不得退化成裸围栏。"""
    ir = _analyze([L("https://example.com/spec.pdf", mono=True,
                     link="https://example.com/spec.pdf"),
                   L("Body after.")])
    b = ir.blocks[0]
    assert b.type == "paragraph", [(x.type, x.text) for x in ir.blocks]
    assert b.links and b.links[0]["url"] == "https://example.com/spec.pdf", b.links
    assert "[https://example.com/spec.pdf](https://example.com/spec.pdf)" in render(ir)


@pytest.mark.gate
def test_mono_internal_link_keeps_its_dest():
    """内部 GoTo 目标是本条最严重的形态：丢了以后正文看不出这里曾有交叉引用。"""
    ir = _analyze([L("表 3 参数一览", mono=True, link_internal=True,
                     link_dest="#page=7"),
                   L("Body after.")])
    b = ir.blocks[0]
    assert b.type == "paragraph", [(x.type, x.text) for x in ir.blocks]
    assert b.links and b.links[0]["dest"] == "#page=7", b.links


@pytest.mark.gate
def test_link_line_closes_an_open_mono_run_and_keeps_note_kind():
    """已开的等宽段遇到带链接的行要收块，且**不得**把注文降级成普通链接段。

    这条同时钉住实现选择：修法是给 mono 分支加前件，不是把链接分支整体上移——上移会让
    含链接的注文从 note 块退化成普通段（`docx_04_links_notes` 会转红）。
    """
    ir = _analyze([L("def main():", mono=True),
                   L("    pass", mono=True),
                   L("注 1：本表数据来自年报。", note_kind="footnote"),
                   L("注 2：详见 https://example.com/a 。", note_kind="footnote",
                     link="https://example.com/a")])
    kinds = [(b.type, b.flags.get("note_kind"), bool(b.links)) for b in ir.blocks]
    assert kinds == [("code", None, False),
                     ("paragraph", "footnote", False),
                     ("paragraph", "footnote", True)], kinds


@pytest.mark.gate
def test_mono_link_line_closes_an_open_mono_run():
    """收块臂的前件是「非等宽 **或** 带链接标注」——只有后半截在这里被钉住。

    上一项的带链接行不是等宽行，`not ln.mono` 已足以收块，把 `or ln.link` 删掉它照样
    绿（变异 M2 实测 0 失败）。要让收块臂的后半截有牙齿，后继行必须**既等宽又带链接**：
    此时 `not ln.mono` 为假，删掉后半截就会把该行连同 URL 一起吞进代码块。
    """
    ir = _analyze([L("def main():", mono=True),
                   L("    pass", mono=True),
                   L("spec.pdf", mono=True, link="https://example.com/spec.pdf"),
                   L("Body after.")])
    assert [(b.type, b.text) for b in ir.blocks[:2]] == [
        ("code", "def main():\n    pass"),
        ("paragraph", "spec.pdf")], [(b.type, b.text) for b in ir.blocks]
    assert ir.blocks[1].links and (
        ir.blocks[1].links[0]["url"] == "https://example.com/spec.pdf"), ir.blocks[1].links


@pytest.mark.gate
def test_mono_line_without_link_still_becomes_code():
    """反方向：无链接的等宽行照旧成代码块——前件不得写成「等宽行一律不成块」。"""
    ir = _analyze([L("x = compute(y)", mono=True), L("Body after.")])
    assert ir.blocks[0].type == "code", [(b.type, b.text) for b in ir.blocks]
    assert render(ir).startswith("```\nx = compute(y)\n```")


# --- R19-PIPE-1：交付 zip 的可复现性 -----------------------------------------
def _ir() -> IR:
    return IR(doc_id="a" * 16, source_key="a" * 24, source_name="x.pdf",
              blocks=[Block(id="b1", text="正文", font={"family": "serif", "size": 10.0},
                            links=[{"url": "https://e/x", "text": "正文", "dest": ""}])])


@pytest.mark.gate
def test_package_zip_bytes_are_wall_clock_independent(monkeypatch, tmp_path):
    """把墙钟拨开，两次打包必须逐字节相同。

    变异证明：回到 `writestr(str, …)` 时 date_time 取 `time.localtime(time.time())`，
    下面这个被拨快的 localtime 会让两份 zip 的 sha256 分叉。
    """
    ticks = itertools.count(1_600_000_000, 86_400)      # 每次调用推进一天
    real = _time.localtime
    monkeypatch.setattr(_time, "localtime", lambda t=None: real(next(ticks)))

    ir, md, meta = _ir(), "# 正文\n", {**_ir().meta()}
    digests = []
    for i in (0, 1):
        zp = package(ir, md, dict(meta), tmp_path / f"out{i}", {})
        digests.append(hashlib.sha256(zp.read_bytes()).hexdigest())
    assert digests[0] == digests[1], f"交付 zip 随墙钟漂移：{digests}"


@pytest.mark.gate
def test_package_zip_entry_metadata_is_pinned(tmp_path):
    """逐条目钉住三个易变字段：时间戳、create_system、压缩方式。

    `compress_type` 这条最阴：`ZipFile(…, ZIP_DEFLATED)` 的默认**只**作用于 str 弧名
    那条路径，传 ZipInfo 时取 `ZipInfo.__init__` 的 ZIP_STORED——本机 3.15 实测同一
    条目 compress_size 36 B 对 4,000 B，漏写会让产物静默膨胀两个数量级。
    """
    from doc2md.pipeline.convert import ZIP_ENTRY_CREATE_SYSTEM, ZIP_ENTRY_DATE_TIME

    zp = package(_ir(), "# 正文\n", {}, tmp_path / "out", {})
    infos = zipfile.ZipFile(zp).infolist()
    assert infos, "产物 zip 没有条目"
    for zi in infos:
        assert zi.date_time == ZIP_ENTRY_DATE_TIME == (1980, 1, 1, 0, 0, 0), zi
        assert zi.create_system == ZIP_ENTRY_CREATE_SYSTEM == 0, (
            f"{zi.filename}: create_system={zi.create_system} 随生成机 OS 漂移"
            "（Windows=0 / Unix=3），跨 OS 的产物哈希比对会静默失效")
        assert zi.compress_type == zipfile.ZIP_DEFLATED, (zi.filename, zi.compress_type)


# --- R19-PERF-1：N-8 臂的空集短路（钉语义，不钉墙钟） --------------------------
def _count_furniture_key(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = s3._furniture_key

    def counted(text: str) -> str:
        calls.append(1)
        return real(text)

    monkeypatch.setattr(s3, "_furniture_key", counted)
    return calls


@pytest.mark.gate
def test_n8_arm_short_circuits_when_page_titles_is_empty(monkeypatch):
    """`page_titles` 为空时，N-8 臂一次 `_furniture_key` 都不该付。

    合成页的所有行都在正文带（`_in_margin` 恒 False），故 :341 / :367 / :686 三个
    调用点贡献 0 次——计数里出现的任何一次都只能来自 N-8 臂。
    """
    calls = _count_furniture_key(monkeypatch)
    ir = _analyze([L("第一章 总则", size=18.0), L("正文一。"), L("正文二。")])
    assert calls == [], f"page_titles 为空却付了 {len(calls)} 次 _furniture_key"
    # 语义面：短路不得顺手把标题抬级关掉
    assert ir.blocks[0].type == "heading", [(b.type, b.text) for b in ir.blocks]


@pytest.mark.gate
def test_n8_arm_still_suppresses_repeated_page_title(monkeypatch):
    """反方向：`page_titles` 非空时 N-8 必须照旧生效（宁漏勿误）。

    页眉要**躲过**折平过滤（`_furniture_key` 计数 ≥2 且 `len(key)<=24` 或字号显著小于
    正文就会被剔除）才进得了 `page_titles`，所以这里把页眉字号抬到与正文众数同档；
    单页出现一次不够，`_PAGE_TITLE_REPEAT = 2` 要求跨两页。

    本项守语义，短路本身的隔离证据在上一项（正文带内 `_furniture_key` 调用数 == 0）：
    那里没有页眉行，:341 / :367 / :686 三个调用点贡献 0 次，计数只能来自 N-8 臂。
    """
    calls = _count_furniture_key(monkeypatch)
    t = "综合长文档测试内部测试样本附录索引一览表"
    head = L(t, size=18.0, top=30.0, bottom=42.0)
    ir = analyze_pages([[head, L(t, size=18.0), L("正文一。")],
                        [head, L("正文二。")]])
    assert calls, "_furniture_key 一次都没被调用——计数夹具失效，上一项会变成空跑"
    promoted = [b for b in ir.blocks if b.type == "heading" and b.text.strip() == t]
    assert not promoted, f"page_titles 非空却仍被抬成标题：{[(b.type, b.text) for b in ir.blocks]}"
