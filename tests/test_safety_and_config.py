import json

import pytest

from breadmaker.config import (REPO_ROOT, RiskLimits, SafetyError, assert_alpaca_key_looks_paper,
                               assert_alpaca_paper, assert_freqtrade_dry_run, load_settings)
from breadmaker.safety_scan import scan


def test_settings_load(tmp_settings):
    assert tmp_settings.risk.max_position_pct == 0.10
    assert tmp_settings.risk.max_risk_per_trade_pct == 0.005
    assert tmp_settings.risk.daily_loss_halt_pct == 0.01
    assert tmp_settings.risk.max_drawdown_halt_pct == 0.05
    assert sum(e.initial_capital for e in tmp_settings.engines.values() if not e.shadow) == 3000


def test_non_simulation_mode_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("mode: live\n")
    with pytest.raises(SafetyError):
        load_settings(p)


@pytest.mark.parametrize("kw", [{"allow_short": True}, {"allow_leverage": True}, {"max_position_pct": 0.9},
                                {"max_drawdown_halt_pct": 0.5}, {"max_risk_per_trade_pct": 0}])
def test_risk_limit_ceilings(kw):
    with pytest.raises(SafetyError):
        RiskLimits(**kw)


def test_alpaca_paper_enforced():
    assert_alpaca_paper("https://paper-api.alpaca.markets", "true")
    assert_alpaca_paper(None, True)
    for url, flag in [("https://api.alpaca.markets", "true"), ("https://evil.example", "true"),
                      ("https://paper-api.alpaca.markets", "false")]:
        with pytest.raises(SafetyError):
            assert_alpaca_paper(url, flag)
    with pytest.raises(SafetyError):
        assert_alpaca_key_looks_paper("AKXXXXXXXXXXXXXXXXXX")
    assert_alpaca_key_looks_paper("PKXXXXXXXXXXXXXXXXXX")


def test_freqtrade_repo_config_is_dry_run():
    cfg = json.loads((REPO_ROOT / "engines/freqtrade/user_data/config.json").read_text())
    assert_freqtrade_dry_run(cfg)
    assert cfg["dry_run_wallet"] == 1000


@pytest.mark.parametrize("mut", [
    lambda c: c.update(dry_run=False),
    lambda c: c.update(trading_mode="futures"),
    lambda c: c.update(margin_mode="isolated"),
    lambda c: c["exchange"].update(key="abc", secret="def"),
])
def test_freqtrade_unsafe_configs_rejected(mut):
    cfg = json.loads((REPO_ROOT / "engines/freqtrade/user_data/config.json").read_text())
    mut(cfg)
    with pytest.raises(SafetyError):
        assert_freqtrade_dry_run(cfg)


def test_repository_safety_scan_passes():
    assert scan(REPO_ROOT) == []


def test_safety_scan_detects_problems(tmp_path):
    (tmp_path / "x.py").write_text('URL = "https://api.alpaca.markets/v2"\nKEY="AKABCDEFGHIJKLMNOPQR"\n')
    probs = scan(tmp_path)
    assert any("live-trading" in p for p in probs) and any("LIVE key" in p for p in probs)
