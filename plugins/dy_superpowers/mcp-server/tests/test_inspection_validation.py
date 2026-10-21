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
              "acceptance_standard": criterion, "evidence_refs": ["odoo://ir.module.module/3"],
              "verification_method": "只读配置核验", "verified_at": datetime.now(timezone.utc).isoformat(),
              "result": "通过", "reason": "应用和依赖符合本期确认范围"}
    return {"expected_checks": [key], "records": [record]}


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


def test_duplicate_results_cannot_hide_unchecked_items(tmp_path, report):
    report['records'].append(dict(report['records'][0]))
    assert validate(tmp_path, report)['valid'] is False


def test_pending_research_can_be_reported_but_cannot_pass(tmp_path, report):
    record = report['records'][0]
    record.update(result='待确认', research_revision='', evidence_refs=[], reason='需要补充调研')
    result = validate(tmp_path, report)
    assert result['valid'] is True
    assert result['gate'] == '待确认'


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
    assert validate(tmp_path, {'expected_checks': [key], 'records': [record]})['gate'] == '通过'
