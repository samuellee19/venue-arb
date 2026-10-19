-- Screened tier: hourly price gaps from Kalshi candles and Polymarket price history. Polymarket history is a
-- traded/mid price, not an ask, so these are NOT executable and never mix with venue_arb.edges.
CREATE TABLE IF NOT EXISTS venue_arb.screened (
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
