"""答卷驱动配置的版本、附件与实时回读门禁。"""
import copy
import multiprocessing

import pytest

from dy_superpowers_mcp.configuration import confirmation_digest, write_configuration
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError
from dy_superpowers_mcp.qa_store import commit_qa, read_qa
from test_configuration_write import Client, mutations, payload
from test_template_inspection import saved


@pytest.fixture
def answer_payload(tmp_path, monkeypatch, payload):
    for key, value in dict(DB_NAME='demo', APP_USER='operator', APP_PASSWORD='not-logged').items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('DY_ODOO_ENVIRONMENT', raising=False)
    payload['trace_context'] = answer_context(saved(tmp_path, module='general-ledger'))
    return payload


def answer_context(state):
    """只用答卷自身身份驱动配置，不经过巡检准备。"""
    answer = state['records'][0]
    return dict(module=state['module'], scope_id=state['scope_id'], template_revision=state['template_revision'],
        question_id=answer['question_id'], answer_revision=answer['revision'], environment='demo')


def revised(root, context, *, complete=True):
    state = read_qa(root, context['module'], context['scope_id'])
    answer = next(a for a in state['records'] if a['question_id'] == context['question_id'])
    commit_qa(root, context['module'], scope=state['scope'], scope_id=state['scope_id'],
        template_revision=state['template_revision'], batch_id='revised', confirmed=True,
        answers=[dict(question_id=answer['question_id'], answer='业务重新确认后的配置要求',
            expected_revision=answer['revision'], research_complete=complete, attachments=answer['attachments'])])


def execute(root, args, client=None):
    return write_configuration(root, 'create', args, client_factory=lambda: client or Client())


def test_answer_bound_preview_and_write_return_same_source(tmp_path, answer_payload):
    preview = execute(tmp_path, {**answer_payload, 'dry_run': True})
    assert preview['confirmation']['trace_context'] == answer_payload['trace_context']
    result = execute(tmp_path, {**answer_payload, 'confirmation_digest': preview['confirmation_digest']})
    assert result['answer_context'] == answer_payload['trace_context']
    assert result['data_readback_status'] == 'passed' and not result['accepted']


@pytest.mark.parametrize('field', ['module', 'scope_id', 'template_revision',
    'question_id', 'answer_revision', 'environment'])
def test_research_identity_is_bound_into_confirmation(answer_payload, field):
    original = confirmation_digest('create', answer_payload)
    changed = copy.deepcopy(answer_payload)
    changed['trace_context'][field] += '-changed'
    assert confirmation_digest('create', changed) != original


def test_connection_identity_is_bound_by_server(answer_payload, monkeypatch):
    original = confirmation_digest('create', answer_payload)
    monkeypatch.setenv('APP_USER', 'different-operator')
    assert confirmation_digest('create', answer_payload) != original


def test_inspection_only_identifiers_are_not_answer_binding(tmp_path, answer_payload):
    preview = execute(tmp_path, {**answer_payload, 'dry_run': True})
    assert set(preview['confirmation']['trace_context']) == set(answer_payload['trace_context'])
    tagged = copy.deepcopy(answer_payload)
    tagged['trace_context'].update(requirement_id='inspection-only', answer_version='inspection-only')
    assert confirmation_digest('create', tagged) == confirmation_digest('create', answer_payload)


@pytest.mark.parametrize('field', ['module', 'scope_id', 'template_revision',
    'question_id', 'answer_revision', 'environment'])
def test_incomplete_source_never_opens_connection(tmp_path, answer_payload, field):
    del answer_payload['trace_context'][field]
    answer_payload['confirmation_digest'] = confirmation_digest('create', answer_payload)
    with pytest.raises(OdooConfigurationError):
        write_configuration(tmp_path, 'create', answer_payload,
            client_factory=lambda: pytest.fail('无效答卷依据不得连接账套'))


def test_removing_answer_binding_cannot_reuse_confirmation(tmp_path, answer_payload):
    preview = execute(tmp_path, {**answer_payload, 'dry_run': True})
    del answer_payload['trace_context']
    with pytest.raises(OdooConfigurationError, match='精确变更摘要'):
        execute(tmp_path, {**answer_payload, 'confirmation_digest': preview['confirmation_digest']})


@pytest.mark.parametrize('change', ['answer', 'attachment', 'identity', 'other-workspace'])
def test_stale_source_after_preview_never_opens_connection(tmp_path, answer_payload, monkeypatch, change):
    preview = execute(tmp_path, {**answer_payload, 'dry_run': True})
    if change == 'answer':
        revised(tmp_path, answer_payload['trace_context'])
    elif change == 'attachment':
        (tmp_path / 'evidence.txt').write_text('附件在确认后发生变化')
    elif change == 'identity':
        monkeypatch.setenv('APP_USER', 'different-operator')
    else:
        tmp_path = tmp_path / 'other-workspace'
        tmp_path.mkdir()
    with pytest.raises(OdooConfigurationError):
        write_configuration(tmp_path, 'create', {**answer_payload, 'confirmation_digest': preview['confirmation_digest']},
            client_factory=lambda: pytest.fail('过期答卷依据不得连接账套'))


