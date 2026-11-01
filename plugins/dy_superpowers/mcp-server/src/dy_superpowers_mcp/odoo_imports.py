"""配置 CSV 导入复用现有逐条白名单；不暴露通用 load 或关系命令。"""
import csv
import io
import json
from datetime import date

from .configuration import POLICIES, write_configuration, _journal
from .odoo_operations import digest
from .odoo_jsonrpc import OdooConfigurationError
from .storage import atomic_write, record_lock


def policy_for(model):
    policy = POLICIES.get(model)
    if not policy or policy.get('update_only'):
        raise OdooConfigurationError('该模型不在可创建配置导入白名单内')
    return policy


def preview(client, args):
    policy = policy_for(args['model'])
    metadata = client.fields_get(args['model'])
    errors, rows = [], []
    data = args['csv_data']
    if len(data.encode()) > 1024 * 1024:
        raise OdooConfigurationError('CSV 超过 1MB 限制')
    try:
        source = list(csv.reader(io.StringIO(data), strict=True))
    except csv.Error:
        return {'valid': False, 'errors': ['CSV 格式无效'], 'rows': [], 'total_rows': 0, 'execution_authorized': False}
    if not source or len(source) < 2 or len(source) > 101:
        raise OdooConfigurationError('CSV 必须包含表头及1至100条明细')
    headers = [h.strip() for h in source[0]]
    if len(headers) != len(set(headers)) or not set(headers) <= set(policy['fields']):
        errors.append('表头重复、为空或不在字段白名单内')
    for name in headers:
        spec = metadata.get(name, {})
        if spec.get('readonly') is not False or spec.get('type') != policy['fields'].get(name):
            errors.append('线上字段缺失、只读或类型不符：' + name)
    required = {k for k, v in metadata.items() if v.get('required') and not v.get('readonly')}
    if not required <= set(headers):
        errors.append('缺少线上必填字段；不猜测默认值')
    for number, values in enumerate(source[1:], 2):
        if len(values) != len(headers):
            errors.append(f'第{number}行列数不匹配')
            continue
        row = {}
        for name, value in zip(headers, values):
            value = value.strip()
            kind = policy['fields'].get(name)
            if kind == 'many2one':
                if not value.isdecimal() or int(value) < 1:
                    errors.append(f'第{number}行关联字段必须为正整数：{name}')
                    continue
                row[name] = int(value)
            elif kind == 'boolean':
                if value.lower() not in {'true', 'false', '1', '0'}:
                    errors.append(f'第{number}行布尔值不明确：{name}')
                row[name] = value.lower() in {'true', '1'}
            else:
                row[name] = value
                if kind == 'selection' and value not in {item[0] for item in metadata.get(name, {}).get('selection', [])}:
                    errors.append(f'第{number}行选项值无效或元数据缺失：{name}')
                if kind == 'date' and value:
                    try:
                        if date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                    except ValueError:
                        errors.append(f'第{number}行日期必须为有效的YYYY-MM-DD：{name}')
            if name in required and not value:
                errors.append(f'第{number}行必填值为空：{name}')
        if row.get('company_id') != args['company_id']:
            errors.append(f'第{number}行公司范围不一致')
        rows.append(row)
    keys = [r.get(policy['key']) for r in rows]
    if len(keys) != len(set(keys)):
        errors.append('本批配置业务键重复')
    missing = [key for key in ('environment', 'idempotency_key') if not args.get(key)]
    return {'valid': not errors, 'errors': errors, 'rows': rows, 'total_rows': len(source) - 1,
            'execution_authorized': False, 'confirmation_digest': digest('import', args) if not missing and not errors else None,
            'missing_confirmation_fields': missing,
            'limitations': '仅校验格式、白名单及字段；不证明关联可访问或业务配置生效。'}


