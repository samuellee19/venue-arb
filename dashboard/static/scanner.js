// Scanner tab: one row per market pair (its better direction), sortable, filtered by category chip.
import {$, esc, num, usd, signed, bpsOf, tone, day, hm, now, seg, legsTxt, label, usBlocked, S, US_BANNER, EXEC, VERDICT_TONES,
  EXAMPLE, FOOT, FOOT_CHECKS, FOOT_NO_HOUR, FOOT_STOCK, NOT_ADVICE, NO_BPS} from "./common.js";

// numeric sort keys; nulls (stale, no asks, a perpetual's settlement) always sort last
const KEYS = {edge: p => p.net_edge_cents, roi: p => p.net_edge_roi, cap: p => p.capacity_usd,
  settles: p => p.settles_at ? Date.parse(p.settles_at) : null};
const STOCK_KEYS = {edge: p => p.net_bps, cap: p => p.capacity_usd, funding: p => p.funding_bps_h};  // the stock-perp header
const keysFor = cat => cat === "stock_perps" ? STOCK_KEYS : KEYS;
const isStock = p => p.kind === "stock_perp";
const stale = p => p.net_edge_cents == null;
const by = (f, dir) => (a, b) => {  // stale / no-asks rows last under every sort, then nulls of the key last
  const x = f(a), y = f(b);
  return stale(a) - stale(b) || (x == null ? (y == null ? 0 : 1) : y == null ? -1 : dir * (y - x));
};
const FIRST = {settles: -1};  // first click: soonest settlement first; numbers start descending

// rows: best direction per (Kalshi id, Polymarket id), filtered to cat, sorted (net edge desc by default);
// counts: rows per category plus "all"; example: true when no pair row (any category) carries the bundle's executable label
export function scannerRows(pairs, {cat = "all", sort = null} = {}) {
  const best = new Map();
  for (const p of pairs) {
    const id = `${p.legs[0].market_id}~${p.legs[1].market_id}`, q = best.get(id);
    if (!q || (p.net_edge_cents ?? -Infinity) > (q.net_edge_cents ?? -Infinity)) best.set(id, p);
  }
  const all = [...best.values()], counts = {all: all.length};
  for (const p of all) counts[p.category] = (counts[p.category] ?? 0) + 1;
  const keys = keysFor(cat), rows = all.filter(p => cat === "all" || p.category === cat)
    .sort(by(keys[sort?.col] ?? keys.edge, sort?.dir ?? 1));
  // a stock perp's executable label shares the EXEC prefix, but the example row shows a pair
  return {rows, counts, example: !all.some(p => !isStock(p) && p.verdict.label.startsWith(EXEC))};
}

const edge = (c, bps) => c == null ? "—" : S.unit === "c" ? signed(c, 1, "¢") : signed(bps ?? bpsOf(c), 0, " bps");
const short = id => id.length > 16 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
const badge = lab => `<span class="badge b-${VERDICT_TONES.find(([l]) => lab.startsWith(l))?.[1] ?? "none"}">${esc(lab)}</span>`;

function when(iso) {
  if (iso == null) return "perpetual";
  const h = (Date.parse(iso) - now()) / 3.6e6;
  const rel = h <= 0 ? "settled" : h >= 48 ? `in ${Math.round(h / 24)} d` : h >= 2 ? `in ${Math.round(h)} h`
    : `in ${Math.max(1, Math.round(h * 60))} m`;
  return `${day(new Date(iso))}<small>${rel}</small>`;
}

// stock perps are quoted in bps: the unit switch cannot show them in ¢, so a ¢ setting shows the same bps (titled)
const bps = (x, d = 0) => x == null ? "—" : signed(x, d, " bps");
const ref = p => !p.reference ? "—" : p.reference.state === "internal" ? "oracle internal"
  : `oracle${p.live ? ", live" : ", stale"} ${num(Math.round((Date.parse(S.bundle.computed_at) - Date.parse(p.reference.asof)) / 1000))}s`;

