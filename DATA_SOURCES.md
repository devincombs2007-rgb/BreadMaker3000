# Data sources and licences (reviewed 2026-11-27, PR #3)

**Rule:** a dataset is used only if its licence or terms are **verified**. This is enforced in code by
`breadmaker/forecasting/licences.py`:

- Every fetch goes through the registry, and an unregistered or unverified source is refused
  **before any request is made**. Tests check that every URL in the forecasting code is registered.
- The owner can unblock an unverified source only by reading its terms personally and adding its key
  to the repository variable `BREADMAKER_DATA_TERMS_ACCEPTED`. Reports then name it as
  owner-accepted.

No paid feeds, no keys that cost money, and no scraping of sites whose terms forbid it.

| Source | Used for | Licence / terms | Status | Evidence |
|---|---|---|---|---|
| Open-Meteo APIs | Weather forecasts | CC BY 4.0; free for non-commercial use under 10k calls/day | **Verified** | open-meteo.com terms, read in v0.3 research |
| Iowa Environmental Mesonet (NWS CLI reports) | Weather truth | Public domain | **Verified** | IEM disclaimer: "in the public domain and may be used freely by anyone for any lawful purpose" |
| BLS Public Data API v1 | CPI, unemployment | Public domain (US government work) | **Verified** | BLS copyright information |
| Retrosheet game logs | MLB | Free use with the required notice | **Verified** | Retrosheet notice (printed in every report) |
| FiveThirtyEight `data` repo (`nbaallelo.csv`) and `nfl_elo.csv` | NBA (1947–2015); NFL (the file is currently not obtainable: non-CSV page) | CC BY 4.0 | **Verified** | github.com/fivethirtyeight/data `LICENSE` = CC BY 4.0, fetched 2026-11-27. The NBA game information comes from Basketball-Reference.com. |
| nflverse/nfldata `games.csv` | (was NFL) | **No licence file** | **Blocked; no longer used** | raw `LICENSE` returns 404. NFL results based on it are withdrawn. |
| Coinbase / Binance.US / Kraken public market data (via ccxt) | Crypto model | Exchange market-data terms | **Unverified → blocked** | Terms pages unreachable from the build environment. Coinbase "reserves all rights in the Market Data not expressly granted". |
| Philadelphia Fed Real-Time Data Set | (candidate: first-release econ) | Unknown | **Unverified; not used** | Site unreachable from the build environment |

Kalshi data is governed separately by the Kalshi Developer Agreement; see KALSHI.md.

## NBA: alternatives investigated (review finding 7)

| Candidate | Outcome |
|---|---|
| FiveThirtyEight live `nba_elo.csv` | Gone: serves a non-CSV page, and the GitHub mirror returns 404 |
| FiveThirtyEight historical `nba-elo/nbaallelo.csv` | **Adopted.** CC BY 4.0, 1947–2015, includes FiveThirtyEight's own pre-game probabilities as a benchmark. Historical only. |
| Basketball-Reference | Terms restrict automated collection; not used directly |
| stats.nba.com (`nba_api`) | NBA.com terms restrict use; not used |
| Kaggle / Hugging Face NBA datasets | Mixed or unclear licences, and Kaggle needs an account; none verified |

There is no free, licence-verified source for **current-season** NBA results. NBA markets therefore
can't be modelled live; they would need such a source first.

## First-release economic data: alternatives investigated (review finding 8)

Kalshi's CPI and jobs markets settle on the **first** BLS release, but the BLS API returns **revised**
values.

| Candidate | Outcome |
|---|---|
| ALFRED (St. Louis Fed) vintages | Free, but needs a registered API key, which would be a credential and an owner decision. Not used. |
| Philadelphia Fed Real-Time Data Set | Free files, but the terms could not be verified from here. Not used. |
| BLS archived news releases | Public domain, but bls.gov was unreachable from the build environment, so a parser can't be built and tested reliably here. Not used yet. |
| **Record first releases ourselves** | **Adopted.** The daily run stores every distinct value the BLS API serves for each month, append-only. The first row per month is the first release. Months published before tracking started are marked `backfilled`. |

## NCAA, Fed decisions, politics, entertainment, technology

These are still **rejected**: no free, reliable, licence-verified source was found. NCAA needs keys or
paid sources; Fed funds futures are paid; poll aggregators restrict reuse; box office and chart sites
forbid automated use; and there is no structured technology source.

## Why validated models are not yet trading Kalshi markets

1. A model must **beat simple baselines significantly** in the walk-forward tournament
   (block-bootstrap 95% interval entirely below zero).
2. Its mapping to Kalshi markets must be checked against live Kalshi data. That is blocked while
   access is off.
3. It must also beat **the market**. No licensed market benchmark exists for sports, and our NBA Elo
   doesn't even beat FiveThirtyEight's.

## Attribution required in published outputs

Printed automatically from the registry in every report:

- Weather data by Open-Meteo.com (CC BY 4.0).
- NWS climate reports via the Iowa Environmental Mesonet, Iowa State University.
- U.S. Bureau of Labor Statistics.
- "The information used here was obtained free of charge from and is copyrighted by Retrosheet.
  Interested parties may contact Retrosheet at www.retrosheet.org."
- Data: FiveThirtyEight (CC BY 4.0).
