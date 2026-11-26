"""Multi-asset daily backtest of the deployed LumiBot ETF logic on real data.

Replays engines/lumibot/bread_lumibot/logic.py (select_targets / plan_orders) unchanged:
* decision at close of day t using closes[:t]; fills at OPEN of t+1 with adverse costs;
* weekly rebalance on `rebalance_weekday`; stops checked every day at the close;
* long-only, cash account, $1,000 start;
* `portfolio_halts=True` also applies the live risk rules: 1% daily loss -> no buys the next
  session; 5% drawdown -> no new buys until an owner release (never released in research).
Timing is more conservative than live (live trades ~15 min before the close).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import RiskLimits
from ..costs import CostModel
from ..metrics import summarize

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engines" / "lumibot"))
from bread_lumibot.logic import Params, plan_orders, select_targets  # noqa: E402


@dataclass
class PortfolioResult:
    equity: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    halted_at: str | None = None


def run_rotation(opens: pd.DataFrame, closes: pd.DataFrame, costs: CostModel, initial: float = 1000.0,
                 limits: RiskLimits = RiskLimits(), params: Params = Params(), rebalance_weekday: int = 0,
                 portfolio_halts: bool = False, start: str | None = None) -> PortfolioResult:
    closes = closes.sort_index()
    opens = opens.reindex(closes.index)
    idx = closes.index
    warm = max(params.lookback, params.trend) + 1
    first = warm if start is None else max(warm, int(idx.searchsorted(pd.Timestamp(start, tz=idx.tz))))
    cash, qty, stops = initial, {}, {}
    pending_sells, pending_buys = [], []
    eq, ex, trades = [], [], []
    peak = day_start = initial
    dd_halt = False
    halted_at = None
    daily_halt_next = False
    total_costs = 0.0
    for i in range(first, len(idx)):
        o = opens.iloc[i]
        # 1) execute yesterday's decisions at today's open
        for sym, q in pending_sells:
            px = o.get(sym)
            q = min(q, qty.get(sym, 0.0))
            if q <= 0 or not np.isfinite(px):
                continue
            f = costs.fill_price("sell", px)
            fee = costs.fee(q * f)
            cash += q * f - fee
            total_costs += fee + (px - f) * q
            qty[sym] -= q
            if qty[sym] <= 1e-9:
                qty.pop(sym)
                stops.pop(sym, None)
            trades.append((idx[i], sym, "sell", q, f, fee))
        if not (dd_halt or daily_halt_next):
            for sym, q, stop in pending_buys:
                px = o.get(sym)
                if not np.isfinite(px) or px <= stop:
                    continue  # gapped through the stop: risk engine would reject (stop must be < price)
                f = costs.fill_price("buy", px)
                q = min(q, cash / (f * (1 + costs.fee_bps / 1e4)))
                if q * f < 1.0:
                    continue
                fee = costs.fee(q * f)
                cash -= q * f + fee
                total_costs += fee + (f - px) * q
                qty[sym] = qty.get(sym, 0.0) + q
                stops.setdefault(sym, stop)
                trades.append((idx[i], sym, "buy", q, f, fee))
        pending_sells, pending_buys = [], []
        # 2) mark to market at the close
        c = closes.iloc[i]
        mv = sum(q * c[s] for s, q in qty.items() if np.isfinite(c[s]))
        equity = cash + mv
        eq.append(equity)
        ex.append(mv / equity if equity > 0 else 0.0)
        # 3) risk state (as deployed)
        daily_halt_next = False
        if portfolio_halts:
            peak = max(peak, equity)
            if equity / peak - 1 <= -limits.max_drawdown_halt_pct and not dd_halt:
                dd_halt, halted_at = True, str(idx[i].date())
            if equity / day_start - 1 <= -limits.daily_loss_halt_pct:
                daily_halt_next = True
        day_start = equity
        # 4) decide at the close
        prices = {s: float(c[s]) for s in closes.columns if np.isfinite(c[s])}
        holdings = dict(qty)
        if idx[i].weekday() == rebalance_weekday and not dd_halt:
            targets = select_targets(closes.iloc[: i + 1].dropna(axis=1, how="all"), equity,
                                     limits.max_position_pct, limits.max_risk_per_trade_pct, params)
            sells, buys = plan_orders(targets, holdings, prices, stops)
            smap = {t.symbol: t.stop_price for t in targets}
            pending_sells = sells
            pending_buys = [(s, q, smap[s]) for s, q in buys]
        else:
            pending_sells = [(s, q) for s, q in holdings.items() if s in stops and prices.get(s, 1e18) < stops[s]]
    eq_s = pd.Series(eq, index=idx[first:], name="equity")
    tdf = pd.DataFrame(trades, columns=["ts", "symbol", "side", "qty", "price", "fee"])
    m = summarize(eq_s, 252, costs_paid=total_costs)
    m["exposure_avg"] = float(np.mean(ex)) if ex else float("nan")
    m["n_fills"] = len(tdf)
    m["turnover_x"] = float((tdf["qty"] * tdf["price"]).sum() / initial / max(len(eq_s) / 252, 1e-9)) if len(tdf) else 0.0
    return PortfolioResult(eq_s, pd.Series(ex, index=idx[first:]), tdf, m, halted_at)


def buy_and_hold(closes: pd.Series, initial: float = 1000.0) -> pd.Series:
    c = closes.dropna()
    return initial * c / c.iloc[0]


def exposure_matched(benchmark: pd.Series, exposure: pd.Series, initial: float = 1000.0) -> pd.Series:
    """Benchmark held at the strategy's *own* daily exposure (rest in 0% cash): isolates selection skill
    from simply holding less risk."""
    r = benchmark.pct_change().reindex(exposure.index).fillna(0.0)
    return initial * (1 + exposure.shift(1).fillna(0.0) * r).cumprod()
