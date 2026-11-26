"""Experiment registry and strategy laboratory.

Every parameter set ever evaluated is recorded, including failures, so that the
number of trials is known and the Deflated Sharpe Ratio can correct for
multiple-testing / selection bias. Parameter search touches only the
in-sample segment; the holdout is evaluated once per candidate and logged.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..backtest import holdout_split, run_backtest
from ..costs import CostModel
from ..metrics import deflated_sharpe_ratio, returns_from_equity
from ..strategies.baselines import REGISTRY


def dataset_fingerprint(df: pd.DataFrame) -> str:
    h = hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()[:16]
    return f"{df.index[0]}..{df.index[-1]}:{len(df)}:{h}"


@dataclass
class AcceptanceCriteria:
    min_oos_sharpe: float = 0.5
    min_dsr: float = 0.95
    max_oos_drawdown: float = -0.20
    min_oos_trades: int = 10
    must_beat_benchmark_sharpe: bool = True
    cost_stress_multiplier: float = 2.0  # must stay profitable with 2x costs


class Lab:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def n_trials(self, family: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM experiments WHERE family=?", (family,)).fetchone()[0]

    def _sharpe_var(self, family: str) -> float:
        vals = [r[0] for r in self.conn.execute(
            "SELECT is_sharpe FROM experiments WHERE family=? AND is_sharpe IS NOT NULL", (family,))]
        vals = [v for v in vals if v is not None and math.isfinite(v)]
        return float(np.var(vals, ddof=1)) if len(vals) > 1 else 0.0

    def record(self, family, name, params, dataset, is_sharpe, oos_sharpe, oos_metrics, n_oos, status):
        self.conn.execute(
            "INSERT INTO experiments(ts,family,name,params,dataset,is_sharpe,oos_sharpe,oos_metrics,n_oos_obs,status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), family, name, json.dumps(params),
             dataset, _finite(is_sharpe), _finite(oos_sharpe), json.dumps(oos_metrics, default=_jsonable),
             n_oos, status))

    def grid_search(self, family: str, strategy: str, grid: dict[str, list], df: pd.DataFrame,
                    costs: CostModel, periods_per_year: float, criteria: AcceptanceCriteria | None = None,
                    holdout_frac: float = 0.25, initial_capital: float = 1000.0) -> pd.DataFrame:
        """Search on in-sample data, then evaluate every candidate on the untouched holdout.

        Note: periods are annualised per-bar; the per-period Sharpe variance across
        trials is used for the DSR benchmark.
        """
        criteria = criteria or AcceptanceCriteria()
        fn = REGISTRY[strategy]
        is_df, oos_df = holdout_split(df, holdout_frac)
        ds = dataset_fingerprint(df)
        bench_oos = run_backtest(oos_df, REGISTRY["buy_and_hold"](oos_df), costs, initial_capital, periods_per_year)
        rows = []
        keys = list(grid)
        for combo in itertools.product(*(grid[k] for k in keys)):
            params = dict(zip(keys, combo))
            try:
                is_res = run_backtest(is_df, fn(is_df, **params), costs, initial_capital, periods_per_year)
                # Warm-up: compute the signal on full history but only *trade* the OOS window,
                # so indicators have history without leaking future data into the past.
                full_sig = fn(df, **params)
                oos_res = run_backtest(oos_df, full_sig.loc[oos_df.index], costs, initial_capital, periods_per_year)
                stress = run_backtest(oos_df, full_sig.loc[oos_df.index], costs.scaled(criteria.cost_stress_multiplier),
                                      initial_capital, periods_per_year)
                rows.append({"params": params, "is": is_res, "oos": oos_res, "stress": stress})
                self.record(family, strategy, params, ds, is_res.metrics["sharpe"], oos_res.metrics["sharpe"],
                            oos_res.metrics, oos_res.metrics["n_obs"], "evaluated")
            except Exception as exc:  # recorded failures still count as trials
                self.record(family, strategy, params, ds, None, None, {"error": str(exc)}, 0, "error")

        n_trials = self.n_trials(family)
        var = self._sharpe_var(family) / periods_per_year  # per-period variance
        out = []
        for row in rows:
            m = row["oos"].metrics
            per_period = returns_from_equity(row["oos"].equity)
            dsr = deflated_sharpe_ratio(per_period, n_trials, var)
            checks = {
                "oos_sharpe": m["sharpe"] >= criteria.min_oos_sharpe,
                "dsr": (dsr if dsr == dsr else 0) >= criteria.min_dsr,
                "drawdown": m["max_drawdown"] >= criteria.max_oos_drawdown,
                "trades": m.get("n_trades", 0) >= criteria.min_oos_trades,
                "beats_benchmark": (not criteria.must_beat_benchmark_sharpe)
                or m["sharpe"] > bench_oos.metrics["sharpe"],
                "survives_cost_stress": row["stress"].metrics["total_return"] > 0,
            }
            out.append({"strategy": strategy, **{f"p_{k}": v for k, v in row["params"].items()},
                        "is_sharpe": row["is"].metrics["sharpe"], "oos_sharpe": m["sharpe"],
                        "oos_return": m["total_return"], "oos_maxdd": m["max_drawdown"],
                        "oos_trades": m.get("n_trades", 0), "dsr": dsr,
                        "bench_oos_sharpe": bench_oos.metrics["sharpe"],
                        "accepted": all(checks.values()),
                        "failed_checks": ",".join(k for k, v in checks.items() if not v)})
        return pd.DataFrame(out)


def _finite(x):
    try:
        return float(x) if x is not None and math.isfinite(float(x)) else None
    except (TypeError, ValueError):
        return None


def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)
