// Research tab: the publisher's hourly research summary (bundle.research, venue_arb/bundle.ResearchSummary) as a
// headline, three cards with a chart each, and the method. Every number comes from the bundle; this only formats.
import {$, esc, num, usd, cents, day, hm, pz, css, baseLayout, PLOT_CFG, REPO, NOT_COMPUTED} from "./common.js";

const METHOD = [["matching", "Matching"], ["edge", "Edge"], ["crypto", "Crypto ladders"], ["not_shown", "What is not shown"],
  ["stock_perps", "Stock perps"]];
const MIN_BPS = 10;  // the stock-perp net floor (config.STOCK_PERP_MIN_NET_BPS), drawn as the dashed line
const dash = (x, f) => x == null ? "—" : f(x);
const count = (n, word) => `${num(n)} <span>${word}${n === 1 ? "" : "s"}</span>`;
const at = iso => `${day(new Date(iso))} ${hm(new Date(iso))}`;
const since = iso => iso ? `since ${day(new Date(iso))}` : "no data yet";
const month = iso => new Date(iso).toLocaleDateString("en-GB", {month: "short", year: "numeric", timeZone: "UTC"});
const span = (a, b) => {  // "Jun–Oct 2026"; the year once when both ends share it
  if (!a) return "no data yet";
  const [x, y] = [month(a), month(b)];
  return x === y ? y : x.slice(-4) === y.slice(-4) ? `${x.slice(0, -5)}–${y}` : `${x}–${y}`;
};

const card = (id, title, win, figure, qualifier, caption) => `<div class="card"><h3>${title}, ${win}</h3><div class="v">${figure}</div>`
  + `<p>${qualifier}</p><div class="rchart" id="${id}"></div><p class="cap">${caption}</p></div>`;

// the two stock-perp cards: net spread by hour, and how much of Monday's move the Sunday perp already held
function stockCards(s) {
  const w = s.weekends, se = s.sensitivity;
  return card("rc-sp", "Stock-perp spread", "net of costs", count(s.episodes, "episode"),
    `max net ${dash(s.max_net_bps, x => num(x, 1))} bps; ${dash(s.share_hours_abs_ge_10bps, x => num(100 * x))}% of session hours over ${MIN_BPS} bps; `
      + `${num(s.names)} names ${since(s.window_start)}`,
    `max net bps per hour; dashed: ${MIN_BPS} bps. Episodes in the last ${num(se.window_days)} d: ${num(se.episodes_base)} at a 24 h hold, `
      + `${num(se.episodes_1h)} at 1 h, ${num(se.episodes_7d)} at 7 d, ${num(se.episodes_with_borrow)} with borrow`)
    + card("rc-wk", "Weekends", `${num(w.names)} names`, `${dash(w.hit_rate_sun, x => num(100 * x))}% <span>lean the right way</span>`,
      [`${num(w.n_sun)} name-weekends`, "perp at Sunday 6 PM ET vs Monday's open", ...(w.enough ? [] : ["too few weekends to conclude"])].join("; "),
      "share of Monday's move the Sunday perp already held, one dot per name; dashed: all of it");
}

export function renderResearch(bundle) {
  const el = $("tab-research"), r = bundle.research;
  for (const g of el.querySelectorAll(".js-plotly-plot")) Plotly.purge(g);  // drops each graph's resize handler
  if (!r) { el.innerHTML = `<p class="mut">${NOT_COMPUTED}</p>`; return; }
  const [question, ...answer] = r.headline;
  const basis = Object.entries(r.basis).map(([a, b]) =>
    `basis ${esc(a)} +${usd(b.up)}/${usd(-b.down)}, ${num(100 * r.basis_q, 1)}th percentile over ${num(b.window_days)} d`);
  const ladderQ = [count(r.episodes_ladder, "episode"), ...(r.ladder_checks == null ? ["no counted hours yet"]
    : [`${num(r.ladder_checks)} admissible checks`, ...basis])].join("; ");
  const rv = r.review;
  el.innerHTML = `<div class="hero"><h2>${esc(question)}</h2><p>${answer.map(esc).join(" ")}</p>${r.stock_perps ? `<p>${esc(r.stock_perps.headline)}</p>` : ""}`
    + `<p class="asof">as of ${at(r.computed_at)} — recomputed hourly from the same tables the Scanner reads — dated note: `
    + `<a href="${REPO}/blob/main/${esc(r.note_path)}">${esc(r.note_path)}</a></p></div>`
    + `<div class="cards">`
    + card("rc-exec", "Executable", since(r.window_start), count(r.episodes_pair, "episode"),
      `median ${dash(r.episode_median_minutes, m => `${num(m)} min`)}, median capacity ${dash(r.episode_median_capacity_usd, usd)}, `
        + `max net edge ${dash(r.max_net_edge_cents, c => cents(c))}`,
      "max net edge per hour, ¢; dashed: the 1¢ threshold")
    + card("rc-screen", "Screened history", span(r.screened_start, r.screened_end),
      dash(r.screened_share_ge_1c, s => `${num(100 * s, 1)}%`),
      `of ${num(r.screened_rows)} rows at ≥ 1¢; ${dash(r.screened_share_early, s => `${num(100 * s)}%`)} of those in a pair's first week`,
      "rows by net gap, ¢; 1¢ and above tinted")
    + card("rc-ladder", "Same-instant ladders", since(r.ladder_start), count(r.ladder_violations, "violation"), ladderQ,
      "admissible checks per counted hour; red marks: violations")
    + (r.stock_perps ? stockCards(r.stock_perps) : "")
    + `</div><div class="method"><h3>How it's measured</h3>`
    + METHOD.filter(([k]) => r.method[k] && (k !== "stock_perps" || r.stock_perps)).map(([k, h]) => `<h4>${h}</h4><p>${esc(r.method[k])}</p>`).join("")
    + `<p class="mut">${rv ? `Match review: precision ${num(rv.precision, 2)} on ${num(rv.labels)} labels (${day(new Date(rv.asof))})`
      : "no review sitting yet"}</p></div>`;
  if (window.Plotly) draw(r);
}

