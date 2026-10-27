"""When the trade.xyz oracle is externally derived (per exchange session) vs following the perp.

Source: trade.xyz Specification Index, read 2026-10-22. Window ends are exclusive.
Asian exchange holidays are not modelled (ponytail: a holiday reads external with a stale oracle).
"""

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from venue_arb.sources.hyperliquid import Label

_ET = ZoneInfo("America/New_York")
_US = {"NASDAQ", "NYSE", "NYSE ARCA", "CBOE BZX"}

# NYSE full-day closures, as observed. Covers 2026-2027 only: extend past 2027 or every later holiday reads external.
US_HOLIDAYS = frozenset(
    date(*d)
    for d in [
        (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 4, 3), (2026, 5, 25),
        (2026, 6, 19), (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 12, 25),
        (2027, 1, 1), (2027, 1, 18), (2027, 2, 15), (2027, 3, 26), (2027, 5, 31),
        (2027, 6, 18), (2027, 7, 5), (2027, 9, 6), (2027, 11, 25), (2027, 12, 24),
    ]
)  # fmt: skip

# exchange -> (zone, Mon-Fri local windows [start, end))
_ASIA = {
    "KRX": ("Asia/Seoul", [(time(9, 1), time(15, 20)), (time(16, 5), time(20, 0))]),
    "TSE": ("Asia/Tokyo", [(time(8, 20), time(16, 30)), (time(17, 0), time(23, 59))]),
    "HKEX": ("Asia/Hong_Kong", [(time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))]),
    "SSE": ("Asia/Shanghai", [(time(9, 30), time(11, 30)), (time(13, 0), time(14, 57))]),
    "SZSE": ("Asia/Shanghai", [(time(9, 30), time(11, 30)), (time(13, 0), time(14, 57))]),
    # NSE: an assumption, not from the trade.xyz index (which does not list it): the regular session 09:15-15:30 IST,
    # no pre-open or closing session; docs/venues.md "Oracle and sessions"
    "NSE": ("Asia/Kolkata", [(time(9, 15), time(15, 30))]),
}


def _us_external(ts: datetime) -> bool:
    t = ts.astimezone(_ET)
    if t.date() in US_HOLIDAYS:
        return False
    wd, tt = t.weekday(), t.time()
    if wd == 5:
        return False
    if wd == 6:
        return tt >= time(20, 5)
    if wd == 4:
        return tt < time(20, 0)
    return True


def external(label: Label, ts: datetime) -> bool:
    if label.kind == "pre_ipo":
        return False
    ex = label.exchange.strip().upper()
    if ex in _US:
        return _us_external(ts)
    if ex in _ASIA:
        zone, windows = _ASIA[ex]
        t = ts.astimezone(ZoneInfo(zone))
        return t.weekday() < 5 and any(a <= t.time() < b for a, b in windows)
    return False


def state(label: Label, ts: datetime) -> Literal["external", "internal", "pre_ipo"]:
    if label.kind == "pre_ipo":
        return "pre_ipo"
    return "external" if external(label, ts) else "internal"
