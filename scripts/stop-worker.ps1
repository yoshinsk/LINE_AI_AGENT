# <PROJECT_ROOT>\scripts\stop-worker.ps1
# LINE AI Agent Windowsワーカーを停止します。

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".state\worker.pid"
. (Join-Path $PSScriptRoot "worker-process.ps1")

if (Test-Path -LiteralPath $PidFile) {
    $pidValue = [int](Get-Content -LiteralPath $PidFile -Raw)
    $process = Get-LineAgentWorkerProcessById -ProcessId $pidValue
    if ($process) {
        Stop-Process -Id $pidValue -Force
        Write-Output "stopped pid=$pidValue"
    } else {
        Write-Output "stale worker pid=$pidValue"
    }
    Remove-Item -LiteralPath $PidFile -Force
    exit 0
}

Write-Output "pid file not found"
