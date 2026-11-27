"""`breadmaker` command line."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


def _load_ohlcv(path: str):
    import pandas as pd
    p = Path(path)
    df = pd.read_feather(p) if p.suffix == ".feather" else pd.read_csv(p)
    date_col = next(c for c in ("date", "timestamp", "datetime", "Date") if c in df.columns)
    df[date_col] = pd.to_datetime(df[date_col], utc=True)
    df = df.set_index(date_col).sort_index()
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def _daily(a) -> int:
    import os
    from datetime import datetime, timezone

    from . import health
    from .config import load_settings
    from .forward import run_daily
    s = load_settings()
    now = datetime.now(timezone.utc)
    fwd = run_daily(s.kalshi.weather_stations, Path(a.state) / "forward.sqlite", now)
    L = [f"# BreadMaker daily report - {now:%Y-%m-%d}", "",
         "_Simulation only. Public data only (no Kalshi data). Weather data: Open-Meteo (CC BY 4.0), NWS CLI via IEM._", "",
         "## Forward forecast log (pre-registered, settled against official NWS reports)",
         f"- Issued today: {fwd['issued']} · settled today: {fwd['settled']}",
         f"- Settled total: {fwd['scoreboard']['settled']} · pending: {fwd['scoreboard']['pending']} · since "
         f"{fwd['scoreboard'].get('first_target')}"]
    t = fwd["scoreboard"].get("tournament")
    if t:
        m = t["models"]["model"]
        L.append(f"- Model Brier {m['brier']:.4f} vs Gaussian {t['models']['gaussian']['brier']:.4f} · "
                 f"diff {m['diff_vs_baseline']:+.4f} [{m['ci95'][0]:+.4f}, {m['ci95'][1]:+.4f}] → **{m['verdict']}**")
    else:
        L.append("- Fewer than 50 settled forecasts: no accuracy verdict yet (by design).")
    L += [f"- ⚠️ {k}: {v}" for k, v in fwd["errors"].items()]
    findings = []
    if os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_REPOSITORY"):
        findings = health.check(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"], now)
        if len(fwd["errors"]) >= max(1, len(s.kalshi.weather_stations) // 2):
            findings.append({"severity": "warning", "check": "forward-log data",
                             "detail": f"{len(fwd['errors'])} station(s) failed today"})
        L += ["", health.markdown(findings)]
    Path(a.out).write_text("\n".join(L) + "\n")
    print("\n".join(L))
    Path(a.out).with_suffix(".health.json").write_text(json.dumps(findings))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="breadmaker", description="BreadMaker3000 (simulation only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("supervisor", help="run the orchestration loop")
    d = sub.add_parser("dashboard", help="run the dashboard")
    d.add_argument("--host", default="0.0.0.0")
    d.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("report", help="write a report")
    r.add_argument("--period", choices=["daily", "weekly"], default="daily")
    sub.add_parser("status", help="print JSON status")
    sub.add_parser("kill", help="engage emergency kill switch")
    sub.add_parser("release", help="release kill switch (owner only)")
    sub.add_parser("hash-password", help="hash a dashboard password")
    rs = sub.add_parser("research", help="baseline strategy lab on an OHLCV file")
    rs.add_argument("data")
    rs.add_argument("--periods-per-year", type=float, default=24 * 365)
    rs.add_argument("--asset", choices=["crypto", "etf"], default="crypto")
    ml = sub.add_parser("ml-eval", help="walk-forward ML evaluation on an OHLCV file")
    ml.add_argument("data")
    ml.add_argument("--horizon", type=int, default=12)
    ml.add_argument("--periods-per-year", type=float, default=24 * 365)
    sub.add_parser("safety-check", help="scan repo configs for live-trading risks")
    dy = sub.add_parser("daily", help="daily research: forward forecast log, rankings, health, report")
    dy.add_argument("--state", required=True, help="directory holding forward.sqlite (state chain)")
    dy.add_argument("--out", default="daily_report.md")
    rk = sub.add_parser("rankings", help="print market rankings as phone-friendly Markdown")
    rk.add_argument("--hours", type=float, default=24)
    kr = sub.add_parser("kalshi-run", help="one Kalshi research/paper cycle (requires authorization)")
    kr.add_argument("--max-books", type=int, default=300)
    kr.add_argument("--demo", action="store_true", help="use the Kalshi DEMO environment (separate gate and book)")
    sub.add_parser("kalshi-status", help="show whether Kalshi API access is allowed and why")
    vf = sub.add_parser("validate-forecasts", help="validate forecasting models on real public data")
    vf.add_argument("--out", default="validation_results")
    a = ap.parse_args(argv)

    if a.cmd == "hash-password":
        from .dashboard.app import hash_password
        pw = getpass.getpass("New dashboard password: ")
        if len(pw) < 12:
            print("Use at least 12 characters.", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0
    if a.cmd == "daily":
        return _daily(a)
    if a.cmd == "safety-check":
        from .safety_scan import scan
        problems = scan(Path(__file__).resolve().parent.parent)
        for p in problems:
            print("SAFETY:", p)
        print("safety-check:", "FAIL" if problems else "PASS")
        return 1 if problems else 0
    from .config import kalshi_access_allowed, load_settings
    from .db import connect
    from .risk import KillSwitch

    s = load_settings()
    if a.cmd == "rankings":
        from .kalshi.tracking import rankings, rankings_markdown
        print(rankings_markdown(rankings(connect(s.db_path), hours=a.hours)))
        return 0
    if a.cmd == "kalshi-status":
        from .config import kalshi_demo_access_allowed
        ok, why = kalshi_access_allowed(s.kalshi)
        dok, dwhy = kalshi_demo_access_allowed(s.kalshi_demo)
        print(json.dumps({"kalshi_api_allowed": ok, "reason": why, "bankroll": s.kalshi.bankroll,
                          "demo_api_allowed": dok, "demo_reason": dwhy}))
        return 0
    if a.cmd == "kalshi-run":
        from .kalshi.client import KalshiAccessDisabled, KalshiPublicClient
        from .kalshi.pipeline import run_cycle
        kcfg = s.kalshi_demo if a.demo else s.kalshi
        try:
            client = KalshiPublicClient(kcfg, demo=a.demo)
        except KalshiAccessDisabled as exc:
            print(f"Kalshi {'DEMO ' if a.demo else ''}access disabled: {exc}")
            return 3
        print(json.dumps(run_cycle(s, client, max_books=a.max_books, engine="kalshi_demo" if a.demo else "kalshi",
                                   kcfg=kcfg), indent=2, default=str))
        return 0
    if a.cmd == "validate-forecasts":
        from .forecasting.validation import main as vmain
        return vmain(["--out", a.out])
    if a.cmd == "supervisor":
        from .orchestrator.supervisor import main as sup
        sup()
    elif a.cmd == "dashboard":
        import uvicorn
        from .dashboard.app import create_app
        uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    elif a.cmd == "report":
        from .reports.generate import write_report
        print(write_report(connect(s.db_path), s.data_dir / "reports", a.period))
    elif a.cmd == "status":
        from .dashboard.app import build_summary
        out = build_summary(connect(s.db_path), s, KillSwitch(s.kill_switch_path))
        for e in out["engines"]:
            e.pop("equity_curve", None)
        print(json.dumps(out, indent=2, default=str))
    elif a.cmd == "kill":
        KillSwitch(s.kill_switch_path).engage("owner emergency stop via CLI")
        print("kill switch ENGAGED")
    elif a.cmd == "release":
        KillSwitch(s.kill_switch_path).release()
        print("kill switch released")
    elif a.cmd == "research":
        from .costs import CRYPTO_KRAKEN, US_ETF_ALPACA
        from .research.experiments import Lab
        df = _load_ohlcv(a.data)
        costs = CRYPTO_KRAKEN if a.asset == "crypto" else US_ETF_ALPACA
        lab = Lab(connect(s.db_path))
        grids = {
            "sma_crossover": {"fast": [10, 20, 50], "slow": [100, 200]},
            "ts_momentum": {"lookback": [24, 72, 168, 720]},
            "rsi_mean_reversion": {"n": [14], "entry": [25, 30], "exit": [50, 60]},
            "donchian_breakout": {"entry": [55, 110], "exit": [20, 55]},
        }
        import pandas as pd
        res = pd.concat([lab.grid_search(f"{a.asset}-baselines", k, g, df, costs, a.periods_per_year)
                         for k, g in grids.items()], ignore_index=True)
        print(res.sort_values("oos_sharpe", ascending=False).to_string(index=False))
    elif a.cmd == "ml-eval":
        from .costs import CRYPTO_KRAKEN
        from .ml.pipeline import available_models, walk_forward_evaluate
        df = _load_ohlcv(a.data)
        for m in available_models():
            r = walk_forward_evaluate(df, m, horizon=a.horizon, costs=CRYPTO_KRAKEN, periods_per_year=a.periods_per_year)
            print(json.dumps({"model": m, "auc": r.auc, "acc": r.accuracy, "base_rate": r.base_rate,
                              "n_test": r.n_test, **{k: r.strategy_metrics[k] for k in ("sharpe", "total_return", "max_drawdown")}},
                             default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
