# 🍞 BreadMaker3000: Prediction Market Intelligence

A research and **paper-trading** platform for Kalshi prediction markets. It forecasts outcomes with
calibrated uncertainty, compares them to executable prices after fees, sizes simulated positions on
a **$25** bankroll, and tracks accuracy, calibration, returns and drawdown.

> **Simulation only.** There is no order-placing, authenticated or real-money code in this
> repository (CI enforces it). **Kalshi API access is currently OFF** pending written authorization
> from Kalshi. See [KALSHI.md](KALSHI.md) for why and how.

The previous equity/crypto trading system (Freqtrade, LumiBot, LEAN) is preserved unchanged on
branch [`archive/trading-system-v0.2`](../../tree/archive/trading-system-v0.2).

## What it does

| Stage | Behaviour |
|---|---|
| Discover | All open Kalshi events and markets, across every category (read-only, rate-limited, backoff) |
| Model | Weather daily highs and crypto price thresholds with calibrated intervals, plus model-free strike and bracket combinations. All other categories are **rejected with a reason**. |
| Price | Executable YES/NO asks and depth from order books; conservative taker-fee model |
| Decide | Trades only if the conservative probability beats the ask by ≥ 3¢ after fees. Size is fractional Kelly within position, event and total caps. **Zero allocation is normal.** |
| Track | Every prediction and its timestamped evidence is logged. Settlements score Brier, log loss and calibration; the paper book shows returns and drawdown. |
| Learn | Walk-forward recalibration using only settlements that happened before each prediction |

## Verified so far

The forecasting models are validated **out-of-sample on real public data** (no Kalshi data needed):
see [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md). The Kalshi side (client, pricing, decisions, paper
book, tracking) is tested end-to-end against a mock API serving clearly labelled synthetic responses.
It has **not** yet touched Kalshi.

## Docs

[KALSHI.md](KALSHI.md) (compliance and design) · [OWNER_SETUP.md](OWNER_SETUP.md) · [HOSTING.md](HOSTING.md) ·
[ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [PROGRESS.md](PROGRESS.md) ·
[TEST_REPORT.md](TEST_REPORT.md) · [ROADMAP.md](ROADMAP.md) · [DEPLOYMENT.md](DEPLOYMENT.md)

## Developer quick start

```bash
pip install -r requirements/core.lock pytest && pip install --no-deps -e .
pytest -q
breadmaker safety-check          # no real-money / Kalshi order or signing code, no secrets
breadmaker kalshi-status         # shows that Kalshi access is OFF and why
pip install -r requirements/research.lock && breadmaker validate-forecasts   # needs internet
```

Weather data by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0). NWS climate reports via the
Iowa Environmental Mesonet.
