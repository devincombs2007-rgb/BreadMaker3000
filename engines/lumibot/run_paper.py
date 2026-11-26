"""Start the LumiBot ETF baseline on Alpaca PAPER trading.

Credentials come from environment variables (ALPACA_API_KEY / ALPACA_API_SECRET) or
from the owner's dashboard settings (stored on the private data volume). Never Git.
Refuses to start unless every paper-only safety check passes.
"""
from __future__ import annotations

import os
import sys
import time

from breadmaker.config import SafetyError, assert_alpaca_key_looks_paper, assert_alpaca_paper, load_settings
from breadmaker.db import connect
from breadmaker.orchestrator.heartbeat import beat
from breadmaker.secrets_store import SecretsStore


def wait_for_keys(store: SecretsStore) -> tuple[str, str]:
    """Idle (no crash loop) and report status until the owner adds paper keys."""
    conn = connect(load_settings().db_path)
    announced = False
    while True:
        creds = store.alpaca()
        if creds:
            return creds
        if not announced:
            print("Alpaca PAPER keys not set yet. Add them in the dashboard Settings. Waiting...", file=sys.stderr)
            announced = True
        beat(conn, "lumibot", "waiting", "awaiting Alpaca PAPER keys (dashboard > Settings)")
        time.sleep(60)


def main() -> int:
    settings = load_settings()
    store = SecretsStore(settings.data_dir)
    key, secret = wait_for_keys(store)
    try:
        assert_alpaca_paper(os.environ.get("ALPACA_BASE_URL"), os.environ.get("ALPACA_PAPER", "true"))
        assert_alpaca_key_looks_paper(key)
    except SafetyError as exc:
        print(f"SAFETY STOP: {exc}", file=sys.stderr)
        beat(connect(settings.db_path), "lumibot", "blocked", f"safety: {exc}")
        time.sleep(3600)  # do not crash-loop
        return 2

    from lumibot.brokers import Alpaca
    from lumibot.traders import Trader

    from bread_lumibot.strategy import BreadETFRotation

    broker = Alpaca({"API_KEY": key, "API_SECRET": secret, "PAPER": True})
    trader = Trader()
    trader.add_strategy(BreadETFRotation(broker=broker))
    trader.run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
