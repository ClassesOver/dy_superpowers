import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp import TraceStore, WorkspaceMemory, redact
from dy_superpowers_mcp import trace as trace_module
from dy_superpowers_mcp.trace import REDACTED
from mcp_helpers import call_tool, listed_tools
from dy_superpowers_mcp import basic_memory
from dy_superpowers_mcp.tools import runtime


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT",
                 "DY_SUPERPOWERS_OTLP_TRACES_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("APP_URL", "DB_NAME", "APP_USER", "APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return project


def research_arguments(**updates):
    arguments = {
        "module": "expense",
        "check_order": "1",
        "research_id": "01",
        "config_item": "应用安装",
        "conclusion": "费用模块本期上线",
        "scope": "公司A/本期",
        "evidence_ref": "需求文档#4",
        "confirmed_by": "顾问",
        "confirmed_at": "2026-10-21",
        "confirmed": True,
    }
    arguments.update(updates)
    return arguments


def test_odoo_record_read_uses_explicit_read_operation(workspace, monkeypatch):
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("APP_USER", "u")
    monkeypatch.setenv("APP_PASSWORD", "p")
    monkeypatch.setattr("dy_superpowers_mcp.odoo_jsonrpc.OdooReadClient.__init__", lambda self, settings: None)
    read = lambda self, model, ids, fields: [{"id": 3, "name": "Demo"}]
    monkeypatch.setattr("dy_superpowers_mcp.odoo_jsonrpc.OdooReadClient.read_records", read)

    result = call_tool(
        "dy_odoo_record_read",
        {"trace_id": "odoo-read", "model": "res.partner", "ids": [3], "fields": ["name"]},
        workspace,
    )
    assert "error" not in result
    assert result["result"]["content"][0]["text"] == '[{"id": 3, "name": "Demo"}]'


def test_odoo_model_fields_is_read_only(workspace, monkeypatch):
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("APP_USER", "u")
    monkeypatch.setenv("APP_PASSWORD", "p")
    monkeypatch.setattr("dy_superpowers_mcp.odoo_jsonrpc.OdooReadClient.__init__", lambda self, settings: None)
    called = []
    monkeypatch.setattr("dy_superpowers_mcp.odoo_jsonrpc.OdooReadClient.fields_get", lambda self, model: called.append(model) or {"name": {"type": "char"}})

    result = call_tool("dy_odoo_model_fields", {"trace_id": "odoo-fields", "model": "res.partner"}, workspace)
    assert "error" not in result
    assert called == ["res.partner"]


def test_knowledge_module_info_works_without_remote_connection(workspace):
    result = call_tool("dy_knowledge_module_info", {"trace_id": "knowledge-1", "module": "account"}, workspace)
    assert "error" not in result
    assert "account.move" in result["result"]["content"][0]["text"]


def test_knowledge_search_finds_chinese_alias(workspace):
    result = call_tool("dy_knowledge_search", {"trace_id": "knowledge-2", "query": "发票"}, workspace)
    assert "error" not in result
    assert "account" in result["result"]["content"][0]["text"]


def test_odoo_read_rejects_non_read_method(workspace):
    result = call_tool(
        "dy_odoo_record_search",
        {"trace_id": "odoo-1", "model": "res.partner", "method": "write", "args": [], "kwargs": {}},
        workspace,
    )
    assert "error" in result
    assert result["error"]["code"] == -32602


def test_odoo_read_requires_connection_environment(workspace):
    result = call_tool(
        "dy_odoo_record_search",
        {"trace_id": "odoo-2", "model": "res.partner", "method": "search_read", "args": [[]], "kwargs": {"limit": 1}},
        workspace,
    )
    assert "error" in result
    assert "APP_URL" in result["error"]["message"]


def test_trace_redacts_sensitive_fields():
    assert redact({"token": "secret", "nested": {"password": "pw"}}) == {
        "token": REDACTED,
        "nested": {"password": REDACTED},
    }
    assert redact({"operation": "Bearer abc123", "source_ref": "api_key=secret"}) == {
        "operation": f"Bearer {REDACTED}",
        "source_ref": f"api_key={REDACTED}",
    }


