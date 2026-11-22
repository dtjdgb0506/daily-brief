# 注册 Windows 计划任务：每天 07:30 跑 run_daily.py
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File scripts/install_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/install_task.ps1 -Time 07:15
# 卸载：
#   Unregister-ScheduledTask -TaskName "每日情报" -Confirm:$false

# 注意：Windows 任务计划只能在本机开机/睡眠时跑。
# 关机也要推 → 用 GitHub Actions（见 README「电脑关机也要推」）。
# 睡眠时想叫醒电脑跑：加 -WakeToRun

param(
    [string]$TaskName = "每日情报",
    [string]$Time = "07:30",
    [string]$Python = "",
    [string]$WorkDir = "",
    [switch]$WakeToRun
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if (-not $WorkDir) { $WorkDir = $Root }
if (-not $Python) {
    # 优先用装机时的 Python；找不到再回退到 PATH 里的 python
    $candidates = @(
        "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
        "python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -eq "python.exe") { $Python = "python.exe"; break }
        if (Test-Path -LiteralPath $c) { $Python = $c; break }
    }
}

$runner = Join-Path $WorkDir "scripts\run_daily.py"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "找不到 $runner"
}

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "`"$runner`"" `
    -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settingsArgs = @{
    StartWhenAvailable = $true
    DontStopIfGoingOnBatteries = $true
    AllowStartIfOnBatteries = $true
    ExecutionTimeLimit = (New-TimeSpan -Minutes 30)
    RestartCount = 2
    RestartInterval = (New-TimeSpan -Minutes 5)
}
if ($WakeToRun) { $settingsArgs["WakeToRun"] = $true }
$settings = New-ScheduledTaskSettingsSet @settingsArgs

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "每日情报：采集 → 聚类 → 出卡 → 发信" -Force | Out-Null

Write-Host "已注册计划任务「$TaskName」：每天 $Time"
Write-Host "  Python : $Python"
Write-Host "  脚本   : $runner"
Write-Host "  工作目录: $WorkDir"
Write-Host "查看：Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "立即测试：Start-ScheduledTask -TaskName '$TaskName'"
