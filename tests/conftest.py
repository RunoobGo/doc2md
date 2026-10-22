"""pytest 夹具：黄金样本可用性门控 + API 测试客户端（含三角色 token）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 未 pip install 也可跑（tests/gguf_fixture）
sys.path.insert(0, str(ROOT / "tests"))

GOLDEN = ROOT / "tests" / "golden"


# --- O-02（QA-D2M-2026-10-21 / C-34 / R-6）：测试环境中立化 --------------------
# 三类环境噪声曾把「产品红灯」稀释成「环境红灯」（本机/沙箱 vs CI 口径不对称）：
# ① 进程级代理变量：回环测试流量被导向外部代理（T2 四项全红，清代理即绿）；
# ② brokered 沙箱拒授「已存在目录的 mkdir」（PermissionError EEXIST）：
#    pytest 9 的 basetemp 引导 `rootdir.mkdir(mode=0o700, exist_ok=True)`
#    撞上即崩，且崩点在 session 夹具内（conftest `cfg` → `mktemp`），
#    症状与产品缺陷无关（ch476 两项 ERROR）；
# ③ 沙箱禁进程树采样（ps 子进程 EPERM）：T6 采样器退化为 none（三项）。
# 处置：① autouse 夹具保存-清除-恢复（确需代理的用例 `keep_proxy` 反向 opt-in）；
#       ② 探测到拒授时把 pytest 临时根重定向到仓库 tmp/（非 brokered；
#          探测为 False 的干净机/CI 不进本分支，语义零变化）；
#       ③ 探测到禁采样时相关用例转 skip 并写明原因，不冒充产品失败。

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
               "ALL_PROXY", "all_proxy")
_SANDBOX_PROBE: dict = {}


def _brokered_mkdir_denied() -> bool:
    """brokered 沙箱签名：对**已存在**目录 `mkdir(exist_ok=True)` 抛 PermissionError。

    干净 POSIX / CI 上该调用静默成功；本机沙箱的 shim 对该操作一律拒授
    （EEXIST，不实现 exist_ok 语义）。探测用系统临时目录下的全新子目录——
    两种环境的首建都允许，只有「已存在 + exist_ok」一步是签名判据。
    模块级只探一次（`_SANDBOX_PROBE` 缓存）。
    """
    if "mkdir" in _SANDBOX_PROBE:
        return _SANDBOX_PROBE["mkdir"]
    denied = False
    probe = Path(tempfile.gettempdir()) / f"doc2md-conftest-probe-{os.getpid()}"
    try:
        probe.mkdir()                       # 全新路径：两类环境都允许
        try:
            probe.mkdir(exist_ok=True)      # 签名判据：已存在 + exist_ok
        except PermissionError:
            denied = True
        except OSError:
            denied = False
        finally:
            try:
                probe.rmdir()
            except OSError:
                pass
    except OSError:
        denied = False       # 全新目录都建不了：非本判据场景，交还 pytest 报错
    _SANDBOX_PROBE["mkdir"] = denied
    return denied


def _rss_sampling_denied() -> bool:
    """沙箱禁进程树采样签名：POSIX ps 后端且 `ps` 子进程被拒（EPERM）。

    /proc（Linux）与 psutil（Windows）路径不产 subprocess，不在此判据内；
    `T6_SAMPLER` 显式覆盖时以覆盖值为准（排障口径，不算环境噪声）。
    """
    if "rss" in _SANDBOX_PROBE:
        return _SANDBOX_PROBE["rss"]
    denied = False
    try:
        sys.path.insert(0, str(ROOT / "ops" / "bench"))
        import t6_large_corpus as bench
        if bench._tree_backend() == "ps":
            import subprocess
            try:
                subprocess.run(["ps", "-axo", "pid=,ppid=,rss="],
                               capture_output=True, timeout=10)
            except PermissionError:
                denied = True      # 沙箱禁进程枚举：T6 驻留列按设计 n/a
            except OSError:
                denied = False
    except Exception:
        denied = False
    _SANDBOX_PROBE["rss"] = denied
    return denied


def pytest_configure(config):
    """O-02②：brokered 沙箱下把 pytest 临时根钉到仓库 `tmp/pytest-basetemp`。

    pytest 的 basetemp 引导 `rootdir.mkdir(mode=0o700, exist_ok=True)`
    （parents=False）在沙箱里对已存在目录被拒（broker 的 mkdir 不实现
    exist_ok 语义，只放行全新路径），且崩点在 session 夹具内
    （conftest `cfg` → `mktemp`），症状与产品无关。`--basetemp` 路径下
    pytest 先 `rm_rf` 再全新 `mkdir`，两类环境都稳定（实测连跑三次全绿）。
    干净机探测为 False 不进本分支；显式传了 `--basetemp` 时尊重调用方。
    """
    if _brokered_mkdir_denied() and not getattr(config.option, "basetemp", None):
        config.option.basetemp = str(ROOT / "tmp" / "pytest-basetemp")


@pytest.fixture(autouse=True)
def _neutral_proxy_env(request):
    """O-02①：回环测试流量不得走进程级代理（保存-清除-恢复）。

    本机/沙箱常设 HTTP_PROXY（企业代理、开发工具），requests/urllib 对
    127.0.0.1 同样生效——T2 的回环断言在代理在位时全红，而 CI 干净所以
    从不暴露。确需走代理的用例用 `@pytest.mark.keep_proxy` 反向 opt-in。
    """
    if "keep_proxy" in getattr(request, "keywords", {}):
        yield
        return
    saved = {k: os.environ[k] for k in _PROXY_VARS if k in os.environ}
    for k in saved:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.update(saved)


@pytest.fixture(scope="session")
def sandbox_denies_mkdir_exist_ok() -> bool:
    """O-02②：沙箱是否拒授「已存在目录 + exist_ok 的 mkdir」（broker 能力缺口）。

    产品代码一律用 `mkdir(parents=True, exist_ok=True)`（递归式、幂等，
    沙箱放行）；测试侧若用 parents=False 的 exist_ok 形式建已存在目录即被拒。
    有此签名的环境里，相应用户应改用 parents=True 形式或显式 skip。
    """
    return _brokered_mkdir_denied()


@pytest.fixture(scope="session")
def sandbox_denies_rss_sampling() -> bool:
    """O-02③：沙箱是否禁进程树采样（T6 驻留列用例的 skip 判据）。"""
    return _rss_sampling_denied()

# CH-471（TEST-1，HIGH）：golden 资产/MANIFEST 缺失时红线门禁必须**硬失败**，
# 不得静默 skip——原 `_need`/`golden_dir` 用 skip，会让「从未生成样本」的机器
# 上 `pytest -m gate` 一片绿（假通过）。仅显式 DOC2MD_ALLOW_MISSING_GOLDEN=1
# 才降级为 skip，供有意的子集运行。
def _missing(msg: str) -> None:
    full = (f"{msg} — run `.venv/bin/python tests/make_golden.py` "
            f"(或设 DOC2MD_ALLOW_MISSING_GOLDEN=1 降级为 skip)")
    if os.environ.get("DOC2MD_ALLOW_MISSING_GOLDEN") == "1":
        pytest.skip(full, allow_module_level=False)
    pytest.fail(full)


def _need(name: str) -> Path:
    p = GOLDEN / name
    if not p.exists():
        _missing(f"golden sample missing: {name}")
    return p


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    if not GOLDEN.is_dir():
        _missing("tests/golden not generated")
    if not (GOLDEN / "MANIFEST.json").is_file():
        _missing("golden MANIFEST.json missing")
    return GOLDEN


@pytest.fixture(scope="session")
def large_pdf(golden_dir: Path) -> Path:
    return _need("large_320p.pdf")


@pytest.fixture(scope="session")
def small_pdf(golden_dir: Path) -> Path:
    return _need("small_3p.pdf")


@pytest.fixture(scope="session")
def cfg(tmp_path_factory):
    """session 级配置。CH-462（A-16）：在隔离 cwd 下 load——loader 的默认文件
    查找（./doc2md.toml）与相对路径解析都以 CWD 为准，原实现会把运行 pytest 的
    工作目录渗入 session 配置（开发者本机恰有 doc2md.toml 时测试结果因机而异）。"""
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("cfg-cwd"))
    try:
        from doc2md import config
        c = config.load()
    finally:
        os.chdir(cwd)
    return c


@pytest.fixture
def tmp_cfg(tmp_path, cfg):
    """把工作根指向 tmp_path，避免测试写进仓库 work/。"""
    import copy
    c = copy.deepcopy(cfg)
    c.setdefault("paths", {})["root"] = str(tmp_path / "work")
    c["paths"]["out"] = str(tmp_path / "work/out")
    c["paths"]["reports"] = str(tmp_path / "work/reports")
    c["paths"]["logs"] = str(tmp_path / "work/logs")
    c["paths"]["tmp"] = str(tmp_path / "work/tmp")
    c.setdefault("ops", {})["backup_local_dir"] = str(tmp_path / "work/reports/backups")
    c["ops"].pop("backup_external_dir", None)
    return c


@pytest.fixture
def store(tmp_path):
    from doc2md.storage.db import Store
    s = Store(tmp_path / "state.sqlite")
    yield s
    s.close()


@pytest.fixture
def api_client(tmp_cfg, tmp_path):
    """(client, tokens) —— 用真实 Flask test client 覆盖红线层的三条 API 门禁（方案 8.1）。"""
    from doc2md import auth
    from doc2md.api import create_app
    from doc2md.storage.db import Store
    app = create_app(tmp_cfg, tmp_path / "work")
    st: Store = app.config["doc2md"]["store"]
    tokens = {r: auth.issue(st, r, f"test-{r}") for r in ("admin", "editor", "reader")}
    with app.test_client() as c:
        yield c, tokens
    app.config["doc2md"]["writer"].close()   # CH-444：服务内置写入器随夹具回收


def hdr(tokens: dict, role: str = "admin") -> dict:
    return {"X-Doc2md-Token": tokens[role]}


@pytest.fixture
def headers():
    return hdr


@pytest.fixture
def saved_doc(api_client, small_pdf):
    """把一个真实小样本入库，返回 doc_id（编辑 / 删除门禁共用）。"""
    client, tokens = api_client
    r = client.post("/jobs", json={"path": str(small_pdf)}, headers=hdr(tokens, "editor"))
    assert r.status_code == 202, (r.status_code, r.get_data(as_text=True)[:300])
    jobs = r.get_json()["jobs"]
    assert jobs and jobs[0].get("doc_id"), jobs
    return client, tokens, jobs[0]["doc_id"]


@pytest.fixture(scope="session")
def names_manifest(golden_dir: Path) -> list[str]:
    p = golden_dir / "names" / "malicious_names.json"
    if not p.is_file():
        _missing("names/malicious_names.json missing")   # CH-471 TEST-1：硬失败非 skip
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("names", [])


@pytest.fixture(scope="session")
def large_single(large_pdf, cfg, tmp_path_factory):
    """大样本转换一次、多处复用（每次 ~30s，门禁与分片单测共享）。"""
    from doc2md.pipeline.convert import convert
    return convert(large_pdf, cfg=cfg, mode="single",
                   out_dir=tmp_path_factory.mktemp("single"))


@pytest.fixture(scope="session")
def large_sharded(large_pdf, cfg, tmp_path_factory):
    from doc2md.pipeline.convert import convert
    return convert(large_pdf, cfg=cfg, mode="two_phase",
                   out_dir=tmp_path_factory.mktemp("sharded"))


@pytest.fixture(scope="session")
def large_zip_names(large_sharded):
    import zipfile
    assert large_sharded.zip_path, "分片产物未打包"
    with zipfile.ZipFile(large_sharded.zip_path) as z:
        return z.namelist()
