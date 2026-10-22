"""工程化加固：协议协商、版本单一来源、Odoo 超时与校验、记忆原子写、索引刷新和 trace 保留。"""
import json
import os
import time
import tomllib
from pathlib import Path

import pytest

import server
from dy_superpowers_mcp import __version__
from dy_superpowers_mcp import memory as memory_module
from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
from dy_superpowers_mcp.memory import WorkspaceMemory
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, OdooReadClient, OdooRPCError, OdooSettings, odoo_timeout
from dy_superpowers_mcp.trace import TraceStore

PLUGIN_ROOT = Path(__file__).parents[2]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD", "DY_ODOO_TIMEOUT", "DY_TRACE_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    return project


@pytest.fixture
def odoo_env(monkeypatch):
    for name, value in {"ODOO_URL": "http://odoo", "ODOO_DB": "db", "ODOO_USERNAME": "u", "ODOO_PASSWORD": "p"}.items():
        monkeypatch.setenv(name, value)


def initialize(protocol_version):
    params = {} if protocol_version is None else {"protocolVersion": protocol_version}
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}, Path("."))["result"]


def test_initialize_echoes_supported_protocol_version_and_falls_back_to_latest():
    assert initialize("2025-06-18")["protocolVersion"] == "2025-06-18"
    assert initialize("2024-11-05")["protocolVersion"] == "2024-11-05"
    assert initialize("1999-01-01")["protocolVersion"] == server.LATEST_PROTOCOL_VERSION
    assert initialize(None)["protocolVersion"] == server.LATEST_PROTOCOL_VERSION


