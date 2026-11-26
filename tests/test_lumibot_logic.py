import numpy as np
import pandas as pd
import pytest

from bread_lumibot.logic import Params, Target, plan_orders, select_targets


def closes(n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    up = 100 * np.exp(np.cumsum(rng.normal(0.003, 0.005, n)))
    dn = 100 * np.exp(np.cumsum(rng.normal(-0.003, 0.005, n)))
    flat = 100 * np.exp(np.cumsum(rng.normal(0.0015, 0.005, n)))
    return pd.DataFrame({"UP": up, "DOWN": dn, "FLAT": flat}, index=idx)


def test_selects_only_trending_assets_with_risk_caps():
    t = select_targets(closes(), 1000, 0.10, 0.005, Params(top_n=3))
    syms = [x.symbol for x in t]
    assert "DOWN" not in syms and "UP" in syms
    for x in t:
        assert x.target_value <= 100 + 1e-9                     # 10% cap
        stop_pct = 1 - x.stop_price / x.price
        assert x.target_value * stop_pct <= 1000 * 0.005 + 1e-9  # 0.5% risk


def test_insufficient_history_returns_nothing():
    assert select_targets(closes(n=100), 1000, 0.1, 0.005) == []


def test_plan_orders_sells_unselected_and_stopped_first():
    targets = [Target("UP", 100, 90, 100, 0.2)]
    sells, buys = plan_orders(targets, {"DOWN": 1.0, "UP": 0.2}, {"UP": 100, "DOWN": 50}, {})
    assert ("DOWN", 1.0) in sells
    assert buys == [("UP", pytest.approx(0.8))]
    sells, _ = plan_orders(targets, {"UP": 1.0}, {"UP": 85}, {"UP": 90})
    assert sells == [("UP", 1.0)]


def test_rebalance_band_avoids_churn():
    targets = [Target("UP", 100, 90, 100, 0.2)]
    sells, buys = plan_orders(targets, {"UP": 0.9}, {"UP": 100}, {})
    assert sells == [] and buys == []
