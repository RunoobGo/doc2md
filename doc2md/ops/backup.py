"""NFR-18 备份加密 + 轮出（CH-406 / ADR-29；CH-460 / A-11 容器 v2）。

方案二选一（README 固化其一，风险 R2）：本实现落地 **age / gpg 对称加密**中的
AES-256-GCM（cryptography 库，跨平台、脚本友好、无 macOS 依赖）；
macOS 部署可选 encrypted sparsebundle（见 ops/backup.md）。

密钥纪律（NFR-20）：密钥不入库、不随备份走、不写入任何日志 / 日报 / 事件流。

容器版本（CH-460 / A-11）：
  - v1（`D2MBAE1`）：静态盐 + 整库一次性加密。两个缺陷：同口令跨部署派生出
    同一把密钥（可离线预制彩虹表）；`read_bytes()` 整库进内存，大库备份即峰值。
    **仍可解密**（历史外置副本的恢复路径不断），但不再生产。
  - v2（`D2MBAE2`）：随机 16B 盐（同口令每次备份密钥都不同）+ 分块流式
    （默认 4MB/块，加解密驻留峰值 ≈ 一块）+ 每块 AAD 绑定容器头部。
    CH-469（O-4）前 AAD 只绑「magic + 总块数 + 块序号」，明文头里的 `chunk_size`
    不在保护范围内；新容器改绑**整个头部字节**，读侧首块探测后按容器自身口径
    判定（存量 v2 副本仍可解密，恢复路径同样不断）。
"""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"D2MBAE1"                      # v1 容器（历史，只解密、不再生产）
MAGIC_V2 = b"D2MBAE2"                   # v2：随机盐 + 分块流式
LEGACY_SALT = b"doc2md-static-salt-v1"  # 仅 v1 解密路径使用（兼容历史副本）
CHUNK_SIZE = 4 * 1024 * 1024            # v2 分块；加解密驻留峰值 ≈ 一块 + tag
MAX_CHUNK_SIZE = 64 * 1024 * 1024       # 头部声明的分块上限（O-4：防恶意头放大分配）
GCM_TAG = 16
_V2_SALT_LEN = 16
_V2_HEADER_LEN = 7 + _V2_SALT_LEN + 4 + 8   # magic | salt | chunk_size | total_chunks

# 本工具产生的副本名（轮出只认这个形态，见 BackupPlan.rotate）
# CH-485（R9-OPS-5）：快照名允许随机后缀，避免同一秒的并发运行互覆盖；
# 旧形态 `state-<ts>.sqlite` 仍由可选组匹配（轮出兼容）。
_SNAPSHOT_RE = re.compile(
    r"^state-(\d{8}T\d{6}Z)(?:-[0-9a-f]{8})?\.sqlite(\.enc)?$")
# CH-483（R8-OPS-1）：删除前整库快照。随机后缀避免同一秒内两次删除互相覆盖。
_PREDELETE_RE = re.compile(r"^predelete-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.sqlite$")
# CH-485（R9-OPS-2）：恢复前旧库备份也属于含正文的本地副本，需同样轮出/0600。
_PRERESTORE_RE = re.compile(r"^pre-restore-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.sqlite$")
# CH-485（R9-OPS-1）：删前/恢复前快照的硬上限（避免 500 次删除 = 500×DB）。
PREDELETE_KEEP_MAX = 50
PREDELETE_MAX_BYTES = 2 * 1024 * 1024 * 1024
_TS_FMT = "%Y%m%dT%H%M%SZ"
MAX_V1_BYTES = 512 * 1024 * 1024        # v1 单块容器整读恢复的防御上限


class BackupBusy(RuntimeError):
    """另一个备份进程持有锁（cron 重入/手动补跑重叠）。"""


_EXTERNAL_WRITE_LOCK = threading.Lock()


def _output_lock(_path: Path) -> threading.Lock:
    """外置副本写入的线程内串行化。

    `.part` 随机后缀已经避免同名临时文件互踩，但最终 `.enc` 仍是固定名；
    Windows 上两个线程同时 `replace` 同一目标会以 WinError 5 失败。外置备份
    是低频磁盘任务，用一把固定锁即可；按路径建字典会让常驻进程随备份次数
    无限增长（快照名带随机后缀，key 永不重复）。
    """
    return _EXTERNAL_WRITE_LOCK


