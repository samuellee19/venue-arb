-- database venue_arb already exists (bin/project-db.sh); never created here

-- Ordering key makes a retried load idempotent (ReplacingMergeTree dedupes identical keys).
CREATE TABLE IF NOT EXISTS venue_arb.book_levels (
    ts DateTime64(3, 'UTC'),
    venue LowCardinality(String),
    market_id String,
    side Enum8('yes' = 1, 'no' = 2),
    level UInt8,
    price Float64,
    size Float64
) ENGINE = ReplacingMergeTree
ORDER BY (venue, market_id, ts, side, level);
