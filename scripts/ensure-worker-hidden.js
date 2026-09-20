// <PROJECT_ROOT>/scripts/ensure-worker-hidden.js
// Windows Task SchedulerからPowerShell監視をコンソールを表示せず実行し、終了コードを返すラッパーです。

(function () {
    if (WScript.Arguments.Length !== 3) {
        WScript.Quit(87);
    }

    function quote(value) {
        // 空白を含むパスもPowerShellの1個の引数として渡せるよう、二重引用符をエスケープします。
        return '"' + String(value).replace(/"/g, '""') + '"';
    }

    var ensureScript = WScript.Arguments.Item(0);
    var envFile = WScript.Arguments.Item(1);
    var logLevel = WScript.Arguments.Item(2);
    var command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + quote(ensureScript) + " -EnvFile " + quote(envFile) + " -LogLevel " + quote(logLevel);
    var shell = new ActiveXObject("WScript.Shell");

    // 第2引数の0は非表示、第3引数trueは監視スクリプトの終了コードを確実に回収するための待機です。
    WScript.Quit(shell.Run(command, 0, true));
}());
