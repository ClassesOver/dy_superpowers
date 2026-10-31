"""评测计算必须区分源码目标、摘要、运行数据问题和未标注文件。"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def evaluator():
    path = ROOT / 'scripts/research/evaluate_knowledge_retrieval.py'
    spec = importlib.util.spec_from_file_location('knowledge_retrieval_eval', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metrics_preserve_summary_positions_and_deduplicate_recall(evaluator):
    result = evaluator.metrics(['summary:module', 'unjudged.py', 'a.py', 'a.py', 'b.py'], ['a.py', 'b.py'])
    assert result == {'hit1': 0, 'hit5': 1, 'hit10': 1, 'mrr10': 1 / 3, 'target_recall10': 1}


def test_missing_and_beyond_cutoff_targets_score_zero(evaluator):
    assert evaluator.metrics(['other.py'] * 10 + ['target.py'], ['target.py']) == {
        'hit1': 0, 'hit5': 0, 'hit10': 0, 'mrr10': 0, 'target_recall10': 0}


def test_partial_target_recall_is_not_hit_rate(evaluator):
    result = evaluator.metrics(['a.py'], ['a.py', 'b.py'])
    assert result['hit1'] == 1
    assert result['target_recall10'] == 0.5


def test_empty_gold_cannot_be_counted_as_correct_rejection(evaluator):
    with pytest.raises(ValueError):
        evaluator.metrics([], [])


def test_fixture_has_frozen_questions_targets_and_negative_controls(evaluator):
    fixture = json.loads((ROOT / 'docs/research-sources/zoekt/natural-language-cases.json').read_text())
    evaluator.validate_cases(fixture)
    assert len(fixture['cases']) == 27
    assert {case['kind'] for case in fixture['cases']} == {'question', 'short_phrase', 'runtime_only'}
    audit = json.loads((ROOT / 'docs/research-sources/zoekt/hrp-20261030-excluded/content-sha256.json').read_text())
    for target in fixture['targets'].values():
        assert target['path'] in audit
        assert target['anchor']
    for case in fixture['cases']:
        if case['targets']:
            assert all(fixture['targets'][key]['path'].split('/')[1] == case['module'] for key in case['targets'])


def test_summary_uses_only_answerable_cases(evaluator):
    rows = [
        {'mode': 'raw', 'kind': 'question', 'metrics': evaluator.metrics(['a.py'], ['a.py'])},
        {'mode': 'raw', 'kind': 'question', 'metrics': evaluator.metrics([], ['a.py'])},
        {'mode': 'raw', 'kind': 'runtime_only', 'metrics': None},
    ]
    summary = evaluator.summarize(rows)
    assert summary['raw']['all_positive']['count'] == 2
    assert summary['raw']['all_positive']['hit10'] == 0.5
    assert 'runtime_only' not in summary['raw']


def test_targets_match_readonly_source_and_frozen_index_snapshot():
    source = Path(os.environ.get('DY_HRP_SOURCE_ROOT', '/home/junge/pros/dypowers/data'))
    if not source.is_dir():
        pytest.skip('需要原始只读源码核对预先标注的目标文件')
    fixture = json.loads((ROOT / 'docs/research-sources/zoekt/natural-language-cases.json').read_text())
    audit = json.loads((ROOT / 'docs/research-sources/zoekt/hrp-20261030-excluded/content-sha256.json').read_text())
    for target in fixture['targets'].values():
        content = (source / target['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == audit[target['path']], target['path']
        assert target['anchor'] in content.decode('utf-8'), target['path']


def test_evaluation_uses_fixed_queries_and_does_not_inflate_counts_with_rounds(evaluator, monkeypatch):
    sys.path.insert(0, str(ROOT / 'plugins/dy_superpowers/mcp-server/src'))
    from dy_superpowers_mcp import knowledge
    monkeypatch.setattr(knowledge, '_source_service', lambda: SimpleNamespace(bundle_id='synthetic'))
    calls = []

    def search(query, **filters):
        calls.append((query, filters))
        results = []
        if query == 'symbol':
            if filters.get('knowledge_type') != 'source':
                results.append({'knowledge_type': 'summary', 'module': 'm', 'reference': 'summary:m'})
            results.append({'knowledge_type': 'source', 'path': 'm/models/a.py', 'reference': 'synthetic'})
        return {'results': results, 'count': len(results), 'total_count': len(results),
                'total_known': True, 'truncated': False}

    monkeypatch.setattr(knowledge, 'search', search)
    data = {'annotation': '合成评测器测试，不是 HRP 结果',
            'targets': {'code': {'path': 'm/models/a.py'}}, 'cases': [
                {'id': 'P', 'kind': 'question', 'question': '完整问题', 'keyword': 'symbol',
                 'module': 'm', 'targets': ['code']},
                {'id': 'N', 'kind': 'runtime_only', 'question': '实时余额', 'keyword': '余额',
                 'module': None, 'targets': []}]}
    result = evaluator.evaluate(data, 3)
    assert len(calls) == 24
    assert {query for query, _ in calls} == {'完整问题', 'symbol', '实时余额', '余额'}
    assert all(filters['limit'] == 20 for _, filters in calls)
    assert result['summary']['keyword_default']['all_positive']['count'] == 1
    assert result['summary']['keyword_default']['all_positive']['mrr10'] == 0.5
    assert result['summary']['keyword_source']['all_positive']['hit1'] == 1
    assert result['unstable_top10'] == []
    assert all(row['metrics'] is None for row in result['rows'] if row['kind'] == 'runtime_only')


def test_backend_failure_is_not_silently_scored_as_no_hit(evaluator, monkeypatch):
    sys.path.insert(0, str(ROOT / 'plugins/dy_superpowers/mcp-server/src'))
    from dy_superpowers_mcp import knowledge
    monkeypatch.setattr(knowledge, '_source_service', lambda: SimpleNamespace(bundle_id='synthetic'))

    def failed(*args, **kwargs):
        raise ValueError('合成检索故障')

    monkeypatch.setattr(knowledge, 'search', failed)
    with pytest.raises(ValueError, match='合成检索故障'):
        evaluator.evaluate({'cases': [{'id': 'N', 'kind': 'runtime_only',
            'question': '实时余额', 'keyword': '余额', 'module': None, 'targets': []}]}, 1)


def test_research_questions_preserve_all_template_text_and_explicit_gaps(evaluator):
    fixture = json.loads((ROOT / 'docs/research-sources/zoekt/research-question-cases.json').read_text())
    evaluator.validate_cases(fixture)
    observed = {case['id']: case for case in fixture['cases'] + fixture['excluded']}
    expected = {}
    for source in fixture['templates']:
        path = ROOT / source['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        for question in json.loads(path.read_text())['questions']:
            expected[question['question_id']] = question
    assert observed.keys() == expected.keys()
    for key, question in expected.items():
        assert observed[key]['question'] == question['question']
        assert observed[key]['config_item'] == question['config_item']
    assert all(case['question'].strip() == '/' for case in fixture['excluded'])
    assert any(case['kind'] == 'unjudged' for case in fixture['cases'])
    assert all(case['scope_note'] for case in fixture['cases'])


def test_research_targets_match_frozen_source():
    source = Path(os.environ.get('DY_HRP_SOURCE_ROOT', '/home/junge/pros/dypowers/data'))
    if not source.is_dir():
        pytest.skip('需要原始只读源码核对调研目标')
    fixture = json.loads((ROOT / 'docs/research-sources/zoekt/research-question-cases.json').read_text())
    audit = json.loads((ROOT / 'docs/research-sources/zoekt/hrp-20261030-excluded/content-sha256.json').read_text())
    for target in fixture['targets'].values():
        content = (source / target['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == audit[target['path']], target['path']
        assert target['anchor'] in content.decode('utf-8'), target['path']
