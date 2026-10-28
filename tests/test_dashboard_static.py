import hashlib
import re
from pathlib import Path

from venue_arb.bundle import Pair, ResearchSummary
from venue_arb.report import copy

STATIC = Path(__file__).parent.parent / "dashboard" / "static"
TEXT = ("index.html", "styles.css", *sorted(p.name for p in STATIC.glob("*.js") if p.name != "plotly-basic.min.js"))
VERDICTS = ("Exact match · no edge", "Executable edge", "Not an arbitrage · basis exposure",
            "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min",
            "Not executable from the US", "Oracle basis · within perp costs", "Oracle internal · market closed", "Oracle basis",
            "Pre-IPO · no listed market")
BANNERS = ("No pair is executable from the US: Polymarket rejects US orders. Gaps shown for reference.",
           "last update {n} min ago", "last update {n} min ago — figures may be behind", "no books in 5 min")
STOCK_COPY = ("Not advice. Hyperliquid blocks US traders; the perp leg is shown for reference.",
              ("{n} stock perps — perp against the venue's oracle, net of 2× perp taker fee, perp spread and 24 h funding, "
               "before stock-leg costs"), "basis is quoted in bps", "oracle internal", "perpetual",
              "The stock leg is not modelled on this page.", "at 1 h / 7 d / with borrow", "nothing to score until it lists")
SCANNER_COPY = ("Example — what a row looks like when a pair clears the rule",
                "per-leg quotes are not published (venue display terms)", "ROI = net edge ÷ all-in cost per contract",
                "admissible ladder checks in the last scored hour", "violations", "no scored hour yet",
                "research summary not computed yet")
RESEARCH_COPY = ("How it's measured", '"Matching"', '"Edge"', '"Crypto ladders"', '"What is not shown"',
                 "Match review: precision", "no review sitting yet",
                 "recomputed hourly from the same tables the Scanner reads", "NOT_COMPUTED")
MIDDOT_OK = tuple(v for v in VERDICTS if "·" in v)  # mid-dots live only inside the verbatim verdict labels
ALLOWED_URL = re.compile(r"https://(kalshi\.com|polymarket\.com|app\.hyperliquid\.xyz)(/|$)")  # the repo URL is config.REPO_URL, injected
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


def test_repo_url_comes_from_config_only():
    assert "github.com" not in _js() + _read("index.html")  # one value: venue_arb.config.REPO_URL
    assert '<meta name="repo" content="__REPO_URL__">' in _read("index.html")
    assert "meta[name=\"repo\"]" in _read("common.js")


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


def test_stock_copy_and_hyperliquid_url_allow_list():
    common, js = _read("common.js"), _js()
    for text in STOCK_COPY:
        assert text in js, text
    for const in ("NOT_ADVICE", "FOOT_STOCK"):
        assert f"export const {const} = " in common, const
    assert "app\\.hyperliquid\\.xyz" in common or "app.hyperliquid.xyz" in common  # safeUrl allows the perp page
    assert ALLOWED_URL.match("https://app.hyperliquid.xyz/trade/xyz:NVDA")
    assert not ALLOWED_URL.match("https://app.hyperliquid.xyz.evil.net/x")


def test_scanner_has_stock_header_template():
    js = _read("scanner.js")
    assert 'S.cat === "stock_perps"' in js and "FOOT_STOCK" in js and "NOT_ADVICE" in js
    for th in ("Name", "Trade", "Basis", "Net", "Funding", "Capacity", "Reference", "Verdict"):
        assert f">{th}<" in js or f">{th}</button>" in js, th
    for key in ("net_bps", "funding_bps_h", "basis_bps", "next_funding_at"):
        assert key in js, key
    assert "oracle internal" in js and "perpetual" in js


def test_research_has_stock_cards_copy():
    js = _read("research.js")
    for text in ("Stock-perp spread", "Weekends", "too few weekends to conclude", '["stock_perps", "Stock perps"]',
                 "perp at Sunday 6 PM ET vs Monday's open", "share_hours_abs_ge_10bps", "hit_rate_sun", "n_sun"):
        assert text in js, text
    assert js.index('card("rc-ladder"') < js.index("stockCards(r.stock_perps)")  # the stock cards come after the ladder card
    assert "r.stock_perps ?" in js  # and only when the bundle carries them


def test_stock_rows_are_guarded_in_the_client():
    assert "l.url ? " in _read("explorer.js") or "l.url?" in _read("explorer.js")  # a stock leg has no url
    common = _read("common.js")
    assert "STOCK_MIN_BPS = 10" in common and "p.net_bps >= STOCK_MIN_BPS" in common  # tone() keys on bps for stock rows


def test_verdict_labels_verbatim_in_common():
    common = _read("common.js")  # common.js alone: a label split across modules would not count
    for text in VERDICTS:
        assert f'"{text}"' in common, text


def test_pre_ipo_client_rules():
    common, scanner, explorer = _read("common.js"), _read("scanner.js"), _read("explorer.js")
    assert '"Pre-IPO · no listed market"' in common and "FOOT_PRE_IPO" in common
    assert 'cat === "pre_ipo"' in scanner and 'S.cat === "pre_ipo"' in scanner
    assert 'category === "pre_ipo"' in explorer and "No listed share yet: nothing to score until it lists." in explorer
    assert '(byKey(S.sel)?.category === "pre_ipo") === !!$("plot")' in explorer  # a same-key label flip rebuilds the layout


def test_pre_ipo_sentence_in_method_copy():
    assert "Pre-IPO perps are listed for their price only and are not scored until the company lists." in copy.METHOD["stock_perps"]


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
            for v in MIDDOT_OK:
                line = line.replace(v, "")
            assert "·" not in line, (name, i, line)
    assert "·" not in "".join(copy.METHOD.values()) + "".join(copy.HEADLINE.values())


