"""归档查询参数必须到达 RPC，且不开放任意上下文。"""
import json
import threading
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))

import server
from dy_superpowers_mcp.tools import runtime
from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient, OdooRPCError
from dy_superpowers_mcp.trace import TraceStore


def test_record_read_does_not_apply_search_filters(monkeypatch):
    client = object.__new__(OdooReadClient)

    def execute(model, method, args, kwargs):
        assert (model, method, args, kwargs) == (
            'res.company', 'read', [[1]], {'fields': ['id', 'name']})
        return [{'id': 1, 'name': '医院'}]

    monkeypatch.setattr(client, '_execute_kw', execute)
    assert client.read_records('res.company', [1], ['id', 'name']) == [{'id': 1, 'name': '医院'}]


@pytest.mark.parametrize('rows', [None, [{}], [1], [{'id': True}], [{'id': 2}], [{'id': 1}, {'id': 1}]])
def test_record_read_rejects_malformed_or_unrequested_records(monkeypatch, rows):
    client = object.__new__(OdooReadClient)
    monkeypatch.setattr(client, '_execute_kw', lambda *args: rows)
    with pytest.raises(OdooRPCError, match='返回格式'):
        client.read_records('res.company', [1], ['id'])


@pytest.mark.parametrize('remote_name,category', [
    ('odoo.exceptions.AccessError', '权限拒绝'),
    ('odoo.exceptions.ValidationError', '业务校验失败'),
    ('odoo.exceptions.MissingError', '记录不存在或不可访问'),
    ('psycopg2.errors.InvalidTextRepresentation', '服务端错误'),
    ('malicious-private-token', '服务端错误'),
])
def test_rpc_error_returns_safe_category_and_local_request_id(remote_name, category):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'jsonrpc': '2.0', 'error': {'message': 'private-password',
                'data': {'name': remote_name, 'debug': 'private-traceback', 'message': 'private-token'}}}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    client = object.__new__(OdooReadClient)
    client._session, client._lock = Session(), threading.RLock()
    client._request_id, client._rpc_url = 40, 'https://example.test/jsonrpc'
    with pytest.raises(OdooRPCError) as error:
        client._rpc('object', 'execute_kw', [])
    assert category in str(error.value)
    assert '41' in str(error.value)
    assert 'private' not in str(error.value) and 'malicious' not in str(error.value)


@pytest.mark.parametrize('model,value,accepted', [
    ('res.company', True, True), ('res.company', False, True),
    ('res.company', 1, False), ('res.company', 'true', False),
    ('res.users', True, False),
])
def test_company_context_is_model_scoped(tmp_path, monkeypatch, model, value, accepted):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    client = object.__new__(OdooReadClient)

    def execute(actual_model, method, args, kwargs):
        assert accepted, '不允许的上下文不得到达 RPC'
        assert actual_model == 'res.company'
        assert kwargs['context'] == {'view_company': value}
        return [{'id': 1}]

    monkeypatch.setattr(client, '_execute_kw', execute)
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: client)
    result, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'company-context', 'model': model,
        'kwargs': {'fields': ['id'], 'context': {'view_company': value}}}, tmp_path)
    if accepted:
        assert error is None
        assert result == [{'id': 1}]
    else:
        assert error and error['code'] == -32602


@pytest.mark.parametrize('active_test', [False, True])
def test_search_context_reaches_rpc_and_trace(tmp_path, monkeypatch, active_test):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    client = object.__new__(OdooReadClient)
    calls = []

    def execute(model, method, args, kwargs):
        calls.append((model, method, args, kwargs))
        return [{'id': 2, 'active': False}] if kwargs.get('context') == {'active_test': False} else []

    monkeypatch.setattr(client, '_execute_kw', execute)
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: client)
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
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: pytest.fail('无效参数不得连接 Odoo'))
    _, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'invalid-context', 'model': 'dy.expense.city.season', 'kwargs': kwargs}, tmp_path)
    assert error and error['code'] == -32602


@pytest.mark.parametrize('extra', [
    {'domain': [['company_id', '=', 7]]},
    {'fields': ['id', 'company_id']}, {'limit': 1}, {'offset': 100},
    {'order': 'id asc'}, {'kwrags': {'limit': 1}},
])
def test_top_level_search_options_are_rejected_without_read_evidence(tmp_path, monkeypatch, extra):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: pytest.fail('无效参数不得连接 Odoo'))
    result, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'misplaced-options', 'model': 'account.journal', **extra}, tmp_path)
    assert result is None
    assert error and error['code'] == -32602
    events = TraceStore(tmp_path).read('misplaced-options')
    assert not any(event.get('event') in {'odoo.query', 'odoo.read'} for event in events)
    assert not any(event.get('event') == 'tool.end' and event.get('status') == 'ok' for event in events)


def test_scoped_search_preserves_shared_records_domain_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    client = object.__new__(OdooReadClient)
    calls = []

    def execute(model, method, args, kwargs):
        calls.append((model, method, args, kwargs))
        return [{'id': 12, 'journal_company_ids': []}]

    monkeypatch.setattr(client, '_execute_kw', execute)
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: client)
    result, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'scoped-read', 'model': 'account.journal',
        'trace_context': {'module': 'general-ledger', 'scope_id': 'company-7'},
        'args': [[['account_structure_id', '=', 3], '|',
                  ['journal_company_ids', '=', False], ['journal_company_ids', 'in', [7]]]],
        'kwargs': {'fields': ['id', 'journal_company_ids'], 'limit': 100, 'offset': 100}}, tmp_path)
    assert error is None
    assert result == [{'id': 12, 'journal_company_ids': []}]
    assert calls == [('account.journal', 'search_read', [
        [['account_structure_id', '=', 3], '|', ['journal_company_ids', '=', False],
         ['journal_company_ids', 'in', [7]]]],
        {'fields': ['id', 'journal_company_ids'], 'limit': 100, 'offset': 100})]
    reads = [event for event in TraceStore(tmp_path).read('scoped-read')
             if event.get('event') == 'odoo.read']
    assert len(reads) == 1
    assert json.loads(reads[0]['domain']) == [['account_structure_id', '=', 3], '|',
                                  ['journal_company_ids', '=', False], ['journal_company_ids', 'in', [7]]]
    assert reads[0]['offset'] == 100
