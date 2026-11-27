"""No dataset without a verified licence (PR #3 review finding 6). SYNTHETIC data only; no network."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from breadmaker.forecasting import feeds, licences, sports
from breadmaker.forecasting.licences import LicenceUnverified

ROOT = Path(__file__).resolve().parents[1] / "breadmaker"


def test_every_url_in_forecasting_code_is_registered():
    urls = set()
    for p in list((ROOT / "forecasting").glob("*.py")) + [ROOT / "forward.py"]:
        urls |= set(re.findall(r"https?://[A-Za-z0-9./_{}-]+", p.read_text()))
    urls = {u for u in urls if "fivethirtyeight.com/interactives" not in u}
    missing = sorted(u for u in urls if licences.key_for(u) is None)
    assert not missing, f"URLs without a licence-registry entry: {missing}"


def test_nflverse_is_blocked_and_no_longer_referenced_by_loaders():
    with pytest.raises(LicenceUnverified, match="not verified"):
        licences.require_url("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", env={})
    assert not any("nflverse" in u for u in sports.NFL_538 + sports.NBA_538 + [sports.NBA_538_HISTORICAL])


def test_unregistered_url_refused_before_any_request(monkeypatch):
    called = []
    monkeypatch.setattr(feeds.httpx, "get", lambda *a, **k: called.append(1))
    with pytest.raises(LicenceUnverified):
        feeds._get("https://some-unvetted-site.example/data.csv", {})
    assert not called


def test_crypto_venues_blocked_until_owner_accepts_terms(monkeypatch):
    monkeypatch.delenv(licences.ENV, raising=False)
    with pytest.raises(LicenceUnverified, match="no crypto venue with verified data terms"):
        feeds.crypto_hourly_closes("BTC", pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime())
    assert licences.allowed("coinbase", env={licences.ENV: "coinbase"})       # explicit owner attestation
    assert not licences.allowed("coinbase", env={licences.ENV: "coinbase-ish"})


def test_verified_sources_allowed():
    for k in ("open-meteo", "iem", "bls", "retrosheet", "fivethirtyeight"):
        assert licences.allowed(k, env={})


def test_nba_historical_conversion_one_row_per_game_home_perspective():
    raw = pd.DataFrame({
        "_iscopy": [0, 1, 0, 1], "lg_id": ["NBA"] * 4, "year_id": [2015] * 4,
        "date_game": ["1/2/2015", "1/2/2015", "1/3/2015", "1/3/2015"],
        "team_id": ["GSW", "CLE", "BOS", "NYK"], "opp_id": ["CLE", "GSW", "NYK", "BOS"],
        "pts": [100, 90, 80, 85], "opp_pts": [90, 100, 85, 80],
        "game_location": ["A", "H", "H", "A"], "forecast": [0.40, 0.60, 0.70, 0.30]})
    g = sports.nba_historical_to_games(raw)
    assert len(g) == 2
    r0 = g.iloc[0]   # GSW played AWAY at CLE -> CLE is team1 (home)
    assert (r0.team1, r0.team2, r0.score1, r0.score2) == ("CLE", "GSW", 90.0, 100.0)
    assert np.isclose(r0.elo_prob1, 0.60)
    r1 = g.iloc[1]
    assert (r1.team1, r1.score1, r1.date) == ("BOS", 80.0, "2015-01-03") and np.isclose(r1.elo_prob1, 0.70)
