# venue-arb

Cross-venue market matching and arbitrage research. When two venues price the same thing, is there money left after
fees and depth, and for whom? venue-arb catalogs the venues, matches equivalent contracts with rules and an LLM
adjudicator, snapshots order books every 60 seconds, scores the gap net of taker fees and fillable size, and publishes
the derived figures on a live page. It places no orders and has no code path that calls an order, account or position
endpoint on any venue. Design: `docs/superpowers/specs/2026-10-19-venue-arb-design.md`.

## What it asked

Three questions, each with its own tier of data:

1. **Prediction markets.** When Kalshi and Polymarket list the same contract, do their prices leave money on the table
   after taker fees and order-book depth, and can a US person take it?
2. **Stock perpetuals.** Do Hyperliquid's trade.xyz stock perps trade away from the stocks they track by more than a
   round trip costs?
3. **Pre-IPO perps.** Several venues price the same unlisted company (Anthropic on five markets). Is the gap between
   them a spread?

## What it found

- **No executable cross-venue edge after fees for a US person on prediction markets.** From
  `reports/research-note-v1.md` (generated 2026-10-22, 15 human-reviewed exact Fed-decision pairs, 60 s cadence):
  62,099 aligned pair-snapshots over 69.1 h gave 13 opportunity episodes, with the largest top-of-book edge after fees at
  0.0432 per $1 against the 0.01 threshold, and 0 of the 13 executable within the US, because Polymarket International
  blocks US trading. The hourly screened tier (not executable) has a median net gap of -0.032 and 3.5% of 27,043 rows
  at or above 0.01, a count biased in both directions. On same-instant crypto ladders, 2 of 1,469,130 admissible
  cross-strike checks cleared 0.01 after fees and 0 episodes formed in 47.6 h; the two settlement indexes (BRTI, Binance
  close) sit about 6 bps apart at the settle, so same-strike pairs are not arbitrage. Nothing generalizes beyond Fed
  decisions yet; the note is re-run once about four weeks of snapshots exist.
- **Stock-perp basis on trade.xyz tracks the venue oracle within a few bps.** The public view scores the perp's mark
  against the venue oracle, net of two taker fees (18 bp on a standard name, under 2 bp in growth mode), the exit
  half-spread and 24 h of funding, before any stock-leg cost; an episode needs net ≥ 10 bps, $100 of perp depth and two
  consecutive snapshots. Outside a name's external session the oracle follows the perp itself, so those rows are never
  scored. A private daily check (below) found the oracle within about 10 bps p95 of the consolidated market for the
  typical name during regular hours, which is why the public view can use the oracle as its reference at all. The live
  count of episodes is on the page's Research tab; the stock perp is not executable from the US in any case.
