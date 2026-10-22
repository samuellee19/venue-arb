-- Stock perpetuals (tokenized-equity perps vs the stock itself). Book, context and reference-quote tables are kept
-- 90 days; the candle table is the weekend tier's long history and has no TTL. Edges are kept without a TTL.
-- books: venue order-book levels per perp symbol. ctx: per-tick perp context (mark, oracle, funding, open interest).
-- refs: stock reference quotes; ts is the collector tick, quote_time the venue's own quote time.
-- candles: hourly stock bars (ReplacingMergeTree dedupes a re-loaded hour). edges: scored executable spreads per
-- direction; direction is 'short_perp_long_stock' or 'long_perp_short_stock'.
CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_books (
    ts DateTime64(3, 'UTC'),
    symbol LowCardinality(String),
    side LowCardinality(String),
    level UInt8,
    price Float64,
    size Float64
) ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, ts, side, level)
TTL toDateTime(ts) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_ctx (
    ts DateTime64(3, 'UTC'),
    symbol LowCardinality(String),
    mark Float64,
    oracle Float64,
    mid Nullable(Float64),
    funding_h Float64,
    premium Nullable(Float64),
    open_interest Float64,
    day_volume_usd Float64,
    growth_mode UInt8
) ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_refs (
    ts DateTime64(3, 'UTC'),
    symbol LowCardinality(String),
    ref_kind LowCardinality(String),
    ref_symbol String,
    bid Float64,
    ask Float64,
    bid_size Float64,
    ask_size Float64,
    last Nullable(Float64),
    quote_time DateTime64(3, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_candles (
    hour DateTime('UTC'),
    symbol LowCardinality(String),
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume Float64,
    funding_h Nullable(Float64)
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (symbol, hour);

CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_edges (
    ts DateTime64(3, 'UTC'),
    symbol LowCardinality(String),
    direction LowCardinality(String),
    ref_kind LowCardinality(String),
    reference Float64,
    mark Float64,
    basis_bps Float64,
    perp_fee_bps Float64,
    perp_spread_bps Nullable(Float64),
    stock_spread_bps Nullable(Float64),
    funding_bps Float64,
    borrow_bps Float64,
    net_bps Float64,
    capacity_contracts Float64,
    capacity_usd Float64,
    fee_version String
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, direction, ts);
