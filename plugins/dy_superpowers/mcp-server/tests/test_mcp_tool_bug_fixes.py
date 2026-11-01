"""MCP 工具缺陷回归：模板打包、错误映射、笔记标识、范围规范化、附件保存、导入幂等和分页。"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp import odoo_discovery, odoo_imports
from dy_superpowers_mcp.tools import runtime
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, OdooSettings
from dy_superpowers_mcp.qa_templates import load_template


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))


def call(root, name, **args):
    return server.execute_tool(name, {'trace_id': 'bug-fixes', **args}, root)


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_research_templates_are_packaged(module):
    template = load_template(module)
    assert template['module'] == module
    assert template['questions'] and template['revision']


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_session_initialize_works_with_packaged_templates(tmp_path, module):
    value, error = call(tmp_path, 'dy_session_initialize', source='startup')
    assert error is None
    assert module in value


class GapClient:
    uid = 2

    def search_read(self, model, domain, fields, limit, offset=0, **kwargs):
        return []

    def module_records(self, module=None, installed_only=False, offset=0):
        return []

    def close(self):
        pass


def test_capability_value_error_is_reported_as_request_error(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: GapClient())
    _, error = call(tmp_path, 'dy_knowledge_community_gaps', module='expense', scope_id='bad')
    assert error == {'code': -32602, 'message': '范围标识无效'}


def save(root, answers, scope='集团 / 总部', batch='b1'):
    template = load_template('expense')
    return call(root, 'dy_research_answers_save', module='expense', scope=scope,
                template_revision=template['revision'], batch_id=batch, confirmed=True, answers=answers)


def test_progress_scope_text_is_normalized_like_saved_scope(tmp_path):
    question = load_template('expense')['questions'][0]['question_id']
    _, error = save(tmp_path, [{'question_id': question, 'answer': '是', 'research_complete': True,
                                'expected_revision': None}])
    assert error is None
    for scope in ('集团 / 总部', '集团/总部'):
        value, error = call(tmp_path, 'dy_research_progress', module='expense', intent='progress', scope=scope)
        assert error is None and value['scope'] == '集团/总部'


def test_attachment_only_answer_for_unanswered_question_is_rejected(tmp_path):
    questions = load_template('expense')['questions']
    (tmp_path / 'a.txt').write_text('x')
    attachment = {'reference': 'a.txt', 'sha256': hashlib.sha256(b'x').hexdigest(), 'requirements': ['需要']}
    _, error = save(tmp_path, [
        {'question_id': questions[0]['question_id'], 'answer': '是', 'research_complete': True, 'expected_revision': None},
        {'question_id': questions[1]['question_id'], 'answer': '', 'research_complete': False,
         'expected_revision': None, 'attachments': [attachment]},
    ])
    assert error and error['code'] == -32602 and questions[1]['question_id'] in error['message']
    assert not (tmp_path / '.dy_superpowers' / 'modules' / 'expense' / 'qa' / 'scopes').exists()


def test_import_releases_idempotency_key_when_no_row_was_attempted(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_URL', 'https://odoo.example.test')
    monkeypatch.setenv('DB_NAME', 'demo')
    monkeypatch.setenv('APP_USER', 'operator')
    monkeypatch.setenv('APP_PASSWORD', 'secret-value')
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    monkeypatch.delenv('DY_ODOO_ENVIRONMENT', raising=False)

    class Client:
        settings = OdooSettings('https://odoo.example.test', 'demo', 'operator', 'secret-value')

    candidate = {'valid': True, 'rows': [{'name': 'A', 'code': 'A', 'company_id': 1}]}
    monkeypatch.setattr(odoo_imports, 'preview', lambda client, args: candidate)
    calls = []

    def reject(workspace, operation, arguments):
        calls.append(arguments)
        raise OdooConfigurationError('预检失败')

    monkeypatch.setattr(odoo_imports, 'write_configuration', reject)
    args = {'model': 'dy.fin.account.analytic.plan', 'company_id': 1, 'csv_data': 'name,code,company_id\nA,A,1\n',
            'environment': 'demo', 'idempotency_key': 'batch-1', 'confirmed': True}
    args['confirmation_digest'] = odoo_imports.digest('import', args)
    first = odoo_imports.execute(tmp_path, Client, args)
    assert first['attempted'] == 0
    second = odoo_imports.execute(tmp_path, Client, args)
    assert second.get('status') != 'already_attempted' and len(calls) == 2


def test_discovery_has_more_is_false_on_exact_last_page():
    class Client:
        def __init__(self, total):
            self.total = total

        def search_read(self, model, domain, fields, limit, offset=0):
            return [{'id': i} for i in range(offset, min(self.total, offset + limit))]

    exact = odoo_discovery.discover(Client(10), 'model_list', {'limit': 10})
    assert exact['count'] == 10 and exact['has_more'] is False and exact['next_offset'] is None
    more = odoo_discovery.discover(Client(11), 'model_list', {'limit': 10})
    assert more['count'] == 10 and more['has_more'] is True and more['next_offset'] == 10


def test_schema_consistency():
    for name in ('dy_research_answers_save', 'dy_research_answers_import'):
        assert server.TOOL_SCHEMAS[name]['properties']['trace_id']['pattern'] == server.TRACE_ID_PATTERN.pattern
    assert server.TOOL_SCHEMAS['dy_session_initialize']['additionalProperties'] is False
    _, error = call(Path('.'), 'dy_session_initialize', source='startup', unexpected=True)
    assert error and error['code'] == -32602
    assert 'legacy=true且省略view时按full' in server.TOOL_SCHEMAS['dy_module_memory_read']['properties']['view']['description']
    json.dumps(server.TOOL_SCHEMAS)
