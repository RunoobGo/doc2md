"""配置装载（方案 5.7 + 附录 A；上游锚点 v2.3 §4.3 / v2.4 附录 C-1）。

读取顺序：DEFAULT_TOML → $DOC2MD_HOME/doc2md.toml → ./doc2md.toml → 显式 path
（后者覆盖前者，深合并；CH-485 与 README/附录 A 统一）。
CH-456（T9）：`run.profile` 三档在此落地为键集覆盖；用户显式配置优先于 profile。
CH-469（O-15）：`SPEC` 表驱动的类型 / 区间 / 枚举校验 + 未知键警告。
"""
from __future__ import annotations

import ipaddress
import os
import re
import tomllib
import warnings
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

# CH-471 SVC-6（D4）：可经 `config.load()` / `convert(cfg=...)` 显式设置、但**禁止经
# 运行期 PUT /settings 修改**的红线键。offline_assert 是离线安全网开关（默认 true）：
# 代码/配置文件按 profile 显式关掉是合法用法，但「在线服务途中被一次 PUT 关掉」就是
# 静默解除断网守护——所以它进 API 不可变集合，而不是 FROZEN_KEYS（后者连 convert 的
# 合法 cfg 也会拦，破坏既有契约）。值 → 允许集合；PUT 命中即 400。
RUNTIME_IMMUTABLE_KEYS: dict[str, tuple[Any, ...]] = {
    "run.offline_assert": (True,),
    # CH-489：`paths.out` 在启动时被请求/删除/重建路径闭包读取；运行期改值会
    # 让旧产物在删除与 `/docs/<id>/markdown` 两侧同时失联。与 paths.root 同口径，
    # 改输出根必须改配置文件后重启。
    "paths.out": (),
    # `paths.logs` 在 create_app 时绑定 RotatingJsonlWriter；运行期改值不会切换
    # 已打开的文件，只会让 /settings 与实际写入位置不一致。与 out 同口径。
    "paths.logs": (),
    # CH-473（R5-SEC-1 / D4 闭环）：opt-in 键本身也不许在线改。原集合只冻结
    # offline_assert，于是两步 PUT 即可掏空 D4——先 `PUT {"api":{"allow_remote_bind":true}}`
    # （单步合法），再 `PUT {"api":{"bind":"0.0.0.0"}}`（candidate 里 opt-in 已为真，
    # validate 的 loopback 检查不再触发），且覆写落盘跨重启生效。现将 opt-in 收敛为
    # 「改配置文件 + 重启」这一条 deliberate 路径；配置文件里本就 true 的部署，
    # PUT 改回 false 同样被拒（缩小暴露面与扩大暴露面同为红线变更）。
    "api.allow_remote_bind": (False,),
}


class ConfigError(ValueError):
    """配置校验失败（CH-469 O-15）。

    刻意继承 `ValueError`：不改既有调用方的捕获语义；CLI 侧单独映射为
    `EXIT_USAGE`（用法错误），与运行期故障（`EXIT_SYS`）分流。
    """


def _int(lo: int, hi: int) -> tuple[str, Any]:
    return ("int", (lo, hi))


def _num(lo: float, hi: float) -> tuple[str, Any]:
    return ("num", (lo, hi))


def _enum(*values: str) -> tuple[str, Any]:
    return ("enum", values)


BOOL: tuple[str, Any] = ("bool", None)
STR: tuple[str, Any] = ("str", None)
STRLIST: tuple[str, Any] = ("strlist", None)
OCTAL: tuple[str, Any] = ("octal", None)

