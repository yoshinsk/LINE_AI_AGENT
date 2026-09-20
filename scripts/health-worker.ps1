# <PROJECT_ROOT>\scripts\health-worker.ps1
# ワーカーと同じPYTHONPATHで公開サーバ内部APIのhealthを確認します。

param(
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# start-worker.ps1と同じモジュール探索パスを設定し、未インストールのsrcパッケージでも診断だけ失敗しないようにします。
& python -m line_ai_agent --env $EnvFile --log-level $LogLevel health
exit $LASTEXITCODE
