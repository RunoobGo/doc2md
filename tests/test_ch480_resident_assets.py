"""CH-480：`serve` / `work` 常驻资产（方案 9.2 T12 的沙箱可验证半）。

对应登记：9.4 B-1（README 曾枚举不存在的 serve / llm 标签）与 12.7 B-9（实机「开机自启 +
崩溃自恢复」待真机）。本文件只管**资产存在且两平台同构**这一半：

- 四档 Label / URI / 安装脚本任务名三处口径必须相等且闭集（改名、漏装、多装都拦）；
- `serve` 在 plist 与 schtasks XML 两侧都钉死回环（NFR-1 / 决策 D4），端口留给配置免两处漂移；
- 常驻语义逐项对齐：launchd 的 RunAtLoad + KeepAlive（仅非零退出重启）+ ThrottleInterval
  ↔ Windows 的 LogonTrigger + RestartOnFailure + IgnoreNew + ExecutionTimeLimit PT0S；
- 平台语义差（launchd 无限重启 / Windows 最多 10 次）如实写进资产注释，不做「等价」空话；
- XML 元素名必须是 Microsoft 模式里的**定义名**（白名单，非枚举拼接）——本轮正是靠这条
  抓到 T5 既有资产把 `StopIfGoingOnBatteries` 写成 `StopIfGoingBatteries` 的缺陷；
- 口令零泄露（NFR-20）与 README 枚举与实存资产一致（B-1 的反向守卫）。

与 `tests/test_ch477_version_sync.py` / `tests/test_ch479_ci_matrix.py` 同一条纪律：先确认
锚点解析成功再比值，锚点失效即红，不让文档改写变成静默空转。Windows 侧只做**文本 + DOM**
校验：本机无 pwsh，`Register-ScheduledTask` 的真机结果与 `.ps1` 的可解析性都归 B-3 / B-7，
不在这里声称已验证。解析用的 ElementTree 只吃本仓库自己受版本控制的资产文件（与 T5 同口径），
不接触外部不可信 XML，故不引入 DTD / 实体防护依赖。
"""
from __future__ import annotations

import plistlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from doc2md import config

REPO_ROOT = Path(config.__file__).resolve().parents[2]   # doc2md/config/__init__.py → 仓库根
WIN_DIR = REPO_ROOT / "ops" / "windows"
LAUNCHD_DIR = REPO_ROOT / "ops" / "launchd"
INSTALLER = WIN_DIR / "install-doc2md-tasks.ps1"

RESIDENT_PLISTS = {"md.doc2md.serve": LAUNCHD_DIR / "md.doc2md.serve.plist",
                   "md.doc2md.work": LAUNCHD_DIR / "md.doc2md.work.plist"}
CRON_PLISTS = [LAUNCHD_DIR / "md.doc2md.cron.plist",
               LAUNCHD_DIR / "md.doc2md.cron.weekly.plist"]
RESIDENT_XMLS = {"md.doc2md.serve": WIN_DIR / "md.doc2md.serve.xml",
                 "md.doc2md.work": WIN_DIR / "md.doc2md.work.xml"}
CRON_XMLS = [WIN_DIR / "md.doc2md.cron.daily.xml", WIN_DIR / "md.doc2md.cron.weekly.xml"]

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
ALL_LABELS = {"md.doc2md.cron", "md.doc2md.cron.weekly", "md.doc2md.serve", "md.doc2md.work"}

# Microsoft《settingsType Complex Type》定义的子元素名；该复合类型是 xsd:all，顺序不敏感。
SETTINGS_ELEMENTS = {
    "AllowStartOnDemand", "RestartOnFailure", "MultipleInstancesPolicy",
    "DisallowStartIfOnBatteries", "StopIfGoingOnBatteries", "AllowHardTerminate",
    "StartWhenAvailable", "NetworkProfileName", "RunOnlyIfNetworkAvailable", "WakeToRun",
    "Enabled", "Hidden", "DeleteExpiredTaskAfter", "IdleSettings", "NetworkSettings",
    "ExecutionTimeLimit", "Priority", "RunOnlyIfIdle", "UseUnifiedSchedulingEngine",
    "DisallowStartOnRemoteAppSession",
}
RESTART_ELEMENTS = {"Interval", "Count"}
# 触发器侧：triggerBaseType 五个 + 各自类型附加（LogonTrigger 多 UserId/Delay）。
TRIGGER_BASE_ELEMENTS = {"StartBoundary", "EndBoundary", "Enabled", "Repetition",
                         "ExecutionTimeLimit"}
