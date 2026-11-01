"""Odoo 能力的离线契约；不连接账套、不执行实际模块操作。"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp.tools import runtime
from dy_superpowers_mcp.odoo_jsonrpc import OdooRPCError, OdooSettings


TOOLS = ('module_list_available', 'module_install', 'module_uninstall',
         'model_list', 'model_search_field', 'import_preview', 'import_execute', 'import_template',
         'analyze_requirements', 'design_solution', 'validate_plan', 'record_execute',
         'diagnostics_health_check')


class Client:
    uid = 7

    def __init__(self):
        self.calls = []
        self.settings = OdooSettings('https://odoo.example.test', 'demo', 'operator', 'private-password')
        self.state = 'uninstalled'
        self.fail = False

    def close(self):
        pass

    def _rpc(self, service, method, args):
        assert (service, method, args) == ('common', 'version', [])
        return {'server_version_info': [12, 0, 0], 'server_version': '12.0'}

    def search_read(self, model, domain, fields, limit, offset=0, **kwargs):
        self.calls.append((model, 'search_read', copy.deepcopy(domain), fields, limit, offset))
        if self.fail:
            raise OdooRPCError('private-password')
        if model == 'ir.module.module':
            if ['auto_install', '=', True] in domain:
                return []
            if ['state', 'in', ['to install', 'to upgrade', 'to remove']] in domain:
                return []
            return [] if offset else [{'id': 8, 'name': 'dy_expense', 'state': self.state,
                                      'latest_version': '12.0.1.0', 'dependencies_id': []}]
        if model == 'ir.model':
            return [{'id': 2, 'model': 'dy.expense.policy', 'name': '费用政策', 'transient': False}]
        if model == 'ir.model.fields':
            return [{'id': 3, 'model': 'dy.expense.policy', 'name': 'company_ids', 'ttype': 'many2many'}]
        if model == 'res.users':
            return [{'id': 7, 'company_id': [2, '甲院'], 'company_ids': [2]}]
        if model == 'res.company':
            return [{'id': 2}]
        return []

    def fields_get(self, model):
        return {'name': {'type': 'char', 'required': True, 'readonly': False, 'string': '名称'},
                'code': {'type': 'char', 'required': True, 'readonly': False},
                'company_id': {'type': 'many2one', 'relation': 'res.company', 'required': True, 'readonly': False},
                'id': {'type': 'integer', 'readonly': True}}

    def _execute_kw(self, model, method, args, kwargs):
        self.calls.append((model, method, copy.deepcopy(args), copy.deepcopy(kwargs)))
        if method == 'check_access_rights':
            return True
        if method.startswith('button_immediate_'):
            if self.fail:
                raise OdooRPCError('private-password')
            self.state = 'uninstalled' if method.endswith('uninstall') else 'installed'
            return False  # Odoo 按钮可能返回 False，必须回读状态。
        raise AssertionError((model, method))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setenv('APP_URL', 'https://odoo.example.test')
    monkeypatch.setenv('DB_NAME', 'demo')
    monkeypatch.setenv('APP_USER', 'operator')
    monkeypatch.setenv('APP_PASSWORD', 'private-password')
    monkeypatch.delenv('DY_ODOO_ENVIRONMENT', raising=False)
    for key in ('DY_ODOO_MODULE_WRITE_ENABLED', 'DY_ODOO_RECORD_DELETE_ENABLED', 'DY_ODOO_MODULE_ALLOWED'):
        monkeypatch.delenv(key, raising=False)
    fake = Client()
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: fake)
    return fake


def call(root, name, **args):
    value, error = server.execute_tool(name, {'trace_id': 'odoo-capabilities', **args}, root)
    assert error is None, error
    return value


@pytest.mark.parametrize('name', TOOLS)
def test_migrated_tool_is_registered_with_closed_schema(name):
    schema = server.TOOL_SCHEMAS.get('dy_odoo_' + name)
    assert schema is not None
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('name,extra,model', [
    ('module_list_available', {'category': '财务'}, 'ir.module.module'),
    ('model_list', {'search': '费用'}, 'ir.model'),
    ('model_search_field', {'query': 'company', 'model': 'dy.expense.policy'}, 'ir.model.fields'),
])
def test_discovery_reads_bounded_actual_metadata(tmp_path, client, name, extra, model):
    value = call(tmp_path, 'dy_odoo_' + name, offset=4, limit=25, **extra)
    assert value['offset'] == 4 and value['limit'] == 25
    assert client.calls[-1][0] == model
    assert client.calls[-1][-2:] == (25, 4)


def lifecycle():
    return dict(module_names=['dy_expense'], environment='demo', idempotency_key='module-batch',
                backup_reference='已核实的备份编号', confirmed=False, dry_run=True)


def test_module_tools_are_hidden_and_rejected_while_switch_is_off(tmp_path, client):
    listed = {tool['name'] for tool in server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']}
    assert not {'dy_odoo_module_install', 'dy_odoo_module_uninstall'} & listed
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='disabled', **lifecycle()), tmp_path)
    assert error and error['code'] == -32602 and 'DY_ODOO_MODULE_WRITE_ENABLED' in error['message']
    assert not client.calls


def test_module_upgrade_is_not_registered():
    assert 'dy_odoo_module_upgrade' not in server.TOOL_SCHEMAS


def test_module_preview_and_digest_binding(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    listed = {tool['name'] for tool in server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']}
    assert {'dy_odoo_module_install', 'dy_odoo_module_uninstall'} <= listed
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_install', **args)
    assert preview['execution_authorized'] is False
    assert not any(c[1].startswith('button_immediate_') for c in client.calls)
    calls_after_preview = len(client.calls)
    args.update(dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])
    args['module_names'] = ['base']
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='changed', **args), tmp_path)
    assert error and len(client.calls) == calls_after_preview


def test_module_install_readback_and_idempotency(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_install', **args)
    args.update(dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])
    result = call(tmp_path, 'dy_odoo_module_install', **args)
    assert result['execution_status'] == 'succeeded' and result['data_readback_status'] == 'passed'
    assert result['accepted'] is False
    calls = len(client.calls)
    assert call(tmp_path, 'dy_odoo_module_install', **args)['status'] == 'already_attempted'
    assert len(client.calls) == calls
    text = ''.join(p.read_text() for p in (tmp_path / '.dy_superpowers').rglob('*.json*'))
    assert 'private-password' not in text


def test_health_failure_cannot_be_overwritten_by_later_success(tmp_path, client):
    client.fail = True
    result = call(tmp_path, 'dy_odoo_diagnostics_health_check')
    assert result['overall'] == 'unhealthy'
    assert 'private-password' not in json.dumps(result)
    assert any(c['status'] == 'not_checked' for c in result['checks'])


def test_import_preview_validates_rows_and_never_writes(tmp_path, client):
    args = dict(model='dy.fin.account.analytic.plan', company_id=2,
                csv_data='name,code,company_id\n方案,A,2\n')
    value = call(tmp_path, 'dy_odoo_import_preview', **args)
    assert value['valid'] and value['total_rows'] == 1
    assert value['rows'][0]['company_id'] == 2
    assert value['execution_authorized'] is False
    bad = call(tmp_path, 'dy_odoo_import_preview', **dict(args, csv_data='name,name\n甲,乙\n'))
    assert not bad['valid']
    assert not any(c[1] in {'create', 'load', 'write'} for c in client.calls)


def test_import_template_uses_whitelist_and_writable_metadata(tmp_path, client):
    value = call(tmp_path, 'dy_odoo_import_template', model='dy.fin.account.analytic.plan', include_optional=True)
    assert set(value['csv_header'].strip().split(',')) == {'name', 'code', 'company_id'}
    _, error = server.execute_tool('dy_odoo_import_template', dict(trace_id='bad', model='account.move'), tmp_path)
    assert error


def test_record_execute_cannot_dispatch_arbitrary_methods(tmp_path, client):
    _, error = server.execute_tool('dy_odoo_record_execute', dict(trace_id='bad-method',
        model='account.move', method='action_post', ids=[1]), tmp_path)
    assert error and not client.calls


def test_planning_is_candidate_only_and_plan_validation_rejects_cycles(tmp_path, client):
    requirements = call(tmp_path, 'dy_odoo_analyze_requirements', description='本期费用报销需要预算控制')
    assert requirements['execution_authorized'] is False
    assert requirements['modules_needed'] and not client.calls
    plan = call(tmp_path, 'dy_odoo_design_solution', requirements=requirements)
    assert plan['execution_authorized'] is False and plan['phases']
    result = call(tmp_path, 'dy_odoo_validate_plan', plan={'phases': [
        {'phase': 1, 'depends_on': [2], 'steps': []}, {'phase': 2, 'depends_on': [1], 'steps': []}]})
    assert not result['valid'] and result['execution_authorized'] is False


def test_gap_analysis_uses_confirmed_answer_not_generic_sales_advice(tmp_path, client):
    from dy_superpowers_mcp.qa_store import commit_qa
    from dy_superpowers_mcp.qa_templates import load_template
    template = load_template('expense')
    state = commit_qa(tmp_path, 'expense', scope='甲院', template_revision=template['revision'],
        batch_id='gap-batch', confirmed=True, confirmed_by='测试', answers=[dict(
            question_id='DY-EXP-01', answer='本期交付OCR识别（ocr_expense）。',
            research_complete=False, expected_revision=None)])
    result = call(tmp_path, 'dy_knowledge_community_gaps', module='expense', scope_id=state['scope_id'])
    assert result['answer_version'] == state['answer_version']
    assert any(g['module'] == 'ocr_expense' for g in result['gaps'])
    assert result['accepted'] is False
    assert not any(g.get('module') == 'crm' for g in result['gaps'])


def test_gap_analysis_excludes_explicitly_uninstalled_rows(tmp_path, client):
    from dy_superpowers_mcp.odoo_discovery import installed_modules
    assert installed_modules(client) == []


def test_module_wrong_environment_rejected_before_connection(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_install', **args)
    args['environment'] = 'other'
    args.update(dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])
    def forbidden(root):
        pytest.fail('环境不符不得创建连接')
    monkeypatch.setattr(runtime, 'odoo_client', forbidden)
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='other-env', **args), tmp_path)
    assert error and error['code'] == -32602


def test_import_digest_requires_environment_and_batch_identity(tmp_path, client):
    value = call(tmp_path, 'dy_odoo_import_preview', model='dy.fin.account.analytic.plan',
        company_id=2, csv_data='name,code,company_id\n方案,A,2\n')
    assert value.get('confirmation_digest') is None
    assert 'environment' in value['missing_confirmation_fields']


def test_import_preview_rejects_missing_selection_values(tmp_path, client, monkeypatch):
    fields = client.fields_get('x')
    del fields['code']
    fields['cal_type'] = {'type': 'selection', 'readonly': False, 'selection': [('rate', '比例')]}
    monkeypatch.setattr(client, 'fields_get', lambda model: fields)
    result = call(tmp_path, 'dy_odoo_import_preview', model='dy.expense.personal.tax.calculation.rule', company_id=2,
        csv_data='name,company_id,cal_type\n规则,2,invalid\n')
    assert result['valid'] is False
    assert any('cal_type' in error for error in result['errors'])


def test_import_preview_valid_selection_is_accepted(tmp_path, client, monkeypatch):
    fields = client.fields_get('x')
    del fields['code']
    fields['cal_type'] = {'type': 'selection', 'readonly': False, 'selection': [('rate', '比例')]}
    monkeypatch.setattr(client, 'fields_get', lambda model: fields)
    result = call(tmp_path, 'dy_odoo_import_preview', model='dy.expense.personal.tax.calculation.rule', company_id=2,
        csv_data='name,company_id,cal_type\n规则,2,rate\n')
    assert result['valid']


def test_module_missing_dependency_cannot_trigger_unconfirmed_install(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    original = client.search_read
    def read(model, domain, fields, limit, offset=0, **kwargs):
        if model == 'ir.module.module.dependency' and ['module_id', 'in', [8]] in domain:
            return [{'id': 4, 'name': 'unapproved_dependency', 'module_id': [8, '费用'] }]
        if model == 'ir.module.module' and ['name', 'in', ['unapproved_dependency']] in domain:
            return [{'id': 9, 'name': 'unapproved_dependency', 'state': 'uninstalled'}]
        return original(model, domain, fields, limit, offset, **kwargs)
    monkeypatch.setattr(client, 'search_read', read)
    args = lifecycle()
    result = call(tmp_path, 'dy_odoo_module_install', **args)
    args.update(dry_run=False, confirmed=True, confirmation_digest=result['confirmation_digest'])
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='dependencies', **args), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in client.calls)


def test_discovery_trace_records_domain_not_returned_rows(tmp_path, client):
    call(tmp_path, 'dy_odoo_model_list', search='费用')
    events = [json.loads(line) for path in server.TraceStore(tmp_path).directory.glob('*.jsonl') for line in path.read_text().splitlines()]
    queries = [e for e in events if e.get('event') == 'odoo.query']
    assert queries and queries[0]['model'] == 'ir.model'
    assert ['name', 'ilike', '费用'] in json.loads(queries[0]['domain'])
    assert any(e.get('event') == 'odoo.read' and e.get('result_count') == 1 for e in events)
    assert 'dy.expense.policy' not in json.dumps(events)


def test_planning_rejects_malformed_module_candidates(tmp_path, client):
    _, error = server.execute_tool('dy_odoo_design_solution', dict(trace_id='bad-plan',
        requirements={'modules_needed': 'not-a-list'}), tmp_path)
    assert error and error['code'] == -32602


def test_health_configuration_failure_is_safe_and_specific(tmp_path, client, monkeypatch):
    from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError
    monkeypatch.setattr(runtime, 'odoo_client', lambda root: (_ for _ in ()).throw(OdooConfigurationError('private-password')))
    value = call(tmp_path, 'dy_odoo_diagnostics_health_check')
    assert value['overall'] == 'unhealthy'
    assert 'private-password' not in json.dumps(value)


def test_import_execution_reuses_configuration_safety_and_locks_batch(tmp_path, client, monkeypatch):
    from test_configuration_write import Client as ConfigurationClient
    from dy_superpowers_mcp import configuration, odoo_imports
    fake = ConfigurationClient()
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    # 仅替换网络边界；实际逐条预检、写入、回读与日志保持运行。
    monkeypatch.setattr(configuration, 'OdooReadClient', lambda settings: fake)
    args = dict(model='dy.fin.account.analytic.plan', company_id=2, environment='demo',
                idempotency_key='import-batch', csv_data='name,code,company_id\n方案,B,2\n')
    preview = call(tmp_path, 'dy_odoo_import_preview', **args)
    execute_args = dict(args, confirmed=True, confirmation_digest=preview['confirmation_digest'])
    result = call(tmp_path, 'dy_odoo_import_execute', **execute_args)
    assert result['results'][0]['data_readback_status'] == 'passed'
    assert len([c for c in fake.calls if c[1] == 'create']) == 1
    replay = call(tmp_path, 'dy_odoo_import_execute', **execute_args)
    assert replay['status'] == 'already_attempted'
    changed = dict(args, csv_data='name,code,company_id\n另一方案,C,2\n')
    preview = call(tmp_path, 'dy_odoo_import_preview', **changed)
    _, error = server.execute_tool('dy_odoo_import_execute', dict(trace_id='changed-import', **changed,
        confirmed=True, confirmation_digest=preview['confirmation_digest']), tmp_path)
    assert error and error['code'] == -32602
    assert len([c for c in fake.calls if c[1] == 'create']) == 1


def test_import_dry_run_never_executes(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    args = dict(model='dy.fin.account.analytic.plan', company_id=2, environment='demo',
                idempotency_key='dry-import', csv_data='name,code,company_id\n方案,B,2\n')
    preview = call(tmp_path, 'dy_odoo_import_preview', **args)
    monkeypatch.setattr(runtime, 'odoo_client', lambda root: pytest.fail('dry_run不连接'))
    result = call(tmp_path, 'dy_odoo_import_execute', **args, confirmed=True, dry_run=True,
                  confirmation_digest=preview['confirmation_digest'])
    assert result['status'] == 'confirmation_required'


def test_module_confirmation_cannot_cross_servers_with_same_database(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_install', **args)
    monkeypatch.setenv('APP_URL', 'https://other.example.test')
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='cross-server', **dict(args,
        dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in client.calls)


@pytest.mark.parametrize('operation', ['uninstall'])
def test_module_existing_lifecycle_readback(tmp_path, client, monkeypatch, operation):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    client.state = 'installed'
    args = lifecycle()
    name = 'dy_odoo_module_' + operation
    preview = call(tmp_path, name, **args)
    result = call(tmp_path, name, **dict(args, dry_run=False, confirmed=True,
                                      confirmation_digest=preview['confirmation_digest']))
    assert result['execution_status'] == 'succeeded' and result['data_readback_status'] == 'passed'
    assert client.state == ('uninstalled' if operation == 'uninstall' else 'installed')


def test_module_uninstall_rejects_unconfirmed_installed_dependents(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    client.state = 'installed'
    original = client.search_read

    def read(model, domain, fields, limit, offset=0, **kwargs):
        if model == 'ir.module.module.dependency' and ['name', 'in', ['dy_expense']] in domain:
            return [{'id': 90, 'name': 'dy_expense', 'module_id': [91, 'downstream_addon']}]
        return original(model, domain, fields, limit, offset, **kwargs)

    monkeypatch.setattr(client, 'search_read', read)
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_uninstall', **args)
    _, error = server.execute_tool('dy_odoo_module_uninstall', dict(trace_id='uninstall-dependent', **dict(
        args, dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])), tmp_path)
    assert error and error['code'] == -32602
    assert '反向依赖' in error['message']
    assert not any(c[1].startswith('button_immediate_') for c in client.calls)
    assert not list((tmp_path / '.dy_superpowers/configuration').glob('*.json'))


@pytest.mark.parametrize('enabled', [False, True])
def test_plan_validation_only_accepts_currently_callable_tools(tmp_path, client, monkeypatch, enabled):
    if enabled:
        monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    step = {'tool': 'dy_odoo_module_install', 'params': dict(module_names=['dy_expense'], environment='demo',
            idempotency_key='plan-step', backup_reference='已核实的备份编号')}
    result = call(tmp_path, 'dy_odoo_validate_plan', plan={'phases': [{'phase': 1, 'steps': [step]}]})
    assert result['valid'] is enabled
    assert ('计划包含未接入或未启用的工具' in result['errors']) is not enabled
    assert not client.calls


@pytest.mark.parametrize('tool', [[], {}, None, 1, True])
def test_plan_validation_reports_invalid_tool_types(tmp_path, client, tool):
    result = call(tmp_path, 'dy_odoo_validate_plan', plan={
        'phases': [{'phase': 1, 'steps': [{'tool': tool}]}]})
    assert result['valid'] is False
    assert result['errors']
    assert result['execution_authorized'] is False
    assert not client.calls


def test_module_remote_timeout_is_not_replayed(tmp_path, client, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    original = client._execute_kw
    def execute(model, method, args, kwargs):
        if method.startswith('button_immediate_'):
            client.calls.append((model, method, args, kwargs))
            raise OdooRPCError('private-password')
        return original(model, method, args, kwargs)
    monkeypatch.setattr(client, '_execute_kw', execute)
    args = lifecycle()
    preview = call(tmp_path, 'dy_odoo_module_install', **args)
    args.update(dry_run=False, confirmed=True, confirmation_digest=preview['confirmation_digest'])
    result = call(tmp_path, 'dy_odoo_module_install', **args)
    assert result['execution_status'] == 'unknown'
    assert 'private-password' not in json.dumps(result)
    result = call(tmp_path, 'dy_odoo_module_install', **args)
    assert result['status'] == 'already_attempted'
    assert len([c for c in client.calls if c[1].startswith('button_immediate_')]) == 1


def test_online_discovery_rejects_false_environment_label(tmp_path, client):
    _, error = server.execute_tool('dy_odoo_model_list', dict(trace_id='bad-label',
        trace_context={'environment': 'other'}), tmp_path)
    assert error and error['code'] == -32602
    assert not client.calls


@pytest.mark.parametrize('operation', ['install', 'uninstall'])
def test_module_pending_global_changes_block_mutation(tmp_path, client, monkeypatch, operation):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    client.state = 'uninstalled' if operation == 'install' else 'installed'
    original = client.search_read
    def read(model, domain, fields, limit, offset=0, **kwargs):
        if ['state', 'in', ['to install', 'to upgrade', 'to remove']] in domain:
            return [{'id': 77, 'name': 'other_module', 'state': 'to remove'}]
        return original(model, domain, fields, limit, offset, **kwargs)
    monkeypatch.setattr(client, 'search_read', read)
    args = lifecycle()
    name = 'dy_odoo_module_' + operation
    preview = call(tmp_path, name, **args)
    _, error = server.execute_tool(name, dict(trace_id='pending', **dict(args, dry_run=False,
        confirmed=True, confirmation_digest=preview['confirmation_digest'])), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in client.calls)


def test_import_partial_preflight_failure_preserves_written_results(tmp_path, client, monkeypatch):
    from test_configuration_write import Client as ConfigurationClient
    from dy_superpowers_mcp import configuration
    fake = ConfigurationClient()
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    monkeypatch.setattr(configuration, 'OdooReadClient', lambda settings: fake)
    original = fake._execute_kw
    def remote(model, method, args, kwargs):
        if method == 'search_read' and model == 'dy.fin.account.analytic.plan' and kwargs.get('fields') == ['id']:
            if ['code', '=', 'C'] in args[0]:
                return [{'id': 88}]
        return original(model, method, args, kwargs)
    monkeypatch.setattr(fake, '_execute_kw', remote)
    args = dict(model='dy.fin.account.analytic.plan', company_id=2, environment='demo',
        idempotency_key='partial-batch', csv_data='name,code,company_id\n方案,B,2\n另一方案,C,2\n')
    preview = call(tmp_path, 'dy_odoo_import_preview', **args)
    result = call(tmp_path, 'dy_odoo_import_execute', **args, confirmed=True,
                  confirmation_digest=preview['confirmation_digest'])
    assert result['results'][0]['data_readback_status'] == 'passed'
    assert result['stopped_at_row'] == 2 and result['completed_rows'] == 1
    assert result['results'][1]['execution_status'] == 'not_attempted'
    assert len([c for c in fake.calls if c[1] == 'create']) == 1
