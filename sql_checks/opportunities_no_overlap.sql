-- engine: postgres
-- severity: error
-- target: venue_arb.opportunities
-- Returns episodes of one pair and direction that overlap in time: one live episode must be one row, not one row
-- per hourly run with a window-truncated start.
SELECT a.kalshi_id, a.pm_id, a.direction, a.start_ts, a.end_ts, b.start_ts AS overlapping_start
FROM venue_arb.opportunities a
JOIN venue_arb.opportunities b USING (kalshi_id, pm_id, direction)
WHERE a.start_ts < b.start_ts AND a.end_ts >= b.start_ts
LIMIT 10
