"""BreadMaker entry gate for Freqtrade strategies (no Freqtrade imports => unit-testable).

FAIL-CLOSED by design. Freqtrade wraps `confirm_trade_entry` with default_retval=True, i.e. an
exception inside it would APPROVE the entry. `entry_allowed` therefore catches everything and
returns (False, reason) on any error, missing file, or unreadable state.

Checks, in order (all read from the shared directory mounted READ-ONLY into the bot):
1. shared directory present          (mount missing -> no entries)
2. global KILL_SWITCH absent          (owner/emergency)
3. HALT_<engine>.json not active      (daily-loss until next UTC midnight; drawdown until owner release)
4. MONITOR_OK_<engine> fresh          (out-of-process risk monitor alive and seeing this bot's equity)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MAX_MONITOR_AGE_SECONDS = 300


def entry_allowed(shared_dir: str | Path, engine: str, now: datetime | None = None,
                  max_monitor_age: float = MAX_MONITOR_AGE_SECONDS) -> tuple[bool, str]:
    try:
        now = now or datetime.now(timezone.utc)
        d = Path(shared_dir)
        if not d.is_dir():
            return False, f"shared risk directory missing: {d}"
        if (d / "KILL_SWITCH").exists():
            return False, "global kill switch engaged"
        halt_file = d / f"HALT_{engine}.json"
        if halt_file.exists():
            h = json.loads(halt_file.read_text())
            until = h.get("until")
            if until is None or now < datetime.fromisoformat(until):
                return False, f"engine halted ({h.get('kind')}): {h.get('reason')}"
        hb = d / f"MONITOR_OK_{engine}"
        if not hb.exists():
            return False, "risk monitor has not confirmed this engine yet"
        age = (now - datetime.fromisoformat(hb.read_text().strip())).total_seconds()
        if age > max_monitor_age:
            return False, f"risk monitor heartbeat stale ({age:.0f}s)"
        if age < -60:
            return False, "risk monitor heartbeat in the future"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - fail closed on ANY error
        return False, f"risk gate error (fail closed): {exc!r}"
