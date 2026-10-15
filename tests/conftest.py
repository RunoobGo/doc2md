"""pytest 夹具：黄金样本可用性门控 + API 测试客户端（含三角色 token）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 未 pip install 也可跑（tests/gguf_fixture）
sys.path.insert(0, str(ROOT / "tests"))

GOLDEN = ROOT / "tests" / "golden"


def _need(name: str) -> Path:
    p = GOLDEN / name
    if not p.exists():
        pytest.skip(f"golden sample missing: {name}", allow_module_level=False)
    return p


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    if not GOLDEN.is_dir():
        pytest.skip("tests/golden not generated (run tests/make_golden.py)")
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
    assert r.status_code == 202, r.get_json()
    jobs = r.get_json()["jobs"]
    assert jobs and jobs[0].get("doc_id"), jobs
    return client, tokens, jobs[0]["doc_id"]


@pytest.fixture(scope="session")
def names_manifest(golden_dir: Path) -> list[str]:
    p = golden_dir / "names" / "malicious_names.json"
    if not p.is_file():
        pytest.skip("names/malicious_names.json missing")
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
