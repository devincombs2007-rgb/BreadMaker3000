"""Backtest the deployed Freqtrade strategy in the REAL Freqtrade engine on REAL downloaded candles.

    python engines/freqtrade/research_backtest.py --data research_data/crypto --out research_results

* Candles come from research_data (see breadmaker/research/data_sources.py; venue in manifest).
* Exchange metadata is stubbed (no keys, no network needed at backtest time); pairs are relabelled
  X/USD regardless of the data venue's quote (USD/USDT/USDC) - a small basis difference.
* Costs: fee per side = Kraken taker 0.40% + 0.15% spread/slippage = 0.55% (1x), and 1.10% (2x stress).
  Freqtrade backtests fill at candle open; slippage is only approximated via the fee.
* Benchmark: equal-weight buy-and-hold of the same pairs over the same window, same entry/exit fees.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
FEE_1X = 0.0055


def fake_markets(pairs):
    m = {}
    for p in pairs:
        base, quote = p.split("/")
        m[p] = {"id": p.replace("/", ""), "symbol": p, "base": base, "quote": quote, "active": True, "spot": True,
                "swap": False, "future": False, "option": False, "margin": False, "contract": False, "type": "spot",
                "linear": None, "inverse": None, "contractSize": None, "precision": {"amount": 1e-8, "price": 0.01},
                "limits": {"amount": {"min": 1e-6, "max": None}, "cost": {"min": 0.5, "max": None},
                           "price": {"min": None, "max": None}, "leverage": {"min": 1, "max": 1}},
                "info": {}, "taker": 0.004, "maker": 0.0025}
    return m


def run(data_dir: Path, fee: float, strategy: str = "BreadTrendV1") -> dict:
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode
    from freqtrade.exchange import Exchange
    from freqtrade.optimize.backtesting import Backtesting

    work = Path(tempfile.mkdtemp(prefix="ft_research_"))
    ud = work / "user_data"
    (ud / "data" / "kraken").mkdir(parents=True)
    (ud / "strategies").mkdir()
    src = REPO / "engines" / "freqtrade" / "user_data"
    for f in (f"{strategy}.py", "bread_risk_gate.py"):
        shutil.copy(src / "strategies" / f, ud / "strategies" / f)
    pairs, first, last = [], None, None
    for f in sorted(data_dir.glob("*_USD-1h.feather")):
        base = f.name.split("_")[0]
        shutil.copy(f, ud / "data" / "kraken" / f"{base}_USD-1h.feather")
        d = pd.read_feather(f)
        first = max(first, d["date"].min()) if first is not None else d["date"].min()
        last = min(last, d["date"].max()) if last is not None else d["date"].max()
        pairs.append(f"{base}/USD")
    if not pairs:
        return {"skipped": "no 1h crypto data"}
    cfg = json.loads((src / "config.json").read_text())
    cfg["exchange"]["pair_whitelist"] = pairs
    cfg["api_server"]["enabled"] = False
    cfg["db_url"] = "sqlite://"
    cfg["fee"] = fee
    (work / "config.json").write_text(json.dumps(cfg))
    args = {"config": [str(work / "config.json")], "user_data_dir": str(ud), "strategy": strategy,
            "timerange": None, "export": "none", "datadir": str(ud / "data" / "kraken"), "fee": fee}
    config = Configuration(args, RunMode.BACKTEST).get_config()
    config["export"] = "none"
    mk = fake_markets(pairs)
    with mock.patch.object(Exchange, "_load_async_markets", lambda *a, **k: mk), \
         mock.patch.object(Exchange, "reload_markets", lambda *a, **k: None), \
         mock.patch.object(Exchange, "markets", new_callable=mock.PropertyMock, return_value=mk), \
         mock.patch.object(Exchange, "validate_config", lambda *a, **k: None), \
         mock.patch.object(Exchange, "get_fee", lambda *a, **k: fee):
        bt = Backtesting(config)
        bt.start()
        res = bt.results["strategy"][strategy]
    trades = res["trades"]
    start, end = pd.Timestamp(res["backtest_start"]), pd.Timestamp(res["backtest_end"])
    bench = []
    for p in pairs:
        d = pd.read_feather(ud / "data" / "kraken" / f"{p.replace('/', '_')}-1h.feather").set_index("date")
        d = d.loc[(d.index >= start.tz_localize(d.index.tz) if start.tzinfo is None else d.index >= start)]
        if len(d):
            bench.append((d["close"].iloc[-1] * (1 - fee)) / (d["open"].iloc[0] * (1 + fee)) - 1)
    pnl = pd.Series([t["profit_abs"] for t in trades], dtype=float)
    return {
        "strategy": strategy, "fee_per_side": fee, "pairs": pairs,
        "window": [str(start), str(end)], "trades": len(trades),
        "final_balance": res["final_balance"], "total_return": res["final_balance"] / res["starting_balance"] - 1,
        "max_drawdown_account": res.get("max_drawdown_account"),
        "sharpe": res.get("sharpe"), "sortino": res.get("sortino"),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()) if (pnl < 0).any() else None,
        "fees_paid_est": float(sum(t["stake_amount"] for t in trades) * fee * 2),
        "max_stake": max((t["stake_amount"] for t in trades), default=0),
        "benchmark_equal_weight_buy_hold_return": float(sum(bench) / len(bench)) if bench else None,
        "benchmark_note": "fully invested; strategy is capped at 5 x 10% = 50% exposure",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="research_data/crypto")
    ap.add_argument("--out", default="research_results")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {f"cost{k}x": run(Path(a.data), FEE_1X * k) for k in (1, 2)}
    (out / "freqtrade_results.json").write_text(json.dumps(results, indent=2, default=str))
    lines = ["## Freqtrade BreadTrendV1 - real Freqtrade engine, real 1h candles", "",
             "| run | window | trades | total return | max DD | Sharpe | win rate | profit factor | B&H (same pairs) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k, r in results.items():
        if "skipped" in r:
            lines.append(f"| {k} | skipped: {r['skipped']} |")
            continue
        f = lambda x, pct=True: "n/a" if x is None else (f"{x:+.1%}" if pct else f"{x:.2f}")  # noqa: E731
        lines.append(f"| {k} (fee {r['fee_per_side']:.2%}/side) | {r['window'][0][:10]} → {r['window'][1][:10]} | "
                     f"{r['trades']} | {f(r['total_return'])} | {f(-r['max_drawdown_account'] if r['max_drawdown_account'] is not None else None)} | "
                     f"{f(r['sharpe'], False)} | {f(r['win_rate'])} | {f(r['profit_factor'], False)} | "
                     f"{f(r['benchmark_equal_weight_buy_hold_return'])} |")
    md = "\n".join(lines) + "\n"
    (out / "freqtrade_report.md").write_text(md)
    print(md)
    print("FT_RESULTS " + json.dumps(results, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
