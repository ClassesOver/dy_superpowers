"""巡检工具：准备、校验、保存、读取与 PDF 导出。"""
from __future__ import annotations

from ..inspection import validate_inspection
from ..inspection_prepare import prepare_inspection
from ..qa_basis import configured_environment_info
from ..qa_templates import template_modules
from ..reports import export_report_pdf, read_report, save_report
from ..trace import TRACE_CODE_PATTERN, TRACE_ID_PATTERN
from ..trace import TraceStore
from .runtime import ToolRequestError, append_trace, invalid_arguments
from pathlib import Path
import json


def _handle_inspection_prepare(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments():
        environment = arguments.get('environment') or configured_environment_info()['environment']
        if environment is None:
            raise ValueError('配置未提供可确定的安全环境标识，请先核对 dy_odoo_environment_info 后明确 environment')
        result = prepare_inspection(workspace, arguments['module'], arguments['scope_id'],
                                    environment, checks=arguments.get('checks'),
                                    previous_records=arguments.get('previous_records'))
    event = {'event': 'inspection.prepare', 'operation': 'prepare', 'tool': name, 'status': 'ok',
             'module': arguments['module'], 'scope_id': arguments['scope_id'],
             'trace_context': {'module': arguments['module'], 'scope_id': arguments['scope_id'],
                               'environment': environment},
             'expected_count': result['preparation']['total'],
             'count': result['preparation']['research_ready'],
             'result_count': result['preparation']['blocked'], 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


def _handle_inspection_validate(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    result = validate_inspection(workspace, arguments.get('report'))
    expected = arguments.get('report', {}).get('expected_checks', []) if isinstance(arguments.get('report'), dict) else []
    event = {'event': 'inspection.validate', 'operation': 'validate', 'tool': name, 'status': 'ok',
             'result': result['gate'], 'valid': result['valid'], 'error_count': len(result['errors']),
             'expected_count': len(expected) if isinstance(expected, list) else 0, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


def _handle_inspection_report_save(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, '保存巡检报告需要用户明确确认')
    report = arguments.get('report')
    if not isinstance(report, dict):
        raise ToolRequestError(-32602, 'report 必须是对象')
    result = validate_inspection(workspace, report)
    if not result['valid']:
        raise ToolRequestError(-32602, '巡检报告校验失败: ' + json.dumps(result['errors'], ensure_ascii=False))
    module = report['basis']['module']
    with invalid_arguments():
        report_id, path, pdf_path = save_report(workspace, module, report, result)
    event = {'event': 'inspection.report.save', 'operation': 'save', 'tool': name, 'status': 'ok',
             'module': module, 'report_id': report_id, 'result': result['gate'],
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return {'saved': True, 'module': module, 'report_id': report_id,
            'path': str(path.relative_to(workspace)), 'pdf_path': str(pdf_path.relative_to(workspace)),
            'gate': result['gate'], 'validated_at': result['validated_at']}


def _handle_inspection_report_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    module = arguments.get('module')
    report_id = arguments.get('report_id')
    if (not isinstance(module, str) or not module.strip()
            or not isinstance(report_id, str) or not report_id.strip()):
        raise ToolRequestError(-32602, 'module 和 report_id 必须是非空文本')
    with invalid_arguments():
        content = read_report(workspace, module, report_id)
    event = {'event': 'inspection.report.read', 'operation': 'read', 'tool': name, 'status': 'ok',
             'module': module, 'report_id': report_id,
             'content_bytes': len(content.encode('utf-8')), 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return content


def _handle_inspection_report_export_pdf(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    module = arguments.get('module')
    report_id = arguments.get('report_id')
    if (not isinstance(module, str) or not module.strip()
            or not isinstance(report_id, str) or not report_id.strip()):
        raise ToolRequestError(-32602, 'module 和 report_id 必须是非空文本')
    with invalid_arguments():
        pdf_path, created = export_report_pdf(workspace, module, report_id)
    event = {'event': 'inspection.report.export_pdf', 'operation': 'export', 'tool': name,
             'status': 'ok', 'module': module, 'report_id': report_id,
             'result': '已生成' if created else '已存在', 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return {'exported': True, 'module': module, 'report_id': report_id, 'created': created,
            'pdf_path': str(pdf_path.relative_to(workspace))}


TOOLS = {
    'dy_inspection_prepare': (
        '只读准备同范围模板巡检：返回当前basis、问题投影全量、答案与附件要求、阻塞和补问。准备就绪不等于验收通过，不执行Odoo查询，不保存或沿用旧结果。',
        {'type': 'object', 'additionalProperties': False,
         'required': ['trace_id', 'module', 'scope_id'],
         'properties': {'trace_id': {'type': 'string', 'pattern': TRACE_ID_PATTERN.pattern},
                        'module': {'type': 'string', 'enum': list(template_modules())},
                        'scope_id': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'},
                        'environment': {'type': 'string', 'maxLength': 128, 'pattern': TRACE_CODE_PATTERN.pattern,
                                        'description': '可省略，使用dy_odoo_environment_info的安全配置身份；显式值须与证据trace_context一致，不能猜dev/prod。'},
                        'checks': {'type': 'array', 'minItems': 1, 'items': {'type': 'object', 'required': ['question_id'], 'properties': {'question_id': {'type': 'string'}}}},
                        'previous_records': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'object'}}}},
        _handle_inspection_prepare,
    ),
    'dy_inspection_validate': (
        '校验当前模板覆盖、QA版本、附件及真实证据。配置明确结果须引用同一comparison及当前读取追踪，比较状态须匹配报告结论；不证明自由文本理解完整，不授予写入授权。',
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
}
