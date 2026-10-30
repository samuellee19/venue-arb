# Venue notes

API facts and dated reading notes per venue (endpoints, rate limits, fees, sessions, access). Each entry is dated and
links the venue's own docs. Anything the docs did not settle when fetched is marked "unverified". Re-check before
relying on a number: fees and geoblocks change. The terms of every venue, in one table with the decisions taken, are in
`docs/terms.md`; the clause quotes there come from the sections below.

## Kalshi

### Read endpoints and rate limits (2026-10-19)
- Base URL: `https://external-api.kalshi.com/trade-api/v2`. The market-data quick start says "No authentication
  headers are required for the endpoints in this guide." Paths used there: `/series/{series_ticker}`,
  `/markets?series_ticker=...&status=open`, `/events/{event_ticker}`, `/markets/{market_ticker}/orderbook`.
  Source: <https://docs.kalshi.com/getting_started/quick_start_market_data>
- Rate limits are token budgets per second per tier, not request counts. Basic: 200 read / 100 write tokens/s;
  Advanced 300/300; Expert 600/600; Premier 1,200/1,200; Paragon 2,400/2,400; Prime 4,800/4,800; Prestige
  12,000/9,600. Most requests cost the default 10 tokens, so Basic sustains about 20 read requests/s.
  Source: <https://docs.kalshi.com/getting_started/rate_limits>
- Whether unauthenticated callers fall under the Basic budget: unverified (the page speaks of authenticated requests).
  This project stays well under 20 requests/s regardless.

### Hourly candlesticks (2026-10-19)
- `GET /series/{series}/markets/{ticker}/candlesticks?period_interval=60` (settled markets: `/historical/markets/
  {ticker}/candlesticks`). `end_period_ts` is the "Unix timestamp for the inclusive end of the candlestick period";
  `yes_bid.close_dollars` / `yes_ask.close_dollars` are the "Offer price on the market at the end of the candlestick
  period". So a candle's close quote applies at `end_period_ts`, and the screened tier keys it there (not at the
  period start). Source: <https://docs.kalshi.com/api-reference/market/get-market-candlesticks>
- Polymarket `/prices-history` prints land a few seconds past each hour; the screened tier pairs each Kalshi close
  with the print nearest that instant within 30 minutes (`venue_arb/screened.py`).

### Taker/maker fee formula (2026-10-19)
- From the fee schedule as summarized by search (the PDF itself returned HTTP 429 when fetched, so this is
  secondhand): taker fee = `0.07 x C x P x (1 - P)`, maker fee = `0.0175 x C x P x (1 - P)` where it applies, both
  rounded up to the next cent; C = contracts, P = price in dollars. Peak taker fee at 50c is about 1.75c/contract.
- Kalshi's help page says "Some markets have fees that are different from those of other markets" and that maker
  fees apply only to some markets, charged only when a resting order trades. Which series carry maker fees:
  unverified.
- Sources: <https://kalshi.com/docs/kalshi-fee-schedule.pdf>, <https://help.kalshi.com/en/articles/13823805-fees>.
  Verify the formula against the PDF before using it in P&L.

### Fee verification attempt (2026-10-19, Task 2)
- Fee schedule PDF <https://kalshi.com/docs/kalshi-fee-schedule.pdf>: HTTP 429 (Vercel Security Checkpoint) on
  two further tries today, after the same on 2026-10-18/19. Coefficient 0.07 stays **secondhand, unverified**.
- <https://help.kalshi.com/en/articles/13823805-fees> (page dated April 19, 2026) fetched OK but gives no
  coefficient: "The complete Fee Schedule, and the math behind the fees, are posted at the bottom of our website".
- <https://docs.kalshi.com/api-reference/market/get-series> defines `fee_type` ('quadratic' = "General Trading Fees
  Table", 'quadratic_with_maker_fees' = that table plus maker fees, 'quadratic_with_combo_maker_fees', 'flat') and
  `fee_multiplier` ("a floating point multiplier applied to the fee calculations"), no numbers.