def test_js_reads_only_model_fields():
    # the seam: every r.<field> the Research tab reads and every p.<field> the Scanner reads exists on the model
    for name, var, model in (("research.js", "r", ResearchSummary), ("scanner.js", "p", Pair)):
        read = set(re.findall(rf"\b{var}\.([a-z_0-9]+)", _read(name)))
        assert read and read <= set(model.model_fields), (name, read - set(model.model_fields))


def test_stock_fix_round_one_client_rules():
    assert "[hidden] { display:none !important; }" in _read("styles.css")  # beats .size { display:inline-flex }
    js = _read("research.js")
    assert "contained_sun" in js and "function contained" not in js and "p_open_reg" not in js  # plots the bundle's share
    assert 'r.method[k] && (k !== "stock_perps" || r.stock_perps)' in js  # no stock prose without the block
    ex = _read("explorer.js")
    stock_fn = ex[ex.index("function costStock(p)"):ex.index("function costStockSnap")]
    assert "p.costs" in stock_fn and "fee_params" not in stock_fn and "fees_cents" not in stock_fn  # prints the bundle's lines
    assert "esc(l.venue.toUpperCase())" in ex
    assert "p.legs[0].side" in _read("common.js") and 'split(" / ")' not in _read("common.js")


def test_no_schwab_on_the_page():
    from venue_arb.report.copy import METHOD
    root = Path(__file__).parent.parent / "dashboard"
    files = [f for f in (root / "static").rglob("*") if f.is_file() and f.suffix in (".js", ".css", ".html", ".json", ".svg")]
    files += list((root / "sample").glob("*.json"))
    assert files
    for f in files:
        assert "schwab" not in f.read_text(encoding="utf-8").lower(), f
    assert "schwab" not in METHOD["stock_perps"].lower()
    assert len(re.findall(r"[.!?](?:\s|$)", METHOD["stock_perps"])) <= 7


def test_client_reads_only_the_oracle_keys():
    js = _js()
    for old in ("stock_spread_bps", "borrow_bps", "borrow_x10", "reference.symbol", "borrow ×10"):
        assert old not in js, old
    assert "episodes_with_borrow" in _read("research.js") and "net_bps_with_borrow" in _read("explorer.js")


def test_stock_perp_copy_says_before_stock_leg_costs():
    from venue_arb.report import copy

    head = copy.HEADLINE["stock_perps"]
    assert "before stock-leg costs, with perp fees and 24 h funding" in head and "round-trip" not in head
    assert '"Stock-perp spread", "before stock-leg costs"' in _read("research.js") and "net of costs" not in _read("research.js")
    assert '"Perp depth while net ≥ 10 bps"' in _read("explorer.js") and "top of book" not in _read("explorer.js")


def test_ux_round_two_markup():
    index, scanner, app, explorer = _read("index.html"), _read("scanner.js"), _read("app.js"), _read("explorer.js")
    assert 'rel="icon"' in index  # no favicon request that 404s on every load
    assert not re.search(r"<th(?=[\s>])(?![^>]*scope=)", index + scanner), "every column header names its scope"
    assert "aria-current" in app  # the active tab is announced
    assert 'class="row' in explorer and 'tabindex="0"' in explorer and '$("rows").onkeydown' in explorer  # pair list by keyboard
    assert "docs/venues.md" not in _js() + index  # repo paths mean nothing to a visitor
    assert '$("sizebox").hidden' not in explorer  # the size row stays put, disabled, on a stock perp


def test_ux_round_two_numbers():
    scanner, app = _read("scanner.js"), _read("app.js")
    assert "bps(p.net_bps, 1)" in scanner and "bps(p.net_bps)" not in scanner  # one decimal in the All view too
    assert "sp ? 2 : 1" in scanner  # a bps row's ROI never prints as −0.0%
    assert "Math.max(1, Math.round(h))" not in scanner and re.search(r"in \$\{[^}]+\} m", scanner)  # minutes under 2 h
    assert re.search(r'=== 1 \? "episode" : "episodes"', app)  # "1 episodes" never shows


def _lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_theme_tokens_meet_aa_contrast():
    light, dark = _read("styles.css").split(':root[data-theme="dark"]')
    for theme, block in (("light", light.split(":root")[1]), ("dark", dark)):
        t = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block.split("}")[0]))
        assert "k-fill" in t, f"{theme}: the active chip needs its own fill token"
        pairs = {"pos on panel": (t["pos"], t["panel"]), "pos on pos-bg": (t["pos"], t["pos-bg"]), "neg on panel": (t["neg"], t["panel"]),
                 "mut on chip": (t["mut"], t["chip"]), "mut on hover": (t["mut"], t["hover"]), "mut on head": (t["mut"], t["head"]),
                 "mut on panel": (t["mut"], t["panel"]), "white on k-fill": ("#ffffff", t["k-fill"]), "k on bg": (t["k"], t["bg"]),
                 "k on panel": (t["k"], t["panel"]), "warn on warn-bg": (t["warn"], t["warn-bg"])}
        low = {k: round(_contrast(*v), 2) for k, v in pairs.items() if _contrast(*v) < 4.5}
        assert not low, f"{theme}: below AA 4.5 for 13 px text: {low}"


def test_dead_bundle_greys_the_figures_and_says_so():
    app, css, common = _read("app.js"), _read("styles.css"), _read("common.js")
    assert "DEAD_BANNER" in common and "figures may be behind" in common
    assert 'classList.toggle("dead"' in app and "DEAD_BANNER" in app  # the page, not only the header figures, goes dead
    assert re.search(r"body\.dead[^{]*\.badge[^{]*\{", css) and re.search(r"body\.dead[^{]*td\.num[^{]*\{", css)
