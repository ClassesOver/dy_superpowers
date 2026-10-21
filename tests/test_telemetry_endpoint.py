import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/dy_superpowers/mcp-server/src"))

from dy_superpowers_mcp import telemetry


def test_default_endpoint_uses_external_http_port():
    assert telemetry.otlp_traces_endpoint() == "http://127.0.0.1:33049/v1/traces"


def test_trace_endpoint_override_has_priority(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://specific.example/traces")
    assert telemetry.otlp_traces_endpoint() == "https://specific.example/traces"


def test_generic_endpoint_override_adds_trace_path(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example/")
    assert telemetry.otlp_traces_endpoint() == "https://collector.example/v1/traces"