# 键集契约（CH-469 O-15）：SPEC 是唯一权威清单——DEFAULT_TOML 里出现、SPEC 里没有的键
# 视为缺陷（单测校验两侧键集相等），SPEC 里的键则允许缺省（validate 只校验出现的值）。
# 区间刻意给得宽：本表拦的是「明显配错」（workers=-1、rotate_mb=0、port=0、
# chunk_pages 是字符串），不是替用户做性能决策；调优请改默认值而不是绕开校验。
SPEC: dict[str, tuple[str, Any]] = {
    # [paths] —— 全部字符串；相对路径按 path_setting 口径挂到 root 下（CH-432）
    "paths.root": STR,
    "paths.out": STR,
    "paths.reports": STR,
    "paths.logs": STR,
    "paths.tmp": STR,
    "paths.vendor_models": STR,
    # [run]
    "run.profile": _enum(*sorted(PROFILES)),
    "run.offline_assert": BOOL,
    # [s1] —— chunk_pages = 0 是合法口径（整本一次读完）
    "s1.chunk_pages": _int(0, 100_000),
    "s1.legacy_doc_backend": _enum("auto", "libreoffice", "word", "none"),
    "s1.legacy_doc_timeout_s": _int(1, 600),
    # [s1_5]
    "s1_5.sample_pages": _enum("auto", "even5", "legacy3"),
    "s1_5.min_chars_per_page": _int(0, 1_000_000),
    "s1_5.reject_on_any_bad_page": BOOL,
    "s1_5.page_detail_in_report": BOOL,
    # [s2]
    "s2.mode": _enum("auto", "single", "two_phase"),
    "s2.max_pages_per_shard": _int(1, 100_000),
    "s2.shard_overlap_pages": _int(0, 1_000),
    "s2.dedupe_overlap": BOOL,
    # [chunks] —— P-49：vocab 来源固化，不接受独立 vocab 文件名
    "chunks.tokenizer": _enum("gguf-embedded"),
    "chunks.manifest": STR,
    # [s4] —— anchor 是唯一保留的内部链接渲染口径（s4_render）
    "s4.preserve_marks_strike": BOOL,
    "s4.link_internal": _enum("plain", "anchor"),
    # [perf]
    "perf.workers": _int(0, 64),
    "perf.queue_batch_factor": _int(1, 64),
    "perf.queue_async": BOOL,
    "perf.enqueue_hash_threads": _int(1, 32),
    "perf.sync_min_parallel": _int(1, 100_000),
    "perf.min_free_mb": _int(0, 10_000_000),
    "perf.disk_safety_factor": _int(0, 100),
    "perf.stale_job_minutes": _int(0, 100_000),
    "perf.stale_check_seconds": _int(0, 100_000),
    "perf.batch_file_timeout_s": _int(0, 10_000_000),
    # [watch]
    "watch.stable_seconds": _num(0, 10_000),
    "watch.follow_symlinks": BOOL,
    "watch.max_depth": _int(1, 1_000),
    "watch.skip_hidden": BOOL,
    "watch.max_files_per_dir": _int(1, 10_000_000),
    # [api]
    "api.bind": STR,
    "api.port": _int(1, 65_535),
    "api.allow_remote_bind": BOOL,
    "api.token_roles": STRLIST,
    "api.revoke_effective_seconds": _num(0, 10_000),
    "api.base_revision_required": BOOL,
    # [llm]
    "llm.mode": _enum("offline", "server"),
    "llm.fallback": BOOL,
    "llm.timeout_ms": _int(1, 10_000_000),
    "llm.retries": _int(0, 10),
    "llm.model": STR,
    "llm.bind": STR,
    "llm.port": _int(1, 65_535),
    "llm.auth": BOOL,
    "llm.op_whitelist": STRLIST,
    # [logs] —— rotate_mb = 0 会让每次写入都触发轮转（自我 DoS），下限 0.1
    "logs.rotate_mb": _num(0.1, 1_000_000),
    "logs.rotate_keep": _int(1, 1_000),
    # [ops]
    "ops.events_retention_days": _int(1, 100_000),
    "ops.tasks_retention_days": _int(1, 100_000),
    "ops.backup_local_dir": STR,
    "ops.backup_external_dir": STR,      # 空串 = 未配置（cron 记 skipped，不算失败）
    "ops.backup_external_encrypted": BOOL,
    "ops.backup_rotation_days": _int(1, 100_000),
    "ops.work_dir_mode": OCTAL,
    # [security]
    "security.max_name_bytes": _int(16, 4096),
    "security.sanitize_fail_action": _enum(*FROZEN_KEYS["security.sanitize_fail_action"]),
}

