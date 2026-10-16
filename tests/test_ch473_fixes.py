"""tests/test_ch472_fixes.py —— CH-473 第五轮审查回归红线（批次①②）。

覆盖本批次实施的正确性 / 数据安全 / 健壮性修复（逐项对应登记编号）：
- PIPE-5：seam 尾缀判定加**相邻页约束**——页不相邻的重复短句（「特此说明。」
  恰是前片某长段的结尾）不再被整块误删（G1「分片≡单片」数据面）；
- PIPE-6：seam 截断替换加**片级锚定**——同页同片的真实短块（列表项「注意：」+
  后续更长块）不得被「完整版」替换；
- PIPE-7：配了空窗上限时，串行快路径也落到单 worker 进程池（挂起件可被记账）；
- X-17：run_batch 返回前本池 worker 全部终止并回收（X-16 的 abandoned-only
  修复不完整——正常完成路径同样在解释器退出阶段死锁，见下）；
- PIPE-10：`_fallback_single` 死参 `head` 删除（签名面回归）；
- STOR-1：force 覆盖时同事务清 `edit_audit`——rollback 不得把覆盖前旧文本复活；
- STOR-2：work/serve 启动 FTS 对账（含失败不阻断启动）；
- STOR-3：COMMIT 自身失败（ENOSPC）显式 ROLLBACK；body 已炸时回滚失败不顶替病因；
- STOR-4：sweep_tmp 在途保护窗（mtime 在 stale_job_minutes 内的条目跳过）；
- SEC-1：`api.allow_remote_bind` 列入运行期不可变键——两步 PUT 掏空 loopback 防线被堵；
- SEC-2：`serve --host` 非回环被拒（CLI 面与配置面同口径，opt-in 放行）；
- SVC-3：`/search` 拒负 limit；SVC-4：PUT /settings 锁内校验（非法整体拒绝）；
- SVC-5：override 落盘三段式（内容 fsync → rename → 目录 fsync）；
- SVC-6：force 的字符串假值（"false"/"no"/"0"）不放行覆盖用户编辑；
- SVC-7：同步进程池全局信号量——池的整个生命周期在临界区内；
- SVC-8：cron.run 正常路径关闭 Store；
- Q-1：八进制权限同时接受 "700" 与 0o700 两种形态；Q-2：netguard 解析缓存封顶；
- OPS-1：外置备份写失败不留半截 `.enc`；OPS-2：GGUF BOOL 短读收敛为 GgufError；
- LLM-2：apply_ops 索引字典化（split 新块入字典，后续 op 可寻址）；
- TEST-5：DB 迁移链——存量 v3 库开库升 v5（结构/凭证哈希/索引行保留/幂等），
  v2.2 遗留库（无 revision / 无 role / FTS 无 aux）补列 + FTS 重建回填。

方法论：块级行为直接构造 Block 调 `overlap_dedupe`（确定性、无 IO）；API 面走
真实 Flask test client；存储/配置面用真实 sqlite 与内存配置；并发.futures 与
Flask.run 等外圈用 monkeypatch 打桩。均不触网。
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import shutil
import sqlite3
import struct
import time

import pytest

from doc2md.ir import IR, Block
from doc2md.pipeline.sharding import fingerprint, overlap_dedupe, stitch_shards

pytestmark = [pytest.mark.milestone("DoD")]


def _blk(bid, text, page, btype="paragraph", size=10.0, family="F"):
    return Block(id=bid, type=btype, text=text, page=page,
                 font={"family": family, "size": size})


def _ir(blocks, start, end, index=0):
    ir = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=end - start)
    ir.blocks = blocks
    ir.extra_meta["shard"] = {"index": index, "start": start, "end": end}
    return ir


def _cfg_with(**paths):
    """从 loader._base() 取一份完整默认配置，按 dotted 路径写入值后返回。"""
    from doc2md.config import loader
    cfg = loader._base()
    for dotted, val in paths.items():
        cur = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return cfg


def _h(tokens, role="admin"):
    return {"X-Doc2md-Token": tokens[role]}


# 两片、2 页重叠：overlap_dedupe 的片界窗口 = {298, 299}
_SPANS = [(0, 300), (298, 600)]


# ------------------------------------------------------------------ PIPE-5

@pytest.mark.gate
def test_pipe5_cross_page_seam_suffix_still_dropped():
    """回归（不收紧过度）：真实的跨页续写尾缀（b.page == a.page + 1）仍判伪影丢弃。"""
    head = _blk("b1", "第一段前半。第二段起始内容，第二段一直续写越过片界。", 297)
    frag = _blk("b2", "第二段起始内容，第二段一直续写越过片界。", 298)
    tail_blk = _blk("b3", "独立段落。", 298)
    out = overlap_dedupe([head, frag, tail_blk], _SPANS)
    assert out == [head, tail_blk]
    assert frag.flags["dropped_dup_of"] == fingerprint(head)


@pytest.mark.gate
def test_pipe5_nonadjacent_repeated_short_sentence_kept():
    """修复断言：页**不相邻**的重复短句不得判为尾缀整块误删。

    「特此说明。」既是前片某长段的结尾、又作为独立短句出现在后片窗口页——
    旧实现（无相邻页约束）按「b 是 a 的真后缀」把它删掉：它不是任何段落的
    尾缀，是真实内容（静默丢字 + 分片≠单片，违反 G1）。
    """
    head = _blk("b1", "本合同自双方签署之日起生效，特此说明。", 100)
    repeat = _blk("b2", "特此说明。", 298)
    out = overlap_dedupe([head, repeat], _SPANS)
    assert out == [head, repeat]
    assert not repeat.flags.get("dropped_dup_of")


def test_pipe5_same_page_repeated_short_sentence_kept():
    """同页重复短句同样不删（既有 `a.page >= b.page` 守卫，相邻页约束不破坏它）。"""
    head = _blk("b1", "前文详见附件，特此说明。", 298)
    repeat = _blk("b2", "特此说明。", 298)
    out = overlap_dedupe([head, repeat], _SPANS)
    assert out == [head, repeat]


def test_pipe5_long_paragraph_crossing_three_pages_not_regressed():
    """跨 3 页续写段落（片界落在第 2 页中）：尾缀块在起始页的下一页，约束不误伤。

    a/b 是前片产出的两份完整文本（起始页 296 与跨页中段 297），c 是后片从
    窗口页 298 起解析得到的尾缀——c 满足相邻页约束（298 == 297+1）仍判伪影，
    而 a/b 都在窗口外原样保留。收紧只挡「页不相邻的重复短句」这一类。
    """
    a = _blk("b1", "长段落开头。中段内容。", 296)
    b = _blk("b2", "中段内容。尾段内容。", 297)
    c = _blk("b3", "尾段内容。", 298)
    out = overlap_dedupe([a, b, c], _SPANS)
    assert out == [a, b]
    assert c.flags["dropped_dup_of"] == fingerprint(b)


# ------------------------------------------------------------------ PIPE-6

@pytest.mark.gate
def test_pipe6_anchored_shard_boundary_truncation_replaced():
    """修复保留行为：片界截断版（某片最后一块）仍由后片完整版替换。"""
    cut = _blk("b1", "跨片段落的前半。", 298)
    full = _blk("b2", "跨片段落的前半。后半内容。", 298)
    nxt = _blk("b3", "另一段。", 299)
    out = overlap_dedupe([cut, full, nxt], _SPANS, shard_bounds=[1, 3])
    assert out == [full, nxt]
    assert cut.flags["dropped_dup_of"] == fingerprint(full)
    assert full.flags.get("dropped_dup_of") is None


@pytest.mark.gate
def test_pipe6_real_short_block_before_longer_not_replaced():
    """修复断言：同页同片的真实短块不得被「完整版」替换。

    列表项「注意：」与紧随的「注意：以下事项须严格遵守。」天然构成前缀关系，
    但短块**不是片末块**（同页同片的更长块跟在它之后）——块层文本无法区分
    这两种结构，只有片界位置能。锚定不成立即不替换（锚定漏判时退化为
    「截断内容留两份」，可由块数相等断言暴露，不会静默丢内容）。
    """
    short = _blk("b1", "注意：", 298)
    long_ = _blk("b2", "注意：以下事项须严格遵守。", 298)
    nxt = _blk("b3", "另一段。", 299)
    out = overlap_dedupe([short, long_, nxt], _SPANS, shard_bounds=[3])
    assert out == [short, long_, nxt]
    assert not any(b.flags.get("dropped_dup_of") for b in out)


def test_pipe6_no_anchor_falls_back_to_text_match():
    """未提供 shard_bounds（测试直调 / 异常 IR 退化）时维持纯文本判定契约。"""
    short = _blk("b1", "注意：", 298)
    long_ = _blk("b2", "注意：以下事项须严格遵守。", 298)
    out = overlap_dedupe([short, long_], _SPANS)
    assert out == [long_]


def test_pipe6_stitch_shards_wires_shard_bounds(monkeypatch):
    """stitch_shards 必须把「每片结束的累计索引」按片序传给 overlap_dedupe。"""
    from doc2md.pipeline import sharding
    seen = {}
    real = sharding.overlap_dedupe

    def spy(blocks, spans, max_pages=300, shard_bounds=None):
        seen["bounds"] = shard_bounds
        seen["spans"] = spans
        return real(blocks, spans, max_pages, shard_bounds=shard_bounds)

    monkeypatch.setattr(sharding, "overlap_dedupe", spy)
    a = _ir([_blk("a1", "x", 0)], 0, 2, index=0)
    b = _ir([_blk("b1", "y", 2)], 1, 4, index=1)      # 与 a 有重叠页 → 窗口非空
    stitch_shards([a, b])
    assert seen["bounds"] == [1, 2], seen


def test_pipe6_stitch_shards_degrades_without_shard_meta(monkeypatch):
    """IR 缺 shard meta（spans 与 bounds 长度不一致）→ 放弃锚定退化为纯文本。"""
    from doc2md.pipeline import sharding
    seen = {}
    real = sharding.overlap_dedupe

    def spy(blocks, spans, max_pages=300, shard_bounds=None):
        seen["bounds"] = shard_bounds
        return real(blocks, spans, max_pages, shard_bounds=shard_bounds)

    monkeypatch.setattr(sharding, "overlap_dedupe", spy)
    a = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=1)
    a.blocks = [_blk("a1", "x", 0)]
    b = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=1)
    b.blocks = [_blk("b1", "y", 1)]
    stitch_shards([a, b])
    assert seen["bounds"] is None


# ------------------------------------------------------------------ PIPE-7

def test_pipe7_serial_fast_path_without_window(monkeypatch):
    """默认（未配空窗上限）保持进程内串行：零开销、零行为变化。"""
    import concurrent.futures as cf

    from doc2md.pipeline import batch
    created = []

    class Spy(cf.ProcessPoolExecutor):
        def __init__(self, **kw):
            created.append(kw)
            super().__init__(**kw)

    monkeypatch.setattr(cf, "ProcessPoolExecutor", Spy)
    out = batch.run_batch(["nope.pdf"], cfg={"perf": {}}, workers=1)
    assert created == [], "未配 window 不得新建进程池"
    assert out and out[0]["status"] == "error" and out[0]["code"] == "E5000"


def test_pipe7_pool_used_when_window_configured(monkeypatch):
    """配了空窗上限（>0）时单文件也落到单 worker 进程池：挂起件可被记账。"""
    import concurrent.futures as cf

    from doc2md.pipeline import batch
    created = []

    class FakeFuture:
        def __init__(self, v):
            self._v = v

        def result(self):
            return self._v

        def cancel(self):
            return False

    class FakePool:
        def __init__(self, **kw):
            created.append(kw)

        def submit(self, fn, *a):
            return FakeFuture(fn(*a))

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    monkeypatch.setattr(cf, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(cf, "wait",
                        lambda pending, timeout=None, return_when=None:
                        (set(pending), set()))
    cfg = {"perf": {"batch_file_timeout_s": 60}}
    out = batch.run_batch(["nope.pdf"], cfg=cfg, workers=1)
    assert created == [{"max_workers": 1}], created
    assert out and out[0]["status"] == "error" and out[0]["code"] == "E5000"


# ------------------------------------------------------------------ PIPE-8

def _sentinel_noop(x):
    """模块级可 pickle 工人函数（哨兵测试的真实进程池用）。"""
    return x


def test_pipe8_processes_attr_sentinel_for_this_stdlib():
    """CH-473（PIPE-8，LOW）：X-16 清理读 `pool._processes`（私有属性）。

    该属性是 `concurrent.futures` 未承诺的 internals：一旦某版 stdlib 改名或
    改惰性生成策略，`getattr(pool, "_processes", None)` 会静默退化为空表 →
    空窗超时后的「按 pid 终止本池残留 worker」整体失效（3.12 退出死锁回归），
    且**无任何报错**。本测试是版本哨兵：不断言清理逻辑本身（由 PIPE-7 覆盖），
    只断言「本机 stdlib 上该属性存在、submit 后非空、形态与清理代码用法一致」，
    把「私有契约被 stdlib 打破」从静默退化变成测试红。
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    ctx = multiprocessing.get_context()          # 与 run_batch 默认同上下文
    with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
        assert pool.submit(_sentinel_noop, 1).result() == 1
        procs = getattr(pool, "_processes", None)
        assert isinstance(procs, dict), type(procs)
        assert procs, "submit 后 _processes 仍为空——清理代码将静默失效"
        # 清理代码的用法是 `list(procs.values())` 后逐个 `proc.terminate()`
        assert all(hasattr(p, "terminate") and hasattr(p, "pid")
                   for p in procs.values())


