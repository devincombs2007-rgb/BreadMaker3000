"""Convert daily OHLCV (e.g. free vendor CSVs) into LEAN's local equity data format.

LEAN daily equity format (open-source engine docs, "Data Formats"):
  <root>/equity/usa/daily/<sym>.zip  ->  <sym>.csv
  rows: "yyyyMMdd 00:00,open,high,low,close,volume" with prices in deci-cents (x10000).
We write split/dividend-ADJUSTED prices, so the factor file is a constant 1 and the
map file maps the ticker to itself. This avoids needing paid QuantConnect data.

Limitation: survivorship bias - only currently listed tickers are converted.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd


def write_lean_daily(df: pd.DataFrame, symbol: str, root: Path) -> Path:
    sym = symbol.lower()
    d = df.sort_index()
    lines = []
    for ts, r in d.iterrows():
        o, h, l, c = (int(round(float(r[k]) * 10000)) for k in ("open", "high", "low", "close"))
        lines.append(f"{pd.Timestamp(ts):%Y%m%d} 00:00,{o},{h},{l},{c},{int(r['volume'])}")
    out = root / "equity" / "usa" / "daily" / f"{sym}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{sym}.csv", "\n".join(lines) + "\n")
    out.write_bytes(buf.getvalue())
    first = pd.Timestamp(d.index[0])
    (root / "equity" / "usa" / "map_files").mkdir(parents=True, exist_ok=True)
    (root / "equity" / "usa" / "map_files" / f"{sym}.csv").write_text(f"{first:%Y%m%d},{sym}\n20501231,{sym}\n")
    (root / "equity" / "usa" / "factor_files").mkdir(parents=True, exist_ok=True)
    (root / "equity" / "usa" / "factor_files" / f"{sym}.csv").write_text(f"{first:%Y%m%d},1,1,1\n20501231,1,1,0\n")
    return out
