"""默认 Knowledge 必须直接读取随包的完整 Zoekt 索引，不依赖源仓库。"""
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp import knowledge


@pytest.fixture(autouse=True)
def isolated_service(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, 'RUNTIME_ROOT', tmp_path / 'runtime', raising=False)
    original = knowledge._source_service
    clients = []

    def service():
        value = original()
        value.idle_seconds = 0.3
        clients.append(value)
        return value

    monkeypatch.setattr(knowledge, '_source_service', service)
    yield
    end = time.monotonic() + 5
    while any(client.state_path.exists() for client in clients) and time.monotonic() < end:
        time.sleep(0.05)
    assert not any(client.state_path.exists() for client in clients)


def test_default_source_search_uses_zoekt_and_preserves_legacy_fields():
    result = knowledge.search('dy.expense.document.type', knowledge_type='source')
    assert result['count'] > 0
    assert result['count'] == len(result['results'])
    assert result['coverage']['backend'] == 'zoekt'
    assert result['query'] == 'dy.expense.document.type'
    assert all(row['knowledge_type'] == 'source' for row in result['results'])
    assert all(len(row['summary']) <= 240 for row in result['results'])
    assert all(row['reference'].startswith('zoekt:') for row in result['results'])
    assert all(row['fact_type'] == 'source_text' for row in result['results'])
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 65536


def test_literal_queries_and_exact_module_boundary():
    result = knowledge.search('dy.expense.document.type', module='dy_expense', knowledge_type='source', limit=3)
    assert result['count'] == 3
    assert result['truncated'] is True
    for row in result['results']:
        assert row['module'] == 'dy_expense'
        assert '/dy_expense/' in row['path']
    # 这些字符串是检索数据，不是 Zoekt 运算符或正则表达式。
    assert knowledge.search('NOT repo:hrp', knowledge_type='source')['count'] == 0
    assert knowledge.search('dyXexpenseXdocumentXtype', knowledge_type='source')['count'] == 0
    assert knowledge.search('dy.expense.document.type', module='expense', knowledge_type='source')['count'] == 0


def test_summary_aliases_stay_available_and_are_labelled():
    result = knowledge.search('报销', knowledge_type='summary')
    assert any(row['module'] == 'dy_expense' for row in result['results'])
    assert all(row['fact_type'] == 'curated_interpretation' for row in result['results'])
    assert knowledge.read('summary:dy_expense')['found'] is True
    assert any(row['module'] == 'dy_fin_expense_pf' for row in knowledge.search('费用制证')['results'])


def test_manifest_references_and_full_unicode_pagination():
    info = knowledge.module_info('dy_expense')
    assert info['found'] is True
    assert info['version_match'] == 'not_checked'
    assert info['fact_type'] == 'curated_interpretation'
    assert info['references']
    reference = next(row['reference'] for row in info['references'] if row['path'].endswith('/__manifest__.py'))
    first = knowledge.read(reference, limit=17)
    assert first['complete'] is False
    rest = knowledge.read(reference, offset=first['next_offset'], limit=16000)
    assert rest['complete'] is True
    whole = knowledge.read(reference, limit=16000)
    assert first['content'] + rest['content'] == whole['content']
    assert '费用管理' in whole['content']
    assert whole['source_dependency'] is False
    assert knowledge.read(reference, offset=whole['total_length'] + 50)['complete'] is True


@pytest.mark.parametrize('filters', [
    {'model': 'dy.expense.report'}, {'knowledge_type': 'field'}, {'knowledge_type': 'model'},
    {'knowledge_type': 'constraint'}, {'limit': True}, {'limit': 0}, {'limit': 51},
])
def test_unsupported_semantics_and_invalid_limits_are_explicit(filters):
    with pytest.raises(ValueError):
        knowledge.search('公司', **filters)


