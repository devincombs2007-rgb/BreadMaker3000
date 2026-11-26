"""LumiBot adapter for the ETF baseline. Paper trading only.

The LumiBot/Alpaca account is used for *execution simulation*; the BreadMaker
virtual ledger ($1,000) is the source of truth for this engine's performance,
and the BreadMaker RiskEngine must approve every order.
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import pandas as pd
from lumibot.strategies import Strategy

from breadmaker.config import load_settings
from breadmaker.costs import US_ETF_ALPACA
from breadmaker.db import connect
from breadmaker.ledger import Ledger
from breadmaker.orchestrator.heartbeat import beat
from breadmaker.risk import KillSwitch, OrderIntent, PortfolioState, RiskEngine

from .logic import DEFAULT_UNIVERSE, Params, plan_orders, select_targets

ENGINE = "lumibot"


class BreadETFRotation(Strategy):
    parameters = {"universe": DEFAULT_UNIVERSE, "rebalance_weekday": 0}

    def initialize(self, db_path: str | None = None, kill_switch_path: str | None = None):
        self.sleeptime = "1D"
        self.minutes_before_closing = 15
        settings = load_settings()
        self.limits = settings.risk
        self.conn = connect(db_path or settings.db_path)
        self.ledger = Ledger(self.conn)
        cfg = settings.engines[ENGINE]
        self.ledger.ensure_portfolio(ENGINE, cfg.asset_class, cfg.initial_capital)
        self.risk = RiskEngine(self.limits, KillSwitch(kill_switch_path or settings.kill_switch_path), self.conn)
        self.state_file = Path(settings.data_dir) / "lumibot_state.json"
        self.p = Params()

    # ---- persistent strategy state (stops, peak, day start) -------------
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"stops": {}, "peak": None, "day": None, "day_start": None}

    def _save_state(self, st: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(self.state_file)

    def _portfolio_state(self, prices: dict[str, float], st: dict) -> PortfolioState:
        s = self.ledger.summary(ENGINE, prices)
        today = self.get_datetime().date().isoformat()
        if st.get("day") != today:
            st["day"], st["day_start"] = today, s["equity"]
        st["peak"] = max(st.get("peak") or s["equity"], s["equity"])
        return PortfolioState(
            equity=s["equity"], cash=s["cash"], day_start_equity=st["day_start"], peak_equity=st["peak"],
            positions_value={p["symbol"]: p["value"] for p in s["positions"]},
            positions_qty={p["symbol"]: p["qty"] for p in s["positions"]},
        )

    def on_trading_iteration(self):
        beat(self.conn, "lumibot", "running")
        universe = self.parameters["universe"]
        closes = {}
        for sym in universe:
            bars = self.get_historical_prices(sym, self.p.trend + 10, "day")
            if bars is not None and bars.df is not None and len(bars.df):
                closes[sym] = bars.df["close"]
        if not closes:
            self.log_message("no data; skipping iteration")
            return
        closes_df = pd.DataFrame(closes).dropna(how="all")
        last_bar = pd.Timestamp(closes_df.index[-1])
        now_ts = pd.Timestamp(self.get_datetime())
        if last_bar.tzinfo is None:
            last_bar = last_bar.tz_localize(now_ts.tzinfo)
        if (now_ts - last_bar) > pd.Timedelta(days=4):
            self.risk.log(ENGINE, "warning", "stale_data", f"latest daily bar {last_bar} too old")
            beat(self.conn, "lumibot", "degraded", "stale data")
            return
        prices = {s: float(closes_df[s].dropna().iloc[-1]) for s in closes_df.columns if closes_df[s].notna().any()}

        st = self._load_state()
        state = self._portfolio_state(prices, st)
        self.ledger.snapshot(ENGINE, state.equity, state.cash, ts=self.get_datetime().isoformat())
        breaches = self.risk.check_portfolio(ENGINE, state)

        rebalance_day = self.get_datetime().weekday() == self.parameters["rebalance_weekday"]
        targets = select_targets(closes_df, state.equity, self.limits.max_position_pct,
                                 self.limits.max_risk_per_trade_pct, self.p) if rebalance_day and not breaches else None
        holdings = dict(state.positions_qty)
        if targets is None:
            # Not a rebalance day (or halted): only enforce stops.
            sells = [(s, q) for s, q in holdings.items() if s in st["stops"] and prices.get(s, 1e18) < st["stops"][s]]
            buys = []
        else:
            sells, buys = plan_orders(targets, holdings, prices, st["stops"])
            for t in targets:
                st["stops"].setdefault(t.symbol, t.stop_price)

        now = self.get_datetime()
        for sym, qty in sells:
            self._submit(sym, "sell", qty, prices[sym], None, now, state)
        stop_map = {t.symbol: t.stop_price for t in (targets or [])}
        for sym, qty in buys:
            self._submit(sym, "buy", qty, prices[sym], stop_map.get(sym), now, state)
        for sym in list(st["stops"]):
            if sym not in holdings and sym not in stop_map:
                st["stops"].pop(sym)
        self._save_state(st)

    def _submit(self, sym, side, qty, price, stop, now, state: PortfolioState):
        live_px = self.get_last_price(sym)  # fresh quote; daily close is only for signals
        if live_px is None or not (float(live_px) > 0):
            self.risk.log(ENGINE, "warning", "invalid_quote", f"no valid quote for {sym}; order skipped")
            return
        price = float(live_px)
        coid = f"bm-lb-{uuid.uuid4().hex[:16]}"
        if side == "buy":
            # Re-size at the live price: never exceed the risk engine's own maximum.
            probe = self.risk.evaluate(OrderIntent(ENGINE, sym, side, max(qty, 1e-9), price, coid, now, stop),
                                       state, now=now, record=False)
            qty = math.floor(min(qty, probe.max_qty) * 1e4) / 1e4  # floor, never round up past a limit
        else:
            qty = min(qty, state.positions_qty.get(sym, 0.0))  # exact: avoid leaving dust
        if qty <= 0 or qty * price < 1.0:
            return
        intent = OrderIntent(ENGINE, sym, side, qty, price, coid, now, stop)
        decision = self.risk.evaluate(intent, state, now=now)
        if not decision.approved:
            self.log_message(f"RISK REJECTED {side} {qty} {sym}: {decision.reasons}")
            return
        order = self.create_order(sym, qty, side)
        order.custom_params = {"bm_coid": coid}
        self.submit_order(order)

    def on_filled_order(self, position, order, price, quantity, multiplier):
        coid = (getattr(order, "custom_params", None) or {}).get("bm_coid") or f"bm-lb-ext-{order.identifier}"
        notional = float(price) * float(quantity)
        try:
            self.ledger.record_fill(ENGINE, order.asset.symbol, order.side.lower() if isinstance(order.side, str) else str(order.side.value).lower(),
                                    float(quantity), float(price), US_ETF_ALPACA.fee(notional), coid,
                                    ts=self.get_datetime().isoformat(),
                                    source="backtest" if self.is_backtesting else "paper")
        except Exception as exc:  # never crash the trading loop on accounting; log loudly
            self.risk.log(ENGINE, "critical", "ledger_error", f"{coid}: {exc}")
