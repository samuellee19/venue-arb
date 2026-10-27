# Pre-IPO Kind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give pre-IPO perps an explicit label kind so one field decides their behaviour everywhere, show them behind a "Pre-IPO" chip, never score them, and key stock-perp rows on the perp symbol alone so a listing keeps the row.

**Architecture:** `Label` gains `kind` (`equity` | `pre_ipo`), read from a new CSV column and validated by `load_labels`. Every consumer that today infers from an empty exchange switches on `kind` instead: `sessions.state` returns `pre_ipo`, the scorer skips the name, the weekend tier and the private oracle check exclude it, `bundle.stock_perp_rows` emits a `pre_ipo` category row with its own verdict, and the client renders the chip, the row and the Explorer note. `bundle.stock_key` drops the stock symbol; the publisher's history keys follow and the pushed history is rewritten once after deploy.

**Tech Stack:** Python 3.14, pydantic models in `venue_arb/bundle.py`, pytest; vanilla ES modules under `dashboard/static/`; labels in `labels/stock_perps.csv`; the OptiPlex CI worktree (`~/projects/venue-arb-ci`) for the authoritative test run (the Mac venv sits in iCloud and can stall).

**Spec:** `docs/superpowers/specs/2026-10-27-venue-arb-pre-ipo-kind-design.md`

## Global Constraints

- Public verdict for the new kind, verbatim and pinned: `Pre-IPO · no listed market`.
- Category slug `pre_ipo`, display name `Pre-IPO` (in `taxonomy.DISPLAY`, which `test_display_covers_every_category` guards).
- Session state literal `pre_ipo` beside `external` and `internal`.
- Stock-perp key format for every stock-perp row: `{perp}~stock_perp` (two parts, `PAIR_KEY_SEP` = `~`).
- A `pre_ipo` label has `ref_kind = none`, no `ref_symbol`, no `exchange`; an `equity` label has an `exchange`; `load_labels` rejects anything else, naming the row.
- No Schwab text or figure on the page (`test_no_schwab_on_the_page` keeps running); no mid-dot in JS outside the verdict labels; JS reads only model fields (`test_js_reads_only_model_fields`).
- No database migration; nothing stores the pair key.
- Labels are manual: the builder never writes an `equity` row for a pre-IPO name that has listed; it stays excluded and is reported (spec §3).
- Commits without a `Co-Authored-By` trailer; CI = `uv run ruff check .` and `uv run pytest -m "not integration" -q` on the OptiPlex worktree before every push.

## Review Focus

1. A `pre_ipo` name whose context ticks come with a stray Schwab ref row (a label edited from `schwab` to `pre_ipo` while the collector still buffered a quote): the scorer must still skip it. Pinned in Task 3.
2. A symbol missing from the labels (`label_or_none`): must stay `equity` with an empty exchange, so unlabelled names behave exactly as today. Pinned in Task 1.
3. A `pre_ipo` row in the All view: its verdict carries no edge, its net is null, so it sorts last with the stale rows and never trips the "example row" logic. Pinned in Task 7 (static guard) and Task 5 (null net).
4. The history Parquet written after this change must carry only two-part stock keys, including the rows of a name that had a reference symbol, otherwise the Explorer's history lookups miss. Pinned in Task 6.
5. The weekend tier with a `pre_ipo` label and a US-shaped exchange left by mistake is impossible (validation), but the weekend row builder must still return `ref_kind none` for a `pre_ipo` label if handed one. Pinned in Task 4.

---

### Task 1: The `kind` field, the loader and the label files

**Files:**
- Modify: `venue_arb/sources/hyperliquid.py:44-51` (`Label`), `:140-150` (`load_labels`), `:153-154` (`label_or_none`)
- Modify: `labels/stock_perps.csv` (new column on every row; the six pre-IPO rows), `labels/stock_perps_excluded.txt` (SPCX, QNT, CBRS removed if still unlisted)
- Test: `tests/test_hyperliquid.py`

**Interfaces:**
- Produces: `Label.kind: Literal["equity", "pre_ipo"] = "equity"` as the last field, so positional construction in existing tests keeps working. `load_labels(path) -> dict[str, Label]` reads the `kind` column and raises `ValueError(f"{path}: bad label row {perp!r} (...)")` for: `kind` not in the two values; `pre_ipo` with a `ref_symbol` or an `exchange` or `ref_kind != "none"`; `equity` with an empty `exchange`. `label_or_none(symbol, labels)` returns `kind="equity"` for an unknown symbol.

- [ ] **Step 1: Write the failing tests**

```python
def test_load_labels_reads_kind_and_validates_it(tmp_path):
    p = tmp_path / "l.csv"
    head = "perp,ref_kind,ref_symbol,exchange,currency,name,kind\n"
    p.write_text(head + "xyz:NVDA,schwab,NVDA,NASDAQ,USD,NVIDIA,equity\nxyz:ANT,none,,,USD,Anthropic (pre-IPO),pre_ipo\n")
    labels = load_labels(p)
    assert labels["xyz:NVDA"].kind == "equity" and labels["xyz:ANT"].kind == "pre_ipo"
    for bad in ("xyz:A,none,,NSE,INR,Jio,pre_ipo",        # pre_ipo with an exchange
                "xyz:B,schwab,B,NASDAQ,USD,B,pre_ipo",     # pre_ipo with a reference
                "xyz:C,none,,,CNY,YMTC,equity",            # equity without an exchange
                "xyz:D,none,,,USD,D,spac"):                # unknown kind
        p.write_text(head + bad + "\n")
        with pytest.raises(ValueError, match="bad label row"):
            load_labels(p)


def test_label_or_none_defaults_to_equity():
    assert label_or_none("xyz:NEW", {}).kind == "equity"


def test_labels_file_has_a_kind_on_every_row_and_six_pre_ipo_names():
    labels = load_labels()
    pre = sorted(s for s, lb in labels.items() if lb.kind == "pre_ipo")
    assert {"xyz:ANT", "xyz:YMTC", "xyz:JIO"} <= set(pre)
    assert all(lb.exchange for lb in labels.values() if lb.kind == "equity")
    assert all(lb.ref_kind == "none" and not lb.ref_symbol and not lb.exchange for lb in labels.values() if lb.kind == "pre_ipo")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hyperliquid.py -q -k "kind or defaults_to_equity or six_pre_ipo"`
Expected: FAIL (`Label` has no `kind`; the CSV has no `kind` column).

- [ ] **Step 3: Implement `Label.kind`, the loader rules and `label_or_none`'s default in `venue_arb/sources/hyperliquid.py`**

`kind: Literal["equity", "pre_ipo"] = "equity"` appended to `Label`. In `load_labels`, read `r["kind"]` (a missing column is a `KeyError` that surfaces as-is: the column is required) and apply the four rules from the Interfaces block in one condition that raises the existing `ValueError` text with `kind` added to the parenthesis.

- [ ] **Step 4: Rewrite the label files**

Add `,equity` to every existing row of `labels/stock_perps.csv` and a `kind` header. Rewrite three rows: `xyz:ANT,none,,,USD,Anthropic (pre-IPO),pre_ipo`; `xyz:YMTC,none,,,CNY,Yangtze Memory (pre-IPO),pre_ipo`; `xyz:JIO,none,,,INR,Reliance Jio (pre-IPO),pre_ipo`. For each of `xyz:SPCX`, `xyz:QNT`, `xyz:CBRS`: check the venue listing (`metaAndAssetCtxs` with `dex: "xyz"`, `isDelisted`) and the company's listing status on the web; if the company is still unlisted and the perp is listed, remove its line from `labels/stock_perps_excluded.txt` and add `xyz:<SYM>,none,,,USD,<Company> (pre-IPO),pre_ipo`; if the company has listed, leave the exclusion line and record the name, ticker and exchange in the task report for Sam's listing-day edit; if the perp is delisted, leave it excluded with its comment changed to `# delisted`.

- [ ] **Step 5: Run the test file and the suite**

Run: `uv run pytest tests/test_hyperliquid.py -q` then `uv run pytest -q -m "not integration"`
Expected: all pass (existing positional `Label(...)` constructions default to `equity`).

- [ ] **Step 6: Commit**

```bash
git add venue_arb/sources/hyperliquid.py labels/stock_perps.csv labels/stock_perps_excluded.txt tests/test_hyperliquid.py
git commit -m "Labels: a kind column (equity | pre_ipo), validated by load_labels; ANT, YMTC, JIO and the unlisted names among SPCX/QNT/CBRS as pre_ipo"
```

---

### Task 2: `sessions.state` knows `pre_ipo`

**Files:**
- Modify: `venue_arb/sessions.py:54-66`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `Label.kind` (Task 1).
- Produces: `state(label, ts) -> Literal["external", "internal", "pre_ipo"]`; `external(label, ts)` is `False` for a `pre_ipo` label at any time.

- [ ] **Step 1: Write the failing test**

```python
def test_pre_ipo_is_its_own_state_at_any_time():
    ant = Label("xyz:ANT", "none", None, "", "USD", "Anthropic (pre-IPO)", "pre_ipo")
    for ts in (datetime(2026, 10, 21, 15, 0, tzinfo=UTC), datetime(2026, 10, 24, 15, 0, tzinfo=UTC)):  # a Wednesday, a Saturday
        assert sessions.state(ant, ts) == "pre_ipo" and sessions.external(ant, ts) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_sessions.py -q -k pre_ipo`
Expected: FAIL (`state` returns `internal`).

- [ ] **Step 3: Implement**

`state` returns `"pre_ipo"` when `label.kind == "pre_ipo"` before consulting `external`; widen the `Literal`. `external` returns `False` for that kind first.

- [ ] **Step 4: Run the test file**

Run: `uv run pytest tests/test_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add venue_arb/sessions.py tests/test_sessions.py
git commit -m "Sessions: pre_ipo state for pre-IPO labels, never external"
```

---

### Task 3: The scorer skips pre-IPO names

**Files:**
- Modify: `venue_arb/stock_perp_window.py:40-56` (`score_window`), `venue_arb/flows/stock_perp_score.py:72-84` (stats keys)
- Test: `tests/test_stock_perp_flow.py`

**Interfaces:**
- Consumes: `label_or_none(...).kind`, `sessions.state` (Task 2).
- Produces: `score_window(...)` writes no edge row and no episode for a `pre_ipo` label and counts it once per symbol in the returned stats under `symbols_pre_ipo`; the flow's `stats` dict gains the key `symbols_pre_ipo` initialised to 0.

- [ ] **Step 1: Write the failing test**

```python
def test_pre_ipo_names_are_never_scored_even_with_a_stray_quote():
    ant = Label("xyz:ANT", "none", None, "", "USD", "Anthropic (pre-IPO)", "pre_ipo")
    ctxs = [ctx("xyz:ANT", t, 115.0, 115.0) for t in TICKS]
    books = [r for t in TICKS for r in book("xyz:ANT", t, 114.9, 115.1)]
    refs = [dict(ref("xyz:ANT", t, 114.0, 114.2), ref_symbol="ANT") for t in TICKS]  # a buffered quote from an old label
    rows, eps, st = score_window(ctxs, books, refs, {"xyz:ANT": ant}, start=START, end=END)
    assert rows == [] and eps == [] and st["symbols_pre_ipo"] == 1 and st["snapshots"] == len(TICKS)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_stock_perp_flow.py -q -k pre_ipo`
Expected: FAIL (rows are scored; no `symbols_pre_ipo` key).

- [ ] **Step 3: Implement the skip in `score_window` and the stats key**

In the tick loop, after `snapshots += 1`, look the label up once (`label_or_none`) and `continue` when its kind is `pre_ipo`, recording the symbol in a set that becomes `symbols_pre_ipo` in the stats; the existing `oracle_only_rows`, `oracle_rows` and `internal_rows` counters are untouched. In the flow, add `"symbols_pre_ipo": 0` to `stats` so the summing loop (`stats[key] += v`) finds it.

- [ ] **Step 4: Run the test file and the suite**

Run: `uv run pytest tests/test_stock_perp_flow.py -q` then `uv run pytest -q -m "not integration"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add venue_arb/stock_perp_window.py venue_arb/flows/stock_perp_score.py tests/test_stock_perp_flow.py
git commit -m "Scorer: pre-IPO names are skipped before the walk, counted as symbols_pre_ipo"
```

---

### Task 4: Weekend tier and the private oracle check exclude the kind

**Files:**
- Modify: `venue_arb/weekends.py:46-67` (`weekend_row`), `venue_arb/flows/stock_perp_weekend.py:60` (the `todo` list), `venue_arb/validation.py:47-50` (`compare`)
- Test: `tests/test_weekends.py`, `tests/test_validation.py`

**Interfaces:**
- Consumes: `Label.kind`.
- Produces: `weekend_row(symbol, friday, marks, refs, label)` returns a row with `ref_kind == "none"` and both opens `None` for a `pre_ipo` label; the weekend flow's `todo` excludes `pre_ipo` symbols (`labels[s].kind != "pre_ipo"`), so no row is written for them; `validation.compare` skips `pre_ipo` labels whatever their exchange.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_weekends.py
def test_pre_ipo_label_gets_no_opens_and_ref_kind_none():
    ant = Label("xyz:ANT", "none", None, "", "USD", "Anthropic (pre-IPO)", "pre_ipo")
    row = weekends.weekend_row("xyz:ANT", FRIDAY, MARKS, REFS, ant)  # reuse the module's fixtures for a US name
    assert row["ref_kind"] == "none" and row["p_open_pre"] is None and row["p_open_reg"] is None


def test_weekend_flow_skips_pre_ipo_symbols():
    text = Path("venue_arb/flows/stock_perp_weekend.py").read_text()
    assert 'kind != "pre_ipo"' in text  # the flow imports the platform stack; pin the filter in source

# tests/test_validation.py
def test_compare_skips_pre_ipo_labels_even_with_a_us_exchange():
    ant = Label("xyz:ANT", "none", None, "NASDAQ", "USD", "Anthropic (pre-IPO)", "pre_ipo")  # unreachable via the CSV, defended anyway
    ticks = [tick(0, sym="xyz:ANT")]
    assert v.compare([c for c, _ in ticks], [r for _, r in ticks], {"xyz:ANT": ant}) == []
```

(Use the fixture names the two test files already define for a Friday, marks and refs; if `tests/test_weekends.py` builds them inline, build the same inline.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_weekends.py tests/test_validation.py -q -k pre_ipo`
Expected: FAIL (the weekend row takes the US branch or the `none` branch with opens; `compare` scores the NASDAQ label; the flow has no filter).

- [ ] **Step 3: Implement**

`weekend_row`: first branch `if label.kind == "pre_ipo": row["ref_kind"] = "none"` with no opens. The flow: `todo = [s for s in labels if labels[s].kind != "pre_ipo" and (s, friday) not in done]`. `compare`: add `or label.kind == "pre_ipo"` to the skip condition.

- [ ] **Step 4: Run both test files**

Run: `uv run pytest tests/test_weekends.py tests/test_validation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add venue_arb/weekends.py venue_arb/flows/stock_perp_weekend.py venue_arb/validation.py tests/test_weekends.py tests/test_validation.py
git commit -m "Weekend tier and oracle check exclude pre-IPO labels by kind"
```

---

### Task 5: Bundle rows: the `pre_ipo` category, its verdict, and the two-part key

**Files:**
- Modify: `venue_arb/bundle.py:226-228` (`stock_key`), `:390-420` (`stock_perp_verdict`), `:432-480` (`stock_perp_rows`); `venue_arb/taxonomy.py:35` (`DISPLAY`)
- Test: `tests/test_bundle.py`, `tests/test_taxonomy.py`

**Interfaces:**
- Consumes: `sessions.state` (Task 2), `Label.kind`.
- Produces: `stock_key(perp: str) -> str` = `f"{perp}{PAIR_KEY_SEP}stock_perp"`. `stock_perp_verdict(edge, state, live, now, *, has_book=True, asof=None)` accepts `state == "pre_ipo"` and returns `Verdict(label="Pre-IPO · no listed market", checks=[reference partial "no listed share", fees_carry partial "not scored: pre-IPO", fees_depth partial "not scored: pre-IPO", jurisdiction as today])`. `stock_perp_rows` emits for a `pre_ipo` label: `category="pre_ipo"`, `kind="stock_perp"`, `key=stock_key(sym)`, `trade="perp only"`, `reference={"kind": "none", "state": "pre_ipo", "asof": ts.isoformat()}`, `legs[1]` = `Leg(venue="pre_ipo", market_id=sym, side="", title=label.name, url=None, settlement_source=None, snapshot_at=None)`, `net_bps=None`, `basis_bps=None`, `capacity_usd=0.0`, `costs=None`, `sensitivity=None`, `funding_bps_h` and `next_funding_at` as today. `taxonomy.DISPLAY["pre_ipo"] == "Pre-IPO"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bundle.py
def test_stock_key_is_the_perp_alone():
    assert stock_key("xyz:NVDA") == "xyz:NVDA~stock_perp"


def test_equity_rows_key_on_the_perp_even_with_a_reference_symbol():
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, SP_SNAP)], {}, SP_NOW)
    assert p.key == "xyz:NVDA~stock_perp" and p.legs[1].market_id == "NVDA"


def test_pre_ipo_row_is_informational():
    ant = Label("xyz:ANT", "none", None, "", "USD", "Anthropic (pre-IPO)", "pre_ipo")
    [p] = stock_perp_rows([(replace(CTX215, symbol="xyz:ANT"), ant, SP_SNAP)], {}, SP_NOW)
    assert p.category == "pre_ipo" and p.kind == "stock_perp" and p.key == "xyz:ANT~stock_perp"
    assert p.verdict.label == "Pre-IPO · no listed market"
    assert p.trade == "perp only" and p.reference == {"kind": "none", "state": "pre_ipo", "asof": SP_SNAP[0].isoformat()}
    assert p.net_bps is None and p.net_edge_cents is None and p.basis_bps is None and p.capacity_usd == 0.0
    assert p.costs is None and p.sensitivity is None and p.funding_bps_h is not None
    assert [c.status for c in p.verdict.checks[:3]] == ["partial", "partial", "partial"]
    assert p.legs[1].venue == "pre_ipo" and p.legs[1].title == "Anthropic (pre-IPO)"

# tests/test_taxonomy.py
def test_display_names_pre_ipo():
    assert DISPLAY["pre_ipo"] == "Pre-IPO"
```

Also update `test_stock_perp_row_label_none_and_spark` (line 313): its key assertions become `"xyz:FOO~stock_perp"` (and the sparks dict key it passes).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_bundle.py tests/test_taxonomy.py -q -k "stock_key or pre_ipo or key_on_the_perp or label_none"`
Expected: FAIL (`stock_key` takes two arguments; no `pre_ipo` branch; `DISPLAY` lacks the key).

- [ ] **Step 3: Implement**

`stock_key(perp)` per the Interfaces block; update its one caller in `stock_perp_rows`. In `stock_perp_rows`, when `label.kind == "pre_ipo"`: compute `state = "pre_ipo"` (Task 2 already returns it), skip `stock_perps.score` entirely, and build the `Pair` with the fields above; the verdict comes from `stock_perp_verdict(None, "pre_ipo", live, now, has_book=bool(levels), asof=ts)`, so give that function an `edge: StockPerpEdge | None` and return the pre-IPO verdict before any use of `edge`. Add `"pre_ipo": "Pre-IPO"` to `DISPLAY`.

- [ ] **Step 4: Run the test files and the suite**

Run: `uv run pytest tests/test_bundle.py tests/test_taxonomy.py -q` then `uv run pytest -q -m "not integration"`
Expected: PASS except `tests/test_publish.py` history-key assertions and `tests/test_sample.py`, which Task 6 fixes; note them in the task report.

- [ ] **Step 5: Commit**

```bash
git add venue_arb/bundle.py venue_arb/taxonomy.py tests/test_bundle.py tests/test_taxonomy.py
git commit -m "Bundle: pre_ipo category rows with their own verdict; stock-perp keys on the perp symbol alone"
```

---

### Task 6: Publisher keys, research exclusion, samples

**Files:**
- Modify: `venue_arb/publish.py:251-300` (`history_table`), `:380-383` (`stock_refs`), `:385-400` (`spark_series`), `:573-590` (`stock_perp_inputs`), the `load_context` call that passes `stock_refs()`
- Modify: `dashboard/sample/make_sample.py` (one pre-IPO row in `live-stock.json`), regenerate `dashboard/sample/*.json` and `dashboard/sample/history/*.parquet`
- Test: `tests/test_publish.py`, `tests/test_research.py`

**Interfaces:**
- Consumes: `bundle.stock_key(perp)` (Task 5).
- Produces: `history_table(..., stock_rows=..., refs=...)` keys stock rows as `stock_key(sym)`; `spark_series(rows, start)` drops its `refs` parameter and keys stock rows the same way; `stock_refs()` is deleted (its only remaining use was keys). `stock_perp_inputs(ch, conn, labels, ex)` excludes `pre_ipo` labels from `by_name` and from any count that is label-driven (edge-driven counts already exclude them, since Task 3 writes no rows).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_publish.py
def test_history_table_keys_stock_rows_on_the_perp():
    tbl = publish.history_table([], [], {}, None, stock_rows=[(T0, "xyz:NVDA", "short_perp_long_stock", 12.0, 11.0, 500.0)])
    assert tbl.column("key").to_pylist() == ["xyz:NVDA~stock_perp"]


def test_spark_series_keys_stock_rows_on_the_perp():
    rows = [("xyz:NVDA", "", "stock_perp", T0, 0.0011)]
    assert set(publish.spark_series(rows, T0)) == {"xyz:NVDA~stock_perp"}


def test_stock_refs_is_gone():
    assert not hasattr(publish, "stock_refs")

# tests/test_research.py (or test_publish.py if stock_perp_inputs is tested there)
def test_stock_perp_inputs_names_exclude_pre_ipo_labels():
    labels = {"xyz:NVDA": LABEL_NVDA, "xyz:ANT": Label("xyz:ANT", "none", None, "", "USD", "Anthropic (pre-IPO)", "pre_ipo")}
    inputs = publish.stock_perp_inputs(FakeCH(...), FakePG(...), labels, [])  # the file's existing fakes
    assert all(n["symbol"] != "xyz:ANT" for n in inputs["by_name"])
```

(Match the argument shapes the existing `history_table` and `spark_series` tests use; the row tuples above follow the SQL column orders documented at `publish.py:257` and `:95`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_publish.py tests/test_research.py -q -k "on_the_perp or stock_refs_is_gone or exclude_pre_ipo"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`history_table` and `spark_series` call `bundle.stock_key(sym)`; remove the `refs` parameter from `spark_series` and the `stock_refs()` argument at its call site in `load_context`; delete `stock_refs`. In `stock_perp_inputs`, filter `by_name` (and any label-driven count) with `label_or_none(sym, labels).kind != "pre_ipo"`.

- [ ] **Step 4: Regenerate the samples**

Add one pre-IPO row to `make_sample.py`'s stock bundle (a `Label(..., kind="pre_ipo")` for an invented name, e.g. `xyz:PREIPO`), run `uv run python dashboard/sample/make_sample.py`, and confirm `git diff --stat dashboard/sample` shows only key changes plus the new row (the generator is deterministic).

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS, including `tests/test_sample.py`.

- [ ] **Step 6: Commit**

```bash
git add venue_arb/publish.py dashboard/sample tests/test_publish.py tests/test_research.py
git commit -m "Publisher: history and spark keys on the perp symbol, stock_refs removed, research names exclude pre-IPO; samples regenerated with a pre-IPO row"
```

---

### Task 7: The client: chip, row, Explorer note, copy

**Files:**
- Modify: `dashboard/static/common.js:13-16` (`VERDICT_TONES`), new `FOOT_PRE_IPO` constant; `dashboard/static/scanner.js:9` (`keysFor`), `:69-78` (`stockRow`), `:93-99` (header choice), `:104-109` (footer); `dashboard/static/explorer.js` (detail for `p.category === "pre_ipo"`: the one-line note, no chart); `venue_arb/report/copy.py:39` (`METHOD["stock_perps"]`)
- Test: `tests/test_dashboard_static.py`

**Interfaces:**
- Consumes: `category == "pre_ipo"`, `reference.kind == "none"`, `trade == "perp only"`, the verdict label (Task 5).
- Produces: `VERDICT_TONES` gains `["Pre-IPO · no listed market", "none"]`; `FOOT_PRE_IPO = "{n} pre-IPO perps — perps on companies with no listed share: price history only, no reference, nothing to score"`; the Scanner uses the stock header for `S.cat === "stock_perps" || S.cat === "pre_ipo"`, and `keysFor("pre_ipo")` is `STOCK_KEYS`; a pre-IPO row's Reference cell reads `—`; the Explorer detail for a `pre_ipo` row shows the heading, the legs, the verdict and one `.note` reading `No listed share yet: nothing to score until it lists.` and no cost table, chart or check cards beyond the verdict; `copy.METHOD["stock_perps"]` gains the sentence `Pre-IPO perps are listed for their price only and are not scored until the company lists.`

- [ ] **Step 1: Write the failing tests**

```python
def test_pre_ipo_client_rules():
    common, scanner, explorer = _read("common.js"), _read("scanner.js"), _read("explorer.js")
    assert '"Pre-IPO · no listed market"' in common and "FOOT_PRE_IPO" in common
    assert 'cat === "pre_ipo"' in scanner and 'S.cat === "pre_ipo"' in scanner
    assert 'category === "pre_ipo"' in explorer and "No listed share yet: nothing to score until it lists." in explorer


def test_pre_ipo_sentence_in_method_copy():
    assert "Pre-IPO perps are listed for their price only and are not scored until the company lists." in copy.METHOD["stock_perps"]
```

Add `"Pre-IPO · no listed market"` to the file's `VERDICTS` tuple (so `test_js_carries_the_copy` and the mid-dot guard cover it) and `"nothing to score until it lists"` to `STOCK_COPY`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_dashboard_static.py -q -k "pre_ipo"`
Expected: FAIL.

- [ ] **Step 3: Implement the client and the copy**

Per the Interfaces block. In `stockRow`, `ref(p)` returns `"—"` when `p.reference?.kind === "none"`; Basis, Net and Capacity already print `—` for null. In `renderScanner`, the footer for the `pre_ipo` chip uses `FOOT_PRE_IPO` with the same singular/plural replacement as `FOOT_STOCK`, followed by `NOT_ADVICE`. In the Explorer, branch on `p.category === "pre_ipo"` before building the cost table and the chart.

- [ ] **Step 4: Render and check**

Serve `dashboard/sample/live-stock.json` locally (`BUNDLE_DIR=<dir with live.json + history/> PYTHONPATH=. uv run uvicorn dashboard.api.app:app --port 8771`, frozen to the sample's `computed_at` as `scratchpad/ux/serve.py` did, or any equivalent) and confirm with Playwright or a browser: the "Pre-IPO" chip appears with a count, its row shows `perp only`, `—` for Basis/Net/Capacity/Reference and the verdict; the Explorer shows the note and no chart; the All view lists the row last among unscored rows. Record the three screenshots' paths in the task report.

- [ ] **Step 5: Run the static tests and the suite**

Run: `uv run pytest tests/test_dashboard_static.py -q` then `uv run pytest -q -m "not integration"`
Expected: PASS (no `docs/venues.md`, no mid-dot outside verdicts, no Schwab, model fields only).

- [ ] **Step 6: Commit**

```bash
git add dashboard/static venue_arb/report/copy.py tests/test_dashboard_static.py
git commit -m "Client: Pre-IPO chip and row, Explorer note instead of a cost table, verdict tone and method sentence"
```

---

### Task 8: Docs and the deploy runbook

**Files:**
- Modify: `README.md` (stock-perps section: the `kind` column, the listing-day runbook of spec §7, the deploy steps below), `docs/venues.md` (exclusion-file meaning narrowed; pre-IPO names listed), `docs/going-public.md` (one line: pre-IPO rows are informational)
- Test: `tests/test_deploy.py` (README mentions), `tests/test_dashboard_static.py` (nothing new)

**Interfaces:** none.

- [ ] **Step 1: Write the failing test**

```python
def test_readme_documents_the_listing_day_edit():
    text = Path("README.md").read_text()
    assert "kind" in text and "listing day" in text.lower() and "publish history --hours 240" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_deploy.py -q -k listing_day`
Expected: FAIL.

- [ ] **Step 3: Write the docs**

README: the `kind` column and its rules; the listing-day runbook (spec §7 verbatim in substance: one row edit, one commit through CI, no restart, what starts when); the deploy steps for this change: (1) update the VM's server tree first (the client files), (2) fast-forward main on the OptiPlex, (3) `systemctl --user restart venue-arb-publish`, (4) `secrets uv run python -m venue_arb.publish history --hours 240` to rewrite the pushed history under the new keys, (5) check `/api/live` shows `"pre_ipo"` in `categories` and no three-part stock keys. `docs/venues.md`: the exclusion file is for indices, commodities, FX and delisted names; pre-IPO names are labels of kind `pre_ipo`. `docs/going-public.md`: pre-IPO rows show price and funding only.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/venues.md docs/going-public.md tests/test_deploy.py
git commit -m "Docs: the label kind, the listing-day runbook, the deploy steps for the key change"
```

---

### Finishing

- CI on the OptiPlex worktree, then integrate with `superpowers:finishing-a-development-branch` (the project's path: push `ci-<sha>`, merge `--ff-only` on the OptiPlex, delete the ref). Deploy only when Sam says so, in the README order from Task 8; the key change makes the history rewrite part of the deploy, not optional.
- Task 1's report names any of SPCX, QNT, CBRS that has listed, for Sam's listing-day edit.
