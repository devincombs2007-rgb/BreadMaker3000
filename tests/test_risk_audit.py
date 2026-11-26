"""Risk-enforcement audit tests (see RISK_AUDIT.md)."""
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest

from breadmaker.config import RiskLimits
from breadmaker.ledger import Ledger
from breadmaker.risk import (EngineHalts, KillSwitch, OrderIntent, PortfolioState, RiskEngine, request_peak_reset,
                             update_equity_state, write_monitor_heartbeat)
from conftest import ROOT

NOW = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "bread_risk_gate", ROOT / "engines/freqtrade/user_data/strategies/bread_risk_gate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.entry_allowed


# ---- Freqtrade fail-closed gate ---------------------------------------------------
def test_gate_fail_closed_matrix(tmp_path):
    gate = load_gate()
    assert gate(tmp_path / "missing", "freqtrade", NOW)[0] is False           # mount missing
    assert gate(tmp_path, "freqtrade", NOW) == (False, "risk monitor has not confirmed this engine yet")
    write_monitor_heartbeat(tmp_path, "freqtrade", NOW - timedelta(seconds=30))
    assert gate(tmp_path, "freqtrade", NOW) == (True, "ok")
    assert gate(tmp_path, "freqtrade", NOW + timedelta(minutes=10))[0] is False  # monitor stale
    assert gate(tmp_path, "freqai_shadow", NOW)[0] is False                    # heartbeat is per engine
    EngineHalts(tmp_path).halt("freqtrade", "daily_loss", "x", NOW, NOW + timedelta(hours=1))
    assert gate(tmp_path, "freqtrade", NOW)[0] is False
    assert gate(tmp_path, "freqtrade", NOW + timedelta(hours=2) - timedelta(minutes=119))[0] is False
    (tmp_path / "HALT_freqtrade.json").write_text("{not json")
    ok, why = gate(tmp_path, "freqtrade", NOW)
    assert ok is False and "fail closed" in why                                # corrupt -> blocked
    (tmp_path / "HALT_freqtrade.json").unlink()
    KillSwitch(tmp_path / "KILL_SWITCH").engage("t")
    assert gate(tmp_path, "freqtrade", NOW)[0] is False


@pytest.mark.parametrize("bad", [None, 123, "\x00bad", object()])
def test_gate_never_raises(bad):
    ok, _ = load_gate()(bad, "freqtrade", NOW)
    assert ok is False


# ---- per-engine halts --------------------------------------------------------------
@pytest.fixture
def risk(conn, tmp_path):
    return RiskEngine(RiskLimits(), KillSwitch(tmp_path / "KILL_SWITCH"), conn)


def st(equity, day_start=1000.0, peak=1000.0):
    return PortfolioState(equity=equity, cash=equity, day_start_equity=day_start, peak_equity=peak)


def buy(coid, ts=NOW):
    return OrderIntent("lumibot", "SPY", "buy", 0.1, 500.0, coid, ts, 475.0)


def test_daily_loss_halt_persists_then_expires_at_midnight(risk):
    assert risk.check_portfolio("lumibot", st(989), NOW)
    # equity recovers intraday, but the halt persists for the rest of the UTC day
    d = risk.evaluate(buy("a"), st(1000), NOW + timedelta(hours=1))
    assert not d.approved and any("halted (daily_loss)" in r for r in d.reasons)
    assert risk.evaluate(buy("b", NOW + timedelta(days=1)), st(1000), NOW + timedelta(days=1)).approved


def test_drawdown_halt_is_per_engine_and_permanent(risk):
    risk.check_portfolio("freqtrade", st(949, 950, 1000), NOW)
    assert risk.halts.active("freqtrade", NOW + timedelta(days=30))["kind"] == "drawdown"
    assert not risk.kill_switch.engaged()                     # other engines keep trading
    assert risk.evaluate(buy("c"), st(1000), NOW).approved    # lumibot unaffected


def test_daily_halt_cannot_downgrade_drawdown(risk):
    risk.halts.halt("x", "drawdown", "dd", NOW, None)
    risk.halts.halt("x", "daily_loss", "d", NOW, NOW + timedelta(hours=1))
    assert risk.halts.active("x", NOW + timedelta(days=2))["kind"] == "drawdown"


