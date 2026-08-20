r"""<PROJECT_ROOT>\src\line_ai_agent\config.py

ワーカーの.env読込と実行設定を提供します。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from typing import Mapping

from .result_assets import default_result_asset_allowed_dirs


DEFAULT_ENV_FILE = Path(".env")


def load_env_file(path: Path | None) -> dict[str, str]:
    """KEY=VALUE形式の.envを依存ライブラリなしで読み込みます。"""
    if path is None or not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def merged_env(env_file: Path | None) -> dict[str, str]:
    """ファイル設定にプロセス環境変数を上書きした設定辞書を返します。"""
    values = load_env_file(env_file)
    values.update({key: value for key, value in os.environ.items() if value is not None})
    return values


def _required(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _bool(values: Mapping[str, str], key: str, *, default: bool = False) -> bool:
    value = values.get(key, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _paths(values: Mapping[str, str], key: str) -> tuple[Path, ...]:
    return tuple(Path(item.strip()).expanduser() for item in values.get(key, "").split(os.pathsep) if item.strip())


@dataclass(frozen=True)
class Settings:
    """ワーカーで使う検証済み設定です。"""

    api_base_url: str
    worker_token: str
    worker_id: str
    poll_interval_seconds: int
    worker_concurrency: int
    attachment_download_dir: Path
    result_asset_output_dir: Path
    result_asset_allowed_dirs: tuple[Path, ...]
    result_asset_max_count: int
    codex_command: str
    codex_command_timeout_seconds: int
    codex_reply_max_chars: int
    codex_no_project_workdir: Path
    codex_projects_json: str
    codex_projects_file: Path | None
    codex_allowed_project_roots: tuple[Path, ...]

    @classmethod
    def from_env_file(cls, env_file: Path | None) -> "Settings":
        """任意の.envと環境変数から設定を作成します。"""
        values = merged_env(env_file)
        projects_file_raw = values.get("CODEX_PROJECTS_FILE", "").strip()
        config_base = env_file.resolve().parent if env_file is not None and env_file.exists() else Path.cwd()
        download_dir = Path(
            values.get(
                "LINE_AGENT_ATTACHMENT_DOWNLOAD_DIR",
                str(Path(".state") / "attachments"),
            )
        ).expanduser()
        if not download_dir.is_absolute():
            download_dir = config_base / download_dir
        result_asset_output_dir = Path(
            values.get(
                "LINE_AGENT_RESULT_ASSET_OUTPUT_DIR",
                str(Path(".state") / "result-assets"),
            )
        ).expanduser()
        if not result_asset_output_dir.is_absolute():
            result_asset_output_dir = config_base / result_asset_output_dir
        configured_result_asset_roots = _paths(values, "LINE_AGENT_RESULT_ASSET_ALLOWED_DIRS")
        result_asset_allowed_dirs = (
            configured_result_asset_roots
            if configured_result_asset_roots
            else default_result_asset_allowed_dirs(config_base)
        )
        no_project_raw = values.get("CODEX_NO_PROJECT_WORKDIR", "").strip()
        no_project_workdir = (
            Path(no_project_raw).expanduser()
            if no_project_raw
            else Path(tempfile.gettempdir()) / "line-ai-agent-no-project"
        )
        return cls(
            api_base_url=_required(values, "LINE_AGENT_API_BASE_URL").rstrip("?"),
            worker_token=_required(values, "LINE_AI_AGENT_WORKER_TOKEN"),
            worker_id=values.get("LINE_AI_AGENT_WORKER_ID", os.environ.get("COMPUTERNAME", "windows-worker")).strip()
            or "windows-worker",
            poll_interval_seconds=max(1, int(values.get("LINE_AGENT_POLL_INTERVAL_SECONDS", "5"))),
            worker_concurrency=max(1, int(values.get("LINE_AGENT_WORKER_CONCURRENCY", "1"))),
            attachment_download_dir=download_dir.resolve(),
            result_asset_output_dir=result_asset_output_dir.resolve(),
            result_asset_allowed_dirs=tuple(path.resolve(strict=False) for path in result_asset_allowed_dirs),
            result_asset_max_count=max(1, min(10, int(values.get("LINE_AGENT_RESULT_ASSET_MAX_COUNT", "5")))),
            codex_command=values.get("CODEX_COMMAND", "").strip(),
            codex_command_timeout_seconds=max(1, int(values.get("CODEX_COMMAND_TIMEOUT_SECONDS", "1800"))),
            codex_reply_max_chars=max(1000, int(values.get("CODEX_REPLY_MAX_CHARS", "8000"))),
            codex_no_project_workdir=no_project_workdir,
            codex_projects_json=values.get("CODEX_PROJECTS_JSON", "").strip(),
            codex_projects_file=Path(projects_file_raw).expanduser() if projects_file_raw else None,
            codex_allowed_project_roots=_paths(values, "CODEX_ALLOWED_PROJECT_ROOTS"),
        )
