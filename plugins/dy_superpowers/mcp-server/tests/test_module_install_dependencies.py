"""真实 MCP 调度的安装依赖计划与固定确认模板；仅替换 Odoo 网络边界。"""
import copy

import pytest

import server
from dy_superpowers_mcp.tools import runtime
from test_odoo_capabilities import Client, call, client, lifecycle


class GraphClient(Client):
    def __init__(self):
        super().__init__()
        self.modules = {
            'dy_expense': dict(id=8, name='dy_expense', state='uninstalled', latest_version='12.0.1.0'),
            'dep_b': dict(id=9, name='dep_b', state='uninstalled', latest_version='12.0.1.0'),
            'dep_c': dict(id=10, name='dep_c', state='uninstalled', latest_version='12.0.1.0'),
            'base': dict(id=1, name='base', state='installed', latest_version='12.0.1.0'),
        }
        self.dependencies = {'dy_expense': ['dep_b', 'dep_c'], 'dep_b': ['base'],
                             'dep_c': ['base'], 'base': []}
        for row in self.modules.values():
            row['installed_version'] = '12.0.1.0'

    def search_read(self, model, domain, fields, limit, offset=0, **kwargs):
        self.calls.append((model, 'search_read', copy.deepcopy(domain), fields, limit, offset))
        if model == 'ir.module.module':
            if any(term[0] in {'auto_install', 'state'} for term in domain):
                return []
            names = next(term[2] for term in domain if term[:2] == ['name', 'in'])
            return [copy.deepcopy(self.modules[name]) for name in names if name in self.modules][offset:offset + limit]
        if model == 'ir.module.module.dependency':
            ids = next(term[2] for term in domain if term[:2] == ['module_id', 'in'])
            return [dict(id=i + 100, name=dep, module_id=[row['id'], row['name']])
                    for row in self.modules.values() if row['id'] in ids
                    for i, dep in enumerate(self.dependencies[row['name']])][offset:offset + limit]
        return []

    def _execute_kw(self, model, method, args, kwargs):
        result = super()._execute_kw(model, method, args, kwargs)
        if method == 'button_immediate_install':
            for row in self.modules.values():
                if row['id'] in args[0]:
                    row['state'] = 'installed'
        return result


@pytest.fixture
def graph(client, monkeypatch):
    fake = GraphClient()
    monkeypatch.setattr(runtime, 'odoo_client', lambda workspace: fake)
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense,dep_b,dep_c')
    return fake


def confirmed(preview):
    return dict(lifecycle(), dry_run=False, confirmed=True,
                confirmation_digest=preview['confirmation_digest'])


def test_install_preview_expands_shared_dependencies_and_fixes_confirmation_template(tmp_path, graph):
    result = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    plan = result['installation_plan']
    assert plan['install_order'] == ['dep_b', 'dep_c', 'dy_expense']
    assert plan['reused_modules'] == ['base']
    assert plan['dependencies']['dy_expense'] == ['dep_b', 'dep_c']
    assert plan['dependencies']['dep_b'] == ['base']
    confirmation = result['confirmation']
    assert confirmation['template_version'] == 'module-change/v1'
    assert confirmation['operation'] == 'install'
    assert confirmation['environment'] == 'demo'
    assert confirmation['requested_modules'] == ['dy_expense']
    assert confirmation['installation_plan'] == plan
    assert confirmation['backup_reference'] == '已核实的备份编号'
    assert confirmation['confirmation_digest'] == result['confirmation_digest']
    assert confirmation['blocked_modules'] == []
    assert 'dep_b' in result['confirmation_text'] and 'base' in result['confirmation_text']
    assert result['execution_authorized'] is False
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)


def test_install_executes_and_reads_back_entire_confirmed_dependency_set(tmp_path, graph):
    preview = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    result = call(tmp_path, 'dy_odoo_module_install', **confirmed(preview))
    assert result['data_readback_status'] == 'passed'
    assert {row['name'] for row in result['modules']} == {'dep_b', 'dep_c', 'dy_expense'}
    writes = [c for c in graph.calls if c[1] == 'button_immediate_install']
    assert [c[2] for c in writes] == [[[9, 10, 8]]]
    calls_before_replay = len(graph.calls)
    replay = call(tmp_path, 'dy_odoo_module_install', **confirmed(preview))
    assert replay['status'] == 'already_attempted'
    assert len(graph.calls) == calls_before_replay


