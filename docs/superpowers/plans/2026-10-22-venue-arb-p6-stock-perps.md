# Stock-Perp Basis (Plan 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether trade.xyz stock perpetuals on Hyperliquid trade away from the stocks they track, whether that
gap is executable after round-trip fees, funding and depth, and what the weekend perp says about Monday's open; show it
as a "Stock perps" category on the live dashboard.

**Architecture:** A second 60-second collector (`venue_arb.collect_perps`) snapshots every equity perp's mark, oracle,
funding and book from Hyperliquid's public info API and one batched Schwab quote for the US names, into four additive
ClickHouse tables. An hourly Prefect flow scores each snapshot into a net basis (round trip, 24 h hold) and episodes
(`source = 'stock_perp'`); a Monday flow writes one weekend row per name. The publisher adds the rows, a research
block and history columns to the existing bundle, gated by `DASHBOARD_SHOW_STOCK_PERPS`. Pure modules first, I/O at the
edges, as the rest of the repo.

**Tech Stack:** Python 3.12, httpx, pydantic v2, pyarrow, ClickHouse + Postgres through the platform `qa` package,
Prefect 3 `serve`, vanilla JS + vendored Plotly, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-22-venue-arb-stock-perp-basis-design.md` (with its 2026-10-22 Pyth
amendment: Asia-listed names are oracle-only; no Pyth client is built).

## Global Constraints

- Read-only public endpoints; no orders, no account calls; no geoblock circumvention.
- Schwab: Market Data product only; `SCHWAB_CLIENT_ID` / `SCHWAB_CLIENT_SECRET` already in Bitwarden; the refresh
  token lives in Bitwarden as `SCHWAB_REFRESH_TOKEN` with `SCHWAB_REFRESH_ISSUED_AT` (ISO 8601 UTC) beside it;
  never a token file in the repo; Schwab quotes never shown raw.
- No raw quotes in the bundle while `config.DASHBOARD_SHOW_BOOKS` is False; stock-perp rows and research render only
  while `config.DASHBOARD_SHOW_STOCK_PERPS` is True (default False).
- Snapshot cadence 60 s (`config.SNAPSHOT_INTERVAL_S`); the publisher's 60 s bundle and rsync push are unchanged.
- Thresholds: `STOCK_PERP_MIN_NET_BPS = 10.0`, `MIN_CAPACITY_USD = 100.0`, 2 consecutive snapshots, 150 s gap breaks
  a run (`episodes.MAX_GAP_S`). Hold assumption `STOCK_PERP_HOLD_H = 24`, sensitivities `(1, 168)`.
  Borrow `STOCK_PERP_BORROW_BPS_YR = 50.0` (sensitivity ×10). Stock leg capped at displayed top-of-book size, charged
  the full quoted spread (half at entry, half at exit). Perp taker fee charged twice.
- Reference freshness: a Schwab quote older than `REF_MAX_AGE_S = 120` s is absent (the row falls back to the oracle).
  A perp snapshot older than `bundle.LIVE_MAX_AGE_S` (300 s) is stale.
- Migrations additive only (`db/migrations/ch/008_*.sql`, `db/migrations/pg/007_*.sql`); every new table partitioned
  by month, `ReplacingMergeTree` where re-scoring rewrites keys.
- Episodes with `source = 'stock_perp'` never enter the header's "episodes · 7 d", `bundle.episodes[]`, or any
  pair/ladder query.
- CI before every commit: `uv run ruff check .` and `uv run pytest -m "not integration" -q` (the obs tests need the
  platform checkout; run the suite on the OptiPlex `ci-<sha>` worktree before merging). No Co-Authored-By trailers.
- Copy rules: verdict labels verbatim as listed in Task 8; mid-dots only inside verdict labels; no emoji.

## Review Focus

1. **Schwab quote after 8 PM ET** keeps returning the last quote with an old `quoteTime`; a row must fall back to the
   oracle (Reference cell "oracle only"), never show a stale stock price as live. Pinned in Task 5 (`test_reference_older_than_max_age_is_absent`).
2. **Perp above reference but the stock bid size is 0** (no displayed size): capacity is $0 and the verdict is not
   executable even with a large basis. Pinned in Task 5 (`test_zero_stock_size_gives_zero_capacity`).
3. **Funding sign**: a long-perp position pays positive funding; a short-perp position receives it. A wrong sign flips
   which direction clears. Pinned in Task 5 (`test_funding_sign_by_side`).
4. **A new perp listed by the venue with no `labels/stock_perps.csv` row** must be collected and shown oracle-only,
   not crash the collector or the bundle. Pinned in Task 1 (`test_unlabelled_perp_is_oracle_only`) and Task 4.
5. **Weekend with a missing Sunday 6 PM snapshot** (collector gap) must yield a row with that field null, not skip the
   weekend or pick a Monday price. Pinned in Task 7 (`test_missing_sample_is_null_not_skipped`).

---

### Task 1: Hyperliquid source, perp catalog and labels

**Files:**
- Create: `venue_arb/sources/hyperliquid.py`, `labels/stock_perps.csv`, `tests/test_hyperliquid.py`,
  `tests/fixtures/hyperliquid/{meta_ctxs.json,l2book.json,candles.json,funding.json}`
- Modify: `venue_arb/taxonomy.py:35` (DISPLAY), `venue_arb/config.py`, `tests/test_taxonomy.py`

**Interfaces:**
- Produces:
  - `INFO = "https://api.hyperliquid.xyz/info"`, `DEX = "xyz"`, `post_json(body: dict, post=None) -> dict | list`
    (httpx POST, same retry policy as `http.get_json`: 429/5xx/transport retried, `tries=4`).
  - `fetch_ctxs(post=post_json) -> list[PerpCtx]` from `{"type": "metaAndAssetCtxs", "dex": DEX}`;
    `PerpCtx(symbol: str, mark: float, oracle: float, mid: float | None, funding_h: float, premium: float | None,
    open_interest: float, day_volume_usd: float, growth_mode: bool, max_leverage: int)`; `funding_h` is the venue's
    hourly rate as a fraction (ctx `funding`).
  - `fetch_book(symbol: str, post=post_json) -> tuple[datetime, list[PerpLevel]]` from `{"type": "l2Book", "coin":
    symbol}`; `PerpLevel(side: Literal["bid", "ask"], level: int, price: float, size: float)`, best-first per side,
    top `config.BOOK_DEPTH`; the datetime is the response `time` (ms since epoch, UTC).
  - `fetch_candles(symbol, start: datetime, end: datetime, post=post_json) -> list[dict]` (`candleSnapshot`,
    interval `"1h"`; dicts `{"hour", "open", "high", "low", "close", "volume"}`, `hour` UTC-aware from `t`).
  - `fetch_funding(symbol, start: datetime, post=post_json) -> list[dict]` (`fundingHistory`; `{"hour", "rate",
    "premium"}`).
  - `load_labels(path: Path = LABELS) -> dict[str, Label]`; `Label(perp: str, ref_kind: Literal["schwab", "none"],
    ref_symbol: str | None, exchange: str, currency: str, name: str)`; `LABELS = Path("labels/stock_perps.csv")`.
  - `load_excluded(path: Path = EXCLUDED) -> set[str]`; `EXCLUDED = Path("labels/stock_perps_excluded.txt")`.
  - `catalog(ctxs: list[PerpCtx], labels: dict[str, Label], excluded: set[str] = frozenset()) -> tuple[list[PerpCtx],
    list[str]]`: perps with a label (in label order) and the symbols the venue lists that are in neither file (the
    collector warns on them and still snapshots them).
  - `label_or_none(symbol: str, labels: dict[str, Label]) -> Label`: the label, or `Label(symbol, "none", None, "",
    "USD", symbol)` for an unlabelled perp (oracle-only everywhere downstream).
  - `config.CATEGORIES` gains `"stock_perps"`; `taxonomy.DISPLAY["stock_perps"] = "Stock perps"`;
    `config.DASHBOARD_SHOW_STOCK_PERPS = False`; `config.STOCK_PERP_MIN_NET_BPS = 10.0`,
    `STOCK_PERP_HOLD_H = 24`, `STOCK_PERP_HOLD_SENSITIVITY_H = (1, 168)`, `STOCK_PERP_BORROW_BPS_YR = 50.0`,
    `REF_MAX_AGE_S = 120`.

- [ ] **Step 1: Write the fixtures** by hand from the shapes observed 2026-10-22: `metaAndAssetCtxs` returns
  `[{"universe": [{"name": "xyz:NVDA", "szDecimals": 3, "maxLeverage": 20, "growthMode": "enabled"}, ...]},
  [{"funding": "0.0000062", "openInterest": "411000", "prevDayPx": "219.1", "dayNtlVlm": "79100000", "premium":
  "0.00008", "oraclePx": "212.81", "markPx": "212.83", "midPx": "212.83", "impactPxs": ["212.8", "212.86"]}, ...]]`;
  `l2Book` returns `{"coin": "xyz:NVDA", "time": 1792690873721, "levels": [[{"px": "212.82", "sz": "111.594", "n": 6},
  ...bids], [{"px": "212.84", "sz": "308.321", "n": 3}, ...asks]]}`; `candleSnapshot` a list of `{"t", "T", "s", "i",
  "o", "c", "h", "l", "v", "n"}` with string prices; `fundingHistory` a list of `{"coin", "fundingRate", "premium",
  "time"}`. Three universe entries: `xyz:NVDA` (growth), `xyz:GOLD` (no growthMode key), `xyz:NEWCO` (not in labels).

- [ ] **Step 2: Write the failing tests**

```python
def test_fetch_ctxs_parses_growth_and_funding(fx):
    ctxs = hl.fetch_ctxs(post=lambda body: fx["meta_ctxs"])
    nvda = next(c for c in ctxs if c.symbol == "xyz:NVDA")
    assert nvda.mark == 212.83 and nvda.oracle == 212.81 and nvda.growth_mode and nvda.max_leverage == 20
    assert nvda.funding_h == pytest.approx(6.2e-6) and nvda.day_volume_usd == 79_100_000.0
    assert not next(c for c in ctxs if c.symbol == "xyz:GOLD").growth_mode

