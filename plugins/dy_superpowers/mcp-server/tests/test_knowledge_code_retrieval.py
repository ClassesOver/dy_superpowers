"""以只读 HRP 源码作为独立答案，验证真实索引的代码检索与完整回读。"""
import ast
import asyncio
import base64
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

import pytest

from dy_superpowers_mcp import knowledge
from dy_superpowers_mcp.zoekt_service import ZoektService


PLUGIN = Path(__file__).resolve().parents[2]
AUDIT = PLUGIN.parents[1] / 'docs/research-sources/zoekt/hrp-20261030-excluded'
SOURCE = Path(os.environ.get('DY_HRP_SOURCE_ROOT', '/home/junge/pros/dypowers/data'))
MANIFESTS = sorted(path for path in json.loads((AUDIT / 'content-sha256.json').read_text())
                   if path.endswith('/__manifest__.py'))
REPOSITORIES = sorted({path.split('/')[0] for path in MANIFESTS})
SUMMARY_MODULES = sorted(name for name, info in knowledge.MODULES.items() if 'source_repo' in info)


@pytest.fixture(scope='module')
def corpus():
    if not SOURCE.is_dir():
        pytest.skip('需要只读 HRP 源码目录 DY_HRP_SOURCE_ROOT，不能用索引自身生成预期答案')
    conversions = {row['path']: row['original_encoding'] for row in (
        json.loads(line) for line in (AUDIT / 'encoding-conversions.jsonl').read_text().splitlines())}
    texts = {}
    for path, digest in json.loads((AUDIT / 'content-sha256.json').read_text()).items():
        content = (SOURCE / path).read_bytes()
        if path in conversions:
            content = content.decode(conversions[path]).encode('utf-8')
        assert hashlib.sha256(content).hexdigest() == digest, path
        texts[path] = content.decode('utf-8')
    return texts


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    service = ZoektService(knowledge.INDEX_DIR, knowledge.BINARY_PATH,
                          runtime_root=tmp_path_factory.mktemp('knowledge-native'), idle_seconds=1)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(knowledge, '_source_service', lambda: service)
        yield service
    end = time.monotonic() + 8
    while service.state_path.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert not service.state_path.exists(), '测试结束后原生共享服务应空闲退出'


def expected_paths(corpus, query, module=None):
    return {path for path, text in corpus.items()
            if (module is None or (len(path.split('/')) > 2 and path.split('/')[1] == module))
            and (query.casefold() in text.casefold() or query.casefold() in path.casefold())}


def check_search(corpus, query, module=None):
    expected = expected_paths(corpus, query, module)
    result = knowledge.search(query, module=module, knowledge_type='source', limit=50)
    actual = {row['path'] for row in result['results']}
    assert actual <= expected, (query, sorted(actual - expected))
    assert len(actual) == result['count'], query
    assert result['total_known'] is True, query
    assert result['total_count'] == len(expected), query
    assert result['count'] == min(50, len(expected)), query
    assert result['truncated'] is (len(expected) > 50), query
    if len(expected) <= 50:
        assert actual == expected, (query, sorted(expected - actual))
    for row in result['results']:
        parts = row['path'].split('/')
        assert row['module'] == (parts[1] if len(parts) > 2 else None)
        assert row['fact_type'] == 'source_text'
        assert row['namespace'] == 'hrp'
        if row['line']:
            assert query.casefold() in corpus[row['path']].splitlines()[row['line'] - 1].casefold()
        else:
            assert query.casefold() in row['path'].casefold()
            assert row['summary'] == row['path'][:240]
    return result


@pytest.mark.parametrize('manifest', MANIFESTS, ids=lambda path: path.split('/')[1])
def test_every_module_business_name_matches_independent_source_scan(corpus, native, manifest):
    """覆盖每个业务模块；不仅检查 manifest 可读，还核对检索的全部文件命中。"""
    module = manifest.split('/')[1]
    query = ast.literal_eval(corpus[manifest])['name'].strip()
    assert manifest in expected_paths(corpus, query, module)
    check_search(corpus, query, module)


@pytest.mark.parametrize('repository', REPOSITORIES)
def test_model_declaration_from_every_repository_is_retrievable_across_modules(corpus, native, repository):
    declarations = []
    for path, text in corpus.items():
        if path.startswith(repository + '/') and path.endswith('.py'):
            declarations.extend((name, path) for name in re.findall(
                r'''(?m)^\s+_name\s*=\s*['"](dy\.[^'"]+)['"]''', text))
    assert declarations, repository
    query, path = sorted(declarations)[0]
    assert path in expected_paths(corpus, query)
    check_search(corpus, query)


