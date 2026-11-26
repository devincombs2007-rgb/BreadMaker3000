"""Independent risk engine.

Strategies and ML models submit *intents*; only the risk engine decides whether
they may proceed. Limits come from central config (validated against hard
ceilings) and cannot be passed in by a strategy. The kill switch is a file on a
shared volume so every engine - including Freqtrade's container - can see it
without network calls, and it persists across restarts.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import RiskLimits

EPS = 1e-9


class KillSwitch:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def engaged(self) -> bool:
        return self.path.exists()

    def reason(self) -> str:
        try:
            return self.path.read_text().strip()
        except FileNotFoundError:
            return ""

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {reason}\n")

    def release(self) -> None:
        """Only the authenticated owner (dashboard/CLI) should call this."""
        self.path.unlink(missing_ok=True)


@dataclass
class OrderIntent:
    engine: str
    symbol: str
    side: str                 # 'buy' or 'sell'
    qty: float
    price: float              # reference price used for sizing
    client_order_id: str
    data_timestamp: datetime  # timestamp of the market data behind `price`
    stop_price: float | None = None  # required for buys (defines risk)


@dataclass
class PortfolioState:
    equity: float
    cash: float
    day_start_equity: float
    peak_equity: float
    positions_value: dict[str, float] = field(default_factory=dict)  # symbol -> market value
    positions_qty: dict[str, float] = field(default_factory=dict)


@dataclass
class Decision:
    approved: bool
    reasons: list[str]
    max_qty: float = 0.0


def size_position(equity: float, price: float, stop_price: float, limits: RiskLimits,
                  existing_value: float = 0.0, cash: float | None = None) -> float:
    """Largest quantity satisfying per-trade risk, position cap and cash (no leverage)."""
    if price <= 0 or stop_price <= 0 or stop_price >= price or equity <= 0:
        return 0.0
    by_risk = equity * limits.max_risk_per_trade_pct / (price - stop_price)
    by_cap = max(0.0, equity * limits.max_position_pct - existing_value) / price
    qty = min(by_risk, by_cap)
    if cash is not None:
        qty = min(qty, max(0.0, cash) / price)
    return max(0.0, qty)


class RiskEngine:
    def __init__(self, limits: RiskLimits, kill_switch: KillSwitch, conn: sqlite3.Connection):
        self.limits = limits
        self.kill_switch = kill_switch
        self.conn = conn

    def log(self, engine: str, severity: str, rule: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO risk_events(ts, engine, severity, rule, detail) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), engine, severity, rule, detail),
        )

    # ---- portfolio-level checks -----------------------------------------
    def check_portfolio(self, engine: str, state: PortfolioState) -> list[str]:
        """Returns breached rules. Drawdown breach engages the kill switch."""
        breaches = []
        if state.day_start_equity > 0:
            daily = state.equity / state.day_start_equity - 1
            if daily <= -self.limits.daily_loss_halt_pct:
                breaches.append(f"daily_loss {daily:.2%} <= -{self.limits.daily_loss_halt_pct:.2%}")
        if state.peak_equity > 0:
            dd = state.equity / state.peak_equity - 1
            if dd <= -self.limits.max_drawdown_halt_pct:
                breaches.append(f"drawdown {dd:.2%} <= -{self.limits.max_drawdown_halt_pct:.2%}")
                self.kill_switch.engage(f"[{engine}] drawdown {dd:.2%}")
        for b in breaches:
            self.log(engine, "critical", "portfolio_limit", b)
        return breaches

    # ---- order-level checks ----------------------------------------------
    def evaluate(self, intent: OrderIntent, state: PortfolioState, now: datetime | None = None,
                 record: bool = True) -> Decision:
        now = now or datetime.now(timezone.utc)
        L = self.limits
        reasons: list[str] = []

        if intent.side not in ("buy", "sell"):
            reasons.append("invalid side")
        if not (intent.qty > 0) or not (intent.price > 0):
            reasons.append("invalid qty/price")
        ts = intent.data_timestamp
        if ts.tzinfo is None:
            reasons.append("data timestamp must be timezone-aware")
        else:
            age = (now - ts).total_seconds()
            if age > L.max_data_age_seconds:
                reasons.append(f"stale data ({age:.0f}s > {L.max_data_age_seconds}s)")
            if age < -60:
                reasons.append("data timestamp in the future")
        if self.conn.execute("SELECT 1 FROM order_ids WHERE engine=? AND client_order_id=?",
                             (intent.engine, intent.client_order_id)).fetchone():
            reasons.append("duplicate client_order_id")

        max_qty = 0.0
        if intent.side == "sell":
            held = state.positions_qty.get(intent.symbol, 0.0)
            if intent.qty > held + EPS:
                reasons.append("sell exceeds holdings (shorting disabled)")
            max_qty = held
            # Exits are always allowed (even under kill switch) so risk can be reduced.
        elif intent.side == "buy":
            if self.kill_switch.engaged():
                reasons.append(f"kill switch engaged: {self.kill_switch.reason()}")
            reasons.extend(self.check_portfolio(intent.engine, state))
            if intent.stop_price is None or not (0 < intent.stop_price < intent.price):
                reasons.append("buy requires a protective stop below price")
                max_qty = 0.0
            else:
                max_qty = size_position(state.equity, intent.price, intent.stop_price, L,
                                        state.positions_value.get(intent.symbol, 0.0), state.cash)
                if intent.qty > max_qty + EPS:
                    reasons.append(f"qty {intent.qty:.6g} exceeds risk-sized max {max_qty:.6g}")
            if intent.qty * intent.price > state.cash + EPS:
                reasons.append("insufficient cash (leverage disabled)")

        approved = not reasons
        if record:
            if approved:
                self.conn.execute("INSERT INTO order_ids(engine, client_order_id, ts) VALUES (?,?,?)",
                                  (intent.engine, intent.client_order_id, now.isoformat()))
            else:
                self.log(intent.engine, "warning", "order_rejected",
                         f"{intent.side} {intent.qty:.6g} {intent.symbol}: " + "; ".join(reasons))
        return Decision(approved, reasons, max_qty)

    def recent_events(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM risk_events ORDER BY id DESC LIMIT ?", (limit,))]
