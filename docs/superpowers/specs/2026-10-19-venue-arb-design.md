# venue-arb: cross-venue market matching and arbitrage measurement

Design, 2026-10-19. Status: draft for Sam's review.

Calls marked **(Claude's call)** were made while writing this up and were not discussed with Sam. Everything else
was agreed in conversation.

## 1. Intent

Liquidity structure differs a lot between venues: Kalshi vs Polymarket, a stock vs its perpetual future on
Hyperliquid. The hypothesis is that these differences leave prices for the *same underlying thing* out of line often
enough, and by enough, to be real arbitrage.

The project answers that with evidence first, then shows it live:

1. **Research:** do cross-venue price gaps exist, how big are they, how long do they last, and how much of them
   survives fees, order-book depth, and differences in contract terms?
2. **Live demo:** a small public page that shows matched markets side by side and flags current gaps, built on the
   same matching and scoring code.

The research earns the demo its credibility. This is also a portfolio project for Sam's job search, so the
methodology has to stand up to a skeptical reader.

## 2. Goals, non-goals, anti-goals

**Goals**
- A matching layer that links listings across venues through a shared hierarchy and explains every link.
- Scoring that separates **exact matches** (true arbitrage) from **near-matches** (relative value), and prices the
  differences in near-matches instead of discarding them.
- A research write-up that answers the four questions in §1 with numbers.
- A live demo page fed by the same pipeline.

**Non-goals**
- Executing trades. No code path calls an order, account, or position endpoint on any venue.
- Latency-competitive arbitrage (co-location, sub-second reaction). Seconds-to-minutes resolution is enough to measure.
- Live data from venues that block US users (see §5.3).

**Anti-goals** (failure even if it technically works)
- Reporting gross price gaps as arbitrage without fees, executable depth, and term differences taken out.
- Matches the system can't explain in plain words ("same event because X, differs by Y").
- A public page that republishes raw licensed quotes (vendor terms forbid it), or data obtained by getting around a
  venue's geoblock.

## 3. The matching tree

Every listing from every venue is placed in a shared four-level hierarchy, and matching happens top-down, so
comparisons only happen inside a branch:

1. **Category**: macro/Fed, crypto prices, equities, elections/politics, sports.
2. **Event**: the real-world thing, e.g. "December 2026 FOMC decision", "NVDA close on 2026-11-20", "BTC price on
   2026-12-31".
3. **Market**: one venue's question about the event, e.g. Kalshi "Fed cuts 25bp in December?", trade.xyz NVDA perp.
4. **Contract/outcome**: the leg that actually trades (Yes/No, a price bucket, a perp).

Stocks and perps attach at the event level through their underlying. A Kalshi "NVDA above $X on date Y" market, the
NVDA perp, NVDA shares, and NVDA listed options all hang off the same underlying and are compared through an implied
probability.

**Matching method (Claude's call):**
- Deterministic rules first, where venues expose structure (Kalshi series/event tickers, Polymarket event slugs and
  tags, perp symbols to stock tickers).
- Within a category, embedding similarity (pgvector, already on the platform) proposes candidate market pairs.
- An LLM adjudicates each candidate and returns a structured diff: same event or not, outcome mapping (which
  side of A corresponds to which side of B), and differences in deadline, threshold, and resolution source, each
  with a one-line reason.
- Adjudicated matches are stored with the diff, confidence, model version, and timestamp. Sam reviews a sample before
  any match feeds the research numbers; low-confidence matches stay out until reviewed.
- **Trust bar (Claude's call, Sam can raise it):** at least 95% precision on a review sample of 200 matches,
  stratified by category and confidence band. Recall is estimated on a smaller hand-built set of known pairs
  (e.g. every FOMC and BTC-price event both venues list), because missed matches shrink the opportunity set the
  research reports.
- Adjudications are cached per market version (title, rules text, deadline). A market is re-adjudicated only when
  those change.

## 4. Scoring

All scoring uses **executable prices from order books at a stated size**, never mid or last trade.

**Whose arbitrage.** The headline measure is arbitrage that *someone, somewhere* could execute: both legs tradable by
some eligible participant, even if no single jurisdiction allows both. Because capital has to sit on two venues, costs
include moving money between them (deposit and withdrawal fees, USDC vs USD conversion, transfer time), not just
trading fees. The write-up states this perspective up front. A **jurisdiction view** is an added cut: the subset of
opportunities executable within one jurisdiction, starting with the US (Kalshi, Polymarket US, CDNA, Rothera,
ForecastEx). Each venue's eligibility by jurisdiction is recorded in the venue notes.

**Exact matches (arbitrage).** For a binary event: buy Yes on venue A and No on venue B (or the reverse). The
opportunity is `1 − (askYes_A + askNo_B) − fees` per $1 contract, computed at the depth actually available. Also
reported:
- **Capacity**: dollars executable before the edge goes to zero.
- **Duration**: how long the edge stays positive across snapshots.
- **Annualized edge**: capital is locked until resolution, so a 2¢ edge on a market resolving in a year is weak.

**Near-matches (relative value).** The gap is decomposed into an explained part and a residual. Only the residual
counts as opportunity:
- **Deadline difference**: priced with a simple hazard model over the gap between the two dates.
- **Threshold difference** (e.g. "BTC > 100k" vs "BTC > 105k", or "NVDA above X"): priced from the option-implied
  distribution: Deribit options for BTC/ETH, listed equity option chains for stocks.
- **Resolution-source difference**: flagged and labeled as basis risk, not priced.

**Perp vs stock (basis).** Perp mark price vs a reference spot, net of the funding rate. The reference depends on the
session:
- Regular hours: consolidated US quotes.
- Overnight (Sunday to Thursday, 8pm–4am ET): Blue Ocean ATS.
- Weekends: there is no reference. Compare perp against perp, and the weekend perp against Monday's open. Whether
  weekend perps predict the open, and which venue gets there first, is a research question in its own right.

**Opportunity threshold (Claude's call, tunable in config):** a net edge of at least 1¢ per $1 contract (or 10 bps
for basis), with at least $100 of executable depth, present in at least two consecutive snapshots.

**Fees** live in a per-venue config table, taken from each venue's current fee docs at build time and dated.

**History vs snapshots (Claude's call).** Venues keep trades and candles, not order-book history, so results come in
two clearly labeled tiers that are never mixed in one number:
- **Screened**: gaps found in historical trades or candles (including the Binance/Bybit archives). These say where and
  when gaps tended to appear, not whether they were executable.
- **Executable**: gaps scored from our own order-book snapshots under the rules above. Only this tier is called
  arbitrage. Snapshot collection for matched markets starts as early as possible in phase 2, because the executable
  tier needs weeks of it.

## 5. Venues and data

### 5.1 Prediction markets
| Venue | Role | Notes |
|---|---|---|
| Kalshi | core, live | Largest by far. Historical split: markets settled before `/historical/cutoff` use `/historical/...` endpoints with different field names. |
| Polymarket International + Polymarket US | core, live | Separate venues for volume; US is forward-only through its WebSocket. |
| Crypto.com (CDNA), Rothera | core, if APIs allow | Public data access **not yet checked**; first task in their phase. |
| Hyperliquid Outcomes | core, live | Same venue as the perps: a clean intra-venue pair. |
| ForecastEx | secondary | Small, but its econ/Fed contracts overlap Kalshi's. |
| Predict.fun, Opinion | secondary | The smaller-venue check. |

### 5.2 Perps and options
| Venue | Role |
|---|---|
| Hyperliquid (incl. trade.xyz HIP-3 stock perps) | core live perps venue, and the main live stock-perp venue |
| OKX | live perps |
| Deribit | live perps, and BTC/ETH options for implied probabilities |

### 5.3 Geoblocked venues (research only, from official archives)
Binance futures (HTTP 451) and Bybit (HTTP 403) refuse US connections, tested from the OptiPlex on 2026-10-18. Both
are used **only** through their official public archives, which load fine from the US:
`data.binance.vision` (daily futures trades and 1-minute klines) and `public.bybit.com/trading`. Tardis.dev is the
paid fallback if more detail is needed. These venues are a day behind and appear in the research, not in live
detection or the demo. No VPN or non-US routing.

### 5.4 Stock and options data
| Source | Use |
|---|---|
| Alpaca Free | prototyping: real-time but IEX-only (~2.5% of volume), 30 streamed symbols |
| Schwab Trader API | research-grade quotes, option chains, price history. Market Data product **only**: the app must not have the Accounts and Trading product. Refresh tokens expire every 7 days (manual browser re-login). Personal use: never shown raw on the demo. |
| Alpaca `boats` feed | overnight Blue Ocean prices |

Status: Sam's Schwab Trader API subscription was pending on 2026-10-18; build against Alpaca until it's ready. If
Schwab's live stream turns out to need the trading product, poll the REST endpoints instead (~120 requests/minute)
rather than enabling trading.

### 5.5 Budget
- LLM and embeddings: under about $20/month. Use a cheap model for adjudication, prune candidates hard, and cache
  per market version.
- Paid data (Tardis, Alpaca's paid tier, options vendors): none without Sam's approval.

## 6. Where it runs

**Research: the OptiPlex**, as a project on Sam's existing data platform (`~/platform`), following
`~/platform/docs/NEW_PROJECT.md` and `~/platform/.agent-rules/hard-rules.md`:
- Repo `~/projects/venue-arb`; database role and schema `venue_arb` **(Claude's call on both names)**.
- Collectors run as long-lived containers (127.0.0.1 only).
- Prefect schedules jobs: catalog refresh, matching, scoring, backfills, archive pulls.
- ClickHouse holds time series (order-book snapshots, trades, reference prices, funding). Postgres holds the matching
  tree, matches, fee config, and opportunities.
- Every load goes through the platform's `qa` layer.
- Box limits: 10 GB WSL RAM shared with the platform; aging HDD until the SSD swap.

**Live demo: a free always-on server.** Oracle Cloud Always Free **(Claude's call, to verify)** runs a slim copy of
the live collectors and scoring for the matched set. A free static page (Cloudflare Pages) shows **only computed
values**: matched pairs, net gaps in cents or bps, implied probabilities, flags. Never raw vendor quotes.

**How the demo gets its matches (Claude's call).** The demo never matches anything itself. The OptiPlex pushes the
reviewed matched set out to the demo server, and only reviewed matches appear publicly. That's an outbound
connection, which the platform's hard rules allow (they forbid public inbound ports). The demo server holds only
venue read keys.

**What the demo may show (Claude's call, gated on terms).** Prediction-market venue prices are shown side by side
only for venues whose public data terms allow display. Each venue's terms are checked and recorded in the venue
notes before it appears. Schwab, Alpaca and options-vendor data are never shown raw. Stock-perp basis and
option-implied pricing appear on the demo only if a reference source whose terms allow publishing derived values
is found. Until then those stay in the research notes.

**Not used:** Inngest. It schedules work but doesn't supply compute, and Prefect already does the scheduling here.

## 7. Phases

1. **Setup**: repo per the runbook (👤 Sam creates Bitwarden secrets), venue fee table, venue API notes.
2. **Catalog and matching, narrow first**: Kalshi + Polymarket, categories Fed/macro and crypto prices. Build the tree,
   matching, and the review loop. Done when Sam has reviewed a sample and the precision is good enough to trust.
3. **Exact-match research**: order-book snapshots for matched markets plus historical backfill. Research note v1:
   do exact arbitrages exist net of fees and depth, how big, how long, how much capacity?
4. **Widen**: remaining prediction venues, near-match pricing (deadline, threshold), perp vs stock basis
   (Hyperliquid/trade.xyz vs Alpaca/Schwab), Binance/Bybit from archives, weekend behavior. Research note v2.
5. **Live demo**: Oracle server, static page.

## 8. Left to the builder
- Libraries, table schemas, module layout (within the platform conventions).
- Snapshot cadence per venue (start around 60s for matched markets; use WebSockets where a venue offers them).
- Embedding model and the LLM used for adjudication (a current low-cost Claude model, within §5.5), and prompt design.
- Exact hazard-model form and option-implied-probability method.
- Demo page framework and design.
- Format of the research notes.
