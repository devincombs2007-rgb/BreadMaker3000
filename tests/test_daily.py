"""Forward log, health checks/alerts, rankings. SYNTHETIC inputs; GitHub API mocked."""
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from breadmaker import forward, health
from breadmaker.forecasting.base import Forecast, Rejection
from breadmaker.kalshi.decision import Decision
from breadmaker.kalshi.markets import Market, parse_orderbook
from breadmaker.kalshi.tracking import log_prediction, rankings, rankings_markdown

NOW = datetime(2026, 12, 1, 13, 40, tzinfo=timezone.utc)


# ---- forward log ------------------------------------------------------------------------------
def test_forward_forecasts_are_idempotent_and_settle_once(tmp_path):
    con = forward.connect(tmp_path / "f.sqlite")
    resid = np.array([-2, -1, 0, 1, 2] * 10, float)
    t = date(2026, 12, 2)
    assert forward.record_forecasts(con, "KNYC", t, 45.0, resid, NOW) == 9
    assert forward.record_forecasts(con, "KNYC", t, 99.0, resid, NOW) == 0          # never overwritten
    assert con.execute("SELECT DISTINCT point_forecast FROM fwd").fetchall()[0][0] == 45.0
    assert forward.settle(con, "KNYC", {t: 46.0}, NOW) == 9
    assert forward.settle(con, "KNYC", {t: 10.0}, NOW) == 0                         # settled once
    outs = {r[0]: r[1] for r in con.execute("SELECT threshold, outcome FROM fwd")}
    assert outs[46] == 1 and outs[47] == 0


def test_forward_refuses_small_residual_pool(tmp_path):
    con = forward.connect(tmp_path / "f.sqlite")
    assert forward.record_forecasts(con, "KNYC", date(2026, 12, 2), 45.0, np.zeros(10), NOW) == 0


def test_scoreboard_needs_50_settled(tmp_path):
    con = forward.connect(tmp_path / "f.sqlite")
    resid = np.array([-2, -1, 0, 1, 2] * 10, float)
    rng = np.random.default_rng(0)
    for i in range(8):
        d = date(2026, 11, 1) + timedelta(days=i)
        forward.record_forecasts(con, "KNYC", d, 45.0, resid, NOW)
        forward.settle(con, "KNYC", {d: 45.0 + float(rng.integers(-2, 3))}, NOW)
    sb = forward.scoreboard(con)
    assert sb["settled"] == 72 and "tournament" in sb


def test_run_daily_with_mocked_feeds(tmp_path, monkeypatch):
    from breadmaker.forecasting import feeds
    prov = {"source": "SYNTHETIC", "url": "x", "params": {}, "fetched_at": NOW, "sha256": "0"}
    hist = {date(2026, 12, 1) - timedelta(days=k): 50.0 for k in range(1, 110)}
    obs = {d: 51.0 for d in hist}
    monkeypatch.setattr(feeds, "cli_daily_highs", lambda *a, **k: (obs, prov))
    monkeypatch.setattr(feeds, "forecast_daily_max", lambda *a, **k: (50.0, prov))
    monkeypatch.setattr(feeds, "previous_day1_daily_max", lambda *a, **k: (hist, prov))
    st = {"KXHIGHNY": {"station": "KNYC", "lat": 0, "lon": 0, "tz": "America/New_York"}}
    rep = forward.run_daily(st, tmp_path / "f.sqlite", NOW)
    assert rep["issued"] == 9 and not rep["errors"]
    con = forward.connect(tmp_path / "f.sqlite")
    assert con.execute("SELECT DISTINCT target_date FROM fwd").fetchone()[0] == "2026-12-02"   # tomorrow, local std time


def test_run_daily_records_errors_without_fabricating(tmp_path, monkeypatch):
    from breadmaker.forecasting import feeds

    def boom(*a, **k):
        raise httpx.ConnectTimeout("down")
    monkeypatch.setattr(feeds, "cli_daily_highs", boom)
    rep = forward.run_daily({"S": {"station": "KNYC", "lat": 0, "lon": 0, "tz": "America/New_York"}},
                            tmp_path / "f.sqlite", NOW)
    assert rep["issued"] == 0 and "KNYC" in rep["errors"]