def test_trace_is_workspace_scoped_and_jsonl(workspace):
    store = TraceStore(workspace, os.environ["CODEX_HOME"])
    path = store.append("trace-1", {"event": "start", "api_key": "hidden", "workspace_id": "forged"})
    assert path.is_relative_to(Path(os.environ["CODEX_HOME"]))
    record = store.read("trace-1")[0]
    assert record["event"] == "start"
    assert record["workspace_id"] == hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    assert str(workspace.resolve()) not in path.read_text(encoding="utf-8")
    assert "hidden" not in path.read_text(encoding="utf-8")


def test_workspace_id_is_stable_per_project_and_differs_between_projects(workspace, tmp_path):
    other = tmp_path / "other-project"
    other.mkdir()

    first = TraceStore(workspace, os.environ["CODEX_HOME"])
    same = TraceStore(workspace / ".", os.environ["CODEX_HOME"])
    second = TraceStore(other, os.environ["CODEX_HOME"])
    first.append("trace-a", {"event": "tool.end"})
    same.append("trace-b", {"event": "tool.end"})
    second.append("trace-c", {"event": "tool.end"})

    ids = [store.read(trace_id)[0]["workspace_id"] for store, trace_id in (
        (first, "trace-a"), (same, "trace-b"), (second, "trace-c"),
    )]
    assert ids[0] == ids[1]
    assert ids[0] != ids[2]


def test_trace_ignores_unapproved_payload_fields(workspace):
    store = TraceStore(workspace, os.environ["CODEX_HOME"])
    store.append("trace-1", {"event": "step", "business_record": "private", "evidence_ref": "employee Alice salary 9000"})
    record = store.read("trace-1")[0]
    assert "business_record" not in record
    assert "evidence_ref" not in record


def test_trace_rejects_path_separators(workspace):
    with pytest.raises(ValueError):
        TraceStore(workspace, os.environ["CODEX_HOME"]).append("../outside", {"event": "step"})


def test_trace_keeps_inspection_metadata(workspace):
    store = TraceStore(workspace, os.environ["CODEX_HOME"])
    store.append("trace-1", {"event": "inspection.result", "skill": "expense-inspection", "scope_id": "scope-1", "result": "通过", "evidence_ref": "odoo://x"})
    record = store.read("trace-1")[0]
    assert record["skill"] == "expense-inspection"
    assert record["scope_id"] == "scope-1"
    assert record["evidence_ref"] == "odoo://x"


def test_traceable_memory_content_still_redacts_credentials():
    event = trace_module.sanitize_trace_event({
        "event": "memory.write", "memory_content": "confirmed_by: Employee Name\nproject token=private-value",
    })

    assert event["memory_content"] == f"confirmed_by: {REDACTED}\nproject token={REDACTED}"
    research = trace_module.sanitize_trace_event({
        "event": "research.read", "research_content": {"confirmed_by": "Employee Name"},
    })
    assert json.loads(research["research_content"]) == {"confirmed_by": REDACTED}


def test_plugin_has_no_external_telemetry_and_local_trace_works(workspace):
    import importlib.util
    assert importlib.util.find_spec("dy_superpowers_mcp.telemetry") is None
    result = call_tool("dy_trace_event", {"trace_id": "trace-local", "event": {"event": "inspection.result", "result": "通过"}}, workspace)
    assert "error" not in result
    assert TraceStore(workspace, os.environ["CODEX_HOME"]).read("trace-local")[1]["result"] == "通过"


def test_failed_tool_has_error_and_end_trace_events(workspace):
    result = call_tool("unknown", {"trace_id": "trace-1"}, workspace)
    assert "error" in result
    events = TraceStore(workspace, os.environ["CODEX_HOME"]).read("trace-1")
    assert [event["event"] for event in events[-2:]] == ["tool.error", "tool.end"]
    assert events[-1]["status"] == "error"
    assert events[-2]["error_code"] == -32602
    assert isinstance(events[-1]["duration_ms"], (int, float))


