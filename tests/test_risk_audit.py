"""Risk-enforcement audit tests (see RISK_AUDIT.md)."""
from datetime import datetime, timedelta, timezone

import pytest

from breadmaker.config import RiskLimits
from breadmaker.risk import (KillSwitch, OrderIntent, PortfolioState, RiskEngine, request_peak_reset,
                             update_equity_state)

NOW = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)


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


