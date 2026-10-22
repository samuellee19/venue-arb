-- engine: clickhouse
-- severity: error
-- target: venue_arb.stock_perp_edges
-- Looks back 27 h. Returns recent stock-perp edge rows that claim capacity while the net edge is below the 10 bps floor.
SELECT symbol, direction, ts, net_bps, capacity_contracts, capacity_usd FROM venue_arb.stock_perp_edges
WHERE ts > now64(3) - INTERVAL 27 HOUR AND capacity_contracts > 0 AND net_bps < 10 - 1e-9
LIMIT 10
