#Requires -Version 5.1
<#
.SYNOPSIS
    Doc2md Windows 任务计划安装 / 校验 / 卸载（CH-456 / T5，与 ops/launchd 同等能力）。

.DESCRIPTION
    默认注册两个定时计划任务（label 与 launchd 完全一致；附录 C-2 / ADR-30 出自已归档蓝本 docs/Doc2md项目开发方案V1.0.md，现行规格见 docs/dev-plan.md 6.5 与附录 E）：
      md.doc2md.cron        每日本地时间 00:00 → doc2md cron --daily
      md.doc2md.cron.weekly 每周日本地时间 00:30 → doc2md cron --weekly

    另两档常驻任务（T12 / CH-480）**默认不装**，需显式加 -WithResident：
      md.doc2md.serve       登录即启动 → doc2md serve（回环 API，失败自恢复）
      md.doc2md.work        登录即启动 → doc2md work（tasks 队列常驻消费，CH-449）
    默认不装的理由：常驻档会开机起一个监听进程与一个转换进程池，属「改变机器长期运行形态」的
    动作，不能跟着定时任务一起被顺手装上；卸载（-Uninstall）则不分口径，四个任务一律清掉，
    并对常驻任务先 Stop-ScheduledTask（等价 `launchctl unload` 的终止语义）。

    任务以**当前用户、最低权限**运行（LogonType=InteractiveToken / LeastPrivilege），
    不需要管理员；笔记本上电池供电也照跑（DisallowStartIfOnBatteries=false）；
    错过的时间窗开机后补跑（StartWhenAvailable=true，对齐 launchd 唤醒补跑语义）。

    口令纪律（NFR-20 / NFR-18）：DOC2MD_BACKUP_PASSPHRASE 绝不写进 XML、脚本或日志。
    -SetPassphrase 走 Read-Host -AsSecureString（输入不回显），值只写入**用户级**环境变量——
    注意该值会以明文持久化在注册表 `HKCU\Environment`（用户态可读，非「不落盘」）。
    更强做法是 Credential Manager / DPAPI 包装脚本，见 ops/backup.md。

.PARAMETER Exe
    doc2md 可执行文件路径。缺省依次尝试：PATH 中的 doc2md、<repo>\.venv|env|venv\Scripts\doc2md.exe。
    .cmd/.bat 形式的 shim 会自动包一层 cmd.exe /c（Exec 动作不能直接跑 .cmd）。

.PARAMETER Root
    部署根目录（work/ 与 doc2md/ 所在目录）。缺省为脚本所在仓库根。

.PARAMETER DailyAt
    日任务触发时间（本机本地时间，HH:mm）。默认 00:00，与 launchd 口径对齐
    （launchd 固定 TZ=UTC；Windows 触发器只认本地时间，要 UTC 口径请把机器时区设为 UTC）。

.PARAMETER WeeklyAt
    周任务触发时间（本机本地时间，HH:mm）。默认 00:30（周日）。

.PARAMETER WithResident
    一并注册常驻两档（md.doc2md.serve / md.doc2md.work，登录触发 + 失败自恢复）。
    缺省不装：常驻会长期占用一个监听进程与一个转换进程池，8GB 目标机上属需显式同意的动作。
    校验（-VerifyOnly）不带本开关时，未安装的常驻任务记 skip（不算失败）而非 fail；
    卸载不受本开关影响，四档一律清理。

.PARAMETER SetPassphrase
    交互式设置用户级 DOC2MD_BACKUP_PASSPHRASE 环境变量（输入不回显；值以**明文**持久化到
    用户级环境变量即注册表 `HKCU\Environment`，故并非「不落盘」，只是不进 XML/脚本/日志。
    需要静态保护请改用 Credential Manager / DPAPI 包装，见 ops/backup.md）。

