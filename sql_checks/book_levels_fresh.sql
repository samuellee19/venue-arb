-- engine: clickhouse
-- severity: error
-- target: venue_arb.book_levels
-- Returns a row for any venue whose newest snapshot is more than 10 minutes old.
SELECT venue, max(ts) AS newest FROM venue_arb.book_levels
GROUP BY venue
HAVING newest < now64(3) - INTERVAL 10 MINUTE
