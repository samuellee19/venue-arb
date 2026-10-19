# Do exact Kalshi-Polymarket matches carry arbitrage? Research note v1

Generated 2026-10-19 21:42 UTC by `python -m venue_arb.report.note`. Every number below is computed from the tables named in Method at generation time; the queries are listed at the end. This is v1: the executable tier covers only the snapshots collected so far, and the note is re-run once about four weeks of snapshots exist.

## Question

When Kalshi and Polymarket list the same contract, do their prices leave money on the table after taker fees and order-book depth? Headline perspective: arbitrage that someone, somewhere could execute (both legs tradable by some eligible participant), with a US-only cut alongside.

**Short answer so far** (each figure is restated with its source under Results):

- Executable tier: 0 opportunity episodes in 2.7 h of snapshots (too short to conclude anything); the largest top-of-book edge after fees was 0.0020 per $1 against a 0.01 threshold.
- Screened tier (history, not executable): median hourly net gap -0.032; 3.6% of rows reached 0.01 (a count that is conservative by up to ~$0.01 per contract, see Limits), 30.7% of those in a pair's first 7 days.

## Data and window

- Venues: Kalshi and Polymarket International (public read endpoints only; no orders).
- Pairs: 9 eligible exact pairs at generation time (0 human-reviewed, 9 not), Kalshi series: `KXFEDDECISION`.
- Screened tier: hourly, 2026-06-18 00:00 UTC to 2026-10-19 20:00 UTC; 27,011 rows (13,735 pair-hours, up to two directions each) over 9 pairs.
- Executable tier: order-book snapshots at a 60 s cadence, 2026-10-19 18:33 UTC to 2026-10-19 21:17 UTC (2.7 h); 1,320 aligned pair-snapshots (2,640 rows, one per direction) over 8 pairs. Eligible pairs with no executable rows yet: KXFEDDECISION-27JAN-H26.
- Tier scope: both tiers keep every pair whose latest prompt-v2 adjudication is `exact` and that has no `wrong`/`unsure` review, whether or not it is open now (a closed pair's history is still evidence). Pairs are Kalshi-Polymarket market pairs.
  - Screened: 9 pairs in scope (9 currently eligible, 0 closed, 0 open but not currently eligible); excluded pairs with rows in the table: none.
  - Executable: 8 pairs in scope (8 currently eligible, 0 closed, 0 open but not currently eligible); excluded pairs with rows in the table: none.

| Kalshi ticker | Polymarket market | LLM confidence | Human review | Closes |
|---|---|---|---|---|
| KXFEDDECISION-26DEC-C25 | `0xf17ad24e...` | 0.85 | none | 2026-12-09 23:59 UTC |
| KXFEDDECISION-26DEC-H0 | `0x723822eb...` | 0.88 | none | 2026-12-09 23:59 UTC |
| KXFEDDECISION-26DEC-H25 | `0x8a419661...` | 0.88 | none | 2026-12-09 23:59 UTC |
| KXFEDDECISION-26OCT-C25 | `0x9cf563d7...` | 0.88 | none | 2026-10-29 03:59 UTC |
| KXFEDDECISION-26OCT-H0 | `0xdf9bf27e...` | 0.90 | none | 2026-10-29 03:59 UTC |
| KXFEDDECISION-26OCT-H25 | `0x12aa13b3...` | 0.88 | none | 2026-10-29 03:59 UTC |
| KXFEDDECISION-27JAN-C25 | `0x0bbe79a9...` | 0.90 | none | 2027-01-28 04:59 UTC |
| KXFEDDECISION-27JAN-H0 | `0xf6e7c89e...` | 0.90 | none | 2027-01-28 04:59 UTC |
| KXFEDDECISION-27JAN-H26 | `0x21cd6160...` | 0.85 | none | 2027-01-28 04:59 UTC |

## Method

**What counts as exact.** Same event, same outcome, same decision date and same resolving authority. Trading close times and the wording of the source pages do not count as differences. Rare edge clauses (for example a cancelled meeting, or an odd-size rate move rounded up) keep a pair exact but are recorded as tail risks and listed under Results. Adjudication was by an LLM, claude-haiku-5-5 (prompt v2).

**Eligibility.** The latest adjudication at the listings' current versions says `relation = 'exact'`, and either confidence >= 0.8 with no review, or a human review labeled `correct`; any `wrong` or `unsure` review excludes the pair. Both listings must be open and seen in the last 2 hours.

**Two tiers, never combined.**

- *Screened* (`venue_arb.screened`): per hour present on both venues, Kalshi's hourly candle ask (Yes ask, or 1 - Yes bid for No) against Polymarket's price history for the opposite leg, minus one contract's taker fee on each leg: `net_gap_top = 1 - k_price - pm_price - fees`. Polymarket history is a traded/mid price, not an ask, so this tier says where gaps tended to appear, not that they were executable. The query filters `k_price < 1`: an earlier run wrote empty-book sentinel rows with `k_price = 1`, which are left in place (no deletes) and excluded here.
- *Executable* (`venue_arb.edges`, `venue_arb.opportunities`): our own order-book snapshots. A Kalshi snapshot is aligned with the nearest Polymarket snapshot within 30 s. Both ask ladders are walked together; `best_edge` is the top-of-book edge after fees and `capacity_usd` the dollars spent on both legs while the marginal edge stays >= 0.01.

**Threshold (an episode).** Net edge >= $0.01 per $1 contract, >= $100 executable, in >= 2 consecutive snapshots (a gap over 150 s breaks the run).

**Fees** (entries dated 2026-10-19, sources in `docs/venues.md`). Kalshi taker: `0.07 x C x P x (1-P)` times the series' `fee_multiplier` (read from the API), rounded up to the cent; the 0.07 coefficient is secondhand (see Limits). Polymarket taker: `C x rate x (P(1-P))^exponent` read from each market's own `feeSchedule` (here: `{"exponent": 1, "rate": 0.05, "rebateRate": 0.25, "takerOnly": true}`), no rounding.

**Transfer cost** (an assumption, not a venue number): $10 fixed + 10 bps per round trip of capital between venues. It is reported as a sensitivity count, not subtracted from the headline edge.

## Results

### Screened tier (history; not arbitrage)

- Rows (pair x hour x direction): 27,011.
- `net_gap_top` p50 -0.032, p90 -0.006, max 0.323.
- Rows with `net_gap_top > 0`: 1,685 (6.2%); >= 0.01: 974 (3.6%). Both counts are conservative: the 1-contract Kalshi fee is ceiled to the cent, which can understate a net gap by up to ~$0.01 (see Limits).
- Of the rows >= 0.01, 299 (30.7%) fall in their pair's first 7 days of screened history, which hold 7.1% of all rows.

| Kalshi ticker | First hour | Rows | p50 net | Max net | Rows >= 0.01 |
|---|---|---|---|---|---|
| KXFEDDECISION-26DEC-C25 | 2026-07-30 02:00 UTC | 2,546 | -0.018 | 0.059 | 121 |
| KXFEDDECISION-26DEC-H0 | 2026-07-30 01:00 UTC | 2,538 | -0.036 | 0.053 | 83 |
| KXFEDDECISION-26DEC-H25 | 2026-07-30 02:00 UTC | 3,050 | -0.044 | 0.148 | 56 |
| KXFEDDECISION-26OCT-C25 | 2026-06-18 01:00 UTC | 3,264 | -0.018 | 0.323 | 116 |
| KXFEDDECISION-26OCT-H0 | 2026-06-18 01:00 UTC | 4,704 | -0.037 | 0.258 | 157 |
| KXFEDDECISION-26OCT-H25 | 2026-06-18 00:00 UTC | 4,540 | -0.036 | 0.308 | 179 |
| KXFEDDECISION-27JAN-C25 | 2026-07-30 02:00 UTC | 2,360 | -0.030 | 0.273 | 183 |
| KXFEDDECISION-27JAN-H0 | 2026-07-30 02:00 UTC | 2,468 | -0.047 | 0.108 | 52 |
| KXFEDDECISION-27JAN-H26 | 2026-07-30 02:00 UTC | 1,541 | -0.017 | 0.255 | 27 |

![Screened net gap distribution](screened-net-gap.png)

*Screened tier. Distribution of `net_gap_top` over all 27,011 rows. The x-axis is clipped to the 0.5th-99.5th percentile, rounded out to the 0.005 bin grid (threshold kept in view); rows outside are pooled into the end bins and counted in the chart's footer line. Log y-axis so the thin positive tail is visible.*

### Executable tier (order books)

- Aligned pair-snapshots: 1,320; with capacity > 0 in either direction (marginal edge >= 0.01 at the top): 0 (0.0%).
- `best_edge` (per row, both directions) p50 -0.0157, max 0.0020.
- Opportunity episodes: 0, so there are no durations, capacities or annualized edges to report.
- Episodes still positive after the transfer assumption: 0 of 0 (approximate and optimistic: it credits each episode's maximum edge on all of its median capacity, against `transfer_cost_usd`).

| Kalshi ticker | Snapshots | Max best_edge | Snapshots with capacity > 0 |
|---|---|---|---|
| KXFEDDECISION-26DEC-C25 | 165 | 0.0020 | 0 |
| KXFEDDECISION-26DEC-H0 | 165 | -0.0096 | 0 |
| KXFEDDECISION-26DEC-H25 | 165 | -0.0123 | 0 |
| KXFEDDECISION-26OCT-C25 | 165 | -0.0078 | 0 |
| KXFEDDECISION-26OCT-H0 | 165 | -0.0153 | 0 |
| KXFEDDECISION-26OCT-H25 | 165 | -0.0144 | 0 |
| KXFEDDECISION-27JAN-C25 | 165 | 0.0018 | 0 |
| KXFEDDECISION-27JAN-H0 | 165 | -0.0178 | 0 |

![Executable tier](executable-edge.png)

*Executable tier. There are 0 opportunity episodes, so there are no episode snapshots to plot capacity against. Shown instead: each pair's best edge (better of the two directions, after taker fees, top of book) at every aligned snapshot, against the 0.01 threshold. Panels are Kalshi tickers without the series prefix; pairs with no snapshots have no panel.*

### Jurisdiction cut (US)

- Episodes executable within the US: 0 of 0. Polymarket International is the only Polymarket venue scored, and it blocks US trading (US_EXECUTABLE = {'kalshi': True, 'polymarket': False}, docs/venues.md geoblock entry), so no pair has both legs executable from the US.

### Contract-term differences (tail risk)

Clauses the LLM adjudicator recorded as rare edge cases that keep each eligible pair exact (latest adjudication at current versions). They are LLM-written, quoted verbatim, and not human-reviewed; pairs whose clauses read like real differences or are muddled are flagged for review before v1-final.

- **KXFEDDECISION-26DEC-C25** *(needs human review before v1-final: says Kalshi's rules do not define the rate variable precisely, which reads like a real resolution difference rather than a rare edge case)*
  - If the December 2026 FOMC meeting is cancelled, Kalshi resolves 'Fed maintains rate' to Yes while Polymarket resolves to 'No change', which is the same outcome mapping but depends on bracket wording.
  - A non-25bp move (e.g. 12.5bp) is rounded up to the 25bp bracket on Polymarket, while Kalshi's rules describe bracket resolution only by exact move size.
  - Polymarket measures the change in the upper bound of the target range, while Kalshi's rules do not define the rate variable precisely.
- **KXFEDDECISION-26DEC-H0**
  - If the December FOMC meeting is cancelled, Kalshi resolves Yes while Polymarket's rules say to resolve to the 'No change' bracket, which also pays Yes, so both agree.
  - Non-standard target moves are rounded to the nearest 25 bps bracket on Polymarket, which Kalshi's bracket structure does not explicitly address.
- **KXFEDDECISION-26DEC-H25** *(needs human review before v1-final: the cancelled-meeting clause is muddled about which side resolves how)*
  - If the December FOMC meeting is cancelled, Kalshi resolves Yes only on 'Fed maintains rate' while Polymarket resolves No, since no hike occurs and the 'No change' bracket would apply only if no statement is released by the next meeting end date.
  - A non-standard change (e.g. 12.5bp) is rounded up to 25bp on Polymarket, which would resolve Polymarket Yes where Kalshi's bracket logic may differ.
  - Kalshi's Yes outcome is a 'Hike 25bps' bracket while Polymarket measures the change in the upper bound, so a 50bp hike resolves No on both but the bracket mapping must match.
- **KXFEDDECISION-26OCT-C25**
  - If the October FOMC meeting is cancelled, Kalshi resolves Fed-maintains-rate Yes and so Cut 25bps is No, while Polymarket resolves to No change, which also pays No on a cut.
  - A non-25bp move such as 12.5bps is rounded up to the 25bp bracket on Polymarket, while Kalshi's rules do not state an equivalent rounding clause.
  - Polymarket resolves on the upper bound of the target range, so an unusual range change could be judged differently than Kalshi's wording.
- **KXFEDDECISION-26OCT-H0**
  - If the FOMC meeting is cancelled, Kalshi resolves Yes while Polymarket resolves to the 'No change' bracket via its no-statement fallback, which should also give Yes.
  - A non-bracket rate move (e.g. 12.5 bps) is rounded up to 25 bps on Polymarket, which resolves No on both markets, so outcomes match.
- **KXFEDDECISION-26OCT-H25**
  - If the October FOMC meeting is cancelled, Kalshi resolves 'Fed maintains rate' Yes and its Hike 25bps market No, while Polymarket resolves No if no statement is released, so both pay No.
  - A move to a non-displayed level such as 12.5bps is rounded up to 25bps on Polymarket, which Kalshi's 'Hike 25 bps' bracket would also treat as a 25bps hike under its bracket rules.
  - A 50bps hike resolves Kalshi's 'Hike 25 bps' to No, while Polymarket's Yes (increase by 25bps) would also resolve No, so the mapping holds only for exactly 25bps moves.
- **KXFEDDECISION-27JAN-C25**
  - If the January 2027 FOMC meeting is cancelled, Kalshi resolves 'Fed maintains rate' to Yes while Polymarket resolves to 'No change', which is a No on the cut market in both cases.
  - A non-25bp move is rounded up to the nearest 25bps bracket on Polymarket (e.g. a 12.5bp cut counts as 25bps), whereas Kalshi's rules do not state this rounding explicitly.
- **KXFEDDECISION-27JAN-H0**
  - If the January 2027 FOMC meeting is cancelled, Kalshi resolves Yes while Polymarket resolves to the 'No change' bracket, which is the same outcome as Yes here.
  - A rate change to a non-displayed level is rounded up to the nearest 25 bps on Polymarket, which Kalshi's bracket rules also cover; an odd-sized move would not resolve Kalshi's 0bps Yes.
- **KXFEDDECISION-27JAN-H26**
  - If the January 2027 FOMC meeting is cancelled, Kalshi resolves 'Fed maintains rate' to Yes while Polymarket's 'No change' bracket would resolve to Yes, so both would still map to a no-hike outcome.
  - A non-25bp move (e.g. 12.5bps) is rounded up to the nearest 25bp bracket on Polymarket, which could differ from Kalshi's bracket treatment for odd-sized moves.
  - Kalshi's Yes is 'Hike >25bps' and Polymarket's Yes is '50+ bps'; a 37.5bp move would count as 50 on Polymarket's rounding rule but as more than 25 on Kalshi, so both resolve Yes.

## Limits

- 60 s sampling misses gaps that open and close within a minute; the executable tier can only undercount those.
- The executable window is 2.7 hours long. Zero or few episodes over hours says little about weeks; v1-final re-runs this after about four weeks of snapshots.
- Kalshi's 0.07 taker coefficient is secondhand and unverified (the fee-schedule PDF could not be fetched; `docs/venues.md`). Polymarket's fee rounding rule is unverified. Either could move edges by a fraction of a cent per contract.
- The screened tier charges Kalshi's fee on one contract, ceiled to the cent (at 50c: 0.07 x 0.25 = $0.0175, charged as $0.02), so screened net gaps are conservative by up to ~$0.01 per contract, the same size as the 0.01 threshold. The executable tier rounds per order-book slice, where the cent is spread over the slice's contracts.
- Polymarket price history is a traded/mid price, not an ask, so the screened tier overstates what was takeable; screened hours are never called arbitrage.
- Few exact pairs (9), from Kalshi series `KXFEDDECISION`; nothing here generalizes to other categories.
- Matches were adjudicated by an LLM, claude-haiku-5-5 (prompt v2); no human review sitting has happened yet: 0 of 9 eligible pairs reviewed, 9 unreviewed.
- No execution latency, fill risk, partial fills, or capital lock-up costs beyond the annualized figure are modeled. The transfer cost is an assumption, reported as a sensitivity only.

## Next

Plan 3: add Polymarket US (makes the US cut meaningful), crypto near-matches priced with the option-implied distribution, and perp-vs-spot basis.

## Queries

```sql
-- screened tier
SELECT ts, kalshi_id, pm_id, direction, net_gap_top FROM venue_arb.screened FINAL WHERE k_price < 1
-- executable tier: edges
SELECT ts, kalshi_id, pm_id, direction, best_edge, capacity_usd FROM venue_arb.edges FINAL
-- executable tier: episodes (Postgres)
SELECT kalshi_id, pm_id, direction, start_ts, end_ts, snapshots, max_edge, median_capacity_usd, annualized, us_executable, transfer_cost_usd FROM venue_arb.opportunities
```

Eligible pairs: `venue_arb/flows/exact_sql.py` (`ELIGIBLE_SQL`) filtered by `venue_arb/exact.py`; tail risks: `ADJ_SQL`; tier scope: `SCOPE_SQL`, both in `venue_arb/report/note.py`. Rows from the three tier queries above are kept only for pairs in tier scope (`_scoped`).
