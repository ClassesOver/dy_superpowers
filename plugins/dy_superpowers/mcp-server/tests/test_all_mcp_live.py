"""显式启用的全工具真实 stdio 验证；不读取仓库 .env。

运行前由操作者准备真实隔离 Odoo，并设置 DY_ALL_MCP_LIVE=1、APP_URL、
DB_NAME、APP_USER、APP_PASSWORD。第二独立进程仅启用已批准配置模型写入，
其余进程保持关闭；模块维护始终不执行，本地记忆只写 tmp_path。
结果矩阵只保留工具名、验证类型和结论，不保存凭据或 Odoo 返回业务行。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest


PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
EXPECTED_TOOLS = {
    'dy_odoo_environment_info', 'dy_session_initialize',
    'dy_research_answers_save', 'dy_research_answers_import',
    'dy_inspection_prepare', 'dy_inspection_validate', 'dy_inspection_report_save',
    'dy_inspection_report_read', 'dy_inspection_report_export_pdf',
    'dy_trace_event', 'dy_trace_read',
    'dy_memory_read', 'dy_memory_write', 'dy_memory_search', 'dy_module_memory_read',
    'dy_research_progress', 'dy_research_unanswered_export',
    'dy_odoo_record_search', 'dy_odoo_record_read', 'dy_odoo_model_fields',
    'dy_odoo_module_list_installed', 'dy_odoo_module_info',
    'dy_knowledge_module_info', 'dy_knowledge_search', 'dy_knowledge_read',
    'dy_odoo_module_list_available', 'dy_odoo_model_list', 'dy_odoo_model_search_field',
    'dy_odoo_diagnostics_health_check', 'dy_knowledge_community_gaps',
    'dy_odoo_import_template', 'dy_odoo_import_preview',
    'dy_odoo_analyze_requirements', 'dy_odoo_design_solution', 'dy_odoo_validate_plan',
    'dy_odoo_record_execute',
}
# 写开关为 0 时这些工具不出现在列表中，调用被拒绝。
GATED_WRITE_TOOLS = {'dy_odoo_record_create', 'dy_odoo_record_update', 'dy_odoo_import_execute',
                     'dy_odoo_module_install', 'dy_odoo_module_uninstall'}

pytestmark = pytest.mark.skipif(
    os.environ.get('DY_ALL_MCP_LIVE') != '1',
    reason='真实全工具验证须显式设置 DY_ALL_MCP_LIVE=1',
)


def _isolated_environment(tmp_path):
    """只继承启动必需项及明确提供的测试身份，阻断生产连接和写入开关。"""
    url = os.environ.get('APP_URL', '')
    parsed = urlsplit(url)
    assert parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert parsed.port == 18069 and parsed.path in {'', '/'}
    assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    database = os.environ.get('DB_NAME', '')
    assert database.startswith('dy_mcp_e2e_'), '必须使用专用隔离库'
    assert os.environ.get('APP_USER') and os.environ.get('APP_PASSWORD'), '必须显式提供隔离库身份'
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    env.update(APP_URL=url.rstrip('/'), DB_NAME=database,
               APP_USER=os.environ['APP_USER'], APP_PASSWORD=os.environ['APP_PASSWORD'],
               CODEX_HOME=str(tmp_path / 'codex'), DY_WORKSPACE_ROOT=str(tmp_path / 'workspace'),
               DY_ODOO_ENVIRONMENT='all-mcp-live', DY_ODOO_CONFIG_WRITE_ENABLED='0',
               DY_ODOO_MODULE_WRITE_ENABLED='0', PYTHONUNBUFFERED='1')
    return env


def test_all_50_mcp_tools_over_real_stdio_and_isolated_odoo(tmp_path):
    """真实读取、真实本地生命周期、明确预览/禁用/外部阻塞分别登记。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError

    env = _isolated_environment(tmp_path)
    workspace = Path(env['DY_WORKSPACE_ROOT'])
    workspace.mkdir()
    assert not (workspace / '.env').exists()
    output = Path(os.environ.get('DY_ALL_MCP_OUTPUT_DIR',
        str(REPO / 'docs/testing/logs/2026-10-31-all-mcp/live')))
    output.mkdir(parents=True, exist_ok=True)
    matrix = {name: [] for name in sorted(EXPECTED_TOOLS | GATED_WRITE_TOOLS)}
    failures = []

    def persist():
        payload = {'database': env['DB_NAME'], 'transport': '真实 MCP stdio',
                   'odoo': '真实隔离 Odoo 12；独立进程验证白名单配置真实写入，模块只预览',
                   'tools': matrix}
        (output / 'tool-results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=[str(PLUGIN / 'mcp-server/src/server.py')], cwd=str(workspace), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listed = {tool.name for tool in (await session.list_tools()).tools}
                assert len(EXPECTED_TOOLS) == 36
                assert listed == EXPECTED_TOOLS, f'工具清单漂移：缺少{EXPECTED_TOOLS-listed}，新增{listed-EXPECTED_TOOLS}'
                active_session = session

                async def call(name, arguments=None, *, check=lambda value: value is not None,
                               kind='真实成功路径', denied=None, blocked=None, error_code=-32602,
                               allowed_blockers=()):
                    entry = {'scenario': kind, 'status': '调用中'}
                    matrix[name].append(entry)
                    persist()
                    try:
                        response = await active_session.call_tool(name, {'trace_id': 'all-mcp-live', **(arguments or {})})
                        assert not response.model_dump(by_alias=True).get('isError', False), 'MCP 返回 isError'
                        text = response.content[0].text
                        try:
                            value = json.loads(text)
                        except json.JSONDecodeError:
                            value = text
                        assert denied is None and blocked is None, '预期业务门禁没有阻止调用'
                        if isinstance(value, dict) and value.get('rpc_diagnostic'):
                            entry['rpc_diagnostic'] = value['rpc_diagnostic']
                        assert check(value), f'{name} 的业务断言失败'
                        entry['status'] = '通过'
                        return value
                    except MCPError as exc:
                        dependency = next((reason for reason in allowed_blockers if reason in str(exc)), None)
                        if dependency and exc.code == -32602:
                            entry.update(status='依赖阻塞', reason=dependency)
                            return None
                        expected = denied or blocked
                        if expected and expected in str(exc) and exc.code == error_code:
                            entry['status'] = '依赖阻塞' if blocked else '按策略拒绝'
                            entry['reason'] = expected
                            return None
                        entry['status'] = '失败'
                        entry['error_type'] = type(exc).__name__
                        entry['error_code'] = exc.code
                        raise AssertionError(f'{name}: 未预期的 MCP 错误 {exc.code}') from None
                    except Exception as exc:
                        entry['status'] = '失败'
                        entry['error_type'] = type(exc).__name__
                        raise
                    finally:
                        persist()

                async def reads():
                    info = await call('dy_odoo_environment_info', check=lambda r: r['configured'] is True)
                    assert info['environment'] == 'all-mcp-live'
                    await call('dy_odoo_diagnostics_health_check', check=lambda r: r['failures'] == 0 and r['passed'] == 3)
                    companies = await call('dy_odoo_record_search', {'model': 'res.company', 'kwargs': {'fields': ['id', 'name'], 'limit': 2, 'context': {'view_company': True}}}, check=lambda r: isinstance(r, list) and bool(r))
                    await call('dy_odoo_record_read', {'model': 'res.company', 'ids': [companies[0]['id']], 'fields': ['name']}, check=lambda r: len(r) == 1 and r[0]['id'] == companies[0]['id'])
                    await call('dy_odoo_model_fields', {'model': 'res.company'}, check=lambda r: r['name']['type'] == 'char')
                    await call('dy_odoo_module_list_installed', check=lambda r: bool(r) and all(row['state'] == 'installed' for row in r))
                    await call('dy_odoo_module_info', {'module': 'base'}, check=lambda r: len(r) == 1 and r[0]['name'] == 'base' and r[0]['state'] == 'installed')
                    await call('dy_odoo_module_list_available', {'limit': 5}, check=lambda r: 0 < r['count'] <= 5)
                    await call('dy_odoo_model_list', {'search': 'res.company', 'limit': 5}, check=lambda r: any(row['model'] == 'res.company' for row in r['models']))
                    await call('dy_odoo_model_search_field', {'query': 'name', 'model': 'res.company', 'limit': 5}, check=lambda r: r['count'] > 0 and all(row['model'] == 'res.company' for row in r['fields']))
                    await call('dy_odoo_record_execute', {'model': 'ir.module.module', 'method': 'check_access_rights'}, check=lambda r: r['allowed'] is True and r['execution_authorized'] is False)

                async def knowledge_and_plan():
                    await call('dy_knowledge_module_info', {'module': 'dy_expense'}, check=lambda r: r['found'] is True)
                    hits = await call('dy_knowledge_search', {'query': 'dy.expense.document.type', 'module': 'dy_expense', 'knowledge_type': 'source', 'limit': 2}, check=lambda r: r['count'] > 0 and r['coverage']['backend'] == 'zoekt')
                    await call('dy_knowledge_read', {'reference': hits['results'][0]['reference'], 'limit': 100}, check=lambda r: r['found'] is True)
                    requirements = await call('dy_odoo_analyze_requirements', {'description': '费用报销及总账凭证需要调研确认'}, check=lambda r: bool(r['modules_needed']) and r['execution_authorized'] is False)
                    plan = await call('dy_odoo_design_solution', {'requirements': requirements}, check=lambda r: len(r['phases']) == 3 and r['execution_authorized'] is False)
                    await call('dy_odoo_validate_plan', {'plan': plan}, check=lambda r: r['valid'] is True and not r['errors'])

                async def workspace_lifecycle():
                    await call('dy_session_initialize', {'source': 'startup'}, check=lambda r: '工作区' in r)
                    await call('dy_memory_write', {'name': 'profile.md', 'content': '隔离验证项目记忆', 'confirmed': True})
                    await call('dy_memory_read', {'name': 'profile.md'}, check=lambda r: r == '隔离验证项目记忆')
                    await call('dy_memory_search', {'query': '隔离验证'}, check=lambda r: bool(r['results']))
                    for module in ('expense', 'general-ledger'):
                        initial = await call('dy_module_memory_read', {'module': module}, check=lambda r: r['total'] > 0 and r['records'] == [])
                        question = initial['questions'][0]
                        await call('dy_research_answers_save', {'module': module, 'scope': '隔离测试范围', 'template_revision': initial['template_revision'], 'answers': [{'question_id': question['question_id'], 'answer': '本项尚未完成业务核实；只验证工具生命周期。', 'research_complete': False, 'expected_revision': None}], 'batch_id': 'live-first', 'confirmed': True}, check=lambda r: r['confirmed_count'] == 1)
                        state = await call('dy_module_memory_read', {'module': module}, check=lambda r: r['confirmed_count'] == 1 and r['answered'] == 0)
                        await call('dy_research_progress', {'module': module, 'intent': 'progress'}, check=lambda r: r['next_action'] == 'report_progress' and r['confirmed_count'] == 1)
                        exported = await call('dy_research_unanswered_export', {'module': module, 'confirmed': True}, check=lambda r: (workspace / r['path']).is_file())
                        await call('dy_research_answers_import', {'path': exported['path']}, check=lambda r: r['can_write_confirmed'] is False)
                        prepared = await call('dy_inspection_prepare', {'module': module, 'scope_id': state['scope_id']}, check=lambda r: len(r['items']) == state['total'] and r['preparation']['blocked'] > 0)
                        await call('dy_knowledge_community_gaps', {'module': module, 'scope_id': state['scope_id']}, check=lambda r: r['answer_version'] == state['answer_version'] and r['accepted'] is False)
                        now = datetime.now(timezone.utc).isoformat()
                        revisions = {r['question_id']: r['revision'] for r in state['records']}
                        report = {'basis': {k: state[k] for k in ('module', 'scope_id', 'template_revision', 'answer_version')}, 'expected_checks': [{'question_id': q['question_id']} for q in state['questions']], 'records': [{'question_id': q['question_id'], 'answer_revision': revisions.get(q['question_id']), 'verification_method': '待现场核验', 'verified_at': now, 'reason': '真实业务依赖尚未核实，不宣称通过。', 'requirements': [], 'evidence_refs': [], 'result': '未检查'} for q in state['questions']]}
                        report['basis'] = prepared['basis']
                        await call('dy_inspection_validate', {'report': report}, check=lambda r: r['valid'] is True and r['gate'] != '通过')
                        saved = await call('dy_inspection_report_save', {'report': report, 'confirmed': True}, check=lambda r: r['saved'] is True and (workspace / r['pdf_path']).read_bytes().startswith(b'%PDF'))
                        report_args = {'module': module, 'report_id': saved['report_id']}
                        await call('dy_inspection_report_read', report_args, check=lambda r: '未检查' in r and '机器可读数据' not in r)
                        await call('dy_inspection_report_export_pdf', report_args, check=lambda r: r['exported'] is True and r['created'] is False)
                    await call('dy_trace_event', {'event': {'event': 'live.validation', 'memory_content': 'password=live-redaction-probe'}}, check=lambda r: r['stored'] is True)
                    await call('dy_trace_read', check=lambda r: 'live.validation' in str(r) and 'live-redaction-probe' not in str(r) and '[REDACTED]' in str(r) and all(not ({'rows', 'actual_values', 'expected_values'} & event.keys()) for event in r if event.get('event') == 'odoo.read'))

                async def write_boundaries():
                    companies = await call('dy_odoo_record_search', {'model': 'res.company', 'kwargs': {'fields': ['id'], 'limit': 1, 'context': {'view_company': True}}}, check=lambda r: bool(r))
                    company_id = companies[0]['id']
                    config = {'environment': {'url': env['APP_URL'], 'database': env['DB_NAME']}, 'model': 'dy.fin.account.analytic.plan', 'company_id': company_id, 'values': {'name': '隔离预览方案', 'code': 'live-preview', 'company_id': company_id}, 'idempotency_key': 'live-preview-create', 'confirmed': False}
                    for operation in ('create', 'update'):
                        args = dict(config)
                        if operation == 'update':
                            # 仅本地预览/默认关闭，不把这个占位编号当成真实业务记录。
                            args.update(record_id=company_id, expected_values=dict(config['values']), expected_write_date='未读取；禁止执行', idempotency_key='live-preview-update')
                        await call('dy_odoo_record_' + operation, {**args, 'dry_run': True}, kind='写开关关闭时工具未启用', denied='写入工具未启用')
                    module_args = {'module_names': ['base'], 'environment': 'all-mcp-live', 'idempotency_key': 'live-module-preview', 'backup_reference': '未提供备份；仅预览', 'dry_run': True}
                    for operation in ('install', 'uninstall'):
                        await call('dy_odoo_module_' + operation, module_args, kind='写开关关闭时工具未启用', denied='写入工具未启用')
                    csv_args = {'model': config['model'], 'company_id': company_id, 'csv_data': f'name,code,company_id\n隔离导入预览,live-import,{company_id}\n', 'environment': 'all-mcp-live', 'idempotency_key': 'live-import-preview'}
                    await call('dy_odoo_import_execute', {**csv_args, 'confirmed': False, 'confirmation_digest': '0' * 64, 'dry_run': True}, kind='写开关关闭时工具未启用', denied='写入工具未启用')
                    models = await call('dy_odoo_model_list', {'search': config['model'], 'limit': 100}, check=lambda r: isinstance(r['models'], list))
                    if not any(r['model'] == config['model'] for r in models['models']):
                        await call('dy_odoo_import_template', {'model': config['model'], 'include_optional': True}, kind='真实调用；模型未安装，非成功路径', blocked='系统查询失败', error_code=-32603)
                        await call('dy_odoo_import_preview', csv_args, kind='真实调用；模型未安装，非成功路径', blocked='系统查询失败', error_code=-32603)
                    else:
                        await call('dy_odoo_import_template', {'model': config['model'], 'include_optional': True}, check=lambda r: 'company_id' in r['fields'] and 'code' in r['fields'])
                        await call('dy_odoo_import_preview', csv_args, check=lambda r: r['valid'] is True and r['total_rows'] == 1 and r['execution_authorized'] is False)

                async def real_configuration_writes():
                    """只写已批准模型；摘要均取自当前真实预览，旧值取自真实回读。"""
                    companies = await call('dy_odoo_record_search', {'model': 'res.company', 'kwargs': {'fields': ['id'], 'limit': 1, 'context': {'view_company': True}}}, check=lambda r: bool(r))
                    company = companies[0]['id']
                    model = 'dy.fin.account.analytic.plan'
                    # pytest 用例目录名跨运行相同，批次 UUID 避免与前次真实测试记录冲突。
                    key = uuid.uuid4().hex
                    args = {'environment': {'url': env['APP_URL'], 'database': env['DB_NAME']},
                            'model': model, 'company_id': company,
                            'values': {'name': '全工具真实创建验证', 'code': 'live_' + key, 'company_id': company},
                            'idempotency_key': 'live-real-create', 'confirmed': True}
                    blockers = ('模块未安装、版本不符或不明确', '创建缺少必填字段',
                                '服务用户没有配置操作权限', '线上配置字段缺失、只读或与白名单类型不符',
                                '目标公司不在当前服务用户的授权及当前公司范围',
                                '禁止使用绕过访问规则的超级用户')
                    preview = await call('dy_odoo_record_create', {**args, 'dry_run': True}, kind='真实创建前精确摘要预览', check=lambda r: r['status'] == 'confirmation_required')
                    created = await call('dy_odoo_record_create', {**args, 'dry_run': False, 'confirmation_digest': preview['confirmation_digest']},
                        kind='真实 Odoo 白名单配置创建；非业务验收', allowed_blockers=blockers,
                        check=lambda r: r['execution_status'] == 'succeeded' and r['data_readback_status'] == 'passed' and r['accepted'] is False)
                    if created is None:
                        matrix['dy_odoo_record_update'].append({'scenario': '真实创建后的更新链路', 'status': '依赖阻塞', 'reason': '前序创建被真实权限、模块或字段预检阻塞；未伪造记录', 'called': False})
                        matrix['dy_odoo_import_execute'].append({'scenario': '真实批量配置创建', 'status': '依赖阻塞', 'reason': '同一白名单模型创建前置条件不满足；未绕过预检', 'called': False})
                        persist()
                        return
                    row = (await call('dy_odoo_record_read', {'model': model, 'ids': [created['record_id']], 'fields': ['name', 'code', 'company_id', 'write_date']}, kind='真实创建后独立回读', check=lambda r: len(r) == 1 and r[0]['name'] == args['values']['name']))[0]
                    update = {**args, 'values': {'name': '全工具真实更新验证'}, 'record_id': row['id'],
                              'expected_values': {'name': row['name']}, 'expected_write_date': row['write_date'],
                              'idempotency_key': 'live-real-update'}
                    preview = await call('dy_odoo_record_update', {**update, 'dry_run': True}, kind='真实记录更新前精确摘要预览', check=lambda r: r['status'] == 'confirmation_required')
                    try:
                        await call('dy_odoo_record_update', {**update, 'dry_run': False, 'confirmation_digest': preview['confirmation_digest']}, kind='真实 Odoo 白名单配置更新', check=lambda r: r['execution_status'] == 'succeeded' and r['data_readback_status'] == 'passed' and r['accepted'] is False)
                        await call('dy_odoo_record_read', {'model': model, 'ids': [row['id']], 'fields': ['name']}, kind='真实更新后独立回读', check=lambda r: len(r) == 1 and r[0]['name'] == update['values']['name'])
                    except Exception as exc:
                        # 更新结果不明不得重放；仍验证使用独立业务键的创建型导入。
                        failures.append(f'真实配置更新: {type(exc).__name__}: {exc}')
                    imported = {'model': model, 'company_id': company,
                                'csv_data': f'name,code,company_id\n全工具真实导入验证,import_{key},{company}\n',
                                'environment': 'all-mcp-live', 'idempotency_key': 'live-real-import'}
                    preview = await call('dy_odoo_import_preview', imported, kind='真实导入字段与候选校验', check=lambda r: r['valid'] is True and bool(r['confirmation_digest']))
                    result = await call('dy_odoo_import_execute', {**imported, 'confirmed': True, 'dry_run': False, 'confirmation_digest': preview['confirmation_digest']}, kind='真实 Odoo 白名单配置 CSV 导入；非整批事务', check=lambda r: r['completed_rows'] == 1 and r['accepted'] is False and r['atomic'] is False)
                    await call('dy_odoo_record_read', {'model': model, 'ids': [result['results'][0]['record_id']], 'fields': ['code']}, kind='真实导入后独立回读', check=lambda r: len(r) == 1 and r[0]['code'] == 'import_' + key)
                    if not failures:
                        company_row = (await call('dy_odoo_record_read', {
                            'model': 'res.company', 'ids': [company], 'fields': ['name']},
                            kind='浏览器验收公司身份核对', check=lambda r: len(r) == 1))[0]
                        (output / 'configuration-browser-input.json').write_text(json.dumps({
                            'url': env['APP_URL'], 'database': env['DB_NAME'], 'company_id': company,
                            'company_name': company_row['name'], 'model': model, 'record_id': row['id'],
                            'expected_name': update['values']['name'], 'data_readback_status': 'passed',
                            'records': [
                                {'record_id': row['id'], 'expected_name': update['values']['name'],
                                 'expected_code': args['values']['code']},
                                {'record_id': result['results'][0]['record_id'], 'expected_name': '全工具真实导入验证',
                                 'expected_code': 'import_' + key}],
                            'browser_acceptance_status': 'not_checked'}, ensure_ascii=False, indent=2) + '\n')

                for label, scenario in (('真实只读', reads), ('知识与正向计划', knowledge_and_plan), ('本地生命周期', workspace_lifecycle), ('写入安全边界', write_boundaries)):
                    try:
                        await scenario()
                    except Exception as exc:
                        failures.append(f'{label}: {type(exc).__name__}: {exc}')
                write_env = {**env, 'DY_ODOO_CONFIG_WRITE_ENABLED': '1'}
                write_params = StdioServerParameters(command=sys.executable,
                    args=[str(PLUGIN / 'mcp-server/src/server.py')], cwd=str(workspace), env=write_env)
                async with stdio_client(write_params) as (write_reader, write_writer):
                    async with ClientSession(write_reader, write_writer) as write_session:
                        await write_session.initialize()
                        active_session = write_session
                        try:
                            await real_configuration_writes()
                        except Exception as exc:
                            failures.append(f'真实配置写入: {type(exc).__name__}: {exc}')
                missing = [name for name, entries in matrix.items() if not entries]
                assert not missing, f'覆盖矩阵缺项：{missing}；场景失败：{failures}'
                assert not failures, failures

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=360))
    finally:
        persist()
