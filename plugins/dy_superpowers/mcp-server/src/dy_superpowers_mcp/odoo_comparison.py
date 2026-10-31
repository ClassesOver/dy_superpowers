"""实时只读结果的精确比较；持久化摘要和状态，不保存业务返回行。"""
import hashlib
import json
import math
import re


def comparison_digest(spec):
    if (not isinstance(spec, dict) or set(spec) != {'expected_count', 'expected_values'}
            or type(spec['expected_count']) is not int or not 0 <= spec['expected_count'] <= 100
            or not isinstance(spec['expected_values'], dict) or len(spec['expected_values']) > 100
            or (spec['expected_count'] > 0 and not spec['expected_values'])):
        raise ValueError('comparison 必须提供 expected_count（0..100）和非空 expected_values；零记录可为空')
    for field, value in spec['expected_values'].items():
        if not isinstance(field, str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', field):
            raise ValueError('comparison 字段必须是字段技术名')
        values = value if isinstance(value, list) else [value]
        if any(type(v) not in (str, int, float, bool, type(None)) or
               (type(v) is float and not math.isfinite(v)) for v in values):
            raise ValueError('comparison 预期值仅支持 JSON 标量或标量列表')
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(payload.encode()) > 8192:
        raise ValueError('comparison 不能超过 8192 字节')
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_comparison(spec, fields, *, limit=None, offset=0):
    if spec is None:
        return
    comparison_digest(spec)
    if not set(spec['expected_values']).issubset(set(fields) | {'id'}):
        raise ValueError('comparison 预期字段必须显式包含在读取 fields 中')
    if limit is not None and (offset != 0 or limit <= spec['expected_count']):
        raise ValueError('comparison 必须从 offset=0 读取，limit 须大于 expected_count 以检测重复与截断')


def compare_records(spec, rows):
    if spec is None:
        return {}
    digest = comparison_digest(spec)

    def equal(actual, expected):
        # Odoo Many2one 返回 [id, 显示名]，预期用真实 ID；bool 不与0/1混同。
        if (type(expected) is int and isinstance(actual, list) and len(actual) == 2
                and type(actual[0]) is int and isinstance(actual[1], str)):
            actual = actual[0]
        if isinstance(expected, list) and isinstance(actual, list):
            return len(actual) == len(expected) and all(equal(a, e) for a, e in zip(actual, expected))
        return type(actual) is type(expected) and actual == expected

    passed = (len(rows) == spec['expected_count'] and all(
        field in row and equal(row[field], expected)
        for row in rows for field, expected in spec['expected_values'].items()))
    return {'comparison_digest': digest, 'comparison_status': 'passed' if passed else 'mismatch'}