- **The pre-IPO cross-unit gap is a share-count assumption, not a spread.** `docs/research/2026-10-pre-ipo-divergence.md`
  (2026-10-27): Anthropic trades per share on trade.xyz and Paragon (115.32 vs 115.05, a 0.2% gap) and in billions of
  dollars of market cap on Entropy, Binance and Coinbase (1,876 to 1,884, a 0.3% gap). No venue publishes a share count
  and the S-1 leaves it blank, so the cross-unit gap moves from +1.2% to +6.2% across defensible counts, 20 times any
  same-unit divergence. It becomes a spread the day a count exists (the 424B4, or a venue's rebase); until then the only
  honest row is same-unit, and the one qualifying public pair (`xyz:ANT` vs `para:ANTH`) has about $2.3K of open
  interest on the Paragon leg, so it is informational.

## How it knows

- **Collectors.** `venue_arb.collect` snapshots Kalshi and Polymarket order books for every matched pair every 60 s
  (top 10 levels a side) into ClickHouse `venue_arb.book_levels_v2`; `venue_arb.collect_perps` does the same for the
  trade.xyz perps from Hyperliquid's public info endpoint, plus private Schwab reference quotes that never leave the
  OptiPlex. Public read endpoints only, under each venue's documented rate limits (`docs/venues.md`).
- **Matching.** Listings from both venues are placed in a shared hierarchy (category, event, market, outcome) and
  compared only inside a branch. Deterministic rules go first where a venue exposes structure (Fed decisions on a 25 bp
  grid, crypto strikes by asset, strike and settlement instant); the rest goes to an LLM adjudicator at a monthly
  spend cap, with hand review sittings (precision 1.00 on 31 labels, 2026-10-20). Each pair gets a verdict and a
  plain-words reason.
- **Scoring tiers, never combined.** *Exact* pairs (both Yes outcomes pay in the same states) are scored as arbitrage
  by walking both ask ladders together with taker fees on each leg; *near* pairs are priced as relative value and never
  called arbitrage; *same-instant crypto ladders* are a model-free tier where a cross-strike spread counts only if the
  strikes sit further apart than the 99.9th percentile of 30 days of settlement-index differences; the *screened* tier
  (hourly history) says where gaps tended to appear, not that they were executable; *stock perps* score the perp
  against the venue oracle; *weekends* test how much of Monday's move the weekend perp had already made. Every
  admissible check is counted, scored or not, so a count of opportunities always has the number of looks it came from.
- **Oracle check.** A private daily flow compares the trade.xyz oracle with the stock reference per name and day,
  during regular and extended sessions separately, and warns when a name's trailing 5-day regular-session p95 exceeds
  10 bps. Its reports live in `data/private/` and are never pushed; a name leaves the public page only by hand, with a
  neutral comment (Operations, below).
- **CI and observability.** Every push runs `uv run ruff check .` and `uv run pytest -m "not integration"` in a CI
  worktree on the OptiPlex before it can fast-forward `main`; the unit suite needs no secrets and no stack, and pins
  the deploy units, the page copy, the Schwab-free rule for every public file and the bundle schema. Flows run under
  Prefect with per-run checks recorded in `qa.check_results` (the SQL checks are in `sql_checks/`); error-severity
  checks reach Telegram through the platform alerter; the publisher stamps every bundle with the last green run, and the
  page greys its figures when the bundle goes stale.

## Live dashboard

<https://34.127.95.87/> (a bare IP with a self-signed certificate until a domain is attached; `docs/going-public.md`),
titled "venue-arb · Prediction Market Arbitrage", with three tabs: **Scanner** (one row per matched pair, sortable, by
category), **Explorer** (one pair's edge history, verdict and cost) and **Research** (the research note's headline
numbers, recomputed hourly). The page shows edges, capacities, bases and episodes, never quotes or order books: the
per-leg book boxes collapse to one derived cost panel. The stock-perp category is public since 2026-10-25
(`DASHBOARD_SHOW_STOCK_PERPS = True`); pre-IPO names show price and funding only.

## Terms

Every venue's terms have been read and recorded with dates and quotes: `docs/terms.md` is the one table (document,
date read, what it allows for read-only collection, display of derived figures, redistribution, US persons, and the
decision taken). The posture, Sam's decision of 2026-10-27 and 2026-10-28, is non-commercial personal research: derived
figures stay on the page, raw quotes are withheld (`DASHBOARD_SHOW_BOOKS = False`), nothing is redistributed, and
written authorization from a venue is the route to anything more. Nothing derived from Schwab data leaves the OptiPlex
(Schwab's agreement bars third-party use and derivative works). Kalshi's taker fee coefficient (0.07) is secondhand and
unverified, as are the jurisdictions each venue admits; both are recorded with sources in `docs/venues.md`. A reading,
not legal advice.

## Architecture

```
OptiPlex (home, outbound only)                      public VM
┌─────────────────────────────────┐   rsync/ssh    ┌──────────────────────────────────┐
│ collectors (60 s books)         │ ─────────────▶ │ /srv/venue-arb/bundle/live.json  │
│ Prefect flows (hourly scoring)  │   every 60 s   │ /srv/venue-arb/bundle/history/   │
│ publish loop:                   │   + hourly     │   pairs-YYYYMMDDHH.parquet       │
│   live bundle (~70–100 KB JSON) │                │ app: FastAPI + DuckDB + static   │
│   history Parquet (hourly)      │                │ Caddy (HTTPS) → :443             │
└─────────────────────────────────┘                └──────────────────────────────────┘
```

Collectors, Postgres, ClickHouse and Prefect run at home; the server never connects back and holds no database: the
bundle directory is the state, and its one computation (`/api/walk`) imports `venue_arb.edges`, so the walk is a single
implementation. Repo layout: `venue_arb/` (pipeline, `publish.py` producer), `dashboard/` (`api/`, `static/`,
`sample/`), `deploy/` (OptiPlex units; `deploy/server/` for the VM), `docs/` (`terms.md`, `venues.md`,
`going-public.md`, `research/`), `reports/`, `labels/`, `sql_checks/`, `tests/`. License: MIT (`LICENSE`).

---

Everything below is operational: how to run the pipeline, the dashboard, the tests and the services.

## How to run

### Setup

Follow `~/platform/docs/NEW_PROJECT.md` (platform checkout, `secrets` wrapper, `mise`, running stack via
`cd ~/platform && just up`).

### Run

Adjudicator prompt v3: `near` needs overlapping Yes outcomes; mutually exclusive brackets of one meeting (hold vs +25) are `different`; Fed moves are on a 25 bp grid, so off-grid and rounding clauses are tail risks.

Fed rate-decision pairs (Kalshi `KXFEDDECISION-*` vs a Polymarket bracket title) skip the LLM: `matching/fed_rule.py` compares the two outcome sets on the 25 bp grid (equal = exact, overlapping = near, disjoint or different meeting = different) and stores them with `model = 'rule:fed-v1'`, cost 0 and no `llm_attempts` row. Crypto strike pairs (Kalshi `KXBTC*`/`KXETH*` "$X or above" vs Polymarket hourly/daily "above X" titles, the shapes `matching/crypto_rule.py` parses) skip it too since 2026-10-28, stored as `rule:crypto-v1`: never exact (the venues settle on different sources), near for the same asset, printed strike and instant, otherwise different. Before that rule the hourly ladders were 97% of LLM calls (~$1.60/day) and never produced an exact pair. `matching/rules.py` lists the rules in order; every other pair still goes to the LLM adjudicator.

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

CI is the worktree `~/projects/venue-arb-ci` on the OptiPlex: push the branch to origin, run both commands there with
`set -o pipefail`, and fast-forward `main` only on green. The Mac clone lives under iCloud, where pytest hangs on
evicted venv files, so the Mac is for editing, not for the suite.

## Operations

### Services

On the OptiPlex, four long-running systemd user units: `venue-arb-collector` (order-book snapshots),
`venue-arb-perps` (stock-perp snapshots; the weekly Schwab login restarts it),
`venue-arb-publish` (dashboard bundle, below) and `venue-arb-flows` (`prefect.serve` of seven deployments, six hourly: `catalog` at :01 (Kalshi opens the next hour's crypto strikes at :00), `match` at :20, `basis` at :35,
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
5. `secrets uv run python -m venue_arb.flows.stock_perp_backfill`. It exits 1 if any symbol failed, and names it. Run it
   with the collector quiet if you can: both share Hyperliquid's per-IP request budget, and a starved fetch fails.
6. `systemctl --user restart venue-arb-flows`. `serve` registers `stock_perp_score` and `stock_perp_weekend`, and a
   restart unpauses every deployment.
7. Run `secrets uv run python -m venue_arb.flows.stock_perp_score` and `... stock_perp_weekend` once by hand. Check
   their results in `qa.check_results` (Postgres) for the pipelines `venue_arb_stock_perp_score` and
   `venue_arb_stock_perp_weekend`.
8. `DASHBOARD_SHOW_STOCK_PERPS` (`venue_arb/config.py`) gates the public view only; the collector, scoring and bundle
   fields run regardless. It has been `True` since 2026-10-25.

Turning the category on, only after the first week of oracle-check reports (`docs/going-public.md`):

1. Deploy the VM first (`deploy/server/README.md`, Update). The server validates the bundle against the new models.
2. One-line commit setting `DASHBOARD_SHOW_STOCK_PERPS = True`, then `systemctl --user restart venue-arb-publish`.
3. Sam reviews `copy.METHOD["stock_perps"]` and `copy.HEADLINE["stock_perps"]` on the rendered page. Wording fixes go in
   `venue_arb/report/copy.py` only.

#### Label kinds and pre-IPO names

`labels/stock_perps.csv` has a `kind` column, `equity` or `pre_ipo`. An `equity` row needs an `exchange`. A `pre_ipo`
row has `ref_kind none`, no `ref_symbol` and no `exchange`. `load_labels` rejects anything else. Pre-IPO rows are
collected every tick but never scored, and they stay out of the weekend tier, the oracle check and the research counts.
The page shows them under the "Pre-IPO" Scanner chip with the verdict "Pre-IPO · no listed market", and the Explorer
shows a one-line note instead of a cost table. Today `xyz:ANT`, `xyz:YMTC` and `xyz:JIO` are `pre_ipo`.
`labels/stock_perps_excluded.txt` is now for indices, commodities, FX and names hidden by hand, not for pre-IPO names.
It still holds `xyz:SPCX` (listed 2026-06-12), `xyz:QNT` (2026-06-04) and `xyz:CBRS` (2026-05-14), which listed on
Nasdaq: they are the first three candidates for the listing-day edit below, and Sam supplies their tickers.

Listing day, per name:

0. Make the edit only after the first regular-session print AND after the venue's oracle has switched to an external
   feed (the row's reference reads "oracle, live" on the page, not internal). The scorer rescores the last 3 hours
   (`WINDOW_HOURS`) with the current labels, so ticks from before the edit are scored as `equity`: wait 3 hours after
   that point, or accept up to 3 hours of early rows.
1. Edit the row in `labels/stock_perps.csv`: `kind` to `equity`, `ref_kind` to `schwab` for a US listing or `none`
   elsewhere, then `ref_symbol`, `exchange` and `currency`. For SPCX, QNT and CBRS also delete the line from
   `labels/stock_perps_excluded.txt`. One commit through the usual CI path, fast-forward `main`. No restart: the
   collector reloads labels every ten ticks, and the publisher and every flow read them per run.
2. From the next collector tick the Schwab quote flows (a US name). From the next external-session tick the row is
   scored. The weekend tier and the oracle check include it from their next runs.
3. The row keeps its key. While the label is `pre_ipo` it has no public scored rows; rows appear from the edit, including
   up to 3 rescored hours. A name whose old scored rows exist (`xyz:JIO`, from the earlier NSE assumption) will see
   those rows reappear in public counts once it leaves the hide-list at listing.

Deploying the `kind` change (stock-perp pair keys went from `{perp}~{stock}~stock_perp` to `{perp}~stock_perp`, and
`Leg.side` now admits `""`):

1. **Update the VM's server tree first** (`deploy/server/README.md`, Update section). This is mandatory: the server
   validates bundles with `extra="forbid"` models, so if the publisher restarts before the VM is updated, the page
   freezes on a rejected bundle.
2. On the OptiPlex, fast-forward `main`, then `uv sync`.
3. `systemctl --user restart venue-arb-publish`.
4. `secrets uv run python -m venue_arb.publish history --hours 240`. It rewrites the pushed history Parquet under the
   new keys; the server keeps about ten days of hour files, so 240 hours covers all of them. Without it the Explorer's
   history lookups miss for every stock perp.
5. Check `/api/live`: `categories` contains `"pre_ipo"` and no stock key has three parts.

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
token expires every 7 days; re-issue it with `secrets uv run python -m venue_arb.sources.schwab login` and a write-capable Bitwarden token in
`BWS_WRITE_TOKEN`. The runbook, token lifetimes
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
5-day **regular-session** (09:30-16:00 ET) p95 exceeds 10 bps; the report lists extended hours separately, for
information only (thin names' pre- and post-market NBBOs are wide, so a gap there does not say which side is off). Run by hand:

```bash
secrets uv run python -m venue_arb.flows.stock_perp_oracle_check
```

No name is hidden automatically. After a week of reports, remove a name from the public page by hand in
`labels/stock_perps_excluded.txt` with a neutral comment: the publisher re-reads the file and drops the name from the
live rows, sparklines, history, research block and episode counts; its rows stay in the private tables. The
History hours already pushed keep the name until rewritten: with `DASHBOARD_SHOW_STOCK_PERPS` on, run `secrets uv run python -m venue_arb.publish history --hours 240` (the server keeps about 10 days of hour files; the run rewrites and pushes them); with the flag off the hour files hold no stock rows, so there is nothing to rewrite. Asian exchange holidays are not in the session calendar, so
on such a day a name may read external against a stale oracle.

