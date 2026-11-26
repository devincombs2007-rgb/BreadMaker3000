"""Real historical market data downloaders (used by the GitHub Actions research workflow).

Every dataset is written with a manifest entry: source, symbol, timeframe, rows, first/last
timestamp, SHA-256 of the saved file, and quality problems found by `validate_ohlcv`.
Network access is required; failures are recorded, never papered over with synthetic data.

Crypto: public OHLCV via ccxt, trying venues in order (Binance is geo-blocked from US runners).
ETFs:   daily, split/dividend-ADJUSTED OHLC via yfinance, fallback Stooq CSV.
Known biases: survivorship (today's tickers only); venue != execution venue for crypto
(execution costs are modelled on Kraken's taker fee regardless of data venue).
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..data import validate_ohlcv

CRYPTO_VENUES = ["binance", "binanceus", "coinbaseexchange", "kraken", "bitstamp"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(df: pd.DataFrame, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index(names="date")[["date", "open", "high", "low", "close", "volume"]].to_feather(out)
    return out


def fetch_crypto(base: str, timeframe: str, since: datetime, out_dir: Path,
                 venues: list[str] | None = None, max_requests: int = 400) -> dict:
    import ccxt

    errors = {}
    for venue in venues or CRYPTO_VENUES:
        try:
            ex = getattr(ccxt, venue)({"enableRateLimit": True, "timeout": 20000})
            ex.load_markets()
            symbol = next((s for s in (f"{base}/USD", f"{base}/USDT", f"{base}/USDC") if s in ex.markets), None)
            if symbol is None:
                errors[venue] = "no USD/USDT/USDC market"
                continue
            tf_ms = ex.parse_timeframe(timeframe) * 1000
            cursor = int(since.timestamp() * 1000)
            rows, reqs = [], 0
            now_ms = ex.milliseconds()
            while cursor < now_ms - tf_ms and reqs < max_requests:
                batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
                reqs += 1
                if not batch:
                    break
                rows.extend(batch)
                nxt = batch[-1][0] + tf_ms
                if nxt <= cursor:
                    break
                cursor = nxt
            if len(rows) < 100:
                errors[venue] = f"only {len(rows)} rows"
                continue
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
            df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
            df = df.sort_index()
            df = df[df.index < pd.Timestamp.now(tz="UTC").floor(timeframe)]  # drop the still-forming candle
            out = _save(df, out_dir / f"{base}_USD-{timeframe}.feather")
            return {"kind": "crypto", "symbol": base, "pair": symbol, "venue": venue, "timeframe": timeframe,
                    "rows": len(df), "first": str(df.index[0]), "last": str(df.index[-1]), "file": out.name,
                    "sha256": _sha(out), "problems": validate_ohlcv(df), "errors_before_success": errors}
        except Exception as exc:  # noqa: BLE001 - try the next venue, keep the reason
            errors[venue] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {"kind": "crypto", "symbol": base, "timeframe": timeframe, "failed": True, "errors": errors}


def _yfinance_daily(sym: str, start: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(sym, start=start, auto_adjust=True, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df


def _stooq_daily(sym: str, start: str) -> pd.DataFrame:
    import httpx
    r = httpx.get(f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d", timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    return df.loc[df.index >= pd.Timestamp(start, tz="UTC"), ["open", "high", "low", "close", "volume"]].dropna()


def fetch_etf(sym: str, start: str, out_dir: Path) -> dict:
    errors = {}
    for name, fn in (("yfinance", _yfinance_daily), ("stooq", _stooq_daily)):
        for attempt in range(3):
            try:
                df = fn(sym, start)
                if len(df) < 250:
                    raise ValueError(f"only {len(df)} rows")
                out = _save(df, out_dir / f"{sym}-1d.feather")
                return {"kind": "etf", "symbol": sym, "venue": name, "adjusted": name == "yfinance",
                        "timeframe": "1d", "rows": len(df), "first": str(df.index[0]), "last": str(df.index[-1]),
                        "file": out.name, "sha256": _sha(out), "problems": validate_ohlcv(df),
                        "errors_before_success": errors}
            except Exception as exc:  # noqa: BLE001
                errors[f"{name}#{attempt + 1}"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                time.sleep(2 * (attempt + 1))
    return {"kind": "etf", "symbol": sym, "failed": True, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Download real historical data + manifest")
    ap.add_argument("--out", default="research_data")
    ap.add_argument("--crypto", default="BTC,ETH,SOL")
    ap.add_argument("--crypto-1h-since", default="2023-01-01")
    ap.add_argument("--crypto-1d-since", default="2018-01-01")
    ap.add_argument("--etfs", default="SPY,QQQ,IWM,EFA,EEM,TLT,IEF,GLD,VNQ,DBC")
    ap.add_argument("--etf-start", default="2007-01-01")
    a = ap.parse_args(argv)
    out = Path(a.out)
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "datasets": []}
    for base in filter(None, a.crypto.split(",")):
        for tf, since in (("1d", a.crypto_1d_since), ("1h", a.crypto_1h_since)):
            m = fetch_crypto(base, tf, datetime.fromisoformat(since).replace(tzinfo=timezone.utc), out / "crypto")
            manifest["datasets"].append(m)
            print(json.dumps({k: v for k, v in m.items() if k != "errors_before_success"}))
    for sym in filter(None, a.etfs.split(",")):
        m = fetch_etf(sym, a.etf_start, out / "etf")
        manifest["datasets"].append(m)
        print(json.dumps({k: v for k, v in m.items() if k != "errors_before_success"}))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    failed = [d for d in manifest["datasets"] if d.get("failed")]
    print(f"datasets ok={len(manifest['datasets']) - len(failed)} failed={len(failed)}")
    return 0 if len(failed) < len(manifest["datasets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
