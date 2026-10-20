"""Serve the hourly flows. Run: secrets uv run python -m venue_arb.flows.serve"""

from prefect import serve

from venue_arb.flows.catalog import catalog_flow
from venue_arb.flows.match import match_flow
from venue_arb.flows.score import score_flow

if __name__ == "__main__":
    # concurrency_limit=1: runs of one deployment never overlap. serve() creates/updates each deployment and
    # its schedule and leaves it unpaused (serve is the owner of the schedule).
    serve(
        catalog_flow.to_deployment("catalog", cron="5 * * * *", concurrency_limit=1),
        match_flow.to_deployment("match", cron="20 * * * *", concurrency_limit=1),
        score_flow.to_deployment("score", cron="40 * * * *", concurrency_limit=1),
    )