def _replace_retry(src: Path, dst: Path, *, attempts: int = 5,
                   delay: float = 0.05) -> None:
    """原子替换；Windows 上杀毒/索引器短暂占用目标时退避重试。"""
    for i in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _lock_holder_dead(p: Path) -> bool:
    """锁文件里记的持有者 PID 是否**确证**已不在（CH-493 / R-A1）。

    `stale_seconds` 默认 24h 是「持有者崩了但锁还在」的兜底，代价是崩溃后长达
    24h 内所有 `backup` / `backup restore` 一律被判 `BackupBusy`（本机实测：
    PID 44418 的遗留锁在 37 分钟后仍挡住两条 CH-483 用例）。O_EXCL 锁没有
    「持有者存活」信息，只能靠 mtime 猜，所以猜得很保守。

    这里补一个**确证**口径：锁文件首字段就是持有者 PID，读出来做一次存活探测。
    只有在「读得到 PID 且该 PID 确证不存在」时才回收锁；任何无法确证的情形
    （空文件 / 格式不符 / 平台不支持探测 / 权限不足）一律返回 False，退回原
    mtime 口径——宁可多等，不可误抢活锁。
    """
    try:
        raw = p.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    token = raw.split(" ", 1)[0].strip()
    if not token.isdigit():
        return False                       # 旧格式 / 空锁：无从确证，退回 mtime
    pid = int(token)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)                    # 探测存在性，不发信号
    except ProcessLookupError:
        return True                        # 确证：进程已不在
    except (OSError, ValueError):
        return False                       # 权限不足 / 平台不支持：无法确证
    return False                           # 进程还在 → 锁有效


@contextlib.contextmanager
def backup_lock(directory: str | Path, name: str = "backup.lock",
                stale_seconds: float = 24 * 3600):
    """跨平台的备份互斥锁（O_EXCL 标记文件；进程崩溃后按 mtime 过期）。

    CH-493（R-A1）：mtime 过期之外再加一道「持有者 PID 确证已死」的回收口径，
    崩溃遗留锁不必等满 `stale_seconds`。见 `_lock_holder_dead`。
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    fd = None
    for _ in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode("ascii"))
            os.fsync(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = 0.0
            if age > stale_seconds or _lock_holder_dead(p):
                p.unlink(missing_ok=True)
                continue
            raise BackupBusy(f"backup already running: {p}")
    if fd is None:
        raise BackupBusy(f"backup lock contention: {p}")
    try:
        yield p
    finally:
        os.close(fd)
        p.unlink(missing_ok=True)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Scrypt 派生。

    CH-469（O-3）：`salt` 改为**必填**。原默认值是 v1 的静态盐，任何新调用只要
    忘了传参就悄悄退回「同口令跨部署同一把密钥」——正是 v2 随机盐要消除的那条。
    需要历史兼容的调用方（只有 v1 解密路径）显式传 `LEGACY_SALT`。

    CH-471 SEC-5（D5 决策 = 保持 2^14）：OWASP 建议 scrypt N≥2^17，但 v2 容器头
    只写 `magic + salt + chunk_size + total`，**不含 KDF 参数**。若直接把 N 提到 2^17，
    派生密钥随之改变，而 `decrypt_stream` 无从得知旧容器用的是哪个 N → 现存全部加密备份
    解密时 GCM 认证失败、永久不可恢复。数据安全优先于此强度小提升，故维持 2^14 并登记：
    提 N 须与「头部携带 KDF 参数 + 按参数派生」的向后兼容格式改造同批进行（属未来变更）。
    """
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(
        passphrase.encode("utf-8"))


def _aad_v2(total: int, seq: int, header: bytes | None = None) -> bytes:
    """块 AAD：绑定容器头部字节 + 本块序号（防重排 / 防换块 / 防改头）。

    `header=None` 是 CH-469 之前 v2 容器的原口径（magic + 总块数 + 序号），
    仅解密侧向后兼容使用。
    """
    if header is None:
        return MAGIC_V2 + total.to_bytes(8, "big") + seq.to_bytes(8, "big")
    return header + seq.to_bytes(8, "big")


