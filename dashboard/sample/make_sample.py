"""Synthetic dashboard sample: live.json + two history hours, built through bundle.* and publish.history_table, and
live-stock.json (the same bundle with the stock-perp tier on: four invented stock-perp rows and a research block).

    uv run python dashboard/sample/make_sample.py

Tickers have real shapes; every price, size and time is invented (seeded, fixed `now`), so the repo carries no real
quotes. Uses config.DASHBOARD_SHOW_BOOKS as it is: while False, live.json has books null and the history Parquet has
null quotes. Rerun after flipping the flag. live-stock.json forces config.DASHBOARD_SHOW_STOCK_PERPS True for its
own build only; live.json stays flag-off. Output is deterministic (sorted-key JSON, zstd Parquet).
"""
import hashlib
import json
import random
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # run as a script, venue_arb is not installed

import pyarrow.parquet as pq

from venue_arb import config
from venue_arb.bundle import (
    Annotation,
    Basis,
    Episode,
    build_live,
    exact_pair_rows,
    ladder_pair_rows,
    stock_perp_rows,
)
from venue_arb.exact import ExactPair
from venue_arb.fees import transfer_cost
from venue_arb.matching.crypto_rule import Strike
from venue_arb.models import Level
from venue_arb.publish import history_table
from venue_arb.report import note, research
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel

NOW = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)
SEED = 20261020
HOURS = (datetime(2026, 10, 20, 14, tzinfo=UTC), datetime(2026, 10, 20, 15, tzinfo=UTC))
INSTANT = datetime(2026, 10, 20, 16, tzinfo=UTC)  # the BTC ladder settles in 30 min
BUMP_FROM = datetime(2026, 10, 20, 15, 2, tzinfo=UTC)  # the executable exact row's episode starts here, still open


def pm_id(name: str) -> str:
    return "0x" + hashlib.sha256(name.encode()).hexdigest()


def levels(*xs) -> list[Level]:
    return [Level(side, i, price, size) for side, i, price, size in xs]  # i = level index within its side


def listing(venue, market_id, category, title, source, raw):
    return {"venue": venue, "market_id": market_id, "category": category, "title": title, "resolution_source": source,
            "raw": raw}


def kraw(series, event):
    return {"event": {"series_ticker": series, "event_ticker": event}}


def praw(slug):
    return {"event": {"slug": slug}}


FED_FEE = {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}
BTC_FEE = {"fee_type": "quadratic", "fee_multiplier": 1}
PM_SCHED = {"rate": 0.05, "exponent": 1, "takerOnly": True, "rebateRate": 0.25}
BTC_PM_SCHED = {"rate": 0.07, "exponent": 1, "takerOnly": True, "rebateRate": 0.2}

K_FED, P_FED = "KXFED-26DEC-H0", pm_id("fed-hike-dec")
K_CPI, P_CPI = "KXCPI-26NOV-T0.3", pm_id("cpi-nov-over-0.3")
K_NYC, P_NYC = "KXHIGHNY-26OCT21-T70", pm_id("nyc-high-above-70")
P_B86, P_B88 = pm_id("btc-above-86000"), pm_id("btc-above-88200")
K_B86, K_B88 = "KXBTCD-26OCT2011-T85999.99", "KXBTCD-26OCT2011-T87999.99"

