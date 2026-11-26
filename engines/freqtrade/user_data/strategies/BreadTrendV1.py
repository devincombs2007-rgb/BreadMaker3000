"""BreadTrendV1 - baseline long-only spot crypto trend strategy for Freqtrade.

Deliberately simple and transparent. It is a BASELINE to beat, not a claim of
profitability.

Entry  : 1h EMA20 crosses above EMA50, close above EMA200 (trend filter),
         volume above its 24h average, ADX-like trend strength via ATR% band.
Exit   : EMA20 crosses below EMA50, or the static 5% stoploss, or ROI ladder.
Sizing : equity * 0.5% risk / 5% stop = 10% of equity, capped at 10% (BreadMaker
         risk limits). Enforced in custom_stake_amount.
Safety : refuses new entries while the BreadMaker kill switch file exists.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import logging

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bread_risk_gate import entry_allowed  # noqa: E402

SHARED_DIR = Path(os.environ.get("BREADMAKER_SHARED_DIR", "/freqtrade/user_data/shared"))
ENGINE = os.environ.get("BREADMAKER_ENGINE", "freqtrade")
logger = logging.getLogger(__name__)
MAX_POSITION_PCT = 0.10
MAX_RISK_PER_TRADE_PCT = 0.005


def _atr(df: DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


class BreadTrendV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "1h"
    startup_candle_count = 220
    process_only_new_candles = True
    # Ignore entry signals from candles that closed > 10 min ago (e.g. after a restart/outage).
    ignore_buying_expired_candle_after = 600
    use_exit_signal = True
    exit_profit_only = False

    stoploss = -0.05
    trailing_stop = False
    minimal_roi = {"0": 0.20, "1440": 0.10, "4320": 0.05}

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {"method": "StoplossGuard", "lookback_period_candles": 24, "trade_limit": 3,
             "stop_duration_candles": 12, "only_per_pair": False},
            {"method": "MaxDrawdown", "lookback_period_candles": 168, "trade_limit": 5,
             "stop_duration_candles": 48, "max_allowed_drawdown": 0.05},
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = dataframe["close"]
        dataframe["ema20"] = c.ewm(span=20, adjust=False).mean()
        dataframe["ema50"] = c.ewm(span=50, adjust=False).mean()
        dataframe["ema200"] = c.ewm(span=200, adjust=False).mean()
        dataframe["atr_pct"] = _atr(dataframe) / c
        dataframe["vol_ma"] = dataframe["volume"].rolling(24).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cross_up = (dataframe["ema20"] > dataframe["ema50"]) & (dataframe["ema20"].shift(1) <= dataframe["ema50"].shift(1))
        cond = (
            cross_up
            & (dataframe["close"] > dataframe["ema200"])
            & (dataframe["volume"] > dataframe["vol_ma"])
            & (dataframe["atr_pct"].between(0.002, 0.04))
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, ["enter_long", "enter_tag"]] = (1, "ema_cross_trend")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cross_dn = (dataframe["ema20"] < dataframe["ema50"]) & (dataframe["ema20"].shift(1) >= dataframe["ema50"].shift(1))
        dataframe.loc[cross_dn & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (1, "ema_cross_down")
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        equity = self.wallets.get_total_stake_amount() if self.wallets else max_stake
        by_risk = equity * MAX_RISK_PER_TRADE_PCT / abs(self.stoploss)
        stake = min(by_risk, equity * MAX_POSITION_PCT, max_stake)
        if min_stake and stake < min_stake:
            return 0  # skip trade rather than violate limits
        return float(np.floor(stake * 100) / 100)

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        if side != "long":
            return False
        if self.dp is not None and self.dp.runmode not in (RunMode.DRY_RUN, RunMode.LIVE):
            return True  # backtest/hyperopt: no shared risk state exists
        ok, reason = entry_allowed(SHARED_DIR, ENGINE, current_time)
        if not ok:
            logger.warning("BreadMaker risk gate blocked %s entry: %s", pair, reason)
        return ok

    def leverage(self, *args, **kwargs) -> float:
        return 1.0