# ------------------------------------------------------------------ PIPE-9

def test_pipe9_block_ids_are_unique_and_sequential():
    """CH-473（PIPE-9，LOW）：块 id 唯一且按落块顺序 b1..bN。

    原实现在循环内 8 处预写 `id=f"b{seq+1}"`，而 `flush_rows` 的「组不成表
    逐行退回段落」分支一次落多块却只递增一次 seq——同批退回行共用一个 id
    （重复 id 温床；虽被文末重排掩盖，预写值是无读取方的死写）。现在唯一
    赋值点是收尾重排，本测试用**覆盖全部落块路径**的文档把「id 序列 =
    位置序列」钉成红线（含单行行组退回段落这条最易触发重复 id 的路径）。
    """
    from doc2md.pipeline.s1_parse import Line, RawPage
    from doc2md.pipeline.s3_structure import analyze

    lines = [
        Line(text="第一章 绪论", size=16.0, bold=True),            # heading
        Line(text="- 列表项", size=10.5),                          # list_item
        Line(text="正文段落一。", size=10.5),                       # paragraph
        Line(text="| 列A | 列B |", size=10.5, x0=10.0,
             row=["列A", "列B"]),                                  # table 第一行
        Line(text="| 列C | 列D |", size=10.5, x0=10.0,
             row=["列C", "列D"]),                                  # table 第二行
        Line(text="```", size=10.5),                               # code（围栏）
        Line(text="code_line_1", size=10.5),
        Line(text="```", size=10.5),
        Line(text="> 引用一行", size=10.5),                        # quote
        Line(text="\x0c", size=0.0),                               # page_break
        Line(text="单独一行 两列", size=10.5, x0=20.0,
             row=["单独", "两列"]),                                # 单行组 → 退回段落
    ]
    ir = analyze([RawPage(index=0, lines=lines)])
    ids = [b.id for b in ir.blocks]
    assert len(ids) == len(set(ids)), f"块 id 重复：{ids}"
    assert ids == [f"b{i}" for i in range(1, len(ids) + 1)], ids
    types = [b.type for b in ir.blocks]
    for t in ("heading", "list_item", "paragraph", "table", "code",
              "quote", "page_break"):
        assert t in types, f"落块路径未覆盖：{t}（types={types}）"


