from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def beat(conn: sqlite3.Connection, worker: str, status: str = "running", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO heartbeats(worker, ts, status, detail) VALUES (?,?,?,?) "
        "ON CONFLICT(worker) DO UPDATE SET ts=excluded.ts, status=excluded.status, detail=excluded.detail",
        (worker, datetime.now(timezone.utc).isoformat(timespec="seconds"), status, detail),
    )


def worker_status(conn: sqlite3.Connection, timeout_seconds: int, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for r in conn.execute("SELECT worker, ts, status, detail FROM heartbeats ORDER BY worker"):
        age = (now - datetime.fromisoformat(r["ts"])).total_seconds()
        status = r["status"] if age <= timeout_seconds else "stale"
        out.append({"worker": r["worker"], "last_seen": r["ts"], "age_seconds": int(age),
                    "status": status, "detail": r["detail"]})
    return out
