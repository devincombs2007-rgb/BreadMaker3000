"""Independent virtual portfolio accounting.

Each engine has its own isolated ledger keyed by ``engine``. Broker paper
accounts (e.g. Alpaca's $100k paper account) are larger than the $1,000 virtual
budget, so this ledger - not the broker balance - is the source of truth for
engine performance.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

EPS = 1e-9


class LedgerError(ValueError):
    pass


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_cost


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- portfolios -------------------------------------------------------
    def ensure_portfolio(self, engine: str, asset_class: str, initial_capital: float) -> None:
        if initial_capital <= 0:
            raise LedgerError("initial capital must be positive")
        self.conn.execute(
            "INSERT OR IGNORE INTO portfolios(engine, asset_class, initial_capital) VALUES (?,?,?)",
            (engine, asset_class, initial_capital),
        )

    def initial_capital(self, engine: str) -> float:
        row = self.conn.execute("SELECT initial_capital FROM portfolios WHERE engine=?", (engine,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown portfolio {engine!r}")
        return float(row[0])

    def engines(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT engine FROM portfolios ORDER BY engine")]

    # ---- fills ------------------------------------------------------------
    def record_fill(
        self,
        engine: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        client_order_id: str,
        ts: str | None = None,
        source: str = "paper",
    ) -> bool:
        """Record a fill. Returns False if this client_order_id was already recorded (idempotent)."""
        if side not in ("buy", "sell"):
            raise LedgerError("side must be buy or sell")
        if qty <= 0 or price <= 0 or fee < 0:
            raise LedgerError("qty and price must be positive; fee non-negative")
        if self.conn.execute(
            "SELECT 1 FROM fills WHERE engine=? AND client_order_id=?", (engine, client_order_id)
        ).fetchone():
            return False
        if side == "buy" and qty * price + fee > self.cash(engine) + EPS:
            raise LedgerError("insufficient virtual cash (no leverage allowed)")
        if side == "sell":
            pos = self.positions(engine).get(symbol)
            if pos is None or qty > pos.qty + EPS:
                raise LedgerError("cannot sell more than held (no shorting allowed)")
        self.conn.execute(
            "INSERT INTO fills(engine, ts, symbol, side, qty, price, fee, client_order_id, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (engine, ts or utcnow(), symbol, side, qty, price, fee, client_order_id, source),
        )
        return True

    def recorded_for_order(self, engine: str, order_id: str) -> tuple[float, float]:
        """(qty, notional) already booked for fills of one broker order (client ids '<order_id>#n')."""
        r = self.conn.execute(
            "SELECT COALESCE(SUM(qty),0), COALESCE(SUM(qty*price),0), COUNT(*) FROM fills "
            "WHERE engine=? AND (client_order_id=? OR client_order_id LIKE ?)",
            (engine, order_id, order_id + "#%")).fetchone()
        return float(r[0]), float(r[1])

    def fill_count_for_order(self, engine: str, order_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM fills WHERE engine=? AND (client_order_id=? OR client_order_id LIKE ?)",
            (engine, order_id, order_id + "#%")).fetchone()[0]

    def fills(self, engine: str | None = None, limit: int = 1000) -> list[dict]:
        q = "SELECT * FROM fills" + (" WHERE engine=?" if engine else "") + " ORDER BY ts DESC, id DESC LIMIT ?"
        args = (engine, limit) if engine else (limit,)
        return [dict(r) for r in self.conn.execute(q, args)]

    # ---- derived state ----------------------------------------------------
    def _replay(self, engine: str) -> tuple[float, dict[str, Position], float, float]:
        cash = self.initial_capital(engine)
        positions: dict[str, Position] = {}
        realized = 0.0
        fees = 0.0
        for r in self.conn.execute(
            "SELECT symbol, side, qty, price, fee FROM fills WHERE engine=? ORDER BY ts, id", (engine,)
        ):
            sym, side, qty, price, fee = r
            fees += fee
            pos = positions.get(sym, Position(sym, 0.0, 0.0))
            if side == "buy":
                cash -= qty * price + fee
                new_qty = pos.qty + qty
                pos.avg_cost = (pos.cost_basis + qty * price) / new_qty
                pos.qty = new_qty
            else:
                cash += qty * price - fee
                realized += qty * (price - pos.avg_cost)
                pos.qty -= qty
            if pos.qty > EPS:
                positions[sym] = pos
            else:
                positions.pop(sym, None)
        return cash, positions, realized - fees, fees

    def closed_trade_pnls(self, engine: str) -> list[float]:
        """Net P&L of each sell fill against average cost (entry fees allocated pro rata)."""
        pnls: list[float] = []
        book: dict[str, list[float]] = {}  # symbol -> [qty, total_cost_incl_fees]
        for sym, side, qty, price, fee in self.conn.execute(
                "SELECT symbol, side, qty, price, fee FROM fills WHERE engine=? ORDER BY ts, id", (engine,)):
            q, c = book.get(sym, [0.0, 0.0])
            if side == "buy":
                book[sym] = [q + qty, c + qty * price + fee]
            elif q > EPS:
                cost_part = c * min(qty / q, 1.0)
                pnls.append(qty * price - fee - cost_part)
                book[sym] = [q - qty, c - cost_part]
        return pnls

    def cash(self, engine: str) -> float:
        return self._replay(engine)[0]

    def positions(self, engine: str) -> dict[str, Position]:
        return self._replay(engine)[1]

    def summary(self, engine: str, marks: dict[str, float] | None = None) -> dict:
        """Mark-to-market summary. Missing marks fall back to cost (flagged)."""
        marks = marks or {}
        cash, positions, realized_net, fees = self._replay(engine)
        mv = 0.0
        unrealized = 0.0
        unmarked = []
        pos_out = []
        for p in positions.values():
            px = marks.get(p.symbol)
            if px is None:
                unmarked.append(p.symbol)
                px = p.avg_cost
            mv += p.qty * px
            unrealized += p.qty * (px - p.avg_cost)
            pos_out.append({"symbol": p.symbol, "qty": p.qty, "avg_cost": p.avg_cost, "mark": px,
                            "value": p.qty * px, "unrealized": p.qty * (px - p.avg_cost)})
        equity = cash + mv
        init = self.initial_capital(engine)
        return {
            "engine": engine,
            "initial_capital": init,
            "cash": cash,
            "market_value": mv,
            "equity": equity,
            "total_pnl": equity - init,
            "total_return": equity / init - 1,
            "realized_pnl_net_of_fees": realized_net,
            "unrealized_pnl": unrealized,
            "fees_paid": fees,
            "positions": pos_out,
            "unmarked_symbols": unmarked,
        }

    # ---- equity history ---------------------------------------------------
    def snapshot(self, engine: str, equity: float, cash: float, ts: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots(engine, ts, equity, cash) VALUES (?,?,?,?)",
            (engine, ts or utcnow(), equity, cash),
        )

    def equity_history(self, engine: str) -> list[tuple[str, float]]:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT ts, equity FROM equity_snapshots WHERE engine=? ORDER BY ts", (engine,))]
