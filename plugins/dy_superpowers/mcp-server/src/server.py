#!/usr/bin/env python3
"""stdio MCP server for the dy_superpowers workspace runtime."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).parent))

from dy_superpowers_mcp import TraceStore, WorkspaceMemory
from dy_superpowers_mcp.memory import WRITABLE_MEMORY_FILES, list_notes, module_memory_files, module_memory_path, read_note, validate_module, write_note
from dy_superpowers_mcp.trace import TRACE_CODE_PATTERN, TRACE_CONTEXT_FIELDS, TRACE_ID_PATTERN
from dy_superpowers_mcp.telemetry import current_trace_context, initialize_telemetry, mark_otel_error, operation_span, record_otel_event, tool_span
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, OdooReadClient, OdooRPCError, OdooSettings, validate_read_arguments
from dy_superpowers_mcp.wiki_sync import sync_wiki
from dy_superpowers_mcp.project_wiki import configure_project, stage_wiki
from dy_superpowers_mcp.knowledge import module_info as knowledge_module_info, search as knowledge_search
from dy_superpowers_mcp.research import read_module_records, read_record, write_record
from dy_superpowers_mcp.research_progress import calculate_progress, export_unanswered_template
from dy_superpowers_mcp.reports import export_report_pdf, read_report, save_report
from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
from dy_superpowers_mcp.inspection import validate_inspection


MEMORY_READ_FILES = [
    "preferences.md",
    "profile.md",
    "decisions.md",
    "delivery-register.md",
    "skill-candidates.md",
]
MEMORY_WRITE_FILES = sorted(WRITABLE_MEMORY_FILES)
SESSION_INDEX_FILES = tuple(MEMORY_READ_FILES)
SESSION_SOURCES = {"startup", "resume", "clear", "compact", "fork"}

# 安装即用：连接与令牌允许放在工作区 .env（已被 .gitignore 排除），服务启动时读取。
# 只认白名单键、不覆盖已设置的环境变量、不回显任何值。
ENV_FILE_KEYS = (
    "APP_URL", "APP_USER", "APP_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "DY_WIKI_API_URL", "DY_WIKI_TOKEN", "DY_WIKI_PROJECT_ID", "DY_WIKI_GROUP_ID", "DY_WIKI_PROJECT_NAME",
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


def append_trace(store: TraceStore, trace_id: str, event: dict) -> bool:
    try:
        store.append(trace_id, {**current_trace_context(), **event})
        return True
    except OSError as exc:
        sys.stderr.write(f"本地操作追踪写入失败（{type(exc).__name__}），请检查日志目录。\n")
        return False


_odoo_lock = RLock()
_odoo_cached = None


def _clear_odoo_client():
    global _odoo_cached
    with _odoo_lock:
        if _odoo_cached is not None:
            try:
                _odoo_cached[2].close()
            except Exception:
                pass
            _odoo_cached = None


def _odoo_client(workspace):
    global _odoo_cached
    settings = OdooSettings.from_environment()
    key = (Path(workspace).resolve(), settings)
    with _odoo_lock:
        if _odoo_cached is None or _odoo_cached[:2] != key:
            _clear_odoo_client()
            _odoo_cached = (*key, OdooReadClient(settings))
        return _odoo_cached[2]


def _handle_session_initialize(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    source = arguments.get('source')
    if not isinstance(source, str) or source not in SESSION_SOURCES:
        raise ToolRequestError(-32602, 'unsupported SessionStart source')
    action = '恢复' if source == 'resume' else '初始化'
    files = '、'.join(SESSION_INDEX_FILES)
    context = '\n'.join([f'工作区会话已{action}（来源：{source}）。', f'追踪工作区标识：{store.workspace_id}。', '项目记忆位于当前工作区 .dy_superpowers/，按需读取，不在会话初始化时注入正文。', '恢复未完成调研时，先按需读取 delivery-register.md 的待办，再按模块和检查键复核最新确认结论。', f'可用文件：{files}。使用 dy_memory_read(trace_id, name) 读取项目记忆；模块调研记忆使用 dy_module_memory_read(trace_id, module, check_order, research_id, scope_id)。', '项目记忆是参考资料，不构成记忆写入或业务操作授权。'])
    event = {'event': 'session.initialize', 'operation': 'session_start', 'tool': name, 'status': 'ok', 'source_ref': source, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return context

def _handle_inspection_validate(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    result = validate_inspection(workspace, arguments.get('report'))
    expected = arguments.get('report', {}).get('expected_checks', []) if isinstance(arguments.get('report'), dict) else []
    event = {'event': 'inspection.validate', 'operation': 'validate', 'tool': name, 'status': 'ok',
             'result': result['gate'], 'valid': result['valid'], 'error_count': len(result['errors']),
             'expected_count': len(expected) if isinstance(expected, list) else 0, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_inspection_report_save(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, '保存巡检报告需要用户明确确认')
    report = arguments.get('report')
    if not isinstance(report, dict):
        raise ToolRequestError(-32602, 'report 必须是对象')
    modules = {item.get('module') for item in report.get('expected_checks', [])
               if isinstance(item, dict) and isinstance(item.get('module'), str)}
    if len(modules) != 1:
        raise ToolRequestError(-32602, '一个报告只能包含一个模块')
    module = next(iter(modules))
    result = validate_inspection(workspace, report)
    if not result['valid']:
        raise ToolRequestError(-32602, '巡检报告校验失败: ' + json.dumps(result['errors'], ensure_ascii=False))
    try:
        report_id, path, pdf_path = save_report(workspace, module, report, result)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'inspection.report.save', 'operation': 'save', 'tool': name, 'status': 'ok',
             'module': module, 'report_id': report_id, 'result': result['gate'],
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return {'saved': True, 'module': module, 'report_id': report_id,
            'path': str(path.relative_to(workspace)), 'pdf_path': str(pdf_path.relative_to(workspace)),
            'gate': result['gate'], 'validated_at': result['validated_at']}

def _handle_inspection_report_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    module = arguments.get('module')
    report_id = arguments.get('report_id')
    if (not isinstance(module, str) or not module.strip()
            or not isinstance(report_id, str) or not report_id.strip()):
        raise ToolRequestError(-32602, 'module 和 report_id 必须是非空文本')
    try:
        content = read_report(workspace, module, report_id)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'inspection.report.read', 'operation': 'read', 'tool': name, 'status': 'ok',
             'module': module, 'report_id': report_id,
             'content_bytes': len(content.encode('utf-8')), 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return content

def _handle_inspection_report_export_pdf(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    module = arguments.get('module')
    report_id = arguments.get('report_id')
    if (not isinstance(module, str) or not module.strip()
            or not isinstance(report_id, str) or not report_id.strip()):
        raise ToolRequestError(-32602, 'module 和 report_id 必须是非空文本')
    try:
        pdf_path, created = export_report_pdf(workspace, module, report_id)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'inspection.report.export_pdf', 'operation': 'export', 'tool': name,
             'status': 'ok', 'module': module, 'report_id': report_id,
             'result': '已生成' if created else '已存在', 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return {'exported': True, 'module': module, 'report_id': report_id, 'created': created,
            'pdf_path': str(pdf_path.relative_to(workspace))}

def _handle_notes_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, 'wiki 记忆写入需要用户明确确认')
    try:
        path = write_note(workspace, arguments.get('slug', ''), arguments.get('title', ''),
                          arguments.get('body', ''), arguments.get('tags', []),
                          confirmed=True, source_refs=arguments.get('source_refs', []),
                          module=arguments.get('module'), research_topic=arguments.get('research_topic', False),
                          expected_content=arguments.get('expected_content'))
    except (ValueError, PermissionError, TypeError) as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    content = path.read_text(encoding='utf-8')
    slug = path.relative_to(workspace / '.dy_superpowers' / 'notes').with_suffix('').as_posix()
    event = {'event': 'notes.write', 'operation': 'write', 'tool': name, 'status': 'ok',
             'memory_file': str(path.relative_to(workspace)), 'memory_content': content,
             'content_bytes': len(content.encode('utf-8')),
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return {'written': True, 'slug': slug, 'path': str(path.relative_to(workspace))}


def _handle_notes_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        content = read_note(workspace, arguments.get('slug', ''))
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    if not content:
        return {'found': False, 'slug': arguments.get('slug', ''), 'notes': list_notes(workspace)}
    event = {'event': 'notes.read', 'operation': 'read', 'tool': name, 'status': 'ok',
             'memory_file': '.dy_superpowers/notes/' + arguments.get('slug', '') + '.md',
             'memory_content': content, 'content_bytes': len(content.encode('utf-8')), 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return content


def _handle_notes_list(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    return {'notes': list_notes(workspace)}


def _handle_project_configure(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        result = configure_project(workspace, arguments['name'], arguments['code'],
                                   confirmed=arguments.get('confirmed', False))
    except (ValueError, PermissionError) as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    return result


def _handle_wiki_build(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, '本地 Wiki 构建需用户确认；不会推送远端')
    try:
        manifest = stage_wiki(workspace)
    except (ValueError, OSError) as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    return {'built': True, 'project': manifest['project'], 'pages': len(manifest['entries']),
            'overview_slug': manifest['overview_slug'], 'published': False}


def _handle_wiki_sync(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, 'wiki 同步是外发动作，需要用户明确确认')
    try:
        result = sync_wiki(workspace, confirmed=True)
    # WikiSyncError 是 ValueError 子类；OSError 覆盖清单页面文件缺失等本地读取失败。
    except (ValueError, OSError, PermissionError) as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'wiki.sync', 'operation': 'sync', 'tool': name,
             'status': 'ok' if result.get('synced') else 'noop',
             'count': result.get('pages', 0), 'result': 'ok' if result.get('synced') else '未知',
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result


def _handle_trace_event(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
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
             if key not in {'workspace_id', 'workflow_id', 'otel_trace_id', 'otel_span_id', 'call_id',
                            'parent_span_id', 'tool', 'span_id', 'status', 'operation'}}
    event.update(tool=name, span_id=span_id)
    stored = append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event, promote=False)
    return {'stored': stored, 'trace_id': arguments['trace_id']}

def _handle_trace_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    return [event for event in store.read(arguments['trace_id']) if event.get('span_id') != span_id]

def _handle_memory_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    memory = WorkspaceMemory(workspace)
    memory_file = arguments.get('name', '')
    try:
        content = memory.read(memory_file)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'memory.read', 'operation': 'read', 'tool': name, 'status': 'ok', 'memory_file': memory_file,
             'content_bytes': len(content.encode('utf-8')), 'memory_content': content, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return content

def _handle_memory_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    query = arguments.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ToolRequestError(-32602, '搜索关键词必须是非空文本')
    query = query.strip()
    if query.startswith('-'):
        raise ToolRequestError(-32602, '搜索关键词不能以连字符开头')
    try:
        module = None
        trace_context = arguments.get('trace_context')
        if isinstance(trace_context, dict):
            module = trace_context.get('module')
        result = BasicMemoryAdapter(workspace).search(query, module=module)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    results = result.get('results')
    event = {'event': 'memory.search', 'operation': 'search', 'tool': name,
             'status': 'ok' if result.get('available') else 'fallback',
             'result': 'ok' if result.get('available') else '未知',
             'backend': result.get('backend'), 'result_count': len(results) if isinstance(results, list) else 0,
             'has_more': bool(result.get('has_more', False)), 'query': query,
             'memory_content': json.dumps(results, ensure_ascii=False, separators=(',', ':'))
             if isinstance(results, list) else '', 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_memory_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    memory = WorkspaceMemory(workspace)
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, 'memory updates require user confirmation')
    content = arguments.get('content')
    if not isinstance(content, str):
        raise ToolRequestError(-32602, 'content must be text')
    try:
        path = memory.write(arguments.get('name', ''), content, confirmed=True, expected_content=arguments.get('expected_content'))
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'memory.write', 'operation': 'write', 'tool': name, 'status': 'ok', 'memory_file': path.name,
             'content_bytes': len(content.encode('utf-8')), 'memory_content': content, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return {'written': True, 'name': path.name}

def _handle_odoo_record_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        model, domain, fields, limit, offset, context = validate_read_arguments(arguments)
        query_event = {'event': 'odoo.query', 'operation': 'search_read', 'tool': name, 'status': 'running',
                       'model': model, 'domain': domain, 'domain_term_count': len(domain),
                       'field_count': len(fields), 'limit': limit, 'offset': offset,
                       'active_test': context.get('active_test', True),
                       'trace_context': arguments.get('trace_context', {}), 'span_id': span_id}
        append_trace(store, arguments['trace_id'], query_event)
        record_otel_event(otel_span, query_event)
        client = _odoo_client(workspace)
        result = client.search_read(model, domain, fields, limit, offset, **({'context': context} if context else {}))
    except OdooConfigurationError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except OdooRPCError as exc:
        _clear_odoo_client()
        raise ToolRequestError(-32603, str(exc)) from exc
    event = {'event': 'odoo.read', 'operation': 'search_read', 'tool': name, 'status': 'ok', 'model': model,
             'count': len(result), 'result_count': len(result), 'field_count': len(fields),
             'domain_term_count': len(domain), 'limit': limit, 'offset': offset, 'domain': domain,
             'active_test': context.get('active_test', True),
             'trace_context': arguments.get('trace_context', {}), 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_odoo_record_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        client = _odoo_client(workspace)
        metadata = {}
        if name == 'dy_odoo_record_read':
            ids, fields = (arguments.get('ids'), arguments.get('fields', []))
            if not isinstance(ids, list) or not ids or len(ids) > 100 or (not all((isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in ids))):
                raise ToolRequestError(-32602, 'ids 必须是 1 到 100 个正整数')
            if not isinstance(fields, list) or not all((isinstance(item, str) and item for item in fields)):
                raise ToolRequestError(-32602, 'fields 必须是字符串列表')
            result = client.read_records(arguments['model'], ids, fields)
            operation = 'record_read'
            model = arguments['model']
            metadata = {'requested_count': len(ids), 'field_count': len(fields)}
        elif name == 'dy_odoo_model_fields':
            result = client.fields_get(arguments['model'])
            operation = 'model_fields'
            model = arguments['model']
            metadata = {'field_count': len(result)}
        else:
            model = 'ir.module.module'
            if name == 'dy_odoo_module_info':
                module = arguments.get('module')
                result = client.module_records(module)
                operation = 'module_info'
                metadata = {'module': module, 'limit': 100, 'offset': 0}
            else:
                offset = arguments.get('offset', 0)
                result = client.module_records(installed_only=True, offset=offset)
                operation = 'module_list_installed'
                metadata = {'limit': 100, 'offset': offset, 'domain_term_count': 1, 'field_count': 5}
        event = {'event': 'odoo.read', 'operation': operation, 'tool': name, 'status': 'ok',
                 'model': model, 'count': len(result), 'result_count': len(result),
                 **metadata, 'trace_context': arguments.get('trace_context', {}), 'span_id': span_id}
        append_trace(store, arguments['trace_id'], event)
        record_otel_event(otel_span, event)
        return result
    except OdooConfigurationError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except OdooRPCError as exc:
        _clear_odoo_client()
        raise ToolRequestError(-32603, str(exc)) from exc

def _handle_knowledge_module_info(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    return knowledge_module_info(arguments.get('module', ''))

def _handle_knowledge_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    query = arguments.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ToolRequestError(-32602, 'query 必须是非空文本')
    return knowledge_search(query)

def _handle_research_record_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    record_arguments = dict(arguments)
    sender_id = os.environ.get('FEISHU_CODEX_BOT_SENDER_USER_ID', '').strip()
    record_arguments['confirmed_by'] = sender_id or str(arguments.get('confirmed_by') or '未提供')
    record_arguments['confirmed_at'] = datetime.now(timezone.utc).isoformat()
    try:
        result = write_record(workspace, record_arguments)
    except PermissionError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    operation = 'repair_record' if arguments.get('repair') else 'retire_scope' if result['status'] == 'retired' else 'confirmed_record'
    record = read_record(workspace, record_arguments['module'], record_arguments['check_order'], record_arguments['research_id'],
                         scope=record_arguments.get('scope'), scope_id=result['scope_id'])
    event = {'event': 'research.write', 'operation': operation, 'tool': name, 'status': 'ok',
             'module': record_arguments['module'], 'check_order': record_arguments['check_order'],
             'research_id': record_arguments['research_id'], 'revision_count': result['revision_count'],
             'check_key': result['check_key'], 'scope_id': result['scope_id'],
             'revision': result['revision'], 'research_status': result['status'],
             'research_content': record,
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_module_memory_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        module = validate_module(arguments['module'])
        has_key = arguments.get('check_order') is not None or arguments.get('research_id') is not None
        if has_key and (not isinstance(arguments.get('check_order'), str) or not isinstance(arguments.get('research_id'), str)):
            raise ValueError('check_order 和 research_id 必须同时提供')
        if has_key:
            result = read_record(workspace, module, arguments['check_order'], arguments['research_id'],
                                 scope=arguments.get('scope'), scope_id=arguments.get('scope_id'))
            records = [result] if result is not None else []
            has_more = False
        else:
            records, has_more = read_module_records(workspace, module,
                                                     offset=arguments.get('offset', 0),
                                                     limit=arguments.get('limit', 100),
                                                     scope=arguments.get('scope'),
                                                     scope_id=arguments.get('scope_id'))
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    result = {'module': module, 'records': records, 'found': bool(records), 'has_more': has_more,
              'offset': arguments.get('offset', 0), 'limit': arguments.get('limit', 100)}
    event = {'event': 'memory.module.read', 'operation': 'module_memory_read', 'tool': name, 'status': 'ok',
             'module': module, 'found': bool(records), 'result_count': len(records),
             'has_more': has_more, 'memory_file': str(module_memory_path(workspace, module).relative_to(workspace)),
             'research_content': records if records else {}, 'span_id': span_id}
    if len(records) == 1 and records[0] is not None:
        record = records[0]
        event.update({'check_order': record['check_order'], 'research_id': record['research_id'],
                      'check_key': f"{module}:{record['check_order']}:{record['research_id']}",
                      'scope_id': record['scope_id'], 'revision': record['revision'],
                      'research_status': record['status'], 'history_count': len(record.get('history', []))})
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_research_progress(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        module = validate_module(arguments['module'])
        scope, scope_id = arguments.get('scope'), arguments.get('scope_id')
        if scope is None and scope_id is None:
            result = {'module': module, 'scope': None, 'scope_id': None, 'answered': 0,
                      'total': None, 'percentage': None, 'route': 'needs_scope',
                      'needs_scope': True, 'items': []}
            event = {'event': 'research.progress', 'operation': 'calculate_progress', 'tool': name,
                     'status': 'needs_scope', 'module': module, 'span_id': span_id}
            append_trace(store, arguments['trace_id'], event)
            record_otel_event(otel_span, event)
            return result
        records, has_more = read_module_records(workspace, module, limit=100, scope=scope, scope_id=scope_id)
        if has_more:
            raise ValueError('调研记录超过单次读取上限，无法可靠计算存储度')
        result = calculate_progress(module, records, scope=scope, scope_id=scope_id)
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'research.progress', 'operation': 'calculate_progress', 'tool': name, 'status': 'ok',
             'module': module, 'scope_id': scope_id, 'answered': result['answered'], 'total': result['total'],
             'percentage': result['percentage'], 'route': result['route'], 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

def _handle_research_unanswered_export(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str, otel_span: object | None):
    try:
        module = validate_module(arguments['module'])
        scope, scope_id = arguments.get('scope'), arguments.get('scope_id')
        if scope is None and scope_id is None:
            raise ValueError('必须提供 scope 或 scope_id，避免跨适用范围导出')
        records, has_more = read_module_records(workspace, module, limit=100, scope=scope, scope_id=scope_id)
        if has_more:
            raise ValueError('调研记录超过单次读取上限，无法可靠生成清单')
        export_scope = scope or (records[0].get('scope') if records else None) or scope_id or '未提供'
        result = export_unanswered_template(workspace, module, arguments['trace_id'], records=records,
                                            scope=export_scope, scope_id=scope_id,
                                            confirmed=arguments.get('confirmed'))
    except PermissionError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except ValueError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    event = {'event': 'research.unanswered.export', 'operation': 'export_unanswered_template',
             'tool': name, 'status': 'ok', 'module': module, 'scope_id': scope_id,
             'question_count': result['question_count'], 'path': result['path'], 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    record_otel_event(otel_span, event)
    return result

# 工具名、说明、schema 和处理器在此唯一注册。
TOOL_REGISTRY = {
    'dy_session_initialize': (
        '初始化或恢复当前工作区会话并返回项目记忆索引；需要时使用记忆工具读取完整内容。',
        {'type': 'object',
         'required': ['trace_id', 'source'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'source': {'type': 'string', 'enum': ['clear', 'compact', 'fork', 'resume', 'startup']}}},
        _handle_session_initialize,
    ),
    'dy_inspection_validate': (
        '校验声明范围内的巡检覆盖、证据引用、当前调研与矩阵版本；不验证证据真实性或授予写入授权。',
        {'type': 'object',
         'required': ['trace_id', 'report'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'report': {'type': 'object'}}},
        _handle_inspection_validate,
    ),
    'dy_inspection_report_save': (
        '重新校验并在用户确认后保存巡检报告到当前工作区固定目录。',
        {'type': 'object',
         'required': ['trace_id', 'report', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'report': {'type': 'object'}, 'confirmed': {'type': 'boolean'}}},
        _handle_inspection_report_save,
    ),
    'dy_inspection_report_read': (
        '按模块和报告编号读取当前工作区已保存的巡检报告。',
        {'type': 'object',
         'required': ['trace_id', 'module', 'report_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
                        'report_id': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'}}},
        _handle_inspection_report_read,
    ),
    'dy_inspection_report_export_pdf': (
        '从已保存的 Markdown 巡检报告重导出业务 PDF；数据按当前矩阵重新校验，已有 PDF 不覆盖。',
        {'type': 'object',
         'required': ['trace_id', 'module', 'report_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
                        'report_id': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'}}},
        _handle_inspection_report_export_pdf,
    ),
    'dy_notes_write': (
        '用户确认后写入主题笔记；更新须先读取并传完整 expected_content。仅标记 research_topic 且绑定 module 的确认主题进入项目 Wiki，不驱动巡检。',
        {'type': 'object',
         'required': ['trace_id', 'slug', 'title', 'body', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'slug': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9/-]{0,120}$'},
                        'title': {'type': 'string', 'minLength': 1, 'maxLength': 120},
                        'body': {'type': 'string', 'minLength': 1},
                        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 10},
                        'source_refs': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20},
                        'module': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
                        'research_topic': {'type': 'boolean', 'default': False},
                        'expected_content': {'type': 'string'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_notes_write,
    ),
    'dy_notes_read': (
        '读取指定 wiki 记忆页；不存在时返回可用页面清单。',
        {'type': 'object',
         'required': ['trace_id', 'slug'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'slug': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9/-]{0,120}$'}}},
        _handle_notes_read,
    ),
    'dy_notes_list': (
        '列出当前工作区全部 wiki 记忆页标识。',
        {'type': 'object',
         'required': ['trace_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}}},
        _handle_notes_list,
    ),
    'dy_project_configure': (
        '经用户确认后绑定当前工作区的唯一项目身份（正式项目名称与稳定代号）。'
        '项目未配置或旧配置未经确认时，Wiki 构建与同步都会拒绝执行，不从账套名或文件名猜测。',
        {'type': 'object',
         'required': ['trace_id', 'name', 'code', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'name': {'type': 'string', 'minLength': 1, 'maxLength': 100,
                                 'description': '经用户确认的正式项目名称，单行文本'},
                        'code': {'type': 'string', 'pattern': '^[a-z0-9]+(?:-[a-z0-9]+)*$', 'maxLength': 32,
                                 'description': '稳定项目代号，小写字母数字连字符'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_project_configure,
    ),
    'dy_wiki_build': (
        '用户确认后按当前项目调研记忆在本地构建中文 Wiki（知识库内项目首页、模块目录、业务主题页）'
        '并生成发布清单；不推送远端、不改动调研记录、不包含 YAML 元数据。',
        {'type': 'object',
         'required': ['trace_id', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_wiki_build,
    ),
    'dy_wiki_sync': (
        '用户确认后把已构建的本项目 Wiki 清单发布到 GitLab Wiki：按页面所有权标记和上次同步哈希校验，'
        '维护共享知识库首页的项目目录；不删除远端页面、不覆盖其他项目页面、不推交付物。'
        '需 DY_WIKI_API_URL / DY_WIKI_TOKEN 及目标项目（DY_WIKI_PROJECT_ID 或 DY_WIKI_GROUP_ID+DY_WIKI_PROJECT_NAME）。',
        {'type': 'object',
         'required': ['trace_id', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_wiki_sync,
    ),
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
                                 'enum': ['preferences.md', 'profile.md', 'decisions.md', 'delivery-register.md',
                                          'skill-candidates.md']}}},
        _handle_memory_read,
    ),
    'dy_module_memory_read': (
        '通过工具读取当前项目指定模块的调研记忆；可按检查键、适用范围和版本读取，不接受任意文件路径。',
        {'type': 'object',
         'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
                        'check_order': {'type': 'string'}, 'research_id': {'type': 'string'},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'},
                        'offset': {'type': 'integer', 'minimum': 0},
                        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}},
        _handle_module_memory_read,
    ),
    'dy_research_progress': (
        '按指定模块和适用范围计算已确认调研子项的存储度；未提供范围时返回 needs_scope，不合并不同范围。低于70%走清单，达到70%走互动问答。',
        {'type': 'object', 'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'enum': ['expense', 'general-ledger']},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'}}},
        _handle_research_progress,
    ),
    'dy_research_unanswered_export': (
        '用户明确确认后，按固定模板导出指定模块和适用范围内未回答问题的 Excel 清单；不覆盖已有文件。',
        {'type': 'object', 'required': ['trace_id', 'module', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'enum': ['expense', 'general-ledger']},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_research_unanswered_export,
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
                                 'enum': ['decisions.md', 'delivery-register.md', 'preferences.md', 'profile.md',
                                          'skill-candidates.md']},
                        'content': {'type': 'string'},
                        'confirmed': {'type': 'boolean'},
                        'expected_content': {'type': 'string', 'description': '更新已有文件必须提供最近读取的完整原文；冲突时重新读取合并。'}}},
        _handle_memory_write,
    ),
    'dy_odoo_record_search': (
        '按 Odoo Forge 的记录搜索命名执行受限的 search_read 只读查询。',
        {'type': 'object',
         'required': ['trace_id', 'model'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'model': {'type': 'string', 'pattern': '^[a-z0-9_][a-z0-9_.]{0,127}$'},
                        'method': {'type': 'string', 'enum': ['search_read']},
                        'args': {'type': 'array'},
                        'kwargs': {'type': 'object', 'additionalProperties': False,
                                   'properties': {'fields': {'type': 'array', 'items': {'type': 'string'}},
                                                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                                                  'offset': {'type': 'integer', 'minimum': 0},
                                                  'context': {'type': 'object', 'additionalProperties': False,
                                                              'properties': {'active_test': {'type': 'boolean'}}}}}}},
        _handle_odoo_record_search,
    ),
    'dy_odoo_record_read': (
        '读取指定 Odoo 记录的受限只读数据。',
        {'type': 'object',
         'required': ['trace_id', 'model', 'ids'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'model': {'type': 'string', 'pattern': '^[a-z0-9_][a-z0-9_.]{0,127}$'},
                        'ids': {'type': 'array',
                                'minItems': 1,
                                'maxItems': 100,
                                'items': {'type': 'integer', 'minimum': 1}},
                        'fields': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}}},
        _handle_odoo_record_read,
    ),
    'dy_odoo_model_fields': (
        '读取 Odoo 模型字段元数据。',
        {'type': 'object',
         'required': ['trace_id', 'model'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'model': {'type': 'string', 'pattern': '^[a-z0-9_][a-z0-9_.]{0,127}$'}}},
        _handle_odoo_record_read,
    ),
    'dy_odoo_module_list_installed': (
        '读取已安装 Odoo 模块列表；每页最多 100 条，需用 offset 翻页取全量。',
        {'type': 'object',
         'required': ['trace_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'offset': {'type': 'integer', 'minimum': 0}}},
        _handle_odoo_record_read,
    ),
    'dy_odoo_module_info': (
        '读取指定 Odoo 模块信息。',
        {'type': 'object',
         'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'minLength': 1, 'pattern': '\\S'}}},
        _handle_odoo_record_read,
    ),
    'dy_knowledge_module_info': (
        '读取本地 HRP/Odoo 模块知识。',
        {'type': 'object',
         'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'module': {'type': 'string'}}},
        _handle_knowledge_module_info,
    ),
    'dy_knowledge_search': (
        '按中文或英文关键词搜索本地 HRP/Odoo 模块知识。',
        {'type': 'object',
         'required': ['trace_id', 'query'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'query': {'type': 'string'}}},
        _handle_knowledge_search,
    ),
    'dy_research_record_write': (
        '在用户确认后追加模块调研结论并保留历史版本。',
        {'type': 'object',
         'additionalProperties': False,
         'required': ['trace_id', 'module', 'check_order', 'research_id', 'config_item', 'conclusion', 'scope',
                      'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string'},
                        'check_order': {'type': 'string'},
                        'research_id': {'type': 'string'},
                        'config_item': {'type': 'string'},
                        'conclusion': {'type': 'string'},
                        'scope': {'type': 'string'},
                        'evidence_ref': {'type': 'string'},
                        'confirmed_by': {'type': 'string', 'description': '由 Feishu 发送人环境变量自动写入；调用方无需提供。'},
                        'confirmed_at': {'type': 'string', 'description': '由 MCP 使用当前系统时间自动写入；调用方无需提供。'},
                        'confirmed': {'type': 'boolean'},
                        'expected_revision': {'type': 'string', 'description': '已有记录必须提供读取版本；冲突时重新读取并确认。'},
                        'scope_id': {'type': 'string', 'description': '稳定范围标识；首次可省略，后续沿用读取值。'},
                        'status': {'type': 'string', 'enum': ['confirmed', 'retired']},
                        'retirement_reason': {'type': 'string'},
                        'repair': {'type': 'boolean', 'description': '损坏记录经核实并确认后显式修复。'}}},
        _handle_research_record_write,
    ),
}
for _, schema, _ in TOOL_REGISTRY.values():
    schema['properties']['trace_context'] = {
        'type': 'object', 'description': '可选的巡检关联标识；不包含正文，不代表业务授权或事实验证。',
        'additionalProperties': False,
        'properties': {key: {'type': 'string', 'pattern': TRACE_CODE_PATTERN.pattern}
                       for key in sorted(TRACE_CONTEXT_FIELDS)},
    }
TOOLS = {name: spec[0] for name, spec in TOOL_REGISTRY.items()}
TOOL_SCHEMAS = {name: spec[1] for name, spec in TOOL_REGISTRY.items()}


def _dispatch_tool(name, arguments, workspace, store, span_id, otel_span):
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RuntimeError("mcp-server 安装缺少必需依赖 jsonschema") from exc

    spec = TOOL_REGISTRY.get(name) if isinstance(name, str) else None
    if spec is None:
        raise ToolRequestError(-32602, "未知工具")
    if next(Draft202012Validator(spec[1]).iter_errors(arguments), None) is not None:
        raise ToolRequestError(-32602, "工具参数不符合 schema，请核对必填字段及类型")
    with _odoo_lock if name.startswith("dy_odoo_") else nullcontext():
        with operation_span(name):
            return spec[2](name, arguments, workspace, store, span_id, otel_span)


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
    initialize_telemetry()
    telemetry_name = name if isinstance(name, str) and name in TOOL_REGISTRY else 'unknown_tool'
    with tool_span(trace_id, workspace, telemetry_name, call_id=span_id, trace_context=trace_context) as otel_span:
        try:
            start_event = {"event": "tool.start", "tool": name, "status": "running", "span_id": span_id}
            append_trace(store, trace_id, start_event)
            record_otel_event(otel_span, start_event)
            started = True
            result = _dispatch_tool(name, arguments, workspace, store, span_id, otel_span)
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
                    record_otel_event(otel_span, error_event)
                    mark_otel_error(otel_span, error_type)
                end_event = {"event": "tool.end", "tool": name, "status": status, "duration_ms": round((time.monotonic() - start) * 1000, 2), "span_id": span_id}
                try:
                    append_trace(store, trace_id, end_event)
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
                                           for name, spec in TOOL_REGISTRY.items()])

    async def call_tool(context, params):
        result, error = await asyncio.to_thread(execute_tool, params.name, params.arguments or {}, workspace)
        if error:
            raise MCPError(error["code"], error["message"])
        return types.CallToolResult(content=[types.TextContent(type="text", text=_mcp_result(result)["content"][0]["text"])])

    server = Server("dy_superpowers", version="0.2.0", on_list_tools=list_tools, on_call_tool=call_tool)
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
        _clear_odoo_client()


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
