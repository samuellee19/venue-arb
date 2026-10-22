"""Dashboard publisher: live.json every 60 s, and per clock hour one history Parquet and the research summary (in
live.json), pushed outbound with rsync.

    secrets uv run python -m venue_arb.publish                     # loop (dest from VENUE_ARB_PUBLISH_DEST)
    secrets uv run python -m venue_arb.publish history --hours 168  # backfill, hour by hour, idempotent

tick() is a plain function over injected readers (as collect.tick), so tests need no stack or network. Read-only:
the publisher writes nothing to the databases except its qa results.
"""

import argparse
import io
import logging
import math
import os
import subprocess
import tempfile
import time
import uuid
from bisect import bisect_left
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from venue_arb import bundle, config
from venue_arb.bundle import Annotation, Basis, Episode, LiveBundle, ResearchSummary, Snap, pair_key
from venue_arb.config import BASIS_WINDOW_DAYS, PIPELINE_PREFIX
from venue_arb.episodes import ALIGN_S
from venue_arb.exact import eligible
from venue_arb.ladder_edges import _LEGS, count_window
from venue_arb.matching import crypto_rule
from venue_arb.matching.adjudicate import PROMPT_VERSION
from venue_arb.scoring import snapshots
from venue_arb.sources import kalshi

# qa and venue_arb.flows.* (the flows package imports obs and qa) are imported lazily: unit tests (and CI) run
# without the platform checkout.

log = logging.getLogger("venue_arb.publish")
logging.getLogger("httpx").setLevel(logging.WARNING)
PIPELINE = f"{PIPELINE_PREFIX}_publish"
INTERVAL_S = 60
REFRESH_EVERY = 10  # ticks between pair/ladder/KPI refreshes
RECORD_EVERY = 5  # ticks between qa records (Ruling 1, as collect.py's flush_every)
HISTORY_DAYS = 7
HISTORY_SIZE_BUDGET_MB = 50
# local hour files older than this are deleted; the server's prune cron cutoff (8 d, Task 9) must be no shorter, or
# rsync (no --delete) copies back every file the cron removed
HISTORY_KEEP = timedelta(days=HISTORY_DAYS + 1)
LIVE_BYTES_BUDGET = 800_000  # real bundles are ~460 KB with ~215 pair rows; headroom for the research block
HISTORY_LAG = timedelta(minutes=55)  # an hour is written once score (:40) and ladder_score (:50) have scored it
GAP = timedelta(minutes=3)
RESEARCH_TIMEOUT_WARN_S = 30  # the hourly research summary blocks one cycle; warn when it takes longer
PUSH_TIMEOUT_S = 120  # rsync's --timeout covers stalled I/O, not a black-holed ssh connect
OUT_DIR = Path("data/bundle")
LIVE_TARGET, HISTORY_TARGET = "bundle/live.json", "bundle/history"
ATTRIBUTION = ["Order books: Kalshi public market-data API (kalshi.com)",
               "Order books: Polymarket public CLOB API (polymarket.com)",
               "Basis bounds derived from CF Benchmarks BRTI and Binance BTC/USDT; neither series is republished"]
CAVEATS = ["Kalshi fee coefficient from public docs; unverified against fills"]

