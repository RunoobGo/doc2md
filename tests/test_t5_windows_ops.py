"""tests/test_t5_windows_ops.py —— Windows 任务计划资产（CH-456 / T5）。

出口条件（方案 9.2 T5）：与 launchd 同等能力并有校验命令。
本文件在 Linux 沙箱内做**静态等价验证**（真机注册待 T3 跨机基线补实测）：

1. 两个 schtasks XML 与两个 launchd plist 的任务名、触发节奏、CLI 动作逐项对齐；
2. Windows 语义补齐（笔记本电池照跑 / 错过补跑 / 最低权限 / 运行时限）；
3. 口令零泄露（NFR-20）：XML 与安装脚本都不得出现 DOC2MD_BACKUP_PASSPHRASE 字样；
4. 安装脚本契约：占位符全覆盖、注册/校验/卸载三态、-WhatIf 支持；
5. 替换回绕：按安装脚本同规则替换占位符后 XML 仍合法且无残留。
"""
from __future__ import annotations

import plistlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from doc2md import config  # noqa: F401  (only to locate repo root consistently)

pytestmark = [pytest.mark.milestone("DoD")]

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
REPO_ROOT = Path(config.__file__).resolve().parents[2]   # doc2md/config/__init__.py → 仓库根
WIN_DIR = REPO_ROOT / "ops" / "windows"
LAUNCHD_DIR = REPO_ROOT / "ops" / "launchd"
DAILY_XML = WIN_DIR / "md.doc2md.cron.daily.xml"
WEEKLY_XML = WIN_DIR / "md.doc2md.cron.weekly.xml"
INSTALLER = WIN_DIR / "install-doc2md-tasks.ps1"
DAILY_PLIST = LAUNCHD_DIR / "md.doc2md.cron.plist"
WEEKLY_PLIST = LAUNCHD_DIR / "md.doc2md.cron.weekly.plist"

LAUNCHD_WEEKDAY = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
                   4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}


def _task_root(path: Path) -> ET.Element:
    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == f"{NS}Task", f"{path.name}: root tag {root.tag}"
    return root


def _find(el: ET.Element, path: str):
    """ElementTree 路径的 {ns} 前缀只作用于第一段，这里逐段补齐。"""
    cur = el
    for seg in path.split("/"):
        cur = cur.find(f"{NS}{seg}")
        if cur is None:
            return None
    return cur


# --- 1) XML 基本形态 -----------------------------------------------------------
@pytest.mark.parametrize("xml_path", [DAILY_XML, WEEKLY_XML])
def test_xml_well_formed_and_versioned(xml_path):
    root = _task_root(xml_path)
    assert root.get("version") == "1.4"


@pytest.mark.parametrize("plist_path", [DAILY_PLIST, WEEKLY_PLIST])
def test_launchd_plists_are_valid_xml(plist_path):
    """plist 必须是合法 XML（注释里的连续连字符曾让 plutil/expat 拒解析，CH-456 修复）。"""
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"].startswith("md.doc2md.cron")
    args = data["ProgramArguments"]
    # ProgramArguments 前缀必须是 exe + cron + 档位开关（超集语义，§539）
    # 注意日任务文件名是 md.doc2md.cron.plist（无 .daily 后缀），周任务才有 .weekly
    expect = "--weekly" if "weekly" in plist_path.name else "--daily"
    assert args[1:3] == ["cron", expect]
    # CH-475（R6-OPS-1）：launchd 默认 cwd 为 /，root_setting 会把相对根名 work 解析成
    # /work → cron 操作错误根。修复口径（D3）＝WorkingDirectory（安装根）+ 显式根参数
    # （安装根/work）双保险；Windows 孪生靠安装脚本渲染 WorkingDirectory，macOS 是手改
    # 模板无渲染脚本，故这里显式钉住根参数。二者缺一即回归。
    wd = data["WorkingDirectory"]
    assert wd and "--root" in args
    assert args[args.index("--root") + 1] == f"{wd}/work"