def test_version_has_single_source_across_manifest_package_and_server():
    pyproject = tomllib.loads((PLUGIN_ROOT / "mcp-server/pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert __version__ == pyproject["project"]["version"] == manifest["version"]
    assert initialize(None)["serverInfo"]["version"] == __version__


def test_server_has_single_protocol_path():
    assert not hasattr(server, "run_fastmcp")
    tools = server.handle({"id": 1, "method": "tools/list"}, Path("."))["result"]["tools"]
    assert {tool["name"] for tool in tools} == set(server.TOOLS) == set(server.TOOL_SCHEMAS)
    assert set(server.MEMORY_READ_FILES) == memory_module.MEMORY_FILES


def test_business_trace_event_is_exported_to_otel(workspace, monkeypatch):
    recorded = []
    monkeypatch.setattr(server, "record_otel_event", lambda span, event: recorded.append(event))
    result, error = server.execute_tool("dy_trace_event", {"trace_id": "otel-business", "event": {"event": "inspection.result", "result": "通过"}}, workspace)
    assert error is None
    assert {"event": "inspection.result", "result": "通过"} in recorded


def test_tool_result_keeps_chinese_readable(workspace):
    response = server.handle(
        {"id": 1, "method": "tools/call", "params": {"name": "dy_knowledge_search", "arguments": {"trace_id": "kn", "query": "发票"}}},
        workspace,
    )
    text = response["result"]["content"][0]["text"]
    assert "财务会计" in text
    assert "\\u" not in text


@pytest.mark.parametrize("tool, arguments", [
    ("dy_odoo_record_read", {"model": "res.partner;drop", "ids": [1]}),
    ("dy_odoo_record_read", {"ids": [1]}),
    ("dy_odoo_model_fields", {"model": "Res.Partner"}),
    ("dy_odoo_model_fields", {}),
    ("dy_odoo_module_info", {"module": ""}),
    ("dy_odoo_module_info", {}),
])
def test_odoo_tools_validate_arguments_before_connecting(workspace, odoo_env, monkeypatch, tool, arguments):
    def must_not_connect(self, settings):
        raise AssertionError("参数校验应先于 Odoo 连接")

    monkeypatch.setattr(server.OdooReadClient, "__init__", must_not_connect)
    result, error = server.execute_tool(tool, {"trace_id": "odoo-invalid", **arguments}, workspace)
    assert result is None
    assert error["code"] == -32602


class FakeJsonRpc:
    """记录 JSON-RPC 请求并按 service/method 返回预设结果。"""

    def __init__(self, results=None, error=None):
        self.calls = []
        self.results = {("common", "authenticate"): 2, **(results or {})}
        self.error = error

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        params = payload["params"]
        key = (params["service"], params["method"] if params["service"] == "common" else params["args"][4])
        if self.error is not None and key[0] == "object":
            return {"jsonrpc": "2.0", "id": payload["id"], "error": self.error}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": self.results[key]}


def test_odoo_client_authenticates_and_reads_over_jsonrpc_with_timeout():
    transport = FakeJsonRpc({("object", "search_read"): [{"id": 1}]})
    client = OdooReadClient(OdooSettings("http://odoo/", "db", "u", "p"), transport=transport)
    assert client.search_read("res.partner", [["id", "=", 1]], ["name"], 5, 10) == [{"id": 1}]

    (auth_url, auth, auth_timeout), (read_url, read, _) = transport.calls
    assert auth_url == read_url == "http://odoo/jsonrpc"
    assert auth_timeout == 15
    assert auth["method"] == "call"
    assert auth["params"] == {"service": "common", "method": "authenticate", "args": ["db", "u", "p", {}]}
    assert read["params"] == {
        "service": "object",
        "method": "execute_kw",
        "args": ["db", 2, "p", "res.partner", "search_read", [[["id", "=", 1]]], {"fields": ["name"], "limit": 5, "offset": 10}],
    }
    assert auth["id"] != read["id"]


def test_odoo_client_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("DY_ODOO_TIMEOUT", "3")
    transport = FakeJsonRpc()
    OdooReadClient(OdooSettings("https://odoo", "db", "u", "p"), transport=transport)
    assert transport.calls[0][2] == 3


def test_odoo_timeout_setting_rejects_invalid_values(monkeypatch):
    for value in ("0", "-1", "abc", "601", "nan"):
        monkeypatch.setenv("DY_ODOO_TIMEOUT", value)
        with pytest.raises(OdooConfigurationError):
            odoo_timeout()


def test_odoo_remote_error_is_redacted():
    transport = FakeJsonRpc(error={"code": 200, "message": "Odoo Server Error", "data": {"debug": "员工 张三 password=secret"}})
    client = OdooReadClient(OdooSettings("http://odoo", "db", "u", "p"), transport=transport)
    with pytest.raises(OdooRPCError) as raised:
        client.search_read("hr.employee", [], [], 1)
    assert str(raised.value) == "Odoo 只读请求失败"
    assert "张三" not in repr(raised.value)


@pytest.mark.parametrize("uid", [False, 0, None, "2"])
def test_odoo_rejected_login_is_authentication_error(uid):
    with pytest.raises(OdooRPCError, match="认证失败"):
        OdooReadClient(OdooSettings("http://odoo", "db", "u", "p"), transport=FakeJsonRpc({("common", "authenticate"): uid}))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://odoo", "odoo.local"])
def test_odoo_url_must_be_http(url):
    with pytest.raises(OdooConfigurationError):
        OdooReadClient(OdooSettings(url, "db", "u", "p"), transport=FakeJsonRpc())


def test_odoo_client_refuses_non_read_methods():
    client = OdooReadClient(OdooSettings("http://odoo", "db", "u", "p"), transport=FakeJsonRpc())
    for method in ("write", "create", "unlink", "execute"):
        with pytest.raises(OdooRPCError):
            client._execute_kw("res.partner", method, [], {})


def test_odoo_adapter_no_longer_uses_xmlrpc():
    source = (PLUGIN_ROOT / "mcp-server/src/dy_superpowers_mcp/odoo_jsonrpc.py").read_text(encoding="utf-8")
    assert "xmlrpc" not in source
    assert not (PLUGIN_ROOT / "mcp-server/src/dy_superpowers_mcp/odoo_xmlrpc.py").exists()


def test_memory_write_is_atomic_and_leaves_no_temporary_files(workspace, monkeypatch):
    replaced = []
    original_replace = os.replace
    monkeypatch.setattr(memory_module.os, "replace", lambda src, dst: replaced.append(Path(dst).name) or original_replace(src, dst))

    WorkspaceMemory(workspace).write("profile.md", "第一版", confirmed=True)
    WorkspaceMemory(workspace).write("profile.md", "第二版", confirmed=True)

    memory_dir = workspace / ".dy_superpowers"
    assert (memory_dir / "profile.md").read_text(encoding="utf-8") == "第二版"
    assert replaced == ["profile.md", "profile.md"]
    assert sorted(path.name for path in memory_dir.iterdir() if not path.name.endswith(".lock")) == ["profile.md"]


def fake_cli(adapter, monkeypatch, calls, fail_reindex=False):
    monkeypatch.setattr(adapter, "_ensure_project", lambda: None)

    def run(args):
        calls.append(args[0])
        if fail_reindex and args[0] == "reindex":
            raise OSError("reindex failed")
        return '{"results":[]}'

    monkeypatch.setattr(adapter, "_run", run)


def test_search_skips_reindex_until_memory_files_change(tmp_path, monkeypatch):
    memory_dir = tmp_path / ".dy_superpowers"
    memory_dir.mkdir()
    profile = memory_dir / "profile.md"
    profile.write_text("第一版", encoding="utf-8")
    calls = []
    adapter = BasicMemoryAdapter(tmp_path)
    fake_cli(adapter, monkeypatch, calls)

    adapter.search("版本")
    adapter.search("版本")
    assert calls == ["reindex", "tool", "tool"]

    profile.write_text("第二版内容", encoding="utf-8")
    # 新实例读取持久化的索引状态，文件变化后重新索引。
    second = BasicMemoryAdapter(tmp_path)
    fake_cli(second, monkeypatch, calls)
    second.search("版本")
    assert calls == ["reindex", "tool", "tool", "reindex", "tool"]


def test_failed_reindex_does_not_record_index_state(tmp_path, monkeypatch):
    (tmp_path / ".dy_superpowers").mkdir()
    (tmp_path / ".dy_superpowers/profile.md").write_text("内容", encoding="utf-8")
    calls = []
    adapter = BasicMemoryAdapter(tmp_path)
    fake_cli(adapter, monkeypatch, calls, fail_reindex=True)
    assert adapter.search("内容")["available"] is False

    calls.clear()
    fake_cli(adapter, monkeypatch, calls)
    adapter.search("内容")
    assert calls == ["reindex", "tool"]


def test_trace_read_supports_limit(workspace):
    store = TraceStore(workspace, os.environ["CODEX_HOME"])
    for index in range(5):
        store.append("trace-limit", {"event": "inspection.result", "operation": f"step-{index}"})

    result, error = server.execute_tool("dy_trace_read", {"trace_id": "trace-limit", "limit": 2}, workspace)
    assert error is None
    assert [event["operation"] for event in result] == ["step-3", "step-4"]

    for limit in (0, 1001, "2", True):
        result, error = server.execute_tool("dy_trace_read", {"trace_id": "trace-limit", "limit": limit}, workspace)
        assert error["code"] == -32602
    schema = server.TOOL_SCHEMAS["dy_trace_read"]["properties"]["limit"]
    assert schema == {"type": "integer", "minimum": 1, "maximum": 1000}


def test_session_initialize_prunes_expired_traces(workspace, monkeypatch):
    store = TraceStore(workspace, os.environ["CODEX_HOME"])
    old = store.append("old-session", {"event": "inspection.result"})
    recent = store.append("recent-session", {"event": "inspection.result"})
    expired = time.time() - 31 * 86400
    os.utime(old, (expired, expired))

    result, error = server.execute_tool("dy_session_initialize", {"trace_id": "new-session", "source": "startup"}, workspace)
    assert error is None
    assert not old.exists()
    assert recent.exists()

    monkeypatch.setenv("DY_TRACE_RETENTION_DAYS", "0")
    os.utime(recent, (expired, expired))
    server.execute_tool("dy_session_initialize", {"trace_id": "next-session", "source": "startup"}, workspace)
    assert recent.exists()


def test_serve_line_never_echoes_messages_without_method(workspace):
    assert server.serve_line('{"jsonrpc":"2.0","id":7}', workspace)["error"]["code"] == -32600
    assert server.serve_line('{"jsonrpc":"2.0","id":7,"result":{}}', workspace) is None
    assert server.serve_line("{bad json", workspace)["error"]["code"] == -32700
    assert server.serve_line('{"jsonrpc":"2.0","method":"notifications/initialized"}', workspace) is None
    assert server.serve_line('{"jsonrpc":"2.0","id":8,"method":"ping"}', workspace) == {"jsonrpc": "2.0", "id": 8, "result": {}}


def test_research_record_read_requires_key_fields(workspace):
    result, error = server.execute_tool("dy_research_record_read", {"trace_id": "research-missing", "module": "expense"}, workspace)
    assert result is None
    assert error["code"] == -32602


def test_urllib_transport_talks_to_odoo_jsonrpc_endpoint():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received = []

    class FakeOdoo(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers["Content-Type"], payload["params"]["service"]))
            result = 7 if payload["params"]["service"] == "common" else [{"id": 1, "name": "演示"}]
            body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), FakeOdoo)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client = OdooReadClient(OdooSettings(f"http://127.0.0.1:{httpd.server_port}", "db", "u", "p"))
        assert client.uid == 7
        assert client.read_records("res.partner", [1], ["name"]) == [{"id": 1, "name": "演示"}]
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert received == [("/jsonrpc", "application/json", "common"), ("/jsonrpc", "application/json", "object")]
