import pytest

from breadmaker.config import (KalshiConfig, RiskLimits, SafetyError, kalshi_access_allowed, load_settings)
from conftest import ROOT as REPO_ROOT
from breadmaker.safety_scan import scan


def test_settings_load(tmp_settings):
    assert tmp_settings.risk.max_position_pct == 0.10
    assert tmp_settings.risk.daily_loss_halt_pct == 0.05
    assert tmp_settings.risk.max_drawdown_halt_pct == 0.20
    assert list(tmp_settings.engines) == ["kalshi"]


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


def test_repository_safety_scan_passes():
    assert scan(REPO_ROOT) == []


def test_safety_scan_detects_problems(tmp_path):
    (tmp_path / "x.py").write_text('URL = "https://api.alpaca.markets/v2"\nKEY="AKABCDEFGHIJKLMNOPQR"\n')
    probs = scan(tmp_path)
    assert any("live-trading" in p for p in probs) and any("LIVE key" in p for p in probs)


def test_kalshi_scan_rejects_trading_code(tmp_path):
    (tmp_path / "bad.py").write_text('h = {"KALSHI-ACCESS-SIGNATURE": sig}\nurl = base + "/portfolio/orders"\n')
    probs = scan(tmp_path)
    assert len([p for p in probs if "live-trading" in p]) == 2


def test_kalshi_access_off_by_default(tmp_settings):
    assert tmp_settings.kalshi.api_enabled is False
    ok, why = kalshi_access_allowed(tmp_settings.kalshi, {})
    assert not ok and "api_enabled is false" in why


def test_kalshi_access_requires_both_flag_and_attestation():
    cfg = KalshiConfig(api_enabled=True)
    assert not kalshi_access_allowed(cfg, {})[0]
    assert not kalshi_access_allowed(cfg, {"KALSHI_ACCESS_AUTHORIZED": "yes"})[0]
    assert kalshi_access_allowed(cfg, {"KALSHI_ACCESS_AUTHORIZED": "written-authorization-on-file"})[0]


@pytest.mark.parametrize("kw", [{"base_url": "https://evil.example/trade-api/v2"}, {"kelly_fraction": 0.9},
                                {"max_position_pct": 0.5}, {"min_edge": 0.0}, {"taker_fee_rate": 0.01},
                                {"max_requests_per_second": 50}])
def test_kalshi_limits_cannot_be_loosened(kw):
    with pytest.raises(SafetyError):
        KalshiConfig(**kw)


def test_default_bankroll_is_25(tmp_settings):
    assert tmp_settings.kalshi.bankroll == 25.0
    assert tmp_settings.engines["kalshi"].initial_capital == 25
