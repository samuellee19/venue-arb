from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def test_tick_on_live_readers_and_history_hour(tmp_path):
    import pyarrow.parquet as pq
    from qa import db

    from venue_arb import publish

    now = datetime.now(UTC)
    state = {}
    live = publish.tick(state, publish.default_readers(db), now)
    print(f"pairs={len(live.pairs)} live={live.kpis.pairs_live} skipped={state['skipped']} "
          f"bytes={len(live.model_dump_json())}")
    assert len(live.pairs) >= 1

    hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)  # the last full hour
    with db.pg() as conn:
        sides = publish.load_sides(conn)
    ch = db.ch()
    path = publish.write_history_hour(ch, tmp_path, hour, sides)
    params = {"lo": hour, "hi": hour + timedelta(hours=1)}
    window = "ts >= {lo:DateTime64(3, 'UTC')} AND ts < {hi:DateTime64(3, 'UTC')}"
    (edges,) = ch.query(f"SELECT count() FROM venue_arb.edges FINAL WHERE {window}", parameters=params).result_rows[0]
    (ladder,) = ch.query(f"SELECT count() FROM venue_arb.ladder_edges FINAL WHERE {window} "
                         "AND k_id NOT LIKE 'ZZTEST-%'", parameters=params).result_rows[0]
    size = path.stat().st_size
    print(f"history {path.name}: edges={edges} ladder={ladder} bytes={size} "
          f"7d projection={size * publish.HISTORY_DAYS * 24 / 1e6:.1f} MB")
    assert pq.read_metadata(path).num_rows == edges + ladder
    assert size < publish.HISTORY_SIZE_BUDGET_MB * 1e6


def test_compute_research_live():
    import time

    from qa import db

    from venue_arb import publish
    from venue_arb.bundle import ResearchSummary

    started = time.monotonic()
    r = publish.compute_research(publish.default_readers(db), datetime.now(UTC))
    seconds = time.monotonic() - started
    j = r.model_dump_json()
    print(f"research: {seconds:.1f}s snapshots={r.snapshots} pairs={r.pairs_eligible} review={r.review} bytes={len(j)}")
    ResearchSummary.model_validate_json(j)
    assert r.snapshots > 0 and len(j) < 150_000
