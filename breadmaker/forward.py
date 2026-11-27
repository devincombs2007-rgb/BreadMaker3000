"""Forward (live, pre-registered) forecast log on PUBLIC data - no Kalshi data.

Each day: for every configured station, publish P(tomorrow's NWS CLI high >= k) for 9 thresholds,
from the model AND the Gaussian baseline, timestamped BEFORE the outcome exists. Later runs settle
them against the official CLI value. This is the only truly out-of-sample track record: the
forecasts are fixed in the state before anyone (including the code) can see the outcome.

Idempotent: re-running a day does not duplicate forecasts; settlement never changes a forecast.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.stats import norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS fwd (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issued_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    station TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    point_forecast REAL NOT NULL,
    p_model REAL NOT NULL,
    p_gauss REAL NOT NULL,
    n_residuals INTEGER NOT NULL,
    outcome INTEGER,
    observed REAL,
    settled_at TEXT,
    UNIQUE (target_date, station, threshold)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def probabilities(point: float, residuals: np.ndarray, thresholds: list[int]) -> list[tuple[int, float, float]]:
    samples = np.rint(point + residuals)
    mu, sd = float(residuals.mean()), max(float(residuals.std(ddof=1)), 0.5)
    out = []
    for k in thresholds:
        pm = (float(np.sum(samples >= k)) + 0.5) / (len(samples) + 1)
        pg = float(norm.sf((k - 0.5 - (point + mu)) / sd))
        out.append((k, pm, pg))
    return out


def record_forecasts(con: sqlite3.Connection, station: str, target: date, point: float, residuals: np.ndarray,
                     issued_at: datetime) -> int:
    if len(residuals) < 30:
        return 0
    # Exactly ONE forecast set per station and target day: the first published set stands. Re-runs
    # with a newer forecast must not add thresholds (that would let later information in).
    if con.execute("SELECT 1 FROM fwd WHERE station=? AND target_date=? LIMIT 1", (station, target.isoformat())).fetchone():
        return 0
    ks = [int(round(point)) + d for d in range(-4, 5)]
    n = 0
    for k, pm, pg in probabilities(point, residuals, ks):
        cur = con.execute("INSERT OR IGNORE INTO fwd(issued_at,target_date,station,threshold,point_forecast,p_model,"
                          "p_gauss,n_residuals) VALUES (?,?,?,?,?,?,?,?)",
                          (issued_at.isoformat(timespec="seconds"), target.isoformat(), station, k, point, pm, pg,
                           len(residuals)))
        n += cur.rowcount
    return n


def settle(con: sqlite3.Connection, station: str, observed: dict[date, float], now: datetime) -> int:
    n = 0
    for r in con.execute("SELECT DISTINCT target_date FROM fwd WHERE station=? AND outcome IS NULL", (station,)).fetchall():
        d = date.fromisoformat(r[0])
        if d in observed:
            v = observed[d]
            n += con.execute("UPDATE fwd SET observed=?, outcome=(? >= threshold), settled_at=? "
                             "WHERE station=? AND target_date=? AND outcome IS NULL",
                             (v, v, now.isoformat(timespec="seconds"), station, d.isoformat())).rowcount
    return n


def scoreboard(con: sqlite3.Connection) -> dict:
    from .research.tournament import compare
    rows = con.execute("SELECT target_date, p_model, p_gauss, outcome FROM fwd WHERE outcome IS NOT NULL").fetchall()
    pending = con.execute("SELECT COUNT(*) FROM fwd WHERE outcome IS NULL").fetchone()[0]
    out = {"settled": len(rows), "pending": pending,
           "first_target": con.execute("SELECT MIN(target_date) FROM fwd").fetchone()[0]}
    if len(rows) >= 50:
        y = np.array([r[3] for r in rows], float)
        out["tournament"] = compare({"gaussian": np.array([r[2] for r in rows]), "model": np.array([r[1] for r in rows])},
                                    y, np.array([r[0] for r in rows]), baseline="gaussian")
    return out


def run_daily(stations: dict, db_path: Path, now: datetime | None = None) -> dict:
    """Settle what can be settled, then publish forecasts for tomorrow (local standard time)."""
    from .forecasting import feeds
    from .forecasting.weather import STANDARD_TZ, residual_pool
    now = now or datetime.now(timezone.utc)
    con = connect(db_path)
    report = {"issued": 0, "settled": 0, "errors": {}}
    for series, cfg in stations.items():
        st = cfg["station"]
        try:
            std_tz = STANDARD_TZ[cfg["tz"]]
            local_today = (now + timedelta(hours=-int(std_tz.split("+")[1]))).date()
            target = local_today + timedelta(days=1)
            obs = {}
            for y in sorted({(target - timedelta(days=100)).year, target.year}):
                o, _ = feeds.cli_daily_highs(st, y)
                obs.update(o)
            report["settled"] += settle(con, st, obs, now)
            point, _ = feeds.forecast_daily_max(cfg["lat"], cfg["lon"], std_tz, target)
            hist, _ = feeds.previous_day1_daily_max(cfg["lat"], cfg["lon"], std_tz, target - timedelta(days=100),
                                                    local_today - timedelta(days=1))
            if point is None:
                report["errors"][st] = "no forecast for target day"
                continue
            report["issued"] += record_forecasts(con, st, target, point, residual_pool(hist, obs, target), now)
        except Exception as exc:  # noqa: BLE001 - record; never fabricate
            report["errors"][st] = f"{type(exc).__name__}: {str(exc)[:200]}"
    report["scoreboard"] = scoreboard(con)
    return report


# ---- first-release economic data (PR #3 review finding 8) ------------------------------------------
# Kalshi's CPI / jobs markets settle on the FIRST release; the BLS API serves revised values. No free,
# keyless, licence-verified archive of first releases was reachable (ALFRED needs a registered key; the
# Philadelphia Fed real-time set could not be checked). So the daily run RECORDS the first releases
# itself from now on: an append-only log of every distinct value BLS has served for each month.
# The earliest row per (series, period) is the first release, except rows marked backfilled (months
# already published before tracking started, whose first-release value is unknown).
ECON_SCHEMA = """
CREATE TABLE IF NOT EXISTS econ_obs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series TEXT NOT NULL,
    period TEXT NOT NULL,
    value REAL NOT NULL,
    seen_at TEXT NOT NULL,
    backfilled INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS econ_obs_sp ON econ_obs(series, period);
