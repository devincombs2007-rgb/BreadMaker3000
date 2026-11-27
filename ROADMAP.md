# Roadmap

## Phase 1 - Foundation ✅
Repo structure, architecture, upstream review (versions, licences, arm64), CI, pinned locks.

## Phase 2 - First paper engines ✅ (code) / ⏳ (live)
- [x] Freqtrade dry-run config + `BreadTrendV1` baseline, risk sizing, kill switch hook
- [x] LumiBot + Alpaca paper `BreadETFRotation` baseline, risk engine gate, virtual ledger
- [x] Independent portfolio accounting
- [ ] First live dry-run / paper trades on Oracle (owner setup)

## Phase 3 - Validation & risk ✅ (code)
- [x] Independent risk engine, sentinel, kill switch
- [x] Backtester, cost model, metrics, strategy lab with DSR
- [x] LEAN cross-check algorithm + data converter
- [x] Real history downloaded + lab run on GitHub Actions (RESEARCH_RESULTS.md): baseline crypto rejected
- [ ] Redesign crypto baseline for low turnover (4h/1d), re-test through the same gates
- [ ] First LEAN vs LumiBot equivalence run on identical data

## Phase 4 - Machine learning (in progress)
- [x] Walk-forward ML pipeline, drift monitors, champion/challenger registry
- [x] FreqAI LightGBM challenger (shadow profile)
- [ ] Weekly bounded research job (cron in supervisor) writing to experiment registry
- [ ] XGBoost/CatBoost pinned + evaluated; ensemble only if it beats single models OOS
- [ ] Regime classifier (volatility/trend) feeding allocation, evaluated vs baseline

## Hosting (Oracle A1 capacity-blocked)
- [x] Investigate free hosts (HOSTING.md); low-memory profile
- [ ] Owner: try Oracle E2.1.Micro, else Google Cloud e2-micro

## Phase 5 - Operations
- [x] Mobile dashboard, reports, health, Compose deployment, cloud-init
- [ ] Verify on Oracle arm64; 7-day continuous-operation soak test
- [ ] Equity curve per-engine benchmark overlay; report delivery (e.g. email) - optional
- [ ] Image digests pinned; automated weekly backups to Oracle Object Storage (free tier)

## Gate to ever propose real money (not before ~Q2 2027)
≥90 days forward paper, positive after costs, credible OOS evidence, benchmark-relative
performance, sufficient trades, acceptable drawdown, operational reliability, independent
review, **explicit owner authorization**. Extend if inconclusive.
