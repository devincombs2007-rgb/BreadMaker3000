"""Offline smoke test: run the REAL Freqtrade backtesting engine on SYNTHETIC data.

Purpose: prove the strategy loads, produces signals, respects sizing, and the
config is valid inside Freqtrade - without network access. Exchange market
metadata is stubbed. Results are NOT performance evidence.

Usage: python offline_smoke_backtest.py <repo_root> [--freqai]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))

from breadmaker.data import synthetic_ohlcv  # noqa: E402

PAIRS = ["BTC/USD", "ETH/USD"]
FREQAI = "--freqai" in sys.argv
STRATEGY = "BreadFreqAIV1" if FREQAI else "BreadTrendV1"


def fake_markets():
    m = {}
    for p in PAIRS:
        base, quote = p.split("/")
        m[p] = {"id": p.replace("/", ""), "symbol": p, "base": base, "quote": quote, "active": True,
                "spot": True, "swap": False, "future": False, "option": False, "margin": False,
                "contract": False, "type": "spot", "linear": None, "inverse": None, "contractSize": None,
                "precision": {"amount": 1e-8, "price": 0.01}, "limits": {"amount": {"min": 1e-5, "max": None},
                "cost": {"min": 0.5, "max": None}, "price": {"min": None, "max": None},
                "leverage": {"min": 1, "max": 1}}, "info": {}, "taker": 0.004, "maker": 0.0025}
    return m


def main() -> int:
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting

    work = Path(tempfile.mkdtemp(prefix="ft_smoke_"))
    ud = work / "user_data"
    (ud / "data" / "kraken").mkdir(parents=True)
    (ud / "strategies").mkdir()
    src = repo / "engines" / "freqtrade" / "user_data"
    for f in (f"{STRATEGY}.py", "bread_risk_gate.py"):
        (ud / "strategies" / f).write_text((src / "strategies" / f).read_text())
    for i, p in enumerate(PAIRS):
        raw = synthetic_ohlcv(n=3000, seed=11 + i, s0=100 * (i + 1))
        df = raw.reset_index(names="date")
        df[["date", "open", "high", "low", "close", "volume"]].to_feather(
            ud / "data" / "kraken" / f"{p.replace('/', '_')}-1h.feather")
        h4 = raw.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                     "volume": "sum"}).dropna().reset_index(names="date")
        h4.to_feather(ud / "data" / "kraken" / f"{p.replace('/', '_')}-4h.feather")

    cfg = json.loads((src / "config.json").read_text())
    if FREQAI:
        overlay = json.loads((src / "config.freqai.json").read_text())
        overlay.pop("api_server", None)
        cfg.update(overlay)
        cfg["freqai"]["train_period_days"] = 30
        cfg["freqai"]["backtest_period_days"] = 15
    cfg["exchange"]["pair_whitelist"] = PAIRS
    cfg["api_server"]["enabled"] = False
    cfg["db_url"] = "sqlite://"
    cfg_path = work / "config.json"
    cfg_path.write_text(json.dumps(cfg))

    args = {"config": [str(cfg_path)], "user_data_dir": str(ud), "strategy": STRATEGY,
            "timerange": "20220215-20220501" if FREQAI else None, "export": "none",
            "datadir": str(ud / "data" / "kraken")}
    if FREQAI:
        args["freqaimodel"] = "LightGBMRegressor"
    config = Configuration(args, RunMode.BACKTEST).get_config()
    config["export"] = "none"

    from freqtrade.exchange import Exchange
    with mock.patch.object(Exchange, "_load_async_markets", lambda *a, **k: fake_markets()), \
         mock.patch.object(Exchange, "reload_markets", lambda *a, **k: None), \
         mock.patch.object(Exchange, "markets", new_callable=mock.PropertyMock, return_value=fake_markets()), \
         mock.patch.object(Exchange, "validate_config", lambda *a, **k: None), \
         mock.patch.object(Exchange, "get_fee", lambda *a, **k: 0.004):
        bt = Backtesting(config)
        bt.start()
        res = bt.results
    strat = res["strategy"][STRATEGY]
    trades = strat["trades"]
    starting = strat["starting_balance"]
    max_stake = max((t["stake_amount"] for t in trades), default=0)
    summary = {
        "SYNTHETIC_DATA": True,
        "strategy": STRATEGY,
        "total_trades": strat["total_trades"],
        "starting_balance": starting,
        "final_balance": strat["final_balance"],
        "max_stake": max_stake,
        "max_stake_pct_of_start": max_stake / starting if starting else None,
        "any_short": any(t.get("is_short") for t in trades),
        "max_leverage": max((t.get("leverage", 1) for t in trades), default=1),
    }
    print("SMOKE_RESULT " + json.dumps(summary))
    ok = ((summary["total_trades"] > 0 or FREQAI) and not summary["any_short"] and summary["max_leverage"] == 1
          and summary["max_stake_pct_of_start"] <= 0.1 + 0.02)  # small tolerance: equity grows
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
