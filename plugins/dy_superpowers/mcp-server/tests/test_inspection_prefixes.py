"""报告保留当前模板问题与原始编号，模块间身份不冲突。"""
import json
import pytest
from dy_superpowers_mcp.qa_templates import load_template
from dy_superpowers_mcp.reports import _render_report
from dy_superpowers_mcp.inspection import validate_inspection
from template_qa_helpers import make_report


@pytest.mark.parametrize('module',['expense','general-ledger'])
def test_business_report_keeps_original_question_ids_and_machine_data(tmp_path,module):
    report=make_report(tmp_path,module)
    validation=validate_inspection(tmp_path,report)
    assert validation['valid'],validation['errors']
    text=_render_report(module,'test-report',report,validation)
    prose,machine=text.split('## 机器可读数据')
    data=json.loads(machine.split('```json\n')[1].rsplit('```',1)[0])
    assert data==report
    assert '优先级' not in prose
    assert len(data['records'])==len(load_template(module)['questions'])
    assert all(q['question_id'] in json.dumps(data) for q in load_template(module)['questions'])


def test_grouped_questions_keep_current_identity_without_duplicate_subitems(tmp_path):
    report=make_report(tmp_path,'general-ledger')
    text=_render_report('general-ledger','duplicate',report,validate_inspection(tmp_path,report))
    template=load_template('general-ledger')
    group=next(q for q in template['questions'] if q['check_order']=='GL-8-024')
    assert group['question_id']=='GL-8-024'
    assert group['question'] in text
    assert 'GL-8-024:01' not in text and 'GL-8-024:02' not in text
    templates=[load_template(module) for module in ('expense','general-ledger')]
    ids=[q['question_id'] for template in templates for q in template['questions']]
    assert len(ids)==len(set(ids))==sum(len(t['questions']) for t in templates)
