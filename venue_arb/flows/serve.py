"""Serve the hourly flows. Run: secrets uv run python -m venue_arb.flows.serve"""

from prefect import serve

from venue_arb.flows.catalog import catalog_flow
from venue_arb.flows.match import match_flow

if __name__ == "__main__":
    serve(
        catalog_flow.to_deployment("catalog", cron="5 * * * *"),
        match_flow.to_deployment("match", cron="20 * * * *"),
    )
