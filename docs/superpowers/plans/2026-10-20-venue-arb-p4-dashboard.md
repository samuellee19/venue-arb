# venue-arb Plan 4: live pair-explorer dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public, always-on page that shows every matched Kalshi/Polymarket pair with its net-of-fees edge, both
books, a verdict with reasons and a Plotly history, refreshed every minute, served from a free Oracle VM fed by
outbound pushes from the OptiPlex.

**Architecture:** A new 60 s loop on the OptiPlex (`venue_arb/publish.py`) reads the latest books, exact pairs and
ladders, scores them with the existing walk, assembles `live.json` plus an hourly history Parquet, and rsyncs both to
a write-only directory on the server. The server runs one FastAPI process that serves static HTML/JS (Plotly), the
bundle, DuckDB queries over the Parquet, and a fill-at-size endpoint that reuses `venue_arb.edges`. No inbound path
to the home network; the server has no database.

**Tech Stack:** Python 3.14, pydantic (bundle schema), pyarrow (Parquet), FastAPI + uvicorn + DuckDB (server,
optional extra), vanilla JS + vendored plotly.js `basic` bundle, Caddy, systemd, rsync/rrsync.

**Spec:** `docs/superpowers/specs/2026-10-20-venue-arb-dashboard-design.md` (§10 holds the binding calls).

## Global Constraints
- Plans 1–3's constraints hold: platform hard rules, qa on every load, pure modules never import `qa`, read-only
  public venue endpoints, no order/account calls, no VPN, no paid data, no LLM calls, commits without a
  Co-Authored-By trailer, CI = `uv run ruff check .` + `env -u PYTHONPATH uv run pytest -m "not integration" -q`.
- **No inbound connection to the home network.** The OptiPlex pushes; the server never connects back.
- **Never republish raw CF Benchmarks or Binance series.** Only `basis.bounds` outputs and summary numbers.
- Units: prices and per-contract edge in **¢**; anything times a size in **$**; bps = net edge ÷ all-in cost.
  "Net edge" = `edges.walk`'s first-slice `best_edge` (spec §10.1). Transfer cost is a separate line (§10.4).
- Supply-chain rule: Python deps obey `exclude-newer = 2026-10-05`; the vendored plotly.js release must predate
  2026-10-05 and its version + sha256 are recorded in `dashboard/static/VENDOR.md`.
- The server installs `venue_arb` **without** its pipeline deps (no prefect, anthropic, fastembed): editable install
  with `--no-deps` plus the `dashboard` extra. `venue_arb.edges`, `fees`, `models`, `exact`, `bundle` must import
  with only those installed (Task 7 test).
- Migrations additive; the publisher only reads.
- Times in every file and API are ISO-8601 UTC with offset; countdowns are computed client-side.

## Review Focus
1. **One leg stale or missing.** A pair whose Kalshi or Polymarket latest snapshot is older than 5 min, or absent, is
   not "live": no edge is computed from mismatched times; it is listed with `live: false`. Test in Task 3
   (`test_pair_not_live_when_leg_stale`).
2. **Empty or one-sided books.** `walk` returns `best_edge None`; the bundle row shows `net_edge_cents: null`, the
   verdict "no asks on one side", never a crash. Test in Task 3 (`test_pair_with_empty_book`).
3. **Size beyond visible depth.** `fill` returns `filled < requested` and `insufficient_depth: true`, never
   extrapolates. Test in Task 2 (`test_fill_insufficient_depth`).
4. **Half-written bundle.** The publisher writes `live.json` atomically (temp + rename); the server keeps the last
   good bundle when the file fails to parse. Tests in Task 4 (`test_write_atomic`) and Task 7
   (`test_store_keeps_last_good_bundle`).
5. **Unknown or odd pair key in a URL.** `/api/pair/{key}/...` with an unknown key is 404, keys contain `~`, `.`,
   `-`, `0x…` and round-trip through URL encoding. Test in Task 7 (`test_pair_key_roundtrip_and_404`).

---

### Task 1: Venue display terms and the Kalshi fee source (gate)

**Files:** Modify `docs/venues.md` (both "Public data terms" sections, Kalshi fee section); Modify
`venue_arb/config.py` (add `DASHBOARD_SHOW_BOOKS: bool`); Modify `venue_arb/fees.py:7` (comment only, if verified).

**Interfaces:**
- Produces: `config.DASHBOARD_SHOW_BOOKS` — `True` if both venues' terms allow displaying order-book quotes on a
  public page, else `False`. Tasks 3 and 8 branch on it.

- [ ] **Step 1: Read the Kalshi Developer Agreement** (linked from <https://docs.kalshi.com/welcome>; fetch the
  agreement itself, not the welcome page) and Polymarket's Terms of Use (<https://polymarket.com/tos>; if the page
  body still doesn't render, try the CLOB/API docs' terms link and the GitHub `polymarket/clob-client` README). For
  each, find the clauses on displaying, redistributing or republishing market data. Quote them verbatim (≤ 2 short
  quotes each) with URL and fetch date under each venue's "Public data terms" heading, replacing "Unverified".
