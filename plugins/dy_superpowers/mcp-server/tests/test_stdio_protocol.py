"""使用官方 MCP 客户端验证插件服务的实际 stdio 协议。"""

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest


# 占位凭据值：测试断言该值绝不进入本地 trace 或 OTLP 载荷。
ODOO_TEST_PLACEHOLDER = 'value-must-not-leak-2605'


@pytest.mark.skipif(importlib.util.find_spec('mcp') is None, reason='需要 MCP SDK')
def test_stdio_initialize_discover_and_read_confirmed_memory(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError

    source = Path(__file__).parents[2]
    plugin = tmp_path / 'installed-plugin'
    shutil.copytree(source, plugin, ignore=shutil.ignore_patterns(
        '.venv', '.pytest_cache', '__pycache__', '*.egg-info'
    ))
    config = json.loads((plugin / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['dy-superpowers-odoo']
    assert config['command'] == 'uv'
    assert '--frozen' in config['args']
    assert config.get('cwd') is None
    workspace = tmp_path / 'project'
    workspace.mkdir()
    class UnavailableOdooHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_error(503)

        def log_message(self, format, *args):
            pass

    rpc = ThreadingHTTPServer(('127.0.0.1', 0), UnavailableOdooHandler)
    rpc_thread = Thread(target=rpc.serve_forever, daemon=True)
    rpc_thread.start()
    origin = f'http://127.0.0.1:{rpc.server_address[1]}'
    env = {**os.environ, 'APP_URL': origin, 'APP_URL': origin,
           'DB_NAME': 'db', 'APP_USER': 'u', 'APP_PASSWORD': 'p', 'CODEX_HOME': str(tmp_path / 'codex')}
    env.pop('VIRTUAL_ENV', None)
    params = StdioServerParameters(
        command=config['command'],
        args=[arg.replace('${PLUGIN_ROOT}', str(plugin)) for arg in config['args']],
        env=env,
        cwd=str(workspace),
    )

    async def exercise():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert 'dy_memory_search' in {tool.name for tool in tools.tools}
                schemas = {tool.name: tool.model_dump(by_alias=True)["inputSchema"] for tool in tools.tools}
                assert 'expected_content' in schemas['dy_memory_write']['properties']
                assert 'scope' in schemas['dy_module_memory_read']['properties']
                assert 'scope_id' in schemas['dy_module_memory_read']['properties']
                assert 'expected_revision' in schemas['dy_research_record_write']['properties']
                assert 'dy_inspection_validate' in schemas
                initialized = await session.call_tool(
                    'dy_session_initialize', {'trace_id': 'protocol-start', 'source': 'resume'}
                )
                assert initialized.model_dump(by_alias=True)["isError"] is False
                assert not (workspace / '.dy_superpowers').exists()
                written = await session.call_tool('dy_memory_write', {
                    'trace_id': 'protocol-write', 'name': 'profile.md',
                    'content': '协议测试记忆', 'confirmed': True,
                })
                assert written.model_dump(by_alias=True)["isError"] is False
                read = await session.call_tool('dy_memory_read', {
                    'trace_id': 'protocol-read', 'name': 'profile.md',
                })
                assert read.model_dump(by_alias=True)["isError"] is False
                assert read.content[0].text == '协议测试记忆'
                with pytest.raises(MCPError, match='冲突') as conflict:
                    await session.call_tool('dy_memory_write', {
                        'trace_id': 'protocol-conflict', 'name': 'profile.md',
                        'content': '过时覆盖', 'confirmed': True, 'expected_content': '旧快照',
                    })
                assert conflict.value.code == -32602
                with pytest.raises(MCPError) as unavailable:
                    await session.call_tool('dy_odoo_record_search', {'trace_id': 'rpc-failure', 'model': 'account.move'})
                assert unavailable.value.code == -32603
                updated = await session.call_tool('dy_memory_write', {
                    'trace_id': 'protocol-update', 'name': 'profile.md',
                    'content': '协议测试记忆（修订）', 'confirmed': True,
                    'expected_content': '协议测试记忆',
                })
                assert updated.model_dump(by_alias=True)["isError"] is False
                research_args = {
                    'trace_id': 'protocol-research', 'module': 'expense', 'check_order': '1',
                    'research_id': '01', 'config_item': '应用安装', 'scope': '公司A/本期',
                    'conclusion': '协议调研确认', 'confirmed': True, 'confirmed_by': '顾问',
                    'confirmed_at': '2026-10-21',
                }
                await session.call_tool('dy_research_record_write', research_args)
                research = await session.call_tool('dy_module_memory_read', {
                    'trace_id': 'protocol-research-read', 'module': 'expense', 'check_order': '1', 'research_id': '01',
                })
                current = json.loads(research.content[0].text)['records'][0]
                with pytest.raises(MCPError, match='expected_revision'):
                    await session.call_tool('dy_research_record_write', research_args)
                retired = await session.call_tool('dy_research_record_write', {
                    **research_args, 'scope_id': current['scope_id'], 'expected_revision': current['revision'],
                    'status': 'retired', 'retirement_reason': '协议测试停用',
                })
                assert json.loads(retired.content[0].text)['status'] == 'retired'
                validation = await session.call_tool('dy_inspection_validate', {
                    'trace_id': 'protocol-inspection', 'report': {'expected_checks': [], 'records': []},
                })
                assert json.loads(validation.content[0].text)['valid'] is False

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    finally:
        rpc.shutdown()
        rpc.server_close()
        rpc_thread.join(timeout=2)
    assert (workspace / '.dy_superpowers/profile.md').read_text(encoding='utf-8') == '协议测试记忆（修订）'
    assert not (plugin / '.dy_superpowers').exists()
    events = [json.loads(line) for path in (tmp_path / 'codex').rglob('*.jsonl')
              for line in path.read_text(encoding='utf-8').splitlines()]
    assert events
    assert '协议测试记忆' in json.dumps(events, ensure_ascii=False)
    assert '协议调研确认' in json.dumps(events, ensure_ascii=False)


@pytest.mark.skipif(importlib.util.find_spec('mcp') is None, reason='需要 MCP SDK')
def test_stdio_odoo_read_succeeds_over_jsonrpc_and_records_redacted_trace(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    source = Path(__file__).parents[2]
    plugin = tmp_path / 'installed-plugin'
    shutil.copytree(source, plugin, ignore=shutil.ignore_patterns(
        '.venv', '.pytest_cache', '__pycache__', '*.egg-info'
    ))
    config = json.loads((plugin / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['dy-superpowers-odoo']
    workspace = tmp_path / 'project'
    workspace.mkdir()
    rpc_calls = []

    class OdooHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request_body = self.rfile.read(int(self.headers['Content-Length']))
            request = json.loads(request_body)
            params = request['params']
            if self.path == '/jsonrpc' and params['service'] == 'common' and params['method'] == 'login':
                rpc_calls.append(request)
                result = 17
            elif self.path == '/jsonrpc' and params['service'] == 'object' and params['method'] == 'execute_kw':
                rpc_calls.append(request)
                result = [{'name': 'dy_fin_acc', 'state': 'installed'}]
            else:
                self.send_error(404)
                return
            response = json.dumps({
                'jsonrpc': '2.0', 'id': request['id'], 'result': result,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    rpc = ThreadingHTTPServer(('127.0.0.1', 0), OdooHandler)
    rpc_thread = Thread(target=rpc.serve_forever, daemon=True)
    rpc_thread.start()
    origin = f'http://127.0.0.1:{rpc.server_port}'
    codex_home = tmp_path / 'codex'
    env = {
        **os.environ,
        'APP_URL': origin,
        'APP_URL': origin,
        'DB_NAME': 'test-db',
        'APP_USER': 'consultant',
        'APP_PASSWORD': ODOO_TEST_PLACEHOLDER,
        'CODEX_HOME': str(codex_home),
    }
    env.pop('VIRTUAL_ENV', None)
    params = StdioServerParameters(
        command=config['command'],
        args=[arg.replace('${PLUGIN_ROOT}', str(plugin)) for arg in config['args']],
        env=env,
        cwd=str(workspace),
    )

    async def exercise():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool('dy_odoo_module_list_installed', {
                    'trace_id': 'odoo-success', 'offset': 0,
                })
                assert result.model_dump(by_alias=True)['isError'] is False
                assert json.loads(result.content[0].text) == [
                    {'name': 'dy_fin_acc', 'state': 'installed'},
                ]
                trace = await session.call_tool('dy_trace_read', {'trace_id': 'odoo-success'})
                events = json.loads(trace.content[0].text)
                event = next(event for event in events if event.get('operation') == 'module_list_installed')
                assert event['workspace_id'] == hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
                assert event['result_count'] == 1
                assert event['limit'] == 100
                assert ODOO_TEST_PLACEHOLDER not in json.dumps(events)
                searched = await session.call_tool('dy_odoo_record_search', {
                    'trace_id': 'odoo-domain-success', 'model': 'res.partner',
                    'args': [[['state', '=', 'posted']]],
                    'kwargs': {'fields': ['name'], 'limit': 10, 'offset': 3},
                })
                assert searched.model_dump(by_alias=True)['isError'] is False
                query_trace = await session.call_tool('dy_trace_read', {'trace_id': 'odoo-domain-success'})
                query_events = json.loads(query_trace.content[0].text)
                query_event = next(event for event in query_events if event.get('event') == 'odoo.read')
                assert json.loads(query_event['domain']) == [['state', '=', 'posted']]
                memory_body = '可追踪的工作区记忆内容'
                written = await session.call_tool('dy_memory_write', {
                    'trace_id': 'memory-content-success', 'name': 'profile.md',
                    'content': memory_body, 'confirmed': True,
                })
                assert written.model_dump(by_alias=True)['isError'] is False
                read = await session.call_tool('dy_memory_read', {
                    'trace_id': 'memory-content-success', 'name': 'profile.md',
                })
                assert read.content[0].text == memory_body

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    finally:
        rpc.shutdown()
        rpc.server_close()
        rpc_thread.join(timeout=2)

    assert len(rpc_calls) == 3
    assert all(call['jsonrpc'] == '2.0' and call['method'] == 'call' for call in rpc_calls)
    assert [call['params']['service'] for call in rpc_calls] == ['common', 'object', 'object']
    assert rpc_calls[0]['params']['method'] == 'login'
    assert rpc_calls[0]['params']['args'] == ['test-db', 'consultant', ODOO_TEST_PLACEHOLDER]
    object_call = rpc_calls[1]['params']
    assert object_call['method'] == 'execute_kw'
    assert object_call['args'][:5] == ['test-db', 17, ODOO_TEST_PLACEHOLDER, 'ir.module.module', 'search_read']
    assert object_call['args'][5] == [[['state', '=', 'installed']]]
    assert object_call['args'][6] == {
        'fields': ['name', 'shortdesc', 'latest_version', 'state', 'dependencies_id'],
        'limit': 100,
        'offset': 0,
    }
    search_call = rpc_calls[2]['params']['args']
    assert search_call[3:5] == ['res.partner', 'search_read']
    assert search_call[5] == [[['state', '=', 'posted']]]
