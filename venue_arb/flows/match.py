"""Match flow: embed current listings -> Kalshi/Polymarket candidates -> LLM adjudication, through qa.

    secrets uv run python -m venue_arb.flows.match
"""

from datetime import UTC, datetime

import numpy as np
import obs
from pgvector.psycopg import register_vector
from prefect import flow, get_run_logger
from qa import checks, db, sql, store

from venue_arb.config import CATEGORIES, PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.matching import adjudicate, rules
from venue_arb.matching.candidates import embed_pairs, merge, needs_embedding, rule_pairs, unadjudicated
from venue_arb.matching.embed import embed, text_for

PIPELINE = f"{PIPELINE_PREFIX}_match"
TARGET = "venue_arb.adjudications"
DIM = 384
COLS = ("venue", "market_id", "event_id", "category", "title", "rules", "yes_label", "no_label", "close_time",
        "resolution_source", "version")


def current_listings(conn) -> list[dict]:
    cur = conn.execute(f"SELECT {', '.join(COLS)} FROM venue_arb.listings "
                       "WHERE last_seen > now() - INTERVAL '2 hours' AND category IS NOT NULL")
    return [dict(zip(COLS, r, strict=True)) for r in cur.fetchall()]


def ensure_embeddings(conn, rows: list[dict]) -> dict[tuple[str, str, str], np.ndarray]:
    """Insert vectors for listings lacking one at their current version; return vectors for all `rows`."""
    keys = [(r["venue"], r["market_id"], r["version"]) for r in rows]
    vecs = {(v, m, ver): x.to_numpy() for v, m, ver, x in conn.execute(
        "SELECT venue, market_id, version, vec FROM venue_arb.embeddings "
        "WHERE (venue, market_id, version) IN (SELECT * FROM unnest(%s::text[], %s::text[], %s::text[]))",
        ([k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys])).fetchall()}
    todo = needs_embedding(rows, set(vecs))
    if todo:
        new = embed([text_for(r) for r in todo])
        if new.shape != (len(todo), DIM):
            raise checks.DataQualityError([checks.Result("embedding_dim", False, new.shape[-1], DIM, target=TARGET)])
        for r, x in zip(todo, new, strict=True):
            conn.execute("INSERT INTO venue_arb.embeddings (venue, market_id, version, vec) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (r["venue"], r["market_id"], r["version"], x))
            vecs[(r["venue"], r["market_id"], r["version"])] = x
    return vecs


def candidate_pairs(rows: list[dict], vecs: dict) -> list[tuple[str, str, float, str]]:
    out = []
    for cat in CATEGORIES:
        k = [r for r in rows if r["venue"] == "kalshi" and r["category"] == cat]
        p = [r for r in rows if r["venue"] == "polymarket" and r["category"] == cat]
        if not (k and p):
            continue
        kv = np.stack([vecs[("kalshi", r["market_id"], r["version"])] for r in k])
        pv = np.stack([vecs[("polymarket", r["market_id"], r["version"])] for r in p])
        out += merge(rule_pairs(k, p), embed_pairs(k, p, kv, pv))
    return out


@flow(name=PIPELINE)
@obs.job("venue-arb.match")
def match_flow(limit: int = 500) -> dict:
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        with db.pg() as conn:
            register_vector(conn)
            rows = current_listings(conn)
            pre = [checks.expect_rows(len(rows), "venue_arb.listings")]
            results += pre
            checks.enforce(pre)
            vecs = ensure_embeddings(conn, rows)
            cands = candidate_pairs(rows, vecs)
            conn.cursor().executemany(
                "INSERT INTO venue_arb.candidates (a_venue, a_id, b_venue, b_id, score, source) "
                "VALUES ('kalshi', %s, 'polymarket', %s, %s, %s) "
                "ON CONFLICT (a_venue, a_id, b_venue, b_id) DO UPDATE SET score = EXCLUDED.score, "
                "source = EXCLUDED.source", [(a, b, s, src) for a, b, s, src in cands])
            # ponytail: pairs past the error cap still take `limit` slots (run() skips them for free); exclude them
            # here if they ever crowd out new pairs
            kal = {r["market_id"]: r for r in rows if r["venue"] == "kalshi"}
            pmr = {r["market_id"]: r for r in rows if r["venue"] == "polymarket"}
            # rule-decidable pairs count as done only when stored by a rule, so old LLM rows get replaced
            done = {(a, av, b, bv) for a, av, b, bv, model in conn.execute(
                "SELECT a_id, a_version, b_id, b_version, model FROM venue_arb.adjudications "
                "WHERE prompt_version = %s", (adjudicate.PROMPT_VERSION,)).fetchall()
                if model in rules.RULE_MODELS or not (a in kal and b in pmr and rules.decide(kal[a], pmr[b]))}
            pairs = unadjudicated(cands, kal, pmr, done, limit)
            m = adjudicate.month_spend(conn)
            budget = m + adjudicate.daily_budget(m, adjudicate.today_spend(conn), datetime.now(UTC).date())
            out = adjudicate.run(conn, pairs, month_spend=m, budget=budget) | {"candidates": len(cands)}
            (n,) = conn.execute(
                "SELECT count(*) FROM venue_arb.adjudications d "
                "JOIN venue_arb.listings la ON la.venue = d.a_venue AND la.market_id = d.a_id AND la.version = d.a_version "
                "JOIN venue_arb.listings lb ON lb.venue = d.b_venue AND lb.market_id = d.b_id AND lb.version = d.b_version"
            ).fetchone()
        results.append(checks.expect_rows(n, TARGET))
        results += [checks.Result(**r, target=TARGET) for r in adjudicate.run_checks(out)]
        results += sql.run_all(pattern="adjudications_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        log.info("match ok: %s", out)
        return out
    except checks.DataQualityError as e:
        results += [r for r in e.failures if r not in results]
        log.error("QA failed: %s", e)
        raise
    except Exception as e:
        results.append(checks.Result("flow_exception", False, detail=type(e).__name__, target=PIPELINE))
        raise
    finally:
        with db.pg() as conn:
            store.record(conn, PIPELINE, run_id, results)


if __name__ == "__main__":
    match_flow()
