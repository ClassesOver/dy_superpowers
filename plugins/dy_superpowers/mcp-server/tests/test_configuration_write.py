"""配置写入安全边界：所有远端行为使用确定性的离线账套替身。"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp.configuration import confirmation_digest, write_configuration
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, OdooRPCError, OdooSettings


class Client:
    uid = 7

    def __init__(self):
        self.settings = OdooSettings('https://odoo.example.test', 'demo', 'operator', 'not-logged')
        self.rows = {8: {'id': 8, 'name': '原方案', 'code': 'A', 'company_id': [2, '医院'], 'write_date': '2026-10-30 01:00:00'}}
        self.calls = []
        self.mismatch = False
        self.fail = False
        self.company = 2
        self.allowed = [2]
        self.denied = False
        self.readonly = False
        self.version = [12, 0, 0, 'final', 0]
        self.module_version = '12.0.0.1'
        self.bad_id = False
        self.remote_result = None
        self.related_company = [2]
        self.read_fail_after_write = False
        self.existing = False

    def _rpc(self, service, method, args):
        assert (service, method, args) == ('common', 'version', [])
        return {'server_version_info': self.version}

    def close(self):
        pass

    def _execute_kw(self, model, method, args, kwargs):
        self.calls.append((model, method, copy.deepcopy(args), copy.deepcopy(kwargs)))
        assert not set(kwargs.get('context', {})) - {'force_company', 'active_test', '__last_update'}
        if method == 'check_access_rights':
            return not self.denied
        if method == 'fields_get':
            metadata = {'name': {'type': 'char', 'required': True, 'readonly': self.readonly},
                    'code': {'type': 'char', 'required': True, 'readonly': False},
                    'company_id': {'type': 'many2one', 'relation': 'res.company', 'required': True, 'readonly': False},
                    'write_date': {'type': 'datetime', 'readonly': True}}
            if model == 'dy.expense.document.type':
                metadata.update(company_ids={'type': 'many2many', 'relation': 'res.company'},
                                exp_req_doc_type_id={'type': 'many2one', 'relation': 'dy.expense.document.type', 'readonly': False})
            return metadata
        if method in {'search_read', 'read'}:
            if model == 'res.users':
                return [{'id': 7, 'company_id': [self.company, '医院'], 'company_ids': self.allowed}]
            if model == 'res.company':
                return [{'id': 2}]
            if model == 'ir.module.module':
                return [{'id': 44, 'name': args[0][0][2], 'state': 'installed', 'latest_version': self.module_version}]
            if model == 'dy.expense.document.type' and kwargs['fields'] == ['company_ids']:
                return [{'id': 4, 'company_ids': self.related_company}]
            if self.existing and kwargs['fields'] == ['id']:
                return [{'id': 88}]
            if self.read_fail_after_write and any(call[1] in {'create', 'write'} for call in self.calls):
                raise TimeoutError('回读超时')
            ids = args[0] if method == 'read' else next((term[2] for term in args[0] if term[:2] == ['id', 'in']), None)
            rows = [self.rows[i] for i in ids or [] if i in self.rows]
            if self.bad_id and rows:
                return [{**rows[0], 'id': 999}]
            return copy.deepcopy(rows)
        if method in {'create', 'write'}:
            if self.remote_result is not None:
                return self.remote_result
            if self.fail:
                raise TimeoutError('包含密码的远端异常不得泄漏')
            rid, values = (19, args[0]) if method == 'create' else (args[0][0], args[1])
            self.rows.setdefault(rid, {'id': rid, 'write_date': '2026-10-30 01:00:00'}).update(values)
            if self.mismatch:
                self.rows[rid]['name'] = '服务端改变了值'
            return rid if method == 'create' else True
        raise AssertionError((model, method))


@pytest.fixture
def payload(monkeypatch):
    monkeypatch.setenv('DY_ODOO_CONFIG_WRITE_ENABLED', '1')
    monkeypatch.setenv('APP_URL', 'https://odoo.example.test')
    return {'environment': {'url': 'https://odoo.example.test', 'database': 'demo'},
            'model': 'dy.fin.account.analytic.plan', 'company_id': 2,
            'values': {'name': '新方案', 'code': 'B', 'company_id': 2},
            'idempotency_key': 'batch-item-1', 'confirmed': True}


def run(tmp_path, payload, client, operation='create'):
    payload['confirmation_digest'] = confirmation_digest(operation, payload)
    return write_configuration(tmp_path, operation, payload, client_factory=lambda: client)

def mutations(client):
    return [call for call in client.calls if call[1] in {'create', 'write'}]


def test_configuration_company_check_uses_exact_read(tmp_path, payload):
    client = Client()
    execute = client._execute_kw

    def hrp_execute(model, method, args, kwargs):
        if model == 'res.company':
            if method == 'search_read':
                return []  # HRP 的搜索过滤不代表记录不存在。
            assert method == 'read' and args == [[2]]
            assert kwargs['context'] == {'force_company': 2}
            return [{'id': 2}]
        return execute(model, method, args, kwargs)

    client._execute_kw = hrp_execute
    assert run(tmp_path, payload, client)['data_readback_status'] == 'passed'


def test_create_returns_real_id_and_readback_without_persisting_values(tmp_path, payload):
    client = Client()
    result = run(tmp_path, payload, client)
    assert result['record_id'] == 19
    assert result['execution_status'] == 'succeeded'
    assert result['data_readback_status'] == 'passed'
    assert result['browser_acceptance_status'] == 'not_checked'
    assert result['accepted'] is False
    assert result['expected_values'] == result['actual_values']
    saved = ''.join(p.read_text() for p in (tmp_path / '.dy_superpowers/configuration').glob('*.json'))
    assert '新方案' not in saved and 'not-logged' not in saved
    assert len(mutations(client)) == 1


@pytest.mark.parametrize('change', [
    {'confirmed': False}, {'model': 'account.move'}, {'values': {'sudo': True}},
    {'values': {'name': [[0, 0, {'name': '非法'}]]}}, {'company_id': True},
    {'environment': {'url': 'https://other.example.test', 'database': 'demo'}},
    {'context': {'sudo': True}}, {'values': {'name': '新方案', 'code': 'B', 'company_id': 3}},
])
def test_invalid_or_unconfirmed_never_writes(tmp_path, payload, change):
    client = Client()
    payload.update(change)
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert not mutations(client)


def test_disabled_does_not_open_connection(tmp_path, payload, monkeypatch):
    monkeypatch.delenv('DY_ODOO_CONFIG_WRITE_ENABLED')
    with pytest.raises(OdooConfigurationError):
        write_configuration(tmp_path, 'create', payload, client_factory=lambda: pytest.fail('默认不能连接'))


def test_digest_binds_exact_change(tmp_path, payload):
    payload['confirmation_digest'] = confirmation_digest('create', payload)
    payload['values']['name'] = '未确认的新值'
    with pytest.raises(OdooConfigurationError):
        write_configuration(tmp_path, 'create', payload, client_factory=Client)


@pytest.mark.parametrize('attribute,value', [('company', 3), ('allowed', [3]), ('denied', True), ('readonly', True)])
def test_live_authorization_or_metadata_blocks_write(tmp_path, payload, attribute, value):
    client = Client()
    setattr(client, attribute, value)
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert not mutations(client)


def test_update_requires_expected_snapshot_and_checks_company(tmp_path, payload):
    client = Client()
    payload.update(record_id=8, values={'name': '新方案'}, expected_values={'name': '原方案'},
                   expected_write_date='2026-10-30 01:00:00')
    assert run(tmp_path, payload, client, 'update')['data_readback_status'] == 'passed'
    assert mutations(client)[0][1:3] == ('write', [[8], {'name': '新方案'}])


@pytest.mark.parametrize('change', [{'expected_values': {'name': '已过期'}},
    {'expected_write_date': '旧版本'}, {'expected_values': {}}, {'record_id': 99}])
def test_stale_or_missing_update_never_writes(tmp_path, payload, change):
    client = Client()
    payload.update(record_id=8, values={'name': '新方案'}, expected_values={'name': '原方案'},
                   expected_write_date='2026-10-30 01:00:00')
    payload.update(change)
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client, 'update')
    assert not mutations(client)


def test_repeat_does_not_write_or_claim_stale_readback(tmp_path, payload):
    client = Client()
    run(tmp_path, payload, client)
    client.rows[19]['name'] = '后来改了'
    replay = run(tmp_path, payload, client)
    assert replay['status'] == 'already_attempted'
    assert replay['data_readback_status'] == 'not_rechecked'
    assert replay['accepted'] is False
    assert len(mutations(client)) == 1


def test_token_cannot_be_reused_for_different_payload(tmp_path, payload):
    client = Client()
    run(tmp_path, payload, client)
    payload['values']['name'] = '另一项'
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert len(mutations(client)) == 1


def test_rpc_write_failure_returns_safe_diagnostic_without_retry(tmp_path, payload):
    client = Client()
    execute = client._execute_kw

    def fail_write(model, method, args, kwargs):
        if method == 'create':
            raise OdooRPCError('private-password-and-traceback', category='业务校验失败', request_id=17)
        return execute(model, method, args, kwargs)

    client._execute_kw = fail_write
    result = run(tmp_path, payload, client)
    assert result['execution_status'] == 'unknown'
    assert result['rpc_diagnostic'] == {'category': '业务校验失败', 'request_id': 17}
    assert 'private' not in json.dumps(result)
    assert run(tmp_path, payload, client)['status'] == 'already_attempted'


@pytest.mark.parametrize('category,request_id', [('private-token', 1), ('权限拒绝', 'private-token'), ('权限拒绝', True)])
def test_rpc_diagnostic_does_not_accept_arbitrary_metadata(category, request_id):
    error = OdooRPCError('private', category=category, request_id=request_id)
    assert error.diagnostic is None


def test_timeout_is_unknown_and_never_automatically_retried(tmp_path, payload):
    client = Client()
    client.fail = True
    result = run(tmp_path, payload, client)
    assert result['execution_status'] == 'unknown'
    assert '密码' not in json.dumps(result, ensure_ascii=False)
    assert run(tmp_path, payload, client)['execution_status'] == 'unknown'
    assert len(mutations(client)) == 1


def test_readback_mismatch_does_not_pass(tmp_path, payload):
    client = Client()
    client.mismatch = True
    result = run(tmp_path, payload, client)
    assert result['execution_status'] == 'succeeded'
    assert result['data_readback_status'] == 'mismatch'
    assert result['accepted'] is False


def test_symlinked_journal_is_rejected(tmp_path, payload):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / '.dy_superpowers').symlink_to(elsewhere, target_is_directory=True)
    client = Client()
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert not mutations(client)


@pytest.mark.parametrize('attribute,value', [('version', [17, 0]), ('module_version', '12.0.99.0'), ('bad_id', True)])
def test_unproven_version_or_wrong_record_blocks_write(tmp_path, payload, attribute, value):
    client = Client()
    setattr(client, attribute, value)
    payload.update(record_id=8, values={'name': '新方案'}, expected_values={'name': '原方案'},
                   expected_write_date='2026-10-30 01:00:00')
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client, 'update')
    assert not mutations(client)


@pytest.mark.parametrize('remote', [False, True, [], {'id': 9}])
def test_non_id_create_response_is_unknown(tmp_path, payload, remote):
    client = Client()
    client.remote_result = remote
    assert run(tmp_path, payload, client)['execution_status'] == 'unknown'
    assert len(mutations(client)) == 1


def test_dry_run_provides_complete_confirmation_without_connection(tmp_path, payload):
    payload.update(dry_run=True, confirmed=False, record_id=8, values={'name': '新方案'},
                   expected_values={'name': '原方案'}, expected_write_date='2026-10-30 01:00:00')
    result = write_configuration(tmp_path, 'update', payload, client_factory=lambda: pytest.fail('本地预览不连接'))
    assert result['confirmation']['expected_values'] == {'name': '原方案'}
    assert result['confirmation']['expected_write_date'] == '2026-10-30 01:00:00'
    assert result['preflight_status'] == 'not_checked'
    assert result['confirmation_digest'] == confirmation_digest('update', payload)


def test_document_relation_cannot_cross_company(tmp_path, payload):
    client = Client()
    client.module_version = '12.0.1.0'
    client.rows[8].update(company_ids=[2], exp_req_doc_type_id=[4, '申请'])
    client.related_company = [3]
    payload.update(model='dy.expense.document.type', record_id=8, values={'exp_req_doc_type_id': 4},
                   expected_values={'exp_req_doc_type_id': 4}, expected_write_date='2026-10-30 01:00:00')
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client, 'update')
    assert not mutations(client)


def test_update_passes_odoo12_concurrency_guard(tmp_path, payload):
    client = Client()
    payload.update(record_id=8, values={'name': '新方案'}, expected_values={'name': '原方案'},
                   expected_write_date='2026-10-30 01:00:00')
    run(tmp_path, payload, client, 'update')
    assert mutations(client)[0][3]['context']['__last_update'] == {
        'dy.fin.account.analytic.plan,8': '2026-10-30 01:00:00'}


def test_existing_config_requires_explicit_reuse_not_create(tmp_path, payload):
    client = Client()
    client.existing = True
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert not mutations(client)


def test_readback_timeout_preserves_success_but_not_acceptance(tmp_path, payload):
    client = Client()
    client.read_fail_after_write = True
    result = run(tmp_path, payload, client)
    assert result['execution_status'] == 'succeeded'
    assert result['record_id'] == 19
    assert result['data_readback_status'] == 'not_checked'
    assert result['accepted'] is False


@pytest.mark.parametrize('companies', [[2, 3], [], [3]])
def test_document_type_shared_scope_is_not_mutable(tmp_path, payload, companies):
    client = Client()
    client.module_version = '12.0.1.0'
    client.rows[8]['company_ids'] = companies
    payload.update(model='dy.expense.document.type', record_id=8, values={'name': '新名称'},
                   expected_values={'name': '原方案'}, expected_write_date='2026-10-30 01:00:00')
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client, 'update')
    assert not mutations(client)


def test_superuser_cannot_bypass_record_rules(tmp_path, payload):
    client = Client()
    client.uid = 1
    with pytest.raises(OdooConfigurationError):
        run(tmp_path, payload, client)
    assert not mutations(client)
