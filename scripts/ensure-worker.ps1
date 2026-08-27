# <PROJECT_ROOT>\scripts\ensure-worker.ps1
# LINE AI Agent Windowsワーカーが停止していれば起動する監視用ワンショットです。

param(
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$StatusScript = Join-Path $PSScriptRoot "status-worker.ps1"
$StartScript = Join-Path $PSScriptRoot "start-worker.ps1"

$statusOutput = & $StatusScript 2>&1
if ($LASTEXITCODE -eq 0) {
    $statusOutput | ForEach-Object { Write-Output $_ }
    exit 0
}

$statusOutput | ForEach-Object { Write-Output $_ }
Write-Output "worker stopped; starting"
& $StartScript -EnvFile $EnvFile -LogLevel $LogLevel
exit 0
