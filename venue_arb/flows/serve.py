"""Serve the hourly flows. Run: secrets uv run python -m venue_arb.flows.serve"""

from prefect import serve

from venue_arb.flows.basis import basis_flow
from venue_arb.flows.catalog import catalog_flow
from venue_arb.flows.ladder_score import ladder_score_flow
from venue_arb.flows.match import match_flow
from venue_arb.flows.score import score_flow
from venue_arb.flows.stock_perp_oracle_check import stock_perp_oracle_check_flow
from venue_arb.flows.stock_perp_score import stock_perp_score_flow
from venue_arb.flows.stock_perp_weekend import stock_perp_weekend_flow

if __name__ == "__main__":
    # concurrency_limit=1: runs of one deployment never overlap. serve() creates/updates each deployment and
    # its schedule on each start and UNPAUSES it (verified 2026-10-20): a UI/CLI pause lasts only until restart.
    serve(
        catalog_flow.to_deployment("catalog", cron="5 * * * *", concurrency_limit=1),
        match_flow.to_deployment("match", cron="20 * * * *", concurrency_limit=1),
        basis_flow.to_deployment("basis", cron="35 * * * *", concurrency_limit=1),
        score_flow.to_deployment("score", cron="40 * * * *", concurrency_limit=1),
        ladder_score_flow.to_deployment("ladder_score", cron="50 * * * *", concurrency_limit=1),
        stock_perp_score_flow.to_deployment("stock_perp_score", cron="55 * * * *", concurrency_limit=1),
        # Monday 15:00 UTC is 10:00 or 11:00 New York, after the regular open has printed
        stock_perp_weekend_flow.to_deployment("stock_perp_weekend", cron="0 15 * * 1", concurrency_limit=1),
        # 01:30 UTC Tue-Sat is 20:30 or 21:30 New York, after the US post-market close (Mon-Fri evenings)
        stock_perp_oracle_check_flow.to_deployment("stock_perp_oracle_check", cron="30 1 * * 2-6", concurrency_limit=1),
    )
