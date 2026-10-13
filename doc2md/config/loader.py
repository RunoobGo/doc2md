"""配置装载（锚点 v2.3 §4.3 / v2.4 附录 C-1）。

读取顺序：DEFAULT_TOML → 显式 path → ./doc2md.toml → $DOC2MD_HOME/doc2md.toml。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .default_toml import DEFAULT_TOML, TOML_BASENAME


def _base() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_TOML)


def load(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg = _base()
    cand = Path(path) if path else None
    if cand is None:
        for p in (Path.cwd() / TOML_BASENAME,
                  Path(os.environ.get("DOC2MD_HOME", "~/.doc2md")).expanduser() / TOML_BASENAME):
            if p.is_file():
                cand = p
                break
    if cand is not None and Path(cand).is_file():
        _deep_merge(cfg, tomllib.loads(Path(cand).read_text(encoding="utf-8")))
    return cfg


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