# hundreds of strike ids: client-side binding (%(x)s) puts them in the POST body, not the URL (as ladder_score)
LATEST_BOOKS_SQL = f"""
SELECT ts, toString(venue), market_id, toString(side), level, price, size FROM venue_arb.book_levels_v2 FINAL
WHERE ts > now64(3) - INTERVAL {bundle.LIVE_MAX_AGE_S} SECOND
  AND ((venue = 'kalshi' AND market_id IN %(k)s) OR (venue = 'polymarket' AND market_id IN %(p)s))
"""
TITLES_SQL = """
SELECT venue, market_id, category, title, resolution_source, close_time, first_seen, raw FROM venue_arb.listings
WHERE (venue = 'kalshi' AND market_id = ANY(%(k)s)) OR (venue = 'polymarket' AND market_id = ANY(%(p)s))
"""
SPARKS_SQL = """
SELECT k, p, d, toStartOfHour(ts) AS h, avg(e) FROM (
  SELECT ts, kalshi_id AS k, pm_id AS p, direction AS d, best_edge AS e FROM venue_arb.edges FINAL
  WHERE ts >= {start:DateTime64(3, 'UTC')}
  UNION ALL
  SELECT ts, k_id, pm_id, direction, best_edge FROM venue_arb.ladder_edges FINAL
  WHERE ts >= {start:DateTime64(3, 'UTC')} AND k_id NOT LIKE 'ZZTEST-%'
) GROUP BY k, p, d, h
"""
# both sources; pair-source rows are scoped in Python (scope_episodes) with note.SCOPE_SQL's rows, as the note does
EPISODES_SQL = """
SELECT source, kalshi_id, pm_id, direction, start_ts, end_ts, max_edge, median_capacity_usd, transfer_cost_usd
FROM venue_arb.opportunities
WHERE kalshi_id NOT LIKE 'ZZTEST-%' AND coalesce(end_ts, now()) > now() - interval '7 days'
ORDER BY start_ts
"""
# the latest ladder_score run's denominator, from runs without a failed error-severity check (as note.ADMISSIBLE_SQL)
LADDER_KPI_SQL = f"""
SELECT r.observed, r.checked_at FROM qa.check_results r
WHERE r.pipeline = '{PIPELINE_PREFIX}_ladder_score' AND r.check_name = 'ladder_admissible_checks'
  AND NOT EXISTS (SELECT 1 FROM qa.check_results f WHERE f.run_id = r.run_id AND NOT f.passed AND f.severity = 'error')
ORDER BY r.checked_at DESC LIMIT 1
"""
VIOLATIONS_SQL = """
SELECT countIf(capacity_contracts > 0) FROM venue_arb.ladder_edges FINAL
WHERE kind = 'arb' AND k_id NOT LIKE 'ZZTEST-%' AND ts >= {lo:DateTime64(3, 'UTC')} AND ts < {hi:DateTime64(3, 'UTC')}
"""
SCREENED_SQL = ("SELECT countIf(net_gap_top >= 0.01), count(), uniqExact(ts) FROM venue_arb.screened_v2 FINAL "
                "WHERE k_price < 1")
GAPS_SQL = ("SELECT DISTINCT toStartOfMinute(ts) AS m FROM venue_arb.book_levels_v2 "
            "WHERE ts > now() - INTERVAL 7 DAY ORDER BY m")
BASIS_CHANGES_SQL = """
SELECT asset, basis_up, basis_down, min(ts) AS first_ts FROM venue_arb.ladder_edges
WHERE ts > now() - INTERVAL 7 DAY AND k_id NOT LIKE 'ZZTEST-%'
GROUP BY asset, basis_up, basis_down ORDER BY first_ts
"""
# history: an hour's scored rows, and the first ask level of each leg around them
EDGES_HOUR_SQL = """
SELECT ts, kalshi_id, pm_id, direction, best_edge, capacity_usd FROM venue_arb.edges FINAL
WHERE ts >= {lo:DateTime64(3, 'UTC')} AND ts < {hi:DateTime64(3, 'UTC')}
"""
LADDER_HOUR_SQL = """
SELECT ts, k_id, pm_id, direction, kind, best_edge, capacity_usd FROM venue_arb.ladder_edges FINAL
WHERE ts >= {lo:DateTime64(3, 'UTC')} AND ts < {hi:DateTime64(3, 'UTC')} AND k_id NOT LIKE 'ZZTEST-%'
"""
HISTORY_BOOKS_SQL = """
SELECT ts, toString(venue), market_id, toString(side), level, price, size FROM venue_arb.book_levels_v2 FINAL
WHERE ts >= fromUnixTimestamp64Milli(%(lo)s, 'UTC') AND ts < fromUnixTimestamp64Milli(%(hi)s, 'UTC') AND level = 0
  AND ((venue = 'kalshi' AND market_id IN %(k)s) OR (venue = 'polymarket' AND market_id IN %(p)s))
"""
# yes mapping of every exact pair ever adjudicated at the current prompt version (closed pairs included, for backfill)
SIDES_SQL = """
SELECT DISTINCT ON (a_id, b_id) a_id, b_id, yes_maps_to FROM venue_arb.adjudications
WHERE a_venue = 'kalshi' AND b_venue = 'polymarket' AND prompt_version = %(pv)s AND yes_maps_to IS NOT NULL
ORDER BY a_id, b_id, created_at DESC
"""
HISTORY_SCHEMA = pa.schema([("ts", pa.timestamp("ms", tz="UTC")), ("key", pa.string()), ("kind", pa.string()),
                            ("direction", pa.string())]
                           + [(c, pa.float64()) for c in ("net_edge_cents", "capacity_usd", "k_ask", "k_size",
                                                          "pm_ask", "pm_size", "fees_cents")])
