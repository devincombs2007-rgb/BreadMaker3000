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
| Size | $25 simulated bankroll: uncertainty shrink, drawdown throttle, correlation-group caps, 50% cash reserve, price bounds, max open positions. Every rejection reason is recorded. |
| Rank | Dashboard and Markdown cards for allocations, near-misses and rejection reasons, built for phone width |
| Learn | Walk-forward recalibration, adopted only if it is significantly better than no recalibration |
| Report | Daily research run: a pre-registered forward forecast log on public data, settlement, health checks, and a GitHub issue alert if anything breaks |
| Persist | A fail-closed state chain: a skipped run, an expired artifact or a failed download stops the simulation with "STATE LOST" instead of silently resetting the bankroll |

## Verified so far

The forecasting models are validated **out-of-sample on real public data** (no Kalshi data needed):
see [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md). Summary:

- **Weather and crypto:** calibrated to within 1–3 points.
- **NFL/MLB Elo:** significantly beats naive baselines, but in the NFL it loses to the closing betting
  line.
- **Machine-learning challengers:** LightGBM never beat the simpler established models.
- **Economics:** nothing beat "same as last month".
- **Parlays:** correlation-aware joint models were not significantly better than independence.

Only models that passed are eligible for routing, and sports and economics still route nothing.

The Kalshi side (client, pricing, decisions, paper
book, tracking) is tested end-to-end against a mock API serving clearly labelled synthetic responses.
It has **not** yet touched Kalshi.

## Docs

[KALSHI.md](KALSHI.md) (compliance and design) · [OWNER_SETUP.md](OWNER_SETUP.md) · [HOSTING.md](HOSTING.md) ·
[ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [PROGRESS.md](PROGRESS.md) ·
[TEST_REPORT.md](TEST_REPORT.md) · [ROADMAP.md](ROADMAP.md) · [DATA_SOURCES.md](DATA_SOURCES.md) · [DEPLOYMENT.md](DEPLOYMENT.md)

## Developer quick start

```bash
pip install -r requirements/core.lock pytest && pip install --no-deps -e .
pytest -q
breadmaker safety-check          # no real-money / Kalshi order or signing code, no secrets
breadmaker kalshi-status         # shows that Kalshi access is OFF and why
pip install -r requirements/research.lock && breadmaker validate-forecasts   # needs internet
```

Weather data by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0). NWS climate reports via the
Iowa Environmental Mesonet. Economic data from the U.S. Bureau of Labor Statistics. NFL data from nflverse.
The information used here was obtained free of charge from and is copyrighted by Retrosheet.
Interested parties may contact Retrosheet at www.retrosheet.org.
