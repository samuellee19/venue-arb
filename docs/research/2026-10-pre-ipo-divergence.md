# Pre-IPO perps: do two markets price the same unlisted company, and can the gap be called a spread?

Research note, 2026-10-27. Read-only; no product code. Every live number below was read on 2026-10-27 and is
dated; everything else cites the document it came from. This is a reading of venue documents, not legal advice.

**Short answer.** Anthropic is the only unlisted company priced on five markets today (three Hyperliquid
deployers, Binance, Coinbase); OpenAI is on three (Entropy, Binance, Coinbase). The markets quote in two
different units: *one share* (trade.xyz `xyz:ANT`, Paragon `para:ANTH`) and *billions of dollars of market
cap* (Entropy `io:ANTH`, Binance `ANTHROPICUSDT`, Coinbase `ANTHROPIC-PERP`). Within each unit the markets
agree to well under 1%. Across units there is no published conversion: Anthropic's S-1 leaves the share count
blank, trade.xyz and Paragon publish none, and Binance's "1 billion shares" is a placeholder it says it will
rescale. The cross-unit gap moves ±5% for every plausible share count, which is larger than any observed
same-unit divergence. So the figures normalise, but the result is a bet on the share count, not a spread.

## 1. Terms (read first, as Sam asked)

Every venue and deployer whose data this note touches, what its terms say about the four things that matter to
the project, with the URL and the date I read it. This is a reading, not legal advice.

