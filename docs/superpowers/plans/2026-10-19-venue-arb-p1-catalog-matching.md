# venue-arb Plan 1: catalog, matching, and snapshot collection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull Kalshi and Polymarket International listings for the Fed/macro and crypto-price categories, match
them across venues with explained, reviewable matches, and start collecting order-book snapshots for candidate pairs.

**Architecture:** Pure modules (`venue_arb/sources`, `taxonomy`, `matching`, `books`) parse and decide; Prefect flows
(`venue_arb/flows`) fetch, run `qa` guards, load, check, and record. Postgres schema `venue_arb` holds listings,
embeddings, candidates, adjudications, and reviews. ClickHouse database `venue_arb` holds order-book levels. A
long-running collector process polls books every 60 s.

**Tech Stack:** Python 3.14, uv, Prefect 3.8.7, psycopg, clickhouse-connect, pgvector, httpx, pydantic, anthropic,
fastembed (local CPU embeddings, no API cost), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-19-venue-arb-design.md`

**Scope of this plan:** spec §7 phases 1 and 2, plus the start of snapshot collection (spec §4, "History vs
snapshots": collection starts early). Later plans: (2) exact-match scoring, fee table in code, historical
screening, research note v1; (3) more venues, near-match pricing, perp vs stock basis, Binance/Bybit archives,
canonical event tree across 3+ venues; (4) live demo. Polymarket US is deferred to plan 3: its data is
forward-only through an authenticated WebSocket.

## Global Constraints

- Follow `~/platform/.agent-rules/hard-rules.md` and `~/platform/docs/NEW_PROJECT.md` (repo `~/projects/venue-arb`, DB
  role/schema/database `venue_arb`).
- `requires-python = ">=3.14,<3.15"`; `[tool.uv] exclude-newer` at least 2 weeks before the day you run `uv lock`;
  `prefect==3.8.7`.
- Connections only through `qa.db` (`db.pg()`, `db.ch()`); functions that touch a DB take the connection as a
  parameter; pure modules never import `qa`.
- Every load: pre-load guards → idempotent load → post-load checks → `sql_checks/` assertions →
  `qa.store.record(conn, pipeline, run_id, results)`.
- No code calls any order, account, portfolio, or position endpoint on any venue. Read-only public endpoints only.
- No VPN or non-US routing; Binance/Bybit are out of scope for this plan.
- LLM spend under $20 per calendar month (spec §5.5). Adjudication model: `claude-haiku-5-5` (env
  `VENUE_ARB_LLM_MODEL`, default that id). Key: `ANTHROPIC_API_KEY` from Bitwarden via `secrets`.
- No paid data sources.
- Trust bar: precision ≥ 95% on a 200-match review sample stratified by category and confidence band; low-confidence
  matches (`confidence < 0.8`) stay out of research numbers until reviewed.
- Commits without a Claude co-author trailer (Sam's rule).

## Review Focus

1. **An empty book side** (Kalshi returned `"yes_dollars": []` for a live Fed market): no levels are stored for the
   missing side. Never a price of 0 or 1. Test in Task 2 (`test_parse_book_empty_side`).
2. **Pagination truncation**: Kalshi `cursor` and Polymarket `offset` must be followed until exhausted, or markets
   silently vanish. Tests in Tasks 2 and 3 (`test_list_events_follows_cursor`, `test_list_events_follows_offset`).
3. **Polymarket markets whose outcomes aren't Yes/No** (team names, "Up"/"Down"): `outcomes[0]` pairs with
   `clobTokenIds[0]` and is stored as the "yes" leg, keeping its label. Test in Task 3
   (`test_parse_listings_non_yes_no_outcomes`).
4. **A venue edits a market's rules or deadline**: the version hash changes, so the old adjudication must not be
   reused for the new version. Test in Task 7 (`test_cache_miss_on_new_version`).
5. **LLM budget exhausted mid-run**: adjudication stops cleanly, the run records how many candidates were skipped,
   and catalog and snapshot jobs keep working. Test in Task 7 (`test_budget_stops_adjudication`).

---

### Task 1: Repo scaffold, database objects, venue notes

**Files:**
- Create: `pyproject.toml`, `mise.toml`, `.gitignore`, `AGENTS.md`, `CLAUDE.md`, `.github/workflows/ci.yml`,
  `venue_arb/__init__.py`, `venue_arb/config.py`, `db/__init__.py`, `db/migrate.py`,
  `db/migrations/pg/001_core.sql`, `db/migrations/ch/001_books.sql`, `docs/venues.md`
- Test: `tests/test_migrate.py`

**Interfaces:**
- Produces: `venue_arb.config` constants `PIPELINE_PREFIX = "venue_arb"`, `CATEGORIES = ("fed_macro", "crypto_price")`,
  `SQL_CHECKS = Path("sql_checks")`, `CONFIDENCE_FLOOR = 0.8`, `MONTHLY_LLM_BUDGET_USD = 20.0`,
  `LLM_MODEL = os.environ.get("VENUE_ARB_LLM_MODEL", "claude-haiku-5-5")`, `SNAPSHOT_INTERVAL_S = 60`,
  `SNAPSHOT_MAX_MARKETS = 300`, `BOOK_DEPTH = 10`.
- Produces: `db.migrate.split_statements(sql: str) -> list[str]` and `db.migrate.apply(pg_conn, ch_client) -> list[str]`.
  Copy `~/projects/fedspeak-surprise/db/migrate.py` with `fedspeak` renamed to `venue_arb`.
- Produces tables (all Postgres in schema `venue_arb`):
  - `listings(venue text, market_id text, event_id text, category text, title text, rules text, yes_label text,
    no_label text, yes_token text, no_token text, close_time timestamptz, resolution_source text, version text,
    raw jsonb, first_seen timestamptz, last_seen timestamptz, PRIMARY KEY (venue, market_id))`
  - `embeddings(venue text, market_id text, version text, vec vector(384), PRIMARY KEY (venue, market_id, version))`
  - `candidates(a_venue, a_id, b_venue, b_id text, source text CHECK (source IN ('rule','embed')), score real,
    created_at timestamptz, PRIMARY KEY (a_venue, a_id, b_venue, b_id))`
  - `adjudications(a_venue, a_id, a_version, b_venue, b_id, b_version text, model text, relation text CHECK
    (relation IN ('exact','near','different')), yes_maps_to text CHECK (yes_maps_to IN ('yes','no')), deadline_diff
    text, threshold_diff text, resolution_diff text, reason text, confidence real, cost_usd numeric(10,6),
    created_at timestamptz, PRIMARY KEY (a_venue, a_id, a_version, b_venue, b_id, b_version))`
  - `reviews(a_venue, a_id, a_version, b_venue, b_id, b_version text, label text CHECK (label IN
    ('correct','wrong','unsure')), note text, reviewed_at timestamptz, PRIMARY KEY (same six columns))`
  - Pair convention everywhere: `a` is Kalshi, `b` is Polymarket.
- Produces ClickHouse `venue_arb.book_levels(ts DateTime64(3,'UTC'), venue LowCardinality(String), market_id String,
  side Enum8('yes'=1,'no'=2), level UInt8, price Float64, size Float64) ENGINE = ReplacingMergeTree ORDER BY (venue,
  market_id, ts, side, level)`. The ordering key makes a retried load idempotent.

- [ ] **Step 1: 👤 Ask Sam** to create Bitwarden secrets `VENUE_ARB_PG_PASSWORD`, `VENUE_ARB_CH_PASSWORD`, and a
  project-specific `ANTHROPIC_API_KEY` with a $20/month spending cap (NEW_PROJECT.md §2). Wait for confirmation.
- [ ] **Step 2: Create the DB logins**: `cd ~/platform && BWS_PROJECT_ID=42f2b924-cf96-49c8-85a3-b4e4001765b4
  secrets bin/project-db.sh venue_arb`. Expected: completes without error.
- [ ] **Step 3: Write the scaffold files.** `mise.toml` per NEW_PROJECT.md §4 with `PGUSER`/`CLICKHOUSE_USER` =
  `venue_arb`. `pyproject.toml` modeled on fedspeak's (same ruff/pytest config and `integration` marker), deps:
  `prefect==3.8.7`, `psycopg[binary]`, `clickhouse-connect`, `pgvector`, `httpx`, `pydantic`, `anthropic`,
  `fastembed`, `numpy`. CI identical to fedspeak's `ci.yml` (with only the `integration` marker excluded). `AGENTS.md` per
  NEW_PROJECT.md §11, adding the "read-only endpoints only" and "no VPN" rules from Global Constraints. Run `mise
  trust`, then `uv lock`.
- [ ] **Step 4: Write `tests/test_migrate.py::test_split_statements_drops_comments`**: asserts
  `split_statements("-- c\nCREATE TABLE a (x Int8);\n\nCREATE TABLE b (y Int8);") == ["CREATE TABLE a (x Int8)",
  "CREATE TABLE b (y Int8)"]`. Run `uv run pytest tests/test_migrate.py -v`: FAIL (module missing).
- [ ] **Step 5: Add `db/migrate.py` and both migration files**, then run the test again: PASS.
- [ ] **Step 6: Apply migrations**: `secrets uv run python -m db.migrate`. Expected: prints `['pg/001_core.sql',
  'ch/001_books.sql']`; a second run prints `up to date`.
- [ ] **Step 7: Smoke test** (NEW_PROJECT.md §8). Expected: `postgres as ('venue_arb', 'venue_arb')`,
  `clickhouse as venue_arb`.
- [ ] **Step 8: Write `docs/venues.md`.** One section per venue (Kalshi, Polymarket International), each with dated
  entries for: read endpoints used and rate limits, taker/maker fee formula, jurisdictions where trading is allowed,
  and whether public data terms allow displaying prices on a public page. Quote and link each venue's own docs.
  This is research, not code. Values the docs don't settle are written as "unverified".
- [ ] **Step 9: Run CI locally**: `uv sync --locked && uv run ruff check . && uv run pytest -m "not integration" -q`.
  Then commit: `git add -A && git commit -m "Scaffold: platform wiring, migrations, venue notes"`.

### Task 2: HTTP helper and Kalshi source

**Files:**
- Create: `venue_arb/http.py`, `venue_arb/models.py`, `venue_arb/sources/__init__.py`, `venue_arb/sources/kalshi.py`,
  `tests/fixtures/kalshi_events_p1.json`, `tests/fixtures/kalshi_events_p2.json`, `tests/fixtures/kalshi_book.json`,
  `tests/fixtures/kalshi_book_empty_yes.json`
- Test: `tests/test_kalshi.py`

**Interfaces:**
- Produces `venue_arb.http.get_json(url: str, params: dict | None = None, delay: float = 0.1, tries: int = 4) -> dict | list`.
  Polite delay between calls; retries 429/5xx with backoff; raises `RuntimeError` naming the URL path (never query
  strings) after the last try; returns `{}` on 404. No disk cache: these are live reads.
- Produces `venue_arb.models`:
  ```python
  @dataclass(frozen=True)
  class Listing:
      venue: str; market_id: str; event_id: str; category: str | None; title: str; rules: str
      yes_label: str; no_label: str; yes_token: str | None; no_token: str | None
      close_time: datetime; resolution_source: str | None; raw: dict

  @dataclass(frozen=True)
  class Level:
      side: Literal["yes", "no"]; level: int; price: float; size: float   # ASK side: price to BUY this outcome
  ```
- Produces `venue_arb.sources.kalshi`:
  - `API = "https://api.elections.kalshi.com/trade-api/v2"`
  - `list_events(series_tickers: list[str], get=http.get_json) -> list[dict]`: `GET /events?status=open&with_nested_markets=true&limit=200&series_ticker=…`,
    following `cursor` until it's empty. `get` is injectable for tests.
  - `parse_listings(events: list[dict]) -> list[Listing]`: one Listing per nested market. `market_id = ticker`,
    `event_id = event_ticker`, `title = market title`, `rules = rules_primary + "\n" + rules_secondary`,
    `yes_label = yes_sub_title`, `no_label = no_sub_title or "No"`, `close_time = close_time` (UTC-aware),
    `resolution_source` = the first `settlement_sources[].url` of the event, `category = None` (Task 4 assigns it).
  - `fetch_book(ticker: str, get=http.get_json) -> dict`: `GET /markets/{ticker}/orderbook?depth=10`.
  - `parse_book(payload: dict, depth: int = 10) -> list[Level]`. Kalshi returns **bids only** (`orderbook_fp.yes_dollars`,
    `orderbook_fp.no_dollars`, each `[price, size]` strings, ascending). The ask to buy Yes at `1 − p` comes from a No
    bid at `p`, and vice versa. Levels are sorted best (cheapest) first, `level` starts at 0, and prices are rounded to 4 dp.

- [ ] **Step 1: Save fixtures** from live calls (real responses, trimmed): two event pages where page 1 has a
  non-empty `cursor` and page 2 has an empty one; one normal book; one book with `"yes_dollars": []`.
- [ ] **Step 2: Write the failing tests:**
  - `test_list_events_follows_cursor`: a fake `get` serves p1 then p2. Asserts two calls, the second with
    `cursor=<p1 cursor>`, and that the result is the concatenation of both pages' events.
  - `test_parse_listings_fields`: on the p1 fixture's first market, asserts `market_id`, `event_id`, `yes_label`,
    and that `close_time.tzinfo is not None`.
  - `test_parse_book_derives_asks`: given `no_dollars=[["0.97","2030.01"],["0.98","756579.01"]]`, asserts the first
    yes Level is `Level("yes", 0, 0.02, 756579.01)` and the second is `Level("yes", 1, 0.03, 2030.01)`.
  - `test_parse_book_empty_side`: on `kalshi_book_empty_yes.json`, asserts no `side == "no"` levels are returned
    (they would come from yes bids, which are empty) and every price is strictly between 0 and 1.
  Run `uv run pytest tests/test_kalshi.py -v`: FAIL.
- [ ] **Step 3: Implement** `http.py`, `models.py`, and `sources/kalshi.py` to the interfaces above.
- [ ] **Step 4: Run** `uv run pytest tests/test_kalshi.py -v`: PASS.
- [ ] **Step 5: Commit**: `git commit -m "Kalshi source: paginated events, listings, bid-derived asks"`.

### Task 3: Polymarket International source

**Files:**
- Create: `venue_arb/sources/polymarket.py`, `tests/fixtures/pm_events_p1.json`, `tests/fixtures/pm_events_p2.json`,
  `tests/fixtures/pm_events_teams.json`, `tests/fixtures/pm_book.json`
- Test: `tests/test_polymarket.py`

**Interfaces:**
- Consumes: `http.get_json`, `models.Listing`, `models.Level`.
- Produces `venue_arb.sources.polymarket`:
  - `GAMMA = "https://gamma-api.polymarket.com"`, `CLOB = "https://clob.polymarket.com"`
  - `list_events(tag_slugs: list[str], get=http.get_json, page: int = 100) -> list[dict]`:
    `GET /events?active=true&closed=false&tag_slug=…&limit=page&offset=…` per tag, following `offset` until a page
    returns fewer than `page` rows. De-duplicated by event `id`.
  - `parse_listings(events: list[dict]) -> list[Listing]`: one per market with `enableOrderBook` true and not
    `closed`. `market_id = conditionId`, `event_id = event id`, `title = question`, `rules = description`,
    `outcomes = json.loads(outcomes)` and `tokens = json.loads(clobTokenIds)`, giving `yes_label, no_label =
    outcomes` and `yes_token, no_token = tokens` (index 0 is the stored "yes" leg),
    `close_time = endDate`, `resolution_source = resolutionSource or None`.
  - `fetch_book(token_id: str, get=http.get_json) -> dict`: `GET {CLOB}/book?token_id=…`.
  - `parse_book(payload: dict, depth: int = 10) -> list[Level]`: `asks` give the "yes" side directly. The "no" side
    is derived from `bids` (`1 − bid`), because Polymarket matches complementary orders across the two tokens. Best
    level first, prices as floats.
  - `# ponytail: one book call per market by mirroring the token-0 book; fetch the token-1 book if a spot check
    disagrees by more than one tick.`

- [ ] **Step 1: Save fixtures** from live calls: two offset pages for tag `fed` (page size 2 so pagination
  shows), one event whose outcomes are not Yes/No (e.g. a sports or "Up/Down" event), and one book.
- [ ] **Step 2: Write the failing tests:**
  - `test_list_events_follows_offset`: with `page=2`, the fake `get` returns 2 events then 1. Asserts offsets `0, 2`
    were requested and 3 events were returned.
  - `test_list_events_dedupes_across_tags`: the same event under two tags is returned once.
  - `test_parse_listings_non_yes_no_outcomes`: on the teams fixture, asserts `yes_label == outcomes[0]` and
    `yes_token == clobTokenIds[0]` for that market.
  - `test_parse_book_mirrors_no_side`: with bids `[{"price":"0.97","size":"10"}]` and asks
    `[{"price":"0.972","size":"5"}]`, asserts `Level("yes",0,0.972,5.0)` and `Level("no",0,0.03,10.0)`.
  Run: FAIL.
- [ ] **Step 3: Implement** `sources/polymarket.py`. Polymarket returns bids ascending and asks descending in at
  least some responses, so sort explicitly; don't trust the order.
- [ ] **Step 4: Run** `uv run pytest tests/test_polymarket.py -v`: PASS.
- [ ] **Step 5: Commit**: `git commit -m "Polymarket source: offset pagination, outcome-token pairing, mirrored book"`.

### Task 4: Taxonomy: category assignment and version hash

**Files:**
- Create: `venue_arb/taxonomy.py`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Consumes: `models.Listing`.
- Produces:
  - `KALSHI_SERIES: dict[str, str]`: maps series tickers to categories. Fed/macro starts with `KXFEDDECISION`,
    `KXFED`, `KXCPI`, `KXGDP`, `KXRATECUTCOUNT`; crypto_price starts with `KXBTC`, `KXBTCD`, `KXETH`, `KXETHD`. Short-dated
    `*15M` series are excluded: they close within 15 minutes and add noise, not matches. Verify each ticker exists
    against the live API while writing this, and drop any that don't.
  - `POLYMARKET_TAGS: dict[str, str]`: `fed`, `fed-rates`, `economic-policy`, `inflation` → fed_macro;
    `bitcoin`, `ethereum`, `crypto-prices` → crypto_price. Verify each slug returns events.
  - `assign_category(listing: Listing, series_ticker: str | None, tag_slugs: list[str]) -> str | None`: Kalshi uses
    the series map. Polymarket takes the first matching tag in `CATEGORIES` order, so a market tagged both `fed`
    and `bitcoin` resolves to fed_macro. Returns `None` if nothing matches (the listing is dropped).
  - `version(listing: Listing) -> str`: first 16 hex chars of sha256 over `title`, `rules`, `yes_label`,
    `close_time.isoformat()`, and `resolution_source`, joined with `\x1f`.

- [ ] **Step 1: Write the failing tests:**
  - `test_assign_category_kalshi_series`: `KXFEDDECISION` → `"fed_macro"`, `KXBTCD` → `"crypto_price"`, `KXBTC15M` → `None`.
  - `test_assign_category_pm_tag_priority`: tags `["bitcoin","fed"]` → `"fed_macro"`.
  - `test_version_changes_on_rules_edit`: two listings that differ only in `rules` produce different versions,
    and identical listings produce identical versions.
  Run: FAIL.
- [ ] **Step 2: Implement** `taxonomy.py`, verifying the tickers and slugs against live endpoints.
- [ ] **Step 3: Run**: PASS. **Commit**: `git commit -m "Taxonomy: categories from series/tags, listing version hash"`.

### Task 5: Catalog flow

**Files:**
- Create: `venue_arb/catalog.py` (pure), `venue_arb/flows/__init__.py`, `venue_arb/flows/catalog.py`,
  `sql_checks/listings_close_after_seen.sql`, `sql_checks/listings_two_venues.sql`
- Test: `tests/test_catalog.py`, `tests/test_catalog_flow.py` (integration)

**Interfaces:**
- Consumes: both sources, `taxonomy.assign_category`, `taxonomy.version`.
- Produces `venue_arb.catalog.to_rows(listings: list[Listing], versions: dict[tuple[str, str], str], now: datetime) -> list[dict]`
  (pure: shapes rows for `listings`, with `raw` JSON-encoded).
- Produces `venue_arb.catalog.upsert(conn, rows: list[dict]) -> int`: `INSERT … ON CONFLICT (venue, market_id) DO UPDATE`
  every column except `first_seen`. Returns the row count.
- Produces flow `venue_arb.flows.catalog.catalog_flow() -> dict` (pipeline `venue_arb_catalog`). Steps: fetch both
  venues → assign categories (drop `None`) → guards (`expect_rows` ≥ 1 per venue, `expect_schema`) → `enforce` →
  upsert → post-checks (`expect_count_match` between upserted rows and listings with `last_seen = now`,
  `expect_fresh` on `last_seen`) → `qa.sql.run_all(pattern="listings_*.sql", directory=SQL_CHECKS)` →
  `store.record`. Returns `{"kalshi": n, "polymarket": n}`.
- SQL checks (header format as in `~/platform/qa/sql_checks/`): `listings_close_after_seen.sql` (error) returns open
  listings whose `close_time < first_seen`; `listings_two_venues.sql` (error) returns a row for each category that
  doesn't have listings from both venues in the last 2 hours.

- [ ] **Step 1: Write `test_to_rows_shapes_and_encodes_raw`** (pure): asserts keys equal the `listings` columns
  except `first_seen`, and that `raw` is a str. Run: FAIL.
- [ ] **Step 2: Implement** `catalog.py`. Run: PASS.
- [ ] **Step 3: Write `test_catalog_flow_idempotent`** (`@pytest.mark.integration`): runs `catalog_flow()` twice.
  Asserts the `listings` row count is equal after both runs and both returned venue counts are > 0. Run:
  `secrets uv run pytest tests/test_catalog_flow.py -m integration -v` (stack up): FAIL.
- [ ] **Step 4: Implement** `flows/catalog.py` and the two SQL checks. Run: PASS. Spot-check in psql: `SELECT venue,
  category, count(*) FROM venue_arb.listings GROUP BY 1,2` shows four non-zero rows.
- [ ] **Step 5: Commit**: `git commit -m "Catalog flow: both venues, two categories, qa-guarded upsert"`.

### Task 6: Embeddings and candidate pairs

**Files:**
- Create: `venue_arb/matching/__init__.py`, `venue_arb/matching/embed.py`, `venue_arb/matching/candidates.py`
- Test: `tests/test_candidates.py`

**Interfaces:**
- Consumes: `listings` rows (dicts with the column names from Task 1).
- Produces `venue_arb.matching.embed`:
  - `MODEL = "BAAI/bge-small-en-v1.5"` (384 dims, runs on CPU through fastembed).
  - `text_for(row: dict) -> str`: `title + " | " + yes_label + " | closes " + close_time.date().isoformat()`.
  - `embed(texts: list[str]) -> np.ndarray` (shape `(n, 384)`, L2-normalized).
- Produces `venue_arb.matching.candidates`:
  - `rule_pairs(kalshi: list[dict], pm: list[dict]) -> list[tuple[str, str, float]]`: for `fed_macro`, pairs whose
    close dates are within 2 days of each other **and** whose titles both name the same FOMC month (Kalshi
    `KXFEDDECISION-26OCT` ↔ a Polymarket title containing "October" and the same year). Score 1.0.
  - `embed_pairs(kalshi: list[dict], pm: list[dict], kvec: np.ndarray, pvec: np.ndarray, k: int = 5, min_sim: float = 0.75, max_days: int = 31) -> list[tuple[str, str, float]]`:
    within the same category only, the top-k Polymarket neighbors by cosine for each Kalshi listing. Keeps pairs
    with sim ≥ `min_sim` and close dates within `max_days` of each other.
  - `merge(rule: list, emb: list) -> list[tuple[str, str, float, str]]`: union keyed by `(kalshi_id, pm_id)`. Rule
    pairs win (`source='rule'`).

- [ ] **Step 1: Write the failing tests** (pure, with hand-built rows and vectors, no model download):
  - `test_embed_pairs_respects_category`: identical vectors across different categories produce no pair.
  - `test_embed_pairs_date_window`: sim 0.99 but close dates 40 days apart produces no pair.
  - `test_rule_pairs_fomc_month`: Kalshi Oct-2026 Fed row ↔ Polymarket "Fed decision in October 2026?" produces a
    pair; the same title with "December" doesn't.
  - `test_merge_prefers_rule`: a pair in both lists comes back once, with source `rule`.
  Run: FAIL.
- [ ] **Step 2: Implement** both modules. Run: PASS.
- [ ] **Step 3: Commit**: `git commit -m "Matching: rule and embedding candidate pairs within category and date window"`.

### Task 7: LLM adjudicator with cache and budget

**Files:**
- Create: `venue_arb/matching/adjudicate.py`
- Test: `tests/test_adjudicate.py`

Read the `claude-api` skill before writing the Anthropic call (structured output via a tool schema; read token
usage from the response).

**Interfaces:**
- Produces:
  ```python
  class Verdict(BaseModel):
      relation: Literal["exact", "near", "different"]
      yes_maps_to: Literal["yes", "no"]        # which Polymarket leg pays when Kalshi Yes pays
      deadline_diff: str | None; threshold_diff: str | None; resolution_diff: str | None
      reason: str                              # one plain sentence: "same event because X, differs by Y"
      confidence: float                        # 0..1
  ```
  - `PRICES: dict[str, tuple[float, float]]`: USD per million input/output tokens for `LLM_MODEL`, taken from the
    claude-api skill at build time.
  - `prompt_for(a: dict, b: dict) -> str`: both listings' title, outcome labels, rules, close time, and resolution
    source. Includes the instruction that "exact" requires the same event, same outcome definition, same deadline,
    and same resolution source.
  - `adjudicate(a: dict, b: dict, call=_call_claude) -> tuple[Verdict, float]` returns the verdict and cost in USD.
    `call` is injectable.
  - `run(conn, pairs: list[tuple[dict, dict]], month_spend: float, budget: float = MONTHLY_LLM_BUDGET_USD, call=_call_claude) -> dict`:
    skips pairs that already have an adjudication with the same `(a_id, a_version, b_id, b_version)`. Stops before
    any call that would push `month_spend` over `budget`. Inserts each verdict. Returns
    `{"adjudicated": n, "cached": n, "skipped_budget": n, "spend": x}`.
  - `month_spend(conn) -> float`: `SELECT coalesce(sum(cost_usd),0) FROM venue_arb.adjudications WHERE created_at >=
    date_trunc('month', now())`.

- [ ] **Step 1: Write the failing tests** (fake `call` returns a fixed Verdict and token counts; uses a
  `FakeConn` that records inserts and answers the cache lookup):
  - `test_cache_hit_skips_call`: an existing adjudication for the same versions means the fake `call` is never invoked.
  - `test_cache_miss_on_new_version`: same ids but a new `b_version` means `call` is invoked once.
  - `test_budget_stops_adjudication`: `month_spend=19.999`, each call costing 0.002, means 0 adjudicated and
    `skipped_budget == len(pairs)`.
  - `test_verdict_rejects_bad_relation`: `Verdict(relation="maybe", …)` raises `ValidationError`.
  Run: FAIL.
- [ ] **Step 2: Implement** `adjudicate.py`. Run: PASS.
- [ ] **Step 3: Live check** on 3 real candidate pairs (one obvious exact Fed pair, one near, one different):
  `secrets uv run python -c "…"`, printing each verdict. Expected: relations match intuition and total cost is under
  $0.01. Paste the three reasons into the commit message body.
- [ ] **Step 4: Commit**: `git commit -m "Adjudicator: structured verdicts, version cache, monthly budget stop"`.

### Task 8: Match flow

**Files:**
- Create: `venue_arb/flows/match.py`, `sql_checks/adjudications_cover_candidates.sql`
- Test: `tests/test_match_flow.py` (integration)

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces flow `venue_arb.flows.match.match_flow(limit: int = 500) -> dict` (pipeline `venue_arb_match`): load
  current listings (`last_seen` within 2 h) → embed listings missing a row for their current `version` and insert
  them (guard: vector dimension 384) → candidates (rule + embed) → upsert `candidates` → `adjudicate.run` on up to
  `limit` pairs not yet adjudicated at current versions, highest score first → post-check
  `expect_rows(adjudications at current versions) ≥ 1` → `qa.sql.run_all(pattern="adjudications_*.sql", directory=SQL_CHECKS)` → `store.record`. The returned dict merges the
  `run()` result with `{"candidates": n}`.
- `adjudications_cover_candidates.sql` (severity `warn`, because the budget can legitimately leave gaps): returns
  candidates with score ≥ 0.9 that have no adjudication at current versions.

- [ ] **Step 1: Write `test_match_flow_second_run_is_cached`** (integration): runs `match_flow(limit=5)` twice.
  Asserts the second run's `adjudicated == 0` and `cached ≥ 1`. Run: FAIL.
- [ ] **Step 2: Implement.** Run: PASS. Cost of the test run under $0.05 (check `month_spend`).
- [ ] **Step 3: Commit**: `git commit -m "Match flow: embed, candidates, adjudicate with qa record"`.

### Task 9: Review loop and trust report

**Files:**
- Create: `venue_arb/matching/review.py`, `labels/known_pairs.csv`, `venue_arb/flows/review_cli.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Produces `venue_arb.matching.review`:
  - `BANDS = ((0.0, 0.8), (0.8, 0.95), (0.95, 1.01))`
  - `stratified_sample(rows: list[dict], n: int = 200, seed: int = 7) -> list[dict]`: rows are adjudications with
    `relation != 'different'`. Allocates `n` across `(category, band)` cells proportionally, with at least 5 per
    non-empty cell (or all the cell has, if fewer). Deterministic for a given seed.
  - `precision(labels: list[str]) -> tuple[float, int]`: `correct / (correct + wrong)` with `unsure` excluded,
    plus the count used.
  - `recall(known: list[tuple[str, str]], found: set[tuple[str, str]]) -> float`.