# ---- health ---------------------------------------------------------------------------------------
def gh(routes):
    def handler(req):
        for key, resp in routes.items():
            if key in req.url.path:
                return resp(req) if callable(resp) else httpx.Response(200, json=resp)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def healthy_routes(art_expired=False, ci="success"):
    return {"/workflows/ci.yml/runs": {"workflow_runs": [{"conclusion": ci, "html_url": "u"}]},
            "/workflows/forecast-validation.yml/runs": {"workflow_runs": [{"updated_at": "2026-11-30T00:00:00Z"}]},
            "/actions/artifacts": {"artifacts": [{"id": 1, "expired": art_expired, "created_at": "2026-12-01T00:00:00Z",
                                                  "expires_at": "2027-03-01T00:00:00Z"}]},
            "/repos/o/r": {"private": False}}


def test_health_ok_and_public_repo_noted():
    f = health.check("o/r", "t", NOW, transport=gh(healthy_routes()))
    assert health.worst(f) == "ok"
    assert any("public" in x["detail"] for x in f)


def test_health_flags_failed_ci_and_expired_state():
    f = health.check("o/r", "t", NOW, transport=gh(healthy_routes(art_expired=True, ci="failure")))
    assert health.worst(f) == "error"
    assert {x["check"] for x in f if x["severity"] == "error"} == {"ci", "forward-log"}


def test_alert_opens_comments_and_closes():
    calls = []

    def issues_list(open_issue):
        return lambda req: httpx.Response(200, json=[{"number": 5, "title": health.ALERT_TITLE}] if open_issue else [])

    def handler_factory(open_issue):
        def handler(req):
            calls.append((req.method, req.url.path))
            if req.method == "GET":
                return issues_list(open_issue)(req)
            return httpx.Response(201, json={})
        return httpx.MockTransport(handler)
    bad = [{"severity": "error", "check": "ci", "detail": "x"}]
    ok = [{"severity": "ok", "check": "ci", "detail": "x"}]
    assert health.alert("o/r", "t", bad, transport=handler_factory(False)) == "opened"
    assert health.alert("o/r", "t", bad, transport=handler_factory(True)) == "commented"
    assert health.alert("o/r", "t", ok, transport=handler_factory(True)) == "closed"
    assert health.alert("o/r", "t", ok, transport=handler_factory(False)) == "none"
    assert ("PATCH", "/repos/o/r/issues/5") in calls


# ---- rankings ---------------------------------------------------------------------------------------
def test_rankings_orders_allocations_candidates_and_rejections(conn):
    now = NOW
    q = parse_orderbook({"orderbook": {"yes": [[28, 50]], "no": [[70, 50]]}}, now)

    def m(t):
        return Market(t, "E", "S", "", "active", now + timedelta(hours=5), "greater", 1.0, None, "")
    fc = Forecast(0.6, 0.55, 0.65, "weather_high_temp", now, [])
    log_prediction(conn, m("A"), fc, q, Decision("A", "yes", 3, 0.30, 0.03, 0.24, 0.87, "edge after fees"), now)
    log_prediction(conn, m("B"), fc, q, Decision("B", None, 0, 0.30, 0.0, 0.01, 0.0, "edge too small"), now)
    log_prediction(conn, m("C"), Rejection("none", "no validated model for category 'Sports'"), None, None, now)
    log_prediction(conn, m("D"), Rejection("none", "no validated model for category 'Sports'"), None, None, now)
    rk = rankings(conn, now=now + timedelta(minutes=1))
    assert [a["ticker"] for a in rk["allocations"]] == ["A"]
    assert [c["ticker"] for c in rk["candidates"]] == ["B"]
    assert rk["rejections"][0]["count"] == 2
    md = rankings_markdown(rk)
    assert "BUY 3 YES" in md and "2 ×" in md and "|" not in md   # phone-friendly: no tables
