"""问题指引只投影调研问题；主 Skill 保留批次确认、安全与分流合同。"""
from pathlib import Path
import pytest
from dy_superpowers_mcp.qa_templates import load_template

ROOT = Path(__file__).parents[2]
SKILL = ROOT / 'skills/module-research/SKILL.md'


@pytest.mark.parametrize('module,groups,attachments', [('expense',25,25), ('general-ledger',37,39)])
def test_builtin_qa_projects_every_current_business_question(module, groups, attachments):
    guide = (SKILL.parent / f'references/{module}-research-question.md').read_text()
    template = load_template(module)
    assert len(set(q['check_order'] for q in template['questions'])) == groups
    assert len(template['attachments']) == attachments
    for q in template['questions']:
        assert f'#### {q["question_id"]}' not in guide
        assert q['question'].replace('\n', '<br>') in guide
    assert '来源定位' not in guide and '验收定位' not in guide
    for label in ('配置路径：', '结论参考：', '参考答案：', '附件：', '## 附件字段', '提问要素：', '#### '):
        assert not any(line.startswith(label) for line in guide.splitlines())


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_inspection_uses_current_question_ids_and_dependency_blocks(module):
    text = (ROOT / f'skills/{module}-inspection/SKILL.md').read_text()
    assert 'question_id' in text and '当前模板的全部question_id' in text
    assert '分母取自prepare返回的expected_checks' in text
    assert '62' not in text
    assert '../../references/inspection-workflow.md' in text
    workflow = (ROOT / 'references/inspection-workflow.md').read_text()
    assert 'dy_odoo_environment_info' in workflow and '独立' in workflow
    assert 'requirements' in workflow and 'sha256' in workflow


@pytest.mark.parametrize('contract', [
    'dy_research_answers_save', 'dy_research_answers_import', 'template_revision',
    'expected_revision', 'batch_id', 'can_write_confirmed=false', '空白不删除',
    '同题冲突', '当前问题总数', '70%', '多范围分别导出', '不重复确认',
    '业务方明确确认完整', '旧格式不可回退', '省略attachments保留', '同步版本答卷不可原地改',
])
def test_module_research_exposes_versioned_batch_contract(contract):
    assert contract in SKILL.read_text()


def test_question_guides_remain_research_only_and_both_module_routes_resolve():
    text = SKILL.read_text()
    for module in ('expense', 'general-ledger'):
        relative = f'references/{module}-research-question.md'
        assert relative in text
        assert (SKILL.parent / relative).is_file()
    assert '问题文件仅投影模板中的调研问题' in text and '按问题开展多轮调研' in text
    assert '巡检' not in text and 'dy_inspection_' not in text


def test_candidate_batches_require_business_confirmation_save_authorization_and_readback():
    text = SKILL.read_text()
    for boundary in ('业务确认和保存授权分别满足', '只保存获批项', '暂缺项保留候选/待办',
                     '本批次已明确确认并保存时不重复问', '同范围dy_module_memory_read回读核对'):
        assert boundary in text


def test_followup_reuses_facts_and_keeps_question_rounds_small():
    text = SKILL.read_text()
    assert '复用已明确事实' in text and '一轮最多三个独立问题' in text
    assert '不设公司、项目、期间固定开场问卷' in text
    assert '不能因低进度丢弃' in text and '不循环索要' in text


def test_research_accepts_template_or_live_answers_with_matching_attachment_details():
    text = SKILL.read_text()
    for phrase in ('用户可填写模板，也可实时回答并提供附件', '两种方式可交替续答',
                   '当前问题关联的附件清单', '答卷对应附件页', '不把附件摘要代替应填明细',
                   '与文字答案一起展示并确认后保存', '回读答卷核对'):
        assert phrase in text


def test_internal_identifiers_are_only_for_tools_and_not_user_replies():
    text = SKILL.read_text()
    assert '内部标识仅用于工具调用和状态关联，不向用户展示' in text
    assert '不得出现在回复、问答卡片或结果说明中' in text
    assert '将内部状态转述为自然业务语言' in text


def test_research_preserves_workspace_memory_and_trace_privacy_boundaries():
    text = SKILL.read_text()
    assert '隐私、账号、密钥、生产原始明细不写记忆' in text
    assert '记忆仅当前工作区' in text
    assert 'trace正文脱敏且不展开附件明细' in text


def test_research_does_not_route_new_qa_to_legacy_write():
    text = SKILL.read_text()
    assert 'dy_research_record_write(' not in text
    assert '历史版本' in text and '续答读取当前版本，不自动回滚' in text
    assert 'legacy=true' not in text


def test_expense_attachment_catalog_preserves_actual_headers_and_no_samples():
    template = load_template('expense')
    text = (SKILL.parent / 'references/expense-research-question.md').read_text()
    assert len(template['attachments']) == 25
    assert '1022' not in text and 'XXX第一医院' not in text
    assert all(a['headers'] for a in template['attachments'])


def test_attachment_branches_have_one_business_question():
    points = [q for q in load_template('general-ledger')['questions'] if q['check_order']=='GL-8-024']
    assert len(points) == 1 and points[0]['question_id'] == 'GL-8-024'
    assert points[0]['rows'] == [48, 49]


def test_workflow_uses_projected_questions_and_reuses_shared_basis_after_confirmation():
    text = SKILL.read_text()
    assert '问题投影是实际调研问题清单' in text
    assert '不将附件分支或布局行拆成额外问题' in text
    assert '不把“/”作为问题展示' in text
    assert '共用依据仍须本轮确认后保存' in text
    assert '43/62' not in text and '44/62' not in text
