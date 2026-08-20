r"""C:\Users\Yoshi\Documents\GitHub\LINE_AI_AGENT\src\line_ai_agent\projects.py

LINE上のproject指定をローカルGit作業ディレクトリへ解決します。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class ProjectSelection:
    """Codex実行時の作業ディレクトリ選択結果です。"""

    mode: str
    project_ref: str | None
    project_path: Path | None
    label: str


class ProjectCatalog:
    """別名と許可ルートに基づいてプロジェクト指定を解決します。"""

    def __init__(self, aliases: dict[str, Path], allowed_roots: tuple[Path, ...]) -> None:
        self._aliases = {key.lower(): value for key, value in aliases.items()}
        self._allowed_roots = tuple(root.resolve() for root in allowed_roots)

    @classmethod
    def from_config(
        cls,
        projects_json: str,
        projects_file: Path | None,
        allowed_roots: tuple[Path, ...],
    ) -> "ProjectCatalog":
        """JSON文字列とJSONファイルを統合してカタログを作成します。"""
        aliases: dict[str, Path] = {}
        if projects_json:
            aliases.update(_parse_projects_json(projects_json))
        if projects_file and projects_file.exists():
            aliases.update(_parse_projects_json(projects_file.read_text(encoding="utf-8")))
        return cls(aliases, allowed_roots)

    def select(self, project_ref: str | None) -> ProjectSelection:
        """指定なしは通常チャット、別名または許可ルート配下の直接パスはプロジェクトとして返します。"""
        if project_ref is None or not project_ref.strip():
            return ProjectSelection("none", None, None, "未指定")

        ref = project_ref.strip()
        alias_path = self._aliases.get(ref.lower())
        if alias_path is not None:
            return ProjectSelection("alias", ref, alias_path.resolve(), ref)

        direct = Path(ref).expanduser()
        if direct.exists() and self._is_allowed(direct):
            return ProjectSelection("path", ref, direct.resolve(), str(direct.resolve()))

        raise ValueError(f"未登録または許可外のプロジェクトです: {ref}")

    def format_project_list(self) -> str:
        """LINEで返すプロジェクト一覧を整形します。"""
        if not self._aliases:
            return "登録済みプロジェクトはありません。"
        lines = ["登録済みプロジェクト:"]
        for alias, path in sorted(self._aliases.items()):
            lines.append(f"- {alias} = {path}")
        return "\n".join(lines)

    def _is_allowed(self, path: Path) -> bool:
        """直接パス指定が許可ルート配下かどうかを確認します。"""
        if not self._allowed_roots:
            return False
        resolved = path.resolve()
        return any(resolved == root or root in resolved.parents for root in self._allowed_roots)


def is_project_list_request(text: str) -> bool:
    """Codexを起動せずローカル応答すべきプロジェクト一覧依頼を判定します。"""
    normalized = text.strip().lower()
    return normalized in {"プロジェクト一覧", "project list", "projects"}


def _parse_projects_json(raw: str) -> dict[str, Path]:
    """alias/path形式のJSONをPath辞書へ変換します。"""
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("CODEX_PROJECTS_JSON must be an object")
    return {str(alias): Path(str(path)).expanduser() for alias, path in decoded.items()}
