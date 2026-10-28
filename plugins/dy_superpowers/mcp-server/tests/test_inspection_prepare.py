"""巡检准备只给出依赖与缺口，不把记忆或历史通过当作本轮验收。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp.research import read_record, write_record


@pytest.fixture(autouse=True)
def isolate_trace_home(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))


def save(workspace, order, item, complete=True, **updates):
    data = dict(module='expense', check_order=order, research_id=item, config_item='测试配置',
                conclusion='测试范围已确认', scope='甲院/一期', scope_id='site-a',
                confirmed=True, confirmed_by='测试', confirmed_at='2026-10-28')
    if complete is not None:
        data['research_complete'] = complete
    data.update(updates)
    return write_record(workspace, data)


def prepare(workspace, **updates):
    args = dict(trace_id='prepare', module='expense', scope_id='site-a', environment='UAT')
    args.update(updates)
    result, error = server.execute_tool('dy_inspection_prepare', args, workspace)
    assert error is None, error
    return result


def test_prepare_separates_readiness_storage_and_actual_verification(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    save(tmp_path, '1', '01')
    save(tmp_path, '2', '02', False)
    save(tmp_path, '3', '03', None)
    save(tmp_path, '4', '04', status='retired', retirement_reason='本期停用')
    save(tmp_path, '1', '05', scope='乙院', scope_id='site-b')
    path = tmp_path / '.dy_superpowers/modules/expense/research.md'
    from dy_superpowers_mcp.research import _visible
    legacy = read_record(tmp_path, 'expense', '1', '01')
    legacy = {key: value for key, value in legacy.items() if key != 'revision'}
    legacy['check_order'] = 'legacy'
    path.write_text(path.read_text() + '\n\n' + '\n'.join(_visible(legacy)) + '\n')
    before = path.read_bytes()
    result = prepare(tmp_path)
    states = {(i['check_order'], i['research_id']): i for i in result['items']}
    assert states['1', '01']['readiness'] == 'research_ready'
    assert states['2', '02']['readiness'] == 'incomplete'
    assert states['3', '03']['readiness'] == 'completeness_unknown'
    assert states['4', '04']['readiness'] == 'retired'
    assert states['1', '05']['readiness'] == 'missing'
    assert states['2', '02']['next_question']
    assert states['1', '01']['acceptance_standard']
    assert result['research_storage']['answered'] == 1
    assert result['preparation'] == {'total': 92, 'research_ready': 1, 'blocked': 91}
    assert result['verification']['performed'] is False
    assert result['execution_authorized'] is False
    assert 'conclusion' not in json.dumps(result, ensure_ascii=False)
    assert result['unmapped_records'][0]['check_order'] == 'legacy'
    assert path.read_bytes() == before


def test_supplement_only_unblocks_its_key_and_revision_change_requires_recheck(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    first = save(tmp_path, '1', '01')
    second = save(tmp_path, '2', '02', False)
    checks = [dict(check_order='1', research_id='01'), dict(check_order='2', research_id='02')]
    initial = prepare(tmp_path, checks=checks)
    previous = [dict(module='expense', check_order='1', research_id='01', scope_id='site-a',
                     environment='UAT', research_revision=first['revision'],
                     matrix_revision=initial['matrix_revision'], verified_at=datetime.now(timezone.utc).isoformat(),
                     result='通过')]
    unchanged = prepare(tmp_path, checks=checks, previous_records=previous)
    assert unchanged['items'][0]['recheck_required'] is False
    assert unchanged['items'][0]['evidence_reusable'] is False
    save(tmp_path, '2', '02', expected_revision=second['revision'])
    supplemented = prepare(tmp_path, checks=checks, previous_records=previous)
    assert supplemented['preparation']['research_ready'] == 2
    assert supplemented['items'][0]['research_revision'] == first['revision']
    save(tmp_path, '1', '01', conclusion='修订范围', expected_revision=first['revision'])
    changed = prepare(tmp_path, checks=checks, previous_records=previous)
    assert changed['items'][0]['recheck_required'] is True
    assert 'research_revision_changed' in changed['items'][0]['recheck_reasons']
    # 只替换版本号，不能让早于新调研的验证时间生效。
    previous[0]['research_revision'] = changed['items'][0]['research_revision']
    assert 'verification_predates_research' in prepare(tmp_path, checks=checks, previous_records=previous)['items'][0]['recheck_reasons']


def test_prepare_trace_keeps_readiness_counts_and_environment_without_business_content(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    save(tmp_path, '1', '01')
    prepare(tmp_path, checks=[dict(check_order='1', research_id='01'),
                              dict(check_order='2', research_id='02')])
    events = [json.loads(line) for path in (tmp_path / 'codex').rglob('*.jsonl')
              for line in path.read_text().splitlines()]
    event = next(item for item in events if item['event'] == 'inspection.prepare')
    assert event['expected_count'] == 2
    assert event['count'] == 1
    assert event['result_count'] == 1
    assert event['trace_context']['environment'] == 'UAT'
    assert '测试范围已确认' not in json.dumps(event, ensure_ascii=False)


@pytest.mark.parametrize('environment', ['生产环境', 'UAT east', 'UAT' + 'x'*126])
def test_prepare_rejects_environment_that_cannot_identify_later_evidence_trace(tmp_path, environment):
    result, error = server.execute_tool('dy_inspection_prepare', dict(
        trace_id='invalid-environment', module='expense', scope_id='site-a',
        environment=environment), tmp_path)
    assert result is None and error['code'] == -32602


@pytest.mark.parametrize('changes', [dict(environment='PROD'), dict(scope_id='site-b'), dict(matrix_revision='old')])
def test_previous_context_mismatch_is_not_reused(tmp_path, monkeypatch, changes):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    current = save(tmp_path, '1', '01')
    checks = [dict(check_order='1', research_id='01')]
    base = prepare(tmp_path, checks=checks)
    prior = dict(module='expense', check_order='1', research_id='01', scope_id='site-a', environment='UAT',
                 matrix_revision=base['matrix_revision'], research_revision=current['revision'],
                 verified_at=datetime.now(timezone.utc).isoformat(), result='通过')
    prior.update(changes)
    result = prepare(tmp_path, checks=checks, previous_records=[prior])
    assert result['items'][0]['evidence_reusable'] is False
    if 'matrix_revision' in changes:
        assert 'matrix_revision_changed' in result['items'][0]['recheck_reasons']
    else:
        assert len(result['unmatched_previous']) == 1
        assert result['items'][0]['previous_result'] is None


@pytest.mark.parametrize('changes', [dict(scope_id=''), dict(environment=''),
    dict(checks=[]), dict(checks=[dict(check_order='999', research_id='01')]),
    dict(checks=[dict(check_order='1', research_id='01')]*2)])
def test_prepare_rejects_ambiguous_scope_and_unknown_or_duplicate_keys(tmp_path, changes):
    args = dict(trace_id='invalid', module='expense', scope_id='site-a', environment='UAT')
    args.update(changes)
    result, error = server.execute_tool('dy_inspection_prepare', args, tmp_path)
    assert result is None and error['code'] == -32602


def test_general_ledger_prepare_uses_original_matrix_keys_and_scoped_qa(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    saved = write_record(tmp_path, dict(module='general-ledger', check_order='GL-1-001', research_id='01',
                        config_item='应用', conclusion='本期启用', scope='甲院', scope_id='site-a',
                        confirmed=True, confirmed_by='测试', confirmed_at='2026-10-28', research_complete=True))
    result, error = server.execute_tool('dy_inspection_prepare',
        dict(trace_id='gl-prepare', module='general-ledger', scope_id='site-a', environment='UAT'), tmp_path)
    assert error is None
    assert result['preparation'] == {'total': 82, 'research_ready': 1, 'blocked': 81}
    assert result['items'][0]['check_order'] == 'GL-1-001'
    assert result['items'][0]['research_revision'] == saved['revision']
    assert result['items'][1]['next_question']


@pytest.mark.parametrize('previous', [[{}], [dict(module='expense', check_order='1', research_id='01',
    scope_id='site-a', environment='UAT')]*2])
def test_prepare_rejects_invalid_or_duplicate_previous_records(tmp_path, previous):
    result, error = server.execute_tool('dy_inspection_prepare',
        dict(trace_id='invalid-previous', module='expense', scope_id='site-a', environment='UAT',
             previous_records=previous), tmp_path)
    assert result is None and error['code'] == -32602


@pytest.mark.parametrize('changes', [dict(result=[]), dict(result={}), dict(research_revision=[]),
    dict(matrix_revision={}), dict(verified_at=42)])
def test_previous_malformed_values_return_invalid_params_not_server_failure(tmp_path, monkeypatch, changes):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    prior = dict(module='expense', check_order='1', research_id='01', scope_id='site-a',
                 environment='UAT', result='通过')
    prior.update(changes)
    result, error = server.execute_tool('dy_inspection_prepare',
        dict(trace_id='malformed-previous', module='expense', scope_id='site-a', environment='UAT',
             previous_records=[prior]), tmp_path)
    assert result is None and error['code'] == -32602
