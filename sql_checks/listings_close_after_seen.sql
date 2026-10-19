-- engine: postgres
-- severity: error
-- target: venue_arb.listings
-- Returns listings that closed more than the catalog's 1-day grace before we first saw them (stale feed rows).
SELECT venue, market_id, close_time, first_seen FROM venue_arb.listings
WHERE close_time < first_seen - INTERVAL '1 day'
LIMIT 10
