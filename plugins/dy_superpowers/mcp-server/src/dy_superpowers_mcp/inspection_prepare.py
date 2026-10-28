"""只读计算巡检的调研准备情况和旧结果复查依赖，不产生验收结论。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .inspection import KEY_FIELDS, RESULTS, _matrix
from .research import read_module_records, validate_checks
from .research_progress import _guide_items, calculate_progress
from .trace import TRACE_CODE_PATTERN


def _previous_records(records: list[dict]) -> dict[tuple, dict]:
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError('previous_records 必须是至多1000条巡检记录的列表')
    indexed = {}
    for record in records:
        if (not isinstance(record, dict) or any(not isinstance(record.get(field), str)
                or not record[field].strip() for field in KEY_FIELDS)):
            raise ValueError('上次巡检记录必须包含完整检查键、范围和环境')
        for field in ('research_revision', 'matrix_revision', 'verified_at', 'result'):
            if field in record and not isinstance(record[field], str):
                raise ValueError(f'上次巡检记录 {field} 必须是文本')
        key = tuple(record[field] for field in KEY_FIELDS)
        if key in indexed:
            raise ValueError('上次巡检记录包含重复的检查键、范围和环境')
        indexed[key] = record
    return indexed


def _recheck_reasons(previous: dict | None, research: dict | None, matrix_revision: str) -> list[str]:
    if previous is None:
        return ['no_previous_record']
    reasons = []
    if previous.get('matrix_revision') != matrix_revision:
        reasons.append('matrix_revision_changed')
    if research is None or previous.get('research_revision') != research['revision']:
        reasons.append('research_revision_changed')
    try:
        verified = datetime.fromisoformat(previous.get('verified_at', '').replace('Z', '+00:00'))
        if verified.tzinfo is None or verified > datetime.now(timezone.utc):
            raise ValueError('无效验证时间')
        if research is not None:
            updated = datetime.fromisoformat(research['updated_at'].replace('Z', '+00:00'))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if verified < updated:
                reasons.append('verification_predates_research')
    except (ValueError, TypeError, AttributeError, KeyError):
        reasons.append('invalid_verification_time')
    if previous.get('result') not in RESULTS:
        reasons.append('invalid_previous_result')
    return reasons


def prepare_inspection(workspace: Path, module: str, scope_id: str, environment: str, *,
                       checks: list[dict] | None = None, previous_records: list[dict] | None = None) -> dict:
    if any(not isinstance(value, str) or not value.strip() for value in (scope_id, environment)):
        raise ValueError('必须明确 scope_id 和 environment，不能猜测范围或环境')
    if len(environment) > 128 or not TRACE_CODE_PATTERN.fullmatch(environment):
        raise ValueError('environment 必须是与证据追踪一致的稳定环境标识，例如 UAT 或 PROD')
    matrix_revision, standards = _matrix(module)
    selected_keys = validate_checks(checks)
    if selected_keys is not None and not selected_keys <= standards.keys():
        raise ValueError('checks 含当前模块矩阵之外的检查键')
    previous = _previous_records([] if previous_records is None else previous_records)
    records = []
    while True:
        page, more = read_module_records(workspace, module, scope_id=scope_id,
                                        offset=len(records), limit=100, include_history=False)
        records.extend(page)
        if not more:
            break
        if not page:
            raise ValueError('调研记录分页没有前进')
    current = {}
    for record in records:
        key = (record['check_order'], record['research_id'])
        if key in current:
            raise ValueError('当前范围存在重复调研记录，不能选择其中一条用于巡检')
        current[key] = record
    questions = {(item['check_order'], item['research_id']): item['question']
                 for item in _guide_items(module)}
    items, used_previous = [], set()
    for key, standard in standards.items():
        if selected_keys is not None and key not in selected_keys:
            continue
        record = current.get(key)
        if record is None:
            readiness, reason = 'missing', '同范围调研缺失，需确认业务结论并授权保存'
        elif record['status'] == 'retired':
            readiness, reason = 'retired', '该范围记录已停用，需重新核对适用性'
        elif 'research_complete' not in record:
            readiness, reason = 'completeness_unknown', '旧记录完整性未知，不能默认完整'
        elif record['research_complete'] is not True:
            readiness, reason = 'incomplete', '仅有局部确认事实，尚不足以驱动明确巡检结果'
        else:
            readiness, reason = 'research_ready', ''
        full_key = (module, *key, scope_id, environment)
        prior = previous.get(full_key)
        if prior is not None:
            used_previous.add(full_key)
        reasons = _recheck_reasons(prior, record, matrix_revision)
        if readiness != 'research_ready':
            reasons.append('research_not_ready')
        items.append({'module': module, 'check_order': key[0], 'research_id': key[1],
                      'scope_id': scope_id, 'environment': environment,
                      'scope': record.get('scope') if record else None,
                      'research_revision': record['revision'] if record else None,
                      'matrix_revision': matrix_revision, 'acceptance_standard': standard,
                      'readiness': readiness, 'blocking_reason': reason,
                      'next_question': questions.get(key, f'请按验收标准补充 {key[0]}/{key[1]} 的业务依据')
                                       if reason else None,
                      'previous_result': prior.get('result') if prior else None,
                      'recheck_required': bool(reasons), 'recheck_reasons': reasons,
                      'evidence_reusable': False})
    progress = calculate_progress(module, records, scope_id=scope_id)
    ready = sum(item['readiness'] == 'research_ready' for item in items)
    unmatched = [{field: value[field] for field in KEY_FIELDS}
                 for key, value in previous.items() if key not in used_previous]
    return {'module': module, 'scope_id': scope_id, 'environment': environment,
            'matrix_revision': matrix_revision, 'items': items,
            'research_storage': {field: progress[field] for field in ('answered', 'total', 'percentage', 'route')},
            'preparation': {'total': len(items), 'research_ready': ready, 'blocked': len(items) - ready},
            'verification': {'performed': False, 'previous_results_reported': dict(Counter(
                item['previous_result'] for item in items if item['previous_result'] is not None))},
            'unmapped_records': progress['unmapped_records'],
            'completeness_unknown': progress['completeness_unknown'],
            'unmatched_previous': unmatched, 'execution_authorized': False,
            'limitations': 'research_ready 仅表示同范围调研已显式完整，仍需核对验收分支并采集实时证据。'
                           'next_question 是指南补问入口，需结合已有事实拆分为一次一题，不自动发起QA。'
                           '本工具未执行巡检；上次结果只作声明，未验证其证据，不自动沿用通过或改写历史报告。'}
