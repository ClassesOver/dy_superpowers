"""测试辅助：与 SDK 入口相同的工具清单与结果封装，不经过手写 JSON-RPC。"""
from pathlib import Path

import server


def listed_tools():
    """当前对外可见的工具定义，与 run_mcp 的 list_tools 来源相同。"""
    return [{'name': name, 'description': spec[0], 'inputSchema': spec[1]} for name, spec in server.enabled_tools()]


def call_tool(name, arguments, workspace):
    """执行工具并按 MCP 文本内容封装结果；错误以 {'error': ...} 返回。"""
    value, error = server.execute_tool(name, arguments, Path(workspace))
    return {'error': error} if error is not None else {'result': server._mcp_result(value)}
