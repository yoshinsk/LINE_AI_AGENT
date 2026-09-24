# <PROJECT_ROOT>\scripts\status-worker.ps1
# LINE AI Agent Windowsワーカーのローカルプロセス状態を確認します。

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".state\worker.pid"
. (Join-Path $PSScriptRoot "worker-process.ps1")

if (Test-Path -LiteralPath $PidFile) {
    $pidValue = [int](Get-Content -LiteralPath $PidFile -Raw)
    $process = Get-LineAgentWorkerProcessById -ProcessId $pidValue
    if ($process) {
        Write-Output "running pid=$pidValue"
        exit 0
    }
}

$matches = Get-LineAgentWorkerProcesses
if ($matches) {
    $matches | ForEach-Object { Write-Output "running pid=$($_.ProcessId)" }
    exit 0
}

Write-Output "stopped"
exit 1
