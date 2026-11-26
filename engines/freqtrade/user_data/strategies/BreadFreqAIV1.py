"""BreadFreqAIV1 - FreqAI research strategy (Phase 4, challenger only).

Predicts the forward mean close-to-close return over `label_period_candles` with
LightGBM, retrained on a sliding window by FreqAI. Enters long only when the
predicted return exceeds a cost-aware threshold AND FreqAI's outlier detection
(DI / do_predict) says the current sample is in-distribution.

It runs as a CHALLENGER: it must beat BreadTrendV1 in forward dry-run before
any promotion. Same risk sizing and kill switch as the baseline.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import numpy as np
from pandas import DataFrame

from freqtrade.strategy import IStrategy

KILL_SWITCH = Path(os.environ.get("BREADMAKER_KILL_SWITCH", "/freqtrade/user_data/shared/KILL_SWITCH"))
MAX_POSITION_PCT = 0.10
MAX_RISK_PER_TRADE_PCT = 0.005
ROUND_TRIP_COST = 0.010  # conservative: 2 x (0.40% fee + 0.10% spread/slippage)


class BreadFreqAIV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "1h"
    startup_candle_count = 100
    process_only_new_candles = True
    stoploss = -0.05
    minimal_roi = {"0": 0.20, "1440": 0.10}
    use_exit_signal = True

    # ---- features: only past data (FreqAI shifts/aligns timeframes) ----
    def feature_engineering_expand_all(self, dataframe: DataFrame, period: int, metadata: dict, **kwargs) -> DataFrame:
        c = dataframe["close"]
        dataframe["%-roc-period"] = c.pct_change(period)
        dataframe["%-vol-period"] = c.pct_change().rolling(period).std()
        dataframe["%-dist_sma-period"] = c / c.rolling(period).mean() - 1
        dataframe["%-rvol-period"] = dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        return dataframe

    def feature_engineering_expand_basic(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["%-ret1"] = dataframe["close"].pct_change()
        dataframe["%-hl_range"] = (dataframe["high"] - dataframe["low"]) / dataframe["close"]
        return dataframe

    def feature_engineering_standard(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour / 23.0
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek / 6.0
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        h = self.freqai_info["feature_parameters"]["label_period_candles"]
        # Label uses FUTURE prices by design; FreqAI drops the last h rows from training.
        dataframe["&-fwd_return"] = dataframe["close"].shift(-h).rolling(h).mean() / dataframe["close"] - 1
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)
        dataframe["ema200"] = dataframe["close"].ewm(span=200, adjust=False).mean()
        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (df["do_predict"] == 1)
            & (df["&-fwd_return"] > ROUND_TRIP_COST * 1.5)
            & (df["close"] > df["ema200"])
            & (df["volume"] > 0)
        )
        df.loc[cond, ["enter_long", "enter_tag"]] = (1, "freqai_fwd_return")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        cond = (df["do_predict"] == 1) & (df["&-fwd_return"] < 0)
        df.loc[cond, ["exit_long", "exit_tag"]] = (1, "freqai_negative")
        return df

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        equity = self.wallets.get_total_stake_amount() if self.wallets else max_stake
        stake = min(equity * MAX_RISK_PER_TRADE_PCT / abs(self.stoploss), equity * MAX_POSITION_PCT, max_stake)
        if min_stake and stake < min_stake:
            return 0
        return float(np.floor(stake * 100) / 100)

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time,
                            entry_tag, side, **kwargs) -> bool:
        return side == "long" and not KILL_SWITCH.exists()

    def leverage(self, *args, **kwargs) -> float:
        return 1.0
