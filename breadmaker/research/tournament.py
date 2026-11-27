"""Model tournament: sophisticated models must beat simple baselines by a statistically
demonstrated margin, or they are rejected.

Paired comparison on the SAME events: d_i = Brier(model)_i - Brier(baseline)_i. Because forecasts
are correlated within blocks (same week/day/month), the confidence interval of mean(d) comes from a
moving/categorical BLOCK bootstrap over block ids, not from i.i.d. resampling.
Accept 'model better' only if the 95% CI upper bound of mean(d) < 0.
"""
from __future__ import annotations

import numpy as np


def block_bootstrap_ci(diff: np.ndarray, blocks: np.ndarray, n_boot: int = 2000, seed: int = 0,
                       level: float = 0.95) -> tuple[float, float, float]:
    diff, blocks = np.asarray(diff, float), np.asarray(blocks)
    ub = np.unique(blocks)
    idx = {b: np.flatnonzero(blocks == b) for b in ub}
    sums = np.array([diff[idx[b]].sum() for b in ub])
    counts = np.array([len(idx[b]) for b in ub])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(ub), size=(n_boot, len(ub)))
    means = sums[draws].sum(1) / counts[draws].sum(1)
    lo, hi = np.quantile(means, [(1 - level) / 2, (1 + level) / 2])
    return float(diff.mean()), float(lo), float(hi)


def compare(preds: dict[str, np.ndarray], y: np.ndarray, blocks: np.ndarray, baseline: str) -> dict:
    """Return per-model Brier and paired CI vs the baseline, with an accept/reject verdict."""
    y = np.asarray(y, float)
    out = {"baseline": baseline, "n": int(len(y)), "n_blocks": int(len(np.unique(blocks))), "models": {}}
    base_b = (np.asarray(preds[baseline], float) - y) ** 2
    for name, p in preds.items():
        b = (np.asarray(p, float) - y) ** 2
        row = {"brier": float(b.mean())}
        if name != baseline:
            mean, lo, hi = block_bootstrap_ci(b - base_b, blocks)
            row.update({"diff_vs_baseline": mean, "ci95": [lo, hi],
                        "verdict": "better" if hi < 0 else "worse" if lo > 0 else "no significant difference"})
        out["models"][name] = row
    return out