@pytest.mark.parametrize('module', SUMMARY_MODULES)
def test_curated_module_hints_do_not_drift_from_packaged_source(corpus, native, module):
    """摘要不能冒充语义索引，但其中的依赖、模型提示仍须与当前源码一致。"""
    manifests = [path for path in MANIFESTS if path.split('/')[1] == module]
    assert len(manifests) == 1, module
    manifest = ast.literal_eval(corpus[manifests[0]])
    info = knowledge.module_info(module)
    assert info['fact_type'] == 'curated_interpretation'
    assert info['semantic_indexing'] is False
    assert info['indexed_models'] is None and info['declared_dependencies'] is None
    assert info['version_match'] == 'not_checked'
    assert info['depends'] == manifest.get('depends', []), module
    python_texts = [text for path, text in corpus.items()
                    if len(path.split('/')) > 2 and path.split('/')[1] == module and path.endswith('.py')]
    for model in info['models']:
        assert any(model in text for text in python_texts), (module, model)


@pytest.mark.parametrize('query,module', [
    ('dy.expense.document.type', 'dy_expense'),
    ('_inherit', 'dy_bank_expense'),
    ('fields.Many2one', 'dy_fin_mdm'),
    ('fields.Many2many', 'dy_expense'),
    ('fields.One2many', 'dy_expense'),
    ('@api.constrains', 'dy_expense'),
    ('_sql_constraints', 'dy_expense'),
    ('@api.depends', 'dy_fin_mdm'),
    ('_compute_sequence', 'dy_fin_mdm'),
    ('domain_force', 'dy_expense'),
    ('perm_unlink', 'dy_expense'),
    ('<menuitem', 'dy_expense'),
    ('ir.actions.act_window', 'dy_fin_mdm'),
    ('odoo.define', 'dy_expense'),
    ('t-name', 'dy_expense'),
    ('报销', 'dy_expense'),
    ('dy.expense.report.payment', None),
    ('dy.fin.account.period', None),
    ('res.company', None),
    ('case:yes', 'dy_expense'),
    ('file:.* OR repo:.*', None),
    ('foo" repo:hrp', None),
    ('missing_symbol[.*?]$', None),
    ('missing_symbol\\(x\\)', None),
    ('missing_symbol OR @api.constrains', None),
    ('missing_symbol\n@api.constrains', None),
    ('缺失符号🙂27e3ff', None),
    ('不存在的源码符号_27e3ff', None),
])
def test_code_review_queries_match_source_oracle(corpus, native, query, module):
    """关联、继承、约束、计算、菜单、权限及跨模块引用都是文本证据，不推断执行顺序。"""
    if ':' not in query and not query.startswith(('不存在', 'missing_', '缺失符号')):
        assert expected_paths(corpus, query, module), query
    check_search(corpus, query, module)


def reference_for(service, path):
    payload = base64.urlsafe_b64encode(json.dumps(['hrp', path],
        ensure_ascii=False, separators=(',', ':')).encode()).decode().rstrip('=')
    return f'zoekt:{service.bundle_id}:{payload}'


