"""归档查询参数必须到达 RPC，且不开放任意上下文。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))

import server
from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient
from dy_superpowers_mcp.trace import TraceStore


@pytest.mark.parametrize('active_test', [False, True])
def test_search_context_reaches_rpc_and_trace(tmp_path, monkeypatch, active_test):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    client = object.__new__(OdooReadClient)
    calls = []

    def execute(model, method, args, kwargs):
        calls.append((model, method, args, kwargs))
        return [{'id': 2, 'active': False}] if kwargs.get('context') == {'active_test': False} else []

    monkeypatch.setattr(client, '_execute_kw', execute)
    monkeypatch.setattr(server, '_odoo_client', lambda workspace: client)
    result, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'archive-read', 'model': 'dy.expense.city.season', 'args': [[]],
        'kwargs': {'fields': ['id', 'active'], 'limit': 10, 'offset': 3,
                   'context': {'active_test': active_test}}}, tmp_path)
    assert error is None
    assert calls == [('dy.expense.city.season', 'search_read', [[]], {
        'fields': ['id', 'active'], 'limit': 10, 'offset': 3,
        'context': {'active_test': active_test}})]
    assert result == ([{'id': 2, 'active': False}] if not active_test else [])
    events = TraceStore(tmp_path).read('archive-read')
    reads = [event for event in events if event.get('event') in {'odoo.query', 'odoo.read'}]
    assert len(reads) == 2
    assert all(event.get('active_test') is active_test for event in reads)
    assert all('rows' not in event for event in events)


@pytest.mark.parametrize('kwargs', [
    {'context': {'active_test': 'false'}}, {'context': {'active_test': 0}},
    {'context': {'allowed_company_ids': [2]}}, {'context': {'sudo': True}},
    {'context': []}, {'context': None}, {'order': 'id'}, {'unknown': True},
])
def test_unsupported_search_options_fail_before_rpc(tmp_path, monkeypatch, kwargs):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    monkeypatch.setattr(server, '_odoo_client', lambda workspace: pytest.fail('无效参数不得连接 Odoo'))
    _, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'invalid-context', 'model': 'dy.expense.city.season', 'kwargs': kwargs}, tmp_path)
    assert error and error['code'] == -32602
