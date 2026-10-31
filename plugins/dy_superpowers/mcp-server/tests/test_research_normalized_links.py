"""指引只投影当前模板调研问题，不投影运行标识或附件结构。"""
from pathlib import Path
import pytest
from dy_superpowers_mcp.qa_templates import load_template

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize('module,count', [('expense', 25), ('general-ledger', 37)])
def test_each_group_projects_only_its_original_research_question(module, count):
    text = (ROOT / f'skills/module-research/references/{module}-research-question.md').read_text()
    template = load_template(module)
    assert len(template['questions']) == count
    groups = {q['check_order']: q for q in template['questions']}
    for key, q in groups.items():
        section = text.split(f'### {key} {q["config_item"]}\n', 1)[1].split('\n### ', 1)[0]
        assert section.strip() == q['question'].replace('\n', '<br>')
    assert '提问要素：' not in text and '原号' not in text
