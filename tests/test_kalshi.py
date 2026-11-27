"""Kalshi client, pricing, models, decisions, paper book and tracking.

ALL market data in this file is SYNTHETIC, hand-written to match the documented response shapes.
It is not Kalshi data and says nothing about real markets or profitability."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from breadmaker.config import KalshiConfig, SafetyError
from breadmaker.db import connect
from breadmaker.forecasting import crypto, structural, weather
from breadmaker.forecasting.base import Forecast, Rejection, beta_interval
from breadmaker.kalshi.client import KalshiAccessDisabled, KalshiPublicClient, TokenBucket
from breadmaker.kalshi.decision import decide, kelly_fraction_binary
from breadmaker.kalshi.markets import Market, parse_orderbook, price_field, taker_fee
from breadmaker.kalshi.paper import PaperBook
from breadmaker.kalshi.tracking import (brier, calibration_table, expected_calibration_error, fit_recalibrator,
                                        log_prediction, metrics, record_settlement, settled_records,
                                        walk_forward_recalibration)
from breadmaker.ledger import LedgerError

NOW = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)
AUTH = {"KALSHI_ACCESS_AUTHORIZED": "written-authorization-on-file"}
ON = KalshiConfig(api_enabled=True)


def mk(ticker="KXHIGHNY-26NOV28-T50", st="greater", lo=50.0, hi=None, status="active",
       rules="Highest temperature recorded in Central Park, New York per the NWS Climatological Report.",
       close=NOW + timedelta(hours=20)):
    return Market(ticker, ticker.rsplit("-", 1)[0], ticker.split("-")[0], "SYNTHETIC", status, close, st, lo, hi, rules)


def book(yes, no):
    return parse_orderbook({"orderbook": {"yes": yes, "no": no}}, NOW)


# ---- access gate & client -----------------------------------------------------------------
def test_client_refuses_without_authorization():
    with pytest.raises(KalshiAccessDisabled):
        KalshiPublicClient(KalshiConfig())
    with pytest.raises(KalshiAccessDisabled):
        KalshiPublicClient(ON, env={})


def test_client_has_no_trading_methods():
    names = {n for n in dir(KalshiPublicClient) if not n.startswith("__")}
    assert not names & {"create_order", "cancel_order", "place_order", "portfolio", "balance", "positions", "login"}


def test_client_blocks_private_paths():
    c = KalshiPublicClient(ON, env=AUTH, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    for path in ("/portfolio/balance", "/portfolio/orders", "/login"):
        with pytest.raises(SafetyError):
            c._get(path)


def test_backoff_on_429_then_success_and_bounded():
    calls, sleeps = [], []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(429, headers={"Retry-After": "2"}) if len(calls) < 3 else httpx.Response(200, json={"ok": 1})
    c = KalshiPublicClient(ON, env=AUTH, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert c._get("/exchange/status") == {"ok": 1}
    assert len(calls) == 3 and any(s >= 2 for s in sleeps)
    c2 = KalshiPublicClient(ON, env=AUTH, transport=httpx.MockTransport(lambda r: httpx.Response(503)),
                            max_retries=3, sleep=lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        c2._get("/exchange/status")
    assert c2.requests_made == 4  # 1 + 3 retries, then gives up


def test_pagination_follows_cursor():
    pages = {None: {"markets": [{"ticker": "A"}], "cursor": "c1"}, "c1": {"markets": [{"ticker": "B"}], "cursor": ""}}
    c = KalshiPublicClient(ON, env=AUTH, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=pages[r.url.params.get("cursor")])), sleep=lambda s: None)
    assert [m["ticker"] for m in c.markets()] == ["A", "B"]


def test_token_bucket_limits_rate():
    t = [0.0]
    slept = []
    tb = TokenBucket(5.0, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    for _ in range(11):
        tb.take()
    assert t[0] >= 2.0 - 1e-9  # 11 requests at 5/s need >= 2 s


# ---- prices & fees ------------------------------------------------------------------------
def test_orderbook_derives_asks_from_opposite_bids():
    q = book(yes=[[40, 10], [42, 5]], no=[[50, 7], [55, 3]])
    assert (q.yes_bid, q.yes_bid_size) == (0.42, 5)
    assert (q.yes_ask, q.yes_ask_size) == (0.45, 3)     # 1 - best NO bid 0.55
    assert (q.no_ask, q.no_ask_size) == (0.58, 5)
    assert q.spread == pytest.approx(0.03)


def test_orderbook_dollar_fields_preferred():
    q = parse_orderbook({"orderbook": {"yes_dollars": [["0.4200", 5]], "no_dollars": [["0.5500", 3]],
                                       "yes": [[1, 1]], "no": [[1, 1]]}}, NOW)
    assert q.yes_bid == 0.42 and q.yes_ask == 0.45
    assert price_field({"yes_ask": 45}, "yes_ask") == 0.45
    assert price_field({"yes_ask": 45, "yes_ask_dollars": "0.4600"}, "yes_ask") == 0.46


def test_empty_book_has_no_quotes():
    q = book([], [])
    assert q.yes_ask is None and q.no_ask is None and q.spread is None


@pytest.mark.parametrize("c,p,expected", [(1, 0.50, 0.02), (10, 0.50, 0.18), (100, 0.50, 1.75), (1, 0.01, 0.01),
                                          (0, 0.5, 0.0)])
def test_taker_fee_rounds_up_to_cent(c, p, expected):
    assert taker_fee(c, p) == pytest.approx(expected)


# ---- weather model -------------------------------------------------------------------------
def test_event_date_parsing():
    assert weather.event_date("KXHIGHNY-26NOV28") == date(2026, 11, 28)
    assert weather.event_date("KXHIGHNY-26XYZ28") is None


def test_weather_probability_from_residuals():
    resid = np.array([-2, -1, 0, 0, 1, 2] * 10, dtype=float)
    p = weather.forecast(mk(st="greater", lo=50), {"rules_keyword": "Central Park", "station": "KNYC"},
                         50.0, resid, NOW, [1])
    assert isinstance(p, Forecast)
    # samples round(50 + r) > 50 -> r in {1,2}: 20/60
    assert p.p == pytest.approx((20 + 0.5) / 61) and p.p_low < p.p < p.p_high


def test_weather_rejects_insufficient_or_mismatched():
    cfg = {"rules_keyword": "Central Park"}
    assert isinstance(weather.forecast(mk(), cfg, 50.0, np.zeros(10), NOW, []), Rejection)
    assert "rules text" in weather.forecast(mk(rules="Some other station"), cfg, 50, np.zeros(60), NOW, []).reason
    assert isinstance(weather.forecast(mk(st="custom"), cfg, 50, np.zeros(60), NOW, []), Rejection)
    assert isinstance(weather.forecast(mk(), cfg, None, np.zeros(60), NOW, []), Rejection)


def test_residual_pool_has_no_lookahead():
    d0 = date(2026, 6, 30)
    fc = {date.fromordinal(d0.toordinal() - k): 70.0 for k in range(0, 120)}
    obs = {d: 72.0 for d in fc}
    obs[d0] = 999.0                                    # target day outcome must never enter the pool
    obs[date.fromordinal(d0.toordinal() - 1)] = 999.0  # nor yesterday's (not final when forecast issued)
    pool = weather.residual_pool(fc, obs, d0)
    assert len(pool) == 90 and np.all(pool == 2.0)


# ---- crypto model ---------------------------------------------------------------------------
def test_crypto_probability_sane():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 24 * 40)
    m = Market("KXBTCD-26NOV2817-T100000", "KXBTCD-26NOV2817", "KXBTCD", "", "active", NOW + timedelta(hours=10),
               "greater", 100000.0, None, "")
    fc = crypto.forecast(m, 100000.0, r, 10, NOW, [1])
    assert isinstance(fc, Forecast) and 0.4 < fc.p < 0.5 and fc.p_low <= fc.p <= fc.p_high
    far = crypto.forecast(Market("X", "E", "KXBTCD", "", "active", None, "greater", 130000.0, None, ""), 100000.0, r, 10, NOW, [])
    assert far.p < 0.01
    assert isinstance(crypto.forecast(m, 100000.0, r[:100], 10, NOW, []), Rejection)
    assert isinstance(crypto.forecast(m, 100000.0, r, 24 * 30, NOW, []), Rejection)


# ---- structural ------------------------------------------------------------------------------
def test_ladder_arbitrage_detected_only_when_locked_profit():
    a, b = mk("E-T50", lo=50), mk("E-T52", lo=52)
    quotes = {"E-T50": book(yes=[[40, 10]], no=[[45, 10]]),    # YES(>50) ask 0.55
              "E-T52": book(yes=[[60, 10]], no=[[30, 10]])}    # NO(>52) ask 0.40 -> bid on >52 above ask on >50
    combos, incoherent = structural.ladder_opportunities([a, b], quotes)
    assert incoherent and combos and combos[0].min_payout >= combos[0].contracts
    assert combos[0].locked_profit > 0
    fair = {"E-T50": book(yes=[[60, 10]], no=[[38, 10]]), "E-T52": book(yes=[[40, 10]], no=[[58, 10]])}
    assert structural.ladder_opportunities([a, b], fair) == ([], [])


def test_integer_bracket_cover_and_sum():
    ms = [mk("E-L", st="less", lo=None, hi=50), mk("E-B50", st="between", lo=50, hi=51),
          mk("E-B52", st="between", lo=52, hi=53), mk("E-G", st="greater", lo=53)]
    assert structural.integer_bracket_cover(ms)[0]
    gap = [ms[0], ms[1], mk("E-B53", st="between", lo=53, hi=54), ms[3]]
    assert not structural.integer_bracket_cover(gap)[0]
    cheap = {m.ticker: book(yes=[[10, 5]], no=[[78, 5]]) for m in ms}   # each YES ask 0.22 -> sum 0.88
    combo, _ = structural.bracket_sum_opportunity(ms, cheap)
    assert combo and combo.locked_profit > 0
    rich = {m.ticker: book(yes=[[20, 5]], no=[[72, 5]]) for m in ms}    # asks 0.28 -> sum 1.12
    assert structural.bracket_sum_opportunity(ms, rich)[0] is None


# ---- decisions ------------------------------------------------------------------------------
def fc(p, lo, hi):
    return Forecast(p, lo, hi, "weather_high_temp", NOW, [1])


def test_zero_allocation_when_no_edge():
    q = book(yes=[[48, 50]], no=[[50, 50]])  # yes ask 0.50
    d = decide(mk(), q, fc(0.52, 0.47, 0.57), ON, 25, 25, 0, 0, NOW, 0.07)
    assert d.side is None and d.contracts == 0 and "edge" in d.reason


def test_trade_sized_by_kelly_and_caps():
    q = book(yes=[[28, 50]], no=[[70, 50]])  # yes ask 0.30
    d = decide(mk(), q, fc(0.62, 0.55, 0.70), ON, 25, 25, 0, 0, NOW, 0.07)
    assert d.side == "yes" and d.contracts >= 1
    assert d.contracts * d.price <= 0.10 * 25 + 1e-9          # position cap $2.50
    assert d.edge_per_contract >= ON.min_edge


def test_no_side_chosen_when_model_low():
    q = book(yes=[[60, 50]], no=[[35, 50]])  # no ask 0.40, yes ask 0.65
    d = decide(mk(), q, fc(0.30, 0.25, 0.36), ON, 25, 25, 0, 0, NOW, 0.07)
    assert d.side == "no"


@pytest.mark.parametrize("kwargs,reason", [({"status": "closed"}, "status"), ({"close": NOW + timedelta(minutes=30)}, "close")])
def test_market_state_blocks_trading(kwargs, reason):
    q = book(yes=[[28, 50]], no=[[70, 50]])
    d = decide(mk(**kwargs), q, fc(0.62, 0.55, 0.70), ON, 25, 25, 0, 0, NOW, 0.07)
    assert d.side is None and reason in d.reason


def test_wide_spread_and_exposure_limits_block():
    wide = book(yes=[[10, 50]], no=[[70, 50]])
    assert "spread" in decide(mk(), wide, fc(0.62, 0.55, 0.7), ON, 25, 25, 0, 0, NOW, 0.07).reason
    q = book(yes=[[28, 50]], no=[[70, 50]])
    full = decide(mk(), q, fc(0.62, 0.55, 0.70), ON, 25, 25, event_exposure=5.0, total_exposure=0, now=NOW, fee_rate=0.07)
    assert full.side is None


def test_kelly_binary():
    assert kelly_fraction_binary(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_fraction_binary(0.4, 0.5) == 0.0


# ---- paper book & tracking -----------------------------------------------------------------
def test_paper_buy_and_settlement_pnl(conn):
    pb = PaperBook(conn, 25.0)
    q = book(yes=[[28, 50]], no=[[70, 50]])
    m = mk()
    d = decide(m, q, fc(0.62, 0.55, 0.70), ON, 25, 25, 0, 0, NOW, 0.07)
    pid = log_prediction(conn, m, fc(0.62, 0.55, 0.70), q, d, NOW)
    assert pb.buy(d, pid, NOW) and not pb.buy(d, pid, NOW)       # idempotent
    cost = d.contracts * d.price + d.fee
    assert pb.cash() == pytest.approx(25 - cost)
    assert pb.settle(m.ticker, "yes", NOW) == d.contracts
    assert pb.cash() == pytest.approx(25 - cost + d.contracts)
    assert record_settlement(conn, m.ticker, "yes", NOW) == 1
    assert settled_records(conn)[0]["outcome"] == 1


def test_losing_settlement_pays_zero(conn):
    pb = PaperBook(conn, 25.0)
    q = book(yes=[[28, 50]], no=[[70, 50]])
    d = decide(mk(), q, fc(0.62, 0.55, 0.70), ON, 25, 25, 0, 0, NOW, 0.07)
    pb.buy(d, 1, NOW)
    assert pb.settle(mk().ticker, "no", NOW) == 0.0
    assert pb.positions() == {}


def test_zero_price_only_for_settlement(conn):
    pb = PaperBook(conn, 25.0)
    with pytest.raises(LedgerError):
        pb.ledger.record_fill("kalshi", "X|yes", "buy", 1, 0.0, 0, "z")


def test_metrics_and_calibration():
    p = [0.1, 0.4, 0.6, 0.9]
    y = [0, 0, 1, 1]
    assert brier(p, y) == pytest.approx((0.01 + 0.16 + 0.16 + 0.01) / 4)
    assert len(calibration_table(p, y)) == 4
    assert 0 <= expected_calibration_error(p, y) <= 1
    assert metrics(p, y)["n"] == 4


def test_recalibration_fixes_overconfidence():
    rng = np.random.default_rng(1)
    true = rng.uniform(0.2, 0.8, 4000)
    y = (rng.uniform(size=4000) < true).astype(float)
    over = 1 / (1 + np.exp(-3 * np.log(true / (1 - true))))      # overconfident forecasts
    rc = fit_recalibrator(over, y)
    assert brier([rc.apply(p) for p in over], y) < brier(over, y)


def test_walk_forward_recalibration_uses_only_past_settlements():
    recs = []
    t0 = NOW
    for i in range(120):
        issued = t0 + timedelta(days=i)
        recs.append({"issued_at": issued.isoformat(), "settled_at": (issued + timedelta(days=1)).isoformat(),
                     "p": 0.9, "outcome": i % 2})
    res = walk_forward_recalibration(recs, min_train=50)
    assert res["evaluated"] == 120 - 51 and res["use_recalibration"] is True
    # a future-settled record must not be usable for an earlier prediction
    leak = [dict(r, settled_at=(NOW + timedelta(days=999)).isoformat()) for r in recs]
    assert walk_forward_recalibration(leak)["use_recalibration"] is False


def test_beta_interval():
    p, lo, hi = beta_interval(5, 10)
    assert lo < p < hi and p == pytest.approx(0.5)


# ---- end-to-end pipeline on a mock Kalshi API (synthetic) ---------------------------------------
def test_pipeline_end_to_end_with_mock_api(tmp_settings, monkeypatch):
    from breadmaker.kalshi import pipeline
    from breadmaker.forecasting import feeds

    close = (NOW + timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    rules = "SYNTHETIC: highest temperature at Central Park, New York, per the NWS Climatological Report."
    events = {"events": [
        {"event_ticker": "KXHIGHNY-26NOV28", "series_ticker": "KXHIGHNY", "category": "Climate and Weather",
         "markets": [{"ticker": f"KXHIGHNY-26NOV28-T{k}", "status": "active", "close_time": close,
                      "strike_type": "greater", "floor_strike": k, "rules_primary": rules} for k in (48, 52)]},
        {"event_ticker": "KXELECT-26", "series_ticker": "KXELECT", "category": "Politics",
         "markets": [{"ticker": "KXELECT-26-A", "status": "active", "close_time": close, "strike_type": "custom",
                      "rules_primary": "SYNTHETIC"}]}], "cursor": ""}
    books = {"KXHIGHNY-26NOV28-T48": {"orderbook": {"yes": [[20, 30]], "no": [[75, 30]]}},   # yes ask 0.25
             "KXHIGHNY-26NOV28-T52": {"orderbook": {"yes": [[40, 30]], "no": [[57, 30]]}}}

    def handler(req):
        path = req.url.path
        if path.endswith("/events"):
            return httpx.Response(200, json=events)
        if path.endswith("/orderbook"):
            return httpx.Response(200, json=books[path.split("/")[-2]])
        if path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [], "cursor": ""})
        return httpx.Response(404)

    prov = {"source": "SYNTHETIC", "url": "mock", "params": {}, "fetched_at": NOW, "sha256": "0" * 64}
    monkeypatch.setattr(feeds, "forecast_daily_max", lambda *a, **k: (50.0, prov))
    hist = {date(2026, 11, 26) - timedelta(days=k): 50.0 for k in range(1, 110)}
    monkeypatch.setattr(feeds, "previous_day1_daily_max", lambda *a, **k: (hist, prov))
    obs = {d: 50.0 + (1 if i % 2 else -1) for i, d in enumerate(hist)}
    monkeypatch.setattr(feeds, "cli_daily_highs", lambda *a, **k: (obs, prov))

    client = KalshiPublicClient(ON, env=AUTH, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = pipeline.run_cycle(tmp_settings, client, now=NOW)
    assert out["categories"] == {"Climate and Weather": 2, "Politics": 1}
    assert out["routed"]["weather_high_temp"] == 2
    assert any("no validated model" in r for r in out["rejections"])
    conn = connect(tmp_settings.db_path)
    rows = [dict(r) for r in conn.execute("SELECT * FROM predictions ORDER BY id")]
    assert len(rows) == 2 and all(r["status"] == "forecast" for r in rows)
    # synthetic residuals +-1 around 50: P(>48) ~ 1, P(>52) ~ 0
    assert rows[0]["p"] > 0.9 and rows[1]["p"] < 0.1
    assert all(json.loads(r["evidence_ids"]) for r in rows)
    acted = [d for d in out["decisions"] if d["side"]]
    assert acted and all(d["contracts"] * d["price"] <= 2.5 + 1e-9 for d in acted)
    assert out["cash"] <= 25.0
