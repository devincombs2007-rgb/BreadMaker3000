"""Transaction cost model: fees + half-spread + slippage, applied adversely."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_bps: float = 10.0       # commission/taker fee per side
    spread_bps: float = 5.0     # full quoted spread; we pay half per side
    slippage_bps: float = 5.0   # market impact / latency
    min_fee: float = 0.0

    def fill_price(self, side: str, ref_price: float) -> float:
        adj = (self.spread_bps / 2 + self.slippage_bps) / 1e4
        return ref_price * (1 + adj) if side == "buy" else ref_price * (1 - adj)

    def fee(self, notional: float) -> float:
        return max(self.min_fee, abs(notional) * self.fee_bps / 1e4)

    def scaled(self, k: float) -> "CostModel":
        """Stress test helper: multiply every cost component by k."""
        return CostModel(self.fee_bps * k, self.spread_bps * k, self.slippage_bps * k, self.min_fee * k)


# Conservative defaults per venue (assumptions; revisit with observed paper fills).
CRYPTO_KRAKEN = CostModel(fee_bps=40.0, spread_bps=10.0, slippage_bps=10.0)  # Kraken Pro taker ~0.40% low tier
US_ETF_ALPACA = CostModel(fee_bps=0.3, spread_bps=2.0, slippage_bps=3.0)     # commission-free + SEC/TAF fees approx
