# 🍞 BreadMaker3000

A quantitative trading **research and paper-trading** platform that coordinates three
independent open-source engines under one risk engine, one accounting system and one
mobile dashboard.

> **Simulation only.** This release has no real-money execution path. Freqtrade is locked to
> dry-run, Alpaca to paper trading, and every configuration is checked for live endpoints
> before anything starts. Nothing here is a claim of profitability.

| Engine | Role | Market | Virtual capital |
|---|---|---|---|
| **Freqtrade** (+ FreqAI) | Crypto spot dry-run, ML research | Kraken public data | $1,000 |
| **LumiBot** | US ETF paper trading | Alpaca paper | $1,000 |
| **QuantConnect LEAN** | Independent cross-validation backtests | Local data | $1,000 |

## What is in the box

- `breadmaker/` - core platform: independent **risk engine** + kill switch, isolated
  **virtual ledgers**, cost model, metrics, leakage-safe **backtester**, **strategy lab**
  with Deflated-Sharpe multiple-testing correction, **ML pipeline** (walk-forward,
  drift, champion/challenger, rollback), orchestrator, reports, **mobile dashboard**.
- `engines/freqtrade/` - dry-run config, `BreadTrendV1` baseline, `BreadFreqAIV1` challenger.
- `engines/lumibot/` - `BreadETFRotation` (trend + relative strength), Alpaca paper runner.
- `engines/lean/` - `BreadLeanTrend` cross-check algorithm + LEAN data converter.
- `deploy/` - Oracle Cloud cloud-init, installer, Tailscale private access.
- `docker-compose.yml` - the whole stack; every port bound to `127.0.0.1`.

## Quick links

- **Owner (iPhone) setup:** [OWNER_SETUP.md](OWNER_SETUP.md)
- Status: [PROGRESS.md](PROGRESS.md) · Tests: [TEST_REPORT.md](TEST_REPORT.md) · Plan: [ROADMAP.md](ROADMAP.md)
- Design: [ARCHITECTURE.md](ARCHITECTURE.md) · Deploy: [DEPLOYMENT.md](DEPLOYMENT.md) · Security: [SECURITY.md](SECURITY.md)

## Developer quick start

```bash
pip install -r requirements/core.lock pytest && pip install --no-deps -e .
pytest -q                      # core test suite
breadmaker safety-check        # scans for live-trading paths and secrets
breadmaker research data.feather --asset crypto   # baseline strategy lab
```
Engine smoke tests (real engines, synthetic data) are in `engines/*/tests/` and run in CI.
