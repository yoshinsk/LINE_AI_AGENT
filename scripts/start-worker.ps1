# C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\start-worker.ps1
# LINE AI Agent Windowsワーカーをバックグラウンド起動します。

param(
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StateDir = Join-Path $ProjectRoot ".state"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$outLog = Join-Path $LogDir "worker.out.log"
$errLog = Join-Path $LogDir "worker.err.log"
$pidFile = Join-Path $StateDir "worker.pid"

$args = @("-u", "-m", "line_ai_agent", "--env", $EnvFile, "--log-level", $LogLevel, "serve")
$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $ProjectRoot -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
Write-Output "started pid=$($process.Id)"

