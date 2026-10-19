# venue-arb Plan 2: exact-match scoring and research note v1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether exact Kalshi↔Polymarket matches carry arbitrage net of fees and depth, in two clearly
separated tiers (screened from history, executable from our own order-book snapshots), and generate research note v1.

**Architecture:** Pure modules decide (`fees`, `exact`, `edges`, `episodes`, `screened`); flows fetch, guard, load,
check and record through the platform `qa` layer, as in Plan 1. Executable edges per aligned snapshot go to ClickHouse;
opportunity episodes go to Postgres. A report module builds the note from both tables.

**Tech Stack:** as Plan 1 (Python 3.14, uv, Prefect 3.8.7, psycopg, clickhouse-connect, pytest, ruff) plus matplotlib
for the note's charts.

**Spec:** `docs/superpowers/specs/2026-10-19-venue-arb-design.md` (§4 Scoring, §7 phase 3). Plan 1:
`docs/superpowers/plans/2026-10-19-venue-arb-p1-catalog-matching.md`.

**Scope facts that shape this plan (checked 2026-10-19):**
- Exact pairs exist today only in fed_macro. Kalshi and Polymarket crypto price markets settle on different sources
  and times (CF Benchmarks RTI at 2pm/5pm ET vs Binance BTC/USDT at noon ET), so every crypto pair is a near-match.
  Structured crypto matching and near-match pricing belong to Plan 3.
- Polymarket International rejects orders from the US, so the US-executable cut of Kalshi × Polymarket International
  is empty by construction. The note says so. Polymarket US arrives in Plan 3.
- Research needs reviewed matches: run Sam's first review-CLI sitting before Task 3's live check.

## Global Constraints

- Everything in Plan 1's Global Constraints still holds (platform hard rules and NEW_PROJECT.md, `qa.db` connections
  passed in, pure modules never import `qa`, read-only public endpoints, no VPN, $20/month LLM cap, no paid data,
  commits without a Claude co-author trailer, CI = `uv run ruff check .` + `env -u PYTHONPATH uv run pytest -m "not integration" -q`).
- Scoring uses executable prices from order books at a stated size, never mid or last trade (spec §4). History-based
  numbers are the **screened** tier and are never called arbitrage or mixed into one number with the executable tier.
- Opportunity threshold (spec §4, tunable): net edge ≥ $0.01 per $1 contract, ≥ $100 executable, in ≥ 2 consecutive
  snapshots.
- Exact matches eligible for scoring: latest adjudication at the listings' current versions has `relation = 'exact'`,
  and either `confidence ≥ 0.8` with no review, or a review labeled `correct`. Any `wrong` or `unsure` review excludes the pair.
- Headline perspective: arbitrage that someone, somewhere could execute. A per-jurisdiction cut (US first) is reported
  alongside it.
- Fees are dated and sourced in `docs/venues.md`; code reads them from venue fields where venues expose them.

## Review Focus

1. **An empty book side at a snapshot** (no Yes asks on one venue): that direction has no executable edge (capacity 0),
   never a price of 0. Test in Task 4 (`test_edge_empty_side_has_no_capacity`).