@pytest.mark.parametrize('change', ['edge', 'version', 'source_version', 'state'])
def test_install_dependency_changes_require_new_confirmation(tmp_path, graph, change):
    preview = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    if change == 'edge':
        graph.dependencies['dep_c'] = []
    elif change == 'version':
        graph.modules['dep_b']['latest_version'] = '12.0.2.0'
    elif change == 'source_version':
        graph.modules['dep_b']['installed_version'] = '12.0.2.0'
    else:
        graph.modules['dep_b']['state'] = 'installed'
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='changed', **confirmed(preview)), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)


@pytest.mark.parametrize('failure', ['cycle', 'missing', 'pending'])
def test_invalid_dependency_graph_is_rejected_before_confirmation(tmp_path, graph, failure):
    if failure == 'cycle':
        graph.dependencies['base'] = ['dy_expense']
    elif failure == 'missing':
        del graph.modules['base']
    else:
        graph.modules['dep_b']['state'] = 'to install'
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='graph-invalid', **lifecycle()), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)


def test_dependency_whitelist_gap_is_displayed_and_blocks_execution(tmp_path, graph, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', 'dy_expense')
    preview = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    assert preview['confirmation']['blocked_modules'] == ['dep_b', 'dep_c']
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='blocked', **confirmed(preview)), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)


def test_installed_target_is_reused_without_an_install_button(tmp_path, graph, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_ALLOWED', '')
    for row in graph.modules.values():
        row['state'] = 'installed'
    preview = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    assert preview['installation_plan']['install_order'] == []
    result = call(tmp_path, 'dy_odoo_module_install', **confirmed(preview))
    assert result['status'] == 'no_changes'
    assert result['accepted'] is False
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)


@pytest.mark.parametrize('operation', ['uninstall'])
def test_module_previews_use_the_same_confirmation_template(tmp_path, client, operation, monkeypatch):
    monkeypatch.setenv('DY_ODOO_MODULE_WRITE_ENABLED', '1')
    result = call(tmp_path, 'dy_odoo_module_' + operation, **lifecycle())
    assert result['confirmation']['template_version'] == 'module-change/v1'
    assert result['confirmation']['operation'] == operation
    assert result['confirmation']['installation_plan'] is None
    assert result['confirmation']['requested_modules'] == ['dy_expense']
    assert result['execution_authorized'] is False
    assert not client.calls


def test_install_timeout_does_not_replay_after_dependency_state_changes(tmp_path, graph, monkeypatch):
    from dy_superpowers_mcp.odoo_jsonrpc import OdooRPCError
    original = graph._execute_kw

    def execute(model, method, args, kwargs):
        result = original(model, method, args, kwargs)
        if method == 'button_immediate_install':
            raise OdooRPCError('离线模拟：写入后连接超时')
        return result

    monkeypatch.setattr(graph, '_execute_kw', execute)
    preview = call(tmp_path, 'dy_odoo_module_install', **lifecycle())
    result = call(tmp_path, 'dy_odoo_module_install', **confirmed(preview))
    assert result['execution_status'] == 'unknown'
    before = len(graph.calls)
    replay = call(tmp_path, 'dy_odoo_module_install', **confirmed(preview))
    assert replay['status'] == 'already_attempted'
    assert replay['execution_status'] == 'unknown'
    assert len(graph.calls) == before


def test_install_refuses_incomplete_dependency_page(tmp_path, graph, monkeypatch):
    original = graph.search_read

    def read(model, domain, fields, limit, offset=0, **kwargs):
        if model == 'ir.module.module.dependency':
            return [dict(id=i, name='dep_' + str(i)) for i in range(100)]
        return original(model, domain, fields, limit, offset, **kwargs)

    monkeypatch.setattr(graph, 'search_read', read)
    _, error = server.execute_tool('dy_odoo_module_install', dict(trace_id='incomplete', **lifecycle()), tmp_path)
    assert error and error['code'] == -32602
    assert not any(c[1].startswith('button_immediate_') for c in graph.calls)
