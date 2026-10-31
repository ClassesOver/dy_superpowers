"""准备仅提供当前 QA 与依赖缺口；历史结果不能代替本轮证据。"""
import json
import pytest
import server
from template_qa_helpers import save_answers


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'codex'))
    monkeypatch.delenv('DB_NAME',raising=False)
    monkeypatch.delenv('DY_ODOO_ENVIRONMENT',raising=False)


def prepare(root,state,**updates):
    args=dict(trace_id='prepare',module=state['module'],scope_id=state['scope_id'],environment='UAT')
    result,error=server.execute_tool('dy_inspection_prepare',{**args,**updates},root)
    assert error is None,error
    return result


@pytest.mark.parametrize('module',['expense','general-ledger'])
def test_prepare_separates_readiness_storage_and_actual_verification(tmp_path,module):
    state=save_answers(tmp_path,module)
    before={p:p.read_bytes() for p in (tmp_path/'.dy_superpowers').rglob('*') if p.is_file()}
    result=prepare(tmp_path,state)
    total=len(state['questions'])
    assert result['preparation']==dict(total=total,research_ready=1,blocked=total-1)
    assert result['items'][0]['answer']==state['records'][0]['answer']
    assert result['items'][1]['readiness']=='missing' and result['items'][1]['next_question']
    assert result['verification']['performed'] is False and result['execution_authorized'] is False
    assert all(p.read_bytes()==b for p,b in before.items())


def test_supplement_only_unblocks_its_question_and_never_reuses_historical_pass(tmp_path):
    first=save_answers(tmp_path,complete=False)
    initial=prepare(tmp_path,first)
    assert initial['items'][0]['readiness']=='incomplete'
    second=save_answers(tmp_path,count=2)
    result=prepare(tmp_path,second,previous_records=[dict(question_id=first['records'][0]['question_id'],result='通过')])
    assert result['preparation']['research_ready']==2
    assert result['items'][2]['readiness']=='missing'
    assert all(i['recheck_required'] and not i['evidence_reusable'] for i in result['items'])
    assert first['records'][0]['revision']!=second['records'][0]['revision']


def test_prepare_attachment_drift_blocks_only_its_dependencies(tmp_path):
    state=save_answers(tmp_path)
    (tmp_path/'evidence.txt').write_text('材料变化')
    result=prepare(tmp_path,state)
    assert result['items'][0]['readiness']=='attachment_changed'
    assert result['items'][1]['readiness']=='missing'


def test_prepare_trace_has_counts_environment_without_answer_body(tmp_path):
    state=save_answers(tmp_path)
    prepare(tmp_path,state)
    events=server.TraceStore(tmp_path).read('prepare')
    event=next(e for e in events if e['event']=='inspection.prepare')
    assert event['expected_count']==len(state['questions']) and event['count']==1
    assert event['trace_context']['environment']=='UAT'
    assert state['records'][0]['answer'] not in json.dumps(event,ensure_ascii=False)


@pytest.mark.parametrize('changes',[
    dict(environment='中文环境'),dict(environment='UAT east'),dict(environment='x'*129),
    dict(scope_id=''),dict(environment=''),dict(checks=[]),
    dict(checks=[dict(check_order='1',research_id='01')]),
    dict(checks=[dict(question_id='unknown')]),
])
def test_prepare_rejects_ambiguous_scope_environment_and_legacy_keys(tmp_path,changes):
    state=save_answers(tmp_path)
    args=dict(trace_id='invalid',module='expense',scope_id=state['scope_id'],environment='UAT')
    result,error=server.execute_tool('dy_inspection_prepare',{**args,**changes},tmp_path)
    assert result is None and error['code']==-32602


def test_checks_cannot_reduce_complete_report_denominator(tmp_path):
    state=save_answers(tmp_path)
    result=prepare(tmp_path,state,checks=[dict(question_id=state['questions'][0]['question_id'])])
    assert len(result['expected_checks'])==len(result['items'])==len(state['questions'])
    args=dict(trace_id='duplicate',module='expense',scope_id=state['scope_id'],environment='UAT',checks=[result['expected_checks'][0]]*2)
    assert server.execute_tool('dy_inspection_prepare',args,tmp_path)[1]['code']==-32602


@pytest.mark.parametrize('previous',[{},'invalid',7])
def test_previous_records_must_be_collection(tmp_path,previous):
    state=save_answers(tmp_path)
    args=dict(trace_id='invalid',module='expense',scope_id=state['scope_id'],environment='UAT',previous_records=previous)
    assert server.execute_tool('dy_inspection_prepare',args,tmp_path)[1]['code']==-32602
