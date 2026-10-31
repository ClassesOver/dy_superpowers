import sqlite3
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp import knowledge
from dy_superpowers_mcp.semantic import SemanticIndex, SemanticUnavailable, chunk_text, rrf_merge


def test_chunk_text_covers_long_input_with_overlap():
    text = ''.join(str(i % 10) for i in range(1000))
    chunks = chunk_text(text, max_chars=240, overlap_chars=40)
    assert chunks
    assert ''.join(piece.text for piece in chunks) != text
    assert chunks[0].start == 0
    assert chunks[-1].end == len(text)
    assert all(piece.end > piece.start for piece in chunks)


def test_rrf_merges_by_file_and_keeps_equal_weight():
    lexical = [{'path': 'a.py', 'summary': 'A'}, {'path': 'b.py', 'summary': 'B'}]
    semantic = [{'path': 'b.py', 'summary': 'B semantic'}, {'path': 'c.py', 'summary': 'C'}]
    result = rrf_merge(lexical, semantic, limit=3)
    assert [row['path'] for row in result] == ['b.py', 'a.py', 'c.py']
    assert result[0]['summary'] == 'B'


def test_semantic_index_rejects_missing_or_mismatched_manifest(tmp_path):
    with pytest.raises(SemanticUnavailable, match='数据库'):
        SemanticIndex(tmp_path / 'missing.sqlite3')
    path = tmp_path / 'bad.sqlite3'
    sqlite3.connect(path).execute('create table metadata (key text, value text)')
    with pytest.raises(SemanticUnavailable, match='版本'):
        SemanticIndex(path)


def test_semantic_index_reads_readonly_chunks_and_filters_module(tmp_path):
    path = tmp_path / 'semantic.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
        create table metadata (key text primary key, value text not null);
        create table chunks (path text, module text, text text, line_start integer, line_end integer, embedding blob);
    ''')
    db.executemany('insert into metadata values (?, ?)', [('format', 'semantic-v1'), ('bundle_id', 'b' * 24)])
    db.executemany('insert into chunks values (?, ?, ?, ?, ?, ?)', [
        ('repo/a/models/x.py', 'a', 'OCR 导入', 4, 4, struct.pack('<ff', 1, 0)),
        ('repo/b/models/y.py', 'b', '其他', 9, 9, struct.pack('<ff', 0, 1)),
    ])
    db.commit(); db.close()

    class Embedder:
        def embed(self, texts):
            return [[1, 0] for _ in texts]

    index = SemanticIndex(path, Embedder(), bundle_id='b' * 24)
    rows = index.search('OCR', module='a')
    assert len(rows) == 1
    assert rows[0]['path'] == 'repo/a/models/x.py'
    assert rows[0]['line'] == 4


def test_remote_embedder_uses_openai_compatible_endpoint_without_leaking_token(monkeypatch):
    from dy_superpowers_mcp.semantic import RemoteEmbedder
    received = {}

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {'data': [{'embedding': [1.0, 0.0]}]}

    def post(url, **kwargs):
        received.update(url=url, kwargs=kwargs)
        return Reply()

    monkeypatch.setattr('requests.post', post)
    result = RemoteEmbedder('https://embed.example/v1/embeddings', 'secret-token', 'model-a').embed(['问题'])
    assert result == [[1.0, 0.0]]
    assert received['kwargs']['headers']['Authorization'] == 'Bearer secret-token'
    assert received['kwargs']['json'] == {'model': 'model-a', 'input': ['问题']}


def test_remote_embedder_rejects_bad_response_without_returning_secret(monkeypatch):
    from dy_superpowers_mcp.semantic import RemoteEmbedder

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {'data': []}

    monkeypatch.setattr('requests.post', lambda *args, **kwargs: Reply())
    with pytest.raises(SemanticUnavailable, match='响应') as error:
        RemoteEmbedder('https://embed.example/v1/embeddings', 'secret-token', 'model-a').embed(['问题'])
    assert 'secret-token' not in str(error.value)


def test_hybrid_mode_explicitly_degrades_when_semantic_index_unavailable(monkeypatch):
    monkeypatch.setattr(knowledge, '_source_service', lambda: type('Service', (), {'bundle_id': 'a' * 24})())
    monkeypatch.setattr(knowledge, '_query_result', lambda *args, **kwargs: {'FileCount': 0, 'Files': []})
    monkeypatch.setattr(knowledge, '_semantic_service', lambda: (_ for _ in ()).throw(SemanticUnavailable('模型未安装')))
    result = knowledge.search('报销', knowledge_type='source', retrieval_mode='hybrid')
    assert result['retrieval_mode'] == 'hybrid'
    assert result['semantic']['status'] == 'degraded'
    assert '模型未安装' in result['semantic']['reason']
    assert result['total_known'] is False
    assert result['total_count'] is None