def test_fetch_book_best_first_and_depth(fx):
    ts, levels = hl.fetch_book("xyz:NVDA", post=lambda body: fx["l2book"])
    assert ts == datetime(2026, 10, 22, 17, 41, 13, 721000, tzinfo=UTC)
    bids = [lv for lv in levels if lv.side == "bid"]; asks = [lv for lv in levels if lv.side == "ask"]
    assert bids[0].price == 212.82 and bids[0].size == 111.594 and bids[0].level == 0
    assert asks[0].price == 212.84 and all(b.price > bids[i + 1].price for i, b in enumerate(bids[:-1]))
    assert len(bids) <= BOOK_DEPTH and len(asks) <= BOOK_DEPTH

def test_candles_and_funding_hours_are_utc(fx): ...  # hour == datetime.fromtimestamp(t/1000, UTC); rate float

def test_labels_cover_every_equity_and_catalog_flags_unlabelled(fx):
    labels = hl.load_labels()
    assert labels["xyz:NVDA"] == hl.Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
    assert labels["xyz:SKHX"].ref_kind == "none" and labels["xyz:SKHX"].exchange == "KRX"
    assert "xyz:SPCX" not in labels and "xyz:GOLD" not in labels  # pre-IPO and commodities out of scope
    assert len(labels) >= 100 and all(k.startswith("xyz:") for k in labels)
    kept, unlabelled = hl.catalog(hl.fetch_ctxs(post=lambda body: fx["meta_ctxs"]), labels)
    assert [c.symbol for c in kept] == ["xyz:NVDA"] and unlabelled == ["xyz:NEWCO"]  # GOLD: labelled out, not unlabelled

def test_unlabelled_perp_is_oracle_only(fx):
    assert hl.label_or_none("xyz:NEWCO", {}).ref_kind == "none"  # Label with name=symbol, exchange "", currency "USD"
```

  Out-of-scope symbols are listed in `labels/stock_perps_excluded.txt` (one symbol per line, a trailing `# reason`);
  `catalog` reports as unlabelled only symbols in neither file. Add `assert "xyz:GOLD" in hl.load_excluded()`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hyperliquid.py -v`
Expected: FAIL with `ModuleNotFoundError: venue_arb.sources.hyperliquid`

- [ ] **Step 4: Write `labels/stock_perps.csv`** with header
  `perp,ref_kind,ref_symbol,exchange,currency,name` and one row per equity or equity-ETF perp on trade.xyz as of
  2026-10-22. US-listed, `ref_kind=schwab`, `ref_symbol` = the perp's ticker without the `xyz:` prefix, `currency=USD`:
  AAOI AAPL ACN AMAT AMD AMZN ARM ASML AVGO BABA BB BE BMNR BOT BX COIN COST CRCL CRWD CRWV CVX DELL DKNG EBAY EWJ EWT
  EWY EWZ GEV GLW GME GOOGL HIMS HOOD IBM INTC IREN KORU KSTR LITE LLY LRCX LYTE MAGS META MRNA MRVL MSFT MSTR MU NBIS
  NCLD NET NFLX NOK NOW NVDA ORCL OURA PLTR QCOM RDDT RIVN RKLB SHAZ SKHY SMH SNAP SNDK SNXX SOXL STRC TLT TSLA TSM
  TTWO TWST UMC URNM USAR WDC XBI XLE ZM (exchange per the trade.xyz Specification Index: NASDAQ, NYSE, NYSE Arca or
  Cboe BZX). Asia-listed, `ref_kind=none`, `ref_symbol` = the home-exchange code: SKHX (KRX 000660, KRW), SMSN (KRX
  005930, KRW), HYUNDAI (KRX 005380, KRW), SOFTBANK (TSE 9984, JPY), KIOXIA (TSE 285A, JPY), IBIDEN (TSE 4062, JPY),
  CAMBRICON (SSE 688256, CNY), CXMT (SSE 688825, CNY), GIGADEV (SSE 603986, CNY), UNITREE (SSE 688836, CNY), AMEC,
  INNOLIGHT, CATL, YMTC (SSE/SZSE, CNY; codes from the Specification Index), SHEIN (HKEX 0625, HKD), JIO, RELIANCE
  (NSE, INR), MINIMAX, ZHIPU (HKEX, HKD). `labels/stock_perps_excluded.txt`: SPCX QNT CBRS (pre-IPO), SP500 XYZ100
  JP225 KR200 NIFTY IBOV VIX DXY (index), EUR GBP JPY KRW (FX), GOLD SILVER COPPER CL BRENTOIL HO NATGAS TTF CORN
  WHEAT URANIUM PLATINUM PALLADIUM ALUMINIUM DIESEL (commodity), DRAM H100 (spot index). Symbols the venue lists that
  are in neither file at build time (e.g. BIRD, MUU, PURRDAT, VOL): look each up in the Specification Index at
  https://docs.trade.xyz/llms-full.txt; an equity or equity ETF goes in the CSV, anything else in the excluded file.

- [ ] **Step 5: Implement `venue_arb/sources/hyperliquid.py`** per the Interfaces block. `post_json` mirrors
  `http._get`'s loop with `httpx.post(INFO, json=body, timeout=30)`. `fetch_book` sorts bids descending and asks
  ascending by price, numbering `level` from 0, and truncates to `config.BOOK_DEPTH`.

- [ ] **Step 6: Add the config constants and `DISPLAY["stock_perps"]`**; extend `tests/test_taxonomy.py` with
  `test_display_covers_every_category` (`set(CATEGORIES) <= set(DISPLAY)`).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_hyperliquid.py tests/test_taxonomy.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add venue_arb/sources/hyperliquid.py labels/ tests/test_hyperliquid.py tests/fixtures/hyperliquid tests/test_taxonomy.py venue_arb/taxonomy.py venue_arb/config.py
git commit -m "Stock perps: Hyperliquid info source, perp labels, category"
```

---

### Task 2: Schwab Market Data source and the token flow

**Files:**
- Create: `venue_arb/sources/schwab.py`, `tests/test_schwab.py`, `tests/fixtures/schwab/{quotes.json,pricehistory.json,token.json}`
- Modify: `README.md` (Schwab section), `docs/venues.md` (new "Schwab Market Data" section)

