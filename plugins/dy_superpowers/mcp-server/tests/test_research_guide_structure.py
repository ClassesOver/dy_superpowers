"""调研指引共用结构，模块字段仍按真实来源保留。"""
from pathlib import Path
import re

import pytest

SKILL = Path(__file__).parents[2] / 'skills/module-research'
SECTIONS = ['来源与覆盖', '模块 QA', '附件清单与存储结构', '多轮整理与记忆', '来源缺口']


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_research_guides_share_sections_without_losing_source_tables(module):
    text = (SKILL / 'references' / f'{module}-research-guide.md').read_text()
    assert re.findall(r'^## (.+)$', text, re.MULTILINE) == SECTIONS
    assert 'evidence_ref' in text
    assert 'scope_id' in text and 'expected_revision' in text
    if module == 'general-ledger':
        assert len(re.findall(r'^### 模板：', text, re.MULTILINE)) == 38
        assert len(set(re.findall(r'^\| (EXP-[0-9]+(?:-[0-9]+)+) \|', text, re.MULTILINE))) == 37


def test_local_agent_rules_define_normalization_and_source_boundaries():
    rules = (SKILL / 'AGENTS.md').read_text()
    for section in SECTIONS:
        assert section in rules
    assert '字段顺序' in rules and '合并单元格' in rules
    assert '示例' in rules and '项目事实' in rules
    assert 'research-checklist.md' in rules
