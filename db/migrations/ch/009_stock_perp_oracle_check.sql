-- Private oracle-vs-Schwab validation, one row per (symbol, ET day); never read by the public publisher.
CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_oracle_check (
    day Date,
    symbol LowCardinality(String),
    n UInt32,
    median_abs_bps Float64,
    p95_abs_bps Float64,
    share_over_10 Float64,
    open_gap_bps Nullable(Float64)
) ENGINE = ReplacingMergeTree
ORDER BY (symbol, day);
