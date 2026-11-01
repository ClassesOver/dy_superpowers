"""答卷依据：题目就绪判断、附件明细展开与本地配置环境身份，供巡检与配置共用。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .odoo_jsonrpc import OdooConfigurationError, OdooSettings
from .qa_store import read_qa
from .trace import TRACE_CODE_PATTERN, redact

READY = 'research_ready'


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


def question_readiness(question, answer, attachment_changes) -> str:
    """当前确认答案能否作为确定依据：缺答案、未完整、缺必需附件或附件已变化均不就绪。"""
    if answer is None:
        return 'missing'
    if any(change['question_id'] == question['question_id'] for change in attachment_changes):
        return 'attachment_changed'
    if not answer['research_complete']:
        return 'incomplete'
    if question['attachment_names'] and not optional_attachment(question) and not answer['attachments']:
        return 'attachment_missing'
    return READY


def answer_items(state):
    """按当前模板顺序返回 (问题, 确认答案, 就绪状态)。"""
    current = {a['question_id']: a for a in state['records']}
    for question in state['questions']:
        answer = current.get(question['question_id'])
        yield question, answer, question_readiness(question, answer, state['attachment_changes'])


def confirmed_answer(workspace: Path, module: str, scope_id: str, question_id: str) -> dict:
    """读取单题当前确认答案及就绪状态；问题不在当前模板时拒绝。"""
    state = read_qa(workspace, module, scope_id)
    for question, answer, readiness in answer_items(state):
        if question['question_id'] == question_id:
            return {'module': state['module'], 'scope_id': state['scope_id'],
                    'template_revision': state['template_revision'],
                    'answer_revision': answer['revision'] if answer else None,
                    'answer': answer, 'readiness': readiness}
    raise ValueError('问题不在当前模板中')