# ------------------------------------------------------------------ PIPE-10

def test_pipe10_fallback_single_has_no_dead_head_param():
    """R5-PIPE-10：`head` 形参（CH-469 移除 mark_shard_edges 后无使用方）已删。"""
    from doc2md.pipeline.convert import _fallback_single
    params = list(inspect.signature(_fallback_single).parameters)
    assert "head" not in params
    assert params[:2] == ["pages", "doc_id"]


# ------------------------------------------------------------------ PIPE-11

def test_pipe11_stray_fence_in_text_does_not_expose_code_block():
    """CH-473（PIPE-11，LOW）：正文里的 ``` 字面行不得让代码块被错切/压缩。

    原实现 `_collapse_outside_fences` 对**整篇**做 ``` 围栏非贪婪配对：段落
    文本中出现的 ```（文档内示例、引用内容）被当围栏起点，与代码块的**开栏**
    配成一对 → 代码块整体落进「围栏外」偶数段，S3 emit_code 特意保留的代码
    空行被连续空行压缩压扁（代码内容失真）。现口径：围栏结构由块类型唯一
    决定（只有 code 块包 ```），code 块一个字节都不动。
    """
    from doc2md.ir import IR, Block
    from doc2md.pipeline.s4_render import render

    ir = IR(doc_id="d", source_key="s", blocks=[
        Block(id="b1", type="paragraph", text="看示例：\n```"),
        Block(id="b2", type="code", text="x\n\n\ny"),
    ])
    md = render(ir)
    # 代码块三连空行逐字节保留（旧实现此处被压成 x\n\ny）
    assert "```\nx\n\n\ny\n```" in md, md
    assert md == "看示例：\n```\n\n```\nx\n\n\ny\n```\n", md


def test_pipe11_nested_fence_inside_code_block_is_untouched():
    """代码块内含 ``` 字面行 + 空行：整块原样透传（含嵌套围栏与三连空行）。"""
    from doc2md.ir import IR, Block
    from doc2md.pipeline.s4_render import render

    code = "def f():\n    pass\n\n\n# 示例：\n```\ninner\n```"
    ir = IR(doc_id="d", source_key="s",
            blocks=[Block(id="b1", type="code", text=code),
                    Block(id="b2", type="paragraph", text="正文。")])
    md = render(ir)
    assert "```\n" + code + "\n```" in md, md
    assert md.endswith("正文。\n")


# ------------------------------------------------------------------ STOR-1

def _doc_ir(doc_id: str = "s5") -> IR:
    ir = IR(doc_id=doc_id, source_key="sk-" + doc_id, source_name="demo.pdf",
            format="pdf", pages=1)
    ir.blocks = [Block(id=f"b{i}", type="paragraph", text=f"原始段落{i}", page=1)
                 for i in range(1, 4)]
    return ir


@pytest.mark.gate
def test_stor1_force_overwrite_purges_edit_audit(store):
    """数据安全：force 覆盖后审计行必须同事务清掉——残留 before/after 会让
    `rollback(to_revision=N)` 把**覆盖前的旧文本**当新版本复活。"""
    from doc2md.edits import patch_block, rollback
    from doc2md.errors import E4041_NotFound
    from doc2md.storage.repo import save_ir
    save_ir(store, _doc_ir())
    patch_block(store, "s5", "b1", {"text": "用户改过的内容", "base_revision": 1}, "tester")
    assert store.q1("SELECT COUNT(*) c FROM edit_audit WHERE doc_id='s5'")["c"] == 1

    save_ir(store, _doc_ir(), force=True)
    assert store.q1("SELECT COUNT(*) c FROM edit_audit WHERE doc_id='s5'")["c"] == 0
    # 覆盖后的 rollback 无审计可依 → 明确 404，而不是复活覆盖前旧文本
    with pytest.raises(E4041_NotFound):
        rollback(store, "s5", "b1", 1, "tester")


def test_stor1_overwritten_event_records_purged_audit(store):
    """doc_overwritten 事件带 purged_audit 计数（只带计数、无正文，NFR-20）。"""
    from doc2md.edits import patch_block
    from doc2md.storage.repo import save_ir
    save_ir(store, _doc_ir())
    patch_block(store, "s5", "b1", {"text": "x", "base_revision": 1}, "tester")
    save_ir(store, _doc_ir(), force=True)
    ev = store.q1("SELECT payload_json FROM events"
                  " WHERE doc_id='s5' AND code='doc_overwritten'")
    payload = json.loads(ev["payload_json"])
    assert payload["purged_audit"] == 1
    assert payload["overwritten_edits"] == 1
    assert payload["forced"] is True


def test_stor1_plain_reconvert_purges_zero_audit(store):
    """无编辑的幂等重写：删 0 行无害，事件计数为 0。"""
    from doc2md.storage.repo import save_ir
    save_ir(store, _doc_ir())
    save_ir(store, _doc_ir())
    ev = store.q1("SELECT payload_json FROM events"
                  " WHERE doc_id='s5' AND code='doc_overwritten'")
    assert json.loads(ev["payload_json"])["purged_audit"] == 0


# ------------------------------------------------------------------ STOR-3

class _CommitBoom:
    """模拟 ENOSPC：COMMIT 执行失败（sqlite3 语义：连接仍处事务内）。"""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *a):
        if sql.strip().upper() == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error (simulated ENOSPC)")
        return self._real.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _BothBoom(_CommitBoom):
    """COMMIT 与 ROLLBACK 都失败（连接已断的最坏姿态）。"""

    def execute(self, sql, *a):
        u = sql.strip().upper()
        if u in ("COMMIT", "ROLLBACK"):
            raise sqlite3.OperationalError(f"boom on {u}")
        return self._real.execute(sql, *a)


