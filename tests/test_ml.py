import numpy as np
import pandas as pd
import pytest

from breadmaker.data import synthetic_ohlcv
from breadmaker.ml.pipeline import (ModelRegistry, available_models, feature_drift, make_features,
                                    make_labels, performance_decay, psi, walk_forward_evaluate)

pytest.importorskip("sklearn")


@pytest.fixture(scope="module")
def df():
    return synthetic_ohlcv(n=2500, seed=5)


def test_features_are_causal(df):
    """Features at t must not change when the future is altered."""
    a = make_features(df)
    df2 = df.copy()
    df2.iloc[1500:, :5] *= 2
    b = make_features(df2)
    pd.testing.assert_frame_equal(a.iloc[:1500], b.iloc[:1500])


def test_labels_nan_at_end(df):
    y = make_labels(df, 12)
    assert y.iloc[-12:].isna().all() and y.iloc[:-12].notna().all()


@pytest.mark.parametrize("model", ["logreg", "random_forest"] + (["lightgbm"] if "lightgbm" in available_models() else []))
def test_walk_forward_runs_without_skill_on_random_walk(df, model):
    r = walk_forward_evaluate(df, model, horizon=12, n_splits=3, min_train=1000)
    assert r.n_test > 500
    # Synthetic data has no exploitable structure beyond regime drift; AUC should be near 0.5.
    # A very high AUC here would indicate leakage.
    assert 0.35 < r.auc < 0.65


def test_label_leak_would_be_detected(df):
    """Sanity check of the leakage test itself: leaking the label gives a near-perfect AUC."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    X = make_features(df)
    y = make_labels(df, 12)
    X["leak"] = df["close"].shift(-12) / df["close"] - 1
    m = X.notna().all(axis=1) & y.notna()
    X, y = X[m], y[m]
    clf = LogisticRegression(max_iter=1000).fit(X.iloc[:1500], y.iloc[:1500])
    assert roc_auc_score(y.iloc[1500:], clf.predict_proba(X.iloc[1500:])[:, 1]) > 0.9


def test_psi_drift():
    rng = np.random.default_rng(0)
    assert psi(rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)) < 0.05
    assert psi(rng.normal(0, 1, 5000), rng.normal(2, 1, 5000)) > 0.25
    d = feature_drift(pd.DataFrame({"a": rng.normal(0, 1, 2000)}), pd.DataFrame({"a": rng.normal(3, 1, 2000)}))
    assert d["drift"] and d["drifted"] == ["a"]
    assert performance_decay(0.50, 0.56) and not performance_decay(0.55, 0.56)


def test_champion_challenger_and_rollback(conn):
    reg = ModelRegistry(conn)
    reg.register("btc", "v1", {"sharpe": 0.8, "n_test": 1000})
    assert reg.promote_if_better("btc", "v1") == (True, "promoted")
    reg.register("btc", "v2", {"sharpe": 0.85, "n_test": 1000})
    ok, why = reg.promote_if_better("btc", "v2")
    assert not ok and "does not beat" in why
    reg.register("btc", "v3", {"sharpe": 1.2, "n_test": 100})
    assert not reg.promote_if_better("btc", "v3")[0]  # insufficient sample
    reg.register("btc", "v4", {"sharpe": 1.2, "n_test": 2000})
    assert reg.promote_if_better("btc", "v4")[0]
    assert reg.champion("btc")["version"] == "v4"
    assert reg.rollback("btc")[0] and reg.champion("btc")["version"] == "v1"
