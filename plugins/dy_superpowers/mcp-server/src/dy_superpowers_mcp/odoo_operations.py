"""固定白名单操作；预览绑定环境及内容，结果不明不自动重放。"""
import hashlib
import json
import os
import re

from .configuration import _journal
from .storage import atomic_write, record_lock
from .odoo_jsonrpc import OdooConfigurationError, OdooSettings


# 方法与模型成对固定；不允许调用者指定方法参数或业务动作。
EXECUTE_READS = {'ir.module.module': {'check_access_rights'}}
DELETE_POLICIES = {}  # 尚无经业务批准可删的模型；空白名单明确拒绝，不开放业务单据。
UPGRADE_BLOCKER = '暂停升级执行：原生升级刷新模块清单后可能新增批外反向依赖，当前只读预检无法证明完整影响范围'


def digest(operation, args):
    payload = {k: v for k, v in args.items() if k not in
               {'trace_id', 'trace_context', 'dry_run', 'confirmed', 'confirmation_digest'}}
    if operation in {'install', 'upgrade', 'uninstall', 'import'}:
        settings = OdooSettings.from_environment()
        payload['_connection'] = {'url': settings.url, 'database': settings.database}
    return hashlib.sha256(json.dumps([operation, payload], sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _installation_plan(client, names):
    """读取目标账套声明的完整依赖闭包；拓扑排序仅用于模块安装。"""
    modules, dependencies, visiting, ordered = {}, {}, set(), []

    def visit(name):
        if name in visiting:
            raise OdooConfigurationError('模块依赖存在循环，必须先核实')
        if name in modules:
            return
        if len(modules) >= 100 or not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise OdooConfigurationError('模块依赖名称无效或闭包超过100个模块，无法完整核对')
        rows = client.search_read('ir.module.module', [['name', 'in', [name]]],
                                  ['name', 'state', 'latest_version', 'installed_version'], 2)
        if (len(rows) != 1 or rows[0].get('name') != name
                or type(rows[0].get('id')) is not int or rows[0]['id'] <= 0):
            raise OdooConfigurationError('依赖模块不存在、重复或不可访问：' + name)
        row = rows[0]
        if row.get('state') not in {'installed', 'uninstalled'}:
            raise OdooConfigurationError('依赖模块状态不可用于安装：' + name)
        # Odoo 12 命名反直觉：latest_version 为数据库安装版本，installed_version 为磁盘源码版本。
        modules[name] = {key: row.get(key) for key in ('id', 'name', 'state', 'latest_version', 'installed_version')}
        deps = client.search_read('ir.module.module.dependency', [['module_id', 'in', [row['id']]]], ['name'], 100)
        if len(deps) >= 100 or any(not isinstance(dep.get('name'), str) for dep in deps):
            raise OdooConfigurationError('依赖声明不完整或超过安全上限，不能生成确认摘要')
        dependencies[name] = sorted({dep['name'] for dep in deps})
        visiting.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        visiting.remove(name)
        ordered.append(name)

    for name in sorted(names):
        visit(name)
    return {'requested_modules': sorted(names),
            'dependencies': {name: dependencies[name] for name in sorted(dependencies)},
            'modules': [modules[name] for name in sorted(modules)],
            'install_order': [name for name in ordered if modules[name]['state'] == 'uninstalled'],
            'reused_modules': sorted(name for name in modules if modules[name]['state'] == 'installed')}


def _module_confirmation(operation, args, fingerprint, plan):
    """MCP 固定确认模板；正文由服务端生成，摘要仍不构成用户授权。"""
    settings = OdooSettings.from_environment()
    targets = plan['install_order'] if plan is not None else args['module_names']
    allowed = set(filter(None, os.environ.get('DY_ODOO_MODULE_ALLOWED', '').split(',')))
    confirmation = {
        'template_version': 'module-change/v1', 'operation': operation,
        'environment': args['environment'], 'connection': {'url': settings.url, 'database': settings.database},
        'requested_modules': args['module_names'], 'affected_modules': targets,
        'installation_plan': plan, 'blocked_modules': sorted(set(targets) - allowed | set(targets) & {'base', 'web'}),
        'write_enabled': os.environ.get('DY_ODOO_MODULE_WRITE_ENABLED') == '1',
        'execution_blockers': [UPGRADE_BLOCKER] if operation == 'upgrade' else [],
        'backup_reference': args['backup_reference'], 'backup_verified': False,
        'idempotency_key': args['idempotency_key'], 'confirmation_digest': fingerprint,
        'warning': '依赖计划只证明本次读取的声明；备份未由工具验证。执行前会重新核对，数据回读不代替业务及浏览器验收。',
    }
    return {'status': 'confirmation_required', 'confirmation_digest': fingerprint,
            'execution_authorized': False, 'module_names': args['module_names'],
            'installation_plan': plan, 'confirmation': confirmation,
            'confirmation_text': '请确认以下模块变更（未执行）：\n' + json.dumps(confirmation, ensure_ascii=False, indent=2),
            'warning': confirmation['warning']}


def module_operation(workspace, operation, args, client_factory):
    names = args['module_names']
    if len(set(names)) != len(names) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', n) for n in names):
        raise OdooConfigurationError('模块名必须合法且不重复')
    fingerprint = digest(operation, args)
    if args.get('dry_run', True):
        plan = None
        if operation == 'install':
            from .inspection_prepare import configured_environment_info
            if args['environment'] != configured_environment_info()['environment']:
                raise OdooConfigurationError('确认环境与当前环境连接不一致')
            client = client_factory()
            version = client._rpc('common', 'version', [])
            if not isinstance(version, dict) or version.get('server_version_info', [])[:2] != [12, 0]:
                raise OdooConfigurationError('仅支持已核验的 Odoo 12')
            plan = _installation_plan(client, names)
            fingerprint = digest(operation, {**args, '_installation_plan': plan})
        return _module_confirmation(operation, args, fingerprint, plan)
    if os.environ.get('DY_ODOO_MODULE_WRITE_ENABLED') != '1':
        raise OdooConfigurationError('模块操作默认关闭')
    allowed = set(filter(None, os.environ.get('DY_ODOO_MODULE_ALLOWED', '').split(',')))
    if set(names) & {'base', 'web'} or (operation != 'install' and not set(names) <= allowed):
        raise OdooConfigurationError('模块不在受信任环境白名单内或为保护模块')
    if args.get('confirmed') is not True or (operation != 'install' and args.get('confirmation_digest') != fingerprint):
        raise OdooConfigurationError('必须确认本次环境、模块、备份及精确操作摘要')
    if not args.get('backup_reference', '').strip():
        raise OdooConfigurationError('必须提供已核实备份的引用；工具不创建或证明备份可恢复')
    from .inspection_prepare import configured_environment_info
    if args['environment'] != configured_environment_info()['environment']:
        raise OdooConfigurationError('确认环境与当前环境连接不一致')
    if operation == 'upgrade':
        raise OdooConfigurationError(UPGRADE_BLOCKER)
    journal = _journal(workspace, 'modules-' + args['idempotency_key'])
    with record_lock(journal):
        if journal.exists():
            prior = json.loads(journal.read_text())
            if (prior.get('request_digest', prior['digest']) != fingerprint
                    or prior['digest'] != args.get('confirmation_digest')):
                raise OdooConfigurationError('幂等键已用于其他操作')
            return {'status': 'already_attempted', 'execution_status': prior['execution_status'],
                    'accepted': False, 'next_action': '只读核对结果，不得自动重试或更换幂等键'}
        client = client_factory()
        if client.uid == 1:
            raise OdooConfigurationError('禁止使用超级用户绕过权限')
        version = client._rpc('common', 'version', [])
        if not isinstance(version, dict) or version.get('server_version_info', [])[:2] != [12, 0]:
            raise OdooConfigurationError('仅支持已核验的 Odoo 12')
        method = {'install': 'button_immediate_install', 'upgrade': 'button_immediate_upgrade',
                  'uninstall': 'button_immediate_uninstall'}[operation]
        request_fingerprint = fingerprint
        if operation == 'install':
            plan = _installation_plan(client, names)
            fingerprint = digest(operation, {**args, '_installation_plan': plan})
            if args.get('confirmation_digest') != fingerprint:
                raise OdooConfigurationError('依赖计划、状态或版本已变化；必须重新预览并确认')
            names = plan['install_order']
            if not set(names) <= allowed or set(names) & {'base', 'web'}:
                raise OdooConfigurationError('完整待安装清单不在白名单内或包含保护模块，必须另行授权')
            by_name = {row['name']: row for row in plan['modules']}
            rows = [by_name[name] for name in names]
            if not names:
                return {'status': 'no_changes', 'execution_status': 'not_attempted', 'accepted': False,
                        'installation_plan': plan, 'data_readback_status': 'passed',
                        'browser_acceptance_status': 'not_checked'}
        else:
            rows = client.search_read('ir.module.module', [['name', 'in', names]],
                                      ['name', 'state', 'latest_version'], 100)
        if len(rows) != len(names) or {r.get('name') for r in rows} != set(names):
            raise OdooConfigurationError('目标模块不存在、重复或不可访问')
        required = {'install': {'uninstalled'}, 'upgrade': {'installed'}, 'uninstall': {'installed'}}[operation]
        if any(r.get('state') not in required for r in rows):
            raise OdooConfigurationError('模块状态不满足操作前提，请重读后确认')
        if client._execute_kw('ir.module.module', 'check_access_rights', ['write', False], {}) is not True:
            raise OdooConfigurationError('当前用户没有模块维护权限')
        pending = client.search_read('ir.module.module', [['state', 'in', ['to install', 'to upgrade', 'to remove']]], ['name', 'state'], 1)
        if pending:
            raise OdooConfigurationError('系统已有待处理模块变更，禁止合并执行未经本轮确认的操作')
        # 仅允许无额外自动变更的批次；缺失依赖或反向依赖需另行审定。
        dependencies = [] if operation == 'install' else client.search_read(
            'ir.module.module.dependency', [['module_id', 'in', [r['id'] for r in rows]]], ['name'], 100)
        if len(dependencies) == 100:
            raise OdooConfigurationError('依赖超出安全上限，无法确认影响范围')
        needed = sorted({r['name'] for r in dependencies} - set(names))
        if needed and operation == 'upgrade':
            available = client.search_read('ir.module.module', [['name', 'in', needed]], ['name', 'state'], 100)
            if len(available) != len(needed) or any(r.get('state') != 'installed' for r in available):
                raise OdooConfigurationError('存在未安装或不可见依赖，不得自动扩大安装范围')
        # Odoo 12 升级也会递归标记已安装的反向依赖，不能只限制卸载范围。
        if operation in {'upgrade', 'uninstall'}:
            reverse = client.search_read('ir.module.module.dependency', [['name', 'in', names], ['module_id.state', '=', 'installed'], ['module_id', 'not in', [r['id'] for r in rows]]], ['name'], 1)
            if reverse:
                raise OdooConfigurationError('存在本批次外已安装反向依赖，不得扩大升级或卸载范围')
        automatic = client.search_read('ir.module.module', [['auto_install', '=', True], ['state', 'in', ['uninstalled', 'to install']]], ['name'], 1)
        if automatic and operation == 'install':
            raise OdooConfigurationError('存在可能自动安装的模块，须先人工核实影响范围')
        state = {'digest': fingerprint, 'request_digest': request_fingerprint, 'execution_status': 'unknown'}
        atomic_write(journal, json.dumps(state))
        result = {'status': 'attempted', 'execution_status': 'unknown', 'data_readback_status': 'not_checked',
                  'accepted': False, 'next_action': '只读核对模块、版本和业务效果；不自动重试'}
        try:
            client._execute_kw('ir.module.module', method, [[r['id'] for r in rows]], {})
            state['execution_status'] = 'succeeded'
            atomic_write(journal, json.dumps(state))
            result['execution_status'] = 'succeeded'
            actual = client.search_read('ir.module.module', [['name', 'in', names]], ['name', 'state', 'latest_version'], 100)
            target = 'uninstalled' if operation == 'uninstall' else 'installed'
            ok = len(actual) == len(names) and {r.get('name') for r in actual} == set(names) and all(r.get('state') == target for r in actual)
            result.update(data_readback_status='passed' if ok else 'mismatch', modules=actual)
        except Exception:
            pass  # 不泄漏远端正文；已记录尝试，结果未知时禁止重放。
        return result


def execute_read(client, args):
    if args['method'] not in EXECUTE_READS.get(args['model'], set()):
        raise OdooConfigurationError('模型和方法不在固定白名单内')
    return {'allowed': client._execute_kw(args['model'], args['method'], ['read', False], {}) is True,
            'execution_authorized': False}


def delete_record(args):
    if args['model'] not in DELETE_POLICIES:
        raise OdooConfigurationError('当前无获批删除白名单；禁止删除业务记录')
