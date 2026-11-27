# Progress

Legend: **Implemented** → **Tested** (automated tests pass) → **Verified** (observed working on real
data or in its real environment). **Blocked** needs something external.

_Last update: 2026-11-27 (Kalshi pivot)_

| Component | Status | Evidence / blocker |
|---|---|---|
| Archive of equity/crypto system | Done | branch `archive/trading-system-v0.2` |
| Kalshi read-only client (gate, rate limit, backoff, pagination) | Tested (mock API, synthetic) | `tests/test_kalshi.py` - **Blocked** for live: awaiting Kalshi written authorization; repo public |
| Kalshi endpoint/field shapes | Implemented from Trade API v2 as known at build time | not re-checkable (docs.kalshi.com unreachable from build sandbox); confirm on first authorized run |
| Market discovery across categories | Tested (mock) | live counts unknown until access |
| Executable pricing + fees | Tested | fee formula conservative (rounds up) |
| Weather model | **Verified on real data** (7 stations × ~997 days, out-of-sample) | RESEARCH_RESULTS.md |
| Crypto model | **Verified on real data** (BTC/ETH, 1/6/24 h, ~20k each) | RESEARCH_RESULTS.md |
| Structural combinations | Tested (synthetic books) | needs live order books |
| Other categories (sports, politics, economics, entertainment, tech) | **Rejected by design** | no free, terms-compatible evidence source integrated |
| Decision engine, zero allocation, $25 paper book | Tested | no live prices yet |
| Evidence store, prediction tracking, calibration, recalibration | Tested (incl. leakage scenario) | no settled live predictions yet |
| Dashboard Kalshi section | Implemented, tested via summary API | |
| GitHub Actions: CI, arm64, validation, gated Kalshi cycle | CI + validation running; Kalshi cycle correctly disabled | |
| Always-on hosting | Optional | HOSTING.md |
| Real-money trading | **Not implemented by design** | |
