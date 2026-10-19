-- Prompt-versioned adjudications: older prompt versions are re-judged; tail_risks records rare edge clauses.
ALTER TABLE venue_arb.adjudications
    ADD COLUMN prompt_version text NOT NULL DEFAULT 'v1',
    ADD COLUMN tail_risks text;
