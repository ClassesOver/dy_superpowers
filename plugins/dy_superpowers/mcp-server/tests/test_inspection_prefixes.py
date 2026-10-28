"""业务展示编号分域，机器数据继续使用既有检查键。"""
import json

import pytest

from dy_superpowers_mcp.inspection import _matrix
from dy_superpowers_mcp.reports import _render_report


@pytest.mark.parametrize('module,order,item,expected', [
    ('expense', '1', '01', r'EXP\-001\-01'),
    ('expense', '28', '03', r'EXP\-028\-03'),
    ('general-ledger', 'GL-1-001', '01', r'GL\-001\-01'),
    ('general-ledger', 'GL-10-032', '02', r'GL\-032\-02'),
    ('general-ledger', 'GL-8-026-02', '01', r'GL\-026\-02\-01'),
])
def test_business_report_uses_domain_prefix_and_keeps_original_keys(module, order, item, expected):
    record = {'module': module, 'check_order': order, 'research_id': item,
              'result': '待确认', 'reason': '待补充范围', 'scope_id': 'scope-a', 'environment': 'UAT'}
    report = {'expected_checks': [record.copy()], 'records': [record]}
    validation = {'gate': '待确认', 'valid': True, 'validated_at': '2026-10-26T00:00:00+00:00',
                  'matrix_revisions': {module: 'a' * 64}}
    text = _render_report(module, 'test-report', report, validation)
    prose, machine = text.split('## 完整检查数据（机器可读）')
    assert f'### {expected}｜待确认' in prose
    assert f'| {expected} |' in prose
    data = json.loads(machine.split('```json\n')[1].rsplit('```', 1)[0])
    assert data == report
    assert record['check_order'] == order


def test_all_current_matrix_keys_have_unique_business_identifiers():
    from dy_superpowers_mcp.reports import _check_label

    labels = []
    for module in ('expense', 'general-ledger'):
        for order, item in _matrix(module)[1]:
            labels.append(_check_label(module, {'check_order': order, 'research_id': item}))
    assert len(labels) == 174
    assert len(labels) == len(set(labels))
    assert all(label.startswith(('GL-', 'EXP-')) and ' + ' not in label for label in labels)
