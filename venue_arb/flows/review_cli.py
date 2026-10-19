"""Hand review of adjudicated matches. Run: secrets uv run python -m venue_arb.flows.review_cli"""

import csv
from collections import defaultdict
from pathlib import Path

from qa import db

from venue_arb.matching.review import band, precision, recall, stratified_sample

KNOWN = Path("labels/known_pairs.csv")
PASS_PRECISION, PASS_COUNT = 0.95, 150

SAMPLE_SQL = """
SELECT a.a_venue, a.a_id, a.a_version, a.b_venue, a.b_id, a.b_version, a.relation, a.reason, a.confidence,
       l.category, l.title AS a_title, l.rules AS a_rules, l.close_time AS a_close,
       p.title AS b_title, p.rules AS b_rules, p.close_time AS b_close
FROM venue_arb.adjudications a
JOIN venue_arb.listings l ON l.venue = a.a_venue AND l.market_id = a.a_id
JOIN venue_arb.listings p ON p.venue = a.b_venue AND p.market_id = a.b_id
WHERE a.relation <> 'different' AND l.category IS NOT NULL
"""
KEY = ("a_venue", "a_id", "a_version", "b_venue", "b_id", "b_version")


def key(r: dict) -> tuple:
    return tuple(r[k] for k in KEY)


def load_sample(conn, n: int = 200, seed: int = 7) -> list[dict]:
    cur = conn.execute(SAMPLE_SQL)
    cols = [c.name for c in cur.description]
    return stratified_sample([dict(zip(cols, row, strict=True)) for row in cur.fetchall()], n, seed)


def load_labels(conn) -> dict[tuple, str]:
    rows = conn.execute("SELECT a_venue,a_id,a_version,b_venue,b_id,b_version,label FROM venue_arb.reviews").fetchall()
    return {tuple(r[:6]): r[6] for r in rows}


def save_review(conn, r: dict, label: str, note: str) -> None:
    conn.execute(
        "INSERT INTO venue_arb.reviews (a_venue,a_id,a_version,b_venue,b_id,b_version,label,note) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (*key(r), label, note or None),
    )


def found_pairs(conn) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT a_id, b_id FROM venue_arb.adjudications WHERE relation <> 'different'"
    ).fetchall()
    return {(a, b) for a, b in rows}


def show(r: dict, i: int, total: int) -> None:
    ex = lambda s: " ".join((s or "").split())[:400]
    print(f"\n[{i}/{total}] {r['category']}  confidence {r['confidence']:.2f}")
    print(f"KALSHI     {r['a_title']}\n  rules: {ex(r['a_rules'])}\n  closes: {r['a_close']}")
    print(f"POLYMARKET {r['b_title']}\n  rules: {ex(r['b_rules'])}\n  closes: {r['b_close']}")
    print(f"VERDICT {r['relation']}: {r['reason']}")


def review_loop(conn, sample: list[dict], labels: dict, ask=input) -> None:
    todo = [r for r in sample if key(r) not in labels]
    for i, r in enumerate(todo, 1):
        show(r, i, len(todo))
        ans = ""
        while not ans or ans[0].lower() not in "cwuq":
            ans = ask("c)orrect / w)rong / u)nsure / q)uit [+ note]: ").strip()
        if ans[0].lower() == "q":
            return
        label = {"c": "correct", "w": "wrong", "u": "unsure"}[ans[0].lower()]
        save_review(conn, r, label, ans[1:].strip())
        labels[key(r)] = label


def report(sample: list[dict], labels: dict, known: list[tuple[str, str]], found: set) -> bool:
    done = [(r, labels[key(r)]) for r in sample if key(r) in labels]
    p, used = precision([lab for _, lab in done])
    print(f"\nOverall precision {p:.3f} on {used} labeled ({len(sample)} sampled)")
    cells = defaultdict(list)
    for r, lab in done:
        cells[(r["category"], band(r["confidence"]))].append(lab)
    for c in sorted(cells):
        cp, cn = precision(cells[c])
        print(f"  {c[0]:13} band {c[1]}: {cp:.3f} (n={cn})")
    print(f"Recall vs known pairs: {recall(known, found):.3f} ({len(known)} known)")
    ok = p >= PASS_PRECISION and used >= PASS_COUNT
    print("PASS" if ok else "NOT YET")
    return ok


def load_known(path: Path = KNOWN) -> list[tuple[str, str]]:
    with path.open() as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return [(r["kalshi_market_id"], r["pm_market_id"]) for r in rows]


def main() -> None:
    with db.pg() as conn:
        sample = load_sample(conn)
        labels = load_labels(conn)
        review_loop(conn, sample, labels)
        report(sample, labels, load_known(), found_pairs(conn))


if __name__ == "__main__":
    main()