- `review_cli.py` (run as `secrets uv run python -m venue_arb.flows.review_cli`): shows one sampled pair at a time
  (both titles, rules excerpts, close times, verdict, and reason), reads `c`/`w`/`u` plus an optional note, and
  writes `reviews`. When the sample is done it prints overall precision and the count used, per-cell precision,
  and recall against `labels/known_pairs.csv`. It prints **PASS** when precision is at least 0.95 with at least 150
  labeled, otherwise **NOT YET**.
- `labels/known_pairs.csv` (`kalshi_market_id,pm_market_id,note`): every currently open FOMC-decision pair both
  venues list, plus BTC/ETH price pairs with the same date and threshold, found by hand inspection. Mark it
  "draft: Sam to confirm" in the file's header comment.

- [ ] **Step 1: Write the failing tests:**
  - `test_stratified_sample_min_per_cell`: 3 cells with sizes 500/20/3 and `n=50` give each cell at least
    min(5, size) and a total of 50.
  - `test_stratified_sample_deterministic`: the same seed gives the same ids.
  - `test_precision_excludes_unsure`: `["correct"]*19 + ["wrong"] + ["unsure"]*5` gives `(0.95, 20)`.
  - `test_recall`: 2 of 4 known pairs found gives 0.5.
  Run: FAIL.
