-- engine: clickhouse
-- severity: error
-- target: venue_arb.ladder_edges
-- Looks back 27 h: the flow writes at most READBACK_CAP (26 h) before its end.
-- Returns ladder rows scored at or after their settlement instant (books are stale from the instant on), or arb rows
-- whose strikes do not clear the directional basis bound they were scored with.
SELECT k_id, pm_id, direction, kind, ts, instant, a, b, basis_up, basis_down FROM venue_arb.ladder_edges
WHERE ts > now64(3) - INTERVAL 27 HOUR AND (ts >= instant
   OR (kind = 'arb' AND direction = 'k_yes_pm_no' AND round(b - a, 6) < basis_down)
   OR (kind = 'arb' AND direction = 'pm_yes_k_no' AND round(a - b, 6) < basis_up))
LIMIT 10
