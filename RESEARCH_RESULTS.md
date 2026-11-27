# Research results - real historical data

**Simulated backtests only.** Historical results are not forward performance and nothing here
is a profitability claim. Every number below was produced by GitHub Actions run
[45913970684](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45913970684)
(2026-11-26, commit `9ee6bcf`) and copied from its log; artifacts `research-results` and
`freqtrade-research-results` hold the full JSON. Re-run: Actions → research → Run workflow.

## Data (downloaded in the run; SHA-256 per file in `manifest.json`)

| Set | Venue | Range | Notes |
|---|---|---|---|
| BTC/ETH/SOL daily | Binance.US (binance.com did not serve the US runner; fallback worked) | BTC/ETH 2019-09 → 2026-11, SOL 2020-09 → | quote currency per venue; costs still modelled on Kraken |
| BTC/ETH/SOL hourly | Binance.US | 2023-01-01 → 2026-11-26 (20,134 bars each) | |
| 10 ETFs daily (SPY QQQ IWM EFA EEM TLT IEF GLD VNQ DBC) | Yahoo (split/dividend adjusted) | 2007-01-03 → 2026-11-25 (5,007 bars) | First run flagged 6 sets "high/low inconsistent" (validator had no tolerance). Re-run [45914339683](https://github.com/devincombs2007-rgb/BreadMaker3000/actions/runs/45914339683) with a 1e-6 relative tolerance: **all 16 datasets clean** → the flags were adjustment rounding, not defects. Survivorship bias: today's tickers only. |

## 1. Freqtrade `BreadTrendV1` (the deployed crypto baseline) - **REJECTED**

Real Freqtrade 2026.10 engine, real 1h candles, BTC/ETH/SOL, 2023-01-10 → 2026-11-26, 10% stakes,
5% stop. Does **not** include BreadMaker's portfolio halts (which would have stopped it early).

| Costs | Trades | Total return | Max DD | Sharpe | Win rate | Profit factor | Buy & hold same coins |
|---|---|---|---|---|---|---|---|
| 1x (0.55%/side) | 185 | **−23.8%** | −24.5% | −0.87 | 21.6% | 0.44 | +204.3% |
| 2x (1.10%/side) | 185 | **−38.1%** | −38.6% | −1.47 | 15.7% | 0.27 | +201.0% |

Reading: frequent 1h EMA-cross entries are whipsawed; ~$180 of estimated fees on a $1,000 book.
It loses money before and after costs and massively lags buy-and-hold. It remains in dry-run **only
as an infrastructure test** (order flow, sentinel, halts); it must not be considered a candidate.

## 2. Crypto daily baseline lab (signal study, full exposure, Kraken-level costs) - **0 accepted**

17 parameter sets per coin (all recorded for multiple-testing correction), last 25% held out.

| Coin | Accepted | Best OOS Sharpe (why rejected) | Buy & hold OOS Sharpe |
|---|---|---|---|
| BTC | 0 / 17 | −0.38 (ts_momentum 180) | −0.82 |
| ETH | 0 / 17 | 1.05 (SMA 10/100: only 3 trades, DSR 0.82 < 0.95, max DD −28%) | −0.17 |
| SOL | 0 / 17 | −0.07 (Donchian 55/10, 2 trades) | −1.26 |

In-sample Sharpes of 0.7-1.6 collapsed out of sample - the classic overfitting pattern the gates
exist to catch.

## 3. ETF rotation (deployed LumiBot logic) 2007 → 2026, split 2018-12-07

Strategy-only, 1x costs (fees + 1 bp half-spread + 3 bp slippage), fills at next open:

| Series | Segment | Total return | CAGR | Sharpe | Max DD |
|---|---|---|---|---|---|
| Strategy | full | +41.1% | +1.8% | 0.65 | −6.4% |
| Strategy | out-of-sample | +19.1% | +2.2% | 0.82 | −3.7% |
| SPY buy & hold | full | +577.5% | +10.6% | 0.61 | −54.7% |
| SPY buy & hold | out-of-sample | +206.5% | +15.1% | 0.82 | −33.7% |
| SPY at the strategy's own exposure | full | +58.7% | +2.5% | 0.68 | −11.2% |
| SPY at the strategy's own exposure | out-of-sample | +26.1% | +3.0% | 0.85 | −6.5% |
| Equal-weight universe | full | +293.7% | +7.5% | 0.58 | −39.2% |

| Run (full period) | Total return | Sharpe | Max DD | Avg exposure | Costs paid | Drawdown halt |
|---|---|---|---|---|---|---|
| strategy, 1x costs | +41.1% | 0.65 | −6.4% | 21.7% | $33.59 | - |
| strategy, 5x costs | +25.8% | 0.44 | −8.1% | 21.7% | $158.31 | - |
| **as deployed (with 1%/5% halts)**, 1x | +17.3% | 0.37 | −5.7% | 14.8% | $21.76 | **2020-02-28** |

Reading:
- The low drawdown comes from **low exposure (~22%)**, not selection skill: SPY held at the same
  exposure did slightly better (Sharpe 0.68 vs 0.65; OOS return +26.1% vs +19.1%).
- Verdict checks: OOS Sharpe positive ✔, beats SPY Sharpe OOS ✘, beats exposure-matched SPY ✘,
  profitable at 5x costs ✔.
- **Risk-limit interaction:** with the 5% drawdown halt, the deployed strategy stopped buying on
  2020-02-28 (COVID crash) and - since research never releases halts - stayed frozen. Live, the owner
  would have to release it. The 10% cap + 0.5% risk + 5% halt combination keeps the book mostly in
  cash; that is a deliberate safety choice, but it also caps any possible return.

## Conclusions

1. No strategy tested has credible evidence of an edge. None qualifies for promotion.
2. The crypto baseline should be redesigned for far lower turnover (daily/4h, fewer, longer trades)
   before any further evaluation; costs dominate at the 1h horizon.
3. The ETF rotation is a safe, low-exposure allocation, not an alpha source on this evidence.
4. The 5% permanent drawdown halt materially changes outcomes; the owner should decide whether that
   threshold is intended for a $1,000 book (it is not changed by this work).