- [ ] **Step 2: Decide and record.** Allowed on both → `DASHBOARD_SHOW_BOOKS = True`. Forbidden or ambiguous on
  either → `False`, and write one line under the heading saying which venue and why, plus "Sam to review". Attribution
  text required by either venue goes verbatim into the same section (Task 8 copies it into the footer).
- [ ] **Step 3: Verify the Kalshi fee coefficient.** Fetch Kalshi's published fee schedule (docs.kalshi.com, "fees"
  or "fee schedule" page). If it states the taker fee formula, record the quote + URL in `docs/venues.md` and change
  the `fees.py:7` comment from "secondhand, unverified" to "verified <date>, <url>". If not found, leave the code and
  add "still unverified <date>" to the doc; Task 8 then shows the caveat line.
- [ ] **Step 4: Run CI** — `uv run ruff check .` passes; `pytest -m "not integration" -q` passes (no code change
  besides the constant).
- [ ] **Step 5: Commit** — `git add docs/venues.md venue_arb/config.py venue_arb/fees.py && git commit -m "Dashboard
  gate: venue display terms and Kalshi fee source recorded"`.

---

### Task 2: Fill at size (`edges.fill`)

**Files:** Modify `venue_arb/edges.py`; Test `tests/test_edges.py`.

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Fill:
      requested: float          # contracts asked for
      filled: float             # contracts both ladders could supply, <= requested
      insufficient_depth: bool  # filled < requested
      k_avg_price: float | None # size-weighted average ask paid on the Kalshi leg (None if filled == 0)
      pm_avg_price: float | None
      fees_usd: float           # fee_k + fee_pm summed over the slices actually filled
      cost_usd: float           # filled * (k_avg + pm_avg) + fees_usd
      net_cents: float | None   # 100 * (filled - cost_usd) / filled, None if filled == 0
      net_bps: float | None     # 1e4 * (filled - cost_usd) / cost_usd, None if filled == 0

  def fill(k_asks: list[Level], pm_asks: list[Level], fee_k: Fee, fee_pm: Fee, contracts: float) -> Fill
  ```
  Walks both best-first ladders slice by slice exactly like `walk` (same slice fee treatment), but keeps going
  through negative slices until `contracts` are filled or a ladder runs out. `contracts <= 0` raises `ValueError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_edges.py`:
  ```python
  def test_fill_single_level():
      k = [Level("yes", 1, 0.41, 120)]; pm = [Level("no", 1, 0.58, 400)]
      f = fill(k, pm, lambda p, q: 0.017 * q, lambda p, q: 0.0, 100)
      assert f.filled == 100 and not f.insufficient_depth
      assert f.k_avg_price == 0.41 and f.pm_avg_price == 0.58
      assert f.fees_usd == pytest.approx(1.7) and f.cost_usd == pytest.approx(100.7)
      assert f.net_cents == pytest.approx(-0.7) and f.net_bps == pytest.approx(-0.7 / 100.7 * 1e4)

  def test_fill_walks_through_negative_slices():
      k = [Level("yes", 1, 0.41, 120), Level("yes", 2, 0.45, 300)]; pm = [Level("no", 1, 0.58, 400)]
      f = fill(k, pm, lambda p, q: 0.0, lambda p, q: 0.0, 400)
      assert f.filled == 400 and f.k_avg_price == pytest.approx((120 * 0.41 + 280 * 0.45) / 400)

  def test_fill_insufficient_depth():
      f = fill([Level("yes", 1, 0.41, 120)], [Level("no", 1, 0.58, 400)], lambda p, q: 0.0, lambda p, q: 0.0, 1000)
      assert f.filled == 120 and f.insufficient_depth and f.requested == 1000

  def test_fill_empty_ladder_and_bad_size():
      assert fill([], [Level("no", 1, 0.58, 400)], lambda p, q: 0.0, lambda p, q: 0.0, 10).filled == 0
      with pytest.raises(ValueError):
          fill([], [], lambda p, q: 0.0, lambda p, q: 0.0, 0)
  ```
- [ ] **Step 2: Run** `env -u PYTHONPATH uv run pytest tests/test_edges.py -q -k fill` — FAIL (ImportError `fill`).
- [ ] **Step 3: Implement `Fill` and `fill` in `venue_arb/edges.py`** below `walk`, sharing its slice loop shape.
- [ ] **Step 4: Run** the same command — PASS; run full CI.
- [ ] **Step 5: Commit** — `git commit -m "edges.fill: fill-at-size walk for the dashboard cost panel"`.

---

### Task 3: Bundle builder (pure)

**Files:** Create `venue_arb/bundle.py`; Test `tests/test_bundle.py`, fixtures `tests/fixtures/bundle/*.json`.

**Interfaces:**
- Consumes: `edges.score`, `edges.walk`, `ladder_edges.select_pairs`, `ladder_edges.score_spread`,
  `fees.transfer_cost`, `fees.US_EXECUTABLE`, `exact.ExactPair`, `crypto_rule.Strike`, `config.DASHBOARD_SHOW_BOOKS`,
  `config.BOOK_DEPTH`.
- Produces (pydantic v2 models, `extra="forbid"`; the JSON schema of `LiveBundle` **is** the `live.json` contract):
  ```python
  LIVE_MAX_AGE_S = 300          # a leg's latest snapshot older than this -> not live
  PAIR_KEY_SEP = "~"            # key = f"{k_id}~{pm_id}~{direction}"

  class Leg(BaseModel): venue: str; market_id: str; side: Literal["yes","no"]; title: str; url: str
                        settlement_source: str | None; snapshot_at: datetime | None
  class BookLevel(BaseModel): price: float; size: float
  class Check(BaseModel): name: Literal["same_instant","exact_match","basis_bound","fees_depth","jurisdiction"]
                          status: Literal["ok","fail","partial"]; detail: str
  class Verdict(BaseModel): label: str; checks: list[Check]   # fixed order: same_instant|exact_match, basis_bound, fees_depth, jurisdiction
  class Pair(BaseModel):
      key: str; kind: Literal["exact","near_exact","ladder"]; category: str; title: str; settles_at: datetime
      ladder: str | None            # "BTC~2026-10-20T16:00:00+00:00" for ladder/near_exact rows, else None
      legs: list[Leg]               # [kalshi, polymarket]
      direction: str; live: bool
      net_edge_cents: float | None; net_edge_bps: float | None; fees_cents: float | None
      capacity_contracts: float; capacity_usd: float
      books: list[list[BookLevel]] | None   # [k_asks, pm_asks] top BOOK_DEPTH, None when DASHBOARD_SHOW_BOOKS is False
      verdict: Verdict; us_executable: bool
      fee_params: dict                # {"k": {"fee_type", "fee_multiplier"}, "pm": {"schedule", "fees_enabled"}} for /api/walk
      spark: list[float | None]     # 24 hourly points, oldest first; None = no scored row that hour
  class Basis(BaseModel): up: float; down: float; window_days: int; asof: datetime
  class Episode(BaseModel): key: str; start: datetime; end: datetime | None; max_edge_cents: float
                            median_capacity_usd: float; transfer_cost_usd: float
  class Annotation(BaseModel): kind: Literal["settlement","listing","basis_change","collector_gap","threshold"]
                               at: datetime; until: datetime | None; key: str | None; label: str
  class Kpis(BaseModel): episodes_7d: int; best_edge: dict | None   # {"key","net_edge_cents","capacity_usd"}
                         pairs_live: dict                            # {"exact","near_exact","ladder"}
                         checks_window: list[datetime] | None; admissible_checks: int | None; violations: int | None
                         screened_share_ge_1c: float | None; screened_hours: int | None
  class LiveBundle(BaseModel): computed_at: datetime; collector_last_snapshot_at: datetime | None
                               show_books: bool; basis: dict[str, Basis]; kpis: Kpis
                               pairs: list[Pair]; episodes: list[Episode]; annotations: list[Annotation]
                               attribution: list[str]; caveats: list[str]

  def pair_key(k_id: str, pm_id: str, direction: str) -> str
  def market_url(venue: str, listing: dict) -> str     # Kalshi from ticker; Polymarket from raw event slug
  def exact_pair_rows(pair: ExactPair, titles: dict, k_book: Snap | None, pm_book: Snap | None,
                      series_fee: dict, sparks: dict, now: datetime) -> list[Pair]      # both directions
  def ladder_pair_rows(asset: str, instant: datetime, ladder: dict, books: dict, up: float, down: float,
                       k_fee, pm_fee, titles: dict, sparks: dict, now: datetime) -> list[Pair]
      # one row per (Kalshi strike, direction): the best-edge admissible PM strike from select_pairs' plan;
      # near-exact strikes as separate rows with kind "near_exact"
  def verdict(kind: str, direction: str, edge: Edge | None, *, a: float | None, b: float | None,
              up: float | None, down: float | None, us_executable: bool, both_live: bool) -> Verdict
  def build_live(*, now, exact_rows: list[Pair], ladder_rows: list[Pair], basis: dict[str, Basis],
                 episodes: list[Episode], annotations: list[Annotation], kpi_inputs: dict,
                 attribution: list[str], caveats: list[str], collector_last: datetime | None) -> LiveBundle
  Snap = tuple[datetime, list[Level]]   # (snapshot ts, levels of one market at that ts)
  ```
  Verdict labels (exact copy): `"Exact match · no edge"`, `"Executable edge · +{net:.1f}¢ · ${cap:,.0f} before
  transfer · ${after:,.0f} after"`, `"Not an arbitrage · basis exposure"`, `"Spread · within basis bound · no edge"`,
  `"Not executable from the US"` (only in the US view — the bundle carries `us_executable`; label chosen client-side
  per §10.6), `"No asks on one side"`, `"Stale · a leg has no snapshot in 5 min"`.
  Check details: basis_bound → `"needs PM − K ≥ ${down:,.0f} (99.9th pct, {window_days} d); gap ${gap:,.0f}"`
  (direction-appropriate); fees_depth → `"net {net:+.1f}¢ at the first {q:,.0f} contracts; fees {fee:.1f}¢"`;
  jurisdiction → `"anywhere: both legs open · US: Polymarket rejects US orders"`.

- [ ] **Step 1: Write fixtures** `tests/fixtures/bundle/exact_pair.json` (one ExactPair with `kalshi_id`
  `KXFED-26DEC-H0`, `pm_id` `0xabc` + two books,
  Kalshi yes asks 41¢×120/42¢×340, PM no asks 58¢×400/59¢×210, Fed-style fee schedule rate 0.05 exp 1, series fee
  `quadratic_with_maker_fees` ×1) and `ladder.json` (a BTC ladder at `2026-10-20T16:00:00+00:00`: Kalshi strikes
  85999.99 and 87999.99, PM strikes 86000 and 88200, basis up 182 / down 152, books for all four).
- [ ] **Step 2: Write the failing tests** in `tests/test_bundle.py`:
  ```python
  def test_exact_pair_rows_both_directions_and_edge():
      rows = exact_pair_rows(PAIR, TITLES, K_BOOK, PM_BOOK, SERIES_FEE, {}, NOW)
      by = {r.direction: r for r in rows}
      assert set(by) == {"k_yes_pm_no", "k_no_pm_yes"} and by["k_yes_pm_no"].kind == "exact"
      r = by["k_yes_pm_no"]
      assert r.net_edge_cents == pytest.approx(100 * (1 - 0.41 - 0.58) - 1.7 - 0.0, abs=0.05)   # Kalshi fee 1.7¢
      assert r.net_edge_bps == pytest.approx(r.net_edge_cents / (100 - r.net_edge_cents) * 1e4, rel=1e-3)
      assert r.books[0][0].price == 0.41 and r.key == "KXFED-26DEC-H0~0xabc~k_yes_pm_no"
      assert r.verdict.label == "Exact match · no edge" and r.live

  def test_pair_not_live_when_leg_stale():
      stale = (NOW - timedelta(seconds=LIVE_MAX_AGE_S + 1), PM_BOOK[1])
      r = exact_pair_rows(PAIR, TITLES, K_BOOK, stale, SERIES_FEE, {}, NOW)[0]
      assert not r.live and r.net_edge_cents is None and r.verdict.label.startswith("Stale")

  def test_pair_with_empty_book():
      r = exact_pair_rows(PAIR, TITLES, K_BOOK, (NOW, []), SERIES_FEE, {}, NOW)[0]
      assert r.net_edge_cents is None and r.verdict.label == "No asks on one side"

  def test_ladder_rows_collapse_to_best_pm_strike_per_kalshi_strike_and_direction():
      rows = ladder_pair_rows("BTC", INSTANT, LADDER, BOOKS, 182, 152, K_FEE, PM_FEE, TITLES, {}, NOW)
      keys = {(r.legs[0].market_id, r.direction, r.kind) for r in rows}
      assert ("KXBTCD-26OCT2011-T85999.99", "k_yes_pm_no", "near_exact") in keys   # 85999.99 vs 86000
      assert ("KXBTCD-26OCT2011-T85999.99", "k_yes_pm_no", "ladder") in keys       # vs 88200 (gap >= 152)
      assert all(r.ladder == "BTC~2026-10-20T16:00:00+00:00" for r in rows)
      near = next(r for r in rows if r.kind == "near_exact")
      assert near.verdict.label == "Not an arbitrage · basis exposure"
      assert near.verdict.checks[1].detail == "needs PM − K ≥ $152 (99.9th pct, 30 d); gap $0"

  def test_build_live_kpis_and_schema_roundtrip():
      b = build_live(now=NOW, exact_rows=EX, ladder_rows=LAD, basis=BASIS, episodes=[], annotations=[],
                     kpi_inputs=KPI, attribution=["Kalshi", "Polymarket"], caveats=[], collector_last=NOW)
      assert b.kpis.pairs_live == {"exact": 2, "near_exact": 2, "ladder": 2}
      assert b.kpis.best_edge["key"] == max(b.pairs, key=lambda p: p.net_edge_cents or -9).key
      assert LiveBundle.model_validate_json(b.model_dump_json()) == b
      assert len(b.model_dump_json()) < 200_000

  def test_market_urls():
      assert market_url("kalshi", {"market_id": "KXBTCD-26OCT2011-T85999.99", "raw": {"event": {"series_ticker": "KXBTCD", "event_ticker": "KXBTCD-26OCT2011"}}}).startswith("https://kalshi.com/markets/")
      assert market_url("polymarket", {"raw": {"event": {"slug": "bitcoin-above-86000-on-october-20-12pm-et"}}}) == "https://polymarket.com/event/bitcoin-above-86000-on-october-20-12pm-et"
  ```
  (`test_market_urls`'s Kalshi assertion is finished in Step 4 once the URL shape is verified.)
- [ ] **Step 3: Run** `pytest tests/test_bundle.py -q` — FAIL (module missing).
- [ ] **Step 4: Verify both venues' market URL shapes** by opening one live market of each on the web and reading
  the address bar; record both patterns in `docs/venues.md` ("Market page URL") and fix the Kalshi assertion to the
  real pattern (series/event/market segments as observed).
- [ ] **Step 5: Implement `venue_arb/bundle.py`** per Interfaces. `spark` = `sparks.get(key, [None] * 24)`.
  `verdict` is a plain decision table: no asks → "No asks on one side"; not both_live → "Stale…"; kind exact →
  edge ≥ 0.01 and capacity_usd ≥ 100 ? "Executable edge…" : "Exact match · no edge"; near_exact → "Not an
  arbitrage · basis exposure"; ladder → edge ≥ 0.01 and capacity_usd ≥ 100 ? "Executable edge…" : "Spread · within
  basis bound · no edge". `fees_cents` = fee of the first slice per contract × 100.
- [ ] **Step 6: Run** `pytest tests/test_bundle.py -q` — PASS; full CI passes.
- [ ] **Step 7: Commit** — `git commit -m "bundle: pure live.json builder with verdicts (pydantic contract)"`.

---

### Task 4: Publisher loop (OptiPlex)

**Files:** Create `venue_arb/publish.py`; Test `tests/test_publish.py` (unit, readers injected) and
`tests/test_publish_flow.py` (`@pytest.mark.integration`).

**Interfaces:**
- Consumes: `bundle.*`, `exact.eligible`, `flows.exact_sql.load_eligible`, `flows.ladder_score.LISTINGS_SQL`,
  `flows.ladder_score.bounds_by_asset`, `crypto_rule.ladders`, `kalshi.fetch_series`, `scoring.snapshots`, `ladder_edges.count_window`, `qa.{checks,db,store}` (lazy, as in
  `collect.py`).
- Produces:
  ```python
  PIPELINE = "venue_arb_publish"; INTERVAL_S = 60; REFRESH_EVERY = 10   # ticks between pair/ladder/KPI refreshes
  HISTORY_DAYS = 7; HISTORY_SIZE_BUDGET_MB = 50
  LATEST_BOOKS_SQL, SPARKS_SQL, EPISODES_SQL, LADDER_KPI_SQL, SCREENED_SQL, GAPS_SQL, BASIS_CHANGES_SQL: str
  def latest_books(rows) -> dict[tuple[str, str], Snap]        # newest ts per (venue, market_id) from book rows
  def write_atomic(path: Path, data: bytes) -> None            # temp file in the same dir + os.replace
  def push(src_dir: Path, dest: str, rsync=subprocess.run) -> bool   # rsync -az --timeout=30 --partial (no --delete)
  def history_table(edge_rows, ladder_rows, books) -> pyarrow.Table
      # columns: ts, key, kind, direction, net_edge_cents, capacity_usd, k_ask, k_size, pm_ask, pm_size, fees_cents
      # k_ask/pm_ask = first ask level of each leg at that snapshot (exact and near_exact only; ladder rows null)
  def write_history_hour(ch, out_dir: Path, hour: datetime) -> Path   # pairs-YYYYMMDDHH.parquet, zstd
  def tick(state: dict, readers: dict, now: datetime) -> LiveBundle   # pure composition; readers injected
  def run_forever(out_dir: Path, dest: str | None, interval_s: int = INTERVAL_S) -> None
  ```
  CLI: `python -m venue_arb.publish` (loop), `python -m venue_arb.publish history --hours 168` (backfill, runs
  hour by hour, idempotent). `dest` from env `VENUE_ARB_PUBLISH_DEST` (`user@host:/srv/venue-arb/bundle/`); unset →
  write locally only (dev).
  Reads per tick: `LATEST_BOOKS_SQL` (book_levels_v2 FINAL, ts > now − 5 min, markets IN watch set). Every
  `REFRESH_EVERY` ticks: eligible pairs, ladders + bounds, listings titles/urls, Kalshi series fees, sparks (hourly
  avg `best_edge` from `edges` ∪ `ladder_edges` FINAL, 24 h), episodes (opportunities, 7 d, `NOT LIKE 'ZZTEST-%'`),
  ladder KPI (latest `ladder_admissible_checks` row from `qa.check_results` + violations =
  `countIf(capacity_contracts > 0)` over `ladder_edges FINAL` where `kind='arb'` in `count_window(checked_at)`),
  screened share (`screened_v2 FINAL WHERE k_price < 1`: `countIf(net_gap_top >= 0.01) / count()`, and hours),
  collector gaps (> 3 min between distinct minutes in `book_levels_v2`, 7 d), basis changes (distinct
  `(basis_up, basis_down)` per asset by first ts in `ladder_edges`, 7 d), settlements (pair close times) and
  listings (`first_seen`, 7 d).
  qa results recorded each tick (`store.record`): `bundle_pairs` (expect_rows on pairs), `bundle_fresh`
  (expect_fresh on collector_last, max 5 min, warn), `push` (ok/failed, error), `history_size_mb` (warn over budget,
  hourly), `cycle_seconds` (≤ interval, warn).

- [ ] **Step 1: Write the failing unit tests** in `tests/test_publish.py`: `test_latest_books_picks_newest_ts`,
  `test_write_atomic` (a crash injected between temp write and replace leaves the old file intact — monkeypatch
  `os.replace` to raise, assert original bytes unchanged, then no stray temp files after success),
  `test_push_returns_false_on_nonzero_exit` (fake `rsync` returning code 23), `test_history_table_schema_and_nulls`
  (ladder rows have null `k_ask`), `test_tick_composes_bundle_with_injected_readers` (readers return the Task 3
  fixtures; assert `isinstance(out, LiveBundle)`, pairs count, `kpis.pairs_live`), `test_tick_refreshes_every_n`
  (reader call counts: books every tick, pairs once per `REFRESH_EVERY`).
- [ ] **Step 2: Run** `pytest tests/test_publish.py -q` — FAIL.
- [ ] **Step 3: Implement `venue_arb/publish.py`**: structure mirrors `collect.py` (`run_forever` with lazy `qa`
  import, per-cycle try/except + log, sleep to the interval). rsync flags: `-az --timeout=30 --partial`
  (no `--delete`); old history files are pruned on the server by a cron (Task 9), not by the publisher.
- [ ] **Step 4: Run** unit tests — PASS; full CI.
- [ ] **Step 5: Write the integration test** `tests/test_publish_flow.py` (marked `integration`): with the live DBs,
  `tick` on real readers produces a bundle with ≥ 1 pair and `write_history_hour` for the last full hour writes a
  file whose row count equals the matching `edges` + `ladder_edges` count for that hour (FINAL), size < budget.
  Run it on the OptiPlex: `secrets uv run pytest tests/test_publish_flow.py -m integration -q` — PASS.
- [ ] **Step 6: Commit** — `git commit -m "publish: 60 s live bundle + hourly history Parquet, pushed outbound"`.

---

### Task 5: OptiPlex unit and docs

**Files:** Create `deploy/venue-arb-publish.service`; Modify `tests/test_deploy.py:36` (units list), `README.md`
(Services section), `mise.toml` (no change unless a new env var is needed — `VENUE_ARB_PUBLISH_DEST` is not a secret:
put it in `mise.toml [env]` and the unit's Environment block).

- [ ] **Step 1: Update `test_units_present`** to expect `["venue-arb-collector.service", "venue-arb-flows.service",
  "venue-arb-publish.service"]`; run — FAIL.
- [ ] **Step 2: Write the unit** by copying `venue-arb-collector.service`, `ExecStart … python -m venue_arb.publish`,
  `Environment=VENUE_ARB_PUBLISH_DEST=venue-arb@SERVER_IP:/srv/venue-arb/bundle/` with the placeholder documented
  (the real IP is set on the box; `test_unit_env_matches_mise` must still pass, so the same placeholder goes in
  `mise.toml`).
- [ ] **Step 3: README Services:** three units; how the publisher is pointed at the server; how to backfill history;
  how to run the publisher locally without a server (`VENUE_ARB_PUBLISH_DEST` unset).
- [ ] **Step 4: Run** CI — PASS. **Commit** — `git commit -m "deploy: venue-arb-publish unit"`.

---

### Task 6: Synthetic sample bundle

**Files:** Create `dashboard/sample/make_sample.py`, `dashboard/sample/live.json`,
`dashboard/sample/history/pairs-2026102015.parquet` (+ one more hour); Test `tests/test_sample.py`.

**Interfaces:**
- Produces: a `live.json` validating as `LiveBundle` with ≥ 6 pairs covering every verdict label and both `kind`s
  of ladder row, one episode, every annotation kind; two history hours for those keys. **Invented quotes** (real
  ticker shapes, prices and sizes made up); `make_sample.py` builds them through `bundle.*` and
  `publish.history_table` so the sample never drifts from the contract. Regenerate with
  `uv run python dashboard/sample/make_sample.py`.

- [ ] **Step 1: Test** `tests/test_sample.py`: `LiveBundle.model_validate_json(Path("dashboard/sample/live.json").read_text())`
  succeeds; labels present ⊇ the seven labels; `pyarrow.parquet.read_table` of each history file has the Task 4
  columns; regenerating produces byte-identical files (fixed `now`, seeded data) — `test_sample_is_reproducible`.
- [ ] **Step 2: Run** — FAIL. **Step 3:** write `make_sample.py` and run it. **Step 4:** tests PASS, CI PASS.
- [ ] **Step 5: Commit** — `git commit -m "dashboard: synthetic sample bundle (no real quotes in the repo)"`.

---

### Task 7: Server API

**Files:** Create `dashboard/api/__init__.py`, `dashboard/api/store.py`, `dashboard/api/app.py`; Modify
`pyproject.toml` (optional extra `dashboard = ["fastapi>=0.115", "uvicorn>=0.30", "duckdb>=1.1"]`, and the same three
in the `dev` group so CI has them); Test `tests/test_dashboard_api.py`.

**Interfaces:**
- Consumes: `bundle.LiveBundle`, `edges.fill`, `fees.kalshi_taker_fee` / `polymarket_taker_fee` with each pair's
  `fee_params`, `fees.transfer_cost`.
- Produces (`BUNDLE_DIR` env, default `/srv/venue-arb/bundle`, tests point it at `dashboard/sample`):
  ```python
  class Store:                     # dashboard/api/store.py
      def __init__(self, bundle_dir: Path): ...
      def live(self) -> tuple[LiveBundle, float]        # (bundle, age_s); re-reads when mtime changes;
                                                        # keeps the last good bundle if the file fails to parse
      def history(self, key: str, range_: Literal["1h","24h","7d","all"]) -> dict   # columns -> lists; "all" = hourly avg
      def snapshot(self, key: str, ts: datetime) -> dict | None                    # the nearest row within 90 s
  app = FastAPI()                 # dashboard/api/app.py
  GET /            -> static index.html;  /static/* -> dashboard/static
  GET /api/live    -> {"bundle": LiveBundle, "age_s": float}
  GET /api/pair/{key}/history?range=24h -> Store.history (404 unknown key)
  GET /api/pair/{key}/snapshot?ts=      -> Store.snapshot (404)
  GET /api/walk?key=&size=              -> edges.fill on the pair's live books: Fill as JSON + transfer_cost_usd
                                           (422 size <= 0 or > 1e6; 404 unknown key; 409 when show_books is False)
  GET /healthz     -> {"ok": true, "age_s": ..., "pairs": n}
  ```
  DuckDB: one in-process connection, `read_parquet('<dir>/history/pairs-*.parquet')` with `WHERE key = ?`;
  "all" = `date_trunc('hour', ts)` average. Keys are path-encoded with `urllib.parse.quote(key, safe="")`.

- [ ] **Step 1: Write the failing tests** with `fastapi.testclient.TestClient` on the sample dir:
  `test_live_and_health`, `test_history_ranges` (24h rows ≤ 1440; all is hourly), `test_snapshot_nearest`,
  `test_walk_matches_fill` (same numbers as calling `fill` directly; 422 on size 0), `test_pair_key_roundtrip_and_404`
  (quote a key containing `~`, `.`, `-`, `0x`, request it, 200; a made-up key, 404), `test_store_keeps_last_good_bundle`
  (overwrite `live.json` with `{"half":` in a tmp copy of the sample, `Store.live()` returns the previous bundle and
  `age_s` keeps growing), `test_server_imports_without_pipeline_deps` (subprocess: `python -c "import
  venue_arb.edges, venue_arb.bundle, dashboard.api.app"` with `sys.modules` blocking `prefect`, `anthropic`,
  `fastembed`, `clickhouse_connect`, `psycopg` via a `-c` shim that inserts `None` for them — must succeed).
- [ ] **Step 2: Run** — FAIL. **Step 3:** `uv add --optional dashboard fastapi uvicorn duckdb` and the dev group;
  implement `store.py` then `app.py`. **Step 4:** tests PASS; CI PASS.
- [ ] **Step 5: Commit** — `git commit -m "dashboard api: live, history (DuckDB), snapshot, walk, healthz"`.

---

### Task 8: Page (static UI)

**Files:** Create `dashboard/static/index.html`, `app.js`, `styles.css`, `plotly-basic.min.js` (vendored),
`VENDOR.md`; Test `tests/test_dashboard_static.py`. Reference: the mockup
`.superpowers/brainstorm/18232-1792513222/content/explorer-v3.html` (git-ignored; copy its CSS variables and layout).

**Interfaces:**
- Consumes: the Task 7 endpoints. No build step, no framework: `app.js` is an ES module, `fetch` + DOM, Plotly via
  the global `Plotly` from the vendored file.
- Produces, as the spec §3 lists: header (live dot, `computed_at` clock, next-tick countdown, category chips, unit
  toggle ¢ | bps with the other unit small beneath each figure, size box default 1000, Executable-from toggle),
  KPI strip (5 tiles; "Ladder checks · last scored hour [lo–hi)"), pair list (groups: exact & near-exact flat;
  ladder rows under a collapsed header per `pair.ladder`; any ladder row with `net_edge_cents ≥ 1` surfaces above the
  fold), detail pane (leg chips as links, verdict badge, three boxes, Plotly chart, four check cards), footer
  (denominator sentence from `kpis`, `attribution`, `caveats`, "Read-only. No orders are placed.", repo link),
  staleness banner at `age_s > 120`, greyed tiles at `> 3600`, US mode greys rows + banner per §10.6, books boxes
  replaced by the derived-only panel when `show_books` is false. Chart: traces per direction from `/history`,
  range buttons call the API with `range`, hover template with the row's asks/sizes/fees/net/capacity, annotations
  toggle maps `annotations[]` + `episodes[]` to Plotly `shapes`/`annotations` (threshold line at +1¢ always), click
  → `/snapshot` → fills the three boxes with "history: first ask level only" note. Polls `/api/live` every 30 s;
  `/api/walk` on size change (debounced 300 ms).
- [ ] **Step 1: Vendor plotly.js** `basic` dist: choose the latest release dated before 2026-10-05 from the
  plotly.js GitHub releases page, download `plotly-basic.min.js` from the release's CDN URL, record version, URL,
  date and `shasum -a 256` in `VENDOR.md`.
- [ ] **Step 2: Test** `tests/test_dashboard_static.py`: `index.html` references only files that exist under
  `dashboard/static`; `VENDOR.md` sha256 matches the vendored file; `app.js` contains no `http://`/`https://`
  except `kalshi.com`/`polymarket.com` link bases and the repo URL (no CDN); the seven verdict labels and the two
  banners' copy appear verbatim in `app.js` (copy from the spec: "No pair is executable from the US: Polymarket
  rejects US orders (docs/venues.md). Gaps shown for reference." and "last update {n} min ago"). Run — FAIL.
- [ ] **Step 3: Write the page** against the sample: `BUNDLE_DIR=dashboard/sample uv run uvicorn dashboard.api.app:app`
  and open it in the preview browser; check every spec §3 element renders with the sample's seven verdicts; toggle
  units, size, US mode, annotations, ranges; click a chart point.
- [ ] **Step 4: Light theme and phone width.** Theme = CSS variables on `:root` with a `prefers-color-scheme: light`
  block and a header toggle (`localStorage`); at 390 px wide the list stacks above the detail and nothing overflows
  horizontally (check in the preview browser's mobile preset). No polish beyond that.
- [ ] **Step 5: Run** the static test — PASS; CI PASS. Take one screenshot to `reports/dashboard-sample.png`.
- [ ] **Step 6: Commit** — `git commit -m "dashboard page: pair explorer with Plotly history (vendored, no CDN)"`.

---

### Task 9: Server provisioning

**Files:** Create `deploy/server/provision.sh`, `deploy/server/venue-arb-web.service`, `deploy/server/Caddyfile`,
`deploy/server/README.md`; Modify `tests/test_deploy.py` (server unit has no DB env: exclude `deploy/server/*` from
`UNITS` and add `test_server_unit_has_no_db_env`).

- [ ] **Step 1: `provision.sh`** (idempotent, run as root on fresh Ubuntu 24.04): create user `venue-arb` (no
  shell), `/srv/venue-arb/{bundle,app}`, install `rsync`, `caddy`, `uv`; `authorized_keys` for `venue-arb` from
  `$1` (the OptiPlex public key) with `command="/usr/bin/rrsync -no-del /srv/venue-arb/bundle",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding`
  (write-only, no delete); a root cron (`deploy/server/prune.cron`) deletes `bundle/history/*.parquet` older than
  8 days daily;
  clone the repo into `/srv/venue-arb/app`, `uv venv`, `uv pip install --no-deps -e .` + the `dashboard` extra deps;
  install the unit (`User=venue-arb`, `Environment=BUNDLE_DIR=/srv/venue-arb/bundle`, `ExecStart=… uvicorn
  dashboard.api.app:app --host 127.0.0.1 --port 8000`), the Caddyfile (`:443` with `tls internal` when no domain,
  reverse_proxy to 8000), `ufw allow 22,443`, enable both services. `bash -n` clean; shellcheck clean if installed.
- [ ] **Step 2: README.md** (server): the five manual steps for Sam (create VM, open 443 in the Oracle security
  list, run provision with the key, set `VENUE_ARB_PUBLISH_DEST` on the OptiPlex, start the publisher), how to update
  (`git pull` + restart), where logs are.
- [ ] **Step 3:** tests PASS; CI PASS. **Commit** — `git commit -m "deploy/server: provisioning, web unit, Caddy"`.

---

### Task 10: Public-repo checklist

**Files:** Create `docs/going-public.md`; Modify `README.md` (front door), `.gitignore` (add `.superpowers/`).

- [ ] **Step 1: Secret scan** of full history with `gitleaks detect --source . --log-opts="--all"` (install via
  `brew install gitleaks` on the Mac or the release binary). Record the command and result in `docs/going-public.md`.
  Any hit → stop and report to the controller (history rewrite is Sam's decision).
- [ ] **Step 2: README** rewrite as the spec §7.4 lists: the question, method (tree → exact/near → walk → verdict),
  results so far (numbers from `reports/research-note-v1.md`), architecture diagram (the spec §4 ASCII), link
  placeholder for the live page, how to run (pipeline and dashboard), tests, services. Keep existing operational
  sections (move them under "Operations").
- [ ] **Step 3: `docs/going-public.md`:** the checklist with boxes: scan clean; `BWS_PROJECT_ID` decision (Sam);
  license (Sam); `.superpowers/` ignored; sample data synthetic; page footer links repo; repo links page.
- [ ] **Step 4:** CI PASS. **Commit** — `git commit -m "README front door + going-public checklist"`.

---

## Deploy (controller, after Sam's approval)
1. Merge the branch into `main` on the OptiPlex; `uv sync`; `pytest tests/test_publish_flow.py -m integration`.
2. Sam: create the Oracle VM, run `deploy/server/provision.sh <optiplex_pubkey>`, give the IP.
3. OptiPlex: set `VENUE_ARB_PUBLISH_DEST` in `mise.toml`/unit, `python -m venue_arb.publish history --hours 168`
   (background), enable `venue-arb-publish`; verify `/healthz` age < 120 s and the page renders real pairs.
4. Record the URL in README and memory; Sam flips the repo public after `docs/going-public.md` is all checked.
