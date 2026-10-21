"""使用官方 MCP 客户端验证插件服务的实际 stdio 协议。"""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(importlib.util.find_spec('mcp') is None, reason='需要 MCP SDK')
def test_stdio_initialize_discover_and_read_confirmed_memory(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    plugin = Path(__file__).parents[2]
    workspace = tmp_path / 'project'
    workspace.mkdir()
    env = {**os.environ, 'OTEL_SDK_DISABLED': 'true', 'CODEX_HOME': str(tmp_path / 'codex')}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(plugin / 'mcp-server/src/server.py')],
        env=env,
        cwd=str(workspace),
    )

    async def exercise():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert 'dy_memory_search' in {tool.name for tool in tools.tools}
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

    asyncio.run(asyncio.wait_for(exercise(), timeout=20))
    assert (workspace / '.dy_superpowers/profile.md').read_text(encoding='utf-8') == '协议测试记忆'
    events = [json.loads(line) for path in (tmp_path / 'codex').rglob('*.jsonl')
              for line in path.read_text(encoding='utf-8').splitlines()]
    assert events
    assert '协议测试记忆' not in json.dumps(events, ensure_ascii=False)
