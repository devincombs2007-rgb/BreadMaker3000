# Research results

> Previous equity/crypto strategy results (all rejected) are preserved on branch
> `archive/trading-system-v0.2`.

## Forecast-model validation on real public data (no Kalshi data)

The numbers come from GitHub Actions runs
[45919313472](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45919313472) (commit `0c46f05`) and
[45919992178](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45919992178) (commit `6bafb87`,
with network retries), both on 2026-11-27, copied from their logs. Artifact `forecast-validation` holds the JSON.
All forecasts are **out-of-sample walk-forward**: each one uses only data available before its target.

**What this does and doesn't show.** It measures whether the probabilities are **accurate and
calibrated**. It says nothing about profitability on Kalshi: that depends on Kalshi's prices, which
have not been accessed (access is OFF pending authorization). A calibrated model only makes money if
the market is mispriced by more than the fees.

### Weather: P(NWS CLI daily high ≥ k), 13 thresholds per day around the forecast

The forecast was issued about 1 day ahead (Open-Meteo previous runs). The residual pool comes from
days ≤ t−2, and the truth is the NWS Daily Climate Report via Iowa Environmental Mesonet.

| Station | Days (period) | Predictions | Brier model | Brier Gaussian baseline | Brier raw point forecast | Skill vs Gaussian | Log loss | ECE |
|---|---|---|---|---|---|---|---|---|
| KNYC (NYC Central Park) | 996 (2024-03-03 → 2026-11-25) | 12,948 | 0.1293 | 0.1289 | 0.1962 | −0.004 | 0.4053 | 0.0167 |
| KMDW (Chicago Midway) | 998 | 12,974 | 0.1207 | 0.1208 | 0.1970 | 0.000 | 0.3841 | 0.0074 |
| KDEN (Denver) | 998 | 12,974 | 0.1277 | 0.1298 | 0.1838 | +0.016 | 0.4091 | 0.0103 |
| KLAX (Los Angeles) | 996 | 12,948 | 0.1069 | 0.1071 | 0.1660 | +0.002 | 0.3449 | 0.0080 |
| KAUS (Austin) | 998 | 12,974 | 0.1124 | 0.1128 | 0.1945 | +0.003 | 0.3635 | 0.0085 |
| KMIA (Miami) | 991 | 12,883 | 0.0919 | 0.0932 | 0.1611 | +0.014 | 0.2943 | 0.0096 |
| KPHL (Philadelphia) | 998 | 12,974 | 0.1161 | 0.1163 | 0.1990 | +0.002 | 0.3689 | 0.0086 |

In the first run, KMIA and KPHL failed with Open-Meteo TLS handshake timeouts, and nothing was
substituted. After bounded retries were added, the second run obtained them. The other five
stations reproduced identically.

Findings:
- **Calibrated:** expected calibration error is 0.7–1.7 percentage points at all 7 stations.
- **Uncertainty matters:** about 30–43% lower Brier than treating the point forecast as certain.
- **No meaningful extra skill over a simple Gaussian** built from the same residual statistics:
  −0.4% to +1.6%.
  The empirical-residual method is not better, just equivalent.
- Thresholds near the forecast are correlated within a day, so the effective sample size is closer
  to ~1,000 days per station than to 13,000.

### Crypto: P(price above strike at t+h), strikes at z ∈ {−1.5, −0.75, 0, +0.75, +1.5}σ

Every 6 h since 2024-01, using public hourly OHLCV (venue recorded in the artifact) and past-only
EWMA volatility. First run shown below. The second run differed slightly (for example BTC 1h Brier
0.1405, ETH 1h 0.1336; calibration error 1.0–3.2 points), most likely because the data venue or
window changed between runs. Treat the third decimal as noise.

| Asset | Horizon | n | Brier normal | Brier Student-t | ECE normal | ECE Student-t |
|---|---|---|---|---|---|---|
| BTC | 1h | 20,615 | 0.1367 | 0.1362 | 0.0190 | 0.0136 |
| BTC | 6h | 20,600 | 0.1382 | 0.1378 | 0.0214 | 0.0174 |
| BTC | 24h | 20,555 | 0.1473 | 0.1481 | 0.0184 | 0.0322 |
| ETH | 1h | 20,610 | 0.1380 | 0.1377 | 0.0166 | 0.0144 |
| ETH | 6h | 20,610 | 0.1365 | 0.1360 | 0.0206 | 0.0146 |
| ETH | 24h | 20,580 | 0.1473 | 0.1478 | 0.0200 | 0.0302 |

Findings:
- Calibration error is 1.4–3.2 points.
- Fat (Student-t) tails help at 1–6 h, but the normal model is better calibrated at 24 h.
- The live model uses Student-t for all horizons; switching to normal beyond ~12 h is a supported
  follow-up.
- Basis risk is not captured: Kalshi settles on an index, not one exchange's close.
- Overlapping windows mean the effective sample is smaller than n.

## Kalshi paper trading

**No results.** Kalshi API access is off pending written authorization, and the repository is public
(see KALSHI.md). No Kalshi prices have been observed, so there are no simulated returns, no
settlement accuracy and no drawdown to report. Nothing here should be read as evidence of an edge
on Kalshi.