PAIRS = [  # kalshi id, pm id, yes_maps_to, close, series, fee schedule, fees enabled
    ExactPair(K_FED, P_FED, "tok-fed", "yes", datetime(2026, 12, 9, 19, tzinfo=UTC), "KXFED", PM_SCHED, False, .95, True),
    ExactPair(K_CPI, P_CPI, "tok-cpi", "yes", datetime(2026, 11, 13, 13, 30, tzinfo=UTC), "KXCPI", PM_SCHED, False,
              .93, True),
    ExactPair(K_NYC, P_NYC, "tok-nyc", "yes", datetime(2026, 10, 21, 23, tzinfo=UTC), "KXHIGHNY", PM_SCHED, False,
              .91, True),
]
LISTINGS = [
    listing("kalshi", K_FED, "fed_macro", "Fed hikes in December 2026?", "FOMC statement", kraw("KXFED", "KXFED-26DEC")),
    listing("polymarket", P_FED, "fed_macro", "Fed rate hike in December?", "https://www.federalreserve.gov",
            praw("fed-decision-in-december")),
    listing("kalshi", K_CPI, "fed_macro", "CPI above 0.3% in October?", "BLS CPI release", kraw("KXCPI", "KXCPI-26NOV")),
    listing("polymarket", P_CPI, "fed_macro", "October CPI MoM over 0.3%?", "https://www.bls.gov",
            praw("october-cpi-over-0-3")),
    listing("kalshi", K_NYC, "weather", "NYC high above 70F on Oct 21?", "NWS Central Park", kraw("KXHIGHNY", "KXHIGHNY-26OCT21")),
    listing("polymarket", P_NYC, "weather", "Will NYC hit 70F on October 21?", "NWS Central Park",
            praw("nyc-high-above-70-oct-21")),
    listing("kalshi", K_B86, "crypto_price", "Bitcoin price on Oct 20, 2026?", "CF Benchmarks BRTI",
            kraw("KXBTCD", "KXBTCD-26OCT2011")),
    listing("kalshi", K_B88, "crypto_price", "Bitcoin price on Oct 20, 2026?", "CF Benchmarks BRTI",
            kraw("KXBTCD", "KXBTCD-26OCT2011")),
    listing("polymarket", P_B86, "crypto_price", "Bitcoin above 86,000 on October 20, 12PM ET?",
            "Binance BTC/USDT 1m close", praw("bitcoin-above-86000-on-october-20-12pm-et")),
    listing("polymarket", P_B88, "crypto_price", "Bitcoin above 88,200 on October 20, 12PM ET?",
            "Binance BTC/USDT 1m close", praw("bitcoin-above-88200-on-october-20-12pm-et")),
]
TITLES = {(r["venue"], r["market_id"]): r for r in LISTINGS}
fresh, stale = NOW - timedelta(seconds=40), NOW - timedelta(minutes=12)
BOOKS = {  # (venue, id) -> (snapshot ts, levels); invented. FED: edge < 0 and no Kalshi-No/PM-Yes asks.
    ("kalshi", K_FED): (fresh, levels(("yes", 0, .41, 120), ("yes", 1, .42, 340))),
    ("polymarket", P_FED): (fresh, levels(("no", 0, .58, 400), ("no", 1, .59, 210))),
    # CPI: the executable exact row (Yes 0.38 + No 0.55 leaves ~5¢ after fees); the other direction loses
    ("kalshi", K_CPI): (fresh, levels(("yes", 0, .38, 600), ("yes", 1, .39, 900), ("no", 0, .64, 400))),
    ("polymarket", P_CPI): (fresh, levels(("no", 0, .55, 500), ("no", 1, .56, 700), ("yes", 0, .45, 300))),
    ("kalshi", K_NYC): (stale, levels(("yes", 0, .30, 200), ("no", 0, .72, 200))),  # Kalshi leg 12 min old: stale
    ("polymarket", P_NYC): (fresh, levels(("no", 0, .69, 300), ("yes", 0, .31, 300))),
    ("kalshi", K_B86): (fresh, levels(("no", 0, .08, 400), ("yes", 0, .93, 500), ("yes", 1, .94, 800))),
    ("kalshi", K_B88): (fresh, levels(("no", 0, .07, 250), ("yes", 0, .96, 300))),
    ("polymarket", P_B86): (fresh, levels(("no", 0, .09, 350), ("yes", 0, .92, 300))),
    ("polymarket", P_B88): (fresh, levels(("no", 0, .04, 600), ("no", 1, .05, 300), ("yes", 0, .95, 200))),
}
LADDER = {"kalshi": [Strike("kalshi", K_B86, None, "BTC", INSTANT, "gt", 85999.99),
                     Strike("kalshi", K_B88, None, "BTC", INSTANT, "gt", 87999.99)],
          "polymarket": [Strike("polymarket", P_B86, "tok-86000", "BTC", INSTANT, "gt", 86000),
                         Strike("polymarket", P_B88, "tok-88200", "BTC", INSTANT, "gt", 88200)]}
