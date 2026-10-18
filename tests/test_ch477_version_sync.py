"""CH-477（T15 落地）：版本常量与文档编号的对账红线。

对应方案 9.2 **T15**：CH-472 那轮出现过「登记行声称 `PLAN_VERSION` 升位、常量实际没动」
的三处漂移（header 写 v3.6 / 常量停在 3.5 / §1.4 写「本版 v2.3」），此后每轮收口靠人工
复读对账（CH-473 补的纪律、CH-476 批次⑬ 实测确认）。人工复读是会被漏掉的，本文件把它
变成 CI 拦得住的静态断言：

- `doc2md.PLAN_VERSION` == 方案 header「方案文档版本」== §1.4「本版 vX.Y」（三处口径）；
- README「当前编号至 CH-xxx / 下一条自 CH-yyy」== 方案 10.1「当前至 / 下一条目自」（同一
  编号进度不得有两份真相）；
- `doc2md.PIPELINE_VERSION` == 方案 header 与 §1.4 版本表两处管线行；
- `doc2md.SCHEMA_VERSION` / `doc2md.storage.db.DB_VERSION` == §1.4 版本表对应行。

断言一律先确认**锚点解析成功**（`match is not None`）再比对值：文档被改写到锚点失效时
必须红，否则「解析不到就跳过」会让整条红线静默恒真——那是比漂移更糟的形态。
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import doc2md
from doc2md import config
from doc2md.storage.db import DB_VERSION

REPO_ROOT = Path(config.__file__).resolve().parents[2]   # doc2md/config/__init__.py → 仓库根
DEV_PLAN = REPO_ROOT / "docs" / "dev-plan.md"
README = REPO_ROOT / "README.md"


def _dev_plan_text() -> str:
    return DEV_PLAN.read_text(encoding="utf-8")


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """切出某个三级标题下、到下一个三级/二级标题之前的正文。"""
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^###? ", rest, re.M)
    return rest[: m.start()] if m else rest


@pytest.mark.gate
def test_plan_version_three_way_reconciliation():
    """`PLAN_VERSION` 与方案 header、§1.4 三处必须实测相等（CH-472 型漂移的根治）。"""
    text = _dev_plan_text()

    header = re.search(r"^\| 方案文档版本 \| \*\*v(\d+\.\d+)\*\*（", text, re.M)
    assert header is not None, "方案 header 的「方案文档版本」行格式漂移，红线锚点失效"

    body = re.search(
        r"^- \*\*方案文档版本\*\*独立编号（本版 v(\d+\.\d+)）", text, re.M)
    assert body is not None, "方案 §1.4 的「本版 vX.Y」句式漂移，红线锚点失效"

    assert doc2md.PLAN_VERSION == header.group(1) == body.group(1), (
        f"方案文档版本三处不一致：常量 {doc2md.PLAN_VERSION} / "
        f"header v{header.group(1)} / §1.4 v{body.group(1)}")


@pytest.mark.gate
def test_readme_and_registry_ch_numbering_agree():
    """README 与方案 10.1 的「当前编号 / 下一条编号」必须是同一个进度。"""
    readme = re.search(
        r"当前编号至 `CH-(\d+)`，下一条自 `CH-(\d+)`", _readme_text())
    assert readme is not None, "README 跟踪约定句式漂移，红线锚点失效"

    registry = _section(_dev_plan_text(), "### 10.1 登记流")
    current = re.search(r"当前至 `CH-(\d+)`。下一条目自 `CH-(\d+)` 起", registry)
    assert current is not None, "方案 10.1 编号行句式漂移，红线锚点失效"

    assert readme.group(1) == current.group(1), (
        f"已用编号分裂：README 称当前至 CH-{readme.group(1)}，"
        f"方案 10.1 称当前至 CH-{current.group(1)}")
    assert readme.group(2) == current.group(2), (
        f"下一条编号分裂：README 称 CH-{readme.group(2)}，方案 10.1 称 CH-{current.group(2)}")


@pytest.mark.gate
def test_pipeline_version_doc_rows_match_constant():
    """管线版本在 header 与 §1.4 版本表各有一处，两处都要等于常量。"""
    text = _dev_plan_text()

    header = re.search(r"^\| 管线版本 \| \*\*v(\d+\.\d+\.\d+)\*\*（", text, re.M)
    assert header is not None, "方案 header 的「管线版本」行格式漂移，红线锚点失效"

    row = re.search(r"^\s*\| 管线版本 \| `PIPELINE_VERSION` \| (\d+\.\d+\.\d+) \|", text, re.M)
    assert row is not None, "方案 §1.4 版本表的管线行格式漂移，红线锚点失效"

    assert doc2md.PIPELINE_VERSION == header.group(1) == row.group(1), (
        f"管线版本不一致：常量 {doc2md.PIPELINE_VERSION} / header v{header.group(1)} / "
        f"版本表 {row.group(1)}")


@pytest.mark.gate
def test_schema_and_db_version_rows_match_constants():
    """IR schema 与状态库结构版本各有一处文档口径，须分别等于对应常量。

    两个版本是两套口径（方案 D-5），曾出现迁移判定读错对象的缺陷，故一并设防。
    """
    text = _dev_plan_text()

    schema = re.search(r"^\s*\| IR schema 版本 \| `SCHEMA_VERSION` \| (\d+) \|", text, re.M)
    assert schema is not None, "方案 §1.4 版本表的 IR schema 行格式漂移，红线锚点失效"

    db = re.search(r"^\s*\| 状态库结构版本 \| `DB_VERSION`（[^|]*\| (\d+) \|", text, re.M)
    assert db is not None, "方案 §1.4 版本表的状态库结构行格式漂移，红线锚点失效"

    assert str(doc2md.SCHEMA_VERSION) == schema.group(1), (
        f"IR schema 版本不一致：常量 {doc2md.SCHEMA_VERSION} / 文档 {schema.group(1)}")
    assert str(DB_VERSION) == db.group(1), (
        f"状态库结构版本不一致：常量 {DB_VERSION} / 文档 {db.group(1)}")


@pytest.mark.gate
def test_pyproject_version_matches_pipeline_constant():
    """CH-485（R9-DOC-2）：包元数据必须与 PIPELINE_VERSION 同源。"""
    py = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert py["project"]["version"] == doc2md.PIPELINE_VERSION, (
        f"pyproject {py['project']['version']} != PIPELINE {doc2md.PIPELINE_VERSION}")
