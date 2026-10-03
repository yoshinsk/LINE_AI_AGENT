# <PROJECT_ROOT>\scripts\ensure-worker.ps1
# LINE AI Agent Windowsワーカーが停止していれば起動する監視用ワンショットです。

param(
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot ".state\logs"
$WatchdogLog = Join-Path $LogDir "worker-watchdog.log"
$StatusScript = Join-Path $PSScriptRoot "status-worker.ps1"
$StartScript = Join-Path $PSScriptRoot "start-worker.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-WorkerWatchdogLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $WatchdogLog -Value "[$timestamp] $Message" -Encoding utf8
}

$statusOutput = & $StatusScript 2>&1
if ($LASTEXITCODE -eq 0) {
    exit 0
}

$statusOutput | ForEach-Object { Write-Output $_ }
Write-Output "worker stopped; starting"
$startOutput = & $StartScript -EnvFile $EnvFile -LogLevel $LogLevel 2>&1
$startExitCode = $LASTEXITCODE
$startOutput | ForEach-Object { Write-Output $_ }
if ($startExitCode -ne 0) {
    Write-WorkerWatchdogLog "restart failed exit=$startExitCode output=$($startOutput -join ' ')"
    exit $startExitCode
}

$verifiedStatus = & $StatusScript 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-WorkerWatchdogLog "restart verification failed output=$($verifiedStatus -join ' ')"
    exit 1
}
Write-WorkerWatchdogLog "worker restarted $($verifiedStatus -join ' ')"
exit 0
