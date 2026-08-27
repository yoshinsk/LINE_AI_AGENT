r"""<PROJECT_ROOT>\tests\test_worker_resilience.py

内部APIの一時障害でWindows常駐ワーカーが停止しないための耐性を検証します。
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import line_ai_agent.worker as worker_module
from line_ai_agent.worker import LineWorker


class _FailingClient:
    """claimとheartbeatの障害を再現する最小クライアントです。"""

    worker_id = "test-worker"

    def claim(self, lease_seconds: int) -> dict:
        raise RuntimeError("temporary claim failure")

    def heartbeat(self, status_text: str, metadata: dict | None = None) -> dict:
        raise RuntimeError("temporary heartbeat failure")


class _FlakyCompleteClient:
    """completeの一時失敗後に成功する挙動を再現します。"""

    worker_id = "test-worker"

    def __init__(self) -> None:
        self.complete_calls = 0

    def complete(
        self,
        job_id: int,
        status: str,
        result_text: str,
        error_text: str = "",
        assets: list[dict] | None = None,
    ) -> dict:
        self.complete_calls += 1
        if self.complete_calls == 1:
            raise RuntimeError("temporary complete failure")
        return {"ok": True, "delivery": {"accepted": True}}


class WorkerResilienceTest(unittest.TestCase):
    """一時的な内部API障害をプロセス停止へ波及させないことを確認します。"""

    def test_claim_failure_returns_none_without_raising(self) -> None:
        worker = LineWorker(_settings(), _FailingClient(), None, None)

        with self.assertLogs("line_ai_agent.worker", level="ERROR"):
            response = worker._claim_next_job(60)

        self.assertIsNone(response)

    def test_heartbeat_failure_does_not_raise(self) -> None:
        worker = LineWorker(_settings(), _FailingClient(), None, None)

        with self.assertLogs("line_ai_agent.worker", level="ERROR"):
            worker._send_heartbeat("idle", {"active_jobs": 0})

    def test_complete_retries_transient_failure(self) -> None:
        client = _FlakyCompleteClient()
        worker = LineWorker(_settings(), client, None, None)
        original_delays = worker_module._COMPLETE_RETRY_DELAYS_SECONDS
        worker_module._COMPLETE_RETRY_DELAYS_SECONDS = (0.0,)
        try:
            with self.assertLogs("line_ai_agent.worker", level="WARNING"):
                completion = worker._complete_job(42, "succeeded", "ok")
        finally:
            worker_module._COMPLETE_RETRY_DELAYS_SECONDS = original_delays

        self.assertEqual({"ok": True, "delivery": {"accepted": True}}, completion)
        self.assertEqual(2, client.complete_calls)

    def test_logs_accepted_delivery(self) -> None:
        with self.assertLogs("line_ai_agent.worker", level="INFO") as captured:
            LineWorker._log_delivery_result(42, {"delivery": {"accepted": True, "status_code": 200, "attempt_count": 1}})

        self.assertIn("job #42 delivery accepted status=200 attempts=1", captured.output[0])


def _settings() -> SimpleNamespace:
    """ワーカー耐性テストに必要な設定だけを持つオブジェクトを返します。"""
    return SimpleNamespace(poll_interval_seconds=1)


if __name__ == "__main__":
    unittest.main()