LADDER_KIND = {"arb": "ladder", "near_exact": "near_exact"}


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def latest_books(rows) -> dict[tuple[str, str], Snap]:
    """Book rows (ts, venue, market_id, side, level, price, size) -> newest snapshot per (venue, market_id)."""
    return {k: next(reversed(v.items())) for k, v in snapshots(rows).items()}


def write_atomic(path: Path, data: bytes) -> None:
    """Temp file in the same directory + os.replace, so a reader (or rsync) never sees half a file."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
            tmp = f.name
            f.write(data)
        os.chmod(tmp, 0o644)  # temp files are 0600; rsync -a carries the mode to the server
        os.replace(tmp, path)
    except BaseException:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise


def push(src_dir: Path, dest: str, rsync=subprocess.run) -> bool:
    """rsync the bundle dir outbound (no --delete: the server prunes its own history). False on failure."""
    try:
        r = rsync(["rsync", "-az", "--timeout=30", f"{src_dir}/", dest], capture_output=True,
                  timeout=PUSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.error("push timed out after %ss", PUSH_TIMEOUT_S)
        return False
    if r.returncode:
        log.error("push failed exit=%s", r.returncode)  # the exit code only: stderr may name the host
        return False
    return True


def _first_ask(snaps: dict | None, t: datetime, side: str):
    """First ask level on `side` in the snapshot nearest t within ALIGN_S (episodes.align's tolerance)."""
    if not snaps:
        return None
    ts = list(snaps)  # ascending (scoring.snapshots)
    i = bisect_left(ts, t)
    near = [x for x in ts[max(0, i - 1):i + 1] if abs((x - t).total_seconds()) <= ALIGN_S]
    if not near:
        return None
    return next((lv for lv in snaps[min(near, key=lambda x: abs(x - t))] if lv.side == side), None)


def history_table(edge_rows, ladder_rows, books, sides: dict | None = None) -> pa.Table:
    """EDGES_HOUR_SQL and LADDER_HOUR_SQL rows + scoring.snapshots books -> one row per scored snapshot.
    k_ask/pm_ask = each leg's first ask at that snapshot, for exact and near-exact rows only; an exact row needs its
    pair's yes mapping in `sides` {(kalshi_id, pm_id): "yes"|"no"} to know the Polymarket side, else asks are null.
    Asks and sizes are null while config.DASHBOARD_SHOW_BOOKS is False; fees_cents (derived) is kept."""
    sides, flip = sides or {}, {"yes": "no", "no": "yes"}
    show = config.DASHBOARD_SHOW_BOOKS  # read at call time (tests monkeypatch it); fees stay, quotes go
    rows = []
    for ts, k, p, d, best, cap in edge_rows:
        ym = sides.get((k, p))
        legs = None if ym is None else ("yes", flip[ym]) if d == "k_yes_pm_no" else ("no", ym)  # as edges.score
        rows.append((ts, k, p, d, "exact", best, cap, legs))
    rows += [(ts, k, p, d, LADDER_KIND[kind], best, cap, _LEGS[d] if kind == "near_exact" else None)
             for ts, k, p, d, kind, best, cap in ladder_rows]
    cols = {f.name: [] for f in HISTORY_SCHEMA}
    for ts, k, p, d, kind, best, cap, legs in rows:
        ts = _utc(ts)
        ka = pa_ = None
        if legs:
            ka = _first_ask(books.get(("kalshi", k)), ts, legs[0])
            pa_ = _first_ask(books.get(("polymarket", p)), ts, legs[1])
        fee = None
        if best is not None and ka and pa_:  # first slice: best = 1 - k0 - pm0 - fees per contract
            fee = round(100 * (1 - ka.price - pa_.price - best), 4)
        for c, v in (("ts", ts), ("key", pair_key(k, p, d)), ("kind", kind), ("direction", d),
                     ("net_edge_cents", None if best is None else round(100 * best, 4)), ("capacity_usd", cap),
                     ("k_ask", ka.price if show and ka else None), ("k_size", ka.size if show and ka else None),
                     ("pm_ask", pa_.price if show and pa_ else None),
                     ("pm_size", pa_.size if show and pa_ else None), ("fees_cents", fee)):
            cols[c].append(v)
    return pa.table(cols, schema=HISTORY_SCHEMA)


def write_history_hour(ch, out_dir: Path, hour: datetime, sides: dict | None = None) -> Path:
    """The clock hour starting at `hour` -> <out_dir>/history/pairs-YYYYMMDDHH.parquet (zstd), rewritten whole."""
    lo = _hour(hour)
    hi = lo + timedelta(hours=1)
    edge = ch.query(EDGES_HOUR_SQL, parameters={"lo": lo, "hi": hi}).result_rows
    lad = ch.query(LADDER_HOUR_SQL, parameters={"lo": lo, "hi": hi}).result_rows
    k = sorted({r[1] for r in edge} | {r[1] for r in lad if r[4] == "near_exact"})
    p = sorted({r[2] for r in edge} | {r[2] for r in lad if r[4] == "near_exact"})
    pad = timedelta(seconds=ALIGN_S)
    books = snapshots(ch.query(HISTORY_BOOKS_SQL, parameters={
        "lo": round((lo - pad).timestamp() * 1000), "hi": round((hi + pad).timestamp() * 1000), "k": k, "p": p}
    ).result_rows) if k else {}
    buf = io.BytesIO()
    pq.write_table(history_table(edge, lad, books, sides), buf, compression="zstd")
    path = _hour_file(out_dir, lo)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, buf.getvalue())
    return path


