"""Pure decision logic for the ETF baseline (no LumiBot imports => unit-testable).

Strategy: trend-filtered relative-strength rotation across liquid ETFs.
* Eligible: close > SMA(trend) AND lookback return > 0 (absolute momentum).
* Rank eligible ETFs by lookback return (relative strength); hold top_n.
* Each position sized by risk: stop distance = stop_vol_mult * 20d daily vol * price,
  value = min(max_position_pct, max_risk_pct / stop_pct) * equity.
* Exit when no longer selected or close < stop.
Published basis: time-series & cross-sectional momentum (Moskowitz/Ooi/Pedersen
2012; Faber 2007 tactical allocation). Baseline only - no profitability claim.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ", "DBC"]


@dataclass(frozen=True)
class Params:
    lookback: int = 126
    trend: int = 200
    top_n: int = 3
    stop_vol_mult: float = 3.0
    vol_window: int = 20
    min_stop_pct: float = 0.04
    max_stop_pct: float = 0.15


@dataclass
class Target:
    symbol: str
    target_value: float
    stop_price: float
    price: float
    score: float


def select_targets(closes: pd.DataFrame, equity: float, max_position_pct: float, max_risk_pct: float,
                   p: Params = Params()) -> list[Target]:
    """`closes`: daily closes, columns = symbols, rows ascending; last row = latest completed bar."""
    if len(closes) < max(p.lookback, p.trend) + 1:
        return []
    last = closes.iloc[-1]
    sma = closes.iloc[-p.trend:].mean()
    mom = last / closes.iloc[-1 - p.lookback] - 1
    vol = closes.pct_change().iloc[-p.vol_window:].std()
    eligible = [s for s in closes.columns
                if np.isfinite(last[s]) and last[s] > sma[s] and mom[s] > 0 and np.isfinite(vol[s])]
    ranked = sorted(eligible, key=lambda s: mom[s], reverse=True)[: p.top_n]
    out = []
    for s in ranked:
        stop_pct = float(np.clip(p.stop_vol_mult * vol[s] * np.sqrt(5), p.min_stop_pct, p.max_stop_pct))
        weight = min(max_position_pct, max_risk_pct / stop_pct)
        out.append(Target(s, equity * weight, float(last[s] * (1 - stop_pct)), float(last[s]), float(mom[s])))
    return out


def plan_orders(targets: list[Target], holdings: dict[str, float], prices: dict[str, float],
                stops: dict[str, float], min_trade_value: float = 5.0, rebalance_band: float = 0.25):
    """Return (sells, buys) as lists of (symbol, qty). Sells first to free cash.

    Positions are only resized when off target by more than `rebalance_band` (fractional)
    to limit turnover costs.
    """
    tmap = {t.symbol: t for t in targets}
    sells, buys = [], []
    for sym, qty in holdings.items():
        px = prices.get(sym)
        if qty <= 0 or px is None:
            continue
        stopped = sym in stops and px < stops[sym]
        if sym not in tmap or stopped:
            sells.append((sym, qty))
            continue
        tv = tmap[sym].target_value
        cur = qty * px
        if cur > tv * (1 + rebalance_band) and cur - tv >= min_trade_value:
            sells.append((sym, (cur - tv) / px))
    for t in targets:
        px = prices.get(t.symbol, t.price)
        cur = holdings.get(t.symbol, 0.0) * px
        if cur < t.target_value * (1 - rebalance_band) and t.target_value - cur >= min_trade_value:
            buys.append((t.symbol, (t.target_value - cur) / px))
    return sells, buys