@pytest.mark.parametrize('kind', ['.py', '.xml', '.js', '.po', '.csv', 'crlf', 'empty', 'converted'])
def test_public_read_reassembles_complete_source_without_unicode_or_newline_loss(corpus, native, kind):
    if kind == 'empty':
        candidates = [path for path, text in corpus.items() if text == '']
    elif kind == 'crlf':
        candidates = [path for path, text in corpus.items() if '\r\n' in text and len(text) > 16000]
    elif kind == 'converted':
        candidates = [json.loads(line)['path'] for line in
                      (AUDIT / 'encoding-conversions.jsonl').read_text().splitlines()]
    else:
        candidates = [path for path, text in corpus.items()
                      if path.endswith(kind) and (128 if kind == '.csv' else 16000) < len(text) < 100000]
    assert candidates, kind
    path = min(candidates, key=lambda key: len(corpus[key]))
    reference = reference_for(native, path)
    expected = corpus[path]
    chunks, offset = [], 0
    for size in [1, 17, 4096] + [16000] * (len(expected) // 16000 + 2):
        page = knowledge.read(reference, offset=offset, limit=size)
        assert page['found'] and page['path'] == path
        assert page['total_length'] == len(expected)
        assert page['offset'] == offset
        assert page['content'] == expected[offset:offset + size], path
        assert page['source_dependency'] is False
        chunks.append(page['content'])
        offset += len(page['content'])
        if page['complete']:
            assert page['next_offset'] is None and offset == len(expected)
            break
        assert page['next_offset'] == offset
    assert ''.join(chunks).encode() == expected.encode(), path
    assert knowledge.read(reference, offset=len(expected), limit=1)['complete'] is True


def test_three_mcp_workspaces_share_native_index_and_exchange_references(tmp_path, corpus):
    """三个真实 stdio 进程共享原生服务，退出一个工作区后其余仍能检索、续读。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    runtime = tmp_path / 'runtime'
    query = 'dy.expense.document.type'
    program = ('from pathlib import Path; from dy_superpowers_mcp import knowledge; '
               f'knowledge.RUNTIME_ROOT=Path({str(runtime)!r}); '
               'knowledge._source_service().idle_seconds=1; '
               'import server; server.main()')
    env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    env.update(PYTHONPATH=str(PLUGIN / 'mcp-server/src'), CODEX_HOME=str(tmp_path / 'codex'))

    async def exercise():
        async with AsyncExitStack() as stack:
            sessions, exits = [], []
            for index in range(3):
                workspace = tmp_path / f'workspace-{index}'
                workspace.mkdir()
                owner = await stack.enter_async_context(AsyncExitStack())
                params = StdioServerParameters(command=sys.executable, args=['-c', program],
                    cwd=str(workspace), env={**env, 'DY_WORKSPACE_ROOT': str(workspace)})
                reader, writer = await owner.enter_async_context(stdio_client(params))
                session = await owner.enter_async_context(ClientSession(reader, writer))
                await session.initialize()
                sessions.append(session)
                exits.append(owner)

            async def call(session, tool, arguments):
                reply = await session.call_tool(tool, {'trace_id': 'knowledge-concurrent', **arguments})
                assert not reply.is_error
                return json.loads(reply.content[0].text)

            replies = await asyncio.gather(*(call(session, 'dy_knowledge_search', {
                'query': query, 'module': 'dy_expense', 'knowledge_type': 'source', 'limit': 50})
                for session in sessions))
            expected = expected_paths(corpus, query, 'dy_expense')
            assert expected and len(expected) <= 50
            assert all({row['path'] for row in reply['results']} == expected for reply in replies)
            states = list(runtime.glob('*/service.json'))
            assert len(states) == 1
            pid = json.loads(states[0].read_text())['server_pid']
            queries = [('dy.expense.document.type', 'dy_expense'),
                       ('dy.fin.account.period', 'dy_fin_mdm'),
                       ('dy.purchase.requisition.order', 'dy_purchase')]
            for round_number in range(8):
                batch = [queries[(round_number + index) % len(queries)] for index in range(3)]
                values = await asyncio.gather(*(call(session, 'dy_knowledge_search', {
                    'query': term, 'module': module, 'knowledge_type': 'source', 'limit': 50})
                    for session, (term, module) in zip(sessions, batch)))
                for value, (term, module) in zip(values, batch):
                    matching = expected_paths(corpus, term, module)
                    assert matching and len(matching) <= 50
                    assert {item['path'] for item in value['results']} == matching
            row = replies[-1]['results'][0]
            # MCP SDK 的任务组按后进先出退出，关闭一个来源工作区后交叉读取引用。
            await exits[-1].aclose()
            for session in sessions[:-1]:
                page = await call(session, 'dy_knowledge_read', {'reference': row['reference'], 'limit': 17})
                assert page['content'] == corpus[row['path']][:17]
                rest = await call(session, 'dy_knowledge_read', {
                    'reference': row['reference'], 'offset': page['next_offset'], 'limit': 17})
                assert rest['content'] == corpus[row['path']][17:34]
                again = await call(session, 'dy_knowledge_search', {
                    'query': query, 'module': 'dy_expense', 'knowledge_type': 'source', 'limit': 50})
                assert {item['path'] for item in again['results']} == expected
            assert json.loads(states[0].read_text())['server_pid'] == pid

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))
    end = time.monotonic() + 8
    while list(runtime.glob('*/service.json')) and time.monotonic() < end:
        time.sleep(0.05)
    assert not list(runtime.glob('*/service.json'))
    for workspace in tmp_path.glob('workspace-*'):
        assert not (workspace / '.dy_superpowers').exists(), '知识检索不能写入业务记忆'
