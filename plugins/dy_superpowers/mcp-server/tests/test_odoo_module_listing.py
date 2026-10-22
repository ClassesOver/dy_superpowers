"""dy_odoo_module_list_installed 翻页与参数校验的边界测试。"""
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

import server


def disable_telemetry(monkeypatch, codex_home: Path):
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)


def stub_odoo_client(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://odoo")
    monkeypatch.setenv("DY_ODOO_ALLOWED_ORIGINS", "http://odoo")
    monkeypatch.setenv("ODOO_DB", "db")
    monkeypatch.setenv("ODOO_USERNAME", "u")
    monkeypatch.setenv("ODOO_PASSWORD", "p")
    monkeypatch.setattr(server.OdooReadClient, "__init__", lambda self, settings: None)


def test_module_list_passes_offset_and_defaults_to_first_page(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    disable_telemetry(monkeypatch, tmp_path / "codex")
    stub_odoo_client(monkeypatch)
    calls = []

    def module_records(self, module=None, installed_only=False, offset=0):
        calls.append((module, installed_only, offset))
        return [{"name": "dy_fin_acc", "state": "installed"}]

    monkeypatch.setattr(server.OdooReadClient, "module_records", module_records)

    result, error = server.execute_tool(
        "dy_odoo_module_list_installed",
        {"trace_id": "module-offset", "offset": 100},
        workspace,
    )
    assert error is None
    assert result == [{"name": "dy_fin_acc", "state": "installed"}]

    result, error = server.execute_tool(
        "dy_odoo_module_list_installed",
        {"trace_id": "module-first"},
        workspace,
    )
    assert error is None
    assert calls == [(None, True, 100), (None, True, 0)]


def test_module_list_rejects_invalid_offset_as_invalid_params(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    disable_telemetry(monkeypatch, tmp_path / "codex")
    stub_odoo_client(monkeypatch)
    # 校验发生在真实 module_records 内部且先于任何远端调用，因此这里不打桩替换该方法。

    for offset in (-1, "100", True, 1.5):
        result, error = server.execute_tool(
            "dy_odoo_module_list_installed",
            {"trace_id": "module-bad", "offset": offset},
            workspace,
        )
        assert result is None
        assert error["code"] == -32602


def test_module_info_does_not_accept_offset_passthrough(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    disable_telemetry(monkeypatch, tmp_path / "codex")
    stub_odoo_client(monkeypatch)
    calls = []

    def module_records(self, module=None, installed_only=False, offset=0):
        calls.append((module, installed_only, offset))
        return [{"name": module, "state": "installed"}]

    monkeypatch.setattr(server.OdooReadClient, "module_records", module_records)

    result, error = server.execute_tool(
        "dy_odoo_module_info",
        {"trace_id": "module-info", "module": "dy_fin_acc", "offset": 300},
        workspace,
    )
    assert error is None
    assert calls == [("dy_fin_acc", False, 0)]


def test_tools_list_exposes_optional_offset_property(tmp_path):
    response = server.handle({"id": 1, "method": "tools/list"}, tmp_path)
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    schema = tools["dy_odoo_module_list_installed"]["inputSchema"]
    assert schema["required"] == ["trace_id"]
    assert schema["properties"]["offset"] == {"type": "integer", "minimum": 0}


@pytest.mark.parametrize('module', ['', '   ', '\t\n'])
def test_module_info_rejects_blank_name_before_connecting(tmp_path, monkeypatch, module):
    disable_telemetry(monkeypatch, tmp_path / 'codex')

    def must_not_connect(workspace):
        pytest.fail('空模块名不应建立 Odoo 连接')

    monkeypatch.setattr(server, '_odoo_client', must_not_connect)
    result, error = server.execute_tool('dy_odoo_module_info',
                                       {'trace_id': 'blank-module', 'module': module}, tmp_path)
    assert result is None
    assert error['code'] == -32602


@pytest.mark.parametrize('module', ['', '   ', '\t\n'])
def test_module_client_rejects_blank_name_without_broad_query(module):
    from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, OdooReadClient

    client = object.__new__(OdooReadClient)
    client.search_read = lambda *args: pytest.fail('空模块名不应扩大为全模块查询')
    with pytest.raises(OdooConfigurationError):
        client.module_records(module)


def test_module_info_trims_surrounding_whitespace_before_exact_query(tmp_path, monkeypatch):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    disable_telemetry(monkeypatch, tmp_path / 'codex')
    from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient

    client = object.__new__(OdooReadClient)
    calls = []
    client.search_read = lambda *args: calls.append(args) or [{'name': 'account'}]
    monkeypatch.setattr(server, '_odoo_client', lambda _workspace: client)
    result, error = server.execute_tool('dy_odoo_module_info',
                                       {'trace_id': 'padded-module', 'module': ' account '}, workspace)
    assert error is None
    assert result == [{'name': 'account'}]
    assert calls[0][1] == [['name', '=', 'account']]
