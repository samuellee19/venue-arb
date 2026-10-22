import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "dashboard" / "static"
TEXT = ("index.html", "styles.css", *sorted(p.name for p in STATIC.glob("*.js") if p.name != "plotly-basic.min.js"))
VERDICTS = ("Exact match · no edge", "Executable edge", "Not an arbitrage · basis exposure",
            "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min",
            "Not executable from the US")
BANNERS = ("No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference.",
           "last update {n} min ago", "no books in 5 min")
SCANNER_COPY = ("Example — what a row looks like when a pair clears the rule",
                "per-leg quotes are not published (venue display terms)", "ROI = net edge ÷ all-in cost per contract",
                "admissible ladder checks in the last scored hour", "violations", "no scored hour yet",
                "research summary not computed yet")
RESEARCH_COPY = ("How it's measured", '"Matching"', '"Edge"', '"Crypto ladders"', '"What is not shown"',
                 "Match review: precision", "no review sitting yet",
                 "recomputed hourly from the same tables the Scanner reads", "NOT_COMPUTED")
MIDDOT_OK = tuple(v for v in VERDICTS if "·" in v)  # mid-dots live only inside the verbatim verdict labels
ALLOWED_URL = re.compile(r"https://(kalshi\.com|polymarket\.com|github\.com/samuellee19/venue-arb)(/|$)")
URL = re.compile(r"https?://[^\s'\"`)]+")
IMPORT = re.compile(r'^import\s[^;]*?\sfrom\s+"([^"]+)"', re.MULTILINE)


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _js() -> str:
    return "".join(_read(n) for n in TEXT if n.endswith(".js"))


def test_index_references_only_vendored_files():
    html = _read("index.html")
    refs = re.findall(r'(?:src|href)="([^"#]+)"', html)
    assert refs, "index.html references nothing"
    for ref in refs:
        assert ref.startswith("/static/"), ref  # no CDN, no external URL
        assert (STATIC / ref.removeprefix("/static/")).is_file(), ref


def test_vendor_md_sha256_matches_plotly():
    sha = re.search(r"\b[0-9a-f]{64}\b", _read("VENDOR.md")).group(0)
    assert hashlib.sha256((STATIC / "plotly-basic.min.js").read_bytes()).hexdigest() == sha


def test_no_external_urls_but_venue_and_repo_links():
    assert {"app.js", "common.js", "scanner.js", "explorer.js", "research.js"} <= set(TEXT)
    for name in TEXT:  # index.html: its inline script too
        for url in URL.findall(_read(name)):
            assert ALLOWED_URL.match(url), (name, url)
    assert not ALLOWED_URL.match("https://kalshi.com.evil.net/x")


def test_title_heading_and_tabs():
    html = _read("index.html")
    assert re.search(r"<title>(.*?)</title>", html).group(1) == "venue-arb · Prediction Market Arbitrage"
    assert "<h1>Prediction Market Arbitrage</h1>" in html
    assert ("Kalshi vs Polymarket: matched markets, executable edge after fees and depth, "
            "and why most gaps aren't arbitrage.") in html
    for tab in ("scanner", "explorer", "research"):
        assert f'href="#{tab}"' in html and f'id="tab-{tab}"' in html, tab
    assert "episodes · 7 d" in html and "pairs live" in html


def test_module_imports_resolve():
    html = _read("index.html")
    assert '<script type="module" src="/static/app.js">' in html
    imports = {n: IMPORT.findall(_read(n)) for n in TEXT if n.endswith(".js")}
    assert {"./common.js", "./scanner.js", "./explorer.js", "./research.js"} <= set(imports["app.js"])
    for name, specs in imports.items():
        for spec in specs:
            assert spec.startswith("./"), (name, spec)  # relative only, no CDN
            assert (STATIC / spec).is_file(), (name, spec)


def test_styles_use_inter_by_name_only():
    css = _read("styles.css")
    assert re.search(r'font-family:\s*Inter,\s*-apple-system,\s*"Segoe UI",\s*sans-serif', css)
    assert "@import" not in css and "url(http" not in css and "@font-face" not in css
    assert "[data-theme=dark]" in css or '[data-theme="dark"]' in css
    assert "gradient" not in css


def test_js_carries_the_copy():
    js = _js()
    for text in VERDICTS + BANNERS + SCANNER_COPY:
        assert text in js, text


def test_verdict_labels_verbatim_in_common():
    common = _read("common.js")  # common.js alone: a label split across modules would not count
    for text in VERDICTS:
        assert f'"{text}"' in common, text


def test_research_tab_copy():
    js = _read("research.js")
    for text in RESEARCH_COPY:
        assert text in js, text
    assert len(js.splitlines()) <= 250


def test_charts_share_one_base_layout():
    assert "export function baseLayout(" in _read("common.js")
    for name in ("explorer.js", "research.js"):
        assert "baseLayout()" in _read(name), name


def test_no_mid_dot_outside_verdict_labels():
    for name in (n for n in TEXT if n.endswith(".js")):
        for i, line in enumerate(_read(name).splitlines(), 1):
            if "·" in line:
                assert any(v in line for v in MIDDOT_OK) or "VERDICT_TONES = [" in line, (name, i, line)
