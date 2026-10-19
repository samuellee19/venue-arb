-- Reviews judge one verdict: record the adjudication prompt version reviewed, so a review of an older prompt's
-- verdict is not applied to the re-judged one. Default 'v1': a review written before this column existed predates
-- prompt v2 (the table was empty when this shipped, 2026-10-19). Additive only.
ALTER TABLE venue_arb.reviews ADD COLUMN prompt_version text NOT NULL DEFAULT 'v1';
