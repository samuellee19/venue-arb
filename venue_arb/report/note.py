"""Research note v1: do exact Kalshi<->Polymarket matches carry arbitrage net of fees and depth?

    secrets uv run python -m venue_arb.report.note      # writes reports/research-note-v1.md + PNGs

`summarize` and `render` are pure (no qa); `main` loads from the DB. The screened tier (hourly history) and the
executable tier (our own order-book snapshots) are summarized from separate inputs into separate keys and are never
combined into one number.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

import numpy as np

from venue_arb.episodes import ALIGN_S, MAX_GAP_S, MIN_EDGE
from venue_arb.exact import MIN_CONFIDENCE, ExactPair
from venue_arb.fees import FEE_VERSION, KALSHI_RATE, TRANSFER, US_EXECUTABLE
from venue_arb.matching.adjudicate import PROMPT_VERSION
from venue_arb.matching.fed_rule import RULE_MODEL

NOTE = "research-note-v1.md"
SCREENED_PNG = "screened-net-gap.png"
EXEC_PNG = "executable-edge.png"
EARLY_DAYS = 7  # "early life": a pair's first week of screened history
CONCLUSIVE_H = 14 * 24  # below this much executable data the short answer says it is too short to conclude
# The builder's reading of LLM-written tail_risks text: clauses that read like real contract differences or are muddled,
# to be checked at a human review sitting before v1-final. Rule-judged pairs (fixed TAIL text) need no entries.
# {kalshi_id: {clause number (1-based, 0 = the pair as a whole): reason}}. Drop entries once reviewed.
REVIEW_FLAGS_DATE = "2026-10-19"
NEEDS_REVIEW: dict[str, dict[int, str]] = {}  # none now: every current pair is a Fed bracket judged by rule

# Queries main() runs; quoted verbatim in the note so every number traces to a table.
SCREENED_SQL = ("SELECT ts, kalshi_id, pm_id, direction, net_gap_top FROM venue_arb.screened_v2 FINAL "
                "WHERE k_price < 1")
EDGES_SQL = "SELECT ts, kalshi_id, pm_id, direction, best_edge, capacity_usd FROM venue_arb.edges FINAL"
OPPS_SQL = ("SELECT kalshi_id, pm_id, direction, start_ts, end_ts, snapshots, max_edge, median_capacity_usd, "
            "annualized, us_executable, transfer_cost_usd FROM venue_arb.opportunities")
# Tier scope (Ruling 11): pairs whose latest adjudication at the current prompt version (%(pv)s) is 'exact' and that
# carry no wrong/unsure review made at that prompt version, whether or not they are open now (a closed pair's history
# is still evidence). A review made against an older prompt's verdict judged a different verdict and is ignored.
SCOPE_SQL = """
WITH latest AS (
  SELECT DISTINCT ON (a_id, b_id) a_id, b_id, relation FROM venue_arb.adjudications
  WHERE a_venue = 'kalshi' AND b_venue = 'polymarket' AND prompt_version = %(pv)s
  ORDER BY a_id, b_id, created_at DESC),
bad AS (SELECT DISTINCT a_id, b_id FROM venue_arb.reviews
  WHERE a_venue = 'kalshi' AND b_venue = 'polymarket' AND label IN ('wrong', 'unsure') AND prompt_version = %(pv)s),
cl AS (SELECT DISTINCT ON (venue, market_id) venue, market_id, close_time FROM venue_arb.listings
  ORDER BY venue, market_id, last_seen DESC)
SELECT l.a_id, l.b_id, l.relation, b.a_id IS NOT NULL AS bad_review,
       coalesce(least(k.close_time, p.close_time) <= now(), false) AS closed
FROM latest l LEFT JOIN bad b USING (a_id, b_id)
LEFT JOIN cl k ON k.venue = 'kalshi' AND k.market_id = l.a_id
LEFT JOIN cl p ON p.venue = 'polymarket' AND p.market_id = l.b_id
"""
# latest adjudication at the listings' current versions, as in flows/exact_sql.ELIGIBLE_SQL
ADJ_SQL = """
SELECT DISTINCT ON (k.market_id, p.market_id) k.market_id, p.market_id, a.tail_risks, a.prompt_version, a.model
FROM venue_arb.adjudications a
JOIN venue_arb.listings k ON k.venue = a.a_venue AND k.market_id = a.a_id AND k.version = a.a_version
JOIN venue_arb.listings p ON p.venue = a.b_venue AND p.market_id = a.b_id AND p.version = a.b_version
WHERE a.a_venue = 'kalshi' AND a.b_venue = 'polymarket' AND a.a_id = ANY(%(k)s)
  AND k.last_seen > now() - interval '2 hours' AND p.last_seen > now() - interval '2 hours'
