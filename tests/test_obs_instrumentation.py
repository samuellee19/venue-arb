import tomllib
from pathlib import Path

import pytest

FLOWS = {"catalog": "catalog_flow", "match": "match_flow", "basis": "basis_flow", "score": "score_flow",
         "ladder_score": "ladder_score_flow", "screened": "screened_flow"}
SCHEDULES = Path.home() / "platform/obs/schedules.toml"

@pytest.mark.parametrize("mod", FLOWS)
def test_flow_is_an_obs_job(mod):
    pytest.importorskip("obs")
    pytest.importorskip("qa")
    m = __import__(f"venue_arb.flows.{mod}", fromlist=[FLOWS[mod]])
    fn = getattr(m, FLOWS[mod]).fn  # Prefect Flow.fn is the decorated function
    assert getattr(fn, "__obs_job__", None) == f"venue-arb.{mod}"

def test_registry_matches_served_flows():
    pytest.importorskip("obs")
    if not SCHEDULES.exists():
        pytest.skip("no platform checkout")
    reg = tomllib.loads(SCHEDULES.read_text())["jobs"]
    served = {"catalog": "5 * * * *", "match": "20 * * * *", "basis": "35 * * * *", "score": "40 * * * *",
              "ladder_score": "50 * * * *"}
    for name, cron in served.items():
        assert reg[f"venue-arb.{name}"]["cadence"] == cron
    assert reg["venue-arb.collector"]["heartbeat"] == "10m"

def _score_tolerating_qa_failure():
    """The live data may fail its own QA checks; that is the flow's business, not the wiring under test."""
    from qa.checks import DataQualityError

    from venue_arb.flows.score import score_flow
    try:
        return score_flow()
    except DataQualityError as e:
        return e

@pytest.mark.integration
def test_score_flow_reports_run():
    import obs
    from obs import runtime
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from qa import db
    runtime._reset()
    try:
        spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
        obs.init("venue-arb", environment="test", exporters=(spans, logs))
        _score_tolerating_qa_failure()
        runtime.flush()
        ev = [r.log_record for r in logs.get_finished_logs() if r.log_record.event_name]
        names = [e.event_name for e in ev]
        terminal = [e for e in ev if e.event_name in ("workflow.run.completed", "workflow.run.failed")]
        assert names.count("workflow.run.started") == 1 and len(terminal) == 1
        started = next(e for e in ev if e.event_name == "workflow.run.started")
        rid = started.attributes["run.id"]
        assert terminal[0].attributes["run.id"] == rid
        assert started.attributes["job.name"] == "venue-arb.score" and started.attributes.get("prefect.flow_run.id")
        with db.pg() as conn:
            n = conn.execute("SELECT count(*) FROM qa.check_results WHERE run_id = %s", (rid,)).fetchone()[0]
        assert n > 0
    finally:
        runtime._reset()

@pytest.mark.integration
def test_flow_survives_collector_down(monkeypatch):
    import time

    import obs
    from obs import runtime
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")  # closed port
    runtime._reset()
    try:
        obs.init("venue-arb", environment="test")  # real OTLP exporters, nothing listening
        t0 = time.monotonic()
        _score_tolerating_qa_failure()
        runtime.flush()
        assert time.monotonic() - t0 < 120  # exporter timeouts bound the cost; no hang, no exception
    finally:
        runtime._reset()
