"""模块标识取自已发布模板目录，不在代码中写死。"""
import json

import server
from dy_superpowers_mcp import qa_templates


def test_module_enum_follows_published_template_catalog():
    published = tuple(sorted(path.stem for path in qa_templates.TEMPLATE_DIR.glob('*.json')))
    assert published and qa_templates.template_modules() == published
    for name in ('dy_research_answers_save', 'dy_inspection_prepare', 'dy_knowledge_community_gaps'):
        assert server.TOOL_SCHEMAS[name]['properties']['module']['enum'] == list(published)


def test_new_catalog_entry_is_loadable_without_code_change(tmp_path, monkeypatch):
    (tmp_path / 'demo.json').write_text(json.dumps({'module': 'demo', 'questions': []}), encoding='utf-8')
    monkeypatch.setattr(qa_templates, 'TEMPLATE_DIR', tmp_path)
    qa_templates.template_modules.cache_clear()
    try:
        assert qa_templates.template_modules() == ('demo',)
        assert qa_templates.load_template('demo')['module'] == 'demo'
    finally:
        qa_templates.template_modules.cache_clear()
