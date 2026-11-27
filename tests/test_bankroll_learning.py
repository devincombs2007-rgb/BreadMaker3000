"""Bankroll sizing and significance-gated learning (SYNTHETIC inputs)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from breadmaker.config import KalshiConfig
from breadmaker.kalshi.bankroll import size
from breadmaker.kalshi.tracking import walk_forward_recalibration

CFG = KalshiConfig(api_enabled=False)


def base(**kw):
    args = dict(cfg=CFG, p_cons=0.62, interval_width=0.05, price=0.40, depth=100, fee_rate=0.07, equity=25.0,
                cash=25.0, peak_equity=25.0, event_exposure=0.0, group_exposure=0.0, total_exposure=0.0, open_positions=0)
    args.update(kw)
    return size(**args)


def test_per_market_cap_binds_at_10pct():
    r = base(p_cons=0.85)
    assert r.contracts * 0.40 <= 2.5 + 1e-9 and "per-market cap" in " ".join(r.notes)


def test_uncertainty_shrinks_size():
    assert base(interval_width=0.40, p_cons=0.50).contracts < base(interval_width=0.0, p_cons=0.50).contracts
    assert base(interval_width=0.6).contracts == 0


def test_drawdown_throttle_reduces_to_zero_at_limit():
    full = base(p_cons=0.5).contracts
    half = base(p_cons=0.5, equity=22.5, cash=22.5, peak_equity=25.0).contracts   # 10% drawdown
    zero = base(p_cons=0.5, equity=20.0, cash=20.0, peak_equity=25.0).contracts   # 20% drawdown
    assert full > half > 0 and zero == 0


def test_correlation_group_and_cash_reserve_and_limits():
    assert base(group_exposure=CFG.max_group_exposure_pct * 25).contracts == 0
    assert base(cash=12.5).contracts == 0                    # 50% reserve already reached
    assert base(price=0.02).contracts == 0 and base(price=0.98).contracts == 0
    assert base(open_positions=CFG.max_open_positions).contracts == 0
    assert base(depth=2).contracts <= 2


def test_recalibration_requires_significant_improvement():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = np.random.default_rng(0)

    def recs(p_fn, n=1500):
        out = []
        for i in range(n):
            truth = rng.uniform(0.2, 0.8)
            issued = t0 + timedelta(hours=6 * i)
            out.append({"issued_at": issued.isoformat(), "settled_at": (issued + timedelta(hours=3)).isoformat(),
                        "p": p_fn(truth), "outcome": float(rng.uniform() < truth)})
        return out
    calibrated = walk_forward_recalibration(recs(lambda q: q))
    assert calibrated["use_recalibration"] is False          # nothing to fix -> no change adopted
    over = walk_forward_recalibration(recs(lambda q: float(np.clip(0.5 + 2.2 * (q - 0.5), 0.01, 0.99))))
    assert over["use_recalibration"] is True and over["diff_ci95"][1] < 0


def test_small_improvement_on_small_sample_is_not_adopted():
    """350 settled predictions with a real but small miscalibration: CI includes 0 -> keep raw model."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = np.random.default_rng(0)
    out = []
    for i in range(400):
        truth = rng.uniform(0.2, 0.8)
        issued = t0 + timedelta(hours=6 * i)
        out.append({"issued_at": issued.isoformat(), "settled_at": (issued + timedelta(hours=3)).isoformat(),
                    "p": float(np.clip(0.5 + 2.2 * (truth - 0.5), 0.01, 0.99)), "outcome": float(rng.uniform() < truth)})
    v = walk_forward_recalibration(out)
    assert v["brier_recalibrated"] < v["brier_raw"] and v["use_recalibration"] is False
