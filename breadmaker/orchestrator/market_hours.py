"""NYSE regular-session calendar (holidays per NYSE rules; early closes at 13:00 ET).

Self-contained so the orchestrator needs no extra dependency. LumiBot uses its
own calendar for execution; this one gates BreadMaker scheduling/monitoring.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, m, weekday):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(y: int) -> frozenset[date]:
    h = {
        _nth_weekday(y, 1, 0, 3),               # MLK day
        _nth_weekday(y, 2, 0, 3),               # Presidents day
        _easter(y) - timedelta(days=2),         # Good Friday
        _last_weekday(y, 5, 0),                 # Memorial day
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),               # Labor day
        _nth_weekday(y, 11, 3, 4),              # Thanksgiving
        _observed(date(y, 12, 25)),
    }
    ny = date(y, 1, 1)
    if ny.weekday() != 5:                       # NYSE does not observe Sat New Year on prior Friday
        h.add(_observed(ny))
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))        # Juneteenth
    return frozenset(h)


def early_close(d: date) -> bool:
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24 and d.weekday() < 5:
        return True
    if d.month == 7 and d.day == 3 and d.weekday() < 5 and date(d.year, 7, 4).weekday() != 0:
        return True
    return False


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def is_market_open(now: datetime) -> bool:
    et = now.astimezone(ET)
    if not is_trading_day(et.date()):
        return False
    close = time(13, 0) if early_close(et.date()) else time(16, 0)
    return time(9, 30) <= et.time() < close
