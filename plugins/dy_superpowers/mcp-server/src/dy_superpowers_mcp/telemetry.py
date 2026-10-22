"""Optional OpenTelemetry export for plugin operation traces."""

from __future__ import annotations

import atexit
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from .trace import TRACE_CONTEXT_FIELDS, otel_event_attributes, workspace_id_for, workflow_id_for
from .endpoints import validate_endpoint

_provider = None
_tracer = None
_initialized = False
DEFAULT_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:33049/v1/traces"
PLUGIN_OTLP_TRACES_ENDPOINT = "DY_SUPERPOWERS_OTLP_TRACES_ENDPOINT"
PLUGIN_OTLP_EXPORT_TIMEOUT_MS = "DY_SUPERPOWERS_OTLP_EXPORT_TIMEOUT_MS"
TELEMETRY_SCOPE = "DY_SUPERPOWERS_TELEMETRY_SCOPE"
PLUGIN_SERVICE_NAME = "dy_superpowers"


def otlp_traces_endpoint() -> str:
    # 标准变量允许部署者在安装后覆盖插件清单中的本机默认值。
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if endpoint:
        return endpoint
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        return f"{endpoint.rstrip('/')}/v1/traces"
    endpoint = os.environ.get(PLUGIN_OTLP_TRACES_ENDPOINT)
    if endpoint:
        return endpoint
    return DEFAULT_OTLP_TRACES_ENDPOINT


def otlp_export_timeout_millis() -> int:
    """限制遥测退出等待时间，避免不可达 collector 拖慢插件。"""
    try:
        value = int(os.environ.get(PLUGIN_OTLP_EXPORT_TIMEOUT_MS, "1000"))
    except (TypeError, ValueError):
        return 1000
    return max(100, min(value, 5000))


def initialize_telemetry() -> bool:
    global _initialized, _provider, _tracer
    if _initialized:
        return _tracer is not None
    _initialized = True
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        sys.stderr.write("OpenTelemetry dependencies are missing; install the dy-superpowers-mcp package dependencies.\n")
        return False

    try:
        endpoint = validate_endpoint(otlp_traces_endpoint(), "DY_OTLP_ALLOWED_ORIGINS",
                                     local_default=DEFAULT_OTLP_TRACES_ENDPOINT)
        import requests

        class DirectSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs["allow_redirects"] = False
                return super().request(*args, **kwargs)

        session = DirectSession()
        session.trust_env = False
        # 只创建并导出本插件自己的 provider；不注册任何全局自动埋点或其他服务的 exporter。
        # 该环境变量只用于声明安装配置；收集范围固定为本插件，不能被调用方扩大。
        configured_scope = os.environ.get(TELEMETRY_SCOPE, "plugin").strip()
        scope = "plugin" if configured_scope != "plugin" else configured_scope
        _provider = TracerProvider(resource=Resource.create({
            "service.name": PLUGIN_SERVICE_NAME,
            "service.version": "0.1.0",
            "dy.plugin": PLUGIN_SERVICE_NAME,
            "dy.telemetry.scope": scope,
        }))
        export_timeout = otlp_export_timeout_millis()
        _provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, session=session, timeout=export_timeout / 1000),
        ))
        # 不注册为进程全局 provider，避免同进程其他库的 span 被此插件 exporter 收集。
        _tracer = _provider.get_tracer("dy_superpowers")
        atexit.register(shutdown_telemetry)
        return True
    except Exception as exc:
        sys.stderr.write(f"OpenTelemetry initialization failed ({type(exc).__name__}); local trace recording remains enabled.\n")
        return False


# 只提升可筛选元数据；正文和搜索词留在事件中，不复制成 span 索引字段。
SPAN_METADATA_FIELDS = {
    "operation", "duration_ms", "error_type", "error_code", "skill", "module", "model",
    "check_order", "research_id", "check_key", "scope_id", "revision", "research_status",
    "result", "valid", "error_count", "expected_count", "memory_file", "backend", "found",
    "count", "result_count", "requested_count", "revision_count", "history_count", "has_more",
    "content_bytes", "content_truncated", "field_count", "domain_term_count", "limit", "offset",
    "domain", "domain_truncated", "source_ref",
}
CATEGORY_LABELS = {
    "session": "会话", "memory": "项目记忆", "research": "模块调研", "inspection": "模块巡检",
    "odoo": "Odoo 查询", "knowledge": "知识查询", "trace": "操作追踪", "validate": "计划校验",
}
BUSINESS_METADATA_FIELDS = TRACE_CONTEXT_FIELDS | {"check_key", "result", "research_status"}
_tool_attributes: ContextVar[dict] = ContextVar("dy_tool_attributes", default={})
_active_tool_span: ContextVar[object | None] = ContextVar("dy_active_tool_span", default=None)


