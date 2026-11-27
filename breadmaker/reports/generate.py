"""Daily / weekly Markdown reports. Losses are reported with the same prominence as gains."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ..ledger import Ledger
from ..metrics import max_drawdown, sharpe, sortino

BANNER = "> **SIMULATED RESULTS ONLY** - paper/dry-run trading with virtual capital. Not real money. Past or simulated performance does not predict future results."


def _period_return(hist: list[tuple[str, float]], since: datetime) -> float | None:
    if not hist:
        return None
    s = pd.Series({pd.Timestamp(t): e for t, e in hist}).sort_index()
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    before = s[s.index <= pd.Timestamp(since)]
    base = before.iloc[-1] if len(before) else s.iloc[0]
    return float(s.iloc[-1] / base - 1)


def build_report(conn: sqlite3.Connection, period: str = "daily", now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    since = now - (timedelta(days=1) if period == "daily" else timedelta(days=7))
    ledger = Ledger(conn)
    lines = [f"# BreadMaker3000 {period} report - {now:%Y-%m-%d %H:%M} UTC", "", BANNER, ""]
    lines += ["## Engines", "", "| Engine | Equity | Period return | Total P&L | Realized (net) | Unrealized | Fees | Max DD | Sharpe* |",
              "|---|---|---|---|---|---|---|---|---|"]
    total_eq = total_init = 0.0
    for eng in ledger.engines():
        s = ledger.summary(eng)  # marks unavailable offline -> cost basis (flagged below)
        hist = ledger.equity_history(eng)
        pr = _period_return(hist, since)
        eq = pd.Series([e for _, e in hist]) if hist else pd.Series(dtype=float)
        dd = max_drawdown(eq) if len(eq) else float("nan")
        sh = sharpe(eq.pct_change().dropna(), 365) if len(eq) > 30 else float("nan")
        total_eq += s["equity"]
        total_init += s["initial_capital"]
        sign = "LOSS " if s["total_pnl"] < 0 else ""
        lines.append(f"| {eng} | ${s['equity']:,.2f} | {'' if pr is None else f'{pr:+.2%}'} | {sign}${s['total_pnl']:+,.2f} "
                     f"| ${s['realized_pnl_net_of_fees']:+,.2f} | ${s['unrealized_pnl']:+,.2f} | ${s['fees_paid']:,.2f} "
                     f"| {dd:.2%} | {sh:.2f} |")
    lines += ["", f"**Combined virtual equity:** ${total_eq:,.2f} of ${total_init:,.2f} initial "
              f"({(total_eq / total_init - 1) if total_init else 0:+.2%}).", "",
              "*Sharpe shown only with >30 snapshots; small samples are not statistically meaningful.", ""]

    ev = conn.execute("SELECT ts, engine, severity, rule, detail FROM risk_events WHERE ts >= ? ORDER BY id",
                      (since.isoformat(timespec="seconds"),)).fetchall()
    lines += ["## Risk incidents", ""]
    lines += [f"- `{r['ts']}` **{r['severity']}** [{r['engine']}] {r['rule']}: {r['detail']}" for r in ev] or ["- None"]
    models = conn.execute("SELECT ts, family, version, role FROM models WHERE ts >= ?",
                          (since.isoformat(timespec="seconds"),)).fetchall()
    lines += ["", "## Model changes", ""]
    lines += [f"- {r['ts']} {r['family']} {r['version']} -> {r['role']}" for r in models] or ["- None"]
    exps = conn.execute("SELECT family, COUNT(*) n, SUM(status='evaluated') ok FROM experiments GROUP BY family").fetchall()
    lines += ["", "## Research (all-time trial counts, for multiple-testing context)", ""]
    lines += [f"- {r['family']}: {r['n']} trials ({r['ok']} evaluated)" for r in exps] or ["- No experiments yet"]
    lines += ["", "## Current limitations", "",
              "- Forward paper-trading track record is shorter than the 90-day minimum; no conclusions on profitability.",
              "- Simulated fills may be more favourable than real fills (queue position, partial fills).", ""]
    return "\n".join(lines)


def write_report(conn: sqlite3.Connection, out_dir: Path, period: str = "daily") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    p = out_dir / f"{now:%Y-%m-%d}-{period}.md"
    p.write_text(build_report(conn, period, now))
    return p
