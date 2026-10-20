-- Which flow found an episode: 'pair' (exact pairs, venue_arb.flows.score) or 'ladder' (same-instant ladder spreads,
-- venue_arb.flows.ladder_score; kalshi_id / pm_id are then the two strikes). Additive: existing rows are pairs.
ALTER TABLE venue_arb.opportunities ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'pair';
