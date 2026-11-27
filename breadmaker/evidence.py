"""Timestamped evidence store. Every external datum used by a forecast is recorded with its source,
URL, request parameters, fetch time (UTC) and a SHA-256 of the raw payload, so any prediction can be
traced to exactly what was known at the time. Raw payloads are not stored (only hashes + summaries)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class EvidenceStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(self, source: str, url: str, params: dict, payload: bytes, summary: dict,
               fetched_at: datetime | None = None) -> int:
        fetched_at = fetched_at or datetime.now(timezone.utc)
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        cur = self.conn.execute(
            "INSERT INTO evidence(fetched_at, source, url, params, sha256, summary) VALUES (?,?,?,?,?,?)",
            (fetched_at.isoformat(timespec="seconds"), source, url, json.dumps(params, sort_keys=True),
             sha256_bytes(payload), json.dumps(summary, default=str)))
        return int(cur.lastrowid)

    def record_provenance(self, prov: dict, summary: dict) -> int:
        """Store a feeds.* provenance dict (already carries sha256 of the raw response and fetch time)."""
        if prov["fetched_at"].tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        cur = self.conn.execute(
            "INSERT INTO evidence(fetched_at, source, url, params, sha256, summary) VALUES (?,?,?,?,?,?)",
            (prov["fetched_at"].isoformat(timespec="seconds"), prov["source"], prov["url"],
             json.dumps(prov["params"], sort_keys=True, default=str), prov["sha256"], json.dumps(summary, default=str)))
        return int(cur.lastrowid)

    def get(self, evidence_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        return dict(r) if r else None

    def available_at(self, evidence_ids: list[int], decision_time: datetime) -> bool:
        """True only if every piece of evidence was fetched no later than the decision time (no look-ahead)."""
        for i in evidence_ids:
            e = self.get(i)
            if e is None or datetime.fromisoformat(e["fetched_at"]) > decision_time:
                return False
        return True
