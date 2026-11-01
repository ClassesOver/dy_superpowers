#!/usr/bin/env python3
"""stdio MCP server for the dy_superpowers workspace runtime."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if __name__ == '__main__':
    try:
        import mcp
    except ModuleNotFoundError as exc:
        if exc.name == 'mcp':
            raise SystemExit('缺少 MCP SDK，请使用插件锁定的 uv 环境安装依赖。') from exc
        raise

from dy_superpowers_mcp.trace import TRACE_CODE_PATTERN, TRACE_CONTEXT_FIELDS, TRACE_ID_PATTERN, TraceStore
# TOOL_REGISTRY / TOOL_SCHEMAS / TOOLS 作为服务对外的工具清单导出。
from dy_superpowers_mcp.tools import TOOL_REGISTRY, TOOL_SCHEMAS, TOOLS, dispatch_tool, enabled_tools  # noqa: F401
from dy_superpowers_mcp.tools.runtime import ToolRequestError, append_trace, clear_odoo_client, trace_scope

PLUGIN_VERSION = json.loads((Path(__file__).resolve().parents[2] / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]

# 安装即用：连接与令牌允许放在工作区 .env（已被 .gitignore 排除），服务启动时读取。
# 只认白名单键、不覆盖已设置的环境变量、不回显任何值。
ENV_FILE_KEYS = (
    "APP_URL", "APP_USER", "APP_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "DY_WIKI_API_URL", "DY_WIKI_TOKEN", "DY_WIKI_PROJECT_ID", "DY_WIKI_GROUP_ID", "DY_WIKI_PROJECT_NAME",
    "DY_ODOO_ENVIRONMENT",
    "DY_KNOWLEDGE_EMBEDDING_PROVIDER", "DY_KNOWLEDGE_EMBEDDING_URL",
    "DY_KNOWLEDGE_EMBEDDING_TOKEN", "DY_KNOWLEDGE_EMBEDDING_MODEL",
)


def load_workspace_env(workspace: Path) -> list[str]:
    """读取工作区 .env 中的白名单配置；返回加载的键名列表，值不进入日志或返回值。"""
    env_file = Path(workspace) / ".env"
    if not env_file.is_file() or env_file.is_symlink():
        return []
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    loaded = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key not in ENV_FILE_KEYS or os.environ.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        if value:
            os.environ[key] = value
            loaded.append(key)
    return loaded


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


def execute_tool(name: str, arguments: object, workspace: Path) -> tuple[object, dict | None]:
    if not isinstance(arguments, dict):
        return None, {"code": -32602, "message": "tool arguments must be an object"}
    trace_id = arguments.get("trace_id")
    if not isinstance(trace_id, str) or not TRACE_ID_PATTERN.fullmatch(trace_id):
        return None, {"code": -32602, "message": "trace_id must contain 1-128 letters, digits, underscores, or hyphens"}
    trace_context = arguments.get('trace_context', {})
    if (not isinstance(trace_context, dict) or any(
        key not in TRACE_CONTEXT_FIELDS or not isinstance(value, str) or not TRACE_CODE_PATTERN.fullmatch(value)
        for key, value in trace_context.items()
    )):
        return None, {"code": -32602, "message": "trace_context 只能包含技能、模块、检查项、范围和版本的非空标识"}

    store = TraceStore(workspace)
    span_id = uuid.uuid4().hex
    start = time.monotonic()
    started = False
    status = "error"
    result = None
    error = None
    error_type = None
    error_code = None
    with trace_scope(span_id, trace_context):
        try:
            start_event = {"event": "tool.start", "tool": name, "status": "running", "span_id": span_id}
            append_trace(store, trace_id, start_event)
            started = True
            result = dispatch_tool(name, arguments, workspace, store, span_id)
            status = "ok"
        except ToolRequestError as exc:
            error = {"code": exc.code, "message": str(exc)}
            error_type = "invalid_request" if exc.code == -32602 else "tool_error"
            error_code = exc.code
        except Exception as exc:
            error = {"code": -32603, "message": "tool execution failed"}
            error_type = type(exc).__name__
            error_code = -32603
        finally:
            if started:
                if error is not None:
                    error_event = {"event": "tool.error", "tool": name, "status": "error", "error_type": error_type,
                                   "error_code": error_code, "span_id": span_id}
                    try:
                        append_trace(store, trace_id, error_event)
                    except Exception:
                        pass
                end_event = {"event": "tool.end", "tool": name, "status": status, "duration_ms": round((time.monotonic() - start) * 1000, 2), "span_id": span_id}
                try:
                    append_trace(store, trace_id, end_event)
                except Exception:
                    if error is None:
                        error = {"code": -32603, "message": "tool execution failed"}
    return result, error


def _mcp_result(value: object) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def handle(request: dict, workspace: Path) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "dy_superpowers", "version": PLUGIN_VERSION}})
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": [{"name": name, "description": spec[0], "inputSchema": spec[1]} for name, spec in enabled_tools()]})
    if method != "tools/call":
        return response(request_id, error={"code": -32601, "message": f"method not found: {method}"})
    params = request.get("params")
    if not isinstance(params, dict):
        return response(request_id, error={"code": -32602, "message": "params must be an object"})
    value, error = execute_tool(params.get("name"), params.get("arguments") or {}, workspace)
    if error is not None:
        return response(request_id, error=error)
    return response(request_id, _mcp_result(value))


def resolve_workspace(argv=None) -> Path:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="dy_superpowers 工作区 MCP")
    parser.add_argument("--workspace", default=os.environ.get("DY_WORKSPACE_ROOT"))
    configured = parser.parse_args(argv).workspace
    workspace = Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()
    plugin = Path(__file__).resolve().parents[2]
    if not workspace.is_dir() or workspace == plugin or plugin in workspace.parents:
        raise ValueError("工作区必须是存在的项目目录，不能是插件安装目录")
    if not configured:
        sys.stderr.write("工作区使用启动目录；可通过 --workspace 或 DY_WORKSPACE_ROOT 显式指定。\n")
    return workspace


def run_mcp(workspace: Path) -> None:
    import asyncio
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.shared.exceptions import MCPError
    from mcp import types

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(name=name, description=spec[0], input_schema=spec[1])
                                           for name, spec in enabled_tools()])

    async def call_tool(context, params):
        result, error = await asyncio.to_thread(execute_tool, params.name, params.arguments or {}, workspace)
        if error:
            raise MCPError(error["code"], error["message"])
        return types.CallToolResult(content=[types.TextContent(type="text", text=_mcp_result(result)["content"][0]["text"])])

    server = Server("dy_superpowers", version=PLUGIN_VERSION, on_list_tools=list_tools, on_call_tool=call_tool)
    # SDK 的自动遥测可能包含请求正文；插件自身不启用外部遥测，只保留本地 JSONL trace。
    try:
        from mcp.server._otel import OpenTelemetryMiddleware
        server.middleware = [item for item in server.middleware if not isinstance(item, OpenTelemetryMiddleware)]
    except ImportError:
        # SDK 内部模块路径变化时，按模块名兜底识别遥测中间件。
        server.middleware = [item for item in server.middleware
                             if "otel" not in type(item).__module__.lower()
                             and "telemetry" not in type(item).__module__.lower()]

    async def run():
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    try:
        asyncio.run(run())
    finally:
        clear_odoo_client()


def main() -> None:
    workspace = resolve_workspace()
    load_workspace_env(workspace)
    try:
        run_mcp(workspace)
    except ModuleNotFoundError as exc:
        if isinstance(exc.name, str) and exc.name == "mcp":
            raise SystemExit("缺少 MCP SDK，请使用插件锁定的 uv 环境安装依赖。") from exc
        raise


if __name__ == "__main__":
    main()
