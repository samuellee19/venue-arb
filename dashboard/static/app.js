// venue-arb dashboard: boot, load/poll of /api/live, tabs, header, theme, banners (dashboard/api/app.py serves it).
// No edge, fee or basis logic here: every number comes from the bundle. Plotly is the vendored global (Explorer).
import {$, esc, num, seg, byKey, S, REPO, POLL_MS, STALE_S, DEAD_S, BOOKS_S, STALE_BANNER, NO_BOOKS,
  NOT_COMPUTED} from "./common.js";
import {renderScanner} from "./scanner.js";

const TABS = ["scanner", "explorer", "research"];

async function load() {
  try {
    const res = await fetch("/api/live");
    if (!res.ok) throw new Error(res.status === 503 ? "no bundle published yet" : `HTTP ${res.status}`);
    const {bundle, age_s} = await res.json();
    S.bundle = bundle; S.age = age_s; S.fetchedAt = Date.now();
    S.skew = Date.parse(bundle.computed_at) + age_s * 1000 - Date.now();  // server clock, via age_s
    renderHeader(); renderScanner(); renderTab(); renderFoot(); tick();
  } catch (e) {
    $("clock").textContent = `cannot load the live bundle (${e.message}); retrying in 30 s`;
  }
}

function renderHeader() {
  const k = S.bundle.kpis;
  $("f-episodes").textContent = num(k.episodes_7d);  // the note's scoped count; the US toggle is a Scanner lens only
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
  $("stale").hidden = age <= STALE_S;
  $("stale").textContent = STALE_BANNER.replace("{n}", num(Math.round(age / 60)));
  $("figs").classList.toggle("dead", age > DEAD_S);
}

function renderTab() {
  const t = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "scanner";
  for (const x of TABS) {
    $(`tab-${x}`).hidden = x !== t;
    document.querySelector(`nav a[href="#${x}"]`).classList.toggle("on", x === t);
  }
  if (!S.bundle) return;
  // ponytail: placeholders until Task 6 wires explorer.js and adds research.js
  const p = S.sel && byKey(S.sel);
  $("tab-explorer").innerHTML = `<p class="mut">The Explorer is coming in the next task.${p ? ` Selected: ${esc(p.title)}` : ""}</p>`;
  $("tab-research").innerHTML = `<p class="mut">${S.bundle.research ? "The Research tab is coming in the next task." : NOT_COMPUTED}</p>`;
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
};
addEventListener("hashchange", renderTab);

renderTab();
load();
setInterval(load, POLL_MS);
setInterval(tick, 1000);
