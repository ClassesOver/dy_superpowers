"""可选的本地语义索引；缺少预构建产物时只报告降级，不现场下载或编译。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
from typing import Any, Iterable
from urllib.parse import urlparse


class SemanticUnavailable(ValueError):
    """语义索引或本地模型不能安全使用。"""


class RemoteEmbedder:
    """OpenAI 兼容的远程 embeddings provider；不持久化输入或响应。"""

    def __init__(self, endpoint: str, token: str, model: str, timeout: float = 10.0):
        parsed = urlparse(endpoint)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise SemanticUnavailable('远程 embedding 地址无效')
        if not token or not model:
            raise SemanticUnavailable('远程 embedding 配置不完整')
        self.endpoint, self.token, self.model, self.timeout = endpoint, token, model, timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            import requests
            response = requests.post(self.endpoint,
                                     headers={'Authorization': f'Bearer {self.token}',
                                              'Content-Type': 'application/json'},
                                     json={'model': self.model, 'input': texts}, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            values = [item['embedding'] for item in payload['data']]
            if len(values) != len(texts) or not all(isinstance(item, list) and item for item in values):
                raise ValueError('响应数量或向量格式不符')
            return values
        except SemanticUnavailable:
            raise
        except ValueError as exc:
            raise SemanticUnavailable('远程 embedding 响应无效') from exc
        except Exception as exc:
            raise SemanticUnavailable(f'远程 embedding 请求失败：{type(exc).__name__}') from exc


@dataclass(frozen=True)
class TextChunk:
    text: str
    start: int
    end: int


def chunk_text(text: str, max_chars: int = 1792, overlap_chars: int = 256) -> list[TextChunk]:
    """无 tokenizer 时使用保守字符上限；不丢失文本且相邻片段有重叠。"""
    if not text:
        return []
    if max_chars <= 0 or not 0 <= overlap_chars < max_chars:
        raise ValueError('切片边界无效')
    result = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind('\n', start, end), text.rfind(' ', start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        result.append(TextChunk(text[start:end], start, end))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return result


def _path(row: dict[str, Any]) -> str:
    return str(row.get('path') or row.get('name') or '')


def rrf_merge(lexical: Iterable[dict[str, Any]], semantic: Iterable[dict[str, Any]], limit: int, k: int = 60) -> list[dict[str, Any]]:
    """按文件去重、等权合并两路排名；保留字面结果的可核查摘要。"""
    scores: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(lexical, 1):
        key = _path(row)
        if not key:
            continue
        scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
        rows.setdefault(key, row)
    for rank, row in enumerate(semantic, 1):
        key = _path(row)
        if not key:
            continue
        scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
        rows.setdefault(key, row)
    return [rows[key] for key in sorted(scores, key=lambda item: (-scores[item], item))[:limit]]


def _decode_vector(value: bytes | memoryview | str) -> list[float]:
    if isinstance(value, str):
        return [float(item) for item in value.split(',') if item]
    raw = bytes(value)
    if len(raw) % 4:
        raise SemanticUnavailable('向量格式无效')
    return list(struct.unpack('<' + 'f' * (len(raw) // 4), raw))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    return dot / (norm_left * norm_right) if norm_left and norm_right else -1.0


class SemanticIndex:
    """只读 SQLite 索引。embedder 需提供 ``embed([text])`` 或可调用接口。"""

    FORMAT = 'semantic-v1'

    def __init__(self, path: Path, embedder: Any = None, *, bundle_id: str | None = None, model_sha256: str | None = None):
        self.path = Path(path)
        if not self.path.is_file():
            raise SemanticUnavailable('语义数据库不存在')
        try:
            self.db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
            metadata = dict(self.db.execute('select key, value from metadata').fetchall())
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SemanticUnavailable('语义数据库损坏') from exc
        if metadata.get('format') != self.FORMAT:
            raise SemanticUnavailable('语义数据库版本不符')
        if bundle_id and metadata.get('bundle_id') != bundle_id:
            raise SemanticUnavailable('语义数据库与 Zoekt 版本不符')
        if model_sha256 and metadata.get('model_sha256') != model_sha256:
            raise SemanticUnavailable('语义模型版本不符')
        self.metadata = metadata
        self.embedder = embedder

    def search(self, query: str, *, module: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.embedder is None:
            raise SemanticUnavailable('本地语义模型未安装')
        try:
            vectors = self.embedder.embed([query]) if hasattr(self.embedder, 'embed') else self.embedder([query])
            query_vector = list(next(iter(vectors)))
        except Exception as exc:
            raise SemanticUnavailable('本地语义模型加载失败') from exc
        sql = 'select path, module, text, line_start, line_end, embedding from chunks'
        params: list[Any] = []
        if module:
            sql += ' where module = ?'
            params.append(module)
        sql += ' limit 200'
        try:
            rows = self.db.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise SemanticUnavailable('语义数据库读取失败') from exc
        scored = []
        for path, row_module, text, line_start, line_end, embedding in rows:
            vector = _decode_vector(embedding)
            score = _cosine(query_vector, vector)
            if score >= 0:
                scored.append((score, {'path': path, 'module': row_module, 'summary': text[:240],
                                       'snippet': text, 'line': line_start, 'line_end': line_end,
                                       'semantic_score': round(score, 6), 'knowledge_type': 'source'}))
        scored.sort(key=lambda item: (-item[0], item[1]['path'], item[1]['line']))
        return [row for _, row in scored[:limit]]


def model_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()
