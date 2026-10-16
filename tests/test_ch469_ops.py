"""tests/test_ch469_ops.py — CH-469 批次③：安全 / 配置 / 运维（O-1…O-15）。

逐项对应本轮登记：
- O-1 netguard：守护可重入、跨线程交叠后不残留；`connect_ex`、`sendto` 同样受拦；
  主机名（含 `localhost`）按**解析结果**判定，不再按字面名放行；
- O-2 轮出只认本工具的副本名，不碰同目录里别人的文件；
- O-3 密钥派生的盐改为必填 + v1 / 存量 v2 容器仍可解密（恢复路径不断）；
- O-4 容器头声明的分块大小有上限（防「一次恢复打爆内存」）；AAD 绑整个头部；
- O-5 zip 条目名拒绝反斜杠 / 盘符 / UNC / 空段（原实现先归一再判定，等于洗白）；
- O-6 磁盘门禁口径是 available 而不是 free（root 预留块不算可用）；
- O-7 内容指纹流式化：与行序无关、按重数计入、不整表进内存；
- O-8 watch 登记簿回收消失的文件；截断轮不做删除判定但留痕；
- O-9 gguf：声明的 KV 数超过上限即报错（不再静默截断）；Tokenizer 计数口径不变；
- O-10 目录遍历截断 = 超限：API 413/E4133、CLI 一次性枚举同样失败；
- O-11 快照中途失败不留半截副本；
- O-12 非 POSIX 卷上 chmod 不生效如实记警告，并进演练 / cron 日报；
- O-13 日志写入器与事件总线并发安全（轮转窗口不写进已改名的句柄）；
- O-14 恢复路径 fsync 先于原子 rename（掉电不留「看得见但读不全」的库）；
- O-15 配置校验：类型 / 区间 / 枚举 + 未知键警告 + 默认值与样例键集一致 +
  CLI 把配置错映射为用法退出码、PUT /settings 先校验后应用。

方法论：安全原语用真实 socket 与真实容器字节（断言「拦不拦」「解不解得开」，
不断言实现细节）；配置面用真实 TOML 文件与真实 Flask test client。
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import struct
import threading
import time
import tomllib
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import gguf_fixture
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from doc2md import cli, config
from doc2md.config import loader
from doc2md.errors import E4133_DirTooWide
from doc2md.ops import backup as bk
from doc2md.ops import diskguard
from doc2md.ops.drill import fingerprint, run_drill
from doc2md.ops.gguf import GgufError, Tokenizer, export_vocab, read_metadata
from doc2md.ops.logging import EventBus, RotatingJsonlWriter
from doc2md.security import dirguard, netguard
from doc2md.security.dirguard import WalkLimits, scan_or_raise
from doc2md.security.names import UnsafeNameError, assert_entry_safe
from doc2md.security.netguard import OfflineViolation, is_armed, offline_guard
from doc2md.security.watcher import StabilityTracker
from doc2md.storage.db import Store

pytestmark = [pytest.mark.milestone("DoD")]

PASS = "ops-pass-phrase-2026"


def _mini_db(path: Path, rows: int = 3) -> Path:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t(i INTEGER, s TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)", [(i, f"行 {i}") for i in range(rows)])
    con.commit()
    con.close()
    return path


def _allows(host: str) -> bool:
    """在守护期内提问：该地址是否放行（判定口径 = 地址，不是名字）。"""
    try:
        netguard._check((host, 80))
    except OfflineViolation:
        return False
    return True


# --- O-1：离线守护 -------------------------------------------------------------


@pytest.mark.gate
def test_offline_guard_is_reentrant_and_unarms_only_at_outermost():
    orig = socket.socket.connect
    with offline_guard(True):
        assert is_armed()
        with offline_guard(True):
            assert is_armed()
        assert is_armed(), "嵌套退出不得提前摘桩"
        assert socket.socket.connect is not orig
    assert not is_armed()
    assert socket.socket.connect is orig


def test_offline_guard_survives_overlapping_threads():
    """两线程交叠进出：先退出者不得把真实实现装回去（那会留下永久残留或空窗）。"""
    orig = socket.socket.connect
    release = threading.Event()

    def run():
        with offline_guard(True):
            release.wait(5)
            time.sleep(0.02)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    assert is_armed()
    release.set()
    for t in threads:
        t.join()
    assert not is_armed()
    assert socket.socket.connect is orig


@pytest.mark.gate
def test_offline_guard_blocks_connect_ex_and_sendto():
    """connect_ex（非阻塞 connect）与 UDP sendto 同为出站原语，一个都不能漏。"""
    with offline_guard(True):
        with pytest.raises(OfflineViolation):
            socket.socket().connect_ex(("93.184.216.34", 53))
        with pytest.raises(OfflineViolation):
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
                b"x", ("93.184.216.34", 53))
        with pytest.raises(OfflineViolation):                       # (data, flags, address)
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
                b"x", 0, ("93.184.216.34", 53))


def test_offline_guard_allows_loopback_connect_ex():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        with offline_guard(True):
            c = socket.socket()
            try:
                c.connect_ex(srv.getsockname())          # 只要求不被守护拦下
            finally:
                c.close()
    finally:
        srv.close()


def test_offline_guard_judges_localhost_by_resolution(monkeypatch):
    """hosts 投毒把 localhost 指到公网：字面名放行等于没匹配 → 必须拦。"""
    def fake(host, port, *a, **kw):
        if host == "localhost":
            return [(2, 1, 6, "", ("93.184.216.34", 0))]
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with offline_guard(True):
        assert not _allows("localhost")
        assert _allows("127.0.0.1")


def test_offline_guard_denies_unresolvable_name(monkeypatch):
    def boom(host, port, *a, **kw):
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with offline_guard(True):
        assert not _allows("model.internal")


def test_loopback_literal_helper_only_accepts_ip_literals(monkeypatch):
    """CH-473（TEST-3，LOW）：原断言 `ipaddress.ip_address("::1").is_loopback`
    是验证 stdlib 自身，恒真、零信息量。改为驱动 netguard 真实判定 hook：
    IP 字面量必须**不查 DNS** 直通；主机名 / 畸形字面量必须走解析判定
    （解析结果非回环 → 拦）。"""
    calls: list[str] = []

    def fake(host, port, *a, **kw):
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]   # 一律解析到公网

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with offline_guard(True):
        # 回环字面量：直通，且不得触发任何解析
        for lit in ("127.0.0.1", "::1", "127.9.9.9"):
            assert _allows(lit), lit
        assert not calls, f"字面量判定不应查 DNS：{calls}"
        # 主机名 / 畸形字面量 / 空串：不得按字面放行，走解析后一律拦
        for junk in ("localhost", "127.1", "999.999.999.999", ""):
            assert not _allows(junk), junk
        assert calls and set(calls) <= {"localhost", "127.1",
                                        "999.999.999.999"}, calls


# --- O-2：轮出的作用面 ---------------------------------------------------------


@pytest.mark.gate
def test_rotate_only_touches_own_snapshots(tmp_path):
    """备份目录常被顺手放别的东西：mtime 过期不等于「该删」。"""
    local = tmp_path / "work/reports/backups"
    local.mkdir(parents=True)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    mine = local / f"state-{old.strftime(bk._TS_FMT)}.sqlite"
    mine.write_bytes(b"x")
    foreign = [local / "合同扫描件.pdf", local / "notes.txt",
               local / "state-not-a-timestamp.sqlite"]
    for p in foreign:
        p.write_bytes(b"a")
    mine.parent.mkdir(parents=True, exist_ok=True)
    for p in [mine, *foreign]:
        os.utime(p, (p.stat().st_atime, old.timestamp()))
    removed = [q.name for q in bk.BackupPlan(local_dir=local, external_dir=None).rotate()]
    assert removed == [mine.name], f"只该删本工具副本，实删 {removed}"
    assert all(p.exists() for p in foreign), "同目录的外来文件被误删"


def test_rotate_uses_embedded_stamp_when_mtime_is_fresh(tmp_path):
    """跨卷拷贝后 mtime 会变，文件名里的生成时刻不会 —— 两个信号取更早者。"""
    ext = tmp_path / "ext"
    ext.mkdir()
    born = datetime.now(timezone.utc) - timedelta(days=20)
    stale = ext / f"state-{born.strftime(bk._TS_FMT)}.sqlite.enc"
    stale.write_bytes(b"x")                       # mtime = 刚刚
    removed = bk.BackupPlan(local_dir=ext, external_dir=None, rotation_days=14).rotate()
    assert [p.name for p in removed] == [stale.name]


# --- O-3 / O-4：容器与密钥 -----------------------------------------------------


@pytest.mark.gate
def test_derive_key_requires_explicit_salt():
    """盐参数不得有默认值：默认值 = v1 静态盐，忘传参就悄悄退回旧口径。"""
    with pytest.raises(TypeError):
        bk.derive_key(PASS)                        # pyright: ignore[call-arg]
    k1 = bk.derive_key(PASS, b"a" * 16)
    k2 = bk.derive_key(PASS, b"b" * 16)
    assert len(k1) == 32 and k1 != k2


def test_same_passphrase_yields_different_containers(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"same payload")
    blobs = []
    for _ in range(2):
        out = BytesIO()
        with src.open("rb") as f:
            bk.encrypt_stream(f, out, PASS)
        blobs.append(out.getvalue())
    assert blobs[0] != blobs[1], "随机盐失效：同口令同密文"
    assert all(b.startswith(bk.MAGIC_V2) for b in blobs)


def test_decrypt_stream_rejects_oversized_chunk_header(tmp_path):
    """头部是可控字段：照它申请缓冲即是一次恢复打爆内存的 DoS 面。"""
    evil = (bk.MAGIC_V2 + b"s" * 16 + (1 << 31).to_bytes(4, "big") + (1).to_bytes(8, "big"))
    with pytest.raises(ValueError, match="chunk_size out of range"):
        bk.decrypt_stream(BytesIO(evil + b"\x00" * 64), BytesIO(), PASS)


def test_encrypt_stream_rejects_out_of_range_chunk_size(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x")
    with src.open("rb") as f:
        with pytest.raises(ValueError, match="positive"):
            bk.encrypt_stream(f, BytesIO(), PASS, chunk_size=0)
        f.seek(0)
        with pytest.raises(ValueError, match="exceeds"):
            bk.encrypt_stream(f, BytesIO(), PASS, chunk_size=bk.MAX_CHUNK_SIZE + 1)


@pytest.mark.gate
def test_legacy_v2_container_still_decrypts(tmp_path):
    """CH-469 前的 v2 容器 AAD 只绑 magic+总块数+序号 —— 存量外置副本必须仍可恢复。"""
    plain = "历史副本内容".encode() * 700           # 跨多块
    salt = b"l" * 16
    chunk = 1024
    total = (len(plain) + chunk - 1) // chunk
    header = bk.MAGIC_V2 + salt + chunk.to_bytes(4, "big") + total.to_bytes(8, "big")
    aes = AESGCM(bk.derive_key(PASS, salt))
    body = bytearray()
    for seq in range(total):
        piece = plain[seq * chunk:(seq + 1) * chunk]
        nonce = bytes([seq]) * 12
        body += nonce + aes.encrypt(
            nonce, piece, bk.MAGIC_V2 + total.to_bytes(8, "big") + seq.to_bytes(8, "big"))
    out = BytesIO()
    assert bk.decrypt_stream(BytesIO(header + bytes(body)), out, PASS) == total
    assert out.getvalue() == plain


def test_legacy_v1_container_still_decrypts():
    """v1（静态盐、整库一次）不再生产，但历史副本的恢复路径不能断。"""
    plain = "历史 v1 明文".encode()
    nonce = b"n" * 12
    ct = AESGCM(bk.derive_key(PASS, bk.LEGACY_SALT)).encrypt(nonce, plain, bk.MAGIC)
    assert bk.decrypt_bytes(bk.MAGIC + nonce + ct, PASS) == plain


@pytest.mark.gate
def test_tampered_header_fails_authentication(tmp_path):
    """改头（换盐 / 改总块数）必须认证失败，而不是「少解一块也算成功」。"""
    src = tmp_path / "s.bin"
    src.write_bytes(b"body" * 500)
    cont = BytesIO()
    with src.open("rb") as f:
        bk.encrypt_stream(f, cont, PASS)
    for pos in (8, 24, 30):                       # 盐 / chunk_size / total_chunks
        blob = bytearray(cont.getvalue())
        blob[pos] ^= 0x01
        with pytest.raises(Exception):            # InvalidTag 或头校验失败
            bk.decrypt_stream(BytesIO(bytes(blob)), BytesIO(), PASS)


def test_decrypt_stream_rejects_trailing_and_truncated(tmp_path):
    """多余 / 缺失字节必须硬失败，且按容器形状走对应判定：

    - 整块容器（末块恰满）→ 结构判定 `trailing bytes` / `truncated container`；
    - 短末块容器 → 末块 `read(full)` 会把追加的字节一并吞进密文，由 GCM 认证
      拦下（InvalidTag）。两条路都不写盘、都不返回块数，安全口径一致，
      所以断言放宽到异常类型而不是消息文本——产品代码不为此放宽校验。
    """
    src = tmp_path / "s.bin"
    src.write_bytes(b"payload" * 100)               # 700B：单块、末块不满
    cont = BytesIO()
    with src.open("rb") as f:
        bk.encrypt_stream(f, cont, PASS)
    good = cont.getvalue()

    src2 = tmp_path / "aligned.bin"
    src2.write_bytes(b"A" * 128)                    # 64+64：两块全满
    cont2 = BytesIO()
    with src2.open("rb") as f:
        bk.encrypt_stream(f, cont2, PASS, chunk_size=64)
    aligned = cont2.getvalue()

    with pytest.raises(ValueError, match="trailing"):
        bk.decrypt_stream(BytesIO(aligned + b"junk"), BytesIO(), PASS)
    with pytest.raises(ValueError, match="truncated"):
        bk.decrypt_stream(BytesIO(aligned[:50]), BytesIO(), PASS)
    # 末块被削到不足 tag（16B）→ 结构判定；只短几个字节则由认证拦下（见 good[:-8]）
    with pytest.raises(ValueError, match="truncated"):
        bk.decrypt_stream(BytesIO(aligned[:-70]), BytesIO(), PASS)
    with pytest.raises((ValueError, InvalidTag)):
        bk.decrypt_stream(BytesIO(good + b"junk"), BytesIO(), PASS)
    with pytest.raises((ValueError, InvalidTag)):
        bk.decrypt_stream(BytesIO(good[:-8]), BytesIO(), PASS)
    with pytest.raises(ValueError, match="v1"):
        bk.decrypt_stream(BytesIO(bk.MAGIC + b"\x00" * 40), BytesIO(), PASS)
    with pytest.raises(ValueError, match="not a Doc2md"):
        bk.decrypt_stream(BytesIO(b"NOPE" + b"\x00" * 40), BytesIO(), PASS)


# --- O-5：zip 条目名 -----------------------------------------------------------


@pytest.mark.gate
@pytest.mark.parametrize("entry", [
    "C:\\Users\\x\\a.md", "\\\\srv\\share\\a.md", "sub\\..\\..\\escape.md",
    "a\\b\\c.md", "/etc/passwd", "~/.ssh/id_ed25519", "//srv/a.md",
    "../a.md", "a/../../b.md", "a//b.md", "sub/CON.txt", ""])
def test_assert_entry_safe_rejects_absolute_and_traversing(entry):
    with pytest.raises(UnsafeNameError):
        assert_entry_safe(entry)


def test_assert_entry_safe_accepts_normal_entries():
    for entry in ("a.md", "sub/dir/a.md", "报告/第1章.md", ".hidden.md"):
        assert_entry_safe(entry)


# --- O-6：磁盘门禁口径 ---------------------------------------------------------


@pytest.mark.gate
def test_free_mb_uses_available_not_free(monkeypatch):
    """root 预留块（ext4 / APFS 默认 5%）不是普通进程能写的量：取两者更小者。"""
    usage = type("U", (), {"free": 10 * 1024 ** 3, "total": 100 * 1024 ** 3,
                           "used": 90 * 1024 ** 3})
    monkeypatch.setattr(diskguard.shutil, "disk_usage", lambda p: usage())

    class Vfs:
        f_bavail = 1024
        f_frsize = 1024 * 1024                    # 真正可写 1GB

    monkeypatch.setattr(diskguard.os, "statvfs", lambda p: Vfs(), raising=False)
    assert diskguard.free_mb("/tmp") == 1024


def test_available_bytes_prefers_the_smaller_signal(monkeypatch):
    usage = type("U", (), {"free": 512 * 1024 ** 2, "total": 1, "used": 1})
    monkeypatch.setattr(diskguard.shutil, "disk_usage", lambda p: usage())

    class Vfs:
        f_bavail = 4096
        f_frsize = 1024 * 1024                    # 4GB > free → 取 free

    monkeypatch.setattr(diskguard.os, "statvfs", lambda p: Vfs(), raising=False)
    assert diskguard.available_bytes("/tmp") == 512 * 1024 ** 2


# --- O-7：指纹流式化 -----------------------------------------------------------


@pytest.mark.gate
def test_fingerprint_is_order_invariant_and_multiset_sensitive(tmp_path):
    a = _mini_db(tmp_path / "a.sqlite", rows=4)
    b = tmp_path / "b.sqlite"
    con = sqlite3.connect(str(b))
    con.execute("CREATE TABLE t(i INTEGER, s TEXT)")
    for i in reversed(range(4)):                  # 行序相反
        con.execute("INSERT INTO t VALUES (?,?)", (i, f"行 {i}"))
    con.commit()
    con.close()
    assert fingerprint(a) == fingerprint(b), "同内容不同插入序必须同指纹"

    c = _mini_db(tmp_path / "c.sqlite", rows=4)
    con = sqlite3.connect(str(c))
    con.execute("DELETE FROM t WHERE i=3")
    con.execute("INSERT INTO t VALUES (0,'行 0')")    # 行数相同、内容不同
    con.commit()
    con.close()
    assert fingerprint(a) != fingerprint(c)


def test_fingerprint_does_not_materialize_whole_table(tmp_path, monkeypatch):
    """整表 `fetchall` 一被使用即失败：证明逐批喂哈希、驻留有界。

    游标 `for row in cur` 是 sqlite3 的服务端惰性取行（内部 fetchone），
    不是全量物化，`_tables()` 用它合法；真正的红线是 `fetchall()`。
    """
    from doc2md.ops import drill as drill_mod

    rows = 1200                                   # 跨多个 fetchmany 批次
    db = _mini_db(tmp_path / "big.sqlite", rows=rows)
    real_connect = sqlite3.connect
    hits = {"fetchmany": 0, "fetchall": 0}

    class CursorSpy:
        def __init__(self, cur):
            self._cur = cur

        def fetchmany(self, n=1):
            hits["fetchmany"] += 1
            return self._cur.fetchmany(n)

        def fetchall(self):
            hits["fetchall"] += 1
            raise AssertionError("fingerprint must stream rows")

        def __iter__(self):
            return iter(self._cur)                 # 惰性游标迭代，不是物化

    class ConSpy:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *a):
            return CursorSpy(self._con.execute(sql, *a))

        def close(self):
            self._con.close()

    class SqliteShim:
        @staticmethod
        def connect(*a, **kw):
            return ConSpy(real_connect(*a, **kw))

    monkeypatch.setattr(drill_mod, "sqlite3", SqliteShim)
    assert fingerprint(db)["t"]["rows"] == rows
    assert hits["fetchmany"] > 2 and hits["fetchall"] == 0


# --- O-8：watch 登记簿回收 -----------------------------------------------------


@pytest.mark.gate
def test_tracker_prunes_disappeared_files(tmp_path):
    t = StabilityTracker(stable_seconds=0.0, stale_rounds=3)
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    key = str(f)
    t.observe(key, now=0.0)
    assert key in t.enqueued
    f.unlink()
    assert t.prune(seen=set()) == [], "偶发一轮没扫到不得立刻出册"
    assert t.prune(seen=set()) == []
    assert t.prune(seen=set()) == [key]
    assert not t.pending and not t.enqueued and not t.first_seen   # CH-471 Q-1：冗余 registered 已删


def test_pruned_file_is_reconverted_after_it_comes_back(tmp_path):
    """旧 Stamp 留在册里时，同名文件重新落盘会被当成「已提交过」而静默漏转换。"""
    f = tmp_path / "b.pdf"
    f.write_bytes(b"x")
    t = StabilityTracker(stable_seconds=0.0, stale_rounds=1)
    t.observe(str(f), now=0.0)
    f.unlink()
    t.prune(seen=set())
    f.write_bytes(b"x")                           # 同名同尺寸重建
    assert t.observe(str(f), now=10.0) is True, "重新出现应重新走提交语义"


def test_poll_all_flags_truncation_and_skips_prune(tmp_path):
    live = tmp_path / "f0.pdf"
    live.write_bytes(b"x")
    gone = tmp_path / "gone.pdf"
    gone.write_bytes(b"x")
    t = StabilityTracker(stable_seconds=0.0, stale_rounds=1)
    t.observe(str(gone), now=0.0)
    gone.unlink()
    key = str(live)

    t.poll_all(tmp_path, WalkLimits(deadline_seconds=0.0))
    assert t.last_truncated is True
    assert str(gone) in t.enqueued, "截断轮的清单不完整，不得据此判定文件已消失"

    t.poll_all(tmp_path, WalkLimits(deadline_seconds=10.0))
    assert t.last_truncated is False
    assert str(gone) not in t.enqueued, "完整轮必须回收消失文件的观察状态"
    assert key in t.enqueued


# --- O-9：gguf 元数据与计数口径 ------------------------------------------------


@pytest.mark.gate
def test_read_metadata_errors_on_oversized_kv_claim(tmp_path):
    """声明的 KV 数超过上限即报错：原实现 min(n_kv, max_kv) 静默截断 → 半份元数据当真。"""
    p = tmp_path / "big.gguf"
    p.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                  + struct.pack("<Q", 10 ** 9))
    with pytest.raises(GgufError, match="limit"):
        read_metadata(p)

    ok = gguf_fixture.build_gguf(tmp_path / "m.gguf")
    assert read_metadata(ok, max_kv=8).n_kv == 8
    with pytest.raises(GgufError, match="limit"):
        read_metadata(ok, max_kv=4)


def test_tokenizer_count_matches_brute_force(tmp_path):
    """候选长度集合优化不得改变计数口径（L0/L1 字节级一致承诺的一部分）。"""
    toks = ["的", "是", "一个人", "test", "，", "。", "a-fairly-long-token-value-x"]
    g = gguf_fixture.build_gguf(tmp_path / "m.gguf", tokens=toks)
    manifest = export_vocab(g)
    tk = Tokenizer(manifest["vocab_file"])

    def brute(text: str) -> int:
        import unicodedata
        s = unicodedata.normalize("NFC", text)
        words = tk._set
        maxlen = max((len(w) for w in words), default=1)
        n = i = 0
        while i < len(s):
            hit = 0
            for L in range(min(maxlen, len(s) - i), 0, -1):
                if s[i:i + L] in words:
                    hit = L
                    break
            n += 1
            i += hit or 1
        return n

    samples = ["这是一个 test。", "a-fairly-long-token-value-x的", "", "未知字形🙂",
               "。的的的，test一个人", "x" * 800]
    for s in samples:
        assert tk.count(s) == brute(s), f"计数口径漂移：{s!r}"
    assert len(tk._lens) < tk._maxlen, "优化未生效（词表长度档数应少于最大长度）"


# --- O-10：遍历截断 = 超限 -----------------------------------------------------


@pytest.mark.gate
def test_scan_or_raise_on_truncated_walk(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"x")
    real = dirguard.guard_walk

    def truncated(root, limits=None):
        res = real(root, limits)
        res.truncated = True
        return res

    monkeypatch.setattr(dirguard, "guard_walk", truncated)
    with pytest.raises(E4133_DirTooWide) as ei:
        scan_or_raise(tmp_path, WalkLimits())
    assert ei.value.detail["reason"] == "walk_truncated"
    assert "collected" in ei.value.detail


def test_api_dir_submit_413_when_scan_truncated(api_client, headers, tmp_path, monkeypatch):
    client, tokens = api_client
    d = tmp_path / "drop"
    d.mkdir()
    for i in range(3):
        (d / f"f{i}.pdf").write_bytes(b"%PDF-1.4")

    def truncated(root, limits=None):
        return dirguard.WalkResult(files=[str(d / "f0.pdf")], truncated=True, dirs_visited=1)

    monkeypatch.setattr(dirguard, "guard_walk", truncated)
    resp = client.post("/jobs", json={"path": str(d)}, headers=headers(tokens, "editor"))
    assert resp.status_code == 413, resp.get_data(as_text=True)[:200]
    assert resp.get_json()["code"] == "E4133"


def test_cli_dir_enumeration_fails_loud_on_truncation(tmp_path, monkeypatch, capsys):
    """一次性枚举的半截清单 = 静默丢文件；常驻 watch 才是「下一轮补齐」语义。"""
    d = tmp_path / "drop"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"%PDF-1.4")
    cfg = tmp_path / "doc2md.toml"
    cfg.write_text('[perf]\nmin_free_mb = 0\ndisk_safety_factor = 0\n',
                 encoding="utf-8")

    def truncated(root, limits=None):
        res = dirguard.WalkResult(files=[str(d / "a.pdf")])
        res.truncated = True
        return res

    monkeypatch.setattr(dirguard, "guard_walk", truncated)
    rc = cli.main(["convert", str(d), "--config", str(cfg), "--out", str(tmp_path / "o"),
                   "--json"])
    assert rc == cli.EXIT_BIZ
    assert "E4133" in capsys.readouterr().err


# --- O-11：快照失败不留半截 ----------------------------------------------------


@pytest.mark.gate
def test_failed_snapshot_leaves_no_partial(tmp_path):
    """源库不可读（被占用 / 不是库）时，半截副本名字合法、当期时间戳，
    轮出不会清它，恢复演练却会挑中它 —— 失败即删。"""
    local = tmp_path / "work/reports/backups"
    plan = bk.BackupPlan(local_dir=local, external_dir=None)
    junk = tmp_path / "not-a-db.sqlite"
    junk.write_text("这是一段文本，不是 sqlite 库", encoding="utf-8")
    with pytest.raises(sqlite3.Error):
        plan.snapshot(junk)
    assert list(local.glob("*")) == [], "失败的快照不得留下半截库"


def test_snapshot_succeeds_and_is_readable(tmp_path):
    db = _mini_db(tmp_path / "state.sqlite")
    plan = bk.BackupPlan(local_dir=tmp_path / "backups", external_dir=None)
    snap = plan.snapshot(db)
    con = sqlite3.connect(str(snap))
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 3
    con.close()
    assert snap.name.startswith("state-") and snap.name.endswith(".sqlite")


# --- O-12：权限落不上要如实说 --------------------------------------------------


def _chmod_raises(self, mode, *a, **kw):
    raise PermissionError(1, "Operation not permitted")


def _chmod_noop(self, mode, *a, **kw):
    return None


def test_mode_warnings_when_chmod_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "chmod", _chmod_raises)
    plan = bk.BackupPlan(local_dir=tmp_path / "b", external_dir=None)
    plan._enforce_mode(tmp_path, 0o700)
    assert plan.mode_warnings and "failed" in plan.mode_warnings[0]


def test_mode_warnings_when_volume_ignores_permissions(tmp_path, monkeypatch):
    """chmod 成功但目录仍是 755（SMB / exFAT / NFS noacl）= M8「work/ 700」前提失效。"""
    target = tmp_path / "work"
    target.mkdir(mode=0o755)
    monkeypatch.setattr(Path, "chmod", _chmod_noop)
    plan = bk.BackupPlan(local_dir=target, external_dir=None)
    plan._enforce_mode(target, 0o700)
    assert plan.mode_warnings and "ignores POSIX" in plan.mode_warnings[0]


def test_drill_surfaces_mode_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC2MD_BACKUP_PASSPHRASE", PASS)
    monkeypatch.setattr(Path, "chmod", _chmod_raises)
    root = tmp_path / "work"
    root.mkdir()
    _mini_db(root / "state.sqlite")
    rep = run_drill({}, root)
    assert rep.ok is True, [s.detail for s in rep.steps]
    step = next(s for s in rep.steps if s.name == "work_dir_mode")
    assert step.detail["warnings"], "权限没落成必须在报告里可见"


def test_cron_report_surfaces_mode_warnings(tmp_cfg, tmp_path, monkeypatch):
    from doc2md import cron
    root = Path(tmp_cfg["paths"]["root"])
    root.mkdir(parents=True, exist_ok=True)
    Store(root / "state.sqlite").close()
    monkeypatch.setattr(Path, "chmod", _chmod_raises)
    rep = cron.run(tmp_cfg, root, mode="daily")
    steps = json.dumps(rep.steps, ensure_ascii=False, default=str)
    assert "work_dir_mode" in steps, rep.steps


# --- O-13：日志并发 ------------------------------------------------------------


@pytest.mark.gate
def test_writer_rotation_under_threads_keeps_jsonl_parseable(tmp_path):
    w = RotatingJsonlWriter(tmp_path / "logs", stem="app", rotate_mb=0.001,
                             rotate_keep=99)

    def spin(tid: int):
        for i in range(60):
            w.write({"code": "T", "tid": tid, "i": i, "note": "x" * 200})

    ts = [threading.Thread(target=spin, args=(t,)) for t in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    w.close()
    assert w.rotations > 0, "本项要覆盖的正是轮转窗口"
    lines = []
    for p in sorted((tmp_path / "logs").glob("app.jsonl*")):
        for raw in p.read_text(encoding="utf-8").splitlines():
            assert raw.strip(), f"{p.name} 出现空行（交错写坏）"
            json.loads(raw)                       # 半截 JSON 即抛
            lines.append(raw)
    assert len(lines) >= 240, "并发写入不得丢记录"


def test_event_bus_seq_is_monotonic_in_file_order(tmp_path):
    w = RotatingJsonlWriter(tmp_path / "logs", stem="bus", rotate_mb=50, rotate_keep=5)
    bus = EventBus(w)
    start = threading.Barrier(4)

    def spin(tid: int):
        start.wait()
        for i in range(40):
            bus.emit("E", tid=tid, i=i)

    ts = [threading.Thread(target=spin, args=(t,)) for t in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    w.close()
    seqs = [json.loads(l)["seq"] for l in
            (tmp_path / "logs" / "bus.jsonl").read_text(encoding="utf-8").splitlines()]
    assert seqs == sorted(seqs), "序号分配顺序与写入顺序倒挂（未同锁）"
    assert len(seqs) == 160 and seqs[-1] == 160


def test_writer_check_rotates_explicitly(tmp_path):
    """cron --daily 的显式检查口：到阈值即轮转并返回 True（NFR-19 不靠 newsyslog）。"""
    w = RotatingJsonlWriter(tmp_path / "logs", stem="c", rotate_mb=1e-6)   # 阈值 1 字节
    w.write({"code": "A"})                          # 本次写入尚未轮转
    assert w.rotations == 0
    assert w.check() is True
    w.close()


# --- O-14：fsync 先于发布 ------------------------------------------------------


@pytest.mark.gate
def test_restore_fsyncs_before_rename(tmp_path, monkeypatch):
    db = _mini_db(tmp_path / "state.sqlite")
    plan = bk.BackupPlan(local_dir=tmp_path / "work/reports/backups",
                         external_dir=tmp_path / "ext")
    enc = plan.to_external(plan.snapshot(db), PASS)
    seen = {}
    real_fsync = bk.os.fsync

    def spy(fh):
        real_fsync(fh)
        seen["part"] = (tmp_path / "restored.sqlite.part").exists()
        seen["dst"] = (tmp_path / "restored.sqlite").exists()

    monkeypatch.setattr(bk, "_fsync", spy)
    plan.restore(enc, tmp_path / "restored.sqlite", PASS)
    assert seen == {"part": True, "dst": False}, "rename 前必须已落盘（原子性只保证目录项）"


def test_external_write_fsyncs_and_chmod_failure_is_recorded(tmp_path, monkeypatch):
    db = _mini_db(tmp_path / "state.sqlite")
    calls = []
    monkeypatch.setattr(bk, "_fsync", lambda fh: calls.append(fh.fileno()))
    plan = bk.BackupPlan(local_dir=tmp_path / "b", external_dir=tmp_path / "e")
    plan.to_external(plan.snapshot(db), PASS)
    assert len(calls) == 1, "外置容器写完必须 fsync"

    monkeypatch.setattr(Path, "chmod", _chmod_raises)
    plan2 = bk.BackupPlan(local_dir=tmp_path / "b2", external_dir=tmp_path / "e2")
    plan2.to_external(plan2.snapshot(db), PASS)
    assert plan2.mode_warnings, "600 落不上也要如实记账"


def test_tampered_container_restore_leaves_nothing(tmp_path):
    db = _mini_db(tmp_path / "state.sqlite")
    plan = bk.BackupPlan(local_dir=tmp_path / "b", external_dir=tmp_path / "e")
    enc = plan.to_external(plan.snapshot(db), PASS)
    blob = bytearray(enc.read_bytes())
    blob[-1] ^= 0xFF
    enc.write_bytes(bytes(blob))
    dst = tmp_path / "out.sqlite"
    with pytest.raises(Exception):
        plan.restore(enc, dst, PASS)
    assert not dst.exists()
    assert not list(tmp_path.glob("out.sqlite.part"))


# --- O-15：配置契约 ------------------------------------------------------------


def _leaf_keys(text: str) -> set[str]:
    """TOML 里的叶子键（分表名带点才算叶子，`_dotted_keys` 会把表名单独列出来）。"""
    return {k for k in loader._dotted_keys(tomllib.loads(text)) if "." in k}


@pytest.mark.gate
def test_spec_defaults_and_example_share_one_key_set():
    """三处（SPEC / 内置默认 / 分发样例）键集一致 —— 新增键漏一处即测试红。"""
    from doc2md.config.default_toml import DEFAULT_TOML
    defaults = _leaf_keys(DEFAULT_TOML)
    assert defaults == set(loader.SPEC), (
        f"默认有 SPEC 无：{sorted(defaults - set(loader.SPEC))}；"
        f"SPEC 有默认无：{sorted(set(loader.SPEC) - defaults)}")
    ex = Path(__file__).resolve().parent.parent / "config" / "doc2md.example.toml"
    assert _leaf_keys(ex.read_text(encoding="utf-8")) == set(loader.SPEC)


@pytest.mark.gate
def test_load_accepts_pristine_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC2MD_HOME", str(tmp_path / "none"))
    monkeypatch.chdir(tmp_path)
    cfg = config.load()
    config.validate(cfg)                          # 默认值自身必须全绿
    assert config.get(cfg, "run.profile") == "standard"
    assert config.unknown_keys(cfg) == []


@pytest.mark.gate
@pytest.mark.parametrize("dotted, bad", [
    ("perf.workers", "8"), ("perf.workers", -1), ("perf.workers", True),
    ("api.port", 0), ("api.port", 70000), ("api.port", "8090"),
    ("logs.rotate_mb", 0), ("logs.rotate_keep", 0),
    ("run.profile", "turbo"), ("chunks.tokenizer", "qwen3"),
    ("s2.mode", "hybrid"), ("s1_5.sample_pages", "all"),
    ("s4.link_internal", "keep"), ("llm.mode", "remote"),
    ("ops.work_dir_mode", "rwx"),
    # CH-473（Q-1）：八进制权限改收「三位八进制字符串或 int 0..0o777」两种形态
    # （TOML `0o700` 字面量经 tomllib 解析为 int 448）——越界 int 仍拒。
    ("ops.work_dir_mode", 0o1000), ("ops.work_dir_mode", 448.0),
    ("security.sanitize_fail_action", "warn"),
    ("security.max_name_bytes", 1), ("watch.max_depth", 0),
    ("s1.chunk_pages", "32"), ("llm.op_whitelist", "split_block"),
    ("run.offline_assert", 1), ("llm.timeout_ms", 0), ("llm.retries", -1),
])
def test_validate_rejects_mistyped_and_out_of_range(dotted, bad):
    cfg = loader._base()
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = bad
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_validate_reports_the_offending_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC2MD_HOME", str(tmp_path / "none"))
    cfg = loader._base()
    cfg["api"]["port"] = 99999
    with pytest.raises(config.ConfigError, match="api.port"):
        config.validate(cfg)
    cfg2 = loader._base()
    cfg2["paths"] = "work"                        # 整表写成了标量
    with pytest.raises(config.ConfigError, match=r"\[paths\]"):
        config.validate(cfg2)


def test_frozen_key_still_reports():
    cfg = loader._base()
    cfg["security"]["sanitize_fail_action"] = "sanitize-and-continue"
    with pytest.raises(config.ConfigError, match="只支持"):
        config.validate(cfg)


def test_unknown_key_warns_at_load(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "doc2md.toml").write_text(
        '[perf]\nworkerz = 4\n[s1]\nchunk_pages = 16\n[mystery]\nx = 1\n', encoding="utf-8")
    monkeypatch.setenv("DOC2MD_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    with pytest.warns(UserWarning, match="workerz"):
        cfg = config.load()
    assert config.get(cfg, "perf.workers") == 0, "拼错的键不得生效"
    assert config.get(cfg, "s1.chunk_pages") == 16


def test_valid_user_file_does_not_warn(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "doc2md.toml").write_text('[perf]\nworkers = 2\n[llm]\nmode = "offline"\n',
                                      encoding="utf-8")
    monkeypatch.setenv("DOC2MD_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        cfg = config.load()
    assert config.get(cfg, "perf.workers") == 2


def test_unparsable_config_is_a_config_error(tmp_path, monkeypatch):
    """TOML 语法错 / 文件读不到同样收敛为 ConfigError（CLI 才有统一的用法退出码）。"""
    bad = tmp_path / "doc2md.toml"
    bad.write_text('[perf\nworkers = 2', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(bad)
    with pytest.raises(config.ConfigError):
        config.load(tmp_path / "nope" / "doc2md.toml")
    monkeypatch.setenv("DOC2MD_HOME", str(tmp_path / "none"))
    monkeypatch.chdir(tmp_path)


@pytest.mark.gate
def test_cli_maps_config_error_to_usage_exit(tmp_path, capsys):
    bad = tmp_path / "doc2md.toml"
    bad.write_text('[perf]\nworkers = -1\n', encoding="utf-8")
    rc = cli.main(["convert", str(tmp_path / "missing.pdf"), "--config", str(bad)])
    assert rc == cli.EXIT_USAGE, rc
    err = capsys.readouterr().err
    assert "workers" in err and "E4001" in err


def test_cli_maps_bad_toml_syntax_to_usage_exit(tmp_path, capsys):
    bad = tmp_path / "doc2md.toml"
    bad.write_text('[perf\nworkers = 2', encoding="utf-8")
    rc = cli.main(["convert", str(tmp_path / "x.pdf"), "--config", str(bad)])
    assert rc == cli.EXIT_USAGE, rc


def test_settings_put_validates_before_applying(api_client, headers):
    client, tokens = api_client
    before = json.dumps(client.application.config["doc2md"]["cfg"],
                        sort_keys=True, default=str)
    r = client.put("/settings", json={"perf": {"workers": -1}}, headers=headers(tokens))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert r.get_json()["code"] == "E4001"
    after = json.dumps(client.application.config["doc2md"]["cfg"], sort_keys=True,
                       default=str)
    assert after == before, "坏值不得接管内存配置"
    ok = client.put("/settings", json={"perf": {"workers": 2}}, headers=headers(tokens))
    assert ok.status_code == 200
    assert int(client.application.config["doc2md"]["cfg"]["perf"]["workers"]) == 2


def test_settings_put_rejects_unknown_key(api_client, headers):
    client, tokens = api_client
    r = client.put("/settings", json={"perf": {"workerz": 4}}, headers=headers(tokens))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert "workerz" in r.get_json()["message"]


def test_reload_keeps_previous_config_when_file_is_bad(api_client, headers):
    client, tokens = api_client
    root = Path(client.application.config["doc2md"]["root"])
    (root / "settings_override.json").write_text(
        json.dumps({"logs": {"rotate_mb": 0}}), encoding="utf-8")
    before = client.get("/settings", headers=headers(tokens)).get_json()
    r = client.post("/system/reload", headers=headers(tokens))
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert client.get("/settings", headers=headers(tokens)).get_json() == before
