# Architecture

```
                GitHub Actions (free)                          optional private server
  ┌────────────────────────────────────────────┐        ┌──────────────────────────────────┐
  │ forecast-validation.yml  (public data only)│        │ docker compose: core + dashboard │
  │   Open-Meteo, NWS CLI (IEM), crypto OHLCV  │        │   supervisor -> kalshi cycle     │
  │   -> walk-forward model validation         │        │   dashboard (127.0.0.1, Tailscale)│
  │ kalshi-paper.yml  (guard: authorized AND   │        └──────────────────────────────────┘
  │   private repo) -> breadmaker kalshi-run   │
  └────────────────────────────────────────────┘

breadmaker kalshi-run
  KalshiPublicClient (GET only, gate, 5 req/s token bucket, bounded backoff, cursor pages)
    -> markets.py   parse markets/strikes; order book -> executable asks (1 - opposite bid); fees
    -> pipeline     route: weather | crypto | none(reject with reason)
    -> feeds.py     timestamped, hashed evidence  -> evidence table
    -> forecasting  Forecast{p, p_low, p_high} | Rejection(reason)
    -> decision.py  conservative edge after fees >= min_edge ? fractional Kelly within caps : zero
    -> paper.py     $25 book on the shared ledger (fills, settlement $1/$0)
    -> tracking.py  predictions table, Brier/log-loss/ECE, walk-forward recalibration
  structural.py    per-event ladder / bracket combinations with locked payoffs
  risk.py          global kill switch + per-engine daily-loss / drawdown halts (unchanged)
```

## Principles

1. **Read-only by construction.** No order, portfolio or authentication code exists; CI's safety scan
   fails on any Kalshi order path or request-signing header.
2. **Default deny.** Kalshi access needs config + attestation + (in Actions) a private repo.
3. **No fabrication.** Missing data -> `Rejection(reason)`, recorded and shown on the dashboard. Synthetic
   test data is labelled SYNTHETIC wherever it appears.
4. **Conservative economics.** Decisions use the lower probability bound, executable asks (not mid),
   fees rounded up, depth limits, and zero allocation by default.
5. **No leakage.** Evidence is timestamped; residual pools and recalibration only use information that
   existed before each prediction (tested).
6. **Reuse.** The ledger, risk engine (kill switch, halts), dashboard (auth, CSRF, setup), secrets store,
   heartbeats and reports come from the previous platform unchanged, apart from the zero-price settlement.

## Licences

| Component | Licence / terms |
|---|---|
| Kalshi API | Kalshi Developer Agreement v1.1 - see KALSHI.md (access OFF) |
| Open-Meteo data | CC BY 4.0, free tier non-commercial, < 10k calls/day - attribution in README |
| NWS climate reports (via IEM) | US government public data |
| Python deps | see requirements/*.lock (BSD/MIT/Apache) |
| Archived engines | Freqtrade/LumiBot GPL-3.0, LEAN Apache-2.0 - only on the archive branch |
