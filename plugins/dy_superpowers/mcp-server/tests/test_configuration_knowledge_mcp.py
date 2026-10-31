"""通用配置的 MCP 边界：知识可续读，写入默认关闭。"""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server


@pytest.fixture(autouse=True)
def local_trace(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)


def test_knowledge_filters_reach_search(tmp_path, monkeypatch):
    received = []

    def search(query, **filters):
        received.append((query, filters))
        return {'query': query, 'count': 0, 'results': []}

    monkeypatch.setattr(server, 'knowledge_search', search)
    value, error = server.execute_tool('dy_knowledge_search', {
        'trace_id': 'knowledge-filter', 'query': '公司', 'module': 'dy_expense',
        'knowledge_type': 'source',
    }, tmp_path)
    assert error is None
    assert value['count'] == 0
    assert received == [('公司', {'module': 'dy_expense', 'knowledge_type': 'source'})]


def test_knowledge_discovery_does_not_offer_retired_model_filter():
    properties = server.TOOL_SCHEMAS['dy_knowledge_search']['properties']
    assert 'model' not in properties
    assert properties['knowledge_type']['enum'] == ['source', 'summary']


@pytest.mark.parametrize('filters', [{'model': 'dy.expense.report'}, {'knowledge_type': 'field'}])
def test_retired_knowledge_filters_are_rejected_before_backend(tmp_path, monkeypatch, filters):
    def unexpected_search(*args, **kwargs):
        pytest.fail('弃用筛选参数不应进入检索后端')

    monkeypatch.setattr(server, 'knowledge_search', unexpected_search)
    _, error = server.execute_tool('dy_knowledge_search', {
        'trace_id': 'retired-filter', 'query': '费用', **filters}, tmp_path)
    assert error and error['code'] == -32602


def test_knowledge_read_routes_reference_and_page(tmp_path, monkeypatch):
    received = []

    def read(reference, offset=0, limit=4000):
        received.append((reference, offset, limit))
        return {'reference': reference, 'content': '代码', 'complete': False, 'next_offset': 20}

    monkeypatch.setattr(server, 'knowledge_read', read, raising=False)
    value, error = server.execute_tool('dy_knowledge_read', {
        'trace_id': 'knowledge-read', 'reference': 'src:0123456789abcdef',
        'offset': 10, 'limit': 10,
    }, tmp_path)
    assert error is None
    assert value['next_offset'] == 20
    assert received == [('src:0123456789abcdef', 10, 10)]


def test_knowledge_search_limit_reaches_the_default_backend(tmp_path, monkeypatch):
    received = []

    def search(query, **filters):
        received.append(filters)
        return {'query': query, 'count': 0, 'results': []}

    monkeypatch.setattr(server, 'knowledge_search', search)
    _, error = server.execute_tool('dy_knowledge_search', {
        'trace_id': 'knowledge-limit', 'query': '公司', 'knowledge_type': 'source', 'limit': 3,
    }, tmp_path)
    assert error is None
    assert received == [{'knowledge_type': 'source', 'limit': 3}]


def test_hybrid_retrieval_mode_reaches_backend_and_is_discoverable(tmp_path, monkeypatch):
    received = []

    def search(query, **filters):
        received.append(filters)
        return {'query': query, 'count': 0, 'results': [], 'retrieval_mode': 'hybrid'}

    monkeypatch.setattr(server, 'knowledge_search', search)
    value, error = server.execute_tool('dy_knowledge_search', {
        'trace_id': 'knowledge-hybrid', 'query': '微信导入', 'retrieval_mode': 'hybrid', 'limit': 3,
    }, tmp_path)
    assert error is None
    assert value['retrieval_mode'] == 'hybrid'
    assert received == [{'limit': 3, 'retrieval_mode': 'hybrid'}]
    assert server.TOOL_SCHEMAS['dy_knowledge_search']['properties']['retrieval_mode']['enum'] == ['lexical', 'hybrid']


def test_knowledge_semantic_filter_rejection_is_visible_over_mcp(tmp_path):
    _, error = server.execute_tool('dy_knowledge_search', {
        'trace_id': 'knowledge-unsupported', 'query': '公司', 'knowledge_type': 'field',
    }, tmp_path)
    assert error and error['code'] == -32602


@pytest.mark.parametrize('tool', ['dy_odoo_record_create', 'dy_odoo_record_update'])
def test_configuration_tools_are_discoverable_but_not_generic_rpc(tool):
    schema = server.TOOL_SCHEMAS.get(tool)
    assert schema is not None
    assert schema['additionalProperties'] is False
    assert {'model', 'confirmed', 'company_id', 'values'} <= set(schema['required'])
    assert not {'method', 'kwargs', 'sudo', 'sql'} & set(schema['properties'])


def test_configuration_write_tools_are_hidden_and_rejected_while_switch_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv('DY_ODOO_CONFIG_WRITE_ENABLED', raising=False)
    listed = {tool['name'] for tool in server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']}
    assert not {'dy_odoo_record_create', 'dy_odoo_record_update', 'dy_odoo_import_execute'} & listed
    arguments = {'trace_id': 'config-preview',
        'environment': {'url': 'https://odoo.example.test', 'database': 'test'},
        'company_id': 2, 'model': 'dy.fin.account.analytic.plan',
        'values': {'name': '测试方案', 'code': 'X', 'company_id': 2},
        'idempotency_key': 'preview-only', 'confirmed': False, 'dry_run': True}
    _, error = server.execute_tool('dy_odoo_record_create', arguments, tmp_path)
    assert error and error['code'] == -32602 and 'DY_ODOO_CONFIG_WRITE_ENABLED' in error['message']
    from dy_superpowers_mcp.trace import TraceStore
    assert '测试方案' not in json.dumps(TraceStore(tmp_path).read('config-preview'), ensure_ascii=False)


def test_configuration_preview_is_available_once_switch_is_on(tmp_path, monkeypatch):
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    listed = {tool['name'] for tool in server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']}
    assert {'dy_odoo_record_create', 'dy_odoo_record_update', 'dy_odoo_import_execute'} <= listed
    preview, error = server.execute_tool('dy_odoo_record_create', {'trace_id': 'config-preview-on',
        'environment': {'url': 'https://odoo.example.test', 'database': 'test'},
        'company_id': 2, 'model': 'dy.fin.account.analytic.plan',
        'values': {'name': '测试方案', 'code': 'X', 'company_id': 2},
        'idempotency_key': 'preview-on', 'confirmed': False, 'dry_run': True}, tmp_path)
    assert error is None
    assert preview['status'] == 'confirmation_required' and len(preview['confirmation_digest']) == 64
    assert not (tmp_path / '.dy_superpowers/configuration').exists()


@pytest.mark.parametrize('reference', ['/etc/passwd', '../data/knowledge.sqlite3'])
def test_knowledge_read_rejects_paths(tmp_path, reference):
    _, error = server.execute_tool('dy_knowledge_read', {
        'trace_id': 'bad-reference', 'reference': reference}, tmp_path)
    assert error and error['code'] == -32602
