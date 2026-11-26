# Architecture

```
                 iPhone (Safari)  ──Tailscale (private, HTTPS)──┐
                                                                 ▼
┌──────────────────────────── Oracle Always Free VM (Docker Compose) ───────────────────────────┐
│  dashboard (FastAPI, 127.0.0.1:8000)   FreqUI (127.0.0.1:8080)                                │
│        │ reads                                  ▲                                             │
│        ▼                                        │ REST (private network)                      │
│  ┌──────────── bm-data volume ─────────────┐   │                                             │
│  │ breadmaker.sqlite (ledgers, risk events, │◄──┤ core: supervisor + Freqtrade sentinel        │
│  │ heartbeats, experiments, models)         │   │  • mirrors dry-run trades into ledger        │
│  │ KILL_SWITCH (file)   secrets/ (0700)     │   │  • daily-loss / drawdown → kill switch       │
│  └──────▲───────────────▲───────────────────┘   │  • /stopentry on breach, reports, health     │
│         │ ro mount      │                        │                                             │
│  freqtrade (dry-run) ───┘ (reads kill switch,    lumibot (Alpaca PAPER)                        │
│  BreadTrendV1, sizing ≤10%  cannot delete it)    every order → RiskEngine → ledger             │
│  freqai (profile, shadow challenger)             lean (profile, on-demand backtests)           │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Principles

1. **Engines are upstream, unmodified.** Freqtrade runs from its official pinned image;
   LumiBot and LEAN are used as libraries/images. BreadMaker adds strategies, risk and
   accounting around them, preserving upstream compatibility.
2. **Risk is independent of strategy.** Limits live in `config/breadmaker.yaml`, validated
   against hard ceilings in `breadmaker/config.py`. Strategies and ML code receive no handle
   to change them. Three layers:
   - pre-trade: `RiskEngine.evaluate` (LumiBot) / `custom_stake_amount` + `confirm_trade_entry` (Freqtrade);
   - portfolio: `check_portfolio` - 1% daily loss halts entries, 5% drawdown engages kill switch;
   - out-of-process: the core sentinel watches Freqtrade and calls `/stopentry`; the kill switch
     is a file mounted **read-only** into trading containers.
   Exits are always permitted so risk can be reduced.
3. **Separate books.** Each engine has its own $1,000 ledger (`fills`, `equity_snapshots`).
   Broker paper balances (Alpaca gives ~$100k) are not used for performance.
4. **Idempotency.** Every order carries a `client_order_id`; the risk engine and the ledger
   both reject duplicates, so restarts or overlapping workers cannot double-trade.
5. **Persistence.** All state is SQLite (WAL) or small JSON files on a named volume; every
   service is `restart: unless-stopped` with health checks.

## Research stack

| Concern | Implementation |
|---|---|
| Look-ahead | Signals at bar *t* close execute at bar *t+1* open (`backtest.py`); tested by perturbing future data |
| Costs | `CostModel` fee + half-spread + slippage, adverse on both sides; 2× cost stress test |
| Out-of-sample | Chronological holdout (last 25%) untouched by parameter search |
| Multiple testing | Every trial stored in `experiments`; Deflated Sharpe Ratio (Bailey & López de Prado) |
| Acceptance | OOS Sharpe ≥ 0.5, DSR ≥ 0.95, OOS max DD ≥ −20%, ≥10 trades, beats buy-and-hold Sharpe, survives 2× costs |
| ML leakage | Causal features, label horizon purge gap, fold-local fitting; tests prove leak detection works |
| Drift | PSI feature drift, AUC decay; FreqAI DI outlier filter |
| Model governance | Registry with challenger → champion only on sufficient sample + material improvement; rollback |

The ML layer can only **propose** (register challengers, rank strategies). It cannot alter
risk limits, enable live trading, or execute generated code.

## Engine comparison policy

Engines trade different asset classes, so their raw returns are **not** directly comparable.
Each is compared to its own benchmark (BTC buy-and-hold; SPY buy-and-hold). Direct
comparisons are only made for equivalent data/asset/cost/period setups - e.g. LEAN vs
LumiBot on the same ETF data (`tests/test_lean_logic.py` proves the decision logic is
identical, so divergence isolates engine/fill-model effects).

## Upstream versions & licences (reviewed 2026-11-26)

| Component | Version pinned | Licence | Obligation summary |
|---|---|---|---|
| Freqtrade (+FreqUI) | 2026.10 (Docker `freqtradeorg/freqtrade:2026.10`, amd64+arm64) | GPL-3.0 | Our strategies run inside GPL software; if this repo is ever **distributed**, it should be under a GPL-3.0-compatible licence with source. Private use: no obligation. |
| LumiBot | 4.6.100 | GPL-3.0 | Same as above (`engines/lumibot` imports it). |
| QuantConnect LEAN | image `quantconnect/lean:18294` | Apache-2.0 | Keep NOTICE/attribution if redistributed. |
| LightGBM / scikit-learn / pandas / FastAPI | see `requirements/*.lock` | MIT / BSD-3 / BSD-3 / MIT | Attribution. |
| XGBoost / CatBoost (optional) | not pinned yet | Apache-2.0 | Attribution. |

No LICENSE file has been added: choosing one is the owner's decision. Recommendation: GPL-3.0.
