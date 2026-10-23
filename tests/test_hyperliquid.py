import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from venue_arb.config import BOOK_DEPTH
from venue_arb.sources import hyperliquid as hl

FX = Path(__file__).parent / "fixtures" / "hyperliquid"


@pytest.fixture
def fx():
    return {p.stem: json.loads(p.read_text()) for p in FX.glob("*.json")}


def test_fetch_ctxs_parses_growth_and_funding(fx):
    sent = []
    ctxs = hl.fetch_ctxs(post=lambda body: sent.append(body) or fx["meta_ctxs"])
    assert sent == [{"type": "metaAndAssetCtxs", "dex": "xyz"}]
    nvda = next(c for c in ctxs if c.symbol == "xyz:NVDA")
    assert nvda.mark == 212.83 and nvda.oracle == 212.81 and nvda.growth_mode and nvda.max_leverage == 20
    assert nvda.funding_h == pytest.approx(6.2e-6) and nvda.day_volume_usd == 79_100_000.0
    assert nvda.mid == 212.83 and nvda.premium == pytest.approx(8e-5) and nvda.open_interest == 411_000.0
    gold = next(c for c in ctxs if c.symbol == "xyz:GOLD")
    assert not gold.growth_mode and gold.mid is None
    assert next(c for c in ctxs if c.symbol == "xyz:NEWCO").premium is None


def test_fetch_book_best_first_and_depth(fx):
    ts, levels = hl.fetch_book("xyz:NVDA", post=lambda body: fx["l2book"])
    assert ts == datetime(2026, 10, 22, 17, 41, 13, 721000, tzinfo=UTC)
    bids = [lv for lv in levels if lv.side == "bid"]
    asks = [lv for lv in levels if lv.side == "ask"]
    assert bids[0].price == 212.82 and bids[0].size == 111.594 and bids[0].level == 0
    assert asks[0].price == 212.84 and asks[0].size == 308.321
    assert all(b.price > bids[i + 1].price for i, b in enumerate(bids[:-1]))
    assert all(a.price < asks[i + 1].price for i, a in enumerate(asks[:-1]))
    assert [b.level for b in bids] == list(range(len(bids)))
    assert len(bids) == len(asks) == BOOK_DEPTH  # fixture carries 12 a side


def test_candles_and_funding_hours_are_utc(fx):
    start, end = datetime(2026, 10, 22, 14, tzinfo=UTC), datetime(2026, 10, 22, 17, tzinfo=UTC)
    sent = []
    candles = hl.fetch_candles("xyz:NVDA", start, end, post=lambda body: sent.append(body) or fx["candles"])
    assert sent[0]["req"] == {"coin": "xyz:NVDA", "interval": "1h", "startTime": 1792677600000, "endTime": 1792688400000}
    raw = fx["candles"][0]
    assert candles[0]["hour"] == datetime.fromtimestamp(raw["t"] / 1000, UTC) and candles[0]["hour"].tzinfo is UTC
    assert candles[0]["open"] == float(raw["o"]) and candles[0]["volume"] == float(raw["v"])
    funding = hl.fetch_funding("xyz:NVDA", start, post=lambda body: sent.append(body) or fx["funding"])
    assert sent[1] == {"type": "fundingHistory", "coin": "xyz:NVDA", "startTime": 1792677600000}
    assert fx["funding"][0]["time"] % 3_600_000 != 0  # raw event time is off the hour boundary
    assert [f["hour"] for f in funding] == [c["hour"] for c in candles]  # floors to the candle hour starts
    assert funding[0]["rate"] == pytest.approx(6.2e-6) and funding[0]["premium"] == pytest.approx(8e-5)


