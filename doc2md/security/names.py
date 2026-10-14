"""FR-127 文件名消毒（上游蓝本 B-11 + 本轮实测修正）。

与上游蓝本 B-11 的差异（登记为 CH-422，见方案 第十一章）：
1. 长度口径统一为 UTF-8 **字节**预算 ≤ maxlen（方案代码按字符切片 `[:128]`，
   但其自带单测断言 `len(out.encode("utf-8")) <= 128` —— CJK 名两侧口径冲突，
   128 个汉字可达 384 字节，测试必红）。本实现按字节预算截断且不切碎多字节字符，
   截断时优先保住扩展名。
2. 显式拒绝消毒后仍不安全的名字（红线：消毒失败即拒收，不设降级，方案 6.1）。
"""
from __future__ import annotations

import re
import unicodedata

from ..errors import E4003_FileNameUnsafe

WIN_RESERVED = {"con", "prn", "aux", "nul"} \
    | {f"com{i}" for i in range(1, 10)} \
    | {f"lpt{i}" for i in range(1, 10)}

SAFE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._\- ]")
DEFAULT_MAX_BYTES = 128


def _blen(s: str) -> int:
    return len(s.encode("utf-8", "surrogatepass"))


def _cut_bytes(s: str, budget: int) -> str:
    """按 UTF-8 字节预算截断，不切碎多字节字符。"""
    out, used = [], 0
    for ch in s:
        n = _blen(ch)
        if used + n > budget:
            break
        out.append(ch)
        used += n
    return "".join(out)


def _fit(stem: str, dot: str, ext: str, maxlen: int) -> str:
    """字节预算内尽量保留扩展名。"""
    if _blen(stem + dot + ext) <= maxlen:
        return stem + dot + ext
    ext_keep = _cut_bytes(ext, max(0, maxlen - len(dot) - 1))
    stem_budget = maxlen - _blen(dot + ext_keep) - 1
    stem_cut = _cut_bytes(stem, max(1, stem_budget))
    return (stem_cut + dot + ext_keep)[:maxlen] or "unnamed"


def sanitize_name(raw: str, maxlen: int = DEFAULT_MAX_BYTES) -> str:
    """返回可安全落库 / 进 zip / 进 front-matter 的名字；原始名另存 source_name_raw。

    顺序与上游蓝本 B-11 一致：NFC → 去 NUL → 剥路径 → 非白名单字符转 `_` → strip(" .")
    → 保留名重写 → 字节预算截断（此处修正，见模块 docstring）。
    """
    name = unicodedata.normalize("NFC", raw or "").replace("\x00", "")
    name = SAFE.sub("_", name.split("/")[-1].split("\\")[-1]).strip(" .")
    if not name:
        return "unnamed"
    stem, dot, ext = name.rpartition(".")
    if not stem:                                  # 无扩展名：rpartition 返回 ("", "", name)
        stem, dot, ext = name, "", ""             # 回退全名为 stem，保留名检查不旁路
    if stem.lower() in WIN_RESERVED:
        stem = f"_{stem}"
    out = _fit(stem, dot, ext, maxlen).strip(" .")
    if not out:
        return "unnamed"
    # 兜底断言：消毒失败即拒收（由调用方转 E4003）
    assert_name_safe(out, maxlen)
    return out


class UnsafeNameError(E4003_FileNameUnsafe, ValueError):
    """消毒结果仍不安全 → E4003（红线：消毒失败即拒收，不设降级，方案 6.1）。

    CH-434：原实现抛裸 ValueError，API 层落到 500；FR-127 的拒收必须是 400/E4003，
    否则「输入安全」门禁在 HTTP 面上不可判定。保留 ValueError 混入，
    使既有 `except ValueError` 调用方行为不变。
    """


def assert_name_safe(name: str, maxlen: int = DEFAULT_MAX_BYTES) -> None:
    """双闸第二道：zip 条目名 / front-matter 写入前二次断言。"""
    problems = []
    if ".." in name:
        problems.append("dot-dot")
    if "/" in name or "\\" in name:
        problems.append("separator")
    if name != name.strip(" ."):
        problems.append("edge-dots-or-space")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in name):
        problems.append("control-char")
    if _blen(name) > maxlen:
        problems.append("too-long")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.lower() in WIN_RESERVED:
        problems.append("win-reserved")
    if problems:
        raise UnsafeNameError(f"unsafe name {name!r}: {','.join(problems)}")


def assert_entry_safe(entry: str) -> None:
    """zip 条目名断言（允许子目录，但禁止越界与绝对路径）。"""
    norm = entry.replace("\\", "/")
    if norm.startswith("/") or norm.startswith("~"):
        raise UnsafeNameError(f"absolute zip entry {entry!r}")
    parts = [p for p in norm.split("/") if p]
    if any(p == ".." for p in parts):
        raise UnsafeNameError(f"traversing zip entry {entry!r}")
    for p in parts:
        stem = p.rsplit(".", 1)[0] if "." in p and not p.startswith(".") else p
        if stem.lower() in WIN_RESERVED:
            raise UnsafeNameError(f"reserved-name zip entry {entry!r}")