def template(client, args):
    policy = policy_for(args['model'])
    metadata = client.fields_get(args['model'])
    fields = [name for name in policy['fields'] if name in metadata and metadata[name].get('readonly') is False
              and (args.get('include_optional', False) or metadata[name].get('required'))]
    return {'model': args['model'], 'fields': fields, 'csv_header': ','.join(fields) + '\n'}


def execute(workspace, client_factory, args):
    import os
    fingerprint = digest('import', args)
    if args.get('dry_run') is True:
        return {'status': 'confirmation_required', 'confirmation_digest': fingerprint, 'execution_authorized': False}
    if os.environ.get('DY_ODOO_CONFIG_WRITE_ENABLED') != '1':
        raise OdooConfigurationError('配置导入默认关闭')
    if args.get('confirmed') is not True or args.get('confirmation_digest') != digest('import', args):
        raise OdooConfigurationError('必须先预览并确认同一批CSV和环境、公司')
    from .qa_basis import configured_environment_info
    if args['environment'] != configured_environment_info()['environment']:
        raise OdooConfigurationError('导入确认环境不一致')
    journal = _journal(workspace, 'imports-' + args['idempotency_key'])
    with record_lock(journal):
        if journal.exists():
            prior = json.loads(journal.read_text())
            if prior['digest'] != fingerprint:
                raise OdooConfigurationError('导入幂等键已用于不同批次内容')
            return {'status': 'already_attempted', 'execution_status': prior['execution_status'],
                    'accepted': False, 'next_action': '只读回查，不自动重试或更换幂等键'}
        client = client_factory()
        candidate = preview(client, args)
        if not candidate['valid']:
            raise OdooConfigurationError('导入候选含缺口，先修正并重新确认')
        state = {'digest': fingerprint, 'execution_status': 'unknown'}
        atomic_write(journal, json.dumps(state))
        result = _execute_rows(workspace, client, args, candidate)
        if result['attempted'] == 0:
            # 首行预检即失败时远端未写入任何数据，释放幂等键，允许修正后用同一批次重试。
            journal.unlink(missing_ok=True)
            return result
        if result['attempted'] == result['total_rows'] and all(r.get('execution_status') == 'succeeded' for r in result['results']):
            state['execution_status'] = 'succeeded'
            atomic_write(journal, json.dumps(state))
        return result


def _execute_rows(workspace, client, args, candidate):
    from .configuration import confirmation_digest
    # 逐行独立提交，不冒称整批事务；失败立即停止，已尝试项依靠幂等日志禁止重放。
    results = []
    for index, row in enumerate(candidate['rows']):
        arguments = dict(environment={'url': client.settings.url, 'database': client.settings.database},
            company_id=args['company_id'], model=args['model'], values=row, confirmed=True,
            idempotency_key='import-row-' + digest('row', {'batch': args['idempotency_key'], 'index': index}))
        arguments['confirmation_digest'] = confirmation_digest('create', arguments)
        try:
            result = write_configuration(workspace, 'create', arguments)
        except OdooConfigurationError:
            results.append({'execution_status': 'not_attempted', 'data_readback_status': 'not_checked',
                            'accepted': False, 'reason': '该行未通过配置预检；先只读核对权限、版本、范围及重复项'})
            break
        except Exception:
            results.append({'execution_status': 'unknown', 'data_readback_status': 'not_checked',
                            'accepted': False, 'reason': '该行结果不明，仅可只读回查，不自动重试'})
            break
        results.append(result)
        if result.get('execution_status') != 'succeeded' or result.get('data_readback_status') != 'passed':
            break
    completed = sum(r.get('execution_status') == 'succeeded' and r.get('data_readback_status') == 'passed' for r in results)
    return {'results': results, 'total_rows': len(candidate['rows']),
            'attempted': sum(r.get('execution_status') != 'not_attempted' for r in results),
            'completed_rows': completed, 'stopped_at_row': len(results) if completed < len(candidate['rows']) else None,
            'accepted': False, 'atomic': False,
            'limitations': '逐行保存，失败不回滚已写行；未知或已尝试项只读回查，不自动重试。'}
