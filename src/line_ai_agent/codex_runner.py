r"""C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\src\line_ai_agent\codex_runner.py

LINEジョブ、会話履歴、検索ナレッジ、添付ファイルをCodex CLIへ渡して最終回答を回収します。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shlex
import subprocess
import tempfile
from typing import Any

from .projects import ProjectSelection


COMMAND_FAILURE_REPLY = "内部処理を完了できませんでした。詳細はWindowsワーカーのログに記録しました。"
AI_AGENT_TIMEOUT_REPLY = "AIエージェントの実行がタイムアウトしました。"
AI_AGENT_EMPTY_REPLY = "AIエージェントの実行結果が空でした。"


@dataclass(frozen=True)
class CodexJob:
    """Codexへ渡す1件のLINEジョブです。"""

    job_id: int
    source_key: str
    request_text: str
    project: ProjectSelection
    recent_messages: tuple[dict[str, Any], ...]
    knowledge: tuple[dict[str, Any], ...]
    attachments: tuple[Path, ...]


@dataclass(frozen=True)
class CodexResult:
    """Codex実行結果です。"""

    text: str
    ok: bool


class CodexRunner:
    """dry-runまたは外部コマンド実行でCodex互換の回答を生成します。"""

    def __init__(
        self,
        command: str,
        timeout_seconds: int,
        no_project_workdir: Path,
        reply_max_chars: int,
    ) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._no_project_workdir = no_project_workdir
        self._reply_max_chars = reply_max_chars

    def run(self, job: CodexJob) -> CodexResult:
        """設定に応じてdry-runまたはCodex CLIを実行します。"""
        if not self._command:
            return CodexResult(build_dry_run_reply(job), True)

        workdir = job.project.project_path or self._no_project_workdir
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(job)
        args, output_file = self._prepare_command(job.job_id)
        env = os.environ.copy()
        env.update(
            {
                "LINE_AI_AGENT_JOB_ID": str(job.job_id),
                "LINE_AI_AGENT_SOURCE_KEY": job.source_key,
                "LINE_AI_AGENT_PROJECT_MODE": job.project.mode,
            }
        )
        if job.project.project_path:
            env["LINE_AI_AGENT_PROJECT_PATH"] = str(job.project.project_path)

        try:
            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                cwd=workdir,
                env=env,
                shell=False,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CodexResult(AI_AGENT_TIMEOUT_REPLY, False)
        except OSError:
            return CodexResult(COMMAND_FAILURE_REPLY, False)

        file_output = _read_and_remove(output_file)
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0 and not file_output:
            detail = stderr or stdout or f"exit code {completed.returncode}"
            return CodexResult(_clip(COMMAND_FAILURE_REPLY + "\n" + _clip(detail, 1000), self._reply_max_chars), False)

        text = file_output or stdout or AI_AGENT_EMPTY_REPLY
        return CodexResult(_clip(text.strip(), self._reply_max_chars), True)

    def _prepare_command(self, job_id: int) -> tuple[tuple[str, ...], Path | None]:
        """コマンド文字列を分割し、{output_file}をジョブ別パスへ置換します。"""
        parts = _resolve_stale_codex_parts(_split_command(self._command))
        if not parts:
            raise ValueError("CODEX_COMMAND is empty")
        if not any("{output_file}" in part for part in parts):
            return tuple(parts), None
        output_dir = Path(tempfile.gettempdir()) / "line-ai-agent"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"last-message-{job_id}.txt"
        output_file.unlink(missing_ok=True)
        return tuple(part.replace("{output_file}", str(output_file)) for part in parts), output_file


def build_prompt(job: CodexJob) -> str:
    """LINE会話履歴、検索ナレッジ、添付パスを含むCodex向けプロンプトを構築します。"""
    lines = [
        "あなたは株式会社NSKのLINE対応AIエージェントです。",
        "回答は日本語で、LINEで読みやすい短い段落を基本にしてください。",
        "事実と推測を分け、不明点は不明と答えてください。",
        "添付ファイルのパスがある場合は、必要に応じて実際に読み取って回答してください。",
        "",
        f"ジョブID: {job.job_id}",
        f"会話キー: {job.source_key}",
        f"プロジェクト: {job.project.label}",
        "",
    ]
    if job.recent_messages:
        lines.extend(["直近の会話履歴:"])
        for item in job.recent_messages:
            lines.append(f"- {item.get('created_at', '')} {item.get('role', '')}: {item.get('body', '')}")
        lines.append("")
    if job.knowledge:
        lines.extend(["検索で見つかった過去ナレッジ候補:"])
        for item in job.knowledge:
            lines.append(f"- {item.get('created_at', '')} {item.get('role', '')}: {item.get('text', '')}")
        lines.append("")
    if job.attachments:
        lines.extend(["添付ファイル:"])
        for path in job.attachments:
            lines.append(f"- {path}")
        lines.append("")
    lines.extend(["依頼内容:", job.request_text.strip()])
    return "\n".join(lines)


def build_dry_run_reply(job: CodexJob) -> str:
    """CODEX_COMMAND未設定時に安全な確認返信を作ります。"""
    attachment_lines = "\n".join(f"- {path}" for path in job.attachments) if job.attachments else "なし"
    return (
        "依頼を受信しました。ただしCODEX_COMMANDが未設定のため、AIエージェント実行は行っていません。\n"
        f"プロジェクト: {job.project.label}\n"
        f"添付ファイル:\n{attachment_lines}\n\n"
        f"依頼内容:\n{job.request_text}"
    )


def _split_command(command: str) -> list[str]:
    """Windowsパスを壊しにくい設定でコマンド文字列を分割します。"""
    return shlex.split(command, posix=os.name != "nt")


def _resolve_stale_codex_parts(parts: list[str]) -> list[str]:
    """Codexデスクトップ更新で古くなったcodex.exeパスを同階層の最新実体へ寄せます。"""
    if not parts:
        return parts
    first = Path(parts[0].strip('"'))
    if first.name.lower() != "codex.exe":
        return parts
    newest = _newest_sibling_codex_exe(first)
    if newest is None:
        return parts
    return [str(newest), *parts[1:]]


def _newest_sibling_codex_exe(path: Path) -> Path | None:
    """OpenAI\\Codex\\bin\\<version>配下から更新日時が最も新しいcodex.exeを探します。"""
    try:
        bin_dir = path.parent.parent
        candidates = [candidate for candidate in bin_dir.glob("*/codex.exe") if candidate.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _read_and_remove(path: Path | None) -> str:
    """Codexの--output-last-messageファイルを読み、後始末します。"""
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        path.unlink(missing_ok=True)


def _clip(text: str, max_chars: int) -> str:
    """LINEへ返す本文が長すぎる場合に末尾を明示して切り詰めます。"""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40].rstrip() + "\n...（長文のため省略）"
