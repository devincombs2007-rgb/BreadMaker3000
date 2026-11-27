# Progress

Legend: **Implemented** (code exists) → **Tested** (automated tests pass) → **Verified**
(observed working in its real target environment). **Blocked** needs something external.

_Last update: 2026-11-26 (iteration 2)_

| Component | Status | Evidence / blocker |
|---|---|---|
| Central config + safety validation | Tested | unit tests; CI |
| Cloud-init credential handling | **Fixed + Tested** | `test_bootstrap_secrets.py` executes the real bootstrap (also under `bash -x`, non-root path) |
| Independent risk engine, per-engine halts, kill switch | Tested | `test_risk_and_ledger.py`, `test_risk_audit.py` |
| Freqtrade fail-closed risk gate | Tested **inside real Freqtrade** | `engines/freqtrade/tests/test_gate_integration.py` (CI) |
| Risk audit across engines | Done | [RISK_AUDIT.md](RISK_AUDIT.md): 10 findings fixed, 5 residual risks documented |
| Separate $1,000 ledgers ×3 (+ FreqAI shadow book) | Tested | partial-fill + reconciliation tests on real LumiBot objects |
| Freqtrade dry-run engine | Tested (engine backtests) · **Blocked** for Verified | needs a host with exchange access |
| LumiBot + Alpaca paper | Tested (engine backtests) · **Blocked** | needs host + owner's paper keys |
| LEAN cross-validation | Implemented, logic + risk gate Tested | engine run not attempted (image size) |
| ARM64 compatibility | **Tested** on native `ubuntu-24.04-arm` (CI run 45914520313, all steps green) | not yet run on an ARM host |
| Real-data research workflow | Implemented | GitHub Actions `research.yml`; see RESEARCH_RESULTS.md |
| Free hosting | Investigated | [HOSTING.md](HOSTING.md); Oracle A1 capacity-blocked; next: Oracle E2.1.Micro or GCP e2-micro |
| Low-memory deployment profile | Implemented, config validated | `docker-compose.micro.yml`; idle RSS measured for core/dashboard/freqtrade start-up only |
| 90-day forward paper validation | Not started | requires a running host |
| Real-money trading | **Not implemented by design** | requires §15 criteria + explicit owner authorization |