def history_hour(now: datetime) -> datetime:
    """The newest clock hour both scoring flows have finished."""
    return _hour(now - HISTORY_LAG) - timedelta(hours=1)


def _hour_file(out_dir: Path, hour: datetime) -> Path:
    return out_dir / "history" / f"pairs-{hour:%Y%m%d%H}.parquet"


def missing_hours(out_dir: Path, now: datetime) -> list[datetime]:
    """Hours of the trailing HISTORY_DAYS window (ending at history_hour(now)) without a file."""
    last = history_hour(now)
    return [h for h in (last - timedelta(hours=i) for i in range(HISTORY_DAYS * 24))
            if not _hour_file(out_dir, h).exists()]


def history_due(out_dir: Path, now: datetime, written: datetime | None) -> list[datetime]:
    """Hours to (re)write, oldest first, once history_hour(now) moves past `written` (None: never written): the newest
    two (a late scorer's rows land on the second pass) plus every missing hour of the trailing window (a fresh start
    or an outage).
    ponytail: a first start writes up to HISTORY_DAYS * 24 hours in one cycle; the live push goes first."""
    h = history_hour(now)
    if written == h:
        return []
    return sorted({h, h - timedelta(hours=1)} | set(missing_hours(out_dir, now)))


def prune_history(out_dir: Path, now: datetime) -> list[Path]:
    """Delete local hour files older than HISTORY_KEEP (see its comment for the server cron's cutoff)."""
    cutoff = _hour_file(out_dir, _hour(now - HISTORY_KEEP)).name
    old = [f for f in (out_dir / "history").glob("pairs-*.parquet") if f.name < cutoff]
    for f in old:
        f.unlink()
    return old


def history_size_mb(out_dir: Path, now: datetime) -> float:
    """Size of the trailing HISTORY_DAYS of hour files."""
    cutoff = _hour_file(out_dir, now - timedelta(days=HISTORY_DAYS)).name
    return sum(f.stat().st_size for f in (out_dir / "history").glob("pairs-*.parquet") if f.name > cutoff) / 1e6


def spark_series(rows, start: datetime) -> dict[str, list[float | None]]:
    """SPARKS_SQL rows (k, p, direction, hour, avg best_edge) -> {key: SPARK_HOURS hourly ¢ points from `start`}."""
    out: dict[str, list[float | None]] = {}
    for k, p, d, h, avg in rows:
        i = int((_utc(h) - start) / timedelta(hours=1))
        if 0 <= i < bundle.SPARK_HOURS and avg is not None and math.isfinite(avg):
            out.setdefault(pair_key(k, p, d), [None] * bundle.SPARK_HOURS)[i] = round(100 * avg, 4)
    return out


def gap_annotations(minutes) -> list[Annotation]:
    """Ascending snapshot minutes -> one annotation per stretch longer than GAP without a snapshot."""
    return [Annotation(kind="collector_gap", at=a, until=b, key=None,
                       label=f"collector gap: no snapshots for {(b - a) // timedelta(minutes=1)} min")
            for a, b in pairwise(_utc(m) for m in minutes) if b - a > GAP]