**Interfaces:**
- Produces:
  - `AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"`, `TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"`,
    `API = "https://api.schwabapi.com/marketdata/v1"`, `REFRESH_TTL = timedelta(days=7)`, `ACCESS_TTL_S = 1800`,
    `QUOTE_BATCH = 100` (symbols per `quotes` call), `CALLS_PER_MIN = 120`.
  - `class Client`: `__init__(self, client_id: str, secret: str, refresh_token: str, post=httpx.post, get=httpx.get,
    clock=time.monotonic)`; `access_token(self) -> str` (POST `TOKEN_URL`, Basic auth `client_id:secret`, form
    `grant_type=refresh_token&refresh_token=...`; cached until 60 s before `expires_in`); `quotes(self, symbols:
    list[str]) -> dict[str, Quote]` (GET `{API}/quotes?symbols=A,B&fields=quote`, batched by `QUOTE_BATCH`, bearer
    header); `price_history(self, symbol: str, start: datetime, end: datetime) -> list[dict]` (GET
    `{API}/pricehistory?symbol=&periodType=month&frequencyType=daily&frequency=1&startDate=ms&endDate=ms`;
    `{"day", "open", "close"}` rows, `day` a UTC date of the session).
  - `Quote(symbol: str, bid: float, ask: float, bid_size: float, ask_size: float, last: float | None, quote_time:
    datetime)`; `quote_time` from the response's `quote.quoteTime` (ms since epoch, UTC). `bid_size`/`askSize` are in
    shares as Schwab reports them (the API reports round lots for some symbols: record the raw value and note the
    uncertainty in `docs/venues.md`; the executable tier treats it as shares, which can only understate capacity if
    Schwab's unit is lots).
  - `class SchwabAuthError(RuntimeError)` raised on HTTP 401 from either URL.
  - `refresh_expires_at(issued_at: datetime) -> datetime` (= `issued_at + REFRESH_TTL`).
  - `exchange_code(client_id, secret, redirect_url: str, redirect_uri: str, post=httpx.post) -> dict` (form
    `grant_type=authorization_code&code=<url-decoded code from redirect_url>&redirect_uri=`; returns the token JSON).
  - CLI: `python -m venue_arb.sources.schwab login` prints the authorize URL
    (`?response_type=code&client_id=...&redirect_uri=https://127.0.0.1`), reads the pasted redirect URL from stdin,
    calls `exchange_code`, then writes `SCHWAB_REFRESH_TOKEN` and `SCHWAB_REFRESH_ISSUED_AT` to the Bitwarden project
    with `bws secret create|edit` (`BWS_ACCESS_TOKEN` must be a write-capable token in the caller's environment;
    `BWS_PROJECT_ID` from the environment as the `secrets` wrapper uses it), and runs
    `systemctl --user restart venue-arb-perps` unless `--no-restart`. Secret values never print; the CLI prints only
    the secret names it wrote and the restart result.

- [ ] **Step 1: Write the fixtures.** `quotes.json`: `{"NVDA": {"assetMainType": "EQUITY", "quote": {"askPrice":
  212.85, "askSize": 300, "bidPrice": 212.83, "bidSize": 200, "lastPrice": 212.84, "quoteTime": 1792690873000}},
  "AAPL": {...}}`; `pricehistory.json`: `{"candles": [{"open": 210.0, "high": 0, "low": 0, "close": 212.0, "volume": 1,
  "datetime": 1792627200000}, ...], "symbol": "NVDA", "empty": false}`; `token.json`: `{"access_token": "a",
  "refresh_token": "r", "expires_in": 1800, "token_type": "Bearer"}`.

- [ ] **Step 2: Write the failing tests**

```python
def test_access_token_cached_until_sixty_seconds_before_expiry(fx):
    calls = []
    clock = [0.0]
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: calls.append(k) or _resp(fx["token"]), clock=lambda: clock[0])
    assert c.access_token() == "a" and c.access_token() == "a" and len(calls) == 1
    assert calls[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "r"} and "r" not in str(calls[0].get("headers"))
    clock[0] = 1800 - 59
    c.access_token(); assert len(calls) == 2

def test_quotes_batched_and_parsed(fx):
    urls = []
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp(fx["token"]), get=lambda url, **k: urls.append(url) or _resp(fx["quotes"]))
    q = c.quotes([f"S{i}" for i in range(150)])
    assert len(urls) == 2 and "symbols=S0%2CS1" in urls[0] or "symbols=S0,S1" in urls[0]
    assert q["NVDA"] == schwab.Quote("NVDA", 212.83, 212.85, 200.0, 300.0, 212.84, datetime(2026, 10, 22, 17, 41, 13, tzinfo=UTC))

def test_401_raises_auth_error(fx): ...  # get returns status 401 -> SchwabAuthError

def test_price_history_rows(fx): ...  # [{"day": date(2026, 10, 21), "open": 210.0, "close": 212.0}]

def test_refresh_expires_at(): assert schwab.refresh_expires_at(datetime(2026, 10, 22, tzinfo=UTC)) == datetime(2026, 10, 29, tzinfo=UTC)

def test_login_cli_writes_secrets_without_printing_values(fx, capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(schwab, "exchange_code", lambda *a, **k: {"refresh_token": "SECRETVALUE", "access_token": "x"})
    monkeypatch.setattr(schwab.subprocess, "run", lambda cmd, **k: ran.append(cmd) or SimpleNamespace(returncode=0, stdout="[]"))
    schwab.main(["login", "--no-restart"], stdin=io.StringIO("https://127.0.0.1/?code=abc%40def&session=1\n"), env={"SCHWAB_CLIENT_ID": "id", "SCHWAB_CLIENT_SECRET": "s", "BWS_PROJECT_ID": "p", "BWS_ACCESS_TOKEN": "w"})
    out = capsys.readouterr().out
    assert "SECRETVALUE" not in out and "SCHWAB_REFRESH_TOKEN" in out and "SCHWAB_REFRESH_ISSUED_AT" in out
    assert any("bws" in c[0] for c in ran) and not any("systemctl" in c[0] for c in ran)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_schwab.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement `venue_arb/sources/schwab.py`** per the Interfaces block. `bws` calls: `bws secret list
  --output json <project>` to find existing ids, then `bws secret edit <id> --value <v>` or `bws secret create <name>
  <v> <project>`; pass the value as an argument (not on stdout), as the CLI's own README documents.

- [ ] **Step 5: Document** in `docs/venues.md` ("Schwab Market Data (2026-10-22)": endpoints, 120 calls/min,
  token lifetimes, the weekly login runbook, personal-use terms with the display question marked "Sam to read",
  the bid/ask size unit uncertainty) and replace the README's Schwab paragraph with the runbook pointer.

- [ ] **Step 6: Run the tests to verify they pass**, then `uv run ruff check .`

- [ ] **Step 7: Commit**

```bash
git add venue_arb/sources/schwab.py tests/test_schwab.py tests/fixtures/schwab docs/venues.md README.md
git commit -m "Stock perps: Schwab Market Data client, quotes, price history, weekly login CLI"
```

---

### Task 3: Tables, SQL checks and the opportunities source

**Files:**
- Create: `db/migrations/ch/008_stock_perps.sql`, `db/migrations/pg/007_stock_perp_weekends.sql`,
  `sql_checks/stock_perp_edges_capacity_consistent.sql`, `sql_checks/stock_perp_books_fresh.sql`
- Modify: `sql_checks/opportunities_source_valid.sql`, `tests/test_migrate.py`

**Interfaces:**
- Produces (ClickHouse, all `PARTITION BY toYYYYMM(ts)`):
  - `venue_arb.stock_perp_books (ts DateTime64(3,'UTC'), symbol LowCardinality(String), side LowCardinality(String),
    level UInt8, price Float64, size Float64) ENGINE = MergeTree ORDER BY (symbol, ts, side, level)`, `TTL ts + INTERVAL 90 DAY`.
  - `venue_arb.stock_perp_ctx (ts DateTime64(3,'UTC'), symbol, mark Float64, oracle Float64, mid Nullable(Float64),
    funding_h Float64, premium Nullable(Float64), open_interest Float64, day_volume_usd Float64, growth_mode UInt8)
    ENGINE = MergeTree ORDER BY (symbol, ts)`, TTL 90 d.
  - `venue_arb.stock_perp_refs (ts DateTime64(3,'UTC'), symbol, ref_kind LowCardinality(String), ref_symbol String,
    bid Float64, ask Float64, bid_size Float64, ask_size Float64, last Nullable(Float64), quote_time DateTime64(3,'UTC'))
    ENGINE = MergeTree ORDER BY (symbol, ts)`, TTL 90 d. `ts` is the collector tick; `quote_time` the venue's.
  - `venue_arb.stock_perp_candles (hour DateTime('UTC'), symbol, open, high, low, close, volume Float64, funding_h
    Nullable(Float64)) ENGINE = ReplacingMergeTree ORDER BY (symbol, hour)`, no TTL (the weekend tier's history).
  - `venue_arb.stock_perp_edges (ts DateTime64(3,'UTC'), symbol, direction LowCardinality(String), ref_kind
    LowCardinality(String), reference Float64, mark Float64, basis_bps Float64, perp_fee_bps Float64,
    stock_spread_bps Nullable(Float64), funding_bps Float64, borrow_bps Float64, net_bps Float64,
    capacity_contracts Float64, capacity_usd Float64, fee_version String) ENGINE = ReplacingMergeTree
    ORDER BY (symbol, direction, ts)`. `direction` ∈ `{"short_perp_long_stock", "long_perp_short_stock"}`.
- Produces (Postgres): `venue_arb.stock_perp_weekends (symbol text, friday date, p_fri double precision, p_sat
  double precision, p_sun double precision, p_open_pre double precision, p_open_reg double precision, ref_kind text
  NOT NULL, computed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (symbol, friday))`.
- Modifies: `opportunities_source_valid.sql` accepts `source = 'stock_perp'` with the two directions above.

- [ ] **Step 1: Write the failing test** in `tests/test_migrate.py`:

```python
def test_stock_perp_migrations_are_additive_and_numbered():
    ch = Path("db/migrations/ch/008_stock_perps.sql").read_text(); pg = Path("db/migrations/pg/007_stock_perp_weekends.sql").read_text()
    assert ch.count("CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_") == 5 and "DROP" not in ch.upper()
    assert "PARTITION BY toYYYYMM(ts)" in ch and "CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_weekends" in pg
    assert all(s.startswith("CREATE") for s in split_statements(ch))
```

  And in a new `tests/test_sql_checks.py` (or extend an existing checks test if one exists): the source check file
  text contains `'stock_perp'` and both direction names.

- [ ] **Step 2: Run to verify it fails**, then write the migrations and checks. `stock_perp_edges_capacity_consistent.sql`
  (engine clickhouse, severity error, target `venue_arb.stock_perp_edges`): rows in the last 27 h with
  `capacity_contracts > 0 AND net_bps < 10 - 1e-9`, `LIMIT 10`. `stock_perp_books_fresh.sql` (severity warn): 1 row
  when `max(ts) < now64(3) - INTERVAL 5 MINUTE` over `stock_perp_ctx`.

- [ ] **Step 3: Apply on the OptiPlex** (deploy step, not CI): `secrets uv run python -m db.migrate` (the runner the
  repo already has; confirm with `uv run python -m db.migrate --help`). Record the table list in the commit message.

- [ ] **Step 4: Run the tests, commit**

```bash
git add db/migrations sql_checks tests/test_migrate.py tests/test_sql_checks.py
git commit -m "Stock perps: ClickHouse tables, weekend table, SQL checks, stock_perp opportunity source"
```

---

### Task 4: The perp collector

**Files:**
- Create: `venue_arb/collect_perps.py`, `deploy/venue-arb-perps.service`, `tests/test_collect_perps.py`
- Modify: `venue_arb/collect.py:141` (`flush` gains `table` and `contract` parameters, defaults unchanged),
  `tests/test_deploy.py:30` (`test_units_present` lists four units), `deploy/server/README.md` (no change) ,
  `README.md` (runbook line)

**Interfaces:**
- Consumes: Task 1 (`fetch_ctxs`, `fetch_book`, `load_labels`, `load_excluded`, `catalog`), Task 2 (`Client.quotes`,
  `SchwabAuthError`, `refresh_expires_at`), Task 3 tables.
- Produces:
  - `PIPELINE = f"{PIPELINE_PREFIX}_perps"`; `TABLES = {"books": "venue_arb.stock_perp_books", "ctx":
    "venue_arb.stock_perp_ctx", "refs": "venue_arb.stock_perp_refs"}`; `CONTRACTS` per table (as `collect.CONTRACT`).
  - `tick(perps: list[PerpCtx] | None, labels: dict, fetch_ctxs, fetch_book, quotes, buf: dict[str, list[dict]],
    stats: dict, now: datetime, workers: int = 8) -> list[PerpCtx]`: one `fetch_ctxs` call (rows → `buf["ctx"]`),
    books in a thread pool (→ `buf["books"]`), one `quotes(symbols)` call for every label with `ref_kind == "schwab"`
    (→ `buf["refs"]`, `ts=now`, `quote_time` from the quote). Failures counted in `stats[table] = [attempts, errors,
    empty]`, never raised, except `SchwabAuthError`, which is counted under `stats["schwab_auth"]` and logged once per
    flush. Returns the catalog for the next tick.
  - `flush_results(checks, stats, issued_at: datetime | None, now) -> list[checks.Result]`: `schwab_auth` (severity
    warn: the perp side keeps working) failing when any auth error was counted;
    `schwab_token_expires` (warn) failing when `refresh_expires_at(issued_at) - now < 24 h`; `unlabelled_perps` (warn)
    listing symbols the venue lists that are in neither label file.
  - `run_forever(interval_s=SNAPSHOT_INTERVAL_S, flush_every=5)`: as `collect.run_forever`, reading
    `SCHWAB_REFRESH_TOKEN` / `SCHWAB_REFRESH_ISSUED_AT` from the environment (the `secrets` wrapper injects them);
    without them the stock leg is skipped and `schwab_auth` warns every flush. Labels reloaded every 10 ticks.
  - Unit `deploy/venue-arb-perps.service`: a copy of the collector unit with `ExecStart=... python -m venue_arb.collect_perps`
    and `Description=venue-arb stock-perp snapshot collector`.

- [ ] **Step 1: Write the failing tests** (pattern: `tests/test_collect.py`, fakes injected, no qa):

```python
def test_tick_fills_three_buffers_and_quotes_once():
    calls = []
    perps = cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), lambda syms: calls.append(syms) or QUOTES, buf, stats, NOW)
    assert calls == [["NVDA", "AAPL"]] and len(buf["ctx"]) == 3 and len(buf["books"]) == 3 * len(BOOK)
    assert {r["symbol"] for r in buf["refs"]} == {"xyz:NVDA", "xyz:AAPL"} and buf["refs"][0]["quote_time"] == QUOTES["NVDA"].quote_time
    assert [p.symbol for p in perps] == ["xyz:NVDA", "xyz:AAPL", "xyz:NEWCO"]  # unlabelled still snapshotted

