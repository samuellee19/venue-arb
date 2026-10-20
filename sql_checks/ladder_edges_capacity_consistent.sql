-- engine: clickhouse
-- severity: error
-- target: venue_arb.ladder_edges
-- Looks back 27 h: the flow writes at most READBACK_CAP (26 h) before its end.
-- Returns recent ladder rows that claim capacity without a qualifying average edge (every filled slice clears $0.01).
SELECT k_id, pm_id, direction, ts, capacity_contracts, avg_edge FROM venue_arb.ladder_edges
WHERE ts > now64(3) - INTERVAL 27 HOUR AND capacity_contracts > 0 AND (avg_edge IS NULL OR avg_edge < 0.01 - 1e-9)
LIMIT 10