def scope_episodes(rows: list[dict], scope: dict) -> list[dict]:
    """EPISODES_SQL rows -> the note's scope: pair-source rows only for (kalshi_id, pm_id) pairs whose latest
    adjudication is exact with no bad review (scope: {(a_id, b_id): {"exact", "bad_review"}}); ladder-source rows
    always (the note scopes only the pair tier)."""
    def ok(r):
        s = scope.get((r["kalshi_id"], r["pm_id"]))
        return s is not None and s["exact"] and not s["bad_review"]
    return [r for r in rows if r["source"] != "pair" or ok(r)]


def load_context(conn, ch, now: datetime) -> dict:
    """The slow-moving bundle parts, read every REFRESH_EVERY ticks: sparks, episodes, annotations, KPI inputs."""
    from venue_arb.report.note import SCOPE_SQL  # lazily: keeps the report module (numpy) off the import path

    start = _hour(now) - timedelta(hours=bundle.SPARK_HOURS - 1)
    sparks = spark_series(ch.query(SPARKS_SQL, parameters={"start": start}).result_rows, start)
    scope = {(k, p): {"exact": rel == "exact", "bad_review": bad}
             for k, p, rel, bad, _closed in conn.execute(SCOPE_SQL, {"pv": PROMPT_VERSION}).fetchall()}
    episodes = [Episode(key=pair_key(r["kalshi_id"], r["pm_id"], r["direction"]), start=r["start_ts"],
                        end=r["end_ts"], max_edge_cents=round(100 * r["max_edge"], 4),
                        median_capacity_usd=r["median_capacity_usd"], transfer_cost_usd=r["transfer_cost_usd"])
                for r in scope_episodes(_dicts(conn.execute(EPISODES_SQL)), scope)]
    notes = gap_annotations(m for (m,) in ch.query(GAPS_SQL).result_rows)
    notes += [Annotation(kind="basis_change", at=_utc(t), until=None, key=None,
                         label=f"{asset} basis bound: up ${up:,.0f} / down ${down:,.0f}")
              for asset, up, down, t in ch.query(BASIS_CHANGES_SQL).result_rows]
    kpi: dict = {}
    if row := conn.execute(LADDER_KPI_SQL).fetchone():
        observed, checked_at = row
        # ponytail: the window is re-derived from the result's time, so a run that crosses the hour mid-run is off
        # by one hour; parse the window from `detail` (as the note does) if that ever shows
        lo, hi = count_window(checked_at)
        (viol,) = ch.query(VIOLATIONS_SQL, parameters={"lo": lo, "hi": hi}).result_rows[0]
        kpi |= {"checks_window": [lo, hi], "admissible_checks": int(observed), "violations": int(viol)}
    hits, total, hours = ch.query(SCREENED_SQL).result_rows[0]
    if total:
        kpi |= {"screened_share_ge_1c": hits / total, "screened_hours": int(hours)}
    return {"sparks": sparks, "episodes": episodes, "annotations": notes, "kpi_inputs": kpi}


def _series(listing: dict) -> str | None:
    return ((listing.get("raw") or {}).get("event") or {}).get("series_ticker")


def refresh(readers: dict, now: datetime) -> dict:
    """Pairs, ladders, bounds, listings and fees; pairs and strikes without a listing row are skipped and counted."""
    pairs, (found, bounds) = readers["pairs"](), readers["ladders"](now)
    skipped = {"pairs_no_listing": 0, "strikes_no_listing": 0, "strikes_no_series": 0, "ladders_no_basis": 0}
    ladders = {}
    for (asset, instant), lad in sorted(found.items()):
        if asset in bounds:
            ladders[(asset, instant)] = lad
        else:
            skipped["ladders_no_basis"] += 1
    ids = {("kalshi", p.kalshi_id) for p in pairs} | {("polymarket", p.pm_id) for p in pairs}
    ids |= {(s.venue, s.market_id) for lad in ladders.values() for xs in lad.values() for s in xs}
    titles = readers["listings"](ids)
    ok = [p for p in pairs if ("kalshi", p.kalshi_id) in titles and ("polymarket", p.pm_id) in titles]
    skipped["pairs_no_listing"] = len(pairs) - len(ok)
    for key, lad in ladders.items():
        kept = {v: [s for s in xs if (v, s.market_id) in titles] for v, xs in lad.items()}
        skipped["strikes_no_listing"] += sum(map(len, lad.values())) - sum(map(len, kept.values()))
        series_ok = [s for s in kept["kalshi"] if _series(titles[("kalshi", s.market_id)])]
        skipped["strikes_no_series"] += len(kept["kalshi"]) - len(series_ok)
        ladders[key] = kept | {"kalshi": series_ok}
    if any(skipped.values()):
        log.warning("refresh skipped %s", skipped)
    series = {p.kalshi_series for p in ok}
    series |= {_series(titles[("kalshi", s.market_id)]) for lad in ladders.values() for s in lad["kalshi"]}
    return {"pairs": ok, "ladders": ladders, "titles": titles, "skipped": skipped,
            "fees": {s: readers["series_fee"](s) for s in sorted(series)},  # once per series per refresh
            "basis": {a: Basis(up=up, down=down, window_days=BASIS_WINDOW_DAYS, asof=now)
                      for a, (up, down) in bounds.items()},
            "k_ids": sorted(m for v, m in ids if v == "kalshi"), "pm_ids": sorted(m for v, m in ids if v == "polymarket"),
            "context": readers["context"](now)}


