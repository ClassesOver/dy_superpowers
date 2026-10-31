"""明确答卷驱动的真实 MCP 效用测试；沿用专用隔离环境与既有写白名单。"""
import asyncio
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

from test_all_mcp_live import PLUGIN, REPO, _isolated_environment, pytestmark


def test_answer_to_live_inspection_and_configuration(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = _isolated_environment(tmp_path)
    env['DY_ODOO_CONFIG_WRITE_ENABLED'] = '1'
    workspace = Path(env['DY_WORKSPACE_ROOT'])
    workspace.mkdir()
    output = Path(os.environ.get('DY_ANSWER_LIVE_OUTPUT_DIR', str(REPO / 'docs/testing/logs/2026-10-31-knowledge-natural-language/answer-live')))
    output.mkdir(parents=True, exist_ok=True)
    outcomes, counts = [], {}

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=[str(PLUGIN / 'mcp-server/src/server.py')], cwd=str(workspace), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(name, *, trace_id='answer-live', **args):
                    counts[name] = counts.get(name, 0) + 1
                    response = await session.call_tool(name, dict(trace_id=trace_id, **args))
                    assert not response.is_error, (name, response.content)
                    try:
                        return json.loads(response.content[0].text)
                    except json.JSONDecodeError:
                        return response.content[0].text

                company = (await call('dy_odoo_record_search', model='res.company',
                    kwargs={'fields': ['id', 'name'], 'limit': 1, 'context': {'view_company': True}}))[0]
                for module, qid, model, app in (
                    ('expense', None, 'ir.module.module', 'dy_expense'),
                    ('general-ledger', 'GL-5-015', 'dy.fin.account.analytic.plan', 'dy_fin_mdm')):
                    initial = await call('dy_module_memory_read', module=module)
                    question = next(q for q in initial['questions'] if qid is None or q['question_id'] == qid)
                    qid = question['question_id']
                    code = 'answer_' + uuid.uuid4().hex
                    expected_name = '调研答案辅助核算验收'
                    text = (f'隔离公司 {company["id"]} 需要已安装 {app}。' if module == 'expense' else
                            f'隔离公司 {company["id"]} 需要辅助核算方案，编码 {code}，名称 {expected_name}。')
                    material = workspace / (module + '-answer.txt')
                    material.write_text(text, encoding='utf-8')
                    attachment = dict(reference=material.name,
                        sha256=hashlib.sha256(material.read_bytes()).hexdigest(), requirements=[text])
                    await call('dy_research_answers_save', module=module,
                        scope=f'合成答案真实效用测试/公司{company["id"]}',
                        template_revision=initial['template_revision'], batch_id='answer-live', confirmed=True,
                        answers=[dict(question_id=qid, answer=text, research_complete=True,
                                      expected_revision=None, attachments=[attachment])])
                    state = await call('dy_module_memory_read', module=module)
                    revision = state['records'][0]['revision']
                    prepared = await call('dy_inspection_prepare', module=module, scope_id=state['scope_id'])
                    assert not prepared['verification']['performed']
                    context = {**prepared['basis'], 'question_id': qid,
                               'answer_revision': revision, 'requirement_id': 'answer-requirement'}
                    hits = await call('dy_knowledge_search', query=model if module != 'expense' else app,
                        module=app, knowledge_type='source', limit=2)
                    assert hits['count'] > 0
                    await call('dy_knowledge_read', reference=hits['results'][0]['reference'], limit=100)
                    metadata = await call('dy_odoo_module_info', module=app)
                    assert metadata[0]['state'] == 'installed'
                    fields = await call('dy_odoo_model_fields', model=model)
                    assert 'name' in fields
                    comparison = dict(expected_count=1, expected_values=(
                        {'name': app, 'state': 'installed'} if module == 'expense' else
                        {'name': expected_name, 'code': code, 'company_id': company['id']}))

                    def report(result, trace_id, reason):
                        evidence = dict(kind='odoo', reference='odoo://' + model,
                                        trace_id=trace_id, model=model, comparison=comparison)
                        records = [dict(question_id=q['question_id'], answer_revision=None,
                            verification_method='待实时核验', verified_at=datetime.now(timezone.utc).isoformat(),
                            reason='本次只核验指定题，其余保留未检查', requirements=[], evidence_refs=[], result='未检查')
                            for q in state['questions']]
                        record = next(r for r in records if r['question_id'] == qid)
                        record.update(answer_revision=revision, result=result, reason=reason,
                            evidence_refs=[evidence], requirements=[dict(requirement_id='answer-requirement',
                            text=text, source_refs=[dict(kind='answer', question_id=qid, revision=revision),
                                dict(kind='attachment', reference=material.name, sha256=attachment['sha256'])],
                            verification_target='configuration', result=result, evidence_refs=[evidence])])
                        return dict(basis=prepared['basis'], expected_checks=prepared['expected_checks'], records=records)

                    if module == 'expense':
                        actual = await call('dy_odoo_record_search', trace_id='expense-installed',
                            trace_context=context, model=model, args=[[['name', '=', app]]],
                            kwargs={'fields': ['name', 'state'], 'limit': 2}, comparison=comparison)
                        assert len(actual) == 1 and actual[0]['state'] == 'installed'
                        final = report('通过', 'expense-installed', '实际安装状态与明确答案一致；不代表报销流程通过')
                        outcomes.append(dict(module=module, question_id=qid, installed_matches_answer=True))
                    else:
                        async def search(trace_id):
                            return await call('dy_odoo_record_search', trace_id=trace_id,
                                trace_context=context, model=model,
                                args=[[['company_id', '=', company['id']], ['code', '=', code]]],
                                kwargs={'fields': ['name', 'code', 'company_id', 'write_date'], 'limit': 2}, comparison=comparison)

                        assert await search('ledger-missing') == []
                        missing = await call('dy_inspection_validate',
                            report=report('不通过', 'ledger-missing', '实际未找到答案所需方案'))
                        assert missing['valid'] and missing['gate'] == '不通过', missing
                        args = dict(environment={'url': env['APP_URL'], 'database': env['DB_NAME']},
                            model=model, company_id=company['id'], confirmed=True,
                            values={'name': '故意不符的测试旧值', 'code': code, 'company_id': company['id']},
                            idempotency_key='answer-create')
                        preview = await call('dy_odoo_record_create', **args, dry_run=True)
                        created = await call('dy_odoo_record_create', **args, dry_run=False,
                                             confirmation_digest=preview['confirmation_digest'])
                        assert created['data_readback_status'] == 'passed' and not created['accepted']
                        row = (await search('ledger-mismatch'))[0]
                        assert row['id'] == created['record_id'] and row['name'] != expected_name
                        mismatch = report('不通过', 'ledger-mismatch', '公司与编码一致，但名称不符')
                        assert (await call('dy_inspection_validate', report=mismatch))['valid']
                        # 名称不符的实时比较不能支撑“通过”。
                        false_pass = report('通过', 'ledger-mismatch', '测试错误判断是否被语义校验拦截；不保存此报告')
                        structural_only = await call('dy_inspection_validate', report=false_pass)
                        assert not structural_only['valid']
                        update = {**args, 'values': {'name': expected_name}, 'record_id': row['id'],
                                  'expected_values': {'name': row['name']},
                                  'expected_write_date': row['write_date'], 'idempotency_key': 'answer-update'}
                        preview = await call('dy_odoo_record_update', **update, dry_run=True)
                        updated = await call('dy_odoo_record_update', **update, dry_run=False,
                                             confirmation_digest=preview['confirmation_digest'])
                        assert updated['data_readback_status'] == 'passed' and not updated['accepted']
                        rows = await search('ledger-final')
                        assert len(rows) == 1 and rows[0]['id'] == row['id']
                        assert rows[0]['name'] == expected_name and rows[0]['code'] == code
                        assert rows[0]['company_id'][0] == company['id']
                        final = report('通过', 'ledger-final', '复用同一记录修正名称，独立回读与答案一致')
                        (output / 'configuration-browser-input.json').write_text(json.dumps(dict(
                            url=env['APP_URL'], database=env['DB_NAME'], company_id=company['id'],
                            company_name=company['name'], model=model, data_readback_status='passed',
                            records=[dict(record_id=row['id'], expected_name=expected_name, expected_code=code)]),
                            ensure_ascii=False, indent=2) + '\n')
                        outcomes.append(dict(module=module, question_id=qid, missing_detected=True,
                            mismatch_detected=True, existing_record_reused=True, independent_readback_passed=True,
                            false_pass_rejected=not structural_only['valid']))

                    checked = await call('dy_inspection_validate', report=final)
                    assert checked['valid'] and checked['gate'] == '待确认', checked
                    without_evidence = copy.deepcopy(final)
                    rec = next(r for r in without_evidence['records'] if r['question_id'] == qid)
                    rec['evidence_refs'] = rec['requirements'][0]['evidence_refs'] = []
                    assert not (await call('dy_inspection_validate', report=without_evidence))['valid']
                    saved = await call('dy_inspection_report_save', report=final, confirmed=True)
                    assert saved['saved']
                    read = await call('dy_inspection_report_read', module=module, report_id=saved['report_id'])
                    assert next(r['reason'] for r in final['records'] if r['question_id'] == qid) in read
                    await call('dy_inspection_report_export_pdf', module=module, report_id=saved['report_id'])
                    (output / (module + '-report.pdf')).write_bytes((workspace / saved['pdf_path']).read_bytes())
                    (output / (module + '-report.json')).write_text(json.dumps(final, ensure_ascii=False, indent=2) + '\n')
                    (output / material.name).write_bytes(material.read_bytes())
                    trace_ids = ['expense-installed'] if module == 'expense' else ['ledger-missing', 'ledger-mismatch', 'ledger-final']
                    traces = {tid: await call('dy_trace_read', trace_id=tid) for tid in trace_ids}
                    (output / (module + '-evidence-traces.json')).write_text(json.dumps(traces, ensure_ascii=False, indent=2) + '\n')
                    await call('dy_research_answers_save', module=module, scope=state['scope'],
                        template_revision=state['template_revision'], batch_id='answer-revised', confirmed=True,
                        answers=[dict(question_id=qid, answer=text + ' 下一批须重新核验。', research_complete=True,
                                      expected_revision=revision, attachments=[attachment])])
                    assert not (await call('dy_inspection_validate', report=final))['valid']
                    outcomes[-1].update(full_template_count=state['total'], batch_gate=checked['gate'],
                        missing_evidence_rejected=True, stale_answer_report_rejected=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=240))
    (output / 'results.json').write_text(json.dumps(dict(transport='真实 MCP stdio',
        scenarios=outcomes, tool_calls=counts, browser_acceptance_status='not_checked'),
        ensure_ascii=False, indent=2) + '\n')
