// Research tab: the publisher's hourly research summary (bundle.research, venue_arb/bundle.ResearchSummary) as a
// headline, three cards with a chart each, and the method. Every number comes from the bundle; this only formats.
import {$, esc, num, usd, cents, day, hm, pz, css, baseLayout, PLOT_CFG, REPO, NOT_COMPUTED} from "./common.js";

const METHOD = [["matching", "Matching"], ["edge", "Edge"], ["crypto", "Crypto ladders"], ["not_shown", "What is not shown"]];
const dash = (x, f) => x == null ? "—" : f(x);
const count = (n, word) => `${num(n)} <span>${word}${n === 1 ? "" : "s"}</span>`;
const at = iso => `${day(new Date(iso))} ${hm(new Date(iso))}`;

const card = (id, title, figure, qualifier, caption) => `<div class="card"><h3>${title}</h3><div class="v">${figure}</div>`
  + `<p>${qualifier}</p><div class="rchart" id="${id}"></div><p class="cap">${caption}</p></div>`;

export function renderResearch(bundle) {
  const el = $("tab-research"), r = bundle.research;
  for (const g of el.querySelectorAll(".js-plotly-plot")) Plotly.purge(g);  // drops each graph's resize handler
  if (!r) { el.innerHTML = `<p class="mut">${NOT_COMPUTED}</p>`; return; }
  const [question, ...answer] = r.headline;
  const basis = Object.entries(r.basis).map(([a, b]) =>
    `basis ${esc(a)} +${usd(b.up)}/${usd(-b.down)}, 99.9th percentile over ${num(b.window_days)} d`);
  const ladderQ = r.ladder_checks == null ? "no counted hours yet"
    : [`${num(r.ladder_checks)} admissible checks`, ...basis].join("; ");
  const rv = r.review;
  el.innerHTML = `<div class="hero"><h2>${esc(question)}</h2><p>${answer.map(esc).join(" ")}</p>`
    + `<p class="asof">as of ${at(r.computed_at)} — recomputed hourly from the same tables the Scanner reads — dated note: `
    + `<a href="${REPO}/blob/main/${esc(r.note_path)}">${esc(r.note_path)}</a></p></div>`
    + `<div class="cards">`
    + card("rc-exec", "Executable", count(r.episodes_pair + r.episodes_ladder, "episode"),
      `median ${dash(r.episode_median_minutes, m => `${num(m)} min`)}, median capacity ${dash(r.episode_median_capacity_usd, usd)}, `
        + `max net edge ${dash(r.max_net_edge_cents, c => cents(c))}`,
      "max net edge per hour, ¢; dashed: the 1¢ threshold")
    + card("rc-screen", "Screened history", dash(r.screened_share_ge_1c, s => `${num(100 * s, 1)}%`),
      `of ${num(r.screened_rows)} rows; ${dash(r.screened_share_early, s => `${num(100 * s)}%`)} in a pair's first week`,
      "rows by net gap, ¢; 1¢ and above tinted")
    + card("rc-ladder", "Same-instant ladders", count(r.ladder_violations, "violation"), ladderQ,
      "admissible checks per counted hour; red marks: violations")
    + `</div><div class="method"><h3>How it's measured</h3>`
    + METHOD.map(([k, h]) => `<h4>${h}</h4><p>${esc(r.method[k])}</p>`).join("")
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
}