.PARAMETER Uninstall
    卸载全部计划任务（定时两档 + 常驻两档，等价 launchctl unload + 删 plist）；
    常驻任务先 Stop-ScheduledTask 再注销，避免进程留着继续占端口 / 跑转换。

.PARAMETER VerifyOnly
    只跑校验，不安装 / 不卸载。

.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -SetPassphrase
.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -VerifyOnly       # 校验命令（对齐 launchctl list）
.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -WithResident     # 定时两档 + 常驻两档
.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Exe = "",
    [string]$Root = "",
    [string]$DailyAt = "00:00",
    [string]$WeeklyAt = "00:30",
    [switch]$WithResident,
    [switch]$SetPassphrase,
    [switch]$Uninstall,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

# Kind = calendar → 时间触发，需替换 __DOC2MD_START__；Kind = logon → 登录触发（常驻），无时间占位符。
# 每个表项的键集合必须一致：脚本跑在 Set-StrictMode 2.0 下，缺键的哈希表点访问不可依赖。
$TASKS = @(
    @{ Name = "md.doc2md.cron";        Xml = "md.doc2md.cron.daily.xml";  Kind = "calendar";
       Args = "cron --daily";          At = $DailyAt },
    @{ Name = "md.doc2md.cron.weekly"; Xml = "md.doc2md.cron.weekly.xml"; Kind = "calendar";
       Args = "cron --weekly";         At = $WeeklyAt },
    @{ Name = "md.doc2md.serve";       Xml = "md.doc2md.serve.xml";       Kind = "logon";
       Args = "serve --host 127.0.0.1"; At = "" },
    @{ Name = "md.doc2md.work";        Xml = "md.doc2md.work.xml";        Kind = "logon";
       Args = "work";                   At = "" }
)

function Write-Step([string]$Message) {
    Write-Host "[doc2md-tasks] $Message"
}

function Resolve-Doc2mdRoot([string]$Hint) {
    if ($Hint) {
        $p = (Resolve-Path -LiteralPath $Hint).Path
    } else {
        # 脚本位于 <repo>/ops/windows/，仓库根向上两级
        $p = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    }
    if (-not (Test-Path -LiteralPath (Join-Path $p "doc2md"))) {
        throw "找不到 doc2md 包：$p（用 -Root 指定部署根，即含 doc2md/ 与 work/ 的目录）"
    }
    return $p
}