def test_labels_cover_every_equity_and_catalog_flags_unlabelled(fx):
    labels = hl.load_labels()
    assert labels["xyz:NVDA"] == hl.Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
    assert labels["xyz:SKHX"].ref_kind == "none" and labels["xyz:SKHX"].exchange == "KRX"
    assert "xyz:SPCX" not in labels and "xyz:GOLD" not in labels  # pre-IPO and commodities out of scope
    assert len(labels) >= 100 and all(k.startswith("xyz:") for k in labels)
    assert all(lb.ref_kind != "schwab" or lb.ref_symbol for lb in labels.values())
    assert "xyz:GOLD" in hl.load_excluded()
    kept, unlabelled = hl.catalog(hl.fetch_ctxs(post=lambda body: fx["meta_ctxs"]), labels, hl.load_excluded())
    assert [c.symbol for c in kept] == ["xyz:NVDA"] and unlabelled == ["xyz:NEWCO"]  # GOLD: labelled out, not unlabelled


def test_a_labelled_name_may_also_be_excluded(fx):
    # the excluded file hides a name from every public path (publish reads it); a labelled name stays catalogued
    labels = {"xyz:NVDA": hl.load_labels()["xyz:NVDA"]}
    kept, unlabelled = hl.catalog(hl.fetch_ctxs(post=lambda body: fx["meta_ctxs"]), labels, {"xyz:NVDA", "xyz:GOLD"})
    assert [c.symbol for c in kept] == ["xyz:NVDA"] and unlabelled == ["xyz:NEWCO"]


def test_catalog_drops_delisted_perps(fx):
    ctxs = hl.fetch_ctxs(post=lambda body: fx["meta_ctxs"])
    assert next(c for c in ctxs if c.symbol == "xyz:OLDCO").delisted and not next(c for c in ctxs if c.symbol == "xyz:NVDA").delisted
    labels = {s: hl.label_or_none(s, {}) for s in ("xyz:NVDA", "xyz:OLDCO")}
    kept, unlabelled = hl.catalog(ctxs, labels)
    assert [c.symbol for c in kept] == ["xyz:NVDA"]  # labelled but delisted: dropped
    assert "xyz:OLDCO" not in unlabelled and unlabelled == ["xyz:GOLD", "xyz:NEWCO"]  # unlabelled and delisted: not reported


def test_unlabelled_perp_is_oracle_only():
    assert hl.label_or_none("xyz:NEWCO", {}) == hl.Label("xyz:NEWCO", "none", None, "", "USD", "xyz:NEWCO")


def test_load_excluded_strips_reasons_and_blanks(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("xyz:A  # why\n\n# only a comment\nxyz:B\n")
    assert hl.load_excluded(p) == {"xyz:A", "xyz:B"}


def test_post_json_retries_then_raises_without_leaking_body(monkeypatch):
    monkeypatch.setattr(hl.time, "sleep", lambda s: None)
    calls = []

    def post(url, json, timeout):
        calls.append(json)
        return httpx.Response(429 if len(calls) < 3 else 200, json={"ok": 1}, request=httpx.Request("POST", url))

    assert hl.post_json({"type": "l2Book", "coin": "xyz:NVDA"}, post=post) == {"ok": 1} and len(calls) == 3

    def down(url, json, timeout):
        raise httpx.ConnectError("boom")

    with pytest.raises(RuntimeError, match="POST info l2Book failed after 4 tries: ConnectError"):
        hl.post_json({"type": "l2Book", "coin": "xyz:NVDA"}, post=down)

    def bad(url, json, timeout):
        return httpx.Response(400, request=httpx.Request("POST", url))

    with pytest.raises(RuntimeError, match="HTTP 400"):
        hl.post_json({"type": "l2Book"}, post=bad)


def test_load_labels_rejects_a_bad_row(tmp_path):
    head = "perp,ref_kind,ref_symbol,exchange,currency,name\n"
    for row, needle in (("xyz:FOO,schwab,,NASDAQ,USD,Foo\n", "xyz:FOO"), ("xyz:BAR,yahoo,BAR,NYSE,USD,Bar\n", "xyz:BAR")):
        p = tmp_path / "labels.csv"
        p.write_text(head + "xyz:OK,none,,KRX,KRW,Ok\n" + row)
        with pytest.raises(ValueError, match=needle):
            hl.load_labels(p)