def encrypt_stream(src, dst, passphrase: str, chunk_size: int = CHUNK_SIZE) -> int:
    """v2 流式加密：读 `src`、写 `dst`，返回总块数。驻留峰值 ≈ 一个 chunk。

    `src` 须可 seek（先取长度算总块数——AAD 要绑定它）；生产路径传入的是
    快照文件句柄，满足。`dst` 为普通写流。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_size > MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size exceeds {MAX_CHUNK_SIZE}")
    size = src.seek(0, 2)                       # EOF
    total = (size + chunk_size - 1) // chunk_size
    src.seek(0)
    salt = os.urandom(_V2_SALT_LEN)
    aes = AESGCM(derive_key(passphrase, salt))
    header = (MAGIC_V2 + salt + chunk_size.to_bytes(4, "big") + total.to_bytes(8, "big"))
    dst.write(header)
    seq = 0
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, chunk, _aad_v2(total, seq, header))
        dst.write(nonce)
        dst.write(ct)
        seq += 1
    if seq != total:
        raise ValueError(f"source changed during encryption: {seq} != {total}")
    return seq


def decrypt_stream(src, dst, passphrase: str) -> int:
    """v2 流式解密：逐块 GCM 认证后写 `dst`，返回块数。

    任一块认证失败即抛异常且不写该块——调用方（`BackupPlan.restore`）负责
    清理半成品文件，保证「恢复失败不留半截库」。
    """
    header = src.read(_V2_HEADER_LEN)
    if not header.startswith(MAGIC_V2):
        if header.startswith(MAGIC):
            raise ValueError("legacy v1 container: use decrypt_bytes")
        raise ValueError("not a Doc2md encrypted backup")
    if len(header) < _V2_HEADER_LEN:
        raise ValueError("truncated container")
    salt = header[7:7 + _V2_SALT_LEN]
    chunk_size = int.from_bytes(header[23:27], "big")
    total = int.from_bytes(header[27:35], "big")
    if not 0 < chunk_size <= MAX_CHUNK_SIZE:
        # 恶意/损坏头：`read(chunk_size + 16)` 会照着头里的数字申请缓冲 → 一次
        # 恢复即可把进程内存打爆（O-4 的 DoS 面）。上限之上直接判定容器不可信。
        raise ValueError("corrupt header: chunk_size out of range")
    aes = AESGCM(derive_key(passphrase, salt))
    full = chunk_size + GCM_TAG                   # GCM tag 16B
    bound_header = True
    for seq in range(total):
        nonce = src.read(12)
        if len(nonce) < 12:
            raise ValueError("truncated container")
        if seq < total - 1:
            ct = src.read(full)
            if len(ct) < full:
                raise ValueError("truncated container")
        else:
            # 末块：明文可能不足整块（甚至整除时恰好整块），只要 tag 在即可
            ct = src.read(full)
            if len(ct) < GCM_TAG:
                raise ValueError("truncated container")
        if seq == 0:
            # 首块探测容器口径：新容器绑整头，CH-469 前的 v2 容器绑 magic+total+seq。
            # 两者都必须由口令未知的同一把密钥认证通过，探测不构成降级通道。
            try:
                plain = aes.decrypt(nonce, ct, _aad_v2(total, seq, header))
            except InvalidTag:
                plain = aes.decrypt(nonce, ct, _aad_v2(total, seq, None))
                bound_header = False
        else:
            plain = aes.decrypt(nonce, ct, _aad_v2(total, seq,
                                                   header if bound_header else None))
        dst.write(plain)
    if src.read(1):
        raise ValueError("trailing bytes after last chunk")
    return total


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """小数据便捷入口（v2 容器）。生产路径大文件请走 `encrypt_stream`。"""
    out = BytesIO()
    encrypt_stream(BytesIO(data), out, passphrase)
    return out.getvalue()


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    """按魔数分派：v1 历史容器原样解密，v2 走流式实现。"""
    if blob.startswith(MAGIC_V2):
        out = BytesIO()
        decrypt_stream(BytesIO(blob), out, passphrase)
        return out.getvalue()
    if not blob.startswith(MAGIC):
        raise ValueError("not a Doc2md encrypted backup")
    body = blob[len(MAGIC):]
    nonce, ct = body[:12], body[12:]
    return AESGCM(derive_key(passphrase, LEGACY_SALT)).decrypt(nonce, ct, MAGIC)


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(str(path) + "-wal"), Path(str(path) + "-shm")


def _remove_sidecars(path: Path) -> None:
    for side in _sidecars(path):
        side.unlink(missing_ok=True)


def sqlite_backup(src: str | Path, dst: str | Path) -> Path:
    """SQLite 在线备份（WAL 安全）：用 backup API，不做文件级复制。

    CH-483（R8-OPS-1 / OPS-4）：`shutil.copy2` 只复制主库文件，已提交但尚未
    checkpoint 的 WAL 帧会丢失，且可能拿到撕裂状态。备份/删除前快照/演练
    统一走这里。
    """
    import sqlite3

    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_c = dst_c = None
    try:
        src_c = sqlite3.connect(str(src))
        dst_c = sqlite3.connect(str(dst))
        src_c.backup(dst_c)
        # 目标连接以 WAL 打开时可能留下 dst-wal/dst-shm；close 前 checkpoint，
        # 避免备份数据只存在于即将被删除的 WAL 里。
        dst_c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        # Windows 不允许删除仍被打开的文件；先关连接再清 `.part`，否则
        # 「失败不留半成品」会在 PermissionError 上二次失败并掩盖原始错误。
        for con in (dst_c, src_c):
            if con is not None:
                con.close()
        dst_c = src_c = None
        dst.unlink(missing_ok=True)
        _remove_sidecars(dst)
        raise
    finally:
        for con in (dst_c, src_c):
            if con is not None:
                con.close()
    # 备份目标是快照/临时库，不是运行中的库；sidecar 不得随快照长期滞留
    #（实测：目标 close 后仍留下 .part-wal/.part-shm，含未 checkpoint 的副本）。
    _remove_sidecars(dst)
    return dst


@dataclass
class BackupPlan:
    local_dir: Path
    external_dir: Path | None
    rotation_days: int = 14
    encrypted_external: bool = True
    work_root: Path | None = None      # M8：work/ 权限 700 的落点（缺省从 local_dir 反推）
    work_dir_mode: str = "700"         # ops.work_dir_mode（CH-456：原为硬编码 0o700）
    cfg: dict | None = None            # CH-471（OPS-4）：传入则写盘路径自带磁盘守卫
    mode_warnings: list[str] = field(default_factory=list)   # O-12：非 POSIX 卷的 chmod 结果


    def sweep_parts(self, max_age_seconds: float = 3600) -> list[Path]:
        """清掉崩溃/超时遗留的 `.part` 全库副本（FR-128 的 14 天上限）。

        `.part` 不在轮出正则里；SIGKILL/断电后它会带着正文永久留在备份目录。
        只在超过年龄阈值后删除，避免与同进程/另一进程刚创建的临时件相撞。
        """
        removed: list[Path] = []
        cutoff = time.time() - max(60.0, float(max_age_seconds))
        for base in (self.local_dir, self.external_dir):
            if not base or not Path(base).is_dir():
                continue
            for pattern in ("*.part", "*.part-wal", "*.part-shm"):
                for p in Path(base).glob(pattern):
                    try:
                        if p.stat().st_mtime <= cutoff:
                            p.unlink()
                            removed.append(p)
                    except OSError:
                        continue
        return removed

    def snapshot(self, state_db: Path, ts: datetime | None = None) -> Path:
        """本地快照（work/ 内可明文，前提是 work/ 权限 700）。

        CH-485（R9-OPS-5）：随机后缀 + `.part` + rename。同一秒的并发运行
        不再写同一路径；SIGKILL/断电最多留下 `.part`，不会被轮出/恢复挑中。
        """
        ts = ts or datetime.now(timezone.utc)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        # CH-489：先收紧目录再写快照，避免 `.part` 在继承来的宽松 ACL 下短暂可见。
        mode = _parse_mode(self.work_dir_mode)
        self._enforce_mode(self.local_dir, mode)
        self._enforce_mode(self._resolve_work_root(), mode)
        self.sweep_parts()
        dst = self.local_dir / (
            f"state-{ts.strftime(_TS_FMT)}-{secrets.token_hex(4)}.sqlite")
        part = dst.with_name(dst.name + ".part")
        try:
            sqlite_backup(state_db, part)
            check = _sqlite_quick_check(part)
            if check != "ok":
                raise ValueError(f"snapshot quick_check failed: {check}")
            # 只读 quick_check 也可能为 WAL 库建 -shm/-wal；它们不属于快照。
            _remove_sidecars(part)
            with part.open("rb+") as fh:
                _fsync(fh)
            _replace_retry(part, dst)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        _fsync_dir(self.local_dir)
        return dst

    def predelete_snapshot(self, state_db: Path,
                           ts: datetime | None = None) -> Path:
        """删除前整库快照（CH-483 / R8-OPS-1）：删除入口调用，失败必须阻断删除。

        CH-485：先过 NFR-6 磁盘门禁、文件 0600、并执行与 cron 同口径的轮出。
        快照失败仍 fail-closed（删除不执行），但磁盘不足必须是可判定的
        E4131，而不是被 API 统一改写成 E5031。
        """
        import secrets

        ts = ts or datetime.now(timezone.utc)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        # CH-489：同 snapshot，先收紧目录再落快照。
        mode = _parse_mode(self.work_dir_mode)
        self._enforce_mode(self.local_dir, mode)
        self._enforce_mode(self._resolve_work_root(), mode)
        self.sweep_parts()
        if self.cfg is not None:
            from .diskguard import assert_free_space
            assert_free_space(self.local_dir, state_db.stat().st_size,
                              cfg=self.cfg, what="predelete_snapshot")
        dst = self.local_dir / (
            f"predelete-{ts.strftime(_TS_FMT)}-{secrets.token_hex(4)}.sqlite")
        # CH-489：与 `snapshot` 同口径——先写 `.part`、quick_check 通过后才原子
        # 改名。原实现直接写最终名且不做完整性校验：SIGKILL/磁盘错误会留下
        # 名字合法但内容不完整的“可恢复快照”，而删除入口拿它当安全网。
        part = dst.with_name(dst.name + ".part")
        try:
            sqlite_backup(state_db, part)
            check = _sqlite_quick_check(part)
            if check != "ok":
                raise ValueError(f"predelete snapshot quick_check failed: {check}")
            _remove_sidecars(part)
            with part.open("rb+") as fh:
                _fsync(fh)
            _replace_retry(part, dst)
        except Exception:
            part.unlink(missing_ok=True)
            _remove_sidecars(part)
            raise
        _fsync_dir(self.local_dir)
        _chmod600(dst, self.mode_warnings)
        self.rotate_predelete(now=ts)
        return dst

    def rotate_predelete(self, now: datetime | None = None) -> list[Path]:
        """清理过期/超量的删前与恢复前快照（CH-485 / FR-128）。

        FR-128 的 14 天是**上限**，不是“至少保留 14 天”；因此最新一份也不能
        免于过期删除。另按份数与总字节做硬上限，防止批量删除把备份目录灌满。
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(self.rotation_days)))
        rows: list[tuple[Path, datetime, int]] = []
        for pattern in ("predelete-*.sqlite", "pre-restore-*.sqlite"):
            for p in sorted(self.local_dir.glob(pattern)):
                m = _PREDELETE_RE.match(p.name) or _PRERESTORE_RE.match(p.name)
                if not m:
                    continue
                try:
                    born = datetime.strptime(
                        m.group(1), _TS_FMT).replace(tzinfo=timezone.utc)
                    st = p.stat()
                    stamp = min(born, datetime.fromtimestamp(
                        st.st_mtime, timezone.utc))
                except (ValueError, OSError):
                    continue
                rows.append((p, stamp, st.st_size))
        removed: list[Path] = []
        # CH-493（P-7）：原实现 `rows.remove((p, stamp, size))` 是**按值**匹配的
        # O(n) 扫描，套在循环里成 O(n²)；且按值匹配在两份元数据相同时会删错行。
        # 改为一次遍历重建保留集，顺序与内容都不变。
        keep: list[tuple] = []
        for p, stamp, size in rows:
            if stamp < cutoff:
                p.unlink(missing_ok=True)
                removed.append(p)
            else:
                keep.append((p, stamp, size))
        rows[:] = keep
        # 先按时间从旧到新淘汰；至少留一份，避免单份超限时把唯一副本删掉。
        rows.sort(key=lambda x: x[1])
        total = sum(size for _p, _stamp, size in rows)
        while len(rows) > 1 and (
                len(rows) > PREDELETE_KEEP_MAX or total > PREDELETE_MAX_BYTES):
            p, _stamp, size = rows.pop(0)
            p.unlink(missing_ok=True)
            removed.append(p)
            total -= size
        return removed

    def _resolve_work_root(self) -> Path:
        """CH-437：原实现 chmod 的是 local_dir.parent（即 work/reports），
        M8「work/ 权限 700」实际从未生效。落点应是数据根，不是快照目录的父目录。"""
        if self.work_root:
            return Path(self.work_root)
        p = Path(self.local_dir).resolve()
        for anc in [p, *p.parents]:
            if anc.name == "work":
                return anc
        return p.parent

    def _enforce_mode(self, path: Path, mode: int) -> None:
        """CH-469（O-12）：chmod 在 SMB / NFS（noacl）、exFAT、被映射的 Windows
        卷上要么抛错要么**成功但不生效**——原实现 `except OSError: pass` 把前者
        吞掉，M8「work/ 700」这条隐私前提在外置卷上静默失效，运维面上无人知晓。
        现在记入 `mode_warnings`，由 cron / CLI / 演练报告如实输出。
        """
        from ..security.perms import ensure_private
        self.mode_warnings.extend(ensure_private(path, mode))

    def to_external(self, snapshot: Path, passphrase: str) -> Path:
        """外置副本（CH-460 / A-11：流式加密，不再把整库读进内存）。"""
        if not self.external_dir:
            raise ValueError("external_dir not configured")
        if not self.encrypted_external:
            raise PermissionError("NFR-18: external backup must be encrypted")
        self.external_dir.mkdir(parents=True, exist_ok=True)
        out = self.external_dir / (snapshot.name + ".enc")
        with _output_lock(out):
            # CH-471（OPS-4）：内部磁盘守卫——原实现只有 cron 在调用点预检，直接 to_external
            # （手抄片段 / 未来调用方）会跳过剩余空间检查、把外置卷写满留半截容器。plan 带
            # cfg 时这里成为唯一闸口；纯测试用裸 plan（cfg=None）保持原样直写。
            if self.cfg is not None:
                from .diskguard import assert_free_space
                assert_free_space(self.external_dir, snapshot.stat().st_size,
                                  cfg=self.cfg, what="external_backup",
                                  factor_override=1.2)
            # CH-473（R5-OPS-1）：写 `.part` 完成后原子 rename——原实现直接以最终名
            # `open("wb")`，encrypt_stream 中途失败（ENOSPC / IO 错误）留下**名字合法
            # 的半截 .enc**：rotate() 的 _SNAPSHOT_RE 不清它，后续恢复演练可能选中
            # 必然认证失败的容器。本地 snapshot（O-11）与 restore 早已是 tmp+rename
            # 口径，外置侧漏修——这里补齐，失败路径 unlink 半截件。
            part = out.with_name(out.name + f".{secrets.token_hex(4)}.part")
            try:
                with snapshot.open("rb") as src, part.open("wb") as dst:
                    encrypt_stream(src, dst, passphrase)
                    _fsync(dst)                      # O-14：外置卷掉电不留半截容器
                # CH-485（R9-OPS-5）：rename 前流式解密自检，防止并发/IO 错误把
                # 认证失败容器当成有效备份（keep_newest 否则会保护它）。
                with part.open("rb") as chk, open(os.devnull, "wb") as sink:
                    decrypt_stream(chk, sink, passphrase)
                _replace_retry(part, out)
            except Exception:
                part.unlink(missing_ok=True)
                raise
            _fsync_dir(self.external_dir)            # OPS-4：新文件目录项抗掉电
            _chmod600(out, self.mode_warnings)
        return out


    def restore(self, enc_path: Path, dst: Path, passphrase: str) -> Path:
        """恢复（CH-460 / A-11：流式解密 + 失败不留半截文件）。

        先写 `.part` 临时文件，全部块认证通过后原子 rename——篡改容器的
        restore 必须失败，且目标路径不得出现半截库。
        """
        tmp = dst.parent / (dst.name + ".part")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with enc_path.open("rb") as src, tmp.open("wb") as w:
                decrypt_stream(src, w, passphrase)
                _fsync(w)                        # O-14：rename 前必须落盘，否则
            # 「原子 rename」只保证看得到目录项，不保证内容已经离开页缓存
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        _replace_retry(tmp, dst)
        _fsync_dir(dst.parent)                   # OPS-4：rename 后落父目录项，原子性才抗掉电
        _chmod600(dst, self.mode_warnings)
        return dst


    def restore_safe(self, src: Path, dst: Path, passphrase: str, *,
                     force: bool = False) -> Path:
        """恢复的安全序列（CH-483 / R8-OPS-2；CH-485 补 v1 与失败回滚）。

        与 `restore` 的区别：
        - 目标已存在默认拒绝；`--force` 先做旧库备份；
        - v1 历史容器（单块）与 v2 流式容器都可恢复；
        - 先落 `.part` + 完整性校验；旧库及 `-wal/-shm` 先移开，替换/复核
          失败时回滚，而不是留下“目标已变、WAL 已删”的半恢复态；
        - `pre-restore-*` 0600，由 `rotate_predelete` 纳入 14 天轮出。
        """
        import sqlite3

        src, dst = Path(src), Path(dst)
        if dst.exists() and not force:
            raise FileExistsError(f"target exists: {dst} (use --force)")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.parent / f"{dst.name}.{secrets.token_hex(4)}.part"
        old_main: Path | None = None
        old_side: list[tuple[Path, Path]] = []
        replaced = False
        try:
            size = src.stat().st_size
            with src.open("rb") as fh:
                head = fh.read(len(MAGIC_V2))
            if head == MAGIC:
                if not passphrase:
                    raise ValueError("passphrase required for encrypted backup")
                if size > MAX_V1_BYTES:
                    raise ValueError(
                        f"legacy v1 container too large to restore in memory: {size}")
                plain = decrypt_bytes(src.read_bytes(), passphrase)
                with tmp.open("wb") as w:
                    w.write(plain)
                    _fsync(w)
            elif head == MAGIC_V2:
                if not passphrase:
                    raise ValueError("passphrase required for encrypted backup")
                with src.open("rb") as r, tmp.open("wb") as w:
                    decrypt_stream(r, w, passphrase)
                    _fsync(w)
            else:
                sqlite_backup(src, tmp)
            con = sqlite3.connect(Path(tmp).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                got = str(con.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                con.close()
            if got != "ok":
                raise ValueError(f"integrity_check failed: {got}")
            _remove_sidecars(tmp)
            if dst.exists():
                ts = datetime.now(timezone.utc).strftime(_TS_FMT)
                backup = dst.parent / f"pre-restore-{ts}-{secrets.token_hex(4)}.sqlite"
                sqlite_backup(dst, backup)
                _chmod600(backup, self.mode_warnings)
                old_main = dst.with_name(f"{dst.name}.old-{secrets.token_hex(4)}")
                os.replace(dst, old_main)
                for suffix in ("-wal", "-shm"):
                    side = Path(str(dst) + suffix)
                    if side.exists():
                        side_old = Path(str(old_main) + suffix)
                        os.replace(side, side_old)
                        old_side.append((side, side_old))
            else:
                # 目标主库不存在但残留 WAL/SHM：旧日志会被新库首次打开时回放，
                # 把恢复内容改坏。无主库时这些 sidecar 没有保留价值，直接清掉。
                for suffix in ("-wal", "-shm"):
                    Path(str(dst) + suffix).unlink(missing_ok=True)
            _replace_retry(tmp, dst)
            replaced = True
            _fsync_dir(dst.parent)
            _chmod600(dst, self.mode_warnings)
            # tmp 在替换前已跑过完整 integrity_check；rename 不改变内容，
            # 不再对同一份 GB 级库做第二次全量校验（恢复耗时≈减半）。
        except Exception:
            tmp.unlink(missing_ok=True)
            # 回滚：优先恢复旧主库与 WAL/SHM；没有旧库时删掉刚替换的目标。
            try:
                if old_main is not None and old_main.exists():
                    dst.unlink(missing_ok=True)
                    os.replace(old_main, dst)
                    for side, side_old in old_side:
                        if side_old.exists():
                            os.replace(side_old, side)
                    _fsync_dir(dst.parent)
                elif replaced and dst.exists() and old_main is None:
                    # 目标原本不存在 → 失败后不得留下未验证的新库；失败发生在
                    # 替换之前时，旧目标必须原样保留。
                    dst.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if old_main is not None:
            old_main.unlink(missing_ok=True)
        for _side, side_old in old_side:
            side_old.unlink(missing_ok=True)
        _fsync_dir(dst.parent)
        return dst


    def rotate(self, now: datetime | None = None, *,
               include_external: bool = True,
               keep_newest: bool = False) -> list[Path]:
        """轮出：> rotation_days 的本工具副本删除（FR-128「已删数据 ≤14 天轮出」）。

        CH-469（O-2）：原实现 `glob("*")` + mtime 判定，会删掉备份目录里**任何**
        过期文件——用户放在同一目录的合同、笔记、别的工具的导出，只要 mtime 超过
        14 天就没了（备份目录恰恰是最容易被当成「顺手放一下」的目录）。现在名字
        必须是 `state-<UTC 秒>.sqlite[.enc]`，不是这个形态一律不碰。

        过期判据取两个信号中更早者：文件名内嵌的生成时刻（跨卷拷贝后 mtime 会变，
        生成时刻不会）与磁盘 mtime（老副本可能没有可解析的名字时刻）。

        CH-483（R8-OPS-3）：
        - `include_external=False` 时完全不碰外置目录——外置备份失败/跳过的轮次
          不得删除外置旧副本（否则连续失败超过保留期会删掉最后一份）；
        - `keep_newest=True` 时每个目录永远保留生成时间最新的一份。
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(self.rotation_days)))
        bases = [self.local_dir]
        if include_external:
            bases.append(self.external_dir)
        removed: list[Path] = []
        for base in filter(None, bases):
            rows: list[tuple[Path, datetime]] = []
            for p in sorted(Path(base).glob("*")):
                if not p.is_file():
                    continue
                m = _SNAPSHOT_RE.match(p.name)
                if not m:
                    continue                     # 不是本工具的副本：不碰
                born = None
                try:
                    born = datetime.strptime(m.group(1), _TS_FMT).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                except OSError:
                    continue
                rows.append((p, min(born, mtime) if born is not None else mtime))
            protected = max(rows, key=lambda x: x[1])[0] if (keep_newest and rows) else None
            for p, stamp in rows:
                if p is protected:
                    continue
                if stamp < cutoff:
                    p.unlink(missing_ok=True)
                    removed.append(p)
        return removed


def _sqlite_quick_check(path: Path) -> str:
    import sqlite3
    try:
        con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return str(con.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return f"error: {type(exc).__name__}"


def _fsync(fh) -> None:
    fh.flush()
    os.fsync(fh.fileno())


def _fsync_dir(path) -> None:
    """CH-471（OPS-4）：rename / create 之后 fsync 父目录，让「目录项本身」也抗掉电。

    `_fsync(文件)` 只保证内容离开页缓存；新建或原子改名后，那条目录项还在目录 inode
    的待落盘状态——掉电可能「内容在、名字看不到」或 rename 回退。POSIX 上以
    `O_RDONLY` 打开目录再 fsync 即可。非 POSIX 卷 / 无权限打开目录会失败：内容已单独
    fsync 过，这里静默降级，绝不让备份整体翻车。
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)



def _chmod600(path: Path, warnings: list[str]) -> None:
    from ..security.perms import ensure_private
    warnings.extend(ensure_private(path, 0o600))


def _parse_mode(value) -> int:
    """`ops.work_dir_mode` 取值解析：接受 "700" / 0o700 / 448 三种写法。"""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 8) if text else 0o700


def hdiutil_recipe() -> str:
    """macOS 备选方案（README 二选一的另一支）。"""
    return ("hdiutil create -encryption AES-256 -stdinpass -type SPARSEBUNDLE "
            "-size 10g -fs APFS ~/doc2md/backups/secure.sparsebundle")
