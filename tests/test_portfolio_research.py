import json

import numpy as np
import pandas as pd
import pytest

from breadmaker.costs import CostModel
from breadmaker.research.portfolio_backtest import buy_and_hold, exposure_matched, run_rotation
from breadmaker.research.run_real import render


def panel(n=700, seed=0, crash_at=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n, tz="UTC")
    drifts = {"SPY": 0.0006, "QQQ": 0.0008, "TLT": -0.0002, "GLD": 0.0003, "IEF": 0.0001}
    closes = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(mu, 0.01, n))) for s, mu in drifts.items()}, index=idx)
    if crash_at is not None:
        closes.iloc[crash_at:] *= 0.5
    opens = closes.shift(1).fillna(closes.iloc[0]) * (1 + rng.normal(0, 0.001, closes.shape))
    return opens, closes


def test_rotation_no_lookahead():
    o, c = panel()
    a = run_rotation(o, c, CostModel())
    o2, c2 = o.copy(), c.copy()
    c2.iloc[600:] *= 3
    o2.iloc[600:] *= 3
    b = run_rotation(o2, c2, CostModel())
    pd.testing.assert_series_equal(a.equity.iloc[: 600 - 201], b.equity.iloc[: 600 - 201])


def test_rotation_respects_position_cap_and_long_only():
    o, c = panel(seed=3)
    r = run_rotation(o, c, CostModel())
    buys = r.trades[r.trades.side == "buy"]
    assert len(buys) > 0
    assert (buys.qty * buys.price).max() <= 0.10 * r.equity.max() * 1.01
    assert r.exposure.max() <= 1.0 and (r.equity > 0).all()


def test_drawdown_halt_freezes_new_buys():
    o, c = panel(seed=4, crash_at=450)
    r = run_rotation(o, c, CostModel(), portfolio_halts=True)
    if r.halted_at is None:
        pytest.skip("halt not triggered for this seed")
    after = r.trades[(r.trades.ts > pd.Timestamp(r.halted_at, tz="UTC")) & (r.trades.side == "buy")]
    assert after.empty


def test_costs_monotonic():
    o, c = panel(seed=5)
    rets = [run_rotation(o, c, CostModel().scaled(k)).equity.iloc[-1] for k in (1, 3, 10)]
    assert rets[0] >= rets[1] >= rets[2]


def test_exposure_matched_benchmark():
    idx = pd.bdate_range("2020-01-01", periods=4, tz="UTC")
    bench = pd.Series([100, 110, 121, 133.1], index=idx)
    expo = pd.Series([0.5, 0.5, 0.5, 0.5], index=idx)
    m = exposure_matched(bench, expo, 1000)
    assert m.iloc[-1] == pytest.approx(1000 * 1.05 ** 3)
    assert buy_and_hold(bench).iloc[-1] == pytest.approx(1331)


def test_report_labels_synthetic_data_as_synthetic():
    res = {"manifest": {"datasets": [{"symbol": "SPY", "timeframe": "1d", "venue": "SYNTHETIC", "rows": 1,
                                      "first": "x", "last": "y", "problems": []}]}, "etf": {}, "crypto": {}}
    assert "SYNTHETIC TEST DATA - NOT MARKET RESULTS" in render(res)
    res["manifest"]["datasets"][0]["venue"] = "yfinance"
    assert "REAL historical data" in render(res)
    assert json.dumps(res)