- [ ] **Step 2: Implement** `review.py` and `review_cli.py`. Build `known_pairs.csv`. Run: PASS.
- [ ] **Step 3: 👤 Hand to Sam**: run the CLI for a first sitting (about 50 pairs) and confirm `known_pairs.csv`.
  Phase 2 is done when the CLI prints PASS (spec §7). This step doesn't block Task 10.
- [ ] **Step 4: Commit**: `git commit -m "Review loop: stratified sample, precision/recall trust report"`.

### Task 10: Snapshot collector and schedules

**Files:**
- Create: `venue_arb/books.py` (pure), `venue_arb/collect.py`, `venue_arb/flows/serve.py`,
  `deploy/venue-arb-collector.service`, `deploy/venue-arb-flows.service`, `sql_checks/book_levels_in_unit.sql`,
  `sql_checks/book_levels_fresh.sql`, `README.md`
- Test: `tests/test_books.py`, `tests/test_collect.py`

**Interfaces:**
- Consumes: both sources' `fetch_book`/`parse_book`, `Level`, the `candidates`/`adjudications`/`listings` tables.
- Produces `venue_arb.books`:
  - `watchlist(rows: list[dict], cap: int = SNAPSHOT_MAX_MARKETS) -> list[tuple[str, str, str | None]]`
    `(venue, market_id, yes_token)`: both legs of every candidate pair, ordered by adjudicated `exact` first, then
    `near`, then unadjudicated candidates by score. Excludes `different`, de-duplicates, and truncates at `cap`.
  - `to_rows(venue: str, market_id: str, ts: datetime, levels: list[Level]) -> list[dict]` (ClickHouse
    `book_levels` rows).