def test_task_names_match_launchd_labels():
    """任务名（URI）与 launchd Label 逐一对应——运维面查找口令才一致。"""
    plist_labels = {plistlib.loads(p.read_bytes())["Label"]
                    for p in (DAILY_PLIST, WEEKLY_PLIST)}
    xml_uris = {_find(_task_root(x), "RegistrationInfo/URI").text.lstrip("\\")
                for x in (DAILY_XML, WEEKLY_XML)}
    assert xml_uris == plist_labels == {"md.doc2md.cron", "md.doc2md.cron.weekly"}


def test_daily_cadence_matches_launchd():
    pl = plistlib.loads(DAILY_PLIST.read_bytes())
    interval = pl["StartCalendarInterval"]
    assert (interval["Hour"], interval["Minute"]) == (0, 0)      # launchd：每日 00:00（UTC）
    trig = _find(_task_root(DAILY_XML), "Triggers/CalendarTrigger")
    by_day = _find(trig, "ScheduleByDay")
    assert by_day is not None and _find(by_day, "DaysInterval").text == "1"
    # 安装脚本默认时间与 launchd 口径一致（Windows 侧按本地时间解释）
    assert _installer_default("DailyAt") == "00:00"


def test_weekly_cadence_matches_launchd():
    pl = plistlib.loads(WEEKLY_PLIST.read_bytes())
    interval = pl["StartCalendarInterval"]
    assert (interval["Hour"], interval["Minute"]) == (0, 30)     # launchd：周日 00:30（UTC）
    assert LAUNCHD_WEEKDAY[interval["Weekday"]] == "Sunday"
    trig = _find(_task_root(WEEKLY_XML), "Triggers/CalendarTrigger")
    by_week = _find(trig, "ScheduleByWeek")
    assert by_week is not None
    assert _find(by_week, "WeeksInterval").text == "1"
    assert _find(_find(by_week, "DaysOfWeek"), "Sunday") is not None
    assert _installer_default("WeeklyAt") == "00:30"


def test_actions_match_cli_contract():
    """Exec 动作与 CLI 契约一致：cron --daily / cron --weekly（§539 步骤序列）。"""
    for xml_path, args in ((DAILY_XML, "cron --daily"), (WEEKLY_XML, "cron --weekly")):
        ex = _find(_task_root(xml_path), "Actions/Exec")
        assert _find(ex, "Arguments").text == args
        assert _find(ex, "Command").text == "__DOC2MD_EXE__"
        assert _find(ex, "WorkingDirectory").text == "__DOC2MD_ROOT__"


# --- 2) Windows 语义补齐 -------------------------------------------------------
@pytest.mark.parametrize("xml_path", [DAILY_XML, WEEKLY_XML])
def test_settings_cover_windows_realities(xml_path):
    s = _find(_task_root(xml_path), "Settings")
    # 笔记本电池供电照跑（家用 Win11 是主要机型，§7）。元素名按 Microsoft settingsType
    # 定义拼写（CH-480 核对文档后补回漏掉的 On：原写法 StopIfGoingBatteries 不是定义元素，
    # 真机 Register-ScheduledTask 是否因此被拒未证 → 归 B-3 / B-7）。
    assert _find(s, "DisallowStartIfOnBatteries").text == "false"
    assert _find(s, "StopIfGoingOnBatteries").text == "false"
    # 错过的时间窗开机补跑（对齐 launchd 唤醒补跑）
    assert _find(s, "StartWhenAvailable").text == "true"
    # 不并发重入（cron 各步骤幂等，但没必要叠运行例）
    assert _find(s, "MultipleInstancesPolicy").text == "IgnoreNew"
    # 运行时限有界，杜绝挂起任务无限占驻
    assert _find(s, "ExecutionTimeLimit").text == "PT2H"
    p = _find(_task_root(xml_path), "Principals/Principal")
    assert _find(p, "LogonType").text == "InteractiveToken"     # 当前用户会话，无需管理员
    assert _find(p, "RunLevel").text == "LeastPrivilege"
    assert _find(p, "UserId").text == "__DOC2MD_USER__"