def _listing_note(p, titles: dict, now: datetime) -> list[Annotation]:
    """Listing line per row when the later leg was first seen in the last HISTORY_DAYS."""
    out = []
    seen = [(titles[(lg.venue, lg.market_id)].get("first_seen"), lg.venue) for lg in p.legs]
    if None not in (seen[0][0], seen[1][0]):
        at, venue = max(seen)
        if at > now - timedelta(days=HISTORY_DAYS):
            out.append(Annotation(kind="listing", at=at, until=None, key=p.key, label=f"listed on {venue.title()}"))
    return out


def tick(state: dict, readers: dict, now: datetime) -> LiveBundle:
    """Latest books every call; refresh() every REFRESH_EVERY calls. A refresh that raises is retried next call; the
    previous refresh's state serves meanwhile (its error type goes to state["refresh_errors"]). Without one, it raises."""
    if state.get("since_refresh", REFRESH_EVERY) >= REFRESH_EVERY:
        try:
            state |= refresh(readers, now) | {"since_refresh": 0}
        except Exception as e:
            if "titles" not in state:
                raise
            log.warning("refresh failed, keeping the previous pairs error=%s", type(e).__name__)
            state.setdefault("refresh_errors", []).append(type(e).__name__)
    state["since_refresh"] += 1
    books = readers["books"](state["k_ids"], state["pm_ids"])
    titles, fees, ctx = state["titles"], state["fees"], state["context"]

    def k_fee(market_id: str) -> dict:
        return fees[_series(titles[("kalshi", market_id)])]

    def pm_fee(market_id: str) -> tuple[dict | None, bool]:  # the market's own feeSchedule, as ladder_score
        m = titles[("polymarket", market_id)]["raw"].get("market") or {}
        return m.get("feeSchedule"), bool(m.get("feesEnabled"))

    exact = [r for p in state["pairs"]
             for r in bundle.exact_pair_rows(p, titles, books.get(("kalshi", p.kalshi_id)),
                                             books.get(("polymarket", p.pm_id)), fees[p.kalshi_series],
                                             ctx["sparks"], now)]
    ladder = [r for (asset, instant), lad in state["ladders"].items()
              for r in bundle.ladder_pair_rows(asset, instant, lad, books, state["basis"][asset].up,
                                               state["basis"][asset].down, k_fee, pm_fee, titles, ctx["sparks"], now)]
    # one settlement line per instant, key None (ladder rows share instants); the client draws it only on pairs whose
    # settles_at equals it, and a basis_change only on ladder pairs of the asset its label names
    notes = ctx["annotations"] + [Annotation(kind="settlement", at=t, until=None, key=None, label="settles")
                                  for t in sorted({p.settles_at for p in exact + ladder})]
    notes += [a for p in exact + ladder for a in _listing_note(p, titles, now)]
    return bundle.build_live(now=now, exact_rows=exact, ladder_rows=ladder, basis=state["basis"],
                             episodes=ctx["episodes"], annotations=notes, kpi_inputs=ctx["kpi_inputs"],
                             attribution=ATTRIBUTION, caveats=CAVEATS,
                             collector_last=max((b[0] for b in books.values()), default=None),
                             research=state.get("research"))


def compute_research(readers: dict, now: datetime) -> ResearchSummary:
    """The note's numbers reduced to the bundle's research block. Readers: research_inputs() -> note.load's dict,
    review_labels() -> (labels of current-verdict reviews, newest reviewed_at)."""
    from venue_arb.matching.review import precision
    from venue_arb.report import note, research  # lazily: numpy off the import path, as load_context

    s = note.summarize(**readers["research_inputs"](), generated_at=now)
    labels, asof = readers["review_labels"]()
    p, n = precision(labels)
    return research.from_summary(s, review={"precision": p, "labels": n, "asof": asof} if n else None,
                                 computed_at=now)


