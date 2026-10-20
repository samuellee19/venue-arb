import hashlib
from datetime import UTC, datetime

import pyarrow.parquet as pq

from venue_arb.bakeoff.snapshot import truth_rows, write_parquet

T = datetime(2026, 10, 20, 16, tzinfo=UTC)


def row(venue, mid, title, yes, event="", category="crypto_price"):
    return {"venue": venue, "market_id": mid, "event_id": event, "category": category, "title": title,
            "yes_label": yes, "close_time": T}


ROWS = [row("kalshi", "KXBTCD-26OCT2017-T79999.99", "Bitcoin price on Oct 20, 2026?", "$80,000 or above",
            "KXBTCD-26OCT2017"),
        row("polymarket", "0xa", "Will the price of Bitcoin be above $80,000 on October 20?", "Yes"),
        row("kalshi", "KXFEDDECISION-26OCT-H0", "Fed decision?", "Hold", "KXFEDDECISION-26OCT", "fed_macro"),
        row("polymarket", "0xf", "No change in October?", "Yes", category="fed_macro")]


def test_truth_rows_keeps_present_known_pairs_only():
    known = [{"kalshi_market_id": "KXFEDDECISION-26OCT-H0", "pm_market_id": "0xf", "note": "hold"},
             {"kalshi_market_id": "KXFEDDECISION-26OCT-H0", "pm_market_id": "0xgone", "note": "gone"}]
    got = truth_rows(ROWS, known)
    assert [(t["kalshi_market_id"], t["pm_market_id"], t["kind"]) for t in got] == [
        ("KXBTCD-26OCT2017-T79999.99", "0xa", "crypto_above"), ("KXFEDDECISION-26OCT-H0", "0xf", "fed_exact")]


def test_write_parquet_columns_text_and_hash(tmp_path):
    path = tmp_path / "s.parquet"
    sha = write_parquet(ROWS, path)
    t = pq.read_table(path)
    assert t.column_names == ["venue", "market_id", "category", "close_time", "text"]
    assert t["text"][0].as_py() == "Bitcoin price on Oct 20, 2026? | $80,000 or above | closes 2026-10-20"
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
