"""Hyperliquid public info API (POST, no key): trade.xyz stock perps live on the `xyz` builder dex; a label on another
builder dex (`para:ANTH`, `io:ANTH`) is collected privately from that dex and never reaches the public page."""

import csv
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from venue_arb import config

INFO = "https://api.hyperliquid.xyz/info"
DEX = "xyz"
LABELS = Path("labels/stock_perps.csv")
EXCLUDED = Path("labels/stock_perps_excluded.txt")


@dataclass(frozen=True)
class PerpCtx:
    symbol: str
    mark: float
    oracle: float
    mid: float | None
    funding_h: float  # venue hourly funding rate as a fraction
    premium: float | None
    open_interest: float
    day_volume_usd: float
    growth_mode: bool
    max_leverage: int
    delisted: bool = False  # venue `isDelisted`; catalog drops these


@dataclass(frozen=True)
class PerpLevel:
    side: Literal["bid", "ask"]
    level: int
    price: float
    size: float


@dataclass(frozen=True)
class Label:
    perp: str
    ref_kind: Literal["schwab", "none"]
    ref_symbol: str | None
    exchange: str
    currency: str
    name: str
    kind: Literal["equity", "pre_ipo"] = "equity"


def post_json(body: dict, post=None, delay: float = 0.1, tries: int = 4) -> dict | list:
    """Polite POST to the info endpoint. Retries 429/5xx/transport errors; errors name the request type only."""
    post = post or httpx.post
    kind = body.get("type")
    time.sleep(delay)
    for i in range(tries):
        try:
            r = post(INFO, json=body, timeout=30)
            if r.status_code != 429 and r.status_code < 500:
                r.raise_for_status()
                return r.json()
            why = f"HTTP {r.status_code}"
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"POST info {kind} failed: HTTP {e.response.status_code}") from None
        except httpx.TransportError as e:
            why = type(e).__name__
        if i < tries - 1:
            time.sleep(delay + 2**i)
    raise RuntimeError(f"POST info {kind} failed after {tries} tries: {why}")


def _f(x) -> float | None:
    return None if x is None else float(x)


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def _ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)


def dex_of(symbol: str) -> str:
    """The builder-dex prefix of a perp symbol ("para:ANTH" -> "para"); "" for a bare main-dex coin."""
    return symbol.split(":", 1)[0] if ":" in symbol else ""


def fetch_ctxs(post=post_json, dex: str = DEX) -> list[PerpCtx]:
    meta, ctxs = post({"type": "metaAndAssetCtxs", "dex": dex})
    return [
        PerpCtx(
            symbol=u["name"], mark=float(c["markPx"]), oracle=float(c["oraclePx"]), mid=_f(c.get("midPx")),
            funding_h=float(c["funding"]), premium=_f(c.get("premium")), open_interest=float(c["openInterest"]),
            day_volume_usd=float(c["dayNtlVlm"]), growth_mode=u.get("growthMode") == "enabled",
            max_leverage=int(u["maxLeverage"]), delisted=bool(u.get("isDelisted", False)),
        )
        for u, c in zip(meta["universe"], ctxs, strict=True)
    ]


def fetch_ctxs_for(labels: dict[str, Label], post=post_json) -> list[PerpCtx]:
    """Contexts for the collector: every perp on DEX (so catalog() can flag unlabelled trade.xyz listings), then only
    the labelled perps of each other deployer in the label file, in label order. A deployer whose call fails is
    skipped for this tick (logged by name, no body); DEX's own failure propagates as before."""
    out = fetch_ctxs(post, DEX)
    for dex in dict.fromkeys(d for s in labels if (d := dex_of(s)) and d != DEX):
        try:
            out += [c for c in fetch_ctxs(post, dex) if c.symbol in labels]
        except Exception as e:  # noqa: BLE001 — one deployer down must not cost the trade.xyz tick
            logging.getLogger(__name__).warning("contexts fetch failed dex=%s error=%s", dex, type(e).__name__)
    return out


def fetch_book(symbol: str, post=post_json) -> tuple[datetime, list[PerpLevel]]:
    r = post({"type": "l2Book", "coin": symbol})
    bids, asks = r["levels"]
    levels = []
    for side, rows, rev in (("bid", bids, True), ("ask", asks, False)):
        top = sorted(rows, key=lambda x: float(x["px"]), reverse=rev)[: config.BOOK_DEPTH]
        levels += [PerpLevel(side, i, float(x["px"]), float(x["sz"])) for i, x in enumerate(top)]
    return _utc(r["time"]), levels


def fetch_candles(symbol: str, start: datetime, end: datetime, post=post_json) -> list[dict]:
    req = {"coin": symbol, "interval": "1h", "startTime": _ms(start), "endTime": _ms(end)}
    return [
        {"hour": _utc(c["t"]), "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
         "close": float(c["c"]), "volume": float(c["v"])}
        for c in post({"type": "candleSnapshot", "req": req})
    ]


def fetch_funding(symbol: str, start: datetime, post=post_json) -> list[dict]:
    """`hour` is the event time floored to the hour, the same key and meaning as fetch_candles."""
    rows = post({"type": "fundingHistory", "coin": symbol, "startTime": _ms(start)})
    return [{"hour": _utc(r["time"]).replace(minute=0, second=0, microsecond=0), "rate": float(r["fundingRate"]), "premium": float(r["premium"])} for r in rows]


def load_labels(path: Path = LABELS) -> dict[str, Label]:
    """ValueError naming the row when kind/ref_kind are invalid, a schwab row has no ref_symbol, a pre_ipo row has a
    reference or an exchange, or an equity row has no exchange."""
    out = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            kind, rk, sym, ex = r["kind"], r["ref_kind"], r["ref_symbol"].strip(), r["exchange"].strip()
            if (kind not in ("equity", "pre_ipo") or rk not in ("schwab", "none") or (rk == "schwab" and not sym)
                    or (kind == "pre_ipo" and (sym or ex or rk != "none")) or (kind == "equity" and not ex)):
                raise ValueError(f"{path}: bad label row {r['perp']!r} (kind={kind!r}, ref_kind={rk!r}, "
                                 f"ref_symbol={r['ref_symbol']!r}, exchange={r['exchange']!r})")
            out[r["perp"]] = Label(r["perp"], rk, r["ref_symbol"] or None, r["exchange"], r["currency"], r["name"], kind)
    return out


def load_excluded(path: Path = EXCLUDED) -> set[str]:
    """One symbol per line; a trailing `# reason` and blank lines are ignored."""
    lines = (ln.split("#", 1)[0].strip() for ln in path.read_text().splitlines())
    return {ln for ln in lines if ln}


def catalog(
    ctxs: list[PerpCtx], labels: dict[str, Label], excluded: set[str] = frozenset()
) -> tuple[list[PerpCtx], list[str]]:
    """(labelled perps in label order, symbols the venue lists that are in neither file); delisted perps are dropped."""
    by_symbol = {c.symbol: c for c in ctxs if not c.delisted}
    kept = [by_symbol[s] for s in labels if s in by_symbol]
    unlabelled = [s for s in by_symbol if s not in labels and s not in excluded]
    return kept, unlabelled


def label_or_none(symbol: str, labels: dict[str, Label]) -> Label:
    return labels.get(symbol) or Label(symbol, "none", None, "", "USD", symbol)
