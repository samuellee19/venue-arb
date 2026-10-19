-- book_levels can't be re-partitioned in place, so snapshots move to a daily-partitioned table with 90-day
-- retention (~8.6M rows/day). The old, empty venue_arb.book_levels is left alone; Sam can drop it by hand.
CREATE TABLE IF NOT EXISTS venue_arb.book_levels_v2 (
    ts DateTime64(3, 'UTC'),
    venue LowCardinality(String),
    market_id String,
    side Enum8('yes' = 1, 'no' = 2),
    level UInt8,
    price Float64,
    size Float64
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (venue, market_id, ts, side, level)
TTL toDateTime(ts) + INTERVAL 90 DAY;
