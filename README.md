# venue-arb

When Kalshi and Polymarket list the same contract, do their prices leave money on the table after taker fees and
order-book depth? venue-arb catalogs both venues, matches equivalent contracts with rules and an LLM adjudicator,
snapshots order books for the matched pairs every 60 seconds, and measures the gap. It places no orders and has no
code path that calls an order, account or position endpoint. Design: `docs/superpowers/specs/2026-10-19-venue-arb-design.md`.

## What it measures

Listings from both venues are placed in a shared hierarchy (category, event, market, outcome) and compared only
inside a branch. Matching runs top-down: deterministic rules where a venue exposes structure (Kalshi tickers,
Polymarket slugs, a 25 bp grid for Fed decisions), then an LLM adjudicator for the rest. Each pair gets a verdict
and a plain-words reason.

- **Exact matches** (both Yes outcomes pay in the same states of the world): scored as arbitrage. Both ask ladders
  are walked together with taker fees on each leg, so the edge is net of fees and of the depth available at that
  price. An episode is a net edge of at least $0.01 per $1 contract, at least $100 executable, in two or more
  consecutive snapshots.
- **Near matches** (the Yes outcomes overlap but differ in window, threshold or resolving authority): priced as
  relative value, with the difference stated, and never called arbitrage.
- **Same-instant crypto ladders** (Kalshi `KXBTCD`/`KXETHD` against Polymarket "above X" contracts that settle at
  the same instant): a model-free tier. A cross-strike spread pays in every state only if the two settlement
  indices (CF Benchmarks BRTI and the Binance candle close) stay within a measured basis bound, so each spread is
  checked against a bound taken from 30 days of hourly differences.
- **The honest denominator.** Every admissible check is counted, scored or not, so a count of opportunities always
  has the number of looks it came from. The screened (hourly history) and executable (our own books) tiers are
  never combined, and screened rows are never called arbitrage.

## Results so far

From `reports/research-note-v1.md`, generated 2026-10-20. This is v1: the executable window is 16.1 hours, and the
note is re-run once about four weeks of snapshots exist.

- **Executable tier.** 14,475 aligned pair-snapshots over 15 exact Fed-decision pairs (`KXFEDDECISION`), 60 s
  cadence. 3 opportunity episodes (median duration 121 min, median capacity $834); the largest top-of-book edge
  after fees was 0.0192 per $1 against the 0.01 threshold. 0 of the 3 are executable within the US, because
  Polymarket International blocks US trading. 16 hours says little about weeks.
- **Screened tier** (hourly history, not executable). Median net gap -0.032; 3.5% of 27,043 rows reached
  0.01, and 31.4% of those fall in a pair's first 7 days. The count is biased in both directions (the one-contract
  fee ceiling pushes it down, the Polymarket mid/last price pushes it up).
- **Ladders.** No ladder snapshots existed at generation time, so no spread has been scored: an absence of data,
  not a negative result. The measured basis is already informative: BRTI sits below Binance at the settle by about
  6 bps on average (BTC -$48.06, ETH -$1.52), so same-strike pairs are not arbitrage. The 0.999 bounds are BTC
  +$182 / -$152 and ETH +$6 / -$7 per settle.
- **Limits.** 0 of 15 pairs are human-reviewed; the Kalshi fee coefficient is unverified (below); nothing here
  generalizes beyond Fed decisions yet.

## Live dashboard

`https://<server>` (placeholder, filled in at deploy). While the venues' data terms are unresolved the page does
not show quotes: it shows edges, capacities and episodes, and the per-leg book boxes collapse to one derived cost
panel.

## Architecture

```
OptiPlex (home, outbound only)                      Oracle Always Free VM (public)
┌─────────────────────────────────┐   rsync/ssh    ┌──────────────────────────────────┐
│ collector (60 s books)          │ ─────────────▶ │ /srv/venue-arb/bundle/live.json  │
│ Prefect flows (hourly scoring)  │   every 60 s   │ /srv/venue-arb/bundle/history/   │
│ publish loop:                   │   + hourly     │   pairs-YYYYMMDDHH.parquet       │
│   live bundle (≈460 KB JSON)    │                │ app: FastAPI + DuckDB + static   │
│   history Parquet (hourly)      │                │ Caddy (auto-HTTPS) → :443        │
└─────────────────────────────────┘                └──────────────────────────────────┘
```