@pytest.mark.gate
def test_stor3_commit_failure_rolls_back_and_frees_connection(store):
    """COMMIT 失败（ENOSPC）→ 显式 ROLLBACK → 行不在、连接可再次开事务。

    无修复时 sqlite3 失败提交不自动回滚：残留写锁阻塞他进程，下次
    BEGIN IMMEDIATE 直接报 "cannot start a transaction within a transaction"，
    长驻进程（doc2md work 主连接）被污染后循环必崩。
    """
    from doc2md.storage.db import _Tx
    real = store._conn
    tx = _Tx(_CommitBoom(real))
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        with tx:
            tx.execute("INSERT INTO docs(doc_id, source_key, source_name, format,"
                       " created_at, updated_at)"
                       " VALUES('txfail','sk','n','pdf','t','t')")
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='txfail'")["c"] == 0
    with store.tx() as t2:                      # 连接无残留事务/写锁
        t2.execute("INSERT INTO docs(doc_id, source_key, source_name, format,"
                   " created_at, updated_at)"
                   " VALUES('txok','sk','n','pdf','t','t')")
    assert store.q1("SELECT COUNT(*) c FROM docs WHERE doc_id='txok'")["c"] == 1


def test_stor3_rollback_error_does_not_mask_body_exception(store):
    """with 体已抛 + ROLLBACK 再失败：body 的异常原样上抛，不被 sqlite 错误顶替。"""
    from doc2md.storage.db import _Tx
    tx = _Tx(_BothBoom(store._conn))
    with pytest.raises(ValueError, match="original body error"):
        with tx:
            tx.execute("SELECT 1")
            raise ValueError("original body error")


# ------------------------------------------------------------------ STOR-2

def test_stor2_worker_startup_reconcile_repairs_missing_index(store):
    """启动对账：blocks 有正文而 search_blocks 缺行（迁移中途崩溃的残局）→ 修回。"""
    from doc2md.storage.repo import save_ir
    from doc2md.worker import _startup_reconcile
    save_ir(store, _doc_ir())
    with store.tx() as t:
        t.execute("DELETE FROM search_blocks WHERE doc_id='s5'")
    rec = _startup_reconcile(store)
    assert rec.get("docs_repaired") == 1 and rec.get("blocks_reindexed") == 3
    assert store.q1("SELECT COUNT(*) c FROM search_blocks WHERE doc_id='s5'")["c"] == 3


def test_stor2_worker_startup_reconcile_failure_is_contained(store, monkeypatch):
    """对账失败不阻断启动：只回异常类名。"""
    from doc2md.storage import repo
    from doc2md.worker import _startup_reconcile

    def boom(s):
        raise RuntimeError("fts broken")

    monkeypatch.setattr(repo, "reconcile_search", boom)
    assert _startup_reconcile(store) == {"error": "RuntimeError"}


def test_stor2_service_start_logs_reconcile(api_client):
    """serve 启动日志带 search_reconcile 字段（计数或错误类名）。"""
    client, _tokens = api_client
    w = client.application.config["doc2md"]["writer"]
    first = json.loads(w._path.read_text(encoding="utf-8").splitlines()[0])
    assert first["code"] == "SERVICE_START"
    assert "search_reconcile" in first
    rec = first["search_reconcile"]
    assert rec.get("docs_repaired") == 0 or "error" in rec


# ------------------------------------------------------------------ STOR-4

def _sweep_cfg(tmp_path, minutes=30):
    return {"paths": {"tmp": str(tmp_path / "tmp")},
            "perf": {"stale_job_minutes": minutes}}


def test_stor4_sweep_tmp_skips_in_flight_entries(tmp_path):
    """在途保护窗：mtime 在 stale_job_minutes（默认 30）内的条目跳过。"""
    from doc2md.cron import sweep_tmp
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    (tmp / "busy.zip").write_bytes(b"x")
    assert sweep_tmp(_sweep_cfg(tmp_path, 30), tmp_path) == 0
    assert (tmp / "busy.zip").exists()


def test_stor4_sweep_tmp_removes_stale_entries(tmp_path):
    """超出保护窗的残留照清（在途件随写入持续更新 mtime，不会误判为陈旧）。"""
    from doc2md.cron import sweep_tmp
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    stale = tmp / "old"
    stale.mkdir()
    (stale / "f").write_bytes(b"x")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    assert sweep_tmp(_sweep_cfg(tmp_path, 30), tmp_path) == 1
    assert not stale.exists()


def test_stor4_sweep_tmp_zero_disables_protection(tmp_path):
    """stale_job_minutes <= 0 = 关闭保护，维持旧行为全清。"""
    from doc2md.cron import sweep_tmp
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    f = tmp / "old"
    f.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert sweep_tmp(_sweep_cfg(tmp_path, 0), tmp_path) == 1
    assert not f.exists()


# ------------------------------------------------------------------ SEC-1

def test_sec1_allow_remote_bind_is_runtime_immutable():
    """D4 闭环：opt-in 键本身也不许经 PUT 改——否则两步 PUT 即可掏空 loopback 默认。"""
    from doc2md import config
    base = loader_base()
    with pytest.raises(config.ConfigError, match="运行期不可变"):
        config.check_runtime_immutable(
            _cfg_with(**{"api.allow_remote_bind": True}), base)
    # 配置文件里本就 true 的部署，PUT 改回 false 同样被拒（缩小暴露面同为红线变更）
    opted = _cfg_with(**{"api.allow_remote_bind": True})
    with pytest.raises(config.ConfigError, match="运行期不可变"):
        config.check_runtime_immutable(base, opted)
    # 同值不算修改 → 放行
    config.check_runtime_immutable(_cfg_with(), base)


def loader_base():
    from doc2md.config import loader
    return loader._base()


@pytest.mark.gate
def test_sec1_two_step_put_cannot_punch_loopback(api_client, headers):
    """HTTP 面闭环：PUT opt-in 被 400；随后 PUT bind=0.0.0.0 时 candidate 里
    opt-in 仍未开，validate 的 loopback 检查照样触发——两步都拒，内存/落盘不动。"""
    client, tokens = api_client
    h = headers(tokens, "admin")
    r = client.put("/settings", json={"api": {"allow_remote_bind": True}}, headers=h)
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"
    r2 = client.put("/settings", json={"api": {"bind": "0.0.0.0"}}, headers=h)
    assert r2.status_code == 400 and r2.get_json()["code"] == "E4001"
    cur = client.get("/settings", headers=h).get_json()
    assert cur["api"]["allow_remote_bind"] is False
    assert cur["api"]["bind"] == "127.0.0.1"


# ------------------------------------------------------------------ SEC-2

@pytest.mark.gate
def test_sec2_serve_rejects_nonloopback_host_flag(tmp_path, capsys, monkeypatch):
    """`serve --host 0.0.0.0` 必须被拒（EXIT_USAGE）——CLI 面与配置面同口径。"""
    from doc2md import cli
    monkeypatch.chdir(tmp_path)                 # 隔离 CWD 里的 doc2md.toml
    rc = cli.main(["serve", "--host", "0.0.0.0", "--root", str(tmp_path / "work")])
    assert rc == cli.EXIT_USAGE
    assert "非本机回环" in capsys.readouterr().err


