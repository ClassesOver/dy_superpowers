#!/usr/bin/env python3
"""stdio MCP server for the dy_superpowers workspace runtime."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dy_superpowers_mcp import TraceStore, WorkspaceMemory, validate_plan
from dy_superpowers_mcp.memory import WRITABLE_MEMORY_FILES
from dy_superpowers_mcp.trace import TRACE_ID_PATTERN
from dy_superpowers_mcp.telemetry import initialize_telemetry, mark_otel_error, record_otel_event, tool_span
from dy_superpowers_mcp.odoo_xmlrpc import OdooConfigurationError, OdooReadClient, OdooRPCError, OdooSettings, validate_read_arguments
from dy_superpowers_mcp.knowledge import module_info as knowledge_module_info, search as knowledge_search
from dy_superpowers_mcp.research import read_record, write_record
from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter


TOOLS = {
    "dy_session_initialize": "初始化或恢复当前工作区会话并返回项目记忆索引；需要时使用记忆工具读取完整内容。",
    "dy_validate_plan": "校验分阶段交付或配置计划结构；校验通过不代表获得执行授权。",
    "dy_trace_event": "追加经过字段过滤和脱敏的插件运行追踪事件。",
    "dy_trace_read": "读取当前工作区关联的运行追踪事件。",
    "dy_memory_read": "读取当前工作区允许的项目记忆文件。",
    "dy_memory_search": "使用 Basic Memory 索引搜索当前工作区记忆；服务不可用时回退本地 Markdown。",
    "dy_memory_write": "在用户确认后更新当前工作区项目记忆。",
    "dy_odoo_read": "连接 Odoo 12 并执行受限的 search_read 只读查询。",
    "dy_odoo_record_search": "按 Odoo Forge 的记录搜索命名执行受限的 search_read 只读查询。",
    "dy_odoo_record_read": "读取指定 Odoo 记录的受限只读数据。",
    "dy_odoo_model_fields": "读取 Odoo 模型字段元数据。",
    "dy_odoo_module_list_installed": "读取已安装 Odoo 模块列表；每页最多 100 条，需用 offset 翻页取全量。",
    "dy_odoo_module_info": "读取指定 Odoo 模块信息。",
    "dy_knowledge_module_info": "读取本地 HRP/Odoo 模块知识。",
    "dy_knowledge_search": "按中文或英文关键词搜索本地 HRP/Odoo 模块知识。",
    "dy_research_record_write": "在用户确认后追加模块调研结论并保留历史版本。",
    "dy_research_record_read": "读取当前工作区指定检查键的最新调研结论。",
}

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

TOOL_SCHEMAS = {
    "dy_session_initialize": {
        "type": "object",
        "required": ["trace_id", "source"],
        "properties": {
            "trace_id": {"type": "string"},
            "source": {"type": "string", "enum": sorted(SESSION_SOURCES)},
        },
    },
    "dy_validate_plan": {
        "type": "object",
        "required": ["trace_id", "plan"],
        "properties": {"trace_id": {"type": "string"}, "plan": {"type": "object"}},
    },
    "dy_trace_event": {
        "type": "object",
        "required": ["trace_id", "event"],
        "properties": {"trace_id": {"type": "string"}, "event": {"type": "object"}},
    },
    "dy_trace_read": {
        "type": "object",
        "required": ["trace_id"],
        "properties": {"trace_id": {"type": "string"}},
    },
    "dy_memory_read": {
        "type": "object",
        "required": ["trace_id", "name"],
        "properties": {
            "trace_id": {"type": "string"},
            "name": {"type": "string", "enum": MEMORY_READ_FILES},
        },
    },
    "dy_memory_search": {
        "type": "object",
        "required": ["trace_id", "query"],
        "properties": {"trace_id": {"type": "string"}, "query": {"type": "string"}},
    },
    "dy_memory_write": {
        "type": "object",
        "required": ["trace_id", "name", "content", "confirmed"],
        "properties": {
            "trace_id": {"type": "string"},
            "name": {"type": "string", "enum": MEMORY_WRITE_FILES},
            "content": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
    },
    "dy_odoo_read": {
        "type": "object",
        "required": ["trace_id", "model"],
        "properties": {
            "trace_id": {"type": "string"},
            "model": {"type": "string"},
            "method": {"type": "string", "enum": ["search_read"]},
            "args": {"type": "array"},
            "kwargs": {"type": "object"},
        },
    },
    "dy_odoo_record_search": {
        "type": "object",
        "required": ["trace_id", "model"],
        "properties": {
            "trace_id": {"type": "string"}, "model": {"type": "string"},
            "method": {"type": "string", "enum": ["search_read"]}, "args": {"type": "array"}, "kwargs": {"type": "object"},
        },
    },
    "dy_odoo_record_read": {"type": "object", "required": ["trace_id", "model", "ids"], "properties": {"trace_id": {"type": "string"}, "model": {"type": "string"}, "ids": {"type": "array"}, "fields": {"type": "array"}}},
    "dy_odoo_model_fields": {"type": "object", "required": ["trace_id", "model"], "properties": {"trace_id": {"type": "string"}, "model": {"type": "string"}}},
    "dy_odoo_module_list_installed": {"type": "object", "required": ["trace_id"], "properties": {"trace_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}},
    "dy_odoo_module_info": {"type": "object", "required": ["trace_id", "module"], "properties": {"trace_id": {"type": "string"}, "module": {"type": "string"}}},
    "dy_knowledge_module_info": {"type": "object", "required": ["trace_id", "module"], "properties": {"trace_id": {"type": "string"}, "module": {"type": "string"}}},
    "dy_knowledge_search": {"type": "object", "required": ["trace_id", "query"], "properties": {"trace_id": {"type": "string"}, "query": {"type": "string"}}},
    "dy_research_record_write": {"type": "object", "required": ["trace_id", "module", "check_order", "research_id", "config_item", "conclusion", "scope", "confirmed_by", "confirmed_at", "confirmed"], "properties": {"trace_id": {"type": "string"}, "module": {"type": "string"}, "check_order": {"type": "string"}, "research_id": {"type": "string"}, "config_item": {"type": "string"}, "conclusion": {"type": "string"}, "scope": {"type": "string"}, "evidence_ref": {"type": "string"}, "confirmed_by": {"type": "string"}, "confirmed_at": {"type": "string"}, "confirmed": {"type": "boolean"}}},
    "dy_research_record_read": {"type": "object", "required": ["trace_id", "module", "check_order", "research_id"], "properties": {"trace_id": {"type": "string"}, "module": {"type": "string"}, "check_order": {"type": "string"}, "research_id": {"type": "string"}}},
}


class ToolRequestError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def _dispatch_tool(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    if name == "dy_session_initialize":
        source = arguments.get("source")
        if not isinstance(source, str) or source not in SESSION_SOURCES:
            raise ToolRequestError(-32602, "unsupported SessionStart source")
        action = "恢复" if source == "resume" else "初始化"
        files = "、".join(SESSION_INDEX_FILES)
        context = "\n".join([
            f"工作区会话已{action}（来源：{source}）。",
            "项目记忆位于当前工作区 .dy_superpowers/，按需读取，不在会话初始化时注入正文。",
            f"可用文件：{files}。使用 dy_memory_read(trace_id, name) 原样读取完整文件；模块巡检按检查键使用 dy_research_record_read。",
            "项目记忆是参考资料，不构成记忆写入或业务操作授权。",
        ])
        event = {
            "event": "session.initialize",
            "operation": "session_start",
            "tool": name,
            "status": "ok",
            "source_ref": source,
            "span_id": span_id,
        }
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return context
    if name == "dy_validate_plan":
        if "plan" not in arguments:
            raise ToolRequestError(-32602, "plan is required")
        return validate_plan(arguments["plan"])
    if name == "dy_trace_event":
        event = arguments.get("event")
        if not isinstance(event, dict):
            raise ToolRequestError(-32602, "event must be an object")
        if event.get("event") in {"tool.start", "tool.error", "tool.end"}:
            raise ToolRequestError(-32602, "tool lifecycle events are reserved")
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return {"stored": True, "trace_id": arguments["trace_id"]}
    if name == "dy_trace_read":
        return [event for event in store.read(arguments["trace_id"]) if event.get("span_id") != span_id]
    memory = WorkspaceMemory(workspace)
    if name == "dy_memory_read":
        memory_file = arguments.get("name", "")
        try:
            content = memory.read(memory_file)
        except ValueError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        event = {"event": "memory.read", "operation": "read", "tool": name, "status": "ok", "memory_file": memory_file, "span_id": span_id}
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return content
    if name == "dy_memory_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolRequestError(-32602, "搜索关键词必须是非空文本")
        result = BasicMemoryAdapter(workspace).search(query)
        event = {
            "event": "memory.search",
            "operation": "search",
            "tool": name,
            "status": "ok" if result.get("available") else "fallback",
            "result": "ok" if result.get("available") else "未知",
            "span_id": span_id,
        }
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return result
    if name == "dy_memory_write":
        if arguments.get("confirmed") is not True:
            raise ToolRequestError(-32602, "memory updates require user confirmation")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolRequestError(-32602, "content must be text")
        path = memory.write(arguments.get("name", ""), content, confirmed=True)
        event = {"event": "memory.write", "operation": "write", "tool": name, "status": "ok", "memory_file": path.name, "span_id": span_id}
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return {"written": True, "name": path.name}
    if name in {"dy_odoo_read", "dy_odoo_record_search"}:
        try:
            model, domain, fields, limit, offset = validate_read_arguments(arguments)
            result = OdooReadClient(OdooSettings.from_environment()).search_read(model, domain, fields, limit, offset)
        except OdooConfigurationError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        except OdooRPCError as exc:
            raise ToolRequestError(-32603, str(exc)) from exc
        event = {"event": "odoo.read", "operation": "search_read", "tool": name, "status": "ok", "model": model, "count": len(result), "span_id": span_id}
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return result
    if name in {"dy_odoo_record_read", "dy_odoo_model_fields", "dy_odoo_module_list_installed", "dy_odoo_module_info"}:
        try:
            client = OdooReadClient(OdooSettings.from_environment())
            if name == "dy_odoo_record_read":
                ids, fields = arguments.get("ids"), arguments.get("fields", [])
                if not isinstance(ids, list) or not ids or len(ids) > 100 or not all(isinstance(item, int) and item > 0 for item in ids):
                    raise ToolRequestError(-32602, "ids 必须是 1 到 100 个正整数")
                if not isinstance(fields, list) or not all(isinstance(item, str) and item for item in fields):
                    raise ToolRequestError(-32602, "fields 必须是字符串列表")
                result = client.read_records(arguments["model"], ids, fields)
                operation = "record_read"
                model = arguments["model"]
            elif name == "dy_odoo_model_fields":
                result = client.fields_get(arguments["model"])
                operation = "model_fields"
                model = arguments["model"]
            else:
                model = "ir.module.module"
                if name == "dy_odoo_module_info":
                    result = client.module_records(arguments.get("module"))
                    operation = "module_info"
                else:
                    result = client.module_records(installed_only=True, offset=arguments.get("offset", 0))
                    operation = "module_list_installed"
            event = {"event": "odoo.read", "operation": operation, "tool": name, "status": "ok", "model": model, "count": len(result) if isinstance(result, list) else len(result), "span_id": span_id}
            store.append(arguments["trace_id"], event)
            record_otel_event(otel_span, event)
            return result
        except OdooConfigurationError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        except OdooRPCError as exc:
            raise ToolRequestError(-32603, str(exc)) from exc
    if name == "dy_knowledge_module_info":
        return knowledge_module_info(arguments.get("module", ""))
    if name == "dy_knowledge_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolRequestError(-32602, "query 必须是非空文本")
        return knowledge_search(query)
    if name == "dy_research_record_write":
        try:
            result = write_record(workspace, arguments)
        except PermissionError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        except ValueError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        event = {"event": "research.write", "operation": "confirmed_record", "tool": name, "status": "ok", "module": arguments["module"], "check_order": arguments["check_order"], "research_id": arguments["research_id"], "span_id": span_id}
        store.append(arguments["trace_id"], event)
        record_otel_event(otel_span, event)
        return result
    if name == "dy_research_record_read":
        try:
            result = read_record(workspace, arguments["module"], arguments["check_order"], arguments["research_id"])
        except ValueError as exc:
            raise ToolRequestError(-32602, str(exc)) from exc
        return result or {"found": False, "check_key": f"{arguments['module']}:{arguments['check_order']}:{arguments['research_id']}"}
    raise ToolRequestError(-32602, f"unknown tool: {name}")


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
        try:
            start_event = {"event": "tool.start", "tool": name, "status": "running", "span_id": span_id}
            store.append(trace_id, start_event)
            record_otel_event(otel_span, start_event)
            started = True
            result = _dispatch_tool(name, arguments, workspace, store, span_id, otel_span)
            status = "ok"
        except ToolRequestError as exc:
            error = {"code": exc.code, "message": str(exc)}
            error_type = "invalid_request"
        except Exception as exc:
            error = {"code": -32603, "message": "tool execution failed"}
            error_type = type(exc).__name__
        finally:
            if started:
                if error is not None:
                    error_event = {"event": "tool.error", "tool": name, "status": "error", "error_type": error_type, "span_id": span_id}
                    try:
                        store.append(trace_id, error_event)
                    except Exception:
                        pass
                    record_otel_event(otel_span, error_event)
                    mark_otel_error(otel_span, error_type)
                end_event = {"event": "tool.end", "tool": name, "status": status, "duration_ms": round((time.monotonic() - start) * 1000, 2), "span_id": span_id}
                try:
                    store.append(trace_id, end_event)
                except Exception:
                    if error is None:
                        error = {"code": -32603, "message": "tool execution failed"}
                record_otel_event(otel_span, end_event)
    return result, error


def _mcp_result(value: object) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    return {"content": [{"type": "text", "text": text}]}


def handle(request: dict, workspace: Path) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "dy_superpowers", "version": "0.1.0"}})
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": [{"name": name, "description": description, "inputSchema": TOOL_SCHEMAS[name]} for name, description in TOOLS.items()]})
    if method != "tools/call":
        return response(request_id, error={"code": -32601, "message": f"method not found: {method}"})
    params = request.get("params")
    if not isinstance(params, dict):
        return response(request_id, error={"code": -32602, "message": "params must be an object"})
    value, error = execute_tool(params.get("name"), params.get("arguments") or {}, workspace)
    if error is not None:
        return response(request_id, error=error)
    return response(request_id, _mcp_result(value))


def run_fastmcp() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("dy_superpowers")
    workspace = Path.cwd()

    def invoke(name: str, arguments: dict):
        result, error = execute_tool(name, arguments, workspace)
        if error is not None:
            raise ValueError(error["message"])
        return result

    @server.tool()
    def dy_session_initialize(trace_id: str, source: str) -> str:
        """初始化或恢复当前工作区会话并返回项目记忆索引。"""
        return invoke("dy_session_initialize", {"trace_id": trace_id, "source": source})

    @server.tool()
    def dy_validate_plan(trace_id: str, plan: dict) -> dict:
        """校验分阶段交付或配置计划结构，不授予执行授权。"""
        return invoke("dy_validate_plan", {"trace_id": trace_id, "plan": plan})

    @server.tool()
    def dy_trace_event(trace_id: str, event: dict) -> dict:
        """追加经过字段过滤和脱敏的插件运行追踪事件。"""
        return invoke("dy_trace_event", {"trace_id": trace_id, "event": event})

    @server.tool()
    def dy_trace_read(trace_id: str) -> list[dict]:
        """读取当前工作区关联的运行追踪事件。"""
        return invoke("dy_trace_read", {"trace_id": trace_id})

    @server.tool()
    def dy_memory_read(trace_id: str, name: str) -> str:
        """读取当前工作区允许的项目记忆文件。"""
        return invoke("dy_memory_read", {"trace_id": trace_id, "name": name})

    @server.tool()
    def dy_memory_search(trace_id: str, query: str) -> dict:
        """搜索当前工作区记忆，Basic Memory 不可用时回退 Markdown 关键词搜索。"""
        return invoke("dy_memory_search", {"trace_id": trace_id, "query": query})

    @server.tool()
    def dy_memory_write(trace_id: str, name: str, content: str, confirmed: bool) -> dict:
        """仅在用户确认后更新当前工作区项目记忆。"""
        return invoke("dy_memory_write", {"trace_id": trace_id, "name": name, "content": content, "confirmed": confirmed})

    @server.tool()
    def dy_odoo_read(trace_id: str, model: str, method: str = "search_read", args: list | None = None, kwargs: dict | None = None) -> list[dict]:
        """执行 Odoo 12 的受限 search_read 只读查询。"""
        return invoke("dy_odoo_read", {"trace_id": trace_id, "model": model, "method": method, "args": args or [[]], "kwargs": kwargs or {}})

    @server.tool()
    def dy_odoo_record_search(trace_id: str, model: str, method: str = "search_read", args: list | None = None, kwargs: dict | None = None) -> list[dict]:
        """按 Odoo Forge 的记录搜索命名执行受限 search_read，只读。"""
        return invoke("dy_odoo_record_search", {"trace_id": trace_id, "model": model, "method": method, "args": args or [[]], "kwargs": kwargs or {}})

    @server.tool()
    def dy_odoo_record_read(trace_id: str, model: str, ids: list[int], fields: list[str] | None = None) -> list[dict]:
        """读取最多 100 条指定 Odoo 记录。"""
        return invoke("dy_odoo_record_read", {"trace_id": trace_id, "model": model, "ids": ids, "fields": fields or []})

    @server.tool()
    def dy_odoo_model_fields(trace_id: str, model: str) -> dict:
        """读取 Odoo 模型字段元数据。"""
        return invoke("dy_odoo_model_fields", {"trace_id": trace_id, "model": model})

    @server.tool()
    def dy_odoo_module_list_installed(trace_id: str, offset: int = 0) -> list[dict]:
        """读取已安装 Odoo 模块；每页最多 100 条，用 offset 翻页取全量。"""
        return invoke("dy_odoo_module_list_installed", {"trace_id": trace_id, "offset": offset})

    @server.tool()
    def dy_odoo_module_info(trace_id: str, module: str) -> list[dict]:
        """读取指定 Odoo 模块信息。"""
        return invoke("dy_odoo_module_info", {"trace_id": trace_id, "module": module})

    @server.tool()
    def dy_knowledge_module_info(trace_id: str, module: str) -> dict:
        """读取本地 HRP/Odoo 模块知识。"""
        return invoke("dy_knowledge_module_info", {"trace_id": trace_id, "module": module})

    @server.tool()
    def dy_knowledge_search(trace_id: str, query: str) -> dict:
        """按中文或英文关键词搜索本地 HRP/Odoo 模块知识。"""
        return invoke("dy_knowledge_search", {"trace_id": trace_id, "query": query})

    @server.tool()
    def dy_research_record_write(trace_id: str, module: str, check_order: str, research_id: str, config_item: str, conclusion: str, scope: str, confirmed_by: str, confirmed_at: str, confirmed: bool, evidence_ref: str = "") -> dict:
        """用户确认后，将模块调研结论按检查键写入当前工作区，并保留旧版本。"""
        return invoke("dy_research_record_write", {"trace_id": trace_id, "module": module, "check_order": check_order, "research_id": research_id, "config_item": config_item, "conclusion": conclusion, "scope": scope, "confirmed_by": confirmed_by, "confirmed_at": confirmed_at, "confirmed": confirmed, "evidence_ref": evidence_ref})

    @server.tool()
    def dy_research_record_read(trace_id: str, module: str, check_order: str, research_id: str) -> dict:
        """按模块和检查键读取当前工作区最新的已确认调研结论。"""
        return invoke("dy_research_record_read", {"trace_id": trace_id, "module": module, "check_order": check_order, "research_id": research_id})

    server.run(transport="stdio")


def main() -> None:
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        pass
    else:
        run_fastmcp()
        return

    workspace = Path.cwd()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = parse_request(line)
        try:
            if "method" not in request:
                result = request
            else:
                result = handle(request, workspace)
            if result is not None:
                sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
                sys.stdout.flush()
        except Exception:
            request_id = request.get("id") if isinstance(request, dict) else None
            sys.stdout.write(json.dumps(response(request_id, error={"code": -32603, "message": "internal error"})) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
