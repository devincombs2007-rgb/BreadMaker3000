"""Market and order-book parsing, executable prices and fees.

Kalshi binary contracts pay $1 if the outcome occurs. The order book lists only BIDS for each side:
a YES ask at price p exists exactly when someone bids for NO at (1 - p). So:
    best YES ask = 1 - best NO bid  (size = size of that NO bid), and vice versa.
Prices are normalised to dollars; both legacy integer-cent fields and *_dollars string fields are
accepted because the API has been migrating between them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import KalshiConfig


def price_field(d: dict, key: str) -> float | None:
    if f"{key}_dollars" in d and d[f"{key}_dollars"] not in (None, ""):
        return float(d[f"{key}_dollars"])
    v = d.get(key)
    return None if v is None else float(v) / 100.0


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass
class Market:
    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    status: str
    close_time: datetime | None
    strike_type: str | None
    floor_strike: float | None
    cap_strike: float | None
    rules_primary: str
    category: str = ""
    result: str = ""
    yes_bid: float | None = None
    yes_ask: float | None = None
    volume: float = 0.0
    open_interest: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict, category: str = "", series_ticker: str = "") -> "Market":
        ev = d.get("event_ticker", "")
        return cls(
            ticker=d["ticker"], event_ticker=ev,
            series_ticker=series_ticker or d.get("series_ticker") or ev.split("-")[0],
            title=d.get("title", ""), status=d.get("status", ""), close_time=parse_time(d.get("close_time")),
            strike_type=d.get("strike_type"), floor_strike=_f(d.get("floor_strike")), cap_strike=_f(d.get("cap_strike")),
            rules_primary=d.get("rules_primary", "") or "", category=category or d.get("category", "") or "",
            result=d.get("result", "") or "", yes_bid=price_field(d, "yes_bid"), yes_ask=price_field(d, "yes_ask"),
            volume=_f(d.get("volume")) or 0.0, open_interest=_f(d.get("open_interest")) or 0.0, raw=d)

    def outcome_yes(self, value: float) -> bool | None:
        """Whether a settlement value makes this market resolve YES, from the strike definition."""
        st, lo, hi = self.strike_type, self.floor_strike, self.cap_strike
        if st in ("greater", "greater_than") and lo is not None:
            return value > lo
        if st in ("greater_or_equal",) and lo is not None:
            return value >= lo
        if st in ("less", "less_than") and hi is not None:
            return value < hi
        if st in ("less_or_equal",) and hi is not None:
            return value <= hi
        if st == "between" and lo is not None and hi is not None:
            return lo <= value <= hi
        return None  # custom/structured/functional: not machine-interpretable -> caller must reject


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Quote:
    yes_bid: float | None
    yes_bid_size: float
    yes_ask: float | None
    yes_ask_size: float
    no_bid: float | None
    no_bid_size: float
    no_ask: float | None
    no_ask_size: float
    fetched_at: datetime

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid


def _levels(book: dict, side: str) -> list[tuple[float, float]]:
    if book.get(f"{side}_dollars"):
        return [(float(p), float(q)) for p, q in book[f"{side}_dollars"]]
    return [(float(p) / 100.0, float(q)) for p, q in (book.get(side) or [])]


def parse_orderbook(payload: dict, fetched_at: datetime | None = None) -> Quote:
    book = payload.get("orderbook") or payload.get("orderbook_fp") or {}
    yes, no = _levels(book, "yes"), _levels(book, "no")
    by = max(yes, default=None)
    bn = max(no, default=None)
    return Quote(
        yes_bid=by[0] if by else None, yes_bid_size=by[1] if by else 0.0,
        yes_ask=round(1 - bn[0], 4) if bn else None, yes_ask_size=bn[1] if bn else 0.0,
        no_bid=bn[0] if bn else None, no_bid_size=bn[1] if bn else 0.0,
        no_ask=round(1 - by[0], 4) if by else None, no_ask_size=by[1] if by else 0.0,
        fetched_at=fetched_at or datetime.now(timezone.utc))


def taker_fee(contracts: int, price: float, rate: float = 0.07) -> float:
    """Kalshi taker fee for one order: rate x C x P x (1-P), rounded UP to the next cent.
    Kalshi's newer rounding (sub-cent accounting) can only be equal or lower, so this is conservative."""
    if contracts <= 0:
        return 0.0
    raw = rate * contracts * price * (1 - price)
    return math.ceil(round(raw * 100, 9)) / 100.0


def fee_rate(cfg: KalshiConfig, series_ticker: str) -> float:
    return max(cfg.taker_fee_rate, float(cfg.fee_rate_overrides.get(series_ticker, 0.0)))