function draw(r) {
  const B = baseLayout(), L = {...B, margin: {...B.margin, b: 48}};  // room for the two-line date ticks
  const pos = css("--pos"), neg = css("--neg"), mut = css("--mut"), k = css("--k");
  const date = {...L.xaxis, type: "date", nticks: 4, tickangle: 0}, ts = s => s.ts.map(pz);

  const ex = r.exec_hourly;
  Plotly.react($("rc-exec"), [{type: "scatter", mode: "lines", x: ts(ex), y: ex.values, line: {color: k, width: 2},
    hovertemplate: "%{x|%d %b %H:00}Z<br>max net edge %{y:.2f}¢<extra></extra>"}],
  {...L, xaxis: date, yaxis: {...L.yaxis, ticksuffix: "¢"},
    shapes: [{type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: {color: neg, width: 1, dash: "dash"}}]}, PLOT_CFG);

  const h = r.screened_hist, n = h.counts.length, lo = h.edges.slice(0, -1);
  const bin = i => i === 0 ? `below ${num(h.edges[1])}¢` : i === n - 1 ? `${num(lo[i])}¢ and above`
    : `${num(lo[i])} to ${num(h.edges[i + 1])}¢`;
  Plotly.react($("rc-screen"), [{type: "bar", x: lo.map(e => e + 0.5), y: h.counts, text: lo.map((_, i) => bin(i)),
    textposition: "none", marker: {color: lo.map(e => e >= 1 ? pos : mut), opacity: lo.map(e => e >= 1 ? 1 : 0.45)},
    hovertemplate: "%{text}: %{y:,} rows<extra></extra>"}],
  {...L, bargap: 0.1, xaxis: {...L.xaxis, ticksuffix: "¢"}}, PLOT_CFG);

  const lh = r.ladder_hourly, hit = lh.violations.flatMap((v, i) => v ? [i] : []);
  Plotly.react($("rc-ladder"), [
    {type: "bar", x: ts(lh), y: lh.checks, marker: {color: k, opacity: 0.6},
      hovertemplate: "%{x|%d %b %H:00}Z<br>%{y:,} admissible checks<extra></extra>"},
    {type: "scatter", mode: "markers", x: hit.map(i => pz(lh.ts[i])), y: hit.map(i => lh.checks[i]),
      text: hit.map(i => `${num(lh.violations[i])} violation${lh.violations[i] === 1 ? "" : "s"}`),
      marker: {color: neg, size: 10, symbol: "x"}, hovertemplate: "%{text}<extra></extra>"}],
  {...L, xaxis: date}, PLOT_CFG);

  const sp = r.stock_perps;
  if (!sp) return;
  const hr = sp.hourly;
  Plotly.react($("rc-sp"), [{type: "scatter", mode: "lines", x: ts(hr), y: hr.values, line: {color: k, width: 2},
    hovertemplate: "%{x|%d %b %H:00}Z<br>max net %{y:.1f} bps<extra></extra>"}],
  {...L, xaxis: date, yaxis: {...L.yaxis, ticksuffix: " bps"},
    shapes: [{type: "line", xref: "paper", x0: 0, x1: 1, y0: MIN_BPS, y1: MIN_BPS, line: {color: neg, width: 1, dash: "dash"}}]}, PLOT_CFG);

  const pts = sp.weekends.rows.filter(x => x.contained_sun != null);  // the bundle's share; the client computes none
  Plotly.react($("rc-wk"), [{type: "scatter", mode: "markers", x: pts.map(x => x.friday), y: pts.map(x => 100 * x.contained_sun),
    text: pts.map(x => x.symbol), marker: {color: k, size: 8, opacity: 0.8},
    hovertemplate: "%{text}, weekend of %{x|%d %b}<br>%{y:.0f}% of Monday's move<extra></extra>"}],
  {...L, xaxis: {...L.xaxis, type: "date", nticks: 4, tickangle: 0}, yaxis: {...L.yaxis, ticksuffix: "%"},
    shapes: [{type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: {color: mut, width: 1, dash: "dash"}}]}, PLOT_CFG);
}
