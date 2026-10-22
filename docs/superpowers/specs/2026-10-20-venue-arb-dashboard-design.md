# venue-arb dashboard: live pair explorer

Design, 2026-10-20. Status: draft for Sam's review. Extends the project design
(`2026-10-19-venue-arb-design.md`, §1 "live demo", §8 Plan 4) with the first public page.

Calls marked **(Claude's call)** were made while writing this up and were not discussed with Sam. Everything else
was agreed in conversation (mockups: `.superpowers/brainstorm/18232-1792513222/content/explorer-v3.html`).

## 1. Intent

A public, always-on page that answers, for every matched Kalshi/Polymarket pair, *"is there arbitrage right now,
and if not, why not?"* — refreshed every minute from the same pipeline that produces the research note.

It is the showcase half of the project. Existing scanners (Oddpool, Kalshinomics, the Apify actors) rank pairs by
gross spread from mid or last prices. This page is different in three ways that are the project's actual
contribution: the edge is **net of fees and walked through both order books**, every pair carries a **verdict with
its reasons** (same instant? basis bound? jurisdiction?), and the page shows the **denominator** (how many checks
found nothing) so a flat line is evidence, not absence of data.

Sam's framing of success: a hiring manager opens the link, understands within a minute what is being measured and
why the answer is usually "no", and can dig into any pair down to the two books and the fee arithmetic.

## 2. Goals, non-goals, anti-goals

**Goals**
- Live pair explorer (layout C from the mockups): KPI strip, pair list, pair detail with both books, fee-walked cost
  at a chosen size, Plotly chart of net edge with annotations, links to both markets, verdict cards.
- Runs unattended on a free always-on server; survives the OptiPlex being unreachable.
- Same code for numbers as the pipeline: no edge, fee or basis logic re-implemented in JavaScript.
- Lives in the venue-arb repo, organized so the repo reads as one complete portfolio project when made public.

**Non-goals**
- Trading, order routing, account links, "copy this trade" buttons. Read-only, forever.
- Sub-minute latency. One refresh per minute matches the collector.
- Users, accounts, alerts, saved views. One public page.
- Mobile layout polish (must not break on a phone; need not be good on one).

**Anti-goals** (failure even if it works)
- Any inbound path from the internet to Sam's home network. The OptiPlex only ever pushes outbound.
- Republishing raw licensed series (CF Benchmarks BRTI values, Binance candles). Only derived numbers (basis bounds,
  "BRTI sits ~5.8 bps below Binance") appear. Venue quotes are shown with attribution; the builder checks Kalshi's
  and Polymarket's API terms on display and records the finding in `docs/venues.md`.
- A page that shows a gap without the fee and depth that erase it. Every edge on the page is net.
- Secrets or private data in the public repo (§7).

## 3. What the visitor sees

> Superseded in part (2026-10-22): dark default, KPI tiles and ≈200 KB superseded by v2 §3/§7.2.

Dark theme by default **(Claude's call;** theme is CSS variables, light is a swap). Desktop-first.

**Header:** project name, live dot + "books HH:MM:SS PT · next tick in N s", category filter chips, unit toggle
**¢ per contract / bps of cost** (default ¢; the other unit shown small beneath each figure — **Claude's call**), a
size box (contracts, default 1,000), and an "Executable from: anywhere | US" toggle (US hides pairs with a
Polymarket leg while `US_EXECUTABLE["polymarket"]` is false).

**Units convention (Sam):** prices and per-contract edge in ¢ (a contract pays $1, so the price is the
probability); anything multiplied by a size in $ (capacity, P&L at size, episode size); bps = net edge ÷ all-in
cost, for cross-venue comparison. Never a "$ per contract" figure.

**KPI strip (5 tiles):** executable episodes in the last 7 days (rule: ≥ 1¢ net, ≥ $100, ≥ 2 snapshots); best net
edge now with its pair and depth at that edge; pairs live, split exact / near-exact / ladder; ladder checks in the
last clock hour and violations (from the `ladder_admissible_checks` qa result and `ladder_edges`); screened history
share ≥ 1¢ gross since June (`screened_v2`).

**Pair list (left):** one row per live pair and direction: title, badge (exact / near-exact / ladder), settlement
countdown, legs ("K yes / PM no"), net edge in the chosen unit, 24 h sparkline. Sort by net edge; search; the
category filter applies. "Live" = both markets open and both books seen in the last 5 minutes.

**Pair detail (right):**
- Title; two leg chips linking to the market pages on kalshi.com and polymarket.com (ticker / event slug shown);
  a settlement chip ("BRTI 60 s avg ↔ Binance 1 m close").
- Verdict badge with its one-line summary: "Exact match · no edge", "Not an arbitrage · basis exposure",
  "Executable edge · +1.4¢ · $620", "Not executable from the US".
- Three boxes: Kalshi ask ladder (top `BOOK_DEPTH` levels, bars by size, levels the walk consumed highlighted); the
  cost of both legs per contract and at the chosen size (each ask averaged over the size, each venue's fee, all-in
  vs $1, net in ¢ and bps, $ at size; capacity at ≥ 1¢ net); Polymarket ask ladder.
- Plotly chart: net edge after fees over time, one line per direction; range buttons 1 h / 24 h / 7 d / all
  (all = hourly decimation); hover shows both asks and sizes, fees, net, capacity at that snapshot; clicking a point
  loads that snapshot's books into the three boxes. Annotations (toggleable): the 1¢ threshold line, episode
  markers, settlement and listing lines, basis-bound changes, shaded collector gaps (> 3 min without a snapshot).
- Four verdict cards, ✓ / ✗ / ◐: same instant (or exact match); basis bound (needed vs actual strike gap, with the
  99.9th-percentile window); fees & depth walked (net at the first slice); jurisdiction (anywhere / US).

**Footer:** the denominator sentence, data sources with attribution, "Read-only. No orders are placed.", link to
the research note and the repo, and — when the last bundle is older than 2 minutes — a visible
"last update N min ago" banner at the top of the page.

## 4. Architecture

```
OptiPlex (home, outbound only)                      Oracle Always Free VM (public)
┌─────────────────────────────────┐   rsync/ssh    ┌──────────────────────────────────┐
│ collector (60 s books)          │ ─────────────▶ │ /srv/venue-arb/bundle/live.json  │
│ Prefect flows (hourly scoring)  │   every 60 s   │ /srv/venue-arb/bundle/history/   │
│ NEW publish loop:               │   + hourly     │   pairs-YYYYMMDDHH.parquet       │
│   live bundle (≈200 KB JSON)    │                │ app: FastAPI + DuckDB + static   │
│   history Parquet (hourly)      │                │ Caddy (auto-HTTPS) → :443        │
└─────────────────────────────────┘                └──────────────────────────────────┘
```

**Producer (OptiPlex).** A third systemd user unit, `venue-arb-publish`, running a 60 s loop like the collector
**(Claude's call:** Prefect's scheduler is hourly-grained and heavier than a loop for this; the loop reuses the
collector's structure and the qa guards). Each tick: read the latest aligned snapshots for every live pair from
ClickHouse, run the existing walk (`edges.walk`, `ladder_edges.score_spread`) and fee functions, assemble
`live.json`, write it atomically, rsync it to the server. Each clock hour: write the trailing 7 days of per-pair,
per-snapshot results as one Parquet file and rsync it; delete server files older than 8 days through the same
restricted rsync. The loop must never block the collector: it is a separate process with its own DB connections.

**Transport.** `rsync` over SSH with a dedicated key, from the OptiPlex to a dedicated unprivileged user on the
server whose `authorized_keys` entry is restricted to `rrsync` on the bundle directory (no shell, no other paths).
The server never connects to the OptiPlex. The key lives in the OptiPlex user's `~/.ssh`, not in Bitwarden
**(Claude's call:** it's host-bound and unlocks nothing but a write-only directory).

**Server.** Oracle Always Free: the ARM `VM.Standard.A1.Flex` shape if capacity is available in Sam's region, else
the AMD micro **(Claude's call)**. Ubuntu LTS. One Python process (FastAPI, uvicorn) under systemd:
- serves the static page (HTML, CSS, Plotly.js `basic` bundle vendored, not CDN);
- `GET /api/live` returns the current `live.json` plus `age_s`;
- `GET /api/pair/{key}/history?range=` and `/api/pair/{key}/snapshot?ts=` query the Parquet files with DuckDB;
- `GET /api/walk?pair=&size=` re-walks the latest books at the requested size using `venue_arb.edges` — the one
  computation the server performs itself, so the size box is live;
- `GET /healthz`.
Caddy in front for HTTPS (Let's Encrypt on the domain, or Caddy's internal CA on the bare IP). No database on the
server; the bundle directory is the state.

**Freshness.** The page polls `/api/live` every 30 s; the header countdown is derived from `computed_at`. Age over
2 min shows the banner; over 1 h the KPI tiles grey out. The server never errors because the OptiPlex is away.

## 5. Data contracts

`live.json` (one object; builder fixes the exact schema, these fields are required):
- `computed_at`, `collector_last_snapshot_at`, `basis` per asset `{up, down, window_days, asof}`, `kpis` (§3),
  `admissible_checks_last_hour`, `violations_last_hour`.
- `pairs[]`: `key`, `kind` (exact | near_exact | ladder), `category`, `title`, `settles_at`, legs
  `{venue, market_id, side, url, title, settlement_source}`, `direction`, `net_edge_cents`, `net_edge_bps`,
  `capacity_contracts`, `capacity_usd`, `fees_cents`, `books` (top `BOOK_DEPTH` asks per leg: price, size),
  `verdict` `{label, checks: [{name, status: ok|fail|partial, detail}]}`, `spark` (24 h net edge, hourly).
- `episodes[]` (last 7 days, from `opportunities`) and `annotations[]` (settlements, listings, basis changes,
  collector gaps) for the chart.

History Parquet (one file per hour, trailing 7 days, partition by hour so the hourly push is one file):
`ts, pair_key, direction, k_ask, k_size, pm_ask, pm_size, fees_cents, net_edge_cents, capacity_usd` — one row
per aligned snapshot. Only the current hour's file is rewritten; older hours are immutable.

Market URLs: Kalshi from the ticker, Polymarket from the event slug in `listings.raw` (builder verifies both URL
shapes against live pages and records them in `docs/venues.md`).

## 6. Repo organization (single public repo)

```
venue_arb/            pipeline (unchanged)           dashboard/
venue_arb/publish.py  NEW producer loop                api/      FastAPI app, DuckDB queries, walk endpoint
deploy/               OptiPlex units + NEW server      static/   index.html, app.js, styles.css, plotly-basic.min.js
                      units + Caddyfile                sample/   a committed live.json + Parquet hour for tests
docs/ reports/ labels/ sql_checks/ tests/
```
The server app imports `venue_arb.edges` / `fees` (same package, installed on the server with the non-DB
extras) so the walk is one implementation. **(Claude's call:** one `pyproject`, with the dashboard's deps
(fastapi, uvicorn, duckdb) in an optional group so the OptiPlex doesn't install them.)

## 7. Going public

Before the repo's visibility changes (Sam's action, after the checklist passes):
1. Secret scan of the full history (gitleaks or equivalent). Anything found → rewrite history before publishing.
2. `deploy/*.service` carry `BWS_PROJECT_ID` (a Bitwarden project id, not a secret, but an identifier). **Theirs:**
   keep it in the units, or move it to the mise env on the box.
3. `data/`, `.env*`, `.superpowers/` stay ignored; `labels/` and `reports/` are fine to publish.
4. README rewritten as the front door: question, method, results so far, architecture diagram, link to the live page,
   how to run. License chosen by Sam.
5. The live page's footer links to the repo; the repo's README links to the page.

## 8. Constraints

- Read-only venue endpoints only; no order, account or position calls anywhere, including the server.
- No inbound connection to the home network; no VPN; no geoblock circumvention.
- Existing CI (`ruff`, `pytest -m "not integration"`) stays green; the dashboard gets its own unit tests (bundle
  builder on fixtures; API on the committed sample bundle; walk endpoint against `edges.walk`).
- LLM budget untouched (the dashboard makes no LLM calls). Server cost: $0 (free tier). If Oracle reclaims the free
  VM, the page is reinstallable from `deploy/` in under an hour; the OptiPlex keeps the data.
- Migrations additive; the producer reads only.

## 9. Left to the builder

- Exact `live.json` schema and the pair `key` format; Plotly figure construction (Python-side figure JSON vs.
  client-side traces — pick one and keep annotations server-generated).
- Server provisioning script (`deploy/server/`): user, rrsync, Caddy, systemd, firewall (443 and 22 only).
- How the chart's "click to load this snapshot" fetches books (from history Parquet; books are not in history, so
  either store top-3 levels per row or reconstruct from `book_levels_v2` at publish time — builder decides, size
  budget: history ≤ 50 MB per 7 days).
- Light theme switch, mobile sanity.

## 10. Calls made after the builder check (Claude's calls, all)

A reviewer read §1–9 as a builder and asked six questions. None needed Sam; the answers are binding on the plan:

1. **"Net edge" is the first-slice net edge** — `edges.walk`'s `best_edge` (first ask on each side, minus each
   venue's fee), the number the pipeline already stores per snapshot. It is what the pair list, sparklines, chart
   lines, history Parquet, KPIs and episodes use, and it can be negative. The size box changes only the cost box, via
   `/api/walk`, which uses a NEW pure function `edges.fill(k_asks, pm_asks, fee_k, fee_pm, contracts)` that fills
   the requested size through both visible ladders and returns average prices, fees, all-in cost and net at that
   size; when the visible depth (`BOOK_DEPTH` levels) can't fill the size it reports "insufficient visible depth"
   and the size it could fill — never extrapolates.
2. **Venue display terms are Task 1 of the plan**, before any UI work. Outcomes: allowed → as designed; not allowed
   or ambiguous → the books boxes and hover books switch to a derived-only variant behind a flag (average fill price,
   fees, net; no ladders), the finding goes to `docs/venues.md`, and Sam is told. The committed `dashboard/sample/`
   fixture is **synthetic** either way (real tickers, invented quotes), so no real quotes live in the repo. The
   same task verifies the Kalshi fee formula against Kalshi's published fee schedule and records the source; until
   verified the footer carries "Kalshi fee coefficient from public docs; unverified against fills".
3. **Ladder rows are collapsed to one per Kalshi strike and direction**, showing the best (nearest admissible)
   Polymarket strike; near-exact rows are separate with their badge. The list groups ladder rows under a collapsible
   ladder header ("BTC · 16:00Z · 28 strikes"), collapsed by default, so the top of the list is exact and near-exact
   pairs plus any ladder row with positive edge. Budget: ≤ 400 rows in `live.json`.
4. **Transfer cost is a separate line, not part of per-contract net.** Per-contract net = fees only (the pipeline's
   definition, so the 1¢ episode rule is unchanged). The cost box at size shows "Cross-venue transfer: $10 + 10 bps"
   (`fees.transfer_cost`) and a "P&L at size after transfer" line in $; the verdict for "executable" uses the
   pipeline's episode rule, and the badge text says "+1.4¢ · $620 before transfer · $598 after".
5. **A ladder violation = an `arb`-kind `ladder_edges` row with `capacity_contracts > 0`** (≥ 1¢ net on at least
   its first slice), counted over the same clock hour as `ladder_admissible_checks`, both read from the hourly
   `ladder_score` run. The KPI is labelled "last scored hour" with its window, not "last hour".
6. **US mode greys rather than hides.** Every Kalshi/Polymarket pair is non-executable from the US today, and that
   *is* the finding: the US toggle greys all affected rows, sets the executable KPIs to 0, and shows a banner
   "No pair is executable from the US: Polymarket rejects US orders (docs/venues.md). Gaps shown for reference." If a
   US-executable venue is added later nothing changes but `US_EXECUTABLE`.

## 11. Sam's actions

- Create the Oracle Cloud account and the VM (Claude cannot create accounts); provide its public IP and install the
  OptiPlex's push public key for the `venue-arb` user when the provisioning script is ready.
- Optional: a domain pointed at the VM.
- Decide §7 item 2 and the license; flip the repo to public after the checklist.
