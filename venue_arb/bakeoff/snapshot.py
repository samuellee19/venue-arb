"""Bake-off input: current listings -> content-hashed Parquet for the Colab job, plus the truth set from the
same read so both refer to the same markets.

    secrets uv run python -m venue_arb.bakeoff.snapshot [out_dir]   (default data/bakeoff, gitignored)
"""

import csv
import hashlib
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from venue_arb.bakeoff.truth import crypto_pairs
from venue_arb.matching.embed import text_for

KNOWN = Path("labels/known_pairs.csv")
TRUTH = Path("labels/crypto_near_pairs.csv")
COLS = ("venue", "market_id", "event_id", "category", "title", "yes_label", "close_time")
# ponytail: same filter as flows.match.current_listings, copied because importing the flow pulls in prefect
QUERY = (f"SELECT {', '.join(COLS)} FROM venue_arb.listings "
         "WHERE last_seen > now() - INTERVAL '2 hours' AND category IS NOT NULL ORDER BY venue, market_id")


def write_parquet(rows: list[dict], path: Path) -> str:
    """Write venue, market_id, category, close_time, text; return the file's sha256."""
    table = pa.table({
        "venue": [r["venue"] for r in rows],
        "market_id": [r["market_id"] for r in rows],
        "category": [r["category"] for r in rows],
        "close_time": pa.array([r["close_time"] for r in rows], pa.timestamp("us", tz="UTC")),
        "text": [text_for(r) for r in rows],
    })
    pq.write_table(table, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def truth_rows(rows: list[dict], known: list[dict]) -> list[dict]:
    """Crypto near-pairs parsed from `rows`, then the Fed exact pairs from known_pairs.csv whose markets are present."""
    by = {(r["venue"], r["market_id"]): r for r in rows}
    k = [r for r in rows if r["venue"] == "kalshi"]
    p = [r for r in rows if r["venue"] == "polymarket"]
    out = [{"kalshi_market_id": a, "pm_market_id": b, "kind": kind,
            "note": f"{by['kalshi', a]['title']} {by['kalshi', a]['yes_label']} ~ {by['polymarket', b]['title']}"}
           for a, b, kind in crypto_pairs(k, p)]
    out += [{"kalshi_market_id": r["kalshi_market_id"], "pm_market_id": r["pm_market_id"], "kind": "fed_exact",
             "note": r["note"]} for r in known
            if ("kalshi", r["kalshi_market_id"]) in by and ("polymarket", r["pm_market_id"]) in by]
    return out


def main(out_dir: str = "data/bakeoff") -> None:
    from qa import checks, db

    with db.pg() as conn:
        rows = [dict(zip(COLS, r, strict=True)) for r in conn.execute(QUERY).fetchall()]
    checks.enforce([checks.expect_rows(len(rows), "venue_arb.listings")])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sha = write_parquet(rows, out / "listings.parquet")
    (out / "listings.parquet.sha256").write_text(sha + "\n")
    known = list(csv.DictReader(line for line in KNOWN.open() if not line.startswith("#")))
    truth = truth_rows(rows, known)
    with TRUTH.open("w", newline="") as f:
        f.write(f"# candidate recall is hour-agnostic: same asset, ET date, direction, threshold (Sam, 2026-10-20)\n# built from listings snapshot sha256 {sha}\n")
        w = csv.DictWriter(f, ["kalshi_market_id", "pm_market_id", "kind", "note"])
        w.writeheader()
        w.writerows(truth)
    kinds = {t["kind"] for t in truth}
    print(f"{len(rows)} listings -> {out / 'listings.parquet'} sha256 {sha}; truth "
          + ", ".join(f"{kd}={sum(t['kind'] == kd for t in truth)}" for kd in sorted(kinds))
          + f" (fed known {len(known)})")


if __name__ == "__main__":
    main(*sys.argv[1:])
