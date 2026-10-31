"""Odoo 能力的工具合同；技术实现按职责拆分。"""
TRACE = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
MODEL = {'type': 'string', 'pattern': '^[a-z][a-z0-9_.]{0,127}$'}
PAGE = {'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}
BOOL = {'type': 'boolean'}
POSITIVE = {'type': 'integer', 'minimum': 1}
WRITE = dict(environment=TEXT, confirmed=BOOL, dry_run=BOOL,
             confirmation_digest={'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
             idempotency_key={'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,100}$'})


def schema(properties, required=()):
    return {'type': 'object', 'additionalProperties': False, 'required': ['trace_id', *required],
            'properties': {'trace_id': TRACE, **properties}}


SCHEMAS = {
    'dy_odoo_module_list_available': schema({**PAGE, 'category': TEXT}),
    'dy_odoo_model_list': schema({**PAGE, 'search': TEXT, 'transient': BOOL}),
    'dy_odoo_model_search_field': schema({**PAGE, 'query': TEXT, 'model': MODEL}, ['query']),
    'dy_odoo_diagnostics_health_check': schema({}),
    'dy_knowledge_community_gaps': schema({'module': {'enum': ['expense', 'general-ledger']}, 'scope_id': TEXT}, ['module', 'scope_id']),
    'dy_odoo_import_template': schema({'model': MODEL, 'include_optional': BOOL}, ['model']),
    'dy_odoo_import_preview': schema({'model': MODEL, 'company_id': POSITIVE,
        'csv_data': {'type': 'string', 'minLength': 1, 'maxLength': 1048576}, 'environment': TEXT,
        'idempotency_key': WRITE['idempotency_key']}, ['model', 'company_id', 'csv_data']),
    'dy_odoo_import_execute': schema({**WRITE, 'model': MODEL, 'company_id': POSITIVE,
        'csv_data': {'type': 'string', 'minLength': 1, 'maxLength': 1048576}},
        ['model', 'company_id', 'csv_data', 'environment', 'idempotency_key', 'confirmed', 'confirmation_digest']),
    'dy_odoo_analyze_requirements': schema({'description': {'type': 'string', 'minLength': 1, 'maxLength': 16000}}, ['description']),
    'dy_odoo_design_solution': schema({'requirements': {'type': 'object'}, 'user_answers': {'type': 'object'}}, ['requirements']),
    'dy_odoo_validate_plan': schema({'plan': {'type': 'object'}}, ['plan']),
    'dy_odoo_record_execute': schema({'model': {'enum': ['ir.module.module']},
        'method': {'enum': ['check_access_rights']}}, ['model', 'method']),
}
for operation in ('install', 'upgrade', 'uninstall'):
    SCHEMAS['dy_odoo_module_' + operation] = schema({**WRITE,
        'module_names': {'type': 'array', 'items': {'type': 'string', 'pattern': '^[a-z][a-z0-9_]*$'}, 'minItems': 1, 'maxItems': 50},
        'backup_reference': TEXT}, ['module_names', 'environment', 'idempotency_key', 'backup_reference'])

DESCRIPTIONS = {
    'dy_odoo_module_list_available': '分页查询当前系统可见模块及安装状态，可按分类筛选。',
    'dy_odoo_model_list': '分页发现当前系统模型，默认不含临时模型。',
    'dy_odoo_model_search_field': '跨模型检索字段名称及业务描述，返回关系与类型。',
    'dy_odoo_diagnostics_health_check': '只读检查认证、服务版本和模块读取；宿主与数据库直连不检查。',
    'dy_knowledge_community_gaps': '结合当前确认答卷、知识及已安装模块生成待核实差异，不作缺陷结论。',
    'dy_odoo_import_template': '依据配置创建白名单及线上字段生成CSV表头，不支持业务单据导入。',
    'dy_odoo_import_preview': '校验配置CSV候选；提供环境和批次键后生成精确确认摘要，不写入。',
    'dy_odoo_import_execute': '经确认逐行创建白名单配置；默认关闭，非整批事务，已尝试批次不重放。',
    'dy_odoo_analyze_requirements': '结合本地知识提取需求关键词及候选模块，不确认需求或执行变更。',
    'dy_odoo_design_solution': '根据候选需求生成分阶段核对计划，不执行或授权实施。',
    'dy_odoo_validate_plan': '静态校验阶段依赖与实际工具参数，不证明环境或业务验收通过。',
    'dy_odoo_record_execute': '仅执行固定模型的只读权限检查；不开放任意RPC或业务动作。',
    **{'dy_odoo_module_' + op: '模块' + label + '：默认返回固定确认模板及摘要；安装预览只读解析线上依赖和安装顺序，执行前重核完整清单；执行须受信任环境开关、精确白名单、备份引用和用户确认。'
       for op, label in [('install', '安装'), ('upgrade', '升级'), ('uninstall', '卸载')]},
    'dy_odoo_module_upgrade': '模块升级仅提供固定确认预览，实际执行暂停：原生刷新模块清单后可能新增批外反向依赖，当前无法证明完整影响范围。',
}