def test_sec2_serve_allows_remote_with_explicit_opt_in(tmp_path, monkeypatch):
    """显式 opt-in（配置文件 api.allow_remote_bind = true）后放行并真正绑定。"""
    import flask

    from doc2md import cli
    toml = tmp_path / "doc2md.toml"
    toml.write_text("[api]\nallow_remote_bind = true\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(flask.Flask, "run",
                        lambda self, *a, **kw: seen.update(kw))
    rc = cli.main(["serve", "--host", "0.0.0.0", "--port", "8123",
                   "--root", str(tmp_path / "work"), "--config", str(toml)])
    assert rc == cli.EXIT_OK
    assert seen["host"] == "0.0.0.0" and seen["port"] == 8123


def test_sec2_serve_loopback_host_needs_no_opt_in(tmp_path, monkeypatch):
    """回环 host 无需 opt-in（默认姿态不被打扰）。"""
    import flask

    from doc2md import cli
    seen = {}
    monkeypatch.setattr(flask.Flask, "run",
                        lambda self, *a, **kw: seen.update(kw))
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["serve", "--host", "127.0.0.1", "--root", str(tmp_path / "work")])
    assert rc == cli.EXIT_OK
    assert seen["host"] == "127.0.0.1"


# ------------------------------------------------------------------ SVC-3 / SVC-4

def test_svc3_search_rejects_negative_limit(api_client, headers):
    """负值 limit 拼进 SQL 得 `LIMIT -1`（SQLite 语义 = 无界）→ 拒负（E4001）。"""
    client, tokens = api_client
    r = client.get("/search?q=x&limit=-1", headers=headers(tokens, "reader"))
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"
    ok = client.get("/search?q=x&limit=0", headers=headers(tokens, "reader"))
    assert ok.status_code == 200


def test_svc4_settings_put_validates_before_apply(api_client, headers):
    """锁内校验：非法 candidate 必须整体拒绝，内存态不半改、落盘不落。"""
    client, tokens = api_client
    h = headers(tokens, "admin")
    before = client.get("/settings", headers=h).get_json()
    r = client.put("/settings", json={"perf": {"workers": "not-a-number"}}, headers=h)
    assert r.status_code == 400 and r.get_json()["code"] == "E4001"
    after = client.get("/settings", headers=h).get_json()
    assert after == before


# ------------------------------------------------------------------ SVC-5

