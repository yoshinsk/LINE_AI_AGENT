# <PROJECT_ROOT>\scripts\start-worker.ps1
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

if (Test-Path -LiteralPath $pidFile) {
    $pidRaw = Get-Content -LiteralPath $pidFile -Raw
    $pidValue = 0
    if ([int]::TryParse($pidRaw.Trim(), [ref]$pidValue)) {
        $existing = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Output "already running pid=$pidValue"
            exit 0
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

$matches = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*line_ai_agent*" -and $_.CommandLine -like "* serve*"
}
if ($matches) {
    $runningPid = [int]($matches | Select-Object -First 1).ProcessId
    $runningPid | Set-Content -LiteralPath $pidFile -Encoding ascii
    Write-Output "already running pid=$runningPid"
    exit 0
}

$args = @("-u", "-m", "line_ai_agent", "--env", $EnvFile, "--log-level", $LogLevel, "serve")
$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $ProjectRoot -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
Write-Output "started pid=$($process.Id)"
