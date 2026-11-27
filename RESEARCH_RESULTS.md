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

## v0.4 tournament: established models vs simple baselines (real public data)

Source: GitHub Actions run
[45924219429](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45924219429) (commit `90793e6`,
2026-11-27), copied from its log. The weather and crypto numbers above reproduced exactly in this run.

**Rule:** a model counts as **better** only if the block-bootstrap 95% interval of its paired Brier
difference against the baseline lies **entirely below zero**. "No significant difference" means
**rejected**: the simpler model is kept.

### Sports: P(home team wins), walk-forward by season (challengers trained on prior seasons only)

**NFL:** 4,767 games, seasons 2009–2026, nflverse `games.csv`

| Model | Brier | vs home-rate baseline [95% CI] | Verdict |
|---|---|---|---|
| Home-win rate (baseline) | 0.2469 | | |
| Elo (FiveThirtyEight method) | 0.2198 | −0.0271 [−0.0314, −0.0231] | better |
| Logistic (Elo diff + form) | 0.2194 | −0.0275 [−0.0316, −0.0236] | better |
| LightGBM | 0.2225 | −0.0243 [−0.0285, −0.0203] | better |
| Closing betting line, vig removed (benchmark, not ours) | **0.2105** | −0.0364 [−0.0410, −0.0320] | better |

**MLB:** 30,053 games, seasons 2013–2025, Retrosheet game logs 2010–2025

| Model | Brier | vs home-rate baseline [95% CI] | Verdict |
|---|---|---|---|
| Home-win rate (baseline) | 0.2489 | | |
| Elo | 0.2434 | −0.0055 [−0.0063, −0.0047] | better |
| Logistic | 0.2433 | −0.0056 [−0.0064, −0.0047] | better |
| LightGBM | 0.2440 | −0.0048 [−0.0058, −0.0040] | better |

**NBA: not obtained.** The FiveThirtyEight `nba_elo.csv` URL now returns a non-CSV page, and the GitHub
mirror returns 404 (FiveThirtyEight shut down in 2025). Nothing was substituted, and no other free
source with a confirmed licence was found.

What this shows:
- Elo genuinely beats the naive baseline in both leagues. Baseball games are close to coin flips, so
  the gain is small there.
- **LightGBM does not beat Elo:** in the direct test it is significantly **worse** in both leagues,
  and logistic regression is statistically equal to Elo. The sophisticated model is rejected, and
  Elo stays.
- **In the NFL, the market beats every model we have** (0.2105 against Elo's 0.2198). A model worse
  than the closing line has no edge against the market, so sports markets stay **unrouted**.

**Direct test against plain Elo:** run
[45925440189](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45925440189) (commit `76eaa20`).
The sports numbers above reproduced exactly.

| League | Challenger | vs Elo [95% CI] | Verdict |
|---|---|---|---|
| NFL | Logistic | −0.0004 [−0.0009, +0.0002] | no significant difference |
| NFL | LightGBM | +0.0028 [+0.0013, +0.0043] | **worse** |
| NFL | Closing line (market) | −0.0093 [−0.0119, −0.0068] | **better than our Elo** |
| MLB | Logistic | −0.0001 [−0.0002, +0.0000] | no significant difference |
| MLB | LightGBM | +0.0006 [+0.0003, +0.0010] | **worse** |

### Economics: P(next BLS print ≥ threshold), walk-forward (refit monthly / yearly)

**CPI month-over-month:** months 2020-02 → 2026-10

Point MAE (percentage points): naive 0.200, 24-month mean 0.217, AutoReg 0.192, LightGBM 0.224.

| Model | Brier | vs naive [95% CI] | Verdict |
|---|---|---|---|
| Naive (last print) (baseline) | 0.1498 | | |
| 24-month mean | 0.1810 | +0.0312 [−0.0022, +0.0639] | no significant difference |
| AutoReg (statsmodels) | 0.1607 | +0.0110 [−0.0073, +0.0281] | no significant difference |
| LightGBM | 0.1952 | +0.0454 [+0.0172, +0.0720] | **worse** |

**Unemployment rate:** months 2020-01 → 2026-10

Point MAE: naive 0.260, mean 0.942, AutoReg 0.549, LightGBM 0.583.

| Model | Brier | vs naive [95% CI] | Verdict |
|---|---|---|---|
| Naive (baseline) | 0.1895 | | |
| 24-month mean | 0.2450 | +0.0555 [+0.0233, +0.0888] | **worse** |
| AutoReg | 0.1987 | +0.0092 [−0.0171, +0.0393] | no significant difference |
| LightGBM | 0.2446 | +0.0551 [+0.0176, +0.0971] | **worse** |

What this shows: **no model beats "same as last month."** LightGBM is significantly worse on both
series. The 2020 COVID shock dominates the errors. The data is also revised, while Kalshi settles on
the first release, so even the naive model is optimistic here. **Economics markets stay rejected.**

### Parlays: P(both cities' highs ≥ their forecasts), same day, 994–998 days per pair

| Pair | Happened | Independent (baseline) Brier | Gaussian copula: diff [95% CI] | Empirical joint: diff [95% CI] |
|---|---|---|---|---|
| NYC + Philadelphia | 55% (independence predicted 50%) | 0.2355 | −0.0017 [−0.0064, +0.0031] no sig. diff. | −0.0002 [−0.0044, +0.0042] no sig. diff. |
| NYC + Chicago | 50% | 0.2344 | +0.0008 [−0.0004, +0.0021] no sig. diff. | −0.0003 [−0.0022, +0.0015] no sig. diff. |
| NYC + Los Angeles | 45% | 0.2405 | +0.0001 [−0.0007, +0.0009] no sig. diff. | +0.0004 [−0.0010, +0.0018] no sig. diff. |
| Chicago + Denver | 51% | 0.2432 | +0.0002 [−0.0006, +0.0010] no sig. diff. | +0.0021 [+0.0005, +0.0037] **worse** |

What this shows:
- For NYC + Philadelphia, which are nearby, the outcome clearly is correlated: it happened 55% of the
  time against the 50% that independence predicts, and the copula's mean prediction of 56% matches.
  The Brier improvement is still not significant over ~1,000 days.
- **No joint model passed the gate.** They are not used to price parlays. No parlay is routed to any
  decision, and the bankroll handles correlation conservatively instead, through correlation-group
  exposure caps.

## Kalshi paper trading

**No results.** Kalshi API access is off pending written authorization, and the repository is public
(see KALSHI.md). No Kalshi prices have been observed, so there are no simulated returns, no
settlement accuracy and no drawdown to report. Nothing here should be read as evidence of an edge
on Kalshi.
