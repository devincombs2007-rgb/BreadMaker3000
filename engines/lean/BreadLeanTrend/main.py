# region imports
from AlgorithmImports import *  # noqa: F401,F403  (provided by the LEAN engine)
# endregion
"""BreadLeanTrend - independent cross-check of the ETF trend/relative-strength
baseline, implemented in QuantConnect LEAN (open-source engine, run locally).

Purpose: cross-engine validation. Same rules as engines/lumibot logic
(SMA trend filter + 126d momentum ranking, risk-sized positions, 10% cap),
executed by a different engine with LEAN's own fill/fee models. Divergence
between engines on equivalent data flags implementation or modelling errors.

Virtual capital: $1,000. Long only. No leverage.
"""

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ", "DBC"]
LOOKBACK, TREND, TOP_N = 126, 200, 3
MAX_POSITION_PCT, MAX_RISK_PCT = 0.10, 0.005
STOP_VOL_MULT, MIN_STOP, MAX_STOP = 3.0, 0.04, 0.15
DAILY_LOSS_HALT, MAX_DRAWDOWN_HALT = 0.01, 0.05


def risk_gate(equity: float, day_start: float, peak: float) -> str:
    """Portfolio-level limits mirrored from breadmaker RiskLimits (LEAN runs without BreadMaker core).
    Returns 'ok', 'halt_entries' (daily loss) or 'liquidate_and_halt' (drawdown)."""
    if peak > 0 and equity / peak - 1 <= -MAX_DRAWDOWN_HALT:
        return "liquidate_and_halt"
    if day_start > 0 and equity / day_start - 1 <= -DAILY_LOSS_HALT:
        return "halt_entries"
    return "ok"


def choose_weights(history: dict) -> dict:
    """Pure decision function. history: symbol -> list of daily closes (oldest first)."""
    import math
    scored = []
    for sym, closes in history.items():
        if len(closes) < max(LOOKBACK, TREND) + 1:
            continue
        last = closes[-1]
        sma = sum(closes[-TREND:]) / TREND
        mom = last / closes[-1 - LOOKBACK] - 1
        rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        if last > sma and mom > 0:
            scored.append((mom, sym, vol))
    scored.sort(reverse=True)
    weights = {}
    for mom, sym, vol in scored[:TOP_N]:
        stop_pct = min(max(STOP_VOL_MULT * vol * math.sqrt(5), MIN_STOP), MAX_STOP)
        weights[sym] = min(MAX_POSITION_PCT, MAX_RISK_PCT / stop_pct)
    return weights


class BreadLeanTrend(QCAlgorithm):  # noqa: F405
    def initialize(self):
        self.set_start_date(2015, 1, 1)
        self.set_end_date(2024, 12, 31)
        self.set_cash(1000)
        self.set_brokerage_model(BrokerageName.ALPACA, AccountType.CASH)  # noqa: F405 - cash account: no margin
        self.symbols = {}
        for t in UNIVERSE:
            eq = self.add_equity(t, Resolution.DAILY)  # noqa: F405
            eq.set_data_normalization_mode(DataNormalizationMode.ADJUSTED)  # noqa: F405
            self.symbols[t] = eq.symbol
        self.set_benchmark("SPY")
        self.set_warm_up(TREND + 5, Resolution.DAILY)  # noqa: F405
        self.peak = self.day_start = 1000.0
        self.halted = False
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.after_market_open("SPY", 1),
                         self.start_of_day)
        self.schedule.on(self.date_rules.week_start("SPY"), self.time_rules.after_market_open("SPY", 30),
                         self.rebalance)

    def start_of_day(self):
        self.day_start = self.portfolio.total_portfolio_value

    def on_data(self, data):
        if self.is_warming_up:
            return
        eq = self.portfolio.total_portfolio_value
        self.peak = max(self.peak, eq)
        if risk_gate(eq, self.day_start, self.peak) == "liquidate_and_halt" and not self.halted:
            self.halted = True
            self.liquidate(tag="drawdown halt")
            self.debug(f"Drawdown halt at equity {eq:.2f} (peak {self.peak:.2f})")

    def rebalance(self):
        if self.is_warming_up or self.halted:
            return
        if risk_gate(self.portfolio.total_portfolio_value, self.day_start, self.peak) != "ok":
            return
        hist = self.history(list(self.symbols.values()), TREND + 10, Resolution.DAILY)  # noqa: F405
        if hist.empty:
            return
        closes = {}
        for t, sym in self.symbols.items():
            try:
                closes[t] = list(hist.loc[sym]["close"].values)
            except KeyError:
                continue
        weights = choose_weights(closes)
        for t, sym in self.symbols.items():
            if t not in weights and self.portfolio[sym].invested:
                self.liquidate(sym, "deselected")
        for t, w in weights.items():
            self.set_holdings(self.symbols[t], w)
