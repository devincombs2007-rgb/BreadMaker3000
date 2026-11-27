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
import time
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


def evaluate_joint_pair(fa: dict, oa: dict, fb: dict, ob: dict, window: int = 90, gap: int = 2,
                        min_pool: int = 30) -> dict:
    """Parlay 'A high >= round(F_A) AND B high >= round(F_B)' on the same day, three estimators."""
    from ..research.tournament import compare
    from .joint import empirical_joint, gaussian_copula, latent_correlation_from_residuals
    days = sorted(set(fa) & set(oa) & set(fb) & set(ob))
    p_ind, p_cop, p_emp, ys, blocks = [], [], [], [], []
    for d in days:
        past = [date.fromordinal(d.toordinal() - k) for k in range(gap, gap + window)]
        rv = np.array([[oa[x] - fa[x], ob[x] - fb[x]] for x in past if x in fa and x in oa and x in fb and x in ob])
        if len(rv) < min_pool:
            continue
        ka, kb = round(fa[d]), round(fb[d])
        ca, cb = (lambda v, k=ka: v >= k), (lambda v, k=kb: v >= k)
        pa = (np.sum(np.rint(fa[d] + rv[:, 0]) >= ka) + 0.5) / (len(rv) + 1)
        pb = (np.sum(np.rint(fb[d] + rv[:, 1]) >= kb) + 0.5) / (len(rv) + 1)
        R = latent_correlation_from_residuals(rv)
        p_ind.append(pa * pb)
        p_cop.append(gaussian_copula([pa, pb], R))
        p_emp.append(empirical_joint([fa[d], fb[d]], rv, [ca, cb])[0])
        ys.append(1.0 if (oa[d] >= ka and ob[d] >= kb) else 0.0)
        blocks.append(f"{d.isocalendar()[0]}-{d.isocalendar()[1]}")
    if not ys:
        return {"n": 0}
    y = np.asarray(ys)
    res = compare({"independent": np.asarray(p_ind), "gaussian_copula": np.asarray(p_cop),
                   "empirical_joint": np.asarray(p_emp)}, y, np.asarray(blocks), baseline="independent")
    res["base_rate"] = float(y.mean())
    res["mean_p"] = {"independent": float(np.mean(p_ind)), "gaussian_copula": float(np.mean(p_cop)),
                     "empirical_joint": float(np.mean(p_emp))}
    return res


