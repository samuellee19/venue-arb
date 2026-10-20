-- Settlement-source basis: Kalshi BRTI settle vs the Binance candle Polymarket's rules name, one row per (asset, instant).
-- binance/diff use the 1h candle ending at the instant (hourly rules); binance_1m/diff_1m the 1m candle (daily noon-ET
-- rule), NaN when that day was unavailable. diff = brti - binance.
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