def test_halt_events_logged_once_not_every_cycle(risk):
    for _ in range(5):  # -1.05% on the day AND -6% from peak
        risk.check_portfolio("freqtrade", st(940, 950, 1000), NOW)
    assert sum(1 for e in risk.recent_events() if e["rule"] == "portfolio_limit") == 2  # daily + drawdown once


def test_peak_reset_after_owner_release(tmp_path):
    state = {}
    update_equity_state(state, 1000, NOW, tmp_path)
    update_equity_state(state, 940, NOW, tmp_path)
    assert state["peak"] == 1000
    request_peak_reset(tmp_path, NOW)
    update_equity_state(state, 940, NOW, tmp_path)
    assert state["peak"] == 940 and state["day_start"] == 940
    update_equity_state(state, 930, NOW, tmp_path)            # reset applied only once
    assert state["peak"] == 940


# ---- sentinel: heartbeat only on success, shadow engine monitored -----------------------
class FT:
    def __init__(self, up=True, rate=100.0):
        self.up, self.rate, self.stopped = up, rate, 0

    def ping(self):
        return self.up

    def show_config(self):
        return {"dry_run": True, "trading_mode": "spot", "state": "running"}

    def trades(self, limit=500, offset=0):
        return {"trades": []}

    def status(self):
        return []

    def stopentry(self):
        self.stopped += 1


def test_sentinel_heartbeat_only_when_monitoring_succeeds(conn, risk, tmp_path):
    from breadmaker.orchestrator.freqtrade_sentinel import run_once
    Ledger(conn).ensure_portfolio("freqai_shadow", "crypto", 1000)
    gate = load_gate()
    state = {}
    run_once(FT(up=False), conn, risk, state, engine="freqai_shadow", now=NOW)
    assert not (tmp_path / "MONITOR_OK_freqai_shadow").exists()
    assert gate(tmp_path, "freqai_shadow", NOW)[0] is False
    run_once(FT(up=False), conn, risk, state, engine="freqai_shadow", now=NOW)
    assert sum(e["rule"] == "engine_unreachable" for e in risk.recent_events()) == 1  # transition only
    r = run_once(FT(), conn, risk, state, engine="freqai_shadow", now=NOW)
    assert r["ok"] and gate(tmp_path, "freqai_shadow", NOW)[0] is True


def test_sentinel_drawdown_writes_halt_and_stops_entries(conn, risk, tmp_path):
    from breadmaker.orchestrator.freqtrade_sentinel import run_once
    Ledger(conn).ensure_portfolio("freqtrade", "crypto", 1000)
    ft = FT()
    r = run_once(ft, conn, risk, {"peak": 1100.0, "day": NOW.date().isoformat(), "day_start": 1000.0},
                 engine="freqtrade", now=NOW)
    assert r["halted"] and ft.stopped == 1
    assert load_gate()(tmp_path, "freqtrade", NOW)[0] is False


def test_sentinel_paginates_trade_history(conn, risk):
    from breadmaker.orchestrator.freqtrade_sentinel import run_once
    Ledger(conn).ensure_portfolio("freqtrade", "crypto", 1_000_000)

    class Many(FT):
        def trades(self, limit=500, offset=0):
            ids = range(offset, min(offset + limit, 1200))
            return {"trades": [{"trade_id": i, "pair": "BTC/USD", "amount": 0.001, "open_rate": 100.0,
                                "is_open": False, "close_rate": 100.0, "open_timestamp": 2 * i,
                                "close_timestamp": 2 * i + 1} for i in ids]}
    run_once(Many(), conn, risk, {}, engine="freqtrade", now=NOW)
    assert len(Ledger(conn).fills("freqtrade", limit=10000)) == 2400


# ---- LEAN portfolio gate -----------------------------------------------------------------
def test_lean_risk_gate():
    from test_lean_logic import load_lean_module
    g = load_lean_module().risk_gate
    assert g(1000, 1000, 1000) == "ok"
    assert g(989, 1000, 1000) == "halt_entries"
    assert g(949, 960, 1000) == "liquidate_and_halt"
