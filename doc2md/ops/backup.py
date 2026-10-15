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
    （默认 4MB/块，加解密驻留峰值 ≈ 一块）+ 每块 AAD 绑定
    「魔数 + 总块数 + 块序号」——重排块、截断容器、篡改头部总块数都在
    GCM 认证上失败，防篡改强度不弱于 v1 的整体认证。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"D2MBAE1"                      # v1 容器（历史，只解密、不再生产）
MAGIC_V2 = b"D2MBAE2"                   # v2：随机盐 + 分块流式
LEGACY_SALT = b"doc2md-static-salt-v1"  # 仅 v1 解密路径使用（兼容历史副本）
CHUNK_SIZE = 4 * 1024 * 1024            # v2 分块；加解密驻留峰值 ≈ 一块 + tag
_V2_SALT_LEN = 16
_V2_HEADER_LEN = 7 + _V2_SALT_LEN + 4 + 8   # magic | salt | chunk_size | total_chunks


def derive_key(passphrase: str, salt: bytes = LEGACY_SALT) -> bytes:
    """Scrypt 派生。`salt` 默认值仅供 v1 兼容路径；v2 一律显式传随机盐。"""
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(
        passphrase.encode("utf-8"))


def _aad_v2(total: int, seq: int) -> bytes:
    """块 AAD：绑定魔数 + 头部声明的总块数 + 本块序号（防重排 / 防改头 / 防换块）。"""
    return MAGIC_V2 + total.to_bytes(8, "big") + seq.to_bytes(8, "big")


def encrypt_stream(src, dst, passphrase: str, chunk_size: int = CHUNK_SIZE) -> int:
    """v2 流式加密：读 `src`、写 `dst`，返回总块数。驻留峰值 ≈ 一个 chunk。

    `src` 须可 seek（先取长度算总块数——AAD 要绑定它）；生产路径传入的是
    快照文件句柄，满足。`dst` 为普通写流。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    size = src.seek(0, 2)                       # EOF
    total = (size + chunk_size - 1) // chunk_size
    src.seek(0)
    salt = os.urandom(_V2_SALT_LEN)
    aes = AESGCM(derive_key(passphrase, salt))
    dst.write(MAGIC_V2 + salt + chunk_size.to_bytes(4, "big")
              + total.to_bytes(8, "big"))
    seq = 0
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, chunk, _aad_v2(total, seq))
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
    salt = header[7:7 + _V2_SALT_LEN]
    chunk_size = int.from_bytes(header[23:27], "big")
    total = int.from_bytes(header[27:35], "big")
    if chunk_size <= 0:
        raise ValueError("corrupt header: chunk_size")
    aes = AESGCM(derive_key(passphrase, salt))
    full = chunk_size + 16                      # GCM tag 16B
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
            if len(ct) < 16:
                raise ValueError("truncated container")
        dst.write(aes.decrypt(nonce, ct, _aad_v2(total, seq)))
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
    return AESGCM(derive_key(passphrase)).decrypt(nonce, ct, MAGIC)


@dataclass
class BackupPlan:
    local_dir: Path
    external_dir: Path | None
    rotation_days: int = 14
    encrypted_external: bool = True
    work_root: Path | None = None      # M8：work/ 权限 700 的落点（缺省从 local_dir 反推）
    work_dir_mode: str = "700"         # ops.work_dir_mode（CH-456：原为硬编码 0o700）

    def snapshot(self, state_db: Path, ts: datetime | None = None) -> Path:
        """本地快照（work/ 内可明文，前提是 work/ 权限 700）。"""
        ts = ts or datetime.now(timezone.utc)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        dst = self.local_dir / f"state-{ts.strftime('%Y%m%dT%H%M%SZ')}.sqlite"
        # SQLite WAL 安全快照：用备份 API 而非 cp
        import sqlite3
        src = sqlite3.connect(str(state_db))
        dstc = sqlite3.connect(str(dst))
        src.backup(dstc)
        dstc.close()
        src.close()
        mode = _parse_mode(self.work_dir_mode)
        _enforce_dir_mode(self.local_dir, mode)
        _enforce_dir_mode(self._resolve_work_root(), mode)
        return dst

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

    def to_external(self, snapshot: Path, passphrase: str) -> Path:
        """外置副本（CH-460 / A-11：流式加密，不再把整库读进内存）。"""
        if not self.external_dir:
            raise ValueError("external_dir not configured")
        if not self.encrypted_external:
            raise PermissionError("NFR-18: external backup must be encrypted")
        self.external_dir.mkdir(parents=True, exist_ok=True)
        out = self.external_dir / (snapshot.name + ".enc")
        with snapshot.open("rb") as src, out.open("wb") as dst:
            encrypt_stream(src, dst, passphrase)
        out.chmod(0o600)
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
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dst)
        dst.chmod(0o600)
        return dst

    def rotate(self, now: datetime | None = None) -> list[Path]:
        """轮出：> rotation_days 的本地/外置副本删除（FR-128「已删数据 ≤14 天轮出」）。"""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.rotation_days)
        removed: list[Path] = []
        for base in filter(None, [self.local_dir, self.external_dir]):
            for p in Path(base).glob("*"):
                if not p.is_file():
                    continue
                mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                if mtime < cutoff:
                    p.unlink()
                    removed.append(p)
        return removed


def _parse_mode(value) -> int:
    """`ops.work_dir_mode` 取值解析：接受 "700" / 0o700 / 448 三种写法。"""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 8) if text else 0o700


def _enforce_dir_mode(path: Path, mode: int) -> None:
    try:
        Path(path).chmod(mode)
    except OSError:
        pass


def hdiutil_recipe() -> str:
    """macOS 备选方案（README 二选一的另一支）。"""
    return ("hdiutil create -encryption AES-256 -stdinpass -type SPARSEBUNDLE "
            "-size 10g -fs APFS ~/doc2md/backups/secure.sparsebundle")