def test_svc5_write_override_fsyncs_file_and_dir(tmp_path, monkeypatch):
    """落盘三段式：内容 fsync → rename → 目录 fsync（override 抗掉电）。"""
    from doc2md import api
    calls = []
    real_fsync = os.fsync

    def spy(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    path = tmp_path / "settings_override.json"
    base = loader_base()
    merged = copy.deepcopy(base)
    merged["logs"]["rotate_mb"] = 12
    diff = api.write_override(path, merged, base)
    assert diff == {"logs": {"rotate_mb": 12}}
    assert len(calls) >= 2, "内容与目录都应 fsync"
    assert json.loads(path.read_text(encoding="utf-8")) == {"logs": {"rotate_mb": 12}}
    monkeypatch.undo()


# ------------------------------------------------------------------ SVC-6

@pytest.mark.gate
def test_svc6_string_false_does_not_force_overwrite(api_client, saved_doc,
                                                    small_pdf, headers):
    """字符串假值（"false"/"no"/"0"/"FALSE"）不是 force——编辑仍受保护。"""
    from doc2md.errors import E4091_DocOverwrite  # noqa: F401 — 冲突即该错误码路径
    client, tokens, doc_id = saved_doc
    blocks = client.get(f"/docs/{doc_id}", headers=headers(tokens)).get_json()["blocks"]
    bid = blocks[0]["id"]
    r = client.patch(f"/docs/{doc_id}/blocks/{bid}",
                     json={"text": "哨兵编辑例外条款ZZ", "base_revision": 1},
                     headers=headers(tokens, "editor"))
    assert r.status_code == 200
    for falsy in ("false", "no", "0", "FALSE"):
        r2 = client.post("/jobs", json={"path": str(small_pdf), "force": falsy},
                         headers=headers(tokens, "editor"))
        jobs = r2.get_json()["jobs"]
        assert jobs[0]["status"] == "conflict", (falsy, jobs)
        row = client.get(f"/docs/{doc_id}", headers=headers(tokens)).get_json()
        blk = next(b for b in row["blocks"] if b["id"] == bid)
        assert blk["text"] == "哨兵编辑例外条款ZZ", falsy


@pytest.mark.gate
def test_svc6_string_true_forces_overwrite(api_client, saved_doc, small_pdf, headers):
    """显式真值字符串（"true"）等价于布尔 true——放行覆盖，编辑被替换。"""
    client, tokens, doc_id = saved_doc
    blocks = client.get(f"/docs/{doc_id}", headers=headers(tokens)).get_json()["blocks"]
    bid = blocks[0]["id"]
    client.patch(f"/docs/{doc_id}/blocks/{bid}",
                 json={"text": "将被覆盖的编辑", "base_revision": 1},
                 headers=headers(tokens, "editor"))
    r = client.post("/jobs", json={"path": str(small_pdf), "force": "true"},
                    headers=headers(tokens, "editor"))
    jobs = r.get_json()["jobs"]
    assert jobs[0].get("doc_id") == doc_id and "conflict" not in jobs[0], jobs
    row = client.get(f"/docs/{doc_id}", headers=headers(tokens)).get_json()
    blk = next(b for b in row["blocks"] if b["id"] == bid)
    assert blk["text"] != "将被覆盖的编辑" and blk["revision"] == 1


# ------------------------------------------------------------------ SVC-7

def test_svc7_sync_pool_runs_under_global_gate(api_client, tmp_path, monkeypatch,
                                               small_pdf):
    """池的整个生命周期必须在 _SYNC_POOL_GATE 临界区内；退出即释放。"""
    import doc2md.pipeline.batch as batch
    from doc2md import api
    client, tokens = api_client
    d = tmp_path / "drop"
    d.mkdir()
    for i in range(3):
        shutil.copy(small_pdf, d / f"s{i}.pdf")
    seen = {}

    def spy(paths, **kw):
        # 池运行期间信号量已被本请求持有（BoundedSemaphore(1) 不可再入）
        seen["locked"] = api._SYNC_POOL_GATE.acquire(blocking=False) is False
        return [{"file": str(p), "status": "error", "code": "E5000"} for p in paths]

    monkeypatch.setattr(batch, "run_batch", spy)
    client.put("/settings", json={"perf": {"sync_min_parallel": 1}},
               headers=_h(tokens, "admin"))
    r = client.post("/jobs", json={"path": str(d)}, headers=_h(tokens, "editor"))
    assert r.status_code == 202
    assert seen.get("locked") is True
    # 请求结束即释放（后续请求可再进临界区）
    assert api._SYNC_POOL_GATE.acquire(blocking=False) is True
    api._SYNC_POOL_GATE.release()


# ------------------------------------------------------------------ SVC-8

def test_svc8_cron_closes_store(tmp_path, tmp_cfg, monkeypatch):
    """cron.run 正常路径关闭 Store（PRAGMA optimize 得以执行）。"""
    from doc2md import cron
    closed = []
    real = cron.Store.close

    def spy(self):
        closed.append(1)
        return real(self)

    monkeypatch.setattr(cron.Store, "close", spy)
    rep = cron.run(tmp_cfg, root=tmp_path / "work", mode="daily", with_backup=False)
    assert any(x["step"] == "daily_report" for x in rep.steps)
    assert closed == [1]


# ------------------------------------------------------------------ Q-1

@pytest.mark.parametrize("val,ok", [
    ("700", True), ("755", True), (0o700, True), (0o644, True), (0, True),
    ("800", False), ("70", False), ("7O0", False),
    (0o1000, False), (True, False), (448.0, False), (None, False),
])
def test_q1_octal_accepts_string_and_int_literals(val, ok):
    """八进制权限：三位八进制字符串或 TOML 八进制字面量（int 0..0o777）都收。"""
    from doc2md import config
    cfg = _cfg_with(**{"ops.work_dir_mode": val})
    if ok:
        config.validate(cfg)
    else:
        with pytest.raises(config.ConfigError, match="八进制"):
            config.validate(cfg)


# ------------------------------------------------------------------ CFG-2

def test_cfg2_doctor_invalid_config_exits_nonzero(tmp_path, monkeypatch):
    """CH-473（CFG-2，LOW）：doctor 自检到配置语义非法必须非零退出。

    原实现 `config_frozen_keys: invalid` 只打印、仍退 EXIT_OK——「doctor 全绿」
    与「配置实际不可用」并存。现口径：`frozen_ok=False` → EXIT_USAGE（配置错
    按 O-15 映射为用法退出码，与加载期同族）。

    注入点选 `cli._cfg`（模块自身的「cfg 已装载」接缝）：经 `--config` 文件
    加载的非法配置会在 `config.load()` 当场raise、由 `main` 映射为 EXIT_USAGE，
    走不到 doctor 内的二次校验分支；本测试直接驱动该分支的退出码契约。
    """
    from doc2md import cli

    class _Args:
        config = None
        root = str(tmp_path / "work")

    monkeypatch.setattr(cli, "_cfg", lambda args: {"perf": {"workers": "8"}})
    rc = cli.cmd_doctor(_Args())
    assert rc == cli.EXIT_USAGE, rc


def test_cfg2_doctor_valid_config_still_exits_zero(tmp_path):
    """对照组：合法配置的自检面不受影响（仍退 0）。"""
    from doc2md import cli

    class _Args:
        config = None
        root = str(tmp_path / "work")

    assert cli.cmd_doctor(_Args()) == cli.EXIT_OK


# ------------------------------------------------------------------ Q-2

def test_q2_resolver_cache_is_capped(monkeypatch):
    """解析缓存封顶：满 256 项后新主机名不再入缓存（正确性不变，只多解析一次）。"""
    from doc2md.security import netguard
    cache = netguard._STATE["resolvers"]
    cache.clear()
    try:
        cache.update({f"h{i}": frozenset({"127.0.0.1"})
                      for i in range(netguard._RESOLVER_CACHE_CAP)})
        monkeypatch.setattr(netguard.socket, "getaddrinfo",
                            lambda host, *a: [(2, 1, 6, "", ("127.0.0.1", 0))])
        assert netguard._allowed("fresh-host", set()) is True
        assert len(cache) == netguard._RESOLVER_CACHE_CAP
        assert "fresh-host" not in cache
    finally:
        cache.clear()


def test_q2_resolver_cache_admits_below_cap(monkeypatch):
    """未满时正常入缓存（封顶只挡溢出，不改变常规路径）。"""
    from doc2md.security import netguard
    cache = netguard._STATE["resolvers"]
    cache.clear()
    try:
        cache.update({"known": frozenset({"127.0.0.1"})})
        monkeypatch.setattr(netguard.socket, "getaddrinfo",
                            lambda host, *a: [(2, 1, 6, "", ("127.0.0.1", 0))])
        assert netguard._allowed("fresh-host", set()) is True
        assert "fresh-host" in cache and len(cache) == 2
    finally:
        cache.clear()


# ------------------------------------------------------------------ OPS-1

def test_ops1_external_failure_leaves_no_partial_container(tmp_path, monkeypatch):
    """外置备份写失败（ENOSPC/IO）：不留名字合法的半截 .enc，也不留 .part。"""
    from doc2md.ops import backup as bk
    ext = tmp_path / "ext"
    plan = bk.BackupPlan(local_dir=tmp_path / "local", external_dir=ext)
    snap = tmp_path / "state-2026.sqlite"
    snap.write_bytes(b"x" * 4096)

    def boom(src, dst, passphrase, **kw):
        dst.write(b"partial")                  # 模拟写了一半炸掉
        raise OSError("No space left on device")

    monkeypatch.setattr(bk, "encrypt_stream", boom)
    with pytest.raises(OSError, match="No space left"):
        plan.to_external(snap, "pass")
    assert list(ext.glob("*.enc")) == []
    assert list(ext.glob("*.part")) == []


# ------------------------------------------------------------------ OPS-2

def test_ops2_truncated_bool_value_raises_gguf_error(tmp_path):
    """截断于 BOOL 值的坏文件报 GgufError（收敛面），不是裸 struct.error。"""
    from doc2md.ops import gguf
    p = tmp_path / "bad.gguf"
    with p.open("wb") as f:
        f.write(gguf.GGUF_MAGIC)
        f.write(struct.pack("<I", 3))          # version 3
        f.write(struct.pack("<Q", 0))          # n_tensors
        f.write(struct.pack("<Q", 1))          # n_kv = 1
        f.write(struct.pack("<Q", 1))          # key 长度
        f.write(b"k")
        f.write(struct.pack("<I", gguf.T_BOOL))   # vtype = BOOL，随后 EOF（缺值字节）
    with pytest.raises(gguf.GgufError, match="bool value"):
        gguf.read_metadata(p)


# ------------------------------------------------------------------ LLM-2

def test_llm2_split_block_registers_new_block_for_followup_ops():
    """split 产生的新块必须入索引：同批后续 op 以新 id 寻址即可命中。"""
    from doc2md import llm
    ir = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=1)
    ir.blocks = [Block(id="b1", type="paragraph",
                       text="前半部分内容后半部分内容", page=1)]
    n = llm.apply_ops(ir, [
        {"op": "split_block", "target": "b1", "at": 5},
        {"op": "mark_table_boundary", "target": "b1s1"},
    ])
    assert n == 2
    new = ir.blocks[1]
    assert new.id == "b1s1" and new.text
    assert new.flags.get("table_boundary") is True
    assert "llm:mark_table_boundary" in new.provenance["modified_by"]


def test_llm2_split_block_accepts_prebuilt_index():
    """`_split_block(index=...)` 用字典查 id 冲突并登记新块（O(1) 替全表扫描）。"""
    from doc2md import llm
    ir = IR(doc_id="d", source_key="s", source_name="n", format="pdf", pages=1)
    b = Block(id="b1", type="paragraph", text="前半部分内容后半部分内容", page=1)
    ir.blocks = [b]
    index = {"b1": b}
    ok = llm._split_block(ir, b, {"op": "split_block", "target": "b1", "at": 3},
                          index)
    assert ok is True
    assert "b1s1" in index
    assert index["b1s1"] is ir.blocks[1]


