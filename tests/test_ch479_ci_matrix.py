"""CH-479：两套 CI 工作流的版本矩阵口径不得各说各话。

对应方案 12.7「Python 3.11 / 3.12 / 3.13 矩阵」行的 **②**（全量套件要不要扩到版本矩阵）。
裁定是「推送腿单口径 + 周度腿三档」，代价是**版本清单从一处变成两处**——一旦有人只改
其中一个文件，跨版本覆盖会静默变形：推送三档绿、周度两档（或反之），而两边都显示成功。
本文件把它钉成静态断言：

- 两份工作流的矩阵版本集合必须相等，且等于 `{"3.11", "3.12", "3.13"}`；
- `ci.yml` 的 `full-suite` 必须仍是钉死单档（无 matrix），否则推送反馈时长被三倍 runner
  占用拖长，裁定即被推翻——要推翻就先改登记，不许由一次「顺手加矩阵」代劳；
- 两边每个作业都带 `timeout-minutes`（缺它时一次挂死占满 GitHub 的 6 小时默认上限，
  红线在几小时内既不是绿也不是红，CH-471 的教训），全量腿统一 30 分钟；
- `full-matrix` 只允许 `schedule` + `workflow_dispatch` 两种触发；挂上 `push` /
  `pull_request` 就等于偷偷加了第二套推送腿。

与 `tests/test_ch477_version_sync.py` 同一条纪律：先确认**锚点解析成功**再比值。YAML 解析器
不在依赖里（`pyyaml` 未安装），故按「作业名两格缩进」切块做文本级解析——CI 配置的形状由
本仓库自己写死，文本锚点足够，且改坏缩进时断言会先红。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from doc2md import config

REPO_ROOT = Path(config.__file__).resolve().parents[2]   # doc2md/config/__init__.py → 仓库根
CI_PUSH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_MATRIX = REPO_ROOT / ".github" / "workflows" / "ci-full-matrix.yml"

SUPPORTED = {"3.11", "3.12", "3.13"}
MATRIX_LIST = re.compile(r'python-version:\s*\[([^\]]*)\]')
PINNED = re.compile(r'python-version:\s*"([\d.]+)"')


def _text(path: Path) -> str:
    assert path.exists(), f"CI 工作流缺失：{path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _jobs(text: str) -> dict[str, str]:
    """按「两格缩进的作业名」把 `jobs:` 段切成 {作业名: 作业体}。"""
    body = text.split("jobs:", 1)[1]
    parts = re.split(r"^  ([A-Za-z][\w-]*):\ *$", body, flags=re.M)
    jobs = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    assert jobs, "jobs: 段解析不出任何作业，本文件的锚点已失效"
    return jobs


def _matrix_versions(job_body: str) -> set[str]:
    m = MATRIX_LIST.search(job_body)
    assert m is not None, f"作业里没有 matrix 的 python-version 列表：\n{job_body[:200]}"
    return set(re.findall(r'"([\d.]+)"', m.group(1)))


@pytest.mark.gate
def test_both_workflows_agree_on_the_python_matrix():
    """两套工作流的跨版本集合必须相等，且等于方案登记的支持矩阵。"""
    push = _matrix_versions(_jobs(_text(CI_PUSH))["merge-gate"])
    weekly = _matrix_versions(_jobs(_text(CI_MATRIX))["full-suite"])
    assert push == weekly == SUPPORTED, (
        f"版本矩阵分叉：ci.yml merge-gate {sorted(push)} / ci-full-matrix full-suite {sorted(weekly)}"
        f" / 登记口径 {sorted(SUPPORTED)}")


@pytest.mark.gate
def test_push_leg_keeps_full_suite_single_interpreter():
    """全量套件在推送路径上仍是 3.12 单档——跨版本覆盖由周度腿承担（方案 12.7 的 ②）。"""
    body = _jobs(_text(CI_PUSH))["full-suite"]
    assert "strategy" not in body and MATRIX_LIST.search(body) is None, (
        "ci.yml 的 full-suite 被加上了矩阵：推送反馈时长与 runner 占用翻三倍，"
        "与 12.7 的 ② 裁定相反。要改先改登记，再同步本断言。")
    pinned = PINNED.search(body)
    assert pinned is not None, "ci.yml 的 full-suite 找不到钉死的解释器版本"
    assert pinned.group(1) == "3.12", f"推送腿的全量解释器漂移为 {pinned.group(1)}"


@pytest.mark.gate
def test_every_job_has_a_timeout_minutes():
    """每个作业都必须有 `timeout-minutes`（CH-471：缺它等于把红线交给 6 小时默认上限）。"""
    for path in (CI_PUSH, CI_MATRIX):
        for name, body in _jobs(_text(path)).items():
            m = re.search(r"^\s*timeout-minutes:\s*(\d+)", body, re.M)
            assert m is not None, f"{path.name} 的作业 {name} 没有 timeout-minutes"
            if name.startswith("full-suite"):
                assert int(m.group(1)) == 30, (
                    f"{path.name} 的 {name} 超时应为 30 分钟，实测 {m.group(1)}")


@pytest.mark.gate
def test_weekly_workflow_triggers_only_on_schedule_or_dispatch():
    """周度腿不得挂 push / pull_request——那会让它变成第二套推送腿。"""
    head = _text(CI_MATRIX).split("jobs:", 1)[0]
    block = re.search(r"^on:\n((?:\ \ +[^\n]*\n)+)", head, re.M)
    assert block is not None, "ci-full-matrix.yml 的 `on:` 段形状漂移，锚点失效"
    triggers = set(re.findall(r"^  ([a-z_]+):", block.group(1), re.M))
    assert triggers <= {"schedule", "workflow_dispatch"}, f"周度腿出现了推送型触发：{sorted(triggers)}"
    assert "schedule" in triggers and re.search(r"cron:", block.group(1)), (
        "周度腿缺 schedule/cron，等于没有跨版本信息面覆盖")


# ---------------------------------------------------------------- 判据本体（信息层）

def test_suite_steps_keep_the_double_verbose_convention():
    """全量步骤用 `-vv`（`pytest.ini` 已含 `-q`，净 verbosity 0）——挂死时能点名嫌疑人。"""
    for path in (CI_PUSH, CI_MATRIX):
        text = _text(path)
        suite = re.findall(r"run: python -m pytest([^\n]*)", text)
        assert suite, f"{path.name} 找不到 pytest 步骤"
        assert all("-vv" in line or "-q" in line for line in suite), (
            f"{path.name} 有全量步骤丢了 -vv：{suite}")


def test_weekly_workflow_still_carries_the_reproducibility_and_doctor_steps():
    """周度腿与推送腿同样跑黄金逐字节复现与 doctor 自检，否则跨版本资产形状无人看。"""
    body = _jobs(_text(CI_MATRIX))["full-suite"]
    assert "Golden assets byte-reproducible" in body, "周度腿丢了黄金样本逐字节复现步骤"
    assert "doctor self-check" in body and "doc2md doctor" in body, "周度腿丢了 doctor 自检步骤"
