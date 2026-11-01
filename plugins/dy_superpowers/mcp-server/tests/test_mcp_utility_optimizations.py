"""调研检索、实时配置比较与核对计划的效用回归。"""
import copy
from datetime import datetime, timezone

import pytest

import server
from dy_superpowers_mcp.tools import runtime
from dy_superpowers_mcp import knowledge, odoo_planning
from dy_superpowers_mcp.qa_basis import configured_environment_info
from test_template_inspection import report_for, validate


def test_natural_question_search_returns_terms_and_source_references():
    result = knowledge.search('各公司如何设置费用报销单据类型？', module='dy_expense', knowledge_type='source')
    assert result['count'] > 0
    assert result['search_strategy'] == 'keywords'
    assert result['search_terms'] and result['query'] == '各公司如何设置费用报销单据类型？'
    assert all(row['module'] == 'dy_expense' and row['reference'].startswith('zoekt:') for row in result['results'])


def test_technical_query_keeps_literal_search():
    result = knowledge.search('dy.expense.document.type', knowledge_type='source', limit=3)
    assert result['search_strategy'] == 'literal'
    assert result['search_terms'] == ['dy.expense.document.type']
    assert knowledge.search('NOT repo:hrp', knowledge_type='source')['count'] == 0


def test_question_with_module_identifier_can_still_extract_keywords():
    assert knowledge._question_terms('问：本期是否交付移动报销（mobile_expense），适用哪些公司？')
    assert not knowledge._question_terms('不存在的源码符号_27e3ff')
    assert not knowledge._question_terms('缺失符号🙂27e3ff')


def test_plan_lists_missing_information_and_readonly_checks():
    plan = odoo_planning.design({'modules_needed': [{'module': 'dy_fin_mdm'}]},
        {'configuration_checks': [{'model': 'dy.fin.account.analytic.plan',
          'company_id': 4, 'domain': [['code', '=', 'A']], 'expected_values': {'name': '方案A'}}]})
    steps = [step for phase in plan['phases'] for step in phase['steps']]
    assert any(step['tool'] == 'dy_odoo_record_search' and step['params']['comparison']['expected_values']['company_id'] == 4 for step in steps)
    assert any(step['tool'] == 'dy_odoo_model_fields' for step in steps)
    assert plan['blockers'] and plan['implementation_ready'] is False
    assert odoo_planning.validate(plan, server.TOOL_SCHEMAS)['valid']
    assert all(step['tool'] not in {'dy_odoo_record_create', 'dy_odoo_record_update'} for step in steps)


def test_empty_plan_does_not_look_implementation_ready():
    plan = odoo_planning.design({'modules_needed': []})
    assert plan['missing_information'] and plan['blockers']
    assert all(phase['steps'] for phase in plan['phases'])
    assert odoo_planning.validate(plan, server.TOOL_SCHEMAS)['implementation_ready'] is False


@pytest.fixture
def live_comparison(tmp_path, monkeypatch):
    for key, value in dict(APP_URL='https://test.example', DB_NAME='UAT', APP_USER='test', APP_PASSWORD='test').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    report = report_for(tmp_path)
    report['basis']['connection_fingerprint'] = configured_environment_info()['connection_fingerprint']
    record = report['records'][0]

    class Client:
        def search_read(self, *args, **kwargs):
            return [{'id': 7, 'name': '真实名称', 'company_id': [4, '甲院'], 'active': False}]

        def read_records(self, *args):
            return self.search_read()

    monkeypatch.setattr(runtime, 'odoo_client', lambda root: Client())

    def query(expected, *, count=1, read=False):
        comparison = {'expected_count': count, 'expected_values': expected}
        context = {**report['basis'], 'question_id': record['question_id'], 'answer_revision': record['answer_revision']}
        args = dict(trace_id='comparison-live', model='demo.config', trace_context=context, comparison=comparison)
        if read:
            args.update(ids=[7], fields=list(expected))
        else:
            args.update(args=[[]], kwargs={'fields': list(expected), 'limit': count + 1})
        result, error = server.execute_tool('dy_odoo_record_read' if read else 'dy_odoo_record_search', args, tmp_path)
        assert error is None, error
        assert result[0]['id'] == 7  # 保持既有返回值，不把比较包裹到业务行里。
        evidence = dict(kind='odoo', model='demo.config', reference='odoo://demo.config/7',
                        trace_id='comparison-live', comparison=comparison)
        record['evidence_refs'] = [evidence]
        for req in record['requirements']:
            req.update(verification_target='configuration', evidence_refs=[evidence])
        record['verified_at'] = datetime.now(timezone.utc).isoformat()
        return report

    return query