function row(p) {
  const t = tone(p.net_edge_cents, p), [k, pm] = p.legs, sp = isStock(p);
  return `<tr data-k="${esc(p.key)}" tabindex="0"${S.us && !p.us_executable ? ' class="grey"' : ""}>`
    + `<td><span class="cat">${esc(S.bundle.categories[p.category] ?? p.category)}</span><span class="title">${esc(p.title)}</span>`
    + `<small class="mono">${esc(k.market_id)} / ${esc(short(pm.market_id))}</small></td>`
    + `<td class="c-legs mut">${sp ? esc(p.trade) : legsTxt(p)}</td>`
    + (sp ? `<td class="num ${t}"${S.unit === "c" ? ` title="${NO_BPS}"` : ""}>${bps(p.net_bps, 1)}</td>`
      : `<td class="num ${t}">${edge(p.net_edge_cents, p.net_edge_bps)}</td>`)
    + `<td class="num ${t}">${p.net_edge_roi == null ? "—" : signed(100 * p.net_edge_roi, sp ? 2 : 1, "%")}</td>`
    + `<td class="num c-cap">${p.capacity_usd ? usd(p.capacity_usd) : "—"}</td>`
    + `<td class="when">${when(p.settles_at)}</td><td>${badge(label(p))}</td></tr>`;
}

// the stock-perp category's own columns: Name, Trade, Basis, Net, Funding, Capacity, Reference, Verdict
const STOCK_HEAD = `<tr><th scope="col">Name</th><th scope="col" class="c-legs">Trade</th><th scope="col" class="num">Basis</th>`
  + `<th scope="col" class="num" data-s="edge"><button>Net</button></th><th scope="col" class="num" data-s="funding"><button>Funding</button></th>`
  + `<th scope="col" class="num c-cap" data-s="cap"><button>Capacity</button></th><th scope="col">Reference</th><th scope="col">Verdict</th></tr>`;
const PAIR_HEAD = document.querySelector("#scan thead").innerHTML;

function stockRow(p) {
  const t = tone(p.net_edge_cents, p), f = p.next_funding_at;
  return `<tr data-k="${esc(p.key)}" tabindex="0" class="sp${S.us && !p.us_executable ? " grey" : ""}">`
    + `<td><span class="title">${esc(p.title)}</span><small class="mono">${esc(p.legs[0].market_id)}</small></td>`
    + `<td class="c-legs mut" data-l="trade">${esc(p.trade)}</td><td class="num" data-l="basis">${bps(p.basis_bps, 1)}</td><td class="num ${t}" data-l="net">${bps(p.net_bps, 1)}</td>`
    + `<td class="num" data-l="funding">${p.funding_bps_h == null ? "—" : signed(p.funding_bps_h, 2, " bps/h")}`
    + `${f ? `<small>next ${hm(new Date(f))}</small>` : ""}</td>`
    + `<td class="num c-cap" data-l="capacity">${p.capacity_usd ? usd(p.capacity_usd) : "—"}</td><td class="mut" data-l="reference">${esc(ref(p))}</td>`
    + `<td>${badge(label(p))}</td></tr>`;
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
  const sp = S.cat === "stock_perps";  // only this category gets the stock-perp header; "All" keeps the pair header
  const head = document.querySelector("#scan thead"), want = sp ? STOCK_HEAD : PAIR_HEAD;
  if (head.dataset.t !== (sp ? "sp" : "pair")) { head.innerHTML = want; head.dataset.t = sp ? "sp" : "pair"; }
  const cBtn = $("unit").querySelector('[data-u="c"]');
  cBtn.disabled = sp; cBtn.title = sp ? NO_BPS : "";
  seg($("unit"), $("unit").querySelector(`[data-u="${sp ? "bps" : S.unit}"]`));
  $("srows").innerHTML = (rows.map(sp ? stockRow : row).join("")
    || `<tr><td colspan="${sp ? 8 : 7}" class="mut">No pair in this category.</td></tr>`) + (example && !sp ? exampleRow() : "");
  for (const th of document.querySelectorAll("#scan th[data-s]")) {
    const on = (S.sort?.col ?? "edge") === th.dataset.s;
    th.setAttribute("aria-sort", on ? ((S.sort?.dir ?? 1) > 0 ? "descending" : "ascending") : "none");
  }
  const n = rows.length;
  $("sfoot").textContent = sp ? `${FOOT_STOCK.replace("{n} stock perps", n === 1 ? "1 stock perp" : `${num(n)} stock perps`)}. ${NOT_ADVICE}`
    : FOOT.replace("{n} pairs", n === 1 ? "1 pair" : `${num(n)} pairs`) + (k.admissible_checks == null
      ? FOOT_NO_HOUR : FOOT_CHECKS.replace("{checks}", num(k.admissible_checks)).replace("{violations}", num(k.violations ?? 0)))
      + (rows.some(isStock) ? `. ${NOT_ADVICE}` : "");
}

$("chips").onclick = e => {
  const c = e.target.closest("[data-c]");
  if (c && S.bundle) {
    S.cat = c.dataset.c;
    if (S.sort && !(S.sort.col in keysFor(S.cat))) S.sort = null;  // a column the other header lacks
    renderScanner();
  }
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
