"""Sports game-winner forecasting + walk-forward validation (NFL, MLB, NBA).

Data - only sources whose licence is verified (breadmaker/forecasting/licences.py):
* NFL - FiveThirtyEight `nfl_elo.csv` (CC BY 4.0). nflverse `games.csv` was used in v0.4's first
  runs but has NO licence file, so it is no longer used (PR #3 review). Without it there is no
  betting-line benchmark; FiveThirtyEight's own published pre-game probability is the benchmark.
* MLB - Retrosheet game logs. Required notice: "The information used here was obtained free of
  charge from and is copyrighted by Retrosheet. Interested parties may contact Retrosheet at
  www.retrosheet.org."
* NBA - FiveThirtyEight (CC BY 4.0): the live `nba_elo.csv` if still published, otherwise the
  historical `nbaallelo.csv` (1947-2015, game information from Basketball-Reference.com).
NCAA - no free source with a confirmed licence was found: not modelled (markets rejected).

Models: home-rate baseline (past-only), FiveThirtyEight-style Elo, logistic regression and
LightGBM on [Elo diff, recent form] retrained each season on PRIOR seasons only.
"""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd

from . import elo
from .feeds import _get, _prov

NFL_538 = ["https://projects.fivethirtyeight.com/nfl-api/nfl_elo.csv"]
RETROSHEET = "https://www.retrosheet.org/gamelogs/gl{year}.zip"
NBA_538 = ["https://projects.fivethirtyeight.com/nba-model/nba_elo.csv"]
NBA_538_HISTORICAL = "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv"
_538_COLS = {"date", "season", "team1", "team2", "score1", "score2"}


# ---- loaders --------------------------------------------------------------------------------
def _load_538(urls: list[str], label: str) -> tuple[pd.DataFrame, dict]:
    errors = {}
    for url in urls:
        try:
            r = _get(url, {}, retries=1)
            df = pd.read_csv(io.StringIO(r.text))
            if _538_COLS.issubset(df.columns):
                return df, _prov(f"FiveThirtyEight {label} (CC BY 4.0)", url, {}, r.content)
            errors[url] = "unexpected columns (page is not the CSV)"
        except Exception as exc:  # noqa: BLE001
            errors[url] = f"{type(exc).__name__}: {str(exc)[:150]}"
    raise RuntimeError(f"{label} unavailable: {errors}")


def load_nfl() -> tuple[pd.DataFrame, dict]:
    return _load_538(NFL_538, "nfl_elo.csv")


def nba_historical_to_games(raw: pd.DataFrame) -> pd.DataFrame:
    """nbaallelo.csv (two rows per game, one per team) -> one row per game in nba_elo.csv layout."""
    g = raw[(raw["_iscopy"] == 0) & (raw["lg_id"] == "NBA")].copy()
    away = g["game_location"] == "A"
    out = pd.DataFrame({
        "date": pd.to_datetime(g["date_game"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
        "season": g["year_id"].astype(int),
        "team1": np.where(away, g["opp_id"], g["team_id"]),
        "team2": np.where(away, g["team_id"], g["opp_id"]),
        "score1": np.where(away, g["opp_pts"], g["pts"]).astype(float),
        "score2": np.where(away, g["pts"], g["opp_pts"]).astype(float),
        # FiveThirtyEight's own pre-game probability for team_id -> for team1 (home)
        "elo_prob1": np.where(away, 1 - g["forecast"], g["forecast"]).astype(float),
        "neutral": (g["game_location"] == "N").astype(int),
    })
    return out.sort_values(["date", "team1"]).reset_index(drop=True)


def load_nba() -> tuple[pd.DataFrame, dict]:
    try:
        return _load_538(NBA_538, "nba_elo.csv")
    except RuntimeError as live_err:
        try:
            r = _get(NBA_538_HISTORICAL, {}, retries=1)
            raw = pd.read_csv(io.StringIO(r.text))
            return nba_historical_to_games(raw), _prov(
                "FiveThirtyEight nbaallelo.csv 1947-2015 (CC BY 4.0; game info from Basketball-Reference.com)",
                NBA_538_HISTORICAL, {}, r.content)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"NBA data unavailable: live: {live_err}; historical: {type(exc).__name__}: {exc}") from exc


def load_mlb(years: range) -> tuple[pd.DataFrame, list]:
    import time
    frames, provs = [], []
    for y in years:
        r = _get(RETROSHEET.format(year=y), {})
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 3, 6, 9, 10])
        raw.columns = ["date", "away", "home", "away_score", "home_score"]
        raw["season"] = y
        frames.append(raw)
        provs.append(_prov("Retrosheet game logs", RETROSHEET.format(year=y), {}, r.content))
        time.sleep(1)
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return df, provs


