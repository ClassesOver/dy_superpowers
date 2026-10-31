#!/usr/bin/env python3
"""开发期从已过滤源码快照构建只读语义 SQLite；运行时不调用此脚本。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sqlite3
import struct
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'plugins/dy_superpowers/mcp-server/src'))
from dy_superpowers_mcp.semantic import chunk_text


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description='构建 HRP 源码语义索引')
    parser.add_argument('source', type=Path, help='与 Zoekt 相同策略过滤后的源码快照')
    parser.add_argument('output', type=Path, help='输出 semantic.sqlite3')
    parser.add_argument('--bundle-id', required=True)
    parser.add_argument('--model-sha256', required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.model_dir.is_dir() or not any(args.model_dir.iterdir()):
        parser.error('模型目录不存在或为空；构建禁止在线下载')
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        parser.error('构建需要开发期 fastembed')
    files = [path for path in args.source.rglob('*') if path.is_file() and path.suffix.lower() in {'.py', '.xml', '.js', '.csv', '.md'}]
    model = TextEmbedding(model_name='BAAI/bge-small-zh-v1.5', cache_dir=str(args.model_dir))
    target = args.output.with_suffix(args.output.suffix + '.tmp')
    if target.exists():
        target.unlink()
    db = sqlite3.connect(target)
    db.executescript('''
        create table metadata (key text primary key, value text not null);
        create table chunks (
            path text not null, module text, text text not null,
            line_start integer not null, line_end integer not null,
            embedding blob not null
        );
        create index chunks_module on chunks(module);
    ''')
    metadata = {'format': 'semantic-v1', 'bundle_id': args.bundle_id, 'model_sha256': args.model_sha256,
                'chunk_format': 'chars-1792-overlap-256'}
    db.executemany('insert into metadata(key, value) values (?, ?)', metadata.items())
    for path in files:
        text = path.read_text(encoding='utf-8', errors='replace')
        chunks = chunk_text(text)
        if not chunks:
            continue
        vectors = model.embed([piece.text for piece in chunks])
        module = path.parts[1] if len(path.parts) > 1 else None
        for piece, vector in zip(chunks, vectors):
            line_start = text.count('\n', 0, piece.start) + 1
            line_end = text.count('\n', 0, piece.end) + 1
            blob = struct.pack('<' + 'f' * len(vector), *vector)
            db.execute('insert into chunks values (?, ?, ?, ?, ?, ?)',
                       (path.as_posix(), module, piece.text, line_start, line_end, blob))
    db.commit()
    db.close()
    target.replace(args.output)
    print(f'已构建 {args.output}（{len(files)} 个文件，sha256={sha256(args.output)}）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