_OCTAL_RE = re.compile(r"[0-7]{3}")
_LAST_UNKNOWN: list[str] = []


def last_unknown_keys() -> list[str]:
    """最近一次 load() 遇到的未知键（doctor 展示用；不含值）。"""
    return list(_LAST_UNKNOWN)


def _base() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_TOML)


def load(path: str | os.PathLike | None = None) -> dict:
    """默认 → $DOC2MD_HOME → 当前目录 → 显式 --config（后者覆盖前者）。

    CH-483（R8-CFG-1）：旧实现只读取“第一个命中的文件”，与 README 宣称的层叠
    顺序不符——home 配置 + 项目配置并存时，后者会被静默忽略。现在按优先级
    逐层深度合并；显式 --config 仍必须存在，且优先级最高。
    """
    cfg = _base()
    _LAST_UNKNOWN.clear()
    home = (Path(os.environ.get("DOC2MD_HOME", "~/.doc2md")).expanduser()
            / TOML_BASENAME)
    cwd = Path.cwd() / TOML_BASENAME
    sources: list[Path] = [home, cwd]
    if path is not None:
        explicit_path = Path(path)
        if not explicit_path.is_file():
            raise ConfigError(f"--config 指向的文件不存在：{path}")
        sources.append(explicit_path)
    seen: set[Path] = set()
    explicit: set[str] = set()
    for cand in sources:
        try:
            key = cand.resolve()
        except OSError:
            key = cand
        if key in seen:
            continue
        seen.add(key)
        if not cand.is_file():
            # 未显式指定的候选缺失不是错误；显式路径已在上面拦下。
            continue
        user = _read(cand)
        # CH-469（O-15）：未知键 = 静默失效的拼写错误，装载期点名（不改值、不阻断启动）
        for dotted in unknown_keys(user):
            _LAST_UNKNOWN.append(f"{cand}:{dotted}")
            warnings.warn(f"{cand}: 未知配置键 {dotted}（不生效，请核对方案 附录 A）",
                          UserWarning, stacklevel=2)
        _deep_merge(cfg, user)
        explicit |= _dotted_keys(user)
    _apply_profile(cfg, explicit)
    validate(cfg)
    return cfg


def _read(p: Path) -> dict:
    """配置文件解析：语法错与读不到都收敛为 ConfigError。

    原实现让 `tomllib.TOMLDecodeError` 直接冒泡，CLI 只能把它当未知异常收进
    `EXIT_SYS`；「doc2md.toml 写错了」和「解析栈炸了」混在同一个退出码里。
    """
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{p}: 配置无法解析（{type(exc).__name__}: {str(exc)[:200]}）") from exc


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
        raise ConfigError(f"unknown run.profile: {name!r}（可选：{sorted(PROFILES)}）")
    for dotted, value in PROFILES[name].items():
        if dotted not in explicit:
            _set(cfg, dotted, value)