| source | document, URL, date read | governs | read-only API collection | display of derived figures (basis, spread, funding) | redistribution | US persons |
|---|---|---|---|---|---|---|
| Hyperliquid info API | Hyperliquid Terms of Use, updated 2026-06-15, read 2026-10-23 (docs/venues.md "Terms record"). Re-check 2026-10-27: https://hyperliquid.xyz/terms answers S3 AccessDenied; the app host is not opened under project rules, so the 2026-10-23 reading stands. API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals (read 2026-10-27) | the `app.hyperliquid.xyz` interface | not restricted by the terms; the API docs set rate limits only (see docs/venues.md "Info endpoint") | not restricted | nothing in the terms | barred from the interface, not from reading the API |
| trade.xyz (`xyz`) | Terms of Use, last updated 2026-09-06, https://trade.xyz/terms, read 2026-10-27: unchanged since the 2026-10-23 reading | "the website-hosted user interface currently available at app.trade.xyz" and Events; Documentation incorporated (1.5) | we read the Hyperliquid API, not the Interface; the scraping clause (4.1.5) covers "content or information from the Interface" only | not restricted for API-sourced figures | nothing on API data | Restricted Persons include US, Canada, UK (2.2); VPN barred (2.3); interface only |
| Paragon (`para`) | Terms of Use, last updated 2026-01-29, https://docs.paragon.trade/legal/terms_of_use.md, read 2026-10-27; Pre-Listing Risk Disclosure https://docs.paragon.trade/legal/pre-listing-risk-disclosure.md | "the Site" (paragon.trade), "the Platform", the Protocol, "Services" (defined to include data) | Hyperliquid API reads are not addressed; automated extraction "from the Site" is barred | Company Content is licensed for "personal, non-commercial use"; a figure computed from Hyperliquid API ticks is not Company Content | copying or republishing Company Content needs written consent; no market-data clause | Services not offered to US persons; VPN barred |
| Entropy (`io`) | Legal & Disclaimers https://docs.entropy.io/legal-and-disclaimers/legal-and-disclaimers.md and Restricted Jurisdictions https://docs.entropy.io/legal-and-disclaimers/restricted-jurisdictions.md, read 2026-10-27; no Terms of Service page in the docs index (https://docs.entropy.io/llms.txt) | the exchange interface | no data-use clause | no clause | no clause | United States and territories restricted, plus Canada, UK, most of the EU/EEA, Australia, Singapore |
| Binance | Terms of Use, effective 2026-10-27, read 2026-10-27 by the parent session from Sam's own browser (https://www.binance.com/en/terms is blank from a US address; it renders a 74-page PDF at https://bin.bnbstatic.com/static/cms/cg08ou2ak0tn7mcplvfg/file/f7c3c8a8fbda48eaa272ff503b8c2e54c6c98ad4f0f2a1e5da3667af5962c7de.pdf; counterparties are the ADGM Binance Entities, Abu Dhabi). Contract notice https://www.binance.com/en/support/announcement/detail/2b9ff1e1acf8433793bc40c59a0cb852 (2026-06-01, read 2026-10-27). API terms: a separate document (clause 2), not read | the Binance Services and Binance IP; API access "subject to separate API terms and our approval" (clause 2), so the public market-data endpoints fall under the unread API terms | clause 27 licenses Binance IP "solely as necessary to allow you to receive the Binance Services for non-commercial personal or internal business use" (Sam's view: satisfied by a hobby dashboard); no market-data licence in these Terms; no scraping or automated-access clause at all (conduct list, clause 31.1, is Prohibited Use Policy, manipulation, breach, IP infringement, prohibited-country access). **The blocker is connectivity, not the terms**: on 2026-10-27 `fapi.binance.com/fapi/v1/ping` and `api.binance.com/api/v3/ping` both answer HTTP 451 "Service unavailable from a restricted location according to 'b. Eligibility'" from the OptiPlex, and `premiumIndex` the same from the Mac; `api.binance.us/api/v3/ping` answers 200 but Binance.US is spot only, no perps | nothing in the Terms grants a right to republish prices; "Third Party Data" is "for information purposes" with no accuracy warranty; the API terms (unread) govern market data; Sam's view is that the redistribution rule is not enforced in practice against hobby dashboards, and the project's standing rule never to republish Binance series stays | **status: terms OK for non-commercial use, API terms unread, excluded from the collector because the project does not route through a VPN (Sam's standing rule)**; no Binance leg in the divergence view unless that rule changes | the Terms do not name the US; they point to https://www.binance.com/en/legal/list-of-prohibited-countries (not fetched); in practice binance.com is unavailable from the US and the terms page itself is blank from a US address |
| Coinbase International Exchange | Market Data Terms of Use, last updated 2026-08-07, https://www.coinbase.com/legal/market_data, read 2026-10-27; Trading Rules https://www.coinbase.com/international-exchange/legal/trading-rules (rules 3.1, 7, 13, 19.6, 20), read 2026-10-27; blog https://www.coinbase.com/blog/pre-ipos-are-launching-on-coinbase-starting-with-spacex (2026-06-03) | Coinbase Market Data however obtained (rule 3.1: Market Data via "the Web Interface or the Coinbase API") | licence is "exclusively for you or your entity's personal or research purposes" | **no**: may not "redistribute, display, or disseminate the Market Data — or any data, charts, analytics, research, or other works … derived from the Market Data" to a third party; may not create "generic or fair value prices" or "valuations"; may not use it as a benchmark or to train AI models | barred without written consent | perps "not available to US persons" (Coinbase Bermuda Ltd); https://www.coinbase.com/price/anthropic shows no price from a US address |

**What this decides before anything else.** A public row may carry Hyperliquid-sourced figures (xyz, para, io)
under the rules the project already follows. A Coinbase leg cannot appear on the public page: a basis or
spread row is a derived work and a fair-value price, both barred. A Binance leg is excluded on connectivity, not terms: the
API answers HTTP 451 from both machines and the project does not route through a VPN (Sam's standing rule),
so there is no Binance leg unless that rule changes; the no-republish rule for Binance series also stays. So the only pairs the dashboard can show are
Hyperliquid-vs-Hyperliquid, and every later section is written under that constraint.

**Related reading, not a source for this note (recorded only).** Kalshi Developer Agreement v1.1,
https://assets.kalshi.com/Kalshi-Developer-Agreement.pdf (6 pages, embedded on kalshi.com/developer-agreement;
accessing the API is acceptance), read 2026-10-27 by the parent session. Clause 3: "Use of Kalshi APIs is
expressly limited to facilitating a member's own trading on the Exchange; all other usages are disallowed."
Clause 3.1 prohibits "Collecting, caching, aggregating, or storing data or content accessed via the API except
for purposes of facilitating your own trading on Kalshi. You may not share such data or content with third
parties in any manner without prior written authorization from Kalshi." Clause 3.5 bars use "for any other
benchmarking or competitive purposes"; 4.1.1 requires API users to be Exchange Members; 6.4 requires written
approval for public statements about Kalshi or its API. Together with the Data Terms of Use read 2026-10-20
(docs/venues.md), both Kalshi documents are now read; the Developer Agreement confirms the restriction reaches
API data, so the public page's Kalshi-derived figures conflict with it on a plain reading. Decision pending
with Sam; nothing in this note depends on Kalshi data.

**API pacing used for this note.** One `perpDexs` call and one `metaAndAssetCtxs` per dex (ten calls), 0.3 s
apart, from the Mac's address, once, at 2026-10-27T20:31:54Z. Nothing was run on the OptiPlex and no tick
history was read.

## 2. Which perp markets price the same unlisted companies right now

### 2a. Hyperliquid HIP-3 deployers (info API, `perpDexs` then `metaAndAssetCtxs` with `dex`, read 2026-10-27T20:31:54Z)

`perpDexs` returns ten deployers. Six have every market flagged `isDelisted` (flx, vntl, hyna, km, abcd,
cash); one lists only indices (mkts). The three live deployers that carry a pre-IPO name:

| dex | fullName (API) | deployer | live markets | pre-IPO names |
|---|---|---|---|---|
| `xyz` | XYZ (trade.xyz) | 0x8880…0888 | 119 | ANT, JIO, OURA |
| `para` | Paragon | 0x8888…6ed3 | 33 | ANTH |
| `io` | EntropyIO | 0x320c…cac6 | 12 | ANTH, OAI |

"Pre-IPO" is decided by each deployer's own specification index, not by me:
- trade.xyz's Pre-IPO Specification Index lists ANT, JIO, OURA as **Live** and SHEIN, UNITREE, CXMT, SKHY,
  SPCX, QNT, CBRS as **Converted** (now ordinary stock perps). `xyz:YMTC` is a dead contract: `isDelisted` true, zero volume and open interest, never produced a tick, and is being moved to the exclusion file; Yangtze Memory itself is still unlisted (parent CCSH has filed for a STAR Market IPO), so it is not counted as a live pre-IPO market here.
  Other recent xyz listings that look private (MINIMAX, ZHIPU, GIGADEV, KIOXIA, SNXX, …) are absent from
  the pre-IPO index, so XYZ treats them as listed equities; I did not check each one.
- Paragon's Contract Specifications show one **Pre-Listing** market, written "ANT" in the docs but served as
  `para:ANTH` by the API; UNITREE is **Listed** (STAR 688836).
- Entropy's Pre-IPO Assets page lists ANTH and OAI. Its Pre-Listing page lists PRL (Pearl, a token, not a
  company). TCNT and XIAOMI are HK equities; SNDK, NBIS, GPRO, IONQ are US equities.
