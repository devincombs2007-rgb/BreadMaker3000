# Progress

Legend: **Implemented** (code exists) → **Tested** (automated tests pass) → **Verified**
(observed working in its real target environment). **Blocked** needs something external.

_Last update: 2026-11-26_

| Component | Status | Evidence / blocker |
|---|---|---|
| Repo structure, CI, pinned lockfiles | Tested | 68 unit tests; CI workflow (not yet run on GitHub) |
| Central config + safety validation | Tested | `test_safety_and_config.py` |
| Independent risk engine + kill switch | Tested | `test_risk_and_ledger.py`; e2e kill switch in Docker |
| Separate $1,000 ledgers ×3 | Tested | ledger tests; LumiBot smoke wrote 33 fills |
| Freqtrade dry-run engine (`BreadTrendV1`) | Tested (engine backtest) · **Blocked** for Verified | real Freqtrade backtest OK; live dry-run needs Kraken access → Oracle |
| Freqtrade sentinel (ledger mirror, halts) | Tested | fake-API tests; live mirror pending Oracle |
| LumiBot + Alpaca paper (`BreadETFRotation`) | Tested (engine backtest) · **Blocked** | needs owner's Alpaca paper keys + Oracle |
| LEAN cross-validation | Implemented, logic Tested | engine run not attempted (image size); needs data |
| Backtester, costs, metrics | Tested | no-look-ahead test |
| Strategy lab + DSR multiple-testing | Tested | rejected all 18 synthetic trials |
| ML pipeline (walk-forward, drift, registry) | Tested | leakage test validated |
| FreqAI challenger | Tested (engine backtest) | shadow mode via `--profile freqai` |
| Dashboard (auth, setup, kill, settings) | Tested + Docker e2e | iPhone viewport screenshot |
| Reports (daily/weekly) | Tested | supervisor writes at 21:30 UTC |
| Docker Compose stack | Tested (amd64 sandbox) | arm64 build pending Oracle |
| Oracle deploy (cloud-init, installer, Tailscale) | Implemented | **Blocked**: owner creates instance |
| Real market data research runs | Planned | needs network (Oracle) |
| 90-day forward paper validation | Planned | starts when bots run on Oracle |
| Real-money trading | **Not implemented by design** | requires §15 criteria + explicit owner authorization |
