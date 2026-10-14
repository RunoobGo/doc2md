"""NFR-18 备份加密 + 轮出（CH-406 / ADR-29）。

方案二选一（README 固化其一，风险 R2）：本实现落地 **age / gpg 对称加密**中的
AES-256-GCM（cryptography 库，跨平台、脚本友好、无 macOS 依赖）；
macOS 部署可选 encrypted sparsebundle（见 ops/backup.md）。

密钥纪律（NFR-20）：密钥不入库、不随备份走、不写入任何日志 / 日报 / 事件流。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"D2MBAE1"          # Doc2md backup encrypted container v1
SALT = b"doc2md-static-salt-v1"


def derive_key(passphrase: str, salt: bytes = SALT) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(
        passphrase.encode("utf-8"))


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    key = derive_key(passphrase)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data, MAGIC)
    return MAGIC + nonce + ct


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
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
        if not self.external_dir:
            raise ValueError("external_dir not configured")
        if not self.encrypted_external:
            raise PermissionError("NFR-18: external backup must be encrypted")
        self.external_dir.mkdir(parents=True, exist_ok=True)
        blob = encrypt_bytes(snapshot.read_bytes(), passphrase)
        out = self.external_dir / (snapshot.name + ".enc")
        out.write_bytes(blob)
        out.chmod(0o600)
        return out

    def restore(self, enc_path: Path, dst: Path, passphrase: str) -> Path:
        dst.write_bytes(decrypt_bytes(enc_path.read_bytes(), passphrase))
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