- <https://docs.kalshi.com/getting_started/fee_rounding>: trade fee is "rounded up to the nearest $0.000001" per
  fill, then a rounding fee restores the account's balance precision ($0.01 for non-direct members). The code
  rounds up to the cent per order, a conservative approximation.
- Observed 2026-10-19: `GET /series/KXFEDDECISION` gives `fee_type = "quadratic_with_maker_fees"`,
  `fee_multiplier = 1`. Code (`venue_arb/fees.py`) reads both per series and raises on any other `fee_type`
  (`flat`, combo) rather than guess.

### Jurisdictions where trading is allowed (2026-10-19)
- Unverified. The fetched docs pages list no country list. Kalshi is a US-regulated exchange; US availability is
  not confirmed from a primary page here. This project does not trade, so it only matters for the demo's framing.

### Public data terms (2026-10-20)
- **Decision: `DASHBOARD_SHOW_BOOKS = False`. Kalshi's Data Terms of Use prohibit public display without written
  authorization, and the Developer Agreement (which governs the API) could not be read. Sam to review.**
- `capacity_contracts` (contracts fillable at ≥ 1¢ net across both books after fees) is published as a derived
  quantity, not a quote (controller Ruling 10; Sam to confirm with the terms).
- The Developer Agreement <https://kalshi.com/developer-agreement> returned HTTP 429 (Vercel checkpoint) to curl and
  WebFetch today; in a browser the page loaded but its body was empty. Not read. Not bypassed.
- The same page's footer links "Data Terms of Service"
  (<https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf>, read 2026-10-20). It covers
  `kalshi.com` website content including "bid-ask prices", not the API explicitly. It says: "You may access content
  only for your personal use for non-commercial purposes." and prohibits, absent "prior written authorization",
  "publicly displaying, publishing" and "systematic retrieval to create collections, compilations, databases".
  Whether it reaches API data is the Developer Agreement's question; on its face it forbids a public quote page.
- Attribution text required: none found (neither document was read in full for the API terms).
- To flip to `True`: obtain Kalshi's written permission, or read the Developer Agreement and find an explicit
  display allowance; then re-run `python -m venue_arb.publish history --hours 168` and regenerate `dashboard/sample`.

### Developer Agreement (2026-10-27)
- Read in full. The page <https://kalshi.com/developer-agreement> embeds the PDF
  <https://assets.kalshi.com/Kalshi-Developer-Agreement.pdf> (v1.1, 6 pages, Kalshi Ex LLC; "Accessing or using
  Kalshi's API shall be considered a binding acceptance"). Opened in the browser pane; curl and WebFetch still get
  HTTP 429.
- Clause 3: "Use of Kalshi APIs is expressly limited to facilitating a member's own trading on the Exchange; all
  other usages are disallowed and may result in account suspension."
- Clause 3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API except for
  purposes of facilitating your own trading on Kalshi. You may not share such data or content with third parties in
  any manner without prior written authorization from Kalshi."
- Clause 3.5 prohibits API use "for purposes of monitoring the availability, performance, or functionality of any of
  Kalshi's services or for any other benchmarking or competitive purposes." Clause 4.1.1: API users "must be a
  Member of Kalshi's Exchange and comply with Kalshi's Rulebook." Clause 6.4: prior written approval for public
  statements "relating to Kalshi, the Kalshi Marks, Kalshi Services, Kalshi API, or this Agreement."
- No display allowance anywhere, so the Data Terms question above is answered: the restriction reaches API data.
  On a plain reading the 60 s collection, the stored history and the page's Kalshi-derived figures (mids, edges,
  capacity, research block) all fall inside clauses 3 and 3.1. `DASHBOARD_SHOW_BOOKS` stays `False`.