def run(stations: dict, out_dir: Path, crypto_bases=("BTC", "ETH"), weather_start=date(2024, 2, 1),
        sections: set | None = None) -> dict:
    from .feeds import cli_daily_highs, crypto_hourly_closes, previous_day1_daily_max
    out_dir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    results = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provenance": [],
               "weather": {}, "crypto": {}, "joint": {}, "sports": {}, "econ": {}, "errors": {}}
    station_data: dict = {}
    for i, (series, cfg) in enumerate(stations.items()):
        st = cfg["station"]
        if i:
            time.sleep(3)  # space out requests to the free Open-Meteo tier
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
            station_data[st] = (fc, obs)
        except Exception as exc:  # noqa: BLE001 - record, never fabricate
            results["errors"][f"weather:{st}"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    for base in crypto_bases:
        try:
            closes, prov = crypto_hourly_closes(base, datetime(2024, 1, 1, tzinfo=timezone.utc))
            results["provenance"].append(prov)
            results["crypto"][base] = {"source": prov["source"], **evaluate_crypto(closes)}
        except Exception as exc:  # noqa: BLE001
            results["errors"][f"crypto:{base}"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    for a, b in (("KNYC", "KPHL"), ("KNYC", "KMDW"), ("KNYC", "KLAX"), ("KMDW", "KDEN")):
        if a in station_data and b in station_data:
            results["joint"][f"{a}+{b}"] = evaluate_joint_pair(*station_data[a], *station_data[b])
    if sections is None or "sports" in sections:
        from . import sports
        for league, fn in (("NFL", sports.validate_nfl), ("MLB", sports.validate_mlb), ("NBA", sports.validate_nba)):
            try:
                results["sports"][league] = fn()
            except Exception as exc:  # noqa: BLE001
                results["errors"][f"sports:{league}"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    if sections is None or "econ" in sections:
        from .econ import validate_econ
        try:
            results["econ"] = validate_econ(datetime.now(timezone.utc).year)
        except Exception as exc:  # noqa: BLE001
            results["errors"]["econ"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    (out_dir / "validation.json").write_text(json.dumps(results, indent=2, default=str))
    (out_dir / "validation.md").write_text(render(results))
    return results


def _n(x, d=4):
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{d}f}"


def _tournament_lines(t: dict) -> list[str]:
    L = []
    for name, m in t.get("models", {}).items():
        if "verdict" in m:
            lo, hi = m["ci95"]
            L.append(f"- **{name}**: Brier {_n(m['brier'])} · vs {t['baseline']} {m['diff_vs_baseline']:+.4f} "
                     f"[{lo:+.4f}, {hi:+.4f}] → **{m['verdict']}**")
        else:
            L.append(f"- {name} (baseline): Brier {_n(m['brier'])}")
    return L


def render(r: dict) -> str:
    """Phone-friendly Markdown (short lists, no wide tables)."""
    L = ["# Forecast validation (real public data, out-of-sample)", "",
         f"_{r['generated_at']}. No Kalshi data. Lower Brier is better. 95% intervals use a block bootstrap; "
         "'better' requires the whole interval below zero._", "",
         "Data: Open-Meteo (CC BY 4.0); NWS climate reports via Iowa Environmental Mesonet; public crypto prices; "
         "BLS; nflverse; FiveThirtyEight (CC BY 4.0). The information used here was obtained free of charge from "
         "and is copyrighted by Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org.", "",
         "## Weather: P(high ≥ k)"]
    for st, w in r.get("weather", {}).items():
        if not w.get("n_predictions"):
            L.append(f"- {st}: insufficient overlapping data")
            continue
        L.append(f"- **{st}** ({w['n_days']} days): Brier {_n(w['model']['brier'])} vs Gaussian "
                 f"{_n(w['gaussian_baseline']['brier'])} vs raw {_n(w['raw_point_forecast']['brier'])}; "
                 f"calibration error {_n(w['model']['ece'], 3)}")
    if r.get("joint"):
        L += ["", "## Parlays: both cities ≥ forecast (same day)"]
        for pair, j in r["joint"].items():
            if j.get("n"):
                L.append(f"**{pair}** ({j['n']} days, happened {j['base_rate']:.0%}; mean predicted: "
                         + ", ".join(f"{k} {v:.0%}" for k, v in j["mean_p"].items()) + ")")
                L += _tournament_lines(j)
    if r.get("crypto"):
        L += ["", "## Crypto: P(price above strike)"]
        for base, c in r["crypto"].items():
            for h, m in c.items():
                if isinstance(m, dict) and "normal" in m:
                    L.append(f"- {base} {h}: Brier normal {_n(m['normal']['brier'])} / t {_n(m['student_t']['brier'])}; "
                             f"calibration error {_n(m['normal']['ece'], 3)} / {_n(m['student_t']['ece'], 3)}")
    if r.get("sports"):
        L += ["", "## Sports: home team wins (walk-forward by season)"]
        for lg, t in r["sports"].items():
            L.append(f"**{lg}** ({t.get('n', 0)} games, seasons {t.get('seasons')}, {t.get('source', '')})")
            L += _tournament_lines(t)
            if t.get("note"):
                L.append(f"- _{t['note']}_")
    econ = r.get("econ") or {}
    for key, label in (("cpi_mom", "CPI month-over-month ≥ threshold"), ("unemployment_rate", "Unemployment rate ≥ threshold")):
        if key in econ:
            t = econ[key]
            L += ["", f"## Economics: {label}", f"Months {t.get('months')} · point MAE: "
                  + ", ".join(f"{k} {v:.3f}" for k, v in t.get("point_mae", {}).items())]
            L += _tournament_lines(t)
    if r.get("errors"):
        L += ["", "## Not obtained (nothing substituted)"] + [f"- {k}: {v}" for k, v in r["errors"].items()]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    from ..config import load_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation_results")
    ap.add_argument("--sections", default="all", help="comma list of extra sections: sports,econ (or 'all'/'none')")
    a = ap.parse_args(argv)
    sections = None if a.sections == "all" else set() if a.sections == "none" else set(a.sections.split(","))
    res = run(load_settings().kalshi.weather_stations, Path(a.out), sections=sections)
    print(render(res))
    ok = any(w.get("n_predictions") for w in res["weather"].values()) or res["crypto"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
