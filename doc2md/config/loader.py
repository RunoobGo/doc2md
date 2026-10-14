"""配置装载（方案 5.7 + 附录 A；上游锚点 v2.3 §4.3 / v2.4 附录 C-1）。

读取顺序：DEFAULT_TOML → 显式 path → ./doc2md.toml → $DOC2MD_HOME/doc2md.toml。
CH-456（T9）：`run.profile` 三档在此落地为键集覆盖；用户显式配置优先于 profile。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .default_toml import DEFAULT_TOML, TOML_BASENAME

# 运行 profile 三档（方案 1.2 冻结契约 + 第七章低配机口径）。
# 覆盖发生在「默认值之后、用户显式配置之后」：显式键永远赢过 profile。
PROFILES: dict[str, dict[str, Any]] = {
    "lite":     {"s1.chunk_pages": 16, "perf.workers": 1, "perf.queue_async": True},
    "standard": {"s1.chunk_pages": 32, "perf.workers": 0, "perf.queue_async": False},
    "strict":   {"s1.chunk_pages": 32, "perf.workers": 0, "perf.queue_async": True,
                 "perf.min_free_mb": 2048},
}

# 冻结键：只接受既定取值（T9：把「看起来可配」收敛成「配错即报」）。
# FR-127 红线：消毒失败即拒收，不设降级（方案 6.1）——该键不是行为开关。
FROZEN_KEYS: dict[str, tuple[Any, ...]] = {
    "security.sanitize_fail_action": ("reject",),
}


def _base() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_TOML)


def load(path: str | os.PathLike | None = None) -> dict:
    cfg = _base()
    cand = Path(path) if path else None
    explicit: set[str] = set()
    if cand is None:
        for p in (Path.cwd() / TOML_BASENAME,
                  Path(os.environ.get("DOC2MD_HOME", "~/.doc2md")).expanduser() / TOML_BASENAME):
            if p.is_file():
                cand = p
                break
    if cand is not None and Path(cand).is_file():
        user = tomllib.loads(Path(cand).read_text(encoding="utf-8"))
        _deep_merge(cfg, user)
        explicit = _dotted_keys(user)
    _apply_profile(cfg, explicit)
    validate(cfg)
    return cfg


def _dotted_keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        dotted = f"{prefix}{k}"
        out.add(dotted)
        if isinstance(v, dict):
            out |= _dotted_keys(v, f"{dotted}.")
    return out


def _apply_profile(cfg: dict, explicit: set[str]) -> None:
    """按 [run].profile 覆盖键集；用户显式设置过的键不覆盖。"""
    name = str(cfg.get("run", {}).get("profile", "standard"))
    if name not in PROFILES:
        raise ValueError(f"unknown run.profile: {name!r}（可选：{sorted(PROFILES)}）")
    for dotted, value in PROFILES[name].items():
        if dotted not in explicit:
            _set(cfg, dotted, value)


def _set(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
        if not isinstance(cur, dict):
            raise ValueError(f"profile target conflicts with scalar: {dotted}")
    cur[parts[-1]] = value


def validate(cfg: dict) -> None:
    """启动期校验：冻结键取值（T9）。用法错误即 ValueError，不进运行期静默失效。"""
    for dotted, allowed in FROZEN_KEYS.items():
        v = get(cfg, dotted)
        if v not in allowed:
            raise ValueError(f"{dotted} 只支持 {list(allowed)}，当前为 {v!r}")


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


DEFAULT_ROOT_NAME = "work"          # DEFAULT_TOML 里 [paths].root 的字面值


def root_setting(cfg: dict) -> Path:
    """[paths].root 自身：绝对值原样，相对值相对当前工作目录。"""
    return Path(str(get(cfg, "paths.root", DEFAULT_ROOT_NAME))).expanduser()


def path_setting(cfg: dict, dotted: str, root: str | os.PathLike | None = None,
                 default: str | None = None) -> Path:
    """路径型配置统一解析（CH-432）。

    默认配置里的相对路径都带 `[paths].root` 前缀（"work/out"、"work/reports/backups"）；
    直接 Path(value) 会让 --root / DOC2MD_HOME 只改 state.sqlite 的位置，
    而备份、tmp、日志、产物仍写死到 ./work（实测 `doc2md cron --root X` 把快照
    写进了 ./work/reports/backups）。

    口径：绝对值原样用；相对值若以 root 段名（默认根名或实际根名）开头，
    剥掉该前缀段后挂到实际 root 下；否则相对 root 解析。
    """
    v = get(cfg, dotted, default)
    if v is None:
        return Path(root) if root is not None else root_setting(cfg)
    p = Path(str(v)).expanduser()
    if p.is_absolute():
        return p
    base = Path(root) if root is not None else root_setting(cfg)
    if dotted == "paths.root":
        return p                              # root 自身不剥前缀、不挂到自己下面
    parts = p.parts
    if parts and parts[0] in (base.name, DEFAULT_ROOT_NAME):
        parts = parts[1:]
    return base.joinpath(*parts) if parts else base
