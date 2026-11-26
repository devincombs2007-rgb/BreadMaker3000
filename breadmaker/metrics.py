"""Shared performance metrics. Pure functions over equity curves / trade P&L."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def returns_from_equity(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def sharpe(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return float("nan")
    downside = np.minimum(r.to_numpy(), 0.0)
    dd = math.sqrt(np.mean(downside ** 2))
    if dd == 0:
        return float("nan")
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def cagr(equity: pd.Series, periods_per_year: float) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return float("nan")
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def trade_stats(trade_pnls: list[float] | pd.Series) -> dict:
    p = pd.Series(trade_pnls, dtype=float)
    if p.empty:
        return {"n_trades": 0, "win_rate": float("nan"), "profit_factor": float("nan"),
                "avg_trade": float("nan")}
    gains = p[p > 0].sum()
    losses = -p[p < 0].sum()
    return {
        "n_trades": int(len(p)),
        "win_rate": float((p > 0).mean()),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf") if gains > 0 else float("nan"),
        "avg_trade": float(p.mean()),
    }


def summarize(equity: pd.Series, periods_per_year: float, trade_pnls=None, costs_paid: float = 0.0) -> dict:
    r = returns_from_equity(equity)
    out = {
        "start_equity": float(equity.iloc[0]) if len(equity) else float("nan"),
        "end_equity": float(equity.iloc[-1]) if len(equity) else float("nan"),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) > 1 else float("nan"),
        "cagr": cagr(equity, periods_per_year),
        "sharpe": sharpe(r, periods_per_year),
        "sortino": sortino(r, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "n_obs": int(len(r)),
        "costs_paid": float(costs_paid),
    }
    if trade_pnls is not None:
        out.update(trade_stats(trade_pnls))
    return out


# ---- multiple-testing correction --------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    # Acklam's rational approximation (sufficient for DSR thresholds).
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum of n_trials Sharpe estimates under the null (Bailey & Lopez de Prado 2014)."""
    if n_trials <= 1:
        return 0.0
    g = 0.5772156649
    return math.sqrt(sr_variance) * ((1 - g) * _norm_ppf(1 - 1 / n_trials) + g * _norm_ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe_ratio(returns: pd.Series, n_trials: int, sr_variance_across_trials: float) -> float:
    """Probability that the true (per-period) Sharpe exceeds the best-of-N null benchmark.

    Values > 0.95 are conventionally treated as significant after multiple testing.
    """
    r = returns.dropna()
    n = len(r)
    if n < 3 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3
    sr0 = expected_max_sharpe(n_trials, sr_variance_across_trials)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return _norm_cdf((sr - sr0) * math.sqrt(n - 1) / denom)
