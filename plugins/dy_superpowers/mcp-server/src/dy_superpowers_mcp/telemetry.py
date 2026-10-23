"""外部遥测（OTLP/OpenObserve）已移除；本模块保留调用点兼容和本地 trace 的元数据上下文。

本地 JSONL 追踪是唯一持久化通道，实现见 trace.py。otel_span 相关函数为兼容既有调用点保留，
均为空操作；tool_span 仅维护本地事件元数据（供 current_trace_context 注入 JSONL 事件）。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from .trace import TRACE_CONTEXT_FIELDS, workspace_id_for, workflow_id_for

BUSINESS_METADATA_FIELDS = TRACE_CONTEXT_FIELDS | {"check_key", "result", "research_status"}
_tool_attributes: ContextVar[dict] = ContextVar("dy_tool_attributes", default={})


def initialize_telemetry() -> bool:
    return False


@contextmanager
def tool_span(trace_id: str, workspace: str | Path, tool: str, *, call_id: str = "",
              trace_context: dict | None = None) -> Iterator[None]:
    context = {key: value for key, value in (trace_context or {}).items() if key in TRACE_CONTEXT_FIELDS}
    if all(key in context for key in ("module", "check_order", "research_id")):
        context["check_key"] = f"{context['module']}:{context['check_order']}:{context['research_id']}"
    attributes = {
        **context,
        "tool": tool,
        "workspace_id": workspace_id_for(workspace),
        "workflow_id": workflow_id_for(workspace, trace_id),
        "call_id": call_id,
    }
    token = _tool_attributes.set(attributes)
    try:
        yield None
    finally:
        _tool_attributes.reset(token)


@contextmanager
def operation_span(tool: str) -> Iterator[None]:
    yield None


def current_trace_context() -> dict[str, str]:
    return {key: value for key, value in _tool_attributes.get().items()
            if key in BUSINESS_METADATA_FIELDS | {"call_id"}}


def record_otel_event(span: object | None, event: dict, *, promote: bool = True) -> None:
    return None


def mark_otel_error(span: object | None, error_type: str | None) -> None:
    return None


def shutdown_telemetry() -> None:
    return None
