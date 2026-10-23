// Explorer tab: the v1 pair list (collapsed ladder groups) and detail pane (verdict, cost panel, size re-walk, net-edge
// chart with snapshot click), moved from the v1 app.js; behaviour unchanged, re-skinned to the v2 system.
// app.js calls renderExplorer() when the tab is shown and on each poll while it is visible.
import {$, esc, enc, num, usd, signed, cents, bpsOf, tone, css, pz, hm, seg, safeUrl, pairs, byKey,
  legsTxt, settles, baseLayout, S, VERDICT_TONES, PLOT_CFG, NOT_ADVICE, STOCK_MIN_BPS} from "./common.js";

const NO_WALK = "size re-walk needs published books";
const KIND = {exact: "exact", near_exact: "near-exact", ladder: "ladder", stock_perp: "stock perp"};
const CHECK = {same_instant: "Same instant", exact_match: "Exact match", basis_bound: "Basis bound",
  fees_depth: "Fees & depth walked", jurisdiction: "Jurisdiction", reference: "Reference", fees_carry: "Fees, funding & borrow"};
const VENUE = {kalshi: ["Kalshi", "kalshi.com"], polymarket: ["Polymarket", "polymarket.com"], hyperliquid: ["Hyperliquid", "app.hyperliquid.xyz"]};
const isStock = p => p.kind === "stock_perp";
const bps = (x, d = 1) => x == null ? "—" : signed(x, d, " bps");
const ICON = {ok: ["✓", "pos"], fail: ["✗", "neg"], partial: ["◐", "warn"]};

let hist = [];  // [{key, h}] history of the selected row and its other-direction sibling
let walkTimer;
let shown = {};  // the pair and bundle (computed_at) the detail pane was last built or refreshed for

// v1 load(): a missing selection takes the default; a new bundle refreshes the detail. New in v2: a selection made on
// the Scanner (S.sel set while this tab was hidden) is built here; an unchanged pane only re-draws (theme, resize).
export function renderExplorer() {
  const b = S.bundle;
  $("size").disabled = !b.show_books;
  $("size").title = $("sizebox").title = b.show_books ? "" : NO_WALK;
  if (!S.sel || !byKey(S.sel)) select(defaultSel());
  else if (S.sel !== shown.sel) {  // picked on the Scanner: open its ladder group so the row shows
    if (byKey(S.sel).kind === "ladder") S.open.add(byKey(S.sel).ladder);
    select(S.sel);
  }
  else { renderList(); if (b.computed_at !== shown.at) refreshDetail(); else drawChart(); }
  shown = {sel: S.sel, at: b.computed_at};
}

// an edge in the chosen unit, the other unit small beneath (or beside, inside the cost box); a stock perp is bps only
function fig(c, bpsVal, p) {
  if (p && isStock(p)) return c == null ? `<span class="mut">—</span> <small>not scored</small>` : bps(p.net_bps);
  if (c == null) return `<span class="mut">—</span> <small>not scored</small>`;
  const a = signed(c, 1, "¢"), b = signed(bpsVal ?? bpsOf(c), 0, " bps");
  return S.unit === "c" ? `${a} <small>${b}</small>` : `${b} <small>${a}</small>`;
}

function defaultSel() {
  const best = S.bundle.kpis.best_edge?.key;
  return best && byKey(best) ? best : flatRows(pairs())[0]?.key ?? pairs()[0]?.key;
}

// ---- pair list ----
const byEdge = (a, b) => (b.net_edge_cents ?? -1e9) - (a.net_edge_cents ?? -1e9);
const flatRows = ps => ps.filter(p => p.kind !== "ladder" || p.net_edge_cents >= 1).sort(byEdge);

function renderList() {
  const q = S.q.trim().toLowerCase();
  const ps = pairs().filter(p => !q || [p.title, p.key, ...p.legs.map(l => l.title)].join(" ").toLowerCase().includes(q));
  let html = flatRows(ps).map(row).join("");
  const groups = new Map();
  for (const p of ps.filter(p => p.kind === "ladder").sort(byEdge)) {
    if (!groups.has(p.ladder)) groups.set(p.ladder, []);
    groups.get(p.ladder).push(p);
  }
  for (const [lad, rs] of groups) {
    const [asset, at] = lad.split("~"), open = S.open.has(lad);
    const strikes = new Set(rs.map(p => p.legs[0].market_id)).size;
    html += `<div class="grp" data-g="${esc(lad)}">${open ? "▾" : "▸"} ${esc(asset)} ${hm(new Date(at))}, ${strikes} strike${strikes === 1 ? "" : "s"}</div>`;
    if (open) html += rs.map(row).join("");
  }
  $("rows").innerHTML = html || `<div class="empty">No pair matches.</div>`;
}

