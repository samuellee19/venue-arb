# venue-arb dashboard v2: Scanner, Explorer, Research

Design, 2026-10-21. Status: draft for Sam's review. Amends `2026-10-20-venue-arb-dashboard-design.md` (v1, live at
https://34.127.95.87/ since 2026-10-21); everything v1 says still holds unless this document changes it.

Calls marked **(Claude's call)** were made while writing this up and were not discussed with Sam. Everything else was
agreed in conversation (mockup: `.superpowers/brainstorm/14127-1792645009/content/tabs-v1.html`, git-ignored).

## 1. Intent

The v1 page is one dense, dark pair-explorer. Compared with the commercial scanners (Oddpool's arb scanner and
market-differences tables), it is harder to scan and it hides the research under a trader's table. Sam wants both
audiences served on one page: a hiring manager who skims should see a product ("they built a trading tool"), and one
who reads should find the finding ("they did the research"). The answer is three tabs over one data source, a light
visual style that reads as a designed product rather than generated UI, and a proper name.

What stays unique to this page, and must not be lost in the redesign: every edge is net of fees and depth, every
row carries a verdict with reasons, the page shows how many checks found nothing, and no quote is displayed while
`DASHBOARD_SHOW_BOOKS` is False.

## 2. Goals, non-goals, anti-goals

**Goals**
- A **Scanner** tab: one flat, sortable row per live pair, readable in two seconds.
- An **Explorer** tab: today's detail view (cost panel, chart, verdict cards), reached from a Scanner row.
- A **Research** tab: the note's findings with **live numbers**, recomputed hourly from the same tables, with the
  method in plain words.
- A visual system that looks designed: one typeface, three sizes, 8-px rhythm, one accent, signed-number colours
  only, no ornament.
- The name: wordmark `venue-arb`, title **Prediction Market Arbitrage**.

**Non-goals**
- New measurements. The Research tab renders what `venue_arb/report/note.py` already computes.
- Mobile polish beyond "does not break"; accounts; alerts; any trading affordance.
- Showing quotes. The flag decision is unchanged and separate.

**Anti-goals**
- Any row that reads as an opportunity without its verdict beside it.
- Copy that sounds like a pitch (no slogans, testimonials, counters framed as achievements, emoji).
- Numbers on the Research tab that disagree with the Scanner's for the same hour (one source, one computation).

## 3. What the visitor sees

**Name and header.** Top-left wordmark `venue-arb` (links to the repo), then the title **Prediction Market
Arbitrage** and one description line: *Kalshi vs Polymarket: matched markets, executable edge after fees and depth,
and why most gaps aren't arbitrage.* Browser tab title: `venue-arb · Prediction Market Arbitrage`. Right side of the
header: two figures with labels — **episodes · 7 d** and **pairs live** — and the live dot with the books time (stale
rules as v1). No countdown, no category chips in the header.

**Tabs** (a thin bar under the header; the URL hash remembers the tab: `#scanner`, `#explorer`, `#research`):

**Scanner (default).** Category chips with counts (All, Fed & macro, Crypto, …, from `pair.category`). One table,
columns: *Pair* (category tag, outcome title, one muted line with both market ids), *Legs* ("K yes / PM no"),
*Net edge* (¢, signed, right-aligned), *ROI* (net edge ÷ all-in cost per contract, %, signed), *Capacity* ($ at ≥ 1¢
net, "—" when 0), *Settles* (date and "in N d/h"), *Verdict* (badge; four tones: none, basis, executable, stale).
Sort by net edge descending by default; header click sorts any numeric column. Unit switch ¢ | bps | ROI applies to
the edge column only. Ladder rows: shown flat like the rest, sorted with everything else **(Claude's call;** the v1
collapsed-ladder grouping is dropped — grouping belongs in the Explorer list, not the scanner). US mode moves to a
small toggle above the table with the v1 banner copy. Below the table a one-line footer: pair count, "per-leg quotes
are not published (venue display terms)", "ROI = net edge ÷ all-in cost per contract". A shaded **example row**
(labelled "example — none live right now") stays at the bottom whenever no executable row exists **(Claude's
call)**, so the concept of an opportunity is visible on a page that may show none for weeks.

**Explorer.** The v1 page body (pair list + detail pane) re-skinned to the new system; opened by clicking a Scanner
row, which selects that pair. Keeps the collapsed ladder groups, the chart with annotations, the cost panel, the
verdict cards, the size box behaviour. Nothing functional changes.

**Research.** In order:
1. A headline block: the question as a sentence, then the short answer in two or three sentences with the live
   numbers bold (pairs, aligned snapshots, largest net edge, episodes, US-executable episodes, ladder checks and
   violations). An "as of" line with the computation time and a link to the dated note in the repo.
2. Three cards, one per tier, each with a figure, a one-line qualifier and a Plotly chart: **Executable** (episodes;
   max net edge per hour over the whole history with the 1¢ line), **Screened history** (share ≥ 1¢ gross; net-gap
   histogram), **Same-instant ladders** (violations; admissible checks per hour with violations marked, plus the
   basis bounds per asset).
3. "How it's measured": four short paragraphs — matching (tree, rule + LLM, review precision), edge (own books,
   fee walk, episode rule), crypto (same instant, basis bound), what's not shown and why. Copy is written once in the
   bundle (the publisher owns the words, the page renders them) **(Claude's call)**, so the note and the page say
   the same thing.

**Style** (binding): Inter with the system stack as fallback; sizes 13/15/22 px (26 for the headline figures);
8-px spacing grid; light theme default, dark via the toggle, both from CSS variables; one accent (`--k` blue) for
links, active tab and selection; green/red only on signed numbers and the executable badge; no mid-dot separators
in running text; monospace only in numeric table cells and ids; no gradients, no emoji, no decorative icons.

## 4. What changes behind the page

- **Research summary in the bundle.** The publisher adds `research: ResearchSummary | None` to `LiveBundle`,
  recomputed every clock hour (same cadence as the history write) by calling the note's own aggregation functions
  (`note.summarize` and the queries it reads) and reducing their output to what the tab needs: the headline numbers,
  per-hour series for the three charts (hourly max net edge; the net-gap histogram bins; admissible checks and
  violations per hour), the basis bounds, the review precision, and the four method paragraphs. A failed research
  computation keeps the previous summary (same rule as a failed refresh) and records a warn result.
  Budget: ≤ 150 KB added to `live.json` (hourly series over the history so far; decimate to daily once the
  executable tier exceeds 60 days) **(Claude's call)**.
- **ROI** is computed in the bundle (`net_edge_roi = net_edge_cents / (100 − net_edge_cents)`, i.e. edge over
  all-in cost per contract) so the Scanner and the Explorer show the same number.
- `category` labels for chips come from `venue_arb/taxonomy.py` display names (add a `DISPLAY` map) **(Claude's
  call)**.
- No new endpoints; no migrations; the server is unchanged except serving the new static files.

## 5. Constraints

- Everything in v1 §8 (no inbound path home, no raw quotes while the flag is False, no CF Benchmarks/Binance series,
  CI, no Co-Authored-By, additive migrations) still holds.
- The Scanner must render from `live.json` alone; the Research tab from `live.json` alone (no new API calls).
- The static test keeps its guarantees (vendored Plotly sha, no external URLs, verdict labels verbatim).
- The sample bundle gains a research section so the page can be developed and tested offline.

## 6. Left to the builder

- Exact `ResearchSummary` schema and which `note.py` functions to call vs. query directly (the note's SQL constants
  are the source of truth for the numbers).
- Plotly figure specs for the three cards.
- Whether the Explorer's list and the Scanner share one row-render function (they should where it doesn't
  contort either).
- Test strategy for the tab logic without a JS test runner: static assertions plus the browser walk, as v1.

## 7. Calls made after the builder check (Claude's calls, all)

A reviewer read §1–6 as a builder and asked eight questions; none needed Sam. The answers bind the plan:

1. **One definition per number, the note's.** The Research tab and the header use the note's scope (exact pairs at the
   current prompt version, no wrong/unsure review; episodes from both sources, labelled by tier). The header's
   "episodes · 7 d" is that same scoped count restricted to episodes ending in the last 7 days; Research headline
   figures are all-history and every figure carries its window in its label. Ladder violations = rows with
   `capacity_contracts > 0` everywhere (the note's `capacity_usd > 0` is changed to match). "Pairs live" counts
   distinct market pairs with both legs live, not direction rows. The publisher's `EPISODES_SQL` and KPI queries
   change to these definitions; `note.py` is the authority they copy.
2. **The v1 KPI tiles go.** Their numbers live on the Research cards. The denominator sentence ("N admissible
   ladder checks in the last scored hour, M violations") becomes the Scanner's footer line and the Research
   ladder card's qualifier. The header figures grey out after 1 h exactly as the v1 tiles did.
3. **Method copy lives in one module**, `venue_arb/report/copy.py`: the four paragraphs as constants, imported by
   both `note.render` (replacing only the equivalent method sentences; the note's other prose is untouched) and
   the publisher. Claude drafts the paragraphs; Sam reviews them (§8). Review precision comes from the `reviews`
   table through `matching.review.precision` over current-verdict labels, shown as "precision P on N labels
   (date)"; with no labels the card says "no review sitting yet". The note link is
   `reports/research-note-v1.md` in the repo (a 404 until the repo is public; the going-public checklist covers it).
4. **Research runs inline, after that tick's live write and push**, so a slow computation delays the next tick,
   never the current bundle; a warn result `research_seconds` fires over 30 s. If the OptiPlex measurement shows
   it regularly exceeds that, the builder moves it to a thread — the schema does not change.
5. **Inter by name only**, with the system stack as fallback; nothing vendored. The wordmark's repo link is set in
   JavaScript from the existing `REPO` constant, as the footer already does, so the static test's rule stands.
6. **Scanner shows one row per market pair** — its better direction by net edge; the other direction is one click
   away in the Explorer. Stale and no-asks rows are listed last with the stale tone; US-greyed rows use the stale
   tone with the "Not executable from the US" badge text.
7. **The example row** is generic ("Example — what a row looks like when a pair clears the rule"), invented round
   numbers, not clickable, excluded from counts, chips, sorting and the pairs figure, rendered only when no
   executable row exists.
8. `LIVE_BYTES_BUDGET` rises to 800,000 bytes.

## 8. Sam's actions

- Review the copy on the Research tab once rendered (it's the part a reader quotes).
- Unchanged from v1: the venue-terms read for `DASHBOARD_SHOW_BOOKS`, the domain (or sslip.io) for a real
  certificate before linking from a resume, the going-public checklist.