- Ventuals (`vntl`) once ran SPACEX, OPENAI and ANTHROPIC markets; all are delisted (funding multiplier 0,
  OI cap 10M still visible in `perpDexs`). Historical only.

Live pre-IPO rows (USD; OI in contracts, as the API reports it; day volume in USD notional):

| market | mark | oracle | open interest | day volume | hourly funding | max lev | margin |
|---|---|---|---|---|---|---|---|
| `xyz:ANT` | 115.32 | 115.46 | 77,695 | 6,614,336 | −0.0000003 | 10 | isolated; growth mode |
| `xyz:JIO` | 13.036 | 13.095 | 226,387 | 449,725 | +0.00000002 | 5 | isolated; growth mode |
| `xyz:OURA` | 48.693 | 48.693 | 16,275 | 5,523 | −0.0000008 | 5 | isolated; growth mode |
| `para:ANTH` | 115.05 | 114.72 | 20.0 | 2,792 | +0.0000063 | 10 | strict isolated; growth mode |
| `io:ANTH` | 1,880.4 | 1,880.2 | 29,275 | 11,662,980 | +0.0000006 | 15 | isolated; growth mode |
| `io:OAI` | 1,487.5 | 1,486.7 | 4,825 | 1,271,938 | +0.0000222 | 6 | isolated; growth mode |

