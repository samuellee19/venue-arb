-- engine: postgres
-- severity: warn
-- target: venue_arb.adjudications
-- Returns candidates scoring 0.9 or more that have no adjudication at the current listing versions (the budget can leave gaps).
SELECT c.a_id, c.b_id, c.score FROM venue_arb.candidates c
JOIN venue_arb.listings la ON la.venue = c.a_venue AND la.market_id = c.a_id
JOIN venue_arb.listings lb ON lb.venue = c.b_venue AND lb.market_id = c.b_id
WHERE c.score >= 0.9 AND la.last_seen > now() - INTERVAL '2 hours' AND lb.last_seen > now() - INTERVAL '2 hours'
  AND NOT EXISTS (SELECT 1 FROM venue_arb.adjudications d
                  WHERE d.a_venue = c.a_venue AND d.a_id = c.a_id AND d.a_version = la.version
                    AND d.b_venue = c.b_venue AND d.b_id = c.b_id AND d.b_version = lb.version
                    AND d.prompt_version = 'v3')  -- keep equal to adjudicate.PROMPT_VERSION
LIMIT 10
