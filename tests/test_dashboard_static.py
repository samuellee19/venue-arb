import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "dashboard" / "static"
VERDICTS = ("Exact match · no edge", "Executable edge", "Not an arbitrage · basis exposure",
            "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min",
            "Not executable from the US")
BANNERS = ("No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference.",
           "last update {n} min ago")
ALLOWED_URLS = ("https://kalshi.com", "https://polymarket.com", "https://github.com/samuellee19/venue-arb")


def test_index_references_only_vendored_files():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs, "index.html references nothing"
    for ref in refs:
        assert ref.startswith("/static/"), ref  # no CDN, no external URL
        assert (STATIC / ref.removeprefix("/static/")).is_file(), ref


def test_vendor_md_sha256_matches_plotly():
    sha = re.search(r"\b[0-9a-f]{64}\b", (STATIC / "VENDOR.md").read_text(encoding="utf-8")).group(0)
    assert hashlib.sha256((STATIC / "plotly-basic.min.js").read_bytes()).hexdigest() == sha


def test_app_js_has_no_cdn_and_carries_the_copy():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for url in re.findall(r"https?://[^\s'\"`)]+", js):
        assert url.startswith(ALLOWED_URLS), url
    for text in VERDICTS + BANNERS:
        assert text in js, text
