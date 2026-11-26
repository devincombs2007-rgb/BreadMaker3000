"""Runs in the LumiBot environment (CI job `lumibot-smoke`):
    BREADMAKER_DATA_DIR=/tmp/x pytest engines/lumibot/tests/test_fill_booking.py
Uses real lumibot Order/Asset/Position objects; the strategy instance is built without a broker."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "engines" / "lumibot")]

from lumibot.entities import Asset, Order  # noqa: E402

from bread_lumibot.strategy import ENGINE, BreadETFRotation  # noqa: E402
from breadmaker.config import RiskLimits  # noqa: E402
from breadmaker.db import connect  # noqa: E402
from breadmaker.ledger import Ledger  # noqa: E402
from breadmaker.risk import KillSwitch, RiskEngine  # noqa: E402


@pytest.fixture
def strat(tmp_path):
    s = BreadETFRotation.__new__(BreadETFRotation)  # bypass broker wiring
    conn = connect(tmp_path / "db.sqlite")
    s.ledger = Ledger(conn)
    s.ledger.ensure_portfolio(ENGINE, "etf", 1000)
    s.risk = RiskEngine(RiskLimits(), KillSwitch(tmp_path / "KILL_SWITCH"), conn)
    s.__dict__["_bm_now"] = datetime(2026, 3, 10, 15, tzinfo=timezone.utc)
    s._now_utc = lambda: s.__dict__["_bm_now"]
    type(s).is_backtesting = property(lambda self: True)
    s.__dict__["parameters"] = {"universe": ["SPY", "QQQ"]}
    return s


def order(qty):
    o = Order("BreadETFRotation", Asset("SPY"), qty, "buy")
    o.custom_params = {"bm_coid": "bm-lb-test"}
    return o


def test_partial_then_final_fill_booked_exactly_once(strat):
    o = order(0.3)
    o.add_transaction(100.0, 0.1)
    strat.on_partially_filled_order(None, o, 100.0, 0.1, 1)
    o.add_transaction(101.0, 0.2)
    strat.on_filled_order(None, o, 101.0, 0.2, 1)         # callback qty is the last chunk
    strat.on_filled_order(None, o, 101.0, 0.3, 1)         # duplicate / cumulative callback
    pos = strat.ledger.positions(ENGINE)["SPY"]
    assert pos.qty == pytest.approx(0.3)
    assert pos.avg_cost == pytest.approx((0.1 * 100 + 0.2 * 101) / 0.3)
    assert len(strat.ledger.fills(ENGINE)) == 2


def test_fill_without_transactions_falls_back_once(strat):
    o = order(0.2)
    strat.on_filled_order(None, o, 100.0, 0.2, 1)
    strat.on_filled_order(None, o, 100.0, 0.2, 1)
    assert strat.ledger.positions(ENGINE)["SPY"].qty == pytest.approx(0.2)


def test_reconciliation_mismatch_halts_entries(strat):
    o = order(0.2)
    o.add_transaction(100.0, 0.2)
    strat.on_filled_order(None, o, 100.0, 0.2, 1)
    pos = lambda q: SimpleNamespace(asset=SimpleNamespace(symbol="SPY"), quantity=q)  # noqa: E731
    strat.get_orders = lambda: []
    strat.get_positions = lambda: [pos(0.2)]
    assert strat._reconcile() is True
    strat.get_positions = lambda: [pos(0.5)]               # broker holds more than our book
    assert strat._reconcile() is False
    assert strat.risk.halts.active(ENGINE, strat._now_utc())["kind"] == "reconciliation"
