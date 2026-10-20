-- engine: clickhouse
-- severity: error
-- target: venue_arb.basis
-- Returns basis rows with a non-positive price, an inconsistent diff, or an implausible gap (> 5% of BRTI).
SELECT instant, asset, brti, binance, diff FROM venue_arb.basis FINAL
WHERE instant >= now() - INTERVAL 31 DAY
  AND (brti <= 0 OR binance <= 0 OR abs(diff - (brti - binance)) > 1e-6 OR abs(diff) > 0.05 * brti)
LIMIT 10
