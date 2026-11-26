"""Freqtrade risk sentinel + ledger mirror.

Runs outside Freqtrade, so a misbehaving strategy cannot disable it:
* verifies the running bot is in dry-run (else stop entries + kill switch),
* mirrors closed/open trade fills into the BreadMaker ledger,
* computes daily loss and drawdown from the virtual wallet and, on breach,
  writes a per-engine halt (read by the strategy's fail-closed gate) and calls
  /stopentry (exits keep working),
* writes MONITOR_OK_<engine> after each successful cycle; without it the bot cannot enter.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..ledger import Ledger
from ..risk import PortfolioState, RiskEngine, update_equity_state, write_monitor_heartbeat
from .heartbeat import beat

ENGINE = "freqtrade"


def mirror_trades(ledger: Ledger, trades: list[dict], engine: str = ENGINE) -> int:
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
        if ledger.record_fill(engine, pair, side, amount, price, fee, coid, ts=ts_str, source="dryrun"):
            n += 1
    return n


def run_once(client, conn: sqlite3.Connection, risk: RiskEngine, state: dict, engine: str = ENGINE,
             now: datetime | None = None) -> dict:
    """One sentinel cycle for one Freqtrade instance. `state` (day start / peak) is persisted by caller.

    The MONITOR_OK_<engine> heartbeat is written only after a fully successful check. If this
    function fails or the bot is unreachable, the heartbeat goes stale and the strategy's fail-closed
    gate blocks new entries within MAX_MONITOR_AGE_SECONDS.
    """
    now = now or datetime.now(timezone.utc)
    ledger = Ledger(conn)
    shared = risk.kill_switch.path.parent
    if not client.ping():
        beat(conn, engine, "down", "API ping failed")
        if not state.get("unreachable"):  # log transitions only, not every cycle
            risk.log(engine, "warning", "engine_unreachable", "Freqtrade API ping failed")
        state["unreachable"] = True
        return {"ok": False}
    if state.pop("unreachable", False):
        risk.log(engine, "info", "engine_reachable", "Freqtrade API reachable again")

    cfg = client.show_config()
    if cfg.get("dry_run") is not True or cfg.get("trading_mode", "spot") != "spot":
        risk.kill_switch.engage(f"[{engine}] bot not in spot dry-run mode")
        client.stopentry()
        risk.log(engine, "critical", "live_mode_detected", "Freqtrade not in spot dry-run; entries stopped")
        return {"ok": False, "fatal": True}

    trades, offset = [], 0
    while True:  # paginate: never silently drop history beyond one page
        page = client.trades(limit=500, offset=offset).get("trades", [])
        trades.extend(page)
        if len(page) < 500:
            break
        offset += 500
    open_trades = client.status()
    mirror_trades(ledger, trades + open_trades, engine)

    marks = {t["pair"]: float(t["current_rate"]) for t in open_trades if t.get("current_rate")}
    s = ledger.summary(engine, marks)
    ledger.snapshot(engine, s["equity"], s["cash"], ts=now.isoformat(timespec="seconds"))
    update_equity_state(state, s["equity"], now, shared)
    ps = PortfolioState(s["equity"], s["cash"], state["day_start"], state["peak"])
    breaches = risk.check_portfolio(engine, ps, now)
    halted = bool(risk.halts.active(engine, now)) or risk.kill_switch.engaged()
    if halted:
        try:
            client.stopentry()  # belt and braces; the strategy gate already refuses entries
        except Exception:  # noqa: BLE001
            pass
    write_monitor_heartbeat(shared, engine, now)
    beat(conn, engine, "halted" if halted else "running", "; ".join(breaches))
    return {"ok": True, "equity": s["equity"], "breaches": breaches, "halted": halted,
            "open_trades": len(open_trades)}
