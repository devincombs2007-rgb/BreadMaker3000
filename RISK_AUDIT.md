# Risk-enforcement audit (2026-11-26)

> Scope: the Freqtrade/LumiBot/LEAN engines, now on branch `archive/trading-system-v0.2`. The shared
> risk-engine findings (F2 persistent halts, F7 per-engine halts, F10 logging) still apply to the Kalshi
> book; Kalshi-specific controls are in KALSHI.md and SECURITY.md.

Question: can any engine, especially Freqtrade, bypass BreadMaker's limits (10% position,
0.5% risk/trade, 1% daily-loss halt, 5% drawdown halt, stale-data and duplicate-order rejection,
no shorts/leverage)? Method: read upstream source (Freqtrade 2026.10, LumiBot 4.6.100), trace every
entry path, then write tests that run inside the real engines.

## Findings and fixes

| # | Engine | Finding (before) | Severity | Fix | Proof |
|---|---|---|---|---|---|
| F1 | Freqtrade | `confirm_trade_entry` is called through `strategy_safe_wrapper(..., default_retval=True)` (freqtradebot.py) - **an exception in the hook APPROVES the entry** (fail-open). | High | New `bread_risk_gate.entry_allowed` catches everything and returns *deny*; strategies only return its result. | `test_freqtrade_wrapper_is_fail_open…`, `test_gate_never_raises`, `test_gate_enforced_in_real_freqtrade` |
| F2 | Freqtrade | Daily-loss halt was only an API `/stopentry` call. A container restart (`initial_state: running`) silently **resumed entries**. | High | Persistent `HALT_<engine>.json` in the read-only shared volume; daily halt expires at 00:00 UTC, drawdown halt only by owner release. | `test_daily_loss_halt_persists_then_expires_at_midnight`, gate tests |
| F3 | Freqtrade | If the core/sentinel container was down, **no portfolio-level limit was enforced at all**. | High | Sentinel writes `MONITOR_OK_<engine>` only after a fully successful check; the gate **refuses entries** if it is missing or > 5 min old (fail-closed). | `test_sentinel_heartbeat_only_when_monitoring_succeeds`, real-Freqtrade gate test |
| F4 | Freqtrade | The FreqAI shadow bot (own $1,000 wallet) was **not monitored** by the sentinel. | Medium | Supervisor monitors every instance in `FREQTRADE_INSTANCES`; `freqai_shadow` has its own book, halts and heartbeat (excluded from the $3,000 competition totals). | sentinel tests with `engine="freqai_shadow"` |
| F5 | Freqtrade | Sentinel read only the first 500 trades. | Low | Pagination. | `test_sentinel_paginates_trade_history` |
| F6 | Freqtrade | After a restart/outage, an old entry signal could still be acted on. | Low | `ignore_buying_expired_candle_after = 600`. Upstream also refuses analysis when the last candle is > 2 timeframes + 5 min old (`interface.py`). | real-Freqtrade stake/attribute test |
| F7 | All | A drawdown on one engine engaged the **global** kill switch, stopping unrelated engines. | Medium | Automatic triggers are per-engine halts; the global switch is for the owner. Owner release clears halts and writes `PEAK_RESET` (new high-water mark, else the halt re-triggers). | `test_drawdown_halt_is_per_engine_and_permanent`, dashboard flow test |
| F8 | LumiBot | Partial fills were not booked (`on_partially_filled_order` unhandled) and a failed ledger write only logged - the $1,000 book could **diverge** from the broker. | High | Fills booked as the delta between the order's cumulative broker transactions and what the ledger holds (works for chunked or cumulative callbacks). Every iteration reconciles ledger vs broker positions; mismatch → permanent `reconciliation` halt. | `test_partial_then_final_fill_booked_exactly_once`, `test_reconciliation_mismatch_halts_entries` (real LumiBot objects) |
| F9 | LEAN | Algorithm enforced position caps but **no daily-loss or drawdown limit**. | Medium | `risk_gate()` mirrors 1% / 5%: halts entries / liquidates and halts. | `test_lean_risk_gate` (engine run itself still untested) |
| F10 | All | Halt/limit events were re-logged every cycle (noise hides real incidents). | Low | Logged on transition only. | `test_halt_events_logged_once_not_every_cycle` |

## Verified to hold (no change needed)

- **Position size / per-trade risk (Freqtrade):** `custom_stake_amount` = min(0.5% equity / 5% stop, 10% equity, max_stake). Real-engine backtests: max stake 9.9-10.1% of starting equity.
- **No shorts / leverage:** `can_short=False`, `leverage()=1`, spot, `margin_mode=""`; config validator + sentinel kill on non-dry-run.
- **Force entries:** `force_entry_enable: false`; even when enabled, Freqtrade's force-entry RPC goes through `execute_entry(mode="initial")`, which calls `confirm_trade_entry` → our gate.
- **Duplicates:** Freqtrade opens at most one trade per pair (`position_adjustment_enable=False`); LumiBot/risk engine reject repeated `client_order_id`s; ledger fills are idempotent.
- **Bots cannot remove their own limits:** shared volume is mounted read-only into Freqtrade containers (verified in Docker: `rm` → "Read-only file system").

## Residual risks (accepted, documented)

1. **Sentinel cadence:** a daily-loss breach is detected within ~60 s. Entries only happen on new 1h candles, so the exposure window is small but non-zero.
2. **Gap risk:** stops are not exchange-side in dry-run and are checked per loop (Freqtrade) or per day (LumiBot); a gap can make a realised loss exceed the planned 0.5%.
3. **Account sharing:** LumiBot reconciliation assumes the Alpaca paper account is used only by BreadMaker; manual trades in the same symbols will (correctly) halt it.
4. **LEAN** limits are implemented in-algorithm and unit-tested, but no LEAN engine run has happened yet.
5. **Owner overrides:** with the dashboard password the owner can release halts. That is intentional.
