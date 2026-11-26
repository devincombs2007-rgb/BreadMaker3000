"""Runs inside the Freqtrade environment (CI job `freqtrade-smoke`):
    pytest engines/freqtrade/tests/test_gate_integration.py

Loads the real strategies with Freqtrade's StrategyResolver and invokes confirm_trade_entry through
`strategy_safe_wrapper(..., default_retval=True)` exactly as freqtradebot.execute_entry does.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from freqtrade.enums import RunMode  # noqa: E402
from freqtrade.resolvers import StrategyResolver  # noqa: E402
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper  # noqa: E402

from breadmaker.risk import EngineHalts, write_monitor_heartbeat  # noqa: E402

NOW = datetime.now(timezone.utc)


def load(name, shared, engine, monkeypatch):
    monkeypatch.setenv("BREADMAKER_SHARED_DIR", str(shared))
    monkeypatch.setenv("BREADMAKER_ENGINE", engine)
    for mod in [m for m in sys.modules if m.startswith(("BreadTrendV1", "BreadFreqAIV1", "freqtrade.strategy.user"))]:
        sys.modules.pop(mod, None)
    cfg = json.loads((ROOT / "engines/freqtrade/user_data/config.json").read_text())
    cfg.update({"strategy": name, "user_data_dir": ROOT / "engines/freqtrade/user_data",
                "strategy_path": str(ROOT / "engines/freqtrade/user_data/strategies"), "runmode": RunMode.DRY_RUN})
    if name == "BreadFreqAIV1":
        cfg.update(json.loads((ROOT / "engines/freqtrade/user_data/config.freqai.json").read_text()))
        cfg["freqaimodel"] = "LightGBMRegressor"
    strat = StrategyResolver.load_strategy(cfg)
    strat.dp = mock.MagicMock(runmode=RunMode.DRY_RUN)
    return strat


def confirm(strat):
    # identical call path to freqtradebot.execute_entry (fail-open default!)
    return strategy_safe_wrapper(strat.confirm_trade_entry, default_retval=True)(
        pair="BTC/USD", order_type="limit", amount=0.001, rate=100.0, time_in_force="GTC",
        current_time=datetime.now(timezone.utc), entry_tag=None, side="long")


@pytest.mark.parametrize("name,engine", [("BreadTrendV1", "freqtrade"), ("BreadFreqAIV1", "freqai_shadow")])
def test_gate_enforced_in_real_freqtrade(tmp_path, monkeypatch, name, engine):
    shared = tmp_path / "shared"
    strat = load(name, shared, engine, monkeypatch)
    assert confirm(strat) is False                         # shared mount missing
    shared.mkdir()
    assert confirm(strat) is False                         # no monitor heartbeat
    write_monitor_heartbeat(shared, engine)
    assert confirm(strat) is True                          # all clear
    EngineHalts(shared).halt(engine, "daily_loss", "test", NOW, NOW + timedelta(hours=1))
    assert confirm(strat) is False                         # halted
    EngineHalts(shared).release()
    (shared / "KILL_SWITCH").write_text("x")
    assert confirm(strat) is False                         # global kill switch
    (shared / "KILL_SWITCH").unlink()
    write_monitor_heartbeat(shared, engine, NOW - timedelta(minutes=10))
    assert confirm(strat) is False                         # monitor stale


def test_freqtrade_wrapper_is_fail_open_which_is_why_gate_catches_everything():
    """Documents the upstream behaviour that motivated the fail-closed gate."""
    def boom(**kwargs):
        raise ValueError("strategy bug")
    assert strategy_safe_wrapper(boom, default_retval=True)() is True


def test_stake_cap_respects_limits(tmp_path, monkeypatch):
    strat = load("BreadTrendV1", tmp_path, "freqtrade", monkeypatch)
    strat.wallets = mock.MagicMock(get_total_stake_amount=lambda: 1000.0)
    stake = strat.custom_stake_amount(pair="BTC/USD", current_time=NOW, current_rate=100.0, proposed_stake=900,
                                      min_stake=1.0, max_stake=900.0, leverage=1.0, entry_tag=None, side="long")
    assert stake <= 100.0                                  # 10% of equity, = 0.5% risk / 5% stop
    assert strat.can_short is False and strat.leverage() == 1.0
    assert strat.ignore_buying_expired_candle_after == 600
