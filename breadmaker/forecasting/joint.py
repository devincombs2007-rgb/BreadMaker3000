"""Joint probabilities for combinations / parlays with correlated legs.

A parlay pays only if ALL legs resolve YES. Treating legs as independent (product of marginals) is
wrong when outcomes are correlated (e.g. NYC and Philadelphia highs on the same day; a team winning
and the game going over). Three estimators:
  * independent      - product of marginals (reference only);
  * gaussian_copula  - marginals joined by a latent multivariate normal with correlation matrix R;
  * empirical        - joint frequency over historical residual VECTORS from the same dates
                       (an empirical copula; preserves real co-movement and tail dependence).
Uncertainty: an interval from perturbing marginals within their intervals and R within a band.
"""
from __future__ import annotations

import itertools

import numpy as np
from scipy.stats import multivariate_normal, norm


def independent(p: list[float]) -> float:
    return float(np.prod(p))


def gaussian_copula(p: list[float], R: np.ndarray) -> float:
    """P(all legs YES): leg i YES <=> Z_i <= Phi^{-1}(p_i), Z ~ N(0, R)."""
    p = np.clip(np.asarray(p, float), 1e-9, 1 - 1e-9)
    if len(p) == 1:
        return float(p[0])
    z = norm.ppf(p)
    return float(multivariate_normal(mean=np.zeros(len(p)), cov=R, allow_singular=True).cdf(z))


def latent_correlation_from_residuals(residuals: np.ndarray) -> np.ndarray:
    """Spearman-rank estimate of the latent normal correlation (robust): R = 2 sin(pi rho_s / 6),
    projected to the nearest valid correlation matrix."""
    import pandas as pd
    rho_s = pd.DataFrame(np.asarray(residuals, float)).corr(method="spearman").to_numpy()
    R = 2 * np.sin(np.pi * rho_s / 6)
    np.fill_diagonal(R, 1.0)
    w, v = np.linalg.eigh(R)
    R = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
    d = np.sqrt(np.diag(R))
    return R / np.outer(d, d)


def empirical_joint(point_forecasts: list[float], residual_vectors: np.ndarray, conditions) -> tuple[float, int]:
    """Fraction of historical same-date residual vectors r for which ALL legs hold when applied to
    today's point forecasts; `conditions[i](value) -> bool`. Jeffreys-smoothed. Returns (p, n)."""
    rv = np.asarray(residual_vectors, float)
    rv = rv[np.all(np.isfinite(rv), axis=1)]
    if len(rv) == 0:
        return float("nan"), 0
    ok = np.ones(len(rv), bool)
    for i, (f, cond) in enumerate(zip(point_forecasts, conditions)):
        ok &= np.array([cond(float(np.rint(f + x))) for x in rv[:, i]])
    k, n = ok.sum(), len(rv)
    return float((k + 0.5) / (n + 1)), int(n)


def interval(p: list[float], p_low: list[float], p_high: list[float], R: np.ndarray,
             rho_band: float = 0.15) -> tuple[float, float, float]:
    """Conservative interval for the copula joint: corners of the marginal box x {R - band, R + band}.
    Limited to <= 6 legs (2^k corners)."""
    if len(p) > 6:
        raise ValueError("parlays with more than 6 legs are not modelled")
    mid = gaussian_copula(p, R)
    vals = [mid]
    off = R - np.eye(len(p))
    for shift in (-rho_band, rho_band):
        Rs = np.clip(off + np.sign(off) * shift, -0.99, 0.99) + np.eye(len(p))
        w, v = np.linalg.eigh(Rs)
        Rs = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
        d = np.sqrt(np.diag(Rs))
        Rs = Rs / np.outer(d, d)
        for corner in itertools.product(*zip(p_low, p_high)):
            vals.append(gaussian_copula(list(corner), Rs))
    return float(mid), float(min(vals)), float(max(vals))