- Produces `venue_arb.collect.run_forever(interval_s: int = SNAPSHOT_INTERVAL_S, flush_every: int = 5)`: each tick
  it rebuilds the watchlist every 10 ticks, fetches each book (Kalshi by `market_id`, Polymarket by `yes_token`),
  and buffers rows. A failure on one market or venue is logged and counted, never raised. Every `flush_every` ticks
  it runs guards (`expect_schema`; `expect_rows ≥ 1` **per venue that was on the watchlist**), loads to ClickHouse,
  runs `qa.sql.run_all(pattern="book_levels_*.sql", directory=SQL_CHECKS)`, and calls `store.record` (pipeline `venue_arb_books`). A failed guard
  for one venue drops that venue's buffer and records the failure. The other venue still loads.
- SQL checks: `book_levels_in_unit.sql` (error) returns rows with `price <= 0 OR price >= 1 OR size <= 0`.
  `book_levels_fresh.sql` (error) returns a row if the newest `ts` for any venue is more than 10 minutes old.
- `flows/serve.py`: `prefect.serve(catalog_flow.to_deployment("catalog", cron="5 * * * *"), match_flow.to_deployment("match", cron="20 * * * *"))`.
- `deploy/*.service`: systemd **user** units (`Restart=always`, `RestartSec=30`, `WorkingDirectory=%h/projects/venue-arb`)
  running `secrets uv run python -m venue_arb.collect` and `secrets uv run python -m venue_arb.flows.serve`. Plan
  deviation from spec §6, which says "containers": systemd user units reuse the `secrets` wrapper without putting
  database passwords into Docker. Same effect: long-running, restarted on failure, nothing listening on a port.