Collector, Postgres, ClickHouse and Prefect run at home; the server never connects back. The server holds no
database: the bundle directory is the state, and its one computation (`/api/walk`) imports `venue_arb.edges`, so
the walk is a single implementation. Repo layout: `venue_arb/` (pipeline, `publish.py` producer), `dashboard/`
(`api/`, `static/`, `sample/`), `deploy/` (OptiPlex units; `deploy/server/` for the VM), `docs/`, `reports/`,
`labels/`, `sql_checks/`, `tests/`.

## How to run

### Setup

Follow `~/platform/docs/NEW_PROJECT.md` (platform checkout, `secrets` wrapper, `mise`, running stack via
`cd ~/platform && just up`).

### Run

Adjudicator prompt v3: `near` needs overlapping Yes outcomes; mutually exclusive brackets of one meeting (hold vs +25) are `different`; Fed moves are on a 25 bp grid, so off-grid and rounding clauses are tail risks.

Fed rate-decision pairs (Kalshi `KXFEDDECISION-*` vs a Polymarket bracket title) skip the LLM: `matching/fed_rule.py` compares the two outcome sets on the 25 bp grid (equal = exact, overlapping = near, disjoint or different meeting = different) and stores them with `model = 'rule:fed-v1'`, cost 0 and no `llm_attempts` row. Every other pair still goes to the LLM adjudicator.

```bash
secrets uv run python -m venue_arb.flows.catalog      # listings from both venues
secrets uv run python -m venue_arb.flows.match        # candidates + LLM adjudication (re-judges pairs stored under an older prompt version)
secrets uv run python -m venue_arb.flows.review_cli   # hand review of adjudicated matches
secrets uv run python -m venue_arb.flows.score        # exact pairs: executable edges (ClickHouse) + opportunity episodes (Postgres)
secrets uv run python -m venue_arb.flows.ladder_score # same-instant crypto ladders: spreads -> venue_arb.ladder_edges + episodes (source 'ladder')
secrets uv run python -m venue_arb.flows.screened     # screened tier: hourly history gaps -> venue_arb.screened_v2 (by hand)
secrets uv run python -m venue_arb.report.note  # research note v1 -> reports/research-note-v1.md + PNGs (re-run after ~4 weeks of snapshots for v1-final)
secrets uv run python -m venue_arb.collect            # order-book snapshot collector (runs forever)
secrets uv run python -m db.migrate                   # apply migrations
```

`venue_arb.screened` (v1, paired each Kalshi candle close with the Polymarket print about an hour earlier) was
dropped by hand on 2026-10-20; `venue_arb.screened_v2` is the only screened table.

LLM spend is capped at $20/month (`MONTHLY_LLM_BUDGET_USD`) and paced daily, so a backlog is worked off over
the month instead of in one day.

### Dashboard

```bash
BUNDLE_DIR=dashboard/sample uv run uvicorn dashboard.api.app:app
```

`dashboard/sample` is a synthetic bundle (invented prices and times), so this runs anywhere without the stack. The
server's setup is in `deploy/server/README.md`.

## Tests

```bash
uv run pytest -m "not integration"      # unit: no secrets, no stack (what CI runs, after `uv run ruff check .`)
secrets uv run pytest -m integration    # needs the stack up
```

## Operations

### Services

