"""M0 测试夹具：合成最小合法 GGUF v3（只写 header + KV，无 tensor）。

字段顺序按 GGUF 规范：key(string) → vtype(uint32) → value；
array = elem_type(uint32) → count(uint64) → elements。
"""
from __future__ import annotations

import struct
from pathlib import Path

T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32 = 0, 1, 2, 3, 4, 5
T_FLOAT32, T_BOOL, T_STRING, T_ARRAY, T_UINT64, T_INT64, T_FLOAT64 = 6, 7, 8, 9, 10, 11, 12

DEFAULT_TOKENS = ["的", "是", "一个人", "test", "，", "。"]


def _wstr(f, s: str) -> None:
    b = s.encode("utf-8")
    f.write(struct.pack("<Q", len(b)))
    f.write(b)


def _key(f, k: str) -> None:
    _wstr(f, k)


def _scalar(f, vtype: int, v) -> None:
    f.write(struct.pack("<I", vtype))
    if vtype == T_BOOL:
        f.write(struct.pack("<B", 1 if v else 0))
    elif vtype == T_STRING:
        _wstr(f, v)
    elif vtype == T_UINT32:
        f.write(struct.pack("<I", v))
    elif vtype == T_FLOAT32:
        f.write(struct.pack("<f", v))
    elif vtype == T_UINT64:
        f.write(struct.pack("<Q", v))
    else:
        raise ValueError(f"unsupported scalar type {vtype}")


def _array(f, etype: int, items: list) -> None:
    f.write(struct.pack("<I", T_ARRAY))
    f.write(struct.pack("<I", etype))
    f.write(struct.pack("<Q", len(items)))
    for it in items:
        if etype == T_STRING:
            _wstr(f, it)
        elif etype == T_FLOAT32:
            f.write(struct.pack("<f", it))
        elif etype == T_UINT32:
            f.write(struct.pack("<I", it))
        elif etype == T_BOOL:
            f.write(struct.pack("<B", 1 if it else 0))
        else:
            raise ValueError(f"unsupported array elem type {etype}")


def build_gguf(path: str | Path, tokens: list[str] | None = None,
               version: int = 3, extra_kv: dict | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    toks = tokens or DEFAULT_TOKENS
    with p.open("wb") as f:
        f.write(b"GGUF")
        f.write(struct.pack("<I", version))
        f.write(struct.pack("<Q", 0))            # n_tensors
        f.write(struct.pack("<Q", 8))            # n_kv
        _key(f, "general.architecture")
        _scalar(f, T_STRING, "doc2md-test")
        _key(f, "tokenizer.ggml.model")
        _scalar(f, T_STRING, "gpt2")
        _key(f, "tokenizer.ggml.tokens")
        _array(f, T_STRING, toks)
        _key(f, "tokenizer.ggml.scores")
        _array(f, T_FLOAT32, [-12.0 + i for i in range(len(toks))])
        _key(f, "tokenizer.ggml.token_type")
        _array(f, T_UINT32, [1] * len(toks))
        _key(f, "tokenizer.ggml.add_bos_token")
        _scalar(f, T_BOOL, True)
        _key(f, "tokenizer.ggml.bos_token_id")
        _scalar(f, T_UINT32, 0)
        _key(f, "tokenizer.ggml.eos_token_id")
        _scalar(f, T_UINT32, 1)
        for k, v in (extra_kv or {}).items():
            _key(f, k)
            _scalar(f, T_STRING, str(v))
    return p
