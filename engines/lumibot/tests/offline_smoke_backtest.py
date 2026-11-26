"""Offline smoke test: run BreadETFRotation inside LumiBot's real backtesting
engine (PandasDataBacktesting) on SYNTHETIC daily data. Verifies wiring:
risk-engine approvals, ledger fills, sizing caps. NOT performance evidence.

Usage: BREADMAKER_DATA_DIR=<tmp> python offline_smoke_backtest.py <repo_root>
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(repo), str(repo / "engines" / "lumibot")]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def make(sym_drifts: dict[str, float], start="2021-01-04", n=600) -> dict:
    from lumibot.entities import Asset, Data
    idx = pd.bdate_range(start, periods=n, tz="America/New_York") + pd.Timedelta(hours=16)
    out = {}
    for i, (sym, mu) in enumerate(sym_drifts.items()):
        rng = np.random.default_rng(100 + i)
        close = 100 * np.exp(np.cumsum(rng.normal(mu, 0.01, n)))
        df = pd.DataFrame({"open": close * (1 + rng.normal(0, 0.001, n)), "close": close,
                           "volume": rng.integers(1e6, 5e6, n).astype(float)}, index=idx)
        df["high"] = df[["open", "close"]].max(axis=1) * 1.003
        df["low"] = df[["open", "close"]].min(axis=1) * 0.997
        a = Asset(sym, asset_type="stock")
        out[a] = Data(a, df[["open", "high", "low", "close", "volume"]], timestep="day")
    return out


def main() -> int:
    from lumibot.backtesting import PandasDataBacktesting

    from bread_lumibot.strategy import BreadETFRotation
    from breadmaker.config import load_settings
    from breadmaker.db import connect
    from breadmaker.ledger import Ledger

    universe = {"SPY": 0.0006, "QQQ": 0.0008, "TLT": -0.0004, "GLD": 0.0003}
    data = make(universe)
    BreadETFRotation.run_backtest(
        PandasDataBacktesting, datetime(2022, 1, 3), datetime(2023, 4, 28), pandas_data=data,
        benchmark_asset=None, show_plot=False, save_tearsheet=False, show_tearsheet=False,
        show_indicators=False, save_logfile=False, budget=100_000,
        parameters={"universe": list(universe), "rebalance_weekday": 0}, quiet_logs=True,
    )
    s = load_settings()
    L = Ledger(connect(s.db_path))
    fills = L.fills("lumibot", limit=10000)
    summ = L.summary("lumibot")
    max_notional = max((f["qty"] * f["price"] for f in fills if f["side"] == "buy"), default=0)
    events = [dict(r) for r in connect(s.db_path).execute("SELECT rule, COUNT(*) n FROM risk_events GROUP BY rule")]
    result = {"SYNTHETIC_DATA": True, "n_fills": len(fills), "virtual_equity_at_cost": summ["equity"],
              "max_single_buy_notional": max_notional, "positions": [p["symbol"] for p in summ["positions"]],
              "risk_events": events}
    print("SMOKE_RESULT " + json.dumps(result, default=str))
    return 0 if len(fills) > 0 and max_notional <= 0.10 * 1000 * 1.25 else 1  # equity may grow above $1000


if __name__ == "__main__":
    os.environ.setdefault("BREADMAKER_DATA_DIR", "/tmp/bm_lb_smoke")
    raise SystemExit(main())
