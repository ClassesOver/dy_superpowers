"""按模板问题列出确认 QA、附件要求及本轮缺口。"""
from pathlib import Path
from .qa_basis import READY, answer_items, attachment_detail_checks, configured_environment_info
from .qa_store import read_qa
from .trace import TRACE_CODE_PATTERN


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
    basis = {k: state[k] for k in ('module', 'scope_id', 'template_revision', 'answer_version')}
    basis['environment'] = environment
    basis['connection_fingerprint'] = configured_environment_info()['connection_fingerprint']
    if previous_records is not None and not isinstance(previous_records, list):
        raise ValueError('previous_records 必须是列表')
    items = []
    for q, a, readiness in answer_items(state):
        items.append({**q, 'answer_revision': a['revision'] if a else None,
            'answer': a['answer'] if a else None, 'confirmed_at': a['confirmed_at'] if a else None,
            'attachments': a['attachments'] if a else [], 'readiness': readiness,
            'detail_checks': attachment_detail_checks(a['attachments']) if a else [],
            'blocking_reason': '' if readiness == READY else '需补齐当前确认答案或附件要求',
            'next_question': q['question'] if readiness != READY else None,
            'recheck_required': True, 'recheck_reasons': ['live_verification_required'], 'evidence_reusable': False})
    ready = sum(i['readiness'] == READY for i in items)
    return {'module': module, 'scope_id': scope_id, 'environment': environment, 'basis': basis, 'items': items,
        'expected_checks': [{'question_id': q['question_id']} for q in questions],
        'research_storage': {k: state[k] for k in ('answered', 'total', 'percentage', 'route', 'confirmed_count', 'partial_count')},
        'preparation': {'total': len(items), 'research_ready': ready, 'blocked': len(items) - ready},
        'verification': {'performed': False}, 'execution_authorized': False,
        'limitations': '完整确认仅提供巡检依据，仍须逐项提取要求和采集本轮真实证据。历史结果不自动复用。'}
