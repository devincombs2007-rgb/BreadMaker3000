from datetime import datetime, timedelta, timezone

import pytest

from breadmaker.config import RiskLimits
from breadmaker.ledger import Ledger, LedgerError
from breadmaker.risk import KillSwitch, OrderIntent, PortfolioState, RiskEngine, size_position

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def risk(conn, tmp_path):
    return RiskEngine(RiskLimits(), KillSwitch(tmp_path / "KILL"), conn)


def state(**kw):
    base = dict(equity=1000, cash=1000, day_start_equity=1000, peak_equity=1000)
    base.update(kw)
    return PortfolioState(**base)


def buy(qty=1.0, price=50.0, stop=47.5, coid="a", ts=NOW, sym="BTC/USD"):
    return OrderIntent("freqtrade", sym, "buy", qty, price, coid, ts, stop)


def test_sizing_respects_risk_and_cap():
    L = RiskLimits()
    # 5% stop -> 0.5%/5% = 10% of equity = $100 -> 2 units at $50
    assert size_position(1000, 50, 47.5, L) == pytest.approx(2.0)
    # 1% stop would allow 50% by risk, but 10% cap binds
    assert size_position(1000, 50, 49.5, L) == pytest.approx(2.0)
    # 20% stop: risk binds -> $25 position
    assert size_position(1000, 50, 40, L) == pytest.approx(0.5)
    assert size_position(1000, 50, 55, L) == 0.0  # stop above price invalid


def test_approves_valid_order_and_blocks_duplicate(risk):
    assert risk.evaluate(buy(), state(), NOW).approved
    d = risk.evaluate(buy(), state(), NOW)
    assert not d.approved and "duplicate client_order_id" in d.reasons


def test_rejects_oversized_order(risk):
    d = risk.evaluate(buy(qty=3.0), state(), NOW)
    assert not d.approved and any("exceeds risk-sized max" in r for r in d.reasons)


def test_rejects_stale_and_naive_data(risk):
    assert not risk.evaluate(buy(coid="s", ts=NOW - timedelta(minutes=10)), state(), NOW).approved
    assert not risk.evaluate(buy(coid="n", ts=NOW.replace(tzinfo=None)), state(), NOW).approved


def test_requires_stop_and_no_leverage(risk):
    assert not risk.evaluate(buy(coid="x", stop=None), state(), NOW).approved
    d = risk.evaluate(buy(coid="y", qty=1.0), state(cash=10), NOW)
    assert not d.approved and any("insufficient cash" in r for r in d.reasons)


def test_no_shorting(risk):
    sell = OrderIntent("freqtrade", "BTC/USD", "sell", 1.0, 50.0, "s1", NOW)
    assert not risk.evaluate(sell, state(), NOW).approved
    assert risk.evaluate(sell, state(positions_qty={"BTC/USD": 1.0}), NOW).approved


def test_daily_loss_halts_entries(risk):
    d = risk.evaluate(buy(coid="d"), state(equity=989, day_start_equity=1000), NOW)
    assert not d.approved and any("daily_loss" in r for r in d.reasons)
    assert not risk.kill_switch.engaged()  # daily loss halts, drawdown kills


def test_drawdown_engages_kill_switch_but_exits_allowed(risk):
    d = risk.evaluate(buy(coid="dd"), state(equity=949, day_start_equity=950, peak_equity=1000), NOW)
    assert not d.approved and risk.kill_switch.engaged()
    assert not risk.evaluate(buy(coid="dd2"), state(), NOW).approved  # persists
    sell = OrderIntent("freqtrade", "BTC/USD", "sell", 1.0, 50.0, "exit", NOW)
    assert risk.evaluate(sell, state(positions_qty={"BTC/USD": 1.0}), NOW).approved
    risk.kill_switch.release()
    assert risk.evaluate(buy(coid="dd3"), state(), NOW).approved


def test_rejections_are_logged(risk):
    risk.evaluate(buy(coid="z", qty=99), state(), NOW)
    assert risk.recent_events()[0]["rule"] == "order_rejected"


def test_ledger_isolated_accounting(conn):
    L = Ledger(conn)
    for e in ("freqtrade", "lumibot", "lean"):
        L.ensure_portfolio(e, "x", 1000)
    assert L.record_fill("freqtrade", "BTC/USD", "buy", 2, 50, 0.4, "o1")
    assert not L.record_fill("freqtrade", "BTC/USD", "buy", 2, 50, 0.4, "o1")  # idempotent
    assert L.record_fill("freqtrade", "BTC/USD", "sell", 2, 55, 0.44, "o2")
    s = L.summary("freqtrade")
    assert s["cash"] == pytest.approx(1000 - 100.4 + 110 - 0.44)
    assert s["realized_pnl_net_of_fees"] == pytest.approx(10 - 0.84)
    assert L.closed_trade_pnls("freqtrade") == [pytest.approx(110 - 0.44 - 100.4)]
    assert L.summary("lumibot")["equity"] == 1000  # isolated


def test_ledger_blocks_shorts_and_leverage(conn):
    L = Ledger(conn)
    L.ensure_portfolio("lumibot", "etf", 1000)
    with pytest.raises(LedgerError):
        L.record_fill("lumibot", "SPY", "sell", 1, 500, 0, "s")
    with pytest.raises(LedgerError):
        L.record_fill("lumibot", "SPY", "buy", 3, 500, 0, "b")


def test_mark_to_market(conn):
    L = Ledger(conn)
    L.ensure_portfolio("lean", "x", 1000)
    L.record_fill("lean", "SPY", "buy", 0.2, 500, 0, "b")
    s = L.summary("lean", {"SPY": 550})
    assert s["equity"] == pytest.approx(1010) and s["unrealized_pnl"] == pytest.approx(10)
    assert L.summary("lean")["unmarked_symbols"] == ["SPY"]
