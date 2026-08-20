# <PROJECT_ROOT>\scripts\status-worker.ps1
# LINE AI Agent Windowsワーカーのローカルプロセス状態を確認します。

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".state\worker.pid"

if (Test-Path -LiteralPath $PidFile) {
    $pidValue = [int](Get-Content -LiteralPath $PidFile -Raw)
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Write-Output "running pid=$pidValue"
        exit 0
    }
}

$matches = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*line_ai_agent*" -and $_.CommandLine -like "* serve*"
}
if ($matches) {
    $matches | ForEach-Object { Write-Output "running pid=$($_.ProcessId)" }
    exit 0
}

Write-Output "stopped"
exit 1

