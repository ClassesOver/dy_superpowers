"""巡检校验必须使用当前调研、矩阵版本和完整声明范围。"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import server
from dy_superpowers_mcp.research import read_record, write_record


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    args = dict(module="expense", check_order="1", research_id="01",
                config_item="应用安装", scope="公司A/本期", conclusion="本期必需", confirmed=True,
                confirmed_by="顾问", confirmed_at="2026-10-21")
    write_record(tmp_path, args)
    research = read_record(tmp_path, "expense", "1", "01")
    matrix = Path(__file__).parents[2] / 'skills/expense-inspection/references/inspection-matrix.md'
    text = matrix.read_text()
    criterion = next(line.split('|')[2].strip() for line in text.splitlines() if line.startswith('| 01｜'))
    key = {"module": "expense", "check_order": "1", "research_id": "01",
           "scope_id": research["scope_id"], "environment": "UAT"}
    record = {**key, "record_id": "EXP-UAT-001", "research_revision": research["revision"],
              "matrix_revision": hashlib.sha256(matrix.read_bytes()).hexdigest(),
              "acceptance_standard": criterion, "evidence_refs": [{"kind": "document", "reference": "odoo://ir.module.module/3"}],
              "verification_method": "只读配置核验", "verified_at": datetime.now(timezone.utc).isoformat(),
              "result": "通过", "reason": "应用和依赖符合本期确认范围"}
    all_checks = [
        {"module": "expense", "check_order": order, "research_id": research_id,
         "scope_id": key["scope_id"], "environment": key["environment"]}
        for order, research_id in sorted(_all_matrix_keys("expense"))
    ]
    excluded = [{**item, "reason": "不属于本次确认的上线范围"} for item in all_checks if item != key]
    return {"expected_checks": [key], "excluded_checks": excluded, "records": [record]}


def _all_matrix_keys(module):
    from dy_superpowers_mcp.inspection import _matrix
    _, standards = _matrix(module)
    return {(order, research_id) for order, research_id in standards}


def validate(root, report):
    result, error = server.execute_tool("dy_inspection_validate", {"trace_id": "inspection-validate", "report": report}, root)
    assert error is None
    return result


def test_current_complete_report_can_pass_without_writing_memory(tmp_path, report):
    result = validate(tmp_path, report)
    assert result['valid'] is True
    assert result['gate'] == '通过'
    assert result['execution_authorized'] is False
    assert not (tmp_path / '.dy_superpowers/delivery-register.md').exists()
    events = list((tmp_path / 'codex').rglob('*.jsonl'))
    assert events
    assert '应用和依赖符合本期确认范围' not in ''.join(path.read_text() for path in events)
    parsed = [json.loads(line) for path in events for line in path.read_text().splitlines()]
    assert any(event.get('event') == 'inspection.validate' and event.get('result') == '通过' for event in parsed)


@pytest.mark.parametrize('field,value', [
    ('evidence_refs', []), ('research_revision', 'old'), ('matrix_revision', 'old'),
    ('acceptance_standard', '另一个子项的标准'), ('environment', 'production'),
    ('verification_method', ''), ('verified_at', 'yesterday'),
])
def test_report_rejects_missing_or_mismatched_evidence_chain(tmp_path, report, field, value):
    report['records'][0][field] = value
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert result['gate'] != '通过'


def test_research_revision_change_and_retirement_invalidate_previous_pass(tmp_path, report):
    current = read_record(tmp_path, 'expense', '1', '01')
    write_record(tmp_path, {**current, 'confirmed': True, 'conclusion': '本期上线含扩展', 'expected_revision': current['revision']})
    assert validate(tmp_path, report)['valid'] is False
    current = read_record(tmp_path, 'expense', '1', '01')
    report['records'][0]['research_revision'] = current['revision']
    report['records'][0]['verified_at'] = datetime.now(timezone.utc).isoformat()
    assert validate(tmp_path, report)['valid'] is True
    write_record(tmp_path, {**current, 'confirmed': True, 'status': 'retired',
                           'retirement_reason': '范围取消', 'expected_revision': current['revision']})
    retired = read_record(tmp_path, 'expense', '1', '01')
    report['records'][0]['research_revision'] = retired['revision']
    assert validate(tmp_path, report)['valid'] is False


def test_coverage_requires_each_declared_check_once(tmp_path, report):
    report['records'] = []
    assert validate(tmp_path, report)['valid'] is False
    report['expected_checks'] = []
    assert validate(tmp_path, report)['valid'] is False


def test_declared_scope_must_cover_every_matrix_item(tmp_path, report):
    report['excluded_checks'] = []
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert any('矩阵' in error['message'] for error in result['errors'])


def test_odoo_evidence_must_reference_a_successful_query_in_trace(tmp_path, report):
    report['records'][0]['evidence_refs'] = [{
        'kind': 'odoo', 'reference': 'account.move:42',
        'trace_id': 'missing-query', 'model': 'account.move',
    }]
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert any('evidence_refs' in error['path'] for error in result['errors'])


def test_successful_odoo_query_in_trace_can_back_evidence(tmp_path, report):
    from dy_superpowers_mcp.trace import TraceStore
    store = TraceStore(tmp_path)
    context = {"module": "expense", "check_order": "1", "research_id": "01",
               "scope_id": report['records'][0]['scope_id'],
               "revision": report['records'][0]['research_revision'], "environment": "UAT"}
    store.append("odoo-evidence", {
        "event": "odoo.read", "operation": "search_read", "tool": "dy_odoo_record_search",
        "status": "ok", "model": "account.move", "count": 1, "span_id": "real-odoo-call",
        "trace_context": context,
    })
    store.append("odoo-evidence", {
        "event": "tool.end", "tool": "dy_odoo_record_search", "status": "ok", "span_id": "real-odoo-call",
    })
    report['records'][0]['evidence_refs'] = [{
        'kind': 'odoo', 'reference': 'account.move:42',
        'trace_id': 'odoo-evidence', 'model': 'account.move',
    }]
    result = validate(tmp_path, report)
    assert result['valid'] is True


def test_odoo_evidence_from_another_scope_cannot_back_report(tmp_path, report):
    from dy_superpowers_mcp.trace import TraceStore
    store = TraceStore(tmp_path)
    context = {"module": "expense", "check_order": "1", "research_id": "01",
               "scope_id": "another-company", "revision": report['records'][0]['research_revision'],
               "environment": "UAT"}
    store.append("wrong-scope", {"event": "odoo.read", "operation": "search_read",
                                 "tool": "dy_odoo_record_search", "status": "ok", "model": "account.move",
                                 "span_id": "call-a", "trace_context": context})
    store.append("wrong-scope", {"event": "tool.end", "tool": "dy_odoo_record_search",
                                 "status": "ok", "span_id": "call-a"})
    report['records'][0]['evidence_refs'] = [{'kind': 'odoo', 'reference': 'account.move:42',
                                              'trace_id': 'wrong-scope', 'model': 'account.move'}]
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert any('成功 Odoo 读取事件' in error['message'] for error in result['errors'])


def test_duplicate_results_cannot_hide_unchecked_items(tmp_path, report):
    report['records'].append(dict(report['records'][0]))
    assert validate(tmp_path, report)['valid'] is False


def test_pending_research_can_be_reported_but_cannot_pass(tmp_path, report):
    record = report['records'][0]
    record.update(result='待确认', research_revision='', evidence_refs=[], reason='需要补充调研')
    result = validate(tmp_path, report)
    assert result['valid'] is True
    assert result['gate'] == '待确认'


def test_future_verification_time_is_rejected(tmp_path, report):
    report['records'][0]['verified_at'] = '2999-01-01T00:00:00+00:00'
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert any('verified_at' in error['path'] for error in result['errors'])


def test_failed_and_inapplicable_results_have_explicit_gate(tmp_path, report):
    report['records'][0]['result'] = '不通过'
    assert validate(tmp_path, report)['gate'] == '不通过'
    report['records'][0]['result'] = '不适用'
    assert validate(tmp_path, report)['gate'] == '不适用'


def test_updating_revision_string_does_not_make_old_evidence_current(tmp_path, report):
    current = read_record(tmp_path, 'expense', '1', '01')
    write_record(tmp_path, {**current, 'confirmed': True, 'conclusion': '范围要求变更', 'expected_revision': current['revision']})
    latest = read_record(tmp_path, 'expense', '1', '01')
    report['records'][0]['research_revision'] = latest['revision']
    result = validate(tmp_path, report)
    assert result['valid'] is False
    assert any('verified_at' in error['path'] for error in result['errors'])


def test_general_ledger_uses_its_own_original_check_numbers(tmp_path, report):
    write_record(tmp_path, dict(module='general-ledger', check_order='EXP-1-001', research_id='01',
                               scope='公司A/本期', conclusion='总账本期必需', confirmed=True,
                               config_item='应用安装', confirmed_by='顾问', confirmed_at='2026-10-21'))
    research = read_record(tmp_path, 'general-ledger', 'EXP-1-001', '01')
    matrix = Path(__file__).parents[2] / 'skills/general-ledger-inspection/references/inspection-matrix.md'
    criterion = next(line.split('|')[2].strip() for line in matrix.read_text().splitlines() if line.startswith('| 01｜'))
    key = {**report['expected_checks'][0], 'module': 'general-ledger', 'check_order': 'EXP-1-001', 'scope_id': research['scope_id']}
    record = {**report['records'][0], **key, 'research_revision': research['revision'],
              'acceptance_standard': criterion, 'matrix_revision': hashlib.sha256(matrix.read_bytes()).hexdigest(),
              'verified_at': datetime.now(timezone.utc).isoformat()}
    all_checks = [
        {"module": "general-ledger", "check_order": order, "research_id": research_id,
         "scope_id": key["scope_id"], "environment": key["environment"]}
        for order, research_id in sorted(_all_matrix_keys("general-ledger"))
    ]
    excluded = [{**item, "reason": "不属于本次确认的上线范围"} for item in all_checks if item != key]
    assert validate(tmp_path, {'expected_checks': [key], 'excluded_checks': excluded,
                               'records': [record]})['gate'] == '通过'


def test_multi_check_matrix_keeps_scope_revision_and_pending_gate_separate(tmp_path, monkeypatch):
    """同一工作流内的多项检查应保留各自调研版本和范围，未确认项阻止整体通过。"""
    from dy_superpowers_mcp.trace import TraceStore

    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))

    matrix = Path(__file__).parents[2] / 'skills/expense-inspection/references/inspection-matrix.md'
    matrix_revision = hashlib.sha256(matrix.read_bytes()).hexdigest()
    from dy_superpowers_mcp.inspection import _matrix
    _, matrix_standards = _matrix('expense')
    standards = {order: matrix_standards[str(int(order)), order] for order in ('01', '02')}
    first_args = dict(module='expense', check_order='1', research_id='01', config_item='应用安装',
                      scope='公司A/2026一期', conclusion='费用基础应用纳入一期', confirmed=True,
                      confirmed_by='顾问', confirmed_at='2026-10-22')
    first, error = server.execute_tool('dy_research_record_write', {
        'trace_id': 'expense-matrix-flow', **first_args,
            'trace_context': {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '1',
                          'research_id': '01', 'scope_id': 'scope-company-a-r1'},
    }, tmp_path)
    assert error is None
    first_record = read_record(tmp_path, 'expense', '1', '01')
    assert first['scope_id'] == first_record['scope_id']

    first_context = {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '1',
                     'research_id': '01', 'scope_id': first_record['scope_id'], 'revision': first['revision']}
    read_result, error = server.execute_tool('dy_module_memory_read', {
        'trace_id': 'expense-matrix-flow', 'module': 'expense', 'check_order': '1', 'research_id': '01',
        'scope_id': first_record['scope_id'], 'trace_context': first_context,
    }, tmp_path)
    assert error is None and read_result['records'][0]['revision'] == first['revision']

    second, error = server.execute_tool('dy_research_record_write', {
        'trace_id': 'expense-matrix-flow', 'module': 'expense', 'check_order': '2', 'research_id': '02',
        'config_item': 'OCR相关模块', 'scope': '公司B/2026二期',
        'conclusion': 'OCR需求仍待业务确认', 'confirmed': True,
        'confirmed_by': '顾问', 'confirmed_at': '2026-10-22',
        'trace_context': {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '2',
                          'research_id': '02'},
    }, tmp_path)
    assert error is None
    second_record = read_record(tmp_path, 'expense', '2', '02')
    second_context = {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '2',
                      'research_id': '02', 'scope_id': second_record['scope_id'],
                      'revision': second['revision']}
    second_read, error = server.execute_tool('dy_module_memory_read', {
        'trace_id': 'expense-matrix-flow', 'module': 'expense', 'check_order': '2', 'research_id': '02',
        'scope_id': second_record['scope_id'], 'trace_context': second_context,
    }, tmp_path)
    assert error is None and second_read['records'][0]['revision'] == second['revision']

    key_a = {'module': 'expense', 'check_order': '1', 'research_id': '01',
             'scope_id': first_record['scope_id'], 'environment': 'UAT'}
    key_b = {'module': 'expense', 'check_order': '2', 'research_id': '02',
             'scope_id': second_record['scope_id'], 'environment': 'UAT'}
    now = datetime.now(timezone.utc).isoformat()
    passed = {**key_a, 'record_id': 'EXP-A-01', 'research_revision': first['revision'],
              'matrix_revision': matrix_revision, 'acceptance_standard': standards['01'],
              'evidence_refs': [{'kind': 'document', 'reference': 'odoo://ir.module.module/3'}], 'verification_method': '只读配置核验',
              'verified_at': now, 'result': '通过', 'reason': '公司A范围内符合验收标准'}
    pending = {**key_b, 'record_id': 'EXP-B-02', 'research_revision': second['revision'],
               'matrix_revision': matrix_revision, 'acceptance_standard': standards['02'],
               'evidence_refs': [], 'verification_method': '等待顾问补充调研',
               'verified_at': now, 'result': '待确认', 'reason': '公司B的OCR需求和票种尚未确认'}
    excluded = []
    for key in (key_a, key_b):
        excluded.extend(
            {"module": key["module"], "check_order": order, "research_id": research_id,
             "scope_id": key["scope_id"], "environment": key["environment"],
             "reason": "不属于对应公司本次声明范围"}
            for order, research_id in sorted(_all_matrix_keys("expense"))
            if (order, research_id) != (key["check_order"], key["research_id"])
        )
    result, error = server.execute_tool('dy_inspection_validate', {
        'trace_id': 'expense-matrix-flow', 'report': {
            'expected_checks': [key_a, key_b], 'excluded_checks': excluded, 'records': [passed, pending],
        },
    }, tmp_path)
    assert error is None
    assert result['valid'] is True and result['gate'] == '待确认', result
    assert result['execution_authorized'] is False

    trace_events = TraceStore(tmp_path).read('expense-matrix-flow')
    research_event = next(event for event in trace_events if event['event'] == 'research.write')
    assert research_event['check_key'] == 'expense:1:01'
    assert research_event['scope_id'] == first_record['scope_id']
    assert research_event['revision'] == first['revision']
    read_event = next(event for event in trace_events if event['event'] == 'memory.module.read')
    assert read_event['scope_id'] == first_record['scope_id']
    assert read_event['revision'] == first['revision']
    second_events = [event for event in trace_events if event.get('check_key') == 'expense:2:02']
    assert {event['scope_id'] for event in second_events if event.get('scope_id')} == {second_record['scope_id']}
    assert {event['revision'] for event in second_events if event.get('revision')} == {second['revision']}
    validation_event = next(event for event in trace_events if event['event'] == 'inspection.validate')
    assert validation_event['result'] == '待确认' and validation_event['valid'] is True


def test_supplemental_research_revision_requires_reinspection(tmp_path, report, monkeypatch):
    """跨轮补充调研产生新版本后，旧的通过记录必须失效并重新核验。"""
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    current = read_record(tmp_path, 'expense', '1', '01')
    initial_report = {
        'expected_checks': report['expected_checks'],
        'excluded_checks': report['excluded_checks'],
        'records': [{**report['records'][0], 'result': '待确认', 'research_revision': '',
                     'evidence_refs': [], 'reason': '本期是否覆盖更多公司尚待补充确认'}],
    }
    first_gate = validate(tmp_path, initial_report)
    assert first_gate['valid'] is True and first_gate['gate'] == '待确认'

    updated, error = server.execute_tool('dy_research_record_write', {
        'trace_id': 'inspection-validate', 'module': 'expense', 'check_order': '1', 'research_id': '01',
        'config_item': current['config_item'], 'scope': current['scope'], 'scope_id': current['scope_id'],
        'conclusion': '本期覆盖公司A和公司B', 'evidence_ref': '顾问确认纪要#2026-10-22',
        'confirmed': True, 'confirmed_by': '顾问', 'confirmed_at': '2026-10-22',
        'expected_revision': current['revision'],
    }, tmp_path)
    assert error is None
    assert updated['revision'] != current['revision']

    stale = dict(report)
    stale['records'] = [dict(report['records'][0])]
    assert validate(tmp_path, stale)['valid'] is False

    latest = read_record(tmp_path, 'expense', '1', '01')
    refreshed = dict(report)
    refreshed['records'] = [{**report['records'][0], 'scope_id': latest['scope_id'],
                             'research_revision': latest['revision'],
                             'verified_at': datetime.now(timezone.utc).isoformat(),
                             'reason': '已按补充确认范围重新核验'}]
    result = validate(tmp_path, refreshed)
    assert result['valid'] is True and result['gate'] == '通过'


def test_partial_scope_reports_coverage_and_partial_limitation(tmp_path, report):
    result = validate(tmp_path, report)
    assert result['valid'] is True
    scope = next(key for key in result['coverage'] if key.startswith('expense|'))
    assert result['coverage'][scope] == {'inspected': 1, 'excluded': 91, 'matrix': 92}
    assert '部分范围巡检' in result['limitations'] and '1/92' in result['limitations']


def test_full_scope_coverage_has_no_partial_limitation(tmp_path, report):
    from dy_superpowers_mcp.inspection import _matrix

    revision, standards = _matrix('expense')
    full = dict(report)
    full['excluded_checks'] = []
    full['expected_checks'] = [
        {'module': 'expense', 'check_order': order, 'research_id': research_id,
         'scope_id': report['expected_checks'][0]['scope_id'], 'environment': 'UAT'}
        for order, research_id in sorted(standards)]
    template = report['records'][0]
    full['records'] = [
        {**template, 'check_order': order, 'research_id': research_id, 'record_id': f'FULL-{index}',
         'acceptance_standard': standards[order, research_id],
         'result': '待确认', 'reason': '全量覆盖演示：待确认无需证据链',
         'research_revision': '', 'evidence_refs': []}
        for index, (order, research_id) in enumerate(sorted(standards))]
    result = validate(tmp_path, full)
    assert result['valid'] is True
    assert '部分范围巡检' not in result['limitations']
    scope = next(key for key in result['coverage'] if key.startswith('expense|'))
    assert result['coverage'][scope]['inspected'] == result['coverage'][scope]['matrix'] == 92
