"""验证模块QA、矩阵要素与真实附件字段表的关联完整性。"""
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / 'mcp-server/src'))
from dy_superpowers_mcp.inspection import _matrix


@pytest.mark.parametrize('module,count', [('expense', 92), ('general-ledger', 81)])
def test_each_matrix_element_resolves_to_qa_and_existing_attachment_fields(module, count):
    text = (ROOT / f'skills/module-research/references/{module}-research-guide.md').read_text()
    heading = '| 模块 | 检查顺序 | 子项 | 提取指引要素 | 关联附件清单 | 来源定位 | 验收定位 | 关联边界 |'
    assert heading in text, '缺少可逐项回链的归一化关联表'
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) == 8 and cells[0] == module:
            rows.append(cells)
    assert len(rows) == count
    assert len({(r[1], r[2]) for r in rows}) == count
    assert {(r[1], r[2]) for r in rows} == set(_matrix(module)[1])
    # 关联表不能只覆盖键，却错填相邻子项的提取要素。
    elements = {}
    matrix = ROOT / f'skills/{module}-inspection/references/inspection-matrix.md'
    for line in matrix.read_text().splitlines():
        if line.startswith('## 检查顺序 '):
            order = line.removeprefix('## 检查顺序 ').strip()
        elif match := re.match(r'^\|\s*(\d+)｜([^|]+)\|', line):
            elements[order, match[1]] = match[2].strip().split('<br>')[0]
    assert {(r[1], r[2]): r[3] for r in rows} == elements
    if module == 'general-ledger':
        targets = set(re.findall(r'^### 模板：(.+)$', text, re.M))
        qa = set(re.findall(r'^\| (EXP-[0-9]+(?:-[0-9]+)+) \|', text, re.M))
        # 补充QA必须有实际问题，不能为满足覆盖率虚构Excel行号。
        supplements = re.findall(r'^\| general-ledger \| (EXP-[0-9]+(?:-[0-9]+)+) \| (问：[^|]+) \| 矩阵补充（非源QA） \|$', text, re.M)
        assert [key for key, _ in supplements] == ['EXP-5-017']
        qa.update(key for key, _ in supplements)
    else:
        targets = {f'{sheet}!{cell}' for sheet, cell in re.findall(
            r'^\| (\d{2}_[^|]+?) \| (A\d+)：', text, re.M)}
        qa = {str(int(x)) for x in re.findall(r'^\| DY-(\d+) \|', text, re.M)}
    for _, order, item, element, attachments, source, acceptance, boundary in rows:
        assert order in qa
        assert element and source and acceptance and boundary
        for attachment in attachments.split('<br>'):
            assert attachment in targets or attachment in {
                '待收集（来源未提供明细结构）', '无需附件（保留回答依据）'}
    if module == 'general-ledger':
        first = next(r for r in rows if r[1:3] == ['EXP-1-001', '01'])
        assert first[4] == '公司应用清单'
        numbering = next(r for r in rows if r[1:3] == ['EXP-6-018', '01'])
        assert numbering[4] == '凭证编号规则' and '别名' in numbering[7]
        printing = next(r for r in rows if r[1:3] == ['EXP-12-037', '01'])
        assert printing[4] == '待收集（来源未提供明细结构）'
        combinations = [r for r in rows if r[1] == 'EXP-5-017']
        assert len(combinations) == 2
        assert all('矩阵补充' in r[5] and '源QA缺失' in r[7] for r in combinations)
        assert 'EXP-8-026-02' in text and '当前矩阵无对应键' in text
        assert all(r[6] == f'总账巡检矩阵：{r[1]}/{r[2]}' for r in rows)
        assert first[5] == '总账调研清单!A4:E6'
        assert printing[5] == '总账调研清单!A65:E65'
    else:
        last = next(r for r in rows if r[1:3] == ['28', '03'])
        assert last[5:7] == ['费用调研清单!G95', '费用模块巡检Skill!K93']
        assert '仅数量汇总' in last[7]
