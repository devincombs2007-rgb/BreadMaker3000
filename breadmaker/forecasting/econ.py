"""Economic-release forecasting (CPI month-over-month, unemployment rate) + walk-forward validation.

Data: BLS Public Data API v1 (no registration; US government data). Series:
  CUSR0000SA0  CPI-U, all items, seasonally adjusted (index)  -> MoM % change
  LNS14000000  unemployment rate, seasonally adjusted (%)
Caveat: the API returns CURRENT (revised) values; Kalshi economic markets settle on the FIRST
release. Seasonal-factor revisions make validation on revised data somewhat optimistic.

Models (predictive normal, sigma = rolling std of the model's own past out-of-sample errors):
  naive (last value), mean24 (24-month mean), autoreg (statsmodels AutoReg, AIC lag <= 6,
  established open-source), lgbm (LightGBM on 12 lags, refit yearly).
Events evaluated: x_t >= threshold for a grid of thresholds (Kalshi-style brackets).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import norm

from .feeds import _prov

BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"


def fetch_bls(series: list[str], start_year: int, end_year: int) -> tuple[dict[str, pd.Series], list]:
    import httpx
    out: dict[str, list] = {s: [] for s in series}
    provs = []
    for a in range(start_year, end_year + 1, 10):  # v1: <= 10 years per request
        b = min(a + 9, end_year)
        body = {"seriesid": series, "startyear": str(a), "endyear": str(b)}
        from .licences import require_url
        require_url(BLS_V1)
        r = httpx.post(BLS_V1, json=body, timeout=60, headers={"User-Agent": "breadmaker3000-research/0.4"})
        r.raise_for_status()
        js = r.json()
        if js.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS: {js.get('status')} {js.get('message')}")
        provs.append(_prov("BLS Public Data API v1", BLS_V1, body, r.content))
        for s in js["Results"]["series"]:
            for d in s["data"]:
                if d["period"].startswith("M") and d["period"] != "M13" and d["value"] not in ("-", ""):
                    out[s["seriesID"]].append((pd.Timestamp(f"{d['year']}-{d['period'][1:]}-01"), float(d["value"])))
    return {k: pd.Series(dict(v)).sort_index() for k, v in out.items() if v}, provs


def _forecasts(x: pd.Series, start: int = 120) -> pd.DataFrame:
    from statsmodels.tsa.ar_model import AutoReg, ar_select_order
    vals = x.to_numpy()
    rows = []
    gbm = None
    for t in range(start, len(vals)):
        hist = vals[:t]
        f = {"date": x.index[t], "y": vals[t], "naive": hist[-1], "mean24": hist[-24:].mean()}
        try:
            sel = ar_select_order(hist[-120:], maxlag=6, ic="aic")
            lags = sel.ar_lags or [1]
            m = AutoReg(hist[-120:], lags=lags).fit()
            f["autoreg"] = float(m.predict(start=len(hist[-120:]), end=len(hist[-120:]))[0])
        except Exception:  # noqa: BLE001
            f["autoreg"] = np.nan
        try:
            import lightgbm as lgb
            if gbm is None or (t - start) % 12 == 0:
                X = np.array([hist[i - 12:i][::-1] for i in range(12, len(hist))])
                gbm = lgb.LGBMRegressor(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=20,
                                        verbose=-1, random_state=0).fit(X, hist[12:])
            f["lgbm"] = float(gbm.predict(hist[-12:][::-1].reshape(1, -1))[0])
        except ImportError:
            f["lgbm"] = np.nan
        rows.append(f)
    return pd.DataFrame(rows)


def evaluate(x: pd.Series, thresholds: list[float], sigma_window: int = 36, relative: bool = False) -> dict:
    """Walk-forward: sigma for month t uses only that model's errors before t.
    relative=True: thresholds are offsets from the last released value (markets sit near it)."""
    from ..kalshi.tracking import metrics
    from ..research.tournament import compare
    fc = _forecasts(x).dropna()
    models = ["naive", "mean24", "autoreg", "lgbm"]
    probs = {m: [] for m in models}
    ys, blocks = [], []
    for i in range(sigma_window, len(fc)):
        row = fc.iloc[i]
        for off in thresholds:
            thr = round(row["naive"] + off, 1) if relative else off
            for m in models:
                err = (fc["y"].iloc[i - sigma_window:i] - fc[m].iloc[i - sigma_window:i]).to_numpy()
                sd = max(float(np.std(err, ddof=1)), 1e-3)
                probs[m].append(float(norm.sf((thr - row[m]) / sd)))
            ys.append(1.0 if row["y"] >= thr - 1e-9 else 0.0)
            blocks.append(str(row["date"].date()))
    preds = {m: np.asarray(v) for m, v in probs.items()}
    y = np.asarray(ys)
    res = compare(preds, y, np.asarray(blocks), baseline="naive")
    res["metrics"] = {m: metrics(preds[m], y) for m in models}
    res["months"] = [str(fc["date"].iloc[sigma_window].date()), str(fc["date"].iloc[-1].date())] if len(fc) > sigma_window else []
    res["point_mae"] = {m: float(np.mean(np.abs(fc["y"] - fc[m]))) for m in models}
    return res


def validate_econ(end_year: int) -> dict:
    series, provs = fetch_bls(["CUSR0000SA0", "LNS14000000"], end_year - 19, end_year)
    out = {"provenance": [json.loads(json.dumps(p, default=str)) for p in provs]}
    cpi = series.get("CUSR0000SA0")
    if cpi is not None and len(cpi) > 200:
        mom = (cpi.pct_change() * 100).round(1).dropna()   # Kalshi-style: MoM % rounded to 0.1
        out["cpi_mom"] = evaluate(mom, [-0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    un = series.get("LNS14000000")
    if un is not None and len(un) > 200:
        out["unemployment_rate"] = evaluate(un, [-0.2, -0.1, 0.0, 0.1, 0.2], relative=True)
    return out
