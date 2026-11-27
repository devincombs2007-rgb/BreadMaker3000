# Kalshi prediction-market intelligence

**Simulation only.** BreadMaker never places orders, holds no Kalshi credentials, and contains no
authenticated or order-placing Kalshi code (enforced by `breadmaker safety-check` in CI).

## Status: Kalshi API access is OFF

Kalshi's Developer Agreement v1.1 (read 2026-11-27 from
`kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf`) says:

- **§3:** "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the
  Exchange; all other usages are disallowed and may result in account suspension."
- **§3.1** prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API
  except for purposes of facilitating your own trading on Kalshi. You may not share such data or
  content with third parties in any manner without prior written authorization from Kalshi."
- **§4.1.1:** "You must be a Member of Kalshi's Exchange".
- **§4.3** requires bounded retries with exponential backoff.
- **§5:** Kalshi may set or change rate limits at any time.

A simulation-only research tool is a grey area under §3. The owner chose to **ask Kalshi for written
authorization first**. Until then, nothing in this repository contacts Kalshi.

The repository is **public**. Even with authorization, storing Kalshi data in a public repo's
Actions artifacts, logs or summaries would share it with third parties (§3.1). The scheduled
workflow therefore refuses to run in a public repository.

### Turning access on (only after written authorization)

All three gates are required:

1. Repository **private**, or the stack running on your own private server.
2. `config/breadmaker.yaml` → `kalshi.api_enabled: true`. The GitHub workflow sets this per run;
   it is never committed.
3. Environment `KALSHI_ACCESS_AUTHORIZED=written-authorization-on-file`. On a server this goes in
   `.env`; the workflow sets it after the guard job passes. For GitHub Actions, also set the
   repository variable `KALSHI_API_ENABLED=true`.

## Kalshi Demo (mock funds) - prepared, OFF

Kalshi's docs describe a separate Demo environment with **mock funds** and **separate accounts and keys**
(demo keys only work on demo endpoints). Recommended host: `external-api.demo.kalshi.co` (legacy
`demo-api.kalshi.co` still works).

| Property | Implementation |
|---|---|
| Separate gate | `kalshi_demo_access_allowed`: only demo hosts, flag `kalshi_demo.api_enabled`, env `KALSHI_DEMO_TERMS_ACCEPTED=demo-terms-accepted` |
| Gates cannot cross | Production gate accepts only `api.elections.kalshi.com`; the demo gate refuses it; neither attestation opens the other (tested) |
| Redirect guard | Client refuses any response served from a different host |
| Separate book | Ledger engine `kalshi_demo`, own state artifact `kalshi-demo-state` |
| Workflow | `kalshi-demo.yml`: variable `KALSHI_DEMO_ENABLED=true` **and** private repo; edits only the demo section of the config |
| Scope | Read-only demo market data plus **local** simulated fills. Authenticated demo order placement (request signing with a demo key) is **not built**. It would be the first order-placing code in the repo and needs your explicit approval and a demo API key. |

Demo data is still Kalshi API data. The Developer Agreement and any demo-specific terms apply, and
demo data must stay private like production data.

## Pipeline

```
discover open events/markets (all categories; paginated; <=5 req/s; backoff on 429/5xx)
  -> route each market:  weather_high_temp | crypto_price | none -> rejected(reason)
  -> order book -> executable YES/NO asks + sizes (asks derived from the opposite side's bids)
  -> evidence (timestamped, hashed) -> forecast {p, p_low, p_high} or Rejection(reason)
  -> decision: conservative edge after fees >= min_edge ? fractional-Kelly size within caps : ZERO
  -> paper fill on the $25 ledger -> later: settle at $1/$0 from Kalshi's published result
  -> per event: model-free ladder / bracket combinations (locked payoff only)
```

| Piece | File |
|---|---|
| Read-only client, gate, rate limit, backoff | `breadmaker/kalshi/client.py`, `config.py` |
| Market and order-book parsing, fees | `breadmaker/kalshi/markets.py` |
| Decision engine (zero allocation by default) | `breadmaker/kalshi/decision.py` |
| Paper book on the shared ledger | `breadmaker/kalshi/paper.py` |
| Prediction log, metrics, recalibration | `breadmaker/kalshi/tracking.py` |
| Evidence store | `breadmaker/evidence.py` |
| Models | `breadmaker/forecasting/{weather,crypto,structural}.py` |
| Real-data validation | `breadmaker/forecasting/validation.py`, `.github/workflows/forecast-validation.yml` |

## Models and category coverage

