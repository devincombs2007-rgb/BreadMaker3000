"""Freqtrade risk sentinel + ledger mirror.

Runs outside Freqtrade, so a misbehaving strategy cannot disable it:
* verifies the running bot is in dry-run (else stop entries + kill switch),
* mirrors closed/open trade fills into the BreadMaker ledger,
* computes daily loss and drawdown from the virtual wallet and, on breach,
  engages the kill switch and calls /stopentry (exits keep working).
"""
from __future__ import annotations

import sqlite3

from ..ledger import Ledger
from ..risk import PortfolioState, RiskEngine
from .heartbeat import beat

ENGINE = "freqtrade"


def mirror_trades(ledger: Ledger, trades: list[dict]) -> int:
    """Insert entry/exit fills for Freqtrade trades. Idempotent by trade id."""
    events = []
    seen = set()
    for t in trades:
        tid = t["trade_id"]
        if tid in seen:
            continue
        seen.add(tid)
        amount = float(t.get("amount") or 0)
        if amount <= 0:
            continue
        open_fee = float(t.get("fee_open_cost") or 0) or float(t.get("fee_open") or 0) * amount * float(t["open_rate"])
        events.append((t.get("open_timestamp", 0), 0, t["pair"], "buy", amount, float(t["open_rate"]), open_fee,
                       f"ft-{tid}-entry", t.get("open_date")))
        if not t.get("is_open", True) and t.get("close_rate"):
            close_fee = float(t.get("fee_close_cost") or 0) or float(t.get("fee_close") or 0) * amount * float(t["close_rate"])
            events.append((t.get("close_timestamp", 0), 1, t["pair"], "sell", amount, float(t["close_rate"]),
                           close_fee, f"ft-{tid}-exit", t.get("close_date")))
    n = 0
    # Chronological replay; at equal timestamps exits go first so freed cash is available to entries.
    for ts, kind, pair, side, amount, price, fee, coid, ts_str in sorted(events, key=lambda e: (e[0], -e[1])):
        if ledger.record_fill(ENGINE, pair, side, amount, price, fee, coid, ts=ts_str, source="dryrun"):
            n += 1
    return n


def run_once(client, conn: sqlite3.Connection, risk: RiskEngine, state: dict) -> dict:
    """One sentinel cycle. `state` carries peak/day_start between cycles (persisted by caller)."""
    ledger = Ledger(conn)
    if not client.ping():
        beat(conn, "freqtrade", "down", "API ping failed")
        risk.log(ENGINE, "warning", "engine_unreachable", "Freqtrade API ping failed")
        return {"ok": False}

    cfg = client.show_config()
    if cfg.get("dry_run") is not True or cfg.get("trading_mode", "spot") != "spot":
        risk.kill_switch.engage("[freqtrade] bot not in spot dry-run mode")
        client.stopentry()
        risk.log(ENGINE, "critical", "live_mode_detected", "Freqtrade not in spot dry-run; entries stopped")
        return {"ok": False, "fatal": True}

    trades = client.trades(limit=500).get("trades", [])
    open_trades = client.status()
    mirror_trades(ledger, trades + open_trades)

    marks = {t["pair"]: float(t["current_rate"]) for t in open_trades if t.get("current_rate")}
    s = ledger.summary(ENGINE, marks)
    ledger.snapshot(ENGINE, s["equity"], s["cash"])
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("day") != today:
        state["day"], state["day_start"] = today, s["equity"]
    state["peak"] = max(state.get("peak") or s["equity"], s["equity"])
    ps = PortfolioState(s["equity"], s["cash"], state["day_start"], state["peak"])
    breaches = risk.check_portfolio(ENGINE, ps)
    if breaches or risk.kill_switch.engaged():
        if cfg.get("state") != "stopped":
            try:
                client.stopentry()
            except Exception:  # noqa: BLE001 - best effort; strategy also checks kill switch file
                pass
    beat(conn, "freqtrade", "halted" if breaches else "running", "; ".join(breaches))
    return {"ok": True, "equity": s["equity"], "breaches": breaches, "open_trades": len(open_trades)}
