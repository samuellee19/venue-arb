-- Screened tier v2: Kalshi candle closes keyed at end_period_ts (the instant the close quote applies), each against
-- the Polymarket print nearest that instant within 30 min. venue_arb.screened (v1) paired Kalshi ~1 h later than
-- Polymarket; it is left untouched (additive migrations only) for Sam to drop by hand. Same columns, engine and key.
CREATE TABLE IF NOT EXISTS venue_arb.screened_v2 (
    ts DateTime('UTC'),
    kalshi_id String,
    pm_id String,
    direction LowCardinality(String),
    k_price Float64,
    pm_price Float64,
    gross_gap Float64,
    net_gap_top Float64
) ENGINE = ReplacingMergeTree
ORDER BY (kalshi_id, pm_id, direction, ts);
