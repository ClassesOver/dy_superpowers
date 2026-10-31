#!/usr/bin/env python3
"""仅用于开发期逐文件验收原生索引；不创建任何自研索引。"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/dy_superpowers/mcp-server/src'))
from dy_superpowers_mcp.zoekt_service import ZoektService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot', 'index', 'server', 'audit'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve(strict=True)
    if snapshot.name != 'hrp' or not snapshot.parent.name.startswith('dy-zoekt-build-'):
        parser.error('只允许验收构建入口产生的临时副本，不得移动原始源码')
    expected = {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(snapshot.rglob('*')) if path.is_file()}
    (args.audit / 'content-sha256.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n')
    # 原生索引元数据中的 Source 路径从此不再存在，回读不可能依赖原副本。
    snapshot.rename(snapshot.with_name('offline-source'))
    service = ZoektService(args.index, args.server, runtime_root=snapshot.parent / 'runtime', idle_seconds=1)
    opts = {'MaxDocDisplayCount': len(expected) + 1, 'MaxMatchDisplayCount': len(expected) + 1,
            'ShardMaxMatchCount': 100000, 'TotalMaxMatchCount': 100000, 'MaxWallTime': 2000000000}
    result = service.request('/api/search', {'Q': 'type:file', 'Opts': opts})['Result']
    actual = {item['FileName'] for item in (result.get('Files') or [])}
    mismatches = []
    for filename, digest in expected.items():
        # 对齐 Go regexp.QuoteMeta；只转义真正的正则元字符。
        query = 'repo:"^hrp$" file:' + json.dumps('^' + re.sub(
            r'([\\.+*?()|\[\]{}^$])', r'\\\1', filename) + '$', ensure_ascii=False)
        reply = service.request('/api/search', {'Q': query, 'Opts': {
            'Whole': True, 'MaxDocDisplayCount': 2, 'MaxWallTime': 2000000000}})['Result']
        files = reply.get('Files') or []
        if len(files) != 1 or hashlib.sha256(base64.b64decode(files[0].get('Content', ''))).hexdigest() != digest:
            mismatches.append(filename)
    report = {'status': 'passed' if actual == set(expected) and not mismatches else 'failed',
              'verified_files': len(expected), 'source_dependency': False,
              'missing_files': sorted(set(expected) - actual), 'unexpected_files': sorted(actual - set(expected)),
              'content_mismatches': mismatches}
    (args.audit / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if report['status'] != 'passed':
        raise SystemExit('原生索引内容不完整，禁止发布；详见 verification.json')


if __name__ == '__main__':
    main()