def test_memory_write_requires_confirmation_and_stays_in_workspace(workspace):
    rejected = call_tool("dy_memory_write", {"trace_id": "memory-1", "name": "profile.md", "content": "project", "confirmed": False}, workspace)
    assert "error" in rejected
    assert not (workspace / ".dy_superpowers/profile.md").exists()

    accepted = call_tool("dy_memory_write", {"trace_id": "memory-2", "name": "profile.md", "content": "project", "confirmed": True}, workspace)
    assert "error" not in accepted
    assert (workspace / ".dy_superpowers/profile.md").read_text(encoding="utf-8") == "project"
    assert "project" in str(TraceStore(workspace, os.environ["CODEX_HOME"]).read("memory-2"))


def test_memory_read_and_write_trace_body_and_byte_counts(workspace):
    content = "项目交付记录，可追踪内容"
    written = call_tool("dy_memory_write", {
        "trace_id": "memory-metadata", "name": "profile.md", "content": content, "confirmed": True,
    }, workspace)
    read = call_tool("dy_memory_read", {
        "trace_id": "memory-metadata", "name": "profile.md",
    }, workspace)

    assert "error" not in written
    assert read["result"]["content"][0]["text"] == content
    events = TraceStore(workspace, os.environ["CODEX_HOME"]).read("memory-metadata")
    memory_events = [event for event in events if event.get("event") in {"memory.write", "memory.read"}]
    assert [event["event"] for event in memory_events] == ["memory.write", "memory.read"]
    assert all(event["content_bytes"] == len(content.encode("utf-8")) for event in memory_events)
    assert all(event["memory_content"] == content for event in memory_events)


def test_odoo_trace_records_domain_conditions_without_returned_records(workspace, monkeypatch):
    class FakeOdoo:
        def search_read(self, model, domain, fields, limit, offset):
            assert model == "res.partner"
            assert domain == [["id", "=", 42], ["active", "=", True]]
            assert fields == ["name", "active"]
            assert limit == 25
            assert offset == 10
            return [{"id": 7, "name": "Returned business row"}]

    monkeypatch.setattr(runtime, 'odoo_client', lambda project: FakeOdoo())
    result = call_tool("dy_odoo_record_search", {
        "trace_id": "odoo-domain",
        "model": "res.partner",
        "args": [[["id", "=", 42], ["active", "=", True]]],
        "kwargs": {"fields": ["name", "active"], "limit": 25, "offset": 10},
    }, workspace)

    assert "error" not in result
    events = TraceStore(workspace, os.environ["CODEX_HOME"]).read("odoo-domain")
    query_event = next(event for event in events if event.get("event") == "odoo.read")
    assert json.loads(query_event["domain"]) == [["id", "=", 42], ["active", "=", True]]
    assert query_event["count"] == 1
    assert query_event["field_count"] == 2
    assert query_event["domain_term_count"] == 2
    assert query_event["limit"] == 25
    assert query_event["offset"] == 10
    assert "Returned business row" not in json.dumps(events)


def test_odoo_domain_trace_redacts_secret_and_personal_field_values():
    event = trace_module.sanitize_trace_event({
        "event": "odoo.read", "model": "hr.employee",
        "domain": [
            ["name", "=", "Employee Name"],
            ["work_email", "=", "employee@example.invalid"],
            ["password", "=", "credential-value"],
            ["active", "=", True],
        ],
    })

    assert json.loads(event["domain"]) == [
        ["name", "=", "[REDACTED]"],
        ["work_email", "=", "[REDACTED]"],
        ["password", "=", "[REDACTED]"],
        ["active", "=", True],
    ]


def test_oversized_odoo_domain_is_marked_as_truncated():
    event = trace_module.sanitize_trace_event({
        "event": "odoo.read", "domain": [["name", "=", "x" * 3000]],
    })

    assert "domain" not in event
    assert event["domain_truncated"] is True