`perpDexs` also reports per-asset funding multipliers and OI caps: `xyz:ANT` 0.005 / $100M, `xyz:JIO` 0.005
/ $25M, `xyz:OURA` 0.005 / $25M, `para:ANTH` 0.006 / $25M, `io:ANTH` 0.125 / $65M, `io:OAI` 0.125 / $12M.
These match the deployers' docs (section 2).

### 2b. Binance and Coinbase (public documentation only)

| venue | symbol | listed | source |
|---|---|---|---|
| Binance Futures (USDⓈ-M) | `SPCXUSDT` | 2026-05-21; SpaceX listed 2026-06-12, so converted | Binance press release via Benzinga/Barchart, 2026-05 |
| Binance Futures | `OPENAIUSDT` | 2026-05-26 | PR Newswire release, 2026-05-27 |
| Binance Futures | `ANTHROPICUSDT` | 2026-06-02 04:30 UTC | Binance exchange notice 2b9ff1e1…, published 2026-06-01 |
| Coinbase International Exchange | `SPCX-PERP` | 2026-06-04; converted to a stock perp 2026-06-12 | Coinbase blog 2026-06-03; help centre as quoted in search |
| Coinbase International Exchange | `ANTHROPIC-PERP`, `OPENAI-PERP` | on/after 2026-06-22 11:00 UTC | Coinbase Markets on X, 2026-06 |

I found no other pre-IPO names on either venue. A third-party tracker counted two pre-IPO contracts on Coinbase
International as of 2026-09-01 (consistent with Anthropic and OpenAI). Binance's `GET /fapi/v1/exchangeInfo`
would be the authoritative list, but it answers HTTP 451 from a US address (section 1), so I did not read it.

### 2c. Names on two or more markets

| company | per-share markets | market-cap markets |
|---|---|---|
| Anthropic | `xyz:ANT`, `para:ANTH` | `io:ANTH`, Binance `ANTHROPICUSDT`, Coinbase `ANTHROPIC-PERP` |
| OpenAI | — | `io:OAI`, Binance `OPENAIUSDT`, Coinbase `OPENAI-PERP` |

Jio and Oura trade only on trade.xyz. Aggregators list another ~20 CEX/DEX Anthropic perps (Bitget, KuCoin,
Lighter, Deribit, Kraken PF_ANTHROPICXUSD, …) all at the $1=$1B unit; out of scope for this note but they show
the market-cap unit is the market convention and trade.xyz/Paragon are the exceptions.

## 3. Each contract's unit and settlement rule

