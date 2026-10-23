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
- **Stock perpetuals** (a separate tier: Hyperliquid's trade.xyz perps against the stocks they track). Basis is the
  perp's mark minus a reference, in bps, with the reference and its age on every row. The reference is Schwab's bid/ask
  mid while that quote is fresh (at most 120 s old), else the venue's oracle, which is labelled "oracle only" and
  carries no executable verdict. The executable tier nets a 24-hour round trip: two taker fees, the perp's exit
  half-spread, the full stock spread, funding and borrow. An episode needs net ≥ 10 bps, at least $100 fillable and two
  consecutive snapshots. The weekend tier is a forecast test, not an arbitrage: how much of Monday's move the weekend
  perp had already made. Details: `docs/venues.md` (Hyperliquid), the design
  `docs/superpowers/specs/2026-10-22-venue-arb-stock-perp-basis-design.md`.
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
- **Stock perps.** No results yet: the tier is not deployed at this commit.
- **Limits.** 15 of 15 pairs human-reviewed; match review precision 1.00 on 31 labels (20 Oct 2026); the Kalshi fee coefficient is unverified (below); nothing here
  generalizes beyond Fed decisions yet.

## Live dashboard

`https://<server>` (placeholder, filled in at deploy), titled "venue-arb · Prediction Market Arbitrage", with three
tabs: **Scanner** (one row per matched pair, sortable, by category), **Explorer** (one pair's edge history, verdict
and cost) and **Research** (the research note's headline numbers, recomputed hourly). While the venues' data terms
are unresolved the page does not show quotes: it shows edges, capacities and episodes, and the per-leg book boxes
collapse to one derived cost panel. The stock-perp category is built but hidden (`DASHBOARD_SHOW_STOCK_PERPS = False`)
until the terms reads in `docs/going-public.md` are done.

## Architecture

```
OptiPlex (home, outbound only)                      Oracle Always Free VM (public)
┌─────────────────────────────────┐   rsync/ssh    ┌──────────────────────────────────┐
│ collector (60 s books)          │ ─────────────▶ │ /srv/venue-arb/bundle/live.json  │
│ Prefect flows (hourly scoring)  │   every 60 s   │ /srv/venue-arb/bundle/history/   │
│ publish loop:                   │   + hourly     │   pairs-YYYYMMDDHH.parquet       │
│   live bundle (~70–100 KB JSON) │                │ app: FastAPI + DuckDB + static   │
│   history Parquet (hourly)      │                │ Caddy (auto-HTTPS) → :443        │
└─────────────────────────────────┘                └──────────────────────────────────┘
```

The live bundle is ~70–100 KB today (research included). Collector, Postgres, ClickHouse and Prefect run at home;
the server never connects back. The server holds no
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
secrets uv run python -m venue_arb.flows.stock_perp_score    # stock-perp tier: collector books + Schwab quotes -> venue_arb.stock_perp_edges, episodes (source 'stock_perp'); hourly at :55
secrets uv run python -m venue_arb.flows.stock_perp_weekend  # weekend tier: one row per name per weekend -> venue_arb.stock_perp_weekends (Mondays 15:00 UTC in serve)
secrets uv run python -m venue_arb.flows.stock_perp_backfill # once at deploy, safe to re-run: 60 days of hourly candles and funding, daily Schwab opens
secrets uv run python -m venue_arb.flows.screened     # screened tier: hourly history gaps -> venue_arb.screened_v2 (by hand)
secrets uv run python -m venue_arb.report.note  # research note v1 -> reports/research-note-v1.md + PNGs (re-run after ~4 weeks of snapshots for v1-final)
secrets uv run python -m venue_arb.collect            # order-book snapshot collector (runs forever)
secrets uv run python -m venue_arb.collect_perps      # stock-perp collector: Hyperliquid books + contexts, Schwab quotes (runs forever)
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

On the OptiPlex, four long-running systemd user units: `venue-arb-collector` (order-book snapshots),
`venue-arb-perps` (stock-perp snapshots; the weekly Schwab login restarts it),
`venue-arb-publish` (dashboard bundle, below) and `venue-arb-flows` (`prefect.serve` of seven deployments, six hourly: `catalog` at :05, `match` at :20, `basis` at :35,
`score` at :40, which scores the last 26 h of books, `ladder_score` at :50, which scores the last 3 h of same-instant
ladder books, and `stock_perp_score` at :55, which scores the last 3 h of stock-perp books. The seventh,
`stock_perp_weekend`, runs Mondays at 15:00 UTC. Overlapping windows re-score idempotently. Apply migrations (`db.migrate`) before restarting
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
systemctl --user enable --now venue-arb-collector venue-arb-perps venue-arb-flows venue-arb-publish
loginctl enable-linger "$USER"          # keep them running after logout
journalctl --user -u venue-arb-collector -u venue-arb-perps -u venue-arb-flows -u venue-arb-publish -f
```

systemd doesn't load `mise.toml`, so each unit repeats its non-secret `[env]` values as `Environment=` lines
(`tests/test_deploy.py` fails if they drift). Without `PGUSER`/`CLICKHOUSE_USER` the services would log in as the
database superusers. After installing, check they connect as the project role:

```bash
systemctl --user show venue-arb-collector venue-arb-perps venue-arb-flows venue-arb-publish -p Environment | tr ' ' '\n' \
  | grep -E '^(PGUSER|CLICKHOUSE_USER|PREFECT_API_URL)='
cd ~/platform && docker compose exec postgres psql -U postgres -d app -c \
  "SELECT usename, count(*) FROM pg_stat_activity WHERE datname = 'app' GROUP BY 1"   # expect venue_arb, not postgres
```

### Stock perps

First deploy, once Sam has approved the merge of `p6-stock-perps` into `main`:

0. **Update the VM first** (`deploy/server/README.md`, Update section), then pull and restart on the OptiPlex. Every
   bundle now carries the stock-perp keys (null while `DASHBOARD_SHOW_STOCK_PERPS` is off), and the server validates
   `live.json` with `extra="forbid"` (`dashboard/api/store.py`): an un-updated VM rejects the new bundles and the page
   freezes on the last one it accepted.

On the OptiPlex:

1. Fast-forward `main`, then `uv sync`.
2. `secrets uv run python -m db.migrate`: ClickHouse `008` creates `stock_perp_books`, `_ctx`, `_refs`, `_candles` and
   `_edges`; Postgres `007` creates `stock_perp_weekends`.
3. `cp deploy/venue-arb-perps.service ~/.config/systemd/user/`, then replace its `Environment=` block with the one in
   the box's installed collector unit (`~/.config/systemd/user/venue-arb-collector.service`): the installed unit
   carries the real publish destination, the committed one a `SERVER_IP` placeholder. `tests/test_deploy.py` only
   checks that the committed units agree with each other, not the installed ones. Then
   `systemctl --user daemon-reload && systemctl --user enable --now venue-arb-perps`.
4. Sam runs the Schwab login once, from the runbook in the Schwab section below. It restarts `venue-arb-perps` itself.
5. `secrets uv run python -m venue_arb.flows.stock_perp_backfill`. It exits 1 if any symbol failed, and names it.
6. `systemctl --user restart venue-arb-flows`. `serve` registers `stock_perp_score` and `stock_perp_weekend`, and a
   restart unpauses every deployment.
7. Run `secrets uv run python -m venue_arb.flows.stock_perp_score` and `... stock_perp_weekend` once by hand. Check
   their results in `qa.check_results` (Postgres) for the pipelines `venue_arb_stock_perp_score` and
   `venue_arb_stock_perp_weekend`.
8. Leave `DASHBOARD_SHOW_STOCK_PERPS = False` in `venue_arb/config.py`. The collector, scoring and bundle fields run
   regardless.

Turning the category on, only after the first week of oracle-check reports (`docs/going-public.md`):

1. Deploy the VM first (`deploy/server/README.md`, Update). The server validates the bundle against the new models.
2. One-line commit setting `DASHBOARD_SHOW_STOCK_PERPS = True`, then `systemctl --user restart venue-arb-publish`.
3. Sam reviews `copy.METHOD["stock_perps"]` and `copy.HEADLINE["stock_perps"]` on the rendered page. Wording fixes go in
   `venue_arb/report/copy.py` only.

### Dashboard publisher

`venue-arb-publish` (`python -m venue_arb.publish`) writes `live.json` every 60 s and one history Parquet per clock
hour, and once an hour recomputes the research summary (the note's numbers, in `live.json`'s `research` block for the
Research tab; a failure keeps the previous one), then pushes them outbound with rsync to `VENUE_ARB_PUBLISH_DEST` (`venue-arb@host:./`; rrsync on the server takes the path relative to the bundle dir, so an absolute path would land in `bundle/srv/...`). The value
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

Stock reference quotes come from Schwab Market Data (Market Data product only) and are private: they feed the full
two-leg stock-perp tier and the oracle check below, never the public page or any pushed file. The refresh
token expires every 7 days; re-issue it with `python -m venue_arb.sources.schwab login`. The runbook, token lifetimes
and the terms record are in `docs/venues.md`.

### Public oracle view and private validation

The public Stock perps view uses Hyperliquid data only: the perp's mark against the trade.xyz venue oracle
(`oracle_basis_bps`), perp-side costs, depth and funding. Outside a name's external session the oracle is the venue's
own average, so those rows read "oracle internal" and carry no verdict. Oracle-mode episodes are stored with
`pm_id = 'oracle'` and `source = 'stock_perp_oracle'`.

The `stock_perp_oracle_check` deployment (cron `30 1 * * 2-6`, registered by `venue_arb/flows/serve.py`; restart
`venue-arb-flows` to pick it up) compares the oracle with the Schwab mid per name and day. It writes ClickHouse
`venue_arb.stock_perp_oracle_check` (migration `ch/009`) and the Markdown report
`data/private/oracle-check-YYYY-MM-DD.md` (git-ignored; never pushed), and warns `oracle_drift` when a name's trailing
5-day p95 exceeds 10 bps. Run by hand:

```bash
secrets uv run python -m venue_arb.flows.stock_perp_oracle_check
```

No name is hidden automatically. After a week of reports, remove a name from the public page by hand in
`labels/stock_perps_excluded.txt` with a neutral comment. Asian exchange holidays are not in the session calendar, so
on such a day a name may read external against a stale oracle.

## Data terms and caveats

Quotes are not displayed publicly: `DASHBOARD_SHOW_BOOKS = False`, because Kalshi's data terms forbid public
display without written authorization and Polymarket's terms, read on 2026-10-23, restrict the data (`docs/venues.md`, terms record). Kalshi's taker fee coefficient
(0.07) is secondhand and unverified, as are the jurisdictions each venue admits; both are recorded with sources in
`docs/venues.md`, and the going-public checklist is `docs/going-public.md`. The stock-perp figures are held back for the same reason:
the public view is held behind `DASHBOARD_SHOW_STOCK_PERPS` until the oracle-check reports are read. Schwab's agreement
bars third-party use of its data, so nothing derived from it is published (terms record in `docs/venues.md`, a reading,
not legal advice).
