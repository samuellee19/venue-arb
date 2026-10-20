// venue-arb pair explorer: fetch + DOM over the dashboard API (dashboard/api/app.py); Plotly is the vendored global.
// No edge, fee or basis logic here: every number comes from the bundle, /history, /snapshot or /api/walk.
const REPO = "https://github.com/samuellee19/venue-arb";
const POLL_MS = 30000, TICK_S = 60, STALE_S = 120, DEAD_S = 3600;
const US_BANNER = "No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference.";
const STALE_BANNER = "last update {n} min ago";
const NOT_US = "Not executable from the US";
const NO_WALK = "size re-walk needs published books";
// verdict label (prefix) -> badge tone; the labels venue_arb/bundle.verdict writes, plus the client-side US one
const TONES = [["Exact match · no edge", "mut"], ["Executable edge", "pos"], ["Not an arbitrage · basis exposure", "warn"],
  ["Spread · within basis bound · no edge", "mut"], ["No asks on one side", "neg"],
  ["Stale · a leg has no snapshot in 5 min", "neg"], [NOT_US, "warn"]];
const KIND = {exact: "exact", near_exact: "near-exact", ladder: "ladder"};
const CATS = {fed_macro: "Fed & macro", crypto_price: "Crypto", weather: "Weather"};
const CHECK = {same_instant: "Same instant", exact_match: "Exact match", basis_bound: "Basis bound",
  fees_depth: "Fees & depth walked", jurisdiction: "Jurisdiction"};
const ICON = {ok: ["✓", "pos"], fail: ["✗", "neg"], partial: ["◐", "warn"]};

const S = {bundle: null, age: 0, fetchedAt: 0, skew: 0, unit: "c", us: false, cat: "all", q: "", sel: null,
  open: new Set(), range: "24h", ann: true, snap: null, walk: null, size: 1000};
let hist = [];  // [{key, h}] history of the selected row and its other-direction sibling
let walkTimer;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const enc = encodeURIComponent;
const num = (x, d = 0) => x.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const usd = x => (x < 0 ? "−$" : "$") + num(Math.abs(x));
const signed = (x, d, u) => `${x < 0 ? "−" : "+"}${num(Math.abs(x), d)}${u}`;
const cents = (x, d = 1) => `${num(x, d)}¢`;
const bpsOf = c => c / (100 - c) * 1e4;  // bps of all-in cost; all-in = $1 payout − net (bundle.net_edge_bps likewise)
const tone = c => c == null ? "mut" : c >= 1 ? "pos" : c < 0 ? "neg" : "";
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const pz = s => s.replace(/(Z|\+00:00)$/, "");  // Plotly ignores zones: strings stay UTC wall time
const now = () => Date.now() + S.skew;
const hm = d => d.toISOString().slice(11, 16) + "Z";
const day = d => d.toLocaleDateString("en-GB", {day: "numeric", month: "short", timeZone: "UTC"});
const seg = (box, on) => [...box.children].forEach(b => b.classList.toggle("on", b === on));
const safeUrl = u => /^https:\/\/(kalshi|polymarket)\.com\//.test(u) ? u : "#";

const pairs = () => S.bundle.pairs;
const byKey = k => pairs().find(p => p.key === k);
const usBlocked = () => S.us && !pairs().some(p => p.us_executable);
const greyed = p => S.us && !p.us_executable;
const label = p => greyed(p) ? NOT_US : p.verdict.label;
const legsTxt = p => p.legs.map(l => `${l.venue === "kalshi" ? "K" : "PM"} ${l.side}`).join(" / ");

// an edge in the chosen unit, the other unit small beneath (or beside, inside the cost box)
function fig(c, bps) {
  if (c == null) return `<span class="mut">—</span> <small>not scored</small>`;
  const a = signed(c, 1, "¢"), b = signed(bps ?? bpsOf(c), 0, " bps");
  return S.unit === "c" ? `${a} <small>${b}</small>` : `${b} <small>${a}</small>`;
}

