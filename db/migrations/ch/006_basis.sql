-- Settlement-source basis: Kalshi BRTI settle vs the Binance candle Polymarket's rules name, one row per (asset, instant).
-- binance/diff use the 1h candle ending at the instant; it shares its close with the 1m candle closing at that instant
-- (hourly and daily noon-ET rules alike). binance_1m/diff_1m are no longer populated (NaN); kept for additive schema.
-- diff = brti - binance. Read with FINAL (ReplacingMergeTree; the trailing days are re-inserted hourly).
CREATE TABLE IF NOT EXISTS venue_arb.basis (
    instant DateTime('UTC'),
    asset LowCardinality(String),
    brti Float64,
    binance Float64,
    diff Float64,
    binance_1m Float64,
    diff_1m Float64
) ENGINE = ReplacingMergeTree
ORDER BY (asset, instant);
