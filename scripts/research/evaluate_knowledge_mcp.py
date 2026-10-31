#!/usr/bin/env python3
"""通过真实 MCP stdio 会话，用原始调研问题评测检索和引用回读。"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from evaluate_knowledge_retrieval import ROOT, evaluate, validate_cases


async def read_reference(call, reference, anchor):
    offset, chunks, pages = 0, [], 0
    while True:
        page = await call('dy_knowledge_read', {'reference': reference, 'offset': offset, 'limit': 16000})
        if not page['found'] or page['offset'] != offset:
            raise ValueError('引用回读不存在或偏移不一致')
        chunks.append(page['content'])
        pages += 1
        end = offset + len(page['content'])
        if page['complete']:
            if end != page['total_length'] or page['next_offset'] is not None:
                raise ValueError('引用回读完整性标志不一致')
            break
        if page['next_offset'] != end or end <= offset:
            raise ValueError('引用回读分页没有前进或丢失内容')
        offset = end
    text = ''.join(chunks)
    return {'pages': pages, 'characters': len(text),
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'anchor_found': anchor in text}


async def run(data, rounds):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory(prefix='dy-research-mcp-') as workspace:
        env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        env.update(PYTHONPATH=str(ROOT / 'plugins/dy_superpowers/mcp-server/src'),
                   DY_WORKSPACE_ROOT=workspace, CODEX_HOME=workspace + '/codex')
        program = ('from dy_superpowers_mcp import knowledge; '
                   'knowledge._source_service().idle_seconds=1; import server; server.main()')
        params = StdioServerParameters(command=sys.executable, args=['-c', program], cwd=workspace, env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                available = {tool.name for tool in (await session.list_tools()).tools}
                assert {'dy_knowledge_search', 'dy_knowledge_read'} <= available
                counts = {}
                coverages = []

                async def call(tool, arguments):
                    counts[tool] = counts.get(tool, 0) + 1
                    # optional 参数在 MCP schema 中必须省略，不能传 null。
                    arguments = {key: value for key, value in arguments.items() if value is not None}
                    reply = await session.call_tool(tool, {'trace_id': 'research-retrieval-eval', **arguments})
                    if reply.is_error:
                        raise ValueError(f'MCP 工具失败：{tool}: {reply.content}')
                    payload = json.loads(reply.content[0].text)
                    if tool == 'dy_knowledge_search':
                        coverages.append(payload['coverage'])
                    return payload

                loop = asyncio.get_running_loop()

                def search(query, **filters):
                    return asyncio.run_coroutine_threadsafe(
                        call('dy_knowledge_search', {'query': query, **filters}), loop).result(timeout=60)

                result = await asyncio.to_thread(evaluate, data, rounds, search, SimpleNamespace(bundle_id=None))
                # 只对限定模式前10命中的预先标注目标读取完整内容，不构造隐藏文件引用。
                audit = json.loads((ROOT / 'docs/research-sources/zoekt/hrp-20261030-excluded/content-sha256.json').read_text())
                reads, cache = [], {}
                for row in result['rows']:
                    if row['run'] != 1 or row['mode'] != 'keyword_source_scoped' or row['metrics'] is None:
                        continue
                    case = next(case for case in data['cases'] if case['id'] == row['id'])
                    for key in case['targets']:
                        target = data['targets'][key]
                        if target['path'] not in row['ranked_top10']:
                            reads.append({'id': case['id'], 'path': target['path'], 'status': 'not_retrieved'})
                            continue
                        rank = row['ranked_top10'].index(target['path'])
                        reference = row['references_top10'][rank]
                        # 不同问题的同文件不同锚点分别核查。
                        identity = (reference, target['anchor'])
                        if identity not in cache:
                            cache[identity] = await read_reference(call, reference, target['anchor'])
                        evidence = cache[identity]
                        matched = evidence['sha256'] == audit[target['path']]
                        reads.append({'id': case['id'], 'path': target['path'], 'rank': rank + 1,
                                      'status': 'passed' if matched and evidence['anchor_found'] else 'failed',
                                      'snapshot_matches': matched, **evidence})
                result.update(bundle_id=coverages[0]['bundle_id'], transport='mcp_stdio', tool_calls=counts, read_evidence=reads,
                              coverage=coverages[0], excluded=data.get('excluded', []))
                return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'docs/research-sources/zoekt/research-question-cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3, choices=range(1, 11))
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    data = json.loads(raw)
    validate_cases(data)
    start = time.monotonic()
    result = asyncio.run(run(data, args.rounds))
    result.update(cases_sha256=hashlib.sha256(raw).hexdigest(), elapsed_seconds=round(time.monotonic() - start, 3))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in {'rows', 'read_evidence', 'excluded'}},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