# --- 3) 口令零泄露（NFR-20） -----------------------------------------------------
@pytest.mark.parametrize("path", [DAILY_XML, WEEKLY_XML])
def test_no_secret_name_in_xml(path):
    """XML 里连口令变量名都不允许出现（任务定义是共享资产）。"""
    assert "DOC2MD_BACKUP_PASSPHRASE" not in path.read_text(encoding="utf-8")


def test_installer_has_no_hardcoded_secret():
    """脚本允许出现变量名（设置用途），不允许出现任何字面量口令赋值。"""
    text = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r"(?i)passphrase\s*[=:]\s*['\"][^'\"]{4,}", text), "疑似硬编码口令"
    # 环境变量注入必须是 User 作用域（不是 Machine，也不需要管理员）
    assert 'SetEnvironmentVariable("DOC2MD_BACKUP_PASSPHRASE", $plain, "User")' in text


def test_installer_never_writes_secret_to_disk():
    """口令只进用户级环境变量；不得出现 Out-File/Set-Content 类落盘写法。"""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "Read-Host -AsSecureString" in text
    for banned in ("Out-File", "Set-Content", "Add-Content", "Tee-Object"):
        assert banned not in text, f"安装脚本不得落盘任何内容（{banned}）"


# --- 4) 安装脚本契约 -----------------------------------------------------------
def _installer_default(param: str) -> str:
    m = re.search(rf'\[string\]\${param}\s*=\s*"([^"]+)"', INSTALLER.read_text(encoding="utf-8"))
    assert m, f"installer param default not found: {param}"
    return m.group(1)


def test_installer_contract():
    text = INSTALLER.read_text(encoding="utf-8")
    for needle in ("Register-ScheduledTask", "Unregister-ScheduledTask",
                   "Get-ScheduledTask", "Get-ScheduledTaskInfo",
                   "SupportsShouldProcess", "Uninstall", "VerifyOnly"):
        assert needle in text, f"installer missing: {needle}"


def test_placeholders_all_substituted_by_installer():
    """XML 里的每个 __DOC2MD_*__ 占位符都必须出现在安装脚本中（防止漏换导致静默装错）。"""
    tokens = set()
    for x in (DAILY_XML, WEEKLY_XML):
        tokens |= set(re.findall(r"__DOC2MD_[A-Z_]+__", x.read_text(encoding="utf-8")))
    assert tokens, "no placeholders found"
    ps1 = INSTALLER.read_text(encoding="utf-8")
    for tok in tokens:
        assert tok in ps1, f"installer does not substitute {tok}"


def test_xml_substitution_roundtrip():
    """按安装脚本同规则替换后：XML 可解析、无残留占位符、时间格式合法。"""
    for xml_path, at in ((DAILY_XML, _installer_default("DailyAt")),
                         (WEEKLY_XML, _installer_default("WeeklyAt"))):
        xml = xml_path.read_text(encoding="utf-8")
        xml = (xml.replace("__DOC2MD_USER__", "WINBOX\\alice")
                  .replace("__DOC2MD_EXE__", r"C:\doc2md\env\Scripts\doc2md.exe")
                  .replace("__DOC2MD_ROOT__", r"C:\doc2md")
                  .replace("__DOC2MD_START__", "2099-01-01T" + at + ":00"))
        assert "__DOC2MD_" not in xml
        root = ET.fromstring(xml)
        boundary = _find(root, "Triggers/CalendarTrigger/StartBoundary").text
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", boundary)
        assert boundary.endswith(at + ":00")
        exe = _find(root, "Actions/Exec/Command").text
        assert exe.endswith("doc2md.exe")
