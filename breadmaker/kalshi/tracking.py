"""Prediction log, settlement, accuracy/calibration metrics and leakage-safe recalibration."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..forecasting.base import Forecast, Rejection
from .decision import Decision
from .markets import Market, Quote


def log_prediction(conn: sqlite3.Connection, m: Market, result: Forecast | Rejection, quote: Quote | None,
                   decision: Decision | None, issued_at: datetime) -> int:
    is_fc = isinstance(result, Forecast)
    cur = conn.execute(
        "INSERT INTO predictions(issued_at,ticker,event_ticker,series_ticker,category,model,p,p_low,p_high,status,"
        "reason,yes_bid,yes_ask,no_ask,evidence_ids,decision,close_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (issued_at.isoformat(timespec="seconds"), m.ticker, m.event_ticker, m.series_ticker, m.category, result.model,
         result.p if is_fc else None, result.p_low if is_fc else None, result.p_high if is_fc else None,
         "forecast" if is_fc else "rejected", "" if is_fc else result.reason,
         quote.yes_bid if quote else None, quote.yes_ask if quote else None, quote.no_ask if quote else None,
         json.dumps(result.evidence_ids if is_fc else []), json.dumps(decision.to_dict() if decision else {}),
         m.close_time.isoformat() if m.close_time else None))
    return int(cur.lastrowid)


def record_settlement(conn: sqlite3.Connection, ticker: str, result: str, settled_at: datetime) -> int:
    if result not in ("yes", "no"):
        return 0
    cur = conn.execute("UPDATE predictions SET outcome=?, settled_at=? WHERE ticker=? AND outcome IS NULL",
                       (1 if result == "yes" else 0, settled_at.isoformat(timespec="seconds"), ticker))
    return cur.rowcount


def settled_records(conn: sqlite3.Connection, model: str | None = None) -> list[dict]:
    q = "SELECT * FROM predictions WHERE status='forecast' AND outcome IS NOT NULL"
    args: tuple = ()
    if model:
        q += " AND model=?"
        args = (model,)
    return [dict(r) for r in conn.execute(q + " ORDER BY issued_at", args)]


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def log_loss(p, y, eps: float = 1e-4) -> float:
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else float("nan")


def calibration_table(p, y, bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        if m.any():
            rows.append({"bin": f"{edges[i]:.1f}-{edges[i + 1]:.1f}", "n": int(m.sum()),
                         "mean_p": round(float(p[m].mean()), 3), "freq": round(float(y[m].mean()), 3)})
    return rows


def expected_calibration_error(p, y, bins: int = 10) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    if not len(p):
        return float("nan")
    return float(sum(r["n"] * abs(r["mean_p"] - r["freq"]) for r in calibration_table(p, y, bins)) / len(p))


def metrics(p, y) -> dict:
    return {"n": int(len(p)), "brier": brier(p, y), "log_loss": log_loss(p, y),
            "ece": expected_calibration_error(p, y), "base_rate": float(np.mean(y)) if len(y) else float("nan")}


# ---- learning from errors without leakage ------------------------------------------------
@dataclass
class Recalibrator:
    a: float = 0.0
    b: float = 1.0

    def apply(self, p: float) -> float:
        p = min(max(p, 1e-4), 1 - 1e-4)
        z = self.a + self.b * math.log(p / (1 - p))
        return 1 / (1 + math.exp(-z))


def fit_recalibrator(p, y, l2: float = 1.0) -> Recalibrator:
    """Platt-style logistic recalibration on logit(p), ridge-shrunk towards identity (a=0,b=1)."""
    from scipy.optimize import minimize

    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    x = np.log(p / (1 - p))

    def objective(w):
        a, b = w
        z = a + b * x
        # numerically stable negative log-likelihood + ridge towards identity (a=0, b=1)
        nll = np.sum(np.logaddexp(0, z) - y * z)
        q = 1 / (1 + np.exp(-z))
        grad = np.array([np.sum(q - y) + l2 * a, np.sum((q - y) * x) + l2 * (b - 1)])
        return nll + 0.5 * l2 * (a * a + (b - 1) ** 2), grad

    res = minimize(objective, np.array([0.0, 1.0]), jac=True, method="L-BFGS-B")
    a, b = res.x
    return Recalibrator(float(a), float(b))


def walk_forward_recalibration(records: list[dict], min_train: int = 50) -> dict:
    """For each prediction (in issue order), fit only on predictions whose SETTLEMENT happened before
    that prediction was ISSUED. Compare held-out Brier of raw vs recalibrated probabilities.
    Recommend recalibration only if it improves held-out Brier on >= min_train evaluated points."""
    recs = sorted(records, key=lambda r: r["issued_at"])
    raw, cal, ys, days = [], [], [], []
    for r in recs:
        train = [t for t in recs if t["settled_at"] and t["settled_at"] < r["issued_at"]]
        if len(train) < min_train:
            continue
        rc = fit_recalibrator([t["p"] for t in train], [t["outcome"] for t in train])
        raw.append(r["p"])
        cal.append(rc.apply(r["p"]))
        ys.append(r["outcome"])
        days.append(r["issued_at"][:10])
    if len(ys) < min_train:
        return {"evaluated": len(ys), "use_recalibration": False, "reason": "insufficient settled history"}
    from ..research.tournament import block_bootstrap_ci
    raw_a, cal_a, y_a = np.asarray(raw), np.asarray(cal), np.asarray(ys, float)
    mean, lo, hi = block_bootstrap_ci((cal_a - y_a) ** 2 - (raw_a - y_a) ** 2, np.asarray(days))
    b_raw, b_cal = brier(raw, ys), brier(cal, ys)
    # Overfitting guard: adopt only if the improvement is statistically significant (CI entirely < 0).
    return {"evaluated": len(ys), "brier_raw": b_raw, "brier_recalibrated": b_cal, "diff_ci95": [lo, hi],
            "use_recalibration": hi < 0, "reason": "held-out comparison with block-bootstrap CI"}


def learned_adjustment(conn: sqlite3.Connection, model: str, now: datetime, min_train: int = 50):
    """Recalibrator for `model` fitted on predictions settled before `now`, or None unless a walk-forward,
    significance-tested comparison shows it improves held-out accuracy (no leakage, overfitting guard)."""
    recs = [r for r in settled_records(conn, model) if r["settled_at"] and r["settled_at"] < now.isoformat()]
    verdict = walk_forward_recalibration(recs, min_train=min_train)
    if not verdict.get("use_recalibration"):
        return None, verdict
    return fit_recalibrator([r["p"] for r in recs], [r["outcome"] for r in recs]), verdict


def rankings(conn: sqlite3.Connection, hours: float = 24, now: datetime | None = None, limit: int = 50) -> dict:
    """Latest prediction per market within `hours`, ranked for the dashboard.

    allocations  - simulated buys (by expected value)
    candidates   - forecasts without a buy (by conservative edge after fees), with the reason
    rejections   - markets not forecast, grouped by reason
    Each forecast carries its evidence sources and fetch times."""
    from datetime import timedelta, timezone as _tz
    now = now or datetime.now(_tz.utc)
    since = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = [dict(r) for r in conn.execute(
        "SELECT p.* FROM predictions p JOIN (SELECT ticker, MAX(id) mid FROM predictions WHERE issued_at >= ? "
        "GROUP BY ticker) l ON p.id = l.mid", (since,))]
    alloc, cand, rej = [], [], {}
    for r in rows:
        if r["status"] == "rejected":
            rej.setdefault(r["reason"] or "unspecified", []).append(r["ticker"])
            continue
        d = json.loads(r["decision"] or "{}")
        ev = []
        for eid in json.loads(r["evidence_ids"] or "[]"):
            e = conn.execute("SELECT source, fetched_at FROM evidence WHERE id=?", (eid,)).fetchone()
            if e:
                ev.append({"source": e[0], "fetched_at": e[1]})
        item = {"ticker": r["ticker"], "model": r["model"], "category": r["category"], "p": r["p"],
                "interval": [r["p_low"], r["p_high"]], "yes_ask": r["yes_ask"], "no_ask": r["no_ask"],
                "edge_after_fees": d.get("edge_per_contract"), "side": d.get("side"), "contracts": d.get("contracts", 0),
                "price": d.get("price"), "expected_value": d.get("expected_value"), "reason": d.get("reason", ""),
                "evidence": ev, "issued_at": r["issued_at"], "close_time": r["close_time"]}
        (alloc if d.get("side") else cand).append(item)
    alloc.sort(key=lambda x: -(x["expected_value"] or 0))
    cand.sort(key=lambda x: -(x["edge_after_fees"] if x["edge_after_fees"] is not None else -9))
    return {"window_hours": hours, "allocations": alloc[:limit], "candidates": cand[:limit],
            "rejections": sorted(({"reason": k, "count": len(v), "examples": v[:3]} for k, v in rej.items()),
                                 key=lambda x: -x["count"])}


def rankings_markdown(rk: dict) -> str:
    """Phone-friendly Markdown (lists, no wide tables)."""
    def pct(x):
        return "–" if x is None else f"{x:.0%}"
    L = [f"## Market rankings (last {rk['window_hours']:.0f} h, simulated)", "", "### Simulated allocations"]
    L += [f"- **{a['ticker']}**: BUY {a['contracts']} {a['side'].upper()} @ {a['price']:.2f} · model {pct(a['p'])} "
          f"({pct(a['interval'][0])}–{pct(a['interval'][1])}) · edge {a['edge_after_fees']:+.3f} · EV ${a['expected_value']:+.2f}"
          for a in rk["allocations"]] or ["- None (zero allocation)"]
    L += ["", "### Best candidates not bought"]
    L += [f"- {c['ticker']}: model {pct(c['p'])} ({pct(c['interval'][0])}–{pct(c['interval'][1])}) · yes ask "
          f"{pct(c['yes_ask'])} · edge {('–' if c['edge_after_fees'] is None else format(c['edge_after_fees'], '+.3f'))} · {c['reason']}"
          for c in rk["candidates"][:10]] or ["- None"]
    L += ["", "### Rejected (no reliable model or data)"]
    L += [f"- {r['count']} × {r['reason']}" for r in rk["rejections"][:10]] or ["- None"]
    return "\n".join(L) + "\n"
