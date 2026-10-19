-- engine: clickhouse
-- severity: error
-- target: venue_arb.book_levels_v2
-- Returns each expected venue whose newest snapshot is more than 10 minutes old, or missing from the last day.
SELECT e.venue, l.newest
FROM (SELECT arrayJoin(['kalshi', 'polymarket']) AS venue) AS e
LEFT JOIN (
    SELECT toString(venue) AS venue, max(ts) AS newest FROM venue_arb.book_levels_v2
    WHERE ts > now64(3) - INTERVAL 1 DAY
    GROUP BY venue
) AS l ON l.venue = e.venue
WHERE l.venue = '' OR l.newest < now64(3) - INTERVAL 10 MINUTE
SETTINGS join_use_nulls = 0
