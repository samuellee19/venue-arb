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
secrets uv run python -m venue_arb.flows.match        # candidates + LLM adjudication
secrets uv run python -m venue_arb.flows.review_cli   # hand review of adjudicated matches
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

Two systemd user units on the OptiPlex: `venue-arb-collector` (order-book snapshots) and `venue-arb-flows`
(serves the catalog flow at :05 and the match flow at :20 each hour).

```bash
cp deploy/*.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now venue-arb-collector venue-arb-flows
loginctl enable-linger "$USER"          # keep them running after logout
systemctl --user status venue-arb-collector venue-arb-flows
journalctl --user -u venue-arb-collector -u venue-arb-flows -f
```

## Schwab

Schwab data is not used yet. The Trader API app has the Market Data product only, and its refresh token needs a
manual browser re-login every 7 days.
