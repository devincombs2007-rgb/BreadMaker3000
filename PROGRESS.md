# Progress

Legend: **Implemented** → **Tested** (automated tests pass) → **Verified** (observed working on real
data or in its real environment). **Blocked** needs something external.

_Last update: 2026-11-27 (v0.4, after PR #3 review)_

| Component | Status | Evidence / blocker |
|---|---|---|
| Archive of equity/crypto system | Done | branch `archive/trading-system-v0.2` |
| Kalshi read-only client (gate, rate limit, backoff, pagination) | Tested (mock API, synthetic) | `tests/test_kalshi.py` - **Blocked** for live: awaiting Kalshi written authorization; repo public |
| Kalshi endpoint/field shapes | Implemented from Trade API v2 as known at build time | not re-checkable (docs.kalshi.com unreachable from build sandbox); confirm on first authorized run |
| Market discovery across categories | Tested (mock) | live counts unknown until access |
| Executable pricing + fees | Tested | fee formula conservative (rounds up) |
| Weather model | **Verified on real data** (7 stations × ~997 days, out-of-sample) | RESEARCH_RESULTS.md |
| Crypto model | Verified on real data earlier; **blocked** pending exchange-terms verification | RESEARCH_RESULTS.md |
| Structural combinations | Tested (synthetic books) | needs live order books |
| MLB / NBA Elo + logistic / LightGBM | **Verified on real data** (tournament) | Elo beats baselines; LightGBM worse than Elo; FiveThirtyEight beats our NBA Elo → **not routed** |
| NFL | nflverse results **withdrawn** (no licence) | FiveThirtyEight `nfl_elo.csv` also unobtainable (run 45928630946) → **no NFL results** |
| NBA | **Verified on historical data** (1983–2015) | live data has no licensed free source |
| Licence gate (registry, refuse unverified sources) | Tested | crypto blocked until the owner accepts exchange terms |
| First-release BLS log | Tested | starts accumulating after merge |
| CPI / unemployment (AutoReg, LightGBM) | **Verified on real data: rejected** | nothing beats naive; LightGBM significantly worse |
| Parlays (Gaussian copula, empirical joint) | **Verified on real data: not adopted** | no significant gain over independence on 4 city pairs |
| NCAA, Fed, politics, entertainment, technology | **Rejected by design** | DATA_SOURCES.md: no free, terms-compatible source |
| Kalshi Demo integration (read-only + local simulated fills) | Tested (mock) | **Off**: needs owner to enable + private repo; authenticated demo orders not built |
| Bankroll controls (uncertainty, drawdown throttle, group caps, reserve) | Tested | `tests/test_bankroll_learning.py` |
| Significance-gated recalibration | Tested | small real gains deliberately not adopted |
| Rankings (API, dashboard cards, Markdown) | Tested | no live markets to rank yet |
| Fail-closed state chain | Tested (16 tests incl. 7- and 5-cycle end-to-end runs on a fake artifact store) | seal-after-modify bug fixed; first real run after merge (owner init phrase) |
| Daily research: forward log, health checks, issue alert | Tested (mocked feeds/API) | alerts also when no report was written; starts after merge + init |
| Private-repo Actions cost controls | Tested (workflow policy tests) | owner must confirm no payment method / $0 budget |
| iPhone accessibility (44 px targets, 16 px inputs, landmarks, reduced motion) | Implemented | |
| Decision engine, zero allocation, $25 paper book | Tested | no live prices yet |
| Evidence store, prediction tracking, calibration | Tested (incl. leakage scenario) | no settled live predictions yet |
| Dashboard Kalshi section | Implemented, tested via summary API | |
| GitHub Actions: CI (amd64 + arm64), validation, daily research, gated Kalshi + Demo cycles | CI + validation green; Kalshi cycles correctly disabled | |
| Always-on hosting | Optional | HOSTING.md |
| Real-money trading | **Not implemented by design** | |
