# File: C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\install-worker-watchdog.ps1
# Summary: Task SchedulerへWindows標準の非表示ラッパーを1分間隔で登録します。

param(
    [string]$TaskName = "LINE_AI_AGENT_Worker_Watchdog",
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HiddenRunner = Join-Path $PSScriptRoot "ensure-worker-hidden.js"
$EnsureScript = Join-Path $PSScriptRoot "ensure-worker.ps1"
$WscriptExe = Join-Path $env:WINDIR "System32\wscript.exe"

# pythonw.exeはGUIサブシステムでも環境により0xC0000005で終了するため、Windows標準のwscript.exeを使います。
if (-not (Test-Path -LiteralPath $WscriptExe)) {
    throw "wscript.exe was not found: $WscriptExe"
}

$argument = "`"$HiddenRunner`" `"$EnsureScript`" `"$EnvFile`" `"$LogLevel`""

$action = New-ScheduledTaskAction -Execute $WscriptExe -Argument $argument -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Keeps the LINE AI Agent Windows worker running." -Force | Out-Null
Write-Output "watchdog registered task=$TaskName interval=1m"