2. **`yes_maps_to = 'no'`** (Kalshi Yes pays when Polymarket's token-0 leg loses): the legs swap. Test in Task 4
   (`test_directions_swap_when_yes_maps_to_no`).
3. **Unaligned snapshots** (one venue's fetch missing for a tick, or > 30 s apart): no edge row for that tick, and an
   episode breaks across the gap. Tests in Task 5 (`test_align_drops_far_pairs`, `test_episode_breaks_on_gap`).
4. **A pair whose review flips to `wrong` after scoring**: it disappears from the eligible set and from the next note.
   Test in Task 3 (`test_wrong_review_excludes`).
5. **Kalshi fee rounding at small size** (1 contract at P = 0.5 → $0.02, not $0.0175): fees use the venue's rounding,
   which can eat a 1¢ edge. Test in Task 2 (`test_kalshi_fee_rounds_up_per_order`).

---

### Task 1: Plan 1 follow-ups

**Files:**
- Modify: `venue_arb/flows/review_cli.py`, `venue_arb/matching/review.py`, `venue_arb/matching/adjudicate.py`, `venue_arb/matching/candidates.py`
- Test: `tests/test_review.py`, `tests/test_adjudicate.py`, `tests/test_candidates.py`

**Interfaces:**
- Produces `review.trust(labels: list[str]) -> tuple[float, int, bool]` returning `(precision, n_used, passed)`, with
  `passed = precision >= 0.95 and n_used >= 150`. The CLI report computes it over **all** reviews joined to current,
  non-`different` adjudications (SQL in `review_cli.py`), not only labels inside the redrawn sample.
- `adjudicate.TRANSIENT_ERRORS = frozenset({"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "OverloadedError"})`.
  The 3-error skip counts only `llm_attempts` rows whose `error_type` is not in that set.
- `candidates.rule_pairs` matches the month name with a word boundary (`\b<Month>\b`, case-insensitive).

- [ ] **Step 1: Write failing tests:** `test_trust_pass_and_threshold` (`["correct"]*150` gives `(1.0, 150, True)`;
  `["correct"]*149` gives `passed False`; `["correct"]*19+["wrong"]` gives `(0.95, 20, False)`),
  `test_transient_errors_do_not_cap_pair` (3 prior `RateLimitError` attempts mean the pair is still adjudicated; 3 prior
  `BilledError` attempts mean it's skipped), `test_rule_pairs_month_word_boundary` ("Will the Fed cut in May 2026?"
  pairs with `...-26MAY`; "Maybe a Fed cut in 2026?" doesn't). Run: FAIL.
- [ ] **Step 2: Implement.** Run all three test files: PASS. Commit: `git commit -m "Follow-ups: trust over all reviews, transient errors don't cap pairs, month word boundary"`.

### Task 2: Fee model

**Files:**
- Create: `venue_arb/fees.py`
- Modify: `venue_arb/sources/kalshi.py` (add `fetch_series`), `docs/venues.md` (dated fee entries)
- Test: `tests/test_fees.py`

**Interfaces:**
- `kalshi.fetch_series(ticker: str, get=http.get_json) -> dict` (`GET /series/{ticker}`, returns `payload["series"]`;
  the dict has `fee_type` and `fee_multiplier`).
- `fees.kalshi_taker_fee(price: float, contracts: float, fee_type: str, multiplier: float) -> float`. For `fee_type` in
  `{"quadratic", "quadratic_with_maker_fees"}`: `ceil(100 * 0.07 * multiplier * contracts * price * (1 - price)) / 100`,
  rounded up to the cent per order. Any other `fee_type` raises `ValueError` naming it; never guess.
- `fees.polymarket_taker_fee(price: float, contracts: float, schedule: dict | None, fees_enabled: bool) -> float`:
  `0.0` if not `fees_enabled` or no schedule, else `contracts * schedule["rate"] * (price * (1 - price)) ** schedule["exponent"]`.
  The schedule is taken from the market's `feeSchedule` in `listings.raw["market"]`. Rounding is unverified in the
  docs, so no rounding (`# ponytail:` comment naming it).
- `fees.TRANSFER = {"fixed_usd": 10.0, "conversion_bps": 10.0}`: conservative cost of moving capital between venues
  per round trip (deposit/withdraw plus USD↔USDC). It's an assumption, dated, and reported as a sensitivity column.
  `fees.transfer_cost(notional_usd: float) -> float = fixed_usd + notional_usd * conversion_bps / 1e4`.
- `fees.US_EXECUTABLE = {"kalshi": True, "polymarket": False}` (source: docs/venues.md geoblock entry).

- [ ] **Step 1: Verify Kalshi's formula** against Kalshi's own pages: the fee schedule PDF (it returned 429 on
  2026-10-18/19, so retry) or `help.kalshi.com` fees articles. Record the quote, URL and date in `docs/venues.md`.
  If the coefficient differs from 0.07, use the verified one and note it. Also record Polymarket's `feeSchedule` field
  (observed 2026-10-19 on Fed markets: `{"rate": 0.05, "exponent": 1, "takerOnly": true}`) and the `TRANSFER` assumption.
- [ ] **Step 2: Write failing tests:** `test_kalshi_fee_rounds_up_per_order` (`kalshi_taker_fee(0.5, 1, "quadratic", 1) == 0.02`;
  `(0.5, 100, "quadratic", 1) == 1.75`), `test_kalshi_unknown_fee_type_raises`, `test_polymarket_fee`
  (`polymarket_taker_fee(0.5, 100, {"rate": 0.05, "exponent": 1}, True) == pytest.approx(1.25)`; disabled gives 0),
  `test_transfer_cost` (`transfer_cost(1000) == 11.0`). Run: FAIL.
- [ ] **Step 3: Implement.** Run: PASS. Commit: `git commit -m "Fee model: Kalshi series fees, Polymarket market fee schedule, transfer assumption"`.

### Task 3: Exact-match set

**Files:**
- Create: `venue_arb/exact.py` (pure), `venue_arb/flows/exact_sql.py` (the query)
- Test: `tests/test_exact.py`

**Interfaces:**
- `@dataclass(frozen=True) class ExactPair: kalshi_id: str; pm_id: str; pm_yes_token: str; yes_maps_to: Literal["yes","no"]; close_time: datetime; kalshi_series: str; pm_fee_schedule: dict | None; pm_fees_enabled: bool; confidence: float; reviewed: bool`
- `exact.eligible(rows: list[dict]) -> list[ExactPair]`. Rows come from `exact_sql.ELIGIBLE_SQL`: current listings
  (`last_seen` within 2 h) joined to their latest adjudication at current versions, plus any review at those versions.
  Applies the Global Constraints rule; `close_time` = the later of the two legs.
- `exact_sql.load_eligible(conn) -> list[dict]` runs the SQL (parameterized; reads `raw->'event'->>'series_ticker'`
  for Kalshi and `raw->'market'->'feeSchedule'` and `raw->'market'->>'feesEnabled'` for Polymarket).

- [ ] **Step 1: Write failing tests** (hand-built rows): `test_exact_unreviewed_high_confidence_included` (0.85, no
  review → included), `test_exact_low_confidence_needs_review` (0.7 without review → excluded; with `correct` → included),
  `test_wrong_review_excludes` (0.99 and `wrong` → excluded; `unsure` → excluded), `test_near_excluded`. Run: FAIL.
- [ ] **Step 2: Implement** both files. Run: PASS.
- [ ] **Step 3: Live check** (after Sam's first review sitting): `secrets uv run python -c "from qa import db; from venue_arb import exact; from venue_arb.flows import exact_sql; c=db.pg(); print(len(exact.eligible(exact_sql.load_eligible(c))))"` prints a non-zero count. Commit:
  `git commit -m "Exact-match set: eligibility from adjudications and reviews"`.

### Task 4: Executable edge math

**Files:**
- Create: `venue_arb/edges.py` (pure)
- Test: `tests/test_edges.py`

**Interfaces:**
- Consumes `Level` (Plan 1 `models.py`; ask side, best first), `fees`, `ExactPair`.
- `Direction = Literal["k_yes_pm_no", "k_no_pm_yes"]`: buy Kalshi Yes + buy the Polymarket leg that pays when Kalshi
  Yes loses, or the reverse. With `yes_maps_to = 'yes'`, "the Polymarket leg that pays when Kalshi Yes loses" is
  Polymarket `no`; with `'no'` it is Polymarket `yes`.
- `@dataclass(frozen=True) class Edge: direction: Direction; best_edge: float; capacity_contracts: float; capacity_usd: float; avg_edge: float`
  — `best_edge` is the net edge per contract on the first contract; `capacity_*` is the largest size whose **marginal**
  net edge per contract stays ≥ `min_edge`; `avg_edge` is the mean net edge per contract over that size; `capacity_usd` is
  the money spent on both legs for that size.
- `edges.walk(k_asks: list[Level], pm_asks: list[Level], fee_k, fee_pm, min_edge: float = 0.01) -> tuple[float, float, float, float]`:
  walks both ask ladders together, one price level at a time (size = min remaining at the two current levels), and
  computes the marginal net edge per contract as `1 − pk − ppm − (fee_k(pk, q) + fee_pm(ppm, q)) / q` for that slice.
  It stops when the marginal edge < `min_edge` or either ladder ends. Returns `(best_edge, capacity_contracts, capacity_usd, avg_edge)`.
  Fees are per slice (Kalshi rounding per slice is conservative — note it).
- `edges.score(pair: ExactPair, k_levels: list[Level], pm_levels: list[Level], series_fee: dict, min_edge: float = 0.01) -> list[Edge]`:
  both directions, picking each leg's side from `pair.yes_maps_to`.

- [ ] **Step 1: Write failing tests:**
  - `test_walk_single_level`: Kalshi Yes ask 0.40 × 500, Polymarket No ask 0.55 × 300, zero fees → `best_edge == 0.05`,
    `capacity_contracts == 300`, `capacity_usd == pytest.approx(285.0)`.
  - `test_walk_stops_at_min_edge`: a second Polymarket level at 0.595 (marginal edge 0.005 < 0.01) isn't included.
  - `test_edge_empty_side_has_no_capacity`: empty Polymarket ladder → `capacity_contracts == 0`, `best_edge` is `None`
    (no price, not 0 or 1).
  - `test_directions_swap_when_yes_maps_to_no`: with `yes_maps_to='no'`, direction `k_yes_pm_no` uses Polymarket **yes** asks.
  - `test_fees_reduce_edge`: Kalshi fee at 0.40 × 100 = $1.68 and Polymarket 5% schedule at 0.55 × 100 = $1.2375
    turn a 0.05 gross edge into `0.05 − 0.029175` per contract (approx).
  Run: FAIL.
- [ ] **Step 2: Implement.** Run: PASS. Commit: `git commit -m "Edge math: walk both books, marginal net edge, capacity"`.

### Task 5: Snapshot alignment and episodes

**Files:**
- Create: `venue_arb/episodes.py` (pure)
- Test: `tests/test_episodes.py`

**Interfaces:**
- `ALIGN_S = 30`, `MAX_GAP_S = 150` (a missing tick at the 60 s cadence breaks an episode).
- `episodes.align(k_ts: list[datetime], pm_ts: list[datetime], tol_s: int = ALIGN_S) -> list[tuple[datetime, datetime]]`:
  for each Kalshi snapshot time, the nearest Polymarket snapshot within `tol_s`. Each Polymarket time is used at most once.
- `@dataclass(frozen=True) class Episode: kalshi_id: str; pm_id: str; direction: str; start: datetime; end: datetime; snapshots: int; max_edge: float; median_capacity_usd: float; annualized: float`
- `episodes.find(points: list[tuple[datetime, Edge]], close_time: datetime, min_capacity_usd: float = 100.0, min_snapshots: int = 2, max_gap_s: int = MAX_GAP_S) -> list[Episode]`:
  maximal runs where `avg_edge ≥ 0.01` and `capacity_usd ≥ min_capacity_usd`, consecutive points ≤ `max_gap_s` apart,
  keeping runs with ≥ `min_snapshots`. `annualized = (avg_edge / cost_per_contract) * 365 / max(days_to_close, 1/24)`,
  using the run's median values, where `cost_per_contract = capacity_usd / capacity_contracts`.

- [ ] **Step 1: Write failing tests:** `test_align_nearest_within_tolerance`, `test_align_drops_far_pairs` (40 s apart
  → nothing), `test_episode_needs_two_snapshots`, `test_episode_breaks_on_gap` (points at t, t+60, t+300 → one run of 2
  plus a lone point that doesn't count), `test_episode_capacity_floor` ($99 capacity doesn't qualify),
  `test_annualized` (edge 0.02 on cost 0.98, 10 days to close → ≈ 0.745). Run: FAIL.
- [ ] **Step 2: Implement.** Run: PASS. Commit: `git commit -m "Alignment and opportunity episodes"`.

### Task 6: Scoring flow

**Files:**
- Create: `db/migrations/ch/003_edges.sql`, `db/migrations/pg/003_opportunities.sql`, `venue_arb/flows/score.py`,
  `sql_checks/edges_capacity_consistent.sql`, `sql_checks/opportunities_valid.sql`
- Modify: `venue_arb/flows/serve.py` (add `score_flow` at cron `"40 * * * *"`, `concurrency_limit=1`), `README.md`
- Test: `tests/test_score_flow.py` (integration)

**Interfaces:**
- ClickHouse `venue_arb.edges(ts DateTime64(3,'UTC'), kalshi_id String, pm_id String, direction LowCardinality(String), best_edge Nullable(Float64), capacity_contracts Float64, capacity_usd Float64, avg_edge Nullable(Float64), fee_version String) ENGINE = ReplacingMergeTree PARTITION BY toYYYYMM(ts) ORDER BY (kalshi_id, pm_id, direction, ts)`.
  `ts` is the Kalshi snapshot time. `fee_version` is the docs/venues.md fee-entry date, so a fee change is visible.
- Postgres `venue_arb.opportunities(kalshi_id text, pm_id text, direction text, start_ts timestamptz, end_ts timestamptz, snapshots int, max_edge real, median_capacity_usd real, annualized real, us_executable boolean, transfer_cost_usd real, scored_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (kalshi_id, pm_id, direction, start_ts))`.
  `transfer_cost_usd = fees.transfer_cost(median_capacity_usd)`; `us_executable = US_EXECUTABLE[k] and US_EXECUTABLE[pm]`.
- `score.score_flow(window_hours: int = 26) -> dict` (pipeline `venue_arb_score`): load eligible exact pairs → fetch
  Kalshi series fees once per series → read `book_levels_v2` for those markets over the window → align → `edges.score`
  → guards (`expect_schema`; `expect_rows ≥ 1` when eligible pairs exist) → insert `edges` → `episodes.find` per pair and
  direction → upsert `opportunities` (`ON CONFLICT DO UPDATE`) → `qa.sql.run_all(pattern="edges_*.sql", …)` and
  `"opportunities_*.sql"` → `store.record`. Returns `{"pairs": n, "edge_rows": n, "episodes": n}`. The window overlaps
  the previous run, so re-scoring is idempotent.
- SQL checks (error): `edges_capacity_consistent.sql` returns rows where `capacity_contracts > 0 AND (avg_edge IS NULL OR avg_edge < 0.01)`
  in the last 2 h; `opportunities_valid.sql` returns rows with `end_ts < start_ts` or `snapshots < 2`.

- [ ] **Step 1: Write `test_score_flow_idempotent`** (integration): run `score_flow(window_hours=2)` twice; the
  `edges` row count from `FINAL` and the `opportunities` count are equal after both runs, and both runs pass all checks.
  Run: FAIL.
- [ ] **Step 2: Implement** migrations, flow, checks, and the serve.py schedule. Apply migrations. Run: PASS.
- [ ] **Step 3: Commit:** `git commit -m "Scoring flow: edges per aligned snapshot, opportunity episodes, schedule"`.

### Task 7: Screened tier from history

**Files:**
- Create: `venue_arb/screened.py` (pure), `venue_arb/flows/screened.py`, `db/migrations/ch/004_screened.sql`
- Modify: `venue_arb/sources/kalshi.py` (`fetch_candles`), `venue_arb/sources/polymarket.py` (`fetch_price_history`)
- Test: `tests/test_screened.py`

**Interfaces:**
- `kalshi.fetch_candles(series: str, ticker: str, start_ts: int, end_ts: int, get=http.get_json) -> list[dict]`: hourly
  (`period_interval=60`), chunked to ≤ 60 days per call. On 404 it falls back to
  `/historical/markets/{ticker}/candlesticks`, where the fields are `price.close` / `volume` instead of `close_dollars` /
  `volume_fp`. Returns `[{"ts": datetime, "yes_bid": float | None, "yes_ask": float | None}]` from the `yes_bid.close_dollars` /
  `yes_ask.close_dollars` fields.
- `polymarket.fetch_price_history(token_id: str, start_ts: int, end_ts: int, get=http.get_json) -> list[dict]`:
  `GET {CLOB}/prices-history?market=<token>&startTs=…&endTs=…&fidelity=60` → `[{"ts": datetime, "price": float}]`.
- ClickHouse `venue_arb.screened(ts DateTime('UTC'), kalshi_id String, pm_id String, direction LowCardinality(String), k_price Float64, pm_price Float64, gross_gap Float64, net_gap_top Float64) ENGINE = ReplacingMergeTree ORDER BY (kalshi_id, pm_id, direction, ts)`.
- `screened.gaps(pair: ExactPair, k: list[dict], pm: list[dict], series_fee: dict) -> list[dict]`: hourly join on the
  truncated hour. For `k_yes_pm_no`: `k_price = yes_ask`, `pm_price = 1 − pm_yes_price` if `yes_maps_to = 'yes'` (else
  `pm_yes_price`). For `k_no_pm_yes`: `k_price = 1 − yes_bid`, the other Polymarket leg. `gross_gap = 1 − k_price − pm_price`.
  `net_gap_top` subtracts both venues' fees for 1 contract at those prices. Polymarket's history is a traded/mid price,
  not an ask, so every row is screened-tier only.
- `flows/screened.screened_flow() -> dict` (pipeline `venue_arb_screened`, run by hand, not scheduled): for each
  eligible pair, history from the later leg's creation (`listings.first_seen`, or the API's `open_time` /
  `startDate` if earlier) to now → guards → insert → `store.record`.

- [ ] **Step 1: Save fixtures** (one Kalshi candle page, one historical-endpoint page, one Polymarket price history).
  Write failing tests: `test_kalshi_candles_parse_and_historical_fallback`, `test_pm_history_parse`,
  `test_gaps_directions_and_mapping` (with `yes_maps_to='no'` the Polymarket leg flips), `test_gaps_skip_missing_hours`
  (an hour missing on either side gives no row). Run: FAIL.
- [ ] **Step 2: Implement** sources, the pure module, migration and flow. Apply the migration. Run unit tests: PASS.
  Run `secrets uv run python -m venue_arb.flows.screened` once; the run is recorded passing in `qa.check_results`.
- [ ] **Step 3: Commit:** `git commit -m "Screened tier: hourly history gaps for exact pairs"`.

### Task 8: Research note v1

**Files:**
- Create: `venue_arb/report/__init__.py`, `venue_arb/report/note.py`, `reports/.gitkeep`
- Modify: `pyproject.toml` (add `matplotlib`), `README.md`
- Test: `tests/test_note.py`

**Interfaces:**
- `note.summarize(pairs: list[ExactPair], screened_rows: list[dict], edge_rows: list[dict], episodes: list[dict]) -> dict` (pure):
  pair counts (eligible, reviewed vs unreviewed), screened stats (hours observed; share of hours with `net_gap_top > 0`
  and ≥ 0.01; p50/p90/max `net_gap_top`), executable stats (aligned snapshots; share with capacity > 0; episode count;
  median/max duration in minutes; median/max capacity; median annualized; the same episode counts net of
  `transfer_cost_usd`), and the jurisdiction cut (US-executable episode count, with the reason it's 0 while Polymarket
  International is the only Polymarket venue).
- `note.render(summary: dict, out_dir: Path) -> Path` writes `reports/research-note-v1.md` plus two PNGs: the screened
  `net_gap_top` distribution, and executable capacity against edge for episode snapshots. Read the `dataviz` skill
  before drawing them. The note's fixed sections: Question; Data and window (venues, dates, cadence, pair counts);
  Method (eligibility, the screened vs executable tiers, fee sources with dates, alignment tolerance, threshold, transfer
  assumption); Results (screened, executable, jurisdiction); Limits (60 s sampling misses sub-minute gaps; secondhand
  fee items marked; Polymarket history isn't an ask; few exact pairs, Fed only; no execution latency or fill risk);
  Next (Plan 3: Polymarket US, crypto near-matches, perps).
- `python -m venue_arb.report.note` loads from the DB (flow layer, may import `qa`) and calls both.

- [ ] **Step 1: Write failing tests:** `test_summarize_tiers_never_mixed` (screened and executable stats live under
  separate keys, and no field combines them), `test_summarize_jurisdiction_zero_with_reason`,
  `test_summarize_empty_inputs` (no pairs gives zeros and an explicit "no eligible pairs" note, not an exception). Run: FAIL.
- [ ] **Step 2: Implement.** Run: PASS. Generate the note once from live data and read it start to finish: every number
  traces to a table, the tiers are labeled, and the limits are stated.
- [ ] **Step 3: Commit:** `git commit -m "Research note v1 generator"`. The note becomes v1-final after about 4 weeks of
  snapshots. Re-run then; it's a manual step for Sam to trigger.
