"""External evidence feeds (free, no API keys). Each returns (data, provenance) where provenance has
source, url, params, fetched_at (UTC), sha256 of the raw response - for the evidence store.

* Open-Meteo (CC BY 4.0, free non-commercial tier, <10k calls/day): forecasts and forecasts from
  previous model runs ('*_previous_day1'), requested in local STANDARD time to match NWS CLI days.
* Iowa Environmental Mesonet: archive of NWS Daily Climate Reports (CLI) - the settlement source
  named in Kalshi's temperature contracts.
* Crypto candles: public OHLCV via ccxt (exchange chosen by availability, recorded in provenance).
Endpoint shapes could not be checked from the build sandbox; parsers are defensive and failures are
reported, never replaced with made-up values.
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timezone

import httpx

UA = {"User-Agent": "breadmaker3000-research/0.3 (non-commercial research)"}
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PREVIOUS = ["https://previous-runs-api.open-meteo.com/v1/forecast",
                       "https://historical-forecast-api.open-meteo.com/v1/forecast"]
IEM_CLI = "https://mesonet.agron.iastate.edu/json/cli.py"


def _prov(source: str, url: str, params: dict, content: bytes) -> dict:
    return {"source": source, "url": url, "params": params, "fetched_at": datetime.now(timezone.utc),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def _get(url: str, params: dict, timeout: float = 60, retries: int = 3, sleep=time.sleep) -> httpx.Response:
    """GET with bounded exponential backoff on network errors, 429 and 5xx (polite to free services)."""
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            r = httpx.get(url, params=params, headers=UA, timeout=timeout, follow_redirects=True)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        except httpx.TransportError:
            if attempt == retries:
                raise
            sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def daily_max_from_hourly(times: list[str], temps: list) -> dict[date, float]:
    out: dict[date, float] = {}
    for t, v in zip(times, temps):
        if v is None:
            continue
        d = date.fromisoformat(t[:10])
        out[d] = max(out.get(d, float("-inf")), float(v))
    return out


def previous_day1_daily_max(lat: float, lon: float, std_tz: str, start: date, end: date):
    """Daily max of the hourly temperature forecast issued ~1 day ahead, per local-standard-time day."""
    params = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m_previous_day1",
              "temperature_unit": "fahrenheit", "timezone": std_tz,
              "start_date": start.isoformat(), "end_date": end.isoformat()}
    errors = {}
    for url in OPEN_METEO_PREVIOUS:
        try:
            r = _get(url, params)
            h = r.json().get("hourly", {})
            temps = h.get("temperature_2m_previous_day1")
            if not temps:
                errors[url] = "no temperature_2m_previous_day1 in response"
                continue
            return daily_max_from_hourly(h["time"], temps), _prov("open-meteo previous runs", url, params, r.content)
        except Exception as exc:  # noqa: BLE001
            errors[url] = f"{type(exc).__name__}: {str(exc)[:160]}"
    raise RuntimeError(f"Open-Meteo previous-runs unavailable: {errors}")


def forecast_daily_max(lat: float, lon: float, std_tz: str, target: date):
    params = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
              "timezone": std_tz, "start_date": target.isoformat(), "end_date": target.isoformat()}
    r = _get(OPEN_METEO_FORECAST, params)
    h = r.json().get("hourly", {})
    return daily_max_from_hourly(h.get("time", []), h.get("temperature_2m", [])).get(target), \
        _prov("open-meteo forecast", OPEN_METEO_FORECAST, params, r.content)


def cli_daily_highs(station: str, year: int):
    """NWS Daily Climate Report highs (degF) for one station-year from the IEM archive."""
    params = {"station": station, "year": year}
    r = _get(IEM_CLI, params)
    rows = r.json().get("results", [])
    out: dict[date, float] = {}
    for row in rows:
        v, hi = row.get("valid"), row.get("high")
        if v and hi not in (None, "M", "") and isinstance(hi, (int, float)):
            out[date.fromisoformat(v[:10])] = float(hi)
    return out, _prov("IEM NWS CLI archive", IEM_CLI, params, r.content)


def crypto_hourly_closes(base: str, since: datetime, venues=("coinbaseexchange", "binanceus", "kraken")):
    import ccxt
    errors = {}
    for venue in venues:
        try:
            ex = getattr(ccxt, venue)({"enableRateLimit": True, "timeout": 20000})
            ex.load_markets()
            sym = next((s for s in (f"{base}/USD", f"{base}/USDT") if s in ex.markets), None)
            if not sym:
                errors[venue] = "no market"
                continue
            rows, cursor = [], int(since.timestamp() * 1000)
            for _ in range(400):
                batch = ex.fetch_ohlcv(sym, "1h", since=cursor, limit=300 if venue == "coinbaseexchange" else 1000)
                if not batch:
                    break
                rows.extend(batch)
                nxt = batch[-1][0] + 3_600_000
                if nxt <= cursor or nxt > ex.milliseconds():
                    break
                cursor = nxt
            if len(rows) < 100:
                errors[venue] = f"only {len(rows)} rows"
                continue
            dedup = {r[0]: r[4] for r in rows}
            content = repr(sorted(dedup.items())).encode()
            return dedup, _prov(f"{venue} {sym} 1h OHLCV via ccxt", venue, {"symbol": sym, "since": since.isoformat()},
                                content)
        except Exception as exc:  # noqa: BLE001
            errors[venue] = f"{type(exc).__name__}: {str(exc)[:160]}"
    raise RuntimeError(f"no crypto venue served {base}: {errors}")
