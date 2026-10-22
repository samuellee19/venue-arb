// Scanner tab: one row per market pair (its better direction), sortable, filtered by category chip.
import {$, esc, num, usd, signed, bpsOf, tone, day, now, seg, legsTxt, label, usBlocked, S, US_BANNER, EXEC, VERDICT_TONES,
  EXAMPLE, FOOT, FOOT_CHECKS, FOOT_NO_HOUR} from "./common.js";

// numeric sort keys; nulls (stale, no asks) always sort last
const KEYS = {edge: p => p.net_edge_cents, roi: p => p.net_edge_roi, cap: p => p.capacity_usd, settles: p => Date.parse(p.settles_at)};
const stale = p => p.net_edge_cents == null;
const by = (f, dir) => (a, b) => {  // stale / no-asks rows last under every sort, then nulls of the key last
  const x = f(a), y = f(b);
  return stale(a) - stale(b) || (x == null ? (y == null ? 0 : 1) : y == null ? -1 : dir * (y - x));
};
const FIRST = {settles: -1};  // first click: soonest settlement first; numbers start descending

// rows: best direction per (Kalshi id, Polymarket id), filtered to cat, sorted (net edge desc by default);
// counts: rows per category plus "all"; example: true when no row (any category) carries the bundle's executable label
export function scannerRows(pairs, {cat = "all", sort = null} = {}) {
  const best = new Map();
  for (const p of pairs) {
    const id = `${p.legs[0].market_id}~${p.legs[1].market_id}`, q = best.get(id);
    if (!q || (p.net_edge_cents ?? -Infinity) > (q.net_edge_cents ?? -Infinity)) best.set(id, p);
  }
  const all = [...best.values()], counts = {all: all.length};
  for (const p of all) counts[p.category] = (counts[p.category] ?? 0) + 1;
  const rows = all.filter(p => cat === "all" || p.category === cat)
    .sort(by(KEYS[sort?.col ?? "edge"], sort?.dir ?? 1));
  return {rows, counts, example: !all.some(p => p.verdict.label.startsWith(EXEC))};
}

const edge = (c, bps) => c == null ? "—" : S.unit === "c" ? signed(c, 1, "¢") : signed(bps ?? bpsOf(c), 0, " bps");
const short = id => id.length > 16 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
const badge = lab => `<span class="badge b-${VERDICT_TONES.find(([l]) => lab.startsWith(l))?.[1] ?? "none"}">${esc(lab)}</span>`;

function when(iso) {
  const h = (Date.parse(iso) - now()) / 3.6e6;
  const rel = h <= 0 ? "settled" : h >= 48 ? `in ${Math.round(h / 24)} d` : `in ${Math.max(1, Math.round(h))} h`;
  return `${day(new Date(iso))}<small>${rel}</small>`;
}

function row(p) {
  const t = tone(p.net_edge_cents), [k, pm] = p.legs;
  return `<tr data-k="${esc(p.key)}" tabindex="0"${S.us && !p.us_executable ? ' class="grey"' : ""}>`
    + `<td><span class="cat">${esc(S.bundle.categories[p.category] ?? p.category)}</span><span class="title">${esc(p.title)}</span>`
    + `<small class="mono">${esc(k.market_id)} / ${esc(short(pm.market_id))}</small></td>`
    + `<td class="c-legs mut">${legsTxt(p)}</td>`
    + `<td class="num ${t}">${edge(p.net_edge_cents, p.net_edge_bps)}</td>`
    + `<td class="num ${t}">${p.net_edge_roi == null ? "—" : signed(100 * p.net_edge_roi, 1, "%")}</td>`
    + `<td class="num c-cap">${p.capacity_usd ? usd(p.capacity_usd) : "—"}</td>`
    + `<td class="when">${when(p.settles_at)}</td><td>${badge(label(p))}</td></tr>`;
}

// invented round numbers, never a live pair: not clickable, not counted, not sorted
const exampleRow = () => `<tr class="example" data-example><td><span class="title">${EXAMPLE}</span></td>`
  + `<td class="c-legs mut">K yes / PM no</td><td class="num pos">${edge(1.4)}</td><td class="num pos">+1.4%</td>`
  + `<td class="num c-cap">$620</td><td class="mut">—</td><td>${badge(EXEC)}</td></tr>`;

export function renderScanner() {
  const b = S.bundle, k = b.kpis, {rows, counts, example} = scannerRows(b.pairs, S);
  const cats = Object.keys({...b.categories, ...counts}).filter(c => c !== "all" && counts[c]);  // only categories present
  $("chips").innerHTML = [["all", "All"], ...cats.map(c => [c, b.categories[c] ?? c])]
    .map(([c, name]) => `<button class="chip${c === S.cat ? " on" : ""}" data-c="${esc(c)}">${esc(name)} <span>${counts[c]}</span></button>`).join("");
  $("usban").hidden = !usBlocked();
  $("usban").textContent = US_BANNER;
  $("srows").innerHTML = (rows.map(row).join("") || `<tr><td colspan="7" class="mut">No pair in this category.</td></tr>`)
    + (example ? exampleRow() : "");
  for (const th of document.querySelectorAll("#scan th[data-s]")) {
    const on = (S.sort?.col ?? "edge") === th.dataset.s;
    th.setAttribute("aria-sort", on ? ((S.sort?.dir ?? 1) > 0 ? "descending" : "ascending") : "none");
  }
  const n = rows.length;
  $("sfoot").textContent = FOOT.replace("{n} pairs", n === 1 ? "1 pair" : `${num(n)} pairs`) + (k.admissible_checks == null
    ? FOOT_NO_HOUR : FOOT_CHECKS.replace("{checks}", num(k.admissible_checks)).replace("{violations}", num(k.violations ?? 0)));
}

$("chips").onclick = e => {
  const c = e.target.closest("[data-c]");
  if (c && S.bundle) { S.cat = c.dataset.c; renderScanner(); }
};
$("unit").onclick = e => {
  if (!e.target.dataset.u || !S.bundle) return;
  S.unit = e.target.dataset.u; seg($("unit"), e.target); renderScanner();
};
document.querySelector("#scan thead").onclick = e => {
  const col = e.target.closest("[data-s]")?.dataset.s;
  if (!col || !S.bundle) return;
  const cur = S.sort ?? {col: "edge", dir: 1};
  S.sort = {col, dir: cur.col === col ? -cur.dir : FIRST[col] ?? 1};
  renderScanner();
};
const open = tr => { if (tr?.dataset.k) { S.sel = tr.dataset.k; location.hash = "#explorer"; } };
$("srows").onclick = e => open(e.target.closest("tr"));
$("srows").onkeydown = e => { if (e.key === "Enter") open(e.target.closest("tr")); };
