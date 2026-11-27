"""Capital-preserving position sizing for the simulated bankroll (default $25).

stake = equity x kelly_fraction x KellyBinary(p_conservative, price)
              x uncertainty_shrink x drawdown_throttle
then capped by: per-market, per-event, per-correlation-group, total exposure, cash reserve, depth,
open-position count. Every reduction is recorded so the dashboard can explain the allocation.

* uncertainty_shrink = max(0, 1 - interval_width / uncertainty_width_cap): wide intervals -> small bets.
* drawdown_throttle  = max(0, 1 - drawdown / drawdown_throttle_full): size falls linearly to 0 as the
  book approaches its drawdown limit (capital preservation before the hard halt triggers).
* correlation group  = outcomes that move together (e.g. all same-day weather markets, all legs of one
  game) share one exposure budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .markets import taker_fee


@dataclass
class SizeResult:
    contracts: int
    notes: list[str] = field(default_factory=list)
    stake_budget: float = 0.0


def kelly_binary(p: float, price: float) -> float:
    if not (0 < price < 1):
        return 0.0
    return max(0.0, (p - price) / (1 - price))


def size(cfg, p_cons: float, interval_width: float, price: float, depth: float, fee_rate: float,
         equity: float, cash: float, peak_equity: float, event_exposure: float, group_exposure: float,
         total_exposure: float, open_positions: int) -> SizeResult:
    notes = []
    if not (cfg.min_price <= price <= cfg.max_price):
        return SizeResult(0, [f"price {price:.2f} outside [{cfg.min_price:.2f}, {cfg.max_price:.2f}]"])
    if open_positions >= cfg.max_open_positions:
        return SizeResult(0, [f"{open_positions} open positions (max {cfg.max_open_positions})"])
    f = cfg.kelly_fraction * kelly_binary(p_cons, price)
    shrink = max(0.0, 1 - interval_width / cfg.uncertainty_width_cap)
    dd = max(0.0, 1 - equity / peak_equity) if peak_equity > 0 else 0.0
    throttle = max(0.0, 1 - dd / cfg.drawdown_throttle_full)
    if shrink < 1:
        notes.append(f"uncertainty shrink x{shrink:.2f} (interval {interval_width:.2f})")
    if throttle < 1:
        notes.append(f"drawdown throttle x{throttle:.2f} (drawdown {dd:.1%})")
    caps = {
        "kelly": f * shrink * throttle * equity,
        "per-market cap": cfg.max_position_pct * equity,
        "per-event cap": cfg.max_event_exposure_pct * equity - event_exposure,
        "correlation-group cap": cfg.max_group_exposure_pct * equity - group_exposure,
        "total-exposure cap": cfg.max_total_exposure_pct * equity - total_exposure,
        "cash reserve": cash - cfg.cash_reserve_pct * equity,
    }
    binding = min(caps, key=caps.get)
    budget = max(0.0, caps[binding])
    if binding != "kelly":
        notes.append(f"limited by {binding}")
    c = int(min(math.floor(budget / price), depth))
    if c < math.floor(budget / price):
        notes.append("limited by visible depth")
    while c > 0 and c * price + taker_fee(c, price, fee_rate) > cash - cfg.cash_reserve_pct * equity + 1e-9:
        c -= 1
    return SizeResult(c, notes, round(budget, 4))
