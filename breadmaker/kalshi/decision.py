"""Turn a forecast + executable quote into a simulated order - or, usually, into NO order.

A trade requires, after fees, that the CONSERVATIVE probability bound beats the executable ask by
`min_edge`. Size = fractional Kelly on the conservative probability, then capped by position,
event and total-exposure limits and by visible depth. Zero allocation is the default outcome.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime

from ..config import KalshiConfig
from ..forecasting.base import Forecast
from .markets import Market, Quote, taker_fee


@dataclass
class Decision:
    ticker: str
    side: str | None          # 'yes' | 'no' | None (no trade)
    contracts: int
    price: float | None
    fee: float
    edge_per_contract: float  # conservative probability - price - fee/contract
    expected_value: float     # using the central probability
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def kelly_fraction_binary(p: float, price: float) -> float:
    """Full-Kelly fraction of bankroll for a $1 binary bought at `price` with win prob p."""
    if price <= 0 or price >= 1:
        return 0.0
    return max(0.0, (p - price) / (1 - price))


def decide(market: Market, quote: Quote, fc: Forecast, cfg: KalshiConfig, equity: float, cash: float,
           event_exposure: float, total_exposure: float, now: datetime, fee_rate: float) -> Decision:
    def no(reason: str) -> Decision:
        return Decision(market.ticker, None, 0, None, 0.0, 0.0, 0.0, reason)

    if market.status not in ("active", "open"):
        return no(f"market status {market.status!r}")
    if market.close_time is None or (market.close_time - now).total_seconds() < cfg.min_hours_to_close * 3600:
        return no("too close to close (or no close time)")
    if quote.spread is None:
        return no("one-sided or empty order book")
    if quote.spread > cfg.max_spread:
        return no(f"spread {quote.spread:.2f} > {cfg.max_spread:.2f}")

    candidates = []
    for side, p_cons, p_mid, price, size in (("yes", fc.p_low, fc.p, quote.yes_ask, quote.yes_ask_size),
                                             ("no", 1 - fc.p_high, 1 - fc.p, quote.no_ask, quote.no_ask_size)):
        if price is None or size <= 0 or not (0 < price < 1):
            continue
        f = cfg.kelly_fraction * kelly_fraction_binary(p_cons, price)
        budget = min(f * equity, cfg.max_position_pct * equity,
                     cfg.max_event_exposure_pct * equity - event_exposure,
                     cfg.max_total_exposure_pct * equity - total_exposure, cash)
        c = int(min(math.floor(max(budget, 0) / price), size))
        while c > 0 and c * price + taker_fee(c, price, fee_rate) > cash:
            c -= 1
        if c < 1:
            candidates.append((side, 0, price, 0.0, p_cons - price, 0.0, "size rounds to 0 contracts under limits"))
            continue
        fee = taker_fee(c, price, fee_rate)
        edge = p_cons - price - fee / c
        ev = c * (p_mid - price) - fee
        candidates.append((side, c, price, fee, edge, ev, ""))

    if not candidates:
        return no("no executable ask")
    best = max(candidates, key=lambda x: x[4])
    side, c, price, fee, edge, ev, why = best
    if c == 0:
        return Decision(market.ticker, None, 0, price, 0.0, round(edge, 4), 0.0,
                        f"best side {side}: edge {edge:+.3f} but {why}")
    if edge < cfg.min_edge:
        return Decision(market.ticker, None, 0, price, 0.0, round(edge, 4), 0.0,
                        f"best side {side}: conservative edge {edge:+.3f} after fees < min {cfg.min_edge:.3f}")
    return Decision(market.ticker, side, c, price, fee, round(edge, 4), round(ev, 4), "edge after fees")
