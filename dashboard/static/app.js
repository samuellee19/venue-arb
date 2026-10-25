// venue-arb dashboard: boot, load/poll of /api/live, tabs, header, theme, banners (dashboard/api/app.py serves it).
// No edge, fee or basis logic here: every number comes from the bundle. Plotly is the vendored global (Explorer,
// Research). Showing a tab renders it; the poll refreshes the visible one; a theme switch re-draws its charts.
import {$, esc, num, seg, S, REPO, POLL_MS, STALE_S, DEAD_S, BOOKS_S, STALE_BANNER, DEAD_BANNER, NO_BOOKS} from "./common.js";
import {renderScanner} from "./scanner.js";
import {renderExplorer} from "./explorer.js";
import {renderResearch} from "./research.js";

const TABS = ["scanner", "explorer", "research"];
// what is on screen: a poll re-renders the Scanner rows only for a new bundle, the Research tab only for a new summary
// or theme (the Explorer guards itself); a rebuild on every poll would drop keyboard focus and re-draw the charts
const shown = {scanner: null, research: null};

async function load() {
  try {
    const res = await fetch("/api/live");
    if (!res.ok) throw new Error(res.status === 503 ? "no bundle published yet" : `HTTP ${res.status}`);
    const {bundle, age_s} = await res.json();
    S.bundle = bundle; S.age = age_s; S.fetchedAt = Date.now();
    S.skew = Date.parse(bundle.computed_at) + age_s * 1000 - Date.now();  // server clock, via age_s
    renderHeader();
    if (bundle.computed_at !== shown.scanner) { shown.scanner = bundle.computed_at; renderScanner(); }
    renderTab(); renderFoot(); tick();
  } catch (e) {
    $("clock").textContent = `cannot load the live bundle (${e.message}); retrying in 30 s`;
  }
}

function renderHeader() {
  const k = S.bundle.kpis;
  $("f-episodes").textContent = num(k.episodes_7d);  // the note's scoped count; the US toggle is a Scanner lens only
  const l = $("f-episodes-l");
  l.textContent = l.textContent.replace(/episodes?/, k.episodes_7d === 1 ? "episode" : "episodes");
  $("f-pairs").textContent = num(Object.values(k.pairs_live).reduce((a, b) => a + b, 0));  // market pairs
}

function tick() {
  if (!S.bundle) return;
  const age = S.age + (Date.now() - S.fetchedAt) / 1000;
  // the collector's newest snapshot, not computed_at: the publisher keeps ticking when the collector stops
  const books = S.bundle.collector_last_snapshot_at;
  const noBooks = !books || Date.parse(S.bundle.computed_at) - Date.parse(books) > BOOKS_S * 1000;
  $("clock").textContent = noBooks ? NO_BOOKS
    : `books ${new Date(books).toLocaleTimeString("en-US", {timeZone: "America/Los_Angeles", hour12: false})} PT`;
  $("dot").classList.toggle("stale", noBooks || age > STALE_S);
  const dead = age > DEAD_S;  // the verdicts and figures were true an hour ago: greyed, never hidden
  $("stale").hidden = age <= STALE_S;
  $("stale").textContent = (dead ? DEAD_BANNER : STALE_BANNER).replace("{n}", num(Math.round(age / 60)));
  $("figs").classList.toggle("dead", dead);
  document.body.classList.toggle("dead", dead);
}

function renderTab() {
  const t = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "scanner";
  for (const x of TABS) {
    $(`tab-${x}`).hidden = x !== t;
    const a = document.querySelector(`nav a[href="#${x}"]`);
    a.classList.toggle("on", x === t);
    if (x === t) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (!S.bundle) return;
  if (t === "explorer") renderExplorer();
  else if (t === "research") {
    const k = `${S.bundle.research?.computed_at}|${document.documentElement.dataset.theme}`;
    if (k !== shown.research) { shown.research = k; renderResearch(S.bundle); }
  }
}

function renderFoot() {
  const b = S.bundle;
  $("foot").innerHTML = `<ul>${[...b.attribution, ...b.caveats].map(x => `<li>${esc(x)}</li>`).join("")}</ul>`
    + `<span>Read-only. No orders are placed.</span>`
    + `<a href="${REPO}/blob/main/reports/research-note-v1.md">research note</a><a href="${REPO}">source on GitHub</a>`;
}

$("wordmark").href = REPO;
$("where").onclick = e => {
  if (!e.target.dataset.w || !S.bundle) return;
  S.us = e.target.dataset.w === "us"; seg($("where"), e.target); renderScanner();
};
$("theme").onclick = () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* private mode: not persisted */ }
  renderTab();  // Plotly reads the colour tokens at draw time
};
addEventListener("hashchange", renderTab);

renderTab();
load();
setInterval(load, POLL_MS);
setInterval(tick, 1000);
