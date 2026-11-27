"""Elo ratings following FiveThirtyEight's published methodology (established, open).

References (methodology articles, CC BY 4.0 data): "How Our NFL Predictions Work", "How Our NBA
Predictions Work", "How Our MLB Predictions Work" (FiveThirtyEight). Parameters below are the
published base-model values; no parameter is tuned on our evaluation data.

Leakage guarantee: each game's probability is computed from ratings BEFORE that game; ratings are
updated only afterwards (`run` yields pre-game probabilities in chronological order).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EloParams:
    k: float
    home_adv: float
    revert: float          # fraction regressed toward the mean between seasons
    mov: str = "none"      # 'nfl' | 'nba' | 'none'
    mean: float = 1505.0


NFL = EloParams(k=20, home_adv=48, revert=1 / 3, mov="nfl")
NBA = EloParams(k=20, home_adv=100, revert=0.25, mov="nba")
MLB = EloParams(k=4, home_adv=24, revert=1 / 3, mov="none")


def win_prob(elo_home: float, elo_away: float, home_adv: float, neutral: bool = False) -> float:
    diff = elo_home - elo_away + (0 if neutral else home_adv)
    return 1 / (1 + 10 ** (-diff / 400))


def _mov_mult(p: EloParams, margin: float, winner_elo_diff: float) -> float:
    m = abs(margin)
    if p.mov == "nfl":
        return math.log(m + 1) * 2.2 / (winner_elo_diff * 0.001 + 2.2)
    if p.mov == "nba":
        return ((m + 3) ** 0.8) / (7.5 + 0.006 * winner_elo_diff)
    return 1.0


@dataclass
class Game:
    season: int
    date: str
    home: str
    away: str
    home_score: float | None
    away_score: float | None
    neutral: bool = False


def run(games: Iterable[Game], params: EloParams):
    """Yield (game, p_home_pre_game, elo_home_pre, elo_away_pre) chronologically; update after."""
    ratings: dict[str, float] = {}
    season = None
    for g in sorted(games, key=lambda g: (g.date, g.home)):
        if season is not None and g.season != season:
            for t in ratings:
                ratings[t] = ratings[t] * (1 - params.revert) + params.mean * params.revert
        season = g.season
        rh, ra = ratings.get(g.home, params.mean), ratings.get(g.away, params.mean)
        p = win_prob(rh, ra, params.home_adv, g.neutral)
        yield g, p, rh, ra
        if g.home_score is None or g.away_score is None:
            continue  # future/unplayed game: no update
        margin = g.home_score - g.away_score
        actual = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
        adj = 0 if g.neutral else params.home_adv
        winner_diff = (rh + adj - ra) if margin > 0 else (ra - rh - adj) if margin < 0 else 0.0
        shift = params.k * _mov_mult(params, margin, winner_diff) * (actual - p)
        ratings[g.home] = rh + shift
        ratings[g.away] = ra - shift


def implied_prob_from_moneylines(home_ml: float, away_ml: float) -> float | None:
    """Vig-free home win probability from American moneylines (market benchmark, not our model)."""
    def raw(ml):
        if ml is None or ml != ml or ml == 0:
            return None
        return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)
    h, a = raw(home_ml), raw(away_ml)
    if h is None or a is None or h + a <= 0:
        return None
    return h / (h + a)
