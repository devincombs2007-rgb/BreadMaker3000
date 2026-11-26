"""Real-data research run: ETF rotation (deployed logic) + crypto baseline lab.

Writes results.json and report.md. Every number comes from a computation in this run on the
datasets listed in the manifest (source, SHA-256). Nothing is hand-entered.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from ..costs import CRYPTO_KRAKEN, US_ETF_ALPACA
from ..db import connect
from ..metrics import summarize
from .experiments import AcceptanceCriteria, Lab
from .portfolio_backtest import buy_and_hold, exposure_matched, run_rotation

SPLIT_FRAC = 0.6  # first 60% "in-sample" (design period), last 40% held out


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_feather(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date").sort_index()


def _fmt(m: dict) -> dict:
    keys = ["total_return", "cagr", "sharpe", "sortino", "max_drawdown", "exposure_avg", "n_fills", "turnover_x",
            "costs_paid", "n_obs"]
    return {k: (None if isinstance(m.get(k), float) and not math.isfinite(m[k]) else m.get(k)) for k in keys if k in m}


def etf_study(data: Path) -> dict:
    files = sorted((data / "etf").glob("*-1d.feather"))
    if not files:
        return {"skipped": "no ETF data"}
    frames = {f.name.split("-")[0]: _load(f) for f in files}
    closes = pd.DataFrame({s: d["close"] for s, d in frames.items()}).sort_index()
    opens = pd.DataFrame({s: d["open"] for s, d in frames.items()}).reindex(closes.index)
    if "SPY" not in closes:
        return {"skipped": "SPY missing (benchmark)"}
    out = {"universe": list(closes.columns), "first": str(closes.index[0].date()), "last": str(closes.index[-1].date())}
    split = closes.index[int(len(closes) * SPLIT_FRAC)]
    out["split_date"] = str(split.date())
    runs = {}
    for label, halts in (("strategy_only", False), ("as_deployed_with_halts", True)):
        for k in (1, 2, 5):
            r = run_rotation(opens, closes, US_ETF_ALPACA.scaled(k), portfolio_halts=halts)
            runs[f"{label}_cost{k}x"] = {"full": _fmt(r.metrics), "halted_at": r.halted_at}
            if k == 1:
                eq = r.equity
                spy = buy_and_hold(closes["SPY"].loc[eq.index[0]:])
                matched = exposure_matched(closes["SPY"], r.exposure)
                ew = (closes.loc[eq.index[0]:].pct_change().fillna(0).mean(axis=1) + 1).cumprod() * 1000
                seg = {}
                for name, s in (("strategy", eq), ("spy_buy_hold", spy), ("spy_exposure_matched", matched),
                                ("equal_weight_universe", ew)):
                    seg[name] = {"full": _fmt(summarize(s, 252)),
                                 "in_sample": _fmt(summarize(s.loc[:split], 252)),
                                 "out_of_sample": _fmt(summarize(s.loc[split:], 252))}
                runs[f"{label}_cost1x"]["comparison"] = seg
    out["runs"] = runs
    so = runs["strategy_only_cost1x"]["comparison"]
    oos = so["strategy"]["out_of_sample"]
    out["verdict"] = {
        "oos_sharpe_positive": (oos.get("sharpe") or 0) > 0,
        "beats_spy_sharpe_oos": (oos.get("sharpe") or -9) > (so["spy_buy_hold"]["out_of_sample"].get("sharpe") or 9),
        "beats_exposure_matched_spy_return_oos": (oos.get("total_return") or -9)
        > (so["spy_exposure_matched"]["out_of_sample"].get("total_return") or 9),
        "profitable_at_5x_costs": (runs["strategy_only_cost5x"]["full"].get("total_return") or -1) > 0,
        "as_deployed_drawdown_halt_triggered": runs["as_deployed_with_halts_cost1x"]["halted_at"],
    }
    return out


def crypto_study(data: Path, db_path: Path) -> dict:
    lab = Lab(connect(db_path))
    grids = {
        "sma_crossover": {"fast": [10, 20, 50], "slow": [100, 200]},
        "ts_momentum": {"lookback": [30, 90, 180]},
        "rsi_mean_reversion": {"n": [14], "entry": [25, 30], "exit": [50, 60]},
        "donchian_breakout": {"entry": [20, 55], "exit": [10, 20]},
    }
    out = {}
    for f in sorted((data / "crypto").glob("*-1d.feather")):
        sym = f.name.split("_")[0]
        df = _load(f)
        res = pd.concat([lab.grid_search(f"crypto-daily-{sym}", k, g, df, CRYPTO_KRAKEN, 365,
                                         AcceptanceCriteria(min_oos_trades=5)) for k, g in grids.items()],
                        ignore_index=True)
        res = res.sort_values("oos_sharpe", ascending=False)
        out[sym] = {
            "rows": len(df), "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
            "n_trials_total": lab.n_trials(f"crypto-daily-{sym}"),
            "accepted": int(res["accepted"].sum()),
            "buy_hold_oos_sharpe": float(res["bench_oos_sharpe"].iloc[0]),
            "top5": json.loads(res.head(5).drop(columns=[c for c in res.columns if c.startswith("p_")]
                                                  ).assign(params=res.head(5).filter(like="p_").apply(
                lambda r: {k[2:]: v for k, v in r.items() if pd.notna(v)}, axis=1)).to_json(orient="records")),
        }
    return out or {"skipped": "no crypto daily data"}


def render(results: dict) -> str:
    venues = {d.get("venue") for d in results["manifest"]["datasets"] if not d.get("failed")}
    kind = "SYNTHETIC TEST DATA - NOT MARKET RESULTS" if "SYNTHETIC" in venues or not venues else "REAL historical data"
    L = [f"# BreadMaker3000 research run ({kind}, simulated trading)", "",
         "> Backtests on historical data are not forward performance. Parameters below were fixed before "
         "this data was examined (literature defaults); halves are reported so decay is visible.", ""]
    L += ["## Data", "", "| dataset | venue | rows | first | last | issues |", "|---|---|---|---|---|---|"]
    for d in results["manifest"]["datasets"]:
        if d.get("failed"):
            L.append(f"| {d['symbol']} {d.get('timeframe', '')} | **FAILED** | | | | {json.dumps(d['errors'])[:120]} |")
        else:
            L.append(f"| {d['symbol']} {d['timeframe']} | {d['venue']} | {d['rows']} | {d['first'][:10]} | "
                     f"{d['last'][:10]} | {', '.join(d['problems']) or 'none'} |")
    e = results.get("etf", {})
    if "runs" in e:
        L += ["", f"## ETF rotation (deployed LumiBot logic) {e['first']} → {e['last']}, split {e['split_date']}", "",
              "| series | segment | total return | CAGR | Sharpe | Sortino | max DD |", "|---|---|---|---|---|---|---|"]
        for name, segs in e["runs"]["strategy_only_cost1x"]["comparison"].items():
            for seg, m in segs.items():
                L.append(f"| {name} | {seg} | {_p(m.get('total_return'))} | {_p(m.get('cagr'))} | "
                         f"{_n(m.get('sharpe'))} | {_n(m.get('sortino'))} | {_p(m.get('max_drawdown'))} |")
        L += ["", "| run (full period) | total return | Sharpe | max DD | avg exposure | fills | turnover/yr | costs paid | drawdown halt |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, v in e["runs"].items():
            m = v["full"]
            L.append(f"| {k} | {_p(m.get('total_return'))} | {_n(m.get('sharpe'))} | {_p(m.get('max_drawdown'))} | "
                     f"{_p(m.get('exposure_avg'))} | {m.get('n_fills')} | {_n(m.get('turnover_x'))}x | "
                     f"${(m.get('costs_paid') or 0):.2f} | {v.get('halted_at') or '-'} |")
        L += ["", "**Verdict checks:** " + ", ".join(f"{k}={v}" for k, v in e["verdict"].items())]
    elif e:
        L += ["", f"ETF study skipped: {e}"]
    for sym, c in (results.get("crypto") or {}).items():
        if not isinstance(c, dict) or "top5" not in c:
            continue
        L += ["", f"## Crypto daily baseline lab: {sym} ({c['first']} → {c['last']})", "",
              f"Trials recorded (all-time, this family): {c['n_trials_total']}. Accepted: **{c['accepted']}**. "
              f"Buy-and-hold OOS Sharpe: {_n(c['buy_hold_oos_sharpe'])}.", "",
              "| strategy | params | IS Sharpe | OOS Sharpe | OOS return | OOS maxDD | trades | DSR | accepted | failed checks |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in c["top5"]:
            L.append(f"| {r['strategy']} | {r['params']} | {_n(r['is_sharpe'])} | {_n(r['oos_sharpe'])} | "
                     f"{_p(r['oos_return'])} | {_p(r['oos_maxdd'])} | {r['oos_trades']} | {_n(r['dsr'])} | "
                     f"{r['accepted']} | {r['failed_checks']} |")
    return "\n".join(L) + "\n"


def _p(x):
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:+.1%}"


def _n(x):
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="research_data")
    ap.add_argument("--out", default="research_results")
    a = ap.parse_args(argv)
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {"manifest": json.loads((data / "manifest.json").read_text())}
    results["etf"] = etf_study(data)
    results["crypto"] = crypto_study(data, out / "experiments.sqlite")
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    (out / "report.md").write_text(render(results))
    print(render(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
