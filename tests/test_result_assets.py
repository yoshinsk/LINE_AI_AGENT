r"""<PROJECT_ROOT>\tests\test_result_assets.py

Codex生成成果物の検出とLINE本文用のローカルパス除去を検証します。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from line_ai_agent.result_assets import collect_result_asset_paths, sanitize_result_text


class ResultAssetsTest(unittest.TestCase):
    """成果物ファイルの検出境界を確認します。"""

    def test_collects_files_under_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "job-1"
            output_dir.mkdir()
            result_file = output_dir / "summary.txt"
            result_file.write_text("result", encoding="utf-8")

            paths = collect_result_asset_paths("", output_dir, (output_dir,), 5)

            self.assertEqual((result_file.resolve(),), paths)

    def test_collects_allowed_path_from_text_and_sanitizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed_dir = Path(temp_dir)
            result_file = allowed_dir / "image.png"
            result_file.write_bytes(b"\x89PNG\r\n\x1a\n")
            text = f"出力先: {result_file}"

            paths = collect_result_asset_paths(text, allowed_dir / "empty", (allowed_dir,), 5)
            sanitized = sanitize_result_text(text, paths)

            self.assertEqual((result_file.resolve(),), paths)
            self.assertNotIn(str(result_file), sanitized)
            self.assertIn("image.png", sanitized)

    def test_rejects_paths_outside_allowed_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed_dir = Path(temp_dir) / "allowed"
            outside_dir = Path(temp_dir) / "outside"
            allowed_dir.mkdir()
            outside_dir.mkdir()
            outside_file = outside_dir / "secret.txt"
            outside_file.write_text("secret", encoding="utf-8")

            paths = collect_result_asset_paths(str(outside_file), allowed_dir, (allowed_dir,), 5)

            self.assertEqual((), paths)


if __name__ == "__main__":
    unittest.main()