def _dicts(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def load_sides(conn) -> dict[tuple[str, str], str]:
    return {(k, p): ym for k, p, ym in conn.execute(SIDES_SQL, {"pv": PROMPT_VERSION}).fetchall()}


def default_readers(db) -> dict:
    """tick()'s readers on the live databases (`db` = qa.db); each opens its own connection."""
    from venue_arb.flows.exact_sql import load_eligible
    from venue_arb.flows.ladder_score import BASIS_SQL, LISTINGS_SQL, bounds_by_asset

    def pairs():
        with db.pg() as conn:
            return eligible(load_eligible(conn))

    def ladders(now):
        with db.pg() as conn:
            rows = _dicts(conn.execute(LISTINGS_SQL, {"start": now}))
        return (crypto_rule.ladders(rows),
                bounds_by_asset(db.ch().query(BASIS_SQL, parameters={"end": now}).result_rows))

    def listings(ids):
        with db.pg() as conn:
            rows = _dicts(conn.execute(TITLES_SQL, {"k": [m for v, m in ids if v == "kalshi"],
                                                    "p": [m for v, m in ids if v == "polymarket"]}))
        return {(r["venue"], r["market_id"]): r for r in rows}

    def books(k, p):  # [""]: an empty IN list is a syntax error; "" matches no market
        return latest_books(db.ch().query(LATEST_BOOKS_SQL, parameters={"k": k or [""], "p": p or [""]}).result_rows)

    def context(now):
        with db.pg() as conn:
            return load_context(conn, db.ch(), now)

    def research_inputs():
        from venue_arb.report import note

        with db.pg() as conn:  # one connection for the whole load, closed on return
            return note.load(conn, db.ch())

    def review_labels():
        from venue_arb.flows.review_cli import all_review_labels  # imports qa at module level

        with db.pg() as conn:
            return (all_review_labels(conn),
                    conn.execute("SELECT max(reviewed_at) FROM venue_arb.reviews").fetchone()[0])

    return {"pairs": pairs, "ladders": ladders, "listings": listings, "series_fee": kalshi.fetch_series,
            "books": books, "context": context, "research_inputs": research_inputs, "review_labels": review_labels}


def cycle(state: dict, readers: dict, out_dir: Path, dest: str | None, now: datetime, *, checks, db=None,
          write=write_atomic, push=push, clock=time.monotonic) -> list:
    """One loop iteration, in order: tick -> live.json -> push -> due history hours -> research (once per
    history_hour, so the summary lands in the next tick's bundle). Each step after the push has its own try, so its
    failure never stops live.json. Returns the qa results of failed steps; the last good bundle, push outcomes and
    cycle seconds go to state ("live", "pushes", "cycles") for _results."""
    pending = []
    started = clock()
    try:
        live = tick(state, readers, now)
        data = live.model_dump_json().encode()
        state["live"], state["live_bytes"] = live, len(data)
        write(out_dir / "live.json", data)
        if dest:  # history files written below go out with the next tick's push
            state.setdefault("pushes", []).append(push(out_dir, dest))
        state.setdefault("cycles", []).append(clock() - started)
    except Exception as e:
        log.exception("publish cycle failed")
        pending.append(checks.Result("cycle_exception", False, detail=type(e).__name__, target=PIPELINE))
    try:
        if due := history_due(out_dir, now, state.get("history_hour")):
            with db.pg() as conn:
                sides = load_sides(conn)
            ch = db.ch()
            for hour in due:
                write_history_hour(ch, out_dir, hour, sides)
            state["history_hour"] = history_hour(now)
            prune_history(out_dir, now)
            pending += _history_results(checks, out_dir, now)
    except Exception as e:
        log.exception("history write failed")
        pending.append(checks.Result("history_exception", False, detail=type(e).__name__, target=HISTORY_TARGET))
    if history_hour(now) != state.get("research_hour"):
        t0 = clock()
        try:  # a failure keeps the previous summary and retries next cycle
            state["research"] = compute_research(readers, now)
            state["research_hour"], state["research_seconds"] = history_hour(now), clock() - t0
        except Exception:
            log.exception("research summary failed, keeping the previous one")
            state["research_failed"] = state.get("research_failed", 0) + 1
    return pending


def run_forever(out_dir: Path, dest: str | None, interval_s: int = INTERVAL_S) -> None:
    from qa import checks, db, store

    readers, state, n, pending = default_readers(db), {}, 0, []
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dest:
        log.warning("VENUE_ARB_PUBLISH_DEST unset: writing %s locally, no push", out_dir)
    while True:
        started = time.monotonic()
        n += 1
        pending += cycle(state, readers, out_dir, dest, datetime.now(UTC), checks=checks, db=db)
        try:
            if n % RECORD_EVERY == 0:
                results = pending + _results(checks, state, interval_s)
                with db.pg() as conn:
                    store.record(conn, PIPELINE, str(uuid.uuid4()), results)
                pending, state["pushes"], state["cycles"] = [], [], []
        except Exception:
            log.exception("qa record failed")
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


def _history_results(checks, out_dir: Path, now: datetime) -> list:
    mb, missing = history_size_mb(out_dir, now), len(missing_hours(out_dir, now))
    return [checks.Result("history_size_mb", mb <= HISTORY_SIZE_BUDGET_MB, round(mb, 2), HISTORY_SIZE_BUDGET_MB,
                          f"trailing {HISTORY_DAYS} d of hour files", severity="warn", target=HISTORY_TARGET),
            checks.Result("history_missing_hours", missing == 0, missing, 0,
                          f"{missing} of the trailing {HISTORY_DAYS * 24} hour files missing", severity="warn",
                          target=HISTORY_TARGET)]


def _results(checks, state: dict, interval_s: int) -> list:
    """qa results since the last record. state["pushes"]: True/False per attempted push; state["cycles"]: seconds."""
    live, pushes, cycles, out = state.get("live"), state.get("pushes", []), state.get("cycles", []), []
    if live is not None:  # the last good bundle: a stale one fails bundle_fresh
        out.append(replace(checks.expect_rows(len(live.pairs), LIVE_TARGET), check="bundle_pairs",
                           detail=f"{len(live.pairs)} pair rows; skipped {state.get('skipped')}"))
        out.append(replace(checks.expect_fresh(live.collector_last_snapshot_at, LIVE_TARGET,
                                               timedelta(seconds=bundle.LIVE_MAX_AGE_S)),
                           check="bundle_fresh", severity="warn"))
        size = state.get("live_bytes", 0)
        out.append(checks.Result("live_bytes", size <= LIVE_BYTES_BUDGET, size, LIVE_BYTES_BUDGET,
                                 f"live.json is {size:,} bytes", severity="warn", target=LIVE_TARGET))
    errs = state.pop("refresh_errors", [])
    out.append(checks.Result("refresh_failed", not errs, len(errs), 0,
                             f"{len(errs)} refreshes failed, previous pairs kept: {sorted(set(errs))}",
                             severity="warn", target=LIVE_TARGET))
    failed = state.pop("research_failed", 0)
    out.append(checks.Result("research_failed", not failed, failed, 0,
                             f"{failed} research computations failed, previous summary kept", severity="warn",
                             target=LIVE_TARGET))
    if (secs := state.pop("research_seconds", None)) is not None:  # only when one ran since the last record
        out.append(checks.Result("research_seconds", secs <= RESEARCH_TIMEOUT_WARN_S, round(secs, 1),
                                 RESEARCH_TIMEOUT_WARN_S, f"research summary took {secs:.1f}s", severity="warn",
                                 target=LIVE_TARGET))
    if pushes:
        bad = pushes.count(False)
        out.append(checks.Result("push", bad == 0, bad, 0, f"{bad}/{len(pushes)} pushes failed", target=LIVE_TARGET))
    if cycles:
        worst = max(cycles)
        out.append(checks.Result("cycle_seconds", worst <= interval_s, round(worst, 1), interval_s,
                                 f"slowest of {len(cycles)} cycles took {worst:.1f}s", severity="warn",
                                 target=LIVE_TARGET))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m venue_arb.publish")
    ap.add_argument("command", nargs="?", choices=["history"], help="omit to run the 60 s loop")
    ap.add_argument("--hours", type=int, default=HISTORY_DAYS * 24, help="history: hours to (re)write")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    dest = os.environ.get("VENUE_ARB_PUBLISH_DEST") or None
    if args.command != "history":
        run_forever(args.out, dest)
        return
    from qa import db

    with db.pg() as conn:
        sides = load_sides(conn)
    last = history_hour(datetime.now(UTC))
    for h in range(args.hours - 1, -1, -1):  # oldest first; rewriting an hour is idempotent
        log.info("history %s", write_history_hour(db.ch(), args.out, last - timedelta(hours=h), sides))
    if dest and not push(args.out, dest):
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
