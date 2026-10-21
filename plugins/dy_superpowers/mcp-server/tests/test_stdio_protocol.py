"""使用官方 MCP 客户端验证插件服务的实际 stdio 协议。"""

import asyncio
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest


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
    env = {**os.environ, 'OTEL_SDK_DISABLED': 'true', 'CODEX_HOME': str(tmp_path / 'codex')}
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
                assert 'scope' in schemas['dy_research_record_read']['properties']
                assert 'scope_id' in schemas['dy_research_record_read']['properties']
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
                with pytest.raises(MCPError, match='冲突'):
                    await session.call_tool('dy_memory_write', {
                        'trace_id': 'protocol-conflict', 'name': 'profile.md',
                        'content': '过时覆盖', 'confirmed': True, 'expected_content': '旧快照',
                    })
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
                research = await session.call_tool('dy_research_record_read', {
                    'trace_id': 'protocol-research-read', 'module': 'expense', 'check_order': '1', 'research_id': '01',
                })
                current = json.loads(research.content[0].text)
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

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    assert (workspace / '.dy_superpowers/profile.md').read_text(encoding='utf-8') == '协议测试记忆（修订）'
    assert not (plugin / '.dy_superpowers').exists()
    events = [json.loads(line) for path in (tmp_path / 'codex').rglob('*.jsonl')
              for line in path.read_text(encoding='utf-8').splitlines()]
    assert events
    assert '协议测试记忆' not in json.dumps(events, ensure_ascii=False)
    assert '协议调研确认' not in json.dumps(events, ensure_ascii=False)
