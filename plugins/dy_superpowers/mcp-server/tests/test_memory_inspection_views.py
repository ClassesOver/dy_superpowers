"""巡检按键取当前事实，历史仅在实际请求的页内展开。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
import dy_superpowers_mcp.research as research


def save(workspace, order='1', **changes):
    data = dict(module='expense', check_order=order, research_id='01', config_item='应用',
                conclusion='本期启用', scope='甲院/本期', scope_id='site-a',
                confirmed=True, confirmed_by='测试', confirmed_at='2026-10-28',
                research_complete=True)
    data.update(changes)
    return research.write_record(workspace, data)


def test_module_page_expands_history_only_after_pagination(tmp_path, monkeypatch):
    for order in ('1', '2', '3'):
        saved = save(tmp_path, order)
        save(tmp_path, order, conclusion=f'修订{order}', expected_revision=saved['revision'])
    original = research._read_history
    expanded = []
    def track(directory, history):
        expanded.append(history[0]['check_order'])
        return original(directory, history)
    monkeypatch.setattr(research, '_read_history', track)
    page, more = research.read_module_records(tmp_path, 'expense', offset=1, limit=1)
    assert page[0]['check_order'] == '2' and more is True
    assert page[0]['history'][0]['conclusion'] == '本期启用'
    assert expanded == ['2']


def test_current_and_summary_views_skip_history_and_do_not_change_memory(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    saved = save(tmp_path)
    save(tmp_path, conclusion='修订结论', expected_revision=saved['revision'])
    path = tmp_path / '.dy_superpowers/modules/expense/research.md'
    before = path.read_bytes()
    monkeypatch.setattr(research, '_read_history', lambda *_: pytest.fail('当前视图不应展开历史'))
    args = dict(legacy=True, trace_id='views', module='expense', scope_id='site-a')
    current, error = server.execute_tool('dy_module_memory_read', dict(args, view='current'), tmp_path)
    assert error is None
    record = current['records'][0]
    assert record['conclusion'] == '修订结论' and record['history_count'] == 1
    assert 'history' not in record
    summary, error = server.execute_tool('dy_module_memory_read', dict(args, view='summary'), tmp_path)
    assert error is None
    assert summary['records'][0]['revision'] == record['revision']
    assert 'conclusion' not in summary['records'][0] and 'history' not in summary['records'][0]
    assert path.read_bytes() == before


def test_batch_keys_do_not_mix_scopes_and_paginate_selected_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    for order in ('1', '2', '3'):
        save(tmp_path, order)
    save(tmp_path, '2', scope='乙院', scope_id='site-b', conclusion='其他范围')
    args = dict(legacy=True, trace_id='batch', module='expense', view='current', scope_id='site-a', limit=1,
                checks=[dict(check_order='2', research_id='01'), dict(check_order='3', research_id='01')])
    first, error = server.execute_tool('dy_module_memory_read', args, tmp_path)
    assert error is None and first['next_offset'] == 1
    second, error = server.execute_tool('dy_module_memory_read', dict(args, offset=1), tmp_path)
    assert error is None and second['has_more'] is False
    assert [r['check_order'] for r in first['records'] + second['records']] == ['2', '3']
    assert all(r['scope_id'] == 'site-a' for r in first['records'] + second['records'])


def test_default_qa_does_not_echo_legacy_unmapped_records(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    save(tmp_path, conclusion='不应重复输出')
    result, error = server.execute_tool('dy_module_memory_read',
        dict(trace_id='new-view', module='expense'), tmp_path)
    assert error is None and result['records'] == [] and result['answered'] == 0
    assert '不应重复输出' not in str(result)


@pytest.mark.parametrize('view', ['current', 'summary', 'full'])
def test_read_views_reject_corrupted_record_instead_of_reporting_missing(tmp_path, monkeypatch, view):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    save(tmp_path)
    path = tmp_path / '.dy_superpowers/modules/expense/research.md'
    path.write_text(path.read_text().replace('本期启用', '未经确认的编辑'))
    result, error = server.execute_tool('dy_module_memory_read',
        dict(trace_id='corrupted', module='expense', scope_id='site-a', view=view), tmp_path)
    assert result is None and error['code'] == -32602


@pytest.mark.parametrize('extra', [dict(checks=[]),
    dict(checks=[dict(check_order='1', research_id='01')]*2),
    dict(check_order='1', research_id='01', checks=[dict(check_order='1', research_id='01')])])
def test_read_rejects_invalid_or_conflicting_batch_keys(tmp_path, extra):
    result, error = server.execute_tool('dy_module_memory_read',
        dict(trace_id='invalid-batch', module='expense', **extra), tmp_path)
    assert result is None and error['code'] == -32602
