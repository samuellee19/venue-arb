-- engine: postgres
-- severity: error
-- target: venue_arb.opportunities
-- Returns episodes whose source is unknown or whose direction does not belong to that source's flow.
SELECT kalshi_id, pm_id, direction, source, start_ts FROM venue_arb.opportunities
WHERE NOT ((source = 'pair' AND direction IN ('k_yes_pm_no', 'k_no_pm_yes'))
        OR (source = 'ladder' AND direction IN ('k_yes_pm_no', 'pm_yes_k_no'))
        OR (source IN ('stock_perp', 'stock_perp_oracle') AND direction IN ('short_perp_long_stock', 'long_perp_short_stock')))
LIMIT 10
