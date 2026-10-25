-- One row per (symbol, ET day, segment): 'regular' (09:30-16:00 ET) or 'extended'. Additive: the column joins the
-- sorting key in the same statement (ClickHouse allows that for a new column without a default); rows written
-- before this migration keep segment '' and are the old pooled figures.
ALTER TABLE venue_arb.stock_perp_oracle_check ADD COLUMN segment LowCardinality(String), MODIFY ORDER BY (symbol, day, segment);
