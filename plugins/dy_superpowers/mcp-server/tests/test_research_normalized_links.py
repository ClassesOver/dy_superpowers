"""验证模块QA、矩阵要素与真实附件字段表的关联完整性。"""
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / 'mcp-server/src'))
from dy_superpowers_mcp.inspection import _matrix


@pytest.mark.parametrize('module,count', [('expense', 92), ('general-ledger', 82)])
def test_each_matrix_element_resolves_to_qa_and_existing_attachment_fields(module, count):
    text = (ROOT / f'skills/module-research/references/{module}-research-guide.md').read_text()
    heading = ('| 模块 | 检查顺序 | 子项 | 提取指引要素 | 关联附件清单 | 处理规则 |'
               if module == 'general-ledger'
               else '| 模块 | 检查顺序 | 子项 | 提取指引要素 | 关联附件模板 | 记忆要求 |')
    assert heading in text, '缺少可逐项回链的归一化关联表'
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) == 6 and cells[0] == module:
            rows.append(cells)
    assert len(rows) == count
    assert len({(r[1], r[2]) for r in rows}) == count
    actual_keys = {(r[1], r[2]) for r in rows}
    expected_keys = set(_matrix(module)[1])
    if module == 'expense':
        expected_keys = {(f'EXP-{int(order):03d}', item) for order, item in expected_keys}
    assert actual_keys == expected_keys
    # 关联表不能只覆盖键，却错填相邻子项的提取要素。
    elements = {}
    matrix = ROOT / f'skills/{module}-inspection/references/inspection-matrix.md'
    for line in matrix.read_text().splitlines():
        if line.startswith('## 检查顺序 '):
            order = line.removeprefix('## 检查顺序 ').strip()
        elif match := re.match(r'^\|\s*(\d+)｜([^|]+)\|', line):
            elements[order, match[1]] = match[2].strip().split('<br>')[0]
    if module == 'expense':
        elements = {(f'EXP-{int(order):03d}', item): value for (order, item), value in elements.items()}
    actual_elements = {(r[1], r[2]): r[3] for r in rows}
    if module == 'expense':
        assert all(actual_elements[key].startswith(value) for key, value in elements.items())
    else:
        assert actual_elements == elements
    if module == 'general-ledger':
        targets = set(re.findall(r'^### 模板：(.+)$', text, re.M))
        qa = set(re.findall(r'^\| (GL-[0-9]+(?:-[0-9]+)+) \|', text, re.M))
        # 补充QA必须有实际问题，不能为满足覆盖率虚构Excel行号。
        supplements = re.findall(r'^\| general-ledger \| (GL-[0-9]+(?:-[0-9]+)+) \| (问：[^|]+) \| 先补问，再按子项分别确认 \|$', text, re.M)
        assert [key for key, _ in supplements] == ['GL-5-017']
        qa.update(key for key, _ in supplements)
    else:
        targets = set(re.findall(r'^### 模板：(.+)$', text, re.M))
        qa = set(re.findall(r'^\| (EXP-\d{3}) \|', text, re.M))
    for row in rows:
        _, order, item, element, attachments = row[:5]
        assert order in qa
        assert element and row[5]
        for attachment in attachments.split('<br>'):
            assert attachment in targets or attachment in {
                '待收集（字段结构待确认）', '无需附件（保留回答依据）'}
    if module == 'general-ledger':
        first = next(r for r in rows if r[1:3] == ['GL-1-001', '01'])
        assert first[4] == '公司应用清单'
        numbering = next(r for r in rows if r[1:3] == ['GL-6-018', '01'])
        assert numbering[4] == '凭证编号规则' and '历史衔接须补问' in numbering[5]
        printing = next(r for r in rows if r[1:3] == ['GL-12-037', '01'])
        assert printing[4] == '凭证打印样张及要求'
        combinations = [r for r in rows if r[1] == 'GL-5-017']
        assert len(combinations) == 2
        assert all('补问' in r[5] for r in combinations)
        assert 'GL-8-026-02' in text
        assert '独立检查键' in next(r for r in rows if r[1:3] == ['GL-8-026-02', '01'])[5]
    else:
        last = next(r for r in rows if r[1:3] == ['EXP-028', '03'])
        assert last[5]  # Fees retain their existing operational normalization notes.
        assert '仅保存已确认事实' in last[5]
