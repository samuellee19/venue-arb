"""Review sampling and trust math. Pure: no DB."""

import random
from collections import defaultdict

BANDS = ((0.0, 0.8), (0.8, 0.95), (0.95, 1.01))
MIN_PER_CELL = 5


def band(confidence: float) -> int:
    return next(i for i, (lo, hi) in enumerate(BANDS) if lo <= confidence < hi)


def stratified_sample(rows: list[dict], n: int = 200, seed: int = 7) -> list[dict]:
    """Rows need `category` and `confidence`. Proportional per (category, band) cell, at least 5 per cell."""
    cells = defaultdict(list)
    for r in rows:
        cells[(r["category"], band(r["confidence"]))].append(r)
    if len(rows) <= n:
        return list(rows)
    keys = sorted(cells)
    take = {k: min(MIN_PER_CELL, len(cells[k])) for k in keys}
    room = {k: len(cells[k]) - take[k] for k in keys}
    left = max(n - sum(take.values()), 0)
    total_room = sum(room.values())
    # largest-remainder split of what's left, proportional to each cell's spare rows
    share = {k: left * room[k] / total_room for k in keys} if total_room else {}
    for k, v in share.items():
        take[k] += int(v)
    left -= sum(int(s) for s in share.values())
    for k in sorted(share, key=lambda k: share[k] - int(share[k]), reverse=True):
        if left and take[k] < len(cells[k]):
            take[k] += 1
            left -= 1
    rng = random.Random(seed)
    out = []
    for k in keys:
        out += rng.sample(cells[k], take[k])
    return out


def precision(labels: list[str]) -> tuple[float, int]:
    used = [x for x in labels if x != "unsure"]
    return (sum(x == "correct" for x in used) / len(used) if used else 0.0), len(used)


def trust(labels: list[str]) -> tuple[float, int, bool]:
    p, n = precision(labels)
    return p, n, p >= 0.95 and n >= 150


def recall(known: list[tuple[str, str]], found: set[tuple[str, str]]) -> float:
    return sum(p in found for p in known) / len(known) if known else 0.0