| Category | Model | Evidence | Status |
|---|---|---|---|
| Weather: daily high (KXHIGH*) | Forecast issued about 1 day ahead, plus a past-only residual pool against the NWS CLI high (integer °F, local standard-time day) | Open-Meteo, NWS CLI via IEM | Validated on real data (see RESEARCH_RESULTS.md). Trades only if the market's rules text names the configured station. |
| Crypto price thresholds | Zero-drift log-normal or Student-t with EWMA volatility; volatility band as the uncertainty | Public exchange OHLCV | Validated on real data. Basis risk: Kalshi settles on an index, not one exchange. |
| Any event with strike ladders or brackets | Structural (model-free) | Order books only | Unit-tested; needs live books |
| NFL / MLB / NBA game winners | Elo (FiveThirtyEight methodology) + logistic/LightGBM challengers | nflverse, Retrosheet, FiveThirtyEight | NFL/MLB: Elo **beats** baselines; LightGBM does not beat Elo; the NFL closing line beats every model. NBA source unavailable. **Not routed** (no edge over the market; mapping unverified) |
| Economics: CPI MoM, unemployment | statsmodels AutoReg + baselines + LightGBM | BLS API v1 | **Rejected by the tournament**: nothing beats naive; LightGBM significantly worse (and the data is revised, not first-release) |
| NCAA, Fed decisions, politics, entertainment, technology, other | **None** | No free, reliable, terms-compatible source (DATA_SOURCES.md) | **Rejected with reason** |

Economics would need data feeds (CPI and jobs nowcasts, Fed funds futures) that are paid, need
keys, or have unclear terms. Sports needs odds or Elo data under compatible terms. Rejection is the
honest default: the system does not guess.

## Pricing and fees

- Binary contract pays $1. YES ask = 1 − best NO bid. Both cent integers and `*_dollars` fields are
  accepted, because Kalshi has been migrating between them.
- Taker fee = ceil-to-cent(0.07 × C × P × (1 − P)). Kalshi's newer sub-cent rounding can only make
  fees equal or lower, so this is conservative. Per-series overrides can only **raise** the modelled
  rate.
- The live docs (docs.kalshi.com) were not reachable from the build environment. Endpoint shapes
  follow Trade API v2 as known at build time and will be confirmed on the first authorized run.

## Combinations and parlays

`breadmaker/forecasting/joint.py` combines leg probabilities with a **Gaussian copula**, or with the
**empirical joint** of historical same-date residual vectors. Its uncertainty interval comes from
perturbing the marginal probabilities and the correlation. Independence (multiplying the leg
probabilities) is the baseline. On four real same-day city pairs (about 1,000 days each), **neither
joint model beat independence significantly**, and the empirical joint was worse on one pair
(RESEARCH_RESULTS.md). The joint models are therefore **not used to price** anything. Correlation is
handled on the risk side instead, through correlation-group exposure caps.

## Risk controls (simulated $25)

| Control | Default | Hard bound |
|---|---|---|
| Fractional Kelly | 0.25 | ≤ 0.5 |
| Max cost in one market | 10% ($2.50) | ≤ 25% |
| Max per event | 20% | ≤ 50% |
| Max total exposure | 50% | ≤ 100% |
| Min edge (conservative p − price − fee per contract) | 0.03 | ≥ 0.01 |
| Max spread | 0.10 | ≤ 0.5 |
| Correlation-group budget (e.g. all same-day weather markets) | 25% | ≤ 50% |
| Cash reserve | 50% of equity | ≥ 25% |
| Uncertainty shrink | size × (1 − interval width / 0.5) | |
| Drawdown throttle | size falls linearly to 0 at 20% drawdown | ≤ 30% |
| Price bounds | 3¢ – 97¢ | |
| Max open positions | 10 | ≤ 50 |
| Daily loss halt / drawdown halt | 5% / 20% | via the shared risk engine |

The global kill switch and per-engine halts from the shared risk engine apply.

## Learning without leakage

- **Weather residual pools** use days up to t−2 only, because the t−1 observation isn't final when
  the forecast is issued (tested).
- **Recalibration** for a prediction is fitted only on predictions **settled before it was issued**.
  It is adopted only if the walk-forward improvement is **statistically significant**: the
  block-bootstrap 95% interval must lie entirely below zero. This is deliberately conservative.
  A real but small improvement on 350 predictions is **not** adopted (tested).
- **Evidence** carries fetch timestamps; `EvidenceStore.available_at` checks that nothing was
  fetched after the decision time.
