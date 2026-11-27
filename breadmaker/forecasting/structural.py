"""Model-free combination analysis inside one Kalshi event (same underlying, same settlement).

1. Ladder (any 'greater' strikes K1 < K2 on the same event):
       YES(>K1) + NO(>K2) pays >= $1 in every state (S<=K1: 0+1, K1<S<=K2: 1+1, S>K2: 1+0).
   Locked profit if  yes_ask(K1) + no_ask(K2) + fees < 1.
   It also flags *incoherent* quotes: P(>K1) must be >= P(>K2).
2. Exhaustive integer brackets (only where the settlement value is a known integer, e.g. CLI degF):
       buying YES on every bracket pays exactly $1. Locked profit if sum(asks) + fees < 1.
   Coverage is verified from the strike fields; any gap, overlap or non-integer strike -> rejected.
Executable prices and sizes come from the order books; fees use the conservative taker formula.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..kalshi.markets import Market, Quote, taker_fee


@dataclass
class Combination:
    kind: str
    legs: list[tuple[str, str, float, float]]   # (ticker, side, ask, available size)
    contracts: int
    cost: float          # total incl. fees
    min_payout: float
    locked_profit: float
    note: str = ""


def ladder_opportunities(markets: list[Market], quotes: dict[str, Quote], fee_rate: float = 0.07,
                         min_profit: float = 0.01) -> tuple[list[Combination], list[str]]:
    greater = sorted([m for m in markets if m.strike_type in ("greater", "greater_or_equal")
                      and m.floor_strike is not None and m.ticker in quotes], key=lambda m: m.floor_strike)
    out, incoherent = [], []
    for i, lo in enumerate(greater):
        for hi in greater[i + 1:]:
            ql, qh = quotes[lo.ticker], quotes[hi.ticker]
            if ql.yes_bid is not None and qh.yes_ask is not None and qh.yes_bid is not None and ql.yes_ask is not None:
                if qh.yes_bid > ql.yes_ask:
                    incoherent.append(f"{hi.ticker} bid {qh.yes_bid:.2f} > {lo.ticker} ask {ql.yes_ask:.2f} "
                                      f"although P(>{hi.floor_strike}) <= P(>{lo.floor_strike})")
            if ql.yes_ask is None or qh.no_ask is None:
                continue
            size = int(min(ql.yes_ask_size, qh.no_ask_size))
            for c in range(size, 0, -1):
                cost = c * (ql.yes_ask + qh.no_ask) + taker_fee(c, ql.yes_ask, fee_rate) + taker_fee(c, qh.no_ask, fee_rate)
                if c * 1.0 - cost >= min_profit * c:
                    out.append(Combination("ladder", [(lo.ticker, "yes", ql.yes_ask, ql.yes_ask_size),
                                                      (hi.ticker, "no", qh.no_ask, qh.no_ask_size)],
                                           c, round(cost, 4), float(c), round(c - cost, 4)))
                    break
    return out, incoherent


def integer_bracket_cover(markets: list[Market]) -> tuple[bool, str]:
    """True if the markets partition all integers: one 'less', contiguous 'between's, one 'greater'."""
    less = [m for m in markets if m.strike_type in ("less", "less_or_equal")]
    greater = [m for m in markets if m.strike_type in ("greater", "greater_or_equal")]
    between = sorted([m for m in markets if m.strike_type == "between"], key=lambda m: m.floor_strike or 0)
    if len(less) != 1 or len(greater) != 1 or len(between) + 2 != len(markets):
        return False, "not exactly one lower tail, one upper tail and between-brackets"
    vals = [m.floor_strike for m in between + greater] + [m.cap_strike for m in between + less]
    if any(v is None or float(v) != int(v) for v in vals):
        return False, "non-integer or missing strikes"
    lo_t = less[0]
    top_of_low = lo_t.cap_strike - (1 if lo_t.strike_type == "less" else 0)
    expect = top_of_low + 1
    for m in between:
        if m.floor_strike != expect:
            return False, f"gap/overlap before {m.ticker}"
        expect = m.cap_strike + 1
    g = greater[0]
    bottom_of_high = g.floor_strike + (1 if g.strike_type == "greater" else 0)
    if bottom_of_high != expect:
        return False, f"gap/overlap before {g.ticker}"
    return True, "complete integer partition"


def bracket_sum_opportunity(markets: list[Market], quotes: dict[str, Quote], fee_rate: float = 0.07,
                            min_profit: float = 0.01) -> tuple[Combination | None, str]:
    ok, why = integer_bracket_cover(markets)
    if not ok:
        return None, why
    if any(m.ticker not in quotes or quotes[m.ticker].yes_ask is None for m in markets):
        return None, "missing executable YES ask on at least one bracket"
    size = int(min(quotes[m.ticker].yes_ask_size for m in markets))
    for c in range(size, 0, -1):
        cost = sum(c * quotes[m.ticker].yes_ask + taker_fee(c, quotes[m.ticker].yes_ask, fee_rate) for m in markets)
        if c - cost >= min_profit * c:
            legs = [(m.ticker, "yes", quotes[m.ticker].yes_ask, quotes[m.ticker].yes_ask_size) for m in markets]
            return Combination("bracket_sum", legs, c, round(cost, 4), float(c), round(c - cost, 4)), "ok"
    s = sum(quotes[m.ticker].yes_ask for m in markets)
    return None, f"sum of asks {s:.2f} + fees leaves no locked profit"