def test_book_failure_counted_not_raised(): ...  # fetch_book raises for one symbol: stats["books"][1] == 1, others stored

def test_schwab_auth_error_counted_and_perp_side_continues(): ...  # quotes raises SchwabAuthError: buf["refs"] == [], stats["schwab_auth"] == 1, ctx/books full

def test_flush_results_warn_day_before_expiry():
    issued = NOW - timedelta(days=6, hours=1)
    res = {r.check: r for r in cp.flush_results(FAKE_CHECKS, {"schwab_auth": 0, "unlabelled": ["xyz:NEWCO"]}, issued, NOW)}
    assert not res["schwab_token_expires"].passed and res["schwab_token_expires"].severity == "warn"
    assert not res["unlabelled_perps"].passed and "xyz:NEWCO" in res["unlabelled_perps"].detail

def test_unit_matches_collector_env():  # in tests/test_deploy.py
    assert unit_env(ROOT / "deploy" / "venue-arb-perps.service") == unit_env(ROOT / "deploy" / "venue-arb-collector.service")
```

- [ ] **Step 2: Run to verify they fail**, then implement `collect_perps.py` and the unit; generalise `collect.flush`
  with `table: str = TABLE, contract: dict = CONTRACT` and call it once per buffer (`venues` = the three table keys).

- [ ] **Step 3: Run `tests/test_collect_perps.py tests/test_collect.py tests/test_deploy.py`, ruff, commit**

```bash
git commit -am "Stock perps: 60 s collector for Hyperliquid books, contexts and Schwab quotes; systemd unit"
```

---

### Task 5: Basis, costs and the executable tier (pure)

**Files:**
- Create: `venue_arb/stock_perps.py`, `tests/test_stock_perps.py`
- Modify: `venue_arb/episodes.py:40` (`find` gains `min_edge` and `annualize`), `venue_arb/fees.py` (`FEE_VERSION` bump
  to `"2026-10-22"`; `HYPERLIQUID_TAKER = {"standard": 0.0009, "growth": 0.00009}`, `US_EXECUTABLE["hyperliquid"] = False`)
- Test: `tests/test_episodes.py` (existing tests unchanged; one added)

**Interfaces:**
- Consumes: `PerpLevel`, `PerpCtx` (Task 1), `Quote` (Task 2), `episodes.find`, `Edge`.
- Produces:
  - `Reference(kind: Literal["schwab", "oracle"], symbol: str, price: float, bid: float | None, ask: float | None,
    bid_size: float | None, ask_size: float | None, asof: datetime)`.
  - `reference(ctx: PerpCtx, quote: Quote | None, label: Label, now: datetime, max_age_s: int = REF_MAX_AGE_S) ->
    Reference`: the Schwab mid `(bid+ask)/2` when the label is `schwab`, the quote exists and `now - quote_time <=
    max_age_s`; otherwise the oracle (`kind="oracle"`, sizes None, `asof` = the ctx snapshot time).
  - `basis_bps(mark: float, ref: float) -> float` = `(mark / ref - 1) * 1e4`.
  - `direction(basis: float) -> Direction` (`"short_perp_long_stock"` when `basis > 0`, else `"long_perp_short_stock"`).
  - `walk_perp(levels: list[PerpLevel], side: Literal["bid", "ask"], cap_usd: float | None) -> tuple[float, float,
    float]`: `(contracts, usd, avg_price)` down the ladder until `cap_usd` is spent (None = whole displayed book).
  - `costs_bps(*, taker: float, spread_bps: float | None, funding_h: float, borrow_bps_yr: float, hold_h: float,
    side: Direction) -> dict`: `{"perp_fee": 2e4 * taker, "stock_spread": spread_bps (0.0 when None),
    "funding": sign * funding_h * hold_h * 1e4, "borrow": borrow_bps_yr * hold_h / (24 * 365) if the stock is the
    short leg else 0.0}`, with `sign = +1` for `long_perp_short_stock` (pays funding when positive) and `-1` for
    `short_perp_long_stock` (receives it).
  - `score(ctx: PerpCtx, book: list[PerpLevel], ref: Reference, *, hold_h: float = STOCK_PERP_HOLD_H, borrow_bps_yr:
    float = STOCK_PERP_BORROW_BPS_YR) -> StockPerpEdge` with `StockPerpEdge(symbol, direction, ref_kind, reference,
    mark, basis_bps, perp_fee_bps, stock_spread_bps, funding_bps, borrow_bps, net_bps, capacity_contracts,
    capacity_usd, stock_cap_usd: float | None)`. The perp leg is walked on the side the direction takes (short perp →
    bids); the stock leg's cap is `bid_size × bid` (long stock → asks: `ask_size × ask`); `capacity_usd` is the
    smaller of the two legs' dollars and `capacity_contracts` that many shares at the perp's average price. With an
    oracle reference `stock_spread_bps` is None, `capacity_usd = 0.0`, and `net_bps` is the gross basis minus the
    perp round trip and carry (so the Scanner still shows a number, never a verdict).
  - `gross_to_net(edge: StockPerpEdge, hold_h: float) -> float` for the sensitivity line.
  - `edge_row(ts: datetime, e: StockPerpEdge, fee_version: str) -> dict` (the `stock_perp_edges` columns).
  - `episode_points(rows: list[dict]) -> list[tuple[datetime, Edge]]`: maps edge rows to `Edge(direction,
    best_edge=net_bps / 1e4, capacity_contracts, capacity_usd, avg_edge=net_bps / 1e4)` so `episodes.find` applies.
  - `episodes.find(..., min_edge: float = MIN_EDGE, annualize=None)`: `annualize(edges: list[Edge], end: datetime)
    -> float` replaces the payout formula when given; stock perps pass `min_edge=STOCK_PERP_MIN_NET_BPS / 1e4` and
    `annualize=lambda edges, end: median(e.avg_edge for e in edges) * 365 * 24 / STOCK_PERP_HOLD_H`.

- [ ] **Step 1: Write the failing tests**

```python
def test_basis_and_direction():
    assert sp.basis_bps(212.83, 212.81) == pytest.approx(0.94, abs=0.01)
    assert sp.direction(5.0) == "short_perp_long_stock" and sp.direction(-5.0) == "long_perp_short_stock"

