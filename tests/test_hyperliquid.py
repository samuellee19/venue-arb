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
    assert "xyz:GOLD" not in labels and labels["xyz:ANT"].kind == "pre_ipo"  # commodities out of scope; pre-IPO names are labelled, not scored
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
    head = "perp,ref_kind,ref_symbol,exchange,currency,name,kind\n"
    for row, needle in (("xyz:FOO,schwab,,NASDAQ,USD,Foo,equity\n", "xyz:FOO"), ("xyz:BAR,yahoo,BAR,NYSE,USD,Bar,equity\n", "xyz:BAR")):
        p = tmp_path / "labels.csv"
        p.write_text(head + "xyz:OK,none,,KRX,KRW,Ok,equity\n" + row)
        with pytest.raises(ValueError, match=needle):
            hl.load_labels(p)


def test_load_labels_reads_kind_and_validates_it(tmp_path):
    p = tmp_path / "l.csv"
    head = "perp,ref_kind,ref_symbol,exchange,currency,name,kind\n"
    p.write_text(head + "xyz:NVDA,schwab,NVDA,NASDAQ,USD,NVIDIA,equity\nxyz:ANT,none,,,USD,Anthropic (pre-IPO),pre_ipo\n")
    labels = hl.load_labels(p)
    assert labels["xyz:NVDA"].kind == "equity" and labels["xyz:ANT"].kind == "pre_ipo"
    for bad in ("xyz:A,none,,NSE,INR,Jio,pre_ipo",        # pre_ipo with an exchange
                "xyz:B,schwab,B,NASDAQ,USD,B,pre_ipo",     # pre_ipo with a reference
                "xyz:C,none,,,CNY,YMTC,equity",            # equity without an exchange
                "xyz:D,none,,,USD,D,spac"):                # unknown kind
        p.write_text(head + bad + "\n")
        with pytest.raises(ValueError, match="bad label row"):
            hl.load_labels(p)


def test_label_or_none_defaults_to_equity():
    assert hl.label_or_none("xyz:NEW", {}).kind == "equity"


def test_labels_file_has_a_kind_on_every_row_and_the_pre_ipo_names():
    labels = hl.load_labels()
    pre = sorted(s for s, lb in labels.items() if lb.kind == "pre_ipo")
    assert {"xyz:ANT", "xyz:JIO", "para:ANTH", "io:ANTH", "io:OAI"} <= set(pre)  # YMTC is delisted on the venue and excluded
    assert all(lb.exchange for lb in labels.values() if lb.kind == "equity")
    assert all(lb.ref_kind == "none" and not lb.ref_symbol and not lb.exchange for lb in labels.values() if lb.kind == "pre_ipo")


def _ctx(name, px="100"):
    u = {"name": name, "szDecimals": 3, "maxLeverage": 10, "growthMode": "enabled"}
    c = {"funding": "0.0", "openInterest": "1", "prevDayPx": px, "dayNtlVlm": "1", "premium": "0", "oraclePx": px,
         "markPx": px, "midPx": px, "impactPxs": [px, px]}
    return u, c


def _dex_post(payloads):
    sent = []

    def post(body):
        sent.append(body)
        us, cs = zip(*payloads[body["dex"]], strict=True)
        return [{"universe": list(us)}, list(cs)]
    return post, sent


def test_dex_of_reads_the_prefix():
    assert hl.dex_of("para:ANTH") == "para" and hl.dex_of("xyz:NVDA") == "xyz"
    assert hl.dex_of("ANTH") == ""  # a bare coin lives on the main dex; never labelled here


def test_fetch_ctxs_for_queries_each_labelled_dex_and_keeps_only_labelled_names_off_xyz():
    labels = {"xyz:NVDA": hl.Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "Nvidia"),
              "para:ANTH": hl.Label("para:ANTH", "none", None, "", "USD", "Anthropic", "pre_ipo"),
              "io:ANTH": hl.Label("io:ANTH", "none", None, "", "USD", "Anthropic", "pre_ipo")}
    post, sent = _dex_post({"xyz": [_ctx("xyz:NVDA"), _ctx("xyz:NEW")],
                            "para": [_ctx("para:ANTH"), _ctx("para:UNITREE")],
                            "io": [_ctx("io:ANTH"), _ctx("io:OAI")]})
    ctxs = hl.fetch_ctxs_for(labels, post=post)
    assert [b["dex"] for b in sent] == ["xyz", "para", "io"] and all(b["type"] == "metaAndAssetCtxs" for b in sent)
    # xyz comes back whole (catalog flags xyz:NEW as unlabelled); the other deployers contribute labelled names only
    assert [c.symbol for c in ctxs] == ["xyz:NVDA", "xyz:NEW", "para:ANTH", "io:ANTH"]
    kept, unlabelled = hl.catalog(ctxs, labels)
    assert [c.symbol for c in kept] == ["xyz:NVDA", "para:ANTH", "io:ANTH"] and unlabelled == ["xyz:NEW"]


def test_fetch_ctxs_for_with_xyz_labels_only_makes_one_call():
    labels = {"xyz:NVDA": hl.Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "Nvidia")}
    post, sent = _dex_post({"xyz": [_ctx("xyz:NVDA")]})
    assert [c.symbol for c in hl.fetch_ctxs_for(labels, post=post)] == ["xyz:NVDA"] and len(sent) == 1


def test_fetch_ctxs_for_survives_one_deployer_failing():
    labels = {"xyz:NVDA": hl.Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "Nvidia"),
              "para:ANTH": hl.Label("para:ANTH", "none", None, "", "USD", "Anthropic", "pre_ipo")}
    post, _ = _dex_post({"xyz": [_ctx("xyz:NVDA")]})  # no "para" payload: that call raises

    def flaky(body):
        if body["dex"] == "para":
            raise RuntimeError("POST info metaAndAssetCtxs failed")
        return post(body)
    assert [c.symbol for c in hl.fetch_ctxs_for(labels, post=flaky)] == ["xyz:NVDA"]