"""
ECON_SERIES = ("CUSR0000SA0", "LNS14000000")


def record_econ(con: sqlite3.Connection, series: dict, now: datetime) -> dict:
    con.executescript(ECON_SCHEMA)
    first_ever = con.execute("SELECT COUNT(*) FROM econ_obs").fetchone()[0] == 0
    new_periods = revisions = 0
    for sid, s in series.items():
        for ts, v in s.items():
            period = f"{ts:%Y-%m}"
            last = con.execute("SELECT value FROM econ_obs WHERE series=? AND period=? ORDER BY id DESC LIMIT 1",
                               (sid, period)).fetchone()
            if last is not None and abs(last[0] - float(v)) < 1e-9:
                continue
            con.execute("INSERT INTO econ_obs(series, period, value, seen_at, backfilled) VALUES (?,?,?,?,?)",
                        (sid, period, float(v), now.isoformat(timespec="seconds"), int(first_ever)))
            if last is None:
                new_periods += 1
            else:
                revisions += 1
    first_releases = con.execute("SELECT COUNT(DISTINCT series || period) FROM econ_obs WHERE backfilled=0").fetchone()[0]
    return {"new_periods": new_periods, "revisions_seen": revisions, "first_releases_tracked": first_releases}


def run_econ_daily(db_path: Path, now: datetime | None = None) -> dict:
    from .forecasting.econ import fetch_bls
    now = now or datetime.now(timezone.utc)
    con = connect(db_path)
    try:
        series, _ = fetch_bls(list(ECON_SERIES), now.year - 1, now.year)
        return record_econ(con, series, now)
    except Exception as exc:  # noqa: BLE001 - record; never fabricate
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
