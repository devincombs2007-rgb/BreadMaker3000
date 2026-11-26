# Test report - 2026-11-26

Environment: Linux x86_64 cloud sandbox, Python 3.12, Docker 29.9. Exchange/broker APIs
(Kraken, Alpaca, Yahoo) and Debian apt mirrors were **blocked by the sandbox network
policy**, so no test here touched live market data. All market data below is **synthetic**
and says nothing about profitability.

## 1. Core unit tests - `pytest -q`: **68 passed, 0 failed**

| Area | What is proven |
|---|---|
| Safety/config | live mode rejected; risk ceilings; Alpaca live URL/flag/`AK` keys rejected; Freqtrade dry-run/spot/no-margin/no-keys enforced; repo safety scan clean and detects planted problems |
| Risk engine | sizing (0.5% risk, 10% cap, cash); duplicate IDs; stale/naive/future timestamps; stop required; no leverage; no shorting; daily-loss halt; drawdown → persistent kill switch; exits allowed under kill switch; rejections logged |
| Ledger | isolated books; idempotent fills; fee-aware realized P&L; mark-to-market; blocks shorts and overspending |
| Backtester | **no look-ahead** (future perturbation leaves past equity identical); next-bar-open execution; costs reduce returns; long-only/no leverage |
| Research | purged walk-forward splits; chronological holdout; DSR penalises many trials; every trial recorded |
| ML | causal features; label NaN tail; walk-forward runs (AUC in 0.35-0.65 on random data); **leak detector validated** (leaked label → AUC > 0.9); PSI drift; champion/challenger/rollback |
| Services | NYSE 2026 holidays & early close; Freqtrade sentinel mirroring, live-mode kill, drawdown halt, exit-before-entry ordering; heartbeats; report banner shows losses |
| Dashboard | auth required; login/CSRF/kill/release flow; rate limit; security headers; first-run setup locks after use (409); Alpaca settings: live key rejected, wrong password rejected, secret never returned, file mode 0600 |
| Cross-engine | LEAN and LumiBot decision logic produce identical selections and weights; LEAN data file format |

## 2. Real engines, offline smoke tests (synthetic data)

| Engine | Result |
|---|---|
| Freqtrade 2026.10 backtester + `BreadTrendV1` | 20 trades, final 1000.40 USD, max stake 100.75 (10.07% of start - cap is 10% of *current* equity), no shorts, leverage 1.0 |
| Freqtrade 2026.10 FreqAI + `BreadFreqAIV1` (LightGBMRegressor) | 5 sliding-window retrains/pair, 86 features, 10 trades, max stake 99.19 (9.9%), no shorts, leverage 1.0 |
| LumiBot 4.6.100 `PandasDataBacktesting` + `BreadETFRotation` | 33 fills through RiskEngine into $1,000 ledger, max buy $92.34, **0 spurious rejections** after fix (see below) |

**Bugs found by these tests and fixed:** (1) LumiBot quantities were rounded *up* past the
risk-sized maximum → now floored and clamped to the risk engine's max at the live price;
(2) Docker images resolved the config path inside site-packages → `BREADMAKER_CONFIG` set;
(3) dashboard asset-class label missing; (4) research CLI lacked `pyarrow` for feather data.

## 3. Strategy lab on synthetic random-walk data (gate check)

`breadmaker research` evaluated 18 parameter sets across 4 baseline families.
**0 accepted.** The best-looking (SMA 50/200: OOS Sharpe 1.79, +21%) was rejected for
DSR 0.67 < 0.95, drawdown −20.8%, and only 5 trades - the gates catch a lucky backtest.

`breadmaker ml-eval`: logreg AUC 0.496, random forest 0.494, LightGBM 0.489 on 6,900 OOS
bars (no skill, no leakage). Naively trading those signals with crypto costs lost ~97% -
a reminder that turnover × costs dominates weak signals.

## 4. Docker Compose stack (sandbox)

Built `core` and `lumibot` images (sandbox-only overlay added the proxy CA; committed
Dockerfiles unchanged), pulled `freqtradeorg/freqtrade:2026.10`, ran the stack:

- core, dashboard, lumibot: **up**; freqtrade: loads config + strategy, then **cannot reach
  Kraken from the sandbox** (`ExchangeNotAvailable`) and restarts - expected here.
- Sentinel recorded `engine_unreachable`; dashboard showed it.
- HTTP e2e: `/` → `/setup`; password set; second setup → 409; unauthenticated API → 401;
  emergency stop wrote `KILL_SWITCH`; Freqtrade container could **read** it and **could not
  delete it** (read-only mount); release required password.
- iPhone 13 viewport (Playwright): renders, no horizontal scroll.
- gitleaks v8.30.1: **no leaks** in the working tree.

## 5. Not tested (and why)

| Item | Reason |
|---|---|
| Live Freqtrade dry-run against Kraken | exchange blocked in sandbox; first real run happens on Oracle |
| Alpaca paper connection | no keys (owner step) and host blocked |
| LEAN engine execution | image is 14 GB (amd64); logic + data format unit-tested only |
| arm64 image builds | sandbox is amd64; CI builds amd64; first arm64 build happens on Oracle |
| install.sh / cloud-init on Oracle | no instance yet (owner step) |
| 90-day forward paper performance | requires time |
