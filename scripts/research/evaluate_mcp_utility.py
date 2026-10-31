#!/usr/bin/env python3
"""通过真实 MCP 测试原题规划与明确答卷门禁；不伪造线上证据。"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'plugins/dy_superpowers/mcp-server/src'


async def run():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    counts, planning, scenarios = {}, [], []
    with tempfile.TemporaryDirectory(prefix='dy-mcp-utility-') as directory:
        workspace = Path(directory)
        env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        env.update(DY_WORKSPACE_ROOT=directory, CODEX_HOME=directory + '/codex', PYTHONPATH=str(SRC))
        program = ('from dy_superpowers_mcp import knowledge; '
                   'knowledge._source_service().idle_seconds=1; import server; server.main()')
        params = StdioServerParameters(command=sys.executable, args=['-c', program], cwd=directory, env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)

                async def call(name, **arguments):
                    counts[name] = counts.get(name, 0) + 1
                    response = await session.call_tool(name, {'trace_id': 'utility-evaluation', **arguments})
                    assert not response.is_error, (name, response.content)
                    return json.loads(response.content[0].text)

                for module in ('expense', 'general-ledger'):
                    state = await call('dy_module_memory_read', module=module)
                    for question in state['questions']:
                        if question['question'].strip() == '/':
                            continue
                        analysis = await call('dy_odoo_analyze_requirements', description=question['question'])
                        plan = await call('dy_odoo_design_solution', requirements=analysis,
                                          user_answers={'范围': '仅测试，不执行配置'})
                        validation = await call('dy_odoo_validate_plan', plan=plan)
                        planning.append({'id': question['question_id'],
                            'candidates': [row['module'] for row in analysis['modules_needed']],
                            'structurally_valid': validation['valid'],
                            'readonly_check_steps': len(plan['phases'][-1]['steps']),
                            'missing_information': plan['missing_information'],
                            'blockers': plan['blockers'],
                            'implementation_ready': validation['implementation_ready'],
                            'execution_authorized': plan['execution_authorized']})

                    # 明确的合成测试答案及附件，不代表真实项目确认。
                    question = state['questions'][0]
                    required_app = 'dy_expense' if module == 'expense' else 'dy_fin_mdm'
                    material = workspace / (module + '-requirements.txt')
                    material.write_text(f'隔离测试公司需要安装 {required_app}，禁止以源码存在代替已安装。', encoding='utf-8')
                    attachment = {'reference': material.name,
                        'sha256': hashlib.sha256(material.read_bytes()).hexdigest(),
                        'requirements': [f'隔离测试公司需要安装 {required_app}']}
                    saved = await call('dy_research_answers_save', module=module,
                        scope='合成效用测试/不代表真实交付', template_revision=state['template_revision'],
                        batch_id='utility-answer', confirmed=True, answers=[{
                            'question_id': question['question_id'],
                            'answer': f'隔离测试公司本期需要 {required_app}；实际安装状态须实时查询。',
                            'research_complete': True, 'expected_revision': None,
                            'attachments': [attachment]}])
                    state = await call('dy_module_memory_read', module=module)
                    prepared = await call('dy_inspection_prepare', module=module,
                                          scope_id=state['scope_id'], environment='utility-test')
                    assert prepared['items'][0]['readiness'] == 'research_ready'
                    assert not prepared['verification']['performed']
                    assert len(prepared['expected_checks']) == state['total']
                    now = datetime.now(timezone.utc).isoformat()
                    revision = state['records'][0]['revision']
                    report = {'basis': prepared['basis'], 'expected_checks': prepared['expected_checks'],
                        'records': [{'question_id': q['question_id'],
                            'answer_revision': revision if q['question_id'] == question['question_id'] else None,
                            'verification_method': '待实时核验', 'verified_at': now,
                            'reason': '没有本轮线上证据', 'requirements': [], 'evidence_refs': [],
                            'result': '未检查'} for q in state['questions']]}
                    first = report['records'][0]
                    first.update(requirements=[{
                        'requirement_id': 'installed-app', 'text': attachment['requirements'][0],
                        'source_refs': [{'kind': 'answer', 'question_id': question['question_id'], 'revision': revision},
                                        {'kind': 'attachment', 'reference': material.name, 'sha256': attachment['sha256']}],
                        'verification_target': 'configuration', 'result': '未检查', 'evidence_refs': []}])
                    pending = await call('dy_inspection_validate', report=report)
                    assert pending['valid'] and pending['gate'] != '通过', pending
                    first['result'] = '通过'
                    first['requirements'][0]['result'] = '通过'
                    false_pass = await call('dy_inspection_validate', report=report)
                    assert not false_pass['valid']
                    scenarios.append({'module': module, 'question_id': question['question_id'],
                        'confirmed_answers': saved['confirmed_count'], 'full_question_count': state['total'],
                        'research_ready_does_not_mean_verified': True,
                        'pending_report_gate': pending['gate'], 'false_pass_without_live_evidence_rejected': True})

    return {'transport': '真实 MCP stdio', 'registered_tools': tools, 'tool_calls': counts,
        'planning': planning, 'answer_scenarios': scenarios,
        'limitations': ['合成答卷仅用于门禁测试，不代表真实业务确认。',
                        '本进程未继承测试账号凭据，未重新运行线上查询、写入或浏览器验收。',
                        '候选模块没有逐题业务金标准，不将候选非空计为需求识别正确。']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'registered_tools': len(result['registered_tools']), 'tool_calls': result['tool_calls'],
        'questions': len(result['planning']), 'zero_candidates': sum(not r['candidates'] for r in result['planning']),
        'empty_readonly_checks': sum(r['readonly_check_steps'] == 0 for r in result['planning']),
        'implementation_ready': sum(r['implementation_ready'] for r in result['planning']),
        'answer_scenarios': result['answer_scenarios']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
