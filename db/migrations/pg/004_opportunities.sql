-- Opportunity episodes (executable tier): consecutive snapshots clearing the spec §4 threshold.
CREATE TABLE venue_arb.opportunities (
    kalshi_id text NOT NULL,
    pm_id text NOT NULL,
    direction text NOT NULL,
    start_ts timestamptz NOT NULL,
    end_ts timestamptz,
    snapshots int,
    max_edge real,
    median_capacity_usd real,
    annualized real,
    us_executable boolean,
    transfer_cost_usd real,
    scored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kalshi_id, pm_id, direction, start_ts)
);
