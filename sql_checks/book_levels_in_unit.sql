-- engine: clickhouse
-- severity: error
-- target: venue_arb.book_levels_v2
-- Binary-market ask prices live strictly inside (0, 1); sizes are positive. Last 15 minutes only (runs every flush).
SELECT ts, venue, market_id, side, level, price, size FROM venue_arb.book_levels_v2
WHERE ts > now64(3) - INTERVAL 15 MINUTE AND (price <= 0 OR price >= 1 OR size <= 0)
LIMIT 10
