# venue-arb

Cross-venue prediction-market research: it catalogs Kalshi and Polymarket markets, matches equivalent contracts
(rules-based candidates, then an LLM adjudication), snapshots order books for matched pairs, and will measure
arbitrage between them. Design and decisions: `docs/superpowers/specs/2026-10-19-venue-arb-design.md`.

## Setup

Follow `~/platform/docs/NEW_PROJECT.md` (platform checkout, `secrets` wrapper, `mise`, running stack via
`cd ~/platform && just up`).

## Run

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

## Tests

```bash
uv run pytest -m "not integration"      # unit: no secrets, no stack (what CI runs, after `uv run ruff check .`)
secrets uv run pytest -m integration    # needs the stack up
```

## Services

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
hour, then pushes them outbound with rsync to `VENUE_ARB_PUBLISH_DEST` (`user@host:/srv/venue-arb/bundle/`). The value
in `mise.toml [env]` and the three units is the placeholder `venue-arb@SERVER_IP:/srv/venue-arb/bundle/` (a shared
env var; only the publisher reads it, and `tests/test_deploy.py` needs it identical everywhere). Once the server
exists (Deploy step), set the real IP on the box in both `mise.toml` and the unit, then `daemon-reload` and restart.

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

## Schwab

Schwab data is not used yet. The Trader API app has the Market Data product only, and its refresh token needs a
manual browser re-login every 7 days.