| | trade.xyz `xyz:ANT` | Paragon `para:ANTH` | Entropy `io:ANTH` / `io:OAI` | Binance `ANTHROPICUSDT` | Coinbase `ANTHROPIC-PERP` |
|---|---|---|---|---|---|
| **Price means** | 1 Class A share of Anthropic, PBC (Nasdaq: ANT), USD | "expected value of one share of Anthropic, PBC" | $1 = $1B of implied market cap ("1 point = $1 billion") | per-share, on an *estimated* 1 billion shares | equity valuation ÷ 1,000,000,000 |
| **Share count published** | none; "any displayed reference share count or implied market cap is indicative only" | none; "may show an indicative share count and implied market cap … estimates only" | none needed | 1 billion, "for informational purposes only"; "Contract Size will be adjusted accordingly" when the real count is known | none needed; rebase uses "official outstanding share count" at IPO |
| **Initial reference** | $125, discretionary, "not a forecast" | $110, discretionary | n/a (order-book + private marks) | n/a | n/a |
| **Oracle before listing** | internal: 30-min EWMA advanced by impact-price difference; no external price | internal: impact-price EWMA, τ = 30 min, shortened to 3 min in the 24 h before expected first trade; may be manually re-anchored to the offering price | blend: w·I + (1−w)·E, I = 5-min EMA of Entropy mid, E = undisclosed aggregate of private-market sources; w capped at 0.95 so E keeps ≥5%; oracle clipped to [300, 4200] for ANTH, [200, 3000] for OAI | mark = average of last 10 s of Binance trades, ±1%/s cap; no external index | index from (i) internal trading on CIE and (ii) "third-party, publicly observable market prices", weighting at Coinbase's discretion |
| **Discovery bounds** | ±50%, 3 range resets, 75% oracle threshold | ±50%, 5 re-anchors per direction | catastrophic bounds L/U only | ±1%/s mark cap; "stricter maximum price" | n/a in rules |
| **At IPO** | "expected to convert into a normal equity perpetual with external pricing", first regular session after listing; funding multiplier 0.005 → 0.5 | Pre-Listing → Listed after regular trading begins and the feed is reliable; funding multiplier 0.006 → 0.6 | 3 calendar days before listing the oracle becomes a 1-h EMA of the mark; then public-equity oracle; **still quoted as market cap** ("Hyperliquid doesn't yet support scaling by a constant") | "Pre-IPO Transition Notice" on listing day; mark converges to Median(Price1, Price2, Contract); funding cap +0.005% → ±2% | rebase to per-share "on or around the IPO becoming effective" (rule 19.6), or cash-settle and delist; may rebase to per-share *before* IPO "to align … with per-share pricing on other venues" (rule 20) |
| **Anticipated listing** | 2026-11-20, grace to 2027-01-19 | "TBD", 60-day listing buffer | no-IPO resolution 2028-08-18 (ANTH), 2028-09-02 (OAI) | none stated | none stated |
| **If no listing** | TWAP from launch to settlement date, unless a transaction/adverse event sets another price | TWAP launch → determination cutoff; Paragon may use another method | 6-month trailing mark TWAP at scheduled end; 30-day TWAP on early notice; deal price on M&A | "IPO Failure Notice" and Removal; process not specified in the notice | cash-settle under 19.6.6/19.6.7 |
| **Funding** | Hyperliquid formula ×0.5 (XYZ) × 0.005 pre-IPO multiplier; premium samples at 1%; hourly | Hyperliquid formula × 0.006, clamp 0.0003; hourly | Hyperliquid formula × 0.125; hourly; no fee on funding | fixed +0.005% per 8 h during pre-IPO (interest 0.015%/day, no premium index) | standard CIE funding interval; rules do not give a pre-IPO rate |
| **Fees (taker/maker)** | HIP-3 9/3 bps, growth mode ×0.1 → 0.9/0.3 bps (growth mode enabled per API) | "Hyperliquid's standard fee schedule"; API says growth mode enabled → 0.9/0.3 bps | 9/3 bps, growth mode ×0.1 → 0.9/0.3 bps | standard USDⓈ-M schedule (fee page did not render; not verified this session) | rules: "0 to 60 bps"; promotional 0.03% taker / 0% maker on coinbase.com/perpetuals |
| **Max leverage** | 10x, normal isolated | 10x, isolated (API: strict isolated) | 15x / 6x, isolated | 20x | cap set by CIE, "may be materially lower" than equity perps; press says 5x |

