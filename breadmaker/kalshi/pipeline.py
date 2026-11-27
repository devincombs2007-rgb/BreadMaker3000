"""One research/paper-trading cycle. Requires Kalshi access authorization (OFF by default).

1. Discover open events/markets across all categories (paginated, rate-limited).
2. Route each market to a model: weather_high_temp | crypto_price | structural | rejected(reason).
3. For modelled markets: fetch order book, gather timestamped evidence, forecast, decide (zero
   allocation unless conservative edge after fees >= min_edge), paper-trade within limits.
4. Settle: close positions / score predictions of markets Kalshi reports as settled.
All data stays in the private data dir (Developer Agreement s3.1: no sharing with third parties).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np

from ..config import Settings
from ..db import connect
from ..evidence import EvidenceStore
from ..forecasting import crypto as crypto_model
from ..forecasting import structural
from ..forecasting import weather as weather_model
from ..forecasting.base import Forecast, Rejection
from ..risk import EngineHalts, KillSwitch
from .client import KalshiPublicClient
from .decision import decide
from .markets import Market, fee_rate, parse_orderbook
from .paper import ENGINE, PaperBook
from .tracking import log_prediction, record_settlement

CRYPTO_SERIES = re.compile(r"^KX(BTC|ETH|SOL)D?$")


def route(m: Market, settings: Settings) -> str | None:
    enabled = settings.kalshi.enabled_models
    if m.series_ticker in settings.kalshi.weather_stations and "weather_high_temp" in enabled:
        return "weather_high_temp"
    if CRYPTO_SERIES.match(m.series_ticker) and "crypto_price" in enabled:
        return "crypto_price"
    return None


def correlation_group(m: Market, model: str | None) -> str:
    """Outcomes likely to move together share one exposure budget."""
    if model == "weather_high_temp":
        d = weather_model.event_date(m.event_ticker)
        return f"weather:{d}" if d else m.event_ticker
    if model == "crypto_price" and m.close_time:
        return f"crypto:{m.close_time.date()}"
    return m.event_ticker


def group_exposure(book: PaperBook, groups: dict[str, str], group: str) -> float:
    return sum(pos.cost_basis for sym, pos in book.positions().items() if groups.get(sym.split("|")[0]) == group)


def run_cycle(settings: Settings, client: KalshiPublicClient, max_books: int = 300, now: datetime | None = None,
              engine: str = ENGINE, kcfg=None) -> dict:
    kcfg = kcfg or settings.kalshi
    now = now or datetime.now(timezone.utc)
    conn = connect(settings.db_path)
    ev = EvidenceStore(conn)
    book = PaperBook(conn, kcfg.bankroll, engine)
    groups = {r[0]: r[1] for r in conn.execute("SELECT ticker, group_key FROM market_groups")}
    from .tracking import learned_adjustment
    learned: dict = {}
    kill = KillSwitch(settings.kill_switch_path)
    halted = kill.engaged() or EngineHalts(settings.data_dir).active(engine, now) is not None
    summary: dict = {"run_at": now.isoformat(timespec="seconds"), "categories": Counter(), "routed": Counter(),
                     "rejections": Counter(), "decisions": [], "combinations": [], "incoherent_quotes": [],
                     "settled": 0, "halted": halted}

    markets: list[Market] = []
    for e in client.events(status="open"):
        cat = e.get("category", "") or "uncategorized"
        for md in e.get("markets", []) or []:
            markets.append(Market.from_api({**md, "event_ticker": e.get("event_ticker", md.get("event_ticker", ""))},
                                           category=cat, series_ticker=e.get("series_ticker", "")))
            summary["categories"][cat] += 1

    # ---- single-market models ------------------------------------------------------------
    books_used = 0
    weather_cache: dict = {}
    crypto_cache: dict = {}
    for m in markets:
        model = route(m, settings)
        summary["routed"][model or "none"] += 1
        if model is None:
            summary["rejections"][f"no validated model for category '{m.category}'"] += 1
            continue
        if books_used >= max_books:
            summary["rejections"]["order-book budget for this run exhausted"] += 1
            continue
        quote = parse_orderbook(client.orderbook(m.ticker), now)
        books_used += 1
        if model == "weather_high_temp":
            result = _weather(m, settings, ev, weather_cache, now)
        else:
            result = _crypto(m, ev, crypto_cache, now)
        decision = None
        if isinstance(result, Forecast) and not halted:
            fr = fee_rate(kcfg, m.series_ticker)
            if model not in learned:
                learned[model] = learned_adjustment(conn, model, now)
            rc, verdict = learned[model]
            if rc is not None:  # significance-gated recalibration learned from settled predictions
                result = Forecast(rc.apply(result.p), rc.apply(result.p_low), rc.apply(result.p_high), result.model,
                                  result.issued_at, result.evidence_ids, {**result.detail, "recalibrated": verdict})
            g = correlation_group(m, model)
            groups[m.ticker] = g
            conn.execute("INSERT OR REPLACE INTO market_groups(ticker, group_key) VALUES (?,?)", (m.ticker, g))
            decision = decide(m, quote, result, kcfg, equity=book.equity_at_cost(), cash=book.cash(),
                              event_exposure=book.exposure(m.event_ticker), total_exposure=book.exposure(),
                              now=now, fee_rate=fr, group_exposure=group_exposure(book, groups, g),
                              peak_equity=book.peak_equity(), open_positions=len(book.positions()))
        pid = log_prediction(conn, m, result, quote, decision, now)
        if isinstance(result, Rejection):
            summary["rejections"][result.reason.split(":")[0][:80]] += 1
        if decision is not None:
            summary["decisions"].append(decision.to_dict())
            if decision.side:
                book.buy(decision, pid, now)

    # ---- structural (model-free) combinations, per event ------------------------------------
    by_event: dict[str, list[Market]] = {}
    for m in markets:
        by_event.setdefault(m.event_ticker, []).append(m)
    for evt, ms in by_event.items():
        ladder = [m for m in ms if m.strike_type in ("greater", "greater_or_equal")]
        if len(ladder) < 2 or books_used + len(ladder) > max_books * 2:
            continue
        quotes = {m.ticker: parse_orderbook(client.orderbook(m.ticker), now) for m in ladder}
        books_used += len(ladder)
        combos, incoherent = structural.ladder_opportunities(ladder, quotes, kcfg.taker_fee_rate)
        summary["combinations"] += [c.__dict__ for c in combos]
        summary["incoherent_quotes"] += incoherent

    # ---- settlements ---------------------------------------------------------------------------
    pending: dict[str, set] = {}
    for t, evt in conn.execute("SELECT DISTINCT ticker, event_ticker FROM predictions "
                               "WHERE status='forecast' AND outcome IS NULL AND close_time < ?", (now.isoformat(),)):
        pending.setdefault(evt, set()).add(t)
    for evt in sorted(pending)[:100]:
        for md in client.markets(status="settled", event_ticker=evt, max_pages=1):
            t = md.get("ticker")
            if t in pending[evt] and md.get("result") in ("yes", "no"):
                record_settlement(conn, t, md["result"], now)
                book.settle(t, md["result"], now)
                summary["settled"] += 1
    book.ledger.snapshot(engine, book.equity_at_cost(), book.cash(), ts=now.isoformat(timespec="seconds"))
    summary["requests"] = client.requests_made
    summary["cash"], summary["equity_at_cost"] = book.cash(), book.equity_at_cost()
    return json.loads(json.dumps(summary, default=lambda o: dict(o) if isinstance(o, Counter) else str(o)))


def _weather(m: Market, settings: Settings, ev: EvidenceStore, cache: dict, now: datetime):
    from ..forecasting import feeds
    cfg = settings.kalshi.weather_stations.get(m.series_ticker)
    d = weather_model.event_date(m.event_ticker)
    if cfg is None or d is None:
        return weather_model.forecast(m, cfg, None, [], now, [])
    key = (m.series_ticker, d)
    if key not in cache:
        try:
            std_tz = weather_model.STANDARD_TZ[cfg["tz"]]
            pf, p1 = feeds.forecast_daily_max(cfg["lat"], cfg["lon"], std_tz, d)
            hist_fc, p2 = feeds.previous_day1_daily_max(cfg["lat"], cfg["lon"], std_tz, d - timedelta(days=100), d - timedelta(days=2))
            obs = {}
            ids = []
            for y in {(d - timedelta(days=100)).year, d.year}:
                o, p3 = feeds.cli_daily_highs(cfg["station"], y)
                obs.update(o)
                ids.append(ev.record_provenance(p3, {"days": len(o)}))
            ids.append(ev.record_provenance(p1, {"forecast_max_f": pf}))
            ids.append(ev.record_provenance(p2, {"days": len(hist_fc)}))
            cache[key] = (pf, weather_model.residual_pool(hist_fc, obs, d), ids)
        except Exception as exc:  # noqa: BLE001
            cache[key] = (None, np.array([]), [], f"{type(exc).__name__}")
    pf, pool, ids = cache[key][:3]
    return weather_model.forecast(m, cfg, pf, pool, now, ids)


def _crypto(m: Market, ev: EvidenceStore, cache: dict, now: datetime):
    from ..forecasting import feeds
    base = CRYPTO_SERIES.match(m.series_ticker).group(1)
    if base not in cache:
        try:
            closes, prov = feeds.crypto_hourly_closes(base, now - timedelta(days=45))
            ts = sorted(closes)
            eid = ev.record_provenance(prov, {"hours": len(ts), "last_ms": ts[-1]})
            cache[base] = (closes[ts[-1]], np.diff(np.log([closes[t] for t in ts])), [eid])
        except Exception:  # noqa: BLE001
            cache[base] = (None, np.array([]), [])
    spot, lr, ids = cache[base]
    if m.close_time is None:
        return Rejection("crypto_price", "no close time")
    return crypto_model.forecast(m, spot, lr, (m.close_time - now).total_seconds() / 3600, now, ids)
