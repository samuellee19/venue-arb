-- engine: postgres
-- severity: error
-- target: venue_arb.listings
-- Returns each category that lacks listings from both venues seen in the last 2 hours.
SELECT category, count(DISTINCT venue) AS venues FROM venue_arb.listings
WHERE last_seen > now() - INTERVAL '2 hours' AND category IS NOT NULL
GROUP BY category
HAVING count(DISTINCT venue) < 2
LIMIT 10
