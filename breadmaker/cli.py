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
    a = ap.parse_args(argv)

    if a.cmd == "hash-password":
        from .dashboard.app import hash_password
        pw = getpass.getpass("New dashboard password: ")
        if len(pw) < 12:
            print("Use at least 12 characters.", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0
    if a.cmd == "safety-check":
        from .safety_scan import scan
        problems = scan(Path(__file__).resolve().parent.parent)
        for p in problems:
            print("SAFETY:", p)
        print("safety-check:", "FAIL" if problems else "PASS")
        return 1 if problems else 0
    from .config import load_settings
    from .db import connect
    from .risk import KillSwitch

    s = load_settings()
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
