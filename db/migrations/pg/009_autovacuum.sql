-- Autovacuum per table (2026-11-12). The default triggers at 20% dead rows: candidates (2.7M rows, rescored hourly)
-- had 418k dead and had never been vacuumed, and listings rewrites last_seen on every row each hour. At 2% both are
-- vacuumed and analyzed as they churn. adjudications only grows: analyze it at 2% new rows so the planner's
-- estimates for the match and scope queries keep up.
ALTER TABLE venue_arb.candidates SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
ALTER TABLE venue_arb.listings SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
ALTER TABLE venue_arb.adjudications SET (autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