def test_reference_older_than_max_age_is_absent():
    q = Quote("NVDA", 212.83, 212.85, 200, 300, None, NOW - timedelta(seconds=121))
    assert sp.reference(CTX, q, LABEL_SCHWAB, NOW).kind == "oracle"
    assert sp.reference(CTX, replace(q, quote_time=NOW - timedelta(seconds=119)), LABEL_SCHWAB, NOW).price == 212.84
    assert sp.reference(CTX, q, LABEL_NONE, NOW).kind == "oracle"

def test_costs_round_trip_and_hold():
    c = sp.costs_bps(taker=0.00009, spread_bps=1.0, funding_h=6.2e-6, borrow_bps_yr=50.0, hold_h=24, side="long_perp_short_stock")
    assert c["perp_fee"] == pytest.approx(1.8) and c["stock_spread"] == 1.0
    assert c["funding"] == pytest.approx(1.488) and c["borrow"] == pytest.approx(50 * 24 / (24 * 365))
    assert sp.costs_bps(taker=0.0009, spread_bps=None, funding_h=0, borrow_bps_yr=50, hold_h=24, side="short_perp_long_stock")["perp_fee"] == pytest.approx(18.0)

def test_funding_sign_by_side():
    kw = dict(taker=0, spread_bps=0, funding_h=1e-4, borrow_bps_yr=0, hold_h=1)
    assert sp.costs_bps(side="long_perp_short_stock", **kw)["funding"] == pytest.approx(1.0)
    assert sp.costs_bps(side="short_perp_long_stock", **kw)["funding"] == pytest.approx(-1.0)

def test_score_walks_perp_bids_and_caps_on_stock_size():
    # mark 215 vs Schwab mid 212.84 -> +101.5 bps; short perp: walk bids; stock asks 300 shares at 212.85 = $63,855
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_SCHWAB)
    assert e.direction == "short_perp_long_stock" and e.basis_bps == pytest.approx(101.5, abs=0.1)
    assert e.capacity_usd == pytest.approx(min(63_855.0, sum(b.price * b.size for b in BIDS)), rel=1e-6)
    assert e.net_bps == pytest.approx(e.basis_bps - e.perp_fee_bps - e.stock_spread_bps - e.funding_bps - e.borrow_bps)

def test_zero_stock_size_gives_zero_capacity():
    e = sp.score(replace(CTX, mark=215.0), BOOK, replace(REF_SCHWAB, ask_size=0.0))
    assert e.capacity_usd == 0.0 and e.capacity_contracts == 0.0 and e.net_bps > 10

def test_oracle_reference_has_no_capacity_or_spread():
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_ORACLE)
    assert e.stock_spread_bps is None and e.capacity_usd == 0.0 and e.ref_kind == "oracle"

def test_episodes_from_edge_rows_use_ten_bps_rule():
    rows = [sp.edge_row(NOW + timedelta(minutes=i), replace(E_OK, net_bps=12.0), "v") for i in range(3)]
    eps = episodes.find(sp.episode_points(rows), NOW + timedelta(days=1), kalshi_id="xyz:NVDA", pm_id="NVDA",
                        direction="short_perp_long_stock", min_edge=10 / 1e4, annualize=sp.annualize)
    assert len(eps) == 1 and eps[0].snapshots == 3 and eps[0].annualized == pytest.approx(12e-4 * 365)
    assert not episodes.find(sp.episode_points([{**rows[0], "net_bps": 9.9}, rows[1]]), NOW, kalshi_id="x", pm_id="y", direction="d", min_edge=1e-3, annualize=sp.annualize)

