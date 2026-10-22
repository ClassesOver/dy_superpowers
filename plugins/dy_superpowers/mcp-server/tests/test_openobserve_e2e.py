"""显式启用后，通过真实 MCP stdio 与 OpenObserve 验证遥测。"""

import asyncio
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests


pytestmark = pytest.mark.skipif(
    not os.environ.get('DY_TEST_OPENOBSERVE_URL'), reason='需要显式配置 OpenObserve 端测地址',
)
PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / 'mcp-server' / 'src'))


def _validated_port() -> int:
    """端测目标边界：仅接受显式配置的本机回环 http 地址，返回其端口用于字面量主机 URL。"""
    parsed = urlparse(os.environ['DY_TEST_OPENOBSERVE_URL'])
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.port:
        pytest.fail('DY_TEST_OPENOBSERVE_URL 必须是 http://127.0.0.1:<端口> 形式的本机地址')
    return parsed.port


def _http():
    session = requests.Session()
    session.trust_env = False
    session.auth = (os.environ['DY_TEST_OPENOBSERVE_USER'], os.environ['DY_TEST_OPENOBSERVE_PASSWORD'])
    return session


def _query_workspace(workspace, expected_tools, expected_calls=0):
    workspace_id = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    port = _validated_port()
    deadline = time.monotonic() + 30
    with _http() as http:
        while time.monotonic() < deadline:
            response = http.post(f'http://127.0.0.1:{port}/api/default/_search',
                                 params={'type': 'traces'}, json={'query': {
                                     'sql': f'SELECT * FROM "dy_superpowers" WHERE dy_workspace_id = \'{workspace_id}\' ORDER BY _timestamp DESC',
                                     'start_time': int((time.time() - 600) * 1_000_000),
                                     'end_time': int(time.time() * 1_000_000), 'from': 0, 'size': 100,
                                 }}, timeout=5, allow_redirects=False)
            if response.ok:
                rows = response.json().get('hits', [])
                roots = [row for row in rows if row.get('dy_span_role') == 'tool']
                if len(roots) >= expected_calls and expected_tools <= {row.get('dy_tool') for row in roots}:
                    return rows
            time.sleep(0.5)
    pytest.fail('OpenObserve 未按工作区查询到 trace（请检查 collector 日志和数据流 schema）')


def _stdio(workspace, codex_home):
    from mcp import StdioServerParameters

    config = json.loads((PLUGIN / '.mcp.json').read_text())['mcpServers']['dy-superpowers-odoo']
    env = dict(os.environ, CODEX_HOME=str(codex_home), OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='http://127.0.0.1:33049/v1/traces',
               OTEL_BSP_SCHEDULE_DELAY='100')
    for key in ('VIRTUAL_ENV', 'DY_WORKSPACE_ROOT', 'OTEL_SDK_DISABLED'):
        env.pop(key, None)
    return StdioServerParameters(command=config['command'],
                                 args=[arg.replace('${PLUGIN_ROOT}', str(PLUGIN)) for arg in config['args']],
                                 env=env, cwd=str(workspace))


def test_openobserve_service_health():
    port = _validated_port()
    response = requests.get(f'http://127.0.0.1:{port}/healthz', timeout=5, allow_redirects=False)
    assert response.status_code == 200
    with _http() as http:
        response = http.get(f'http://127.0.0.1:{port}/api/default/streams', timeout=5, allow_redirects=False)
        assert response.status_code == 200
        assert isinstance(response.json()['list'], list)


