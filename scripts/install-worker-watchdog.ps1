# <PROJECT_ROOT>\scripts\install-worker-watchdog.ps1
# Task Schedulerへensure-worker.ps1の1分間隔監視タスクを登録します。

param(
    [string]$TaskName = "LINE_AI_AGENT_Worker_Watchdog",
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$EnsureScript = Join-Path $PSScriptRoot "ensure-worker.ps1"
$PowerShellExe = Join-Path $PSHOME "powershell.exe"
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$EnsureScript`" -EnvFile `"$EnvFile`" -LogLevel `"$LogLevel`""

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $argument
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Keeps the LINE AI Agent Windows worker running." -Force | Out-Null
Write-Output "watchdog registered task=$TaskName interval=1m"