def test_answer_changed_during_preflight_prevents_remote_mutation(tmp_path, answer_payload):
    preview = execute(tmp_path, {**answer_payload, 'dry_run': True})
    client = Client()
    original = client._execute_kw

    def changed(model, method, args, kwargs):
        result = original(model, method, args, kwargs)
        if model == 'dy.fin.account.analytic.plan' and method == 'fields_get':
            revised(tmp_path, answer_payload['trace_context'])
        return result

    client._execute_kw = changed
    with pytest.raises(OdooConfigurationError):
        execute(tmp_path, {**answer_payload, 'confirmation_digest': preview['confirmation_digest']}, client)
    assert not mutations(client)


def test_write_readback_does_not_confuse_false_with_zero(tmp_path, payload):
    payload['values']['name'] = False
    client = Client()
    original = client._execute_kw

    def wrong_type(model, method, args, kwargs):
        result = original(model, method, args, kwargs)
        if model == payload['model'] and method == 'read' and result:
            result[0]['name'] = 0
        return result

    client._execute_kw = wrong_type
    payload['confirmation_digest'] = confirmation_digest('create', payload)
    assert execute(tmp_path, payload, client)['data_readback_status'] == 'mismatch'


def test_incomplete_confirmed_answer_cannot_drive_configuration(tmp_path, answer_payload):
    revised(tmp_path, answer_payload['trace_context'], complete=False)
    state = read_qa(tmp_path, 'general-ledger')
    answer_payload['trace_context'].update(answer_revision=state['records'][0]['revision'])
    with pytest.raises(OdooConfigurationError, match='未就绪'):
        execute(tmp_path, {**answer_payload, 'dry_run': True})


def test_invalid_update_snapshot_does_not_confuse_false_with_zero(tmp_path, payload):
    client = Client()
    client.rows[8]['name'] = 0
    payload.update(record_id=8, values={'name': '已确认名称'}, expected_values={'name': False},
        expected_write_date=client.rows[8]['write_date'])
    payload['confirmation_digest'] = confirmation_digest('update', payload)
    with pytest.raises(OdooConfigurationError, match='原值已变化'):
        write_configuration(tmp_path, 'update', payload, client_factory=lambda: client)
    assert not mutations(client)


@pytest.mark.parametrize('value', [{'nested': 'value'}, [['write', 3]], ['x'] * 9000])
def test_malformed_snapshot_is_rejected_before_connecting(tmp_path, payload, value):
    payload.update(record_id=8, values={'name': '已确认名称'}, expected_values={'name': value},
        expected_write_date='2026-10-30 01:00:00')
    payload['confirmation_digest'] = confirmation_digest('update', payload)
    with pytest.raises(OdooConfigurationError):
        write_configuration(tmp_path, 'update', payload,
            client_factory=lambda: pytest.fail('无效旧值不得连接账套'))


def _concurrent_write(root, arguments, mutation_log, gate, queue):
    """真实进程锁配合离线账套替身，只统计远端变更调用。"""
    client = Client()
    original = client._execute_kw

    def counted(model, method, args, kwargs):
        if method in {'create', 'write'}:
            with mutation_log.open('a') as output:
                output.write('mutation\n')
        return original(model, method, args, kwargs)

    client._execute_kw = counted
    gate.wait(timeout=10)
    try:
        queue.put(execute(root, arguments, client)['status'])
    except Exception as exc:
        queue.put(type(exc).__name__)


@pytest.mark.parametrize('separate_workspaces', [False, True])
def test_multiple_processes_keep_idempotency_in_workspace(tmp_path, answer_payload, separate_workspaces):
    context = multiprocessing.get_context('fork')
    gate, queue = context.Event(), context.Queue()
    roots = [tmp_path] * 4
    payloads = [copy.deepcopy(answer_payload) for _ in roots]
    if separate_workspaces:
        for index in range(1, len(roots)):
            roots[index] = tmp_path / ('workspace-' + str(index))
            roots[index].mkdir()
            payloads[index]['trace_context'] = answer_context(saved(roots[index], module='general-ledger'))
    for args in payloads:
        args['confirmation_digest'] = confirmation_digest('create', args)
    log = tmp_path / 'mutations.txt'
    processes = [context.Process(target=_concurrent_write, args=(root, args, log, gate, queue))
        for root, args in zip(roots, payloads)]
    try:
        for process in processes:
            process.start()
        gate.set()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        expected = 4 if separate_workspaces else 1
        assert results.count('attempted') == expected
        assert results.count('already_attempted') == 4 - expected
        assert log.read_text().splitlines() == ['mutation'] * expected
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()
