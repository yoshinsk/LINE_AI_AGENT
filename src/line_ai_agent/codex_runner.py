r"""<PROJECT_ROOT>\src\line_ai_agent\codex_runner.py

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
import time
from typing import Any

from .projects import ProjectSelection
from .result_assets import collect_result_asset_paths, sanitize_result_text


COMMAND_FAILURE_REPLY = "内部処理を完了できませんでした。詳細はワーカーのログに記録しました。"
AI_AGENT_TIMEOUT_REPLY = "AIエージェントの実行がタイムアウトしました。"
AI_AGENT_EMPTY_REPLY = "AIエージェントの実行結果が空でした。"
CODEX_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
OFFICE_DOCUMENT_SUFFIXES = {".docx", ".xlsx"}
OFFICE_TEXT_SIDECAR_SUFFIX = ".line-office-extracted.txt"
OFFICE_REVISION_KEYWORDS = (
    "添削",
    "校正",
    "校閲",
    "修正",
    "訂正",
    "推敲",
    "書き直",
    "書き換",
    "リライト",
    "ブラッシュアップ",
    "改善",
    "磨いて",
    "直して",
)


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
    result_asset_dir: Path | None = None


@dataclass(frozen=True)
class CodexResult:
    """Codex実行結果です。"""

    text: str
    ok: bool
    asset_paths: tuple[Path, ...] = ()


class CodexRunner:
    """dry-runまたは外部コマンド実行でCodex互換の回答を生成します。"""

    def __init__(
        self,
        command: str,
        timeout_seconds: int,
        no_project_workdir: Path,
        reply_max_chars: int,
        result_asset_output_dir: Path,
        result_asset_allowed_dirs: tuple[Path, ...],
        result_asset_max_count: int,
    ) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._no_project_workdir = no_project_workdir
        self._reply_max_chars = reply_max_chars
        self._result_asset_output_dir = result_asset_output_dir
        self._result_asset_allowed_dirs = result_asset_allowed_dirs
        self._result_asset_max_count = result_asset_max_count

    def run(self, job: CodexJob) -> CodexResult:
        """設定に応じてdry-runまたはCodex CLIを実行します。"""
        job = self._with_result_asset_dir(job)
        if not self._command:
            return CodexResult(build_dry_run_reply(job), True, ())

        return self._run_command(job, build_prompt(job))

    def run_office_revision_retry(self, job: CodexJob) -> CodexResult:
        """Office修正成果物が未生成だった場合、成果物作成だけを明示してCodexを再実行します。"""
        job = self._with_result_asset_dir(job)
        if not self._command:
            return CodexResult("修正済みOfficeファイルを生成できませんでした。", False, ())

        return self._run_command(job, build_office_revision_retry_prompt(job))

    def _run_command(self, job: CodexJob, prompt: str) -> CodexResult:
        """指定プロンプトでCodex CLIを一度実行し、今回更新された成果物だけを回収します。"""

        workdir = job.project.project_path or self._no_project_workdir
        workdir.mkdir(parents=True, exist_ok=True)
        args, output_file = self._prepare_command(job)
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
        if job.result_asset_dir:
            env["LINE_AI_AGENT_RESULT_ASSET_DIR"] = str(job.result_asset_dir)

        execution_started_at = time.time()
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

        raw_text = (file_output or stdout or AI_AGENT_EMPTY_REPLY).strip()
        asset_paths = collect_result_asset_paths(
            raw_text,
            job.result_asset_dir or self._result_asset_output_dir / f"job-{job.job_id}",
            self._result_asset_allowed_dirs,
            self._result_asset_max_count,
            modified_since=execution_started_at - 2.0,
        )
        text = sanitize_result_text(raw_text, asset_paths)
        return CodexResult(_clip(text, self._reply_max_chars), True, asset_paths)

    def _with_result_asset_dir(self, job: CodexJob) -> CodexJob:
        """ジョブごとの成果物出力ディレクトリを確定し、Codexへ渡せる状態にします。"""
        result_asset_dir = job.result_asset_dir or self._result_asset_output_dir / f"job-{job.job_id}"
        result_asset_dir.mkdir(parents=True, exist_ok=True)
        return CodexJob(
            job_id=job.job_id,
            source_key=job.source_key,
            request_text=job.request_text,
            project=job.project,
            recent_messages=job.recent_messages,
            knowledge=job.knowledge,
            attachments=job.attachments,
            result_asset_dir=result_asset_dir,
        )

    def _prepare_command(self, job: CodexJob) -> tuple[tuple[str, ...], Path | None]:
        """コマンド文字列を分割し、出力先と画像添付引数をジョブ別に組み立てます。"""
        parts = _resolve_stale_codex_parts(_split_command(self._command))
        if not parts:
            raise ValueError("CODEX_COMMAND is empty")
        output_file = None
        if any("{output_file}" in part for part in parts):
            output_dir = Path(tempfile.gettempdir()) / "line-ai-agent"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"last-message-{job.job_id}.txt"
            output_file.unlink(missing_ok=True)
            parts = [part.replace("{output_file}", str(output_file)) for part in parts]

        image_args = _codex_image_args(job.attachments)
        if image_args:
            parts = _insert_codex_image_args(parts, image_args)
        return tuple(parts), output_file


def build_prompt(job: CodexJob) -> str:
    """LINE会話履歴、検索ナレッジ、添付パスを含むCodex向けプロンプトを構築します。"""
    lines = [
        "あなたはLINE対応AIエージェントです。",
        "回答は日本語で、LINEで読みやすい短い段落を基本にしてください。",
        "事実と推測を分け、不明点は不明と答えてください。",
        "添付ファイルのパスがある場合は、必要に応じて実際に読み取って回答してください。",
        "",
        f"ジョブID: {job.job_id}",
        f"会話キー: {job.source_key}",
        f"プロジェクト: {job.project.label}",
        "",
    ]
    if job.result_asset_dir:
        lines.extend(
            [
                "LINE送信用の成果物出力先:",
                str(job.result_asset_dir),
                "画像、テキスト、PDF、Office文書などをファイルとして返す場合は、上記ディレクトリへ保存してください。",
                "回答には生成ファイル名を短く書き、ローカル保存先だけで完了扱いにしないでください。",
                "成果物を保存できた場合、本文には完了と生成ファイル名だけを記載し、ローカルパス、CryptUnprotectDataなどの内部実行環境エラー、または「この返信内に出力済み」という表現は含めないでください。",
                "",
            ]
        )
    if requires_office_revision(job):
        source_names = ", ".join(path.name for path in office_documents(job))
        lines.extend(
            [
                "Office修正成果物の必須条件:",
                f"今回の依頼は {source_names} の修正済みファイル返却を求めています。本文の提案だけで完了してはいけません。",
                "元のDOCX/XLSXをコピーして実際に編集し、元と同じ拡張子の修正済みファイルをLINE送信用の成果物出力先へ保存してください。",
                "Wordは本文を実ファイル内で修正し、Excelは対象セルの文言を修正してください。Excelの数式・書式・シート構成は保持してください。",
                "ファイル名は末尾を -revised.docx または -revised.xlsx とし、回答前に出力先に実在することを確認してください。",
                "ローカルパス、修正案だけの本文、または元ファイルの単なるコピーだけを成果物として返してはいけません。",
                "",
            ]
        )
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
    attachments = [path for path in job.attachments if not _is_office_text_sidecar(path)]
    office_text_sidecars = [path for path in job.attachments if _is_office_text_sidecar(path)]
    if attachments:
        lines.extend(["添付ファイル:"])
        for path in attachments:
            lines.append(f"- {path}")
        if any(_is_codex_image(path) for path in attachments):
            lines.append("画像添付はCodex CLIの--imageにも渡されています。")
        lines.append("")
    if office_text_sidecars:
        lines.extend(["Office文書から抽出した内容:"])
        for path in office_text_sidecars:
            source_name = path.name.removesuffix(OFFICE_TEXT_SIDECAR_SUFFIX)
            try:
                extracted_text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                extracted_text = ""
            if extracted_text:
                lines.extend([f"--- {source_name} ---", extracted_text, "--- 抽出終了 ---"])
        lines.extend(
            [
                "上記の抽出内容をOffice文書の本文・セル値として読み、元ファイルを読めないことだけを理由に回答を保留しないでください。",
                "",
            ]
        )
    lines.extend(["依頼内容:", job.request_text.strip()])
    return "\n".join(lines)


def build_office_revision_retry_prompt(job: CodexJob) -> str:
    """Office修正の初回実行で成果物がなかったとき、ファイル生成に限定した再試行指示を作ります。"""
    return "\n".join(
        [
            build_prompt(job),
            "",
            "重要: 前回の実行ではLINEへ返却できる修正済みOfficeファイルが出力先にありませんでした。",
            "今回の実行では説明文だけを返さず、元ファイルを実際に編集した -revised.docx または -revised.xlsx を成果物出力先へ必ず作成してください。",
            "出力ファイルが存在することを確認した後で、生成ファイル名だけを短く回答してください。",
        ]
    )


def office_documents(job: CodexJob) -> tuple[Path, ...]:
    """添付群から、返却対象として扱えるOffice Open XML文書だけを取り出します。"""
    return tuple(path for path in job.attachments if path.suffix.lower() in OFFICE_DOCUMENT_SUFFIXES)


def requires_office_revision(job: CodexJob) -> bool:
    """Office添付に対する添削・修正依頼かを、明示的な日本語キーワードで判定します。"""
    request = job.request_text.lower()
    return bool(office_documents(job)) and any(keyword in request for keyword in OFFICE_REVISION_KEYWORDS)


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


def _codex_image_args(attachments: tuple[Path, ...]) -> list[str]:
    """Codex CLIの--imageへ渡せる画像添付だけを引数化します。"""
    args: list[str] = []
    for path in attachments:
        if not _is_codex_image(path):
            continue
        args.extend(["--image", str(path.resolve(strict=False))])
    return args


def _is_codex_image(path: Path) -> bool:
    """Codex CLIが画像入力として受け取れる拡張子かを判定します。"""
    return path.suffix.lower() in CODEX_IMAGE_SUFFIXES


def _is_office_text_sidecar(path: Path) -> bool:
    """ワーカーが作成したOffice抽出テキストかをファイル名の固定接尾辞で判定します。"""
    return path.name.endswith(OFFICE_TEXT_SIDECAR_SUFFIX)


def _insert_codex_image_args(parts: list[str], image_args: list[str]) -> list[str]:
    """標準入力プロンプト指定の直前に--imageを差し込みます。"""
    insert_at = len(parts)
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == "-":
            insert_at = index
            break
    return [*parts[:insert_at], *image_args, *parts[insert_at:]]


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
