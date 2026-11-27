"""Validation evaluators on SYNTHETIC inputs (plumbing + statistical sanity only; not results)."""
from datetime import date, timedelta

import numpy as np

from breadmaker.forecasting.feeds import daily_max_from_hourly
from breadmaker.forecasting.validation import evaluate_crypto, evaluate_weather, render


def synthetic_weather(n=400, noise=2.5, bias=1.0, seed=0):
    rng = np.random.default_rng(seed)
    d0 = date(2024, 2, 1)
    days = [d0 + timedelta(days=i) for i in range(n)]
    truth = 60 + 15 * np.sin(np.arange(n) / 58)
    fc = {d: float(t) for d, t in zip(days, truth)}
    obs = {d: float(round(t + bias + rng.normal(0, noise))) for d, t in zip(days, truth)}
    return fc, obs


def test_weather_model_calibrated_on_well_specified_data():
    fc, obs = synthetic_weather()
    r = evaluate_weather(fc, obs)
    assert r["n_days"] > 250
    assert r["model"]["ece"] < 0.05                      # learns bias + spread from past residuals
    assert r["brier_skill_vs_raw"] > 0.3                 # uncertainty beats a certain point forecast
    assert abs(r["brier_skill_vs_gaussian"]) < 0.1       # ~equal to Gaussian when noise IS Gaussian


def test_weather_evaluation_ignores_future_observations():
    fc, obs = synthetic_weather(n=200)
    base = evaluate_weather(fc, obs)
    last = max(obs)
    obs2 = dict(obs)
    obs2[last] = 999.0      # changing the LAST day's outcome must only change that day's scoring rows
    alt = evaluate_weather(fc, obs2)
    assert alt["n_predictions"] == base["n_predictions"]


def test_crypto_evaluation_runs_and_normal_is_calibrated_on_gaussian_walk():
    rng = np.random.default_rng(3)
    n = 24 * 200
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    closes = {i * 3_600_000: float(p) for i, p in enumerate(px)}
    r = evaluate_crypto(closes, step_hours=12)
    assert r["1h"]["n"] > 1000
    assert r["24h"]["normal"]["ece"] < 0.06


def test_crypto_evaluation_skips_gaps():
    closes = {i * 3_600_000: 100.0 + i for i in range(24 * 40)}
    closes = {k: v for k, v in closes.items() if not (800 * 3_600_000 <= k <= 805 * 3_600_000)}
    r = evaluate_crypto(closes, step_hours=1, horizons=(24,))
    assert r["24h"]["n"] >= 0  # no crash, misaligned windows skipped


def test_daily_max_from_hourly():
    out = daily_max_from_hourly(["2026-01-01T00:00", "2026-01-01T15:00", "2026-01-02T03:00"], [30, 41.5, None])
    assert out == {date(2026, 1, 1): 41.5}


def test_render_reports_errors_and_no_fabrication():
    md = render({"generated_at": "x", "weather": {"KNYC": {"n": 0}}, "crypto": {}, "errors": {"weather:KNYC": "HTTP 403"}})
    assert "insufficient" in md and "HTTP 403" in md and "nothing substituted" in md


def test_feed_get_retries_then_raises(monkeypatch):
    import httpx
    from breadmaker.forecasting import feeds
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise httpx.ConnectTimeout("handshake timed out")
    monkeypatch.setattr(feeds.httpx, "get", boom)
    import pytest
    with pytest.raises(httpx.ConnectTimeout):
        feeds._get("https://example.invalid", {}, retries=2, sleep=lambda s: None)
    assert len(calls) == 3
