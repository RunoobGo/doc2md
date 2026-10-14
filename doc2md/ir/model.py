"""IR —— 中间表示（方案 5.1；上游锚点 v2.1 §4.3 + v2.2/2.3/2.4 §4.1 增补）。

v2.4 增补字段（CH-419 / IR 约定 14）：
- meta.source_name      安全名，可进 zip / front-matter
- meta.source_name_raw  消毒前原始名，仅展示层（HTML 转义后），不进 zip
- meta.sharded / meta.shard_count   分片产物标记（FR-126 再修订 ⑤）
- blocks[i].flags.shard_edge        分片边界块
- blocks[i].flags.absorbed_ids      被本块吸收的块 id（缝合结果留痕）

约定 14：任何文件名字段不得参与 doc_id 计算（doc_id 永远只哈希文件内容）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .. import SCHEMA_VERSION

BLOCK_TYPES = ("heading", "paragraph", "list_item", "table", "code", "quote", "page_break")


def doc_id_of(content: bytes) -> str:
    """doc_id 只哈希文件内容 —— 文件名永不参与（约定 14）。"""
    return hashlib.sha256(content).hexdigest()[:16]


def hash_of_path(path, chunk_bytes: int = 1 << 20) -> tuple[str, str]:
    """流式取 (doc_id, source_key)：与 `doc_id_of(content)` / `source_key_of(content)`
    逐字节等价，但不把整份文件读进内存（CH-447，低配机大文件纪律）。"""
    from pathlib import Path
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for blk in iter(lambda: f.read(chunk_bytes), b""):
            h.update(blk)
    d = h.hexdigest()
    return d[:16], d[:24]


def source_key_of(content: bytes) -> str:
    """整源键：同一来源所有版本可整源删除（FR-128）。内容哈希前缀即源身份。"""
    return hashlib.sha256(content).hexdigest()[:24]


@dataclass
class Block:
    id: str
    type: str = "paragraph"
    text: str = ""
    level: int | None = None                 # heading 层级
    font: dict[str, Any] = field(default_factory=dict)
    marks: dict[str, bool] = field(default_factory=dict)   # bold/italic/strikethrough
    links: list[dict[str, str]] = field(default_factory=list)
    page: int | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    immutable: bool = False
    revision: int = 1

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "type": self.type, "text": self.text,
            "level": self.level, "font": self.font, "marks": self.marks,
            "links": self.links, "page": self.page,
            "flags": {"shard_edge": bool(self.flags.get("shard_edge")),
                      "absorbed_ids": list(self.flags.get("absorbed_ids") or []),
                      **{k: v for k, v in self.flags.items()
                         if k not in ("shard_edge", "absorbed_ids")}},
            "provenance": {"modified_by": list(self.provenance.get("modified_by") or []),
                           **{k: v for k, v in self.provenance.items() if k != "modified_by"}},
            "immutable": self.immutable,
            "revision": self.revision,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            id=d["id"], type=d.get("type", "paragraph"), text=d.get("text", ""),
            level=d.get("level"), font=d.get("font", {}), marks=d.get("marks", {}),
            links=d.get("links", []), page=d.get("page"), flags=d.get("flags", {}),
            provenance=d.get("provenance", {}), immutable=d.get("immutable", False),
            revision=d.get("revision", 1),
        )


@dataclass
class IR:
    doc_id: str
    source_key: str
    source_name: str = "unnamed"
    source_name_raw: str = ""
    format: str = "pdf"
    pages: int = 0
    blocks: list[Block] = field(default_factory=list)
    sharded: bool = False
    shard_count: int = 1
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    extra_meta: dict[str, Any] = field(default_factory=dict)

    def meta(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "source_key": self.source_key,
            "source_name": self.source_name,
            "source_name_raw": self.source_name_raw,
            "format": self.format,
            "pages": self.pages,
            "sharded": self.sharded,
            "shard_count": self.shard_count,
            "schema_version": self.schema_version,
            "warnings": list(self.warnings),
            **self.extra_meta,
        }

    def to_dict(self) -> dict:
        return {"meta": self.meta(), "blocks": [b.to_dict() for b in self.blocks]}

    @classmethod
    def from_dict(cls, d: dict) -> "IR":
        m = d.get("meta", {})
        extra = {k: v for k, v in m.items() if k not in (
            "doc_id", "source_key", "source_name", "source_name_raw", "format",
            "pages", "sharded", "shard_count", "schema_version", "warnings")}
        return cls(
            doc_id=m.get("doc_id", ""), source_key=m.get("source_key", ""),
            source_name=m.get("source_name", "unnamed"),
            source_name_raw=m.get("source_name_raw", ""),
            format=m.get("format", "pdf"), pages=int(m.get("pages", 0) or 0),
            blocks=[Block.from_dict(b) for b in d.get("blocks", [])],
            sharded=bool(m.get("sharded", False)),
            shard_count=int(m.get("shard_count", 1) or 1),
            warnings=list(m.get("warnings", []) or []),
            schema_version=int(m.get("schema_version", SCHEMA_VERSION)),
            extra_meta=extra,
        )