EXEC_KEY = f"{K_CPI}~{P_CPI}~k_yes_pm_no"  # the exact row with the episode
BASIS = {"BTC": Basis(up=182, down=152, window_days=30, asof=NOW)}


def live_rows(sparks):
    ex = [r for p in PAIRS for r in exact_pair_rows(p, TITLES, BOOKS[("kalshi", p.kalshi_id)],
                                                    BOOKS[("polymarket", p.pm_id)], FED_FEE, sparks, NOW)]
    lad = ladder_pair_rows("BTC", INSTANT, LADDER, BOOKS, 182, 152, lambda _: BTC_FEE, lambda _: (BTC_PM_SCHED, True),
                           TITLES, sparks, NOW)
    return ex, lad


def make_sparks(rng, rows):
    out = {}
    for r in rows:
        if r.net_edge_cents is None:
            continue
        pts = [round(r.net_edge_cents + rng.gauss(0, .3), 2) for _ in range(24)]
        if r.key == EXEC_KEY:
            pts = [round(0.2 + rng.gauss(0, .1), 2) for _ in range(22)] + [1.4, round(r.net_edge_cents, 2)]
        pts[rng.randrange(0, 6)] = None  # a few hours with no scored row
        out[r.key] = pts
    return out


def history_rows(rng, hour, rows):
    """Per-minute (edge_rows, ladder_rows, books) for the sample's keys: around today's net edge, the executable exact
    row at ~0.2¢ until BUMP_FROM then at its live edge (an open episode), rows with no live edge all null."""
    edge_rows, ladder_rows, books = [], [], {}
    for m in range(60):
        ts = hour + timedelta(minutes=m)
        snap = {v: list(BOOKS[v][1]) for v in BOOKS}  # a fresh copy of every book this minute
        for r in rows:
            k, p = r.legs[0].market_id, r.legs[1].market_id
            if r.net_edge_cents is None:
                best = cap = None
            else:
                up = r.key == EXEC_KEY and ts >= BUMP_FROM
                c = r.net_edge_cents * (.7 + .3 * rng.random()) if up else (
                    0.2 + rng.gauss(0, .1) if r.key == EXEC_KEY else r.net_edge_cents + rng.gauss(0, .2))
                c = round(c, 2)  # gauss goes through libm: round so the Parquet bytes match across platforms
                best = round(c / 100, 4)
                cap = round(r.capacity_usd * (.9 + .2 * rng.random()) if up or r.key != EXEC_KEY else 40.0, 2)
                if r.kind != "ladder":  # steer the first Kalshi ask so the derived fee stays the pair's fee
                    ks, ps = r.legs[0].side, r.legs[1].side
                    pa = next(lv for lv in snap[("polymarket", p)] if lv.side == ps).price
                    kl = snap[("kalshi", k)]
                    i = next(i for i, lv in enumerate(kl) if lv.side == ks)
                    kl[i] = replace(kl[i], price=round(1 - pa - (c + r.fees_cents) / 100, 4))
            if r.kind == "exact":
                edge_rows.append((ts, k, p, r.direction, best, cap))
            else:
                ladder_rows.append((ts, k, p, r.direction, "arb" if r.kind == "ladder" else "near_exact", best, cap))
        for v, lv in snap.items():
            books.setdefault(v, {})[ts] = sorted(lv, key=lambda x: (x.side, x.level))
    return edge_rows, ladder_rows, books


