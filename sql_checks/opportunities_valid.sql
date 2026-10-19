-- engine: postgres
-- severity: error
-- target: venue_arb.opportunities
-- Returns episodes that end before they start or span fewer than the 2 snapshots an episode needs.
SELECT kalshi_id, pm_id, direction, start_ts, end_ts, snapshots FROM venue_arb.opportunities
WHERE end_ts < start_ts OR snapshots < 2
LIMIT 10
