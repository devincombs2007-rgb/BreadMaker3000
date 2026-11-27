"""Out-of-sample validation of the forecasting models on REAL public data (no Kalshi data needed).

Weather: for each station and day d, the forecast issued ~1 day before (Open-Meteo previous runs)
and a residual pool from days <= d-2 produce P(high >= k) for thresholds k around the forecast;
outcomes are the NWS CLI highs. Compared against (a) a Gaussian with the same residual mean/sd and
(b) the raw point forecast treated as certain.
Crypto: every 6 hours, P(price above K at t+h) for strikes at z in {-1.5..1.5} sigma, horizons
1/6/24 h, normal vs Student-t tails, volatility from past returns only.
Overlapping horizons and nearby thresholds are correlated: effective sample sizes are smaller than n.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.stats import norm

from ..kalshi.markets import Market
from ..kalshi.tracking import calibration_table, metrics
from .crypto import ewma_sigma, prob_above
from .weather import STANDARD_TZ, probability_from_residuals, residual_pool


def _threshold_market(k: int) -> Market:
    return Market("T", "E", "S", "", "active", None, "greater_or_equal", float(k), None, "")


def evaluate_weather(forecasts: dict[date, float], observed: dict[date, float], min_pool: int = 30,
                     offsets=range(-6, 7)) -> dict:
    rows = []
    for d in sorted(set(forecasts) & set(observed)):
        pool = residual_pool(forecasts, observed, d)
        if len(pool) < min_pool:
            continue
        F, mu, sd = forecasts[d], float(pool.mean()), float(pool.std(ddof=1)) or 1.0
        for off in offsets:
            k = int(round(F)) + off
            p_model = probability_from_residuals(F, pool, _threshold_market(k))[0]
            p_gauss = float(norm.sf((k - 0.5 - (F + mu)) / sd))
            p_raw = 1.0 if round(F) >= k else 0.0
            rows.append((d, k, p_model, p_gauss, p_raw, 1.0 if observed[d] >= k else 0.0))
    if not rows:
        return {"n": 0}
    a = np.array([r[2:] for r in rows])
    y = a[:, 3]
    res = {"n_predictions": len(rows), "n_days": len({r[0] for r in rows}),
           "first_day": str(rows[0][0]), "last_day": str(rows[-1][0]),
           "model": metrics(a[:, 0], y), "gaussian_baseline": metrics(a[:, 1], y), "raw_point_forecast": metrics(a[:, 2], y),
           "calibration": calibration_table(a[:, 0], y)}
    res["brier_skill_vs_gaussian"] = 1 - res["model"]["brier"] / res["gaussian_baseline"]["brier"]
    res["brier_skill_vs_raw"] = 1 - res["model"]["brier"] / res["raw_point_forecast"]["brier"]
    return res


def evaluate_crypto(closes: dict[int, float], step_hours: int = 6, horizons=(1, 6, 24), zs=(-1.5, -0.75, 0, 0.75, 1.5),
                    lookback: int = 24 * 30) -> dict:
    ts = sorted(closes)
    px = np.array([closes[t] for t in ts])
    lr = np.diff(np.log(px))
    out = {}
    for h in horizons:
        rows = []
        for i in range(lookback + 1, len(ts) - h, step_hours):
            if ts[i + h] - ts[i] != h * 3_600_000:
                continue  # gap in data: skip rather than misalign
            sig = ewma_sigma(lr[i - lookback:i])
            if not math.isfinite(sig) or sig <= 0:
                continue
            s, s_end = px[i], px[i + h]
            for z in zs:
                k = s * math.exp(z * sig * math.sqrt(h))
                rows.append((prob_above(s, k, sig, h, "normal"), prob_above(s, k, sig, h, "t"), 1.0 if s_end > k else 0.0))
        if not rows:
            out[f"{h}h"] = {"n": 0}
            continue
        a = np.array(rows)
        out[f"{h}h"] = {"n": len(rows), "normal": metrics(a[:, 0], a[:, 2]), "student_t": metrics(a[:, 1], a[:, 2]),
                        "calibration_t": calibration_table(a[:, 1], a[:, 2])}
    out["first"] = datetime.fromtimestamp(ts[0] / 1000, timezone.utc).isoformat() if ts else None
    out["last"] = datetime.fromtimestamp(ts[-1] / 1000, timezone.utc).isoformat() if ts else None
    return out


def run(stations: dict, out_dir: Path, crypto_bases=("BTC", "ETH"), weather_start=date(2024, 2, 1)) -> dict:
    from .feeds import cli_daily_highs, crypto_hourly_closes, previous_day1_daily_max
    out_dir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    results = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provenance": [],
               "weather": {}, "crypto": {}, "errors": {}}
    for series, cfg in stations.items():
        st = cfg["station"]
        try:
            std_tz = STANDARD_TZ[cfg["tz"]]
            fc, prov = previous_day1_daily_max(cfg["lat"], cfg["lon"], std_tz, weather_start, end)
            results["provenance"].append(prov)
            obs = {}
            for y in range(weather_start.year, end.year + 1):
                o, p2 = cli_daily_highs(st, y)
                obs.update(o)
                results["provenance"].append(p2)
            results["weather"][st] = {"series": series, **evaluate_weather(fc, obs)}
        except Exception as exc:  # noqa: BLE001 - record, never fabricate
            results["errors"][f"weather:{st}"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    for base in crypto_bases:
        try:
            closes, prov = crypto_hourly_closes(base, datetime(2024, 1, 1, tzinfo=timezone.utc))
            results["provenance"].append(prov)
            results["crypto"][base] = {"source": prov["source"], **evaluate_crypto(closes)}
        except Exception as exc:  # noqa: BLE001
            results["errors"][f"crypto:{base}"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    (out_dir / "validation.json").write_text(json.dumps(results, indent=2, default=str))
    (out_dir / "validation.md").write_text(render(results))
    return results


def _n(x, d=4):
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{d}f}"


def render(r: dict) -> str:
    L = ["# Forecast model validation (REAL public data, out-of-sample)", "",
         f"Generated {r['generated_at']}. No Kalshi data used. Weather data: Open-Meteo (CC BY 4.0) and NWS CLI via "
         "Iowa Environmental Mesonet. Lower Brier/log-loss is better; ECE = calibration error.", "",
         "## Weather: P(daily high >= k)", "",
         "| Station | days | predictions | Brier model | Brier gaussian | Brier raw | skill vs gaussian | log loss | ECE |",
         "|---|---|---|---|---|---|---|---|---|"]
    for st, w in r["weather"].items():
        if not w.get("n_predictions"):
            L.append(f"| {st} | 0 | insufficient overlapping data |  |  |  |  |  |  |")
            continue
        L.append(f"| {st} | {w['n_days']} ({w['first_day']}→{w['last_day']}) | {w['n_predictions']} | "
                 f"{_n(w['model']['brier'])} | {_n(w['gaussian_baseline']['brier'])} | {_n(w['raw_point_forecast']['brier'])} | "
                 f"{_n(w['brier_skill_vs_gaussian'], 3)} | {_n(w['model']['log_loss'])} | {_n(w['model']['ece'])} |")
    L += ["", "## Crypto: P(price above strike at t+h)", "",
          "| Asset | horizon | n | Brier normal | Brier Student-t | ECE normal | ECE Student-t |", "|---|---|---|---|---|---|---|"]
    for base, c in r["crypto"].items():
        for h, m in c.items():
            if not isinstance(m, dict) or "normal" not in m:
                continue
            L.append(f"| {base} | {h} | {m['n']} | {_n(m['normal']['brier'])} | {_n(m['student_t']['brier'])} | "
                     f"{_n(m['normal']['ece'])} | {_n(m['student_t']['ece'])} |")
    if r["errors"]:
        L += ["", "## Errors (data not obtained - nothing substituted)", ""] + [f"- {k}: {v}" for k, v in r["errors"].items()]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    from ..config import load_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation_results")
    a = ap.parse_args(argv)
    res = run(load_settings().kalshi.weather_stations, Path(a.out))
    print(render(res))
    ok = any(w.get("n_predictions") for w in res["weather"].values()) or res["crypto"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
