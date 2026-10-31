#!/usr/bin/env python3
"""离线评测固定业务问法的目标代码命中与排序，不执行 Odoo 或调用模型。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]


def metrics(ranked, targets):
    gold = set(targets)
    if not gold:
        raise ValueError('没有源码目标的问题单独观察，不计入命中率')
    top = ranked[:10]
    first = next((rank for rank, path in enumerate(top, 1) if path in gold), None)
    return {'hit1': int(bool(gold.intersection(ranked[:1]))),
            'hit5': int(bool(gold.intersection(ranked[:5]))),
            'hit10': int(bool(gold.intersection(top))),
            'mrr10': 1 / first if first else 0,
            'target_recall10': len(gold.intersection(top)) / len(gold)}


def validate_cases(data):
    if data['format'] != 1 or not data['annotation']:
        raise ValueError('评测清单格式或标注边界缺失')
    ids = [case['id'] for case in data['cases']]
    if len(ids) != len(set(ids)):
        raise ValueError('问题编号重复')
    for case in data['cases']:
        if case['kind'] not in {'question', 'short_phrase', 'runtime_only', 'unjudged'}:
            raise ValueError('问题类型无效')
        if not case['question'].strip() or not case['keyword'].strip():
            raise ValueError('问法或固定关键词缺失')
        if bool(case['targets']) == (case['kind'] in {'runtime_only', 'unjudged'}):
            raise ValueError('源码目标与运行数据问题混淆')
        for key in case['targets']:
            target = data['targets'][key]
            if not target['path'] or not target['anchor']:
                raise ValueError('源码目标需要路径和人工核对锚点')


def summarize(rows):
    grouped = {}
    for mode in sorted({row['mode'] for row in rows}):
        selected = [row for row in rows if row['mode'] == mode and row['metrics'] is not None]
        groups = {'all_positive': selected}
        for kind in sorted({row['kind'] for row in selected}):
            groups[kind] = [row for row in selected if row['kind'] == kind]
        grouped[mode] = {kind: {'count': len(values), **{
            key: sum(row['metrics'][key] for row in values) / len(values)
            for key in ('hit1', 'hit5', 'hit10', 'mrr10', 'target_recall10')}}
            for kind, values in groups.items() if values}
    return grouped


def evaluate(data, rounds, search=None, service=None):
    sys.path.insert(0, str(ROOT / 'plugins/dy_superpowers/mcp-server/src'))
    from dy_superpowers_mcp import knowledge

    service = service or knowledge._source_service()
    service.idle_seconds = 1
    search = search or knowledge.search
    rows = []
    for run in range(1, rounds + 1):
        for case in data['cases']:
            targets = [data['targets'][key]['path'] for key in case['targets']]
            modes = [
                ('natural_default', case['question'], {}),
                ('keyword_default', case['keyword'], {}),
                ('keyword_source', case['keyword'], {'knowledge_type': 'source'}),
                ('keyword_source_scoped', case['keyword'], {
                    'knowledge_type': 'source', 'module': case['module']}),
            ]
            for mode, query, filters in modes:
                start = time.monotonic()
                response = search(query, limit=20, **filters)
                elapsed = (time.monotonic() - start) * 1000
                ranked = [item['path'] if item['knowledge_type'] == 'source'
                          else 'summary:' + item['module'] for item in response['results']]
                rows.append({'run': run, 'id': case['id'], 'kind': case['kind'],
                    'mode': mode, 'query': query, 'filters': filters,
                    'ranked_top10': ranked[:10], 'returned_count': response['count'],
                    'total_count': response['total_count'], 'total_known': response['total_known'],
                    'truncated': response['truncated'], 'elapsed_ms': round(elapsed, 3),
                    'references_top10': [item['reference'] for item in response['results'][:10]],
                    'metrics': metrics(ranked, targets) if targets else None})
    # 宏平均以问题为单位；重复轮次只检查稳定性，不膨胀独立问题数量。
    first_run = [row for row in rows if row['run'] == 1]
    unstable = []
    for row in first_run:
        peers = [item for item in rows if item['id'] == row['id'] and item['mode'] == row['mode']]
        if any(item['ranked_top10'] != row['ranked_top10'] for item in peers):
            unstable.append({'id': row['id'], 'mode': row['mode']})
    return {'bundle_id': service.bundle_id, 'rounds': rounds, 'question_count': len(data['cases']),
            'annotation': data['annotation'], 'limit': 20, 'summary': summarize(first_run),
            'unstable_top10': unstable, 'partial_searches': sum(not row['total_known'] for row in rows),
            'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path,
                        default=ROOT / 'docs/research-sources/zoekt/natural-language-cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3, choices=range(1, 11))
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    cases = json.loads(raw)
    validate_cases(cases)
    result = evaluate(cases, args.rounds)
    result['cases_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'rows'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
