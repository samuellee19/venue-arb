# Venue notes

Research notes for the two venues in scope. Each entry is dated and links the venue's own docs. Anything the docs
did not settle when fetched is marked "unverified". Re-check before relying on a number: fees and geoblocks change.

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

### Public data terms (2026-10-19)
- Unverified. The docs say that by using the API "you are agreeing to be bound to our Developer Agreement"
  (<https://docs.kalshi.com/welcome>) but that agreement was not fetched, so whether prices may be shown on a
  public page is not settled. Treat the public demo as blocked on this until the Developer Agreement is read.

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

### Public data terms (2026-10-19)
- Unverified. The Terms of Use page body could not be read (only a banner came back) and no clause on data
  display or redistribution was found. Treat the public demo as blocked on this until the terms are read.
  Source to check: <https://polymarket.com/tos>
