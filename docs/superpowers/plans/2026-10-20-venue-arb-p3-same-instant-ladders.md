# venue-arb Plan 3: same-instant crypto ladders

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test, without any pricing model, whether Kalshi and Polymarket crypto price ladders that settle at the same
instant are consistent with each other net of fees, depth and the measured settlement-source gap.

**Architecture:** A deterministic crypto parser (from the bake-off's `venue_arb/bakeoff/truth.py`) groups both venues'
"price above X at instant T" markets into same-instant ladders. The collector snapshots each ladder's strikes within the
range both venues cover. A ladder scorer walks two-leg spreads (Kalshi "≥ a" with Polymarket "not above b", and the
reverse) that pay at least $1 in every outcome once the measured source basis is respected, and reuses the Plan 2
edge walk, episodes, opportunities table and note.

**Tech Stack:** as Plans 1–2.

**Spec:** `docs/superpowers/specs/2026-10-19-venue-arb-design.md`, amended by the decision below (Sam, 2026-10-20).

## Decision this plan implements (Sam, 2026-10-20)
- What we're trying to prove: cross-venue prices are inconsistent in a way a skeptical reader can't dismiss as a modelling
  choice. So only **same-instant** pairs count as evidence: both markets settle at the same moment (Kalshi's close_time
  equals its settlement instant — "60-second average of CF Benchmarks BRTI before H"; Polymarket's hourly "Bitcoin
  above X on <date>, <h>AM ET?" settles on the Binance candle ending at H; its daily ladder at noon ET).
- Pairs with a settlement-time gap need a volatility model, so they are **not** part of the proof. Out of scope here.
- Evidence types: (1) same-strike near-exact pairs; (2) cross-strike **ladder monotonicity**: P(S ≥ a) ≥ P(S > b) for
  a ≤ b, so buying Kalshi "≥ a" Yes and Polymarket "above b" No pays ≥ $1 in every state.
- Snapshot 2026-10-20 ~00:30 PT: 2 same-instant ladders (BTC, ETH @ 4 AM ET), 36 same-strike pairs, 1,312 cross-strike
  checks. Kalshi ladders are wide (BTC 188 strikes, ETH 300): only strikes inside Polymarket's range are snapshotted.

## Global Constraints
- Everything in Plans 1–2's Global Constraints still holds (platform hard rules, qa on every load, pure modules never
  import qa, read-only public endpoints, no VPN, no paid data, no LLM spend for this plan, commits without a Claude
  co-author trailer, CI = `uv run ruff check .` + `env -u PYTHONPATH uv run pytest -m "not integration" -q`).
- Prices are executable asks from our own book snapshots, never mid/last trade. History-based numbers stay a separate
  screened tier.
- A spread counts only when it pays ≥ $1 in **every** state after the settlement-source basis: the strikes must satisfy
  `a + B ≤ b` where `B` is the basis bound (Task 2). Same-strike pairs (a = b) count only if `B` = 0 rounds away — they
  are reported as near-exact with their basis exposure, not as arbitrage.
- Threshold semantics are exact: Kalshi "$X or above" = BRTI > X − 0.01 (ticker T{X−0.01}); Polymarket "above X" =
  Binance close > X (verify strict/non-strict on the market rules and record it).
- Live hourly path stays on CPU; nothing here uses Colab.

## Review Focus
1. **Strict vs non-strict thresholds** (Kalshi "> X−0.01" vs Polymarket "> X"): a one-cent boundary must never be counted
   as overlap that isn't there. Test in Task 1 (`test_threshold_semantics_boundary`).
2. **Basis sign and bound**: a spread is admitted only if it pays in every state given |BRTI − Binance| ≤ B; a test with
   a ≤ b < a + B must be rejected. Test in Task 3 (`test_spread_requires_basis_gap`).
3. **Ladder at settlement**: after the instant, books go stale/empty; no edge may be scored at or after T. Test in Task 4
   (`test_no_edges_at_or_after_settlement`).
4. **Watchlist cap**: one ETH Kalshi ladder alone has 300 strikes; ladder strikes must be range-clipped and capped per
   ladder so Fed pairs keep their slots. Test in Task 2b (`test_watchlist_ladder_clip_and_cap`).
5. **Polymarket hourly ladders that lack a Kalshi partner** at that instant produce no rows (not errors). Test in Task 1.

---

### Task 1: Crypto ladder parser and same-instant grouping

**Files:** Create `venue_arb/matching/crypto_rule.py` (pure; move the parsing from `venue_arb/bakeoff/truth.py`, which then
imports it); Test `tests/test_crypto_rule.py`.

**Interfaces:**
- `Strike = NamedTuple(venue: str, market_id: str, yes_token: str | None, asset: Literal["BTC","ETH"], instant: datetime, op: Literal["gt"], level: float)`
  — normalized so Kalshi "$X or above" → `level = X − 0.01` and Polymarket "above X" → `level = X` (op is always
  strictly greater-than after normalization; verify both venues' rule text and record it in the docstring).
- `parse_strike(row: dict) -> Strike | None` for listing rows (keys as `venue_arb.listings`). Settlement instant:
  Kalshi `close_time`; Polymarket hourly → the hour in the title (ET); Polymarket daily "above/greater than" → 12:00 ET.
  Ranges, "below/less than", Up/Down and other shapes → None.
- `ladders(rows: list[dict]) -> dict[tuple[str, datetime], dict[str, list[Strike]]]` — keyed by (asset, instant), only
  keys with both venues, each venue's strikes sorted by level.
- `clip(ladder, pad: float) -> ladder` — Kalshi strikes limited to [pm_min − pad, pm_max + pad].

- [ ] Tests first: parsing of real titles/labels from `tests/fixtures` (save a trimmed listings fixture with both venues,
  hourly and daily); `test_threshold_semantics_boundary` (Kalshi "$80,000 or above" → 79,999.99; PM "above $80,000" →
  80,000; ordering correct); `test_ladders_only_same_instant` (4 AM vs 5 AM never grouped); `test_ladders_need_both_venues`;
  `test_clip`. Run: FAIL → implement → PASS. Commit: `git commit -m "Crypto ladder parser and same-instant grouping"`.

### Task 2: Settlement-source basis (BRTI vs Binance)

**Files:** Create `venue_arb/basis.py` (pure), `venue_arb/flows/basis.py`, `db/migrations/ch/006_basis.sql`; Modify
`venue_arb/sources/kalshi.py` (`fetch_settled(series, min_close_ts) -> list[dict]` paging `/markets?series_ticker=…&status=settled`),
add `venue_arb/sources/binance_archive.py` (`klines_1m(symbol, day) -> list[dict]` from
`https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-{YYYY-MM-DD}.zip`, verified with checksum file);
Test `tests/test_basis.py`.

**Interfaces:**
- Per settled Kalshi hourly event: BRTI settle = the event's `expiration_value` (one value per event instant).
- Binance comparison price at the same instant: the close of the 1-minute candle ending at H (and, separately, the 1-hour
  candle close ending at H — record which one Polymarket's rules name; use that one for B).
- ClickHouse `venue_arb.basis(instant DateTime('UTC'), asset LowCardinality(String), brti Float64, binance Float64, diff Float64) ENGINE = ReplacingMergeTree ORDER BY (asset, instant)`.
- `basis.bound(diffs: list[float], q: float = 0.999) -> float` — the basis bound B per asset = the q-quantile of |diff|
  over the last 30 days, rounded up to $1; `basis_flow(days: int = 30)` loads history and writes rows (pipeline
  `venue_arb_basis`, run hourly on the score timer's unit before scoring, idempotent).

- [ ] Tests first (fixtures of one settled event page and one kline zip): parse, join on instant, `bound` on a known list.
  Live run: report per asset n, mean, p99, p99.9, max |diff|, and B. Commit.

### Task 2b: Watchlist includes same-instant ladders

**Files:** Modify `venue_arb/books.py` (`watchlist(rows, ladder_markets, cap)`) and `venue_arb/collect.py`
(`load_watchlist` also builds ladders from current listings with `crypto_rule.ladders` + `clip(pad=B)`); Test `tests/test_books.py`.

- Order: exact pairs, then ladder strikes (per ladder at most `LADDER_CAP = 60` markets, nearest-to-PM-mid first), then
  near pairs, then unadjudicated by score; overall cap unchanged (`SNAPSHOT_MAX_MARKETS`).
- [ ] `test_watchlist_ladder_clip_and_cap`; existing tests green. Commit.

### Task 3: Ladder spreads (pure)

**Files:** Create `venue_arb/ladder_edges.py`; Test `tests/test_ladder_edges.py`.

**Interfaces:**
- Two spread directions per (Kalshi strike a, Polymarket strike b) at one aligned snapshot:
  - `k_yes_pm_no`: buy Kalshi Yes(> a) + buy Polymarket No(> b). Pays ≥ $1 in every state iff `a + B ≤ b`
    (if BRTI and Binance differ by at most B).
  - `pm_yes_k_no`: buy Polymarket Yes(> b) + buy Kalshi No(> a). Pays ≥ $1 in every state iff `b + B ≤ a`.
- `admissible(a: float, b: float, B: float) -> list[Direction]`.
- `score_spread(direction, k_levels, pm_levels, series_fee, pm_schedule, pm_fees_enabled, min_edge=0.01) -> Edge` —
  reuses `edges.walk` (fees per slice, marginal edge ≥ min_edge, capacity).
- Same-strike pairs (|a − b| < B) are scored as `near_exact` rows with their basis exposure B recorded, never counted as
  arbitrage episodes.
- [ ] Tests first: `test_spread_requires_basis_gap`; both directions pay ≥ $1 in every state on a grid of settlement
  prices when admissible (property-style test over S ∈ [a−3B, b+3B] with BRTI−Binance ∈ [−B, B]); a hand-computed
  two-level walk; fees from the Plan 2 fee model. Commit.

### Task 4: Ladder scoring flow

**Files:** Create `venue_arb/flows/ladder_score.py`, `db/migrations/ch/007_ladder_edges.sql`,
`sql_checks/ladder_edges_*.sql`; Modify `deploy/venue-arb-score.service` (run basis → score → ladder_score in order, one
oneshot) and README; Test `tests/test_ladder_flow.py` (integration).

- ClickHouse `venue_arb.ladder_edges(ts DateTime64(3,'UTC'), asset LowCardinality(String), instant DateTime('UTC'), k_id String, pm_id String, direction LowCardinality(String), kind LowCardinality(String) /* arb | near_exact */, a Float64, b Float64, basis_b Float64, best_edge Nullable(Float64), capacity_contracts Float64, capacity_usd Float64, avg_edge Nullable(Float64), fee_version String) ENGINE = ReplacingMergeTree PARTITION BY toYYYYMM(ts) ORDER BY (asset, instant, k_id, pm_id, direction, ts)`.
- Episodes: reuse `episodes.align` / `episodes.find` per (k_id, pm_id, direction, kind='arb'); write to the existing
  `venue_arb.opportunities` with kalshi_id/pm_id = the two strikes (add `source text NOT NULL DEFAULT 'pair'` via
  additive migration `pg/006_opportunities_source.sql`; ladder rows use `'ladder'`).
- `test_no_edges_at_or_after_settlement`; idempotency test (two runs, same counts); all qa steps as Plan 2's score flow.
- [ ] Live run once; report ladders, strikes snapshotted, spread rows, arb rows with capacity > 0, episodes. Commit.

### Task 5: Research note — ladder tier

**Files:** Modify `venue_arb/report/note.py`, tests.

- New Results subsection "Same-instant crypto ladders (model-free)": ladders observed, strikes, spread checks, share with
  positive executable edge after fees and basis, episodes (count, duration, capacity), and the near-exact same-strike
  table with basis exposure. Method: the same-instant rule, threshold normalization, the basis bound B with its data
  (n, quantile, window), and why gapped pairs are excluded. Limits: basis bound is empirical (tail risk beyond q), Kalshi
  fee coefficient secondhand, 60 s sampling.
- [ ] Tests for the new summary keys (tiers never mixed; empty-input path). Regenerate the note from live data. Commit.

## Out of scope (later plans)
Gapped-settlement pairs (model-based), Polymarket US, perps/stock basis, Binance/Bybit derivatives archives, the demo.