# ------------------------------------------------------------------ TEST-5

# v3 存量库形状 = import 提交 23f8906（Doc2md v2.4）的 DDL：blocks 已有 revision、
# tokens 已有 role、FTS 已有 aux；缺 v4 的 tasks.source_path/mode 与
# ix_tasks_queued / ix_tasks_doc / ix_events_job / ix_events_doc；token 为明文。
_V3_DDL = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id        TEXT PRIMARY KEY,
  source_key    TEXT NOT NULL,
  source_name   TEXT NOT NULL,
  source_name_raw TEXT DEFAULT '',
  format        TEXT NOT NULL,
  pages         INTEGER DEFAULT 0,
  sharded       INTEGER DEFAULT 0,
  shard_count   INTEGER DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'done',
  profile       TEXT DEFAULT 'standard',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  meta_json     TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_docs_source ON docs(source_key);
CREATE TABLE IF NOT EXISTS blocks (
  doc_id   TEXT NOT NULL,
  block_id TEXT NOT NULL,
  type     TEXT NOT NULL,
  text     TEXT NOT NULL,
  level    INTEGER,
  page     INTEGER,
  flags_json  TEXT DEFAULT '{}',
  marks_json  TEXT DEFAULT '{}',
  immutable INTEGER DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  PRIMARY KEY (doc_id, block_id)
);
CREATE TABLE IF NOT EXISTS edit_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL, block_id TEXT NOT NULL,
  before TEXT, after TEXT,
  from_revision INTEGER, to_revision INTEGER,
  actor TEXT, ts TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS deletions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL, source_key TEXT, actor TEXT, ts TEXT,
  kind TEXT NOT NULL DEFAULT 'doc',
  cascade_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  role  TEXT NOT NULL DEFAULT 'admin',
  label TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  revoked_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (
  job_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  source_key TEXT,
  status TEXT NOT NULL,
  idem_key TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT,
  error_code TEXT, detail_json TEXT DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_doc_active
  ON tasks(doc_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, code TEXT NOT NULL,
  doc_id TEXT, job_id TEXT, payload_json TEXT DEFAULT '{}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_blocks USING fts5(
  doc_id UNINDEXED, block_id UNINDEXED, text, aux, tokenize='unicode61'
);
"""

_V3_TOKEN = "d2m_legacy_stock_token_0001"
_V3_TEXT = "本条款涉及例外情形的处理，双方另有约定的除外。"


def _seed_v3_lib(path) -> None:
    """按 v3 形状造存量库（用户_version=3 + 明文 token + 无 v4 列/索引）。"""
    from doc2md.storage.db import now_iso
    from doc2md.storage.fts import cjk_aux
    con = sqlite3.connect(str(path))
    con.executescript(_V3_DDL)
    con.execute("PRAGMA user_version=3")
    ts = now_iso()
    con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("dv3", "sk-v3", "存量文档.pdf", "", "pdf", 2, 0, 1,
                 "done", "standard", ts, ts, "{}"))
    con.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("dv3", "b1", "paragraph", _V3_TEXT, None, 1, "{}", "{}",
                 0, 1, "", ""))
    con.execute("INSERT INTO search_blocks VALUES (?,?,?,?)",
                ("dv3", "b1", _V3_TEXT, cjk_aux(_V3_TEXT)))
    con.execute("INSERT INTO tokens VALUES (?,?,?,?,?)",
                (_V3_TOKEN, "reader", "存量凭证", ts, ""))
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("jv3", "dv3", "sk-v3", "done", None, ts, ts, ts, None, "{}"))
    con.execute("INSERT INTO events(ts, code, doc_id, job_id, payload_json)"
                " VALUES (?,?,?,?,?)", (ts, "doc_saved", "dv3", "jv3", "{}"))
    con.commit()
    con.close()


@pytest.mark.gate
def test_test5_v3_stock_library_upgrades_to_v5(tmp_path):
    """CH-473（TEST-5，INFO）：存量 v3 库开库即无损升级到 v5。

    迁移链三段全覆盖：结构（user_version 3→5、v4 列与四个索引补建、既有行
    不回填）、凭证（CH-471 SVC-9 存量明文 token 就地哈希，角色/时间戳不变、
    明文不再可查）、检索（v3 已有 aux → 不重建，索引行原样保留仍可命中）。
    另验二次开库幂等（不会二次哈希 / 重复建索引）。
    """
    from doc2md.auth import check
    from doc2md.storage.db import Store, token_hash
    from doc2md.storage.repo import search

    db = tmp_path / "state.sqlite"
    _seed_v3_lib(db)
    store = Store(db)

    # ① 结构：版本 + v4 列 + v4/CH-469 索引
    assert store.q1("PRAGMA user_version")[0] == 5
    tcols = {r["name"] for r in store.q("PRAGMA table_info(tasks)")}
    assert {"source_path", "mode"} <= tcols
    idx = {r["name"] for r in store.q(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_tasks_queued", "ix_tasks_doc",
            "ix_events_job", "ix_events_doc"} <= idx
    # 存量任务行不回填 source_path（NULL = 非队列行，消费方判失败）
    assert store.q1("SELECT source_path FROM tasks WHERE job_id='jv3'")["source_path"] is None

    # ② 凭证：明文就地升级为哈希，其余列原样；auth.check 按明文仍认（不打断在用凭证）
    row = store.q1("SELECT * FROM tokens")
    assert row["token"] == token_hash(_V3_TOKEN)
    assert len(row["token"]) == 64 and not row["token"].startswith("d2m_")
    assert (row["role"], row["label"]) == ("reader", "存量凭证")
    assert store.q1("SELECT COUNT(*) c FROM tokens")["c"] == 1
    assert check(store, _V3_TOKEN, "read") == "reader"

    # ③ 数据与检索：docs/blocks/FTS 行原样，中文子串命中走 fts 通道
    assert store.q1("SELECT COUNT(*) c FROM blocks")["c"] == 1
    assert store.q1("SELECT text FROM blocks")["text"] == _V3_TEXT
    assert store.q1("SELECT COUNT(*) c FROM search_blocks")["c"] == 1
    res = search(store, "例外")
    assert res["mode"] == "fts" and res["total"] >= 1, res
    store.close()

    # ④ 幂等：二次开库不重复哈希、不重复建索引、数据不动
    store2 = Store(db)
    assert store2.q1("PRAGMA user_version")[0] == 5
    assert store2.q1("SELECT COUNT(*) c FROM tokens")["c"] == 1
    assert store2.q1("SELECT token FROM tokens")["token"] == token_hash(_V3_TOKEN)
    assert search(store2, "例外")["total"] >= 1
    store2.close()


# v2.2 遗留形状：blocks 无 revision、tokens 无 role、FTS 无 aux（CH-435 之前）。
_V22_DDL = _V3_DDL.replace(
    "  revision INTEGER NOT NULL DEFAULT 1,\n", ""
).replace(
    "  role  TEXT NOT NULL DEFAULT 'admin',\n", ""
).replace(
    "doc_id UNINDEXED, block_id UNINDEXED, text, aux, tokenize='unicode61'",
    "doc_id UNINDEXED, block_id UNINDEXED, text, tokenize='unicode61'"
)


def _seed_v22_lib(path) -> None:
    from doc2md.storage.db import now_iso
    con = sqlite3.connect(str(path))
    con.executescript(_V22_DDL)
    con.execute("PRAGMA user_version=2")
    ts = now_iso()
    con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("dv22", "sk-v22", "遗留文档.pdf", "", "pdf", 1, 0, 1,
                 "done", "standard", ts, ts, "{}"))
    con.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("dv22", "b1", "paragraph", _V3_TEXT, None, 1, "{}", "{}",
                 0, "", ""))
    con.execute("INSERT INTO search_blocks(doc_id, block_id, text) VALUES (?,?,?)",
                ("dv22", "b1", _V3_TEXT))
    con.execute("INSERT INTO tokens VALUES (?,?,?,?)",
                (_V3_TOKEN, "遗留", ts, ""))
    con.commit()
    con.close()


@pytest.mark.gate
def test_test5_v22_legacy_rebuilds_fts_and_backfills_columns(tmp_path):
    """CH-473（TEST-5，INFO）：v2.2 遗留库（无 revision / 无 role / FTS 无 aux）。

    覆盖迁移链最重的一段：补列（存量行取默认值）+ FTS 影子表重建并**流式回填**
    aux n-gram——回填后中文子串检索才真正可用（重建前 unicode61 把连续 CJK 当
    整词，命中 0）。同时验存量明文 token 同样就地哈希。
    """
    from doc2md.storage.db import Store, token_hash
    from doc2md.storage.repo import search

    db = tmp_path / "legacy.sqlite"
    _seed_v22_lib(db)
    # 迁移前形状自检：三处「缺」都真实存在（否则下面断言空转）
    con = sqlite3.connect(str(db))
    assert "revision" not in {r[1] for r in con.execute("PRAGMA table_info(blocks)")}
    assert "role" not in {r[1] for r in con.execute("PRAGMA table_info(tokens)")}
    assert "aux" not in {r[1] for r in con.execute("PRAGMA table_info(search_blocks)")}
    con.close()

    store = Store(db)
    assert store.q1("PRAGMA user_version")[0] == 5
    # 补列：存量行取默认值
    assert store.q1("SELECT revision FROM blocks")["revision"] == 1
    assert store.q1("SELECT role FROM tokens")["role"] == "admin"
    # FTS 重建：aux 列就位、行不丢、CJK 子串改走 fts 通道命中
    assert "aux" in {r["name"] for r in store.q("PRAGMA table_info(search_blocks)")}
    assert store.q1("SELECT COUNT(*) c FROM search_blocks")["c"] == 1
    res = search(store, "例外")
    assert res["mode"] == "fts" and res["total"] >= 1, res
    # 明文 token 同路哈希
    assert store.q1("SELECT token FROM tokens")["token"] == token_hash(_V3_TOKEN)
    store.close()


# ------------------------------------------------------------------ X-17

@pytest.mark.gate
def test_x17_batch_returns_with_all_pool_workers_reaped(monkeypatch):
    """CH-474（X-17，HIGH）：run_batch 返回前本池 worker 必须已全部终止并回收。

    X-16（`3f7f9f2`）的修复只在 abandoned（空窗超时）分支 terminate 本池 worker；
    run `38901173280` 证明**正常完成路径同样挂死**：3.12 上多线程父进程 fork 出的
    worker 可能持有继承锁、永不响应退出哨兵（CI 日志 `This process (pid=…) is
    multi-threaded, use of fork() may lead to deadlocks in the child` 即此形态），
    `_ExecutorManagerThread.join_processes` 对该 worker 只 join 不 terminate →
    manager 线程永不结束 → 解释器退出时 `_python_exit` join manager 死锁：
    「605 passed」全部打印完毕后卡退出 27 分钟直至作业超时被取消（同代码的
    4151515 干净退出——竞态，runner 负载越高窗口越大）。修复为 finally 无条件
    terminate + join 本池 worker。本测试钉住可观测契约：**batch 返回时**（而非
    解释器退出时）本池 worker 已全部退出。修复前正常路径无 terminate，worker 由
    manager 异步优雅关闭，返回瞬间几乎必然仍存活 → 本测试红；修复后 terminate +
    join 同步完成 → 确定绿（3.12 死锁场景本机 3.11 不复现，不在此断言）。
    """
    import concurrent.futures as cf

    from doc2md.pipeline import batch
    pools = []
    worker_snapshots = []

    class Recording(cf.ProcessPoolExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            pools.append(self)

        def shutdown(self, wait=True, **kw):
            # CPython 的 `ProcessPoolExecutor.shutdown()` 无论 wait 真假都会
            # 同步把 `_processes` 置 None（stdlib 源码末三行），事后从池上取
            # 不到 worker——必须在调用前抓快照，与产品代码同一时序。
            worker_snapshots.append(
                list((getattr(self, "_processes", None) or {}).values()))
            super().shutdown(wait, **kw)

    # run_batch 函数内 `from concurrent.futures import ProcessPoolExecutor` 在
    # 调用时解析模块属性 → patch `cf.ProcessPoolExecutor` 即可拦截（同 PIPE-7）。
    monkeypatch.setattr(cf, "ProcessPoolExecutor", Recording)
    # workers=2 且两个文件 → 走真实进程池；文件不存在使每个 future 都有结论
    # （worker 内转换失败），即正常完成路径（非空窗超时）。
    out = batch.run_batch(["x1_none.pdf", "x2_none.pdf"], cfg={"perf": {}}, workers=2)
    assert len(out) == 2 and all(r["status"] == "error" for r in out), out
    assert pools, "未创建进程池——测试前提失效"
    assert worker_snapshots and worker_snapshots[0], \
        "shutdown 时本池无 worker——与 PIPE-8 哨兵冲突，清理已失效"
    # 契约：batch 返回后每个 worker 至少收到过 SIGTERM+SIGKILL → 内核必终止，
    # 宽限窗口内必然可回收。**不用 `Process.is_alive()` 判定**：CPython 的
    # `Popen.poll()` 把 waitpid 的 ECHILD（已被回收）也返回 None（process.py
    # 对 #1731717 的处理），is_alive 会把「已回收」误报成「仍存活」；直接用
    # waitpid 语义判定——返回 pid = 本次回收、ECHILD = 已被回收（主线程或
    # manager 线程抢先回收都算达成）、WNOHANG=0 = 仍活着（继续等）。
    import errno
    import time as _time

    def _reaped(pid: int) -> bool:
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
        except OSError as e:
            if e.errno == errno.ECHILD:
                return True
            raise
        return wpid == pid

    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline:
        if all(_reaped(p.pid) for s in worker_snapshots for p in s):
            break
        _time.sleep(0.1)
    stuck = [p.pid for s in worker_snapshots for p in s if not _reaped(p.pid)]
    assert not stuck, f"batch 返回后 10s 内本池 worker 未被回收：{stuck}（X-16 回归）"
