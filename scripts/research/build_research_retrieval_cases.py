#!/usr/bin/env python3
"""将调研原题机械投影为评测清单；标注先于检索固定，缺口显式保留。"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'docs/research-sources/zoekt'


def build():
    previous = json.loads((AUDIT / 'natural-language-cases.json').read_text())
    targets = {key: previous['targets'][key] for key in
               ('doc_type', 'permission', 'season', 'credit', 'tax', 'analytic', 'cashflow', 'mapping', 'periodic')}
    # 仅为每题明确的规则子目标标注一个文件，不声称覆盖整题全部子问。
    extra = {
        'structure': ('account_structure.py', 'dy.fin.account.structure'),
        'type': ('account_type.py', 'dy.fin.account.type'),
        'distribution': ('account_distribution.py', 'dy.fin.account.distribution'),
        'account_permission': ('account_permissions_settings.py', 'dy.account.permissions.settings'),
        'year': ('account_fiscal_year.py', 'account.fiscal.year'),
        'analytic_item': ('account_analytic_account.py', 'account.analytic.account'),
        'voucher_type': ('dy_fin_voucher_type.py', 'dy.fin.voucher.type'),
    }
    for key, (name, symbol) in extra.items():
        targets[key] = {'path': 'dy-finance-dev/dy_fin_mdm/models/' + name, 'anchor': symbol}
    targets['expense_period'] = {'path': 'dy-expense-dev/dy_expense/models/expense_period.py',
                                 'anchor': 'def validate_expense_period(self, period_id):'}
    # id: 目标、固定查询、待核查规则范围。无标注题仍执行真实 MCP 查询，但不计算相关性。
    labels = {
        'DY-EXP-05': ('doc_type', 'dy.expense.page.configuration', '单据页面字段配置模型'),
        'DY-EXP-06': ('doc_type', 'dy.expense.document.type', '单据类型的公司关联'),
        'DY-EXP-09': ('doc_type', 'dy.expense.application.item', '申请项目与单据关联'),
        'DY-EXP-10': ('doc_type', 'dy.expense.item', '费用项目字段及关联'),
        'DY-EXP-11': ('season', 'low_season_ids', '城市淡旺季月份重叠约束；不覆盖跨月计算'),
        'DY-EXP-12': ('doc_type', 'dy.expense.policy', '政策公司、项目及政策行关联；不覆盖全部计费算法'),
        'DY-EXP-13': ('expense_period', 'validate_expense_period', '关账时费用制证检查；不回答当前期间状态'),
        'DY-EXP-19': ('tax', 'dy.expense.personal.tax.calculation.rule', '个税规则模型；不回答本期采用的计税方式'),
        'DY-EXP-20': ('permission', 'is_active', '授权有效状态计算；不回答实际授权有效性'),
        'DY-EXP-21': ('credit', 'extra_points', '信用评分加分约束；不回答本期分值'),
        'GL-2-005': ('structure', 'dy.fin.account.structure', '科目结构及公司分配'),
        'GL-2-006': ('type', 'dy.fin.account.type', '科目类型模型'),
        'GL-2-007': ('distribution', 'dy.fin.account.distribution', '科目分配属性及公司关联'),
        'GL-2-008': ('distribution', 'partner_unit_ids', '科目往来类别字段'),
        'GL-2-010': ('account_permission', 'dy.account.permissions.settings', '科目权限配置模型'),
        'GL-3-011': ('year', 'account.fiscal.year', '年度及期间模板字段；不回答本期上线日期'),
        'GL-5-015': ('analytic_item', 'account.analytic.account', '辅助核算项层级及公司字段'),
        'GL-5-016': ('analytic', 'dy.fin.account.analytic.plan', '辅助方案维度及科目关联'),
        'GL-6-020': ('voucher_type', 'dy.fin.voucher.type', '凭证类型模型及关联'),
        'GL-6-021': ('periodic', 'dy.fin.acc.account.periodicity.voucher', '凭证模板字段；不覆盖全部生成方式'),
        'GL-8-028': ('cashflow', 'dy.fin.cash.flow.mapping', '现金流项目与科目映射'),
        'GL-9-029': ('mapping', 'account.mapping', '财务与预算科目映射'),
    }
    data = {'format': 1, 'annotation':
        '完整抽取两份调研模板原文；人工预先标注22题的部分代码规则目标，其他题未标注且不计相关性。'
        '非独立专家、非留出集、非全部相关文件；固定技术关键词和模块是给定先验，不代表自动模型能力。'
        '所有问题的本期公司、人员、数值及启用选择仍需调研或实时MCP，源码不能作答。',
        'templates': [], 'targets': targets, 'cases': [], 'excluded': []}
    for module in ('expense', 'general-ledger'):
        path = ROOT / 'plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/data/research_templates' / (module + '.json')
        raw = path.read_bytes()
        data['templates'].append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest()})
        for question in json.loads(raw)['questions']:
            key = question['question_id']
            case = {'id': key, 'question': question['question'], 'config_item': question['config_item'],
                    'template_module': module}
            if question['question'].strip() == '/':
                data['excluded'].append({**case, 'reason': '模板占位符，无自然语言问题'})
                continue
            label = labels.get(key)
            case.update(kind='question' if label else 'unjudged', keyword=label[1] if label else question['config_item'],
                        module=targets[label[0]]['path'].split('/')[1] if label else None,
                        targets=[label[0]] if label else [],
                        scope_note=label[2] if label else '尚未人工标注相关代码；仅观察工具返回，不评价正确性')
            data['cases'].append(case)
    return data


if __name__ == '__main__':
    output = AUDIT / 'research-question-cases.json'
    output.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + '\n')
    print(output)
