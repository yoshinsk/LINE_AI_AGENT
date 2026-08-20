r"""<PROJECT_ROOT>\src\line_ai_agent\worker.py

VPSのDBキューからジョブを取得し、添付を保存してCodex実行結果をLINEへ返す常駐ワーカーです。
"""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import re
import threading
import time
from typing import Any

from .api_client import ApiClient
from .codex_runner import CodexJob, CodexRunner
from .config import Settings
from .projects import ProjectCatalog, is_project_list_request


LOGGER = logging.getLogger(__name__)


class LineWorker:
    """VPS入口とWindows上のCodex実行をつなぐワーカーサービスです。"""

    def __init__(self, settings: Settings, client: ApiClient, runner: CodexRunner, projects: ProjectCatalog) -> None:
        self._settings = settings
        self._client = client
        self._runner = runner
        self._projects = projects
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        """ジョブを1件claimして処理します。ジョブがなければFalseを返します。"""
        response = self._client.claim(self._settings.codex_command_timeout_seconds + 120)
        job_payload = response.get("job")
        if not job_payload:
            return False

        self._process_claimed_response(response)
        return True

    def serve_forever(self) -> None:
        """停止要求までポーリングを続けます。"""
        LOGGER.info("worker started id=%s concurrency=%s", self._client.worker_id, self._settings.worker_concurrency)
        self._client.heartbeat("started", {"concurrency": self._settings.worker_concurrency})
        active: set[Future[bool]] = set()
        with ThreadPoolExecutor(max_workers=self._settings.worker_concurrency, thread_name_prefix="line-ai-agent") as executor:
            while not self._stop_event.is_set():
                active = {future for future in active if not future.done()}
                did_claim = False
                while len(active) < self._settings.worker_concurrency:
                    response = self._client.claim(self._settings.codex_command_timeout_seconds + 120)
                    if not response.get("job"):
                        break
                    active.add(executor.submit(self._process_claimed_response, response))
                    did_claim = True

                self._client.heartbeat("running" if active else "idle", {"active_jobs": len(active)})
                if not did_claim:
                    self._stop_event.wait(self._settings.poll_interval_seconds)

    def stop(self) -> None:
        """外部からの停止要求を記録します。"""
        self._stop_event.set()

    def _process_claimed_response(self, response: dict[str, Any]) -> bool:
        """claim済みレスポンスをCodex実行まで進めます。"""
        job_payload = response.get("job")
        if not job_payload:
            return False

        job_id = int(job_payload["id"])
        LOGGER.info("claimed job #%s", job_id)
        try:
            request_text = str(job_payload.get("request_text", ""))
            if is_project_list_request(request_text):
                result_text = self._projects.format_project_list()
                self._client.complete(job_id, "succeeded", result_text)
                return True

            project = self._projects.select(job_payload.get("project_ref"))
            attachments = self._download_attachments(job_id, response.get("attachments") or [])
            codex_job = CodexJob(
                job_id=job_id,
                source_key=str(job_payload.get("source_key", "")),
                request_text=request_text,
                project=project,
                recent_messages=tuple(response.get("recent_messages") or ()),
                knowledge=tuple(response.get("knowledge") or ()),
                attachments=tuple(attachments),
            )
            result = self._runner.run(codex_job)
            self._client.complete(job_id, "succeeded" if result.ok else "failed", result.text)
            return True
        except Exception as exc:
            LOGGER.exception("job #%s failed", job_id)
            self._client.complete(job_id, "failed", "内部処理を完了できませんでした。", str(exc))
            return True

    def _download_attachments(self, job_id: int, attachments: list[dict[str, Any]]) -> list[Path]:
        """ジョブ添付をローカル一時領域へ取得します。"""
        saved: list[Path] = []
        for item in attachments:
            attachment_id = int(item["id"])
            file_name = _safe_file_name(str(item.get("file_name") or f"attachment-{attachment_id}"))
            destination_dir = self._settings.attachment_download_dir / f"job-{job_id}"
            if item.get("storage_status") == "stored":
                destination = destination_dir / f"{attachment_id}-{file_name}"
                saved.append(self._client.download_attachment(attachment_id, destination).resolve())
                continue
            if item.get("storage_status") == "external":
                provider = (item.get("metadata") or {}).get("contentProvider") or {}
                external_url = provider.get("originalContentUrl") or provider.get("previewImageUrl")
                if external_url:
                    destination_dir.mkdir(parents=True, exist_ok=True)
                    note = destination_dir / f"{attachment_id}-{file_name}.url.txt"
                    note.write_text(str(external_url), encoding="utf-8")
                    saved.append(note.resolve())
        return saved


def build_worker(settings: Settings) -> LineWorker:
    """設定から具象サービスを組み立てます。"""
    client = ApiClient(settings.api_base_url, settings.worker_token, settings.worker_id)
    projects = ProjectCatalog.from_config(
        settings.codex_projects_json,
        settings.codex_projects_file,
        settings.codex_allowed_project_roots,
    )
    runner = CodexRunner(
        settings.codex_command,
        settings.codex_command_timeout_seconds,
        settings.codex_no_project_workdir,
        settings.codex_reply_max_chars,
    )
    return LineWorker(settings, client, runner, projects)


def _safe_file_name(file_name: str) -> str:
    """VPS側とは別にWindows保存時もファイル名を安全化します。"""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", file_name).strip(" .")
    return clean[:180] or "attachment.bin"
