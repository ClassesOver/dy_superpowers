"""模块调研答卷工具。"""
from __future__ import annotations

from .. import research as research_lib
from ..memory import module_memory_path, validate_module
from ..qa_store import commit_qa, export_qa, normalize_scope, parse_qa_workbook, read_qa
from ..trace import TRACE_ID_PATTERN
from ..qa_templates import template_modules
from ..trace import TraceStore
from .runtime import append_trace, invalid_arguments
from datetime import datetime, timezone
from pathlib import Path
import os


def _handle_research_record_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    record_arguments = dict(arguments)
    sender_id = os.environ.get('FEISHU_CODEX_BOT_SENDER_USER_ID', '').strip()
    record_arguments['confirmed_by'] = sender_id or str(arguments.get('confirmed_by') or '未提供')
    record_arguments['confirmed_at'] = datetime.now(timezone.utc).isoformat()
    with invalid_arguments(PermissionError, ValueError):
        result = research_lib.write_record(workspace, record_arguments)
    operation = 'repair_record' if arguments.get('repair') else 'retire_scope' if result['status'] == 'retired' else 'confirmed_record'
    record = research_lib.read_record(workspace, record_arguments['module'], record_arguments['check_order'], record_arguments['research_id'],
                         scope=record_arguments.get('scope'), scope_id=result['scope_id'])
    event = {'event': 'research.write', 'operation': operation, 'tool': name, 'status': 'ok',
             'module': record_arguments['module'], 'check_order': record_arguments['check_order'],
             'research_id': record_arguments['research_id'], 'revision_count': result['revision_count'],
             'check_key': result['check_key'], 'scope_id': result['scope_id'],
             'revision': result['revision'], 'research_status': result['status'],
             'research_content': record,
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


def _qa_trace_records(records):
    """调研正文仍可追踪；已确认附件明细只保存在工作区版本与答卷。"""
    return [{**record, 'attachments': [{key: value for key, value in bound.items() if key != 'details'}
            for bound in record['attachments']]} for record in records]


def _handle_module_memory_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    if arguments.get('legacy') is not True:
        with invalid_arguments():
            legacy_fields = {'check_order', 'research_id', 'checks', 'offset', 'limit', 'scope'} & arguments.keys()
            if legacy_fields:
                raise ValueError('新版 QA 按 scope_id/question_ids 读取；旧键、scope文本或分页参数须显式 legacy=true')
            result = read_qa(workspace, arguments['module'], arguments.get('scope_id'), arguments.get('question_ids'))
            append_trace(store, arguments['trace_id'], {'event': 'memory.module.read', 'operation': 'qa_read',
                'tool': name, 'status': 'ok', 'module': arguments['module'], 'scope_id': result['scope_id'],
                'research_content': _qa_trace_records(result['records']), 'span_id': span_id})
            return result
    with invalid_arguments():
        module = validate_module(arguments['module'])
        has_key = arguments.get('check_order') is not None or arguments.get('research_id') is not None
        view = arguments.get('view', 'full')
        if has_key and arguments.get('checks') is not None:
            raise ValueError('单个检查键与 checks 批量查询不能同时提供')
        if has_key and (not isinstance(arguments.get('check_order'), str) or not isinstance(arguments.get('research_id'), str)):
            raise ValueError('check_order 和 research_id 必须同时提供')
        if has_key:
            result = research_lib.read_record(workspace, module, arguments['check_order'], arguments['research_id'],
                                 scope=arguments.get('scope'), scope_id=arguments.get('scope_id'),
                                 include_history=view == 'full')
            records = [result] if result is not None else []
            has_more = False
        else:
            records, has_more = research_lib.read_module_records(workspace, module,
                                                     offset=arguments.get('offset', 0),
                                                     limit=arguments.get('limit', 100),
                                                     scope=arguments.get('scope'),
                                                     scope_id=arguments.get('scope_id'),
                                                     include_history=view == 'full', checks=arguments.get('checks'))
        if view == 'summary':
            records = [research_lib.record_summary(record) for record in records]
    result = {'module': module, 'records': records, 'found': bool(records), 'has_more': has_more,
              'offset': arguments.get('offset', 0), 'limit': arguments.get('limit', 100),
              'next_offset': arguments.get('offset', 0) + len(records) if has_more else None}
    event = {'event': 'memory.module.read', 'operation': 'module_memory_read', 'tool': name, 'status': 'ok',
             'module': module, 'found': bool(records), 'result_count': len(records),
             'has_more': has_more, 'memory_file': str(module_memory_path(workspace, module).relative_to(workspace)),
             'research_content': records if records else {}, 'span_id': span_id}
    if len(records) == 1 and records[0] is not None:
        record = records[0]
        event.update({'check_order': record['check_order'], 'research_id': record['research_id'],
                      'check_key': f"{module}:{record['check_order']}:{record['research_id']}",
                      'scope_id': record['scope_id'], 'revision': record['revision'],
                      'research_status': record['status'],
                      'history_count': record.get('history_count', len(record.get('history', [])))})
    append_trace(store, arguments['trace_id'], event)
    return result


def _handle_research_progress(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments():
        module = validate_module(arguments['module'])
        intent = arguments.get('intent')
        if intent is not None and intent not in ('research', 'progress', 'export', 'import', 'confirm'):
            raise ValueError('intent 必须为 research、progress、export、import 或 confirm')
        scope, scope_id = arguments.get('scope'), arguments.get('scope_id')
        result = read_qa(workspace, module, scope_id)
        if scope is not None and result.get('scope') != normalize_scope(scope):
            raise ValueError('请按最新 QA 选择真实 scope_id')
        result['unknown_count'] = 0
        result['intent'] = intent
        result['next_action'] = 'classify_intent' if intent is None else {
            'progress': 'report_progress', 'export': 'export_checklist', 'import': 'confirm_source',
            'confirm': 'save_confirmed',
            'research': 'ask_missing' if result['route'] == 'interactive' else 'offer_checklist',
        }[intent]
    event = {'event': 'research.progress', 'operation': 'calculate_progress', 'tool': name, 'status': 'ok',
             'module': module, 'scope_id': result['scope_id'], 'scope_mode': result['scope_mode'],
             'answered': result['answered'], 'total': result['total'],
             'confirmed_count': result['confirmed_count'], 'confirmed_percentage': result['confirmed_percentage'],
             'partial_count': result['partial_count'], 'unknown_count': result['unknown_count'],
             'unanswered_count': result['unanswered_count'],
             'percentage': result['percentage'], 'route': result['route'],
             'intent': intent or 'unset', 'next_action': result['next_action'], 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


def _handle_research_unanswered_export(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments(PermissionError, ValueError):
        if 'scope' in arguments:
            raise ValueError('新版答卷按 scope_id 导出，不接受 scope 文本，请先读取真实范围标识')
        result = export_qa(workspace, arguments['module'], scope_id=arguments.get('scope_id'),
                           confirmed=arguments.get('confirmed'))
    return result


def _handle_research_answers_save(name, arguments, workspace, store, span_id):
    with invalid_arguments(ValueError, PermissionError):
        result = commit_qa(workspace, arguments['module'], scope=arguments['scope'],
            scope_id=arguments.get('scope_id'), template_revision=arguments['template_revision'],
            answers=arguments['answers'], batch_id=arguments['batch_id'], confirmed=arguments['confirmed'],
            confirmed_by=os.environ.get('FEISHU_CODEX_BOT_SENDER_USER_ID', '').strip() or '当前会话用户')
    append_trace(store, arguments['trace_id'], {'event': 'research.write', 'operation': 'qa_batch',
        'tool': name, 'status': 'ok', 'module': arguments['module'], 'scope_id': result['scope_id'],
        'research_content': _qa_trace_records(result['records']), 'span_id': span_id})
    return result


def _handle_research_answers_import(name, arguments, workspace, store, span_id):
    with invalid_arguments(ValueError, OSError):
        return parse_qa_workbook(workspace, arguments['path'])


TOOLS = {
    'dy_research_answers_save': (
        '确认后批次保存当前模板问题答案；答案整项替换，其他问题保留，返回同步版本答卷路径。',
        {'type': 'object', 'additionalProperties': False,
         'required': ['trace_id', 'module', 'scope', 'template_revision', 'answers', 'batch_id', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': TRACE_ID_PATTERN.pattern}, 'module': {'type': 'string', 'enum': list(template_modules())},
             'scope': {'type': 'string', 'minLength': 1, 'maxLength': 500}, 'scope_id': {'type': 'string'},
             'template_revision': {'type': 'string'}, 'batch_id': {'type': 'string', 'minLength': 1},
             'confirmed': {'type': 'boolean'}, 'answers': {'type': 'array', 'items': {
                 'type': 'object', 'additionalProperties': False,
                 'required': ['question_id', 'answer', 'research_complete', 'expected_revision'],
                 'properties': {'question_id': {'type': 'string'}, 'answer': {'type': 'string'},
                     'research_complete': {'type': 'boolean'}, 'expected_revision': {'type': ['string', 'null']},
                     'attachments': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                         'required': ['reference', 'sha256', 'requirements'], 'properties': {
                             'reference': {'type': 'string'}, 'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                             'requirements': {'type': 'array', 'items': {'type': 'string'}},
                             'details': {'type': 'array', 'description': '经确认且已脱敏的本问题附件模板字段明细，日期时间用 ISO 文本；同引用及摘要省略时保留已有明细，显式空列表清空。',
                                 'items': {'type': 'object', 'additionalProperties': False, 'required': ['sheet', 'rows'],
                                     'properties': {'sheet': {'type': 'string'}, 'rows': {'type': 'array',
                                         'items': {'type': 'object', 'additionalProperties': {'type': ['string', 'number', 'boolean', 'null']}}}}}}}}}}}}}},
        _handle_research_answers_save,
    ),
    'dy_research_answers_import': (
        '仅解析工作区已登记答卷为候选，不写入记忆，不自动提升完整性。',
        {'type': 'object', 'additionalProperties': False, 'required': ['trace_id', 'path'],
         'properties': {'trace_id': {'type': 'string', 'pattern': TRACE_ID_PATTERN.pattern}, 'path': {'type': 'string', 'minLength': 1}}},
        _handle_research_answers_import,
    ),
    'dy_module_memory_read': (
        '读取当前问题投影对应的QA，按scope_id/question_ids过滤，默认返回全量问题与当前确认答案。不接受任意文件路径。',
        {'type': 'object',
         'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'},
                        'check_order': {'type': 'string'}, 'research_id': {'type': 'string'},
                        'legacy': {'type': 'boolean', 'default': False},
                        'question_ids': {'type': 'array', 'items': {'type': 'string'}},
                        'view': {'type': 'string', 'enum': ['qa', 'current', 'summary', 'full'], 'default': 'qa',
                                 'description': '默认qa。current/summary/full仅legacy=true历史视图；legacy=true且省略view时按full返回。不指定legacy不会读取旧答案。'},
                        'checks': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {
                            'type': 'object', 'additionalProperties': False,
                            'required': ['check_order', 'research_id'],
                            'properties': {'check_order': {'type': 'string', 'minLength': 1},
                                           'research_id': {'type': 'string', 'minLength': 1}}}},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'},
                        'offset': {'type': 'integer', 'minimum': 0},
                        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 100,
                                  'description': '每页1至100条；省略即100。超限时改用100，不重试相同非法参数。'}}},
        _handle_module_memory_read,
    ),
    'dy_research_progress': (
        '调研发问前先调用。必须由模型按本轮意图传intent，再按next_action执行：progress仅报告；export导出清单；import核对本轮已填来源；confirm完成当前已明确批准的候选保存；research按完整完成比例低于70%走清单、达到70%才补问业务。未传intent时next_action=classify_intent，先识别意图并重新调用，不得直接行动。历史待办不构成import，非当前确认批次的“确认”不构成confirm。分别展示progress_summary的已确认回答与完整完成，后者决定route。范围可省略，唯一范围自动复用，多范围分别展示scope_progress，不合并、不先问范围。本工具只读，next_action本身不代表授权。',
        {'type': 'object', 'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'enum': list(template_modules())},
                        'intent': {'type': 'string', 'enum': ['research', 'progress', 'export', 'import', 'confirm'],
                                   'description': '必填：模型识别当前请求，开始/继续调研research；只查进度progress；明确要清单export；本轮导入/核对已填来源import；回应当前明确确认/保存批次confirm。'},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'}}},
        _handle_research_progress,
    ),
    'dy_research_unanswered_export': (
        '导出当前问卷完整答卷；本轮明确下载即授权confirmed=true，不受70%门槛。按scope_id选择真实范围，省略时唯一范围复用、多范围分别生成登记副本，不能拼接答案。包含问题投影全量、最新确认答案和附件明细，不覆盖原版本或旧下载。scope文本不支持。',
        {'type': 'object', 'required': ['trace_id', 'module', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'module': {'type': 'string', 'enum': list(template_modules())},
                        'scope': {'type': 'string'}, 'scope_id': {'type': 'string'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_research_unanswered_export,
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
                        'research_complete': {'type': 'boolean', 'description': '按本子项适用要素、范围及必要依据独立判断是否完整；实际缺口仍在时填写false。同组其他子项未答或尚未现场巡检不代表本项不完整。'},
                        'status': {'type': 'string', 'enum': ['confirmed', 'retired']},
                        'retirement_reason': {'type': 'string'},
                        'repair': {'type': 'boolean', 'description': '损坏记录经核实并确认后显式修复。'}}},
        _handle_research_record_write,
    ),
}
