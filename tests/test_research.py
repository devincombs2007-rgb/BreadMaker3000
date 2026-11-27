import numpy as np
import pandas as pd
import pytest

from breadmaker.backtest import holdout_split, run_backtest, walk_forward_splits
from breadmaker.costs import CostModel
from breadmaker.data import synthetic_ohlcv, validate_ohlcv
from breadmaker.metrics import deflated_sharpe_ratio, max_drawdown, sharpe, trade_stats
from breadmaker.research.experiments import Lab
from breadmaker.strategies.baselines import REGISTRY, sma_crossover


@pytest.fixture(scope="module")
def df():
    return synthetic_ohlcv(n=3000, freq="1h", seed=3)


def test_synthetic_data_is_clean(df):
    assert validate_ohlcv(df, pd.Timedelta("1h")) == []


def test_validate_detects_bad_data(df):
    bad = df.copy()
    bad.iloc[5, bad.columns.get_loc("high")] = 0.0001
    probs = validate_ohlcv(bad)
    assert any(p.startswith("high/low inconsistent with open/close: 1 rows") for p in probs)


def test_validate_ignores_float_rounding_from_adjustment(df):
    rounded = df.copy()
    rounded["high"] = rounded[["open", "close"]].max(axis=1) * (1 - 1e-12)  # vendor rounding noise
    assert not any("high/low" in p for p in validate_ohlcv(rounded))


def test_no_lookahead_future_change_does_not_alter_past(df):
    """Perturbing data after bar k must not change the equity curve up to bar k."""
    k = 2000
    a = run_backtest(df, sma_crossover(df), CostModel(), periods_per_year=8760)
    df2 = df.copy()
    df2.iloc[k + 1:, :4] *= 1.5
    b = run_backtest(df2, sma_crossover(df2), CostModel(), periods_per_year=8760)
    pd.testing.assert_series_equal(a.equity.iloc[:k + 1], b.equity.iloc[:k + 1])


def test_execution_is_next_bar_open():
    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    d = pd.DataFrame({"open": [10, 20, 30, 40], "high": [11, 21, 31, 41], "low": [9, 19, 29, 39],
                      "close": [10, 20, 30, 40], "volume": 1.0}, index=idx)
    res = run_backtest(d, pd.Series([1, 1, 1, 1], index=idx), CostModel(0, 0, 0), 100, 252)
    assert res.trades.iloc[0]["price"] == 20  # signal at bar0 close executes at bar1 open
    assert res.equity.iloc[0] == 100


def test_costs_reduce_returns(df):
    sig = sma_crossover(df)
    free = run_backtest(df, sig, CostModel(0, 0, 0), periods_per_year=8760)
    costly = run_backtest(df, sig, CostModel(40, 10, 10), periods_per_year=8760)
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]
    assert costly.metrics["costs_paid"] > 0


def test_long_only_no_leverage(df):
    res = run_backtest(df, pd.Series(5.0, index=df.index), CostModel(), periods_per_year=8760)
    assert res.exposure.max() <= 1.0 + 1e-9 and (res.equity > 0).all()


def test_walk_forward_purge_gap():
    splits = walk_forward_splits(2000, n_splits=4, min_train=500, gap=12)
    assert len(splits) == 4
    for tr, te in splits:
        assert tr.max() + 12 < te.min()
    assert splits[0][1].max() < splits[1][1].min()


def test_holdout_chronological(df):
    a, b = holdout_split(df, 0.25)
    assert a.index.max() < b.index.min() and len(b) == 750


def test_metrics_basic():
    eq = pd.Series([100, 110, 99, 120.0])
    assert max_drawdown(eq) == pytest.approx(99 / 110 - 1)
    st = trade_stats([10, -5, 5])
    assert st["win_rate"] == pytest.approx(2 / 3) and st["profit_factor"] == pytest.approx(3.0)
    assert np.isnan(sharpe(pd.Series([0.01]), 252))


def test_dsr_penalises_many_trials():
    r = pd.Series(np.random.default_rng(0).normal(0.0005, 0.01, 1000))
    assert deflated_sharpe_ratio(r, 1, 0.0) > deflated_sharpe_ratio(r, 1000, 0.002)


def test_all_baselines_bounded(df):
    for name, fn in REGISTRY.items():
        s = fn(df)
        assert s.between(0, 1).all(), name
        assert len(s) == len(df)


def test_lab_records_every_trial(conn, df):
    lab = Lab(conn)
    out = lab.grid_search("t", "sma_crossover", {"fast": [10, 20], "slow": [100]}, df, CostModel(), 8760)
    assert lab.n_trials("t") == 2 and len(out) == 2
    assert {"accepted", "dsr", "failed_checks"}.issubset(out.columns)