Undocumented, stated plainly: the share count behind `xyz:ANT` and `para:ANTH` (neither deployer publishes
one), Coinbase's index weighting and current pre-IPO funding rate, Binance's failed-IPO settlement price, and
Entropy's external source list. Binance's 1 billion is a placeholder by its own wording, not an estimate of
Anthropic's count.

Sources: docs.trade.xyz (Pre-IPO Perpetuals, Alternative Settlement Events, Pre-IPO Specification Index,
Oracle Price, Fees, Funding Rate Formula Updates); docs.paragon.trade (Pre-Listing Pricing, Contract
Specifications, Fees, Pre-Listing Risk Disclosure); docs.entropy.io (Pre-IPO Perpetuals, Oracle Price, Mark
Price, Funding Rate, Fees, No-IPO Resolution, Pre-IPO Assets); Binance exchange notice
`support/announcement/detail/2b9ff1e1acf8433793bc40c59a0cb852` (2026-06-01); Coinbase International Exchange
Trading Rules (pre-IPO section, rules 7, 13, 19.6, 20) and Coinbase blog 2026-06-03; Hyperliquid docs
(Funding, HIP-3, Info endpoint › Perpetuals). All read 2026-10-27.

## 4. Can the figures be normalised to one implied valuation? Anthropic, end to end

Inputs, all 2026-10-27:

| market | price | unit | published conversion | implied valuation |
|---|---|---|---|---|
| `io:ANTH` (API 20:31Z) | 1,880.4 | $B market cap | $1 = $1B (Entropy docs) | **$1,880B** |
| Binance `ANTHROPICUSDT` (aggregators perpequities.com / CoinGecko, 2026-10-27) | 1,875.6–1,879 | per share on 1B est. shares | 1,000,000,000 (Binance notice) | **$1,876–1,879B** |
| Coinbase `ANTHROPIC-PERP` | not observable from a US address today; last sourced $1,941.33 on 2026-09-01 (CoinGecko snapshot) | valuation ÷ 1B (Coinbase rules) | 1,000,000,000 | **$1,941B on 2026-09-01**; no current figure |
| `xyz:ANT` (API 20:31Z) | 115.32 | per Class A share | **none published** | depends on count |
| `para:ANTH` (API 20:31Z) | 115.05 | per share | **none published** | depends on count |

The per-share markets agree with each other to 0.2% (115.32 vs 115.05), and the market-cap markets agree to
0.3% (1,876–1,884 across io, Binance and the other CEX feeds). Normalising across the two units needs a share
count, and nobody who publishes a price publishes one:

- The S-1 (filed 2026-10-26, Nasdaq: ANT) leaves the offered share count and price range blank. Its only
  per-share figure is a footnote implying $71.43 per Class N share as a board valuation at 2026-09-30.
- A third-party reading of the S-1 (artificialdiligence.substack.com) derives a fully diluted count of
  16,936.8M at 2026-06-30 and uses 17.31B for valuation. Press implied-valuation quotes for `xyz:ANT`
  ($1.88T at ~$112, Markets Media quoting Weisenthal; $1.93T at $114.06, KuCoin) back out counts of
  roughly 16.5–17B. OKX's own Anthropic contract assumes 10B shares; Binance's assumes 1B.

What `xyz:ANT` = 115.32 implies under each count, against `io:ANTH` = 1,880:

