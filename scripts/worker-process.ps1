<#
File: C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\worker-process.ps1
Summary: LINE AI Agent常駐ワーカーだけをPython実行コマンドから厳密に識別する共通関数です。
#>

<#
プロセス名とコマンドラインがLINE AI Agentワーカーの実行形式と一致するか判定します。
PowerShell監視スクリプト自身の本文に "line_ai_agent" や "serve" が含まれていても、
Python実行プロセス以外はワーカーとして扱いません。
#>
function Test-LineAgentWorkerProcess {
    param(
        [string]$ProcessName,
        [string]$CommandLine
    )

    return (($ProcessName -in @("python.exe", "pythonw.exe")) -and
        ($CommandLine -match "(?i)(?:^|\s)-m\s+line_ai_agent(?:\s|$)") -and
        ($CommandLine -match "(?i)(?:^|\s)serve(?:\s|$)"))
}

<#
実際に常駐ワーカーとして起動しているPythonプロセスだけを返します。
#>
function Get-LineAgentWorkerProcesses {
    $pythonProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue
    return @($pythonProcesses | Where-Object {
        Test-LineAgentWorkerProcess -ProcessName $_.Name -CommandLine $_.CommandLine
    })
}

<#
PIDファイルの値が、偶然再利用された別プロセスではなく実ワーカーかを確認して返します。
#>
function Get-LineAgentWorkerProcessById {
    param([int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process -and (Test-LineAgentWorkerProcess -ProcessName $process.Name -CommandLine $process.CommandLine)) {
        return $process
    }
    return $null
}