def research_section():
    """Invented ResearchSummary through note.summarize + research.from_summary: 3 pairs x 48 hourly edge rows (the CPI
    pair carries a positive run, hence one episode), 300 screened values, 6 counted ladder hours (1 violation)."""
    rng = random.Random(SEED + 1)  # its own stream: the history Parquet bytes do not depend on the research
    end = NOW.replace(minute=0)
    hrs = [end - timedelta(hours=h) for h in range(47, -1, -1)]
    run = hrs[-6:]  # CPI's positive run: the last six hours
    edge_rows = []
    for p in PAIRS:
        for h in hrs:
            up = p.kalshi_id == K_CPI and h in run
            e = round(0.02 + rng.random() * 0.01, 4) if up else round(-0.04 + rng.gauss(0, .01), 4)
            edge_rows.append({"ts": h, "kalshi_id": p.kalshi_id, "pm_id": p.pm_id, "direction": "k_yes_pm_no",
                              "best_edge": e, "capacity_usd": round(300 + rng.random() * 200, 2) if up else 0.0})
    cpi = [r for r in edge_rows if r["kalshi_id"] == K_CPI and r["best_edge"] >= .01]
    episodes = [{"kalshi_id": K_CPI, "pm_id": P_CPI, "direction": "k_yes_pm_no", "start_ts": run[0],
                 "end_ts": run[-1], "snapshots": len(cpi), "max_edge": max(r["best_edge"] for r in cpi),
                 "median_capacity_usd": 410.0, "annualized": 0.35, "us_executable": False,
                 "transfer_cost_usd": round(transfer_cost(410.0), 2)}]
    screened = []
    for i in range(300):  # centred below 0, a few at or above the 1¢ threshold
        p = PAIRS[i % 3]
        net = round(0.012 + i / 1e4, 4) if i in (3, 40, 91, 140) else min(round(rng.gauss(-0.035, 0.03), 4), 0.009)
        screened.append({"ts": end - timedelta(hours=i // 3), "kalshi_id": p.kalshi_id, "pm_id": p.pm_id,
                         "direction": "k_yes_pm_no", "net_gap_top": net})
    lh = [end - timedelta(hours=h) for h in range(6, 0, -1)]
    admissible = [{"hour": h, "checks": 280 + rng.randrange(60)} for h in lh]
    ladder_hours = [{"hour": h, "scored": a["checks"], "pos": int(h == lh[2])} for h, a in zip(lh, admissible)]
    ladder_rows = [{"asset": "BTC", "instant": INSTANT, "k_id": K_B86, "pm_id": P_B86, "direction": d, "kind": "arb",
                    "a": 85999.99, "b": 86000.0, "basis_up": 182.0, "basis_down": 152.0,
                    "snapshots": sum(x["scored"] for x in ladder_hours), "n_capacity_gt0": int(d == "k_yes_pm_no"),
                    "best_edge_max": .014 if d == "k_yes_pm_no" else -.02, "first_ts": lh[0], "last_ts": lh[-1]}
                   for d in ("k_yes_pm_no", "pm_yes_k_no")]
    # one basis per sample: bounds are the nearest-rank 99.9th pct rounded up to $1, i.e. ceil(max) and ceil(-min) at
    # 48 rows, so pin the extremes to land on BASIS (BTC up 182 / down 152)
    diffs = [round(max(-100, min(100, rng.gauss(12, 40))), 2) for _ in range(46)] + [BASIS["BTC"].up - .5,
                                                                                     -BASIS["BTC"].down + .5]
    basis_rows = [("BTC", d, 87000.0) for d in diffs]
    s = note.summarize(PAIRS, screened, edge_rows, episodes, generated_at=NOW, ladder_rows=ladder_rows,
                       basis_rows=basis_rows, admissible=admissible, ladder_hours=ladder_hours)
    return research.from_summary(s, review={"precision": 1.0, "labels": 31, "asof": NOW - timedelta(days=1)},
                                 computed_at=NOW)


def stock_inputs():
    """Four invented stock perps through stock_perp_rows (oracle mode): NVDA oracle basis (mark 213.4 vs oracle 212.8),
    TSLA inside perp costs (book on the mark), SKHX oracle internal (Korean exchange closed), MSFT stale (snapshot
    12 min old)."""
    def book(mid, step, size):
        return [PerpLevel(side, i, round(mid + sign * (step / 2 + i * step), 2), size)
                for side, sign in (("bid", -1), ("ask", 1)) for i in range(3)]
    snap = NOW - timedelta(seconds=40)
    nvda = PerpCtx("xyz:NVDA", 213.4, 212.8, 213.4, 0.00001, 0.0005, 1e6, 5e6, True, 10)
    tsla = PerpCtx("xyz:TSLA", 250.4, 250.38, 250.4, 0.00001, 0.0001, 1e6, 4e6, True, 10)
    skhx = PerpCtx("xyz:SKHX", 180000.0, 179800.0, 180000.0, 0.00001, 0.0002, 5e5, 1e6, False, 5)
    msft = PerpCtx("xyz:MSFT", 420.0, 420.0, 420.0, 0.00001, 0.0001, 1e6, 3e6, True, 10)
    return [
        (nvda, Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA"), (snap, book(213.4, 0.2, 100.0))),
        (tsla, Label("xyz:TSLA", "schwab", "TSLA", "NASDAQ", "USD", "Tesla"), (snap, book(250.4, 0.1, 100.0))),
        (skhx, Label("xyz:SKHX", "none", None, "KRX", "KRW", "SK hynix"), (snap, book(180000.0, 100.0, 20.0))),
        (msft, Label("xyz:MSFT", "schwab", "MSFT", "NASDAQ", "USD", "Microsoft"),  # collector behind: the stale row
         (NOW - timedelta(minutes=12), book(420.0, 0.2, 100.0)))]


def stock_block():
    """Invented stock_perp_inputs -> research.stock_perp_block: 72 hourly maxima, three names, four weekends each."""
    rng = random.Random(SEED + 2)  # its own stream, like the research section's
    end = NOW.replace(minute=0)
    hours = [end - timedelta(hours=h) for h in range(71, -1, -1)]
    hourly = [(h, round(max(-5.0, rng.gauss(4, 4)) + (9 if i in (50, 51, 52) else 0), 2)) for i, h in enumerate(hours)]
    weekend = []
    for sym, base in (("xyz:NVDA", 215.0), ("xyz:TSLA", 250.0), ("xyz:AAPL", 180.0)):
        for w in range(4):
            fri = date(2026, 10, 16) - timedelta(days=7 * (3 - w))  # four Fridays ending before NOW (a Tuesday)
            move = round(base * rng.choice((-1, 1)) * (0.004 + 0.002 * rng.random()), 2)  # Monday's open vs Friday
            weekend.append({"symbol": sym, "friday": fri, "p_fri": base, "p_sat": round(base + move * 0.5, 2),
                            "p_sun": round(base + move * (0.4 + 0.8 * rng.random()), 2), "p_open_pre": base + move,
                            "p_open_reg": round(base + move, 2), "ref_kind": "oracle_us"})
    return research.stock_perp_block({
        "hourly": hourly, "names": 3, "snapshots": 4320, "window_start": hours[0], "episodes": 2, "share_hours": 0.12,
        "by_name": [{"symbol": "xyz:NVDA", "name": "NVIDIA", "max_net_bps": 13.0, "hours_ge_10": 3},
                    {"symbol": "xyz:TSLA", "name": "Tesla", "max_net_bps": 7.5, "hours_ge_10": 0},
                    {"symbol": "xyz:AAPL", "name": "Apple", "max_net_bps": 4.0, "hours_ge_10": 0}],
        "sensitivity": {"episodes_base": 2, "episodes_1h": 4, "episodes_7d": 0, "episodes_with_borrow": 2,
                        "window_days": 14},
        "weekend_rows": weekend}, NOW)


def stock_sparks(rng, rows):  # hourly net in ¢ of net (bps / 100), like the pair rows' sparks
    out = {}
    for r in rows:
        if r.net_edge_cents is not None:
            pts = [round(r.net_edge_cents + rng.gauss(0, abs(r.net_edge_cents) * .15 + .02), 3) for _ in range(24)]
            pts[rng.randrange(0, 6)] = None
            out[r.key] = pts
    return out


def make_bundle(rng, stock: bool = False):
    sparks = make_sparks(rng, [r for rows in live_rows({}) for r in rows])
    ex, lad = live_rows(sparks)
    top = next(r for r in ex if r.key == EXEC_KEY)
    cap = top.capacity_usd
    stock_rows = research_s = None
    if stock:
        perps = stock_inputs()
        bare = stock_perp_rows(perps, {}, NOW)
        stock_rows = stock_perp_rows(perps, stock_sparks(random.Random(SEED + 3), bare), NOW)
    research_s = research_section()
    if stock:
        research_s = research_s.model_copy(update={"stock_perps": stock_block()})
    else:  # the publisher drops the stock-perp prose while the tier is off; so does the sample
        research_s = research.without_stock_method(research_s)
    return build_live(
        now=NOW, exact_rows=ex, ladder_rows=lad, basis=BASIS,
        episodes=[Episode(key=EXEC_KEY, start=BUMP_FROM, end=None, max_edge_cents=top.net_edge_cents,
                          median_capacity_usd=cap, transfer_cost_usd=round(transfer_cost(cap), 2))],
        annotations=[
            Annotation(kind="settlement", at=INSTANT, until=None, key=None, label="BTC ladder settles 16:00 UTC"),
            Annotation(kind="listing", at=datetime(2026, 10, 20, 14, 20, tzinfo=UTC), until=None, key=None,
                       label="New listing: KXCPI-26NOV-T0.3 matched to its Polymarket twin"),
            Annotation(kind="basis_change", at=datetime(2026, 10, 20, 13, tzinfo=UTC), until=None, key=None,
                       label="BTC basis bound 178 to 182 (99.9th pct, 30 d)"),
            Annotation(kind="collector_gap", at=datetime(2026, 10, 20, 14, 41, tzinfo=UTC),
                       until=datetime(2026, 10, 20, 14, 47, tzinfo=UTC), key=None, label="Collector gap: 6 min"),
            Annotation(kind="threshold", at=BUMP_FROM, until=None, key=EXEC_KEY, label="Net edge crossed 1¢")],
        kpi_inputs={"checks_window": [HOURS[0], HOURS[1]], "admissible_checks": 1200, "violations": 0,
                    "screened_share_ge_1c": 0.004, "screened_hours": 3100, "stock_perps": {"episodes_7d": 2}},
        attribution=["Order books: Kalshi public market-data API (kalshi.com)",
                     "Order books: Polymarket public CLOB API (polymarket.com)"],
        caveats=["Synthetic sample: every price, size and time is invented."], collector_last=NOW - timedelta(seconds=40),
        research=research_s, stock_rows=stock_rows)


def write_live(out: Path, name: str, bundle) -> None:
    (out / name).write_text(
        json.dumps(json.loads(bundle.model_dump_json()), sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def build(out: Path) -> None:
    rng = random.Random(SEED)
    bundle = make_bundle(rng)
    sides = {(p.kalshi_id, p.pm_id): p.yes_maps_to for p in PAIRS}
    (out / "history").mkdir(parents=True, exist_ok=True)
    write_live(out, "live.json", bundle)
    for hour in HOURS:
        tbl = history_table(*history_rows(rng, hour, bundle.pairs), sides)
        pq.write_table(tbl, out / "history" / f"pairs-{hour:%Y%m%d%H}.parquet", compression="zstd")


def build_stock(out: Path) -> None:
    """live-stock.json: the stock-perp tier on. The config flag is forced for this build only."""
    out.mkdir(parents=True, exist_ok=True)
    with mock.patch.object(config, "DASHBOARD_SHOW_STOCK_PERPS", True):
        write_live(out, "live-stock.json", make_bundle(random.Random(SEED), stock=True))


def main() -> None:
    build(ROOT / "dashboard" / "sample")
    build_stock(ROOT / "dashboard" / "sample")


if __name__ == "__main__":
    main()
