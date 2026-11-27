"""Minimal, honest bar-based backtester used for cross-engine validation.

Rules:
* Signal at bar t uses data up to and including bar t's close.
* Orders execute at bar t+1's OPEN with adverse spread/slippage and fees.
* Long-only, no leverage: exposure is clipped to [0, max_exposure].
* Trades smaller than `min_trade_value` are skipped (avoids fee churn).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .costs import CostModel
from .metrics import summarize


@dataclass
class BacktestResult:
    equity: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, target: pd.Series, costs: CostModel, initial_capital: float = 1000.0,
                 periods_per_year: float = 252, max_exposure: float = 1.0,
                 min_trade_value: float = 1.0, rebalance_threshold: float = 0.05) -> BacktestResult:
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"df needs columns {required}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("df index must be sorted ascending (chronological)")
    target = target.reindex(df.index).fillna(0.0).clip(0.0, max_exposure)

    opens = df["open"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    tgt = target.to_numpy(float)
    n = len(df)
    cash = initial_capital
    qty = 0.0
    entry_cost = 0.0
    eq = np.empty(n)
    expo = np.empty(n)
    trades = []
    total_costs = 0.0

    for i in range(n):
        # Execute decision from previous bar at this bar's open.
        if i > 0:
            px = opens[i]
            equity_open = cash + qty * px
            desired_value = tgt[i - 1] * equity_open
            cur_value = qty * px
            diff = desired_value - cur_value
            full_exit = tgt[i - 1] == 0 and qty > 0
            if full_exit or (abs(diff) >= min_trade_value and abs(diff) >= rebalance_threshold * max(equity_open, 1e-9)):
                side = "buy" if diff > 0 else "sell"
                fpx = costs.fill_price(side, px)
                if side == "buy":
                    # spend at most available cash, including fee
                    trade_qty = min(diff, cash / (1 + costs.fee_bps / 1e4)) / fpx
                else:
                    trade_qty = qty if full_exit else min(qty, -diff / px)
                if trade_qty > 0:
                    notional = trade_qty * fpx
                    fee = costs.fee(notional)
                    slip_cost = abs(fpx - px) * trade_qty
                    total_costs += fee + slip_cost
                    if side == "buy":
                        cash -= notional + fee
                        entry_cost += notional + fee
                        qty += trade_qty
                        trades.append({"ts": df.index[i], "side": side, "qty": trade_qty, "price": fpx, "fee": fee, "pnl": np.nan})
                    else:
                        frac = trade_qty / qty
                        cost_part = entry_cost * frac
                        proceeds = notional - fee
                        cash += proceeds
                        qty -= trade_qty
                        entry_cost -= cost_part
                        trades.append({"ts": df.index[i], "side": side, "qty": trade_qty, "price": fpx, "fee": fee,
                                       "pnl": proceeds - cost_part})
                        if qty < 1e-12:
                            qty, entry_cost = 0.0, 0.0
        eq[i] = cash + qty * closes[i]
        expo[i] = (qty * closes[i]) / eq[i] if eq[i] > 0 else 0.0

    equity = pd.Series(eq, index=df.index, name="equity")
    tdf = pd.DataFrame(trades, columns=["ts", "side", "qty", "price", "fee", "pnl"])
    closed = tdf["pnl"].dropna() if not tdf.empty else pd.Series(dtype=float)
    metrics = summarize(equity, periods_per_year, trade_pnls=closed, costs_paid=total_costs)
    metrics["exposure_avg"] = float(np.mean(expo))
    return BacktestResult(equity, pd.Series(expo, index=df.index), tdf, metrics)


def walk_forward_splits(n: int, n_splits: int = 5, min_train: int = 252, test_size: int | None = None,
                        gap: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window chronological splits with a purge `gap` between train and test."""
    if test_size is None:
        test_size = (n - min_train - gap) // n_splits
    if test_size <= 0:
        raise ValueError("not enough data for walk-forward splits")
    splits = []
    for k in range(n_splits):
        test_start = min_train + gap + k * test_size
        test_end = min(test_start + test_size, n)
        if test_start >= n:
            break
        train_idx = np.arange(0, test_start - gap)
        test_idx = np.arange(test_start, test_end)
        splits.append((train_idx, test_idx))
    return splits


def holdout_split(df: pd.DataFrame, holdout_frac: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split; the final `holdout_frac` is untouched out-of-sample data."""
    cut = int(len(df) * (1 - holdout_frac))
    return df.iloc[:cut], df.iloc[cut:]
