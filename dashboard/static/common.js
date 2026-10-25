// Shared state, constants, copy and formatting helpers for the dashboard modules (app.js, scanner.js, explorer.js,
// research.js).
// No edge, fee or basis logic here: every number comes from the bundle (dashboard/api/app.py serves it).
export const REPO = "https://github.com/samuellee19/venue-arb";
export const POLL_MS = 30000, STALE_S = 120, DEAD_S = 3600, BOOKS_S = 300;  // BOOKS_S = bundle.LIVE_MAX_AGE_S
export const US_BANNER = "No pair is executable from the US: Polymarket rejects US orders. Gaps shown for reference.";
export const STALE_BANNER = "last update {n} min ago";
export const NO_BOOKS = "no books in 5 min";
export const NOT_US = "Not executable from the US";
export const NOT_COMPUTED = "research summary not computed yet";
export const EXEC = "Executable edge";
// verdict label (prefix) -> Scanner badge tone; the labels venue_arb/bundle.verdict writes, plus the client-side US one
export const VERDICT_TONES = [["Exact match · no edge", "none"], [EXEC, "exec"], ["Not an arbitrage · basis exposure", "basis"],
  ["Spread · within basis bound · no edge", "none"], ["No asks on one side", "stale"],
  ["Stale · a leg has no snapshot in 5 min", "stale"], [NOT_US, "stale"],
  ["Oracle basis · within perp costs", "none"], ["Oracle internal · market closed", "stale"], ["Oracle basis", "exec"]];  // the last three: stock perps, most specific first
export const EXAMPLE = "Example — what a row looks like when a pair clears the rule";
export const FOOT = "{n} pairs listed — per-leg quotes are not published (venue display terms) — ROI = net edge ÷ all-in cost per contract — ";
export const FOOT_CHECKS = "{checks} admissible ladder checks in the last scored hour, {violations} violations";
export const FOOT_NO_HOUR = "no scored hour yet";
export const NOT_ADVICE = "Not advice. Hyperliquid blocks US traders; the perp leg is shown for reference.";
export const FOOT_STOCK = "{n} stock perps — perp against the venue's oracle, net of 2× perp taker fee, perp spread and 24 h funding, before stock-leg costs";
export const NO_BPS = "basis is quoted in bps";
export const STOCK_MIN_BPS = 10;  // config.STOCK_PERP_MIN_NET_BPS: the net a stock perp must clear to read as an edge

export const S = {bundle: null, age: 0, fetchedAt: 0, skew: 0, unit: "c", us: false, cat: "all", sort: null, q: "", sel: null,
  open: new Set(), range: "24h", ann: true, snap: null, walk: null, size: 1000};

export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
export const enc = encodeURIComponent;
export const num = (x, d = 0) => x.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
export const usd = x => (x < 0 ? "−$" : "$") + num(Math.abs(x));
export const signed = (x, d, u) => `${x < 0 ? "−" : "+"}${num(Math.abs(x), d)}${u}`;
export const cents = (x, d = 1) => `${num(x, d)}¢`;
export const bpsOf = c => c / (100 - c) * 1e4;  // bps of all-in cost; all-in = $1 payout − net (bundle.net_edge_bps likewise)
// ¢ per contract for pair rows; pass the row as `p` and a stock perp is toned by its net_bps instead (¢ would read 0.1)
export const tone = (c, p) => p?.kind === "stock_perp" ? (p.net_bps == null ? "mut" : p.net_bps >= STOCK_MIN_BPS ? "pos" : p.net_bps < 0 ? "neg" : "")
  : c == null ? "mut" : c >= 1 ? "pos" : c < 0 ? "neg" : "";
export const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const pz = s => s.replace(/(Z|\+00:00)$/, "");  // Plotly ignores zones: strings stay UTC wall time
export const now = () => Date.now() + S.skew;
export const hm = d => d.toISOString().slice(11, 16) + "Z";
export const day = d => d.toLocaleDateString("en-GB", {day: "numeric", month: "short", timeZone: "UTC"});
export const seg = (box, on) => [...box.children].forEach(b => b.classList.toggle("on", b === on));
export const safeUrl = u => /^https:\/\/(kalshi\.com|polymarket\.com|app\.hyperliquid\.xyz)\//.test(u) ? u : "#";

export const pairs = () => S.bundle.pairs;
export const byKey = k => pairs().find(p => p.key === k);
export const usBlocked = () => S.us && !pairs().some(p => p.us_executable);
export const greyed = p => S.us && !p.us_executable;
export const label = p => greyed(p) ? NOT_US : p.verdict.label;
export const legsTxt = p => p.kind === "stock_perp" ? `${esc(p.legs[0].side)} perp / ${esc(p.legs[1].side)} stock`
  : p.legs.map(l => `${l.venue === "kalshi" ? "K" : "PM"} ${esc(l.side)}`).join(" / ");

export function settles(iso) {  // v1's long form, for the Explorer; a stock perp has no settlement
  if (iso == null) return "perpetual";
  const d = new Date(iso), ms = d - now();
  const when = iso.slice(0, 10) === new Date(now()).toISOString().slice(0, 10) ? hm(d) : day(d);
  if (ms <= 0) return `settled ${when}`;
  const m = Math.round(ms / 60000);
  return `settles ${when}, in ${m >= 2880 ? `${Math.round(m / 1440)} d` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} m`}`;
}

// one Plotly layout for every chart (Explorer, Research): Inter 13, 8-px margins, no modebar (PLOT_CFG); colours are
// read from the CSS tokens at draw time, so a re-draw after a theme switch picks up the other theme
export function baseLayout() {
  const line = css("--line"), ax = {gridcolor: line, linecolor: line, zerolinecolor: line};
  return {margin: {l: 48, r: 16, t: 16, b: 32}, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: {family: "Inter, -apple-system, Segoe UI, sans-serif", size: 13, color: css("--mut")},
    hoverlabel: {bgcolor: css("--panel"), bordercolor: line, font: {color: css("--txt"), size: 13}},
    showlegend: false, hovermode: "closest", xaxis: {...ax}, yaxis: {...ax}, uirevision: "keep"};
}
export const PLOT_CFG = {displayModeBar: false, responsive: true};