def _set(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
        if not isinstance(cur, dict):
            raise ConfigError(f"profile target conflicts with scalar: {dotted}")
    cur[parts[-1]] = value


def validate(cfg: dict) -> None:
    """启动期校验（CH-469 O-15）：分表形状 + 类型 / 区间 / 枚举 + 冻结键。

    口径：配置错即用法错误 → `ConfigError`，不留到运行期静默失效
    （原实现只查 `FROZEN_KEYS` 一项，`workers = "8"` 之类要到进程池建不起来才炸）。
    只校验 SPEC 里出现的键：SPEC 是清单，不是必填表。
    """
    if not isinstance(cfg, dict):
        raise ConfigError(f"配置根必须是表（当前为 {type(cfg).__name__}）")
    for sec in {k.split(".", 1)[0] for k in SPEC}:
        v = cfg.get(sec)
        if v is not None and not isinstance(v, dict):
            raise ConfigError(f"[{sec}] 必须是表（当前为 {type(v).__name__}）")
    for dotted, spec in SPEC.items():
        parts = dotted.split(".")
        if _leaf(cfg, parts) is _MISSING:
            continue
        _check(dotted, spec, get(cfg, dotted))
    # 冻结键独立成段：报错口径要写明「这是红线，不是行为开关」
    for dotted, allowed in FROZEN_KEYS.items():
        v = get(cfg, dotted)
        if v not in allowed:
            raise ConfigError(f"{dotted} 只支持 {list(allowed)}，当前为 {v!r}")
    # CH-471 SVC-5（D4）：默认强制 loopback 绑定。api/llm 服务只应监听本机回环；
    # 绑到 0.0.0.0 / 局域网 IP 会把离线单机变成对外可达的服务（NFR-1 违背）。
    # 确需远程暴露须显式 api.allow_remote_bind = true（该键本身不设冻结，是有意 opt-in）。
    if not get(cfg, "api.allow_remote_bind"):
        for dotted in ("api.bind", "llm.bind"):
            v = get(cfg, dotted)
            if v is not None and not is_loopback_bind(str(v)):
                raise ConfigError(
                    f"{dotted}={v!r} 非本机回环：默认只允许 loopback 绑定，"
                    f"如需对外暴露请显式设 api.allow_remote_bind = true")
    # CH-489：`sweep_tmp` 会删除 `paths.tmp` 下超过保护窗的全部条目。若 tmp
    # 配成工作根（`work` / `.`）或其祖先，cron/serve 启动一次就会清掉 state.sqlite、
    # out/logs 等正文数据。这里按解析后的真实路径判包含关系，拒绝这种配置。
    root_path = root_setting(cfg).resolve()
    tmp_path = path_setting(cfg, "paths.tmp", root_path,
                            str(root_path / "tmp")).resolve()
    try:
        root_path.relative_to(tmp_path)
    except ValueError:
        pass
    else:
        raise ConfigError(
            f"paths.tmp={get(cfg, 'paths.tmp')!r} 不得等于工作根或其祖先 "
            f"（解析后 root={root_path}, tmp={tmp_path}）；sweep_tmp 会清空该目录")


def check_runtime_immutable(candidate: dict, baseline: dict) -> None:
    """运行期配置变更门禁（CH-471 SVC-6 / D4）。

    `RUNTIME_IMMUTABLE_KEYS` 里的红线键允许出现在 `config.load()` / `convert(cfg=...)`
    的显式配置里（合法的按 profile 用法），但不许被 PUT /settings 或 reload 从基线
    **改动**——否则在线途中一次请求（或改文件后 reload）就能关掉离线守护。改回同值
    （无实际变化）放行；确需调整须重启进程。
    """
    for dotted in RUNTIME_IMMUTABLE_KEYS:
        cur_v, base_v = get(candidate, dotted), get(baseline, dotted)
        if cur_v != base_v:
            raise ConfigError(
                f"{dotted} 为运行期不可变红线键，禁止经 PUT /settings 或 reload 修改"
                f"（基线 {base_v!r} → 请求 {cur_v!r}）；确需调整请改配置文件后重启")


class _Sentinel:
    __slots__ = ()


_MISSING = _Sentinel()


def _leaf(cfg: dict, parts: list[str]) -> Any:
    cur: Any = cfg
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _fmt(v: Any) -> str:
    return f"{v!r}（{type(v).__name__}）"


def is_loopback_bind(value: str) -> bool:
    """判断 bind 值是否只监听本机回环（CH-471 SVC-5；CH-473 公开供 CLI --host 复用）。

    口径：`localhost` 或 IP `is_loopback`（127.0.0.0/8、::1）为真；
    `0.0.0.0` / `::`（unspecified，全部接口）与局域网 IP 或无法解析的主机名一律为假
    —— 校验取保守侧，把「不能确定是回环」当作「对外暴露」。
    """
    host = value.strip()
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):   # 容忍 IPv6 的 [::1] 写法
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# 内部旧引用的兼容别名（CH-473 公开化时保留，避免遗漏调用点静默换语义）
_is_loopback_bind = is_loopback_bind


