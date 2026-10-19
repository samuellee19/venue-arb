-- engine: clickhouse
-- severity: error
-- target: venue_arb.book_levels
-- Binary-market ask prices live strictly inside (0, 1); sizes are positive.
SELECT ts, venue, market_id, side, level, price, size FROM venue_arb.book_levels
WHERE price <= 0 OR price >= 1 OR size <= 0
LIMIT 10
