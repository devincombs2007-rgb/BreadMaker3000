"""Leakage-safe ML research pipeline with walk-forward validation,
champion/challenger registry, and drift monitoring.

Guarantees:
* Features at bar t use only data <= t (rolling windows, no centered ops).
* Label = sign of forward return over `horizon` bars; the final `horizon` rows
  (unknown labels) are dropped, and a purge gap of `horizon` separates every
  train/test boundary so overlapping labels cannot leak.
* Scaling/fitting happens inside each training fold only.
* The ML layer only *proposes*; it cannot change risk limits or place orders.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..backtest import run_backtest, walk_forward_splits
from ..costs import CostModel


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    c, v = df["close"], df["volume"]
    r = c.pct_change()
    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 24):
        f[f"ret_{n}"] = c.pct_change(n)
    for n in (12, 24, 72):
        f[f"vol_{n}"] = r.rolling(n).std()
        f[f"dist_sma_{n}"] = c / c.rolling(n).mean() - 1
        f[f"rvol_{n}"] = v / v.rolling(n).mean()
    f["hl_range"] = (df["high"] - df["low"]) / c
    f["vol_ratio"] = f["vol_12"] / f["vol_72"]
    return f


def make_labels(df: pd.DataFrame, horizon: int) -> pd.Series:
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    y = (fwd > 0).astype(float)
    y[fwd.isna()] = np.nan
    return y


def model_factory(name: str, seed: int = 0):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
    if name == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=200, max_depth=5, min_samples_leaf=50, n_jobs=2, random_state=seed)
    if name == "lightgbm":
        import lightgbm as lgb
        return lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=100,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=seed,
                                  verbose=-1, n_jobs=2)
    if name == "xgboost":
        import xgboost as xgb
        return xgb.XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.03, subsample=0.8,
                                 colsample_bytree=0.8, random_state=seed, n_jobs=2)
    if name == "catboost":
        from catboost import CatBoostClassifier
        return CatBoostClassifier(iterations=200, depth=4, learning_rate=0.03, random_seed=seed, verbose=False,
                                  thread_count=2)
    raise ValueError(f"unknown model {name}")


def available_models() -> list[str]:
    out = ["logreg", "random_forest"]
    for name, mod in (("lightgbm", "lightgbm"), ("xgboost", "xgboost"), ("catboost", "catboost")):
        try:
            __import__(mod)
            out.append(name)
        except ImportError:
            pass
    return out


@dataclass
class WalkForwardResult:
    model: str
    auc: float
    accuracy: float
    base_rate: float
    oos_proba: pd.Series
    strategy_metrics: dict
    n_test: int


def walk_forward_evaluate(df: pd.DataFrame, model_name: str, horizon: int = 12, n_splits: int = 5,
                          min_train: int = 1000, threshold: float = 0.55, costs: CostModel | None = None,
                          periods_per_year: float = 24 * 365) -> WalkForwardResult:
    from sklearn.metrics import accuracy_score, roc_auc_score

    X = make_features(df)
    y = make_labels(df, horizon)
    mask = X.notna().all(axis=1) & y.notna()
    Xv, yv = X[mask], y[mask]
    splits = walk_forward_splits(len(Xv), n_splits=n_splits, min_train=min_train, gap=horizon)
    proba = pd.Series(np.nan, index=Xv.index)
    for tr, te in splits:
        m = model_factory(model_name)
        m.fit(Xv.iloc[tr].to_numpy(), yv.iloc[tr].to_numpy())
        proba.iloc[te] = m.predict_proba(Xv.iloc[te].to_numpy())[:, 1]
    tested = proba.dropna()
    yt = yv.loc[tested.index]
    auc = float(roc_auc_score(yt, tested)) if yt.nunique() > 1 else float("nan")
    acc = float(accuracy_score(yt, tested > 0.5))
    # Trade the OOS predictions only (signal at t -> executes t+1 open inside run_backtest).
    oos_df = df.loc[tested.index[0]:tested.index[-1]]
    target = (tested.reindex(oos_df.index) > threshold).astype(float)
    bt = run_backtest(oos_df, target, costs or CostModel(), periods_per_year=periods_per_year)
    return WalkForwardResult(model_name, auc, acc, float(yt.mean()), tested, bt.metrics, len(tested))


# ---- drift -------------------------------------------------------------------
def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index. >0.25 is conventionally significant drift."""
    expected = expected[np.isfinite(expected)]
    actual = actual[np.isfinite(actual)]
    if len(expected) < bins or len(actual) < bins:
        return float("nan")
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def feature_drift(train_X: pd.DataFrame, live_X: pd.DataFrame, threshold: float = 0.25) -> dict:
    scores = {c: psi(train_X[c].to_numpy(float), live_X[c].to_numpy(float)) for c in train_X.columns}
    drifted = [c for c, s in scores.items() if s == s and s > threshold]
    return {"psi": scores, "drifted": drifted, "drift": bool(drifted)}


def performance_decay(recent_auc: float, baseline_auc: float, tolerance: float = 0.03) -> bool:
    """Concept-drift proxy: OOS discrimination fell materially below its validated level."""
    return recent_auc == recent_auc and recent_auc < baseline_auc - tolerance


# ---- champion / challenger registry -----------------------------------------
class ModelRegistry:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def register(self, family: str, version: str, metrics: dict, artifact_path: str = "") -> None:
        self.conn.execute(
            "INSERT INTO models(ts, family, version, role, metrics, artifact_path) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), family, version, "challenger",
             json.dumps(metrics, default=float), artifact_path))

    def champion(self, family: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM models WHERE family=? AND role='champion'", (family,)).fetchone()
        return dict(r) | {"metrics": json.loads(r["metrics"])} if r else None

    def promote_if_better(self, family: str, version: str, key: str = "sharpe", min_improvement: float = 0.1,
                          min_n: int = 500) -> tuple[bool, str]:
        """Promote a challenger only with sufficient OOS sample AND a material improvement."""
        r = self.conn.execute("SELECT * FROM models WHERE family=? AND version=?", (family, version)).fetchone()
        if r is None:
            return False, "unknown version"
        m = json.loads(r["metrics"])
        if m.get("n_test", 0) < min_n:
            return False, f"insufficient OOS sample ({m.get('n_test', 0)} < {min_n})"
        if not (m.get(key, float("nan")) > 0):
            return False, f"challenger {key} not positive"
        champ = self.champion(family)
        if champ and not (m[key] >= champ["metrics"].get(key, float("-inf")) + min_improvement):
            return False, f"challenger {key}={m[key]:.3f} does not beat champion by {min_improvement}"
        self.conn.execute("UPDATE models SET role='retired' WHERE family=? AND role='champion'", (family,))
        self.conn.execute("UPDATE models SET role='champion' WHERE family=? AND version=?", (family, version))
        return True, "promoted"

    def rollback(self, family: str) -> tuple[bool, str]:
        """Restore the most recently retired model as champion."""
        prev = self.conn.execute(
            "SELECT version FROM models WHERE family=? AND role='retired' ORDER BY id DESC LIMIT 1", (family,)).fetchone()
        if prev is None:
            return False, "no previous champion"
        self.conn.execute("UPDATE models SET role='retired' WHERE family=? AND role='champion'", (family,))
        self.conn.execute("UPDATE models SET role='champion' WHERE family=? AND version=?", (family, prev[0]))
        return True, f"rolled back to {prev[0]}"
