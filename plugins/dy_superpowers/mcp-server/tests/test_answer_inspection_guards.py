"""答案驱动巡检：实际漏洞的离线回归，不连接业务系统。"""
import copy
import hashlib
from datetime import datetime, timezone

import pytest

import server
from test_template_inspection import report_for, validate
from dy_superpowers_mcp.qa_store import commit_qa
from dy_superpowers_mcp.qa_basis import configured_environment_info
from dy_superpowers_mcp.trace import TraceStore


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    for key in ('APP_URL', 'APP_USER', 'APP_PASSWORD', 'DB_NAME', 'DY_ODOO_ENVIRONMENT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)


def independent_report(root):
    report = report_for(root)
    file = root / 'verification.txt'
    file.write_text('当前系统独立核验材料', encoding='utf-8')
    evidence = dict(kind='document', reference=file.name,
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                    observed_at=datetime.now(timezone.utc).isoformat())
    record = report['records'][0]
    record['evidence_refs'] = [evidence]
    for req in record['requirements']:
        req.update(verification_target='document', evidence_refs=[evidence])
    record['verified_at'] = datetime.now(timezone.utc).isoformat()
    return report


def test_connection_identity_changes_with_host_but_not_password(monkeypatch):
    monkeypatch.setenv('APP_URL', 'https://one.example.test')
    monkeypatch.setenv('APP_USER', 'operator')
    monkeypatch.setenv('APP_PASSWORD', 'placeholder')
    monkeypatch.setenv('DB_NAME', 'UAT')
    first = configured_environment_info().get('connection_fingerprint')
    assert isinstance(first, str) and len(first) == 64
    monkeypatch.setenv('APP_PASSWORD', 'other-placeholder')
    assert configured_environment_info()['connection_fingerprint'] == first
    monkeypatch.setenv('APP_URL', 'https://two.example.test')
    assert configured_environment_info()['connection_fingerprint'] != first


def test_report_from_other_server_same_database_is_rejected(tmp_path, monkeypatch):
    for key, value in dict(APP_URL='https://one.example.test', DB_NAME='UAT',
                           APP_USER='operator', APP_PASSWORD='placeholder').items():
        monkeypatch.setenv(key, value)
    report = independent_report(tmp_path)
    report['basis']['connection_fingerprint'] = configured_environment_info().get('connection_fingerprint')
    assert validate(tmp_path, report)['valid']
    monkeypatch.setenv('APP_URL', 'https://two.example.test')
    assert not validate(tmp_path, report)['valid']


def test_research_attachment_is_not_independent_system_evidence(tmp_path):
    report = independent_report(tmp_path)
    rec = report['records'][0]
    source = rec['requirements'][0]['source_refs'][1]
    evidence = {k: source[k] for k in ('reference', 'sha256')}
    evidence.update(kind='document', observed_at=datetime.now(timezone.utc).isoformat())
    rec['evidence_refs'] = [evidence]
    for req in rec['requirements']:
        req['evidence_refs'] = [evidence]
    rec['verified_at'] = datetime.now(timezone.utc).isoformat()
    assert not validate(tmp_path, report)['valid']


def test_definitive_requirement_must_declare_verification_target(tmp_path):
    report = independent_report(tmp_path)
    del report['records'][0]['requirements'][0]['verification_target']
    assert not validate(tmp_path, report)['valid']


def test_configuration_target_requires_live_system_evidence(tmp_path):
    report = independent_report(tmp_path)
    report['records'][0]['requirements'][0]['verification_target'] = 'configuration'
    assert not validate(tmp_path, report)['valid']


def test_document_evidence_requires_observation_after_confirmation(tmp_path):
    report = independent_report(tmp_path)
    report['records'][0]['evidence_refs'][0]['observed_at'] = '2020-01-01T00:00:00+00:00'
    assert not validate(tmp_path, report)['valid']


def test_attachment_detail_cells_cannot_be_omitted(tmp_path):
    report = independent_report(tmp_path)
    state, error = server.execute_tool('dy_module_memory_read', dict(trace_id='read', module='expense'), tmp_path)
    assert error is None
    answer = state['records'][0]
    attachments = copy.deepcopy(answer['attachments'])
    attachments[0]['details'] = [dict(sheet='01_应用安装', rows=[{'应用名称（含英文编码）': 'missing_app', '是否需要安装': False}])]
    state = commit_qa(tmp_path, 'expense', scope=state['scope'], scope_id=state['scope_id'],
        template_revision=state['template_revision'], batch_id='details', confirmed=True,
        answers=[dict(question_id=answer['question_id'], answer=answer['answer'], research_complete=True,
                      expected_revision=answer['revision'], attachments=attachments)])
    report['basis']['answer_version'] = state['answer_version']
    rec = report['records'][0]
    rec['answer_revision'] = state['records'][0]['revision']
    for req in rec['requirements']:
        req['source_refs'][0]['revision'] = rec['answer_revision']
    rec['evidence_refs'][0]['observed_at'] = datetime.now(timezone.utc).isoformat()
    rec['verified_at'] = datetime.now(timezone.utc).isoformat()
    assert not validate(tmp_path, report)['valid']
    prep, error = server.execute_tool('dy_inspection_prepare', dict(trace_id='prepare', module='expense',
        scope_id=state['scope_id'], environment='UAT'), tmp_path)
    assert error is None
    checks = prep['items'][0]['detail_checks']
    assert len(checks) == 2 and any(c['expected_value'] is False for c in checks)
    for check in checks:
        rec['requirements'].append(dict(requirement_id=check['requirement_id'], text=check['text'],
            source_refs=[check['source_ref']], result='未检查', evidence_refs=[], verification_target='configuration'))
    rec['result'] = '待确认'
    assert validate(tmp_path, report)['valid']


def test_gap_candidates_consider_long_answers_and_attachment_details(tmp_path, monkeypatch):
    from dy_superpowers_mcp import odoo_discovery
    state = dict(scope_id='scope', answer_version='v1', attachment_integrity=True,
        records=[dict(question_id='q1', answer='本期需要支持员工报销与发票识别，具体安装需求参照已经确认的附件清单。',
            attachments=[dict(requirements=['按本期清单安装'], details=[dict(sheet='应用', rows=[{'应用': 'ocr_expense'}])])])])
    monkeypatch.setattr(odoo_discovery, 'read_qa', lambda *args: state)
    monkeypatch.setattr(odoo_discovery, 'installed_modules', lambda client: [])
    result = odoo_discovery.community_gaps(tmp_path, object(), 'expense', 'scope')
    assert any(row['module'] == 'ocr_expense' for row in result['candidates'])


def system_evidence(root, report):
    from dy_superpowers_mcp.odoo_comparison import compare_records
    comparison = {'expected_count': 1, 'expected_values': {'state': 'installed'}}
    record = report['records'][0]
    evidence = dict(kind='odoo', reference='odoo://modules', trace_id='system-proof', model='ir.module.module', comparison=comparison)
    context = {**report['basis'], 'question_id': record['question_id'], 'answer_revision': record['answer_revision']}
    store = TraceStore(root)
    store.append('system-proof', dict(event='odoo.read', status='ok', tool='dy_odoo_record_search',
        operation='search_read', model=evidence['model'], span_id='proof', trace_context=context,
        **compare_records(comparison, [{'state': 'installed'}])))
    store.append('system-proof', dict(event='tool.end', status='ok', tool='dy_odoo_record_search', span_id='proof'))
    record['evidence_refs'] = [evidence]
    for requirement in record['requirements']:
        requirement['evidence_refs'] = [evidence]
    record['verified_at'] = datetime.now(timezone.utc).isoformat()


def test_missing_connection_cannot_disable_odoo_fingerprint(tmp_path, monkeypatch):
    for key, value in dict(APP_URL='https://one.example.test', DB_NAME='UAT', APP_USER='operator', APP_PASSWORD='placeholder').items():
        monkeypatch.setenv(key, value)
    report = independent_report(tmp_path)
    report['basis']['connection_fingerprint'] = configured_environment_info()['connection_fingerprint']
    for requirement in report['records'][0]['requirements']:
        requirement['verification_target'] = 'configuration'
    system_evidence(tmp_path, report)
    assert validate(tmp_path, report)['valid']
    monkeypatch.delenv('APP_PASSWORD')
    del report['basis']['connection_fingerprint']
    assert not validate(tmp_path, report)['valid']


def test_renamed_research_attachment_is_not_independent_evidence(tmp_path):
    report = independent_report(tmp_path)
    record = report['records'][0]
    source = record['requirements'][0]['source_refs'][1]
    file = tmp_path / 'renamed.txt'
    file.write_bytes((tmp_path / source['reference']).read_bytes())
    record['evidence_refs'][0].update(reference=file.name, sha256=source['sha256'])
    assert not validate(tmp_path, report)['valid']


def test_document_target_requires_material_evidence(tmp_path):
    report = independent_report(tmp_path)
    system_evidence(tmp_path, report)
    assert not validate(tmp_path, report)['valid']


@pytest.mark.parametrize('target', [{}, [], 1])
def test_malformed_verification_target_returns_invalid(tmp_path, target):
    report = independent_report(tmp_path)
    report['records'][0]['requirements'][0]['verification_target'] = target
    assert not validate(tmp_path, report)['valid']


def test_duplicate_attachment_binding_is_rejected_before_save(tmp_path):
    report = independent_report(tmp_path)
    state, error = server.execute_tool('dy_module_memory_read', dict(trace_id='read', module='expense'), tmp_path)
    assert error is None
    answer = state['records'][0]
    duplicate = copy.deepcopy(answer['attachments'][0])
    duplicate['requirements'] = ['不能被后条覆盖的额外要求']
    with pytest.raises(ValueError, match='重复'):
        commit_qa(tmp_path, 'expense', scope=state['scope'], scope_id=state['scope_id'],
            template_revision=state['template_revision'], batch_id='duplicate', confirmed=True,
            answers=[dict(question_id=answer['question_id'], answer=answer['answer'], research_complete=True,
                expected_revision=answer['revision'], attachments=[duplicate, *answer['attachments']])])


def test_other_question_attachment_is_not_independent_evidence(tmp_path):
    from dy_superpowers_mcp.qa_store import read_qa
    report = independent_report(tmp_path)
    state = read_qa(tmp_path, 'expense')
    file = tmp_path / 'other-question.txt'
    file.write_text('另一题调研材料', encoding='utf-8')
    evidence = dict(kind='document', reference=file.name,
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                    observed_at=datetime.now(timezone.utc).isoformat())
    state = commit_qa(tmp_path, 'expense', scope=state['scope'], scope_id=state['scope_id'],
        template_revision=state['template_revision'], batch_id='other-question', confirmed=True,
        answers=[dict(question_id=state['questions'][1]['question_id'], answer='另一题调研材料',
            research_complete=False, expected_revision=None,
            attachments=[dict(reference=evidence['reference'], sha256=evidence['sha256'],
                              requirements=['另一题的要求'])])])
    report['basis']['answer_version'] = state['answer_version']
    other = next(a for a in state['records'] if a['question_id'] == state['questions'][1]['question_id'])
    report['records'][1].update(answer_revision=other['revision'], result='待确认', requirements=[dict(
        requirement_id='other', text='另一题的要求', result='未检查', evidence_refs=[], source_refs=[dict(
            kind='attachment', reference=file.name, sha256=evidence['sha256'])])])
    assert validate(tmp_path, report)['valid']
    report['records'][0]['evidence_refs'] = [evidence]
    for req in report['records'][0]['requirements']:
        req['evidence_refs'] = [evidence]
    report['records'][0]['verified_at'] = datetime.now(timezone.utc).isoformat()
    assert not validate(tmp_path, report)['valid']


@pytest.mark.parametrize('sha', [[], {}, None, 1])
def test_malformed_material_sha_returns_invalid_without_crash(tmp_path, sha):
    report = independent_report(tmp_path)
    report['records'][0]['evidence_refs'][0]['sha256'] = sha
    assert not validate(tmp_path, report)['valid']
