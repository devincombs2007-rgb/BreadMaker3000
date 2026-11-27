"""Simulated Kalshi book on the shared ledger (engine 'kalshi', default bankroll $25).

Positions are keyed '<ticker>|yes' / '<ticker>|no'. Buys are recorded at the executable ask plus the
conservative taker fee. On settlement every open contract is closed at $1 (won) or $0 (lost), using
Kalshi's published market result only - never our own forecast.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..ledger import Ledger
from .decision import Decision

ENGINE = "kalshi"


class PaperBook:
    def __init__(self, conn: sqlite3.Connection, bankroll: float, engine: str = ENGINE):
        self.conn = conn
        self.engine = engine
        self.ledger = Ledger(conn)
        self.ledger.ensure_portfolio(engine, "prediction_markets", bankroll)

    def peak_equity(self) -> float:
        hist = [e for _, e in self.ledger.equity_history(self.engine)]
        return max(hist + [self.ledger.initial_capital(self.engine), self.equity_at_cost()])

    def cash(self) -> float:
        return self.ledger.cash(self.engine)

    def positions(self) -> dict:
        return self.ledger.positions(self.engine)

    def exposure(self, event_ticker: str | None = None) -> float:
        tot = 0.0
        for sym, pos in self.positions().items():
            if event_ticker is None or sym.split("|")[0].startswith(event_ticker + "-") or sym.split("|")[0] == event_ticker:
                tot += pos.cost_basis
        return tot

    def equity_at_cost(self) -> float:
        return self.cash() + self.exposure()

    def buy(self, d: Decision, prediction_id: int, ts: datetime | None = None) -> bool:
        if not d.side or d.contracts < 1:
            return False
        return self.ledger.record_fill(self.engine, f"{d.ticker}|{d.side}", "buy", d.contracts, d.price, d.fee,
                                       f"{self.engine}-pred-{prediction_id}-{d.side}", ts=(ts or datetime.now(timezone.utc)).isoformat(),
                                       source="paper")

    def settle(self, ticker: str, result: str, ts: datetime | None = None) -> float:
        """Close all positions in `ticker` given Kalshi result 'yes'/'no'. Returns payout."""
        if result not in ("yes", "no"):
            raise ValueError(f"unsettled or unknown result {result!r}")
        payout = 0.0
        for side in ("yes", "no"):
            sym = f"{ticker}|{side}"
            pos = self.positions().get(sym)
            if not pos:
                continue
            price = 1.0 if side == result else 0.0
            self.ledger.record_fill(self.engine, sym, "sell", pos.qty, price, 0.0, f"settle-{ticker}-{side}",
                                    ts=(ts or datetime.now(timezone.utc)).isoformat(), source="settlement")
            payout += pos.qty * price
        return payout

    def mark_to_market(self, bids: dict[str, float]) -> float:
        """Equity valuing each position at its current best bid (what could actually be sold)."""
        eq = self.cash()
        for sym, pos in self.positions().items():
            eq += pos.qty * bids.get(sym, 0.0)
        return eq
