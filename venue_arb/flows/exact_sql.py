"""Query for exact-match eligibility: current listings, latest adjudication at current versions, review."""

ELIGIBLE_SQL = """
SELECT DISTINCT ON (k.market_id, p.market_id)
       k.market_id AS kalshi_id, p.market_id AS pm_id, p.yes_token AS pm_yes_token,
       a.relation, a.yes_maps_to, a.confidence, r.label,
       k.close_time AS kalshi_close, p.close_time AS pm_close,
       k.raw->'event'->>'series_ticker' AS kalshi_series,
       p.raw->'market'->'feeSchedule' AS pm_fee_schedule,
       (p.raw->'market'->>'feesEnabled')::boolean AS pm_fees_enabled
FROM venue_arb.adjudications a
JOIN venue_arb.listings k ON k.venue = a.a_venue AND k.market_id = a.a_id AND k.version = a.a_version
JOIN venue_arb.listings p ON p.venue = a.b_venue AND p.market_id = a.b_id AND p.version = a.b_version
LEFT JOIN venue_arb.reviews r USING (a_venue, a_id, a_version, b_venue, b_id, b_version)
WHERE a.a_venue = %(a)s AND a.b_venue = %(b)s
  AND k.last_seen > now() - interval '2 hours' AND p.last_seen > now() - interval '2 hours'
ORDER BY k.market_id, p.market_id, a.created_at DESC
"""


def load_eligible(conn) -> list[dict]:
    cur = conn.execute(ELIGIBLE_SQL, {"a": "kalshi", "b": "polymarket"})
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
