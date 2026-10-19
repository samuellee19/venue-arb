-- Every paid LLM call, successful or not: the budget reads spend from here, not from adjudications.
CREATE TABLE venue_arb.llm_attempts (
    a_venue text NOT NULL,
    a_id text NOT NULL,
    a_version text NOT NULL,
    b_venue text NOT NULL,
    b_id text NOT NULL,
    b_version text NOT NULL,
    model text,
    outcome text NOT NULL CHECK (outcome IN ('ok', 'error')),
    error_type text,
    cost_usd numeric(10, 6) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX llm_attempts_created_at ON venue_arb.llm_attempts (created_at);

-- Carry spend already recorded on adjudications so this month's budget stays whole.
INSERT INTO venue_arb.llm_attempts (a_venue, a_id, a_version, b_venue, b_id, b_version, model, outcome, cost_usd,
                                    created_at)
SELECT a_venue, a_id, a_version, b_venue, b_id, b_version, model, 'ok', coalesce(cost_usd, 0), created_at
FROM venue_arb.adjudications;