function settles(iso) {
  const d = new Date(iso), ms = d - now();
  const when = iso.slice(0, 10) === new Date(now()).toISOString().slice(0, 10) ? hm(d) : day(d);
  if (ms <= 0) return `settled ${when}`;
  const m = Math.round(ms / 60000);
  return `settles ${when} · in ${m >= 2880 ? `${Math.round(m / 1440)} d` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} m`}`;
}

function win([a, b]) {  // [lo–hi) of the last scored hour
  const f = s => (a.slice(0, 10) === b.slice(0, 10) ? "" : day(new Date(s)) + " ") + hm(new Date(s));
  return `[${f(a)}–${f(b)})`;
}

// ---- data ----
async function load() {
  try {
    const res = await fetch("/api/live");
    if (!res.ok) throw new Error(res.status === 503 ? "no bundle published yet" : `HTTP ${res.status}`);
    const {bundle, age_s} = await res.json();
    const changed = !S.bundle || S.bundle.computed_at !== bundle.computed_at;
    S.bundle = bundle; S.age = age_s; S.fetchedAt = Date.now();
    S.skew = Date.parse(bundle.computed_at) + age_s * 1000 - Date.now();  // server clock, via age_s
    renderHeader(); renderKpis(); renderFoot();
    if (!S.sel || !byKey(S.sel)) select(defaultSel());
    else { renderList(); if (changed) refreshDetail(); }
    tick();
  } catch (e) {
    $("clock").textContent = `cannot load the live bundle (${e.message}); retrying in 30 s`;
  }
}

function defaultSel() {
  const best = S.bundle.kpis.best_edge?.key;
  return best && byKey(best) ? best : flatRows(pairs())[0]?.key ?? pairs()[0]?.key;
}

// ---- header, banners, KPIs, footer ----
function renderHeader() {
  const b = S.bundle, cats = ["all", ...new Set(b.pairs.map(p => p.category))];
  $("cats").innerHTML = cats.map(c => `<button class="chip${c === S.cat ? " on" : ""}" data-c="${esc(c)}">`
    + `${esc(c === "all" ? "All" : CATS[c] ?? c)}</button>`).join("");
  $("size").disabled = !b.show_books;
  $("size").title = $("sizebox").title = b.show_books ? "" : NO_WALK;
}

function tick() {
  if (!S.bundle) return;
  const age = S.age + (Date.now() - S.fetchedAt) / 1000;
  const next = Math.round((Date.parse(S.bundle.computed_at) + TICK_S * 1000 - now()) / 1000);
  const t = new Date(S.bundle.computed_at).toLocaleTimeString("en-US", {timeZone: "America/Los_Angeles", hour12: false});
  $("clock").textContent = `live · books ${t} PT · ${next > 0 ? `next tick in ${next} s` : "next tick due"}`;
  $("dot").classList.toggle("stale", age > STALE_S);
  $("stale").hidden = age <= STALE_S;
  $("stale").textContent = STALE_BANNER.replace("{n}", num(Math.round(age / 60)));
  $("kpis").classList.toggle("stale", age > DEAD_S);
  $("usban").hidden = !usBlocked();
  $("usban").textContent = US_BANNER;
}

function renderKpis() {
  const k = S.bundle.kpis, best = k.best_edge, bp = best && byKey(best.key), live = k.pairs_live, w = k.checks_window;
  const tile = (l, v, s) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  const zero = usBlocked();  // executable KPIs read 0 when no pair is executable from the US
  $("kpis").innerHTML = [
    tile("Executable episodes · 7 d", zero ? 0 : num(k.episodes_7d), "≥ 1¢ net, ≥ $100, ≥ 2 snapshots"),
    zero || !best
      ? tile("Best net edge now", zero ? 0 : "—", zero ? "no pair is executable from the US" : "no live pair scored")
      : tile("Best net edge now", `<span class="${tone(best.net_edge_cents)}">${fig(best.net_edge_cents, bp?.net_edge_bps)}</span>`,
        `${esc(bp?.title ?? best.key)} · depth ${usd(best.capacity_usd)} at that edge`),
    tile("Pairs live", num(live.exact + live.near_exact + live.ladder),
      `${live.exact} exact · ${live.near_exact} near-exact · ${live.ladder} ladder`),
    tile(`Ladder checks · last scored hour ${w ? win(w) : ""}`, k.admissible_checks == null ? "—"
      : `${num(k.admissible_checks)} <span class="${k.violations ? "neg" : "pos"}" style="font-size:14px">· ${num(k.violations ?? 0)} violations</span>`,
      "basis-admissible strike pairs"),
    tile("Screened history", k.screened_share_ge_1c == null ? "—" : `${num(100 * k.screened_share_ge_1c, 1)}%`,
      k.screened_hours == null ? "no screened hours yet" : `of ${num(k.screened_hours)} pair-hours ≥ 1¢ gross since Jun`),
  ].join("");
}

function renderFoot() {
  const b = S.bundle, k = b.kpis;
  const den = k.admissible_checks == null
    ? "Denominator: no ladder hour has been scored yet."
    : `Denominator kept honest: ${num(k.admissible_checks)} basis-admissible strike pairs checked in the last scored hour `
      + `${k.checks_window ? win(k.checks_window) : ""}, ${num(k.violations ?? 0)} violated the bound`
      + (k.screened_hours ? `; ${num(100 * k.screened_share_ge_1c, 1)}% of ${num(k.screened_hours)} screened pair-hours reached ≥ 1¢ gross` : "")
      + `; ${num(k.episodes_7d)} executable episode${k.episodes_7d === 1 ? "" : "s"} in 7 days.`;
  $("foot").innerHTML = `<span>${den}</span><ul>${[...b.attribution, ...b.caveats].map(x => `<li>${esc(x)}</li>`).join("")}</ul>`
    + `<span>Read-only. No orders are placed.</span>`
    + `<span><a href="${REPO}/blob/main/reports/research-note-v1.md">research note</a> · <a href="${REPO}">source on GitHub</a></span>`;
}

// ---- pair list ----
const byEdge = (a, b) => (b.net_edge_cents ?? -1e9) - (a.net_edge_cents ?? -1e9);
const flatRows = ps => ps.filter(p => p.kind !== "ladder" || p.net_edge_cents >= 1).sort(byEdge);

function renderList() {
  const q = S.q.trim().toLowerCase();
  const ps = pairs().filter(p => (S.cat === "all" || p.category === S.cat)
    && (!q || [p.title, p.key, ...p.legs.map(l => l.title)].join(" ").toLowerCase().includes(q)));
  let html = flatRows(ps).map(row).join("");
  const groups = new Map();
  for (const p of ps.filter(p => p.kind === "ladder").sort(byEdge)) {
    if (!groups.has(p.ladder)) groups.set(p.ladder, []);
    groups.get(p.ladder).push(p);
  }
  for (const [lad, rs] of groups) {
    const [asset, at] = lad.split("~"), open = S.open.has(lad);
    const strikes = new Set(rs.map(p => p.legs[0].market_id)).size;
    html += `<div class="grp" data-g="${esc(lad)}">${open ? "▾" : "▸"} ${esc(asset)} · ${hm(new Date(at))} · ${strikes} strike${strikes === 1 ? "" : "s"}</div>`;
    if (open) html += rs.map(row).join("");
  }
  $("rows").innerHTML = html || `<div class="empty">No pair matches.</div>`;
}

function row(p) {
  return `<div class="row${p.key === S.sel ? " sel" : ""}${greyed(p) ? " grey" : ""}" data-k="${esc(p.key)}">`
    + `<div><div class="n">${esc(p.title)} <span class="badge ${p.kind}">${KIND[p.kind]}</span></div>`
    + `<div class="m">${settles(p.settles_at)} · ${legsTxt(p)}${p.live ? "" : " · stale"}</div></div>`
    + `<div class="e ${tone(p.net_edge_cents)}">${fig(p.net_edge_cents, p.net_edge_bps)}</div>${spark(p)}</div>`;
}

function spark(p) {
  const v = p.spark, ys = v.filter(x => x != null);
  if (!ys.length) return `<svg class="spark"></svg>`;
  const lo = Math.min(...ys, 0), hi = Math.max(...ys, 1), Y = x => (16 - (x - lo) / (hi - lo) * 14).toFixed(1);
  let d = "", pen = false;
  v.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${(i * 300 / (v.length - 1)).toFixed(1)},${Y(x)}`;
    pen = true;
  });
  return `<svg class="spark" viewBox="0 0 300 18" preserveAspectRatio="none">`
    + `<line x1="0" x2="300" y1="${Y(1)}" y2="${Y(1)}" style="stroke:var(--neg)" stroke-opacity=".35" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`
    + `<path d="${d}" fill="none" style="stroke:var(${p.net_edge_cents > 0 ? "--pos" : "--mut"})" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>`;
}

