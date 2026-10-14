#Requires -Version 5.1
<#
.SYNOPSIS
    Doc2md Windows 任务计划安装 / 校验 / 卸载（CH-456 / T5，与 ops/launchd 同等能力）。

.DESCRIPTION
    注册两个计划任务（label 与 launchd 完全一致，方案 §539 / 附录 C-2 / ADR-30）：
      md.doc2md.cron        每日本地时间 00:00 → doc2md cron --daily
      md.doc2md.cron.weekly 每周日本地时间 00:30 → doc2md cron --weekly

    任务以**当前用户、最低权限**运行（LogonType=InteractiveToken / LeastPrivilege），
    不需要管理员；笔记本上电池供电也照跑（DisallowStartIfOnBatteries=false）；
    错过的时间窗开机后补跑（StartWhenAvailable=true，对齐 launchd 唤醒补跑语义）。

    口令纪律（NFR-20 / NFR-18）：DOC2MD_BACKUP_PASSPHRASE 绝不写进 XML、脚本或日志。
    -SetPassphrase 走 Read-Host -AsSecureString，只写入**用户级**环境变量；
    更强做法是 Credential Manager 包装脚本，见 ops/backup.md。

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

.PARAMETER SetPassphrase
    交互式设置用户级 DOC2MD_BACKUP_PASSPHRASE 环境变量（不回显、不落盘）。

.PARAMETER Uninstall
    卸载两个计划任务（等价 launchctl unload + 删 plist）。

.PARAMETER VerifyOnly
    只跑校验，不安装 / 不卸载。

.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -SetPassphrase
.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -VerifyOnly      # 校验命令（对齐 launchctl list）
.EXAMPLE
    pwsh ops/windows/install-doc2md-tasks.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Exe = "",
    [string]$Root = "",
    [string]$DailyAt = "00:00",
    [string]$WeeklyAt = "00:30",
    [switch]$SetPassphrase,
    [switch]$Uninstall,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$TASKS = @(
    @{ Name = "md.doc2md.cron";        Xml = "md.doc2md.cron.daily.xml";  Args = "cron --daily";  At = $DailyAt },
    @{ Name = "md.doc2md.cron.weekly"; Xml = "md.doc2md.cron.weekly.xml"; Args = "cron --weekly"; At = $WeeklyAt }
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
    # StartBoundary 取「明天 + 指定本地时间」：避免安装瞬间被判定为过期窗口而立即补跑
    $time = $Task.At
    if ($time -notmatch '^\d{1,2}:\d{2}$') { throw "时间格式应为 HH:mm，收到：$time" }
    $start = (Get-Date).Date.AddDays(1).ToString("yyyy-MM-dd") + "T" + $time + ":00"
    $args = $ExePrefix + $Task.Args
    $xml = $xml.Replace("__DOC2MD_USER__", $UserId)
    $xml = $xml.Replace("__DOC2MD_EXE__", $ExeCommand)
    $xml = $xml.Replace("__DOC2MD_ROOT__", $RepoRoot)
    $xml = $xml.Replace("__DOC2MD_START__", $start)
    if ($xml -match '__DOC2MD_[A-Z_]+__') {
        throw "占位符未替换干净：$($Matches[0])（XML：$XmlPath）"
    }
    return $xml
}

function Install-Doc2mdTasks([string]$RepoRoot, [string]$UserId) {
    $exe = Resolve-Doc2mdExe $Exe $RepoRoot
    foreach ($t in $TASKS) {
        $xmlPath = Join-Path $PSScriptRoot $t.Xml
        $xml = New-TaskXml $xmlPath $t $exe.Command $exe.Prefix $RepoRoot $UserId
        if ($PSCmdlet.ShouldProcess($t.Name, "Register-ScheduledTask（$($t.Args) @ $($t.At)）")) {
            Register-ScheduledTask -TaskName $t.Name -Xml $xml -Force | Out-Null
            Write-Step "已注册 $($t.Name)：$($exe.Command) $($exe.Prefix)$($t.Args)（$($t.At) 本地时间）"
        }
    }
}

function Test-Doc2mdTask([string]$Name, [string]$ExpectArgs, [string]$RepoRoot) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) { return @{ Ok = $false; Why = "任务不存在" } }
    if ($task.State -eq "Disabled") { return @{ Ok = $false; Why = "任务被禁用" } }
    $act = $task.Actions[0]
    if (-not $act) { return @{ Ok = $false; Why = "无执行动作" } }
    if ($act.Arguments -notmatch [regex]::Escape($ExpectArgs)) {
        return @{ Ok = $false; Why = "动作参数不符：$($act.Arguments)（期望含 $ExpectArgs）" }
    }
    if ($act.WorkingDirectory -ne $RepoRoot) {
        return @{ Ok = $false; Why = "工作目录不符：$($act.WorkingDirectory)（期望 $RepoRoot）" }
    }
    $trig = $task.Triggers[0]
    if (-not $trig -or -not $trig.Enabled) { return @{ Ok = $false; Why = "无可用触发器" } }
    $info = Get-ScheduledTaskInfo -TaskName $Name
    $last = "从未运行"
    if ($info.LastRunTime) {
        $last = "$($info.LastRunTime.ToString('yyyy-MM-dd HH:mm')) 结果=$($info.LastTaskResult)"
        if ($info.LastTaskResult -ne 0) { return @{ Ok = $false; Why = "上次运行失败：$last" } }
    }
    return @{ Ok = $true; Why = "下次 $($info.NextRunTime)｜$last" }
}

function Test-Doc2mdTasks([string]$RepoRoot) {
    $fail = 0
    foreach ($t in $TASKS) {
        $r = Test-Doc2mdTask $t.Name $t.Args $RepoRoot
        if ($r.Ok) {
            Write-Host ("[doc2md-tasks] OK   {0,-24} {1}" -f $t.Name, $r.Why)
        } else {
            Write-Host ("[doc2md-tasks] FAIL {0,-24} {1}" -f $t.Name, $r.Why) -ForegroundColor Red
            $fail++
        }
    }
    return $fail
}

function Uninstall-Doc2mdTasks {
    foreach ($t in $TASKS) {
        $existing = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
        if ($existing) {
            if ($PSCmdlet.ShouldProcess($t.Name, "Unregister-ScheduledTask")) {
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
    $secure = Read-Host -AsSecureString "DOC2MD_BACKUP_PASSPHRASE（外置副本口令，不回显不落盘）"
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $plain) { throw "口令不能为空" }
    [Environment]::SetEnvironmentVariable("DOC2MD_BACKUP_PASSPHRASE", $plain, "User")
    $plain = $null
    Write-Step "已写入用户级环境变量 DOC2MD_BACKUP_PASSPHRASE（新开终端/会话后生效）"
}

if ($Uninstall) {
    Uninstall-Doc2mdTasks
    $left = @(Get-ScheduledTask -TaskName "md.doc2md.cron*" -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) { Write-Step "警告：仍有残留 $($left.Count) 个任务"; exit 1 }
    Write-Step "校验通过：md.doc2md.cron* 已全部移除"
    exit 0
}

if (-not $VerifyOnly) { Install-Doc2mdTasks $repoRoot $userId }

$failures = Test-Doc2mdTasks $repoRoot
if ($failures -gt 0) {
    Write-Step "校验失败 $failures 项；排障：Get-ScheduledTask -TaskName md.doc2md.cron* | Format-List *"
    exit 1
}
Write-Step "校验通过（等价 launchctl list | grep doc2md）：两个任务就绪，错过的时间窗会开机补跑"