function Resolve-Doc2mdExe([string]$Hint, [string]$RepoRoot) {
    $candidates = @()
    if ($Hint) { $candidates += $Hint }
    $cmd = Get-Command doc2md -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.CommandType -eq "Application") { $candidates += $cmd.Source }
    $candidates += @(
        (Join-Path $RepoRoot ".venv\Scripts\doc2md.exe"),
        (Join-Path $RepoRoot "env\Scripts\doc2md.exe"),
        (Join-Path $RepoRoot "venv\Scripts\doc2md.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            $full = (Resolve-Path -LiteralPath $c).Path
            if ($full -match '\.(cmd|bat)$') {
                Write-Step "检测到 shim $full → Exec 动作包一层 cmd.exe /c"
                return @{ Command = "$env:SystemRoot\System32\cmd.exe"; Prefix = "/c `"$full`" " }
            }
            return @{ Command = $full; Prefix = "" }
        }
    }
    throw "找不到 doc2md 可执行文件。先 pip install，或用 -Exe 指定路径（尝试过：$($candidates -join '; ')）"
}

function New-TaskXml([string]$XmlPath, [hashtable]$Task, [string]$ExeCommand,
                     [string]$ExePrefix, [string]$RepoRoot, [string]$UserId) {
    if (-not (Test-Path -LiteralPath $XmlPath)) { throw "任务定义缺失：$XmlPath" }
    $xml = Get-Content -LiteralPath $XmlPath -Raw -Encoding UTF8
    $xml = $xml.Replace("__DOC2MD_USER__", $UserId)
    $xml = $xml.Replace("__DOC2MD_EXE__", $ExeCommand)
    $xml = $xml.Replace("__DOC2MD_ROOT__", $RepoRoot)
    if ($Task.Kind -eq "calendar") {
        # StartBoundary 取「明天 + 指定本地时间」：避免安装瞬间被判定为过期窗口而立即补跑
        $time = $Task.At
        if ($time -notmatch '^\d{1,2}:\d{2}$') { throw "时间格式应为 HH:mm，收到：$time" }
        $start = (Get-Date).Date.AddDays(1).ToString("yyyy-MM-dd") + "T" + $time + ":00"
        $xml = $xml.Replace("__DOC2MD_START__", $start)
    } elseif ($xml.Contains("__DOC2MD_START__")) {
        throw "常驻任务（Kind=logon）不该带时间占位符：$XmlPath"
    }
    if ($xml -match '__DOC2MD_[A-Z_]+__') {
        throw "占位符未替换干净：$($Matches[0])（XML：$XmlPath）"
    }
    return $xml
}

function Install-Doc2mdTasks([string]$RepoRoot, [string]$UserId, [bool]$AlsoResident) {
    $exe = Resolve-Doc2mdExe $Exe $RepoRoot
    foreach ($t in $TASKS) {
        if ($t.Kind -ne "calendar" -and -not $AlsoResident) { continue }
        $xmlPath = Join-Path $PSScriptRoot $t.Xml
        $xml = New-TaskXml $xmlPath $t $exe.Command $exe.Prefix $RepoRoot $UserId
        $taskArgs = $exe.Prefix + $t.Args
        if ($PSCmdlet.ShouldProcess($t.Name, "Register-ScheduledTask（$($t.Args)）")) {
            Register-ScheduledTask -TaskName $t.Name -Xml $xml -Force | Out-Null
            if ($t.Kind -eq "calendar") {
                Write-Step "已注册 $($t.Name)：$($exe.Command) $taskArgs（$($t.At) 本地时间）"
            } else {
                Write-Step "已注册常驻 $($t.Name)：$($exe.Command) $taskArgs（登录触发，不限时长）"
            }
        }
    }
}

function Test-Doc2mdTask([hashtable]$Task, [string]$RepoRoot, [bool]$AlsoResident) {
    $name = $Task.Name
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        # 常驻档默认不装：没装不是故障，记 skip；但 -WithResident 口径下缺失就是真没装上。
        if ($Task.Kind -ne "calendar" -and -not $AlsoResident) {
            return @{ Status = "skip"; Why = "未安装（常驻档需 -WithResident 才注册）" }
        }
        return @{ Status = "fail"; Why = "任务不存在" }
    }
    if ($task.State -eq "Disabled") { return @{ Status = "fail"; Why = "任务被禁用" } }
    $act = $task.Actions[0]
    if (-not $act) { return @{ Status = "fail"; Why = "无执行动作" } }
    if ($act.Arguments -notmatch [regex]::Escape($Task.Args)) {
        return @{ Status = "fail"; Why = "动作参数不符：$($act.Arguments)（期望含 $($Task.Args)）" }
    }
    if ($act.WorkingDirectory -ne $RepoRoot) {
        return @{ Status = "fail"; Why = "工作目录不符：$($act.WorkingDirectory)（期望 $RepoRoot）" }
    }
    $trig = $task.Triggers[0]
    if (-not $trig -or -not $trig.Enabled) { return @{ Status = "fail"; Why = "无可用触发器" } }
    $info = Get-ScheduledTaskInfo -TaskName $name
    # 常驻档的正常态就是「正在跑」：此时 LastTaskResult = 267009（0x41301，任务正在运行），
    # 按定时档的「非零即失败」判它会把健康的 serve / work 报成故障。
    if ($task.State -eq "Running") {
        return @{ Status = "ok"; Why = "正在运行（常驻）｜始于 $($info.LastRunTime)" }
    }
    $last = "从未运行"
    if ($info.LastRunTime) {
        $last = "$($info.LastRunTime.ToString('yyyy-MM-dd HH:mm')) 结果=$($info.LastTaskResult)"
        if ($info.LastTaskResult -ne 0) { return @{ Status = "fail"; Why = "上次运行失败：$last" } }
    }
    $next = if ($Task.Kind -ne "calendar") { "下次登录时" } else { "$($info.NextRunTime)" }
    return @{ Status = "ok"; Why = "$next｜$last" }
}

function Test-Doc2mdTasks([string]$RepoRoot, [bool]$AlsoResident) {
    $fail = 0
    $skip = 0
    foreach ($t in $TASKS) {
        $r = Test-Doc2mdTask $t $RepoRoot $AlsoResident
        switch ($r.Status) {
            "ok"   { Write-Host ("[doc2md-tasks] OK   {0,-22} {1}" -f $t.Name, $r.Why) }
            "skip" { Write-Host ("[doc2md-tasks] SKIP {0,-22} {1}" -f $t.Name, $r.Why) -ForegroundColor DarkGray
                     $skip++ }
            default { Write-Host ("[doc2md-tasks] FAIL {0,-22} {1}" -f $t.Name, $r.Why) -ForegroundColor Red
                      $fail++ }
        }
    }
    return @($fail, $skip)
}

function Uninstall-Doc2mdTasks {
    foreach ($t in $TASKS) {
        $existing = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
        if ($existing) {
            if ($PSCmdlet.ShouldProcess($t.Name, "Unregister-ScheduledTask")) {
                if ($t.Kind -ne "calendar") {
                    # 常驻档先终止实例再注销：只注销任务会让已在跑的 serve 继续占端口、
                    # work 继续吃 CPU（launchd 侧的 unload 是「终止 + 卸载」一体语义）。
                    Stop-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
                }
                Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
                Write-Step "已卸载 $($t.Name)"
            }
        } else {
            Write-Step "$($t.Name) 不存在，跳过"
        }
    }
}

# --- 主流程 -------------------------------------------------------------------
$repoRoot = Resolve-Doc2mdRoot $Root
$userId = "$env:USERDOMAIN\$env:USERNAME"
Write-Step "部署根：$repoRoot｜运行身份：$userId（最低权限，无需管理员）"

if ($SetPassphrase) {
    $secure = Read-Host -AsSecureString "DOC2MD_BACKUP_PASSPHRASE（外置副本口令；输入不回显，将以明文存于用户级环境变量 HKCU\Environment）"
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $plain) { throw "口令不能为空" }
    [Environment]::SetEnvironmentVariable("DOC2MD_BACKUP_PASSPHRASE", $plain, "User")
    $plain = $null
    Write-Step "已写入用户级环境变量 DOC2MD_BACKUP_PASSPHRASE（明文持久化于注册表 HKCU\Environment；新开终端/会话后生效）"
}

if ($Uninstall) {
    Uninstall-Doc2mdTasks
    $left = @(Get-ScheduledTask -TaskName "md.doc2md.*" -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) { Write-Step "警告：仍有残留 $($left.Count) 个任务"; exit 1 }
    Write-Step "校验通过：md.doc2md.* 已全部移除"
    exit 0
}

if (-not $VerifyOnly) { Install-Doc2mdTasks $repoRoot $userId ([bool]$WithResident) }

$result = Test-Doc2mdTasks $repoRoot ([bool]$WithResident)
$failures = $result[0]
$skipped = $result[1]
if ($failures -gt 0) {
    Write-Step "校验失败 $failures 项；排障：Get-ScheduledTask -TaskName md.doc2md.* | Format-List *"
    exit 1
}
$note = if ($skipped -gt 0) { "（常驻 $skipped 档未装，需要时加 -WithResident）" } else { "" }
Write-Step "校验通过（等价 launchctl list | grep doc2md）：定时档就绪、错过的时间窗会开机补跑$note"
