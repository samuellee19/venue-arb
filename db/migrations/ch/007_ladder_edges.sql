-- Executable spread per aligned snapshot of a same-instant ladder: Kalshi strike a (BRTI > a) against Polymarket strike
-- b (Binance > b), both settling at `instant`. kind 'arb' = basis-proof (pays >= $1 in every state within the 30-day
-- directional basis bounds basis_up / basis_down); 'near_exact' = same strike, scored both ways, never arbitrage.
-- ts is the Kalshi snapshot time, always < instant. Re-scoring a window rewrites identical keys.
CREATE TABLE IF NOT EXISTS venue_arb.ladder_edges (
    ts DateTime64(3, 'UTC'),
    asset LowCardinality(String),
    instant DateTime('UTC'),
    k_id String,
    pm_id String,
    direction LowCardinality(String),
    kind LowCardinality(String),
    a Float64,
    b Float64,
    basis_up Float64,
    basis_down Float64,
    best_edge Nullable(Float64),
    capacity_contracts Float64,
    capacity_usd Float64,
    avg_edge Nullable(Float64),
    fee_version String
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (asset, instant, k_id, pm_id, direction, ts);
