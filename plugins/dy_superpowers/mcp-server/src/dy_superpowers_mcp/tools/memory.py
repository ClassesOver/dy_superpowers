"""工作区记忆、主题笔记与本地 trace 工具。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import basic_memory
from ..memory import MEMORY_FILES, WRITABLE_MEMORY_FILES, WorkspaceMemory
from ..trace import TraceStore
from .runtime import ToolRequestError, append_trace, invalid_arguments


def _handle_trace_event(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    event = arguments.get('event')
    if not isinstance(event, dict):
        raise ToolRequestError(-32602, 'event must be an object')
    event_name = event.get('event')
    if not isinstance(event_name, str) or not event_name.strip() or len(event_name) > 128:
        raise ToolRequestError(-32602, 'event.event 必须是 1 到 128 个字符的事件名')
    if event_name in {'tool.start', 'tool.error', 'tool.end', 'odoo.query', 'odoo.read'}:
        raise ToolRequestError(-32602, '工具生命周期与 Odoo 读取事件由服务端生成')
    # 自报业务事件不得伪造工具身份、本地/远程关联标识、调用状态或操作名。
    event = {key: value for key, value in event.items()
             if key not in {'workspace_id', 'workflow_id', 'call_id',
                            'parent_span_id', 'tool', 'span_id', 'status', 'operation'}}
    event.update(tool=name, span_id=span_id)
    stored = append_trace(store, arguments['trace_id'], event)
    return {'stored': stored, 'trace_id': arguments['trace_id']}


def _handle_trace_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    return [event for event in store.read(arguments['trace_id']) if event.get('span_id') != span_id]


def _handle_memory_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    memory = WorkspaceMemory(workspace)
    memory_file = arguments.get('name', '')
    with invalid_arguments():
        content = memory.read(memory_file)
    event = {'event': 'memory.read', 'operation': 'read', 'tool': name, 'status': 'ok', 'memory_file': memory_file,
             'content_bytes': len(content.encode('utf-8')), 'memory_content': content, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return content


def _handle_memory_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    query = arguments.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ToolRequestError(-32602, '搜索关键词必须是非空文本')
    query = query.strip()
    if query.startswith('-'):
        raise ToolRequestError(-32602, '搜索关键词不能以连字符开头')
    with invalid_arguments():
        module = None
        trace_context = arguments.get('trace_context')
        if isinstance(trace_context, dict):
            module = trace_context.get('module')
        result = basic_memory.BasicMemoryAdapter(workspace).search(query, module=module)
    results = result.get('results')
    event = {'event': 'memory.search', 'operation': 'search', 'tool': name,
             'status': 'ok' if result.get('available') else 'fallback',
             'result': 'ok' if result.get('available') else '未知',
             'backend': result.get('backend'), 'result_count': len(results) if isinstance(results, list) else 0,
             'has_more': bool(result.get('has_more', False)), 'query': query,
             'memory_content': json.dumps(results, ensure_ascii=False, separators=(',', ':'))
             if isinstance(results, list) else '', 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


def _handle_memory_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    memory = WorkspaceMemory(workspace)
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, 'memory updates require user confirmation')
    content = arguments.get('content')
    if not isinstance(content, str):
        raise ToolRequestError(-32602, 'content must be text')
    with invalid_arguments():
        path = memory.write(arguments.get('name', ''), content, confirmed=True, expected_content=arguments.get('expected_content'))
    event = {'event': 'memory.write', 'operation': 'write', 'tool': name, 'status': 'ok', 'memory_file': path.name,
             'content_bytes': len(content.encode('utf-8')), 'memory_content': content, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return {'written': True, 'name': path.name}


TOOLS = {
    'dy_trace_event': (
        '追加经过字段过滤和脱敏的插件运行追踪事件。',
        {'type': 'object',
         'required': ['trace_id', 'event'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'event': {'type': 'object'}}},
        _handle_trace_event,
    ),
    'dy_trace_read': (
        '读取当前工作区关联的运行追踪事件。',
        {'type': 'object',
         'required': ['trace_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}}},
        _handle_trace_read,
    ),
    'dy_memory_read': (
        '读取当前工作区允许的项目记忆文件。',
        {'type': 'object',
         'required': ['trace_id', 'name'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'name': {'type': 'string',
                                 'enum': list(MEMORY_FILES)}}},
        _handle_memory_read,
    ),
    'dy_memory_search': (
        '使用 Basic Memory 索引搜索当前工作区记忆；服务不可用时回退本地 Markdown。',
        {'type': 'object',
         'required': ['trace_id', 'query'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'query': {'type': 'string'}}},
        _handle_memory_search,
    ),
    'dy_memory_write': (
        '用户确认后更新工作区记忆；修改已有文件必须提供最近读取的 expected_content，冲突时重新合并。',
        {'type': 'object',
         'required': ['trace_id', 'name', 'content', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'name': {'type': 'string',
                                 'enum': sorted(WRITABLE_MEMORY_FILES)},
                        'content': {'type': 'string'},
                        'confirmed': {'type': 'boolean'},
                        'expected_content': {'type': 'string', 'description': '更新已有文件必须提供最近读取的完整原文；冲突时重新读取合并。'}}},
        _handle_memory_write,
    ),
}
