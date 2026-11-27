"""Read-only Kalshi public market-data client.

* GET only. There is deliberately no order, portfolio, or authenticated method in this codebase.
* Every request passes the access gate (`config.kalshi_access_allowed`) - OFF by default.
* Client-side token bucket keeps us under `max_requests_per_second` (conservative default 5/s).
* Bounded exponential backoff with jitter on HTTP 429 / 5xx / network errors, honouring
  Retry-After (Kalshi Developer Agreement v1.1 s4.3 requires backoff and bounded retries).
* Endpoint paths follow Kalshi Trade API v2 as known at build time; they could not be re-checked
  against live docs from the build sandbox and are verified on first authorized run.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Iterator

import httpx

from ..config import KalshiConfig, SafetyError, _host, kalshi_access_allowed, kalshi_demo_access_allowed


class KalshiAccessDisabled(RuntimeError):
    pass


class TokenBucket:
    def __init__(self, rate: float, burst: int = 1, clock=time.monotonic, sleep=time.sleep):
        self.rate, self.capacity = rate, max(1, burst)
        self.tokens = float(self.capacity)
        self.clock, self.sleep = clock, sleep
        self.last = clock()
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1 - 1e-9:  # tolerance: float refill can land at 0.9999999...
                    self.tokens = max(0.0, self.tokens - 1)
                    return
                self.sleep(max((1 - self.tokens) / self.rate, 1e-3))  # never a sub-resolution sleep


class KalshiPublicClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, cfg: KalshiConfig, env: dict | None = None, transport: httpx.BaseTransport | None = None,
                 max_retries: int = 5, sleep=time.sleep, demo: bool = False):
        ok, why = (kalshi_demo_access_allowed if demo else kalshi_access_allowed)(cfg, env)
        if not ok:
            raise KalshiAccessDisabled(why)
        self.cfg = cfg
        self.demo = demo
        self.host = _host(cfg.base_url)
        self.base = cfg.base_url.rstrip("/")
        self.http = httpx.Client(timeout=20, transport=transport,
                                 headers={"User-Agent": "breadmaker3000-research/0.3 (read-only)"})
        self.bucket = TokenBucket(cfg.max_requests_per_second, sleep=sleep)
        self.max_retries = max_retries
        self.sleep = sleep
        self.requests_made = 0

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not path.startswith("/") or any(x in path for x in ("/portfolio", "/orders", "/login")):
            raise SafetyError(f"path {path!r} is not a public market-data endpoint")
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self.bucket.take()
            self.requests_made += 1
            try:
                r = self.http.get(self.base + path, params={k: v for k, v in (params or {}).items() if v is not None})
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 60)
                continue
            if r.status_code in self.RETRY_STATUSES and attempt < self.max_retries:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else delay
                self.sleep(wait + random.uniform(0, delay / 2))
                delay = min(delay * 2, 60)
                continue
            if _host(str(r.url)) != self.host:  # never follow a redirect to another (e.g. production) host
                raise SafetyError(f"response came from unexpected host {_host(str(r.url))!r}")
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    def _paginate(self, path: str, key: str, params: dict, max_pages: int) -> Iterator[dict]:
        cursor = None
        for _ in range(max_pages):
            data = self._get(path, {**params, "cursor": cursor})
            yield from data.get(key, []) or []
            cursor = data.get("cursor")
            if not cursor:
                return

    # ---- public market data ---------------------------------------------------------
    def exchange_status(self) -> dict:
        return self._get("/exchange/status")

    def series(self, series_ticker: str) -> dict:
        return self._get(f"/series/{series_ticker}").get("series", {})

    def events(self, status: str = "open", series_ticker: str | None = None, max_pages: int = 20) -> Iterator[dict]:
        return self._paginate("/events", "events", {"status": status, "series_ticker": series_ticker,
                                                    "limit": 200, "with_nested_markets": "true"}, max_pages)

    def markets(self, status: str = "open", series_ticker: str | None = None, event_ticker: str | None = None,
                max_pages: int = 20) -> Iterator[dict]:
        return self._paginate("/markets", "markets", {"status": status, "series_ticker": series_ticker,
                                                      "event_ticker": event_ticker, "limit": 1000}, max_pages)

    def orderbook(self, ticker: str, depth: int = 10) -> dict:
        return self._get(f"/markets/{ticker}/orderbook", {"depth": depth})