- [ ] **Step 1: Write the failing tests:**
  - `test_watchlist_order_and_cap`: exact pairs come before near pairs before unadjudicated ones; `different` is
    excluded; `cap=3` gives 3 entries.
  - `test_to_rows_shape`: keys equal the `book_levels` columns and `side` stays a string.
  - `test_collect_one_venue_down` (in `test_collect.py`, fakes for fetchers and loader): the Polymarket fetcher
    raises on every call, so a flush loads Kalshi rows, records a failed guard for polymarket, and doesn't raise.
  Run: FAIL.
- [ ] **Step 2: Implement** `books.py` and `collect.py` (the loader and recorder are injected so the test runs without
  the stack). Run: PASS.
- [ ] **Step 3: Install and start the units**: `cp deploy/*.service ~/.config/systemd/user/ && systemctl --user
  daemon-reload && systemctl --user enable --now venue-arb-collector venue-arb-flows && loginctl enable-linger
  slee19`. (If `enable-linger` needs sudo, 👤 ask Sam to run it.)
- [ ] **Step 4: Verify after 15 minutes**: `SELECT venue, count(), max(ts) FROM venue_arb.book_levels GROUP BY venue`
  shows both venues with `max(ts)` within the last 5 minutes. `qa.check_results` has passing
  `venue_arb_books` rows. `systemctl --user status` shows both units active.
- [ ] **Step 5: Write `README.md`**: what the project is (one paragraph, pointing to the spec), how to run each flow,
  the review CLI, the systemd units, and the weekly Schwab note (not used yet). Commit:
  `git commit -m "Snapshot collector, flow schedules, systemd units"`.
