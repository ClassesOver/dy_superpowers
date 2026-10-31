"""字面检索保留业务词，并优先展示可核查的实现。"""
import base64

from dy_superpowers_mcp import knowledge


def test_unregistered_business_words_are_preserved():
    assert knowledge._question_terms('微信导入') == ['微信', '导入']
    assert '卡包' in knowledge._question_terms('微信卡包发票导入')


def test_summary_does_not_prevent_source_keyword_search(monkeypatch):
    class Service:
        bundle_id = 'a' * 24

    expressions = []

    def query(service, expression, limit):
        expressions.append(expression)
        files = [] if len(expressions) == 1 else [{'FileName': 'repo/mobile_expense/models/mobile.py'}]
        return {'FileCount': len(files), 'Files': files}

    monkeypatch.setattr(knowledge, '_source_service', Service)
    monkeypatch.setattr(knowledge, '_query_result', query)
    result = knowledge.search('移动报销')
    assert any(row['knowledge_type'] == 'source' for row in result['results'])
    assert result['search_terms'] == ['移动', '报销']
    assert result['results'][0]['reference'] == 'summary:mobile_expense'
    assert result['results'][1]['knowledge_type'] == 'source'
    assert ' or ' not in expressions[1]
    assert result['query_relaxed'] is False


def test_literal_search_ranks_code_above_translation(monkeypatch):
    class Service:
        bundle_id = 'a' * 24

    def row(path, lines):
        return {'FileName': path, 'LineMatches': [
            {'Line': base64.b64encode(line.encode()).decode(), 'LineNumber': i + 1}
            for i, line in enumerate(lines)]}

    monkeypatch.setattr(knowledge, '_source_service', Service)
    monkeypatch.setattr(knowledge, '_query_result', lambda *args: {'FileCount': 2, 'Files': [
        row('repo/demo/i18n/zh_CN.po', ['OCR']),
        row('repo/demo/models/invoice.py', ['OCR'])]})
    result = knowledge.search('OCR', knowledge_type='source', limit=1)
    assert result['results'][0]['path'].endswith('invoice.py')
    assert result['total_count'] == 2


def test_relaxed_query_is_explicit_and_keeps_terms(monkeypatch):
    class Service:
        bundle_id = 'a' * 24

    expressions = []

    def query(service, expression, limit):
        expressions.append(expression)
        return {'FileCount': 0, 'Files': []}

    monkeypatch.setattr(knowledge, '_source_service', Service)
    monkeypatch.setattr(knowledge, '_query_result', query)
    result = knowledge.search('微信导入', knowledge_type='source')
    assert len(expressions) == 3
    assert ' or ' in expressions[-1]
    assert result['query_relaxed'] is True
    assert result['search_terms'] == ['微信', '导入']


def test_keyword_partial_result_is_not_relaxed(monkeypatch):
    class Service:
        bundle_id = 'a' * 24

    expressions = []

    def query(service, expression, limit):
        expressions.append(expression)
        return {'FileCount': 0, 'Files': [], 'FilesSkipped': int(len(expressions) > 1)}

    monkeypatch.setattr(knowledge, '_source_service', Service)
    monkeypatch.setattr(knowledge, '_query_result', query)
    result = knowledge.search('微信导入', knowledge_type='source')
    assert len(expressions) == 2
    assert result['query_relaxed'] is False
    assert result['total_known'] is False
    assert result['truncated'] is True


def test_all_matching_lines_contribute_to_rank_and_best_snippet():
    class Service:
        bundle_id = 'a' * 24

    def row(path, texts):
        return {'FileName': path, 'LineMatches': [
            {'Line': base64.b64encode(text.encode()).decode(), 'LineNumber': i + 1}
            for i, text in enumerate(texts)]}

    rows = [row('repo/demo/models/one.py', ['导入']),
            row('repo/demo/i18n/zh_CN.po', ['微信导入']),
            row('repo/demo/models/two.py', ['其他', '微信导入'])]
    result = knowledge._rank_sources(Service(), rows, ['微信', '导入'])
    assert result[0]['path'].endswith('two.py')
    assert result[0]['summary'] == '微信导入'
    assert result[0]['line'] == 2
    assert '/i18n/' in result[-1]['path']
