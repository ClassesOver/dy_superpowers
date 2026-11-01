"""费用与总账：确认答卷、只读计划、差异巡检、受限修正及答卷修订的真实闭环。"""
import asyncio
import copy
import hashlib
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_all_mcp_live import PLUGIN, REPO, _isolated_environment, pytestmark


ANSWER_FIELDS = ('module', 'scope_id', 'template_revision', 'question_id', 'answer_revision', 'environment')


def answer_binding(context):
    """配置只绑定答卷身份与环境；巡检证据的 basis 与 requirement_id 不参与配置确认。"""
    return {key: context[key] for key in ANSWER_FIELDS}


@pytest.mark.parametrize('module,qid,model,app', [
    ('expense', 'DY-EXP-19', 'dy.expense.personal.tax.calculation.rule', 'dy_expense'),
    ('general-ledger', 'GL-5-015', 'dy.fin.account.analytic.plan', 'dy_fin_mdm'),
])
def test_confirmed_answer_drives_configuration_lifecycle(tmp_path, module, qid, model, app):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError

    env = _isolated_environment(tmp_path)
    env['DY_ODOO_CONFIG_WRITE_ENABLED'] = '1'
    workspace = Path(env['DY_WORKSPACE_ROOT'])
    workspace.mkdir()
    output = Path(os.environ.get('DY_ANSWER_LOOP_OUTPUT_DIR',
        str(REPO / 'docs/testing/logs/2026-11-01-answer-configuration-loop/live'))) / module
    output.mkdir(parents=True, exist_ok=True)
    checks = {}

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=[str(PLUGIN / 'mcp-server/src/server.py')], cwd=str(workspace), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(tool, **args):
                    response = await session.call_tool(tool, {'trace_id': 'answer-loop', **args})
                    assert not response.is_error, (tool, response.content)
                    try:
                        return json.loads(response.content[0].text)
                    except json.JSONDecodeError:
                        return response.content[0].text

                async def denied(tool, **args):
                    with pytest.raises(MCPError) as caught:
                        await call(tool, **args)
                    assert caught.value.code == -32602

                company = (await call('dy_odoo_record_search', model='res.company',
                    kwargs={'fields': ['id', 'name'], 'limit': 1, 'context': {'view_company': True}}))[0]
                token = uuid.uuid4().hex
                target = {'name': '答卷闭环_' + token, 'company_id': company['id']}
                if module == 'general-ledger':
                    target['code'] = 'loop_' + token
                else:
                    # 居民标志修正会触发有效期重叠约束；复测不得与历史测试规则冲突。
                    existing = []
                    while True:
                        page = await call('dy_odoo_record_search', model=model,
                            args=[[['company_id', '=', company['id']], ['cal_type', '=', 'pre_tax']]],
                            kwargs={'fields': ['effective_date', 'expiration_date'], 'limit': 100,
                                'offset': len(existing), 'context': {'active_test': False}})
                        existing.extend(page)
                        if len(page) < 100:
                            break
                    assert all(r['effective_date'] and r['expiration_date'] for r in existing), '已有规则日期不明确，暂停选择测试期间'
                    start = max([date(2026, 1, 1), *[date.fromisoformat(r['expiration_date']) + timedelta(days=1) for r in existing]])
                    target.update(effective_date=start.isoformat(), expiration_date=(start + timedelta(days=364)).isoformat(),
                        cal_type='pre_tax', resident_flag=True)
                text = '本期隔离测试配置要求：' + json.dumps(target, ensure_ascii=False, sort_keys=True)
                material = workspace / 'confirmed-requirements.txt'
                material.write_text(text)
                attachment = dict(reference=material.name, sha256=hashlib.sha256(material.read_bytes()).hexdigest(), requirements=[text])
                initial = await call('dy_module_memory_read', module=module)
                await call('dy_research_answers_save', module=module, scope='闭环测试/公司' + str(company['id']),
                    template_revision=initial['template_revision'], batch_id='initial', confirmed=True,
                    answers=[dict(question_id=qid, answer=text, research_complete=True,
                        expected_revision=None, attachments=[attachment])])
                state = await call('dy_module_memory_read', module=module)
                prepared = await call('dy_inspection_prepare', module=module, scope_id=state['scope_id'])
                context = {**prepared['basis'], 'question_id': qid, 'answer_revision': state['records'][0]['revision'],
                    'requirement_id': 'required-configuration'}
                hits = await call('dy_knowledge_search', query=model, module=app, knowledge_type='source', limit=2)
                assert hits['count'] > 0
                await call('dy_knowledge_read', reference=hits['results'][0]['reference'], limit=100)
                key = 'code' if module == 'general-ledger' else 'name'
                domain = [[key, '=', target[key]]]
                plan = await call('dy_odoo_design_solution', requirements={'modules_needed': [{'module': app}]},
                    user_answers={'configuration_checks': [dict(model=model, company_id=company['id'],
                        domain=domain, expected_values=target)]})
                assert (await call('dy_odoo_validate_plan', plan=plan))['valid']
                step = next(s for p in plan['phases'] for s in p['steps'] if s['tool'] == 'dy_odoo_record_search')
                comparison = step['params']['comparison']

                async def search(trace_id):
                    params = copy.deepcopy(step['params'])
                    params['kwargs']['fields'] = list(dict.fromkeys([*target, 'write_date']))
                    return await call(step['tool'], **params, trace_id=trace_id, trace_context=context)

                def report(result, trace_id):
                    evidence = dict(kind='odoo', reference='odoo://' + model, trace_id=trace_id,
                        model=model, comparison=comparison)
                    records = [dict(question_id=q['question_id'], answer_revision=None, verification_method='待实时核验',
                        verified_at=datetime.now(timezone.utc).isoformat(), reason='本批只核验指定配置',
                        requirements=[], evidence_refs=[], result='未检查') for q in state['questions']]
                    record = next(r for r in records if r['question_id'] == qid)
                    record.update(answer_revision=context['answer_revision'], result=result,
                        reason='按确认答卷核对实际字段', evidence_refs=[evidence], requirements=[dict(
                            requirement_id=context['requirement_id'], text=text, verification_target='configuration',
                            source_refs=[dict(kind='answer', question_id=qid, revision=context['answer_revision']),
                                dict(kind='attachment', reference=material.name, sha256=attachment['sha256'])],
                            result=result, evidence_refs=[evidence])])
                    return dict(basis=prepared['basis'], expected_checks=prepared['expected_checks'], records=records)

                assert await search('missing') == []
                assert (await call('dy_inspection_validate', report=report('不通过', 'missing')))['valid']
                checks['missing_detected'] = True
                wrong = {**target, ('name' if module == 'general-ledger' else 'resident_flag'):
                    ('故意错误名称' if module == 'general-ledger' else False)}
                args = dict(environment={'url': env['APP_URL'], 'database': env['DB_NAME']}, model=model,
                    company_id=company['id'], values=wrong, idempotency_key='create', confirmed=True, trace_context=answer_binding(context))
                preview = await call('dy_odoo_record_create', **args, dry_run=True)
                assert preview['confirmation']['trace_context'] == answer_binding(context)
                created = await call('dy_odoo_record_create', **args, confirmation_digest=preview['confirmation_digest'])
                assert created['answer_context'] == answer_binding(context) and created['data_readback_status'] == 'passed'
                row = (await search('mismatch'))[0]
                assert row['id'] == created['record_id']
                assert not (await call('dy_inspection_validate', report=report('通过', 'mismatch')))['valid']
                assert (await call('dy_inspection_validate', report=report('不通过', 'mismatch')))['valid']
                checks['false_pass_rejected'] = True
                field = 'name' if module == 'general-ledger' else 'resident_flag'
                update = {**args, 'values': {field: target[field]}, 'record_id': row['id'],
                    'expected_values': {field: row[field]}, 'expected_write_date': row['write_date'], 'idempotency_key': 'update'}
                preview = await call('dy_odoo_record_update', **update, dry_run=True)
                updated = await call('dy_odoo_record_update', **update, confirmation_digest=preview['confirmation_digest'])
                assert updated['data_readback_status'] == 'passed' and not updated['accepted'], updated
                replay = await call('dy_odoo_record_update', **update, confirmation_digest=preview['confirmation_digest'])
                assert replay['status'] == 'already_attempted' and replay['data_readback_status'] == 'not_rechecked'
                checks['idempotency_preserved'] = True
                final_row = (await search('final'))[0]
                assert final_row['id'] == row['id']
                final = report('通过', 'final')
                assert (await call('dy_inspection_validate', report=final))['valid']
                saved = await call('dy_inspection_report_save', report=final, confirmed=True)
                assert saved['saved']
                checks['existing_record_corrected_and_rechecked'] = True
                duplicate = {**args, 'values': target, 'idempotency_key': 'duplicate'}
                preview = await call('dy_odoo_record_create', **duplicate, dry_run=True)
                await denied('dy_odoo_record_create', **duplicate, confirmation_digest=preview['confirmation_digest'])
                checks['duplicate_create_rejected'] = True
                update.update(expected_values={field: final_row[field]}, expected_write_date=final_row['write_date'],
                    idempotency_key='stale-answer-update')
                stale_preview = await call('dy_odoo_record_update', **update, dry_run=True)
                await call('dy_research_answers_save', module=module, scope=state['scope'], scope_id=state['scope_id'],
                    template_revision=state['template_revision'], batch_id='revised', confirmed=True,
                    answers=[dict(question_id=qid, answer=text + ' 本批要求已再次确认。', research_complete=True,
                        expected_revision=context['answer_revision'], attachments=[attachment])])
                await denied('dy_odoo_record_update', **update, confirmation_digest=stale_preview['confirmation_digest'])
                assert not (await call('dy_inspection_validate', report=final))['valid']
                checks['stale_answer_write_and_report_rejected'] = True
                state = await call('dy_module_memory_read', module=module, scope_id=state['scope_id'])
                prepared = await call('dy_inspection_prepare', module=module, scope_id=state['scope_id'])
                context = {**prepared['basis'], 'question_id': qid, 'answer_revision': state['records'][0]['revision'],
                    'requirement_id': 'required-configuration'}
                current = (await search('revised-final'))[0]
                assert current['id'] == row['id'] and current['write_date'] == final_row['write_date']
                final = report('通过', 'revised-final')
                checked = await call('dy_inspection_validate', report=final)
                assert checked['valid'] and checked['gate'] == '待确认'
                saved = await call('dy_inspection_report_save', report=final, confirmed=True)
                assert saved['saved']
                await call('dy_inspection_report_export_pdf', module=module, report_id=saved['report_id'])
                (output / 'report.pdf').write_bytes((workspace / saved['pdf_path']).read_bytes())
                (output / 'report.json').write_text(json.dumps(final, ensure_ascii=False, indent=2))
                (output / 'configuration-browser-input.json').write_text(json.dumps(dict(
                    url=env['APP_URL'], database=env['DB_NAME'], company_id=company['id'], company_name=company['name'],
                    model=model, answer_context=answer_binding(context), report_id=saved['report_id'], data_readback_status='passed',
                    records=[dict(record_id=row['id'], expected_name=target['name'],
                        **({'expected_code': target['code']} if 'code' in target else {}))]), ensure_ascii=False, indent=2))
                checks['revised_answer_independently_rechecked'] = True

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=240))
    finally:
        (output / 'results.json').write_text(json.dumps(dict(module=module, question_id=qid,
            transport='真实 MCP stdio', scenarios=checks, browser_acceptance_status='not_checked'), ensure_ascii=False, indent=2))
