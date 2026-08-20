r"""<PROJECT_ROOT>\tests\test_codex_prompt.py

Codexへ渡すプロンプトに会話履歴、検索ナレッジ、添付パスが入ることを検証します。
"""

from __future__ import annotations

from pathlib import Path
import unittest

from line_ai_agent.codex_runner import CodexJob, build_prompt
from line_ai_agent.projects import ProjectSelection


class CodexPromptTest(unittest.TestCase):
    """LINEジョブをCodex向け文脈へ変換する処理を確認します。"""

    def test_prompt_contains_attachment_paths_and_knowledge(self) -> None:
        job = CodexJob(
            job_id=12,
            source_key="user:Uxxx",
            request_text="添付を要約してください。",
            project=ProjectSelection("none", None, None, "未指定"),
            recent_messages=({"role": "user", "body": "前回の相談", "created_at": "2026-08-20"},),
            knowledge=({"role": "assistant", "text": "過去の回答", "created_at": "2026-08-20"},),
            attachments=(Path("C:/tmp/report.pdf"),),
        )
        prompt = build_prompt(job)
        self.assertIn(str(Path("C:/tmp/report.pdf")), prompt)
        self.assertIn("過去の回答", prompt)
        self.assertIn("添付を要約してください。", prompt)


if __name__ == "__main__":
    unittest.main()