@contextmanager
def _span(name: str, attributes: dict, *, root: bool = False) -> Iterator[object | None]:
    if _tracer is None:
        yield None
        return
    manager = None
    entered = False
    span = None
    try:
        from opentelemetry.context import Context

        options = {"context": Context()} if root else {}
        manager = _tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False, **options,
        )
        span = manager.__enter__()
        entered = True
        span.set_attributes(attributes)
    except Exception as exc:
        span = None
        sys.stderr.write(f"遥测 span 初始化失败（{type(exc).__name__}），继续本地操作。\n")

    # 不把异常正文、堆栈或业务结果交给 SDK 自动记录。
    try:
        yield span
    except BaseException as exc:
        mark_otel_error(span, type(exc).__name__)
        raise
    finally:
        if entered:
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:
                sys.stderr.write(f"遥测 span 结束失败（{type(exc).__name__}），继续本地操作。\n")


@contextmanager
def tool_span(trace_id: str, workspace: str | Path, tool: str, *, call_id: str = "", trace_context: dict | None = None) -> Iterator[object | None]:
    context = {key: value for key, value in (trace_context or {}).items() if key in TRACE_CONTEXT_FIELDS}
    if all(key in context for key in ("module", "check_order", "research_id")):
        context["check_key"] = f"{context['module']}:{context['check_order']}:{context['research_id']}"
    attributes = otel_event_attributes({
        **context,
        "tool": tool, "workspace_id": workspace_id_for(workspace),
        "workflow_id": workflow_id_for(workspace, trace_id), "call_id": call_id,
        "category": tool.removeprefix("dy_").split("_", 1)[0],
    })
    attributes["dy.category_label"] = CATEGORY_LABELS.get(attributes.get("dy.category"), "工具调用")
    attributes["dy.span_role"] = "tool"
    token = _tool_attributes.set(attributes)
    try:
        # 每次调用是真实根节点；多轮流程按 workflow_id 聚合，不制造不存在的父节点。
        with _span(tool, attributes, root=True) as span:
            active_token = _active_tool_span.set(span)
            try:
                yield span
            finally:
                _active_tool_span.reset(active_token)
    finally:
        _tool_attributes.reset(token)


@contextmanager
def operation_span(tool: str) -> Iterator[object | None]:
    if _active_tool_span.get() is None:
        yield None
        return
    attributes = {**_tool_attributes.get(), "dy.span_role": "operation"}
    with _span(tool.removeprefix("dy_").replace("_", "."), attributes) as span:
        yield span
        _mark_otel_ok(span)


def current_trace_context() -> dict[str, str]:
    metadata = {key.removeprefix("dy."): value for key, value in _tool_attributes.get().items()
                if key.removeprefix("dy.") in BUSINESS_METADATA_FIELDS | {"call_id"}}
    if _tracer is None or _active_tool_span.get() is None:
        return metadata
    try:
        from opentelemetry.trace import get_current_span

        context = get_current_span().get_span_context()
        if context.is_valid:
            return {**metadata, "otel_trace_id": f"{context.trace_id:032x}", "otel_span_id": f"{context.span_id:016x}"}
    except Exception:
        pass
    return metadata


def _mark_otel_ok(span: object | None) -> None:
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.OK))
    except Exception as exc:
        sys.stderr.write(f"遥测状态记录失败（{type(exc).__name__}），继续本地操作。\n")


def record_otel_event(span: object | None, event: dict, *, promote: bool = True) -> None:
    if span is None:
        return
    try:
        from opentelemetry.trace import get_current_span

        attributes = otel_event_attributes(event)
        event_name = attributes.pop("dy.event", "dy.event")
        span.add_event(event_name, attributes)
        metadata = {key: value for key, value in attributes.items()
                    if key.removeprefix("dy.") in (SPAN_METADATA_FIELDS if promote else BUSINESS_METADATA_FIELDS)}
        if promote and event_name in {"tool.start", "tool.error", "tool.end"}:
            metadata["dy.status"] = attributes.get("dy.status", "unknown")
        elif promote and "dy.backend" in attributes:
            metadata["dy.backend_status"] = attributes.get("dy.status", "unknown")
        span.set_attributes(metadata)
        current = get_current_span()
        if current is not span:
            current.set_attributes(metadata)
        if event_name == "tool.end" and attributes.get("dy.status") == "ok":
            _mark_otel_ok(span)
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
        try:
            _provider.shutdown()
        except Exception as exc:
            sys.stderr.write(f"遥测关闭失败（{type(exc).__name__}）。\n")
