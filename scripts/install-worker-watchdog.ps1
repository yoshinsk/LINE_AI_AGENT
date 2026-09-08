# File: C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\install-worker-watchdog.ps1
# Summary: Task Schedulerへensure-worker.ps1の非表示ラッパーを1分間隔で登録します。

param(
    [string]$TaskName = "LINE_AI_AGENT_Worker_Watchdog",
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HiddenRunner = Join-Path $PSScriptRoot "ensure-worker-hidden.py"
$PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
$PythonwExe = if ($PythonExe) { Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe" } else { "pythonw.exe" }

# Windows Terminalが既定ターミナルの場合、powershell.exe直起動では一瞬ウィンドウが出るためGUIサブシステムのpythonw.exeを使います。
if (-not (Get-Command $PythonwExe -ErrorAction SilentlyContinue)) {
    throw "pythonw.exe was not found. Install Python or ensure pythonw.exe is on PATH."
}

$argument = "`"$HiddenRunner`" --env-file `"$EnvFile`" --log-level `"$LogLevel`""

$action = New-ScheduledTaskAction -Execute $PythonwExe -Argument $argument -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Keeps the LINE AI Agent Windows worker running." -Force | Out-Null
Write-Output "watchdog registered task=$TaskName interval=1m"