function select(key) {
  S.sel = key; S.snap = null; S.walk = null;
  renderList();
  buildDetail();
}

// ---- pair detail ----
function buildDetail() {
  const old = $("plot");
  if (old && window.Plotly) Plotly.purge(old);  // drops the graph and its window resize handler before the node goes
  if (!S.sel) { $("detail").innerHTML = `<div class="empty">No pairs in this bundle.</div>`; return; }
  const ranges = ["1h", "24h", "7d", "all"].map(r => `<button data-r="${r}"${r === S.range ? ' class="on"' : ""}>${r.replace(/(\d+)/, "$1 ")}</button>`);
  $("detail").innerHTML = `<div id="dhead"></div><div id="boxes"></div>`
    + `<div class="chart"><div class="tools"><h4>Net edge after fees · ¢ per contract · UTC</h4>`
    + `<span class="seg" id="range">${ranges.join("")}</span><button class="chip${S.ann ? " on" : ""}" id="ann">annotations</button></div>`
    + `<div id="plot"></div><div class="legend"><span>dashed red: +1¢ episode threshold</span><span>dotted: settlement · listing · basis change</span>`
    + `<span>shaded: collector gap</span><span>▲ episode</span><span>hover for the books · click a point to load that snapshot</span></div></div>`
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
  const p = byKey(S.sel), lab = label(p), t = TONES.find(([l]) => lab.startsWith(l))?.[1] ?? "mut";
  const legs = p.legs.map(l => {
    const k = l.venue === "kalshi", id = k ? l.market_id : l.url.split("/event/")[1] || l.market_id;
    return `<a class="leg" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener"><span class="sw" style="background:var(--${k ? "k" : "pm"})"></span>`
      + `${k ? "Kalshi" : "Polymarket"} · buy ${l.side.toUpperCase()} <span class="id">${esc(id)}</span> <span class="ext">↗ ${k ? "kalshi.com" : "polymarket.com"}</span></a>`;
  }).join("");
  const src = p.legs.map(l => l.settlement_source).filter(Boolean);
  const checks = p.verdict.checks.map(c => `${CHECK[c.name].toLowerCase()} ${ICON[c.status][0]}`).join(" · ");
  $("dhead").innerHTML = `<div class="title"><div><h2>${esc(p.title)}</h2><div class="legs">${legs}`
    + (src.length ? `<span class="leg mut">settles: ${src.map(esc).join(" ↔ ")}</span>` : "") + `</div></div>`
    + `<div class="verdict"><div class="big ${t}">${esc(lab)}</div><div class="cd">${checks} · ${settles(p.settles_at)}</div></div></div>`;
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
    return `<div class="lvl${hit ? " hit" : ""}"><span class="mono">${cents(100 * l.price)}</span><div class="bar">`
      + `<i style="width:${100 * l.size / max}%;background:var(--${k ? "k" : "pm"}${hit ? "" : "2"})"></i></div>`
      + `<span class="mono" style="text-align:right">${num(l.size)}</span></div>`;
  }).join("");
  return `<div class="box"><h4><span class="sw" style="background:var(--${k ? "k" : "pm"})"></span>${k ? "Kalshi" : "Polymarket"} · buy ${side.toUpperCase()}</h4>`
    + `${rows || `<div class="note">${empty}</div>`}<div class="legend"><span>contracts at each ask</span></div></div>`;
}

