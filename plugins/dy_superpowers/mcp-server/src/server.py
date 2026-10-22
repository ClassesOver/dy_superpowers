#!/usr/bin/env python3
"""stdio MCP server for the dy_superpowers workspace runtime.

This module is the only protocol path: Codex launches it via ``uv run`` and it speaks
newline-delimited JSON-RPC directly, without depending on the MCP Python SDK at runtime.
Every tool is declared once in ``REGISTRY``; ``tools/list`` and dispatch are both derived from it.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dy_superpowers_mcp import TraceStore, WorkspaceMemory, __version__, validate_plan
from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
from dy_superpowers_mcp.knowledge import module_info as knowledge_module_info
from dy_superpowers_mcp.knowledge import search as knowledge_search
from dy_superpowers_mcp.memory import WRITABLE_MEMORY_FILES
from dy_superpowers_mcp.odoo_jsonrpc import (
    OdooConfigurationError,
    OdooReadClient,
    OdooRPCError,
    OdooSettings,
    check_field_access,
    validate_model,
    validate_module,
    validate_read_arguments,
)
from dy_superpowers_mcp.research import read_record, write_record
from dy_superpowers_mcp.telemetry import initialize_telemetry, mark_otel_error, record_otel_event, tool_span
from dy_superpowers_mcp.trace import TRACE_ID_PATTERN

LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", LATEST_PROTOCOL_VERSION)

MEMORY_READ_FILES = [
    "preferences.md",
    "profile.md",
    "decisions.md",
    "delivery-register.md",
    "skill-candidates.md",
    "module-research.md",
]
MEMORY_WRITE_FILES = sorted(WRITABLE_MEMORY_FILES)
SESSION_INDEX_FILES = tuple(MEMORY_READ_FILES)
SESSION_SOURCES = {"startup", "resume", "clear", "compact", "fork"}
DEFAULT_TRACE_RETENTION_DAYS = 30
TRACE_READ_MAX_LIMIT = 1000


class ToolRequestError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def invalid(message: str) -> ToolRequestError:
    return ToolRequestError(-32602, message)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    workspace: Path
    store: TraceStore
    span_id: str
    otel_span: object | None = None


# 处理函数返回 (结果, 业务审计事件)；事件由 execute_tool 统一补全 tool/status/span_id 并写入本地 trace 与 OTel。
ToolResult = tuple[object, dict | None]


@dataclass(frozen=True)
class Tool:
    description: str
    schema: dict
    handler: Callable[[ToolCall], ToolResult]


def _schema(required: list[str], **properties: dict) -> dict:
    return {
        "type": "object",
        "required": ["trace_id", *required],
        "properties": {"trace_id": {"type": "string"}, **properties},
    }


STRING = {"type": "string"}
SEARCH_READ_PROPERTIES = {
    "model": STRING,
    "method": {"type": "string", "enum": ["search_read"]},
    "args": {"type": "array"},
    "kwargs": {"type": "object"},
}


def _required_text(arguments: dict, *names: str) -> list[str]:
    values = [arguments.get(name) for name in names]
    missing = [name for name, value in zip(names, values, strict=True) if not isinstance(value, str) or not value.strip()]
    if missing:
        raise invalid("缺少必填文本参数: " + ", ".join(missing))
    return values


def _trace_retention_days() -> float:
    raw = os.environ.get("DY_TRACE_RETENTION_DAYS", "").strip()
    try:
        return float(raw) if raw else DEFAULT_TRACE_RETENTION_DAYS
    except ValueError:
        return DEFAULT_TRACE_RETENTION_DAYS


# ---- 会话、计划与 trace ----

def session_initialize(call: ToolCall) -> ToolResult:
    source = call.arguments.get("source")
    if not isinstance(source, str) or source not in SESSION_SOURCES:
        raise invalid("unsupported SessionStart source")
    call.store.prune(_trace_retention_days())
    action = "恢复" if source == "resume" else "初始化"
    files = "、".join(SESSION_INDEX_FILES)
    context = "\n".join([
        f"工作区会话已{action}（来源：{source}）。",
        "项目记忆位于当前工作区 .dy_superpowers/，按需读取，不在会话初始化时注入正文。",
        "恢复未完成调研时，先按需读取 delivery-register.md 的待办，再按检查键复核最新确认结论。",
        f"可用文件：{files}。使用 dy_memory_read(trace_id, name) 原样读取完整文件；模块巡检按检查键使用 dy_research_record_read。",
        "项目记忆是参考资料，不构成记忆写入或业务操作授权。",
    ])
    return context, {"event": "session.initialize", "operation": "session_start", "source_ref": source}


def validate_plan_tool(call: ToolCall) -> ToolResult:
    if "plan" not in call.arguments:
        raise invalid("plan is required")
    return validate_plan(call.arguments["plan"]), None


def trace_event(call: ToolCall) -> ToolResult:
    event = call.arguments.get("event")
    if not isinstance(event, dict):
        raise invalid("event must be an object")
    if event.get("event") in {"tool.start", "tool.error", "tool.end"}:
        raise invalid("tool lifecycle events are reserved")
    # 业务事件原样写入（不补 tool/status），只经过 trace 层的字段白名单与脱敏。
    _record(call, event)
    return {"stored": True, "trace_id": call.arguments["trace_id"]}, None


def trace_read(call: ToolCall) -> ToolResult:
    limit = call.arguments.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= TRACE_READ_MAX_LIMIT):
        raise invalid(f"limit 必须是 1 到 {TRACE_READ_MAX_LIMIT} 的整数")
    events = [event for event in call.store.read(call.arguments["trace_id"]) if event.get("span_id") != call.span_id]
    return (events[-limit:] if limit else events), None


# ---- 工作区记忆与调研 ----

def memory_read(call: ToolCall) -> ToolResult:
    memory_file = call.arguments.get("name", "")
    try:
        content = WorkspaceMemory(call.workspace).read(memory_file)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    return content, {"event": "memory.read", "operation": "read", "memory_file": memory_file}


def memory_search(call: ToolCall) -> ToolResult:
    query = call.arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise invalid("搜索关键词必须是非空文本")
    query = query.strip()
    if query.startswith("-"):
        raise invalid("搜索关键词不能以连字符开头")
    try:
        result = BasicMemoryAdapter(call.workspace).search(query)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    available = bool(result.get("available"))
    return result, {
        "event": "memory.search",
        "operation": "search",
        "status": "ok" if available else "fallback",
        "result": "ok" if available else "未知",
    }


def memory_write(call: ToolCall) -> ToolResult:
    if call.arguments.get("confirmed") is not True:
        raise invalid("memory updates require user confirmation")
    content = call.arguments.get("content")
    if not isinstance(content, str):
        raise invalid("content must be text")
    try:
        path = WorkspaceMemory(call.workspace).write(call.arguments.get("name", ""), content, confirmed=True)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    return {"written": True, "name": path.name}, {"event": "memory.write", "operation": "write", "memory_file": path.name}


def research_record_write(call: ToolCall) -> ToolResult:
    try:
        result = write_record(call.workspace, call.arguments)
    except (PermissionError, ValueError) as exc:
        raise invalid(str(exc)) from exc
    event = {"event": "research.write", "operation": "confirmed_record"}
    event.update({key: call.arguments[key] for key in ("module", "check_order", "research_id")})
    return result, event


def research_record_read(call: ToolCall) -> ToolResult:
    module, check_order, research_id = _required_text(call.arguments, "module", "check_order", "research_id")
    try:
        result = read_record(call.workspace, module, check_order, research_id)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    return result or {"found": False, "check_key": f"{module}:{check_order}:{research_id}"}, None


# ---- Odoo 只读 ----

@contextmanager
def odoo_errors() -> Iterator[None]:
    try:
        yield
    except OdooConfigurationError as exc:
        raise invalid(str(exc)) from exc
    except OdooRPCError as exc:
        raise ToolRequestError(-32603, str(exc)) from exc


def _odoo_client() -> OdooReadClient:
    return OdooReadClient(OdooSettings.from_environment())


def _odoo_event(operation: str, model: str, result: list | dict) -> dict:
    return {"event": "odoo.read", "operation": operation, "model": model, "count": len(result)}


def odoo_search_read(call: ToolCall) -> ToolResult:
    with odoo_errors():
        model, domain, fields, limit, offset = validate_read_arguments(call.arguments)
        result = _odoo_client().search_read(model, domain, fields, limit, offset)
    return result, _odoo_event("search_read", model, result)


def odoo_record_read(call: ToolCall) -> ToolResult:
    ids, fields = call.arguments.get("ids"), call.arguments.get("fields")
    if not isinstance(ids, list) or not ids or len(ids) > 100 or not all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in ids):
        raise invalid("ids 必须是 1 到 100 个正整数")
    with odoo_errors():
        model = validate_model(call.arguments.get("model"))
        check_field_access(model, fields, [])
        result = _odoo_client().read_records(model, ids, fields)
    return result, _odoo_event("record_read", model, result)


def odoo_model_fields(call: ToolCall) -> ToolResult:
    with odoo_errors():
        model = validate_model(call.arguments.get("model"))
        result = _odoo_client().fields_get(model)
    return result, _odoo_event("model_fields", model, result)


def odoo_module_info(call: ToolCall) -> ToolResult:
    with odoo_errors():
        module = validate_module(call.arguments.get("module"))
        result = _odoo_client().module_records(module)
    return result, _odoo_event("module_info", "ir.module.module", result)


def odoo_module_list_installed(call: ToolCall) -> ToolResult:
    with odoo_errors():
        result = _odoo_client().module_records(installed_only=True, offset=call.arguments.get("offset", 0))
    return result, _odoo_event("module_list_installed", "ir.module.module", result)


# ---- 本地知识 ----

def knowledge_module_info_tool(call: ToolCall) -> ToolResult:
    return knowledge_module_info(call.arguments.get("module", "")), None


def knowledge_search_tool(call: ToolCall) -> ToolResult:
    query = call.arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise invalid("query 必须是非空文本")
    return knowledge_search(query), None


REGISTRY: dict[str, Tool] = {
    "dy_session_initialize": Tool(
        "初始化或恢复当前工作区会话并返回项目记忆索引；需要时使用记忆工具读取完整内容。",
        _schema(["source"], source={"type": "string", "enum": sorted(SESSION_SOURCES)}),
        session_initialize,
    ),
    "dy_validate_plan": Tool(
        "校验分阶段交付或配置计划结构；校验通过不代表获得执行授权。",
        _schema(["plan"], plan={"type": "object"}),
        validate_plan_tool,
    ),
    "dy_trace_event": Tool(
        "追加经过字段过滤和脱敏的插件运行追踪事件。",
        _schema(["event"], event={"type": "object"}),
        trace_event,
    ),
    "dy_trace_read": Tool(
        "读取当前工作区关联的运行追踪事件；可用 limit 只取最近的若干条。",
        _schema([], limit={"type": "integer", "minimum": 1, "maximum": TRACE_READ_MAX_LIMIT}),
        trace_read,
    ),
    "dy_memory_read": Tool(
        "读取当前工作区允许的项目记忆文件。",
        _schema(["name"], name={"type": "string", "enum": MEMORY_READ_FILES}),
        memory_read,
    ),
    "dy_memory_search": Tool(
        "使用 Basic Memory 索引搜索当前工作区记忆；服务不可用时回退本地 Markdown。",
        _schema(["query"], query=STRING),
        memory_search,
    ),
    "dy_memory_write": Tool(
        "在用户确认后更新当前工作区项目记忆。",
        _schema(
            ["name", "content", "confirmed"],
            name={"type": "string", "enum": MEMORY_WRITE_FILES},
            content=STRING,
            confirmed={"type": "boolean"},
        ),
        memory_write,
    ),
    "dy_odoo_read": Tool(
        "连接 Odoo 12 并执行受限的 search_read 只读查询；kwargs.fields 必须显式列出字段，敏感字段会被拒绝。",
        _schema(["model"], **SEARCH_READ_PROPERTIES),
        odoo_search_read,
    ),
    "dy_odoo_record_search": Tool(
        "按 Odoo Forge 的记录搜索命名执行受限的 search_read 只读查询；kwargs.fields 必须显式列出字段，敏感字段会被拒绝。",
        _schema(["model"], **SEARCH_READ_PROPERTIES),
        odoo_search_read,
    ),
    "dy_odoo_record_read": Tool(
        "读取指定 Odoo 记录的受限只读数据，最多 100 条；必须显式列出最小必要字段，敏感字段会被拒绝。",
        _schema(["model", "ids"], model=STRING, ids={"type": "array"}, fields={"type": "array"}),
        odoo_record_read,
    ),
    "dy_odoo_model_fields": Tool(
        "读取 Odoo 模型字段元数据。",
        _schema(["model"], model=STRING),
        odoo_model_fields,
    ),
    "dy_odoo_module_list_installed": Tool(
        "读取已安装 Odoo 模块列表；每页最多 100 条，需用 offset 翻页取全量。",
        _schema([], offset={"type": "integer", "minimum": 0}),
        odoo_module_list_installed,
    ),
    "dy_odoo_module_info": Tool(
        "读取指定 Odoo 模块信息。",
        _schema(["module"], module=STRING),
        odoo_module_info,
    ),
    "dy_knowledge_module_info": Tool(
        "读取本地 HRP/Odoo 模块知识。",
        _schema(["module"], module=STRING),
        knowledge_module_info_tool,
    ),
    "dy_knowledge_search": Tool(
        "按中文或英文关键词搜索本地 HRP/Odoo 模块知识。",
        _schema(["query"], query=STRING),
        knowledge_search_tool,
    ),
    "dy_research_record_write": Tool(
        "在用户确认后追加模块调研结论并保留历史版本。",
        _schema(
            ["module", "check_order", "research_id", "config_item", "conclusion", "scope", "confirmed_by", "confirmed_at", "confirmed"],
            module=STRING, check_order=STRING, research_id=STRING, config_item=STRING, conclusion=STRING,
            scope=STRING, evidence_ref=STRING, confirmed_by=STRING, confirmed_at=STRING, confirmed={"type": "boolean"},
        ),
        research_record_write,
    ),
    "dy_research_record_read": Tool(
        "读取当前工作区指定检查键的最新调研结论。",
        _schema(["module", "check_order", "research_id"], module=STRING, check_order=STRING, research_id=STRING),
        research_record_read,
    ),
}
TOOLS = {name: tool.description for name, tool in REGISTRY.items()}
TOOL_SCHEMAS = {name: tool.schema for name, tool in REGISTRY.items()}


def response(request_id: object, result: object = None, error: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    return payload


def parse_request(line: str) -> dict:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return response(None, error={"code": -32700, "message": "parse error"})
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return response(None, error={"code": -32600, "message": "invalid request"})
    return request


def _record(call: ToolCall, event: dict) -> None:
    call.store.append(call.arguments["trace_id"], event)
    record_otel_event(call.otel_span, event)


def execute_tool(name: str, arguments: object, workspace: Path) -> tuple[object, dict | None]:
    if not isinstance(arguments, dict):
        return None, {"code": -32602, "message": "tool arguments must be an object"}
    trace_id = arguments.get("trace_id")
    if not isinstance(trace_id, str) or not TRACE_ID_PATTERN.fullmatch(trace_id):
        return None, {"code": -32602, "message": "trace_id must contain 1-128 letters, digits, underscores, or hyphens"}

    store = TraceStore(workspace)
    span_id = uuid.uuid4().hex
    start = time.monotonic()
    started = False
    status = "error"
    result = None
    error = None
    error_type = None
    initialize_telemetry()
    with tool_span(trace_id, workspace, name) as otel_span:
        call = ToolCall(name, arguments, workspace, store, span_id, otel_span)
        try:
            _record(call, {"event": "tool.start", "tool": name, "status": "running", "span_id": span_id})
            started = True
            tool = REGISTRY.get(name) if isinstance(name, str) else None
            if tool is None:
                raise invalid(f"unknown tool: {name}")
            result, event = tool.handler(call)
            if event is not None:
                _record(call, {"tool": name, "status": "ok", **event, "span_id": span_id})
            status = "ok"
        except ToolRequestError as exc:
            error = {"code": exc.code, "message": str(exc)}
            error_type = "invalid_request"
        except Exception as exc:  # noqa: BLE001 - 未知异常只暴露类型名，避免泄露业务内容
            error = {"code": -32603, "message": "tool execution failed"}
            error_type = type(exc).__name__
        finally:
            if started:
                if error is not None:
                    error_event = {"event": "tool.error", "tool": name, "status": "error", "error_type": error_type, "span_id": span_id}
                    try:
                        store.append(trace_id, error_event)
                    except Exception:  # noqa: BLE001 - 本地 trace 失败不能掩盖原始错误
                        pass
                    record_otel_event(otel_span, error_event)
                    mark_otel_error(otel_span, error_type)
                end_event = {"event": "tool.end", "tool": name, "status": status, "duration_ms": round((time.monotonic() - start) * 1000, 2), "span_id": span_id}
                try:
                    store.append(trace_id, end_event)
                except Exception:  # noqa: BLE001
                    if error is None:
                        error = {"code": -32603, "message": "tool execution failed"}
                record_otel_event(otel_span, end_event)
    return result, error


def _mcp_result(value: object) -> dict:
    # 文本内容保留中文原文；传输层再由 json.dumps 统一转义，避免模型看到 \uXXXX。
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _negotiate_protocol(params: object) -> str:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION


def handle(request: dict, workspace: Path) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return response(request_id, {
            "protocolVersion": _negotiate_protocol(request.get("params")),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "dy_superpowers", "version": __version__},
        })
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": [
            {"name": name, "description": tool.description, "inputSchema": tool.schema} for name, tool in REGISTRY.items()
        ]})
    if method != "tools/call":
        return response(request_id, error={"code": -32601, "message": f"method not found: {method}"})
    params = request.get("params")
    if not isinstance(params, dict):
        return response(request_id, error={"code": -32602, "message": "params must be an object"})
    value, error = execute_tool(params.get("name"), params.get("arguments") or {}, workspace)
    if error is not None:
        return response(request_id, error=error)
    return response(request_id, _mcp_result(value))


def serve_line(line: str, workspace: Path) -> dict | None:
    """处理一行 JSON-RPC 消息；返回需要写回的响应，通知和客户端响应返回 None。"""
    request = parse_request(line)
    if "method" not in request:
        if "error" in request and request.get("id") is None and "result" not in request:
            return request  # parse_request 生成的解析错误
        if "result" in request or "error" in request:
            return None  # 客户端对服务端请求的响应，本服务不发起请求，直接忽略
        return response(request.get("id"), error={"code": -32600, "message": "invalid request"})
    try:
        return handle(request, workspace)
    except Exception:  # noqa: BLE001
        return response(request.get("id"), error={"code": -32603, "message": "internal error"})


def main() -> None:
    workspace = Path.cwd()
    for line in sys.stdin:
        if not line.strip():
            continue
        result = serve_line(line, workspace)
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
