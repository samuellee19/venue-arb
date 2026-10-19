-- Executable edge per aligned snapshot and direction. ts is the Kalshi snapshot time; fee_version is the date of the
-- fee entries in docs/venues.md, so a fee change shows in the rows. Re-scoring a window rewrites identical keys.
CREATE TABLE IF NOT EXISTS venue_arb.edges (
    ts DateTime64(3, 'UTC'),
    kalshi_id String,
    pm_id String,
    direction LowCardinality(String),
    best_edge Nullable(Float64),
    capacity_contracts Float64,
    capacity_usd Float64,
    avg_edge Nullable(Float64),
    fee_version String
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (kalshi_id, pm_id, direction, ts);
