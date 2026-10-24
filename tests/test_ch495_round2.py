"""CH-495（第十四轮系统审查）Batch 2 回归红线。

覆盖本轮性能与健壮性修复的行为等价 / fail-closed 契约：
- B2：`_escape_inline` 的 translate 表实现与逐字符参考实现逐字节等价；
- B7：`_flush` 的片段列表实现与「顺序 `_append_line` 累积」参考实现等价；
- B4：`sqlite_backup` 分步拷贝（pages=1000）产出完整可读副本；
- B3：`skip_existing_hit` 的 Store 进程内缓存——同库路径复用同一实例；
- F8：`Store` 拒绝 `user_version` 高于本构建 `DB_VERSION` 的库（fail-closed，
  防止 DDL 尾部 PRAGMA 把新库版本戳静默降级）。
"""
from __future__ import annotations

import random
import sqlite3

import pytest

from doc2md.ops.backup import sqlite_backup
from doc2md.pipeline import batch, s3_structure
from doc2md.pipeline.s3_structure import _append_line, _flush
from doc2md.pipeline.s4_render import _INLINE_ESCAPE, _escape_inline
from doc2md.storage.db import DB_VERSION, Store


def _ref_escape(text: str) -> str:
    return "".join("\\" + ch if ch in _INLINE_ESCAPE else ch for ch in text)


@pytest.mark.gate
def test_escape_inline_translate_matches_reference():
    rnd = random.Random(495)
    alphabet = list(_INLINE_ESCAPE) + list("ab中文 01\n\t<>[]*_`~\\")
    for _ in range(200):
        s = "".join(rnd.choice(alphabet) for _ in range(rnd.randrange(0, 60)))
        assert _escape_inline(s) == _ref_escape(s)
    assert _escape_inline("") == ""


def _ref_flush_text(buf: list[str], lines: list | None) -> str:
    """B7 参考实现：修复前的顺序累积口径（与 `_append_line` 同一条路径）。"""
    text = buf[0]
    for i, nxt in enumerate(buf[1:], start=1):
        prev_ln = lines[i - 1] if lines and i - 1 < len(lines) else None
        next_ln = lines[i] if lines and i < len(lines) else None
        space = bool(getattr(prev_ln, "trailing_space", False)
                     or getattr(next_ln, "leading_space", False))
        text = _append_line(text, nxt, space=space)
    return text.strip()


class _Ln:
    def __init__(self, trailing=False, leading=False):
        self.trailing_space = trailing
        self.leading_space = leading


@pytest.mark.gate
@pytest.mark.parametrize("buf,lines", [
    (["under-", "score", "s"], None),                       # 连字符断词
    (["页数 2", "026 页"], None),                            # 数字断行不插空格
    (["中文折", "行续接"], None),                             # CJK 边界默认无空格
    (["hello", "world"], None),                              # 西文词间补空格
    (["标题-", "续"], None),                                   # 片段尾连字符 + 小写续接
    (["a", "b", "c", "d-", "e"], [None, _Ln(trailing=True), _Ln(leading=True),
                                  None, _Ln(trailing=True)]),  # 行缘空白证据
    (["solo"], None),
    (["x"] * 50, None),
])
def test_flush_matches_sequential_reference(buf, lines):
    got = []
    _flush(list(buf), got, {}, 1, lines=list(lines) if lines else None)
    ref_text = _ref_flush_text(list(buf), lines)
    got_text = got[0].text if got else ""
    assert got_text == ref_text


@pytest.mark.gate
def test_flush_random_equivalence():
    rnd = random.Random(9514)
    # 生产不变量：buf 由 s1/s3 供行，不含空串（空行进 buf 时新旧实现同崩，非回归面）。
    atoms = ["中文", "abc", "123", "under-", "026", "x", "-"]
    for _ in range(120):
        n = rnd.randrange(1, 12)
        buf = [rnd.choice(atoms) for _ in range(n)]
        lines = [None if rnd.random() < 0.5 else
                 _Ln(rnd.random() < 0.3, rnd.random() < 0.3) for _ in range(n)]
        got = []
        _flush(list(buf), got, {}, 1, lines=lines)
        got_text = got[0].text if got else ""
        assert got_text == _ref_flush_text(list(buf), lines)


@pytest.mark.gate
def test_sqlite_backup_stepwise_copy_is_complete(tmp_path):
    src = tmp_path / "src.sqlite"
    con = sqlite3.connect(str(src))
    con.execute("CREATE TABLE t(x INTEGER, s TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)",
                    [(i, "行" * 500) for i in range(4000)])   # 跨多个 1000 页步长
    con.commit()
    con.close()
    dst = sqlite_backup(src, tmp_path / "out" / "dst.sqlite")
    check = sqlite3.connect(str(dst))
    assert check.execute("SELECT COUNT(*), SUM(x) FROM t").fetchone() == (4000, 7998000)
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()


@pytest.mark.gate
def test_skip_store_cache_reuses_instance(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    Store(db).close()
    monkeypatch.setattr(batch, "_SKIP_STORES", {})
    s1 = batch._skip_store(db)
    s2 = batch._skip_store(db)
    assert s1 is s2
    assert len(batch._SKIP_STORES) == 1
    s1.close()


@pytest.mark.gate
def test_store_rejects_newer_user_version(tmp_path):
    db = tmp_path / "state.sqlite"
    con = sqlite3.connect(str(db))
    con.execute(f"PRAGMA user_version={DB_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="newer than this build"):
        Store(db)
    # 拒绝必须先于任何 DDL 重放：版本戳不得被 PRAGMA 回写降级。
    con = sqlite3.connect(str(db))
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert ver == DB_VERSION + 1


@pytest.mark.gate
def test_ordered_item_is_public_shared_contract():
    """F12：S3/S4 共享的有序列表编译式已转正为公共名（文档背书的双模块契约）。"""
    from doc2md.pipeline import s4_render
    assert s4_render.ORDERED_ITEM is s3_structure.ORDERED_ITEM
    assert not hasattr(s3_structure, "_ORDERED_ITEM")
