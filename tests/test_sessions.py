from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from venue_arb import sessions as s
from venue_arb.sources.hyperliquid import Label


def _aware(tz, *a):
    return datetime(*a, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def et(*a):
    return _aware("America/New_York", *a)


def kst(*a):
    return _aware("Asia/Seoul", *a)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


NVDA = Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")


def test_us_external_window():
    assert s.external(NVDA, et(2026, 10, 21, 2, 0))
    assert not s.external(NVDA, et(2026, 10, 23, 20, 0))
    assert not s.external(NVDA, et(2026, 10, 24, 12, 0))  # Saturday
    assert not s.external(NVDA, et(2026, 10, 25, 20, 4))
    assert s.external(NVDA, et(2026, 10, 25, 20, 5))
    assert s.state(NVDA, et(2026, 10, 21, 2, 0)) == "external"
    assert s.state(NVDA, et(2026, 10, 24, 12, 0)) == "internal"


def test_us_exchange_case_insensitive():
    arca = Label("xyz:SPY", "schwab", "SPY", "NYSE Arca", "USD", "SPY")
    assert s.external(arca, et(2026, 10, 21, 11, 0))


def test_us_holiday_is_internal():
    assert not s.external(NVDA, et(2026, 11, 26, 11, 0))
    assert not s.external(NVDA, et(2026, 11, 26, 0, 0))
    assert not s.external(NVDA, et(2026, 11, 26, 23, 59))
    assert s.external(NVDA, et(2026, 11, 27, 0, 0))


def test_krx_session_is_its_own():
    skhx = Label("xyz:SKHX", "none", "000660", "KRX", "KRW", "SK hynix")
    assert s.external(skhx, kst(2026, 10, 22, 10, 0))
    assert not s.external(skhx, kst(2026, 10, 22, 15, 40))
    assert not s.external(skhx, kst(2026, 10, 22, 9, 0))
    assert s.external(skhx, kst(2026, 10, 22, 9, 1))
    assert not s.external(skhx, kst(2026, 10, 22, 20, 0))
    assert not s.external(skhx, kst(2026, 10, 24, 10, 0))  # Saturday
    assert not s.external(skhx, et(2026, 10, 21, 11, 0))


def test_other_asian_exchanges():
    def lab(ex):
        return Label("xyz:X", "none", "", ex, "", "X")

    def at(tz, *a):
        return _aware(tz, *a)

    assert s.external(lab("TSE"), at("Asia/Tokyo", 2026, 10, 22, 23, 58))
    assert not s.external(lab("TSE"), at("Asia/Tokyo", 2026, 10, 22, 23, 59))
    assert s.external(lab("HKEX"), at("Asia/Hong_Kong", 2026, 10, 22, 13, 0))
    assert not s.external(lab("HKEX"), at("Asia/Hong_Kong", 2026, 10, 22, 12, 0))
    assert s.external(lab("SSE"), at("Asia/Shanghai", 2026, 10, 22, 14, 56))
    assert not s.external(lab("SZSE"), at("Asia/Shanghai", 2026, 10, 22, 14, 57))


def test_unknown_exchange_is_internal():
    assert not s.external(Label("xyz:X", "none", "", "LSE", "GBP", "X"), utc(2026, 10, 21, 10, 0))
    assert not s.external(Label("xyz:Q", "none", "", "", "", "Q"), utc(2026, 10, 21, 15, 0))


def test_nse_window_is_the_assumed_regular_session():
    sessions = s
    nse = Label("xyz:RELIANCE", "none", "RELIANCE", "NSE", "INR", "Reliance Industries")
    ist = ZoneInfo("Asia/Kolkata")
    at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=ist)
    assert not sessions.external(nse, at(23, 9, 14)) and sessions.external(nse, at(23, 9, 15))
    assert sessions.external(nse, at(23, 15, 29)) and not sessions.external(nse, at(23, 15, 30))
    assert not sessions.external(nse, at(24, 11, 0))  # Saturday
