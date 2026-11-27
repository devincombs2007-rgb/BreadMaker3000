"""SQLite persistence (WAL mode) shared by core services.

SQLite is deliberate: zero ops, survives restarts, fits Always Free resources,
and is never exposed on the network.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios (
    engine TEXT PRIMARY KEY,
    asset_class TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL REFERENCES portfolios(engine),
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy','sell')),
    qty REAL NOT NULL CHECK (qty > 0),
    price REAL NOT NULL CHECK (price >= 0),
    fee REAL NOT NULL DEFAULT 0,
    client_order_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paper',
    UNIQUE (engine, client_order_id)
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    engine TEXT NOT NULL,
    ts TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    PRIMARY KEY (engine, ts)
);
CREATE TABLE IF NOT EXISTS order_ids (
    engine TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (engine, client_order_id)
);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    engine TEXT NOT NULL,
    severity TEXT NOT NULL,
    rule TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS heartbeats (
    worker TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    family TEXT NOT NULL,
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    dataset TEXT NOT NULL,
    is_sharpe REAL,
    oos_sharpe REAL,
    oos_metrics TEXT NOT NULL,
    n_oos_obs INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    params TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issued_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    series_ticker TEXT NOT NULL,
    category TEXT NOT NULL,
    model TEXT NOT NULL,
    p REAL,
    p_low REAL,
    p_high REAL,
    status TEXT NOT NULL,              -- 'forecast' | 'rejected'
    reason TEXT NOT NULL DEFAULT '',
    yes_bid REAL, yes_ask REAL, no_ask REAL,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    decision TEXT NOT NULL DEFAULT '{}',
    close_time TEXT,
    outcome INTEGER,                   -- 1 yes / 0 no once settled
    settled_at TEXT
);
CREATE INDEX IF NOT EXISTS predictions_ticker ON predictions(ticker);
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    family TEXT NOT NULL,
    version TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('champion','challenger','retired')),
    metrics TEXT NOT NULL,
    artifact_path TEXT NOT NULL DEFAULT '',
    UNIQUE (family, version)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn
