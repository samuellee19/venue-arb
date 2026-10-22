# venue-arb Plan 5: dashboard v2 (Scanner, Explorer, Research)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the v1 pair-explorer page into a three-tab product — a scannable table, the existing explorer, and a
research tab with live numbers — under the name "Prediction Market Arbitrage", in a light, designed visual system.

**Architecture:** One data source, `live.json`, gains a `research` section recomputed hourly by the publisher from the
note's own aggregation (`note.summarize` over `note.load`), reduced by a new pure module to the figures, three chart
series and the method copy the tab renders. The bundle's definitions (episodes, pairs, violations) are unified on the
note's. The page is rewritten as small ES modules: boot/tabs/header, scanner, explorer (today's detail code moved), and
research; the server is unchanged.

**Tech Stack:** as Plan 4 (pydantic bundle, publisher loop, FastAPI server, vanilla JS + vendored Plotly 4.1.1).

**Spec:** `docs/superpowers/specs/2026-10-21-venue-arb-dashboard-v2-design.md` (§7 holds the binding calls); v1 spec
`2026-10-20-venue-arb-dashboard-design.md` still applies where v2 is silent.

## Global Constraints
- Plan 4's Global Constraints hold (no inbound path home, no raw quote while `DASHBOARD_SHOW_BOOKS` is False, no CF
  Benchmarks/Binance series republished, pure modules never import `qa`, CI = `uv run ruff check .` +
  `env -u PYTHONPATH uv run pytest -m "not integration" -q`, no Co-Authored-By trailer, additive migrations — none here).
- One definition per number, the note's (spec §7.1): scope = exact pairs at the current prompt version without a
  wrong/unsure review; episodes from both sources, labelled by tier; ladder violations = `capacity_contracts > 0`;
  "pairs live" = distinct market pairs with both legs live.
- Name: wordmark `venue-arb`; H1 **Prediction Market Arbitrage**; description *Kalshi vs Polymarket: matched markets,
  executable edge after fees and depth, and why most gaps aren't arbitrage.*; `<title>` `venue-arb · Prediction Market
  Arbitrage`.
- Style (spec §3): Inter by name with the system stack fallback (nothing vendored); sizes 13/15/22 px, 26 for headline
  figures; 8-px spacing grid; light default, dark via toggle, both from CSS variables; one accent (`--k`); green/red
  only on signed numbers and the executable badge; no mid-dot separators in running text; monospace only in numeric
  cells and ids; no gradients, emoji or decorative icons.
- The Scanner and the Research tab render from `live.json` alone. Static test guarantees stay: vendored Plotly sha,
  no external URLs in any static file (repo and venue links set in JS from constants), verdict labels verbatim.
- `LIVE_BYTES_BUDGET = 800_000`; the research section adds ≤ 150 KB.
- Research recomputes inline after that tick's live write and push (never delays the current bundle); a failed
  computation keeps the previous summary; warn results `research_failed` and `research_seconds` (> 30 s).

## Review Focus
1. **No research yet** (first start, or the computation failed before ever succeeding): `bundle.research` is `None`;
   the tab shows "research summary not computed yet" and the Scanner is unaffected. Test in Task 6
   (`test_research_tab_copy_handles_missing_summary` — static assertion the copy string exists; browser walk with the
   sample's `research` set to null).
2. **A pair with both directions scored**: the Scanner must show one row — the better direction — and the other
   direction must still be reachable in the Explorer. Test in Task 5 (`scanner_rows` pure helper test:
   `test_scanner_one_row_per_pair_best_direction`).
3. **No executable row**: the example row renders, is not counted in chips or the pairs figure, is not clickable.
   Test in Task 5 (`test_example_row_only_when_no_executable` on the helper; the page's `data-example` row carries no
   click handler — checked in the browser walk).
4. **An episode with `end_ts` NULL (open)**: the scoped 7-day count must include it. Test in Task 1
   (`test_episodes_7d_counts_open_episode`, integration, and the unit test on the SQL window via a fixture row).
5. **Header figures stale**: `computed_at` older than 1 h greys the two header figures exactly as the v1 tiles did;
   `collector_last_snapshot_at` null keeps the books rule. Test in Task 5 (static copy + browser walk with an old
   sample `computed_at`).

---

### Task 1: Contract and definitions (bundle, publisher, note)

**Files:** Modify `venue_arb/bundle.py`, `venue_arb/publish.py:44-60,83-100`, `venue_arb/report/note.py:56-77`,
`venue_arb/taxonomy.py`; Test `tests/test_bundle.py`, `tests/test_publish.py`, `tests/test_note.py`,
`tests/test_taxonomy.py`.

**Interfaces:**
- Produces, in `venue_arb/bundle.py` (pydantic, `extra="forbid"`):
  ```python
  class Pair(_M): ...; net_edge_roi: float | None   # fraction: net_edge_cents / (100 - net_edge_cents); None with no edge
  class ResearchSeries(_M): ts: list[datetime]; values: list[float | None]
  class ResearchHist(_M): edges: list[float]; counts: list[int]           # len(edges) == len(counts) + 1, in ¢
  class ResearchLadderSeries(_M): ts: list[datetime]; checks: list[int]; violations: list[int]
  class ResearchSummary(_M):
      computed_at: datetime; window_start: datetime | None
      headline: list[str]                     # 2–3 sentences, numbers already formatted, publisher-owned
      pairs_eligible: int; pairs_reviewed: int; snapshots: int
      max_net_edge_cents: float | None
      episodes_pair: int; episodes_ladder: int; episodes_us: int
      episode_median_minutes: float | None; episode_median_capacity_usd: float | None
      screened_rows: int; screened_share_ge_1c: float | None; screened_share_early: float | None
      ladder_checks: int | None; ladder_violations: int; ladder_scored: int
      basis: dict[str, Basis]
      review: dict | None                     # {"precision": float, "labels": int, "asof": datetime} or None
      exec_hourly: ResearchSeries             # max net edge (¢) per hour over all scoped pairs, whole history
      screened_hist: ResearchHist             # net-gap histogram, bins −10¢..+10¢ by 1¢ (22 edges incl. open ends)
      ladder_hourly: ResearchLadderSeries     # admissible checks and violations per counted hour
      method: dict[str, str]                  # keys matching, edge, crypto, not_shown
      note_path: str                          # "reports/research-note-v1.md"
  class LiveBundle(_M): ...; categories: dict[str, str]; research: ResearchSummary | None
  ```
  `Kpis.pairs_live` becomes `{"exact": n, "near_exact": n, "ladder": n}` counted over **distinct market pairs**
  (`(k_id, pm_id)`), not direction rows; `Kpis.episodes_7d` counts scoped episodes of both sources whose
  `coalesce(end_ts, now()) > now() − 7 d`.
- `venue_arb/taxonomy.py`: `DISPLAY = {"fed_macro": "Fed & macro", "crypto_price": "Crypto"}`; `build_live` fills
  `categories` from it (plus any `pair.category` missing from DISPLAY, mapped to itself).
- `venue_arb/publish.py`: `EPISODES_SQL` gains the note's scope (`SCOPE_SQL`'s latest-exact-no-bad-review condition
  — reuse `note.SCOPE_SQL` as a CTE or filter in Python with the scope rows the context already loads) and the
  `coalesce(end_ts, now())` window; `LIVE_BYTES_BUDGET = 800_000`.
- `venue_arb/report/note.py`: `LADDER_SQL`'s `n_capacity_gt0` and `LADDER_HOURS_SQL`'s `pos` use
  `capacity_contracts > 0` (the comment on `_ladder` updated: "Positive = capacity_contracts > 0").

- [ ] **Step 1: Failing tests.** `tests/test_bundle.py`: `test_net_edge_roi` (0.5¢ → `0.005/0.995` ≈ 0.005025;
  None when edge None); `test_pairs_live_counts_pairs_not_directions` (the exact fixture yields `exact: 1`);
  `test_categories_from_taxonomy_display` (`b.categories == {"fed_macro": "Fed & macro", "crypto_price": "Crypto"}`
  for a bundle containing both); `test_research_optional` (`research=None` validates; a minimal `ResearchSummary`
  round-trips). `tests/test_taxonomy.py`: `DISPLAY` covers every `config.CATEGORIES`. `tests/test_note.py`: the two
  SQL constants contain `capacity_contracts > 0` and not `capacity_usd > 0`. `tests/test_publish.py`:
  `EPISODES_SQL` contains `coalesce(end_ts, now())`, the budget constant equals 800_000, and
  `test_episodes_7d_counts_open_episode`: `scope_episodes` keeps a pair-source row with `end_ts None` whose pair is in
  scope, drops one with a bad review, keeps a ladder-source row regardless.
- [ ] **Step 2: Run** `pytest tests/test_bundle.py tests/test_taxonomy.py tests/test_note.py tests/test_publish.py -q` — FAIL.
- [ ] **Step 3: Implement** per Interfaces. Episode scoping in the publisher: load scope rows with `note.SCOPE_SQL`
  in `load_context` (every `REFRESH_EVERY` ticks) and filter with a new pure helper
  `scope_episodes(rows: list[dict], scope: dict) -> list[dict]` in `venue_arb/publish.py`: pair-source rows kept when
  `(kalshi_id, pm_id)` is exact without a bad review; ladder-source rows always kept (the note scopes only the pair
  tier). `build_live` gains `research: ResearchSummary | None = None` and `categories` is filled inside it.
- [ ] **Step 4: Run** the four files — PASS; full CI PASS. On the OptiPlex (controller): `tests/test_publish_flow.py`
  integration still passes.
- [ ] **Step 5: Commit** — `git commit -m "bundle: ROI, pair counts, categories, research contract; publisher and note on one definition per number"`.

---

### Task 2: Method copy, note loader, research reducer (pure)

**Files:** Create `venue_arb/report/copy.py`, `venue_arb/report/research.py`; Modify `venue_arb/report/note.py:852-890`
(split `main` into `load(conn, ch) -> dict` + the existing render), `venue_arb/report/note.py:613+` (render imports
`copy.METHOD` for the method sentences it already prints — replace only equivalents); Test `tests/test_research.py`,
`tests/test_note.py`.

**Interfaces:**
- `copy.py`: `METHOD: dict[str, str]` with keys `matching`, `edge`, `crypto`, `not_shown` — four paragraphs, plain
  prose, no mid-dots, no slogans (drafted by the implementer; Sam reviews after the page renders). `HEADLINE`:
  three `str.format` templates: `question` ("Is there cross-venue arbitrage between Kalshi and Polymarket?"),
  `answer` (uses `pairs`, `snapshots`, `since`, `max_edge`, `episodes`, `us_episodes`), `ladders` (uses `checks`,
  `violations`). `NOT_COMPUTED = "research summary not computed yet"`.
- `note.load(conn, ch) -> dict` returns exactly the keyword inputs `summarize` takes (`pairs`, `screened_rows`,
  `edge_rows`, `episodes`, `adjudications`, `scope`, `ladder_rows`, `ladder_episodes`, `basis_rows`, `admissible`,
  `ladder_hours`); `main` becomes `s = summarize(**load(conn, ch), generated_at=now); render(s, out_dir)`.
- `research.py`: `from_summary(s: dict, *, review: dict | None, computed_at: datetime) -> ResearchSummary` — pure;
  `s` is `summarize`'s output. Derivations: `exec_hourly` = per clock hour, max over `s["executable"]["series"]`
  values (every pair, both directions already maxed), whole history; `screened_hist` from `s["screened"]["values"]`
  with `edges = [-inf, -0.10, -0.09, …, 0.10, +inf]` expressed as cents and the two open ends as ±11; `ladder_hourly`
  from `s["ladder"]` — needs the per-hour rows: `summarize` already receives `admissible` and `ladder_hours`; expose
  them by adding `"hours": [{"hour", "checks", "violations"}]` to `_ladder`'s return (counted hours only, violations =
  `pos` for that hour). `headline` = `HEADLINE` templates filled with formatted numbers (¢ to one decimal, counts
  with thousands separators, dates as "19 Oct"). `review` passed through. `episodes_us` = `s["jurisdiction"]["us_episodes"]`.
  `method = copy.METHOD`; `note_path = "reports/research-note-v1.md"`.

- [ ] **Step 1: Failing tests** `tests/test_research.py` on a summary built by calling `note.summarize` with small
  invented inputs (reuse `tests/test_note.py`'s fixture builders): `test_from_summary_headline_numbers` (the answer
  sentence contains "3 episodes" and "1.9¢" for a fixture with those), `test_exec_hourly_is_hourly_max`,
  `test_screened_hist_bins` (22 edges, counts sum to rows), `test_ladder_hourly_counted_hours_only`,
  `test_review_none_passthrough`, `test_from_summary_validates` (returns a `ResearchSummary`, JSON round-trips,
  `len(json) < 150_000` for a 60-day synthetic series). `tests/test_note.py`: `test_load_returns_summarize_kwargs`
  (signature introspection: keys == `summarize` parameter names minus `generated_at`; marked integration if it needs
  the DBs — prefer a unit test with fake `conn`/`ch` objects returning empty results).
- [ ] **Step 2: Run** `pytest tests/test_research.py tests/test_note.py -q` — FAIL.
- [ ] **Step 3: Implement** `copy.py`, `research.py`, the `note.load` split, `_ladder`'s `hours` key, and the render's
  use of `copy.METHOD`. `note.render`'s existing wording for the method moves into `copy.METHOD` verbatim where it
  exists and is tightened where it doesn't; the note's output must still pass `tests/test_note.py`.
- [ ] **Step 4: Run** — PASS; full CI PASS. Regenerate the note on the OptiPlex once (controller) and check the diff
  is only the method wording.
- [ ] **Step 5: Commit** — `git commit -m "research: note loader split, method copy module, pure reducer to the bundle's ResearchSummary"`.

---

### Task 3: Hourly research in the publisher

**Files:** Modify `venue_arb/publish.py` (`run_forever`, `_results`, `default_readers`, state); Test
`tests/test_publish.py`, `tests/test_publish_flow.py` (integration).

**Interfaces:**
- Consumes `note.load`, `note.summarize`, `research.from_summary`, `review_cli.all_review_labels`,
  `review.precision`.
- Produces: `compute_research(readers: dict, now: datetime) -> ResearchSummary` (reader `research_inputs()` →
  `note.load`'s dict; reader `review_labels()` → `(labels, asof)`), `RESEARCH_TIMEOUT_WARN_S = 30`; state key
  `state["research"]` (last good), `state["research_hour"]`; `tick` passes `state.get("research")` into
  `build_live(research=…)`; qa results `research_failed` (warn, count since last record), `research_seconds` (warn
  over 30). Trigger: the same hour-change detection as the history write, run **after** the live write and push.

- [ ] **Step 1: Failing tests** `tests/test_publish.py`: `test_research_runs_after_push_on_hour_change` (injected
  readers/writers record call order: write live → push → research), `test_research_failure_keeps_previous`
  (reader raises → `state["research"]` unchanged, `research_failed` result counted), `test_research_seconds_warn`
  (a slow fake → warn result not passed when > 30 s, using an injected clock).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** PASS; full CI. Integration
  (`tests/test_publish_flow.py`): extend to call `compute_research` on the live DBs and assert the summary validates,
  `snapshots > 0`, and `len(model_dump_json()) < 150_000`; run on the OptiPlex (controller) and record the measured
  seconds in the report.
- [ ] **Step 5: Commit** — `git commit -m "publish: hourly research summary in the bundle (after push, keeps last good)"`.

---

### Task 4: Sample bundle with research

**Files:** Modify `dashboard/sample/make_sample.py`, regenerate `dashboard/sample/live.json` (+ history); Test
`tests/test_sample.py`.

- [ ] **Step 1: Test** `test_sample_has_research` (`research` present, headline has 2–3 sentences, the three series
  non-empty, `review == {"precision": 1.0, "labels": 31, "asof": …}`, `categories` covers every pair category) and the
  existing reproducibility test still byte-exact.
- [ ] **Step 2:** FAIL. **Step 3:** build the research section through `note.summarize` over invented rows (a few
  pairs, 48 hourly edge rows, 300 screened values, 6 ladder hours) and `research.from_summary`; keep `NOW` fixed.
  **Step 4:** PASS (run twice, byte-identical); CI.
- [ ] **Step 5: Commit** — `git commit -m "dashboard sample: research section"`.

---

### Task 5: Page skeleton, header, styles, Scanner tab

**Files:** Rewrite `dashboard/static/index.html`, `dashboard/static/styles.css`; Create `dashboard/static/common.js`
(helpers from v1 `app.js:25-47`: `$`, `esc`, `enc`, `num`, `usd`, `signed`, `cents`, `tone`, `safeUrl`, `settles`,
state `S`), `dashboard/static/scanner.js`; Modify `dashboard/static/app.js` (boot, load/poll, tabs, header; the
detail code moves out in Task 6); Test `tests/test_dashboard_static.py`.

**Interfaces:**
- `scanner.js` exports `scannerRows(pairs, {cat, us}) -> {rows, counts, example}`: one row per `(k_id, pm_id)` — the
  direction with the larger `net_edge_cents` (nulls last) — sorted by net edge desc, stale/no-asks rows last;
  `counts` per category for the chips; `example` true when no row has the executable label. `renderScanner()`.
  Columns and copy per spec §3; verdict tones: `none` (exact/spread no edge), `basis` (near-exact), `exec`, `stale`
  (no asks, stale, US-greyed with the "Not executable from the US" text). Unit switch ¢ | bps applies to the edge
  column; ROI is its own column (`net_edge_roi` as `+1.4%`). Footer line: "{n} pairs — per-leg quotes are not
  published (venue display terms) — ROI = net edge ÷ all-in cost per contract — {checks} admissible ladder checks in
  the last scored hour, {violations} violations". Example row: `data-example`, text "Example — what a row looks like
  when a pair clears the rule", values +1.4¢ / +1.4% / $620, no click handler.
- `app.js`: tabs `#scanner` (default) `#explorer` `#research` from the URL hash; header per spec (wordmark link set
  from `REPO`, H1, description, two figures `episodes · 7 d` and `pairs live` = sum of `kpis.pairs_live`, live dot +
  books time; figures greyed when `age_s > 3600`); poll as v1; the US toggle moves above the Scanner table with the v1
  banner copy; theme toggle in the header's right edge.
- `styles.css`: the spec's style block as CSS variables and three type sizes; light `:root`, dark under
  `[data-theme=dark]`; table rules (left text / right numbers, 44-px rows).

- [ ] **Step 1: Failing test** updates in `tests/test_dashboard_static.py`: `<title>` equals the spec's; H1 text
  present verbatim in `index.html`; every `import … from "./x.js"` in the static JS resolves to an existing file;
  `styles.css` declares `font-family: Inter` with the system fallback and no `@import`/`url(http`; the tab ids exist;
  the copy strings (example row text, footer fragments, `NOT_COMPUTED`) appear verbatim; all v1 guarantees kept.
  There is no JS test runner: `scannerRows`' two rules (best direction per pair; example row only when nothing is
  executable) are verified in the browser walk of Step 4 and stated as such in the report (Review Focus 2 and 3).
- [ ] **Step 2: Run** the static test — FAIL. **Step 3:** build the skeleton, styles, header, tabs and the Scanner
  against the sample (`BUNDLE_DIR=dashboard/sample uv run uvicorn dashboard.api.app:app --port 8787`); the Explorer
  and Research tabs show their placeholders ("coming in the next task") for now.
- [ ] **Step 4: Browser walk** (Claude Browser pane): both themes; chips filter and counts; sort by each numeric
  header; unit switch; US toggle greys + banner; the example row present with the sample (which has an executable
  row — temporarily serve a copy with that row removed to see the example row) and absent otherwise; stale header
  after serving a copy with an old `computed_at`; 390 px width stacks without horizontal scroll. Screenshot to
  `reports/dashboard-scanner.png`.
- [ ] **Step 5:** static test PASS; CI PASS. **Commit** — `git commit -m "dashboard v2: skeleton, light system, header, Scanner tab"`.

---

### Task 6: Explorer and Research tabs

**Files:** Create `dashboard/static/explorer.js` (v1 `app.js` list + detail code moved, re-skinned: collapsed ladder
groups, chart, cost panel, verdict cards, size box, snapshot click — functionally unchanged), `dashboard/static/research.js`;
Modify `dashboard/static/app.js` (wire tabs: a Scanner row click selects the pair and switches to `#explorer`),
`dashboard/static/styles.css`; Test `tests/test_dashboard_static.py`; update `reports/dashboard-sample.png`.

**Interfaces:**
- `research.js` `renderResearch(bundle)`: headline block (question, answer sentences from `research.headline`, the
  "as of {computed_at} — recomputed hourly from the same tables the Scanner reads — dated note: {note_path}" line
  with the note link built from `REPO`); three cards: **Executable** (figure `episodes_pair + episodes_ladder`,
  qualifier "median {m} min, median capacity ${c}, max net edge {x}¢"; Plotly line of `exec_hourly` with a dashed 1¢
  line), **Screened history** (figure `screened_share_ge_1c` as %, qualifier "of {rows} rows, Jun–{end}; {early}% in
  a pair's first week"; Plotly bar of `screened_hist`), **Same-instant ladders** (figure `ladder_violations`
  "violations", qualifier "{checks} admissible checks; basis {asset} +{up}/−{down} …"; Plotly bars of
  `ladder_hourly.checks` with violation markers); then the four `method` paragraphs under "How it's measured", and a
  review line "match review: precision {p} on {n} labels ({date})" or "no review sitting yet". When
  `bundle.research` is null: the `NOT_COMPUTED` copy and nothing else.
