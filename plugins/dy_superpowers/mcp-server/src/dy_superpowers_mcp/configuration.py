"""默认关闭、绑定确认内容的少量 HRP 配置写入；不提供通用 RPC。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .odoo_jsonrpc import OdooConfigurationError, OdooRPCError, OdooReadClient, OdooSettings
from .odoo_comparison import compare_records
from .qa_basis import READY, configured_environment_info, confirmed_answer
from .storage import atomic_write, record_lock


# 固定配置模型；元数据仍须在目标账套核对，不能由 Knowledge 扩大授权。
POLICIES = {
    'dy.expense.personal.tax.calculation.rule': {
        'module': 'dy_expense', 'version': '1.0', 'scope': 'company_id', 'key': 'name',
        'fields': {'name': 'char', 'company_id': 'many2one', 'effective_date': 'date',
                   'expiration_date': 'date', 'cal_type': 'selection', 'resident_flag': 'boolean'}},
    'dy.fin.account.analytic.plan': {
        'module': 'dy_fin_mdm', 'version': '0.1', 'scope': 'company_id', 'key': 'code',
        'fields': {'name': 'char', 'code': 'char', 'company_id': 'many2one'}},
    'dy.expense.document.type': {
        'module': 'dy_expense', 'version': '1.0', 'scope': 'company_ids', 'update_only': True,
        'fields': {'name': 'char', 'is_active': 'boolean', 'fund_application_control': 'boolean',
                   'exp_req_doc_type_id': 'many2one', 'approval_control': 'boolean',
                   'account_auditor_control': 'boolean', 'payment_control': 'boolean',
                   'is_calculator_tax': 'boolean', 'enable_reimbursement_detail': 'boolean',
                   'is_share_expenses': 'boolean', 'is_required_attachment': 'boolean'}},
}

_PROPERTIES = {
    'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
    'environment': {'type': 'object', 'additionalProperties': False, 'required': ['url', 'database'],
                    'properties': {'url': {'type': 'string', 'minLength': 1}, 'database': {'type': 'string', 'minLength': 1}}},
    'model': {'type': 'string', 'enum': list(POLICIES)},
    'company_id': {'type': 'integer', 'minimum': 1},
    'values': {'type': 'object', 'minProperties': 1},
    'idempotency_key': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
    'confirmed': {'type': 'boolean'},
    'confirmation_digest': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
    'dry_run': {'type': 'boolean'},
}
CONFIGURATION_SCHEMAS = {
    operation: {'type': 'object', 'additionalProperties': False,
                'required': ['trace_id', 'environment', 'model', 'company_id', 'values', 'idempotency_key', 'confirmed']
                            + (['record_id', 'expected_values', 'expected_write_date'] if operation == 'update' else []),
                'properties': {**_PROPERTIES, **({
                    'record_id': {'type': 'integer', 'minimum': 1},
                    'expected_values': {'type': 'object', 'minProperties': 1},
                    'expected_write_date': {'type': 'string', 'minLength': 1},
                } if operation == 'update' else {})}}
    for operation in ('create', 'update')
}


# 答卷驱动配置的依据：只绑定答卷自身身份与环境，不依赖巡检准备结果。
ANSWER_CONTEXT_FIELDS = {'module', 'scope_id', 'template_revision', 'question_id', 'answer_revision', 'environment'}


def _confirmation_payload(arguments):
    payload = {key: value for key, value in arguments.items()
               if key not in {'trace_id', 'trace_context', 'confirmed', 'confirmation_digest', 'dry_run'}}
    context = arguments.get('trace_context', {})
    if isinstance(context, dict) and set(context) & (ANSWER_CONTEXT_FIELDS - {'environment'}):
        payload['trace_context'] = {key: value for key, value in context.items() if key in ANSWER_CONTEXT_FIELDS}
    return payload


def confirmation_digest(operation: str, arguments: dict) -> str:
    """操作、业务参数、关联答卷身份及服务连接身份 UTF-8 规范 JSON 的 SHA-256。"""
    payload = _confirmation_payload(arguments)
    fingerprint = configured_environment_info()['connection_fingerprint']
    content = json.dumps({'operation': operation, **payload, 'connection_fingerprint': fingerprint},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _validate_answer_context(workspace, arguments):
    context = _confirmation_payload(arguments).get('trace_context')
    if context is None:
        return None
    if set(context) != ANSWER_CONTEXT_FIELDS or any(not isinstance(v, str) or not v for v in context.values()):
        raise OdooConfigurationError('答卷驱动配置必须提供完整答卷依据：模块、范围、模板版本、问题、答案修订及环境')
    if context['environment'] != configured_environment_info()['environment']:
        raise OdooConfigurationError('配置答卷依据的环境与当前服务配置身份不一致')
    try:
        current = confirmed_answer(workspace, context['module'], context['scope_id'], context['question_id'])
    except ValueError as exc:
        raise OdooConfigurationError('配置答卷依据无效，请重新读取答卷') from exc
    if any(context[key] != current[key] for key in ('module', 'scope_id', 'template_revision', 'answer_revision')):
        raise OdooConfigurationError('配置答卷版本已变化，请重新读取答卷并确认')
    if current['readiness'] != READY:
        raise OdooConfigurationError('配置所依据的确认答案或附件未就绪，请重新读取答卷')
    return context


def _positive(value):
    return type(value) is int and value > 0


def _relation(value):
    if isinstance(value, list) and len(value) == 2 and _positive(value[0]):
        return value[0]
    return value


def _validate(operation, arguments):
    if operation not in CONFIGURATION_SCHEMAS or not isinstance(arguments, dict):
        raise OdooConfigurationError('仅允许配置 create/update')
    allowed = set(CONFIGURATION_SCHEMAS[operation]['properties']) | {'trace_context'}
    if set(arguments) - allowed:
        raise OdooConfigurationError('配置参数含未允许的字段或上下文')
    model, values, company = arguments.get('model'), arguments.get('values'), arguments.get('company_id')
    if model not in POLICIES or not isinstance(values, dict) or not values or not _positive(company):
        raise OdooConfigurationError('配置模型、字段或公司不合法')
    policy = POLICIES[model]
    if operation == 'create' and policy.get('update_only'):
        raise OdooConfigurationError('该配置仅允许更新，创建所需明细不在白名单内')
    if set(values) - set(policy['fields']):
        raise OdooConfigurationError('字段不在固定配置白名单内')
    if 'company_id' in values and values['company_id'] != company:
        raise OdooConfigurationError('禁止更改公司或跨公司配置')
    if operation == 'create' and values.get('company_id') != company:
        raise OdooConfigurationError('创建必须明确目标公司')
    env = arguments.get('environment')
    if (not isinstance(env, dict) or set(env) != {'url', 'database'}
            or not all(isinstance(value, str) and value for value in env.values())):
        raise OdooConfigurationError('必须明确环境 URL 和数据库')
    if not isinstance(arguments.get('idempotency_key'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', arguments['idempotency_key']):
        raise OdooConfigurationError('幂等键必须是稳定的短标识')
    if operation == 'update':
        expected = arguments.get('expected_values')
        if (not _positive(arguments.get('record_id')) or not isinstance(expected, dict)
                or set(expected) != set(values) or not isinstance(arguments.get('expected_write_date'), str)
                or not arguments['expected_write_date']):
            raise OdooConfigurationError('更新必须提供逐字段原值与 expected_write_date')
    for snapshot in (values, arguments['expected_values']) if operation == 'update' else (values,):
        for field, value in snapshot.items():
            kind = policy['fields'][field]
            valid = (type(value) is bool if kind == 'boolean' else
                     _positive(value) or value is False if kind == 'many2one' else
                     isinstance(value, str) and len(value) <= 2000 or value is False)
            if not valid:
                raise OdooConfigurationError('只允许类型正确的标量，禁止嵌套关系命令')
    return policy


def _journal(workspace: Path, key: str) -> Path:
    workspace = Path(workspace).resolve()
    path = workspace
    for part in ('.dy_superpowers', 'configuration'):
        path = path / part
        if path.is_symlink():
            raise OdooConfigurationError('配置日志目录不得为符号链接')
        path.mkdir(exist_ok=True)
    target = path / (hashlib.sha256(key.encode()).hexdigest() + '.json')
    if target.is_symlink() or target.with_suffix('.json.lock').is_symlink():
        raise OdooConfigurationError('配置日志不得为符号链接')
    return target


def _rpc(client, model, method, args, company):
    # 调用点全部在本模块内固定；不向 MCP 暴露 method、kwargs 或任意 context。
    return client._execute_kw(model, method, args, {'context': {'force_company': company}})


def _read(client, model, ids, fields, company):
    rows = client._execute_kw(model, 'read', [ids],
                             {'fields': fields, 'context': {'force_company': company}})
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or row.get('id') not in ids for row in rows)
            or len({row['id'] for row in rows}) != len(rows)):
        raise OdooConfigurationError('配置核对返回格式无效')
    return rows


def _scope(row, scope, company):
    return (_relation(row.get(scope)) == company if scope == 'company_id'
            else row.get(scope) == [company])


def _preflight(client, operation, arguments, policy):
    company, model, values = arguments['company_id'], arguments['model'], arguments['values']
    expected_env = {'url': client.settings.url, 'database': client.settings.database}
    if arguments['environment'] != expected_env:
        raise OdooConfigurationError('确认环境与连接目标不一致')
    version = client._rpc('common', 'version', [])
    if not isinstance(version, dict) or version.get('server_version_info', [])[:2] != [12, 0]:
        raise OdooConfigurationError('仅允许已核验的 Odoo 12.0，其他版本暂停配置')
    modules = client._execute_kw('ir.module.module', 'search_read', [[['name', '=', policy['module']]]],
                                 {'fields': ['name', 'state', 'latest_version'], 'limit': 2})
    if (not isinstance(modules, list) or len(modules) != 1 or modules[0].get('name') != policy['module']
            or modules[0].get('state') != 'installed'
            or modules[0].get('latest_version') != '12.0.' + policy['version']):
        raise OdooConfigurationError('模块未安装、版本不符或不明确；不得据本地源码继续写入')
    if client.uid == 1:
        raise OdooConfigurationError('禁止使用绕过访问规则的超级用户进行配置')
    users = _read(client, 'res.users', [client.uid], ['company_id', 'company_ids'], company)
    if (len(users) != 1 or _relation(users[0].get('company_id')) != company
            or company not in users[0].get('company_ids', [])):
        raise OdooConfigurationError('目标公司不在当前服务用户的授权及当前公司范围')
    companies = _read(client, 'res.company', [company], ['id'], company)
    if len(companies) != 1 or companies[0].get('id') != company:
        raise OdooConfigurationError('目标公司不存在或不可访问')
    if _rpc(client, model, 'check_access_rights', ['create' if operation == 'create' else 'write', False], company) is not True:
        raise OdooConfigurationError('服务用户没有配置操作权限')
    metadata = _rpc(client, model, 'fields_get', [], company)
    if not isinstance(metadata, dict):
        raise OdooConfigurationError('配置字段元数据无效')
    for field in values:
        spec = metadata.get(field, {})
        if spec.get('type') != policy['fields'][field] or spec.get('readonly') is not False:
            raise OdooConfigurationError('线上配置字段缺失、只读或与白名单类型不符')
        if spec.get('type') == 'many2one':
            expected_relation = 'res.company' if field == 'company_id' else 'dy.expense.document.type'
            if spec.get('relation') != expected_relation:
                raise OdooConfigurationError('线上关联模型与白名单不符')
            if values[field] is not False and field != 'company_id':
                related = _read(client, expected_relation, [values[field]], ['company_ids'], company)
                if len(related) != 1 or not _scope(related[0], 'company_ids', company):
                    raise OdooConfigurationError('关联记录不存在、不可访问或跨公司')
    scope = policy['scope']
    scope_meta = metadata.get(scope, {})
    if scope_meta.get('type') != ('many2one' if scope == 'company_id' else 'many2many') or scope_meta.get('relation') != 'res.company':
        raise OdooConfigurationError('线上公司隔离字段与白名单不符')
    fields = list(dict.fromkeys([*values, scope, 'write_date']))
    if operation == 'update':
        rows = _read(client, model, [arguments['record_id']], fields, company)
        if len(rows) != 1 or not _scope(rows[0], scope, company):
            raise OdooConfigurationError('配置记录不存在、不可访问、共享或跨公司')
        if rows[0].get('write_date') != arguments['expected_write_date'] or compare_records(
                {'expected_count': 1, 'expected_values': arguments['expected_values']}, rows)['comparison_status'] != 'passed':
            raise OdooConfigurationError('配置原值已变化，请重新读取并确认')
    else:
        missing = [field for field, spec in metadata.items() if spec.get('required') and not spec.get('readonly') and field not in values]
        if missing:
            raise OdooConfigurationError('创建缺少必填字段，请核对默认值与白名单')
        key = policy['key']
        if not values.get(key):
            raise OdooConfigurationError('创建必须明确配置唯一定位字段')
        existing = client._execute_kw(model, 'search_read', [[[scope, '=', company], [key, '=', values[key]]]],
                                     {'fields': ['id'], 'limit': 2, 'context': {'force_company': company, 'active_test': False}})
        if existing:
            raise OdooConfigurationError('已有同键配置，请读取、复用或单独确认更新')
    return fields


def write_configuration(workspace: Path, operation: str, arguments: dict, *, client_factory=None) -> dict:
    """仅由 MCP handler 使用。dry_run 仅生成确认摘要，不连接或授予写权限。"""
    policy = _validate(operation, arguments)
    answer_context = _validate_answer_context(workspace, arguments)
    digest = confirmation_digest(operation, arguments)
    if arguments.get('dry_run') is True:
        return {'status': 'confirmation_required', 'confirmation_digest': digest,
                'preflight_status': 'not_checked',
                'confirmation': _confirmation_payload(arguments),
                'operation': operation, 'environment': arguments['environment'],
                'model': arguments['model'], 'company_id': arguments['company_id'],
                'values': arguments['values'], 'record_id': arguments.get('record_id'), 'accepted': False}
    if os.environ.get('DY_ODOO_CONFIG_WRITE_ENABLED') != '1':
        raise OdooConfigurationError('配置写入默认关闭，需在 MCP 进程环境显式启用')
    if arguments.get('confirmed') is not True or arguments.get('confirmation_digest') != digest:
        raise OdooConfigurationError('写入需要用户确认环境、公司、记录及精确变更摘要')
    journal = _journal(workspace, arguments['idempotency_key'])
    with record_lock(journal):
        if journal.exists():
            prior = json.loads(journal.read_text(encoding='utf-8'))
            if prior.get('digest') != digest:
                raise OdooConfigurationError('幂等键已用于不同变更')
            return {'status': 'already_attempted', 'execution_status': prior.get('execution_status', 'unknown'),
                    'record_id': prior.get('record_id'), 'data_readback_status': 'not_rechecked',
                    'browser_acceptance_status': 'not_checked', 'accepted': False,
                    'next_action': '重新通过只读 MCP 核对；结果不明时不得换幂等键重试'}
        client = (client_factory or (lambda: OdooReadClient(OdooSettings.from_environment())))()
        try:
            fields = _preflight(client, operation, arguments, policy)
            # 线上预检可能耗时；落盘尝试及写入前再次核实答卷，不能沿用旧预览。
            _validate_answer_context(workspace, arguments)
            state = {'digest': digest, 'execution_status': 'unknown', 'record_id': arguments.get('record_id')}
            # 先持久化尝试占位：超时、崩溃及无法确定远端结果均不得自动重放。
            atomic_write(journal, json.dumps(state))
            result = {'status': 'attempted', 'execution_status': 'unknown',
                      'data_readback_status': 'not_checked', 'browser_acceptance_status': 'not_checked',
                      'accepted': False, 'model': arguments['model'], 'company_id': arguments['company_id'],
                      'expected_values': arguments['values'], 'next_action': '只读回读并完成浏览器验收'}
            if answer_context is not None:
                result['answer_context'] = answer_context
            try:
                company, model = arguments['company_id'], arguments['model']
                context = {'force_company': company}
                if operation == 'update':
                    context['__last_update'] = {f"{model},{arguments['record_id']}": arguments['expected_write_date']}
                remote = client._execute_kw(model, 'create' if operation == 'create' else 'write',
                                            [arguments['values']] if operation == 'create' else [[arguments['record_id']], arguments['values']],
                                            {'context': context})
                valid = _positive(remote) if operation == 'create' else remote is True
                if not valid:
                    return result
                rid = remote if operation == 'create' else arguments['record_id']
                state.update(execution_status='succeeded', record_id=rid)
                atomic_write(journal, json.dumps(state))
                result.update(execution_status='succeeded', record_id=rid)
                rows = _read(client, model, [rid], fields, company)
                actual = {field: _relation(rows[0].get(field)) for field in arguments['values']} if len(rows) == 1 else {}
                matches = (len(rows) == 1 and _scope(rows[0], policy['scope'], company)
                    and compare_records({'expected_count': 1, 'expected_values': arguments['values']}, rows)['comparison_status'] == 'passed')
                result.update(actual_values=actual, data_readback_status='passed' if matches else 'mismatch')
                return result
            except Exception as exc:
                # 不回显远端异常正文；已成功写入但回读失败仍保留 succeeded。
                if isinstance(exc, OdooRPCError) and exc.diagnostic is not None:
                    result['rpc_diagnostic'] = exc.diagnostic
                return result
        finally:
            client.close()
