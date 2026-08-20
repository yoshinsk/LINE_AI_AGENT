r"""<PROJECT_ROOT>\src\line_ai_agent\result_assets.py

Codexが生成した画像・文書などの成果物ファイルをLINE送信用に検出し、ローカルパスを回答文から除去します。
"""

from __future__ import annotations

from pathlib import Path
import os
import re


RESULT_ASSET_SUFFIXES = {
    ".csv",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".pptx",
    ".tsv",
    ".txt",
    ".webp",
    ".xlsx",
}
RESULT_ASSET_BLOCKED_SUFFIXES = {
    ".bat",
    ".bash",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".fish",
    ".hta",
    ".html",
    ".jar",
    ".js",
    ".msi",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".so",
    ".svg",
    ".vbs",
    ".wsf",
    ".xhtml",
    ".xml",
    ".zsh",
}
_SUFFIX_PATTERN = "|".join(re.escape(item.lstrip(".")) for item in sorted(RESULT_ASSET_SUFFIXES, key=len, reverse=True))
_WINDOWS_PATH_PATTERN = re.compile(rf"[A-Za-z]:\\[^\r\n<>\"|?*]+?\.({_SUFFIX_PATTERN})(?=$|[\s\r\n\"')\]}}、。,.）】])", re.IGNORECASE)
_POSIX_PATH_PATTERN = re.compile(rf"/[^\r\n<>\"|?*]+?\.({_SUFFIX_PATTERN})(?=$|[\s\r\n\"')\]}}、。,.）】])", re.IGNORECASE)


def default_result_asset_allowed_dirs(config_base: Path) -> tuple[Path, ...]:
    """ワーカー既定で安全に成果物として扱えるディレクトリを返します。"""
    return (
        config_base / ".state" / "result-assets",
        Path.home() / ".codex" / "generated_images",
    )


def collect_result_asset_paths(text: str, output_dir: Path, allowed_dirs: tuple[Path, ...], max_count: int) -> tuple[Path, ...]:
    """ジョブ成果物ディレクトリと回答文の絶対パスから送信対象ファイルを集めます。"""
    roots = _resolved_roots((output_dir, *allowed_dirs))
    candidates = [*_files_under(output_dir), *(_path_from_text(item) for item in _path_strings(text))]
    collected: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_file():
            continue
        if not _is_inside_any(resolved, roots) or not is_result_asset_allowed(resolved):
            continue
        collected.append(resolved)
        seen.add(key)
        if len(collected) >= max(1, max_count):
            break
    return tuple(collected)


def sanitize_result_text(text: str, asset_paths: tuple[Path, ...]) -> str:
    """LINE本文にWindowsローカル絶対パスを出さないよう、ファイル名表記へ置き換えます。"""
    sanitized = text
    for path in asset_paths:
        label = f"{path.name}（LINEへ送信します）"
        variants = {str(path), path.as_posix()}
        try:
            variants.add(str(path.resolve(strict=False)))
            variants.add(path.resolve(strict=False).as_posix())
        except OSError:
            pass
        for variant in sorted(variants, key=len, reverse=True):
            sanitized = sanitized.replace(variant, label)
    return sanitized


def is_result_asset_allowed(path: Path) -> bool:
    """成果物として公開してよい拡張子だけを許可します。"""
    suffix = path.suffix.lower()
    if suffix in RESULT_ASSET_BLOCKED_SUFFIXES:
        return False
    return suffix in RESULT_ASSET_SUFFIXES


def _resolved_roots(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """存在しないルートも含め、比較用の絶対パスへ正規化します。"""
    roots: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen:
            roots.append(resolved)
            seen.add(key)
    return tuple(roots)


def _files_under(path: Path) -> list[Path]:
    """成果物ディレクトリ配下のファイルを更新順で列挙します。"""
    if not path.is_dir():
        return []
    try:
        files = [item for item in path.rglob("*") if item.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)


def _path_strings(text: str) -> list[str]:
    """回答文中のWindows/POSIX絶対パス候補を取り出します。"""
    values: list[str] = []
    for pattern in (_WINDOWS_PATH_PATTERN, _POSIX_PATH_PATTERN):
        values.extend(match.group(0).strip().rstrip("。、.,") for match in pattern.finditer(text))
    return values


def _path_from_text(value: str) -> Path | None:
    """文字列パスをPathへ変換します。空値は捨てます。"""
    stripped = value.strip().strip('"').strip("'")
    return Path(stripped) if stripped else None


def _is_inside_any(path: Path, roots: tuple[Path, ...]) -> bool:
    """成果物パスが許可ルート配下にあるか確認します。"""
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False
