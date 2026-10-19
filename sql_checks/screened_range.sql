-- engine: clickhouse
-- severity: error
-- target: venue_arb.screened_v2
-- Returns screened rows with a leg price outside (0, 1) or a net gap above the gross gap (fees are never negative).
-- Whole table: every screened run rewrites each in-scope pair's full history, so the last run's window is all of it.
SELECT ts, kalshi_id, pm_id, direction, k_price, pm_price, gross_gap, net_gap_top FROM venue_arb.screened_v2
WHERE k_price <= 0 OR k_price >= 1 OR pm_price <= 0 OR pm_price >= 1 OR gross_gap < net_gap_top
LIMIT 10
