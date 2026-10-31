#!/usr/bin/env python3
"""通过真实 MCP 检查业务短词检索及第一条源码完整回读。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from evaluate_knowledge_mcp import ROOT, read_reference


async def run():
    with tempfile.TemporaryDirectory(prefix='dy-business-search-') as workspace:
        env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        env.update(PYTHONPATH=str(ROOT / 'plugins/dy_superpowers/mcp-server/src'),
                   DY_WORKSPACE_ROOT=workspace, CODEX_HOME=workspace + '/codex')
        program = ('from dy_superpowers_mcp import knowledge; '
                   'knowledge._source_service().idle_seconds=1; import server; server.main()')
        params = StdioServerParameters(command=sys.executable, args=['-c', program], cwd=workspace, env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(tool, arguments):
                    reply = await session.call_tool(tool, {'trace_id': 'business-lexical-eval', **arguments})
                    if reply.is_error:
                        raise ValueError(f'MCP 工具失败：{tool}: {reply.content}')
                    return json.loads(reply.content[0].text)

                rows = []
                for query in ('移动报销', 'OCR', '微信导入', '微信卡包发票导入'):
                    result = await call('dy_knowledge_search', {'query': query, 'limit': 5})
                    sources = [row for row in result['results'] if row['knowledge_type'] == 'source']
                    evidence = await read_reference(call, sources[0]['reference'], '') if sources else None
                    rows.append({'query': query, 'result': result, 'first_source_read': evidence})
                return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    for row in rows:
        print(row['query'], row['result']['search_terms'], row['result']['total_count'])
        print([item.get('path', item['reference']) for item in row['result']['results']])


if __name__ == '__main__':
    main()
