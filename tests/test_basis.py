import hashlib
import io
import zipfile
from datetime import UTC, date, datetime

import pytest

from venue_arb import basis
from venue_arb.sources import binance_archive, kalshi

H = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def zipped(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "\n".join(lines))
    return buf.getvalue()


def row(t, close):
    return f"{t},1,2,3,{close},5,6,7,8,9,10,0"


def test_parse_zip_ms_us_and_header():
    ms = int(H.timestamp() * 1000)
    out = binance_archive.parse_zip(zipped(["open_time,open", row(ms, "81400.5"), row(ms * 1000, "81401")]))
    assert out == [{"open": H, "close": 81400.5}, {"open": H, "close": 81401.0}]


def test_klines_checksum_and_missing():
    blob = zipped([row(1, "5")])
    sums = (hashlib.sha256(blob).hexdigest() + "  f.zip").encode()
    ok = lambda u: sums if u.endswith("CHECKSUM") else blob
    assert binance_archive.klines("BTCUSDT", "1h", date(2026, 10, 18), get=ok)[0]["close"] == 5.0
    assert binance_archive.klines("BTCUSDT", "1h", date(2026, 10, 20), get=lambda u: None) is None
    bad = lambda u: b"0" * 64 + b"  f" if u.endswith("CHECKSUM") else blob
    with pytest.raises(RuntimeError, match="checksum"):
        binance_archive.klines("BTCUSDT", "1h", date(2026, 10, 18), get=bad)


def test_fetch_settled_follows_cursor():
    pages = [{"markets": [{"a": 1}], "cursor": "c"}, {"markets": [{"a": 2}], "cursor": ""}]
    calls = []

    def fake(url, params=None, **kw):
        calls.append(dict(params))
        return pages[len(calls) - 1]

    assert kalshi.fetch_settled("KXBTCD", 100, get=fake) == [{"a": 1}, {"a": 2}]
    assert calls[0]["status"] == "settled" and calls[0]["min_close_ts"] == 100 and calls[1]["cursor"] == "c"


def mk(tkr, close, val):
    return {"event_ticker": tkr, "close_time": close, "expiration_value": val}


def test_brti_by_instant_dedupes_and_skips():
    ms = [mk("KXBTCD-26OCT2005", "2026-10-20T09:00:00Z", "81393.93"),
          mk("KXBTCD-26OCT2005", "2026-10-20T09:00:00Z", "81393.93"),  # same event, other strike
          mk("KXETHD-26OCT2005", "2026-10-20T09:00:00Z", "2400.5"),
          mk("KXBTCD-26OCT2006", "2026-10-20T10:00:00Z", ""),  # unsettled value
          mk("KXBTCD-26OCT2006", "2026-10-20T10:30:00Z", "1")]  # off the hour
    assert basis.brti_by_instant(ms) == {("BTC", H): 81393.93, ("ETH", H): 2400.5}
    with pytest.raises(ValueError):
        basis.brti_by_instant(ms + [mk("KXBTCD-26OCT2005", "2026-10-20T09:00:00Z", "5")])


def test_join_on_instant():
    brti = {("BTC", H): 81393.93, ("BTC", datetime(2026, 10, 20, 10, tzinfo=UTC)): 1.0}
    h1 = {("BTC", H): 81400.0}  # keyed by candle END
    m1 = {("BTC", H): 81395.0}
    rows = basis.join(brti, h1, m1)
    assert rows == [{"instant": H, "asset": "BTC", "brti": 81393.93, "binance": 81400.0, "diff": pytest.approx(-6.07),
                     "binance_1m": 81395.0, "diff_1m": pytest.approx(-1.07)}]


def test_candle_ends():
    c = [{"open": datetime(2026, 10, 20, 8, tzinfo=UTC), "close": 7.0}]
    assert basis.by_end("BTC", c, 3600) == {("BTC", H): 7.0}


def test_bound_rounds_up_to_dollar():
    diffs = [-0.5, 1.0, 2.0, 3.5, -4.2] + [0.1] * 995
    assert basis.bound(diffs) == 4.0  # nearest-rank 99.9th of 1000 is 3.5 -> $4
    assert basis.bound(diffs, q=1.0) == 5.0
    assert basis.bound([0.0, 0.0]) == 0.0
    assert basis.bound([3.0] * 10) == 3.0
    with pytest.raises(ValueError):
        basis.bound([])
