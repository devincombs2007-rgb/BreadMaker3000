"""Tests LEAN algorithm decision logic without the LEAN engine (AlgorithmImports stubbed)."""
import importlib.util
import sys
import types

import numpy as np
import pandas as pd

from conftest import ROOT


def load_lean_module():
    stub = types.ModuleType("AlgorithmImports")
    stub.QCAlgorithm = object
    sys.modules["AlgorithmImports"] = stub
    spec = importlib.util.spec_from_file_location("lean_main", ROOT / "engines/lean/BreadLeanTrend/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lean_weights_match_lumibot_logic():
    """Cross-engine consistency: LEAN and LumiBot implementations select the same ETFs and weights."""
    from bread_lumibot.logic import Params, select_targets
    lean = load_lean_module()
    rng = np.random.default_rng(1)
    n = 300
    closes = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(mu, 0.006, n)))
                           for s, mu in {"A": 0.003, "B": 0.002, "C": -0.002, "D": 0.001, "E": 0.0025}.items()})
    w_lean = lean.choose_weights({c: list(closes[c]) for c in closes})
    t = select_targets(closes, 1.0, 0.10, 0.005, Params())
    w_lb = {x.symbol: x.target_value for x in t}
    assert set(w_lean) == set(w_lb)
    for s in w_lean:
        assert abs(w_lean[s] - w_lb[s]) < 1e-9
        assert w_lean[s] <= 0.10


def test_lean_daily_data_format(tmp_path):
    import zipfile
    from breadmaker.integrations.lean_data import write_lean_daily
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    df = pd.DataFrame({"open": [470.5, 471], "high": [472, 473], "low": [469, 470], "close": [471.25, 472],
                       "volume": [1000, 2000]}, index=idx)
    p = write_lean_daily(df, "SPY", tmp_path)
    assert p == tmp_path / "equity/usa/daily/spy.zip"
    rows = zipfile.ZipFile(p).read("spy.csv").decode().splitlines()
    assert rows[0] == "20240102 00:00,4705000,4720000,4690000,4712500,1000"
    assert (tmp_path / "equity/usa/map_files/spy.csv").exists()
    assert (tmp_path / "equity/usa/factor_files/spy.csv").exists()
