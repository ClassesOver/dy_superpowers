import json
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp import TraceStore, WorkspaceMemory, redact, validate_plan
from dy_superpowers_mcp import telemetry, trace as trace_module
from dy_superpowers_mcp.trace import REDACTED
from dy_superpowers_mcp.research import read_record, write_record
import server
from server import handle


@pytest.fixture(autouse=True)
def isolated_telemetry(monkeypatch):
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


def call_tool(name, arguments, project):
    return handle(
        {"id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        Path(project),
    )


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


def test_confirmed_research_record_is_stored_by_key_and_keeps_history(workspace):
    first = research_arguments(trace_id="research-a")
    saved = call_tool("dy_research_record_write", first, workspace)
    assert "error" not in saved

    updated = {**first, "trace_id": "research-b", "conclusion": "费用模块及OCR本期上线", "evidence_ref": "需求文档#7"}
    updated["expected_revision"] = read_record(workspace, "expense", "1", "01")["revision"]
    saved = call_tool("dy_research_record_write", updated, workspace)
    assert "error" not in saved

    read = call_tool(
        "dy_module_memory_read",
        {"trace_id": "research-c", "module": "expense", "check_order": "1", "research_id": "01"},
        workspace,
    )
    payload = json.loads(read["result"]["content"][0]["text"])["records"][0]
    assert payload["conclusion"] == "费用模块及OCR本期上线"
    assert payload["status"] == "confirmed"
    assert payload["history"][0]["status"] == "superseded"
    assert len(payload["history"]) == 1
    research_trace = str(TraceStore(workspace, os.environ["CODEX_HOME"]).read("research-b"))
    assert "费用模块及OCR本期上线" in research_trace
    assert "需求文档#7" in research_trace


def test_research_record_write_requires_confirmation(workspace):
    arguments = research_arguments(trace_id="research-no", confirmed=False)
    result = call_tool("dy_research_record_write", arguments, workspace)
    assert "error" in result
    assert not (workspace / ".dy_superpowers/modules/expense/research.md").exists()


def test_research_record_write_rejects_invalid_key_and_date(workspace):
    with pytest.raises(ValueError, match="check_order"):
        write_record(workspace, research_arguments(check_order="../outside"))
    with pytest.raises(ValueError, match="confirmed_at"):
        write_record(workspace, research_arguments(confirmed_at="yesterday"))


def test_research_record_write_rejects_workspace_symlink_escape(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".dy_superpowers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        write_record(workspace, research_arguments())


def test_concurrent_research_updates_preserve_every_revision(workspace):
    def update(index):
        # 每次冲突后重新读取；测试重试只针对这些人工构造的独立确认修订。
        for _ in range(8):
            current = read_record(workspace, "expense", "1", "01")
            arguments = research_arguments(conclusion=f"费用模块第{index}次确认", evidence_ref=f"需求文档#{index}")
            if current:
                arguments["expected_revision"] = current["revision"]
            try:
                return write_record(workspace, arguments)
            except ValueError as exc:
                if "冲突" not in str(exc) and "expected_revision" not in str(exc):
                    raise
        raise AssertionError("并发修订未能在重试预算内提交")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(update, range(8)))

    record = json.loads(
        call_tool(
            "dy_module_memory_read",
            {"trace_id": "research-read", "module": "expense", "check_order": "1", "research_id": "01"},
            workspace,
        )["result"]["content"][0]["text"]
    )["records"][0]
    assert all(result["stored"] for result in results)
    assert record["status"] == "confirmed"
    assert len(record["history"]) == 7
    assert all(revision["status"] == "superseded" for revision in record["history"])


def test_odoo_record_read_uses_explicit_read_operation(workspace, monkeypatch):
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("APP_URL", "http://odoo")
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("APP_USER", "u")
    monkeypatch.setenv("APP_PASSWORD", "p")
    monkeypatch.setattr("server.OdooReadClient.__init__", lambda self, settings: None)
    read = lambda self, model, ids, fields: [{"id": 3, "name": "Demo"}]
    monkeypatch.setattr("server.OdooReadClient.read_records", read)

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
    monkeypatch.setattr("server.OdooReadClient.__init__", lambda self, settings: None)
    called = []
    monkeypatch.setattr("server.OdooReadClient.fields_get", lambda self, model: called.append(model) or {"name": {"type": "char"}})

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


def test_invalid_json_is_reported_without_crashing_server():
    from server import parse_request

    result = parse_request("{bad json")
    assert result["error"]["code"] == -32700


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
    store.append("trace-1", {"event": "inspection.result", "skill": "expense-inspection", "check_order": "1", "research_id": "01", "result": "通过", "evidence_ref": "odoo://x"})
    record = store.read("trace-1")[0]
    assert record["skill"] == "expense-inspection"
    assert record["research_id"] == "01"
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


def test_external_telemetry_is_inert_and_local_trace_works(workspace):
    assert telemetry.initialize_telemetry() is False
    with telemetry.tool_span("trace-1", workspace, "dy_trace_event") as span:
        assert span is None
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

    monkeypatch.setattr(server, "_odoo_client", lambda project: FakeOdoo())
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


def test_research_read_traces_memory_content(workspace):
    arguments = research_arguments(trace_id="research-source")
    assert "error" not in call_tool("dy_research_record_write", arguments, workspace)
    result = call_tool("dy_module_memory_read", {
        "trace_id": "research-read-metadata", "module": "expense",
        "check_order": "1", "research_id": "01",
    }, workspace)

    assert "error" not in result
    events = TraceStore(workspace, os.environ["CODEX_HOME"]).read("research-read-metadata")
    research_event = next(event for event in events if event.get("event") == "memory.module.read")
    assert research_event["found"] is True
    assert research_event["history_count"] == 0
    assert research_event["module"] == "expense"
    research_content = json.loads(research_event["research_content"])[0]
    assert research_content["conclusion"] == "费用模块本期上线"
    assert research_content["evidence_ref"] == "需求文档#4"


def test_module_research_cannot_be_overwritten_by_generic_memory_tool(workspace):
    arguments = {"trace_id": "research-1", "name": "module-research.md", "content": "# 费用模块\n\n调研点待补充。", "confirmed": False}
    rejected = call_tool("dy_memory_write", arguments, workspace)
    assert "error" in rejected
    assert not (workspace / ".dy_superpowers/modules/expense/research.md").exists()

    arguments["confirmed"] = True
    rejected = call_tool("dy_memory_write", arguments, workspace)
    assert "error" in rejected
    assert not (workspace / ".dy_superpowers/modules/expense/research.md").exists()
    tools = {tool["name"]: tool for tool in handle({"id": 3, "method": "tools/list"}, workspace)["result"]["tools"]}
    assert "module-research.md" not in tools["dy_memory_read"]["inputSchema"]["properties"]["name"]["enum"]
    assert "module-research.md" not in tools["dy_memory_write"]["inputSchema"]["properties"]["name"]["enum"]


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
    assert "${PLUGIN_ROOT}" in " ".join(config["args"])
    assert config.get("cwd") is None
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


def test_workspace_memory_is_scoped_and_allowlisted(workspace):
    memory = WorkspaceMemory(workspace)
    memory.write("profile.md", "项目：HRP", confirmed=True)
    assert memory.read("profile.md") == "项目：HRP"
    assert (workspace / ".dy_superpowers/profile.md").is_file()
    with pytest.raises(ValueError):
        memory.write("../outside.md", "bad", confirmed=True)
    with pytest.raises(PermissionError):
        memory.write("profile.md", "未确认内容")
    with pytest.raises(ValueError, match="unsupported memory file"):
        memory.write("module-research.md", "whole-file overwrite", confirmed=True)


def test_workspace_memory_rejects_directory_symlink_escape(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".dy_superpowers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        WorkspaceMemory(workspace).write("profile.md", "bad", confirmed=True)


def test_tool_schemas_register_memory_tools_with_trace_id(workspace):
    result = handle({"id": 1, "method": "tools/list"}, workspace)
    tools = {tool["name"]: tool for tool in result["result"]["tools"]}
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

    monkeypatch.setattr(server, "BasicMemoryAdapter", FakeAdapter)
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


def test_plan_requires_verification_and_approval_for_writes():
    result = validate_plan({"steps": [{"target": "odoo", "operation": "write", "read_write": "write", "verification": "read back"}]})
    assert result["valid"] is False
    assert any(error["path"].endswith("approval_required") for error in result["errors"])


def test_string_approval_does_not_authorize_write():
    result = validate_plan({"steps": [{"target": "odoo", "operation": "write", "read_write": "write", "verification": "read back", "approval_required": "true"}]})
    assert result["valid"] is False


def test_valid_plan_does_not_grant_execution_authorization():
    result = validate_plan({"steps": [{"target": "odoo", "operation": "write", "read_write": "write", "verification": "read back", "approval_required": True}]})
    assert result["valid"] is True
    assert result["execution_authorized"] is False