LOGON_TRIGGER_ELEMENTS = TRIGGER_BASE_ELEMENTS | {"UserId", "Delay"}
CALENDAR_TRIGGER_ELEMENTS = TRIGGER_BASE_ELEMENTS | {
    "RandomDelay", "ScheduleByDay", "ScheduleByWeek", "ScheduleByMonth",
    "ScheduleByMonthDayOfWeek"}


def _find(el: ET.Element, path: str):
    """ElementTree 的 {ns} 前缀只作用于第一段，这里逐段补齐（与 T5 测试同规则）。"""
    cur = el
    for seg in path.split("/"):
        cur = cur.find(f"{NS}{seg}")
        if cur is None:
            return None
    return cur


def _root(path: Path) -> ET.Element:
    assert path.exists(), f"资产缺失：{path.relative_to(REPO_ROOT)}"
    root = ET.parse(path).getroot()
    assert root.tag == f"{NS}Task", f"{path.name}: root tag {root.tag}"
    return root


def _plist(path: Path) -> dict:
    assert path.exists(), f"资产缺失：{path.relative_to(REPO_ROOT)}"
    data = plistlib.loads(path.read_bytes())
    assert data.get("ProgramArguments"), f"{path.name}: 解析不出 ProgramArguments，锚点已失效"
    return data


def _uri(xml_path: Path) -> str:
    node = _find(_root(xml_path), "RegistrationInfo/URI")
    assert node is not None and node.text, f"{xml_path.name}: 缺 RegistrationInfo/URI"
    return node.text.lstrip("\\")


# ---------------------------------------------------------------- 红线：口径闭集

@pytest.mark.gate
@pytest.mark.parametrize("label", sorted(RESIDENT_PLISTS))
def test_resident_plist_is_self_healing_and_root_pinned(label):
    """常驻 plist：登录自启 + 仅非零退出重启 + 防风暴节流，根口径与 cron 档双保险一致。"""
    data = _plist(RESIDENT_PLISTS[label])
    assert data["Label"] == label, f"Label 漂移：{data['Label']}"
    assert data["RunAtLoad"] is True, f"{label}: 缺 RunAtLoad，开机/登录后不会自启"
    # KeepAlive 取 dict 且 SuccessfulExit=false → 崩溃（非零退出）重启，正常退出与 unload 不复活。
    # 写成 <true/> 会让 `launchctl unload` 语义变成「杀不掉」，写反则崩溃后不自恢复。
    assert data["KeepAlive"] == {"SuccessfulExit": False}, (
        f"{label}: KeepAlive 不是「仅非零退出重启」口径：{data.get('KeepAlive')!r}")
    assert int(data.get("ThrottleInterval", 0)) >= 10, (
        f"{label}: 缺 ThrottleInterval 节流，崩溃风暴会空转")
    args = data["ProgramArguments"]
    assert args[1] == label.split(".", 2)[-1], f"{label}: 动作错位 {args[1:3]}"
    # 与 cron 档同一条 CH-475 / R6-OPS-1 纪律：launchd 默认 cwd 为 /，
    # WorkingDirectory（安装根）+ 显式根参数（安装根/work）二者缺一即回归。
    wd = data["WorkingDirectory"]
    assert wd, f"{label}: 缺 WorkingDirectory"
    assert "--root" in args, f"{label}: 缺显式根参数"
    assert args[args.index("--root") + 1] == f"{wd}/work", f"{label}: 根参数与 cwd 不匹配"


@pytest.mark.gate
def test_label_uri_and_installer_task_names_are_one_closed_set():
    """四档口径三处相等：plist Label == XML URI == 安装脚本任务名 == 登记闭集。"""
    labels = {_plist(p)["Label"] for p in (*CRON_PLISTS, *RESIDENT_PLISTS.values())}
    uris = {_uri(p) for p in (*CRON_XMLS, *RESIDENT_XMLS.values())}
    names = set(re.findall(r'Name = "(md\.doc2md\.[\w.]+)"', INSTALLER.read_text("utf-8")))
    assert labels == uris == names == ALL_LABELS, (
        f"任务名口径分叉：plist {sorted(labels)} / XML URI {sorted(uris)}"
        f" / installer {sorted(names)} / 登记 {sorted(ALL_LABELS)}")


