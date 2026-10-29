"""One-off history load for the stock-perp tier, run once at deploy (and safe to re-run):

    secrets uv run python -m venue_arb.flows.stock_perp_backfill [SYMBOL ...]

With symbols, only those labels are loaded (a few new names beside the running collector; the whole set exhausts the
per-IP budget).

Hourly candles and funding of every labelled perp from the last 60 days into stock_perp_candles (a re-run rewrites
the same hours: ReplacingMergeTree), and the daily stock opens of every Schwab-labelled perp into stock_perp_refs as
`schwab_daily` rows stamped at 09:30 New York of the session day (the weekend tier's regular-session open); a re-run
skips the (symbol, ts) rows already there, as that table is a plain MergeTree. Needs the four SCHWAB_* variables for the stock opens; without them only the
candles are loaded.
"""
import os
import sys
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from venue_arb.sources import hyperliquid, schwab

DAYS = 60
CANDLES = "venue_arb.stock_perp_candles"
REFS = "venue_arb.stock_perp_refs"
CANDLE_COLS = ["symbol", "hour", "open", "high", "low", "close", "volume", "funding_h"]
REF_COLS = ["ts", "symbol", "ref_kind", "ref_symbol", "bid", "ask", "bid_size", "ask_size", "last", "quote_time"]
OPEN_ET = time(9, 30, tzinfo=ZoneInfo("America/New_York"))  # the regular session's open; 13:30 UTC in EDT, 14:30 in EST


def funding_by_hour(symbol: str, start: datetime, end: datetime, fetch=hyperliquid.fetch_funding) -> dict[datetime, float]:
    """The venue returns a bounded page from `start`; page on from the last hour seen until a page adds nothing."""
    out: dict[datetime, float] = {}
    while start < end:
        page = [r for r in fetch(symbol, start) if r["hour"] >= start]
        if not page:
            break
        out.update({r["hour"]: r["rate"] for r in page})
        start = max(out) + timedelta(hours=1)
    return out


def candle_rows(symbol: str, candles: list[dict], funding: dict[datetime, float]) -> list[list]:
    return [[symbol, c["hour"], c["open"], c["high"], c["low"], c["close"], c["volume"], funding.get(c["hour"])]
            for c in candles]


def schwab_rows(symbol: str, ref_symbol: str, days: list[dict], existing: set[datetime]) -> list[list]:
    rows = []
    for d in days:
        ts = datetime.combine(d["day"], OPEN_ET).astimezone(UTC)
        if ts not in existing:
            rows.append([ts, symbol, "schwab_daily", ref_symbol, d["open"], d["open"], 0.0, 0.0, d["close"], ts])
    return rows


def _schwab_client(env=os.environ) -> schwab.Client | None:
    names = ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN")
    return schwab.Client(*(env[n] for n in names)) if all(env.get(n) for n in names) else None


def main(now: datetime | None = None, only: list[str] | None = None) -> int:
    # lazy, like the other entry points: tests import this module without the platform stack
    from qa import db

    now = now or datetime.now(UTC)
    start = now - timedelta(days=DAYS)
    labels, ch, client = hyperliquid.load_labels(), db.ch(), _schwab_client()
    if only:
        unknown = sorted(set(only) - labels.keys())
        if unknown:
            print(f"not in labels: {', '.join(unknown)}", file=sys.stderr)
            return 2
        labels = {s: labels[s] for s in only}
    failed = []
    for symbol, label in labels.items():
        try:
            candles = hyperliquid.fetch_candles(symbol, start, now)
            rows = candle_rows(symbol, candles, funding_by_hour(symbol, start, now))
            if rows:
                ch.insert(CANDLES, rows, column_names=CANDLE_COLS)
            print(f"{symbol}: {len(rows)} candle hours")
            if label.ref_kind != "schwab" or client is None:
                continue
            existing = {r[0] for r in ch.query(
                f"SELECT ts FROM {REFS} WHERE symbol = {{s:String}} AND ref_kind = 'schwab_daily' "
                "AND ts >= {lo:DateTime64(3, 'UTC')}", parameters={"s": symbol, "lo": start}).result_rows}
            days = client.price_history(label.ref_symbol, start, now)
            new = schwab_rows(symbol, label.ref_symbol, days, existing)
            if new:
                ch.insert(REFS, new, column_names=REF_COLS)
            print(f"{symbol}: {len(new)} new daily opens of {len(days)}")
        except Exception as e:  # noqa: BLE001 — one bad symbol must not stop the load; named below, exit non-zero
            failed.append(symbol)
            print(f"{symbol}: FAILED {type(e).__name__}", file=sys.stderr)
    if client is None:
        print("SCHWAB_* not set: stock opens skipped", file=sys.stderr)
    print(f"done: {len(labels) - len(failed)} of {len(labels)} perps")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(only=sys.argv[1:] or None))
