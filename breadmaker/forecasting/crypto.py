"""Crypto price-threshold model (e.g. 'BTC above $X at time T').

P(S_T > K) under a zero-drift log-normal with volatility from an EWMA of past hourly log returns
(lambda=0.94), optionally with Student-t tails. The interval brackets volatility uncertainty
(sigma x [0.75, 1.33]). Known basis risk: Kalshi crypto markets settle on an index (e.g. an average
of a reference rate), not a single exchange print; markets whose rules cannot be matched are rejected.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
from scipy.stats import norm, t as student_t

from ..kalshi.markets import Market
from .base import Forecast, Rejection

MODEL = "crypto_price"
MIN_HISTORY_HOURS = 24 * 30
MAX_HORIZON_HOURS = 24 * 7


def ewma_sigma(log_returns: np.ndarray, lam: float = 0.94) -> float:
    r = np.asarray(log_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")
    var = float(np.var(r[: min(len(r), 48)]))
    for x in r:
        var = lam * var + (1 - lam) * x * x
    return math.sqrt(var)


def prob_above(spot: float, strike: float, sigma_h: float, hours: float, tails: str = "normal", nu: float = 4.0) -> float:
    if hours <= 0:
        return 1.0 if spot > strike else 0.0
    s = sigma_h * math.sqrt(hours)
    z = (math.log(strike / spot) + 0.5 * s * s) / s
    if tails == "t":
        scale = math.sqrt((nu - 2) / nu)  # unit-variance t
        return float(student_t.sf(z / scale, nu))
    return float(norm.sf(z))


def forecast(market: Market, spot: float | None, hourly_log_returns, hours_to_settle: float,
             issued_at: datetime, evidence_ids: list[int], tails: str = "t") -> Forecast | Rejection:
    if spot is None or not spot > 0:
        return Rejection(MODEL, "no spot price evidence")
    r = np.asarray(hourly_log_returns, dtype=float)
    if len(r) < MIN_HISTORY_HOURS:
        return Rejection(MODEL, f"only {len(r)} hours of price history (< {MIN_HISTORY_HOURS})")
    if not (0 < hours_to_settle <= MAX_HORIZON_HOURS):
        return Rejection(MODEL, f"horizon {hours_to_settle:.1f}h outside (0, {MAX_HORIZON_HOURS}]")
    sig = ewma_sigma(r)
    st, lo_k, hi_k = market.strike_type, market.floor_strike, market.cap_strike

    def p_at(sigma: float) -> float | None:
        if st in ("greater", "greater_or_equal") and lo_k:
            return prob_above(spot, lo_k, sigma, hours_to_settle, tails)
        if st in ("less", "less_or_equal") and hi_k:
            return 1 - prob_above(spot, hi_k, sigma, hours_to_settle, tails)
        if st == "between" and lo_k and hi_k and hi_k > lo_k:
            return prob_above(spot, lo_k, sigma, hours_to_settle, tails) - prob_above(spot, hi_k, sigma, hours_to_settle, tails)
        return None

    ps = [p_at(sig * k) for k in (0.75, 1.0, 1.33)]
    if any(p is None for p in ps):
        return Rejection(MODEL, f"strike type {st!r} not supported")
    p = min(max(ps[1], 1e-4), 1 - 1e-4)
    lo, hi = min(ps + [p]), max(ps + [p])
    return Forecast(p, max(0.0, lo), min(1.0, hi), MODEL, issued_at, evidence_ids,
                    {"spot": spot, "sigma_hourly": round(sig, 6), "hours": round(hours_to_settle, 2), "tails": tails})