def test_find_default_behaviour_unchanged():  # tests/test_episodes.py: existing fixtures, no min_edge/annualize passed
```

- [ ] **Step 2: Run to verify they fail**, then implement `stock_perps.py`, the `episodes.find` parameters, and the
  `fees.py` constants. `annualize` is exported from `stock_perps` as `annualize(edges, end)`.

- [ ] **Step 3: Run `tests/test_stock_perps.py tests/test_episodes.py tests/test_fees.py -v`, ruff, commit**

```bash
git commit -am "Stock perps: basis, round-trip costs with a 24 h hold, stock-size cap, episode rule at 10 bps"
```

---

### Task 6: Scoring flow and the history backfill

**Files:**
- Create: `venue_arb/flows/stock_perp_score.py`, `venue_arb/flows/stock_perp_backfill.py`, `tests/test_stock_perp_flow.py`
- Modify: `venue_arb/flows/serve.py` (deployment `stock_perp_score`, cron `55 * * * *`), `venue_arb/flows/score.py:33-45`
  (`OPP_COLS` unchanged; `MERGE_OVERLAP`/`UPSERT` already carry `source`)

**Interfaces:**
- Consumes: Task 3 tables, Task 5 (`reference`, `score`, `edge_row`, `episode_points`, `annualize`),
  `flows.score.upsert_opportunities`, `flows.score.OPEN_START`, `scoring.opportunity_row(ep, source="stock_perp")`.
- Produces:
  - `PIPELINE = f"{PIPELINE_PREFIX}_stock_perp_score"`, `WINDOW_HOURS = 3`, `READBACK_CAP = timedelta(hours=26)`.
  - `score_window(ctx_rows, book_rows, ref_rows, labels, *, start, end) -> tuple[list[dict], list[Episode], dict]`
    (pure, tested): per symbol, per ctx snapshot `ts`, the book rows at that `ts` and the ref row at the same tick
    (`refs.ts == ctx.ts`; a missing ref → oracle); returns edge rows, episodes per (symbol, direction) via
    `episodes.find` with `close_time = end + timedelta(hours=STOCK_PERP_HOLD_H)`, and stats `{"symbols", "snapshots",
    "edge_rows", "rows_with_capacity", "episodes", "oracle_only_rows"}`.
  - `stock_perp_score_flow(window_hours=WINDOW_HOURS, end=None, pipeline=PIPELINE) -> dict`: reads the window (plus
    open-episode read-back, as `ladder_score_flow`), writes `stock_perp_edges`, upserts opportunities with
    `kalshi_id = symbol`, `pm_id = label.ref_symbol or symbol`, `source = "stock_perp"`, `us_executable = False`,
    `transfer_cost_usd = 0.0`; qa: `expect_schema`, `expect_rows` when snapshots exist, `sql.run_all(pattern=
    "stock_perp_*.sql")`, `opportunities_*.sql`.
  - `stock_perp_backfill` (CLI, run once at deploy, idempotent): for every labelled perp, `fetch_candles` and
    `fetch_funding` from `now - 60 d` into `stock_perp_candles` (ReplacingMergeTree, so re-runs rewrite); for every
    `schwab` label, `Client.price_history` into `stock_perp_refs` with `ts = quote_time = the session's 13:30 UTC`,
    `ref_kind = "schwab_daily"`, `bid = ask = open`, sizes 0. The weekend tier reads `p_open_reg` from those rows;
    the scorer ignores them (`score_window` keeps `ref_kind == "schwab"` only).

- [ ] **Step 1: Write the failing tests** for `score_window` (fixture rows built in the test as the ladder flow test
  does): two symbols × four ticks; one symbol with Schwab refs and a 12 bps net run of three ticks → one episode with
  `source`-ready fields; the other oracle-only → rows with `ref_kind == "oracle"` and no episode; a tick whose ref is
  missing falls back to oracle; `stats` equal to the literal dict. And `test_serve_lists_stock_perp_deployment`
  (`"55 * * * *"` appears in `serve.py` beside `stock_perp_score`).

- [ ] **Step 2: Run to verify they fail**, then implement the flow (structure of `ladder_score_flow`: one symbol at a
  time, `del` the rows after insert), the backfill CLI, and the serve entry.

- [ ] **Step 3: Run the tests, ruff, commit**

```bash
git commit -am "Stock perps: hourly scoring flow (edges, episodes source stock_perp), candle and funding backfill"
```

---

### Task 7: The weekend tier

**Files:**
- Create: `venue_arb/weekends.py`, `venue_arb/flows/stock_perp_weekend.py`, `tests/test_weekends.py`
- Modify: `venue_arb/flows/serve.py` (deployment `stock_perp_weekend`, cron `0 15 * * 1`)

**Interfaces:**
- Consumes: `stock_perp_ctx` (mark per minute), `stock_perp_candles` (hourly close before the collector started),
  `stock_perp_refs` (`schwab` quotes, `schwab_daily` opens), Task 3's `stock_perp_weekends`.
- Produces:
  - `SAMPLES = {"fri": ("Fri", time(20, 0)), "sat": ("Sat", time(12, 0)), "sun": ("Sun", time(18, 0))}` in
    `America/New_York`; `OPEN_PRE = time(4, 0)`, `OPEN_REG = time(9, 30)` (Monday, ET); the KRX open is Monday
    09:00 `Asia/Seoul`, i.e. Sunday 20:00 ET outside DST shifts, computed in Seoul time.
  - `sample(points: list[tuple[datetime, float]], at: datetime, tol: timedelta = timedelta(minutes=5)) -> float |
    None`: the last mark at or before `at` within `tol`, else None.
  - `weekend_row(symbol: str, friday: date, marks: list[tuple[datetime, float]], refs: list[tuple[datetime, float]],
    label: Label) -> dict`: `{"symbol", "friday", "p_fri", "p_sat", "p_sun", "p_open_pre", "p_open_reg", "ref_kind"}`;
    `p_open_pre` = first `schwab` ref at or after Monday 04:00 ET (None for `ref_kind == "none"` labels; for KRX
    labels the first oracle print at or after Sunday 20:00 ET goes in `p_open_pre` and `ref_kind = "oracle_krx"`);
    `p_open_reg` = first `schwab` ref at or after Monday 09:30 ET, else the `schwab_daily` open of that Monday.
  - `summary(rows: list[dict]) -> dict`: `{"weekends": n, "names": k, "hit_rate_sun": share of rows with
    sign(p_sun − p_fri) == sign(p_open_reg − p_fri) over rows where both are non-null and p_open_reg != p_fri,
    "hit_rate_sat": same for p_sat, "contained_sun_median": median of (p_sun − p_fri)/(p_open_reg − p_fri) over rows
    with |p_open_reg − p_fri| / p_fri >= 10 bps (the denominator floor, stated), "n_sun": rows in that median,
    "enough": n_sun >= 20}` (the sign-test threshold is the builder's; 20 is the plan's default).
  - `stock_perp_weekend_flow(end=None)`: for the last completed weekend and any weekend in the last 60 days without
    a row (the backfill's candles give `p_fri/p_sat/p_sun` at hourly resolution: use the candle close at the sample
    hour when no minute marks exist), upsert into `stock_perp_weekends`.

- [ ] **Step 1: Write the failing tests**

```python
def test_sample_last_point_within_tolerance(): ...
def test_missing_sample_is_null_not_skipped():
    row = wk.weekend_row("xyz:NVDA", date(2026, 10, 16), MARKS_WITHOUT_SUNDAY, REFS, LABEL_SCHWAB)
    assert row["p_sun"] is None and row["p_fri"] == 213.0 and row["p_open_reg"] == 215.2
def test_open_prints_pre_and_regular(): ...  # p_open_pre from the 04:00 quote, p_open_reg from the 09:30 one
def test_krx_label_uses_sunday_evening_oracle(): ...
def test_summary_hit_rate_and_floor():
    s = wk.summary(ROWS)  # 25 rows; 20 agree in sign; 3 under the 10 bps floor
    assert s["hit_rate_sun"] == pytest.approx(20 / 25) and s["n_sun"] == 22 and s["enough"]
```

- [ ] **Step 2: Run to verify they fail**, then implement `weekends.py` (pure) and the flow (`zoneinfo`, no new deps).

- [ ] **Step 3: Run the tests, ruff, commit**

```bash
git commit -am "Stock perps: weekend tier (Friday/Saturday/Sunday perp vs Monday open), Monday flow"
```

---

### Task 8: Bundle rows, verdicts and the publisher

**Files:**
- Modify: `venue_arb/bundle.py` (models and a new row builder), `venue_arb/publish.py` (readers, context, history),
  `dashboard/api/store.py:24` (`COLS` gains `basis_bps`, `net_bps`), `tests/test_bundle.py`, `tests/test_publish.py`,
  `tests/fixtures/bundle/stock_perp.json`