@pytest.mark.parametrize('read', [False, True])
def test_current_matching_values_accept_configuration_pass(tmp_path, live_comparison, read):
    report = live_comparison({'name': '真实名称', 'company_id': 4}, read=read)
    assert validate(tmp_path, report)['valid']


@pytest.mark.parametrize('expected,count', [({'name': '错误名称'}, 1), ({'name': '真实名称'}, 2),
                                            ({'company_id': 99}, 1), ({'active': 0}, 1)])
def test_mismatch_cannot_be_reported_as_pass(tmp_path, live_comparison, expected, count):
    report = live_comparison(expected, count=count)
    assert not validate(tmp_path, report)['valid']
    rec = report['records'][0]
    rec['result'] = '不通过'
    for req in rec['requirements']:
        req['result'] = '不通过'
    assert validate(tmp_path, report)['valid']


def test_expected_values_cannot_be_changed_after_query(tmp_path, live_comparison):
    report = live_comparison({'name': '真实名称'})
    changed = copy.deepcopy(report)
    changed['records'][0]['evidence_refs'][0]['comparison']['expected_values']['name'] = '伪造预期'
    assert not validate(tmp_path, changed)['valid']


def test_comparison_trace_does_not_persist_expected_or_returned_values(tmp_path, live_comparison):
    live_comparison({'name': '真实名称'})
    from dy_superpowers_mcp.trace import TraceStore
    traces = TraceStore(tmp_path).read('comparison-live')
    assert any(e.get('comparison_status') == 'passed' for e in traces)
    assert '真实名称' not in str(traces) and '甲院' not in str(traces)


def test_pagination_limit_cannot_prove_exact_count(tmp_path, live_comparison):
    # limit=2 的第一页不能证明总数恰好为2（当前模拟返回1，应可核实总数为1）。
    report = live_comparison({'name': '真实名称'})
    assert validate(tmp_path, report)['valid']
    result, error = server.execute_tool('dy_odoo_record_search', dict(trace_id='bad-page', model='demo.config',
        args=[[]], kwargs={'fields': ['name'], 'limit': 1, 'offset': 1},
        comparison={'expected_count': 1, 'expected_values': {'name': '真实名称'}}), tmp_path)
    assert error is not None and error['code'] == -32602


def test_latest_comparison_on_same_trace_cannot_reuse_old_pass(tmp_path, live_comparison, monkeypatch):
    old = copy.deepcopy(live_comparison({'name': '真实名称'}))
    from types import SimpleNamespace
    monkeypatch.setattr(runtime, 'odoo_client', lambda _: SimpleNamespace(
        search_read=lambda *args, **kwargs: [{'id': 7, 'name': '后来被修改的名称'}]))
    live_comparison({'name': '真实名称'})
    old['records'][0]['verified_at'] = datetime.now(timezone.utc).isoformat()
    assert not validate(tmp_path, old)['valid']


def test_configuration_trace_without_comparison_cannot_prove_pass(tmp_path, live_comparison):
    report = live_comparison({'name': '真实名称'})
    del report['records'][0]['evidence_refs'][0]['comparison']
    assert not validate(tmp_path, report)['valid']


def test_partial_literal_search_does_not_start_keyword_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(knowledge, '_source_service', lambda: object())
    monkeypatch.setattr(knowledge, '_coverage', lambda _: {'status': 'partial'})
    def query(*args):
        calls.append(args)
        return {'FileCount': 0, 'Files': [], 'FilesSkipped': 1}
    monkeypatch.setattr(knowledge, '_query_result', query)
    result = knowledge.search('如何设置费用类型？', knowledge_type='source')
    assert len(calls) == 1 and result['search_strategy'] == 'literal'
    assert result['total_known'] is False and result['truncated'] is True


@pytest.mark.parametrize('check', [
    {'model': 'demo', 'domain': [], 'expected_values': {'name': '方案'}},
    {'model': 'demo', 'company_id': 4, 'domain': [], 'expected_values': {'company_id': 5}},
    {'model': 'demo', 'company_id': 4, 'domain': [], 'expected_values': {'name': '方案'}, 'expected_count': 100},
])
def test_plan_rejects_incomplete_or_conflicting_configuration_checks(check):
    from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError
    with pytest.raises(OdooConfigurationError):
        odoo_planning.design({'modules_needed': []}, {'configuration_checks': [check]})
