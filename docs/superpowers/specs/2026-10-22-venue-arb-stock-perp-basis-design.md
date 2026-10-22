# venue-arb: stock-perp basis (Hyperliquid trade.xyz vs the underlying)

Design, 2026-10-22. Status: reviewed by a builder check 2026-10-22; Sam approved the cost definition (§5). Awaiting Sam's approval of the whole. This is phase 4 ("Widen") of
`2026-10-19-venue-arb-design.md`, narrowed to one slice: stock perpetuals on Hyperliquid's trade.xyz builder
exchange against the stocks they track. Everything the v1 design and the dashboard designs say still holds unless
this document changes it. Calls marked **(Claude's call)** were made while writing this up and were not discussed
with Sam.

## 1. Intent

Sam's question, in his words: do perp markets follow the underlying equity and its derivatives; if not, what is the
spread; and can we take advantage of the deviation. The first two are measurement, the third is the executable tier
the project already runs for prediction markets, applied to a venue where the two legs are a perpetual future and a
share.

Two questions share one collector and must never be reported as one number:

- **The spread.** During the hours the stock trades, does the perp price sit away from the stock, by how much, and
  could someone have taken it after fees, funding and depth. This is executable-tier work.
- **The weekend.** From Friday 8:00 PM to Sunday 8:05 PM ET the stock does not trade and the venue's oracle floats
  on the perp's own price. How much of the Friday-to-Monday move did the weekend perp already contain, and does the
  Korean open on Sunday evening answer it before New York does. This is a forecast test, not an arbitrage test.

**Why now.** The dashboard is live and the prediction-market tiers are collecting on their own. Stock perps are the
finding that reads to a trading desk: "stock perps on a crypto venue misprice the forward by X bps for Y minutes a
day" is a claim they can weigh, and the live page is one people could use in practice, which Sam wants it to be.

## 2. Goals, non-goals, anti-goals

**Goals**
- A basis series per name: perp mark minus its reference, in bps, with the reference and its age on every point.
- An executable tier for the spread: two legs stated as a trade, fees and funding netted, capacity bounded.
- A weekend tier: descriptive at first (hit rate, share of the move contained), one row per name per weekend.
- A "Stock perps" category on the Scanner, an Explorer view per name, a Research card per question, all from the
  same 60-second bundle the page already reads.
- The 45 days of hourly history Hyperliquid serves, backfilled once, so the weekend tier has six weekends on day one.

**Non-goals**
- Sub-minute latency. Sam: "every 60 seconds is fine, we aren't building a high frequency trading platform".
  Snapshots stay at 60 s; the publisher and the push stay as they are.
- Order entry, account links, alerts, any trading affordance. The page says where the edge is.
- Pre-IPO perps (SpaceX, Quantinuum, Cerebras), index, FX and commodity perps: no reference exists, or only a proxy
  with its own tracking basis. Out of scope; they may be collected later for the weekend question only.
- Option-implied forwards from Schwab chains. Deferred (§6): the venue's oracle already embeds carry, and the
  spread question is answerable against the quote first.
- A vol model for pairs with a time gap, near-match pricing, the Binance/Bybit archives, remaining prediction
  venues: the rest of phase 4, later.

**Anti-goals**
- A row that shows a spread without the trade that captures it, its fees, and the reference it was computed from.
- A number that is fast but opaque. Sam: the discrepancy between what the page shows and what it was computed
  from is what matters, so provenance beats speed.
- Showing either venue's raw quotes while the terms are unread (same rule as `DASHBOARD_SHOW_BOOKS`).
- Mixing the weekend tier's figures into the executable tier's, or calling either "arbitrage" on the strength of
  the other.

## 3. Scope

**Names.** Every equity and equity-ETF perp on trade.xyz: 104 of the 138 listed on 2026-10-22, read from the venue
(`perpDexs` / `meta` with `dex = "xyz"`) at catalog time, not hard-coded. Classification is by a small tracked table
`labels/stock_perps.csv` (perp symbol, reference kind, reference symbol, home exchange, currency) **(Claude's call)**;
a new perp with no row is collected (mark, oracle, funding, book) but has no reference until the row is added, and
the catalog flow records a warn naming it.

**References**, by name, in this order of preference during the hours each exists:

| Reference | Covers | Hours | Source |
|---|---|---|---|
| Consolidated US quote | the ~85 US-listed names (Nasdaq/NYSE/Arca/Cboe, ADRs included: TSM, BABA, ASML, ARM, NOK, UMC, SKHY) | 4:00 AM to 8:00 PM ET, weekdays | Schwab Market Data `quotes`, batched |
| Home-exchange price | the ~19 Asia-listed names (KRX: SKHX, SMSN, HYUNDAI; TSE: SOFTBANK, KIOXIA, IBIDEN; SSE/SZSE: CAMBRICON, CXMT, GIGADEV, UNITREE, AMEC, INNOLIGHT, CATL, YMTC; HKEX: SHEIN; NSE: JIO, RELIANCE; and MINIMAX, ZHIPU) | home session, converted to USD with the matching Pyth FX feed | Pyth Hermes, `Equity.<CC>.<code>/<CCY>` |
| Second opinion | the US names | US session | Pyth `Equity.US.*` |
| Venue oracle | every name, always | always; alone overnight (Blue Ocean through the oracle) and on weekends | Hyperliquid `metaAndAssetCtxs` |

The venue's oracle is externally derived Sunday 8:05 PM to Friday 8:00 PM ET for US names (pre-market, market,
post-market, overnight via Blue Ocean ATS) and for Asian names during their home sessions. Outside those windows the
external price freezes and the oracle advances as an exponentially weighted moving average of the perp's own impact
price with a 30-minute time constant; funding settles against the oracle. So on weekends the perp is anchored to
nothing but itself, which is the weekend tier's whole premise.

## 4. What the visitor sees

**Scanner.** A "Stock perps" category chip. One row per name: *Name* (ticker, company, home exchange tag), *Trade*
("short perp / long stock" or the reverse), *Basis* (signed bps, perp mark minus reference; the unit switch adds ¢
per $1 and ROI), *Funding* (signed rate per hour and time to the next payment), *Capacity* ($ at ≥ 10 bps net, the
smaller of the two legs), *Reference* (which one and its age, e.g. "Schwab 14 s", "oracle only"), *Verdict*. Rows
whose reference is the oracle alone are shown with that said in the Reference cell and never carry the executable
verdict (§5). Sorted by net basis, same mechanics as the prediction-market rows.

**Explorer.** Per name: the basis chart over the chosen range with the reference switches drawn as bands (session,
overnight, weekend) and funding ticks marked; the cost panel stating the trade in words with each cost line (perp
taker fee, half the stock spread, borrow assumption when short stock, funding over the window); the verdict cards.

**Research.** Two cards. *Stock-perp spread*: episodes, max net basis per hour, share of session-hours with |basis|
over 10 bps, by name. *Weekends*: per weekend per name, the perp's Friday-close, Saturday-noon and Sunday-6-PM
prices against Monday's open (and for Korean names Sunday 8 PM ET), hit rate of direction, share of the move
contained; a plain sentence that this is a prediction test with N weekends and no claim of exploitability.

**Everywhere.** The US cut is stated as zero for the perp leg (Hyperliquid blocks US traders) and the stock leg as
US-executable, so the two-leg trade is "someone, somewhere" like Polymarket. No raw quotes. One line: not advice.

## 5. The measurement

**Basis.** `basis_bps = (perp_mark / reference - 1) * 1e4`. The reference is the best available per §3 at the
snapshot instant, named on the row. Mark is the venue's `markPx` (median of oracle, oracle plus a 150 s EWMA of the
mid-to-oracle gap, and the book's mid/last); the book's own mid is stored too, and the executable tier walks the book,
not the mark.

**The trade (executable tier).** Perp above reference: short the perp, long the stock. Below: long the perp, short
the stock.

- Perp leg: walked slice by slice down the venue's book (asks when buying, bids when selling), taker fee per the
  venue's schedule for that name (growth-mode names 0.9 bp, standard 9 bps base; the fee table is read from the
  venue's `meta` and dated, like the Kalshi and Polymarket fee entries).
- Stock leg: capped at the displayed top-of-book size on the reference quote; charged half the quoted spread;
  when the stock is the short leg, a borrow assumption of 50 bps annualized prorated to the holding window
  **(Claude's call)**, reported as a sensitivity (how many episodes survive at 10x), like the transfer cost today.
  Asia-listed names have a Pyth price and confidence interval but no displayed size, so their stock leg has no
  capacity figure and their rows are basis-only (no executable verdict) **(Claude's call)**.
- **Costs are the round trip, carry is a stated 24-hour hold (Sam, 2026-10-22).** A basis trade pays only when
  the gap closes, which means exiting both legs, and it carries funding and borrow while held. So, per snapshot:
  `net_bps = gross_bps − 2 × perp taker fee − full stock spread − 24 h of funding (signed by side) − 24 h of borrow
  (short-stock side only)`, all in bps of notional. The 24 h hold is an assumption printed on the page; a sensitivity
  line reports how many episodes survive at a 1 h and a 7 d hold. Funding is the venue's hourly rate × 24; the
  position pays or receives it by side. For growth-mode names the round trip is under 2 bps; for standard-fee names
  it is 18 bps before carry, so most of them never clear 10 bps, which is the honest result.
- Episode rule: `net_bps ≥ 10`, ≥ $100 fillable on the smaller leg, in ≥ 2 consecutive snapshots, 150 s gap breaks
  the run. `episodes.find` is reused with its threshold and payout arithmetic parameterised (it hard-codes a 1¢
  edge and a `close_time` for annualisation; stock perps have no close, so annualised edge is `net_bps` over the
  stated hold) **(Claude's call)**.
- Episodes are stored with `source = 'stock_perp'`. The pair-tier queries read `source = 'pair'` and the ladder
  tier `'ladder'`, but `publish.EPISODES_SQL` and `publish.scope_episodes` currently keep every non-pair row, so
  both must exclude `'stock_perp'` from the header's "episodes · 7 d" and the bundle's `episodes[]` (the anti-goal
  in §2 decides it; the stock-perp figures get their own fields).

**The weekend tier.** For each name and weekend: `p_fri` = last perp mark before Friday 8:00 PM ET, `p_sat` = mark at
Saturday 12:00 PM ET, `p_sun` = mark at Sunday 6:00 PM ET, `p_open` = first reference print after the weekend (Sunday
8:00 PM ET for KRX names, Monday 4:00 AM ET pre-market and 9:30 AM ET regular for US names; both recorded).
Reported: direction hit rate of `sign(p_sun − p_fri)` against `sign(p_open − p_fri)`; share of the move contained,
`(p_sun − p_fri) / (p_open − p_fri)` with the denominator floor stated; and the same for `p_sat`. Shown from the
first weekend with a count; no significance claim until the builder's test says there are enough weekends (a sign
test at the usual level, stated).

**Screened history.** Hyperliquid serves ~45 days of hourly candles (`candleSnapshot`, interval `1h`) and hourly
`fundingHistory` per perp. Backfilled once at deploy into a screened table (hour, open, close, volume, funding).
The weekend tier reads perp candles from it for weekends before the collector started; the reference side of those
weekends comes from Schwab `pricehistory` (daily open) for US names and is absent for Asia names until the
collector has run. Screened and executable tiers are labelled apart, as elsewhere.

## 6. What changes behind the page

- **Catalog.** `venue_arb/sources/hyperliquid.py`: `meta` and `metaAndAssetCtxs` for `dex = "xyz"`, `l2Book` per
  name, `fundingHistory`, `candleSnapshot`. Public, no key. Weight budget 1,200/min per IP; a 60 s sweep of 104
  books costs ~208.
- **References.** `venue_arb/sources/schwab.py`: OAuth (access token 30 min, refresh token 7 days, no programmatic
  renewal of the refresh token), `quotes` batched (one call for all US names each tick; 120 calls/min cap),
  `pricehistory` for the backfill. `venue_arb/sources/pyth.py`: Hermes `latest` for the Asian equity feeds, their
  FX feeds and the US second opinions.
- **Schwab is approved** (Market Data Production, "Ready For Use" 2026-10-20) and `SCHWAB_CLIENT_ID` /
  `SCHWAB_CLIENT_SECRET` are already in the project's Bitwarden secrets. No refresh token exists yet. Build order:
  Hyperliquid collector, backfill and the weekend tier first (they need nothing from Schwab), then the Schwab leg
  and the executable verdicts **(Claude's call)**.
- **Schwab token upkeep (Claude's call).** The `secrets` wrapper is read-only `bws run` and injects secrets at
  process start, so a new refresh token cannot reach a running collector. A CLI
  (`python -m venue_arb.sources.schwab login`) prints the login URL, Sam signs in and pastes the redirect URL; the
  CLI exchanges it, writes `SCHWAB_REFRESH_TOKEN` into the Bitwarden project with a write-capable BWS access token
  that lives only in Sam's interactive shell (never in a unit or the repo), then restarts `venue-arb-collector`.
  One command plus a browser login, weekly. The collector records a qa warn `schwab_token_expires` 24 h before
  expiry (the token's issue time is stored beside it) and `schwab_auth` the moment a call fails on auth; the page
  marks the Schwab reference stale and rows fall back to Pyth, then the oracle, with the Reference cell saying so.
  Sam accepted the weekly login.
- **Collector.** The existing 60 s collector gains a stock-perp watchlist: per name, mark, oracle, funding rate, next
  funding time, top N book levels; per tick, one Schwab batch and one Pyth batch. New ClickHouse tables, additive
  (`stock_perp_books`, `stock_perp_refs`, `stock_perp_candles`), daily partitions, same TTL policy as `book_levels_v2`.
- **Visibility flag.** `DASHBOARD_SHOW_STOCK_PERPS = False` until Sam's terms read (§8); the collector, scoring and
  bundle fields run regardless, the Scanner chip and Research cards render only when it is on **(Claude's call)**.
- **Scoring.** Prefect flow `stock_perp_score` on the hour (slot `:55` **(Claude's call)**), rescoring the last 3 h
  like `ladder_score`; writes `stock_perp_edges` and episodes with `source = 'stock_perp'`. A `weekend` flow Monday
  after the US open writes one row per name per weekend.
- **Bundle.** `Pair` gains the fields the Scanner needs (`funding_bps_h`, `next_funding_at`, `reference`,
  `reference_age_s`, `basis_bps`, legs described); `ResearchSummary` gains the two cards' figures; `copy.METHOD`
  gains a `stock_perps` paragraph. `LIVE_BYTES_BUDGET` is revisited (104 names × spark; see the open `live_bytes`
  warn).
- **Terms.** `docs/venues.md` gains Hyperliquid, trade.xyz, Pyth and Schwab entries: data-display terms (Hyperliquid's
  terms page is a JavaScript shell and the site returns 403 to fetches; Pyth's off-chain display terms unread; Schwab
  app is personal use), US access (Hyperliquid blocks US traders; read access is public), fee schedules dated. Sam
  reads them before the category is public (§8).

## 7. Constraints

- Everything in the v1 and dashboard designs §8/§5: no inbound path home, no raw quotes, CI before push, no
  Co-Authored-By, additive migrations, read-only public endpoints, no orders, no geoblock circumvention.
- 60 s snapshots; the publisher's one-minute bundle and push are unchanged.
- Schwab: Market Data product only; refresh token in Bitwarden; never shown raw; the stock leg degrades gracefully
  without it.
- Budget: Hyperliquid and Pyth are free; Schwab is free with the brokerage account; no paid data without Sam.
- The reference on every stored row and every rendered number (kind, symbol, timestamp); a row with no reference is
  stored and shown as oracle-only, never scored.

## 8. Sam's actions

- Read the Hyperliquid/trade.xyz, Pyth and Schwab terms on public display of derived figures; decide whether the
  category ships publicly or stays behind the `DASHBOARD_SHOW_BOOKS`-style flag.
- Run the Schwab login once at deploy and weekly after (one minute; the collector warns a day ahead).
- Review the `stock_perps` method paragraph once rendered.

## 9. Left to the builder

- Table schemas, the exact ClickHouse partitioning and TTL for the three new tables, and the book depth stored.
- Whether the Hyperliquid book is fetched over REST per name or one WebSocket subscription set; both fit the budget.
- The Pyth confidence-interval rule for marking a thin print (e.g. drop the reference when `conf / price` exceeds a
  config constant).
- The sign-test threshold for the weekend tier's "enough weekends" sentence.
- Exact Scanner column widths and the Explorer's band rendering; Plotly figure specs for the two cards.
- The `labels/stock_perps.csv` initial fill (from the venue's spec index and the listings) and its test.
