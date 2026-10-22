"""Dashboard server: serves the published bundle (BUNDLE_DIR) read-only. Routes only; I/O lives in store.py.

GET /api/live                     -> {"bundle": LiveBundle, "age_s": float}; 503 {"detail": "no bundle yet"}
GET /api/pair/{key}/history?range -> Store.history (columns -> lists); 404 when the key is neither live nor in history
GET /api/pair/{key}/snapshot?ts   -> Store.snapshot (one row); 404 when no row within 90 s
GET /api/walk?key&size            -> edges.fill on the pair's published books (size = contracts) as JSON, plus
                                     transfer_cost_usd on cost_usd; 422 size <= 0 or > 1e6, 404 unknown key,
                                     409 {"detail": "books not published"} while show_books is False or the pair has none
GET /healthz                      -> {"ok": true, "age_s": float, "pairs": n}; 503 as /api/live
Keys travel path-encoded: urllib.parse.quote(key, safe="").
"""
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from dashboard.api.store import NoBundle, Store
from venue_arb.bundle import LiveBundle
from venue_arb.edges import fill
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee, transfer_cost
from venue_arb.models import Level

STATIC = Path(__file__).parent.parent / "static"
store = Store(Path(os.environ.get("BUNDLE_DIR", "/srv/venue-arb/bundle")))

app = FastAPI(title="venue-arb dashboard", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # live.json is ~70–100 KB today (research included)
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _live() -> tuple[LiveBundle, float]:
    try:
        return store.live()
    except NoBundle:
        raise HTTPException(503, "no bundle yet") from None


@app.get("/", include_in_schema=False)
def index():
    page = STATIC / "index.html"
    return FileResponse(page) if page.is_file() else PlainTextResponse("dashboard not built")


@app.get("/api/live")
def live():
    bundle, age = _live()
    # ponytail: re-serialised per request; cache the bytes per bundle if viewers ever load the box
    return {"bundle": bundle.model_dump(mode="json"), "age_s": age}


@app.get("/healthz")
def healthz():
    bundle, age = _live()
    return {"ok": True, "age_s": age, "pairs": len(bundle.pairs)}


@app.get("/api/pair/{key}/history")
def history(key: str, range: Literal["1h", "24h", "7d", "all"] = "24h"):
    out = store.history(key, range)
    if not out:
        try:
            known = any(p.key == key for p in store.live()[0].pairs)
        except NoBundle:
            known = False
        if not known:
            raise HTTPException(404, "unknown key")
    return out


@app.get("/api/pair/{key}/snapshot")
def snapshot(key: str, ts: datetime):
    if (row := store.snapshot(key, ts)) is None:
        raise HTTPException(404, "no row within 90 s")
    return row


@app.get("/api/walk")
def walk(key: str, size: float = Query(gt=0, le=1e6)):
    bundle, _ = _live()
    pair = next((p for p in bundle.pairs if p.key == key), None)
    if pair is None:
        raise HTTPException(404, "unknown key")
    if not bundle.show_books or pair.books is None:
        raise HTTPException(409, "books not published")
    k, pm = pair.fee_params["k"], pair.fee_params["pm"]
    k_asks, pm_asks = ([Level(side, i, lv.price, lv.size) for i, lv in enumerate(book)]
                       for side, book in zip((pair.legs[0].side, pair.legs[1].side), pair.books))
    f = fill(k_asks, pm_asks,
             lambda p, q: kalshi_taker_fee(p, q, k["fee_type"], k["fee_multiplier"]),
             lambda p, q: polymarket_taker_fee(p, q, pm["schedule"], pm["fees_enabled"]), size)
    return {**asdict(f), "transfer_cost_usd": transfer_cost(f.cost_usd)}
