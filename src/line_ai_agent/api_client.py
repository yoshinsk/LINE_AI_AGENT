r"""<PROJECT_ROOT>\src\line_ai_agent\api_client.py

ワーカーから公開サーバの内部APIへアクセスする小さなHTTPクライアントです。
"""

from __future__ import annotations

from pathlib import Path
import base64
import hashlib
import json
import mimetypes
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiClient:
    """内部APIのclaim、complete、heartbeat、添付ダウンロードを担当します。"""

    def __init__(self, base_url: str, worker_token: str, worker_id: str) -> None:
        self._base_url = base_url
        self._worker_token = worker_token
        self._worker_id = worker_id

    @property
    def worker_id(self) -> str:
        """ログやheartbeatで使うワーカー識別子を返します。"""
        return self._worker_id

    def health(self) -> dict:
        """内部APIとDBの疎通を確認します。"""
        return self._post("health", {})

    def heartbeat(self, status_text: str, metadata: dict | None = None) -> dict:
        """ワーカーの生存状態を公開サーバへ記録します。"""
        return self._post("heartbeat", {"worker_id": self._worker_id, "status_text": status_text, "metadata": metadata or {}})

    def claim(self, lease_seconds: int) -> dict:
        """処理待ちジョブを1件取得します。"""
        return self._post("claim", {"worker_id": self._worker_id, "lease_seconds": lease_seconds})

    def complete(
        self,
        job_id: int,
        status: str,
        result_text: str,
        error_text: str = "",
        assets: list[dict] | None = None,
    ) -> dict:
        """ジョブ結果を公開サーバへ返し、LINE push送信まで進めます。"""
        return self._post(
            "complete",
            {
                "worker_id": self._worker_id,
                "job_id": job_id,
                "status": status,
                "result_text": result_text,
                "error_text": error_text,
                "assets": assets or [],
            },
        )

    def upload_result_asset(self, job_id: int, path: Path) -> dict:
        """Codexが生成した成果物ファイルを公開サーバへアップロードします。"""
        binary = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self._post(
            "result_asset",
            {
                "worker_id": self._worker_id,
                "job_id": job_id,
                "file_name": path.name,
                "content_type": content_type,
                "sha256": hashlib.sha256(binary).hexdigest(),
                "content_base64": base64.b64encode(binary).decode("ascii"),
            },
            timeout=180,
        )
        asset = response.get("asset")
        if not isinstance(asset, dict):
            raise RuntimeError("internal API returned invalid asset payload")
        return asset

    def download_attachment(self, attachment_id: int, destination: Path) -> Path:
        """添付ファイルを認証付き内部APIから保存します。"""
        destination.parent.mkdir(parents=True, exist_ok=True)
        query = urlencode({"action": "attachment", "id": str(attachment_id)})
        request = Request(
            f"{self._base_url}?{query}",
            headers={"X-Line-AI-Agent-Worker-Token": self._worker_token},
            method="GET",
        )
        with urlopen(request, timeout=120) as response:
            destination.write_bytes(response.read())
        return destination

    def _post(self, action: str, payload: dict, timeout: int = 60) -> dict:
        """JSON POSTを送信し、JSONレスポンスを辞書として返します。"""
        body = json.dumps({"action": action, **payload}, ensure_ascii=False).encode("utf-8")
        request = Request(
            self._base_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Line-AI-Agent-Worker-Token": self._worker_token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"internal API error {exc.code}: {detail}") from exc
        decoded = json.loads(raw or "{}")
        if not isinstance(decoded, dict):
            raise RuntimeError("internal API returned non-object JSON")
        if decoded.get("ok") is False:
            raise RuntimeError(f"internal API rejected request: {decoded}")
        return decoded
