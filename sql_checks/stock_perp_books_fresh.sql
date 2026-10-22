-- engine: clickhouse
-- severity: warn
-- target: venue_arb.stock_perp_ctx
-- Returns one row when the newest stock-perp context tick is more than 5 minutes old (or the table is empty).
SELECT max(ts) AS newest FROM venue_arb.stock_perp_ctx
HAVING max(ts) < now64(3) - INTERVAL 5 MINUTE