const capRow = p => r("Capacity at ≥ 1¢ net", `${num(p.capacity_contracts)} contracts · ${usd(p.capacity_usd)}`);

function costWalk(p) {  // books published: the fill at the chosen size from /api/walk
  const w = S.walk, [kl, pl] = p.legs;
  if (!w || w.key !== p.key) return `<div class="note">walking both books at ${num(S.size)} contracts…</div>`;
  if (w.err) return `<div class="note">${esc(w.err)}</div>${capRow(p)}`;
  const f = w.res;
  if (!f.filled) return `<div class="note">no visible depth on one side</div>${capRow(p)}`;
  const pnl = f.net_cents * f.filled / 100;
  return r(`Kalshi ${kl.side.toUpperCase()} ask (avg over ${num(f.filled)})`, cents(100 * f.k_avg_price))
    + r(`Polymarket ${pl.side.toUpperCase()} ask (avg over ${num(f.filled)})`, cents(100 * f.pm_avg_price))
    + r(`<span class="mut">Fees, both venues</span>`, `<span class="mut">+${cents(100 * f.fees_usd / f.filled)} · ${usd(f.fees_usd)}</span>`)
    + r(`All-in, ${num(f.filled)} contracts`, `<span class="${tone(f.net_cents)}">${cents(100 * f.cost_usd / f.filled)} → ${fig(f.net_cents, f.net_bps)} · <b>${usd(pnl)}</b></span>`, "t")
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

function renderBoxes() {
  const p = byKey(S.sel), show = S.bundle.show_books, el = $("boxes");
  if (!p || !el) return;
  const mid = (h, body, note = "") => `<div class="box"><h4>${h}</h4><div class="wf">${body}</div>${note}</div>`;
  if (S.snap) {
    const q = byKey(S.snap.key) ?? p, s = S.snap.row;
    const note = `<div class="note">history: first ask level only · snapshot ${s.ts.slice(11, 19)}Z · ${legsTxt(q)} · <button id="golive">back to live</button></div>`;
    const lv = (a, z) => a == null ? [] : [{price: a, size: z ?? 0}];
    el.className = show ? "grid3" : "grid3 one";
    el.innerHTML = show
      ? book("kalshi", q.legs[0].side, lv(s.k_ask, s.k_size), undefined, "no ask in this history row") + mid("Cost of both legs · per contract at this snapshot", costSnap(q, s), note)
        + book("polymarket", q.legs[1].side, lv(s.pm_ask, s.pm_size), undefined, "no ask in this history row")
      : mid("Derived at this snapshot · quotes not published", costSnap(q, s), note);
  } else if (show && p.books) {
    const filled = S.walk?.key === p.key ? S.walk.res?.filled : undefined;
    el.className = "grid3";
    el.innerHTML = book("kalshi", p.legs[0].side, p.books[0], filled)
      + mid(`Cost of both legs · per contract and at <span class="mono" style="color:var(--txt)">${num(S.size)}</span>`, costWalk(p))
      + book("polymarket", p.legs[1].side, p.books[1], filled);
  } else {
    el.className = "grid3 one";
    el.innerHTML = mid("Cost of both legs · per contract, first slice", costDerived(p),
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
  const sib = pairs().find(q => q.key !== p.key && q.key.startsWith(base));  // same two markets, other direction
  const keys = [p, sib].filter(Boolean).map(q => q.key), sel = S.sel, range = S.range;
  const res = await Promise.all(keys.map(k => fetch(`/api/pair/${enc(k)}/history?range=${range}`)
    .then(x => x.ok ? x.json() : {}).catch(() => ({}))));
  if (S.sel !== sel || S.range !== range) return;  // a newer selection owns the chart
  hist = keys.map((key, i) => ({key, h: res[i]}));
  drawChart();
}

function hover(h, j) {
  const v = c => h[c]?.[j], net = v("net_edge_cents");
  const ask = (n, a, z) => a == null ? "" : `${n} ask ${cents(100 * a)} ×${z == null ? "?" : num(z)}`;
  const asks = [ask("K", v("k_ask"), v("k_size")), ask("PM", v("pm_ask"), v("pm_size"))].filter(Boolean).join(" · ");
  return `<b>${h.ts[j].slice(0, 16).replace("T", " ")} UTC</b><br>${asks || "asks not published"}<br>`
    + `fees ${v("fees_cents") == null ? "—" : cents(v("fees_cents"))} · net ${net == null ? "—" : signed(net, 2, "¢")}`
    + ` · capacity ≥ 1¢: ${v("capacity_usd") == null ? "—" : usd(v("capacity_usd"))}<br><i>click to load this snapshot</i>`;
}

function drawChart() {
  const el = $("plot");
  if (!el || !window.Plotly) return;
  const mut = css("--mut"), grid = css("--line"), colors = [css("--pos"), mut];
  const traces = hist.filter(t => t.h.ts?.length).map((t, i) => ({
    type: "scatter", mode: "lines+markers", marker: {size: 3}, line: {color: colors[i], width: i ? 1.3 : 1.8},
    name: `net edge, ${legsTxt(byKey(t.key) ?? byKey(S.sel)).replace(" / ", " + ")}`,
    x: t.h.ts.map(pz), y: t.h.net_edge_cents, customdata: t.h.ts.map(ts => [t.key, ts]),
    text: t.h.ts.map((_, j) => hover(t.h, j)), hovertemplate: "%{text}<extra></extra>"}));
  if (!traces.length) {
    Plotly.purge(el);
    delete el.dataset.bound;
    el.innerHTML = `<div class="empty">No history for this pair in the ${S.range} range yet.</div>`;
    return;
  }
  el.querySelector(":scope > .empty")?.remove();
  const xs = traces.flatMap(t => t.x).sort(), x0 = xs[0], x1 = xs.at(-1), keys = hist.map(t => t.key);
  const shapes = [{type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: {color: css("--neg"), width: 1, dash: "dash"}}];
  const notes = [{xref: "paper", x: 0, y: 1, yanchor: "bottom", xanchor: "left", text: "+1¢", showarrow: false, font: {size: 10, color: css("--neg")}}];
  if (S.ann) {
    let n = 0;
    const tag = (x, text, color = mut) => notes.push({x, y: 1 - 0.08 * (n++ % 4), xref: "x", yref: "paper", showarrow: false,
      text: esc(text.length > 34 ? text.slice(0, 33) + "…" : text), hovertext: esc(text), xanchor: "left", yanchor: "top", font: {size: 10, color}});
    for (const a of S.bundle.annotations.filter(a => a.key == null || keys.includes(a.key))) {
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
      notes.push({x: pz(e.start), y: e.max_edge_cents, text: `▲ episode +${num(e.max_edge_cents, 1)}¢ · ${usd(e.median_capacity_usd)}`,
        showarrow: true, arrowhead: 0, ax: 30, ay: 48, xanchor: "left", arrowcolor: css("--pos"), font: {size: 10, color: css("--pos")}});
    }
  }
  Plotly.react(el, traces, {
    margin: {l: 44, r: 10, t: 16, b: 30}, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: {color: mut, size: 11}, showlegend: true, legend: {orientation: "h", x: 0, y: -0.18}, hovermode: "closest",
    hoverlabel: {bgcolor: css("--panel"), bordercolor: css("--on-line"), font: {color: css("--txt")}},
    xaxis: {type: "date", gridcolor: grid, linecolor: grid, range: [x0, x1]},
    yaxis: {gridcolor: grid, zerolinecolor: grid, ticksuffix: "¢"}, shapes, annotations: notes,
  }, {displayModeBar: false, responsive: true});
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

// ---- controls ----
$("unit").onclick = e => {
  if (!e.target.dataset.u || !S.bundle) return;
  S.unit = e.target.dataset.u; seg($("unit"), e.target); renderKpis(); renderList(); renderBoxes();
};
$("where").onclick = e => {
  if (!e.target.dataset.w || !S.bundle) return;
  S.us = e.target.dataset.w === "us"; seg($("where"), e.target); renderKpis(); renderList(); renderHead(); tick();
};
$("cats").onclick = e => {
  if (!e.target.dataset.c) return;
  S.cat = e.target.dataset.c; renderHeader(); renderList();
};
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
$("theme").onclick = () => {
  const root = document.documentElement;
  const cur = root.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  root.dataset.theme = cur === "light" ? "dark" : "light";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* private mode: not persisted */ }
  drawChart();
};

load();
setInterval(load, POLL_MS);
setInterval(tick, 1000);
