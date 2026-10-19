# venue-arb

Cross-venue prediction-market research: it catalogs Kalshi and Polymarket markets, matches equivalent contracts
(rules-based candidates, then an LLM adjudication), snapshots order books for matched pairs, and will measure
arbitrage between them. Design and decisions: `docs/superpowers/specs/2026-10-19-venue-arb-design.md`.

## Setup

Follow `~/platform/docs/NEW_PROJECT.md` (platform checkout, `secrets` wrapper, `mise`, running stack via
`cd ~/platform && just up`).

## Run

```bash
secrets uv run python -m venue_arb.flows.catalog      # listings from both venues
secrets uv run python -m venue_arb.flows.match        # candidates + LLM adjudication (re-judges pairs stored under an older prompt version)
secrets uv run python -m venue_arb.flows.review_cli   # hand review of adjudicated matches
secrets uv run python -m venue_arb.flows.score        # exact pairs: executable edges (ClickHouse) + opportunity episodes (Postgres)
secrets uv run python -m venue_arb.collect            # order-book snapshot collector (runs forever)
secrets uv run python -m db.migrate                   # apply migrations
```

LLM spend is capped at $20/month (`MONTHLY_LLM_BUDGET_USD`) and paced daily, so a backlog is worked off over
the month instead of in one day.

## Tests

```bash
uv run pytest -m "not integration"      # unit: no secrets, no stack (what CI runs, after `uv run ruff check .`)
secrets uv run pytest -m integration    # needs the stack up
```

## Services

On the OptiPlex: one long-running systemd user unit, `venue-arb-collector` (order-book snapshots), and three
hourly timers that start oneshot units: `venue-arb-catalog.timer` (:05), `venue-arb-match.timer` (:20) and
`venue-arb-score.timer` (:40, scores the last 26 h of books; overlapping windows re-score idempotently). Runs
still appear in Prefect as ad hoc flow runs. (Timers replace Prefect deployment schedules because the platform
Prefect server's scheduler crashed on 2026-10-19; switch back once it's fixed.)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now venue-arb-collector venue-arb-catalog.timer venue-arb-match.timer venue-arb-score.timer
loginctl enable-linger "$USER"          # keep them running after logout
systemctl --user list-timers 'venue-arb-*'
journalctl --user -u venue-arb-collector -u venue-arb-catalog -u venue-arb-match -u venue-arb-score -f
```

systemd doesn't load `mise.toml`, so each unit repeats its non-secret `[env]` values as `Environment=` lines
(`tests/test_deploy.py` fails if they drift). Without `PGUSER`/`CLICKHOUSE_USER` the services would log in as the
database superusers. After installing, check they connect as the project role:

```bash
systemctl --user show venue-arb-collector venue-arb-catalog venue-arb-match venue-arb-score -p Environment | tr ' ' '\n' \
  | grep -E '^(PGUSER|CLICKHOUSE_USER|PREFECT_API_URL)='
cd ~/platform && docker compose exec postgres psql -U postgres -d app -c \
  "SELECT usename, count(*) FROM pg_stat_activity WHERE datname = 'app' GROUP BY 1"   # expect venue_arb, not postgres
```

Snapshots land in ClickHouse `venue_arb.book_levels_v2` (daily partitions, 90-day TTL). The original
`venue_arb.book_levels` (unpartitioned, never written to) is left in place; drop it by hand when convenient:
`DROP TABLE venue_arb.book_levels`.

## Schwab

Schwab data is not used yet. The Trader API app has the Market Data product only, and its refresh token needs a
manual browser re-login every 7 days.