# ---- feature frame (strictly pre-game) ----------------------------------------------------------
def build_frame(games: list[elo.Game], params: elo.EloParams, form_window: int = 5) -> pd.DataFrame:
    rows, form = [], {}
    for g, p, rh, ra in elo.run(games, params):
        fh = np.mean(form.get(g.home, [0.0])[-form_window:])
        fa = np.mean(form.get(g.away, [0.0])[-form_window:])
        rows.append({"season": g.season, "date": g.date, "home": g.home, "away": g.away, "p_elo": p,
                     "elo_diff": rh - ra, "form_diff": fh - fa,
                     "y": np.nan if g.home_score is None or g.away_score is None or g.home_score == g.away_score
                     else float(g.home_score > g.away_score)})
        if g.home_score is not None and g.away_score is not None:  # update AFTER the pre-game row
            m = g.home_score - g.away_score
            form.setdefault(g.home, []).append(m)
            form.setdefault(g.away, []).append(-m)
    return pd.DataFrame(rows)


def walk_forward_models(df: pd.DataFrame, min_train_seasons: int = 3) -> pd.DataFrame:
    """Add p_home_rate, p_logit, p_lgbm columns; each season predicted from prior seasons only."""
    from sklearn.linear_model import LogisticRegression
    df = df.copy()
    for c in ("p_home_rate", "p_logit", "p_lgbm"):
        df[c] = np.nan
    seasons = sorted(df["season"].unique())
    feats = ["elo_diff", "form_diff"]
    for i, s in enumerate(seasons):
        train = df[(df["season"] < s) & df["y"].notna()]
        mask = df["season"] == s
        if train["season"].nunique() < min_train_seasons:
            continue
        df.loc[mask, "p_home_rate"] = train["y"].mean()
        X, y = train[feats].to_numpy(), train["y"].to_numpy()
        lr = LogisticRegression(C=1.0, max_iter=1000).fit(X, y)
        df.loc[mask, "p_logit"] = lr.predict_proba(df.loc[mask, feats].to_numpy())[:, 1]
        try:
            import lightgbm as lgb
            gbm = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=50,
                                     verbose=-1, random_state=0).fit(X, y)
            df.loc[mask, "p_lgbm"] = gbm.predict_proba(df.loc[mask, feats].to_numpy())[:, 1]
        except ImportError:
            pass
    return df


def tournament(df: pd.DataFrame, extra: dict | None = None) -> dict:
    from ..kalshi.tracking import metrics
    from ..research.tournament import compare
    cols = ["p_home_rate", "p_elo", "p_logit", "p_lgbm"] + list((extra or {}).keys())
    available = [c for c in cols if c in df.columns and df[c].notna().any()]
    ev = df.dropna(subset=["y"] + available)   # same events for every model (paired comparison)
    preds = {c: ev[c].to_numpy() for c in available}
    blocks = (ev["season"].astype(str) + "-" + pd.to_datetime(ev["date"]).dt.isocalendar().week.astype(str)).to_numpy()
    res = compare(preds, ev["y"].to_numpy(), blocks, baseline="p_home_rate")
    res["metrics"] = {c: metrics(preds[c], ev["y"].to_numpy()) for c in preds}
    res["seasons"] = [int(ev["season"].min()), int(ev["season"].max())] if len(ev) else []
    # Second question: does anything beat plain Elo? (ML challengers must beat the simple established
    # model, not only the home-rate baseline; the market benchmark is compared to Elo the same way.)
    if "p_elo" in preds and len(preds) > 2:
        vs = {c: v for c, v in preds.items() if c != "p_home_rate"}
        res["vs_elo"] = compare(vs, ev["y"].to_numpy(), blocks, baseline="p_elo")
    return res


# ---- league runners ------------------------------------------------------------------------------
def _validate_538(raw: pd.DataFrame, prov: dict, params: elo.EloParams, first_season: int) -> dict:
    raw = raw[raw["season"] >= first_season]
    games = [elo.Game(int(r.season), str(r.date), r.team1, r.team2,
                      None if pd.isna(r.score1) else float(r.score1), None if pd.isna(r.score2) else float(r.score2),
                      neutral=bool(getattr(r, "neutral", 0) or 0)) for r in raw.itertuples()]
    df = walk_forward_models(build_frame(games, params))
    extra = None
    if "elo_prob1" in raw.columns:  # FiveThirtyEight's own published pre-game Elo probability (benchmark)
        key = raw.drop_duplicates(["date", "team1"]).set_index(["date", "team1"])["elo_prob1"]
        df["p_538"] = pd.to_numeric(pd.Series([key.get((d, h), np.nan) for d, h in zip(df["date"], df["home"])],
                                              index=df.index), errors="coerce")
        if df["p_538"].notna().any():
            extra = {"p_538": True}
    res = tournament(df, extra=extra)
    res["source"] = prov["source"]
    if extra:
        res["note"] = "p_538 = FiveThirtyEight's published pre-game probability: an external benchmark, not our model"
    return res


def validate_nfl() -> dict:
    raw, prov = load_nfl()
    return _validate_538(raw, prov, elo.NFL, 2000)


def validate_mlb(years=range(2010, 2026)) -> dict:
    raw, provs = load_mlb(years)
    games = [elo.Game(int(r.season), r.date, r.home, r.away, float(r.home_score), float(r.away_score))
             for r in raw.itertuples()]
    res = tournament(walk_forward_models(build_frame(games, elo.MLB, form_window=10)))
    res["source"] = f"Retrosheet game logs {years.start}-{years.stop - 1}"
    return res


def validate_nba() -> dict:
    raw, prov = load_nba()
    return _validate_538(raw, prov, elo.NBA, 1980)
