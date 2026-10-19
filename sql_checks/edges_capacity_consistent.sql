-- engine: clickhouse
-- severity: error
-- target: venue_arb.edges
-- Returns recent edge rows that claim capacity without a qualifying average edge (every filled slice clears $0.01;
-- the 1e-9 slack absorbs float noise in the weighted mean).
SELECT kalshi_id, pm_id, direction, ts, capacity_contracts, avg_edge FROM venue_arb.edges
WHERE ts > now64(3) - INTERVAL 2 HOUR AND capacity_contracts > 0 AND (avg_edge IS NULL OR avg_edge < 0.01 - 1e-9)
LIMIT 10
