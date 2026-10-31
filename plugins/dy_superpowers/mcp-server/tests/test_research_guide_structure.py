"""调研指引完整投影模板问题，运行结构与审计资料保留各自边界。"""
from pathlib import Path
import re
import pytest
from dy_superpowers_mcp.qa_templates import load_template

SKILL = Path(__file__).parents[2] / 'skills/module-research'


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_research_guides_only_organize_current_questions_by_configuration_group(module):
    text = (SKILL / 'references' / f'{module}-research-question.md').read_text()
    template = load_template(module)
    assert re.findall(r'^## (.+)$', text, re.M) == ['模块 QA']
    assert not re.findall(r'^#### ', text, re.M)
    assert all(f'#### {q["question_id"]}' not in text for q in template['questions'])
    groups = {q['check_order']: q['config_item'] for q in template['questions']}
    assert re.findall(r'^### (.+)$', text, re.M) == [f'{key} {item}' for key, item in groups.items()]
    assert '配置路径：' not in text


def test_local_agent_rules_define_normalization_and_source_boundaries():
    rules = (SKILL.parents[3] / 'AGENTS.md').read_text()
    assert 'docs/research-sources/' in rules and '来源审计信息' in rules
    assert '记忆只属于当前工作区' in rules


def test_general_ledger_skill_reference_excludes_source_audit_metadata():
    text = (SKILL / 'references/general-ledger-research-question.md').read_text()
    for detail in ('来源定位', '验收定位', '来源工作表', 'V2.1', '解析完整性'):
        assert detail not in text


def test_repository_rules_keep_source_audit_in_local_docs():
    rules = (SKILL.parents[3] / 'AGENTS.md').read_text()
    assert 'docs/research-sources/' in rules and '来源审计信息' in rules