def test_stdio_memory_and_research_content_reaches_openobserve(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    workspace = tmp_path / 'memory-project'
    workspace.mkdir()
    body = '# 项目背景\n\n端测记忆正文：本期上线费用模块\npassword=e2e-secret-must-be-redacted\n'

    async def exercise():
        from mcp.shared.exceptions import MCPError

        async with stdio_client(_stdio(workspace, tmp_path / 'codex')) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                async def call(tool_name, **arguments):
                    result = await session.call_tool(tool_name, dict(trace_id='openobserve-memory-e2e', **arguments))
                    assert not result.model_dump(by_alias=True)['isError'], tool_name
                    return result.content[0].text
                await call('dy_session_initialize', source='resume')
                await call('dy_memory_write', name='profile.md', content=body, confirmed=True)
                assert await call('dy_memory_read', name='profile.md') == body
                searched = json.loads(await call('dy_memory_search', query='端测记忆正文'))
                assert searched['backend'] == 'basic-memory' and searched['results']
                await call('dy_research_record_write', module='expense', check_order='1', research_id='01',
                           config_item='应用安装', scope='端测公司/本期', conclusion='端测调研正文：本期上线',
                           confirmed=True, confirmed_by='e2e-private-consultant', confirmed_at='2026-10-22')
                record_result = json.loads(await call('dy_module_memory_read', module='expense', check_order='1', research_id='01'))
                assert record_result['found'] and len(record_result['records']) == 1
                record = record_result['records'][0]
                assert record['conclusion'] == '端测调研正文：本期上线'
                assert await call('dy_memory_read', name='profile.md', trace_context={
                    'skill': 'expense-inspection', 'module': 'expense', 'check_order': '1', 'research_id': '01',
                    'scope_id': record['scope_id'], 'revision': record['revision'],
                }) == body
                with pytest.raises(MCPError) as failure:
                    await session.call_tool('dy_memory_write', {
                        'trace_id': 'openobserve-memory-e2e', 'name': 'profile.md', 'content': '不应写入', 'confirmed': False,
                    })
                assert failure.value.code == -32602
                gate = json.loads(await call('dy_inspection_validate', report={}))
                assert gate['valid'] is False and gate['gate'] == '待确认'
                events = json.loads(await call('dy_trace_read'))
                payload = json.dumps(events, ensure_ascii=False)
                assert '端测记忆正文' in payload and '端测调研正文' in payload
                assert 'e2e-secret-must-be-redacted' not in payload
                assert 'e2e-private-consultant' not in payload
    asyncio.run(asyncio.wait_for(exercise(), timeout=120))
    rows = _query_workspace(workspace, {'dy_memory_read', 'dy_memory_write', 'dy_memory_search',
                                        'dy_module_memory_read', 'dy_research_record_write', 'dy_inspection_validate'}, expected_calls=10)
    payload = json.dumps(rows, ensure_ascii=False)
    assert '端测记忆正文' in payload and '端测调研正文' in payload
    assert 'memory.read' in payload and 'research.read' in payload
    assert 'e2e-secret-must-be-redacted' not in payload
    assert 'e2e-private-consultant' not in payload
    assert '[REDACTED]' in payload
    roots = [row for row in rows if row.get('dy_span_role') == 'tool']
    assert len({row['dy_workflow_id'] for row in roots}) == 1
    assert len({row['trace_id'] for row in roots}) == len(roots)
    assert all(row['operation_name'] == row['dy_tool'] for row in roots)
    assert all(row.get('dy_status') in {'ok', 'error'} for row in roots)
    assert any(row.get('dy_status') == 'error' and str(row.get('dy_error_code')) == '-32602' for row in roots)
    research = next(row for row in roots if row['dy_tool'] == 'dy_research_record_write')
    assert research['dy_check_key'] == 'expense:1:01' and research['dy_revision'] and research['dy_scope_id']
    linked_memory = next(row for row in roots if row['dy_tool'] == 'dy_memory_read' and row.get('dy_check_key'))
    assert linked_memory['dy_revision'] == research['dy_revision']
    assert linked_memory['dy_scope_id'] == research['dy_scope_id']
    gate = next(row for row in roots if row['dy_tool'] == 'dy_inspection_validate')
    assert gate['dy_status'] == 'ok' and gate['dy_result'] == '待确认'
    assert str(gate['dy_valid']).lower() == 'false'
    assert all(row['span_status'] in {'OK', 'ERROR'} for row in roots)
    print('OpenObserve 记忆端测：', len(rows), '条 span；trace：', rows[0]['trace_id'])


@pytest.mark.skipif(not all(os.environ.get(key) for key in (
    'ODOO_URL', 'DY_ODOO_ALLOWED_ORIGINS', 'ODOO_DB', 'ODOO_USERNAME', 'ODOO_PASSWORD',
)), reason='需要显式配置真实只读 Odoo 环境')
def test_stdio_real_odoo_domain_reaches_openobserve(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    workspace = tmp_path / 'odoo-project'
    workspace.mkdir()
    domain = [['name', 'ilike', 'expense'], ['state', '=', 'installed']]
    scope = '真实 Odoo 账套/当前实施范围'
    environment = 'odoo-test'
    trace_id = 'openobserve-odoo-inspection-e2e'
    research = {}
    query_rows = []

    async def exercise():
        async with stdio_client(_stdio(workspace, tmp_path / 'codex')) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                written = await session.call_tool('dy_research_record_write', {
                    'trace_id': trace_id, 'module': 'expense', 'check_order': '1', 'research_id': '01',
                    'config_item': '应用安装', 'scope': scope,
                    'conclusion': '本期费用基础模块需要安装并启用',
                    'evidence_ref': '真实端测场景确认', 'confirmed': True,
                    'confirmed_by': '端测确认人', 'confirmed_at': datetime.date.today().isoformat(),
                })
                assert not written.model_dump(by_alias=True)['isError']
                research.update(json.loads(written.content[0].text))
                context = {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '1',
                           'research_id': '01', 'scope_id': research['scope_id'],
                           'revision': research['revision'], 'environment': environment}
                result = await session.call_tool('dy_odoo_record_search', {
                    'trace_id': trace_id, 'model': 'ir.module.module', 'args': [domain],
                    'kwargs': {'fields': ['name', 'state'], 'limit': 20}, 'trace_context': context,
                })
                assert not result.model_dump(by_alias=True)['isError']
                query_rows.extend(json.loads(result.content[0].text))
                assert query_rows and all(row['state'] == 'installed' for row in query_rows)
                assert all('expense' in row['name'].lower() for row in query_rows)
                from dy_superpowers_mcp.inspection import _matrix
                matrix_revision, standards = _matrix('expense')
                expected_checks = []
                report_records = []
                for (check_order, research_id), acceptance_standard in standards.items():
                    key = {'module': 'expense', 'check_order': check_order, 'research_id': research_id,
                           'scope_id': research['scope_id'], 'environment': environment}
                    expected_checks.append(key)
                    is_queried_check = (check_order, research_id) == ('1', '01')
                    report_records.append({
                        **key,
                        'record_id': f"ODOO-E2E-EXPENSE-{check_order}-{research_id}",
                        'research_revision': research['revision'] if is_queried_check else '',
                        'matrix_revision': matrix_revision, 'acceptance_standard': acceptance_standard,
                        'evidence_refs': ([{'kind': 'odoo', 'reference': '真实端测费用模块查询',
                                            'trace_id': trace_id, 'model': 'ir.module.module'}]
                                          if is_queried_check else []),
                        'verification_method': 'Odoo 12 只读 JSON-RPC 查询' if is_queried_check else '端测未覆盖',
                        'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'result': '通过' if is_queried_check else '未检查',
                        'reason': '真实 Odoo 查询返回费用相关已安装模块' if is_queried_check else '真实环境端测只覆盖模块查询',
                    })
                report = {'expected_checks': expected_checks, 'records': report_records}
                gate = await session.call_tool('dy_inspection_validate', {
                    'trace_id': trace_id, 'report': report,
                })
                assert not gate.model_dump(by_alias=True)['isError']
                gate_value = json.loads(gate.content[0].text)
                assert gate_value['valid'] is True and gate_value['gate'] == '待确认'
                assert gate_value['execution_authorized'] is False
    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    rows = _query_workspace(workspace, {'dy_research_record_write', 'dy_odoo_record_search', 'dy_inspection_validate'}, expected_calls=3)
    payload = json.dumps(rows, ensure_ascii=False)
    assert 'dy.domain' in payload and 'installed' in payload
    roots = [row for row in rows if row.get('dy_span_role') == 'tool']
    root = next(row for row in roots if row['dy_tool'] == 'dy_odoo_record_search')
    assert root['dy_model'] == 'ir.module.module'
    assert json.loads(root['dy_domain']) == domain
    assert root['dy_status'] == 'ok'
    assert root['dy_check_key'] == 'expense:1:01'
    assert root['dy_scope_id'] == research['scope_id'] and root['dy_revision'] == research['revision']
    gate = next(row for row in roots if row['dy_tool'] == 'dy_inspection_validate')
    assert gate['dy_status'] == 'ok' and gate['dy_result'] == '待确认'
    assert str(gate['dy_valid']).lower() == 'true'
    assert len({row['dy_workflow_id'] for row in roots}) == 1
    assert os.environ['ODOO_PASSWORD'] not in payload
    print('OpenObserve Odoo 端测 trace：', rows[0]['trace_id'])
