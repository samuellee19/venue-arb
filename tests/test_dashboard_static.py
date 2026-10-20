import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "dashboard" / "static"
VERDICTS = ("Exact match · no edge", "Executable edge", "Not an arbitrage · basis exposure",
            "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min",
            "Not executable from the US")
BANNERS = ("No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference.",
           "last update {n} min ago", "no books in 5 min")
ALLOWED_URL = re.compile(r"https://(kalshi\.com|polymarket\.com|github\.com/samuellee19/venue-arb)(/|$)")
URL = re.compile(r"https?://[^\s'\"`)]+")


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


def test_no_external_urls_but_venue_and_repo_links():
    for name in ("app.js", "styles.css", "index.html"):  # index.html: its inline script too
        for url in URL.findall((STATIC / name).read_text(encoding="utf-8")):
            assert ALLOWED_URL.match(url), (name, url)
    assert not ALLOWED_URL.match("https://kalshi.com.evil.net/x")


def test_app_js_carries_the_copy():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for text in VERDICTS + BANNERS:
        assert text in js, text