function row(p) {
  return `<div class="row${p.key === S.sel ? " sel" : ""}" data-k="${esc(p.key)}">`
    + `<div><div class="n">${esc(p.title)} <span class="kind k-${p.kind}">${KIND[p.kind]}</span></div>`
    + `<div class="m"><span>${settles(p.settles_at)}</span><span>${legsTxt(p)}</span>${p.live ? "" : "<span>stale</span>"}</div></div>`
    + `<div class="e ${tone(p.net_edge_cents, p)}">${fig(p.net_edge_cents, p.net_edge_bps, p)}</div>${spark(p)}</div>`;
}

function spark(p) {
  const v = p.spark, ys = v.filter(x => x != null);
  if (!ys.length) return `<svg class="spark"></svg>`;
  const thr = isStock(p) ? STOCK_MIN_BPS / 100 : 1;  // the stock spark is net bps / 100, so its threshold is 0.1
  const up = isStock(p) ? p.net_bps >= STOCK_MIN_BPS : p.net_edge_cents > 0;
  const lo = Math.min(...ys, 0), hi = Math.max(...ys, thr), Y = x => (16 - (x - lo) / (hi - lo) * 14).toFixed(1);
  let d = "", pen = false;
  v.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${(i * 300 / (v.length - 1)).toFixed(1)},${Y(x)}`;
    pen = true;
  });
  return `<svg class="spark" viewBox="0 0 300 18" preserveAspectRatio="none">`
    + `<line x1="0" x2="300" y1="${Y(thr)}" y2="${Y(thr)}" style="stroke:var(--neg)" stroke-opacity=".35" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`
    + `<path d="${d}" fill="none" style="stroke:var(${up ? "--pos" : "--mut"})" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>`;
}

function select(key) {
  S.sel = key; S.snap = null; S.walk = null;
  shown = {sel: key, at: S.bundle.computed_at};
  renderList();
  buildDetail();
}

// ---- pair detail ----
function buildDetail() {
  const old = $("plot");
  if (old && window.Plotly) Plotly.purge(old);  // drops the graph and its window resize handler before the node goes
  if (!S.sel) { $("detail").innerHTML = `<div class="empty">No pairs in this bundle.</div>`; return; }
  const sp = isStock(byKey(S.sel));
  $("sizebox").hidden = sp;  // a stock perp has no size re-walk (/api/walk is 409)
  const ranges = ["1h", "24h", "7d", "all"].map(r => `<button data-r="${r}"${r === S.range ? ' class="on"' : ""}>${r.replace(/(\d+)/, "$1 ")}</button>`);
  $("detail").innerHTML = `<div id="dhead"></div><div id="boxes"></div>`
    + `<div class="chart"><div class="tools"><h4>${sp ? "Net and basis, bps of notional, UTC" : "Net edge after fees, ¢ per contract, UTC"}</h4>`
    + `<span class="seg" id="range">${ranges.join("")}</span><button class="chip${S.ann ? " on" : ""}" id="ann">annotations</button></div>`
    + `<div id="plot"></div><div class="legend">${sp
      ? `<span>dashed red: +${STOCK_MIN_BPS} bps net threshold</span><span>amber band: net not scored</span><span>grey band: collector gap</span>`
      : `<span>dashed red: +1¢ episode threshold</span><span>dotted: settlement, listing, basis change</span><span>shaded: collector gap</span><span>▲ episode</span>`
        + `<span>${S.bundle.show_books ? "hover for the books" : "hover for the row's values"}</span>`}<span>click a point to load that snapshot</span></div></div>`
    + `<div class="why" id="why"></div>`;
  $("range").onclick = e => {
    if (!e.target.dataset.r) return;
    S.range = e.target.dataset.r; seg($("range"), e.target); loadChart();
  };
  $("ann").onclick = () => { S.ann = !S.ann; $("ann").classList.toggle("on", S.ann); drawChart(); };
  $("boxes").onclick = e => { if (e.target.id === "golive") { S.snap = null; renderBoxes(); } };
  refreshDetail();
}

function refreshDetail() {
  if (!$("dhead")) return buildDetail();
  renderHead(); renderBoxes(); renderWhy(); loadChart(); walk();
}

function renderHead() {
  const p = byKey(S.sel), lab = p.verdict.label, t = VERDICT_TONES.find(([l]) => lab.startsWith(l))?.[1] ?? "none";
  const legs = p.legs.map(l => {
    const k = l.venue === "kalshi", sp = isStock(p), [name, host] = VENUE[l.venue] ?? [`Stock (${esc(l.venue.toUpperCase())})`, ""];
    const id = k || sp ? l.market_id : l.url?.split("/event/")[1] || l.market_id;
    const body = `<span class="sw" style="background:var(--${l.venue === "polymarket" ? "pm" : l.url ? "k" : "mut"})"></span>`
      + `${name}, ${sp ? esc(l.side) : `buy ${esc(l.side.toUpperCase())}`} <span class="id">${esc(id)}</span>`;
    return l.url  // a stock leg has no page: no link
      ? `<a class="leg" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${body} <span class="ext">↗ ${host}</span></a>`
      : `<span class="leg">${body}</span>`;
  }).join("");
  const src = p.legs.map(l => l.settlement_source).filter(Boolean);
  const checks = p.verdict.checks.map(c => `<span>${CHECK[c.name].toLowerCase()} ${ICON[c.status][0]}</span>`).join("");
  $("dhead").innerHTML = `<div class="title"><div><h2>${esc(p.title)}</h2><div class="legs">${legs}`
    + (src.length ? `<span class="leg mut">settles: ${src.map(esc).join(" ↔ ")}</span>` : "") + `</div></div>`
    + `<div class="verdict"><div class="big badge b-${t}">${esc(lab)}</div><div class="cd">${checks}<span>${settles(p.settles_at)}</span></div></div></div>`;
}

function renderWhy() {
  $("why").innerHTML = byKey(S.sel).verdict.checks.map(c => {
    const [i, cl] = ICON[c.status];
    return `<div class="c"><b class="${cl}">${i} ${CHECK[c.name]}</b><span>${esc(c.detail)}</span></div>`;
  }).join("");
}

const r = (a, b, cls = "") => `<div class="r ${cls}"><span>${a}</span><span class="mono">${b}</span></div>`;

function book(venue, side, levels, filled, empty = "no asks") {
  const k = venue === "kalshi", max = Math.max(...levels.map(l => l.size), 1);
  let cum = 0;
  const rows = levels.map(l => {
    const hit = filled != null && cum < filled;
    cum += l.size;
    return `<div class="lvl${hit ? " hit" : ""}"><span class="mono">${cents(100 * l.price)}</span><div class="meter">`
      + `<i style="width:${100 * l.size / max}%;background:var(--${k ? "k" : "pm"}${hit ? "" : "2"})"></i></div>`
      + `<span class="mono" style="text-align:right">${num(l.size)}</span></div>`;
  }).join("");
  return `<div class="box"><h4><span class="sw" style="background:var(--${k ? "k" : "pm"})"></span>${k ? "Kalshi" : "Polymarket"}, buy ${esc(side.toUpperCase())}</h4>`
    + `${rows || `<div class="note">${empty}</div>`}<div class="legend"><span>contracts at each ask</span></div></div>`;
}

const capRow = p => r("Capacity at ≥ 1¢ net", `${num(p.capacity_contracts)} contracts, ${usd(p.capacity_usd)}`);

function costWalk(p) {  // books published: the fill at the chosen size from /api/walk
  const w = S.walk, [kl, pl] = p.legs;
  if (!w || w.key !== p.key) return `<div class="note">walking both books at ${num(S.size)} contracts…</div>`;
  if (w.err) return `<div class="note">${esc(w.err)}</div>${capRow(p)}`;
  const f = w.res;
  if (!f.filled) return `<div class="note">no visible depth on one side</div>${capRow(p)}`;
  const pnl = f.net_cents * f.filled / 100;
  return r(`Kalshi ${kl.side.toUpperCase()} ask (avg over ${num(f.filled)})`, cents(100 * f.k_avg_price))
    + r(`Polymarket ${pl.side.toUpperCase()} ask (avg over ${num(f.filled)})`, cents(100 * f.pm_avg_price))
    + r(`<span class="mut">Fees, both venues</span>`, `<span class="mut">+${cents(100 * f.fees_usd / f.filled)}, ${usd(f.fees_usd)}</span>`)
    + r(`All-in, ${num(f.filled)} contracts`, `<span class="${tone(f.net_cents)}">${cents(100 * f.cost_usd / f.filled)} → ${fig(f.net_cents, f.net_bps)}, <b>${usd(pnl)}</b></span>`, "t")
    + (f.insufficient_depth ? r(`<span class="warn">Insufficient visible depth</span>`, `<span class="warn">filled ${num(f.filled)} of ${num(f.requested)}</span>`) : "")
    + r(`<span class="mut">Cross-venue transfer: $10 + 10 bps</span>`, `<span class="mut">${usd(-f.transfer_cost_usd)}</span>`)
    + r("P&amp;L at size after transfer", `<span class="${pnl - f.transfer_cost_usd >= 0 ? "pos" : "neg"}">${usd(pnl - f.transfer_cost_usd)}</span>`, "t")
    + capRow(p);
}

function costDerived(p) {  // books hidden: derived numbers only, no per-leg price or size
  const c = p.net_edge_cents;
  if (c == null) return r("Not scored", esc(p.verdict.checks.find(x => x.name === "fees_depth")?.detail ?? "")) + capRow(p);
  return r("All-in cost per contract (both asks + fees)", cents(100 - c))
    + r(`<span class="mut">Fees, both venues</span>`, `<span class="mut">+${cents(p.fees_cents)}</span>`)
    + r("Net vs the $1 payout", `<span class="${tone(c)}">${fig(c, p.net_edge_bps)}</span>`, "t")
    + capRow(p);
}

function costSnap(q, s) {  // a history row: first ask level only
  const [kl, pl] = q.legs;
  return (s.k_ask != null ? r(`Kalshi ${kl.side.toUpperCase()} ask`, cents(100 * s.k_ask)) : "")
    + (s.pm_ask != null ? r(`Polymarket ${pl.side.toUpperCase()} ask`, cents(100 * s.pm_ask)) : "")
    + (s.net_edge_cents == null ? r("Not scored at this snapshot", "—")
      : (s.fees_cents != null ? r(`<span class="mut">Fees, both venues</span>`, `<span class="mut">+${cents(s.fees_cents)}</span>`) : "")
        + r("All-in cost per contract", cents(100 - s.net_edge_cents))
        + r("Net vs the $1 payout", `<span class="${tone(s.net_edge_cents)}">${fig(s.net_edge_cents)}</span>`, "t"))
    + r("Capacity at ≥ 1¢ net", s.capacity_usd == null ? "—" : usd(s.capacity_usd));
}

// ---- stock perps: the cost table prints the bundle's cost lines (row.costs); net is at the walked fill, not basis minus lines ----
const bp0 = x => Math.abs(x) < 0.05 ? "0.0 bps" : bps(x);
const ageS = p => Math.round((Date.parse(S.bundle.computed_at) - Date.parse(p.reference.asof)) / 1000);
const refTxt = p => !p.reference ? "—" : p.reference.state === "internal" ? "oracle internal, market closed"
  : `oracle${p.live ? ", live" : ", stale"}, ${num(ageS(p))} s old`;

function costStock(p) {
  const rows = [r("Trade", esc(p.trade)), r("Reference", refTxt(p))];
  if (p.net_bps == null) {
    const c = p.verdict.checks.find(x => x.name === "fees_carry");
    return rows.join("") + r("Not scored", esc(c?.detail ?? ""));
  }
  const c = p.costs, s = p.sensitivity, cost = x => x == null ? "—" : bp0(-x);  // the bundle's cost lines, shown as costs
  return rows.join("") + r("Basis, mark vs reference", bps(p.basis_bps))
    + r("2× perp taker fee", cost(c.perp_fee_bps)) + r("Perp spread (exit)", cost(c.perp_spread_bps))
    + r(`Funding over ${p.hold_hours} h`, cost(c.funding_bps))
    + r("Net, before stock-leg costs", `<span class="${tone(p.net_edge_cents, p)}">${bps(p.net_bps)}</span>`, "t")
    + r("Net at 1 h / 7 d / with borrow", `${bps(s.net_bps_1h)} / ${bps(s.net_bps_7d)} / ${bps(s.net_bps_with_borrow)}`)
    + r("Perp depth while net ≥ 10 bps", usd(p.capacity_usd))
    + `<div class="note">The stock leg is not modelled on this page.</div>`;
}

function costStockSnap(s) {  // a history row of a stock perp
  return r("Basis, mark vs reference", bps(s.basis_bps)) + r("Net, before stock-leg costs", bps(s.net_bps), "t")
    + r("Capacity", s.capacity_usd == null ? "—" : usd(s.capacity_usd));
}

function renderBoxes() {
  const p = byKey(S.sel), show = S.bundle.show_books, el = $("boxes");
  if (!p || !el) return;
  const mid = (h, body, note = "") => `<div class="box"><h4>${h}</h4><div class="wf">${body}</div>${note}</div>`;
  if (isStock(p)) {
    const adv = `<div class="note">${NOT_ADVICE}</div>`;
    el.className = "grid3 one";
    el.innerHTML = S.snap
      ? mid("Derived at this snapshot", costStockSnap(S.snap.row),
        `<div class="note">history: snapshot ${S.snap.row.ts.slice(11, 19)}Z <button id="golive">back to live</button></div>${adv}`)
      : mid(`Cost of the trade, bps of notional, ${p.hold_hours} h hold`, costStock(p), adv);
    return;
  }
  if (S.snap) {
    const q = byKey(S.snap.key) ?? p, s = S.snap.row;
    const note = `<div class="note">history: first ask level only, snapshot ${s.ts.slice(11, 19)}Z, ${legsTxt(q)} <button id="golive">back to live</button></div>`;
    const lv = (a, z) => a == null ? [] : [{price: a, size: z ?? 0}];
    el.className = show ? "grid3" : "grid3 one";
    el.innerHTML = show
      ? book("kalshi", q.legs[0].side, lv(s.k_ask, s.k_size), undefined, "no ask in this history row") + mid("Cost of both legs, per contract at this snapshot", costSnap(q, s), note)
        + book("polymarket", q.legs[1].side, lv(s.pm_ask, s.pm_size), undefined, "no ask in this history row")
      : mid("Derived at this snapshot, quotes not published", costSnap(q, s), note);
  } else if (show && p.books) {
    const filled = S.walk?.key === p.key ? S.walk.res?.filled : undefined;
    el.className = "grid3";
    el.innerHTML = book("kalshi", p.legs[0].side, p.books[0], filled)
      + mid(`Cost of both legs, per contract and at <span class="mono" style="color:var(--txt)">${num(S.size)}</span>`, costWalk(p))
      + book("polymarket", p.legs[1].side, p.books[1], filled);
  } else {
    el.className = "grid3 one";
    el.innerHTML = mid("Cost of both legs, per contract, first slice", costDerived(p),
      `<div class="note">Per-leg prices and sizes are not published (venue display terms, docs/venues.md); ${NO_WALK}.</div>`);
  }
}

function walk() {
  clearTimeout(walkTimer);
  const p = byKey(S.sel);
  if (!S.bundle.show_books || !p?.books) { S.walk = null; return; }
  walkTimer = setTimeout(async () => {
    const key = p.key, size = S.size;
    try {
      const res = await fetch(`/api/walk?key=${enc(key)}&size=${size}`), j = await res.json();
      S.walk = res.ok ? {key, res: j} : {key, err: res.status === 409 ? NO_WALK
        : typeof j.detail === "string" ? j.detail : "size must be between 1 and 1,000,000 contracts"};
    } catch (e) {
      S.walk = {key, err: `walk failed: ${e.message}`};
    }
    if (S.sel === key && S.size === size) renderBoxes();
  }, 300);
}

// ---- chart ----
async function loadChart() {
  const p = byKey(S.sel), base = p.key.slice(0, p.key.lastIndexOf("~") + 1);
  // same two markets, other direction; a stock perp has one row a name, keyed "perp~stock~stock_perp" whichever
  // direction it holds, so no sibling and a direction flip keeps its history
  const sib = isStock(p) ? null : pairs().find(q => q.key !== p.key && q.key.startsWith(base));
  const keys = [p, sib].filter(Boolean).map(q => q.key), sel = S.sel, range = S.range;
  const res = await Promise.all(keys.map(k => fetch(`/api/pair/${enc(k)}/history?range=${range}`)
    .then(x => x.ok ? x.json() : {}).catch(() => ({}))));
  if (S.sel !== sel || S.range !== range) return;  // a newer selection owns the chart
  hist = keys.map((key, i) => ({key, h: res[i]}));
  drawChart();
}

function hover(h, j, stock) {
  const v = c => h[c]?.[j], net = v("net_edge_cents");
  if (stock) return `<b>${h.ts[j].slice(0, 16).replace("T", " ")} UTC</b><br>basis ${bps(v("basis_bps"))}, net ${bps(v("net_bps"))}`
    + `, capacity ${v("capacity_usd") == null ? "—" : usd(v("capacity_usd"))}<br><i>click to load this snapshot</i>`;
  const ask = (n, a, z) => a == null ? "" : `${n} ask ${cents(100 * a)} ×${z == null ? "?" : num(z)}`;
  const asks = [ask("K", v("k_ask"), v("k_size")), ask("PM", v("pm_ask"), v("pm_size"))].filter(Boolean).join(", ");
  return `<b>${h.ts[j].slice(0, 16).replace("T", " ")} UTC</b><br>${asks || "asks not published"}<br>`
    + `fees ${v("fees_cents") == null ? "—" : cents(v("fees_cents"))}, net ${net == null ? "—" : signed(net, 2, "¢")}`
    + `, capacity ≥ 1¢: ${v("capacity_usd") == null ? "—" : usd(v("capacity_usd"))}<br><i>click to load this snapshot</i>`;
}

function drawChart() {
  const el = $("plot");
  if (!el || !window.Plotly) return;
  const mut = css("--mut"), colors = [css("--pos"), mut], sel = byKey(S.sel), sp = isStock(sel);
  const traces = hist.filter(t => t.h.ts?.length).map((t, i) => ({
    type: "scatter", mode: "lines+markers", marker: {size: 3}, line: {color: colors[i], width: i ? 1.3 : 1.8},
    name: sp ? "net" : `net edge, ${legsTxt(byKey(t.key) ?? sel).replace(" / ", " + ")}`,
    x: t.h.ts.map(pz), y: sp ? t.h.net_bps : t.h.net_edge_cents, customdata: t.h.ts.map(ts => [t.key, ts]),
    text: t.h.ts.map((_, j) => hover(t.h, j, sp)), hovertemplate: "%{text}<extra></extra>"}));
  const cur = hist[0]?.key === S.sel ? hist[0].h : null;  // not a previous selection's history
  if (sp && cur?.ts?.length) {  // the basis itself, dotted, on the same axis (the first hist entry is the selected row)
    traces.push({type: "scatter", mode: "lines", line: {color: mut, width: 1.3, dash: "dot"}, name: "basis, mark vs reference",
      x: cur.ts.map(pz), y: cur.basis_bps, customdata: cur.ts.map(ts => [hist[0].key, ts]),
      text: cur.ts.map((_, j) => hover(cur, j, true)), hovertemplate: "%{text}<extra></extra>"});
  }
  if (!traces.length) {
    Plotly.purge(el);
    delete el.dataset.bound;
    el.innerHTML = `<div class="empty">No history for this pair in the ${S.range} range yet.</div>`;
    return;
  }
  el.querySelector(":scope > .empty")?.remove();
  const xs = traces.flatMap(t => t.x).sort(), x0 = xs[0], x1 = xs.at(-1), keys = hist.map(t => t.key);
  const thr = sp ? STOCK_MIN_BPS : 1;
  const shapes = [{type: "line", xref: "paper", x0: 0, x1: 1, y0: thr, y1: thr, line: {color: css("--neg"), width: 1, dash: "dash"}}];
  const notes = [{xref: "paper", x: 0, y: thr, yanchor: "bottom", xanchor: "left", text: sp ? `+${thr} bps` : "+1¢", showarrow: false, font: {size: 13, color: css("--neg")}}];
  if (sp && cur?.ts?.length) {  // net not scored (a stale leg or no reference): a band from the first null to the next scored row
    let from = null;
    cur.ts.forEach((t, j) => {
      const none = cur.net_bps[j] == null;
      if (none && from === null) from = t;
      if (from !== null && (!none || j === cur.ts.length - 1)) {
        shapes.push({type: "rect", xref: "x", yref: "paper", x0: pz(from), x1: pz(t), y0: 0, y1: 1, fillcolor: css("--warn"), opacity: 0.15, line: {width: 0}});
        from = null;
      }
    });
  }
  if (S.ann) {
    let n = 0;
    const tag = (x, text, color = mut) => notes.push({x, y: 1 - 0.08 * (n++ % 4), xref: "x", yref: "paper", showarrow: false,
      text: esc(text.length > 34 ? text.slice(0, 33) + "…" : text), hovertext: esc(text), xanchor: "left", yanchor: "top", font: {size: 13, color}});
    // global notes (key null) only where they apply: settlement at this pair's instant, basis change on its ladder's asset
    const p = sel, asset = p.ladder?.split("~")[0];
    const mine = a => a.key != null ? keys.includes(a.key) : sp ? a.kind === "collector_gap"  // the pair notes do not apply
      : a.kind === "settlement" ? Date.parse(a.at) === Date.parse(p.settles_at)
      : a.kind === "basis_change" ? asset != null && a.label.startsWith(`${asset} `) : true;
    for (const a of S.bundle.annotations.filter(mine)) {
      const at = pz(a.at), until = pz(a.until ?? a.at);
      if (until < x0 || at > x1) continue;
      if (a.kind === "collector_gap") {
        shapes.push({type: "rect", xref: "x", yref: "paper", x0: at, x1: until, y0: 0, y1: 1, fillcolor: mut, opacity: 0.15, line: {width: 0}});
      } else {
        shapes.push({type: "line", xref: "x", yref: "paper", x0: at, x1: at, y0: 0, y1: 1,
          line: {color: a.kind === "settlement" ? css("--warn") : mut, width: 1, dash: "dot"}});
      }
      tag(at, a.label, a.kind === "settlement" ? css("--warn") : mut);
    }
    for (const e of S.bundle.episodes.filter(e => keys.includes(e.key) && pz(e.start) >= x0 && pz(e.start) <= x1)) {
      notes.push({x: pz(e.start), y: e.max_edge_cents, text: `▲ episode +${num(e.max_edge_cents, 1)}¢, ${usd(e.median_capacity_usd)}`,
        showarrow: true, arrowhead: 0, ax: 30, ay: 48, xanchor: "left", arrowcolor: css("--pos"), font: {size: 13, color: css("--pos")}});
    }
  }
  const L = baseLayout();
  Plotly.react(el, traces, {
    ...L, showlegend: true, legend: {orientation: "h", x: 0, y: -0.18},
    xaxis: {...L.xaxis, type: "date", range: [x0, x1]}, yaxis: {...L.yaxis, ticksuffix: sp ? " bps" : "¢"}, shapes, annotations: notes,
    uirevision: S.sel + S.range,  // the minute re-render keeps the user's zoom
  }, PLOT_CFG);
  if (!el.dataset.bound) {
    el.dataset.bound = "1";
    el.on("plotly_click", async ev => {
      const [key, ts] = ev.points[0].customdata, sel = S.sel;
      const res = await fetch(`/api/pair/${enc(key)}/snapshot?ts=${enc(ts)}`);
      if (!res.ok) return;
      const row = await res.json();
      if (S.sel !== sel) return;  // another row was selected meanwhile
      S.snap = {key, row};
      renderBoxes();
    });
  }
}

$("q").oninput = e => { S.q = e.target.value; if (S.bundle) renderList(); };
$("size").oninput = e => {
  const v = Math.round(+e.target.value);
  if (v > 0 && v <= 1e6) { S.size = v; walk(); }
};
$("rows").onclick = e => {
  const g = e.target.closest(".grp"), rw = e.target.closest(".row");
  if (g) { S.open.has(g.dataset.g) ? S.open.delete(g.dataset.g) : S.open.add(g.dataset.g); renderList(); }
  else if (rw) {
    select(rw.dataset.k);
    if (matchMedia("(max-width: 760px)").matches) $("detail").scrollIntoView({behavior: "smooth"});
  }
};