def _check(dotted: str, spec: tuple[str, Any], val: Any) -> None:
    kind, arg = spec
    if kind == "bool":
        if not isinstance(val, bool):
            raise ConfigError(f"{dotted} 必须是布尔（true/false），当前为 {_fmt(val)}")
    elif kind == "int":
        # bool 是 int 的子类，但 `workers = true` 不是数量 → 显式拒绝
        if isinstance(val, bool) or not isinstance(val, int):
            raise ConfigError(f"{dotted} 必须是整数，当前为 {_fmt(val)}")
        _range(dotted, val, arg)
    elif kind == "num":
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigError(f"{dotted} 必须是数值，当前为 {_fmt(val)}")
        _range(dotted, val, arg)
    elif kind == "str":
        if not isinstance(val, str):
            raise ConfigError(f"{dotted} 必须是字符串，当前为 {_fmt(val)}")
    elif kind == "enum":
        if val not in arg:
            raise ConfigError(f"{dotted} 只支持 {list(arg)}，当前为 {_fmt(val)}")
    elif kind == "strlist":
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise ConfigError(f"{dotted} 必须是字符串数组，当前为 {_fmt(val)}")
    elif kind == "octal":
        # CH-473（R5-Q-1）：三位八进制字符串（如 "700"）**或** TOML 八进制字面量
        # （`work_dir_mode = 0o700` 经 tomllib 解析为 int 448）都收。原实现只认
        # 字符串，而 ops/backup._parse_mode 的 docstring 声称两种形态均可——运维按
        # docstring 写法直接启动即被 validate 拒（口径分裂，拒绝侧无安全后果）。
        # 两种形态等价收敛到 0..0o777。
        if isinstance(val, str) and _OCTAL_RE.fullmatch(val):
            pass
        elif isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 0o777:
            pass
        else:
            raise ConfigError(
                f"{dotted} 必须是八进制权限（\"700\" 或 0o700），当前为 {_fmt(val)}")
    else:                                  # pragma: no cover — SPEC 自身的缺陷
        raise ConfigError(f"{dotted} 的校验规则未知：{kind!r}")


def _range(dotted: str, val: Any, bounds: tuple[Any, Any]) -> None:
    lo, hi = bounds
    if not lo <= val <= hi:
        raise ConfigError(f"{dotted} 必须在 [{lo}, {hi}] 区间内，当前为 {val}")


SECTIONS: frozenset[str] = frozenset(k.split(".", 1)[0] for k in SPEC)


def _section_known(prefix: str) -> bool:
    return prefix in SECTIONS or any(k.startswith(f"{prefix}.") for k in SPEC)


def unknown_keys(user: dict) -> list[str]:
    """用户配置里 SPEC 之外的键（拼错的键不会生效，但原实现全程静默）。

    未知分表只报**表名**一条（`[mystery]` 不必把里面每个键都念一遍）。
    """
    out: list[str] = []
    for dotted in sorted(_dotted_keys(user)):
        if dotted in SPEC or _section_known(dotted):
            continue                                # 已知键 / 已知分表（含空表）
        parents = dotted.split(".")[:-1]
        if any(not _section_known(".".join(parents[:i + 1])) for i in range(len(parents))):
            continue                                # 所属未知分表已单独报过
        out.append(dotted)
    return out


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
