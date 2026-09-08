# File: C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\scripts\ensure-worker-hidden.py
# Summary: Task Schedulerからensure-worker.ps1をコンソール非表示で実行するラッパーです。

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
POWERSHELL_EXE = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")


def parse_args() -> argparse.Namespace:
    """Task Schedulerから渡される監視用引数を解釈します。"""
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=str(project_root / ".env"))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    """PowerShell監視スクリプトを子プロセスとして非表示実行し、終了コードを引き継ぎます。"""
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    ensure_script = script_dir / "ensure-worker.ps1"

    command = [
        str(POWERSHELL_EXE),
        "-NoProfile",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ensure_script),
        "-EnvFile",
        str(Path(args.env_file)),
        "-LogLevel",
        args.log_level,
    ]

    # pythonw.exeには標準コンソールがないため、子PowerShellにもコンソールを割り当てません。
    result = subprocess.run(
        command,
        cwd=str(project_root),
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return int(result.returncode)


if __name__ == "__main__":
    sys.exit(main())
