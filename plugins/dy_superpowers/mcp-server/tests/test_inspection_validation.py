"""新版巡检逐题检验完整依据、当前版本、真实证据、完整覆盖和门禁。"""
import copy
from datetime import datetime,timezone,timedelta
import pytest
import server
from dy_superpowers_mcp.qa_templates import load_template
from template_qa_helpers import make_report,save_answers


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'codex'))
    monkeypatch.delenv('DB_NAME',raising=False)
    monkeypatch.delenv('DY_ODOO_ENVIRONMENT',raising=False)


@pytest.fixture
def report(tmp_path):
    return make_report(tmp_path)


def validate(root,report):
    result,error=server.execute_tool('dy_inspection_validate',dict(trace_id='inspection-validate',report=report),root)
    assert error is None,error
    return result


def test_current_report_validates_without_writing_report_or_memory(tmp_path,report):
    before={p:p.read_bytes() for p in (tmp_path/'.dy_superpowers').rglob('*') if p.is_file()}
    result=validate(tmp_path,report)
    assert result['valid'] and result['gate']=='待确认' and result['execution_authorized'] is False
    assert all(p.read_bytes()==data for p,data in before.items())
    assert not (tmp_path/'.dy_superpowers/reports').exists()
    assert any(e['event']=='inspection.validate' for e in server.TraceStore(tmp_path).read('inspection-validate'))


@pytest.mark.parametrize('field,value',[
    ('evidence_refs',[]),('answer_revision','old'),('environment','PROD'),
    ('verification_method',''),('verified_at','yesterday'),('result','bogus'),
    ('verified_at',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()),
])
def test_report_rejects_missing_mismatched_or_future_evidence_chain(tmp_path,report,field,value):
    report['records'][0][field]=value
    assert not validate(tmp_path,report)['valid']


def test_answer_change_invalidates_old_report_even_if_revision_string_is_updated(tmp_path,report):
    state=save_answers(tmp_path,answer='修订后的本期清单')
    assert not validate(tmp_path,report)['valid']
    report['basis']['answer_version']=state['answer_version']
    report['records'][0]['answer_revision']=state['records'][0]['revision']
    assert not validate(tmp_path,report)['valid']
    refreshed=make_report(tmp_path,state=state)
    assert validate(tmp_path,refreshed)['valid']


def test_partial_confirmed_answer_cannot_back_definitive_result(tmp_path):
    state=save_answers(tmp_path,complete=False)
    report=make_report(tmp_path,state=state)
    assert validate(tmp_path,report)['valid']
    report['records'][0]['result']='通过'
    result=validate(tmp_path,report)
    assert not result['valid'] and any('完整' in e['message'] for e in result['errors'])


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(records=[]),lambda r:r.update(expected_checks=[]),
    lambda r:r['records'].append(copy.deepcopy(r['records'][0])),
    lambda r:r['expected_checks'].pop(),lambda r:r['records'].pop(),
    lambda r:r['records'][0].update(question_id='GL-1-001'),
    lambda r:r['expected_checks'][0].update(scope_id='other'),
])
def test_every_current_template_question_is_required_once(tmp_path,report,mutation):
    mutation(report)
    assert not validate(tmp_path,report)['valid']


def test_narrow_scope_exclusion_cannot_remove_unchecked_questions(tmp_path,report):
    report['excluded_checks']=[dict(question_id=r['question_id'],reason='尚未验收') for r in report['records'][1:]]
    report['records']=report['records'][:1]
    report['expected_checks']=report['expected_checks'][:1]
    assert not validate(tmp_path,report)['valid']


@pytest.mark.parametrize('context_changes',[{},dict(scope_id='other'),dict(answer_version='old')])
def test_odoo_evidence_requires_complete_successful_current_context(tmp_path,report,context_changes,monkeypatch):
    from dy_superpowers_mcp.odoo_comparison import compare_records
    comparison={'expected_count':1,'expected_values':{'state':'installed'}}
    for key,value in dict(APP_URL='https://odoo.example.test',DB_NAME='UAT',APP_USER='operator',APP_PASSWORD='placeholder').items():
        monkeypatch.setenv(key,value)
    from dy_superpowers_mcp.qa_basis import configured_environment_info
    report['basis']['connection_fingerprint']=configured_environment_info()['connection_fingerprint']
    record=report['records'][0]
    evidence=dict(kind='odoo',reference='odoo://ir.module.module',trace_id='odoo-proof',model='ir.module.module',comparison=comparison)
    record['evidence_refs']=[evidence]
    for req in record['requirements']:
        req['evidence_refs']=[evidence]
        req['verification_target']='configuration'
    assert not validate(tmp_path,report)['valid']
    context={**report['basis'],'question_id':record['question_id'],'answer_revision':record['answer_revision'],**context_changes}
    store=server.TraceStore(tmp_path)
    store.append('odoo-proof',dict(event='odoo.read',status='ok',tool='dy_odoo_record_search',operation='search_read',model='ir.module.module',span_id='proof',trace_context=context,
        **compare_records(comparison,[{'state':'installed'}])))
    assert not validate(tmp_path,report)['valid']
    store.append('odoo-proof',dict(event='tool.end',status='ok',tool='dy_odoo_record_search',span_id='proof'))
    record['verified_at']=datetime.now(timezone.utc).isoformat()
    assert validate(tmp_path,report)['valid'] is (not context_changes)


@pytest.mark.parametrize('outcome,gate',[('不通过','不通过'),('不适用','通过'),('部分通过','待确认')])
def test_complete_scope_aggregates_explicit_outcomes(tmp_path,outcome,gate):
    total=len(load_template('expense')['questions'])
    state=save_answers(tmp_path,count=total)
    report=make_report(tmp_path,state=state)
    report['records'][0]['result']=outcome
    for req in report['records'][0]['requirements']:req['result']=outcome
    result=validate(tmp_path,report)
    assert result['valid'],result['errors']
    assert result['gate']==gate
    assert next(iter(result['coverage'].values()))['template']==total


@pytest.mark.parametrize('module',['expense','general-ledger'])
def test_each_module_retains_its_own_current_template_questions(tmp_path,module):
    report=make_report(tmp_path,module)
    result=validate(tmp_path,report)
    assert result['valid'] and len(report['records'])==len(load_template(module)['questions'])
    assert all(r['question_id'].startswith('DY-EXP-' if module=='expense' else 'GL-') for r in report['records'])
