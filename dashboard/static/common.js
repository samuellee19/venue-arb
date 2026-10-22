// Shared state, constants, copy and formatting helpers for the dashboard modules (app.js, scanner.js, explorer.js).
// No edge, fee or basis logic here: every number comes from the bundle (dashboard/api/app.py serves it).
export const REPO = "https://github.com/samuellee19/venue-arb";
export const POLL_MS = 30000, STALE_S = 120, DEAD_S = 3600, BOOKS_S = 300;  // BOOKS_S = bundle.LIVE_MAX_AGE_S
export const US_BANNER = "No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference.";
export const STALE_BANNER = "last update {n} min ago";
export const NO_BOOKS = "no books in 5 min";
export const NOT_US = "Not executable from the US";
export const NOT_COMPUTED = "research summary not computed yet";
export const EXEC = "Executable edge";
// verdict label (prefix) -> Scanner badge tone; the labels venue_arb/bundle.verdict writes, plus the client-side US one
export const VERDICT_TONES = [["Exact match · no edge", "none"], [EXEC, "exec"], ["Not an arbitrage · basis exposure", "basis"],
  ["Spread · within basis bound · no edge", "none"], ["No asks on one side", "stale"],
  ["Stale · a leg has no snapshot in 5 min", "stale"], [NOT_US, "stale"]];
export const EXAMPLE = "Example — what a row looks like when a pair clears the rule";
export const FOOT = "{n} pairs — per-leg quotes are not published (venue display terms) — ROI = net edge ÷ all-in cost per contract — ";
export const FOOT_CHECKS = "{checks} admissible ladder checks in the last scored hour, {violations} violations";
export const FOOT_NO_HOUR = "no scored hour yet";

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
export const tone = c => c == null ? "mut" : c >= 1 ? "pos" : c < 0 ? "neg" : "";
export const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const pz = s => s.replace(/(Z|\+00:00)$/, "");  // Plotly ignores zones: strings stay UTC wall time
export const now = () => Date.now() + S.skew;
export const hm = d => d.toISOString().slice(11, 16) + "Z";
export const day = d => d.toLocaleDateString("en-GB", {day: "numeric", month: "short", timeZone: "UTC"});
export const seg = (box, on) => [...box.children].forEach(b => b.classList.toggle("on", b === on));
export const safeUrl = u => /^https:\/\/(kalshi|polymarket)\.com\//.test(u) ? u : "#";

export const pairs = () => S.bundle.pairs;
export const byKey = k => pairs().find(p => p.key === k);
export const usBlocked = () => S.us && !pairs().some(p => p.us_executable);
export const greyed = p => S.us && !p.us_executable;
export const label = p => greyed(p) ? NOT_US : p.verdict.label;
export const legsTxt = p => p.legs.map(l => `${l.venue === "kalshi" ? "K" : "PM"} ${esc(l.side)}`).join(" / ");

export function settles(iso) {  // v1's long form, for the Explorer
  const d = new Date(iso), ms = d - now();
  const when = iso.slice(0, 10) === new Date(now()).toISOString().slice(0, 10) ? hm(d) : day(d);
  if (ms <= 0) return `settled ${when}`;
  const m = Math.round(ms / 60000);
  return `settles ${when} · in ${m >= 2880 ? `${Math.round(m / 1440)} d` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} m`}`;
}
