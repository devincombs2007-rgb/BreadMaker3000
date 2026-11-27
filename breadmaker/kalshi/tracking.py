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
    raw, cal, ys = [], [], []
    for r in recs:
        train = [t for t in recs if t["settled_at"] and t["settled_at"] < r["issued_at"]]
        if len(train) < min_train:
            continue
        rc = fit_recalibrator([t["p"] for t in train], [t["outcome"] for t in train])
        raw.append(r["p"])
        cal.append(rc.apply(r["p"]))
        ys.append(r["outcome"])
    if len(ys) < min_train:
        return {"evaluated": len(ys), "use_recalibration": False, "reason": "insufficient settled history"}
    b_raw, b_cal = brier(raw, ys), brier(cal, ys)
    return {"evaluated": len(ys), "brier_raw": b_raw, "brier_recalibrated": b_cal,
            "use_recalibration": b_cal < b_raw, "reason": "held-out comparison"}