def test_business_event_cannot_forge_tool_end(workspace):
    result = call_tool("dy_trace_event", {"trace_id": "trace-1", "event": {"event": "tool.end", "status": "ok"}}, workspace)
    assert "error" in result
    events = TraceStore(workspace, os.environ["CODEX_HOME"]).read("trace-1")
    assert [event["event"] for event in events] == ["tool.start", "tool.error", "tool.end"]
    assert events[-1]["status"] == "error"


def test_trace_read_excludes_its_own_lifecycle_events(workspace):
    result = call_tool("dy_trace_read", {"trace_id": "trace-1"}, workspace)
    assert json.loads(result["result"]["content"][0]["text"]) == []
    stored = TraceStore(workspace, os.environ["CODEX_HOME"]).read("trace-1")
    assert [event["event"] for event in stored] == ["tool.start", "tool.end"]


def test_server_requires_mcp_sdk_instead_of_starting_compatibility_protocol(workspace, tmp_path, monkeypatch):
    plugin_root = Path(__file__).parents[2]
    config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dy-superpowers-odoo"]
    assert config["command"] == "./mcp-server/start.sh"
    assert config.get("cwd") == "."
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_root))
    # 禁用 site-packages，验证缺少必需 SDK 时给出明确错误且不切换协议实现。
    process = subprocess.run(
        [sys.executable, "-S", str(plugin_root / "mcp-server/src/server.py")],
        cwd=workspace,
        input=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "dy_memory_write", "arguments": {"trace_id": "startup-test", "name": "profile.md", "content": "project memory", "confirmed": True}},
        }) + "\n",
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        timeout=10,
    )
    assert process.returncode != 0
    assert "缺少 MCP SDK" in process.stderr
    assert not (workspace / ".dy_superpowers/profile.md").exists()
    assert not (plugin_root / ".dy_superpowers/profile.md").exists()


def test_workspace_memory_rejects_directory_symlink_escape(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".dy_superpowers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        WorkspaceMemory(workspace).write("profile.md", "bad", confirmed=True)


def test_tool_schemas_register_memory_tools_with_trace_id(workspace):
    tools = {tool["name"]: tool for tool in listed_tools()}
    assert "dy_memory_read" in tools
    assert "dy_memory_write" in tools
    assert "trace_id" in tools["dy_memory_read"]["inputSchema"]["required"]
    assert "trace_id" in tools["dy_memory_write"]["inputSchema"]["required"]


def test_memory_search_uses_optional_basic_memory_and_records_status_only(workspace, monkeypatch):
    class FakeAdapter:
        def __init__(self, project):
            assert project == workspace

        def search(self, query, *, module=None):
            assert query == "交付台账"
            return {
                "backend": "local-fallback",
                "available": False,
                "expected_version": "0.23.2",
                "project": "dy-superpowers",
                "results": [{"name": "profile.md", "matches": ["项目使用 Odoo 12"]}],
                "has_more": False,
            }

    monkeypatch.setattr(basic_memory, 'BasicMemoryAdapter', FakeAdapter)
    result = call_tool("dy_memory_search", {"trace_id": "memory-search", "query": "交付台账"}, workspace)

    assert "error" not in result
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["backend"] == "local-fallback"
    assert payload["results"][0]["name"] == "profile.md"
    trace = TraceStore(workspace, os.environ["CODEX_HOME"]).read("memory-search")
    search_event = next(event for event in trace if event.get("event") == "memory.search")
    assert search_event["status"] == "fallback"
    assert search_event["backend"] == "local-fallback"
    assert search_event["result_count"] == 1
    assert search_event["has_more"] is False
    assert search_event["query"] == "交付台账"
    assert "项目使用 Odoo 12" in search_event["memory_content"]


def test_memory_search_rejects_empty_query_without_creating_memory(workspace):
    result = call_tool("dy_memory_search", {"trace_id": "memory-empty", "query": " "}, workspace)

    assert result["error"]["code"] == -32602
    assert not (workspace / ".dy_superpowers").exists()
