# venue-arb: stock perps, public oracle view with private Schwab validation

Design, 2026-10-23. Status: approved by Sam 2026-10-23 with manual-only exclusion (Sam skipped the brainstorm questions; every decision below not
stated by Sam is marked **(Claude's call)**). Amends `2026-10-22-venue-arb-stock-perp-basis-design.md` (Plan 6,
merged to main at 9b99493, not deployed). Everything that design says still holds unless this document changes it.

## 1. Why this exists

On 2026-10-23 Sam had the terms read (record in the memory vault, summarised in §7). The Schwab Trader API
Developer Program Agreement (May 2023) licenses Sam's "Trader API – Individual" app for his own use: data may be
used only through his own application, not by third parties (§9.4); exchange data is licensed personally and may not
be shared or re-used (§9.4.2); derivative works of Schwab data are barred (§5(n), §8.1); Schwab names may not be used
without written permission (§8.3); apps whose output reaches third parties are "Commercial Applications" (§2, §6.2).
Pyth equities with display rights start at $2,500 a month (Pro plan), outside the project's no-paid-data rule.

Sam's direction: a public view built on the venue's own oracle, with Schwab used **only as private validation**.

## 2. The rule this design enforces

**Nothing derived from Schwab data leaves the OptiPlex.** Not in `live.json`, not in the history Parquet pushed to
the VM, not in the research block, not in any committed file (the repo is meant to go public), not in the page's
copy, not as a reason shown for hiding a row. Schwab data stays in ClickHouse and in private reports on the box.

The public tier uses only Hyperliquid data (perp books, mark, oracle, funding), which Hyperliquid's terms do not
restrict. Hyperliquid's and trade.xyz's terms govern their trading websites, which we do not use.

## 3. Goals, non-goals, anti-goals

**Goals**
- A public Stock perps view: per name, the perp's mark against the venue's oracle, the perp-side costs of trading
  it, perp-side depth, funding, and a verdict that never claims a two-leg trade.
- The full two-leg, Schwab-referenced executable tier keeps running privately, unchanged in substance.
- A private validation report: how closely the venue's oracle tracks Schwab's consolidated quote, per name and
  session, so Sam knows whether the public reference is fair.
- The weekend tier made public on oracle data alone.

**Non-goals**
- Any paid data source. The Polymarket terms question (it bears on the live pair tier) is a separate decision.
- Showing stock-side size, spread or borrow from any source.

**Anti-goals**
- A public row labelled "Executable edge" for stock perps. The stock leg is not modelled publicly, so no public
  stock-perp row claims a completed trade.
- A public number that moves because of a Schwab quote: if a figure would change when Schwab data changes, it is
  private.
- The word "Schwab" on the page.

## 4. The public row

**Reference.** The venue's oracle (`stock_perp_ctx.oracle`) at the same tick. During the external session (US names
Sunday 8:05 PM to Friday 8:00 PM ET, which covers pre-market, regular, post-market and Blue Ocean overnight; Asian
names in their home sessions) the oracle is the venue's externally derived fair price. Outside it the oracle is an
EWMA of the perp's own impact price, so a perp-vs-oracle gap measures nothing; those rows show the state
"oracle internal" and carry no basis verdict **(Claude's call)**. The session state comes from the label's
exchange and a fixed session calendar in `venue_arb/sessions.py` (new), not from any Schwab field.

**Figures.**
- `oracle_basis_bps = (mark / oracle − 1) × 1e4`, the displayed basis.
- Perp-side costs over the existing 24 h hold: 2 × taker, perp half spread on exit, funding × 24. Borrow is a
  stated assumption, not market data, so it stays as a sensitivity line only **(Claude's call)**.
- `perp_net_bps`: the walked perp fill against the oracle, in the trade's favour, minus those perp-side costs.
  Labelled on the page "before stock-leg costs".
- `perp_depth_usd`: perp book depth walked while the marginal perp-side net stays ≥ 10 bps (the existing rule,
  with no stock cap).

**Verdicts (public, verbatim strings to pin in tests):**
- `Oracle basis · {net:+.0f} bps before stock costs · ${depth:,.0f} perp depth`, when `perp_net_bps ≥ 10` and depth
  ≥ $100.
- `Oracle basis · within perp costs`.
- `Oracle internal · market closed` (outside the external session).
- `Stale · no snapshot in 5 min` (reuses the existing stale rule).

**Trade line:** "short perp / long stock" (or the reverse), as today, with the stock leg plain text and no source
named.

## 5. What changes in the code

- **Scoring (pure).** `stock_perps.score` gains an explicit `mode: Literal["executable", "oracle"]`
  **(Claude's call)**. `oracle` mode uses the oracle as the reference, no stock cap, no stock spread, and reports
  `perp_net_bps` and `perp_depth_usd`. `executable` mode is today's behaviour (Schwab when fresh, else oracle).
- **Storage.** The hourly scoring flow writes **two** rows per tick into `stock_perp_edges`, one per mode, with a new
  column `mode LowCardinality(String)` and `mode` added to the ORDER BY so the two do not collapse under
  ReplacingMergeTree. Migration `ch/008` is not yet applied anywhere, so it is edited in place (same reasoning as the
  Plan 6 `perp_spread_bps` ruling) **(Claude's call)**. Episodes keep `source = 'stock_perp'` for the executable mode;
  the oracle mode gets `source = 'stock_perp_oracle'`, and `sql_checks/opportunities_source_valid.sql` accepts both.
- **Bundle and publisher.** `stock_perp_rows` builds public rows from oracle mode only. The `perps()` reader stops
  reading Schwab quotes for the bundle. `Pair.reference` becomes `{"kind": "oracle", "state": "external" |
  "internal", "asof"}`; `Pair.costs` drops `stock_spread_bps` and `borrow_bps` (borrow moves to the sensitivity
  line). Research aggregates, sparks and the history Parquet read `mode = 'oracle'` only. A test asserts that no
  bundle or history field is computed from `stock_perp_refs`, by building a bundle from fixtures with the refs reader
  raising.
- **Weekend tier.** Public weekend rows use oracle prints only: Monday's open for US names is the first external
  oracle print at or after Monday 9:30 AM ET, and KRX names keep their Sunday-evening Korean oracle open. The
  Schwab-referenced opens move to the private report. `stock_perp_weekends` gains `ref_kind` values `oracle_us` and
  `oracle_krx` for the public rows; Schwab rows are no longer written there **(Claude's call)**.
- **Dashboard.** The Reference cell shows "oracle, live" or "oracle internal"; the word Schwab is removed from
  `explorer.js` and the docs-facing copy; the Explorer cost table lists perp-side lines only and states that the
  stock leg is not modelled on this page. `copy.METHOD["stock_perps"]` is rewritten to describe the public view
  (oracle reference, perp-side costs, no stock leg) without naming Schwab.
- **Private validation.** A new module `venue_arb/validation.py` (pure) and a CLI
  `python -m venue_arb.validation` on the OptiPlex compare `stock_perp_ctx.oracle` with the Schwab mid at the same tick
  during US regular and extended sessions: per name and day, median and p95 of |oracle − mid| in bps, share of ticks
  over 10 bps, and count of ticks compared. Output goes to a ClickHouse table `stock_perp_oracle_check` (additive) and
  a Markdown report under `data/private/` (git-ignored) **(Claude's call)**. A qa warn `oracle_drift` fires per name
  when its trailing 5-day p95 exceeds 10 bps.
- **No automatic hiding (Sam, 2026-10-23).** Every name is shown on the public page. The validation report is for
  Sam to decide by hand; removing a name is a manual edit of `labels/stock_perps_excluded.txt` with a neutral
  comment that does not cite Schwab.

## 6. Constraints

- Everything in the Plan 6 design and the dashboard designs still holds (60 s cadence, no raw quotes, no paid data,
  no inbound path home, CI before push, additive migrations except the in-place 008 edit above).
- `DASHBOARD_SHOW_STOCK_PERPS` keeps its meaning (the public oracle view is on or off). There is no flag that puts
  Schwab-derived numbers on the page; that code path is removed rather than gated.
- The Hyperliquid trade link stays; the page already says Hyperliquid blocks US traders.

## 7. Terms record (to land in `docs/venues.md`, dated 2026-10-23)

- Schwab Trader API Developer Program Agreement, May 2023: the clauses in §1 above; plus §5(v) automated collection
  only through your own app, §10 keep 18 months of records with audit rights, Texas law.
- Hyperliquid Terms of Use, updated 2026-06-15: cover only the app.hyperliquid.xyz interface; US persons barred from
  it; no data licence or scraping clause.
- trade.xyz Terms of Use, updated 2026-09-06: cover app.trade.xyz; US persons barred; no scraping of that interface.
- Pyth: website terms grant no right to reuse price data; API plans: free (no API), Starter $500 (crypto only), Pro
  from $2,500 (equities, display rights).
- Polymarket Terms of Use, effective 2026-08-11: noted here for completeness; the decision belongs to the pair tier.

## 8. Sam's actions

- Read the validation report after the first week of data and decide by hand whether any name should be excluded.
- Review the rewritten `copy.METHOD["stock_perps"]` once rendered.
- Unchanged: the Schwab login at deploy and weekly; the Polymarket decision for the live pair tier.

## 9. Left to the builder

- The session calendar's holiday handling (US exchange holidays at minimum; a hard-coded list for 2026–2027 is fine).
- Exact schema of `stock_perp_oracle_check` and the report layout.
- How the two scoring modes share the walk (one walk with two cost sets, or two calls).
- Plotly specs for any changed card; the copy wording within the rules above.
