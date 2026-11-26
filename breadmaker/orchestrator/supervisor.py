"""Central orchestration loop (runs in the `core` container).

* initialises the three isolated portfolios
* runs the Freqtrade sentinel every cycle
* watches worker heartbeats and resource usage
* writes daily/weekly reports
* bounded, resource-aware research jobs are run via `breadmaker research` on a
  schedule (see docker-compose) - never continuously
Restart-safe: all state lives in SQLite / files on the persistent volume.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import psutil

from ..config import load_settings
from ..db import connect
from ..ledger import Ledger
from ..reports.generate import write_report
from ..risk import KillSwitch, RiskEngine
from .heartbeat import beat, worker_status

log = logging.getLogger("breadmaker.supervisor")
_running = True


def _stop(*_):
    global _running
    _running = False


def main(interval: int = 60) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = load_settings()
    conn = connect(settings.db_path)
    ledger = Ledger(conn)
    for name, e in settings.engines.items():
        ledger.ensure_portfolio(name, e.asset_class, e.initial_capital)
    risk = RiskEngine(settings.risk, KillSwitch(settings.kill_switch_path), conn)
    state_path = settings.data_dir / "sentinel_state.json"
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    ft = None
    if os.environ.get("FREQTRADE_API_URL"):
        from ..integrations.freqtrade_client import FreqtradeClient
        ft = FreqtradeClient(os.environ["FREQTRADE_API_URL"], os.environ.get("FREQTRADE_API_USER", "breadmaker"),
                             os.environ.get("FREQTRADE__API_SERVER__PASSWORD", ""))
    report_dir = settings.data_dir / "reports"
    last_report_day = state.get("last_report_day")

    while _running:
        try:
            if ft is not None:
                from .freqtrade_sentinel import run_once
                res = run_once(ft, conn, risk, state.setdefault("freqtrade", {}))
                log.info("freqtrade sentinel: %s", res)
            mem = psutil.virtual_memory().percent
            cpu = psutil.cpu_percent(interval=None)
            disk = psutil.disk_usage(str(settings.data_dir)).percent
            beat(conn, "supervisor", "running", f"cpu={cpu:.0f}% mem={mem:.0f}% disk={disk:.0f}%")
            if mem > 92 or disk > 92:
                risk.log("system", "warning", "resources", f"mem={mem}% disk={disk}%")
            for w in worker_status(conn, settings.raw.get("schedule", {}).get("heartbeat_timeout_seconds", 300)):
                if w["status"] == "stale" and w["worker"] != "lumibot":  # lumibot sleeps outside market hours
                    risk.log(w["worker"], "warning", "heartbeat_stale", f"last seen {w['last_seen']}")
            now = datetime.now(timezone.utc)
            hhmm = settings.raw.get("schedule", {}).get("daily_report_utc", "21:30")
            if now.strftime("%H:%M") >= hhmm and last_report_day != now.date().isoformat():
                write_report(conn, report_dir, "daily")
                if now.weekday() == settings.raw.get("schedule", {}).get("weekly_report_weekday", 5):
                    write_report(conn, report_dir, "weekly")
                last_report_day = state["last_report_day"] = now.date().isoformat()
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            tmp.replace(state_path)
        except Exception as exc:  # keep supervising; failures are visible in risk events
            log.exception("supervisor cycle failed")
            try:
                risk.log("system", "warning", "supervisor_error", repr(exc)[:500])
            except Exception:  # noqa: BLE001
                pass
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)
    log.info("supervisor stopped")