@pytest.mark.parametrize('reference', ['/etc/passwd', '../data/knowledge.sqlite3', 'zoekt:bad'])
def test_read_does_not_open_arbitrary_paths(reference):
    with pytest.raises(ValueError):
        knowledge.read(reference)


def test_old_snippet_references_are_explicitly_stale():
    result = knowledge.read('src:' + 'a' * 32)
    assert result['found'] is False
    assert '重新检索' in result['limitations']


def test_no_result_is_distinct_from_missing_index(tmp_path, monkeypatch):
    result = knowledge.search('definitely_missing_6f638eeb6a', knowledge_type='source')
    assert result['count'] == 0
    assert result['total_known'] is True
    assert result['truncated'] is False
    monkeypatch.setattr(knowledge, 'INDEX_DIR', tmp_path / 'missing', raising=False)
    with pytest.raises(ValueError, match='索引'):
        knowledge.search('报销')
    assert knowledge.search('报销', knowledge_type='summary')['count'] > 0


def test_stale_bundle_reference_cannot_read_another_version():
    row = knowledge.search('费用管理', module='dy_expense', knowledge_type='source')['results'][0]
    prefix, _, path = row['reference'].split(':', 2)
    stale = knowledge.read(prefix + ':' + '0' * 24 + ':' + path)
    assert stale['found'] is False
    assert '重新检索' in stale['limitations']


def test_display_match_limit_is_reported_even_below_document_limit(monkeypatch):
    monkeypatch.setattr(knowledge, '_query_result', lambda *args, **kwargs: {
        'FileCount': 3, 'Files': [{'FileName': 'repo/demo/example.py'}]})
    result = knowledge.search('many_matches', knowledge_type='source', limit=20)
    assert result['count'] == 1
    assert result['total_count'] == 3
    assert result['total_known'] is True
    assert result['truncated'] is True


def test_partial_module_lookup_cannot_prove_a_module_is_absent(monkeypatch):
    monkeypatch.setattr(knowledge, '_query_result', lambda *args, **kwargs: {
        'Files': [], 'FilesSkipped': 5})
    with pytest.raises(ValueError, match='不完整'):
        knowledge.module_info('module_without_summary')


def test_requirement_analysis_keeps_its_existing_long_description_contract():
    from dy_superpowers_mcp.odoo_planning import analyze
    description = '采购库存管理需求说明。' * 200
    result = analyze(description)
    assert result['description'] == description
    assert result['execution_authorized'] is False
    assert isinstance(result['modules_needed'], list)


def test_standard_module_version_is_only_a_summary_version():
    info = knowledge.module_info('account')
    assert info['odoo_version'] == '12.0'
    assert info['odoo_version_scope'] == 'summary'
    assert info['version_match'] == 'not_checked'
    assert info['indexed_odoo_versions'] is None


def test_native_shared_search_survives_original_install_move(tmp_path):
    from dy_superpowers_mcp.zoekt_service import ZoektService

    installs = [tmp_path / name for name in ('first', 'second')]
    for install in installs:
        install.mkdir()
        for path in knowledge.INDEX_DIR.iterdir():
            if path.is_file():
                (install / path.name).hardlink_to(path)
    first, second = [ZoektService(path, knowledge.BINARY_PATH,
        runtime_root=tmp_path / 'native-runtime', idle_seconds=1) for path in installs]
    payload = {'Q': 'file:__manifest__\\.py$', 'Opts': {'MaxDocDisplayCount': 200}}
    before = first.request('/api/search', payload)['Result']
    assert before['FileCount'] == 166
    original_pid = json.loads(first.state_path.read_text())['server_pid']
    installs[0].rename(tmp_path / 'retired')
    for _ in range(4):
        time.sleep(0.3)
        after = second.request('/api/search', payload)['Result']
        assert after['FileCount'] == 166
        assert json.loads(second.state_path.read_text())['server_pid'] == original_pid
    end = time.monotonic() + 5
    while second.state_path.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert not second.state_path.exists()
    assert not list(second.directory.glob('pinned-*'))
