"""Tests never report to the live alerter: the flows' import-time obs.init becomes a no-op (init is idempotent)."""
try:
    import obs
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    obs.init("venue-arb", environment="test", exporters=(InMemorySpanExporter(), InMemoryLogRecordExporter()))
except ImportError:  # CI has no platform checkout; the obs-dependent tests skip themselves
    pass
