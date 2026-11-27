"""Elo, tournament, joint/parlay and econ/sports evaluators. SYNTHETIC data with known truth."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from breadmaker.forecasting import elo, joint
from breadmaker.forecasting.econ import evaluate as econ_evaluate
from breadmaker.forecasting.sports import build_frame, tournament, walk_forward_models
from breadmaker.forecasting.validation import evaluate_joint_pair, render
from breadmaker.research.tournament import block_bootstrap_ci, compare


# ---- Elo ---------------------------------------------------------------------------------------
def test_win_prob_and_symmetry():
    assert elo.win_prob(1500, 1500, 0) == pytest.approx(0.5)
    assert elo.win_prob(1500, 1500, 48) > 0.5
    assert elo.win_prob(1600, 1500, 0) == pytest.approx(1 / (1 + 10 ** (-0.25)))


def test_elo_probability_is_pre_game_no_leakage():
    g = [elo.Game(2020, f"2020-01-{d:02d}", "A", "B", 30, 0) for d in range(1, 6)]
    out = list(elo.run(g, elo.NFL))
    assert out[0][1] == pytest.approx(elo.win_prob(1505, 1505, 48))   # first game: no info yet
    assert out[1][1] > out[0][1]                                         # learns only from earlier games
    changed = [elo.Game(2020, g[-1].date, "A", "B", 0, 30)]              # flip last result
    out2 = list(elo.run(g[:-1] + changed, elo.NFL))
    assert out2[-1][1] == pytest.approx(out[-1][1])                      # its own result can't affect it


def test_season_reversion():
    g = [elo.Game(2020, "2020-01-01", "A", "B", 40, 0), elo.Game(2021, "2021-01-01", "A", "B", None, None)]
    (_, _, rh0, _), (_, _, rh1, _) = list(elo.run(g, elo.NFL))
    after = list(elo.run(g[:1], elo.NFL))
    assert rh1 < 1505 + 20 * 2 and rh1 > 1505


def test_moneyline_vig_removed():
    p = elo.implied_prob_from_moneylines(-150, 130)
    assert 0.55 < p < 0.6
    assert elo.implied_prob_from_moneylines(None, 100) is None


# ---- tournament -------------------------------------------------------------------------------
def test_tournament_detects_real_improvement_and_rejects_noise():
    rng = np.random.default_rng(0)
    n = 4000
    truth = rng.uniform(0.2, 0.8, n)
    y = (rng.uniform(size=n) < truth).astype(float)
    blocks = np.repeat(np.arange(n // 20), 20)
    res = compare({"base": np.full(n, y.mean()), "good": truth,
                   "noise": np.clip(np.full(n, y.mean()) + rng.normal(0, 0.05, n), 0, 1)}, y, blocks, "base")
    assert res["models"]["good"]["verdict"] == "better"
    assert res["models"]["noise"]["verdict"] in ("worse", "no significant difference")


def test_block_bootstrap_wider_than_iid_for_correlated_blocks():
    rng = np.random.default_rng(1)
    shocks = np.repeat(rng.normal(0, 1, 50), 40)            # strong within-block correlation
    _, lo_b, hi_b = block_bootstrap_ci(shocks, np.repeat(np.arange(50), 40))
    _, lo_i, hi_i = block_bootstrap_ci(shocks, np.arange(2000))
    assert (hi_b - lo_b) > 3 * (hi_i - lo_i)


# ---- joint / parlays ----------------------------------------------------------------------------
def test_gaussian_copula_limits():
    p = [0.5, 0.5]
    assert joint.gaussian_copula(p, np.eye(2)) == pytest.approx(0.25, abs=1e-3)
    assert joint.gaussian_copula(p, np.array([[1, 0.999], [0.999, 1]])) == pytest.approx(0.5, abs=0.02)
    assert joint.gaussian_copula(p, np.array([[1, 0.5], [0.5, 1]])) == pytest.approx(1 / 3, abs=1e-3)  # 1/4 + asin(.5)/2pi


def test_latent_correlation_recovered():
    rng = np.random.default_rng(2)
    z = rng.multivariate_normal([0, 0], [[1, 0.7], [0.7, 1]], 20000)
    R = joint.latent_correlation_from_residuals(z)
    assert R[0, 1] == pytest.approx(0.7, abs=0.03)


def test_joint_interval_brackets_mid():
    mid, lo, hi = joint.interval([0.6, 0.5], [0.55, 0.45], [0.65, 0.55], np.array([[1, 0.4], [0.4, 1]]))
    assert lo <= mid <= hi


def test_correlated_cities_beat_independence():
    """Synthetic 'neighbouring cities' with shared weather shocks: copula/empirical must win."""
    rng = np.random.default_rng(3)
    d0 = date(2024, 1, 1)
    fa, fb, oa, ob = {}, {}, {}, {}
    for i in range(700):
        d = d0 + timedelta(days=i)
        shock = rng.normal(0, 3)
        fa[d], fb[d] = 60.0, 62.0
        oa[d] = float(round(60 + shock + rng.normal(0, 1)))
        ob[d] = float(round(62 + shock + rng.normal(0, 1)))
    res = evaluate_joint_pair(fa, oa, fb, ob)
    assert res["models"]["gaussian_copula"]["verdict"] == "better"
    assert res["models"]["empirical_joint"]["verdict"] == "better"
    assert res["mean_p"]["independent"] < res["base_rate"] < res["mean_p"]["empirical_joint"] + 0.05


# ---- sports pipeline -----------------------------------------------------------------------------
def test_sports_walk_forward_uses_prior_seasons_only():
    rng = np.random.default_rng(4)
    teams = [f"T{i}" for i in range(8)]
    strength = {t: rng.normal(0, 7) for t in teams}
    games = []
    for season in range(2010, 2018):
        for wk in range(15):
            order = rng.permutation(teams)
            for h, a in zip(order[::2], order[1::2]):
                m = strength[h] - strength[a] + 2 + rng.normal(0, 10)
                games.append(elo.Game(season, f"{season}-{(wk // 4) + 9:02d}-{(wk % 4) * 7 + 1:02d}", h, a,
                                      max(m, 0) + 10, max(-m, 0) + 10))
    df = walk_forward_models(build_frame(games, elo.NFL))
    assert df.loc[df["season"] < 2013, "p_logit"].isna().all()       # no model without 3 prior seasons
    res = tournament(df)
    assert res["models"]["p_elo"]["verdict"] == "better"              # real signal exists in synthetic data
    assert set(res["metrics"]) >= {"p_home_rate", "p_elo", "p_logit"}


# ---- economics --------------------------------------------------------------------------------------
def test_econ_evaluate_runs_and_ar_beats_naive_on_ar_process():
    rng = np.random.default_rng(5)
    n = 360
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.2 + 0.3 * (x[t - 1] - 0.2) + rng.normal(0, 0.2)
    s = pd.Series(np.round(x, 1), index=pd.date_range("1995-01-01", periods=n, freq="MS"))
    res = econ_evaluate(s, [0.0, 0.2, 0.4])
    assert res["models"]["autoreg"]["verdict"] == "better"
    assert res["point_mae"]["autoreg"] < res["point_mae"]["naive"]


def test_render_includes_new_sections():
    md = render({"generated_at": "x", "weather": {}, "crypto": {}, "errors": {},
                 "sports": {"NFL": {"n": 10, "seasons": [2010, 2020], "source": "s", "baseline": "p_home_rate",
                                    "models": {"p_home_rate": {"brier": 0.25},
                                               "p_elo": {"brier": 0.22, "diff_vs_baseline": -0.03,
                                                         "ci95": [-0.04, -0.02], "verdict": "better"}}}}})
    assert "NFL" in md and "better" in md and "Retrosheet" in md
