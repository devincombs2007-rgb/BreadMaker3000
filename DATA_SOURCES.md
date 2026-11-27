# Data sources by category (researched 2026-11-27)

Rule: a category is modelled only if a **free, reliable, terms-compatible** source exists. Otherwise its
markets are rejected with a recorded reason, which is the honest default. No paid feeds, no API keys
that need spending, no scraping of sites whose terms forbid it.

| Category | Source | Terms / licence | Status |
|---|---|---|---|
| Weather (daily highs) | Open-Meteo forecasts and previous runs; NWS Daily Climate Reports via Iowa Environmental Mesonet | Open-Meteo CC BY 4.0, free tier non-commercial, < 10k calls/day; NWS is US-government data | **Validated** (7 stations) and **live forward log** |
| Crypto price thresholds | Public exchange OHLCV via ccxt | Public market data | **Validated** |
| NFL | nflverse `games.csv` (scores, closing moneylines) | nflreadr package MIT; **data-file licence not confirmed** → only derived metrics are published, never raw data | Validated in the tournament; not routed to Kalshi |
| MLB | Retrosheet game logs | Retrosheet notice permits use; attribution required (printed in every report) | Validated in the tournament; not routed |
| NBA | FiveThirtyEight `nba_elo.csv` | CC BY 4.0; FiveThirtyEight shut down in 2025, so the file may no longer be published | Attempted; reported as unavailable if the download fails |
| NCAA | none found | No free source with a confirmed licence (CollegeFootballData needs a registered key; KenPom is paid) | **Rejected** |
| Economics: CPI MoM, unemployment | BLS Public Data API v1 | No registration; US-government data. The API returns **revised** values while Kalshi settles on the **first release**. | Validated in the tournament; not routed |
| Economics: Fed decisions, GDP nowcasts | Fed funds futures (CME) and nowcasts | Paid or unclear terms | **Rejected** |
| Politics | Poll aggregates | FiveThirtyEight closed; RealClearPolitics terms restrict reuse; Wikipedia poll tables are CC BY-SA but unreliable as a live feed | **Rejected** |
| Entertainment (box office, charts, awards) | Box Office Mojo, Billboard, Rotten Tomatoes | Commercial terms forbid automated use | **Rejected** |
| Technology (launch dates, app ranks) | none | No reliable free structured source | **Rejected** |

## Why validated models are not yet trading Kalshi markets

1. A model must **beat simple baselines significantly** in the walk-forward tournament
   (`breadmaker/research/tournament.py`, block-bootstrap 95% interval entirely below zero).
2. Its mapping to Kalshi markets (series tickers, settlement source and timing, rounding) must be
   checked against live Kalshi data. That is blocked while production access is off.
   - Demo data could help once the owner enables the Demo gate.
3. Beating baselines is not enough to trade. The model must also beat **the market**. For the NFL the
   tournament includes the vig-free closing moneyline as that benchmark.

## Attribution required in published outputs

- Weather data by Open-Meteo.com (CC BY 4.0).
- "The information used here was obtained free of charge from and is copyrighted by Retrosheet.
  Interested parties may contact Retrosheet at www.retrosheet.org."
- FiveThirtyEight data (CC BY 4.0), where used.
