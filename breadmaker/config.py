"""Central configuration and hard safety validation.

Configuration is read from ``config/breadmaker.yaml`` (non-secret settings) and
environment variables (secrets). Safety rules here are *not* configurable: they
reject any configuration that could route an order to a real-money venue.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "breadmaker.yaml"


class SafetyError(RuntimeError):
    """Raised when a configuration would permit real-money trading."""


# Alpaca: only the paper trading host is acceptable for trading endpoints.
ALPACA_PAPER_HOST = "paper-api.alpaca.markets"
ALPACA_LIVE_HOSTS = ("api.alpaca.markets",)
_URL_HOST = re.compile(r"^(?:[a-z]+://)?([^/:]+)", re.I)


def _host(url: str) -> str:
    m = _URL_HOST.match(url.strip())
    return (m.group(1) if m else "").lower()


def assert_alpaca_paper(base_url: str | None, paper_flag: Any) -> None:
    """Reject anything except Alpaca paper trading."""
    if str(paper_flag).strip().lower() not in ("true", "1", "yes"):
        raise SafetyError("ALPACA_PAPER must be true. Live Alpaca trading is not supported.")
    if base_url:
        host = _host(base_url)
        if host in ALPACA_LIVE_HOSTS or host != ALPACA_PAPER_HOST:
            raise SafetyError(f"Alpaca endpoint {host!r} is not the paper endpoint {ALPACA_PAPER_HOST!r}.")


def assert_alpaca_key_looks_paper(api_key: str | None) -> None:
    """Alpaca paper keys start with 'PK'; live keys start with 'AK'."""
    if api_key and api_key.strip().upper().startswith("AK"):
        raise SafetyError("This looks like a LIVE Alpaca key (starts with 'AK'). Use paper keys (start with 'PK').")


def assert_freqtrade_dry_run(ft_config: dict) -> None:
    """Freqtrade must run in dry-run, spot, unleveraged mode with no exchange keys."""
    if ft_config.get("dry_run") is not True:
        raise SafetyError("Freqtrade config must set dry_run: true.")
    if ft_config.get("trading_mode", "spot") != "spot":
        raise SafetyError("Only spot trading_mode is allowed.")
    if ft_config.get("margin_mode", "") not in ("", None):
        raise SafetyError("margin_mode must be empty (no margin).")
    ex = ft_config.get("exchange", {})
    if ex.get("key") or ex.get("secret") or ex.get("password"):
        raise SafetyError("Exchange API keys must not be configured; dry-run uses public market data only.")
    api = ft_config.get("api_server", {})
    if api.get("enabled") and api.get("listen_ip_address") not in ("127.0.0.1", "0.0.0.0"):
        raise SafetyError("Freqtrade API must listen on loopback or the private Docker network only.")


def load_freqtrade_config(path: str | Path) -> dict:
    with open(path) as fh:
        return json.load(fh)


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.10
    max_risk_per_trade_pct: float = 0.005
    daily_loss_halt_pct: float = 0.01
    max_drawdown_halt_pct: float = 0.05
    max_data_age_seconds: int = 300
    allow_short: bool = False
    allow_leverage: bool = False

    def __post_init__(self) -> None:
        # Hard ceilings: configuration may tighten, never loosen beyond these.
        if self.allow_short or self.allow_leverage:
            raise SafetyError("Shorting and leverage are disabled in this release.")
        ceilings = {
            "max_position_pct": 0.25,
            "max_risk_per_trade_pct": 0.02,
            "daily_loss_halt_pct": 0.05,
            "max_drawdown_halt_pct": 0.20,
        }
        for name, ceiling in ceilings.items():
            v = getattr(self, name)
            if not (0 < v <= ceiling):
                raise SafetyError(f"Risk limit {name}={v} outside allowed range (0, {ceiling}].")


@dataclass
class EngineConfig:
    name: str
    asset_class: str
    initial_capital: float
    enabled: bool = True
    benchmark: str = ""


@dataclass
class Settings:
    data_dir: Path
    db_path: Path
    kill_switch_path: Path
    risk: RiskLimits
    engines: dict[str, EngineConfig] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("BREADMAKER_CONFIG", DEFAULT_CONFIG_PATH))
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    if raw.get("mode") != "simulation":
        raise SafetyError("config mode must be 'simulation'. Real-money mode does not exist in this release.")
    data_dir = Path(os.environ.get("BREADMAKER_DATA_DIR", raw.get("data_dir", REPO_ROOT / "data")))
    risk = RiskLimits(**(raw.get("risk") or {}))
    engines = {
        name: EngineConfig(name=name, **cfg) for name, cfg in (raw.get("engines") or {}).items()
    }
    return Settings(
        data_dir=data_dir,
        db_path=data_dir / "breadmaker.sqlite",
        kill_switch_path=data_dir / "KILL_SWITCH",
        risk=risk,
        engines=engines,
        raw=raw,
    )
