"""M0 —— GGUF 内嵌 vocab 导出与 manifest 绑定（CH-402 / P-49）。

背景：`tokenizer = "qwen3"` 指向独立 vocab 文件，但 GGUF 通常内嵌 tokenizer；
来源、版本与 GGUF 的对应关系未定义 → chunks 的 token 计数口径落空。

本模块做法：
1. 直接解析 GGUF 的 KV metadata（GGUF v2/v3 二进制格式），取出
   `tokenizer.ggml.tokens` / `.scores` / `.token_type` / `.add_bos_token` 等键；
2. 导出 `*.vocab`（JSONL，一行一 token：id/token/score/type）；
3. 写 manifest：GGUF 文件名 + sha256 前缀 + token 数 + vocab 文件 sha256，
   与模型版本绑定；单测校验 manifest 与 GGUF 实际哈希一致（M0 验收项）。
4. 提供 `count_tokens()`：口径唯一 —— 按导出的 vocab 做贪心最长匹配，
   不依赖任何外部 vocab 文件。
"""
from __future__ import annotations

import hashlib
import json
import struct
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

GGUF_MAGIC = b"GGUF"

T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32 = 0, 1, 2, 3, 4, 5
T_FLOAT32, T_BOOL, T_STRING, T_ARRAY, T_UINT64, T_INT64, T_FLOAT64 = 6, 7, 8, 9, 10, 11, 12
_SCALAR = {
    T_UINT8: "B", T_INT8: "b", T_UINT16: "<H", T_INT16: "<h",
    T_UINT32: "<I", T_INT32: "<i", T_FLOAT32: "<f", T_UINT64: "<Q",
    T_INT64: "<q", T_FLOAT64: "<d",
}


class GgufError(ValueError):
    pass


@dataclass
class Gguf:
    version: int
    n_tensors: int
    n_kv: int
    kv: dict = field(default_factory=dict)

    @property
    def tokens(self) -> list[str]:
        return list(self.kv.get("tokenizer.ggml.tokens") or [])


def _rd_string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    return f.read(n).decode("utf-8", "replace")


def _rd_value(f, vtype: int):
    if vtype == T_BOOL:
        return struct.unpack("<B", f.read(1))[0] != 0
    if vtype == T_STRING:
        return _rd_string(f)
    if vtype == T_ARRAY:
        (atype,) = struct.unpack("<I", f.read(4))
        (count,) = struct.unpack("<Q", f.read(8))
        if atype == T_STRING:
            return [_rd_string(f) for _ in range(count)]
        fmt = _SCALAR.get(atype)
        if fmt is None:
            raise GgufError(f"nested array type {atype} unsupported")
        size = struct.calcsize(fmt)
        return [struct.unpack(fmt, f.read(size))[0] for _ in range(count)]
    fmt = _SCALAR.get(vtype)
    if fmt is None:
        raise GgufError(f"unknown gguf value type {vtype}")
    return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]


def read_metadata(path: str | Path, max_kv: int = 4096) -> Gguf:
    """只读 header + KV，不读 tensor info —— 大模型文件也能秒级完成。"""
    p = Path(path)
    with p.open("rb") as f:
        if f.read(4) != GGUF_MAGIC:
            raise GgufError(f"not a GGUF file: {p.name}")
        (version,) = struct.unpack("<I", f.read(4))
        if version not in (2, 3):
            raise GgufError(f"unsupported GGUF version {version}")
        (n_tensors,) = struct.unpack("<Q", f.read(8))
        (n_kv,) = struct.unpack("<Q", f.read(8))
        kv: dict = {}
        for _ in range(min(n_kv, max_kv)):
            key = _rd_string(f)
            (vtype,) = struct.unpack("<I", f.read(4))
            kv[key] = _rd_value(f, vtype)
        return Gguf(version=version, n_tensors=n_tensors, n_kv=n_kv, kv=kv)


