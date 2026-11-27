from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from scipy.stats import beta


@dataclass
class Forecast:
    p: float                     # probability the market resolves YES
    p_low: float                 # conservative lower bound (5th pct)
    p_high: float                # conservative upper bound (95th pct)
    model: str
    issued_at: datetime
    evidence_ids: list[int] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if not (0 <= self.p_low <= self.p <= self.p_high <= 1):
            raise ValueError(f"invalid forecast interval {self.p_low} <= {self.p} <= {self.p_high}")
        if self.issued_at.tzinfo is None:
            raise ValueError("issued_at must be timezone-aware")


@dataclass
class Rejection:
    model: str
    reason: str


def beta_interval(k: float, n: float, level: float = 0.90) -> tuple[float, float, float]:
    """Jeffreys-prior estimate and equal-tailed credible interval for k successes out of n."""
    a, b = k + 0.5, n - k + 0.5
    lo, hi = beta.ppf([(1 - level) / 2, (1 + level) / 2], a, b)
    return a / (a + b), float(lo), float(hi)
