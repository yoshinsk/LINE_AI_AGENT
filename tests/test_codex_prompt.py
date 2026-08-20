r"""<PROJECT_ROOT>\tests\test_codex_prompt.py

Codexへ渡すプロンプトに会話履歴、検索ナレッジ、添付パスが入ることを検証します。
"""

from __future__ import annotations

from pathlib import Path
import unittest

from line_ai_agent.codex_runner import CodexJob, CodexRunner, build_prompt
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

    def test_codex_command_attaches_image_files(self) -> None:
        runner = CodexRunner(
            command='codex exec --output-last-message {output_file} -',
            timeout_seconds=30,
            no_project_workdir=Path("C:/tmp"),
            reply_max_chars=4500,
        )
        image_path = Path("C:/tmp/photo.jpg")
        pdf_path = Path("C:/tmp/report.pdf")
        job = CodexJob(
            job_id=34,
            source_key="group:Gxxx",
            request_text="画像を確認してください。",
            project=ProjectSelection("none", None, None, "未指定"),
            recent_messages=(),
            knowledge=(),
            attachments=(image_path, pdf_path),
        )
        args, output_file = runner._prepare_command(job)
        image_arg_index = args.index("--image")
        self.assertEqual(str(image_path.resolve(strict=False)), args[image_arg_index + 1])
        self.assertLess(image_arg_index, args.index("-"))
        self.assertNotIn(str(pdf_path.resolve(strict=False)), args)
        self.assertIsNotNone(output_file)


if __name__ == "__main__":
    unittest.main()
