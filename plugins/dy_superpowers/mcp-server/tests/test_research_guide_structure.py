"""调研指引共用结构，模块字段仍按真实来源保留。"""
from pathlib import Path
import re

import pytest

SKILL = Path(__file__).parents[2] / 'skills/module-research'
SECTIONS = ['使用边界', '模块 QA', '提取要素与矩阵映射', '附件清单与存储结构', '多轮整理与记忆', '补问与巡检衔接', '输出要求']


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_research_guides_share_sections_without_losing_source_tables(module):
    text = (SKILL / 'references' / f'{module}-research-guide.md').read_text()
    assert re.findall(r'^## (.+)$', text, re.MULTILINE) == SECTIONS
    assert 'evidence_ref' in text
    assert 'scope_id' in text and 'expected_revision' in text
    if module == 'general-ledger':
        assert len(re.findall(r'^### 模板：', text, re.MULTILINE)) == 39
        assert len(set(re.findall(r'^\| (GL-[0-9]+(?:-[0-9]+)+) \|', text, re.MULTILINE))) == 37
    else:
        assert len(re.findall(r'^### 模板：', text, re.MULTILINE)) == 19
        assert '### 模板：18_费用项目字段明细' in text
        assert '### 模板：19_申请项目字段明细' in text


def test_local_agent_rules_define_normalization_and_source_boundaries():
    rules = (SKILL.parents[3] / 'AGENTS.md').read_text()
    assert 'docs/research-sources/' in rules
    assert '来源审计信息' in rules
    assert '记忆只属于当前工作区' in rules


def test_general_ledger_skill_reference_excludes_source_audit_metadata():
    text = (SKILL / 'references' / 'general-ledger-research-guide.md').read_text()

    for source_detail in (
        '来源与覆盖', '来源缺口', '来源定位', '验收定位', '来源工作表',
        '总账模块巡检skill手册（更新调研问题指引与存数模板）_V2.1.xlsx',
        '总账调研清单!A4:E6', 'V2.1', '解析完整性',
    ):
        assert source_detail not in text


def test_repository_rules_keep_source_audit_in_local_docs():
    rules = (SKILL.parents[3] / 'AGENTS.md').read_text()
    assert 'docs/research-sources/' in rules
    assert '来源审计信息' in rules
