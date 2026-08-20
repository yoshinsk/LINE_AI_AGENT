# C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\stop-worker.ps1
# LINE AI Agent Windowsワーカーを停止します。

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".state\worker.pid"

if (Test-Path -LiteralPath $PidFile) {
    $pidValue = [int](Get-Content -LiteralPath $PidFile -Raw)
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $pidValue -Force
        Write-Output "stopped pid=$pidValue"
    }
    Remove-Item -LiteralPath $PidFile -Force
    exit 0
}

Write-Output "pid file not found"