| assumed count | xyz implied valuation | gap vs io:ANTH |
|---|---|---|
| 16.5B (back-solved from press quotes) | $1,903B | +1.2% |
| 16.94B (S-1-derived fully diluted, 2026-06-30) | $1,953B | +3.9% |
| 17.31B (same source's valuation basis) | $1,996B | +6.2% |
| 1B (Binance's placeholder) | $115B | −94% (unit mismatch, not a price) |

**Which assumptions dominate.** The share count alone moves the "gap" by five percentage points across
counts that are all defensible, which is 20× the divergence between any two markets that share a unit. Second
is unit drift after listing: Entropy says `io:ANTH` stays quoted in market cap after the IPO while trade.xyz,
Paragon, Binance and Coinbase all go per-share, so the pair never converges by construction. Third is oracle
regime: xyz and Paragon oracles are pure order-book EWMAs (no external anchor); Entropy blends an undisclosed
private-market aggregate with at least 5% weight; Binance is pure last-trade; Coinbase is discretionary. Funding
is immaterial today (every leg is within ±0.002%/h) because every venue damps pre-IPO funding by 8–200×.

**Verdict.** The comparison is not sound enough to call a spread. Within a unit (io vs Binance vs Coinbase;
xyz vs Paragon) the divergence is sub-1% and is real but tiny. Across units the number is a share-count
assumption dressed as a price gap. It becomes a spread on the day a count exists: the final 424B4 prospectus,
or a trade.xyz/Paragon announcement of a reference count, or Coinbase's rule-20 rebase to per-share. Until
then the honest dashboard row is "same unit only".

## 5. What a first version could show

**Pairs that qualify today** (same unit, both legs on Hyperliquid, both readable through the collector's
existing client):

| pair | unit | long / short rule | gap at 20:31Z 2026-10-27 |
|---|---|---|---|
| `xyz:ANT` vs `para:ANTH` | per share | long the lower mark, short the higher | para below xyz by 0.23% (115.05 vs 115.32) |
| `io:ANTH` vs (Binance/Coinbase) | $B market cap | — | not showable (section 1); internal only, and Binance geoblocked |
| `io:OAI` vs (Binance/Coinbase) | $B market cap | — | same |

So the public first version is one pair, Anthropic per-share, with a 3-week life (xyz anticipated listing
2026-11-20, Paragon "TBD"). The cross-unit Anthropic view (`xyz:ANT` × count vs `io:ANTH`) can be shown as an
*explorer* chart with the count as a visible slider or three labelled lines (16.5B / 16.94B / 17.31B), never as
a scored row.

**Dashboard row** (one per pair, both legs read from the same `metaAndAssetCtxs` tick, 60 s cadence as today):
long market, short market, mark on each leg, normalised gap in % of the mean mark, each leg's hourly funding
(API `funding`, already a fraction per hour) and its 8-h and annualised equivalents, each leg's taker fee
(0.9 bps growth mode for all three deployers today; read `growthMode` per tick rather than hard-code), OI
and OI cap per leg (`perpDexs` caps: xyz:ANT $100M, para:ANTH $25M), and the two deployers' listing
treatment side by side (xyz: anticipated 2026-11-20, grace to 2027-01-19, multiplier 0.005→0.5; Paragon:
TBD, 60-day buffer, 0.006→0.6). Verdict copy must say the gap is after fees but before the listing-day
regime change, and that `para:ANTH` has $2.3K of open interest, so the row is informational until depth
appears. Label kind stays `pre_ipo`; the row is a new category ("pre-IPO pairs"), not a `stock_perp` edge.

**Biggest risk to the idea.** Not the share count (that is handled by refusing to cross units) but
*convergence failure at listing*. The two legs do not convert on the same clock or to the same thing:
trade.xyz converts "after the underlying asset begins regular trading", Paragon "once the external feed is
reliable", and either may re-anchor the oracle to the offering price by hand in the final 24 h (Paragon says
so explicitly; its risk disclosure warns of immediate liquidations). A 0.2% gap is smaller than the
discretionary re-anchor on either leg, so a position held into listing day is a bet on two operators'
timing, with isolated margin on both sides and a 10x cap. The second risk is liquidity: `para:ANTH` traded
$2.8K in the last day, so the quoted gap is not executable at any size, and a dashboard that prints it as
an edge misleads. The third is regulatory: both legs are on interfaces that bar US persons, and the SEC
proposal from the Hyperliquid Policy Center and trade.xyz (2026-08-18) is a proposal, not a rule, so a
listed-in-the-US pre-IPO perp could appear or the existing ones could be halted on short notice.