On the OptiPlex, three long-running systemd user units: `venue-arb-collector` (order-book snapshots),
`venue-arb-publish` (dashboard bundle, below) and `venue-arb-flows` (`prefect.serve` of five hourly deployments: `catalog` at :05, `match` at :20, `basis` at :35,
`score` at :40, which scores the last 26 h of books, and `ladder_score` at :50, which scores the last 3 h of same-instant
ladder books; overlapping windows re-score idempotently). Apply migrations (`db.migrate`) before restarting
`venue-arb-flows` after a pull: `score` reads `opportunities.source` (pg/006). Schedules, retries and run
history are in the Prefect UI (Deployments); to pause one, use its toggle there or
`prefect deployment pause '<flow>/<name>'`, undo with `prefect deployment resume '<flow>/<name>'` (e.g.
`venue_arb_catalog/catalog`; `prefect deployment ls` shows the exact names). A pause lasts only until `venue-arb-flows` restarts
(verified: a restart unpauses all of them; it restarts on reboot and via `Restart=always`). To stop a job durably,
remove it from `serve.py` and restart the unit, or stop the unit (stops all of them). Use pause/resume for short pauses.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/*.service ~/.config/systemd/user/ && systemctl --user daemon-reload
# one-off when migrating from the old timers:
systemctl --user disable --now venue-arb-{catalog,match,score}.timer; rm -f ~/.config/systemd/user/venue-arb-{catalog,match,score}.{service,timer}
systemctl --user enable --now venue-arb-collector venue-arb-flows venue-arb-publish
loginctl enable-linger "$USER"          # keep them running after logout
journalctl --user -u venue-arb-collector -u venue-arb-flows -u venue-arb-publish -f
```

systemd doesn't load `mise.toml`, so each unit repeats its non-secret `[env]` values as `Environment=` lines
(`tests/test_deploy.py` fails if they drift). Without `PGUSER`/`CLICKHOUSE_USER` the services would log in as the
database superusers. After installing, check they connect as the project role:

```bash
systemctl --user show venue-arb-collector venue-arb-flows venue-arb-publish -p Environment | tr ' ' '\n' \
  | grep -E '^(PGUSER|CLICKHOUSE_USER|PREFECT_API_URL)='
cd ~/platform && docker compose exec postgres psql -U postgres -d app -c \
  "SELECT usename, count(*) FROM pg_stat_activity WHERE datname = 'app' GROUP BY 1"   # expect venue_arb, not postgres
```

### Dashboard publisher

`venue-arb-publish` (`python -m venue_arb.publish`) writes `live.json` every 60 s and one history Parquet per clock
hour, then pushes them outbound with rsync to `VENUE_ARB_PUBLISH_DEST` (`venue-arb@host:./`; rrsync on the server takes the path relative to the bundle dir, so an absolute path would land in `bundle/srv/...`). The value
in `mise.toml [env]` and the three units is the placeholder `venue-arb@SERVER_IP:./` (a shared
env var; only the publisher reads it, and `tests/test_deploy.py` needs it identical everywhere). Once the server
exists, set the real host on the box in both `mise.toml` and the unit (the `venue-arb-push-host` ssh alias of
`deploy/server/README.md` step 4), then `daemon-reload` and restart.

Backfill history (oldest hour first, idempotent; pushes at the end when a destination is set):

```bash
secrets uv run python -m venue_arb.publish history --hours 168
```

Without a server, unset the destination and the publisher only writes locally; this also works on a laptop:

```bash
env -u VENUE_ARB_PUBLISH_DEST secrets uv run python -m venue_arb.publish   # add `history --hours N` to backfill
```

The local bundle lands in `data/bundle/` (`live.json`, `history/`); `--out DIR` changes it.

Snapshots land in ClickHouse `venue_arb.book_levels_v2` (daily partitions, 90-day TTL). The original unpartitioned
`venue_arb.book_levels` was never written to and was dropped by hand on 2026-10-20.

### Schwab

Schwab data is not used yet. The Trader API app has the Market Data product only, and its refresh token needs a
manual browser re-login every 7 days.

## Data terms and caveats

Quotes are not displayed publicly: `DASHBOARD_SHOW_BOOKS = False`, because Kalshi's data terms forbid public
display without written authorization and Polymarket's terms could not be read. Kalshi's taker fee coefficient
(0.07) is secondhand and unverified, as are the jurisdictions each venue admits; both are recorded with sources in
`docs/venues.md`, and the going-public checklist is `docs/going-public.md`.