@pytest.mark.gate
def test_serve_stays_on_loopback_on_both_platforms():
    """serve 两侧都显式钉回环（NFR-1 离线单机姿态 / 决策 D4），端口只由配置决定。"""
    plist_args = _plist(RESIDENT_PLISTS["md.doc2md.serve"])["ProgramArguments"]
    assert plist_args[plist_args.index("--host") + 1] == "127.0.0.1", (
        f"plist 的 host 参数不再是回环：{plist_args[1:]}")
    act = _find(_root(RESIDENT_XMLS["md.doc2md.serve"]), "Actions/Exec")
    assert _find(act, "Arguments").text == "serve --host 127.0.0.1", (
        f"schtasks 动作漂移：{_find(act, 'Arguments').text}")
    for label, path in RESIDENT_PLISTS.items():
        args = _plist(path)["ProgramArguments"]
        assert "--port" not in args, f"{label}: 端口写进了资产，会与 api.port 两处漂移"
    for label, path in RESIDENT_XMLS.items():
        act = _find(_root(path), "Actions/Exec")
        assert "--port" not in _find(act, "Arguments").text, f"{label}: 端口写进了资产"


@pytest.mark.gate
@pytest.mark.parametrize("label", sorted(RESIDENT_XMLS))
def test_resident_windows_tasks_are_unbounded_but_self_restarting(label):
    """常驻 ≠ 定时：不限时长（PT0S）+ 失败自恢复 + 重复触发让位，权限口径与 cron 档一致。"""
    s = _find(_root(RESIDENT_XMLS[label]), "Settings")
    assert s is not None, f"{label}: 无 Settings 段，锚点失效"
    # 文档明确 PT0S = 不限时长；写 PT2H 会让 serve / work 每天被砍一次（cron 档才该有界）。
    assert _find(s, "ExecutionTimeLimit").text == "PT0S", f"{label}: 常驻任务被加了时长上限"
    assert _find(s, "MultipleInstancesPolicy").text == "IgnoreNew", (
        f"{label}: 重复触发会叠实例，第二个 serve 只会因端口占用失败重启")
    restart = _find(s, "RestartOnFailure")
    assert restart is not None, f"{label}: 缺 RestartOnFailure，崩溃后不自恢复"
    assert {c.tag.replace(NS, "") for c in restart} == RESTART_ELEMENTS, (
        f"{label}: RestartOnFailure 必须同时给 Interval 与 Count（文档要求二者皆备）")
    assert _find(restart, "Interval").text == "PT1M"
    assert 1 <= int(_find(restart, "Count").text) <= 999, f"{label}: RestartOnFailure 次数越界"
    p = _find(_root(RESIDENT_XMLS[label]), "Principals/Principal")
    assert _find(p, "LogonType").text == "InteractiveToken"
    assert _find(p, "RunLevel").text == "LeastPrivilege"
    assert _find(p, "UserId").text == "__DOC2MD_USER__"
    trig = _find(_root(RESIDENT_XMLS[label]), "Triggers/LogonTrigger")
    assert trig is not None and _find(trig, "Enabled").text == "true", (
        f"{label}: 缺登录触发器，与 launchd LaunchAgents 的登录加载不对应")
    assert _find(trig, "StartBoundary") is None, (
        f"{label}: 登录触发不该带时间占位符（安装脚本只对 calendar 档替换）")


@pytest.mark.gate
def test_windows_asset_element_names_are_all_schema_defined():
    """XML 元素名必须是 Microsoft 模式的定义名——拼错的名字不在模式里，真机可能被拒注册。"""
    for path in (*CRON_XMLS, *RESIDENT_XMLS.values()):
        root = _root(path)
        settings = _find(root, "Settings")
        assert settings is not None, f"{path.name}: 无 Settings 段"
        unknown = {c.tag.replace(NS, "") for c in settings} - SETTINGS_ELEMENTS
        assert not unknown, f"{path.name}: Settings 含未定义元素 {sorted(unknown)}"
        for kind, allowed in (("LogonTrigger", LOGON_TRIGGER_ELEMENTS),
                              ("CalendarTrigger", CALENDAR_TRIGGER_ELEMENTS)):
            node = _find(_find(root, "Triggers"), kind)
            if node is None:
                continue
            bad = {c.tag.replace(NS, "") for c in node} - allowed
            assert not bad, f"{path.name}: {kind} 含未定义元素 {sorted(bad)}"
        restart = _find(settings, "RestartOnFailure")
        if restart is not None:
            bad = {c.tag.replace(NS, "") for c in restart} - RESTART_ELEMENTS
            assert not bad, f"{path.name}: RestartOnFailure 含未定义元素 {sorted(bad)}"


# ---------------------------------------------------------------- 判据本体（信息层）