def sha256_prefix(path: str | Path, n: int = 16) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def export_vocab(gguf_path: str | Path, out_path: str | Path | None = None) -> dict:
    """从生产 GGUF 导出 vocab（JSONL）并返回 manifest 条目。"""
    g = read_metadata(gguf_path)
    tokens = g.tokens
    if not tokens:
        raise GgufError(f"no tokenizer.ggml.tokens in {Path(gguf_path).name}")
    scores = list(g.kv.get("tokenizer.ggml.scores") or [0.0] * len(tokens))
    types = list(g.kv.get("tokenizer.ggml.token_type") or [1] * len(tokens))
    out = Path(out_path) if out_path else Path(gguf_path).with_suffix(".vocab")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i, tok in enumerate(tokens):
            rec = {"id": i, "token": tok, "score": float(scores[i] if i < len(scores) else 0.0),
                   "type": int(types[i] if i < len(types) else 1)}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    manifest = {
        "gguf_file": Path(gguf_path).name,
        "gguf_sha256_prefix": sha256_prefix(gguf_path),
        "gguf_version": g.version,
        "n_vocab": len(tokens),
        "vocab_file": str(out),
        "vocab_sha256_prefix": sha256_prefix(out),
        "tokenizer_config": {
            "add_bos_token": bool(g.kv.get("tokenizer.ggml.add_bos_token", False)),
            "bos_token_id": g.kv.get("tokenizer.ggml.bos_token_id"),
            "eos_token_id": g.kv.get("tokenizer.ggml.eos_token_id"),
            "chat_template": (g.kv.get("tokenizer.chat_template") or "")[:200],
            "pre": str(g.kv.get("tokenizer.ggml.pre", "default")),
        },
        "source": "gguf-embedded",
    }
    return manifest


def write_manifest(manifest: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_manifest(manifest_path: str | Path, gguf_path: str | Path) -> dict:
    """单测口径：manifest 与 GGUF 实际哈希一致性校验（M0 验收 4）。"""
    m = load_manifest(manifest_path)
    actual = sha256_prefix(gguf_path, len(m["gguf_sha256_prefix"]))
    vocab_actual = sha256_prefix(m["vocab_file"], len(m["vocab_sha256_prefix"]))
    return {
        "gguf_ok": actual == m["gguf_sha256_prefix"],
        "vocab_ok": vocab_actual == m["vocab_sha256_prefix"],
        "name_ok": Path(gguf_path).name == m["gguf_file"],
        "n_vocab_ok": _count_lines(m["vocab_file"]) == m["n_vocab"],
        "actual_gguf_sha": actual,
        "actual_vocab_sha": vocab_actual,
    }


def _count_lines(p: str | Path) -> int:
    with Path(p).open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


class Tokenizer:
    """口径唯一：用导出的 vocab 做贪心最长匹配计数（无外部依赖、可复现）。

    这是 chunks token 预算的**保守上界口径**（CJK 一字一 token 接近真实 BPE 量级），
    确定性 100%，满足 L0/L1 字节级一致承诺；真实推理时以 llama-server 的
    tokens 计数为准，两者差异由 [chunks] tokenizer_source 标注。
    """

    def __init__(self, vocab_path: str | Path):
        self.vocab: list[str] = []
        with Path(vocab_path).open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    self.vocab.append(json.loads(line)["token"])
        # 按长度降序，贪心最长匹配；控制字符/特殊 token 忽略文本本体
        self._by_len = sorted((t for t in self.vocab if t and not t.startswith("<|")),
                              key=len, reverse=True)
        self._maxlen = max((len(t) for t in self._by_len), default=1)
        self._set = set(self._by_len)

    def count(self, text: str) -> int:
        if not text:
            return 0
        s = unicodedata.normalize("NFC", text)
        n, i = 0, 0
        while i < len(s):
            hit = 0
            for L in range(min(self._maxlen, len(s) - i), 0, -1):
                if s[i:i + L] in self._set:
                    hit = L
                    break
            n += 1
            i += hit or 1
        return n
