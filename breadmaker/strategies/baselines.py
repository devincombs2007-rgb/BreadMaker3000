"""Simple, transparent baseline strategies.

Every function returns a *target exposure* series in [0, 1] computed only from
data available at the close of each bar. The backtester executes at the NEXT
bar's open, so there is no look-ahead. Advanced/ML strategies must beat these
(after costs, out of sample) before they are considered.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


def sma_crossover(df: pd.DataFrame, fast: int = 20, slow: int = 100) -> pd.Series:
    f = df["close"].rolling(fast).mean()
    s = df["close"].rolling(slow).mean()
    return (f > s).astype(float).where(s.notna(), 0.0)


def time_series_momentum(df: pd.DataFrame, lookback: int = 90) -> pd.Series:
    roc = df["close"].pct_change(lookback)
    return (roc > 0).astype(float).where(roc.notna(), 0.0)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def rsi_mean_reversion(df: pd.DataFrame, n: int = 14, entry: float = 30, exit: float = 55,
                       trend: int = 200) -> pd.Series:
    """Buy oversold dips only while above the long-term trend; exit on recovery."""
    r = rsi(df["close"], n)
    tr = df["close"] > df["close"].rolling(trend).mean()
    pos = np.zeros(len(df))
    held = False
    for i, (ri, ti) in enumerate(zip(r.to_numpy(), tr.to_numpy())):
        if not held and ti and ri < entry:
            held = True
        elif held and (ri > exit or not ti):
            held = False
        pos[i] = 1.0 if held else 0.0
    return pd.Series(pos, index=df.index)


def donchian_breakout(df: pd.DataFrame, entry: int = 55, exit: int = 20) -> pd.Series:
    hi = df["high"].rolling(entry).max().shift(1)
    lo = df["low"].rolling(exit).min().shift(1)
    pos = np.zeros(len(df))
    held = False
    for i, (c, h, l) in enumerate(zip(df["close"].to_numpy(), hi.to_numpy(), lo.to_numpy())):
        if not held and not np.isnan(h) and c > h:
            held = True
        elif held and not np.isnan(l) and c < l:
            held = False
        pos[i] = 1.0 if held else 0.0
    return pd.Series(pos, index=df.index)


def vol_target(exposure: pd.Series, df: pd.DataFrame, target_ann_vol: float, periods_per_year: float,
               lookback: int = 20, cap: float = 1.0) -> pd.Series:
    """Scale exposure so realised volatility targets `target_ann_vol` (never > cap, no leverage)."""
    vol = df["close"].pct_change().rolling(lookback).std() * np.sqrt(periods_per_year)
    scale = (target_ann_vol / vol).clip(upper=cap).fillna(0.0)
    return (exposure * scale).clip(0, cap)


REGISTRY = {
    "buy_and_hold": buy_and_hold,
    "sma_crossover": sma_crossover,
    "ts_momentum": time_series_momentum,
    "rsi_mean_reversion": rsi_mean_reversion,
    "donchian_breakout": donchian_breakout,
}
