# venue-arb: pre-IPO perps as an explicit kind

Design, 2026-10-27. Status: drafted after a short brainstorm with Sam; decisions he made are marked **(Sam)**, the
rest are **(Claude's call)**. Amends `2026-10-23-venue-arb-stock-perp-public-view-design.md` (Plan 7) and the
Plan 6 design; everything in them still holds unless this document changes it.

## 1. Why this exists

trade.xyz lists perps on companies that have not listed yet (SpaceX, Cerebras, and since 2026-10-26 Anthropic as
`xyz:ANT`). The label ontology has no word for them, so they are handled two different ways today: SPCX, QNT and
CBRS sit in `labels/stock_perps_excluded.txt` (not collected at all), while ANT is a `none` label with an empty
exchange (collected, never scored). Everything downstream infers "pre-IPO" from the empty exchange and gets it
half right: the public verdict reads "Oracle internal · market closed" for a contract that has no market, the
research block counts it as a name, and the Explorer offers a stock leg that does not exist.

Sam's direction **(Sam)**: keep pre-IPO names, show them on the public page behind a filter, and when one lists,
have it become a stock, continuous with its own past.

## 2. Goals, non-goals, anti-goals

**Goals**
- One explicit field decides how a pre-IPO perp behaves everywhere: collection, session, scoring, verdict,
  weekend tier, oracle check, research counts, Explorer copy, public filter.
- The listing day is a label edit and nothing else; the row keeps its key and its history.
- All four pre-IPO names today behave the same.

**Non-goals**
- Pricing or scoring a pre-IPO perp against anything. There is no reference; the row is informational.
- Automatic classification. The venue exposes no asset class, so the label file stays the ontology and a human
  writes each row (the existing `unlabelled_perps` warn names new listings).

**Anti-goals**
- A pre-IPO row that reads as an edge, a closed market, or a two-leg trade.
- Any public number that depends on a private source (unchanged rule: nothing Schwab-derived leaves the box; a
  pre-IPO name has no Schwab data anyway).

## 3. The ontology

`labels/stock_perps.csv` gains a column `kind` with two values **(Claude's call)**:

| kind | ref_kind | ref_symbol | exchange | meaning |
|---|---|---|---|---|
| `equity` | `schwab` or `none` | required for `schwab` | required | today's rows, unchanged |
| `pre_ipo` | `none` | empty | empty | a perp on a company with no listed share |

`load_labels` rejects a row that breaks the table (a `pre_ipo` row with a reference or an exchange, an `equity`
row without an exchange), naming the row. `Label` gains `kind`. The exclusion file keeps its meaning (hidden from
every public path and not collected) and is now only for what is not an equity-like contract at all: indices,
commodities, FX, and delisted names.

**The pre-IPO names (Claude's call, from the companies' status on 2026-10-27).** Two rows already in the labels
are unlisted companies and become `pre_ipo`: `xyz:YMTC` (Yangtze Memory; today `none` with no exchange, which the
new rule would reject as `equity`) and `xyz:JIO` (Reliance Jio; today carries `NSE`, which goes, as a `pre_ipo`
row has no exchange and the NSE window was only ever an assumption). With `xyz:ANT` that is three rows to
rewrite. SPCX, QNT and CBRS move from the exclusion file into the labels as `pre_ipo` in the same change, after
the builder checks each one's listing state (SpaceX held an IPO in 2026): a name that has already listed is **not**
written as `equity` by the builder; it stays in the exclusion file and is reported to Sam, who makes the listing-day
edit of §7 himself with the ticker and exchange. A wrong `equity` row would score a public row against the wrong
reference, while a delayed one only hides it. So the change ends with up to six `pre_ipo` rows and a short status
report.

## 4. Behaviour by kind

| | `equity` | `pre_ipo` |
|---|---|---|
| collected (ctx, books) | yes | yes, every tick, so the history exists on listing day |
| Schwab quote | if `ref_kind = schwab` | never |
| session state (`sessions.state`) | external / internal by calendar | `pre_ipo`, always |
| scoring (both modes) | yes | no rows written |
| public verdict | Plan 7 strings | `Pre-IPO · no listed market` (verbatim, pinned in tests) |
| reference cell | oracle live / internal | `—` (the Reference check shows `none`, status `partial`) |
| weekend tier | yes | excluded |
| oracle check (private) | US names | excluded (already: not US; now by kind, so a `pre_ipo` row with a US exchange cannot slip in) |
| research block | counted | excluded from names, episodes, hourly max and the headline |
| Explorer detail | cost table, chart | cost table replaced by one line: "No listed share yet: nothing to score until it lists." No chart |
| sparkline / public history | yes | none before listing (see below) |
| category | `stock_perps` | `pre_ipo`, shown as "Pre-IPO" |

**Before listing, nothing shows behind the row (Claude's call, baseline).** Every public history read takes scored
external-session rows, and a pre-IPO row is never scored, so the row carries its live mark, funding and verdict and
no sparkline, chart or history Parquet rows. The collector's tick history stays private on the box for the
listing-day analysis. A basis chart was considered and dropped: a pre-IPO oracle is the venue's own pricing, so the
gap to it is noise. A public price history (mark series from the context table) is a possible later addition; the
cross-market pre-IPO divergence view in §11 is the more useful place for that data, so this design builds neither.

`sessions.state` gains the third literal `pre_ipo`; every consumer that switches on `internal` treats `pre_ipo` the
same way for "unscored" and differently only for copy. The scorer skips `pre_ipo` names before the walk, so
`stock_perp_edges` never holds them and the hourly history Parquet carries no net for them.

## 5. Public presentation

- The Scanner's category chips gain **"Pre-IPO"** next to "Stock perps" **(Sam: a filter; the chip is Claude's
  call)**. The chip uses the stock-perp header (Name, Trade, Basis, Net, Funding, Capacity, Reference, Verdict);
  Trade reads `perp only`, Basis, Net and Capacity read `—`, Funding shows the venue's rate as today. In the All
  view the rows sort last with the other unscored rows.
- Footer caption under the chip: "{n} pre-IPO perps — perps on companies with no listed share: price history only,
  no reference, nothing to score. Not advice. Hyperliquid blocks US traders." (wording within these rules is the
  builder's).
- `copy.METHOD["stock_perps"]` gains one sentence saying pre-IPO perps are listed for their price history and are
  not scored; the research cards and headline exclude them.
- The page never names a source; unchanged.

## 6. Key stability (the decision)

Today a stock-perp row's key is `{perp}~{stock}~stock_perp`, where `stock` is the reference symbol or, without
one, the perp symbol again. On listing day the reference symbol appears and the key would change
(`xyz:ANT~xyz:ANT~stock_perp` → `xyz:ANT~ANTH~stock_perp`), restarting the sparkline and splitting the history.

**Decision (Claude's call, following Sam's "reflect as being stock" with continuity):** the key becomes
`{perp}~stock_perp` for every stock-perp row, `equity` and `pre_ipo` alike. The stock symbol was never part of a
row's identity (the direction is a field, and one perp has one reference), so nothing is lost. Consequences the
builder carries out:

- `bundle.stock_key` takes the perp alone; the second leg keeps carrying the stock symbol as data.
- `publish.history_table` keys stock rows the same way, and `stock_refs()` is no longer needed for keys (it may
  remain for the Explorer's reference symbol).
- The public history Parquet pushed since 2026-10-26 holds the old keys. After deploy, rewrite it once with
  `secrets uv run python -m venue_arb.publish history --hours 240`; the server keeps about ten days of hour files,
  so this covers all of them. The client reads keys from the bundle, so no client migration.
- Sample bundles regenerated; the key format is pinned in `tests/test_bundle.py`.
- Nothing in ClickHouse or Postgres stores this key (episodes key on `(symbol, 'oracle')`), so no migration.

## 7. The listing-day runbook (README)

1. Edit the name's row: `kind` → `equity`, `ref_kind` → `schwab` (US) or `none` (elsewhere), `ref_symbol`,
   `exchange`, `currency`. One commit through CI, fast-forward main. No restart: the collector reloads labels every
   ten ticks, the publisher and every flow read them per run.
2. From the next collector tick the Schwab quote flows (US names); from the next external-session tick the row is
   scored; the weekend tier and the oracle check include it from their next runs.
3. The row keeps its key; its sparkline and history begin on listing day (the pre-IPO stretch has no public rows).

## 8. Constraints

- Everything in the Plan 6 and Plan 7 designs still holds: 60 s cadence, no raw quotes, no paid data, no inbound
  path home, CI before push, additive migrations (none needed here), manual exclusion only, no Schwab text or
  figure on the page.
- Verdict strings are verbatim and pinned; `test_no_schwab_on_the_page` and the key-leak test keep running.
- `unlabelled_perps` keeps naming new listings; nothing is classified automatically.

## 9. Left to the builder

- The exact chip order and the caption wording within §5.
- Whether the Explorer's basis chart for a pre-IPO row is the existing chart with the net trace dropped or a
  smaller one; either is fine.
- The listing-state check for SPCX, QNT and CBRS at build time (§3) and their rows.
- The test list: label validation, `sessions.state` for `pre_ipo`, scorer skip, verdict string, research exclusion,
  key format, history rewrite, static copy guards.

## 10. Sam's actions

- For any of SPCX, QNT and CBRS that the builder reports as already listed: the listing-day edit of §7.
- On a listing day, the one label edit in §7.

## 11. Future, not in this design: cross-market pre-IPO divergence

Sam's idea (2026-10-27): the same unlisted company is priced by several perp markets at once (Anthropic on
trade.xyz as `xyz:ANT` near 115 and on another Hyperliquid deployer as `io:ANTH` near 1,950; Binance and Coinbase
list their own), and the markets disagree. A divergence view would normalise every contract to one implied
valuation (each defines its own unit and settlement) and show the gap as a two-perp spread with both legs' funding
and fees. That is a project of its own: research first (which markets, their contract units, whose terms allow
the data; the other Hyperliquid deployers share the read-only API and terms already in use, Binance and Coinbase
carry data-use limits this project has avoided), then a design. The `pre_ipo` kind here is its prerequisite.
