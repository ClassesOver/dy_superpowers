"""HRP 方案候选分析；只生成计划，不执行变更。"""
from . import knowledge
from .odoo_jsonrpc import OdooConfigurationError


def analyze(description):
    modules = {}
    for word in [description[start:start + 1024] for start in range(0, len(description), 1024)]:
        for row in knowledge.search(word, knowledge_type='summary')['results']:
            modules[row['module']] = {'module': row['module'], 'reason': '关键词匹配候选，需业务确认',
                                      'reference': row['reference'], 'confirmed': False}
    return {'description': description, 'modules_needed': list(modules.values()),
            'questions': ['请确认本期范围、例外及候选模块是否确实需要交付。'],
            'execution_authorized': False, 'limitations': '关键词候选不等于确认需求；不写入调研记忆。'}


def design(requirements, user_answers=None):
    candidates = requirements.get('modules_needed', [])
    if not isinstance(candidates, list) or any(not isinstance(r, dict) or not isinstance(r.get('module'), str) for r in candidates):
        raise OdooConfigurationError('候选模块必须为包含模块名的列表')
    names = [r['module'] for r in requirements.get('modules_needed', []) if isinstance(r, dict) and isinstance(r.get('module'), str)]
    answers = user_answers or {}
    checks = answers.get('configuration_checks', [])
    if not isinstance(checks, list):
        raise OdooConfigurationError('configuration_checks 必须为列表')
    steps = []
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get('model'), str)
                or type(check.get('company_id')) is not int or check['company_id'] < 1
                or not isinstance(check.get('domain'), list)
                or not isinstance(check.get('expected_values'), dict) or not check['expected_values']):
            raise OdooConfigurationError('配置核对须明确模型、公司ID、domain和非空预期值')
        expected = dict(check['expected_values'])
        if 'company_id' in expected and (type(expected['company_id']) is not int or expected['company_id'] != check['company_id']):
            raise OdooConfigurationError('配置预期公司与核对公司冲突')
        expected['company_id'] = check['company_id']
        comparison = {'expected_count': check.get('expected_count', 1), 'expected_values': expected}
        from .odoo_comparison import comparison_digest
        try:
            comparison_digest(comparison)
            from .odoo_jsonrpc import validate_read_arguments
            validate_read_arguments({'model': check['model'], 'args': [check['domain']],
                'kwargs': {'fields': list(expected), 'limit': comparison['expected_count'] + 1}})
        except ValueError as exc:
            raise OdooConfigurationError(str(exc)) from exc
        steps.extend([
            {'tool': 'dy_odoo_model_fields', 'params': {'model': check['model']}},
            {'tool': 'dy_odoo_record_search', 'params': {'model': check['model'],
             'args': [[['company_id', '=', check['company_id']], *check['domain']]],
             'kwargs': {'fields': list(expected), 'limit': comparison['expected_count'] + 1},
             'comparison': comparison}}])
    missing = []
    if not names:
        missing.append('缺少经业务确认的候选模块；须核对当前已安装模块与交付范围。')
    if not checks:
        missing.append('缺少结构化配置预期：模型、公司ID、查询条件、数量及字段预期值。')
    blockers = ['尚未执行环境、字段、模块依赖及配置回读核对；须处理冲突后再确认变更。', *missing]
    return {'phases': [
        {'phase': 1, 'name': '确认需求与环境', 'depends_on': [], 'steps': [
            {'tool': 'dy_odoo_diagnostics_health_check', 'params': {}}]},
        {'phase': 2, 'name': '核对模块与依赖', 'depends_on': [1], 'steps': [
            {'tool': 'dy_odoo_module_info', 'params': {'module': name}} for name in names] or [
            {'tool': 'dy_odoo_module_list_installed', 'params': {}}]},
        {'phase': 3, 'name': '只读核对配置预期', 'depends_on': [2], 'steps': steps or [
            {'tool': 'dy_odoo_model_list', 'params': {'limit': 20}}]}],
        'missing_information': missing, 'blockers': blockers, 'implementation_ready': False,
        'unresolved': ['候选模块需求、安装依赖、配置方案及验收样例仍须确认。'],
        'user_answers': user_answers or {}, 'execution_authorized': False,
        'limitations': '仅生成核对计划；不自动安装模块、执行写入或宣称需求已确认。'}


def validate(plan, available_tools):
    errors, warnings = [], []
    phases = plan.get('phases')
    if not isinstance(phases, list) or not phases:
        return {'valid': False, 'errors': ['计划必须包含阶段'], 'execution_authorized': False}
    ids = [p.get('phase') for p in phases if isinstance(p, dict)]
    if len(ids) != len(phases) or any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids):
        return {'valid': False, 'errors': ['阶段编号必须唯一且为正整数'], 'execution_authorized': False}
    for phase in phases:
        deps = phase.get('depends_on', [])
        if not isinstance(deps, list) or any(type(d) is not int or d not in ids or d >= phase['phase'] for d in deps):
            errors.append('依赖缺失、循环或顺序无效')
        steps = phase.get('steps')
        if not isinstance(steps, list):
            errors.append('步骤必须为列表')
            continue
        for step in steps:
            if (not isinstance(step, dict) or not isinstance(step.get('tool'), str)
                    or step['tool'] not in available_tools):
                errors.append('计划包含未接入工具')
                continue
            from jsonschema import Draft202012Validator
            schema = available_tools[step['tool']]
            params = step.get('params', {})
            if not isinstance(params, dict) or next(Draft202012Validator(schema).iter_errors({'trace_id': 'plan-validation', **params}), None):
                errors.append('步骤参数不符合实际工具合同')
            if any(s in step['tool'] for s in ('install', 'upgrade', 'uninstall', 'create', 'update', 'execute', 'delete')):
                warnings.append('包含潜在变更；执行前仍须独立预览、环境核对及用户授权')
    if plan.get('blockers'):
        warnings.append('计划有未解决阻塞项，只可用于核对，不表示可以实施')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings, 'execution_authorized': False,
            'implementation_ready': False,
            'limitations': '静态结构校验不证明部署版本、权限、依赖完整或业务验收通过。'}
