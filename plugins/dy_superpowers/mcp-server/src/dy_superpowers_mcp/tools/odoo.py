"""Odoo 只读查询与能力工具；全部 search_read 经 AuditedOdoo 审计。"""
from __future__ import annotations

from pathlib import Path

from . import runtime
from .. import odoo_discovery, odoo_imports, odoo_operations, odoo_planning
from ..odoo_capabilities import DESCRIPTIONS as ODOO_CAPABILITY_DESCRIPTIONS, SCHEMAS as ODOO_CAPABILITY_SCHEMAS
from ..odoo_jsonrpc import validate_read_arguments
from ..qa_basis import configured_environment_info
from ..trace import TRACE_ID_PATTERN, TraceStore
from .runtime import AuditedOdoo, ToolRequestError, append_trace, invalid_arguments, odoo_errors


def _handle_odoo_environment_info(name, arguments, workspace, store, span_id):
    return configured_environment_info()


def _handle_odoo_record_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with odoo_errors():
        model, domain, fields, limit, offset, context = validate_read_arguments(arguments)
        from ..odoo_comparison import compare_records, validate_comparison
        with invalid_arguments():
            validate_comparison(arguments.get('comparison'), fields, limit=limit, offset=offset)
        client = AuditedOdoo(runtime.odoo_client(workspace), store, arguments, name, span_id)
        return client.search_read(model, domain, fields, limit, offset,
                                  summary=lambda rows: compare_records(arguments.get('comparison'), rows),
                                  **({'context': context} if context else {}))


def _handle_odoo_record_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with odoo_errors():
        client = runtime.odoo_client(workspace)
        metadata = {}
        if name == 'dy_odoo_record_read':
            ids, fields = (arguments.get('ids'), arguments.get('fields', []))
            if not isinstance(ids, list) or not ids or len(ids) > 100 or (not all((isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in ids))):
                raise ToolRequestError(-32602, 'ids 必须是 1 到 100 个正整数')
            if not isinstance(fields, list) or not all((isinstance(item, str) and item for item in fields)):
                raise ToolRequestError(-32602, 'fields 必须是字符串列表')
            from ..odoo_comparison import compare_records, validate_comparison
            with invalid_arguments():
                validate_comparison(arguments.get('comparison'), fields)
            result = client.read_records(arguments['model'], ids, fields)
            operation = 'record_read'
            model = arguments['model']
            metadata = {'requested_count': len(ids), 'field_count': len(fields),
                        **compare_records(arguments.get('comparison'), result)}
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
        return result


def _handle_odoo_capability(name, arguments, workspace, store, span_id):
    with odoo_errors(rpc_message='系统查询失败，请核对连接与权限；不返回远端正文'):
        try:
            return _run_odoo_capability(name, arguments, workspace, store, span_id)
        except ValueError as exc:
            if isinstance(exc, runtime.OdooConfigurationError):
                raise
            # 答卷、知识库等参数校验错误属于可修正的请求错误，不能落成笼统的内部错误。
            raise ToolRequestError(-32602, str(exc)) from exc


def _run_odoo_capability(name, arguments, workspace, store, span_id):
    factory = lambda: AuditedOdoo(runtime.odoo_client(workspace), store, arguments, name, span_id)
    if name == 'dy_odoo_analyze_requirements':
        return odoo_planning.analyze(arguments['description'])
    if name == 'dy_odoo_design_solution':
        return odoo_planning.design(arguments['requirements'], arguments.get('user_answers'))
    if name == 'dy_odoo_validate_plan':
        from . import available_validator
        return odoo_planning.validate(arguments['plan'], available_validator)
    if name == 'dy_odoo_diagnostics_health_check':
        return odoo_discovery.health_check(factory)
    if name in {'dy_odoo_module_install', 'dy_odoo_module_uninstall'}:
        result = odoo_operations.module_operation(workspace, name.rsplit('_', 1)[1], arguments, factory)
        if result.get('status') == 'attempted':
            runtime.clear_odoo_client()
        return result
    if name == 'dy_odoo_import_execute':
        return odoo_imports.execute(workspace, factory, arguments)
    client = factory()
    if name == 'dy_knowledge_community_gaps':
        return odoo_discovery.community_gaps(workspace, client, arguments['module'], arguments['scope_id'])
    if name == 'dy_odoo_import_template':
        return odoo_imports.template(client, arguments)
    if name == 'dy_odoo_import_preview':
        return odoo_imports.preview(client, arguments)
    if name == 'dy_odoo_record_execute':
        return odoo_operations.execute_read(client, arguments)
    return odoo_discovery.discover(client, name.removeprefix('dy_odoo_'), arguments)


TOOLS = {
    'dy_odoo_environment_info': (
        '只读查看本地已配置环境身份，不登录或访问Odoo。仅返回稳定environment、非密database标识与configured，不返回URL、用户、密码或令牌；不猜dev/prod。',
        {'type': 'object', 'additionalProperties': False, 'required': ['trace_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': TRACE_ID_PATTERN.pattern}}},
        _handle_odoo_environment_info,
    ),
    'dy_odoo_record_search': (
        '执行受限的 search_read 只读记录查询。筛选用 args=[domain]；字段与分页用 kwargs={fields,limit,offset,context}，不支持 order。',
        {'type': 'object', 'additionalProperties': False,
         'required': ['trace_id', 'model'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'model': {'type': 'string', 'pattern': '^[a-z0-9_][a-z0-9_.]{0,127}$'},
                        'method': {'type': 'string', 'enum': ['search_read']},
                        'args': {'type': 'array', 'description': '仅包含一个 domain 列表，即 [domain]；省略表示无筛选。'},
                        'kwargs': {'type': 'object', 'additionalProperties': False,
                                   'properties': {'fields': {'type': 'array', 'items': {'type': 'string'}},
                                                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                                                  'offset': {'type': 'integer', 'minimum': 0},
                                                  'context': {'type': 'object', 'additionalProperties': False,
                                                              'properties': {'active_test': {'type': 'boolean'},
                                                                             'view_company': {'type': 'boolean', 'description': '仅限 res.company'}}}}}}},
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
}
for name, schema in ODOO_CAPABILITY_SCHEMAS.items():
    TOOLS[name] = (ODOO_CAPABILITY_DESCRIPTIONS[name], schema, _handle_odoo_capability)
for name in ('dy_odoo_record_search', 'dy_odoo_record_read'):
    TOOLS[name][1]['properties']['comparison'] = {
        'type': 'object', 'additionalProperties': False,
        'description': '实时精确比较；trace 仅保存预期摘要和比较状态。搜索须 offset=0 且 limit 大于预期数量。',
        'required': ['expected_count', 'expected_values'],
        'properties': {'expected_count': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                       'expected_values': {'type': 'object', 'maxProperties': 100}},
    }
