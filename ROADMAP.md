# Roadmap

## Done (v0.4, PR #3)
- [x] Fail-closed state persistence for every Actions workflow (no silent bankroll resets)
- [x] Kalshi Demo gate, separate book and workflow (off; read-only + local simulated fills)
- [x] Elo (NFL/MLB/NBA), AutoReg (CPI, unemployment), LightGBM/logistic challengers in a block-bootstrap tournament
- [x] Correlated-parlay modelling (Gaussian copula, empirical joint), measured against independence
- [x] Bankroll: uncertainty shrink, drawdown throttle, correlation-group caps, cash reserve, price bounds
- [x] Significance-gated learning; rankings dashboard; daily report + forward log + health alerts; iPhone accessibility

## Done (PR #3 review fixes)
- [x] State chain: seal-after-modify fixed; owner phrases for init / rollback / reset; append-only guard; e2e multi-cycle tests
- [x] Failure alerts even without a health report; per-workflow alert issues
- [x] Licence registry and gate; nflverse dropped; NBA from FiveThirtyEight history; first-release BLS log
- [x] Actions cost controls for private repos

## Done earlier
- [x] v0.3: Kalshi read-only client, pricing, fees, decision engine, $25 paper book, weather/crypto models, evidence, tracking
- [x] v0.2 equity/crypto system preserved on `archive/trading-system-v0.2`

## Blocked on the owner
- [ ] Written authorization from Kalshi → private repo → `KALSHI_API_ENABLED=true`
- [ ] Demo: read the Demo terms, make the repo private, set `KALSHI_DEMO_ENABLED=true`
- [ ] After merge: run daily-research once with the init phrase
- [ ] Optional: read exchange data terms and set `BREADMAKER_DATA_TERMS_ACCEPTED` to re-enable crypto
- [ ] Before going private: confirm no payment method / $0 Actions budget
- [ ] Decide whether to approve authenticated **demo** order placement (needs a demo API key; would be the first order-signing code)

## Next
- [ ] Collect ≥ 90 days of the forward forecast log before claiming live skill
- [ ] First live (or demo) discovery: confirm endpoint shapes, sports/econ series tickers and settlement wording
- [ ] Sports: only route markets where our probability beats the market, not just baselines (currently it doesn't in the NFL)
- [ ] Economics: evaluate on the self-recorded first-release log once it has enough months; ALFRED (free key) would add history, which is an owner decision
- [ ] A licence-verified source of current NBA/NFL results and of closing lines (none found)
- [ ] Crypto: switch to normal tails beyond ~12 h (better calibrated in validation)

## Not planned
Real-money execution, production order placement, paid data feeds.
