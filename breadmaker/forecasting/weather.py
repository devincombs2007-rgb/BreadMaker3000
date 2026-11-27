"""Daily-high-temperature model (Kalshi KXHIGH* series).

Method (model-output-statistics style, learned only from the past):
* point forecast F = max of the hourly 2 m temperature forecast over the settlement day, from the
  model run issued about one day ahead (Open-Meteo);
* residual pool R = {observed CLI high - F} for recent days whose observations were final before the
  forecast was issued (strictly past; default: days t-92 .. t-2);
* predictive distribution of the integer settlement value = round(F + r), r in R. This learns the
  station's bias (hourly sampling misses peaks; grid vs station) and spread from its own history.
* P(YES) = Jeffreys-smoothed fraction of samples satisfying the market's strike definition; the
  interval reflects the finite residual sample.
Settlement value: NWS Daily Climate Report (CLI) high, integer degF, local STANDARD time day.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import numpy as np

from ..kalshi.markets import Market
from .base import Forecast, Rejection, beta_interval

MODEL = "weather_high_temp"
MIN_RESIDUALS = 30
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                         "NOV", "DEC"], 1)}

STANDARD_TZ = {  # CLI days are local standard time all year
    "America/New_York": "Etc/GMT+5", "America/Chicago": "Etc/GMT+6",
    "America/Denver": "Etc/GMT+7", "America/Phoenix": "Etc/GMT+7", "America/Los_Angeles": "Etc/GMT+8",
}


def event_date(event_ticker: str) -> date | None:
    """KXHIGHNY-26NOV28 -> 2026-11-28."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def probability_from_residuals(point_forecast: float, residuals: np.ndarray, market: Market):
    samples = np.rint(point_forecast + residuals)
    outcomes = [market.outcome_yes(float(v)) for v in samples]
    if any(o is None for o in outcomes):
        return None
    k = float(sum(outcomes))
    return beta_interval(k, len(outcomes))


def forecast(market: Market, station_cfg: dict | None, point_forecast: float | None, residuals,
             issued_at: datetime, evidence_ids: list[int]) -> Forecast | Rejection:
    if station_cfg is None:
        return Rejection(MODEL, f"no station configured for series {market.series_ticker}")
    kw = station_cfg.get("rules_keyword", "")
    if not kw or kw.lower() not in market.rules_primary.lower():
        return Rejection(MODEL, f"rules text does not name the configured station ({kw!r}); not modelled")
    d = event_date(market.event_ticker)
    if d is None:
        return Rejection(MODEL, f"cannot parse date from {market.event_ticker}")
    if point_forecast is None or not np.isfinite(point_forecast):
        return Rejection(MODEL, "no point forecast available")
    r = np.asarray(residuals, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < MIN_RESIDUALS:
        return Rejection(MODEL, f"only {len(r)} past residuals (< {MIN_RESIDUALS}): insufficient calibration data")
    res = probability_from_residuals(point_forecast, r, market)
    if res is None:
        return Rejection(MODEL, f"strike type {market.strike_type!r} not machine-interpretable")
    p, lo, hi = res
    return Forecast(p, min(lo, p), max(hi, p), MODEL, issued_at, evidence_ids,
                    {"target_date": d.isoformat(), "point_forecast_f": round(point_forecast, 1),
                     "n_residuals": int(len(r)), "residual_mean": round(float(r.mean()), 2),
                     "residual_sd": round(float(r.std(ddof=1)), 2), "station": station_cfg.get("station")})


def residual_pool(forecasts: dict[date, float], observed: dict[date, float], target: date,
                  window: int = 90, gap_days: int = 2) -> np.ndarray:
    """Residuals from days [target-gap-window+1, target-gap]; observations for later days would not
    yet be final when the target-day forecast is issued (no look-ahead)."""
    out = []
    for k in range(gap_days, gap_days + window):
        d = date.fromordinal(target.toordinal() - k)
        if d in forecasts and d in observed:
            out.append(observed[d] - forecasts[d])
    return np.asarray(out, dtype=float)