@pytest.mark.parametrize("label", sorted(RESIDENT_PLISTS))
def test_resident_plists_share_the_cron_clock_and_log_layout(label):
    """时钟口径与日志落点与 cron 档一致；两份捕获文件按档名分开且都在 logs/ 下。"""
    data = _plist(RESIDENT_PLISTS[label])
    cron = _plist(CRON_PLISTS[0])
    assert data["EnvironmentVariables"] == cron["EnvironmentVariables"], (
        f"{label}: TZ 口径与 cron 档分叉")
    exe = cron["ProgramArguments"][0]
    assert data["ProgramArguments"][0] == exe, f"{label}: 解释器路径口径与 cron 档分叉"
    tail = label.rsplit(".", 1)[-1]
    root_work = f"{data['WorkingDirectory']}/work"
    for key in ("StandardOutPath", "StandardErrorPath"):
        p = data[key]
        assert p.startswith(f"{root_work}/logs/launchd-{tail}."), (
            f"{label}: {key} 不在本档专属的 logs/launchd-<档名>.* 命名里：{p}")
    assert data["StandardOutPath"] != data["StandardErrorPath"]


@pytest.mark.parametrize("asset", sorted(RESIDENT_PLISTS) + sorted(RESIDENT_XMLS))
def test_resident_assets_carry_no_secret_names(asset):
    """任务定义是共享资产：连口令变量名都不许出现（NFR-20，与 T5 同一条纪律）。"""
    path = RESIDENT_PLISTS.get(asset) or RESIDENT_XMLS[asset]
    assert "DOC2MD_BACKUP_PASSPHRASE" not in path.read_text(encoding="utf-8"), asset


def test_installer_treats_resident_tasks_as_opt_in():
    """常驻档必须显式开关才装、校验认「正在运行」、卸载先终止——三条都钉住。"""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "[switch]$WithResident" in text, "常驻档成了默认安装项：装定时任务会顺手起常驻进程"
    assert re.search(r'Kind\s*=|"logon"', text), "安装脚本没有区分常驻与定时"
    assert "Stop-ScheduledTask" in text, "卸载常驻档未先终止实例（进程会继续占端口 / 吃 CPU）"
    assert '"md.doc2md.*"' in text, "卸载残留校验仍只扫 cron 前缀，会漏掉常驻档"
    assert 'State -eq "Running"' in text, (
        "校验不认「正在运行」：健康的常驻任务会被 LastTaskResult=267009 报成故障")


def test_installer_keeps_the_cron_placeholders_and_defaults():
    """加常驻档不得动定时档的既有口径：默认时刻、时间占位符与替换清单一致。"""
    text = INSTALLER.read_text(encoding="utf-8")
    for name, default in (("DailyAt", "00:00"), ("WeeklyAt", "00:30")):
        m = re.search(rf'\[string\]\${name}\s*=\s*"([^"]+)"', text)
        assert m and m.group(1) == default, f"{name} 默认值漂移：{m and m.group(1)}"
    for path in CRON_XMLS:
        assert "__DOC2MD_START__" in path.read_text(encoding="utf-8"), path.name
    for path in RESIDENT_XMLS.values():
        assert "__DOC2MD_START__" not in path.read_text(encoding="utf-8"), path.name
    tokens = set()
    for path in (*CRON_XMLS, *RESIDENT_XMLS.values()):
        tokens |= set(re.findall(r"__DOC2MD_[A-Z_]+__", path.read_text(encoding="utf-8")))
    for tok in tokens:
        assert tok in text, f"安装脚本不替换 {tok}"


def test_readme_enumerates_exactly_the_existing_labels():
    """README 只能枚举实存资产（9.4 B-1 的反向守卫：曾枚举过不存在的 serve / llm 标签）。

    取**集合相等**而不是逐个 `in`：子串式存在断言对「改名后仍带旧前缀」的写法天然失明
    （`md.doc2md.work` → `md.doc2md.workX` 里旧名仍是子串，实测这样变异后仍判绿），
    而 B-1 的病灶正是「名字对不上实存资产」，必须连多出来的名字一起拦。
    """
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    found = set()
    for m in re.finditer(r"md\.doc2md(?:\.[A-Za-z0-9_-]+)+", text):
        tok = m.group(0)
        for suffix in (".plist", ".xml"):     # 安装命令里写的是文件名，不是标签名
            if tok.endswith(suffix):
                tok = tok[: -len(suffix)]
        found.add(tok)
    assert found, "README 里一个 `md.doc2md.*` 标签都解析不到，锚点已失效"
    assert found == ALL_LABELS, (
        f"README 枚举与实存资产不一致：多出 {sorted(found - ALL_LABELS)}"
        f" / 缺项 {sorted(ALL_LABELS - found)}")
    assert "尚无标签" not in text, "README 仍写着常驻档「尚无标签」，与 ops/launchd/ 现状不符"
