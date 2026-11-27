"""Central configuration and hard safety validation.

Configuration is read from ``config/breadmaker.yaml`` (non-secret settings) and
environment variables (secrets). Safety rules here are *not* configurable: they
reject any configuration that could route an order to a real-money venue.
"""
from __future__ import annotations

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


# Kalshi: read-only public market data. These are the only hosts the client may call.
KALSHI_ALLOWED_HOSTS = ("api.elections.kalshi.com", "demo-api.kalshi.co")
KALSHI_AUTHORIZATION_ENV = "KALSHI_ACCESS_AUTHORIZED"
KALSHI_AUTHORIZATION_VALUE = "written-authorization-on-file"
_URL_HOST = re.compile(r"^(?:[a-z]+://)?([^/:]+)", re.I)


def _host(url: str) -> str:
    m = _URL_HOST.match(url.strip())
    return (m.group(1) if m else "").lower()


def kalshi_access_allowed(cfg: "KalshiConfig", env: dict | None = None) -> tuple[bool, str]:
    """Kalshi API access is OFF unless BOTH are true:
    1. config `kalshi.api_enabled: true`, and
    2. env KALSHI_ACCESS_AUTHORIZED == 'written-authorization-on-file' (owner attests Kalshi
       membership and written authorization for this use - Developer Agreement v1.1 s3, s4.1).
    The GitHub workflow additionally refuses to run Kalshi steps in a public repository (s3.1: no
    sharing of API data with third parties)."""
    env = os.environ if env is None else env
    if not cfg.api_enabled:
        return False, "kalshi.api_enabled is false (default) - see KALSHI.md"
    if env.get(KALSHI_AUTHORIZATION_ENV) != KALSHI_AUTHORIZATION_VALUE:
        return False, f"{KALSHI_AUTHORIZATION_ENV} attestation missing - see KALSHI.md"
    if _host(cfg.base_url) not in KALSHI_ALLOWED_HOSTS:
        return False, f"base_url host {_host(cfg.base_url)!r} not allowed"
    return True, "ok"


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
    shadow: bool = False   # challenger/shadow book: tracked and risk-limited, excluded from the $3,000 competition


@dataclass(frozen=True)
class KalshiConfig:
    api_enabled: bool = False
    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    max_requests_per_second: float = 5.0
    bankroll: float = 25.0
    kelly_fraction: float = 0.25
    max_position_pct: float = 0.10
    max_event_exposure_pct: float = 0.20
    max_total_exposure_pct: float = 0.50
    min_edge: float = 0.03
    max_spread: float = 0.10
    min_hours_to_close: float = 1.0
    taker_fee_rate: float = 0.07
    fee_rate_overrides: dict = field(default_factory=dict)  # series_ticker -> rate
    enabled_models: tuple = ("weather_high_temp", "crypto_price", "structural")
    weather_stations: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _host(self.base_url) not in KALSHI_ALLOWED_HOSTS:
            raise SafetyError(f"Kalshi base_url host {_host(self.base_url)!r} not in {KALSHI_ALLOWED_HOSTS}")
        bounds = {  # (min, max) - config may tighten, never loosen
            "max_requests_per_second": (0.1, 10.0),
            "bankroll": (1.0, 10_000.0),
            "kelly_fraction": (0.0, 0.5),
            "max_position_pct": (0.0, 0.25),
            "max_event_exposure_pct": (0.0, 0.5),
            "max_total_exposure_pct": (0.0, 1.0),
            "min_edge": (0.01, 1.0),
            "max_spread": (0.0, 0.5),
            "taker_fee_rate": (0.07, 1.0),  # never model fees below Kalshi's standard taker rate
        }
        for name, (lo, hi) in bounds.items():
            v = getattr(self, name)
            if not (lo <= v <= hi):
                raise SafetyError(f"kalshi.{name}={v} outside allowed range [{lo}, {hi}]")


@dataclass
class Settings:
    data_dir: Path
    db_path: Path
    kill_switch_path: Path
    risk: RiskLimits
    engines: dict[str, EngineConfig] = field(default_factory=dict)
    kalshi: KalshiConfig = field(default_factory=KalshiConfig)
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
    kcfg = dict(raw.get("kalshi") or {})
    if "enabled_models" in kcfg:
        kcfg["enabled_models"] = tuple(kcfg["enabled_models"])
    return Settings(
        kalshi=KalshiConfig(**kcfg),
        data_dir=data_dir,
        db_path=data_dir / "breadmaker.sqlite",
        kill_switch_path=data_dir / "KILL_SWITCH",
        risk=risk,
        engines=engines,
        raw=raw,
    )