- Plotly layouts share one `baseLayout(theme)` in `common.js` (font Inter 13, margins 8-px multiples, no modebar,
  `uirevision`).

- [ ] **Step 1: Failing test** additions: `explorer.js` and `research.js` referenced from `app.js` imports and
  present; `NOT_COMPUTED` and the review-line copy present; v1 verdict labels still verbatim (now in `common.js` or
  `explorer.js`).
- [ ] **Step 2:** FAIL. **Step 3:** move the v1 code into `explorer.js` (no behaviour change except theme), write
  `research.js`, wire row→explorer selection, dark-theme check of the charts.
- [ ] **Step 4: Browser walk:** Scanner row → Explorer shows that pair; Explorer unchanged behaviours (range buttons,
  annotations, snapshot click, size box disabled with its tooltip); Research tab with the sample (all three charts
  render, headline numbers match the sample's research fields, method paragraphs render); Research with
  `research: null` in a served copy; both themes. Screenshots `reports/dashboard-research.png` and the refreshed
  `reports/dashboard-sample.png`.
- [ ] **Step 5:** static test PASS; CI PASS. **Commit** — `git commit -m "dashboard v2: Explorer moved, Research tab with live summary"`.

---

## Deploy (controller, after Sam's approval)
1. Merge the branch into `main` on the OptiPlex; `uv sync`; integration tests; `systemctl --user restart
   venue-arb-publish` (the research section appears at the next clock hour; run `python -m venue_arb.publish` once
   by hand if you want it immediately — not needed).
2. VM: re-copy the checkout (tar over ssh as on 2026-10-21) into `/srv/venue-arb/app`, remove `._*` files,
   `sudo systemctl restart venue-arb-web`; check `https://34.127.95.87/` renders the Scanner, the Explorer and the
   Research tab with live numbers.
3. Sam reviews the Research copy (spec §8); fix wording in `copy.py` only; redeploy both sides.
