"""Query for exact-match eligibility: current listings, latest adjudication at current versions and the current
prompt version, and a review only if it was made of that prompt version's verdict."""

from venue_arb.matching.adjudicate import PROMPT_VERSION

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
LEFT JOIN venue_arb.reviews r ON (r.a_venue, r.a_id, r.a_version, r.b_venue, r.b_id, r.b_version)
  = (a.a_venue, a.a_id, a.a_version, a.b_venue, a.b_id, a.b_version) AND r.prompt_version = a.prompt_version
WHERE a.a_venue = %(a)s AND a.b_venue = %(b)s AND a.prompt_version = %(pv)s
  AND k.last_seen > now() - interval '2 hours' AND p.last_seen > now() - interval '2 hours'
  AND k.close_time > now() AND p.close_time > now()
ORDER BY k.market_id, p.market_id, a.created_at DESC
"""


def load_eligible(conn) -> list[dict]:
    cur = conn.execute(ELIGIBLE_SQL, {"a": "kalshi", "b": "polymarket", "pv": PROMPT_VERSION})
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