ORDER BY k.market_id, p.market_id, a.created_at DESC
"""


def _q(xs: list[float], q: float) -> float | None:
    return float(np.percentile(xs, q)) if xs else None


def _share(n: int, d: int) -> float:
    return n / d if d else 0.0


def _screened(rows: list[dict]) -> dict:
    net = [r["net_gap_top"] for r in rows]
    first = {}
    for r in rows:
        k = r["kalshi_id"]
        first[k] = min(first.get(k, r["ts"]), r["ts"])
    hits = [r for r in rows if r["net_gap_top"] >= MIN_EDGE]
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[r["kalshi_id"]].append(r["net_gap_top"])
    return {
        "rows": len(rows),
        "pairs": len(by_pair),
        "pair_hours": len({(r["kalshi_id"], r["ts"]) for r in rows}),
        "start": min((r["ts"] for r in rows), default=None),
        "end": max((r["ts"] for r in rows), default=None),
        "n_gt0": sum(x > 0 for x in net),
        "n_ge_001": len(hits),
        "share_gt0": _share(sum(x > 0 for x in net), len(net)),
        "share_ge_001": _share(len(hits), len(net)),
        "n_early": sum(r["ts"] < first[r["kalshi_id"]] + timedelta(days=EARLY_DAYS) for r in rows),
        "n_ge_001_early": sum(r["ts"] < first[r["kalshi_id"]] + timedelta(days=EARLY_DAYS) for r in hits),
        "p50": _q(net, 50), "p90": _q(net, 90), "max": max(net, default=None),
        "per_pair": [{"kalshi_id": k, "rows": len(v), "p50": _q(v, 50), "max": max(v),
                      "n_ge_001": sum(x >= MIN_EDGE for x in v), "first": first[k]}
                     for k, v in sorted(by_pair.items())],
        "values": net,
    }


def _executable(edge_rows: list[dict], episodes: list[dict]) -> dict:
    snaps = defaultdict(list)  # (kalshi_id, ts) -> rows for both directions
    for r in edge_rows:
        snaps[(r["kalshi_id"], r["ts"])].append(r)
    with_cap = sum(any(r["capacity_usd"] > 0 for r in v) for v in snaps.values())
    best = [r["best_edge"] for r in edge_rows if r["best_edge"] is not None]
    series = defaultdict(list)  # per pair: best edge over both directions at each snapshot
    for (k, ts), v in sorted(snaps.items(), key=lambda kv: kv[0][1]):
        b = [r["best_edge"] for r in v if r["best_edge"] is not None]
        if b:
            series[k].append((ts, max(b)))
    dur = [(e["end_ts"] - e["start_ts"]).total_seconds() / 60 for e in episodes]
    cap = [e["median_capacity_usd"] for e in episodes]
    # ponytail: approximate and optimistic: profit ~ max_edge * contracts, contracts ~ capacity_usd / (1 - max_edge),
    # i.e. the episode's best edge on all its median capacity. Store the episode's median avg_edge to tighten this.
    net_ok = sum(e["max_edge"] * e["median_capacity_usd"] / (1 - e["max_edge"]) > e["transfer_cost_usd"]
                 for e in episodes)
    in_ep = [(r["capacity_usd"], r["best_edge"]) for r in edge_rows for e in episodes
             if (r["kalshi_id"], r["pm_id"], r["direction"]) == (e["kalshi_id"], e["pm_id"], e["direction"])
             and e["start_ts"] <= r["ts"] <= e["end_ts"] and r["best_edge"] is not None]
    by_pair = defaultdict(list)
    for (k, _), v in snaps.items():
        by_pair[k].append(v)
    return {
        "rows": len(edge_rows),
        "pairs": len(by_pair),
        "snapshots": len(snaps),
        "start": min((r["ts"] for r in edge_rows), default=None),
        "end": max((r["ts"] for r in edge_rows), default=None),
        "n_capacity_gt0": with_cap,
        "share_capacity_gt0": _share(with_cap, len(snaps)),
        "best_edge_p50": _q(best, 50), "best_edge_max": max(best, default=None),
        "episodes": len(episodes),
        "duration_min_median": median(dur) if dur else None, "duration_min_max": max(dur, default=None),
        "capacity_usd_median": median(cap) if cap else None, "capacity_usd_max": max(cap, default=None),
        "annualized_median": median(e["annualized"] for e in episodes) if episodes else None,
        "episodes_net_of_transfer": net_ok,
        "per_pair": [{"kalshi_id": k, "snapshots": len(v),
                      "best_edge_max": max((r["best_edge"] for s in v for r in s if r["best_edge"] is not None),
                                           default=None),
                      "n_capacity_gt0": sum(any(r["capacity_usd"] > 0 for r in s) for s in v)}
                     for k, v in sorted(by_pair.items())],
        "series": dict(series),
        "episode_points": in_ep,
    }


def _scoped(rows: list[dict], scope: dict | None, eligible_keys: set) -> tuple[list[dict], dict | None]:
    """Keep rows of in-scope pairs; count pairs by status. scope: {(kalshi_id, pm_id): {"reason", "closed"}},
    reason None = in scope. A pair with no current-prompt adjudication is out of scope."""
    if scope is None:
        return rows, None
    keys = {(r["kalshi_id"], r["pm_id"]) for r in rows}

    def why(k):
        return scope[k]["reason"] if k in scope else f"no prompt-{PROMPT_VERSION} adjudication"

    kept = {k for k in keys if why(k) is None}
    closed = {k for k in kept - eligible_keys if scope[k]["closed"]}
    return [r for r in rows if (r["kalshi_id"], r["pm_id"]) in kept], {
        "pairs": len(kept), "eligible": len(kept & eligible_keys), "closed": len(closed),
        "other": len(kept - eligible_keys - closed),
        "excluded": dict(Counter(why(k) for k in keys - kept)),
    }


def summarize(pairs: list[ExactPair], screened_rows: list[dict], edge_rows: list[dict], episodes: list[dict],
              *, adjudications: dict | None = None, scope: dict | None = None,
              generated_at: datetime | None = None) -> dict:
    """Pure. `adjudications`: {(kalshi_id, pm_id): {"tail_risks", "model", "prompt_version"}} for eligible pairs.
    `scope`: see `_scoped`; None keeps every row (no scope filter)."""
    adj = adjudications or {}
    ek = {(p.kalshi_id, p.pm_id) for p in pairs}
    screened_rows, sc_scope = _scoped(screened_rows, scope, ek)
    edge_rows, ex_scope = _scoped(edge_rows, scope, ek)
    episodes, _ = _scoped(episodes, scope, ek)
    reviewed = sum(p.reviewed for p in pairs)
    us = [e for e in episodes if e["us_executable"]]  # episodes already scoped above
    return {
        "generated_at": generated_at,
        "note": "" if pairs else "There are no eligible pairs at generation time, so there is nothing to score.",
        "pairs": {"eligible": len(pairs), "reviewed": reviewed, "unreviewed": len(pairs) - reviewed},
        "pair_list": [{"kalshi_id": p.kalshi_id, "pm_id": p.pm_id, "confidence": p.confidence,
                       "reviewed": p.reviewed, "close_time": p.close_time, "series": p.kalshi_series,
                       "pm_fee": json.dumps(p.pm_fee_schedule, sort_keys=True) if p.pm_fees_enabled else "none",
                       **adj.get((p.kalshi_id, p.pm_id), {})} for p in sorted(pairs, key=lambda p: p.kalshi_id)],
        "fees": {"version": FEE_VERSION, "kalshi_rate": KALSHI_RATE, "transfer": TRANSFER},
        "screened": {**_screened(screened_rows), "scope": sc_scope},
        "executable": {**_executable(edge_rows, episodes), "scope": ex_scope},
        "jurisdiction": {
            "all_episodes": len(episodes),
            "us_episodes": len(us),
            "reason": ("Polymarket International is the only Polymarket venue scored, and it blocks US trading "
                       f"(US_EXECUTABLE = {US_EXECUTABLE}, docs/venues.md geoblock entry), so no pair has both "
                       "legs executable from the US." if not US_EXECUTABLE["polymarket"] else ""),
        },
    }


# ---------- rendering ----------

INK, INK2, GRID, SURFACE, BLUE, ORANGE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#2a78d6", "#eb6834"


def _f(x, nd=3, pct=False) -> str:
    if x is None:
        return "n/a"
    return f"{100 * x:.1f}%" if pct else f"{x:,.{nd}f}"


def _t(ts) -> str:
    return "n/a" if ts is None else ts.strftime("%Y-%m-%d %H:%M UTC")


def _axes(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _chart_screened(sc: dict, path: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    v = np.array(sc["values"])
    fig, ax = plt.subplots(figsize=(8, 4), facecolor=SURFACE)
    _axes(ax)
    if len(v):
        w = 0.005  # bin edges on a 0.005 grid, so 0 and the 0.01 threshold fall on bin edges
        lo = np.floor(np.percentile(v, 0.5) / w) * w
        hi = np.ceil(max(np.percentile(v, 99.5), MIN_EDGE + 0.01) / w) * w
        n_lo, n_hi = int((v < lo).sum()), int((v > hi).sum())
        ax.hist(np.clip(v, lo, hi), bins=np.arange(lo, hi + w / 2, w), color=BLUE, rwidth=0.85)
        ax.set_yscale("log")
        for x, lab, ha in ((0, "0 ", "right"), (MIN_EDGE, f" {MIN_EDGE} threshold", "left")):
            ax.axvline(x, color=INK2, linewidth=1, linestyle="--")
            ax.text(x, 1.01, lab, transform=ax.get_xaxis_transform(), color=INK2, fontsize=8, ha=ha)
        fig.text(0.01, 0.01, f"End bins pool the tails: {n_lo} rows below {lo:.3f}; {n_hi} rows above {hi:.3f} "
                 f"(max {v.max():.3f}).", color=INK2, fontsize=8)
    ax.set_xlabel(r"net_gap_top (\$ per \$1 contract, after taker fees)", color=INK2, fontsize=9)
    ax.set_ylabel("pair-hour-direction rows (log scale)", color=INK2, fontsize=9)
    ax.set_title("Screened tier: hourly net gap, exact pairs (history, not executable)",
                 color=INK, fontsize=10, loc="left")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return (f"Screened tier. Distribution of `net_gap_top` over all {sc['rows']:,} rows. The x-axis is clipped to the "
            "0.5th-99.5th percentile, rounded out to the 0.005 bin grid (threshold kept in view); rows outside are "
            "pooled into the end bins and counted in the chart's footer line. Log y-axis so the thin positive tail is visible.")


def _chart_executable(ex: dict, path: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if ex["episode_points"]:
        fig, ax = plt.subplots(figsize=(8, 4), facecolor=SURFACE)
        _axes(ax)
        cap, edge = zip(*ex["episode_points"], strict=True)
        ax.scatter(edge, cap, s=18, color=BLUE, edgecolors=SURFACE, linewidths=1)
        ax.set_xlabel(r"best_edge at the top of book (\$ per \$1 contract, after fees)", color=INK2, fontsize=9)
        ax.set_ylabel("capacity_usd (edge >= 0.01)", color=INK2, fontsize=9)
        ax.set_title("Executable tier: capacity vs edge, snapshots inside episodes", color=INK, fontsize=10,
                     loc="left")
        fig.tight_layout()
        caption = (f"Executable tier. Each dot is one snapshot inside one of the {ex['episodes']} episodes: "
                   "edge at the top of book against dollars executable before the edge falls below 0.01.")
    else:
        series = ex["series"]
        n = max(len(series), 1)
        cols = min(3, n)
        rows = -(-n // cols)
        fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 2.2 * rows), sharex=True, sharey=True,
                                 facecolor=SURFACE, squeeze=False)
        for ax in axes.flat:
            ax.set_visible(False)
        for ax, (k, pts) in zip(axes.flat, sorted(series.items()), strict=False):
            ax.set_visible(True)
            _axes(ax)
            t, e = zip(*pts, strict=True)
            ax.plot(t, e, color=BLUE, linewidth=1.2)
            ax.axhline(MIN_EDGE, color=ORANGE, linewidth=1, linestyle="--")
            ax.set_title(k.split("-", 1)[-1], color=INK, fontsize=8, loc="left")
            ax.tick_params(axis="x", labelrotation=30, labelsize=6, labelbottom=True)
        fig.suptitle("Executable tier: best edge per snapshot (dashed = 0.01 threshold)", color=INK, fontsize=10,
                     x=0.01, ha="left")
        fig.supylabel(r"best_edge (\$ per \$1 contract, after fees)", color=INK2, fontsize=8)
        fig.supxlabel("snapshot time (UTC)", color=INK2, fontsize=8)
        fig.tight_layout()
        caption = ("Executable tier. There are 0 opportunity episodes, so there are no episode snapshots to plot "
                   "capacity against. Shown instead: each pair's best edge (better of the two directions, after "
                   "taker fees, top of book) at every aligned snapshot, against the 0.01 threshold. "
                   "Panels are Kalshi tickers without the series prefix; pairs with no snapshots have no panel.")
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return caption


def _tail(p: dict) -> list[str]:
    """Tail risks are stored as text: clauses joined with "; ", or a JSON list. One sub-bullet per clause; a "; "
    is a clause break only before an uppercase letter, so a semicolon inside a clause survives."""
    t = p.get("tail_risks")
    try:
        items = json.loads(t) if t else []
    except ValueError:
        items = [x.strip() for x in re.split(r"; (?=[A-Z])", t) if x.strip()]
    items = items if isinstance(items, list) else [str(items)]
    flags = NEEDS_REVIEW.get(p["kalshi_id"], {})

    def flag(i):
        return f" *(flagged for human review before v1-final: {flags[i]})*" if i in flags else ""

    return ([f"- **{p['kalshi_id']}**" + ("" if items else ": none recorded") + flag(0)]
            + [f"  - {x}{flag(i)}" for i, x in enumerate(items, 1)])


def _scope_line(tier: str, sc: dict | None) -> str:
    if sc is None:
        return f"  - {tier}: no scope filter applied."
    ex = "; ".join(f"{n} ({why})" for why, n in sorted(sc["excluded"].items())) or "none"
    return (f"  - {tier}: {sc['pairs']} pairs in scope ({sc['eligible']} currently eligible, {sc['closed']} closed, "
            f"{sc['other']} open but not currently eligible); excluded pairs with rows in the table: {ex}.")


def render(summary: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    s, sc, ex, j, pc = summary, summary["screened"], summary["executable"], summary["jurisdiction"], summary["pairs"]
    cap_sc = _chart_screened(sc, out_dir / SCREENED_PNG)
    cap_ex = _chart_executable(ex, out_dir / EXEC_PNG)
    tr = s["fees"]["transfer"]
    models = sorted({(p.get("model"), p.get("prompt_version")) for p in s["pair_list"] if p.get("model")})
    llm = [f"{m} (prompt {v})" for m, v in models if m != RULE_MODEL]
    rule = [f"{m} (prompt {v})" for m, v in models if m == RULE_MODEL]
    parts = ([f"by an LLM ({', '.join(llm)})"] if llm else [])
    parts += [f"by a deterministic rule ({', '.join(rule)})"] if rule else []
    adj_txt = ", or, for Fed decision brackets, ".join(parts) if len(parts) == 2 else (parts or ["by an unrecorded method"])[0]
    series = ", ".join(f"`{x}`" for x in sorted({p["series"] for p in s["pair_list"]})) or "none"
    fee_pm = ", ".join(f"`{x}`" for x in sorted({p["pm_fee"] for p in s["pair_list"]})) or "n/a"
    no_books = [p["kalshi_id"] for p in s["pair_list"] if p["kalshi_id"] not in {r["kalshi_id"] for r in ex["per_pair"]}]
    hours = (ex["end"] - ex["start"]).total_seconds() / 3600 if ex["start"] else 0.0
    L = [
        "# Do exact Kalshi-Polymarket matches carry arbitrage? Research note v1",
        "",
        (f"Generated {_t(s['generated_at'])} by `python -m venue_arb.report.note`. Every number below is computed "
        "from the tables named in Method at generation time; the queries are listed at the end. This is v1: the "
        "executable tier covers only the snapshots collected so far, and the note is re-run once about four weeks "
        "of snapshots exist."),
        "",
    ]
    if s["note"]:
        L += [f"**Note:** {s['note']}", ""]
    L += [
        "## Question",
        "",
        ("When Kalshi and Polymarket list the same contract, do their prices leave money on the table after taker "
        "fees and order-book depth? Headline perspective: arbitrage that someone, somewhere could execute (both "
        "legs tradable by some eligible participant; which jurisdictions each venue admits is itself unverified, see "
        "`docs/venues.md`), with a US-only cut alongside."),
        "",
        "**Short answer so far** (each figure is restated with its source under Results):",
        "",
        (f"- Executable tier: {ex['episodes']} opportunity episodes in {hours:.1f} h of snapshots"
         + (" (too short to conclude anything)" if hours < CONCLUSIVE_H else "")
         + f"; the largest top-of-book edge after fees was {_f(ex['best_edge_max'], 4)} per $1 against a "
         f"{MIN_EDGE} threshold."),
        (f"- Screened tier (history, not executable): median hourly net gap {_f(sc['p50'])}; "
         f"{_f(sc['share_ge_001'], pct=True)} of rows reached {MIN_EDGE} (a count biased in both directions: the "
         "1-contract fee ceiling pushes it down, the mid/last Polymarket price pushes it up; see Limits), "
         f"{_f(_share(sc['n_ge_001_early'], sc['n_ge_001']), pct=True)} of those in a pair's first {EARLY_DAYS} days."),
        "",
        "## Data and window",
        "",
        "- Venues: Kalshi and Polymarket International (public read endpoints only; no orders).",
        (f"- Pairs: {pc['eligible']} eligible exact pairs at generation time ({pc['reviewed']} human-reviewed, "
        f"{pc['unreviewed']} not), Kalshi series: {series}."),
        (f"- Screened tier: hourly, {_t(sc['start'])} to {_t(sc['end'])}; {sc['rows']:,} rows "
        f"({sc['pair_hours']:,} pair-hours, up to two directions each) over {sc['pairs']} pairs."),
        f"- Executable tier: order-book snapshots at a 60 s cadence, {_t(ex['start'])} to {_t(ex['end'])} "
        f"({hours:.1f} h); {ex['snapshots']:,} aligned pair-snapshots ({ex['rows']:,} rows, one per direction) "
        f"over {ex['pairs']} pairs."
        + (f" Eligible pairs with no executable rows yet: {', '.join(no_books)}." if no_books else ""),
        (f"- Tier scope: both tiers keep every pair whose latest prompt-{PROMPT_VERSION} adjudication is `exact` and that has no "
         "`wrong`/`unsure` review of that verdict (reviews made against an older prompt's verdict are ignored), "
         "whether or not it is open now (a closed pair's history is still evidence). "
         "Pairs are Kalshi-Polymarket market pairs."),
        _scope_line("Screened", sc["scope"]),
        _scope_line("Executable", ex["scope"]),
        "",
        "| Kalshi ticker | Polymarket market | LLM confidence | Human review | Closes |",
        "|---|---|---|---|---|",
        *[f"| {p['kalshi_id']} | `{p['pm_id'][:10]}...` | {p['confidence']:.2f} | "
          f"{'correct' if p['reviewed'] else 'none'} | {_t(p['close_time'])} |" for p in s["pair_list"]],
        "",
        "## Method",
        "",
        "**What counts as exact.** The two Yes outcomes pay in the same states of the world: same event, same outcome "
        "definition, same decision date and same resolving authority. Trading close times and the wording of the "
        "source pages do not count as differences. Rare edge clauses (for example a cancelled meeting, or an odd-size "
        "rate move rounded up) keep a pair exact but are recorded as tail risks and listed under Results. "
        "**Near** means the Yes outcomes overlap but differ (window, threshold or authority). **Different** means the "
        "Yes outcomes are mutually exclusive or disjoint, including different outcome brackets of the same meeting "
        "(hold vs +25 bp, exactly 25 vs more than 25, +25 vs 50+). Every Fed target change since 1989 has been a "
        "multiple of 25 bp, so \"more than 25 bp\" and \"50 bp or more\" are the same outcome set; an off-grid "
        "move is a tail risk. Adjudication was " + adj_txt + ".",
        "",
        ("**Eligibility.** The latest adjudication at the listings' current versions says `relation = 'exact'`, and "
        f"either confidence >= {MIN_CONFIDENCE} with no review, or a human review labeled `correct`; any `wrong` "
        "or `unsure` review excludes the pair. The adjudication must be at the current prompt version, and only "
        "reviews made of that verdict count. Both listings must be open and seen in the last 2 hours."),
        "",
        "**Two tiers, never combined.**",
        "",
        ("- *Screened* (`venue_arb.screened_v2`): Kalshi's hourly candle close ask (Yes ask, or 1 - Yes bid for No), "
        "taken at the candle's `end_period_ts`, the instant Kalshi documents the close quote for, against the "
        "Polymarket price-history print nearest that instant (within 30 minutes; none, no row) for the opposite "
        "leg, minus one contract's taker fee on each leg: `net_gap_top = 1 - k_price - pm_price - fees`. Hours "
        "where Kalshi's book side was empty give no row. Polymarket history is a traded/mid price, not an ask, so "
        "this tier says where gaps tended to appear, not that they were executable. (`venue_arb.screened`, v1, "
        "paired each Kalshi close with the Polymarket print about an hour earlier and is no longer read.)"),
        ("- *Executable* (`venue_arb.edges`, `venue_arb.opportunities`): our own order-book snapshots. A Kalshi "
        f"snapshot is aligned with the nearest Polymarket snapshot within {ALIGN_S} s. Both ask ladders are walked "
        "together; `best_edge` is the top-of-book edge after fees and `capacity_usd` the dollars spent on both "
        f"legs while the marginal edge stays >= {MIN_EDGE}."),
        "",
        (f"**Threshold (an episode).** Net edge >= ${MIN_EDGE} per $1 contract, >= $100 executable, in >= 2 "
        f"consecutive snapshots (a gap over {MAX_GAP_S} s breaks the run)."),
        "",
        (f"**Fees** (entries dated {s['fees']['version']}, sources in `docs/venues.md`). Kalshi taker: "
        f"`{s['fees']['kalshi_rate']} x C x P x (1-P)` times the series' `fee_multiplier` (read from the API), "
        "rounded up to the cent; the 0.07 coefficient is secondhand (see Limits). Polymarket taker: "
        f"`C x rate x (P(1-P))^exponent` read from each market's own `feeSchedule` (here: {fee_pm}), no rounding."),
        "",
        (f"**Transfer cost** (an assumption, not a venue number): ${tr['fixed_usd']:.0f} fixed + "
        f"{tr['conversion_bps']:.0f} bps per round trip of capital between venues. It is reported as a "
        "sensitivity count, not subtracted from the headline edge."),
        "",
        "## Results",
        "",
        "### Screened tier (history; not arbitrage)",
        "",
        f"- Rows (pair x hour x direction): {sc['rows']:,}.",
        f"- `net_gap_top` p50 {_f(sc['p50'])}, p90 {_f(sc['p90'])}, max {_f(sc['max'])}.",
        (f"- Rows with `net_gap_top > 0`: {sc['n_gt0']:,} ({_f(sc['share_gt0'], pct=True)}); "
        f">= {MIN_EDGE}: {sc['n_ge_001']:,} ({_f(sc['share_ge_001'], pct=True)}). Both counts are biased in both "
        "directions: the 1-contract Kalshi fee is ceiled to the cent, which can understate a net gap by up to "
        "~$0.01 (biases the counts down), and Polymarket's history is a mid/last price rather than an ask "
        "(biases them up). Which effect dominates is not known; see Limits."),
        (f"- Of the rows >= {MIN_EDGE}, {sc['n_ge_001_early']:,} ({_f(_share(sc['n_ge_001_early'], sc['n_ge_001']), pct=True)}) "
        f"fall in their pair's first {EARLY_DAYS} days of screened history, which hold "
        f"{_f(_share(sc['n_early'], sc['rows']), pct=True)} of all rows."),
        "",
        "| Kalshi ticker | First hour | Rows | p50 net | Max net | Rows >= 0.01 |",
        "|---|---|---|---|---|---|",
        *[f"| {r['kalshi_id']} | {_t(r['first'])} | {r['rows']:,} | {_f(r['p50'])} | {_f(r['max'])} | "
          f"{r['n_ge_001']:,} |" for r in sc["per_pair"]],
        "",
        f"![Screened net gap distribution]({SCREENED_PNG})",
        "",
        f"*{cap_sc}*",
        "",
        "### Executable tier (order books)",
        "",
        (f"- Aligned pair-snapshots: {ex['snapshots']:,}; with capacity > 0 in either direction "
        f"(marginal edge >= {MIN_EDGE} at the top): {ex['n_capacity_gt0']:,} "
        f"({_f(ex['share_capacity_gt0'], pct=True)})."),
        (f"- `best_edge` (per row, both directions) p50 {_f(ex['best_edge_p50'], 4)}, "
        f"max {_f(ex['best_edge_max'], 4)}."),
        (f"- Opportunity episodes: {ex['episodes']}. Duration median {_f(ex['duration_min_median'], 1)} min, "
         f"max {_f(ex['duration_min_max'], 1)} min; median capacity ${_f(ex['capacity_usd_median'], 0)}, max "
         f"${_f(ex['capacity_usd_max'], 0)}; median annualized edge {_f(ex['annualized_median'], pct=True)}."
         if ex["episodes"] else "- Opportunity episodes: 0, so there are no durations, capacities or annualized "
         "edges to report."),
        (f"- Episodes still positive after the transfer assumption: {ex['episodes_net_of_transfer']} of "
        f"{ex['episodes']} (approximate and optimistic: it credits each episode's maximum edge on all of its median "
        "capacity, against `transfer_cost_usd`)."),
        "",
        "| Kalshi ticker | Snapshots | Max best_edge | Snapshots with capacity > 0 |",
        "|---|---|---|---|",
        *[f"| {r['kalshi_id']} | {r['snapshots']:,} | {_f(r['best_edge_max'], 4)} | {r['n_capacity_gt0']:,} |"
          for r in ex["per_pair"]],
        "",
        f"![Executable tier]({EXEC_PNG})",
        "",
        f"*{cap_ex}*",
        "",
        "### Jurisdiction cut (US)",
        "",
        f"- Episodes executable within the US: {j['us_episodes']} of {j['all_episodes']}. {j['reason']}",
        "",
        "### Contract-term differences (tail risk)",
        "",
        ("Clauses recorded as rare edge cases that keep each eligible pair exact (latest adjudication at current "
        "versions): LLM-written for LLM-judged pairs, a fixed text for rule-judged Fed pairs. Quoted verbatim, not "
        "human-reviewed." + (
            f" The flags are the builder's own reading of the clauses, dated {REVIEW_FLAGS_DATE}, not a human review "
            "sitting: a pair or clause that reads like a real difference or is muddled is flagged for review before "
            "v1-final; an unflagged clause has not been checked either."
            if any(p["kalshi_id"] in NEEDS_REVIEW for p in s["pair_list"]) else "")),
        "",
        *[line for p in s["pair_list"] for line in _tail(p)],
        "",
        "## Limits",
        "",
        ("- 60 s sampling misses gaps that open and close within a minute; the executable tier can only "
        "undercount those."),
        (f"- The executable window is {hours:.1f} hours long. Zero or few episodes over hours says little about "
        "weeks; v1-final re-runs this after about four weeks of snapshots."),
        ("- Kalshi's 0.07 taker coefficient is secondhand and unverified (the fee-schedule PDF could not be fetched; "
        "`docs/venues.md`). Polymarket's fee rounding rule is unverified. Either could move edges by up to about "
        "a cent per contract at mid prices."),
        ("- The screened tier charges Kalshi's fee on one contract, ceiled to the cent (at 50c: 0.07 x 0.25 = "
         "$0.0175, charged as $0.02), so screened net gaps can be understated by up to ~$0.01 per contract, the "
         "same size as the 0.01 threshold. The executable tier rounds per order-book slice, where the cent is "
         "spread over the slice's contracts."),
        ("- Polymarket price history is a traded/mid price, not an ask, so the screened tier overstates what was "
        "takeable; screened hours are never called arbitrage."),
        ("- Annualized figures use days to the later of the two trading close times, not to payout. Polymarket "
        "settles through UMA's oracle, which can pay out later than trading closes, so annualized edges are "
        "slightly overstated."),
        (f"- Few exact pairs ({pc['eligible']}), from Kalshi series {series}; nothing here generalizes to "
        "other categories."),
        (f"- Matches were adjudicated {adj_txt}; no human review sitting has happened yet: "
        f"{pc['reviewed']} of {pc['eligible']} eligible pairs reviewed, {pc['unreviewed']} unreviewed."),
        ("- No execution latency, fill risk, partial fills, or capital lock-up costs beyond the annualized figure "
        "are modeled. The transfer cost is an assumption, reported as a sensitivity only."),
        "",
        "## Next",
        "",
        ("Plan 3: add Polymarket US (makes the US cut meaningful), crypto near-matches priced with the "
        "option-implied distribution, and perp-vs-spot basis."),
        "",
        "## Queries",
        "",
        "```sql",
        f"-- screened tier\n{SCREENED_SQL}",
        f"-- executable tier: edges\n{EDGES_SQL}",
        f"-- executable tier: episodes (Postgres)\n{OPPS_SQL}",
        "```",
        "",
        ("Eligible pairs: `venue_arb/flows/exact_sql.py` (`ELIGIBLE_SQL`) filtered by `venue_arb/exact.py`; tail "
        "risks: `ADJ_SQL`; tier scope: `SCOPE_SQL`, both in `venue_arb/report/note.py`. Rows from the three "
        "tier queries above are kept only for pairs in tier scope (`_scoped`)."),
        "",
    ]
    path = out_dir / NOTE
    path.write_text("\n".join(L))
    return path


def main(out_dir: Path = Path("reports")) -> Path:
    from qa import db

    from venue_arb.exact import eligible
    from venue_arb.flows.exact_sql import load_eligible

    def rows(cur) -> list[dict]:
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def utc(r: dict) -> dict:
        return {k: v.replace(tzinfo=UTC) if isinstance(v, datetime) and not v.tzinfo else v for k, v in r.items()}

    with db.pg() as conn:
        pairs = eligible(load_eligible(conn))
        episodes = rows(conn.execute(OPPS_SQL))
        adj = {(k, p): {"tail_risks": t, "prompt_version": v, "model": m} for k, p, t, v, m in
               conn.execute(ADJ_SQL, {"k": [p.kalshi_id for p in pairs]}).fetchall()}
        scope = {(k, p): {"reason": None if rel == "exact" and not bad else
                          ("wrong/unsure review" if bad else f"latest {PROMPT_VERSION} relation '{rel}'"), "closed": closed}
                 for k, p, rel, bad, closed in conn.execute(SCOPE_SQL, {"pv": PROMPT_VERSION}).fetchall()}
    ch = db.ch()
    scr = [utc(dict(zip(("ts", "kalshi_id", "pm_id", "direction", "net_gap_top"), r, strict=True)))
           for r in ch.query(SCREENED_SQL).result_rows]
    edges = [utc(dict(zip(("ts", "kalshi_id", "pm_id", "direction", "best_edge", "capacity_usd"), r, strict=True)))
             for r in ch.query(EDGES_SQL).result_rows]
    s = summarize(pairs, scr, edges, episodes, adjudications=adj, scope=scope, generated_at=datetime.now(UTC))
    path = render(s, out_dir)
    print(f"wrote {path}: pairs={s['pairs']} screened_rows={s['screened']['rows']} "
          f"snapshots={s['executable']['snapshots']} episodes={s['executable']['episodes']}")
    return path


if __name__ == "__main__":
    main()
