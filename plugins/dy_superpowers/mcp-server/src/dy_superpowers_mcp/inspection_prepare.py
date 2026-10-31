"""按模板问题列出确认 QA、附件要求及本轮缺口。"""
from pathlib import Path
import os
import re
import hashlib
import json
from .qa_store import read_qa
from .trace import TRACE_CODE_PATTERN, redact
from .odoo_jsonrpc import OdooSettings, OdooConfigurationError


def configured_environment_info():
    """仅读取安全配置身份；不创建连接或进行认证。"""
    def identifier(value):
        value = value.strip()
        return value if value and len(value) <= 128 and TRACE_CODE_PATTERN.fullmatch(value) and '://' not in value and redact(value) == value else None
    database = identifier(os.environ.get('DB_NAME', ''))
    explicit = os.environ.get('DY_ODOO_ENVIRONMENT', '').strip()
    environment = identifier(explicit) if explicit else database
    try:
        settings = OdooSettings.from_environment()
        fingerprint = hashlib.sha256(json.dumps([settings.url, settings.database, settings.username],
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        configured = True
    except OdooConfigurationError:
        configured = False
        fingerprint = None
    return {'environment': environment, 'database': database, 'configured': configured,
            'connection_fingerprint': fingerprint}


def attachment_detail_checks(attachments):
    """确认明细的非空字段均须保留，包括 false 与零；定位仅用于内部覆盖。"""
    checks = []
    for attachment in attachments:
        for detail in attachment.get('details', []):
            for index, row in enumerate(detail['rows'], 1):
                for field, value in row.items():
                    if value is None or value == '':
                        continue
                    source = dict(kind='attachment', reference=attachment['reference'], sha256=attachment['sha256'],
                                  sheet=detail['sheet'], row=index, field=field)
                    rid = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
                    checks.append(dict(requirement_id='detail-' + rid, source_ref=source, expected_value=value,
                        text=f"{detail['sheet']} 第{index}行 {field}：{json.dumps(value, ensure_ascii=False)}"))
    return checks


def optional_attachment(question):
    """只读取完整模板的明确按需标记，不猜测确认答案的业务语义。"""
    instruction = question.get('attachment_instruction', '')
    optional = re.search(r'附件[（(]按需[）)]', instruction)
    required = re.search(r'附件\s*[:：]', instruction)
    return bool(optional and not required)


def prepare_inspection(workspace: Path, module: str, scope_id: str, environment: str, *,
                       checks=None, previous_records=None) -> dict:
    if not isinstance(environment, str) or len(environment) > 128 or not TRACE_CODE_PATTERN.fullmatch(environment):
        raise ValueError('environment 必须是稳定环境标识')
    configured = configured_environment_info()['environment']
    if configured is not None and environment != configured:
        raise ValueError('environment 与当前服务实际配置身份不一致')
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError('必须明确 scope_id')
    state = read_qa(workspace, module, scope_id)
    questions = state['questions']
    if checks is not None:
        if not isinstance(checks, list) or not checks or any(not isinstance(q, dict) or q.get('question_id') not in {x['question_id'] for x in questions} for q in checks):
            raise ValueError('checks 只能使用当前模板 question_id；完整报告仍须覆盖全部问题')
        if len({q['question_id'] for q in checks}) != len(checks):
            raise ValueError('checks 不可重复')
    current = {a['question_id']: a for a in state['records']}
    basis = {k: state[k] for k in ('module', 'scope_id', 'template_revision', 'answer_version')}
    basis['environment'] = environment
    basis['connection_fingerprint'] = configured_environment_info()['connection_fingerprint']
    if previous_records is not None and not isinstance(previous_records, list):
        raise ValueError('previous_records 必须是列表')
    items = []
    for q in questions:
        a = current.get(q['question_id'])
        readiness = 'missing' if a is None else 'incomplete' if not a['research_complete'] else 'attachment_missing' if q['attachment_names'] and not optional_attachment(q) and not a['attachments'] else 'research_ready'
        if a and any(change['question_id'] == q['question_id'] for change in state['attachment_changes']):
            readiness = 'attachment_changed'
        items.append({**q, 'answer_revision': a['revision'] if a else None,
            'answer': a['answer'] if a else None, 'confirmed_at': a['confirmed_at'] if a else None,
            'attachments': a['attachments'] if a else [], 'readiness': readiness,
            'detail_checks': attachment_detail_checks(a['attachments']) if a else [],
            'blocking_reason': '' if readiness == 'research_ready' else '需补齐当前确认答案或附件要求',
            'next_question': q['question'] if readiness != 'research_ready' else None,
            'recheck_required': True, 'recheck_reasons': ['live_verification_required'], 'evidence_reusable': False})
    ready = sum(i['readiness'] == 'research_ready' for i in items)
    return {'module': module, 'scope_id': scope_id, 'environment': environment, 'basis': basis, 'items': items,
        'expected_checks': [{'question_id': q['question_id']} for q in questions],
        'research_storage': {k: state[k] for k in ('answered', 'total', 'percentage', 'route', 'confirmed_count', 'partial_count')},
        'preparation': {'total': len(items), 'research_ready': ready, 'blocked': len(items) - ready},
        'verification': {'performed': False}, 'execution_authorized': False,
        'limitations': '完整确认仅提供巡检依据，仍须逐项提取要求和采集本轮真实证据。历史结果不自动复用。'}
