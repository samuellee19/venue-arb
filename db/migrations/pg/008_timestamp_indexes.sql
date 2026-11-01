-- Indexes on the columns read by time (2026-11-01): the hourly audit's max(ts) and count-in-window, and the catalog
-- flow's max(last_seen), were sequential scans of listings (1 GB) and candidates (618 MB). adjudications_latest
-- matches the match query's filter and its DISTINCT ON (a_id, b_id) ... created_at DESC order, so no sort.
-- listings.last_seen is rewritten for every row each hour, so that index churns fully each hour: cheap on the SSD.
CREATE INDEX IF NOT EXISTS listings_last_seen ON venue_arb.listings (last_seen);
CREATE INDEX IF NOT EXISTS candidates_created_at ON venue_arb.candidates (created_at);
CREATE INDEX IF NOT EXISTS adjudications_created_at ON venue_arb.adjudications (created_at);
CREATE INDEX IF NOT EXISTS adjudications_latest ON venue_arb.adjudications (a_venue, b_venue, prompt_version, a_id, b_id, created_at DESC);
CREATE INDEX IF NOT EXISTS stock_perp_weekends_computed_at ON venue_arb.stock_perp_weekends (computed_at);