**Interfaces:**
- Consumes: Task 5 (`reference`, `score`, `StockPerpEdge`), Task 1 (`Label`, `PerpCtx`, `PerpLevel`), Task 2 `Quote`.
- Produces (bundle models, all additive with defaults so old `live.json` still validates):
  - `Pair.kind` Literal gains `"stock_perp"`; `Pair.settles_at: datetime | None`; `Leg.side` Literal gains `"long",
    "short"`; `Leg.url: str | None`; `Check.name` Literal gains `"reference", "fees_carry"`.
  - New optional `Pair` fields: `basis_bps: float | None = None`, `net_bps: float | None = None`, `funding_bps_h:
    float | None = None`, `next_funding_at: datetime | None = None`, `reference: dict | None = None`
    (`{"kind": "schwab" | "oracle", "symbol": str, "asof": iso}`), `trade: str | None = None`
    ("short xyz:NVDA / long NVDA"), `hold_hours: int | None = None`, `sensitivity: dict | None = None`
    (`{"net_bps_1h", "net_bps_7d", "net_bps_borrow_x10"}`).
  - `Kpis.stock_perps: dict | None = None` (`{"names_live": int, "episodes_7d": int}`).
  - `stock_perp_rows(perps: list[tuple[PerpCtx, Label, Snap_perp, Quote | None]], sparks: dict, now: datetime) ->
    list[Pair]` where `Snap_perp = tuple[datetime, list[PerpLevel]]`. One row per name, `key = f"{symbol}~{ref_symbol
    or symbol}~{direction}"`, `category = "stock_perps"`, `title = f"{label.name} ({label.exchange})"`, legs
    `[Leg(venue="hyperliquid", market_id=symbol, side=perp side, title=symbol, url=f"https://app.hyperliquid.xyz/trade/{symbol}")
    , Leg(venue=label.exchange.lower(), market_id=ref_symbol or symbol, side=stock side, title=label.name, url=None)]`,
    `net_edge_cents = net_bps / 100` (bps of notional as ¢ per $100 for the unit switch) and `net_edge_bps = net_bps`,
    `net_edge_roi = net_bps / 1e4`, `fees_cents = (perp_fee + stock_spread) / 100`, `capacity_*` from the edge, `books
    = None`, `fee_params = {"perp": {"taker": rate, "growth_mode": bool}, "stock": {"borrow_bps_yr": 50.0}}`,
    `us_executable = False`, `next_funding_at` = the next top of the hour after `now`.
  - Verdict labels (verbatim; `Executable edge` keeps the Scanner's example-row and tone logic):
    `"Executable edge · +14 bps · $12,000 at top of book"` (format `f"Executable edge · {net:+.0f} bps · ${cap:,.0f} at top of book"`),
    `"Basis · within costs · no edge"`, `"Reference closed · oracle only"`, and the existing
    `"Stale · a leg has no snapshot in 5 min"`. Checks in order: `reference` (ok "Schwab quote 14 s old" / partial
    "oracle only: stock market closed or no quote"), `fees_carry` (ok/partial/fail with detail
    `f"net {net:+.1f} bps after 2× taker, spread, {hold} h funding and borrow"`), `fees_depth` (capacity vs $100),
    `jurisdiction` (partial, "anywhere: perp leg open · US: Hyperliquid blocks US traders").
  - Publisher: `default_readers` gains `perps()` → `list[tuple[PerpCtx, Label, Snap_perp, Quote | None]]` from the
    latest `stock_perp_ctx` row per symbol within `LIVE_MAX_AGE_S`, its `stock_perp_books` rows at that `ts`, and the
    latest `schwab` ref per symbol; `tick` appends `bundle.stock_perp_rows(...)` only while
    `config.DASHBOARD_SHOW_STOCK_PERPS`; `load_context` reads `STOCK_PERP_EPISODES_SQL` (`source = 'stock_perp'`,
    last 7 d) into `kpi_inputs["stock_perps"]` and **`EPISODES_SQL` and `scope_episodes` exclude
    `source = 'stock_perp'`**; `SPARKS_SQL` gets a union over `stock_perp_edges` (hourly max `net_bps / 100` per key);
    `history_table` gains nullable columns `basis_bps`, `net_bps` (`HISTORY_SCHEMA` extended; pair rows null) and
    `write_history_hour` reads `stock_perp_edges` for the hour; `LIVE_BYTES_BUDGET` → `1_200_000` with the comment
    corrected to the measured sizes (868 KB at 399 pair rows on 2026-10-22, before stock perps).
  - Store: `COLS` gains the two columns; `union_by_name` already covers old files.

- [ ] **Step 1: Write the failing tests**

```python
def test_stock_perp_row_executable(fxp):  # NVDA mark 215 vs Schwab 212.84, growth fee, 300 shares displayed
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, (NOW, BOOK), QUOTE)], {}, NOW)
    assert p.kind == "stock_perp" and p.category == "stock_perps" and p.settles_at is None and p.books is None
    assert p.trade == "short xyz:NVDA / long NVDA" and p.reference["kind"] == "schwab" and p.hold_hours == 24
    assert p.verdict.label.startswith("Executable edge · +") and p.verdict.checks[0].name == "reference"
    assert p.net_edge_bps == pytest.approx(p.net_bps) and p.net_edge_cents == pytest.approx(p.net_bps / 100)
    assert p.us_executable is False and p.legs[0].url == "https://app.hyperliquid.xyz/trade/xyz:NVDA" and p.legs[1].url is None

def test_stock_perp_row_oracle_only_never_executable(): ...  # label none, or quote 121 s old: label "Reference closed · oracle only", capacity 0
def test_stock_perp_row_within_costs(): ...  # mark 212.9: "Basis · within costs · no edge"
def test_stock_perp_row_stale(): ...  # ctx 301 s old: the stale label, net None
def test_old_live_json_without_new_fields_still_validates(): LiveBundle.model_validate_json(SAMPLE_V2_TEXT)

# tests/test_publish.py
def test_tick_skips_stock_perps_while_flag_off(monkeypatch): ...  # readers["perps"] not called; pairs unchanged
def test_tick_adds_stock_perp_rows_when_flag_on(monkeypatch): ...  # category "stock_perps" present; kpis.stock_perps == {"names_live": 1, "episodes_7d": 0}
def test_episodes_sql_and_scope_exclude_stock_perp():
    assert "source <> 'stock_perp'" in publish.EPISODES_SQL
    assert publish.scope_episodes([{"source": "stock_perp", "kalshi_id": "x", "pm_id": "y"}], {}) == []
def test_history_table_has_basis_columns_null_for_pairs(): ...
```

- [ ] **Step 2: Run to verify they fail**, then implement. `stock_perp_rows` lives in `bundle.py` beside
  `ladder_pair_rows`; the verdict for stock perps is a separate `stock_perp_verdict(edge, ref, live, now) -> Verdict`
  (the existing `verdict` is untouched).

- [ ] **Step 3: Run `tests/test_bundle.py tests/test_publish.py tests/test_dashboard_api.py tests/test_sample.py`, ruff, commit**

```bash
git commit -am "Stock perps: bundle rows and verdicts, publisher readers, episodes scoped apart, history columns"
```

---

### Task 9: Research block

**Files:**
- Modify: `venue_arb/bundle.py` (`StockPerpResearch`, `ResearchSummary.stock_perps`), `venue_arb/report/copy.py`
  (`METHOD["stock_perps"]`, `HEADLINE["stock_perps"]`), `venue_arb/report/research.py` (`stock_perp_block`),
  `venue_arb/publish.py` (`compute_research` reads a `stock_perp_inputs` reader), `tests/test_research.py`,
  `tests/test_publish.py`

**Interfaces:**
- Produces:
  - `StockPerpResearch(_M)`: `names: int`, `snapshots: int`, `window_start: datetime | None`, `episodes: int`,
    `max_net_bps: float | None`, `share_hours_abs_ge_10bps: float | None`, `hourly: ResearchSeries` (max `net_bps`
    per hour, whole history), `by_name: list[dict]` (`{"symbol", "name", "max_net_bps", "hours_ge_10", "ref_kind"}`,
    top 10 by `max_net_bps`), `sensitivity: dict` (`{"episodes_1h", "episodes_7d", "episodes_borrow_x10"}`),
    `weekends: dict` (Task 7 `summary` plus `rows` of the last 8 weekends for the top 5 names), `headline: str`.
  - `ResearchSummary.stock_perps: StockPerpResearch | None = None`.
  - `copy.METHOD["stock_perps"]` (Claude drafts, Sam reviews): the trade.xyz oracle, Schwab reference during 4 AM to
    8 PM ET, oracle-only outside and for Asia names, the round-trip cost rule with the 24 h hold, the stock-size cap,
    the weekend test's three samples and Monday's open, and that Hyperliquid blocks US traders. `copy.HEADLINE
    ["stock_perps"]`: `"Across {names} stock perps and {snapshots} since {since}, the largest net basis after round-trip
    costs and a 24 h hold was {max_net}; {episodes} met the rule of at least 10 bps and $100 over two snapshots."`
  - `research.stock_perp_block(inputs: dict, computed_at) -> StockPerpResearch` where `inputs` is the publisher's
    reader output: `{"hourly": [(hour, max_net_bps)], "names": int, "snapshots": int, "window_start", "episodes":
    int, "share_hours": float | None, "by_name": [...], "sensitivity": {...}, "weekend_rows": [...]}`.
  - Publisher reader `stock_perp_inputs()`: ClickHouse-side aggregates (`SELECT toStartOfHour(ts), max(net_bps) ...
    GROUP BY 1`, `countIf(abs(basis_bps) >= 10) / count()` over session hours where `ref_kind = 'schwab'`, per-name
    maxima; sensitivities recomputed from `stock_perp_edges` columns: `net_bps + funding_bps + borrow_bps − (funding and
    borrow at 1 h / 168 h / borrow ×10)` and re-run through `episodes.find` per (symbol, direction) in Python) and the
    `stock_perp_weekends` rows from Postgres.

- [ ] **Step 1: Write the failing tests**: `test_stock_perp_block_headline_and_series` (literal inputs → headline
  text contains "3 stock perps", "12.4 bps", "1 episode"; `hourly.values` rounded to 2 dp; `by_name` sorted),
  `test_research_summary_without_stock_block_still_validates`, `test_compute_research_attaches_stock_block_when_flag_on`,
  `test_method_has_five_paragraphs` (`set(copy.METHOD) == {"matching", "edge", "crypto", "not_shown", "stock_perps"}`
  and `note.render` output unchanged: it uses only `edge` and `crypto`).

- [ ] **Step 2: Run to verify they fail**, then implement; `compute_research` builds the block only while the flag is
  on, in the same hourly slot, and its seconds count toward `research_seconds`.

- [ ] **Step 3: Run `tests/test_research.py tests/test_publish.py tests/test_note.py`, ruff, commit**

```bash
git commit -am "Stock perps: research block (spread and weekend cards), method copy, hourly reader"
```

---

### Task 10: Dashboard: Scanner, Explorer, Research

**Files:**
- Modify: `dashboard/static/{index.html,common.js,scanner.js,explorer.js,research.js,styles.css}`,
  `dashboard/sample/make_sample.py`, `tests/test_dashboard_static.py`, `tests/test_sample.py`
- Regenerate: `dashboard/sample/live.json`, `dashboard/sample/history/*.parquet` (deterministic)

**Interfaces:**
- Consumes: the bundle fields of Tasks 8 and 9.
- Produces (binding copy, added to `tests/test_dashboard_static.py` tuples):
  - `common.js`: `VERDICT_TONES` gains `["Basis · within costs · no edge", "none"]`, `["Reference closed · oracle only",
    "stale"]`; `legsTxt` renders stock-perp legs as `"short perp / long stock"` from `p.trade` when `p.kind ===
    "stock_perp"`; `safeUrl` allows `https://app.hyperliquid.xyz/`; `NOT_ADVICE = "Not advice. Hyperliquid blocks US
    traders; the perp leg is shown for reference."`; `FOOT_STOCK = "{n} stock perps — basis in bps of notional,
    net of 2× perp taker fee, stock spread, 24 h funding and borrow — reference and its age on every row"`.
  - Scanner: when `S.cat === "stock_perps"` the table header switches to *Name · Trade · Basis · Net · Funding ·
    Capacity · Reference · Verdict* (`renderScanner` picks a header template by category; the sort keys `edge` → `net_bps`,
    `cap`, `funding` → `funding_bps_h`); the Reference cell is `"{kind} {age}s"` or `"oracle only"`; the unit switch
    shows bps for this category and the ¢ button is disabled with title "basis is quoted in bps"; the footer uses
    `FOOT_STOCK` and `NOT_ADVICE`. Under "All" the stock-perp rows sort with the others by `net_edge_cents`.
  - Explorer: for `kind === "stock_perp"` the detail pane shows the trade line, a cost table (gross basis, 2× perp
    taker, stock spread, funding over the hold, borrow, net), the sensitivity line (`net at 1 h / 7 d / borrow ×10`),
    the reference and its age, and the chart draws `net_bps` and `basis_bps` from `/api/pair/{key}/history` with the
    reference switch bands (an annotation `kind: "reference"` is **not** added; the client derives bands from the
    history rows' `basis_bps` nulls) — keep it to a shaded band where `net_bps` is null; no size box (`/api/walk` is
    409 anyway).
  - Research: two cards after the ladder card, rendered only when `r.stock_perps` exists: *Stock-perp spread* (figure
    `episodes`, qualifier `max net {max_net_bps} bps; {share}% of session hours over 10 bps; {names} names since
    {window_start}`, chart `hourly` with a 10 bps dashed line) and *Weekends* (figure `hit_rate_sun` as %, qualifier
    `{n_sun} name-weekends; perp at Sunday 6 PM ET vs Monday's open; {enough ? "" : "too few weekends to conclude"}`,
    chart: contained share per weekend, dots per name). `METHOD` list gains `["stock_perps", "Stock perps"]`.
  - Sample: three stock-perp rows (executable, within costs, oracle-only) and a research `stock_perps` block built
    through `research.stock_perp_block`; `make_sample.py` keeps the flag-off behaviour: with
    `DASHBOARD_SHOW_STOCK_PERPS` False the sample has no stock-perp rows (so the page tests both states: the static
    test loads the sample with the flag monkeypatched on through a second generated file `dashboard/sample/live-stock.json`
    — committed, deterministic).

- [ ] **Step 1: Extend the static test** with the new verdicts, copy strings, the hyperliquid URL allow-list entry,
  `test_scanner_has_stock_header_template`, `test_research_has_stock_cards_copy`, and in `tests/test_sample.py`
  `test_sample_stock_covers_three_states` (reads `live-stock.json`: kinds include `stock_perp`, the three labels,
  `research.stock_perps` present).

- [ ] **Step 2: Run to verify they fail**, implement the JS and CSS, regenerate both samples
  (`uv run python dashboard/sample/make_sample.py`), and walk the page in the browser against the sample server
  (`BUNDLE_DIR=dashboard/sample uv run uvicorn dashboard.api.app:app --port 8791`, with a copy of `live-stock.json`
  as `live.json` in a temp dir for the on-state): the three tabs, the Stock perps chip, a row click into the
  Explorer, the two Research cards, light and dark, 375 px wide.

- [ ] **Step 3: Run the full suite, ruff, commit**

```bash
git add dashboard tests
git commit -m "Stock perps: Scanner category with trade, funding and reference columns; Explorer costs; Research cards; samples"
```

---

### Task 11: Docs, terms and the deploy runbook

**Files:**
- Modify: `docs/venues.md` (Hyperliquid / trade.xyz section: info endpoint, weight budget, fee tiers dated
  2026-10-22, growth mode, oracle sessions, US block, terms unread with the 403 noted), `docs/going-public.md`
  (checklist lines: terms reads for Hyperliquid, Schwab; `DASHBOARD_SHOW_STOCK_PERPS` stays off until then),
  `README.md` (pipeline overview, the stock-perps tier, the weekly Schwab login, the two new flows and the unit),
  `deploy/server/README.md` (no server change; note the history columns), the spec's §8.

- [ ] **Step 1: Write the docs**; `tests/test_dashboard_static.py` and `tests/test_deploy.py` already pin the code
  side. Add `test_readme_mentions_perps_unit_and_login` (README contains `venue-arb-perps` and
  `python -m venue_arb.sources.schwab login`).

- [ ] **Step 2: Run the full suite, ruff, commit**

```bash
git commit -am "Stock perps: venue notes (Hyperliquid, Schwab), runbooks, going-public checklist"
```

## Deploy (after the whole-branch review; Sam approves the merge)

1. OptiPlex: fast-forward main; `uv sync`; apply migrations (`secrets uv run python -m db.migrate`); install and
   enable `venue-arb-perps.service` (copy the real env block from the box's collector unit, as the other units);
   Sam runs `secrets uv run python -m venue_arb.sources.schwab login` once (needs a write-capable `BWS_ACCESS_TOKEN`
   in his shell); `secrets uv run python -m venue_arb.flows.stock_perp_backfill`; restart `venue-arb-flows` (serve
   picks up the two deployments); run `stock_perp_score` and `stock_perp_weekend` once by hand and check the qa rows.
2. Leave `DASHBOARD_SHOW_STOCK_PERPS = False` until Sam has read the Hyperliquid and Schwab terms; the collector and
   flows run meanwhile. Flipping it is a one-line commit plus `systemctl --user restart venue-arb-publish` and the
   VM re-copy (server validates with the new models: deploy the VM first, as in Plan 5).
3. Sam reviews `copy.METHOD["stock_perps"]` and `copy.HEADLINE["stock_perps"]`; wording fixes in `copy.py` only.
