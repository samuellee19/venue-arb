# Stock Perps Public Oracle View (Plan 7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public stock-perps view use only Hyperliquid data (perp against the venue's oracle), keep the
Schwab-referenced executable tier private, and add a private oracle-vs-Schwab validation report.

**Architecture:** The pure scorer gains an `oracle` mode beside today's `executable` mode; the hourly flow stores
both per tick in `stock_perp_edges` (new `mode` and `session` columns). Everything that reaches the VM (bundle,
history Parquet, research block, weekend card) reads `mode = 'oracle'` only, and the bundle no longer reads Schwab
quotes at all. A new daily flow compares the oracle with Schwab's mid and writes a private table and report.

**Tech Stack:** Python 3.12, pydantic v2, ClickHouse via the platform `qa` package, Prefect 3 `serve`, vanilla JS,
pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-venue-arb-stock-perp-public-view-design.md` (amends
`2026-10-22-venue-arb-stock-perp-basis-design.md`).

## Global Constraints

- Nothing derived from Schwab data leaves the OptiPlex: not in `live.json`, the history Parquet, the research block,
  any committed file, the page's copy, or any reason shown for hiding a row.
- No automatic hiding of names. Exclusion is a manual edit of `labels/stock_perps_excluded.txt` with a neutral
  comment.
- The word "Schwab" never appears in `dashboard/static/*`, in `copy.METHOD["stock_perps"]`, or in either sample
  bundle.
- Public verdict labels, verbatim:
  `f"Oracle basis · {net:+.0f} bps before stock costs · ${depth:,.0f} perp depth"`,
  `"Oracle basis · within perp costs"`, `"Oracle internal · market closed"`, `"Stale · a leg has no snapshot in 5 min"`.
- Public cost lines: 2 × perp taker, perp half spread on exit, funding over `STOCK_PERP_HOLD_H = 24`. No stock spread,
  no borrow in the public net.
- Thresholds unchanged: `STOCK_PERP_MIN_NET_BPS = 10.0`, `MIN_CAPACITY_USD = 100.0`, 2 snapshots, 150 s gap.
- `DASHBOARD_SHOW_STOCK_PERPS` (default False) turns the public oracle view on or off; no flag puts Schwab-derived
  numbers on the page.
- Migration `db/migrations/ch/008_stock_perps.sql` is edited in place (never applied anywhere); any new table goes in
  a new additive migration `ch/009`.
- CI before every commit (`uv run ruff check .`, `uv run pytest -m "not integration" -q`); no Co-Authored-By trailers.

## Review Focus

1. **US exchange holiday weekday**: the oracle is not externally priced on a full-day US holiday, so a US row must read
   "Oracle internal · market closed", not show a basis verdict. Pinned in Task 1 (`test_us_holiday_is_internal`).
2. **Asian name on a US weekday evening**: KRX outside its session must be internal even while US names are external.
   Pinned in Task 1 (`test_krx_session_is_its_own`).
3. **An oracle-mode episode written under the executable source** would leak into the private counts and vice versa.
   Pinned in Task 3 (`test_two_modes_two_sources`).
4. **Public bundle silently reading a Schwab quote** through a leftover code path. Pinned in Task 4
   (`test_public_bundle_never_reads_refs`: the refs reader raises and the bundle still builds).
5. **Weekend open taken from a Schwab quote** for the public card. Pinned in Task 5
   (`test_public_weekend_rows_use_oracle_only`).

---

### Task 1: Session calendar

**Files:**
- Create: `venue_arb/sessions.py`, `tests/test_sessions.py`

**Interfaces:**
- Produces: `external(label: Label, ts: datetime) -> bool` and `state(label, ts) -> Literal["external", "internal"]`.
  Rules (from the trade.xyz Specification Index, read 2026-10-22):
  - US-listed (`label.exchange` in NASDAQ, NYSE, NYSE ARCA, CBOE BZX, case-insensitive): external from Sunday 20:05
    to Friday 20:00 `America/New_York`, except full-day US market holidays (`US_HOLIDAYS`, a frozenset of dates
    for 2026 and 2027 per the NYSE calendar).
  - KRX: Mon–Fri 09:01–15:20 and 16:05–20:00 `Asia/Seoul`. TSE: 08:20–16:30 and 17:00–23:59 `Asia/Tokyo`.
    HKEX: 09:30–12:00 and 13:00–16:00 `Asia/Hong_Kong`. SSE and SZSE: 09:30–11:30 and 13:00–14:57 `Asia/Shanghai`.
  - Any other exchange (including NSE and a blank exchange): always internal.
  - Asian exchange holidays are not modelled **(Claude's call; a holiday then reads external with a stale oracle,
    which the existing stale rule does not catch; noted in docs)**.

- [ ] **Step 1: Write the failing tests**

```python
def test_us_external_window():
    nvda = Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
    assert s.external(nvda, et(2026, 10, 21, 2, 0))        # Wed 2 AM ET, Blue Ocean overnight
    assert not s.external(nvda, et(2026, 10, 23, 20, 0))   # Fri 8:00 PM ET exactly: closed
    assert not s.external(nvda, et(2026, 10, 25, 20, 4))   # Sun 8:04 PM ET
    assert s.external(nvda, et(2026, 10, 25, 20, 5))       # Sun 8:05 PM ET

def test_us_holiday_is_internal():
    assert not s.external(nvda, et(2026, 11, 26, 11, 0))   # Thanksgiving 2026

def test_krx_session_is_its_own():
    skhx = Label("xyz:SKHX", "none", "000660", "KRX", "KRW", "SK hynix")
    assert s.external(skhx, kst(2026, 10, 22, 10, 0)) and not s.external(skhx, kst(2026, 10, 22, 15, 40))
    assert not s.external(skhx, et(2026, 10, 21, 11, 0))   # US morning = Seoul midnight

def test_unknown_exchange_is_internal():
    assert not s.external(Label("xyz:JIO", "none", "", "NSE", "INR", "Jio"), utc(2026, 10, 21, 6, 0))
```

- [ ] **Step 2: Run, see them fail, implement with `zoneinfo`, run, see them pass; ruff; commit**

```bash
git add venue_arb/sessions.py tests/test_sessions.py
git commit -m "Stock perps: session calendar for the venue oracle's external hours"
```

---

### Task 2: Oracle scoring mode (pure)

**Files:**
- Modify: `venue_arb/stock_perps.py`, `tests/test_stock_perps.py`

**Interfaces:**
- Consumes: today's `score`, `costs_bps`, `walk_perp`, `StockPerpEdge`.
- Produces:
  - `Mode = Literal["executable", "oracle"]`; `StockPerpEdge` gains trailing fields `mode: Mode = "executable"` and
    `session: Literal["external", "internal"] = "external"`.
  - `score(ctx, book, ref, *, mode: Mode = "executable", session="external", hold_h=..., borrow_bps_yr=...,
    min_net_bps=...)`. In `oracle` mode: the reference is always `Reference("oracle", ctx.symbol, ctx.oracle, None,
    None, None, None, ref.asof)` whatever `ref` holds; the stock leg has no cap and no spread
    (`stock_spread_bps = None`); the fixed costs are `perp_fee + perp_spread + funding`; borrow is computed into
    `borrow_bps` (the assumption, for the sensitivity line) but **not** subtracted from `net_bps`; the level-accept
    rule and the fill-based net are otherwise today's; `capacity_usd`/`capacity_contracts` are the perp depth that
    clears `min_net_bps`. `executable` mode is unchanged.
  - `edge_row` writes `mode` and `session`.
  - `gross_to_net(edge, hold_h)` rescales funding only for oracle-mode edges (borrow is not in their net).

- [ ] **Step 1: Write the failing tests**

```python
def test_oracle_mode_ignores_the_schwab_reference():
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_SCHWAB, mode="oracle")
    assert e.mode == "oracle" and e.ref_kind == "oracle" and e.reference == CTX.oracle
    assert e.stock_spread_bps is None and e.stock_cap_usd is None

def test_oracle_mode_net_excludes_stock_spread_and_borrow():
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_ORACLE, mode="oracle")
    fill = e.capacity_usd / e.capacity_contracts
    assert e.net_bps == pytest.approx(sp.basis_bps(fill, CTX.oracle) - e.perp_fee_bps - e.perp_spread_bps - e.funding_bps)
    assert e.borrow_bps >= 0.0   # computed, not subtracted (long stock here: 0)

def test_oracle_mode_capacity_is_perp_depth_at_ten_bps():
    # same 12 / 5 bps two-level book as test_score_capacity_stops_at_the_first_failing_level, oracle = 100.0
    e = sp.score(CTX_ORACLE_100, BOOK_12_5, REF_ORACLE, mode="oracle")
    assert e.capacity_usd == pytest.approx(BOOK_12_5_LEVEL1_USD) and e.capacity_usd > 0

def test_edge_row_carries_mode_and_session(): ...
def test_gross_to_net_oracle_mode_rescales_funding_only(): ...
```

- [ ] **Step 2: Run, see them fail, implement, run `tests/test_stock_perps.py tests/test_episodes.py`, ruff, commit**

```bash
git commit -am "Stock perps: oracle scoring mode (perp-side costs, perp depth, no stock leg)"
```

---

### Task 3: Store both modes; sources; checks

**Files:**
- Modify: `db/migrations/ch/008_stock_perps.sql` (in place), `venue_arb/stock_perp_window.py`,
  `venue_arb/flows/stock_perp_score.py`, `sql_checks/opportunities_source_valid.sql`,
  `sql_checks/stock_perp_edges_capacity_consistent.sql`, `tests/test_stock_perp_flow.py`, `tests/test_migrate.py`

**Interfaces:**
- Consumes: Task 1 `sessions.state`, Task 2 `score(..., mode=, session=)`.
- Produces:
  - `stock_perp_edges` gains `mode LowCardinality(String)` and `session LowCardinality(String)` after `ref_kind`;
    `ORDER BY (symbol, mode, direction, ts)`.
  - `score_window` scores every tick twice (`mode="executable"` with today's reference choice, `mode="oracle"`)
    with `session = sessions.state(label, ts)`, returns rows for both, and finds episodes per (symbol, mode,
    direction): oracle-mode episodes only from `session == "external"` ticks (internal ticks enter as
    non-qualifying points). `stock_perp_opportunity_row(ep, source)` takes the source: `"stock_perp"` for executable,
    `"stock_perp_oracle"` for oracle. Stats gain `oracle_rows` and `internal_rows`.
  - `opportunities_source_valid.sql` accepts `source = 'stock_perp_oracle'` with the two stock directions.
  - The capacity check applies per row unchanged (both modes accept capacity only at ≥ 10 bps marginal net).
  - The flow's CONTRACT gains `mode` and `session`.

- [ ] **Step 1: Write the failing tests**

```python
def test_two_modes_two_sources():
    rows, eps, st = score_window(CTX_ROWS, BOOK_ROWS, REF_ROWS, LABELS, start=T0, end=T1)
    assert {r["mode"] for r in rows} == {"executable", "oracle"} and len(rows) == 2 * st["snapshots"]
    assert {src for src, _ in eps} <= {"stock_perp", "stock_perp_oracle"}
    assert st["oracle_rows"] == st["snapshots"]

def test_internal_ticks_make_no_oracle_episode(): ...   # a weekend window: oracle rows stored, session "internal", no episode
def test_migration_008_has_mode_and_session_in_order_by(): ...
```

`episodes.Episode` has no `source` field, so `score_window` returns `(rows, [(source, ep), ...], stats)` and the
flow builds each opportunity row with `stock_perp_opportunity_row(ep, source)`.

- [ ] **Step 2: Run, see them fail, implement, run the covering tests, ruff, commit**

```bash
git commit -am "Stock perps: store executable and oracle modes per tick; oracle episodes as their own source"
```

---

### Task 4: Public bundle and publisher read the oracle mode only

**Files:**
- Modify: `venue_arb/bundle.py`, `venue_arb/publish.py`, `venue_arb/report/research.py` (if its inputs change),
  `tests/test_bundle.py`, `tests/test_publish.py`, `tests/test_research.py`

**Interfaces:**
- Consumes: Task 2 `score(mode="oracle")`, Task 1 `sessions.state`.
- Produces:
  - `stock_perp_rows(perps: list[tuple[PerpCtx, Label, SnapPerp]], sparks, now) -> list[Pair]` (no quote): scores
    with `mode="oracle"`; `reference = {"kind": "oracle", "state": state, "asof": ts}`; `costs = {"perp_fee_bps",
    "perp_spread_bps", "funding_bps"}` (None on stale rows); `sensitivity = {"net_bps_1h", "net_bps_7d",
    "net_bps_with_borrow"}` where the last is `net_bps − borrow_bps`.
  - `stock_perp_verdict(edge, state, live, now, *, has_book)` with the four public labels in Global Constraints, in
    this precedence: stale, internal, no book ("No asks on one side", existing), oracle basis (≥ 10 bps and ≥ $100
    depth), within perp costs. Checks: `reference` (ok "oracle live, N s old" / partial "oracle internal: market
    closed"), `fees_carry` (detail `f"net {net:+.1f} bps after 2× taker, perp spread and {hold} h funding"`),
    `fees_depth` (perp depth vs $100), `jurisdiction` (unchanged).
  - Publisher: `perps()` stops reading `stock_perp_refs` (delete `PERP_QUOTES_SQL`); research SQL (`SP_HOURLY_SQL`,
    `SP_COUNTS_SQL`, `SP_SHARE_SQL`, `SP_BY_NAME_SQL`, `SP_EDGES_SQL`), the stock leg of `SPARKS_SQL`, and
    `STOCK_PERP_HOUR_SQL` all filter `mode = 'oracle' AND session = 'external'` (the share query keeps its
    hour averaging; `maxOrNullIf(..., ref_kind = 'schwab')` becomes a plain `max`); episode counts read
    `source = 'stock_perp_oracle'`. The borrow ×10 sensitivity in the research block becomes `episodes_with_borrow`.

- [ ] **Step 1: Write the failing tests**

```python
def test_public_bundle_never_reads_refs(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    readers = {**READERS, "quotes": boom}          # any refs/quotes reader raises if called
    b = publish.tick({}, readers, NOW)
    rows = [p for p in b.pairs if p.kind == "stock_perp"]
    assert rows and all(p.reference["kind"] == "oracle" for p in rows)
    assert "schwab" not in b.model_dump_json().lower()

def test_public_labels(): ...   # four fixtures -> the four verbatim labels; never startswith("Executable edge")
def test_research_sql_reads_oracle_mode_only():
    for q in (publish.SP_HOURLY_SQL, publish.SP_COUNTS_SQL, publish.SP_SHARE_SQL, publish.SP_BY_NAME_SQL,
              publish.SP_EDGES_SQL, publish.STOCK_PERP_HOUR_SQL):
        assert "mode = 'oracle'" in q and "session = 'external'" in q
    assert "stock_perp_refs" not in publish.PERP_CTX_SQL + publish.PERP_BOOKS_SQL and not hasattr(publish, "PERP_QUOTES_SQL")
```

- [ ] **Step 2: Run, see them fail, implement, run the covering tests plus `tests/test_dashboard_api.py`, ruff, commit**

```bash
git commit -am "Stock perps: public bundle, history and research from the oracle mode only; no Schwab read"
```

---

### Task 5: Public weekend tier on oracle prints

**Files:**
- Modify: `venue_arb/weekends.py`, `venue_arb/flows/stock_perp_weekend.py`, `tests/test_weekends.py`

**Interfaces:**
- Produces: `weekend_row(...)` for a US label (`exchange` US-listed) takes `oracle` prints as refs:
  `p_open_pre` = first oracle print at or after Sunday 20:05 ET (the overnight external restart),
  `p_open_reg` = first oracle print at or after Monday 09:30 ET within the regular session; `ref_kind = "oracle_us"`.
  KRX keeps `oracle_krx`. Schwab refs and `schwab_daily` rows are no longer read by the public weekend flow (the
  `daily` parameter and `fresh_quote` move to the validation module in Task 6). `_open()` accepts `oracle_us` rows
  through `p_open_reg`.

- [ ] **Step 1: Write the failing test**

```python
def test_public_weekend_rows_use_oracle_only():
    row = wk.weekend_row("xyz:NVDA", date(2026, 10, 16), MARKS, ORACLE_PRINTS, LABEL_NVDA)
    assert row["ref_kind"] == "oracle_us"
    assert row["p_open_pre"] == ORACLE_AT_SUN_2005 and row["p_open_reg"] == ORACLE_AT_MON_0930
```

Also assert the flow's SQL no longer selects from `stock_perp_refs`.

- [ ] **Step 2: Run, see it fail, implement, run `tests/test_weekends.py`, ruff, commit**

```bash
git commit -am "Stock perps: public weekend tier reads the venue oracle only"
```

---

### Task 6: Private oracle validation

**Files:**
- Create: `venue_arb/validation.py`, `venue_arb/flows/stock_perp_oracle_check.py`,
  `db/migrations/ch/009_stock_perp_oracle_check.sql`, `tests/test_validation.py`
- Modify: `venue_arb/flows/serve.py` (deployment `stock_perp_oracle_check`, cron `30 1 * * 2-6`, after the US
  post-market close)

**Interfaces:**
- Produces:
  - Table `venue_arb.stock_perp_oracle_check (day Date, symbol LowCardinality(String), n UInt32, median_abs_bps
    Float64, p95_abs_bps Float64, share_over_10 Float64, open_gap_bps Nullable(Float64)) ENGINE = ReplacingMergeTree
    ORDER BY (symbol, day)`.
  - `compare(ctx_rows, ref_rows) -> list[dict]` (pure): per (symbol, ET day), ticks where a fresh two-sided Schwab
    quote exists (`weekends.fresh_quote` moved here as `fresh`) and the US session is external; `|oracle / mid − 1| ×
    1e4` → n, median, p95, share over 10 bps. `open_gap_bps` on Mondays: the oracle's Monday 09:30 print against the
    first fresh Schwab mid at or after 09:30.
  - `drift_results(checks, rows_5d) -> list[Result]`: warn `oracle_drift` per symbol whose trailing 5-day p95 > 10.
  - The flow writes the table, records the warns under its own pipeline, and writes
    `data/private/oracle-check-YYYY-MM-DD.md` (git-ignored) listing every name with its figures, worst first.
  - Nothing in this task is read by `publish.py` (test asserts the table name does not appear in `publish.py` or
    `bundle.py`).

- [ ] **Step 1: Write the failing tests** (`test_compare_skips_stale_and_one_sided_quotes`,
  `test_compare_percentiles`, `test_monday_open_gap`, `test_drift_warn_threshold`,
  `test_publisher_never_reads_the_check_table`).

- [ ] **Step 2: Run, see them fail, implement, run, ruff, commit**

```bash
git commit -am "Stock perps: private oracle-vs-Schwab validation (table, daily flow, report, drift warn)"
```

---

### Task 7: Dashboard, copy, samples

**Files:**
- Modify: `dashboard/static/{common.js,scanner.js,explorer.js,research.js}`, `venue_arb/report/copy.py`,
  `dashboard/sample/make_sample.py`, `tests/test_dashboard_static.py`, `tests/test_sample.py`
- Regenerate: `dashboard/sample/live.json`, `dashboard/sample/live-stock.json`, `dashboard/sample/history/*.parquet`

**Interfaces:**
- Consumes: Task 4 bundle fields.
- Produces:
  - `VERDICT_TONES` gains `["Oracle basis · within perp costs", "none"]`, `["Oracle internal · market closed",
    "stale"]`, and `["Oracle basis", "exec"]` placed after the within-costs entry so the more specific prefix wins;
    stock rows never use the `EXEC` prefix, so the Scanner's example-row flag ignores them.
  - Reference cell: `"oracle, live"` + age, or `"oracle internal"`. The Explorer cost table lists gross basis,
    2× perp taker, perp spread (exit), funding over 24 h, net "before stock-leg costs", perp depth, and the line
    "The stock leg is not modelled on this page." No "Schwab" anywhere.
  - `FOOT_STOCK = "{n} stock perps — perp against the venue's oracle, net of 2× perp taker fee, perp spread and 24 h funding, before stock-leg costs"`.
  - `copy.METHOD["stock_perps"]` rewritten (one paragraph, at most six sentences): the venue's oracle as the
    reference and when it is externally priced, the perp-side costs, that the stock leg is not modelled, the weekend
    test on oracle prints, Hyperliquid blocks US traders. No "Schwab".
  - Samples: `live-stock.json` stock rows rebuilt through the new `stock_perp_rows` (an oracle-basis row, a
    within-costs row, an internal row, a stale row).

- [ ] **Step 1: Extend the static and sample tests**: the three new copy strings; `test_no_schwab_on_the_page`
  (`"schwab"` absent, case-insensitive, from every `dashboard/static` text file, both sample JSON files, and
  `copy.METHOD["stock_perps"]`); `test_sample_stock_covers_four_states`.
- [ ] **Step 2: Run, see them fail, implement, regenerate the samples, walk the page in the browser on
  `live-stock.json` (Scanner chip, Explorer stock row, Research cards; light, dark, 375 px), run the full suite,
  commit**

```bash
git add dashboard venue_arb/report/copy.py tests
git commit -m "Stock perps: page shows the oracle view only; copy and samples without Schwab"
```

---

### Task 8: Docs and the terms record

**Files:**
- Modify: `docs/venues.md` (new dated subsections 2026-10-23: Schwab agreement clauses, Hyperliquid and trade.xyz
  terms, Pyth plans and site terms, Polymarket terms pointer), `docs/going-public.md` (the rule in Global
  Constraints; the manual exclusion process; the Polymarket decision as an open item), `README.md` (public oracle
  view, private validation flow and report path, the new deployment), the Plan 6 spec's §8 (pointer to the Plan 7
  spec)
- Test: `tests/test_deploy.py::test_readme_mentions_oracle_check` (README names `stock_perp_oracle_check` and
  `data/private/`)

- [ ] **Step 1: Write the docs from the spec's §7 and the code; add the test; run the full suite; commit**

```bash
git commit -am "Stock perps: terms record, public/private split and validation runbook"
```

## Deploy (after the whole-branch review; Sam approves the merge)

Same as Plan 6's runbook, with these differences: apply `ch/008` (as edited) and `ch/009` together on first deploy;
restart `venue-arb-flows` to pick up the oracle-check deployment; after a week, read
`data/private/oracle-check-*.md` before deciding to turn `DASHBOARD_SHOW_STOCK_PERPS` on; update the VM server code
before any publisher restart (Plan 6 lesson: run provision.sh's import check on the new tree first).
