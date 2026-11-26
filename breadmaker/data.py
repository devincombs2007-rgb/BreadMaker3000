"""Market data helpers.

`synthetic_ohlcv` produces regime-switching random-walk data for SOFTWARE TESTS
ONLY. Results computed on synthetic data say nothing about real profitability
and must never be reported as performance.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


def synthetic_ohlcv(n: int = 2000, freq: str = "1h", start: str = "2022-01-01", seed: int = 7,
                    s0: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Markov regimes: 0=bull,1=bear,2=chop
    drift = np.array([0.0006, -0.0006, 0.0])
    vol = np.array([0.008, 0.012, 0.006])
    P = np.array([[0.995, 0.003, 0.002], [0.004, 0.993, 0.003], [0.003, 0.003, 0.994]])
    reg = np.empty(n, dtype=int)
    reg[0] = 2
    for i in range(1, n):
        reg[i] = rng.choice(3, p=P[reg[i - 1]])
    r = drift[reg] + vol[reg] * rng.standard_t(5, n) / np.sqrt(5 / 3)
    close = s0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[s0], close[:-1]]) * np.exp(rng.normal(0, 0.0005, n))
    hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol[reg] / 2)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol[reg] / 2)))
    volume = rng.lognormal(10, 0.5, n) * (1 + 5 * np.abs(r))
    idx = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": volume,
                         "regime": reg}, index=idx)


def validate_ohlcv(df: pd.DataFrame, max_gap: pd.Timedelta | None = None) -> list[str]:
    """Data quality checks: returns a list of problems (empty == clean)."""
    problems = []
    if df.index.has_duplicates:
        problems.append("duplicate timestamps")
    if not df.index.is_monotonic_increasing:
        problems.append("index not sorted")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        problems.append("non-positive prices")
    if ((df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))).any():
        problems.append("high/low inconsistent with open/close")
    if df[["open", "high", "low", "close"]].isna().any().any():
        problems.append("missing prices")
    if max_gap is not None and len(df) > 1:
        gaps = df.index.to_series().diff().dropna()
        if (gaps > max_gap).any():
            problems.append(f"{int((gaps > max_gap).sum())} gaps larger than {max_gap}")
    return problems


def is_stale(last_ts: datetime, max_age_seconds: float, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return (now - last_ts).total_seconds() > max_age_seconds