- **Decision (Sam, 2026-10-27): recorded, public page kept as is.** Non-commercial personal research; the same
  posture as for Binance (see the research note's terms table). Revisit if Kalshi objects or before any commercial
  use; written authorization is the way to flip `DASHBOARD_SHOW_BOOKS`.

### Kalshi fee schedule source (2026-10-20, Task 1)
- Still unverified. <https://kalshi.com/fee-schedule> and the PDF both return HTTP 429; the S3 guess
  `kalshi-public-docs.s3.amazonaws.com/kalshi-fee-schedule.pdf` is 404. `docs.kalshi.com/llms.txt` lists no fee
  schedule page (only fee rounding, series/event fee-change endpoints, margin fee tiers). Coefficient 0.07 stays
  secondhand; Task 8 shows the caveat line.

### Market page URL (2026-10-20)
- Verified in the browser pane (curl and WebFetch get HTTP 429). `kalshi.com/markets/kxbtcd` redirected to
  `/markets/kxbtcd/bitcoin-price-abovebelow/kxbtcd-26oct2018`: series ticker, a slug, event ticker, all lower case.
  The slug is not a key: `/markets/kxbtcd/x/kxbtcd-26oct2117` opened that event and canonicalized the slug, and
  `/markets/kxfeddecision/market/kxfeddecision-26oct` opened "Fed decision in October?". Pages are per event, not
  per market (strike).
- Code (`bundle.market_url`): `https://kalshi.com/markets/{series_ticker}/market/{event_ticker}`, lower case, from
  `listings.raw["event"]`.

## Polymarket International

### Read endpoints and rate limits (2026-10-19)
- Public read services: Gamma `https://gamma-api.polymarket.com` (markets/events), CLOB
  `https://clob.polymarket.com` (books/prices), Data `https://data-api.polymarket.com`. Third-party guides say the
  read endpoints need no auth; the market-data overview page did not state it, so treat auth-free access as
  unverified for each endpoint until the first call succeeds.
- Limits are IP-based and enforced by Cloudflare; going over delays requests rather than rejecting them.
  Gamma general 4,000 req/10s, `/events` 500/10s, `/markets` 300/10s. CLOB `/book` 1,500/10s, `/books` (batch)
  500/10s, `/price` 1,500/10s, `/prices-history` 1,000/10s. Data API general 1,000/10s.
  Source: <https://docs.polymarket.com/api-reference/rate-limits>

### Taker/maker fee formula (2026-10-19)
- Quote: "fee = C x feeRate x p x (1 - p)". Only takers pay; makers are never charged. Taker rates by category:
  Crypto 0.07, Sports 0.05, Finance 0.04, Politics 0.04, Economics 0.05, Culture 0.05, Weather 0.05,
  Other/General 0.05, Mentions 0.04, Tech 0.04, Geopolitics 0. Maker rebates of 15-25% of fees (Crypto 20%,
  Sports 15%, others 25%) are paid daily. Rounding rule: unverified.
  Source: <https://docs.polymarket.com/trading/fees>

### Market fee schedule field (2026-10-19, observed)
- Fed markets on Gamma carry `feesEnabled: true`, `feeType: "economics_fees"`, `feeSchedule: {"exponent": 1,
  "rate": 0.05, "takerOnly": true, "rebateRate": 0.25}`. This matches the Economics 0.05 rate and 25% maker rebate
  above. Code reads `rate` and `exponent` from the market's own `feeSchedule` (`listings.raw["market"]`) and charges
  `C x rate x (p(1-p))^exponent` with no rounding (rule unverified).

### Capital transfer assumption (2026-10-19)
- Not a venue number. `fees.TRANSFER` assumes $10 fixed plus 10 bps per round trip (deposit/withdraw plus
  USD to USDC), deliberately conservative, reported as a sensitivity column rather than baked into headline edge.

### Jurisdictions where trading is allowed (2026-10-19)
- The geoblock page lists fully blocked regions (Iran, Syria, Cuba, North Korea, Crimea, Donetsk, Luhansk) and
  "close-only" regions on both frontend and API, which include the United States, United Kingdom, France, Germany,
  Italy, Belgium, Poland, Russia, Singapore, Taiwan, Thailand, Australia, Canada (BC, ON, AB, QC), Brazil and
  others; Ireland, Japan, Netherlands, South Korea (and Malta, sports only) are close-only on the frontend only.
  Orders from these regions are rejected. The homepage banner confirms "Trading is blocked in the United States on
  polymarket.com." Reading public data is not a trade, and this project places no orders and uses no VPN.
  Source: <https://docs.polymarket.com/api-reference/geoblock>
- Allowed countries are everything not listed above; the page does not enumerate them.

### Public data terms (2026-10-20)
- **Decision: `DASHBOARD_SHOW_BOOKS = False`.** On 2026-10-20 the Terms of Use could not be fetched. Sam read them on
  2026-10-23: they restrict use and redistribution of the data; see "Terms record (2026-10-23)" below.
- **Decision (Sam, 2026-10-28): recorded, public page kept as is.** The Terms of Use (effective 2026-08-11, read
  2026-10-23) limit US persons to the Content Features, bar robots and scrapers from removing data from the Site and
  Interfaces without written consent, and bar market-data distributors and Capital Market Clients from using the Data
  (raw, derived or aggregated) or receiving it. The project is a non-commercial personal research page that reads the
  public CLOB API, so Sam takes the same posture as for Kalshi (see "Developer Agreement (2026-10-27)" above): derived
  figures stay on the page, raw quotes stay withheld (`DASHBOARD_SHOW_BOOKS = False`), nothing is redistributed to a
  distributor, and written consent is the route to anything more. Revisit if Polymarket objects or before commercial use.
- 2026-10-20 fetch attempt: <https://polymarket.com/tos> fetched 2026-10-20 by curl (HTTP 200, 343 KB) and in the browser pane: the document
  body does not render, only the site shell (and no clause text is in the raw HTML either). The only text is the meta
  description, "By using Polymarket you agree to our Terms of Use."
- Alternatives tried: `docs.polymarket.com` home and `llms.txt` (no terms link or data-use clause), GitHub
  `Polymarket/clob-client` README (no terms; lists an MIT license for the code only). No clause on displaying or
  redistributing market data was found, so none is recorded.
- Attribution text required: none found.

### Market page URL (2026-10-20)
- Verified in the browser pane: `polymarket.com/event/how-many-fed-rate-cuts-in-2026` (the Gamma event slug from
  `tests/fixtures/pm_events_p1.json`) opened "How many Fed rate cuts in 2026?" with the address unchanged.
- Code (`bundle.market_url`): `https://polymarket.com/event/{slug}` from `listings.raw["event"]["slug"]`.

## Hyperliquid (trade.xyz stock perps)

Stock perpetuals on Hyperliquid's `xyz` builder dex (the trade.xyz listings), measured against the stocks they track.
Read-only: the collector calls the public info endpoint and no order, account or position endpoint. Code:
`venue_arb/sources/hyperliquid.py`, `venue_arb/stock_perps.py`, `venue_arb/collect_perps.py`.

### Info endpoint and rate budget (2026-10-22)
- `POST https://api.hyperliquid.xyz/info` with a JSON body whose `type` names the request. Public, no key.
  `post_json` waits `delay` before a call (0.1 s by default; the collector passes 0 and bounds the rate with its
  worker count), retries HTTP 429 and 5xx with backoff, and names only the request type in its errors.
- Requests the code makes: `metaAndAssetCtxs` with `dex = "xyz"` (the universe and each perp's context in one call:
  mark, oracle, mid, funding, premium, open interest, volume, growth mode, leverage, delisted flag); `l2Book` per perp
  (the top `BOOK_DEPTH` = 10 levels a side); `candleSnapshot` at `1h` and `fundingHistory`, both for the backfill.
  The code does not call `perpDexs` or `meta`: the universe comes back inside `metaAndAssetCtxs`.
- Rate budget: 1,200 weight per minute per IP; `l2Book` costs weight 2 (official docs, observed 2026-10-22). A 60 s sweep of 105 books is about 210 weight, plus the context call.
- Counts on 2026-10-22: 138 perps on the `xyz` dex; 105 equities or equity ETFs in scope, the rows of
  `labels/stock_perps.csv` (86 with a Schwab reference, 19 oracle-only). `catalog` drops delisted perps
  (`isDelisted`). `labels/stock_perps_excluded.txt`
  lists 32 pre-IPO, index, FX, commodity and spot-index perps, plus any name hidden by hand from the public page (a
  labelled name may also be excluded). A perp in neither file is named by the collector's
  `unlabelled_perps` warn.

### Fees (2026-10-22)
- Taker fee 0.09% (9 bp) for standard names, 0.009% (0.9 bp) for names in growth mode. The rates are constants in
  `venue_arb/fees.py` (`HYPERLIQUID_TAKER`); the code does not read them from the venue. Growth mode is read per perp
  from the `metaAndAssetCtxs` universe (`growthMode == "enabled"`).
- Every edge row is stamped with `FEE_VERSION = "2026-10-22"`, the date of these entries. A schedule change needs a new
  entry here, new constants and a new `FEE_VERSION`.
- The cost rule charges two taker fees per round trip: 18 bp on a standard name, under 2 bp in growth mode, before
  spreads, funding and borrow (`costs_bps` in `venue_arb/stock_perps.py`).

### Oracle and sessions (2026-10-22)
Observed on 2026-10-22:
- For US names the oracle is derived externally from Sunday 8:05 PM to Friday 8:00 PM ET: pre-market, market,
  post-market, and overnight through Blue Ocean ATS. For Asia-listed names it is derived externally during their home
  sessions.
- NSE (Jio Platforms, Reliance Industries): the trade.xyz index read on 2026-10-22 gives no NSE window. The code
  assumes NSE's regular session, Monday to Friday 09:15-15:30 Asia/Kolkata (`venue_arb/sessions.py`); this is an
  assumption, not an observation. Before this, NSE names read internal at all times.
- Outside those windows the external price freezes, and the oracle advances as an exponentially weighted average of
  the perp's own impact price with a 30-minute time constant.
- Funding settles hourly against the oracle.

What this means in the code: on weekends, and for Asia-listed names outside their home hours, the oracle is not an
outside price. `reference()` uses the Schwab quote only while it is two-sided, uncrossed and at most 120 s old
(`REF_MAX_AGE_S`); otherwise the row's reference is the oracle, labelled "oracle only", and it carries no executable
verdict (spec §5).

### Access (2026-10-22)
- Hyperliquid blocks US traders. Read access is public. The dashboard's US cut states the perp leg as zero (spec §4).

### Terms (read 2026-10-23)
- Read on 2026-10-23; see "Terms record (2026-10-23)" below. The earlier fetch problems (`app.hyperliquid.xyz/terms` is
  a JavaScript shell, `hyperliquid.xyz` answers HTTP 403) no longer block anything.
- The two derived-value points raised in the 2026-10-22 code reviews (`basis_bps` plus the public mark recovering the
  Schwab mid; `fees_cents` embedding the Schwab spread) described Schwab-referenced public fields. Those fields no
  longer exist publicly: the public view uses the venue oracle only (spec 2026-10-23 §2).

## Schwab Market Data

### Schwab Market Data (2026-10-22)
- Stock reference for the stock-perp tier. The Trader API app has the **Market Data product only**; no account or order
  endpoint is called. Code: `venue_arb/sources/schwab.py`.
- Endpoints (`https://api.schwabapi.com`): `GET /marketdata/v1/quotes?symbols=A,B&fields=quote` (batched 100 symbols per
  call; invalid symbols come back under an `errors` key without failing the call, and are skipped: unverified, as
  recalled) and
  `GET /marketdata/v1/pricehistory?symbol=&periodType=month&frequencyType=daily&frequency=1&startDate=ms&endDate=ms`
  (unverified, as recalled and not checked against Schwab's docs or a live call: daily candles are stamped at local midnight, so the UTC date of `datetime` is the session). Quote fields used:
  `bidPrice`, `askPrice`, `bidSize`, `askSize`, `lastPrice`, `quoteTime` (ms since epoch, UTC).
- Rate limit: 120 calls per minute (`CALLS_PER_MIN`; unverified, as recalled); one batched quote per snapshot is far
  under it.
- Unverified (as recalled, not observed): after the 8 PM ET close the API keeps returning the last quote with an old `quoteTime`. Consumers must check the quote's age regardless.
- **Bid/ask size unit is uncertain.** Schwab reports `bidSize`/`askSize` as shares for most symbols but may report round
  lots (x100) for some. The raw value is recorded; the executable tier treats it as shares, which can only understate
  capacity if the unit is lots.
- Token lifetimes: access token 30 minutes (`expires_in` 1800, the client refreshes 60 s early); refresh token 7 days
  (`REFRESH_TTL`). After expiry (unverified, as recalled) the data endpoints answer HTTP 401 and the token endpoint
  most likely HTTP 400 `refresh_token_authentication_error` (per schwab-py); the client raises `SchwabAuthError` on a
  401 from any endpoint and on any 4xx from the token endpoint, naming the path only. The token endpoint takes HTTP
  Basic auth `client_id:secret` and a form body (unverified, as recalled).
- Secrets live in Bitwarden: `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, `SCHWAB_REFRESH_TOKEN`, and
  `SCHWAB_REFRESH_ISSUED_AT` (ISO 8601 UTC; the expiry is that plus 7 days). Never a token file in the repo.
- **Weekly login runbook** (every 7 days, or on `SchwabAuthError`):
  1. In `~/platform/projects/venue-arb` on the OptiPlex, put a write-capable Bitwarden access token in `BWS_WRITE_TOKEN`
     (`read -rs BWS_WRITE_TOKEN && export BWS_WRITE_TOKEN`; the `secrets` wrapper's own token is read-only and it
     strips `BWS_ACCESS_TOKEN`), then
     `UV_PROJECT_ENVIRONMENT=~/platform/.venvs/venue-arb secrets uv run --locked --package venue-arb python -m venue_arb.sources.schwab login`.
     Without the token it stops before the browser step; `read -rs` shows nothing while you paste, so check it took
     with `[ -n "$BWS_WRITE_TOKEN" ] && echo set`.
  2. Open the printed authorize URL, sign in, approve. The browser lands on `https://127.0.0.1/?code=...&session=...`
     and shows a connection error; that is expected. Paste the full URL back at the prompt (the code is URL-encoded in
     the URL and the CLI decodes it before the exchange).
  3. The CLI writes `SCHWAB_REFRESH_TOKEN` and `SCHWAB_REFRESH_ISSUED_AT` with `bws secret create|edit` and restarts
     `venue-arb-perps` (`--no-restart` skips that). It prints secret names and the restart result, never values.
- Personal-use terms: read on 2026-10-23, see "Terms record (2026-10-23)". Schwab data is licensed for the developer's
  own use, so quotes and anything derived from them stay on the OptiPlex.

### Display terms for stock-perp figures (2026-10-22)
- The developer app is for personal use (spec §6). Schwab is approved for Market Data (Production, "Ready For Use",
  2026-10-20, per the spec).
- The terms were read on 2026-10-23 ("Terms record (2026-10-23)"): third-party use and derivative works are barred, so
  no Schwab-derived figure is displayed. The old `basis_bps` / `fees_cents` disclosure points no longer apply, since
  those Schwab-referenced fields are not public.
- When the refresh token lapses, the collector's stock leg stops (the first failed call is logged; `schwab_auth` is
  recorded at the next QA flush, every five ticks) and rows fall back to the oracle. `schwab_token_expires` fails at error severity 48 h before expiry, which the platform alerter pushes to Telegram (and `schwab_auth` likewise when the token stops working).

## Pyth Network

### Hermes access (2026-10-22)
- Pyth's Hermes price endpoint (`v2/updates/price/latest`) has required a paid API key since the Pyth Core upgrade of
  2026-08-26. Only a free trial is available, and the call answers HTTP 401 without a key (spec §3 amendment).
- No Pyth client is built. The Asia-listed names are oracle-only, and the US names have no second opinion. The
  spec leaves room for a client behind `PYTH_API_KEY` later; none exists in the repo. Paid data needs Sam's approval
  (spec §7), so nothing here calls Pyth.
- JIO and RELIANCE have no Pyth equity feed in any case (spec §3).
- Pyth's terms were read on 2026-10-23 ("Terms record (2026-10-23)"): no reuse right on the website, equities with display rights need Pro.

## Terms record (2026-10-23)

Read by Sam on 2026-10-23 (spec `2026-10-23-venue-arb-stock-perp-public-view-design.md`
§7). This is a reading, not legal advice. It supersedes the "unread" notes above (Hyperliquid Terms, Schwab
personal-use terms, Pyth display terms).

### Schwab Trader API Developer Program Agreement, May 2023 (as read on 2026-10-23)
- Texas law. The app is "Trader API - Individual", Market Data Production only.
- §2 and §6.2: an app whose output reaches third parties is a "Commercial Application", which needs a company profile
  and review, with limits on passing market data to users who are not entitled to it.
- §9.4: data is for use through your own application only, with no third-party use. §9.4.2: exchange data is licensed
  to you personally, to be neither shared nor re-used.
- §5(n) and §8.1: no derivative works of Schwab property, which includes its data.
- §5(v): automated collection only through your own app.
- §8.3: Schwab names are not to be used without written permission.
- §10: keep records for 18 months; Schwab has audit rights.
- Consequence (spec §2): nothing derived from Schwab data leaves the OptiPlex. The public stock-perp view is built on
  Hyperliquid data only; Schwab feeds the private two-leg tier and the private oracle check.

### Hyperliquid Terms of Use, updated 2026-06-15 (as read on 2026-10-23)
- They govern the `app.hyperliquid.xyz` interface only. US persons are barred from that interface. There is no
  data-licence or scraping clause; automated use beyond reasonable usage is prohibited.
- The collector reads the public API (`api.hyperliquid.xyz/info`), not the interface, at a rate set in "Info endpoint
  and rate budget" above.

### trade.xyz Terms of Use, updated 2026-09-06 (as read on 2026-10-23)
- They govern `app.trade.xyz`. US persons are barred. Scraping that interface without permission is not allowed.
- We read the Hyperliquid API, not their interface.

### Pyth (as read on 2026-10-23)
- The website terms grant no right to reuse price data and limit use to private, non-commercial purposes.
- Plans: Free (view-only, no API, no redistribution); Starter $500 a month (crypto only); Pro from $2,500 a month
  (equities, display rights, limited redistribution). Not used: paid data is out of scope.

### Polymarket Terms of Use, effective 2026-08-11 (as read on 2026-10-23)
- US persons may use only the news and information features. Robots and scrapers are barred.
- Capital Market Clients and market-data distributors may not use the Data (raw, derived or aggregated), and it may not
  be redistributed to them. This bears on the live pair tier, which publishes Polymarket-derived figures. Open
  decision for Sam: `docs/going-public.md`.

### Conventions that follow from the record
- Oracle-mode episodes in `stock_perp_window` carry `pm_id = 'oracle'` (`ORACLE_PM_ID`) and `source =
  'stock_perp_oracle'`; the private two-leg tier keeps `source = 'stock_perp'`.
- Exclusion is manual only: a name leaves the public page when someone adds it to `labels/stock_perps_excluded.txt`
  by hand with a neutral comment that does not cite Schwab. Nothing hides a name automatically. The file is for
  indices, commodities, FX and delisted names; pre-IPO names are labels of kind `pre_ipo` in `labels/stock_perps.csv`
  (`xyz:ANT`, `xyz:YMTC`, `xyz:JIO`), collected but never scored. SPCX, QNT and CBRS stay listed here until their
  listing-day edit (README, Stock perps). The file then means
  "hidden from every public path": the publisher re-reads it and filters the name out of the live rows, sparklines,
  history Parquet, research block and episode counts (history hours already pushed keep the name until rewritten: with `DASHBOARD_SHOW_STOCK_PERPS` on, run `secrets uv run python -m venue_arb.publish history --hours 240` (the server keeps about 10 days of hour files; the run rewrites and pushes them); with the flag off the hour files hold no stock rows, so there is nothing to rewrite.). A name may be in both the labels CSV and the excluded file; its
  rows stay in the private tables. The collector (`collect_perps.run_forever`) skips excluded names, so an excluded
  name's private rows stop growing from the next label reload; what was collected before stays.
- Asian exchange holidays are not modelled in the session calendar (`venue_arb/sessions.py`); on such a day a name
  can read as external against a stale oracle.
