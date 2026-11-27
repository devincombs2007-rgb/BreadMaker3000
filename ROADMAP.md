# Roadmap

## Done (this PR)
- [x] Preserve the equity/crypto system (`archive/trading-system-v0.2`)
- [x] Read-only Kalshi client with access gate, rate limit, backoff, pagination
- [x] Executable pricing, fee model, strike parsing; decision engine with zero allocation; $25 paper book
- [x] Weather and crypto models; structural (ladder and bracket) combination analysis
- [x] Evidence store, prediction tracking, calibration metrics, leakage-safe recalibration
- [x] Real-data validation workflow (no Kalshi data); free hosting via GitHub Actions

## Blocked on the owner
- [ ] Written authorization from Kalshi → private repo → `KALSHI_API_ENABLED=true`
- [ ] First live discovery run: confirm endpoint shapes, category list and station wording in market rules

## Next (after access)
- [ ] Measure the live coverage share per category (how many markets each model can price)
- [ ] Confirm per-series fee multipliers and settlement-source wording; tighten station/index matching
- [ ] Add models only where free, terms-compatible evidence exists (e.g. other NWS products: precipitation, snow)
- [ ] Correlation-aware exposure across same-day weather events (joint simulation from residual pools)
- [ ] ≥ 90 days of settled paper predictions before drawing any conclusion about edge

## Not planned
Real-money execution, authenticated Kalshi access, paid data feeds.
