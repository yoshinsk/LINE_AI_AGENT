r"""<PROJECT_ROOT>\tests\test_projects.py

プロジェクト別名解決とローカル固定応答コマンドを検証します。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from line_ai_agent.projects import ProjectCatalog, is_project_list_request


class ProjectCatalogTest(unittest.TestCase):
    """LINEからのproject指定に対する解決ルールを確認します。"""

    def test_alias_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = ProjectCatalog.from_config('{"LINE_AI_AGENT":"' + temp_dir.replace("\\", "\\\\") + '"}', None, ())
            selection = catalog.select("line_ai_agent")
            self.assertEqual(Path(temp_dir).resolve(), selection.project_path)

    def test_project_list_request(self) -> None:
        self.assertTrue(is_project_list_request("プロジェクト一覧"))
        self.assertTrue(is_project_list_request("project list"))
        self.assertFalse(is_project_list_request("プロジェクト: demo"))


if __name__ == "__main__":
    unittest.main()
