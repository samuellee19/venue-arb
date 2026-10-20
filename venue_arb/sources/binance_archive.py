"""Binance spot kline archive (data.binance.vision): daily zips, each verified against its .CHECKSUM (sha256)."""

import csv
import hashlib
import io
import zipfile
from datetime import UTC, date, datetime

from venue_arb import http

BASE = "https://data.binance.vision/data/spot/daily/klines"


def parse_zip(blob: bytes) -> list[dict]:
    """Kline rows -> {open: aware UTC datetime, close: float}. Timestamps are ms, or us in recent files."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].isdigit():  # header, if any
            continue
        t = int(row[0])
        out.append({"open": datetime.fromtimestamp(t / (1e6 if t > 1e14 else 1e3), UTC), "close": float(row[4])})
    return out


def klines(symbol: str, interval: str, day: date, get=http.get_bytes) -> list[dict] | None:
    """One UTC day of `interval` klines, or None when the archive has not published it yet (it lags ~1 day)."""
    url = f"{BASE}/{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"
    blob = get(url)
    if blob is None:
        return None
    sums = get(url + ".CHECKSUM")
    want = sums.decode().split()[:1] if sums else []
    if want != [hashlib.sha256(blob).hexdigest()]:
        raise RuntimeError(f"checksum mismatch for {symbol}-{interval}-{day}")
    return parse_zip(blob)
