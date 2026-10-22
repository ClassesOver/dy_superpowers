"""Optional OpenTelemetry export for plugin operation traces."""

from __future__ import annotations

import atexit
import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .trace import otel_event_attributes

_provider = None
_tracer = None
_initialized = False
DEFAULT_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:33049/v1/traces"


def otlp_traces_endpoint() -> str:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if endpoint:
        return endpoint
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        return f"{endpoint.rstrip('/')}/v1/traces"
    return DEFAULT_OTLP_TRACES_ENDPOINT


def initialize_telemetry() -> bool:
    global _initialized, _provider, _tracer
    if _initialized:
        return _tracer is not None
    _initialized = True
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        sys.stderr.write("OpenTelemetry dependencies are missing; install the dy-superpowers-mcp package dependencies.\n")
        return False

    try:
        _provider = TracerProvider(resource=Resource.create({"service.name": "dy_superpowers", "service.version": __version__}))
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_traces_endpoint())))
        trace.set_tracer_provider(_provider)
        _tracer = trace.get_tracer("dy_superpowers")
        atexit.register(shutdown_telemetry)
        return True
    except Exception as exc:
        sys.stderr.write(f"OpenTelemetry initialization failed ({type(exc).__name__}); local trace recording remains enabled.\n")
        return False


@contextmanager
def tool_span(trace_id: str, workspace: str | Path, tool: str) -> Iterator[object | None]:
    if _tracer is None:
        yield None
        return

    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

    scoped_id = hashlib.sha256(f"{Path(workspace).resolve()}\0{trace_id}".encode()).digest()[:16]
    parent_id = hashlib.sha256(scoped_id + b"dy_superpowers.parent").digest()[:8]
    parent = NonRecordingSpan(SpanContext(
        trace_id=int.from_bytes(scoped_id, "big"),
        span_id=int.from_bytes(parent_id, "big"),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    ))
    context = set_span_in_context(parent)
    manager = _tracer.start_as_current_span("dy_superpowers.tool", context=context)
    try:
        span = manager.__enter__()
    except Exception as exc:
        sys.stderr.write(f"OpenTelemetry span creation failed ({type(exc).__name__}); local trace recording remains enabled.\n")
        yield None
        return
    try:
        attributes = otel_event_attributes({"tool": tool})
        if attributes:
            span.set_attributes(attributes)
        yield span
    finally:
        try:
            manager.__exit__(*sys.exc_info())
        except Exception as exc:
            sys.stderr.write(f"OpenTelemetry span export failed ({type(exc).__name__}); local trace recording remains enabled.\n")


def record_otel_event(span: object | None, event: dict) -> None:
    if span is None:
        return
    try:
        attributes = otel_event_attributes(event)
        event_name = attributes.pop("dy.event", "dy.event")
        span.add_event(event_name, attributes)
    except Exception as exc:
        sys.stderr.write(f"OpenTelemetry event recording failed ({type(exc).__name__}); local trace recording remains enabled.\n")


def mark_otel_error(span: object | None, error_type: str | None) -> None:
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, error_type or "tool_error"))
    except Exception as exc:
        sys.stderr.write(f"OpenTelemetry status recording failed ({type(exc).__name__}); local trace recording remains enabled.\n")


def shutdown_telemetry() -> None:
    if _provider is not None:
        _provider.shutdown()
