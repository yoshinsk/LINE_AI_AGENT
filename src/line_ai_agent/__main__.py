r"""<PROJECT_ROOT>\src\line_ai_agent\__main__.py

LINE AI Agentワーカーのコマンドライン入口です。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from .api_client import ApiClient
from .config import DEFAULT_ENV_FILE, Settings
from .worker import build_worker


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈してhealth、once、serveを実行します。"""
    parser = argparse.ArgumentParser(prog="line-ai-agent")
    parser.add_argument("--env", type=Path, default=None, help="Path to a worker .env file")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Check the public server internal API")
    subparsers.add_parser("once", help="Claim and process one queued job")
    subparsers.add_parser("serve", help="Run the worker loop")

    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    try:
        env_file = args.env or (DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.exists() else None)
        settings = Settings.from_env_file(env_file)
        if args.command == "health":
            client = ApiClient(settings.api_base_url, settings.worker_token, settings.worker_id)
            print(client.health())
            return 0
        worker = build_worker(settings)
        if args.command == "once":
            print(f"processed={worker.run_once()}")
            return 0
        if args.command == "serve":
            worker.serve_forever()
            return 0
        parser.error(f"unknown command: {args.command}")
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
