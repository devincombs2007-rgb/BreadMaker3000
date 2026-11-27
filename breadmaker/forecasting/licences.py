"""Data-licence registry and gate: no dataset is used unless its terms have been verified.

Every external data source the forecasting code touches is listed here with its licence and the
evidence for it. Loaders call `require(key)` before fetching. A source whose terms could NOT be
verified is blocked; the owner may unblock one after reading its terms personally by listing its key
in the environment variable BREADMAKER_DATA_TERMS_ACCEPTED (comma-separated). That is the owner's
attestation, recorded in reports; the code never assumes it.

Status values:
  verified    - licence/terms text read; use permitted for this purpose (with any attribution)
  unverified  - terms not confirmed (page unreachable from the build sandbox, or no licence exists)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENV = "BREADMAKER_DATA_TERMS_ACCEPTED"


@dataclass(frozen=True)
class Dataset:
    key: str
    name: str
    hosts: tuple[str, ...]          # URL prefixes (or ccxt venue ids) covered by this entry
    licence: str
    status: str
    evidence: str
    attribution: str = ""


REGISTRY: dict[str, Dataset] = {d.key: d for d in [
    Dataset("open-meteo", "Open-Meteo forecast / previous-runs / historical-forecast APIs",
            ("https://api.open-meteo.com/", "https://previous-runs-api.open-meteo.com/",
             "https://historical-forecast-api.open-meteo.com/"),
            "CC BY 4.0; free API for non-commercial use, < 10,000 calls/day", "verified",
            "open-meteo.com terms, read during v0.3 research (2026-11-26)", "Weather data by Open-Meteo.com (CC BY 4.0)"),
    Dataset("iem", "Iowa Environmental Mesonet: NWS Daily Climate (CLI) reports", ("https://mesonet.agron.iastate.edu/",),
            "Public domain", "verified",
            "mesonet.agron.iastate.edu/disclaimer.php: 'The materials found on this website are in the public domain "
            "and may be used freely by anyone for any lawful purpose.' (checked 2026-11-27)",
            "NWS climate reports via the Iowa Environmental Mesonet, Iowa State University"),
    Dataset("bls", "U.S. Bureau of Labor Statistics Public Data API v1", ("https://api.bls.gov/",),
            "Public domain (U.S. government work)", "verified",
            "BLS copyright information page, read during v0.4 research", "U.S. Bureau of Labor Statistics"),
    Dataset("retrosheet", "Retrosheet game logs", ("https://www.retrosheet.org/",),
            "Free use with the required notice", "verified", "retrosheet.org notice, read during v0.4 research",
            "The information used here was obtained free of charge from and is copyrighted by Retrosheet. "
            "Interested parties may contact Retrosheet at www.retrosheet.org."),
    Dataset("fivethirtyeight", "FiveThirtyEight data (NBA historical Elo; NFL Elo)",
            ("https://raw.githubusercontent.com/fivethirtyeight/data/", "https://projects.fivethirtyeight.com/"),
            "CC BY 4.0", "verified",
            "github.com/fivethirtyeight/data LICENSE = Creative Commons Attribution 4.0 International (fetched "
            "2026-11-27). The NBA file notes its game information comes from Basketball-Reference.com.",
            "Data: FiveThirtyEight (CC BY 4.0)"),
    Dataset("nflverse-nfldata", "nflverse/nfldata games.csv", ("https://raw.githubusercontent.com/nflverse/nfldata/",
                                                              "https://github.com/nflverse/nfldata/"),
            "NONE FOUND", "unverified",
            "repository has no LICENSE file (raw LICENSE returns 404, checked 2026-11-27); no licence means no "
            "permission. No longer used."),
    Dataset("coinbase", "Coinbase Exchange public market data (via ccxt)", ("coinbaseexchange",),
            "Coinbase Market Data Terms of Use", "unverified",
            "terms page not reachable from the build sandbox; search results show Coinbase 'reserves all rights in the "
            "Market Data not expressly granted' and licenses redistribution separately"),
    Dataset("binanceus", "Binance.US public market data (via ccxt)", ("binanceus",),
            "Binance.US Terms of Use", "unverified", "terms page not reachable from the build sandbox"),
    Dataset("kraken", "Kraken public market data (via ccxt)", ("kraken",),
            "Kraken Terms of Service", "unverified", "no API market-data terms located; not reachable from the sandbox"),
    Dataset("philly-fed-rtdsm", "Philadelphia Fed Real-Time Data Set for Macroeconomists (first-release vintages)",
            ("https://www.philadelphiafed.org/",), "unknown", "unverified",
            "site not reachable from the build sandbox; not used"),
]}


class LicenceUnverified(RuntimeError):
    """Refusing to use a dataset whose terms have not been verified."""


def accepted_by_owner(env: dict | None = None) -> set[str]:
    raw = (os.environ if env is None else env).get(ENV, "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def allowed(key: str, env: dict | None = None) -> bool:
    d = REGISTRY.get(key)
    return d is not None and (d.status == "verified" or key in accepted_by_owner(env))


def require(key: str, env: dict | None = None) -> Dataset:
    d = REGISTRY.get(key)
    if d is None:
        raise LicenceUnverified(f"dataset '{key}' is not in the licence registry")
    if not allowed(key, env):
        raise LicenceUnverified(f"{d.name}: licence/terms not verified ({d.evidence}). Not used. The owner may read "
                                f"the terms and, if acceptable, add '{key}' to {ENV}.")
    return d


def key_for(url_or_venue: str) -> str | None:
    for d in REGISTRY.values():
        if any(url_or_venue == h or url_or_venue.startswith(h) for h in d.hosts):
            return d.key
    return None


def require_url(url_or_venue: str, env: dict | None = None) -> Dataset:
    k = key_for(url_or_venue)
    if k is None:
        raise LicenceUnverified(f"{url_or_venue} is not covered by the licence registry")
    return require(k, env)
